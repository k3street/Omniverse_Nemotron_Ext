#!/usr/bin/env python3
"""The pill-packing kit, generated as sim-ready USD: tablets, an openable pill
vial and a pair of tweezers - the parts the user's task (2026-10-17: a pill
bottle packed or unpacked one pill at a time) needs and the library lacked
(its pill bottles are one rigid body, their pills fused inside; its tweezers
one rigid piece).

  tablet      a round tablet 10 mm across, 4 mm thick, 0.40 g; continuous
              collision detection (a few-mm body dropped into a vial)
  pill_vial   a 30-dram pharmacy vial: 41 mm across, 91 mm tall, a 1.2 mm
              polypropylene wall - its inside a real cavity (the wall an
              annulus of exact convex pieces on a floor); its cap a separate
              body, 44 mm across and 12 mm tall
  tweezers    115 mm stainless tweezers: a root block and two arms, each arm
              on a hinge at the root whose spring is the arm's bending
              stiffness (a cantilever: k_theta = 3EI/L), the tips 12 mm apart
              at rest

    make_pill_kit.py [--out workspace/asset_library]
writes <out>/<id>/<id>.usda and <id>.json (dimensions, masses, the spring, sources).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SOURCES = {
    "tablet_size": "FDA guidance 'Size, Shape, and Other Physical Attributes of Generic Tablets and Capsules' (Rev. 1, 2022): a tablet's "
                   "largest dimension should not exceed 22 mm (https://fda.gov/media/161902/download); 10 x 4 mm is a common round tablet (reasoned)",
    "tablet_density": "a pressed tablet ~1.25 g/cm3 (reasoned: microcrystalline-cellulose compacts 1.1-1.4)",
    "vial": "pharmacy vial 30 dram: 91 mm tall without its cap, 41 mm across (Fisher Scientific "
            "https://www.fishersci.com/shop/products/vial-30-dram-bluesafe-cap/502643423); polypropylene 905 kg/m3; wall 1.2 mm (reasoned)",
    "cap_thread": "GPI '400' continuous-thread finish: one turn of thread (DWK lab guide https://www.dwk.com/knowledge-hub/blogs/"
                  "screw-thread-closures-and-bottle-neck-finishes-a-laboratory-selection-guide); a 38-400 cap 11.7 mm tall, 37.9 mm across (Comar)",
    "tweezers": "115 mm stainless tweezers weigh 15-20 g (RS PRO / Distrelec listings); arms 9 mm wide, 1.0 mm thick (reasoned: the mass "
                "the listings give); AISI 304 E = 193 GPa, 8000 kg/m3 (Engineering Toolbox)",
}

TABLET = {"d": 0.010, "t": 0.004, "density": 1250.0}
VIAL = {"r_out": 0.0205, "wall": 0.0012, "h": 0.091, "floor": 0.0015, "sides": 24, "density": 905.0,
        "cap_r_out": 0.022, "cap_wall": 0.0013, "cap_h": 0.012, "cap_top": 0.002}
TWEEZERS = {"length": 0.115, "root": 0.015, "width": 0.009, "thick": 0.0010, "tip_len": 0.025, "tip_width": 0.003,
            "tip_gap": 0.012, "E": 193e9, "density": 8000.0}


HINGE_OFF = TWEEZERS["thick"] / 2 + 0.0002                    # each arm's hinge off the midline (over the root block's face)


def tweezer_spring() -> dict:
    """Each arm's hinge spring from its bending stiffness (a cantilever of the flexing length)."""
    tw = TWEEZERS
    L = tw["length"] - tw["root"]
    I = tw["width"] * tw["thick"] ** 3 / 12.0
    k_tip = 3.0 * tw["E"] * I / L ** 3                        # N/m at the tip
    k_theta = 3.0 * tw["E"] * I / L                           # N m/rad at the root (k_tip L^2)
    # the gap between the tips' inner faces, as one measures tweezers: each arm's midline sits half its
    # thickness outside its inner face, its hinge HINGE_OFF off the tool's midline
    theta0 = math.atan((tw["tip_gap"] / 2.0 + tw["thick"] / 2.0 - HINGE_OFF) / L)
    return {"flex_length_m": L, "I_m4": I, "k_tip_n_per_m": k_tip, "k_theta_n_m_per_rad": k_theta,
            "k_theta_n_m_per_deg": k_theta * math.pi / 180.0, "rest_angle_deg": math.degrees(theta0),
            "tip_force_closed_n": k_tip * tw["tip_gap"] / 2.0}


