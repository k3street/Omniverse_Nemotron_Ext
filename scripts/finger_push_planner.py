#!/usr/bin/env python3
"""The right hand works a held articulated object: its index fingertip pushes
the free half of a scissors about the pivot while the left hand holds the
other half (the scissors task's second step). HomeHero's geometry over both
arms (r5_arm_side.both_arms_geometry), its coal checker with the right arm's
links active against the obstacles and both arms' self-collisions, its RRT
for the right arm's reach, IK per 5 mm along the push arc.

    python scripts/finger_push_planner.py <scene_dir> --hold <targets.npz> --poses <settled_poses.json> --out <push.npz>
reads the scene's spec.json (finger_push: the pushed body, its loop point and
radius in the asset's frame, the pivot joint's axis, the closing angle) and
writes <out>: targets (N x 28, the left columns the hold row), stages.
Prints PUSH_PLAN OK|FAIL ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
HH = Path("~/Documents/Github/HomeHero").expanduser()
for sub in ("scripts/rl_teacher", "scripts", ""):
    sys.path.insert(0, str(HH / sub))

STEP_M = 0.005                                           # IK per 5 mm along the arc
HZ = 30


def outer_wall(points, loop_c, axis, t_hat, s_hat, band_m=0.004, window_m=0.010):
    """Where the finger meets the handle: of the pushed body's points within
    `band_m` of the ring's plane through the loop point and `window_m` of it
    along the handle (s_hat), the one farthest against the motion (t_hat):
    the handle's outer wall. The loop is no circle (2026-10-15: its rim lay
    10 mm from the loop point on the finger's side and the spec's 15 mm radius
    put the fingertip 5 mm outside it, grazing the rim once and moving on)."""
    pts = np.asarray(points, float)
    rel = pts - np.asarray(loop_c, float)
    band = pts[(np.abs(rel @ axis) < band_m) & (np.abs(rel @ s_hat) < window_m)]
    if len(band) == 0:
        return None
    return band[np.argmin(band @ t_hat)]


def finger_rotation(local_triad, t_hat, s_hat, yaw=0.0, face="pad"):
    """The distal link's world rotation that lays one face of the finger on
    the wall facing the push (t_hat): its pad (the palm up under a loop) or
    its back (a hook: the palm down, the finger's back lifting the loop,
    the pronated reach an arm makes naturally); the finger's axis turned
    `yaw` about the push direction from s_hat (along the handle toward the
    pivot): yaw 0 lays the finger along the handle, pi against it, +/-pi/2
    across the ring's plane (reaching under the loop from one side). W L^T
    for the link's own right-handed triad L = [axis, face normal, axis x normal]."""
    f_l, pad_l, _ = local_triad
    n_l = np.asarray(pad_l, float) if face == "pad" else -np.asarray(pad_l, float)
    L = np.column_stack([f_l, n_l, np.cross(f_l, n_l)])
    t = np.asarray(t_hat, float) / np.linalg.norm(t_hat)
    s_ = np.asarray(s_hat, float) - t * (np.asarray(s_hat, float) @ t)          # s_hat need not be square to the push
    s_ /= np.linalg.norm(s_)
    f = _rot(t, yaw) @ s_
    W = np.column_stack([f, t, np.cross(f, t)])
    return W @ L.T


def open_direction(grasp, contact, pivot, axis, tangent, min_arm_m=0.03):
    """The push direction for a stroke on the FREE half of a hinged object
    the other hand holds: the line of action through the holding grasp, away
    from it. Its moment about the grasp is zero, so the whole object does not
    swing in the hand; its moment about the hinge is what turns the half. A
    push along the hinge's tangent swung the scissors about the pinch instead
    (take 10, 2026-10-15: three strokes, the held half's tilt 72 -> 93 deg,
    the hinge 14 deg). Falls back to the tangent when the line through the
    grasp turns the hinge the wrong way or with less than min_arm_m of lever.
    Returns (direction, moment arm about the hinge in metres, mode)."""
    grasp, contact, pivot = (np.asarray(x, float) for x in (grasp, contact, pivot))
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    tangent = np.asarray(tangent, float) / np.linalg.norm(tangent)
    d = contact - grasp
    d /= np.linalg.norm(d)
    arm = float(np.cross(contact - pivot, d) @ axis)
    want = float(np.cross(contact - pivot, tangent) @ axis)
    if np.sign(arm) == np.sign(want) and abs(arm) >= min_arm_m:
        return d, abs(arm), "through_grasp"
    return tangent, abs(want), "tangent"


