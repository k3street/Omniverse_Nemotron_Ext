#!/usr/bin/env python3
"""Mesh segmentation for baked assets (BACKLOG #4).

Many scan assets fuse separate mechanical parts into single meshes (an
office chair as one mesh; a wheelchair whose left+right wheels share one
mesh). Articulation is impossible until the parts are separate prims.

This splits a fused UsdGeom.Mesh into its connected components — geometry
islands that share no vertices — authoring each as its own Mesh prim in
the asset's derivative wrapper (the referenced source is never modified;
the original mesh is deactivated by an override). Islands smaller than
MIN_FACE_FRACTION of the mesh are merged into the nearest large component
by centroid, so screws and labels ride with their parent part instead of
becoming physics bodies.

Carried per part: points/faces (reindexed), vertex- and faceVarying-
interpolated primvars (normals, UVs), uniform primvars, material binding,
and the source mesh's local transform. GeomSubsets are not carried (v1).

Usage:
    python scripts/segment_mesh.py <queue_asset_id>      # segment entry's fused meshes
    python scripts/segment_mesh.py --file <usd> --mesh </prim/path>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MIN_FACE_FRACTION = 0.01


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def mesh_components(counts, indices, points=None) -> list[list[int]]:
    """Group face indices into connected components via shared vertices.

    Scan exports often duplicate vertices per face/strip (same position,
    different index), which fragments one physical part into thousands of
    micro-islands. When points are given, exact-duplicate positions are
    welded (unioned) first so connectivity reflects the actual geometry.
    """
    n_points = (max(indices) + 1) if indices else 0
    uf = _UnionFind(n_points)
    if points is not None:
        pos_seen: dict[tuple, int] = {}
        for i in range(min(n_points, len(points))):
            key = (points[i][0], points[i][1], points[i][2])
            if key in pos_seen:
                uf.union(pos_seen[key], i)
            else:
                pos_seen[key] = i
    off = 0
    for c in counts:
        first = indices[off]
        for k in range(1, c):
            uf.union(first, indices[off + k])
        off += c
    comp_faces: dict[int, list[int]] = {}
    off = 0
    for fi, c in enumerate(counts):
        root = uf.find(indices[off])
        comp_faces.setdefault(root, []).append(fi)
        off += c
    return list(comp_faces.values())


def _face_offsets(counts):
    offs = [0]
    for c in counts:
        offs.append(offs[-1] + c)
    return offs


def _centroid(points, indices, counts, faces, offs):
    xs = ys = zs = 0.0
    n = 0
    for f in faces:
        for k in range(counts[f]):
            p = points[indices[offs[f] + k]]
            xs += p[0]
            ys += p[1]
            zs += p[2]
            n += 1
    return (xs / n, ys / n, zs / n) if n else (0.0, 0.0, 0.0)


MAX_PARTS = 32


def merge_small(components, points, indices, counts, total_faces):
    """Merge sub-threshold islands into the nearest large component, and
    cap the part count (physics parts, not render granularity)."""
    offs = _face_offsets(counts)
    big = [c for c in components if len(c) >= max(1, int(total_faces * MIN_FACE_FRACTION))]
    small = [c for c in components if c not in big]
    if not big:
        big = sorted(components, key=len, reverse=True)[:MAX_PARTS]
        small = [c for c in components if c not in big]
    cents = [_centroid(points, indices, counts, c, offs) for c in big]
    for s in small:
        sc = _centroid(points, indices, counts, s, offs)
        best = min(range(len(big)), key=lambda i: sum(
            (cents[i][k] - sc[k]) ** 2 for k in range(3)))
        big[best].extend(s)
    # cap: repeatedly merge the smallest into its nearest neighbour
    while len(big) > MAX_PARTS:
        big.sort(key=len)
        s = big.pop(0)
        sc = _centroid(points, indices, counts, s, offs)
        cents = [_centroid(points, indices, counts, c, offs) for c in big]
        best = min(range(len(big)), key=lambda i: sum(
            (cents[i][k] - sc[k]) ** 2 for k in range(3)))
        big[best].extend(s)
    return big


def split_mesh(stage, mesh_path: str) -> list[str]:
    """Author one Mesh prim per component next to the fused mesh; deactivate
    the original. Returns the new prim paths."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

    mesh_prim = stage.GetPrimAtPath(mesh_path)
    mesh = UsdGeom.Mesh(mesh_prim)
    if not mesh:
        raise RuntimeError(f"not a Mesh: {mesh_path}")
    points = list(mesh.GetPointsAttr().Get() or [])
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
    if not counts:
        raise RuntimeError("mesh has no faces")
    comps = mesh_components(counts, indices, points)
    if len(comps) < 2:
        return []
    comps = merge_small(comps, points, indices, counts, len(counts))
    if len(comps) < 2:
        return []
    offs = _face_offsets(counts)

    # primvars to carry
    pv_api = UsdGeom.PrimvarsAPI(mesh_prim)
    primvars = []
    for pv in pv_api.GetPrimvars():
        interp = pv.GetInterpolation()
        vals = pv.Get()
        if vals is None:
            continue
        primvars.append((pv, interp, list(vals),
                         list(pv.GetIndices() or []) if pv.IsIndexed() else None))
    normals = mesh.GetNormalsAttr().Get()
    normals = list(normals) if normals else None
    normals_interp = mesh.GetNormalsInterpolation() if normals else None

    binding = UsdShade.MaterialBindingAPI(mesh_prim).GetDirectBinding()
    material = binding.GetMaterial() if binding else None

    xform_ops = mesh_prim.GetAttribute("xformOpOrder")
    parent_path = mesh_prim.GetParent().GetPath()
    base_name = mesh_prim.GetName()
    new_paths = []
    for ci, faces in enumerate(sorted(comps, key=len, reverse=True)):
        part_path = parent_path.AppendChild(f"{base_name}_part{ci:02d}")
        part = UsdGeom.Mesh.Define(stage, part_path)
        # point remap
        used = []
        seen = {}
        new_indices = []
        new_counts = []
        fv_sel = []  # face-vertex flat indices kept, for faceVarying remap
        for f in faces:
            new_counts.append(counts[f])
            for k in range(counts[f]):
                flat = offs[f] + k
                fv_sel.append(flat)
                pi = indices[flat]
                if pi not in seen:
                    seen[pi] = len(used)
                    used.append(pi)
                new_indices.append(seen[pi])
        part.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*points[i]) for i in used]))
        part.CreateFaceVertexCountsAttr(Vt.IntArray(new_counts))
        part.CreateFaceVertexIndicesAttr(Vt.IntArray(new_indices))
        # normals
        if normals:
            if normals_interp == UsdGeom.Tokens.faceVarying:
                part.CreateNormalsAttr(Vt.Vec3fArray(
                    [Gf.Vec3f(*normals[i]) for i in fv_sel if i < len(normals)]))
            elif normals_interp == UsdGeom.Tokens.vertex and len(normals) == len(points):
                part.CreateNormalsAttr(Vt.Vec3fArray(
                    [Gf.Vec3f(*normals[i]) for i in used]))
            if normals_interp:
                part.SetNormalsInterpolation(normals_interp)
        # primvars
        part_pv = UsdGeom.PrimvarsAPI(part.GetPrim())
        for pv, interp, vals, pv_idx in primvars:
            name = pv.GetPrimvarName()
            tname = pv.GetTypeName()
            npv = part_pv.CreatePrimvar(name, tname, interp)
            try:
                if pv_idx is not None:
                    # indexed primvar: keep the value table, remap indices
                    if interp == UsdGeom.Tokens.faceVarying:
                        npv.Set(vals)
                        npv.SetIndices(Vt.IntArray([pv_idx[i] for i in fv_sel
                                                    if i < len(pv_idx)]))
                    elif interp == UsdGeom.Tokens.vertex:
                        npv.Set(vals)
                        npv.SetIndices(Vt.IntArray([pv_idx[i] for i in used
                                                    if i < len(pv_idx)]))
                    else:
                        npv.Set(vals)
                elif interp == UsdGeom.Tokens.faceVarying:
                    npv.Set([vals[i] for i in fv_sel if i < len(vals)])
                elif interp == UsdGeom.Tokens.vertex and len(vals) == len(points):
                    npv.Set([vals[i] for i in used])
                elif interp == UsdGeom.Tokens.uniform and len(vals) == len(counts):
                    npv.Set([vals[f] for f in faces])
                else:  # constant or unknown layout — copy as-is
                    npv.Set(vals)
            except Exception:
                continue
        if material:
            UsdShade.MaterialBindingAPI.Apply(part.GetPrim()).Bind(material)
        # carry the source mesh's local transform
        if xform_ops and xform_ops.Get():
            for op_name in xform_ops.Get():
                src_attr = mesh_prim.GetAttribute(str(op_name))
                if src_attr and src_attr.HasValue():
                    part.GetPrim().CreateAttribute(
                        str(op_name), src_attr.GetTypeName()).Set(src_attr.Get())
            part.GetPrim().CreateAttribute(
                "xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(xform_ops.Get())
        new_paths.append(str(part_path))
    mesh_prim.SetActive(False)
    return new_paths


def segment_entry(asset_id: str) -> str:
    """Segment every multi-component mesh in a queue entry's derivative."""
    from pxr import Usd, UsdGeom

    from ingest_asset import QUEUE_DIR, build_wrapper, run_report

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if "original_file" not in entry or not str(entry["file"]).endswith(".usda"):
        entry.setdefault("original_file", entry["file"])
        entry["file"] = build_wrapper(entry, None)
        entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    stage = Usd.Stage.Open(entry["file"])
    results = {}
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue
        counts = prim.GetAttribute("faceVertexCounts").Get()
        if not counts:
            continue
        try:
            parts = split_mesh(stage, str(prim.GetPath()))
        except Exception:
            continue
        if parts:
            results[prim.GetName()] = len(parts)
    stage.GetRootLayer().Save()
    entry.setdefault("applied_fixes", []).append(
        "mesh segmentation: " + (", ".join(f"{k} -> {v} parts"
                                           for k, v in results.items()) or "no fused meshes found"))
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    from ingest_asset import propose_category, render_thumbnail
    entry["proposed_category"] = propose_category(entry["report"])
    entry["thumbnail"] = render_thumbnail(entry["file"], asset_id)
    qf.write_text(json.dumps(entry, indent=1))
    if not results:
        return "no multi-component meshes found — nothing to segment"
    return ("segmented " + ", ".join(f"{k} into {v} parts" for k, v in results.items())
            + f"; asset now has {entry['report'].get('structure', {}).get('meshes')} meshes")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--file":
        from pxr import Usd
        stage = Usd.Stage.Open(args[1])
        parts = split_mesh(stage, args[3] if len(args) > 3 else args[2])
        stage.GetRootLayer().Save()
        print(f"{len(parts)} parts: {parts}")
        return 0
    print(segment_entry(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