def annulus_sectors(r_in: float, r_out: float, sides: int) -> list[list[tuple[float, float]]]:
    """A ring as `sides` convex quadrilaterals (each between two radii, the inner and outer chords)."""
    out = []
    for k in range(sides):
        a0, a1 = 2 * math.pi * k / sides, 2 * math.pi * (k + 1) / sides
        out.append([(r_in * math.cos(a0), r_in * math.sin(a0)), (r_out * math.cos(a0), r_out * math.sin(a0)),
                    (r_out * math.cos(a1), r_out * math.sin(a1)), (r_in * math.cos(a1), r_in * math.sin(a1))])
    return out


def _prism_mesh(st, path, poly, z0, z1, color):
    from pxr import Gf, UsdGeom, UsdPhysics, Vt
    k = len(poly)
    if sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1])) < 0:
        poly = poly[::-1]
    pts = [(x, y, z0) for x, y in poly] + [(x, y, z1) for x, y in poly]
    faces = [list(range(k))[::-1], list(range(k, 2 * k))] + [[i, (i + 1) % k, k + (i + 1) % k, k + i] for i in range(k)]
    m = UsdGeom.Mesh.Define(st, path)
    m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*q) for q in pts]))
    m.CreateFaceVertexCountsAttr(Vt.IntArray([len(f) for f in faces]))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
    m.CreateSubdivisionSchemeAttr("none")
    m.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
    UsdPhysics.CollisionAPI.Apply(m.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr("convexHull")
    return m


def _material(st, root, name, sf, df, rest=0.2):
    from pxr import UsdPhysics, UsdShade
    m = UsdShade.Material.Define(st, f"{root}/PhysicsMaterials/{name}")
    api = UsdPhysics.MaterialAPI.Apply(m.GetPrim())
    api.CreateStaticFrictionAttr(sf)
    api.CreateDynamicFrictionAttr(df)
    api.CreateRestitutionAttr(rest)
    return m


def _bind(prim, mat):
    from pxr import UsdShade
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat, UsdShade.Tokens.weakerThanDescendants, "physics")


def _stage(path: Path, root_name: str):
    from pxr import Usd, UsdGeom
    path.parent.mkdir(parents=True, exist_ok=True)
    st = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(st, f"/World/{root_name}")
    st.SetDefaultPrim(st.GetPrimAtPath("/World"))
    return st, root


def _small_body(prim, contact=0.0008):
    """A few-mm body: continuous collision detection and contact offsets at its scale."""
    from pxr import Sdf
    prim.AddAppliedSchema("PhysxRigidBodyAPI")
    prim.CreateAttribute("physxRigidBody:enableCCD", Sdf.ValueTypeNames.Bool).Set(True)


def _collider_offsets(prim, contact=0.0008):
    from pxr import Sdf
    prim.AddAppliedSchema("PhysxCollisionAPI")
    prim.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(contact)
    prim.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(0.0)


def make_tablet(out: Path) -> dict:
    from pxr import Gf, UsdGeom, UsdPhysics, Vt
    tb = TABLET
    st, root = _stage(out, "Tablet")
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    _small_body(root.GetPrim())
    mass = math.pi * (tb["d"] / 2) ** 2 * tb["t"] * tb["density"]
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(mass)
    cyl = UsdGeom.Cylinder.Define(st, "/World/Tablet/Body")
    cyl.CreateAxisAttr("Z")
    cyl.CreateRadiusAttr(tb["d"] / 2)
    cyl.CreateHeightAttr(tb["t"])
    cyl.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.96, 0.95, 0.92)]))
    UsdPhysics.CollisionAPI.Apply(cyl.GetPrim())
    _collider_offsets(cyl.GetPrim())
    _bind(cyl.GetPrim(), _material(st, "/World/Tablet", "Tablet", 0.4, 0.35, 0.1))
    meta = {"asset_id": "tablet", "class": "pill_tablet", "size_m": [tb["d"], tb["d"], tb["t"]], "mass_kg": round(mass, 6)}
    root.GetPrim().SetCustomDataByKey("er:generated", json.dumps({"by": "scripts/make_pill_kit.py", **meta, "sources": {k: SOURCES[k] for k in ("tablet_size", "tablet_density")}}))
    st.GetRootLayer().Save()
    return meta


