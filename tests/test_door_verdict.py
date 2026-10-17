"""door_verdict.hand_drove_door: the door opened by the hand, not by itself."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from door_verdict import hand_drove_door  # noqa: E402


def test_a_light_door_pushed_with_a_fifth_of_a_newton_was_opened_by_the_hand():
    degs = np.linspace(22.0, 66.2, 400)
    forces = np.full(400, 0.19)
    v = hand_drove_door(degs, forces, least_deg=45.0, planned_push_deg=46.0)
    assert v["opened_by_hand"] and v["turning_with_hand_fraction"] == 1.0


def test_a_door_that_crept_open_by_itself_was_not():
    degs = np.linspace(13.5, 90.0, 400)
    v = hand_drove_door(degs, np.zeros(400), least_deg=45.0, planned_push_deg=52.0)
    assert not v["opened_by_hand"] and v["turning_with_hand_fraction"] == 0.0


def test_a_door_already_open_that_the_hand_never_moved_was_not():
    v = hand_drove_door(np.full(300, 90.0), np.zeros(300), least_deg=45.0, planned_push_deg=52.0)
    assert not v["opened_by_hand"] and v["pushed_by_hand_deg"] == 0.0


def test_a_push_short_of_the_least_angle_fails():
    v = hand_drove_door(np.linspace(20.0, 38.0, 200), np.full(200, 0.2), least_deg=45.0, planned_push_deg=46.0)
    assert not v["opened_by_hand"]
