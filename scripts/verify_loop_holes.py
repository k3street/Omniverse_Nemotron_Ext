#!/usr/bin/env python3
"""Does an asset's cooked collision keep its holes? A 6 mm ball dropped over
each hole centre falls through an open hole and stops on a filled one. A
convex decomposition fills small holes with its pieces: the scissors'
part02 loop stays open, part01's is filled (2026-10-15), and a peg through a
filled loop throws the asset across the room. (Kit; the Isaac slot.)

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/verify_loop_holes.py <asset.usda> '<json: {"name": [x, y], ...}>' [--z-top 0.018] [--ball-mm 6]
    the hole centres in the asset's frame, x and y; the asset lies as authored, z up
prints HOLE <name>: OPEN|FILLED and writes nothing
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("asset")
ap.add_argument("holes", help="JSON: {name: [x, y]} in the asset's frame")
ap.add_argument("--z-top", type=float, default=0.02, help="the asset's top, above which the ball starts")
ap.add_argument("--ball-mm", type=float, default=6.0)
ap.add_argument("--seconds", type=float, default=2.0)
args = ap.parse_args()
holes = json.loads(args.holes)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics  # noqa: E402

ctx = omni.usd.get_context()
ctx.new_stage()
stage = ctx.get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
sc = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
sc.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
sc.CreateGravityMagnitudeAttr(9.81)
sc.GetPrim().AddAppliedSchema("PhysxSceneAPI")
sc.GetPrim().CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(False)
sc.GetPrim().CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(480)
LIFT = 0.30
asset = stage.DefinePrim("/World/Asset", "Xform")
asset.GetReferences().AddReference(str(Path(args.asset).resolve()))
UsdGeom.Xformable(asset).AddTranslateOp().Set(Gf.Vec3d(0, 0, LIFT))     # held in the air, kinematic
for q in Usd.PrimRange(asset):
    if q.HasAPI(UsdPhysics.RigidBodyAPI):
        q.CreateAttribute("physics:kinematicEnabled", Sdf.ValueTypeNames.Bool).Set(True)
balls = {}
for name, (x, y) in holes.items():
    b = UsdGeom.Sphere.Define(stage, f"/World/Ball_{name}")
    b.CreateRadiusAttr(args.ball_mm / 2000.0)
    b.AddTranslateOp().Set(Gf.Vec3d(x, y, LIFT + args.z_top + 0.03))
    UsdPhysics.CollisionAPI.Apply(b.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(b.GetPrim())
    UsdPhysics.MassAPI.Apply(b.GetPrim()).CreateMassAttr(0.001)
    balls[name] = b.GetPrim()
px = omni.physx.get_physx_interface()
omni.timeline.get_timeline_interface().play()
for _ in range(10):
    app.update()
t = 0.0
for _ in range(int(480 * args.seconds)):
    px.update_simulation(1 / 480, t)
    t += 1 / 480
px.update_transformations(False, True, False, False)
app.update()
xc = UsdGeom.XformCache()
for name, prim in balls.items():
    z = xc.GetLocalToWorldTransform(prim).ExtractTranslation()[2]
    print(f"HOLE {name}: {'OPEN' if z < LIFT else 'FILLED'} (ball z {z:.4f}, asset at {LIFT})", flush=True)
app.close()
