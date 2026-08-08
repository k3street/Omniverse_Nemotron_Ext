#!/usr/bin/env python3
"""Parametric garment/linen assets for the laundry-folding mission.

Scanned cloth almost always has degenerate topology (double-sided strips,
duplicated vertices, fragmented parts) that no cloth solver can integrate.
Folding policies train on CLEAN procedural meshes — this generates them
as USD assets and pushes them through the normal ingest gate, so they
arrive in the library as `deformable_*` citizens like everything else.

Garments (all quad grids, meters, Z-up, origin at center):
    towel       0.70 x 1.40 m bath towel        (default 28x56 grid)
    hand_towel  0.40 x 0.70 m
    washcloth   0.30 x 0.30 m
    napkin      0.45 x 0.45 m
    tshirt      0.70 x 0.75 m T-shirt silhouette (grid masked to shape)

Usage:
    python scripts/make_garment.py towel [--out DIR] [--res 0.025]
    python scripts/make_garment.py all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

GARMENTS = {
    "towel": {"w": 0.70, "h": 1.40, "class": "towel"},
    "hand_towel": {"w": 0.40, "h": 0.70, "class": "towel"},
    "washcloth": {"w": 0.30, "h": 0.30, "class": "towel"},
    "napkin": {"w": 0.45, "h": 0.45, "class": "towel"},
    "tshirt": {"w": 0.70, "h": 0.75, "class": "clothing_garment",
               "mask": "tshirt"},
}


def _tshirt_mask(u: float, v: float) -> bool:
    """Inside-the-silhouette test on the unit square (v=0 hem, v=1 collar).
    Body spans the middle 56%% of width; sleeves flare above v=0.62."""
    if v <= 0.62:
        return abs(u - 0.5) <= 0.28
    if v <= 0.88:
        return True  # sleeve band spans full width
    # shoulder line with a collar notch
    return abs(u - 0.5) <= 0.42 and not (
        abs(u - 0.5) <= 0.10 and v >= 0.94)


def build_garment(name: str, out_dir: Path, res: float = 0.025) -> Path:
    from pxr import Gf, Usd, UsdGeom

    spec = GARMENTS[name]
    w, h = spec["w"], spec["h"]
    # cell size scales with the garment: below ~3.5% of the long side the
    # normalized edges leave the cloth solver's stable stiffness regime
    res = max(res, 0.035 * max(w, h))
    nx, ny = max(2, round(w / res)), max(2, round(h / res))
    mask = _tshirt_mask if spec.get("mask") == "tshirt" else (lambda u, v: True)

    # vertex grid, masked; index map keeps only used vertices
    vid = {}
    pts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            u, v = i / nx, j / ny
            if mask(u, v):
                vid[(i, j)] = len(pts)
                pts.append(Gf.Vec3f(w * u - w / 2, h * v - h / 2, 0.0))
    counts, idx = [], []
    for j in range(ny):
        for i in range(nx):
            quad = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            if all(q in vid for q in quad):
                counts.append(4)
                idx += [vid[q] for q in quad]

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.usda"
    stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, f"/World/{name.title().replace('_', '')}")
    mesh.GetPointsAttr().Set(pts)
    mesh.GetFaceVertexCountsAttr().Set(counts)
    mesh.GetFaceVertexIndicesAttr().Set(idx)
    mesh.GetDoubleSidedAttr().Set(True)
    stage.GetRootLayer().Save()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("garment", choices=[*GARMENTS, "all"])
    ap.add_argument("--out", default=str(REPO / "workspace" / "generated_garments"))
    ap.add_argument("--res", type=float, default=0.025,
                    help="grid resolution in meters (default 2.5 cm)")
    ap.add_argument("--no-ingest", action="store_true")
    args = ap.parse_args()

    names = list(GARMENTS) if args.garment == "all" else [args.garment]
    for name in names:
        out = build_garment(name, Path(args.out), args.res)
        print(f"built {out}")
        if not args.no_ingest:
            from ingest_asset import queue_file
            entry = queue_file(str(out), class_hint=GARMENTS[name]["class"],
                               asset_id=f"garment_{name}")
            print(f"  queued garment_{name}: "
                  f"category {entry['proposed_category']}, "
                  f"deformable {entry.get('deformable')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
