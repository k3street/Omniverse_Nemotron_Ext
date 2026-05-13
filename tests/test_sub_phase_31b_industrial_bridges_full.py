"""Tests for Phase 31b — Industrial bridges full surface.

Covers:
  - phase metadata
  - BridgeConfig / RegisterMapEntry dataclasses
  - LEGAL_TRANSITIONS completeness
  - connect dry-run state path
  - disconnect from ready → closed
  - write_register / read_register round-trip
  - write_register read_only raises ValueError
  - read_register unknown name raises KeyError
  - batch_write + batch_read
  - health_check dict keys
  - BRIDGE_PRESETS count
  - illegal transition raises ValueError
  - dry_run=False connect raises NotImplementedError
  - expected_state_transitions returns ≥4 states
  - RegisterMapEntry negative address raises ValueError
"""
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_bridge(registers=None, dry_run=True):
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        BridgeConfig,
        IndustrialBridge,
        RegisterMapEntry,
    )

    register_map = registers or [
        RegisterMapEntry(name="speed", address=0, data_type="float32"),
        RegisterMapEntry(name="estop", address=1, data_type="bool", read_only=True),
        RegisterMapEntry(name="count", address=2, data_type="int32"),
    ]
    cfg = BridgeConfig(
        protocol="modbus_tcp",
        host="127.0.0.1",
        port=502,
        register_map=register_map,
    )
    return IndustrialBridge(cfg, dry_run=dry_run)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------
def test_phase_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == "31b"
    assert md["status"] == "landed"
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. BridgeConfig dataclass
# ---------------------------------------------------------------------------
def test_bridge_config_defaults():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        BridgeConfig,
    )

    cfg = BridgeConfig(protocol="opc_ua", host="10.0.0.1", port=4840)
    assert cfg.unit_id == 1
    assert cfg.poll_interval_ms == 100
    assert cfg.register_map == []


def test_bridge_config_custom():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        BridgeConfig,
        RegisterMapEntry,
    )

    reg = RegisterMapEntry(name="x", address=5, data_type="int16")
    cfg = BridgeConfig(
        protocol="mqtt_sparkplug",
        host="broker",
        port=1883,
        unit_id=3,
        poll_interval_ms=250,
        register_map=[reg],
    )
    assert len(cfg.register_map) == 1
    assert cfg.register_map[0].name == "x"


# ---------------------------------------------------------------------------
# 3. RegisterMapEntry — negative address raises
# ---------------------------------------------------------------------------
def test_register_map_entry_negative_address():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        RegisterMapEntry,
    )

    with pytest.raises(ValueError, match="address must be >= 0"):
        RegisterMapEntry(name="bad", address=-1, data_type="bool")


# ---------------------------------------------------------------------------
# 4. LEGAL_TRANSITIONS completeness
# ---------------------------------------------------------------------------
def test_legal_transitions_key_triples():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        IndustrialBridge,
    )

    lt = IndustrialBridge.LEGAL_TRANSITIONS
    assert "connecting" in lt["disconnected"]
    assert "handshaking" in lt["connecting"]
    assert "ready" in lt["handshaking"]


# ---------------------------------------------------------------------------
# 5. connect dry-run — full state path
# ---------------------------------------------------------------------------
def test_connect_dry_run_full_path():
    bridge = _make_bridge()
    assert bridge.state == "disconnected"
    bridge.connect()
    assert bridge.state == "ready"


# ---------------------------------------------------------------------------
# 6. disconnect from ready → closed
# ---------------------------------------------------------------------------
def test_disconnect_from_ready():
    bridge = _make_bridge()
    bridge.connect()
    assert bridge.state == "ready"
    bridge.disconnect()
    assert bridge.state == "closed"


# ---------------------------------------------------------------------------
# 7. write_register stores value
# ---------------------------------------------------------------------------
def test_write_register_stores_value():
    bridge = _make_bridge()
    bridge.connect()
    bridge.write_register("speed", 3.14)
    assert bridge.read_register("speed") == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# 8. write_register read_only raises ValueError
# ---------------------------------------------------------------------------
def test_write_register_read_only_raises():
    bridge = _make_bridge()
    bridge.connect()
    with pytest.raises(ValueError, match="read-only"):
        bridge.write_register("estop", True)


