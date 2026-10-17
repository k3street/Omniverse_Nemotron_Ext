#!/usr/bin/env python3
"""A hinged door opened by its handle with the R5's left Ability Hand - a
refrigerator's door taken by its bar handle, the fingers wrapped behind the
bar, the hand carried round the hinge with the door - planned with HomeHero's
geometry, checker and RRT (the HomeHero venv; called by the adapter).

Everything about the door comes from the asset (plan_er_grasp.py reads it):
the hinge is the door joint's frame, the axis its axis, the handle the named
prim under the door, the opening sense the one that carries the handle away
from the appliance (out of the door's plane, toward the room).

The grip, as a person takes a refrigerator's bar handle:

  the hand's y (the palm's normal)  into the door, the palm on the bar's front
  the hand's x (pinky -> index)    along the bar (either way up: searched)
  the hand's z (the fingers)        across the bar, curling behind it, into
                                    the gap between the bar and the door

searched over heights along the bar, depths along the fingers and small turns
about the bar; each candidate's fingers swept closed against the bar (they must
meet it before the door), the open hand clear of everything.

Then: an RRT reach to a staging pose in front of the bar with the hand open,
a straight slide onto it, the close (the firmware stops each finger on the
bar), the pull - the grip carried rigidly round the hinge, the door at each
step where the hand has taken it, IK per degree, the forearm clear of the
swinging door - as far as the arm reaches up to the target angle, the hold,
the release, the hand drawn back from the door.
"""
from __future__ import annotations

import numpy as np

HZ = 30
FINGERS = ("index", "middle", "ring", "pinky")


def body_name(path):
    """A checker body's name: its link for a robot collider (".../amber_left_arm_link_4/commissioning_collision"),
    else its own last element (an obstacle id)."""
    parts = str(path).split("/")
    return parts[-2] if len(parts) > 1 and parts[-1] in ("commissioning_collision", "collision", "collisions") else parts[-1]


def hit_names(pairs, k=3):
    return ", ".join(f"{body_name(a_)}~{body_name(b_)}" for a_, b_ in pairs[:k])


def rot_about(axis, ang):
    """The rotation matrix about the unit `axis` by `ang` (rad)."""
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)


def swing(P, hinge, axis, ang):
    """Points (or a pose's origin) carried round the hinge by `ang`."""
    R = rot_about(axis, ang)
    return np.asarray(hinge, float) + (np.atleast_2d(np.asarray(P, float)) - hinge) @ R.T


def door_frame(handle_pts, door_pts, hinge, axis, body_centre=None):
    """The handle bar and the door it stands on: the bar's long axis `a`
    (pointing up when it is near vertical), the door's outward normal `n`
    (the side the handle stands out on), the opening sense `sense` (+1 or
    -1 about `axis`: the one that moves the handle along +n), the bar's
    middle point `c` (half way through its depth along n), the depths along
    n of the bar's front, its back, and the door's face behind it, and the
    bar's free span along `a` (between its posts)."""
    H = np.asarray(handle_pts, float)
    D = np.asarray(door_pts, float)
    hinge = np.asarray(hinge, float)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    hc = H.mean(0)
    a = np.linalg.svd(H - hc, full_matrices=False)[2][0]
    if a[2] < 0:
        a = -a
    r = hc - hinge
    r = r - axis * (r @ axis)
    # the door's own plane: its thinnest direction (square to the hinge axis), not
    # the hinge-to-handle line - the handle stands proud of the door and tilted it 6 deg
    Dc = D - D.mean(0)
    Dc = Dc - np.outer(Dc @ axis, axis)
    vt = np.linalg.svd(Dc, full_matrices=False)[2]
    n = next(v for v in vt[::-1] if abs(v @ axis) < 0.5)                # the thinnest direction that is not the axis
    n = n - axis * (n @ axis)
    n = n / np.linalg.norm(n)
    # outward: away from the appliance's body when it is known (a door's own free edge,
    # taken as the handle, stands out on neither side: the palm went behind the door, 2026-10-16);
    # else the side the handle stands out on
    if body_centre is not None:
        if (D.mean(0) - np.asarray(body_centre, float)) @ n < 0:
            n = -n
    elif (hc - D.mean(0)) @ n < 0:
        n = -n
    sense = 1.0 if np.cross(axis, r) @ n > 0 else -1.0
    # the bar: its points between the posts (the middle half of its length)
    s = (H - hc) @ a
    mid = H[np.abs(s) < 0.25 * np.ptp(s)]
    dn = (mid - hinge) @ n
    front, back = float(dn.max()), float(dn.min())
    # the door's face behind the bar: door points near the handle, not the handle's own
    lat = H - np.outer((H - hc) @ a, a)
    from scipy.spatial import cKDTree
    D = D[cKDTree(H).query(D)[0] > 0.003]                                # the door without its handle
    near = D[np.linalg.norm((D - hc) - np.outer((D - hc) @ a, a), axis=1) < 0.05]
    near = near[np.abs((near - hc) @ a) < 0.5 * np.ptp(s)]
    face = float(((near - hinge) @ n).max()) if len(near) else back
    c = mid.mean(0)
    c = c + n * (0.5 * (front + back) - (c - hinge) @ n)
    return {"a": a, "n": n, "sense": sense, "c": c, "front": front, "back": back, "door_face": face,
            "gap": back - face, "span": [float(((mid - c) @ a).min()), float(((mid - c) @ a).max())],
            "radius": float(np.linalg.norm(r)), "lateral_width": float(np.ptp(lat @ np.cross(a, n)))}


