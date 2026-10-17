#!/usr/bin/env python3
"""A drilled hole proved in PhysX: the board built from a take's outcome
(grasp/outcome.json: material_laws.hole_pieces' exact convex colliders) and two
gauge pegs pushed along the hole's axis, one at a time - one a little under
the hole's size, one over it. The hole is there, and its size, if the small
peg runs through (or, blind, stops at the hole's depth) and the large one
stops at the face. Writes <scene>/grasp/probe.json; prints PROBE PASS|FAIL.
(Isaac's python.sh; one Kit at a time - scripts/isaac_slot.sh.)

    probe_hole.py <scene_dir> [--under-mm 0.15] [--over-mm 0.5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--under-mm", type=float, default=0.15, help="the fitting peg's radius under the hole's (mm)")
ap.add_argument("--over-mm", type=float, default=0.5, help="the oversize peg's radius over the hole's (mm)")
ap.add_argument("--speed", type=float, default=0.05, help="the pegs' speed into the hole (m/s)")
ap.add_argument("--seconds", type=float, default=1.5)
args = ap.parse_args()
scene_dir = Path(args.scene_dir).resolve()
outcome = json.loads((scene_dir / "grasp" / "outcome.json").read_text())
spec = json.loads((scene_dir / "spec.json").read_text())

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, UsdGeom, UsdPhysics  # noqa: E402

from build_er_scene import _holed_board  # noqa: E402

fx = dict(next(f for f in spec["environment"]["fixtures"] if f["name"] == outcome["fixture"]))
fx.pop("drillable", None)
h = outcome["holes"][0]
n = np.asarray(h["out"], float)
into = -n
entry = np.asarray(h["entry"], float)
r = float(h["r"])
HZ = 240
L = 0.04


def run(peg_r: float, label: str) -> dict:
    ctx = omni.usd.get_context()
    ctx.new_stage()
    for _ in range(5):
        app.update()
    st = ctx.get_stage()
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    scene = UsdPhysics.Scene.Define(st, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)
    scene.GetPrim().AddAppliedSchema("PhysxSceneAPI")
    scene.GetPrim().CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(False)
    scene.GetPrim().CreateAttribute("physxScene:broadphaseType", Sdf.ValueTypeNames.Token).Set("MBP")
    scene.GetPrim().CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(HZ)
    _holed_board(st, fx, outcome["holes"])
    # the peg: a cylinder on the hole's axis, its nose 5 mm short of the face, pushed in
    k = int(np.argmax(np.abs(into)))
    peg = UsdGeom.Cylinder.Define(st, "/World/Peg")
    peg.CreateAxisAttr("XYZ"[k])
    peg.CreateRadiusAttr(peg_r)
    peg.CreateHeightAttr(L)
    c0 = entry - into * (0.005 + L / 2)
    peg.AddTranslateOp().Set(Gf.Vec3d(*c0.tolist()))
    p = peg.GetPrim()
    UsdPhysics.CollisionAPI.Apply(p)
    rb = UsdPhysics.RigidBodyAPI.Apply(p)
    rb.CreateVelocityAttr(Gf.Vec3f(*(into * args.speed).tolist()))
    UsdPhysics.MassAPI.Apply(p).CreateMassAttr(0.005)
    p.AddAppliedSchema("PhysxRigidBodyAPI")
    p.CreateAttribute("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool).Set(True)
    p.AddAppliedSchema("PhysxCollisionAPI")
    p.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(0.0005)
    p.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(0.0)
    for _ in range(5):
        app.update()
    px = omni.physx.get_physx_interface()
    omni.timeline.get_timeline_interface().play()
    for _ in range(3):
        app.update()
    t, track = 0.0, []
    for i in range(int(args.seconds * HZ)):
        px.update_simulation(1.0 / HZ, t)
        t += 1.0 / HZ
        if i % 12 == 0:
            q = px.get_rigidbody_transformation("/World/Peg")
            pos = np.asarray(q["position"], float)
            track.append(round(float((pos + into * L / 2 - entry) @ into) * 1000, 2))     # the nose past the face (mm)
    omni.timeline.get_timeline_interface().stop()
    q = px.get_rigidbody_transformation("/World/Peg")
    pos = np.asarray(q["position"], float)
    nose = float((pos + into * L / 2 - entry) @ into)
    lateral = float(np.linalg.norm((pos - entry) - into * float((pos - entry) @ into)))
    return {"peg": label, "peg_d_mm": round(2000 * peg_r, 3), "nose_past_face_mm": round(nose * 1000, 2),
            "lateral_mm": round(lateral * 1000, 3), "track_mm": track}


fit = run(r - args.under_mm / 1000.0, "under")
over = run(r + args.over_mm / 1000.0, "over")
depth = float(h["depth_m"])
t_board = float(fx["size"][int(np.argmax(np.abs(n)))])
fit_ok = (fit["nose_past_face_mm"] > 1000 * t_board + 2.0) if h.get("through") else (abs(fit["nose_past_face_mm"] - 1000 * depth) < 1.0)
over_ok = over["nose_past_face_mm"] < 0.5
res = {"outcome": str(scene_dir / "grasp" / "outcome.json"), "hole_d_mm": round(2000 * r, 3), "through": bool(h.get("through")),
       "depth_mm": round(1000 * depth, 2), "board_mm": round(1000 * t_board, 1), "fit": fit, "over": over,
       "fit_ok": bool(fit_ok), "over_ok": bool(over_ok), "pass": bool(fit_ok and over_ok)}
(scene_dir / "grasp" / "probe.json").write_text(json.dumps(res, indent=1))
print(f"PROBE {'PASS' if res['pass'] else 'FAIL'} {scene_dir.name}: a {res['hole_d_mm']} mm hole "
      f"({'through ' + str(res['board_mm']) + ' mm' if res['through'] else str(res['depth_mm']) + ' mm deep'}): the {fit['peg_d_mm']} mm peg's nose "
      f"{fit['nose_past_face_mm']} mm past the face, the {over['peg_d_mm']} mm peg's {over['nose_past_face_mm']} mm", flush=True)
app.close()
