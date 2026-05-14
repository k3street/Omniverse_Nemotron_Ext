"""Phase 63 — contact_sequence handler.

Bridges the `execute_contact_sequence_plan` tool to the Phase 63
runtime in `multimodal.execute_contact_sequence_runtime`. The orchestrator
logic (state machine, predicate evaluation, dry-run executor) lives in
that module; this file is the dispatch-layer entry point.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from service.isaac_assist_service.multimodal.execute_contact_sequence_runtime import (
    ContactObservation,
    ContactSequencePlan,
    ContactSequenceRuntime,
    ContactStep,
)
from service.isaac_assist_service.observability.handler_telemetry import with_telemetry


@with_telemetry
async def _handle_execute_contact_sequence_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch handler for `execute_contact_sequence_plan` tool.

    Args (from tool call):
        steps: list of step dicts. Each must have step_idx, step_type,
            prim_a, prim_b. Optional fields: target_force_N, target_torque_Nm,
            duration_s, success_predicate, retry_count, mutex_paths.
        abort_on_failure: bool (default True).
        dry_run: bool (default True). Live mode requires Kit RPC.

    Returns dict:
        success: bool — whether plan executed cleanly
        results: list of per-step dicts (step_idx, step_type, success,
            duration_s, error)
        plan_complete: bool — whether all steps ran
        issues: list of validation issues
    """
    raw_steps = args.get("steps") or []
    if not isinstance(raw_steps, list):
        return {
            "success": False,
            "error": "steps must be a list of step dicts",
            "results": [],
            "plan_complete": False,
            "issues": ["malformed 'steps' arg"],
        }

    try:
        steps = [_normalize_step(s, i) for i, s in enumerate(raw_steps)]
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"step normalization failed: {exc}",
            "results": [],
            "plan_complete": False,
            "issues": [str(exc)],
        }

    abort_on_failure = bool(args.get("abort_on_failure", True))
    dry_run = bool(args.get("dry_run", True))

    plan = ContactSequencePlan(steps=steps, abort_on_failure=abort_on_failure)
    issues = plan.validate()
    if issues:
        return {
            "success": False,
            "error": "plan validation failed",
            "results": [],
            "plan_complete": False,
            "issues": issues,
        }

    runtime = ContactSequenceRuntime(dry_run=dry_run)
    try:
        results = runtime.execute_plan(plan)
    except NotImplementedError as exc:
        return {
            "success": False,
            "error": str(exc),
            "results": [],
            "plan_complete": False,
            "issues": ["live mode requires Kit RPC; use dry_run=True"],
        }

    return {
        "success": all(r.success for r in results),
        "results": [
            {
                "step_idx": r.step_idx,
                "step_type": r.step_type,
                "success": r.success,
                "duration_s": r.duration_s,
                "error": r.error,
            }
            for r in results
        ],
        "plan_complete": len(results) == len(steps),
        "issues": [],
    }


def _normalize_step(raw: Any, idx_hint: int) -> ContactStep:
    """Coerce a dict-form step into a ContactStep dataclass."""
    if not isinstance(raw, dict):
        raise TypeError(f"step[{idx_hint}] must be a dict, got {type(raw).__name__}")
    return ContactStep(
        step_idx=int(raw.get("step_idx", idx_hint)),
        step_type=raw.get("step_type", "make_contact"),
        prim_a=str(raw.get("prim_a", "")),
        prim_b=str(raw.get("prim_b", "")),
        target_force_N=float(raw.get("target_force_N", 0.0)),
        target_torque_Nm=float(raw.get("target_torque_Nm", 0.0)),
        duration_s=float(raw.get("duration_s", 1.0)),
        success_predicate=str(raw.get("success_predicate", "contact_established")),
        retry_count=int(raw.get("retry_count", 0)),
        mutex_paths=list(raw.get("mutex_paths") or []),
    )


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Register Phase 63 handler into the dispatch table."""
    data["execute_contact_sequence_plan"] = _handle_execute_contact_sequence_plan
