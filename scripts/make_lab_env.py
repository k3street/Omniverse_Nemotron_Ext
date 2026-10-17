#!/usr/bin/env python3
"""A clean laboratory room for HomeHero's laboratory materials handler
occupation (scene request sterile_materials_lab_v1), generated: its stations
are static colliders with physics materials, its look plain lit surfaces.

The stations sit where the R5's proven work does: the receiving and the
inspection bench end to end along the robot's left, their tops at the dining
table's measured 0.708 m and their front edge 0.25 m from the robot's side,
as the table it has picked, carried and placed on (the can, the mug, the
pawn) - one stance reaches both. HomeHero asks for reachable bench heights
from measured robot geometry, not guesses: these are the measured ones.

  - benches: white laminate tops (epoxy-resin friction) 25 mm thick on
    steel legs, each 0.70 x 0.75 m
  - the open storage bin: a 400 x 300 Euro container (VDA 4500: 396 x 297 x
    147.5 mm outside, 3 mm walls) standing on the inspection bench's far end
  - the operator desk: out of the manipulation corridor
  - a room of white walls and a grey floor, lit from the ceiling

    make_lab_env.py [--out workspace/generated_scenes/sterile_materials_lab_v1/lab.usda]
writes the USD and a stations.json beside it (each station's box, top and role).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MATS = json.loads((REPO / "workspace" / "knowledge" / "physics_materials.json").read_text())["materials"]

TOP_Z = 0.708                  # the dining table's measured top (the R5's proven grasps and place)
BENCH_Y = (0.523, 1.273)       # front edge at the table's, 0.75 m deep
TOP_T = 0.025
LEG = 0.04
SOURCES = {
    "bench_height": "the dining table's measured top (aida_r5_dining_* build.json support_top_m 0.708) where the R5's grasps and place pass",
    "euro_container": "VDA 4500 Euro container 400 x 300: outside 396 x 297 mm, height 147.5 mm (https://en.wikipedia.org/wiki/Euro_container)",
}


def stations() -> dict:
    """The room's stations as boxes (world metres): name -> {role, parts: [{name, size, centre, material, color}], top_z, region}."""
    def bench(name, x0, x1, role):
        y0, y1 = BENCH_Y
        parts = [{"name": "Top", "size": [x1 - x0, y1 - y0, TOP_T], "centre": [(x0 + x1) / 2, (y0 + y1) / 2, TOP_Z - TOP_T / 2],
                  "material": "resin_epoxy", "color": [0.93, 0.94, 0.95]}]
        for i, (lx, ly) in enumerate(((x0 + 0.05, y0 + 0.05), (x1 - 0.05, y0 + 0.05), (x0 + 0.05, y1 - 0.05), (x1 - 0.05, y1 - 0.05))):
            parts.append({"name": f"Leg{i}", "size": [LEG, LEG, TOP_Z - TOP_T], "centre": [lx, ly, (TOP_Z - TOP_T) / 2],
                          "material": "steel_stainless", "color": [0.72, 0.74, 0.76]})
        return {"role": role, "parts": parts, "top_z": TOP_Z,
                "region": {"min": [x0 + 0.03, y0 + 0.03], "max": [x1 - 0.03, y1 - 0.03]}}

    out = {
        "ReceivingBench": bench("ReceivingBench", 3.95, 4.65, "receiving_bench"),
        "InspectionBench": bench("InspectionBench", 4.67, 5.37, "inspection_bench"),
    }
    # the open storage bin on the inspection bench's far end: a Euro container's floor and walls
    bx, by, ox, oy, h, w = 5.15, 0.90, 0.396, 0.297, 0.1475, 0.003
    z0 = TOP_Z
    blue = [0.28, 0.42, 0.62]
    out["OpenStorageBin"] = {"role": "open_storage_bin", "top_z": z0 + h, "parts": [
        {"name": "Floor", "size": [ox, oy, w], "centre": [bx, by, z0 + w / 2], "material": "plastic_abs", "color": blue},
        {"name": "WallXn", "size": [w, oy, h], "centre": [bx - ox / 2 + w / 2, by, z0 + h / 2], "material": "plastic_abs", "color": blue},
        {"name": "WallXp", "size": [w, oy, h], "centre": [bx + ox / 2 - w / 2, by, z0 + h / 2], "material": "plastic_abs", "color": blue},
        {"name": "WallYn", "size": [ox - 2 * w, w, h], "centre": [bx, by - oy / 2 + w / 2, z0 + h / 2], "material": "plastic_abs", "color": blue},
        {"name": "WallYp", "size": [ox - 2 * w, w, h], "centre": [bx, by + oy / 2 - w / 2, z0 + h / 2], "material": "plastic_abs", "color": blue}],
        "region": {"min": [bx - ox / 2 + w + 0.01, by - oy / 2 + w + 0.01], "max": [bx + ox / 2 - w - 0.01, by + oy / 2 - w - 0.01]},
        "floor_z": z0 + w}
    out["OperatorDesk"] = {"role": "operator_desk", "top_z": 0.74, "parts": [
        {"name": "Top", "size": [1.2, 0.6, 0.03], "centre": [6.9, -1.6, 0.725], "material": "resin_epoxy", "color": [0.85, 0.86, 0.88]},
        {"name": "Body", "size": [0.4, 0.55, 0.71], "centre": [7.3, -1.6, 0.355], "material": "steel_mild", "color": [0.6, 0.62, 0.65]}],
        "region": {"min": [6.35, -1.85], "max": [7.45, -1.35]}}
    return out


ROOM = {"x": (2.8, 7.8), "y": (-2.6, 1.32), "h": 2.7}


