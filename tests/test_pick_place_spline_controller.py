from __future__ import annotations

import ast
import textwrap

import pytest

from service.isaac_assist_service.chat.tools.handlers.pick_place import (
    _gen_pick_place_spline,
)


pytestmark = pytest.mark.l0


def _spline_code() -> str:
    return _gen_pick_place_spline(
        robot_path="/World/Franka",
        sensor_path=None,
        belt_path="/World/Conveyor/Belt",
        source_paths=["/World/Cube_0", "/World/Cube_1"],
        destination_path="/World/Bin",
        drop_target=None,
        ee_offset=[0.0, 0.0, 0.0],
        spline_waypoint_dt=1.5,
    )


def test_spline_controller_generated_code_parses():
    ast.parse(textwrap.dedent(_spline_code()))


def test_spline_controller_uses_deferred_belt_pause_and_surface_gating():
    code = _spline_code()

    assert "_belt_pause_request" in code
    assert "subscribe_physics_on_step_events" in code
    assert "_apply_belt_pause_outside_callback(), True, 0" in code
    assert "\ndef _resume_belt():" in code
    assert "_PICK_REACH_M = 0.88" in code
    assert "off_surface_z" in code
    assert "one flying/fallen body poisons" in code
    assert "_stabilize_source_feed" in code
    assert "physics:kinematicEnabled" in code
    assert "selected cube is released back to dynamic motion" in code
    assert "_settle_released_cube" in code
    assert "_reach_min_x" in code
    assert "unreachable X positions" in code
    assert "wait_no_cube" in code
    assert "spline_mode_log" in code


def test_spline_controller_has_no_stale_curobo_or_ur10_state_refs():
    code = _spline_code()

    assert "curobo_mode_log" not in code
    assert "_UR10_FJ_PATH" not in code
    assert 'ROBOT_FAMILY = "franka"' in code
