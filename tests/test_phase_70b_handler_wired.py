"""Phase 70b handler wiring tests — create_behavior.

Verifies that _gen_create_behavior in handlers/robot.py now calls the
multimodal CreateBehaviorCodeGenerator instead of raising NotImplementedError.

Gate:
  - Handler returns a non-empty code string
  - Returned code contains isaacsim.cortex.framework (5.x namespace)
  - Handler works for pick_place and navigate_to patterns
  - Legacy alias "pick_and_place" is normalised to "pick_place" pattern
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _get_handler():
    from service.isaac_assist_service.chat.tools.handlers.robot import (
        _gen_create_behavior,
    )
    return _gen_create_behavior


def test_create_behavior_handler_pick_place_returns_cortex_code():
    """Handler for pick_place pattern emits valid 5.x Cortex code."""
    handler = _get_handler()
    code = handler({
        "articulation_path": "/World/Franka",
        "behavior_type": "pick_place",
        "params": {
            "pick_pose": [0.5, 0.0, 0.3],
            "place_pose": [0.5, 0.3, 0.3],
        },
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "raise NotImplementedError" not in code
    assert "isaacsim.cortex.framework" in code


def test_create_behavior_handler_navigate_to_returns_cortex_code():
    """Handler for navigate_to pattern emits valid 5.x Cortex code."""
    handler = _get_handler()
    code = handler({
        "articulation_path": "/World/Carter",
        "behavior_type": "navigate_to",
        "params": {"target_xy": [3.0, 1.0]},
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "raise NotImplementedError" not in code
    assert "isaacsim.cortex.framework" in code


def test_create_behavior_handler_pick_and_place_alias_normalized():
    """Legacy alias 'pick_and_place' maps to pick_place pattern without error."""
    handler = _get_handler()
    code = handler({
        "articulation_path": "/World/UR10",
        "behavior_type": "pick_and_place",
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "isaacsim.cortex.framework" in code


def test_create_behavior_handler_unknown_pattern_falls_back_to_pick_place():
    """Unknown behavior_type falls back to pick_place without raising."""
    handler = _get_handler()
    code = handler({
        "articulation_path": "/World/Robot",
        "behavior_type": "totally_unknown_behavior",
    })
    assert isinstance(code, str)
    assert "NotImplementedError" not in code
    assert "isaacsim.cortex.framework" in code
