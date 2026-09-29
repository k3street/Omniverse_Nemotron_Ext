"""Frozen acceptance gate for planner evaluation episodes (no Isaac imports).

The runner prints its own PASS/FAIL, but an evaluation should not grade itself:
this gate re-derives the outcome from the recorded trace against thresholds
fixed in the acceptance file, and reports cost next to it without gating on it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

FROZEN_FIELDS = ("frozen_utc", "frozen_sha256")
BUDGET_LINE = re.compile(
    r"\[budget\] final: (?P<calls>\d+) calls, .*?\$(?P<spent>[0-9.]+) of"
)


def acceptance_digest(acceptance: Mapping[str, Any]) -> str:
    """Hash of everything except the freeze stamp, so any later edit shows."""
    body = {k: v for k, v in acceptance.items() if k not in FROZEN_FIELDS}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_frozen(acceptance: Mapping[str, Any]) -> bool:
    return bool(acceptance.get("frozen_utc")) and (
        acceptance.get("frozen_sha256") == acceptance_digest(acceptance)
    )


def parse_budget(log_text: str) -> dict[str, Any]:
    matches = list(BUDGET_LINE.finditer(log_text))
    if not matches:
        return {"model_calls": None, "cost_usd": None}
    last = matches[-1]
    return {"model_calls": int(last["calls"]), "cost_usd": float(last["spent"])}


def _receptacle_box(trace: Mapping[str, Any]) -> tuple[list[float], list[float]] | None:
    """Receptacle footprint seen at the preflight, moved by any later drift."""
    evidence = trace.get("task_feasibility_preflight", {}).get("capability_evidence", {})
    geometry = evidence.get("target_receptacle", {}).get("visible_rgbd_geometry") or {}
    low, high = geometry.get("visible_aabb_min_base_m"), geometry.get("visible_aabb_max_base_m")
    start = trace.get("initial_state", {}).get("target_receptacle_xyz")
    end = trace.get("final", {}).get("target_receptacle_xyz")
    if low is None or high is None:
        return None
    shift = [0.0, 0.0]
    if start is not None and end is not None:
        shift = [end[0] - start[0], end[1] - start[1]]
    return (
        [low[0] + shift[0], low[1] + shift[1]],
        [high[0] + shift[0], high[1] + shift[1]],
    )


def check_episode(
    trace: Mapping[str, Any] | None, log_text: str, acceptance: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Grade one episode. A missing trace fails every check rather than none."""
    checks = acceptance["checks"]
    details: dict[str, Any] = {**parse_budget(log_text)}
    if not trace:
        details["failed"] = ["trace_missing"]
        return False, details
    final = trace.get("final") or {}
    results: dict[str, bool] = {}

    results["runner_status"] = trace.get("status") == checks["runner_status"]

    object_xyz = final.get("movable_object_xyz")
    box = _receptacle_box(trace)
    inset = checks["object_within_receptacle_inset_m"]
    if object_xyz is None or box is None:
        results["object_within_receptacle"] = False
        details["object_within_receptacle"] = "final object pose or receptacle footprint not recorded"
    else:
        low, high = box
        margins = [
            object_xyz[0] - (low[0] + inset),
            (high[0] - inset) - object_xyz[0],
            object_xyz[1] - (low[1] + inset),
            (high[1] - inset) - object_xyz[1],
        ]
        details["object_inset_margin_m"] = min(margins)
        results["object_within_receptacle"] = min(margins) >= 0.0

    height = final.get("object_height_above_target_m")
    results["object_resting_low"] = height is not None and (
        height <= checks["max_object_height_above_receptacle_m"]
    )
    details["object_height_above_receptacle_m"] = height

    retreat = trace.get("release_retreat") or {}
    retreat_z = retreat.get("eef_retreat_z_m")
    drift = retreat.get("object_motion_during_retreat_m")
    results["gripper_retreated"] = retreat_z is not None and retreat_z >= checks["min_retreat_m"]
    results["object_left_in_place"] = drift is not None and (
        drift <= checks["max_object_motion_during_retreat_m"]
    )
    details["retreat_z_m"], details["object_motion_during_retreat_m"] = retreat_z, drift

    telemetry = final.get("contact_telemetry") or {}
    results["contact_telemetry"] = telemetry.get("passed") is checks["contact_telemetry_passed"]
    results["physics_steps_are_local"] = trace.get("physics_steps_are_local") is checks[
        "physics_steps_are_local"
    ]
    lessons = (trace.get("critic_memory_applied") or {}).get("lessons") or []
    results["independent_of_other_episodes"] = not (checks["no_critic_memory"] and lessons)

    details["checks"] = results
    details["runner_final_tests"] = final.get("tests")
    details["failed"] = [name for name, ok in results.items() if not ok]
    return not details["failed"], details


