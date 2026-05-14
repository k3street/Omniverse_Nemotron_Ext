"""Per-axis metric computation for diagnose_scene_feasibility.

Each metric function takes a context dict (everything the metric needs) and
returns a (value, severity, threshold_used) triple. Caller (tool.py) builds
Violation objects from these.

Metrics are pure-Python in this module — actual physics queries
(solve_ik, check_singularity, check_path_clearance, raycast) are made by
tool.py via execute_tool_call, then passed in here as already-resolved
inputs. This separation keeps unit tests possible without Kit RPC.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .schema import Severity, THRESHOLDS


def _euclid(a: List[float], b: List[float]) -> float:
    """Return Euclidean distance between two equal-length numeric vectors."""
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(min(len(a), len(b)))))


def metric_ik_feasible(*, ik_result: Dict[str, Any]) -> Tuple[bool, Optional[Severity]]:
    """ik_result comes from solve_ik tool call.

    Returns (success, severity_if_fail).
    """
    ok = bool(ik_result.get("success") or ik_result.get("ok"))
    if ok:
        return True, None
    return False, THRESHOLDS["ik_feasible"]["fail_severity"]


def metric_collision_distance(*, distance_m: Optional[float], obstacle: str = "") -> Tuple[Optional[float], Optional[Severity]]:
    """check_collisions / sweep_sphere returns nearest-obstacle distance.

    distance_m < 0 → in collision (CRITICAL).
    distance_m < 0.005 → too close (ERROR).
    """
    if distance_m is None:
        return None, None
    th = THRESHOLDS["collision_distance"]
    if distance_m < th["critical"]:
        return distance_m, Severity.CRITICAL
    if distance_m < th["error"]:
        return distance_m, Severity.ERROR
    return distance_m, None


def metric_manipulability(*, manip: Optional[float]) -> Tuple[Optional[float], Optional[Severity]]:
    """check_singularity returns the manipulability index = sqrt(det(J·J^T)).

    Below 0.05 → near-singular → WARNING.
    """
    if manip is None:
        return None, None
    th = THRESHOLDS["manipulability"]["warning"]
    if manip < th:
        return manip, Severity.WARNING
    return manip, None


def metric_reach_utilization(*, pose: List[float], robot_base: List[float], max_reach: float
                             ) -> Tuple[float, Optional[Severity]]:
    """|pose - robot_base| / max_reach.

    > 1.0 → CRITICAL (out of reach).
    > 0.95 → WARNING (IK fragile near edge).
    """
    if max_reach <= 0:
        return 0.0, None
    util = _euclid(pose, robot_base) / float(max_reach)
    th = THRESHOLDS["reach_utilization"]
    if util > th["critical"]:
        return util, Severity.CRITICAL
    if util > th["warning"]:
        return util, Severity.WARNING
    return util, None


def metric_inside_obstacle_bbox(*, pose: List[float], obstacle_bboxes: Dict[str, Dict[str, List[float]]]
                                ) -> Tuple[Optional[str], Optional[Severity]]:
    """Test pose ∈ obstacle bbox for each registered obstacle.

    Returns (offending_path, CRITICAL) if pose inside any bbox, else (None, None).
    obstacle_bboxes keys: prim path → {"min": [x,y,z], "max": [x,y,z]}.
    """
    for path, bb in (obstacle_bboxes or {}).items():
        mn = bb.get("min")
        mx = bb.get("max")
        if not mn or not mx:
            continue
        if (mn[0] <= pose[0] <= mx[0]
                and mn[1] <= pose[1] <= mx[1]
                and mn[2] <= pose[2] <= mx[2]):
            return path, THRESHOLDS["inside_obstacle_bbox"]["fail_severity"]
    return None, None


def metric_clearance_pct(*, clear_count: int, total: int) -> Tuple[float, Optional[Severity]]:
    """Path-clearance percentage from N straight-line samples.

    < 60% → ERROR (planner likely fails).
    < 90% → WARNING (planner needs alternative).
    """
    if total <= 0:
        return 0.0, None
    pct = (clear_count / float(total)) * 100.0
    th = THRESHOLDS["clearance_pct"]
    if pct < th["error"]:
        return pct, Severity.ERROR
    if pct < th["warning"]:
        return pct, Severity.WARNING
    return pct, None


def metric_cube_in_sensor_zone_at_settle(*,
                                         cube_xys: List[List[float]],
                                         sensor_xy: List[float],
                                         sensor_radius: float,
                                         k_factor: float = 3.0) -> Tuple[bool, Optional[Severity]]:
    """At settle-tick, does any cube fall within k_factor × sensor_radius of sensor xy?

    If no cube does → controller will never claim → ERROR.
    """
    if not cube_xys or not sensor_xy or sensor_radius <= 0:
        return True, None  # not enough info; don't flag
    threshold = k_factor * sensor_radius
    for c in cube_xys:
        if _euclid(c[:2], sensor_xy[:2]) <= threshold:
            return True, None
    return False, THRESHOLDS["cube_in_sensor_zone_at_settle"]["fail_severity"]


def metric_mutex_conflict(*,
                          robot_a_corridor: Dict[str, List[float]],
                          robot_b_corridor: Dict[str, List[float]],
                          has_mutex: bool) -> Tuple[bool, Optional[Severity]]:
    """If two robots' transit corridors (axis-aligned bbox) overlap AND no
    mutex declared → ERROR (CP-65 pattern)."""
    if has_mutex or not robot_a_corridor or not robot_b_corridor:
        return False, None
    a_min = robot_a_corridor.get("min", [0, 0, 0])
    a_max = robot_a_corridor.get("max", [0, 0, 0])
    b_min = robot_b_corridor.get("min", [0, 0, 0])
    b_max = robot_b_corridor.get("max", [0, 0, 0])
    overlap = all(
        a_min[i] <= b_max[i] and a_max[i] >= b_min[i] for i in range(3)
    )
    if overlap:
        return True, THRESHOLDS["mutex_conflict"]["fail_severity"]
    return False, None