def build(out: Path) -> dict:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade, Vt

    out.parent.mkdir(parents=True, exist_ok=True)
    st = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(st, "/Root")
    st.SetDefaultPrim(root.GetPrim())

    looks, phys = {}, {}

    def look(color, rough=0.6, metal=0.0):
        key = (tuple(round(c, 3) for c in color), rough, metal)
        if key not in looks:
            m = UsdShade.Material.Define(st, f"/Root/Looks/L{len(looks)}")
            sh = UsdShade.Shader.Define(st, f"{m.GetPath()}/Surface")
            sh.CreateIdAttr("UsdPreviewSurface")
            sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
            sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
            m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
            looks[key] = m
        return looks[key]

    def physics_material(name):
        if name not in phys:
            m = UsdShade.Material.Define(st, f"/Root/PhysicsMaterials/{name}")
            api = UsdPhysics.MaterialAPI.Apply(m.GetPrim())
            api.CreateStaticFrictionAttr(float(MATS[name]["static_friction"]))
            api.CreateDynamicFrictionAttr(float(MATS[name]["dynamic_friction"]))
            api.CreateRestitutionAttr(float(MATS[name]["restitution"]))
            phys[name] = m
        return phys[name]

    def box(path, size, centre, color, material=None, collide=True, rough=0.6, metal=0.0):
        c = UsdGeom.Cube.Define(st, path)
        c.CreateSizeAttr(1.0)
        c.AddTranslateOp().Set(Gf.Vec3d(*centre))
        c.AddScaleOp().Set(Gf.Vec3f(*size))
        c.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        UsdShade.MaterialBindingAPI.Apply(c.GetPrim()).Bind(look(color, rough, metal))
        if collide:
            UsdPhysics.CollisionAPI.Apply(c.GetPrim())
            if material:
                UsdShade.MaterialBindingAPI(c.GetPrim()).Bind(physics_material(material), UsdShade.Tokens.weakerThanDescendants, "physics")
        return c

    # the room: floor, four walls, the ceiling's lights
    (x0, x1), (y0, y1), h = ROOM["x"], ROOM["y"], ROOM["h"]
    box("/Root/Room/Floor", [x1 - x0, y1 - y0, 0.02], [(x0 + x1) / 2, (y0 + y1) / 2, -0.01], [0.62, 0.64, 0.66], "concrete", rough=0.8)
    wall = [0.94, 0.95, 0.96]
    box("/Root/Room/WallN", [x1 - x0, 0.1, h], [(x0 + x1) / 2, y1 + 0.05, h / 2], wall, "concrete", rough=0.9)
    box("/Root/Room/WallS", [x1 - x0, 0.1, h], [(x0 + x1) / 2, y0 - 0.05, h / 2], wall, "concrete", rough=0.9)
    box("/Root/Room/WallW", [0.1, y1 - y0, h], [x0 - 0.05, (y0 + y1) / 2, h / 2], wall, "concrete", rough=0.9)
    box("/Root/Room/WallE", [0.1, y1 - y0, h], [x1 + 0.05, (y0 + y1) / 2, h / 2], wall, "concrete", rough=0.9)
    for i, (lx, ly) in enumerate(((4.0, 0.2), (5.6, 0.2), (4.0, -1.6), (5.6, -1.6), (7.0, -1.0))):
        lt = UsdLux.RectLight.Define(st, f"/Root/Room/CeilingLight{i}")
        lt.CreateWidthAttr(1.2)
        lt.CreateHeightAttr(0.3)
        lt.CreateIntensityAttr(9000.0)
        lt.CreateColorAttr(Gf.Vec3f(1.0, 0.98, 0.95))
        UsdGeom.Xformable(lt).AddTranslateOp().Set(Gf.Vec3d(lx, ly, h - 0.02))
    dome = UsdLux.DomeLight.Define(st, "/Root/Room/Ambient")
    dome.CreateIntensityAttr(400.0)

    sts = stations()
    for name, s in sts.items():
        grp = UsdGeom.Xform.Define(st, f"/Root/Stations/{name}")
        grp.GetPrim().SetCustomDataByKey("er:station", json.dumps({"role": s["role"], "top_z": s["top_z"], "region": s["region"],
                                                                    **({"floor_z": s["floor_z"]} if "floor_z" in s else {})}))
        for p in s["parts"]:
            box(f"/Root/Stations/{name}/{p['name']}", p["size"], p["centre"], p["color"], p["material"],
                rough=0.35 if p["material"].startswith("steel") else 0.55, metal=0.8 if p["material"].startswith("steel") else 0.0)
    root.GetPrim().SetCustomDataByKey("er:generated", json.dumps({"by": "scripts/make_lab_env.py", "request": "sterile_materials_lab_v1",
                                                                   "sources": SOURCES}))
    st.GetRootLayer().Save()
    meta = {"usd": str(out), "support": "/Root/Stations/ReceivingBench/Top", "room": ROOM,
            "stations": {k: {kk: v[kk] for kk in ("role", "top_z", "region") if kk in v} | ({"floor_z": v["floor_z"]} if "floor_z" in v else {})
                         for k, v in sts.items()},
            "sources": SOURCES}
    (out.parent / "stations.json").write_text(json.dumps(meta, indent=1))
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "workspace" / "generated_scenes" / "sterile_materials_lab_v1" / "lab.usda"))
    args = ap.parse_args()
    meta = build(Path(args.out))
    print(json.dumps({k: meta[k] for k in ("usd", "support")}), {k: v["role"] for k, v in meta["stations"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
