#!/usr/bin/env python3
"""Scissors by one hand: the thumb through one handle loop, the index through
the other, the hand's own thumb-index spread opening and closing the blades.

The geometry is the flat tool's: each half rasterised in the scissors' plane
(a ring's hole stays open there - every convex slab of a ring bridges it), a
digit colliding where its points inside the plane's thickness band fall on a
half's solid cells (dilated by a clearance). The loops' holes come from the
same rasters (loop_holes.holes): their centres are where the digits go.

The hand: from a table of Ability Hand configurations (thumb rotation and
flexion, index flexion; the middle, ring and pinky curled away), those whose
thumb and index distal links point the same way (within a tolerance) with
their tips a given distance apart - the loops' spread now, and the spreads the
cycle moves through. A configuration's palm pose is fitted so the two tips sit
`insert_m` under the scissors' top at the two hole centres and the digits
point down (Kabsch on three point pairs); kept when both digits clear the
rims, the rest of the hand clears the scissors and the stand, and the arm has
IK clear of the table and the robot.

Then: the reach (HomeHero's RRT) to a staging pose above, straight down
through the loops, the lift on the digits, and the cycles: the hand moved
between its open and closed spreads with the palm held.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HZ = 30


# --- the flat tool in its plane ------------------------------------------------------------------

def plane_frame(points):
    """Origin, axes (u, v in the plane, n its normal pointing up) of a flat body's points."""
    P = np.asarray(points, float)
    c = P.mean(0)
    vt = np.linalg.svd(P - c, full_matrices=False)[2]
    n = vt[2] if vt[2][2] >= 0 else -vt[2]
    u = vt[0] - n * (vt[0] @ n)
    u /= np.linalg.norm(u)
    return c, u, np.cross(n, u), n


class FlatOccupancy:
    """A flat body's silhouette in a plane frame, dilated by `clearance`, and
    the thickness band (along n) it occupies."""

    def __init__(self, points, frame, cell=0.001, clearance=0.002):
        from scipy import ndimage

        c, u, v, n = frame
        P = np.asarray(points, float) - c
        uv = np.c_[P @ u, P @ v]
        self.h = P @ n
        self.band = (float(self.h.min()) - clearance, float(self.h.max()) + clearance)
        self.frame, self.cell = frame, cell
        self.lo = uv.min(0) - 0.01
        ij = np.floor((uv - self.lo) / cell).astype(int)
        grid = np.zeros(ij.max(0) + int(0.02 / cell) + 2, bool)
        grid[ij[:, 0], ij[:, 1]] = True
        grid = ndimage.binary_closing(grid, iterations=2)
        r = max(1, int(round(clearance / cell)))
        self.grid = ndimage.binary_dilation(grid, iterations=r)

    def hits(self, W):
        """Which world points fall on the body (within the band, on a solid cell)."""
        c, u, v, n = self.frame
        P = np.asarray(W, float) - c
        h = P @ n
        ij = np.floor((np.c_[P @ u, P @ v] - self.lo) / self.cell).astype(int)
        inb = (h >= self.band[0]) & (h <= self.band[1])
        ok = inb & (ij[:, 0] >= 0) & (ij[:, 1] >= 0) & (ij[:, 0] < self.grid.shape[0]) & (ij[:, 1] < self.grid.shape[1])
        out = np.zeros(len(P), bool)
        out[ok] = self.grid[ij[ok, 0], ij[ok, 1]]
        return out

    def under(self, W, depth=0.02):
        """Which world points lie BELOW the body's solid (under its band, within
        `depth` of it, over a solid cell): a rim the points would catch if lifted."""
        c, u, v, n = self.frame
        P = np.asarray(W, float) - c
        h = P @ n
        ij = np.floor((np.c_[P @ u, P @ v] - self.lo) / self.cell).astype(int)
        below = (h < self.band[0]) & (h >= self.band[0] - depth)
        ok = below & (ij[:, 0] >= 0) & (ij[:, 1] >= 0) & (ij[:, 0] < self.grid.shape[0]) & (ij[:, 1] < self.grid.shape[1])
        out = np.zeros(len(P), bool)
        out[ok] = self.grid[ij[ok, 0], ij[ok, 1]]
        return out


