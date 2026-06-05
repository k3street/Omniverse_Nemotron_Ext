"""Phase 56c contract tests — ctrl:profile attribute + controller profile presets.

Covers:
- Phase metadata is correct and status == "landed"
- CONTROLLER_PROFILES has >= 4 named presets
- ControllerAttrSet has profile field (integration with Phase 11c)
- get_profile for known and unknown names
- apply_to_attrset sets the profile field correctly
- list_profiles is complete and sorted
- with_profile / apply_profile round-trips on ControllerAttrSet
- Hint keys are silently ignored by apply_profile (no Pydantic error)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.sub_phase_56c_ctrl_profile_attr import (
    CONTROLLER_PROFILES,
    apply_to_attrset,
    get_phase_metadata,
    get_profile,
    list_profiles,
)
from service.isaac_assist_service.types.ctrl_namespace import ControllerAttrSet


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

class TestPhaseMetadata:
    def test_metadata_phase_id(self):
        md = get_phase_metadata()
        assert md["phase"] == "56c"

    def test_metadata_status_landed(self):
        md = get_phase_metadata()
        assert md["status"] == "landed", (
            f"Expected status='landed', got {md['status']!r}. "
            "Update PHASE_STATUS in sub_phase_56c_ctrl_profile_attr.py."
        )

    def test_metadata_has_spec_ref(self):
        md = get_phase_metadata()
        assert "spec_ref" in md
        assert "56c" in md["spec_ref"]

    def test_metadata_has_title(self):
        md = get_phase_metadata()
        assert "title" in md
        assert md["title"]


# ---------------------------------------------------------------------------
# 2. CONTROLLER_PROFILES registry
# ---------------------------------------------------------------------------

class TestControllerProfilesRegistry:
    def test_at_least_four_profiles(self):
        assert len(CONTROLLER_PROFILES) >= 4, (
            f"Expected >= 4 profiles, got {len(CONTROLLER_PROFILES)}: "
            f"{sorted(CONTROLLER_PROFILES)}"
        )

    def test_expected_canonical_profiles_present(self):
        expected = {"default", "high_precision", "production_factory", "development", "safety_critical"}
        missing = expected - set(CONTROLLER_PROFILES)
        assert not missing, f"Missing canonical profiles: {missing}"

    def test_each_profile_is_dict(self):
        for name, preset in CONTROLLER_PROFILES.items():
            assert isinstance(preset, dict), f"Profile {name!r} is not a dict"

    def test_each_profile_has_profile_key_matching_name(self):
        for name, preset in CONTROLLER_PROFILES.items():
            assert preset.get("profile") == name, (
                f"Profile {name!r}: preset['profile']={preset.get('profile')!r} "
                f"should equal {name!r}"
            )


# ---------------------------------------------------------------------------
# 3. ControllerAttrSet has profile field (Phase 11c integration)
# ---------------------------------------------------------------------------

class TestControllerAttrSetProfileField:
    def test_profile_field_defaults_to_none(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning")
        assert s.profile is None

    def test_profile_field_accepts_string(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning", profile="default")
        assert s.profile == "default"

    def test_profile_field_round_trips_to_usd_attrs(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning", profile="high_precision")
        attrs = s.to_usd_attrs()
        assert attrs["ctrl:profile"] == "high_precision"

    def test_profile_absent_from_usd_attrs_when_none(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning")
        attrs = s.to_usd_attrs()
        assert "ctrl:profile" not in attrs

    def test_with_profile_returns_new_instance_with_profile_set(self):
        base = ControllerAttrSet(adapter="curobo", phase="planning")
        profiled = base.with_profile("production_factory")
        assert profiled.profile == "production_factory"
        # Original unchanged
        assert base.profile is None

    def test_with_profile_preserves_other_fields(self):
        base = ControllerAttrSet(
            adapter="builtin_pp", phase="approach", tick=99, status="stalled"
        )
        profiled = base.with_profile("development")
        assert profiled.adapter == "builtin_pp"
        assert profiled.phase == "approach"
        assert profiled.tick == 99
        assert profiled.status == "stalled"
        assert profiled.profile == "development"

    def test_apply_profile_sets_profile_field(self):
        base = ControllerAttrSet(adapter="spline", phase="track")
        updated = base.apply_profile({"profile": "safety_critical", "status": "ok"})
        assert updated.profile == "safety_critical"
        assert updated.status == "ok"

    def test_apply_profile_ignores_hint_keys(self):
        """hint:* keys must not cause Pydantic errors — they're filtered out."""
        base = ControllerAttrSet(adapter="curobo", phase="planning")
        preset = {
            "profile": "high_precision",
            "status": "ok",
            "hint:tolerance_scale": 0.25,
            "hint:speed_scale": 0.40,
            "hint:replanning_enabled": True,
        }
        # Should not raise
        updated = base.apply_profile(preset)
        assert updated.profile == "high_precision"

    def test_apply_profile_ignores_unknown_keys(self):
        base = ControllerAttrSet(adapter="curobo", phase="planning")
        updated = base.apply_profile({"profile": "default", "nonexistent_key": 42})
        assert updated.profile == "default"


