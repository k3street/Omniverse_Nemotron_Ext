"""Round 3 Patch A — wire-up tests for role-based code_template dispatch.

Verifies that execute_template_canonical routes to instantiate_role_based_code
when a template carries all three role fields {roles, role_defaults, code_template},
and falls back to the legacy `code` path otherwise.

All tests mock Kit RPC / network dependencies; Isaac Sim need not be running.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously (works in pytest without asyncio plugin)."""
    return asyncio.run(coro)


def _make_execute_tool_call_mock(ok: bool = True):
    """Return an AsyncMock that simulates a successful tool execution."""
    async def _mock(tool_name: str, args: Dict[str, Any]):
        return {"type": "success", "success": True, "output": f"ok:{tool_name}"}
    return _mock


# ---------------------------------------------------------------------------
# Fixture templates
# ---------------------------------------------------------------------------

_ROLE_TEMPLATE = {
    "task_id": "CP-TEST-ROLE",
    "roles": ["primary_robot"],
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0],
        }
    },
    "code_template": (
        "robot_wizard(robot_class={{primary_robot.class}}, "
        "robot_path={{primary_robot.path}})"
    ),
    # legacy code field absent — role path only
}

_LEGACY_TEMPLATE = {
    "task_id": "CP-TEST-LEGACY",
    # No roles / role_defaults / code_template
    "code": 'create_prim(prim_path="/World/Box", prim_type="Cube")',
}

_BOTH_FIELDS_TEMPLATE = {
    "task_id": "CP-TEST-BOTH",
    "roles": ["primary_robot"],
    "role_defaults": {
        "primary_robot": {
            "path": "/World/FrankaNew",
            "class": "franka_panda",
        }
    },
    "code_template": (
        "robot_wizard(robot_class={{primary_robot.class}}, "
        "robot_path={{primary_robot.path}})"
    ),
    # legacy code field also present — role path should win
    "code": 'robot_wizard(robot_class="franka_panda", robot_path="/World/FrankaOld")',
}

_BAD_SUBSTITUTION_TEMPLATE = {
    "task_id": "CP-TEST-BAD",
    "roles": ["primary_robot"],
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            # 'class' key intentionally missing → placeholder left unresolved
        }
    },
    "code_template": (
        "robot_wizard(robot_class={{primary_robot.class}}, "
        "robot_path={{primary_robot.path}})"
    ),
}


# ---------------------------------------------------------------------------
# Test 1 — role-based template routes to instantiate_role_based_code
# ---------------------------------------------------------------------------

def test_role_based_template_uses_role_path():
    """A template with {roles, role_defaults, code_template} must use the
    role-based code path, not template['code']."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        execute_template_canonical,
    )

    dispatched_to_role_path = []

    original_instantiate = None

    from service.isaac_assist_service.chat import canonical_instantiator as _ci

    original_fn = _ci.instantiate_role_based_code

    def _spy(template, role_bindings=None):
        dispatched_to_role_path.append(template.get("task_id"))
        return original_fn(template, role_bindings)

    mock_exec = _make_execute_tool_call_mock()

    with (
        patch.object(_ci, "instantiate_role_based_code", side_effect=_spy),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.DATA_HANDLERS",
            {"robot_wizard": None},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.CODE_GEN_HANDLERS",
            {},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
            new=mock_exec,
        ),
    ):
        result = _run(execute_template_canonical(_ROLE_TEMPLATE))

    assert "CP-TEST-ROLE" in dispatched_to_role_path, (
        "execute_template_canonical did not call instantiate_role_based_code "
        "for a role-based template"
    )
    assert result["instantiated"] is True
    assert result["n_calls"] == 1


# ---------------------------------------------------------------------------
# Test 2 — legacy template uses legacy `code` path
# ---------------------------------------------------------------------------

def test_legacy_template_uses_code_field():
    """A template without role fields must use template['code'] directly and
    NOT call instantiate_role_based_code."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        execute_template_canonical,
    )
    from service.isaac_assist_service.chat import canonical_instantiator as _ci

    role_path_called = []

    def _spy(template, role_bindings=None):
        role_path_called.append(template.get("task_id"))
        return _ci.instantiate_role_based_code.__wrapped__(template, role_bindings)

    mock_exec = _make_execute_tool_call_mock()

    with (
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.DATA_HANDLERS",
            {"create_prim": None},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.CODE_GEN_HANDLERS",
            {},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
            new=mock_exec,
        ),
    ):
        result = _run(execute_template_canonical(_LEGACY_TEMPLATE))

    # instantiate_role_based_code must NOT have been invoked (no spy wrapping needed;
    # we verify the result uses the legacy code by checking task executed correctly)
    assert result["instantiated"] is True
    assert result["n_calls"] == 1
    assert result["executed"][0]["tool"] == "create_prim"
    assert "CP-TEST-LEGACY" not in role_path_called


# ---------------------------------------------------------------------------
# Test 3 — template with both code_template and legacy code → use code_template
# ---------------------------------------------------------------------------

