"""Tests for Phase 70b — create_behavior code-generator (Isaac Sim 5.x).

Gate: code generator emits valid Isaac Sim 5.x Cortex behavior code;
behavior pattern enum complete.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.create_behavior_codegen import (
    BEHAVIOR_TEMPLATES,
    PATTERN_REQUIRED_PARAMS,
    BehaviorConfig,
    CreateBehaviorCodeGenerator,
    expected_patterns,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

def test_phase_70b_metadata():
    md = get_phase_metadata()
    assert md["phase"] == "70b"
    assert md["status"] == "landed"
    assert "create_behavior" in md["title"] or "behavior" in md["title"].lower()


# ---------------------------------------------------------------------------
# 2. expected_patterns — at least 7 entries
# ---------------------------------------------------------------------------

def test_expected_patterns_count():
    patterns = expected_patterns()
    assert len(patterns) >= 7


def test_expected_patterns_contains_all_known():
    patterns = expected_patterns()
    for p in [
        "pick_place",
        "navigate_to",
        "scan_grid",
        "press_button",
        "follow_path",
        "guard_zone",
        "synchronize_with",
    ]:
        assert p in patterns, f"Pattern '{p}' missing from expected_patterns()"


# ---------------------------------------------------------------------------
# 3. BEHAVIOR_TEMPLATES — 7 entries, each matching a known pattern
# ---------------------------------------------------------------------------

def test_behavior_templates_has_7_entries():
    assert len(BEHAVIOR_TEMPLATES) == 7


def test_behavior_templates_keys_match_patterns():
    patterns = set(expected_patterns())
    for key in BEHAVIOR_TEMPLATES:
        assert key in patterns, f"Template key '{key}' not in expected_patterns()"


# ---------------------------------------------------------------------------
# 4. PATTERN_REQUIRED_PARAMS — 7 entries
# ---------------------------------------------------------------------------

def test_pattern_required_params_has_7_entries():
    assert len(PATTERN_REQUIRED_PARAMS) == 7


def test_pattern_required_params_keys_match_patterns():
    patterns = set(expected_patterns())
    for key in PATTERN_REQUIRED_PARAMS:
        assert key in patterns, f"Required-params key '{key}' not in expected_patterns()"


# ---------------------------------------------------------------------------
# 5. validate_config — clean config returns []
# ---------------------------------------------------------------------------

def test_validate_config_clean_returns_empty():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="MyPickPlace",
        pattern="pick_place",
        robot_prim_path="/World/Franka",
        end_effector_path="/World/Franka/panda_hand",
        params={"pick_pose": [0.4, 0, 0.5], "place_pose": [0.4, 0.3, 0.5]},
    )
    issues = gen.validate_config(cfg)
    assert issues == [], f"Expected no issues, got: {issues}"


# ---------------------------------------------------------------------------
# 6. validate_config — empty name → issue reported
# ---------------------------------------------------------------------------

def test_validate_config_empty_name_is_issue():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="",
        pattern="navigate_to",
        robot_prim_path="/World/Robot",
        params={"target_xy": [1.0, 2.0]},
    )
    issues = gen.validate_config(cfg)
    assert any("name" in i.lower() for i in issues), f"Expected name issue, got: {issues}"


# ---------------------------------------------------------------------------
# 7. validate_config — invalid identifier name "123x" → issue
# ---------------------------------------------------------------------------

def test_validate_config_invalid_identifier_is_issue():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="123x",
        pattern="navigate_to",
        robot_prim_path="/World/Robot",
        params={"target_xy": [1.0, 2.0]},
    )
    issues = gen.validate_config(cfg)
    assert any("identifier" in i.lower() or "name" in i.lower() for i in issues), (
        f"Expected identifier issue, got: {issues}"
    )


# ---------------------------------------------------------------------------
# 8. validate_config — unknown pattern → issue
# ---------------------------------------------------------------------------

def test_validate_config_unknown_pattern_is_issue():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="MyBehavior",
        pattern="teleport",  # not a known pattern
        robot_prim_path="/World/Robot",
        params={},
    )
    issues = gen.validate_config(cfg)
    assert any("pattern" in i.lower() or "unknown" in i.lower() for i in issues), (
        f"Expected pattern issue, got: {issues}"
    )


# ---------------------------------------------------------------------------
# 9. validate_config — missing required param → issue
# ---------------------------------------------------------------------------

def test_validate_config_missing_required_param_is_issue():
    gen = CreateBehaviorCodeGenerator()
    # pick_place needs pick_pose AND place_pose; omit place_pose
    cfg = BehaviorConfig(
        name="Picker",
        pattern="pick_place",
        robot_prim_path="/World/Franka",
        params={"pick_pose": [0, 0, 0]},  # missing place_pose
    )
    issues = gen.validate_config(cfg)
    assert any("place_pose" in i for i in issues), (
        f"Expected missing-param issue for 'place_pose', got: {issues}"
    )


# ---------------------------------------------------------------------------
# 10. generate(pick_place) returns string containing class or DfNetwork
# ---------------------------------------------------------------------------

def test_generate_pick_place_returns_class_or_dfnetwork():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="PickAndPlace",
        pattern="pick_place",
        robot_prim_path="/World/Franka",
        params={"pick_pose": [0.4, 0, 0.5], "place_pose": [0.4, 0.3, 0.5]},
    )
    code = gen.generate(cfg)
    assert isinstance(code, str)
    assert len(code) > 50
    assert ("class " in code) or ("DfNetwork" in code), (
        "Expected class definition or DfNetwork reference in generated code"
    )


# ---------------------------------------------------------------------------
# 11. generate ALWAYS includes `omni.isaac.cortex`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,params", [
    ("pick_place",       {"pick_pose": [0, 0, 0], "place_pose": [0, 0, 1]}),
    ("navigate_to",      {"target_xy": [1.0, 2.0]}),
    ("scan_grid",        {"grid_origin": [0, 0, 0], "grid_size": [2, 2], "grid_step": 0.5}),
    ("press_button",     {"button_path": "/World/Button"}),
    ("follow_path",      {"waypoints": [[0, 0, 0], [1, 0, 0]]}),
    ("guard_zone",       {"zone_bbox": [[0, 0, 0], [1, 1, 1]]}),
    ("synchronize_with", {"partner_robot_path": "/World/PartnerRobot"}),
])
def test_generate_always_includes_cortex_import(pattern, params):
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="TestBehavior",
        pattern=pattern,
        robot_prim_path="/World/Robot",
        params=params,
    )
    code = gen.generate(cfg)
    assert "omni.isaac.cortex" in code, (
        f"Pattern '{pattern}': generated code missing 'omni.isaac.cortex'"
    )


# ---------------------------------------------------------------------------
# 12. generate NEVER includes `MotionCommander('`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,params", [
    ("pick_place",       {"pick_pose": [0, 0, 0], "place_pose": [0, 0, 1]}),
    ("navigate_to",      {"target_xy": [1.0, 2.0]}),
    ("scan_grid",        {"grid_origin": [0, 0, 0], "grid_size": [2, 2], "grid_step": 0.5}),
    ("press_button",     {"button_path": "/World/Button"}),
    ("follow_path",      {"waypoints": [[0, 0, 0], [1, 0, 0]]}),
    ("guard_zone",       {"zone_bbox": [[0, 0, 0], [1, 1, 1]]}),
    ("synchronize_with", {"partner_robot_path": "/World/PartnerRobot"}),
])
def test_generate_never_includes_deprecated_motion_commander(pattern, params):
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="TestBehavior",
        pattern=pattern,
        robot_prim_path="/World/Robot",
        params=params,
    )
    code = gen.generate(cfg)
    assert "MotionCommander('" not in code, (
        f"Pattern '{pattern}': generated code contains deprecated MotionCommander('/path') pattern"
    )


# ---------------------------------------------------------------------------
# 13. generate NEVER includes `CortexRobot(..., motion_commander=`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,params", [
    ("pick_place",       {"pick_pose": [0, 0, 0], "place_pose": [0, 0, 1]}),
    ("navigate_to",      {"target_xy": [1.0, 2.0]}),
    ("guard_zone",       {"zone_bbox": [[0, 0, 0], [1, 1, 1]]}),
    ("synchronize_with", {"partner_robot_path": "/World/PartnerRobot"}),
])
def test_generate_never_includes_deprecated_cortex_robot_kwarg(pattern, params):
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="TestBehavior",
        pattern=pattern,
        robot_prim_path="/World/Robot",
        params=params,
    )
    code = gen.generate(cfg)
    assert not ("CortexRobot(" in code and "motion_commander=" in code), (
        f"Pattern '{pattern}': generated code contains deprecated "
        "CortexRobot(..., motion_commander=...) pattern"
    )


# ---------------------------------------------------------------------------
# 14. validate_generated catches deprecated MotionCommander
# ---------------------------------------------------------------------------

def test_validate_generated_catches_deprecated_motion_commander():
    gen = CreateBehaviorCodeGenerator()
    bad_code = (
        "import omni.isaac.cortex\n"
        "mc = MotionCommander('/World/Franka')\n"
        "def run(): pass\n"
    )
    issues = gen.validate_generated(bad_code)
    assert any("MotionCommander" in i for i in issues), (
        f"Expected MotionCommander issue, got: {issues}"
    )


# ---------------------------------------------------------------------------
# 15. validate_generated catches missing cortex import
# ---------------------------------------------------------------------------

def test_validate_generated_catches_missing_cortex_import():
    gen = CreateBehaviorCodeGenerator()
    bad_code = (
        "import numpy as np\n"
        "class MyBehavior:\n"
        "    def step(self): pass\n"
    )
    issues = gen.validate_generated(bad_code)
    assert any("omni.isaac.cortex" in i for i in issues), (
        f"Expected cortex import issue, got: {issues}"
    )


# ---------------------------------------------------------------------------
# 16. validate_generated clean code → []
# ---------------------------------------------------------------------------

def test_validate_generated_clean_code_returns_empty():
    gen = CreateBehaviorCodeGenerator()
    cfg = BehaviorConfig(
        name="CleanBehavior",
        pattern="navigate_to",
        robot_prim_path="/World/Robot",
        params={"target_xy": [3.0, 4.0]},
    )
    code = gen.generate(cfg)
    issues = gen.validate_generated(code)
    assert issues == [], f"Clean generated code should have no issues, got: {issues}"