def _finger_frame(g, q, link, tip_local, joint_index=22):
    """The distal link's triad from the model, not assumed: the finger's
    axis (the tip vertex's direction from the link's origin), the pad's
    normal (the way the tip moves when the finger flexes, orthogonalised to
    the axis) and their cross (the width, the flexion axis); and the pad's
    depth: how far the link's hull extends on the pad side beyond the tip
    vertex, so the pad's face, not the tip point, is laid on the wall."""
    from pinch_place_planner import CACHE
    q0 = np.asarray(q, float).copy(); q0[joint_index] = 0.0
    g.forward(q0); p0, R0 = g.pose(link); t0 = p0 + R0 @ tip_local
    q1 = q0.copy(); q1[joint_index] = 0.3
    g.forward(q1); p1, R1 = g.pose(link); t1 = p1 + R1 @ tip_local
    f = np.asarray(tip_local, float) / np.linalg.norm(tip_local)
    curl = R0.T @ (t1 - t0)
    pad = curl - f * (curl @ f); pad /= np.linalg.norm(pad)
    w = np.cross(f, pad)
    man = json.loads(CACHE.with_suffix(".json").read_text()); pts = np.load(CACHE.with_suffix(".npz"))
    cloud = np.concatenate([pts[f"p{i}"] for i, sh in enumerate(man["shapes"]) if sh["link"] == link])
    depth = {"pad": float((cloud @ pad).max() - np.asarray(tip_local, float) @ pad),
             "back": float((cloud @ -pad).max() - np.asarray(tip_local, float) @ -pad)}
    return (f, pad, w), depth


def _solve_laid(g, seed, target, tip_local, R_pref, link="psyonic_right_index_L2"):
    """The right arm with the fingertip at `target` and the finger near the
    laid-along-the-handle frame: the orientation as a soft preference first
    (which trades a few millimetres of position for it), then the position
    alone from there (the tip lands on the wall, the orientation stays)."""
    cand, _ = g.solve_right(seed, target, link, tip_local, R_pref=R_pref)
    return g.solve_right(cand, target, link, tip_local)


def ordered_fits(fits, ranks=3):
    """The approach fits to try in turn: the best of each (face, direction)
    first, then each group's second, then third. Seeds converge on one basin,
    and the eight best fits by score were one configuration eight times; a
    stroke that fails from one basin fails from it again (2026-10-15)."""
    per = {}
    for f_ in sorted(fits, key=lambda t: t[0]):
        per.setdefault((f_[2], int(round(np.degrees(f_[1])))), []).append(f_)
    ordered = []
    for rank in range(ranks):
        layer = [v[rank] for v in per.values() if len(v) > rank]
        layer.sort(key=lambda t: t[0])
        ordered += layer
    return ordered


def _short(name):
    """A shape path as link/shape; an obstacle's id as it is."""
    return "/".join(name.split("/")[-2:]) if "/" in name else name


