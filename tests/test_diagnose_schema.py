"""Unit tests for diagnose/schema.py — pure-Python, no Kit RPC needed."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.diagnose.schema import (
    Severity, Verdict, Violation, Alternative, FeasibilityReport,
    classify_verdict, THRESHOLDS,
)


def _v(axis: str, severity: Severity, value=0, threshold=0, message="") -> Violation:
    return Violation(axis=axis, severity=severity, value=value,
                     threshold=threshold, message=message)


class TestClassifyVerdict:
    def test_no_violations_is_feasible(self):
        assert classify_verdict([]) == Verdict.FEASIBLE

    def test_only_info_is_feasible(self):
        assert classify_verdict([_v("x", Severity.INFO)]) == Verdict.FEASIBLE

    def test_warning_is_tightly_feasible(self):
        assert classify_verdict([_v("reach_utilization", Severity.WARNING)]) == Verdict.TIGHTLY_FEASIBLE

    def test_error_is_overconstrained(self):
        assert classify_verdict([_v("clearance_pct", Severity.ERROR)]) == Verdict.OVERCONSTRAINED

    def test_critical_is_infeasible(self):
        assert classify_verdict([_v("ik_feasible", Severity.CRITICAL)]) == Verdict.INFEASIBLE

    def test_critical_dominates_lesser(self):
        vs = [
            _v("a", Severity.WARNING),
            _v("b", Severity.ERROR),
            _v("c", Severity.CRITICAL),
        ]
        assert classify_verdict(vs) == Verdict.INFEASIBLE

    def test_error_dominates_warning(self):
        vs = [_v("a", Severity.WARNING), _v("b", Severity.ERROR)]
        assert classify_verdict(vs) == Verdict.OVERCONSTRAINED


class TestViolationSerialization:
    def test_severity_serialized_as_string(self):
        v = _v("ik_feasible", Severity.CRITICAL, value=False, threshold=True,
               message="No IK")
        d = v.to_dict()
        assert d["severity"] == "CRITICAL"
        assert d["axis"] == "ik_feasible"
        assert d["value"] is False
        assert d["message"] == "No IK"

    def test_details_default_empty(self):
        v = _v("x", Severity.WARNING)
        assert v.to_dict()["details"] == {}


class TestAlternativeSerialization:
    def test_skips_none_fields(self):
        a = Alternative(axis="reach", suggestion="Move closer")
        d = a.to_dict()
        assert "expected_value" not in d
        assert "delta" not in d
        assert d["axis"] == "reach"

    def test_keeps_provided_fields(self):
        a = Alternative(axis="reach", suggestion="Move 5cm closer",
                        expected_value=0.88, delta={"axis": "x", "shift_m": -0.05})
        d = a.to_dict()
        assert d["expected_value"] == 0.88
        assert d["delta"]["shift_m"] == -0.05


class TestFeasibilityReportSerialization:
    def test_round_trip(self):
        r = FeasibilityReport(
            verdict=Verdict.TIGHTLY_FEASIBLE,
            metrics={"reach_utilization": 0.94, "manipulability": 0.04},
            violations=[
                _v("manipulability", Severity.WARNING, value=0.04, threshold=0.05,
                   message="Near singularity"),
            ],
            alternatives=[Alternative(axis="manip", suggestion="Tilt EE 10°")],
            seed_used=42, cache_hit=False, elapsed_ms=420,
        )
        d = r.to_dict()
        assert d["verdict"] == "tightly_feasible"
        assert d["seed_used"] == 42
        assert len(d["violations"]) == 1
        assert d["violations"][0]["severity"] == "WARNING"
        assert d["alternatives"][0]["suggestion"] == "Tilt EE 10°"
        # Multi-robot fields absent when None
        assert "per_cycle" not in d
        assert "aggregate" not in d

    def test_multirobot_fields_present_when_set(self):
        r = FeasibilityReport(
            verdict=Verdict.OVERCONSTRAINED,
            metrics={},
            per_cycle=[{"verdict": "feasible"}, {"verdict": "infeasible"}],
            aggregate={"worst_severity": "CRITICAL"},
        )
        d = r.to_dict()
        assert "per_cycle" in d
        assert "aggregate" in d
        assert d["aggregate"]["worst_severity"] == "CRITICAL"


class TestThresholdsTable:
    def test_required_axes_present(self):
        for axis in ("ik_feasible", "collision_distance", "manipulability",
                     "reach_utilization", "inside_obstacle_bbox", "clearance_pct"):
            assert axis in THRESHOLDS

    def test_reach_utilization_bounds(self):
        th = THRESHOLDS["reach_utilization"]
        assert th["critical"] >= th["warning"]
        assert th["critical"] == 1.0
        assert th["warning"] == 0.95