def grip_rotation(a, n, x_sign=1.0, yaw=0.0):
    """The hand's world rotation (columns x, y, z) for the bar grip: x along
    the bar (x_sign: index up or down), y = -n (the palm onto the door), z =
    x cross y (the fingers across the bar); yaw turns it about the bar."""
    from tool_grip_planner import _rot
    x = np.asarray(a, float) * x_sign
    y = -np.asarray(n, float)
    R = np.column_stack([x, y, np.cross(x, y)])
    return _rot(x, yaw) @ R


def push_needed(open_deg, min_open_deg, angle_now_deg, min_push_deg=10.0):
    """The push (planned, least) that takes a door standing `angle_now_deg` open
    to the door angles open_deg and min_open_deg - at least min_push_deg of it,
    so a door already past its least is still worked by the hand."""
    if angle_now_deg <= 0.5:
        return float(open_deg), float(min_open_deg)
    return max(float(open_deg) - angle_now_deg, 0.0), max(float(min_open_deg) - angle_now_deg, float(min_push_deg))


def cabinet_of(others, door_body=None):
    """The appliance's body - the outward side of its door is away from it: its
    biggest body that is not the door itself. Among a button's 'other bodies'
    the door's points outnumbered the cabinet's, were taken for the cabinet,
    and the door's swing was turned into the cabinet (2026-10-16)."""
    return max((np.asarray(P, float) for k, P in others.items() if k != door_body), key=len)


def body_boxes(P, name, slab_m=0.02, pad=0.005):
    """An appliance body as boxes for the checker: axis-aligned slabs `slab_m`
    wide across its wider horizontal extent, each its own box, padded. One
    box about a microwave's cabinet took its control panel's front (4.2 cm
    proud of the door's recess) across the whole front and filled the gap
    behind the popped door with obstacle: the hand pushing from inside hit
    it on every pose (2026-10-16)."""
    P = np.asarray(P, float)
    k = int(np.argmax(np.ptp(P[:, :2], axis=0)))
    s = P[:, k]
    out = []
    for i, lo in enumerate(np.arange(s.min(), s.max() + 1e-9, slab_m)):
        m = (s >= lo - 1e-9) & (s <= lo + slab_m + 1e-9)
        if m.sum() < 3:
            continue
        out.append(dict(id=f"door:{name}:{i}", min=(P[m].min(0) - pad).tolist(), max=(P[m].max(0) + pad).tolist()))
    return out


def body_hulls(P, band=0.02, min_points=8):
    """An appliance body as convex hulls of slabs across its wider horizontal
    extent (as body_boxes): slabs along the vertical made each slice's hull run
    straight from the control panel's proud front corner to the hinge side,
    filling the door's recess with a wedge up to 4 cm deep - every push pose
    behind a door popped 9.6 deg read as in the cabinet (2026-10-16)."""
    from tool_grip_planner import slab_hulls
    P = np.asarray(P, float)
    k = int(np.argmax(np.ptp(P[:, :2], axis=0)))
    ax = np.zeros(3); ax[k] = 1.0
    return slab_hulls(P, ax, band=band, min_points=min_points)


def push_approaches(inward, back_edge, back_up):
    """The ways a hand gets into the gap behind a door ajar, each (name, the hand's
    direction of travel in, how far back along it staging stands): from beyond the
    free edge along the door, down from above its top (the gap is open there), or
    both at once. Staging is the push pose minus direction x distance - from above
    is a travel DOWN (a first cut staged the hand 30 cm under the push, in the
    counter, 2026-10-16)."""
    inward = np.asarray(inward, float)
    down = np.array([0.0, 0.0, -1.0])
    diag = (inward + down) / np.linalg.norm(inward + down)
    return [("beyond the edge", inward, float(back_edge)), ("from above", down, float(back_up)),
            ("above and beyond", diag, max(float(back_edge), float(back_up)) * float(np.sqrt(2.0)))]


