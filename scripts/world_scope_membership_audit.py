"""Task-neutral audit of graph entity scope against a fresh observation.

Task membership answers which observed entities are covered by the human
instruction.  It is intentionally independent of reachability, mass, geometry,
destination capacity, provider availability, or any realization mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

try:
    from .world_goal_graph_contract import (
        ENTITY_SCOPE_STATUSES,
        WorldGoalGraph,
        semantic_scene_inventory_entity_ids,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_goal_graph_contract import (  # type: ignore[no-redef]
        ENTITY_SCOPE_STATUSES,
        WorldGoalGraph,
        semantic_scene_inventory_entity_ids,
    )


WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION = "world-scope-membership-audit.v1"
INSTRUCTION_SCOPE_TYPES = frozenset({"collective", "specific", "uncertain"})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldScopeMembershipAuditError(ValueError):
    """Raised when a scope audit is stale, incomplete, or malformed."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldScopeMembershipAuditError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldScopeMembershipAuditError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldScopeMembershipAuditError(
            "confidence must be a number in [0, 1]"
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldScopeMembershipAuditError(
            "confidence must be a number in [0, 1]"
        )
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldScopeMembershipAuditError(
            f"{path} exceeds maximum nesting depth"
        )
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldScopeMembershipAuditError(
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
                raise WorldScopeMembershipAuditError(
                    f"{path} keys must be non-empty"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldScopeMembershipAuditError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class WorldScopeMembershipDecision:
    entity_id: str
    status: str
    reason: str

    def __post_init__(self) -> None:
        _identifier(self.entity_id, "entity_id")
        if self.status not in ENTITY_SCOPE_STATUSES:
            raise WorldScopeMembershipAuditError(
                f"unsupported membership status {self.status!r}"
            )
        _text(self.reason, "reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorldScopeMembershipAudit:
    observation_id: str
    instruction_scope: str
    feasibility_independent: bool
    decisions: tuple[WorldScopeMembershipDecision, ...]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "instruction_scope": self.instruction_scope,
            "feasibility_independent": self.feasibility_independent,
            "decisions": [item.to_dict() for item in self.decisions],
            "confidence": self.confidence,
            "reason": self.reason,
            "motion_authority": False,
            "execution_authority": False,
        }


def world_scope_membership_observation_id(
    instruction: str,
    inventory: Mapping[str, Any],
    graph: WorldGoalGraph,
) -> str:
    instruction = _text(instruction, "instruction")
    seed = json.dumps(
        {
            "instruction": instruction,
            "inventory": _json_copy(inventory, "inventory"),
            "graph": graph.to_dict(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "scope-membership:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class WorldScopeMembershipAuditGate:
    """Parse a model audit and bind it to the exact inventory entity set."""

    def __init__(
        self,
        observation_id: str,
        inventory: Mapping[str, Any],
    ) -> None:
        self.observation_id = _identifier(observation_id, "observation_id")
        self.inventory_entity_ids = semantic_scene_inventory_entity_ids(inventory)

    def dispatch(self, payload: Mapping[str, Any]) -> WorldScopeMembershipAudit:
        if not isinstance(payload, Mapping):
            raise WorldScopeMembershipAuditError("scope audit must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "instruction_scope",
            "feasibility_independent",
            "decisions",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldScopeMembershipAuditError(
                f"scope audit contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldScopeMembershipAuditError(
                f"scope audit is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION:
            raise WorldScopeMembershipAuditError("scope audit schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.observation_id:
            raise WorldScopeMembershipAuditError("stale scope audit observation_id")
        instruction_scope = _text(
            payload["instruction_scope"], "instruction_scope"
        )
        if instruction_scope not in INSTRUCTION_SCOPE_TYPES:
            raise WorldScopeMembershipAuditError(
                f"unsupported instruction_scope {instruction_scope!r}"
            )
        if payload["feasibility_independent"] is not True:
            raise WorldScopeMembershipAuditError(
                "scope audit must keep task membership independent of feasibility"
            )
        raw_decisions = payload["decisions"]
        if not isinstance(raw_decisions, list):
            raise WorldScopeMembershipAuditError("decisions must be an array")
        decisions: list[WorldScopeMembershipDecision] = []
        for index, raw in enumerate(raw_decisions):
            if not isinstance(raw, Mapping):
                raise WorldScopeMembershipAuditError(
                    f"decisions[{index}] must be an object"
                )
            allowed_decision = {"entity_id", "status", "reason"}
            if set(raw) != allowed_decision:
                raise WorldScopeMembershipAuditError(
                    f"decisions[{index}] must contain exactly "
                    "entity_id, status, and reason"
                )
            decisions.append(
                WorldScopeMembershipDecision(
                    entity_id=_identifier(
                        raw["entity_id"], f"decisions[{index}].entity_id"
                    ),
                    status=_text(raw["status"], f"decisions[{index}].status"),
                    reason=_text(raw["reason"], f"decisions[{index}].reason"),
                )
            )
        audited_ids = [item.entity_id for item in decisions]
        if len(set(audited_ids)) != len(audited_ids):
            raise WorldScopeMembershipAuditError(
                "scope audit entity decisions must be unique"
            )
        missing_ids = sorted(self.inventory_entity_ids - set(audited_ids))
        extra_ids = sorted(set(audited_ids) - self.inventory_entity_ids)
        if missing_ids or extra_ids:
            raise WorldScopeMembershipAuditError(
                "scope audit must cover the exact inventory entity set; "
                f"missing={missing_ids}, extra={extra_ids}"
            )
        return WorldScopeMembershipAudit(
            observation_id=observation_id,
            instruction_scope=instruction_scope,
            feasibility_independent=True,
            decisions=tuple(sorted(decisions, key=lambda item: item.entity_id)),
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


@dataclass(frozen=True)
class WorldScopeMembershipAssessment:
    admitted: bool
    resolved_subset_admitted: bool
    scope_resolution_complete: bool
    graph_id: str
    observation_id: str
    instruction_scope: str
    mismatches: tuple[Mapping[str, Any], ...]
    unknown_entity_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "resolved_subset_admitted": self.resolved_subset_admitted,
            "scope_resolution_complete": self.scope_resolution_complete,
            "graph_id": self.graph_id,
            "observation_id": self.observation_id,
            "instruction_scope": self.instruction_scope,
            "mismatches": [
                _json_copy(item, "mismatch") for item in self.mismatches
            ],
            "unknown_entity_ids": list(self.unknown_entity_ids),
            "deferred_unknown_entity_ids": list(self.unknown_entity_ids),
            "task_completion_allowed": self.scope_resolution_complete,
            "authority": "fresh_model_grounded_task_membership",
            "motion_authority": False,
            "execution_authority": False,
        }


def assess_world_goal_graph_membership_audit(
    graph: WorldGoalGraph,
    audit: WorldScopeMembershipAudit,
) -> WorldScopeMembershipAssessment:
    """Compare graph scope to a feasibility-independent membership audit."""
    graph_scope = {item.entity_id: item.status for item in graph.entity_scope}
    mismatches = tuple(
        {
            "entity_id": decision.entity_id,
            "graph_status": graph_scope.get(decision.entity_id),
            "audited_status": decision.status,
            "audit_reason": decision.reason,
        }
        for decision in audit.decisions
        if graph_scope.get(decision.entity_id) != decision.status
    )
    unknown_ids = tuple(
        sorted(
            item.entity_id for item in audit.decisions if item.status == "unknown"
        )
    )
    required_observation_entity_ids = {
        entity_id
        for predicate in graph.required_observations
        for entity_id in (predicate.subject_id, predicate.reference_id)
        if entity_id is not None
    }
    executable_entity_ids = {
        entity_id
        for predicate in (
            *graph.constraints,
            *(
                predicate
                for goal in graph.goals
                for predicate in (*goal.desired_state, *goal.valid_while)
            ),
        )
        for entity_id in (predicate.subject_id, predicate.reference_id)
        if entity_id is not None
    }
    resolved_subset_admitted = bool(
        not mismatches
        and unknown_ids
        and audit.instruction_scope == "collective"
        and graph.status == "needs_observation"
        and set(unknown_ids).issubset(required_observation_entity_ids)
        and not set(unknown_ids).intersection(executable_entity_ids)
        and executable_entity_ids
    )
    return WorldScopeMembershipAssessment(
        admitted=not mismatches and not unknown_ids,
        resolved_subset_admitted=resolved_subset_admitted,
        scope_resolution_complete=not unknown_ids,
        graph_id=graph.graph_id,
        observation_id=audit.observation_id,
        instruction_scope=audit.instruction_scope,
        mismatches=mismatches,
        unknown_entity_ids=unknown_ids,
    )


def world_scope_membership_audit_json_schema(
    observation_id: str,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    entity_ids = sorted(semantic_scene_inventory_entity_ids(inventory))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "observation_id",
            "instruction_scope",
            "feasibility_independent",
            "decisions",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {
                "const": WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION
            },
            "observation_id": {"const": observation_id},
            "instruction_scope": {"enum": sorted(INSTRUCTION_SCOPE_TYPES)},
            "feasibility_independent": {"const": True},
            "decisions": {
                "type": "array",
                "minItems": len(entity_ids),
                "maxItems": len(entity_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["entity_id", "status", "reason"],
                    "properties": {
                        "entity_id": {"enum": entity_ids},
                        "status": {"enum": sorted(ENTITY_SCOPE_STATUSES)},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_world_scope_membership_audit_prompt(
    *,
    instruction: str,
    observation_id: str,
    inventory: Mapping[str, Any],
    graph: WorldGoalGraph,
) -> str:
    """Ask for semantic scope only, explicitly excluding feasibility reasoning."""
    instruction = _text(instruction, "instruction")
    return f"""Audit which observed entities are semantically covered by the human
instruction. This audit answers task membership only, not how to act.

Human instruction:
{instruction}

Fresh observation token:
{observation_id}

Fresh semantic scene inventory:
{json.dumps(_json_copy(inventory, "inventory"), indent=2)}

Proposed world goal graph:
{json.dumps(graph.to_dict(), indent=2)}

Provide exactly one decision for every inventory entity identifier. Use
included when the desired outcome applies to the entity, context for a surface,
destination, scene scope, or other relevant structure, excluded only when the
entity is confidently outside the instruction's semantic category or location,
and unknown when visual evidence cannot decide.

Determine whether the instruction is collective, specific, or uncertain. For a
collective category or location outcome, every observed entity covered by that
category or location remains included. Size, mass, shape, reachability,
destination capacity, provider availability, and expected difficulty are not
task-membership evidence. They may block realization later, but they must not
turn a covered entity into context or excluded. Set feasibility_independent to
true to acknowledge this separation.

Do not propose goals, mechanisms, body parts, controllers, trajectories, poses,
or commands. Return exactly one JSON object matching this schema, with no
Markdown:
{json.dumps(world_scope_membership_audit_json_schema(observation_id, inventory), indent=2, sort_keys=True)}
"""
