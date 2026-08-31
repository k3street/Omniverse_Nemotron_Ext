"""Typed, RGB-D-grounded tool invocation proposals with no dispatch path.

The selected runtime tool owns its invocation schema and bounds.  This module
binds one shadow execution-lease proposal to fresh controlled-frame evidence,
RGB-D position anchors, and observed orientation axes.  It validates a complete
typed invocation but never receives a handler or issues the lease it references.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

try:
    from .observation_bound_motion_tools import (
        retained_contact_supports_loaded_actuator,
    )
    from .world_effect_execution_lease import (
        ShadowExecutionLeaseCandidateSet,
        ShadowExecutionLeaseDecision,
    )
    from .world_effect_operation_plan import PlanningWorldEffectProviderInstance
except ImportError:  # Script execution adds this directory directly to sys.path.
    from observation_bound_motion_tools import (  # type: ignore[no-redef]
        retained_contact_supports_loaded_actuator,
    )
    from world_effect_execution_lease import (  # type: ignore[no-redef]
        ShadowExecutionLeaseCandidateSet,
        ShadowExecutionLeaseDecision,
    )
    from world_effect_operation_plan import (  # type: ignore[no-redef]
        PlanningWorldEffectProviderInstance,
    )


WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION = "world-effect-tool-invocation.v1"
WORLD_EFFECT_TOOL_INVOCATION_DECISIONS = frozenset(
    {"propose_invocation", "observe_again", "blocked"}
)
RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION = "runtime-tool-observation.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectToolInvocationError(ValueError):
    """Raised when a proposed invocation exceeds its evidence or tool schema."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectToolInvocationError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectToolInvocationError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectToolInvocationError("confidence must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectToolInvocationError("confidence must be a number in [0, 1]")
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldEffectToolInvocationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectToolInvocationError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _json_copy(item, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise WorldEffectToolInvocationError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectToolInvocationError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _vector(value: Any, length: int, path: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise WorldEffectToolInvocationError(
            f"{path} must contain exactly {length} numbers"
        )
    result: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise WorldEffectToolInvocationError(f"{path}[{index}] must be a number")
        numeric = float(item)
        if not math.isfinite(numeric):
            raise WorldEffectToolInvocationError(f"{path}[{index}] must be finite")
        result.append(numeric)
    return tuple(result)


def _normalize(value: Sequence[float], path: str) -> tuple[float, ...]:
    norm = math.sqrt(sum(component * component for component in value))
    if norm <= 1.0e-9:
        raise WorldEffectToolInvocationError(f"{path} must have non-zero magnitude")
    return tuple(component / norm for component in value)


def _rotate_wxyz(
    quaternion: Sequence[float], vector: Sequence[float]
) -> tuple[float, float, float]:
    w, x, y, z = quaternion
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _validate_schema_value(value: Any, schema: Mapping[str, Any], path: str) -> Any:
    expected = schema.get("type")
    if isinstance(expected, list):
        if value is None and "null" in expected:
            return None
        choices = [item for item in expected if item != "null"]
        if len(choices) != 1:
            raise WorldEffectToolInvocationError(f"{path} has unsupported union type")
        expected = choices[0]
    if expected == "object":
        if not isinstance(value, Mapping):
            raise WorldEffectToolInvocationError(f"{path} must be an object")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise WorldEffectToolInvocationError(f"{path}.properties must be an object")
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise WorldEffectToolInvocationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectToolInvocationError(
                f"{path} is missing fields: {sorted(missing)}"
            )
        return {
            key: _validate_schema_value(item, properties[key], f"{path}.{key}")
            if key in properties
            else _json_copy(item, f"{path}.{key}")
            for key, item in value.items()
        }
    if expected == "array":
        if not isinstance(value, (list, tuple)):
            raise WorldEffectToolInvocationError(f"{path} must be an array")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise WorldEffectToolInvocationError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise WorldEffectToolInvocationError(f"{path} has too many items")
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise WorldEffectToolInvocationError(f"{path}.items must be an object")
        return [
            _validate_schema_value(item, item_schema, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorldEffectToolInvocationError(f"{path} must be a number")
        result = float(value)
        if not math.isfinite(result):
            raise WorldEffectToolInvocationError(f"{path} must be finite")
        if "minimum" in schema and result < float(schema["minimum"]):
            raise WorldEffectToolInvocationError(f"{path} is below its minimum")
        if "maximum" in schema and result > float(schema["maximum"]):
            raise WorldEffectToolInvocationError(f"{path} exceeds its maximum")
        return result
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorldEffectToolInvocationError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise WorldEffectToolInvocationError(f"{path} is below its minimum")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise WorldEffectToolInvocationError(f"{path} exceeds its maximum")
        return value
    if expected == "boolean":
        if not isinstance(value, bool):
            raise WorldEffectToolInvocationError(f"{path} must be boolean")
        return value
    if expected == "string":
        if not isinstance(value, str):
            raise WorldEffectToolInvocationError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise WorldEffectToolInvocationError(f"{path} is not an allowed value")
        return value
    raise WorldEffectToolInvocationError(
        f"{path} uses unsupported schema type {expected!r}"
    )


def validate_materialized_invocation_arguments(
    candidate: "ShadowToolInvocationCandidate",
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate a completed runtime-owned invocation at an authority boundary."""
    if not isinstance(arguments, Mapping):
        raise WorldEffectToolInvocationError(
            "materialized invocation arguments must be an object"
        )
    validated = _validate_schema_value(
        arguments,
        candidate.invocation_schema,
        "materialized_invocation_arguments",
    )
    if not isinstance(validated, dict):
        raise WorldEffectToolInvocationError(
            "materialized invocation schema must produce an object"
        )
    return validated


@dataclass(frozen=True)
class PositionGroundingAnchor:
    anchor_id: str
    entity_id: str
    geometry_digest: str
    position_m: tuple[float, float, float]
    source_field: str
    offset_min_m: tuple[float, float, float]
    offset_max_m: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "entity_id": self.entity_id,
            "geometry_digest": self.geometry_digest,
            "position_m": list(self.position_m),
            "source_field": self.source_field,
            "offset_min_m": list(self.offset_min_m),
            "offset_max_m": list(self.offset_max_m),
        }


@dataclass(frozen=True)
class OrientationGroundingAxis:
    alignment_id: str
    entity_id: str
    geometry_digest: str
    axis_robot_root: tuple[float, float, float]
    source_field: str
    bidirectional: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "alignment_id": self.alignment_id,
            "entity_id": self.entity_id,
            "geometry_digest": self.geometry_digest,
            "axis_robot_root": list(self.axis_robot_root),
            "source_field": self.source_field,
            "bidirectional": self.bidirectional,
        }


@dataclass(frozen=True)
class ShadowToolInvocationCandidate:
    candidate_id: str
    lease_observation_id: str
    lease_id: str
    provider_instance_id: str
    operation_candidate_id: str
    tool_id: str
    tool_family: str
    purpose: str
    semantic_effect_id: str | None
    required_invocation_arguments: Mapping[str, Any]
    coordinate_frame: str
    invocation_schema: Mapping[str, Any]
    model_argument_schema: Mapping[str, Any]
    materialized_argument_fields: tuple[str, ...]
    invocation_constraints: Mapping[str, Any]
    tool_configuration: Mapping[str, Any]
    invalidation_condition_ids: tuple[str, ...]
    current_controlled_position_m: tuple[float, float, float]
    current_controlled_quaternion_wxyz: tuple[float, float, float, float]
    current_interaction_position_m: tuple[float, float, float] | None
    current_interaction_offsets_from_anchors: tuple[Mapping[str, Any], ...]
    interaction_origin_offset_local_m: tuple[float, float, float] | None
    interaction_alignment_axis_local: tuple[float, float, float] | None
    interaction_alignment_relation: str
    interaction_grasp_geometry: Mapping[str, Any]
    two_pad_grasp_alignment: Mapping[str, Any]
    retained_contact_supported: bool
    position_anchors: tuple[PositionGroundingAnchor, ...]
    orientation_axes: tuple[OrientationGroundingAxis, ...]

    @property
    def position_grounding_required(self) -> bool:
        properties = self.invocation_schema.get("properties", {})
        return bool(
            isinstance(properties, Mapping)
            and (
                "target_position_m" in properties
                or self.ordered_waypoint_grounding_required
            )
        )

    @property
    def orientation_grounding_required(self) -> bool:
        properties = self.invocation_schema.get("properties", {})
        return (
            isinstance(properties, Mapping)
            and (
                "target_quaternion_wxyz" in properties
                or self.ordered_waypoint_grounding_required
            )
        )

    @property
    def ordered_waypoint_grounding_required(self) -> bool:
        return (
            self.invocation_constraints.get("grounding_mode")
            == "ordered_waypoint_path"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "lease_observation_id": self.lease_observation_id,
            "lease_id": self.lease_id,
            "provider_instance_id": self.provider_instance_id,
            "operation_candidate_id": self.operation_candidate_id,
            "tool_id": self.tool_id,
            "tool_family": self.tool_family,
            "purpose": self.purpose,
            "semantic_effect_id": self.semantic_effect_id,
            "required_invocation_arguments": _json_copy(
                self.required_invocation_arguments,
                "required_invocation_arguments",
            ),
            "coordinate_frame": self.coordinate_frame,
            "invocation_schema": _json_copy(
                self.invocation_schema, "invocation_schema"
            ),
            "model_argument_schema": _json_copy(
                self.model_argument_schema, "model_argument_schema"
            ),
            "materialized_argument_fields": list(
                self.materialized_argument_fields
            ),
            "invocation_constraints": _json_copy(
                self.invocation_constraints, "invocation_constraints"
            ),
            "tool_configuration": _json_copy(
                self.tool_configuration, "tool_configuration"
            ),
            "invalidation_condition_ids": list(self.invalidation_condition_ids),
            "current_controlled_frame": {
                "position_m": list(self.current_controlled_position_m),
                "quaternion_wxyz": list(
                    self.current_controlled_quaternion_wxyz
                ),
            },
            "current_interaction_position_m": (
                None
                if self.current_interaction_position_m is None
                else list(self.current_interaction_position_m)
            ),
            "current_interaction_offsets_from_anchors": [
                _json_copy(item, "current_interaction_offset")
                for item in self.current_interaction_offsets_from_anchors
            ],
            "interaction_frame": {
                "origin_offset_local_m": (
                    None
                    if self.interaction_origin_offset_local_m is None
                    else list(self.interaction_origin_offset_local_m)
                ),
                "alignment_axis_local": (
                    None
                    if self.interaction_alignment_axis_local is None
                    else list(self.interaction_alignment_axis_local)
                ),
                "alignment_relation": self.interaction_alignment_relation,
                "grasp_geometry": _json_copy(
                    self.interaction_grasp_geometry,
                    "interaction_grasp_geometry",
                ),
                "two_pad_grasp_alignment": _json_copy(
                    self.two_pad_grasp_alignment,
                    "two_pad_grasp_alignment",
                ),
                "retained_contact_supported": self.retained_contact_supported,
            },
            "position_anchors": [item.to_dict() for item in self.position_anchors],
            "orientation_axes": [item.to_dict() for item in self.orientation_axes],
            "position_grounding_required": self.position_grounding_required,
            "orientation_grounding_required": self.orientation_grounding_required,
            "ordered_waypoint_grounding_required": (
                self.ordered_waypoint_grounding_required
            ),
            "execution_lease_issued": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class ShadowToolInvocationCandidateSet:
    observation_id: str
    runtime_observation_digest: str
    lease_id: str
    candidates: tuple[ShadowToolInvocationCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "runtime_observation_digest": self.runtime_observation_digest,
            "lease_id": self.lease_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "invocation_validated": False,
            "execution_lease_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def _position_anchors(binding: Any) -> list[PositionGroundingAnchor]:
    geometry = binding.geometry
    anchors: list[PositionGroundingAnchor] = []
    extent = geometry.get("visible_extent_base_m")
    if isinstance(extent, (list, tuple)):
        extent_vector = _vector(extent, 3, "geometry.visible_extent_base_m")
    else:
        extent_vector = (0.10, 0.10, 0.10)
    half_extent = tuple(0.5 * max(0.001, value) for value in extent_vector)
    center = geometry.get("center_base_m")
    if isinstance(center, (list, tuple)):
        anchors.append(
            PositionGroundingAnchor(
                anchor_id=f"{binding.entity_id}.center",
                entity_id=binding.entity_id,
                geometry_digest=binding.geometry_digest,
                position_m=_vector(center, 3, "geometry.center_base_m"),
                source_field="center_base_m",
                offset_min_m=(-half_extent[0], -half_extent[1], -half_extent[2]),
                offset_max_m=(half_extent[0], half_extent[1], 0.35),
            )
        )
    lower = geometry.get("visible_aabb_min_base_m")
    upper = geometry.get("visible_aabb_max_base_m")
    if isinstance(lower, (list, tuple)) and isinstance(upper, (list, tuple)):
        lower_vector = _vector(lower, 3, "geometry.visible_aabb_min_base_m")
        upper_vector = _vector(upper, 3, "geometry.visible_aabb_max_base_m")
        anchors.append(
            PositionGroundingAnchor(
                anchor_id=f"{binding.entity_id}.visible_aabb_top_center",
                entity_id=binding.entity_id,
                geometry_digest=binding.geometry_digest,
                position_m=(
                    0.5 * (lower_vector[0] + upper_vector[0]),
                    0.5 * (lower_vector[1] + upper_vector[1]),
                    upper_vector[2],
                ),
                source_field="visible_aabb_min_base_m+visible_aabb_max_base_m",
                offset_min_m=(-half_extent[0], -half_extent[1], 0.0),
                offset_max_m=(half_extent[0], half_extent[1], 0.35),
            )
        )
    return anchors


def _orientation_axes(
    binding: Any,
    alignment_relation: str,
) -> list[OrientationGroundingAxis]:
    axes: list[OrientationGroundingAxis] = []
    normal_raw = binding.geometry.get("support_plane_normal_base")
    support_normal: tuple[float, ...] | None = None
    if isinstance(normal_raw, (list, tuple)):
        try:
            support_normal = _normalize(
                _vector(normal_raw, 3, "geometry.support_plane_normal_base"),
                "geometry.support_plane_normal_base",
            )
        except WorldEffectToolInvocationError:
            support_normal = None

    def compatible(axis: Sequence[float]) -> bool:
        if support_normal is None or alignment_relation == "unrestricted":
            return True
        normal_component = abs(
            sum(left * right for left, right in zip(axis, support_normal))
        )
        if alignment_relation == "surface_tangent":
            return normal_component <= math.sin(math.radians(15.0))
        if alignment_relation == "surface_normal":
            return normal_component >= math.cos(math.radians(15.0))
        return False

    for field in (
        "oriented_footprint_axes_base",
        "principal_axes_base",
    ):
        raw_axes = binding.geometry.get(field)
        if not isinstance(raw_axes, (list, tuple)):
            continue
        for index, raw_axis in enumerate(raw_axes):
            try:
                axis = _normalize(
                    _vector(raw_axis, 3, f"geometry.{field}[{index}]"),
                    f"geometry.{field}[{index}]",
                )
            except WorldEffectToolInvocationError:
                continue
            if not compatible(axis):
                continue
            axes.append(
                OrientationGroundingAxis(
                    alignment_id=f"{binding.entity_id}.{field}.{index}",
                    entity_id=binding.entity_id,
                    geometry_digest=binding.geometry_digest,
                    axis_robot_root=axis,  # type: ignore[arg-type]
                    source_field=f"{field}[{index}]",
                )
            )
    if support_normal is not None and compatible(support_normal):
        axes.append(
            OrientationGroundingAxis(
                alignment_id=f"{binding.entity_id}.support_plane_normal_base",
                entity_id=binding.entity_id,
                geometry_digest=binding.geometry_digest,
                axis_robot_root=support_normal,  # type: ignore[arg-type]
                source_field="support_plane_normal_base",
            )
        )
    return axes


def build_shadow_tool_invocation_candidates(
    instance: PlanningWorldEffectProviderInstance,
    lease_candidates: ShadowExecutionLeaseCandidateSet,
    lease_decision: ShadowExecutionLeaseDecision,
    runtime_observation: Mapping[str, Any],
) -> ShadowToolInvocationCandidateSet:
    """Bind one validated lease proposal to fresh tool and frame evidence."""
    if lease_decision.decision != "propose_lease" or lease_decision.lease_id is None:
        raise WorldEffectToolInvocationError(
            "tool invocation requires a propose_lease shadow decision"
        )
    selected_lease = next(
        (
            item
            for item in lease_candidates.candidates
            if item.candidate_id == lease_decision.candidate_id
            and item.provider_instance_id == lease_decision.provider_instance_id
            and item.operation_candidate_id == lease_decision.operation_candidate_id
            and item.tool_id == lease_decision.tool_id
        ),
        None,
    )
    if selected_lease is None:
        raise WorldEffectToolInvocationError(
            "lease decision is absent from the exact candidate set"
        )
    if selected_lease.provider_instance_id != instance.instance_id:
        raise WorldEffectToolInvocationError(
            "lease candidate does not match provider instance"
        )
    activation = next(
        (
            item
            for item in instance.tool_activations
            if item.activated_tool_id == selected_lease.tool_id
            and item.requirement_id == selected_lease.requirement_id
        ),
        None,
    )
    if activation is None:
        raise WorldEffectToolInvocationError(
            "lease tool is absent from the planning provider instance"
        )
    invocation_schema = activation.tool_advertisement.get("invocation_schema")
    if not isinstance(invocation_schema, Mapping) or invocation_schema.get("type") != "object":
        raise WorldEffectToolInvocationError(
            "selected runtime tool does not advertise an invocation_schema"
        )
    invocation_schema = _json_copy(invocation_schema, "invocation_schema")
    constraints = invocation_schema.get("x-runtime-constraints", {})
    if not isinstance(constraints, Mapping):
        raise WorldEffectToolInvocationError(
            "invocation x-runtime-constraints must be an object"
        )
    constraints = _json_copy(constraints, "invocation_constraints")

    if not isinstance(runtime_observation, Mapping):
        raise WorldEffectToolInvocationError("runtime_observation must be an object")
    if runtime_observation.get("schema_version") != RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION:
        raise WorldEffectToolInvocationError("runtime observation schema_version mismatch")
    coordinate_frame = _identifier(
        runtime_observation.get("coordinate_frame"), "coordinate_frame"
    )
    constraint_frame = constraints.get("coordinate_frame")
    if constraint_frame is not None and constraint_frame != coordinate_frame:
        raise WorldEffectToolInvocationError(
            "runtime observation frame does not match tool constraint frame"
        )
    controlled = runtime_observation.get("controlled_frame")
    interaction = runtime_observation.get("interaction_frame")
    if not isinstance(controlled, Mapping) or not isinstance(interaction, Mapping):
        raise WorldEffectToolInvocationError(
            "runtime observation requires controlled_frame and interaction_frame"
        )
    current_position = _vector(
        controlled.get("position_m"), 3, "controlled_frame.position_m"
    )
    current_quaternion_raw = _vector(
        controlled.get("quaternion_wxyz"),
        4,
        "controlled_frame.quaternion_wxyz",
    )
    current_quaternion = _normalize(
        current_quaternion_raw, "controlled_frame.quaternion_wxyz"
    )
    raw_origin_offset = interaction.get("origin_offset_local_m")
    origin_offset = (
        None
        if raw_origin_offset is None
        else _vector(raw_origin_offset, 3, "interaction_frame.origin_offset_local_m")
    )
    raw_alignment_axis = interaction.get("alignment_axis_local")
    alignment_axis = (
        None
        if raw_alignment_axis is None
        else _normalize(
            _vector(
                raw_alignment_axis,
                3,
                "interaction_frame.alignment_axis_local",
            ),
            "interaction_frame.alignment_axis_local",
        )
    )
    alignment_relation = _identifier(
        interaction.get("alignment_relation", "unrestricted"),
        "interaction_frame.alignment_relation",
    )
    if alignment_relation not in {
        "surface_tangent",
        "surface_normal",
        "unrestricted",
    }:
        raise WorldEffectToolInvocationError(
            "unsupported interaction alignment relation"
        )
    raw_grasp_geometry = interaction.get("grasp_geometry", {})
    if not isinstance(raw_grasp_geometry, Mapping):
        raise WorldEffectToolInvocationError(
            "interaction_frame.grasp_geometry must be an object"
        )
    grasp_geometry = _json_copy(
        raw_grasp_geometry, "interaction_frame.grasp_geometry"
    )
    raw_grasp_alignment = interaction.get("two_pad_grasp_alignment", {})
    if not isinstance(raw_grasp_alignment, Mapping):
        raise WorldEffectToolInvocationError(
            "interaction_frame.two_pad_grasp_alignment must be an object"
        )
    grasp_alignment = _json_copy(
        raw_grasp_alignment,
        "interaction_frame.two_pad_grasp_alignment",
    )
    raw_current_contact = runtime_observation.get("current_contact")
    if raw_current_contact is not None and not isinstance(
        raw_current_contact, Mapping
    ):
        raise WorldEffectToolInvocationError(
            "runtime_observation.current_contact must be an object"
        )
    retained_contact_supported = retained_contact_supports_loaded_actuator(
        raw_current_contact
    )
    lease_grounding_ids = set(lease_decision.grounding_entity_ids)
    bindings = tuple(
        item
        for item in selected_lease.geometry_bindings
        if item.entity_id in lease_grounding_ids
    )
    if {item.entity_id for item in bindings} != lease_grounding_ids:
        raise WorldEffectToolInvocationError(
            "lease grounding entities are missing fresh geometry bindings"
        )
    position_anchors = tuple(
        anchor for binding in bindings for anchor in _position_anchors(binding)
    )
    orientation_axes = tuple(
        axis
        for binding in bindings
        for axis in _orientation_axes(binding, alignment_relation)
    )
    properties = invocation_schema.get("properties", {})
    ordered_waypoint_grounding = (
        constraints.get("grounding_mode") == "ordered_waypoint_path"
    )
    ordered_waypoint_schema = (
        properties.get("ordered_waypoints")
        if isinstance(properties, Mapping)
        else None
    )
    if ordered_waypoint_grounding and not isinstance(
        ordered_waypoint_schema, Mapping
    ):
        raise WorldEffectToolInvocationError(
            "ordered waypoint grounding requires an ordered_waypoints schema"
        )
    requires_position = bool(
        isinstance(properties, Mapping)
        and (
            "target_position_m" in properties
            or ordered_waypoint_grounding
        )
    )
    requires_orientation = (
        isinstance(properties, Mapping)
        and (
            "target_quaternion_wxyz" in properties
            or ordered_waypoint_grounding
        )
    )
    if requires_position and (not position_anchors or origin_offset is None):
        raise WorldEffectToolInvocationError(
            "position invocation lacks geometry anchors or interaction-frame offset"
        )
    if requires_orientation and (not orientation_axes or alignment_axis is None):
        raise WorldEffectToolInvocationError(
            "orientation invocation lacks observed axes or interaction alignment axis"
        )
    current_interaction_position: tuple[float, ...] | None = None
    current_interaction_offsets: tuple[Mapping[str, Any], ...] = ()
    if origin_offset is not None:
        rotated_current_origin = _rotate_wxyz(
            current_quaternion,
            origin_offset,
        )
        current_interaction_position = tuple(
            position + component
            for position, component in zip(
                current_position,
                rotated_current_origin,
            )
        )
        current_interaction_offsets = tuple(
            {
                "anchor_id": anchor.anchor_id,
                "entity_id": anchor.entity_id,
                "current_interaction_offset_from_anchor_m": [
                    current - target
                    for current, target in zip(
                        current_interaction_position,
                        anchor.position_m,
                    )
                ],
                "current_interaction_distance_from_anchor_m": _distance(
                    current_interaction_position,
                    anchor.position_m,
                ),
            }
            for anchor in position_anchors
        )
    model_argument_schema = _json_copy(
        invocation_schema, "model_argument_schema"
    )
    required_invocation_arguments = _json_copy(
        selected_lease.required_invocation_arguments,
        "required_invocation_arguments",
    )
    model_properties = model_argument_schema.get("properties")
    if required_invocation_arguments and not isinstance(model_properties, dict):
        raise WorldEffectToolInvocationError(
            "semantic invocation binding requires object properties"
        )
    for field_name, expected_value in required_invocation_arguments.items():
        field_schema = model_properties.get(field_name)
        if not isinstance(field_schema, dict):
            raise WorldEffectToolInvocationError(
                "semantic invocation binding references an unadvertised field"
            )
        if field_schema.get("type") == "string":
            field_schema["enum"] = [expected_value]
    materialized_argument_fields: tuple[str, ...] = ()
    if ordered_waypoint_grounding:
        model_properties = model_argument_schema.get("properties")
        waypoint_schema = (
            model_properties.get("ordered_waypoints")
            if isinstance(model_properties, dict)
            else None
        )
        waypoint_items = (
            waypoint_schema.get("items")
            if isinstance(waypoint_schema, dict)
            else None
        )
        waypoint_properties = (
            waypoint_items.get("properties")
            if isinstance(waypoint_items, dict)
            else None
        )
        waypoint_required = (
            waypoint_items.get("required")
            if isinstance(waypoint_items, dict)
            else None
        )
        if not isinstance(waypoint_properties, dict) or not isinstance(
            waypoint_required, list
        ):
            raise WorldEffectToolInvocationError(
                "ordered waypoint schema items require mutable properties/required"
            )
        if "target_position_m" not in waypoint_properties:
            raise WorldEffectToolInvocationError(
                "ordered waypoint schema must contain target_position_m"
            )
        if "target_quaternion_wxyz" not in waypoint_properties:
            raise WorldEffectToolInvocationError(
                "ordered waypoint schema must contain target_quaternion_wxyz"
            )
        waypoint_properties.pop("target_position_m")
        waypoint_properties.update(
            {
                "position_anchor_id": {
                    "type": "string",
                    "enum": sorted(anchor.anchor_id for anchor in position_anchors),
                },
                "interaction_offset_from_anchor_m": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "orientation_alignment_id": {
                    "type": "string",
                    "enum": sorted(
                        axis.alignment_id for axis in orientation_axes
                    ),
                },
            }
        )
        waypoint_items["required"] = [
            field for field in waypoint_required if field != "target_position_m"
        ] + [
            "position_anchor_id",
            "interaction_offset_from_anchor_m",
            "orientation_alignment_id",
        ]
        materialized_argument_fields = (
            "ordered_waypoints[].target_position_m",
        )
    elif requires_position:
        model_properties = model_argument_schema.get("properties")
        model_required = model_argument_schema.get("required")
        if not isinstance(model_properties, dict) or not isinstance(
            model_required, list
        ):
            raise WorldEffectToolInvocationError(
                "invocation schema properties/required must be mutable collections"
            )
        model_properties.pop("target_position_m", None)
        model_argument_schema["required"] = [
            field for field in model_required if field != "target_position_m"
        ]
        materialized_argument_fields = ("target_position_m",)
    invalidation_ids = tuple(
        sorted(item.condition_id for item in lease_decision.invalidation_conditions)
    )
    candidate_seed = {
        "lease_decision": lease_decision.to_dict(),
        "runtime_observation": _json_copy(runtime_observation, "runtime_observation"),
        "invocation_schema": invocation_schema,
        "model_argument_schema": model_argument_schema,
        "materialized_argument_fields": list(materialized_argument_fields),
        "position_anchors": [item.to_dict() for item in position_anchors],
        "current_interaction_position_m": current_interaction_position,
        "current_interaction_offsets_from_anchors": current_interaction_offsets,
        "orientation_axes": [item.to_dict() for item in orientation_axes],
    }
    candidate = ShadowToolInvocationCandidate(
        candidate_id="tool-invocation:" + _digest(candidate_seed),
        lease_observation_id=lease_decision.observation_id,
        lease_id=lease_decision.lease_id,
        provider_instance_id=instance.instance_id,
        operation_candidate_id=selected_lease.operation_candidate_id,
        tool_id=selected_lease.tool_id,
        tool_family=selected_lease.tool_family,
        purpose=selected_lease.purpose,
        semantic_effect_id=selected_lease.semantic_effect_id,
        required_invocation_arguments=required_invocation_arguments,
        coordinate_frame=coordinate_frame,
        invocation_schema=invocation_schema,
        model_argument_schema=model_argument_schema,
        materialized_argument_fields=materialized_argument_fields,
        invocation_constraints=constraints,
        tool_configuration=lease_decision.tool_configuration,
        invalidation_condition_ids=invalidation_ids,
        current_controlled_position_m=current_position,  # type: ignore[arg-type]
        current_controlled_quaternion_wxyz=current_quaternion,  # type: ignore[arg-type]
        current_interaction_position_m=current_interaction_position,  # type: ignore[arg-type]
        current_interaction_offsets_from_anchors=current_interaction_offsets,
        interaction_origin_offset_local_m=origin_offset,  # type: ignore[arg-type]
        interaction_alignment_axis_local=alignment_axis,  # type: ignore[arg-type]
        interaction_alignment_relation=alignment_relation,
        interaction_grasp_geometry=grasp_geometry,
        two_pad_grasp_alignment=grasp_alignment,
        retained_contact_supported=retained_contact_supported,
        position_anchors=position_anchors,
        orientation_axes=orientation_axes,
    )
    runtime_digest = "runtime-tool-observation:" + _digest(runtime_observation)
    observation_id = "tool-invocation-observation:" + _digest(
        {
            "runtime_observation_digest": runtime_digest,
            "candidate": candidate.to_dict(),
        }
    )
    return ShadowToolInvocationCandidateSet(
        observation_id=observation_id,
        runtime_observation_digest=runtime_digest,
        lease_id=lease_decision.lease_id,
        candidates=(candidate,),
    )


@dataclass(frozen=True)
class ShadowToolInvocationDecision:
    observation_id: str
    decision: str
    candidate_id: str | None
    lease_id: str | None
    tool_id: str | None
    position_anchor_id: str | None
    interaction_offset_from_anchor_m: tuple[float, ...]
    orientation_alignment_id: str | None
    invocation_arguments: Mapping[str, Any]
    acknowledged_invalidation_condition_ids: tuple[str, ...]
    grounding_assessment: Mapping[str, Any]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "candidate_id": self.candidate_id,
            "lease_id": self.lease_id,
            "tool_id": self.tool_id,
            "position_anchor_id": self.position_anchor_id,
            "interaction_offset_from_anchor_m": list(
                self.interaction_offset_from_anchor_m
            ),
            "orientation_alignment_id": self.orientation_alignment_id,
            "invocation_arguments": _json_copy(
                self.invocation_arguments, "invocation_arguments"
            ),
            "acknowledged_invalidation_condition_ids": list(
                self.acknowledged_invalidation_condition_ids
            ),
            "grounding_assessment": _json_copy(
                self.grounding_assessment, "grounding_assessment"
            ),
            "confidence": self.confidence,
            "reason": self.reason,
            "invocation_validated": self.decision == "propose_invocation",
            "execution_lease_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def _materialize_ordered_waypoint_path(
    candidate: ShadowToolInvocationCandidate,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ground every model-proposed checkpoint against advertised geometry."""
    raw_waypoints = arguments.get("ordered_waypoints")
    if not isinstance(raw_waypoints, list):
        raise WorldEffectToolInvocationError(
            "invocation_arguments.ordered_waypoints must be an array"
        )
    if candidate.interaction_origin_offset_local_m is None:
        raise WorldEffectToolInvocationError(
            "interaction origin offset is unavailable"
        )
    if candidate.interaction_alignment_axis_local is None:
        raise WorldEffectToolInvocationError(
            "interaction alignment axis is unavailable"
        )
    constraints = candidate.invocation_constraints
    maximum_offset = float(constraints.get("maximum_grounding_offset_m", 0.35))
    maximum_alignment_error = float(
        constraints.get("maximum_alignment_error_deg", 15.0)
    )
    maximum_segment_displacement = float(
        constraints.get(
            "maximum_segment_displacement_m",
            constraints.get("maximum_displacement_m", math.inf),
        )
    )
    maximum_path_length = float(
        constraints.get("maximum_path_length_m", math.inf)
    )
    workspace_min = constraints.get("workspace_min_m")
    workspace_max = constraints.get("workspace_max_m")
    lower = (
        _vector(workspace_min, 3, "workspace_min_m")
        if workspace_min is not None
        else None
    )
    upper = (
        _vector(workspace_max, 3, "workspace_max_m")
        if workspace_max is not None
        else None
    )
    if (lower is None) != (upper is None):
        raise WorldEffectToolInvocationError(
            "workspace bounds must provide both minimum and maximum"
        )
    position_tolerance = candidate.tool_configuration.get("position_tolerance_m")
    minimum_segment_displacement = (
        float(position_tolerance)
        if isinstance(position_tolerance, (int, float))
        and not isinstance(position_tolerance, bool)
        and math.isfinite(float(position_tolerance))
        else 0.0
    )
    previous_position: Sequence[float] = candidate.current_controlled_position_m
    path_length = 0.0
    materialized_waypoints: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for index, raw_waypoint in enumerate(raw_waypoints):
        if not isinstance(raw_waypoint, Mapping):
            raise WorldEffectToolInvocationError(
                f"invocation_arguments.ordered_waypoints[{index}] must be an object"
            )
        waypoint = dict(raw_waypoint)
        anchor_id = _identifier(
            waypoint.pop("position_anchor_id", None),
            f"invocation_arguments.ordered_waypoints[{index}].position_anchor_id",
        )
        anchor = next(
            (
                item
                for item in candidate.position_anchors
                if item.anchor_id == anchor_id
            ),
            None,
        )
        if anchor is None:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} position anchor was not advertised"
            )
        offset = _vector(
            waypoint.pop("interaction_offset_from_anchor_m", None),
            3,
            (
                "invocation_arguments.ordered_waypoints"
                f"[{index}].interaction_offset_from_anchor_m"
            ),
        )
        if _distance(offset, (0.0, 0.0, 0.0)) > maximum_offset:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} exceeds runtime grounding limit"
            )
        if any(
            value < minimum or value > maximum
            for value, minimum, maximum in zip(
                offset, anchor.offset_min_m, anchor.offset_max_m
            )
        ):
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} exceeds its RGB-D anchor envelope"
            )
        alignment_id = _identifier(
            waypoint.pop("orientation_alignment_id", None),
            (
                "invocation_arguments.ordered_waypoints"
                f"[{index}].orientation_alignment_id"
            ),
        )
        axis = next(
            (
                item
                for item in candidate.orientation_axes
                if item.alignment_id == alignment_id
            ),
            None,
        )
        if axis is None:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} orientation axis was not advertised"
            )
        raw_quaternion = _vector(
            waypoint.get("target_quaternion_wxyz"),
            4,
            (
                "invocation_arguments.ordered_waypoints"
                f"[{index}].target_quaternion_wxyz"
            ),
        )
        quaternion_norm = math.sqrt(
            sum(value * value for value in raw_quaternion)
        )
        if abs(quaternion_norm - 1.0) > 0.01:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} target quaternion is not normalized"
            )
        quaternion = _normalize(
            raw_quaternion, f"ordered waypoint {index} target quaternion"
        )
        realized_axis = _normalize(
            _rotate_wxyz(
                quaternion,
                candidate.interaction_alignment_axis_local,
            ),
            f"ordered waypoint {index} realized interaction alignment axis",
        )
        dot = sum(
            left * right
            for left, right in zip(realized_axis, axis.axis_robot_root)
        )
        if axis.bidirectional:
            dot = abs(dot)
        alignment_error_deg = math.degrees(
            math.acos(min(1.0, max(-1.0, dot)))
        )
        if alignment_error_deg > maximum_alignment_error:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} exceeds observed-axis alignment limit"
            )
        rotated_origin_offset = _rotate_wxyz(
            quaternion,
            candidate.interaction_origin_offset_local_m,
        )
        realized_interaction_position = tuple(
            position + component
            for position, component in zip(anchor.position_m, offset)
        )
        target_position = tuple(
            position - component
            for position, component in zip(
                realized_interaction_position, rotated_origin_offset
            )
        )
        if lower is not None and upper is not None and any(
            value < minimum or value > maximum
            for value, minimum, maximum in zip(target_position, lower, upper)
        ):
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} is outside runtime workspace bounds"
            )
        segment_displacement = _distance(target_position, previous_position)
        if segment_displacement <= minimum_segment_displacement:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} is already within the configured "
                "position tolerance"
            )
        if segment_displacement > maximum_segment_displacement:
            raise WorldEffectToolInvocationError(
                f"ordered waypoint {index} exceeds runtime segment displacement bound"
            )
        path_length += segment_displacement
        if path_length > maximum_path_length:
            raise WorldEffectToolInvocationError(
                "ordered waypoint path exceeds runtime path-length bound"
            )
        materialized_waypoints.append(
            {
                "target_position_m": list(target_position),
                "target_quaternion_wxyz": list(quaternion),
            }
        )
        assessments.append(
            {
                "waypoint_index": index,
                "position_anchor_id": anchor_id,
                "anchor_entity_id": anchor.entity_id,
                "geometry_digest": anchor.geometry_digest,
                "anchor_position_m": list(anchor.position_m),
                "interaction_offset_from_anchor_m": list(offset),
                "realized_interaction_position_m": list(
                    realized_interaction_position
                ),
                "controlled_target_position_m": list(target_position),
                "segment_displacement_m": segment_displacement,
                "orientation_alignment_id": alignment_id,
                "orientation_entity_id": axis.entity_id,
                "realized_interaction_axis_robot_root": list(realized_axis),
                "observed_alignment_axis_robot_root": list(axis.axis_robot_root),
                "alignment_error_deg": alignment_error_deg,
            }
        )
        previous_position = target_position
    materialized_arguments = dict(arguments)
    materialized_arguments["ordered_waypoints"] = materialized_waypoints
    return materialized_arguments, {
        "coordinate_frame": candidate.coordinate_frame,
        "lease_invalidations_preserved": True,
        "grounding_mode": "ordered_waypoint_path",
        "ordered_waypoint_count": len(materialized_waypoints),
        "ordered_waypoint_path_length_m": path_length,
        "maximum_path_length_m": maximum_path_length,
        "maximum_segment_displacement_m": maximum_segment_displacement,
        "ordered_waypoints": assessments,
    }


