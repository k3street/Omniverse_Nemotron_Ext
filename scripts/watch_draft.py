#!/usr/bin/env python3
"""Watch tier of articulation drafting: crown, bezel and hands.

The dial is the flat disc that carries the most small parts (hands,
indices) just above it - not simply the largest disc, which is often the
case back. Its thin direction is the face axis and the side those parts are
on is the face. From there:

  crown   a small part just outside the case rim at the case's height:
          a revolute about its own (radial) axis, two turns each way.
  bezel   a hollow ring around the dial on the face side, no wider than
          the case: a revolute about the face axis (the ratchet's clicks
          are not modelled).
  hands   thin elongated parts over the dial reaching its centre: each a
          revolute about the face axis through the centre; the longest is
          the leader, and the shortest (the hour hand) follows it at 1/12
          through a mimic coupling.

Everything else, the strap included, is fixed to the case (a flexible strap
is a deformable, not a joint). Hands modelled as one mesh must be segmented
first to move separately.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FLAT = 0.1            # a disc: thinnest dim under this fraction of its width
ROUND = 0.15          # ...and its two wide dims within this of each other
CROWN_MAX = 0.35      # crown and pushers: smaller than this fraction of the dial
AXIS_SNAP_DEG = 25.0  # a radial axis within this of X/Y/Z is that axis


def _covers(stage, path, axis, point) -> bool:
    """Whether any face of the meshes under path, projected along axis,
    contains point (crossing-number test). A disc's cap does whether or not
    it has a vertex at its centre; a ring's faces never do."""
    from pxr import Gf, Usd, UsdGeom

    other = [i for i in range(3) if i != axis]
    px, py = float(point[other[0]]), float(point[other[1]])
    for prim in Usd.PrimRange(stage.GetPrimAtPath(path)):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
        v = np.array([list(m.Transform(Gf.Vec3d(*q))) for q in mesh.GetPointsAttr().Get()])[:, other]
        idx = mesh.GetFaceVertexIndicesAttr().Get()
        k = 0
        for c in mesh.GetFaceVertexCountsAttr().Get():
            poly = v[list(idx[k:k + c])]
            k += c
            inside = False
            for (x0, y0), (x1, y1) in zip(poly, np.roll(poly, -1, axis=0)):
                if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
                    inside = not inside
            if inside:
                return True
    return False


