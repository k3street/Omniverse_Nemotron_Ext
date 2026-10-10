#!/usr/bin/env python3
"""Make a chess set's board and pieces know what they are (pxr only).

Geometry gives a slab and 32 bodies. Play needs more:

  board   the 8x8 grid in world space: a1's centre, the file (a->h) and rank
          (1->8) directions, the square size and the playing surface height.
          a1 is found from the pieces, not assumed: white's back rank is
          rank 1, and files run left to right from white's side. The
          board's colouring is then checked: a1 must be a dark square
          ("light on the right"), a common modelling mistake.
  pieces  type, colour and starting square for every piece, from the part
          survey's roles ("king piece, one per side"), the names where the
          file names them, else from shape (a standard set is 2 kings, 2
          queens, 4 bishops, 4 knights, 4 rooks and 16 pawns, tallest to
          shortest in that order) and the side of the board.

The bodies come first (set_bodies: one rigid body per piece, the board as
one). Both are authored as customData (simReady:chessboard on the board,
simReady:chess on each piece), so a simulator, a planner or a robot policy
can read the game from the stage. `fen_placement()` turns live piece
positions back into a FEN placement, which is how play is verified.

Usage:
    python scripts/chess_board.py <queue_asset_id>      # annotate + report
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TYPES = {"king": "k", "queen": "q", "bishop": "b", "knight": "n", "rook": "r",
         "castle": "r", "pawn": "p"}
STANDARD = {"k": 2, "q": 2, "b": 4, "n": 4, "r": 4, "p": 16}
BY_HEIGHT = "kqbnrp"            # a Staunton set, tallest first
START = {  # standard start: (file index, rank index) -> piece letter
    **{(f, 0): "RNBQKBNR"[f] for f in range(8)}, **{(f, 1): "P" for f in range(8)},
    **{(f, 7): "rnbqkbnr"[f] for f in range(8)}, **{(f, 6): "p" for f in range(8)},
}
WHITE = re.compile(r"\b(white|light|w|weiss|weiß|hell|ivory|cream|natur)\b|(?<![a-z])w_", re.I)
BLACK = re.compile(r"\b(black|dark|b|schwarz|dunkel|ebony)\b|(?<![a-z])b_", re.I)
SQUARES = re.compile(r"\b(squares?|inlay|playing (?:surface|field|grid|area)|grid|chequer\w*|checker\w*)\b", re.I)
RIM = re.compile(r"\b(frame|trim|edging|border|rim|rail|moulding|tray|plinth|label|plaque|marking|"
                 r"underside|bottom|base)\b", re.I)


def square_name(f: int, r: int) -> str:
    if not (0 <= f < 8 and 0 <= r < 8):
        return f"off-board({f},{r})"
    return "abcdefgh"[f] + str(r + 1)


def _bounds(stage, prim):
    from pxr import Usd, UsdGeom

    r = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]) \
        .ComputeWorldBound(prim).ComputeAlignedRange()
    return r.GetMin(), r.GetMax()


def _meshes(prim):
    from pxr import Usd, UsdGeom

    return [p for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh) and p.IsActive()]


def _material(mesh):
    from pxr import UsdShade

    m, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
    return m if m and m.GetPrim() else None


def _material_names(prim):
    return {m.GetPrim().GetName() for q in _meshes(prim) for m in [_material(q)] if m}


def _luminance(material) -> float | None:
    """The material's constant base colour as a luminance; None when it is
    textured or has no colour we can read."""
    from pxr import Usd, UsdShade

    if not material:
        return None
    for shader in [UsdShade.Shader(p) for p in Usd.PrimRange(material.GetPrim()) if p.IsA(UsdShade.Shader)]:
        for name in ("diffuseColor", "diffuse_color_constant", "base_color", "baseColor", "albedo"):
            inp = shader.GetInput(name)
            if not inp:
                continue
            if inp.HasConnectedSource():
                return None
            v = inp.Get()
            if v is not None and len(v) >= 3:
                r, g, b = float(v[0]), float(v[1]), float(v[2])
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
    return None


def _colour_words(text: str) -> str | None:
    w, b = bool(WHITE.search(text)), bool(BLACK.search(text))
    if w and not b:
        return "white"
    if b and not w:
        return "black"
    return None


def _roles(survey: dict | None) -> dict[str, str]:
    from set_bodies import roles_of

    return roles_of(survey)


def _bodies(stage, asset_root: str) -> list:
    from pxr import Usd, UsdPhysics

    root = stage.GetPrimAtPath(asset_root)
    return [p for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.RigidBodyAPI) and _meshes(p)]


def analyse(stage, asset_root: str, survey: dict | None = None) -> dict:
    """Board grid, orientation and piece identities, in world coordinates."""
    from set_bodies import core, kind_of

    bodies = _bodies(stage, asset_root)
    if len(bodies) < 2:
        # before the bodies are authored, the set's members are its objects
        from ingest_asset import set_members
        bodies = set_members(stage, asset_root)
    if len(bodies) < 2:
        raise ValueError(f"{len(bodies)} objects: the set's bodies have not been authored")
    roles = _roles(survey)

    def role_text(prim):
        return " ".join(roles.get(str(q.GetPath()), "") for q in _meshes(prim))

    def role_kinds(prim):
        return Counter(kind_of(roles.get(str(q.GetPath()), "")) for q in _meshes(prim))

    # the board: the body the survey calls a surface, else the widest body
    def footprint(prim):
        lo, hi = _bounds(stage, prim)
        return (hi[0] - lo[0]) * (hi[1] - lo[1])

    surfaces = [b for b in bodies if role_kinds(b)["surface"] > role_kinds(b)["piece"]]
    board = max(surfaces or bodies, key=footprint)
    pieces = [b for b in bodies if b != board]

    info = []
    for p in pieces:
        lo, hi = _bounds(stage, p)
        text = (p.GetName() + " " + " ".join(q.GetName() for q in _meshes(p)) + " "
                + " ".join(_material_names(p))).lower()
        rtext = core(role_text(p)).lower()
        votes = Counter(v for k, v in TYPES.items() for _ in re.findall(rf"\b{k}s?\b", rtext))
        kind = votes.most_common(1)[0][0] if votes else next((v for k, v in TYPES.items() if k in text), None)
        lum = [_luminance(_material(q)) for q in _meshes(p)]
        lum = [v for v in lum if v is not None]
        info.append({"prim": p, "path": str(p.GetPath()), "kind": kind, "kind_from": "survey" if votes else "name",
                     "said": _colour_words(rtext) or _colour_words(text),
                     "lum": sum(lum) / len(lum) if lum else None,
                     "xy": ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2),
                     "height": hi[2] - lo[2],
                     "dims": sorted([hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]])})
    if not info:
        raise ValueError("no piece bodies beside the board")

    # the ranks run along the axis the pieces leave open (two camps with
    # the middle ranks empty); the camps are the two sides
    axis = _open_axis([i["xy"] for i in info])
    along = sorted((i["xy"][0] * axis[0] + i["xy"][1] * axis[1], n) for n, i in enumerate(info))
    gaps = [(along[k + 1][0] - along[k][0], k) for k in range(len(along) - 1)]
    cut = max(gaps)[1] if gaps else 0
    camp_a = {n for _, n in along[:cut + 1]}
    for n, i in enumerate(info):
        i["camp"] = "A" if n in camp_a else "B"

    # which camp is white: what the survey and the names call its pieces,
    # else the lighter material; else white's side is a choice
    def white_votes(camp):
        said = Counter(i["said"] for i in info if i["camp"] == camp and i["said"])
        return said["white"] - said["black"]

    va, vb = white_votes("A"), white_votes("B")
    colours_from, assumed_white = "survey", False
    if va == vb:
        la = [i["lum"] for i in info if i["camp"] == "A" and i["lum"] is not None]
        lb = [i["lum"] for i in info if i["camp"] == "B" and i["lum"] is not None]
        if la and lb and abs(sum(la) / len(la) - sum(lb) / len(lb)) > 0.08:
            va, vb = (1, 0) if sum(la) / len(la) > sum(lb) / len(lb) else (0, 1)
            colours_from = "material colour"
        else:
            va, vb, assumed_white, colours_from = 1, 0, True, "side (white's side assumed)"
    white = "A" if va > vb else "B"
    for i in info:
        i["color"] = "white" if i["camp"] == white else "black"
        i["color_from"] = colours_from if i["said"] == i["color"] else (
            colours_from if i["said"] is None else f"{colours_from} (the survey said {i['said']})")

    def centroid(col):
        pts = [i["xy"] for i in info if i["color"] == col]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    cw, cb = centroid("white"), centroid("black")
    d = (cb[0] - cw[0], cb[1] - cw[1])
    rank_dir = (math.copysign(1, d[0]), 0.0) if abs(d[0]) > abs(d[1]) else (0.0, math.copysign(1, d[1]))
    file_dir = (rank_dir[1], -rank_dir[0])  # right of a player facing the rank direction (Z up)

    # kinds: the survey's, when they make a set; else the nearest named
    # shape; a king stands taller than its queen (Staunton) either way
    if Counter(i["kind"] for i in info) != STANDARD:
        named = [i for i in info if i["kind"]]
        for i in info:
            if not i["kind"] and named:
                i["kind"] = min(named, key=lambda n: sum((a - b) ** 2 for a, b in zip(n["dims"], i["dims"])))["kind"]
                i["kind_from"] = "shape"
        if len(info) == 32 and Counter(i["kind"] for i in info) != STANDARD:
            # 2 kings, 2 queens, 4 bishops, 4 knights, 4 rooks, 16 pawns,
            # tallest first (ties broken by footprint: a rook is stouter)
            order = sorted(info, key=lambda i: (-round(i["height"], 4), -i["dims"][1] * i["dims"][0]))
            letters = "".join(letter_ * STANDARD[letter_] for letter_ in BY_HEIGHT)
            for i, letter_ in zip(order, letters):
                if i["kind"] != letter_:
                    i["kind"], i["kind_from"] = letter_, "height"
    # pieces standing in a start arrangement name themselves by their
    # squares when the shapes agree: the four on the corners alike, the
    # four beside them alike, bishops taller than knights taller than rooks
    # (Staunton); the survey's reading of unnamed CAD shapes then yields
    _slots(info, rank_dir, file_dir, stage, board, roles)
    for col in ("white", "black"):
        royals = [i for i in info if i["color"] == col and i["kind"] in ("k", "q")]
        if len(royals) == 2 and royals[0]["kind"] != royals[1]["kind"]:
            tall = max(royals, key=lambda i: i["height"])
            if tall["kind"] == "q" and tall["height"] > 1.05 * min(r["height"] for r in royals):
                for r in royals:
                    r["kind"], r["kind_from"] = ("k" if r is tall else "q"), "height (the survey had king and queen the other way)"

    # the grid: from the playing field the survey names, the board, or the
    # pieces themselves (a start position is symmetric about the centre and
    # a file apart); whichever puts the pieces nearest their squares
    candidates = _grids(stage, board, roles, info, rank_dir, file_dir)
    best = None
    for name, centre, size in candidates:
        a1 = (centre[0] - 3.5 * size * (file_dir[0] + rank_dir[0]),
              centre[1] - 3.5 * size * (file_dir[1] + rank_dir[1]))
        offs = [_to_square(i["xy"], a1, size, file_dir, rank_dir)[2] / size for i in info]
        score = max(offs)
        if best is None or score < best[0]:
            best = (score, name, centre, size, a1)
    _, grid_from, centre, size, a1 = best

    for i in info:
        f, r, off = _to_square(i["xy"], a1, size, file_dir, rank_dir)
        i["square"], i["file"], i["rank"], i["offset_m"] = square_name(f, r), f, r, off
    top = _surface_height(stage, board, roles, centre, size)
    return {"board": board, "board_path": str(board.GetPath()), "pieces": info, "size": size,
            "a1": a1, "file_dir": file_dir, "rank_dir": rank_dir, "top_z": top, "grid_from": grid_from,
            "white_side_assumed": assumed_white, "colours_from": colours_from,
            "a1_dark": _a1_is_dark(stage, board, a1, size, top, file_dir, roles)}


def _slots(info, rank_dir, file_dir, stage, board, roles) -> None:
    if len(info) != 32:
        return
    best = None
    for name, centre, size in _grids(stage, board, roles, info, rank_dir, file_dir):
        a1 = (centre[0] - 3.5 * size * (file_dir[0] + rank_dir[0]),
              centre[1] - 3.5 * size * (file_dir[1] + rank_dir[1]))
        sq = [_to_square(i["xy"], a1, size, file_dir, rank_dir) for i in info]
        if all(0 <= f < 8 and 0 <= r < 8 and off < 0.3 * size for f, r, off in sq) and (best is None or max(x[2] for x in sq) < best[0]):
            best = (max(x[2] for x in sq), sq)
    if best is None:
        return
    squares = best[1]
    if not all((f, r) in START for f, r, _ in squares):
        return                                # not a start arrangement
    by_slot: dict[str, list] = {}
    for i, (f, r, _) in zip(info, squares):
        by_slot.setdefault(START[(f, r)].lower(), []).append(i)
    heights, alike = {}, {}
    for k, group in by_slot.items():
        hs = [i["height"] for i in group]
        alike[k] = max(hs) <= 1.05 * min(hs)
        heights[k] = sum(hs) / len(hs)
    royals = [i["height"] for i in by_slot.get("k", []) + by_slot.get("q", [])]
    staunton = all(alike[k] for k in "bnrp") and heights["b"] > heights["n"] > heights["r"] > heights["p"] \
        and royals and min(royals) > heights["b"]
    # a figure set (dragons for bishops) keeps no Staunton order; when every
    # square's group is alike and the survey named each group one kind, it
    # merely called the groups by the wrong names
    said = {k: {i["kind"] for i in group} for k, group in by_slot.items()}
    permuted = all(alike.values()) and all(len(v) == 1 for v in said.values()) \
        and len({next(iter(v)) for v in said.values()}) == 6
    if staunton:
        for k, group in by_slot.items():
            for i in group:
                if i["kind"] != k and k in ("b", "n", "r", "p"):
                    i["kind"], i["kind_from"] = k, f"square (the survey said {i['kind']}; shapes agree with Staunton)"
    elif permuted:
        for k, group in by_slot.items():
            for i in group:
                if i["kind"] != k:
                    i["kind"], i["kind_from"] = k, f"square (the survey said {i['kind']}; a figure set, alike on alike squares)"


def start_faults(pieces: list[dict]) -> dict:
    """How the placement differs from the standard start: swapped pairs
    (a king and queen the other way round on one side) and the rest."""
    placed = {(i["file"], i["rank"]): i for i in pieces if 0 <= i["file"] < 8 and 0 <= i["rank"] < 8}
    wrong = [sq for sq, want in START.items()
             if sq not in placed or letter(placed[sq]["kind"], placed[sq]["color"]) != want]
    extra = [sq for sq in placed if sq not in START]
    swaps = []
    seen = set()
    for sq in wrong:
        if sq in seen or sq not in placed:
            continue
        want = START[sq]
        # the piece that belongs here is on the square this one belongs on
        for other in wrong:
            if other != sq and other in placed and other not in seen \
                    and letter(placed[other]["kind"], placed[other]["color"]) == want \
                    and letter(placed[sq]["kind"], placed[sq]["color"]) == START[other]:
                swaps.append((sq, other))
                seen |= {sq, other}
                break
    rest = [sq for sq in wrong if sq not in seen] + extra
    return {"swaps": swaps, "other": rest}


def turn_board(stage, a: dict, back: bool = False) -> bool:
    """A quarter turn of the board body about the grid's centre (the pieces
    stay): a square board coloured with a1 light becomes one with a1 dark.
    Returns False for a board that is not square."""
    from pxr import Gf, UsdGeom

    board = a["board"]
    lo, hi = _bounds(stage, board)
    w, d = hi[0] - lo[0], hi[1] - lo[1]
    if abs(w - d) > 0.02 * max(w, d):
        return False
    centre = Gf.Vec3d((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0)
    cache = UsdGeom.XformCache()
    world = cache.GetLocalToWorldTransform(board)
    parent_world = cache.GetLocalToWorldTransform(board.GetParent())
    turn = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), -90 if back else 90))
    about = Gf.Matrix4d().SetTranslate(-centre) * turn * Gf.Matrix4d().SetTranslate(centre)
    xf = UsdGeom.Xformable(board)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(world * about * parent_world.GetInverse())
    return True


def repair_start(stage, pieces: list[dict], swaps: list[tuple]) -> list[str]:
    """Put swapped pieces on each other's squares (their placement in the
    board's plane exchanged, height kept). Returns what moved."""
    from pxr import Gf, UsdGeom

    by_sq = {(i["file"], i["rank"]): i for i in pieces}
    moved = []
    for a, b in swaps:
        pa, pb = by_sq[a]["prim"], by_sq[b]["prim"]
        xa, xb = UsdGeom.Xformable(pa), UsdGeom.Xformable(pb)
        ca = UsdGeom.XformCache()
        wa, wb = ca.GetLocalToWorldTransform(pa), ca.GetLocalToWorldTransform(pb)
        ta, tb = wa.ExtractTranslation(), wb.ExtractTranslation()
        # the body's own box centre is what sits on the square; its prim
        # origin may lie elsewhere, so shift by the difference of the centres
        da = Gf.Vec3d(by_sq[b]["xy"][0] - by_sq[a]["xy"][0], by_sq[b]["xy"][1] - by_sq[a]["xy"][1], 0)
        for prim, xf, world, delta in ((pa, xa, wa, da), (pb, xb, wb, -da)):
            parent_world = ca.GetLocalToWorldTransform(prim.GetParent())
            new_world = Gf.Matrix4d(world)
            new_world.SetTranslateOnly(world.ExtractTranslation() + delta)
            local = new_world * parent_world.GetInverse()
            xf.ClearXformOpOrder()
            xf.AddTransformOp().Set(local)
        moved.append(f"{square_name(*a)}<->{square_name(*b)}")
        by_sq[a]["xy"], by_sq[b]["xy"] = by_sq[b]["xy"], by_sq[a]["xy"]
        by_sq[a]["square"], by_sq[b]["square"] = by_sq[b]["square"], by_sq[a]["square"]
        by_sq[a]["file"], by_sq[b]["file"] = by_sq[b]["file"], by_sq[a]["file"]
        by_sq[a]["rank"], by_sq[b]["rank"] = by_sq[b]["rank"], by_sq[a]["rank"]
    return moved


