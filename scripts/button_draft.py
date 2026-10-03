#!/usr/bin/env python3
"""Button tier of articulation drafting: keys and push buttons on a body.

Remotes, keyboards, elevator panels and watch pushers are a body with small
parts standing on one of its faces. Each such part becomes a short prismatic
joint pressing into that face, sprung back to rest; labels and trim lying
inside a button's outline ride on the button; parts that duplicate another
exactly (the same geometry modelled twice) are fixed to it.

The class prior's mechanism_templates.buttons supplies what geometry cannot
measure: press force and the longest travel.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FACE_TOL_M = 0.004       # a button's inner side within this of the body's face
MAX_BUTTON_FRACTION = 0.4  # a button's face is at most this fraction of the body's face span
KNOB_PROUD = 0.4           # a round control standing this many diameters proud is a knob


def _overlap(a_lo, a_hi, b_lo, b_hi) -> bool:
    return a_lo <= b_hi and b_lo <= a_hi


def propose_buttons(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = collect_parts(stage, asset_root)
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    body = max(parts, key=lambda p: p["volume"])
    notes = []
    force = float(template.get("press_force_n", 3.0))
    max_travel = float(template.get("max_travel_m", 0.005))

    buttons, decorations, duplicates = [], [], []
    seen = {}
    for p in parts:
        if p is body:
            continue
        key = tuple(round(v, 4) for v in p["min"] + p["max"])
        if key in seen:
            duplicates.append((p, seen[key]))
            continue
        seen[key] = p
        # which face of the body does it stand on? the axis where it pokes out
        for axis in range(3):
            for sign, face in ((1, body["max"][axis]), (-1, body["min"][axis])):
                inner = p["min"][axis] if sign > 0 else p["max"][axis]
                outer = p["max"][axis] if sign > 0 else p["min"][axis]
                proud = (outer - face) * sign
                if abs((inner - face) * sign) > FACE_TOL_M and not (inner - face) * sign < 0 < proud:
                    continue
                if proud <= 0.0005:
                    continue
                others = [k for k in range(3) if k != axis]
                small = all(p["size"][k] <= MAX_BUTTON_FRACTION * body["size"][k] for k in others)
                within = all(_overlap(p["min"][k], p["max"][k], body["min"][k], body["max"][k]) for k in others)
                if small and within:
                    p["face"] = (axis, sign)
                    break
            if "face" in p:
                break
        if "face" not in p:
            continue
        # thin flat parts lying inside another candidate's outline are its trim
        buttons.append(p)

    def contains(outer, inner, axis):
        others = [k for k in range(3) if k != axis]
        return all(outer["min"][k] - 0.002 <= inner["min"][k] and inner["max"][k] <= outer["max"][k] + 0.002
                   for k in others)

    real = []
    for b in sorted(buttons, key=lambda q: -q["size"][[k for k in range(3) if k != q["face"][0]][0]]):
        host = next((r for r in real if r["face"] == b["face"] and contains(r, b, b["face"][0])), None)
        if host is not None:
            decorations.append((b, host))
        else:
            real.append(b)

    # Labels, insets and caps sit ON a button, in front of the body's face, so
    # they never stand on the body itself: anything inside a button's outline
    # on the button's side of the face rides on that button.
    taken = {id(b) for b in real} | {id(d) for d, _ in decorations} | {id(d) for d, _ in duplicates}
    for p in parts:
        if p is body or id(p) in taken:
            continue
        for b in real:
            axis, sign = b["face"]
            face = body["max"][axis] if sign > 0 else body["min"][axis]
            if (p["centroid"][axis] - face) * sign > 0 and contains(b, p, axis):
                decorations.append((p, b))
                break

    # knobs: round across the face and standing well proud - they turn
    def is_knob(b):
        axis, _ = b["face"]
        across = [b["size"][k] for k in range(3) if k != axis]
        return min(across) >= 0.85 * max(across) and b["size"][axis] >= KNOB_PROUD * max(across)

    # only where the class has knobs (audio gear, cameras): a remote's tall
    # round button is still a button
    knobs = [b for b in real if is_knob(b)] if template.get("knobs") else []
    real = [b for b in real if b not in knobs]
    knob_range = float(template.get("knob_range_deg", 150.0))
    joints = []
    for n, k in enumerate(sorted(knobs, key=lambda q: (q["face"], q["centroid"][2], q["centroid"][0]))):
        axis, sign = k["face"]
        name = f"knob_{n:02d}"
        k["joint"] = name
        joints.append({"name": name, "joint_type": "revolute", "parent_prim": body["path"], "child_prim": k["path"],
                       "axis": "XYZ"[axis], "lower_limit": -knob_range, "upper_limit": knob_range,
                       "anchor": [round(v, 5) for v in k["centroid"]], "stiffness": 0.0,
                       "damping": float(template.get("knob_damping", 1e-3)), "max_force": 1.0, "_role": "knob"})
    for n, b in enumerate(sorted(real, key=lambda q: (q["face"], q["centroid"][2], q["centroid"][0]))):
        axis, sign = b["face"]
        depth = b["size"][axis]
        travel = round(min(0.5 * depth, max_travel), 4)
        name = f"button_{n:02d}"
        b["joint"] = name
        anchor = list(b["centroid"])
        anchor[axis] = body["max"][axis] if sign > 0 else body["min"][axis]
        joints.append({
            "name": name, "joint_type": "prismatic", "parent_prim": body["path"], "child_prim": b["path"],
            "axis": "XYZ"[axis],
            # pressing moves it into the body: toward -axis on a +face
            "lower_limit": -travel if sign > 0 else 0.0, "upper_limit": 0.0 if sign > 0 else travel,
            "anchor": [round(v, 5) for v in anchor],
            # sprung: full travel takes the template's press force
            "stiffness": round(force / max(travel, 1e-4), 1), "damping": round(0.05 * force / max(travel, 1e-4), 2),
            "max_force": 50.0, "_role": "button",
        })
    for i, (d, host) in enumerate(decorations):
        # meshes often share names (two 'Cube_005_Red_0' on one button)
        joints.append({"name": f"trim_{i:03d}_on_{host['joint']}",
                       "joint_type": "fixed", "parent_prim": host["path"], "child_prim": d["path"]})
    for d, original in duplicates:
        parent = original if "joint" in original else body
        joints.append({"name": f"dup_{len(joints):03d}", "joint_type": "fixed",
                       "parent_prim": parent["path"], "child_prim": d["path"]})
    # everything else is fixed to the body
    linked = {j["child_prim"] for j in joints}
    for p in parts:
        if p is not body and p["path"] not in linked:
            joints.append({"name": f"part_{len(joints):03d}", "joint_type": "fixed",
                           "parent_prim": body["path"], "child_prim": p["path"]})
    if not real and not knobs:
        raise RuntimeError("no parts standing proud of the body's faces — nothing to press")
    notes.append(f"{len(real)} buttons, {len(knobs)} knobs, {len(decorations)} labels/trim riding on them, "
                 f"{len(duplicates)} duplicate parts merged")
    return {
        # a wall panel is fixed where it is mounted; a remote is loose
        "prim_path": asset_root, "fixed_base": bool(template.get("mounted", False)), "approximation": "convexHull",
        "joints": joints, "button_joints": [j["name"] for j in joints if j.get("_role") == "button"],
        "knob_joints": [j["name"] for j in joints if j.get("_role") == "knob"],
        "_analysis": {"tier": "buttons", "body": body["path"], "buttons": len(real), "notes": notes},
        "_instructions": "Buttons drafted from parts standing proud of the body. CHECK travel and press force.",
    }, notes
