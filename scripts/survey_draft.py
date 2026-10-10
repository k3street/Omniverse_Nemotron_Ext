#!/usr/bin/env python3
"""Survey tier of articulation drafting: joints from the part survey.

For a kind of object no drafting tier knows (a wall switch, a toaster, a desk
lamp), the part survey (part_survey.py) says per part what it is, what it
moves against and how - in words a vision model can judge from renders. This
tier resolves the words with geometry:

  axis   part_long / part_short / part_face_normal: the part's own principal
         directions; vertical; horizontal_long / horizontal_short: the
         object's - each snapped to the nearest world axis.
  pivot  center, top, bottom, end_near_parent / end_far_from_parent (along the
         axis-free long direction of the part), contact_with_parent (the
         middle of where the part's and its parent's boxes overlap).
  hinge  a revolute: about a centre pivot it rocks both ways (a rocker
         switch: half the range each way); about an edge it opens one way -
         the way with room before it runs into its parent (swing_contact),
         capped there.
  slide  a prismatic along the axis toward the parent's middle (a toaster's
         lever slides down its slot); press: the same, short, sprung back.
  spin   a revolute with no stops (a knob, a wheel, a fan).
  detach a press-fit on its parent (add_mechanism): held, comes off above a
         force.
  flex   fixed (a cable, a spring arm: a soft body's business).

Parts the survey calls fixed, or small fasteners (screws), ride on their
parent - or on the part they were said to sit on. Wheels are named
wheel_L_*/wheel_R_* so a wheeled base (behaviors) drives them.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

UP = 2
FORK = re.compile(r"\b(fork|yoke|swivel (bracket|housing|plate|mount))\b", re.I)
FASTENER = re.compile(r"\b(screw|bolt|rivet|nut|washer|pin)s?\b", re.I)
PIN = re.compile(r"\b(pin|rivet|axle|pivot)s?\b", re.I)
ROLLING = re.compile(r"\b(wheel|caster|castor|roller)s?\b", re.I)
DIAL = re.compile(r"\b(dial|thumb|combination|number(ed)?|scroll|setting|hand|steering|selector|code)\b", re.I)
DRIVEN = re.compile(r"\b(rack and pinion|driven by|geared to|follows)\b", re.I)
LEADER = re.compile(r"\b(worm|screw shaft|screw|shaft|spindle|handle|crank|knob)\b", re.I)
DRIVEN_TURNS = 5.0            # a butterfly corkscrew's wings: fully up after about five turns


def _pin_id(p, sparts, geo):
    """The id of the pin part that joins p (see _pin_for). With the critic
    naming a pin (p["_on_pin"]), any pin-shaped fastener touching p counts,
    whatever the survey called its purpose."""
    best = None
    pc = np.array(p["centroid"], float)
    gp = geo.get(p["path"])
    if not gp:
        return None
    for q in sparts.values():
        if q is p or not (PIN.search(q.get("role") or "") or (p.get("_on_pin") and FASTENER.search(q.get("role") or ""))):
            continue
        if not p.get("_on_pin") and not re.search(r"hinge|pivot|join|connect|axle|pin", q.get("role") or "", re.I):
            continue
        gq = geo.get(q["path"])
        if not gq:
            continue
        if not all(gp["min"][k] - 0.003 <= gq["max"][k] and gq["min"][k] - 0.003 <= gp["max"][k] for k in range(3)):
            continue
        sz = np.array(q["size_m"], float)
        if sz.max() < 1.5 * np.sort(sz)[1]:
            continue                                   # a pin is long: no axis to read off a blob
        d = float(np.linalg.norm(np.array(q["centroid"], float) - pc))
        if best is None or d < best[0]:
            best = (d, q["id"])
    return best[1] if best else None


def _pin_for(p, sparts, geo):
    """(axis, centre) of the pin that joins hinge part p, or None: a pin-named
    part the survey says joins/hinges something, touching p's box, long in
    one direction."""
    qid = _pin_id(p, sparts, geo)
    if qid is None:
        return None
    q = sparts[qid]
    sz = np.array(q["size_m"], float)
    axis = np.eye(3)[int(np.argmax(sz))]
    return axis, np.array(q["centroid"], float)


def _pca(pts):
    c = pts.mean(0)
    _, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
    return c, sv, vt


def _snap(v):
    """The world axis nearest a direction: (index, sign)."""
    k = int(np.argmax(np.abs(v)))
    return k, (1.0 if v[k] >= 0 else -1.0)


def propose_survey(stage, asset_root: str, survey: dict, template: dict | None = None) -> tuple[dict, list[str]]:
    from articulation_draft import collect_parts
    from pivot_draft import _points

    template = template or {}
    sparts = {p["id"]: p for p in survey["parts"]}
    geo = {p["path"]: p for p in collect_parts(stage, asset_root)}
    lo = np.min([g["min"] for g in geo.values()], axis=0)
    hi = np.max([g["max"] for g in geo.values()], axis=0)
    span = float(max(hi - lo))
    # a wheel is round about its axle, a fork is not: labels read off colours
    # swap them (the engine crane's box-shaped forks were its "caster wheels")
    def _round(q):
        sz = sorted(q["size_m"])
        return sz[2] > 0 and abs(sz[2] - sz[1]) <= 0.15 * sz[2] and sz[0] < 0.8 * sz[1]
    for w in list(sparts.values()):
        if w.get("motion") != "spin" or not ROLLING.search(w.get("role") or "") or DIAL.search(w.get("role") or "") \
                or FORK.search(w.get("role") or "") or _round(w):
            continue
        f = next((q for q in sparts.values() if q is not w and FORK.search(q.get("role") or "") and _round(q)
                  and (q["id"] == w.get("relative_to") or q.get("relative_to") == w["id"])), None)
        if f is None:
            continue
        keys = ("role", "motion", "axis", "pivot", "range", "pivot_toward", "reference_part")
        wv, fv = {k: w.get(k) for k in keys}, {k: f.get(k) for k in keys}
        rel_w, rel_f = w.get("relative_to"), f.get("relative_to")
        w.update(fv)
        f.update(wv)
        # who hangs on whom flips with the names: the wheel hangs on the fork
        w["relative_to"], f["relative_to"] = (f["id"] if rel_f == w["id"] else rel_f), \
                                              (rel_w if rel_w != f["id"] else w["relative_to"])
        if rel_w == f["id"]:
            w["relative_to"], f["relative_to"] = rel_f, w["id"]

    # copies of one part (part_survey: identical meshes) each move on their
    # own: a part per copy, with what was folded in going to its nearest copy
    siblings: dict = {}
    for p in [q for q in sparts.values() if len(q.get("copies") or []) > 1]:
        copies = [c for c in p["copies"] if c in geo]
        if len(copies) < 2:
            continue
        folded = [m for m in p["members"] if m not in p["copies"]]
        near = {c: [] for c in copies}
        for m in folded:
            mc = np.array(geo[m]["centroid"]) if m in geo else np.array(p["centroid"])
            near[min(copies, key=lambda c: np.linalg.norm(np.array(geo[c]["centroid"]) - mc))].append(m)
        ids = []
        for i, c in enumerate(copies):
            g = geo[c]
            nid = p["id"] if c == p["path"] else p["id"] * 100 + i
            sparts[nid] = {**p, "id": nid, "path": c, "members": [c] + near[c], "copies": [],
                           "size_m": [float(v) for v in np.array(g["max"]) - np.array(g["min"])],
                           "centroid": [float(v) for v in g["centroid"]], "copy_of": p["id"]}
            ids.append(nid)
        if p["path"] not in copies:          # the numbered mesh itself was not a copy (should not happen)
            sparts.pop(p["id"], None)
        siblings[p["id"]] = ids
    horiz = sorted((0, 1), key=lambda k: -(hi - lo)[k])            # object's long, short horizontal
    # the body is what the other parts move against - not merely the biggest box:
    # a multimeter's test-lead cable outspans its housing, and hanging the meter
    # off a cable toppled it. Flexible parts (cables, straps) are never the body.
    from collections import Counter
    named = Counter(p.get("relative_to") for p in sparts.values() if p.get("relative_to") in sparts)
    rigid = [p for p in sparts.values() if p.get("motion") != "flex"] or list(sparts.values())
    # and a substantial part that stays put: six wheels name their forks, and
    # an engine crane hung off a caster fork
    big = max(np.prod(p["size_m"]) for p in rigid)
    still = [p for p in rigid if p.get("motion") in (None, "none") and not FORK.search(p.get("role") or "")
             and np.prod(p["size_m"]) >= 0.1 * big] or rigid
    body = max(still, key=lambda p: (named.get(p["id"], 0), np.prod(p["size_m"])))
    # a set (two coffee makers in one file): each object has its own body, and a
    # part with no parent named moves against its own object's body
    from ingest_asset import set_members
    members = [str(m.GetPath()) for m in set_members(stage, asset_root)] if template.get("set") else []
    obj_body = {}
    for mpath in members:
        inside = [p for p in sparts.values() if p["path"].startswith(mpath + "/") or p["path"] == mpath]
        if inside:
            b = max(inside, key=lambda p: np.prod(p["size_m"]))
            for p in inside:
                obj_body[p["id"]] = b
    obj_size = float(np.prod(hi - lo))

    held_back = []

    _surf: dict = {}
    forks: set = set()

    def contained(p) -> float:
        """Box volume of the other parts whose middles are inside p's box, over
        p's: a housing holds the machine (the K-Slim shell held its
        reservoir and pump), a lid or a door holds a handle and buttons."""
        g = geo.get(p["path"])
        if not g:
            return 0.0
        gmin, gmax = np.array(g["min"]), np.array(g["max"])
        pad = 0.05 * (gmax - gmin)
        # what rides on p is p's own (a boom's chain and extension, a leg's
        # caster): only parts hung elsewhere count as enclosed
        cands = [q for q in sparts.values() if q is not p and q.get("relative_to") != p["id"]
                 and np.all(np.array(q["centroid"]) > gmin + pad) and np.all(np.array(q["centroid"]) < gmax - pad)]
        if not cands:
            return 0.0
        # inside its box is not inside it: an engine crane's boom lies on a
        # diagonal whose box holds half the crane. A housing's surface is on
        # both sides of what it holds, along every axis.
        from swing_contact import surface_points
        cell = 0.02 * float(max(gmax - gmin))
        if p["id"] not in _surf:
            _surf[p["id"]] = surface_points(stage, p["members"], cell)
        sp = _surf[p["id"]]
        if not len(sp):
            return 0.0

        def enclosed(c):
            # both sides along two axes of three: a shell modelled open at the
            # bottom (or the back) still holds what is in it
            sides = 0
            for k in range(3):
                o = [i for i in range(3) if i != k]
                ray = sp[np.all(np.abs(sp[:, o] - c[o]) < 3 * cell, axis=1), k]
                sides += bool(len(ray)) and ray.min() < c[k] < ray.max()
            return sides >= 2

        inside = sum(np.prod(q["size_m"]) for q in cands if enclosed(np.array(q["centroid"], float)))
        return float(inside / max(np.prod(gmax - gmin), 1e-12))

    def full_height(p) -> bool:
        """As tall as the asset and as long one way, and not a thin leaf: a
        shell (a K-Slim's outer skin with its lid moulded in, which the survey
        called the lid). A cabinet door spans height and width but is thin."""
        ext = hi - lo
        frac = np.array(p["size_m"], float) / np.maximum(ext, 1e-9)
        h = [k for k in range(3) if k != UP]
        # only for an object that stands: lying flat (a caliper), every part
        # spans its thickness, and its sliding jaw read as a housing
        if ext[UP] < 0.3 * max(ext):
            return False
        return bool(frac[UP] >= 0.85 and max(frac[h[0]], frac[h[1]]) >= 0.85
                    and min(frac[h[0]], frac[h[1]]) >= 0.2)

    def housing(p) -> bool:
        # most of the asset (half its box), not of the body: the body is what
        # parts hang on, and on an engine crane that is a small foot plate
        return bool(np.prod(p["size_m"]) > 0.5 * obj_size or contained(p) > 0.35 or full_height(p)
                    or re.search(r"\b(shell|body|housing|base)\b.*\band\b|\band\b.*\b(shell|body|housing)\b",
                                 p["role"] or "", re.I))

    # a housing named as the mover hands its motion to the leaf riding on it:
    # the K-Mini survey called a full-height shell mesh the lid and the real
    # lid (29 mm thick, riding on it) a "bezel" - the shell is held back, and
    # the lid would have stayed fixed with it
    for p in sorted(sparts.values(), key=lambda p: p["id"]):
        if p is body or p["motion"] not in ("hinge", "slide") or not housing(p):
            continue
        riders = [q for q in sparts.values() if q.get("relative_to") == p["id"] and q is not p
                  and q.get("motion") in (None, "none") and q.get("seen") is not False and not housing(q)]
        if not riders:
            continue
        leaf = max(riders, key=lambda q: np.prod(q["size_m"]))
        sparts[leaf["id"]] = leaf = {**leaf, **{k: p.get(k) for k in ("motion", "axis", "pivot", "range",
                                                                      "pivot_toward", "reference_part")},
                                     "relative_to": p.get("relative_to"),
                                     "role": f"{leaf['role']} (moves as #{p['id']} {p['role']})"}
        for q in riders:
            if q is not leaf and q["id"] != leaf["id"]:
                sparts[q["id"]] = {**q, "relative_to": leaf["id"]}
        held_back.append(f"#{p['id']} {p['role']}: a housing; its motion given to #{leaf['id']}, the part on it")

    def moving(p):
        if p["motion"] in (None, "none", "flex") or p.get("seen") is False \
                or re.search(r"\b(internal|hidden)\b", p["role"] or "", re.I):
            return False
        # a "moving" part that is most of its object, or is named as two things
        # (a body shell and its brew head in one mesh), cannot move alone: it
        # needs splitting first (keurig K-Slim's shell hinged the machine)
        if p["motion"] in ("hinge", "slide") and housing(p):
            note = (f"#{p['id']} {p['role']}: moves as described but is merged with fixed parts - "
                             "split it first")
            if note not in held_back and not any(n.startswith(f"#{p['id']} ") and "a housing;" in n
                                                 for n in held_back):
                held_back.append(note)
            return False
        if FASTENER.search(p["role"] or "") and p["motion"] != "detach" \
                and np.prod(p["size_m"]) < 0.02 * np.prod(body["size_m"]):
            return False                                           # a screw head turns only for a screwdriver
            # (a corkscrew's "screw shaft" is the mechanism, not a fastener: size tells them apart)
        if p["motion"] == "spin" and FORK.search(p["role"] or ""):
            # a caster's fork swivels about its stem, not the axle: the wheeled
            # base makes it the caster's swivelling body (the crane's six forks
            # were drafted as six more wheels)
            forks.add(p["path"])
            return False
        return True

    def gap(a, b) -> float:
        """Distance between two parts' boxes (0 when they touch or overlap)."""
        ga, gb = geo.get(a["path"]), geo.get(b["path"])
        if not ga or not gb:
            return 0.0
        d = np.maximum(0.0, np.maximum(np.array(ga["min"]) - gb["max"], np.array(gb["min"]) - ga["max"]))
        return float(np.linalg.norm(d))

    def parent_of(p):
        q = sparts.get(p.get("relative_to"))
        q = q if q is not None and q is not p else obj_body.get(p["id"], body)
        if q["id"] in siblings:
            # hung on a part with copies: on the copy it touches
            q = min((sparts[i] for i in siblings[q["id"]] if i in sparts and sparts[i] is not p),
                    key=lambda r: gap(p, r), default=q)
        # a part moves against what it touches: the survey hung an engine
        # crane's wheel on the far leg (its mirror twin's), 0.7 m away
        reach = max(0.01, 0.25 * max(p["size_m"]))
        if gap(p, q) > reach:
            mine = {p["id"], p.get("copy_of")} - {None}       # what hangs on p (or on its copy group)
            near = [r for r in sparts.values() if r is not p and r.get("motion") not in ("flex",)
                    and r.get("relative_to") not in mine and gap(p, r) <= reach]
            if near:
                q = min(near, key=lambda r: (gap(p, r), -np.prod(r["size_m"])))
        return q

    # one control in several parts (a lever arm and its end knob, a knob and
    # its shaft): moving parts with the same motion and axis that touch move
    # as one - the smaller rides on the larger
    rides_on = {}
    movers = [p for p in sparts.values() if p is not body and moving(p)]
    for p in sorted(movers, key=lambda p: np.prod(p["size_m"])):
        for q in sorted(movers, key=lambda q: -np.prod(q["size_m"])):
            if q is p or q["id"] in rides_on or np.prod(q["size_m"]) < np.prod(p["size_m"]):
                continue
            if (q["motion"], q.get("axis")) != (p["motion"], p.get("axis")):
                continue
            # a rider is a small thing on a big one (a cap on a knob), with
            # its middle inside the carrier's box: five equal dials side by
            # side touch, and are five dials (a padlock's were chained as one)
            if np.prod(p["size_m"]) > 0.5 * np.prod(q["size_m"]):
                continue
            gp, gq = geo.get(p["path"]), geo.get(q["path"])
            if gp and gq and all(gq["min"][k] - 0.002 <= p["centroid"][k] <= gq["max"][k] + 0.002 for k in range(3)) \
                    and all(gp["min"][k] - 0.002 <= gq["max"][k] and gq["min"][k] - 0.002 <= gp["max"][k] for k in range(3)):
                rides_on[p["id"]] = q["id"]
                break
    joints, mechanisms, notes, attached = [], [], [], set()
    names = "XYZ"
    # the motion critic's corrections (a wing drafted to yaw: "through the
    # part's face, on its pin"): the survey's words for that part, overridden
    corrections = {int(k): v for k, v in (template.get("corrections") or {}).items() if v}
    for p in sorted(sparts.values(), key=lambda p: p["id"]):
        if p is body or not moving(p) or p["id"] in rides_on:
            continue
        fixed_axis = False
        if p["id"] in corrections:
            fix = corrections[p["id"]]
            p = {**p, **{k: fix[k] for k in ("axis", "pivot") if fix.get(k)}}
            if fix.get("pivot"):
                p["pivot_toward"] = None          # the critic's pivot, not the survey's edge
            fixed_axis = bool(fix.get("axis"))     # and its axis is final: no re-reading from the edge
            if fix.get("on_pin"):
                p["_on_pin"] = True                # the critic names a pin: any pin-shaped part touching it will do
            sparts[p["id"]] = p
            notes.append(f"#{p['id']} drafted on the critic's correction: " +
                         ", ".join(f"{k} {fix[k]}" for k in ("axis", "pivot") if fix.get(k)))
        par = parent_of(p)
        # a moving part hung on another moving part is fine (a shade on a lamp
        # arm); one hung on a part that is itself fixed rides on the body chain
        pts = _points(stage, p["path"])
        c, sv, vt = _pca(pts) if len(pts) >= 3 else (np.array(p["centroid"]), np.ones(3), np.eye(3))
        dirs = {"part_long": vt[0], "part_short": vt[1], "part_face_normal": vt[2],
                "vertical": np.eye(3)[UP], "horizontal_long": np.eye(3)[horiz[0]],
                "horizontal_short": np.eye(3)[horiz[1]],
                # "horizontal", the critic's word: a pin runs through a plate's
                # face, so the level direction nearest the part's face normal
                "horizontal_face": np.eye(3)[max(horiz, key=lambda i: abs(float(vt[2][i])))]}
        a_vec = dirs.get(p.get("axis") or "", vt[2] if p["motion"] in ("press", "spin") else vt[0])
        if p["motion"] == "spin":
            # a knob or a wheel turns about the direction it is round about: the
            # box axis across which the other two extents match (a 4 x 39 x 39 mm
            # dial turns about the 4). Not the vertices' principal axes: a
            # low-poly dial's 55 vertices bunch at its pointer.
            sz = np.array(p["size_m"], float)
            srt = np.sort(sz)
            if p.get("axis") == "part_long" and srt[2] >= 2.5 * srt[1]:
                # a shaft or a worm spins about its length (a corkscrew's screw
                # with its ring handle is round about nothing)
                a_vec = np.eye(3)[int(np.argmax(sz))]
            else:
                k = min(range(3), key=lambda i: (round(abs(sz[(i + 1) % 3] - sz[(i + 2) % 3])
                                                       / max(sz[(i + 1) % 3], sz[(i + 2) % 3], 1e-9), 2), sz[i]))
                a_vec = np.eye(3)[k]
        ax, _ = _snap(a_vec)
        pc = np.array(par["centroid"])
        g = geo.get(p["path"])
        pmin, pmax = (np.array(g["min"]), np.array(g["max"])) if g else (c - 0.01, c + 0.01)
        pivot = np.array(p["centroid"], float)
        piv = p.get("pivot") or "center"
        if piv == "contact_with_parent" and p["motion"] == "hinge":
            gp = geo.get(par["path"])
            if gp:
                olo, ohi = np.maximum(pmin, gp["min"]), np.minimum(pmax, gp["max"])
                if np.all(ohi - olo >= 0.6 * (pmax - pmin)):
                    # the part lies within its parent's box (a lid in a housing):
                    # "where they meet" is all of it - a hinge pins an edge
                    piv = "end_near_parent"
        tw = sparts.get(p.get("pivot_toward")) if p["motion"] == "hinge" else None
        if tw is not None and tw is not p:
            # the edge facing a named part (a K-Mini lid's rear edge faces its
            # reservoir): of the box's two directions across the hinge axis,
            # the one the part lies most along, its face on that part's side
            d = np.array(tw["centroid"], float) - np.array(p["centroid"], float)
            ext = pmax - pmin
            # which side faces it, over all three directions: the axis word
            # (read off the views) must not rule the facing side out (a K-Mini
            # lid's "part_short" was front-to-back, and its pin went on the front)
            srt = sorted(range(3), key=lambda i: ext[i])
            # an edge, not a face: never the thin direction of a flat part
            k = max((i for i in range(3) if i != srt[0]), key=lambda i: abs(d[i]) / max(ext[i], 1e-9))
            if ext[srt[2]] >= 3 * ext[srt[1]]:
                # a long part (a leg, a boom, a ram) is pinned at an END: its
                # narrow side faced the crane's foot plate, and the leg folded
                # about its own length
                k = srt[2]
            pivot[k] = pmax[k] if d[k] > 0 else pmin[k]
            piv = f"edge toward #{tw['id']}"
            # the pin runs along that edge, so across k. The axis words are
            # read off views and a whole file (a pair of K-Slims made
            # "horizontal_short" front-to-back): geometry decides between the two
            # directions left. A leaf (lid, door, leg) turns about the one in
            # its plane, not its thickness; a beam (boom, ram, handle) about a
            # horizontal one - the one other hinges here use, as linkages do.
            allowed = [i for i in range(3) if i != k]
            ext_o = sorted(range(3), key=lambda i: ext[i])
            beam = ext[ext_o[1]] < 2.5 * ext[ext_o[0]] and ext[ext_o[2]] > 3 * ext[ext_o[1]]
            if beam:
                horiz_ok = [i for i in allowed if i != UP] or allowed
                used = [names.index(j["axis"]) for j in joints if j.get("joint_type") == "revolute"
                        and j.get("lower_limit") is not None and names.index(j["axis"]) in horiz_ok]
                new_ax = max(set(used), key=used.count) if used else (ax if ax in horiz_ok else horiz_ok[0])
            else:
                thin_i = ext_o[0]
                cand = [i for i in allowed if i != thin_i] or allowed
                new_ax = ax if ax in cand else cand[0]
            if new_ax != ax and not fixed_axis:
                ax = new_ax
                a_vec = np.eye(3)[ax]
                vt = np.array([vt[0], np.eye(3)[ax], vt[2]])
        elif piv == "top":
            pivot[UP] = pmax[UP]
        elif piv == "bottom":
            pivot[UP] = pmin[UP]
        elif piv in ("end_near_parent", "end_far_from_parent"):
            long_dir = vt[0]
            s = pts @ long_dir
            near_end = pts[np.argmin(s)] if (pc - c) @ long_dir < 0 else pts[np.argmax(s)]
            far_end = pts[np.argmax(s)] if (pc - c) @ long_dir < 0 else pts[np.argmin(s)]
            e = near_end if piv == "end_near_parent" else far_end
            pivot = c + ((e - c) @ long_dir) * long_dir
        elif piv == "contact_with_parent":
            gp = geo.get(par["path"])
            if gp:
                olo, ohi = np.maximum(pmin, gp["min"]), np.minimum(pmax, gp["max"])
                pivot = (olo + ohi) / 2
                # a part that meets its parent in two places (a padlock's
                # shackle, both legs in the body) turns about the deeper one:
                # the retained leg holds more of the part inside the body
                inside = pts[np.all((pts >= np.array(gp["min"]) - 0.002) & (pts <= np.array(gp["max"]) + 0.002), axis=1)] \
                    if len(pts) else pts
                if len(inside) >= 6:
                    # the points that reach the parent are the contact; two
                    # clumps (both legs) -> the bigger; one clump (only the
                    # retained leg reaches the body) -> that one
                    across = [i for i in range(3) if i != ax]
                    spread = {i: float(np.ptp(inside[:, i])) for i in across}
                    k = max(across, key=lambda i: spread[i])
                    order = np.sort(inside[:, k])
                    gaps = np.diff(order)
                    contact = inside
                    if len(gaps) and gaps.max() > 0.2 * max(spread[k], 1e-9):
                        cut = order[int(np.argmax(gaps))]
                        low, high = inside[inside[:, k] <= cut], inside[inside[:, k] > cut]
                        contact = low if len(low) >= len(high) else high
                    pivot = contact.mean(axis=0)
                    pivot[ax] = (olo[ax] + ohi[ax]) / 2
        if p["motion"] == "hinge":
            # a pin or rivet the survey says joins this part is the hinge: its
            # long axis and its centre (a butterfly corkscrew's wings were
            # drafted to yaw about the body; their rivets run across it)
            pin = _pin_for(p, sparts, geo)
            if pin is not None:
                pax, pc = pin
                ax, _ = _snap(pax)
                pivot = pc
                notes.append(f"#{p['id']} hinges about pin #{_pin_id(p, sparts, geo)}")
        name = f"{p['motion']}_{p['id']:02d}_" + re.sub(r"\W+", "_", (p["role"] or "part").lower()).strip("_")[:24]
        rng = p.get("range")
        j = {"name": name, "parent_prim": par["path"], "child_prim": p["path"], "axis": names[ax],
             "anchor": [round(float(v), 6) for v in pivot]}
        if p["motion"] == "spin":
            if ROLLING.search(p["role"] or "") and not DIAL.search(p["role"] or ""):
                side = "L" if p["centroid"][ax] < (lo[ax] + hi[ax]) / 2 else "R"
                j["name"] = f"wheel_{side}_{p['id']:02d}"
            j.update(joint_type="revolute", lower_limit=None, upper_limit=None, stiffness=0.0, damping=0.01,
                     max_force=10.0)
        elif p["motion"] == "hinge":
            r = float(rng or template.get("hinge_deg", 90.0))
            j.update(joint_type="revolute", stiffness=0.0, damping=0.05, max_force=50.0)
            if piv == "center":
                j.update(lower_limit=round(-r / 2, 2), upper_limit=round(r / 2, 2))      # a rocker
            else:
                from swing_contact import swing_until_contact
                # a lid's pin is at its top or bottom face, not mid-thickness:
                # turned about its middle, the K-Mini lid's back corner dug into
                # the body at once both ways (0.5 deg of 90). Try the pivot on
                # either face of the part's thin direction and keep the most room.
                thin = np.cross(np.eye(3)[ax], vt[0])
                if np.linalg.norm(thin) < 1e-6:
                    thin = vt[2]
                thin = thin / np.linalg.norm(thin)
                t = pts @ thin
                c_t = pivot @ thin
                best = None
                for off in (0.0, t.max() - c_t, t.min() - c_t):
                    pv = pivot + off * thin
                    for d in (1.0, -1.0):
                        rm = swing_until_contact(stage, [par["path"]], p["members"], pv, ax, d, span, max_deg=r + 5,
                                                 hub_m=1.5 * float(t.max() - t.min()))
                        room_d = r if rm is None else min(r, rm)
                        if best is None or room_d > best[0] + 1e-6:
                            best = (room_d, d, pv)
                    if best[0] >= r:
                        break
                far, d, pivot = best
                if far < 0.25 * r:
                    # blocked every way, at once: the part sits in its parent's
                    # envelope (a lid in a housing's recess, the housing's walls
                    # single sheets) and the swing test cannot tell touching from
                    # inside. Open it as hinges do - the pin on the face away
                    # from the parent, the part's middle swinging away from the
                    # parent's - the full range; the critic judges it.
                    away = c - pc
                    pivot = pivot + ((t.max() if away @ thin > 0 else t.min()) - c_t) * thin
                    lever = c - pivot
                    sweep = np.cross(np.eye(3)[ax], lever)        # +turn moves the middle this way
                    d = 1.0 if sweep @ away >= 0 else -1.0
                    notes.append(f"#{p['id']} {p['role']}: the contact check found {far:.1f} of {r:g} deg "
                                 "(it sits in its parent's recess); opened away from its parent, full range")
                    far = r
                j["anchor"] = [round(float(v), 6) for v in pivot]
                j["_intended_deg"] = r
                j.update(lower_limit=round(min(0.0, d * far), 2), upper_limit=round(max(0.0, d * far), 2))
        elif p["motion"] in ("slide", "press"):
            r = float(rng or (3.0 if p["motion"] == "press" else 20.0)) / 1000.0
            toward = float(np.sign((pc - pivot) @ np.eye(3)[ax])) or -1.0
            gp = geo.get(par["path"])
            rail = False
            if p["motion"] == "slide" and gp:
                # where a slide runs, by shape before words (the axis word put a
                # caliper's jaw across its beam and its depth rod sideways):
                # a long thin part (a rod, a seat post) runs along its own
                # length; a part that wraps a rail (a caliper's slider) runs
                # along the rail.
                gext = np.array(gp["max"]) - np.array(gp["min"])
                psz = np.array(p["size_m"], float)
                ps = sorted(range(3), key=lambda i: psz[i])
                gs = sorted(range(3), key=lambda i: gext[i])
                long_k = None
                if psz[ps[2]] >= 3 * psz[ps[1]]:
                    long_k = ps[2]
                elif gext[gs[2]] >= 2 * gext[gs[1]] and psz[gs[2]] < 0.6 * gext[gs[2]] and any(
                        psz[i] >= 0.9 * gext[i] for i in range(3) if i != gs[2]):
                    long_k = gs[2]
                if long_k is not None:
                    rail = True
                    ax = long_k
                    j["axis"] = names[ax]
                    ahead = gp["max"][long_k] - pmax[long_k]
                    behind = pmin[long_k] - gp["min"][long_k]
                    if max(ahead, behind) > 0.2 * psz[long_k]:
                        toward = 1.0 if ahead >= behind else -1.0     # into the room the rail leaves
                        r = min(r, max(ahead, behind))
                    else:
                        # sticking out of its parent (a depth rod, a seat post):
                        # it runs further out, the way it points out
                        out_dir = float(np.sign((np.array(p["centroid"]) - np.array(gp["centroid"]))[long_k])) or 1.0
                        toward = out_dir
            if p["motion"] == "slide" and gp and not rail and (p.get("axis") or "").startswith("horizontal"):
                # a tray, a drawer, a reservoir slides out the way it is nearest
                # to leaving its parent (the K-Mini's drip tray went sideways)
                gap = {}
                for k2 in (0, 1):
                    gap[(k2, 1.0)] = gp["max"][k2] - pmax[k2]
                    gap[(k2, -1.0)] = pmin[k2] - gp["min"][k2]
                (ax, out_sign) = min(gap, key=gap.get)
                j["axis"] = names[ax]
                toward = out_sign
            j.update(joint_type="prismatic", lower_limit=round(min(0.0, toward * r), 5),
                     upper_limit=round(max(0.0, toward * r), 5))
            if p["motion"] == "press":
                force = float(template.get("press_force_n", 3.0))
                j.update(stiffness=round(force / r, 1), damping=round(0.05 * force / r, 2), max_force=100.0,
                         _role="button")
            else:
                j.update(stiffness=0.0, damping=1.0, max_force=100.0)
        elif p["motion"] == "detach":
            top = list(pivot)
            mechanisms.append({"type": "press_fit", "holder": par["path"], "part": p["path"],
                               "anchor": [round(float(v), 6) for v in top],
                               "break_force_n": float(template.get("detach_force_n", 20.0))})
            attached.add(p["path"])
            notes.append(f"#{p['id']} {p['role']} comes off {par['role']}")
            continue
        joints.append(j)
        attached.add(p["path"])
        notes.append(f"#{p['id']} {p['role']}: {p['motion']} about/along {names[ax]} at {piv}"
                     + (f", range {rng:g}" if rng else ""))
    # everything else rides: on the part the survey said it sits on, else the body
    moving_paths = {j["child_prim"] for j in joints} | attached
    # a part the survey says a worm, screw or rack drives follows that
    # part's turn: a butterfly corkscrew's wings rise as the handle is
    # screwed down (rack and pinion). PhysX mimic: the coupling is two-way.
    # Full travel over DRIVEN_TURNS turns unless a manual says otherwise.
    driven = [j for j in joints if j.get("joint_type") == "revolute" and j.get("lower_limit") is not None
              and DRIVEN.search(next((q.get("role") or "" for q in sparts.values() if q["path"] == j["child_prim"]), ""))]
    leaders = [j for j in joints if j.get("joint_type") == "revolute" and j.get("lower_limit") is None
               and LEADER.search(next((q.get("role") or "" for q in sparts.values() if q["path"] == j["child_prim"]), ""))]
    if driven and len(leaders) == 1:
        lead = leaders[0]
        for j in driven:
            lo_, hi_ = float(j["lower_limit"]), float(j["upper_limit"])
            travel = hi_ if abs(hi_) >= abs(lo_) else lo_
            # follower + gearing * leader = 0: the handle screwed down (a
            # negative turn about its axis, clockwise from above) lifts the wing
            gearing = travel / (DRIVEN_TURNS * 360.0)
            mechanisms.append({"type": "couple", "leader": lead["name"], "follower": j["name"],
                               "gearing": round(gearing, 6),
                               "note": f"follows {lead['name']}: {abs(travel):g} deg over {DRIVEN_TURNS:g} turns (rack and pinion)"})
            notes.append(f"{j['name']} follows {lead['name']} ({abs(travel):g} deg over {DRIVEN_TURNS:g} turns)")

    for p in sorted(sparts.values(), key=lambda p: p["id"]):
        for member in p["members"]:
            if member in moving_paths or member == body["path"]:
                continue
            host = p["path"] if member != p["path"] else None
            if host is None and p["id"] in rides_on:
                host = sparts[rides_on[p["id"]]]["path"]           # part of the same control
            if host is None:
                # the copy it touches, as for moving parts (a fork on the leg
                # beside it, not on the leg the survey's number names)
                rel = parent_of(p) if p.get("relative_to") in sparts else None
                host = rel["path"] if rel and rel["path"] in moving_paths else body["path"]
            joints.append({"name": f"part_{len(joints):03d}", "joint_type": "fixed", "parent_prim": host,
                           "child_prim": member})
    notes += held_back
    for u in survey.get("unmatched_reference") or []:
        # the manual says it moves, the model has it fused into another part
        notes.append(f"reference: {u['part']} moves on the real product but is "
                     + (f"modelled into #{u['merged_into']} - split it out to articulate it"
                        if u.get("merged_into") else "not in the model"))
    if not any(j["joint_type"] != "fixed" for j in joints) and not mechanisms:
        raise RuntimeError("the survey found no moving part" + (f" it can move alone ({'; '.join(held_back)})"
                                                               if held_back else ""))
    spec = {"prim_path": asset_root, "fixed_base": bool(template.get("mounted", False)),
            "approximation": "convexDecomposition", "joints": joints, "mechanisms": mechanisms,
            "button_joints": [j["name"] for j in joints if j.get("_role") == "button"],
            "_analysis": {"tier": "survey", "body": body["path"], "notes": notes, "forks": sorted(forks)},
            "_instructions": "Drafted from the part survey (a vision model's reading of each part). "
                             "CHECK each moving part's axis, pivot and range."}
    return spec, notes
