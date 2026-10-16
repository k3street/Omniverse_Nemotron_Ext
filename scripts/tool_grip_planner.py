#!/usr/bin/env python3
"""A tool grip for the R5's left Ability Hand: a pistol-grip tool (a cordless
drill) taken by its handle with the index on its trigger, lifted, the trigger
pulled and let go - planned with HomeHero's geometry, checker and RRT.

Everything about the tool comes from the tool itself (plan_er_grasp.py reads
it from the asset's motor behaviour and passes its parts' surface points at
their settled poses): the trigger is the behaviour's throttle joint's body,
the handle is that joint's other body between the battery (the power
press-fit's body) and the trigger, the pull is the throttle's axis, forward is
toward the output (the chuck).

The grip, as a person's left hand holds a drill:

  the hand's x (pinky -> index)  along the handle's axis, the index uppermost
  the hand's y (the palm's normal) toward the handle from the tool's LEFT side
                                   (left = up x forward)
  the hand's z (the fingers)       forward, so they wrap the handle's front

searched over small turns of that frame and offsets along the handle and
forward, each candidate's digits swept closed against the tool's surface
(slab hulls of its parts): the middle, ring and pinky must wrap the handle,
the thumb must close on it from behind, and the index must meet the TRIGGER
(not the handle) at a flexion with room left to push it its full travel.

Then: an RRT reach to a staging pose beside the handle with the hand open
(the thumb laid flat), a straight slide in, the thumb turned to its grip
rotation, the grip command (middle, ring, pinky, thumb) with the index held
just short of the trigger, the lift on the shoulder slide (HomeHero's lift
rule), the pull, the run, the release, the hold.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HZ = 30
DIGITS = ("index", "middle", "ring", "pinky", "thumb")


# --- geometry (no HomeHero needed) ----------------------------------------------------------

def sample_faces(points, counts, indices, spacing=0.003):
    """A mesh's surface as points about `spacing` apart: its vertices and,
    for each polygon (fanned into triangles), barycentric samples. A
    vertex-only cloud is too sparse to slice: a 1 cm band of the drill's
    handle held 20 vertices on one side only (2026-10-15)."""
    P = np.asarray(points, float)
    out = [P]
    k = 0
    for c in counts:
        poly = indices[k:k + c]
        k += c
        for j in range(1, c - 1):
            a, b, d = P[poly[0]], P[poly[j]], P[poly[j + 1]]
            n = int(max(np.linalg.norm(b - a), np.linalg.norm(d - a), np.linalg.norm(d - b)) / spacing)
            if n < 1:
                continue
            for u in range(n + 1):
                for v in range(n + 1 - u):
                    w = n - u - v
                    out.append(((u * a + v * b + w * d) / n)[None])
    return np.unique(np.round(np.vstack(out), 5), axis=0)


def face_normals(points, counts, indices):
    """A mesh's triangles (polygons fanned): centroids, unit normals by the
    winding (right-handed: outward for a closed, consistently wound mesh)
    and areas. Degenerate triangles dropped."""
    P = np.asarray(points, float)
    C, N, A = [], [], []
    k = 0
    for c in counts:
        poly = indices[k:k + c]
        k += c
        for j in range(1, c - 1):
            a, b, d = P[poly[0]], P[poly[j]], P[poly[j + 1]]
            n = np.cross(b - a, d - a)
            ar = float(np.linalg.norm(n)) / 2
            if ar < 1e-12:
                continue
            C.append((a + b + d) / 3); N.append(n / (2 * ar)); A.append(ar)
    return np.asarray(C), np.asarray(N), np.asarray(A)


def slab_hulls(points, axis, band=0.006, min_points=8, overlap=0.003):
    """A body as convex hulls of its slabs along `axis` (a non-convex body -
    a pistol grip and its barrel - is convex enough slab by slab). Returns
    a list of hulls (equations n x 4, n.p + d <= 0 inside; the hull's box lo, hi)."""
    from scipy.spatial import ConvexHull, QhullError

    P = np.asarray(points, float)
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    h = P @ a
    hulls = []
    for lo in np.arange(h.min(), h.max() + 1e-9, band):
        # each slab reaches `overlap` into its neighbours (at least the surface
        # sampling's spacing): a 0.5 mm overlap left 3 mm gaps between the
        # hulls of a sampled barrel's sides, and a probe inside it read free
        m = (h >= lo - overlap) & (h <= lo + band + overlap)
        if m.sum() < min_points:
            continue
        try:
            hulls.append((ConvexHull(P[m]).equations, P[m].min(0), P[m].max(0)))
        except QhullError:
            continue
    return hulls


def inside(P, hulls, tol=0.0):
    """Which points lie inside any hull, deeper than tol (m); each hull's
    box first, so a finger far from the tool costs almost nothing."""
    P = np.asarray(P, float)
    hit = np.zeros(len(P), bool)
    for eq, lo, hi in hulls:
        m = np.all((P >= lo) & (P <= hi), axis=1) & ~hit
        if not m.any():
            continue
        hit[np.flatnonzero(m)[np.all(P[m] @ eq[:, :3].T + eq[:, 3] <= -tol, axis=1)]] = True
    return hit


def handle_axis(handle_pts, up, band=0.008):
    """The handle's axis: a line through its slab centres (least squares),
    pointing up. Returns (a point on it, unit direction)."""
    P = np.asarray(handle_pts, float)
    up = np.asarray(up, float) / np.linalg.norm(up)
    h = P @ up
    centres = []
    for lo in np.arange(h.min(), h.max() - band / 2, band):
        m = (h >= lo) & (h < lo + band)
        if m.sum() >= 4:
            centres.append((P[m].max(0) + P[m].min(0)) / 2)
    C = np.asarray(centres)
    if len(C) < 2:
        return P.mean(0), up
    c0 = C.mean(0)
    d = np.linalg.svd(C - c0)[2][0]
    return c0, d if d @ up > 0 else -d


def grip_axes(a_hat, forward_hint):
    """The tool's frame at its handle: a (up the handle), f (forward,
    square to a, toward the output), l (the tool's left = a x f)."""
    a = np.asarray(a_hat, float) / np.linalg.norm(a_hat)
    f = np.asarray(forward_hint, float) - a * (np.asarray(forward_hint, float) @ a)
    f /= np.linalg.norm(f)
    return a, f, np.cross(a, f)


def hull_entry(P, direction, eq):
    """For points P moving along `direction`, how far each goes before it
    enters the convex hull eq (n.x + d <= 0 inside; 0 if already inside;
    inf if it never does) - the slab method, face by face."""
    P = np.atleast_2d(np.asarray(P, float))
    u = np.asarray(direction, float) / np.linalg.norm(direction)
    n, dd = eq[:, :3], eq[:, 3]
    A = P @ n.T + dd                                  # N x F: > 0 outside that face
    B = n @ u                                         # F
    with np.errstate(divide="ignore", invalid="ignore"):
        t = -A / B
    lower = np.where(B < 0, t, -np.inf).max(axis=1)
    upper = np.where(B > 0, t, np.inf).min(axis=1)
    parallel_out = ((B == 0) & (A > 0)).any(axis=1)
    enter = np.maximum(lower, 0.0)
    hit = (enter <= upper) & ~parallel_out
    return np.where(hit, enter, np.inf)


def seat_along(hand_pts, tool_rel, direction):
    """How far the tool (points in the hand's frame) can move along
    `direction` before any of its points enters the hand's convex collider
    (the hull of hand_pts: the palm's collider IS one convex hull). None when
    nothing is in the way. The closing fingers pull a handle back into the
    hand until it meets the palm (the drill's trigger finished 3.4 cm nearer
    the wrist than planned, under the middle finger, drill rig 2026-10-15)."""
    from scipy.spatial import ConvexHull

    eq = ConvexHull(np.asarray(hand_pts, float)).equations
    t = hull_entry(tool_rel, direction, eq)
    t = t[np.isfinite(t)]
    return float(t.min()) if len(t) else None


def palm_seat(palm_pts, handle_rel):
    """How far the hand can move along its palm normal (+y) before its
    palm's collider - the convex hull of palm_pts - touches the handle (the
    handle moving -y into it). The hull's slanted face between the heel and
    the finger bases is what the handle meets, not the palm's highest point
    (its heel: the handle sat 3 cm off the palm) and not its nearest
    vertices (sparse on a flat face). None when the handle is not over it."""
    return seat_along(palm_pts, handle_rel, [0.0, -1.0, 0.0])


def _rot(axis, ang):
    from scipy.spatial.transform import Rotation
    axis = np.asarray(axis, float)
    return Rotation.from_rotvec(axis / np.linalg.norm(axis) * ang).as_matrix()


def left_grip_rotation(a, f, l, yaw=0.0, roll=0.0):
    """The left hand's world rotation (columns: its x, y, z) for the pistol
    grip: x along the handle (index up), y = -l (the palm on the tool's left
    facing right), z = x cross y (forward). yaw turns it about the handle,
    roll about the forward axis."""
    R = np.column_stack([a, -l, np.cross(a, -l)])
    return _rot(a, yaw) @ _rot(f, roll) @ R


def trigger_travel(finger_pts, trigger_pts, pull, radius=0.003):
    """How far the trigger must move along `pull` (unit, the way it
    travels) to clear the finger's points: for each finger point, the
    trigger's points within `radius` of the line through it along pull,
    the finger's depth past the trigger's front (its least pull-coordinate
    on that line). 0 when the finger does not reach it."""
    F = np.asarray(finger_pts, float)
    T = np.asarray(trigger_pts, float)
    pull = np.asarray(pull, float) / np.linalg.norm(pull)
    tp = T @ pull
    T_perp = T - np.outer(tp, pull)
    best = 0.0
    for p in F:
        pp = p @ pull
        d = np.linalg.norm(T_perp - (p - pp * pull), axis=1)
        m = d < radius
        if m.any():
            best = max(best, pp - tp[m].min())
    return float(best)


def push_lever(P0, P1, dq, mask, direction):
    """How far the touching points move along `direction` per radian of the
    digit's motor (m/rad): the finger's lever on what it pushes. P0, P1 the
    digit's points (one frame, the same order) at q and q + dq; mask the
    touching points. A force there is the motor's torque over this lever
    (virtual work through the coupled joints); the median of the touching
    points' levers."""
    v = ((np.asarray(P1, float) - np.asarray(P0, float)) / float(dq))[np.asarray(mask, bool)]
    return float(np.median(v @ (np.asarray(direction, float) / np.linalg.norm(direction)))) if len(v) else 0.0


def push_force(torque_nm, v, face_n, pull):
    """The force along a sprung control's travel `pull` from a digit whose
    touching points move v (m/rad of its motor) against faces of outward
    normal face_n: frictionless, the contact force is along -n, its size the
    torque over the digit's lever into the face (-n . v), and the control
    takes its component along the travel (-n . pull). A digit pressing a
    face side-on pushes hard and moves the control little: the drill's index
    rode the trigger's lower edge (faces within 8 mm of its touch: -n.pull
    0.3) and this gives 7.2 N where the take stalled at 7.5 N (2026-10-15).
    0 when the digit moves out of the face or the face does not face the travel."""
    n = np.asarray(face_n, float) / np.linalg.norm(face_n)
    into = float(-n @ np.asarray(v, float))
    along = float(-n @ (np.asarray(pull, float) / np.linalg.norm(pull)))
    if into <= 1e-5 or along <= 0.0:
        return 0.0
    return float(torque_nm) * along / into


def force_reach(travel_at, force_at, qs, spring_n_per_m, travel_full):
    """How far a finger can push a sprung trigger: along the finger's flexion
    `qs`, the trigger's displacement where the finger is (travel_at(q)) and
    the force the finger makes along the travel there (force_at(q), N). The
    reach ends where that force falls short of the spring at that
    displacement - the motor stalls - or where the finger stops pushing it.
    Returns metres, at most travel_full."""
    reach = 0.0
    for q in qs:
        t = float(travel_at(q))
        if t <= 0.0:
            continue
        f = float(force_at(q))
        if f <= 0.0:
            break
        if spring_n_per_m > 0 and f < spring_n_per_m * t:
            reach = max(reach, min(t, f / spring_n_per_m))
            break
        reach = max(reach, t)
        if reach >= travel_full:
            break
    return float(min(reach, travel_full))


def settled_hand(o, R, settle):
    """The hand's pose against a tool that has settled in it, the tool held
    still: settle is the tool's motion in the palm's frame, [tx, ty, tz] (m) or
    [tx, ty, tz, rx, ry, rz] (m, then a rotation vector in degrees) - X1 =
    T X0 for the tool's pose in the palm frame. The hand then is at X_palm T^-1:
    R' = R Rs^T, o' = o - R Rs^T t. A drill in the knuckle grip turned 7.2 deg
    about the fingers' axis as it settled, and a shift alone missed it
    (2026-10-15)."""
    from scipy.spatial.transform import Rotation as _Rot

    v = np.asarray(settle, float)
    t = v[:3]
    Rs = _Rot.from_rotvec(np.radians(v[3:6])).as_matrix() if len(v) >= 6 else np.eye(3)
    R2 = np.asarray(R, float) @ Rs.T
    return np.asarray(o, float) - R2 @ t, R2


def contact_site(W, tool_pts, near=0.004):
    """The centroid of the tool's points within `near` of the digit's points
    W (where the digit touches), or None."""
    from scipy.spatial import cKDTree

    T = np.asarray(tool_pts, float)
    d, _ = cKDTree(np.asarray(W, float)).query(T)
    m = d < near
    return T[m].mean(0) if m.any() else None


# --- the planner (HomeHero's venv) ---------------------------------------------------------------

def _link_points(cache, links, per_link=600):
    man = json.loads(Path(cache).with_suffix(".json").read_text())
    data = np.load(Path(cache).with_suffix(".npz"))
    rng = np.random.default_rng(0)
    out = {}
    for i, s in enumerate(man["shapes"]):
        if s["link"] in links:
            p = data[f"p{i}"]
            out.setdefault(s["link"], []).append(p)
    for k, v in out.items():
        P = np.vstack(v)
        out[k] = P[rng.choice(len(P), per_link, replace=False)] if len(P) > per_link else P
    return out


class HandModel:
    """The left hand's links in its base frame, by digit configuration
    (forward kinematics through HomeHero's Geometry once per configuration)."""

    def __init__(self, g, cache, palm="psyonic_left_base"):
        self.g, self.palm = g, palm
        self.links = {d: [f"psyonic_left_{d}_L1", f"psyonic_left_{d}_L2"] for d in DIGITS}
        self.pts = _link_points(cache, [palm] + [x for v in self.links.values() for x in v])
        self.arm = np.asarray(g.rest[:8], float)
        self._cache = {}

    def digit(self, d, q_hand):
        """digit d's link points in the palm frame with the hand at q_hand (6)."""
        key = (d, tuple(np.round(q_hand, 4)))
        if key not in self._cache:
            self.g.forward(np.r_[self.arm, q_hand])
            pb, Rb = self.g.pose(self.palm)
            out = []
            for ln in self.links[d]:
                pl, Rl = self.g.pose(ln)
                out.append((Rb.T @ (pl - pb)) + (Rb.T @ Rl @ self.pts[ln].T).T)
            self._cache[key] = np.vstack(out)
        return self._cache[key]

    def palm_pts(self):
        return self.pts[self.palm]

    def digit_hulls(self, d, q_hand):
        """digit d's link colliders (convex hulls) in the palm frame with the
        hand at q_hand: [(R, p, eq)] - a link's points x (palm frame) are
        inside when eq applied to R.T (x - p) is <= 0."""
        from scipy.spatial import ConvexHull

        key = ("hull", d, tuple(np.round(q_hand, 4)))
        if key not in self._cache:
            if not hasattr(self, "_eq"):
                self._eq = {}
            self.g.forward(np.r_[self.arm, q_hand])
            pb, Rb = self.g.pose(self.palm)
            out = []
            for ln in self.links[d]:
                if ln not in self._eq:
                    self._eq[ln] = ConvexHull(self.pts[ln]).equations
                pl, Rl = self.g.pose(ln)
                out.append((Rb.T @ Rl, Rb.T @ (pl - pb), self._eq[ln]))
            self._cache[key] = out
        return self._cache[key]


def tool_in_hulls(tool_rel, hulls, tol=0.0005):
    """Whether any tool point (in the palm frame) lies inside any of the
    link hulls [(R, p, eq)] deeper than tol: a finger wrapping a narrow
    handle meets it with a face, and no finger vertex enters the handle."""
    T = np.asarray(tool_rel, float)
    for Rl, pl, eq in hulls:
        L = (T - pl) @ Rl
        lo, hi = L.min(0), L.max(0)
        if np.all(L @ eq[:, :3].T + eq[:, 3] <= -tol, axis=1).any():
            return True
    return False


def search_grips(hand, tool, opts):
    """Candidate grips (palm pose in the world, the digits' contact angles)
    ranked by how well they hold the handle and reach the trigger."""
    up = np.asarray(tool["up"], float)
    c0, a_dir = handle_axis(tool["handle_points"], up)
    a, f, l = grip_axes(a_dir, tool["forward"])
    trig = np.asarray(tool["trigger_points"], float)
    trig_c = (trig.max(0) + trig.min(0)) / 2
    pull = np.asarray(tool["pull"], float) / np.linalg.norm(tool["pull"])
    hulls_tool = [h for name, pts in tool["bodies"].items() if name != tool["trigger_body"]
                  for h in slab_hulls(pts, up)]
    hulls_trig = slab_hulls(trig, up, band=0.004, min_points=6)
    swept_hulls = {k_t: slab_hulls(trig + np.asarray(tool["pull"], float) / np.linalg.norm(tool["pull"]) * float(tool.get("travel_m", 0.01)) * k_t,
                                   up, band=0.004, min_points=6) for k_t in (0.0, 0.5, 1.0)}
    handle = np.asarray(tool["handle_points"], float)
    all_tool = np.vstack([np.asarray(v, float) for v in tool["bodies"].values()])
    tool_near = np.vstack([np.asarray(v, float) for k, v in tool["bodies"].items() if k != tool["trigger_body"]])
    tool_near = tool_near[np.random.default_rng(1).choice(len(tool_near), min(len(tool_near), 4000), replace=False)]
    all_tool = all_tool[np.random.default_rng(0).choice(len(all_tool), min(len(all_tool), 6000), replace=False)]
    palm = hand.palm_pts()
    from scipy.spatial import ConvexHull
    palm_eq = ConvexHull(palm).equations                         # the palm's collider: one convex hull
    gap = float(opts.get("palm_gap_m", 0.003))
    touch_lo, touch_hi = opts.get("index_touch_rad", [0.8, 1.25])
    pull_max = float(opts.get("pull_max_rad", 1.6))
    travel_full = float(tool.get("travel_m", 0.01))
    open_hand = np.array([0.1, 0.1, 0.1, 0.1, 0.0, -1.47])
    qs = np.round(np.arange(0.1, 1.741, 0.03), 3)
    out, why = [], {}

    def first_contact(d, base_q, R, o, hulls, index_trigger=False):
        """The least flexion at which digit d's points enter the hulls (binary
        search on a grid; contact is monotone in flexion over this range)."""
        k = {"index": 0, "middle": 1, "ring": 2, "pinky": 3, "thumb": 4}[d]

        near_tool = tool_near if tool_near is not None else None

        def hit(qv):
            qh = base_q.copy(); qh[k] = qv
            W = o + hand.digit(d, qh) @ R.T
            if inside(W, hulls, 0.0005).any():
                return True
            # and the tool's points inside the digit's own convex colliders (a face contact)
            pts_ = (trig if hulls is hulls_trig else near_tool)
            return pts_ is not None and tool_in_hulls((pts_ - o) @ R, hand.digit_hulls(d, qh))
        if not hit(qs[-1]):
            return None
        lo_i, hi_i = 0, len(qs) - 1
        if hit(qs[0]):
            return float(qs[0])
        while hi_i - lo_i > 1:
            mid = (lo_i + hi_i) // 2
            lo_i, hi_i = (lo_i, mid) if hit(qs[mid]) else (mid, hi_i)
        return float(qs[hi_i])

    # where the handle sits in the hand, along the fingers: the Ability Hand's
    # digits close round a point about 10 cm out from the palm's origin (under
    # their proximal links); at 7.5 cm the middle and ring curled past the
    # handle's front edge into the air (the drill's live pose, 2026-10-15)
    depth = float(opts.get("handle_depth_m", 0.10))
    fingers_x = [float(hand.digit(dg, open_hand)[:, 0].mean()) for dg in ("index", "pinky")]
    for yaw in np.radians(opts.get("yaw_deg", [-20, -10, 0, 10, 20])):
        for roll in np.radians(opts.get("roll_deg", [-10, -5, 0, 5, 10])):
            R = left_grip_rotation(a, f, l, yaw, roll)
            for dh in opts.get("along_m", np.round(np.arange(-0.009, 0.0091, 0.003), 4).tolist()):
                for df in (opts.get("forward_m", np.round(np.arange(-0.025, 0.0451, 0.005), 3).tolist()) if not opts.get("seat_at_heel", True) else [0.0]):
                    key = f"yaw {np.degrees(yaw):+.0f} roll {np.degrees(roll):+.0f}"
                    # the index's knuckle at the trigger's height, the palm's face `gap` off the handle
                    idx_x = float(hand.digit("index", open_hand)[:, 0].mean())
                    o = trig_c + R[:, 0] * (-idx_x + dh)                 # along the handle: index at the trigger
                    o = o + R[:, 2] * (((c0 - o) @ R[:, 2]) - depth + df)   # forward: the handle `depth` out along the fingers
                    # across: slide along the palm's normal until the face is gap off the
                    # handle where the fingers wrap it (between the pinky and the index):
                    # off its flare at the battery, the wrapped section sat 1.2 cm out
                    # seated as the close would seat it: from clear of the tool (drawn back
                    # along the palm's normal - a ray cast from inside the palm reads 0),
                    # onto the palm's face, then back toward the wrist until the palm's
                    # slanted face and heel stop it, onto the face again, and so on: on
                    # the rig the handle slid down that slant into the corner at the heel
                    # (0.5 cm off the palm, 3.4 cm toward the wrist; 2026-10-15). Every part
                    # of the tool counts (the barrel and the battery are wider than the
                    # handle; seated off the handle alone they pierced the palm's collider)
                    def _on_palm(o_):
                        far = o_ - R[:, 1] * 0.08
                        rel_ = (all_tool - far) @ R
                        rel_ = rel_[(rel_[:, 0] > palm[:, 0].min() - 0.005) & (rel_[:, 0] < palm[:, 0].max() + 0.005)
                                    & (rel_[:, 2] > palm[:, 2].min() - 0.005) & (rel_[:, 2] < palm[:, 2].max() + 0.005)]
                        t_ = seat_along(palm, rel_, [0.0, -1.0, 0.0]) if len(rel_) else None
                        return None if t_ is None else far + R[:, 1] * (t_ - gap)
                    o = _on_palm(o)
                    if o is None:
                        why["no palm under the tool"] = why.get("no palm under the tool", 0) + 1
                        continue
                    for _ in range(int(opts.get("seat_passes", 4)) if opts.get("seat_at_heel", True) else 0):
                        rel_ = (all_tool - o) @ R
                        rel_ = rel_[(rel_[:, 0] > palm[:, 0].min() - 0.01) & (rel_[:, 0] < palm[:, 0].max() + 0.01)]
                        t_ = seat_along(palm, rel_, [0.0, 0.0, -1.0])
                        if t_ is None or t_ - gap < 0.001:
                            break
                        o_next = _on_palm(o + R[:, 2] * (t_ - gap))
                        if o_next is None:
                            break
                        o = o_next
                    # the open hand (the four fingers and the palm) clear of the tool: hand
                    # points in the tool's hulls, and the tool's points in the palm's own
                    # convex collider (a flat palm is pierced with no palm vertex inside the tool)
                    W_open = np.vstack([o + palm @ R.T] + [o + hand.digit(d, open_hand) @ R.T for d in DIGITS[:4]])
                    if inside(W_open, hulls_tool + hulls_trig, 0.0005).any() or \
                            inside((all_tool - o) @ R, [(palm_eq, palm.min(0), palm.max(0))], 0.0005).any():
                        why["open hand in the tool"] = why.get("open hand in the tool", 0) + 1
                        continue
                    # the wrap: middle, ring, pinky meet the handle
                    wrap = {d: first_contact(d, open_hand, R, o, hulls_tool) for d in ("middle", "ring", "pinky")}
                    wrap_max = float(opts.get("wrap_max_rad", 1.72))             # the grip command closes to 1.74: a contact short of that is a wrap
                    if any(v is None or v > wrap_max for v in wrap.values()):
                        k_ = "no wrap: " + ",".join(dg for dg, v in wrap.items() if v is None or v > wrap_max)
                        why[k_] = why.get(k_, 0) + 1
                        continue
                    # the wrapping digits clear of the trigger and of the space it travels
                    # through: the middle wrapped round the trigger's lower part pressed it
                    # 8 N - the chuck ran before the pull and after the release, and the
                    # trigger stopped at a third of its travel (SDF rig, 2026-10-15)
                    # - along the WHOLE close, not only where it ends: the middle met the trigger
                    # at 0.99 rad on its way to the handle (planned 1.18, halted at 1.28), pressed
                    # it through the lift and held it 2.9 mm in after the release (knuckle take,
                    # 2026-10-15). The close overshoots the planned contact by `wrap_overshoot_rad`
                    blocked = None
                    over = float(opts.get("wrap_overshoot_rad", 0.15))
                    for dg in ("middle", "ring", "pinky"):
                        k_d = {"middle": 1, "ring": 2, "pinky": 3}[dg]
                        for qv in [v for v in qs[::3] if v <= min(1.74, wrap[dg] + over)] + [min(1.74, wrap[dg] + over)]:
                            qh_w = open_hand.copy(); qh_w[k_d] = qv
                            Wd = o + hand.digit(dg, qh_w) @ R.T
                            Hd = hand.digit_hulls(dg, qh_w)
                            for k_t in (0.0, 0.5, 1.0):
                                T_k = trig + pull * travel_full * k_t
                                if inside(Wd, swept_hulls[k_t], 0.0005).any() or tool_in_hulls((T_k - o) @ R, Hd):
                                    blocked = dg
                                    break
                            if blocked:
                                break
                        if blocked:
                            break
                    if blocked:
                        why[f"{blocked} on the trigger"] = why.get(f"{blocked} on the trigger", 0) + 1
                        continue
                    # the index: the trigger first, not the body
                    q_trig = first_contact("index", open_hand, R, o, hulls_trig)
                    q_body = first_contact("index", open_hand, R, o, hulls_tool)
                    if q_trig is None or not (touch_lo <= q_trig <= touch_hi) or (q_body is not None and q_body <= q_trig + 0.06):
                        why["index misses the trigger"] = why.get("index misses the trigger", 0) + 1
                        continue
                    # the finger within the trigger's height (up the handle, the hand's x):
                    # riding on its bottom edge, the finger's lower edge pressed the handle's
                    # face under the trigger and stalled there with 0.75 mm of travel (rig, 2026-10-15)
                    qh_t = open_hand.copy(); qh_t[0] = q_trig
                    fx = hand.digit("index", qh_t)[:, 0]
                    tx = (trig - o) @ R[:, 0]
                    edge = float(opts.get("finger_edge_m", 0.002))
                    if fx.min() < tx.min() + edge or fx.max() > tx.max() - edge:
                        why["index off the trigger's height"] = why.get("index off the trigger's height", 0) + 1
                        continue
                    centred = abs((fx.min() + fx.max()) / 2 - (tx.min() + tx.max()) / 2)
                    # the pull: the finger presses the trigger in until it meets the
                    # body itself (the trigger slides into a slot in the handle's front:
                    # the finger bottoms out on the handle at the end of the travel)
                    q_stop = q_body if q_body is not None else pull_max
                    qh = open_hand.copy(); qh[0] = q_stop
                    travel = trigger_travel(o + hand.digit("index", qh) @ R.T, trig, pull)
                    if travel < float(opts.get("min_travel_fraction", 0.6)) * float(tool.get("travel_m", 0.01)):
                        why["short pull"] = why.get("short pull", 0) + 1
                        continue
                    # robust to the tool settling in the hand: the close draws the handle
                    # in and the lift lets it sag (measured on the drill: 1.1 cm down the
                    # handle, 0.5 cm off the palm, 1.6 cm toward the wrist), and a grip
                    # whose index met the trigger 0.15 rad before the body met the body
                    # first once it had (drill rig, 2026-10-15). Each shift (the hand's
                    # frame) moves the hand by its opposite against the tool
                    worst = q_body - q_trig if q_body is not None else 0.5
                    touch_min = q_trig
                    shifted_ok = True
                    for sh in opts.get("settle_shifts_m", []):
                        o_s, R_s = settled_hand(o, R, sh)
                        qt_s = first_contact("index", open_hand, R_s, o_s, hulls_trig)
                        qb_s = first_contact("index", open_hand, R_s, o_s, hulls_tool)
                        if qt_s is None or (qb_s is not None and qb_s <= qt_s + 0.03):
                            shifted_ok = False
                            break
                        qh_s = open_hand.copy(); qh_s[0] = qb_s if qb_s is not None else pull_max
                        if trigger_travel(o_s + hand.digit("index", qh_s) @ R_s.T, trig, pull) < float(opts.get("min_travel_fraction", 0.6)) * travel_full:
                            shifted_ok = False
                            break
                        worst = min(worst, (qb_s - qt_s) if qb_s is not None else 0.5)
                        touch_min = min(touch_min, qt_s)
                    if not shifted_ok:
                        why["index fails once the tool settles"] = why.get("index fails once the tool settles", 0) + 1
                        continue
                    # the index's strength along the trigger's travel: its motor's stall
                    # torque (at the hand's collision voltage) over the lever at the
                    # touching points. At its fingertip pad the index made ~8 N against
                    # the trigger's 15 N spring and stalled at half its travel (drill
                    # take 4: the critic's full_trigger_travel, 2026-10-15); a stiff
                    # trigger is taken nearer the finger's first joint, where it is stronger
                    force_frac = None
                    k_spring = float(tool.get("trigger_spring_n_per_m", 0.0))
                    torque = float(tool.get("finger_torque_nm", 0.0))
                    tfaces = tool.get("trigger_faces")
                    if k_spring > 0 and torque > 0:
                        from scipy.spatial import cKDTree
                        FC0 = np.asarray(tfaces["c"], float) if tfaces else None
                        FN0 = np.asarray(tfaces["n"], float) if tfaces else None
                        FA0 = np.asarray(tfaces["area"], float) if tfaces else None

                        def _travel_at(qv, o_=o, R=R):
                            qh_ = open_hand.copy(); qh_[0] = qv
                            return min(trigger_travel(o_ + hand.digit("index", qh_) @ R.T, trig, pull), travel_full)

                        def _force_at(qv, o_=o, R=R):
                            qh0 = open_hand.copy(); qh0[0] = qv
                            qh1 = qh0.copy(); qh1[0] = qv + 0.01
                            P0, P1 = hand.digit("index", qh0), hand.digit("index", qh1)
                            W0 = o_ + P0 @ R.T
                            t_ = _travel_at(qv, o_, R)
                            dist = cKDTree(trig + pull * t_).query(W0)[0]
                            m_ = dist < 0.004
                            if not m_.any():
                                m_ = dist <= dist.min() + 1e-9
                            v_ = ((P1 - P0) / 0.01)[m_].mean(0) @ R.T             # world, per rad
                            if FC0 is None:
                                lever = push_lever(P0, P1, 0.01, m_, R.T @ pull)
                                return torque / lever if lever > 1e-4 else 0.0
                            FC = FC0 + pull * t_
                            sel = np.zeros(len(FC), bool)
                            for pt in W0[m_]:
                                sel |= np.linalg.norm(FC - pt, axis=1) < 0.008
                            if not sel.any():
                                return 0.0
                            n_ = (FN0[sel] * FA0[sel, None]).sum(0)
                            return push_force(torque, v_, n_, pull)
                        # where the grip puts the tool and where it settles in the hand (the
                        # take's index met the trigger 1 cm lower once the drill had settled:
                        # 19% of the travel planned there, 50% reached)
                        fracs = []
                        # at_settled: the force counted only where the tool comes to rest (a
                        # take's measured settle, settle_shifts_m), the grip's own pose being
                        # where it starts, not where the index pulls
                        for sh in ([] if opts.get("force_at_settled_only") else [None]) + list(opts.get("settle_shifts_m", [])):
                            o_f, R_f = (o, R) if sh is None else settled_hand(o, R, sh)
                            q0 = q_trig if sh is None else (first_contact("index", open_hand, R_f, o_f, hulls_trig) or q_trig)
                            qb = q_body if sh is None else first_contact("index", open_hand, R_f, o_f, hulls_tool)
                            q_end = qb if qb is not None else pull_max
                            fracs.append(force_reach(lambda qv, o_=o_f, R_=R_f: _travel_at(qv, o_, R_), lambda qv, o_=o_f, R_=R_f: _force_at(qv, o_, R_),
                                                     np.arange(q0, q_end + 1e-9, 0.03), k_spring, travel_full) / travel_full)
                        force_frac = min(fracs)
                        if force_frac < float(opts.get("min_force_travel_fraction", 0.0)):
                            why["index too weak for the trigger"] = why.get("index too weak for the trigger", 0) + 1
                            continue
                    # the thumb: a rotation whose open thumb clears the tool (it is held
                    # at that rotation from the slide in on) and whose close meets it
                    thumb = None
                    for rot in opts.get("thumb_rot", [-1.47, -1.2, -0.9, -0.6, -0.3, 0.0]):
                        base = open_hand.copy(); base[5] = rot
                        if inside(o + hand.digit("thumb", base) @ R.T, hulls_tool + hulls_trig, 0.0005).any():
                            continue
                        if opts.get("thumb", "wrap") == "off":
                            # the thumb kept out of the grip, clear of the tool: closed on the
                            # barrel's top it pressed the drill down with 13-17 N and the
                            # fingers had that to hold as well as its weight (drill take 3)
                            thumb = (rot, None)
                            break
                        qt = first_contact("thumb", base, R, o, hulls_tool)
                        if qt is None or qt > 1.5:
                            continue
                        if opts.get("thumb") == "wrap_handle":
                            # a person's thumb closes round the handle's far side, under the
                            # housing, so the hand is a ring round the handle; the first thumb
                            # the search found closed on the barrel's TOP and pressed the drill
                            # down 13-17 N (drill take 3), and the critic's thumb_opposes
                            # failed the flat thumb that replaced it (2026-10-15)
                            qh_t = base.copy(); qh_t[4] = qt + 0.03
                            site = contact_site(o + hand.digit("thumb", qh_t) @ R.T, tool_near, near=0.004)
                            if site is None:
                                continue
                            h_ = float((site - c0) @ a)
                            top_ = float(((trig - c0) @ a).max())
                            across_ = float((site - c0) @ R[:, 1])
                            if h_ > top_:
                                why["thumb on the housing"] = why.get("thumb on the housing", 0) + 1
                                continue
                            if across_ < float(opts.get("thumb_far_side_m", 0.0)):
                                why["thumb on the near side"] = why.get("thumb on the near side", 0) + 1
                                continue
                        thumb = (rot, qt)
                        break
                    if thumb is None:
                        why["no thumb"] = why.get("no thumb", 0) + 1
                        continue
                    score = abs(q_trig - 1.0) + sum(abs(v - 1.0) for v in wrap.values()) / 3 + 0.3 * abs(yaw) + 0.3 * abs(roll) \
                        + 30 * max(0.0, travel_full - travel) - 2.0 * min(worst, 0.3) + 20.0 * centred \
                        + (float(opts.get("force_score_weight", 2.0)) * (1.0 - force_frac) if force_frac is not None else 0.0)
                    out.append(dict(R=R, o=o, yaw_deg=round(float(np.degrees(yaw)), 1), roll_deg=round(float(np.degrees(roll)), 1),
                                    along_m=dh, forward_m=df, wrap=wrap, index_trigger_rad=q_trig, index_touch_min_rad=touch_min, index_body_rad=q_body,
                                    index_stop_rad=q_stop, travel_at_stop_m=round(min(travel, travel_full), 4), settled_margin_rad=round(float(worst), 3),
                                    thumb_rot=thumb[0], thumb_contact_rad=thumb[1],
                                    force_travel_fraction=None if force_frac is None else round(float(force_frac), 3),
                                    score=round(float(score), 4)))
    out.sort(key=lambda c: c["score"])
    # the incumbent: the grip the critic loop measured best so far is tried first
    # while it still passes every check - a new ranking does not replace a measured
    # grip until a take beats it (take 7's top-ranked grip pulled the trigger 15%
    # against the incumbent's 50%, 2026-10-15)
    inc = opts.get("incumbent")
    if inc:
        def _is_inc(c):
            return all(abs(float(c[k]) - float(inc[k])) <= 1e-6 for k in ("yaw_deg", "roll_deg", "along_m") if k in inc)
        out.sort(key=lambda c: (not _is_inc(c), c["score"]))
        if out and _is_inc(out[0]):
            out[0]["incumbent"] = True
    return out, {"rejected": why, "handle_axis": a.round(3).tolist(), "forward": f.round(3).tolist(), "left": l.round(3).tolist(),
                 "handle_point": c0.round(4).tolist(), "trigger_centre": trig_c.round(4).tolist(), "pull": pull.round(3).tolist(),
                 "tool_hulls": len(hulls_tool), "trigger_hulls": len(hulls_trig)}


def plan_tool_grip(req, g, checker, start, lower, upper, action_step, slide_bounds, make_checker):
    """The whole plan from `start` (14). make_checker(extra_boxes) -> a
    Checker with the request's obstacles plus those boxes."""
    from scipy.spatial.transform import Rotation

    from aida_r5_motion import joint_path, screen
    from pinch_place_planner import CACHE

    task = req["task"]
    tool = task["tool"]
    opts = task.get("tool_grip", {})
    hand = HandModel(g, CACHE)
    cands, frame = search_grips(hand, tool, opts)
    if not cands:
        return {"passed": False, "reason": "no grip candidate: none wrapped the handle, met the trigger first and cleared the tool open",
                "frame": frame}
    step = np.asarray(action_step, float)
    up = np.asarray(tool["up"], float)
    hulls_tool = [h for name, pts in tool["bodies"].items() for h in slab_hulls(pts, up)]
    arm_pts = _link_points(CACHE, [f"amber_left_arm_link_{k}" for k in (5, 6, 7)], per_link=400)
    tool_boxes = []
    for name, pts in tool["bodies"].items():
        P = np.asarray(pts, float)
        tool_boxes.append(dict(id=f"tool:{name}", min=(P.min(0) - 0.01).tolist(), max=(P.max(0) + 0.01).tolist()))
    reach_checker = make_checker(tool_boxes)

    def arm_clear_of_tool(q):
        """The forearm and wrist links (not the hand: it is meant to touch) out of the tool."""
        g.forward(q)
        for ln, P in arm_pts.items():
            p, R = g.pose(ln)
            if inside(p + P @ R.T, hulls_tool, 0.001).any():
                return False
        return True

    seeds = [np.asarray(start, float)] + [np.r_[np.asarray(s, float)[:8], start[8:]] for s in task.get("ik_seeds", [])]
    # and seeds scattered about those: least squares from one seed lands in one
    # basin, and the two best grips had no IK from the start or the reset arm
    # (the drill's live pose, 2026-10-15)
    rng = np.random.default_rng(int(opts.get("ik_seed", 20261015)))
    lo_b, hi_b = np.asarray(g.bounds, float)[:, 0], np.asarray(g.bounds, float)[:, 1]
    base_seeds = list(seeds)
    for k in range(int(opts.get("ik_seeds", 32))):
        b = base_seeds[k % len(base_seeds)].copy()
        b[:8] = np.clip(b[:8] + rng.normal(0.0, 0.3 + 0.05 * k, 8), lo_b, hi_b)
        seeds.append(b)
    tried = []
    for cand in cands[:int(opts.get("max_candidates", 12))]:
        R, o = cand["R"], cand["o"]
        # the palm's pose: g.pose() is the palm (psyonic_left_base) frame; the
        # hand open, its thumb already at the grip's rotation
        hand_open = np.array([0.1, 0.1, 0.1, 0.1, 0.0, cand["thumb_rot"]])
        q_grip = None
        ik_why = {"ik": 0, "table or robot": 0, "forearm in the tool": 0}
        best_err = None
        for s in seeds:
            q_, fit = g.solve(np.r_[s[:8], hand_open], o, R)
            if not fit["passed"]:
                ik_why["ik"] += 1
                e = (round(fit["position_error_m"] * 1000, 1), round(float(np.degrees(fit.get("angle_error_rad", 0.0))), 1))
                best_err = e if best_err is None or e < best_err else best_err
                continue
            if not checker(q_):
                ik_why["table or robot"] += 1
                hits_ = checker(q_, True)[:2]
                ik_why.setdefault("pairs", set()).update(f"{a.split('/')[-2]}~{b.split('/')[-1] if '/' in b else b}" for a, b in hits_)
                continue
            if not arm_clear_of_tool(q_):
                ik_why["forearm in the tool"] += 1
                continue
            q_grip = q_
            break
        if q_grip is None:
            if "pairs" in ik_why:
                ik_why["pairs"] = sorted(ik_why["pairs"])[:6]
            tried.append({"candidate": cand["score"], "rejected": f"no IK clear of the table, the robot and the tool: {ik_why}, best IK error (mm, deg) {best_err}"})
            continue
        back = float(task.get("approach_m", 0.07))
        o_st = o - R[:, 1] * back + up * 0.01
        q_st, fit = g.solve(q_grip, o_st, R)
        if not (fit["passed"] and reach_checker(q_st)):
            tried.append({"candidate": cand["score"], "rejected": "the staging pose beside the handle has no clear IK"})
            continue
        # the straight slide in, screened against the table and robot, the arm against the tool, the open hand against the tool
        ok, q_ = True, q_st.copy()
        line = []
        n = max(1, int(np.ceil(np.linalg.norm(o - o_st) / 0.005)))
        for fr in np.linspace(0, 1, n + 1)[1:]:
            q_, fit = g.solve(q_, o_st * (1 - fr) + o * fr, R)
            if not (fit["passed"] and checker(q_) and arm_clear_of_tool(q_)):
                ok = False
                break
            g.forward(q_)
            pb, Rb = g.pose()
            W = np.vstack([pb + hand.palm_pts() @ Rb.T] + [pb + hand.digit(d, hand_open) @ Rb.T for d in DIGITS[:4]]
                          + [pb + hand.digit("thumb", hand_open) @ Rb.T])
            if inside(W, hulls_tool, 0.0005).any():
                ok = False
                break
            line.append(q_.copy())
        if not ok:
            tried.append({"candidate": cand["score"], "rejected": f"the slide in is blocked at {fr:.2f} of the way"})
            continue
        # the reach: the hand opened where it is, then the RRT to the staging pose
        path, stages = [np.asarray(start, float)], ["start"]

        def walk(goal, label, hand_free=False, scale=1.0):
            q0 = path[-1].copy()
            s_ = step * scale
            if hand_free:
                s_ = s_.copy(); s_[8:] = np.inf
            n_ = int(np.ceil(np.max(np.abs(goal - q0) / s_))) if np.any(goal != q0) else 0
            n_ = max(n_, 1) if hand_free else n_
            for fr_ in np.linspace(0, 1, n_ + 1)[1:]:
                q1 = q0 * (1 - fr_) + goal * fr_
                if hand_free:
                    q1[8:] = goal[8:]
                path.append(q1); stages.append(label)

        def hold(seconds, label):
            for _ in range(int(seconds * HZ)):
                path.append(path[-1].copy()); stages.append(label)

        walk(np.r_[path[-1][:8], hand_open], "preshape")
        try:
            dense, info = joint_path(reach_checker, path[-1][:8], q_st[:8], hand_open, timeout=float(task.get("rrt_timeout_s", 120)))
        except Exception as e:                                   # noqa: BLE001 - the RRT's own failure, recorded
            tried.append({"candidate": cand["score"], "rejected": f"reach: {e}"})
            continue
        for qa in dense:
            walk(np.r_[np.asarray(qa, float)[:8], hand_open], "reach")
        walk(np.r_[q_st[:8], hand_open], "reach")
        for q_l in line:                                          # the slide, at a third of the arm's rate
            walk(np.r_[q_l[:8], hand_open], "approach", scale=0.35)
        # the grip command: the middle, ring, pinky and the thumb's flexor
        # closed (the firmware stops each on the handle), the index to just
        # short of the trigger
        # short of the EARLIEST touch: where the grip puts the trigger and where the tool
        # settles in the hand (the knuckle take's index waited 0.08 short of the planned
        # touch, and the drill settled toward it)
        q_pre = max(0.1, cand.get("index_touch_min_rad", cand["index_trigger_rad"]) - float(opts.get("index_short_rad", 0.08)))
        q_close = path[-1].copy(); q_close[9:12] = 1.74; q_close[8] = q_pre
        if cand["thumb_contact_rad"] is not None:
            q_close[12] = 1.74
        walk(q_close, "close", hand_free=True)
        hold(float(task.get("close_s", 1.0)), "close")
        # the lift: the shoulder slide alone (HomeHero's rule), 0.4 mm per tick
        top = min(float(slide_bounds[1]), float(upper[0]))
        q_l = path[-1].copy()
        goal_z = min(q_l[0] + float(task.get("lift_m", 0.08)), top)
        while q_l[0] < goal_z - 1e-9:
            q_l = q_l.copy(); q_l[0] = min(q_l[0] + 0.0004, goal_z)
            path.append(q_l); stages.append("lift")
        lifted = float(q_l[0] - q_close[0])
        # the rest of the lift by the arm, the palm straight up (its turn held),
        # IK per 5 mm, screened with the hand as it is: the slide is near its
        # reachable top when the hand is at a table's height (1.8 cm left)
        want = float(task.get("lift_m", 0.08))
        thumb_seen = (cand["thumb_contact_rad"] - 0.02) if cand["thumb_contact_rad"] is not None else 0.0
        hand_seen = np.r_[cand["index_stop_rad"] - 0.1, [cand["wrap"][d] - 0.02 for d in ("middle", "ring", "pinky")],
                          thumb_seen, cand["thumb_rot"]]
        if want - lifted > 0.005:
            g.forward(path[-1])
            p0, R0 = g.pose()
            n = int(np.ceil((want - lifted) / 0.005))
            q_a = path[-1].copy()
            arm_ok = True
            for k in range(1, n + 1):
                q_a, fit = g.solve(np.r_[q_a[:8], hand_seen], p0 + up * (want - lifted) * k / n, R0)
                if not (fit["passed"] and checker(q_a)):
                    arm_ok = False
                    break
                walk(np.r_[q_a[:8], path[-1][8:]], "lift", scale=0.5)
            g.forward(path[-1])
            lifted = float(g.pose()[0][2] - p0[2]) + float(q_l[0] - q_close[0])
            if not arm_ok and lifted < 0.05:
                tried.append({"candidate": cand["score"], "rejected": f"the lift stops at {lifted * 100:.1f} cm (IK or a collision)"})
                continue
        hold(0.5, "settle")
        # the pull: the index driven toward pull_max - it stalls on the trigger's
        # spring and its worm holds it there; the run; the release
        q_pull = path[-1].copy(); q_pull[8] = float(opts.get("pull_max_rad", 1.6))
        walk(q_pull, "pull")
        hold(float(task.get("run_s", 2.0)), "run")
        # the release: the index back to where it waited (its curl is part of the grip:
        # opened all the way, the drill slid 12 mm and dropped 8 mm in the hand, take 8;
        # the trigger still held in after take 5's release was the MIDDLE on it, which
        # the whole-close rule above now keeps off, 2026-10-15)
        q_rel = path[-1].copy(); q_rel[8] = float(opts.get("index_release_rad", q_pre))
        walk(q_rel, "release")
        hold(float(task.get("hold_s", 1.5)), "hold")
        arr = np.asarray(path)
        # the ticks screened against the table and the robot with the hand as
        # it will BE: the grip command drives the digits to closed, the
        # firmware stops each on the handle (at its swept contact), the index
        # stops on the trigger and then the body; screened at the command the
        # digits went through each other and the palm (the can's close too)
        seen = []
        for q_, s_ in zip(arr, stages):
            q2 = q_.copy()
            if s_ in ("close", "lift", "settle", "pull", "run", "release", "hold"):
                q2[9], q2[10], q2[11] = (min(q_[9 + k], cand["wrap"][d] - 0.02) for k, d in enumerate(("middle", "ring", "pinky")))
                q2[12] = min(q_[12], cand["thumb_contact_rad"] - 0.02) if cand["thumb_contact_rad"] is not None else q_[12]
                q2[8] = min(q_[8], cand["index_stop_rad"] - 0.02)
            seen.append(q2)
        sc = screen(checker, [q for q, s_ in zip(seen, stages) if s_ in ("preshape", "reach", "approach", "close", "lift")])
        if not sc["passed"]:
            tried.append({"candidate": cand["score"], "rejected": f"screen: {sc['collisions'][:1]}"})
            continue
        g.forward(q_grip)
        pb, Rb = g.pose()
        return {"passed": True, "targets": arr, "stages": stages, "grip": {k: v for k, v in cand.items() if k not in ("R", "o")},
                "palm": {"p": pb.round(4).tolist(), "q_xyzw": Rotation.from_matrix(Rb).as_quat().round(5).tolist()},
                "index_pre_rad": round(q_pre, 3), "lifted_m": round(lifted, 4), "frame": frame, "rrt": info,
                "candidates": len(cands), "tried": tried, "screen": {k: sc[k] for k in ("passed", "waypoints")}}
    return {"passed": False, "reason": f"{len(cands)} grip candidates, none planned", "tried": tried[:12], "frame": frame,
            "best": [{k: v for k, v in c.items() if k not in ("R", "o")} for c in cands[:3]]}
