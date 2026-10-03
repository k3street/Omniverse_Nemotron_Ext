#!/usr/bin/env python3
"""Power-drill tier of articulation drafting: chuck, clutch, trigger, battery.

A pistol-grip drill is a barrel along a horizontal spindle axis over a
handle, with a battery under the handle. Up is Z.

  spindle  the barrel's long horizontal axis; the front is the end whose
           cross-section is narrower (the chuck, not the motor housing).
  chuck    the largest round part (round across the spindle axis) at the
           front: a revolute about the spindle, spinning freely. Round parts
           in front of it (a bit, the nose) ride on it.
  clutch   a round ring just behind the chuck and wider than it: a revolute
           about the spindle with friction, through the class's settings arc.
  trigger  a small part under the barrel, in front of the handle: a
           prismatic toward the handle, sprung back.
  battery  the large part at the bottom: press-fitted to the body, pulled
           off above the class's force.

Everything else is fixed to the body (the largest part).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

UP = 2


def propose_drill(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = [p for p in collect_parts(stage, asset_root) if max(p["size"]) > 0]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    lo = np.min([p["min"] for p in parts], axis=0)
    hi = np.max([p["max"] for p in parts], axis=0)
    H = hi[UP] - lo[UP]
    size = lambda p: np.array(p["size"])  # noqa: E731
    body = max(parts, key=lambda p: p["volume"])

    # the barrel: parts in the top third; its long horizontal axis is the spindle
    top = [p for p in parts if p["centroid"][UP] > lo[UP] + 0.62 * H]
    if not top:
        raise RuntimeError("nothing in the top third: not a pistol-grip drill")
    t_lo = np.min([p["min"] for p in top], axis=0)
    t_hi = np.max([p["max"] for p in top], axis=0)
    ax = int(np.argmax((t_hi - t_lo)[:2]))
    side = 1 - ax

    def round_across(p):
        s = size(p)
        return min(s[side], s[UP]) >= 0.75 * max(s[side], s[UP]) and max(s[side], s[UP]) > 0

    # the front: the end of the barrel with the round parts out past the body
    def beyond(p, sgn):
        edge = body["max"][ax] if sgn > 0 else body["min"][ax]
        return (p["centroid"][ax] - edge) * sgn

    scores = {sgn: sum(1 for p in top if p is not body and round_across(p) and beyond(p, sgn) > -0.05 * (t_hi - t_lo)[ax])
              for sgn in (1, -1)}
    front = max(scores, key=scores.get)
    front_round = [p for p in top if p is not body and round_across(p) and beyond(p, front) > -0.1 * (t_hi - t_lo)[ax]]
    if not front_round:
        raise RuntimeError("no round parts at the front of the barrel: no chuck")
    # the chuck: the front-most substantial round part (the clutch collar
    # behind it is bulkier); the axis runs through it
    widest = max(max(size(p)[[side, UP]]) for p in front_round)
    substantial = [p for p in front_round if max(size(p)[[side, UP]]) >= 0.5 * widest]
    chuck = max(substantial, key=lambda p: (p["max"][ax] if front > 0 else -p["min"][ax]))
    axis_pt = np.array(chuck["centroid"])
    names = "XYZ"
    joints, notes, taken = [], [], {body["path"], chuck["path"]}
    joints.append({"name": "chuck", "joint_type": "revolute", "parent_prim": body["path"], "child_prim": chuck["path"],
                   "axis": names[ax], "lower_limit": -720.0, "upper_limit": 720.0,
                   "anchor": [round(float(v), 6) for v in axis_pt], "stiffness": 0.0, "damping": 1e-3, "max_force": 5.0})
    riders = [p for p in front_round if p is not chuck and (p["centroid"][ax] - chuck["centroid"][ax]) * front > 0
              and max(size(p)[[side, UP]]) <= max(size(chuck)[[side, UP]])]
    for r in riders:
        joints.append({"name": f"chuck_part_{len(joints):02d}", "joint_type": "fixed",
                       "parent_prim": chuck["path"], "child_prim": r["path"]})
        taken.add(r["path"])
    notes.append(f"chuck {Path(chuck['path']).name} spins about {names[ax]}"
                 + (f" with {len(riders)} part(s) ahead of it" if riders else ""))

    # clutch collar: round, just behind the chuck, wider than it
    behind = [p for p in front_round if p["path"] not in taken
              and (p["centroid"][ax] - chuck["centroid"][ax]) * front < 0
              and max(size(p)[[side, UP]]) > max(size(chuck)[[side, UP]])]
    if behind:
        clutch = min(behind, key=lambda p: abs(p["centroid"][ax] - chuck["centroid"][ax]))
        arc = float(template.get("clutch_arc_deg", 300.0))
        joints.append({"name": "clutch", "joint_type": "revolute", "parent_prim": body["path"],
                       "child_prim": clutch["path"], "axis": names[ax], "lower_limit": 0.0, "upper_limit": arc,
                       "anchor": [round(float(v), 6) for v in axis_pt], "stiffness": 0.0, "damping": 0.05,
                       "max_force": 5.0})
        taken.add(clutch["path"])
        notes.append(f"clutch collar {Path(clutch['path']).name} turns through {arc:g} deg of settings")

    # trigger: under the barrel, in front of the handle, small
    trig = [p for p in parts if p["path"] not in taken and p is not body
            and lo[UP] + 0.45 * H < p["centroid"][UP] < t_lo[UP] + 0.15 * H
            and max(size(p)) < 0.25 * H]
    if trig:
        trigger = max(trig, key=lambda p: (p["centroid"][ax] - body["centroid"][ax]) * front * p["volume"] ** 0.2)
        # a real trigger travels ~8-10 mm whatever its size
        travel = round(min(0.4 * size(trigger)[ax], float(template.get("trigger_travel_m", 0.01))), 4)
        force = float(template.get("trigger_force_n", 15.0))
        back = -float(front)
        joints.append({"name": "trigger", "joint_type": "prismatic", "parent_prim": body["path"],
                       "child_prim": trigger["path"], "axis": names[ax],
                       "lower_limit": min(0.0, back * travel), "upper_limit": max(0.0, back * travel),
                       "anchor": [round(float(v), 6) for v in trigger["centroid"]],
                       "stiffness": round(force / max(travel, 1e-4), 1), "damping": round(0.05 * force / max(travel, 1e-4), 2),
                       "max_force": 100.0, "_role": "button"})
        taken.add(trigger["path"])
        notes.append(f"trigger {Path(trigger['path']).name} pulls {travel * 1000:.1f} mm toward the handle at {force:g} N")

    # battery: the large part at the bottom
    mechanisms = []
    bottoms = [p for p in parts if p["path"] not in taken and p["centroid"][UP] < lo[UP] + 0.3 * H
               and max(size(p)) > 0.25 * H]
    if bottoms:
        battery = max(bottoms, key=lambda p: p["volume"])
        top_of = list(battery["centroid"])
        top_of[UP] = battery["max"][UP]
        mechanisms.append({"type": "press_fit", "holder": body["path"], "part": battery["path"],
                           "anchor": [round(float(v), 6) for v in top_of],
                           "break_force_n": float(template.get("battery_hold_n", 40.0))})
        taken.add(battery["path"])
        notes.append(f"battery {Path(battery['path']).name} slides off above "
                     f"{template.get('battery_hold_n', 40.0):g} N")
    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": body["path"], "child_prim": p["path"]})
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "mechanisms": mechanisms,
            "_analysis": {"tier": "power_drill", "body": body["path"], "notes": notes},
            "_instructions": "Drill drafted from its barrel. CHECK the chuck, trigger and battery."}, notes
