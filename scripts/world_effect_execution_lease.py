"""Fresh, geometry-grounded shadow leases for semantic effect operations.

This contract bridges a validated semantic operation to one runtime-advertised
tool configuration.  It binds the proposal to exact scene geometry and explicit
invalidation evidence, but deliberately stores no handler and accepts no pose,
trajectory, actuator command, or other dispatch argument.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

try:
    from .world_effect_operation_plan import (
        PlanningWorldEffectProviderInstance,
        WorldEffectOperationCandidateSet,
        WorldEffectOperationDecision,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_effect_operation_plan import (  # type: ignore[no-redef]
        PlanningWorldEffectProviderInstance,
        WorldEffectOperationCandidateSet,
        WorldEffectOperationDecision,
    )


WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION = "world-effect-execution-lease.v1"
WORLD_EFFECT_EXECUTION_LEASE_DECISIONS = frozenset(
    {"propose_lease", "observe_again", "blocked"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectExecutionLeaseError(ValueError):
    """Raised when a shadow execution lease exceeds its evidence or tool schema."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectExecutionLeaseError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectExecutionLeaseError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectExecutionLeaseError("confidence must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectExecutionLeaseError("confidence must be a number in [0, 1]")
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldEffectExecutionLeaseError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectExecutionLeaseError(f"{path} must contain finite numbers")
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
                raise WorldEffectExecutionLeaseError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectExecutionLeaseError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _object_schema(
    properties: Mapping[str, Any],
    *,
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": _json_copy(properties, "properties"),
        "required": list(required),
    }


@dataclass(frozen=True)
class GeometryEvidenceBinding:
    entity_id: str
    observation_status: str
    geometry_digest: str
    geometry: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identifier(self.entity_id, "entity_id")
        _identifier(self.observation_status, "observation_status")
        _identifier(self.geometry_digest, "geometry_digest")
        if not isinstance(self.geometry, Mapping) or not self.geometry:
            raise WorldEffectExecutionLeaseError(
                "geometry evidence must be a non-empty object"
            )
        _json_copy(self.geometry, "geometry")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "observation_status": self.observation_status,
            "geometry_digest": self.geometry_digest,
            "geometry": _json_copy(self.geometry, "geometry"),
        }


@dataclass(frozen=True)
class LeaseInvalidationCandidate:
    condition_id: str
    evidence_source_id: str
    description: str
    entity_scope: str
    parameter_schema: Mapping[str, Any]
    linked_tool_configuration_fields: tuple[str, ...] = ()
    mandatory: bool = False

    def __post_init__(self) -> None:
        for path, value in (
            ("condition_id", self.condition_id),
            ("evidence_source_id", self.evidence_source_id),
            ("entity_scope", self.entity_scope),
        ):
            _identifier(value, path)
        _text(self.description, "description")
        if self.entity_scope not in {"none", "operation_targets"}:
            raise WorldEffectExecutionLeaseError("unsupported invalidation entity_scope")
        if not isinstance(self.parameter_schema, Mapping):
            raise WorldEffectExecutionLeaseError("parameter_schema must be an object")
        _json_copy(self.parameter_schema, "parameter_schema")
        if len(set(self.linked_tool_configuration_fields)) != len(
            self.linked_tool_configuration_fields
        ):
            raise WorldEffectExecutionLeaseError(
                "linked tool configuration fields must not contain duplicates"
            )
        for index, field in enumerate(self.linked_tool_configuration_fields):
            _identifier(field, f"linked_tool_configuration_fields[{index}]")
        if not isinstance(self.mandatory, bool):
            raise WorldEffectExecutionLeaseError("mandatory must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "evidence_source_id": self.evidence_source_id,
            "description": self.description,
            "entity_scope": self.entity_scope,
            "parameter_schema": _json_copy(
                self.parameter_schema, "parameter_schema"
            ),
            "linked_tool_configuration_fields": list(
                self.linked_tool_configuration_fields
            ),
            "mandatory": self.mandatory,
        }


