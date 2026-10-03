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

  dispense <syringe_asset_id> [--cup white_mug] [--draw 0.06] [--push-s 2]
          [--nozzle-mm 0]
        the syringe's own articulation nozzle-down over the cup, fixed to the
        world; its plunger joint drawn back, then pushed by a position drive.
        PhysX has no flow through a barrel, so the barrel is an emitter: each
        frame the plunger's measured travel x the bore (the stopper's cross
        section) leaves the nozzle as particles, at the speed that flow needs
        through the opening (as modelled, or --nozzle-mm where the model hides
        the lumen). Measured: stroke, volume expelled vs stroke x bore, and
        how much lands in the cup.

Measured on syringe_ecec87 into white_mug (2 mm particles): bore 24 mm,
drawn 60 mm, pushed 71.5 mm, 32.4 mL expelled = stroke x bore; through the
modelled luer-lock collar (13.6 mm) 31.0 mL lands in the cup, through a
2 mm luer lumen the jet leaves at 5.2 m/s and 9.6 mL splashes out.

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
water_args = argparse.ArgumentParser(add_help=False)
water_args.add_argument("--spacing", type=float, default=0.003, help="particle spacing, m")
water_args.add_argument("--substeps", type=int, default=4)
water_args.add_argument("--iters", type=int, default=32, help="particle solver position iterations")
water_args.add_argument("--solid", type=float, default=0.83, help="particle-rigid rest offset / spacing")
water_args.add_argument("--contact", type=float, default=0.005, help="particle-rigid contact margin, m")
sub = ap.add_subparsers(dest="cmd", required=True)
pp = sub.add_parser("pour", parents=[water_args])
pp.add_argument("asset")
pp.add_argument("--fill", type=float, default=0.7)
pp.add_argument("--tilt", type=float, default=100.0)
pp.add_argument("--seconds", type=float, default=3.0)
pp.add_argument("--static", action="store_true", help="a static cup (diagnostic): no kinematic body, no tilt")
pp.add_argument("--approx", default="boxes",
                help="liquid collider: boxes (a floor disc and a ring of wall boxes - convex shapes, what PhysX "
                     "particles collide with reliably), or the revolved shell as sdf / none (triangles)")
dp = sub.add_parser("dispense", parents=[water_args])
dp.add_argument("syringe")
dp.add_argument("--cup", default="white_mug", help="the cup it dispenses into")
dp.add_argument("--draw", type=float, default=0.06, help="how far the plunger is drawn back first, m")
dp.add_argument("--push-s", type=float, default=2.0, help="seconds the push takes")
dp.add_argument("--seconds", type=float, default=4.0)
dp.add_argument("--nozzle-mm", type=float, default=0.0,
                help="the nozzle's bore, mm, where the model does not show it (a luer tip: ~2); 0 = as modelled")
dp.add_argument("--gap", type=float, default=0.03, help="nozzle height over the cup's rim, m")
args = ap.parse_args()
if args.cmd == "dispense" and "--spacing" not in sys.argv:
    args.spacing = 0.002                                   # a syringe stream is thin

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import omni.physx  # noqa: E402
import omni.usd  # noqa: E402
from omni.physx.scripts import particleUtils, physicsUtils  # noqa: E402
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, Vt  # noqa: E402

from liquid_newton import cup_cavity, cup_collider, draw_frames_png  # noqa: E402

# particle positions (and velocities, for the emitter) back to USD every step
carb.settings.get_settings().set("/physics/updateParticlesToUsd", True)
carb.settings.get_settings().set("/physics/updateVelocitiesToUsd", True)
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


def new_stage():
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
    return stage, scene


