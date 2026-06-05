"""Phase 81b — RouteValidator test suite.

Covers:
- Metadata contract (phase / status)
- Clean route → ValidationResult.valid=True, no violations
- Each of the four violation types fires independently
- Multiple violations aggregate correctly (n_hard / n_soft)
- Empty route (0 waypoints) → insufficient_waypoints blocks further checks
- Single-waypoint route → insufficient_waypoints (hard)
- Close-but-not-duplicate segment (distance just above 1e-6) does NOT fire zero_distance_segment
"""
from __future__ import annotations

import math
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.sub_phase_81b_route_validators import (
    Route,
    RouteValidator,
    Waypoint,
    get_phase_metadata,
)
from service.isaac_assist_service.types.violations import ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wp(x: float, y: float = 0.0, z: float = 0.0, label: str = "") -> Waypoint:
    return Waypoint(x=x, y=y, z=z, label=label)


def _route(route_id: str, waypoints: list[Waypoint]) -> Route:
    return Route(route_id=route_id, waypoints=waypoints)


VALIDATOR = RouteValidator()


# ---------------------------------------------------------------------------
# Test 1 — Metadata contract
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_id(self):
        md = get_phase_metadata()
        assert md["phase"] == "81b"

    def test_status_landed(self):
        md = get_phase_metadata()
        assert md["status"] == "landed"

    def test_title_present(self):
        md = get_phase_metadata()
        assert "title" in md and md["title"]

    def test_spec_ref_present(self):
        md = get_phase_metadata()
        assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — Clean route → valid=True, zero violations
# ---------------------------------------------------------------------------