@dataclass(frozen=True)
class ShadowExecutionLeaseCandidate:
    candidate_id: str
    provider_instance_id: str
    membership_lease_id: str
    operation_observation_id: str
    operation_candidate_id: str
    requirement_id: str
    tool_id: str
    tool_family: str
    purpose: str
    operation_target_entity_ids: tuple[str, ...]
    desired_outcome: str
    stop_condition: str
    tool_configuration_schema: Mapping[str, Any]
    geometry_bindings: tuple[GeometryEvidenceBinding, ...]
    invalidation_candidates: tuple[LeaseInvalidationCandidate, ...]

    def __post_init__(self) -> None:
        for path, value in (
            ("candidate_id", self.candidate_id),
            ("provider_instance_id", self.provider_instance_id),
            ("membership_lease_id", self.membership_lease_id),
            ("operation_observation_id", self.operation_observation_id),
            ("operation_candidate_id", self.operation_candidate_id),
            ("requirement_id", self.requirement_id),
            ("tool_id", self.tool_id),
            ("tool_family", self.tool_family),
            ("purpose", self.purpose),
        ):
            _identifier(value, path)
        if not self.operation_target_entity_ids:
            raise WorldEffectExecutionLeaseError(
                "operation_target_entity_ids must not be empty"
            )
        for index, entity_id in enumerate(self.operation_target_entity_ids):
            _identifier(entity_id, f"operation_target_entity_ids[{index}]")
        _text(self.desired_outcome, "desired_outcome")
        _text(self.stop_condition, "stop_condition")
        if not isinstance(self.tool_configuration_schema, Mapping):
            raise WorldEffectExecutionLeaseError(
                "tool_configuration_schema must be an object"
            )
        _json_copy(self.tool_configuration_schema, "tool_configuration_schema")
        if not self.geometry_bindings:
            raise WorldEffectExecutionLeaseError("geometry_bindings must not be empty")
        bound_ids = {item.entity_id for item in self.geometry_bindings}
        if not set(self.operation_target_entity_ids).issubset(bound_ids):
            raise WorldEffectExecutionLeaseError(
                "operation targets must have fresh geometry bindings"
            )
        condition_ids = [item.condition_id for item in self.invalidation_candidates]
        if len(condition_ids) != len(set(condition_ids)):
            raise WorldEffectExecutionLeaseError(
                "invalidation condition ids must be unique"
            )

    def mandatory_condition_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(item.condition_id for item in self.invalidation_candidates if item.mandatory)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "provider_instance_id": self.provider_instance_id,
            "membership_lease_id": self.membership_lease_id,
            "operation_observation_id": self.operation_observation_id,
            "operation_candidate_id": self.operation_candidate_id,
            "requirement_id": self.requirement_id,
            "tool_id": self.tool_id,
            "tool_family": self.tool_family,
            "purpose": self.purpose,
            "operation_target_entity_ids": list(self.operation_target_entity_ids),
            "desired_outcome": self.desired_outcome,
            "stop_condition": self.stop_condition,
            "tool_configuration_schema": _json_copy(
                self.tool_configuration_schema, "tool_configuration_schema"
            ),
            "geometry_bindings": [item.to_dict() for item in self.geometry_bindings],
            "invalidation_candidates": [
                item.to_dict() for item in self.invalidation_candidates
            ],
            "mandatory_condition_ids": list(self.mandatory_condition_ids()),
            "observation_policy": "event_or_completion",
            "configuration_only": True,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class ShadowExecutionLeaseCandidateSet:
    observation_id: str
    inventory_digest: str
    provider_instance_id: str
    candidates: tuple[ShadowExecutionLeaseCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "inventory_digest": self.inventory_digest,
            "provider_instance_id": self.provider_instance_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "execution_lease_issued": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def _target_geometry_shift_limit_m(
    geometry_bindings: Sequence[GeometryEvidenceBinding],
    operation_target_entity_ids: Sequence[str],
) -> float:
    """Bound drift by the smallest observed target dimension, not a task rule."""
    target_ids = set(operation_target_entity_ids)
    dimensions: list[float] = []
    for binding in geometry_bindings:
        if binding.entity_id not in target_ids:
            continue
        extent = binding.geometry.get("visible_extent_base_m")
        if isinstance(extent, (list, tuple)) and len(extent) == 3:
            dimensions.extend(
                float(value)
                for value in extent
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and float(value) > 0.0
            )
            continue
        lower = binding.geometry.get("visible_aabb_min_base_m")
        upper = binding.geometry.get("visible_aabb_max_base_m")
        if (
            isinstance(lower, (list, tuple))
            and isinstance(upper, (list, tuple))
            and len(lower) == 3
            and len(upper) == 3
        ):
            dimensions.extend(
                delta
                for delta in (
                    float(maximum) - float(minimum)
                    for minimum, maximum in zip(lower, upper)
                    if isinstance(minimum, (int, float))
                    and not isinstance(minimum, bool)
                    and isinstance(maximum, (int, float))
                    and not isinstance(maximum, bool)
                )
                if math.isfinite(delta) and delta > 0.0
            )
    if not dimensions:
        raise WorldEffectExecutionLeaseError(
            "operation target geometry lacks a measurable visible extent"
        )
    return max(0.001, min(0.50, min(dimensions)))


def _invalidation_candidates(
    configuration_schema: Mapping[str, Any],
    geometry_bindings: Sequence[GeometryEvidenceBinding],
    operation_target_entity_ids: Sequence[str],
) -> tuple[LeaseInvalidationCandidate, ...]:
    properties = configuration_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise WorldEffectExecutionLeaseError(
            "tool configuration schema properties must be an object"
        )
    maximum_geometry_shift_m = _target_geometry_shift_limit_m(
        geometry_bindings,
        operation_target_entity_ids,
    )
    empty = _object_schema({})
    items = [
        LeaseInvalidationCandidate(
            condition_id="scene.target_visibility_lost",
            evidence_source_id="scene.geometry.rgbd",
            description="Stop when any operation target is no longer visible.",
            entity_scope="operation_targets",
            parameter_schema=empty,
            mandatory=True,
        ),
        LeaseInvalidationCandidate(
            condition_id="scene.target_geometry_drift",
            evidence_source_id="scene.geometry.rgbd",
            description=(
                "Stop when target center or visible extent changes beyond the "
                "model-selected tolerance from the bound geometry digest."
            ),
            entity_scope="operation_targets",
            parameter_schema=_object_schema(
                {
                    "maximum_center_shift_m": {
                        "type": "number",
                        "minimum": 0.001,
                        "maximum": maximum_geometry_shift_m,
                        "description": (
                            "Upper bound is the smallest visible dimension of "
                            "the current operation targets."
                        ),
                    },
                    "maximum_extent_change_fraction": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 2.0,
                    },
                },
                required=(
                    "maximum_center_shift_m",
                    "maximum_extent_change_fraction",
                ),
            ),
            mandatory=True,
        ),
        LeaseInvalidationCandidate(
            condition_id="lease.membership_changed",
            evidence_source_id="scene.membership_lease",
            description="Stop if the task-neutral scene membership lease changes.",
            entity_scope="none",
            parameter_schema=empty,
            mandatory=True,
        ),
        LeaseInvalidationCandidate(
            condition_id="provider.instance_changed",
            evidence_source_id="world_effect.provider_session",
            description="Stop if the selected provider instance changes.",
            entity_scope="none",
            parameter_schema=empty,
            mandatory=True,
        ),
    ]

    def add_linked(
        condition_id: str,
        source_id: str,
        description: str,
        fields: tuple[str, ...],
    ) -> None:
        if all(field in properties for field in fields):
            items.append(
                LeaseInvalidationCandidate(
                    condition_id=condition_id,
                    evidence_source_id=source_id,
                    description=description,
                    entity_scope="operation_targets",
                    parameter_schema=empty,
                    linked_tool_configuration_fields=fields,
                )
            )

    add_linked(
        "tool.motion_progress_stalled",
        "tool.kinematic_feedback",
        "Stop when configured minimum progress is absent for the configured count.",
        ("minimum_progress_m", "maximum_stalled_observations"),
    )
    add_linked(
        "scene.tracked_pose_error_exceeded",
        "scene.tracked_entity_pose",
        "Stop when tracked target pose error exceeds the configured maximum.",
        ("maximum_tracked_pose_error_m",),
    )
    add_linked(
        "scene.tracked_orientation_error_exceeded",
        "scene.geometry.rgbd",
        "Stop when tracked target orientation error exceeds the configured maximum.",
        ("maximum_tracked_orientation_error_deg", "tracked_object_id"),
    )
    add_linked(
        "scene.observed_clearance_below_minimum",
        "scene.geometry.rgbd",
        "Stop when observed clearance drops below the configured minimum.",
        ("minimum_observed_clearance_m",),
    )
    if "require_contact" in properties:
        items.append(
            LeaseInvalidationCandidate(
                condition_id="contact.required_contact_lost",
                evidence_source_id="actuator.contact_feedback",
                description=(
                    "Select only with require_contact=true. Then stop on contact "
                    "loss or force below the configured minimum; never select "
                    "this condition when require_contact=false."
                ),
                entity_scope="operation_targets",
                parameter_schema=empty,
                linked_tool_configuration_fields=("require_contact",),
            )
        )
    return tuple(items)


