"""
tool_executor.py
-----------------
Dispatches LLM tool-calls to the appropriate backend:
  - Kit RPC (port 8001) for live scene operations
  - Local data lookups (sensor specs, deformable presets)
  - Code generation for complex operations sent to Kit for approval

All handlers return a dict that gets fed back to the LLM as a tool result.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from . import kit_tools
from .patch_validator import validate_patch, format_issues_for_llm, has_blocking_issues
from ...config import config

logger = logging.getLogger(__name__)

# ── Paths to knowledge files ─────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[4] / "workspace"

# Cache loaded once

# ═══════════════════════════════════════════════════════════════════════════
# Recovered state for bundled PR handlers (local QA branch only)
# Module-level dicts, regexes, classes, and imports that the extraction
# script missed. Restores 182 broken name references so handlers can run.
# ═══════════════════════════════════════════════════════════════════════════
import re
import re as _re
import time
import time as _time
import threading as _threading
import uuid as _uuid
import uuid as _wf_uuid
from datetime import datetime as _wf_dt
from typing import Tuple
import asyncio as _asyncio
from dataclasses import dataclass, field

from ...finetune.turn_recorder import TurnRecorder

# cleanly, but the Python-side wrapper keeps ordering deterministic for tests.

import asyncio as _asyncio
from dataclasses import dataclass, field
from typing import Tuple

# Phase 8 wave 29 (2026-05-13): _LockedPatch + _StageWriteLockQueue
# canonical home moved to handlers/_state.py. Aliased here for any
# remaining `_te._LockedPatch` / `_te._StageWriteLockQueue` callsites.
from .handlers._state import LockedPatch as _LockedPatch  # noqa: E402, F401
from .handlers._state import StageWriteLockQueue as _StageWriteLockQueue  # noqa: E402, F401

# ── Recovered module-level state from PR branches ───────────────────────

# Phase 8 wave 28 (2026-05-13): _ASYNC_TASKS + _ASYNC_TASKS_LOCK
# canonical home moved to handlers/_state.py. Aliased here so any
# remaining `_te._ASYNC_TASKS*` callsites see the same instances.
from .handlers._state import ASYNC_TASKS as _ASYNC_TASKS  # noqa: E402, F401
from .handlers._state import ASYNC_TASKS_LOCK as _ASYNC_TASKS_LOCK  # noqa: E402, F401

# Phase 14 + 16 (2026-05-13): canonical homes moved to handlers/_state.py.
# Aliased here for any remaining `_te.X` callsites. NOTE: these are
# the legacy `_WORKFLOWS` dict + `_WORKFLOW_TEMPLATES` registry, NOT
# the typed `WORKFLOWS = WorkflowState()` singleton. Names kept with
# underscore prefix to preserve `_te._WORKFLOWS` access semantics.
from .handlers import _state as _state_module  # noqa: E402
_WORKFLOWS = _state_module._WORKFLOWS  # noqa: F811
_WORKFLOW_TEMPLATES = _state_module._WORKFLOW_TEMPLATES  # noqa: F811

_eureka_runs: Dict[str, Dict] = {}

# _PHYSICS_MATERIALS_PATH + _physics_materials migrated to handlers/physics.py (Phase 8 wave 6).

# _ROBOT_NAME_PATTERNS + _detect_robot_type deleted as dead code (2026-05-13).
# Pattern dict was used only by _detect_robot_type below; _detect_robot_type
# had zero callers (confirmed via grep). Removed in Phase 8 cleanup.

# Named-robot registry for robot_wizard — maps a known name to the
# canonical RELATIVE path under the Isaac asset root (5.x layout).
# robot_wizard resolves to a local disk path when ASSETS_ROOT_PATH is
# set and the file exists (faster, offline-capable), otherwise falls
# back to the cloud HTTPS URL.
#
# Relationship to _CATALOG_ROBOTS (module-level, used by catalog_search):
# _CATALOG_ROBOTS is a flat filename map assuming Collected_Robots/*.usd
# layout. That layout is WRONG for 5.x — Franka actually lives at
# Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd. This registry is
# the authoritative import source; _CATALOG_ROBOTS just drives search.

# Phase 8 wave 29 (2026-05-13): singleton lives in handlers/_state.py.
from .handlers._state import WRITE_LOCK_QUEUE as _WRITE_LOCK_QUEUE  # noqa: E402, F401

_turn_recorder = TurnRecorder()

# ═══════════════════════════════════════════════════════════════════════════

# ── Safe xform helper (inlined into generated code) ─────────────────────────
# Referenced USD assets (e.g. robots) often already have xform ops.
# Calling AddTranslateOp() again crashes with "Error in AddXformOp".
# Cross-handler constant; 9 import sites across 5 themes now use:
#   from ._shared import _SAFE_XFORM_SNIPPET

# ── Code generation helpers ──────────────────────────────────────────────────

# Phase 3 wave 1 — these three code generators have moved to
# handlers/scene_authoring.py. Names are re-imported here so the
# existing CODE_GEN_HANDLERS dispatch lines (e.g.
# `CODE_GEN_HANDLERS["create_prim"] = _gen_create_prim` further down
# in this file) keep working unchanged. Phase 9 swaps the dispatch
# pattern to a `register()`-based registration and the legacy inline
# assignments go away.

# Imported back at the top of this file (see Phase 3 wave 1 import block).

# ── Robot anchoring ──────────────────────────────────────────────────────────
# Isaac Sim robot USD assets contain a "rootJoint" (6-DOF free joint) that
# allows them to float freely. To anchor a robot:
# 1. Set PhysxArticulationAPI.fixedBase = True (keeps ArticulationRootAPI on root)
# 2. Delete the rootJoint (free joint)
# 3. Optionally create a FixedJoint to attach to a specific surface
# CRITICAL: Do NOT move ArticulationRootAPI — it must stay on the root prim
# or the tensor API pattern '/World/Robot' will fail with
# "Pattern did not match any articulations".

# ── Code generation dispatch ─────────────────────────────────────────────────

# Phase 9 (2026-05-13): both dispatch dicts populated by
# handlers/_dispatch.py:register_handlers() — sole entry point.
# Replaces 2 dict literals + ~340 inline assignments + 3 external
# registrator calls + ROS2 try/except block (all migrated).
CODE_GEN_HANDLERS: Dict[str, Callable[..., Any]] = {}
DATA_HANDLERS: Dict[str, Callable[..., Awaitable[Any]]] = {}

from .handlers._dispatch import register_handlers
register_handlers(DATA_HANDLERS, CODE_GEN_HANDLERS)

# ── Spec / data lookup handlers (no code gen, just return data) ──────────────

# Per-object-class size buckets in meters. The "default" row handles
# unknown classes with sensible cube-like defaults. Tuned to match
# common Isaac Sim / industrial-robotics conventions: small cubes are
# 5cm (manipulation benchmark size), tables are 1.2m (workbench).

# robot-class → registry key. Anchors generic class language ('a manipulator',
# 'a humanoid', 'a wheeled robot') to the same name resolution that
# robot_wizard / import_robot already understand. Avoids the agent inventing
# random asset paths when it should be selecting a known-good default.

# Default reach radius (meters) per robot type. Used by
# verify_pickplace_pipeline when no explicit reach is supplied.
# These are conservative envelope estimates from the manufacturer specs;
# actual cuRobo / Lula IK can refine but the envelope is what matters
# for pipeline-feasibility-without-running-IK.

# Data-only handlers (no code gen → return data directly to LLM)

# ── Main dispatch ────────────────────────────────────────────────────────────

# ── P1: per-tool result-size cap (kcode-spec sec 6.2) ──────────────────
# Bounds the size of any single tool_result before it enters the
# orchestrator's messages history. Justified by Track C 9.4 measurement:
# chars/token ratio is 2.25 (vs. chars/4 heuristic), so token cost is 2x
# what we naively estimate. Capping single tool outputs at 50KB ensures
# no single call burns ~22k tokens of context budget.
#
# Config: per-tool overrides for tools that need MORE headroom, plus
# tools that should NEVER be capped (capture_viewport's image data).
# Env flag RESULT_CAP=off disables capping entirely.

# Default cap in bytes of json-stringified result. Tools above this
# threshold get their `output` field truncated with a marker.
_RESULT_CAP_DEFAULT_CHARS = int(os.environ.get("RESULT_CAP_DEFAULT", "50000"))
# Tools that should never be capped (semantic loss > token saving)
_RESULT_CAP_EXEMPT = frozenset({
    "capture_viewport",       # image bytes — VLM needs intact data
    "vision_detect_objects",  # detection coordinates — small but every entry matters
    # Function/form gates emit the informative result as a JSON line at
    # END of output. Truncating from the beginning loses it. The output
    # may include long preceding noise (controller reset prints, stale-
    # sub Tracebacks) but those don't affect parsing as long as the
    # final JSON line survives. Exempt rather than build a tail-aware
    # truncator (simpler, lower risk of off-by-one).
    "simulate_traversal_check",
    "verify_pickplace_pipeline",
})
# Per-tool overrides (in chars). Smaller = aggressive cap.
_RESULT_CAP_OVERRIDES = {
    "run_usd_script": 12000,           # 9.2 max 205KB — tail outputs blow the budget
    "setup_pick_place_controller": 18000,  # 9.2 max 44KB — controller code is heavy
    "scene_summary": 8000,             # path-heavy, tokenizes 2.0 chars/token
    "list_all_prims": 6000,
    "find_prims_by_schema": 6000,
    "preflight_check": 16000,
}

def _apply_result_cap(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate large tool_result content. Returns either the original
    result (if under cap or capping disabled) or a copy with truncated
    fields and a `_truncated` marker.

    Truncation strategy:
    1. If `output` field exists and is large, truncate it first.
    2. If still over cap and `code` field exists, drop the `code` field
       (LLM rarely needs to re-read it; reduces noise on repeated calls).
    3. Add `_truncated` marker dict so the LLM sees the cap fired.

    Idempotent: re-capping an already-capped result is a no-op.
    """
    if os.environ.get("RESULT_CAP", "on").lower() in ("off", "0", "false"):
        return result
    if not isinstance(result, dict):
        return result
    if tool_name in _RESULT_CAP_EXEMPT:
        return result
    # Already capped — don't recap (prevents marker doubling)
    if "_truncated" in result:
        return result

    cap = _RESULT_CAP_OVERRIDES.get(tool_name, _RESULT_CAP_DEFAULT_CHARS)
    blob_size = len(json.dumps(result, default=str))
    if blob_size <= cap:
        return result

    out = dict(result)
    original_chars = blob_size
    # Step 1: truncate `output` field
    if "output" in out and isinstance(out["output"], str) and len(out["output"]) > 500:
        keep_chars = max(500, cap - 2000)  # leave room for other fields
        out["output"] = (
            out["output"][:keep_chars]
            + f"...[output truncated; original {len(out['output'])} chars]"
        )
    # Step 2: drop `code` field if still over
    new_size = len(json.dumps(out, default=str))
    if new_size > cap and "code" in out:
        out["code"] = "<dropped: code field — see prior tool_result for source>"
        new_size = len(json.dumps(out, default=str))
    out["_truncated"] = {
        "tool": tool_name,
        "original_chars": original_chars,
        "kept_chars": new_size,
        "cap": cap,
    }
    return out

