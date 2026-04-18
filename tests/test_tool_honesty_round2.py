"""Regression tests for the second wave of handler fail-fast fixes
added during the 2026-04-18 autonomous audit cycle.

Covers the post-trajectory-handler audit round:
 - measure_distance, inspect_camera: prim-validity pre-checks
 - add_sublayer: URL resolver probe before attach
 - nucleus_browse: explicit error key on non-OK omni.client.list
 - simplify_collision: approximation allowlist + Set() verification
 - checkpoint_training: reject IPC ack with empty checkpoint_path
 - configure_teleop_mapping: raise on empty mapping
 - solve_ik: raise on IK failure
 - fix_ros2_qos: raise when no publisher exists
 - bulk_set_attribute: raise when zero of N paths updated
 - evaluate_groot: raise when results file missing post-wait
 - check_physics_health: stage-wide PhysicsScene search (the C-03
   root-cause fix)

L0 — no Kit, no USD. Asserts on generated code strings only.
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytestmark = pytest.mark.l0


def _T():
    from service.isaac_assist_service.chat.tools import tool_executor
    return tool_executor


def test_measure_distance_gates_both_prims():
    T = _T()
    src = inspect.getsource(T._handle_measure_distance)
    assert "measure_distance: prim_a not found" in src
    assert "measure_distance: prim_b not found" in src
    assert "IsValid()" in src


def test_inspect_camera_checks_isa_camera():
    T = _T()
    code = T._gen_inspect_camera({"camera_path": "/World/Camera"})
    ast.parse(code)
    assert "IsValid()" in code
    assert "IsA(UsdGeom.Camera)" in code
    assert "is not a UsdGeom.Camera" in code


def test_add_sublayer_probes_remote_urls():
    T = _T()
    # Remote URL case — should probe via Sdf.Layer.FindOrOpen
    code = T._gen_add_sublayer({"layer_path": "omniverse://localhost/test.usda"})
    ast.parse(code)
    assert "FindOrOpen" in code
    assert "Refusing to attach" in code
    # Local path case — should not probe (just create-or-reuse)
    local = T._gen_add_sublayer({"layer_path": "/tmp/sublayer.usda"})
    ast.parse(local)
    # Local path still has a separate create-new error path
    assert "CreateNew" in local


def test_nucleus_browse_sets_error_key_on_failure():
    T = _T()
    src = inspect.getsource(T._handle_nucleus_browse)
    assert "payload[\"error\"]" in src
    assert "Nucleus server unreachable" in src


def test_simplify_collision_validates_approximation():
    T = _T()
    # Unknown approximation
    bad = T._gen_simplify_collision({"prim_path": "/W/M", "approximation": "bogusShape"})
    ast.parse(bad)
    assert "raise ValueError" in bad
    assert "unknown approximation" in bad
    # Known approximation
    ok = T._gen_simplify_collision({"prim_path": "/W/M", "approximation": "convexHull"})
    ast.parse(ok)
    assert "IsValid()" in ok
    assert "refused the value" in ok


def test_checkpoint_training_rejects_empty_path():
    T = _T()
    src = inspect.getsource(T._handle_checkpoint_training)
    assert "if not ckpt_path:" in src
    assert "did not include a checkpoint_path" in src


def test_configure_teleop_mapping_raises_on_empty():
    T = _T()
    code = T._gen_configure_teleop_mapping({"robot_path": "/World/R"})
    ast.parse(code)
    assert "0 axes ended up mapped" in code
    assert "raise RuntimeError" in code
    # Promoted assert → raise
    assert "configure_teleop_mapping: robot not found" in code


def test_solve_ik_raises_on_failure():
    T = _T()
    code = T._gen_solve_ik({
        "articulation_path": "/World/Arm",
        "target_position": [0.5, 0, 0.5],
        "robot_type": "franka",
    })
    ast.parse(code)
    assert "IK failed for" in code
    assert "raise RuntimeError" in code
    # Old silent print must not remain
    assert "IK failed — target may be unreachable or near singularity" not in code


def test_fix_ros2_qos_raises_without_publisher():
    T = _T()
    code = T._gen_fix_ros2_qos({"topic": "/camera/rgb"})
    ast.parse(code)
    assert "no ROS2 publisher node found" in code
    assert "raise RuntimeError" in code


def test_bulk_set_attribute_raises_when_zero_applied():
    T = _T()
    code = T._gen_bulk_set_attribute({
        "prim_paths": ["/W/A", "/W/B"],
        "attr": "mass",
        "value": 1.0,
    })
    ast.parse(code)
    assert "if _applied == 0" in code
    assert "0 of " in code
    assert "raise RuntimeError" in code


def test_evaluate_groot_raises_when_results_missing():
    T = _T()
    code = T._gen_evaluate_groot({"task": "pick_place", "num_episodes": 10})
    ast.parse(code)
    assert "no results file at" in code
    assert "raise RuntimeError" in code
    assert "server_proc.terminate()" in code


def test_check_physics_health_searches_whole_stage_for_scene():
    """C-03 root-cause fix: PhysicsScene existence is a stage-level
    property and must not be scoped to the articulation's subtree."""
    T = _T()
    code_scoped = T._gen_check_physics_health({"articulation_path": "/World/Arm"})
    ast.parse(code_scoped)
    assert "_all_stage_prims" in code_scoped
    assert "GetPseudoRoot" in code_scoped
    # The (correctly scoped) articulation-subtree iteration for other checks
    # must still be there
    assert "scope_root" in code_scoped
    # Unscoped code path shouldn't regress either
    code_no_scope = T._gen_check_physics_health({})
    ast.parse(code_no_scope)
    assert "_all_stage_prims" in code_no_scope
