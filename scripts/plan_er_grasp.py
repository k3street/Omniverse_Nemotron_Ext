#!/usr/bin/env python3
"""Plan a grasp of a scene's target with HomeHero's R5 planner, for
replay_grasp.py to execute and film.

From the built scene (pxr): the robot's base pose and start posture, the
target's surface points in world, the support's box and every other object
on it as an obstacle. Hands them to homehero_grasp_adapter.py in HomeHero's
venv (pinocchio, coal), writes the planned targets (.npz, the reference
format replay_grasp.py reads) and the planner's account (plan.json), and
points the scene spec's grasp_replay at them.

    python scripts/plan_er_grasp.py config/scenes/aida_r5_dining_can_grasp.json
writes workspace/scenes/<name>/grasp_plan/{request.json, plan.json, targets.npz}
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
HH = Path("/home/kimate/Documents/Github/HomeHero")
HH_PY = HH / ".venv/bin/python"
EXPERIMENT = HH / "artifacts/isaac/aida_r5_20261004/reference_learning_v1"
CONFIG = HH / "config/skill_pipeline/aida_r5_geometry_pickup_v6.json"
ARM = ["amber_left_shoulder_lift_joint", "amber_left_shoulder_pitch_joint", "amber_left_shoulder_roll_joint",
       "amber_left_upper_arm_roll_joint", "amber_left_elbow_pitch_joint", "amber_left_forearm_roll_joint",
       "amber_left_wrist_pitch_joint", "amber_left_wrist_roll_joint"]


def _chess_survey(stage, target, xc, bc, dest, grip_fraction):
    """The pieces of the target's colour that can move from the start, each
    with its grip (a pinch at grip_fraction of its height, across its width
    there) and the squares it may go to (pawns one or two ahead, knights'
    jumps), for pinch_place_planner.survey."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    import numpy as np
    Mb = xc.GetLocalToWorldTransform(stage.GetPrimAtPath(dest["board"]))
    cw = np.array(Mb.Transform(Gf.Vec3d(*dest["centre_board"])), float)
    cs = [np.array(Mb.Transform(Gf.Vec3d(*c)), float) for c in dest["corners_board"]]
    sq = dest["square_m"]
    fd, rd = (cs[1] - cs[0]) / sq, (cs[3] - cs[0]) / sq
    f0, r0 = "abcdefgh".index(dest["square"][0]), int(dest["square"][1]) - 1
    a1 = cw - f0 * sq * fd - r0 * sq * rd

    def centre(name):
        return a1 + "abcdefgh".index(name[0]) * sq * fd + (int(name[1]) - 1) * sq * rd
    info_t = json.loads(target.GetCustomDataByKey("simReady:chess"))
    set_prim = target
    while set_prim.GetParent().GetPath().pathString != "/World/Objects":
        set_prim = set_prim.GetParent()
    out = []
    for q in Usd.PrimRange(set_prim):
        if not q.HasCustomDataKey("simReady:chess"):
            continue
        info = json.loads(q.GetCustomDataByKey("simReady:chess"))
        if info["color"] != info_t["color"] or info["piece"] not in ("p", "n"):
            continue
        sqname = info["start_square"]
        f, r = "abcdefgh".index(sqname[0]), int(sqname[1]) - 1
        step = 1 if info["color"] == "white" else -1
        if info["piece"] == "p":
            goals = [(f, r + step), (f, r + 2 * step)]
        else:
            goals = [(f + df, r + 2 * step) for df in (-1, 1)]
        goals = [f"{'abcdefgh'[x]}{y + 1}" for x, y in goals if 0 <= x < 8 and 0 <= y < 8]
        pts = []
        for m_ in Usd.PrimRange(q):
            if m_.IsA(UsdGeom.Mesh):
                M = xc.GetLocalToWorldTransform(m_)
                pts += [list(M.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(m_).GetPointsAttr().Get()]
        P = np.asarray(pts)
        base, h = float(P[:, 2].min()), float(np.ptp(P[:, 2]))
        axis = (P[:, :2].max(0) + P[:, :2].min(0)) / 2
        gz = base + grip_fraction * h
        band = P[np.abs(P[:, 2] - gz) < 0.003]
        width = float(2 * np.linalg.norm(band[:, :2] - axis, axis=1).max())
        out.append({"square": sqname, "piece": info["piece"], "body_id": f"{set_prim.GetName()}:{q.GetName()}",
                    "grip_point": [float(axis[0]), float(axis[1]), gz], "grip_width_m": width,
                    "destinations": [{"square": gsq, "place_point": (centre(gsq) + np.array([0, 0, gz - base])).tolist()}
                                     for gsq in goals]})
    return out


def _slices(body, xc, bc, name, band_m=0.01):
    """A body as boxes stacked in height bands, each over that band of its
    mesh: a chess piece is narrow at its neck and wide at its base, and one
    box over the whole piece closes the gap a fingertip passes through (the
    white king's box met the thumb in 43 of 47 pinch grasps). A flat body
    (the board) stays one box."""
    from pxr import Gf, Usd, UsdGeom
    import numpy as np
    r = bc.ComputeWorldBound(body).ComputeAlignedRange()
    if r.GetSize()[2] < 2 * band_m:
        return [dict(id=name, min=list(r.GetMin()), max=list(r.GetMax()))]
    pts = []
    for q in Usd.PrimRange(body):
        if q.IsA(UsdGeom.Mesh):
            M = xc.GetLocalToWorldTransform(q)
            pts += [list(M.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(q).GetPointsAttr().Get()]
    if not pts:
        return [dict(id=name, min=list(r.GetMin()), max=list(r.GetMax()))]
    P = np.asarray(pts)
    z0, z1 = P[:, 2].min(), P[:, 2].max()
    out = []
    for k, lo in enumerate(np.arange(z0, z1, band_m)):
        hi = min(lo + band_m, z1)
        band = P[(P[:, 2] >= lo - 1e-6) & (P[:, 2] <= hi + 1e-6)]
        if len(band):
            out.append(dict(id=f"{name}:{k}", min=[*band[:, :2].min(0).tolist(), float(lo)],
                            max=[*band[:, :2].max(0).tolist(), float(hi)]))
    return out


def _at_settled_pose(world_pts, authored_matrix, settled):
    """World points of a body, moved from the body's authored pose to its
    settled one (p, q_wxyz): a frame change about the body's origin, the
    authored rotation taken from the matrix with its scale normalised out
    (an asset's scale op otherwise scattered the points, 2026-10-15)."""
    import numpy as np
    from scipy.spatial.transform import Rotation as _R
    M = np.array(authored_matrix).T                                   # column convention: axes in the columns, translation in the last
    p_auth = M[:3, 3].copy()
    R_auth = M[:3, :3] / np.linalg.norm(M[:3, :3], axis=0)
    R_set = _R.from_quat([settled["q_wxyz"][1], settled["q_wxyz"][2], settled["q_wxyz"][3], settled["q_wxyz"][0]]).as_matrix()
    p_set = np.asarray(settled["p"], float)
    pts = (np.asarray(world_pts, float) - p_auth) @ (R_set @ R_auth.T).T + p_set
    return pts, float(np.linalg.norm(p_set - p_auth))


def _loop_grip_task(stage, obj, xc, d, gp, drop):
    """A flat tool's bodies (a scissors' halves) densely sampled at their
    settled poses, for loop_grip_planner.py."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from tool_grip_planner import sample_faces

    settled = json.loads((d / "settled_poses.json").read_text())["bodies"] if (d / "settled_poses.json").exists() else {}
    bodies = {}
    for b in Usd.PrimRange(obj):
        if not b.HasAPI(UsdPhysics.RigidBodyAPI) or not b.IsActive():
            continue
        P = []
        for m in Usd.PrimRange(b):
            if m != b and m.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            if m.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m).GetPurposeAttr().Get() != "guide":
                M = xc.GetLocalToWorldTransform(m)
                mesh = UsdGeom.Mesh(m)
                V = np.asarray([list(M.Transform(Gf.Vec3d(*v))) for v in mesh.GetPointsAttr().Get()], float)
                P.append(sample_faces(V, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()), 0.0015))
        if not P:
            continue
        W = np.vstack(P)
        sp = settled.get(b.GetPath().pathString)
        W = _at_settled_pose(W, xc.GetLocalToWorldTransform(b), sp)[0] if sp else W - drop
        bodies[b.GetName()] = np.round(W, 5).tolist()
    print(f"[loop] {obj.GetName()}: {', '.join(f'{k} {len(v)} points' for k, v in bodies.items())}", flush=True)
    return {"kind": "loop_grip", "bodies": bodies, "loop_grip": gp.get("loop_grip", {}),
            **{k: gp[k] for k in ("lift_m", "cycles", "hold_s", "rrt_timeout_s") if k in gp}}


def _tool_grip_task(stage, tool, xc, d, gp, drop, poses_path=None):
    """A motor tool's parts, by its own behaviour (simReady:behaviors, the
    motor law): the throttle joint's body is the trigger and its other body
    the handle's; the power press-fit's body the battery; the output joint
    (the chuck) says which way is forward. Every body's surface densely
    sampled, at its settled pose when the replay wrote one."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from tool_grip_planner import sample_faces

    beh = None
    for q in Usd.PrimRange(tool):
        raw = q.GetCustomDataByKey("simReady:behaviors")
        if raw:
            beh = next((b for b in json.loads(raw) if b.get("type") == "motor"), None)
            if beh:
                break
    if beh is None:
        raise SystemExit(f"{tool.GetName()}: no motor behaviour (simReady:behaviors) - the tool grip reads its parts from it")
    joints = {q.GetName(): q for q in Usd.PrimRange(tool) if q.IsA(UsdPhysics.Joint)}
    for k in ("throttle", "output"):
        if beh.get(k) not in joints:
            raise SystemExit(f"{tool.GetName()}: the motor's {k} joint {beh.get(k)!r} is not in the asset")
    # the bodies where they rest - or where they are NOW (a replay's live poses: a drill whose switch the
    # hand has just set, gripped by a later stage, tool_then.py)
    pp_ = Path(poses_path) if poses_path else d / "settled_poses.json"
    settled = json.loads(pp_.read_text())["bodies"] if pp_.exists() else {}

    def world_fn(body):
        """authored-world point(s) -> where the body is now"""
        sp = settled.get(body.GetPath().pathString)
        M = xc.GetLocalToWorldTransform(body)
        if sp:
            return lambda W: _at_settled_pose(np.atleast_2d(W), M, sp)[0]
        return lambda W: np.atleast_2d(W) - drop

    from tool_grip_planner import face_normals

    bodies, fns, faces = {}, {}, {}
    for b in Usd.PrimRange(tool):
        if not b.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        fn = world_fn(b)
        fns[b.GetPath().pathString] = fn
        P, F = [], []
        for m in Usd.PrimRange(b):
            if m != b and m.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            if m.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m).GetPurposeAttr().Get() != "guide":
                M = xc.GetLocalToWorldTransform(m)
                mesh = UsdGeom.Mesh(m)
                V = np.asarray([list(M.Transform(Gf.Vec3d(*v))) for v in mesh.GetPointsAttr().Get()], float)
                P.append(sample_faces(V, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()),
                                      float(gp.get("tool_grip", {}).get("sample_m", 0.003))))
                # the triangles too (at the settled pose: the vertices moved, the normals
                # recomputed), for the force a digit makes through a face it pushes
                c_, n_, a_ = face_normals(fn(V), list(mesh.GetFaceVertexCountsAttr().Get()),
                                          list(mesh.GetFaceVertexIndicesAttr().Get()))
                if str(mesh.GetOrientationAttr().Get() or "rightHanded") == "leftHanded":
                    n_ = -n_
                F.append((c_, n_, a_))
        if P:
            bodies[b.GetName()] = np.round(fn(np.vstack(P)), 5)
        if F:
            faces[b.GetName()] = {"c": np.round(np.vstack([f[0] for f in F]), 5).tolist(),
                                  "n": np.round(np.vstack([f[1] for f in F]), 5).tolist(),
                                  "area": np.round(np.concatenate([f[2] for f in F]), 9).tolist()}

    def joint_world(jp):
        j = UsdPhysics.Joint(jp)
        b0 = stage.GetPrimAtPath(j.GetBody0Rel().GetTargets()[0])
        b1 = stage.GetPrimAtPath(j.GetBody1Rel().GetTargets()[0])
        M0 = xc.GetLocalToWorldTransform(b0)
        lp = jp.GetAttribute("physics:localPos0").Get()
        lr = jp.GetAttribute("physics:localRot0").Get()
        ax = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}[str(jp.GetAttribute("physics:axis").Get() or "X")]
        ax_b = Gf.Rotation(Gf.Quatd(lr.GetReal(), Gf.Vec3d(lr.GetImaginary()))).TransformDir(ax)
        p0 = np.asarray(list(M0.Transform(Gf.Vec3d(lp))), float)
        p1 = np.asarray(list(M0.Transform(Gf.Vec3d(lp) + ax_b * 0.01)), float)
        w0, w1 = fns[b0.GetPath().pathString](np.vstack([p0, p1]))
        return b0.GetName(), b1.GetName(), w0, (w1 - w0) / np.linalg.norm(w1 - w0)

    h_body, trig_body, _, t_axis = joint_world(joints[beh["throttle"]])
    # the trigger's return spring (its drive: the force the finger works against)
    jt = joints[beh["throttle"]]
    k_trig = float(jt.GetAttribute("drive:linear:physics:stiffness").Get() or 0.0) if jt.HasAttribute("drive:linear:physics:stiffness") else 0.0
    _, _, chuck_p, _ = joint_world(joints[beh["output"]])
    pull = t_axis * (1.0 if float(beh.get("throttle_full", 1.0)) >= 0 else -1.0)
    up = np.array([0.0, 0.0, 1.0])
    bat = None
    if beh.get("power") in joints:
        _, bat, _, _ = joint_world(joints[beh["power"]])
    trig = bodies[trig_body]
    H = bodies[h_body]
    lo = float(bodies[bat][:, 2].max()) + 0.004 if bat in bodies else float(H[:, 2].min()) + 0.03
    hi = float(trig[:, 2].min()) - 0.002
    handle = H[(H[:, 2] > lo) & (H[:, 2] < hi)]
    hc = (handle.max(0) + handle.min(0)) / 2
    fwd = chuck_p - hc
    fwd[2] = 0.0
    fwd /= np.linalg.norm(fwd)
    print(f"[tool] {tool.GetName()}: handle {h_body} ({len(handle)} points, z {lo:.3f}-{hi:.3f}), trigger {trig_body} (pull "
          f"{np.round(pull, 3).tolist()}), battery {bat}, chuck at {np.round(chuck_p, 3).tolist()}, forward {np.round(fwd, 3).tolist()}; "
          f"{sum(len(v) for v in bodies.values())} surface points over {len(bodies)} bodies", flush=True)
    return {"kind": "tool_grip",
            "tool": {"bodies": {k: v.tolist() for k, v in bodies.items()}, "handle_body": h_body, "trigger_body": trig_body,
                     "battery_body": bat, "handle_points": handle.tolist(), "trigger_points": trig.tolist(),
                     "pull": pull.tolist(), "forward": fwd.tolist(), "up": up.tolist(),
                     "chuck_point": chuck_p.tolist(), "travel_m": abs(float(beh.get("throttle_full", 0.01))), "behavior": beh,
                     "trigger_spring_n_per_m": k_trig, "trigger_faces": faces.get(trig_body)},
            "tool_grip": gp.get("tool_grip", {}),
            **{k: gp[k] for k in ("approach_m", "lift_m", "close_s", "pull_s", "run_s", "release_s", "hold_s", "rrt_timeout_s") if k in gp}}


