"""
L0 tests for CRM-C1 — compliance_mode / compliance_params /
compliance_handoff_at fields on LayoutSpec.

Verification: pytest tests/test_layout_spec_compliance_field.py (>=6 tests)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(**overrides):
    """Build a minimal valid LayoutSpec; compliance overrides forwarded."""
    from service.isaac_assist_service.multimodal import (
        Counts,
        Intent,
        LayoutSpec,
        Source,
        StructuralFeatures,
    )

    intent = Intent(
        pattern_hint="pick_place",
        counts=Counts(robots=1),
        structural_features=StructuralFeatures(),
        structural_tags=[],
    )
    return LayoutSpec(
        intent=intent,
        source=Source(modality="text", confidence=1.0),
        **overrides,
    )


def _validate(spec):
    from service.isaac_assist_service.multimodal.validate import validate_layout_spec

    return validate_layout_spec(spec)


# ---------------------------------------------------------------------------
# Field-absent / default tests
# ---------------------------------------------------------------------------

class TestComplianceFieldDefaults:
    def test_fields_absent_gives_no_error(self):
        """A LayoutSpec with no compliance fields should validate cleanly."""
        spec = _make_spec()
        result = _validate(spec)
        compliance_errors = [
            i for i in result.errors if i.code.startswith("compliance.")
        ]
        assert compliance_errors == [], compliance_errors

    def test_compliance_mode_defaults_to_none(self):
        spec = _make_spec()
        assert spec.compliance_mode is None

    def test_compliance_params_defaults_to_empty_dict(self):
        spec = _make_spec()
        assert spec.compliance_params == {}

    def test_compliance_handoff_at_defaults_to_0_5(self):
        spec = _make_spec()
        assert spec.compliance_handoff_at == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compliance_mode — enum membership
# ---------------------------------------------------------------------------

class TestComplianceModeEnum:
    @pytest.mark.parametrize("mode", [
        "admittance",
        "cartesian_compliance_fdcc",
        "cartesian_impedance",
        "variable_impedance",
        "franka_cartesian_impedance",
        "null",
    ])
    def test_valid_enum_member_passes(self, mode: str):
        spec = _make_spec(compliance_mode=mode)
        result = _validate(spec)
        mode_errors = [i for i in result.errors if i.code == "compliance.unknown_mode"]
        assert mode_errors == [], f"mode={mode!r} should be valid but got: {mode_errors}"

    def test_none_mode_passes(self):
        spec = _make_spec(compliance_mode=None)
        result = _validate(spec)
        mode_errors = [i for i in result.errors if i.code == "compliance.unknown_mode"]
        assert mode_errors == []

    def test_bogus_mode_rejected(self):
        spec = _make_spec(compliance_mode="bogus")
        result = _validate(spec)
        assert not result.valid
        codes = [i.code for i in result.errors]
        assert "compliance.unknown_mode" in codes

    def test_mixed_case_mode_rejected(self):
        """Mode matching is case-sensitive; 'Admittance' is not in the enum."""
        spec = _make_spec(compliance_mode="Admittance")
        result = _validate(spec)
        assert not result.valid
        codes = [i.code for i in result.errors]
        assert "compliance.unknown_mode" in codes

    def test_empty_string_mode_rejected(self):
        spec = _make_spec(compliance_mode="")
        result = _validate(spec)
        assert not result.valid
        codes = [i.code for i in result.errors]
        assert "compliance.unknown_mode" in codes


# ---------------------------------------------------------------------------
# compliance_handoff_at — range [0, 1]
# ---------------------------------------------------------------------------

class TestComplianceHandoffAt:
    def test_handoff_at_zero_is_valid(self):
        spec = _make_spec(compliance_handoff_at=0.0)
        result = _validate(spec)
        assert result.valid

    def test_handoff_at_one_is_valid(self):
        spec = _make_spec(compliance_handoff_at=1.0)
        result = _validate(spec)
        assert result.valid

    def test_handoff_at_0_7_is_valid(self):
        spec = _make_spec(compliance_handoff_at=0.7)
        result = _validate(spec)
        assert result.valid
        assert spec.compliance_handoff_at == pytest.approx(0.7)

    def test_handoff_at_above_one_rejected_by_pydantic(self):
        """Pydantic enforces le=1.0 at construction; validate.py need not re-check."""
        with pytest.raises(ValidationError):
            _make_spec(compliance_handoff_at=1.5)

    def test_handoff_at_negative_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            _make_spec(compliance_handoff_at=-0.1)


# ---------------------------------------------------------------------------
# compliance_params — free-form dict, no nested validation
# ---------------------------------------------------------------------------

class TestComplianceParams:
    def test_params_dict_with_list_value_passes(self):
        """No nested validation — any dict value is accepted."""
        spec = _make_spec(compliance_params={"K": [400, 400, 200]})
        result = _validate(spec)
        params_errors = [i for i in result.errors if i.code == "compliance.params_not_dict"]
        assert params_errors == []

    def test_params_dict_with_nested_dict_passes(self):
        spec = _make_spec(compliance_params={"gains": {"Kp": 500.0, "Kd": 20.0}})
        result = _validate(spec)
        params_errors = [i for i in result.errors if i.code == "compliance.params_not_dict"]
        assert params_errors == []

    def test_params_empty_dict_passes(self):
        spec = _make_spec(compliance_params={})
        result = _validate(spec)
        params_errors = [i for i in result.errors if i.code == "compliance.params_not_dict"]
        assert params_errors == []

    def test_all_three_fields_together_valid(self):
        """Smoke: all three fields set together with a valid enum member."""
        spec = _make_spec(
            compliance_mode="admittance",
            compliance_params={"K": [400, 400, 200], "D": [40, 40, 20]},
            compliance_handoff_at=0.6,
        )
        result = _validate(spec)
        assert result.valid
        assert spec.compliance_mode == "admittance"
        assert spec.compliance_params == {"K": [400, 400, 200], "D": [40, 40, 20]}
        assert spec.compliance_handoff_at == pytest.approx(0.6)
