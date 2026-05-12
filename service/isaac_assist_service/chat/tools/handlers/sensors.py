"""Sensor handlers — target scope: cameras, lidars, force-torque
sensors, NIR / hyperspectral sensors, barcode reader, contact
sensors, proximity sensors, debug-draw.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 6 ("Move robot, sensor, SDG, training handlers").

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 6."""
    return None
