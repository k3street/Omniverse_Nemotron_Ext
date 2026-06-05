"""QA-10 orphan-wiring tests for Phase 61/64/66/67/71.

Each test verifies one previously-orphaned module is now reachable via
the production code path (tool dispatch or registry lookup).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def test_phase_71_gp25_present_in_robot_wizard_registry():
    """Phase 71: Yaskawa GP25 must be wired into _ROBOT_WIZARD_REGISTRY."""
    from service.isaac_assist_service.chat.tools.handlers._shared import (
        _ROBOT_WIZARD_REGISTRY,
    )
    assert "yaskawa_gp25" in _ROBOT_WIZARD_REGISTRY
    entry = _ROBOT_WIZARD_REGISTRY["yaskawa_gp25"]
    assert isinstance(entry, dict)
    # Required keys from the Phase 71 module
    for key in ("name", "manufacturer", "model"):
        assert key in entry, f"missing {key} in registry entry"
    # Aliases exist
    assert _ROBOT_WIZARD_REGISTRY.get("gp25") == "yaskawa_gp25"
    assert _ROBOT_WIZARD_REGISTRY.get("yaskawa_motoman_gp25") == "yaskawa_gp25"


@pytest.mark.asyncio
async def test_phase_61_sample_correlated_dr_reachable_via_dispatch():
    """Phase 61: sample_correlated_dr tool dispatch returns samples."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "sample_correlated_dr",
        {"preset": "sensor_camera", "n_samples": 50, "seed": 7},
    )
    assert result.get("n_samples") == 50
    assert "lighting_kelvin" in result.get("axis_names", [])
    assert result.get("empirical_rho")
    assert len(result.get("samples", [])) == 50


@pytest.mark.asyncio
async def test_phase_64_eureka_history_reachable_via_dispatch():
    """Phase 64: eureka_history tool returns dict from in-memory store."""
    from service.isaac_assist_service.chat.tools import tool_executor
    result = await tool_executor.execute_tool_call(
        "eureka_history",
        {"limit": 5},
    )
    # In-memory store starts empty → runs list is empty but shape correct
    assert "runs" in result
    assert isinstance(result["runs"], list)
    assert "count" in result


@pytest.mark.asyncio
async def test_phase_66_validate_usd_reference_post_reachable():
    """Phase 66: validate_usd_reference_post tool returns validator output."""
    from service.isaac_assist_service.chat.tools import tool_executor
    # Clean state — should pass
    ok_result = await tool_executor.execute_tool_call(
        "validate_usd_reference_post",
        {
            "prim_path": "/World/MyRef",
            "reference_target": "omniverse://localhost/assets/robot.usd",
            "asset_exists": True,
            "depth": 1,
            "prim_type_after": "Xform",
            "parent_path": "/World",
        },
    )
    assert ok_result.get("passed") is True

    # Broken state — non-existent asset
    bad_result = await tool_executor.execute_tool_call(
        "validate_usd_reference_post",
        {
            "prim_path": "/World/MyRef",
            "reference_target": "omniverse://localhost/missing.usd",
            "asset_exists": False,
        },
    )
    assert bad_result.get("passed") is False
    assert bad_result["severity_counts"]["error"] >= 1


@pytest.mark.asyncio
async def test_phase_67_validate_joint_post_reachable():
    """Phase 67: validate_joint_post tool returns validator output."""
    from service.isaac_assist_service.chat.tools import tool_executor
    # Clean revolute joint — should pass (modulo warns)
    ok_result = await tool_executor.execute_tool_call(
        "validate_joint_post",
        {
            "prim_path": "/World/Robot/joint1",
            "joint_type": "revolute",
            "body0": "/World/Robot/link0",
            "body1": "/World/Robot/link1",
            "axis": "Z",
            "lower_limit": -1.57,
            "upper_limit": 1.57,
            "articulation_root_path": "/World/Robot",
        },
    )
    assert ok_result.get("passed") is True

    # Inverted limits — error
    bad_result = await tool_executor.execute_tool_call(
        "validate_joint_post",
        {
            "prim_path": "/World/Robot/joint1",
            "joint_type": "revolute",
            "body0": "/World/Robot/link0",
            "body1": "/World/Robot/link1",
            "axis": "Z",
            "lower_limit": 1.57,
            "upper_limit": -1.57,
        },
    )
    assert bad_result.get("passed") is False
    assert bad_result["severity_counts"]["error"] >= 1
