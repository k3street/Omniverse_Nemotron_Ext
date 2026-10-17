#!/usr/bin/env python3
"""A screw driven with the drill already in the hand (HomeHero's R5): from
where the drill sits in the hand NOW (the replay's live poses after the lift:
the drill settles a centimetre off the plan in the hand, and the bit must find
a 2.6 mm recess), the bit is brought onto the screw's axis, pressed onto its
head, the trigger pulled, and the hand fed along the axis behind the screw as
it goes in - a little slower than the screw can advance (behaviors.screw_drive:
the screw turns only while the bit sits in its head, so the screw follows the
hand and never runs away from it); held while the clutch slips at the seat,
the trigger let go, the bit drawn back.

Runs in HomeHero's venv through homehero_grasp_adapter.py (task kind
"screw_drive"), from screw_then.py.
"""
from __future__ import annotations

import numpy as np

HZ = 30


def rot_between(a, b):
    """The smallest rotation taking unit vector a onto unit vector b."""
    from scipy.spatial.transform import Rotation
    a = np.asarray(a, float) / np.linalg.norm(a)
    b = np.asarray(b, float) / np.linalg.norm(b)
    v = np.cross(a, b)
    s, c = float(np.linalg.norm(v)), float(a @ b)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        perp = np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0])
        return Rotation.from_rotvec(perp / np.linalg.norm(perp) * np.pi).as_matrix()
    return Rotation.from_rotvec(v / s * np.arctan2(s, c)).as_matrix()


def palm_for_bit(R_palm, p_palm, tip_w, dir_w, target_tip, target_dir):
    """The palm pose that puts the bit's tip at target_tip pointing along
    target_dir, the drill held in the hand as it is now (tip and direction
    carried rigidly with the palm)."""
    tip_p = R_palm.T @ (np.asarray(tip_w, float) - p_palm)
    dir_p = R_palm.T @ np.asarray(dir_w, float)
    R_new = rot_between(R_palm @ dir_p, target_dir) @ R_palm
    return R_new, np.asarray(target_tip, float) - R_new @ tip_p


def feed_ticks(drive_m, pitch_m, rpm, feed_fraction=0.8, hz=HZ):
    """Ticks to feed drive_m behind a screw advancing pitch_m a turn at rpm,
    the hand at feed_fraction of the screw's own rate (it follows the hand)."""
    v = feed_fraction * pitch_m * rpm / 60.0
    return max(1, int(np.ceil(drive_m / v * hz))), v


