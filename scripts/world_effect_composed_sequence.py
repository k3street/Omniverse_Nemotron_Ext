"""One-call, sensor-invalidated compositions of runtime world-effect tools.

The model proposes an ordered list of *draft* runtime tool invocations in one
response.  Drafts contain geometry-relative arguments and no execution
authority.  The live runner rebinds each draft to fresh RGB-D/contact evidence,
revalidates it through the existing operation, lease, and invocation gates, and
issues only one single-use runtime lease at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

try:
    from .world_effect_operation_plan import (
        PlanningWorldEffectProviderInstance,
        WORLD_EFFECT_OPERATION_PURPOSES,
        WorldEffectOperationCandidate,
        WorldEffectOperationCandidateSet,
    )
except ImportError:  # Script execution adds this directory to sys.path.
    from world_effect_operation_plan import (  # type: ignore[no-redef]
        PlanningWorldEffectProviderInstance,
        WORLD_EFFECT_OPERATION_PURPOSES,
        WorldEffectOperationCandidate,
        WorldEffectOperationCandidateSet,
    )


WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION = (
    "world-effect-composed-tool-sequence.v1"
)
WORLD_EFFECT_COMPOSED_SEQUENCE_DECISIONS = frozenset(
    {"propose_sequence", "observe_again", "blocked"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectComposedSequenceError(ValueError):
    """Raised when a composed tool queue exceeds its advertised contract."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectComposedSequenceError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectComposedSequenceError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectComposedSequenceError(
            "confidence must be a number in [0, 1]"
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectComposedSequenceError(
            "confidence must be a number in [0, 1]"
        )
    return result


