"""Observation-bound, runtime-configurable execution tools.

Motion executors register their own model-facing tools and configuration
schemas at runtime.  A selected model or human may invoke one executor, hold,
or abort using exactly one fresh observation token.  This protocol does not
know about tasks, embodiments, joints, objects, phases, or controller types.

Actuator executors use the same fresh-token and fail-closed rules, but own a
runtime-defined command schema instead of a world-space target.

Operation schedulers choose among runtime-advertised next operations using the
same single-use observation token. Candidate descriptions are supplied by the
runtime, so the scheduling contract does not encode a task, phase sequence,
embodiment, or executor implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import Any, Mapping, Sequence


CONTROL_TOOL_NAMES = frozenset({"hold_motion", "abort_motion"})
ACTUATOR_CONTROL_TOOL_NAMES = frozenset(
    {"hold_actuation", "abort_actuation"}
)
SCHEDULER_CONTROL_TOOL_NAMES = frozenset(
    {"observe_again", "complete_task", "abort_task"}
)
FEASIBILITY_STATUSES = frozenset({"feasible", "infeasible", "unknown"})
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_OPERATION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_CAPABILITY_TAG = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class MotionToolValidationError(ValueError):
    """Raised when a motion tool call is stale, malformed, or unsafe."""


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MotionToolValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _finite_vector(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MotionToolValidationError(f"{path} must be an XYZ array")
    if len(value) != 3:
        raise MotionToolValidationError(f"{path} must contain exactly three numbers")
    result: list[float] = []
    for index, component in enumerate(value):
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise MotionToolValidationError(f"{path}[{index}] must be a number")
        component = float(component)
        if not math.isfinite(component):
            raise MotionToolValidationError(f"{path}[{index}] must be finite")
        result.append(component)
    return result[0], result[1], result[2]


def _finite_quaternion(
    value: Any, path: str
) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MotionToolValidationError(f"{path} must be a WXYZ array")
    if len(value) != 4:
        raise MotionToolValidationError(
            f"{path} must contain exactly four numbers"
        )
    result: list[float] = []
    for index, component in enumerate(value):
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise MotionToolValidationError(f"{path}[{index}] must be a number")
        component = float(component)
        if not math.isfinite(component):
            raise MotionToolValidationError(f"{path}[{index}] must be finite")
        result.append(component)
    norm = math.sqrt(sum(component * component for component in result))
    if norm <= 1.0e-9:
        raise MotionToolValidationError(f"{path} must have non-zero magnitude")
    return tuple(component / norm for component in result)  # type: ignore[return-value]


def _quaternion_multiply_wxyz(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return _finite_quaternion(
        (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        ),
        "target_after_quaternion_wxyz",
    )


def _axis_angle_degrees_quaternion_wxyz(
    rotation_delta_axis_angle_deg: Sequence[float],
) -> tuple[float, float, float, float]:
    angle_deg = math.sqrt(
        sum(component * component for component in rotation_delta_axis_angle_deg)
    )
    if angle_deg <= 1.0e-12:
        return 1.0, 0.0, 0.0, 0.0
    axis = tuple(component / angle_deg for component in rotation_delta_axis_angle_deg)
    half_angle = math.radians(angle_deg) * 0.5
    scale = math.sin(half_angle)
    return math.cos(half_angle), *(component * scale for component in axis)


def compare_grasp_pose_to_failed_attempts(
    *,
    failed_attempts: Sequence[Mapping[str, Any]],
    current_eef_xyz_m: Sequence[float],
    current_object_xyz_m: Sequence[float],
    current_eef_quaternion_wxyz: Sequence[float],
) -> list[dict[str, Any]]:
    """Report object-relative pose deltas from failed grasp attempts.

    This intentionally encodes no retry threshold. It exposes measured
    translation and shortest-path quaternion deltas so a selected governor can
    reason about pose novelty without inferring it from a large raw payload.
    """
    if not isinstance(failed_attempts, Sequence) or isinstance(
        failed_attempts, (str, bytes)
    ):
        raise MotionToolValidationError("failed_attempts must be an array")
    current_eef = _finite_vector(current_eef_xyz_m, "current_eef_xyz_m")
    current_object = _finite_vector(
        current_object_xyz_m, "current_object_xyz_m"
    )
    current_quaternion = _finite_quaternion(
        current_eef_quaternion_wxyz,
        "current_eef_quaternion_wxyz",
    )
    current_relative = tuple(
        eef_component - object_component
        for eef_component, object_component in zip(current_eef, current_object)
    )
    comparisons: list[dict[str, Any]] = []
    for index, attempt in enumerate(failed_attempts):
        if not isinstance(attempt, Mapping):
            raise MotionToolValidationError(
                f"failed_attempts[{index}] must be an object"
            )
        attempt_relative = _finite_vector(
            attempt.get("eef_minus_object_m"),
            f"failed_attempts[{index}].eef_minus_object_m",
        )
        attempt_quaternion = _finite_quaternion(
            attempt.get("eef_quaternion_wxyz"),
            f"failed_attempts[{index}].eef_quaternion_wxyz",
        )
        translation_delta = math.sqrt(
            sum(
                (current_component - attempt_component) ** 2
                for current_component, attempt_component in zip(
                    current_relative, attempt_relative
                )
            )
        )
        quaternion_dot = abs(
            sum(
                current_component * attempt_component
                for current_component, attempt_component in zip(
                    current_quaternion, attempt_quaternion
                )
            )
        )
        orientation_delta_deg = math.degrees(
            2.0 * math.acos(min(1.0, max(-1.0, quaternion_dot)))
        )
        comparisons.append(
            {
                "attempt_id": attempt.get("attempt_id", index + 1),
                "comparison_frame": "object_relative_end_effector_pose",
                "translation_delta_m": translation_delta,
                "orientation_delta_deg": orientation_delta_deg,
            }
        )
    return comparisons


def failed_grasp_pose_lease_released(
    *,
    pose_comparisons: Sequence[Mapping[str, Any]],
    minimum_translation_delta_m: float = 0.015,
    minimum_orientation_delta_deg: float = 10.0,
) -> bool:
    """Return whether a new engagement pose differs from every failed pose.

    The thresholds are runtime configuration, not task or embodiment knowledge.
    Translation and orientation are alternative ways to establish a materially
    new object-relative grasp pose. An empty failure history imposes no lease.
    """
    if not isinstance(pose_comparisons, Sequence) or isinstance(
        pose_comparisons, (str, bytes)
    ):
        raise MotionToolValidationError("pose_comparisons must be an array")
    for name, value in (
        ("minimum_translation_delta_m", minimum_translation_delta_m),
        ("minimum_orientation_delta_deg", minimum_orientation_delta_deg),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be finite and positive")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise MotionToolValidationError(f"{name} must be finite and positive")

    for index, comparison in enumerate(pose_comparisons):
        if not isinstance(comparison, Mapping):
            raise MotionToolValidationError(
                f"pose_comparisons[{index}] must be an object"
            )
        deltas: dict[str, float] = {}
        for name in ("translation_delta_m", "orientation_delta_deg"):
            value = comparison.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MotionToolValidationError(
                    f"pose_comparisons[{index}].{name} must be finite"
                )
            value = float(value)
            if not math.isfinite(value) or value < 0.0:
                raise MotionToolValidationError(
                    f"pose_comparisons[{index}].{name} must be finite and non-negative"
                )
            deltas[name] = value
        if (
            deltas["translation_delta_m"] < minimum_translation_delta_m
            and deltas["orientation_delta_deg"] < minimum_orientation_delta_deg
        ):
            return False
    return True


def compare_target_to_stalled_recovery(
    *,
    previous_recovery_outcome: Mapping[str, Any] | None,
    proposed_target_xyz_m: Sequence[float],
    proposed_target_quaternion_wxyz: Sequence[float],
) -> dict[str, Any] | None:
    """Compare a proposed target with the last physically stalled target.

    The prior executor's model-selected pose tolerances define whether the new
    proposal is materially distinct. This keeps the check task-, object-, and
    embodiment-neutral while preventing an executor from physically retrying a
    target that its own measured outcome already invalidated.
    """
    if not isinstance(previous_recovery_outcome, Mapping):
        return None
    invalidation = previous_recovery_outcome.get("lease_invalidation_reason")
    if not isinstance(invalidation, str) or "motion_progress_stalled" not in {
        item.strip()
        for item in invalidation.removeprefix("lease_invalidated:").split(",")
    }:
        return None

    previous_target = _finite_vector(
        previous_recovery_outcome.get("attempted_target_xyz_m"),
        "previous_recovery_outcome.attempted_target_xyz_m",
    )
    previous_quaternion = _finite_quaternion(
        previous_recovery_outcome.get("attempted_target_quaternion_wxyz"),
        "previous_recovery_outcome.attempted_target_quaternion_wxyz",
    )
    proposed_target = _finite_vector(
        proposed_target_xyz_m,
        "proposed_target_xyz_m",
    )
    proposed_quaternion = _finite_quaternion(
        proposed_target_quaternion_wxyz,
        "proposed_target_quaternion_wxyz",
    )
    position_tolerance = previous_recovery_outcome.get("position_tolerance_m")
    orientation_tolerance = previous_recovery_outcome.get(
        "orientation_tolerance_deg"
    )
    for value, path in (
        (position_tolerance, "previous_recovery_outcome.position_tolerance_m"),
        (
            orientation_tolerance,
            "previous_recovery_outcome.orientation_tolerance_deg",
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise MotionToolValidationError(
                f"{path} must be a finite non-negative number"
            )

    translation_delta = math.sqrt(
        sum(
            (proposed_component - previous_component) ** 2
            for proposed_component, previous_component in zip(
                proposed_target, previous_target
            )
        )
    )
    quaternion_dot = abs(
        sum(
            proposed_component * previous_component
            for proposed_component, previous_component in zip(
                proposed_quaternion, previous_quaternion
            )
        )
    )
    orientation_delta = math.degrees(
        2.0 * math.acos(min(1.0, max(-1.0, quaternion_dot)))
    )
    return {
        "comparison_frame": "world_space_motion_target",
        "translation_delta_m": translation_delta,
        "orientation_delta_deg": orientation_delta,
        "previous_position_tolerance_m": float(position_tolerance),
        "previous_orientation_tolerance_deg": float(orientation_tolerance),
        "effectively_identical": bool(
            translation_delta <= float(position_tolerance)
            and orientation_delta <= float(orientation_tolerance)
        ),
    }


def compare_motion_invocation_to_recent_failures(
    *,
    recent_operation_history: Mapping[str, Any] | None,
    proposed_checkpoints: Sequence[Mapping[str, Any]],
    minimum_translation_delta_m: float = 0.03,
    minimum_orientation_delta_deg: float = 10.0,
) -> dict[str, Any]:
    """Reject a motion path whose terminal pose repeats a recent failed pose.

    Only the trailing, uninterrupted run of physical motion failures is
    considered. Planning-only rejections do not alter the scene and therefore
    do not clear that run; a consumed actuator or successful motion does. The
    thresholds are runtime configuration, so this contract contains no task,
    object, controller, or embodiment identity.
    """
    for name, value in (
        ("minimum_translation_delta_m", minimum_translation_delta_m),
        ("minimum_orientation_delta_deg", minimum_orientation_delta_deg),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise MotionToolValidationError(
                f"{name} must be a finite positive number"
            )
    if not isinstance(proposed_checkpoints, Sequence) or isinstance(
        proposed_checkpoints, (str, bytes)
    ) or not proposed_checkpoints:
        raise MotionToolValidationError(
            "proposed_checkpoints must be a non-empty array"
        )
    terminal = proposed_checkpoints[-1]
    if not isinstance(terminal, Mapping):
        raise MotionToolValidationError(
            "proposed_checkpoints terminal entry must be an object"
        )
    proposed_target = _finite_vector(
        terminal.get("target_position_m"),
        "proposed_checkpoints[-1].target_position_m",
    )
    proposed_quaternion = _finite_quaternion(
        terminal.get("target_quaternion_wxyz"),
        "proposed_checkpoints[-1].target_quaternion_wxyz",
    )
    raw_entries = (
        recent_operation_history.get("entries")
        if isinstance(recent_operation_history, Mapping)
        else None
    )
    if raw_entries is None:
        raw_entries = []
    if not isinstance(raw_entries, Sequence) or isinstance(
        raw_entries, (str, bytes)
    ):
        raise MotionToolValidationError(
            "recent_operation_history.entries must be an array"
        )

    comparisons: list[dict[str, Any]] = []
    for reverse_index, raw_entry in enumerate(reversed(raw_entries)):
        if not isinstance(raw_entry, Mapping):
            raise MotionToolValidationError(
                "recent_operation_history entries must be objects"
            )
        result = raw_entry.get("result")
        result = result if isinstance(result, Mapping) else {}
        planning_only_rejection = isinstance(
            result.get("invocation_rejection"), Mapping
        ) and result.get("converged") is None
        if planning_only_rejection:
            continue
        failed_motion = bool(
            raw_entry.get("tool_family") == "motion"
            and result.get("converged") is False
        )
        if not failed_motion:
            break
        previous_target = _finite_vector(
            result.get("terminal_target_position_m"),
            (
                "recent_operation_history.entries"
                f"[{len(raw_entries) - reverse_index - 1}]"
                ".result.terminal_target_position_m"
            ),
        )
        previous_quaternion = _finite_quaternion(
            result.get("terminal_target_quaternion_wxyz"),
            (
                "recent_operation_history.entries"
                f"[{len(raw_entries) - reverse_index - 1}]"
                ".result.terminal_target_quaternion_wxyz"
            ),
        )
        translation_delta = math.sqrt(
            sum(
                (proposed - previous) ** 2
                for proposed, previous in zip(
                    proposed_target, previous_target
                )
            )
        )
        quaternion_dot = abs(
            sum(
                proposed * previous
                for proposed, previous in zip(
                    proposed_quaternion, previous_quaternion
                )
            )
        )
        orientation_delta = math.degrees(
            2.0 * math.acos(min(1.0, max(-1.0, quaternion_dot)))
        )
        comparisons.append(
            {
                "operation_index": raw_entry.get("operation_index"),
                "translation_delta_m": translation_delta,
                "orientation_delta_deg": orientation_delta,
                "revocation_reason": result.get("revocation_reason"),
                "materially_distinct": bool(
                    translation_delta >= float(minimum_translation_delta_m)
                    or orientation_delta
                    >= float(minimum_orientation_delta_deg)
                ),
            }
        )

    repeated = next(
        (item for item in comparisons if not item["materially_distinct"]),
        None,
    )
    return {
        "admitted": repeated is None,
        "reason": (
            "materially_distinct_from_recent_failed_motion"
            if repeated is None
            else "repeated_recent_failed_motion_target"
        ),
        "comparison_frame": "robot_root_terminal_controlled_pose",
        "minimum_translation_delta_m": float(minimum_translation_delta_m),
        "minimum_orientation_delta_deg": float(
            minimum_orientation_delta_deg
        ),
        "proposed_terminal_target_position_m": list(proposed_target),
        "proposed_terminal_target_quaternion_wxyz": list(
            proposed_quaternion
        ),
        "comparisons": comparisons,
        "blocking_comparison": repeated,
        "execution_authority": False,
    }


def opposing_contact_force_capacity(
    *,
    joint_effort_limit: float,
    contact_point_linear_jacobian_columns: Sequence[Sequence[float]],
    closing_axis: Sequence[float],
    effective_dynamic_friction: float | None = None,
    gravity_m_s2: float | None = None,
) -> dict[str, Any]:
    """Derive a two-contact clamp capacity from live simulator mechanics.

    For equal opposing normal forces ``F`` at two contact points, virtual work
    gives ``tau = (J0 dot n + J1 dot -n) F``.  The active joint effort limit
    therefore bounds the per-contact normal force without embedding a gripper
    model, transmission ratio, task, or object identity.
    """
    if (
        isinstance(joint_effort_limit, bool)
        or not isinstance(joint_effort_limit, (int, float))
        or not math.isfinite(float(joint_effort_limit))
        or float(joint_effort_limit) <= 0.0
    ):
        raise MotionToolValidationError(
            "joint_effort_limit must be a finite positive number"
        )
    if not isinstance(contact_point_linear_jacobian_columns, Sequence) or len(
        contact_point_linear_jacobian_columns
    ) != 2:
        raise MotionToolValidationError(
            "exactly two contact-point Jacobian columns are required"
        )
    jacobians = [
        _finite_vector(value, f"contact_point_linear_jacobian_columns[{index}]")
        for index, value in enumerate(contact_point_linear_jacobian_columns)
    ]
    axis = _finite_vector(closing_axis, "closing_axis")
    axis_norm = math.sqrt(sum(component * component for component in axis))
    if axis_norm <= 1.0e-9:
        raise MotionToolValidationError("closing_axis must have non-zero magnitude")
    axis = tuple(component / axis_norm for component in axis)
    generalized_effort_per_unit_force = abs(
        sum(jacobians[0][index] * axis[index] for index in range(3))
        - sum(jacobians[1][index] * axis[index] for index in range(3))
    )
    if generalized_effort_per_unit_force <= 1.0e-9:
        raise MotionToolValidationError(
            "contact Jacobian does not expose closing-axis mechanical advantage"
        )
    per_contact_normal_force = (
        float(joint_effort_limit) / generalized_effort_per_unit_force
    )
    total_normal_force = 2.0 * per_contact_normal_force
    result: dict[str, Any] = {
        "source": "live_contact_point_jacobian_virtual_work",
        "joint_effort_limit": float(joint_effort_limit),
        "generalized_effort_per_unit_contact_force_m": (
            generalized_effort_per_unit_force
        ),
        "normal_force_per_contact_n": per_contact_normal_force,
        "total_opposing_normal_force_n": total_normal_force,
        "assumptions": [
            "two_equal_opposing_contact_forces",
            "continuous_joint_effort_available",
            "contact_at_runtime_published_body_geometry_centers",
        ],
    }
    if effective_dynamic_friction is not None:
        if (
            isinstance(effective_dynamic_friction, bool)
            or not isinstance(effective_dynamic_friction, (int, float))
            or not math.isfinite(float(effective_dynamic_friction))
            or float(effective_dynamic_friction) < 0.0
        ):
            raise MotionToolValidationError(
                "effective_dynamic_friction must be finite and non-negative"
            )
        friction_load = total_normal_force * float(effective_dynamic_friction)
        result.update(
            {
                "effective_dynamic_friction": float(
                    effective_dynamic_friction
                ),
                "friction_supported_tangential_load_n": friction_load,
            }
        )
        if gravity_m_s2 is not None:
            if (
                isinstance(gravity_m_s2, bool)
                or not isinstance(gravity_m_s2, (int, float))
                or not math.isfinite(float(gravity_m_s2))
                or float(gravity_m_s2) <= 0.0
            ):
                raise MotionToolValidationError(
                    "gravity_m_s2 must be a finite positive number"
                )
            result["physics_derived_payload_capacity_kg"] = (
                friction_load / float(gravity_m_s2)
            )
    return result


def _copy_json(value: Any, path: str = "value") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{path} must contain finite values")
        return value
    if isinstance(value, list):
        return [_copy_json(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise MotionToolValidationError(f"{path} keys must be strings")
            copied[key] = _copy_json(item, f"{path}.{key}")
        return copied
    raise MotionToolValidationError(f"{path} must be JSON-compatible")


def _validate_config_value(value: Any, schema: Mapping[str, Any], path: str) -> Any:
    """Validate the small JSON-Schema subset used by executor configurations."""
    expected = schema.get("type")
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{path} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{path} must be finite")
        if "minimum" in schema and value < float(schema["minimum"]):
            raise MotionToolValidationError(f"{path} is below its minimum")
        if "maximum" in schema and value > float(schema["maximum"]):
            raise MotionToolValidationError(f"{path} exceeds its maximum")
        return value
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MotionToolValidationError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise MotionToolValidationError(f"{path} is below its minimum")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise MotionToolValidationError(f"{path} exceeds its maximum")
        return value
    if expected == "boolean":
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{path} must be a boolean")
        return value
    if expected == "string":
        if not isinstance(value, str):
            raise MotionToolValidationError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise MotionToolValidationError(f"{path} is not an allowed value")
        return value
    raise MotionToolValidationError(
        f"{path} uses unsupported executor-config schema type {expected!r}"
    )


@dataclass(frozen=True)
class MotionExecutorSpec:
    """One runtime-advertised executor and its model-configurable settings."""

    executor_id: str
    tool_name: str
    description: str
    configuration_schema: Mapping[str, Any]
    capability_tags: tuple[str, ...] = ()
    invocation_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _required_text(self.executor_id, "executor_id")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME.fullmatch(
            self.tool_name
        ):
            raise MotionToolValidationError("tool_name has an invalid format")
        if self.tool_name in CONTROL_TOOL_NAMES:
            raise MotionToolValidationError("executor tool collides with a control tool")
        _required_text(self.description, "description")
        schema = self.configuration_schema
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise MotionToolValidationError(
                "configuration_schema must describe an object"
            )
        _copy_json(schema, "configuration_schema")
        if self.invocation_schema is not None:
            if (
                not isinstance(self.invocation_schema, Mapping)
                or self.invocation_schema.get("type") != "object"
            ):
                raise MotionToolValidationError(
                    "invocation_schema must describe an object when supplied"
                )
            _copy_json(self.invocation_schema, "invocation_schema")
        if len(set(self.capability_tags)) != len(self.capability_tags):
            raise MotionToolValidationError(
                "capability_tags must not contain duplicates"
            )
        for index, tag in enumerate(self.capability_tags):
            if not isinstance(tag, str) or not _CAPABILITY_TAG.fullmatch(tag):
                raise MotionToolValidationError(
                    f"capability_tags[{index}] has an invalid format"
                )

    def advertisement(self) -> dict[str, Any]:
        """Describe runtime semantics without creating control authority."""
        advertisement = {
            "executor_id": self.executor_id,
            "tool_name": self.tool_name,
            "tool_family": "motion",
            "capability_tags": list(self.capability_tags),
            "configuration_schema": _copy_json(
                self.configuration_schema, "configuration_schema"
            ),
        }
        if self.invocation_schema is not None:
            advertisement["invocation_schema"] = _copy_json(
                self.invocation_schema, "invocation_schema"
            )
        return advertisement

    def tool_schema(self, observation_id: str) -> dict[str, Any]:
        properties = _common_properties(observation_id)
        properties.update(
            {
                "translation_delta_m": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": (
                        "Optional XYZ correction of the current world-space target, "
                        "in meters in the frame supplied by the runtime."
                    ),
                },
                "rotation_delta_axis_angle_deg": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": (
                        "Optional world-frame axis-angle correction of the current "
                        "target orientation. Vector direction is the rotation axis "
                        "and vector magnitude is degrees."
                    ),
                },
                "executor_config": _copy_json(
                    self.configuration_schema, "configuration_schema"
                ),
            }
        )
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": ["observation_id", "confidence", "reason"],
                },
            },
        }

    def validate_configuration(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise MotionToolValidationError("executor_config must be an object")
        schema = self.configuration_schema
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise MotionToolValidationError(
                f"executor_config contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"executor_config is missing fields: {sorted(missing)}"
            )
        validated = {
            key: _validate_config_value(item, properties[key], f"executor_config.{key}")
            if key in properties
            else _copy_json(item, f"executor_config.{key}")
            for key, item in value.items()
        }
        return validated


class MotionExecutorRegistry:
    """Runtime registry used to discover, advertise, and resolve executors."""

    def __init__(self) -> None:
        self._by_tool_name: dict[str, MotionExecutorSpec] = {}
        self._executor_ids: set[str] = set()

    def register(self, spec: MotionExecutorSpec) -> None:
        if not isinstance(spec, MotionExecutorSpec):
            raise MotionToolValidationError("executor registration requires a spec")
        if spec.tool_name in self._by_tool_name:
            raise MotionToolValidationError(
                f"executor tool {spec.tool_name!r} is already registered"
            )
        if spec.executor_id in self._executor_ids:
            raise MotionToolValidationError(
                f"executor id {spec.executor_id!r} is already registered"
            )
        self._by_tool_name[spec.tool_name] = spec
        self._executor_ids.add(spec.executor_id)

    def resolve(self, tool_name: str) -> MotionExecutorSpec | None:
        return self._by_tool_name.get(tool_name)

    def specs(self) -> tuple[MotionExecutorSpec, ...]:
        return tuple(self._by_tool_name[name] for name in sorted(self._by_tool_name))

    def advertisement(self) -> tuple[dict[str, Any], ...]:
        return tuple(spec.advertisement() for spec in self.specs())


def _common_properties(observation_id: str) -> dict[str, Any]:
    return {
        "observation_id": {
            "type": "string",
            "const": observation_id,
            "description": "The fresh observation token supplied for this decision.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "reason": {
            "type": "string",
            "minLength": 1,
            "description": "Concise reasoning grounded in the fresh observation.",
        },
    }


def task_feasibility_tool_schema(observation_id: str) -> list[dict[str, Any]]:
    """Advertise the mandatory task-neutral pre-motion feasibility tool."""
    observation_id = _required_text(observation_id, "observation_id")
    status = {
        "type": "string",
        "enum": sorted(FEASIBILITY_STATUSES),
    }
    properties = _common_properties(observation_id)
    properties.update(
        {
            "movable_object_visible": {"type": "boolean"},
            "target_receptacle_visible": {"type": "boolean"},
            "reachability": dict(status),
            "grasp_feasibility": dict(status),
            "payload_feasibility": dict(status),
            "task_feasibility": dict(status),
            "motion_authorized": {
                "type": "boolean",
                "description": (
                    "True only when every required feasibility category is "
                    "feasible from current evidence."
                ),
            },
            "blocking_reasons": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "required_runtime_evidence": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "recommended_operations": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        }
    )
    return [
        {
            "type": "function",
            "function": {
                "name": "assess_task_feasibility",
                "description": (
                    "Assess physical reachability, grasp compatibility, payload "
                    "support, and whole-task feasibility before any movement."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": [
                        "observation_id",
                        "confidence",
                        "reason",
                        "movable_object_visible",
                        "target_receptacle_visible",
                        "reachability",
                        "grasp_feasibility",
                        "payload_feasibility",
                        "task_feasibility",
                        "motion_authorized",
                        "blocking_reasons",
                        "required_runtime_evidence",
                        "recommended_operations",
                    ],
                },
            },
        }
    ]


@dataclass(frozen=True)
class TaskFeasibilityOutcome:
    observation_id: str
    confidence: float
    reason: str
    movable_object_visible: bool
    target_receptacle_visible: bool
    reachability: str
    grasp_feasibility: str
    payload_feasibility: str
    task_feasibility: str
    motion_authorized: bool
    blocking_reasons: tuple[str, ...]
    required_runtime_evidence: tuple[str, ...]
    recommended_operations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": "assess_task_feasibility",
            "observation_id": self.observation_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "movable_object_visible": self.movable_object_visible,
            "target_receptacle_visible": self.target_receptacle_visible,
            "reachability": self.reachability,
            "grasp_feasibility": self.grasp_feasibility,
            "payload_feasibility": self.payload_feasibility,
            "task_feasibility": self.task_feasibility,
            "motion_authorized": self.motion_authorized,
            "blocking_reasons": list(self.blocking_reasons),
            "required_runtime_evidence": list(self.required_runtime_evidence),
            "recommended_operations": list(self.recommended_operations),
        }


class ObservationBoundTaskFeasibilityGate:
    """Consume one fail-closed feasibility assessment for one observation."""

    def __init__(self, *, observation_id: str):
        self.observation_id = _required_text(observation_id, "observation_id")
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> TaskFeasibilityOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one feasibility call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        if name != "assess_task_feasibility":
            raise MotionToolValidationError(
                f"unregistered feasibility tool {name!r}"
            )
        required = {
            "observation_id",
            "confidence",
            "reason",
            "movable_object_visible",
            "target_receptacle_visible",
            "reachability",
            "grasp_feasibility",
            "payload_feasibility",
            "task_feasibility",
            "motion_authorized",
            "blocking_reasons",
            "required_runtime_evidence",
            "recommended_operations",
        }
        unknown = set(arguments) - required
        missing = required - set(arguments)
        if unknown:
            raise MotionToolValidationError(
                f"feasibility tool contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"feasibility tool is missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError(
                "feasibility tool call uses a stale observation"
            )
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(
            confidence, (int, float)
        ):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")

        booleans: dict[str, bool] = {}
        for field in (
            "movable_object_visible",
            "target_receptacle_visible",
            "motion_authorized",
        ):
            value = arguments[field]
            if not isinstance(value, bool):
                raise MotionToolValidationError(f"{field} must be a boolean")
            booleans[field] = value
        statuses: dict[str, str] = {}
        for field in (
            "reachability",
            "grasp_feasibility",
            "payload_feasibility",
            "task_feasibility",
        ):
            value = arguments[field]
            if value not in FEASIBILITY_STATUSES:
                raise MotionToolValidationError(
                    f"{field} must be one of {sorted(FEASIBILITY_STATUSES)}"
                )
            statuses[field] = str(value)

        def text_array(field: str) -> tuple[str, ...]:
            value = arguments[field]
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise MotionToolValidationError(f"{field} must be an array")
            return tuple(
                _required_text(item, f"{field}[{index}]")
                for index, item in enumerate(value)
            )

        admitted = bool(
            booleans["movable_object_visible"]
            and booleans["target_receptacle_visible"]
            and all(value == "feasible" for value in statuses.values())
        )
        if booleans["motion_authorized"] != admitted:
            raise MotionToolValidationError(
                "motion_authorized must be true exactly when both scene roles "
                "are visible and every feasibility category is feasible"
            )
        return TaskFeasibilityOutcome(
            observation_id=self.observation_id,
            confidence=confidence,
            reason=" ".join(_required_text(arguments["reason"], "reason").split()),
            movable_object_visible=booleans["movable_object_visible"],
            target_receptacle_visible=booleans["target_receptacle_visible"],
            reachability=statuses["reachability"],
            grasp_feasibility=statuses["grasp_feasibility"],
            payload_feasibility=statuses["payload_feasibility"],
            task_feasibility=statuses["task_feasibility"],
            motion_authorized=admitted,
            blocking_reasons=text_array("blocking_reasons"),
            required_runtime_evidence=text_array("required_runtime_evidence"),
            recommended_operations=text_array("recommended_operations"),
        )


def motion_tool_schemas(
    observation_id: str, registry: MotionExecutorRegistry
) -> list[dict[str, Any]]:
    """Advertise currently registered executors plus hold/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    if not isinstance(registry, MotionExecutorRegistry):
        raise MotionToolValidationError("registry must be a MotionExecutorRegistry")
    schemas = [spec.tool_schema(observation_id) for spec in registry.specs()]
    common = _common_properties(observation_id)
    for name, description in (
        (
            "hold_motion",
            "Hold position and require a new observation before further movement.",
        ),
        ("abort_motion", "Abort the current motion because it is unsafe."),
    ):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": common,
                        "required": ["observation_id", "confidence", "reason"],
                    },
                },
            }
        )
    return schemas


