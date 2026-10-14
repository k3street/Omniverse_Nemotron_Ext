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

    objs = stage.GetPrimAtPath("/World/Objects").GetChildren()
    target = next(p for p in objs if p.GetCustomDataByKey("er:target"))
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
    for p in objs:
        if p == target:
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
           "lift_m": gp.get("lift_m", 0.08), "hold_s": gp.get("hold_s", 1.5), "via": via, "hover_clearance_m": gp.get("hover_clearance_m"),
           "out_npz": str(out_dir / "targets.npz")}
    (out_dir / "request.json").write_text(json.dumps(req))
    r = subprocess.run([str(HH_PY), str(REPO / "scripts" / "homehero_grasp_adapter.py")],
                       input=json.dumps(req), capture_output=True, text=True, cwd=HH)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit("the planner adapter failed")
    plan = json.loads(r.stdout.strip().splitlines()[-1])
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=1))
    if not plan["passed"]:
        print(f"GRASP_PLAN FAIL {spec['name']}: {plan.get('reason')}; trials: "
              + "; ".join(str(t.get("failure"))[:120] for t in (plan.get("trials") or [])[:6]))
        return 1
    print(f"GRASP_PLAN OK {spec['name']}: {plan['ticks']} ticks ({plan['seconds_at_30hz']} s at 30 Hz), stages {plan['stage_ticks']}, "
          f"closure {plan['closure_targets']}, opposed fingers predicted "
          f"{(plan['candidate'].get('contact_prediction') or {}).get('opposed_fingers')}, planned in {plan['seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