def search_pushes(hand, door, opts):
    """A door pushed open from inside (a popped microwave door: the hand in the
    gap behind its free edge, as a person opens it from the latch side): the
    hand's palm side toward the door's INNER face, its fingers into the gap
    along the door (or along the edge), the wrist pitched out toward the room
    and the fingers curled to hook behind the edge; seated along the door's
    outward normal until it meets the door, then kept only when two or more
    of its parts (palm, fingers) touch the inner face - not the edge's side,
    not the cabinet - and the whole hand is clear of the door and the
    cabinet. A wrap round the edge from outside needs the arm beyond the edge
    (no IK from the latch side), and a flat palm behind the door put the
    forearm on the control panel's front (2026-10-16)."""
    from scipy.spatial import ConvexHull, cKDTree

    from tool_grip_planner import _rot, hull_entry, inside, slab_hulls
    H = np.asarray(door["handle_points"], float)
    D = np.asarray(door["door_points"], float)
    hinge, axis = np.asarray(door["hinge"], float), np.asarray(door["axis"], float)
    others = [np.asarray(v, float) for v in door.get("others", {}).values()]
    body_c = max(others, key=len).mean(0) if others else None
    fr = door_frame(H, D, hinge, axis, body_c)
    a, n = fr["a"], fr["n"]                                            # n: out of the door, away from the cabinet
    up = np.array([0.0, 0.0, 1.0])
    door_hulls = slab_hulls(D, up, band=0.01, min_points=8)
    other_hulls = [h for P in others for h in body_hulls(P)]
    everything = np.vstack([D] + others)
    Dtree = cKDTree(D)
    palm = hand.palm_pts()
    palm_eq = ConvexHull(palm).equations
    rvec = hinge - H.mean(0)                                           # the door's plane, square to the edge:
    rvec = rvec - axis * (rvec @ axis) - n * (rvec @ n)                # from the free edge in toward the hinge
    inward = rvec / np.linalg.norm(rvec)
    edge_in = float(((D - fr["c"]) @ inward).min())                    # the free edge, along the door from c
    gap = float(opts.get("palm_gap_m", 0.004))
    thumb_rot = float(opts.get("thumb_rot", 0.0))
    # the hand's parts at each curl, each its own convex piece (a curled finger's hull
    # with the palm's would fill the hook the edge sits in)
    shapes = {}
    for curl in opts.get("push_curl_rad", [0.25, 0.7, 1.1]):
        q = np.array([curl] * 4 + [0.0, thumb_rot])
        parts = {"palm": palm, **{d: hand.digit(d, q) for d in ("index", "middle", "ring", "pinky", "thumb")}}
        shapes[float(curl)] = (q, parts, {k: ConvexHull(P).equations for k, P in parts.items()})
    sH = (H - fr["c"]) @ a                                             # an edge has no posts: its whole length
    span = [float(sH.min()), float(sH.max())]
    heights = opts.get("heights_m") or np.round(np.arange(span[0] + 0.06, span[1] - 0.06 + 1e-9, 0.04), 3).tolist()
    out, why = [], {}

    def tally(k):
        why[k] = why.get(k, 0) + 1
    dirs = {"along the door": inward, "down the edge": -a, "up the edge": a}
    for z_name in opts.get("push_dirs", list(dirs)):
        z_dir = dirs[z_name]
        zz = z_dir - n * (z_dir @ n)
        zz /= np.linalg.norm(zz)
        R0 = np.column_stack([np.cross(n, zz), n, zz])                 # the palm (+y) toward the door's inner face
        for yaw in np.radians(opts.get("yaw_deg", [0.0])):
            for pitch in np.radians(opts.get("pitch_deg", [0.0, 15.0, 30.0, 45.0])):
                # pitched about the hand's x: the fingers tilt into the gap (-n), the wrist out (+n)
                R = _rot(R0[:, 0], pitch) @ _rot(n, yaw) @ R0
                for curl, (q, parts, eqs) in shapes.items():
                    for h in heights:
                        for inset in opts.get("inset_m", [-0.03, -0.01, 0.01, 0.03, 0.05]):
                            # the palm's centre `inset` in from the free edge (negative: beyond it)
                            c = fr["c"] + a * h + inward * (edge_in + inset)
                            o0 = c - R @ palm.mean(0)
                            far = o0 - n * 0.08
                            near = everything[np.linalg.norm(everything - o0, axis=1) < 0.22]
                            # seated against the door alone (the cabinet behind the hand is checked after:
                            # the pitched fingertips start in it and stopped the seat at once)
                            rel = (Dn_ := D[np.linalg.norm(D - o0, axis=1) < 0.22], (Dn_ - far) @ R)[1]
                            u = R.T @ -n                                       # the scene moves -n in the hand's frame
                            ts = [hull_entry(rel, u, eq) for eq in eqs.values()]
                            t = min((float(t_[np.isfinite(t_)].min()) for t_ in ts if np.isfinite(t_).any()), default=None)
                            if t is None:
                                tally("nothing to push"); continue
                            o = far + n * (t - gap)
                            Wp = {k: o + P @ R.T for k, P in parts.items()}
                            W = np.vstack(list(Wp.values()))
                            if inside(W, door_hulls, 0.0015).any():
                                tally("the hand in the door"); continue
                            if inside(W, other_hulls, 0.0015).any() or \
                                    inside((near - o) @ R, [(palm_eq, palm.min(0), palm.max(0))], 0.0015).any():
                                tally("the hand in the cabinet"); continue
                            # which parts touch the door, and where: on its inner face, in from the edge
                            on = []
                            for k, P in Wp.items():
                                dd, ii = Dtree.query(P)
                                j = int(np.argmin(dd))
                                if dd[j] < gap + 0.004 and (D[ii[j]] - fr["c"]) @ inward > edge_in + 0.008 \
                                        and (D[ii[j]] - P[j]) @ n > 0:
                                    on.append(k)
                            if len(on) < int(opts.get("push_min_parts", 2)):
                                tally("fewer than two parts on the inner face"); continue
                            back = float(((W - o) @ inward).max() - ((D - o) @ inward).min()) + 0.02
                            # or from above: the gap behind a door ajar is open at its top
                            back_up = float(D[:, 2].max() - W[:, 2].min()) + 0.03
                            approaches = push_approaches(inward, back, back_up)
                            score = -0.15 * len(on) + 0.5 * abs(h) / max(1e-6, np.ptp(span)) + 0.2 * abs(yaw)
                            out.append(dict(R=R, o=o, x_sign=0.0, yaw_deg=round(float(np.degrees(yaw)), 1),
                                            pitch_deg=round(float(np.degrees(pitch)), 1), curl_rad=curl, hand_q=q.tolist(),
                                            height_m=h, depth_m=inset, wrap={}, push=z_name, on=on,
                                            approach_dir=inward, approach_m=round(back, 3), approaches=approaches,
                                            score=round(float(score), 4),
                                            key=f"push {z_name} pitch {np.degrees(pitch):.0f} curl {curl:.2f}"))
    out.sort(key=lambda c_: c_["score"])
    return out, {"rejected": why, "edge_in_m": round(edge_in, 4), "found": len(out),
                 "frame": {k: (np.round(v, 4).tolist() if isinstance(v, np.ndarray) else v) for k, v in fr.items()}}


