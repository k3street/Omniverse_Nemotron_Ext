"""ROS2 handlers — target scope: ros2_connect, list / subscribe /
publish topics, services, OmniGraph ROS2 bridge, replay rosbags,
TF viewer, QoS fix, AMENT precheck.

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` (and the
adjacent `ros_mcp_tools.py`) into here in Phase 7. Note that
Phase 7b adapts to Isaac Sim 6.0's split of `isaacsim.ros2.bridge`
into four focused extensions; that work also lives here.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 7 / 7b."""
    return None