def hook_flex(digit_points_at, flex0, occ_solid, step=0.05, max_extra=0.8, min_under=12, allow_push=False):
    """The least extra flexion that curls a digit, already through a loop,
    under the loop's rim: its points under the loop's solid (occ_solid.under)
    before any enters the loop's material (occ_solid.hits) - a straight digit
    pointing down through a loop catches nothing, and the scissors stayed on
    the stand as the hand rose (loop take 1, 2026-10-15). digit_points_at(flex)
    gives the digit's world points. Returns (extra_rad, points_under) or None."""
    sgn = -1.0 if step < 0 else 1.0                                 # a negative step: the digit EXTENDS toward the loop's far side
    for extra in np.arange(abs(step), max_extra + 1e-9, abs(step)) * sgn:
        W = digit_points_at(flex0 + extra)
        if occ_solid.hits(W).any() and not allow_push:              # allow_push: the digit may press the wall it swings the half by
            return None
        n_under = int(occ_solid.under(W).sum())
        if n_under >= min_under:
            return float(extra), n_under
    return None


# --- the hand's spreads ---------------------------------------------------------------------------

def spread_table(hand, curl=1.6, n=18):
    """Ability Hand configurations (thumb rot, thumb flex, index flex; the
    other fingers curled) with the thumb and index tips and their distal
    links' directions in the palm frame."""
    from pinch_place_planner import _tip

    g = hand.g
    ti, tt = _tip("psyonic_left_index_L2"), _tip("psyonic_left_thumb_L2")
    rows = []
    for rot in np.linspace(-1.74, 0.0, n):
        for tf in np.linspace(0.0, 1.5, n):
            for fi in np.linspace(0.0, 1.5, n):
                q = np.array([fi, curl, curl, curl, tf, rot])
                g.forward(np.r_[hand.arm, q])
                pb, Rb = g.pose(hand.palm)
                out = []
                for name, tip in (("index", ti), ("thumb", tt)):
                    p2, R2 = g.pose(f"psyonic_left_{name}_L2")
                    t = Rb.T @ (p2 + R2 @ tip - pb)
                    d = Rb.T @ (R2 @ tip)
                    out += [t, d / np.linalg.norm(d)]
                rows.append((q, *out))
    return rows


def kabsch(A, B):
    """R, t minimising |R A + t - B| (rows are points)."""
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    U, _, Vt = np.linalg.svd(H)
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    return R, cb - R @ ca


def palm_pose_for(cfg, hole_t, hole_i, n_up, insert_m, top_h, thumb_insert_m=None, offsets=(None, None)):
    """The palm pose putting the thumb tip under hole_t and the index tip
    under hole_i, the digits pointing down: the index `insert_m` below the
    top surface, the thumb `thumb_insert_m` (default the same) - each at least
    that deep, the configuration's own tip offset kept (a tip further along
    the digits goes that much deeper). offsets: the digits' sections' measured
    horizontal offsets from their holes (thumb, index), subtracted from the
    targets so the sections, not the tip points, are centred."""
    q, ti, di, tt, dt = cfg
    d_mean = (di + dt) / np.linalg.norm(di + dt)
    A = np.vstack([tt, ti, (tt + ti) / 2 - d_mean * 0.05])
    ins_t = insert_m if thumb_insert_m is None else float(thumb_insert_m)
    D = float(tt @ d_mean) - float(ti @ d_mean)                  # the thumb tip's lead along the digits
    e_i = max(insert_m, ins_t - D)
    e_t = e_i + D
    off_t, off_i = (np.zeros(3) if o_ is None else np.asarray(o_, float) for o_ in offsets)
    Bt = hole_t - off_t + n_up * ((top_h - e_t) - (hole_t - off_t) @ n_up)
    Bi = hole_i - off_i + n_up * ((top_h - e_i) - (hole_i - off_i) @ n_up)
    B = np.vstack([Bt, Bi, (Bt + Bi) / 2 + n_up * 0.05])
    R, t = kabsch(A, B)
    err = float(np.linalg.norm((A @ R.T + t) - B, axis=1).max())
    return R, t, err