def build_shadow_execution_lease_candidates(
    instance: PlanningWorldEffectProviderInstance,
    operation_candidates: WorldEffectOperationCandidateSet,
    operation_decision: WorldEffectOperationDecision,
    inventory: Mapping[str, Any],
) -> ShadowExecutionLeaseCandidateSet:
    """Bind one semantic operation to fresh geometry and a tool config schema."""
    if operation_decision.decision != "propose_operation":
        raise WorldEffectExecutionLeaseError(
            "execution lease candidates require a propose_operation decision"
        )
    if operation_candidates.provider_instance_id != instance.instance_id:
        raise WorldEffectExecutionLeaseError(
            "operation candidate set does not match provider instance"
        )
    selected = next(
        (
            item
            for item in operation_candidates.candidates
            if item.operation_candidate_id == operation_decision.operation_candidate_id
            and item.requirement_id == operation_decision.requirement_id
            and item.tool_id == operation_decision.tool_id
        ),
        None,
    )
    if selected is None:
        raise WorldEffectExecutionLeaseError(
            "operation decision is absent from the exact candidate set"
        )
    activation = next(
        (
            item
            for item in instance.tool_activations
            if item.requirement_id == selected.requirement_id
            and item.activated_tool_id == selected.tool_id
        ),
        None,
    )
    if activation is None:
        raise WorldEffectExecutionLeaseError(
            "selected operation tool is absent from the provider instance"
        )
    raw_schema = activation.tool_advertisement.get("configuration_schema")
    if raw_schema is None:
        raw_schema = _object_schema({})
    if not isinstance(raw_schema, Mapping) or raw_schema.get("type") != "object":
        raise WorldEffectExecutionLeaseError(
            "selected tool configuration_schema must describe an object"
        )
    configuration_schema = _json_copy(raw_schema, "configuration_schema")

    raw_entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(raw_entities, list):
        raise WorldEffectExecutionLeaseError("inventory entities must be an array")
    entities: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw_entities):
        if not isinstance(item, Mapping):
            raise WorldEffectExecutionLeaseError(
                f"inventory entities[{index}] must be an object"
            )
        entity_id = _identifier(
            item.get("entity_id"), f"inventory entities[{index}].entity_id"
        )
        if entity_id in entities:
            raise WorldEffectExecutionLeaseError(
                f"inventory contains duplicate entity id {entity_id!r}"
            )
        entities[entity_id] = item

    target_ids = operation_decision.target_entity_ids
    related_ids = instance.related_entity_ids()
    bindings: list[GeometryEvidenceBinding] = []
    for entity_id in related_ids:
        entity = entities.get(entity_id)
        if entity is None:
            if entity_id in target_ids:
                raise WorldEffectExecutionLeaseError(
                    f"operation target {entity_id!r} is absent from fresh inventory"
                )
            continue
        geometry = entity.get("geometry")
        status = entity.get("observation_status")
        if not isinstance(geometry, Mapping) or not geometry:
            if entity_id in target_ids:
                raise WorldEffectExecutionLeaseError(
                    f"operation target {entity_id!r} lacks fresh geometry"
                )
            continue
        bindings.append(
            GeometryEvidenceBinding(
                entity_id=entity_id,
                observation_status=_identifier(
                    status, f"inventory entity {entity_id}.observation_status"
                ),
                geometry_digest="geometry:" + _digest(geometry),
                geometry=_json_copy(geometry, f"entities.{entity_id}.geometry"),
            )
        )
    if not set(target_ids).issubset({item.entity_id for item in bindings}):
        raise WorldEffectExecutionLeaseError(
            "all operation targets require fresh geometry bindings"
        )

    invalidations = _invalidation_candidates(
        configuration_schema,
        bindings,
        target_ids,
    )
    seed = {
        "provider_instance_id": instance.instance_id,
        "membership_lease_id": instance.membership_lease_id,
        "operation_decision": operation_decision.to_dict(),
        "tool_configuration_schema": configuration_schema,
        "geometry_bindings": [item.to_dict() for item in bindings],
        "invalidation_candidates": [item.to_dict() for item in invalidations],
    }
    candidate_id = "execution-lease:" + _digest(seed)
    candidate = ShadowExecutionLeaseCandidate(
        candidate_id=candidate_id,
        provider_instance_id=instance.instance_id,
        membership_lease_id=instance.membership_lease_id,
        operation_observation_id=operation_decision.observation_id,
        operation_candidate_id=selected.operation_candidate_id,
        requirement_id=selected.requirement_id,
        tool_id=selected.tool_id,
        tool_family=selected.tool_family,
        purpose=operation_decision.purpose or "observe",
        operation_target_entity_ids=target_ids,
        desired_outcome=operation_decision.desired_outcome or "Observe again.",
        stop_condition=operation_decision.stop_condition or "Observe again.",
        tool_configuration_schema=configuration_schema,
        geometry_bindings=tuple(bindings),
        invalidation_candidates=invalidations,
    )
    inventory_digest = "inventory:" + _digest(inventory)
    observation_id = "execution-lease-observation:" + _digest(
        {
            "inventory_digest": inventory_digest,
            "candidate": candidate.to_dict(),
        }
    )
    return ShadowExecutionLeaseCandidateSet(
        observation_id=observation_id,
        inventory_digest=inventory_digest,
        provider_instance_id=instance.instance_id,
        candidates=(candidate,),
    )


