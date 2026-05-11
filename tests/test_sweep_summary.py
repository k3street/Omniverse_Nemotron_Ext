"""Unit tests for scripts/qa/sweep_summary.py _classify_fail."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.l0
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))
from sweep_summary import _classify_fail


def _make_run(cube_final=None, speed=0, above=True, at_rest=True, in_xy=False):
    return {
        "per_run": [{
            "cube_final": cube_final or [0.0, 0.0, 0.8],
            "speed": speed,
            "above_floor": above,
            "at_rest": at_rest,
            "in_xy": in_xy,
        }]
    }


def test_physx_explosion_cube_position():
    """Cube > 100m flags PHYSX_EXPLOSION."""
    r = _make_run(cube_final=[1000.0, 500.0, -200.0], speed=50000)
    assert _classify_fail(r) == "A_PHYSX_EXPLOSION"


def test_physx_explosion_speed():
    """Speed > 1000 m/s flags PHYSX_EXPLOSION even if position normal."""
    r = _make_run(speed=2000)
    assert _classify_fail(r) == "A_PHYSX_EXPLOSION"


def test_fell_through_bin():
    """Above_floor=False + in_xy=True → fell through bin."""
    r = _make_run(above=False, in_xy=True)
    assert _classify_fail(r) == "B_FELL_THROUGH_BIN"


def test_fell_off_belt():
    """Above_floor=False + in_xy=False → fell off belt."""
    r = _make_run(above=False, in_xy=False)
    assert _classify_fail(r) == "C_FELL_OFF_BELT"


def test_not_at_rest():
    """In xy but moving → D."""
    r = _make_run(in_xy=True, at_rest=False)
    assert _classify_fail(r) == "D_NOT_AT_REST"


def test_off_target_xy():
    """Above floor + at rest but wrong xy → E."""
    r = _make_run(in_xy=False, above=True, at_rest=True)
    assert _classify_fail(r) == "E_OFF_TARGET_XY"


def test_true_near_miss():
    """All checks pass but still fail → F."""
    r = _make_run(in_xy=True, above=True, at_rest=True)
    assert _classify_fail(r) == "F_TRUE_NEAR_MISS"


def test_no_per_run_data():
    """Missing per_run → Z."""
    r = {}
    assert _classify_fail(r) == "Z_NO_DATA"


def test_no_cube_final():
    """per_run with no cube_final → Z."""
    r = {"per_run": [{"success": False}]}
    assert _classify_fail(r) == "Z_NO_DATA"
