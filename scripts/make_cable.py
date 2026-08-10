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
    # physical linear-density mass (~2.5 g for 4 mm cord segments) is
    # SOLVER-HOSTILE: the iterative solver cannot converge 24 joint
    # constraints between gram-scale links carrying a 100 g tool — the
    # chain crumples and climbs. 15 g links are stable; recorded in
    # customLayerData so consumers know the fudge.
    physical_mass = RUBBER_DENSITY * math.pi * radius_m ** 2 * seg
    link_mass = max(physical_mass, 0.015)

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
        link.GetPrim().CreateAttribute(
            "physxRigidBody:solverPositionIterationCount",
            Sdf.ValueTypeNames.Int).Set(64)
        link.GetPrim().CreateAttribute(
            "physxRigidBody:solverVelocityIterationCount",
            Sdf.ValueTypeNames.Int).Set(8)
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
            # D6 joint: omni.physx IGNORES SphericalJoint cone limits
            # inside articulations (15 deg authored -> 90 deg+ observed
            # folds). A generic Joint with per-axis LimitAPIs and
            # rotational stiffness/damping DRIVES is bending stiffness
            # the solver actually enforces.
            j = UsdPhysics.Joint.Define(stage, f"/Cable/joints/j_{i:02d}")
            j.CreateBody0Rel().SetTargets([Sdf.Path(prev)])
            j.CreateBody1Rel().SetTargets([Sdf.Path(lp)])
            p0 = pos(i - 1)
            p1 = pos(i)
            mid = (p0 + p1) / 2.0
            j.CreateLocalPos0Attr().Set(Gf.Vec3f(*(mid - p0)))
            j.CreateLocalPos1Attr().Set(Gf.Vec3f(*(mid - p1)))
            prim = j.GetPrim()
            for dof in ("transX", "transY", "transZ"):
                lim = UsdPhysics.LimitAPI.Apply(prim, dof)
                lim.CreateLowAttr().Set(1.0)    # low > high = locked
                lim.CreateHighAttr().Set(-1.0)
            for dof, ang in (("rotX", 20.0), ("rotY", 25.0), ("rotZ", 25.0)):
                lim = UsdPhysics.LimitAPI.Apply(prim, dof)
                lim.CreateLowAttr().Set(-ang)
                lim.CreateHighAttr().Set(ang)
                drv = UsdPhysics.DriveAPI.Apply(prim, dof)
                drv.CreateTypeAttr().Set("force")
                drv.CreateTargetPositionAttr().Set(0.0)  # wants straight
                # bend stiffness: gentle spring toward straight (angular
                # drives are in DEGREES in omni.physx)
                # Bend stiffness must hold the cord's shape when it is
                # SLACK. Under tension (tool hanging) a whisper suffices;
                # a cord lying loose on a table coils up without real
                # stiffness. Scaled to link weight so it stays physical.
                drv.CreateStiffnessAttr().Set(round(link_mass * 4.0, 5))
                drv.CreateDampingAttr().Set(round(link_mass * 0.8, 5))
        prev = lp

    # attachment points for composing with tools/plugs
    a = UsdGeom.Xform.Define(stage, "/Cable/link_00/AttachA")
    UsdGeom.XformCommonAPI(a).SetTranslate(Gf.Vec3d(-seg / 2, 0, 0))
    b = UsdGeom.Xform.Define(stage, f"/Cable/link_{links-1:02d}/AttachB")
    UsdGeom.XformCommonAPI(b).SetTranslate(Gf.Vec3d(seg / 2, 0, 0))

    stage.GetRootLayer().customLayerData = {
        "cable": {"length_m": length_m, "radius_m": radius_m,
                  "links": links, "link_mass_kg": round(link_mass, 6),
                  "physical_mass_kg": round(physical_mass, 6),
                  "note": "link mass floored at 15 g for solver stability"}}
    stage.GetRootLayer().Save()
    return out_path