def _finite_number(value: Any, path: str, *, minimum: float = -math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectComposedSequenceError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise WorldEffectComposedSequenceError(
            f"{path} must be finite and at least {minimum}"
        )
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 16:
        raise WorldEffectComposedSequenceError(
            f"{path} exceeds maximum nesting depth"
        )
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectComposedSequenceError(
                f"{path} must contain finite numbers"
            )
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
                raise WorldEffectComposedSequenceError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(
                item, f"{path}.{key}", depth=depth + 1
            )
        return result
    raise WorldEffectComposedSequenceError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class GeometryDriftTolerance:
    maximum_center_shift_m: float
    maximum_extent_change_fraction: float

    def __post_init__(self) -> None:
        _finite_number(
            self.maximum_center_shift_m,
            "maximum_center_shift_m",
            minimum=0.001,
        )
        _finite_number(
            self.maximum_extent_change_fraction,
            "maximum_extent_change_fraction",
            minimum=0.0,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "maximum_center_shift_m": self.maximum_center_shift_m,
            "maximum_extent_change_fraction": (
                self.maximum_extent_change_fraction
            ),
        }


@dataclass(frozen=True)
class ComposedToolCallDraft:
    call_id: str
    requirement_id: str
    tool_id: str
    tool_family: str
    semantic_effect_id: str | None
    purpose: str
    target_entity_ids: tuple[str, ...]
    desired_outcome: str
    stop_condition: str
    tool_configuration: Mapping[str, Any]
    geometry_drift_tolerance: GeometryDriftTolerance
    position_anchor_id: str | None
    interaction_offset_from_anchor_m: tuple[float, ...]
    orientation_alignment_id: str | None
    invocation_arguments: Mapping[str, Any]
    expected_state_change: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "requirement_id": self.requirement_id,
            "tool_id": self.tool_id,
            "tool_family": self.tool_family,
            "semantic_effect_id": self.semantic_effect_id,
            "purpose": self.purpose,
            "target_entity_ids": list(self.target_entity_ids),
            "desired_outcome": self.desired_outcome,
            "stop_condition": self.stop_condition,
            "tool_configuration": _json_copy(
                self.tool_configuration, "tool_configuration"
            ),
            "geometry_drift_tolerance": (
                self.geometry_drift_tolerance.to_dict()
            ),
            "position_anchor_id": self.position_anchor_id,
            "interaction_offset_from_anchor_m": list(
                self.interaction_offset_from_anchor_m
            ),
            "orientation_alignment_id": self.orientation_alignment_id,
            "invocation_arguments": _json_copy(
                self.invocation_arguments, "invocation_arguments"
            ),
            "expected_state_change": self.expected_state_change,
            "reason": self.reason,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class ComposedToolSequenceCandidateSet:
    observation_id: str
    goal_id: str
    maximum_tool_calls: int
    related_entity_ids: tuple[str, ...]
    operation_candidates: WorldEffectOperationCandidateSet
    provider_instance: PlanningWorldEffectProviderInstance
    grounding_catalog: Mapping[str, Any]
    execution_context: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "goal_id": self.goal_id,
            "maximum_tool_calls": self.maximum_tool_calls,
            "related_entity_ids": list(self.related_entity_ids),
            "operation_candidates": self.operation_candidates.to_dict(),
            "planning_provider_instance": self.provider_instance.to_dict(),
            "grounding_catalog": _json_copy(
                self.grounding_catalog, "grounding_catalog"
            ),
            "execution_context": _json_copy(
                self.execution_context, "execution_context"
            ),
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class ComposedToolSequenceDecision:
    observation_id: str
    decision: str
    goal_id: str
    tool_calls: tuple[ComposedToolCallDraft, ...]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "goal_id": self.goal_id,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "confidence": self.confidence,
            "reason": self.reason,
            "queue_authority": "pending_fresh_evidence_per_call",
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def _grounding_catalog(
    inventory: Mapping[str, Any],
    execution_context: Mapping[str, Any],
    *,
    allowed_entity_ids: frozenset[str],
) -> dict[str, Any]:
    anchors: list[dict[str, Any]] = []
    axes: list[dict[str, Any]] = []
    geometry_limits: list[dict[str, Any]] = []
    for raw_entity in inventory.get("entities", []):
        if not isinstance(raw_entity, Mapping):
            continue
        entity_id = raw_entity.get("entity_id")
        geometry = raw_entity.get("geometry")
        if not isinstance(entity_id, str) or not isinstance(geometry, Mapping):
            continue
        if entity_id not in allowed_entity_ids:
            continue
        center = geometry.get("center_base_m")
        extent = geometry.get("visible_extent_base_m")
        positive_dimensions: list[float] = []
        half_extent = (0.05, 0.05, 0.05)
        if (
            isinstance(extent, (list, tuple))
            and len(extent) == 3
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in extent
            )
        ):
            normalized_extent = tuple(
                max(0.001, float(value)) for value in extent
            )
            half_extent = tuple(0.5 * value for value in normalized_extent)
            positive_dimensions.extend(
                float(value)
                for value in extent
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and float(value) > 0.0
            )
        if isinstance(center, (list, tuple)) and len(center) == 3:
            anchors.append(
                {
                    "anchor_id": f"{entity_id}.center",
                    "entity_id": entity_id,
                    "position_m": _json_copy(center, "center_base_m"),
                    "visible_extent_m": _json_copy(
                        extent if isinstance(extent, (list, tuple)) else [],
                        "visible_extent_base_m",
                    ),
                    "offset_min_m": [
                        -half_extent[0],
                        -half_extent[1],
                        -half_extent[2],
                    ],
                    "offset_max_m": [
                        half_extent[0],
                        half_extent[1],
                        0.35,
                    ],
                }
            )
        lower = geometry.get("visible_aabb_min_base_m")
        upper = geometry.get("visible_aabb_max_base_m")
        if (
            isinstance(lower, (list, tuple))
            and len(lower) == 3
            and isinstance(upper, (list, tuple))
            and len(upper) == 3
        ):
            positive_dimensions.extend(
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
            anchors.append(
                {
                    "anchor_id": f"{entity_id}.visible_aabb_top_center",
                    "entity_id": entity_id,
                    "position_m": [
                        0.5 * (float(lower[0]) + float(upper[0])),
                        0.5 * (float(lower[1]) + float(upper[1])),
                        float(upper[2]),
                    ],
                    "visible_extent_m": _json_copy(
                        extent if isinstance(extent, (list, tuple)) else [],
                        "visible_extent_base_m",
                    ),
                    "offset_min_m": [
                        -half_extent[0],
                        -half_extent[1],
                        0.0,
                    ],
                    "offset_max_m": [
                        half_extent[0],
                        half_extent[1],
                        0.35,
                    ],
                }
            )
        if positive_dimensions:
            geometry_limits.append(
                {
                    "entity_id": entity_id,
                    "maximum_center_shift_m": max(
                        0.001, min(0.50, min(positive_dimensions))
                    ),
                    "source": "smallest_visible_target_dimension",
                }
            )
        support_normal = geometry.get("support_plane_normal_base")
        normalized_support: tuple[float, float, float] | None = None
        if isinstance(support_normal, (list, tuple)) and len(support_normal) == 3:
            support_norm = math.sqrt(
                sum(float(value) ** 2 for value in support_normal)
            )
            if support_norm > 1.0e-9:
                normalized_support = tuple(
                    float(value) / support_norm for value in support_normal
                )
        for field in (
            "oriented_footprint_axes_base",
            "principal_axes_base",
        ):
            raw_axes = geometry.get(field)
            if not isinstance(raw_axes, (list, tuple)):
                continue
            for index, axis in enumerate(raw_axes):
                if isinstance(axis, (list, tuple)) and len(axis) == 3:
                    axis_norm = math.sqrt(sum(float(value) ** 2 for value in axis))
                    if axis_norm <= 1.0e-9:
                        continue
                    normalized_axis = tuple(
                        float(value) / axis_norm for value in axis
                    )
                    if normalized_support is not None and abs(
                        sum(
                            left * right
                            for left, right in zip(
                                normalized_axis, normalized_support
                            )
                        )
                    ) > math.sin(math.radians(15.0)):
                        continue
                    axes.append(
                        {
                            "orientation_alignment_id": (
                                f"{entity_id}.{field}.{index}"
                            ),
                            "entity_id": entity_id,
                            "axis_robot_root": list(normalized_axis),
                        }
                    )
    interaction = execution_context.get("interaction_frame")
    interaction = interaction if isinstance(interaction, Mapping) else {}
    return {
        "coordinate_frame": inventory.get("frame", "unknown"),
        "position_anchors": anchors,
        "orientation_axes": axes,
        "geometry_drift_limits": geometry_limits,
        "interaction_origin_offset_local_m": interaction.get(
            "contact_center_local_m"
        ),
        "interaction_alignment_axis_local": interaction.get(
            "closing_axis_local"
        ),
        "alignment_relation": "surface_tangent",
    }


def build_unbound_composed_grounding_catalog(
    inventory: Mapping[str, Any],
    execution_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Advertise scene-relative grounding before a provider is selected.

    A bundled task-planning response must be able to name generic RGB-D anchors
    and axes in the same model call that selects its goal and provider.  This
    catalog grants no tool or execution authority; the selected provider and
    every queued call are still rebound through the normal typed gates.
    """
    entity_ids = frozenset(
        str(item.get("entity_id"))
        for item in inventory.get("entities", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("entity_id"), str)
    )
    return _grounding_catalog(
        inventory,
        execution_context,
        allowed_entity_ids=entity_ids,
    )


def rebind_composed_tool_call_to_fresh_interaction_relation(
    step: ComposedToolCallDraft,
    execution_context: Mapping[str, Any],
) -> tuple[ComposedToolCallDraft, dict[str, Any] | None]:
    """Rebind a pending interaction motion to the latest sensed relation."""
    if (
        step.tool_family != "motion"
        or step.tool_configuration.get("require_interaction_relation") is not True
    ):
        return step, None
    alignment = execution_context.get("two_pad_grasp_alignment")
    if not isinstance(alignment, Mapping) or alignment.get("available") is not True:
        return step, None
    if alignment.get("object_center_inside_full_grasp_corridor") is not False:
        return step, None
    contract = alignment.get("corrective_motion_grounding_contract")
    if not isinstance(contract, Mapping):
        return step, None
    anchor_id = _identifier(
        contract.get("required_terminal_position_anchor_id"),
        "corrective_motion_grounding_contract.required_terminal_position_anchor_id",
    )
    raw_offset = contract.get(
        "required_terminal_interaction_offset_from_anchor_m"
    )
    if not isinstance(raw_offset, Sequence) or isinstance(raw_offset, (str, bytes)):
        raise WorldEffectComposedSequenceError(
            "fresh corrective interaction offset must be a three-value array"
        )
    offset = tuple(
        _finite_number(
            value,
            "corrective_motion_grounding_contract."
            "required_terminal_interaction_offset_from_anchor_m",
        )
        for value in raw_offset
    )
    if len(offset) != 3:
        raise WorldEffectComposedSequenceError(
            "fresh corrective interaction offset must have three values"
        )
    before = {
        "position_anchor_id": step.position_anchor_id,
        "interaction_offset_from_anchor_m": list(
            step.interaction_offset_from_anchor_m
        ),
    }
    arguments = dict(step.invocation_arguments)
    waypoints = arguments.get("ordered_waypoints")
    if isinstance(waypoints, list) and waypoints:
        rebound_waypoints = [dict(item) for item in waypoints]
        rebound_waypoints[-1]["position_anchor_id"] = anchor_id
        rebound_waypoints[-1]["interaction_offset_from_anchor_m"] = list(offset)
        arguments["ordered_waypoints"] = rebound_waypoints
        rebound = replace(step, invocation_arguments=arguments)
    else:
        rebound = replace(
            step,
            position_anchor_id=anchor_id,
            interaction_offset_from_anchor_m=offset,
        )
    configuration = dict(rebound.tool_configuration)
    fresh_tolerance = contract.get("maximum_terminal_position_error_m")
    if (
        isinstance(fresh_tolerance, (int, float))
        and not isinstance(fresh_tolerance, bool)
        and math.isfinite(float(fresh_tolerance))
        and 0.001 <= float(fresh_tolerance) <= 0.05
    ):
        requested = configuration.get("position_tolerance_m")
        configuration["position_tolerance_m"] = min(
            float(fresh_tolerance),
            float(requested)
            if isinstance(requested, (int, float))
            and not isinstance(requested, bool)
            and math.isfinite(float(requested))
            else float(fresh_tolerance),
        )
        rebound = replace(rebound, tool_configuration=configuration)
    return rebound, {
        "source": "fresh_rgbd_plus_runtime_interaction_geometry",
        "before": before,
        "after": {
            "position_anchor_id": anchor_id,
            "interaction_offset_from_anchor_m": list(offset),
        },
        "model_called": False,
        "execution_authority": False,
    }


def admit_fresh_contact_egress_motion(
    step: ComposedToolCallDraft,
    execution_context: Mapping[str, Any],
    *,
    fresh_replan_after_invalidation: bool,
) -> tuple[ComposedToolCallDraft, dict[str, Any] | None]:
    """Let one freshly reasoned motion escape already-observed contact.

    The no-contact lease prevents a new pre-grasp collision.  Once that lease
    has fired, however, the first motion from the resulting fresh model plan
    must be able to leave the existing contact state.  Later queued approach
    motions keep their normal no-contact lease.
    """
    contact = execution_context.get("current_contact")
    if (
        not fresh_replan_after_invalidation
        or step.tool_family != "motion"
        or step.tool_configuration.get("forbid_contact") is not True
        or not isinstance(contact, Mapping)
        or contact.get("touch") is not True
    ):
        return step, None
    configuration = dict(step.tool_configuration)
    configuration.pop("forbid_contact", None)
    rebound = replace(step, tool_configuration=configuration)
    return rebound, {
        "source": "fresh_post_invalidation_contact_state",
        "reason": "allow_first_freshly_reasoned_motion_to_egress_existing_contact",
        "observed_touch": True,
        "observed_net_force_n": contact.get("net_force_n"),
        "model_called_for_recovery_plan": True,
        "applies_to_this_call_only": True,
        "execution_authority": False,
    }


def rebind_loaded_motion_targets_to_fresh_attachment(
    step: ComposedToolCallDraft,
    inventory: Mapping[str, Any],
) -> tuple[ComposedToolCallDraft, dict[str, Any] | None]:
    """Carry fresh retained-attachment lineage into a loaded motion call."""
    if (
        step.tool_family != "motion"
        or step.tool_configuration.get("require_contact") is not True
    ):
        return step, None
    continuation = inventory.get("world_effect_continuation_evidence")
    if (
        not isinstance(continuation, Mapping)
        or continuation.get("retained_contact_supported") is not True
    ):
        return step, None
    raw_attachment_ids = continuation.get("attachment_entity_ids")
    if not isinstance(raw_attachment_ids, list) or not raw_attachment_ids:
        return step, None
    inventory_ids = {
        str(item.get("entity_id"))
        for item in inventory.get("entities", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("entity_id"), str)
    }
    attachment_ids = tuple(
        _identifier(entity_id, "attachment_entity_ids")
        for entity_id in raw_attachment_ids
        if isinstance(entity_id, str) and entity_id in inventory_ids
    )
    if not attachment_ids:
        return step, None
    rebound_targets = tuple(
        dict.fromkeys((*step.target_entity_ids, *attachment_ids))
    )
    if rebound_targets == step.target_entity_ids:
        return step, None
    rebound = replace(step, target_entity_ids=rebound_targets)
    return rebound, {
        "source": "fresh_retained_attachment_continuation_evidence",
        "before_target_entity_ids": list(step.target_entity_ids),
        "after_target_entity_ids": list(rebound_targets),
        "attachment_entity_ids": list(attachment_ids),
        "model_called": False,
        "execution_authority": False,
    }


def build_composed_tool_sequence_candidates(
    *,
    instance: PlanningWorldEffectProviderInstance,
    operation_candidates: WorldEffectOperationCandidateSet,
    inventory: Mapping[str, Any],
    execution_context: Mapping[str, Any],
    maximum_tool_calls: int,
) -> ComposedToolSequenceCandidateSet:
    """Build one model-call candidate containing every advertised tool."""
    if isinstance(maximum_tool_calls, bool) or not isinstance(
        maximum_tool_calls, int
    ):
        raise WorldEffectComposedSequenceError(
            "maximum_tool_calls must be an integer"
        )
    if maximum_tool_calls <= 0:
        raise WorldEffectComposedSequenceError(
            "maximum_tool_calls must be positive"
        )
    if operation_candidates.provider_instance_id != instance.instance_id:
        raise WorldEffectComposedSequenceError(
            "operation candidates do not match provider instance"
        )
    executable_operation_candidates = WorldEffectOperationCandidateSet(
        observation_id=operation_candidates.observation_id,
        provider_instance_id=operation_candidates.provider_instance_id,
        related_entity_ids=operation_candidates.related_entity_ids,
        candidates=tuple(
            item
            for item in operation_candidates.candidates
            if item.tool_family in {"motion", "actuator"}
        ),
    )
    if not executable_operation_candidates.candidates:
        raise WorldEffectComposedSequenceError(
            "composition requires an advertised motion or actuator tool"
        )
    catalog = _grounding_catalog(
        inventory,
        execution_context,
        allowed_entity_ids=frozenset(
            executable_operation_candidates.related_entity_ids
        ),
    )
    seed = {
        "goal_id": instance.goal_id,
        "maximum_tool_calls": maximum_tool_calls,
        "operation_candidates": executable_operation_candidates.to_dict(),
        "provider_instance": instance.to_dict(),
        "inventory": _json_copy(inventory, "inventory"),
        "execution_context": _json_copy(execution_context, "execution_context"),
        "grounding_catalog": catalog,
    }
    return ComposedToolSequenceCandidateSet(
        observation_id="composed-tool-sequence-observation:" + _digest(seed),
        goal_id=instance.goal_id,
        maximum_tool_calls=maximum_tool_calls,
        related_entity_ids=operation_candidates.related_entity_ids,
        operation_candidates=executable_operation_candidates,
        provider_instance=instance,
        grounding_catalog=catalog,
        execution_context=_json_copy(execution_context, "execution_context"),
    )


def _candidate_key(
    candidate: WorldEffectOperationCandidate,
) -> tuple[str, str, str | None]:
    return (
        candidate.requirement_id,
        candidate.tool_id,
        candidate.semantic_effect_id,
    )


class ComposedToolSequenceGate:
    """Validate a complete composition while withholding execution authority."""

    def __init__(self, candidate_set: ComposedToolSequenceCandidateSet) -> None:
        self.candidate_set = candidate_set
        self._candidates = {
            _candidate_key(item): item
            for item in candidate_set.operation_candidates.candidates
        }
        catalog = candidate_set.grounding_catalog
        self._anchor_ids = {
            str(item.get("anchor_id"))
            for item in catalog.get("position_anchors", [])
            if isinstance(item, Mapping) and isinstance(item.get("anchor_id"), str)
        }
        self._anchor_envelopes = {
            str(item.get("anchor_id")): (
                tuple(float(value) for value in item.get("offset_min_m", [])),
                tuple(float(value) for value in item.get("offset_max_m", [])),
            )
            for item in catalog.get("position_anchors", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("anchor_id"), str)
            and isinstance(item.get("offset_min_m"), list)
            and len(item.get("offset_min_m")) == 3
            and isinstance(item.get("offset_max_m"), list)
            and len(item.get("offset_max_m")) == 3
        }
        self._anchor_entities = {
            str(item.get("anchor_id")): str(item.get("entity_id"))
            for item in catalog.get("position_anchors", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("anchor_id"), str)
            and isinstance(item.get("entity_id"), str)
        }
        self._axis_ids = {
            str(item.get("orientation_alignment_id"))
            for item in catalog.get("orientation_axes", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("orientation_alignment_id"), str)
        }
        self._axis_entities = {
            str(item.get("orientation_alignment_id")): str(item.get("entity_id"))
            for item in catalog.get("orientation_axes", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("orientation_alignment_id"), str)
            and isinstance(item.get("entity_id"), str)
        }
        self._geometry_drift_limits = {
            str(item.get("entity_id")): float(item["maximum_center_shift_m"])
            for item in catalog.get("geometry_drift_limits", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("entity_id"), str)
            and isinstance(item.get("maximum_center_shift_m"), (int, float))
            and not isinstance(item.get("maximum_center_shift_m"), bool)
        }

    def _tool_call(
        self, raw: Any, index: int
    ) -> ComposedToolCallDraft:
        path = f"tool_calls[{index}]"
        if not isinstance(raw, Mapping):
            raise WorldEffectComposedSequenceError(f"{path} must be an object")
        allowed = {
            "call_id",
            "requirement_id",
            "tool_id",
            "tool_family",
            "semantic_effect_id",
            "purpose",
            "target_entity_ids",
            "desired_outcome",
            "stop_condition",
            "tool_configuration",
            "geometry_drift_tolerance",
            "position_anchor_id",
            "interaction_offset_from_anchor_m",
            "orientation_alignment_id",
            "invocation_arguments",
            "expected_state_change",
            "reason",
        }
        unknown = set(raw) - allowed
        missing = allowed - set(raw)
        if unknown or missing:
            raise WorldEffectComposedSequenceError(
                f"{path} fields mismatch: unknown={sorted(unknown)} "
                f"missing={sorted(missing)}"
            )
        call_id = _identifier(raw["call_id"], f"{path}.call_id")
        requirement_id = _identifier(
            raw["requirement_id"], f"{path}.requirement_id"
        )
        tool_id = _identifier(raw["tool_id"], f"{path}.tool_id")
        semantic_effect_id = raw["semantic_effect_id"]
        if semantic_effect_id is not None:
            semantic_effect_id = _identifier(
                semantic_effect_id, f"{path}.semantic_effect_id"
            )
        candidate = self._candidates.get(
            (requirement_id, tool_id, semantic_effect_id)
        )
        if candidate is None:
            raise WorldEffectComposedSequenceError(
                f"{path} does not select an advertised requirement/tool/effect"
            )
        tool_family = _identifier(raw["tool_family"], f"{path}.tool_family")
        if tool_family != candidate.tool_family:
            raise WorldEffectComposedSequenceError(
                f"{path}.tool_family does not match the advertised tool"
            )
        purpose = _identifier(raw["purpose"], f"{path}.purpose")
        if purpose not in WORLD_EFFECT_OPERATION_PURPOSES:
            raise WorldEffectComposedSequenceError(
                f"{path}.purpose is unsupported"
            )
        raw_targets = raw["target_entity_ids"]
        if not isinstance(raw_targets, list) or not raw_targets:
            raise WorldEffectComposedSequenceError(
                f"{path}.target_entity_ids must be a non-empty array"
            )
        targets = tuple(
            _identifier(item, f"{path}.target_entity_ids[{target_index}]")
            for target_index, item in enumerate(raw_targets)
        )
        if len(set(targets)) != len(targets) or not set(targets).issubset(
            self.candidate_set.related_entity_ids
        ):
            raise WorldEffectComposedSequenceError(
                f"{path}.target_entity_ids must be unique related entities"
            )
        raw_config = raw["tool_configuration"]
        raw_arguments = raw["invocation_arguments"]
        if not isinstance(raw_config, Mapping) or not isinstance(
            raw_arguments, Mapping
        ):
            raise WorldEffectComposedSequenceError(
                f"{path} configuration and invocation_arguments must be objects"
            )
        for field, expected in candidate.required_invocation_arguments.items():
            if raw_arguments.get(field) != expected:
                raise WorldEffectComposedSequenceError(
                    f"{path}.invocation_arguments contradicts semantic effect "
                    f"field {field!r}"
                )
        raw_drift = raw["geometry_drift_tolerance"]
        if not isinstance(raw_drift, Mapping) or set(raw_drift) != {
            "maximum_center_shift_m",
            "maximum_extent_change_fraction",
        }:
            raise WorldEffectComposedSequenceError(
                f"{path}.geometry_drift_tolerance has invalid fields"
            )
        drift = GeometryDriftTolerance(
            maximum_center_shift_m=_finite_number(
                raw_drift["maximum_center_shift_m"],
                f"{path}.geometry_drift_tolerance.maximum_center_shift_m",
                minimum=0.001,
            ),
            maximum_extent_change_fraction=_finite_number(
                raw_drift["maximum_extent_change_fraction"],
                (
                    f"{path}.geometry_drift_tolerance."
                    "maximum_extent_change_fraction"
                ),
                minimum=0.0,
            ),
        )
        target_drift_limits = [
            self._geometry_drift_limits[target]
            for target in targets
            if target in self._geometry_drift_limits
        ]
        if not target_drift_limits:
            raise WorldEffectComposedSequenceError(
                f"{path} targets lack an advertised geometry-drift ceiling"
            )
        maximum_advertised_drift = min(target_drift_limits)
        if drift.maximum_center_shift_m > maximum_advertised_drift:
            raise WorldEffectComposedSequenceError(
                f"{path}.geometry_drift_tolerance.maximum_center_shift_m "
                f"exceeds advertised maximum {maximum_advertised_drift:.6f}"
            )
        position_anchor_id = raw["position_anchor_id"]
        orientation_alignment_id = raw["orientation_alignment_id"]
        raw_offset = raw["interaction_offset_from_anchor_m"]
        if not isinstance(raw_offset, list) or len(raw_offset) not in {0, 3}:
            raise WorldEffectComposedSequenceError(
                f"{path}.interaction_offset_from_anchor_m must have 0 or 3 values"
            )
        offset = tuple(
            _finite_number(value, f"{path}.interaction_offset_from_anchor_m")
            for value in raw_offset
        )
        if position_anchor_id is not None:
            position_anchor_id = _identifier(
                position_anchor_id, f"{path}.position_anchor_id"
            )
            if position_anchor_id not in self._anchor_ids:
                raise WorldEffectComposedSequenceError(
                    f"{path}.position_anchor_id was not advertised"
                )
            envelope = self._anchor_envelopes.get(position_anchor_id)
            if envelope is not None and len(offset) == 3 and any(
                value < minimum or value > maximum
                for value, minimum, maximum in zip(
                    offset, envelope[0], envelope[1]
                )
            ):
                raise WorldEffectComposedSequenceError(
                    f"{path}.interaction_offset_from_anchor_m exceeds the "
                    "advertised RGB-D anchor envelope"
                )
        if orientation_alignment_id is not None:
            orientation_alignment_id = _identifier(
                orientation_alignment_id, f"{path}.orientation_alignment_id"
            )
            if orientation_alignment_id not in self._axis_ids:
                raise WorldEffectComposedSequenceError(
                    f"{path}.orientation_alignment_id was not advertised"
                )
        ordered_waypoints = raw_arguments.get("ordered_waypoints")
        if ordered_waypoints is not None:
            if not isinstance(ordered_waypoints, list) or not 2 <= len(
                ordered_waypoints
            ) <= 6:
                raise WorldEffectComposedSequenceError(
                    f"{path}.ordered_waypoints must contain 2 to 6 items"
                )
            if position_anchor_id is not None or raw_offset or (
                orientation_alignment_id is not None
            ):
                raise WorldEffectComposedSequenceError(
                    f"{path} ordered waypoints require null top-level grounding"
                )
            for waypoint_index, waypoint in enumerate(ordered_waypoints):
                if not isinstance(waypoint, Mapping):
                    raise WorldEffectComposedSequenceError(
                        f"{path}.ordered_waypoints[{waypoint_index}] must be an object"
                    )
                anchor_id = waypoint.get("position_anchor_id")
                axis_id = waypoint.get("orientation_alignment_id")
                if anchor_id not in self._anchor_ids or axis_id not in self._axis_ids:
                    raise WorldEffectComposedSequenceError(
                        f"{path}.ordered_waypoints[{waypoint_index}] uses "
                        "unadvertised grounding"
                    )
                if "target_quaternion_wxyz" in waypoint:
                    raise WorldEffectComposedSequenceError(
                        f"{path}.ordered_waypoints[{waypoint_index}] must omit "
                        "target_quaternion_wxyz; the motion tool materializes it"
                    )
                waypoint_offset = waypoint.get(
                    "interaction_offset_from_anchor_m"
                )
                if not isinstance(waypoint_offset, list) or len(
                    waypoint_offset
                ) != 3:
                    raise WorldEffectComposedSequenceError(
                        f"{path}.ordered_waypoints[{waypoint_index}] requires "
                        "a three-value interaction_offset_from_anchor_m"
                    )
                for component_index, value in enumerate(waypoint_offset):
                    _finite_number(
                        value,
                        f"{path}.ordered_waypoints[{waypoint_index}]."
                        f"interaction_offset_from_anchor_m[{component_index}]",
                    )
                envelope = self._anchor_envelopes.get(str(anchor_id))
                if envelope is not None and any(
                    float(value) < minimum or float(value) > maximum
                    for value, minimum, maximum in zip(
                        waypoint_offset, envelope[0], envelope[1]
                    )
                ):
                    raise WorldEffectComposedSequenceError(
                        f"{path}.ordered_waypoints[{waypoint_index}] exceeds "
                        "the advertised RGB-D anchor envelope"
                    )
        elif tool_family == "motion":
            if "target_quaternion_wxyz" in raw_arguments:
                raise WorldEffectComposedSequenceError(
                    f"{path}.invocation_arguments must omit "
                    "target_quaternion_wxyz; the motion tool materializes it"
                )
            if (
                position_anchor_id is None
                or len(offset) != 3
                or orientation_alignment_id is None
            ):
                raise WorldEffectComposedSequenceError(
                    f"{path} single-pose motion requires complete top-level "
                    "position and orientation grounding"
                )
        return ComposedToolCallDraft(
            call_id=call_id,
            requirement_id=requirement_id,
            tool_id=tool_id,
            tool_family=tool_family,
            semantic_effect_id=semantic_effect_id,
            purpose=purpose,
            target_entity_ids=targets,
            desired_outcome=_text(
                raw["desired_outcome"], f"{path}.desired_outcome"
            ),
            stop_condition=_text(
                raw["stop_condition"], f"{path}.stop_condition"
            ),
            tool_configuration=_json_copy(raw_config, f"{path}.tool_configuration"),
            geometry_drift_tolerance=drift,
            position_anchor_id=position_anchor_id,
            interaction_offset_from_anchor_m=offset,
            orientation_alignment_id=orientation_alignment_id,
            invocation_arguments=_json_copy(
                raw_arguments, f"{path}.invocation_arguments"
            ),
            expected_state_change=_text(
                raw["expected_state_change"], f"{path}.expected_state_change"
            ),
            reason=_text(raw["reason"], f"{path}.reason"),
        )

    def dispatch(self, payload: Mapping[str, Any]) -> ComposedToolSequenceDecision:
        if not isinstance(payload, Mapping):
            raise WorldEffectComposedSequenceError(
                "composed sequence proposal must be an object"
            )
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "goal_id",
            "tool_calls",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown or missing:
            raise WorldEffectComposedSequenceError(
                "composed sequence fields mismatch: "
                f"unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION:
            raise WorldEffectComposedSequenceError(
                "composed sequence schema_version mismatch"
            )
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.candidate_set.observation_id:
            raise WorldEffectComposedSequenceError(
                "stale composed sequence observation_id"
            )
        goal_id = _identifier(payload["goal_id"], "goal_id")
        if goal_id != self.candidate_set.goal_id:
            raise WorldEffectComposedSequenceError("composed sequence goal mismatch")
        decision = _identifier(payload["decision"], "decision")
        if decision not in WORLD_EFFECT_COMPOSED_SEQUENCE_DECISIONS:
            raise WorldEffectComposedSequenceError(
                f"unsupported composed sequence decision {decision!r}"
            )
        raw_calls = payload["tool_calls"]
        if not isinstance(raw_calls, list):
            raise WorldEffectComposedSequenceError("tool_calls must be an array")
        if decision == "propose_sequence":
            if not 1 <= len(raw_calls) <= self.candidate_set.maximum_tool_calls:
                raise WorldEffectComposedSequenceError(
                    "proposed sequence call count is outside the runtime budget"
                )
            calls = tuple(
                self._tool_call(raw, index)
                for index, raw in enumerate(raw_calls)
            )
            call_ids = [item.call_id for item in calls]
            if len(call_ids) != len(set(call_ids)):
                raise WorldEffectComposedSequenceError(
                    "composed sequence call_id values must be unique"
                )
            materialized_calls = list(calls)
            for index, call in enumerate(materialized_calls):
                if (
                    call.semantic_effect_id != "entity_attachment.acquire"
                    or index == 0
                    or materialized_calls[index - 1].tool_family != "motion"
                ):
                    continue
                motion = materialized_calls[index - 1]
                terminal_anchor_id = motion.position_anchor_id
                terminal_axis_id = motion.orientation_alignment_id
                waypoints = motion.invocation_arguments.get("ordered_waypoints")
                if isinstance(waypoints, list) and waypoints:
                    terminal = waypoints[-1]
                    if isinstance(terminal, Mapping):
                        terminal_anchor_id = terminal.get("position_anchor_id")
                        terminal_axis_id = terminal.get(
                            "orientation_alignment_id"
                        )
                anchor_entity_id = self._anchor_entities.get(
                    str(terminal_anchor_id)
                )
                axis_entity_id = self._axis_entities.get(str(terminal_axis_id))
                if (
                    anchor_entity_id is None
                    or axis_entity_id is None
                    or anchor_entity_id != axis_entity_id
                    or anchor_entity_id not in call.target_entity_ids
                ):
                    raise WorldEffectComposedSequenceError(
                        f"tool_calls[{index - 1}] terminal grasp position and "
                        "orientation must both be grounded to the entity "
                        f"acquired by tool_calls[{index}]"
                    )
            grasp_alignment = self.candidate_set.execution_context.get(
                "two_pad_grasp_alignment"
            )
            grasp_alignment = (
                grasp_alignment
                if isinstance(grasp_alignment, Mapping)
                else {}
            )
            corrective_contract = grasp_alignment.get(
                "corrective_motion_grounding_contract"
            )
            corrective_contract = (
                corrective_contract
                if isinstance(corrective_contract, Mapping)
                else None
            )
            corrective_entity_id = (
                corrective_contract.get("entity_id")
                if corrective_contract is not None
                else None
            )
            relation_required = bool(
                grasp_alignment.get("available") is True
                and grasp_alignment.get(
                    "object_center_inside_full_grasp_corridor"
                )
                is False
                and corrective_contract is not None
                and corrective_entity_id
                in self.candidate_set.related_entity_ids
            )
            if relation_required:
                required_anchor_id = corrective_contract.get(
                    "required_terminal_position_anchor_id"
                )
                required_offset = corrective_contract.get(
                    "required_terminal_interaction_offset_from_anchor_m"
                )
                for index, call in enumerate(materialized_calls):
                    if call.semantic_effect_id != "entity_attachment.acquire":
                        continue
                    if index == 0 or materialized_calls[index - 1].tool_family != "motion":
                        raise WorldEffectComposedSequenceError(
                            f"tool_calls[{index}] acquisition requires an "
                            "immediately preceding motion that establishes the "
                            "advertised interaction relation"
                        )
                    motion = materialized_calls[index - 1]
                    terminal_anchor_id = motion.position_anchor_id
                    terminal_offset: Sequence[float] = (
                        motion.interaction_offset_from_anchor_m
                    )
                    waypoints = motion.invocation_arguments.get(
                        "ordered_waypoints"
                    )
                    if isinstance(waypoints, list) and waypoints:
                        terminal = waypoints[-1]
                        if isinstance(terminal, Mapping):
                            terminal_anchor_id = terminal.get(
                                "position_anchor_id"
                            )
                            terminal_offset = terminal.get(
                                "interaction_offset_from_anchor_m", []
                            )
                    if terminal_anchor_id != required_anchor_id or list(
                        terminal_offset
                    ) != list(required_offset or []):
                        if (
                            required_anchor_id not in self._anchor_ids
                            or not isinstance(required_offset, Sequence)
                            or isinstance(required_offset, (str, bytes))
                            or len(required_offset) != 3
                        ):
                            raise WorldEffectComposedSequenceError(
                                "fresh corrective interaction relation is not "
                                "valid advertised grounding"
                            )
                        materialized_offset = tuple(
                            _finite_number(
                                value,
                                "corrective_motion_grounding_contract."
                                "required_terminal_interaction_offset_from_anchor_m",
                            )
                            for value in required_offset
                        )
                        envelope = self._anchor_envelopes.get(
                            str(required_anchor_id)
                        )
                        if envelope is not None and any(
                            value < minimum or value > maximum
                            for value, minimum, maximum in zip(
                                materialized_offset, envelope[0], envelope[1]
                            )
                        ):
                            raise WorldEffectComposedSequenceError(
                                "fresh corrective interaction relation exceeds "
                                "the advertised RGB-D anchor envelope"
                            )
                        if isinstance(waypoints, list) and waypoints:
                            materialized_waypoints = [
                                dict(waypoint) for waypoint in waypoints
                            ]
                            materialized_waypoints[-1][
                                "position_anchor_id"
                            ] = required_anchor_id
                            materialized_waypoints[-1][
                                "interaction_offset_from_anchor_m"
                            ] = list(materialized_offset)
                            materialized_arguments = dict(
                                motion.invocation_arguments
                            )
                            materialized_arguments["ordered_waypoints"] = (
                                materialized_waypoints
                            )
                            motion = replace(
                                motion,
                                invocation_arguments=materialized_arguments,
                            )
                        else:
                            motion = replace(
                                motion,
                                position_anchor_id=str(required_anchor_id),
                                interaction_offset_from_anchor_m=(
                                    materialized_offset
                                ),
                            )
                    relation_configuration = dict(
                        motion.tool_configuration
                    )
                    relation_configuration[
                        "require_interaction_relation"
                    ] = True
                    relation_tolerance = corrective_contract.get(
                        "maximum_terminal_position_error_m"
                    )
                    if (
                        isinstance(relation_tolerance, (int, float))
                        and not isinstance(relation_tolerance, bool)
                        and math.isfinite(float(relation_tolerance))
                        and 0.001 <= float(relation_tolerance) <= 0.05
                    ):
                        configured_tolerance = relation_configuration.get(
                            "position_tolerance_m"
                        )
                        relation_configuration["position_tolerance_m"] = min(
                            float(relation_tolerance),
                            float(configured_tolerance)
                            if isinstance(configured_tolerance, (int, float))
                            and not isinstance(configured_tolerance, bool)
                            and math.isfinite(float(configured_tolerance))
                            else float(relation_tolerance),
                        )
                    materialized_calls[index - 1] = replace(
                        motion,
                        tool_configuration=relation_configuration,
                    )
            attachment_active = bool(
                self.candidate_set.execution_context.get(
                    "retained_contact_supported", False
                )
            )
            for index, call in enumerate(materialized_calls):
                if call.tool_family == "motion" and attachment_active:
                    contact_configuration = dict(call.tool_configuration)
                    contact_configuration.pop("forbid_contact", None)
                    contact_configuration["require_contact"] = True
                    # Collision monitoring is a local authority constraint, not
                    # a model-authored task phase.  Zero requests detection of
                    # actual RGB-D AABB penetration without imposing a fixed
                    # object- or embodiment-specific clearance distance.
                    contact_configuration.setdefault(
                        "minimum_observed_clearance_m", 0.0
                    )
                    call = replace(
                        call,
                        tool_configuration=contact_configuration,
                    )
                    materialized_calls[index] = call
                elif call.tool_family == "motion":
                    contact_configuration = dict(call.tool_configuration)
                    contact_configuration.pop("require_contact", None)
                    if any(
                        future.semantic_effect_id
                        == "entity_attachment.acquire"
                        for future in materialized_calls[index + 1 :]
                    ):
                        # An approach which is intended to end with a later
                        # actuator acquisition must remain contact-free.  Touch
                        # before that semantic boundary is a fresh physical
                        # event, so revoke and let the model recompose instead
                        # of driving through the target.
                        contact_configuration["forbid_contact"] = True
                    call = replace(
                        call,
                        tool_configuration=contact_configuration,
                    )
                    materialized_calls[index] = call
                if call.semantic_effect_id == "entity_attachment.acquire":
                    attachment_active = True
                elif call.semantic_effect_id == "entity_attachment.release":
                    attachment_active = False
            calls = tuple(materialized_calls)
        else:
            if raw_calls:
                raise WorldEffectComposedSequenceError(
                    f"decision {decision!r} requires an empty tool_calls array"
                )
            calls = ()
        return ComposedToolSequenceDecision(
            observation_id=observation_id,
            decision=decision,
            goal_id=goal_id,
            tool_calls=calls,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def composed_tool_sequence_json_schema(
    candidate_set: ComposedToolSequenceCandidateSet,
) -> dict[str, Any]:
    requirement_ids = sorted(
        {item.requirement_id for item in candidate_set.operation_candidates.candidates}
    )
    tool_ids = sorted(
        {item.tool_id for item in candidate_set.operation_candidates.candidates}
    )
    tool_families = sorted(
        {item.tool_family for item in candidate_set.operation_candidates.candidates}
    )
    semantic_effect_ids = sorted(
        {
            item.semantic_effect_id
            for item in candidate_set.operation_candidates.candidates
            if item.semantic_effect_id is not None
        }
    )
    entities = list(candidate_set.related_entity_ids)
    call_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "call_id",
            "requirement_id",
            "tool_id",
            "tool_family",
            "semantic_effect_id",
            "purpose",
            "target_entity_ids",
            "desired_outcome",
            "stop_condition",
            "tool_configuration",
            "geometry_drift_tolerance",
            "position_anchor_id",
            "interaction_offset_from_anchor_m",
            "orientation_alignment_id",
            "invocation_arguments",
            "expected_state_change",
            "reason",
        ],
        "properties": {
            "call_id": {"type": "string"},
            "requirement_id": {"enum": requirement_ids},
            "tool_id": {"enum": tool_ids},
            "tool_family": {"enum": tool_families},
            "semantic_effect_id": {
                "type": ["string", "null"],
                "enum": [None, *semantic_effect_ids],
            },
            "purpose": {"enum": sorted(WORLD_EFFECT_OPERATION_PURPOSES)},
            "target_entity_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"enum": entities},
            },
            "desired_outcome": {"type": "string", "minLength": 1},
            "stop_condition": {"type": "string", "minLength": 1},
            "tool_configuration": {"type": "object"},
            "geometry_drift_tolerance": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "maximum_center_shift_m",
                    "maximum_extent_change_fraction",
                ],
                "properties": {
                    "maximum_center_shift_m": {
                        "type": "number",
                        "minimum": 0.001,
                    },
                    "maximum_extent_change_fraction": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 2.0,
                    },
                },
            },
            "position_anchor_id": {"type": ["string", "null"]},
            "interaction_offset_from_anchor_m": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 0,
                "maxItems": 3,
            },
            "orientation_alignment_id": {"type": ["string", "null"]},
            "invocation_arguments": {"type": "object"},
            "expected_state_change": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "observation_id",
            "decision",
            "goal_id",
            "tool_calls",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {
                "const": WORLD_EFFECT_COMPOSED_SEQUENCE_SCHEMA_VERSION
            },
            "observation_id": {"const": candidate_set.observation_id},
            "decision": {
                "enum": sorted(WORLD_EFFECT_COMPOSED_SEQUENCE_DECISIONS)
            },
            "goal_id": {"const": candidate_set.goal_id},
            "tool_calls": {
                "type": "array",
                "minItems": 0,
                "maxItems": candidate_set.maximum_tool_calls,
                "items": call_schema,
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_composed_tool_sequence_prompt(
    *,
    instruction: str,
    inventory: Mapping[str, Any],
    candidate_set: ComposedToolSequenceCandidateSet,
    recent_operation_history: Mapping[str, Any] | None = None,
    invalidated_suffix: Mapping[str, Any] | None = None,
) -> str:
    """Request the longest valid composition in one Gemini response."""
    instruction = _text(instruction, "instruction")
    history = (
        {"status": "none"}
        if recent_operation_history is None
        else _json_copy(recent_operation_history, "recent_operation_history")
    )
    invalidation = (
        {"status": "none"}
        if invalidated_suffix is None
        else _json_copy(invalidated_suffix, "invalidated_suffix")
    )
    return f"""Compose the longest currently supportable ordered sequence of
runtime tool calls for the selected world goal in ONE response. The runtime will
execute these calls in order without asking the model again while fresh sensor
evidence remains consistent. It will discard the unexecuted suffix and request a
new composition only when RGB-D, contact/tactile, clearance, membership,
provider, or motion feedback invalidates it; when an unexpected situation
appears; when this queue exhausts before the goal; or when the goal changes.

Human instruction:
{instruction}

Fresh semantic scene inventory:
{json.dumps(_json_copy(inventory, "inventory"), indent=2)}

Composition candidate and runtime advertisements:
{json.dumps(candidate_set.to_dict(), indent=2)}

Recent measured operation history:
{json.dumps(history, indent=2)}

Invalidated prior suffix, if any:
{json.dumps(invalidation, indent=2)}

Return propose_sequence with as many foreseeable calls as are needed and can be
grounded now, up to maximum_tool_calls. Do not return only the first call when a
longer composition is supportable. Calls are generic tool compositions, not a
hard-coded task routine or embodiment phase list. Select only an exact
advertised requirement_id/tool_id/semantic_effect_id combination. Use each
tool's advertised configuration and invocation schemas.

Treat satisfied_spatial_relations in recent_operation_history as measured
state, including when the containing operation stopped for a different sensor
condition. Preserve a satisfied orientation alignment on a replan unless fresh,
reliable orientation evidence explicitly invalidates it. An ambiguous or
axis-symmetric fresh footprint is not evidence that a previously satisfied
orientation became wrong. Continue from the remaining unsatisfied relation
instead of undoing an already-correct wrist alignment.

Motion must be expressed relative to advertised RGB-D position anchors and
orientation axes. For a single-pose tool, put its selected anchor, interaction
offset, and orientation axis in the top-level grounding fields; leave both
materialized target_position_m and target_quaternion_wxyz out. For an ordered
waypoint tool, set all top-level grounding fields to null/empty and put 2-6
waypoints in invocation_arguments. Each waypoint must contain
position_anchor_id, interaction_offset_from_anchor_m,
orientation_alignment_id, and must omit target_position_m and
target_quaternion_wxyz. The configurable motion tool derives each quaternion by
shortest-axis alignment while preserving wrist twist. Every offset must remain
inside its advertised RGB-D anchor envelope. Use enough waypoints to satisfy the advertised maximum
segment displacement and path length. Align the interaction axis, including the
wrist rotation, before acquisition. The motion immediately preceding an
attachment acquisition must ground both its terminal position anchor and its
terminal orientation axis to the entity being acquired; a destination,
receptacle, or unrelated entity axis is invalid for that grasp. For actuator effects, use null/empty
grounding and copy the advertised semantic command binding exactly.

tool_configuration is the configuration for that call. Loaded motion always
receives require_contact=true from the deterministic sequence gate, even if the
draft omits it; do not use false to request open-loop transport. Pre-attachment
motion that leads to an acquisition receives forbid_contact=true, so unexpected
touch revokes that motion and requires fresh reasoning. Before an
acquisition, include an immediately preceding motion for the interaction. When
a fresh corrective_motion_grounding_contract applies, the runtime motion tool
will bind that motion's terminal anchor and offset to the exact sensed relation;
the model still chooses the sequence, clearance path, and orientation. Earlier
clearance motions remain valid independent calls; they do not claim the grasp
relation. Geometry drift tolerances are model-
selected sensor invalidation thresholds. maximum_center_shift_m must not exceed
the smallest geometry_drift_limits maximum for that call's target entities;
the runtime will also add all mandatory and configuration-linked invalidation
conditions. expected_state_change describes the intended observation after the
call (for example acquired contact or target-relative motion); an expected
change advances the queue and is not itself a reason to replan. stop_condition
is the observable completion condition for that individual call.

Future calls are drafts only. Immediately before each call, the runtime rebuilds
fresh operation, lease, grounding, reachability, and invocation candidates and
passes the draft through every existing typed gate. No queued call has authority
until that just-in-time validation and its own single-use permit. If a future
draft no longer matches fresh evidence, the suffix is discarded instead of
being forced.

Return exactly one JSON object matching this schema, with no Markdown:
{json.dumps(composed_tool_sequence_json_schema(candidate_set), indent=2, sort_keys=True)}
"""


def matching_operation_candidate(
    step: ComposedToolCallDraft,
    candidate_set: WorldEffectOperationCandidateSet,
) -> WorldEffectOperationCandidate:
    """Resolve a draft against a fresh operation candidate set."""
    match = next(
        (
            item
            for item in candidate_set.candidates
            if _candidate_key(item)
            == (step.requirement_id, step.tool_id, step.semantic_effect_id)
        ),
        None,
    )
    if match is None:
        raise WorldEffectComposedSequenceError(
            "queued tool call is no longer advertised by fresh evidence"
        )
    return match
