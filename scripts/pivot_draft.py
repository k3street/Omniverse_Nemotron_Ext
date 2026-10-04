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


def _half_points(stage, half, step=None):
    """Points of every part of a half (vertices, or with `step` along the edges too)."""
    from thread_draft import _edge_points

    paths = half.get("paths") or [half["path"]]
    return np.vstack([_edge_points(stage, q, step) if step else _points(stage, q) for q in paths])


def _bend(q):
    """How far a point cloud is from a straight bar: the second spread over the first."""
    sv = np.linalg.svd(q - q.mean(0), full_matrices=False)[1]
    return float(sv[1] / max(sv[0], 1e-12))


def _halves(stage, parts, span):
    """The two halves of a pair of pliers: each a metal arm and the grip on it.

    The two biggest parts are often the rubber grips, not the arms. A grip
    runs on into its own arm, so a half is one long, near-straight piece; of
    the ways to split the (non-pin) parts in two, each half spanning most of
    the tool, the one whose halves are straightest. (Least contact between
    halves fails: the arms' stacked rivet touches more than a grip does its
    arm.) Up to 10 parts; past that, the two biggest parts are the arms."""
    big = [p for p in parts if max(p["size"]) >= 0.15 * span]
    if len(big) <= 2 or len(big) > 10:
        return [parts[0]], [parts[1]]
    cell = 0.01 * span
    pts = {p["path"]: _half_points(stage, p, cell / 2) for p in big}

    n = len(big)
    lo = np.min([p["min"] for p in big], axis=0)
    hi = np.max([p["max"] for p in big], axis=0)
    length = float(max(hi - lo))

    def reach(side):
        return float(max(np.max([big[i]["max"] for i in side], axis=0) - np.min([big[i]["min"] for i in side], axis=0)))

    best = None
    for mask in range(1, 2 ** (n - 1)):
        A = [i for i in range(n) if not (mask >> i) & 1]
        B = [i for i in range(n) if (mask >> i) & 1]
        if reach(A) < 0.6 * length or reach(B) < 0.6 * length:
            continue
        # each half is one long, near-straight piece (a grip running on into
        # its arm); a grip paired with the other arm makes a bent or forked one
        score = sum(_bend(np.vstack([pts[big[i]["path"]] for i in side])) for side in (A, B))
        if best is None or score < best[0]:
            best = (score, A, B)
    if best is None:
        return [parts[0]], [parts[1]]
    return [big[i] for i in best[1]], [big[i] for i in best[2]]


def _as_half(members):
    root = max(members, key=lambda p: p["volume"])
    lo = np.min([p["min"] for p in members], axis=0)
    hi = np.max([p["max"] for p in members], axis=0)
    return {"path": root["path"], "paths": [p["path"] for p in members], "min": list(lo), "max": list(hi),
            "size": list(hi - lo), "centroid": list((lo + hi) / 2), "volume": root["volume"]}


def _crossing(pa, pb):
    """Where the two arms' long-axis lines cross in the plane, or None when
    they are near parallel (a closed pair lying side by side)."""
    def line(q):
        c = q.mean(0)
        d = np.linalg.svd(q - c, full_matrices=False)[2][0]
        return c, d
    (ca, da), (cb, db) = line(pa), line(pb)
    m = np.array([da, -db]).T
    if abs(np.linalg.det(m)) < math.sin(math.radians(4)):
        return None
    t = np.linalg.solve(m, cb - ca)
    return ca + t[0] * da


