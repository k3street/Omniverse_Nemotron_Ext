#!/usr/bin/env python3
"""Make a chess set's board and pieces know what they are (pxr only).

Geometry gives a slab and 33 objects. Play needs more:

  board   the 8x8 grid in world space: a1's centre, the file (a->h) and rank
          (1->8) directions, the square size and the playing surface height.
          a1 is found from the pieces, not assumed: white's back rank is
          rank 1, and files run left to right from white's side. The
          board's colouring is then checked: a1 must be a dark square
          ("light on the right"), a common modelling mistake.
  pieces  type, colour and starting square for every piece, from its name
          where the file names it, else from its shape (nearest named piece
          by size) and its side of the board.

Both are authored as customData (simReady:chessboard on the board,
simReady:chess on each piece), so a simulator, a planner or a robot policy
can read the game from the stage. `fen_placement()` turns live piece
positions back into a FEN placement, which is how play is verified.

Usage:
    python scripts/chess_board.py <queue_asset_id>      # annotate + report
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TYPES = {"king": "k", "queen": "q", "bishop": "b", "knight": "n", "rook": "r",
         "castle": "r", "pawn": "p"}
START = {  # standard start: (file index, rank index) -> piece letter
    **{(f, 0): "RNBQKBNR"[f] for f in range(8)}, **{(f, 1): "P" for f in range(8)},
    **{(f, 7): "rnbqkbnr"[f] for f in range(8)}, **{(f, 6): "p" for f in range(8)},
}


def square_name(f: int, r: int) -> str:
    return "abcdefgh"[f] + str(r + 1)


def _bounds(stage, prim):
    from pxr import Usd, UsdGeom

    r = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]) \
        .ComputeWorldBound(prim).ComputeAlignedRange()
    return r.GetMin(), r.GetMax()


def _material_names(prim):
    from pxr import Usd, UsdGeom, UsdShade

    names = set()
    for p in Usd.PrimRange(prim):
        if p.IsA(UsdGeom.Mesh):
            rel = p.GetRelationship("material:binding")
            for t in rel.GetTargets() if rel else []:
                names.add(t.name)
    return names


def analyse(stage, asset_root: str) -> dict:
    """Board grid, orientation and piece identities, in world coordinates."""
    from ingest_asset import set_members

    members = set_members(stage, asset_root)
    board = next(m for m in members if "board" in m.GetName().lower())
    pieces = [m for m in members if m != board]
    blo, bhi = _bounds(stage, board)
    top = bhi[2]
    centre = ((blo[0] + bhi[0]) / 2, (blo[1] + bhi[1]) / 2)
    size = min(bhi[0] - blo[0], bhi[1] - blo[1]) / 8.0

    info = []
    for p in pieces:
        lo, hi = _bounds(stage, p)
        text = (p.GetName() + " " + " ".join(_material_names(p))).lower()
        kind = next((v for k, v in TYPES.items() if k in text), None)
        color = "white" if "white" in text else "black" if "black" in text else None
        info.append({"prim": p, "path": str(p.GetPath()), "kind": kind, "color": color,
                     "xy": ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2),
                     "dims": sorted([hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]])})
    # unnamed pieces: the named piece of nearest size gives the kind
    named = [i for i in info if i["kind"]]
    for i in info:
        if not i["kind"]:
            i["kind"] = min(named, key=lambda n: sum((a - b) ** 2 for a, b in zip(n["dims"], i["dims"])))["kind"]
            i["kind_from"] = "shape"
    # rank direction: from white's pieces toward black's, snapped to a board axis
    def centroid(col):
        pts = [i["xy"] for i in info if i["color"] == col]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None
    cw, cb = centroid("white"), centroid("black")
    d = (cb[0] - cw[0], cb[1] - cw[1])
    rank_dir = (math.copysign(1, d[0]), 0.0) if abs(d[0]) > abs(d[1]) else (0.0, math.copysign(1, d[1]))
    file_dir = (rank_dir[1], -rank_dir[0])  # right of a player facing the rank direction (Z up)
    a1 = (centre[0] - 3.5 * size * (file_dir[0] + rank_dir[0]),
          centre[1] - 3.5 * size * (file_dir[1] + rank_dir[1]))

    def to_square(xy):
        dx, dy = xy[0] - a1[0], xy[1] - a1[1]
        f = (dx * file_dir[0] + dy * file_dir[1]) / size
        r = (dx * rank_dir[0] + dy * rank_dir[1]) / size
        fi, ri = round(f), round(r)
        off = math.hypot(f - fi, r - ri) * size
        return fi, ri, off

    for i in info:
        f, r, off = to_square(i["xy"])
        i["square"], i["file"], i["rank"], i["offset_m"] = square_name(f, r), f, r, off
        if not i["color"]:
            i["color"] = "white" if r <= 3 else "black"
            i["color_from"] = "side"

    return {"board": board, "board_path": str(board.GetPath()), "pieces": info, "size": size,
            "a1": a1, "file_dir": file_dir, "rank_dir": rank_dir, "top_z": top,
            "a1_dark": _a1_is_dark(stage, board, a1, size, top)}


def _a1_is_dark(stage, board, a1, size, top):
    """Which of the board's meshes covers a1's centre on the top face."""
    from pxr import Gf, Usd, UsdGeom

    for p in Usd.PrimRange(board):
        if not p.IsA(UsdGeom.Mesh):
            continue
        m = UsdGeom.Mesh(p)
        M = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
        pts = [M.Transform(Gf.Vec3d(*v)) for v in m.GetPointsAttr().Get()]
        idx, off = m.GetFaceVertexIndicesAttr().Get(), 0
        for c in m.GetFaceVertexCountsAttr().Get():
            face = [pts[idx[off + k]] for k in range(c)]
            off += c
            if any(abs(v[2] - top) > 1e-3 for v in face):
                continue
            xs, ys = [v[0] for v in face], [v[1] for v in face]
            if min(xs) - 1e-6 <= a1[0] <= max(xs) + 1e-6 and min(ys) - 1e-6 <= a1[1] <= max(ys) + 1e-6:
                name = " ".join(_material_names(p)).lower() + " " + p.GetName().lower()
                return "black" in name or "dark" in name
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


def letter(kind: str, color: str) -> str:
    return kind.upper() if color == "white" else kind


def annotate(stage, asset_root: str) -> dict:
    a = analyse(stage, asset_root)
    board_meta = {"square_m": round(a["size"], 5), "a1_center": [round(v, 5) for v in a["a1"]],
                  "file_dir": list(a["file_dir"]), "rank_dir": list(a["rank_dir"]),
                  "surface_z": round(a["top_z"], 5), "a1_is_dark": a["a1_dark"]}
    a["board"].SetCustomDataByKey("simReady:chessboard", json.dumps(board_meta))
    placed = {}
    for i in a["pieces"]:
        i["prim"].SetCustomDataByKey("simReady:chess", json.dumps(
            {"piece": i["kind"], "color": i["color"], "start_square": i["square"]}))
        placed[(i["file"], i["rank"])] = letter(i["kind"], i["color"])
    report = {
        "board": board_meta,
        "start_fen": fen_placement(placed),
        "standard_start": fen_placement(placed) == fen_placement(START),
        "max_offset_from_square_m": round(max(i["offset_m"] for i in a["pieces"]), 4),
        "pieces": len(a["pieces"]),
        "inferred": [f"{Path(i['path']).name}: {i['kind']} ({i.get('kind_from', 'name')}), "
                     f"{i['color']} ({i.get('color_from', 'name')})"
                     for i in a["pieces"] if i.get("kind_from") or i.get("color_from")],
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
    report = annotate(stage, root)
    stage.GetRootLayer().Save()
    entry["game"] = {"type": "chess", **{k: report[k] for k in ("start_fen", "standard_start", "board")}}
    entry.setdefault("applied_fixes", []).append("chess: board grid + piece identities annotated")
    qf.write_text(json.dumps(entry, indent=1))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
