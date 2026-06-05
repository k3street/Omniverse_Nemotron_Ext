"""Phase 63c — Per-robot-family cuRobo debugging protocol.

Provides a structured debug checklist, failure classifier, and fixup recipe
registry for cuRobo motion-planning failures, organised by robot family.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63c.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "63c"
PHASE_TITLE = "Per-robot-family cuRobo debugging protocol"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63c",
        "robot_families": list(ROBOT_FAMILIES),
        "failure_modes": list(FAILURE_MODES),
    }


# ---------------------------------------------------------------------------
# Type aliases / Literals
# ---------------------------------------------------------------------------

RobotFamily = Literal[
    "franka",
    "ur",
    "yaskawa",
    "abb",
    "kuka",
    "fanuc",
    "humanoid_g1",
    "humanoid_h1",
    "mobile_base",
]

CuRoboFailureMode = Literal[
    "no_plan_found",
    "scene_collision_phantom",
    "joint_limit_violation",
    "ik_singularity",
    "obstacle_inflation_too_aggressive",
    "warp_kernel_oom",
    "self_collision_at_start",
    "goal_unreachable",
]

ROBOT_FAMILIES: tuple[str, ...] = (
    "franka",
    "ur",
    "yaskawa",
    "abb",
    "kuka",
    "fanuc",
    "humanoid_g1",
    "humanoid_h1",
    "mobile_base",
)

FAILURE_MODES: tuple[str, ...] = (
    "no_plan_found",
    "scene_collision_phantom",
    "joint_limit_violation",
    "ik_singularity",
    "obstacle_inflation_too_aggressive",
    "warp_kernel_oom",
    "self_collision_at_start",
    "goal_unreachable",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DebugCheck:
    """A single cuRobo debug verification check for a robot family."""
    check_id: str
    robot_family: Optional[str]  # None = universal (applies to all families)
    description: str
    expected_value: str
    how_to_inspect: str
    severity: Literal["info", "warn", "error"] = "info"


@dataclass
class FixupRecipe:
    """Step-by-step remediation recipe for a cuRobo failure mode."""
    failure_mode: str  # CuRoboFailureMode value
    recipe_id: str
    steps: List[str]
    estimated_minutes: int
    success_indicator: str


# ---------------------------------------------------------------------------
# Universal checks (robot_family=None) — returned for every family
# ---------------------------------------------------------------------------

_UNIVERSAL_CHECKS: List[DebugCheck] = [
    DebugCheck(
        check_id="scene_obstacles_loaded",
        robot_family=None,
        description="Verify that all scene collision meshes are registered in the cuRobo WorldConfig before planning.",
        expected_value="world_model.collision_mesh_list is non-empty and matches stage prims",
        how_to_inspect=(
            "Call world_config.get_mesh_names() and compare against "
            "stage.Traverse() prims that have PhysicsCollisionAPI applied."
        ),
        severity="error",
    ),
    DebugCheck(
        check_id="joint_limits_match_urdf",
        robot_family=None,
        description="Confirm cuRobo robot config joint limits agree with the URDF / USD articulation limits.",
        expected_value="Max delta between cuRobo limits and URDF limits < 0.01 rad for each joint",
        how_to_inspect=(
            "Parse robot_config['kinematics']['joint_limit'] and compare against "
            "ArticulationView.get_dof_limits() for the staged robot."
        ),
        severity="error",
    ),
    DebugCheck(
        check_id="tcp_frame_set_correctly",
        robot_family=None,
        description="Check that the tool-centre-point (TCP) frame in cuRobo matches the physical end-effector.",
        expected_value="TCP offset translation error < 1 mm; no unexpected rotation offset",
        how_to_inspect=(
            "Compare robot_config['kinematics']['ee_link'] T matrix with "
            "the USD Xform of the mounted end-effector root prim."
        ),
        severity="warn",
    ),
    DebugCheck(
        check_id="cuda_graph_disabled_with_obstacles",
        robot_family=None,
        description=(
            "When scene-collision (TSDF / mesh) is enabled, cuda_graph must be False "
            "to avoid kernel launch errors."
        ),
        expected_value="motion_gen_config.cuda_graph == False when scene_model is set",
        how_to_inspect=(
            "Inspect MotionGenConfig instantiation kwargs; if scene_model is not None, "
            "assert cuda_graph=False is passed."
        ),
        severity="error",
    ),
    DebugCheck(
        check_id="warp_version_compatible",
        robot_family=None,
        description="Warp version must be ≥ 1.8.2 for cuRobo scene-collision primitives to function.",
        expected_value="warp.__version__ >= '1.8.2'",
        how_to_inspect="import warp; print(warp.__version__)",
        severity="warn",
    ),
    DebugCheck(
        check_id="interpolation_dt_matches_sim_step",
        robot_family=None,
        description="cuRobo trajectory interpolation dt should match IsaacSim physics dt to avoid step-count mismatches.",
        expected_value="motion_gen_config.interpolation_dt ≈ physics_scene.timestep (default 1/60 s)",
        how_to_inspect=(
            "Print motion_gen_config.interpolation_dt and compare with "
            "UsdPhysics.Scene.Get(stage, '/physicsScene').GetTimeStepsPerSecondAttr().Get()."
        ),
        severity="warn",
    ),
]


# ---------------------------------------------------------------------------
# Family-specific checks
# ---------------------------------------------------------------------------

_FAMILY_CHECKS: Dict[str, List[DebugCheck]] = {
    "franka": [
        DebugCheck(
            check_id="franka_panda_link8_offset",
            robot_family="franka",
            description=(
                "Franka Panda link8 has a fixed 0.107 m offset that must be included "
                "in the cuRobo kinematics YAML; omitting it shifts the TCP by ~10 cm."
            ),
            expected_value="kinematics.yaml: link8 z-offset = 0.107",
            how_to_inspect="Open franka.yml kinematics section and verify link8 fixed-joint z translation.",
            severity="error",
        ),
        DebugCheck(
            check_id="franka_gripper_collision_spheres",
            robot_family="franka",
            description="Panda finger collision spheres must cover both finger links to avoid phantom collisions.",
            expected_value="self_collision_buffer['panda_finger_joint1'] and ['panda_finger_joint2'] both present",
            how_to_inspect="Inspect robot_config['self_collision_ignore'] and sphere definitions in franka.yml.",
            severity="warn",
        ),
        DebugCheck(
            check_id="franka_velocity_limits_scaled",
            robot_family="franka",
            description=(
                "cuRobo uses velocity-limit fractions; Franka factory limits are 2.175 rad/s for joint 1 — "
                "ensure the YAML does not use absolute SI values."
            ),
            expected_value="velocity_scale <= 1.0 in motion_gen_config",
            how_to_inspect="Check MotionGenConfig(velocity_scale=...) arg; factory default should be ≤ 0.8.",
            severity="warn",
        ),
    ],
    "ur": [
        DebugCheck(
            check_id="ur_dh_params_use_kinematics_yaml",
            robot_family="ur",
            description=(
                "UR robots have DH-parameter variants per model (UR3e/UR5e/UR10e); "
                "using the wrong YAML causes systematic IK offset errors."
            ),
            expected_value="kinematics YAML filename matches robot model (e.g., ur10e.yml for UR10e)",
            how_to_inspect="Print robot_config['kinematics']['urdf_path'] and verify model suffix.",
            severity="error",
        ),
        DebugCheck(
            check_id="ur_base_frame_z_up",
            robot_family="ur",
            description="UR USD assets imported via UR_SDK may have Y-up base; cuRobo expects Z-up.",
            expected_value="Robot base prim WorldRotation is identity (no 90° X rotation)",
            how_to_inspect="Check get_world_transform() on the robot base prim for unexpected X-axis rotation.",
            severity="error",
        ),
        DebugCheck(
            check_id="ur_e_series_joint_offset",
            robot_family="ur",
            description="e-Series UR robots have a 180° wrist-3 joint offset compared to CB3; ensure YAML matches series.",
            expected_value="joint_6 (wrist_3) zero-position matches actual robot home config",
            how_to_inspect="Move robot to 0-rad config and compare FK output vs physical pose.",
            severity="warn",
        ),
    ],
    "yaskawa": [
        DebugCheck(
            check_id="yaskawa_motoman_axes_xyz_inverted",
            robot_family="yaskawa",
            description=(
                "Yaskawa Motoman USD exports often have X/Y axes inverted relative to "
                "the INFORM coordinate frame; cuRobo YAML must compensate."
            ),
            expected_value="base_link rotation in YAML applies 180° Y rotation to match INFORM frame",
            how_to_inspect="Compare FK of joint-zero config: cuRobo tool-frame Z should point up-and-forward.",
            severity="error",
        ),
        DebugCheck(
            check_id="yaskawa_s_axis_limit_asymmetric",
            robot_family="yaskawa",
            description="Yaskawa S-axis (joint 1) has asymmetric limits (+/-170° for HC10, +/-180° for GP12); verify per model.",
            expected_value="joint_1 limit matches controller parameter S1AX in controller backup",
            how_to_inspect="Query ArticulationView.get_dof_limits()[0] and compare with robot spec sheet.",
            severity="warn",
        ),
        DebugCheck(
            check_id="yaskawa_tool_number_zero",
            robot_family="yaskawa",
            description="cuRobo must plan with tool number 0 (no tool offset) unless a custom TCP transform is loaded.",
            expected_value="ee_link in kinematics YAML references flange frame when no tool is mounted",
            how_to_inspect="Inspect kinematics YAML ee_link field; 'tool0' or 'flange' are correct references.",
            severity="info",
        ),
    ],
    "abb": [
        DebugCheck(
            check_id="abb_wobj_in_world_frame",
            robot_family="abb",
            description=(
                "ABB work-objects (wobj) define task frames; cuRobo targets must be expressed "
                "in world frame, not wobj frame."
            ),
            expected_value="All goal poses passed to plan_single_js/plan_single are in world frame",
            how_to_inspect="Confirm no wobj transform is applied to target poses before passing to cuRobo.",
            severity="error",
        ),
        DebugCheck(
            check_id="abb_irb_singular_config_near_home",
            robot_family="abb",
            description="ABB IRB robots have a wrist singularity near the home configuration (joints 4 and 6 aligned).",
            expected_value="Home pose joint 5 != 0 rad (offset by at least 0.1 rad)",
            how_to_inspect="Check robot home config in YAML; joint 5 (B-axis) should be non-zero.",
            severity="warn",
        ),
        DebugCheck(
            check_id="abb_rapid_speed_scaled",
            robot_family="abb",
            description="ABB speed scaling in cuRobo must not exceed 100% to respect controller velocity limits.",
            expected_value="velocity_scale ≤ 1.0; acceleration_scale ≤ 1.0 in MotionGenConfig",
            how_to_inspect="Inspect MotionGenConfig(velocity_scale, acceleration_scale) kwargs.",
            severity="warn",
        ),
    ],
    "kuka": [
        DebugCheck(
            check_id="kuka_lbr_joint_impedance_mode",
            robot_family="kuka",
            description=(
                "KUKA LBR iiwa runs in joint impedance mode by default; "
                "cuRobo targets must avoid positions where impedance creates large residual forces."
            ),
            expected_value="Planned trajectory joint positions stay > 5° from joint limits",
            how_to_inspect="Post-process trajectory: assert no point is within 5° of any joint limit.",
            severity="warn",
        ),
        DebugCheck(
            check_id="kuka_sunrise_urdf_version_match",
            robot_family="kuka",
            description="KUKA Sunrise controller version determines exact DH parameters; cuRobo YAML must match firmware.",
            expected_value="kinematics YAML version tag matches controller Sunrise OS version",
            how_to_inspect="Read YAML header comment for version; compare with KRC controller info page.",
            severity="error",
        ),
        DebugCheck(
            check_id="kuka_eki_dt_100ms",
            robot_family="kuka",
            description="KUKA EKI interface has 100 ms cycle time; ensure cuRobo interpolation_dt is a multiple of 0.1 s.",
            expected_value="interpolation_dt ∈ {0.1, 0.2, 0.4, ...}",
            how_to_inspect="Print motion_gen_config.interpolation_dt; check modulo 0.1 == 0.",
            severity="info",
        ),
    ],
    "fanuc": [
        DebugCheck(
            check_id="fanuc_joint_axis_sign_convention",
            robot_family="fanuc",
            description=(
                "FANUC joints use vendor-specific positive-direction conventions "
                "that differ from ISO 9283; cuRobo YAML must apply correct sign flips."
            ),
            expected_value="All joint axes signs in YAML match vendor datasheet positive directions",
            how_to_inspect="Drive each joint +10° from TP pendant and verify URDF forward-kinematics agrees.",
            severity="error",
        ),
        DebugCheck(
            check_id="fanuc_r30ib_payload_inertia",
            robot_family="fanuc",
            description="FANUC R-30iB controller tracks end-effector payload inertia; mismatches cause cuRobo dynamics errors.",
            expected_value="tool mass + CoM in YAML matches value set in TP → SETUP → TOOL FRAME",
            how_to_inspect="Compare robot_config payload fields with TP screen TOOL FRAME payload registers.",
            severity="warn",
        ),
        DebugCheck(
            check_id="fanuc_uframe_transform",
            robot_family="fanuc",
            description="FANUC user frames (UFRAME) shift the task coordinate origin; targets must be world-frame before cuRobo.",
            expected_value="Goal poses transformed by inv(UFRAME) before planning",
            how_to_inspect="Log goal pose and verify it is expressed in robot-base frame, not UFRAME.",
            severity="warn",
        ),
    ],
    "humanoid_g1": [
        DebugCheck(
            check_id="g1_floating_base_excluded",
            robot_family="humanoid_g1",
            description="G1 floating base (6-DOF virtual joint) must be excluded from cuRobo arm planning DOF list.",
            expected_value="lock_joints in kinematics YAML lists all floating-base DOFs",
            how_to_inspect="Inspect kinematics YAML lock_joints; should include px, py, pz, rx, ry, rz of base.",
            severity="error",
        ),
        DebugCheck(
            check_id="g1_whole_body_collision_mask",
            robot_family="humanoid_g1",
            description=(
                "G1 legs must be added as self-collision primitives to prevent the arm "
                "planner from routing trajectories through the lower body."
            ),
            expected_value="self_collision_buffer contains leg link entries",
            how_to_inspect="Inspect robot_config['self_collision_ignore'] for absence of leg-link pairs.",
            severity="warn",
        ),
        DebugCheck(
            check_id="g1_arm_workspace_limit",
            robot_family="humanoid_g1",
            description="G1 arm workspace is constrained by torso width; goals outside ±0.6 m lateral from base are unreachable.",
            expected_value="Goal x-offset from base < 0.6 m laterally",
            how_to_inspect="Print goal pose; assert abs(goal.position.y) < 0.6.",
            severity="warn",
        ),
        DebugCheck(
            check_id="g1_elbow_retraction_seed",
            robot_family="humanoid_g1",
            description="IK seeds for G1 should prefer elbow-up configuration to avoid floor collisions.",
            expected_value="retract_config in kinematics YAML uses elbow-up seed angles",
            how_to_inspect="Inspect retract_config joint values; elbow joint should be positive (flexed).",
            severity="info",
        ),
    ],
    "humanoid_h1": [
        DebugCheck(
            check_id="h1_dual_arm_frame_offset",
            robot_family="humanoid_h1",
            description="H1 left and right arm base frames are offset ±0.2 m from body centre; YAML must encode per-arm offsets.",
            expected_value="base_link frame offsets ±0.2 m in Y for left/right arm YAML entries",
            how_to_inspect="Inspect h1_left.yml and h1_right.yml base_link transform sections.",
            severity="error",
        ),
        DebugCheck(
            check_id="h1_torso_pitch_compensation",
            robot_family="humanoid_h1",
            description="H1 torso pitches during walking; arm planning must account for current torso pitch when using fixed-base cuRobo.",
            expected_value="Base pose fed to cuRobo is updated with current torso pitch at plan-time",
            how_to_inspect="Log base_pose used in MotionGenPlanConfig; verify pitch != 0 during locomotion.",
            severity="warn",
        ),
        DebugCheck(
            check_id="h1_wrist_collision_spheres",
            robot_family="humanoid_h1",
            description="H1 wrist has limited clearance; sphere radius in collision model must be ≤ 0.04 m.",
            expected_value="Wrist collision sphere radius <= 0.04 m in robot YAML",
            how_to_inspect="Inspect robot YAML collision_sphere_buffer for wrist link entries.",
            severity="warn",
        ),
    ],
    "mobile_base": [
        DebugCheck(
            check_id="mobile_base_2d_nav_excluded_from_arm",
            robot_family="mobile_base",
            description=(
                "Mobile base drives (x, y, theta) must be locked during arm cuRobo planning "
                "to prevent the planner from using base motion to reach arm goals."
            ),
            expected_value="lock_joints includes base_x, base_y, base_theta (or equivalent) DOFs",
            how_to_inspect="Inspect kinematics YAML lock_joints; verify base translation/rotation DOFs listed.",
            severity="error",
        ),
        DebugCheck(
            check_id="mobile_base_arm_mount_transform",
            robot_family="mobile_base",
            description="Arm mount transform relative to base footprint must be exact; errors propagate to all IK solutions.",
            expected_value="Arm root prim WorldTransform matches robot spec arm-mount offset",
            how_to_inspect="Call get_world_transform('/World/Robot/arm_base') and verify against datasheet.",
            severity="error",
        ),
        DebugCheck(
            check_id="mobile_footprint_in_collision_model",
            robot_family="mobile_base",
            description="Mobile base footprint must be added as a static collision object to prevent arm plans passing through the chassis.",
            expected_value="WorldConfig contains mobile_base_footprint mesh or primitive",
            how_to_inspect="Call world_config.get_mesh_names() and check for footprint entry.",
            severity="warn",
        ),
        DebugCheck(
            check_id="mobile_nav_collision_clearance",
            robot_family="mobile_base",
            description="Navigation planner obstacle inflation should not over-inflate objects that the arm needs to reach near.",
            expected_value="obstacle inflation radius < 0.15 m for task-space objects",
            how_to_inspect="Inspect NavStack costmap inflation_radius param; compare with nearest approach distance.",
            severity="warn",
        ),
    ],
}

# Add remaining universal checks into every family
DEBUG_CHECKS_BY_FAMILY: Dict[str, List[DebugCheck]] = {
    family: _UNIVERSAL_CHECKS + _FAMILY_CHECKS.get(family, [])
    for family in ROBOT_FAMILIES
}


# ---------------------------------------------------------------------------
# Fixup recipes (≥1 per failure mode, ≥8 total)
# ---------------------------------------------------------------------------

FIXUP_RECIPES: List[FixupRecipe] = [
    FixupRecipe(
        failure_mode="no_plan_found",
        recipe_id="no_plan_found_relax_constraints",
        steps=[
            "Increase num_trajopt_seeds from default 4 to 8 in MotionGenConfig.",
            "Enable pose_cost_metric fallback: set pose_cost_metric='l2' in MotionGenPlanConfig.",
            "Reduce position_threshold from 1e-3 to 5e-3 m and rotation_threshold from 0.05 to 0.1 rad.",
            "Verify goal pose is within robot reachable workspace (check with show_workspace tool).",
            "If still failing, try a mid-way waypoint via plan_single_js to split the trajectory.",
        ],
        estimated_minutes=15,
        success_indicator="plan_single returns success=True with a non-empty trajectory",
    ),
    FixupRecipe(
        failure_mode="scene_collision_phantom",
        recipe_id="scene_collision_phantom_rebuild_world",
        steps=[
            "Call world_model.clear() and re-register all collision meshes from scratch.",
            "Verify no stale USD Mesh prims are included in the collision list (check for deleted/hidden prims).",
            "Set voxel_size to 0.02 (coarser) and observe if phantom disappears; if yes, reduce mesh resolution.",
            "Enable debug_draw to visualise cuRobo collision spheres overlaid on stage to identify mismatch.",
            "If using TSDF, increase tsdf_voxel_size to reduce sensor noise artefacts.",
        ],
        estimated_minutes=20,
        success_indicator="Planning succeeds without collision in free-space trajectory",
    ),
    FixupRecipe(
        failure_mode="joint_limit_violation",
        recipe_id="joint_limit_violation_sync_limits",
        steps=[
            "Read ArticulationView.get_dof_limits() for the staged robot and print all min/max values.",
            "Compare against kinematics YAML joint_limit entries; update YAML to match staged limits.",
            "Check if any joint was manually adjusted in USD (authoring overrides PhysicsRevoluteJoint limits).",
            "Re-instantiate MotionGen after YAML correction and replan.",
        ],
        estimated_minutes=10,
        success_indicator="No JointLimitViolation in trajectory validation; plan_single returns success=True",
    ),
    FixupRecipe(
        failure_mode="ik_singularity",
        recipe_id="ik_singularity_seed_diversification",
        steps=[
            "Add a non-singular retract_config in kinematics YAML (elbow-up seed, all joints != 0).",
            "Increase num_ik_seeds from 32 to 64 in MotionGenConfig.",
            "Enable null_space_weight to bias IK away from singular configurations.",
            "If goal is near a known singularity (e.g., fully-extended arm), offset goal by 1-2 cm and replan.",
            "Consider switching to a different IK solver: set ik_solver='warp_batch_env' for batched recovery.",
        ],
        estimated_minutes=15,
        success_indicator="IK converges in < 1 ms per seed; no NaN in Jacobian determinant",
    ),
    FixupRecipe(
        failure_mode="obstacle_inflation_too_aggressive",
        recipe_id="obstacle_inflation_reduce_buffer",
        steps=[
            "Reduce WorldConfig collision_activation_distance from default to 0.01 m.",
            "Decrease self_collision_buffer for links closest to obstacles (inspect debug_draw spheres).",
            "Check if collision buffer was set via apply_robot_fix_profile with an overly conservative preset.",
            "Recompute convex hull of obstacles to ensure they are not artificially enlarged.",
        ],
        estimated_minutes=10,
        success_indicator="Arm reaches within 2 cm of target obstacle without phantom collision rejection",
    ),
    FixupRecipe(
        failure_mode="warp_kernel_oom",
        recipe_id="warp_kernel_oom_reduce_batch",
        steps=[
            "Reduce num_trajopt_seeds * num_graph_seeds product; try seeds=(4, 4) instead of (8, 8).",
            "Lower trajectory interpolation steps: reduce max_attempts or use shorter horizon.",
            "Free unused Warp arrays by calling warp.ScopedTimer and identifying large allocations.",
            "Check GPU VRAM headroom via check_vram_headroom tool before planning; free Isaac Sim render buffers if low.",
            "Upgrade Warp to ≥ 1.11.0 (improved memory management) if currently on ≤ 1.8.x.",
        ],
        estimated_minutes=20,
        success_indicator="Planning completes without CUDA OOM error; warp peak allocation < 80% VRAM",
    ),
    FixupRecipe(
        failure_mode="self_collision_at_start",
        recipe_id="self_collision_at_start_jog_to_clear",
        steps=[
            "Use get_joint_positions to read current configuration.",
            "Identify which link pair is colliding via robot_config['self_collision_ignore'] inspection.",
            "Jog the robot using set_joint_targets to a clear configuration (e.g., home/retract pose).",
            "Re-run plan_single only after the start state is collision-free.",
            "If start state cannot be cleared by jogging, check for USD prim interpenetration in the stage.",
        ],
        estimated_minutes=10,
        success_indicator="check_collisions returns no self-collision pairs at start state",
    ),
    FixupRecipe(
        failure_mode="goal_unreachable",
        recipe_id="goal_unreachable_workspace_check",
        steps=[
            "Call show_workspace to visualise the robot's reachable sphere.",
            "Verify goal position magnitude from base is within max reach (check spec sheet).",
            "Check whether a mounted tool/payload extends effective reach; subtract tool length from goal offset.",
            "If goal is in shadow of robot body, try an approach direction override in MotionGenPlanConfig.",
            "Consider using a via-point approach: plan to an intermediate reachable waypoint first.",
        ],
        estimated_minutes=15,
        success_indicator="plan_single with adjusted goal returns success=True",
    ),
]

# Build a quick-lookup dict by failure_mode (first recipe wins)
_RECIPE_LOOKUP: Dict[str, FixupRecipe] = {}
for _r in FIXUP_RECIPES:
    if _r.failure_mode not in _RECIPE_LOOKUP:
        _RECIPE_LOOKUP[_r.failure_mode] = _r


# ---------------------------------------------------------------------------
# CuRoboDebugProtocol class
# ---------------------------------------------------------------------------


class CuRoboDebugProtocol:
    """Structured debug protocol for cuRobo motion-planning failures.

    Provides per-robot-family checklists, failure classification, and
    fixup recipes to accelerate diagnosis of cuRobo planning issues.
    """

    def __init__(self) -> None:
        """Initialise the protocol, wiring the pre-populated check and recipe tables."""
        self._checks: Dict[str, List[DebugCheck]] = DEBUG_CHECKS_BY_FAMILY
        self._recipes: Dict[str, FixupRecipe] = _RECIPE_LOOKUP

    # ------------------------------------------------------------------
    # Check accessors
    # ------------------------------------------------------------------

    def checks_for(self, family: str) -> List[DebugCheck]:
        """Return universal checks + family-specific checks for *family*.

        Returns an empty list if *family* is not recognised.
        """
        return self._checks.get(family, [])

    # ------------------------------------------------------------------
    # Recipe accessors
    # ------------------------------------------------------------------

    def recipe_for(self, failure_mode: str) -> Optional[FixupRecipe]:
        """Return the primary FixupRecipe for *failure_mode*, or None."""
        return self._recipes.get(failure_mode)

    def recipes_for_failures(self, failures: List[str]) -> List[FixupRecipe]:
        """Return one recipe per failure mode in *failures* that has a match.

        Duplicate failure modes yield only one recipe each.
        """
        seen: set = set()
        result: List[FixupRecipe] = []
        for fm in failures:
            if fm not in seen:
                seen.add(fm)
                r = self._recipes.get(fm)
                if r is not None:
                    result.append(r)
        return result

    # ------------------------------------------------------------------
    # Failure classifier
    # ------------------------------------------------------------------

    def classify_failure(self, error_message: str) -> Optional[str]:
        """Map a cuRobo error string to a CuRoboFailureMode value, or None.

        Matching is case-insensitive substring search.
        """
        if not error_message or not error_message.strip():
            return None

        msg = error_message.lower()

        if "phantom" in msg:
            return "scene_collision_phantom"
        if "inflation" in msg:
            return "obstacle_inflation_too_aggressive"
        if "joint limit" in msg:
            return "joint_limit_violation"
        if "singularity" in msg:
            return "ik_singularity"
        if "oom" in msg or "out of memory" in msg:
            return "warp_kernel_oom"
        if "collision at start" in msg:
            return "self_collision_at_start"
        if "unreachable" in msg:
            return "goal_unreachable"
        if "no feasible" in msg or "planning failed" in msg:
            return "no_plan_found"

        return None

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary_for(self, family: str) -> Dict[str, Any]:
        """Return a summary dict for *family* with check count and recipe count.

        Args:
            family (str): Robot family name, e.g. ``"franka"``, ``"ur"``.

        Returns:
            Dict[str, Any]: Keys ``check_count``, ``families_supported``, ``recipe_count``.
        """
        checks = self.checks_for(family)
        return {
            "check_count": len(checks),
            "families_supported": len(self._checks),
            "recipe_count": len(FIXUP_RECIPES),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def expected_failure_modes() -> List[str]:
    """Return the canonical list of CuRoboFailureMode values."""
    return list(FAILURE_MODES)
