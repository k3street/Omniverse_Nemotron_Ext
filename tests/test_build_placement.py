"""Placing an object by its overhang past a support's measured edge (build_er_scene.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_er_scene import centre_by_overhang, support_edge  # noqa: E402

TOP = 0.708


def _table():
    # a slab top y 0.549..1.374 at z 0.708 (its edge face down to 0.687), leg panels out to y 0.523 below the slab
    ys, xs = np.linspace(0.549, 1.374, 30), np.linspace(4.14, 5.81, 40)
    slab = [[x, y, TOP] for x in xs for y in ys] + [[x, 0.549, 0.687] for x in xs]
    legs = [[x, y, z] for x in (4.55, 5.38) for y in np.linspace(0.523, 1.374, 20) for z in np.linspace(0.0, 0.687, 8)]
    return slab + legs


def test_the_slab_edge_is_found_at_the_object_not_the_legs():
    assert support_edge(_table(), "y", "-", 4.90, 0.15, TOP) == pytest.approx(0.549)


def test_the_leg_panels_do_not_count_as_the_edge_even_under_the_object():
    assert support_edge(_table(), "y", "-", 4.55, 0.15, TOP) == pytest.approx(0.549)


def test_a_ten_centimetre_overhang_puts_the_towel_centre_past_the_edge_by_half_its_length_less_the_overhang():
    assert centre_by_overhang(0.549, "-", 0.40, 0.10) == pytest.approx(0.649)
    assert centre_by_overhang(1.374, "+", 0.40, 0.10) == pytest.approx(1.274)


def test_no_slab_under_the_object_is_an_error():
    with pytest.raises(ValueError):
        support_edge(_table(), "y", "-", 9.0, 0.1, TOP)
