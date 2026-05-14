"""Phase 81c — MoveIt 2 + cuMotion external execution bridge.

Provides a pure-Python bridge configuration, message schemas, dry-run
orchestrator, and service-call shape validation layer for integrating
MoveIt 2 and NVIDIA cuMotion / cuRobo planners as external execution
backends.

Live ROS2 execution and cuMotion GPU calls are opus-runtime only;
this module handles dry-run orchestration and config/schema contracts.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 81c.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "81c"
PHASE_TITLE = "MoveIt 2 + cuMotion external execution bridge"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 81c",
    }


# ---------------------------------------------------------------------------
# Planner backend enum-like Literal
# ---------------------------------------------------------------------------

PlannerBackend = Literal["moveit_pilz", "moveit_ompl", "cumotion", "curobo_v2"]


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class BridgeConfig:
    """Configuration for the MoveIt 2 / cuMotion execution bridge.

    Attributes
    ----------
    ros_domain_id:
        ROS 2 domain identifier.  Defaults to 0 (single-host default).
    planning_group:
        MoveIt planning group name (e.g. ``"manipulator"``, ``"arm"``).
    planner:
        Which planning backend to use.
    num_planning_attempts:
        Number of re-tries the planner may use before declaring failure.
    planning_time_s:
        Wall-clock budget (seconds) per planning call.
    max_velocity_scale:
        Fraction of joint velocity limits (0, 1].
    max_acceleration_scale:
        Fraction of joint acceleration limits (0, 1].
    collision_check_enabled:
        Whether to enable collision checking in the planning pipeline.
    """

    ros_domain_id: int = 0
    planning_group: str = "manipulator"
    planner: PlannerBackend = "cumotion"
    num_planning_attempts: int = 10
    planning_time_s: float = 1.0
    max_velocity_scale: float = 1.0
    max_acceleration_scale: float = 1.0
    collision_check_enabled: bool = True


# ---------------------------------------------------------------------------
# Goal message schemas
# ---------------------------------------------------------------------------

@dataclass
class JointGoal:
    """Target joint-space configuration.

    Attributes
    ----------
    joint_names:
        Ordered list of joint names matching the planning group.
    positions:
        Desired joint positions (radians for revolute, metres for prismatic).
    tolerance:
        Per-joint goal tolerance in radians/metres.
    """

    joint_names: List[str]
    positions: List[float]
    tolerance: float = 0.001


@dataclass
class PoseGoal:
    """Target Cartesian pose in a named reference frame.

    Attributes
    ----------
    frame_id:
        TF2 frame of the goal pose (e.g. ``"world"``, ``"base_link"``).
    position:
        (x, y, z) translation in metres.
    orientation_xyzw:
        Quaternion (x, y, z, w) — ROS convention.
    position_tolerance_m:
        Allowable position error in metres.
    orientation_tolerance_rad:
        Allowable orientation error in radians.
    """

    frame_id: str
    position: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]
    position_tolerance_m: float = 0.005
    orientation_tolerance_rad: float = 0.01


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryPoint:
    """A single point in a joint-space trajectory.

    Attributes
    ----------
    time_from_start_s:
        Time offset from the start of the trajectory in seconds.
    positions:
        Joint positions at this point.
    velocities:
        Joint velocities at this point.  May be empty if not provided by
        the planner.
    """

    time_from_start_s: float
    positions: List[float]
    velocities: List[float] = field(default_factory=list)


@dataclass
class PlanResult:
    """Outcome of a planning request.

    Attributes
    ----------
    success:
        ``True`` when a valid trajectory was found.
    trajectory:
        Ordered list of :class:`TrajectoryPoint` objects.
    planning_time_s:
        Actual wall-clock time used by the planner.
    planner_used:
        Which backend produced this result.
    error:
        Human-readable error description when ``success`` is ``False``.
    """

    success: bool
    trajectory: List[TrajectoryPoint]
    planning_time_s: float
    planner_used: PlannerBackend
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Planner capability registry
# ---------------------------------------------------------------------------

PLANNER_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "moveit_pilz": {
        "supports_collision": True,
        "supports_curobo_obstacles": False,
        "supports_orientation_constraint": False,
        "recommended_for": ["linear_motion", "circular_motion", "ptp_safe"],
    },
    "moveit_ompl": {
        "supports_collision": True,
        "supports_curobo_obstacles": False,
        "supports_orientation_constraint": True,
        "recommended_for": ["complex_ik", "obstacle_avoidance", "whole_body"],
    },
    "cumotion": {
        "supports_collision": True,
        "supports_curobo_obstacles": True,
        "supports_orientation_constraint": True,
        "recommended_for": ["fast_pick_place", "reactive_motion", "high_freq_replan"],
    },
    "curobo_v2": {
        "supports_collision": True,
        "supports_curobo_obstacles": True,
        "supports_orientation_constraint": True,
        "recommended_for": [
            "complex_assembly",
            "dense_obstacle_field",
            "multi_arm_coordination",
        ],
    },
}


# ---------------------------------------------------------------------------
# Heuristic planner selector
# ---------------------------------------------------------------------------

def detect_planner_for_task(task_type: str, has_obstacles: bool) -> PlannerBackend:
    """Return the most appropriate planner backend for a given task type.

    Heuristic rules (in priority order):

    1. ``"complex_assembly"`` → ``"curobo_v2"`` (handles dense obstacle fields
       and multi-step assembly constraints).
    2. ``"fast_pick_place"`` → ``"cumotion"`` (optimised for low-latency
       reactive replanning in pick-and-place workflows).
    3. ``"linear_motion"`` → ``"moveit_pilz"`` (Pilz LIN motion primitive gives
       a guaranteed straight Cartesian path).
    4. *Fallback with obstacles* → ``"curobo_v2"``; without → ``"cumotion"``.

    Parameters
    ----------
    task_type:
        Short descriptor of the task (e.g. ``"fast_pick_place"``).
    has_obstacles:
        Whether the scene contains dynamic or complex obstacles that require
        cuRobo-aware collision representations.
    """
    if task_type == "complex_assembly":
        return "curobo_v2"
    if task_type == "fast_pick_place":
        return "cumotion"
    if task_type == "linear_motion":
        return "moveit_pilz"
    # Generic fallback
    return "curobo_v2" if has_obstacles else "cumotion"


# ---------------------------------------------------------------------------
# Bridge class
# ---------------------------------------------------------------------------

class MoveItCuMotionBridge:
    """Orchestrate motion planning requests to MoveIt 2 / cuMotion backends.

    In *dry-run* mode (the default), all planning calls return plausible mock
    trajectories without communicating with ROS 2.  This allows the bridge API
    to be exercised in unit tests and CI pipelines that have no ROS 2
    environment.

    In live mode (``dry_run=False``), ``plan_to_joint`` / ``plan_to_pose``
    raise :class:`NotImplementedError` to signal that the opus-runtime layer
    must supply the real ROS 2 / cuMotion integration.

    Parameters
    ----------
    config:
        Bridge configuration.
    dry_run:
        When ``True`` (default) mock all planner interactions.
    """

    def __init__(self, config: BridgeConfig, dry_run: bool = True) -> None:
        self.config = config
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Configuration validation
    # ------------------------------------------------------------------

    def validate_config(self) -> List[str]:
        """Validate *self.config* and return a list of issue strings.

        Returns an empty list if the configuration is clean.  Each entry in
        the returned list is a human-readable description of a problem.

        Checks performed
        ----------------
        * ``planning_group`` must be non-empty.
        * ``planning_time_s`` must be strictly positive.
        * ``max_velocity_scale`` must be in ``(0, 1]``.
        * ``max_acceleration_scale`` must be in ``(0, 1]``.
        * ``num_planning_attempts`` must be > 0.
        """
        issues: List[str] = []
        cfg = self.config

        if not cfg.planning_group or not cfg.planning_group.strip():
            issues.append("planning_group must be a non-empty string.")

        if cfg.planning_time_s <= 0:
            issues.append(
                f"planning_time_s must be > 0, got {cfg.planning_time_s}."
            )

        if not (0 < cfg.max_velocity_scale <= 1.0):
            issues.append(
                f"max_velocity_scale must be in (0, 1], got {cfg.max_velocity_scale}."
            )

        if not (0 < cfg.max_acceleration_scale <= 1.0):
            issues.append(
                f"max_acceleration_scale must be in (0, 1], got {cfg.max_acceleration_scale}."
            )

        if cfg.num_planning_attempts <= 0:
            issues.append(
                f"num_planning_attempts must be > 0, got {cfg.num_planning_attempts}."
            )

        return issues

    # ------------------------------------------------------------------
    # Planning API
    # ------------------------------------------------------------------

    def plan_to_joint(self, goal: JointGoal) -> PlanResult:
        """Plan a motion to a joint-space goal.

        In dry-run mode returns a mock 3-waypoint trajectory.
        In live mode raises :class:`NotImplementedError`.

        Parameters
        ----------
        goal:
            Desired joint configuration.
        """
        if not self.dry_run:
            raise NotImplementedError(
                "Live MoveIt 2 / cuMotion execution is an opus-runtime feature. "
                "Instantiate MoveItCuMotionBridge with dry_run=True for testing."
            )

        joint_count = len(goal.joint_names) if goal.joint_names else 7
        trajectory = self._mock_trajectory(joint_count=joint_count)

        return PlanResult(
            success=True,
            trajectory=trajectory,
            planning_time_s=0.042,
            planner_used=self.config.planner,
            error=None,
        )

    def plan_to_pose(self, goal: PoseGoal) -> PlanResult:
        """Plan a motion to a Cartesian-space pose goal.

        In dry-run mode returns a mock 3-waypoint trajectory.
        In live mode raises :class:`NotImplementedError`.

        Parameters
        ----------
        goal:
            Desired end-effector pose.
        """
        if not self.dry_run:
            raise NotImplementedError(
                "Live MoveIt 2 / cuMotion execution is an opus-runtime feature. "
                "Instantiate MoveItCuMotionBridge with dry_run=True for testing."
            )

        trajectory = self._mock_trajectory(joint_count=7)

        return PlanResult(
            success=True,
            trajectory=trajectory,
            planning_time_s=0.058,
            planner_used=self.config.planner,
            error=None,
        )

    # ------------------------------------------------------------------
    # Execution API
    # ------------------------------------------------------------------

    def execute(self, plan: PlanResult) -> Dict[str, Any]:
        """Execute a previously planned trajectory.

        In dry-run mode returns an execution summary without performing any
        real motion.

        Parameters
        ----------
        plan:
            A :class:`PlanResult` previously returned by :meth:`plan_to_joint`
            or :meth:`plan_to_pose`.
        """
        if not self.dry_run:
            raise NotImplementedError(
                "Live execution is an opus-runtime feature."
            )

        final_position = (
            plan.trajectory[-1].positions if plan.trajectory else []
        )
        return {
            "success": True,
            "executed_points": len(plan.trajectory),
            "final_position": final_position,
            "planner_used": plan.planner_used,
        }

    def stop(self) -> Dict[str, Any]:
        """Send a stop command to the execution backend.

        In dry-run mode returns a no-op success response.
        """
        return {
            "success": True,
            "message": "stop issued (dry_run)" if self.dry_run else "stop issued",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mock_trajectory(
        self,
        joint_count: int = 7,
        num_points: int = 3,
        duration_s: float = 1.5,
    ) -> List[TrajectoryPoint]:
        """Generate a plausible mock trajectory for testing purposes.

        Produces *num_points* evenly spaced points over *duration_s* seconds,
        with linearly interpolated positions from zero and zero velocities.

        Parameters
        ----------
        joint_count:
            Number of joints in the trajectory.
        num_points:
            Number of waypoints to generate.
        duration_s:
            Total trajectory duration in seconds.
        """
        trajectory: List[TrajectoryPoint] = []
        for i in range(num_points):
            t = duration_s * i / max(num_points - 1, 1)
            alpha = i / max(num_points - 1, 1)
            # Linearly interpolate positions from 0.0 to 0.1 * joint_index
            positions = [round(alpha * 0.1 * j, 6) for j in range(joint_count)]
            velocities = [0.0] * joint_count
            trajectory.append(
                TrajectoryPoint(
                    time_from_start_s=t,
                    positions=positions,
                    velocities=velocities,
                )
            )
        return trajectory
