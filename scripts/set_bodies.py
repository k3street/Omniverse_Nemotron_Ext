#!/usr/bin/env python3
"""One rigid body per object of a set, read from the part survey (pxr only).

Ingest gives a set (chess pieces on a board, dominoes in a box) a body per
child of the meshes' common ancestor. That is the modeller's grouping, not
the objects': a set with its black and white pieces under two groups got two
bodies of sixteen pieces; a set whose eight pawns are one mesh got one pawn
body; a board modelled as a slab, two layers of squares and thirty
coordinate decals got thirty-three bodies. The survey knows what each mesh
is, so once it exists the bodies follow it:

  pieces   meshes the survey calls a piece (a king, a domino, a bottle); a
           mesh holding several (a row of pawns) is split into its islands
           first; meshes whose boxes overlap are one piece (a knight's
           mane, a felt pad under a base, a crown decal)
  surface  the board, its frame, inlays and labels: one body, a box
  other    what the survey names neither: joins the body whose box holds
           it, else is a body of its own

A body sits on the deepest prim that holds its meshes and no other body's;
when there is none (a board's layers modelled as siblings of the pieces)
the meshes are re-authored under one Xform, world placement kept.

    python scripts/set_bodies.py <asset_id> [--plan]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

PIECE = re.compile(r"\b(king|queen|bishop|knight|rook|castle|pawn|piece|figure|figurine|token|"
                   r"domino|checker|die|dice|bottle|jar|cup|glass|can)(s|es)?\b", re.I)
SURFACE = re.compile(r"\b(board|slab|playing (?:surface|field|grid|area)|squares?|tiles?|inlay|frame|trim|edging|"
                     r"tray|border|rim|base plate|label|marking|coordinates?|rack|stand|holder|box|case)\b", re.I)
SQUARES = re.compile(r"\b(squares?|inlay|playing (?:surface|field|grid|area)|grid|checker(?:ed)?)\b", re.I)
RIM = re.compile(r"\b(frame|trim|edging|border|rim|tray|base plate|plinth|label|marking|coordinates?)\b", re.I)

# the survey's motion note and where a thing sits: "king piece - lifted and
# placed on squares" is a piece, "felt pad under a piece base" a pad
NOTE = re.compile(r"\s+[-\u2013]\s.*$")
WHERE = re.compile(r"\b(on|onto|over|across|along|under|beneath|from|off|into|inside|within|atop|top of)\s+"
                   r"(the\s+|a\s+|an\s+|its\s+|each\s+|every\s+)?(\w+\s+){0,2}"
                   r"(board|squares?|surface|grid|field|tray|piece|pieces|base|king|queen|bishop|knight|rook|pawn)s?\b",
                   re.I)

OURS = re.compile(r"Board|Piece_\d+")         # the Xforms relocate() defines
PART = re.compile(r"^(.+)_part\d\d$")           # a mesh split_mesh() authored beside its source

PHYSICS_APIS = ("PhysicsArticulationRootAPI", "PhysxArticulationAPI", "PhysicsRigidBodyAPI",
                "PhysicsCollisionAPI", "PhysicsMeshCollisionAPI", "PhysicsMassAPI",
                "PhysicsFilteredPairsAPI", "PhysxCollisionAPI", "PhysxRigidBodyAPI")


def roles_of(survey: dict | None) -> dict[str, str]:
    """mesh path -> the survey's role for it (members and copies alike)."""
    out = {}
    for p in (survey or {}).get("parts", []):
        for m in [p.get("path")] + list(p.get("members") or []) + list(p.get("copies") or []):
            if m:
                out[m] = p.get("role") or ""
    return out


def core(role: str) -> str:
    """The role without its motion note and its "on the board" phrases."""
    return WHERE.sub(" ", NOTE.sub("", role or ""))


def kind_of(role: str) -> str | None:
    """'piece', 'surface', or None when the role says both or neither."""
    text = core(role)
    piece, surface = bool(PIECE.search(text)), bool(SURFACE.search(text))
    if piece and not surface:
        return "piece"
    if surface and not piece:
        return "surface"
    return None


