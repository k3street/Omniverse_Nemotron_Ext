"""A drill bit cutting wood (material_laws.drill_cut) and the board with the
hole it leaves (material_laws.hole_pieces): the law reproduces its source's
measured thrust and torque, the feed follows the thrust, and the hole's
pieces are convex and leave exactly the hole empty."""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from material_laws import (board_frame, drill_coefficients, drill_cut, hole_pieces, is_convex,  # noqa: E402
                           ring_polygons)


def test_the_law_reproduces_the_measured_mdf_line():
    c = drill_coefficients(0.0093, 742.0)
    # Szwajka & Trzepiecinski 2018, Fig. 6b: thrust ~65 N / torque ~0.135 N m at 0.05 mm, ~160 N / 0.39 N m at 0.25 mm
    for h, f, m in ((0.05, 65.0, 0.135), (0.25, 160.0, 0.39)):
        assert abs(c["f0_n"] + c["kf_n_per_mm"] * h - f) < 3.0
        assert abs(c["m0_nm"] + c["km_nm_per_mm"] * h - m) < 0.01


def test_a_smaller_bit_in_lighter_wood_needs_less_thrust_and_much_less_torque():
    big, small = drill_coefficients(0.0093, 742.0), drill_coefficients(0.006, 710.0)
    assert small["f0_n"] < big["f0_n"] and small["m0_nm"] < big["m0_nm"]
    assert small["m0_nm"] / big["m0_nm"] < small["f0_n"] / big["f0_n"]      # torque with d^2


def test_below_the_threshold_the_bit_rubs_and_does_not_feed():
    c = drill_coefficients(0.006, 710.0)
    st = {}
    tq = drill_cut(st, 0.9 * c["f0_n"], math.radians(30), c, 0.018)
    assert st["depth_m"] == 0.0 and abs(tq - c["m0_nm"]) < 1e-12


def test_the_feed_per_turn_is_the_thrust_s_chip_on_each_blade():
    c = drill_coefficients(0.006, 710.0)
    st = {}
    thrust = c["f0_n"] + c["kf_n_per_mm"] * 0.1                              # a 0.1 mm chip a blade
    for _ in range(10):
        drill_cut(st, thrust, 2 * math.pi / 10, c, 0.018)                    # one turn in ten steps
    assert abs(st["depth_m"] - 2 * 0.1e-3) < 1e-9                             # 0.2 mm a turn on two blades
    assert abs(st["turns"] - 1.0) < 1e-9


def test_the_chip_is_capped_and_reverse_does_not_cut_and_through_stops():
    c = drill_coefficients(0.006, 710.0)
    st = {}
    drill_cut(st, 10 * c["f0_n"], 2 * math.pi, c, 0.018)
    assert abs(st["depth_m"] - 2 * c["h_max_mm"] / 1000.0) < 1e-9
    d = st["depth_m"]
    drill_cut(st, 10 * c["f0_n"], -2 * math.pi, c, 0.018)
    assert st["depth_m"] == d
    for _ in range(200):
        drill_cut(st, 10 * c["f0_n"], 2 * math.pi, c, 0.018)
    assert st["through"] and st["depth_m"] == 0.018
    assert drill_cut(st, 10 * c["f0_n"], 2 * math.pi, c, 0.018) == 0.0      # through: it cuts air


def _inside(poly, x, y):
    P = np.asarray(poly, float)
    s = [np.cross(P[(i + 1) % len(P)] - P[i], np.array([x, y]) - P[i]) for i in range(len(P))]
    return all(v >= -1e-12 for v in s) or all(v <= 1e-12 for v in s)


def test_the_ring_is_convex_pieces_that_leave_only_the_hole():
    r, a, b, sides = 0.0031, 0.1, 0.15, 16
    polys = ring_polygons(0.012, -0.03, r, a, b, sides)
    assert len(polys) == sides and all(is_convex(p) for p in polys)
    rng = np.random.default_rng(0)
    R = r / math.cos(math.pi / sides)
    for x, y in rng.uniform([-a, -b], [a, b], (4000, 2)):
        d = math.hypot(x - 0.012, y + 0.03)
        hits = sum(_inside(p, x, y) for p in polys)
        if d < r * 0.999:
            assert hits == 0                                                  # the hole is empty
        elif d > R * 1.001:
            assert hits >= 1                                                  # the board is whole
    area = sum(abs(0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(p, p[1:] + p[:1]))) for p in polys)
    hole = 0.5 * sides * R * R * math.sin(2 * math.pi / sides)
    assert abs(area - (4 * a * b - hole)) < 1e-9