def _to_square(xy, a1, size, file_dir, rank_dir):
    dx, dy = xy[0] - a1[0], xy[1] - a1[1]
    f = (dx * file_dir[0] + dy * file_dir[1]) / size
    r = (dx * rank_dir[0] + dy * rank_dir[1]) / size
    fi, ri = round(f), round(r)
    off = math.hypot(f - fi, r - ri) * size
    return fi, ri, off


def _open_axis(points):
    """The direction across the biggest empty span among the points, along
    whichever board axis has it (the ranks between the two camps)."""
    best = (0.0, (1.0, 0.0))
    for axis, vec in ((0, (1.0, 0.0)), (1, (0.0, 1.0))):
        vals = sorted(p[axis] for p in points)
        gaps = [(vals[i + 1] - vals[i], i) for i in range(len(vals) - 1)]
        if gaps:
            g, _ = max(gaps)
            if g > best[0]:
                best = (g, vec)
    return best[1]


def _grids(stage, board, roles, info, rank_dir, file_dir):
    """(name, centre, square size) candidates for the 8x8 grid."""
    out = []
    meshes = _meshes(board)
    field = [q for q in meshes if SQUARES.search(roles.get(str(q.GetPath()), "")) and not RIM.search(roles.get(str(q.GetPath()), ""))]
    if field:
        lo, hi = _bounds_of(stage, field)
        out.append(("playing field", ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2), min(hi[0] - lo[0], hi[1] - lo[1]) / 8.0))
    slab = max(meshes, key=lambda q: _area(stage, q), default=None)
    if slab is not None:
        lo, hi = _bounds(stage, slab)
        out.append(("largest board mesh", ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2), min(hi[0] - lo[0], hi[1] - lo[1]) / 8.0))
    lo, hi = _bounds(stage, board)
    out.append(("board", ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2), min(hi[0] - lo[0], hi[1] - lo[1]) / 8.0))
    # the pieces: centre of the camp, a file between neighbours on a rank
    pts = [i["xy"] for i in info]
    centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    along = sorted(p[0] * file_dir[0] + p[1] * file_dir[1] for p in pts)
    cols = []
    for v in along:
        if cols and abs(v - cols[-1][-1]) < 0.012:
            cols[-1].append(v)
        else:
            cols.append([v])
    if len(cols) >= 4:
        means = [sum(c) / len(c) for c in cols]
        steps = sorted(means[i + 1] - means[i] for i in range(len(means) - 1))
        step = steps[len(steps) // 2]
        if step > 0.01:
            out.append(("pieces", centre, step))
    return out


def _area(stage, prim):
    lo, hi = _bounds(stage, prim)
    return (hi[0] - lo[0]) * (hi[1] - lo[1])


def _bounds_of(stage, prims):
    bs = [_bounds(stage, p) for p in prims]
    return (tuple(min(b[0][k] for b in bs) for k in range(3)), tuple(max(b[1][k] for b in bs) for k in range(3)))


def _surface_height(stage, board, roles, centre, size):
    """The top of the playing field: the highest face under the board's
    centre among its meshes (a raised rim does not count)."""
    best = None
    for q in _meshes(board):
        lo, hi = _bounds(stage, q)
        if lo[0] <= centre[0] <= hi[0] and lo[1] <= centre[1] <= hi[1] and (best is None or hi[2] > best):
            best = hi[2]
    return best if best is not None else _bounds(stage, board)[1][2]


def _mesh_at(stage, board, xy, top):
    """The board mesh whose top-face polygon covers xy at height `top`."""
    from pxr import Gf, UsdGeom

    for p in _meshes(board):
        m = UsdGeom.Mesh(p)
        lo, hi = _bounds(stage, p)
        if abs(hi[2] - top) > 2e-3 or not (lo[0] - 1e-6 <= xy[0] <= hi[0] + 1e-6 and lo[1] - 1e-6 <= xy[1] <= hi[1] + 1e-6):
            continue
        M = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
        pts = [M.Transform(Gf.Vec3d(*v)) for v in m.GetPointsAttr().Get()]
        idx, off = m.GetFaceVertexIndicesAttr().Get(), 0
        for c in m.GetFaceVertexCountsAttr().Get():
            face = [pts[idx[off + k]] for k in range(c)]
            off += c
            if any(abs(v[2] - top) > 2e-3 for v in face):
                continue
            if _inside(xy, [(v[0], v[1]) for v in face]):
                return p
    return None


def _inside(pt, poly) -> bool:
    x, y, inside = pt[0], pt[1], False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def _a1_is_dark(stage, board, a1, size, top, file_dir, roles=None):
    """Whether a1 is a dark square: the mesh under a1 against the one under
    b1 (always the other colour), by what the survey or the names call them,
    else by material colour when one is clearly the darker (red against
    green is a convention, not a shade); None when the board is one
    textured mesh."""
    b1 = (a1[0] + size * file_dir[0], a1[1] + size * file_dir[1])
    ma, mb = _mesh_at(stage, board, a1, top), _mesh_at(stage, board, b1, top)
    if ma is None or mb is None or ma == mb:
        return None
    roles = roles or {}
    na = (roles.get(str(ma.GetPath()), "") + " " + " ".join(_material_names(ma)) + " " + ma.GetName()).lower()
    nb = (roles.get(str(mb.GetPath()), "") + " " + " ".join(_material_names(mb)) + " " + mb.GetName()).lower()
    ca, cb = _colour_words(na), _colour_words(nb)
    if ca and cb and ca != cb:
        return ca == "black"
    if ca and not cb:
        return ca == "black"
    if cb and not ca:
        return cb == "white"
    la, lb = _luminance(_material(ma)), _luminance(_material(mb))
    if la is not None and lb is not None and abs(la - lb) > 0.3:
        return la < lb
    return None


def fen_placement(squares: dict) -> str:
    """{(file, rank): letter} -> FEN piece placement (rank 8 first)."""
    rows = []
    for r in range(7, -1, -1):
        row, empty = "", 0
        for f in range(8):
            ch = squares.get((f, r))
            if ch:
                row += (str(empty) if empty else "") + ch
                empty = 0
            else:
                empty += 1
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows)