class TestCleanRoute:
    def test_simple_forward_route_is_valid(self):
        route = _route(
            "forward_aisle_A",
            [_wp(0.0), _wp(1.0), _wp(2.0), _wp(3.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is True
        assert result.violations == []
        assert result.n_hard == 0
        assert result.n_soft == 0

    def test_non_forward_decreasing_x_is_valid(self):
        """Decreasing x on a non-forward route should not trigger inverted_direction."""
        route = _route(
            "return_aisle_A",
            [_wp(5.0), _wp(3.0), _wp(1.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is True
        assert result.n_hard == 0


# ---------------------------------------------------------------------------
# Test 3 — Inverted direction fires on forward route
# ---------------------------------------------------------------------------

class TestInvertedDirection:
    def test_fires_when_last_x_less_than_first_and_route_is_forward(self):
        route = _route(
            "forward_main_corridor",
            [_wp(10.0), _wp(5.0), _wp(2.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is False
        ids = [v.constraint_id for v in result.violations]
        assert "route.inverted_direction" in ids

    def test_not_fired_when_forward_not_in_route_id(self):
        route = _route(
            "reverse_pass",
            [_wp(10.0), _wp(5.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is True
        ids = [v.constraint_id for v in result.violations]
        assert "route.inverted_direction" not in ids

    def test_not_fired_when_last_x_equals_first_x(self):
        route = _route(
            "forward_side",
            [_wp(5.0), _wp(5.0, y=1.0)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.inverted_direction" not in ids

    def test_violation_is_hard_error(self):
        route = _route(
            "forward_test",
            [_wp(10.0), _wp(1.0)],
        )
        result = VALIDATOR.validate(route)
        inverted = next(
            v for v in result.violations
            if v.constraint_id == "route.inverted_direction"
        )
        assert inverted.category == "hard"
        from service.isaac_assist_service.types.uncertainty import GradedScale
        assert inverted.severity == GradedScale.ERROR


# ---------------------------------------------------------------------------
# Test 4 — Duplicate waypoints
# ---------------------------------------------------------------------------

class TestDuplicateWaypoints:
    def test_fires_on_exact_duplicate_xyz(self):
        route = _route(
            "warehouse_loop",
            [_wp(0.0), _wp(1.0), _wp(1.0), _wp(2.0)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.duplicate_waypoint" in ids

    def test_violation_is_soft_warning(self):
        route = _route(
            "shelf_row",
            [_wp(0.0), _wp(3.0), _wp(3.0)],
        )
        result = VALIDATOR.validate(route)
        dup = next(
            v for v in result.violations
            if v.constraint_id == "route.duplicate_waypoint"
        )
        assert dup.category == "soft"
        from service.isaac_assist_service.types.uncertainty import GradedScale
        assert dup.severity == GradedScale.WARNING

    def test_result_remains_valid_with_only_soft(self):
        route = _route(
            "shelf_row",
            [_wp(0.0), _wp(3.0), _wp(3.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is True

    def test_different_z_is_not_duplicate(self):
        route = _route(
            "multi_level",
            [_wp(0.0, y=0.0, z=0.0), _wp(0.0, y=0.0, z=1.0)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.duplicate_waypoint" not in ids


# ---------------------------------------------------------------------------
# Test 5 — Insufficient waypoints
# ---------------------------------------------------------------------------

class TestInsufficientWaypoints:
    def test_empty_route_fires(self):
        route = _route("empty_route", [])
        result = VALIDATOR.validate(route)
        assert result.valid is False
        ids = [v.constraint_id for v in result.violations]
        assert "route.insufficient_waypoints" in ids

    def test_single_waypoint_fires(self):
        route = _route("solo_forward", [_wp(0.0)])
        result = VALIDATOR.validate(route)
        assert result.valid is False
        ids = [v.constraint_id for v in result.violations]
        assert "route.insufficient_waypoints" in ids

    def test_insufficient_is_hard(self):
        route = _route("solo", [_wp(1.0)])
        result = VALIDATOR.validate(route)
        v = next(
            v for v in result.violations
            if v.constraint_id == "route.insufficient_waypoints"
        )
        assert v.category == "hard"

    def test_insufficient_short_circuits_other_checks(self):
        """A 0-waypoint forward route should only produce insufficient_waypoints,
        not inverted_direction (which can't be checked without 2 points)."""
        route = _route("forward_empty", [])
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.insufficient_waypoints" in ids
        assert "route.inverted_direction" not in ids

    def test_two_waypoints_does_not_fire(self):
        route = _route("minimal", [_wp(0.0), _wp(1.0)])
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.insufficient_waypoints" not in ids


# ---------------------------------------------------------------------------
# Test 6 — Zero-distance segment
# ---------------------------------------------------------------------------

class TestZeroDistanceSegment:
    def test_fires_on_identical_consecutive_waypoints(self):
        route = _route(
            "stuck_loop",
            [_wp(0.0), _wp(1.0), _wp(1.0), _wp(2.0)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        # duplicate_waypoint fires too; we only care zero_distance fires
        assert "route.zero_distance_segment" in ids

    def test_violation_is_soft(self):
        route = _route(
            "stuck",
            [_wp(0.0), _wp(0.0)],
        )
        result = VALIDATOR.validate(route)
        zd = next(
            v for v in result.violations
            if v.constraint_id == "route.zero_distance_segment"
        )
        assert zd.category == "soft"

    def test_close_but_above_threshold_does_not_fire(self):
        """Two waypoints separated by 2e-6 (just above 1e-6) must NOT trigger."""
        d = 2e-6
        route = _route(
            "close_pair",
            [_wp(0.0), _wp(d)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.zero_distance_segment" not in ids

    def test_exactly_at_threshold_does_not_fire(self):
        """Exactly 1e-6 is NOT < 1e-6 so should not fire."""
        d = 1e-6
        route = _route(
            "threshold_pair",
            [_wp(0.0), _wp(d)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.zero_distance_segment" not in ids

    def test_below_threshold_fires(self):
        """Distance of 5e-7 (< 1e-6) should fire zero_distance_segment."""
        d = 5e-7
        route = _route(
            "near_zero",
            [_wp(0.0), _wp(d)],
        )
        result = VALIDATOR.validate(route)
        ids = [v.constraint_id for v in result.violations]
        assert "route.zero_distance_segment" in ids


# ---------------------------------------------------------------------------
# Test 7 — Multiple violations aggregate + n_hard / n_soft counts
# ---------------------------------------------------------------------------

class TestMultipleViolations:
    def test_hard_and_soft_aggregate(self):
        """Inverted direction (hard) + duplicate waypoint (soft) on same route."""
        route = _route(
            "forward_broken",
            # Last x (0.0) < first x (10.0) → inverted (hard)
            # wp at x=5.0 duplicated → duplicate (soft)
            [_wp(10.0), _wp(5.0), _wp(5.0), _wp(0.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is False  # hard violation present
        assert result.n_hard >= 1
        assert result.n_soft >= 1

    def test_n_hard_n_soft_counts_are_correct(self):
        """Verify arithmetic: n_hard + n_soft == len(violations)."""
        route = _route(
            "forward_chaos",
            [_wp(10.0), _wp(5.0), _wp(5.0), _wp(0.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.n_hard + result.n_soft == len(result.violations)

    def test_only_soft_violations_keeps_valid_true(self):
        """Duplicate + zero-distance on a non-forward route → all soft → valid."""
        route = _route(
            "shelf_pass",
            [_wp(0.0), _wp(1.0), _wp(1.0)],
        )
        result = VALIDATOR.validate(route)
        assert result.valid is True
        assert result.n_hard == 0
        assert result.n_soft >= 1