def _validate_args_pydantic(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Phase 10 (2026-05-13) — validate args via the generated Pydantic
    model from handlers/_models.py:MODEL_REGISTRY. Returns None on
    success, or a structured error message string on validation failure.

    The handler still receives the original `arguments` dict — this
    function only signals invalid input before dispatch. Permissive
    models (extra='allow', Optional fields) mean validation rarely
    rejects; it catches the egregious cases (wrong type, missing
    required field).
    """
    try:
        from .handlers._models import MODEL_REGISTRY
    except Exception:
        return None  # Models not available — skip validation gracefully
    model_cls = MODEL_REGISTRY.get(tool_name)
    if model_cls is None:
        return None  # Unknown tool — fall through to dispatch's own error path
    try:
        model_cls.model_validate(arguments)
        return None
    except Exception as e:
        return f"validation failed: {type(e).__name__}: {str(e)[:300]}"


async def execute_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a single tool call and return the result dict.

    Phase 10 (2026-05-13): args are validated against the generated
    Pydantic model in handlers/_models.py:MODEL_REGISTRY before dispatch.
    Validation failures return early with `type=error, validation_blocked=True`.

    Returns:
        {"type": "code_patch", "code": ..., "description": ...}  for code-gen tools
        {"type": "data", ...}                                      for data-lookup tools
        {"type": "error", "error": ...}                            on failure

    All returns flow through `_apply_result_cap` (P1 from kcode-spec sec 6.2)
    which truncates oversized result payloads to bound LLM token cost.
    """
    logger.info(f"[ToolExecutor] Executing tool: {tool_name}({json.dumps(arguments)[:200]})")

    # Phase 10: input validation via Pydantic model.
    validation_err = _validate_args_pydantic(tool_name, arguments)
    if validation_err is not None:
        logger.warning(f"[ToolExecutor] {tool_name}: {validation_err}")
        return _apply_result_cap(tool_name, {
            "type": "error",
            "error": validation_err,
            "validation_blocked": True,
        })

    async def _inner() -> Dict[str, Any]:
        """Dispatch the validated tool call to its handler and return the result dict."""
        # 1. Data handlers — return result directly
        if tool_name in DATA_HANDLERS:
            handler = DATA_HANDLERS[tool_name]
            if handler is None:
                # Tool handled inline by LLM, no execution needed
                return {"type": "data", "note": f"{tool_name} is handled by the LLM reasoning, no live execution needed."}
            result = await handler(arguments)
            return {"type": "data", **result}

        # 2. run_usd_script — pass through to Kit
        if tool_name == "run_usd_script":
            code = arguments.get("code", "")
            desc = arguments.get("description", "Run custom script")
            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}
            result = await kit_tools.queue_exec_patch(code, desc)
            return {
                "type": "code_patch",
                "code": code,
                "description": desc,
                "queued": result.get("queued", False),
                "executed": result.get("executed", False),
                "success": result.get("success"),
                "output": result.get("output", ""),
            }

        # 3. Code generation tools — generate code, send to Kit for approval
        if tool_name in CODE_GEN_HANDLERS:
            gen_fn = CODE_GEN_HANDLERS[tool_name]
            code = gen_fn(arguments)
            desc = f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(arguments.items())[:3])})"

            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}

            # Add sensor spec auto-lookup for add_sensor_to_prim
            if tool_name == "add_sensor_to_prim" and arguments.get("product_name"):
                spec_result = await _handle_lookup_product_spec({"product_name": arguments["product_name"]})
                if spec_result.get("found"):
                    return {
                        "type": "code_patch_with_spec",
                        "code": code,
                        "description": desc,
                        "product_spec": spec_result["spec"],
                    }

            result = await kit_tools.queue_exec_patch(code, desc)
            return {
                "type": "code_patch",
                "code": code,
                "description": desc,
                "queued": result.get("queued", False),
                "executed": result.get("executed", False),
                "success": result.get("success"),
                "output": result.get("output", ""),
            }

        return {"type": "error", "error": f"Unknown tool: {tool_name}"}

    try:
        result = await _inner()
    except Exception as e:
        logger.error(f"[ToolExecutor] {tool_name} failed: {e}")
        result = {"type": "error", "error": str(e)}

    return _apply_result_cap(tool_name, result)

