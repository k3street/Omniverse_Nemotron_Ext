#!/usr/bin/env python3
"""Material interactions: what a tool does TO a workpiece, as a law run every
physics step and an outcome the scene keeps (a hole a later task can use).

drill_cut - a twist drill cutting wood: the thrust the bit presses with sets
    the chip it takes (below the chisel edge's threshold it only rubs); the
    feed per turn advances the hole's bottom, and the chip sets the torque
    against the chuck. Linear in the uncut chip thickness, as measured by
    Szwajka & Trzepiecinski (2018, BioResources 13(4) 8263-8278, Fig. 6:
    MDF of 742 kg/m3, a two-blade 9.3 mm drill, feeds 0.1-0.5 mm/rev at
    0.48 m/s), scaled to the bit (edge length ~ d, torque ~ d^2) and to the
    wood's density (reasoned: denser wood, more energy to cut).

hole_pieces - a board with a hole in it as convex pieces (PhysX's convex
    colliders are exact for them): a ring of N prisms between a polygon
    circumscribed about the hole and the board's outline, through the
    board or to a depth with the rest of the board behind it.
"""
from __future__ import annotations

import math

import numpy as np

# the measured line (vc 0.48 m/s; Fig. 6b, read at h = 0.05 and 0.25 mm per blade):
# thrust 65 -> 160 N, torque 0.135 -> 0.39 N m
_REF = {"d_mm": 9.3, "rho": 742.0, "blades": 2,
        "f0_n": 41.0, "kf_n_per_mm": 475.0,        # thrust = f0 + kf * h (h: chip per blade, mm)
        "m0_nm": 0.071, "km_nm_per_mm": 1.275,     # torque = m0 + km * h
        "h_max_mm": 0.25}                          # the largest chip measured (0.5 mm/rev)

DRILL_SOURCES = {
    "cutting": "Szwajka K., Trzepiecinski T. (2018) On the machinability of medium density fiberboard by drilling. "
               "BioResources 13(4) 8263-8278, Fig. 6 (thrust and torque vs uncut chip thickness, 9.3 mm two-blade drill, MDF 742 kg/m3)",
    "density_birch_plywood": "Latvijas Finieris Riga Ply EPD: 710 kg/m3 average",
    "scaling": "reasoned: thrust with the cutting edge's length (d), torque with d^2, both with the wood's density",
}


def drill_coefficients(bit_d_m: float, density_kg_m3: float, blades: int = 2) -> dict:
    """The law's line for this bit in this wood (forces N, torques N m, chip mm per blade)."""
    s = (bit_d_m * 1000.0 / _REF["d_mm"]) * (density_kg_m3 / _REF["rho"])
    sm = (bit_d_m * 1000.0 / _REF["d_mm"]) ** 2 * (density_kg_m3 / _REF["rho"])
    return {"f0_n": _REF["f0_n"] * s, "kf_n_per_mm": _REF["kf_n_per_mm"] * s,
            "m0_nm": _REF["m0_nm"] * sm, "km_nm_per_mm": _REF["km_nm_per_mm"] * sm,
            "h_max_mm": _REF["h_max_mm"], "blades": int(blades)}


def drill_cut(state: dict, thrust_n: float, dphi_rad: float, coeff: dict, thickness_m: float) -> float:
    """One physics step of a drill bit at the bottom of its hole: `thrust_n`
    the bit's push on the uncut wood (the plug's contact), `dphi_rad` the
    chuck's turn this step (positive cuts; reverse only rubs). Advances the
    hole by the feed the thrust buys and returns the wood's torque against
    the turning (N m, a magnitude). `state` keeps {'depth_m', 'through',
    'turns', 'chip_mm' (last), 'cut_m3'}; through the board the bit cuts air."""
    state.setdefault("depth_m", 0.0)
    state.setdefault("through", False)
    state.setdefault("turns", 0.0)
    state.setdefault("chip_mm", 0.0)
    state.setdefault("cut_m3", 0.0)
    state.setdefault("bit_d_m", None)
    if state["through"] or thrust_n <= 0.0:
        state["chip_mm"] = 0.0
        return 0.0
    if dphi_rad <= 0.0:
        state["chip_mm"] = 0.0
        return coeff["m0_nm"] if dphi_rad < 0.0 else 0.0
    h = min(max((thrust_n - coeff["f0_n"]) / coeff["kf_n_per_mm"], 0.0), coeff["h_max_mm"])
    feed_m = coeff["blades"] * h / 1000.0 * dphi_rad / (2 * math.pi)
    state["turns"] += dphi_rad / (2 * math.pi)
    state["chip_mm"] = h
    d = state["depth_m"] + feed_m
    if d >= thickness_m:
        d, state["through"] = thickness_m, True
    if state["bit_d_m"]:
        state["cut_m3"] += math.pi * state["bit_d_m"] ** 2 / 4 * (d - state["depth_m"])
    state["depth_m"] = d
    # rubbing below the chisel edge's threshold: the torque's intercept only
    return coeff["m0_nm"] + coeff["km_nm_per_mm"] * h