def make_vial(out: Path) -> dict:
    """The vial's body and its cap as two rigid bodies of one asset (the cap seated on the body's rim, not jointed:
    the thread's law joins them in the replay - cap_thread)."""
    from pxr import Gf, UsdGeom, UsdPhysics
    v = VIAL
    st, root = _stage(out, "PillVial")
    amber = (0.80, 0.42, 0.10)
    white = (0.95, 0.95, 0.95)
    pp = _material(st, "/World/PillVial", "Polypropylene", 0.4, 0.35, 0.3)
    body = UsdGeom.Xform.Define(st, "/World/PillVial/Body")
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    r_in = v["r_out"] - v["wall"]
    for i, poly in enumerate(annulus_sectors(r_in, v["r_out"], v["sides"])):
        m = _prism_mesh(st, f"/World/PillVial/Body/Wall{i:02d}", poly, v["floor"], v["h"], amber)
        _bind(m.GetPrim(), pp)
        _collider_offsets(m.GetPrim(), 0.001)
    disk = [(v["r_out"] * math.cos(2 * math.pi * k / v["sides"]), v["r_out"] * math.sin(2 * math.pi * k / v["sides"])) for k in range(v["sides"])]
    fl = _prism_mesh(st, "/World/PillVial/Body/Floor", disk, 0.0, v["floor"], amber)
    _bind(fl.GetPrim(), pp)
    _collider_offsets(fl.GetPrim(), 0.001)
    vol_body = math.pi * (v["r_out"] ** 2 - r_in ** 2) * (v["h"] - v["floor"]) + math.pi * v["r_out"] ** 2 * v["floor"]
    UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(vol_body * v["density"])
    # the cap, seated on the rim: its skirt round the body's top, its top over the opening
    cap = UsdGeom.Xform.Define(st, "/World/PillVial/Cap")
    cap.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, v["h"] - v["cap_h"] + v["cap_top"]))
    UsdPhysics.RigidBodyAPI.Apply(cap.GetPrim())
    c_in = v["cap_r_out"] - v["cap_wall"]
    for i, poly in enumerate(annulus_sectors(c_in, v["cap_r_out"], v["sides"])):
        m = _prism_mesh(st, f"/World/PillVial/Cap/Skirt{i:02d}", poly, 0.0, v["cap_h"] - v["cap_top"], white)
        _bind(m.GetPrim(), pp)
        _collider_offsets(m.GetPrim(), 0.001)
    top = [(v["cap_r_out"] * math.cos(2 * math.pi * k / v["sides"]), v["cap_r_out"] * math.sin(2 * math.pi * k / v["sides"])) for k in range(v["sides"])]
    tp = _prism_mesh(st, "/World/PillVial/Cap/Top", top, v["cap_h"] - v["cap_top"], v["cap_h"], white)
    _bind(tp.GetPrim(), pp)
    _collider_offsets(tp.GetPrim(), 0.001)
    vol_cap = math.pi * (v["cap_r_out"] ** 2 - c_in ** 2) * (v["cap_h"] - v["cap_top"]) + math.pi * v["cap_r_out"] ** 2 * v["cap_top"]
    UsdPhysics.MassAPI.Apply(cap.GetPrim()).CreateMassAttr(vol_cap * v["density"])
    meta = {"asset_id": "pill_vial", "class": "pill_bottle", "body": "Body", "cap": "Cap",
            "outer_d_m": 2 * v["r_out"], "inner_d_m": 2 * r_in * math.cos(math.pi / v["sides"]), "height_m": v["h"],
            "cap_d_m": 2 * v["cap_r_out"], "cap_h_m": v["cap_h"], "body_mass_kg": round(vol_body * v["density"], 5),
            "cap_mass_kg": round(vol_cap * v["density"], 5), "opening_z_m": v["h"], "floor_z_m": v["floor"],
            "thread": {"turns": 1.0, "pitch_m": 0.00423, "why": "GPI 400: one turn; 6 threads per inch for a ~38-43 mm neck (reasoned)"}}
    root.GetPrim().SetCustomDataByKey("er:generated", json.dumps({"by": "scripts/make_pill_kit.py", **meta,
                                                                  "sources": {k: SOURCES[k] for k in ("vial", "cap_thread")}}))
    st.GetRootLayer().Save()
    return meta


