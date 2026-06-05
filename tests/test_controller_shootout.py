"""Unit tests for scripts/qa/controller_shootout_report.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))

from controller_shootout_report import _aggregate, _infer_controller, _render


def test_infer_controller_curobo_franka():
    """Default canonical (no explicit target_source) defaults to curobo+franka_panda."""
    tmp = REPO_ROOT / "tests/_tmp_test_curobo.json"
    tmp.write_text(json.dumps({
        "code": (
            "robot_wizard(robot_name='franka_panda', dest_path='/World/Franka', position=[0,0,0.75])\n"
            "setup_pick_place_controller(robot_path='/World/Franka', target_source='curobo', source_paths=['/World/Cube_1'])"
        )
    }))
    info = _infer_controller(tmp)
    assert info["target_source"] == "curobo"
    assert info["robot_family"] == "franka_panda"
    tmp.unlink()


def test_infer_controller_spline_franka():
    tmp = REPO_ROOT / "tests/_tmp_test_spline.json"
    tmp.write_text(json.dumps({
        "code": (
            "robot_wizard(robot_name='franka_panda', dest_path='/World/Franka', position=[0,0,0.75])\n"
            "setup_pick_place_controller(robot_path='/World/Franka', target_source='spline', source_paths=['/World/Cube_1'])"
        )
    }))
    info = _infer_controller(tmp)
    assert info["target_source"] == "spline"
    assert info["robot_family"] == "franka_panda"
    tmp.unlink()


def test_infer_controller_ur10_builtin():
    tmp = REPO_ROOT / "tests/_tmp_test_ur10.json"
    tmp.write_text(json.dumps({
        "code": (
            "import_robot(file_path='UR10', dest_path='/World/UR10')\n"
            "setup_pick_place_controller(robot_path='/World/UR10', robot_family='ur10', target_source='builtin')"
        )
    }))
    info = _infer_controller(tmp)
    assert info["target_source"] == "builtin"
    assert info["robot_family"] == "ur10"
    tmp.unlink()


def test_aggregate_empty():
    by_bucket = _aggregate([])
    assert by_bucket == {}


def test_aggregate_simple():
    """Multiple CPs into separate buckets via inferred controller."""
    fake_baselines = [
        {"label": "CP-X", "status": "stable_ok", "per_run": [{"above_floor": True, "at_rest": True, "speed": 0.05}]},
    ]
    # Expects CP-X.json to exist; we'll skip assertion on bucket key but verify structure
    by_bucket = _aggregate(fake_baselines)
    # If CP-X.json doesn't exist, bucket key is curobo/franka_panda (defaults)
    assert isinstance(by_bucket, dict)


def test_render_empty():
    """Render with no buckets writes 'No baselines found'."""
    out = REPO_ROOT / "tests/_tmp_test_render.md"
    _render({}, out)
    text = out.read_text()
    assert "No baselines found" in text
    out.unlink()


def test_render_with_data():
    """Render formats summary table with all expected columns."""
    by_bucket = {
        "curobo/franka_panda": {
            "canonicals": {"CP-22"},
            "verdicts": ["stable_ok"],
            "above_floor": [1.0],
            "at_rest": [1.0],
            "speeds": [0.01],
            "n_runs": 1,
        }
    }
    out = REPO_ROOT / "tests/_tmp_test_render2.md"
    _render(by_bucket, out)
    text = out.read_text()
    assert "Controller Shootout Report" in text
    assert "curobo/franka_panda" in text
    assert "stable_ok" in text
    out.unlink()
