"""Workflow handlers — target scope: start_workflow,
edit_workflow_plan, approve_workflow_checkpoint,
cancel_workflow, get_workflow_status, plus the workflow
template registry.

Phase 2 stub: empty module with a no-op `register()`. The
workflow handlers move into here in Phase 15 ("Move the
workflow handlers to a stateful module"), which is split from
the rest of Epoch I's moves because workflows carry state and
need a different test harness.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 15."""
    return None