def attach_frame(usd_path: str, end: str = "auto"):
    """Where a cord actually enters an asset, and which way it points.

    Returns (point, direction) in the asset's own space (meters): the
    cord-entry point on the surface and the outward axis at that end.

    Heuristic: cords enter at the FAT end of the principal axis — a
    soldering iron's tip is thin and its handle thick; a plug's pins are
    thin and its body thick. Cross-section area of the outer slab at each
    end decides. `end` may be forced to 'min'/'max' along the principal
    axis when the heuristic guesses wrong.
    """
    import numpy as np
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.Open(usd_path)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    cache = UsdGeom.XformCache()
    pts = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        raw = UsdGeom.Mesh(prim).GetPointsAttr().Get()
        if not raw:
            continue
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=float)
        pts.append((np.array(raw, dtype=float) @ m[:3, :3] + m[3, :3]) * mpu)
    if not pts:
        return Gf.Vec3d(0, 0, 0), Gf.Vec3d(1, 0, 0)
    P = np.concatenate(pts)
    lo, hi = P.min(axis=0), P.max(axis=0)
    axis = int(np.argmax(hi - lo))
    span = float(hi[axis] - lo[axis]) or 1e-6
    perp = [i for i in range(3) if i != axis]

    def _slab(at_max: bool):
        cut = (hi[axis] - 0.15 * span) if at_max else (lo[axis] + 0.15 * span)
        sel = P[P[:, axis] >= cut] if at_max else P[P[:, axis] <= cut]
        if len(sel) == 0:
            sel = P
        w = sel[:, perp].max(axis=0) - sel[:, perp].min(axis=0)
        return sel, float(w[0] * w[1])

    slab_max, area_max = _slab(True)
    slab_min, area_min = _slab(False)
    at_max = area_max >= area_min if end == "auto" else (end == "max")
    slab = slab_max if at_max else slab_min
    point = slab.mean(axis=0)
    # sit the anchor ON the end face, not inside the slab
    point[axis] = hi[axis] if at_max else lo[axis]
    # Direction from the LOCAL geometry, not the bbox axis: a strain-relief
    # boot curves away from the tool axis, and a cord welded along the bbox
    # axis meets it at a visible angle (the critic's "two unrelated parts
    # crossing"). PCA of the end slab gives the boot's own axis.
    centred = slab - slab.mean(axis=0)
    if len(slab) >= 8:
        _, _, vecs = np.linalg.svd(centred, full_matrices=False)
        d = vecs[0]
    else:
        d = np.zeros(3)
        d[axis] = 1.0
    outward = 1.0 if at_max else -1.0
    if d[axis] * outward < 0:          # point it out of the asset
        d = -d
    n = float(np.linalg.norm(d)) or 1.0
    return Gf.Vec3d(*point), Gf.Vec3d(*(d / n))


def strip_baked_cord(usd_path: str) -> int:
    """Deactivate baked cord geometry (meshes named wire/cable/cord/lead)
    in a derivative wrapper, in place. Returns how many were removed.

    Scanned corded tools ship their cord sprawled across the scan — it
    dominates the bounding box (which wrecks auto-scaling) and fights any
    cord we route. Deactivating leaves the tool intact and re-measurable.
    """
    import re as _re

    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(usd_path)
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        if _re.search(r"wire|cable|cord|lead", prim.GetName(), _re.I):
            prim.SetActive(False)
            n += 1
    if n:
        stage.GetRootLayer().Save()
    del stage
    return n


