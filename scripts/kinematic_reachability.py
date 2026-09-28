"""Non-actuating reachability evidence for a sequence of end-effector poses.

The feasibility preflight is asked whether the grasp, carry and placement can be
reached before any motion is authorized. Positions and a list of registered IK
executors do not answer that. This module does: it solves damped least squares
IK along straight-line paths between the poses on the live articulation's own
kinematics, writing joint positions without stepping physics, and then puts the
arm back exactly where it was.

The adapter is kinematic only. Nothing here commands a joint target or advances
the simulator, so no contact is made and no object is disturbed; it also means
link poses are checked against a support height, not against meshes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol, Sequence

import torch

try:
    from .adaptive_pick_place import quaternion_error_axis_angle_wxyz
    from .residual_centering import bounded_vector_step, damped_least_squares_delta
except ImportError:  # Script execution adds this directory to sys.path.
    from adaptive_pick_place import quaternion_error_axis_angle_wxyz  # type: ignore[no-redef]
    from residual_centering import (  # type: ignore[no-redef]
        bounded_vector_step,
        damped_least_squares_delta,
    )


class ArmKinematics(Protocol):
    """Kinematic view of one arm; ``set_joint_positions`` must not step physics."""

    link_names: Sequence[str]

    def joint_positions(self) -> torch.Tensor: ...

    def joint_limits(self) -> torch.Tensor: ...

    def set_joint_positions(self, positions: torch.Tensor) -> None: ...

    def eef_pose(self) -> tuple[torch.Tensor, torch.Tensor]: ...

    def jacobian(self) -> torch.Tensor: ...

    def link_positions(self) -> torch.Tensor: ...


@dataclass(frozen=True)
class ProbePose:
    name: str
    xyz: torch.Tensor
    quaternion_wxyz: torch.Tensor


@dataclass(frozen=True)
class ProbeConfig:
    position_tolerance_m: float = 0.005
    orientation_tolerance_deg: float = 3.0
    maximum_iterations_per_waypoint: int = 120
    translation_step_limit_m: float = 0.02
    rotation_step_limit_deg: float = 6.0
    joint_step_limit_rad: float = 0.08
    damping: float = 0.05
    waypoint_spacing_m: float = 0.03
    waypoint_rotation_deg: float = 10.0
    limit_guard_rad: float = 1.0e-3
    refresh_probe_rad: float = 0.05
    minimum_refresh_motion_m: float = 1.0e-3
    restore_tolerance_m: float = 1.0e-4


def slerp_wxyz(start: torch.Tensor, end: torch.Tensor, fraction: float) -> torch.Tensor:
    """Shortest-arc spherical interpolation between unit quaternions."""
    start = start / torch.linalg.vector_norm(start)
    end = end / torch.linalg.vector_norm(end)
    dot = float(torch.dot(start, end))
    if dot < 0.0:
        end = -end
        dot = -dot
    if dot > 0.9995:
        blended = start + fraction * (end - start)
        return blended / torch.linalg.vector_norm(blended)
    theta = math.acos(min(1.0, dot))
    scale_start = math.sin((1.0 - fraction) * theta) / math.sin(theta)
    scale_end = math.sin(fraction * theta) / math.sin(theta)
    return scale_start * start + scale_end * end


def normalized_joint_margin(positions: torch.Tensor, limits: torch.Tensor) -> float:
    """Smallest distance to a joint limit, as a fraction of that joint's range."""
    width = limits[:, 1] - limits[:, 0]
    margins = torch.minimum(positions - limits[:, 0], limits[:, 1] - positions)
    return float(torch.clamp(margins / width, min=0.0).min())


def _orientation_error_deg(target: torch.Tensor, current: torch.Tensor) -> float:
    return math.degrees(
        float(torch.linalg.vector_norm(quaternion_error_axis_angle_wxyz(target, current)))
    )


def _solve_waypoint(
    arm: ArmKinematics,
    xyz: torch.Tensor,
    quaternion_wxyz: torch.Tensor,
    limits: torch.Tensor,
    config: ProbeConfig,
) -> tuple[bool, int, float, float]:
    orientation_tolerance = config.orientation_tolerance_deg
    rotation_step = math.radians(config.rotation_step_limit_deg)
    position_error = orientation_error = math.inf
    for iteration in range(config.maximum_iterations_per_waypoint + 1):
        eef_xyz, eef_quaternion = arm.eef_pose()
        error = xyz - eef_xyz
        rotation_error = quaternion_error_axis_angle_wxyz(quaternion_wxyz, eef_quaternion)
        position_error = float(torch.linalg.vector_norm(error))
        orientation_error = math.degrees(float(torch.linalg.vector_norm(rotation_error)))
        if (
            position_error <= config.position_tolerance_m
            and orientation_error <= orientation_tolerance
        ):
            return True, iteration, position_error, orientation_error
        if iteration == config.maximum_iterations_per_waypoint:
            break
        twist = torch.cat(
            (
                bounded_vector_step(error, config.translation_step_limit_m),
                bounded_vector_step(rotation_error, rotation_step),
            )
        )
        jacobian = arm.jacobian()
        delta = damped_least_squares_delta(
            jacobian, twist.to(jacobian), config.damping, config.joint_step_limit_rad
        )
        positions = arm.joint_positions() + delta.to(limits)
        arm.set_joint_positions(
            torch.clamp(
                positions,
                min=limits[:, 0] + config.limit_guard_rad,
                max=limits[:, 1] - config.limit_guard_rad,
            )
        )
    return False, config.maximum_iterations_per_waypoint, position_error, orientation_error


