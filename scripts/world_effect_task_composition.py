"""One-call task reasoning before local world-effect gate materialization.

The model returns its world intent, causal goal graph, scope audit, selected
goal/capability/provider, and ordered generic tool drafts in one response.  The
response itself has no authority.  Runtime code injects all observation-bound
IDs and validates every nested decision through the existing strict gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
from typing import Any, Mapping, Sequence

try:
    from .world_goal_graph_contract import world_goal_graph_json_schema
    from .world_intent_contract import world_intent_json_schema
    from .world_effect_operation_plan import WORLD_EFFECT_OPERATION_PURPOSES
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_goal_graph_contract import world_goal_graph_json_schema  # type: ignore[no-redef]
    from world_intent_contract import world_intent_json_schema  # type: ignore[no-redef]
    from world_effect_operation_plan import (  # type: ignore[no-redef]
        WORLD_EFFECT_OPERATION_PURPOSES,
    )


WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION = "world-effect-task-composition.v1"
TASK_COMPOSITION_DECISIONS = frozenset(
    {"propose_task_plan", "observe_again", "blocked", "complete"}
)


class WorldEffectTaskCompositionError(ValueError):
    """Raised when a one-call task composition is malformed."""


def materialize_missing_task_composition_trace_reason(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Fill only a missing non-authoritative envelope reason from nested text."""
    if not isinstance(payload, Mapping):
        raise WorldEffectTaskCompositionError(
            "task composition must be an object"
        )
    normalized = _json_copy(payload, "task composition")
    if "reason" in normalized:
        return normalized, False
    sequence = normalized.get("tool_sequence")
    nested_reason = sequence.get("reason") if isinstance(sequence, Mapping) else None
    normalized["reason"] = _text(
        nested_reason,
        "tool_sequence.reason",
    )
    return normalized, True