def digit_core(P, tip, d, back_m, half=0.004):
    """A digit's core point `back_m` behind its tip along its direction d:
    the centroid of its points (palm frame) in a slab +/- half there - where
    the digit crosses the scissors' plane when its tip is back_m past it."""
    P = np.asarray(P, float)
    s = (np.asarray(tip, float) - P) @ np.asarray(d, float)
    m = (s >= back_m - half) & (s <= back_m + half)
    return P[m].mean(0) if m.any() else np.asarray(tip, float) - np.asarray(d, float) * back_m


def palm_pose_for_cores(core_t, core_i, d_mean, hole_t, hole_i, n_up, plane_h):
    """The palm pose putting the digits' core points (palm frame) on the hole
    centres at the plane's mid-height, the digits pointing down."""
    A = np.vstack([core_t, core_i, (core_t + core_i) / 2 - d_mean * 0.05])
    Bt = hole_t + n_up * (plane_h - hole_t @ n_up)
    Bi = hole_i + n_up * (plane_h - hole_i @ n_up)
    B = np.vstack([Bt, Bi, (Bt + Bi) / 2 + n_up * 0.05])
    R, t = kabsch(A, B)
    return R, t, float(np.linalg.norm((A @ R.T + t) - B, axis=1).max())


def section_offset(W, hole, n_up, band):
    """A digit's section where it crosses the scissors' plane (its points
    between the band's heights along n_up): the section centroid's horizontal
    offset from the hole's centre, or None when the digit does not cross."""
    h = W @ n_up
    sec = W[(h >= band[0]) & (h <= band[1])]
    if not len(sec):
        return None
    off = sec.mean(0) - hole
    return off - n_up * (off @ n_up)


