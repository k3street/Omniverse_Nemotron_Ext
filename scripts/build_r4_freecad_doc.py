"""Build native FreeCAD 1.1.4 (`.FCStd`) multi-part B-Rep assemblies AND per-part
tessellated `.stl` render meshes for Amber v0.5 `tool_gripper_r4` (Right & Left hands)
+ docked `Wrist_Tool_Changer_R2`.

Run via:
  H:\FreeCAD-1.1.4\bin\python.exe scripts\build_r4_freecad_doc.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import FreeCAD as App
import Mesh
import Part

REPO_ROOT = Path(__file__).resolve().parents[1]
R4_CAD_DIR = REPO_ROOT / "workspace" / "amber_gripper_r4" / "cad"


def build_fcstd_for_hand(hand: str) -> dict[str, str]:
    idx_path = R4_CAD_DIR / f"fcstd_parts_{hand}.json"
    items = json.loads(idx_path.read_text(encoding="utf-8"))

    stl_dir = R4_CAD_DIR / f"parts_stl_{hand}"
    stl_dir.mkdir(parents=True, exist_ok=True)

    doc_name = f"Amber_v05_Gripper_R4_{hand.capitalize()}_With_WTC_R2"
    doc = App.newDocument(doc_name)
    groups: dict[str, Any] = {}
    mesh_records = []

    for idx, rec in enumerate(items):
        gname = rec["group"]
        if gname not in groups:
            grp = doc.addObject("App::DocumentObjectGroup", gname)
            grp.Label = gname
            groups[gname] = grp

        shape = Part.read(rec["step_path"])
        feat = doc.addObject("Part::Feature", rec["name"])
        feat.Label = rec["name"]
        feat.Shape = shape
        groups[gname].addObject(feat)

        stl_path = stl_dir / f"{idx:02d}_{rec['name']}.stl"
        verts, tris = shape.tessellate(0.08)
        facets = [[verts[i] for i in tri] for tri in tris]
        m = Mesh.Mesh(facets)
        m.write(str(stl_path))
        mesh_records.append({**rec, "stl_path": str(stl_path)})

    doc.recompute()
    fcstd_path = R4_CAD_DIR / f"amber_gripper_r4_{hand}_with_changer.FCStd"
    if fcstd_path.exists():
        fcstd_path.unlink()
    doc.saveAs(str(fcstd_path))
    App.closeDocument(doc.Name)

    meshes_json = R4_CAD_DIR / f"fcstd_parts_{hand}_meshes.json"
    meshes_json.write_text(json.dumps(mesh_records, indent=2), encoding="utf-8")
    return {"fcstd_path": str(fcstd_path), "meshes_json": str(meshes_json)}


def main() -> int:
    out = {}
    for hand in ("right", "left"):
        idx_p = R4_CAD_DIR / f"fcstd_parts_{hand}.json"
        if idx_p.exists():
            out[hand] = build_fcstd_for_hand(hand)
    print(json.dumps({"fcstd_outputs": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