def letter(kind: str | None, color: str) -> str:
    kind = kind or "x"                        # a piece of no known type never makes a standard start
    return kind.upper() if color == "white" else kind


def annotate(stage, asset_root: str, survey: dict | None = None, repair: bool = False) -> dict:
    """Author the convention; with `repair`, a king and queen the modeller
    set the other way round on one side are put on their squares first."""
    a = analyse(stage, asset_root, survey)
    faults = start_faults(a["pieces"])
    repaired = []
    if repair and faults["swaps"] and not faults["other"]:
        repaired = repair_start(stage, a["pieces"], faults["swaps"])
        faults = start_faults(a["pieces"])
    if repair and a["a1_dark"] is False and turn_board(stage, a):
        a["a1_dark"] = _a1_is_dark(stage, a["board"], a["a1"], a["size"], a["top_z"], a["file_dir"], _roles(survey))
        if a["a1_dark"]:
            repaired.append("board turned a quarter so that a1 is dark")
        else:
            turn_board(stage, a, back=True)
    # tags from an earlier reading, on prims that are no longer bodies, go
    from pxr import Usd
    for p in Usd.PrimRange(stage.GetPrimAtPath(asset_root)):
        for key in ("simReady:chess", "simReady:chessboard"):
            if p.HasCustomDataKey(key):
                p.ClearCustomDataByKey(key)
    board_meta = {"square_m": round(a["size"], 5), "a1_center": [round(v, 5) for v in a["a1"]],
                  "file_dir": list(a["file_dir"]), "rank_dir": list(a["rank_dir"]),
                  "surface_z": round(a["top_z"], 5), "a1_is_dark": a["a1_dark"], "grid_from": a["grid_from"]}
    a["board"].SetCustomDataByKey("simReady:chessboard", json.dumps(board_meta))
    placed = {}
    for i in a["pieces"]:
        i["prim"].SetCustomDataByKey("simReady:chess", json.dumps(
            {"piece": i["kind"], "color": i["color"], "start_square": i["square"]}))
        if 0 <= i["file"] < 8 and 0 <= i["rank"] < 8:
            placed[(i["file"], i["rank"])] = letter(i["kind"], i["color"])
    report = {
        "board": board_meta,
        "start_fen": fen_placement(placed),
        "standard_start": fen_placement(placed) == fen_placement(START),
        "max_offset_from_square_m": round(max(i["offset_m"] for i in a["pieces"]), 4),
        "pieces": len(a["pieces"]),
        "unknown_kinds": sum(1 for i in a["pieces"] if not i["kind"]),
        "off_board": sum(1 for i in a["pieces"] if i["square"].startswith("off")),
        "kinds_from": dict(Counter(i["kind_from"] for i in a["pieces"])),
        "colours_from": dict(Counter(i["color_from"] for i in a["pieces"])),
        "white_side_assumed": a["white_side_assumed"],
        "faults": {"swapped": [f"{square_name(*x)}<->{square_name(*y)}" for x, y in faults["swaps"]],
                   "other": [square_name(*sq) for sq in faults["other"]]},
        "repaired": repaired,
        "inferred": [f"{Path(i['path']).name}: {i['kind']} ({i['kind_from']}), {i['color']} ({i['color_from']})"
                     for i in a["pieces"] if i["kind_from"] != "survey" or i["color_from"] != "survey"],
    }
    return report


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    from pxr import Usd

    from ingest_asset import QUEUE_DIR

    qf = QUEUE_DIR / f"{sys.argv[1]}.json"
    entry = json.loads(qf.read_text())
    stage = Usd.Stage.Open(entry["file"])
    root = str(stage.GetDefaultPrim().GetChildren()[0].GetPath())
    report = annotate(stage, root, entry.get("part_survey"))
    stage.GetRootLayer().Save()
    entry["game"] = {"type": "chess", **{k: report[k] for k in ("start_fen", "standard_start", "board")}}
    entry.setdefault("applied_fixes", []).append("chess: board grid + piece identities annotated")
    qf.write_text(json.dumps(entry, indent=1))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