def _validate_schema_value(value: Any, schema: Mapping[str, Any], path: str) -> Any:
    expected = schema.get("type")
    if isinstance(expected, list):
        if value is None and "null" in expected:
            return None
        non_null = [item for item in expected if item != "null"]
        if len(non_null) != 1:
            raise WorldEffectExecutionLeaseError(f"{path} has unsupported union type")
        expected = non_null[0]
    if expected == "object":
        if not isinstance(value, Mapping):
            raise WorldEffectExecutionLeaseError(f"{path} must be an object")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise WorldEffectExecutionLeaseError(f"{path}.properties must be an object")
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise WorldEffectExecutionLeaseError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectExecutionLeaseError(
                f"{path} is missing fields: {sorted(missing)}"
            )
        return {
            key: _validate_schema_value(item, properties[key], f"{path}.{key}")
            if key in properties
            else _json_copy(item, f"{path}.{key}")
            for key, item in value.items()
        }
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorldEffectExecutionLeaseError(f"{path} must be a number")
        result = float(value)
        if not math.isfinite(result):
            raise WorldEffectExecutionLeaseError(f"{path} must be finite")
        if "minimum" in schema and result < float(schema["minimum"]):
            raise WorldEffectExecutionLeaseError(f"{path} is below its minimum")
        if "maximum" in schema and result > float(schema["maximum"]):
            raise WorldEffectExecutionLeaseError(f"{path} exceeds its maximum")
        return result
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorldEffectExecutionLeaseError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise WorldEffectExecutionLeaseError(f"{path} is below its minimum")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise WorldEffectExecutionLeaseError(f"{path} exceeds its maximum")
        return value
    if expected == "boolean":
        if not isinstance(value, bool):
            raise WorldEffectExecutionLeaseError(f"{path} must be boolean")
        return value
    if expected == "string":
        if not isinstance(value, str):
            raise WorldEffectExecutionLeaseError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise WorldEffectExecutionLeaseError(f"{path} is not an allowed value")
        return value
    raise WorldEffectExecutionLeaseError(
        f"{path} uses unsupported schema type {expected!r}"
    )


