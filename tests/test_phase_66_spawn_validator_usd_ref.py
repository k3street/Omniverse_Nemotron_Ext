"""Phase 66 — pytest gate: USDReferenceValidator.

All tests are l0 (pure-Python, no external dependencies).
Gate: validator must catch dangling reference, asset_not_found, depth-limit,
and circular reference.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 66.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.spawn_validator_usd_ref import (
    USDReferenceFinding,
    USDReferenceState,
    USDReferenceValidator,
    expected_validator_checks,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clean_ref(**kwargs) -> USDReferenceState:
    """Return a fully valid USDReferenceState; override any field via kwargs."""
    defaults = dict(
        prim_path="/World/Robot",
        reference_target="omniverse://localhost/Assets/robot.usd",
        asset_exists=True,
        asset_size_bytes=1024 * 1024,  # 1 MB
        prim_type_after="Xform",
        parent_path="/World",
        depth=1,
        is_circular=False,
    )
    defaults.update(kwargs)
    return USDReferenceState(**defaults)


def _errors(findings: list[USDReferenceFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "error"]


def _warns(findings: list[USDReferenceFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "warn"]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def test_phase_66_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 66


def test_phase_66_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_66_metadata_spec_ref_present():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "66" in md["spec_ref"]


# ---------------------------------------------------------------------------
# expected_validator_checks
# ---------------------------------------------------------------------------


def test_expected_validator_checks_returns_seven_or_more():
    checks = expected_validator_checks()
    assert len(checks) >= 7, f"Expected ≥7 check_ids, got {len(checks)}: {checks}"


def test_expected_validator_checks_contains_required_ids():
    checks = expected_validator_checks()
    required = {
        "target_set",
        "asset_exists",
        "asset_too_large",
        "prim_type_resolved",
        "parent_exists",
        "depth_within_limit",
        "not_circular",
        "target_extension",
    }
    missing = required - set(checks)
    assert not missing, f"Missing check_ids: {missing}"


# ---------------------------------------------------------------------------
# Clean reference — should produce zero errors
# ---------------------------------------------------------------------------


def test_clean_ref_passes():
    validator = USDReferenceValidator()
    state = _make_clean_ref()
    findings = validator.validate(state)
    assert validator.passed(findings), (
        f"Expected clean reference to pass but got findings: {findings}"
    )
    assert _errors(findings) == [], f"Unexpected errors: {_errors(findings)}"


# ---------------------------------------------------------------------------
# target_set
# ---------------------------------------------------------------------------


def test_empty_target_raises_target_set_error():
    validator = USDReferenceValidator()
    state = _make_clean_ref(reference_target="")
    findings = validator.validate(state)
    assert "target_set" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# asset_exists
# ---------------------------------------------------------------------------


def test_asset_not_found_raises_asset_exists_error():
    validator = USDReferenceValidator()
    state = _make_clean_ref(asset_exists=False)
    findings = validator.validate(state)
    assert "asset_exists" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# asset_too_large
# ---------------------------------------------------------------------------


def test_oversize_asset_raises_warn():
    validator = USDReferenceValidator(max_size_mb=100)
    # 200 MB exceeds the 100 MB limit
    state = _make_clean_ref(asset_size_bytes=200 * 1024 * 1024)
    findings = validator.validate(state)
    assert "asset_too_large" in _warns(findings)
    # Warn only — should still pass in non-strict mode
    assert validator.passed(findings)


def test_asset_within_size_limit_no_warn():
    validator = USDReferenceValidator(max_size_mb=500)
    state = _make_clean_ref(asset_size_bytes=100 * 1024 * 1024)
    findings = validator.validate(state)
    assert "asset_too_large" not in _warns(findings)


# ---------------------------------------------------------------------------
# prim_type_resolved
# ---------------------------------------------------------------------------


def test_none_prim_type_raises_prim_type_resolved_warn():
    validator = USDReferenceValidator()
    state = _make_clean_ref(prim_type_after=None)
    findings = validator.validate(state)
    assert "prim_type_resolved" in _warns(findings)
    # Warn only — should still pass
    assert validator.passed(findings)


# ---------------------------------------------------------------------------
# parent_exists
# ---------------------------------------------------------------------------


def test_nested_path_without_parent_raises_parent_exists_error():
    validator = USDReferenceValidator()
    state = _make_clean_ref(
        prim_path="/World/Robot/Arm",
        parent_path=None,
    )
    findings = validator.validate(state)
    assert "parent_exists" in _errors(findings)
    assert not validator.passed(findings)


def test_root_path_without_parent_no_parent_exists_error():
    """Top-level prim like /World does not require a recorded parent_path."""
    validator = USDReferenceValidator()
    state = _make_clean_ref(
        prim_path="/World",
        parent_path=None,
    )
    findings = validator.validate(state)
    assert "parent_exists" not in _errors(findings)


def test_nested_path_with_parent_no_parent_exists_error():
    validator = USDReferenceValidator()
    state = _make_clean_ref(
        prim_path="/World/Robot",
        parent_path="/World",
    )
    findings = validator.validate(state)
    assert "parent_exists" not in _errors(findings)


# ---------------------------------------------------------------------------
# depth_within_limit
# ---------------------------------------------------------------------------


def test_depth_exceeds_limit_raises_error():
    validator = USDReferenceValidator(max_depth=8)
    state = _make_clean_ref(depth=10)
    findings = validator.validate(state)
    assert "depth_within_limit" in _errors(findings)
    assert not validator.passed(findings)


def test_depth_at_limit_no_error():
    validator = USDReferenceValidator(max_depth=8)
    state = _make_clean_ref(depth=8)
    findings = validator.validate(state)
    assert "depth_within_limit" not in _errors(findings)


# ---------------------------------------------------------------------------
# not_circular
# ---------------------------------------------------------------------------


def test_circular_reference_raises_not_circular_error():
    validator = USDReferenceValidator()
    state = _make_clean_ref(is_circular=True)
    findings = validator.validate(state)
    assert "not_circular" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# target_extension
# ---------------------------------------------------------------------------


def test_txt_extension_raises_target_extension_warn():
    validator = USDReferenceValidator()
    state = _make_clean_ref(reference_target="omniverse://localhost/Assets/robot.txt")
    findings = validator.validate(state)
    assert "target_extension" in _warns(findings)
    # Warn only — should still pass
    assert validator.passed(findings)


def test_usd_extension_no_target_extension_warn():
    for ext in (".usd", ".usda", ".usdc", ".usdz"):
        validator = USDReferenceValidator()
        state = _make_clean_ref(
            reference_target=f"omniverse://localhost/Assets/robot{ext}"
        )
        findings = validator.validate(state)
        assert "target_extension" not in _warns(findings), (
            f"Unexpected target_extension warn for extension '{ext}'"
        )


# ---------------------------------------------------------------------------
# strict=True — warns promoted to errors
# ---------------------------------------------------------------------------


def test_strict_mode_promotes_prim_type_warn_to_error():
    """With strict=True, prim_type_resolved warn must become an error."""
    validator = USDReferenceValidator(strict=True)
    state = _make_clean_ref(prim_type_after=None)
    findings = validator.validate(state)
    assert "prim_type_resolved" in _errors(findings)


def test_strict_mode_passed_returns_false_for_promoted_warn():
    """passed() must return False if any warn was promoted to error."""
    validator = USDReferenceValidator(strict=True)
    state = _make_clean_ref(prim_type_after=None)
    findings = validator.validate(state)
    assert not validator.passed(findings)


def test_non_strict_mode_passed_true_for_warn_only():
    """In non-strict mode, warns alone → passed() returns True."""
    validator = USDReferenceValidator(strict=False)
    state = _make_clean_ref(prim_type_after=None)
    findings = validator.validate(state)
    assert validator.passed(findings)


# ---------------------------------------------------------------------------
# validate_batch
# ---------------------------------------------------------------------------


def test_validate_batch_returns_dict_keyed_by_prim_path():
    validator = USDReferenceValidator()
    states = [
        _make_clean_ref(prim_path="/World/RobotA"),
        _make_clean_ref(prim_path="/World/RobotB", asset_exists=False),
    ]
    result = validator.validate_batch(states)
    assert set(result.keys()) == {"/World/RobotA", "/World/RobotB"}
    # Clean reference — no errors
    assert validator.passed(result["/World/RobotA"])
    # Dangling reference — error present
    assert not validator.passed(result["/World/RobotB"])


def test_validate_batch_empty_list():
    validator = USDReferenceValidator()
    result = validator.validate_batch([])
    assert result == {}
