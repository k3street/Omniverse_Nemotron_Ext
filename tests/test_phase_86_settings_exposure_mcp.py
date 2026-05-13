"""Phase 86 — Settings exposure: settings registry tests."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_registry():
    """Return a new, empty SettingsRegistry for test isolation."""
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import (
        SettingsRegistry,
    )
    return SettingsRegistry()


def _make_setting(name="my_key", typ=int, default=42, description="test setting",
                  validator=None):
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import Setting
    return Setting(name=name, type=typ, default=default,
                   description=description, validator=validator)


# ---------------------------------------------------------------------------
# Test 1 — metadata: phase landed
# ---------------------------------------------------------------------------

def test_phase_86_metadata_landed():
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import (
        get_phase_metadata,
    )
    md = get_phase_metadata()
    assert md["phase"] == 86
    assert md["status"] == "landed"


# ---------------------------------------------------------------------------
# Test 2 — register + list_all
# ---------------------------------------------------------------------------

def test_register_and_list_all():
    reg = _fresh_registry()
    s = _make_setting(name="timeout_s", typ=float, default=10.0,
                      description="Timeout in seconds")
    reg.register(s)

    items = reg.list_all()
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "timeout_s"
    assert item["type"] == "float"
    assert item["current_value"] == 10.0
    assert item["default"] == 10.0
    assert "Timeout" in item["description"]


# ---------------------------------------------------------------------------
# Test 3 — get default value
# ---------------------------------------------------------------------------

def test_get_default_value():
    reg = _fresh_registry()
    reg.register(_make_setting(name="cap", typ=int, default=99))
    assert reg.get("cap") == 99


# ---------------------------------------------------------------------------
# Test 4 — set valid value
# ---------------------------------------------------------------------------

def test_set_valid_value():
    reg = _fresh_registry()
    reg.register(_make_setting(name="cap", typ=int, default=10))
    reg.set("cap", 200)
    assert reg.get("cap") == 200


# ---------------------------------------------------------------------------
# Test 5 — set wrong type raises TypeError
# ---------------------------------------------------------------------------

def test_set_wrong_type_raises():
    reg = _fresh_registry()
    reg.register(_make_setting(name="cap", typ=int, default=10))
    with pytest.raises(TypeError):
        reg.set("cap", "not-an-int")


# ---------------------------------------------------------------------------
# Test 6 — reset restores default
# ---------------------------------------------------------------------------

def test_reset_to_default():
    reg = _fresh_registry()
    reg.register(_make_setting(name="cap", typ=int, default=100))
    reg.set("cap", 999)
    assert reg.get("cap") == 999
    reg.reset("cap")
    assert reg.get("cap") == 100


# ---------------------------------------------------------------------------
# Test 7 — unknown key raises KeyError on get
# ---------------------------------------------------------------------------

def test_unknown_key_get_raises():
    reg = _fresh_registry()
    with pytest.raises(KeyError):
        reg.get("does_not_exist")


# ---------------------------------------------------------------------------
# Test 8 — unknown key raises KeyError on set
# ---------------------------------------------------------------------------

def test_unknown_key_set_raises():
    reg = _fresh_registry()
    with pytest.raises(KeyError):
        reg.set("ghost_key", 1)


# ---------------------------------------------------------------------------
# Test 9 — custom range-check validator
# ---------------------------------------------------------------------------

def test_custom_validator_range_check():
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import Setting

    def must_be_positive(v: float) -> None:
        if v <= 0:
            raise ValueError(f"Value must be > 0, got {v}")

    reg = _fresh_registry()
    s = Setting(name="rate", type=float, default=1.0,
                description="A positive rate", validator=must_be_positive)
    reg.register(s)

    # Valid value
    reg.set("rate", 5.0)
    assert reg.get("rate") == 5.0

    # Invalid value
    with pytest.raises(ValueError):
        reg.set("rate", -1.0)


# ---------------------------------------------------------------------------
# Test 10 — module singleton has default knobs
# ---------------------------------------------------------------------------

def test_module_singleton_defaults():
    from service.isaac_assist_service.multimodal.settings_exposure_mcp import (
        get_registry,
    )
    reg = get_registry()
    assert reg.get("result_cap_default_chars") == 50_000
    assert reg.get("telemetry_enabled") is False
    assert reg.get("log_level") == "INFO"
    assert reg.get("dispatch_timeout_s") == 30.0


# ---------------------------------------------------------------------------
# Test 11 — int→float coercion allowed
# ---------------------------------------------------------------------------

def test_int_to_float_coercion():
    reg = _fresh_registry()
    reg.register(_make_setting(name="timeout", typ=float, default=1.0))
    # Passing an int where float is expected should be silently coerced
    reg.set("timeout", 5)
    val = reg.get("timeout")
    assert isinstance(val, float)
    assert val == 5.0