# Register the sensor generator

# ── Motion Planning (RMPflow / Lula) ─────────────────────────────────────────

# Robot config map: robot_type → (rmpflow_config_dir, robot_description_path, urdf_path, end_effector_frame)

# ── Asset Catalog Search ─────────────────────────────────────────────────────

# Robot name map (module-level copy for catalog indexing)

# ── Local Filesystem Search ──────────────────────────────────────────────────
# When the user references "this URDF" / "the STEP file you imported" without
# a path, the agent needs to discover local files. Without this tool the agent
# either asks the user (annoying) or generates ad-hoc glob.glob() code-patches
# (unguarded). This is a guarded discovery primitive scoped to known asset
# roots — not a general filesystem walker.
import os as _os_files
import glob as _glob_files
import fnmatch as _fnmatch_files

# Hard cap to stop the agent from triggering massive filesystem walks.
# Asset-relevant extensions only — refuse to surface secrets / source code.

# ── Nucleus Browse & Download ────────────────────────────────────────────────

# ── Scene Builder ────────────────────────────────────────────────────────────

# ── IsaacLab RL Training ─────────────────────────────────────────────────────

# ─── Vision tools — _get_viewport_bytes + _get_vision_provider migrated to handlers/_shared.py (Phase 14, 2026-05-13) ───

