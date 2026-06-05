"""Unit tests for phase2_action_plan._action_2a/2b/2c/2d."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.qa.phase2_action_plan import (
    _action_2a, _action_2b, _action_2c, _action_2d,
)


def _detail(violations=None, metrics=None):
    return {
        "violations": violations or [],
        "metrics": metrics or {},
    }


class TestAction2A:
    def test_empty_violations_default_action(self):
        ap = _action_2a(_detail())
        assert ap["class"] == "2a-TEMPLATE_FIX"
        assert any("manual review" in a["do"].lower() for a in ap["actions"])

    def test_ik_feasible_critical(self):
        ap = _action_2a(_detail(violations=[
            {"axis": "ik_feasible", "severity": "CRITICAL"}
        ]))
        assert any("unreachable" in a["do"].lower() for a in ap["actions"])

    def test_inside_obstacle_uses_suggest_delta(self):
        ap = _action_2a(_detail(violations=[
            {"axis": "inside_obstacle_bbox", "severity": "CRITICAL",
             "value": "/World/Bin",
             "details": {"suggest_axis": "z", "suggest_delta_m": 0.07}},
        ]))
        a = ap["actions"][0]
        assert "Bin" in a["do"]
        assert "+z" in a["do"]
        assert a["delta"]["shift_m"] == 0.07

    def test_warning_severity_ignored(self):
        # Only CRITICAL handled in 2a
        ap = _action_2a(_detail(violations=[
            {"axis": "manipulability", "severity": "WARNING"}
        ]))
        # Falls through to default
        assert any("manual review" in a["do"].lower() for a in ap["actions"])


class TestAction2B:
    def test_clearance_pct_error(self):
        ap = _action_2b(_detail(violations=[
            {"axis": "clearance_pct", "severity": "ERROR", "value": 42.0},
        ]))
        a = ap["actions"][0]
        assert "42" in a["do"]
        assert "way-point" in a["do"] or "obstacle" in a["do"]
        assert a["target"] == ">= 90"

    def test_mutex_conflict_error(self):
        ap = _action_2b(_detail(violations=[
            {"axis": "mutex_conflict", "severity": "ERROR"},
        ]))
        assert any("MUTEX_PATH" in a["do"] for a in ap["actions"])

    def test_sensor_zone_error(self):
        ap = _action_2b(_detail(violations=[
            {"axis": "cube_in_sensor_zone_at_settle", "severity": "ERROR"},
        ]))
        assert any("sensor" in a["do"].lower() for a in ap["actions"])


class TestAction2C:
    def test_high_pick_reach_flagged(self):
        ap = _action_2c(_detail(metrics={
            "pick_reach_utilization": 0.94,
            "drop_reach_utilization": 0.50,
        }), gate_rate=0.20)
        do_str = " ".join(a.get("do", "") for a in ap["actions"])
        assert "94%" in do_str

    def test_low_manipulability_flagged(self):
        ap = _action_2c(_detail(metrics={
            "pick_manipulability": 0.03,
        }), gate_rate=0.40)
        do_str = " ".join(a.get("do", "") for a in ap["actions"])
        assert "0.03" in do_str
        assert "singular" in do_str.lower()

    def test_phase4_marker_always_present(self):
        ap = _action_2c(_detail(), gate_rate=0.20)
        assert any(a.get("phase") == "4-scenario-profile" for a in ap["actions"])


class TestAction2D:
    def test_multi_robot_canonical(self):
        ap = _action_2d("CP-65", gate_status="stable_fail")
        assert "multi_robot_relay" in ap["bug_categories"]
        assert any("multi-robot" in a["do"].lower() for a in ap["actions"])

    def test_ur10_canonical(self):
        ap = _action_2d("CP-74", gate_status="stable_fail")
        assert "ur10_grip" in ap["bug_categories"]
        assert any("UR10" in a["do"] for a in ap["actions"])

    def test_obstacle_rich_canonical(self):
        ap = _action_2d("CP-37", gate_status="flaky")
        assert "obstacle_rich" in ap["bug_categories"]
        assert any("Obstacle-rich" in a["do"] or "obstacle" in a["do"].lower()
                   for a in ap["actions"])

    def test_unknown_canonical_general_category(self):
        ap = _action_2d("CP-99", gate_status="stable_fail")
        assert ap["bug_categories"] == []
        assert any("Mode B" in a["do"] or "drop precision" in a["do"]
                   for a in ap["actions"])