def _tool_name_and_arguments(call: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(call, Mapping):
        raise MotionToolValidationError("tool call must be an object")
    function = call.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
        arguments = function.get("arguments", {})
    else:
        name = call.get("name")
        arguments = call.get("arguments", {})
    name = _required_text(name, "tool name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise MotionToolValidationError("tool arguments must be valid JSON") from error
    if not isinstance(arguments, Mapping):
        raise MotionToolValidationError("tool arguments must be an object")
    return name, arguments


@dataclass(frozen=True)
class MotionToolOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    executor_id: str | None
    executor_config: Mapping[str, Any]
    target_before_m: tuple[float, float, float]
    target_after_m: tuple[float, float, float]
    requested_translation_delta_m: tuple[float, float, float]
    target_before_quaternion_wxyz: tuple[float, float, float, float]
    target_after_quaternion_wxyz: tuple[float, float, float, float]
    requested_rotation_delta_axis_angle_deg: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "executor_id": self.executor_id,
            "executor_config": _copy_json(self.executor_config, "executor_config"),
            "target_before_m": list(self.target_before_m),
            "target_after_m": list(self.target_after_m),
            "requested_translation_delta_m": list(
                self.requested_translation_delta_m
            ),
            "target_before_quaternion_wxyz": list(
                self.target_before_quaternion_wxyz
            ),
            "target_after_quaternion_wxyz": list(
                self.target_after_quaternion_wxyz
            ),
            "requested_rotation_delta_axis_angle_deg": list(
                self.requested_rotation_delta_axis_angle_deg
            ),
        }


class ObservationBoundMotionGate:
    """Validate and consume one model-issued call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        current_target_m: Sequence[float],
        maximum_correction_m: float,
        registry: MotionExecutorRegistry,
        current_target_quaternion_wxyz: Sequence[float] = (1.0, 0.0, 0.0, 0.0),
        maximum_rotation_correction_deg: float = 45.0,
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        maximum_correction_m = float(maximum_correction_m)
        if not math.isfinite(maximum_correction_m) or maximum_correction_m <= 0.0:
            raise MotionToolValidationError("maximum_correction_m must be positive")
        maximum_rotation_correction_deg = float(maximum_rotation_correction_deg)
        if (
            not math.isfinite(maximum_rotation_correction_deg)
            or maximum_rotation_correction_deg <= 0.0
            or maximum_rotation_correction_deg > 180.0
        ):
            raise MotionToolValidationError(
                "maximum_rotation_correction_deg must be within (0, 180]"
            )
        if not isinstance(registry, MotionExecutorRegistry):
            raise MotionToolValidationError("registry must be a MotionExecutorRegistry")
        self.current_target_m = _finite_vector(current_target_m, "current_target_m")
        self.current_target_quaternion_wxyz = _finite_quaternion(
            current_target_quaternion_wxyz,
            "current_target_quaternion_wxyz",
        )
        self.maximum_correction_m = maximum_correction_m
        self.maximum_rotation_correction_deg = maximum_rotation_correction_deg
        self.registry = registry
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> MotionToolOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one tool call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        spec = self.registry.resolve(name)
        if spec is None and name not in CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered motion tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if spec is not None:
            allowed.update(
                {
                    "translation_delta_m",
                    "rotation_delta_axis_angle_deg",
                    "executor_config",
                }
            )
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError("motion tool call uses a stale observation")
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")

        delta = (0.0, 0.0, 0.0)
        rotation_delta = (0.0, 0.0, 0.0)
        executor_id: str | None = None
        executor_config: Mapping[str, Any] = {}
        if spec is not None:
            action = "execute"
            executor_id = spec.executor_id
            executor_config = spec.validate_configuration(
                arguments.get("executor_config")
            )
            if "translation_delta_m" in arguments:
                delta = _finite_vector(
                    arguments["translation_delta_m"], "translation_delta_m"
                )
                magnitude = math.sqrt(
                    sum(component * component for component in delta)
                )
                if magnitude > self.maximum_correction_m + 1.0e-9:
                    raise MotionToolValidationError(
                        f"requested correction {magnitude:.4f} m exceeds the "
                        f"{self.maximum_correction_m:.4f} m safety limit"
                    )
            if "rotation_delta_axis_angle_deg" in arguments:
                rotation_delta = _finite_vector(
                    arguments["rotation_delta_axis_angle_deg"],
                    "rotation_delta_axis_angle_deg",
                )
                rotation_magnitude = math.sqrt(
                    sum(component * component for component in rotation_delta)
                )
                if (
                    rotation_magnitude
                    > self.maximum_rotation_correction_deg + 1.0e-9
                ):
                    raise MotionToolValidationError(
                        f"requested rotation correction {rotation_magnitude:.2f} "
                        f"degrees exceeds the "
                        f"{self.maximum_rotation_correction_deg:.2f} degree "
                        "safety limit"
                    )
        else:
            action = "hold" if name == "hold_motion" else "abort"
        target_after = tuple(
            before + change for before, change in zip(self.current_target_m, delta)
        )
        target_after_quaternion = _quaternion_multiply_wxyz(
            _axis_angle_degrees_quaternion_wxyz(rotation_delta),
            self.current_target_quaternion_wxyz,
        )
        return MotionToolOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            executor_id=executor_id,
            executor_config=executor_config,
            target_before_m=self.current_target_m,
            target_after_m=target_after,
            requested_translation_delta_m=delta,
            target_before_quaternion_wxyz=self.current_target_quaternion_wxyz,
            target_after_quaternion_wxyz=target_after_quaternion,
            requested_rotation_delta_axis_angle_deg=rotation_delta,
        )


@dataclass(frozen=True)
class ActuatorExecutorSpec:
    """One runtime-advertised actuator and its configurable command surface."""

    executor_id: str
    tool_name: str
    description: str
    command_schema: Mapping[str, Any]
    configuration_schema: Mapping[str, Any]
    capability_tags: tuple[str, ...] = ()
    semantic_command_bindings: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        _required_text(self.executor_id, "executor_id")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME.fullmatch(
            self.tool_name
        ):
            raise MotionToolValidationError("tool_name has an invalid format")
        if self.tool_name in CONTROL_TOOL_NAMES | ACTUATOR_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError("executor tool collides with a control tool")
        _required_text(self.description, "description")
        for path, schema in (
            ("command_schema", self.command_schema),
            ("configuration_schema", self.configuration_schema),
        ):
            if not isinstance(schema, Mapping) or schema.get("type") != "object":
                raise MotionToolValidationError(f"{path} must describe an object")
            _copy_json(schema, path)
        if len(set(self.capability_tags)) != len(self.capability_tags):
            raise MotionToolValidationError(
                "capability_tags must not contain duplicates"
            )
        for index, tag in enumerate(self.capability_tags):
            if not isinstance(tag, str) or not _CAPABILITY_TAG.fullmatch(tag):
                raise MotionToolValidationError(
                    f"capability_tags[{index}] has an invalid format"
                )
        if not isinstance(self.semantic_command_bindings, Mapping):
            raise MotionToolValidationError(
                "semantic_command_bindings must be an object"
            )
        for effect_id, command in self.semantic_command_bindings.items():
            if effect_id not in self.capability_tags:
                raise MotionToolValidationError(
                    "semantic command binding must name an advertised capability tag"
                )
            self._validate_object(
                command,
                self.command_schema,
                f"semantic_command_bindings.{effect_id}",
                optional=False,
            )

    def advertisement(self) -> dict[str, Any]:
        """Describe runtime semantics without selecting an embodiment."""
        return {
            "executor_id": self.executor_id,
            "tool_name": self.tool_name,
            "tool_family": "actuator",
            "capability_tags": list(self.capability_tags),
            "command_schema": _copy_json(self.command_schema, "command_schema"),
            "invocation_schema": _copy_json(
                self.command_schema, "command_schema"
            ),
            "configuration_schema": _copy_json(
                self.configuration_schema, "configuration_schema"
            ),
            "semantic_command_bindings": _copy_json(
                self.semantic_command_bindings,
                "semantic_command_bindings",
            ),
        }

    @staticmethod
    def _validate_object(
        value: Any,
        schema: Mapping[str, Any],
        path: str,
        *,
        optional: bool,
    ) -> dict[str, Any]:
        if value is None and optional:
            return {}
        if not isinstance(value, Mapping):
            raise MotionToolValidationError(f"{path} must be an object")
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise MotionToolValidationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"{path} is missing fields: {sorted(missing)}"
            )
        return {
            key: _validate_config_value(item, properties[key], f"{path}.{key}")
            if key in properties
            else _copy_json(item, f"{path}.{key}")
            for key, item in value.items()
        }

    def validate_command(self, value: Any) -> dict[str, Any]:
        return self._validate_object(
            value, self.command_schema, "command", optional=False
        )

    def validate_configuration(self, value: Any) -> dict[str, Any]:
        return self._validate_object(
            value,
            self.configuration_schema,
            "executor_config",
            optional=True,
        )

    def tool_schema(self, observation_id: str) -> dict[str, Any]:
        properties = _common_properties(observation_id)
        properties.update(
            {
                "command": _copy_json(self.command_schema, "command_schema"),
                "executor_config": _copy_json(
                    self.configuration_schema, "configuration_schema"
                ),
            }
        )
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": [
                        "observation_id",
                        "confidence",
                        "reason",
                        "command",
                    ],
                },
            },
        }


class ActuatorExecutorRegistry:
    """Runtime discovery and resolution for actuator executors."""

    def __init__(self) -> None:
        self._by_tool_name: dict[str, ActuatorExecutorSpec] = {}
        self._executor_ids: set[str] = set()

    def register(self, spec: ActuatorExecutorSpec) -> None:
        if not isinstance(spec, ActuatorExecutorSpec):
            raise MotionToolValidationError("executor registration requires a spec")
        if spec.tool_name in self._by_tool_name:
            raise MotionToolValidationError(
                f"executor tool {spec.tool_name!r} is already registered"
            )
        if spec.executor_id in self._executor_ids:
            raise MotionToolValidationError(
                f"executor id {spec.executor_id!r} is already registered"
            )
        self._by_tool_name[spec.tool_name] = spec
        self._executor_ids.add(spec.executor_id)

    def resolve(self, tool_name: str) -> ActuatorExecutorSpec | None:
        return self._by_tool_name.get(tool_name)

    def specs(self) -> tuple[ActuatorExecutorSpec, ...]:
        return tuple(self._by_tool_name[name] for name in sorted(self._by_tool_name))

    def advertisement(self) -> tuple[dict[str, Any], ...]:
        return tuple(spec.advertisement() for spec in self.specs())


def actuator_tool_schemas(
    observation_id: str, registry: ActuatorExecutorRegistry
) -> list[dict[str, Any]]:
    """Advertise runtime actuator executors plus hold/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    if not isinstance(registry, ActuatorExecutorRegistry):
        raise MotionToolValidationError("registry must be an ActuatorExecutorRegistry")
    schemas = [spec.tool_schema(observation_id) for spec in registry.specs()]
    common = _common_properties(observation_id)
    for name, description in (
        (
            "hold_actuation",
            "Maintain the current actuator command and require a new observation.",
        ),
        (
            "abort_actuation",
            "Abort the actuator transition because it is unsafe.",
        ),
    ):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": common,
                        "required": ["observation_id", "confidence", "reason"],
                    },
                },
            }
        )
    return schemas


