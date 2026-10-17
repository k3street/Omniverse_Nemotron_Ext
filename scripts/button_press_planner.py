#!/usr/bin/env python3
"""A push button pressed with the R5's left index - a microwave's door-release
bar - planned with HomeHero's geometry, checker and RRT (the HomeHero venv;
called by the adapter).

The button comes from the asset (plan_er_grasp.py reads it): its body's
surface, its prismatic joint's axis and travel; the press direction is the
travel inward (the way the joint's range runs). The press, as a person's:

  the index straight, pointing into the panel, its fingertip on the button's
  face at the middle; the other fingers curled and the thumb folded in, clear
  of the panel and of the door beside the button;

searched over the finger's roll about the press direction and small tilts off
it; each candidate's other digits and palm clear of the appliance (its bodies'
slab hulls), the index clear of everything but the button.

Then: an RRT reach to a staging pose in front of the button, the straight
approach to touch, the press (slowly, the travel and a little more: the pad
gives), the hold (the door's latch lets go and it pops), the release, and the
hand drawn back.
"""
from __future__ import annotations

import numpy as np

HZ = 30


def press_frame(button_pts, axis, travel_sign):
    """The button's face centre (the points furthest out against the press
    direction, their middle) and the unit press direction (inward)."""
    B = np.asarray(button_pts, float)
    u = np.asarray(axis, float) / np.linalg.norm(axis) * float(travel_sign)
    s = B @ u
    face = B[s < s.min() + 0.0015]
    return face.mean(0), u


def finger_rotation(tip_dir, u, roll, tilt=(0.0, 0.0)):
    """The palm's world rotation that points the index's distal direction
    `tip_dir` (palm frame) along the press direction u, rolled by `roll` about
    it and tilted by (a, b) radians about two axes square to it."""
    from scipy.spatial.transform import Rotation as R
    d = np.asarray(tip_dir, float) / np.linalg.norm(tip_dir)
    u = np.asarray(u, float) / np.linalg.norm(u)
    # the rotation taking d to u (shortest), then the roll about u, then the tilts
    v = np.cross(d, u)
    c = float(d @ u)
    if np.linalg.norm(v) < 1e-9:
        R0 = np.eye(3) if c > 0 else R.from_rotvec(np.pi * _perp(d)).as_matrix()
    else:
        R0 = R.from_rotvec(v / np.linalg.norm(v) * np.arccos(np.clip(c, -1, 1))).as_matrix()
    p1 = _perp(u)
    p2 = np.cross(u, p1)
    Rt = R.from_rotvec(p1 * tilt[0]).as_matrix() @ R.from_rotvec(p2 * tilt[1]).as_matrix()
    return Rt @ R.from_rotvec(u * roll).as_matrix() @ R0


