"""Phase 70 — assemble_robot implementation.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 70.

Gate: assembles 3-piece arm + base + gripper from typed config; output
is a prim hierarchy + joint list that the Kit RPC layer would author.
"""
from __future__ import annotations

import pytest

from service.isaac_assist_service.multimodal.assemble_robot import (
    AssemblySpec,
    CHILD_ORDER_RULES,
    KNOWN_ATTACH_POINTS,
    RobotPart,
    _safe_prim_segment,
    assemble,
    get_phase_metadata,
    make_demo_franka_with_robotiq,
    make_demo_three_link_arm,
    validate_attachment,
    validate_part,
)

pytestmark = pytest.mark.l0


def test_phase_70_metadata():
    md = get_phase_metadata()
    assert md["phase"] == 70
    assert md["status"] == "landed"


def test_validate_part_clean_part_returns_no_issues():
    part = RobotPart(
        name="link1",
        category="arm_link",
        asset_ref="omni://x.usd",
        parent_attach_point="flange",
        self_attach_point="base",
        joint_type="revolute",
        joint_axis="Z",
        joint_lower_limit=-1.0,
        joint_upper_limit=1.0,
    )
    assert validate_part(part) == []


def test_validate_part_empty_name():
    part = RobotPart(name="", category="arm_link", asset_ref="x", parent_attach_point="flange")
    issues = validate_part(part)
    assert any("name is empty" in i for i in issues)


def test_validate_part_empty_asset_ref():
    part = RobotPart(name="x", category="arm_link", asset_ref="", parent_attach_point="flange")
    issues = validate_part(part)
    assert any("asset_ref is empty" in i for i in issues)


def test_validate_part_revolute_without_axis():
    part = RobotPart(
        name="x", category="arm_link", asset_ref="a",
        parent_attach_point="flange", joint_type="revolute"
    )
    issues = validate_part(part)
    assert any("joint_axis" in i for i in issues)


def test_validate_part_bad_axis():
    part = RobotPart(
        name="x", category="arm_link", asset_ref="a",
        parent_attach_point="flange", joint_type="revolute",
        joint_axis="W",  # type: ignore[arg-type]
    )
    issues = validate_part(part)
    assert any("X/Y/Z" in i for i in issues)


def test_validate_part_inverted_joint_limits():
    part = RobotPart(
        name="x", category="arm_link", asset_ref="a",
        parent_attach_point="flange", joint_type="revolute", joint_axis="Z",
        joint_lower_limit=1.0, joint_upper_limit=-1.0,
    )
    issues = validate_part(part)
    assert any("limits inverted" in i for i in issues)


def test_validate_part_non_positive_mass():
    part = RobotPart(
        name="x", category="arm_link", asset_ref="a",
        parent_attach_point="flange", mass_kg=0.0,
    )
    issues = validate_part(part)
    assert any("mass_kg" in i for i in issues)


def test_validate_attachment_legal_base_to_arm():
    child = RobotPart(name="link1", category="arm_link", asset_ref="a",
                      parent_attach_point="base_link", self_attach_point="base")
    issues = validate_attachment("base", child)
    assert not any(i.startswith("WARN: unusual attachment") for i in issues)


def test_validate_attachment_unusual_pair_warns():
    child = RobotPart(name="g", category="gripper", asset_ref="a",
                      parent_attach_point="base_link", self_attach_point="base")
    issues = validate_attachment("base", child)
    assert any(i.startswith("WARN: unusual attachment") for i in issues)


def test_validate_attachment_unknown_attach_point_warns():
    child = RobotPart(name="link1", category="arm_link", asset_ref="a",
                      parent_attach_point="nonexistent_thing", self_attach_point="base")
    issues = validate_attachment("base", child)
    assert any("parent_attach_point" in i and "WARN" in i for i in issues)


def test_assemble_minimal_base_only():
    base = RobotPart(name="base", category="base", asset_ref="a",
                     parent_attach_point="world", self_attach_point="base_link")
    spec = AssemblySpec(robot_name="r", base_part=base)
    result = assemble(spec)
    assert result.success
    assert result.prim_paths == ["/World/Robot/base"]
    assert len(result.joints) == 1
    assert result.joints[0].name == "world_to_base"
    assert result.articulation_root == "/World/Robot/base"


