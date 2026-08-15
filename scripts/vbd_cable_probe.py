#!/usr/bin/env python3
"""Evidence probe: Newton Cosserat rod + VBD as the DYNAMIC cord model.

Run with the Newton venv:
    .venv-newton/bin/python scripts/vbd_cable_probe.py

Vertex Block Descent (Chen, Liu, Yang & Yuksel, SIGGRAPH 2024 —
ankachan.github.io/Projects/VertexBlockDescent) is what Newton's
SolverVBD implements. Its stated advantage over XPBD at extreme mass
ratios is exactly the failure our PhysX D6 cord hit: gram-scale links
carrying a 100 g tool would not converge, so make_cable floors link mass
at 15 g, and a SLACK cord still coils into a hairpin (1.0 m collapsed to
a 0.19 m span).

Measured here, physical masses throughout:
  HANG  100 g tool on 3.02 g segments -> arc 0.954 m, dead straight,
        hanging 1.200 -> 0.246 m
  SLACK 1.0 m of cord across a 0.55 m span, both ends pinned -> holds
        the bow: span 0.548 m, z 0.004-0.006 m, lying flat

Newton API facts that cost the most time: add_rod builds RIGID BODIES +
cable joints (not particles); it wants one quaternion per SEGMENT mapping
local +Z onto the segment direction; the given positions are the REST
shape (spacing nodes closer builds a SHORTER rod, not a slack one); VBD
requires ModelBuilder.color() before finalize (its graph-colouring
parallelism); pin by zero inverse mass — a world FixedJoint let the rod
fall 78 m.
"""
import math
import numpy as np
import newton
import warp as wp

from newton_runtime import collision_pipeline, require_newton_15

require_newton_15()
wp.set_device("cpu")
L, R, N = 1.0, 0.004, 21
RUBBER = 1200.0
seg = L / (N - 1)
seg_mass = RUBBER * math.pi * R**2 * seg
print(f"physical segment mass: {seg_mass*1000:.2f} g   (PhysX needed 15 g)")


def _q_to(d):
    d = np.asarray(d, float); d /= np.linalg.norm(d)
    z = np.array([0.0, 0.0, 1.0]); c = float(np.dot(z, d))
    if c > 1 - 1e-9:
        return wp.quat_identity()
    if c < -1 + 1e-9:
        return wp.quat_from_axis_angle(wp.vec3(1.0, 0.0, 0.0), math.pi)
    ax = np.cross(z, d); ax /= np.linalg.norm(ax)
    return wp.quat_from_axis_angle(wp.vec3(*ax), math.acos(c))


def run(hang: bool, tool_kg: float = 0.0):
    b = newton.ModelBuilder()
    if hang:                       # straight down from a fixed top
        pos = [wp.vec3(0.0, 0.0, 1.2 - i * seg) for i in range(N)]
    else:
        # GENUINE slack: 1.0 m of cord across a 0.55 m span, laid as a
        # circular bow. (Spacing the nodes closer just builds a SHORTER
        # rod — add_rod takes the given positions as the REST shape.)
        chord, arc_len = 0.55, L
        lo, hi = chord / 2 + 1e-6, 5.0
        for _ in range(60):        # solve arc = 2 r asin(chord/2r)
            rad = 0.5 * (lo + hi)
            a = 2 * rad * math.asin(min(1.0, chord / (2 * rad)))
            if a > arc_len: lo = rad
            else: hi = rad
        half = math.asin(min(1.0, chord / (2 * rad)))
        pos = []
        for i in range(N):
            t = -half + 2 * half * i / (N - 1)
            pos.append(wp.vec3(rad * math.sin(t) + chord / 2,
                               rad * (math.cos(t) - math.cos(half)),
                               R + 0.002))
    quats = [_q_to(np.array(pos[i + 1]) - np.array(pos[i])) for i in range(N - 1)]
    newton.solvers.SolverVBD.register_custom_attributes(
        b, dahl_defaults_enabled=False)
    b.add_rod(positions=pos, quaternions=quats, radius=R,
              stretch_stiffness=1.0e5, stretch_damping=1.0e-2,
              bend_stiffness=5.0e-3, bend_damping=1.0e-4,
              body_frame_origin="start")
    nb = b.body_count
    for i in range(nb):            # PHYSICAL masses
        b.body_mass[i] = seg_mass
        b.body_inv_mass[i] = 1.0 / seg_mass
    if tool_kg:                    # a 100 g tool on the free end
        b.body_mass[nb - 1] = tool_kg
        b.body_inv_mass[nb - 1] = 1.0 / tool_kg
    # pin by zero inverse mass — a world FixedJoint left the rod free and
    # it fell 78 m. Slack case pins BOTH ends (tool one side, plug the other).
    b.body_mass[0] = 0.0
    b.body_inv_mass[0] = 0.0
    if not hang:
        b.body_mass[nb - 1] = 0.0
        b.body_inv_mass[nb - 1] = 0.0
    if not hang:
        b.add_ground_plane()
    b.color()          # VBD parallelises over graph colours (paper S4)
    model = b.finalize()
    pipeline, contacts = collision_pipeline(model)
    solver = newton.solvers.SolverVBD(model, iterations=10)
    s0, s1 = model.state(), model.state()
    ctrl = model.control()
    dt = 1.0 / 60.0 / 8
    for _ in range(int(4.0 * 60)):
        for _ in range(8):
            s0.clear_forces()
            pipeline.collide(s0, contacts)
            solver.step(s0, s1, ctrl, contacts, dt)
            s0, s1 = s1, s0
    q = s0.body_q.numpy()[:, :3]
    if not np.isfinite(q).all():
        return None
    arc = sum(float(np.linalg.norm(q[i + 1] - q[i])) for i in range(len(q) - 1))
    return arc, float(np.linalg.norm(q[-1] - q[0])), q[:, 2].min(), q[:, 2].max()


for hang, tool in ((True, 0.1), (False, 0.0)):
    r = run(hang, tool)
    tag = "HANG (100 g tool)" if hang else "SLACK on ground"
    if r is None:
        print(f"{tag:18} -> DIVERGED")
    else:
        arc, span, zmin, zmax = r
        print(f"{tag:18} -> chain arc {arc:.3f} m, end-to-end {span:.3f} m, "
              f"z [{zmin:.3f}, {zmax:.3f}]")
