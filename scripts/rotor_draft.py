#!/usr/bin/env python3
"""Rotor tier of articulation drafting: a multirotor's propellers spin.

Up is the asset's up axis (Z after ingest).

  stations  where the motors are: small round parts (as wide as deep),
            away from the centre, that come in copies - the same size at
            the same distance from the centre, one per arm. Copies of each
            kind are clustered by where they stand; 2 to 8 stations at one
            radius make the rotor layout.
  rotors    at each station, the parts whose middle is above the motor's
            mid-height and whose footprint covers the station: long flat
            blades (longer than the motor is wide) and a spinner cap centred
            on it. A revolute about the vertical through the station turns
            them; the motor stack stays on the frame.

Everything else is fixed to the frame (the largest part). Neighbouring
rotors turn opposite ways on a real multirotor; the joints are free, and the
template's rpm is recorded for drives that spin them.
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


def _stations(parts, centre, span):
    """Motor stations: (xy, diameter, top z, member parts), or []."""
    round_ = []
    for p in parts:
        sx, sy = p["size"][0], p["size"][1]
        d = max(sx, sy)
        r = math.dist(p["centroid"][:2], centre)
        if d <= 0 or abs(sx - sy) > 0.25 * d or d > 0.25 * span or r < 0.2 * span:
            continue
        round_.append(p)
    # the layout's centre is the motors' own (blades skew the bounding box):
    # the middle of the round parts that come in copies (by size alone here; a
    # single spinner cap would pull the middle toward its rotor)
    copies = [p for p in round_ if sum(
        1 for q in round_ if abs(max(p["size"][:2]) - max(q["size"][:2])) <= 0.12 * max(q["size"][:2])
        and abs(p["size"][UP] - q["size"][UP]) <= 0.15 * max(q["size"][UP], 1e-6)) >= 2]
    if copies:
        centre = np.mean([p["centroid"][:2] for p in copies], axis=0)
    # copies: the same size at the same distance from the centre
    kinds = []
    for p in round_:
        r = math.dist(p["centroid"][:2], centre)
        for k in kinds:
            q = k[0]
            if (abs(max(p["size"][:2]) - max(q["size"][:2])) <= 0.12 * max(q["size"][:2])
                    and abs(p["size"][UP] - q["size"][UP]) <= 0.15 * max(q["size"][UP], 1e-6)
                    and abs(r - math.dist(q["centroid"][:2], centre)) <= 0.15 * r):
                k.append(p)
                break
        else:
            kinds.append([p])
    kinds = [k for k in kinds if 2 <= len(k) <= 8]
    if not kinds:
        return []
    # stations: cluster every copy's xy
    pts = [(np.array(p["centroid"][:2]), p) for k in kinds for p in k]
    tol = 0.6 * float(np.median([max(p["size"][:2]) for _, p in pts]))
    groups = []
    for xy, p in pts:
        for g in groups:
            if np.linalg.norm(g["xy"] - xy) <= tol:
                g["members"].append(p)
                g["xy"] = np.mean([m["centroid"][:2] for m in g["members"]], axis=0)
                break
        else:
            groups.append({"xy": xy.copy(), "members": [p]})
    # the rotor layout: stations at one radius, at least two parts stacked or 3+ stations
    groups = [g for g in groups if len(g["members"]) >= 2] or groups
    radii = np.array([np.linalg.norm(g["xy"] - centre) for g in groups])
    if len(groups) < 2:
        return []
    med = float(np.median(radii))
    groups = [g for g, r in zip(groups, radii) if abs(r - med) <= 0.2 * med]
    if not 2 <= len(groups) <= 8:
        return []
    out = []
    for g in groups:
        dia = max(max(m["size"][:2]) for m in g["members"])
        top = max(m["max"][UP] for m in g["members"])
        mid = 0.5 * (min(m["min"][UP] for m in g["members"]) + top)
        out.append({"xy": g["xy"], "dia": dia, "top": top, "mid": mid, "members": g["members"]})
    return out


def propose_rotors(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    lo = np.min([p["min"] for p in parts], axis=0)
    hi = np.max([p["max"] for p in parts], axis=0)
    centre = (lo[:2] + hi[:2]) / 2
    span = float(max(hi[:2] - lo[:2]))
    stations = _stations(parts, centre, span)
    if not stations:
        raise RuntimeError("no rotor stations: no repeated round motor parts around the centre "
                           "(segment the mesh first, or the props are folded)")
    frame = max(parts, key=lambda p: p["volume"] if math.dist(p["centroid"][:2], centre) < 0.2 * span else 0)
    motors = {m["path"] for s in stations for m in s["members"]}
    taken = {frame["path"]}
    joints, notes = [], []
    rpm = float(template.get("rpm", 6000))
    for i, s in enumerate(stations):
        tol = 0.15 * s["dia"]

        def covers(p):
            return all(p["min"][k] - tol <= s["xy"][k] <= p["max"][k] + tol for k in (0, 1))

        # blades: long flat parts over the station, above the motor's middle
        blades = [p for p in parts if p["path"] not in taken | motors and covers(p) and p["centroid"][UP] >= s["mid"]
                  and max(p["size"][:2]) >= 1.4 * s["dia"]]
        if not blades:
            notes.append(f"station {i} at ({s['xy'][0] * 1000:.0f}, {s['xy'][1] * 1000:.0f}) mm: no blades over it")
            continue
        z_b = min(p["min"][UP] for p in blades)
        # caps: round parts centred on the station at blade height or above (a
        # spinner, the prop's hub) - station members or not - turn with them
        caps = [p for p in parts if p not in blades and p["path"] not in taken
                and math.dist(p["centroid"][:2], s["xy"]) <= 0.5 * s["dia"]
                and max(p["size"][:2]) <= 1.2 * s["dia"] and p["min"][UP] >= z_b - 0.1 * s["dia"]]
        on = blades + caps
        hub = max(blades, key=lambda p: max(p["size"][:2]))
        joints.append({"name": f"rotor_{i}", "joint_type": "revolute", "parent_prim": frame["path"],
                       "child_prim": hub["path"], "axis": "Z", "lower_limit": None, "upper_limit": None,
                       "anchor": [round(float(s["xy"][0]), 6), round(float(s["xy"][1]), 6), round(float(z_b), 6)],
                       "stiffness": 0.0, "damping": 1e-4, "max_force": 1.0, "_role": "rotor"})
        for p in on:
            if p is not hub:
                joints.append({"name": f"rotor_{i}_part_{len(joints):02d}", "joint_type": "fixed",
                               "parent_prim": hub["path"], "child_prim": p["path"]})
        taken |= {p["path"] for p in on}
        notes.append(f"rotor {i} at ({s['xy'][0] * 1000:.0f}, {s['xy'][1] * 1000:.0f}) mm: "
                     f"{len(blades)} blade part(s), {len(caps)} cap(s) over a {s['dia'] * 1000:.0f} mm motor")
    if not any(j["joint_type"] == "revolute" for j in joints):
        raise RuntimeError("motor stations found, but no blades over them: " + "; ".join(notes))
    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": frame["path"], "child_prim": p["path"]})
    spec = {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints,
            "_analysis": {"tier": "rotors", "frame": frame["path"], "rpm": rpm, "notes": notes},
            "_instructions": "Rotors drafted from the motor stations. CHECK which parts spin with each rotor."}
    return spec, [f"{sum(j['joint_type'] == 'revolute' for j in joints)} rotors"] + notes
