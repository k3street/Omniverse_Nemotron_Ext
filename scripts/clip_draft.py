#!/usr/bin/env python3
"""Clip tier of articulation drafting: spring clips (clothes pegs).

A clip is two similar halves lying side by side, held shut by a spring
between them. The halves are the two largest parts of matching size,
adjacent across their thinnest shared direction (the separation); the
spring is whatever sits between them and rides on one half. The pivot is
the spring's centre and the hinge axis is perpendicular to both the
halves' length and their separation.

The spring is a torsion spring preloaded against the closed stop: the
joint's drive aims past closed, so the jaws stay shut until a squeeze
passes the preload, and open to the class's angle at the full force (the
same preload pattern as add_mechanism.add_two_stop).

Which end is the jaw cannot be told from a symmetric clip: the drafter
opens the end toward +length; the reviewer checks it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def propose_clip(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = sorted([p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0], key=lambda p: -p["volume"])
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    a, b = parts[0], parts[1]
    sa, sb = np.array(a["size"]), np.array(b["size"])
    if not np.allclose(np.sort(sa), np.sort(sb), rtol=0.25):
        raise RuntimeError("the two largest parts are not a matching pair of halves")
    ca, cb = np.array(a["centroid"]), np.array(b["centroid"])
    sep = int(np.argmax(np.abs(cb - ca)))                 # the halves lie apart along this
    length = int(np.argmax(np.where(np.arange(3) == sep, -1, sa)))
    hinge = 3 - sep - length                               # perpendicular to both
    names = "XYZ"
    rest = parts[2:]
    # the spring: parts between the halves; its centre is the fulcrum
    between = [p for p in rest if min(ca[sep], cb[sep]) - 0.5 * sa[sep] <= p["centroid"][sep]
               <= max(ca[sep], cb[sep]) + 0.5 * sa[sep]]
    if between:
        lo = np.min([p["min"] for p in between], axis=0)
        hi = np.max([p["max"] for p in between], axis=0)
        fulcrum = (lo + hi) / 2
    else:
        fulcrum = (ca + cb) / 2
    fulcrum[sep] = (ca[sep] + cb[sep]) / 2
    open_deg = float(template.get("open_deg", 25.0))
    preload = float(template.get("preload_torque_nm", 0.15))
    full = float(template.get("full_torque_nm", 0.4))
    # opening the +length end: b swings away from a there. A turn of +q
    # about the hinge axis moves +length toward +sep when (hinge, length,
    # sep) is a right-handed cycle.
    right_handed = (hinge, length, sep) in ((0, 1, 2), (1, 2, 0), (2, 0, 1))
    away = 1.0 if cb[sep] > ca[sep] else -1.0
    sign = away * (1.0 if right_handed else -1.0)
    # which end opens: squeezing the handles opens the jaws until the handles
    # meet; turned the other way the shut jaws run into each other at once.
    # So the way with more room before the halves meet is the opening, and it
    # stops there (measured: a peg opened at its handle end, jaws through jaws)
    from swing_contact import swing_until_contact

    span = float(max(max(sa), max(sb)))
    room = {d: swing_until_contact(stage, [a["path"]], [b["path"]], fulcrum, hinge, d, span) for d in (1.0, -1.0)}
    roomy = {d: (v if v is not None else float("inf")) for d, v in room.items()}
    if roomy[-sign] > roomy[sign]:
        sign = -sign
    if room[sign] is not None:
        if room[sign] < 2.0:
            raise RuntimeError(f"the halves meet within {room[sign]:g} deg either way: no room to open")
        open_deg = min(open_deg, room[sign])
    lims = sorted([0.0, sign * open_deg])
    k = (full - preload) / open_deg                         # N m per degree
    joints = [{"name": "clip_hinge", "joint_type": "revolute", "parent_prim": a["path"], "child_prim": b["path"],
               "axis": names[hinge], "lower_limit": round(lims[0], 2), "upper_limit": round(lims[1], 2),
               "anchor": [round(float(v), 6) for v in fulcrum],
               # the drive aims past closed by the preload: shut at rest
               "stiffness": round(k, 6), "damping": round(0.02 * k, 6), "max_force": 10.0,
               "target": round(-sign * preload / k, 3)}]
    for p in rest:
        joints.append({"name": f"spring_{len(joints):02d}" if p in between else f"part_{len(joints):02d}",
                       "joint_type": "fixed", "parent_prim": a["path"], "child_prim": p["path"]})
    notes = [f"halves {Path(a['path']).name} and {Path(b['path']).name} hinge about {names[hinge]} at the spring; "
             f"shut until {preload:g} N m, {open_deg:g} deg open at {full:g} N m - the way with room before "
             f"the halves meet (CHECK which end is the jaw)"]
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexHull", "joints": joints,
            "_analysis": {"tier": "clip", "notes": notes},
            "_instructions": "Clip drafted from its halves and spring. CHECK which end is the jaw."}, notes
