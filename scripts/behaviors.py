#!/usr/bin/env python3
"""Behaviors: what an object DOES when it is worked, by convention.

A joint graph says how parts can move; a mechanism (add_mechanism) couples
one joint to another physically or gates it by rule (a crash bar's latch).
Some objects also have a control law: their inputs decide how their outputs
are driven. A cordless drill's chuck spins only while the battery is on and
the trigger is pulled, as fast as the trigger is pulled, the way the
direction switch says. A powered wheelchair's joystick sets its drive
wheels' speeds and its casters swivel to follow.

A behavior is a record on the asset's root (customData simReady:behaviors,
a list) naming its roles - joints of the asset - and the parameters of its
law. The laws live here as pure functions of the joints' state, so the
PhysX check (animate_asset.py) and any runtime that operates the asset
(a robot's sim, a scene script) drive it the same way.

What an asset should do is reasoned at ingest, like its scale and materials:
its class's behaviors (the priors' "behaviors" list) and the functions the
classifying VLM saw it perform (a drill spins its chuck by its trigger).
check(entry) reports each expected behavior, whether the asset's draft has
it and which roles (KIND_ROLES) it lacks - a drill with no direction switch,
a wheelchair whose casters do not swivel.

    python scripts/behaviors.py check <asset_id> [...]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"

# behavior kind -> the roles it needs (joint names in the draft) and what each
# is for. Optional roles end with "?". Which classes have which kinds is data:
# the class priors' "behaviors" list (a class the VLM proposes brings its own,
# from the functions it saw).
KIND_ROLES = {
    "motor": {"output": "the part the motor turns (a chuck, a spindle)",
              "throttle": "the control that sets the speed (a trigger)",
              "direction?": "the switch that sets which way (forward, reverse, centred locked)",
              "power?": "what powers it (a battery on its press-fit)"},
    "wheeled_base": {"drive_left": "the left drive wheels", "drive_right": "the right drive wheels",
                     "casters?": "casters that swivel to follow"},
    "gate": {"gated": "the part that moves only after another (a door leaf)",
             "actuator": "the part that frees it (a crash bar, a key)"},
}
PRIORS_PATH = REPO / "workspace" / "knowledge" / "asset_class_priors.json"


def expected(entry: dict) -> dict:
    """The behaviors the asset should have: its class's (priors), and what the
    classifying VLM saw it do (its functions). kind -> where that came from."""
    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    prior = json.loads(PRIORS_PATH.read_text())["classes"].get(cls or "", {})
    out = {k: f"class {cls}" for k in prior.get("behaviors", []) if k in KIND_ROLES}
    for f in (entry.get("vlm") or {}).get("functions") or []:
        if f.get("kind") in KIND_ROLES:
            out.setdefault(f["kind"], f"seen: {f.get('does')}")
    return out


# --- laws ------------------------------------------------------------------------

def motor(b: dict, q: dict, powered: bool = True) -> dict:
    """A motor tool (a drill, a driver): the output's target velocity (deg/s,
    joint sign) from its inputs' positions q (joint units).

    b: {"output", "throttle", "direction", "max_rpm", "forward_sign",
        "throttle_full", "direction_forward"} - throttle_full is the trigger's
    full travel (signed), direction_forward the switch's forward position
    (signed); forward_sign the output's joint sign for forward (clockwise
    seen from behind the tool). The switch is forward past a third of its
    travel toward forward, reverse past a third the other way, and locked
    (no drive, trigger blocked) in between."""
    t = q.get(b["throttle"], 0.0) / b["throttle_full"] if b.get("throttle_full") else 0.0
    t = min(max(t, 0.0), 1.0)
    d = 0.0
    if b.get("direction"):
        s = q.get(b["direction"], 0.0) / b["direction_forward"] if b.get("direction_forward") else 0.0
        d = 1.0 if s > 1 / 3 else -1.0 if s < -1 / 3 else 0.0
    else:
        d = 1.0
    w = (b.get("max_rpm", 1500.0) * 6.0) * t * d * b.get("forward_sign", 1.0) if powered else 0.0
    return {b["output"]: w, "_state": {"throttle": round(t, 3), "direction": d, "powered": powered}}


def wheeled_base(b: dict, v: float, w: float) -> dict:
    """Differential drive: wheel target velocities (deg/s, joint sign) for a
    forward speed v (m/s) and a turn rate w (rad/s, left positive).

    b: {"drive_left": [joints], "drive_right": [joints], "radius_m",
        "track_m", "forward_sign": {joint: +-1}} - forward_sign is the joint
    sign that rolls each wheel forward."""
    r, track = b["radius_m"], b["track_m"]
    vl, vr = v - w * track / 2, v + w * track / 2
    out = {}
    for side, vs in (("drive_left", vl), ("drive_right", vr)):
        for j in b.get(side, []):
            out[j] = math.degrees(vs / r) * b.get("forward_sign", {}).get(j, 1.0)
    return out


LAWS = {"motor": motor, "wheeled_base": wheeled_base}


# --- drafting a behavior from joints already drafted -----------------------------

def draft_wheeled_base(stage, spec: dict, up: int = 2) -> tuple[dict | None, list[str]]:
    """A wheeled base from the draft's wheel joints (wheel_L_*, wheel_R_*):
    the drive wheels are each side's biggest, the rest are casters; forward
    points from the drive wheels toward the casters (a wheelchair's small
    wheels are in front); each wheel's forward sign is the joint sign that
    rolls it that way (a wheel rolling along f turns about up x f)."""
    import numpy as np

    from articulation_draft import collect_parts

    wheels = [j for j in spec.get("joints", []) if j["joint_type"] == "revolute"
              and j["name"].startswith(("wheel_L", "wheel_R"))]
    if len(wheels) < 2:
        return None, ["no wheel joints (wheel_L_*, wheel_R_*) to drive"]
    parts = {p["path"]: p for p in collect_parts(stage, spec["prim_path"])}
    info = []
    for j in wheels:
        p = parts.get(j["child_prim"])
        if p is None:
            continue
        ax = "XYZ".index(j["axis"])
        across = [p["size"][k] for k in range(3) if k != ax]
        info.append({"j": j, "ax": ax, "r": 0.5 * max(across), "c": np.array(p["centroid"]),
                     "side": "drive_left" if j["name"].startswith("wheel_L") else "drive_right"})
    if not info:
        return None, ["wheel parts not found"]
    rmax = max(w["r"] for w in info)
    notes = []
    axle = np.eye(3)[info[0]["ax"]]
    horiz = np.cross(np.eye(3)[up], axle)                 # rolling direction, either way
    small = [w for w in info if w["r"] < 0.85 * rmax]
    big = [w for w in info if w["r"] >= 0.85 * rmax]
    if small:
        # a wheelchair's small wheels are in front
        f = horiz if (np.mean([w["c"] for w in small], axis=0) - np.mean([w["c"] for w in big], axis=0)) @ horiz > 0 \
            else -horiz
    else:
        # wheels all one size: a seat's back is its highest part, at the rear
        from pivot_draft import _points

        pts = _points(stage, spec["prim_path"])          # vertices: the body is often one part
        lo_z, hi_z = pts[:, up].min(), pts[:, up].max()
        top = pts[pts[:, up] > lo_z + 0.7 * (hi_z - lo_z)]
        mid = (pts.min(0) + pts.max(0)) / 2
        back = top.mean(0) if len(top) else mid
        f = horiz if (mid - back) @ horiz > 0 else -horiz
        notes.append("wheels all one size: forward is away from the backrest (the highest parts)")
    if small:
        drive, casters = big, small
    elif len(info) >= 4:
        # four alike: the rear pair drive, the front pair are casters
        along = sorted(info, key=lambda w: w["c"] @ f)
        drive, casters = along[:len(info) // 2], along[len(info) // 2:]
        notes.append("the front pair are taken as casters, the rear pair drive")
    else:
        drive, casters = info, []
    left = [w for w in drive if w["side"] == "drive_left"]
    right = [w for w in drive if w["side"] == "drive_right"]
    if not left or not right:
        return None, notes + ["drive wheels on one side only"]
    dc = np.mean([w["c"] for w in drive], axis=0)
    roll = np.cross(np.eye(3)[up], f)                       # the turn that rolls a wheel along f
    track = float(abs((np.mean([w["c"] for w in left], axis=0) - np.mean([w["c"] for w in right], axis=0)) @ axle))
    b = {"type": "wheeled_base", "drive_left": [w["j"]["name"] for w in left],
         "drive_right": [w["j"]["name"] for w in right], "casters": [w["j"]["name"] for w in casters],
         "radius_m": round(float(np.mean([w["r"] for w in drive])), 4), "track_m": round(track, 4),
         "forward": [round(float(v), 3) for v in f],
         "forward_sign": {w["j"]["name"]: float(np.sign(roll @ np.eye(3)[w["ax"]]) or 1.0) for w in info}}
    # which side is left: seen from behind facing forward, left is up x f... wheel_L may be
    # named for either side, so take the side from geometry
    left_dir = np.cross(np.eye(3)[up], f)
    lc = np.mean([w["c"] for w in left], axis=0) @ left_dir
    rc = np.mean([w["c"] for w in right], axis=0) @ left_dir
    if lc < rc:
        b["drive_left"], b["drive_right"] = b["drive_right"], b["drive_left"]
    # casters that only spin get a swivel (add_mechanism.add_caster): fixed in
    # direction, four wheels scrub instead of turning (measured: 1 of 57 deg)
    frame = next((j["parent_prim"] for j in wheels), None)
    mechs = []
    for w in casters:
        mechs.append({"type": "caster", "frame": w["j"]["parent_prim"], "wheel": w["j"]["child_prim"],
                      "spin_joint": w["j"]["name"], "axle": w["j"]["axis"],
                      "centre": [round(float(v), 5) for v in w["c"]],
                      "forward": [round(float(v), 4) for v in f], "trail_m": round(0.4 * w["r"], 4),
                      "up": "XYZ"[up], "carrier_kg": 0.5})
    b["mechanisms"] = mechs
    notes.append(f"wheeled base: {len(drive)} drive wheels r={b['radius_m'] * 1000:.0f} mm, track "
                 f"{track * 1000:.0f} mm, {len(casters)} swivel casters")
    _ = frame
    return b, notes


# --- checks ----------------------------------------------------------------------

def check(entry: dict) -> dict:
    """For each behavior the asset should have (expected()), whether its draft
    has one and which roles it lacks. A gate is met by a mechanism or a rule
    on a joint (add_mechanism)."""
    spec = json.loads(entry.get("articulation_draft") or "{}")
    joints = {j["name"] for j in spec.get("joints", [])}
    have = {b["type"]: b for b in spec.get("behaviors", [])}
    out = {}
    for kind, why in expected(entry).items():
        if kind == "gate":
            ok = bool(spec.get("mechanisms")) or any(j.get("gate") for j in spec.get("joints", []))
            out[kind] = {"why": why, "present": ok, "missing": [] if ok else ["a latch or rule (add_mechanism)"],
                         "ok": ok}
            continue
        b = have.get(kind)
        missing = []
        for role, what in KIND_ROLES[kind].items():
            key = role.rstrip("?")
            val = (b or {}).get(key)
            names = val if isinstance(val, list) else [val] if val else []
            if (not names or any(n not in joints for n in names)) and not role.endswith("?"):
                missing.append(f"{key} ({what})")
        out[kind] = {"why": why, "present": b is not None, "missing": missing, "ok": b is not None and not missing}
    # what else the VLM saw it do by hand (a backrest that reclines, an armrest
    # that flips up, a battery that comes off): no law to check, but a reviewer
    # should see whether the draft lets it happen
    other = [f"{f.get('does')} ({f.get('moving_part')}, {f.get('kind')})"
             for f in (entry.get("vlm") or {}).get("functions") or [] if f.get("kind") not in KIND_ROLES]
    if other:
        out["_also_seen"] = {"functions": other, "ok": True, "missing": []}
    return out


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "check":
        print(__doc__)
        return 2
    for a in sys.argv[2:]:
        e = json.loads((QUEUE_DIR / f"{a}.json").read_text())
        r = check(e)
        print(a, json.dumps(r) if r else "(no behavior conventions for its class)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
