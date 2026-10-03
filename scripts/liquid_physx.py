#!/usr/bin/env python3
"""Water in PhysX (Isaac Sim, headless): PBD particle fluid in a sim-ready cup.

PhysX's GPU position-based-dynamics particles are the liquid; the cup is a
kinematic body carrying the asset's meshes as visuals and, as the liquid's
collider, the measured cavity (liquid_newton.cup_cavity) rebuilt from convex
primitives - a floor disc and a ring of wall boxes (an artist's mug is not
watertight, and its walls are thinner than the particles' contact offset).

  pour <cup_asset_id> [--fill 0.7] [--tilt 0|100] [--spacing 0.003]
        fill the cavity with water, then tip the cup over its base edge on
        the side away from the handle (--tilt 0 is the upright control: all
        the water must stay in). Measured: volume kept, poured, and the
        settled surface height against the height the fill's volume stands
        at (water keeps its volume - the check Newton's MPM failed).

Measured on white_mug (3 mm particles, 10.6k): upright 202.6/202.6 mL kept,
surface 58.4 mm vs 58.7 expected; tipped 100 deg, 179.7 mL poured. What it
took (each measured, each a silent failure otherwise):
  - the scene's timeStepsPerSecond set to the substep rate: PhysX steps at
    the scene's rate whatever dt update_simulation is given, and at 60 Hz
    3 mm particles boil out of the cup;
  - not an SDF collider: the revolved shell as an SDF let 40% of the water
    out of the upright cup (as raw triangles it held, once the step rate was
    right; the convex primitives are the default as the more robust contact);
  - the fill on a face-centred cubic lattice: PBD water settles into close
    packing, so a cubic grid slumps to 1/sqrt(2) of its height.

Run in the machine's single Isaac slot:
    (source scripts/isaac_slot.sh; python.sh scripts/liquid_physx.py pour white_mug)
Evidence goes to the queue entry under "liquid_physx"; side views to
workspace/liquid_tests/<id>_physx_pour_tilt<deg>.png.
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
pp.add_argument("--static", action="store_true", help="a static cup (diagnostic): no kinematic body, no tilt")
pp.add_argument("--contact", type=float, default=0.005, help="particle-rigid contact margin over the rest offset, m")
pp.add_argument("--substeps", type=int, default=4)
pp.add_argument("--iters", type=int, default=32, help="particle solver position iterations")
pp.add_argument("--tension", type=float, default=0.0, help="PBD surface tension (and cohesion), 0 = off")
pp.add_argument("--vorticity", type=float, default=0.0)
pp.add_argument("--max-vel", type=float, default=0.0, help="particle speed cap, m/s (0 = none)")
pp.add_argument("--solid", type=float, default=0.83, help="particle-rigid rest offset / spacing")
pp.add_argument("--damping", type=float, default=0.0, help="PBD material damping")
pp.add_argument("--probe", action="store_true", help="raycast the liquid collider before the pour (diagnostic)")
pp.add_argument("--guide", action="store_true", help="give the liquid collider purpose guide (diagnostic)")
pp.add_argument("--approx", default="boxes",
                help="liquid collider: boxes (a floor disc and a ring of wall boxes - convex shapes, what PhysX "
                     "particles collide with reliably), or the revolved shell as sdf / none (triangles)")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import omni.physx  # noqa: E402
import omni.usd  # noqa: E402
from omni.physx.scripts import particleUtils, physicsUtils  # noqa: E402
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt  # noqa: E402

from liquid_newton import cup_cavity, cup_collider, draw_frames_png  # noqa: E402

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


def fcc_fill(centre, r_in, floor, level, spacing, margin):
    """Particles filling the cavity cylinder to `level` on a face-centred cubic lattice,
    nearest neighbours `spacing` apart: the packing PBD water settles into (measured:
    a cubic grid slumped to 1/sqrt(2) of its height), so the fill starts at rest."""
    a = spacing * math.sqrt(2.0)
    basis = np.array([[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]]) * a
    xs = np.arange(-r_in, r_in + 1e-9, a)
    zs = np.arange(floor + margin, level, a)
    g = np.stack(np.meshgrid(xs, xs, zs, indexing="ij"), -1).reshape(-1, 3)
    g = (g[:, None, :] + basis[None]).reshape(-1, 3)
    g = g[(np.hypot(g[:, 0], g[:, 1]) < r_in - margin) & (g[:, 2] < level)]
    g += (np.random.default_rng(7).random(g.shape) - 0.5) * 0.1 * spacing
    g[:, :2] += centre
    return g


def convex_container(stage, path, centre, r_in, wall, floor, rim, base, n=32):
    """The cavity as convex primitives: a floor cylinder and n wall boxes on a ring
    (overlapping, so no gap opens between neighbours). Invisible; collision only."""
    root = UsdGeom.Xform.Define(stage, path)
    UsdGeom.Imageable(root).MakeInvisible()
    r_mid = r_in + 0.5 * wall
    disc = UsdGeom.Cylinder.Define(stage, f"{path}/Floor")
    disc.CreateAxisAttr().Set("Z")
    disc.CreateRadiusAttr().Set(r_in + wall)
    disc.CreateHeightAttr().Set(floor - base)
    UsdGeom.XformCommonAPI(disc).SetTranslate(Gf.Vec3d(centre[0], centre[1], 0.5 * (floor + base)))
    UsdPhysics.CollisionAPI.Apply(disc.GetPrim())
    chord = 2 * (r_in + wall) * math.tan(math.pi / n) * 1.15
    for k in range(n):
        th = 2 * math.pi * k / n
        b = UsdGeom.Cube.Define(stage, f"{path}/Wall{k:02d}")
        b.CreateSizeAttr().Set(1.0)
        x = UsdGeom.XformCommonAPI(b)
        x.SetTranslate(Gf.Vec3d(centre[0] + r_mid * math.cos(th), centre[1] + r_mid * math.sin(th), 0.5 * (rim + base)))
        x.SetRotate(Gf.Vec3f(0, 0, math.degrees(th)))
        x.SetScale(Gf.Vec3f(wall, chord, rim - base))
        UsdPhysics.CollisionAPI.Apply(b.GetPrim())
    return root


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
    # the scene's own step rate is what PhysX steps at, whatever dt update_simulation is
    # given (measured: 2, 4 and 8 substeps gave identical results at the default 60 Hz)
    sp.CreateTimeStepsPerSecondAttr().Set(60 * args.substeps)
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
    if args.approx == "boxes":
        convex_container(stage, "/World/Cup/LiquidCollider", centre - pivot[:2], r_in, wall, floor,
                         rim, min(0.0, floor - 3 * sp_))
    else:
        col = UsdGeom.Mesh.Define(stage, "/World/Cup/LiquidCollider")
        col.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*(v - pivot)) for v in cv]))
        col.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(ct)))
        col.CreateFaceVertexIndicesAttr(Vt.IntArray([int(i) for i in ct.reshape(-1)]))
        if args.guide:
            col.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
        UsdPhysics.CollisionAPI.Apply(col.GetPrim())
        # measured upright: triangles held all of it, an SDF let 40% out
        UsdPhysics.MeshCollisionAPI.Apply(col.GetPrim()).CreateApproximationAttr().Set(args.approx)
        if args.approx == "sdf":
            PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(col.GetPrim()).CreateSdfResolutionAttr().Set(256)
    if not args.static:
        rb = UsdPhysics.RigidBodyAPI.Apply(cup.GetPrim())
        rb.CreateKinematicEnabledAttr().Set(True)

    # the water
    fluid_rest = 0.5 * sp_                 # the PhysX demos' ratios: fluid 0.5, solid 0.83 of the spacing
    solid_rest = args.solid * sp_
    particleUtils.add_physx_particle_system(
        stage, Sdf.Path("/World/ParticleSystem"), contact_offset=solid_rest + args.contact, rest_offset=solid_rest,
        particle_contact_offset=fluid_rest / 0.6, solid_rest_offset=solid_rest, fluid_rest_offset=fluid_rest,
        solver_position_iterations=args.iters, max_neighborhood=96, simulation_owner=scene.GetPath())
    if args.max_vel > 0:
        PhysxSchema.PhysxParticleSystem(stage.GetPrimAtPath("/World/ParticleSystem")).CreateMaxVelocityAttr().Set(args.max_vel)
    particleUtils.add_pbd_particle_material(stage, Sdf.Path("/World/WaterMaterial"), friction=0.05,
                                            viscosity=0.0005, surface_tension=args.tension, cohesion=args.tension,
                                            vorticity_confinement=args.vorticity, damping=args.damping, density=1000.0)
    physicsUtils.add_physics_material_to_prim(stage, stage.GetPrimAtPath("/World/ParticleSystem"),
                                              Sdf.Path("/World/WaterMaterial"))
    level = floor + args.fill * (rim - floor)
    water = fcc_fill(centre, r_in, floor, level, sp_, margin=solid_rest)
    cell = sp_ ** 3 / math.sqrt(2.0)                     # the volume each particle stands for
    vol_ml = len(water) * cell * 1e6
    particleUtils.add_physx_particleset_points(
        stage, Sdf.Path("/World/Water"), [Gf.Vec3f(*p) for p in water], [Gf.Vec3f(0.0)] * len(water),
        [2 * fluid_rest] * len(water), Sdf.Path("/World/ParticleSystem"), self_collision=True, fluid=True,
        particle_group=0, particle_mass=1000.0 * cell, density=0.0)
    for _ in range(10):
        app.update()

    px = omni.physx.get_physx_interface()
    px.start_simulation()
    if args.probe:
        print(f"PROBE cavity centre={centre} r_in={r_in:.4f} r_out={r_out:.4f} floor={floor:.4f} rim={rim:.4f} "
              f"collider z {cv[:, 2].min():.4f}..{cv[:, 2].max():.4f} water z {water[:, 2].min():.4f}", flush=True)
        px.update_simulation(dt_probe := 1e-4, 0.0)
        sq = omni.physx.get_physx_scene_query_interface()
        for name, xy in (("centre", centre), ("wall", centre + [r_in + 0.5 * wall, 0.0])):
            hit = sq.raycast_closest(carb.Float3(float(xy[0]), float(xy[1]), 0.5), carb.Float3(0, 0, -1), 1.0)
            print(f"PROBE ray {name}: {hit['hit'] and (hit['collision'], round(hit['position'][2], 4))}", flush=True)
    fps, sub_steps = 60, args.substeps
    dt = 1.0 / fps / sub_steps
    tip_s = 0.5 * args.seconds
    rot_op = UsdGeom.Xformable(cup)
    t, frames = 0.0, []
    ax = Gf.Vec3d(0, away, 0)
    for f in range(int(args.seconds * fps)):
        a = 0.0 if args.static else args.tilt * min(1.0, t / tip_s)
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
          "spacing_mm": sp_ * 1000, "collider": args.approx,
          "static_cup": args.static, "fill_ml": round(vol_ml, 1), "tilt_deg": args.tilt,
          "kept_ml": round(kept.sum() * ml, 1), "poured_ml": round(poured.sum() * ml, 1),
          "elsewhere_ml": round((~kept & ~poured).sum() * ml, 1),
          "surface_mm_start": round(level * 1000, 1), "surface_mm_end": round(surface * 1000, 1),
          # the level the fill's volume stands at in the cavity: a fluid that keeps its volume settles there
          "surface_mm_expected": round((floor + vol_ml * 1e-6 / (math.pi * r_in ** 2)) * 1000, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    npz = OUT / f"{args.asset}_physx_frames.npz"
    pick = frames[::max(1, len(frames) // 6)][:6]
    cups = [(pts[::5] - pivot) @ np.array(Gf.Matrix3d(Gf.Rotation(ax, fr[2]))) + pivot for fr in pick]
    np.savez(npz, **{f"water{i}": fr[1][:, [0, 2]] for i, fr in enumerate(pick)},
             **{f"cup{i}": c[:, [0, 2]] for i, c in enumerate(cups)},
             labels=np.array([f"t={fr[0]:.2f}s {fr[2]:.0f}deg" for fr in pick]))
    ev["side_views"] = str(draw_frames_png(npz, OUT / f"{args.asset}_physx_pour_tilt{args.tilt:g}.png",
                                           around_cup=True).relative_to(REPO))
    entry = json.loads((QUEUE_DIR / f"{args.asset}.json").read_text())
    entry.setdefault("liquid_physx", {})[f"tilt_{args.tilt:g}"] = ev
    (QUEUE_DIR / f"{args.asset}.json").write_text(json.dumps(entry, indent=1))
    print("LIQUID " + json.dumps(ev), flush=True)


if args.cmd == "pour":
    pour()
app.close()