def embedment_strength_n_mm2(bit_d_m: float, density_kg_m3: float) -> float:
    """The wood's bearing strength against a dowel in its own hole (N/mm2):
    EN 1995-1-1's pre-drilled form f_h = 0.082 (1 - 0.01 d) rho (d mm, rho
    kg/m3; as quoted by RoyMech's EN 1995 timber-connection notes), taken with
    the board's mean density for its mean strength."""
    return 0.082 * (1.0 - 0.01 * bit_d_m * 1000.0) * density_kg_m3


def bit_reaction(tip, tip_prev, dt: float, face, n, entry, bottom_m: float, k: float, c: float, cutting: bool = True,
                 bearing_n_per_m: float | None = None):
    """The wood's push on a bit at one physics step, as damped springs: on the
    uncut wood under its tip (the face before the hole starts, the cut's bottom
    after; through the board, nothing), and - once in its hole - the wall
    holding it to the line it entered on - up to what the wood bears over the
    depth the bit is in (bearing_n_per_m: embedment strength x bit diameter);
    past it the wall crushes and the hole's line yields toward the bit. A
    linear wall read a 14 mm jolt on a bit 0.3 mm into the face as 280 N
    (2026-10-17). The tip's velocity is its own
    position's change: a link's linear velocity is not at its origin, and
    taken there with the chuck's spin a drill's tip read 1.7 m/s sideways and
    the wall's damping threw the drill out of the hand (2026-10-17).
    Returns (force on the tip (3,), thrust along n, lateral offset m, the
    hole line's yield (3,): add it to the entry)."""
    tip, n = np.asarray(tip, float), np.asarray(n, float)
    vt = (tip - np.asarray(tip_prev, float)) / dt if tip_prev is not None else np.zeros(3)
    into = -n
    depth_tip = float((np.asarray(face, float) - tip) @ n)
    F = np.zeros(3)
    thrust = 0.0
    if cutting:
        pen = depth_tip - bottom_m
        if pen > 0.0:
            thrust = max(0.0, k * pen + c * float(vt @ into))
            F += n * thrust
    lat_m = 0.0
    shift = np.zeros(3)
    if entry is not None:
        rel = tip - np.asarray(entry, float)
        lat_v = rel - into * float(rel @ into)
        lat_m = float(np.linalg.norm(lat_v))
        if depth_tip > 0.0 and lat_m > 1e-9:
            u = lat_v / lat_m
            f = max(0.0, k * lat_m + c * float(vt @ u))
            if bearing_n_per_m is not None:
                cap = bearing_n_per_m * depth_tip
                if k * lat_m > cap:
                    shift = u * (lat_m - cap / k)                 # crushed: the hole's line follows the bit
                f = min(f, cap)
            F += -u * f
    return F, thrust, lat_m, shift


def _ray_to_box(px: float, py: float, ang: float, a: float, b: float) -> tuple[float, float]:
    c, s = math.cos(ang), math.sin(ang)
    ts = []
    if c > 1e-12:
        ts.append((a - px) / c)
    elif c < -1e-12:
        ts.append((-a - px) / c)
    if s > 1e-12:
        ts.append((b - py) / s)
    elif s < -1e-12:
        ts.append((-b - py) / s)
    t = min(ts)
    return px + t * c, py + t * s