def add_cup(stage, entry, approx="boxes", body=None):
    """The cup at the origin, standing on the ground: the asset's meshes as visuals
    and, as the liquid's collider, its measured cavity. The prim's origin is the
    base edge away from the handle (the pivot a pour tips it over); body
    "kinematic" makes it a kinematic body. Returns the cavity measures."""
    sp_ = args.spacing
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
    pts[:, 2] -= z0
    centre, r_in, r_out, floor, rim = cup_cavity(pts)
    wall = max(r_out - r_in, 3 * sp_)
    base = min(0.0, floor - 3 * sp_)
    lo, hi = pts.min(0), pts.max(0)
    away = -1.0 if (hi[0] - centre[0]) > (centre[0] - lo[0]) else 1.0
    pivot = np.array([centre[0] + away * r_out, centre[1], 0.0])
    UsdGeom.XformCommonAPI(cup).SetTranslate(Gf.Vec3d(*pivot))
    UsdGeom.XformCommonAPI(visual).SetTranslate(Gf.Vec3d(-pivot[0], -pivot[1], -z0))
    if approx == "boxes":
        convex_container(stage, "/World/Cup/LiquidCollider", centre - pivot[:2], r_in, wall, floor, rim, base)
    else:
        cv, ct = cup_collider(r_in, r_in + wall, floor, rim, base=base)
        cv[:, :2] += centre
        col = UsdGeom.Mesh.Define(stage, "/World/Cup/LiquidCollider")
        col.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*(v - pivot)) for v in cv]))
        col.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(ct)))
        col.CreateFaceVertexIndicesAttr(Vt.IntArray([int(i) for i in ct.reshape(-1)]))
        UsdPhysics.CollisionAPI.Apply(col.GetPrim())
        # measured upright: triangles held all of it, an SDF let 40% out
        UsdPhysics.MeshCollisionAPI.Apply(col.GetPrim()).CreateApproximationAttr().Set(approx)
        if approx == "sdf":
            PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(col.GetPrim()).CreateSdfResolutionAttr().Set(256)
    if body == "kinematic":
        UsdPhysics.RigidBodyAPI.Apply(cup.GetPrim()).CreateKinematicEnabledAttr().Set(True)
    return {"prim": cup, "pts": pts, "centre": centre, "r_in": r_in, "r_out": r_out, "floor": floor,
            "rim": rim, "wall": wall, "away": away, "pivot": pivot}


def add_water(stage, scene):
    """The PBD particle system and its water material. Returns (fluid rest
    offset, solid rest offset, the volume one particle stands for)."""
    sp_ = args.spacing
    fluid_rest = 0.5 * sp_                 # the PhysX demos' ratios: fluid 0.5, solid 0.83 of the spacing
    solid_rest = args.solid * sp_
    particleUtils.add_physx_particle_system(
        stage, Sdf.Path("/World/ParticleSystem"), contact_offset=solid_rest + args.contact, rest_offset=solid_rest,
        particle_contact_offset=fluid_rest / 0.6, solid_rest_offset=solid_rest, fluid_rest_offset=fluid_rest,
        solver_position_iterations=args.iters, max_neighborhood=96, simulation_owner=scene.GetPath())
    particleUtils.add_pbd_particle_material(stage, Sdf.Path("/World/WaterMaterial"), friction=0.05,
                                            viscosity=0.0005, surface_tension=0.0, cohesion=0.0,
                                            vorticity_confinement=0.0, density=1000.0)
    physicsUtils.add_physics_material_to_prim(stage, stage.GetPrimAtPath("/World/ParticleSystem"),
                                              Sdf.Path("/World/WaterMaterial"))
    # PBD water settles into close packing: each particle stands for sp^3 / sqrt(2)
    return fluid_rest, solid_rest, sp_ ** 3 / math.sqrt(2.0)


def add_particles(stage, pts, vel, fluid_rest, cell, max_particles=None):
    ps = particleUtils.add_physx_particleset_points(
        stage, Sdf.Path("/World/Water"), [Gf.Vec3f(*p) for p in pts], [Gf.Vec3f(*v) for v in vel],
        [2 * fluid_rest] * len(pts), Sdf.Path("/World/ParticleSystem"), self_collision=True, fluid=True,
        particle_group=0, particle_mass=1000.0 * cell, density=0.0)
    if max_particles:
        ps.GetPrim().CreateAttribute("physxParticle:maxParticles", Sdf.ValueTypeNames.Int).Set(max_particles)
    return ps


def in_cavity(q, c, tol):
    rr = np.linalg.norm(q[:, :2] - c["centre"], axis=1)
    return (rr < c["r_in"] + tol) & (q[:, 2] > c["floor"] - tol) & (q[:, 2] < c["rim"] + tol)


def save_evidence(asset, key, ev):
    entry = json.loads((QUEUE_DIR / f"{asset}.json").read_text())
    entry.setdefault("liquid_physx", {})[key] = ev
    (QUEUE_DIR / f"{asset}.json").write_text(json.dumps(entry, indent=1))
    print("LIQUID " + json.dumps(ev), flush=True)