@dataclass(frozen=True)
class LeaseInvalidationSelection:
    condition_id: str
    target_entity_ids: tuple[str, ...]
    parameters: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "target_entity_ids": list(self.target_entity_ids),
            "parameters": _json_copy(self.parameters, "parameters"),
        }


@dataclass(frozen=True)
class ShadowExecutionLeaseDecision:
    observation_id: str
    decision: str
    lease_id: str | None
    candidate_id: str | None
    provider_instance_id: str | None
    operation_candidate_id: str | None
    tool_id: str | None
    grounding_entity_ids: tuple[str, ...]
    tool_configuration: Mapping[str, Any]
    invalidation_conditions: tuple[LeaseInvalidationSelection, ...]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "lease_id": self.lease_id,
            "candidate_id": self.candidate_id,
            "provider_instance_id": self.provider_instance_id,
            "operation_candidate_id": self.operation_candidate_id,
            "tool_id": self.tool_id,
            "grounding_entity_ids": list(self.grounding_entity_ids),
            "tool_configuration": _json_copy(
                self.tool_configuration, "tool_configuration"
            ),
            "invalidation_conditions": [
                item.to_dict() for item in self.invalidation_conditions
            ],
            "confidence": self.confidence,
            "reason": self.reason,
            "observation_policy": "event_or_completion",
            "configuration_validated": self.decision == "propose_lease",
            "execution_lease_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


