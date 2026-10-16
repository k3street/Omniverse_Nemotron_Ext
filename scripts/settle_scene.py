#!/usr/bin/env python3
"""Let a built scene's rigid objects settle in PhysX and write where every
body of them came to rest: an articulated asset hung on a peg swings on its
joints (a scissors' free half opens under gravity), and the planner plans
against the settled pose, not the authored one. (Kit; the Isaac slot.)

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/settle_scene.py workspace/scenes/<name> [--seconds 4]
writes <scene>/settled_poses.json: {body prim path: {"p": [x, y, z], "q_wxyz": [...]}} plus each articulation joint's angle
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--seconds", type=float, default=4.0)
args = ap.parse_args()
scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Usd, UsdGeom, UsdPhysics  # noqa: E402

omni.usd.get_context().open_stage(str(scene_dir / "scene.usda"))
for _ in range(30):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
omni.timeline.get_timeline_interface().play()
for _ in range(10):
    app.update()
HZ = int(spec.get("physics_hz", 120))
DT = 1.0 / HZ
t = 0.0
xc0 = UsdGeom.XformCache()
first = next((q for o in stage.GetPrimAtPath("/World/Objects").GetChildren() for q in Usd.PrimRange(o) if q.HasAPI(UsdPhysics.RigidBodyAPI)), None)
p_start = np.array(xc0.GetLocalToWorldTransform(first).ExtractTranslation()) if first else None
trajectory = []
for k in range(int(args.seconds * HZ)):
    px.update_simulation(DT, t)
    t += DT
    if first is not None and k % (HZ // 4) == 0:
        px.update_transformations(False, True, False, False)
        pk = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(first).ExtractTranslation())
        trajectory.append({"t": round(t, 3), "p": [round(float(v), 4) for v in pk], "moved_m": round(float(np.linalg.norm(pk - p_start)), 4)})
        if trajectory[-1]["moved_m"] > 0.05 and (len(trajectory) < 2 or trajectory[-2]["moved_m"] <= 0.05):
            print(f"[settle] {first.GetName()} left its start at t {t:.2f} s: {trajectory[-1]}", flush=True)
px.update_transformations(False, True, False, False)
for _ in range(3):
    app.update()
xc = UsdGeom.XformCache()
out = {}
bodies = [(o, q) for o in stage.GetPrimAtPath("/World/Objects").GetChildren() for q in Usd.PrimRange(o) if q.HasAPI(UsdPhysics.RigidBodyAPI)]
# the poses from the physics tensors (PhysX's own state) beside what USD holds:
# after a GPU fault USD showed one pose for every body of an articulated asset
import omni.physics.tensors as tensors  # noqa: E402
sim_view = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
sim_view.set_subspace_roots("/")
tensor_pose = {}
for o, q in bodies:
    try:
        rbv = sim_view.create_rigid_body_view(q.GetPath().pathString)
        tf = np.asarray(rbv.get_transforms(), float).reshape(-1, 7)[0]        # x y z qx qy qz qw
        tensor_pose[q.GetPath().pathString] = {"p": [round(float(v), 5) for v in tf[:3]],
                                               "q_wxyz": [round(float(tf[6]), 6)] + [round(float(v), 6) for v in tf[3:6]]}
    except Exception as e:                                                     # an articulation link is not a rigid body view
        tensor_pose[q.GetPath().pathString] = {"error": str(e)[:80]}
for o, q in bodies:
    M = xc.GetLocalToWorldTransform(q)
    p = M.ExtractTranslation(); r = M.ExtractRotationQuat()
    usd = {"p": [round(v, 5) for v in p], "q_wxyz": [round(r.GetReal(), 6)] + [round(v, 6) for v in r.GetImaginary()]}
    tp = tensor_pose.get(q.GetPath().pathString, {})
    best = tp if "p" in tp else usd
    out[q.GetPath().pathString] = {"object": o.GetName(), **best, "usd": usd, "tensors": tp}
    print(f"[settle] {o.GetName()}:{q.GetName()} tensors {tp.get('p', tp.get('error'))} usd {usd['p']}", flush=True)
# each articulated object's joints where they came to rest (a scissors' halves
# swing on the pivot as it settles: the loops' spread is planned from there)
joints = {}
for q in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects")):
    if q.HasAPI(UsdPhysics.ArticulationRootAPI):
        try:
            av = sim_view.create_articulation_view(q.GetPath().pathString)
            names = av.shared_metatype.dof_names
            pos = np.asarray(av.get_dof_positions(), float).reshape(-1)
            types = [str(t) for t in av.shared_metatype.dof_types] if hasattr(av.shared_metatype, "dof_types") else []
            joints[q.GetPath().pathString] = {n: round(float(np.degrees(v)) if "ranslation" not in (types[i] if i < len(types) else "") else float(v), 4)
                                              for i, (n, v) in enumerate(zip(names, pos))}
            print(f"[settle] joints {q.GetName()}: {joints[q.GetPath().pathString]}", flush=True)
        except Exception as e:                                                 # noqa: BLE001
            joints[q.GetPath().pathString] = {"error": str(e)[:80]}
(scene_dir / "settled_poses.json").write_text(json.dumps({"seconds": args.seconds, "bodies": out, "joints": joints, "trajectory": trajectory}, indent=1))
print("SCENE_SETTLED " + json.dumps({"bodies": len(out), "seconds": args.seconds}), flush=True)
app.close()