# ---------------------------------------------------------------------------
# 4. get_profile
# ---------------------------------------------------------------------------

class TestGetProfile:
    def test_get_known_profile_returns_dict(self):
        for name in CONTROLLER_PROFILES:
            preset = get_profile(name)
            assert isinstance(preset, dict)
            assert preset["profile"] == name

    def test_get_profile_returns_copy(self):
        """Mutating the returned dict must not affect the registry."""
        preset = get_profile("default")
        preset["injected"] = "poison"
        assert "injected" not in CONTROLLER_PROFILES["default"]

    def test_get_unknown_profile_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown controller profile"):
            get_profile("nonexistent_profile_xyz")

    def test_get_unknown_includes_known_names_in_message(self):
        with pytest.raises(KeyError) as exc_info:
            get_profile("bad_name")
        msg = str(exc_info.value)
        assert "default" in msg


# ---------------------------------------------------------------------------
# 5. apply_to_attrset
# ---------------------------------------------------------------------------

class TestApplyToAttrset:
    def _base(self, profile: str | None = None) -> ControllerAttrSet:
        return ControllerAttrSet(adapter="curobo", phase="planning", profile=profile)

    def test_sets_profile_field(self):
        result = apply_to_attrset(self._base(), "default")
        assert result.profile == "default"

    def test_sets_high_precision(self):
        result = apply_to_attrset(self._base(), "high_precision")
        assert result.profile == "high_precision"

    def test_sets_production_factory(self):
        result = apply_to_attrset(self._base(), "production_factory")
        assert result.profile == "production_factory"

    def test_sets_development(self):
        result = apply_to_attrset(self._base(), "development")
        assert result.profile == "development"

    def test_sets_safety_critical(self):
        result = apply_to_attrset(self._base(), "safety_critical")
        assert result.profile == "safety_critical"

    def test_returns_new_frozen_instance(self):
        base = self._base()
        result = apply_to_attrset(base, "default")
        assert result is not base
        # Still frozen
        with pytest.raises(ValidationError):
            result.adapter = "new"  # type: ignore[misc]

    def test_preserves_adapter_and_phase(self):
        base = ControllerAttrSet(adapter="builtin_pp", phase="grasp", tick=7)
        result = apply_to_attrset(base, "high_precision")
        assert result.adapter == "builtin_pp"
        assert result.phase == "grasp"
        assert result.tick == 7

    def test_unknown_profile_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown controller profile"):
            apply_to_attrset(self._base(), "does_not_exist")

    def test_result_serialises_to_usd_attrs_with_profile(self):
        result = apply_to_attrset(self._base(), "safety_critical")
        attrs = result.to_usd_attrs()
        assert attrs["ctrl:profile"] == "safety_critical"


# ---------------------------------------------------------------------------
# 6. list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_returns_list(self):
        assert isinstance(list_profiles(), list)

    def test_length_matches_registry(self):
        assert len(list_profiles()) == len(CONTROLLER_PROFILES)

    def test_contains_all_canonical_names(self):
        names = set(list_profiles())
        for expected in ("default", "high_precision", "production_factory", "development", "safety_critical"):
            assert expected in names, f"Missing canonical profile {expected!r} from list_profiles()"

    def test_is_sorted(self):
        profiles = list_profiles()
        assert profiles == sorted(profiles), "list_profiles() should return sorted names"
