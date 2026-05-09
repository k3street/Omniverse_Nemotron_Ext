"""Unit tests for diagnose/tool.py — mocked execute_tool_call.

Exercises the orchestrator logic end-to-end using a fake execute_tool_call
that returns canned dicts. Validates verdict classification across the
6 violation types from spec §Validation criteria.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import patch, AsyncMock

import pytest

pytestmark = [pytest.mark.l0, pytest.mark.asyncio]

from service.isaac_assist_service.diagnose import tool as dtool
from service.isaac_assist_service.diagnose import cache as dcache


def _ok_solve_ik(joint_positions=None) -> Dict[str, Any]:
    return {"output": json.dumps({
        "success": True,
        "joint_positions": joint_positions or [0, 0, 0, 0, 0, 0, 0],
    })}


def _fail_solve_ik() -> Dict[str, Any]:
    return {"output": json.dumps({"success": False, "error": "out_of_workspace"})}


def _singularity_ok() -> Dict[str, Any]:
    return {"output": json.dumps({"manipulability": 0.12})}


def _singularity_singular() -> Dict[str, Any]:
    return {"output": json.dumps({"manipulability": 0.02})}


def _bbox(prim_path: str, mn, mx) -> Dict[str, Any]:
    return {"output": json.dumps({"min": list(mn), "max": list(mx)})}


def _path_clearance(clear, total=20) -> Dict[str, Any]:
    return {"output": json.dumps({"clear_samples": clear, "n_samples": total})}


def _build_router(replies: Dict[str, list]):
    """Return a mock execute_tool_call that returns replies in order per tool."""
    counters = {k: 0 for k in replies}

    async def fake(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name in replies:
            i = counters[name]
            counters[name] = min(i + 1, len(replies[name]) - 1)
            return replies[name][i]
        return {"output": ""}
    return fake


@pytest.fixture(autouse=True)
def _clear_cache_each():
    dcache.clear_cache()
    yield
    dcache.clear_cache()


@pytest.mark.asyncio
async def test_feasible_pick_only_no_obstacles():
    fake = _build_router({
        "solve_ik": [_ok_solve_ik()],
        "check_singularity": [_singularity_ok()],
        "get_bounding_box": [],
        "check_path_clearance": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [0.4, 0.0, 0.5],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "feasible"
    assert report["violations"] == []


@pytest.mark.asyncio
async def test_infeasible_no_ik_at_pick():
    fake = _build_router({
        "solve_ik": [_fail_solve_ik()],
        "get_bounding_box": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [10.0, 0.0, 0.5],  # far from robot
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "infeasible"
    axes = [v["axis"] for v in report["violations"]]
    assert "ik_feasible" in axes


@pytest.mark.asyncio
async def test_overconstrained_blocked_path():
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0]*7), _ok_solve_ik([1]*7)],
        "check_singularity": [_singularity_ok(), _singularity_ok()],
        "check_path_clearance": [_path_clearance(clear=5, total=20)],  # 25%
        "get_bounding_box": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [0.4, 0.0, 0.5],
            "drop_pose": [-0.4, 0.0, 0.3],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "overconstrained"
    axes = [v["axis"] for v in report["violations"]]
    assert "clearance_pct" in axes
    assert report["metrics"]["clearance_pct"] == 25.0


@pytest.mark.asyncio
async def test_tightly_feasible_near_reach_edge():
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0]*7)],
        "check_singularity": [_singularity_ok()],
        "get_bounding_box": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [0.83, 0.0, 0.0],  # 0.83/0.855 = 97% — WARNING
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "tightly_feasible"
    axes = [v["axis"] for v in report["violations"]]
    assert "reach_utilization" in axes


@pytest.mark.asyncio
async def test_inside_obstacle_bbox_critical():
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0]*7)],
        "check_singularity": [_singularity_ok()],
        # bbox for obstacle Bin
        "get_bounding_box": [_bbox("/World/Bin", [0.4, -0.1, 0.0], [0.6, 0.1, 0.4])],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "drop_pose": [0.5, 0.0, 0.2],  # inside Bin bbox
            "obstacles": ["/World/Bin"],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "infeasible"
    axes = [v["axis"] for v in report["violations"]]
    assert "inside_obstacle_bbox" in axes
    # Alternative should suggest moving along an axis
    alt_axes = [a["axis"] for a in report["alternatives"]]
    assert "inside_obstacle_bbox" in alt_axes


@pytest.mark.asyncio
async def test_cache_hit_on_repeat_call():
    fake_call_count = {"n": 0}

    async def fake(name, args):
        fake_call_count["n"] += 1
        return _ok_solve_ik() if name == "solve_ik" else {"output": ""}

    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        args = {
            "robot_path": "/World/Franka",
            "pick_pose": [0.4, 0.0, 0.5],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "seed": 42,
        }
        r1 = await dtool._handle_diagnose_scene_feasibility(args)
        n_after_first = fake_call_count["n"]
        r2 = await dtool._handle_diagnose_scene_feasibility(args)
        n_after_second = fake_call_count["n"]
    assert r1["cache_hit"] is False
    assert r2["cache_hit"] is True
    # Second call should not re-invoke physics queries
    assert n_after_second == n_after_first


@pytest.mark.asyncio
async def test_missing_robot_path_returns_error():
    report = await dtool._handle_diagnose_scene_feasibility({})
    assert "error" in report
    assert "robot_path" in report["error"]


@pytest.mark.asyncio
async def test_register_handlers_adds_to_dict():
    handlers: Dict[str, Any] = {}
    dtool.register_diagnose_handlers(handlers)
    assert "diagnose_scene_feasibility" in handlers


@pytest.mark.asyncio
async def test_determinism_same_seed_byte_identical():
    """T-DETERM-1: same scene + same seed → byte-identical metrics dict.

    Cache disabled to force re-computation. Mock returns identical responses
    for both calls (real solve_ik with same seed should also be deterministic).
    """
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])],
        "check_singularity": [_singularity_ok()],
        "get_bounding_box": [],
    })
    args = {
        "robot_path": "/World/Franka",
        "pick_pose": [0.4, 0.0, 0.5],
        "robot_base": [0.0, 0.0, 0.0],
        "max_reach": 0.855,
        "use_cache": False,
        "seed": 42,
    }
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        r1 = await dtool._handle_diagnose_scene_feasibility(args)
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        r2 = await dtool._handle_diagnose_scene_feasibility(args)
    # Strip elapsed_ms (timing-dependent) before compare
    r1_norm = {k: v for k, v in r1.items() if k != "elapsed_ms"}
    r2_norm = {k: v for k, v in r2.items() if k != "elapsed_ms"}
    assert r1_norm == r2_norm
    assert r1["seed_used"] == 42 == r2["seed_used"]


@pytest.mark.asyncio
async def test_path_clear_no_violation():
    """T-PATH-2: clearance_pct = 100% → no violation."""
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0]*7), _ok_solve_ik([1]*7)],
        "check_singularity": [_singularity_ok(), _singularity_ok()],
        "check_path_clearance": [_path_clearance(clear=20, total=20)],  # all clear
        "get_bounding_box": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [0.4, 0.0, 0.5],
            "drop_pose": [-0.4, 0.0, 0.3],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "feasible"
    axes = [v["axis"] for v in report["violations"]]
    assert "clearance_pct" not in axes
    assert report["metrics"]["clearance_pct"] == 100.0


@pytest.mark.asyncio
async def test_singular_config_warning():
    """Manipulability warning flips verdict to tightly_feasible without
    blocking other axes."""
    fake = _build_router({
        "solve_ik": [_ok_solve_ik([0]*7)],
        "check_singularity": [_singularity_singular()],  # 0.02 → WARNING
        "get_bounding_box": [],
    })
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        report = await dtool._handle_diagnose_scene_feasibility({
            "robot_path": "/World/Franka",
            "pick_pose": [0.4, 0.0, 0.5],
            "robot_base": [0.0, 0.0, 0.0],
            "max_reach": 0.855,
            "use_cache": False,
        })
    assert report["verdict"] == "tightly_feasible"
    axes = [v["axis"] for v in report["violations"]]
    assert "manipulability" in axes


@pytest.mark.asyncio
async def test_cache_disabled_skips_lookup():
    """use_cache=False should bypass cache entirely — both calls miss."""
    fake_call_count = {"n": 0}

    async def fake(name, args):
        fake_call_count["n"] += 1
        return _ok_solve_ik() if name == "solve_ik" else {"output": ""}

    args = {
        "robot_path": "/World/Franka",
        "pick_pose": [0.4, 0.0, 0.5],
        "robot_base": [0.0, 0.0, 0.0],
        "max_reach": 0.855,
        "use_cache": False,
    }
    with patch.object(dtool, "_execute_tool_call", side_effect=fake):
        r1 = await dtool._handle_diagnose_scene_feasibility(args)
        n_after_first = fake_call_count["n"]
        r2 = await dtool._handle_diagnose_scene_feasibility(args)
        n_after_second = fake_call_count["n"]
    assert r1["cache_hit"] is False
    assert r2["cache_hit"] is False
    # Each call should re-invoke physics queries
    assert n_after_second > n_after_first