# ── Scene Package Export ─────────────────────────────────────────────────────
# Collects all approved code patches from the audit log for a session,
# then writes:  scene_setup.py, ros2_launch.py (if ROS2 nodes present),
# README.md, and a ros2_topics.yaml listing detected topics.

# ── Stage Analysis ───────────────────────────────────────────────────────────

# _detect_robot_type deleted as dead code — see line ~271 marker above.

# get_nav2_bridge_profile deleted as dead code (2026-05-13).
# Zero callers across service/, tests/, scripts/ via comprehensive grep.
# _NAV2_BRIDGE_PROFILES was migrated to handlers/ros2.py (Phase 8 wave 4).

# ── Recovered handler registrations (missing from original bundle extraction) ─

# ══════════════════════════════════════════════════════════════════════
# setup_pick_place_controller — composite Tier-1 industrial pick-place
#
# Built 2026-04-19 from the conveyor+Franka smoke-test. The retired
# create_behavior tool pointed callers to isaaclab_tasks or Cortex
# examples; this fills the gap with a direct RmpFlow + state-machine
# integration that runs inside Isaac Sim via a physics-step callback.
#
# Architecture: "python_callback" — Python state machine hooked into
# omni.physx, uses RmpFlow for motion generation, attaches each cube
# to the end-effector via a temporary FixedJoint during transport, and
# releases via FixedJoint deletion over the destination. No OmniGraph
# state machine, no external ROS2 controller — everything runs in-sim
# from a single code patch. The matching ROS2-bridge tool
# (setup_pick_place_ros2_bridge) provides the industrial-realism
# alternative for digital-twin scenarios; see its docstring.
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# Phase-12 toolkit — proximity sensor + teach/load pose + mode-driven
# pick-place controller. Built 2026-04-19 after conveyor_pick_place
# template surfaced these gaps across ML-researcher, industrial, and
# vision personas.
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# Mode-specific generators for setup_pick_place_controller
# ══════════════════════════════════════════════════════════════════════

