"""Test _augment_verify_with_feasibility wrapper (Phase 1.5 / Opus §F)."""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.l0, pytest.mark.asyncio]

from service.isaac_assist_service.chat.tools.handlers import diagnostics as te


def _verify_result(stages_result, issues=None, pipeline_ok=True) -> Dict[str, Any]:
    payload = {
        "results": stages_result,
        "issues": issues or [],
        "pipeline_ok": pipeline_ok,
    }
    return {"output": json.dumps(payload)}


def _stage(idx, robot="/World/Franka", pick=[0.4, 0, 0.5], place=[0.3, 0, 0.3], reach=0.855):
    return {
        "index": idx,
        "robot_path": robot,
        "robot_pos": [0, 0, 0],
        "pick_pos": pick,
        "place_pos": place,
        "reach_m": reach,
        "reachable": True,
    }


def _make_diagnose_async(verdict="feasible", violations=None):
    async def _diag(name, args):
        if name != "diagnose_scene_feasibility":
            return {"output": "{}"}
        return {
            "verdict": verdict,
            "violations": violations or [],
            "metrics": {},
        }
    return _diag


@pytest.mark.asyncio
async def test_feasible_keeps_pipeline_ok():
    verify_result = _verify_result([_stage(0)], pipeline_ok=True)
    with patch.object(te, "execute_tool_call", side_effect=_make_diagnose_async("feasible")):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    parsed = json.loads(out["output"])
    assert parsed["pipeline_ok"] is True
    assert parsed["issues"] == []
    assert parsed["feasibility_reports"][0]["verdict"] == "feasible"


@pytest.mark.asyncio
async def test_infeasible_flips_pipeline_ok():
    verify_result = _verify_result([_stage(0)], pipeline_ok=True)
    violations = [{
        "axis": "ik_feasible", "severity": "CRITICAL",
        "value": False, "threshold": True,
        "message": "No IK at drop pose [10, 0, 0.5]",
    }]
    diag = _make_diagnose_async("infeasible", violations)
    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    parsed = json.loads(out["output"])
    assert parsed["pipeline_ok"] is False
    # CRITICAL message merged into issues
    assert any("[feasibility]" in m and "No IK" in m for m in parsed["issues"])
    assert parsed["feasibility_reports"][0]["verdict"] == "infeasible"


@pytest.mark.asyncio
async def test_overconstrained_keeps_ok_but_adds_issues():
    """ERROR severity adds issue but doesn't flip pipeline_ok per spec."""
    verify_result = _verify_result([_stage(0)], pipeline_ok=True)
    violations = [{
        "axis": "clearance_pct", "severity": "ERROR",
        "value": 25.0, "threshold": 60.0,
        "message": "Transit corridor 25% clear",
    }]
    diag = _make_diagnose_async("overconstrained", violations)
    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    parsed = json.loads(out["output"])
    assert parsed["pipeline_ok"] is True  # ERROR alone doesn't flip
    assert any("Transit corridor" in m for m in parsed["issues"])


@pytest.mark.asyncio
async def test_warning_no_issues_added():
    """WARNING severity: violation reported in feasibility_reports but not issues."""
    verify_result = _verify_result([_stage(0)], pipeline_ok=True)
    violations = [{
        "axis": "manipulability", "severity": "WARNING",
        "value": 0.04, "threshold": 0.05,
        "message": "Near singularity",
    }]
    diag = _make_diagnose_async("tightly_feasible", violations)
    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    parsed = json.loads(out["output"])
    assert parsed["pipeline_ok"] is True
    # WARNING is below the merge threshold (only ERROR/CRITICAL merge)
    assert not any("Near singularity" in m for m in parsed["issues"])


@pytest.mark.asyncio
async def test_diagnose_failure_adds_issue_but_doesnt_crash():
    verify_result = _verify_result([_stage(0)], pipeline_ok=True)

    async def diag_raises(name, args):
        raise RuntimeError("kit_rpc unreachable")

    with patch.object(te, "execute_tool_call", side_effect=diag_raises):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    parsed = json.loads(out["output"])
    assert any("diagnose call failed" in m for m in parsed["issues"])
    # pipeline_ok unchanged because we couldn't get a verdict
    assert parsed["pipeline_ok"] is True


@pytest.mark.asyncio
async def test_multiple_stages_all_diagnosed():
    verify_result = _verify_result([_stage(0), _stage(1, robot="/World/UR10")])
    counts = {"n": 0}

    async def diag(name, args):
        counts["n"] += 1
        return {"verdict": "feasible", "violations": [], "metrics": {}}

    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [{}, {}])
    parsed = json.loads(out["output"])
    assert counts["n"] == 2
    assert len(parsed["feasibility_reports"]) == 2


@pytest.mark.asyncio
async def test_unparseable_output_returned_unchanged():
    verify_result = {"output": "not json"}

    async def diag(name, args):
        return {"verdict": "feasible"}

    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [])
    assert out == verify_result  # leave alone


@pytest.mark.asyncio
async def test_stage_without_pick_pos_skipped():
    """Stage missing pick_pos / place_pos shouldn't be diagnosed."""
    bad_stage = {"index": 0, "robot_path": "/World/Franka",
                 "robot_pos": [0, 0, 0], "pick_pos": None, "place_pos": [0.3, 0, 0.3]}
    verify_result = _verify_result([bad_stage])
    counts = {"n": 0}

    async def diag(name, args):
        counts["n"] += 1
        return {"verdict": "feasible"}

    with patch.object(te, "execute_tool_call", side_effect=diag):
        out = await te._augment_verify_with_feasibility(verify_result, [{}])
    assert counts["n"] == 0  # skipped due to missing pick_pos