# ---------------------------------------------------------------------------
# 9. read_register returns written value
# ---------------------------------------------------------------------------
def test_read_register_returns_written():
    bridge = _make_bridge()
    bridge.write_register("count", 42)
    assert bridge.read_register("count") == 42


# ---------------------------------------------------------------------------
# 10. read_register unknown name raises KeyError
# ---------------------------------------------------------------------------
def test_read_register_unknown_name_raises():
    bridge = _make_bridge()
    with pytest.raises(KeyError):
        bridge.read_register("does_not_exist")


# ---------------------------------------------------------------------------
# 11. batch_write + batch_read round-trip
# ---------------------------------------------------------------------------
def test_batch_write_and_read():
    bridge = _make_bridge()
    bridge.connect()
    bridge.batch_write({"speed": 1.5, "count": 99})
    result = bridge.batch_read(["speed", "count"])
    assert result["speed"] == pytest.approx(1.5)
    assert result["count"] == 99


# ---------------------------------------------------------------------------
# 12. health_check returns dict with all required keys
# ---------------------------------------------------------------------------
def test_health_check_keys():
    bridge = _make_bridge()
    bridge.connect()
    hc = bridge.health_check()
    for key in ("state", "protocol", "register_count", "last_poll_ts", "error_count"):
        assert key in hc, f"Missing key: {key}"
    assert hc["state"] == "ready"
    assert hc["protocol"] == "modbus_tcp"
    assert hc["register_count"] == 3


# ---------------------------------------------------------------------------
# 13. BRIDGE_PRESETS has exactly 4 entries
# ---------------------------------------------------------------------------
def test_bridge_presets_count():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        BRIDGE_PRESETS,
    )

    assert len(BRIDGE_PRESETS) == 4
    expected_keys = {
        "modbus_factory_floor",
        "mqtt_sparkplug_default",
        "opc_ua_machine_a",
        "modbus_rtu_legacy",
    }
    assert set(BRIDGE_PRESETS.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 14. BRIDGE_PRESETS — each preset has a non-empty register_map
# ---------------------------------------------------------------------------
def test_bridge_presets_have_registers():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        BRIDGE_PRESETS,
    )

    for name, cfg in BRIDGE_PRESETS.items():
        assert len(cfg.register_map) > 0, f"Preset {name!r} has empty register_map"


# ---------------------------------------------------------------------------
# 15. Illegal transition raises ValueError
# ---------------------------------------------------------------------------
def test_illegal_transition_raises():
    bridge = _make_bridge()
    # 'disconnected' cannot jump directly to 'ready'
    with pytest.raises(ValueError, match="Illegal transition"):
        bridge._transition("ready")


# ---------------------------------------------------------------------------
# 16. dry_run=False connect raises NotImplementedError
# ---------------------------------------------------------------------------
def test_connect_live_raises_not_implemented():
    bridge = _make_bridge(dry_run=False)
    with pytest.raises(NotImplementedError):
        bridge.connect()


# ---------------------------------------------------------------------------
# 17. expected_state_transitions returns ≥4 states
# ---------------------------------------------------------------------------
def test_expected_state_transitions_length():
    from service.isaac_assist_service.multimodal.sub_phase_31b_industrial_bridges_full import (
        expected_state_transitions,
    )

    for protocol in ("modbus_tcp", "modbus_rtu", "opc_ua", "mqtt_sparkplug"):
        states = expected_state_transitions(protocol)
        assert len(states) >= 4, f"Protocol {protocol!r} returned < 4 states"
        assert states[0] == "disconnected"
        assert "ready" in states


# ---------------------------------------------------------------------------
# 18. Batch write rejects read_only registers (all-or-nothing validation)
# ---------------------------------------------------------------------------
def test_batch_write_rejects_read_only():
    bridge = _make_bridge()
    with pytest.raises(ValueError, match="read-only"):
        bridge.batch_write({"speed": 2.0, "estop": True})


# ---------------------------------------------------------------------------
# 19. closed bridge cannot transition further
# ---------------------------------------------------------------------------
def test_closed_state_no_further_transitions():
    bridge = _make_bridge()
    bridge.connect()
    bridge.disconnect()
    assert bridge.state == "closed"
    with pytest.raises(ValueError, match="Illegal transition"):
        bridge._transition("connecting")