class ShadowToolInvocationGate:
    """Validate typed invocation geometry while withholding every dispatch path."""

    def __init__(self, candidate_set: ShadowToolInvocationCandidateSet) -> None:
        self.candidate_set = candidate_set
        self._candidates = {item.candidate_id: item for item in candidate_set.candidates}

    def dispatch(self, payload: Mapping[str, Any]) -> ShadowToolInvocationDecision:
        if not isinstance(payload, Mapping):
            raise WorldEffectToolInvocationError("invocation proposal must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "candidate_id",
            "lease_id",
            "tool_id",
            "position_anchor_id",
            "interaction_offset_from_anchor_m",
            "orientation_alignment_id",
            "invocation_arguments",
            "acknowledged_invalidation_condition_ids",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldEffectToolInvocationError(
                f"invocation proposal contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectToolInvocationError(
                f"invocation proposal is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION:
            raise WorldEffectToolInvocationError("invocation schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.candidate_set.observation_id:
            raise WorldEffectToolInvocationError("stale tool-invocation observation_id")
        decision = _text(payload["decision"], "decision")
        if decision not in WORLD_EFFECT_TOOL_INVOCATION_DECISIONS:
            raise WorldEffectToolInvocationError(
                f"unsupported tool-invocation decision {decision!r}"
            )
        raw_offset = payload["interaction_offset_from_anchor_m"]
        raw_arguments = payload["invocation_arguments"]
        raw_acknowledgements = payload[
            "acknowledged_invalidation_condition_ids"
        ]
        if not isinstance(raw_offset, list):
            raise WorldEffectToolInvocationError(
                "interaction_offset_from_anchor_m must be an array"
            )
        if not isinstance(raw_arguments, Mapping):
            raise WorldEffectToolInvocationError("invocation_arguments must be an object")
        if not isinstance(raw_acknowledgements, list):
            raise WorldEffectToolInvocationError(
                "acknowledged invalidation ids must be an array"
            )
        proposal_ids = (
            "candidate_id",
            "lease_id",
            "tool_id",
            "position_anchor_id",
            "orientation_alignment_id",
        )
        if decision != "propose_invocation":
            if any(payload[field] is not None for field in proposal_ids):
                raise WorldEffectToolInvocationError(
                    f"decision {decision!r} requires null proposal ids"
                )
            if raw_offset or raw_arguments or raw_acknowledgements:
                raise WorldEffectToolInvocationError(
                    f"decision {decision!r} requires empty invocation fields"
                )
            return ShadowToolInvocationDecision(
                observation_id=observation_id,
                decision=decision,
                candidate_id=None,
                lease_id=None,
                tool_id=None,
                position_anchor_id=None,
                interaction_offset_from_anchor_m=(),
                orientation_alignment_id=None,
                invocation_arguments={},
                acknowledged_invalidation_condition_ids=(),
                grounding_assessment={},
                confidence=_confidence(payload["confidence"]),
                reason=_text(payload["reason"], "reason"),
            )

        candidate_id = _identifier(payload["candidate_id"], "candidate_id")
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise WorldEffectToolInvocationError(
                "selected tool-invocation candidate was not advertised"
            )
        lease_id = _identifier(payload["lease_id"], "lease_id")
        tool_id = _identifier(payload["tool_id"], "tool_id")
        if lease_id != candidate.lease_id or tool_id != candidate.tool_id:
            raise WorldEffectToolInvocationError(
                "invocation lease/tool pair was not advertised; selected "
                f"candidate requires lease_id={candidate.lease_id!r} and "
                f"tool_id={candidate.tool_id!r}, got lease_id={lease_id!r} "
                f"and tool_id={tool_id!r}"
            )
        if (
            candidate.semantic_effect_id == "entity_attachment.acquire"
            and candidate.interaction_grasp_geometry
        ):
            grasp_alignment = candidate.two_pad_grasp_alignment
            if grasp_alignment.get("available") is not True:
                raise WorldEffectToolInvocationError(
                    "selected acquire effect requires a fresh two-pad grasp "
                    "alignment observation"
                )
            if grasp_alignment.get("object_fits_configured_aperture") is not True:
                raise WorldEffectToolInvocationError(
                    "selected acquire effect requires an object that fits the "
                    "advertised configured-open aperture"
                )
            if (
                grasp_alignment.get("object_fully_between_open_pad_planes")
                is not True
            ):
                raise WorldEffectToolInvocationError(
                    "selected acquire effect requires the object fully inside "
                    "the advertised two-pad grasp corridor"
                )
            if (
                grasp_alignment.get(
                    "object_center_inside_transverse_pad_bounds"
                )
                is not True
            ):
                raise WorldEffectToolInvocationError(
                    "selected acquire effect requires the object center inside "
                    "the advertised transverse pad-face bounds"
                )
        acknowledgements = tuple(
            _identifier(item, f"acknowledged_invalidation_condition_ids[{index}]")
            for index, item in enumerate(raw_acknowledgements)
        )
        if len(acknowledgements) != len(set(acknowledgements)):
            raise WorldEffectToolInvocationError(
                "acknowledged invalidation ids must not contain duplicates"
            )
        if set(acknowledgements) != set(candidate.invalidation_condition_ids):
            raise WorldEffectToolInvocationError(
                "invocation must acknowledge every exact lease invalidation"
            )
        model_arguments = _validate_schema_value(
            raw_arguments,
            candidate.model_argument_schema,
            "invocation_arguments",
        )
        arguments = dict(model_arguments)
        for field_name, expected_value in (
            candidate.required_invocation_arguments.items()
        ):
            if arguments.get(field_name) != expected_value:
                raise WorldEffectToolInvocationError(
                    "invocation arguments contradict the selected semantic effect"
                )
        grasp_alignment = candidate.two_pad_grasp_alignment
        corrective_contract = grasp_alignment.get(
            "corrective_motion_grounding_contract"
        )
        if (
            candidate.tool_family == "motion"
            and not candidate.retained_contact_supported
            and grasp_alignment.get("available") is True
            and grasp_alignment.get(
                "object_center_inside_full_grasp_corridor"
            )
            is False
            and isinstance(corrective_contract, Mapping)
        ):
            required_anchor_id = _identifier(
                corrective_contract.get(
                    "required_terminal_position_anchor_id"
                ),
                (
                    "corrective_motion_grounding_contract."
                    "required_terminal_position_anchor_id"
                ),
            )
            required_offset = _vector(
                corrective_contract.get(
                    "required_terminal_interaction_offset_from_anchor_m"
                ),
                3,
                (
                    "corrective_motion_grounding_contract."
                    "required_terminal_interaction_offset_from_anchor_m"
                ),
            )
            if candidate.ordered_waypoint_grounding_required:
                raw_waypoints = model_arguments.get("ordered_waypoints")
                if not isinstance(raw_waypoints, list) or not raw_waypoints:
                    raise WorldEffectToolInvocationError(
                        "corrective motion requires a terminal grounded waypoint"
                    )
                terminal_waypoint = raw_waypoints[-1]
                if not isinstance(terminal_waypoint, Mapping):
                    raise WorldEffectToolInvocationError(
                        "corrective motion terminal waypoint must be an object"
                    )
                terminal_anchor_id = terminal_waypoint.get(
                    "position_anchor_id"
                )
                terminal_offset = terminal_waypoint.get(
                    "interaction_offset_from_anchor_m"
                )
            else:
                terminal_anchor_id = payload["position_anchor_id"]
                terminal_offset = raw_offset
            if terminal_anchor_id != required_anchor_id:
                raise WorldEffectToolInvocationError(
                    "corrective motion terminal anchor contradicts the "
                    "advertised interaction-grounding contract"
                )
            normalized_terminal_offset = _vector(
                terminal_offset,
                3,
                "corrective_motion_terminal_offset",
            )
            if normalized_terminal_offset != required_offset:
                raise WorldEffectToolInvocationError(
                    "corrective motion terminal offset contradicts the "
                    "advertised interaction-grounding contract"
                )
        assessment: dict[str, Any] = {
            "coordinate_frame": candidate.coordinate_frame,
            "lease_invalidations_preserved": True,
        }
        position_anchor_id = payload["position_anchor_id"]
        orientation_alignment_id = payload["orientation_alignment_id"]
        offset: tuple[float, ...] = ()

        if candidate.ordered_waypoint_grounding_required:
            if position_anchor_id is not None or raw_offset:
                raise WorldEffectToolInvocationError(
                    "ordered waypoint invocation requires null top-level position grounding"
                )
            if orientation_alignment_id is not None:
                raise WorldEffectToolInvocationError(
                    "ordered waypoint invocation requires null top-level orientation grounding"
                )
            arguments, assessment = _materialize_ordered_waypoint_path(
                candidate,
                arguments,
            )
            arguments = validate_materialized_invocation_arguments(
                candidate, arguments
            )
            return ShadowToolInvocationDecision(
                observation_id=observation_id,
                decision=decision,
                candidate_id=candidate_id,
                lease_id=lease_id,
                tool_id=tool_id,
                position_anchor_id=None,
                interaction_offset_from_anchor_m=(),
                orientation_alignment_id=None,
                invocation_arguments=arguments,
                acknowledged_invalidation_condition_ids=acknowledgements,
                grounding_assessment=assessment,
                confidence=_confidence(payload["confidence"]),
                reason=_text(payload["reason"], "reason"),
            )

        quaternion: tuple[float, ...] | None = None
        if candidate.orientation_grounding_required:
            orientation_alignment_id = _identifier(
                orientation_alignment_id, "orientation_alignment_id"
            )
            axis = next(
                (
                    item
                    for item in candidate.orientation_axes
                    if item.alignment_id == orientation_alignment_id
                ),
                None,
            )
            if axis is None:
                raise WorldEffectToolInvocationError(
                    "orientation alignment axis was not advertised"
                )
            raw_quaternion = _vector(
                arguments.get("target_quaternion_wxyz"),
                4,
                "invocation_arguments.target_quaternion_wxyz",
            )
            quaternion_norm = math.sqrt(sum(value * value for value in raw_quaternion))
            if abs(quaternion_norm - 1.0) > 0.01:
                raise WorldEffectToolInvocationError(
                    "target quaternion must be normalized within 0.01"
                )
            quaternion = _normalize(raw_quaternion, "target_quaternion_wxyz")
            if candidate.interaction_alignment_axis_local is None:
                raise WorldEffectToolInvocationError(
                    "interaction alignment axis is unavailable"
                )
            realized_axis = _normalize(
                _rotate_wxyz(
                    quaternion,
                    candidate.interaction_alignment_axis_local,
                ),
                "realized interaction alignment axis",
            )
            dot = sum(
                left * right
                for left, right in zip(realized_axis, axis.axis_robot_root)
            )
            if axis.bidirectional:
                dot = abs(dot)
            alignment_error_deg = math.degrees(
                math.acos(min(1.0, max(-1.0, dot)))
            )
            maximum_alignment_error = float(
                candidate.invocation_constraints.get(
                    "maximum_alignment_error_deg", 15.0
                )
            )
            if alignment_error_deg > maximum_alignment_error:
                raise WorldEffectToolInvocationError(
                    "target orientation exceeds observed-axis alignment limit"
                )
            assessment.update(
                {
                    "orientation_alignment_id": orientation_alignment_id,
                    "realized_interaction_axis_robot_root": list(realized_axis),
                    "observed_alignment_axis_robot_root": list(
                        axis.axis_robot_root
                    ),
                    "alignment_error_deg": alignment_error_deg,
                    "maximum_alignment_error_deg": maximum_alignment_error,
                }
            )
        elif orientation_alignment_id is not None:
            raise WorldEffectToolInvocationError(
                "tool invocation does not accept orientation grounding"
            )

        if candidate.position_grounding_required:
            position_anchor_id = _identifier(position_anchor_id, "position_anchor_id")
            anchor = next(
                (
                    item
                    for item in candidate.position_anchors
                    if item.anchor_id == position_anchor_id
                ),
                None,
            )
            if anchor is None:
                raise WorldEffectToolInvocationError(
                    "position grounding anchor was not advertised"
                )
            offset = _vector(
                raw_offset, 3, "interaction_offset_from_anchor_m"
            )
            maximum_offset = float(
                candidate.invocation_constraints.get(
                    "maximum_grounding_offset_m", 0.35
                )
            )
            if _distance(offset, (0.0, 0.0, 0.0)) > maximum_offset:
                raise WorldEffectToolInvocationError(
                    "interaction offset exceeds runtime grounding limit"
                )
            if any(
                value < minimum or value > maximum
                for value, minimum, maximum in zip(
                    offset,
                    anchor.offset_min_m,
                    anchor.offset_max_m,
                )
            ):
                raise WorldEffectToolInvocationError(
                    "interaction offset exceeds the RGB-D-derived anchor envelope"
                )
            if quaternion is None:
                quaternion = candidate.current_controlled_quaternion_wxyz
            if candidate.interaction_origin_offset_local_m is None:
                raise WorldEffectToolInvocationError(
                    "interaction origin offset is unavailable"
                )
            rotated_origin_offset = _rotate_wxyz(
                quaternion,
                candidate.interaction_origin_offset_local_m,
            )
            expected_interaction_position = tuple(
                position + component
                for position, component in zip(anchor.position_m, offset)
            )
            target_position = tuple(
                position - component
                for position, component in zip(
                    expected_interaction_position, rotated_origin_offset
                )
            )
            arguments["target_position_m"] = list(target_position)
            realized_interaction_position = tuple(
                position + component
                for position, component in zip(
                    target_position, rotated_origin_offset
                )
            )
            grounding_error_m = _distance(
                realized_interaction_position,
                expected_interaction_position,
            )
            if grounding_error_m > 0.002:
                raise WorldEffectToolInvocationError(
                    "target pose does not realize the selected RGB-D anchor and offset"
                )
            constraints = candidate.invocation_constraints
            workspace_min = constraints.get("workspace_min_m")
            workspace_max = constraints.get("workspace_max_m")
            if workspace_min is not None and workspace_max is not None:
                lower = _vector(workspace_min, 3, "workspace_min_m")
                upper = _vector(workspace_max, 3, "workspace_max_m")
                if any(
                    value < minimum or value > maximum
                    for value, minimum, maximum in zip(
                        target_position, lower, upper
                    )
                ):
                    raise WorldEffectToolInvocationError(
                        "target position is outside runtime workspace bounds"
                    )
            displacement = _distance(
                target_position, candidate.current_controlled_position_m
            )
            position_tolerance = candidate.tool_configuration.get(
                "position_tolerance_m"
            )
            if (
                candidate.tool_family == "motion"
                and isinstance(position_tolerance, (int, float))
                and not isinstance(position_tolerance, bool)
                and math.isfinite(float(position_tolerance))
                and displacement <= float(position_tolerance)
            ):
                raise WorldEffectToolInvocationError(
                    "materialized motion target is already within the configured "
                    "position tolerance; choose a meaningfully different "
                    "interaction offset or a non-motion operation"
                )
            maximum_displacement = float(
                constraints.get("maximum_displacement_m", math.inf)
            )
            if displacement > maximum_displacement:
                raise WorldEffectToolInvocationError(
                    "target position exceeds runtime displacement bound"
                )
            assessment.update(
                {
                    "position_anchor_id": position_anchor_id,
                    "geometry_digest": anchor.geometry_digest,
                    "anchor_position_m": list(anchor.position_m),
                    "interaction_offset_from_anchor_m": list(offset),
                    "realized_interaction_position_m": list(
                        realized_interaction_position
                    ),
                    "grounding_error_m": grounding_error_m,
                    "controlled_frame_displacement_m": displacement,
                    "maximum_controlled_frame_displacement_m": (
                        maximum_displacement
                    ),
                }
            )
        elif position_anchor_id is not None or raw_offset:
            raise WorldEffectToolInvocationError(
                "tool invocation does not accept position grounding"
            )

        arguments = validate_materialized_invocation_arguments(
            candidate, arguments
        )

        return ShadowToolInvocationDecision(
            observation_id=observation_id,
            decision=decision,
            candidate_id=candidate_id,
            lease_id=lease_id,
            tool_id=tool_id,
            position_anchor_id=position_anchor_id,
            interaction_offset_from_anchor_m=offset,
            orientation_alignment_id=orientation_alignment_id,
            invocation_arguments=arguments,
            acknowledged_invalidation_condition_ids=acknowledgements,
            grounding_assessment=assessment,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def shadow_tool_invocation_json_schema(
    candidate_set: ShadowToolInvocationCandidateSet,
) -> dict[str, Any]:
    ordered_waypoint_only = bool(
        candidate_set.candidates
        and all(
            item.ordered_waypoint_grounding_required
            for item in candidate_set.candidates
        )
    )
    candidate_ids = [item.candidate_id for item in candidate_set.candidates]
    tool_ids = sorted({item.tool_id for item in candidate_set.candidates})
    position_ids = sorted(
        {anchor.anchor_id for item in candidate_set.candidates for anchor in item.position_anchors}
    )
    orientation_ids = sorted(
        {axis.alignment_id for item in candidate_set.candidates for axis in item.orientation_axes}
    )
    invalidation_ids = sorted(
        {
            condition_id
            for item in candidate_set.candidates
            for condition_id in item.invalidation_condition_ids
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "observation_id",
            "decision",
            "candidate_id",
            "lease_id",
            "tool_id",
            "position_anchor_id",
            "interaction_offset_from_anchor_m",
            "orientation_alignment_id",
            "invocation_arguments",
            "acknowledged_invalidation_condition_ids",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_EFFECT_TOOL_INVOCATION_SCHEMA_VERSION},
            "observation_id": {"const": candidate_set.observation_id},
            "decision": {"enum": sorted(WORLD_EFFECT_TOOL_INVOCATION_DECISIONS)},
            "candidate_id": {"type": ["string", "null"], "enum": [None, *candidate_ids]},
            "lease_id": {"type": ["string", "null"], "enum": [None, candidate_set.lease_id]},
            "tool_id": {"type": ["string", "null"], "enum": [None, *tool_ids]},
            "position_anchor_id": {
                "type": ["string", "null"],
                "enum": [None] if ordered_waypoint_only else [None, *position_ids],
            },
            "interaction_offset_from_anchor_m": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 0,
                "maxItems": 0 if ordered_waypoint_only else 3,
            },
            "orientation_alignment_id": {
                "type": ["string", "null"],
                "enum": (
                    [None]
                    if ordered_waypoint_only
                    else [None, *orientation_ids]
                ),
            },
            "invocation_arguments": {"type": "object"},
            "acknowledged_invalidation_condition_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": invalidation_ids},
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_shadow_tool_invocation_prompt(
    *,
    instruction: str,
    candidate_set: ShadowToolInvocationCandidateSet,
    rejection_context: Mapping[str, Any] | None = None,
) -> str:
    """Request one typed invocation while preserving the no-dispatch boundary."""
    instruction = _text(instruction, "instruction")
    previous_rejection = (
        {"status": "none"}
        if rejection_context is None
        else _json_copy(rejection_context, "rejection_context")
    )
    exact_proposal_identities = [
        {
            "candidate_id": item.candidate_id,
            "lease_id": item.lease_id,
            "tool_id": item.tool_id,
        }
        for item in candidate_set.candidates
    ]
    return f"""Propose one typed, RGB-D-grounded invocation for the selected
runtime tool and shadow execution lease.

Human instruction:
{instruction}

Fresh invocation candidate:
{json.dumps(candidate_set.to_dict(), indent=2)}

Previous proposal rejection for this same fresh observation:
{json.dumps(previous_rejection, indent=2)}

Exact proposal identity triples (copy all three strings from one row verbatim;
candidate_id determines its lease_id and tool_id):
{json.dumps(exact_proposal_identities, indent=2)}

Choose propose_invocation only for the exact candidate, lease, and tool. Preserve
every lease invalidation id exactly. Fill invocation_arguments using only the
model_argument_schema and respect x-runtime-constraints. Omit every field in
materialized_argument_fields; the deterministic gate derives those fields from
the selected evidence and validates the completed value against invocation_schema.
When semantic_effect_id and required_invocation_arguments are present, they are
the runtime tool's declarative binding for the already selected semantic effect.
For propose_invocation, copy those required arguments exactly; selecting a
contradictory actuator state is rejected before a runtime lease can be issued.
The semantic effect was already selected from fresh scene and sensor evidence by
the operation planner and accepted by the execution-lease planner. This stage
translates that selected effect into the advertised typed command; it must not
veto the effect merely because it prefers a different next action from the
image. Use blocked only when the exact bound command itself cannot satisfy the
advertised runtime contract or current evidence, not to replace the selected
semantic operation. Visual proximity or apparent enclosure alone is not proof
of a retained attachment.
When interaction_frame advertises grasp_geometry, treat it as observed tool
geometry rather than an embodiment-specific instruction. For an acquire effect,
the fresh two_pad_grasp_alignment must report that the visible object fits, is
fully between the open pad planes, and has its center inside both transverse
pad-face bounds. Otherwise return observe_again so the
operation planner can select corrective motion; the deterministic gate rejects
premature acquisition. For corrective pregrasp motion, make the object center
coincident with the advertised grasp-corridor center using the object center
anchor and its measured required_contact_center_translation_robot_root_m. A
top-center clearance pose is not a centered two-pad grasp pose.
When two_pad_grasp_alignment advertises a
corrective_motion_grounding_contract and reports that the object center remains
outside the full interaction corridor, every motion proposal must terminate at
the exact required terminal anchor and interaction offset. Ordered paths may use
other evidence-grounded intermediate anchors for clearance, but may not finish
at a destination or obstacle while claiming to establish the interaction
relation. This is a runtime-derived spatial relation, not a fixed pose.
When interaction_frame.retained_contact_supported is true, opposing tactile
evidence has already established the attachment and the pregrasp corrective
terminal contract is inactive. Use fresh destination geometry and the retained-
contact lease for transport; contact or orientation invalidation still stops
genuine attachment loss or slip.
For blocked or observe_again, do not copy them: set candidate_id, lease_id,
tool_id, position_anchor_id, and orientation_alignment_id to null; set
interaction_offset_from_anchor_m and acknowledged_invalidation_condition_ids to
empty arrays; and set invocation_arguments and grounding_assessment to empty
objects. A non-executing decision must never carry a latent command.

For a position-grounded invocation, select position_anchor_id and
interaction_offset_from_anchor_m to describe the desired interaction-frame
origin relative to fresh RGB-D geometry. The gate materializes the controlled
target_position_m so that:
  controlled target + rotated local interaction origin offset
  = selected RGB-D anchor + interaction_offset_from_anchor_m

The offset must also remain inside the selected anchor's scene-derived
offset_min_m/offset_max_m envelope. This prevents a nominally grounded pose from
placing the interaction point laterally outside the observed target.

The candidate explicitly reports current_interaction_offsets_from_anchors.
These are the current interaction-frame offsets, not suggested targets. Change
the selected offset when the operation asks to lower, raise, or otherwise move
the interaction frame. Never repeat the current offset while claiming a
different observable outcome. In particular, an offset above a top-center
anchor is clearance of the interaction point itself; it is not the controlled
frame's gripper-base-to-contact distance, because the deterministic gate already
accounts for interaction_origin_offset_local_m.
If a previous proposal was rejected as already within the configured position
tolerance, do not resubmit that no-op target. Change the interaction offset to
realize the selected observable outcome, or return blocked/observe_again so the
provider can choose a different advertised operation.

For an orientation-grounded invocation, select an observed orientation axis.
The target quaternion must rotate the advertised local interaction alignment
axis parallel to that observed axis within the runtime limit and honor the
advertised interaction alignment_relation. Axes are
bidirectional, so either sign is equivalent. Use the geometry and tool frames;
do not infer a body part, embodiment, controller, or task phase.

When ordered_waypoint_grounding_required is true, keep every top-level position
and orientation grounding field empty. Fill ordered_waypoints in
invocation_arguments instead. Each checkpoint independently selects an
advertised RGB-D position anchor, an in-envelope interaction offset, an observed
orientation axis, and a normalized target quaternion. Choose the fewest ordered
checkpoints that realize the requested observable outcome while respecting the
advertised segment and total path bounds. For loaded transport this can express
a raised free-space checkpoint followed by destination alignment and lowering;
those outcomes are model-selected, not implicit controller phases. The runtime
materializes every controlled-frame target and executes the accepted sequence
under one revocable observation-bound lease.

This proposal validates a typed invocation only. The referenced lease remains
unissued; no handler is bound and no tool or simulator action is called. Use
observe_again if the transforms or geometry are insufficient and blocked when
no bounded invocation satisfies the evidence.

Return exactly one JSON object matching this response schema, with no Markdown:
{json.dumps(shadow_tool_invocation_json_schema(candidate_set), indent=2, sort_keys=True)}
"""