def _button_press_task(stage, obj, xc, d, gp, drop):
    """A push button's parts at their settled poses: the button's surface,
    its prismatic joint's axis in the world and its travel (the way its range
    runs is inward), and the appliance's other bodies (what the hand must
    clear: the panel around it, the door beside it)."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from tool_grip_planner import sample_faces

    opts = gp.get("button_press", {})
    jp = next((q for q in Usd.PrimRange(obj) if q.IsA(UsdPhysics.PrismaticJoint) and q.GetName() == opts.get("joint")), None)
    if jp is None:
        raise SystemExit(f"{obj.GetName()}: button_press.joint {opts.get('joint')!r} is not a prismatic joint of the asset")
    j = UsdPhysics.PrismaticJoint(jp)
    b0 = stage.GetPrimAtPath(j.GetBody0Rel().GetTargets()[0])
    b1 = stage.GetPrimAtPath(j.GetBody1Rel().GetTargets()[0])
    settled = json.loads((d / "settled_poses.json").read_text())["bodies"] if (d / "settled_poses.json").exists() else {}

    def world_fn(body):
        sp = settled.get(body.GetPath().pathString)
        M = xc.GetLocalToWorldTransform(body)
        if sp:
            return lambda W: _at_settled_pose(np.atleast_2d(W), M, sp)[0]
        return lambda W: np.atleast_2d(W) - drop

    def surface(prim, spacing):
        P = []
        it = iter(Usd.PrimRange(prim))
        for m in it:
            if m != prim and m.HasAPI(UsdPhysics.RigidBodyAPI):
                it.PruneChildren()
                continue
            if m.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m).GetPurposeAttr().Get() != "guide":
                M = xc.GetLocalToWorldTransform(m)
                mesh = UsdGeom.Mesh(m)
                V = np.asarray([list(M.Transform(Gf.Vec3d(*v))) for v in mesh.GetPointsAttr().Get()], float)
                P.append(sample_faces(V, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()), spacing))
        return np.vstack(P) if P else np.zeros((0, 3))

    rng = np.random.default_rng(0)

    def thin(P, k):
        return P[rng.choice(len(P), k, replace=False)] if len(P) > k else P
    button_pts = thin(world_fn(b1)(surface(b1, 0.002)), 4000)
    M0 = xc.GetLocalToWorldTransform(b0)
    lr = j.GetLocalRot0Attr().Get()
    ax = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}[str(j.GetAxisAttr().Get() or "X")]
    lp = j.GetLocalPos0Attr().Get()
    a0 = np.asarray(list(M0.Transform(Gf.Vec3d(lp))), float)
    a1 = np.asarray(list(M0.Transform(Gf.Vec3d(lp) + Gf.Rotation(Gf.Quatd(lr.GetReal(), Gf.Vec3d(lr.GetImaginary()))).TransformDir(ax) * 0.1)), float)
    w = world_fn(b0)(np.vstack([a0, a1]))
    axis = (w[1] - w[0]) / np.linalg.norm(w[1] - w[0])
    lo, hi = float(j.GetLowerLimitAttr().Get()), float(j.GetUpperLimitAttr().Get())
    sign = 1.0 if abs(hi) >= abs(lo) else -1.0
    others = {}
    for b in Usd.PrimRange(obj):
        if b.HasAPI(UsdPhysics.RigidBodyAPI) and b != b1 and b.IsActive():
            P = thin(world_fn(b)(surface(b, float(opts.get("body_sample_m", 0.01)))), int(opts.get("body_points", 15000)))
            if len(P):
                others[b.GetName()] = np.round(P, 4).tolist()
    body_c = np.asarray(others[b0.GetName()], float).mean(0) if b0.GetName() in others else button_pts.mean(0)
    inward = float((body_c - button_pts.mean(0)) @ (axis * sign))
    print(f"[button] {obj.GetName()}: {jp.GetName()} on {b1.GetName()} ({len(button_pts)} points, centre {np.round(button_pts.mean(0), 3).tolist()}), "
          f"press along {np.round(axis * sign, 3).tolist()} {'(into the body)' if inward > 0 else '(OUT of the body - check the joint)'}, "
          f"travel {max(abs(lo), abs(hi)) * 1000:.1f} mm; {len(others)} other bodies", flush=True)
    # the door the button releases, and how far it swings by itself: the hand stays out of it
    swing_ = None
    if opts.get("door_joint"):
        dt_ = _door_open_task(stage, obj, xc, d, {"door_open": {"joint": opts["door_joint"], "handle": "@edge", "edge_m": 0.03,
                                                                 "door_points": 6000}}, drop)
        swing_ = {k: dt_["door"][k] for k in ("door_points", "handle_points", "hinge", "axis")}
        swing_["door_body"] = dt_["door"]["name"]
        swing_["sweep_deg"] = float(opts.get("clear_swing_deg", 25.0))
    return {"kind": "button_press",
            "button": {"name": b1.GetName(), "joint": jp.GetName(), "axis": axis.tolist(), "travel_sign": sign, "travel_m": max(abs(lo), abs(hi)),
                       "button_points": np.round(button_pts, 4).tolist(), "others": others, **({"door_swing": swing_} if swing_ else {})},
            "button_press": dict(opts, object_name=obj.GetName()),
            **{k: gp[k] for k in ("approach_m", "hold_s", "rrt_timeout_s") if k in gp}}


def _door_open_task(stage, obj, xc, d, gp, drop, poses_path=None):
    """A hinged door's parts at their settled poses: the door's hinge (the
    joint's frame on the door body, in the world) and axis, its limits and
    which way it opens, the door's surface, its handle's surface (the named
    prim under the door), and the appliance's other bodies as obstacles."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from tool_grip_planner import sample_faces

    opts = gp.get("door_open", {})
    joints = {q.GetName(): q for q in Usd.PrimRange(obj) if q.IsA(UsdPhysics.Joint)}
    jp = joints.get(opts.get("joint"))
    if jp is None or not jp.IsA(UsdPhysics.RevoluteJoint):
        raise SystemExit(f"{obj.GetName()}: door_open.joint {opts.get('joint')!r} is not a revolute joint of the asset")
    j = UsdPhysics.RevoluteJoint(jp)
    door = stage.GetPrimAtPath(j.GetBody1Rel().GetTargets()[0])
    # the bodies where they rest - or where they are NOW (a replay's live poses: a door popped ajar)
    pp_ = Path(poses_path) if poses_path else d / "settled_poses.json"
    settled = json.loads(pp_.read_text())["bodies"] if pp_.exists() else {}

    def world_fn(body):
        sp = settled.get(body.GetPath().pathString)
        M = xc.GetLocalToWorldTransform(body)
        if sp:
            return lambda W: _at_settled_pose(np.atleast_2d(W), M, sp)[0]
        return lambda W: np.atleast_2d(W) - drop

    def surface(prim, spacing, skip_bodies=True):
        P = []
        it = iter(Usd.PrimRange(prim))
        for m in it:
            if skip_bodies and m != prim and m.HasAPI(UsdPhysics.RigidBodyAPI):
                it.PruneChildren()
                continue
            if m.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m).GetPurposeAttr().Get() != "guide":
                M = xc.GetLocalToWorldTransform(m)
                mesh = UsdGeom.Mesh(m)
                V = np.asarray([list(M.Transform(Gf.Vec3d(*v))) for v in mesh.GetPointsAttr().Get()], float)
                P.append(sample_faces(V, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()), spacing))
        return np.vstack(P) if P else np.zeros((0, 3))

    fd = world_fn(door)
    rng = np.random.default_rng(0)

    def thin(P, k):
        return P[rng.choice(len(P), k, replace=False)] if len(P) > k else P
    # dense meshes (the door's 1.9 M samples at 8 mm): thinned to what the hulls need
    door_pts = thin(fd(surface(door, float(opts.get("door_sample_m", 0.008)))), int(opts.get("door_points", 40000)))
    handle = None
    if opts.get("handle") != "@edge":
        handle = next((q for q in Usd.PrimRange(door) if q.GetName() == opts.get("handle")), None)
        if handle is None:
            raise SystemExit(f"{obj.GetName()}: door_open.handle {opts.get('handle')!r} is not under the door {door.GetName()}")
        handle_pts = thin(fd(surface(handle, float(opts.get("handle_sample_m", 0.002)))), int(opts.get("handle_points", 8000)))
    # the hinge: the joint's frame on the door body, carried to the settled pose
    M1 = xc.GetLocalToWorldTransform(door)
    lp = j.GetLocalPos1Attr().Get(); lr = j.GetLocalRot1Attr().Get()
    ax = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}[str(j.GetAxisAttr().Get() or "Z")]
    ax_b = Gf.Rotation(Gf.Quatd(lr.GetReal(), Gf.Vec3d(lr.GetImaginary()))).TransformDir(ax)
    h0 = np.asarray(list(M1.Transform(Gf.Vec3d(lp))), float)
    h1 = np.asarray(list(M1.Transform(Gf.Vec3d(lp) + ax_b * 0.1)), float)
    w = fd(np.vstack([h0, h1]))
    hinge, axis = w[0], (w[1] - w[0]) / np.linalg.norm(w[1] - w[0])
    if handle is None:
        # no handle: the door's free edge is what the fingers take (a microwave's door, popped
        # ajar by its latch) - the strip of the door within edge_m of its farthest line from the hinge
        rad = np.linalg.norm((door_pts - hinge) - np.outer((door_pts - hinge) @ axis, axis), axis=1)
        handle_pts = door_pts[rad > rad.max() - float(opts.get("edge_m", 0.03))]
    lo, hi = float(j.GetLowerLimitAttr().Get()), float(j.GetUpperLimitAttr().Get())
    # how far the door stands open now: its authored (shut) surface carried by the body it
    # hangs on, against where it is - a door popped ajar is planned to an angle, not a push
    angle_deg = 0.0
    b0t = j.GetBody0Rel().GetTargets()
    if b0t:
        Pa = surface(door, 0.03)
        if len(Pa):
            v0 = world_fn(stage.GetPrimAtPath(b0t[0]))(Pa).mean(0) - hinge
            v1 = fd(Pa).mean(0) - hinge
            v0, v1 = v0 - axis * (v0 @ axis), v1 - axis * (v1 @ axis)
            angle_deg = float(abs(np.degrees(np.arctan2(np.cross(v0, v1) @ axis, v0 @ v1))))
    others = {}
    for b in Usd.PrimRange(obj):
        if b.HasAPI(UsdPhysics.RigidBodyAPI) and b != door and b.IsActive():
            P = thin(world_fn(b)(surface(b, float(opts.get("body_sample_m", 0.02)))), int(opts.get("body_points", 12000)))
            if len(P):
                others[b.GetName()] = np.round(P, 4).tolist()
    print(f"[door] {obj.GetName()}: door {door.GetName()} hinge {np.round(hinge, 3).tolist()} axis {np.round(axis, 3).tolist()} limits [{lo}, {hi}] deg; "
          f"handle {handle.GetName() if handle is not None else 'its free edge'} ({len(handle_pts)} points, z {handle_pts[:, 2].min():.3f}-{handle_pts[:, 2].max():.3f}); "
          f"door {len(door_pts)} points; {len(others)} other bodies; open {angle_deg:.1f} deg now", flush=True)
    return {"kind": "door_open",
            "door": {"name": door.GetName(), "joint": jp.GetName(), "hinge": hinge.tolist(), "axis": axis.tolist(), "angle_deg": round(angle_deg, 2),
                     "lower_deg": lo, "upper_deg": hi, "open_sign": 1.0 if abs(hi) >= abs(lo) else -1.0,
                     "door_points": np.round(door_pts, 4).tolist(), "handle_points": np.round(handle_pts, 4).tolist(), "others": others},
            "door_open": dict(opts, object_name=obj.GetName()),
            **{k: gp[k] for k in ("approach_m", "close_s", "hold_s", "rrt_timeout_s") if k in gp}}


