#!/usr/bin/env python3
"""Physically accurate cables/cords: capsule-chain articulations.

Scanned cords are baked into the scan mesh — unusable for physics, same
verdict as scanned cloth. This generates the standard robotics cable
model instead: N capsule links joined by SPHERICAL joints (D6 with the
twist axis locked soft), joint damping for cable stiffness, rubber
material, per-link masses from linear density. The result is a legal
PhysX articulation (ArticulationRootAPI on the root link) the existing
joint-verification machinery understands.

Both ends expose attachment prims (AttachA = root link end, AttachB =
tip link end): FixedJoint them to a soldering iron body and a plug body
and the tool hangs from a cord that bends, swings, and drapes.

`compose` builds exactly that: iron asset + cable + plug asset as one
assembly, ends welded, ready to drop in a scene.

Usage:
    python scripts/make_cable.py cable --length 1.2 --radius 0.004
    python scripts/make_cable.py compose --iron workspace/assets_fixed/soldering_iron_simready.usda \\
        --plug workspace/assets_fixed/power_plug_european_simready.usda
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT_DIR = REPO / "workspace" / "generated_cables"

RUBBER_DENSITY = 1200.0  # kg/m3, cable jacket


def build_cable(out_path: Path, length_m: float = 1.2, radius_m: float = 0.004,
                links: int = 24, sag: bool = True) -> Path:
    """Capsule-chain cable along +X, root at origin. `sag` lays the chain
    out in a shallow catenary so it starts near its rest shape instead of
    a rigid bar."""
    import math

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    seg = length_m / links
    link_mass = RUBBER_DENSITY * math.pi * radius_m ** 2 * seg

    out_path.parent.mkdir(parents=True, exist_ok=True)
    layer = Sdf.Layer.Find(str(out_path))
    if layer:
        layer.Clear()
        stage = Usd.Stage.Open(layer)
    else:
        if out_path.exists():
            out_path.unlink()
        stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, "/Cable")
    stage.SetDefaultPrim(root.GetPrim())

    # rubber physics material
    mat = stage.DefinePrim("/Cable/PhysicsMaterials/rubber")
    m = UsdPhysics.MaterialAPI.Apply(mat)
    m.CreateStaticFrictionAttr().Set(0.9)
    m.CreateDynamicFrictionAttr().Set(0.8)
    m.CreateRestitutionAttr().Set(0.1)
    m.CreateDensityAttr().Set(RUBBER_DENSITY)

    # link positions: straight or shallow catenary in XZ
    def pos(i: int) -> Gf.Vec3d:
        x = (i + 0.5) * seg
        z = 0.0
        if sag:
            a = length_m * 0.6
            z = -(a * math.cosh((x - length_m / 2) / a)
                  - a * math.cosh(length_m / 2 / a))
        return Gf.Vec3d(x, 0.0, z)

    prev = None
    for i in range(links):
        lp = f"/Cable/link_{i:02d}"
        link = UsdGeom.Xform.Define(stage, lp)
        UsdGeom.XformCommonAPI(link).SetTranslate(pos(i))
        cap = UsdGeom.Capsule.Define(stage, lp + "/geom")
        cap.GetAxisAttr().Set("X")
        cap.GetRadiusAttr().Set(radius_m)
        # full-length capsules: continuous cord look; overlap at joints
        # is harmless with articulation self-collision disabled
        cap.GetHeightAttr().Set(seg)
        cap.GetDisplayColorAttr().Set([(0.05, 0.05, 0.05)])  # rubber black
        UsdPhysics.CollisionAPI.Apply(cap.GetPrim())
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        UsdPhysics.MassAPI.Apply(link.GetPrim()).CreateMassAttr().Set(
            round(link_mass, 6))
        rel = link.GetPrim().CreateRelationship(
            "physics:materialBinding", custom=False)
        rel.SetTargets([Sdf.Path("/Cable/PhysicsMaterials/rubber")])
        if i == 0:
            UsdPhysics.ArticulationRootAPI.Apply(link.GetPrim())
            # wheelchair lesson: articulation self-collision ON + touching
            # links = contact impulses that ball up or explode the chain
            link.GetPrim().CreateAttribute(
                "physxArticulation:enabledSelfCollisions",
                Sdf.ValueTypeNames.Bool).Set(False)
        else:
            j = UsdPhysics.SphericalJoint.Define(
                stage, f"/Cable/joints/j_{i:02d}")
            j.CreateBody0Rel().SetTargets([Sdf.Path(prev)])
            j.CreateBody1Rel().SetTargets([Sdf.Path(lp)])
            # anchor at the shared end of the two capsules, in each
            # body's local frame
            p0 = pos(i - 1)
            p1 = pos(i)
            mid = (p0 + p1) / 2.0
            j.CreateLocalPos0Attr().Set(Gf.Vec3f(*(mid - p0)))
            j.CreateLocalPos1Attr().Set(Gf.Vec3f(*(mid - p1)))
            j.CreateAxisAttr().Set("X")
            # cable bend: a real jacket can't fold 40 deg every 4 cm —
            # generous cones let the chain ACCORDION into a zigzag ball
            # (measured: 1.0 m cord compacted to 0.25 m span). 15 deg per
            # joint is still ~360 deg of total flex over the chain.
            j.CreateConeAngle0LimitAttr().Set(15.0)
            j.CreateConeAngle1LimitAttr().Set(15.0)
            # rubber jackets damp fast — undamped the assembly is a
            # perpetual pendulum and solver noise slowly ADDS energy
            # scaled to link gravity torque (~5e-4 N*m for gram links);
            # 0.02 made the cord rigid under its own weight
            j.GetPrim().CreateAttribute(
                "physxJoint:jointFriction",
                Sdf.ValueTypeNames.Float).Set(round(link_mass * 9.81 * seg * 0.15, 6))
        prev = lp

    # attachment points for composing with tools/plugs
    a = UsdGeom.Xform.Define(stage, "/Cable/link_00/AttachA")
    UsdGeom.XformCommonAPI(a).SetTranslate(Gf.Vec3d(-seg / 2, 0, 0))
    b = UsdGeom.Xform.Define(stage, f"/Cable/link_{links-1:02d}/AttachB")
    UsdGeom.XformCommonAPI(b).SetTranslate(Gf.Vec3d(seg / 2, 0, 0))

    stage.GetRootLayer().customLayerData = {
        "cable": {"length_m": length_m, "radius_m": radius_m,
                  "links": links, "link_mass_kg": round(link_mass, 6)}}
    stage.GetRootLayer().Save()
    return out_path


def compose(iron: str, plug: str, out_path: Path,
            length_m: float = 1.0, radius_m: float = 0.004,
            links: int = 24) -> Path:
    """Iron + cable + plug as ONE fixed-base-able articulation rooted at
    the plug. Physics runs on clean UNSCALED proxy bodies (joint frames
    through scaled scan wrappers are treacherous — that instability cost
    three exploding dangle tests); the scan meshes ride along as pure
    visuals with no physics APIs. Pass the QUEUE simready wrappers — the
    raw scan sources and scales are read from their queue entries when
    available, else the wrapper is used as visual directly."""
    import json as _json

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    cable_file = build_cable(OUT_DIR / "_compose_cable.usda",
                             length_m, radius_m, links, sag=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    layer = Sdf.Layer.Find(str(out_path))
    if layer:
        layer.Clear()
        stage = Usd.Stage.Open(layer)
    else:
        if out_path.exists():
            out_path.unlink()
        stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    def _dims(path: str):
        st = Usd.Stage.Open(path)
        rng = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
        mpu = UsdGeom.GetStageMetersPerUnit(st)
        if rng.IsEmpty():
            return [0.1, 0.05, 0.05], Gf.Vec3d(0, 0, 0)
        s = rng.GetSize()
        mid = Gf.Vec3d(rng.GetMidpoint())
        return ([s[0] * mpu, s[1] * mpu, s[2] * mpu],
                Gf.Vec3d(mid[0] * mpu, mid[1] * mpu, mid[2] * mpu))

    def _proxy(name: str, wrapper: str, at: Gf.Vec3d, mass: float) -> str:
        """Unscaled rigid proxy box + the wrapper as physics-free visual."""
        pp = f"/World/{name}"
        prim = stage.DefinePrim(pp, "Xform")
        UsdGeom.XformCommonAPI(prim).SetTranslate(at)
        UsdPhysics.RigidBodyAPI.Apply(prim)
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr().Set(mass)
        dims, mid = _dims(wrapper)
        box = UsdGeom.Cube.Define(stage, pp + "/collider")
        box.GetSizeAttr().Set(1.0)
        bx = UsdGeom.XformCommonAPI(box.GetPrim())
        bx.SetScale(Gf.Vec3f(*(max(d, 0.01) for d in dims)))
        bx.SetTranslate(mid)
        UsdPhysics.CollisionAPI.Apply(box.GetPrim())
        box.GetPrim().CreateAttribute(
            "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        UsdGeom.Imageable(box.GetPrim()).MakeInvisible()
        vis = stage.DefinePrim(pp + "/visual", "Xform")
        src = Usd.Stage.Open(wrapper)
        default = src.GetDefaultPrim()
        if default:
            vis.GetReferences().AddReference(wrapper, str(default.GetPath()))
        else:
            vis.GetReferences().AddReference(wrapper)
        # strip physics from the visual subtree so only the proxy simulates
        for p in Usd.PrimRange(stage.GetPrimAtPath(pp + "/visual")):
            for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI",
                        "PhysicsMeshCollisionAPI", "PhysicsMassAPI",
                        "PhysicsArticulationRootAPI"):
                if api in [str(s) for s in p.GetAppliedSchemas()]:
                    p.RemoveAppliedSchema(api)
        return pp

    plug_dims, _ = _dims(plug)
    iron_dims, _ = _dims(iron)
    seg = length_m / links
    x_plug = 0.0
    x_cable = plug_dims[0] / 2 + 0.01
    x_iron = x_cable + length_m + iron_dims[0] / 2 + 0.01

    plug_body = _proxy("Plug", plug, Gf.Vec3d(x_plug, 0, 0), 0.05)
    cable_prim = stage.DefinePrim("/World/Cord", "Xform")
    csrc = Usd.Stage.Open(str(cable_file))
    cable_prim.GetReferences().AddReference(
        str(cable_file), str(csrc.GetDefaultPrim().GetPath()))
    UsdGeom.XformCommonAPI(cable_prim).SetTranslate(Gf.Vec3d(x_cable, 0, 0))
    iron_body = _proxy("Iron", iron, Gf.Vec3d(x_iron, 0, 0), 0.12)

    cache = UsdGeom.XformCache()

    def _local(body_path: str, world_point: Gf.Vec3d) -> Gf.Vec3f:
        inv = cache.GetLocalToWorldTransform(
            stage.GetPrimAtPath(body_path)).GetInverse()
        return Gf.Vec3f(*inv.Transform(world_point))

    weld_a = Gf.Vec3d(x_cable, 0, 0)
    weld_b = Gf.Vec3d(x_cable + length_m, 0, 0)
    j1 = UsdPhysics.FixedJoint.Define(stage, "/World/joints/plug_to_cord")
    j1.CreateBody0Rel().SetTargets([Sdf.Path(plug_body)])
    j1.CreateBody1Rel().SetTargets([Sdf.Path("/World/Cord/link_00")])
    j1.CreateLocalPos0Attr().Set(_local(plug_body, weld_a))
    j1.CreateLocalPos1Attr().Set(_local("/World/Cord/link_00", weld_a))
    tip = f"/World/Cord/link_{links-1:02d}"
    j2 = UsdPhysics.FixedJoint.Define(stage, "/World/joints/cord_to_iron")
    j2.CreateBody0Rel().SetTargets([Sdf.Path(tip)])
    j2.CreateBody1Rel().SetTargets([Sdf.Path(iron_body)])
    j2.CreateLocalPos0Attr().Set(_local(tip, weld_b))
    j2.CreateLocalPos1Attr().Set(_local(iron_body, weld_b))

    # one articulation, rooted at the plug (the anchorable end)
    stage.GetPrimAtPath("/World/Cord/link_00").RemoveAppliedSchema(
        "PhysicsArticulationRootAPI")
    root_prim = stage.GetPrimAtPath(plug_body)
    UsdPhysics.ArticulationRootAPI.Apply(root_prim)
    root_prim.CreateAttribute(
        "physxArticulation:enabledSelfCollisions",
        Sdf.ValueTypeNames.Bool).Set(False)

    UsdPhysics.Scene.Define(stage, Sdf.Path("/PhysicsScene"))
    stage.GetRootLayer().Save()
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["cable", "compose"])
    ap.add_argument("--length", type=float, default=1.2)
    ap.add_argument("--radius", type=float, default=0.004)
    ap.add_argument("--links", type=int, default=24)
    ap.add_argument("--iron")
    ap.add_argument("--plug")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.mode == "cable":
        out = Path(args.out) if args.out else OUT_DIR / "cable.usda"
        print(f"built {build_cable(out, args.length, args.radius, args.links)}")
    else:
        if not (args.iron and args.plug):
            print("compose needs --iron and --plug")
            return 1
        out = (Path(args.out) if args.out
               else OUT_DIR / "soldering_iron_with_cord.usda")
        print(f"built {compose(args.iron, args.plug, out, args.length, args.radius, args.links)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