def strip_model_materialized_motion_pose_fields(
    tool_calls: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove absolute poses that the anchored runtime must materialize.

    This is a privilege reduction, not a repair of semantic planning: all
    anchor, offset, orientation-axis, tool, effect, and safety choices remain
    model-authored and still pass through the composed-sequence gate.
    """
    calls = _json_copy(tool_calls, "tool_calls")
    removed: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict) or call.get("tool_family") != "motion":
            continue
        arguments = call.get("invocation_arguments")
        if not isinstance(arguments, dict):
            continue
        for field in ("target_position_m", "target_quaternion_wxyz"):
            if field in arguments:
                arguments.pop(field)
                removed.append({"tool_call_index": index, "field": field})
        waypoints = arguments.get("ordered_waypoints")
        if isinstance(waypoints, list):
            for waypoint_index, waypoint in enumerate(waypoints):
                if not isinstance(waypoint, dict):
                    continue
                for field in ("target_position_m", "target_quaternion_wxyz"):
                    if field in waypoint:
                        waypoint.pop(field)
                        removed.append(
                            {
                                "tool_call_index": index,
                                "waypoint_index": waypoint_index,
                                "field": field,
                            }
                        )
    return calls, removed


def resolve_model_grounding_aliases(
    tool_calls: Sequence[Mapping[str, Any]],
    grounding_catalog: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Restrict stale grounding names to advertised IDs on the same entity."""
    calls = _json_copy(tool_calls, "tool_calls")
    replacements: list[dict[str, Any]] = []
    anchors = [
        item
        for item in grounding_catalog.get("position_anchors", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("anchor_id"), str)
        and isinstance(item.get("entity_id"), str)
    ]
    axes = [
        item
        for item in grounding_catalog.get("orientation_axes", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("orientation_alignment_id"), str)
        and isinstance(item.get("entity_id"), str)
    ]

    def resolve(
        value: Any,
        records: Sequence[Mapping[str, Any]],
        id_field: str,
        target_entity_ids: Sequence[str],
    ) -> str | None:
        if value is None or not isinstance(value, str):
            return value
        advertised = {str(item[id_field]) for item in records}
        if value in advertised:
            return value
        matching_entity = next(
            (
                entity_id
                for entity_id in sorted(target_entity_ids, key=len, reverse=True)
                if value == entity_id or value.startswith(entity_id + ".")
            ),
            None,
        )
        if matching_entity is None and len(target_entity_ids) == 1:
            matching_entity = target_entity_ids[0]
        if matching_entity is None:
            return value
        candidates = sorted(
            str(item[id_field])
            for item in records
            if item.get("entity_id") == matching_entity
        )
        if not candidates:
            return value
        return max(
            candidates,
            key=lambda candidate: (
                SequenceMatcher(None, value, candidate).ratio(),
                candidate,
            ),
        )

    def replace_grounding(
        container: dict[str, Any],
        *,
        call_index: int,
        waypoint_index: int | None,
        target_entity_ids: Sequence[str],
    ) -> None:
        anchor_entities = {
            str(item["anchor_id"]): str(item["entity_id"])
            for item in anchors
        }
        for field, records, id_field in (
            ("position_anchor_id", anchors, "anchor_id"),
            ("orientation_alignment_id", axes, "orientation_alignment_id"),
        ):
            original = container.get(field)
            resolution_entity_ids = target_entity_ids
            if field == "orientation_alignment_id":
                anchor_entity = anchor_entities.get(
                    str(container.get("position_anchor_id"))
                )
                if anchor_entity in target_entity_ids:
                    resolution_entity_ids = [anchor_entity]
            resolved = resolve(
                original,
                records,
                id_field,
                resolution_entity_ids,
            )
            if resolved != original:
                container[field] = resolved
                replacement = {
                    "tool_call_index": call_index,
                    "field": field,
                    "from": original,
                    "to": resolved,
                    "same_entity_only": True,
                }
                if waypoint_index is not None:
                    replacement["waypoint_index"] = waypoint_index
                replacements.append(replacement)

    for call_index, call in enumerate(calls):
        if not isinstance(call, dict) or call.get("tool_family") != "motion":
            continue
        targets = call.get("target_entity_ids")
        targets = (
            [item for item in targets if isinstance(item, str)]
            if isinstance(targets, list)
            else []
        )
        replace_grounding(
            call,
            call_index=call_index,
            waypoint_index=None,
            target_entity_ids=targets,
        )
        arguments = call.get("invocation_arguments")
        waypoints = (
            arguments.get("ordered_waypoints")
            if isinstance(arguments, dict)
            else None
        )
        if isinstance(waypoints, list):
            for waypoint_index, waypoint in enumerate(waypoints):
                if isinstance(waypoint, dict):
                    replace_grounding(
                        waypoint,
                        call_index=call_index,
                        waypoint_index=waypoint_index,
                        target_entity_ids=targets,
                    )
    return calls, replacements


def tighten_model_geometry_drift_tolerances(
    tool_calls: Sequence[Mapping[str, Any]],
    grounding_catalog: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Clamp model drift thresholds to fresh per-target RGB-D ceilings."""
    calls = _json_copy(tool_calls, "tool_calls")
    limits = {
        str(item["entity_id"]): float(item["maximum_center_shift_m"])
        for item in grounding_catalog.get("geometry_drift_limits", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("entity_id"), str)
        and isinstance(item.get("maximum_center_shift_m"), (int, float))
        and not isinstance(item.get("maximum_center_shift_m"), bool)
        and math.isfinite(float(item["maximum_center_shift_m"]))
        and float(item["maximum_center_shift_m"]) >= 0.001
    }
    tightenings: list[dict[str, Any]] = []
    for call_index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        targets = call.get("target_entity_ids")
        target_limits = [
            limits[item]
            for item in targets
            if isinstance(targets, list)
            and isinstance(item, str)
            and item in limits
        ] if isinstance(targets, list) else []
        if not target_limits:
            continue
        tolerance = call.get("geometry_drift_tolerance")
        if not isinstance(tolerance, dict):
            continue
        requested = tolerance.get("maximum_center_shift_m")
        ceiling = min(target_limits)
        if (
            isinstance(requested, (int, float))
            and not isinstance(requested, bool)
            and math.isfinite(float(requested))
            and float(requested) > ceiling
        ):
            tolerance["maximum_center_shift_m"] = ceiling
            tightenings.append(
                {
                    "tool_call_index": call_index,
                    "requested_maximum_center_shift_m": float(requested),
                    "effective_maximum_center_shift_m": ceiling,
                    "tightened": True,
                }
            )
    return calls, tightenings


def bind_model_tool_calls_to_runtime_candidates(
    tool_calls: Sequence[Mapping[str, Any]],
    operation_candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind semantic tool drafts to exact provider requirement tuples."""
    calls = _json_copy(tool_calls, "tool_calls")
    candidates = [
        _json_copy(item, "operation_candidate")
        for item in operation_candidates
        if isinstance(item, Mapping)
    ]
    bindings: list[dict[str, Any]] = []
    tuple_fields = (
        "requirement_id",
        "tool_id",
        "tool_family",
        "semantic_effect_id",
    )
    allowed = {
        tuple(item.get(field) for field in tuple_fields) for item in candidates
    }
    for call_index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        selected = tuple(call.get(field) for field in tuple_fields)
        if selected in allowed:
            continue
        same_tool = [
            item for item in candidates if item.get("tool_id") == call.get("tool_id")
        ]
        effect_matches = [
            item
            for item in same_tool
            if item.get("semantic_effect_id") == call.get("semantic_effect_id")
        ]
        match = effect_matches[0] if len(effect_matches) == 1 else None
        if match is None and len(same_tool) == 1:
            match = same_tool[0]
        if match is None:
            same_family_effect = [
                item
                for item in candidates
                if item.get("tool_family") == call.get("tool_family")
                and item.get("semantic_effect_id")
                == call.get("semantic_effect_id")
            ]
            if len(same_family_effect) == 1:
                match = same_family_effect[0]
        if match is None:
            continue
        before = {field: call.get(field) for field in tuple_fields}
        for field in tuple_fields:
            call[field] = match.get(field)
        bindings.append(
            {
                "tool_call_index": call_index,
                "from": before,
                "to": {field: call.get(field) for field in tuple_fields},
                "binding_source": "active_runtime_operation_candidates",
            }
        )
    bound_calls: list[dict[str, Any]] = []
    for call_index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        selected = tuple(call.get(field) for field in tuple_fields)
        if selected in allowed:
            bound_calls.append(call)
            continue
        bindings.append(
            {
                "tool_call_index": call_index,
                "action": "dropped_unbound_future_draft",
                "from": {field: call.get(field) for field in tuple_fields},
                "binding_source": "active_runtime_operation_candidates",
                "execution_authority": False,
            }
        )
    return bound_calls, bindings


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectTaskCompositionError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any, path: str = "confidence") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectTaskCompositionError(f"{path} must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectTaskCompositionError(f"{path} must be a number in [0, 1]")
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 20:
        raise WorldEffectTaskCompositionError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectTaskCompositionError(f"{path} contains a non-finite number")
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
                raise WorldEffectTaskCompositionError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectTaskCompositionError(f"{path} must be JSON-compatible")


def _exact_object(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorldEffectTaskCompositionError(f"{path} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise WorldEffectTaskCompositionError(
            f"{path} fields mismatch: unknown={sorted(unknown)} missing={sorted(missing)}"
        )
    return _json_copy(value, path)


@dataclass(frozen=True)
class WorldEffectTaskCompositionDraft:
    decision: str
    world_intent: Mapping[str, Any]
    world_goal_graph: Mapping[str, Any]
    scope_membership: Mapping[str, Any]
    goal_activation: Mapping[str, Any]
    provider_selection: Mapping[str, Any]
    tool_sequence: Mapping[str, Any]
    confidence: float
    reason: str

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        maximum_tool_calls: int,
    ) -> "WorldEffectTaskCompositionDraft":
        if not isinstance(payload, Mapping):
            raise WorldEffectTaskCompositionError("task composition must be an object")
        fields = {
            "schema_version",
            "decision",
            "world_intent",
            "world_goal_graph",
            "scope_membership",
            "goal_activation",
            "provider_selection",
            "tool_sequence",
            "confidence",
            "reason",
        }
        unknown = set(payload) - fields
        missing = fields - set(payload)
        if unknown or missing:
            raise WorldEffectTaskCompositionError(
                "task composition fields mismatch: "
                f"unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION:
            raise WorldEffectTaskCompositionError("task composition schema_version mismatch")
        decision = _text(payload["decision"], "decision")
        if decision not in TASK_COMPOSITION_DECISIONS:
            raise WorldEffectTaskCompositionError(
                f"unsupported task composition decision {decision!r}"
            )
        scope = _exact_object(
            payload["scope_membership"],
            "scope_membership",
            {"instruction_scope", "feasibility_independent", "decisions", "confidence", "reason"},
        )
        activation = _exact_object(
            payload["goal_activation"],
            "goal_activation",
            {"decision", "goal_id", "capability_id", "confidence", "reason"},
        )
        provider = _exact_object(
            payload["provider_selection"],
            "provider_selection",
            {"decision", "provider_id", "confidence", "reason"},
        )
        sequence = _exact_object(
            payload["tool_sequence"],
            "tool_sequence",
            {"decision", "tool_calls", "confidence", "reason"},
        )
        raw_calls = sequence["tool_calls"]
        if not isinstance(raw_calls, list):
            raise WorldEffectTaskCompositionError("tool_sequence.tool_calls must be an array")
        if len(raw_calls) > maximum_tool_calls:
            raise WorldEffectTaskCompositionError(
                "tool_sequence.tool_calls exceeds the runtime operation budget"
            )
        if decision == "propose_task_plan" and not raw_calls:
            raise WorldEffectTaskCompositionError(
                "propose_task_plan requires at least one tool call"
            )
        return cls(
            decision=decision,
            world_intent=_json_copy(payload["world_intent"], "world_intent"),
            world_goal_graph=_json_copy(payload["world_goal_graph"], "world_goal_graph"),
            scope_membership=scope,
            goal_activation=activation,
            provider_selection=provider,
            tool_sequence=sequence,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION,
            "decision": self.decision,
            "world_intent": _json_copy(self.world_intent, "world_intent"),
            "world_goal_graph": _json_copy(self.world_goal_graph, "world_goal_graph"),
            "scope_membership": _json_copy(self.scope_membership, "scope_membership"),
            "goal_activation": _json_copy(self.goal_activation, "goal_activation"),
            "provider_selection": _json_copy(self.provider_selection, "provider_selection"),
            "tool_sequence": _json_copy(self.tool_sequence, "tool_sequence"),
            "confidence": self.confidence,
            "reason": self.reason,
            "execution_authority": False,
        }


def world_effect_task_composition_json_schema(maximum_tool_calls: int) -> dict[str, Any]:
    """Return the outer one-call schema; nested strict gates remain authoritative."""
    if isinstance(maximum_tool_calls, bool) or not isinstance(maximum_tool_calls, int):
        raise WorldEffectTaskCompositionError("maximum_tool_calls must be an integer")
    if maximum_tool_calls <= 0:
        raise WorldEffectTaskCompositionError("maximum_tool_calls must be positive")
    selection = lambda fields: {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": fields,
    }
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
            "call_id": {"type": "string", "minLength": 1},
            "requirement_id": {"type": "string", "minLength": 1},
            "tool_id": {"type": "string", "minLength": 1},
            "tool_family": {"type": "string", "minLength": 1},
            "semantic_effect_id": {"type": ["string", "null"]},
            "purpose": {"enum": sorted(WORLD_EFFECT_OPERATION_PURPOSES)},
            "target_entity_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
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
                "minItems": 0,
                "maxItems": 3,
                "items": {"type": "number"},
            },
            "orientation_alignment_id": {"type": ["string", "null"]},
            "invocation_arguments": {"type": "object"},
            "expected_state_change": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1},
        },
        "allOf": [
            {
                "if": {
                    "properties": {"tool_family": {"const": "motion"}},
                    "required": ["tool_family"],
                },
                "then": {
                    "oneOf": [
                        {
                            "properties": {
                                "position_anchor_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "interaction_offset_from_anchor_m": {
                                    "type": "array",
                                    "minItems": 3,
                                    "maxItems": 3,
                                    "items": {"type": "number"},
                                },
                                "orientation_alignment_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "invocation_arguments": {
                                    "type": "object",
                                    "not": {
                                        "anyOf": [
                                            {"required": ["target_position_m"]},
                                            {"required": ["target_quaternion_wxyz"]},
                                            {"required": ["ordered_waypoints"]},
                                        ]
                                    },
                                },
                            }
                        },
                        {
                            "properties": {
                                "position_anchor_id": {"const": None},
                                "interaction_offset_from_anchor_m": {
                                    "type": "array",
                                    "maxItems": 0,
                                },
                                "orientation_alignment_id": {"const": None},
                                "invocation_arguments": {
                                    "type": "object",
                                    "required": ["ordered_waypoints"],
                                    "not": {
                                        "anyOf": [
                                            {"required": ["target_position_m"]},
                                            {"required": ["target_quaternion_wxyz"]},
                                        ]
                                    },
                                    "properties": {
                                        "ordered_waypoints": {
                                            "type": "array",
                                            "minItems": 2,
                                            "maxItems": 6,
                                            "items": {
                                                "type": "object",
                                                "required": [
                                                    "position_anchor_id",
                                                    "interaction_offset_from_anchor_m",
                                                    "orientation_alignment_id",
                                                ],
                                                "properties": {
                                                    "position_anchor_id": {
                                                        "type": "string",
                                                        "minLength": 1,
                                                    },
                                                    "interaction_offset_from_anchor_m": {
                                                        "type": "array",
                                                        "minItems": 3,
                                                        "maxItems": 3,
                                                        "items": {"type": "number"},
                                                    },
                                                    "orientation_alignment_id": {
                                                        "type": "string",
                                                        "minLength": 1,
                                                    },
                                                },
                                            },
                                        }
                                    },
                                },
                            }
                        },
                    ]
                },
            }
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "decision", "world_intent", "world_goal_graph",
            "scope_membership", "goal_activation", "provider_selection",
            "tool_sequence", "confidence", "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_EFFECT_TASK_COMPOSITION_SCHEMA_VERSION},
            "decision": {"enum": sorted(TASK_COMPOSITION_DECISIONS)},
            "world_intent": world_intent_json_schema(),
            "world_goal_graph": world_goal_graph_json_schema(),
            "scope_membership": selection({
                "instruction_scope": {"enum": ["collective", "specific", "uncertain"]},
                "feasibility_independent": {"const": True},
                "decisions": {"type": "array"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reason": {"type": "string", "minLength": 1},
            }),
            "goal_activation": selection({
                "decision": {"enum": ["select_goal", "observe_again", "blocked", "complete"]},
                "goal_id": {"type": ["string", "null"]},
                "capability_id": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reason": {"type": "string", "minLength": 1},
            }),
            "provider_selection": selection({
                "decision": {"enum": ["select_provider", "observe_again", "blocked"]},
                "provider_id": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reason": {"type": "string", "minLength": 1},
            }),
            "tool_sequence": selection({
                "decision": {"enum": ["propose_sequence", "observe_again", "blocked"]},
                "tool_calls": {
                    "type": "array",
                    "maxItems": maximum_tool_calls,
                    "items": call_schema,
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reason": {"type": "string", "minLength": 1},
            }),
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_world_effect_task_composition_prompt(
    *,
    instruction: str,
    inventory: Mapping[str, Any],
    predicate_advertisement: Mapping[str, Any],
    capability_advertisement: Mapping[str, Any],
    provider_advertisement: Mapping[str, Any],
    runtime_effect_tools: Sequence[Mapping[str, Any]],
    grounding_catalog: Mapping[str, Any],
    execution_context: Mapping[str, Any],
    maximum_tool_calls: int,
) -> str:
    """Request all model-authored planning choices in one composition call."""
    instruction = _text(instruction, "instruction")
    context = {
        "semantic_scene_inventory": _json_copy(inventory, "inventory"),
        "predicate_advertisement": _json_copy(predicate_advertisement, "predicate_advertisement"),
        "capability_advertisement": _json_copy(capability_advertisement, "capability_advertisement"),
        "provider_advertisement": _json_copy(provider_advertisement, "provider_advertisement"),
        "runtime_effect_tools": _json_copy(runtime_effect_tools, "runtime_effect_tools"),
        "grounding_catalog": _json_copy(grounding_catalog, "grounding_catalog"),
        "execution_context": _json_copy(execution_context, "execution_context"),
        "maximum_tool_calls": maximum_tool_calls,
    }
    schema = world_effect_task_composition_json_schema(maximum_tool_calls)
    call_schema = schema["properties"]["tool_sequence"]["properties"][
        "tool_calls"
    ]["items"]
    call_properties = call_schema["properties"]
    entity_ids = sorted(
        item["entity_id"]
        for item in inventory.get("entities", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("entity_id"), str)
    )
    anchor_ids = sorted(
        item["anchor_id"]
        for item in grounding_catalog.get("position_anchors", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("anchor_id"), str)
    )
    axis_ids = sorted(
        item["orientation_alignment_id"]
        for item in grounding_catalog.get("orientation_axes", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("orientation_alignment_id"), str)
    )
    tool_ids = sorted(
        item["tool_id"]
        for item in runtime_effect_tools
        if isinstance(item, Mapping) and isinstance(item.get("tool_id"), str)
    )
    tool_families = sorted(
        {
            item["tool_family"]
            for item in runtime_effect_tools
            if isinstance(item, Mapping)
            and isinstance(item.get("tool_family"), str)
        }
    )
    provider_records = provider_advertisement.get("providers", [])
    requirement_ids = sorted(
        requirement["requirement_id"]
        for provider in provider_records
        if isinstance(provider, Mapping)
        for requirement in provider.get("requirements", [])
        if isinstance(requirement, Mapping)
        and isinstance(requirement.get("requirement_id"), str)
    )
    semantic_effect_ids = sorted(
        {
            tag
            for item in runtime_effect_tools
            if isinstance(item, Mapping)
            for tag in item.get("capability_tags", [])
            if isinstance(tag, str) and tag.startswith("entity_attachment.")
        }
    )
    if entity_ids:
        call_properties["target_entity_ids"]["items"] = {"enum": entity_ids}
    if requirement_ids:
        call_properties["requirement_id"] = {"enum": requirement_ids}
    if tool_ids:
        call_properties["tool_id"] = {"enum": tool_ids}
    if tool_families:
        call_properties["tool_family"] = {"enum": tool_families}
    call_properties["semantic_effect_id"] = {
        "enum": [None, *semantic_effect_ids]
    }
    if anchor_ids:
        call_properties["position_anchor_id"] = {
            "enum": [None, *anchor_ids]
        }
    if axis_ids:
        call_properties["orientation_alignment_id"] = {
            "enum": [None, *axis_ids]
        }
    return f"""Reason about the human task and compose the longest currently
supportable runtime tool sequence in ONE JSON response. This is a planning draft
only; it grants no motion or execution authority. The runtime will inject fresh
observation IDs, independently validate the intent, graph, exact scene scope,
goal/capability pair, provider, and every queued tool call, then issue one
single-use permit per call. It will ask again only if sensor or execution
evidence invalidates the unexecuted suffix, the queue exhausts, or the goal
changes.

Human instruction:
{instruction}

Fresh runtime planning context:
{json.dumps(context, indent=2)}

For propose_task_plan:
- world_intent and world_goal_graph must use the embedded strict schemas and
  only stable entity IDs from the inventory.
- scope_membership.decisions must cover every inventory entity exactly once;
  membership is independent of reachability or feasibility.
- select one goal/capability and one compatible provider from the advertisements.
- tool_sequence must contain as many foreseeable generic calls as can be
  grounded now, up to maximum_tool_calls. Use exact advertised tool IDs,
  requirement IDs, semantic effects, configuration fields, and command bindings.
- Use only grounding_catalog position anchors and orientation axes. Single-pose
  motion uses top-level grounding; waypoint motion uses 2-6 ordered_waypoints
  and null/empty top-level grounding. A motion call with null top-level
  grounding and no ordered_waypoints is invalid. Never echo target_position_m
  or target_quaternion_wxyz from the executor schema; the runtime materializes
  both from the selected RGB-D anchor, offset, and orientation axis.
- Include an alignment motion immediately before acquisition. The runtime may
  tighten geometry thresholds and bind the terminal grasp relation to fresh
  RGB-D/tactile evidence, but it never broadens the model's safety thresholds.
- Future calls are drafts. Contact loss, slip, pose/orientation drift, collision,
  clearance, visibility, membership, provider, or motion failure invalidates the
  remaining suffix before execution.

Return exactly one JSON object matching this schema, with no Markdown:
{json.dumps(schema, indent=2, sort_keys=True)}
"""