def ring_polygons(px: float, py: float, r: float, a: float, b: float, sides: int = 16) -> list[list[tuple[float, float]]]:
    """The board's face (half-sizes a, b, centred on 0) less a regular polygon
    circumscribed about the circle (px, py, r), as `sides` convex polygons:
    each between one side of the polygon, the two rays out through its ends
    and the outline (with the corner the rays enclose)."""
    if abs(px) + r / math.cos(math.pi / sides) >= a or abs(py) + r / math.cos(math.pi / sides) >= b:
        raise ValueError("the hole reaches the board's edge")
    R = r / math.cos(math.pi / sides)
    corners = [(a, b), (-a, b), (-a, -b), (a, -b)]
    polys = []
    for k in range(sides):
        t0, t1 = 2 * math.pi * k / sides, 2 * math.pi * (k + 1) / sides
        v0 = (px + R * math.cos(t0), py + R * math.sin(t0))
        v1 = (px + R * math.cos(t1), py + R * math.sin(t1))
        e0, e1 = _ray_to_box(px, py, t0, a, b), _ray_to_box(px, py, t1, a, b)
        inside = []
        for cx, cy in corners:
            ang = math.atan2(cy - py, cx - px) % (2 * math.pi)
            if t0 < ang < t1:
                inside.append((ang, (cx, cy)))
        outer = [e1] + [c for _, c in sorted(inside, key=lambda x: -x[0])] + [e0]
        polys.append([v0, v1] + outer)
    return polys


def is_convex(poly) -> bool:
    P = np.asarray(poly, float)
    n = len(P)
    cr = [np.cross(P[(i + 1) % n] - P[i], P[(i + 2) % n] - P[(i + 1) % n]) for i in range(n)]
    cr = [c for c in cr if abs(c) > 1e-12]
    return all(c > 0 for c in cr) or all(c < 0 for c in cr)


def hole_pieces(board: dict, hole: dict, sides: int = 16) -> list[dict]:
    """The board with its hole as convex prisms in the world. `board`: centre,
    axes u, v (its face) and n (out of the drilled face), half-sizes a, b and
    thickness t. `hole`: entry point (on the face), radius r, depth_m (to the
    board's back face when through). Returns [{name, points (N,3), faces:
    [[i..]], role}]: the ring (to the hole's depth) and, for a blind hole,
    the board behind its bottom."""
    c = np.asarray(board["centre"], float)
    u, v, n = (np.asarray(board[k], float) for k in ("u", "v", "n"))
    a, b, t = float(board["a"]), float(board["b"]), float(board["t"])
    p = np.asarray(hole["entry"], float) - c
    px, py = float(p @ u), float(p @ v)
    depth = min(float(hole["depth_m"]), t)
    z_front, z_bottom, z_back = t / 2, t / 2 - depth, -t / 2

    def W(x, y, z):
        return (c + x * u + y * v + z * n).tolist()

    def prism(poly2, z0, z1, name, role):
        k = len(poly2)
        if sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly2, poly2[1:] + poly2[:1])) < 0:
            poly2 = poly2[::-1]                                     # counter-clockwise: the caps face out
        pts = [W(x, y, z0) for x, y in poly2] + [W(x, y, z1) for x, y in poly2]
        faces = [list(range(k))[::-1], list(range(k, 2 * k))] + [[i, (i + 1) % k, k + (i + 1) % k, k + i] for i in range(k)]
        return {"name": name, "points": pts, "faces": faces, "role": role}

    out = []
    if depth > 1e-6:
        for i, poly in enumerate(ring_polygons(px, py, float(hole["r"]), a, b, sides)):
            out.append(prism(poly, z_bottom, z_front, f"Ring{i:02d}", "ring"))
    behind = [(-a, -b), (a, -b), (a, b), (-a, b)]
    if z_bottom - z_back > 1e-6:
        out.append(prism(behind, z_back, z_bottom, "Behind", "behind"))
    return out


def plug_polygon(r: float, sides: int = 16) -> list[tuple[float, float]]:
    """The uncut wood in the hole: the ring's own polygon (it fills the hole exactly)."""
    R = r / math.cos(math.pi / sides)
    return [(R * math.cos(2 * math.pi * k / sides), R * math.sin(2 * math.pi * k / sides)) for k in range(sides)]


def board_frame(size, centre, out) -> dict:
    """An axis-aligned box fixture (size along world x, y, z) drilled on its face
    `out` (a world axis, signed) as hole_pieces' board."""
    size, centre, out = (np.asarray(x, float) for x in (size, centre, out))
    k = int(np.argmax(np.abs(out)))
    n = np.zeros(3); n[k] = math.copysign(1.0, out[k])
    others = [i for i in range(3) if i != k]
    u = np.zeros(3); u[others[0]] = 1.0
    v = np.cross(n, u)
    return {"centre": centre.tolist(), "u": u.tolist(), "v": v.tolist(), "n": n.tolist(),
            "a": float(size[others[0]] / 2), "b": float(size[others[1]] / 2), "t": float(size[k])}
