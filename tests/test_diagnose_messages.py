"""Unit tests for diagnose/messages.py — Swedish + English templates."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.diagnose.messages import (
    format_violation, format_for_user,
)


class TestFormatViolation:
    def test_reach_utilization_warning_swedish(self):
        msg = format_violation("reach_utilization", "WARNING",
                               value=0.94, threshold=0.95, pose_label="pick", lang="sv")
        assert "94%" in msg
        assert "räckvidd" in msg
        assert "pick" in msg

    def test_reach_utilization_warning_english(self):
        msg = format_violation("reach_utilization", "WARNING",
                               value=0.94, threshold=0.95, pose_label="pick", lang="en")
        assert "94%" in msg
        assert "reach" in msg.lower()

    def test_inside_obstacle_swedish(self):
        msg = format_violation("inside_obstacle_bbox", "CRITICAL",
                               path="/World/Bin", delta_m=0.05,
                               axis_label="x", pose_label="drop", lang="sv")
        assert "Bin" in msg
        assert "0.05" in msg
        assert "+x" in msg

    def test_clearance_pct_error_swedish(self):
        msg = format_violation("clearance_pct", "ERROR", value=42.0, threshold=60.0, lang="sv")
        assert "blockerad" in msg
        assert "42" in msg

    def test_unknown_axis_falls_back(self):
        msg = format_violation("nonexistent_axis", "WARNING", value=1, lang="sv")
        assert "nonexistent_axis" in msg

    def test_missing_kwarg_does_not_raise(self):
        # value, threshold, pose_label all missing — must not crash
        msg = format_violation("reach_utilization", "WARNING", lang="sv")
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestFormatForUser:
    def test_feasible_minimal(self):
        report = {"verdict": "feasible", "violations": []}
        out = format_for_user(report, lang="sv")
        assert "feasible" in out.lower()
        assert "✅" in out

    def test_infeasible_with_violations(self):
        report = {
            "verdict": "infeasible",
            "violations": [
                {"axis": "ik_feasible", "severity": "CRITICAL", "message": "No IK at drop"},
                {"axis": "reach_utilization", "severity": "WARNING", "message": "Near edge"},
            ],
        }
        out = format_for_user(report, lang="sv")
        assert "infeasible" in out.lower()
        # CRITICAL surfaces first (severity sort)
        idx_crit = out.find("No IK at drop")
        idx_warn = out.find("Near edge")
        assert idx_crit != -1
        assert idx_warn == -1 or idx_crit < idx_warn

    def test_max_3_violations_in_summary(self):
        report = {
            "verdict": "overconstrained",
            "violations": [
                {"axis": f"x{i}", "severity": "ERROR", "message": f"violation {i}"}
                for i in range(10)
            ],
        }
        out = format_for_user(report, lang="sv")
        # First 3 must appear (sorted by severity but all are ERROR so order=insertion)
        for i in range(3):
            assert f"violation {i}" in out
        # 4th and beyond NOT included
        assert "violation 4" not in out

    def test_english_headers(self):
        report = {"verdict": "tightly_feasible", "violations": []}
        out = format_for_user(report, lang="en")
        assert "viable" in out.lower() or "tight" in out.lower()
