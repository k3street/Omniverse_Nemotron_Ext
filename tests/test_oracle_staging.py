"""L0 tests for which oracle demonstrations reach training.

A mistake here poisons the dataset rather than crashing, so the cases below pin
the three outcomes that matter: a clean pick-and-place is kept, an untouched
object is ignored, and an attempted-but-missed grasp disqualifies the episode.
That last one matters most -- a demonstration of the gripper closing on nothing
teaches exactly the failure the training is meant to remove.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.l0

from scripts.stage_oracle_episodes import judge_by_placement

CONTAINER = "grey_bin"
LIMITS = {"container": CONTAINER, "radius_m": 0.13, "min_lift_m": 0.05, "move_m": 0.02}


def track(*rows) -> np.ndarray:
    return np.asarray(rows, dtype=np.float32)


BIN_TRACK = track([0.5, -0.2, 0.0], [0.5, -0.2, 0.0], [0.5, -0.2, 0.0])
# Lifted clear of the table, then set down on the container.
DELIVERED = track([0.3, 0.2, 0.02], [0.3, 0.2, 0.18], [0.5, -0.2, 0.05])


def test_keeps_a_clean_pick_and_place():
    ok, why = judge_by_placement(
        {CONTAINER: BIN_TRACK, "red_block": DELIVERED}, **LIMITS
    )
    assert ok, why
    assert "red_block" in why


def test_ignores_objects_the_arm_never_touched():
    untouched = track([0.1, 0.4, 0.02], [0.1, 0.4, 0.02], [0.1, 0.4, 0.02])
    ok, why = judge_by_placement(
        {CONTAINER: BIN_TRACK, "red_block": DELIVERED, "green_block": untouched},
        **LIMITS,
    )
    # Scene clutter sits still all episode; it is neither a success nor a miss.
    assert ok, why
    assert "green_block" not in why


def test_rejects_an_attempted_grasp_that_missed():
    # Nudged across the table but never lifted or delivered.
    dragged = track([0.3, 0.1, 0.02], [0.3, 0.1, 0.02], [0.36, 0.1, 0.02])
    ok, why = judge_by_placement(
        {CONTAINER: BIN_TRACK, "red_block": DELIVERED, "blue_block": dragged},
        **LIMITS,
    )
    assert not ok
    assert "blue_block" in why


def test_rejects_an_episode_that_delivered_nothing():
    ok, why = judge_by_placement({CONTAINER: BIN_TRACK}, **LIMITS)
    assert not ok
    assert "nothing placed" in why


def test_lifted_but_dropped_short_of_the_container_is_a_miss():
    short = track([0.3, 0.2, 0.02], [0.3, 0.2, 0.18], [0.3, 0.05, 0.02])
    ok, why = judge_by_placement({CONTAINER: BIN_TRACK, "red_block": short}, **LIMITS)
    assert not ok
    assert "red_block" in why