def _closing_contact(stage, a, b, pivot, plane, axis, span, direction, step=0.5, hub=0.12):
    """Degrees b turns (in `direction`, joint sign) before it first lands on a,
    looking down the pivot axis and ignoring the rivet's hub, where the arms
    lie stacked by design. None when they never touch within 120 degrees."""
    from thread_draft import _edge_points

    cell = 0.01 * span
    pv = np.array(pivot)[plane]
    pa = _half_points(stage, a, cell / 2)[:, plane]
    pb = _half_points(stage, b, cell / 2)[:, plane]
    pa = pa[np.linalg.norm(pa - pv, axis=1) > hub * span]
    pb = pb[np.linalg.norm(pb - pv, axis=1) > hub * span]
    if not len(pa) or not len(pb):
        return None
    occ = {tuple(k) for k in np.floor(pa / cell).astype(int)}
    # where the arms already lie over each other at rest is stacked by design
    # (a box joint's heads reach past any hub radius): only new overlap is contact
    rest = {tuple(k) for k in np.floor(pb / cell).astype(int)} & occ
    # and around it: turning a hair moves the stacked head's edges into the
    # next cells over, which is not the jaws meeting
    occ -= {(c[0] + du, c[1] + dv) for c in rest for du in range(-2, 3) for dv in range(-2, 3)}
    sgn = -1.0 if axis == 1 else 1.0          # joint sign to in-plane counter-clockwise
    rel = pb - pv
    for k in range(int(120 / step) + 1):
        q = math.radians(sgn * direction * k * step)
        c, s_ = math.cos(q), math.sin(q)
        rot = np.stack([c * rel[:, 0] - s_ * rel[:, 1], s_ * rel[:, 0] + c * rel[:, 1]], 1) + pv
        hits = sum(1 for key in {tuple(x) for x in np.floor(rot / cell).astype(int)} if key in occ)
        if hits >= 2:
            return max(0.0, (k - 1) * step)
    return None


