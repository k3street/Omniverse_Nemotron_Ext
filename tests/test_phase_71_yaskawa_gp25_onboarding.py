"""Phase 71 — Yaskawa GP25 onboarding spec/data tests.

Tests the static SPEC + DATA layer. No runtime (Kit RPC / Nucleus)
required. Gate: all tests pass under pytest --tb=short.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.yaskawa_gp25_onboarding import (
    GP25_SPEC,
    ONBOARDING_CHECKLIST,
    PHASE_STATUS,
    YaskawaGP25Spec,
    OnboardingStep,
    get_phase_metadata,
    gp25_to_robot_wizard_entry,
    steps_without_runtime,
)


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------

def test_phase_71_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 71


def test_phase_71_metadata_status_landed():
    """SPEC/DATA layer must be 'landed', not 'scaffold'."""
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_71_metadata_has_spec_ref():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "71" in md["spec_ref"]


# ---------------------------------------------------------------------------
# GP25_SPEC field presence tests
# ---------------------------------------------------------------------------

def test_gp25_spec_is_frozen_dataclass():
    """GP25_SPEC must be a frozen YaskawaGP25Spec instance.

    Frozen dataclasses raise FrozenInstanceError (subclass of AttributeError)
    on direct attribute assignment. object.__setattr__ bypasses the frozen
    guard, so we test via normal assignment syntax.
    """
    assert isinstance(GP25_SPEC, YaskawaGP25Spec)
    # Normal assignment must raise FrozenInstanceError (subclass of AttributeError)
    with pytest.raises(AttributeError):
        GP25_SPEC.payload_kg = 0.0  # type: ignore[misc]


def test_gp25_spec_required_fields_present():
    """All required fields must be present and non-empty/non-zero."""
    assert GP25_SPEC.name
    assert GP25_SPEC.manufacturer
    assert GP25_SPEC.model
    assert GP25_SPEC.payload_kg > 0
    assert GP25_SPEC.reach_m > 0
    assert GP25_SPEC.repeatability_mm > 0
    assert GP25_SPEC.dof > 0
    assert GP25_SPEC.weight_kg > 0
    assert GP25_SPEC.controller_model
    assert GP25_SPEC.nucleus_asset_path
    assert GP25_SPEC.urdf_path


# ---------------------------------------------------------------------------
# Key spec value tests
# ---------------------------------------------------------------------------

def test_gp25_spec_name_manufacturer():
    assert GP25_SPEC.name == "Yaskawa GP25"
    assert GP25_SPEC.manufacturer == "Yaskawa"
    assert GP25_SPEC.model == "GP25"


def test_gp25_spec_payload_kg():
    assert GP25_SPEC.payload_kg == 25.0


def test_gp25_spec_reach_m():
    """Reach must be approximately 1.730 m (within 1 mm tolerance)."""
    assert abs(GP25_SPEC.reach_m - 1.730) < 0.001


def test_gp25_spec_dof():
    assert GP25_SPEC.dof == 6


def test_gp25_spec_controller():
    assert GP25_SPEC.controller_model == "YRC1000"


# ---------------------------------------------------------------------------
# Joint limits tests
# ---------------------------------------------------------------------------

def test_gp25_joint_limits_has_6_entries():
    assert len(GP25_SPEC.joint_limits_deg) == 6


def test_gp25_joint_limits_each_entry_valid():
    """Each joint limit entry must be (lo, hi) with lo < hi."""
    for i, (lo, hi) in enumerate(GP25_SPEC.joint_limits_deg):
        assert lo < hi, f"Joint {i+1}: expected lo < hi, got lo={lo}, hi={hi}"


# ---------------------------------------------------------------------------
# Joint velocity limits tests
# ---------------------------------------------------------------------------

def test_gp25_joint_velocity_limits_has_6_entries():
    assert len(GP25_SPEC.joint_velocity_limits_dps) == 6


def test_gp25_joint_velocity_limits_all_positive():
    for i, v in enumerate(GP25_SPEC.joint_velocity_limits_dps):
        assert v > 0, f"Joint {i+1} velocity limit must be positive, got {v}"


# ---------------------------------------------------------------------------
# Protocol options tests
# ---------------------------------------------------------------------------

def test_gp25_protocol_options_non_empty():
    assert len(GP25_SPEC.protocol_options) > 0


def test_gp25_protocol_options_contains_motoplus():
    """MotoPlus is the Yaskawa-native interface and must be listed."""
    assert "MotoPlus" in GP25_SPEC.protocol_options


# ---------------------------------------------------------------------------
# Asset path tests
# ---------------------------------------------------------------------------

def test_gp25_nucleus_asset_path_contains_gp25():
    assert "gp25" in GP25_SPEC.nucleus_asset_path.lower() or \
           "GP25" in GP25_SPEC.nucleus_asset_path


def test_gp25_urdf_path_contains_gp25():
    assert "gp25" in GP25_SPEC.urdf_path.lower() or \
           "GP25" in GP25_SPEC.urdf_path


# ---------------------------------------------------------------------------
# ONBOARDING_CHECKLIST tests
# ---------------------------------------------------------------------------

def test_onboarding_checklist_has_at_least_8_entries():
    assert len(ONBOARDING_CHECKLIST) >= 8


def test_onboarding_checklist_unique_step_ids():
    """Every step must have a unique step_id."""
    ids = [step.step_id for step in ONBOARDING_CHECKLIST]
    assert len(ids) == len(set(ids)), f"Duplicate step_ids found: {ids}"


def test_onboarding_checklist_step_ids_1_indexed():
    """Step IDs must be 1-indexed (minimum step_id == 1)."""
    ids = [step.step_id for step in ONBOARDING_CHECKLIST]
    assert min(ids) == 1


def test_onboarding_checklist_has_runtime_needed_step():
    """At least one step requires runtime (otherwise scope is wrong)."""
    runtime_steps = [s for s in ONBOARDING_CHECKLIST if s.runtime_needed]
    assert len(runtime_steps) > 0, "No runtime-needed steps found — spec scope error"


def test_onboarding_checklist_has_non_runtime_step():
    """At least one step must be verifiable without runtime (spec data layer)."""
    static_steps = [s for s in ONBOARDING_CHECKLIST if not s.runtime_needed]
    assert len(static_steps) > 0, "All steps require runtime — spec/data layer missing"


def test_onboarding_checklist_all_steps_have_valid_category():
    valid_categories = {"precondition", "asset", "validation", "configuration", "registration"}
    for step in ONBOARDING_CHECKLIST:
        assert step.category in valid_categories, \
            f"Step {step.step_id} has invalid category: {step.category!r}"


# ---------------------------------------------------------------------------
# steps_without_runtime() tests
# ---------------------------------------------------------------------------

def test_steps_without_runtime_returns_non_runtime_subset():
    no_rt = steps_without_runtime()
    assert all(not step.runtime_needed for step in no_rt)


def test_steps_without_runtime_subset_of_checklist():
    no_rt = steps_without_runtime()
    checklist_ids = {step.step_id for step in ONBOARDING_CHECKLIST}
    for step in no_rt:
        assert step.step_id in checklist_ids


def test_steps_without_runtime_non_empty():
    no_rt = steps_without_runtime()
    assert len(no_rt) > 0


# ---------------------------------------------------------------------------
# gp25_to_robot_wizard_entry() tests
# ---------------------------------------------------------------------------

def test_robot_wizard_entry_has_required_keys():
    """Entry must include the minimum keys expected by _ROBOT_WIZARD_REGISTRY."""
    entry = gp25_to_robot_wizard_entry()
    required_keys = {"name", "manufacturer", "model", "urdf_path", "nucleus_asset_path"}
    missing = required_keys - entry.keys()
    assert not missing, f"Missing required keys: {missing}"


def test_robot_wizard_entry_name_matches_spec():
    entry = gp25_to_robot_wizard_entry()
    assert entry["name"] == GP25_SPEC.name


def test_robot_wizard_entry_robot_type_manipulator():
    entry = gp25_to_robot_wizard_entry()
    assert entry.get("robot_type") == "manipulator"


def test_robot_wizard_entry_payload_matches_spec():
    entry = gp25_to_robot_wizard_entry()
    assert entry["payload_kg"] == GP25_SPEC.payload_kg


def test_robot_wizard_entry_has_rel_path_and_cloud_url():
    """Registry entries should have rel_path and cloud_url for asset resolution."""
    entry = gp25_to_robot_wizard_entry()
    assert "rel_path" in entry
    assert "cloud_url" in entry
    assert entry["rel_path"]
    assert entry["cloud_url"].startswith("https://")
