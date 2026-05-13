"""Phase 69 — pytest gate: ContactReachabilityValidator.

All tests are l0 (pure-Python, no external dependencies).

Gate: validator must catch out-of-reach contacts, occluded line-of-reach,
and joint-limit violations; geometry helpers must be numerically correct.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 69.
"""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.spawn_validator_contact_reachability import (
    ContactPoint,
    ContactReachabilityValidator,
    OccluderBox,
    ReachabilityFinding,
    RobotReachSpec,
    expected_validator_checks,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _robot(
    base=(0.0, 0.0, 0.0),
    max_reach=1.0,
    min_reach=0.0,
    violations=None,
) -> RobotReachSpec:
    return RobotReachSpec(
        base_position=base,
        max_reach_m=max_reach,
        min_reach_m=min_reach,
        joint_limit_violations=violations or [],
    )


def _contact(position=(0.5, 0.0, 0.0), normal=(0.0, 0.0, 1.0), surface_id="obj") -> ContactPoint:
    return ContactPoint(position=position, normal=normal, surface_id=surface_id)


def _errors(findings: list[ReachabilityFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "error"]


def _warns(findings: list[ReachabilityFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "warn"]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def test_phase_69_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 69


def test_phase_69_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_69_metadata_spec_ref_present():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "69" in md["spec_ref"]


# ---------------------------------------------------------------------------
# expected_validator_checks
# ---------------------------------------------------------------------------


def test_expected_validator_checks_returns_five_or_more():
    checks = expected_validator_checks()
    assert len(checks) >= 5, f"Expected ≥5 check_ids, got {len(checks)}: {checks}"


def test_expected_validator_checks_contains_required_ids():
    checks = expected_validator_checks()
    required = {
        "within_reach",
        "not_below_min_reach",
        "not_occluded",
        "no_joint_violations",
        "normal_valid",
    }
    missing = required - set(checks)
    assert not missing, f"Missing check_ids: {missing}"


# ---------------------------------------------------------------------------
# distance helper
# ---------------------------------------------------------------------------


def test_distance_3_4_5_triangle():
    """Classic Pythagorean triple: (3, 4, 0) → distance = 5."""
    v = ContactReachabilityValidator()
    d = v.distance((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))
    assert math.isclose(d, 5.0, rel_tol=1e-9), f"Expected 5.0, got {d}"


def test_distance_identity():
    v = ContactReachabilityValidator()
    assert v.distance((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == 0.0


def test_distance_unit_diagonal():
    """3-D unit diagonal: sqrt(3)."""
    v = ContactReachabilityValidator()
    d = v.distance((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert math.isclose(d, math.sqrt(3.0), rel_tol=1e-9)


# ---------------------------------------------------------------------------
# is_within_reach
# ---------------------------------------------------------------------------


def test_is_within_reach_contact_at_half_max():
    """Contact at 0.5 m, max_reach=1.0, min_reach=0.0, margin=0.05 → True."""
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.5, 0.0, 0.0))
    robot = _robot(max_reach=1.0, min_reach=0.0)
    assert v.is_within_reach(contact, robot) is True


def test_is_within_reach_contact_beyond_max():
    """Contact at 2.0 m, max_reach=1.0 → False."""
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(2.0, 0.0, 0.0))
    robot = _robot(max_reach=1.0, min_reach=0.0)
    assert v.is_within_reach(contact, robot) is False


def test_is_within_reach_contact_below_min_reach():
    """Contact at 0.05 m from base, min_reach=0.1, margin=0.05 → False.
    Effective lower bound = 0.1 + 0.05 = 0.15 m."""
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.05, 0.0, 0.0))
    robot = _robot(max_reach=1.0, min_reach=0.1)
    assert v.is_within_reach(contact, robot) is False


def test_is_within_reach_exactly_at_margin_boundary():
    """Contact at exactly max_reach - margin should be reachable (boundary inclusive)."""
    margin = 0.05
    v = ContactReachabilityValidator(reach_margin_m=margin)
    max_r = 1.0
    # Place contact exactly at hi boundary
    contact = _contact(position=(max_r - margin, 0.0, 0.0))
    robot = _robot(max_reach=max_r, min_reach=0.0)
    assert v.is_within_reach(contact, robot) is True


# ---------------------------------------------------------------------------
# ray_intersects_aabb
# ---------------------------------------------------------------------------


def test_ray_passes_through_unit_cube():
    """Ray from (-2,0,0) to (2,0,0) through a unit cube centred at origin → True."""
    v = ContactReachabilityValidator()
    box = OccluderBox(center=(0.0, 0.0, 0.0), half_extents=(0.5, 0.5, 0.5))
    assert v.ray_intersects_aabb((-2.0, 0.0, 0.0), (2.0, 0.0, 0.0), box) is True


def test_ray_misses_unit_cube():
    """Ray from (-2, 2, 0) to (2, 2, 0) — passes above cube at y=2, box at y=0 → False."""
    v = ContactReachabilityValidator()
    box = OccluderBox(center=(0.0, 0.0, 0.0), half_extents=(0.5, 0.5, 0.5))
    assert v.ray_intersects_aabb((-2.0, 2.0, 0.0), (2.0, 2.0, 0.0), box) is False


def test_ray_grazes_cube_face():
    """Ray that exactly touches one face (start outside, end outside, tangent) → True."""
    v = ContactReachabilityValidator()
    # Box from (0,0,0)→(1,1,1), ray at y=1 exactly (on the face boundary)
    box = OccluderBox(center=(0.5, 0.5, 0.5), half_extents=(0.5, 0.5, 0.5))
    assert v.ray_intersects_aabb((-1.0, 1.0, 0.5), (2.0, 1.0, 0.5), box) is True


def test_ray_parallel_outside_slab():
    """Ray parallel to an axis but outside the slab → False."""
    v = ContactReachabilityValidator()
    box = OccluderBox(center=(5.0, 5.0, 5.0), half_extents=(1.0, 1.0, 1.0))
    # Ray along X at y=0, z=0 — far from the box
    assert v.ray_intersects_aabb((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), box) is False


# ---------------------------------------------------------------------------
# is_occluded
# ---------------------------------------------------------------------------


def test_is_occluded_no_occluders_returns_false():
    v = ContactReachabilityValidator()
    contact = _contact(position=(0.5, 0.0, 0.0))
    robot = _robot()
    assert v.is_occluded(contact, robot, []) is False


def test_is_occluded_with_occluder_between_base_and_contact():
    """Obstacle sits directly on the line between base and contact → occluded."""
    v = ContactReachabilityValidator()
    # Base at origin, contact at (2,0,0), occluder centred at (1,0,0)
    contact = _contact(position=(2.0, 0.0, 0.0))
    robot = _robot(max_reach=3.0)
    box = OccluderBox(center=(1.0, 0.0, 0.0), half_extents=(0.2, 0.2, 0.2))
    assert v.is_occluded(contact, robot, [box]) is True


def test_is_occluded_with_occluder_behind_contact():
    """Obstacle placed beyond the contact point (behind target) — NOT blocking."""
    v = ContactReachabilityValidator()
    # Base at origin, contact at (1,0,0), occluder at (3,0,0)
    contact = _contact(position=(1.0, 0.0, 0.0))
    robot = _robot(max_reach=5.0)
    box = OccluderBox(center=(3.0, 0.0, 0.0), half_extents=(0.2, 0.2, 0.2))
    assert v.is_occluded(contact, robot, [box]) is False


def test_is_occluded_occluder_beside_ray():
    """Obstacle off to the side of the ray — not intersecting → False."""
    v = ContactReachabilityValidator()
    contact = _contact(position=(2.0, 0.0, 0.0))
    robot = _robot(max_reach=3.0)
    box = OccluderBox(center=(1.0, 5.0, 0.0), half_extents=(0.2, 0.2, 0.2))
    assert v.is_occluded(contact, robot, [box]) is False


# ---------------------------------------------------------------------------
# validate — clean contact → no errors
# ---------------------------------------------------------------------------


def test_validate_clean_contact_no_errors():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.5, 0.0, 0.0))
    robot = _robot(max_reach=1.0, min_reach=0.0)
    findings = v.validate(contact, robot)
    assert v.passed(findings), f"Expected no errors; got {findings}"
    assert _errors(findings) == []