def search_grips(hand, door, opts):
    """Bar grips ranked: every finger that meets the bar does so before the
    door (the fingers curl into the gap behind it), at least three fingers on
    the bar, the open hand clear of the bar, the door and the appliance."""
    from scipy.spatial import ConvexHull

    from tool_grip_planner import inside, seat_along, slab_hulls, tool_in_hulls
    H = np.asarray(door["handle_points"], float)
    D = np.asarray(door["door_points"], float)
    hinge, axis = np.asarray(door["hinge"], float), np.asarray(door["axis"], float)
    others_ = [np.asarray(v, float) for v in door.get("others", {}).values()]
    body_c = max(others_, key=len).mean(0) if others_ else None          # the appliance's biggest body: its cabinet
    fr = door_frame(H, D, hinge, axis, body_c)
    a, n = fr["a"], fr["n"]
    up = np.array([0.0, 0.0, 1.0])
    bar_hulls = slab_hulls(H, a, band=0.004, min_points=6)
    hpts = H[np.random.default_rng(0).choice(len(H), min(len(H), 3000), replace=False)]
    others = [np.asarray(v, float) for v in door.get("others", {}).values()]
    # the door without its handle: the face the fingers must not reach first
    from scipy.spatial import cKDTree
    Dn = D[cKDTree(H).query(D)[0] > 0.004]
    door_hulls = slab_hulls(Dn, up, band=0.01, min_points=8)
    other_hulls = [h for P in others for h in body_hulls(P)]
    everything = np.vstack([H, Dn] + others)
    palm = hand.palm_pts()
    palm_eq = ConvexHull(palm).equations
    open_hand = np.array([0.1, 0.1, 0.1, 0.1, 0.0, float(opts.get("thumb_rot", 0.0))])
    # seated as the open palm AND fingers meet the bar: the fingers lie across its front
    # (seated on the palm alone, the open fingers' pads went through the bar)
    flat = np.vstack([palm] + [hand.digit(d, open_hand) for d in FINGERS])
    qs = np.round(np.arange(0.1, 1.741, 0.03), 3)
    gap = float(opts.get("palm_gap_m", 0.004))
    out, why = [], {}

    def first_contact(d, R, o, hulls, pts=None):
        k = {"index": 0, "middle": 1, "ring": 2, "pinky": 3}[d]

        def hit(qv):
            qh = open_hand.copy(); qh[k] = qv
            if inside(o + hand.digit(d, qh) @ R.T, hulls, 0.0005).any():
                return True
            return pts is not None and tool_in_hulls((pts - o) @ R, hand.digit_hulls(d, qh))
        if not hit(qs[-1]):
            return None
        lo_i, hi_i = 0, len(qs) - 1
        if hit(qs[0]):
            return float(qs[0])
        while hi_i - lo_i > 1:
            mid = (lo_i + hi_i) // 2
            lo_i, hi_i = (lo_i, mid) if hit(qs[mid]) else (mid, hi_i)
        return float(qs[hi_i])

    span = fr["span"]
    heights = opts.get("heights_m") or np.round(np.arange(span[0] + 0.03, span[1] - 0.03 + 1e-9, 0.02), 3).tolist()
    for x_sign in (1.0, -1.0):
        for yaw in np.radians(opts.get("yaw_deg", [-15, -7.5, 0, 7.5, 15])):
            R = grip_rotation(a, n, x_sign, yaw)
            for h in heights:
                for depth in opts.get("depth_m", [0.07, 0.08, 0.09, 0.10, 0.11]):
                    key = f"x{x_sign:+.0f} yaw {np.degrees(yaw):+.1f}"
                    # the bar `depth` out along the fingers, then the palm seated onto
                    # it along its normal from clear in front, `gap` short of contact
                    o0 = fr["c"] + a * h - R[:, 2] * depth
                    far = o0 - R[:, 1] * 0.08
                    rel = (everything - far) @ R
                    rel = rel[(rel[:, 0] > flat[:, 0].min() - 0.005) & (rel[:, 0] < flat[:, 0].max() + 0.005)
                              & (rel[:, 2] > flat[:, 2].min() - 0.005) & (rel[:, 2] < flat[:, 2].max() + 0.005)]
                    t = seat_along(flat, rel, [0.0, -1.0, 0.0]) if len(rel) else None
                    if t is None:
                        why["no palm before the bar"] = why.get("no palm before the bar", 0) + 1
                        continue
                    o = far + R[:, 1] * (t - gap)
                    W_open = np.vstack([o + palm @ R.T] + [o + hand.digit(d, open_hand) @ R.T for d in ("index", "middle", "ring", "pinky", "thumb")])
                    if inside(W_open, bar_hulls + door_hulls + other_hulls, 0.0015).any() or \
                            inside((everything - o) @ R, [(palm_eq, palm.min(0), palm.max(0))], 0.0015).any():
                        why["open hand in the door or bar"] = why.get("open hand in the door or bar", 0) + 1
                        continue
                    wrap, bad = {}, None
                    for d in FINGERS:
                        q_bar = first_contact(d, R, o, bar_hulls, hpts)
                        q_door = first_contact(d, R, o, door_hulls + other_hulls)
                        if q_bar is not None and (q_door is None or q_door > q_bar + 0.06) and q_bar <= float(opts.get("wrap_max_rad", 1.6)):
                            wrap[d] = q_bar
                        elif q_door is not None and (q_bar is None or q_door <= q_bar + 0.06) and d != "pinky":
                            bad = d                                      # the pinky, past the bar's span, may rest on the door
                    if bad:
                        why[f"{bad} meets the door first"] = why.get(f"{bad} meets the door first", 0) + 1
                        continue
                    if len(wrap) < int(opts.get("min_fingers", 3)) or not all(f in wrap for f in ("index", "middle", "ring")):
                        why["too few fingers round the bar"] = why.get("too few fingers round the bar", 0) + 1
                        continue
                    score = sum(abs(v - 1.1) for v in wrap.values()) / len(wrap) + 0.3 * abs(yaw) + 2.0 * abs(h) / max(1e-6, np.ptp(span)) \
                        - 0.1 * len(wrap)
                    out.append(dict(R=R, o=o, x_sign=x_sign, yaw_deg=round(float(np.degrees(yaw)), 1), height_m=h, depth_m=depth,
                                    wrap=wrap, score=round(float(score), 4), key=key))
    out.sort(key=lambda c_: c_["score"])
    return out, {"rejected": why, "frame": {k: (np.round(v, 4).tolist() if isinstance(v, np.ndarray) else v) for k, v in fr.items()}}


