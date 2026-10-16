#!/usr/bin/env python3
"""A support (a table) as boxes: its slab, and each leg panel under it, from
its mesh points. The planners use these as obstacles (plan_er_grasp.py,
`support_parts: split`: a pinch under the slab's edge is free space the one
box to the floor would forbid) and a scene can use the same boxes as the
support's PhysX collision in place of its render meshes
(build_er_scene.py, `support_collision: proxy`): the dining table's top is
two overlapping collision meshes - a rim and an inlay plate, coplanar with a
1 cm seam 1.5-2.5 cm from the edge - and a towel hanging 10 cm over that
edge crept off it in PhysX while holding on every clean slab
(verify_cloth_friction.py, 2026-10-15).
"""
from __future__ import annotations


def densify_top(points, face_counts, face_indices, top: float, top_depth: float = 0.015, spacing: float = 0.02):
    """Points sampled over a mesh's top faces (every vertex within top_depth of
    the top) on a grid about `spacing` apart, with the vertices themselves:
    a table top is a few large faces, and its vertices alone leave 30 cm
    stretches with no point at all (the dining table between x 4.63 and 4.93),
    so an edge or a band measured on vertices finds nothing there."""
    import numpy as np
    P = np.asarray(points, float)
    counts = np.asarray(face_counts, int)
    idx = np.asarray(face_indices, int)
    out = [P]
    k = 0
    for c in counts:
        f = idx[k:k + c]
        k += c
        if c < 3 or np.any(P[f, 2] < top - top_depth):
            continue
        for a, b, d in ((f[0], f[i], f[i + 1]) for i in range(1, c - 1)):       # a fan of triangles
            A, B, C = P[a], P[b], P[d]
            n = int(np.ceil(max(np.linalg.norm(B - A), np.linalg.norm(C - A), np.linalg.norm(C - B)) / spacing))
            if n < 2:
                continue
            u, v = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
            keep = (u + v) <= n
            u, v = u[keep] / n, v[keep] / n
            out.append(A + np.outer(u, B - A) + np.outer(v, C - A))
    return np.vstack(out)


def box_samples(lo, hi, spacing: float = 0.02):
    """The eight corners of an axis-aligned box and a grid over its top face
    (a fixture such as a drying rail, authored as a Cube)."""
    import numpy as np
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    xs = np.linspace(lo[0], hi[0], max(2, int(np.ceil((hi[0] - lo[0]) / spacing)) + 1))
    ys = np.linspace(lo[1], hi[1], max(2, int(np.ceil((hi[1] - lo[1]) / spacing)) + 1))
    top = np.array([[x, y, hi[2]] for x in xs for y in ys])
    return np.vstack([corners, top])


