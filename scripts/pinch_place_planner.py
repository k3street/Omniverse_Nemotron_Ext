#!/usr/bin/env python3
"""Plan a pinch pick-and-place for the R5's left Ability Hand (HomeHero's
venv: pinocchio, coal, scipy). Called by homehero_grasp_adapter.py for a
request whose task is "pinch_place" - a chess move, say: pick the piece up
between index and thumb and set it down on a square.

HomeHero's R5 grasp planner wraps the hand round an object (cans, mugs); a
pawn among its neighbours is a pinch. This plans one with HomeHero's own
geometry and checking - pinocchio kinematics and IK (prepare_aida_r5_transfer
.Geometry), the coal collision checker against the support, the board and
every other piece (aida_r5_motion.Checker), its RRT for free motion
(joint_path), its per-tick action limits and its slide headroom:

  pinch    the index and thumb configuration whose fingertips close on each
           other across the object's width (opposition of their closing
           directions along the line between them), the other fingers curled
           out of the way; and an open preshape a clearance wider
  grasp    hand orientations about the object's grip point (the pinch line
           level, every heading either way round, every roll about it), each solved by IK,
           kept if it and a pre-grasp straight above it are collision-free,
           the least arm motion from the start chosen
  path     reach (RRT) - descend (straight, IK per 5 mm) - close (the hand's
           grip command: index and thumb driven closed, stopped on contact by
           its firmware) - lift (the shoulder slide, HomeHero's lift rule) -
           carry (straight, IK per 5 mm, hand closed) - lower (slide) -
           release (index and thumb back to the preshape) - retreat (slide)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

HH = Path("/home/kimate/Documents/Github/HomeHero")
CACHE = HH / "artifacts/isaac/aida_r5_20261004/physics_v7_joint_filters/planning_collision"
HZ = 30


def _tip(link):
    man = json.loads(CACHE.with_suffix(".json").read_text())
    pts = np.load(CACHE.with_suffix(".npz"))
    cloud = np.concatenate([pts[f"p{i}"] for i, s in enumerate(man["shapes"]) if s["link"] == link])
    return cloud[np.argmax(np.linalg.norm(cloud, axis=1))]          # the fingertip: farthest from the joint


def pinch_configuration(g, width_m, curl=1.5, clearance_m=0.02):
    """Index and thumb flexion and thumb rotation that close their tips on
    each other across width_m, best opposed; and the preshape that opens
    them clearance_m either side. Returned with the pinch's midpoint and
    line in the palm frame."""
    from prepare_aida_r5_transfer import PALM
    tip_i, tip_t = _tip("psyonic_left_index_L2"), _tip("psyonic_left_thumb_L2")
    fid = {n: g.model.getFrameId(n) for n in (PALM, "psyonic_left_index_L2", "psyonic_left_thumb_L2")}
    arm0 = np.zeros(8)

    def tips(h):
        g.forward(np.r_[arm0, h])
        T = g.data.oMf[fid[PALM]]
        return (T.actInv(g.data.oMf[fid["psyonic_left_index_L2"]].act(tip_i)),
                T.actInv(g.data.oMf[fid["psyonic_left_thumb_L2"]].act(tip_t)))

    want = width_m + 0.004               # the tip points are the fingertips' outer surface
    best = None
    for qi in np.linspace(0.4, 1.3, 19):
        for qf in np.linspace(0.3, 1.3, 21):
            for qr in np.linspace(-1.74, -0.5, 11):
                h = np.array([qi, curl, curl, curl, qf, qr])
                pi, pt = tips(h)
                d = float(np.linalg.norm(pt - pi))
                if abs(d - want) > 0.004:
                    continue
                a = (pt - pi) / d
                pi2, _ = tips(h + [0.01, 0, 0, 0, 0, 0])
                _, pt2 = tips(h + [0, 0, 0, 0, 0.01, 0])
                vi, vt = pi2 - pi, pt2 - pt
                opp = min(vi @ a / np.linalg.norm(vi), -(vt @ a) / np.linalg.norm(vt))
                if best is None or opp > best[0]:
                    best = (float(opp), h, (pi + pt) / 2, a, d)
    if best is None:
        raise ValueError(f"no index-thumb pinch closes across {width_m:.4f} m")
    opp, hc, mid, axis, d = best
    ho = hc.copy()
    for delta in np.arange(0.02, 1.0, 0.02):              # both digits back along their arcs together
        ho = hc.copy()
        ho[0] = max(0.0, hc[0] - delta)
        ho[4] = max(0.0, hc[4] - delta)
        pi, pt = tips(ho)
        if np.linalg.norm(pt - pi) >= width_m + 2 * clearance_m:
            break
    pi, pt = tips(ho)
    # each digit's closing direction at the closed configuration, in the palm
    # frame: the fingertip's motion per radian of further closing
    pi2, _ = tips(hc + [0.01, 0, 0, 0, 0, 0])
    _, pt2 = tips(hc + [0, 0, 0, 0, 0.01, 0])
    pic, ptc = tips(hc)
    closing = {"index": (pi2 - pic) / 0.01, "thumb": (pt2 - ptc) / 0.01}
    return {"closed": hc, "open": ho, "midpoint_palm": mid, "axis_palm": axis, "opposition": opp, "closing_palm": closing,
            "closed_separation_m": d, "open_separation_m": float(np.linalg.norm(pt - pi)),
            "open_midpoint_palm": (pi + pt) / 2}


def _rotation_taking(a_from, b_from, a_to, b_to):
    def frame(a, b):
        a = a / np.linalg.norm(a)
        b = b - (b @ a) * a
        b /= np.linalg.norm(b)
        return np.column_stack((a, b, np.cross(a, b)))
    return frame(a_to, b_to) @ frame(a_from, b_from).T


def plan_pinch_place(req, g, checker, start, lower, upper, action_step, slide_bounds):
    from aida_r5_motion import joint_path, screen

    task = req["task"]
    step = np.asarray(action_step, float)
    grip_point = np.asarray(task["grip_point"], float)
    place_point = np.asarray(task["place_point"], float)      # where the grip point is to be, set down
    pinch = pinch_configuration(g, float(task["grip_width_m"]), float(task.get("curl_rad", 1.5)),
                                float(task.get("open_clearance_m", 0.02)))
    hc, ho = pinch["closed"], pinch["open"]
    m, a = pinch["midpoint_palm"], pinch["axis_palm"]
    b = np.cross(a, [0.0, 0.0, 1.0])
    if np.linalg.norm(b) < 1e-6:
        b = np.cross(a, [1.0, 0.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    pre_h = float(task.get("pregrasp_height_m", 0.08))
    seed = np.asarray(start, float)
    # IK starts from HomeHero's reset pose (the arm out over the table, as its
    # own planner does) and from the start: from the arms-down rest alone the
    # solver stalls 16 cm short of every pose over the board
    seeds = [np.asarray(x, float)[:8] for x in (task.get("ik_seeds") or [])] + [seed[:8]]

    # --- grasp: the pinch line level, every heading and roll -------------------------
    tried, options = 0, []
    why = {"ik": 0, "open_collides": 0, "closed_collides": 0, "pregrasp_ik": 0, "pregrasp_collides": 0}
    examples = {}
    for psi in np.radians(np.arange(0, 360, 15)):             # index-to-thumb either way round: different grasps
        a_w = np.array([np.cos(psi), np.sin(psi), 0.0])
        u, v = up, np.cross(a_w, up)
        for phi in np.radians(np.arange(0, 360, 15)):
            R = _rotation_taking(a, b, a_w, np.cos(phi) * u + np.sin(phi) * v)
            p = grip_point - R @ m
            if p[2] - grip_point[2] < 0.03:                   # the palm above the grip, not beside or under it
                continue
            tried += 1
            for sd in seeds:
                q, fit = g.solve(np.r_[sd, ho], p, R)
                if fit["passed"]:
                    break
            if not fit["passed"]:
                why["ik"] += 1
                examples.setdefault("ik", {"position_error_m": round(fit["position_error_m"], 4), "palm": p.round(3).tolist()})
                continue
            q_open, q_closed = np.r_[q[:8], ho], np.r_[q[:8], hc]
            bad = None
            if not checker(q_open):
                bad = ("open_collides", q_open)
            elif not checker(q_closed):
                bad = ("closed_collides", q_closed)
            if bad:
                why[bad[0]] += 1
                pairs = checker(bad[1], details=True)[:3]
                for a_, b_ in pairs:
                    key = f"{a_.split('/')[-2]} x {b_.split('/')[-2] if '/' in b_ else b_}"
                    examples.setdefault(bad[0], {}).setdefault(key, 0)
                    examples[bad[0]][key] += 1
                continue
            qp, fitp = g.solve(np.r_[q[:8], ho], p + pre_h * up, R)
            if not fitp["passed"]:
                why["pregrasp_ik"] += 1
                continue
            if not checker(qp):
                why["pregrasp_collides"] += 1
                continue
            # how much of each digit's closing motion is vertical in the world:
            # a pinch that closes level only squeezes; one whose tips rise as
            # they close levers the piece (measured: a 14 g pawn rolled out at
            # 61 deg as the digits crept closed during the carry)
            rise = sum(abs((R @ pinch["closing_palm"][d])[2]) / np.linalg.norm(pinch["closing_palm"][d]) for d in ("index", "thumb")) / 2
            options.append({"cost": float(np.linalg.norm(q[:8] - seed[:8])), "q": q_open, "q_pre": qp, "p": p, "R": R,
                            "rise": round(float(rise), 3),
                            "heading_deg": round(float(np.degrees(psi)), 1), "roll_deg": round(float(np.degrees(phi)), 1)})
    if not options:
        return {"passed": False, "reason": f"no reachable, collision-free pinch over {tried} hand orientations: {why}",
                "pinch": _jsonable(pinch), "rejections": why, "examples": examples}
    # level closing first (within 0.15 of the levellest), then the least arm motion
    best_rise = min(o["rise"] for o in options)
    options.sort(key=lambda o: (o["rise"] > best_rise + 0.15, o["cost"]))
    # the place: the same grip over the destination, lifted, possibly turned
    # about the vertical through the grip (the piece stays upright)
    lift = float(task.get("lift_m", 0.03))
    yaws = np.radians([0, 15, -15, 30, -30])
    feasible = []
    for o in options:
        for yw in yaws:
            Rz = Rotation.from_euler("z", yw).as_matrix()
            R2 = Rz @ o["R"]
            p2 = place_point + np.array([0, 0, lift]) - R2 @ m
            q2, fit2 = g.solve(np.r_[o["q"][:8], hc], p2, R2)
            if fit2["passed"] and checker(np.r_[q2[:8], hc]):
                feasible.append({**o, "place_yaw": float(yw)})
                break
    why["place_unreachable"] = len(options) - len(feasible)
    if not feasible:
        return {"passed": False, "reason": f"none of {len(options)} pinch grasps can be carried to the destination: {why}",
                "pinch": _jsonable(pinch), "rejections": why, "examples": examples}
    options = feasible

    last = None
    for choice in options[:8]:                                 # the cheapest whose whole path checks out
        try:
            result = _path(choice, req, g, checker, seed, hc, ho, grip_point, place_point, step, lower, upper,
                           slide_bounds, joint_path, screen, m)
        except RuntimeError as e:                              # HomeHero's RRT out of budget
            result = {"passed": False, "reason": f"reach: {e}"}
        if result["passed"]:
            result.update(pinch=_jsonable(pinch), options=len(options), tried=tried,
                          grasp={k: choice[k] for k in ("heading_deg", "roll_deg", "cost", "rise")},
                          rise_range=[round(min(o["rise"] for o in options), 3), round(max(o["rise"] for o in options), 3)])
            return result
        last = result
    return {**last, "pinch": _jsonable(pinch), "options": len(options), "tried": tried, "rejections": why}


def closure_contact(g, checker, cloud, q_arm, ho, s_max=1.74, step=0.01):
    """Close index and thumb from the preshape as the hand's firmware does
    (both at once, the other digits held) and report, from the collision
    hulls, when each first touches the object and when they touch each
    other. The firmware stops a digit on its first contact, so this is
    where the real grip lands."""
    import coal
    pts = coal.StdVec_Vec3s()
    for pnt in np.unique(np.asarray(cloud, float), axis=0):
        pts.append(pnt)
    obj = coal.Convex.convexHull(pts, True, "Qt")
    ident = coal.Transform3s()
    shapes = {d: [i for i, sh in enumerate(checker.shapes) if sh["link"] == f"psyonic_left_{d}_L2"] for d in ("index", "thumb")}
    first = {"index": None, "thumb": None, "tip_to_tip": None}
    reqc = coal.CollisionRequest()
    for sv in np.arange(ho[0], s_max + 1e-9, step):
        h = ho.copy()
        h[0], h[4] = sv, ho[4] + (sv - ho[0])
        checker.g.forward(np.r_[q_arm, h])
        poses = {d: checker.g.pose(f"psyonic_left_{d}_L2") for d in ("index", "thumb")}
        tf = {d: coal.Transform3s(poses[d][1], poses[d][0]) for d in poses}
        for d in ("index", "thumb"):
            if first[d] is None and any(coal.collide(checker.models[i], tf[d], obj, ident, reqc, coal.CollisionResult()) > 0
                                        for i in shapes[d]):
                first[d] = round(float(sv), 3)
        if first["tip_to_tip"] is None and any(
                coal.collide(checker.models[i], tf["index"], checker.models[j], tf["thumb"], reqc, coal.CollisionResult()) > 0
                for i in shapes["index"] for j in shapes["thumb"]):
            first["tip_to_tip"] = round(float(sv), 3)
        if all(v is not None for v in first.values()):
            break
    ok = (first["index"] is not None and first["thumb"] is not None
          and (first["tip_to_tip"] is None or first["tip_to_tip"] > max(first["index"], first["thumb"])))
    return ok, first


def _jsonable(d):
    return {k: (v.round(4).tolist() if isinstance(v, np.ndarray) else v) for k, v in d.items()}


def _path(choice, req, g, checker, start, hc, ho, grip_point, place_point, step, lower, upper, slide_bounds,
          joint_path, screen, m):
    task = req["task"]
    path, stages = [np.asarray(start, float)], ["start"]

    def walk(goal, label, hand_free=False, step_scale=1.0):
        q = path[-1].copy()
        s = step.copy() * step_scale
        if hand_free:
            s[8:] = np.inf                                    # the hand's firmware runs a grip command itself
        n = int(np.ceil(np.max(np.abs(goal - q) / s))) if np.any(goal != q) else 0
        if hand_free:
            n = max(n, 1)
        for f in np.linspace(0, 1, n + 1)[1:]:
            path.append(q * (1 - f) + goal * f if not hand_free else np.r_[(q * (1 - f) + goal * f)[:8], goal[8:]])
            stages.append(label)

    def hold(seconds, label):
        for _ in range(int(seconds * HZ)):
            path.append(path[-1].copy())
            stages.append(label)

    def straight(target_p, R, label, hand_seen, yaw=0.0, pivot=None):
        """The palm along a straight line, IK every 5 mm, each pose screened
        with the hand as it is (hand_seen: a grip's digits stop on the object,
        short of the closed command); the command is kept in the path. With
        yaw, the hand also turns about the vertical through pivot (in the
        palm frame), reaching yaw at the end."""
        g.forward(np.r_[path[-1][:8], hand_seen])
        p0, _ = g.pose()
        n = max(1, int(np.ceil(max(np.linalg.norm(target_p - p0), abs(yaw) * 0.05) / 0.005)))
        # a smooth profile (smoothstep over twice the steps): the palm starts
        # and stops without a jolt, at most 3/4 of the 5 mm step at mid-stroke.
        # Started at 15 cm/s in one step, a pawn pinched above its centre of
        # mass swung 17 deg and rolled out of the pads (measured)
        n2 = 2 * n
        us = np.linspace(0, 1, n2 + 1)[1:]
        fs = [u * u * (3 - 2 * u) for u in us]
        for u, f in zip(us, fs):
            # the per-tick joint step scaled with the profile's slope, so the
            # ticks slow at both ends rather than only the waypoints bunching
            scale = max(0.2, 6 * u * (1 - u))
            pf, Rf = p0 * (1 - f) + target_p * f, R
            if yaw:
                Rz = Rotation.from_euler("z", yaw * f).as_matrix()
                c = pf + R @ pivot                           # the grip point stays on the line
                Rf = Rz @ R
                pf = c - Rf @ pivot
            q, fit = g.solve(np.r_[path[-1][:8], hand_seen], pf, Rf)
            if not fit["passed"]:
                return f"{label}: IK failed at {f:.2f} of the way"
            if not checker(q):
                return f"{label}: collision at {f:.2f} of the way: {checker(q, details=True)[:2]}"
            walk(np.r_[q[:8], path[-1][8:]], label, step_scale=min(1.0, scale))
        return None

    def slide(dz, label):
        q = path[-1].copy()
        top = min(float(slide_bounds[1]), float(upper[0]))
        bottom = max(float(slide_bounds[0]), float(lower[0]))
        goal = float(np.clip(q[0] + dz, bottom, top))
        while abs(q[0] - goal) > 1e-9:
            q = q.copy()
            q[0] += float(np.clip(goal - q[0], -0.0004, 0.0004))
            path.append(q)
            stages.append(label)

    # reach: the hand to its preshape where it is, then the arm by RRT to a
    # staging pose - the pre-grasp's height and orientation, drawn back toward
    # the robot out of the pieces - and straight in from there. (Straight at
    # the pre-grasp, the RRT starved: its open fingers hang among the
    # neighbours' heads, 1.5 cm over the king, under the palm's ceiling.)
    walk(np.r_[path[-1][:8], ho], "preshape")
    goal = choice["q_pre"]
    staged = None
    reach_info = None
    toward = np.asarray(req["base_pose"][:2], float) - grip_point[:2]
    toward /= np.linalg.norm(toward)
    side = np.array([-toward[1], toward[0]])
    g.forward(goal)
    p_pre, R_pre = g.pose()

    def segment_clear(q_from, p_from, p_to, R, hand):
        """A straight palm segment, IK per 5 mm and screened, without walking it."""
        n = max(1, int(np.ceil(np.linalg.norm(p_to - p_from) / 0.005)))
        q = np.r_[q_from[:8], hand]
        for f in np.linspace(0, 1, n + 1)[1:]:
            q, fit = g.solve(q, p_from * (1 - f) + p_to * f, R)
            if not fit["passed"] or not checker(q):
                return False
        return True
    # the staging pose: drawn back from the pre-grasp along the first of four
    # directions whose straight approach is clear - toward the robot (over
    # e1 that swept the open index through the king's crown), away (over the
    # empty squares ahead), or to either side
    for d_name, d_ in (("toward", toward), ("away", -toward), ("side+", side), ("side-", -side)):
        for back in (float(task.get("staging_back_m", 0.10)), 0.07, 0.13):
            p_st = p_pre + np.r_[d_ * back, 0.0]
            q_st, f_st = g.solve(goal, p_st, R_pre)
            if f_st["passed"] and checker(q_st) and segment_clear(q_st, p_st, p_pre, R_pre, ho):
                staged = (q_st, p_pre, R_pre, back, d_name)
                goal = q_st
                break
        if staged:
            break
    legs = None
    if task.get("reach") == "cumotion":
        # NVIDIA cuMotion (Isaac Sim 6's planner, GPU graph search) for the
        # free-space reach: candidates in tens of milliseconds, each screened
        # here with HomeHero's mesh checker; the RRT below is the fallback
        from cumotion_bridge import cumotion_reach
        dense, reach_info = cumotion_reach(req, path[-1][:8], [goal[:8]], checker, ho, screen,
                                           seeds=int(task.get("cumotion_seeds", 8)))
        if dense is not None:
            legs, rrt = [dense], {"algorithm": "cumotion graph planner", **{k: v for k, v in reach_info.items() if k != "candidates"}}
    if legs is not None:
        pass
    else:
      try:
        dense, rrt = joint_path(checker, path[-1][:8], goal[:8], ho, timeout=float(task.get("rrt_timeout_s", 300)))
        legs = [dense]
      except RuntimeError:
        # HomeHero's own way out of the rest pose: by its reset arm (hand in the preshape)
        via = np.asarray(req["task"]["ik_seeds"][0], float)[:8]
        if not checker(np.r_[via, ho]):
            raise RuntimeError("no direct reach, and the reset arm collides with the hand in its preshape")
        d1, rrt = joint_path(checker, path[-1][:8], via, ho, timeout=float(task.get("rrt_timeout_s", 300)))
        d2, _ = joint_path(checker, via, goal[:8], ho, timeout=float(task.get("rrt_timeout_s", 300)))
        legs = [d1, d2]
        rrt = dict(rrt or {}, via="HomeHero reset arm")
    if reach_info is not None:
        rrt = dict(rrt or {}, cumotion=reach_info if legs is None else {k: v for k, v in reach_info.items() if k != "candidates"})
    for q in (np.asarray(x, float) for leg in legs for x in leg):
        walk(np.r_[q[:8], ho], "reach")
    walk(np.r_[goal[:8], ho], "reach")
    if staged:
        rrt = dict(rrt or {}, staging_back_m=staged[3], staging_direction=staged[4])
        err = straight(staged[1], staged[2], "approach", ho)
        if err:
            return {"passed": False, "reason": err}
    # the grasp height, settled by the closure the hand will make: lowered
    # by 2 mm steps until index and thumb both meet the piece before they
    # meet each other (measured: at the first height the tips rose 9 mm
    # along their arcs and met over the crown, the pawn untouched)
    p_grasp = np.asarray(choice["p"], float).copy()
    closure = None
    for k in range(int(task.get("closure_steps", 8))):
        q_g, fit = g.solve(np.r_[path[-1][:8], ho], p_grasp, choice["R"])
        if not fit["passed"] or not checker(q_g):
            break
        ok, first = closure_contact(g, checker, req["cloud"], q_g[:8], ho)
        closure = {"lowered_m": round(0.002 * k, 4), "first_contact": first, "ok": ok}
        if ok:
            break
        p_grasp = p_grasp - np.array([0.0, 0.0, 0.002])
    if closure is None or not closure["ok"]:
        return {"passed": False, "reason": f"no grasp height where the pinch closes on the piece: {closure}"}
    err = straight(p_grasp, choice["R"], "descend", ho)
    if err:
        return {"passed": False, "reason": err}
    # close: the grip command, index and thumb flexor driven closed; held
    grip = path[-1].copy()
    grip[8], grip[12] = 1.74, 1.74
    walk(grip, "close", hand_free=True)
    hold(float(task.get("close_s", 1.0)), "close")
    lift = float(task.get("lift_m", 0.03))
    z0 = path[-1][0]
    slide(lift, "lift")
    lifted = path[-1][0] - z0
    # carry: the palm straight across, by the move, at the lifted height
    g.forward(np.r_[path[-1][:8], hc])
    p_now, R_now = g.pose()
    target = p_now + np.r_[(place_point - grip_point)[:2], 0.0]
    err = straight(target, R_now, "carry", hc, yaw=choice.get("place_yaw", 0.0), pivot=m)
    if err:
        return {"passed": False, "reason": err}
    slide(-(lifted - float(task.get("set_down_gap_m", 0.002))), "lower")
    hold(0.3, "lower")
    rel = path[-1].copy()
    rel[8], rel[12] = ho[0], ho[4]
    walk(rel, "release", hand_free=True)
    hold(float(task.get("release_s", 0.7)), "release")
    slide(float(task.get("retreat_m", 0.06)), "retreat")
    hold(float(task.get("hold_s", 1.0)), "hold")

    arr = np.asarray(path)
    labels = np.asarray(stages)
    jumps = np.abs(np.diff(arr, axis=0))
    firm = np.isin(labels[1:], ("close", "release"))
    jumps[firm, 8:] = 0.0
    if np.any(jumps > step + 1e-8):
        i = int(np.argmax(np.max(jumps - step, axis=1)))
        return {"passed": False, "reason": f"per-tick limit exceeded at tick {i} ({labels[i + 1]})"}
    if np.any(arr < lower - 1e-8) or np.any(arr > upper + 1e-8):
        return {"passed": False, "reason": "the path leaves the joint limits"}
    # screened as the hand is: holding the piece (the pinch) from the close
    # to the release, open otherwise
    held = np.isin(labels, ("close", "lift", "carry", "lower"))
    seen = arr.copy()
    seen[held, 8:] = hc
    screened = [q for q, s in zip(seen, labels) if s in ("reach", "approach", "descend", "lift", "carry", "lower", "retreat")]
    sc = screen(checker, screened)
    if not sc["passed"]:
        return {"passed": False, "reason": f"collision in the path: {sc['collisions'][:1]}"}
    return {"passed": True, "targets": arr, "stages": labels, "closure": closure,
            "rrt": {k: v for k, v in (rrt or {}).items() if not isinstance(v, list)},
            "lifted_m": round(float(lifted), 4), "screened_ticks": len(screened)}


def survey(req, g, checker_factory, start):
    """Which pieces can be pinched where they stand, and moved where: for
    each candidate (a piece's grip and the squares it may go to), the pinch
    grasps that are reachable and collision-free (IK; open and closed hand
    against every other piece; the pre-grasp), and of those, how many reach
    each destination with the piece held. No RRT: a map to choose a move by,
    which plan_pinch_place then plans in full."""
    task = req["task"]
    pinch = pinch_configuration(g, 0.0165, float(task.get("curl_rad", 1.5)), float(task.get("open_clearance_m", 0.01)))
    rows = []
    for cand in task["survey"]:
        checker = checker_factory(cand["body_id"])
        pinch = pinch_configuration(g, cand["grip_width_m"], float(task.get("curl_rad", 1.5)),
                                    float(task.get("open_clearance_m", 0.01)))
        hc, ho, m, a = pinch["closed"], pinch["open"], pinch["midpoint_palm"], pinch["axis_palm"]
        b = np.cross(a, [0.0, 0.0, 1.0])
        up = np.array([0.0, 0.0, 1.0])
        seeds = [np.asarray(x, float)[:8] for x in (task.get("ik_seeds") or [])] + [np.asarray(start, float)[:8]]
        gp = np.asarray(cand["grip_point"], float)
        grasps = []
        for psi in np.radians(np.arange(0, 360, 30)):
            a_w = np.array([np.cos(psi), np.sin(psi), 0.0])
            for phi in np.radians(np.arange(0, 360, 30)):
                R = _rotation_taking(a, b, a_w, np.cos(phi) * up + np.sin(phi) * np.cross(a_w, up))
                p = gp - R @ m
                if p[2] - gp[2] < 0.03:
                    continue
                for sd in seeds:
                    q, fit = g.solve(np.r_[sd, ho], p, R)
                    if fit["passed"]:
                        break
                if not fit["passed"] or not checker(np.r_[q[:8], ho]) or not checker(np.r_[q[:8], hc]):
                    continue
                qp, fp = g.solve(np.r_[q[:8], ho], p + 0.05 * up, R)
                if fp["passed"] and checker(qp):
                    grasps.append((q, R))
        dests = {}
        for d in cand["destinations"]:
            ok = 0
            for q, R in grasps:
                for yw in np.radians([0, 15, -15, 30, -30]):
                    R2 = Rotation.from_euler("z", yw).as_matrix() @ R
                    p2 = np.asarray(d["place_point"], float) + np.array([0, 0, float(task.get("lift_m", 0.03))]) - R2 @ m
                    q2, f2 = g.solve(np.r_[q[:8], hc], p2, R2)
                    if f2["passed"] and checker(np.r_[q2[:8], hc]):
                        ok += 1
                        break
            dests[d["square"]] = ok
        rows.append({"square": cand["square"], "piece": cand["piece"], "grasps": len(grasps), "destinations": dests})
    return rows
