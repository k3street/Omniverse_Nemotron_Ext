#!/usr/bin/env python3
"""Motion critic: does an articulated asset move the way the real object does?

animate_asset checks that each joint REACHES its travel in PhysX; it cannot
tell whether that travel is the right motion. A nail clipper's lever drafted
as a scissor pivot swings flat across the body, reaches its 30 degrees, and
passes. This critic looks at the animation the way a reviewer would: for
each moving joint (a few of each kind on a keyboard's hundred keys), the
frame at rest beside the frame where that joint is furthest from rest, with
the object's name, class and the parts the classifying VLM said move. The
judge answers, per joint, whether that is how that part of that object
moves - the right part, about the right axis, the right way, nothing
passing through or coming off - and if not, what the motion should be.

Fail-closed like visual_qa: a joint the judge could not see, or any judge
error, is not a pass. Evidence goes to the queue entry under "motion_qa".

    python scripts/motion_critic.py <asset_id> [...] [--judge claude|cosmos|gemma]

Needs workspace/asset_animations/<id>/ (frames/, joints.csv, summary.json)
from scripts/animate_asset.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ANIM_DIR = REPO / "workspace" / "asset_animations"
QUEUE_DIR = REPO / "workspace" / "review_queue"
PER_KIND = 3          # joints judged per kind (button, rotor, pivot ...) when there are many


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "part_visible": {"type": "boolean", "description": "can you see a part move between the panels? "
                                                              "false when nothing visibly changes"},
            "moving_part": {"type": "string", "description": "which part of the object moves between the frames"},
            "motion_seen": {"type": "string", "description": "how it moves: about/along what, which way"},
            "motion_ok": {"type": "boolean", "description": "is that how this part of this object really moves?"},
            "problem": {"type": "string", "description": "what is wrong, or empty"},
            "expected_motion": {"type": "string", "description": "how this part should move on the real object"},
            "confidence": {"type": "number", "description": "0..1"},
            "correction": {
                "type": "object",
                "description": "when motion_ok is false: the ONE change to this joint that would make the motion "
                               "right, in terms a drafter can apply. kind 'none' when the motion is right or the "
                               "fault is not this joint's (wrong part moving, model broken).",
                "properties": {
                    "kind": {"type": "string", "enum": ["none", "axis", "pivot", "wrong_part", "direction", "range"]},
                    "axis": {"type": "string",
                             "enum": ["none", "horizontal", "vertical", "along_part_length", "across_part_width",
                                      "through_part_face", "along_pin"],
                             "description": "what the part should turn about: a world direction, or one of the "
                                            "part's own (its length, its width, through its flat face), or the "
                                            "pin/rivet that joins it"},
                    "pivot": {"type": "string",
                              "enum": ["none", "other_end", "part_centre", "where_it_meets_parent", "on_pin"],
                              "description": "where the pivot should sit"},
                    "note": {"type": "string", "description": "one line for a person"},
                },
                "required": ["kind", "axis", "pivot", "note"],
                "additionalProperties": False,
            },
        },
        "required": ["part_visible", "moving_part", "motion_seen", "motion_ok", "problem", "expected_motion",
                     "confidence", "correction"],
        "additionalProperties": False,
    }


def _prompt(entry: dict, joint: str, info: dict, value: float) -> str:
    vlm = entry.get("vlm") or {}
    unit = "deg" if info.get("type") == "revolute" else "m"
    return (
        f"These panels are from a physics simulation of a 3D asset: {vlm.get('object_name') or entry['asset_id']}"
        f" (class {entry.get('class_hint') or entry.get('report', {}).get('matched_class')}). "
        f"Parts a classifier expected to move: {', '.join(vlm.get('visible_moving_parts') or []) or 'none listed'}.\n"
        f"Left to right: its {info.get('type')} joint '{joint}' at rest, a third of the way, two thirds, and "
        f"driven to {value:.3g} {unit} (other joints at rest). Where the panels are close-ups, the camera is framed "
        "on the moving part. First say whether you can see a part move at all (part_visible); if you cannot, "
        "do not guess the rest.\n"
        "Judge the MOTION, not the model's looks: is the part that moved the part that moves on the real object, "
        "about (or along) the right axis, the right way, through a plausible range - without passing through "
        "another part, detaching, or the whole object moving instead? Parts joined at the pivot must stay joined "
        "there: a jaw or head pulling away from its mate means the pivot is in the wrong place. "
        "If you cannot see a difference, set part_visible false and "
        "motion_ok false. A red ring marks the joint's pivot where one is drawn: the part turns about it.\n"
        "When the motion is wrong, fill `correction` with the one change that would put it right - which axis "
        "the part should turn about (in the part's own terms, or the pin that joins it) and where the pivot "
        "belongs - so the joint can be drafted again; kind 'wrong_part' when a different part should be the one "
        "moving, 'none' when no change to this joint would help.\n"
        + measured_motion(entry, joint) + _reference(entry, joint)
    )


def _reference(entry: dict, joint: str) -> str:
    """The named product's manual says how its parts move (product_lookup):
    judge against that, not a guess about what such an object does."""
    m = entry.get("product_mechanics")
    if not m:
        return ""
    ref = None
    try:
        spec = json.loads(entry.get("articulation_draft") or "{}")
        child = next(j["child_prim"] for j in spec.get("joints", []) if j["name"] == joint)
        part = next((p for p in (entry.get("part_survey") or {}).get("parts", []) if p["path"] == child), None)
        ref = (part or {}).get("reference_part")
    except (StopIteration, ValueError, KeyError):
        pass
    lines = "\n".join(f"- {q['part']}: {q['motion']}; {q['how']} Pivot: {q['pivot']}."
                      + (f" Range {q['range']:g}." if q.get("range") is not None else "")
                      for q in m["moving_parts"])
    return (f"\nThis is a known product, {m['product_name']}. Its manual says its parts move like this:\n{lines}\n"
            + (f"This joint was drafted as the reference's '{ref}': it must move as that line says. "
               if ref else "This joint matches none of these: it should not move unless you can see it plainly does. ")
            + "A motion the manual contradicts is motion_ok false.")


def measured_motion(entry: dict, joint: str) -> str:
    """What the joint does, in the object's own terms, from its authored axis:
    one oblique view cannot tell a lever lifting from one swinging flat (a
    nail clipper's drafted as a scissor pivot fooled the judge)."""
    try:
        spec = json.loads(entry.get("articulation_draft") or "{}")
        j = next(x for x in spec.get("joints", []) if x["name"] == joint)
        dims = None
    except (StopIteration, ValueError):
        return ""
    axis = "XYZ".index(j.get("axis", "Z")) if j.get("axis") in ("X", "Y", "Z") else None
    if axis is None:
        return ""
    lo = None
    if dims is None:
        from pxr import Usd, UsdGeom

        st = Usd.Stage.Open(entry["file"])
        r = UsdGeom.BBoxCache(0, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]).ComputeWorldBound(
            st.GetPseudoRoot()).ComputeAlignedRange()
        dims, lo = list(r.GetSize()), list(r.GetMin())
        # a file of several units (a pair of K-Slims side by side): the
        # directions of the unit the joint is on, not of the row - "across the
        # object's length" sent a drip tray that slid out the front sideways
        if j.get("anchor"):
            cache = UsdGeom.BBoxCache(0, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
            whole = dims[0] * dims[1] * dims[2]
            for part in (entry.get("part_survey") or {}).get("parts", []):
                for c in part.get("copies") or []:
                    prim = st.GetPrimAtPath(c)
                    if not prim:
                        continue
                    u = cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    us, um = list(u.GetSize()), list(u.GetMin())
                    inside = all(um[k] - 0.01 <= j["anchor"][k] <= um[k] + us[k] + 0.01 for k in range(3))
                    if inside and us[0] * us[1] * us[2] >= 0.2 * whole and us[0] * us[1] * us[2] < 0.9 * whole:
                        dims, lo = us, um
                        break
                else:
                    continue
                break
    long_axis = max((0, 1), key=lambda k: dims[k])         # the length, lying on the ground
    where = ""
    if j.get("anchor") and lo is not None:
        f = (j["anchor"][long_axis] - lo[long_axis]) / max(dims[long_axis], 1e-9)
        f = min(max(f, 0.0), 1.0)
        where = (f" The pivot is {100 * min(f, 1 - f):.0f}% of the object's length from one END"
                 if min(f, 1 - f) < 0.2 else f" The pivot is {100 * f:.0f}% along the object's length, in its MIDDLE part")
        where += ("; check it is where this part really hinges (a rivet, a pin, a hinge), not at the wrong end."
                  if j["joint_type"] == "revolute" else ".")
    if j["joint_type"] == "revolute":
        what = {2: "a VERTICAL axis (perpendicular to the ground): the part swings SIDEWAYS, flat in the "
                   "ground plane - it does not lift or tilt",
                long_axis: "the object's LENGTH: the part rolls/twists about it",
                }.get(axis, "a horizontal axis ACROSS the object's length: the part's far end lifts up or dips down")
        return f"Measured from the joint (not from the image): it turns about {what}.{where}"
    what = {2: "VERTICALLY (up/down)", long_axis: "ALONG the object's length"}.get(axis, "SIDEWAYS, across the length")
    return f"Measured from the joint (not from the image): the part slides {what}.{where}"


def overtravel(entry: dict, joint: str) -> str | None:
    """A measured check the judge cannot be fooled on: does the joint's limit
    turn the child past where it first runs into its parent? PhysX never
    collides two links joined by a joint, so a limit past contact puts them
    through each other (scissors' rings, a peg's jaws). Returns the problem,
    or None."""
    try:
        spec = json.loads(entry.get("articulation_draft") or "{}")
        j = next(x for x in spec.get("joints", []) if x["name"] == joint)
    except (StopIteration, ValueError):
        return None
    if j.get("joint_type") != "revolute" or not j.get("anchor") \
            or j.get("lower_limit") is None or j.get("upper_limit") is None:
        return None                                # a spindle or a caster: no stops to run past
    from pxr import Usd

    from swing_contact import swing_until_contact

    def side(root):  # a link and the parts fixed to it as its own (a grip on its arm)
        return [root] + [x["child_prim"] for x in spec["joints"] if x["joint_type"] == "fixed"
                         and x["parent_prim"] == root and x["name"].startswith(("half_part", "arm_part"))]

    stage = Usd.Stage.Open(entry["file"])
    span = float(entry.get("report", {}).get("max_dim_m") or 0.2)
    axis = "XYZ".index(j["axis"])
    out = []
    for d, lim in ((1.0, j["upper_limit"]), (-1.0, j["lower_limit"])):
        if d * lim <= 0.5:
            continue
        meet = swing_until_contact(stage, side(j["parent_prim"]), side(j["child_prim"]), j["anchor"], axis, d, span,
                                   max_deg=abs(lim) + 5)
        if meet is not None and abs(lim) > meet + 2.0:
            out.append(f"the limit {lim:g} deg turns it {abs(lim) - meet:.1f} deg past where it meets its mate "
                       f"({d * meet:g} deg): the parts pass through each other")
    return "; ".join(out) or None


def _reached(v: dict) -> bool:
    if v.get("unlimited"):            # no stops: it turned a sweep each way
        return v["measured_range"][0] <= 0.9 * v["limits"][0] and v["measured_range"][1] >= 0.9 * v["limits"][1]
    far = v["limits"][1] if abs(v["limits"][1]) >= abs(v["limits"][0]) else v["limits"][0]
    got = v["measured_range"][1] if far > 0 else v["measured_range"][0]
    return abs(got - far) <= 0.1 * abs(far) + 1e-4


def _measured_only(entry: dict, summary: dict) -> dict:
    """Joints a picture cannot judge, with their verdict from measurement:
    a slide too short to see (under 6% of the object: a trigger's 10 mm, a switch's 3 mm on a 30 cm
    drill) or a turn under a few degrees is judged by whether PhysX drove it
    through its travel; a wheel or caster of a base that drives off under the
    camera (the whole asset moves) by its behavior's scenarios."""
    out = {}
    size = float((entry.get("report") or {}).get("max_dim_m") or 0.0)
    rolls = {}
    spec = json.loads(entry.get("articulation_draft") or "{}")
    beh = summary.get("behaviors") or {}
    for b in spec.get("behaviors", []):
        if b.get("type") == "wheeled_base":
            runs = [r for k, v in beh.items() if k.startswith("wheeled_base") for r in v.values()]
            ok = bool(runs) and all(r.get("ok") for r in runs)
            why = "; ".join(f"{k} {'ok' if r.get('ok') else 'FAILED'}" for v in beh.values() for k, r in v.items()
                            if "moved_m" in r or "yaw_deg" in r)
            for j in b.get("drive_left", []) + b.get("drive_right", []) + b.get("casters", []):
                for name in (j, f"caster_swivel_{j}"):
                    rolls[name] = {"motion_ok": ok, "measured": True,
                                   "problem": "" if ok else f"the wheeled base did not drive as its law says: {why}"}
    # a round part turning about its own axis of symmetry (a dial, a knob, a
    # wheel) looks the same at every angle - a picture cannot judge it, and the
    # axis is right by construction: judged by measurement
    round_spins = set()
    intended = {j["name"]: float(j["_intended_deg"]) for j in spec.get("joints", []) if j.get("_intended_deg")}
    try:
        from pxr import Usd, UsdGeom
        st = Usd.Stage.Open(entry["file"])
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        for j in spec.get("joints", []):
            if j.get("joint_type") != "revolute" or j.get("lower_limit") is not None or j.get("axis") not in "XYZ":
                continue
            prim = st.GetPrimAtPath(j["child_prim"])
            if not prim:
                continue
            s = cache.ComputeWorldBound(prim).ComputeAlignedRange().GetSize()
            k = "XYZ".index(j["axis"])
            a, b = s[(k + 1) % 3], s[(k + 2) % 3]
            if max(a, b) > 0 and abs(a - b) <= 0.15 * max(a, b):
                round_spins.add(j["name"])
    except Exception:  # noqa: BLE001 - without pxr, the picture judges
        pass
    for name, v in summary.get("joints", {}).items():
        if v.get("follower"):
            continue
        if name in rolls:
            out[name] = rolls[name]
            continue
        if name in round_spins:
            ok = _reached(v)
            out[name] = {"motion_ok": ok, "measured": True,
                         "problem": "" if ok else f"driven {v['measured_range']}: it did not turn a sweep each way"}
            continue
        travel = max(abs(v["limits"][0]), abs(v["limits"][1]))
        small = (v.get("type") == "prismatic" and size and travel < 0.06 * size) or \
                (v.get("type") == "revolute" and travel < 3.0)
        meant = intended.get(name)
        if v.get("type") == "revolute" and meant and travel < 0.5 * meant:
            # stopped far short of what it is for: a lid limited to 0.5 deg of
            # its 90 reached its 0.5 and read as a "small turn" that passed
            out[name] = {"motion_ok": False, "measured": True,
                         "problem": f"limited to {travel:g} deg of the {meant:g} it should open: "
                                    "its pivot or its parent is wrong"}
            continue
        if small:
            ok = _reached(v)
            out[name] = {"motion_ok": ok, "measured": True,
                         "problem": "" if ok else f"driven {v['measured_range']} of {v['limits']}: short of its travel"}
    return out


def _frames(asset_id: str):
    d = ANIM_DIR / asset_id
    summary = json.loads((d / "summary.json").read_text())
    rows = list(csv.DictReader(open(d / "joints.csv")))
    frames = sorted((d / "frames").glob("f*.png"))
    return summary, rows, frames


def _pick_joints(summary: dict) -> list[str]:
    """Every moving joint, or PER_KIND of each kind when there are many."""
    moving = [k for k, v in summary["joints"].items() if not v.get("follower")]
    if len(moving) <= 8:
        return moving
    kinds: dict[str, list[str]] = {}
    for k in moving:
        kinds.setdefault(k.rstrip("0123456789_"), []).append(k)
    out = []
    for names in kinds.values():
        step = max(1, len(names) // PER_KIND)
        out += names[::step][:PER_KIND]
    return out


def project(p, cam: dict, frame: int):
    """A world point to pixel (x, y) through animate_asset's logged camera."""
    import math

    import numpy as np

    eye, target = (np.array(v, float) for v in cam["frames"][frame])
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, cam.get("up", [0, 0, 1]))
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    d = np.array(p, float) - eye
    z = d @ f
    if z <= 0:
        return None
    t = math.tan(math.radians(cam["hfov_deg"]) / 2)
    w, h = cam["width"], cam["height"]
    return (w / 2 * (1 + (d @ r) / (z * t)), h / 2 * (1 - (d @ u) / (z * t * h / w)))


def _mark(img, xy, scale):
    from PIL import ImageDraw

    if xy is None:
        return img
    x, y = xy[0] * scale[0], xy[1] * scale[1]
    g = ImageDraw.Draw(img)
    for rad, col in ((14, (255, 255, 255)), (12, (230, 20, 20)), (11, (230, 20, 20))):
        g.ellipse((x - rad, y - rad, x + rad, y + rad), outline=col, width=3)
    g.text((x + 16, y - 8), "pivot", fill=(230, 20, 20))
    return img


def _side_by_side(a: Path, b: Path, out: Path, marks=(None, None), cam=None) -> Path:
    from PIL import Image

    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if cam:
        sc = (ia.width / cam["width"], ia.height / cam["height"])
        ia, ib = _mark(ia, marks[0], sc), _mark(ib, marks[1], sc)
    img = Image.new("RGB", (ia.width + ib.width, max(ia.height, ib.height)), "white")
    img.paste(ia, (0, 0))
    img.paste(ib, (ia.width, 0))
    img.save(out)
    return out


def _strip(paths: list, out: Path, marks: list | None = None, cam=None, width: int = 640) -> Path:
    """Panels side by side, each scaled to `width`, the pivot ring on each."""
    from PIL import Image

    tiles = []
    for k, pth in enumerate(paths):
        im = Image.open(pth).convert("RGB")
        if cam and marks and marks[k]:
            im = _mark(im, marks[k], (im.width / cam["width"], im.height / cam["height"]))
        h = round(im.height * width / im.width)
        tiles.append(im.resize((width, h)))
    img = Image.new("RGB", (width * len(tiles), max(t.height for t in tiles)), "white")
    for k, t in enumerate(tiles):
        img.paste(t, (k * width, 0))
    img.save(out)
    return out


def _focus_frames(summary: dict, name: str, n: int) -> list[int]:
    """The frames the camera spent framed on this joint (animate_asset logs it)."""
    f = (summary.get("camera") or {}).get("focus") or []
    return [i for i in range(min(len(f), n)) if f[i] == name]


def completeness(entry: dict) -> dict:
    """Whether every part that moves on the real object moves in the draft.
    A BMX passed with a seat post as its only joint: each joint was judged,
    nobody asked what was missing. The survey (with the manual, where there is
    one) says what moves; a part counts as moving when it is a joint's child,
    a mechanism's part, or fixed onto one of those."""
    survey = entry.get("part_survey") or {}
    spec = json.loads(entry.get("articulation_draft") or "{}")
    if not survey.get("parts") or not spec:
        return {"ok": True, "missing": [], "not_separable": [], "checked": False}
    moving = {j["child_prim"] for j in spec.get("joints", []) if j.get("joint_type") not in (None, "fixed")}
    for m in spec.get("mechanisms", []):
        moving |= {m.get(k) for k in ("part", "wheel", "fork") if m.get(k)}
    up = {j["child_prim"]: j["parent_prim"] for j in spec.get("joints", []) if j.get("joint_type") == "fixed"}

    def moves(path):
        for _ in range(32):
            if path in moving:
                return True
            if path not in up:
                return False
            path = up[path]
        return False

    import re as _re
    notes = " ".join((spec.get("_analysis") or {}).get("notes", []))
    held = {int(i) for i in _re.findall(r"#(\d+) [^#]*?(?:merged with fixed parts|a housing;)", notes)}
    forks = set((spec.get("_analysis") or {}).get("forks") or [])
    # a pin or rivet a hinge turns about is that hinge's axis, not a mover of
    # its own (a corkscrew's wing rivets were wanted as spin joints)
    pins = {int(i) for i in _re.findall(r"hinges about pin #(\d+)", notes)}
    missing, not_separable = [], []
    for p in survey["parts"]:
        if p.get("motion") in (None, "none", "flex") or p.get("seen") is False:
            continue
        if _re.search(r"\b(internal|hidden)\b", p.get("role") or "", _re.I):
            continue
        if p["id"] in pins or (p.get("motion") == "spin" and _re.search(r"\b(pin|rivet|axle)s?\b", p.get("role") or "", _re.I)
                               and _re.search(r"hinge|pivot|join|connect", p.get("role") or "", _re.I)):
            continue
        paths = [c for c in (p.get("copies") or [p["path"]])]
        if any(moves(x) or x in forks for x in paths):
            continue
        (not_separable if p["id"] in held else missing).append(f"#{p['id']} {p.get('role')} ({p.get('motion')})")
    for u in survey.get("unmatched_reference") or []:
        if u.get("merged_into"):
            not_separable.append(f"{u['part']} (modelled into #{u['merged_into']})")
    # what the classification saw move, against what is jointed: one survey run
    # called a floor lamp's head fixed while classification said it tilts, and
    # a 3 mm collar press passed as the lamp's whole motion
    stop = {"with", "from", "part", "parts", "joint", "pivot", "its", "the", "and", "that", "into", "onto",
            "clearly", "modelled", "not", "inline", "main", "body", "unit"}

    def words(t):
        return {w.rstrip("s") for w in _re.findall(r"[a-z]{4,}", (t or "").lower())} - stop

    jointed_words = set()
    for p in survey["parts"]:
        if any(moves(x) or x in forks for x in (p.get("copies") or [p["path"]])):
            jointed_words |= words(p.get("role"))
    for f in (entry.get("vlm") or {}).get("functions") or []:
        if f.get("kind") not in ("manual", "spring_return", "motor", "gate", "wheeled_base"):
            continue
        mp = f.get("moving_part") or ""
        if _re.search(r"\b(internal|hidden|not (clearly )?modell?ed|no external)\b", mp, _re.I):
            continue
        if _re.search(r"\bwhole (tool|object|body|unit|assembly|thing|device)\b", mp, _re.I):
            continue                           # the tool used as a lever: a use of the rigid whole, not a joint
        if not (words(mp) & jointed_words):
            missing.append(f"{f.get('does')} ({mp}) - seen by classification, nothing jointed matches")
    return {"ok": not missing, "missing": missing, "not_separable": not_separable, "checked": True}


def _video_schema() -> dict:
    return {"type": "object", "properties": {
        "detached_or_floating": {"type": "boolean", "description": "a part comes off, drops away, flies off or "
                                                                     "floats free of what holds it"},
        "passes_through": {"type": "boolean", "description": "a moving part passes through another part"},
        "whole_object_moves": {"type": "boolean", "description": "the whole object tips, slides, jumps or spins "
                                                                 "when only a part should move"},
        "broken_geometry": {"type": "boolean", "description": "missing, black, inside-out or flickering surfaces"},
        "physically_plausible": {"type": "boolean", "description": "overall: does this look like the real object "
                                                                   "being worked under real physics?"},
        "problems": {"type": "string", "description": "what is wrong, panel by panel, or empty"},
        "confidence": {"type": "number"}},
        "required": ["detached_or_floating", "passes_through", "whole_object_moves", "broken_geometry",
                     "physically_plausible", "problems", "confidence"], "additionalProperties": False}


def whole_video(entry: dict, summary: dict, frames: list, out_dir: Path, n: int = 8, rows: list | None = None) -> dict:
    """Frames from across the whole run, in order: the failures between joints
    (a knob dropping off a radio, grips leaving a handlebar) that no single
    joint's before-and-after shows."""
    import anthropic
    from PIL import Image

    import visual_qa as vq

    if len(frames) < 2:
        return {"physically_plausible": False, "problems": "no frames", "not_judged": True}
    # wide frames only: a close-up framed on a padlock's dials cuts its
    # shackle at the top edge, and the judge read a shackle "rising beyond
    # its length"; the close-ups have their own judge
    wide_dir = out_dir.parent / "frames_wide"      # the run's own wide view of the whole run
    wide = sorted(wide_dir.glob("f*.png")) if wide_dir.is_dir() else []
    if len(wide) < n:
        wide = _wide_frames(summary, frames) or frames
    picks = _picks(wide, summary, n, rows)
    # cropped to where the object is: at full frame a corkscrew was a
    # thumbnail and the judge, by its own account, could not see it
    box = _object_box([Image.open(p).convert("RGB") for p in picks])
    tiles = [_crop_tile(Image.open(p).convert("RGB"), box, (480, 270)) for p in picks]
    grid = Image.new("RGB", (480 * 4, 270 * ((n + 3) // 4)), "white")
    for k, t in enumerate(tiles):
        grid.paste(t, ((k % 4) * 480, (k // 4) * 270))
    path = out_dir / "whole_video.png"
    grid.save(path)
    vlm = entry.get("vlm") or {}
    beh = [b.get("type") for b in json.loads(entry.get("articulation_draft") or "{}").get("behaviors", [])]
    prompt = (f"Eight frames, in order left to right then top to bottom, from a physics simulation of "
              f"{vlm.get('object_name') or entry['asset_id']}. Its joints are worked one at a time; some frames "
              "are close-ups of the part being moved."
              + (" It is a wheeled base: driving or being pushed across the floor is meant to happen."
                 if "wheeled_base" in beh else "")
              + " The camera is not fixed: panels come from different cameras (wide and close-up), so the "
              "object's place, size and angle in the panel change without the object moving; judge the whole "
              "object moving only from its relation to the floor and its shadow."
              " Look for what a physics engine gets wrong: parts that come off or float, parts passing through "
              "each other, the whole object tipping or sliding when only a part should move, broken surfaces. "
              "Judge only what you can see: when the object is too small or the frames too alike to tell, say so "
              "and give a confidence below 0.5 rather than inferring a fault.")
    r = anthropic.Anthropic().messages.create(
        model="claude-opus-5", max_tokens=8000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": vq._b64(str(path))}},
            {"type": "text", "text": prompt}]}],
        output_config={"format": {"type": "json_schema", "schema": _video_schema()}})
    v = json.loads(next(b.text for b in r.content if b.type == "text"))
    allowed_move = "wheeled_base" in beh
    moved = (summary.get("integrity") or {}).get("root_moved_m")
    if v["whole_object_moves"] and moved is not None and float(moved) < 0.02:
        # the panels' cameras differ; the run measured the root: it stayed
        # (a pipe wrench "drifting and rotating" between panels moved 0 m)
        v["whole_object_moves"] = False
        v["camera_not_object"] = f"the root moved {moved} m by measurement; the panels' cameras differ"
        if not any(v[k] for k in ("detached_or_floating", "passes_through", "broken_geometry")):
            v["physically_plausible"] = True
    v["ok"] = bool(v["physically_plausible"] and not v["detached_or_floating"] and not v["passes_through"]
                   and not v["broken_geometry"] and (allowed_move or not v["whole_object_moves"]))
    if not v["ok"] and float(v.get("confidence") or 0) < 0.5:
        # an unsure fault still counts: on the gold set the judge saw a
        # multimeter's probes hover and a radio's knob dangle at confidence
        # 0.45, and the measured integrity run catches neither (rigid cables,
        # a knob still on its joint). A small render is answered by the crop
        # above, not by waving the verdict through.
        v["uncertain"] = True
    v["image"] = str(path.relative_to(REPO))
    return v


def _picks(wide: list, summary: dict, n: int, rows: list | None = None) -> list:
    """n wide frames: the start, each joint at its furthest (the frame its
    command peaks in joints.csv - a drive returns to rest by its end, and
    eight evenly spaced frames missed a shackle driven in the first three
    seconds), the end; the rest spread evenly."""
    import re as _re

    idx = {int(m.group(1)): p for p in wide for m in [_re.search(r"f(\d+)", p.name)] if m}
    if not idx:
        return [wide[round(k * (len(wide) - 1) / (n - 1))] for k in range(n)]
    peaks = []
    for name, v in (summary.get("joints") or {}).items():
        if v.get("follower") or not rows:
            continue
        col = f"{name}_cmd" if f"{name}_cmd" in rows[0] else f"{name}_meas" if f"{name}_meas" in rows[0] else None
        if col is None:
            continue
        vals = [abs(float(r.get(col) or 0.0)) for r in rows]
        peaks.append(max(range(len(vals)), key=lambda i: vals[i]))
    keys = sorted(idx)
    chosen = []
    for e in [0] + sorted(peaks) + [len(rows) - 1 if rows else keys[-1]]:
        near = min(keys, key=lambda k: abs(k - e))
        if idx[near] not in chosen:
            chosen.append(idx[near])
    if len(chosen) > n:
        step = (len(chosen) - 1) / (n - 1)
        chosen = [chosen[round(k * step)] for k in range(n)]
    for p in wide:
        if len(chosen) >= n:
            break
        if p not in chosen:
            chosen.append(p)
    return sorted(chosen, key=lambda p: p.name)[:n]


def _wide_frames(summary: dict, frames: list) -> list:
    """The frames shot from the wide camera: the run records every frame's
    camera (eye, target); a close-up stands much nearer its target. Without
    a record, frames whose object sits clear of every edge."""
    import math

    from PIL import Image

    rec = (summary.get("camera") or {}).get("frames") or []
    if len(rec) == len(frames) and all(isinstance(f, list) and len(f) >= 2 for f in rec):
        d = [math.dist(f[0], f[1]) for f in rec]
        far = max(d)
        return [p for p, x in zip(frames, d) if x >= 0.9 * far]
    return [p for p in frames if _whole_in_frame(Image.open(p).convert("RGB"))]


def _whole_in_frame(im) -> bool:
    """Whether the object sits wholly inside the frame (its colour or
    contrast core clear of every edge): a wide shot, not a close-up."""
    import numpy as np

    a = np.asarray(im, dtype=np.int16)
    h, w = a.shape[:2]
    bg = np.median(a.reshape(-1, 3), axis=0)
    # the whole object, pale parts too (a grey shackle over a brass body)
    core = ((a.max(axis=2) - a.min(axis=2)) > 30) | (np.abs(a - bg).max(axis=2) > 50)
    ys, xs = np.nonzero(core)
    if len(xs) < 20:
        return False
    m = 0.02
    return xs.min() > m * w and xs.max() < (1 - m) * w and ys.min() > m * h and ys.max() < (1 - m) * h


def _object_box(images) -> tuple:
    """The box, in pixels, that holds the object in all of these frames: the
    pixels that differ from the flat background (the floor's grey), with a
    margin. Falls back to the whole frame."""
    import numpy as np

    w, h = images[0].size
    lo, hi = [w, h], [0, 0]
    for im in images:
        a = np.asarray(im, dtype=np.int16)
        bg = np.median(a.reshape(-1, 3), axis=0)                    # the object is small: the median is floor
        diff = np.abs(a - bg).max(axis=2)
        sat = a.max(axis=2) - a.min(axis=2)
        # the object's core: colour, or strong contrast; a shadow and the
        # render's vignette are grey and mild. Pale parts (a translucent
        # handle) are caught by the margin around the core.
        # colour, or contrast against the floor: a brass body and its grey
        # shackle both; a mild shadow may widen the box, which is harmless,
        # a cut-off shackle is not (the judge read it as flying off)
        core = (sat > 30) | (diff > 70)       # 70: a grey shackle, not the floor's soft shadow
        ys, xs = np.nonzero(core)
        if len(xs) < 20:
            continue
        lo = [min(lo[0], int(xs.min())), min(lo[1], int(ys.min()))]
        hi = [max(hi[0], int(xs.max())), max(hi[1], int(ys.max()))]
    if hi[0] <= lo[0] or hi[1] <= lo[1]:
        return (0, 0, w, h)
    mx, my = 0.35 * (hi[0] - lo[0]) + 30, 0.35 * (hi[1] - lo[1]) + 30
    return (max(0, int(lo[0] - mx)), max(0, int(lo[1] - my)), min(w, int(hi[0] + mx)), min(h, int(hi[1] + my)))


def _crop_tile(im, box, size):
    """The box cut out at the tile's aspect (widened or heightened to fit,
    inside the frame where it can be) and scaled to the tile."""
    w, h = im.size
    x0, y0, x1, y1 = box
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    tw, th = size
    if bw / bh < tw / th:
        need = bh * tw / th
        cx = (x0 + x1) / 2
        x0, x1 = cx - need / 2, cx + need / 2
    else:
        need = bw * th / tw
        cy = (y0 + y1) / 2
        y0, y1 = cy - need / 2, cy + need / 2
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    return im.crop((int(x0), int(y0), int(x1), int(y1))).resize(size)


def judge(image: Path, prompt: str, which: str) -> dict:
    import visual_qa as vq

    if which == "claude":
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-5", max_tokens=16000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": vq._b64(str(image))}},
                {"type": "text", "text": prompt}]}],
            output_config={"format": {"type": "json_schema", "schema": _schema()}},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined")
        return json.loads(next(b.text for b in response.content if b.type == "text"))
    if which == "gemma":
        out = vq._post_json(f"{vq.OLLAMA_URL}/api/chat", {
            "model": vq.GEMMA_MODEL, "stream": False, "format": _schema(), "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt, "images": [vq._b64(str(image))]}]})
        return json.loads(out["message"]["content"])
    out = vq._post_json(f"{vq.COSMOS_URL}/chat/completions", {
        "model": vq.COSMOS_MODEL, "temperature": 0, "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vq._b64(str(image))}"}},
            {"type": "text", "text": prompt + "\n\nAnswer as JSON with keys " + ", ".join(_schema()["required"])}]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "motion_qa", "schema": _schema()}}})
    return vq._extract_json(out["choices"][0]["message"]["content"])