def propose_watch(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts
    from thread_draft import _edge_points

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    size = lambda p: np.array(p["size"])  # noqa: E731

    def disc_axis(p):
        s = size(p)
        k = int(np.argmin(s))
        wide = [s[i] for i in range(3) if i != k]
        if not (s[k] <= FLAT * max(wide) and min(wide) >= (1 - ROUND) * max(wide)):
            return None
        # round by its points, not its box: a hand at 1:30 has a square box
        q = _edge_points(stage, p["path"], max(s) / 16)[:, [i for i in range(3) if i != k]]
        sv = np.linalg.svd(q - q.mean(0), compute_uv=False)
        return k if sv[1] >= (1 - 2 * ROUND) * sv[0] else None

    def carried(d, k):
        """Small parts lying over disc d, just off its face; signed by side."""
        r = 0.5 * max(size(d))
        c = np.array(d["centroid"])
        other = [i for i in range(3) if i != k]
        up = down = 0
        for p in parts:
            if p is d or max(size(p)) > 1.2 * 2 * r:
                continue
            if np.linalg.norm(np.array(p["centroid"])[other] - c[other]) > r:
                continue
            gap = p["centroid"][k] - c[k]
            if 0 < gap <= 0.25 * r:
                up += 1
            elif -0.25 * r <= gap < 0:
                down += 1
        return up, down

    discs = [(p, disc_axis(p)) for p in parts]
    discs = [(p, k) for p, k in discs if k is not None]
    if not discs:
        raise RuntimeError("no dial: no flat round part")
    def solid(d, k):
        """A dial is a plate, not a ring (the case top around the glass): some
        face, seen along the axis, covers its centre."""
        return _covers(stage, d["path"], k, np.array(d["centroid"]))

    def case_behind(d, k, face):
        """The case's bulk lies behind the dial; the glass has it in front."""
        r = 0.5 * max(size(d))
        c = np.array(d["centroid"])
        other = [i for i in range(3) if i != k]
        bulk = [p for p in parts if p is not d and np.linalg.norm(np.array(p["centroid"])[other] - c[other]) < 0.3 * r
                and 2 * r <= max(size(p)[other]) <= 3.2 * r]
        if not bulk:
            return True
        z = sum(p["volume"] * p["centroid"][k] for p in bulk) / sum(p["volume"] for p in bulk)
        return (z - c[k]) * face <= 0

    scored = []
    for p, k in discs:
        up, down = carried(p, k)
        face = 1.0 if up >= down else -1.0
        if max(up, down) and solid(p, k) and case_behind(p, k, face):
            scored.append((max(up, down), p, k, face))
    if not scored:
        raise RuntimeError("no dial: no solid disc carrying hands with the case behind it")
    _, dial, axis, face = max(scored, key=lambda t: (t[0], max(size(t[1]))))
    other = [i for i in range(3) if i != axis]
    centre = np.array(dial["centroid"])
    R = 0.5 * max(size(dial))

    def radial(p):
        return float(np.linalg.norm(np.array(p["centroid"])[other] - centre[other]))

    def footprint(p):
        return max(size(p)[other])

    # the case: around the dial, the bulkiest such part
    around = [p for p in parts if p is not dial and radial(p) < 0.3 * R and 2 * R <= footprint(p) <= 3.2 * R]
    case = max(around, key=lambda p: p["volume"]) if around else dial
    Rc = 0.5 * footprint(case)
    c_lo, c_hi = case["min"][axis], case["max"][axis]

    def hollow(p):
        return not _covers(stage, p["path"], axis, centre)

    joints, notes = [], []
    taken = {case["path"]}
    names = "XYZ"

    # crown: small, at the case's height, sticking out past the case along
    # its own radial axis (the case's lugs make it wider in other directions)
    rim = [p for p in parts if p["path"] not in taken and max(size(p)) < CROWN_MAX * 2 * R
           and radial(p) >= 0.8 * R and c_lo - 0.1 * R <= p["centroid"][axis] <= c_hi + 0.1 * R]
    crown = None
    for p in sorted(rim, key=lambda p: -p["volume"]):
        d = np.array(p["centroid"]) - centre
        d[axis] = 0.0
        d /= np.linalg.norm(d)
        k = int(np.argmax(np.abs(d)))
        if np.degrees(np.arccos(min(1.0, abs(d[k])))) > AXIS_SNAP_DEG:
            continue
        out = p["max"][k] - case["max"][k] if d[k] > 0 else case["min"][k] - p["min"][k]
        inner = p["min"][k] - case["max"][k] if d[k] > 0 else case["min"][k] - p["max"][k]
        if not (out > 0.02 * R and inner < 0.15 * R):
            continue
        crown = p
        turns = float(template.get("crown_turns", 2.0))
        joints.append({"name": "crown", "joint_type": "revolute", "parent_prim": case["path"],
                       "child_prim": p["path"], "axis": names[k], "lower_limit": -360.0 * turns,
                       "upper_limit": 360.0 * turns, "anchor": [round(float(v), 6) for v in p["centroid"]],
                       "stiffness": 0.0, "damping": 1e-4, "max_force": 1.0})
        taken.add(p["path"])
        notes.append(f"crown {Path(p['path']).name} turns about {names[k]}")
        break

    # hands: thin, elongated, over the dial on the face side, reaching the centre
    hands = []
    for p in parts:
        if p["path"] in taken or p is dial:
            continue
        s = size(p)
        gap = (p["centroid"][axis] - centre[axis]) * face
        reaches = all(p["min"][i] - 0.08 * R <= centre[i] <= p["max"][i] + 0.08 * R for i in other)
        if not (s[axis] < 0.08 * R and 0 <= gap <= 0.3 * R and max(s[other]) <= 2.05 * R and reaches):
            continue
        # elongation from the points' principal spread: a hand at 1:30 has a
        # square bounding box
        q = _edge_points(stage, p["path"], max(s) / 16)[:, other]
        _, sv, vt = np.linalg.svd(q - q.mean(0), full_matrices=False)
        if sv[0] < 2.5 * max(sv[1], 1e-12):
            continue
        # a hand pivots near one end; a bar centred on the pivot is a decoration
        t = (q - q.mean(0)) @ vt[0]
        tc = float((centre[other] - q.mean(0)) @ vt[0])
        if abs(tc - 0.5 * (t.max() + t.min())) < 0.2 * (t.max() - t.min()):
            continue
        p["_length"] = float(t.max() - t.min())
        hands.append(p)
    # a hand modelled in two materials is two meshes with the same bounds
    merged, twins = [], []
    for h in hands:
        same = next((m for m in merged if np.allclose(m["min"], h["min"], atol=1e-5)
                     and np.allclose(m["max"], h["max"], atol=1e-5)), None)
        (twins.append((h, same)) if same else merged.append(h))
    hands = sorted(merged, key=lambda p: -p["_length"])
    anchor = [round(float(v), 6) for v in centre]
    for i, h in enumerate(hands):
        joints.append({"name": f"hand_{i}", "joint_type": "revolute", "parent_prim": case["path"],
                       "child_prim": h["path"], "axis": names[axis], "lower_limit": -720.0,
                       "upper_limit": 720.0, "anchor": anchor, "stiffness": 0.0, "damping": 1e-5,
                       "max_force": 0.1})
        taken.add(h["path"])
    for h, same in twins:
        joints.append({"name": f"hand_twin_{len(joints):03d}", "joint_type": "fixed",
                       "parent_prim": same["path"], "child_prim": h["path"]})
        taken.add(h["path"])
    mechanisms = []
    if len(hands) >= 2:
        # the shortest is the hour hand: a twelfth of the leader's turn
        mechanisms.append({"type": "couple", "follower": f"hand_{len(hands) - 1}", "leader": "hand_0",
                           "gearing": round(-1.0 / 12.0, 6)})
        notes.append(f"{len(hands)} hands; the hour hand follows the longest at 1/12")
    elif hands:
        notes.append("one hand mesh: segment it to move the hands separately")

    # bezel: a hollow ring around the dial on the face side, no wider than the case
    for p in sorted(parts, key=lambda p: -p["volume"]):
        if p["path"] in taken or p is dial or radial(p) > 0.05 * R:
            continue
        top = (p["max"][axis] if face > 0 else -p["min"][axis])
        dial_face = centre[axis] * face
        if (R <= 0.5 * footprint(p) <= 1.1 * Rc and size(p)[axis] < 0.3 * R
                and top >= dial_face and hollow(p)):
            joints.append({"name": "bezel", "joint_type": "revolute", "parent_prim": case["path"],
                           "child_prim": p["path"], "axis": names[axis], "lower_limit": -360.0,
                           "upper_limit": 360.0, "anchor": anchor, "stiffness": 0.0, "damping": 2e-4,
                           "max_force": 1.0})
            taken.add(p["path"])
            notes.append(f"bezel {Path(p['path']).name} turns about the face axis (clicks not modelled)")
            break

    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):03d}", "joint_type": "fixed",
                           "parent_prim": case["path"], "child_prim": p["path"]})
    if crown is None:
        notes.append("no crown found at the rim")
    notes.insert(0, f"dial {Path(dial['path']).name}, face {'+-'[face < 0]}{names[axis]}, "
                    f"case {Path(case['path']).name}")
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "mechanisms": mechanisms,
            "_analysis": {"tier": "watch", "dial": dial["path"], "case": case["path"], "notes": notes},
            "_instructions": "Watch drafted from the dial. CHECK the crown, bezel and which hand is which."}, notes
