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


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(a, apply(a))