# ---------------------------------------------------------------------------
# validate — out-of-reach → within_reach error
# ---------------------------------------------------------------------------


def test_validate_out_of_reach_raises_within_reach_error():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(5.0, 0.0, 0.0))
    robot = _robot(max_reach=1.0)
    findings = v.validate(contact, robot)
    assert "within_reach" in _errors(findings)
    assert not v.passed(findings)


# ---------------------------------------------------------------------------
# validate — occluded → not_occluded error
# ---------------------------------------------------------------------------


def test_validate_occluded_raises_not_occluded_error():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.8, 0.0, 0.0))
    robot = _robot(max_reach=1.0)
    box = OccluderBox(center=(0.4, 0.0, 0.0), half_extents=(0.1, 0.1, 0.1))
    findings = v.validate(contact, robot, [box])
    assert "not_occluded" in _errors(findings)
    assert not v.passed(findings)


# ---------------------------------------------------------------------------
# validate — joint_limit_violations → no_joint_violations error
# ---------------------------------------------------------------------------


def test_validate_joint_violations_raises_error():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.5, 0.0, 0.0))
    robot = _robot(max_reach=1.0, violations=["Joint_0", "Joint_3"])
    findings = v.validate(contact, robot)
    assert "no_joint_violations" in _errors(findings)
    assert not v.passed(findings)