def plan_screw_drive(req, g, checker, start, lower, upper, action_step, slide_bounds, make_checker):
    """The whole stage from `start` (14): align, approach, pull, drive, seat, release, back off, hold."""
    from scipy.spatial.transform import Rotation, Slerp

    task = req["task"]
    sd = task["screw"]
    opts = task.get("screw_drive", {})
    step = np.asarray(action_step, float)
    q0 = np.asarray(start, float)
    g.forward(q0)
    p0, R0 = g.pose()
    tip, bdir = np.asarray(sd["bit_tip"], float), np.asarray(sd["bit_dir"], float)
    head, out = np.asarray(sd["head"], float), np.asarray(sd["out"], float) / np.linalg.norm(sd["out"])
    into = -out
    standoff = float(opts.get("standoff_m", 0.015))
    preload = float(opts.get("preload_m", 0.001))
    drive_m = float(sd["seat_depth_m"]) - float(sd["start_depth_m"]) + float(opts.get("over_m", 0.002))
    rpm = float(opts.get("rpm", 450.0)) * float(opts.get("throttle", 1.0))
    n_feed, v_feed = feed_ticks(drive_m, float(sd["pitch_m"]), rpm, float(opts.get("feed_fraction", 0.8)))
    path, stages = [q0.copy()], ["start"]
    # the grip itself: pairs already touching at the start (the fingers wrapped round the
    # handle meet the palm in the checker's meshes) are the hold, not a collision - only
    # new pairs stop a move (the first align read the middle and ring on the palm, 2026-10-16)
    grip_pairs = set(tuple(x) for x in (checker(q0, True) or []))

    def clear_q(q_):
        hits = checker(q_, True) or []
        return all(tuple(x) in grip_pairs for x in hits), [x for x in hits if tuple(x) not in grip_pairs]

    def walk(goal, label, hand_free=False, scale=1.0):
        a = path[-1].copy()
        s_ = step * scale
        if hand_free:
            s_ = s_.copy(); s_[8:] = np.inf
        m = int(np.ceil(np.max(np.abs(goal - a) / s_))) if np.any(goal != a) else 0
        m = max(m, 1) if hand_free else m
        for f in np.linspace(0, 1, m + 1)[1:]:
            q1 = a * (1 - f) + goal * f
            if hand_free:
                q1[8:] = goal[8:]
            path.append(q1); stages.append(label)

    def hold(sec, label):
        for _ in range(int(sec * HZ)):
            path.append(path[-1].copy()); stages.append(label)

    def line(p_from, R_from, p_to, R_to, label, every_m=0.002, scale=0.5, clear=True):
        """IK along a straight line, the orientation slerped; screened tick by tick."""
        n = max(1, int(np.ceil(np.linalg.norm(p_to - p_from) / every_m)))
        sl = Slerp([0, 1], Rotation.from_matrix([R_from, R_to]))
        q_ = path[-1].copy()
        for f in np.linspace(0, 1, n + 1)[1:]:
            q_, fit = g.solve(q_, p_from * (1 - f) + p_to * f, sl([f]).as_matrix()[0])
            if not fit["passed"]:
                return f"IK {fit.get('position_error_m', 0) * 1000:.1f} mm off at {f:.2f} of the {label}"
            if clear:
                ok_, new_ = clear_q(q_)
                if not ok_:
                    return f"collision at {f:.2f} of the {label}: {new_[:2]}"
            walk(np.r_[q_[:8], path[-1][8:]], label, scale=scale)
        return None

    # 1. align: the bit on the screw's axis, standoff off its head, pointing into it
    R_al, p_al = palm_for_bit(R0, p0, tip, bdir, head + out * standoff, into)
    why = line(p0, R0, p_al, R_al, "align", every_m=0.004, scale=0.5)
    if why:
        return {"passed": False, "reason": why}
    pulled = float(start[8]) >= float(opts.get("pull_max_rad", 1.6)) - 0.3       # the trigger already held (a spin stage before)
    if opts.get("part") == "spin":
        # the trigger pulled at the standoff: the index's squeeze shifts the drill in the hand
        # (the bit jumped 11.8 mm off the screw's axis and 4 deg the instant it was pulled on the
        # head, 2026-10-16) - spun up here, a look after corrects the shift, and the bit meets
        # the screw already turning, as a person starts a drill before setting it on the screw
        q_pull = path[-1].copy(); q_pull[8] = float(opts.get("pull_max_rad", 1.6))
        walk(q_pull, "pull", hand_free=True)
        hold(float(opts.get("spin_up_s", 0.6)) + float(opts.get("look_s", 0.5)), "spin")
        return {"passed": True, "targets": np.asarray(path), "stages": stages, "grip_pairs_ignored": len(grip_pairs),
                "drive": {"part": "spin", "align_rotation_deg": round(float(np.degrees(np.arccos(np.clip(bdir @ into, -1, 1)))), 2),
                          "align_shift_m": round(float(np.linalg.norm((head + out * standoff) - tip)), 4), "turns": 0, "drive_m": 0.0,
                          "rpm": rpm, "feed_m_s": 0.0}}
    if opts.get("part") == "align":
        # look, then correct: the arm carrying the drill lands ~1 cm off the line it was sent
        # along (10 mm off the axis, 4.4 mm short, 2026-10-16) - the next stage, planned from
        # where the bit IS, corrects what is left before the drive
        hold(float(opts.get("look_s", 0.5)), "align_hold")
        return {"passed": True, "targets": np.asarray(path), "stages": stages, "grip_pairs_ignored": len(grip_pairs),
                "drive": {"part": "align", "align_rotation_deg": round(float(np.degrees(np.arccos(np.clip(bdir @ into, -1, 1)))), 2),
                          "align_shift_m": round(float(np.linalg.norm((head + out * standoff) - tip)), 4), "turns": 0, "drive_m": 0.0,
                          "rpm": rpm, "feed_m_s": 0.0}}
    # 2. approach: onto the head, preload into it (the screw is held by the wood: the bit stops on it)
    R_ap, p_ap = palm_for_bit(R0, p0, tip, bdir, head - out * preload, into)
    why = line(p_al, R_al, p_ap, R_ap, "approach", every_m=0.001, scale=0.25, clear=False)
    if why:
        return {"passed": False, "reason": why}
    # 3. the trigger pulled: the index driven toward pull_max, the worm holds it where it stalls
    #    (already held when a spin stage came before: the bit meets the screw turning)
    if not pulled:
        q_pull = path[-1].copy(); q_pull[8] = float(opts.get("pull_max_rad", 1.6))
        walk(q_pull, "pull", hand_free=True)
        hold(float(opts.get("spin_up_s", 0.2)), "pull")
    # 4. drive: the hand fed along the axis behind the screw, a tick at a time - and the
    #    trigger re-squeezed every resqueeze_s: the index, halted on the trigger and held by
    #    its worm at the post-contact voltage, does not follow the trigger as the drill creeps
    #    in the grip (2 mm over a drive took the trigger from 34 to 0 % and the screw stopped
    #    at 11.8 of 26.8 mm, 2026-10-16); a command just below where it stalled reverses it,
    #    which clears the halt (the firmware), and the close after drives it on at full voltage
    q_ = path[-1].copy()
    pull_q = float(opts.get("pull_max_rad", 1.6))
    every = max(1, int(round(float(opts.get("resqueeze_s", 1.0)) * HZ)))
    dip_q, dip_n = float(opts.get("resqueeze_dip_rad", 1.15)), int(opts.get("resqueeze_ticks", 3))
    for k in range(1, n_feed + 1):
        p_k = p_ap + into * drive_m * k / n_feed
        q_, fit = g.solve(q_, p_k, R_ap)
        if not fit["passed"]:
            return {"passed": False, "reason": f"IK {fit.get('position_error_m', 0) * 1000:.1f} mm off {k} ticks into the drive"}
        hand = path[-1][8:].copy()
        hand[0] = dip_q if (every and k % every < dip_n and k >= every) else pull_q
        path.append(np.r_[q_[:8], hand]); stages.append("drive")
    # 5. seat: the trigger still pulled and the hand still leaning in at the feed's rate until the
    #    clutch slips at the seated head - held still, the drill crept back in the grip, the tip rose
    #    out of the recess and the screw stopped 0.5 mm short of its seat (2026-10-16)
    n_seat = int(float(opts.get("seat_s", 1.0)) * HZ)
    lean = float(v_feed) if opts.get("seat_feed", False) else 0.0       # off: a seated screw takes no more, and the push shoves the drill back in the grip
    for k in range(1, n_seat + 1):
        p_k = p_ap + into * (drive_m + lean * k / HZ)
        q_, fit = g.solve(q_, p_k, R_ap)
        if not fit["passed"]:
            break
        hand = path[-1][8:].copy()
        hand[0] = dip_q if (every and (n_feed + k) % every < dip_n) else pull_q
        path.append(np.r_[q_[:8], hand]); stages.append("seat")
    # 6. release and back off along the axis
    q_rel = path[-1].copy(); q_rel[8] = float(opts.get("index_release_rad", start[8]))     # back to where the grip held it
    walk(q_rel, "release", hand_free=True)
    hold(0.3, "release")
    g.forward(path[-1])
    p_end, R_end = g.pose()
    why = line(p_end, R_end, p_end + out * float(opts.get("back_off_m", 0.03)), R_end, "back_off", every_m=0.003, scale=0.5)
    if why:
        return {"passed": False, "reason": why}
    hold(float(opts.get("hold_s", 1.0)), "hold")
    arr = np.asarray(path)
    return {"passed": True, "targets": arr, "stages": stages, "grip_pairs_ignored": len(grip_pairs),
            "drive": {"drive_m": round(drive_m, 4), "feed_m_s": round(v_feed, 5), "feed_ticks": n_feed, "rpm": rpm,
                      "turns": round(drive_m / float(sd["pitch_m"]), 1),
                      "align_rotation_deg": round(float(np.degrees(np.arccos(np.clip(bdir @ into, -1, 1)))), 2),
                      "align_shift_m": round(float(np.linalg.norm((head + out * standoff) - tip)), 4)}}