def test_a_blind_hole_keeps_the_board_behind_its_bottom_and_a_through_hole_does_not():
    board = board_frame([0.2, 0.018, 0.3], [4.8, 0.88175, 0.858], [0.0, -1.0, 0.0])
    assert board["n"] == [0.0, -1.0, 0.0] and abs(board["t"] - 0.018) < 1e-12
    entry = [4.81, 0.88175 - 0.009, 0.9]
    blind = hole_pieces(board, {"entry": entry, "r": 0.0031, "depth_m": 0.010})
    through = hole_pieces(board, {"entry": entry, "r": 0.0031, "depth_m": 0.018})
    assert [p["role"] for p in blind].count("behind") == 1 and [p["role"] for p in through].count("behind") == 0
    ring = [p for p in blind if p["role"] == "ring"][0]
    ys = [q[1] for q in ring["points"]]
    assert abs(min(ys) - (0.88175 - 0.009)) < 1e-9 and abs(max(ys) - (0.88175 - 0.009 + 0.010)) < 1e-9
    behind = [p for p in blind if p["role"] == "behind"][0]
    assert abs(max(q[1] for q in behind["points"]) - (0.88175 + 0.009)) < 1e-9


def test_a_hole_at_the_edge_is_refused():
    try:
        ring_polygons(0.098, 0.0, 0.0031, 0.1, 0.15)
    except ValueError:
        return
    raise AssertionError("a hole through the board's edge was built")


def test_the_wood_pushes_back_on_the_tip_below_its_floor_and_holds_it_to_its_line():
    from material_laws import bit_reaction
    n = np.array([0.0, -1.0, 0.0])                                           # the face looks along -y; the bit goes in along +y
    face = np.array([4.8, 0.87275, 0.949])
    entry = face.copy()
    tip = face + np.array([0.0005, 0.002, 0.0])                              # 2 mm in, 0.5 mm off the line
    F, thrust, lat, _ = bit_reaction(tip, tip, 1 / 480, face, n, entry, 0.001, 2.0e4, 150.0)
    assert abs(thrust - 2.0e4 * 0.001) < 1e-9                                # 1 mm past the cut's bottom (at rest: no damping)
    assert abs(lat - 0.0005) < 1e-12
    assert np.allclose(F, n * thrust + np.array([-2.0e4 * 0.0005, 0.0, 0.0]))
    # before the hole: the face is the floor and nothing holds it sideways
    F0, t0, _, _ = bit_reaction(face + np.array([0.0, 0.001, 0.0]), None, 1 / 480, face, n, None, 0.0, 2.0e4, 150.0)
    assert abs(t0 - 20.0) < 1e-9 and np.allclose(F0, n * 20.0)
    # through the board: no floor
    _, t1, _, _ = bit_reaction(tip, tip, 1 / 480, face, n, entry, 0.018, 2.0e4, 150.0, cutting=False)
    assert t1 == 0.0


def test_a_tip_that_has_not_moved_feels_no_damping_whatever_the_chuck_spins():
    from material_laws import bit_reaction
    n = np.array([0.0, -1.0, 0.0])
    face = np.zeros(3)
    tip = np.array([0.0, 0.003, 0.0])
    # the tip on the spin axis does not move as the chuck turns: its velocity is its own position's change
    F, thrust, lat, _ = bit_reaction(tip, tip.copy(), 1 / 480, face, n, face, 0.003, 2.0e4, 150.0)
    assert thrust == 0.0 and lat == 0.0 and np.allclose(F, 0.0)


def test_the_wall_bears_no_more_than_the_wood_and_then_yields():
    from material_laws import bit_reaction, embedment_strength_n_mm2
    fh = embedment_strength_n_mm2(0.003175, 710.0)
    assert 54.0 < fh < 58.0                                                  # EN 1995-1-1 pre-drilled: ~56 N/mm2
    bearing = fh * 1e6 * 0.003175                                            # N per m of bit in the wood
    n = np.array([0.0, -1.0, 0.0])
    face = np.zeros(3)
    tip = np.array([0.014, 0.0003, 0.0])                                     # a 14 mm jolt, 0.3 mm into the face
    F, _, lat, shift = bit_reaction(tip, tip, 1 / 480, face, n, face, 0.0, 2.0e4, 150.0, bearing_n_per_m=bearing)
    wall = np.linalg.norm(F - n * float(F @ n))
    assert abs(wall - bearing * 0.0003) < 1e-6                               # ~53 N, not 280
    assert abs(np.linalg.norm(shift) - (0.014 - bearing * 0.0003 / 2.0e4)) < 1e-9 and shift[0] > 0
