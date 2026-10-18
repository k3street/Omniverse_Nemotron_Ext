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
import re
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


INTERNAL = re.compile(r"\b(internal|hidden|inside the (body|housing|case))\b|no (external|visible) (motion|moving)", re.I)


def expected(entry: dict) -> dict:
    """The behaviors the asset should have: its class's (priors), and what the
    classifying VLM saw it do (its functions). kind -> where that came from."""
    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    prior = json.loads(PRIORS_PATH.read_text())["classes"].get(cls or "", {})
    out = {k: f"class {cls}" for k in prior.get("behaviors", []) if k in KIND_ROLES}
    for f in (entry.get("vlm") or {}).get("functions") or []:
        # a coffee maker's pump is a motor nothing outside shows: no part to drive
        if INTERNAL.search(f.get("moving_part") or ""):
            continue
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


def gasket(angle_rad: float, force_n: float, handle_radius_m: float, edge_radius_m: float, decay_m: float = 0.003) -> float:
    """A door's magnetic gasket as a closing torque on its hinge (N m): the
    stated opening force at the handle while the door is shut, falling as
    exp(-gap / decay) with the gap the latch edge has opened (edge radius x
    angle). A refrigerator's gasket lets go within a few millimetres; no
    manufacturer states the force - magnet suppliers give 22-44 N at the
    handle, and 16 CFR 1750.5 caps opening from inside at 66.7 N."""
    gap = edge_radius_m * max(float(angle_rad), 0.0)
    return float(force_n) * float(handle_radius_m) * math.exp(-gap / float(decay_m))


def latch_kick(angle_rad: float, button_m: float, state: dict, trip_m: float, latch_torque_n_m: float,
               kick_torque_n_m: float, kick_end_rad: float, catch_rad: float = math.radians(0.5),
               relatch_rad: float = math.radians(1.0), arm_rad: float = math.radians(3.0)) -> float:
    """A button-released door (a microwave's): the torque on its hinge (N m,
    positive opens) from its latch and its kick spring, and the latch's state
    updated in `state` ({'latched': bool, 'armed': bool}).

    Latched, the hooks hold it shut: a closing torque that takes up the
    catch's play within catch_rad and holds with latch_torque. The button's
    travel past trip_m lifts the hooks: unlatched, the kick spring pushes the
    door open, its torque falling to nothing at kick_end_rad. Once the door
    has opened past arm_rad, coming back under relatch_rad latches it again
    (not before: the finger still on the button must not re-hook it)."""
    a = float(angle_rad)
    state.setdefault("latched", True)
    state.setdefault("armed", False)
    if state.get("latched", True) and button_m >= trip_m:
        state["latched"], state["armed"] = False, False
    if not state.get("latched", True):
        if a > arm_rad:
            state["armed"] = True
        if state.get("armed") and a < relatch_rad and button_m < 0.5 * trip_m:
            state["latched"] = True
    if state.get("latched", True):
        return -latch_torque_n_m * min(1.0, max(a, 0.0) / catch_rad)
    return kick_torque_n_m * max(0.0, 1.0 - max(a, 0.0) / kick_end_rad)


def hinge_friction(omega_rad_s: float, torque_n_m: float, slip_rad_s: float = math.radians(0.5)) -> float:
    """A hinge's friction (N m, positive opens; against the door's turning):
    Coulomb friction of `torque_n_m` once the door turns faster than
    slip_rad_s, proportionally less below - a smooth stick that holds a door
    against any smaller torque, letting it creep at slip x atanh(load/torque).
    A microwave's door stays where its kick leaves it; with damping alone the
    0.08 deg tilt of its settled hinge axis turned gravity into 0.005 N m and
    the door crept from 13.5 to 90 deg in 28 s (2026-10-16)."""
    return -float(torque_n_m) * math.tanh(float(omega_rad_s) / float(slip_rad_s))


def screw_resistance(state: dict, drive_n_m: float, tighten_n_m: float, strip_n_m: float, engage_m: float = 0.006,
                     tighten_rad: float = math.pi / 2) -> float:
    """The wood's torque against a screw being turned in (N m), along the
    insertion curve measured for wood screws (Kumpenza et al. 2020: 4.8 mm
    screws in birch plywood with a 3.5 mm pilot): rising to the driving torque
    as the thread engages over engage_m, flat while the thread forms (~1 N m),
    then - the head on the surface - rising to the tightening torque over
    tighten_rad and on to the stripping torque; stripped, the sheared thread
    spins at a third of the driving torque. `state` as screw_drive keeps it."""
    if state.get("stripped"):
        return drive_n_m / 3.0
    if not state.get("seated"):
        return drive_n_m * min(1.0, max(state.get("engaged_m", 0.0), 0.0) / engage_m)
    past = max(state.get("past_seat_rad", 0.0), 0.0)
    if past <= tighten_rad:
        return drive_n_m + (tighten_n_m - drive_n_m) * past / tighten_rad
    return tighten_n_m + (strip_n_m - tighten_n_m) * (past - tighten_rad) / tighten_rad