def _rot(axis, ang):
    from scipy.spatial.transform import Rotation
    return Rotation.from_rotvec(np.asarray(axis, float) / np.linalg.norm(axis) * ang).as_matrix()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--hold", required=True, help="the pick's targets.npz: its last row is the hold (28 columns)")
    ap.add_argument("--poses", required=True, help="settled_poses.json at the hold (every body's p, q_wxyz)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--debug", action="store_true", help="print each failing finger option's pose")
    ap.add_argument("--stroke", choices=("both", "close", "open"), default="both",
                    help="close: the push only; open: the opening stroke alone, from the loop as it is NOW (the replay plans it live after the close, "
                         "since the close lands where it lands: planned at the assumed closed angle the open finger pressed air 2 cm above the loop, take 7); both: in one plan")
    args = ap.parse_args()
    d = Path(args.scene_dir).resolve()
    spec = json.loads((d / "spec.json").read_text())
    fp = spec["finger_push"]
    gr = spec["grasp_replay"]
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from r5_arm_side import RIGHT_LINKS, both_arms_geometry, set_active
    from aida_r5_motion import Checker, joint_path, screen
    from pinch_place_planner import CACHE, _tip
    from plan_er_grasp import _at_settled_pose
    from support_geometry import mesh_samples, slab_boxes, support_boxes
    from r5_collision_exceptions import apply_to_checker

    hold_rows = np.load(args.hold)["targets"]
    hold = np.asarray(hold_rows[-1], float)
    if hold.shape[0] < 28:
        raise SystemExit("the hold row has fewer than 28 columns: the pick was not planned as a two-arm task")
    # the left hand as it IS at the hold, for the checker: the pick's closure
    # (the tips met through the rim, 4 mm apart), not its grip command (1.74
    # rad, the digits through each other - a self-collision in the model)
    pick_plan = json.loads((d / "grasp_plan" / "plan.json").read_text()) if (d / "grasp_plan" / "plan.json").exists() else {}
    hold_check = hold.copy()
    if pick_plan.get("pinch", {}).get("closed"):
        hold_check[8:14] = np.asarray(pick_plan["pinch"]["closed"], float)
    poses = json.loads(Path(args.poses).read_text())["bodies"]
    robot_json = json.loads((REPO / "workspace/knowledge/homehero_r5/robot.json").read_text()) if (REPO / "workspace/knowledge/homehero_r5/robot.json").exists() \
        else json.loads(Path(gr["robot"]).expanduser().read_text())
    urdf = str(Path(spec["robot"]["urdf"]).expanduser())
    base = list(spec["robot"]["position"]) + [0.0, 0.0, float(np.sin(np.radians(spec["robot"]["cad_yaw_deg"]) / 2)), float(np.cos(np.radians(spec["robot"]["cad_yaw_deg"]) / 2))]
    g = both_arms_geometry(urdf, base)

    # --- the scene: the pushed body, the held body, the pivot ------------------------
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    xc = UsdGeom.XformCache()
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    obj = stage.GetPrimAtPath(f"/World/Objects/{fp['object']}")
    body = {q.GetName(): q for q in Usd.PrimRange(obj) if q.HasAPI(UsdPhysics.RigidBodyAPI)}
    pushed, held = body[fp["pushed"]], body[fp["held"]]
    sp_pushed, sp_held = poses[pushed.GetPath().pathString], poses[held.GetPath().pathString]
    M_pushed = xc.GetLocalToWorldTransform(pushed)
    M_obj = xc.GetLocalToWorldTransform(obj)                 # the loop points are measured in the asset's frame (metres, as its file places them)
    loop_auth = np.array(list(M_obj.Transform(Gf.Vec3d(*fp["loop_point"]))), float)         # the loop's centre, authored pose
    loop_c, _ = _at_settled_pose(loop_auth[None], M_pushed, sp_pushed)
    loop_c = loop_c[0]
    pivot = np.asarray(sp_pushed["p"], float)                                                 # the bodies' origin is the pivot
    from scipy.spatial.transform import Rotation
    R_pushed = Rotation.from_quat([sp_pushed["q_wxyz"][1], sp_pushed["q_wxyz"][2], sp_pushed["q_wxyz"][3], sp_pushed["q_wxyz"][0]]).as_matrix()
    axis = R_pushed @ np.asarray(fp.get("pivot_axis", [0, 0, 1]), float)
    axis /= np.linalg.norm(axis)
    # the held half's loop, for the closing sense
    M_held = xc.GetLocalToWorldTransform(held)
    held_loop_auth = np.array(list(M_obj.Transform(Gf.Vec3d(*fp["held_loop_point"]))), float)
    held_loop_c, _ = _at_settled_pose(held_loop_auth[None], M_held, sp_held)
    held_loop_c = held_loop_c[0]
    r_push = loop_c - pivot; r_held = held_loop_c - pivot
    r_push -= axis * (r_push @ axis); r_held -= axis * (r_held @ axis)
    ang = np.arctan2(np.cross(r_push, r_held) @ axis, r_push @ r_held)                      # from the pushed loop to the held one, about the axis
    close_by = np.radians(float(fp.get("close_deg", 40.0))) * np.sign(ang)
    close_by = np.clip(close_by, -abs(ang) + np.radians(6), abs(ang) - np.radians(6)) if abs(ang) > np.radians(8) else close_by
    print(f"[push] pivot {pivot.round(4).tolist()} axis {axis.round(3).tolist()} loop {loop_c.round(4).tolist()} held loop {held_loop_c.round(4).tolist()} "
          f"angle between {np.degrees(ang):+.1f} deg, closing by {np.degrees(close_by):+.1f}", flush=True)

    # --- the checker: both arms, the right arm's links active against the obstacles ----
    top = float(bc.ComputeWorldBound(stage.GetPrimAtPath(spec["environment"]["support"] if spec["environment"]["support"].startswith("/World") else "/World/Environment" + spec["environment"]["support"][len(spec["environment"].get("prim", "")):])).ComputeAlignedRange().GetMax()[2])
    sup = stage.GetPrimAtPath(spec["environment"]["support"] if spec["environment"]["support"].startswith("/World") else "/World/Environment" + spec["environment"]["support"][len(spec["environment"].get("prim", "")):])
    obstacles = support_boxes(mesh_samples(sup, top), top)
    for o in fp.get("obstacles", []):
        obstacles.append(dict(o))
    # the held half where it is now, as a box; the pushed half is what the finger meets
    lp = [list(xc.GetLocalToWorldTransform(m_).Transform(Gf.Vec3d(*v))) for m_ in Usd.PrimRange(held)
          if m_.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m_).GetPurposeAttr().Get() != "guide" for v in UsdGeom.Mesh(m_).GetPointsAttr().Get()]
    Ws, _ = _at_settled_pose(np.asarray(lp, float), M_held, sp_held)
    obstacles += slab_boxes(Ws, n=int(fp.get("held_slabs", 8)), margin=0.005, prefix=f"{fp['object']}:{fp['held']}")   # the hanging half as slabs along its length
    config = json.loads(Path(fp.get("planning_config", "~/Documents/Github/HomeHero/artifacts/isaac/aida_r5_20261004/shared_learning_v6/planning.json")).expanduser().read_text()) \
        if Path(fp.get("planning_config", "~/Documents/Github/HomeHero/artifacts/isaac/aida_r5_20261004/shared_learning_v6/planning.json")).expanduser().exists() else {"planning": {"table_margin_m": 0.0}}
    checker = Checker(g, CACHE, obstacles, clearance=float(config["planning"].get("table_margin_m", 0.0)))
    n_pairs = set_active(checker, RIGHT_LINKS)       # the right arm against the room AND the robot's own body (the Checker's pairs are the left arm's)
    n_dropped = apply_to_checker(checker)
    print(f"[push] checker: {len(checker.active)} right-side shapes, {n_pairs} self pairs, {n_dropped} excepted, {len(obstacles)} obstacles", flush=True)
    for o in obstacles:
        print(f"[push]   obstacle {o['id']}: {np.round(o['min'], 3).tolist()} .. {np.round(o['max'], 3).tolist()}", flush=True)

    class RightChecker:                                  # HomeHero's RRT sees the right arm's eight joints and hand, the left held
        class _G:
            bounds = g.bounds[8:]
        g = _G()

        def __call__(self, q14, details=False):
            return checker(np.r_[hold_check[:14], q14], details)

    right_checker = RightChecker()

    # --- the right hand's preshape and the fingertip -------------------------------
    preshape = np.asarray(fp.get("preshape", [0.0, 1.5, 1.5, 1.5, 0.0, -1.47]), float)   # index straight, the others curled, the thumb away
    tip_local = _tip("psyonic_right_index_L2")
    q = hold.copy()
    q[22:28] = preshape
    qc = hold_check.copy(); qc[22:28] = preshape

    def clear(row):                                      # the checker sees the left hand at its closure
        rc = np.asarray(row, float).copy(); rc[8:14] = hold_check[8:14]
        return checker(rc)

    if not clear(q):
        rc = q.copy(); rc[8:14] = hold_check[8:14]
        print(f"PUSH_PLAN FAIL the preshape collides at the hold: {checker(rc, True)[:4]}", flush=True)
        return 1
    rows, stages = [], []

    def walk_to(goal, label, n):
        start = rows[-1] if rows else q
        for f in np.linspace(0, 1, n + 1)[1:]:
            rows.append(start * (1 - f) + goal * f); stages.append(label)

    rows.append(hold.copy()); stages.append("start")
    if np.max(np.abs(hold[22:28] - preshape)) > 0.05:                 # the hand to its preshape (already there for a later stroke)
        walk_to(q, "right_preshape", 45)

    # --- the arc: tip targets from an approach point to the closed angle --------------
    # the push geometry, measured: the handle's outer wall where the finger
    # meets it, and the finger laid along the handle with its pad on the wall
    sgn = 1.0 if close_by > 0 else -1.0
    rv0 = loop_c - pivot
    t0 = np.cross(axis, rv0); t0 /= np.linalg.norm(t0); t0 *= sgn                           # the closing motion's tangent at the loop point
    s0 = np.cross(t0, axis); s0 /= np.linalg.norm(s0)                                        # in the ring's plane, across the motion: along the handle
    if s0 @ (pivot - loop_c) < 0:
        s0 = -s0                                                                             # toward the pivot
    pp = [list(xc.GetLocalToWorldTransform(m_).Transform(Gf.Vec3d(*v))) for m_ in Usd.PrimRange(pushed)
          if m_.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m_).GetPurposeAttr().Get() != "guide" for v in UsdGeom.Mesh(m_).GetPointsAttr().Get()]
    Wp, _ = _at_settled_pose(np.asarray(pp, float), M_pushed, sp_pushed)
    wall0 = outer_wall(Wp, loop_c, axis, t0, s0, float(fp.get("wall_band_m", 0.004)), float(fp.get("wall_window_m", 0.010)))
    if wall0 is None:
        print("PUSH_PLAN FAIL no points of the pushed half within the wall band about the loop point", flush=True)
        return 1
    triad, depth = _finger_frame(g, q, "psyonic_right_index_L2", tip_local)
    press = float(fp.get("press_m", 0.003))
    print(f"[push] wall {wall0.round(4).tolist()}: {float((loop_c - wall0) @ t0) * 1000:.1f} mm from the loop point against the motion; "
          f"the finger's pad {depth['pad'] * 1000:.1f} mm and back {depth['back'] * 1000:.1f} mm beyond the tip vertex; pressed {press * 1000:.0f} mm in; "
          f"handle along {s0.round(2).tolist()}, pushing along {t0.round(2).tolist()}", flush=True)

    station = float(fp.get("pad_station_m", 0.015))                                         # the pad's point that meets the wall, back from the tip vertex
    yaws = np.radians(np.asarray(fp.get("finger_yaws_deg", [0, 45, 90, 135, 180, 225, 270, 315]), float))
    faces = list(fp.get("finger_faces", ["back", "pad"]))
    rng = np.random.default_rng(int(fp.get("ik_seed", 20261015)))
    lo, hi = g.bounds[8:, 0], g.bounds[8:, 1]
    rate = float(fp.get("joint_rate_rad_per_tick", 0.012))               # the reach, at most this per joint per tick (0.36 rad/s)
    tip_v = float(fp.get("tip_speed_m_s", 0.03))                         # the fingertip along the arc: 3 cm/s (10 ticks per 5 mm step)

    def stroke(name, push_base, wall_base, theta_from, theta_to, q_start, approach_base=None, aim_from=None):
        """One stroke of the finger: the wall (a point riding with the pushed
        body, given at theta 0) pressed along push_base (the finger's face
        normal, also riding; the arc's tangent for the close, straight down for
        the open) while the contact follows the body from theta_from to theta_to
        about the pivot. The approach configuration over the finger's
        faces x directions x seeds (the least-squares fit from one seed lands in
        one basin, and the nearest fit may run through the torso), the clear
        fit nearest q_start; HomeHero's RRT to it; then straight in, the arc,
        and out. Appends rows/stages; returns False on failure."""
        a_base = -push_base if approach_base is None else np.asarray(approach_base, float) / np.linalg.norm(approach_base)

        def frame(theta):
            Rt = _rot(axis, theta)
            wall = pivot + Rt @ (wall_base - pivot)
            if aim_from is not None:                                    # the line of action through the grasp, as the contact turns
                t = wall - np.asarray(aim_from, float); t /= np.linalg.norm(t)
            else:
                t = Rt @ push_base
            s_ = Rt @ s0; s_ = s_ - t * (s_ @ t); s_ /= np.linalg.norm(s_)
            return t, s_, wall, Rt @ a_base

        def finger_R(theta, yaw, face):
            t, s_, _, _ = frame(theta)
            return finger_rotation(triad, t, s_, yaw, face)

        def tip_target(theta, yaw, face, back=0.0):
            t, s_, wall, a = frame(theta)
            f = _rot(t, yaw) @ s_
            return wall + f * station - t * (depth[face] - press) + a * back                # the face on the wall, pressed in; `back` along the approach before contact

        n_arc = max(2, int(abs(theta_to - theta_from) * np.linalg.norm(wall_base - pivot) / STEP_M) + 1)
        thetas = np.linspace(theta_from, theta_to, n_arc)
        fits, why = [], {}
        for face in faces:                                            # the finger's back (a hook, palm down) or its pad (palm up)
            for yaw in yaws:                                          # the finger's direction at the wall, about the push
                R_pref = finger_R(theta_from, yaw, face)
                first = tip_target(theta_from, yaw, face, back=float(fp.get("approach_m", 0.04)))
                key = f"{face} {int(np.degrees(yaw))}"
                for k in range(int(fp.get("ik_seeds", 12))):
                    sd = q_start.copy()
                    if k:
                        sd[14:22] = np.clip(q_start[14:22] + rng.normal(0.0, 0.4 + 0.1 * k, 8), lo, hi)
                    cand, fit = _solve_laid(g, sd, first, tip_local, R_pref)
                    if not fit["passed"]:
                        why.setdefault(key, []).append(f"IK {fit['position_error_m'] * 1000:.0f} mm")
                        continue
                    if not clear(cand):
                        rc = cand.copy(); rc[8:14] = hold_check[8:14]
                        why.setdefault(key, []).append("collides " + " ".join(f"{_short(a)}~{_short(b)}" for a, b in checker(rc, True)[:2]))
                        if k == 0 and args.debug:
                            g.forward(cand); pL2, RL2 = g.pose("psyonic_right_index_L2"); tipw = pL2 + RL2 @ tip_local
                            off0 = float(np.linalg.norm(Rotation.from_matrix(R_pref.T @ RL2).as_rotvec()))
                            print(f"[debug] {name} {key}: target {first.round(3).tolist()} tip {tipw.round(3).tolist()} L2 origin {pL2.round(3).tolist()} "
                                  f"finger axis {(RL2 @ triad[0]).round(2).tolist()} face normal {(RL2 @ (triad[1] if face == 'pad' else -triad[1])).round(2).tolist()} "
                                  f"{np.degrees(off0):.0f} deg off; {[(_short(a), _short(b)) for a, b in checker(rc, True)[:3]]}", flush=True)
                        continue
                    g.forward(cand); _, R_got = g.pose("psyonic_right_index_L2")
                    off = float(np.linalg.norm(Rotation.from_matrix(R_pref.T @ R_got).as_rotvec()))
                    fits.append((float(np.linalg.norm(cand[14:22] - q_start[14:22])) + off, yaw, face, k, off, cand))
        if not fits:
            print(f"PUSH_PLAN FAIL {name}: the approach is unreachable or collides for every finger face and direction: "
                  + "; ".join(f"{y}: {w[0]} x{len(w)}" for y, w in why.items()), flush=True)
            return False
        fits.sort(key=lambda t: t[0])
        print(f"[push] {name} approach: {len(fits)} clear fits over {len(faces)} faces x {len(yaws)} directions; "
              f"clear {sorted(set((f[2], int(np.degrees(f[1]))) for f in fits))}", flush=True)
        slabs = slab_boxes(pivot + (Wp - pivot) @ _rot(axis, theta_from).T, n=int(fp.get("pushed_slabs", 8)), margin=0.005, prefix=f"{fp['object']}:{fp['pushed']}")
        reach_ck = Checker(g, CACHE, obstacles + slabs, clearance=float(config["planning"].get("table_margin_m", 0.0)))
        set_active(reach_ck, RIGHT_LINKS)
        apply_to_checker(reach_ck)

        class ReachChecker:
            g = RightChecker._G()

            def __call__(self, q14, details=False):
                return reach_ck(np.r_[hold_check[:14], q14], details)

        reach_checker = ReachChecker()
        n0 = len(rows)
        # the stroke from the best fits in turn: the arc continues from the
        # approach configuration by IK and can drift into the torso (offline,
        # take 5's hold: the first open fit's arc put the upper arm in the
        # casing); the next fit starts the arc from another basin
        ordered = ordered_fits(fits)
        for _, yaw_best, face_best, k_best, off_best, q_first in ordered[:int(fp.get("stroke_tries", 10))]:
            del rows[n0:]; del stages[n0:]
            print(f"[push] {name}: trying {face_best} yaw {np.degrees(yaw_best):.0f} deg (seed {k_best}), {np.degrees(off_best):.0f} deg off its frame; "
                  f"right arm {np.round(q_first[14:22], 2).tolist()}", flush=True)
            # the reach: HomeHero's RRT for the right arm, the hand in its
            # preshape, the pushed half (where it is now) an obstacle too: the
            # open stroke's reach swept the hand through the closed half and
            # knocked it to the hinge's far stop (take 6, 2026-10-15)
            try:
                dense, info = joint_path(reach_checker, q_start[14:22], q_first[14:22], preshape, timeout=float(fp.get("rrt_timeout_s", 120)))
            except Exception as e:
                print(f"[push] {name} reach failed: {e}", flush=True)
                continue
            for qa in dense:
                r = q_start.copy(); r[14:22] = np.asarray(qa)[:8]
                prev = rows[-1]
                n = max(1, int(np.ceil(np.max(np.abs(r[14:22] - prev[14:22])) / rate)))
                for f in np.linspace(0, 1, n + 1)[1:]:
                    rows.append(prev * (1 - f) + r * f); stages.append(f"{name}_reach")
            cur = rows[-1].copy()
            prev_tgt = None
            failed = None
            for label, targets in ((f"{name}_approach", [(tip_target(theta_from, yaw_best, face_best, back=b), theta_from) for b in np.linspace(float(fp.get("approach_m", 0.04)), 0.0, 9)[1:]]),
                                   (name, [(tip_target(th, yaw_best, face_best), th) for th in thetas[1:]]),
                                   (f"{name}_release", [(tip_target(thetas[-1], yaw_best, face_best, back=b), thetas[-1]) for b in np.linspace(0.0, float(fp.get("release_m", 0.05)), 11)[1:]])):
                for tgt, th in targets:
                    R_pref = finger_R(th, yaw_best, face_best)
                    nxt, fit = _solve_laid(g, cur, tgt, tip_local, R_pref)
                    if fit["passed"] and not clear(nxt):                                # the continuation drifted into something: re-solve from nearby seeds
                        for _k in range(10):
                            sd = cur.copy(); sd[14:22] = np.clip(cur[14:22] + rng.normal(0.0, 0.15 + 0.03 * _k, 8), lo, hi)
                            alt, fit_alt = _solve_laid(g, sd, tgt, tip_local, R_pref)
                            if fit_alt["passed"] and clear(alt) and np.max(np.abs(alt[14:22] - cur[14:22])) < 0.6:
                                nxt, fit = alt, fit_alt
                                break
                    if not fit["passed"]:
                        failed = f"{label}: IK {fit['position_error_m'] * 1000:.1f} mm at {tgt.round(3).tolist()}"
                        break
                    if not clear(nxt):
                        rc = nxt.copy(); rc[8:14] = hold_check[8:14]
                        failed = f"{label}: collision at {tgt.round(3).tolist()}: {[(_short(a), _short(b)) for a, b in checker(rc, True)[:2]]}"
                        break
                    cur = nxt
                    prev = rows[-1]
                    step_len = float(np.linalg.norm(tgt - prev_tgt)) if prev_tgt is not None else STEP_M
                    n = max(int(np.ceil(step_len / (tip_v / HZ))), int(np.ceil(np.max(np.abs(cur[14:22] - prev[14:22])) / rate)), 1)
                    for f in np.linspace(0, 1, n + 1)[1:]:
                        rows.append(prev * (1 - f) + cur * f); stages.append(label)
                    prev_tgt = tgt
                if failed:
                    break
            if failed:
                print(f"[push] {name} {failed}", flush=True)
                continue
            return True
        del rows[n0:]; del stages[n0:]
        print(f"PUSH_PLAN FAIL {name}: no fit completed the stroke", flush=True)
        return False

    # the close: the loop pushed along its arc by close_by, from the handle's outer wall
    if args.stroke in ("both", "close"):
        if not stroke("push", t0, wall0, 0.0, close_by, q):
            return 1
    open_deg = float(fp.get("open_deg", 0.0))
    if args.stroke == "open" and open_deg <= 0:
        raise SystemExit("--stroke open needs finger_push.open_deg")
    if open_deg > 0 and args.stroke in ("both", "open"):
        # the open: the same finger from the other side of the loop, pushing it
        # back by open_deg (the half closed by a push stays closed: at 17 deg the
        # loops rest on each other, as real scissors stay where they are put)
        theta_end = close_by if args.stroke == "both" else 0.0            # alone: the loop as it is now IS the closed state
        R_end = _rot(axis, theta_end)
        # pressed along the opening arc's tangent at the contact (square on the
        # loop's upper wall, whose normal is that tangent: pressed straight down
        # on a wall 34 deg off the vertical the pad slid along it and turned the
        # half 3 deg, take 8), but APPROACHED from straight above: the tangent's
        # own trailing direction runs into the held loop and the pinch 2 cm away
        approach_open = np.asarray(fp.get("open_approach_dir", [0.0, 0.0, 1.0]), float); approach_open /= np.linalg.norm(approach_open)
        Wp_end = pivot + (Wp - pivot) @ R_end.T
        # the band about a point farther out along the handle than the loop point:
        # closed, the pushed loop's top wall lies 2 cm under the held loop and the
        # left hand's pinch, and the finger coming down there met the held half
        # (offline, take 5's hold); toward the handle's end there is room, and
        # the lever is longer
        s_end = R_end @ s0
        loop_end = pivot + R_end @ (loop_c - pivot) - s_end * float(fp.get("open_station_m", 0.025))
        r_band = loop_end - pivot
        tangent_open = -sgn * np.cross(axis, r_band); tangent_open /= np.linalg.norm(tangent_open)   # the opening motion's tangent at the band
        # the left hand's pinch (the midpoint of its index and thumb tips at the closure)
        g.forward(hold_check)
        pinch_w = 0.5 * (g.point("psyonic_left_index_L2", _tip("psyonic_left_index_L2")) + g.point("psyonic_left_thumb_L2", _tip("psyonic_left_thumb_L2")))
        band, wb = float(fp.get("wall_band_m", 0.004)), float(fp.get("wall_window_m", 0.010))
        t_open_end, arm, aim_mode = tangent_open, float(np.linalg.norm(np.cross(r_band, tangent_open))), "tangent"
        if fp.get("open_aim", "grasp") == "grasp":
            t_open_end, arm, aim_mode = open_direction(pinch_w, loop_end, pivot, axis, tangent_open)
        wall_open_end = outer_wall(Wp_end, loop_end, axis, t_open_end, s_end, band, wb)
        if wall_open_end is not None and aim_mode == "through_grasp":     # re-aim at the wall point itself
            t_open_end, arm, aim_mode = open_direction(pinch_w, wall_open_end, pivot, axis, tangent_open)
            wall_open_end = outer_wall(Wp_end, loop_end, axis, t_open_end, s_end, band, wb)
        if wall_open_end is None:
            print("PUSH_PLAN FAIL open: no points of the pushed half within the wall band on the loop's other side", flush=True)
            return 1
        print(f"[push] open aim: {aim_mode}, the pinch at {pinch_w.round(4).tolist()}, {float(np.linalg.norm(wall_open_end - pinch_w)) * 100:.1f} cm from the contact; "
              f"lever about the hinge {arm * 100:.1f} cm (the tangent's {float(np.linalg.norm(np.cross(wall_open_end - pivot, tangent_open))) * 100:.1f}); "
              f"{np.degrees(np.arccos(np.clip(t_open_end @ tangent_open, -1, 1))):.0f} deg off the tangent", flush=True)
        wall_open0 = pivot + R_end.T @ (wall_open_end - pivot)                                # the same point at theta 0, so it rides with the body
        theta_to = theta_end - np.sign(close_by) * np.radians(open_deg)
        if args.stroke == "both":
            theta_to = float(np.clip(theta_to, min(0.0, close_by), max(0.0, close_by)))     # back toward where it hung, not past it
        print(f"[push] open: the loop's upper wall {wall_open_end.round(4).tolist()} ({float((loop_end - wall_open_end) @ t_open_end) * 1000:.1f} mm from the band's centre "
              f"against the opening), pressed along {t_open_end.round(2).tolist()}, approached from {approach_open.round(2).tolist()}, "
              f"from {np.degrees(theta_end):+.0f} back to {np.degrees(theta_to):+.0f} deg", flush=True)
        if not stroke("open", R_end.T @ t_open_end, wall_open0, theta_end, theta_to, rows[-1].copy() if args.stroke == "both" else q,
                      approach_base=R_end.T @ approach_open, aim_from=pinch_w if aim_mode == "through_grasp" else None):
            return 1
    for _ in range(int(float(fp.get("hold_s", 2.0)) * HZ)):
        rows.append(rows[-1].copy()); stages.append("hold")
    arr = np.asarray(rows)
    np.savez(args.out, targets=arr, stages=np.asarray(stages))
    counts = {s: stages.count(s) for s in dict.fromkeys(stages)}
    print(f"PUSH_PLAN OK {spec['name']} ({args.stroke}): {len(arr)} ticks ({len(arr) / HZ:.1f} s at 30 Hz), stages {counts}"
          + (f", closing by {np.degrees(close_by):+.1f} deg" if args.stroke != "open" else "")
          + (f", opening by {open_deg:.0f} deg" if (open_deg > 0 and args.stroke != "close") else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
