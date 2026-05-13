"""Phase 68 — pytest gate: APISchemaValidator.

All tests are l0 (pure-Python, no external dependencies).
Gate: validator catches unknown schema, conflicting schemas, missing
required attrs, and advisory warnings (prim_type not set).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 68.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.spawn_validator_api_schema import (
    APISchemaFinding,
    APISchemaState,
    APISchemaValidator,
    KNOWN_API_SCHEMAS,
    SCHEMA_CONFLICTS,
    SCHEMA_REQUIRED_ATTRS,
    expected_validator_checks,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clean_rigid_body(**kwargs) -> APISchemaState:
    """Return a fully valid PhysicsRigidBodyAPI state; override via kwargs."""
    defaults = dict(
        prim_path="/World/Robot/Link0",
        schema_name="PhysicsRigidBodyAPI",
        applied=True,
        required_attributes_present=["physics:rigidBodyEnabled"],
        required_attributes_missing=[],
        conflicts_with=[],
        prim_type="Xform",
    )
    defaults.update(kwargs)
    return APISchemaState(**defaults)


def _errors(findings: list[APISchemaFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "error"]


def _warns(findings: list[APISchemaFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "warn"]


def _infos(findings: list[APISchemaFinding]) -> list[str]:
    return [f.check_id for f in findings if f.severity == "info"]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def test_phase_68_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 68


def test_phase_68_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_68_metadata_spec_ref_present():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "68" in md["spec_ref"]


# ---------------------------------------------------------------------------
# KNOWN_API_SCHEMAS
# ---------------------------------------------------------------------------


def test_known_api_schemas_has_at_least_15_entries():
    assert len(KNOWN_API_SCHEMAS) >= 15, (
        f"Expected ≥15 entries in KNOWN_API_SCHEMAS, got {len(KNOWN_API_SCHEMAS)}"
    )


def test_known_api_schemas_contains_required_schemas():
    required = {
        "PhysicsRigidBodyAPI",
        "PhysicsCollisionAPI",
        "PhysicsMassAPI",
        "PhysicsArticulationRootAPI",
        "PhysicsRevoluteJoint",
        "PhysicsPrismaticJoint",
        "PhysxRigidBodyAPI",
        "PhysxJointAPI",
        "PhysxArticulationAPI",
        "MaterialBindingAPI",
        "SemanticsAPI",
        "KindAPI",
        "XformCommonAPI",
        "LightAPI",
        "MeshSimplificationAPI",
    }
    missing = required - KNOWN_API_SCHEMAS
    assert not missing, f"Missing from KNOWN_API_SCHEMAS: {missing}"


# ---------------------------------------------------------------------------
# SCHEMA_CONFLICTS
# ---------------------------------------------------------------------------


def test_schema_conflicts_has_at_least_one_pair():
    assert len(SCHEMA_CONFLICTS) >= 1, (
        "SCHEMA_CONFLICTS must have at least one entry"
    )


def test_schema_conflicts_articulation_root_vs_rigid_body():
    assert "PhysicsRigidBodyAPI" in SCHEMA_CONFLICTS.get(
        "PhysicsArticulationRootAPI", []
    ), "PhysicsArticulationRootAPI must conflict with PhysicsRigidBodyAPI"


def test_schema_conflicts_revolute_vs_prismatic():
    assert "PhysicsPrismaticJoint" in SCHEMA_CONFLICTS.get(
        "PhysicsRevoluteJoint", []
    ), "PhysicsRevoluteJoint must conflict with PhysicsPrismaticJoint"


# ---------------------------------------------------------------------------
# SCHEMA_REQUIRED_ATTRS
# ---------------------------------------------------------------------------


def test_schema_required_attrs_has_at_least_4_entries():
    assert len(SCHEMA_REQUIRED_ATTRS) >= 4, (
        f"Expected ≥4 entries in SCHEMA_REQUIRED_ATTRS, got {len(SCHEMA_REQUIRED_ATTRS)}"
    )


def test_schema_required_attrs_physics_rigid_body():
    assert "physics:rigidBodyEnabled" in SCHEMA_REQUIRED_ATTRS.get(
        "PhysicsRigidBodyAPI", []
    )


def test_schema_required_attrs_physics_mass():
    assert "physics:mass" in SCHEMA_REQUIRED_ATTRS.get("PhysicsMassAPI", [])


# ---------------------------------------------------------------------------
# expected_validator_checks
# ---------------------------------------------------------------------------


def test_expected_validator_checks_returns_five_or_more():
    checks = expected_validator_checks()
    assert len(checks) >= 5, (
        f"Expected ≥5 check_ids, got {len(checks)}: {checks}"
    )


def test_expected_validator_checks_contains_required_ids():
    checks = expected_validator_checks()
    required = {
        "schema_known",
        "applied",
        "required_attrs_present",
        "no_conflicts",
        "prim_type_set",
    }
    missing = required - set(checks)
    assert not missing, f"Missing check_ids: {missing}"


# ---------------------------------------------------------------------------
# Clean state — PhysicsRigidBodyAPI fully applied → passes
# ---------------------------------------------------------------------------


def test_clean_rigid_body_state_passes():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body()
    findings = validator.validate(state)
    assert validator.passed(findings), (
        f"Expected clean state to pass but got findings: {findings}"
    )
    assert _errors(findings) == [], f"Unexpected errors: {_errors(findings)}"


# ---------------------------------------------------------------------------
# schema_known — unknown schema name
# ---------------------------------------------------------------------------


def test_unknown_schema_name_raises_schema_known_error():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(schema_name="NonExistentFooAPI")
    findings = validator.validate(state)
    assert "schema_known" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# applied — schema not applied
# ---------------------------------------------------------------------------


def test_applied_false_raises_applied_error():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(applied=False)
    findings = validator.validate(state)
    assert "applied" in _errors(findings)
    assert not validator.passed(findings)


# ---------------------------------------------------------------------------
# required_attrs_present — missing required attributes
# ---------------------------------------------------------------------------


def test_required_attributes_missing_raises_error():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(
        required_attributes_missing=["physics:rigidBodyEnabled"]
    )
    findings = validator.validate(state)
    assert "required_attrs_present" in _errors(findings)
    assert not validator.passed(findings)


def test_no_required_attributes_missing_no_error():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(required_attributes_missing=[])
    findings = validator.validate(state)
    assert "required_attrs_present" not in _errors(findings)


# ---------------------------------------------------------------------------
# no_conflicts — conflicting schemas co-applied
# ---------------------------------------------------------------------------


def test_conflicts_with_known_schema_raises_no_conflicts_error():
    validator = APISchemaValidator()
    # PhysicsArticulationRootAPI conflicts with PhysicsRigidBodyAPI
    state = APISchemaState(
        prim_path="/World/Robot",
        schema_name="PhysicsArticulationRootAPI",
        applied=True,
        required_attributes_present=[],
        required_attributes_missing=[],
        conflicts_with=["PhysicsRigidBodyAPI"],
        prim_type="Xform",
    )
    findings = validator.validate(state)
    assert "no_conflicts" in _errors(findings)
    assert not validator.passed(findings)


def test_non_conflicting_co_applied_schema_no_error():
    validator = APISchemaValidator()
    # PhysicsRigidBodyAPI does not conflict with PhysicsCollisionAPI
    state = _make_clean_rigid_body(conflicts_with=["PhysicsCollisionAPI"])
    findings = validator.validate(state)
    assert "no_conflicts" not in _errors(findings)


# ---------------------------------------------------------------------------
# prim_type_set — None triggers warn
# ---------------------------------------------------------------------------


def test_prim_type_none_raises_prim_type_set_warn():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(prim_type=None)
    findings = validator.validate(state)
    assert "prim_type_set" in _warns(findings)
    # Should not be an error in non-strict mode
    assert "prim_type_set" not in _errors(findings)


def test_prim_type_set_no_warn():
    validator = APISchemaValidator()
    state = _make_clean_rigid_body(prim_type="Xform")
    findings = validator.validate(state)
    assert "prim_type_set" not in _warns(findings)


# ---------------------------------------------------------------------------
# strict mode — warns promoted to errors
# ---------------------------------------------------------------------------


def test_strict_mode_promotes_prim_type_warn_to_error():
    validator = APISchemaValidator(strict=True)
    state = _make_clean_rigid_body(prim_type=None)
    findings = validator.validate(state)
    assert "prim_type_set" in _errors(findings)


def test_strict_mode_passed_returns_false_for_promoted_warn():
    validator = APISchemaValidator(strict=True)
    state = _make_clean_rigid_body(prim_type=None)
    findings = validator.validate(state)
    assert not validator.passed(findings)


def test_non_strict_mode_passed_true_for_warn_only():
    validator = APISchemaValidator(strict=False)
    state = _make_clean_rigid_body(prim_type=None)
    findings = validator.validate(state)
    # Only a warn present — should still pass in non-strict
    assert validator.passed(findings)


# ---------------------------------------------------------------------------
# validate_batch — keyed by prim_path
# ---------------------------------------------------------------------------


def test_validate_batch_returns_dict_keyed_by_prim_path():
    validator = APISchemaValidator()
    states = [
        _make_clean_rigid_body(prim_path="/World/Link0"),
        _make_clean_rigid_body(prim_path="/World/Link1", applied=False),
    ]
    result = validator.validate_batch(states)
    assert set(result.keys()) == {"/World/Link0", "/World/Link1"}
    assert validator.passed(result["/World/Link0"])
    assert not validator.passed(result["/World/Link1"])


def test_validate_batch_empty_list():
    validator = APISchemaValidator()
    result = validator.validate_batch([])
    assert result == {}
