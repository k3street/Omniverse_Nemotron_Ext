#!/usr/bin/env python3
"""Geometry-driven articulation drafting (BACKLOG #3, deterministic tier).

Given a (segmented) multi-part asset, propose articulation links and
joints from geometry alone:

  1. per-part world bbox + centroid;
  2. symmetry axis detection — the horizontal axis (X or Y) with the most
     mirrored part pairs;
  3. wheel candidates — disc-shaped parts (two similar dims, thin third)
     whose thin axis matches the symmetry axis, in mirrored pairs;
  4. link grouping — parts whose bbox is mostly contained in a wheel's
     bbox on the same side join that wheel's link (tire + rim + spokes);
  5. output: revolute joint per wheel (axis = thin axis, continuous),
     fixed joints binding each wheel's companions, everything else fixed
     to the base part.

The output is a DRAFT for the human reviewer — the machine proposes, the
reviewer confirms/edits in the hub. A VLM tier can later replace step 3-4
for non-wheel mechanisms (lids, doors) that geometry alone cannot name.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DISC_SIMILAR = 1.35     # d0/d1 below this -> round-ish profile
DISC_THIN = 1.6         # d1/d2 above this -> flat/thin
MIRROR_TOL = 0.12       # fraction of asset max_dim for mirror matching
CONTAIN_FRAC = 0.6      # bbox containment fraction for link membership


def _axis_index(name: str) -> int:
    return {"X": 0, "Y": 1, "Z": 2}[name]


def collect_parts(stage, asset_root: str) -> list[dict]:
    from pxr import Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                              [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    parts = []
    root = stage.GetPrimAtPath(asset_root)
    for p in Usd.PrimRange(root):
        if not p.IsA(UsdGeom.Mesh) or not p.IsActive():
            continue
        r = cache.ComputeWorldBound(p).ComputeAlignedRange()
        if r.IsEmpty():
            continue
        mn, mx = r.GetMin(), r.GetMax()
        size = [mx[i] - mn[i] for i in range(3)]
        parts.append({
            "path": str(p.GetPath()),
            "min": [mn[0], mn[1], mn[2]],
            "max": [mx[0], mx[1], mx[2]],
            "size": size,
            "centroid": [(mn[i] + mx[i]) / 2 for i in range(3)],
            "volume": max(1e-12, size[0] * size[1] * size[2]),
        })
    return parts


def detect_symmetry_axis(parts: list[dict], max_dim: float) -> int:
    """Horizontal axis (0=X or 1=Y) with the most mirrored centroid pairs."""
    best_axis, best_count = 0, -1
    for axis in (0, 1):
        mid = sum(p["centroid"][axis] for p in parts) / len(parts)
        count = 0
        for i, a in enumerate(parts):
            off = a["centroid"][axis] - mid
            if abs(off) < 0.02 * max_dim:
                continue
            for b in parts[i + 1:]:
                if abs((b["centroid"][axis] - mid) + off) < MIRROR_TOL * max_dim \
                        and all(abs(a["centroid"][k] - b["centroid"][k])
                                < MIRROR_TOL * max_dim
                                for k in range(3) if k != axis):
                    count += 1
                    break
        if count > best_count:
            best_axis, best_count = axis, count
    return best_axis


def find_wheels(parts: list[dict], axis: int, max_dim: float) -> list[dict]:
    """Disc-shaped parts whose thin axis is the symmetry axis, in mirror
    pairs. Returns wheel dicts {part, side}."""
    # wheels touch the ground: bbox bottom within 15% of the asset's bottom
    # (round side-guards and panels are disc-like too, but they float)
    z_lo = min(p["min"][2] for p in parts)
    z_span = max(p["max"][2] for p in parts) - z_lo
    discs = []
    for p in parts:
        order = sorted(range(3), key=lambda i: p["size"][i], reverse=True)
        d0, d1, d2 = (p["size"][i] for i in order)
        if d1 <= 0 or d2 <= 0:
            continue
        if d0 / d1 <= DISC_SIMILAR and d1 / d2 >= DISC_THIN and order[2] == axis \
                and (p["min"][2] - z_lo) <= 0.15 * z_span:
            discs.append(p)
    mid = _mid(parts, axis)
    wheels = []
    used = set()
    for i, a in enumerate(discs):
        if i in used:
            continue
        for j in range(i + 1, len(discs)):
            if j in used:
                continue
            b = discs[j]
            mirrored = abs((a["centroid"][axis] - mid) + (b["centroid"][axis] - mid)) \
                < MIRROR_TOL * max_dim
            colocated = all(abs(a["centroid"][k] - b["centroid"][k]) < MIRROR_TOL * max_dim
                            for k in range(3) if k != axis)
            similar = abs(a["volume"] - b["volume"]) < 0.6 * max(a["volume"], b["volume"])
            if mirrored and colocated and similar:
                used.add(i)
                used.add(j)
                wheels.append({"part": a, "side": "L" if a["centroid"][axis] < mid else "R"})
                wheels.append({"part": b, "side": "L" if b["centroid"][axis] < mid else "R"})
                break
    # collapse candidates that live inside a bigger kept wheel (handrim
    # rings, tires, spoke segments): they rejoin as members via
    # group_links' bbox containment
    collapsed = []
    for w in sorted(wheels, key=lambda x: x["part"]["volume"], reverse=True):
        dup = any(
            w["side"] == c["side"]
            and (_containment(w["part"], c["part"]) >= CONTAIN_FRAC
                 or all(abs(w["part"]["centroid"][k] - c["part"]["centroid"][k])
                        < MIRROR_TOL * max_dim for k in range(3) if k != axis))
            for c in collapsed)
        if not dup:
            collapsed.append(w)
    return collapsed


def _mid(parts: list[dict], axis: int) -> float:
    """Geometric center along axis — bbox center, NOT the centroid mean
    (part-count imbalance between sides skews the mean off the symmetry
    plane; the wheelchair's wheels missed pairing by exactly that skew)."""
    lo = min(p["min"][axis] for p in parts)
    hi = max(p["max"][axis] for p in parts)
    return (lo + hi) / 2.0


def _containment(inner: dict, outer: dict) -> float:
    iv = 1.0
    for k in range(3):
        lo = max(inner["min"][k], outer["min"][k])
        hi = min(inner["max"][k], outer["max"][k])
        if hi <= lo:
            return 0.0
        iv *= (hi - lo)
    return iv / inner["volume"]


def group_links(parts, wheels, axis, max_dim):
    """Assign parts to wheel links by same-side bbox containment."""
    mid = _mid(parts, axis)
    wheel_paths = {w["part"]["path"] for w in wheels}
    members = {w["part"]["path"]: [] for w in wheels}
    for p in parts:
        if p["path"] in wheel_paths:
            continue
        best, best_frac = None, 0.0
        for w in wheels:
            wp = w["part"]
            same_side = ((p["centroid"][axis] - mid) * (wp["centroid"][axis] - mid)) > 0 \
                or abs(p["centroid"][axis] - mid) < 0.02 * max_dim
            if not same_side:
                continue
            frac = _containment(p, wp)
            if frac > best_frac:
                best, best_frac = wp["path"], frac
        if best and best_frac >= CONTAIN_FRAC:
            members[best].append(p["path"])
    return members


def propose(stage, asset_root: str, prim_path_for_spec: str) -> dict:
    """Full proposal: returns the articulate_asset spec draft."""
    parts = collect_parts(stage, asset_root)
    if len(parts) < 2:
        raise RuntimeError("fewer than 2 active mesh parts — segment first")
    max_dim = max(max(p["size"]) for p in parts)
    # pick the axis that yields wheel pairs (discs thin along it, mirrored);
    # only fall back to generic part-mirror counting when neither axis has
    # wheels — front/back frame pairs must not outvote the axle
    candidates = [(a, find_wheels(parts, a, max_dim)) for a in (0, 1)]
    candidates.sort(key=lambda c: len(c[1]), reverse=True)
    if candidates[0][1]:
        axis, wheels = candidates[0]
    else:
        axis = detect_symmetry_axis(parts, max_dim)
        wheels = []
    axis_name = "XYZ"[axis]
    members = group_links(parts, wheels, axis, max_dim) if wheels else {}
    grouped = set()
    for w, ms in members.items():
        grouped.update(ms)
    grouped.update(w["part"]["path"] for w in wheels)
    ungrouped = [p for p in parts if p["path"] not in grouped]
    base = max(ungrouped, key=lambda p: p["volume"]) if ungrouped \
        else max(parts, key=lambda p: p["volume"])

    joints = []
    for w in wheels:
        wp = w["part"]["path"]
        name = f"wheel_{w['side']}_{len([j for j in joints if j['name'].startswith('wheel_' + w['side'])])}"
        joints.append({"name": name, "joint_type": "revolute",
                       "parent_prim": base["path"], "child_prim": wp,
                       "axis": axis_name, "lower_limit": None, "upper_limit": None,
                       "_note": "continuous wheel spin — limits intentionally open"})
        for i, m in enumerate(members.get(wp, [])):
            joints.append({"name": f"{name}_mount{i:02d}", "joint_type": "fixed",
                           "parent_prim": wp, "child_prim": m})
    for i, p in enumerate(u for u in ungrouped if u["path"] != base["path"]):
        joints.append({"name": f"frame{i:02d}", "joint_type": "fixed",
                       "parent_prim": base["path"], "child_prim": p["path"]})

    return {
        "prim_path": prim_path_for_spec,
        "fixed_base": False,
        "joints": joints,
        "_analysis": {
            "parts": len(parts),
            "symmetry_axis": axis_name,
            "wheels_detected": len(wheels),
            "base_link": base["path"],
        },
        "_instructions": ("Machine proposal from geometry: wheels are revolute "
                          "(continuous), companions fixed to their wheel, the "
                          "rest fixed to the base. REVIEW: correct any part the "
                          "geometry misjudged, add prismatic/limited joints the "
                          "heuristics cannot see (lids, drawers), then Apply. "
                          "Remove this key and _note/_analysis keys when done."),
    }
