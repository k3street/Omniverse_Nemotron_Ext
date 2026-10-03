#!/usr/bin/env python3
"""Folding-arms tier of articulation drafting: eyeglasses' temples.

Two similar, parallel, elongated parts (the temples) run back from either
end of a front (the frame). Each temple hinges about the vertical at the
end nearest the front and folds inward, its far end swinging toward the
middle, through the class's fold angle. Small parts lying inside a
temple's outline (tips, hinge plates) ride on it; everything else is fixed
to the front. Up is Z.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

UP = 2


def propose_temples(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 3:
        raise RuntimeError("fewer than three parts — segment the mesh first")
    size = lambda p: np.array(p["size"])  # noqa: E731
    # temples: elongated along a horizontal axis D, thin across it
    cands = []
    for p in parts:
        s = size(p)
        d = int(np.argmax(s[:2]))
        if s[d] >= 4 * max(s[1 - d], 1e-9) and s[d] >= 2.5 * s[UP]:
            cands.append((p, d))
    pair = None
    for i, (a, da) in enumerate(cands):
        for b, db in cands[i + 1:]:
            if da == db and abs(a["size"][da] - b["size"][db]) < 0.2 * max(a["size"][da], b["size"][db]):
                w = 1 - da
                gap = abs(a["centroid"][w] - b["centroid"][w])
                if gap > 0.5 * a["size"][da] and (pair is None or a["size"][da] > pair[0]["size"][pair[2]]):
                    pair = (a, b, da)
    if pair is None:
        raise RuntimeError("no pair of parallel arms")
    a, b, D = pair
    W = 1 - D
    # the front: spans between the arms, at one end of them, thin along D
    lo_w, hi_w = sorted([a["centroid"][W], b["centroid"][W]])
    fronts = [p for p in parts if p is not a and p is not b
              and p["min"][W] <= lo_w + 0.15 * (hi_w - lo_w) and p["max"][W] >= hi_w - 0.15 * (hi_w - lo_w)
              and p["size"][D] < 0.5 * a["size"][D]]
    if not fronts:
        raise RuntimeError("no front spanning between the arms")
    front = max(fronts, key=lambda p: p["volume"])
    fc = front["centroid"][D]
    end_near = lambda arm: arm["min"][D] if abs(arm["min"][D] - fc) < abs(arm["max"][D] - fc) else arm["max"][D]  # noqa: E731
    fold = float(template.get("fold_deg", 95.0))
    names = "XYZ"
    joints, taken = [], {front["path"], a["path"], b["path"]}
    mid_w = 0.5 * (lo_w + hi_w)
    for i, arm in enumerate((a, b)):
        h = list(arm["centroid"])
        h[D] = end_near(arm)
        far = list(arm["centroid"])
        far[D] = arm["max"][D] if h[D] == arm["min"][D] else arm["min"][D]

        def turned(deg):
            t = math.radians(deg)
            dx, dy = far[0] - h[0], far[1] - h[1]
            return (h[0] + dx * math.cos(t) - dy * math.sin(t), h[1] + dx * math.sin(t) + dy * math.cos(t))

        # inward: the turn that brings the far end toward the middle
        sign = min((1.0, -1.0), key=lambda sg: abs(turned(sg * 90.0)[W] - mid_w))
        lims = sorted([0.0, sign * fold])
        name = f"temple_{i}"
        joints.append({"name": name, "joint_type": "revolute", "parent_prim": front["path"], "child_prim": arm["path"],
                       "axis": names[UP], "lower_limit": lims[0], "upper_limit": lims[1],
                       "anchor": [round(float(v), 6) for v in h], "stiffness": 0.0,
                       "damping": float(template.get("hinge_damping", 1e-3)), "max_force": 1.0})
        pad = 0.002
        for p in parts:
            if p["path"] in taken:
                continue
            inside = all(arm["min"][k] - pad <= p["min"][k] and p["max"][k] <= arm["max"][k] + pad for k in range(3))
            if inside and abs(p["centroid"][D] - h[D]) > 0.05 * arm["size"][D]:
                joints.append({"name": f"{name}_part_{len(joints):02d}", "joint_type": "fixed",
                               "parent_prim": arm["path"], "child_prim": p["path"]})
                taken.add(p["path"])
    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": front["path"], "child_prim": p["path"]})
    notes = [f"temples {Path(a['path']).name} and {Path(b['path']).name} fold inward {fold:g} deg "
             f"about {names[UP]} at the front {Path(front['path']).name}"]
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "_analysis": {"tier": "temples", "front": front["path"], "notes": notes},
            "_instructions": "Temples drafted from the arms. CHECK the hinge points."}, notes
