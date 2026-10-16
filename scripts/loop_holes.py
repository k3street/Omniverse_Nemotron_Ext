#!/usr/bin/env python3
"""The finger holes of a flat tool (scissors, pliers handles) from its mesh,
without a simulator: the surface sampled densely, projected onto the tool's
plane (the two largest principal axes), rasterised at 1 mm; the empty regions
not reachable from the border are its holes; each hole's width is the
diameter of its largest inscribed circle, its length its longest extent. What
decides whether a robot finger can go through a loop: the Ability Hand's
index is 1.6 x 1.8 cm across its distal link, its thumb 2.5 x 3.0 cm, and the
library's approved scissors' loops are 1.3 cm wide (2026-10-15).

    loop_holes.py <asset usd> [...]          # every hole of each asset, widest first
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))


def surface_points(path: str, spacing: float = 0.0015) -> np.ndarray:
    from pxr import Gf, Usd, UsdGeom

    from tool_grip_planner import sample_faces

    st = Usd.Stage.Open(path)
    xc = UsdGeom.XformCache()
    out = []
    for m in st.Traverse():
        if m.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(m).GetPurposeAttr().Get() != "guide":
            M = xc.GetLocalToWorldTransform(m)
            mesh = UsdGeom.Mesh(m)
            pts = mesh.GetPointsAttr().Get()
            if not pts:
                continue
            V = np.asarray([list(M.Transform(Gf.Vec3d(*v))) for v in pts], float) * UsdGeom.GetStageMetersPerUnit(st)
            out.append(sample_faces(V, list(mesh.GetFaceVertexCountsAttr().Get()), list(mesh.GetFaceVertexIndicesAttr().Get()), spacing))
    return np.vstack(out) if out else np.zeros((0, 3))


def holes(points: np.ndarray, cell: float = 0.001) -> list[dict]:
    """Holes of the tool's silhouette in its own plane, widest first."""
    from scipy import ndimage

    P = np.asarray(points, float)
    c = P.mean(0)
    vt = np.linalg.svd(P - c, full_matrices=False)[2]
    uv = (P - c) @ vt[:2].T
    lo = uv.min(0) - 3 * cell
    ij = np.floor((uv - lo) / cell).astype(int)
    shape = ij.max(0) + 4
    solid = np.zeros(shape, bool)
    solid[ij[:, 0], ij[:, 1]] = True
    solid = ndimage.binary_closing(solid, iterations=2)                    # close the sampling's pinholes
    empty = ~solid
    lab, n = ndimage.label(empty)
    outside = set(np.unique(np.r_[lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))
    dist = ndimage.distance_transform_edt(empty) * cell
    out = []
    for k in range(1, n + 1):
        if k in outside:
            continue
        m = lab == k
        area = float(m.sum()) * cell * cell
        if area < 2e-5:                                                   # under 0.2 cm2: a seam, not a hole
            continue
        ii, jj = np.nonzero(m)
        pts = np.c_[ii, jj] * cell
        ext = pts - pts.mean(0)
        ax = np.linalg.svd(ext, full_matrices=False)[2]
        along = ext @ ax.T
        centre_uv = pts.mean(0) + lo
        out.append({"width_m": round(2 * float(dist[m].max()), 4), "length_m": round(float(np.ptp(along[:, 0])), 4),
                    "breadth_m": round(float(np.ptp(along[:, 1])), 4), "area_cm2": round(area * 1e4, 2),
                    "centre": np.round(c + centre_uv[0] * vt[0] + centre_uv[1] * vt[1], 4).tolist()})
    return sorted(out, key=lambda h: -h["width_m"])


def main() -> int:
    for path in sys.argv[1:]:
        P = surface_points(path)
        if not len(P):
            print(f"{path}: no mesh points")
            continue
        size = np.round(np.ptp(P, axis=0) * 100, 1).tolist()
        hs = holes(P)
        print(f"{Path(path).name}: size {size} cm; {len(hs)} holes: " +
              "; ".join(f"{h['width_m'] * 100:.1f} cm wide x {h['length_m'] * 100:.1f} long" for h in hs[:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
