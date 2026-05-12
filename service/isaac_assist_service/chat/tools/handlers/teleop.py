"""Teleop handlers — target scope: start / stop teleop sessions,
record demos, validate demos, configure mapping, safety configs,
watchdog scripts.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 7 ("Move ROS2, teleop, scene-blueprint, diagnostic,
arena, vision handlers").

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
