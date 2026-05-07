"""
canonical_instantiator.py
─────────────────────────
Hard-instantiate a verified canonical template (CP-N) by executing its
`code` field as a sequence of tool calls, bypassing the LLM tool-loop.

Why this exists: when a user prompt matches a canonical template with
high confidence (similarity > 0.85), the optimal path is plan-then-
execute — run the template's verified tool sequence directly. The
agent's role then collapses from "build + verify" to just "verify and
report". This eliminates iteration, keeps conversation history small,
sidesteps payload-driven Gemini 503 throttling, and gives deterministic
results for canonical task shapes.

The fallback path (agentic iteration with templates as guidance)
remains for prompts that don't strongly match any canonical.

Architecture:
  retrieve_templates_with_scores(prompt) → top match + similarity
  if similarity > threshold:
      execute_template_canonical(template) ← THIS MODULE
      → run code field in sandbox, intercept tool calls, dispatch via
        execute_tool_call. Return summary.
  else:
      normal LLM tool-loop with templates as few-shot

Sandbox safety: only standard builtins (enumerate, range, etc.) +
the registered tool functions are bound. No file I/O, network, or
Python imports are reachable from template code.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


_SAFE_BUILTINS = {
    "enumerate": enumerate, "range": range, "len": len, "list": list,
    "dict": dict, "tuple": tuple, "set": set, "frozenset": frozenset,
    "str": str, "int": int, "float": float, "bool": bool, "bytes": bytes,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "sorted": sorted, "reversed": reversed, "zip": zip, "map": map, "filter": filter,
    "True": True, "False": False, "None": None,
    "print": print,  # diagnostic only
}


async def execute_template_canonical(template: Dict[str, Any]) -> Dict[str, Any]:
    """Run a canonical template's `code` field as tool-call sequence.

    Returns:
        {
            "task_id": str,
            "n_calls": int,
            "executed": [{"tool": str, "ok": bool, "args_preview": str}, ...],
            "errors": [str, ...],
            "instantiated": bool,
        }
    """
    from .tools.tool_executor import (
        DATA_HANDLERS, CODE_GEN_HANDLERS, execute_tool_call,
    )

    code = template.get("code") or ""
    task_id = template.get("task_id", "?")
    if not code.strip():
        return {
            "task_id": task_id, "n_calls": 0, "executed": [], "errors": ["empty code field"],
            "instantiated": False,
        }

    captured: List[tuple] = []  # list of (tool_name, kwargs)

    def _make_capturer(tool_name: str):
        def _capture(**kwargs):
            captured.append((tool_name, dict(kwargs)))
        return _capture

    tool_names = set(DATA_HANDLERS.keys()) | set(CODE_GEN_HANDLERS.keys())
    sandbox: Dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    for name in tool_names:
        sandbox[name] = _make_capturer(name)

    # Capture phase — exec the code in sandbox, intercept calls into `captured`
    try:
        exec(compile(code, f"<{task_id}>", "exec"), sandbox)
    except Exception as e:
        logger.warning(f"[CanonicalInst] {task_id} sandbox exec failed: {e}")
        return {
            "task_id": task_id, "n_calls": 0, "executed": [],
            "errors": [f"sandbox exec failed: {type(e).__name__}: {e}"],
            "instantiated": False,
        }

    if not captured:
        return {
            "task_id": task_id, "n_calls": 0, "executed": [],
            "errors": ["no tool calls captured from code field"],
            "instantiated": False,
        }

    # Execute phase — actually invoke each captured call via execute_tool_call
    executed: List[Dict[str, Any]] = []
    errors: List[str] = []
    for tool_name, args in captured:
        try:
            result = await execute_tool_call(tool_name, args)
            rtype = result.get("type")
            ok = (rtype != "error") and (result.get("success") is not False)
            args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:2])[:80]
            executed.append({"tool": tool_name, "ok": ok, "args_preview": args_preview})
            if not ok:
                err_text = result.get("error") or (result.get("output") or "").strip()
                errors.append(f"{tool_name}: {str(err_text)[:200]}")
        except Exception as e:
            executed.append({"tool": tool_name, "ok": False, "args_preview": ""})
            errors.append(f"{tool_name} raised: {type(e).__name__}: {e}")

    n_ok = sum(1 for e in executed if e["ok"])
    logger.info(
        f"[CanonicalInst] {task_id} instantiated: "
        f"{n_ok}/{len(executed)} tool calls succeeded, {len(errors)} errors"
    )
    return {
        "task_id": task_id,
        "n_calls": len(executed),
        "n_ok": n_ok,
        "executed": executed,
        "errors": errors,
        "instantiated": True,
    }


def format_instantiation_summary(result: Dict[str, Any]) -> str:
    """Concise system-prompt addendum describing what was pre-built."""
    if not result.get("instantiated"):
        return ""
    tid = result.get("task_id", "?")
    n_ok = result.get("n_ok", 0)
    n_total = result.get("n_calls", 0)
    err_count = len(result.get("errors", []))
    head = (
        f"[CANONICAL_INSTANTIATION] The scene was pre-scaffolded from "
        f"verified canonical template {tid}. "
        f"{n_ok}/{n_total} tool calls executed successfully"
    )
    if err_count:
        head += f"; {err_count} returned errors (first three below)"
    head += ".\n"

    lines = [head]
    if err_count:
        for err in result.get("errors", [])[:3]:
            lines.append(f"  - {err}")
        lines.append("")

    lines.append(
        "Your task now is verification, not building. Call "
        "verify_pickplace_pipeline (form gate) and simulate_traversal_check "
        "(function gate). Report results honestly — do NOT rebuild from scratch. "
        "If verify reports issues, surface them and propose a targeted fix."
    )
    return "\n".join(lines)