def plan_door_open(req, g, checker, start, lower, upper, action_step, slide_bounds, make_checker):
    """The whole plan from `start` (14)."""
    from scipy.spatial.transform import Rotation

    from aida_r5_motion import joint_path, screen
    from pinch_place_planner import CACHE
    from tool_grip_planner import HandModel, _link_points, inside, slab_hulls

    task = req["task"]
    door = task["door"]
    opts = task.get("door_open", {})
    hand = HandModel(g, CACHE)
    push_mode = opts.get("contact") == "push_inner"
    cands, info = (search_pushes if push_mode else search_grips)(hand, door, opts)
    if not cands:
        return {"passed": False, "reason": ("no push pose: no flat hand fits behind the door clear of the cabinet" if push_mode else
                                            "no bar grip: no hand pose wrapped three fingers round the bar before the door"), "search": info}
    others_ = [np.asarray(v, float) for v in door.get("others", {}).values()]
    fr = door_frame(door["handle_points"], door["door_points"], door["hinge"], door["axis"],
                    max(others_, key=len).mean(0) if others_ else None)
    hinge, axis = np.asarray(door["hinge"], float), np.asarray(door["axis"], float)
    sense = fr["sense"]
    D = np.asarray(door["door_points"], float)
    D = D[np.random.default_rng(2).choice(len(D), min(len(D), 4000), replace=False)]
    up = np.array([0.0, 0.0, 1.0])
    # the appliance's other bodies (its cabinet, the other door) as slab boxes for the checker
    boxes = []
    for name, P in door.get("others", {}).items():
        boxes += body_boxes(P, name, float(opts.get("body_slab_m", 0.02)))
    # ... in place of the scene's one box a body (the request's "<object>:<body>"): the
    # cabinet's box held the control panel's front across the door's recess (2026-10-16)
    obj_prefix = str(opts.get("object_name", "")) + ":"
    saved = list(req["obstacles"])
    if obj_prefix != ":":
        req["obstacles"][:] = [o_ for o_ in saved if not str(o_.get("id", "")).startswith(obj_prefix)]
    # the door as it stands, for the reach: slabs too when it stands ajar (one box about a popped
    # door filled the gap behind it, where a push from inside starts: 2026-10-16)
    door_now = (body_boxes(D, "door_now", float(opts.get("body_slab_m", 0.02)), pad=float(opts.get("door_slab_pad_m", 0.002))) if push_mode
                else [dict(id="door:closed", min=(D.min(0) - 0.005).tolist(), max=(D.max(0) + 0.005).tolist())])
    reach_checker = make_checker(boxes + door_now)
    pull_checker = make_checker(boxes)
    req["obstacles"][:] = saved
    arm_pts = _link_points(CACHE, [f"amber_left_arm_link_{k}" for k in (4, 5, 6, 7)], per_link=300)
    step = np.asarray(action_step, float)

    def arm_clear_of_door(q, ang):
        """The forearm and upper arm (not the hand: it holds the handle) out of the door swung to `ang`."""
        Dw = swing(D, hinge, axis, sense * ang)
        hulls = slab_hulls(Dw, up, band=0.02, min_points=8)
        g.forward(q)
        for ln, P in arm_pts.items():
            p, R = g.pose(ln)
            if inside(p + P @ R.T, hulls, 0.002).any():
                return False
        return True

    seeds = [np.asarray(start, float)]
    rng = np.random.default_rng(int(opts.get("ik_seed", 20261016)))
    lo_b, hi_b = np.asarray(g.bounds, float)[:, 0], np.asarray(g.bounds, float)[:, 1]
    for k in range(int(opts.get("ik_seeds", 32))):
        b = seeds[0].copy()
        b[:8] = np.clip(b[:8] + rng.normal(0.0, 0.3 + 0.05 * k, 8), lo_b, hi_b)
        seeds.append(b)
    open_deg = float(opts.get("open_deg", 40.0))
    min_deg = float(opts.get("min_open_deg", 25.0))
    # open_deg and min_open_deg are the door's angles, not the push's: a door already ajar
    # (a microwave's, 19.8 deg after its pop) needs only the rest - asked for 45 deg MORE, every
    # push that took it to 36-43 deg failed (2026-10-16)
    ang_now = float(door.get("angle_deg", 0.0))
    open_deg, min_deg = push_needed(open_deg, min_deg, ang_now, float(opts.get("min_push_deg", 10.0)))
    tried = []
    rng_r = np.random.default_rng(int(opts.get("ik_seed", 20261016)) + 1)

    def repose(q0, ang0, ang1):
        """A posture walk from q0 (the hand at the door's angle ang0) to a posture that holds
        the hand at ang1, clear: seeds about q0, the nearest clear solution first, then a
        walk of IK solves from seeds moved a step at a time toward it, each holding the
        hand at ang0 and clear, no joint moving more than a tick's step x 4 between solves.
        None when there is none."""
        R0_, o0_ = rot_about(axis, sense * ang0) @ R_cur[0], swing(o_cur[0], hinge, axis, sense * ang0)[0]
        R1_, o1_ = rot_about(axis, sense * ang1) @ R_cur[0], swing(o_cur[0], hinge, axis, sense * ang1)[0]
        goals = []
        for k in range(int(opts.get("repose_seeds", 24))):
            sd = q0.copy()
            sd[:8] = np.clip(sd[:8] + rng_r.normal(0.0, 0.2 + 0.1 * (k % 4), 8), lo_b, hi_b)
            q1, f1 = g.solve(sd, o1_, R1_)
            if f1["passed"] and pull_checker(q1) and arm_clear_of_door(q1, ang1):
                goals.append(q1)
        for q1 in sorted(goals, key=lambda q_: float(np.abs(q_[:8] - q0[:8]).max())):
            seq = posture_walk(q0, q1, ang0, (o0_, R0_))
            if seq is not None:
                return seq[:-1] + [q1]
        return None

    def posture_walk(q0, q1, ang0, pose=None):
        """From posture q0 to q1 with the hand held where it is at the door's angle ang0: IK
        solves from seeds moved a step at a time from q0 to q1, each clear, no joint moving
        more than 6 ticks' steps between solves. The list of postures (ending at q1's
        neighbour), or None."""
        o0_, R0_ = pose if pose else (swing(o_cur[0], hinge, axis, sense * ang0)[0], rot_about(axis, sense * ang0) @ R_cur[0])
        jump = float(np.abs(q1[:8] - q0[:8]).max())
        m = max(1, int(np.ceil(jump / (4 * float(step[1:8].max())))))
        seq, prev = [], q0
        for f in np.linspace(0, 1, m + 1)[1:]:
            qi, fi = g.solve(q0 * (1 - f) + q1 * f, o0_, R0_)
            if not (fi["passed"] and pull_checker(qi) and arm_clear_of_door(qi, ang0)) or \
                    float(np.abs(qi[:8] - prev[:8]).max()) > 6 * float(step[1:8].max()):
                return None
            seq.append(qi); prev = qi
        return seq
    R_cur, o_cur = [None], [None]

    def back_off(q0, hand_to):
        """When the start, or the hand reshaped there for the push, is not clear of the door
        as it stands (a microwave door's kick leaves its free edge against the thumb that
        pressed the release, and the uncurling ring finger met it, 2026-10-16): the hand
        drawn straight away, orientation held, until both are - out toward the room, up, or
        both, at most `back_off_max_m`. The postures, [] when the start is clear, None when
        no direction clears it."""
        def clear(q_):
            # the whole reshape, tick by tick as it will be walked: a curling finger reaches out
            # furthest mid-curl (half way was clear, 57 % was not), and the turning thumb clipped
            # the door between two tenths (2026-10-16)
            m_ = max(1, int(np.ceil(np.max(np.abs(hand_to - q_[8:]) / step[8:]))))
            return all(reach_checker(np.r_[q_[:8], q_[8:] * (1 - f) + hand_to * f]) for f in np.linspace(0.0, 1.0, m_ + 1))
        if clear(q0):
            return []
        g.forward(q0)
        p0, Rh = g.pose()
        n0 = fr["n"]
        for d_ in (n0, (n0 + up) / np.linalg.norm(n0 + up), up):
            seq, q_ = [], q0.copy()
            for k in range(1, int(float(opts.get("back_off_max_m", 0.08)) / 0.005) + 1):
                q_, fit = g.solve(q_, p0 + d_ * 0.005 * k, Rh)
                if not (fit["passed"] and pull_checker(q_)):
                    break
                seq.append(q_.copy())
                if clear(q_):
                    return seq
        return None
    for cand in cands[:int(opts.get("max_candidates", 12))]:
        R, o = cand["R"], cand["o"]
        R_cur[0], o_cur[0] = R, o
        hand_open = np.asarray(cand["hand_q"], float) if push_mode else np.array([0.1] * 4 + [0.0, float(opts.get("thumb_rot", 0.0))])
        q_grip, why = None, {"ik": 0, "collision": 0}
        for s in seeds:
            q_, fit = g.solve(np.r_[s[:8], hand_open], o, R)
            if not fit["passed"]:
                why["ik"] += 1
                err = (round(fit.get("position_error_m", 0) * 1000), round(float(np.degrees(fit.get("angle_error_rad", 0.0)))))
                why["closest"] = min(why.get("closest", err), err)
                continue
            if not (pull_checker(q_) and arm_clear_of_door(q_, 0.0)):
                why["collision"] += 1
                pairs = pull_checker(q_, True)
                hit = hit_names(pairs, 1) if pairs else "the arm in the door"
                why["hit"] = why.get("hit", {}); why["hit"][hit] = why["hit"].get(hit, 0) + 1
                continue
            q_grip = q_
            break
        if q_grip is None:
            tried.append({"candidate": cand["score"], "key": cand.get("key"), "at": [cand.get("height_m"), cand.get("depth_m"), cand.get("yaw_deg")],
                          "rejected": f"no IK {'behind the door' if push_mode else 'at the bar'}: {why}"})
            continue
        # the pull: the grip carried round the hinge, a degree at a time
        pull, ang = [q_grip.copy()], 0.0
        closed_hand = None
        reposed = 0

        def why_not(q_, fit, ang_):
            if not fit["passed"]:
                return f"IK ({fit.get('position_error_m', 0) * 1000:.0f} mm, {np.degrees(fit.get('angle_error_rad', 0.0)):.0f} deg off)"
            if not pull_checker(q_):
                return "collision: " + hit_names(pull_checker(q_, True))
            if not arm_clear_of_door(q_, ang_):
                return "the arm meets the swinging door"
            return None

        while ang < np.radians(open_deg) - 1e-9:
            nxt = min(ang + np.radians(1.0), np.radians(open_deg))
            Rn = rot_about(axis, sense * nxt) @ R
            on = swing(o, hinge, axis, sense * nxt)[0]
            q_, fit = g.solve(pull[-1], on, Rn)
            stop = why_not(q_, fit, nxt)
            if stop and opts.get("pull_repose", True):
                # the local IK walked the arm into the body: another posture of the arm for the
                # same hand (8 joints for a 6-dof pose), reached by a walk of IK solves that
                # holds the hand where it is (2026-10-16: the pushing forearm met the torso at 19 deg)
                walk_ = repose(pull[-1], ang, nxt)
                if walk_ is not None:
                    pull += walk_[:-1]
                    q_, stop = walk_[-1], None
                    reposed += 1
            if stop:
                break
            pull.append(q_); ang = nxt
        if np.degrees(ang) < min_deg - 1e-6:
            tried.append({"candidate": cand["score"], "rejected": f"the pull stops at {np.degrees(ang):.0f} deg: {stop}"})
            continue
        # staging in front of the bar, the hand open - or, pushing from inside,
        # beyond the free edge: the hand slides into the gap along the door
        if push_mode:
            # into the gap: from beyond the free edge (the full slide, or shorter ones - the door
            # is slabs for the reach), from above, or both; drawing the hand back toward the
            # robot's midline folded the arm into the torso on every pose (2026-10-16)
            ways = []
            for name_, d_, b0 in cand["approaches"]:
                bs = [b0] + ([b_ for b_ in opts.get("push_backs_m", [0.08, 0.05, 0.03, 0.0]) if b_ < b0] if name_ == "beyond the edge" else [])
                ways += [(name_, np.asarray(d_, float), b_) for b_ in bs]
        else:
            ways = [("in front", R[:, 1], float(task.get("approach_m", 0.06)))]
        staged, what, ways_why = None, None, {}
        # pushing from inside, staging stands off the inner face too (the slabs' 5 mm pad
        # took the fingertips 4 mm off it, 2026-10-16); the slide closes it
        n_ = fr["n"]
        offs = [n_ * float(v) for v in opts.get("push_standoff_m", [0.02, 0.015, 0.01])] if push_mode else [np.zeros(3)]
        for (way, adir, back), off in ((w_, f_) for w_ in ways for f_ in offs):
            o_st = o - adir * back - off
            # the staging posture from the grip's, else from seeds about it (the nearest first):
            # drawing the hand toward the midline folded the arm into the torso (2026-10-16)
            sols = [g.solve(q_grip, o_st, R)]
            if push_mode:
                for k in range(int(opts.get("staging_seeds", 12))):
                    sd = q_grip.copy(); sd[:8] = np.clip(sd[:8] + rng.normal(0.0, 0.2 + 0.1 * (k % 3), 8), lo_b, hi_b)
                    sols.append(g.solve(sd, o_st, R))
            for q_st, fit in sorted(sols, key=lambda qf: float(np.abs(qf[0][:8] - q_grip[:8]).max())):
                if not fit["passed"]:
                    ways_why.setdefault(f"{way} {back * 100:.0f}", f"IK {fit.get('position_error_m', 0) * 1000:.0f} mm off")
                    what = what or ways_why[f"{way} {back * 100:.0f}"]
                    continue
                if not reach_checker(q_st):
                    what = "collision: " + hit_names(reach_checker(q_st, True), 2)
                    ways_why[f"{way} {back * 100:.0f}"] = what
                    continue
                # the slide from there onto the door, the hand on its line all the way
                line, ok, q_ = [], True, q_st.copy()
                nst = max(1, int(np.ceil(back / 0.005)))
                for f in np.linspace(0, 1, nst + 1)[1:]:
                    q_, fit2 = g.solve(q_, o_st * (1 - f) + o * f, R)
                    if not (fit2["passed"] and pull_checker(q_)):
                        ok = False
                        break
                    line.append(q_.copy())
                if not ok:
                    what = (f"the slide {'in behind the door' if push_mode else 'onto the bar'} is blocked at {f:.2f}: "
                            + (f"IK {fit2.get('position_error_m', 0) * 1000:.0f} mm off" if not fit2["passed"] else hit_names(pull_checker(q_, True), 2)))
                    ways_why[f"{way} {back * 100:.0f}"] = what
                    continue
                if float(np.abs(line[-1][:8] - q_grip[:8]).max()) > 0.05:
                    # the slide ended in another posture than the push's: walk to it, the hand held
                    walk_ = posture_walk(line[-1], q_grip, 0.0) if push_mode else None
                    if walk_ is None:
                        what = "the slide ends in a posture the push cannot start from"
                        continue
                    line = line + walk_
                staged = (back, o_st, q_st, line, way)
                break
            if staged:
                break
        if not staged:
            tried.append({"candidate": cand["score"], "rejected": "no clear staging pose " + ("into the gap" if push_mode else "in front of the bar")
                          + f" ({what})", "ways": ways_why})
            continue
        back, o_st, q_st, line, way = staged
        path, stages = [np.asarray(start, float)], ["start"]

        def walk(goal, label, hand_free=False, scale=1.0):
            q0 = path[-1].copy()
            s_ = step * scale
            if hand_free:
                s_ = s_.copy(); s_[8:] = np.inf
            m = int(np.ceil(np.max(np.abs(goal - q0) / s_))) if np.any(goal != q0) else 0
            m = max(m, 1) if hand_free else m
            for f_ in np.linspace(0, 1, m + 1)[1:]:
                q1 = q0 * (1 - f_) + goal * f_
                if hand_free:
                    q1[8:] = goal[8:]
                path.append(q1); stages.append(label)

        def hold(sec, label):
            for _ in range(int(sec * HZ)):
                path.append(path[-1].copy()); stages.append(label)

        backed = back_off(np.asarray(start, float), hand_open)
        if backed is None:
            tried.append({"candidate": cand["score"], "rejected": "the start (or the hand reshaped there) is in the door as it stands and no straight move clears it"})
            continue
        for q_b in backed:
            walk(np.r_[q_b[:8], path[-1][8:]], "back_off", scale=0.5)
        walk(np.r_[path[-1][:8], hand_open], "preshape")
        try:
            dense, rinfo = joint_path(reach_checker, path[-1][:8], q_st[:8], hand_open, timeout=float(task.get("rrt_timeout_s", 120)))
        except Exception as e:                                       # noqa: BLE001 - the RRT's own failure, recorded
            tried.append({"candidate": cand["score"], "rejected": f"reach: {e}"})
            continue
        for qa in dense:
            walk(np.r_[np.asarray(qa, float)[:8], hand_open], "reach")
        walk(np.r_[q_st[:8], hand_open], "reach")
        for q_l in line:
            walk(np.r_[q_l[:8], hand_open], "approach", scale=float(opts.get("approach_scale", 0.35)))
        # the close: the four fingers commanded shut, the firmware stops each on the bar -
        # or, pushing from inside, the hand kept as it is (flat on the door)
        closed_hand = hand_open.copy()
        if not push_mode:
            closed_hand[:4] = 1.74
        walk(np.r_[path[-1][:8], closed_hand], "close", hand_free=True)
        hold(float(task.get("close_s", 1.0)), "close")
        # the pull: slowly (the gasket lets go at once and the door would run on)
        for q_p in pull[1:]:
            walk(np.r_[q_p[:8], closed_hand], "pull", scale=float(opts.get("pull_scale", 0.3)))
        hold(0.5, "pulled")
        walk(np.r_[path[-1][:8], hand_open], "release", hand_free=True)
        hold(0.5, "release")
        # back off from the door, along its swung normal
        Rn = rot_about(axis, sense * ang) @ R
        on = swing(o, hinge, axis, sense * ang)[0]
        q_r, ok_r = path[-1].copy(), True
        for k in range(1, 11):
            q_r, fit = g.solve(q_r, on - Rn[:, 1] * 0.005 * k, Rn)
            if not fit["passed"]:
                ok_r = False
                break
            walk(np.r_[q_r[:8], hand_open], "retreat", scale=0.5)
        hold(float(task.get("hold_s", 1.0)), "hold")
        arr = np.asarray(path)
        sc = screen(reach_checker, [q for q, s_ in zip(arr, stages) if s_ in ("preshape", "reach")])
        if not sc["passed"]:
            tried.append({"candidate": cand["score"], "rejected": f"screen: {sc['collisions'][:1]}"})
            continue
        g.forward(q_grip)
        pb, Rb = g.pose()
        return {"passed": True, "targets": arr, "stages": stages,
                "grip": {k: (np.round(v, 4).tolist() if isinstance(v, np.ndarray) else v) for k, v in cand.items() if k not in ("R", "o", "approaches")},
                "palm": {"p": pb.round(4).tolist(), "q_xyzw": Rotation.from_matrix(Rb).as_quat().round(5).tolist()},
                "pull_deg": round(float(np.degrees(ang)), 1), "door_angle_before_deg": round(ang_now, 1), "reposed": reposed, "approach": way, "backed_off_ticks": len(backed), "retreat_ok": ok_r, "rrt": rinfo, "candidates": len(cands),
                "tried": tried, "search": info, "screen": {k: sc[k] for k in ("passed", "waypoints")}}
    return {"passed": False, "reason": f"{len(cands)} {'push poses' if push_mode else 'bar grips'}, none planned", "tried": tried[:int(opts.get("report_tried", 12))], "search": info,
            "best": [{k: (np.round(v, 4).tolist() if isinstance(v, np.ndarray) else v) for k, v in c_.items() if k not in ("R", "o", "approaches")} for c_ in cands[:3]]}
