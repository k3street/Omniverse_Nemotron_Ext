"""The cuMotion bridge's pure parts: path shortcutting and the hand expansion."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cumotion_bridge import shortcut  # noqa: E402
from cumotion_r5 import COUPLING, hand_joint_positions  # noqa: E402


def _dense(points, step=0.02):
    out = [np.asarray(points[0], float)]
    for a, b in zip(points[:-1], points[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(1, int(np.ceil(np.max(np.abs(b - a)) / step)))
        out += [a * (1 - f) + b * f for f in np.linspace(0, 1, n + 1)[1:]]
    return out


def test_shortcut_removes_a_detour_in_free_space():
    detour = _dense([[0, 0], [0.5, 1.0], [1.0, 0]])
    short = shortcut(detour, lambda q: True, hand=[], step_rad=0.02)
    assert np.allclose(short[0], [0, 0]) and np.allclose(short[-1], [1.0, 0])
    assert max(abs(q[1]) for q in short) < 1e-9            # straight along the axis now
    assert len(short) < len(detour)


def test_shortcut_keeps_the_detour_round_a_wall():
    # the wall: everything with |x - 0.5| < 0.05 and y < 0.5 is in collision
    def clear(q):
        return not (abs(q[0] - 0.5) < 0.05 and q[1] < 0.5)
    detour = _dense([[0, 0], [0.5, 1.0], [1.0, 0]])
    short = shortcut(detour, clear, hand=[], step_rad=0.02)
    assert all(clear(q) for q in short)
    assert max(q[1] for q in short) >= 0.5                 # still goes over the wall


def test_shortcut_steps_stay_within_the_resampling_step():
    detour = _dense([[0, 0, 0], [0.3, 0.9, 0.2], [1.0, 0, 0.4]])
    short = shortcut(detour, lambda q: True, hand=[], step_rad=0.02)
    jumps = np.max(np.abs(np.diff(np.asarray(short), axis=0)), axis=1)
    assert jumps.max() <= 0.02 + 1e-9


def test_hand_expansion_follows_the_four_bar_coupling():
    h = hand_joint_positions([0.7, 1.5, 1.5, 1.5, 0.7, -1.244])
    assert h["psyonic_left_index_q1"] == 0.7
    assert abs(h["psyonic_left_index_q2"] - 0.7 * COUPLING) < 1e-12
    assert h["psyonic_left_thumb_q2"] == 0.7 and h["psyonic_left_thumb_q1"] == -1.244
    assert len(h) == 10