def screw_drive(state: dict, dphi_rad: float, engaged: bool, pitch_m: float, seat_depth_m: float,
                drive_n_m: float, tighten_n_m: float, strip_n_m: float, start_depth_m: float = 0.0,
                engage_m: float = 0.006, tighten_rad: float = math.pi / 2) -> float:
    """One physics step of a screw driven by a bit: while the bit sits in the
    screw's head (`engaged`), the screw turns with the chuck (dphi_rad, positive
    drives it in) and advances pitch_m a turn until its head seats at
    seat_depth_m; past seating it only tightens, and past the stripping torque
    the thread shears. Off the bit the screw stays where it is. Updates `state`
    ({'theta_rad', 'depth_m', 'engaged_m', 'seated', 'past_seat_rad',
    'stripped'}) and returns the wood's torque against the turning (N m, a
    magnitude: the caller opposes the chuck's motion with it)."""
    state.setdefault("theta_rad", 0.0)
    state.setdefault("depth_m", float(start_depth_m))
    state.setdefault("engaged_m", float(start_depth_m))
    state.setdefault("seated", False)
    state.setdefault("past_seat_rad", 0.0)
    state.setdefault("stripped", False)
    if not engaged or dphi_rad == 0.0:
        return screw_resistance(state, drive_n_m, tighten_n_m, strip_n_m, engage_m, tighten_rad) if engaged else 0.0
    state["theta_rad"] += dphi_rad
    if state["stripped"]:
        pass                                                        # spins in its sheared thread
    elif not state["seated"]:
        d = state["depth_m"] + pitch_m * dphi_rad / (2 * math.pi)
        if d >= seat_depth_m:
            state["past_seat_rad"] = (d - seat_depth_m) / pitch_m * 2 * math.pi
            d, state["seated"] = seat_depth_m, True
        state["depth_m"] = max(d, 0.0)
        state["engaged_m"] = state["depth_m"]
    else:
        state["past_seat_rad"] += dphi_rad
        if state["past_seat_rad"] < 0.0:                            # backed out of its seat
            state["seated"] = False
            state["depth_m"] = seat_depth_m + pitch_m * state["past_seat_rad"] / (2 * math.pi)
            state["past_seat_rad"] = 0.0
    t = screw_resistance(state, drive_n_m, tighten_n_m, strip_n_m, engage_m, tighten_rad)
    if state["seated"] and not state["stripped"] and t >= strip_n_m:
        state["stripped"] = True
    return t


