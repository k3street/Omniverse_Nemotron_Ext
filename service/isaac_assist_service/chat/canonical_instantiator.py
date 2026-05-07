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


#: Tool names allowed to remain in the LLM schema after a hard-instantiate.
#: These are verify/inspect/fix tools — the agent uses them to confirm the
#: scaffolded scene works and to make targeted adjustments (teleport, set
#: gains/attrs) when verify reports issues. Build tools (create_*,
#: robot_wizard, setup_pick_place_controller, run_usd_script, etc.) are
#: deliberately excluded — the canonical already executed them.
ALLOWED_AFTER_INSTANTIATE = frozenset({
    # Form gate
    "verify_pickplace_pipeline",
    # Function gate
    "simulate_traversal_check",
    # Targeted fix tools (physics state adjustment without rebuilding)
    "teleport_prim",
    "set_attribute",
    "set_drive_gains",
    "set_joint_targets",
    "set_joint_limits",
    "set_physics_params",
    "set_physics_scene_config",
    # Inspection (read-only)
    "scene_summary",
    "list_all_prims",
    "find_prims_by_name",
    "find_prims_by_schema",
    "get_bounding_box",
    "get_articulation_state",
    "get_world_transform",
    "get_attribute",
    "list_attributes",
    "list_applied_schemas",
    "get_console_errors",
    "get_physics_errors",
    # Knowledge (allows agent to look up corrective recipes)
    "lookup_knowledge",
    "explain_error",
})


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


_PRIM_PATH_RE = __import__("re").compile(
    r"prim_path=['\"]([^'\"]+)['\"]|dest_path=['\"]([^'\"]+)['\"]|"
    r"sensor_path=['\"]([^'\"]+)['\"]|robot_path=['\"]([^'\"]+)['\"]"
)


def _extract_prim_paths(template: Dict[str, Any]) -> List[str]:
    """Pull prim paths from the template's code field so we can tell the
    LLM exactly which paths were created (no guessing from naming heuristics)."""
    code = template.get("code") or ""
    paths: List[str] = []
    seen: set = set()
    for m in _PRIM_PATH_RE.finditer(code):
        for g in m.groups():
            if g and g.startswith("/") and g not in seen:
                paths.append(g)
                seen.add(g)
    return paths


def format_instantiation_summary(
    result: Dict[str, Any],
    template: Dict[str, Any] = None,
) -> str:
    """Strong directive system-prompt addendum — tells the LLM the scene is
    already built, lists FORBIDDEN tools, and enumerates the actual prim
    paths so the agent uses them verbatim instead of guessing."""
    if not result.get("instantiated"):
        return ""
    tid = result.get("task_id", "?")
    n_ok = result.get("n_ok", 0)
    n_total = result.get("n_calls", 0)
    err_count = len(result.get("errors", []))

    # Names of tools that built the scene — agent must NOT call them again.
    forbidden = sorted({e["tool"] for e in result.get("executed", [])
                        if e.get("ok")})

    prim_paths = _extract_prim_paths(template) if template else []

    lines = [
        "## CRITICAL: Scene already built — do NOT rebuild",
        "",
        f"The scene has been pre-scaffolded from verified canonical template "
        f"**{tid}**. {n_ok}/{n_total} build tool calls executed successfully"
        + (f"; {err_count} returned errors." if err_count else "."),
        "",
    ]

    if prim_paths:
        lines.append("**Prims created (use these EXACT paths in your tool calls — do not invent variants):**")
        for p in prim_paths:
            lines.append(f"  - `{p}`")
        lines.append("")

    lines.append(
        "**Your role for this turn is VERIFICATION ONLY.** The build phase is "
        "complete. The LLM tool schema has been filtered to remove build tools "
        "— you literally cannot call them. Only verify/inspect/fix tools are "
        "available."
    )

    if err_count:
        lines.append("")
        lines.append("Errors during instantiation (do not retry the same call; "
                     "if these block verification, surface them honestly):")
        for err in result.get("errors", [])[:5]:
            lines.append(f"  - {err}")

    lines.extend([
        "",
        "**Required tool calls for this turn (in this order):**",
        "  1. `verify_pickplace_pipeline` — form gate. Pass stages list "
        "(robot_path, pick_path, place_path) inferred from the scene.",
        "  2. `simulate_traversal_check` — function gate. Pass cube_path + "
        "target_path; default duration_s=60s.",
        "  3. Reply with the results, honestly. If either gate fails, "
        "surface the specific issue and propose a targeted fix using "
        "tools like `teleport_prim`, `set_attribute`, or `set_drive_gains` "
        "— do NOT re-run any of the forbidden build tools.",
    ])
    return "\n".join(lines)