def pour():
    entry = json.loads((QUEUE_DIR / f"{args.asset}.json").read_text())
    stage, scene = new_stage()
    sp_ = args.spacing
    c = add_cup(stage, entry, args.approx, body=None if args.static else "kinematic")
    cup, pts, centre, r_in, floor, rim, pivot = (c[k] for k in ("prim", "pts", "centre", "r_in", "floor", "rim",
                                                              "pivot"))
    fluid_rest, solid_rest, cell = add_water(stage, scene)
    level = floor + args.fill * (rim - floor)
    water = fcc_fill(centre, r_in, floor, level, sp_, margin=solid_rest)
    vol_ml = len(water) * cell * 1e6
    add_particles(stage, water, np.zeros_like(water), fluid_rest, cell)
    for _ in range(10):
        app.update()

    px = omni.physx.get_physx_interface()
    px.start_simulation()
    fps, sub_steps = 60, args.substeps
    dt = 1.0 / fps / sub_steps
    tip_s = 0.5 * args.seconds
    t, frames = 0.0, []
    ax = Gf.Vec3d(0, c["away"], 0)
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
    rot = np.array(Gf.Matrix3d(Gf.Rotation(ax, 0.0 if args.static else args.tilt)))
    in_cup = (q - pivot) @ np.linalg.inv(rot) + pivot
    kept = in_cavity(in_cup, c, sp_)
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
             **{f"cup{i}": c_[:, [0, 2]] for i, c_ in enumerate(cups)},
             labels=np.array([f"t={fr[0]:.2f}s {fr[2]:.0f}deg" for fr in pick]))
    ev["side_views"] = str(draw_frames_png(npz, OUT / f"{args.asset}_physx_pour_tilt{args.tilt:g}.png",
                                           around_cup=True).relative_to(REPO))
    save_evidence(args.asset, f"tilt_{args.tilt:g}", ev)


