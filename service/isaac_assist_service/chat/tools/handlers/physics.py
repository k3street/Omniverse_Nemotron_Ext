"""Physics handlers — target scope: physics scene config,
articulations, drives, joints, deformable meshes, contact sensors,
gravity dispenser, force application.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 5 ("Move physics handlers").

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 5."""
    return None