def cap_thread(state: dict, u_rad: float, torque_n_m: float = 0.0, *, turns: float, retention: float = 0.5,
               run_n_m: float = 0.02, seat_rad: float = math.radians(5.0), applied_n_m: float = 0.0,
               free_pose: dict | None = None, pitch_m: float = 0.0, align_m: float = 0.0015,
               align_rad: float = math.radians(5.0), catch_m: float = 0.002, seat_tol_rad: float = math.radians(1.0),
               past_end_rad: float = math.radians(2.0)) -> dict:
    """One physics step of a screw cap on its bottle's thread (a GPI 400
    finish: one turn). On the thread the asset's joint pair holds the cap to
    its helix (a revolute and a prismatic coupled a pitch a turn); this law
    says what the pair does. `u_rad` is the cap's turn off its seat (positive
    opens), `torque_n_m` the torque the hand puts on it (positive opens).

    Seated (u under seat_rad, not yet broken loose) the closure holds with its
    removal torque: `retention` x the torque it was last applied with - 40-60 %
    of the application torque 24 h after capping (Oberk, "How to make sure
    your cap is tight enough"; a 38 mm closure is applied at 17-26 in-lb and
    removed at 7-16): the caller locks its turn ('locked'). An opening torque
    from the hand that reaches the hold - or a turn past seat_rad - breaks it
    loose, and it turns against the thread's running friction. Turned away from
    its seat (past seat_rad) and back down onto it (within seat_tol_rad: the
    joint's stop holds it there), it is seated again, and the closing torque the
    hand holds there is the new application torque - not while it is still
    leaving the seat it just broke loose from (it re-locked 2 ms after breaking
    loose, 0.6 deg up, on the rig, 2026-10-17). Turned past_end_rad past
    `turns` it leaves the thread: state['on'] goes False and the caller releases the
    pair (the cap free). A free cap lifted clear of the neck (higher than
    catch_m over the thread's start) and set back on it - its axis within
    align_m and align_rad of the bottle's, its height within catch_m above
    the thread's start (free_pose {'off_axis_m', 'tilt_rad', 'height_m' above
    its seat}) - catches the thread at its top; one only leaving the thread
    does not.

    Updates `state` ({'on', 'broken', 'away', 'applied_n_m', 'closing_n_m', 'clear'}) and
    returns {'on', 'locked' (seated and held: the turn locked), 'friction_n_m'
    (the turning friction now), 'event' ('broke_loose' | 'off_thread' |
    'on_thread' | 'seated' | None)}."""
    state.setdefault("on", True)
    state.setdefault("broken", False)
    state.setdefault("applied_n_m", float(applied_n_m))
    state.setdefault("closing_n_m", 0.0)
    state.setdefault("away", False)
    event = None
    top = 2 * math.pi * float(turns)
    if not state["on"]:
        fp = free_pose or {}
        over = fp.get("height_m", -1.0) - pitch_m * turns
        if over > catch_m:
            state["clear"] = True
        if (state.get("clear") and fp.get("off_axis_m", 1.0) <= align_m and fp.get("tilt_rad", 1.0) <= align_rad
                and 0.0 <= over <= catch_m):
            state.update(on=True, broken=True, away=True, closing_n_m=0.0, clear=False)
            # caught: the caller counts its turn from the thread's end from here (the turn it has now is a free cap's -
            # read in the same step, a turn already past the end let it go again at once, 2026-10-17)
            return {"on": True, "locked": False, "friction_n_m": float(run_n_m), "event": "on_thread"}
        else:
            return {"on": False, "locked": False, "friction_n_m": 0.0, "event": None}
    u = float(u_rad)
    if u >= top + past_end_rad:
        state.update(on=False, clear=False)
        return {"on": False, "locked": False, "friction_n_m": 0.0, "event": "off_thread"}
    if state["broken"] and u > seat_rad:
        state["away"] = True
    if u <= seat_tol_rad:
        state["closing_n_m"] = max(state["closing_n_m"], -float(torque_n_m))
    if state["broken"]:
        if u <= seat_tol_rad and state["away"]:
            state.update(broken=False, away=False, applied_n_m=state["closing_n_m"])
            event = "seated"
    elif u > seat_rad or float(torque_n_m) >= retention * state["applied_n_m"] > 0.0:
        state.update(broken=True, away=u > seat_rad, closing_n_m=0.0)
        event = "broke_loose"
    if not state["broken"] and u <= seat_tol_rad:
        state["applied_n_m"] = max(state["applied_n_m"], state["closing_n_m"])
    held = 0.0 if state["broken"] else retention * state["applied_n_m"]
    return {"on": True, "locked": not state["broken"], "friction_n_m": max(held, run_n_m), "event": event}


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