def test_assemble_three_link_arm_with_gripper_is_the_gate():
    """Spec gate: assembles 3-piece arm + base + gripper from typed config."""
    spec = make_demo_three_link_arm()
    result = assemble(spec)
    assert result.success, f"unexpected issues: {result.issues}"
    assert len(result.prim_paths) == 5
    assert len(result.joints) == 5
    assert result.end_effector_path is not None
    assert "gripper" in result.end_effector_path
    assert result.articulation_root.endswith("/arm_base")
    revolute_joints = [j for j in result.joints if j.joint_type == "revolute"]
    assert len(revolute_joints) == 3
    for j in revolute_joints:
        assert j.axis in ("X", "Y", "Z")


def test_assemble_franka_with_robotiq_attaches_gripper_to_arm_tip():
    spec = make_demo_franka_with_robotiq()
    result = assemble(spec)
    assert result.success, f"issues: {result.issues}"
    gripper_path = result.end_effector_path
    assert gripper_path is not None
    assert gripper_path.endswith("/robotiq_2f85")
    assert "panda_link8" in gripper_path


def test_assemble_duplicate_part_names_flagged():
    base = RobotPart(name="b", category="base", asset_ref="a", parent_attach_point="world")
    dup1 = RobotPart(name="x", category="arm_link", asset_ref="a",
                     parent_attach_point="base_link", joint_type="revolute", joint_axis="Z")
    dup2 = RobotPart(name="x", category="arm_link", asset_ref="a",
                     parent_attach_point="flange", joint_type="revolute", joint_axis="Z")
    spec = AssemblySpec(robot_name="r", base_part=base, children=[dup1, dup2])
    result = assemble(spec)
    assert any("duplicate part name" in i for i in result.issues)
    assert not result.success


def test_assemble_chain_each_arm_link_attaches_to_previous():
    spec = make_demo_three_link_arm()
    result = assemble(spec)
    paths = result.prim_paths
    assert any(p.endswith("/arm_base/link_1") for p in paths)
    assert any(p.endswith("/arm_base/link_1/link_2") for p in paths)
    assert any(p.endswith("/arm_base/link_1/link_2/link_3") for p in paths)


def test_assemble_references_one_per_prim():
    spec = make_demo_three_link_arm()
    result = assemble(spec)
    assert len(result.references) == len(result.prim_paths)
    for prim_path, asset_ref in result.references:
        assert prim_path in result.prim_paths
        assert asset_ref


def test_assemble_bad_robot_prim_path():
    base = RobotPart(name="b", category="base", asset_ref="a", parent_attach_point="world")
    spec = AssemblySpec(robot_name="r", base_part=base, robot_prim_path="not_absolute")
    result = assemble(spec)
    assert any("must start with /" in i for i in result.issues)
    assert not result.success


def test_assemble_empty_robot_name_flagged():
    base = RobotPart(name="b", category="base", asset_ref="a", parent_attach_point="world")
    spec = AssemblySpec(robot_name="", base_part=base)
    result = assemble(spec)
    assert any("robot_name is empty" in i for i in result.issues)


def test_assemble_wrong_base_category_flagged():
    not_base = RobotPart(name="b", category="arm_link", asset_ref="a", parent_attach_point="flange")
    spec = AssemblySpec(robot_name="r", base_part=not_base)
    result = assemble(spec)
    assert any("base_part category must be 'base'" in i for i in result.issues)


def test_safe_prim_segment_strips_unsafe_chars():
    assert _safe_prim_segment("foo bar") == "foo_bar"
    assert _safe_prim_segment("foo-bar.baz") == "foo_bar_baz"
    assert _safe_prim_segment("123x") == "_123x"
    assert _safe_prim_segment("") == "Part"


def test_assembly_result_success_warns_dont_block():
    spec = make_demo_franka_with_robotiq()
    result = assemble(spec)
    if result.issues:
        assert all(i.startswith("WARN:") for i in result.issues), \
            f"unexpected non-WARN issues: {result.issues}"
    assert result.success


def test_known_attach_points_registry_complete():
    assert "base" in KNOWN_ATTACH_POINTS
    assert "arm_link" in KNOWN_ATTACH_POINTS
    assert "gripper" in KNOWN_ATTACH_POINTS
    assert "sensor" in KNOWN_ATTACH_POINTS
    assert "end_effector_extension" in KNOWN_ATTACH_POINTS


def test_child_order_rules_includes_base_to_arm():
    assert ("base", "arm_link") in CHILD_ORDER_RULES
    assert ("arm_link", "gripper") in CHILD_ORDER_RULES
    assert ("arm_link", "arm_link") in CHILD_ORDER_RULES


def test_assemble_semantic_tags_attached_to_prim_paths():
    spec = make_demo_franka_with_robotiq()
    result = assemble(spec)
    assert result.semantic_labels
    base_path = result.prim_paths[0]
    assert base_path in result.semantic_labels
    assert "robot" in result.semantic_labels[base_path]
