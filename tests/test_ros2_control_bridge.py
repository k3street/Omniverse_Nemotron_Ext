"""CRM-A1 contract tests — ros2_control bridge.

Validates the pure-Python state machine of `Ros2ControlBridge` (lifecycle,
registration, health snapshot). The bridge guards every rclpy import so
these tests run cleanly with or without ROS2 installed.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make the ext-folder discoverable without installing it.
_EXT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "exts",
    "isaac_6.0",
    "omni.isaac.assist",
)
if _EXT_PATH not in sys.path:
    sys.path.insert(0, _EXT_PATH)

pytestmark = pytest.mark.l0


@pytest.fixture(autouse=True)
def _reset_bridge_singleton():
    """Drop the module-level singleton between tests so they're independent."""
    from omni.isaac.assist.ros2_control_bridge import reset_bridge_for_testing

    reset_bridge_for_testing()
    yield
    reset_bridge_for_testing()


def test_module_imports_without_rclpy():
    """Bridge module must import even if rclpy is missing."""
    from omni.isaac.assist import ros2_control_bridge as mod

    assert hasattr(mod, "Ros2ControlBridge")
    assert hasattr(mod, "get_bridge")
    assert hasattr(mod, "reset_bridge_for_testing")
    assert callable(mod.get_bridge)


def test_get_bridge_returns_singleton():
    from omni.isaac.assist.ros2_control_bridge import get_bridge

    a = get_bridge()
    b = get_bridge()
    assert a is b


def test_health_check_baseline_state():
    """Fresh bridge reports zero publishers / subscribers and not-started."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    h = bridge.health_check()
    assert h.node_started is False
    assert h.ft_publishers == 0
    assert h.state_subscribers == 0


def test_attach_ft_sensor_registers_without_node():
    """Registration succeeds before start(); just no live ROS handle."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    result = bridge.attach_ft_sensor("/World/R/FT", "/sensor/ft")
    assert result["registered"] is True
    assert result["prim_path"] == "/World/R/FT"
    assert result["topic"] == "/sensor/ft"
    assert result["live"] is False  # node not started
    assert bridge.health_check().ft_publishers == 1


def test_attach_ft_sensor_idempotent_update():
    """Re-attaching same prim updates the topic without duplicating."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    bridge.attach_ft_sensor("/World/R/FT", "/sensor/v1")
    bridge.attach_ft_sensor("/World/R/FT", "/sensor/v2")
    assert bridge.health_check().ft_publishers == 1


def test_attach_ft_sensor_rejects_empty_args():
    """Empty prim_path or topic surfaces a clear error, not a silent ok."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    r1 = bridge.attach_ft_sensor("", "/topic")
    r2 = bridge.attach_ft_sensor("/prim", "")
    assert r1["registered"] is False
    assert "non-empty" in r1["reason"]
    assert r2["registered"] is False


def test_detach_ft_sensor_idempotent():
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    bridge.attach_ft_sensor("/World/R/FT", "/sensor/ft")
    r = bridge.detach_ft_sensor("/World/R/FT")
    assert r["removed"] is True
    r2 = bridge.detach_ft_sensor("/World/R/FT")  # already gone
    assert r2["removed"] is False
    assert bridge.health_check().ft_publishers == 0


def test_subscribe_compliance_state_registers_without_node():
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    received: list = []
    result = bridge.subscribe_compliance_state(
        "admittance_controller",
        "/admittance_controller/state",
        lambda d: received.append(d),
    )
    assert result["subscribed"] is True
    assert result["controller"] == "admittance_controller"
    assert result["topic"] == "/admittance_controller/state"
    assert bridge.health_check().state_subscribers == 1


def test_subscribe_rejects_empty_args():
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    r = bridge.subscribe_compliance_state("", "/topic", lambda d: None)
    assert r["subscribed"] is False


def test_extract_state_fields_admittance_shape():
    """Admittance message extractor picks the documented keys."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    class FakeMsg:
        current_pose = [0.0, 0.0, 0.5]
        current_wrench = [1.0, 0.0, 0.0]
        is_engaged = True

    out = Ros2ControlBridge._extract_state_fields("admittance_controller", FakeMsg())
    assert out["controller"] == "admittance_controller"
    assert out["current_pose"] == [0.0, 0.0, 0.5]
    assert out["is_engaged"] is True


def test_extract_state_fields_unknown_controller_returns_raw():
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    out = Ros2ControlBridge._extract_state_fields("some_other_ctrl", object())
    assert out["controller"] == "some_other_ctrl"
    assert "raw_data" in out


def test_start_without_rclpy_returns_available_false():
    """In test environments rclpy is typically absent; start() must not raise."""
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    r = bridge.start()
    # Either rclpy is missing (available=False) OR start succeeds; both
    # paths must NOT raise and must return a structured dict.
    assert isinstance(r, dict)
    assert "available" in r
    assert "success" in r, "Section 19 honesty gate: success key required"
    if r["available"] is False:
        assert r["started"] is False
        assert r["success"] is False
        assert "reason" in r


def test_stop_before_start_is_safe():
    from omni.isaac.assist.ros2_control_bridge import Ros2ControlBridge

    bridge = Ros2ControlBridge()
    r = bridge.stop()
    assert isinstance(r, dict)
    assert r.get("stopped") is True
    assert r.get("success") is True, "stopped state is a successful no-op"
    assert "not started" in r.get("reason", "")


def test_extension_module_syntactically_valid():
    """extension.py parses cleanly (omni.ext is Kit-only so full import
    won't work outside a running Kit; this test checks Python syntax +
    that the bridge wire-in references resolve at parse time)."""
    import ast

    ext_path = os.path.join(_EXT_PATH, "omni", "isaac", "assist", "extension.py")
    with open(ext_path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=ext_path)
    # Must define IsaacAssistExtension
    class_names = [
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
    ]
    assert "IsaacAssistExtension" in class_names
    # Bridge wire-in must reference get_bridge somewhere
    assert "get_bridge" in source
    assert "ros2_control_bridge" in source
