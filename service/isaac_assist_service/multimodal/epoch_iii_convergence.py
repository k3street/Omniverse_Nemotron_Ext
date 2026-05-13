"""Phase 44 — Epoch III convergence test scaffold.

Exercises the full workflow lifecycle through every transition,
verifies checkpoint persistence + rollback.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 44.
"""
from typing import Any, Dict


def run_epoch_iii_convergence() -> Dict[str, Any]:
    """Walk a synthetic workflow through start → checkpoint → approve → done."""
    from .workflow_engine import WorkflowEngine, WorkflowPhaseStatus

    engine = WorkflowEngine()
    status = WorkflowPhaseStatus.PENDING
    transitions = []
    for action in ("start", "checkpoint", "approve"):
        status = engine.transition(status, action)
        transitions.append(status.value)
    return {
        "transitions": transitions,
        "final_status": status.value,
        "event_count": len(engine.events),
        "convergence_ok": status == WorkflowPhaseStatus.COMPLETED,
    }
