"""Phase 67 — pytest gate: JointSpawnValidator.

All tests are l0 (pure-Python, no external dependencies).
Gate: validator must catch missing body/articulation root, mis-typed axis,
and invalid range.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 67.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.spawn_validator_joint import (
    JointPrimState,
    JointSpawnValidator,
    JointValidationFinding,
    expected_validator_checks,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clean_revolute(**kwargs) -> JointPrimState:
    """Return a fully valid revolute JointPrimState; override any field via kwargs."""
    defaults = dict(
        prim_path="/World/Robot/RevoluteJoint",
        joint_type="revolute",
        body0="/World/Robot/Link0",
        body1="/World/Robot/Link1",
        axis="Z",
        lower_limit=-90.0,
        upper_limit=90.0,
        articulation_root_path="/World/Robot",
        exists=True,
    )
    defaults.update(kwargs)
    return JointPrimState(**defaults)


def _errors(findings: list[JointValidationFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "error"]


def _warns(findings: list[JointValidationFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "warn"]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def test_phase_67_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 67


def test_phase_67_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_67_metadata_spec_ref_present():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "67" in md["spec_ref"]


# ---------------------------------------------------------------------------
# expected_validator_checks
# ---------------------------------------------------------------------------


def test_expected_validator_checks_returns_eight_or_more():
    checks = expected_validator_checks()
    assert len(checks) >= 8, f"Expected ≥8 check_ids, got {len(checks)}: {checks}"


def test_expected_validator_checks_contains_required_ids():
    checks = expected_validator_checks()
    required = {
        "prim_exists",
        "body0_set",
        "body1_set",
        "axis_set",
        "axis_valid",
        "limits_consistent",
        "articulation_root",
        "joint_type_known",
    }
    missing = required - set(checks)
    assert not missing, f"Missing check_ids: {missing}"


# ---------------------------------------------------------------------------
# Clean revolute joint — should produce zero errors
# ---------------------------------------------------------------------------


def test_clean_revolute_passes():
    validator = JointSpawnValidator()
    state = _make_clean_revolute()
    findings = validator.validate(state)
    assert validator.passed(findings), (
        f"Expected clean revolute to pass but got findings: {findings}"
    )
    assert _errors(findings) == [], f"Unexpected errors: {_errors(findings)}"


# ---------------------------------------------------------------------------
# prim_exists
# ---------------------------------------------------------------------------


def test_missing_prim_raises_prim_exists_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(exists=False)
    findings = validator.validate(state)
    assert "prim_exists" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# body0_set
# ---------------------------------------------------------------------------


def test_missing_body0_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(body0=None)
    findings = validator.validate(state)
    assert "body0_set" in _errors(findings)


def test_empty_body0_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(body0="")
    findings = validator.validate(state)
    assert "body0_set" in _errors(findings)


# ---------------------------------------------------------------------------
# body1_set
# ---------------------------------------------------------------------------


def test_revolute_without_body1_raises_warn():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(body1=None)
    findings = validator.validate(state)
    assert "body1_set" in _warns(findings), (
        f"Expected body1_set warn; errors={_errors(findings)}, warns={_warns(findings)}"
    )
    # Should still be warn (not error) in non-strict mode
    assert "body1_set" not in _errors(findings)


def test_fixed_joint_without_body1_raises_warn_not_error():
    """Fixed joint anchored to world — valid use-case, so warn severity only."""
    validator = JointSpawnValidator()
    state = _make_clean_revolute(joint_type="fixed", axis=None, body1=None)
    findings = validator.validate(state)
    assert "body1_set" in _warns(findings)
    assert "body1_set" not in _errors(findings)


# ---------------------------------------------------------------------------
# axis_set
# ---------------------------------------------------------------------------


def test_revolute_without_axis_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(axis=None)
    findings = validator.validate(state)
    assert "axis_set" in _errors(findings)


def test_prismatic_without_axis_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(joint_type="prismatic", axis=None)
    findings = validator.validate(state)
    assert "axis_set" in _errors(findings)


def test_fixed_joint_without_axis_does_not_raise_axis_set_error():
    """Fixed joints do not require an axis — no axis_set error expected."""
    validator = JointSpawnValidator()
    state = _make_clean_revolute(joint_type="fixed", axis=None)
    findings = validator.validate(state)
    assert "axis_set" not in _errors(findings)


# ---------------------------------------------------------------------------
# axis_valid
# ---------------------------------------------------------------------------


def test_bad_axis_raises_axis_valid_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(axis="W")  # type: ignore[arg-type]
    findings = validator.validate(state)
    assert "axis_valid" in _errors(findings)


def test_lowercase_axis_raises_axis_valid_error():
    """Lowercase 'x' is not the canonical form — should be caught."""
    validator = JointSpawnValidator()
    state = _make_clean_revolute(axis="x")  # type: ignore[arg-type]
    findings = validator.validate(state)
    assert "axis_valid" in _errors(findings)


# ---------------------------------------------------------------------------
# limits_consistent
# ---------------------------------------------------------------------------


def test_inverted_limits_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(lower_limit=90.0, upper_limit=-90.0)
    findings = validator.validate(state)
    assert "limits_consistent" in _errors(findings)


def test_equal_limits_raises_error():
    """lower == upper is also invalid (degenerate range)."""
    validator = JointSpawnValidator()
    state = _make_clean_revolute(lower_limit=0.0, upper_limit=0.0)
    findings = validator.validate(state)
    assert "limits_consistent" in _errors(findings)


def test_valid_limits_no_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(lower_limit=-180.0, upper_limit=180.0)
    findings = validator.validate(state)
    assert "limits_consistent" not in _errors(findings)


def test_none_limits_no_error():
    """Omitting limits entirely is allowed (unlimited joint)."""
    validator = JointSpawnValidator()
    state = _make_clean_revolute(lower_limit=None, upper_limit=None)
    findings = validator.validate(state)
    assert "limits_consistent" not in _errors(findings)


# ---------------------------------------------------------------------------
# articulation_root
# ---------------------------------------------------------------------------


def test_missing_articulation_root_raises_warn():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(articulation_root_path=None)
    findings = validator.validate(state)
    assert "articulation_root" in _warns(findings)
    assert "articulation_root" not in _errors(findings)


# ---------------------------------------------------------------------------
# joint_type_known
# ---------------------------------------------------------------------------


def test_unknown_joint_type_raises_error():
    validator = JointSpawnValidator()
    state = _make_clean_revolute(joint_type="gear")  # type: ignore[arg-type]
    findings = validator.validate(state)
    assert "joint_type_known" in _errors(findings)


# ---------------------------------------------------------------------------
# strict=True — warns promoted to errors
# ---------------------------------------------------------------------------


def test_strict_mode_promotes_warn_to_error():
    """With strict=True, articulation_root warn must become an error."""
    validator = JointSpawnValidator(strict=True)
    state = _make_clean_revolute(articulation_root_path=None)
    findings = validator.validate(state)
    # In strict mode the warn is promoted — check_id appears as error
    assert "articulation_root" in _errors(findings)


def test_strict_mode_passed_returns_false_for_warn_promoted():
    """passed() must return False if any warn was promoted to error."""
    validator = JointSpawnValidator(strict=True)
    # Missing articulation_root produces a warn → promoted to error in strict mode
    state = _make_clean_revolute(articulation_root_path=None)
    findings = validator.validate(state)
    assert not validator.passed(findings)


def test_non_strict_mode_passed_true_for_warn_only():
    """In non-strict mode, only warns → passed() returns True."""
    validator = JointSpawnValidator(strict=False)
    state = _make_clean_revolute(articulation_root_path=None)
    findings = validator.validate(state)
    # Only a warn present — should still pass
    assert validator.passed(findings)


# ---------------------------------------------------------------------------
# validate_batch
# ---------------------------------------------------------------------------


def test_validate_batch_returns_dict_keyed_by_prim_path():
    validator = JointSpawnValidator()
    states = [
        _make_clean_revolute(prim_path="/World/Joint0"),
        _make_clean_revolute(prim_path="/World/Joint1", exists=False),
    ]
    result = validator.validate_batch(states)
    assert set(result.keys()) == {"/World/Joint0", "/World/Joint1"}
    # Clean joint — no errors
    assert validator.passed(result["/World/Joint0"])
    # Missing prim — error present
    assert not validator.passed(result["/World/Joint1"])


def test_validate_batch_empty_list():
    validator = JointSpawnValidator()
    result = validator.validate_batch([])
    assert result == {}
