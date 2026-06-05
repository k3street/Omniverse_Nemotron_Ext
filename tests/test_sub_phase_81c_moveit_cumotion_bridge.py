"""Phase 81c — MoveItCuMotionBridge test suite.

Covers:
- Phase metadata contract (phase id / title / status / spec_ref)
- BridgeConfig defaults are sane
- validate_config: clean config → empty issue list
- validate_config: empty planning_group → issue reported
- validate_config: planning_time_s <= 0 → issue reported
- validate_config: max_velocity_scale > 1 → issue reported
- validate_config: max_velocity_scale <= 0 → issue reported
- validate_config: max_acceleration_scale > 1 → issue reported
- validate_config: num_planning_attempts <= 0 → issue reported
- plan_to_joint dry-run → success + 3 waypoints
- plan_to_pose dry-run → success + 3 waypoints
- plan_to_joint live mode (dry_run=False) → NotImplementedError
- plan_to_pose live mode (dry_run=False) → NotImplementedError
- execute dry-run → success dict with executed_points
- stop dry-run → success dict
- PLANNER_CAPABILITIES has exactly 4 entries
- PLANNER_CAPABILITIES keys match PlannerBackend values
- detect_planner_for_task("fast_pick_place", False) → cumotion
- detect_planner_for_task("complex_assembly", True) → curobo_v2
- detect_planner_for_task("linear_motion", False) → moveit_pilz
- JointGoal dataclass round-trips correctly
- PoseGoal dataclass round-trips correctly
- TrajectoryPoint default velocities is empty list
- PlanResult carries planner_used field
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.sub_phase_81c_moveit_cumotion_bridge import (
    PHASE_STATUS,
    PLANNER_CAPABILITIES,
    BridgeConfig,
    JointGoal,
    MoveItCuMotionBridge,
    PlanResult,
    PoseGoal,
    TrajectoryPoint,
    detect_planner_for_task,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_bridge(dry_run: bool = True) -> MoveItCuMotionBridge:
    return MoveItCuMotionBridge(BridgeConfig(), dry_run=dry_run)


def _joint_goal(n: int = 6) -> JointGoal:
    names = [f"joint_{i}" for i in range(n)]
    positions = [0.0] * n
    return JointGoal(joint_names=names, positions=positions)


def _pose_goal() -> PoseGoal:
    return PoseGoal(
        frame_id="world",
        position=(0.5, 0.0, 0.4),
        orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
    )


# ---------------------------------------------------------------------------
# Test group 1 — Metadata contract
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_id(self):
        md = get_phase_metadata()
        assert md["phase"] == "81c"

    def test_status_landed(self):
        md = get_phase_metadata()
        assert md["status"] == "landed"
        assert PHASE_STATUS == "landed"

    def test_title_non_empty(self):
        md = get_phase_metadata()
        assert md.get("title", "") != ""

    def test_spec_ref_present(self):
        md = get_phase_metadata()
        assert "spec_ref" in md and "81c" in md["spec_ref"]


# ---------------------------------------------------------------------------
# Test group 2 — BridgeConfig defaults
# ---------------------------------------------------------------------------

class TestBridgeConfigDefaults:
    def test_ros_domain_id_default(self):
        cfg = BridgeConfig()
        assert cfg.ros_domain_id == 0

    def test_planning_group_default(self):
        cfg = BridgeConfig()
        assert cfg.planning_group == "manipulator"

    def test_planner_default(self):
        cfg = BridgeConfig()
        assert cfg.planner == "cumotion"

    def test_num_planning_attempts_default(self):
        cfg = BridgeConfig()
        assert cfg.num_planning_attempts == 10

    def test_planning_time_default(self):
        cfg = BridgeConfig()
        assert cfg.planning_time_s == 1.0

    def test_velocity_scale_default(self):
        cfg = BridgeConfig()
        assert cfg.max_velocity_scale == 1.0

    def test_acceleration_scale_default(self):
        cfg = BridgeConfig()
        assert cfg.max_acceleration_scale == 1.0

    def test_collision_check_enabled_default(self):
        cfg = BridgeConfig()
        assert cfg.collision_check_enabled is True


# ---------------------------------------------------------------------------
# Test group 3 — validate_config
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def test_clean_config_returns_empty_list(self):
        bridge = _default_bridge()
        issues = bridge.validate_config()
        assert issues == []

    def test_empty_planning_group_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(planning_group=""))
        issues = bridge.validate_config()
        assert len(issues) >= 1
        assert any("planning_group" in i for i in issues)

    def test_whitespace_planning_group_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(planning_group="   "))
        issues = bridge.validate_config()
        assert any("planning_group" in i for i in issues)

    def test_planning_time_zero_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(planning_time_s=0.0))
        issues = bridge.validate_config()
        assert any("planning_time_s" in i for i in issues)

    def test_planning_time_negative_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(planning_time_s=-1.0))
        issues = bridge.validate_config()
        assert any("planning_time_s" in i for i in issues)

    def test_velocity_scale_above_one_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(max_velocity_scale=1.01))
        issues = bridge.validate_config()
        assert any("max_velocity_scale" in i for i in issues)

    def test_velocity_scale_zero_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(max_velocity_scale=0.0))
        issues = bridge.validate_config()
        assert any("max_velocity_scale" in i for i in issues)

    def test_velocity_scale_negative_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(max_velocity_scale=-0.5))
        issues = bridge.validate_config()
        assert any("max_velocity_scale" in i for i in issues)

    def test_acceleration_scale_above_one_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(max_acceleration_scale=2.0))
        issues = bridge.validate_config()
        assert any("max_acceleration_scale" in i for i in issues)

    def test_num_attempts_zero_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(num_planning_attempts=0))
        issues = bridge.validate_config()
        assert any("num_planning_attempts" in i for i in issues)

    def test_num_attempts_negative_is_issue(self):
        bridge = MoveItCuMotionBridge(BridgeConfig(num_planning_attempts=-3))
        issues = bridge.validate_config()
        assert any("num_planning_attempts" in i for i in issues)

    def test_multiple_bad_fields_returns_multiple_issues(self):
        bridge = MoveItCuMotionBridge(
            BridgeConfig(planning_group="", planning_time_s=-1.0, num_planning_attempts=0)
        )
        issues = bridge.validate_config()
        assert len(issues) >= 3


# ---------------------------------------------------------------------------
# Test group 4 — plan_to_joint
# ---------------------------------------------------------------------------

class TestPlanToJoint:
    def test_dry_run_returns_success(self):
        bridge = _default_bridge()
        result = bridge.plan_to_joint(_joint_goal())
        assert result.success is True

    def test_dry_run_returns_3_waypoints(self):
        bridge = _default_bridge()
        result = bridge.plan_to_joint(_joint_goal())
        assert len(result.trajectory) == 3

    def test_dry_run_trajectory_points_are_TrajectoryPoint(self):
        bridge = _default_bridge()
        result = bridge.plan_to_joint(_joint_goal(n=6))
        for pt in result.trajectory:
            assert isinstance(pt, TrajectoryPoint)

    def test_dry_run_positions_length_matches_joints(self):
        bridge = _default_bridge()
        goal = _joint_goal(n=4)
        result = bridge.plan_to_joint(goal)
        for pt in result.trajectory:
            assert len(pt.positions) == 4

    def test_dry_run_planner_used_matches_config(self):
        cfg = BridgeConfig(planner="moveit_ompl")
        bridge = MoveItCuMotionBridge(cfg)
        result = bridge.plan_to_joint(_joint_goal())
        assert result.planner_used == "moveit_ompl"

    def test_live_mode_raises_not_implemented(self):
        bridge = _default_bridge(dry_run=False)
        with pytest.raises(NotImplementedError):
            bridge.plan_to_joint(_joint_goal())

    def test_dry_run_no_error_field(self):
        bridge = _default_bridge()
        result = bridge.plan_to_joint(_joint_goal())
        assert result.error is None


# ---------------------------------------------------------------------------
# Test group 5 — plan_to_pose
# ---------------------------------------------------------------------------

class TestPlanToPose:
    def test_dry_run_returns_success(self):
        bridge = _default_bridge()
        result = bridge.plan_to_pose(_pose_goal())
        assert result.success is True

    def test_dry_run_returns_waypoints(self):
        bridge = _default_bridge()
        result = bridge.plan_to_pose(_pose_goal())
        assert len(result.trajectory) >= 1

    def test_dry_run_waypoints_have_positions(self):
        bridge = _default_bridge()
        result = bridge.plan_to_pose(_pose_goal())
        for pt in result.trajectory:
            assert isinstance(pt.positions, list)
            assert len(pt.positions) > 0

    def test_live_mode_raises_not_implemented(self):
        bridge = _default_bridge(dry_run=False)
        with pytest.raises(NotImplementedError):
            bridge.plan_to_pose(_pose_goal())

    def test_dry_run_planner_used_field(self):
        bridge = _default_bridge()
        result = bridge.plan_to_pose(_pose_goal())
        assert result.planner_used == "cumotion"


# ---------------------------------------------------------------------------
# Test group 6 — execute
# ---------------------------------------------------------------------------

class TestExecute:
    def test_dry_run_returns_success(self):
        bridge = _default_bridge()
        plan = bridge.plan_to_joint(_joint_goal())
        result = bridge.execute(plan)
        assert result["success"] is True

    def test_dry_run_executed_points_matches_trajectory_length(self):
        bridge = _default_bridge()
        plan = bridge.plan_to_joint(_joint_goal())
        result = bridge.execute(plan)
        assert result["executed_points"] == len(plan.trajectory)

    def test_dry_run_final_position_present(self):
        bridge = _default_bridge()
        plan = bridge.plan_to_joint(_joint_goal(n=5))
        result = bridge.execute(plan)
        assert "final_position" in result
        assert len(result["final_position"]) == 5

    def test_dry_run_planner_used_in_result(self):
        bridge = _default_bridge()
        plan = bridge.plan_to_joint(_joint_goal())
        result = bridge.execute(plan)
        assert "planner_used" in result


# ---------------------------------------------------------------------------
# Test group 7 — stop
# ---------------------------------------------------------------------------

class TestStop:
    def test_dry_run_returns_success(self):
        bridge = _default_bridge()
        result = bridge.stop()
        assert result["success"] is True

    def test_stop_returns_dict(self):
        bridge = _default_bridge()
        result = bridge.stop()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test group 8 — PLANNER_CAPABILITIES registry
# ---------------------------------------------------------------------------

class TestPlannerCapabilities:
    def test_has_exactly_4_entries(self):
        assert len(PLANNER_CAPABILITIES) == 4

    def test_all_four_backends_present(self):
        expected = {"moveit_pilz", "moveit_ompl", "cumotion", "curobo_v2"}
        assert set(PLANNER_CAPABILITIES.keys()) == expected

    def test_each_entry_has_required_keys(self):
        required = {
            "supports_collision",
            "supports_curobo_obstacles",
            "supports_orientation_constraint",
            "recommended_for",
        }
        for backend, caps in PLANNER_CAPABILITIES.items():
            missing = required - set(caps.keys())
            assert not missing, f"{backend} missing keys: {missing}"

    def test_recommended_for_is_non_empty_list(self):
        for backend, caps in PLANNER_CAPABILITIES.items():
            assert isinstance(caps["recommended_for"], list)
            assert len(caps["recommended_for"]) >= 1, f"{backend} has empty recommended_for"

    def test_cumotion_supports_curobo_obstacles(self):
        assert PLANNER_CAPABILITIES["cumotion"]["supports_curobo_obstacles"] is True

    def test_curobo_v2_supports_curobo_obstacles(self):
        assert PLANNER_CAPABILITIES["curobo_v2"]["supports_curobo_obstacles"] is True

    def test_moveit_pilz_does_not_support_curobo_obstacles(self):
        assert PLANNER_CAPABILITIES["moveit_pilz"]["supports_curobo_obstacles"] is False


# ---------------------------------------------------------------------------
# Test group 9 — detect_planner_for_task
# ---------------------------------------------------------------------------

class TestDetectPlannerForTask:
    def test_fast_pick_place_no_obstacles(self):
        assert detect_planner_for_task("fast_pick_place", has_obstacles=False) == "cumotion"

    def test_fast_pick_place_with_obstacles(self):
        # Even with obstacles, fast_pick_place should prefer cumotion
        assert detect_planner_for_task("fast_pick_place", has_obstacles=True) == "cumotion"

    def test_complex_assembly_with_obstacles(self):
        assert detect_planner_for_task("complex_assembly", has_obstacles=True) == "curobo_v2"

    def test_complex_assembly_no_obstacles(self):
        assert detect_planner_for_task("complex_assembly", has_obstacles=False) == "curobo_v2"

    def test_linear_motion(self):
        assert detect_planner_for_task("linear_motion", has_obstacles=False) == "moveit_pilz"

    def test_unknown_task_with_obstacles(self):
        result = detect_planner_for_task("some_unknown_task", has_obstacles=True)
        assert result == "curobo_v2"

    def test_unknown_task_no_obstacles(self):
        result = detect_planner_for_task("some_unknown_task", has_obstacles=False)
        assert result == "cumotion"


# ---------------------------------------------------------------------------
# Test group 10 — dataclass round-trips
# ---------------------------------------------------------------------------

class TestDataclassRoundTrips:
    def test_joint_goal_fields(self):
        goal = JointGoal(
            joint_names=["shoulder", "elbow"],
            positions=[0.1, -0.2],
            tolerance=0.002,
        )
        assert goal.joint_names == ["shoulder", "elbow"]
        assert goal.positions == [0.1, -0.2]
        assert goal.tolerance == 0.002

    def test_joint_goal_default_tolerance(self):
        goal = JointGoal(joint_names=["j0"], positions=[0.0])
        assert goal.tolerance == 0.001

    def test_pose_goal_fields(self):
        goal = PoseGoal(
            frame_id="base_link",
            position=(1.0, 2.0, 3.0),
            orientation_xyzw=(0.0, 0.0, 0.707, 0.707),
        )
        assert goal.frame_id == "base_link"
        assert goal.position == (1.0, 2.0, 3.0)
        assert goal.orientation_xyzw == (0.0, 0.0, 0.707, 0.707)

    def test_pose_goal_default_tolerances(self):
        goal = _pose_goal()
        assert goal.position_tolerance_m == 0.005
        assert goal.orientation_tolerance_rad == 0.01

    def test_trajectory_point_default_velocities(self):
        pt = TrajectoryPoint(time_from_start_s=0.5, positions=[0.1, 0.2])
        assert pt.velocities == []

    def test_trajectory_point_with_velocities(self):
        pt = TrajectoryPoint(
            time_from_start_s=1.0,
            positions=[0.0, 0.1],
            velocities=[0.05, 0.03],
        )
        assert len(pt.velocities) == 2

    def test_plan_result_fields(self):
        traj = [TrajectoryPoint(time_from_start_s=0.0, positions=[0.0])]
        result = PlanResult(
            success=True,
            trajectory=traj,
            planning_time_s=0.1,
            planner_used="cumotion",
        )
        assert result.success is True
        assert result.planner_used == "cumotion"
        assert result.error is None
