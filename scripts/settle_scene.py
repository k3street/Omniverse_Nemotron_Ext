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
# a door held shut by its gasket in the replay (grasp_replay.door) is held so in
# the settle too: free, a refrigerator's door drifted open as it settled
DOOR = None
_dc = (spec.get("grasp_replay") or {}).get("door")
if _dc:
    import omni.physics.tensors as _tensors
    sys_path = str(Path(__file__).resolve().parent)
    import sys as _sys
    _sys.path.insert(0, sys_path)
    from behaviors import gasket as _gasket, hinge_friction as _friction, latch_kick as _latch
    _sv = _tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
    _sv.set_subspace_roots("/")
    _root = next(q for q in stage.Traverse() if q.HasAPI(UsdPhysics.ArticulationRootAPI) and "/World/Objects/" in q.GetPath().pathString
                 and any(j.GetName() == _dc["joint"] for j in Usd.PrimRange(q.GetParent())))
    _av = _sv.create_articulation_view(_root.GetPath().pathString)
    _names = list(_av.shared_metatype.dof_names)
    _jp = next(j for j in Usd.PrimRange(_root.GetParent()) if j.GetName() == _dc["joint"])
    _lo, _hi = float(_jp.GetAttribute("physics:lowerLimit").Get()), float(_jp.GetAttribute("physics:upperLimit").Get())
    # the lever arms as the replay measures them are not needed to hold the door shut: the full
    # opening force at a 0.5 m handle, decaying over the gasket's reach
    DOOR = {"view": _av, "dof": _names.index(_dc["joint"]), "n": len(_names), "sign": 1.0 if abs(_hi) >= abs(_lo) else -1.0}
    print(f"[settle] holding {_dc['joint']} shut ({_dc.get('law', 'magnetic_gasket')})", flush=True)
for k in range(int(args.seconds * HZ)):
    if DOOR is not None:
        th = float(np.asarray(DOOR["view"].get_dof_positions(), float).reshape(-1)[DOOR["dof"]]) * DOOR["sign"]
        f = np.zeros((1, DOOR["n"]), np.float32)
        if _dc.get("law", "magnetic_gasket") == "latch_kick":
            f[0, DOOR["dof"]] = DOOR["sign"] * _latch(th, 0.0, DOOR.setdefault("state", {}), float(_dc.get("trip_m", 0.006)),
                                                      float(_dc.get("latch_torque_n_m", 30.0)), 0.0, 1.0)
        else:
            f[0, DOOR["dof"]] = -DOOR["sign"] * _gasket(th, float(_dc.get("opening_force_n", 35.0)), 0.5, 0.6, float(_dc.get("decay_m", 0.003)))
        if float(_dc.get("hinge_friction_n_m", 0.0)) > 0.0:
            om = float(np.asarray(DOOR["view"].get_dof_velocities(), float).reshape(-1)[DOOR["dof"]]) * DOOR["sign"]
            f[0, DOOR["dof"]] += DOOR["sign"] * _friction(om, float(_dc["hinge_friction_n_m"]))
        DOOR["view"].set_dof_actuation_forces(f, np.arange(1, dtype=np.int32))
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
