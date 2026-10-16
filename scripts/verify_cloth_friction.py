#!/usr/bin/env python3
"""Does a cloth hold on a table edge in PhysX? Copies of a cloth asset lie on
identical static mesh slabs, each with a 10 cm flap over the edge, each copy
with a different parameter set; the rig simulates them side by side and
reports which crept off. The dining towel (fabric_cotton, 2026-10-15) held
for 15 s with 1-8 mm of vertex jitter a quarter second, then slid off the
edge in stick-slip bursts: the plan's rest shape was never at rest.
(Kit; the Isaac slot.)

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/verify_cloth_friction.py [--asset ...] [--seconds 30] [--hz 240]
writes workspace/verify/cloth_friction/report.json; prints one CLOTH_HOLD line per variant
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VARIANTS = [
    {"name": "as_built", "why": "the towel as make_towel.py writes it: 16 iterations, pairs every 4 steps, 4 collision iterations, mu 0.6/0.48, rest 0.75 contact 2.75 mm"},
    {"name": "iter64", "solverPositionIterationCount": 64, "why": "more solver iterations: position-based friction drifts less per step"},
    {"name": "pairs1", "collisionPairUpdateFrequency": 1, "collisionIterationMultiplier": 1, "why": "contact pairs refreshed every step (stale pairs let vertices dip in and pop out)"},
    {"name": "mu1", "staticFriction": 1.0, "dynamicFriction": 0.9, "why": "the cloth's own friction at the pads' level"},
    {"name": "band6mm", "restOffset": 0.002, "contactOffset": 0.006, "why": "the demo's 1:3 offset ratio with a wider contact band"},
    {"name": "all", "solverPositionIterationCount": 64, "collisionPairUpdateFrequency": 1, "collisionIterationMultiplier": 1,
     "staticFriction": 1.0, "dynamicFriction": 0.9, "restOffset": 0.002, "contactOffset": 0.006, "why": "every lever at once"},
]

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--asset", default=str(REPO / "workspace/asset_library/towel_cotton/towel_cotton.usda"))
ap.add_argument("--seconds", type=float, default=30.0)
ap.add_argument("--hz", type=int, default=240)
ap.add_argument("--overhang", type=float, default=0.10, help="how far the cloth's near edge hangs past the slab edge")
ap.add_argument("--rim", type=float, default=0.0, help="a raised rim this wide along the edge, the field behind it --step lower (the dining table: 0.025 and 0.006)")
ap.add_argument("--step", type=float, default=0.006)
ap.add_argument("--slab-thickness", type=float, default=0.02,
                help="the slab's thickness: the flap must hang FREE below the edge as it does from the dining table's 2 cm slab - "
                     "against a 0.7 m box face every variant held while the same cloth crept off the real table (2026-10-15)")
ap.add_argument("--variants", default=None, help="a JSON file with the variant list (default: the six above)")
ap.add_argument("--out", default=str(REPO / "workspace/verify/cloth_friction/report.json"))
args = ap.parse_args()
variants = json.loads(Path(args.variants).read_text()) if args.variants else VARIANTS

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt  # noqa: E402

ctx = omni.usd.get_context()
ctx.new_stage()
stage = ctx.get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
scene.CreateGravityMagnitudeAttr(9.81)
sp = scene.GetPrim()
sp.AddAppliedSchema("PhysxSceneAPI")                       # as build_er_scene.py sets a cloth scene
sp.CreateAttribute("physxScene:enableGPUDynamics", Sdf.ValueTypeNames.Bool).Set(True)
sp.CreateAttribute("physxScene:broadphaseType", Sdf.ValueTypeNames.Token).Set("GPU")
sp.CreateAttribute("physxScene:gpuMaxDeformableSurfaceContacts", Sdf.ValueTypeNames.UInt).Set(1 << 21)
sp.CreateAttribute("physxScene:gpuCollisionStackSize", Sdf.ValueTypeNames.UInt).Set(1 << 27)
sp.CreateAttribute("physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(args.hz)

# the table's material (the dining scene's support_physics)
mat = UsdShade.Material.Define(stage, "/World/SlabMaterial")
UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
mat.GetPrim().GetAttribute("physics:staticFriction").Set(1.0)
mat.GetPrim().GetAttribute("physics:dynamicFriction").Set(0.8)

TOP = 0.70
asset = Usd.Stage.Open(args.asset)
size = json.loads(asset.GetDefaultPrim().GetCustomDataByKey("simReady:cloth"))["size_m"]
rest_default = float(json.loads(asset.GetDefaultPrim().GetCustomDataByKey("simReady:cloth")).get("rest_offset_m", 0.00075))


def box_mesh(path, lo, hi):
    """A static triangle-mesh box (the dining table is a triangle mesh, no approximation)."""
    pts = [Gf.Vec3f(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
    # wound counter-clockwise seen from outside (normals out): a deformable
    # collides with a triangle mesh from its front side only - the first run
    # of this rig had the top face wound inward and every cloth fell through
    # it onto the slab's floor
    faces = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr(Vt.Vec3fArray(pts))
    m.CreateFaceVertexCountsAttr(Vt.IntArray([4] * 6))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray([k for f in faces for k in f]))
    UsdPhysics.CollisionAPI.Apply(m.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr("none")
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat, materialPurpose="physics")


def slab(i, x0):
    """The slab, its edge at y = 0: flat, or a rim along the edge with the field behind it a step lower."""
    th = args.slab_thickness
    if args.rim > 0:
        box_mesh(f"/World/Slab_{i}_rim", Gf.Vec3f(x0 - 0.4, -args.rim, TOP - th), Gf.Vec3f(x0 + 0.4, 0.0, TOP))
        box_mesh(f"/World/Slab_{i}_field", Gf.Vec3f(x0 - 0.4, -0.7, TOP - args.step - th), Gf.Vec3f(x0 + 0.4, -args.rim, TOP - args.step))
    else:
        box_mesh(f"/World/Slab_{i}", Gf.Vec3f(x0 - 0.4, -0.7, TOP - th), Gf.Vec3f(x0 + 0.4, 0.0, TOP))


ground = UsdGeom.Mesh.Define(stage, "/World/Ground")
box_mesh("/World/Ground", Gf.Vec3f(-2.0, -2.0, -0.1), Gf.Vec3f(1.5 * len(variants) + 2.0, 2.0, 0.0))
copies = []
for i, v in enumerate(variants):
    x0 = 1.5 * i
    slab(i, x0)
    p = stage.DefinePrim(f"/World/Cloth_{i}", "Xform")
    p.GetReferences().AddReference(args.asset)
    xf = UsdGeom.Xformable(p)
    rest = float(v.get("restOffset", rest_default))
    # the cloth's near edge args.overhang past the slab edge (y = 0), lying at its rest offset
    xf.AddTranslateOp().Set(Gf.Vec3d(x0, args.overhang - size[1] / 2, TOP + rest + 0.0005))
    body = next(q for q in Usd.PrimRange(p) if "PhysxSurfaceDeformableBodyAPI" in q.GetAppliedSchemas())
    sim = next(q for q in Usd.PrimRange(p) if q.GetName() == "simMesh")
    material = next(q for q in Usd.PrimRange(p) if q.IsA(UsdShade.Material))
    for k in ("solverPositionIterationCount", "collisionPairUpdateFrequency", "collisionIterationMultiplier"):
        if k in v:
            body.GetAttribute(f"physxDeformableBody:{k}").Set(int(v[k]))
    for k in ("sleepThreshold", "settlingThreshold", "settlingDamping", "linearDamping", "sleepDamping"):
        if k in v:
            body.CreateAttribute(f"physxDeformableBody:{k}", Sdf.ValueTypeNames.Float).Set(float(v[k]))
    for k in ("restOffset", "contactOffset"):
        if k in v:
            sim.GetAttribute(f"physxCollision:{k}").Set(float(v[k]))
    for k in ("staticFriction", "dynamicFriction"):
        if k in v:
            material.GetAttribute(f"omniphysics:{k}").Set(float(v[k]))
    for k in ("elasticityDamping", "bendDamping"):
        if k in v:
            material.CreateAttribute(f"physxDeformableMaterial:{k}", Sdf.ValueTypeNames.Float).Set(float(v[k]))
    for k in ("surfaceBendStiffness", "surfaceStretchStiffness", "surfaceShearStiffness", "surfaceThickness", "density", "youngsModulus"):
        if k in v:
            material.CreateAttribute(f"omniphysics:{k}", Sdf.ValueTypeNames.Float).Set(float(v[k]))
    contact = float(sim.GetAttribute("physxCollision:contactOffset").Get() or 0.02)
    body.GetAttribute("physxDeformableBody:maxLinearVelocity").Set(contact * args.hz)      # as build_er_scene.py
    mesh = next(q for q in Usd.PrimRange(p) if q.IsA(UsdGeom.Mesh) and q.GetName() != "simMesh")
    copies.append({"variant": v, "prim": p, "sim": sim, "mesh": mesh, "x0": x0})

px = omni.physx.get_physx_interface()
omni.timeline.get_timeline_interface().play()
for _ in range(10):
    app.update()
for _ in range(900):
    app.update()
    if all(UsdGeom.Mesh(c["sim"]).GetPointsAttr().Get() and len(UsdGeom.Mesh(c["sim"]).GetPointsAttr().Get()) > 3 for c in copies):
        break
print("[rig] cooked:", [len(UsdGeom.Mesh(c["sim"]).GetPointsAttr().Get() or []) for c in copies], flush=True)


def points(c):
    M = UsdGeom.XformCache().GetLocalToWorldTransform(c["mesh"])
    return np.array([list(M.Transform(Gf.Vec3d(q))) for q in UsdGeom.Mesh(c["mesh"]).GetPointsAttr().Get()], float)


DT = 1.0 / args.hz
CHUNK = 0.25
t = 0.0
prev = [None] * len(copies)
rows = []
t0 = time.time()
while t < args.seconds - 1e-9:
    for _ in range(int(round(CHUNK * args.hz))):
        px.update_simulation(DT, t)
        t += DT
    px.update_transformations(False, True, False, False)
    app.update()
    row = {"t": round(t, 3)}
    for i, c in enumerate(copies):
        P = points(c)
        over = int((P[:, 1] > 0.0).sum())                     # points past the slab edge: the flap
        row[c["variant"]["name"]] = {"flap_points": over, "lowest_z": round(float(P[:, 2].min()), 4), "highest_z": round(float(P[:, 2].max()), 4),
                                     "hem_y": round(float(P[np.argmin(P[:, 2]), 1]), 4),      # the lowest point's y: + is out past the edge, - under it
                                     "max_move_mm": round(1000 * float(np.linalg.norm(P - prev[i], axis=1).max()), 2) if prev[i] is not None else None,
                                     "centre_y": round(float(P[:, 1].mean()), 4)}
        prev[i] = P
    rows.append(row)
print(f"[rig] {args.seconds} s simulated in {time.time() - t0:.0f} s wall", flush=True)

report = {"asset": args.asset, "hz": args.hz, "seconds": args.seconds, "overhang_m": args.overhang, "rim_m": args.rim, "step_m": args.step,
          "slab_thickness_m": args.slab_thickness, "variants": []}
settle_rows = [r for r in rows if r["t"] >= 3.0]              # the first 3 s: the flap drops and swings
for c in copies:
    n = c["variant"]["name"]
    first, last = settle_rows[0][n], rows[-1][n]
    slide_mm = round(1000 * (first["centre_y"] - last["centre_y"]) * -1, 1)   # +y is over the edge
    jitter = sorted(r[n]["max_move_mm"] for r in settle_rows)
    tail = [r[n]["lowest_z"] for r in rows if r["t"] >= args.seconds - 10.0]
    swing_mm = round(1000 * float(np.std(tail)), 2) if tail else None
    on_slab = first["lowest_z"] < TOP and first["highest_z"] > TOP - 0.01         # lying on the slab, a flap below its edge
    res = {"name": n, "settings": {k: v for k, v in c["variant"].items() if k not in ("name", "why")}, "why": c["variant"].get("why"),
           "slide_mm": slide_mm, "flap_points_3s": first["flap_points"], "flap_points_end": last["flap_points"],
           "lowest_z_3s": first["lowest_z"], "lowest_z_end": last["lowest_z"], "highest_z_3s": first["highest_z"], "on_slab": on_slab,
           "jitter_mm_per_quarter_s": {"median": jitter[len(jitter) // 2], "p90": jitter[int(0.9 * len(jitter))], "max": jitter[-1]},
           "swing_mm_last10s": swing_mm, "hem_y_end": last["hem_y"],
           "held": bool(on_slab and abs(slide_mm) <= 5.0 and last["flap_points"] - first["flap_points"] <= 8)}
    report["variants"].append(res)
    print(f"CLOTH_HOLD {n}: {'HELD' if res['held'] else ('SLID' if on_slab else 'FELL THROUGH')} slide {slide_mm:+.1f} mm, flap {first['flap_points']}->{last['flap_points']} points, "
          f"lowest z {first['lowest_z']:.4f}->{last['lowest_z']:.4f}, jitter median {res['jitter_mm_per_quarter_s']['median']} p90 "
          f"{res['jitter_mm_per_quarter_s']['p90']} mm/0.25s, swing {swing_mm} mm, hem y {last['hem_y']:+.4f} (edge at 0)", flush=True)
report["trajectory"] = rows
out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=1))
print("CLOTH_FRICTION_REPORT", out, flush=True)
app.close()
