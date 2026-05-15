"""Block 1B Step 18 — role-based template equivalence.

For each refactored CP-N template, sandbox-capture tool calls from both the
legacy `code` field and the new `code_template`+`role_defaults`. Assert the
captured (tool, args) sequence is identical. This proves the refactor is
pre-execution-equivalent and the function-gate will continue to pass.

When this test passes for CP-01..CP-05, the legacy `code` field can be
removed in a future commit without risk to function-gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

pytestmark = pytest.mark.l0


REPO = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO / "workspace" / "templates"


def _capture_tool_calls(code: str, task_id: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Run code in the canonical sandbox; return list of (tool_name, kwargs)."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        _SAFE_BUILTINS,
    )
    from service.isaac_assist_service.chat.tools.tool_executor import (
        CODE_GEN_HANDLERS,
        DATA_HANDLERS,
    )

    captured: List[Tuple[str, Dict[str, Any]]] = []

    def _make_cap(name: str):
        def _cap(**kwargs):
            captured.append((name, dict(kwargs)))
        return _cap

    tool_names = set(DATA_HANDLERS.keys()) | set(CODE_GEN_HANDLERS.keys())
    sandbox: Dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    for n in tool_names:
        sandbox[n] = _make_cap(n)
    exec(compile(code, f"<{task_id}>", "exec"), sandbox)
    return captured


def _normalize(captured: List[Tuple[str, Dict[str, Any]]]) -> List[Tuple[str, str]]:
    """Convert captured (name, kwargs) to (name, sorted-key-repr) so dict-
    insertion-order differences don't cause spurious diffs.

    Float values are repr'd with full precision; lists/tuples preserve order
    (semantically meaningful for source_paths, planning_obstacles, etc.).
    """
    out = []
    for name, kwargs in captured:
        items = sorted(kwargs.items())
        out.append((name, repr(items)))
    return out


def _load(task_id: str) -> Dict[str, Any]:
    path = TEMPLATES_DIR / f"{task_id}.json"
    return json.loads(path.read_text())


@pytest.mark.parametrize("task_id", ["CP-01", "CP-02", "CP-03", "CP-04", "CP-05", "CP-09", "CP-10", "CP-11", "CP-12", "CP-13", "CP-14", "CP-15", "CP-16", "CP-17", "CP-18", "CP-19", "CP-21", "CP-23", "CP-24", "CP-26", "CP-27", "CP-28", "CP-29", "CP-30", "CP-31", "CP-32", "CP-33", "CP-34", "CP-36", "CP-38"])
def test_code_template_equivalent_to_legacy_code(task_id: str):
    """code_template + role_defaults produces same captured tool calls as
    legacy code field."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        instantiate_role_based_code,
    )

    tpl = _load(task_id)
    legacy_code = tpl.get("code") or ""
    role_code = instantiate_role_based_code(tpl)

    assert legacy_code.strip(), f"{task_id} has empty legacy code"
    assert role_code.strip(), f"{task_id} has empty role_based code"

    legacy_calls = _capture_tool_calls(legacy_code, task_id + "-legacy")
    role_calls = _capture_tool_calls(role_code, task_id + "-role")

    assert _normalize(legacy_calls) == _normalize(role_calls), (
        f"{task_id} role-template does not match legacy code. "
        f"legacy={len(legacy_calls)} calls, role={len(role_calls)} calls.\n"
        f"first divergence at index "
        f"{next((i for i, (a, b) in enumerate(zip(_normalize(legacy_calls), _normalize(role_calls))) if a != b), 'tail')}"
    )


def test_role_template_unfilled_placeholders_detect():
    """Sanity: substituted code_template must contain no unfilled {{...}}."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        instantiate_role_based_code,
    )

    tpl = _load("CP-01")
    code = instantiate_role_based_code(tpl)
    assert "{{" not in code, f"CP-01 has unfilled placeholders: {code[:300]}"