def gprim_points(q, M) -> list[list[float]]:
    """A shape's points in world: a mesh's vertices, a cube's corners, a cylinder's two end rings (16 a
    ring) - generated tools and tablets are cubes and cylinders, and a cloud of meshes alone was empty
    for them (the tweezers, 2026-10-17)."""
    import math
    from pxr import Gf, UsdGeom
    if q.IsA(UsdGeom.Mesh):
        return [list(M.Transform(Gf.Vec3d(*v))) for v in (UsdGeom.Mesh(q).GetPointsAttr().Get() or [])]
    if q.IsA(UsdGeom.Cube):
        h = float(UsdGeom.Cube(q).GetSizeAttr().Get() or 2.0) / 2
        return [list(M.Transform(Gf.Vec3d(x, y, z))) for x in (-h, h) for y in (-h, h) for z in (-h, h)]
    if q.IsA(UsdGeom.Cylinder):
        c = UsdGeom.Cylinder(q)
        r, hh, ax = float(c.GetRadiusAttr().Get()), float(c.GetHeightAttr().Get()) / 2, str(c.GetAxisAttr().Get() or "Z")
        out = []
        for z in (-hh, hh):
            for k in range(16):
                a, b = r * math.cos(2 * math.pi * k / 16), r * math.sin(2 * math.pi * k / 16)
                v = {"X": (z, a, b), "Y": (a, z, b), "Z": (a, b, z)}[ax]
                out.append(list(M.Transform(Gf.Vec3d(*v))))
        return out
    return []


