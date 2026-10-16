#!/usr/bin/env python3
"""A towel: a rectangular cotton cloth as a PhysX surface deformable (the
OmniPhysics deformable schema Isaac Sim 6 carries), authored with pxr the
way soft_body_parts.py authors a soft part - a body Xform with the
deformable APIs, the render mesh as the cooking source and bind pose, an
empty simMesh PhysX fills at load, a cotton surface material.

    python scripts/make_towel.py [--size 0.30 0.40] [--res 0.02] [--out workspace/asset_library/towel_cotton/towel_cotton.usda]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))


def main() -> int:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

    from soft_body_parts import SOFT_PRESETS, _bind_pose, _soft_material

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=float, nargs=2, default=[0.30, 0.40], help="x, y extent in metres")
    ap.add_argument("--res", type=float, default=0.02, help="grid cell size in metres")
    ap.add_argument("--preset", default="fabric_cotton")
    ap.add_argument("--iterations", type=int, default=64,
                    help="solver position iterations (verify_cloth_friction.py, 2026-10-15: at 16 a 10 cm flap over a slab edge "
                         "crept 7 mm in 30 s; at 64 it held with zero slide and the least jitter)")
    ap.add_argument("--linear-damping", type=float, default=0.0,
                    help="vertex velocity damping (1/s), air drag's stand-in: PhysX has none, and a free-hanging flap swings "
                         "undamped for as long as it hangs, which drives the cloth off a table edge (verify_cloth_friction.py)")
    ap.add_argument("--rest-offset", type=float, default=None, help="the cloth's rest distance from rigid surfaces (default: half its thickness)")
    ap.add_argument("--contact-offset", type=float, default=None, help="where cloth-rigid contacts are generated (default: rest offset + 2 mm)")
    ap.add_argument("--out", default=str(REPO / "workspace/asset_library/towel_cotton/towel_cotton.usda"))
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sx, sy = args.size
    nx, ny = int(round(sx / args.res)), int(round(sy / args.res))
    stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Towel")
    stage.SetDefaultPrim(root.GetPrim())
    body = UsdGeom.Xform.Define(stage, "/Towel/Body")
    # the cloth: a grid of quads, centred, lying in z = 0
    pts = [Gf.Vec3f(-sx / 2 + i * sx / nx, -sy / 2 + j * sy / ny, 0.0) for j in range(ny + 1) for i in range(nx + 1)]
    counts, idx = [], []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            idx += [a, a + 1, a + nx + 2, a + nx + 1]
            counts.append(4)
    mesh = UsdGeom.Mesh.Define(stage, "/Towel/Body/towel")
    mesh.CreatePointsAttr(Vt.Vec3fArray(pts))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(idx))
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.55, 0.72, 0.85)]))
    mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(Vt.Vec3fArray(pts)))
    _bind_pose(mesh.GetPrim())
    # the simulation mesh PhysX cooks from the render mesh
    sim = UsdGeom.Mesh.Define(stage, "/Towel/Body/simMesh")
    sim.GetPrim().AddAppliedSchema("OmniPhysicsSurfaceDeformableSimAPI")
    UsdPhysics.CollisionAPI.Apply(sim.GetPrim())
    # the cloth's distance from rigid surfaces: PhysX's default contact offset is
    # 2 cm, and a surface deformable RESTS there - the towel floated 2 cm above
    # the table and 2 cm off the fingertips, so a pinch closed on nothing
    # (measured 2026-10-15). Rest at half the fabric's thickness, contacts
    # generated 2 mm further out.
    preset = dict(SOFT_PRESETS[args.preset])
    rest = args.rest_offset if args.rest_offset is not None else float(preset["thickness_m"]) / 2
    contact = args.contact_offset if args.contact_offset is not None else rest + 0.002
    sim.GetPrim().AddAppliedSchema("PhysxCollisionAPI")
    sim.GetPrim().CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(rest)
    sim.GetPrim().CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(contact)
    sim.CreatePurposeAttr(UsdGeom.Tokens.guide)
    sim.GetPrim().AddAppliedSchema("OmniPhysicsDeformablePoseAPI:default")
    sim.GetPrim().CreateAttribute("deformablePose:default:omniphysics:purposes", Sdf.ValueTypeNames.TokenArray).Set(["bindPose"])
    bp = body.GetPrim()
    bp.AddAppliedSchema("PhysxAutoDeformableBodyAPI")
    bp.CreateAttribute("physxDeformableBody:autoDeformableBodyEnabled", Sdf.ValueTypeNames.Bool).Set(True)
    bp.CreateRelationship("physxDeformableBody:cookingSourceMesh").SetTargets([mesh.GetPath()])
    bp.AddAppliedSchema("PhysxAutoDeformableMeshSimplificationAPI")
    bp.CreateAttribute("physxDeformableBody:autoDeformableMeshSimplificationEnabled", Sdf.ValueTypeNames.Bool).Set(True)
    bp.CreateAttribute("physxDeformableBody:remeshingEnabled", Sdf.ValueTypeNames.Bool).Set(True)
    bp.CreateAttribute("physxDeformableBody:targetTriangleCount", Sdf.ValueTypeNames.UInt).Set(2 * nx * ny)
    bp.AddAppliedSchema("OmniPhysicsDeformableBodyAPI")
    bp.CreateAttribute("omniphysics:deformableBodyEnabled", Sdf.ValueTypeNames.Bool).Set(True)
    bp.AddAppliedSchema("PhysxSurfaceDeformableBodyAPI")
    bp.CreateAttribute("physxDeformableBody:selfCollision", Sdf.ValueTypeNames.Bool).Set(True)
    bp.CreateAttribute("physxDeformableBody:enableSpeculativeCCD", Sdf.ValueTypeNames.Bool).Set(True)
    bp.CreateAttribute("physxDeformableBody:solverPositionIterationCount", Sdf.ValueTypeNames.UInt).Set(int(args.iterations))
    bp.CreateAttribute("physxDeformableBody:linearDamping", Sdf.ValueTypeNames.Float).Set(float(args.linear_damping))
    bp.CreateAttribute("physxDeformableBody:collisionPairUpdateFrequency", Sdf.ValueTypeNames.UInt).Set(4)
    bp.CreateAttribute("physxDeformableBody:collisionIterationMultiplier", Sdf.ValueTypeNames.UInt).Set(4)
    mat = _soft_material(stage, "/Towel/Materials/Cotton", preset, 1.0)
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                                                           materialPurpose="physics")
    UsdShade.MaterialBindingAPI.Apply(sim.GetPrim()).Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                                                          materialPurpose="physics")
    root.GetPrim().SetCustomDataByKey("simReady:cloth", json.dumps({"size_m": [sx, sy], "res_m": args.res, "preset": args.preset,
                                                                    "preset_values": preset, "cells": [nx, ny],
                                                                    "rest_offset_m": rest, "contact_offset_m": contact,
                                                                    "solver_iterations": int(args.iterations),
                                                                    "linear_damping_per_s": float(args.linear_damping)}))
    stage.GetRootLayer().Save()
    print(f"wrote {out}: {nx}x{ny} cells, {len(pts)} points, preset {args.preset} {preset}, rest offset {rest * 1000:.2f} mm, contact offset {contact * 1000:.2f} mm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