@dataclass(frozen=True)
class ActuatorToolOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    executor_id: str | None
    command: Mapping[str, Any]
    executor_config: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "executor_id": self.executor_id,
            "command": _copy_json(self.command, "command"),
            "executor_config": _copy_json(
                self.executor_config, "executor_config"
            ),
        }


class ObservationBoundActuatorGate:
    """Validate and consume one actuator call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        registry: ActuatorExecutorRegistry,
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        if not isinstance(registry, ActuatorExecutorRegistry):
            raise MotionToolValidationError(
                "registry must be an ActuatorExecutorRegistry"
            )
        self.registry = registry
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> ActuatorToolOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one tool call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        spec = self.registry.resolve(name)
        if spec is None and name not in ACTUATOR_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered actuator tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if spec is not None:
            allowed.update({"command", "executor_config"})
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if spec is not None and "command" not in arguments:
            missing.add("command")
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError("actuator tool call uses a stale observation")
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")
        executor_id: str | None = None
        command: Mapping[str, Any] = {}
        executor_config: Mapping[str, Any] = {}
        if spec is not None:
            action = "execute"
            executor_id = spec.executor_id
            command = spec.validate_command(arguments["command"])
            executor_config = spec.validate_configuration(
                arguments.get("executor_config")
            )
        else:
            action = "hold" if name == "hold_actuation" else "abort"
        return ActuatorToolOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            executor_id=executor_id,
            command=command,
            executor_config=executor_config,
        )


def motion_report_yields_to_actuator(
    motion_report: Mapping[str, Any],
    *,
    actuator_transition_pending: bool,
) -> bool:
    """Return whether a model hold is a safe capability handoff, not failure."""
    if not actuator_transition_pending or not isinstance(motion_report, Mapping):
        return False
    recovery = motion_report.get("recovery_request")
    if not isinstance(recovery, Mapping):
        return False
    if recovery.get("reason") != "model_requested_hold":
        return False
    decision = recovery.get("coach_decision")
    if not isinstance(decision, Mapping):
        return False
    tool = decision.get("motion_tool")
    return isinstance(tool, Mapping) and tool.get("action") == "hold"


@dataclass(frozen=True)
class OperationCandidate:
    """One runtime-proposed next operation, independent of its implementation."""

    operation_id: str
    kind: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not _OPERATION_ID.fullmatch(
            self.operation_id
        ):
            raise MotionToolValidationError("operation_id has an invalid format")
        _required_text(self.kind, "kind")
        _required_text(self.description, "description")

    def to_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "description": self.description,
        }


def _normalize_operation_candidates(
    candidates: Sequence[OperationCandidate],
) -> tuple[OperationCandidate, ...]:
    normalized = tuple(candidates)
    if not normalized:
        raise MotionToolValidationError("at least one operation candidate is required")
    if any(not isinstance(item, OperationCandidate) for item in normalized):
        raise MotionToolValidationError(
            "candidates must contain OperationCandidate values"
        )
    operation_ids = [item.operation_id for item in normalized]
    if len(operation_ids) != len(set(operation_ids)):
        raise MotionToolValidationError("operation candidate ids must be unique")
    return normalized


def operation_scheduler_tool_schemas(
    observation_id: str,
    candidates: Sequence[OperationCandidate],
) -> list[dict[str, Any]]:
    """Advertise current runtime operations plus observe/complete/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    normalized = _normalize_operation_candidates(candidates)
    common = _common_properties(observation_id)
    dispatch_properties = dict(common)
    dispatch_properties["operation_id"] = {
        "type": "string",
        "enum": [item.operation_id for item in normalized],
        "description": "One operation advertised for this fresh observation.",
    }
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "dispatch_operation",
                "description": (
                    "Dispatch exactly one of the runtime-advertised next operations."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": dispatch_properties,
                    "required": [
                        "observation_id",
                        "operation_id",
                        "confidence",
                        "reason",
                    ],
                },
            },
        }
    ]
    for name, description in (
        (
            "observe_again",
            "Preserve current commands and require a new observation before scheduling.",
        ),
        (
            "complete_task",
            "Declare that the human instruction is already physically complete.",
        ),
        ("abort_task", "Abort because no advertised operation is currently safe."),
    ):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": common,
                        "required": ["observation_id", "confidence", "reason"],
                    },
                },
            }
        )
    return schemas


