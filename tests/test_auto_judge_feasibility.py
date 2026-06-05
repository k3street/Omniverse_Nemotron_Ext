"""Test the scene_feasibility axis added to auto_judge.heuristic_verdict.

Per Opus review §I: 6th axis 0-5 scored from diagnose_scene_feasibility output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.qa.auto_judge import heuristic_verdict, _score_scene_feasibility


def _tx(tool_calls=None, persona_msgs=None, turns=3, end_reason="completed"):
    return {
        "turns": turns,
        "tool_calls": tool_calls or [],
        "end_reason": end_reason,
        "persona_messages": persona_msgs or [],
    }


def _task(expected_tools=None):
    return {"expected_tools": expected_tools or []}


def _diagnose_call(verdict: str) -> dict:
    return {
        "tool": "diagnose_scene_feasibility",
        "executed": True,
        "success": True,
        "output": json.dumps({"verdict": verdict, "metrics": {}, "violations": []}),
    }


class TestScoreSceneFeasibility:
    def test_feasible_scores_5(self):
        score, v = _score_scene_feasibility([_diagnose_call("feasible")])
        assert score == 5 and v == "feasible"

    def test_tightly_feasible_scores_3(self):
        score, v = _score_scene_feasibility([_diagnose_call("tightly_feasible")])
        assert score == 3 and v == "tightly_feasible"

    def test_overconstrained_scores_1(self):
        score, v = _score_scene_feasibility([_diagnose_call("overconstrained")])
        assert score == 1 and v == "overconstrained"

    def test_infeasible_scores_0(self):
        score, v = _score_scene_feasibility([_diagnose_call("infeasible")])
        assert score == 0 and v == "infeasible"

    def test_not_called_scores_3_neutral(self):
        score, v = _score_scene_feasibility([
            {"tool": "scene_summary", "executed": True, "success": True, "output": "{}"},
        ])
        assert score == 3 and v is None

    def test_dict_output_parsed(self):
        score, v = _score_scene_feasibility([{
            "tool": "diagnose_scene_feasibility",
            "executed": True, "success": True,
            "output": {"verdict": "feasible"},  # already-dict, not str
        }])
        assert score == 5

    def test_unknown_verdict_falls_through(self):
        score, v = _score_scene_feasibility([_diagnose_call("invalid_verdict")])
        assert score == 3 and v is None


class TestHeuristicVerdictMaxIs30:
    def test_perfect_run_with_feasibility(self):
        tx = _tx(tool_calls=[_diagnose_call("feasible")] + [
            {"tool": "scene_summary", "executed": True, "success": True}
            for _ in range(5)
        ])
        out = heuristic_verdict(tx, _task())
        assert out["max"] == 30
        # 5 (engagement) + 5 (tool_execution=min(5,n_ok=6)) + 3 (expected) + 5 (halluc) + 3 (resp) + 5 (feas) = 26
        assert out["scores"]["scene_feasibility"] == 5
        assert out["total"] >= 25

    def test_neutral_when_diagnose_not_called(self):
        tx = _tx(tool_calls=[
            {"tool": "scene_summary", "executed": True, "success": True}
        ])
        out = heuristic_verdict(tx, _task())
        assert out["scores"]["scene_feasibility"] == 3  # neutral
        assert out["scene_feasibility_verdict"] is None

    def test_infeasible_diagnosis_caps_score(self):
        # Even if everything else is perfect, infeasible scene → 0 on this axis
        tx = _tx(tool_calls=[_diagnose_call("infeasible")] + [
            {"tool": "x", "executed": True, "success": True} for _ in range(5)
        ])
        out = heuristic_verdict(tx, _task())
        assert out["scores"]["scene_feasibility"] == 0
        assert out["scene_feasibility_verdict"] == "infeasible"
