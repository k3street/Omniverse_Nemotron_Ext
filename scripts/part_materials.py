#!/usr/bin/env python3
"""Physics materials per part, from the part survey: an asset is multi-material.

Ingest gave a whole asset one physics material (its class's first typical
material): pliers all steel, grips included; a toaster all steel, rubber feet
included. The part survey (part_survey.py) names each part's material; this
binds, to every mesh of each part, a physics material (UsdPhysics.MaterialAPI:
static and dynamic friction, restitution, density from
workspace/knowledge/physics_materials.json), with the "physics" material
purpose - the look of the asset is untouched.

Masses stay as authored (the class's range, per body); density is on the
material for tools that compute mass from geometry.

    python scripts/part_materials.py <asset_id> [...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
DB = REPO / "workspace" / "knowledge" / "physics_materials.json"


def apply(asset_id: str) -> dict:
    from pxr import Sdf, Usd, UsdPhysics, UsdShade

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    from processing import owned
    if not owned(entry["file"]):
        raise RuntimeError("not ours to write (a source file): build its derivative first")
    survey = entry.get("part_survey")
    if not survey:
        raise RuntimeError("no part survey (run part_survey.py first)")
    db = json.loads(DB.read_text())["materials"]
    stage = Usd.Stage.Open(entry["file"])
    root = survey.get("root") or str(next(iter(stage.GetPrimAtPath("/World").GetChildren())).GetPath())
    scope = f"{root}/PhysicsMaterials"
    made, bound = {}, Counter()
    for p in survey["parts"]:
        key = p.get("material")
        if key not in db:
            continue
        if key not in made:
            m = UsdShade.Material.Define(stage, f"{scope}/{key}")
            api = UsdPhysics.MaterialAPI.Apply(m.GetPrim())
            api.CreateStaticFrictionAttr(float(db[key]["static_friction"]))
            api.CreateDynamicFrictionAttr(float(db[key]["dynamic_friction"]))
            api.CreateRestitutionAttr(float(db[key]["restitution"]))
            api.CreateDensityAttr(float(db[key]["density_kg_m3"]))
            made[key] = m
        for path in p.get("members") or [p["path"]]:
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            for q in Usd.PrimRange(prim):
                if q.GetTypeName() == "Mesh":
                    UsdShade.MaterialBindingAPI.Apply(q).Bind(
                        made[key], bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                        materialPurpose="physics")
                    bound[key] += 1
    stage.GetRootLayer().Save()
    entry = json.loads(qf.read_text())
    entry["part_materials"] = {"materials": sorted(made), "meshes_bound": dict(bound)}
    from processing import record
    record(entry, "materials", materials=sorted(made))
    qf.write_text(json.dumps(entry, indent=1))
    return entry["part_materials"]


def ensure_physics(asset_id: str) -> str:
    """Every collider gets a physics material PhysX will use. A collider whose
    only binding is its look material (an older ingest, a single-mesh asset
    with no part survey) falls back to PhysX's default friction: bind the
    class's typical material to it, with the physics purpose."""
    from pxr import Usd, UsdPhysics, UsdShade

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    from processing import owned
    if not owned(entry["file"]):
        return "not ours to write (a source file): build its derivative first"
    db = json.loads(DB.read_text())["materials"]
    from processing import _prior
    mats = [m for m in (_prior(entry).get("typical_materials") or []) if m in db]
    if not mats:
        return "no class material to bind"
    key = mats[0]
    stage = Usd.Stage.Open(entry["file"])
    root = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()))
    asset_root = next((c for c in root.GetChildren() if c.GetName() not in ("Looks", "Materials")), root)

    def has_physics(p):
        m, _ = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial(materialPurpose="physics")
        return bool(m) and m.GetPrim().HasAPI(UsdPhysics.MaterialAPI)

    bare = [p for p in Usd.PrimRange(asset_root) if p.HasAPI(UsdPhysics.CollisionAPI) and not has_physics(p)]
    if not bare:
        return "every collider has a physics material"
    m = UsdShade.Material.Define(stage, f"{asset_root.GetPath()}/PhysicsMaterials/{key}")
    api = UsdPhysics.MaterialAPI.Apply(m.GetPrim())
    api.CreateStaticFrictionAttr(float(db[key]["static_friction"]))
    api.CreateDynamicFrictionAttr(float(db[key]["dynamic_friction"]))
    api.CreateRestitutionAttr(float(db[key]["restitution"]))
    api.CreateDensityAttr(float(db[key]["density_kg_m3"]))
    for p in bare:
        UsdShade.MaterialBindingAPI.Apply(p).Bind(m, bindingStrength=UsdShade.Tokens.strongerThanDescendants,
                                                  materialPurpose="physics")
    stage.GetRootLayer().Save()
    entry = json.loads(qf.read_text())
    entry.setdefault("applied_fixes", []).append(f"physics material {key} bound to {len(bare)} bare collider(s)")
    from processing import record
    record(entry, "materials", materials=[key], fallback="class")
    qf.write_text(json.dumps(entry, indent=1))
    return f"{key} bound to {len(bare)} collider(s) that had none"


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(a, apply(a))