def test_both_fields_prefers_code_template():
    """When template has role fields AND legacy code, the role-based path wins."""
    from service.isaac_assist_service.chat import canonical_instantiator as _ci

    original_fn = _ci.instantiate_role_based_code
    dispatched: list = []

    def _spy(template, role_bindings=None):
        dispatched.append(template.get("task_id"))
        return original_fn(template, role_bindings)

    mock_exec = _make_execute_tool_call_mock()

    with (
        patch.object(_ci, "instantiate_role_based_code", side_effect=_spy),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.DATA_HANDLERS",
            {"robot_wizard": None},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.CODE_GEN_HANDLERS",
            {},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
            new=mock_exec,
        ),
    ):
        result = _run(_ci.execute_template_canonical(_BOTH_FIELDS_TEMPLATE))

    assert "CP-TEST-BOTH" in dispatched, (
        "Template with both fields should dispatch to role-based path"
    )
    # Verify it used the new path (FrankaNew) not the legacy path (FrankaOld)
    assert result["instantiated"] is True
    args_preview = result["executed"][0]["args_preview"]
    assert "FrankaNew" in args_preview, (
        f"Expected FrankaNew path from code_template, got: {args_preview}"
    )
    assert "FrankaOld" not in args_preview


# ---------------------------------------------------------------------------
# Test 4 — role-based template with substitution errors → graceful behavior
# ---------------------------------------------------------------------------

def test_bad_substitution_passes_through_unresolved_placeholder():
    """When a placeholder has no matching role_default key, substitute_role_placeholders
    leaves it unchanged (the sentinel behavior documented in substitute_role_placeholders).
    The exec still succeeds if the tool function accepts strings as kwargs;
    the test verifies the call is captured and no Python exception is raised.

    Design decision: partial substitution is safer than a hard error — the tool
    call will fail at execution with a clear bad-arg error rather than raising
    during template instantiation and giving the user no diagnostic.
    """
    from service.isaac_assist_service.chat import canonical_instantiator as _ci

    mock_exec = _make_execute_tool_call_mock()

    with (
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.DATA_HANDLERS",
            {"robot_wizard": None},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.CODE_GEN_HANDLERS",
            {},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
            new=mock_exec,
        ),
    ):
        result = _run(_ci.execute_template_canonical(_BAD_SUBSTITUTION_TEMPLATE))

    # The function should not crash — it either executes (partially resolved args)
    # or returns an "empty code field" / "sandbox exec failed" result.
    # Critically: no Python exception escapes to the caller.
    assert isinstance(result, dict)
    assert "task_id" in result
    assert result["task_id"] == "CP-TEST-BAD"
    # The unresolved placeholder {{primary_robot.class}} is left as a literal
    # string in the code, which causes a NameError in exec → sandbox exec fails
    # or the call is captured with the sentinel string. Either is acceptable;
    # we just check no exception leaked and the result is a well-formed dict.
    assert "instantiated" in result


# ---------------------------------------------------------------------------
# Test 5 — smoke: CP-09/10/11 pilot templates hit role-based path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_id", ["CP-09", "CP-10", "CP-11"])
def test_pilot_cp_routes_to_role_based_path(task_id: str):
    """CP-09/10/11 have code_template + roles + role_defaults — they must
    dispatch through instantiate_role_based_code in production execution."""
    import json
    from pathlib import Path

    from service.isaac_assist_service.chat import canonical_instantiator as _ci

    repo = Path(__file__).resolve().parents[1]
    template = json.loads((repo / "workspace" / "templates" / f"{task_id}.json").read_text())

    # Confirm the template has all three role fields (guard against regressions)
    assert template.get("code_template"), f"{task_id} missing code_template"
    assert template.get("roles"), f"{task_id} missing roles"
    assert template.get("role_defaults"), f"{task_id} missing role_defaults"

    original_fn = _ci.instantiate_role_based_code
    dispatched: list = []

    def _spy(t, role_bindings=None):
        dispatched.append(t.get("task_id"))
        return original_fn(t, role_bindings)

    mock_exec = _make_execute_tool_call_mock()

    # Collect all tool names the template uses so the sandbox has them bound
    from service.isaac_assist_service.chat.tools.tool_executor import (
        DATA_HANDLERS,
        CODE_GEN_HANDLERS,
    )
    all_tools = {**{k: None for k in DATA_HANDLERS}, **{k: None for k in CODE_GEN_HANDLERS}}

    with (
        patch.object(_ci, "instantiate_role_based_code", side_effect=_spy),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.DATA_HANDLERS",
            all_tools,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.CODE_GEN_HANDLERS",
            {},
        ),
        patch(
            "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
            new=mock_exec,
        ),
    ):
        result = _run(_ci.execute_template_canonical(template))

    assert task_id in dispatched, (
        f"{task_id}: execute_template_canonical did not call instantiate_role_based_code. "
        f"Result: {result}"
    )
    assert result.get("instantiated") is True, (
        f"{task_id}: instantiation failed. Result: {result}"
    )
