"""Regression tests for the six handler fail-fast fixes added on 2026-04-18.

Each of these handlers previously had a silent-success hole where a
degenerate input (missing prim, empty waypoints, zero-joint articulation,
None planner result, empty sensors list) produced a `print(...)` and a
success=True return instead of raising. Keep the raise-on-bad-input
wording pinned so a careless refactor doesn't reintroduce the hole.

L0 — no Kit, no USD. Asserts on the generated code string only.
"""
from __future__ import annotations

import ast

import pytest

pytestmark = pytest.mark.l0


def test_record_trajectory_raises_on_invalid_articulation():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_record_trajectory({"articulation": "/World/Arm", "duration": 1.0})
    ast.parse(code)
    assert "raise RuntimeError" in code
    assert "articulation path not found" in code
    assert "no Revolute/Prismatic joints" in code


def test_replay_trajectory_raises_on_empty_waypoints():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_replay_trajectory(
        {"articulation_path": "/World/Arm", "trajectory_path": "/tmp/t.json"}
    )
    ast.parse(code)
    assert "raise RuntimeError" in code
    assert "no waypoints" in code
    # The 'print("No waypoints found ...")' from the old code must not return
    assert "No waypoints found in trajectory file." not in code


def test_record_waypoints_raises_on_zero_joints():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_record_waypoints(
        {"articulation_path": "/W/A", "output_path": "/tmp/wp.json", "format": "json"}
    )
    ast.parse(code)
    assert "raise RuntimeError" in code
    assert "has no joints" in code
    assert "nothing to record" in code


def test_plan_trajectory_raises_on_none_result():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_plan_trajectory(
        {
            "articulation_path": "/W/A",
            "waypoints": [{"position": [0, 0, 0]}],
            "robot_type": "franka",
        }
    )
    ast.parse(code)
    assert "raise RuntimeError" in code
    assert "planner could not connect the requested waypoints" in code
    # The old 'print("Failed to plan trajectory ...")' must not remain
    assert "Failed to plan trajectory — try different waypoints" not in code


def test_move_to_pose_lula_rrt_raises_on_none_trajectory():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_move_to_pose(
        {
            "articulation_path": "/W/A",
            "target_position": [0.5, 0.5, 0.5],
            "planner": "lula_rrt",
        }
    )
    ast.parse(code)
    assert "raise RuntimeError" in code
    assert "planner returned None" in code
    # The old 'Lula RRT: failed to find path' print must not remain
    assert "Lula RRT: failed to find path" not in code


def test_configure_ros2_bridge_raises_on_empty_sensors():
    from service.isaac_assist_service.chat.tools import tool_executor as T
    code = T._gen_configure_ros2_bridge({"sensors": []})
    ast.parse(code)
    assert "raise ValueError" in code
    assert "no sensors were specified" in code
    # The old 'No sensors specified — nothing to configure' print must not remain
    assert "print('No sensors specified" not in code
