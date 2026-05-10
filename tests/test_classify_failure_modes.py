"""Unit tests for scripts/qa/classify_failure_modes.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))

from classify_failure_modes import _classify


def test_classify_no_summary_is_F_BUILD():
    """A probe with no summary = build failed."""
    result = _classify("CP-X", {})
    assert result["category"] == "F-BUILD_FAILED"


def test_classify_high_plan_fail_rate_A():
    """plan_calls > 0, plan_fails / plan_calls > 0.5 → A."""
    data = {
        "summary": {
            "plan_calls": 24,
            "plan_fails": 24,
            "cubes_delivered_final": 0,
            "cycles_attempted_final": 0,
            "last_phase": {},
            "cube_paths": [],
        }
    }
    result = _classify("CP-X", data)
    assert "A-PLAN_FAILS_HIGH" in result["category"]


def test_classify_partial_delivery_B():
    """1 of 4 cubes delivered → B-PARTIAL_DELIVERY."""
    data = {
        "summary": {
            "plan_calls": 10,
            "plan_fails": 0,
            "cubes_delivered_final": 1,
            "cycles_attempted_final": 1,
            "last_phase": {},
            "cube_paths": ["/World/Cube_1", "/World/Cube_2", "/World/Cube_3", "/World/Cube_4"],
        }
    }
    result = _classify("CP-X", data)
    assert "B-PARTIAL_DELIVERY" in result["category"]


def test_classify_no_plan_no_pick_D():
    """plan_calls=0, cycles=0 → D."""
    data = {
        "summary": {
            "plan_calls": 0,
            "plan_fails": 0,
            "cubes_delivered_final": 0,
            "cycles_attempted_final": 0,
            "last_phase": {"/World/Franka": "wait_sensor"},
            "cube_paths": ["/World/Cube_1"],
        }
    }
    result = _classify("CP-X", data)
    assert "D-NO_PLAN_NO_PICK" in result["category"]


def test_classify_event_cycle_E():
    """last_phase contains 'event=N' → E-EVENT_CYCLE."""
    data = {
        "summary": {
            "plan_calls": 5,
            "plan_fails": 0,
            "cubes_delivered_final": 0,
            "cycles_attempted_final": 1,
            "last_phase": {"/World/UR10": "event=3"},
            "cube_paths": ["/World/Cube_1"],
        }
    }
    result = _classify("CP-X", data)
    assert "E-EVENT_CYCLE" in result["category"]


def test_classify_z_other_fallback():
    """No matching pattern → Z-OTHER."""
    data = {
        "summary": {
            "plan_calls": 20,
            "plan_fails": 2,  # 10% rate, below threshold
            "cubes_delivered_final": 0,
            "cycles_attempted_final": 1,
            "last_phase": {"/World/Franka": "executing"},
            "cube_paths": ["/World/Cube_1"],
        }
    }
    result = _classify("CP-X", data)
    assert "Z-OTHER" in result["category"]


def test_classify_returns_metadata():
    """Result has expected fields populated."""
    data = {
        "summary": {
            "plan_calls": 10, "plan_fails": 5,
            "cubes_delivered_final": 1, "cycles_attempted_final": 2,
            "last_phase": {"/World/Franka": "wait_sensor"},
            "cube_paths": ["/World/Cube_1"],
            "last_errors": ["Error: foo"],
        }
    }
    result = _classify("CP-X", data)
    assert result["plan_calls"] == 10
    assert result["plan_fails"] == 5
    assert result["plan_fail_rate"] == 0.5
    assert result["cubes_delivered"] == 1
    assert result["cycles_attempted"] == 2
    assert result["last_phase"] == {"/World/Franka": "wait_sensor"}
    assert "Error: foo" in result["last_errors"][0]