@dataclass(frozen=True)
class ScheduledOperationOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    operation_id: str | None
    operation_kind: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
        }


class ObservationBoundOperationGate:
    """Consume one scheduling call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        candidates: Sequence[OperationCandidate],
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        normalized = _normalize_operation_candidates(candidates)
        self._by_id = {item.operation_id: item for item in normalized}
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> ScheduledOperationOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one scheduler call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        if name != "dispatch_operation" and name not in SCHEDULER_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered scheduler tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if name == "dispatch_operation":
            allowed.add("operation_id")
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if name == "dispatch_operation" and "operation_id" not in arguments:
            missing.add("operation_id")
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError(
                "scheduler tool call uses a stale observation"
            )
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")
        candidate = None
        if name == "dispatch_operation":
            operation_id = _required_text(arguments["operation_id"], "operation_id")
            candidate = self._by_id.get(operation_id)
            if candidate is None:
                raise MotionToolValidationError(
                    f"operation {operation_id!r} was not advertised"
                )
            action = "dispatch"
        else:
            action = {
                "observe_again": "observe",
                "complete_task": "complete",
                "abort_task": "abort",
            }[name]
        return ScheduledOperationOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            operation_id=None if candidate is None else candidate.operation_id,
            operation_kind=None if candidate is None else candidate.kind,
        )


def motion_report_yields_to_scheduler(motion_report: Mapping[str, Any]) -> bool:
    """Return whether an actual model hold should yield to fresh scheduling."""
    if not isinstance(motion_report, Mapping):
        return False
    recovery = motion_report.get("recovery_request")
    if not isinstance(recovery, Mapping):
        return False
    if recovery.get("reason") != "model_requested_hold":
        return False
    decision = recovery.get("coach_decision")
    if not isinstance(decision, Mapping):
        return False
    tool = decision.get("motion_tool")
    return isinstance(tool, Mapping) and tool.get("action") == "hold"


def motion_checkpoint_scheduler_handoff_reason(
    checkpoint: Mapping[str, Any],
) -> str | None:
    """Return a local kinematic invalidation needing fresh scheduling.

    A stall or divergence is not evidence that the current operation should
    remain motion. Yielding lets the observation-bound scheduler choose from
    every currently admissible runtime operation, including actuator evaluation.
    """
    if not isinstance(checkpoint, Mapping):
        return None
    reason = checkpoint.get("reason")
    if not isinstance(reason, str):
        return None
    prefix = "lease_invalidated:"
    if not reason.startswith(prefix):
        return None
    invalidations = {
        item.strip() for item in reason[len(prefix) :].split(",") if item.strip()
    }
    for invalidation in (
        "motion_execution_diverged",
        "motion_orientation_diverged",
        "motion_progress_stalled",
    ):
        if invalidation in invalidations:
            return invalidation
    return None


def stalled_motion_checkpoint_yields_to_scheduler(
    checkpoint: Mapping[str, Any],
) -> bool:
    """Return whether local kinematic evidence needs fresh scheduling."""
    return motion_checkpoint_scheduler_handoff_reason(checkpoint) is not None


def recovery_motion_handoff_from_report(
    motion_report: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Compact a stopped motion report for the next model-bound recovery.

    Iteration histories can be large, but the next fresh-observation decision
    needs the attempted target, measured residual, and local stop evidence to
    avoid proposing effectively identical invalidated geometry.
    """
    if not isinstance(motion_report, Mapping):
        return None
    recovery = motion_report.get("recovery_request")
    if not isinstance(recovery, Mapping):
        return None
    iterations = motion_report.get("iterations")
    last_iteration = (
        iterations[-1]
        if isinstance(iterations, Sequence)
        and not isinstance(iterations, (str, bytes))
        and iterations
        and isinstance(iterations[-1], Mapping)
        else {}
    )
    executor_config = motion_report.get("executor_config")
    if not isinstance(executor_config, Mapping):
        executor_config = {}
    return {
        "phase": _copy_json(motion_report.get("phase")),
        "attempted_target_xyz_m": _copy_json(
            motion_report.get("target_xyz")
        ),
        "attempted_target_quaternion_wxyz": _copy_json(
            motion_report.get("target_quaternion_wxyz")
        ),
        "eef_start_xyz_m": _copy_json(motion_report.get("eef_start_xyz")),
        "eef_final_xyz_m": _copy_json(motion_report.get("eef_final_xyz")),
        "target_error_before_m": _copy_json(
            motion_report.get("target_error_before_m")
        ),
        "target_error_after_m": _copy_json(
            motion_report.get("target_error_after_m")
        ),
        "orientation_error_after_deg": _copy_json(
            motion_report.get("orientation_error_after_deg")
        ),
        "position_tolerance_m": _copy_json(
            executor_config.get("position_tolerance_m")
        ),
        "orientation_tolerance_deg": _copy_json(
            executor_config.get("orientation_tolerance_deg")
        ),
        "stopped_reason": _copy_json(recovery.get("reason")),
        "lease_invalidation_reason": _copy_json(
            recovery.get("lease_invalidation_reason")
        ),
        "last_measured_progress_m": _copy_json(
            last_iteration.get("measured_target_progress_m")
        ),
        "last_stalled_observation_count": _copy_json(
            last_iteration.get("stalled_observation_count")
        ),
    }


