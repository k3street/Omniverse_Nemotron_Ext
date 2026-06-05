"""Phase 80b contract tests — suction array sensor."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_4cup_grid():
    """Return a 2×2 grid array with cups at the corners of a 100 mm square."""
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import (
        SuctionArray,
        SuctionCup,
    )

    cups = [
        SuctionCup(cup_id="c0", position_mm=(0.0, 0.0, 0.0)),
        SuctionCup(cup_id="c1", position_mm=(100.0, 0.0, 0.0)),
        SuctionCup(cup_id="c2", position_mm=(0.0, 100.0, 0.0)),
        SuctionCup(cup_id="c3", position_mm=(100.0, 100.0, 0.0)),
    ]
    return SuctionArray(array_id="grid4", cups=cups)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_80b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == "80b"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. read_state — mixed vacuum levels
# ---------------------------------------------------------------------------

def test_read_state_mixed_vacuum():
    """Cups above threshold → no contact; cups at/below → contact."""
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import (
        SuctionArray,
        SuctionCup,
    )

    cups = [
        SuctionCup(cup_id="a", position_mm=(0.0, 0.0, 0.0), vacuum_threshold_kpa=-20.0),
        SuctionCup(cup_id="b", position_mm=(50.0, 0.0, 0.0), vacuum_threshold_kpa=-20.0),
        SuctionCup(cup_id="c", position_mm=(100.0, 0.0, 0.0), vacuum_threshold_kpa=-20.0),
    ]
    arr = SuctionArray(array_id="test", cups=cups)

    # "a" has weak vacuum (not picked up), "b" barely over threshold (not contact),
    # "c" has strong vacuum (contact)
    readings = arr.read_state({"a": -5.0, "b": -19.9, "c": -25.0})

    assert len(readings) == 3
    by_id = {r.cup_id: r for r in readings}

    assert by_id["a"].contact is False
    assert by_id["b"].contact is False
    assert by_id["c"].contact is True
    assert by_id["c"].vacuum_kpa == -25.0
    # Timestamps are present
    assert by_id["a"].timestamp != ""


# ---------------------------------------------------------------------------
# 3. Pickup pattern — full
# ---------------------------------------------------------------------------

def test_pickup_pattern_full():
    arr = make_4cup_grid()
    # All four cups in contact
    readings = arr.read_state({f"c{i}": -30.0 for i in range(4)})
    assert arr.pickup_pattern(readings) == "full"
    assert arr.pickup_count(readings) == 4


# ---------------------------------------------------------------------------
# 4. Pickup pattern — partial with N/M ratio
# ---------------------------------------------------------------------------

def test_pickup_pattern_partial():
    arr = make_4cup_grid()
    # c0 and c1 have contact; c2 and c3 do not
    readings = arr.read_state({"c0": -30.0, "c1": -30.0, "c2": -5.0, "c3": -5.0})
    pattern = arr.pickup_pattern(readings)
    assert pattern == "partial:2/4"
    assert arr.pickup_count(readings) == 2


# ---------------------------------------------------------------------------
# 5. Pickup pattern — none
# ---------------------------------------------------------------------------

def test_pickup_pattern_none():
    arr = make_4cup_grid()
    # All cups above threshold
    readings = arr.read_state({f"c{i}": -1.0 for i in range(4)})
    assert arr.pickup_pattern(readings) == "none"
    assert arr.pickup_count(readings) == 0


# ---------------------------------------------------------------------------
# 6. Pickup pattern — single
# ---------------------------------------------------------------------------

def test_pickup_pattern_single():
    arr = make_4cup_grid()
    readings = arr.read_state({"c0": -50.0, "c1": -1.0, "c2": -1.0, "c3": -1.0})
    assert arr.pickup_pattern(readings) == "single"
    assert arr.pickup_count(readings) == 1


# ---------------------------------------------------------------------------
# 7. Centroid math on 4-cup grid
# ---------------------------------------------------------------------------

def test_centroid_of_active_4cup_grid():
    """When all 4 corner cups are active the centroid is the grid centre."""
    arr = make_4cup_grid()
    readings = arr.read_state({f"c{i}": -30.0 for i in range(4)})
    centroid = arr.centroid_of_active(readings)
    assert centroid is not None
    cx, cy, cz = centroid
    assert cx == pytest.approx(50.0)
    assert cy == pytest.approx(50.0)
    assert cz == pytest.approx(0.0)


def test_centroid_of_active_partial():
    """With only c0 and c1 active the centroid lies at x=50, y=0."""
    arr = make_4cup_grid()
    readings = arr.read_state({"c0": -30.0, "c1": -30.0, "c2": -1.0, "c3": -1.0})
    centroid = arr.centroid_of_active(readings)
    assert centroid is not None
    cx, cy, cz = centroid
    assert cx == pytest.approx(50.0)
    assert cy == pytest.approx(0.0)


def test_centroid_none_when_no_contact():
    arr = make_4cup_grid()
    readings = arr.read_state({f"c{i}": -1.0 for i in range(4)})
    assert arr.centroid_of_active(readings) is None


# ---------------------------------------------------------------------------
# 8. Threshold edge case: vacuum exactly == threshold → contact
# ---------------------------------------------------------------------------

def test_threshold_boundary_exact():
    """Vacuum exactly equal to threshold must count as contact."""
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import (
        SuctionArray,
        SuctionCup,
    )

    threshold = -20.0
    cup = SuctionCup(cup_id="x", position_mm=(0.0, 0.0, 0.0), vacuum_threshold_kpa=threshold)
    arr = SuctionArray(array_id="edge", cups=[cup])

    readings = arr.read_state({"x": threshold})
    assert len(readings) == 1
    assert readings[0].contact is True


def test_threshold_just_above_boundary():
    """Vacuum one epsilon above threshold must NOT count as contact."""
    from service.isaac_assist_service.multimodal.sub_phase_80b_suction_array_sensor import (
        SuctionArray,
        SuctionCup,
    )

    threshold = -20.0
    cup = SuctionCup(cup_id="x", position_mm=(0.0, 0.0, 0.0), vacuum_threshold_kpa=threshold)
    arr = SuctionArray(array_id="edge", cups=[cup])

    readings = arr.read_state({"x": threshold + 0.001})
    assert readings[0].contact is False


# ---------------------------------------------------------------------------
# 9. Partial-array reads (subset of cups supplied)
# ---------------------------------------------------------------------------

def test_partial_array_read():
    """read_state skips cups whose cup_id is absent from vacuum_readings."""
    arr = make_4cup_grid()
    # Only supply readings for c0 and c3
    readings = arr.read_state({"c0": -30.0, "c3": -30.0})
    assert len(readings) == 2
    ids = {r.cup_id for r in readings}
    assert ids == {"c0", "c3"}
    assert arr.pickup_pattern(readings) == "full"
