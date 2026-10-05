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
FASTENER = re.compile(r"\b(screw|bolt|rivet|nut|washer|pin)s?\b", re.I)


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
    horiz = sorted((0, 1), key=lambda k: -(hi - lo)[k])            # object's long, short horizontal
    # the body is what the other parts move against - not merely the biggest box:
    # a multimeter's test-lead cable outspans its housing, and hanging the meter
    # off a cable toppled it. Flexible parts (cables, straps) are never the body.
    from collections import Counter
    named = Counter(p.get("relative_to") for p in sparts.values() if p.get("relative_to") in sparts)
    rigid = [p for p in sparts.values() if p.get("motion") != "flex"] or list(sparts.values())
    body = max(rigid, key=lambda p: (named.get(p["id"], 0), np.prod(p["size_m"])))
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

    def contained(p) -> float:
        """Box volume of the other parts whose middles are inside p's box, over
        p's: a housing holds the machine (the K-Slim shell held its
        reservoir and pump), a lid or a door holds a handle and buttons."""
        g = geo.get(p["path"])
        if not g:
            return 0.0
        gmin, gmax = np.array(g["min"]), np.array(g["max"])
        pad = 0.05 * (gmax - gmin)
        inside = sum(np.prod(q["size_m"]) for q in sparts.values() if q is not p
                     and np.all(np.array(q["centroid"]) > gmin + pad) and np.all(np.array(q["centroid"]) < gmax - pad))
        return float(inside / max(np.prod(gmax - gmin), 1e-12))

    def housing(p) -> bool:
        own = obj_body.get(p["id"], body)
        return bool(np.prod(p["size_m"]) > 0.5 * np.prod(own["size_m"]) or contained(p) > 0.35
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
        sparts[leaf["id"]] = leaf = {**leaf, **{k: p.get(k) for k in ("motion", "axis", "pivot", "range")},
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
        if FASTENER.search(p["role"] or "") and p["motion"] != "detach":
            return False                                           # a screw head turns only for a screwdriver
        return True

    def parent_of(p):
        q = sparts.get(p.get("relative_to"))
        return q if q is not None and q is not p else obj_body.get(p["id"], body)

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
            gp, gq = geo.get(p["path"]), geo.get(q["path"])
            if gp and gq and all(gp["min"][k] - 0.002 <= gq["max"][k] and gq["min"][k] - 0.002 <= gp["max"][k]
                                 for k in range(3)):
                rides_on[p["id"]] = q["id"]
                break
    joints, mechanisms, notes, attached = [], [], [], set()
    names = "XYZ"
    for p in sorted(sparts.values(), key=lambda p: p["id"]):
        if p is body or not moving(p) or p["id"] in rides_on:
            continue
        par = parent_of(p)
        # a moving part hung on another moving part is fine (a shade on a lamp
        # arm); one hung on a part that is itself fixed rides on the body chain
        pts = _points(stage, p["path"])
        c, sv, vt = _pca(pts) if len(pts) >= 3 else (np.array(p["centroid"]), np.ones(3), np.eye(3))
        dirs = {"part_long": vt[0], "part_short": vt[1], "part_face_normal": vt[2],
                "vertical": np.eye(3)[UP], "horizontal_long": np.eye(3)[horiz[0]],
                "horizontal_short": np.eye(3)[horiz[1]]}
        a_vec = dirs.get(p.get("axis") or "", vt[2] if p["motion"] in ("press", "spin") else vt[0])
        if p["motion"] == "spin":
            # a knob or a wheel turns about the direction it is round about: the
            # box axis across which the other two extents match (a 4 x 39 x 39 mm
            # dial turns about the 4). Not the vertices' principal axes: a
            # low-poly dial's 55 vertices bunch at its pointer.
            sz = np.array(p["size_m"], float)
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
        if piv == "top":
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
        name = f"{p['motion']}_{p['id']:02d}_" + re.sub(r"\W+", "_", (p["role"] or "part").lower()).strip("_")[:24]
        rng = p.get("range")
        j = {"name": name, "parent_prim": par["path"], "child_prim": p["path"], "axis": names[ax],
             "anchor": [round(float(v), 6) for v in pivot]}
        if p["motion"] == "spin":
            if re.search(r"wheel|caster|castor|roller", p["role"] or "", re.I):
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
            if p["motion"] == "slide" and gp and (p.get("axis") or "").startswith("horizontal"):
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
    for p in sorted(sparts.values(), key=lambda p: p["id"]):
        for member in p["members"]:
            if member in moving_paths or member == body["path"]:
                continue
            host = p["path"] if member != p["path"] else None
            if host is None and p["id"] in rides_on:
                host = sparts[rides_on[p["id"]]]["path"]           # part of the same control
            if host is None:
                rel = sparts.get(p.get("relative_to"))
                host = rel["path"] if rel and rel["path"] in moving_paths else body["path"]
            joints.append({"name": f"part_{len(joints):03d}", "joint_type": "fixed", "parent_prim": host,
                           "child_prim": member})
    notes += held_back
    if not any(j["joint_type"] != "fixed" for j in joints) and not mechanisms:
        raise RuntimeError("the survey found no moving part" + (f" it can move alone ({'; '.join(held_back)})"
                                                               if held_back else ""))
    spec = {"prim_path": asset_root, "fixed_base": bool(template.get("mounted", False)),
            "approximation": "convexDecomposition", "joints": joints, "mechanisms": mechanisms,
            "button_joints": [j["name"] for j in joints if j.get("_role") == "button"],
            "_analysis": {"tier": "survey", "body": body["path"], "notes": notes},
            "_instructions": "Drafted from the part survey (a vision model's reading of each part). "
                             "CHECK each moving part's axis, pivot and range."}
    return spec, notes