def tool_grip_frame(meta: dict, grip_from_root_m: float):
    """A generated tweezers' grip in its own frame (make_pill_kit.py's er:generated): the point on the
    midline grip_from_root_m from the root's far end, the opening axis (+z) and the arms' outer width there."""
    import math
    import numpy as np
    spr = meta["spring"]
    th = math.radians(spr["rest_angle_deg"])
    thick = 0.001 if "thick_m" not in meta else float(meta["thick_m"])
    hinge_off = thick / 2 + 0.0002
    root = float(meta.get("root_m", 0.015))
    s_ = float(grip_from_root_m)
    outer = 2 * (hinge_off + s_ * math.sin(th)) + thick
    sl = meta.get("grip_sleeve")
    if sl and sl["from_m"] <= s_ <= sl["to_m"]:
        outer += 2 * float(sl["thick_m"])                    # over the cushion grips
    return np.array([root + s_ * math.cos(th), 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), outer


def _tool_pick_task(stage, tool, xc, gp, drop) -> dict:
    """A slim tool (generated tweezers) taken in a precision pinch across its arms at a point along them:
    a pinch_lift whose closing axis is held to the tool's opening axis (the planner's closing-axis rule)."""
    import numpy as np
    from pxr import Gf, Usd
    root = next((q for q in Usd.PrimRange(tool) if q.HasCustomDataKey("er:generated")), None)
    if root is None:
        raise SystemExit("tool_pick: the target is not a generated tool (no er:generated)")
    meta = json.loads(root.GetCustomDataByKey("er:generated"))
    p_loc, ax_loc, width = tool_grip_frame(meta, float(gp.get("grip_from_root_m", 0.055)))
    M = xc.GetLocalToWorldTransform(root)
    grip = np.asarray(M.Transform(Gf.Vec3d(*p_loc)), float) - drop
    axis = np.asarray(M.TransformDir(Gf.Vec3d(*ax_loc)), float)
    axis /= np.linalg.norm(axis)
    along = np.asarray(M.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)), float)       # the arms' length, root to tips
    along /= np.linalg.norm(along)
    return {"kind": "pinch_lift", "grip_point": grip.tolist(), "grip_width_m": float(width),
            "cloth_normal": axis.tolist(), "closing_axis_tolerance_deg": float(gp.get("closing_axis_tolerance_deg", 10.0)),
            "object_base_z": float(grip[2]) - 0.0045, "object_height_m": 0.009, "place_point": grip.tolist(),
            "lift_mode": gp.get("lift_mode", "palm"), "tripod": bool(gp.get("tripod", True)),
            "finger_row_axis": along.tolist() if gp.get("tripod", True) else None,
            "finger_row_tolerance_deg": float(gp.get("finger_row_tolerance_deg", 25.0)),
            "tool": {"asset_id": meta.get("asset_id"), "grip_from_root_m": float(gp.get("grip_from_root_m", 0.055)),
                                                               "opening_axis": axis.tolist()},
            **{k: gp[k] for k in ("lift_m", "close_s", "hold_s", "pregrasp_height_m", "open_clearance_m", "curl_rad",
                                  "reach", "cumotion_seeds", "staging_back_m", "rrt_timeout_s") if k in gp}}


