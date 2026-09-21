"""L0 tests for which oracle demonstrations reach training.

A mistake here poisons the dataset rather than crashing it, so these pin the
outcomes that matter. The central rule is that **lift**, not lateral movement,
marks an attempted grasp: measured on the cluttered scene, a grasp that misses
leaves its target sitting exactly where it was, while objects the arm brushes
past on the way through do shift a centimetre or two. Counting those bumps as
failures threw away whole episodes whose actual grasp was clean.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.l0

from scripts.stage_oracle_episodes import judge_by_placement

CONTAINER = "grey_bin"
LIMITS = {"container": CONTAINER, "radius_m": 0.13, "min_lift_m": 0.05}


def track(*rows) -> np.ndarray:
    return np.asarray(rows, dtype=np.float32)


BIN_TRACK = track([0.5, -0.2, 0.0], [0.5, -0.2, 0.0], [0.5, -0.2, 0.0])
# Lifted clear of the table, then set down on the container.
DELIVERED = track([0.3, 0.2, 0.02], [0.3, 0.2, 0.18], [0.5, -0.2, 0.05])
# Picked up but put down somewhere else entirely.
MISPLACED = track([0.3, 0.2, 0.02], [0.3, 0.2, 0.18], [0.3, 0.05, 0.02])
# Never left the table: a missed grasp, or clutter the arm brushed past.
NUDGED = track([0.3, 0.1, 0.02], [0.3, 0.1, 0.02], [0.33, 0.1, 0.02])
UNTOUCHED = track([0.1, 0.4, 0.02], [0.1, 0.4, 0.02], [0.1, 0.4, 0.02])


def test_keeps_a_clean_pick_and_place():
    ok, why = judge_by_placement({CONTAINER: BIN_TRACK, "red_block": DELIVERED}, **LIMITS)
    assert ok, why
    assert "red_block" in why


def test_ignores_objects_the_arm_never_lifted():
    ok, why = judge_by_placement(
        {CONTAINER: BIN_TRACK, "red_block": DELIVERED,
         "green_block": UNTOUCHED, "blue_block": NUDGED},
        **LIMITS,
    )
    # Clutter that was skipped, or only clipped in passing, is not a failure.
    assert ok, why
    assert "green_block" not in why and "blue_block" not in why


def test_rejects_an_object_lifted_but_not_delivered():
    ok, why = judge_by_placement(
        {CONTAINER: BIN_TRACK, "red_block": DELIVERED, "blue_block": MISPLACED}, **LIMITS
    )
    assert not ok
    assert "blue_block" in why


def test_rejects_an_episode_that_delivered_nothing():
    ok, why = judge_by_placement({CONTAINER: BIN_TRACK, "red_block": NUDGED}, **LIMITS)
    assert not ok
    assert "nothing placed" in why


def test_attempted_list_scopes_the_verdict():
    # The controller only went for red; blue was dropped by something else and
    # must not condemn the episode.
    tracks = {CONTAINER: BIN_TRACK, "red_block": DELIVERED, "blue_block": MISPLACED}
    assert judge_by_placement(tracks, attempted=("red_block",), **LIMITS)[0]
    assert not judge_by_placement(tracks, attempted=("red_block", "blue_block"), **LIMITS)[0]