def mesh_samples(prim, top: float, spacing: float = 0.02):
    """densify_top over every mesh under a USD prim (and box_samples over
    every Cube), in world coordinates."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom
    xc = UsdGeom.XformCache()
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.guide])
    parts = []
    for q in Usd.PrimRange(prim):
        if q.IsA(UsdGeom.Mesh):
            m = UsdGeom.Mesh(q)
            M = xc.GetLocalToWorldTransform(q)
            pts = [list(M.Transform(Gf.Vec3d(pt))) for pt in m.GetPointsAttr().Get()]
            parts.append(densify_top(pts, m.GetFaceVertexCountsAttr().Get(), m.GetFaceVertexIndicesAttr().Get(), top, spacing=spacing))
        elif q.IsA(UsdGeom.Cube) or q.IsA(UsdGeom.Cylinder):
            r = bc.ComputeWorldBound(q).ComputeAlignedRange()                  # a cylinder as its bounding box: a peg's box is its obstacle
            parts.append(box_samples(list(r.GetMin()), list(r.GetMax()), spacing))
    return np.vstack(parts)


def support_boxes(points, top: float, top_depth: float = 0.015, max_slab_thickness: float = 0.05, leg_gap: float = 0.05,
                  band_m: float = 0.1):
    """[{id, min, max}]: the slab as x-bands band_m wide, each with the y
    extent of the TOP face within it (the dining table's leg columns run
    flush to its top and stand 2.6 cm proud of the slab's edge, so one box
    for the whole top would put the edge 2.6 cm out along the whole length);
    then one box per cluster of the points below the slab, clustered along x
    by gaps wider than leg_gap. Leg panels meet the slab's underside, so the
    top face alone (points within top_depth of the top) gives the footprint;
    the underside is the lowest point under that footprint within
    max_slab_thickness of the top."""
    import numpy as np
    SP = np.asarray(points, float)
    face = SP[SP[:, 2] >= top - top_depth]
    (fx0, fy0), (fx1, fy1) = face.min(0)[:2], face.max(0)[:2]
    in_fp = (SP[:, 0] >= fx0) & (SP[:, 0] <= fx1) & (SP[:, 1] >= fy0) & (SP[:, 1] <= fy1)
    below = SP[(SP[:, 2] < top - top_depth) & (SP[:, 2] >= top - max_slab_thickness) & in_fp]
    bottom = float(below[:, 2].min()) if len(below) else top - top_depth
    boxes = []
    n = max(1, int(np.ceil((fx1 - fx0) / band_m)))
    edges = np.linspace(fx0, fx1, n + 1)
    extents = []
    for k in range(n):
        band = face[(face[:, 0] >= edges[k] - 1e-9) & (face[:, 0] <= edges[k + 1] + 1e-9)]
        extents.append((float(band[:, 1].min()), float(band[:, 1].max())) if len(band) else None)
    for k in range(n):
        if extents[k] is None:
            # no vertex in this band (a top face that is one large polygon):
            # the outline runs straight between vertices, so the band spans
            # what its nearest filled neighbours on each side span
            left = next((extents[j] for j in range(k - 1, -1, -1) if extents[j]), None)
            right = next((extents[j] for j in range(k + 1, n) if extents[j]), None)
            both = [e for e in (left, right) if e]
            extents[k] = (min(e[0] for e in both), max(e[1] for e in both))
        boxes.append(dict(id=f"table:slab{k}", min=[float(edges[k]), extents[k][0], bottom],
                          max=[float(edges[k + 1]), extents[k][1], float(top)]))
    under = SP[SP[:, 2] < bottom - 1e-6]
    if len(under):
        xs = np.sort(under[:, 0])
        gaps = np.flatnonzero(np.diff(xs) > leg_gap)
        bounds = [xs[0]] + [xs[i + 1] for i in gaps] + [xs[-1] + 1e-6]
        for k in range(len(bounds) - 1):
            part = under[(under[:, 0] >= bounds[k]) & (under[:, 0] < bounds[k + 1])]
            if len(part):
                boxes.append(dict(id=f"table:under{k}", min=part.min(0).tolist(), max=part.max(0).tolist()))
    return boxes


def slab_boxes(points, n=6, margin=0.0, prefix="body"):
    """A thin elongated body (a scissors' half hanging at an angle) as `n`
    axis-aligned boxes along its longest axis (PCA), each with `margin`:
    far tighter than one box about the whole, which filled the free space
    beside a hanging blade (2026-10-15: the right hand's curled fingers
    could not pass beside the held half's blade for the one-box obstacle)."""
    import numpy as np

    P = np.asarray(points, float)
    c = P.mean(0)
    u = np.linalg.svd(P - c, full_matrices=False)[2][0]
    s = (P - c) @ u
    edges = np.linspace(s.min(), s.max(), n + 1)
    out = []
    for k in range(n):
        m = (s >= edges[k]) & (s <= edges[k + 1])
        if not m.any():
            continue
        out.append(dict(id=f"{prefix}:{k}", min=(P[m].min(0) - margin).tolist(), max=(P[m].max(0) + margin).tolist()))
    return out
