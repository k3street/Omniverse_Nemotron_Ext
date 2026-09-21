"""Strict task- and embodiment-neutral world-intent contract.

This module deliberately describes desired world state only.  It does not
describe how any particular agent, mechanism, or controller should realize
that state.  Runtime integration is intentionally outside this first change.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping


WORLD_INTENT_SCHEMA_VERSION = "world-intent.v1"
WORLD_INTENT_OPERATIONS = frozenset(
    {"observe", "achieve", "verify", "complete", "unable"}
)
REOBSERVE_POLICIES = frozenset(
    {"state_change", "uncertainty", "time_budget", "always", "never"}
)


class WorldIntentValidationError(ValueError):
    """Raised when a model response violates the world-intent contract."""


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldIntentValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _json_value(value: Any, path: str, *, depth: int = 0) -> Any:
    """Validate and copy a finite JSON value without accepting Python objects."""
    if depth > 16:
        raise WorldIntentValidationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldIntentValidationError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, list):
        return [
            _json_value(item, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise WorldIntentValidationError(
                    f"{path} object keys must be non-empty strings"
                )
            copied[key] = _json_value(item, f"{path}.{key}", depth=depth + 1)
        return copied
    raise WorldIntentValidationError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class WorldPredicate:
    """One testable assertion about an entity in the observed world."""

    subject_id: str
    attribute: str
    operator: str
    value: Any
    reference_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], path: str) -> "WorldPredicate":
        if not isinstance(payload, Mapping):
            raise WorldIntentValidationError(f"{path} must be an object")
        allowed = {"subject_id", "attribute", "operator", "value", "reference_id"}
        unknown = set(payload) - allowed
        missing = {"subject_id", "attribute", "operator", "value"} - set(payload)
        if unknown:
            raise WorldIntentValidationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldIntentValidationError(
                f"{path} is missing required fields: {sorted(missing)}"
            )
        reference = payload.get("reference_id")
        if reference is not None:
            reference = _required_text(reference, f"{path}.reference_id")
        return cls(
            subject_id=_required_text(payload["subject_id"], f"{path}.subject_id"),
            attribute=_required_text(payload["attribute"], f"{path}.attribute"),
            operator=_required_text(payload["operator"], f"{path}.operator"),
            value=_json_value(payload["value"], f"{path}.value"),
            reference_id=reference,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "subject_id": self.subject_id,
            "attribute": self.attribute,
            "operator": self.operator,
            "value": _json_value(self.value, "predicate.value"),
        }
        if self.reference_id is not None:
            result["reference_id"] = self.reference_id
        return result


@dataclass(frozen=True)
class WorldIntent:
    """A desired world-state change or observation, independent of realization."""

    intent_id: str
    operation: str
    goals: tuple[WorldPredicate, ...]
    constraints: tuple[WorldPredicate, ...]
    reobserve_after: str
    confidence: float
    schema_version: str = WORLD_INTENT_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WorldIntent":
        if not isinstance(payload, Mapping):
            raise WorldIntentValidationError("world intent must be an object")
        allowed = {
            "schema_version",
            "intent_id",
            "operation",
            "goals",
            "constraints",
            "reobserve_after",
            "confidence",
        }
        unknown = set(payload) - allowed
        required = allowed - {"constraints"}
        missing = required - set(payload)
        if unknown:
            raise WorldIntentValidationError(
                f"world intent contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldIntentValidationError(
                f"world intent is missing required fields: {sorted(missing)}"
            )
        version = _required_text(payload["schema_version"], "schema_version")
        if version != WORLD_INTENT_SCHEMA_VERSION:
            raise WorldIntentValidationError(
                f"unsupported schema_version {version!r}; "
                f"expected {WORLD_INTENT_SCHEMA_VERSION!r}"
            )
        operation = _required_text(payload["operation"], "operation")
        if operation not in WORLD_INTENT_OPERATIONS:
            raise WorldIntentValidationError(f"unsupported operation {operation!r}")
        reobserve_after = _required_text(payload["reobserve_after"], "reobserve_after")
        if reobserve_after not in REOBSERVE_POLICIES:
            raise WorldIntentValidationError(
                f"unsupported reobserve_after policy {reobserve_after!r}"
            )
        raw_goals = payload["goals"]
        raw_constraints = payload.get("constraints", [])
        if not isinstance(raw_goals, list) or not isinstance(raw_constraints, list):
            raise WorldIntentValidationError("goals and constraints must be arrays")
        if len(raw_goals) > 32 or len(raw_constraints) > 32:
            raise WorldIntentValidationError(
                "goals and constraints may contain at most 32 predicates each"
            )
        goals = tuple(
            WorldPredicate.from_mapping(item, f"goals[{index}]")
            for index, item in enumerate(raw_goals)
        )
        constraints = tuple(
            WorldPredicate.from_mapping(item, f"constraints[{index}]")
            for index, item in enumerate(raw_constraints)
        )
        if operation != "unable" and not goals:
            raise WorldIntentValidationError(
                f"operation {operation!r} requires at least one world-state goal"
            )
        confidence = payload["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise WorldIntentValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise WorldIntentValidationError("confidence must be a number in [0, 1]")
        return cls(
            intent_id=_required_text(payload["intent_id"], "intent_id"),
            operation=operation,
            goals=goals,
            constraints=constraints,
            reobserve_after=reobserve_after,
            confidence=confidence,
            schema_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intent_id": self.intent_id,
            "operation": self.operation,
            "goals": [predicate.to_dict() for predicate in self.goals],
            "constraints": [predicate.to_dict() for predicate in self.constraints],
            "reobserve_after": self.reobserve_after,
            "confidence": self.confidence,
        }


def parse_world_intent_json(text: str) -> WorldIntent:
    """Parse one strict JSON response into a validated world intent."""
    if not isinstance(text, str) or not text.strip():
        raise WorldIntentValidationError("world intent response must be non-empty JSON")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise WorldIntentValidationError(f"invalid world intent JSON: {error.msg}") from error
    return WorldIntent.from_mapping(payload)


def world_intent_json_schema() -> dict[str, Any]:
    """Return the provider-neutral JSON Schema for structured model output."""
    predicate = {
        "type": "object",
        "additionalProperties": False,
        "required": ["subject_id", "attribute", "operator", "value"],
        "properties": {
            "subject_id": {"type": "string", "minLength": 1},
            "attribute": {"type": "string", "minLength": 1},
            "operator": {"type": "string", "minLength": 1},
            "value": {},
            "reference_id": {"type": "string", "minLength": 1},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WorldIntent",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "intent_id",
            "operation",
            "goals",
            "reobserve_after",
            "confidence",
        ],
        "properties": {
            "schema_version": {"const": WORLD_INTENT_SCHEMA_VERSION},
            "intent_id": {"type": "string", "minLength": 1},
            "operation": {"enum": sorted(WORLD_INTENT_OPERATIONS)},
            "goals": {"type": "array", "maxItems": 32, "items": predicate},
            "constraints": {"type": "array", "maxItems": 32, "items": predicate},
            "reobserve_after": {"enum": sorted(REOBSERVE_POLICIES)},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
    }


def build_world_intent_prompt(instruction: str) -> str:
    """Build a provider-neutral request for one declarative world intent."""
    instruction = _required_text(instruction, "instruction")
    schema = json.dumps(world_intent_json_schema(), indent=2, sort_keys=True)
    return f"""Translate the instruction and attached fresh scene observation into the
next desired world-state intent.

Instruction:
{instruction}

Express only observable state to achieve, observe, or verify. Refer to scene
entities with stable semantic identifiers derived from the observation. Do not
describe mechanisms, body parts, controllers, trajectories, or motor commands.
Represent requirements that must remain true while the goal is being realized
as world-state predicates in `constraints`.
Return exactly one JSON object matching this schema, with no Markdown:
{schema}
"""