def _boxes(stage, root):
    from pxr import Usd, UsdGeom

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    out = {}
    for p in Usd.PrimRange(stage.GetPrimAtPath(root)):
        if p.IsA(UsdGeom.Mesh) and p.IsActive():
            r = cache.ComputeWorldBound(p).ComputeAlignedRange()
            if not r.IsEmpty():
                out[str(p.GetPath())] = (tuple(r.GetMin()), tuple(r.GetMax()))
    return out


def _overlap(a, b, margin=0.0005, gap=0.005):
    """Fraction of the smaller footprint the two boxes share, when they also
    meet in height (a gap of up to `gap`, a finial modelled floating over
    its crown, still counts); 0 otherwise."""
    (alo, ahi), (blo, bhi) = a, b
    if min(ahi[2], bhi[2]) - max(alo[2], blo[2]) < -gap:
        return 0.0
    ix = min(ahi[0], bhi[0]) - max(alo[0], blo[0]) + margin
    iy = min(ahi[1], bhi[1]) - max(alo[1], blo[1]) + margin
    if ix <= 0 or iy <= 0:
        return 0.0
    fa = max(ahi[0] - alo[0], 1e-6) * max(ahi[1] - alo[1], 1e-6)
    fb = max(bhi[0] - blo[0], 1e-6) * max(bhi[1] - blo[1], 1e-6)
    return (ix * iy) / min(fa, fb)


def _union(boxes):
    lo = tuple(min(b[0][k] for b in boxes) for k in range(3))
    hi = tuple(max(b[1][k] for b in boxes) for k in range(3))
    return lo, hi


def _holds_several(stage, prim) -> bool:
    """A piece mesh holding several pieces: islands alike in size (a row of
    pawns, a pair of rooks) standing apart, not a figure whose wings and
    body are islands of their own."""
    from pxr import Gf, UsdGeom

    from segment_mesh import merge_small, mesh_components

    mesh = UsdGeom.Mesh(prim)
    points = list(mesh.GetPointsAttr().Get() or [])
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
    if not counts:
        return False
    comps = mesh_components(counts, indices, points)
    if len(comps) < 2:
        return False
    comps = merge_small(comps, points, indices, counts, len(counts))
    if len(comps) < 2:
        return False
    sizes = sorted(len(c) for c in comps)
    if sizes[-1] > 2.0 * sizes[0]:
        return False
    M = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
    offs, boxes = [0], []
    for c in counts:
        offs.append(offs[-1] + c)
    for comp in comps:
        pts = [M.Transform(Gf.Vec3d(*points[indices[offs[f] + k]])) for f in comp for k in range(counts[f])]
        boxes.append((tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3))))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _overlap(boxes[i], boxes[j]) >= 0.3:
                return False
    return True


def split_merged(stage, survey: dict, roles: dict[str, str]) -> dict[str, list[str]]:
    """Split every piece mesh that holds several pieces (a row of pawns as
    one mesh) and point the survey at the parts. Returns old -> new paths."""
    from segment_mesh import split_mesh

    done = {}
    for path, role in list(roles.items()):
        if kind_of(role) != "piece":
            continue
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsActive() or not prim.GetAttribute("faceVertexCounts").Get():
            continue
        try:
            parts = split_mesh(stage, path) if _holds_several(stage, prim) else []
        except Exception:  # noqa: BLE001 - a mesh that will not split stays one piece
            continue
        if parts:
            done[path] = parts
    for old, new in done.items():
        for p in survey.get("parts", []):
            if old in (p.get("members") or []) or old in (p.get("copies") or []) or p.get("path") == old:
                p["members"] = [m for m in (p.get("members") or []) if m != old] + new
                if old in (p.get("copies") or []) or p.get("path") == old:
                    p["copies"] = [m for m in (p.get("copies") or []) if m != old] + new
                if p.get("path") == old:
                    p["path"] = new[0]
                p["split"] = {**(p.get("split") or {}), old: new}
    return done


def _area(box):
    return max(box[1][0] - box[0][0], 1e-6) * max(box[1][1] - box[0][1], 1e-6)


