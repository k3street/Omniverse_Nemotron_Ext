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
    for q in Usd.PrimRange(target):
        if q.IsA(UsdGeom.Mesh):
            M = xc.GetLocalToWorldTransform(q)
            pts += [list(M.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(q).GetPointsAttr().Get()]
    cloud = np.unique(np.round(np.asarray(pts), 5), axis=0)
    if len(cloud) > args.max_points:
        cloud = cloud[np.random.default_rng(0).choice(len(cloud), args.max_points, replace=False)]
    tr = bc.ComputeWorldBound(target).ComputeAlignedRange()

    env = spec["environment"]
    src_root = env.get("prim", "")
    sup = stage.GetPrimAtPath("/World/Environment" + env["support"][len(src_root):])
    srng = bc.ComputeWorldBound(sup).ComputeAlignedRange()
    obstacles = [dict(id="table", min=list(srng.GetMin()), max=list(srng.GetMax()))]
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
            for q in Usd.PrimRange(p):
                if q.HasAPI(UsdPhysics.RigidBodyAPI) and q != target:
                    qr = bc.ComputeWorldBound(q).ComputeAlignedRange()
                    near = np.linalg.norm(np.asarray(qr.GetMidpoint(), float)[:2] - tc_[:2]) < float(spec.get("grasp_plan", {}).get("slice_radius_m", 0.12))
                    obstacles += _slices(q, xc, bc, f"{p.GetName()}:{q.GetName()}") if near else \
                        [dict(id=f"{p.GetName()}:{q.GetName()}", min=list(qr.GetMin()), max=list(qr.GetMax()))]
            continue
        r = bc.ComputeWorldBound(p).ComputeAlignedRange()
        obstacles.append(dict(id=p.GetName(), min=list(r.GetMin()), max=list(r.GetMax())))

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
    if gp.get("task") == "pinch_place":
        # a pinch at a height up the piece, across its width there; set down
        # on the destination with the grip point at the same height above it
        allpts = np.asarray(pts)
        base_z, height = float(allpts[:, 2].min()), float(np.ptp(allpts[:, 2]))
        axis_xy = (allpts[:, :2].max(0) + allpts[:, :2].min(0)) / 2
        gz = base_z + float(gp.get("grip_height_fraction", 0.85)) * height
        band = allpts[np.abs(allpts[:, 2] - gz) < 0.003]
        width = float(2 * np.linalg.norm(band[:, :2] - axis_xy, axis=1).max())
        dest = json.loads(stage.GetPrimAtPath("/World").GetCustomDataByKey("er:destination"))
        dc = dest["centre_world_at_build"]
        task = {"kind": "pinch_place", "grip_point": [float(axis_xy[0]), float(axis_xy[1]), gz],
                "grip_width_m": width, "object_base_z": base_z, "object_height_m": height,
                "place_point": [float(dc[0]), float(dc[1]), float(dc[2]) + (gz - base_z)],
                "destination": dest["label"],
                # HomeHero's reset arm (the lift raised for this table) as an IK starting point
                "ik_seeds": [[v + (float(srng.GetMax()[2]) - float(json.loads((EXPERIMENT / "protocol.json").read_text())["table"]["top"]))
                              * (i == 0) for i, v in enumerate(robot_json["reset_arm"])]],
                **{k: gp[k] for k in ("lift_m", "close_s", "release_s", "retreat_m", "hold_s", "pregrasp_height_m",
                                      "open_clearance_m", "curl_rad", "set_down_gap_m", "reach", "cumotion_seeds",
                                      "staging_back_m", "rrt_timeout_s") if k in gp}}
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
    if task:
        print(f"GRASP_PLAN OK {spec['name']}: pinch-place, {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), "
              f"stages {plan['stage_ticks']}, grasp {plan.get('grasp')}, pinch closed {plan['pinch']['closed']} "
              f"open {plan['pinch']['open']} (opposition {plan['pinch']['opposition']:.2f}), {plan.get('options')} options")
        return 0
    print(f"GRASP_PLAN OK {spec['name']}: {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
          f"closure {plan['closure_targets']}, opposed fingers predicted "
          f"{(plan['candidate'].get('contact_prediction') or {}).get('opposed_fingers')}, planned in {plan['seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
