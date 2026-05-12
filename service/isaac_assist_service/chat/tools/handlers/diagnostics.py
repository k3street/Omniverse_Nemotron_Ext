"""Diagnostic handlers — target scope: verify_pickplace_pipeline,
check_collisions, check_physics_health, console errors,
fix_error, explain_error, diagnose_whole_body.

Note: `diagnose_scene_feasibility` already lives in
`service/isaac_assist_service/diagnose/tool.py` and is registered
via `register_diagnose_handlers()`. That module is the model for
how this theme's handlers will look post-decomposition.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 7.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 7."""
    return None