def plan(stage, root: str, survey: dict) -> list[dict]:
    """Bodies as [{name, kind, meshes}], pieces ordered across the set."""
    from pxr import Sdf

    roles = roles_of(survey)
    part_of = {m: p["id"] for p in survey.get("parts", [])
               for m in [p.get("path")] + list(p.get("members") or []) + list(p.get("copies") or []) if m}
    boxes = _boxes(stage, root)
    kinds = {m: kind_of(roles.get(m, "")) if m in roles else None for m in boxes}

    # a modeller groups one object's meshes under one prim: a mesh among a
    # few piece meshes, within their footprint, is that piece's (the survey
    # once called a king's base disc a plaque on the board frame)
    siblings: dict[str, list[str]] = {}
    for m in boxes:
        siblings.setdefault(str(Sdf.Path(m).GetParentPath()), []).append(m)
    for m, k in list(kinds.items()):
        if k == "piece":
            continue
        sibs = [x for x in siblings[str(Sdf.Path(m).GetParentPath())] if x != m and kinds[x] == "piece"]
        if sibs and len(siblings[str(Sdf.Path(m).GetParentPath())]) <= 8 \
                and _overlap(boxes[m], _union([boxes[x] for x in sibs])) >= 0.6 \
                and _area(boxes[m]) <= 1.5 * _area(_union([boxes[x] for x in sibs])):
            kinds[m] = "piece"
    pieces = [m for m, k in kinds.items() if k == "piece"]
    surface = [m for m, k in kinds.items() if k == "surface"]
    loose = [m for m, k in kinds.items() if k is None]
    # a "plaque on the board frame" standing at the height of a king's
    # crown is on a king: the surface is what lies at the slab
    if surface:
        slab = max(surface, key=lambda m: _area(boxes[m]))
        top = boxes[slab][1][2]
        high = [m for m in surface if boxes[m][0][2] > top + 0.01]
        surface = [m for m in surface if m not in high]
        loose += high

    # pieces: meshes a modeller grouped together (a prim holding a few
    # meshes: a pawn's two halves, a knight's head on its plinth) join when
    # their boxes touch; across groups a mesh whose footprint lies within
    # another's is part of it (a mane, a finial, a pad), unless the survey
    # lists them as copies of one part - distinct objects however their
    # boxes meet (winged figures reach over the next square)
    under: dict[str, int] = {}
    for m in boxes:
        q = Sdf.Path(m).GetParentPath()
        while q.HasPrefix(Sdf.Path(root)):
            under[str(q)] = under.get(str(q), 0) + 1
            q = q.GetParentPath()
    parent = {m: m for m in pieces}

    def find(m):
        while parent[m] != m:
            parent[m] = parent[parent[m]]
            m = parent[m]
        return m

    def joins(a, b):
        common = Sdf.Path(a).GetParentPath()
        while not Sdf.Path(b).HasPrefix(common):
            common = common.GetParentPath()
        if under.get(str(common), 99) <= 8 and _overlap(boxes[a], boxes[b], margin=0.001) > 0:
            return True
        same = part_of.get(a) is not None and part_of.get(a) == part_of.get(b)
        return not same and _overlap(boxes[a], boxes[b]) >= 0.6

    for i, a in enumerate(pieces):
        for b in pieces[i + 1:]:
            if joins(a, b):
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for m in pieces:
        groups.setdefault(find(m), []).append(m)
    piece_groups = list(groups.values())
    surface_box = _union([boxes[m] for m in surface]) if surface else None

    # the rest joins the body its box lies in: a smaller thing on a piece is
    # the piece's; a flat thing on the board is the board's
    others = []
    for m in loose:
        lo, hi = boxes[m]
        flat = min(hi[k] - lo[k] for k in range(3)) < 0.002

        def within(g):
            gb = _union([boxes[x] for x in g])
            return _overlap(boxes[m], gb) if _area(boxes[m]) <= 1.5 * _area(gb) else 0.0

        best = max(piece_groups, key=within, default=None)
        if best is not None and within(best) >= 0.6:
            best.append(m)
        elif surface_box and _overlap(boxes[m], surface_box) >= 0.5 and (flat or not piece_groups
                                                                           or _area(boxes[m]) > 0.02 * _area(surface_box)):
            surface.append(m)
        else:
            others.append(m)

    def centre(g):
        lo, hi = _union([boxes[x] for x in g])
        return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)

    piece_groups.sort(key=lambda g: (round(centre(g)[1], 3), round(centre(g)[0], 3)))
    out = [{"name": f"Piece_{i + 1:02d}", "kind": "piece", "meshes": g} for i, g in enumerate(piece_groups)]
    if surface:
        out.append({"name": "Board", "kind": "surface", "meshes": surface})
    for m in others:
        out.append({"name": Path(m).name, "kind": "other", "meshes": [m]})
    return out


