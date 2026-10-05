#!/usr/bin/env python3
"""Simulation proxies for cloth: a clean, light mesh the solver can step.

An artist's garment is made to be seen, not simulated: hundreds of
thousands of vertices (a ball gown: 256k), sliver triangles beside tiny
ones (12k triangles over 20:1 on one dress), seams where three faces share
an edge, and - for a skinned garment - points in bind space until the
skeleton poses them. VBD cloth diverges on all of these.

The proxy is the garment as a cloth solver wants it:
  1. posed: a UsdSkel garment's skinning is baked (in memory only);
  2. welded, with degenerate and doubled faces dropped;
  3. manifold: no edge on more than two faces;
  4. one piece: the largest part joined through shared edges (buttons,
     trims and bits hanging off a single vertex are not the cloth);
  5. light and even: quadric-decimated if huge, then isotropically
     remeshed (split long edges, collapse short ones, relax tangentially)
     toward one edge length giving ~TARGET_VERTICES; a few remaining
     slivers repaired (needles collapsed, caps flipped).

It is written into the asset's derivative as <asset>/ClothProxy, an
invisible guide-purpose mesh, and named in customData simReady:clothProxy;
the drape test and live cloth authoring use it instead of the render mesh.

Usage (Newton venv, which has fast_simplification):
    .venv-newton/bin/python scripts/cloth_proxy.py <queue_asset_id> [...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
TARGET_VERTICES = 4000
MIN_ANGLE_DEG = 12.0


def posed_mesh(usd_file: str) -> tuple[np.ndarray, np.ndarray]:
    """Every active mesh under the stage in world metres, skinned meshes
    posed by their skeleton (baked into the session layer, never saved)."""
    from pxr import Usd, UsdGeom, UsdSkel

    stage = Usd.Stage.Open(usd_file)
    if any(p.IsA(UsdSkel.Root) for p in stage.Traverse()):
        stage.SetEditTarget(stage.GetSessionLayer())
        UsdSkel.BakeSkinning(stage.Traverse())
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    cache = UsdGeom.XformCache()
    pts, tris, off = [], [], 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        mesh = UsdGeom.Mesh(prim)
        if mesh.GetPurposeAttr().Get() in (UsdGeom.Tokens.guide, UsdGeom.Tokens.proxy):
            continue  # a proxy from an earlier run is not the garment
        raw = mesh.GetPointsAttr().Get(Usd.TimeCode.EarliestTime()) or mesh.GetPointsAttr().Get()
        if not raw:
            continue
        p = np.array(raw, dtype=float)
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=float)
        pts.append((p @ m[:3, :3] + m[3, :3]) * mpu)
        idx, k = list(mesh.GetFaceVertexIndicesAttr().Get()), 0
        for c in mesh.GetFaceVertexCountsAttr().Get():
            for j in range(1, c - 1):  # fan
                tris.append((idx[k] + off, idx[k + j] + off, idx[k + j + 1] + off))
            k += c
        off += len(p)
    if not pts:
        raise RuntimeError("no mesh")
    return np.concatenate(pts), np.array(tris, dtype=np.int64)


def _clean(points, tris):
    """Weld, drop degenerate/doubled faces, make manifold, keep the largest piece."""
    span = float(np.ptp(points, axis=0).max())
    q = np.round(points / (span * 1e-6)).astype(np.int64)
    _, first, inverse = np.unique(q, axis=0, return_index=True, return_inverse=True)
    p = points[first]
    t = inverse.reshape(-1)[tris]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    _, keep = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
    t = t[np.sort(keep)]
    # manifold: an edge belongs to at most two faces (first come, first kept)
    uses, ok = Counter(), []
    for f in t:
        edges = [tuple(sorted((f[i], f[(i + 1) % 3]))) for i in range(3)]
        if all(uses[e] < 2 for e in edges):
            for e in edges:
                uses[e] += 1
            ok.append(f)
    t = np.array(ok)
    # largest piece by area, faces joined only through shared EDGES: pieces
    # touching at a single vertex (bowties) are separate pieces to a cloth
    # solver, and keeping them is what blew the dresses up
    parent = np.arange(len(t))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for fi, f in enumerate(t):
        for i in range(3):
            e = (min(f[i], f[(i + 1) % 3]), max(f[i], f[(i + 1) % 3]))
            if e in owner:
                parent[find(fi)] = find(owner[e])
            else:
                owner[e] = fi
    roots = np.array([find(i) for i in range(len(t))])
    area = 0.5 * np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), axis=1)
    sums = Counter()
    for r, a in zip(roots, area):
        sums[r] += a
    main = max(sums, key=sums.get)
    t = t[roots == main]
    used = np.unique(t)
    remap = -np.ones(len(p), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return p[used], remap[t]


def _min_angles(p, t):
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]

    def ang(u, v):
        cu = np.einsum("ij,ij->i", u, v) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-18)
        return np.degrees(np.arccos(np.clip(cu, -1, 1)))

    return np.minimum(np.minimum(ang(b - a, c - a), ang(a - b, c - b)), ang(a - c, b - c))


def _normals(p, t):
    n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-18)


def _repair_slivers(p, t, min_deg, passes=20):
    """Fix slivers without wrecking the mesh: a NEEDLE (one edge far shorter
    than the others) has that edge collapsed, unless that would flip a
    neighbouring face; a CAP (one angle near 180) has its longest edge
    flipped when the flip improves both faces. One fix per neighbourhood per
    pass; stops when a pass changes nothing."""
    p = p.copy()
    t = t.copy()
    for _ in range(passes):
        ang = _min_angles(p, t)
        bad = np.where(ang < min_deg)[0]
        if not len(bad):
            break
        v2f = {}
        for fi, f in enumerate(t):
            for v in f:
                v2f.setdefault(int(v), []).append(fi)
        e2f = {}
        for fi, f in enumerate(t):
            for i in range(3):
                e2f.setdefault(tuple(sorted((int(f[i]), int(f[(i + 1) % 3])))), []).append(fi)
        nrm = _normals(p, t)
        dead, touched, changed = set(), set(), False
        for fi in bad[np.argsort(ang[bad])]:
            f = [int(x) for x in t[fi]]
            if fi in dead or touched & set(f):
                continue
            L = [np.linalg.norm(p[f[i]] - p[f[(i + 1) % 3]]) for i in range(3)]
            order = np.argsort(L)
            if L[order[0]] < 0.35 * L[order[1]]:
                # needle: collapse the short edge to its midpoint
                i = int(order[0])
                u, v = f[i], f[(i + 1) % 3]
                mid = 0.5 * (p[u] + p[v])
                ring = set(v2f.get(u, [])) | set(v2f.get(v, []))
                gone = set(e2f.get(tuple(sorted((u, v))), []))
                ok = True
                for g in ring - gone:
                    gf = [mid if int(x) in (u, v) else p[int(x)] for x in t[g]]
                    n = np.cross(gf[1] - gf[0], gf[2] - gf[0])
                    if np.linalg.norm(n) < 1e-18 or n @ nrm[g] / np.linalg.norm(n) < 0.3:
                        ok = False
                        break
                if not ok:
                    continue
                p[u] = mid
                t[t == v] = u
                dead |= gone
                touched |= {u, v} | {int(x) for g in ring for x in t[g]}
                changed = True
            else:
                # cap: flip the longest edge
                i = int(order[2])
                a, b = f[i], f[(i + 1) % 3]
                c = f[(i + 2) % 3]
                faces = e2f.get(tuple(sorted((a, b))), [])
                if len(faces) != 2:
                    continue
                gi = faces[0] if faces[1] == fi else faces[1]
                if gi in dead:
                    continue
                d = next(int(x) for x in t[gi] if int(x) not in (a, b))
                new1, new2 = np.array([c, a, d]), np.array([c, d, b])
                old_min = min(ang[fi], ang[gi])
                new_min = float(_min_angles(p, np.array([new1, new2])).min())
                n1 = np.cross(p[new1[1]] - p[new1[0]], p[new1[2]] - p[new1[0]])
                n2 = np.cross(p[new2[1]] - p[new2[0]], p[new2[2]] - p[new2[0]])
                if new_min <= old_min or n1 @ nrm[fi] <= 0 or n2 @ nrm[fi] <= 0:
                    continue
                t[fi], t[gi] = new1, new2
                touched |= {a, b, c, d}
                changed = True
        keep = np.array([i for i in range(len(t)) if i not in dead], dtype=np.int64)
        t = t[keep]
        t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
        if not changed:
            break
    used = np.unique(t)
    remap = -np.ones(len(p), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return p[used], remap[t]


def _edges(t):
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    return np.unique(e, axis=0)


def _boundary_vertices(t):
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    u, c = np.unique(e, axis=0, return_counts=True)
    return set(u[c == 1].reshape(-1).tolist())


def _collapse_short(p, t, short):
    """Collapse edges shorter than `short` to their midpoints, shortest first,
    skipping any that would flip a face or move a boundary vertex inward."""
    p = p.copy()
    nrm = _normals(p, t)
    v2f = {}
    for fi, f in enumerate(t):
        for v in f:
            v2f.setdefault(int(v), []).append(fi)
    border = _boundary_vertices(t)
    e_all = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    eu, ec = np.unique(e_all, axis=0, return_counts=True)
    border_edges = set(map(tuple, eu[ec == 1].tolist()))
    E = _edges(t)
    L = np.linalg.norm(p[E[:, 0]] - p[E[:, 1]], axis=1)
    target = np.arange(len(p))
    touched = set()
    for k in np.argsort(L):
        if L[k] >= short:
            break
        u, v = int(E[k, 0]), int(E[k, 1])
        if u in touched or v in touched:
            continue
        if (u in border) != (v in border):
            keep_pos = p[u] if u in border else p[v]   # slide the inner one onto the border
        elif u in border and (u, v) not in border_edges:
            continue                                   # two border vertices across the cloth
        else:
            # an inner edge, or a short edge ALONG the border: a hem's tiny
            # original edges are as stiff and light as any (and kept them NaN)
            keep_pos = 0.5 * (p[u] + p[v])
        ring = set(v2f.get(u, [])) | set(v2f.get(v, []))
        ok = True
        for g in ring:
            f = t[g]
            if u in f and v in f:
                continue
            q = [keep_pos if int(x) in (u, v) else p[int(x)] for x in f]
            n = np.cross(q[1] - q[0], q[2] - q[0])
            ln = np.linalg.norm(n)
            if ln < 1e-18 or n @ nrm[g] / ln < 0.5:
                ok = False
                break
        if not ok:
            continue
        p[u] = keep_pos
        target[v] = u
        touched |= {u, v} | {int(x) for g in ring for x in t[g]}
    t = target[t]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    return p, t


def _relax(p, t, rounds=3, lam=0.5):
    """Tangential smoothing: each inner vertex moves toward its neighbours'
    average within its tangent plane, evening the triangles without
    shrinking the garment; the border stays put."""
    border = _boundary_vertices(t)
    E = _edges(t)
    nbr = [[] for _ in range(len(p))]
    for a, b in E:
        nbr[a].append(b)
        nbr[b].append(a)
    for _ in range(rounds):
        vn = np.zeros_like(p)
        fn = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        for i in range(3):
            np.add.at(vn, t[:, i], fn)
        vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-18)
        new = p.copy()
        for i, nb in enumerate(nbr):
            if i in border or not nb:
                continue
            d = p[nb].mean(0) - p[i]
            d -= (d @ vn[i]) * vn[i]
            new[i] = p[i] + lam * d
        p = new
    return p


def remesh_isotropic(p, t, target_vertices, iterations=6):
    """Botsch-Kobbelt style: split long edges, collapse short ones, relax,
    toward one edge length L that gives about target_vertices."""
    import trimesh.remesh

    area = float(0.5 * np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), axis=1).sum())
    L = (2.0 * area / (target_vertices * np.sqrt(3) / 2)) ** 0.5 / np.sqrt(2)
    for _ in range(iterations):
        p, t = trimesh.remesh.subdivide_to_size(p, t, max_edge=4.0 / 3.0 * L)
        p, t = _clean(np.asarray(p, float), np.asarray(t, np.int64))
        p, t = _collapse_short(p, t, 0.8 * L)
        p, t = _clean(p, t)
        p = _relax(p, t)
    return p, t


def _unfold(p, t, cos_limit=-0.9, passes=10):
    """Drop flaps folded back onto their neighbour (adjacent faces whose
    normals oppose: a ruffle or a doubled hem pressed flat, or a flip left by
    decimation). VBD's bending term is singular at a 180 degree fold: these
    are what made clean-looking proxies NaN."""
    for _ in range(passes):
        n = _normals(p, t)
        area = 0.5 * np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), axis=1)
        e2f = {}
        for fi, f in enumerate(t):
            for i in range(3):
                e2f.setdefault((min(f[i], f[(i + 1) % 3]), max(f[i], f[(i + 1) % 3])), []).append(fi)
        drop = set()
        for fs in e2f.values():
            if len(fs) == 2 and n[fs[0]] @ n[fs[1]] < cos_limit:
                drop.add(fs[0] if area[fs[0]] < area[fs[1]] else fs[1])
        if not drop:
            break
        t = t[[i for i in range(len(t)) if i not in drop]]
        p, t = _clean(p, t)
    return p, t


def _orient(p, t):
    """Consistent winding across the piece: a face wound against its
    neighbours reads as a 180 degree fold to the bending term."""
    import trimesh

    m = trimesh.Trimesh(p, t, process=False)
    trimesh.repair.fix_winding(m)
    return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)


def make_proxy(points, tris, target_vertices=TARGET_VERTICES, min_deg=MIN_ANGLE_DEG):
    import fast_simplification

    p, t = _clean(points, tris)
    if len(p) > 4 * target_vertices:
        # cut the bulk first; the remesh then evens what is left
        p, t = fast_simplification.simplify(p, t.astype(np.int32), target_count=8 * target_vertices)
        p, t = _clean(np.asarray(p, dtype=float), np.asarray(t, dtype=np.int64))
    p, t = _unfold(p, t)
    p, t = remesh_isotropic(p, t, target_vertices)
    p, t = _repair_slivers(p, t, min_deg, passes=3)
    p, t = _clean(p, t)
    p, t = _unfold(p, t)
    p, t = _orient(p, t)
    return p, t


def _folded(p, t, cos_limit=-0.9):
    n = _normals(p, t)
    e2f = {}
    for fi, f in enumerate(t):
        for i in range(3):
            e2f.setdefault((min(f[i], f[(i + 1) % 3]), max(f[i], f[(i + 1) % 3])), []).append(fi)
    return sum(1 for fs in e2f.values() if len(fs) == 2 and n[fs[0]] @ n[fs[1]] < cos_limit)


def _consistent(t) -> bool:
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    return len(np.unique(e, axis=0)) == len(e)


def quality(p, t) -> dict:
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    cnt = Counter(map(tuple, e))
    return {"vertices": int(len(p)), "triangles": int(len(t)),
            "non_manifold_edges": int(sum(1 for v in cnt.values() if v > 2)),
            "min_angle_deg_p1": round(float(np.percentile(_min_angles(p, t), 1)), 2),
            "folded_edges": int(_folded(p, t)), "winding_consistent": _consistent(t)}


def write_proxy(entry: dict, p, t) -> str:
    """The proxy as <asset>/ClothProxy in the derivative: invisible guide
    geometry in the asset root's local space."""
    from pxr import Gf, Usd, UsdGeom, Vt

    stage = Usd.Stage.Open(entry["file"])
    root = stage.GetDefaultPrim().GetChildren()
    root = next(c for c in root if c.IsA(UsdGeom.Xformable) and c.GetName() not in ("Materials", "Looks"))
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    to_local = UsdGeom.XformCache().GetLocalToWorldTransform(root).GetInverse()
    mesh = UsdGeom.Mesh.Define(stage, root.GetPath().AppendChild("ClothProxy"))
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(to_local.Transform(Gf.Vec3d(*(v / mpu)))) for v in p]))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(t)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([int(i) for i in t.reshape(-1)]))
    mesh.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
    mesh.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    root.SetCustomDataByKey("simReady:clothProxy", str(mesh.GetPath()))
    stage.GetRootLayer().Save()
    return str(mesh.GetPath())


