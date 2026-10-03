#!/usr/bin/env python3
"""Turntable tier of articulation drafting: platter, tonearm and lid.

Up is the asset's up axis (Z after ingest).

  platter  the largest round flat part whose centre lies over the plinth:
           a revolute about the vertical through its centre (it spins; the
           class template's rpm is recorded for drives that play it).
  tonearm  an elongated part reaching from beside the platter to near its
           edge. Its pivot is the end away from the platter; small parts on
           the arm (counterweight, headshell) ride on it. The pivot housing
           - the part under the pivot reaching down to the plinth - stays
           on the base. Limits run from the modelled pose (on its rest) to
           the stylus at the lead-out groove, both measured from geometry.
  lid      a large part above the plinth covering at least the platter: a
           revolute about its lowest edge, from closed (flat over the
           plinth) to how far open it is modelled (or the template's).

Everything else is fixed to the plinth.
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


def _svd(pts):
    c = pts.mean(0)
    _, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
    return c, sv, vt


def _rot_z(v, centre, deg):
    a = math.radians(deg)
    d = np.array(v[:2]) - centre[:2]
    return centre[:2] + np.array([d[0] * math.cos(a) - d[1] * math.sin(a), d[0] * math.sin(a) + d[1] * math.cos(a)])


def propose_turntable(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts
    from thread_draft import _edge_points

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    size = lambda p: np.array(p["size"])  # noqa: E731
    pts_of = lambda p: _edge_points(stage, p["path"], max(p["size"]) / 24)  # noqa: E731

    # the plinth: the bulkiest part that is wider than it is tall
    plinths = [p for p in parts if size(p)[UP] < 0.6 * min(size(p)[:2])]
    if not plinths:
        raise RuntimeError("no plinth: nothing wider than tall")
    base = max(plinths, key=lambda p: p["volume"])
    b_lo, b_hi = np.array(base["min"]), np.array(base["max"])

    def over_base(p):
        c = p["centroid"]
        return b_lo[0] <= c[0] <= b_hi[0] and b_lo[1] <= c[1] <= b_hi[1]

    # platter: a round flat disc over the plinth, the largest such
    discs = []
    for p in parts:
        s = size(p)
        if p is base or not over_base(p) or s[UP] > 0.2 * min(s[:2]) or min(s[:2]) < 0.85 * max(s[:2]):
            continue
        _, sv, _ = _svd(pts_of(p)[:, :2])
        if sv[1] >= 0.8 * sv[0]:
            discs.append(p)
    if not discs:
        raise RuntimeError("no platter: no round flat part over the plinth")
    platter = max(discs, key=lambda p: max(size(p)[:2]))
    c = np.array(platter["centroid"])
    R = 0.5 * max(size(platter)[:2])
    joints, notes, taken = [], [], {base["path"], platter["path"]}
    rpm = float(template.get("rpm", 33.33))
    joints.append({"name": "platter", "joint_type": "revolute", "parent_prim": base["path"],
                   "child_prim": platter["path"], "axis": "Z", "lower_limit": -720.0, "upper_limit": 720.0,
                   "anchor": [round(float(v), 6) for v in c], "stiffness": 0.0, "damping": 1e-3, "max_force": 5.0,
                   "_role": "platter"})
    notes.append(f"platter {Path(platter['path']).name}, {2 * R * 1000:.0f} mm, spins at {rpm:g} rpm")

    # tonearm: elongated, lying beside the platter (parked on its rest) or
    # reaching over it. Its pivot is the end with the pivot housing or
    # counterweight beside it; failing that, the end farther from the platter.
    arm = None
    for p in sorted(parts, key=lambda p: -max(size(p)[:2])):
        if p["path"] in taken or not 0.5 * R <= max(size(p)[:2]) <= 3.0 * R:
            continue
        # an arm is long and low (an open dust cover is long from above too)
        if size(p)[UP] > 0.35 * max(size(p)[:2]):
            continue
        q = pts_of(p)
        _, sv, vt = _svd(q[:, :2])
        if sv[0] < 4 * max(sv[1], 1e-12):
            continue
        t = (q[:, :2] - q[:, :2].mean(0)) @ vt[0]
        ends = [q[int(np.argmin(t))], q[int(np.argmax(t))]]
        if max(np.linalg.norm(e[:2] - c[:2]) for e in ends) > 2.6 * R:
            continue
        length = float(t.max() - t.min())

        def company(e):
            near = [o for o in parts if o is not p and o["path"] not in taken
                    and max(size(o)) < 0.5 * length
                    and np.linalg.norm(np.array(o["centroid"][:2]) - e[:2]) < 0.2 * length]
            return sum(o["volume"] for o in near)

        ends.sort(key=lambda e: (company(e), np.linalg.norm(e[:2] - c[:2])))
        stylus, pivot = ends
        arm = p
        break
    if arm is not None:
        taken.add(arm["path"])
        a_lo, a_hi = np.array(arm["min"]), np.array(arm["max"])
        pad = 0.05 * max(size(arm))
        # The pivot housing is the largest part on the arm away from both of
        # its ends (a counterweight can sit behind the pivot); the stylus is
        # the end farther from it. Other small parts on the arm ride with it.
        q_arm = pts_of(arm)
        _, _, vt_a = _svd(q_arm[:, :2])
        t_arm = (q_arm[:, :2] - q_arm[:, :2].mean(0)) @ vt_a[0]
        length = float(t_arm.max() - t_arm.min())
        e_lo, e_hi = q_arm[int(np.argmin(t_arm))], q_arm[int(np.argmax(t_arm))]
        on_arm = []
        for p in parts:
            if p["path"] in taken or max(size(p)) > 0.6 * max(size(arm)):
                continue
            lo, hi = np.array(p["min"]), np.array(p["max"])
            if np.all(lo <= a_hi + pad) and np.all(hi >= a_lo - pad):
                on_arm.append(p)
        mid = [p for p in on_arm if min(np.linalg.norm(np.array(p["centroid"][:2]) - e[:2]) for e in (e_lo, e_hi))
               > 0.1 * length]
        housing = max(mid, key=lambda p: p["volume"]) if mid else None
        riders = [p for p in on_arm if p is not housing]
        if housing is not None:
            px, py = housing["centroid"][0], housing["centroid"][1]
            hp = np.array([px, py])
            stylus = max((e_lo, e_hi), key=lambda e: np.linalg.norm(e[:2] - hp))
        else:
            px, py = pivot[0], pivot[1]
        # how far the arm swings: stylus from rest to the lead-out groove
        lead_out = float(template.get("lead_out_fraction", 0.35)) * R

        def stylus_r(deg):
            s = _rot_z(stylus, np.array([px, py, 0.0]), deg)
            return float(np.linalg.norm(s - c[:2]))

        best = None
        for sgn in (1.0, -1.0):
            for k in range(1, 1800):
                deg = sgn * 0.1 * k
                if stylus_r(deg) <= lead_out:
                    if best is None or abs(deg) < abs(best):
                        best = deg
                    break
        if best is None:
            notes.append("tonearm cannot reach the lead-out groove from its pivot: limits are a guess")
            best = 30.0
        lims = sorted([0.0, round(best, 1)])
        joints.append({"name": "tonearm", "joint_type": "revolute", "parent_prim": base["path"],
                       "child_prim": arm["path"], "axis": "Z", "lower_limit": lims[0], "upper_limit": lims[1],
                       "anchor": [round(float(px), 6), round(float(py), 6), round(float(arm["centroid"][UP]), 6)],
                       "stiffness": 0.0, "damping": 2e-3, "max_force": 1.0})
        for r in riders:
            joints.append({"name": f"arm_part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": arm["path"], "child_prim": r["path"]})
            taken.add(r["path"])
        notes.append(f"tonearm {Path(arm['path']).name} pivots at {'its housing' if housing is not None else 'its far end'}, "
                     f"{abs(best):.0f} deg from rest to the lead-out groove"
                     + (f", {len(riders)} part(s) riding on it" if riders else ""))
    else:
        notes.append("no tonearm found")

    # lid: large, above the plinth, covering the platter; hinged at its lowest edge
    lid = None
    for p in sorted(parts, key=lambda p: -p["volume"]):
        if p["path"] in taken:
            continue
        s = size(p)
        if p["centroid"][UP] < b_hi[UP] or sorted(s)[1] < 1.8 * R:
            continue
        # a lid is a slab (a shallow hollow box at most), not a speaker box
        _, sv_l, _ = _svd(pts_of(p))
        if sv_l[2] > 0.25 * sv_l[0]:
            continue
        lid = p
        break
    if lid is not None:
        q = pts_of(lid)
        _, sv, vt = _svd(q)
        normal = vt[2]
        open_deg = math.degrees(math.acos(min(1.0, abs(float(normal[UP])))))
        # hinge: the lowest edge, along the horizontal axis it runs on
        low = q[q[:, UP] <= q[:, UP].min() + 0.03 * max(s)]
        span = low.max(0) - low.min(0)
        h_axis = 0 if span[0] >= span[1] else 1
        hinge = low.mean(0)
        names = "XY"
        # which way closes it: rotate the lid's centre and keep the side
        # that lands it over the plinth
        lc = np.array(lid["centroid"])
        axis_v = np.zeros(3)
        axis_v[h_axis] = 1.0

        def rotated(deg):
            """Rodrigues: the lid's centre turned about the hinge line."""
            a = math.radians(deg)
            d = lc - hinge
            r = d * math.cos(a) + np.cross(axis_v, d) * math.sin(a) + axis_v * (axis_v @ d) * (1 - math.cos(a))
            return hinge + r

        # closing lands the lid flat over the plinth; the other way swings it away
        bc = (b_lo + b_hi) / 2
        close = min((-open_deg, open_deg), key=lambda d: np.linalg.norm(rotated(d)[:2] - bc[:2]))
        extra = max(0.0, float(template.get("lid_open_deg", open_deg)) - open_deg)
        lims = sorted([close, -math.copysign(extra, close)])
        joints.append({"name": "lid", "joint_type": "revolute", "parent_prim": base["path"],
                       "child_prim": lid["path"], "axis": names[h_axis],
                       "lower_limit": round(lims[0], 1), "upper_limit": round(lims[1], 1),
                       "anchor": [round(float(v), 6) for v in hinge], "stiffness": 0.0, "damping": 0.05,
                       "max_force": 50.0})
        taken.add(lid["path"])
        notes.append(f"lid {Path(lid['path']).name} hinged on {names[h_axis]} at its lowest edge, "
                     f"modelled {open_deg:.0f} deg open")

    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": base["path"], "child_prim": p["path"]})
    spec = {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "turntable": {"rpm": rpm, "platter_joint": "platter"},
            "_analysis": {"tier": "turntable", "base": base["path"], "notes": notes},
            "_instructions": "Turntable drafted from the platter. CHECK the tonearm's swing and the lid hinge."}
    return spec, notes