def _waypoint_count(
    start_xyz: torch.Tensor,
    start_quaternion: torch.Tensor,
    pose: ProbePose,
    config: ProbeConfig,
) -> int:
    distance = float(torch.linalg.vector_norm(pose.xyz - start_xyz))
    rotation = _orientation_error_deg(pose.quaternion_wxyz, start_quaternion)
    return max(
        1,
        math.ceil(distance / config.waypoint_spacing_m),
        math.ceil(rotation / config.waypoint_rotation_deg),
    )


def kinematic_refresh_observed(arm: ArmKinematics, config: ProbeConfig) -> bool:
    """Whether a kinematic-only joint write actually moves the reported EEF.

    If the adapter's refresh path is inactive, every probe would read the same
    pose and report nonsense, so the probe refuses to run instead.
    """
    original = arm.joint_positions().clone()
    before, _ = arm.eef_pose()
    nudged = original.clone()
    limits = arm.joint_limits()
    direction = 1.0 if float(limits[0, 1] - original[0]) > config.refresh_probe_rad else -1.0
    nudged[0] += direction * config.refresh_probe_rad
    arm.set_joint_positions(nudged)
    after, _ = arm.eef_pose()
    arm.set_joint_positions(original)
    return float(torch.linalg.vector_norm(after - before)) >= config.minimum_refresh_motion_m


def probe_pose_sequence(
    arm: ArmKinematics,
    poses: Sequence[ProbePose],
    *,
    support_height_m: float | None = None,
    config: ProbeConfig = ProbeConfig(),
) -> dict[str, Any]:
    """Solve IK along the pose sequence from the current configuration.

    Each pose is reached through waypoints interpolated from the previous one,
    warm-starting from the previous solution, so a pose counts as reachable
    only if the arm can get there continuously, as the executor would. Once a
    pose fails, later poses are left unevaluated rather than solved from a
    configuration the arm could not have reached. The original joint positions
    are restored before returning, whatever happens.
    """
    original = arm.joint_positions().clone()
    start_xyz, _ = arm.eef_pose()
    result: dict[str, Any] = {
        "status": "unavailable",
        "method": "non_actuating_damped_least_squares_ik_on_live_articulation",
        "physics_stepped": False,
        "joint_targets_commanded": False,
        "frame": "robot_root_position_world_axis_orientation_wxyz",
        "tolerances": {
            "position_m": config.position_tolerance_m,
            "orientation_deg": config.orientation_tolerance_deg,
        },
        "poses": [],
        "all_reachable": False,
        "restored": False,
    }
    try:
        if not kinematic_refresh_observed(arm, config):
            result["unavailable_reason"] = (
                "a kinematic-only joint write did not move the reported end "
                "effector, so this adapter cannot evaluate poses"
            )
            return result
        limits = arm.joint_limits()
        link_names = list(arm.link_names)
        chain_broken = False
        for pose in poses:
            record: dict[str, Any] = {
                "name": pose.name,
                "target_xyz_m": pose.xyz.tolist(),
                "target_quaternion_wxyz": pose.quaternion_wxyz.tolist(),
            }
            if chain_broken:
                record["reachable"] = None
                record["not_evaluated_reason"] = "an earlier pose in the sequence was unreachable"
                result["poses"].append(record)
                continue
            segment_xyz, segment_quaternion = arm.eef_pose()
            waypoints = _waypoint_count(segment_xyz, segment_quaternion, pose, config)
            minimum_margin = normalized_joint_margin(arm.joint_positions(), limits)
            lowest_link: tuple[float, str] | None = None
            iterations = 0
            reached = True
            position_error = orientation_error = math.inf
            for index in range(1, waypoints + 1):
                fraction = index / waypoints
                reached, used, position_error, orientation_error = _solve_waypoint(
                    arm,
                    segment_xyz + fraction * (pose.xyz - segment_xyz),
                    slerp_wxyz(segment_quaternion, pose.quaternion_wxyz, fraction),
                    limits,
                    config,
                )
                iterations += used
                minimum_margin = min(
                    minimum_margin, normalized_joint_margin(arm.joint_positions(), limits)
                )
                if support_height_m is not None:
                    heights = arm.link_positions()[:, 2] - support_height_m
                    index_low = int(torch.argmin(heights))
                    candidate = (float(heights[index_low]), link_names[index_low])
                    if lowest_link is None or candidate[0] < lowest_link[0]:
                        lowest_link = candidate
                if not reached:
                    record["failed_at_waypoint"] = f"{index}/{waypoints}"
                    break
            record.update(
                {
                    "reachable": reached,
                    "position_error_m": position_error,
                    "orientation_error_deg": orientation_error,
                    "waypoints": waypoints,
                    "iterations": iterations,
                    "minimum_normalized_joint_limit_margin_along_path": minimum_margin,
                    "joint_solution_rad": arm.joint_positions().tolist(),
                }
            )
            if lowest_link is not None:
                record["minimum_link_origin_height_above_support_m"] = lowest_link[0]
                record["lowest_link_along_path"] = lowest_link[1]
            chain_broken = not reached
            result["poses"].append(record)
        result["status"] = "evaluated"
        result["all_reachable"] = bool(poses) and not chain_broken
        return result
    finally:
        arm.set_joint_positions(original)
        restored_xyz, _ = arm.eef_pose()
        result["restored"] = (
            float(torch.linalg.vector_norm(restored_xyz - start_xyz))
            <= config.restore_tolerance_m
        )