def draft_wheeled_base(stage, spec: dict, up: int = 2, pushed: bool = False,
                       forks: list | None = None) -> tuple[dict | None, list[str]]:
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
    def round_across(p, ax):
        a, b = (p["size"][k] for k in range(3) if k != ax)
        return min(a, b) >= 0.85 * max(a, b) > 0

    info = []
    drafted = {j["child_prim"] for j in wheels}
    for j in wheels:
        p = parts.get(j["child_prim"])
        if p is None:
            continue
        ax = "XYZ".index(j["axis"])
        fork = None
        if not round_across(p, ax):
            # not round about its axle: a caster's fork (taller than wide), the
            # wheel is the round part beside it (wheelchair_01's fork spun about
            # the axle with its wheel welded on)
            near = [q for q in parts.values() if q["path"] not in drafted and round_across(q, ax)
                    and np.linalg.norm(np.array(q["centroid"]) - p["centroid"]) < 1.5 * max(p["size"])]
            if near:
                fork, p = p, min(near, key=lambda q: np.linalg.norm(np.array(q["centroid"]) - p["centroid"]))
        if fork is None and forks:
            # the survey named the forks: the one over this wheel swivels it
            near = [parts[f] for f in forks if f in parts
                    and np.linalg.norm(np.array(parts[f]["centroid"]) - p["centroid"]) < 1.5 * max(p["size"])]
            if near:
                fork = min(near, key=lambda q: np.linalg.norm(np.array(q["centroid"]) - p["centroid"]))
        # a wheel modelled in pieces (a tyre and a hub on one axle) can be hung
        # on its own other piece: that piece turns with it, and the wheel hangs
        # on what the piece hung on (the engine crane's casters closed a loop)
        for _ in range(4):
            par = parts.get(j["parent_prim"])
            if par is None or par["path"] == p["path"] or not round_across(par, ax):
                break
            off = np.array(par["centroid"]) - np.array(p["centroid"])
            axis_v = np.eye(3)[ax]
            if np.linalg.norm(off - (off @ axis_v) * axis_v) > 0.2 * max(p["size"]):
                break
            up_j = next((jj for jj in spec["joints"] if jj["child_prim"] == par["path"]), None)
            if up_j is None:
                break
            j["parent_prim"] = up_j["parent_prim"]
            up_j["parent_prim"] = p["path"]
            up_j["joint_type"] = "fixed"
            for k2 in ("axis", "lower_limit", "upper_limit", "stiffness", "damping", "max_force"):
                up_j.pop(k2, None)
        across = [p["size"][k] for k in range(3) if k != ax]
        info.append({"j": j, "ax": ax, "r": 0.5 * max(across), "c": np.array(p["centroid"]), "part": p,
                     "fork": fork, "side": "drive_left" if j["name"].startswith("wheel_L") else "drive_right"})
    # what turns with a wheel: round parts on its axle (a tyre, a push rim)
    # left fixed to the frame (wheelchair_01's left push rim stood still)
    for w in info:
        axis = np.eye(3)[w["ax"]]
        for jj in spec["joints"]:
            q = parts.get(jj["child_prim"])
            if jj["joint_type"] != "fixed" or q is None or q is w["part"] or jj["child_prim"] in drafted:
                continue
            off = np.array(q["centroid"]) - w["c"]
            if (np.linalg.norm(off - (off @ axis) * axis) < 0.1 * w["r"] and round_across(q, w["ax"])
                    and 0.5 * max(q["size"]) >= 0.5 * w["r"] and abs(off @ axis) < 0.6 * w["r"]
                    and jj["parent_prim"] != w["part"]["path"]):
                jj["parent_prim"] = w["part"]["path"]
                jj["name"] = f"{w['j']['name']}_rides_{len(spec['joints'])}"
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
    if pushed:
        # nothing drives it (an engine crane, a cart, an IV stand on casters):
        # every wheel rolls and swivels free, and a hand pushes the frame
        drive, casters = [], info
        notes.append("pushed: every wheel a free caster, no drive")
    elif small:
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
    if pushed:
        b = {"type": "wheeled_base", "pushed": True, "drive_left": [], "drive_right": [],
             "casters": [w["j"]["name"] for w in casters],
             "radius_m": round(float(np.mean([w["r"] for w in info])), 4), "track_m": 0.0,
             "forward": [round(float(v), 3) for v in f], "forward_sign": {}}
        b["mechanisms"] = _caster_mechs(casters, f, up, spec["joints"])
        notes.append(f"wheeled base: pushed on {len(casters)} swivel casters")
        return b, notes
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
    b["mechanisms"] = _caster_mechs(casters, f, up, spec["joints"])
    notes.append(f"wheeled base: {len(drive)} drive wheels r={b['radius_m'] * 1000:.0f} mm, track "
                 f"{track * 1000:.0f} mm, {len(casters)} swivel casters")
    return b, notes


def _caster_mechs(casters: list, f, up: int, spec_joints: list = ()) -> list:
    import numpy as np

    mechs = []
    for w in casters:
        frame = w["j"]["parent_prim"]
        own = {w["part"]["path"]} | ({w["fork"]["path"]} if w["fork"] is not None else set())
        for _ in range(4):
            if frame not in own:
                break
            # the wheel hangs on its fork: the frame is what the fork hangs on
            frame = next((j["parent_prim"] for j in spec_joints if j["child_prim"] == frame), frame)
        m = {"type": "caster", "frame": frame, "wheel": w["part"]["path"],
             "spin_joint": w["j"]["name"], "axle": w["j"]["axis"],
             "centre": [round(float(v), 5) for v in w["c"]],
             "forward": [round(float(v), 4) for v in f], "trail_m": round(0.4 * w["r"], 4),
             "up": "XYZ"[up], "carrier_kg": 0.5}
        if w["fork"] is not None:
            # the fork is the swivelling body: it turns about its stem (the top
            # of the fork, over the wheel's leading side), the wheel spins in it
            fk = w["fork"]
            stem = np.array(fk["centroid"], float)
            stem[up] = fk["max"][up]
            m.update({"fork": fk["path"], "pivot": [round(float(v), 5) for v in stem]})
        mechs.append(m)
    return mechs


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
        roles = KIND_ROLES[kind]
        if kind == "wheeled_base" and (b or {}).get("pushed"):
            # pushed on casters: no drive, the casters are what it needs
            roles = {"casters": "the free swivel casters it rolls on"}
        moved = joints | {m.get("spin_joint") for m in spec.get("mechanisms", []) if m.get("spin_joint")}
        for role, what in roles.items():
            key = role.rstrip("?")
            val = (b or {}).get(key)
            names = val if isinstance(val, list) else [val] if val else []
            if (not names or any(n not in moved for n in names)) and not role.endswith("?"):
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