def runtime_transition_motion_handoff(
    motion_report: Mapping[str, Any],
    *,
    admission_before: Mapping[str, Any],
    admission_after: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Expose an ineffective transition motion to the next model call.

    A target can be inside the executor's pose tolerance and therefore report
    convergence without changing any capability evidence.  At a pending
    runtime transition that is a no-op, not progress.  Convert it to the same
    compact stalled-target contract used by physical motion invalidations so
    the next proposal must be materially different.
    """
    for value, path in (
        (motion_report, "motion_report"),
        (admission_before, "admission_before"),
        (admission_after, "admission_after"),
    ):
        if not isinstance(value, Mapping):
            raise MotionToolValidationError(f"{path} must be an object")

    measured_handoff = recovery_motion_handoff_from_report(motion_report)
    if measured_handoff is not None:
        return measured_handoff
    if admission_after.get("admitted") is True:
        return None
    missing_before = admission_before.get("missing_evidence")
    missing_after = admission_after.get("missing_evidence")
    if (
        not isinstance(missing_before, Sequence)
        or isinstance(missing_before, (str, bytes))
        or not isinstance(missing_after, Sequence)
        or isinstance(missing_after, (str, bytes))
        or list(missing_before) != list(missing_after)
    ):
        return None

    eef_start = _finite_vector(
        motion_report.get("eef_start_xyz"),
        "motion_report.eef_start_xyz",
    )
    eef_final = _finite_vector(
        motion_report.get("eef_final_xyz"),
        "motion_report.eef_final_xyz",
    )
    executor_config = motion_report.get("executor_config")
    if not isinstance(executor_config, Mapping):
        raise MotionToolValidationError(
            "motion_report.executor_config must be an object"
        )
    position_tolerance = executor_config.get("position_tolerance_m")
    orientation_tolerance = executor_config.get("orientation_tolerance_deg")
    for value, path in (
        (position_tolerance, "motion_report.executor_config.position_tolerance_m"),
        (
            orientation_tolerance,
            "motion_report.executor_config.orientation_tolerance_deg",
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise MotionToolValidationError(
                f"{path} must be a finite non-negative number"
            )
    measured_displacement = math.sqrt(
        sum(
            (final_component - start_component) ** 2
            for start_component, final_component in zip(eef_start, eef_final)
        )
    )
    if measured_displacement > float(position_tolerance):
        return None

    return {
        "phase": _copy_json(motion_report.get("phase")),
        "attempted_target_xyz_m": _copy_json(
            motion_report.get("target_xyz")
        ),
        "attempted_target_quaternion_wxyz": _copy_json(
            motion_report.get("target_quaternion_wxyz")
        ),
        "eef_start_xyz_m": list(eef_start),
        "eef_final_xyz_m": list(eef_final),
        "target_error_before_m": _copy_json(
            motion_report.get("target_error_before_m")
        ),
        "target_error_after_m": _copy_json(
            motion_report.get("target_error_after_m")
        ),
        "orientation_error_after_deg": _copy_json(
            motion_report.get("orientation_error_after_deg")
        ),
        "position_tolerance_m": float(position_tolerance),
        "orientation_tolerance_deg": float(orientation_tolerance),
        "stopped_reason": (
            "runtime_capability_unchanged_after_converged_noop"
        ),
        "lease_invalidation_reason": (
            "lease_invalidated:motion_progress_stalled"
        ),
        "measured_eef_displacement_m": measured_displacement,
        "missing_capability_evidence": list(missing_after),
    }


def retained_contact_supports_loaded_actuator(
    contact_observation: Mapping[str, Any] | None,
    *,
    maximum_pairwise_force_direction_cosine: float = 0.25,
    minimum_force_magnitude_ratio: float = 0.15,
) -> bool:
    """Return whether measured contact supports preserving a loaded actuator.

    A single-channel actuator may retain its load from one measured contact.
    When a runtime exposes multiple contact channels, at least two must be
    active and their forces must be sufficiently opposed and balanced.  The
    rule consumes runtime sensor topology rather than an embodiment name.
    """
    for name, value in (
        (
            "maximum_pairwise_force_direction_cosine",
            maximum_pairwise_force_direction_cosine,
        ),
        ("minimum_force_magnitude_ratio", minimum_force_magnitude_ratio),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be finite")
        if not math.isfinite(float(value)):
            raise MotionToolValidationError(f"{name} must be finite")
    if not -1.0 <= float(maximum_pairwise_force_direction_cosine) <= 1.0:
        raise MotionToolValidationError(
            "maximum_pairwise_force_direction_cosine must be in [-1, 1]"
        )
    if not 0.0 <= float(minimum_force_magnitude_ratio) <= 1.0:
        raise MotionToolValidationError(
            "minimum_force_magnitude_ratio must be in [0, 1]"
        )
    if not isinstance(contact_observation, Mapping):
        return False
    if not bool(contact_observation.get("available")) or not bool(
        contact_observation.get("touch")
    ):
        return False
    bodies = contact_observation.get("contact_bodies")
    if not isinstance(bodies, Mapping) or not bool(bodies.get("available")):
        # Preserve compatibility with actuators whose sensor does not expose
        # per-contact-body channels.
        return True
    channels = bodies.get("channels")
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        return False
    if len(channels) <= 1:
        return True
    active_body_count = bodies.get("active_body_count")
    if isinstance(active_body_count, bool) or not isinstance(
        active_body_count, (int, float)
    ):
        return False
    if int(active_body_count) < 2:
        return False
    cosine = bodies.get("pairwise_force_direction_cosine")
    ratio = bodies.get("force_magnitude_ratio_min_over_max")
    if (
        isinstance(cosine, bool)
        or not isinstance(cosine, (int, float))
        or isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
    ):
        return False
    if not math.isfinite(float(cosine)) or not math.isfinite(float(ratio)):
        return False
    return (
        float(cosine) <= float(maximum_pairwise_force_direction_cosine)
        and float(ratio) >= float(minimum_force_magnitude_ratio)
    )


def runtime_transition_admission(
    required_capability: str,
    *,
    actuator_engaged: bool,
    retained_contact_observed: bool,
    interaction_candidate_observed: bool,
    interaction_confirmed_observed: bool,
    actuator_disengaged_observed: bool,
) -> dict[str, Any]:
    """Admit a runtime transition from measured capability evidence.

    The scheduler supplies the next operation while this gate prevents a
    legacy phase scaffold from becoming control authority.  Requirements are
    capability predicates rather than task, object, phase, or embodiment
    names, so another runtime can publish the same evidence through different
    adapters.
    """
    required_capability = _required_text(
        required_capability, "required_capability"
    )
    evidence = {
        "actuator_engaged": actuator_engaged,
        "retained_contact_observed": retained_contact_observed,
        "interaction_candidate_observed": interaction_candidate_observed,
        "interaction_confirmed_observed": interaction_confirmed_observed,
        "actuator_disengaged_observed": actuator_disengaged_observed,
    }
    for name, value in evidence.items():
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{name} must be boolean")

    if required_capability == "supported_loaded_interaction":
        requirements = {
            "actuator_engaged": actuator_engaged,
            "retained_contact_observed": retained_contact_observed,
            "interaction_geometry_observed": bool(
                interaction_candidate_observed
                or interaction_confirmed_observed
            ),
        }
    elif required_capability == "released_interaction":
        requirements = {
            "actuator_command_disengaged": not actuator_engaged,
            "actuator_disengaged_observed": actuator_disengaged_observed,
            "loaded_contact_absent": not retained_contact_observed,
        }
    else:
        raise MotionToolValidationError(
            "required_capability must be supported_loaded_interaction or "
            "released_interaction"
        )

    missing = [name for name, satisfied in requirements.items() if not satisfied]
    return {
        "required_capability": required_capability,
        "admitted": not missing,
        "missing_evidence": missing,
        "requirements": requirements,
        "evidence": evidence,
        "authority": "fresh_runtime_capability_evidence",
    }


def actuator_command_outcome_invalidation_reason(
    *,
    requested_state: str,
    actuator_position_changed: bool,
    loaded_contact_supported: bool,
) -> str | None:
    """Describe a measured actuator command failure that requires re-observation."""
    if requested_state not in {"engage", "disengage", "maintain"}:
        raise MotionToolValidationError(
            "requested_state must be engage, disengage, or maintain"
        )
    for name, value in (
        ("actuator_position_changed", actuator_position_changed),
        ("loaded_contact_supported", loaded_contact_supported),
    ):
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{name} must be boolean")
    if (
        requested_state == "engage"
        and not actuator_position_changed
        and not loaded_contact_supported
    ):
        return "engagement_produced_no_motion_or_supported_loaded_contact"
    return None


def actuator_transition_is_admissible(
    *,
    actuator_engaged: bool,
    goal_contact_observed: bool,
    retained_contact_observed: bool,
    measured_actuator_outcome_invalidated: bool = False,
    failed_grasp_pose_lease_released: bool = True,
    interaction_distance_m: float,
    maximum_interaction_distance_m: float = 0.02,
) -> bool:
    """Admit actuator evaluation only when fresh physical preconditions allow it.

    The rule is task-neutral: an engaged actuator stays loaded while contact is
    retained away from the goal, but may transition at the goal, after contact
    loss, or after a measured outcome invalidates the retained contact as an
    effective actuation. A disengaged actuator is offered only at retained touch
    or inside the runtime interaction envelope; otherwise motion must establish
    proximity.
    """
    for name, value in (
        ("actuator_engaged", actuator_engaged),
        ("goal_contact_observed", goal_contact_observed),
        ("retained_contact_observed", retained_contact_observed),
        (
            "measured_actuator_outcome_invalidated",
            measured_actuator_outcome_invalidated,
        ),
        ("failed_grasp_pose_lease_released", failed_grasp_pose_lease_released),
    ):
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{name} must be boolean")
    for name, value in (
        ("interaction_distance_m", interaction_distance_m),
        ("maximum_interaction_distance_m", maximum_interaction_distance_m),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be finite")
        if not math.isfinite(float(value)):
            raise MotionToolValidationError(f"{name} must be finite")
    if interaction_distance_m < 0 or maximum_interaction_distance_m <= 0:
        raise MotionToolValidationError(
            "interaction distance must be non-negative and its maximum positive"
        )
    if actuator_engaged:
        return (
            goal_contact_observed
            or not retained_contact_observed
            or measured_actuator_outcome_invalidated
        )
    if not failed_grasp_pose_lease_released:
        return False
    return (
        retained_contact_observed
        or interaction_distance_m <= maximum_interaction_distance_m
    )


@dataclass(frozen=True)
class ActuatorFeedbackEventPolicy:
    """Configurable trigger for returning changed physical state to a governor."""

    minimum_position_change: float
    minimum_force_change_n: float

    def __post_init__(self) -> None:
        for name, value, allow_zero in (
            ("minimum_position_change", self.minimum_position_change, False),
            ("minimum_force_change_n", self.minimum_force_change_n, True),
        ):
            value = float(value)
            if not math.isfinite(value) or value < 0.0 or (
                not allow_zero and value == 0.0
            ):
                qualifier = "non-negative" if allow_zero else "positive"
                raise MotionToolValidationError(f"{name} must be finite and {qualifier}")


@dataclass(frozen=True)
class ActuatorFeedbackEvent:
    triggered: bool
    actuator_position_changed: bool
    tactile_changed: bool
    actuator_position_change: float
    tactile_force_change_n: float
    touch_changed: bool
    policy: ActuatorFeedbackEventPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "actuator_position_changed": self.actuator_position_changed,
            "tactile_changed": self.tactile_changed,
            "actuator_position_change": self.actuator_position_change,
            "tactile_force_change_n": self.tactile_force_change_n,
            "touch_changed": self.touch_changed,
            "policy": {
                "minimum_position_change": self.policy.minimum_position_change,
                "minimum_force_change_n": self.policy.minimum_force_change_n,
            },
        }


def assess_actuator_feedback_event(
    *,
    position_before: float,
    position_after: float,
    force_before_n: float,
    force_after_n: float,
    touch_before: bool,
    touch_after: bool,
    policy: ActuatorFeedbackEventPolicy,
) -> ActuatorFeedbackEvent:
    """Fuse actuator displacement and tactile change into one re-observe event."""
    if not isinstance(policy, ActuatorFeedbackEventPolicy):
        raise MotionToolValidationError(
            "policy must be an ActuatorFeedbackEventPolicy"
        )
    numeric: dict[str, float] = {}
    for name, value in (
        ("position_before", position_before),
        ("position_after", position_after),
        ("force_before_n", force_before_n),
        ("force_after_n", force_after_n),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be a finite number")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{name} must be a finite number")
        numeric[name] = value
    if not isinstance(touch_before, bool) or not isinstance(touch_after, bool):
        raise MotionToolValidationError("touch values must be boolean")
    position_change = abs(numeric["position_after"] - numeric["position_before"])
    force_change = abs(numeric["force_after_n"] - numeric["force_before_n"])
    touch_changed = touch_before != touch_after
    position_changed = position_change >= policy.minimum_position_change
    tactile_changed = touch_changed or force_change >= policy.minimum_force_change_n
    return ActuatorFeedbackEvent(
        triggered=position_changed and tactile_changed,
        actuator_position_changed=position_changed,
        tactile_changed=tactile_changed,
        actuator_position_change=position_change,
        tactile_force_change_n=force_change,
        touch_changed=touch_changed,
        policy=policy,
    )


@dataclass(frozen=True)
class MotionLeaseConditions:
    """Runtime-evaluable invariants attached to a longer-lived motion call."""

    require_contact: bool = False
    minimum_contact_force_n: float = 0.0
    maximum_tracked_pose_error_m: float | None = None
    minimum_observed_clearance_m: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.require_contact, bool):
            raise MotionToolValidationError("require_contact must be boolean")
        force = float(self.minimum_contact_force_n)
        if not math.isfinite(force) or force < 0.0:
            raise MotionToolValidationError(
                "minimum_contact_force_n must be finite and non-negative"
            )
        for name, value in (
            ("maximum_tracked_pose_error_m", self.maximum_tracked_pose_error_m),
            ("minimum_observed_clearance_m", self.minimum_observed_clearance_m),
        ):
            if value is None:
                continue
            value = float(value)
            if not math.isfinite(value) or value < 0.0:
                raise MotionToolValidationError(
                    f"{name} must be finite and non-negative when supplied"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_contact": self.require_contact,
            "minimum_contact_force_n": self.minimum_contact_force_n,
            "maximum_tracked_pose_error_m": self.maximum_tracked_pose_error_m,
            "minimum_observed_clearance_m": self.minimum_observed_clearance_m,
        }


@dataclass(frozen=True)
class MotionLeaseAssessment:
    valid: bool
    invalidation_reasons: tuple[str, ...]
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "invalidation_reasons": list(self.invalidation_reasons),
            "evidence": _copy_json(self.evidence, "evidence"),
        }


def assess_motion_lease(
    conditions: MotionLeaseConditions,
    *,
    contact_available: bool,
    touch: bool | None,
    contact_force_n: float | None,
    tracked_pose_error_m: float | None,
    observed_clearance_m: float | None,
) -> MotionLeaseAssessment:
    """Evaluate advertised lease conditions without task or executor knowledge."""
    if not isinstance(conditions, MotionLeaseConditions):
        raise MotionToolValidationError("conditions must be MotionLeaseConditions")
    if not isinstance(contact_available, bool):
        raise MotionToolValidationError("contact_available must be boolean")
    if touch is not None and not isinstance(touch, bool):
        raise MotionToolValidationError("touch must be boolean or null")

    numeric: dict[str, float | None] = {}
    for name, value in (
        ("contact_force_n", contact_force_n),
        ("tracked_pose_error_m", tracked_pose_error_m),
        ("observed_clearance_m", observed_clearance_m),
    ):
        if value is None:
            numeric[name] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be finite or null")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{name} must be finite or null")
        numeric[name] = value

    reasons: list[str] = []
    if conditions.require_contact:
        if not contact_available:
            reasons.append("contact_observation_unavailable")
        elif touch is not True:
            reasons.append("contact_lost")
        elif numeric["contact_force_n"] is None:
            reasons.append("contact_force_unavailable")
        elif numeric["contact_force_n"] < conditions.minimum_contact_force_n:
            reasons.append("contact_force_below_lease_minimum")
    if conditions.maximum_tracked_pose_error_m is not None:
        tracked_error = numeric["tracked_pose_error_m"]
        if tracked_error is None:
            reasons.append("tracked_pose_unavailable")
        elif tracked_error > conditions.maximum_tracked_pose_error_m:
            reasons.append("tracked_pose_error_exceeded")
    if conditions.minimum_observed_clearance_m is not None:
        clearance = numeric["observed_clearance_m"]
        if clearance is None:
            reasons.append("observed_clearance_unavailable")
        elif clearance < conditions.minimum_observed_clearance_m:
            reasons.append("observed_clearance_below_lease_minimum")
    evidence = {
        "contact_available": contact_available,
        "touch": touch,
        **numeric,
        "conditions": conditions.to_dict(),
    }
    return MotionLeaseAssessment(
        valid=not reasons,
        invalidation_reasons=tuple(reasons),
        evidence=evidence,
    )


def motion_lease_source_errors(
    conditions: MotionLeaseConditions,
    *,
    contact_available: bool,
    tracked_pose_available: bool,
    observed_clearance_available: bool,
) -> tuple[str, ...]:
    """Reject lease conditions whose required observation source is unavailable."""
    if not isinstance(conditions, MotionLeaseConditions):
        raise MotionToolValidationError("conditions must be MotionLeaseConditions")
    for name, value in (
        ("contact_available", contact_available),
        ("tracked_pose_available", tracked_pose_available),
        ("observed_clearance_available", observed_clearance_available),
    ):
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{name} must be boolean")
    errors: list[str] = []
    if conditions.require_contact and not contact_available:
        errors.append("contact source is unavailable")
    if (
        conditions.maximum_tracked_pose_error_m is not None
        and not tracked_pose_available
    ):
        errors.append("tracked-pose source is unavailable")
    if (
        conditions.minimum_observed_clearance_m is not None
        and not observed_clearance_available
    ):
        errors.append("observed-clearance source is unavailable")
    return tuple(errors)
