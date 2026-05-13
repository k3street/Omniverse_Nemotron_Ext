"""Phase 60b contract tests — SDG preset edge cases.

Gate: pytest tests/test_sub_phase_60b_sdg_preset_edge_cases.py
      edge-case preset list has ≥3 entries.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mod():
    from service.isaac_assist_service.multimodal import sub_phase_60b_sdg_preset_edge_cases as m
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_60b_metadata():
    md = _mod().get_phase_metadata()
    assert md["phase"] == "60b"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


def test_at_least_five_presets():
    """Gate requirement: ≥5 edge-case presets defined."""
    presets = _mod().list_presets()
    assert len(presets) >= 5, f"Expected ≥5 presets, got {len(presets)}: {presets}"


def test_each_preset_has_required_keys():
    """Every preset must carry name, ranges, and num_samples."""
    mod = _mod()
    for preset_name in mod.list_presets():
        p = mod.get_preset(preset_name)
        assert "name" in p, f"{preset_name}: missing 'name'"
        assert "ranges" in p, f"{preset_name}: missing 'ranges'"
        assert "num_samples" in p, f"{preset_name}: missing 'num_samples'"
        assert isinstance(p["ranges"], dict), f"{preset_name}: ranges must be a dict"
        assert p["num_samples"] > 0, f"{preset_name}: num_samples must be positive"


def test_get_preset_known():
    """get_preset for a known name returns a non-empty dict."""
    mod = _mod()
    known = mod.list_presets()[0]
    result = mod.get_preset(known)
    assert result, f"Expected non-empty dict for known preset '{known}'"
    assert result["name"] == known


def test_get_preset_unknown():
    """get_preset for an unknown name returns empty dict (not None, not raises)."""
    result = _mod().get_preset("__does_not_exist__")
    assert result == {}, f"Expected empty dict, got {result!r}"


def test_list_presets_sorted():
    """list_presets must return names in sorted order."""
    names = _mod().list_presets()
    assert names == sorted(names), "list_presets() must return sorted names"


def test_severity_of_preset_classifies_correctly():
    """severity_of_preset maps each canonical preset to expected severity tag."""
    mod = _mod()
    expected = {
        "extreme_lighting": "extreme",
        "high_occlusion": "extreme",
        "noisy_sensors": "noisy",
        "physics_extreme": "physics_outlier",
        "actuator_failure": "failure",
    }
    for name, want in expected.items():
        got = mod.severity_of_preset(name)
        assert got == want, f"severity_of_preset({name!r}) = {got!r}, want {want!r}"


def test_severity_valid_values():
    """Every preset severity must be one of the four allowed literals."""
    mod = _mod()
    allowed = {"extreme", "noisy", "failure", "physics_outlier"}
    for name in mod.list_presets():
        sev = mod.severity_of_preset(name)
        assert sev in allowed, f"{name}: severity {sev!r} not in {allowed}"


def test_ranges_have_valid_numeric_bounds():
    """For numeric range pairs [min, max], min must be ≤ max."""
    mod = _mod()
    for preset_name in mod.list_presets():
        ranges = mod.get_preset(preset_name)["ranges"]
        for key, val in ranges.items():
            if (
                isinstance(val, (list, tuple))
                and len(val) == 2
                and isinstance(val[0], (int, float))
                and isinstance(val[1], (int, float))
            ):
                lo, hi = val[0], val[1]
                assert lo <= hi, (
                    f"{preset_name}.ranges[{key!r}]: min ({lo}) > max ({hi})"
                )


def test_edge_case_presets_dict_exported():
    """EDGE_CASE_PRESETS dict is importable and non-empty."""
    mod = _mod()
    assert hasattr(mod, "EDGE_CASE_PRESETS")
    assert isinstance(mod.EDGE_CASE_PRESETS, dict)
    assert len(mod.EDGE_CASE_PRESETS) >= 5