def plan_loop_grip(req, g, checker, start, lower, upper, action_step, slide_bounds, make_checker):
    from aida_r5_motion import joint_path, screen
    from loop_holes import holes
    from pinch_place_planner import CACHE
    from tool_grip_planner import DIGITS, HandModel

    task = req["task"]
    opts = task.get("loop_grip", {})
    bodies = {k: np.asarray(v, float) for k, v in task["bodies"].items()}
    t_name, i_name = opts["thumb_loop"], opts["finger_loop"]
    allp = np.vstack(list(bodies.values()))
    frame = plane_frame(allp)
    c, u, v, n_up = frame
    occ = {k: FlatOccupancy(P, frame, clearance=float(opts.get("clearance_m", 0.002))) for k, P in bodies.items()}
    top_h = float(((allp - c) @ n_up).max())
    hole = {}
    for k in (t_name, i_name):
        hs = holes(bodies[k])
        if not hs:
            return {"passed": False, "reason": f"no loop hole found in {k}"}
        hole[k] = np.asarray(hs[0]["centre"], float)
    spread_now = float(np.linalg.norm((hole[t_name] - hole[i_name]) - n_up * ((hole[t_name] - hole[i_name]) @ n_up)))
    hand = HandModel(g, CACHE)
    table = spread_table(hand)
    tol = np.radians(float(opts.get("parallel_deg", 15.0)))

    def spread(cfg):
        q, ti, di, tt, dt = cfg
        d = (di + dt) / np.linalg.norm(di + dt)
        s = tt - ti
        return float(np.linalg.norm(s - d * (s @ d))), float(abs(s @ d)), float(np.arccos(np.clip(di @ dt, -1, 1)))
    usable, cycling = [], []
    for cfg in table:
        sp, lev, ang = spread(cfg)
        lead = float(opts.get("thumb_insert_m", opts.get("insert_m", 0.025))) - float(opts.get("insert_m", 0.025))
        q_, ti_, di_, tt_, dt_ = cfg
        dm_ = (di_ + dt_) / np.linalg.norm(di_ + dt_)
        lev_t = abs(float((tt_ - ti_) @ dm_) - lead)            # off the wanted thumb lead (0: level tips)
        if ang <= tol and lev_t <= float(opts.get("level_m", 0.015)):
            usable.append((sp, cfg, lev_t))
        if ang <= tol and lev <= float(opts.get("cycle_level_m", 0.035)):
            # once through the loops the tips need not be level: a deeper digit only
            # goes further through its loop - the close draws the digits nearer than
            # level digits reach (6.9 cm; closed loops are 5.4 cm apart)
            cycling.append((sp, cfg))
    if not usable:
        return {"passed": False, "reason": "no hand configuration holds the thumb and index parallel and level"}
    # nearest the loops' spread, and level: a tip further along the digits than the
    # other goes that much deeper, and the hand with it - the curled middle, ring
    # and pinky then hung down to the scissors (the index 3.6 cm ahead of the thumb)
    band_half = 0.5 * float(((allp - c) @ n_up).max() - ((allp - c) @ n_up).min())
    cores = {}
    if opts.get("thumb_insert_m") is not None:
        # the digits' SECTIONS where they cross the plane, not their tip points: each
        # digit's core (its section's centroid) at its own depth behind the tip
        ins_i_, ins_t_ = float(opts.get("insert_m", 0.025)), float(opts["thumb_insert_m"])
        ranked = []
        for sp, cfg, lev in usable:
            q_, ti_, di_, tt_, dt_ = cfg
            dm_ = (di_ + dt_) / np.linalg.norm(di_ + dt_)
            ct = digit_core(hand.digit("thumb", q_), tt_, dt_, ins_t_ - band_half)
            ci = digit_core(hand.digit("index", q_), ti_, di_, ins_i_ - band_half)
            sv = ct - ci
            c_sp, c_lev = float(np.linalg.norm(sv - dm_ * (sv @ dm_))), abs(float(sv @ dm_))
            cores[id(cfg)] = (ct, ci, dm_)
            ranked.append((abs(c_sp - spread_now) + 0.5 * c_lev, c_sp, cfg))
        ranked.sort(key=lambda r: r[0])
        near = [(c_sp, cfg) for _, c_sp, cfg in ranked[:int(opts.get("configs", 24))]]
    else:
        near = [(sp, cfg) for sp, cfg, lev in sorted(usable, key=lambda sc: abs(sc[0] - spread_now) + 0.5 * sc[2])[:int(opts.get("configs", 24))]]
    usable = [(sp, cfg) for sp, cfg, lev in usable]
    stand = [o for o in req["obstacles"]]
    tried, insert = [], float(opts.get("insert_m", 0.025))
    seeds = [np.asarray(start, float)] + [np.r_[np.asarray(s, float)[:8], start[8:]] for s in task.get("ik_seeds", [])]
    rng = np.random.default_rng(20261015)
    lo_b, hi_b = np.asarray(g.bounds, float)[:, 0], np.asarray(g.bounds, float)[:, 1]
    for k in range(24):
        b = seeds[k % len(seeds)].copy()
        b[:8] = np.clip(b[:8] + rng.normal(0.0, 0.3 + 0.05 * k, 8), lo_b, hi_b)
        seeds.append(b)
    hand_pts = {d: None for d in DIGITS}
    for sp, cfg in near:
        q = cfg[0]
        top_w = top_h + c @ n_up
        band_w = (float((allp @ n_up).min()), float((allp @ n_up).max()))
        t_ins = opts.get("thumb_insert_m")
        if id(cfg) in cores:
            ct, ci, dm_ = cores[id(cfg)]
            R, o, err = palm_pose_for_cores(ct, ci, dm_, hole[t_name], hole[i_name], n_up, top_w - band_half)
        else:
            R, o, err = palm_pose_for(cfg, hole[t_name], hole[i_name], n_up, insert, top_w, t_ins)
        # centre the digits' SECTIONS in the holes, not their tip points: the index's
        # tip point under the hole put its section 8 mm off centre in a 2.6 cm loop
        # (2026-10-15); a few passes of measure-and-shift
        offs = (np.zeros(3), np.zeros(3))
        for _ in range(0 if id(cfg) in cores else int(opts.get("centre_passes", 3))):
            ot = section_offset(o + hand.digit("thumb", q) @ R.T, hole[t_name], n_up, band_w)
            oi = section_offset(o + hand.digit("index", q) @ R.T, hole[i_name], n_up, band_w)
            if ot is None or oi is None:
                break
            offs = (offs[0] + ot, offs[1] + oi)
            R, o, err = palm_pose_for(cfg, hole[t_name], hole[i_name], n_up, insert, top_w, t_ins, offs)
        if t_ins is not None and opts.get("thumb_max_insert_m") is not None:
            depth_t = top_w - float((o + R @ cfg[3]) @ n_up)
            depth_i = top_w - float((o + R @ cfg[1]) @ n_up)
            if depth_t > float(opts["thumb_max_insert_m"]) + 1e-9 or depth_i < float(opts.get("index_min_insert_m", 0.018)):
                tried.append({"spread_m": round(sp, 4), "rejected": f"depths: the thumb {depth_t * 100:.1f} cm, the index {depth_i * 100:.1f} cm into the loops"})
                continue
        if err > float(opts.get("fit_m", 0.006)):
            tried.append({"spread_m": round(sp, 4), "rejected": f"the tips cannot both sit in the loops ({err * 1000:.1f} mm)"})
            continue
        # every digit and the palm against both halves (the thumb and index may pass only through their holes)
        W = {d: o + hand.digit(d, q) @ R.T for d in DIGITS}
        W["palm"] = o + hand.palm_pts() @ R.T
        clash = [(d, k) for d, P in W.items() for k, oc in occ.items() if oc.hits(P).any()]
        if clash:
            tried.append({"spread_m": round(sp, 4), "rejected": f"touches the scissors: {clash[:3]}"})
            continue
        qa = None
        for s0 in seeds:
            q_, fit = g.solve(np.r_[s0[:8], q], o, R)
            if fit["passed"] and checker(q_):
                qa = q_
                break
        if qa is None:
            tried.append({"spread_m": round(sp, 4), "rejected": "no IK clear of the table and the robot"})
            continue
        # staging above and the straight descent through the loops
        up = n_up * float(opts.get("staging_m", 0.06))
        q_st, fit = g.solve(qa, o + up, R)
        if not (fit["passed"] and checker(q_st)):
            tried.append({"spread_m": round(sp, 4), "rejected": "no clear staging pose above"})
            continue
        path, stages = [np.asarray(start, float)], ["start"]
        step = np.asarray(action_step, float)

        def walk(goal, label, scale=1.0):
            q0 = path[-1].copy()
            m = int(np.ceil(np.max(np.abs(goal - q0) / (step * scale)))) if np.any(goal != q0) else 0
            for f in np.linspace(0, 1, m + 1)[1:]:
                path.append(q0 * (1 - f) + goal * f); stages.append(label)

        def hold(sec, label):
            for _ in range(int(sec * HZ)):
                path.append(path[-1].copy()); stages.append(label)

        walk(np.r_[path[-1][:8], q], "preshape")
        try:
            dense, info = joint_path(make_checker([dict(id=f"scissors:{k}", min=(P.min(0) - 0.01).tolist(), max=(P.max(0) + 0.01).tolist())
                                                   for k, P in bodies.items()]), path[-1][:8], q_st[:8], q,
                                     timeout=float(task.get("rrt_timeout_s", 120)))
        except Exception as e:                                           # noqa: BLE001
            tried.append({"spread_m": round(sp, 4), "rejected": f"reach: {e}"})
            continue
        for qa_ in dense:
            walk(np.r_[np.asarray(qa_, float)[:8], q], "reach")
        walk(np.r_[q_st[:8], q], "reach")
        ok, q_ = True, q_st.copy()
        nstep = max(1, int(np.ceil(np.linalg.norm(up) / 0.004)))
        for f in np.linspace(0, 1, nstep + 1)[1:]:
            q_, fit = g.solve(q_, o + up * (1 - f), R)
            if not (fit["passed"] and checker(q_)):
                ok = False
                break
            walk(np.r_[q_[:8], q], "insert", scale=0.3)
        if not ok:
            tried.append({"spread_m": round(sp, 4), "rejected": "the descent through the loops has no clear IK"})
            continue
        # the hook: the index curled under its loop's rim so the lift catches the
        # scissors (the thumb, shallower, presses its loop: the hand's spread)
        q_hook = q.copy()
        if opts.get("hook", True):
            occ0 = FlatOccupancy(bodies[i_name], frame, clearance=0.0)
            def _index_at(fl, q_=q_):
                qh = q.copy(); qh[0] = fl
                g.forward(np.r_[q_[:8], qh])
                pb, Rb = g.pose(hand.palm)
                return pb + hand.digit("index", qh) @ Rb.T
            hk = hook_flex(_index_at, float(q[0]), occ0, step=float(opts.get("hook_step_rad", 0.05)) * (-1.0 if opts.get("hook_side", "outer") == "outer" else 1.0),
                           max_extra=float(opts.get("hook_max_rad", 0.8)), min_under=int(opts.get("hook_min_points", 12)),
                           allow_push=bool(opts.get("hook_allow_push", False)))
            if hk is None:
                tried.append({"spread_m": round(sp, 4), "rejected": "the index cannot curl under its loop's rim (it meets the loop first, or never gets under it)"})
                continue
            q_hook[0] = max(0.0, q[0] + hk[0] + np.sign(hk[0]) * float(opts.get("hook_extra_rad", 0.05)))
            walk(np.r_[q_[:8], q_hook], "hook", scale=0.4)
        hold(0.5, "seat")
        # the lift on the digits: the palm straight up
        lift = n_up * float(task.get("lift_m", 0.05))
        nstep = max(1, int(np.ceil(np.linalg.norm(lift) / 0.004)))
        for f in np.linspace(0, 1, nstep + 1)[1:]:
            q_, fit = g.solve(q_, o + lift * f, R)
            if not (fit["passed"] and checker(q_)):
                ok = False
                break
            walk(np.r_[q_[:8], q_hook], "lift", scale=0.5)
        if not ok:
            tried.append({"spread_m": round(sp, 4), "rejected": "the lift has no clear IK"})
            continue
        hold(0.5, "settle")
        # the cycles: the spread opened and closed with the palm where it is. The
        # open spread: the scissors' full opening (the halves' limits) bounds it;
        # the configurations kept are the parallel ones nearest that spread whose
        # tips stay near the loops' arcs - here, simply the nearest by spread
        sp_open = float(opts.get("open_spread_target_m", spread_now + 0.03))
        sp_close = float(opts.get("close_spread_target_m", max(0.0, spread_now - 0.02)))
        pick = lambda target: min(cycling, key=lambda sc: abs(sc[0] - target) + 0.02 * float(np.abs(sc[1][0] - q).sum()))[1][0]
        q_open, q_close = pick(sp_open).copy(), pick(sp_close).copy()
        q_open[0] = q_close[0] = q_hook[0]                                # the index stays hooked: the thumb's spread works the blades
        for _ in range(int(task.get("cycles", 5))):
            walk(np.r_[path[-1][:8], q_open], "open", scale=0.6)
            hold(0.4, "open_hold")
            walk(np.r_[path[-1][:8], q_close], "close", scale=0.6)
            hold(0.4, "close_hold")
        hold(float(task.get("hold_s", 1.0)), "hold")
        arr = np.asarray(path)
        sc = screen(checker, [qq for qq, st_ in zip(arr, stages) if st_ in ("preshape", "reach", "insert", "hook", "lift")])
        if not sc["passed"]:
            tried.append({"spread_m": round(sp, 4), "rejected": f"screen: {sc['collisions'][:1]}"})
            continue
        return {"passed": True, "targets": arr, "stages": stages,
                "grip": {"spread_now_m": round(spread_now, 4), "config_spread_m": round(sp, 4), "q_insert": np.round(q, 3).tolist(),
                         "q_open": np.round(q_open, 3).tolist(), "q_close": np.round(q_close, 3).tolist(), "fit_err_m": round(err, 4),
                         "q_hook": np.round(q_hook, 3).tolist()},
                "holes": {k: np.round(h, 4).tolist() for k, h in hole.items()}, "rrt": info, "tried": tried, "candidates": len(near)}
    return {"passed": False, "reason": f"{len(near)} configurations near the loops' spread ({spread_now * 100:.1f} cm), none planned",
            "tried": tried[:12], "holes": {k: np.round(h, 4).tolist() for k, h in hole.items()}, "spread_now_m": round(spread_now, 4)}
