#!/usr/bin/env python3
"""Pivot tier of articulation drafting: two arms crossing at a pin.

Scissors, pliers, tongs and shears are two arms (blade and handle each) that
cross at a small pin. The pin is the small compact part where the arms
overlap; the pivot axis is the arms' thin direction; one arm carries the
pin, the other turns about it. How open the file has them is measured from
the arms' long axes (principal directions of their points), so the limits
run from closed (arms aligned) to the class's opening.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _points(stage, path):
    from pxr import Gf, Usd, UsdGeom

    pts = []
    for p in Usd.PrimRange(stage.GetPrimAtPath(path)):
        if p.IsA(UsdGeom.Mesh):
            m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(0)
            pts += [list(m.Transform(Gf.Vec3d(*v))) for v in UsdGeom.Mesh(p).GetPointsAttr().Get()]
    return np.array(pts)


def propose_pivot(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = sorted(collect_parts(stage, asset_root), key=lambda p: -p["volume"])
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    a, b = parts[0], parts[1]
    span = max(max(a["size"]), max(b["size"]))
    pins = [p for p in parts[2:] if max(p["size"]) < 0.15 * span]
    # the pivot axis: the direction both arms are thin in
    axis = int(np.argmin(np.array(a["size"]) + np.array(b["size"])))
    plane = [k for k in range(3) if k != axis]
    if pins:
        pin = min(pins, key=lambda p: math.dist(p["centroid"], (np.array(a["centroid"]) + b["centroid"]) / 2))
        pivot = np.array(pin["centroid"])
    else:
        # no pin modelled: the middle of the arms' overlap
        lo = np.maximum(a["min"], b["min"])
        hi = np.minimum(a["max"], b["max"])
        pivot = (lo + hi) / 2
        pin = None

    def long_axis(part):
        pts = _points(stage, part["path"])[:, plane]
        d = np.linalg.svd(pts - pts.mean(0), full_matrices=False)[2][0]
        # point it from the pivot toward the arm's farther end
        far = pts[np.argmax(np.linalg.norm(pts - pivot[plane], axis=1))] - pivot[plane]
        return d if d @ far >= 0 else -d

    da, db = long_axis(a), long_axis(b)
    theta = math.degrees(math.atan2(da[0] * db[1] - da[1] * db[0], float(da @ db)))
    # two lines: an opening is never more than 90 degrees from closed
    if theta > 90:
        theta -= 180
    elif theta < -90:
        theta += 180
    s = 1.0 if theta >= 0 else -1.0
    # never narrower than the file's own pose, or the first step snaps it
    open_deg = max(float(template.get("open_deg", 60)), abs(theta))
    # the joint measures b relative to a: closed is -theta, open is s*open-theta
    lims = sorted([round(-theta, 2), round(s * open_deg - theta, 2)])
    names = "XYZ"
    joints = [{"name": "pivot", "joint_type": "revolute", "parent_prim": a["path"], "child_prim": b["path"],
               "axis": names[axis], "lower_limit": lims[0], "upper_limit": lims[1],
               "anchor": [round(float(v), 5) for v in pivot], "stiffness": 0.0, "damping": 0.02,
               "max_force": 100.0}]
    if pin is not None:
        joints.append({"name": "pin_on_arm", "joint_type": "fixed", "parent_prim": a["path"], "child_prim": pin["path"]})
    for p in parts[2:]:
        if p is not pin:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": a["path"], "child_prim": p["path"]})
    notes = [f"arms {Path(a['path']).name} and {Path(b['path']).name}, "
             f"pivot about {names[axis]} at {'the pin ' + Path(pin['path']).name if pin else 'their overlap'}; "
             f"modelled {abs(theta):.0f} deg open"]
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints,
            "_analysis": {"tier": "pivot", "modelled_open_deg": round(theta, 1), "notes": notes},
            "_instructions": "Pivot drafted from the arms and pin. CHECK which arm is fixed and the opening."}, notes
