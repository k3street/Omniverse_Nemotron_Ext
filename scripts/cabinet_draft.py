#!/usr/bin/env python3
"""Cabinet tier of articulation drafting: drawers and doors on a carcass.

The carcass is the bulkiest part. A FRONT is a thin panel lying against
one of the carcass's vertical faces, inside its outline:

  drawer  a front wider than tall (or anything with a box behind it): a
          prismatic out along the face normal. Its box - parts behind the
          front inside its outline - and its handle - parts proud of it
          inside its outline - ride on it. It slides out by most of the box's
          depth (or of the carcass's, with no box modelled), damped.
  door    a front taller than wide: a revolute about its vertical edge
          away from its handle (the handle marks the opening edge), opening
          outward through the class's swing.

Everything else is fixed to the carcass. Up is Z.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

UP = 2
THIN = 0.2          # a front's depth is under this fraction of its smaller face side
FACE_TOL = 0.03     # of the carcass's depth: how near the face a front must lie


def propose_cabinet(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    size = lambda p: np.array(p["size"])  # noqa: E731
    body = max(parts, key=lambda p: p["volume"])
    b_lo, b_hi = np.array(body["min"]), np.array(body["max"])

    # fronts: thin panels against a vertical carcass face, inside its outline
    fronts = []
    for p in parts:
        if p is body:
            continue
        s = size(p)
        for ax in (0, 1):
            across = 1 - ax
            if s[ax] > THIN * min(s[across], s[UP]):
                continue
            for sgn in (1, -1):
                face = b_hi[ax] if sgn > 0 else b_lo[ax]
                near = abs((p["max"][ax] if sgn > 0 else p["min"][ax]) - face) < FACE_TOL * (b_hi[ax] - b_lo[ax]) + s[ax]
                inside = (p["min"][across] >= b_lo[across] - 0.01 and p["max"][across] <= b_hi[across] + 0.01
                          and p["min"][UP] >= b_lo[UP] - 0.01 and p["max"][UP] <= b_hi[UP] + 0.01)
                if near and inside and s[across] < 0.98 * (b_hi[across] - b_lo[across]) + 1e-9:
                    fronts.append((p, ax, sgn))
                    break
            else:
                continue
            break
    if not fronts:
        raise RuntimeError("no drawer or door fronts on the carcass's faces")
    # keep the face with the most fronts (a chest's drawers share one face)
    by_face = {}
    for f in fronts:
        by_face.setdefault((f[1], f[2]), []).append(f)
    (ax, sgn), fronts = max(by_face.items(), key=lambda kv: len(kv[1]))
    across = 1 - ax
    names = "XYZ"
    depth_c = b_hi[ax] - b_lo[ax]
    joints, notes, taken = [], [], {body["path"]} | {f[0]["path"] for f in fronts}
    n_drawers = n_doors = 0
    swing = float(template.get("door_swing_deg", 100.0))

    def within(p, f, pad=0.005):
        return (p["min"][across] >= f["min"][across] - pad and p["max"][across] <= f["max"][across] + pad
                and p["min"][UP] >= f["min"][UP] - pad and p["max"][UP] <= f["max"][UP] + pad)

    for f, _, _ in sorted(fronts, key=lambda t: (-t[0]["centroid"][UP], t[0]["centroid"][across])):
        riders_out = [p for p in parts if p["path"] not in taken and within(p, f)
                      and (p["centroid"][ax] - f["centroid"][ax]) * sgn > 0]          # handles
        riders_in = [p for p in parts if p["path"] not in taken and within(p, f)
                     and (p["centroid"][ax] - f["centroid"][ax]) * sgn < 0
                     and (p["min"][ax] if sgn > 0 else -p["max"][ax]) > (b_lo[ax] if sgn > 0 else -b_hi[ax]) - 1e-6]
        w, h = f["size"][across], f["size"][UP]
        is_drawer = w >= 1.0 * h or bool(riders_in)
        if is_drawer:
            box_depth = max((p["size"][ax] for p in riders_in), default=0.7 * depth_c)
            travel = round(float(template.get("drawer_open_fraction", 0.8)) * box_depth, 4)
            name = f"drawer_{n_drawers:02d}"
            n_drawers += 1
            joints.append({"name": name, "joint_type": "prismatic", "parent_prim": body["path"],
                           "child_prim": f["path"], "axis": names[ax],
                           "lower_limit": min(0.0, sgn * travel), "upper_limit": max(0.0, sgn * travel),
                           "anchor": [round(float(v), 6) for v in f["centroid"]], "stiffness": 0.0,
                           "damping": float(template.get("drawer_damping", 50.0)), "max_force": 500.0})
            riders = riders_out + riders_in
        else:
            handle = max(riders_out, key=lambda p: p["volume"]) if riders_out else None
            # the hinge is the vertical edge away from the handle
            if handle is not None and handle["centroid"][across] > f["centroid"][across]:
                hinge_at = f["min"][across]
                side = -1
            else:
                hinge_at = f["max"][across]
                side = 1
            anchor = list(f["centroid"])
            anchor[across] = hinge_at
            anchor[ax] = f["max"][ax] if sgn > 0 else f["min"][ax]
            # opening swings the free edge outward (+sgn along the face normal)
            # a +turn about +Z moves a point at +across... pick the sign that does
            r = -side  # direction from hinge to free edge along `across`
            out_for_plus = (r if ax == 1 else -r)  # Z x (r e_across) along e_ax
            lims = [0.0, swing] if out_for_plus * sgn > 0 else [-swing, 0.0]
            name = f"door_{n_doors:02d}"
            n_doors += 1
            joints.append({"name": name, "joint_type": "revolute", "parent_prim": body["path"],
                           "child_prim": f["path"], "axis": "Z", "lower_limit": lims[0], "upper_limit": lims[1],
                           "anchor": [round(float(v), 6) for v in anchor], "stiffness": 0.0,
                           "damping": float(template.get("door_damping", 2.0)), "max_force": 500.0})
            riders = riders_out
        for r_ in riders:
            joints.append({"name": f"{name}_part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": f["path"], "child_prim": r_["path"]})
            taken.add(r_["path"])
    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):03d}", "joint_type": "fixed",
                           "parent_prim": body["path"], "child_prim": p["path"]})
    notes.append(f"{n_drawers} drawer(s) and {n_doors} door(s) on the {'+-'[sgn < 0]}{names[ax]} face")
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "_analysis": {"tier": "cabinet", "body": body["path"], "notes": notes},
            "_instructions": "Cabinet drafted from its fronts. CHECK drawer travel and door hinge sides."}, notes
