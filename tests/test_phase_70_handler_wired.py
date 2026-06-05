"""Phase 70 handler wiring tests — assemble_robot.

Verifies that _gen_assemble_robot in handlers/robot.py now calls the
multimodal assemble_robot module instead of raising NotImplementedError.

Gate:
  - Handler returns a non-empty code string (not a NotImplementedError raise)
  - Returned code contains the robot prim path
  - Handler works with explicit args and with demo fallback (empty args)
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _get_handler():
    from service.isaac_assist_service.chat.tools.handlers.robot import (
        _gen_assemble_robot,
    )
    return _gen_assemble_robot


def test_assemble_robot_handler_returns_success_code_with_args():
    """Handler with explicit base/attachment args returns non-error code."""
    handler = _get_handler()
    code = handler({
        "base_path": "/World/Franka",
        "attachment_path": "/World/Robotiq2F85",
        "base_mount": "panda_hand",
        "attach_mount": "tool_base",
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "raise NotImplementedError" not in code
    # Must reference the robot prim path (default /World/Robot)
    assert "/World/Robot" in code


def test_assemble_robot_handler_returns_prim_paths():
    """Handler embeds prim paths from the assemble() result."""
    handler = _get_handler()
    code = handler({
        "base_path": "/World/Franka",
        "attachment_path": "/World/Gripper",
        "base_mount": "flange",
        "attach_mount": "base",
    })
    assert isinstance(code, str)
    # The generated code must list prim paths
    assert "prim_path" in code or "/World/Robot/" in code


def test_assemble_robot_handler_demo_fallback_when_no_args():
    """Handler with empty args falls back to make_demo_three_link_arm() and still succeeds."""
    handler = _get_handler()
    code = handler({
        "base_path": "",
        "attachment_path": "",
        "base_mount": "flange",
        "attach_mount": "base",
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "raise NotImplementedError" not in code
    assert "assemble" in code.lower() or "/World/Robot" in code