def carrier(stage, root: str, meshes: list[str], taken: set[str]):
    """The deepest prim under root holding these meshes and no other body's
    (None when the meshes have to be re-authored together)."""
    from pxr import Sdf, Usd, UsdGeom

    paths = [Sdf.Path(m) for m in meshes]
    common = paths[0]
    while not all(q.HasPrefix(common) for q in paths):
        common = common.GetParentPath()
    if not common.HasPrefix(Sdf.Path(root)) or common == Sdf.Path(root):
        return None
    prim = stage.GetPrimAtPath(common)
    mine = set(meshes)
    for q in Usd.PrimRange(prim):
        if q.IsA(UsdGeom.Mesh) and q.IsActive() and str(q.GetPath()) not in mine and str(q.GetPath()) in taken:
            return None
    return prim


def relocate(stage, parent_path: str, name: str, meshes: list[str], moved: dict | None = None) -> str:
    """Re-author these meshes under <parent>/<name> (world placement kept),
    deactivate the originals; returns the new Xform's path. `moved` collects
    old path -> new path."""
    from pxr import Sdf, UsdGeom

    from segment_mesh import _author_parts

    from pxr import UsdShade

    xf_path = Sdf.Path(parent_path).AppendChild(name)
    UsdGeom.Xform.Define(stage, xf_path)
    cache = UsdGeom.XformCache()
    to_parent = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(parent_path)).GetInverse()
    used = set()
    for m in meshes:
        prim = stage.GetPrimAtPath(m)
        counts = prim.GetAttribute("faceVertexCounts").Get() or []
        base = prim.GetName()
        nm, n = base, 1
        while nm in used:
            n += 1
            nm = f"{base}_{n}"
        used.add(nm)
        local = cache.GetLocalToWorldTransform(prim) * to_parent
        new = _author_parts(stage, prim, [(nm, list(range(len(counts))))], under=xf_path, transform=local)
        if moved is not None:
            moved[m] = new[0]
        # every face kept in order: the material subsets carry over as they are
        for sub in [UsdGeom.Subset(c) for c in prim.GetChildren() if c.IsA(UsdGeom.Subset)]:
            dst = UsdGeom.Subset.Define(stage, Sdf.Path(new[0]).AppendChild(sub.GetPrim().GetName()))
            dst.CreateElementTypeAttr(sub.GetElementTypeAttr().Get())
            dst.CreateIndicesAttr(sub.GetIndicesAttr().Get())
            if sub.GetFamilyNameAttr().Get():
                dst.CreateFamilyNameAttr(sub.GetFamilyNameAttr().Get())
            bound = UsdShade.MaterialBindingAPI(sub.GetPrim()).GetDirectBinding().GetMaterial()
            if bound:
                UsdShade.MaterialBindingAPI.Apply(dst.GetPrim()).Bind(bound)
    return str(xf_path)


