#!/usr/bin/env python3
"""Plunger tier of articulation drafting: pipettes (and syringes).

Along the tool's long axis: a body; a plunger standing on the axis above
it; optionally a tip ejector, a second long part beside the shaft that
reaches up to its own button; and a disposable tip at the narrow end. The
plunger gets a two-stop spring pair (add_mechanism.add_two_stop), the
ejector a sprung slide toward the tip, and the tip a press fit that breaks
when pushed (add_mechanism.add_press_fit). Everything else (shaft, cone,
finger rest) is fixed to the body.

The class prior's mechanism_templates.plunger gives what geometry cannot:
stroke and blow-out travel and forces, ejector travel and force, and the
force that pulls a tip off.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

LONG_FRACTION = 0.3   # body and ejector span at least this much of the length
TOP_FRACTION = 0.12   # the plunger sits within this much of the body's top
PUSH_OFF_M = 0.004    # how far past the tip's collar the ejector pushes


def propose_pipette(stage, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts

    parts = [p for p in collect_parts(stage, asset_root) if min(p["size"]) > 1e-5]
    if len(parts) < 2:
        raise RuntimeError("one part only — segment the mesh first")
    if template.get("single_stage"):
        return _propose_syringe(parts, asset_root, template)
    lo = np.min([p["min"] for p in parts], axis=0)
    hi = np.max([p["max"] for p in parts], axis=0)
    axis = int(np.argmax(hi - lo))
    other = [k for k in range(3) if k != axis]
    length = float(hi[axis] - lo[axis])

    def width(p):
        return max(p["size"][k] for k in other)

    # the tip end is the narrow one: the part reaching that end is slimmest there
    low_end = min(parts, key=lambda p: p["min"][axis])
    high_end = max(parts, key=lambda p: p["max"][axis])
    tip_end = -1 if width(low_end) <= width(high_end) else 1
    s = lambda v: tip_end * -v  # noqa: E731  (larger = further from the tip)

    longs = [p for p in parts if p["size"][axis] >= LONG_FRACTION * length]
    if not longs:
        raise RuntimeError("no part runs along the tool — not a pipette")
    body = max(longs, key=width)
    centre = np.array([body["centroid"][k] for k in other])
    tip_part = low_end if tip_end < 0 else high_end
    if tip_part is body:
        tip_part = None
    else:
        centre = np.array([tip_part["centroid"][k] for k in other])
    body_top = max(s(body["min"][axis]), s(body["max"][axis]))

    def on_axis(p):
        """Straddles the axis (a finger rest beside the plunger does not)."""
        return all(p["min"][k] <= c <= p["max"][k] for k, c in zip(other, centre))

    plunger = [p for p in parts if p is not body and p is not tip_part
               and min(s(p["min"][axis]), s(p["max"][axis])) >= body_top - TOP_FRACTION * length
               and on_axis(p)]
    ejector = next((p for p in sorted(longs, key=lambda p: -p["size"][axis])
                    if p is not body and p not in plunger), None)
    if not plunger:
        raise RuntimeError("nothing on the axis above the body — no plunger to press")

    # a hand pipette: ~12 mm stroke at ~10 N, ~4 mm blow-out at ~25 N, a tip
    # seated at ~15 N and an ejector pushing ~25 N (ergonomics literature)
    t = {"first_stop_m": 0.012, "blowout_m": 0.004, "first_force_n": 10.0, "blowout_force_n": 25.0,
         "ejector_travel_m": 0.006, "ejector_force_n": 25.0, "tip_hold_n": 15.0, **template}
    names = "XYZ"
    a = names[axis]
    press = float(tip_end)  # pushing goes toward the tip
    lead = max(plunger, key=lambda p: p["volume"])
    anchor = [0.0, 0.0, 0.0]
    for k, c in zip(other, centre):
        anchor[k] = float(c)
    anchor[axis] = float(lead["min"][axis] if tip_end < 0 else lead["max"][axis])

    joints = [{"name": f"plunger_{i:02d}", "joint_type": "fixed", "parent_prim": lead["path"],
               "child_prim": p["path"]} for i, p in enumerate(q for q in plunger if q is not lead)]
    # placeholder the two-stop pair replaces
    joints.append({"name": "plunger", "joint_type": "prismatic", "parent_prim": body["path"],
                   "child_prim": lead["path"], "axis": a,
                   "lower_limit": min(0.0, press * (t["first_stop_m"] + t["blowout_m"])),
                   "upper_limit": max(0.0, press * (t["first_stop_m"] + t["blowout_m"])), "anchor": anchor})
    mechanisms = [{"type": "two_stop", "body": body["path"], "plunger": lead["path"], "axis": a, "press": press,
                   "anchor": [round(v, 6) for v in anchor],
                   **{k: t[k] for k in ("first_stop_m", "blowout_m", "first_force_n", "blowout_force_n")}}]
    notes = [f"plunger {', '.join(Path(p['path']).name for p in plunger)} presses "
             f"{'-+'[press > 0]}{a}: {t['first_stop_m'] * 1000:g} mm to the first stop, "
             f"{t['blowout_m'] * 1000:g} mm more to blow-out"]
    if ejector is not None:
        travel = t["ejector_travel_m"]
        if tip_part is not None:
            # the sleeve must reach the tip's collar and push it off: models
            # often stop the sleeve short, at the top of the shaft
            e_end = ejector["min"][axis] if tip_end < 0 else ejector["max"][axis]
            tip_top = tip_part["max"][axis] if tip_end < 0 else tip_part["min"][axis]
            travel = round(max(travel, abs(e_end - tip_top) + PUSH_OFF_M), 4)
        e_anchor = list(anchor)
        joints.append({"name": "tip_ejector", "joint_type": "prismatic", "parent_prim": body["path"],
                       "child_prim": ejector["path"], "axis": a,
                       "lower_limit": min(0.0, press * travel), "upper_limit": max(0.0, press * travel),
                       "anchor": e_anchor, "stiffness": round(t["ejector_force_n"] / travel, 1),
                       "damping": round(0.02 * t["ejector_force_n"] / travel, 2), "max_force": 50.0})
        notes.append(f"tip ejector {Path(ejector['path']).name} slides {travel * 1000:g} mm toward the tip")
    if tip_part is not None:
        tip_top = tip_part["max"][axis] if tip_end < 0 else tip_part["min"][axis]
        t_anchor = list(anchor)
        t_anchor[axis] = float(tip_top)
        fit = {"type": "press_fit", "holder": body["path"], "part": tip_part["path"],
               "anchor": [round(v, 6) for v in t_anchor], "break_force_n": t["tip_hold_n"]}
        if ejector is not None:
            # released once the sleeve has pushed the collar a little way
            fit["released_by"] = {"joint": "tip_ejector",
                                  "travel_m": round(abs(e_end - tip_top) + 0.25 * PUSH_OFF_M, 4)}
        mechanisms.append(fit)
        notes.append(f"tip {Path(tip_part['path']).name} press-fitted, comes off above {t['tip_hold_n']:g} N")
    taken = {body["path"], *(p["path"] for p in plunger), *(j["child_prim"] for j in joints)}
    if tip_part is not None:
        taken.add(tip_part["path"])
    for p in parts:
        if p["path"] not in taken:
            joints.append({"name": f"part_{len(joints):02d}", "joint_type": "fixed",
                           "parent_prim": body["path"], "child_prim": p["path"]})
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "mechanisms": mechanisms,
            "_analysis": {"tier": "plunger", "body": body["path"], "notes": notes},
            "_instructions": "Plunger drafted from the parts along the axis. CHECK the travels and forces."}, notes


def _propose_syringe(parts, asset_root: str, template: dict) -> tuple[dict, list[str]]:
    """A syringe: the barrel is the widest long part; the plunger the other
    long part on its axis; the BACK is the end where the plunger stands out
    of the barrel (a syringe may stand tip-up or lie either way). The plunger
    pushes in until its seal nears the barrel's tip end and pulls out until
    the seal nears its back, held where it is left by friction. A small part
    past the tip is the needle."""
    lo = np.min([p["min"] for p in parts], axis=0)
    hi = np.max([p["max"] for p in parts], axis=0)
    axis = int(np.argmax(hi - lo))
    other = [k for k in range(3) if k != axis]
    longs = [p for p in parts if p["size"][axis] >= 0.4 * (hi - lo)[axis]]
    if len(longs) < 2:
        raise RuntimeError("one long part only — segment the plunger from the barrel")
    barrel = max(longs, key=lambda p: max(p["size"][k] for k in other))
    centre = [barrel["centroid"][k] for k in other]
    on_axis = [p for p in longs if p is not barrel
               and all(p["min"][k] <= c <= p["max"][k] for k, c in zip(other, centre))]
    if not on_axis:
        raise RuntimeError("no long part on the barrel's axis — no plunger")
    plunger = max(on_axis, key=lambda p: p["size"][axis])
    out_lo = barrel["min"][axis] - plunger["min"][axis]
    out_hi = plunger["max"][axis] - barrel["max"][axis]
    back = -1.0 if out_lo >= out_hi else 1.0            # the plunger stands out this way
    seal = plunger["max"][axis] if back < 0 else plunger["min"][axis]
    tip_inner = barrel["max"][axis] if back < 0 else barrel["min"][axis]
    back_inner = barrel["min"][axis] if back < 0 else barrel["max"][axis]
    push = round(0.95 * abs(tip_inner - seal), 4)
    pull = round(0.9 * abs(seal - back_inner), 4)
    press = -back                                        # pushing moves it toward the tip
    a = "XYZ"[axis]
    anchor = [0.0, 0.0, 0.0]
    for k, c in zip(other, centre):
        anchor[k] = float(c)
    anchor[axis] = float(seal)
    lims = sorted([press * push, -press * pull])
    joints = [{"name": "plunger", "joint_type": "prismatic", "parent_prim": barrel["path"],
               "child_prim": plunger["path"], "axis": a, "lower_limit": lims[0], "upper_limit": lims[1],
               "anchor": [round(v, 6) for v in anchor], "stiffness": 0.0,
               "damping": float(template.get("plunger_damping", 20.0)), "max_force": 100.0}]
    for p in parts:
        if p is barrel or p is plunger:
            continue
        beyond_tip = (p["centroid"][axis] - tip_inner) * press > 0
        inside_plunger = all(plunger["min"][k] - 1e-3 <= p["min"][k] and p["max"][k] <= plunger["max"][k] + 1e-3
                             for k in range(3))
        parent = plunger if inside_plunger else barrel
        joints.append({"name": "needle" if beyond_tip else f"part_{len(joints):02d}", "joint_type": "fixed",
                       "parent_prim": parent["path"], "child_prim": p["path"]})
    notes = [f"plunger {Path(plunger['path']).name} pushes {push * 1000:.0f} mm in and pulls "
             f"{pull * 1000:.0f} mm out of the barrel along {a}, held by friction"]
    return {"prim_path": asset_root, "fixed_base": False, "approximation": "convexDecomposition",
            "joints": joints, "mechanisms": [],
            "_analysis": {"tier": "plunger", "body": barrel["path"], "notes": notes},
            "_instructions": "Syringe drafted from the barrel. CHECK the stroke."}, notes