def route_cord(stage, root_path: str, p0, d0, p1, d1,
               length_m: float, radius_m: float, segments: int = 40,
               ground_z: float | None = None):
    """A STATIC routed cord: a smooth curve leaving p0 along d0 and
    arriving at p1 from direction d1 (d1 points back toward p0), arc-length
    matched to `length_m` so the slack shows as droop, sampled into capsule
    colliders.

    Why static: a dynamic cable only earns its cost when the robot
    MANIPULATES it. For scene dressing — a lamp plugged into the wall,
    an iron on a bench — what matters is that the cord looks right,
    occupies space, and can be collided with. The dynamic capsule chain
    stays available (cord_mode='dynamic') for grasping work; it needs
    tension to hold its shape and coils when slack.
    """
    import math

    from pxr import Gf, Sdf, UsdGeom, UsdPhysics

    p0 = Gf.Vec3d(*p0)
    p1 = Gf.Vec3d(*p1)
    d0 = Gf.Vec3d(*d0).GetNormalized()
    d1 = Gf.Vec3d(*d1).GetNormalized()
    span = (p1 - p0).GetLength() or 1e-6
    slack = max(0.0, length_m - span)

    # slack cord lying on a surface meanders — a dead-straight run reads
    # as a rod. Push the control points to opposite sides of the run.
    run = (p1 - p0)
    side = Gf.Vec3d(-run[1], run[0], 0.0)
    side = side.GetNormalized() if side.GetLength() > 1e-6 else Gf.Vec3d(0, 1, 0)
    # keep the sideways bulge a fraction of the RUN, not of the slack —
    # unbounded it turns a short cord into a curly telephone cable
    meander = side * min(slack * 0.35, span * 0.12)

    def curve(handle: float, sag: float):
        c0 = p0 + d0 * handle + Gf.Vec3d(0, 0, -sag) + meander
        c1 = p1 + d1 * handle + Gf.Vec3d(0, 0, -sag) - meander
        pts = []
        for i in range(segments + 1):
            t = i / segments
            u = 1.0 - t
            b = (p0 * (u ** 3) + c0 * (3 * u * u * t)
                 + c1 * (3 * u * t * t) + p1 * (t ** 3))
            if ground_z is not None and b[2] < ground_z:
                b = Gf.Vec3d(b[0], b[1], ground_z)   # cords rest on surfaces
            pts.append(b)
        return pts

    def arclen(pts):
        return sum((pts[i + 1] - pts[i]).GetLength() for i in range(len(pts) - 1))

    # grow handle+sag until the curve is as long as the cord really is
    lo, hi = 0.0, max(span, length_m) * 1.5
    pts = curve(span * 0.3, slack * 0.5)
    for _ in range(24):
        mid = 0.5 * (lo + hi)
        pts = curve(mid, slack * 0.55 + mid * 0.15)
        if arclen(pts) < length_m:
            lo = mid
        else:
            hi = mid
    UsdGeom.Xform.Define(stage, root_path)
    mat_path = root_path + "/rubber"
    mat = stage.DefinePrim(mat_path)
    m = UsdPhysics.MaterialAPI.Apply(mat)
    m.CreateStaticFrictionAttr().Set(0.9)
    m.CreateDynamicFrictionAttr().Set(0.8)
    m.CreateRestitutionAttr().Set(0.05)
    m.CreateDensityAttr().Set(RUBBER_DENSITY)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = (b - a).GetLength()
        if seg < 1e-6:
            continue
        sp = f"{root_path}/seg_{i:03d}"
        cap = UsdGeom.Capsule.Define(stage, sp)
        cap.GetAxisAttr().Set("X")
        cap.GetRadiusAttr().Set(radius_m)
        cap.GetHeightAttr().Set(seg)
        cap.GetDisplayColorAttr().Set([(0.05, 0.05, 0.05)])
        xf = UsdGeom.Xformable(cap.GetPrim())
        xf.ClearXformOpOrder()
        rot = Gf.Rotation(Gf.Vec3d(1, 0, 0), (b - a).GetNormalized())
        mtx = Gf.Matrix4d().SetRotate(rot)
        mtx.SetTranslateOnly((a + b) * 0.5)
        xf.AddTransformOp().Set(mtx)
        UsdPhysics.CollisionAPI.Apply(cap.GetPrim())
        rel = cap.GetPrim().CreateRelationship(
            "physics:materialBinding", custom=False)
        rel.SetTargets([Sdf.Path(mat_path)])
    return arclen(pts)


