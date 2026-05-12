"""Vision handlers — target scope: vision_detect_objects,
vision_bounding_boxes, vision_plan_trajectory,
vision_analyze_scene, capture_camera_image.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 7. Phase 76 ("Vision tool handlers: real Gemini Vision")
later deepens this module with the real provider plumbing.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 7 / deepened by Phase 76."""
    return None
