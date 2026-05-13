"""Phase 10 — dispatch-time arg validation via Pydantic.

Validates that execute_tool_call calls MODEL_REGISTRY[tool_name].model_validate
on the args BEFORE dispatch and returns `validation_blocked=True` on
failure.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 10.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


@pytest.mark.asyncio
async def test_invalid_args_blocked_before_dispatch():
    """create_prim requires prim_path + prim_type. Calling without them
    returns validation_blocked=True, not a NameError from the handler.
    """
    from service.isaac_assist_service.chat.tools import tool_executor as te
    result = await te.execute_tool_call("create_prim", {})
    assert result.get("type") == "error"
    assert result.get("validation_blocked") is True
    assert "validation failed" in result.get("error", "")


@pytest.mark.asyncio
async def test_unknown_tool_falls_through_to_dispatch():
    """A tool name not in MODEL_REGISTRY isn't validated; dispatch's
    own error path handles it.
    """
    from service.isaac_assist_service.chat.tools import tool_executor as te
    result = await te.execute_tool_call("definitely_not_a_real_tool_xyz", {})
    assert result.get("type") == "error"
    # NOT validation_blocked — dispatch's "unknown tool" path
    assert "validation_blocked" not in result or result.get("validation_blocked") is False


def test_validate_args_helper_returns_none_on_valid():
    """The internal helper returns None when validation passes."""
    from service.isaac_assist_service.chat.tools.tool_executor import _validate_args_pydantic
    result = _validate_args_pydantic("create_prim", {
        "prim_path": "/World/A", "prim_type": "Cube",
    })
    assert result is None


def test_validate_args_helper_returns_message_on_invalid():
    """The internal helper returns a non-empty string on validation failure."""
    from service.isaac_assist_service.chat.tools.tool_executor import _validate_args_pydantic
    result = _validate_args_pydantic("create_prim", {"prim_path": "/W"})
    assert result is not None
    assert "validation failed" in result


def test_validate_args_helper_returns_none_for_unknown_tool():
    """Unknown tool name → None (no model to validate against)."""
    from service.isaac_assist_service.chat.tools.tool_executor import _validate_args_pydantic
    assert _validate_args_pydantic("definitely_not_a_tool", {}) is None
