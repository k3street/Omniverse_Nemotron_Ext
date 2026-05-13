"""Central handler registry — the dispatch pattern that replaces
the inline `DATA_HANDLERS["X"] = _handle_X` assignments in
`tool_executor.py`.

Phase 9 (2026-05-13) — every theme module's `register(data, codegen)`
is now populated. `register_handlers()` is the sole dispatch entry
point called by `tool_executor.py`; the inline assignments and dict
literals are gone.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phases 2 + 9.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from . import (
    animation,
    arena,
    contact_sequence,
    diagnostics,
    physics,
    pick_place,
    rendering,
    resolve,
    robot,
    ros2,
    scene_authoring,
    scene_blueprints,
    sdg,
    sensors,
    teleop,
    training,
    vision,
    workflow,
)

logger = logging.getLogger(__name__)

# Module order — themes with no internal state (scene/physics) come
# first; resolve/workflow (which reference earlier ones at call time
# only) come last. Tool names are disjoint across modules per the
# Phase 9 byte-diff audit, so order is informational, not load-bearing.
_THEME_MODULES = (
    scene_authoring,
    physics,
    robot,
    sensors,
    sdg,
    training,
    ros2,
    teleop,
    scene_blueprints,
    diagnostics,
    arena,
    vision,
    rendering,
    animation,
    pick_place,
    contact_sequence,
    workflow,
    resolve,
)

# ros2-live tools registered via ros_mcp_tools when ros-mcp is installed;
# when absent, all 11 names register as None sentinels so dispatch still
# sees them (callers get a clear "ros-mcp not installed" error rather
# than KeyError).
_ROS2_LIVE_TOOL_NAMES = (
    "ros2_connect",
    "ros2_list_topics",
    "ros2_get_topic_type",
    "ros2_get_message_type",
    "ros2_subscribe_once",
    "ros2_publish",
    "ros2_publish_sequence",
    "ros2_list_services",
    "ros2_call_service",
    "ros2_list_nodes",
    "ros2_get_node_details",
)


def _register_ros2_live(data: Dict[str, Any]) -> None:
    """Wire `handle_ros2_*` from `ros_mcp_tools` if importable, else None."""
    try:
        from .. import ros_mcp_tools as rmt
        data.update({
            "ros2_connect": rmt.handle_ros2_connect,
            "ros2_list_topics": rmt.handle_ros2_list_topics,
            "ros2_get_topic_type": rmt.handle_ros2_get_topic_type,
            "ros2_get_message_type": rmt.handle_ros2_get_message_type,
            "ros2_subscribe_once": rmt.handle_ros2_subscribe_once,
            "ros2_publish": rmt.handle_ros2_publish,
            "ros2_publish_sequence": rmt.handle_ros2_publish_sequence,
            "ros2_list_services": rmt.handle_ros2_list_services,
            "ros2_call_service": rmt.handle_ros2_call_service,
            "ros2_list_nodes": rmt.handle_ros2_list_nodes,
            "ros2_get_node_details": rmt.handle_ros2_get_node_details,
        })
    except ImportError:
        logger.warning(
            "[ToolExecutor] ros-mcp not installed — "
            "ROS2 live tools disabled (pip install ros-mcp)"
        )
        for name in _ROS2_LIVE_TOOL_NAMES:
            data[name] = None


def register_handlers(
    data: Dict[str, Callable[..., Awaitable[Any]]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Invoke each theme module's `register(data, codegen)`, then attach
    external registrators (multimodal, diagnose, bridges, ros2-live).

    Args:
        data:    the `DATA_HANDLERS` dict to populate.
        codegen: the `CODE_GEN_HANDLERS` dict to populate.
    """
    for module in _THEME_MODULES:
        module.register(data, codegen)

    # External registrators — own their own pattern, already extracted.
    from ..multimodal_handlers import register_multimodal_handlers
    register_multimodal_handlers(data)

    from ....diagnose.tool import register_diagnose_handlers
    register_diagnose_handlers(data)

    from ..bridge_tools import register_bridge_handlers
    register_bridge_handlers(data)

    # ROS2 live tools (ros-mcp optional dep).
    _register_ros2_live(data)
