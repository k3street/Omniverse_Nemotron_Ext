#!/usr/bin/env python3
"""A Franka picks a garment off a table, carries it, and puts it down.

This is the robot version of `verify_asset_newton.py cloth_grasp`. That test
proved a garment can be held by friction alone, but it used two abstract
kinematic finger boxes. This uses an actual Franka FR3 arm with its real
hand, driven through a full pick-and-place cycle, and asks whether the cloth
ends up where the robot put it.

Why the standalone Newton path: NVIDIA's reference for robot cloth
manipulation uses this pairing — a Franka under SolverFeatherstone coupled
one-way to cloth under SolverVBD (see example_cloth_franka.py, from which the
Jacobian velocity-IK and coupling order here are adapted). Isaac Lab 3.0 Beta
2 now has an experimental Newton cloth-lift path, but it pins an older Newton;
this probe isolates Newton 1.5 so its measured baseline stays reproducible.

The coupling is ONE-WAY: the arm moves the cloth, the cloth does not push
the arm back. That is what the reference does and it is acceptable here —
a 30 g washcloth does not perturb a Franka.

Usage (Newton venv):
    .venv-newton/bin/python scripts/pick_place_cloth.py garment_washcloth
    PICK_PLACE_EXPORT_USD=out.usda python scripts/pick_place_cloth.py garment_napkin

Set NEWTON_FULL_SURFACE_CONTACT=1 to A/B Newton 1.5's opt-in VBD edge/face
contact path. The default remains particle contact for baseline continuity.

Criteria (all measured, none assumed):
    carried  — the cloth's centroid moves with the arm to the place target
    intact   — no vertex ends further from its neighbours than the garment's
               own rest reach allows (nothing tore or exploded)
    landed   — the cloth comes to rest ON the table, not through it or off it
    settled  — it stops moving by the end of the run
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from newton_runtime import (collision_pipeline, require_newton_15,
                            runtime_label, runtime_metadata)

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"

FPS = 60
FRAME_DT = 1.0 / FPS
SUBSTEPS = 15          # the reference uses 15; cloth needs the small dt
IK_ITERATIONS = 5

# Table geometry. The Franka sits beside it; everything happens on the top
# face, so TABLE_TOP is the reference height for every key pose.
TABLE_CENTER = (0.0, -0.5, 0.1)
TABLE_HALF = (0.4, 0.4, 0.1)
TABLE_TOP = TABLE_CENTER[2] + TABLE_HALF[2]        # 0.2 m
FRANKA_BASE = (-0.5, -0.5, -0.1)

# Gripper finger activation. The control law maps activation -> finger
# opening as `activation * 0.04` metres, so these are 32 mm and 2.4 mm.
GRIP_OPEN = 0.8
GRIP_CLOSE = 0.06


def _device():
    """Prefer CUDA — VBD is GPU-parallel and Newton disables its tiled solve
    on CPU. Override with NEWTON_DEVICE."""
    _, wp = require_newton_15()

    want = os.environ.get("NEWTON_DEVICE")
    if want:
        wp.set_device(want)
        return want
    try:
        if wp.get_cuda_device_count() > 0:
            wp.set_device("cuda:0")
            wp.zeros(1, dtype=float)
            return "cuda:0"
    except Exception:
        pass
    wp.set_device("cpu")
    return "cpu"


def _garment(target: str):
    """Our garment mesh, in metres, centred on the origin and flattened."""
    import numpy as np

    sys.path.insert(0, str(REPO / "scripts"))
    from verify_asset_newton import _world_mesh

    entry, path = None, target
    qf = QUEUE_DIR / f"{target}.json"
    if qf.exists():
        entry = json.loads(qf.read_text())
        path = entry.get("file", target)
    if not Path(path).exists():
        raise SystemExit(f"{target}: file not found ({path})")
    points, tris = _world_mesh(path)
    if points is None or not tris:
        raise SystemExit(f"{target}: no usable mesh")
    points = np.asarray(points, dtype=float)
    points = points - points.mean(axis=0)
    return points, tris, entry


class ClothPickPlace:
    """Franka + garment, one-way coupled, driven through a pick-place cycle.

    The Jacobian velocity IK and the substep ordering are adapted from
    newton/examples/cloth/example_cloth_franka.py (Apache-2.0).
    """

    def __init__(self, target: str):
        import numpy as np
        import warp as wp

        import newton
        import newton.utils
        from newton import ModelBuilder
        from newton.solvers import SolverFeatherstone, SolverVBD

        self.np, self.wp, self.newton = np, wp, newton
        points, tris, self.entry = _garment(target)
        self.target_name = target

        scene = ModelBuilder()

        self.full_surface_contact = (
            os.environ.get("NEWTON_FULL_SURFACE_CONTACT", "0") == "1")

        # ---- the robot -------------------------------------------------
        franka = ModelBuilder()
        if self.full_surface_contact:
            # Full-surface contact requires volume SDFs for participating
            # mesh/convex colliders; primitives do not need this conversion.
            franka.default_shape_cfg.configure_sdf(force_sdf=True)
        asset = newton.utils.download_asset("franka_emika_panda")
        franka.add_urdf(
            str(asset / "urdf" / "fr3_franka_hand.urdf"),
            xform=wp.transform(FRANKA_BASE, wp.quat_identity()),
            floating=False, scale=1, enable_self_collisions=False,
            collapse_fixed_joints=True, force_show_colliders=False)
        # a sane elbow-up start, as in the reference
        franka.joint_q[:6] = [0.0, 0.0, 0.0, -1.59695, 0.0, 2.5307]
        scene.add_world(franka)
        self.bodies_per_world = franka.body_count
        # the hand body; its tool frame is 22 cm further along local +Z
        self.endeffector_id = franka.body_count - 3
        self.endeffector_offset = wp.transform([0.0, 0.0, 0.22],
                                               wp.quat_identity())

        # ---- the table -------------------------------------------------
        scene.add_shape_box(-1, xform=wp.transform(wp.vec3(*TABLE_CENTER),
                                                  wp.quat_identity()),
                            hx=TABLE_HALF[0], hy=TABLE_HALF[1],
                            hz=TABLE_HALF[2])

        # ---- the garment, laid with one edge OVERHANGING ------------------
        # A parallel-jaw gripper cannot pick a flat sheet off a table: the
        # fingers close BESIDE zero-thickness fabric pressed against the
        # surface. Dropping it does not help either — a plain square sheet
        # lands flat again (measured: 8 mm of loft, i.e. its own thickness).
        # The reference example gets away with it only because a shirt has
        # sleeves and a collar that hold fabric off the table.
        # So stage it the way the task is actually staged: part of the
        # garment over the table edge, where the flap hangs in free air with
        # something for each finger to close against.
        self.table_edge_y = TABLE_CENTER[1] - TABLE_HALF[1]      # -0.9
        span_y = float(points[:, 1].max() - points[:, 1].min())
        # centre it ON the table (+y of the edge) so only a flap overhangs;
        # putting the centre past the edge just slides the whole thing off
        self.cloth_start = np.array(
            [0.0, self.table_edge_y + span_y * 0.30, TABLE_TOP + 0.02])
        placed = points + self.cloth_start
        self.rest_reach_all = float(
            np.linalg.norm(points - points.mean(axis=0), axis=1).max())
        scene.add_cloth_mesh(
            pos=wp.vec3(0.0, 0.0, 0.0), rot=wp.quat_identity(), scale=1.0,
            vertices=[wp.vec3(*p) for p in placed], indices=tris,
            vel=wp.vec3(0.0, 0.0, 0.0), density=0.2,
            tri_ke=1.0e2, tri_ka=1.0e2, tri_kd=1.5e-6,
            edge_ke=1.0e-4, edge_kd=1.0e-3,
            particle_radius=0.008)
        scene.color()
        scene.add_ground_plane()

        self.model = scene.finalize(requires_grad=False)
        self.model.soft_contact_ke = 100.0
        self.model.soft_contact_kd = 2.0e-3
        self.model.soft_contact_mu = 1.0       # robot_friction: the grasp

        # the reference pushes the soft-contact stiffness onto every shape
        ke = self.model.shape_material_ke.numpy(); ke[...] = 100.0
        kd = self.model.shape_material_kd.numpy(); kd[...] = 2.0e-3
        self.model.shape_material_ke = wp.array(
            ke, dtype=self.model.shape_material_ke.dtype)
        self.model.shape_material_kd = wp.array(
            kd, dtype=self.model.shape_material_kd.dtype)

        self.state_0, self.state_1 = self.model.state(), self.model.state()
        self.control = self.model.control()
        self.target_joint_qd = wp.empty_like(self.state_0.joint_qd)
        self.collision_pipeline, self.contacts = collision_pipeline(
            self.model,
            soft_contact_margin=0.01,
            enable_rigid_soft_full_surface_contact=self.full_surface_contact,
        )

        self.robot_solver = SolverFeatherstone(
            self.model, update_mass_matrix_interval=SUBSTEPS)
        # bending off is the reference's documented VBD stability workaround
        self.model.edge_rest_angle.zero_()
        self.cloth_solver = SolverVBD(
            self.model, iterations=IK_ITERATIONS,
            integrate_with_external_rigid_solver=True,
            particle_self_contact_radius=0.002,
            particle_self_contact_margin=0.003,
            particle_enable_self_contact=True,
            particle_vertex_contact_buffer_size=32,
            particle_edge_contact_buffer_size=64,
            particle_collision_detection_interval=-1,
            rigid_contact_k_start=100.0)

        # gravity is swapped per substep: the robot integrates without it
        # (it is position-controlled), the cloth integrates with it
        gravity_count = self.model.gravity.shape[0]
        self.gravity_zero = wp.zeros(
            gravity_count, dtype=wp.vec3, device=self.model.device)
        self.gravity_earth = wp.full(
            gravity_count, wp.vec3(0.0, 0.0, -9.81),
            dtype=wp.vec3, device=self.model.device)

        newton.eval_fk(self.model, self.model.joint_q, self.model.joint_qd,
                       self.state_0)
        self._setup_ik()
        # a home pose to hold while the cloth lands
        self.home = np.array([0.0, -0.45, TABLE_TOP + 0.30,
                              1.0, 0.0, 0.0, 0.0, GRIP_OPEN],
                             dtype=np.float32)
        self.targets = self.home.reshape(1, -1)
        self.key_times = np.array([1e9], dtype=np.float32)
        self.total_time = 0.0
        self.target = self.home
        self.sim_time = 0.0

    # -- the pick-and-place plan ------------------------------------------
    def _plan(self, settled):
        """Key poses: [duration, px,py,pz, qx,qy,qz,qw, grip].

        The grasp point is chosen from the SETTLED cloth, not assumed: the
        highest vertex is where the fabric has lifted off the table into a
        fold, and that lofted material is the only thing a parallel-jaw
        gripper can actually close around. This is the same choice a
        perception stack makes — find the graspable loft, then reach for it.
        """
        np = self.np
        # The overhanging flap is the graspable feature: take a point part
        # way down it, not its very tip (a tip pinch peels off) and not the
        # part still lying on the table (nothing to close against).
        hanging = settled[settled[:, 2] < TABLE_TOP - 0.01]
        if len(hanging) < 3:
            raise SystemExit(
                f"{self.target_name}: nothing overhangs the table edge "
                f"(lowest vertex {settled[:, 2].min():.3f} vs table top "
                f"{TABLE_TOP:.3f}) — restage the garment")
        z_lo, z_hi = hanging[:, 2].min(), TABLE_TOP - 0.01
        band = hanging[np.abs(hanging[:, 2] - (z_lo + z_hi) * 0.5) < 0.02]
        g = (band.mean(axis=0) if len(band) else hanging.mean(axis=0))
        self.grasp_xy = g[:2].copy()
        self.grasp_z = float(g[2])
        self.flap_drop = float(TABLE_TOP - z_lo)
        # place it back on the table top, a clear 30 cm away in +y
        self.place_xy = np.array([g[0], g[1] + 0.30])
        self.commanded_move = float(np.linalg.norm(self.place_xy -
                                                   self.grasp_xy))

        down = (1.0, 0.0, 0.0, 0.0)      # gripper pointing at the table
        gx, gy = self.grasp_xy
        px, py = self.place_xy
        gz = self.grasp_z
        poses = [
            (2.5, gx, gy, TABLE_TOP + 0.16, *down, GRIP_OPEN),   # above the edge
            (2.5, gx, gy, gz,               *down, GRIP_OPEN),   # down beside the flap
            (1.5, gx, gy, gz,               *down, GRIP_CLOSE),  # pinch the flap
            (2.5, gx, gy, TABLE_TOP + 0.22, *down, GRIP_CLOSE),  # lift clear
            (3.0, px, py, TABLE_TOP + 0.22, *down, GRIP_CLOSE),  # carry
            # Set it DOWN before opening. Releasing 6 cm up left the cloth
            # draped over the hand, so it rode back up with the retreat —
            # the gripper let go but the fabric had nowhere to fall from.
            (2.5, px, py, TABLE_TOP + 0.015, *down, GRIP_CLOSE),  # set down
            (2.0, px, py, TABLE_TOP + 0.015, *down, GRIP_OPEN),   # release
            (3.0, px, py, TABLE_TOP + 0.26,  *down, GRIP_OPEN),   # retreat slowly
            (3.0, px, py, TABLE_TOP + 0.26,  *down, GRIP_OPEN),   # let it settle
        ]
        arr = self.np.array(poses, dtype=self.np.float32)
        self.targets = arr[:, 1:]
        self.key_times = self.np.cumsum(arr[:, 0])
        self.total_time = float(self.key_times[-1])
        self.target = self.targets[0]

    # -- Jacobian velocity IK (adapted from the reference) -----------------
    def _setup_ik(self):
        import warp as wp

        ee_id, ee_off = self.endeffector_id, self.endeffector_offset

        @wp.kernel
        def compute_ee_delta(body_q: wp.array(dtype=wp.transform),
                             offset: wp.transform, body_id: int,
                             target: wp.transform,
                             ee_delta: wp.array(dtype=wp.spatial_vector)):
            tf = body_q[body_id] * offset
            pos = wp.transform_get_translation(tf)
            pos_des = wp.transform_get_translation(target)
            d = pos_des - pos
            rot = wp.transform_get_rotation(tf)
            rot_des = wp.transform_get_rotation(target)
            a = rot_des * wp.quat_inverse(rot)
            ee_delta[0] = wp.spatial_vector(d[0], d[1], d[2], a[0], a[1], a[2])

        @wp.kernel
        def compute_body_out(body_q: wp.array(dtype=wp.transform),
                             body_qd: wp.array(dtype=wp.spatial_vector),
                             body_com: wp.array(dtype=wp.vec3),
                             body_out: wp.array(dtype=float)):
            # Newton 1.5 body_qd is COM-referenced in world space. Convert it
            # to velocity at the tool tip, matching compute_ee_delta.
            body_id = wp.static(ee_id)
            offset = wp.static(wp.vec3(*ee_off.p))
            xform = body_q[body_id]
            r_world = wp.transform_vector(xform, offset - body_com[body_id])
            twist = body_qd[body_id]
            omega = wp.spatial_bottom(twist)
            linear = wp.spatial_top(twist) + wp.cross(omega, r_world)
            body_out[0] = linear[0]
            body_out[1] = linear[1]
            body_out[2] = linear[2]
            body_out[3] = omega[0]
            body_out[4] = omega[1]
            body_out[5] = omega[2]

        self._k_ee_delta = compute_ee_delta
        self._k_body_out = compute_body_out
        self._temp_state = self.model.state(requires_grad=True)
        self._body_out = wp.empty(6, dtype=float, requires_grad=True)
        self._J_flat = wp.empty(6 * self.model.joint_dof_count, dtype=float)
        self._ee_delta = wp.empty(1, dtype=wp.spatial_vector)
        self._onehots = [
            wp.array([1.0 if j == i else 0.0 for j in range(6)], dtype=float)
            for i in range(6)]
        self.initial_pose = self.model.joint_q.numpy().copy()

    def _jacobian(self, joint_q, joint_qd):
        import warp as wp

        from newton import eval_fk

        joint_q.requires_grad = True
        joint_qd.requires_grad = True
        tape = wp.Tape()
        with tape:
            eval_fk(self.model, joint_q, joint_qd, self._temp_state)
            wp.launch(self._k_body_out, 1,
                      inputs=[self._temp_state.body_q,
                              self._temp_state.body_qd,
                              self.model.body_com],
                      outputs=[self._body_out])
        n = self.model.joint_dof_count
        for i in range(6):
            tape.backward(grads={self._body_out: self._onehots[i]})
            wp.copy(self._J_flat[i * n:(i + 1) * n], joint_qd.grad)
            tape.zero()

    def _control(self, state_in):
        np, wp = self.np, self.wp
        t = min(self.sim_time, self.total_time - 1e-6)
        self.target = self.targets[int(np.searchsorted(self.key_times, t))]

        wp.launch(self._k_ee_delta, dim=1,
                  inputs=[state_in.body_q, self.endeffector_offset,
                          self.endeffector_id,
                          wp.transform(*self.target[:7])],
                  outputs=[self._ee_delta])
        self._jacobian(state_in.joint_q, state_in.joint_qd)

        J = self._J_flat.numpy().reshape(-1, self.model.joint_dof_count)
        delta = self._ee_delta.numpy()[0]
        J_inv = np.linalg.pinv(J)
        N = np.eye(J.shape[1], dtype=np.float32) - J_inv @ J

        q = state_in.joint_q.numpy()
        q_des = q.copy()
        q_des[1:] = self.initial_pose[1:]        # elbow-up null-space pull
        delta_q = J_inv @ delta + N @ (1.0 * (q_des - q))
        # fingers are commanded directly, not through the Jacobian
        delta_q[-2] = self.target[-1] * 0.04 - q[-2]
        delta_q[-1] = self.target[-1] * 0.04 - q[-1]
        self.target_joint_qd.assign(delta_q)

    # -- the coupled step --------------------------------------------------
    def simulate(self):
        self.cloth_solver.rebuild_bvh(self.state_0)
        for _ in range(SUBSTEPS):
            self.state_0.clear_forces()
            self.state_1.clear_forces()

            # ROBOT: advanced blind to the particles and without gravity.
            # Hiding the particles from the rigid solver is what makes this
            # one-way; without it the solvers fight over the same contacts.
            pc = self.model.particle_count
            self.model.particle_count = 0
            self.model.gravity.assign(self.gravity_zero)
            self.model.shape_contact_pair_count = 0
            self.state_0.joint_qd.assign(self.target_joint_qd)
            self.robot_solver.step(self.state_0, self.state_1, self.control,
                                   None, FRAME_DT / SUBSTEPS)
            self.state_0.particle_f.zero_()
            self.model.particle_count = pc
            self.model.gravity.assign(self.gravity_earth)

            # CLOTH: now sees where the robot got to.
            self.collision_pipeline.collide(self.state_0, self.contacts)
            self.cloth_solver.step(self.state_0, self.state_1, self.control,
                                   self.contacts, FRAME_DT / SUBSTEPS)
            self.state_0, self.state_1 = self.state_1, self.state_0

    def _viewer(self, frames):
        """A viewer for the run. PICK_PLACE_RECORD_USD writes the whole
        scene — arm included — to USD; PICK_PLACE_VIEWER=gl opens a live
        OpenGL window instead, which is the same simulation watched as it
        happens rather than scored after the fact."""
        if os.environ.get("PICK_PLACE_VIEWER") == "gl":
            import warp as wp

            from newton.viewer import ViewerGL
            v = ViewerGL(width=1600, height=1000, vsync=True)
            v.set_model(self.model)
            # look at the table from the front-right, as the renders do
            v.set_camera(wp.vec3(1.05, -1.75, 0.95), -46.0, -22.0)
            return v
        out = os.environ.get("PICK_PLACE_RECORD_USD")
        if not out:
            return None
        from newton.viewer import ViewerUSD
        v = ViewerUSD(output_path=out, fps=FPS, up_axis="Z", num_frames=frames)
        v.set_model(self.model)
        return v

    def run(self):
        np = self.np
        # --- let the cloth land and settle while the arm holds home ---
        settle_frames = int(2.5 / FRAME_DT)
        live = self._viewer(0) if os.environ.get("PICK_PLACE_VIEWER") == "gl" \
            else None
        for _ in range(settle_frames):
            self._control(self.state_0)
            self.simulate()
            if live is not None:
                live.begin_frame(self.sim_time)
                live.log_state(self.state_0)
                live.end_frame()
            self.sim_time += FRAME_DT
        settled = self.state_0.particle_q.numpy().copy()
        self.settled_loft = float(settled[:, 2].max() - TABLE_TOP)
        self.settled_start_xy = settled.mean(axis=0)[:2].copy()
        self._plan(settled)
        self.sim_time = 0.0
        if os.environ.get("PICK_DEBUG"):
            print(f"  settled: flap hangs {self.flap_drop*1000:.0f} mm below "
                  f"the table edge, grasping at ({self.grasp_xy[0]:+.3f}, "
                  f"{self.grasp_xy[1]:+.3f}, {self.grasp_z:.3f})", flush=True)

        frames = int(self.total_time / FRAME_DT)
        viewer = live if live is not None else self._viewer(frames)
        centroid_trace = []
        for f in range(frames):
            self._control(self.state_0)
            self.simulate()
            if viewer is not None:
                viewer.begin_frame(f * FRAME_DT)
                viewer.log_state(self.state_0)
                viewer.end_frame()
            self.sim_time += FRAME_DT
            pq = self.state_0.particle_q.numpy()
            centroid_trace.append(pq.mean(axis=0).copy())
            if viewer is not None and f == frames - 1 and live is None:
                viewer.close()
            if os.environ.get("PICK_DEBUG") and f % 30 == 0:
                c = centroid_trace[-1]
                bq = self.state_0.body_q.numpy()[self.endeffector_id]
                # tool frame = hand pose composed with the 22 cm offset
                import warp as wp
                tf = wp.transform(*bq) * self.endeffector_offset
                ee = np.array([tf[0], tf[1], tf[2]])
                tgt = self.target[:3]
                q = self.state_0.joint_q.numpy()
                print(f"  f{f:4d} t={self.sim_time:5.2f} "
                      f"ee=({ee[0]:+.3f},{ee[1]:+.3f},{ee[2]:.3f}) "
                      f"tgt=({tgt[0]:+.3f},{tgt[1]:+.3f},{tgt[2]:.3f}) "
                      f"err={np.linalg.norm(ee-tgt):.3f} "
                      f"fing={q[-2]:.4f} "
                      f"cloth=({c[0]:+.3f},{c[1]:+.3f},{c[2]:.3f})",
                      flush=True)
        if live is not None:
            # Hold the final frame so the result can actually be looked at;
            # the window closes when the user closes it. The physics is done
            # by now — this only keeps redrawing the last state.
            print("  [viewer] run complete — close the window to finish",
                  flush=True)
            while live.is_running():
                live.begin_frame(self.sim_time)
                live.log_state(self.state_0)
                live.end_frame()
            live.close()
        return np.array(centroid_trace)


def pick_place(target: str) -> str:
    import numpy as np

    _device()
    sim = ClothPickPlace(target)
    trace = sim.run()

    pq = sim.state_0.particle_q.numpy()
    if not np.isfinite(pq.sum()):
        return f"FAIL {target}: cloth solve diverged during pick-and-place"

    start_xy = sim.settled_start_xy
    final = pq.mean(axis=0)
    moved = float(np.linalg.norm(final[:2] - start_xy))
    # did it go the way the arm went, not just anywhere?
    want = sim.place_xy - sim.grasp_xy
    got = final[:2] - start_xy
    along = float(got @ (want / (np.linalg.norm(want) or 1.0)))
    carried = along > 0.5 * sim.commanded_move

    span = float(np.linalg.norm(pq - pq.mean(axis=0), axis=1).max())
    intact = span < sim.rest_reach_all * 1.35

    z = pq[:, 2]
    landed = bool(z.min() > TABLE_TOP - 0.02 and z.max() < TABLE_TOP + 0.15)
    tail = trace[-30:]
    drift = float(np.linalg.norm(tail.max(axis=0) - tail.min(axis=0)))
    settled = drift < 0.02

    ok = carried and intact and landed and settled
    if os.environ.get("PICK_DEBUG"):
        print(f"  final cloth z: min={z.min():.3f} mean={z.mean():.3f} "
              f"max={z.max():.3f} (table top {TABLE_TOP:.3f}) landed={landed}",
              flush=True)
    evidence = {
        "date": date.today().isoformat(),
        "method": "headless_newton_franka_cloth_pick_place",
        "engine": runtime_label(
            "SolverFeatherstone (Franka FR3) one-way coupled to SolverVBD "
            "(cloth), friction grasp mu=1.0"),
        **runtime_metadata(),
        "device": _device(),
        "full_surface_contact": sim.full_surface_contact,
        "commanded_move_m": round(sim.commanded_move, 4),
        "cloth_moved_m": round(moved, 4),
        "moved_along_command_m": round(along, 4),
        "carried": carried,
        "span_m": round(span, 4),
        "rest_reach_m": round(sim.rest_reach_all, 4),
        "intact": intact,
        "rests_on_table": landed,
        "settle_drift_m": round(drift, 4),
        "settled": settled,
        "particles": int(len(pq)),
        "robot_pick_place_ok": ok,
    }
    if sim.entry is not None:
        suffix = "full_surface" if sim.full_surface_contact else "particle"
        sim.entry[f"cloth_pick_place_test_newton_1_5_{suffix}"] = evidence
        (QUEUE_DIR / f"{target}.json").write_text(
            json.dumps(sim.entry, indent=1))

    dump = os.environ.get("PICK_PLACE_EXPORT_USD")
    if dump:
        sys.path.insert(0, str(REPO / "scripts"))
        from verify_asset_newton import _export_cloth_usd
        bq = sim.state_0.body_q.numpy()
        _export_cloth_usd(dump, pq, sim.model.tri_indices.numpy().flatten(),
                          [bq[sim.endeffector_id][:3]], half=(0.02, 0.02, 0.03))
        print(f"  wrote {dump}", flush=True)

    return (f"{'PASS' if ok else 'FAIL'} {target}: robot carried the cloth "
            f"{along:.2f} m of a commanded {sim.commanded_move:.2f} m "
            f"(total displacement {moved:.2f} m), span {span:.2f}/"
            f"{sim.rest_reach_all:.2f} m, on_table={landed}, "
            f"settled={settled}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    failures = 0
    for t in sys.argv[1:]:
        try:
            line = pick_place(t)
            print(line, flush=True)
            failures += 0 if line.startswith("PASS") else 1
        except Exception as e:
            failures += 1
            print(f"ERROR {t}: {str(e)[:300]}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