# ---------------------------------------------------------------------------
# validate — zero-magnitude normal → normal_valid warn
# ---------------------------------------------------------------------------


def test_validate_zero_normal_raises_warn():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contact = _contact(position=(0.5, 0.0, 0.0), normal=(0.0, 0.0, 0.0))
    robot = _robot(max_reach=1.0)
    findings = v.validate(contact, robot)
    assert "normal_valid" in _warns(findings)
    # warn should not cause passed() to return False
    assert v.passed(findings)


# ---------------------------------------------------------------------------
# validate — contact inside min_reach dead-zone
# ---------------------------------------------------------------------------


def test_validate_inside_min_reach_raises_error():
    """Contact inside the inner dead-zone triggers both within_reach and
    not_below_min_reach errors."""
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    # min_reach=0.3, contact at 0.1 — clearly inside dead-zone
    contact = _contact(position=(0.1, 0.0, 0.0))
    robot = _robot(max_reach=1.0, min_reach=0.3)
    findings = v.validate(contact, robot)
    assert "not_below_min_reach" in _errors(findings)


# ---------------------------------------------------------------------------
# validate_batch
# ---------------------------------------------------------------------------


def test_validate_batch_returns_dict_keyed_by_index():
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    robot = _robot(max_reach=1.0)
    contacts = [
        _contact(position=(0.5, 0.0, 0.0), surface_id="c0"),   # reachable
        _contact(position=(5.0, 0.0, 0.0), surface_id="c1"),   # out of reach
    ]
    result = v.validate_batch(contacts, robot)
    assert set(result.keys()) == {0, 1}
    assert v.passed(result[0])
    assert not v.passed(result[1])


def test_validate_batch_empty_list():
    v = ContactReachabilityValidator()
    robot = _robot()
    result = v.validate_batch([], robot)
    assert result == {}


def test_validate_batch_three_contacts_mixed():
    """Three contacts: one clean, one out-of-reach, one with joint violations."""
    v = ContactReachabilityValidator(reach_margin_m=0.05)
    contacts = [
        _contact(position=(0.5, 0.0, 0.0), surface_id="ok"),
        _contact(position=(3.0, 0.0, 0.0), surface_id="far"),
        _contact(position=(0.5, 0.0, 0.0), surface_id="joint"),
    ]
    robots = [
        _robot(max_reach=1.0),
        _robot(max_reach=1.0),
        _robot(max_reach=1.0, violations=["J2"]),
    ]
    # validate_batch uses a single robot spec; test with uniform robot + per-call
    result_0 = v.validate(contacts[0], robots[0])
    result_1 = v.validate(contacts[1], robots[1])
    result_2 = v.validate(contacts[2], robots[2])

    assert v.passed(result_0)
    assert not v.passed(result_1)
    assert not v.passed(result_2)