def make_tweezers(out: Path) -> dict:
    """An articulation: the root block (the root link) and two arm links on revolute hinges at the root, the arms
    in the x-z plane along +x, opening in +-z; each hinge's drive holds its arm at rest with the arm's stiffness."""
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, Vt
    tw = TWEEZERS
    spr = tweezer_spring()
    st, root = _stage(out, "Tweezers")
    steel = _material(st, "/World/Tweezers", "Steel", 0.75, 0.58, 0.25)
    grey = (0.78, 0.79, 0.81)
    UsdPhysics.ArticulationRootAPI.Apply(root.GetPrim())

    def box(path, size, centre):
        c = UsdGeom.Cube.Define(st, path)
        c.CreateSizeAttr(1.0)
        c.AddTranslateOp().Set(Gf.Vec3d(*centre))
        c.AddScaleOp().Set(Gf.Vec3f(*size))
        c.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*grey)]))
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
        _bind(c.GetPrim(), steel)
        _collider_offsets(c.GetPrim(), 0.0005)
        return c

    L = tw["length"] - tw["root"]
    th0 = math.radians(spr["rest_angle_deg"])
    rootb = UsdGeom.Xform.Define(st, "/World/Tweezers/Root")
    UsdPhysics.RigidBodyAPI.Apply(rootb.GetPrim())
    root_h = 2 * tw["thick"] + 0.0004
    box("/World/Tweezers/Root/Block", [tw["root"], tw["width"], root_h], [tw["root"] / 2, 0.0, 0.0])
    m_root = tw["root"] * tw["width"] * root_h * tw["density"]
    UsdPhysics.MassAPI.Apply(rootb.GetPrim()).CreateMassAttr(m_root)
    m_arm = ((L - tw["tip_len"]) * tw["width"] + tw["tip_len"] * tw["tip_width"]) * tw["thick"] * tw["density"]
    joints = {}
    for side, sgn in (("Upper", 1.0), ("Lower", -1.0)):
        arm = UsdGeom.Xform.Define(st, f"/World/Tweezers/{side}Arm")
        # the arm's frame at its hinge (the root's far end), turned open by its rest angle about y
        arm.AddTranslateOp().Set(Gf.Vec3d(tw["root"], 0.0, sgn * HINGE_OFF))
        arm.AddOrientOp().Set(Gf.Quatf(float(math.cos(-sgn * th0 / 2)), Gf.Vec3f(0.0, float(math.sin(-sgn * th0 / 2)), 0.0)))
        UsdPhysics.RigidBodyAPI.Apply(arm.GetPrim())
        UsdPhysics.MassAPI.Apply(arm.GetPrim()).CreateMassAttr(m_arm)
        main = L - tw["tip_len"]
        box(f"/World/Tweezers/{side}Arm/Blade", [main, tw["width"], tw["thick"]], [main / 2, 0.0, 0.0])
        box(f"/World/Tweezers/{side}Arm/Tip", [tw["tip_len"], tw["tip_width"], tw["thick"]], [main + tw["tip_len"] / 2, 0.0, 0.0])
        j = UsdPhysics.RevoluteJoint.Define(st, f"/World/Tweezers/{side}Hinge")
        j.CreateBody0Rel().SetTargets([rootb.GetPath()])
        j.CreateBody1Rel().SetTargets([arm.GetPath()])
        j.CreateAxisAttr("Y")
        j.CreateLocalPos0Attr(Gf.Vec3f(tw["root"], 0.0, sgn * HINGE_OFF))
        j.CreateLocalRot0Attr(Gf.Quatf(float(math.cos(-sgn * th0 / 2)), Gf.Vec3f(0.0, float(math.sin(-sgn * th0 / 2)), 0.0)))
        j.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
        j.CreateLocalRot1Attr(Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)))
        # the joint's zero is the arm at rest; a positive turn about y takes x toward -z: it closes the upper arm
        # (sgn +1) and opens the lower one - each arm travels from rest to the midline (the rig's first build had the
        # limits mirrored: a closing arm met its stop at 0.2 deg, 6.6x the beam's stiffness, 2026-10-17)
        lo, hi = (0.0, spr["rest_angle_deg"]) if sgn > 0 else (-spr["rest_angle_deg"], 0.0)
        j.CreateLowerLimitAttr(lo - 0.2)
        j.CreateUpperLimitAttr(hi + 0.2)
        d = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
        d.CreateTypeAttr("force")
        d.CreateTargetPositionAttr(0.0)
        d.CreateStiffnessAttr(spr["k_theta_n_m_per_deg"])
        d.CreateDampingAttr(spr["k_theta_n_m_per_deg"] * 0.02)
        j.GetPrim().CreateAttribute("er:closing_sign", Sdf.ValueTypeNames.Float).Set(sgn)
        joints[side] = j.GetPath().pathString
    total = m_root + 2 * m_arm
    meta = {"asset_id": "tweezers_115", "class": "tweezers", "length_m": tw["length"], "mass_kg": round(total, 5), "joints": joints,
            "arms": {"upper": "UpperArm", "lower": "LowerArm"}, "tip_gap_m": tw["tip_gap"], "spring": spr}
    root.GetPrim().SetCustomDataByKey("er:generated", json.dumps({"by": "scripts/make_pill_kit.py", **meta, "sources": {"tweezers": SOURCES["tweezers"]}}))
    st.GetRootLayer().Save()
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "workspace" / "asset_library"))
    args = ap.parse_args()
    out = Path(args.out)
    for aid, fn in (("tablet", make_tablet), ("pill_vial", make_vial), ("tweezers_115", make_tweezers)):
        meta = fn(out / aid / f"{aid}.usda")
        (out / aid / f"{aid}.json").write_text(json.dumps(dict(meta, sources=SOURCES), indent=1))
        print(aid, json.dumps({k: v for k, v in meta.items() if k not in ("joints",)})[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
