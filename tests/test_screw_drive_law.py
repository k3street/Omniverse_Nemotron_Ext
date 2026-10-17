"""behaviors.screw_drive: a screw follows the bit only while engaged, advances
a pitch a turn, seats, tightens, and strips past its stripping torque."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from behaviors import screw_drive  # noqa: E402

KW = dict(pitch_m=0.0254 / 15, seat_depth_m=0.025, drive_n_m=1.0, tighten_n_m=4.0, strip_n_m=6.0, start_depth_m=0.005)


def turn(state, turns, engaged=True, steps=200):
    t = 0.0
    for _ in range(steps):
        t = screw_drive(state, 2 * math.pi * turns / steps, engaged, **KW)
    return t


def test_off_the_bit_the_screw_does_not_move():
    st = {}
    assert turn(st, 3.0, engaged=False) == 0.0
    assert st["depth_m"] == 0.005 and st["theta_rad"] == 0.0


def test_a_turn_advances_one_pitch_against_the_driving_torque():
    st = {}
    t = turn(st, 2.0)
    assert abs(st["depth_m"] - (0.005 + 2 * KW["pitch_m"])) < 1e-9
    assert abs(t - 1.0) < 1e-9                                      # the thread fully engaged past 6 mm: the driving torque


def test_the_head_seats_then_tightens_then_strips():
    st = {}
    n_seat = (0.025 - 0.005) / KW["pitch_m"]                          # ~11.8 turns to seat
    turn(st, n_seat - 0.01)
    assert not st["seated"]
    t = turn(st, 0.26)                                               # just past a quarter turn beyond seating
    assert st["seated"] and st["depth_m"] == 0.025 and 3.9 < t <= 4.1 and not st["stripped"]
    t = turn(st, 0.3)                                                # on past the stripping torque
    assert st["stripped"] and abs(t - 1.0 / 3.0) < 1e-9


def test_reverse_backs_the_screw_out():
    st = {}
    turn(st, 2.0)
    turn(st, -1.0)
    assert abs(st["depth_m"] - (0.005 + KW["pitch_m"])) < 1e-9