def fixture_obstacles(stage, bc) -> list[dict]:
    """The scene's own fixtures (/World/Fixtures: a cradle, a rail, a board), each collider its own box: a plan
    that passes through one collides in PhysX (the tweezers' cradle was not in the plan, 2026-10-17)."""
    from pxr import Usd, UsdPhysics
    root = stage.GetPrimAtPath("/World/Fixtures")
    out = []
    if not root:
        return out
    for q in Usd.PrimRange(root):
        if q.HasAPI(UsdPhysics.CollisionAPI):
            r = bc.ComputeWorldBound(q).ComputeAlignedRange()
            out.append(dict(id=f"fixture:{q.GetParent().GetName()}:{q.GetName()}", min=[float(v) for v in r.GetMin()],
                            max=[float(v) for v in r.GetMax()]))
    return out


def station_obstacles(stage, bc) -> list[dict]:
    """Every station of a generated environment (make_lab_env.py's er:station
    prims: benches, a bin, a desk), each collider part its own box - its parts
    are boxes, so exact. The support alone (one bench's top) left the arm
    routed through the other bench and its own legs (the lab, 2026-10-17)."""
    from pxr import Usd, UsdPhysics
    out = []
    env = stage.GetPrimAtPath("/World/Environment")
    if not env:
        return out
    for q in Usd.PrimRange(env):
        if not q.HasCustomDataKey("er:station"):
            continue
        for part in Usd.PrimRange(q):
            if part != q and part.HasAPI(UsdPhysics.CollisionAPI):
                r = bc.ComputeWorldBound(part).ComputeAlignedRange()
                out.append(dict(id=f"{q.GetName()}:{part.GetName()}", min=[float(v) for v in r.GetMin()], max=[float(v) for v in r.GetMax()]))
    return out


def slice_width(points, axis_xy, z: float) -> float:
    """An object's width at height z across its axis: twice the farthest point of
    the slice of its points' convex hull at z (each hull edge that crosses z,
    interpolated). The points within 3 mm of z read a low-poly bottle - a
    cylinder with vertices only at its ends - as empty (2026-10-17)."""
    import numpy as np
    from scipy.spatial import ConvexHull
    P = np.asarray(points, float)
    hull = ConvexHull(P)
    cut = []
    for simplex in hull.simplices:
        for i in range(3):
            a, b = P[simplex[i]], P[simplex[(i + 1) % 3]]
            if (a[2] - z) * (b[2] - z) <= 0 and a[2] != b[2]:
                cut.append(a + (b - a) * (z - a[2]) / (b[2] - a[2]))
            elif a[2] == z:
                cut.append(a)
    if not cut:
        raise SystemExit(f"slice_width: nothing of the object at height {z:.4f}")
    C = np.asarray(cut)
    return float(2 * np.linalg.norm(C[:, :2] - np.asarray(axis_xy, float), axis=1).max())


def then_request(base: dict, hold_row, task: dict, out_npz) -> dict:
    """A stage after the first, as a request: the scene's first request with
    this stage's task, starting where the stage before left the arm, and its
    IK seeded by the first stage's seeds (HomeHero's reset arm) and the arm the
    scene was authored in as well as that start. The stage before leaves the
    arm in a posture of its own (a press reaching across with the wrist
    rolled); seeds scattered only about it land in its basin (the drill's
    switch, 2026-10-17: after the press, 8 grip candidates, none with IK clear
    of the torso; with these seeds, 1492 ticks)."""
    seeds = list(task.get("ik_seeds") or []) + list(base.get("task", {}).get("ik_seeds") or [])
    if base.get("measured") is not None:
        seeds.append([float(v) for v in base["measured"]])
    req = dict(base, task=dict(task, ik_seeds=seeds), measured=[float(v) for v in hold_row[:14]], out_npz=str(out_npz))
    req.pop("ik_seeds", None)
    return req