def undo(stage, entry: dict) -> int:
    """Take back what an earlier apply() authored: meshes moved under new
    Xforms go back to their originals, split parts to their mesh, and the
    survey follows. Returns how many prims were put back."""
    from pxr import Sdf, Usd, UsdGeom

    survey = entry.get("part_survey") or {}
    moved = (entry.get("set_bodies") or {}).get("moved") or {}
    n = 0
    # whatever the records say: the Xforms this module defines (Board,
    # Piece_NN, in the derivative itself) go, and every surveyed mesh that
    # was switched off comes back
    root = survey.get("root")
    top = stage.GetPrimAtPath(root) if root else None
    layer = stage.GetRootLayer()
    ours = [q.GetPath() for q in (Usd.PrimRange(top) if top else [])
            if q.IsA(UsdGeom.Xform) and OURS.fullmatch(q.GetName())
            and (layer.GetPrimAtPath(q.GetPath()) or Sdf.PrimSpec).specifier == Sdf.SpecifierDef]
    for path in ours:                         # paths, not prims: a removed parent expires its children
        if stage.GetPrimAtPath(path):
            stage.RemovePrim(path)
            n += 1
    for path in roles_of(survey):
        q = stage.GetPrimAtPath(path)
        if q and not q.IsActive() and not any(path == old for p in survey.get("parts", [])
                                               for old in (p.get("split") or {})):
            q.SetActive(True)
            n += 1

    def rename(old_paths, new_path):
        for p in survey.get("parts", []):
            for key in ("members", "copies"):
                if p.get(key) and any(x in p[key] for x in old_paths):
                    kept = [x for x in p[key] if x not in old_paths]
                    p[key] = kept + ([new_path] if new_path not in kept else [])
            if p.get("path") in old_paths:
                p["path"] = new_path

    for old, new in moved.items():
        prim = stage.GetPrimAtPath(new)
        if prim:
            parent = prim.GetParent()
            stage.RemovePrim(prim.GetPath())
            if parent and not parent.GetChildren() and parent.GetPath() != stage.GetDefaultPrim().GetPath():
                stage.RemovePrim(parent.GetPath())
        src = stage.GetPrimAtPath(old)
        if src:
            src.SetActive(True)
        rename([new], old)
        n += 1
    # an earlier apply that did not record its moves: its re-authored
    # bodies still name the originals they were made from
    prev = entry.get("set_bodies") or {}
    for body in prev.get("bodies") or []:
        if body.get("name") in (prev.get("relocated") or []) and not any(m in moved for m in body.get("meshes") or []):
            prim = stage.GetPrimAtPath(body["prim"])
            if prim and prim.GetTypeName() == "Xform" and prim.GetName() == body["name"]:
                stage.RemovePrim(prim.GetPath())
                n += 1
            for m in body.get("meshes") or []:
                src = stage.GetPrimAtPath(m)
                if src:
                    src.SetActive(True)
    for p in survey.get("parts", []):
        for old, parts in list((p.get("split") or {}).items()):
            for part in parts:
                if stage.GetPrimAtPath(part):
                    stage.RemovePrim(part)
            src = stage.GetPrimAtPath(old)
            if src:
                src.SetActive(True)
            rename(parts, old)
            n += 1
        p.pop("split", None)
    # split parts whose source mesh is back (a record consumed by an earlier
    # undo, a part moved and moved again): the source stands, the parts go
    top = stage.GetPrimAtPath(root) if root else None
    stale = []
    for q in (Usd.PrimRange(top) if top else []):
        m = PART.match(q.GetName())
        if m and q.IsA(UsdGeom.Mesh) and layer.GetPrimAtPath(q.GetPath()) is not None \
                and q.GetParent().GetChild(m.group(1)) and q.GetParent().GetChild(m.group(1)).IsA(UsdGeom.Mesh):
            stale.append((q.GetPath(), q.GetParent().GetChild(m.group(1)).GetPath()))
    for part, base in stale:
        stage.RemovePrim(part)
        src = stage.GetPrimAtPath(base)
        if src and not src.IsActive():
            src.SetActive(True)
        n += 1
    if stale:
        gone = {str(part) for part, _ in stale}
        for p in survey.get("parts", []):
            for key in ("members", "copies"):
                if p.get(key):
                    kept = [x for x in p[key] if x not in gone]
                    for part, base in stale:
                        if str(part) in p[key] and str(base) not in kept:
                            kept.append(str(base))
                    p[key] = kept
            if p.get("path") in gone:
                p["path"] = next(str(base) for part, base in stale if str(part) == p["path"])
    if "moved" in (entry.get("set_bodies") or {}):
        entry["set_bodies"]["moved"] = {}
    return n


