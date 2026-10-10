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
    python scripts/segment_mesh.py --box <queue_asset_id> <mesh_name> \
        <xmin ymin zmin> <xmax ymax zmax> <inside_name> <outside_name>
                                    # split by world box ('-' = open side)
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
    from pxr import UsdGeom

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
    base_name = mesh_prim.GetName()
    return _author_parts(stage, mesh_prim, [
        (f"{base_name}_part{ci:02d}", faces)
        for ci, faces in enumerate(sorted(comps, key=len, reverse=True))])


def split_mesh_by_box(stage, mesh_path: str, box_min, box_max,
                      inside_name: str, outside_name: str) -> list[str]:
    """Split a mesh whose parts share vertices, so connectivity cannot
    separate them (a door leaf modelled into its frame).

    Faces with every vertex inside the world-space box [box_min, box_max]
    become `inside_name`; the rest become `outside_name`. Use None in a
    bound for an open side. Returns the new prim paths.
    """
    from pxr import Gf, UsdGeom

    mesh_prim = stage.GetPrimAtPath(mesh_path)
    mesh = UsdGeom.Mesh(mesh_prim)
    if not mesh:
        raise RuntimeError(f"not a Mesh: {mesh_path}")
    points = list(mesh.GetPointsAttr().Get() or [])
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
    to_world = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(0)
    world = [to_world.Transform(Gf.Vec3d(*p)) for p in points]

    def inside(p) -> bool:
        return all((box_min[k] is None or p[k] >= box_min[k]) and
                   (box_max[k] is None or p[k] <= box_max[k]) for k in range(3))

    offs = _face_offsets(counts)
    groups: dict[bool, list[int]] = {True: [], False: []}
    for f, c in enumerate(counts):
        groups[all(inside(world[indices[offs[f] + k]]) for k in range(c))].append(f)
    if not groups[True] or not groups[False]:
        raise RuntimeError(f"box keeps {len(groups[True])} of {len(counts)} faces — "
                           "nothing to split")
    return _author_parts(stage, mesh_prim, [(inside_name, groups[True]),
                                            (outside_name, groups[False])])