def propose_pivot(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = sorted(collect_parts(stage, asset_root), key=lambda p: -p["volume"])
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    span = max(max(parts[0]["size"]), max(parts[1]["size"]))
    half_a, half_b = _halves(stage, parts, span)
    a, b = _as_half(half_a), _as_half(half_b)
    in_halves = {p["path"] for p in half_a + half_b}
    rest = [p for p in parts if p["path"] not in in_halves]
    pins = [p for p in rest if max(p["size"]) < 0.15 * span]
    # the pivot axis: the normal of the plane the tool lies in (the least
    # spread of both arms' points). Not the arms' thinnest bounding direction:
    # nearly closed pliers lying flat are narrower across than they are thick
    both_pts = np.vstack([_half_points(stage, a), _half_points(stage, b)])
    normal = np.linalg.svd(both_pts - both_pts.mean(0), full_matrices=False)[2][2]
    axis = int(np.argmax(np.abs(normal)))
    plane = [k for k in range(3) if k != axis]
    if pins:
        pin = min(pins, key=lambda p: math.dist(p["centroid"], (np.array(a["centroid"]) + b["centroid"]) / 2))
        pivot = np.array(pin["centroid"])
    else:
        # no pin modelled: where the arms lie over each other. Not the middle
        # of their bounding boxes' overlap - two arms of a pair of pliers both
        # run the tool's length, so that is mid-handle, and an arm turning
        # there pulls its jaw off the other's (measured: grandpa's pliers).
        # Looking down the pivot axis, the cells both arms' surfaces occupy
        # are the rivet's stack; the pivot is their middle.
        pin = None
        from thread_draft import _edge_points

        cell = 0.02 * span
        # vertices and points along the edges: a low-poly arm has few vertices
        pa, pb = _half_points(stage, a, cell / 2), _half_points(stage, b, cell / 2)
        ka = {tuple(k) for k in np.floor(pa[:, plane] / cell).astype(int)}
        kb = {tuple(k) for k in np.floor(pb[:, plane] / cell).astype(int)}
        shared = ka & kb
        lo = np.maximum(a["min"], b["min"])
        hi = np.minimum(a["max"], b["max"])
        pivot = (lo + hi) / 2
        if shared:
            # the arms may lie over each other in several places (the handles
            # too, when closed): the rivet is the patch where their lines cross
            patches, todo = [], set(shared)
            while todo:
                stack, patch = [todo.pop()], []
                while stack:
                    c = stack.pop()
                    patch.append(c)
                    for du in (-1, 0, 1):
                        for dv in (-1, 0, 1):
                            n = (c[0] + du, c[1] + dv)
                            if n in todo:
                                todo.remove(n)
                                stack.append(n)
                patches.append((np.array(patch, dtype=float) + 0.5) * cell)
            # a rivet is in from the tool's ends: closed jaws touch at one end,
            # closed handles at the other (measured: pliers_1's jaw tips)
            allp = np.vstack([pa[:, plane], pb[:, plane]])
            c0 = allp.mean(0)
            d0 = np.linalg.svd(allp - c0, full_matrices=False)[2][0]
            t = (allp - c0) @ d0
            t0, t1 = t.min(), t.max()

            def inward(q):
                f = ((q.mean(0) - c0) @ d0 - t0) / max(t1 - t0, 1e-9)
                return min(f, 1 - f)

            inner = [q for q in patches if inward(q) >= 0.12] or patches
            cross = _crossing(pa[:, plane], pb[:, plane])
            if cross is None:
                best = max(inner, key=inward)
            else:
                best = min(inner, key=lambda q: np.linalg.norm(q.mean(0) - cross))
            pivot[plane] = best.mean(0)

    def long_axis(part):
        pts = _half_points(stage, part)[:, plane]
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
    # theta is counter-clockwise in the plane's (u, v); a positive turn about
    # +Y is clockwise there (Z onto X), so for Y the joint's angle is -theta
    if axis == 1:
        theta = -theta
    s = 1.0 if theta >= 0 else -1.0
    # never narrower than the file's own pose, or the first step snaps it
    open_deg = max(float(template.get("open_deg", 60)), abs(theta))
    # the joint measures b relative to a: closed is -theta, open is s*open-theta
    lims = sorted([round(-theta, 2), round(s * open_deg - theta, 2)])
    contact = None
    if template.get("jaws_meet"):
        # pliers close until the jaws meet (the handles still apart), not to
        # the arms' long axes lining up (bent arms make that angle meaningless).
        # Closing the jaws closes the handles too, so closing is the way b
        # lands on a soonest, away from the rivet; opening is the other way.
        both = {d: _closing_contact(stage, a, b, pivot, plane, axis, span, d) for d in (1.0, -1.0)}
        hit = [d for d in both if both[d] is not None]
        if hit:
            d = min(hit, key=lambda k: both[k])
            contact = both[d]
            far = float(template.get("open_deg", 60))
            if both[-d] is not None:
                far = min(far, max(0.0, both[-d]))
            lims = sorted([round(d * contact, 2), round(-d * far, 2)])
    names = "XYZ"
    joints = [{"name": "pivot", "joint_type": "revolute", "parent_prim": a["path"], "child_prim": b["path"],
               "axis": names[axis], "lower_limit": lims[0], "upper_limit": lims[1],
               "anchor": [round(float(v), 5) for v in pivot], "stiffness": 0.0, "damping": 0.02,
               "max_force": 100.0}]
    if pin is not None:
        joints.append({"name": "pin_on_arm", "joint_type": "fixed", "parent_prim": a["path"], "child_prim": pin["path"]})
    for half, h in ((half_a, a), (half_b, b)):            # the grip rides on its arm
        for p in half:
            if p["path"] != h["path"]:
                joints.append({"name": f"half_part_{len(joints):02d}", "joint_type": "fixed",
                               "parent_prim": h["path"], "child_prim": p["path"]})
    for p in rest:
        if p is not pin:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": a["path"], "child_prim": p["path"]})
    notes = [f"arms {Path(a['path']).name} and {Path(b['path']).name}, "
             f"pivot about {names[axis]} at {'the pin ' + Path(pin['path']).name if pin else 'their overlap'}; "
             f"modelled {abs(theta):.0f} deg open"
             + (f", jaws meet after closing {contact:.1f} deg" if contact is not None else "")]
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints,
            "_analysis": {"tier": "pivot", "modelled_open_deg": round(theta, 1), "notes": notes},
            "_instructions": "Pivot drafted from the arms and pin. CHECK which arm is fixed and the opening."}, notes