def strip_physics(stage, root: str) -> int:
    """Every physics API and attribute under root off; joint scopes gone."""
    from pxr import Usd

    top = stage.GetPrimAtPath(root)
    n = 0
    for scope in ("Joints", "Mechanisms"):
        if top.GetChild(scope):
            stage.RemovePrim(top.GetPath().AppendChild(scope))
    for p in Usd.PrimRange(top):
        hit = False
        # bare pxr drops unregistered names (PhysxCollisionAPI) from
        # GetAppliedSchemas: the authored list is checked too
        lo = p.GetMetadata("apiSchemas")
        applied = set(p.GetAppliedSchemas()) | (set(lo.GetAddedOrExplicitItems()) if lo else set())
        for api in PHYSICS_APIS:
            if api in applied:
                p.RemoveAppliedSchema(api)
                hit = True
        for a in p.GetAttributes():
            if a.GetName().startswith(("physics:", "physx")):
                p.RemoveProperty(a.GetName())
                hit = True
        n += hit
    return n


def apply(stage, root: str, entry: dict, prior: dict) -> str:
    """Author the bodies the survey describes; returns a note. The entry's
    survey is updated in place when a mesh is split; `entry["set_bodies"]`
    records what was done."""
    import contextlib
    import io

    from pxr import Usd, UsdGeom

    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _gen_make_sim_ready,
        _load_asset_priors,
        _load_physics_materials,
    )

    _stub_omni(stage)
    survey = entry.get("part_survey") or {}
    mb = prior.get("multi_body") or {}
    classes = _load_asset_priors().get("classes", {})
    member = classes.get(mb.get("member_class", ""), {})
    m_mats = member.get("typical_materials") or prior.get("typical_materials") or []
    s_mats = prior.get("typical_materials") or m_mats
    m_range = member.get("mass_kg") or [0.01, 1.0]
    dens = _load_physics_materials()["materials"]
    m_density = dens.get(m_mats[0] if m_mats else "", {}).get("density_kg_m3", 1000.0)
    s_density = dens.get(s_mats[0] if s_mats else "", {}).get("density_kg_m3", 700.0)
    fill = float(mb.get("member_fill", 0.45))
    mpu = UsdGeom.GetStageMetersPerUnit(stage)

    # a soft authoring from an earlier rule set (felt pads once counted) goes
    from processing import applicable
    if stage.GetPrimAtPath(f"{root}/SoftBodies") and "soft" not in applicable(entry):
        from soft_body_parts import strip as soft_strip
        soft_strip(stage, root, entry.get("soft_body_parts"))
    undo(stage, entry)
    strip_physics(stage, root)
    split = split_merged(stage, survey, roles_of(survey))
    groups = plan(stage, root, survey)
    if not any(g["kind"] == "piece" for g in groups):
        raise ValueError("the survey names no piece of the set")

    taken = {m for g in groups for m in g["meshes"]}
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    relocated, total, notes, moved = [], 0.0, [], {}
    # a figure whose box reaches over the next square (a dragon's wings) gets
    # a decomposed collider: its convex hull would fill the air between them
    # and fling the neighbour off the board at the first step
    boxes = _boxes(stage, root)
    piece_boxes = {g["name"]: _union([boxes[m] for m in g["meshes"] if m in boxes])
                   for g in groups if g["kind"] == "piece" and any(m in boxes for m in g["meshes"])}
    for g in groups:
        g["decomposed"] = g["kind"] == "piece" and g["name"] in piece_boxes and any(
            h != g["name"] and _overlap(piece_boxes[g["name"]], piece_boxes[h]) > 0.05 for h in piece_boxes)
    common = str(_common(stage, root, sorted(taken)))
    for g in groups:
        prim = carrier(stage, root, g["meshes"], taken) if len(groups) > 1 else stage.GetPrimAtPath(root)
        if prim is None:
            path = relocate(stage, common, g["name"], g["meshes"], moved)
            prim = stage.GetPrimAtPath(path)
            g["meshes"] = [moved.get(m, m) for m in g["meshes"]]
            relocated.append(g["name"])
        size = cache.ComputeWorldBound(prim).ComputeAlignedRange().GetSize()
        vol = abs(size[0] * size[1] * size[2]) * mpu ** 3
        if g["kind"] == "surface":
            mass = vol * s_density * fill
            mass = min(max(mass, 0.3), 4.0) if vol > 1e-7 else float(mb.get("surface_mass_kg", 1.0))
            mats = s_mats
        else:
            mass = min(max(vol * m_density * fill, m_range[0]), m_range[1])
            mats = m_mats
        args = {"prim_path": str(prim.GetPath()), "profile": "manipulable", "mass_kg": round(mass, 4)}
        if g["kind"] == "surface" and mb.get("surface_approximation"):
            args["approximation"] = mb["surface_approximation"]
        elif g.get("decomposed"):
            args["approximation"] = "convexDecomposition"
        if mats:
            args["material"] = mats[0]
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(_gen_make_sim_ready(args), "<set-bodies>", "exec"), {"__builtins__": __builtins__})
        g["prim"] = str(prim.GetPath())
        g["mass_kg"] = round(mass, 4)
        total += mass
    # the survey follows the meshes to their new prims (materials bind by it)
    for p in survey.get("parts", []):
        for key in ("members", "copies"):
            if p.get(key):
                p[key] = [moved.get(m, m) for m in p[key]]
        if p.get("path") in moved:
            p["path"] = moved[p["path"]]
    pieces = [g for g in groups if g["kind"] == "piece"]
    surface = next((g for g in groups if g["kind"] == "surface"), None)
    others = [g for g in groups if g["kind"] == "other"]
    entry["set_bodies"] = {
        "survey": survey.get("date"), "pieces": len(pieces), "surface": surface["prim"] if surface else None,
        "others": [g["prim"] for g in others], "split": {Path(k).name: len(v) for k, v in split.items()},
        "relocated": relocated, "moved": moved,
        "bodies": [{k: g.get(k) for k in ("name", "kind", "prim", "mass_kg", "meshes", "decomposed")} for g in groups],
        "mass_kg": round(total, 3)}
    notes.append(f"{len(pieces)} pieces" + (" + board" if surface else "") +
                 (f" + {len(others)} other" if others else ""))
    if any(g.get("decomposed") for g in pieces):
        notes.append(f"{sum(1 for g in pieces if g.get('decomposed'))} reaching over a neighbour, colliders decomposed")
    if split:
        notes.append("split " + ", ".join(f"{Path(k).name} into {len(v)}" for k, v in split.items()))
    if relocated:
        notes.append("re-authored " + ", ".join(relocated))
    return f"set bodies (survey): {', '.join(notes)}, {round(total, 3)} kg"