def dispense():
    """The syringe's own articulation, nozzle down over the cup and fixed to the
    world; its plunger joint is drawn back, then pushed by a position drive.
    The water is emitted at the nozzle: each frame, the plunger's measured
    travel toward the nozzle times the bore's area (the stopper's cross
    section) becomes particles leaving the nozzle at the speed that volume
    flow needs. PhysX has no flow through a barrel; the emitter is the barrel."""
    sy_entry = json.loads((QUEUE_DIR / f"{args.syringe}.json").read_text())
    draft = json.loads(sy_entry["articulation_draft"])
    joint = next(j for j in draft["joints"] if j["joint_type"] == "prismatic")
    stage, scene = new_stage()
    sp_ = args.spacing
    c = add_cup(stage, json.loads((QUEUE_DIR / f"{args.cup}.json").read_text()))
    fluid_rest, solid_rest, cell = add_water(stage, scene)

    # the syringe, nozzle down: a mount (rotation, placement) over the asset's own root
    root = draft["prim_path"]
    mount = UsdGeom.Xform.Define(stage, "/World/SyringeMount")
    sy = stage.DefinePrim("/World/SyringeMount/Syringe", "Xform")
    sy.GetReferences().AddReference(sy_entry["file"], root)
    for _ in range(5):
        app.update()
    here = lambda path: path.replace(root, "/World/SyringeMount/Syringe", 1)  # noqa: E731
    barrel, plunger = here(joint["parent_prim"]), here(joint["child_prim"])

    def axis_and_ends():
        bp, pq = world_points(stage, barrel), world_points(stage, plunger)
        jp = stage.GetPrimAtPath(here(f"{root}/Joints/{joint['name']}"))
        m = UsdGeom.Xformable(stage.GetPrimAtPath(barrel)).ComputeLocalToWorldTransform(0)
        rot0 = UsdPhysics.PrismaticJoint(jp).GetLocalRot0Attr().Get()
        e = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}[joint["axis"]]
        d = m.TransformDir(Gf.Rotation(rot0).TransformDir(e))
        d = np.array(d) / np.linalg.norm(d)
        if (bp @ d).max() < (pq @ d).max():                  # the push runs from the plunger toward the nozzle
            d = -d
        return d, bp, pq

    d, _, _ = axis_and_ends()
    # turn the syringe so the push points down, then put the nozzle over the cup
    rot = Gf.Rotation(Gf.Vec3d(*d), Gf.Vec3d(0, 0, -1))
    UsdGeom.XformCommonAPI(mount).SetRotate(Gf.Vec3f(*rot.Decompose(Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(),
                                                                       Gf.Vec3d.ZAxis())), UsdGeom.XformCommonAPI.RotationOrderXYZ)
    def nozzle():
        """The nozzle's centre (on the plunger's axis line - the plunger is round -
        at the barrel's far end), and the bore and nozzle radii about that line."""
        d, bp, pq = axis_and_ends()
        o = pq.mean(0)
        off = lambda x: np.linalg.norm(np.cross(x - o, d), axis=1)  # noqa: E731
        tip = o + d * ((bp - o) @ d).max()
        s_pq, s_bp = pq @ d, bp @ d
        bore_r = float(off(pq[s_pq > s_pq.max() - 0.008]).max())       # the stopper's cross-section
        end = off(bp[s_bp > s_bp.max() - 0.003])                        # the barrel's last 3 mm
        # a hollow end (a luer-lock collar) opens at its inner edge; a solid tip at its outside
        nozzle_r = float(end.min() if end.min() > 0.25 * end.max() else end.max())
        if args.nozzle_mm:
            nozzle_r = 0.0005 * args.nozzle_mm
        return d, tip, bore_r, nozzle_r

    d, tip, _, _ = nozzle()
    shift = np.array([c["centre"][0] - tip[0], c["centre"][1] - tip[1], c["rim"] + args.gap - tip[2]])
    UsdGeom.XformCommonAPI(mount).SetTranslate(Gf.Vec3d(*shift))
    d, tip, bore_r, nozzle_r = nozzle()
    print(f"SYRINGE push {d.round(3)} nozzle {tip.round(4)} bore {2000 * bore_r:.1f} mm "
          f"nozzle {2000 * nozzle_r:.1f} mm", flush=True)
    area = math.pi * bore_r ** 2

    # the barrel fixed to the world where it stands; the plunger on a position drive
    bm = UsdGeom.Xformable(stage.GetPrimAtPath(barrel)).ComputeLocalToWorldTransform(0)
    bt = Gf.Transform(bm)
    fix = UsdPhysics.FixedJoint.Define(stage, "/World/SyringeMount/FixToWorld")
    fix.CreateBody1Rel().SetTargets([barrel])
    fix.CreateLocalPos0Attr().Set(Gf.Vec3f(bt.GetTranslation()))
    fix.CreateLocalRot0Attr().Set(Gf.Quatf(bt.GetRotation().GetQuat()))
    fix.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))
    fix.CreateLocalRot1Attr().Set(Gf.Quatf(1.0))
    jp = stage.GetPrimAtPath(here(f"{root}/Joints/{joint['name']}"))
    drive = UsdPhysics.DriveAPI.Apply(jp, "linear")
    drive.CreateTypeAttr().Set("force")
    drive.CreateStiffnessAttr().Set(5000.0)
    drive.CreateDampingAttr().Set(200.0)
    drive.CreateMaxForceAttr().Set(200.0)
    drive.CreateTargetPositionAttr().Set(0.0)
    for _ in range(10):
        app.update()

    px = omni.physx.get_physx_interface()
    px.start_simulation()
    fps, sub_steps = 60, args.substeps
    dt = 1.0 / fps / sub_steps
    draw_s, hold_s = 0.6, 0.3
    push_end = max(joint["upper_limit"] - 0.001, 0.0)
    q0 = 0.0
    target = lambda t: (q0 - args.draw * min(1.0, t / draw_s) if t < draw_s + hold_s  # noqa: E731
                        else q0 - args.draw + (args.draw + push_end) * min(1.0, (t - draw_s - hold_s) / args.push_s))
    plunger_s = lambda: float(np.array(UsdGeom.Xformable(stage.GetPrimAtPath(plunger))  # noqa: E731
                                       .ComputeLocalToWorldTransform(0).ExtractTranslation()) @ d)
    s0 = plunger_s()
    rng = np.random.default_rng(3)
    t, frames, s_prev, s_min, owed = 0.0, [], s0, s0, 0.0
    emitted, nozzle_v, max_n = 0, 0.0, int(1.5 * args.draw * area / cell) + 1000
    water = None
    lane_r = max(nozzle_r, 0.5 * sp_)
    for f in range(int(args.seconds * fps)):
        drive.GetTargetPositionAttr().Set(float(target(t)))
        for _ in range(sub_steps):
            px.update_simulation(dt, t)
            t += dt
        px.update_transformations(False, True, True, False)
        s = plunger_s()
        if t < draw_s + hold_s:
            s_min, s_prev = min(s_min, s), s
        else:
            owed += max(0.0, s - s_prev) * area / cell           # particles the push displaced this frame
            s_prev = max(s_prev, s)
        n = int(owed)
        if n:
            owed -= n
            flow = n * cell * fps                                # m^3/s out of the nozzle
            v = flow / (math.pi * lane_r ** 2)
            nozzle_v = max(nozzle_v, v)
            # spread over the stretch the stream left the nozzle by this frame
            along = rng.random(n) * min(v / fps, 0.05) + sp_
            rr, th = lane_r * np.sqrt(rng.random(n)), rng.random(n) * 2 * math.pi
            e1 = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
            e1 /= np.linalg.norm(e1)
            e2 = np.cross(d, e1)
            new = tip + along[:, None] * d + (rr * np.cos(th))[:, None] * e1 + (rr * np.sin(th))[:, None] * e2
            new_v = np.repeat((d * v)[None], n, 0)
            if water is None:
                water = add_particles(stage, new, new_v, fluid_rest, cell, max_particles=max_n)
            else:
                old = np.array(water.GetPointsAttr().Get())
                old_v = np.array(water.GetVelocitiesAttr().Get())
                water.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(np.vstack([old, new]).astype(np.float32)))
                water.GetVelocitiesAttr().Set(Vt.Vec3fArray.FromNumpy(np.vstack([old_v, new_v]).astype(np.float32)))
                water.GetWidthsAttr().Set(Vt.FloatArray([2 * fluid_rest] * (len(old) + n)))
            emitted += n
        if f % 30 == 0 or f == int(args.seconds * fps) - 1:
            q = np.array(water.GetPointsAttr().Get()) if water is not None else np.zeros((0, 3))
            frames.append((t, q.copy(), s, np.vstack([world_points(stage, "/World/SyringeMount")[::7],
                                                      c["pts"][::5]])))
    stroke = s_prev - s_min
    q = np.array(water.GetPointsAttr().Get()) if water is not None else np.zeros((0, 3))
    if not np.isfinite(q).all():
        print("LIQUID ERROR: solve diverged", flush=True)
        return
    caught = in_cavity(q, c, sp_)
    ml = cell * 1e6
    ev = {"date": date.today().isoformat(), "method": "physx_pbd_syringe_emitter", "cup": args.cup,
          "spacing_mm": sp_ * 1000, "bore_mm": round(2000 * bore_r, 1), "nozzle_mm": round(2000 * nozzle_r, 1),
          "drawn_mm": round(1000 * (s0 - s_min), 1), "stroke_mm": round(1000 * stroke, 1),
          "expected_ml": round(stroke * area * 1e6, 1), "expelled_ml": round(emitted * ml, 1),
          "peak_nozzle_speed_m_s": round(nozzle_v, 2), "particles": int(emitted),
          "in_cup_ml": round(caught.sum() * ml, 1), "spilled_ml": round((~caught).sum() * ml, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    npz = OUT / f"{args.syringe}_physx_dispense_frames.npz"
    pick = frames[::max(1, len(frames) // 6)][:5] + frames[-1:]
    np.savez(npz, **{f"water{i}": (fr[1] if len(fr[1]) else np.zeros((1, 3)) - 1)[:, [0, 2]]
                     for i, fr in enumerate(pick)},
             **{f"cup{i}": fr[3][:, [0, 2]] for i, fr in enumerate(pick)},
             labels=np.array([f"t={fr[0]:.2f}s plunger {1000 * (fr[2] - s0):+.0f}mm" for fr in pick]))
    ev["side_views"] = str(draw_frames_png(npz, OUT / f"{args.syringe}_physx_dispense{'_nozzle' + format(args.nozzle_mm, 'g') if args.nozzle_mm else ''}.png",
                                           around_cup=True).relative_to(REPO))
    ev["nozzle_source"] = "argument" if args.nozzle_mm else "modelled opening"
    save_evidence(args.syringe, f"dispense_nozzle{args.nozzle_mm:g}" if args.nozzle_mm else "dispense", ev)


if args.cmd == "pour":
    pour()
elif args.cmd == "dispense":
    dispense()
app.close()
