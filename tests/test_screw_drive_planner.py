"""screw_drive_planner: the palm that carries the bit onto the screw, and the feed."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from screw_drive_planner import feed_ticks, palm_for_bit, rot_between  # noqa: E402


def test_rot_between_takes_one_direction_onto_another():
    for a, b in (([1, 0, 0], [0, 1, 0]), ([0, 1, 0], [0, 1, 0]), ([0, 1, 0], [0, -1, 0]), ([0.3, 0.9, 0.1], [0.0, 1.0, 0.05])):
        a = np.asarray(a, float) / np.linalg.norm(a); b = np.asarray(b, float) / np.linalg.norm(b)
        R = rot_between(a, b)
        assert np.allclose(R @ a, b, atol=1e-9) and np.allclose(R.T @ R, np.eye(3), atol=1e-9)


def test_the_palm_carries_the_bit_rigidly_onto_the_target():
    R0 = np.eye(3); p0 = np.array([4.8, 0.55, 0.9])
    tip = np.array([4.81, 0.80, 0.95]); bdir = np.array([0.05, 1.0, 0.02]); bdir /= np.linalg.norm(bdir)
    target, into = np.array([4.80, 0.831, 0.949]), np.array([0.0, 1.0, 0.0])
    R, p = palm_for_bit(R0, p0, tip, bdir, target, into)
    tip_p, dir_p = R0.T @ (tip - p0), R0.T @ bdir                   # the bit in the palm, as it is held
    assert np.allclose(p + R @ tip_p, target, atol=1e-9)
    assert np.allclose(R @ dir_p, into, atol=1e-9)


def test_the_hand_feeds_behind_the_screw():
    # a #8 screw (15 TPI) driven 26.75 mm at 450 rpm, the hand at 0.8 of its rate
    n, v = feed_ticks(0.02675, 0.0254 / 15, 450.0, 0.8, 30)
    assert abs(v - 0.8 * 0.0254 / 15 * 7.5) < 1e-12
    assert n == int(np.ceil(0.02675 / v * 30))
    assert v < 0.0254 / 15 * 7.5                                     # slower than the screw can go: it follows the hand