def proxy_mesh(entry: dict):
    """The stored proxy in world metres, or None."""
    from pxr import Usd, UsdGeom

    path = (entry.get("cloth_proxy") or {}).get("prim")
    if not path:
        return None
    stage = Usd.Stage.Open(entry["file"])
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        return None
    m = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(prim), dtype=float)
    pts = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
    pts = (pts @ m[:3, :3] + m[3, :3]) * UsdGeom.GetStageMetersPerUnit(stage)
    t = np.array(UsdGeom.Mesh(prim).GetFaceVertexIndicesAttr().Get(), dtype=np.int64).reshape(-1, 3)
    return pts, t


def build(asset_id: str) -> str:
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO))
    from ingest_asset import FIXED_DIR, build_wrapper

    if not str(entry["file"]).startswith(str(FIXED_DIR)):
        # a soft asset has no derivative yet (rigid physics was skipped):
        # make one - the proxy is never written into the downloaded source
        entry.setdefault("original_file", entry["file"])
        entry["file"] = build_wrapper(entry, None)
    pts, tris = posed_mesh(entry["file"])
    before = {"vertices": int(len(pts)), "triangles": int(len(tris))}
    p, t = make_proxy(pts, tris)
    q = quality(p, t)
    path = write_proxy(entry, p, t)
    entry["cloth_proxy"] = {"prim": path, "source": before, **q}
    entry.setdefault("applied_fixes", []).append(
        f"cloth proxy: {before['vertices']} -> {q['vertices']} vertices, manifold, slivers collapsed")
    qf.write_text(json.dumps(entry, indent=1))
    return f"{asset_id}: {before['vertices']} v -> {q}"


if __name__ == "__main__":
    for a in sys.argv[1:]:
        try:
            print(build(a))
        except Exception as ex:  # noqa: BLE001
            print(f"ERROR {a}: {ex}")