def main() -> int:
    import numpy as np
    from pxr import Gf, Usd, UsdGeom

    from build_er_scene import build, load_spec

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--max-points", type=int, default=1500)
    args = ap.parse_args()
    spec = load_spec(args.spec)
    d = REPO / "workspace" / "scenes" / spec["name"]
    from build_er_scene import foreign_reference
    _foreign = foreign_reference(spec, d)
    if _foreign:
        raise SystemExit(f"{spec['name']}: {_foreign}")
    build(spec, d)
    (d / "spec.json").write_text(json.dumps(spec, indent=1))
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    xc = UsdGeom.XformCache()

    rb = spec["robot"]
    yaw = math.radians(float(rb.get("cad_yaw_deg", 0.0)))
    base_pose = [*rb["position"], 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]
    post = rb["posture_rad_or_m"]
    hand = [post.get(f"psyonic_left_{f}_q1", 0.0) for f in ("index", "middle", "ring", "pinky")]
    measured = [float(post.get(n, 0.0)) for n in ARM] + hand + [float(post.get("psyonic_left_thumb_q2", 0.0)),
                                                                float(post.get("psyonic_left_thumb_q1", -1.4702284649855213))]

    from pxr import UsdPhysics
    objs = stage.GetPrimAtPath("/World/Objects").GetChildren()
    # the target: an object, or a member of one (a chess set's piece)
    target = next(p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects")) if p.GetCustomDataByKey("er:target"))
    pts = []
    settled = d / "cloth_settled.npz"
    target_obj = target
    while target_obj.GetParent().GetPath().pathString != "/World/Objects":
        target_obj = target_obj.GetParent()
    cloth = settled.exists() and target_obj.GetName() in np.load(settled).files
    if cloth:
        # a cloth: where it came to rest in PhysX (settle_cloth.py), not the flat sheet it was placed as
        pts = np.load(settled)[target_obj.GetName()].tolist()
    for q in ([] if cloth else Usd.PrimRange(target)):
        if q.GetName() != "simMesh":
            pts += gprim_points(q, xc.GetLocalToWorldTransform(q))
    # the build sets every object down 2 mm above the support and the replay
    # settles it first, so the planned geometry is 2 mm high (measured: the
    # pinch skimmed the pawn's crown); planned against the settled pose
    DROP = np.array([0.0, 0.0, 0.002])
    pts = [list(np.asarray(v, float) - (0 if cloth else DROP)) for v in pts]
    cloud = np.unique(np.round(np.asarray(pts), 5), axis=0)
    if len(cloud) > args.max_points:
        cloud = cloud[np.random.default_rng(0).choice(len(cloud), args.max_points, replace=False)]
    tr = bc.ComputeWorldBound(target).ComputeAlignedRange()
    tr = Gf.Range3d(tr.GetMin() - Gf.Vec3d(0, 0, 0.002), tr.GetMax() - Gf.Vec3d(0, 0, 0.002))

    env = spec["environment"]
    src_root = env.get("prim", "")
    # the support: a prim of the environment (under /World/Environment) or one of the scene's own fixtures
    sup_path = env["support"]
    if src_root and sup_path.startswith(src_root):
        sup_path = "/World/Environment" + sup_path[len(src_root):]
    sup = stage.GetPrimAtPath(sup_path)
    if not sup:
        raise SystemExit(f"support prim {sup_path} is not in the scene")
    srng = bc.ComputeWorldBound(sup).ComputeAlignedRange()
    if gp_support_split := (spec.get("grasp_plan") or {}).get("support_parts") == "split":
        # the slab and each leg panel as their own boxes (a pinch under the
        # slab's edge is free space the one box to the floor would forbid)
        from support_geometry import mesh_samples, support_boxes
        obstacles = support_boxes(mesh_samples(sup, float(srng.GetMax()[2])), float(srng.GetMax()[2]))
    else:
        obstacles = [dict(id="table", min=list(srng.GetMin()), max=list(srng.GetMax()))]
    for op in env.get("obstacle_prims", []):
        # more of the room the arm must clear (the table under a stand that is the support)
        opath = "/World/Environment" + op[len(src_root):] if src_root and op.startswith(src_root) else op
        oprim = stage.GetPrimAtPath(opath)
        if not oprim:
            raise SystemExit(f"obstacle prim {opath} is not in the scene")
        from support_geometry import mesh_samples, support_boxes
        otop = float(bc.ComputeWorldBound(oprim).ComputeAlignedRange().GetMax()[2])
        obstacles += [dict(b, id=f"{oprim.GetName()}:{b['id']}") for b in support_boxes(mesh_samples(oprim, otop), otop)]
    obstacles += station_obstacles(stage, bc)
    obstacles += fixture_obstacles(stage, bc)
    tpath = target.GetPath().pathString
    obstacles_all = []
    for p in objs:
        if tpath.startswith(p.GetPath().pathString + "/"):
            for q in Usd.PrimRange(p):
                if q.HasAPI(UsdPhysics.RigidBodyAPI):
                    obstacles_all += _slices(q, xc, bc, f"{p.GetName()}:{q.GetName()}")
    for p in objs:
        if p == target:
            continue
        if tpath.startswith(p.GetPath().pathString + "/"):
            # the set the target belongs to: each of its other bodies (a
            # board, the other pieces) is an obstacle of its own
            # sliced near the target, where a fingertip passes between pieces;
            # one box each further off (every box costs each collision check,
            # and HomeHero's RRT ran out of budget with all of them sliced)
            tc_ = np.asarray(bc.ComputeWorldBound(target).ComputeAlignedRange().GetMidpoint(), float)
            settled = json.loads((d / "settled_poses.json").read_text())["bodies"] if (d / "settled_poses.json").exists() else {}
            for q in Usd.PrimRange(p):
                if q.HasAPI(UsdPhysics.RigidBodyAPI) and q != target:
                    qr = bc.ComputeWorldBound(q).ComputeAlignedRange()
                    sp = settled.get(q.GetPath().pathString)
                    if sp:
                        # the body where it came to rest (a scissors' free half swung on the pivot): its mesh points at the settled pose
                        lp = [list(xc.GetLocalToWorldTransform(m_).Transform(Gf.Vec3d(*v))) for m_ in Usd.PrimRange(q)
                              if m_.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m_).GetPurposeAttr().Get() != "guide" for v in UsdGeom.Mesh(m_).GetPointsAttr().Get()]
                        if lp:
                            Ws, _ = _at_settled_pose(np.asarray(lp, float), xc.GetLocalToWorldTransform(q), sp)
                            qr = Gf.Range3d(Gf.Vec3d(*Ws.min(0)), Gf.Vec3d(*Ws.max(0)))
                    near = np.linalg.norm(np.asarray(qr.GetMidpoint(), float)[:2] - tc_[:2]) < float(spec.get("grasp_plan", {}).get("slice_radius_m", 0.12))
                    boxes_ = _slices(q, xc, bc, f"{p.GetName()}:{q.GetName()}") if (near and not sp) else \
                        [dict(id=f"{p.GetName()}:{q.GetName()}", min=list(qr.GetMin()), max=list(qr.GetMax()))]
                    for b_ in boxes_:
                        b_["min"], b_["max"] = list(np.asarray(b_["min"]) - DROP), list(np.asarray(b_["max"]) - DROP)
                    obstacles += boxes_
            continue
        r = bc.ComputeWorldBound(p).ComputeAlignedRange()
        obstacles.append(dict(id=p.GetName(), min=list(np.asarray(r.GetMin()) - DROP), max=list(np.asarray(r.GetMax()) - DROP)))

    robot_json = json.loads((EXPERIMENT / "robot.json").read_text())
    # HomeHero's planner keeps the gravity-compensated slide target (reference
    # + gravity force / stiffness) inside the slide's limits, so it needs the
    # slide's offset. The slide is vertical: its load is every link it carries,
    # whatever the arm's pose - summed from the built USD (7.616 kg, 7.47 mm;
    # PhysX measured 7.5 mm; a URDF-mass estimate gave 0.5 mm and the target
    # clamped at the slide's stop).
    from pxr import UsdPhysics
    slide_name = robot_json["arm_names"][0]
    children, slide_child, slide_k = {}, None, None
    for q in Usd.PrimRange(stage.GetPrimAtPath("/World/Robot")):
        if not q.IsA(UsdPhysics.Joint):
            continue
        j = UsdPhysics.Joint(q)
        b0, b1 = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
        if b0 and b1:
            children.setdefault(str(b0[0]), []).append(str(b1[0]))
        if q.GetName() == slide_name:
            slide_child = str(b1[0])
            slide_k = float(q.GetAttribute("drive:linear:physics:stiffness").Get())
    carried, stack, seen = 0.0, [slide_child], set()
    while stack:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        m = stage.GetPrimAtPath(b).GetAttribute("physics:mass")
        if not (m and m.Get()):
            raise SystemExit(f"{b} has no authored mass; the slide's gravity offset needs every carried link's")
        carried += float(m.Get())
        stack += children.get(b, [])
    slide_offset = carried * 9.81 / slide_k
    gp = spec.get("grasp_plan", {})
    via = None
    if gp.get("via") == "homehero_reset":
        # HomeHero's reset pose (robot.json reset_arm/reset_hand), the shoulder
        # lift raised by this support's extra height over the recording's table
        protocol = json.loads((EXPERIMENT / "protocol.json").read_text())
        via = list(robot_json["reset_arm"]) + list(robot_json["reset_hand"])
        via[0] += float(srng.GetMax()[2]) - float(protocol["table"]["top"])
    task = None
    if gp.get("task") == "pinch_lift":
        # a hanging corner: of a cloth's flap (cloth_grip) or of a rigid
        # member hung from a peg (member_grip, a scissors' free half). The
        # grip point: in the lowest band, inset from the band's far end along
        # `side`, up from the bottom; the pinch's width is the part's thickness
        allpts = np.asarray(pts)
        if (gp.get("member_grip") and not cloth) and (d / "settled_poses.json").exists():
            # a rigid member at its SETTLED pose (settle_scene.py, or the replay's
            # live dump): hung on a peg it swings on its joints after authoring
            body = next((q for q in Usd.PrimRange(target) if q.HasAPI(UsdPhysics.RigidBodyAPI)), target)
            sp = json.loads((d / "settled_poses.json").read_text())["bodies"].get(body.GetPath().pathString)
            if sp:
                allpts, moved = _at_settled_pose(allpts, xc.GetLocalToWorldTransform(body), sp)
                print(f"[member] {body.GetName()} at its settled pose: moved {1000 * moved:.1f} mm from the authored", flush=True)
        cg = gp.get("cloth_grip") or gp.get("member_grip") or {}
        band_m = float(cg.get("hang_band_m", cg.get("band_m", 0.03)))
        if cg.get("band", "lowest") == "highest":
            low = allpts[allpts[:, 2] > allpts[:, 2].max() - band_m]                              # the top band (a loop's rim, handles up)
        else:
            low = allpts[allpts[:, 2] < allpts[:, 2].min() + band_m]                              # the lower band (a hem, a hanging corner)
        side = cg.get("corner_side", cg.get("side", "+x"))                                       # which end of the band, or its centre
        if side == "centre":
            corner = low[np.argmax(low[:, 2])].copy() if cg.get("band") == "highest" else low[np.argmin(low[:, 2])].copy()
            corner[:2] = low[:, :2].mean(0)
            axis, sgn = 0, 0.0
        else:
            axis, sgn = (0, 1.0) if side == "+x" else (0, -1.0) if side == "-x" else (1, 1.0) if side == "+y" else (1, -1.0)
            corner = low[np.argmax(sgn * low[:, axis])]
        grip = corner.copy()
        grip[axis] -= sgn * float(cg.get("inset_m", 0.02))
        grip[2] += float(cg.get("up_m", 0.02)) * (-1.0 if cg.get("band") == "highest" else 1.0)   # down from a top rim, up from a hem
        # on a cloth VERTEX: PhysX makes cloth-rigid contacts at the cloth's
        # vertices, and the pads are narrower than the mesh's cells (1 cm vs
        # 2 cm). The rule's point sat between four vertices; the pads closed
        # on a triangle's interior and the fabric slid out of a held pinch in
        # the draw-out (nearest vertex 7-11 mm from the pinch, 2026-10-15)
        snapped = allpts[np.argmin(np.linalg.norm(allpts - grip, axis=1))]
        print(f"[cloth] grip {grip.round(4).tolist()} snapped to the nearest vertex {snapped.round(4).tolist()} "
              f"({1000 * np.linalg.norm(snapped - grip):.1f} mm)", flush=True)
        grip = snapped.copy()
        # the cloth's normal at the grip (a plane fit to the points within 5
        # cm): a sheet is pinched ACROSS it, the closing axis along the normal.
        # Ranked by cost alone the planner chose a closing axis in the flap's
        # plane and the tips met edge-on with nothing between them (2026-10-15)
        near = allpts[np.linalg.norm(allpts - grip, axis=1) < 0.05]
        _, _, vt = np.linalg.svd(near - near.mean(0))
        cloth_normal = vt[-1] / np.linalg.norm(vt[-1])
        print(f"[cloth] normal at the grip {cloth_normal.round(3).tolist()} from {len(near)} points", flush=True)
        # the object the pinch closes on is the cloth's local patch, densely
        # sampled over its grid cells: closure_contact sweeps the digits
        # against the cloud's CONVEX HULL, and the hull of a whole draped
        # towel is a 13 x 33 x 25 cm block (the thumb never "touched" it)
        cells = None
        try:
            cells = json.loads(target_obj.GetCustomDataByKey("simReady:cloth") or "{}").get("cells") if cloth else None
        except Exception:
            cells = None
        patch = []
        if cells and (cells[0] + 1) * (cells[1] + 1) == len(allpts):
            nx, ny = int(cells[0]), int(cells[1])
            G = allpts.reshape(ny + 1, nx + 1, 3)                     # row-major as make_towel.py writes the grid
            for j in range(ny):
                for i in range(nx):
                    q00, q10, q01, q11 = G[j, i], G[j, i + 1], G[j + 1, i], G[j + 1, i + 1]
                    if min(np.linalg.norm(q - grip) for q in (q00, q10, q01, q11)) > 0.06:
                        continue
                    for u in np.linspace(0, 1, 5):
                        for v in np.linspace(0, 1, 5):
                            patch.append((1 - u) * (1 - v) * q00 + u * (1 - v) * q10 + (1 - u) * v * q01 + u * v * q11)
        patch = np.asarray(patch) if patch else near
        patch = patch[np.linalg.norm(patch - grip, axis=1) < 0.05]
        cloud = np.unique(np.round(patch, 5), axis=0)
        print(f"[cloth] the pinch's object: the patch within 5 cm of the grip, {len(cloud)} points", flush=True)
        # the rest of the cloth is an obstacle: where it lies on the table the
        # curled fingers pressed it onto the slab and the arm stalled (measured:
        # no rigid contact reported, the slide 9 cm short). Every settled point
        # farther than free_radius_m from the grip becomes a 2 cm box
        free_r = float(cg.get("free_radius_m", 0.06))
        cell = float(cg.get("obstacle_cell_m", 0.02))
        cells = {}
        for q_ in allpts:
            if np.linalg.norm(q_ - grip) < free_r:
                continue
            key = tuple(np.floor(q_ / cell).astype(int))
            cells.setdefault(key, []).append(q_)
        for k_, members in cells.items():
            m_ = np.asarray(members)
            obstacles.append(dict(id=f"cloth:{k_[0]}_{k_[1]}_{k_[2]}", min=(m_.min(0) - 0.004).tolist(), max=(m_.max(0) + 0.004).tolist()))
        task = {"kind": "pinch_lift", "grip_point": grip.tolist(), "grip_width_m": float(cg.get("thickness_m", 0.002)),
                "cloth_normal": cloth_normal.tolist(), "closing_axis_tolerance_deg": float(cg.get("closing_axis_tolerance_deg", 30.0)),
                "cloth_obstacle_boxes": len(cells),
                "object_base_z": float(allpts[:, 2].min()), "object_height_m": float(np.ptp(allpts[:, 2])),
                "place_point": grip.tolist(), "lift_mode": gp.get("lift_mode", "palm"), "corner": corner.tolist(),
                **{k: gp[k] for k in ("lift_m", "lift_out_xy", "after_lift_xy", "close_s", "hold_s", "pregrasp_height_m", "open_clearance_m", "curl_rad",
                                      "reach", "cumotion_seeds", "staging_back_m", "rrt_timeout_s") if k in gp}}
    if gp.get("task") == "tool_pick":
        task = _tool_pick_task(stage, target_obj, xc, gp, DROP)
        # the tool is the reach's to clear but where the fingers take it: its points in 1 cm cells,
        # none within free_r of the grip; dropped once it is in the hand (the pinch planner drops held:)
        # - the reach swept the thumb through the tweezers at 114 N and flung them out of the cradle (2026-10-17)
        free_r, cell = float(gp.get("grip_free_radius_m", 0.02)), 0.01
        g_ = np.asarray(task["grip_point"], float)
        cells = {}
        for q_ in np.asarray(pts, float) - DROP:
            if np.linalg.norm(q_ - g_) < free_r:
                continue
            cells.setdefault(tuple(np.floor(q_ / cell).astype(int)), []).append(q_)
        for k_, members in cells.items():
            m_ = np.asarray(members)
            obstacles.append(dict(id=f"held:{k_[0]}_{k_[1]}_{k_[2]}", min=(m_.min(0) - 0.003).tolist(), max=(m_.max(0) + 0.003).tolist()))
        task["held_obstacle_boxes"] = len(cells)
    if gp.get("task") == "tool_grip":
        task = _tool_grip_task(stage, target_obj, xc, d, gp, DROP)
        # the index's strength: a finger's stall torque at the hand's collision
        # voltage (the firmware halts it there), from the hand's calibration
        hm = (spec.get("grasp_replay") or {}).get("hand_model") or {}
        if hm.get("kind") == "ability_hand":
            cal = json.loads((REPO / hm.get("calibration", "config/ability_hand_calibration.json")).read_text())
            v = float(hm.get("collision_voltage", 3.0))
            task["tool"]["finger_torque_nm"] = float(cal["stall_torque_per_volt_nm"]["finger"]) * min(v, float(cal.get("supply_v", 8.4)))
    if gp.get("task") == "loop_grip":
        task = _loop_grip_task(stage, target_obj, xc, d, gp, DROP)
    if gp.get("task") == "door_open":
        task = _door_open_task(stage, target_obj, xc, d, gp, DROP)
    if gp.get("task") == "button_press":
        task = _button_press_task(stage, target_obj, xc, d, gp, DROP)
    if gp.get("task") == "pinch_place":
        # a pinch at a height up the piece, across its width there; set down
        # on the destination with the grip point at the same height above it
        allpts = np.asarray(pts)
        base_z, height = float(allpts[:, 2].min()), float(np.ptp(allpts[:, 2]))
        axis_xy = (allpts[:, :2].max(0) + allpts[:, :2].min(0)) / 2
        gz = base_z + float(gp.get("grip_height_fraction", 0.85)) * height
        width = slice_width(allpts, axis_xy, gz)
        dest = json.loads(stage.GetPrimAtPath("/World").GetCustomDataByKey("er:destination"))
        dc = list(np.asarray(dest["centre_world_at_build"], float) - DROP)
        task = {"kind": "pinch_place", "grip_point": [float(axis_xy[0]), float(axis_xy[1]), gz],
                "grip_width_m": width, "object_base_z": base_z, "object_height_m": height,
                "place_point": [float(dc[0]), float(dc[1]), float(dc[2]) + (gz - base_z)],
                "destination": dest["label"],
                **{k: gp[k] for k in ("lift_m", "close_s", "release_s", "retreat_m", "hold_s", "pregrasp_height_m",
                                      "open_clearance_m", "curl_rad", "set_down_gap_m", "reach", "cumotion_seeds",
                                      "staging_back_m", "rrt_timeout_s") if k in gp}}
    if task:
        # HomeHero's reset arm (the lift raised for this table) as an IK starting point
        task["ik_seeds"] = [[v + (float(srng.GetMax()[2]) - float(json.loads((EXPERIMENT / "protocol.json").read_text())["table"]["top"]))
                             * (i == 0) for i, v in enumerate(robot_json["reset_arm"])]]
    if task and gp.get("survey"):
        task["survey"] = _chess_survey(stage, target, xc, bc, json.loads(stage.GetPrimAtPath("/World").GetCustomDataByKey(
            "er:destination")), float(gp.get("grip_height_fraction", 0.85)))
    out_dir = d / "grasp_plan"
    out_dir.mkdir(parents=True, exist_ok=True)
    req = {"experiment": str(EXPERIMENT), "config": str(CONFIG),
           "robot_usd_sha256": robot_json["physics"]["usd_sha256"],
           "base_pose": base_pose, "measured": measured, "joint_positions": {k: float(v) for k, v in post.items()},
           "cloud": cloud.tolist(), "object_id": target.GetCustomDataByKey("er:asset_id"),
           "object_pose": [*tr.GetMidpoint(), 0.0, 0.0, 0.0, 1.0],
           "table_top": float(srng.GetMax()[2]), "obstacles": obstacles,
           "target_box": dict(id=target.GetName(), min=list(tr.GetMin()), max=list(tr.GetMax())),
           "preload_rad": gp.get("preload_rad", {"fingers": 0.08, "thumb_flex": 0.15}),
           "slide_gravity_offset": {"m": slide_offset, "carried_kg": round(carried, 3), "links": len(seen),
                                    "stiffness_n_per_m": slide_k},
           "close": gp.get("close"), "task": task, "lift_m": gp.get("lift_m", 0.08), "obstacles_all": obstacles_all, "hold_s": gp.get("hold_s", 1.5), "via": via, "hover_clearance_m": gp.get("hover_clearance_m"),
           "out_npz": str(out_dir / "targets.npz")}
    (out_dir / "request.json").write_text(json.dumps(req))
    r = subprocess.run([str(HH_PY), str(REPO / "scripts" / "homehero_grasp_adapter.py")],
                       input=json.dumps(req), capture_output=True, text=True, cwd=HH)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit("the planner adapter failed")
    plan = json.loads(r.stdout.strip().splitlines()[-1])
    if (spec.get("grasp_replay") or {}).get("bimanual") and plan.get("passed"):
        # a two-arm task: the pick's plan carries the right arm and hand at
        # rest in columns 14:28 (the replay drives both arms from one row)
        z = np.load(req["out_npz"])
        arr = np.asarray(z["targets"], float)
        if arr.shape[1] < 28:
            posture = spec["robot"].get("posture_rad_or_m", {})
            right_arm = [float(posture.get(n.replace("left", "right"), 0.0)) for n in robot_json["arm_names"]]
            right_hand = [float(v) for v in robot_json["reset_hand"]]
            pad = np.tile(np.r_[right_arm, right_hand], (len(arr), 1))
            arr = np.hstack([arr, pad[:, :28 - arr.shape[1]]])
            np.savez(req["out_npz"], targets=arr, stages=z["stages"])
            plan["columns"] = int(arr.shape[1])
            print(f"[bimanual] the plan padded to {arr.shape[1]} columns: the right arm and hand at rest", flush=True)
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=1))
    if plan.get("survey"):
        print(f"GRASP_SURVEY {spec['name']}:")
        for row in plan["survey"]:
            print(f"  {row['piece']} {row['square']}: {row['grasps']} pinch grasps; to " +
                  ", ".join(f"{k} {v}" for k, v in row["destinations"].items()))
        return 0
    if not plan["passed"]:
        print(f"GRASP_PLAN FAIL {spec['name']}: {plan.get('reason')}; trials: "
              + "; ".join(str(t.get("failure"))[:120] for t in (plan.get("trials") or [])[:6]))
        return 1
    if task and task["kind"] == "loop_grip":
        print(f"GRASP_PLAN OK {spec['name']}: loop_grip, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
              f"grip {plan.get('grip')}, holes {plan.get('holes')}")
        return 0
    if task and task["kind"] == "button_press":
        gr_ = plan.get("grip", {})
        print(f"GRASP_PLAN OK {spec['name']}: button_press, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
              f"the index on the button rolled {gr_.get('roll_deg')} deg (tilts {gr_.get('tilt_deg')}), pressed {gr_.get('press_m')} m along {gr_.get('press_dir')}, "
              f"{plan.get('candidates')} candidates")
        return 0
    if task and task["kind"] == "door_open":
        gr_ = plan.get("grip", {})
        print(f"GRASP_PLAN OK {spec['name']}: door_open, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
              f"the bar gripped with {sorted((gr_.get('wrap') or {}).items())} (x {gr_.get('x_sign')}, yaw {gr_.get('yaw_deg')}, "
              f"{gr_.get('height_m')} m up the bar, {gr_.get('depth_m')} m along the fingers), the door pulled to {plan.get('pull_deg')} deg, "
              f"{plan.get('candidates')} candidates")
        return 0
    if task and task["kind"] == "tool_grip":
        gr_ = plan.get("grip", {})
        print(f"GRASP_PLAN OK {spec['name']}: tool_grip, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
              f"grip yaw {gr_.get('yaw_deg')} roll {gr_.get('roll_deg')}, wrap {gr_.get('wrap')}, index meets the trigger at {gr_.get('index_trigger_rad')} rad "
              f"(held at {plan.get('index_pre_rad')}), thumb rot {gr_.get('thumb_rot')} contact {gr_.get('thumb_contact_rad')}, "
              f"trigger travel where the index stops {gr_.get('travel_at_stop_m')} m (at {gr_.get('index_stop_rad')} rad), {plan.get('candidates')} candidates")
        return 0
    if task:
        print(f"GRASP_PLAN OK {spec['name']}: {task['kind']}, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), "
              f"stages {plan['stage_ticks']}, grasp {plan.get('grasp')}, pinch closed {plan['pinch']['closed']} "
              f"open {plan['pinch']['open']} (opposition {plan['pinch']['opposition']:.2f}), {plan.get('options')} options")
        return 0
    print(f"GRASP_PLAN OK {spec['name']}: {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
          f"closure {plan['closure_targets']}, opposed fingers predicted "
          f"{(plan['candidate'].get('contact_prediction') or {}).get('opposed_fingers')}, planned in {plan['seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