def _perp(v):
    v = np.asarray(v, float)
    a = np.array([1.0, 0.0, 0.0]) if abs(v[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    p = np.cross(v, a)
    return p / np.linalg.norm(p)


def press_depth(travel_m, opts):
    """How far the finger pushes a button: opts press_m when set (a release
    button pressed to its trip and a margin), else the full travel and
    overtravel_m (2 mm)."""
    return float(opts["press_m"]) if opts.get("press_m") else float(travel_m) + float(opts.get("overtravel_m", 0.002))


def plan_button_press(req, g, checker, start, lower, upper, action_step, slide_bounds, make_checker):
    """The whole plan from `start` (14)."""
    from scipy.spatial.transform import Rotation

    from aida_r5_motion import joint_path, screen
    from pinch_place_planner import CACHE, _tip
    from tool_grip_planner import HandModel, inside, slab_hulls

    task = req["task"]
    bt = task["button"]
    opts = task.get("button_press", {})
    hand = HandModel(g, CACHE)
    face, u = press_frame(bt["button_points"], bt["axis"], bt["travel_sign"])
    travel = float(bt["travel_m"])
    # how far the finger pushes: to a set depth (a release button: its trip and a margin - a person
    # stops when the latch lets go), else past the full travel by overtravel_m; pressed past where the
    # pad meets the bezel the index loaded the microwave's panel with 15 N (2026-10-16)
    press = press_depth(travel, opts)
    up = np.array([0.0, 0.0, 1.0])
    others = {k: np.asarray(v, float) for k, v in bt["others"].items()}
    hulls = [h for P in others.values() for h in slab_hulls(P, up, band=0.01, min_points=8)]
    # the hand: index straight, the others curled, the thumb folded across the palm's base
    q_hand = np.array([float(opts.get("index_rad", 0.15)), 1.6, 1.6, 1.6, float(opts.get("thumb_flex", 0.6)), float(opts.get("thumb_rot", -1.2))])
    tip_l = _tip("psyonic_left_index_L2")
    g.forward(np.r_[hand.arm, q_hand])
    pb, Rb = g.pose(hand.palm)
    p2, R2 = g.pose("psyonic_left_index_L2")
    tip_palm = Rb.T @ (p2 + R2 @ tip_l - pb)                       # the fingertip in the palm frame
    l1, _ = g.pose("psyonic_left_index_L1")
    tip_dir = Rb.T @ ((p2 + R2 @ tip_l) - l1)                       # the finger's line, knuckle to tip
    other_digits = ("middle", "ring", "pinky", "thumb")
    boxes = [dict(id=f"app:{k}", min=(P.min(0) - 0.005).tolist(), max=(P.max(0) + 0.005).tolist()) for k, P in others.items()]
    reach_checker = make_checker(boxes)
    # near the panel the checker holds only the room: the hand is checked against the
    # appliance's hulls itself (the index ON the button read as a collision with it)
    obj_prefix = str(opts.get("object_name", "")) + ":"
    saved = list(req["obstacles"])
    req["obstacles"][:] = [o_ for o_ in saved if not str(o_.get("id", "")).startswith(obj_prefix)] if obj_prefix != ":" else saved
    near_checker = make_checker([])
    req["obstacles"][:] = saved
    arm_pts = None
    cands, why = [], {}
    for roll in np.radians(opts.get("roll_deg", list(range(0, 360, 30)))):
        for ta in np.radians(opts.get("tilt_deg", [-40, -25, -10, 0, 10, 25, 40])):
            for tb in np.radians(opts.get("tilt_deg", [-40, -25, -10, 0, 10, 25, 40])):
                R = finger_rotation(tip_dir, u, roll, (ta, tb))
                o_touch = face - R @ tip_palm
                o_press = o_touch + u * press
                W = {d: o_press + hand.digit(d, q_hand) @ R.T for d in other_digits}
                W["palm"] = o_press + hand.palm_pts() @ R.T
                if any(inside(P, hulls, 0.001).any() for P in W.values()):
                    why["a digit or the palm in the appliance"] = why.get("a digit or the palm in the appliance", 0) + 1
                    continue
                # the index itself: only its tip region may be inside (the button pressed in)
                Wi = o_press + hand.digit("index", q_hand) @ R.T
                deep = Wi[(Wi - face) @ u > 0.0]
                far = deep[np.linalg.norm((deep - face) - np.outer((deep - face) @ u, u), axis=1) > 0.012] if len(deep) else deep
                if len(far):
                    why["the index beside the button, into the panel"] = why.get("the index beside the button, into the panel", 0) + 1
                    continue
                cands.append(dict(R=R, o_touch=o_touch, o_press=o_press, roll_deg=round(float(np.degrees(roll)), 1),
                                  tilt_deg=[round(float(np.degrees(ta)), 1), round(float(np.degrees(tb)), 1)],
                                  score=abs(ta) + abs(tb)))
    # the door the button releases swings out by itself: the hand - pressing, and drawn back
    # after - stays out of its first `sweep_deg` (the thumb in front of a microwave's free edge
    # held its kick back: the door stopped at 9.6 deg, a fingers-wide slot, 2026-10-16)
    sw = bt.get("door_swing")
    if sw and cands:
        from door_open_planner import cabinet_of, door_frame, swing
        D_ = np.asarray(sw["door_points"], float)
        hinge_, axis_ = np.asarray(sw["hinge"], float), np.asarray(sw["axis"], float)
        # the appliance's body: its biggest body that is not the door (the door's own points
        # outnumbered the cabinet's, read as the cabinet and swung the door INTO it)
        cab = cabinet_of(others, sw.get("door_body"))
        sense_ = door_frame(sw["handle_points"], D_, hinge_, axis_, cab.mean(0))["sense"]
        swept = np.vstack([swing(D_, hinge_, axis_, sense_ * np.radians(a_))
                           for a_ in np.arange(2.0, float(sw.get("sweep_deg", 25.0)) + 1e-9, 2.0)])
        sweep_hulls = slab_hulls(swept, up, band=0.01, min_points=8)
        back_ = float(task.get("approach_m", 0.06))
        kept = []
        for c in cands:
            hit = False
            for o_ in [c["o_press"], c["o_touch"]] + [c["o_touch"] - u * back_ * f for f in (0.33, 0.67, 1.0)]:
                Wall = np.vstack([o_ + hand.palm_pts() @ c["R"].T] + [o_ + hand.digit(d_, q_hand) @ c["R"].T for d_ in ("index",) + other_digits])
                if inside(Wall, sweep_hulls, 0.0).any():
                    hit = True
                    break
            if hit:
                why["the hand in the door's swing"] = why.get("the hand in the door's swing", 0) + 1
            else:
                kept.append(c)
        cands = kept
    cands.sort(key=lambda c: c["score"])
    if not cands:
        return {"passed": False, "reason": "no finger pose presses the button with the hand clear of the appliance and of the door's swing", "rejected": why}
    seeds = [np.asarray(start, float)]
    rng = np.random.default_rng(20261016)
    lo_b, hi_b = np.asarray(g.bounds, float)[:, 0], np.asarray(g.bounds, float)[:, 1]
    for k in range(int(opts.get("ik_seeds", 32))):
        b = seeds[0].copy()
        b[:8] = np.clip(b[:8] + rng.normal(0.0, 0.3 + 0.05 * k, 8), lo_b, hi_b)
        seeds.append(b)
    step = np.asarray(action_step, float)
    back = float(task.get("approach_m", 0.05))
    tried = []
    for cand in cands[:int(opts.get("max_candidates", 40))]:
        R = cand["R"]
        q_t, best_err, coll = None, None, set()
        for s in seeds:
            q_, fit = g.solve(np.r_[s[:8], q_hand], cand["o_touch"], R)
            if not fit["passed"]:
                e = (round(fit.get("position_error_m", 0) * 1000, 1), round(float(np.degrees(fit.get("angle_error_rad", 0.0))), 1))
                best_err = e if best_err is None or e < best_err else best_err
                continue
            if not near_checker(q_):
                coll.update(f"{a_.split('/')[-2]}~{b_.split('/')[-1] if '/' in b_ else b_}" for a_, b_ in near_checker(q_, True)[:3])
                continue
            q_t = q_
            break
        if q_t is None:
            tried.append({"candidate": cand["roll_deg"], "tilt": cand["tilt_deg"],
                          "rejected": f"no IK at the button (best error mm/deg {best_err}; collisions {sorted(coll)[:4]})"})
            continue
        q_p, fit = g.solve(q_t, cand["o_press"], R)
        if not (fit["passed"] and near_checker(q_p)):
            tried.append({"candidate": cand["roll_deg"], "rejected": "no IK pressed in"})
            continue
        q_st, why_st = None, []
        for back in [float(task.get("approach_m", 0.05))] + [0.08, 0.10, 0.12]:
            o_st = cand["o_touch"] - u * back
            q_s, fit = g.solve(q_t, o_st, R)
            if not fit["passed"]:
                why_st.append(f"{back:.2f} m: IK")
                continue
            if not reach_checker(q_s):
                why_st.append(f"{back:.2f} m: " + ",".join(f"{a_.split('/')[-2]}~{b_.split('/')[-1] if '/' in b_ else b_}" for a_, b_ in reach_checker(q_s, True)[:2]))
                continue
            q_st = q_s
            break
        if q_st is None:
            tried.append({"candidate": cand["roll_deg"], "tilt": cand["tilt_deg"], "rejected": "no clear staging pose in front of the button: " + "; ".join(why_st)})
            continue
        line, ok = [], True
        q_ = q_st.copy()
        for f in np.linspace(0, 1, max(1, int(np.ceil(back / 0.004))) + 1)[1:]:
            q_, fit = g.solve(q_, o_st * (1 - f) + cand["o_touch"] * f, R)
            if not (fit["passed"] and near_checker(q_)):
                ok = False
                break
            line.append(q_.copy())
        if not ok:
            tried.append({"candidate": cand["roll_deg"], "rejected": "the approach to the button is blocked"})
            continue
        path, stages = [np.asarray(start, float)], ["start"]

        def walk(goal, label, scale=1.0):
            q0 = path[-1].copy()
            m = int(np.ceil(np.max(np.abs(goal - q0) / (step * scale)))) if np.any(goal != q0) else 0
            for f_ in np.linspace(0, 1, m + 1)[1:]:
                path.append(q0 * (1 - f_) + goal * f_); stages.append(label)

        def hold(sec, label):
            for _ in range(int(sec * HZ)):
                path.append(path[-1].copy()); stages.append(label)

        walk(np.r_[path[-1][:8], q_hand], "preshape")
        try:
            dense, rinfo = joint_path(reach_checker, path[-1][:8], q_st[:8], q_hand, timeout=float(task.get("rrt_timeout_s", 120)))
        except Exception as e:                                       # noqa: BLE001 - the RRT's own failure, recorded
            tried.append({"candidate": cand["roll_deg"], "rejected": f"reach: {e}"})
            continue
        for qa in dense:
            walk(np.r_[np.asarray(qa, float)[:8], q_hand], "reach")
        walk(np.r_[q_st[:8], q_hand], "reach")
        for q_l in line:
            walk(np.r_[q_l[:8], q_hand], "approach", scale=0.3)
        # the press: a few mm per second (a person presses a button in a quarter second; the
        # pad and the latch read the travel on the way)
        n_p = max(4, int(np.ceil(press / 0.0005)))
        for f in np.linspace(0, 1, n_p + 1)[1:]:
            q_m, _ = g.solve(path[-1], cand["o_touch"] + u * press * f, R)
            path.append(np.r_[q_m[:8], q_hand]); stages.append("press")
        hold(float(opts.get("hold_s", 1.0)), "pressed")
        for f in np.linspace(1, 0, n_p + 1)[1:]:
            q_m, _ = g.solve(path[-1], cand["o_touch"] + u * press * f, R)
            path.append(np.r_[q_m[:8], q_hand]); stages.append("release")
        for q_l in line[::-1]:
            walk(np.r_[q_l[:8], q_hand], "retreat", scale=0.5)
        hold(float(task.get("hold_s", 1.0)), "hold")
        arr = np.asarray(path)
        sc = screen(reach_checker, [q for q, s_ in zip(arr, stages) if s_ in ("preshape", "reach")])
        if not sc["passed"]:
            tried.append({"candidate": cand["roll_deg"], "rejected": f"screen: {sc['collisions'][:1]}"})
            continue
        g.forward(q_t)
        pb, Rb = g.pose()
        return {"passed": True, "targets": arr, "stages": stages,
                "grip": {"roll_deg": cand["roll_deg"], "tilt_deg": cand["tilt_deg"], "press_m": round(press, 4),
                         "face": np.round(face, 4).tolist(), "press_dir": np.round(u, 4).tolist(), "q_hand": np.round(q_hand, 3).tolist()},
                "palm": {"p": pb.round(4).tolist(), "q_xyzw": Rotation.from_matrix(Rb).as_quat().round(5).tolist()},
                "rrt": rinfo, "candidates": len(cands), "tried": tried, "rejected": why, "screen": {k: sc[k] for k in ("passed", "waypoints")}}
    return {"passed": False, "reason": f"{len(cands)} finger poses, none planned", "tried": tried[:12], "rejected": why}