def critique(asset_id: str, which: str = "claude") -> dict:
    entry = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())
    summary, rows, frames = _frames(asset_id)
    out_dir = ANIM_DIR / asset_id / "motion_qa"
    out_dir.mkdir(exist_ok=True)
    verdicts = {}
    integ = summary.get("integrity") or {}
    if integ and not integ.get("ok", True):
        # the asset came apart or was thrown: its frames show the floor, and no
        # joint can be judged from them (nor blamed for it)
        bits = [f"joint {integ.get('worst_joint')} came {integ.get('max_joint_separation_m')} m apart",
                f"the root moved {integ.get('root_moved_m')} m"]
        if integ.get("fixed_came_apart"):
            bits.append("fixed parts came apart: " + ", ".join(integ["fixed_came_apart"][:6]))
        if integ.get("press_fits_let_go"):
            bits.append("press fits let go by themselves: " + ", ".join(integ["press_fits_let_go"][:6]))
        if integ.get("loose_bodies"):
            bits.append("parts held by nothing drifted off: " + ", ".join(integ["loose_bodies"][:6]))
        problem = ("the asset did not hold together in PhysX: " + "; ".join(bits)
                   + " (colliders overlapping across bodies, bodies nested in bodies, or parts never jointed)")
        result = {"date": date.today().isoformat(), "judge": "measured", "pass": False, "joints_judged": 0,
                  "joints": {}, "integrity": integ, "problem": problem}
        entry["motion_qa"] = result
        from processing import record
        record(entry, "critic", judge="measured", passed=False)
        (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
        return result
    unseen = _measured_only(entry, summary)
    for name in _pick_joints(summary):
        col = f"{name}_meas"
        vals = [abs(float(r[col])) if r.get(col) not in (None, "") else 0.0 for r in rows]
        if name in unseen:
            verdicts[name] = unseen[name]
            continue
        if not vals or max(vals) == 0.0:
            verdicts[name] = {"motion_ok": False, "problem": "the joint never moved in the animation"}
            continue
        n = min(len(vals), len(frames))
        signed = [float(rows[i][col]) if rows[i].get(col) not in (None, "") else 0.0 for i in range(n)]
        # the frames framed on this joint, where the animation logged them: a
        # close-up of the part, not the whole asset with the part a few pixels
        cand = _focus_frames(summary, name, n) or list(range(n))
        rest_i = cand[0]
        # each way the joint went (pliers open AND close), if it went far that way
        extremes = [max(cand, key=lambda i: signed[i]), min(cand, key=lambda i: signed[i])]
        top = max(abs(signed[i] - signed[rest_i]) for i in extremes)
        extremes = [i for i in extremes if abs(signed[i] - signed[rest_i]) >= 0.15 * top and top > 0]
        cam, pivot = summary.get("camera"), summary["joints"][name].get("pivot_world")
        for k, far in enumerate(extremes):
            key = name if k == 0 else f"{name} (other way)"
            # rest, a third, two thirds, the end: the motion, not two snapshots
            span = [i for i in cand if min(rest_i, far) <= i <= max(rest_i, far)] or [rest_i, far]
            d = signed[far] - signed[rest_i]
            mids = [min(span, key=lambda i: abs(signed[i] - (signed[rest_i] + f * d))) for f in (1 / 3, 2 / 3)]
            picks = [rest_i, *mids, far]
            rev = summary["joints"][name].get("type") == "revolute"
            marks = [project(pivot, cam, i) for i in picks] if cam and pivot and rev else None
            image = _strip([frames[i] for i in picks], out_dir / f"{name}{'_b' if k else ''}.png", marks,
                           cam if marks else None)
            v = None
            for attempt in range(2):            # a dropped connection is retried once
                try:
                    v = judge(image, _prompt(entry, name, summary["joints"][name], signed[far]), which)
                    break
                except Exception as ex:  # noqa: BLE001
                    err = str(ex)[:160]
            if v is None:
                # not judged is not failed: the repair must not take out a joint
                # the judge never saw (a connection error pruned 25 joints on 15
                # assets, a BMX's pedals and front wheel among them)
                v = {"motion_ok": None, "not_judged": True, "problem": f"judge error: {err}"}
            elif v.get("part_visible") is False:
                # unseen is not seen right: a floor lamp and a game console
                # passed with nothing visibly moving
                v.update(motion_ok=None, not_judged=True,
                         problem="no part visibly moves in the close-up: " + (v.get("problem") or ""))
            v["image"] = str(image.relative_to(REPO))
            verdicts[key] = v
    for name in _pick_joints(summary):
        problem = overtravel(entry, name)
        if problem:
            verdicts[f"{name} (measured)"] = {"motion_ok": False, "problem": problem}
    # what is missing: the real object's moving parts the draft left fixed
    comp = completeness(entry)
    if not comp["ok"]:
        verdicts["(completeness)"] = {"motion_ok": False, "whole_asset": True,
                                      "problem": "moves on the real object but is not jointed: "
                                                 + "; ".join(comp["missing"][:8])}
    # what happens between the joints: parts dropping off, passing through
    if which == "claude" and frames:
        try:
            wv = whole_video(entry, summary, frames, out_dir, rows=rows)
            verdicts["(whole video)"] = {"motion_ok": wv["ok"], "whole_asset": True,
                                         "problem": "" if wv["ok"] else wv.get("problems", ""), **wv}
        except Exception as ex:  # noqa: BLE001 - not judged, not failed
            verdicts["(whole video)"] = {"motion_ok": None, "not_judged": True, "whole_asset": True,
                                         "problem": f"judge error: {str(ex)[:160]}"}
    incomplete = any(v.get("not_judged") for v in verdicts.values())
    ok = bool(verdicts) and not incomplete and all(v.get("motion_ok") for v in verdicts.values())
    result = {"date": date.today().isoformat(), "judge": which, "pass": ok,
              "joints_judged": sum(1 for v in verdicts.values() if not v.get("not_judged")),
              "joints": verdicts, "completeness": comp, **({"incomplete": True} if incomplete else {})}
    entry["motion_qa"] = result
    from processing import record
    record(entry, "critic", judge=which, passed=ok, **({"incomplete": True} if incomplete else {}))
    (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="+")
    ap.add_argument("--judge", default="claude", choices=("claude", "cosmos", "gemma"))
    args = ap.parse_args()
    for a in args.assets:
        r = critique(a, args.judge)
        bad = {k: v.get("problem") or v.get("expected_motion") for k, v in r["joints"].items() if not v.get("motion_ok")}
        if r.get("integrity"):
            bad["(whole asset)"] = r["problem"]
        print(f"{a}: {'PASS' if r['pass'] else 'FAIL'} ({r['joints_judged']} joints judged)"
              + "".join(f"\n   {k}: {p}" for k, p in bad.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
