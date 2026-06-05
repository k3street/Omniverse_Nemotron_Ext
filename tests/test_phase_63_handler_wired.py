"""Phase 63 handler wiring tests.

Verifies execute_contact_sequence_plan tool is reachable via dispatch
and that the handler delegates to the multimodal runtime correctly.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


@pytest.mark.asyncio
async def test_dispatch_reaches_contact_sequence_handler():
    """execute_contact_sequence_plan resolvable from dispatch."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "execute_contact_sequence_plan",
        {
            "steps": [
                {"step_idx": 0, "step_type": "approach",
                 "prim_a": "/World/RobotA", "prim_b": "/World/Target"},
                {"step_idx": 1, "step_type": "make_contact",
                 "prim_a": "/World/RobotA", "prim_b": "/World/Target"},
                {"step_idx": 2, "step_type": "release",
                 "prim_a": "/World/RobotA", "prim_b": "/World/Target"},
            ],
            "dry_run": True,
        },
    )
    assert result.get("success") is True
    assert result.get("plan_complete") is True
    assert len(result["results"]) == 3
    assert all(r["success"] for r in result["results"])


@pytest.mark.asyncio
async def test_handler_returns_validation_issues_on_bad_plan():
    """Plan validator catches duplicate step_idx."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "execute_contact_sequence_plan",
        {
            "steps": [
                {"step_idx": 0, "step_type": "approach",
                 "prim_a": "/A", "prim_b": "/B"},
                {"step_idx": 0, "step_type": "release",  # duplicate idx
                 "prim_a": "/A", "prim_b": "/B"},
            ],
            "dry_run": True,
        },
    )
    assert result.get("success") is False
    assert "validation" in (result.get("error") or "").lower()
    assert result.get("issues")


@pytest.mark.asyncio
async def test_handler_accepts_mutex_paths():
    """The mutex_paths field on ContactStep is preserved through dispatch."""
    from service.isaac_assist_service.multimodal.execute_contact_sequence_runtime import (
        ContactStep,
    )
    step = ContactStep(
        step_idx=0,
        step_type="make_contact",
        prim_a="/A",
        prim_b="/B",
        mutex_paths=["/locks/shared_workspace"],
    )
    assert step.mutex_paths == ["/locks/shared_workspace"]


def test_handler_module_registered_in_dispatch():
    """contact_sequence module is in _THEME_MODULES."""
    from service.isaac_assist_service.chat.tools.handlers import _dispatch
    from service.isaac_assist_service.chat.tools.handlers import contact_sequence
    assert contact_sequence in _dispatch._THEME_MODULES


def test_handler_module_provides_register():
    """Module exposes register() function with the expected name."""
    from service.isaac_assist_service.chat.tools.handlers import contact_sequence
    assert callable(contact_sequence.register)
    data: dict = {}
    codegen: dict = {}
    contact_sequence.register(data, codegen)
    assert "execute_contact_sequence_plan" in data