# ── Shared controller snippets ─────────────────────────────────────────
# Extracted for re-use across pick-place controller generators (native,
# spline, curobo, diffik, osc). Inserted via {var} f-string interpolation
# in each generator — contents must use SINGLE braces (they get emitted
# verbatim into the generated exec_sync script).
#
# Contracts (documented in docs/qa/ctrl_attrs_schema.md):
#   - Scene Reset Manager: idempotent singleton at builtins._scene_reset_manager
#       · register(name, reset_fn) / unregister(name)
#       · reset_fn() → bool (True = done, False = retry next tick)
#   - Observability: every pick-place controller creates ctrl:* attrs on
#       its robot prim. See _PP_CTRL_ATTRS for the canonical list.

# ══════════════════════════════════════════════════════════════════════
# Controller matrix — availability probe (FAS 4)
# ══════════════════════════════════════════════════════════════════════

# === Phase 6 M4 — cuMotion-as-MoveIt2 ===

# ---------------------------------------------------------------------------
# Phase 14 finish (2026-05-13): PEP 562 lazy re-export for handler symbols.
# tool_executor.py used to import every _handle_X / _gen_X from every theme
# module so that legacy callers (tests, slash_commands.py) could do
# `from tool_executor import _handle_X`. Phase 14 replaces those ~400
# explicit imports with a single dynamic dispatch — names are resolved
# lazily by walking the registered handler dispatch dicts.

_THEME_MODULE_NAMES = (
    "arena", "animation", "scene_authoring", "scene_blueprints", "sensors",
    "physics", "pick_place", "diagnostics", "rendering", "resolve",
    "robot", "ros2", "sdg", "teleop", "training", "vision", "workflow",
)

def __getattr__(name: str):
    """Resolve _handle_X / _gen_X / _gen_Y from theme modules."""
    if name.startswith("_handle_") or name.startswith("_gen_"):
        for mod_name in _THEME_MODULE_NAMES:
            try:
                from importlib import import_module
                mod = import_module(f".handlers.{mod_name}", package=__package__)
                if hasattr(mod, name):
                    return getattr(mod, name)
            except ImportError:
                continue
    raise AttributeError(
        f"module 'tool_executor' has no attribute {name!r} (lazy resolution failed)"
    )
