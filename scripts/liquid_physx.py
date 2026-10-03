#!/usr/bin/env python3
"""Water in PhysX (Isaac Sim, headless): PBD particle fluid in a sim-ready cup.

PhysX's GPU position-based-dynamics particles are the liquid; the cup is a
kinematic body carrying the asset's meshes as visuals and, as the liquid's
collider, a watertight shell revolved from the measured cavity
(liquid_newton.cup_cavity / cup_collider - an artist's mug is not
watertight and its walls are thinner than the particles' contact offset).

  pour <cup_asset_id> [--fill 0.7] [--tilt 0|100] [--spacing 0.003]
        fill the cavity with water, then tip the cup over its base edge on
        the side away from the handle (--tilt 0 is the upright control: all
        the water must stay in). Measured: volume kept, poured, and the
        settled surface height (water keeps its volume - the check Newton's
        MPM failed).

Run in the machine's single Isaac slot:
    (source scripts/isaac_slot.sh; python.sh scripts/liquid_physx.py pour white_mug)
Evidence goes to the queue entry under "liquid_physx"; side views to
workspace/liquid_tests/<id>_physx_pour.png.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
QUEUE_DIR = REPO / "workspace" / "review_queue"
OUT = REPO / "workspace" / "liquid_tests"

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = ap.add_subparsers(dest="cmd", required=True)
pp = sub.add_parser("pour")
pp.add_argument("asset")
pp.add_argument("--fill", type=float, default=0.7)
pp.add_argument("--tilt", type=float, default=100.0)
pp.add_argument("--seconds", type=float, default=3.0)
pp.add_argument("--spacing", type=float, default=0.003, help="particle spacing, m")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import omni.physx  # noqa: E402
import omni.usd  # noqa: E402
from omni.physx.scripts import particleUtils, physicsUtils  # noqa: E402
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt  # noqa: E402

from liquid_newton import cup_cavity, cup_collider, fill_points  # noqa: E402

# particle positions back to USD every step, so they can be read here
carb.settings.get_settings().set("/physics/updateParticlesToUsd", True)
carb.settings.get_settings().set("/physics/updateToUsd", True)


def world_points(stage, root):
    pts = []
    for p in Usd.PrimRange(stage.GetPrimAtPath(root)):
        if p.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(p).ComputeVisibility() != UsdGeom.Tokens.invisible:
            m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
            pts += [list(m.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(p).GetPointsAttr().Get()]
    return np.array(pts)


def pour():
    entry = json.loads((QUEUE_DIR / f"{args.asset}.json").read_text())
    ctx = omni.usd.get_context()
    ctx.new_stage()
    for _ in range(5):
        app.update()
    stage = ctx.get_stage()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr().Set(9.81)
    sp = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
    sp.CreateEnableGPUDynamicsAttr().Set(True)          # particles are GPU-only
    sp.CreateBroadphaseTypeAttr().Set("GPU")
    physicsUtils.add_ground_plane(stage, "/World/Ground", "Z", 2.0, Gf.Vec3f(0.0), Gf.Vec3f(0.6))

    # the cup: the asset's meshes as visuals, under a kinematic body
    cup = UsdGeom.Xform.Define(stage, "/World/Cup")
    visual = stage.DefinePrim("/World/Cup/Visual", "Xform")
    visual.GetReferences().AddReference(entry["file"])
    for _ in range(5):
        app.update()
    for p in Usd.PrimRange(visual):                       # the visuals do not collide or fall
        for api in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI, UsdPhysics.ArticulationRootAPI):
            if p.HasAPI(api):
                p.RemoveAPI(api)
    pts = world_points(stage, "/World/Cup/Visual")
    z0 = pts[:, 2].min()
    UsdGeom.XformCommonAPI(visual).SetTranslate(Gf.Vec3d(0, 0, -z0))
    pts[:, 2] -= z0
    centre, r_in, r_out, floor, rim = cup_cavity(pts)
    sp_ = args.spacing
    wall = max(r_out - r_in, 3 * sp_)
    cv, ct = cup_collider(r_in, r_in + wall, floor, rim, base=min(0.0, floor - 3 * sp_))
    cv[:, :2] += centre
    lo, hi = pts.min(0), pts.max(0)
    away = -1.0 if (hi[0] - centre[0]) > (centre[0] - lo[0]) else 1.0
    pivot = np.array([centre[0] + away * r_out, centre[1], 0.0])
    # the body's origin is the pivot, so tipping is a rotation of /World/Cup
    UsdGeom.XformCommonAPI(cup).SetTranslate(Gf.Vec3d(*pivot))
    UsdGeom.XformCommonAPI(visual).SetTranslate(Gf.Vec3d(-pivot[0], -pivot[1], -z0))
    col = UsdGeom.Mesh.Define(stage, "/World/Cup/LiquidCollider")
    col.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*(v - pivot)) for v in cv]))
    col.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(ct)))
    col.CreateFaceVertexIndicesAttr(Vt.IntArray([int(i) for i in ct.reshape(-1)]))
    col.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
    UsdPhysics.CollisionAPI.Apply(col.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(col.GetPrim()).CreateApproximationAttr().Set("none")  # triangles: kinematic
    rb = UsdPhysics.RigidBodyAPI.Apply(cup.GetPrim())
    rb.CreateKinematicEnabledAttr().Set(True)

    # the water
    fluid_rest = 0.5 * sp_
    particleUtils.add_physx_particle_system(
        stage, Sdf.Path("/World/ParticleSystem"), contact_offset=1.2 * sp_, rest_offset=fluid_rest,
        particle_contact_offset=fluid_rest / 0.6, solid_rest_offset=fluid_rest, fluid_rest_offset=fluid_rest,
        solver_position_iterations=16, simulation_owner=scene.GetPath())
    particleUtils.add_pbd_particle_material(stage, Sdf.Path("/World/WaterMaterial"), friction=0.05,
                                            viscosity=0.0005, surface_tension=0.0074, cohesion=0.01,
                                            vorticity_confinement=0.5, density=1000.0)
    physicsUtils.add_physics_material_to_prim(stage, stage.GetPrimAtPath("/World/ParticleSystem"),
                                              Sdf.Path("/World/WaterMaterial"))
    level = floor + args.fill * (rim - floor)
    water = fill_points(centre, r_in, floor, level, sp_, margin=sp_)
    vol_ml = len(water) * sp_ ** 3 * 1e6
    particleUtils.add_physx_particleset_points(
        stage, Sdf.Path("/World/Water"), [Gf.Vec3f(*p) for p in water], [Gf.Vec3f(0.0)] * len(water),
        [2 * fluid_rest] * len(water), Sdf.Path("/World/ParticleSystem"), self_collision=True, fluid=True,
        particle_group=0, particle_mass=1000.0 * sp_ ** 3, density=0.0)
    for _ in range(10):
        app.update()

    px = omni.physx.get_physx_interface()
    px.start_simulation()
    fps, sub_steps = 60, 2
    dt = 1.0 / fps / sub_steps
    tip_s = 0.5 * args.seconds
    rot_op = UsdGeom.Xformable(cup)
    t, frames = 0.0, []
    ax = Gf.Vec3d(0, away, 0)
    for f in range(int(args.seconds * fps)):
        a = args.tilt * min(1.0, t / tip_s)
        UsdGeom.XformCommonAPI(cup).SetRotate(Gf.Vec3f(*(Gf.Vec3d(ax) * a)))  # tipping toward `away`
        for _ in range(sub_steps):
            px.update_simulation(dt, t)
            t += dt
        px.update_transformations(False, True, True, False)
        if f % 15 == 0:
            q = np.array(UsdGeom.Points(stage.GetPrimAtPath("/World/Water")).GetPointsAttr().Get())
            frames.append((t, q.copy(), a))
    q = np.array(UsdGeom.Points(stage.GetPrimAtPath("/World/Water")).GetPointsAttr().Get())
    if not np.isfinite(q).all():
        print("LIQUID ERROR: solve diverged", flush=True)
        return
    # back into the upright cup's frame (Gf matrices act on row vectors)
    rot = np.array(Gf.Matrix3d(Gf.Rotation(ax, args.tilt)))
    in_cup = (q - pivot) @ np.linalg.inv(rot) + pivot
    rr = np.linalg.norm(in_cup[:, :2] - centre, axis=1)
    kept = (rr < r_in + sp_) & (in_cup[:, 2] > floor - sp_) & (in_cup[:, 2] < rim + sp_)
    poured = ~kept & (q[:, 2] < rim)
    ml = vol_ml / len(water)
    surface = float(np.percentile(in_cup[kept, 2], 98)) if kept.any() else 0.0
    ev = {"date": date.today().isoformat(), "method": "physx_pbd_fluid_pour", "particles": int(len(water)),
          "spacing_mm": sp_ * 1000, "fill_ml": round(vol_ml, 1), "tilt_deg": args.tilt,
          "kept_ml": round(kept.sum() * ml, 1), "poured_ml": round(poured.sum() * ml, 1),
          "elsewhere_ml": round((~kept & ~poured).sum() * ml, 1),
          "surface_mm_start": round(level * 1000, 1), "surface_mm_end": round(surface * 1000, 1)}
    entry = json.loads((QUEUE_DIR / f"{args.asset}.json").read_text())
    entry.setdefault("liquid_physx", {})[f"tilt_{args.tilt:g}"] = ev
    (QUEUE_DIR / f"{args.asset}.json").write_text(json.dumps(entry, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(OUT / f"{args.asset}_physx_frames.npz", **{f"water{i}": fr[1][:, [0, 2]] for i, fr in enumerate(frames)},
             labels=np.array([f"t={fr[0]:.2f}s {fr[2]:.0f}deg" for fr in frames]))
    print("LIQUID " + json.dumps(ev), flush=True)


if args.cmd == "pour":
    pour()
app.close()
