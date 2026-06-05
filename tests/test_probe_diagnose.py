"""Unit tests for probe_ctrl_telemetry.py _diagnose_stuck patterns."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))

from probe_ctrl_telemetry import _diagnose_stuck


def test_diagnose_returns_nonempty():
    """Empty-but-positive summary still returns at least one diagnosis."""
    summary = {
        "duration_s": 60,
        "plan_calls": 5,
        "cubes_delivered_final": 1,
        "cycles_attempted_final": 1,
    }
    diags = _diagnose_stuck(summary)
    assert len(diags) >= 1  # At minimum the 'no obvious pattern' fallback


def test_diagnose_zero_plan_zero_cycles():
    summary = {
        "duration_s": 60,
        "plan_calls": 0,
        "cubes_delivered_final": 0,
        "cycles_attempted_final": 0,
    }
    diags = _diagnose_stuck(summary)
    assert any("controller never engaged" in d for d in diags)


def test_diagnose_cycles_no_delivery():
    summary = {
        "duration_s": 60,
        "plan_calls": 30,
        "cubes_delivered_final": 0,
        "cycles_attempted_final": 5,
    }
    diags = _diagnose_stuck(summary)
    assert any("gripper-release issue" in d for d in diags)


def test_diagnose_high_plan_fail_rate():
    summary = {
        "duration_s": 60,
        "plan_calls": 24,
        "plan_fail_rate": 1.0,  # 100% fail
    }
    diags = _diagnose_stuck(summary)
    assert any("planning failing" in d for d in diags)


def test_diagnose_phantom_handoff_pattern():
    """N>=2 robots, 1 cube delivered, majority in wait_sensor → phantom_handoff."""
    summary = {
        "duration_s": 60,
        "plan_calls": 10,
        "cubes_delivered_final": 1,
        "cycles_attempted_final": 1,
        "last_phase": {
            "/World/FrankaA": "wait_sensor",
            "/World/FrankaB": "wait_sensor",
        },
    }
    diags = _diagnose_stuck(summary)
    assert any("phantom_handoff" in d for d in diags)


def test_diagnose_cube_stuck():
    """Cube didn't move >5cm in 60s → 'stuck' pattern."""
    cube_traj = [
        {"sim_t": 0.0, "/World/Cube_1": [0.0, 0.4, 0.835]},
        {"sim_t": 30.0, "/World/Cube_1": [0.01, 0.40, 0.835]},
        {"sim_t": 60.0, "/World/Cube_1": [0.015, 0.40, 0.835]},
    ]
    summary = {
        "duration_s": 60,
        "cube_paths": ["/World/Cube_1"],
        "plan_calls": 0,
        "cubes_delivered_final": 0,
    }
    diags = _diagnose_stuck(summary, cube_traj)
    assert any("stuck" in d.lower() for d in diags)


def test_diagnose_cube_fell_below_floor():
    """Cube ended at z<0.5 → 'fell below'."""
    cube_traj = [
        {"sim_t": 0.0, "/World/Cube_1": [0.0, 0.4, 0.835]},
        {"sim_t": 30.0, "/World/Cube_1": [0.5, 0.4, 0.700]},
        {"sim_t": 60.0, "/World/Cube_1": [1.0, 0.4, 0.480]},
    ]
    summary = {
        "duration_s": 60,
        "cube_paths": ["/World/Cube_1"],
        "plan_calls": 5,
    }
    diags = _diagnose_stuck(summary, cube_traj)
    assert any("fell below" in d.lower() for d in diags)


def test_diagnose_drop_precision_pattern():
    """Cube moved into bin xy but final z is significantly below initial — fell off bin edge."""
    cube_traj = [
        {"sim_t": 0.0, "/World/Cube_1": [0.0, 0.4, 0.835]},
        {"sim_t": 30.0, "/World/Cube_1": [0.7, -0.5, 0.85]},
        {"sim_t": 60.0, "/World/Cube_1": [0.72, -0.65, 0.525]},
    ]
    summary = {
        "duration_s": 60,
        "cube_paths": ["/World/Cube_1"],
        "plan_calls": 10,
        "cubes_delivered_final": 1,
    }
    diags = _diagnose_stuck(summary, cube_traj)
    assert any("vertical drop" in d.lower() or "drop_target" in d.lower() for d in diags)


def test_diagnose_runtime_error_passthrough():
    summary = {
        "duration_s": 60,
        "plan_calls": 1,
        "last_errors": [
            "RuntimeError: planning failed for /World/Cube_1",
            "AttributeError: foo",
        ],
    }
    diags = _diagnose_stuck(summary)
    assert any("runtime error" in d.lower() for d in diags)