def _stub_omni(stage) -> None:
    """The generated make_sim_ready code reads the stage from omni.usd; bare
    pxr has none, so a stand-in hands it this stage."""
    import types

    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    ctx = type("Ctx", (), {"get_stage": lambda self: stage})()
    omni_usd.get_context = lambda: ctx
    omni.usd = omni_usd
    sys.modules["omni"] = omni
    sys.modules["omni.usd"] = omni_usd


def _common(stage, root, meshes):
    from pxr import Sdf

    paths = [Sdf.Path(m) for m in meshes]
    common = paths[0].GetParentPath()
    while not all(q.HasPrefix(common) for q in paths):
        common = common.GetParentPath()
    return common if common.HasPrefix(Sdf.Path(root)) else Sdf.Path(root)


def main() -> int:
    from pxr import Usd

    from asset_review_hub import _camel, save_queue_entry
    from processing import _prior, owned

    ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in ids:
        qf = REPO / "workspace" / "review_queue" / f"{a}.json"
        entry = json.loads(qf.read_text())
        stage = Usd.Stage.Open(entry["file"])
        root = f"/World/{_camel(a)}"
        if "--plan" in sys.argv:
            for g in plan(stage, root, entry.get("part_survey") or {}):
                print(f"{a}: {g['name']:10} {g['kind']:8} {len(g['meshes'])} mesh(es)  "
                      + ", ".join(Path(m).name for m in g["meshes"][:4]) + (" ..." if len(g["meshes"]) > 4 else ""))
            continue
        if not owned(entry["file"]):
            print(f"{a}: a source file, not ours to write")
            continue
        note = apply(stage, root, entry, _prior(entry))
        stage.GetRootLayer().Save()
        entry.setdefault("applied_fixes", []).append(note)
        save_queue_entry(entry)
        print(f"{a}: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
