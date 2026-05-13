"""Phase 81 contract tests — multi-rate physics config.

Gate: pytest tests/test_phase_81_multi_rate_physics.py --tb=short
All 7+ tests must pass.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports (done inside each test so import errors surface clearly)
# ---------------------------------------------------------------------------

def _import():
    from service.isaac_assist_service.multimodal import multi_rate_physics as mrp
    return mrp


# ---------------------------------------------------------------------------
# 1. Metadata — status must be "landed" after implementation
# ---------------------------------------------------------------------------

def test_phase_81_metadata():
    """Phase metadata returns correct id and landed status."""
    mrp = _import()
    md = mrp.get_phase_metadata()
    assert md["phase"] == 81
    assert md["status"] == "landed", (
        f"Expected status='landed', got '{md['status']}'"
    )
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. Default config — rates match spec
# ---------------------------------------------------------------------------

def test_default_config_rates():
    """MultiRateConfig() defaults match the spec values."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    assert cfg.physics_hz == 240.0
    assert cfg.vision_hz == 30.0
    assert cfg.planning_hz == 10.0
    assert cfg.control_hz == 120.0
    assert cfg.logging_hz == 1.0


# ---------------------------------------------------------------------------
# 3. ratio(vision, physics) == 30 / 240
# ---------------------------------------------------------------------------

def test_ratio_vision_to_physics():
    """ratio('vision', 'physics') returns vision_hz / physics_hz."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    result = cfg.ratio("vision", "physics")
    expected = 30.0 / 240.0
    assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"


# ---------------------------------------------------------------------------
# 4. subdivisions_per_physics_tick(vision) == 8.0
# ---------------------------------------------------------------------------

def test_subdivisions_vision():
    """physics_hz / vision_hz = 240 / 30 = 8 physics ticks per vision frame."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    result = cfg.subdivisions_per_physics_tick("vision")
    assert abs(result - 8.0) < 1e-9, f"Expected 8.0, got {result}"


# ---------------------------------------------------------------------------
# 5. validate() warns when control_hz > physics_hz
# ---------------------------------------------------------------------------

def test_validate_warns_control_faster_than_physics():
    """validate() returns a warning when control_hz > physics_hz."""
    mrp = _import()
    # control_hz=300 > physics_hz=240 → suspicious
    cfg = mrp.MultiRateConfig(physics_hz=240.0, control_hz=300.0)

    warnings = cfg.validate()
    assert len(warnings) >= 1
    # At least one warning mentions control
    assert any("control_hz" in w for w in warnings), (
        f"Expected 'control_hz' warning, got: {warnings}"
    )


def test_validate_clean_config_no_warnings():
    """Default config produces no warnings."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()  # control=120 < physics=240 — all fine

    warnings = cfg.validate()
    assert warnings == [], f"Unexpected warnings: {warnings}"


# ---------------------------------------------------------------------------
# 6. extras channels appear in as_channels()
# ---------------------------------------------------------------------------

def test_extras_show_up_in_as_channels():
    """User-defined extras are included in as_channels() output."""
    mrp = _import()
    extra = mrp.RateChannel(name="lidar", hz=20.0, priority=2, description="LiDAR sweep")
    cfg = mrp.MultiRateConfig(extras={"lidar": extra})

    channels = cfg.as_channels()
    names = [ch.name for ch in channels]

    # All 5 builtins present
    for builtin in ("physics", "vision", "planning", "control", "logging"):
        assert builtin in names, f"Missing builtin channel '{builtin}'"

    # Extra also present
    assert "lidar" in names, f"Extra channel 'lidar' missing from as_channels()"
    lidar_ch = next(ch for ch in channels if ch.name == "lidar")
    assert lidar_ch.hz == 20.0
    assert lidar_ch.description == "LiDAR sweep"


# ---------------------------------------------------------------------------
# 7. Custom RateChannel with priority field
# ---------------------------------------------------------------------------

def test_rate_channel_priority_field():
    """RateChannel stores name, hz, priority, and description correctly."""
    mrp = _import()
    ch = mrp.RateChannel(name="tactile", hz=500.0, priority=5, description="High-freq touch")

    assert ch.name == "tactile"
    assert ch.hz == 500.0
    assert ch.priority == 5
    assert ch.description == "High-freq touch"


def test_rate_channel_invalid_hz_raises():
    """RateChannel raises ValueError for non-positive hz."""
    mrp = _import()
    with pytest.raises(ValueError, match="hz must be > 0"):
        mrp.RateChannel(name="bad", hz=0.0)


# ---------------------------------------------------------------------------
# 8. DEFAULT_CONFIG is a MultiRateConfig instance with correct defaults
# ---------------------------------------------------------------------------

def test_default_config_singleton():
    """Module-level DEFAULT_CONFIG is a MultiRateConfig with spec defaults."""
    mrp = _import()
    cfg = mrp.DEFAULT_CONFIG

    assert isinstance(cfg, mrp.MultiRateConfig)
    assert cfg.physics_hz == 240.0
    assert cfg.logging_hz == 1.0


# ---------------------------------------------------------------------------
# 9. ratio() for planning vs vision
# ---------------------------------------------------------------------------

def test_ratio_planning_to_vision():
    """ratio('planning', 'vision') = 10 / 30 ≈ 0.333..."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    result = cfg.ratio("planning", "vision")
    expected = 10.0 / 30.0
    assert abs(result - expected) < 1e-9


# ---------------------------------------------------------------------------
# 10. subdivisions for planning == 24
# ---------------------------------------------------------------------------

def test_subdivisions_planning():
    """physics_hz / planning_hz = 240 / 10 = 24 ticks per planning cycle."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    result = cfg.subdivisions_per_physics_tick("planning")
    assert abs(result - 24.0) < 1e-9


# ---------------------------------------------------------------------------
# 11. ratio() raises KeyError for unknown channel
# ---------------------------------------------------------------------------

def test_ratio_unknown_channel_raises():
    """ratio() raises KeyError when a channel name is not found."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    with pytest.raises(KeyError):
        cfg.ratio("nonexistent", "physics")


# ---------------------------------------------------------------------------
# 12. as_channels() returns exactly 5 builtins when no extras
# ---------------------------------------------------------------------------

def test_as_channels_builtin_count():
    """as_channels() returns exactly 5 channels when extras is empty."""
    mrp = _import()
    cfg = mrp.MultiRateConfig()

    channels = cfg.as_channels()
    assert len(channels) == 5


# ---------------------------------------------------------------------------
# 13. extras lookup via subdivisions_per_physics_tick
# ---------------------------------------------------------------------------

def test_subdivisions_extra_channel():
    """subdivisions_per_physics_tick works for a user-defined extra channel."""
    mrp = _import()
    extra = mrp.RateChannel(name="force_torque", hz=60.0)
    cfg = mrp.MultiRateConfig(extras={"force_torque": extra})

    # 240 / 60 = 4 physics ticks per force-torque sample
    result = cfg.subdivisions_per_physics_tick("force_torque")
    assert abs(result - 4.0) < 1e-9
