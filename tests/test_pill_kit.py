"""The pill kit's geometry and springs (scripts/make_pill_kit.py): the tweezers'
arm spring is the cantilever's, the vial's wall a ring of convex pieces that
leaves its cavity empty, the masses those the sources give."""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from make_pill_kit import TABLET, TWEEZERS, VIAL, annulus_sectors, tweezer_spring  # noqa: E402
from material_laws import is_convex  # noqa: E402


def test_the_tweezers_spring_is_the_arms_cantilever():
    s = tweezer_spring()
    tw = TWEEZERS
    I = tw["width"] * tw["thick"] ** 3 / 12
    L = tw["length"] - tw["root"]
    assert abs(s["k_tip_n_per_m"] - 3 * tw["E"] * I / L ** 3) < 1e-9
    assert abs(s["k_theta_n_m_per_rad"] - s["k_tip_n_per_m"] * L ** 2) < 1e-9
    assert abs(s["k_theta_n_m_per_deg"] - s["k_theta_n_m_per_rad"] * math.pi / 180) < 1e-12
    assert 1.0 < s["tip_force_closed_n"] < 5.0                 # a squeeze a hand gives, not a vice's


def test_the_tweezers_weigh_what_the_listings_say():
    tw = TWEEZERS
    L = tw["length"] - tw["root"]
    arm = ((L - tw["tip_len"]) * tw["width"] + tw["tip_len"] * tw["tip_width"]) * tw["thick"] * tw["density"]
    root = tw["root"] * tw["width"] * (2 * tw["thick"] + 0.0004) * tw["density"]
    assert 0.012 <= 2 * arm + root <= 0.020                     # 115 mm stainless tweezers: 15-20 g


def test_the_vial_wall_is_convex_pieces_round_an_empty_cavity():
    r_in = VIAL["r_out"] - VIAL["wall"]
    pieces = annulus_sectors(r_in, VIAL["r_out"], VIAL["sides"])
    assert all(is_convex(p) for p in pieces)
    tablet_r = TABLET["d"] / 2

    def inside(poly, x, y):
        P = np.asarray(poly, float)
        s = [np.cross(P[(i + 1) % 4] - P[i], np.array([x, y]) - P[i]) for i in range(4)]
        return all(v >= -1e-12 for v in s) or all(v <= -1e-12 for v in s)
    for x, y in np.random.default_rng(0).uniform(-r_in, r_in, (2000, 2)):
        if math.hypot(x, y) < r_in * math.cos(math.pi / VIAL["sides"]) - 1e-6:
            assert not any(inside(p, x, y) for p in pieces)      # the cavity is empty
    assert 2 * r_in * math.cos(math.pi / VIAL["sides"]) > 3 * TABLET["d"]   # three tablets across


def test_the_tablet_is_within_the_fda_limit_and_weighs_a_fraction_of_a_gram():
    assert max(TABLET["d"], TABLET["t"]) <= 0.022
    m = math.pi * (TABLET["d"] / 2) ** 2 * TABLET["t"] * TABLET["density"]
    assert 0.0002 < m < 0.001
