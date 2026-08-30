"""Shadow handoff from a selected world goal to a runtime effect provider.

The goal graph remains mechanism-free.  This layer binds one fresh activation
decision to runtime-discovered provider candidates, lets a reasoner choose a
compatible provider, and deliberately stops before provider activation or any
tool dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

try:
    from .world_effect_provider_registry import WorldEffectProviderAssessment
    from .world_goal_activation import (
        GoalActivationCandidateSet,
        WorldGoalActivationDecision,
    )
    from .world_goal_graph_contract import WorldGoalGraph
    from .world_goal_graph_membership import SceneMembershipLease
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_effect_provider_registry import (  # type: ignore[no-redef]
        WorldEffectProviderAssessment,
    )
    from world_goal_activation import (  # type: ignore[no-redef]
        GoalActivationCandidateSet,
        WorldGoalActivationDecision,
    )
    from world_goal_graph_contract import WorldGoalGraph  # type: ignore[no-redef]
    from world_goal_graph_membership import (  # type: ignore[no-redef]
        SceneMembershipLease,
    )


WORLD_EFFECT_SESSION_SCHEMA_VERSION = "world-effect-session.v1"
WORLD_EFFECT_SESSION_DECISIONS = frozenset(
    {"select_provider", "observe_again", "blocked"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectSessionError(ValueError):
    """Raised when a world-effect session proposal violates its evidence lease."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectSessionError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectSessionError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectSessionError("confidence must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectSessionError("confidence must be a number in [0, 1]")
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldEffectSessionError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectSessionError(f"{path} must contain finite numbers")
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
                raise WorldEffectSessionError(f"{path} keys must be non-empty")
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectSessionError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class WorldEffectSessionCandidate:
    candidate_id: str
    activation_observation_id: str
    graph_id: str
    membership_lease_id: str
    goal_id: str
    world_capability_id: str
    provider_id: str
    desired_state: tuple[Mapping[str, Any], ...]
    requirement_bindings: tuple[Mapping[str, Any], ...]
    inactive_requirement_ids: tuple[str, ...]
    tool_binding_active: bool

    def __post_init__(self) -> None:
        for path, value in (
            ("candidate_id", self.candidate_id),
            ("activation_observation_id", self.activation_observation_id),
            ("graph_id", self.graph_id),
            ("membership_lease_id", self.membership_lease_id),
            ("goal_id", self.goal_id),
            ("world_capability_id", self.world_capability_id),
            ("provider_id", self.provider_id),
        ):
            _identifier(value, path)
        for index, item in enumerate(self.desired_state):
            _json_copy(item, f"desired_state[{index}]")
        for index, item in enumerate(self.requirement_bindings):
            _json_copy(item, f"requirement_bindings[{index}]")
        for index, item in enumerate(self.inactive_requirement_ids):
            _identifier(item, f"inactive_requirement_ids[{index}]")
        if not isinstance(self.tool_binding_active, bool):
            raise WorldEffectSessionError("tool_binding_active must be boolean")
        if self.tool_binding_active and self.inactive_requirement_ids:
            raise WorldEffectSessionError(
                "active tool bindings cannot contain inactive requirements"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "activation_observation_id": self.activation_observation_id,
            "graph_id": self.graph_id,
            "membership_lease_id": self.membership_lease_id,
            "goal_id": self.goal_id,
            "world_capability_id": self.world_capability_id,
            "provider_id": self.provider_id,
            "desired_state": [
                _json_copy(item, "desired_state") for item in self.desired_state
            ],
            "requirement_bindings": [
                _json_copy(item, "requirement_binding")
                for item in self.requirement_bindings
            ],
            "inactive_requirement_ids": list(self.inactive_requirement_ids),
            "tool_binding_active": self.tool_binding_active,
            "provider_instantiated": False,
            "execution_ready": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class WorldEffectSessionCandidateSet:
    observation_id: str
    activation_observation_id: str
    graph_id: str
    membership_lease_id: str
    goal_id: str
    world_capability_id: str
    candidates: tuple[WorldEffectSessionCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_SESSION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "activation_observation_id": self.activation_observation_id,
            "graph_id": self.graph_id,
            "membership_lease_id": self.membership_lease_id,
            "goal_id": self.goal_id,
            "world_capability_id": self.world_capability_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "provider_instantiated": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def build_world_effect_session_candidates(
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    activation_candidate_set: GoalActivationCandidateSet,
    activation_decision: WorldGoalActivationDecision,
    provider_assessment: WorldEffectProviderAssessment,
) -> WorldEffectSessionCandidateSet:
    """Bind compatible providers to one exact fresh goal activation decision."""
    if activation_decision.decision != "select_goal":
        raise WorldEffectSessionError(
            "effect-session candidates require a select_goal activation decision"
        )
    if activation_decision.goal_id is None or activation_decision.capability_id is None:
        raise WorldEffectSessionError(
            "select_goal activation must include goal_id and capability_id"
        )
    if graph.graph_id != membership_lease.graph_id:
        raise WorldEffectSessionError("membership lease does not match graph")
    if activation_candidate_set.graph_id != graph.graph_id:
        raise WorldEffectSessionError("activation candidate set does not match graph")
    if activation_candidate_set.membership_lease_id != membership_lease.lease_id:
        raise WorldEffectSessionError(
            "activation candidate set does not match membership lease"
        )
    if provider_assessment.world_capability_id != activation_decision.capability_id:
        raise WorldEffectSessionError(
            "provider assessment does not match selected world capability"
        )
    selected_activation = next(
        (
            item
            for item in activation_candidate_set.candidates
            if item.goal_id == activation_decision.goal_id
            and activation_decision.capability_id in item.planning_capability_ids()
        ),
        None,
    )
    if selected_activation is None:
        raise WorldEffectSessionError(
            "activation decision is absent from the exact candidate set"
        )
    if not any(goal.goal_id == activation_decision.goal_id for goal in graph.goals):
        raise WorldEffectSessionError("selected goal is absent from graph")

    candidates: list[WorldEffectSessionCandidate] = []
    for binding in sorted(provider_assessment.bindings, key=lambda item: item.provider_id):
        if not binding.compatible:
            continue
        inactive_requirements = tuple(
            sorted(
                str(item["requirement_id"])
                for item in binding.requirement_bindings
                if item.get("activation_status") != "active"
            )
        )
        seed = json.dumps(
            {
                "activation_observation_id": activation_decision.observation_id,
                "graph_id": graph.graph_id,
                "membership_lease_id": membership_lease.lease_id,
                "goal_id": activation_decision.goal_id,
                "world_capability_id": activation_decision.capability_id,
                "provider_id": binding.provider_id,
                "requirement_bindings": binding.to_dict()["requirement_bindings"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate_id = "effect-session:" + hashlib.sha256(
            seed.encode("utf-8")
        ).hexdigest()[:16]
        candidates.append(
            WorldEffectSessionCandidate(
                candidate_id=candidate_id,
                activation_observation_id=activation_decision.observation_id,
                graph_id=graph.graph_id,
                membership_lease_id=membership_lease.lease_id,
                goal_id=activation_decision.goal_id,
                world_capability_id=activation_decision.capability_id,
                provider_id=binding.provider_id,
                desired_state=selected_activation.desired_state,
                requirement_bindings=binding.requirement_bindings,
                inactive_requirement_ids=inactive_requirements,
                tool_binding_active=binding.active,
            )
        )
    observation_seed = json.dumps(
        {
            "activation_decision": activation_decision.to_dict(),
            "provider_candidates": [item.to_dict() for item in candidates],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    observation_id = "effect-session-observation:" + hashlib.sha256(
        observation_seed.encode("utf-8")
    ).hexdigest()[:16]
    return WorldEffectSessionCandidateSet(
        observation_id=observation_id,
        activation_observation_id=activation_decision.observation_id,
        graph_id=graph.graph_id,
        membership_lease_id=membership_lease.lease_id,
        goal_id=activation_decision.goal_id,
        world_capability_id=activation_decision.capability_id,
        candidates=tuple(candidates),
    )


@dataclass(frozen=True)
class WorldEffectSessionDecision:
    observation_id: str
    decision: str
    candidate_id: str | None
    provider_id: str | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_SESSION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "candidate_id": self.candidate_id,
            "provider_id": self.provider_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "provider_instantiated": False,
            "motion_authority": False,
            "execution_authority": False,
        }


class WorldEffectSessionGate:
    """Validate a provider choice against one exact shadow candidate set."""

    def __init__(self, candidate_set: WorldEffectSessionCandidateSet) -> None:
        self.candidate_set = candidate_set
        self._pairs = {
            (item.candidate_id, item.provider_id) for item in candidate_set.candidates
        }

    def dispatch(self, payload: Mapping[str, Any]) -> WorldEffectSessionDecision:
        if not isinstance(payload, Mapping):
            raise WorldEffectSessionError("effect-session response must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "candidate_id",
            "provider_id",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldEffectSessionError(
                f"effect-session response contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectSessionError(
                f"effect-session response is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_SESSION_SCHEMA_VERSION:
            raise WorldEffectSessionError("effect-session schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.candidate_set.observation_id:
            raise WorldEffectSessionError("stale effect-session observation_id")
        decision = _text(payload["decision"], "decision")
        if decision not in WORLD_EFFECT_SESSION_DECISIONS:
            raise WorldEffectSessionError(
                f"unsupported effect-session decision {decision!r}"
            )
        candidate_id = payload["candidate_id"]
        provider_id = payload["provider_id"]
        if decision == "select_provider":
            candidate_id = _identifier(candidate_id, "candidate_id")
            provider_id = _identifier(provider_id, "provider_id")
            if (candidate_id, provider_id) not in self._pairs:
                raise WorldEffectSessionError(
                    "selected effect-session provider pair was not advertised"
                )
        elif candidate_id is not None or provider_id is not None:
            raise WorldEffectSessionError(
                f"decision {decision!r} requires null candidate_id and provider_id"
            )
        return WorldEffectSessionDecision(
            observation_id=observation_id,
            decision=decision,
            candidate_id=candidate_id,
            provider_id=provider_id,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def world_effect_session_json_schema(
    candidate_set: WorldEffectSessionCandidateSet,
) -> dict[str, Any]:
    candidate_ids = sorted(item.candidate_id for item in candidate_set.candidates)
    provider_ids = sorted({item.provider_id for item in candidate_set.candidates})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "observation_id",
            "decision",
            "candidate_id",
            "provider_id",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_EFFECT_SESSION_SCHEMA_VERSION},
            "observation_id": {"const": candidate_set.observation_id},
            "decision": {"enum": sorted(WORLD_EFFECT_SESSION_DECISIONS)},
            "candidate_id": {
                "type": ["string", "null"],
                "enum": [None, *candidate_ids],
            },
            "provider_id": {
                "type": ["string", "null"],
                "enum": [None, *provider_ids],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_world_effect_session_prompt(
    *,
    instruction: str,
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    activation_decision: WorldGoalActivationDecision,
    candidate_set: WorldEffectSessionCandidateSet,
) -> str:
    """Ask a reasoner to choose a discovered provider without activating it."""
    instruction = _text(instruction, "instruction")
    return f"""Select a runtime provider for one already-selected world-state effect.

Human instruction:
{instruction}

Validated world goal graph:
{json.dumps(graph.to_dict(), indent=2)}

Fresh scene membership lease:
{json.dumps(membership_lease.to_dict(), indent=2)}

Validated goal activation decision:
{json.dumps(activation_decision.to_dict(), indent=2)}

Compatible runtime effect-session candidates:
{json.dumps(candidate_set.to_dict(), indent=2)}

Choose select_provider only for an exact advertised candidate_id/provider_id
pair. Compare semantic requirement bindings; do not invent a provider or tool.
If inactive_requirement_ids is non-empty, explicitly acknowledge that those
tool factories are not active. This is a shadow proposal only: it does not
instantiate a provider, call a tool, or grant motion or execution authority.
Use blocked if no candidate can realize the selected world capability, or
observe_again if the provider choice requires fresher evidence.

Describe only provider selection and evidence. Do not output body parts,
controllers, trajectories, poses, or motor commands. Return exactly one JSON
object matching this schema, with no Markdown:
{json.dumps(world_effect_session_json_schema(candidate_set), indent=2, sort_keys=True)}
"""