def wilson_interval(passes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a pass rate; honest at small n, unlike passes/trials alone."""
    if trials == 0:
        return (0.0, 1.0)
    p = passes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


# --- Final-state grading, common to every kind of policy --------------------
#
# check_episode above grades the planner from its own trace. A scripted oracle
# or an end-to-end VLA run through RoboLab has no such trace, so comparing them
# needs a grade every runner can produce: where the object and the receptacle
# ended up, whether the gripper let go, and whether the object is at rest. All
# of it is read from the per-step recording each runner writes, and measured in
# the receptacle's own frame so the robot's frame convention does not matter.


def _yaw_from_wxyz(q) -> float:
    w, x, y, z = (float(v) for v in q)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _in_receptacle_frame(point, receptacle_xyz, receptacle_quat_wxyz):
    yaw = _yaw_from_wxyz(receptacle_quat_wxyz)
    dx, dy = point[0] - receptacle_xyz[0], point[1] - receptacle_xyz[1]
    c, s = math.cos(-yaw), math.sin(-yaw)
    return (c * dx - s * dy, s * dx + c * dy, point[2] - receptacle_xyz[2])


def outcome_from_episode_hdf5(path, object_name: str, receptacle_name: str, *, fps: float = 15.0) -> dict:
    """Final state of one recorded episode (RoboLab, oracle or planner recorder)."""
    import h5py
    import numpy as np

    with h5py.File(path, "r") as source:
        demo = source["data/demo_0"]
        rigid = demo["states/rigid_object"]
        obj = np.asarray(rigid[object_name]["root_pose"])
        rec = np.asarray(rigid[receptacle_name]["root_pose"])
        actions = np.asarray(demo["actions"])
    window = max(1, int(round(fps)))
    tail = obj[-min(window, len(obj)):, :3]
    return {
        "source": "episode_recording",
        "object_xyz": obj[-1, :3].tolist(),
        "receptacle_xyz": rec[-1, :3].tolist(),
        "receptacle_quat_wxyz": rec[-1, 3:7].tolist(),
        "gripper_command": float(actions[-1, 7]),
        "object_motion_last_second_m": float(np.linalg.norm(tail[-1] - tail[0])),
        "steps": int(len(obj)),
    }


def outcome_from_trace(trace: Mapping[str, Any]) -> dict | None:
    """The same outcome from an older planner trace that has no recording."""
    final = trace.get("final") or {}
    retreat = trace.get("release_retreat") or {}
    if final.get("movable_object_xyz") is None or final.get("target_receptacle_xyz") is None:
        return None
    return {
        "source": "planner_trace",
        "object_xyz": final["movable_object_xyz"],
        "receptacle_xyz": final["target_receptacle_xyz"],
        "receptacle_quat_wxyz": [1.0, 0.0, 0.0, 0.0],  # the trace does not record bin yaw
        "gripper_command": 0.0 if retreat.get("eef_retreat_z_m") is not None else 1.0,
        "object_motion_last_second_m": retreat.get("object_motion_during_retreat_m"),
        "steps": None,
    }


def check_final_state(outcome: Mapping[str, Any] | None, acceptance: Mapping[str, Any]) -> tuple[bool, dict]:
    """Grade an outcome against the acceptance file's final-state checks."""
    spec = acceptance["final_state"]
    footprint = spec["receptacle_footprint_m"]
    details: dict[str, Any] = {}
    if not outcome:
        return False, {"failed": ["outcome_missing"]}
    x, y, z = _in_receptacle_frame(outcome["object_xyz"], outcome["receptacle_xyz"], outcome["receptacle_quat_wxyz"])
    inset = spec["inset_m"]
    margin = min(footprint["half_x"] - inset - abs(x), footprint["half_y"] - inset - abs(y))
    motion = outcome.get("object_motion_last_second_m")
    results = {
        "inside_receptacle": margin >= 0.0,
        "resting_low": 0.0 <= z <= spec["max_object_height_above_receptacle_m"],
        "gripper_released": outcome["gripper_command"] < 0.5,
        "object_still": motion is not None and motion <= spec["max_object_motion_last_second_m"],
    }
    details.update(
        inside_margin_m=margin, height_above_receptacle_m=z, object_motion_last_second_m=motion,
        outcome_source=outcome.get("source"), checks=results,
        failed=[name for name, ok in results.items() if not ok],
    )
    return not details["failed"], details