class ShadowExecutionLeaseGate:
    """Validate a model-authored lease proposal without issuing the lease."""

    def __init__(self, candidate_set: ShadowExecutionLeaseCandidateSet) -> None:
        self.candidate_set = candidate_set
        self._candidates = {item.candidate_id: item for item in candidate_set.candidates}

    def dispatch(self, payload: Mapping[str, Any]) -> ShadowExecutionLeaseDecision:
        if not isinstance(payload, Mapping):
            raise WorldEffectExecutionLeaseError("lease proposal must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "candidate_id",
            "provider_instance_id",
            "operation_candidate_id",
            "tool_id",
            "grounding_entity_ids",
            "tool_configuration",
            "invalidation_conditions",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldEffectExecutionLeaseError(
                f"lease proposal contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectExecutionLeaseError(
                f"lease proposal is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION:
            raise WorldEffectExecutionLeaseError("lease schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.candidate_set.observation_id:
            raise WorldEffectExecutionLeaseError("stale execution-lease observation_id")
        decision = _text(payload["decision"], "decision")
        if decision not in WORLD_EFFECT_EXECUTION_LEASE_DECISIONS:
            raise WorldEffectExecutionLeaseError(
                f"unsupported execution-lease decision {decision!r}"
            )
        proposal_fields = (
            "candidate_id",
            "provider_instance_id",
            "operation_candidate_id",
            "tool_id",
        )
        raw_groundings = payload["grounding_entity_ids"]
        raw_configuration = payload["tool_configuration"]
        raw_invalidations = payload["invalidation_conditions"]
        if not isinstance(raw_groundings, list):
            raise WorldEffectExecutionLeaseError("grounding_entity_ids must be an array")
        if not isinstance(raw_configuration, Mapping):
            raise WorldEffectExecutionLeaseError("tool_configuration must be an object")
        if not isinstance(raw_invalidations, list):
            raise WorldEffectExecutionLeaseError(
                "invalidation_conditions must be an array"
            )
        if decision != "propose_lease":
            if any(payload[field] is not None for field in proposal_fields):
                raise WorldEffectExecutionLeaseError(
                    f"decision {decision!r} requires null proposal ids"
                )
            if raw_groundings or raw_configuration or raw_invalidations:
                raise WorldEffectExecutionLeaseError(
                    f"decision {decision!r} requires empty lease fields"
                )
            return ShadowExecutionLeaseDecision(
                observation_id=observation_id,
                decision=decision,
                lease_id=None,
                candidate_id=None,
                provider_instance_id=None,
                operation_candidate_id=None,
                tool_id=None,
                grounding_entity_ids=(),
                tool_configuration={},
                invalidation_conditions=(),
                confidence=_confidence(payload["confidence"]),
                reason=_text(payload["reason"], "reason"),
            )

        candidate_id = _identifier(payload["candidate_id"], "candidate_id")
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise WorldEffectExecutionLeaseError(
                "selected execution-lease candidate was not advertised"
            )
        provider_instance_id = _identifier(
            payload["provider_instance_id"], "provider_instance_id"
        )
        operation_candidate_id = _identifier(
            payload["operation_candidate_id"], "operation_candidate_id"
        )
        tool_id = _identifier(payload["tool_id"], "tool_id")
        if (
            provider_instance_id != candidate.provider_instance_id
            or operation_candidate_id != candidate.operation_candidate_id
            or tool_id != candidate.tool_id
        ):
            raise WorldEffectExecutionLeaseError(
                "lease provider/operation/tool triple was not advertised"
            )
        grounding_ids = tuple(
            _identifier(item, f"grounding_entity_ids[{index}]")
            for index, item in enumerate(raw_groundings)
        )
        if len(grounding_ids) != len(set(grounding_ids)):
            raise WorldEffectExecutionLeaseError(
                "grounding_entity_ids must not contain duplicates"
            )
        available_groundings = {item.entity_id for item in candidate.geometry_bindings}
        if not set(candidate.operation_target_entity_ids).issubset(grounding_ids):
            raise WorldEffectExecutionLeaseError(
                "lease grounding must include every operation target"
            )
        if not set(grounding_ids).issubset(available_groundings):
            raise WorldEffectExecutionLeaseError(
                "lease grounding contains an entity without fresh geometry"
            )
        configuration = _validate_schema_value(
            raw_configuration,
            candidate.tool_configuration_schema,
            "tool_configuration",
        )
        invalidation_by_id = {
            item.condition_id: item for item in candidate.invalidation_candidates
        }
        selections: list[LeaseInvalidationSelection] = []
        selected_ids: set[str] = set()
        for index, raw in enumerate(raw_invalidations):
            if not isinstance(raw, Mapping):
                raise WorldEffectExecutionLeaseError(
                    f"invalidation_conditions[{index}] must be an object"
                )
            if set(raw) != {"condition_id", "target_entity_ids", "parameters"}:
                raise WorldEffectExecutionLeaseError(
                    f"invalidation_conditions[{index}] has invalid fields"
                )
            condition_id = _identifier(
                raw["condition_id"], f"invalidation_conditions[{index}].condition_id"
            )
            spec = invalidation_by_id.get(condition_id)
            if spec is None:
                raise WorldEffectExecutionLeaseError(
                    f"invalidation condition {condition_id!r} was not advertised"
                )
            if condition_id in selected_ids:
                raise WorldEffectExecutionLeaseError(
                    "invalidation conditions must not contain duplicates"
                )
            selected_ids.add(condition_id)
            raw_targets = raw["target_entity_ids"]
            if not isinstance(raw_targets, list):
                raise WorldEffectExecutionLeaseError(
                    f"invalidation_conditions[{index}].target_entity_ids must be an array"
                )
            targets = tuple(
                _identifier(item, f"invalidation_conditions[{index}].target_entity_ids")
                for item in raw_targets
            )
            if len(targets) != len(set(targets)):
                raise WorldEffectExecutionLeaseError(
                    "invalidation target_entity_ids must not contain duplicates"
                )
            expected_targets = (
                candidate.operation_target_entity_ids
                if spec.entity_scope == "operation_targets"
                else ()
            )
            if set(targets) != set(expected_targets):
                raise WorldEffectExecutionLeaseError(
                    f"condition {condition_id!r} requires exact targets {expected_targets}"
                )
            parameters = _validate_schema_value(
                raw["parameters"],
                spec.parameter_schema,
                f"invalidation_conditions[{index}].parameters",
            )
            missing_linked = set(spec.linked_tool_configuration_fields) - set(
                configuration
            )
            if missing_linked:
                raise WorldEffectExecutionLeaseError(
                    f"condition {condition_id!r} requires tool configuration fields "
                    f"{sorted(missing_linked)}"
                )
            if condition_id == "contact.required_contact_lost" and not configuration.get(
                "require_contact"
            ):
                raise WorldEffectExecutionLeaseError(
                    "contact invalidation requires require_contact=true"
                )
            selections.append(
                LeaseInvalidationSelection(
                    condition_id=condition_id,
                    target_entity_ids=targets,
                    parameters=parameters,
                )
            )
        missing_mandatory = set(candidate.mandatory_condition_ids()) - selected_ids
        if missing_mandatory:
            raise WorldEffectExecutionLeaseError(
                f"lease is missing mandatory invalidations: {sorted(missing_mandatory)}"
            )
        linked_activation = {
            "maximum_tracked_pose_error_m": "scene.tracked_pose_error_exceeded",
            "maximum_tracked_orientation_error_deg": (
                "scene.tracked_orientation_error_exceeded"
            ),
            "minimum_observed_clearance_m": (
                "scene.observed_clearance_below_minimum"
            ),
        }
        for field, condition_id in linked_activation.items():
            if field in configuration and condition_id not in selected_ids:
                raise WorldEffectExecutionLeaseError(
                    f"configured field {field!r} requires invalidation {condition_id!r}"
                )
        if configuration.get("require_contact") and (
            "contact.required_contact_lost" not in selected_ids
        ):
            raise WorldEffectExecutionLeaseError(
                "require_contact=true requires contact invalidation"
            )
        progress_fields = {"minimum_progress_m", "maximum_stalled_observations"}
        if progress_fields.intersection(configuration) and (
            "tool.motion_progress_stalled" not in selected_ids
        ):
            raise WorldEffectExecutionLeaseError(
                "configured motion progress fields require stalled-motion invalidation"
            )
        lease_seed = {
            "observation_id": observation_id,
            "candidate_id": candidate_id,
            "grounding_entity_ids": grounding_ids,
            "tool_configuration": configuration,
            "invalidation_conditions": [item.to_dict() for item in selections],
        }
        lease_id = "shadow-execution-lease:" + _digest(lease_seed)
        return ShadowExecutionLeaseDecision(
            observation_id=observation_id,
            decision=decision,
            lease_id=lease_id,
            candidate_id=candidate_id,
            provider_instance_id=provider_instance_id,
            operation_candidate_id=operation_candidate_id,
            tool_id=tool_id,
            grounding_entity_ids=grounding_ids,
            tool_configuration=configuration,
            invalidation_conditions=tuple(selections),
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def revalidate_shadow_execution_lease_decision(
    candidate_set: ShadowExecutionLeaseCandidateSet,
    decision: ShadowExecutionLeaseDecision,
) -> ShadowExecutionLeaseDecision:
    """Re-run the complete lease gate before crossing an authority boundary."""
    payload = {
        "schema_version": WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION,
        "observation_id": decision.observation_id,
        "decision": decision.decision,
        "candidate_id": decision.candidate_id,
        "provider_instance_id": decision.provider_instance_id,
        "operation_candidate_id": decision.operation_candidate_id,
        "tool_id": decision.tool_id,
        "grounding_entity_ids": list(decision.grounding_entity_ids),
        "tool_configuration": _json_copy(
            decision.tool_configuration, "tool_configuration"
        ),
        "invalidation_conditions": [
            item.to_dict() for item in decision.invalidation_conditions
        ],
        "confidence": decision.confidence,
        "reason": decision.reason,
    }
    validated = ShadowExecutionLeaseGate(candidate_set).dispatch(payload)
    if validated.lease_id != decision.lease_id:
        raise WorldEffectExecutionLeaseError(
            "execution lease identity changed during authority revalidation"
        )
    return validated


def shadow_execution_lease_json_schema(
    candidate_set: ShadowExecutionLeaseCandidateSet,
) -> dict[str, Any]:
    candidate_ids = [item.candidate_id for item in candidate_set.candidates]
    provider_ids = sorted({item.provider_instance_id for item in candidate_set.candidates})
    operation_ids = sorted({item.operation_candidate_id for item in candidate_set.candidates})
    tool_ids = sorted({item.tool_id for item in candidate_set.candidates})
    grounding_ids = sorted(
        {binding.entity_id for item in candidate_set.candidates for binding in item.geometry_bindings}
    )
    condition_ids = sorted(
        {
            condition.condition_id
            for item in candidate_set.candidates
            for condition in item.invalidation_candidates
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
            "provider_instance_id",
            "operation_candidate_id",
            "tool_id",
            "grounding_entity_ids",
            "tool_configuration",
            "invalidation_conditions",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_EFFECT_EXECUTION_LEASE_SCHEMA_VERSION},
            "observation_id": {"const": candidate_set.observation_id},
            "decision": {"enum": sorted(WORLD_EFFECT_EXECUTION_LEASE_DECISIONS)},
            "candidate_id": {"type": ["string", "null"], "enum": [None, *candidate_ids]},
            "provider_instance_id": {
                "type": ["string", "null"],
                "enum": [None, *provider_ids],
            },
            "operation_candidate_id": {
                "type": ["string", "null"],
                "enum": [None, *operation_ids],
            },
            "tool_id": {"type": ["string", "null"], "enum": [None, *tool_ids]},
            "grounding_entity_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": grounding_ids},
            },
            "tool_configuration": {"type": "object"},
            "invalidation_conditions": {
                "type": "array",
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["condition_id", "target_entity_ids", "parameters"],
                    "properties": {
                        "condition_id": {"enum": condition_ids},
                        "target_entity_ids": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"enum": grounding_ids},
                        },
                        "parameters": {"type": "object"},
                    },
                },
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_shadow_execution_lease_prompt(
    *,
    instruction: str,
    candidate_set: ShadowExecutionLeaseCandidateSet,
) -> str:
    """Ask for tool configuration and invalidations, never a dispatch call."""
    instruction = _text(instruction, "instruction")
    return f"""Propose a fresh, geometry-grounded shadow execution lease for the
validated semantic operation.

Human instruction:
{instruction}

Exact runtime lease candidate:
{json.dumps(candidate_set.to_dict(), indent=2)}

Choose propose_lease only for the exact advertised candidate, provider instance,
operation candidate, and tool. Include every operation target in
grounding_entity_ids. Configure the tool only through its advertised
tool_configuration_schema. Select every mandatory invalidation condition. A
condition with linked_tool_configuration_fields may be selected only when all
those fields are supplied. Conversely, configured tracking, clearance, contact,
or progress thresholds require their corresponding invalidation condition.
The contact.required_contact_lost condition is valid only when
tool_configuration.require_contact is true; omit it when require_contact is
false or absent.
Each invalidation selection must use the exact target list required by its
entity_scope and parameters matching its parameter_schema.

The lease is event-or-completion based: it can cover many local runtime steps,
but fresh evidence ends it immediately when a selected condition fires. This is
still shadow-only. It validates configuration but does not issue a lease, bind a
handler, call a tool, or dispatch movement. Do not provide a target pose, pose
delta, trajectory, actuator command, joint value, motor command, or tool
arguments. Use observe_again if the geometry is insufficient and blocked if no
safe lease can be formed.

Return exactly one JSON object matching this response schema, with no Markdown:
{json.dumps(shadow_execution_lease_json_schema(candidate_set), indent=2, sort_keys=True)}
"""