def compose(iron: str, plug: str, out_path: Path,
            length_m: float = 1.0, radius_m: float = 0.004,
            links: int = 24, tool_attach: str = "auto",
            plug_attach: str = "auto", upright: bool = False,
            cord_mode: str = "routed", strip_cord: bool = True) -> Path:
    """Iron + cable + plug as ONE fixed-base-able articulation rooted at
    the plug. Physics runs on clean UNSCALED proxy bodies (joint frames
    through scaled scan wrappers are treacherous — that instability cost
    three exploding dangle tests); the scan meshes ride along as pure
    visuals with no physics APIs. Pass the QUEUE simready wrappers — the
    raw scan sources and scales are read from their queue entries when
    available, else the wrapper is used as visual directly."""
    import json as _json

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    if strip_cord:
        # a tool that already has a baked cord would otherwise wear two
        strip_baked_cord(iron)
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

    def _proxy(name: str, wrapper: str, xform: Gf.Matrix4d,
               mass: float) -> str:
        """Unscaled rigid proxy box + the wrapper as physics-free visual."""
        pp = f"/World/{name}"
        prim = stage.DefinePrim(pp, "Xform")
        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(xform)
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

    # Attach frames: rotate each asset so its CORD-ENTRY end faces the
    # cord, then translate so that entry point sits exactly on the cord
    # end. Bbox-corner welding put the cord on the wrong faces entirely.
    plug_pt, plug_dir = attach_frame(plug, plug_attach)
    iron_pt, iron_dir = attach_frame(iron, tool_attach)
    x_cable = 0.0
    weld_a = Gf.Vec3d(x_cable, 0, 0)
    weld_b = Gf.Vec3d(x_cable + length_m, 0, 0)

    def _xform_to(point: Gf.Vec3d, direction: Gf.Vec3d,
                  weld: Gf.Vec3d, face: Gf.Vec3d) -> Gf.Matrix4d:
        rot = Gf.Rotation(direction, face)
        m = Gf.Matrix4d().SetRotate(rot)
        m.SetTranslateOnly(weld - m.TransformDir(point))
        return m

    if cord_mode == "routed":
        # Tool keeps its pose; the cord is a static routed curve from its
        # real exit to the plug. No solver, no coiling, always looks right.
        tdims, _ = _dims(iron)
        st = Usd.Stage.Open(iron)
        trng = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
        tmpu = UsdGeom.GetStageMetersPerUnit(st)
        lift = (-float(trng.GetMin()[2]) * tmpu) if upright else 0.0
        tool_x = Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 0, lift))
        iron_body = _proxy("Iron", iron, tool_x, 0.12)
        exit_w = Gf.Vec3d(iron_pt[0], iron_pt[1], iron_pt[2] + lift)
        h = Gf.Vec3d(iron_dir[0], iron_dir[1], 0.0)
        if h.GetLength() < 1e-6:
            h = Gf.Vec3d(1, 0, 0)
        h = h.GetNormalized()
        # plug lies on the surface at ~70% of the cord length away
        plug_at = exit_w + h * (length_m * 0.7)
        plug_at = Gf.Vec3d(plug_at[0], plug_at[1], radius_m * 2)
        plug_body = _proxy("Plug", plug,
                           _xform_to(plug_pt, plug_dir, plug_at, -h), 0.05)
        # d1 is the direction the cord ARRIVES FROM at the plug — i.e.
        # pointing back toward the tool. Passing +h made the curve
        # overshoot past the plug and loop back through it.
        got = route_cord(stage, "/World/Cord", exit_w, iron_dir,
                         plug_at, -h, length_m, radius_m,
                         ground_z=(radius_m if upright else None))
        stage.GetRootLayer().customLayerData = {
            "cord": {"mode": "routed", "requested_m": length_m,
                     "routed_m": round(got, 4), "static": True}}
        UsdPhysics.Scene.Define(stage, Sdf.Path("/PhysicsScene"))
        stage.GetRootLayer().Save()
        return out_path

    cable_prim = stage.DefinePrim("/World/Cord", "Xform")
    csrc = Usd.Stage.Open(str(cable_file))
    cable_prim.GetReferences().AddReference(
        str(cable_file), str(csrc.GetDefaultPrim().GetPath()))

    if upright:
        # A lamp/appliance must KEEP ITS POSE and stand on the ground —
        # rotating it so its cord-exit faces the cord (the hanging-tool
        # convention) tips it on its side. Here the CORD adapts instead:
        # the tool sits upright, and the cord runs from its real exit
        # point to the plug.
        tdims, _ = _dims(iron)
        st = Usd.Stage.Open(iron)
        trng = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
        tmpu = UsdGeom.GetStageMetersPerUnit(st)
        lift = -float(trng.GetMin()[2]) * tmpu          # base onto z=0
        tool_x = Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 0, lift))
        iron_body = _proxy("Iron", iron, tool_x, 0.12)
        exit_w = Gf.Vec3d(iron_pt[0], iron_pt[1], iron_pt[2] + lift)
        # cord leaves horizontally along the exit direction's XY heading
        h = Gf.Vec3d(iron_dir[0], iron_dir[1], 0.0)
        if h.GetLength() < 1e-6:
            h = Gf.Vec3d(1, 0, 0)
        h = h.GetNormalized()
        weld_a = exit_w
        weld_b = exit_w + h * length_m
        # cord: local +X mapped onto the run direction, root at the exit
        cm = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), h))
        cm.SetTranslateOnly(weld_a)
        cxf = UsdGeom.Xformable(cable_prim)
        cxf.ClearXformOpOrder()
        cxf.AddTransformOp().Set(cm)
        # plug at the far end, its entry facing back along the cord
        plug_body = _proxy("Plug", plug,
                           _xform_to(plug_pt, plug_dir, weld_b, -h), 0.05)
    else:
        # hanging tool: plug's entry faces +X (toward the cord), tool's -X
        plug_body = _proxy("Plug", plug,
                           _xform_to(plug_pt, plug_dir, weld_a, Gf.Vec3d(1, 0, 0)),
                           0.05)
        UsdGeom.XformCommonAPI(cable_prim).SetTranslate(Gf.Vec3d(x_cable, 0, 0))
        iron_body = _proxy("Iron", iron,
                           _xform_to(iron_pt, iron_dir, weld_b, Gf.Vec3d(-1, 0, 0)),
                           0.12)

    cache = UsdGeom.XformCache()

    def _local(body_path: str, world_point: Gf.Vec3d) -> Gf.Vec3f:
        inv = cache.GetLocalToWorldTransform(
            stage.GetPrimAtPath(body_path)).GetInverse()
        return Gf.Vec3f(*inv.Transform(world_point))

    # weld_a is the cord ROOT end, weld_b the tip end — which body sits at
    # which end flips between modes, and welding them crossed (as an
    # earlier version did) launches the whole assembly on the first step
    body_a, body_b = ((iron_body, plug_body) if upright
                      else (plug_body, iron_body))
    j1 = UsdPhysics.FixedJoint.Define(stage, "/World/joints/weld_root")
    j1.CreateBody0Rel().SetTargets([Sdf.Path(body_a)])
    j1.CreateBody1Rel().SetTargets([Sdf.Path("/World/Cord/link_00")])
    j1.CreateLocalPos0Attr().Set(_local(body_a, weld_a))
    j1.CreateLocalPos1Attr().Set(_local("/World/Cord/link_00", weld_a))
    tip = f"/World/Cord/link_{links-1:02d}"
    j2 = UsdPhysics.FixedJoint.Define(stage, "/World/joints/weld_tip")
    j2.CreateBody0Rel().SetTargets([Sdf.Path(tip)])
    j2.CreateBody1Rel().SetTargets([Sdf.Path(body_b)])
    j2.CreateLocalPos0Attr().Set(_local(tip, weld_b))
    j2.CreateLocalPos1Attr().Set(_local(body_b, weld_b))

    # The cord's end links START INSIDE the tool/plug proxy colliders (the
    # weld point is on the asset surface, and the proxy is a box around
    # the whole asset). Un-filtered, PhysX resolves that overlap by
    # ejecting the cord — it coils and gets pushed through the floor.
    # Filter the welded pairs; the joints hold them together instead.
    for body, near_links in ((body_a, range(0, min(4, links))),
                             (body_b, range(max(0, links - 4), links))):
        prim = stage.GetPrimAtPath(body)
        api = UsdPhysics.FilteredPairsAPI.Apply(prim)
        rel = api.CreateFilteredPairsRel()
        for i in near_links:
            rel.AddTarget(Sdf.Path(f"/World/Cord/link_{i:02d}"))

    # one articulation, rooted at the plug (the anchorable end)
    stage.GetPrimAtPath("/World/Cord/link_00").RemoveAppliedSchema(
        "PhysicsArticulationRootAPI")
    root_prim = stage.GetPrimAtPath(body_a)  # the anchorable end
    UsdPhysics.ArticulationRootAPI.Apply(root_prim)
    root_prim.CreateAttribute(
        "physxArticulation:enabledSelfCollisions",
        Sdf.ValueTypeNames.Bool).Set(False)

    scene = UsdPhysics.Scene.Define(stage, Sdf.Path("/PhysicsScene"))
    # the convergence package that makes the cord DRAPE instead of
    # crumple (validated live): 240 Hz physics + 64 position iterations
    scene.GetPrim().CreateAttribute(
        "physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt).Set(240)
    root_prim.CreateAttribute(
        "physxArticulation:solverPositionIterationCount",
        Sdf.ValueTypeNames.Int).Set(64)
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