def _author_parts(stage, mesh_prim, groups, under=None, transform=None) -> list[str]:
    """Author one Mesh prim per (name, faces) group next to `mesh_prim`,
    carrying its attributes; deactivate the original. `under` puts the parts
    beneath another prim instead, with `transform` (their placement below
    it, a Gf.Matrix4d) in place of the source's own xform ops."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade, UsdSkel, Vt

    mesh = UsdGeom.Mesh(mesh_prim)
    points = list(mesh.GetPointsAttr().Get() or [])
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
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
    parent_path = Sdf.Path(under) if under else mesh_prim.GetParent().GetPath()
    new_paths = []
    for part_name, faces in groups:
        part_path = parent_path.AppendChild(part_name)
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
        # an extent: under a SkelRoot, a part without one bounds to a point
        # (the whole asset measured 0 m)
        part.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(part.GetPointsAttr().Get()))
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
                elif interp == UsdGeom.Tokens.vertex and len(vals) == len(points) * pv.GetElementSize():
                    # several values a point (skinning's joint indices and weights)
                    es = pv.GetElementSize()
                    npv.Set([vals[i * es + k] for i in used for k in range(es)])
                    npv.SetElementSize(es)
                elif interp == UsdGeom.Tokens.uniform and len(vals) == len(counts):
                    npv.Set([vals[f] for f in faces])
                else:  # constant or unknown layout — copy as-is
                    npv.Set(vals)
            except Exception:
                continue
        if material:
            UsdShade.MaterialBindingAPI.Apply(part.GetPrim()).Bind(material)
        # a skinned mesh's binding to its skeleton (without it the part is not
        # skinned, and a SkelRoot bounds over skinned meshes only)
        if mesh_prim.HasAPI(UsdSkel.BindingAPI):
            src, dst = UsdSkel.BindingAPI(mesh_prim), UsdSkel.BindingAPI.Apply(part.GetPrim())
            if src.GetGeomBindTransformAttr().HasAuthoredValue():
                dst.CreateGeomBindTransformAttr(src.GetGeomBindTransformAttr().Get())
            if src.GetJointsAttr().HasAuthoredValue():
                dst.CreateJointsAttr(src.GetJointsAttr().Get())
            if src.GetSkeletonRel().GetTargets():
                dst.CreateSkeletonRel().SetTargets(src.GetSkeletonRel().GetTargets())
        # carry the source mesh's local transform (or the placement given)
        if transform is not None:
            xf = UsdGeom.Xformable(part.GetPrim())
            xf.ClearXformOpOrder()
            xf.AddTransformOp().Set(Gf.Matrix4d(transform))
        elif xform_ops and xform_ops.Get():
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


def split_keys(stage, mesh_path: str, up: int = 2) -> list[str]:
    """Split a mesh holding a keyboard's (or a panel's) keys into one part a key.

    Each key is several islands - the cap and its legend - stacked over one
    footprint; split_mesh's merging would weld them all back together. The
    islands are grouped by footprint (looking down the up axis, one inside
    the other), and the split is kept only when it finds keys: at least 8
    groups, most of them a similar size. Returns the new prim paths."""
    from pxr import Gf, UsdGeom

    mesh_prim = stage.GetPrimAtPath(mesh_path)
    mesh = UsdGeom.Mesh(mesh_prim)
    points = list(mesh.GetPointsAttr().Get() or [])
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
    if not counts:
        return []
    comps = mesh_components(counts, indices, points)
    if len(comps) < 8:
        return []
    m = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(0)
    world = [m.Transform(Gf.Vec3d(*p)) for p in points]
    offs = _face_offsets(counts)
    plane = [k for k in range(3) if k != up]
    boxes = []
    for c in comps:
        vs = [world[indices[offs[f] + k]] for f in c for k in range(counts[f])]
        boxes.append([(min(v[k] for v in vs), max(v[k] for v in vs)) for k in plane])

    def inside(a, b):
        """a's centre within b's footprint."""
        return all(b[i][0] <= 0.5 * (a[i][0] + a[i][1]) <= b[i][1] for i in range(2))

    area = [(b[0][1] - b[0][0]) * (b[1][1] - b[1][0]) for b in boxes]
    order = sorted(range(len(comps)), key=lambda i: -area[i])
    owner = {}
    keys = []
    for i in order:                       # largest footprints first: they are the caps
        host = next((k for k in keys if inside(boxes[i], boxes[k[0]])), None)
        if host is None:
            keys.append([i])
        else:
            host.append(i)
        owner[i] = host
    if not 8 <= len(keys) <= 160:          # a full keyboard has ~105 keys; a scan's islands run to hundreds
        return []
    import statistics
    med = statistics.median(area[k[0]] for k in keys)
    similar = sum(1 for k in keys if 0.2 * med <= area[k[0]] <= 8 * med)
    if similar < 0.6 * len(keys):
        return []
    base_name = mesh_prim.GetName()
    groups = [(f"{base_name}_key{ki:03d}", [f for i in k for f in comps[i]]) for ki, k in enumerate(keys)]
    return _author_parts(stage, mesh_prim, groups)


def unsegment(stage) -> int:
    """Undo segmentation in a derivative: remove every authored part
    (*_partNN, *_keyNNN) and reactivate the meshes they came from. Returns
    the number of parts removed."""
    import re

    from pxr import Sdf

    layer = stage.GetRootLayer()
    gone = 0

    def walk(spec):
        nonlocal gone
        for c in list(spec.nameChildren):
            if re.search(r"_(part\d+|key\d+)$", c.name):
                del spec.nameChildren[c.name]
                gone += 1
            else:
                if c.HasInfo("active") and not c.GetInfo("active"):
                    c.ClearInfo("active")
                walk(c)

    for root in list(layer.rootPrims):
        walk(root)
    _ = Sdf
    return gone


def key_split_entry(asset_id: str) -> str:
    """Split the key meshes of a queue entry's derivative (undoing an earlier
    generic segmentation that merged them), then re-check it."""
    from pxr import Usd, UsdGeom

    from ingest_asset import QUEUE_DIR, build_wrapper, propose_category, run_report

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    if "original_file" not in entry or not str(entry["file"]).endswith(".usda"):
        entry.setdefault("original_file", entry["file"])
        entry["file"] = build_wrapper(entry, None)
    stage = Usd.Stage.Open(entry["file"])
    undone = unsegment(stage)
    stage.GetRootLayer().Save()
    stage = Usd.Stage.Open(entry["file"])
    results = {}
    for prim in list(stage.Traverse()):
        if prim.IsA(UsdGeom.Mesh) and prim.IsActive():
            parts = split_keys(stage, str(prim.GetPath()))
            if parts:
                results[prim.GetName()] = len(parts)
    stage.GetRootLayer().Save()
    entry.setdefault("applied_fixes", []).append(
        "key split: " + (", ".join(f"{k} -> {v} keys" for k, v in results.items()) or "no key meshes found")
        + (f" (undid {undone} segmented parts)" if undone else ""))
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    entry["proposed_category"] = propose_category(entry["report"])
    qf.write_text(json.dumps(entry, indent=1))
    if not results:
        return "no key meshes found"
    return "split " + ", ".join(f"{k} into {v} keys" for k, v in results.items())


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


MERGED_MOVER = None


def merged_targets(survey: dict) -> list[str]:
    """Meshes the survey says hold a moving part moulded into another: a
    reference part modelled into #n, or a role naming a moving piece along with
    the fixed one ("main beam with fixed jaw and sliding jaw")."""
    import re

    global MERGED_MOVER
    if MERGED_MOVER is None:
        MERGED_MOVER = re.compile(
            r"\b(and|with|plus)\b.*\b(sliding|moving|hinged|rotating|removable|folding|swivel\w*|lid|door|"
            r"flap|jaw|trigger|button|knob|dial|wheel|drawer|tray|handle|lever|cap|cover)\b", re.I)
    by_id = {p["id"]: p for p in survey.get("parts", [])}
    ids = {u["merged_into"] for u in survey.get("unmatched_reference") or [] if u.get("merged_into")}
    ids |= {p["id"] for p in survey.get("parts", []) if p.get("motion") in (None, "none")
            and MERGED_MOVER.search(p.get("role") or "")}
    out = []
    for i in sorted(ids):
        p = by_id.get(i)
        if not p:
            continue
        for m in [p["path"]] + (p.get("copies") or []):
            if p and m not in out:
                out.append(m)
    return out


def split_targets_entry(asset_id: str, paths: list[str]) -> str:
    """Split just these meshes into their connected parts (each kept only when
    it has more than one sizeable island). Returns '' when nothing split."""
    from pxr import Usd

    from ingest_asset import QUEUE_DIR, run_report

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    from processing import owned
    if not owned(entry["file"]):
        return ""
    stage = Usd.Stage.Open(entry["file"])
    done = {}
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsActive() or not prim.GetAttribute("faceVertexCounts").Get():
            continue
        try:
            parts = split_mesh(stage, path)
        except Exception:  # noqa: BLE001 - one mesh that will not split is not the end
            continue
        if parts:
            done[prim.GetName()] = len(parts)
    if not done:
        return ""
    stage.GetRootLayer().Save()
    entry = json.loads(qf.read_text())
    entry.setdefault("applied_fixes", []).append(
        "targeted segmentation (a moving part moulded in): " + ", ".join(f"{k} -> {v} parts" for k, v in done.items()))
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    qf.write_text(json.dumps(entry, indent=1))
    return "split " + ", ".join(f"{k} into {v}" for k, v in done.items())


def box_split_entry(asset_id: str, mesh_name: str, box_min, box_max,
                    inside_name: str, outside_name: str) -> str:
    """Box-split one mesh of a queue entry's derivative, then re-check it."""
    from pxr import Usd

    from ingest_asset import QUEUE_DIR, propose_category, render_thumbnail, run_report

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    stage = Usd.Stage.Open(entry["file"])
    matches = [p for p in stage.Traverse() if p.GetName() == mesh_name and p.IsActive()]
    if len(matches) != 1:
        raise RuntimeError(f"{len(matches)} active prims named {mesh_name!r}")
    parts = split_mesh_by_box(stage, str(matches[0].GetPath()), box_min, box_max,
                              inside_name, outside_name)
    stage.GetRootLayer().Save()
    entry.setdefault("applied_fixes", []).append(
        f"box split: {mesh_name} -> {inside_name} + {outside_name}")
    entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    entry["proposed_category"] = propose_category(entry["report"])
    entry["thumbnail"] = render_thumbnail(entry["file"], asset_id)
    qf.write_text(json.dumps(entry, indent=1))
    return f"split {mesh_name} into {', '.join(p.rsplit('/', 1)[-1] for p in parts)}"


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
    if args[0] == "--box":
        bound = lambda v: None if v == "-" else float(v)  # noqa: E731
        asset_id, mesh_name = args[1], args[2]
        lo, hi = [bound(v) for v in args[3:6]], [bound(v) for v in args[6:9]]
        print(box_split_entry(asset_id, mesh_name, lo, hi, args[9], args[10]))
        return 0
    print(segment_entry(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
