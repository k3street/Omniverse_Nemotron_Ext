"""Planning-only provider instantiation and first semantic operation proposal.

Selected tool factories may publish declarative executor specifications, but
this module never receives or stores execution handlers.  A model may propose
which provider requirement should act next and what evidence-defined outcome it
should pursue; no tool call, pose, actuator command, or simulator action is
authorized here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

try:
    from .world_effect_provider_registry import RuntimeToolCapability
    from .world_effect_session import (
        WorldEffectSessionCandidateSet,
        WorldEffectSessionDecision,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_effect_provider_registry import (  # type: ignore[no-redef]
        RuntimeToolCapability,
    )
    from world_effect_session import (  # type: ignore[no-redef]
        WorldEffectSessionCandidateSet,
        WorldEffectSessionDecision,
    )


WORLD_EFFECT_OPERATION_SCHEMA_VERSION = "world-effect-operation.v1"
WORLD_EFFECT_OPERATION_DECISIONS = frozenset(
    {"propose_operation", "observe_again", "blocked"}
)
WORLD_EFFECT_OPERATION_PURPOSES = frozenset(
    {"observe", "establish_precondition", "realize_effect", "verify_effect"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_CAPABILITY_TAG = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class WorldEffectOperationPlanError(ValueError):
    """Raised when planning-only activation or a proposal violates its lease."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectOperationPlanError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectOperationPlanError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectOperationPlanError(
            "confidence must be a number in [0, 1]"
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldEffectOperationPlanError(
            "confidence must be a number in [0, 1]"
        )
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldEffectOperationPlanError(
            f"{path} exceeds maximum nesting depth"
        )
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectOperationPlanError(
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
                raise WorldEffectOperationPlanError(
                    f"{path} keys must be non-empty"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectOperationPlanError(f"{path} must be JSON-compatible")


PlanningFactoryActivator = Callable[[], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class PlanningToolFactory:
    factory_tool_id: str
    tool_family: str
    capability_tags: tuple[str, ...]
    activator: PlanningFactoryActivator

    def __post_init__(self) -> None:
        _identifier(self.factory_tool_id, "factory_tool_id")
        _identifier(self.tool_family, "tool_family")
        if not self.capability_tags:
            raise WorldEffectOperationPlanError(
                "factory capability_tags must not be empty"
            )
        if len(set(self.capability_tags)) != len(self.capability_tags):
            raise WorldEffectOperationPlanError(
                "factory capability_tags must not contain duplicates"
            )
        for index, tag in enumerate(self.capability_tags):
            if not isinstance(tag, str) or not _CAPABILITY_TAG.fullmatch(tag):
                raise WorldEffectOperationPlanError(
                    f"capability_tags[{index}] has an invalid format"
                )
        if not callable(self.activator):
            raise WorldEffectOperationPlanError("factory activator must be callable")


class PlanningToolFactoryCatalog:
    """Runtime catalog whose activators may return specs but never handlers."""

    def __init__(self) -> None:
        self._factories: dict[str, PlanningToolFactory] = {}

    def register(self, factory: PlanningToolFactory) -> None:
        if not isinstance(factory, PlanningToolFactory):
            raise WorldEffectOperationPlanError(
                "factory registration requires PlanningToolFactory"
            )
        if factory.factory_tool_id in self._factories:
            raise WorldEffectOperationPlanError(
                f"factory {factory.factory_tool_id!r} is already registered"
            )
        self._factories[factory.factory_tool_id] = factory

    def resolve(self, factory_tool_id: str) -> PlanningToolFactory | None:
        return self._factories.get(factory_tool_id)


@dataclass(frozen=True)
class PlanningToolActivation:
    requirement_id: str
    source_tool_id: str
    activated_tool_id: str
    tool_family: str
    capability_tags: tuple[str, ...]
    tool_advertisement: Mapping[str, Any]
    factory_instantiated: bool

    def __post_init__(self) -> None:
        for path, value in (
            ("requirement_id", self.requirement_id),
            ("source_tool_id", self.source_tool_id),
            ("activated_tool_id", self.activated_tool_id),
            ("tool_family", self.tool_family),
        ):
            _identifier(value, path)
        if not self.capability_tags:
            raise WorldEffectOperationPlanError(
                "activated tool capability_tags must not be empty"
            )
        if len(set(self.capability_tags)) != len(self.capability_tags):
            raise WorldEffectOperationPlanError(
                "activated tool capability_tags must not contain duplicates"
            )
        for index, tag in enumerate(self.capability_tags):
            if not isinstance(tag, str) or not _CAPABILITY_TAG.fullmatch(tag):
                raise WorldEffectOperationPlanError(
                    f"capability_tags[{index}] has an invalid format"
                )
        if not isinstance(self.tool_advertisement, Mapping):
            raise WorldEffectOperationPlanError(
                "tool_advertisement must be an object"
            )
        _json_copy(self.tool_advertisement, "tool_advertisement")
        if not isinstance(self.factory_instantiated, bool):
            raise WorldEffectOperationPlanError(
                "factory_instantiated must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "source_tool_id": self.source_tool_id,
            "activated_tool_id": self.activated_tool_id,
            "tool_family": self.tool_family,
            "capability_tags": list(self.capability_tags),
            "tool_advertisement": _json_copy(
                self.tool_advertisement, "tool_advertisement"
            ),
            "factory_instantiated": self.factory_instantiated,
            "activation_mode": "planning_only",
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class PlanningWorldEffectProviderInstance:
    instance_id: str
    session_observation_id: str
    session_candidate_id: str
    provider_id: str
    graph_id: str
    membership_lease_id: str
    goal_id: str
    world_capability_id: str
    desired_state: tuple[Mapping[str, Any], ...]
    tool_activations: tuple[PlanningToolActivation, ...]
    activation_blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        for path, value in (
            ("instance_id", self.instance_id),
            ("session_observation_id", self.session_observation_id),
            ("session_candidate_id", self.session_candidate_id),
            ("provider_id", self.provider_id),
            ("graph_id", self.graph_id),
            ("membership_lease_id", self.membership_lease_id),
            ("goal_id", self.goal_id),
            ("world_capability_id", self.world_capability_id),
        ):
            _identifier(value, path)
        for index, item in enumerate(self.desired_state):
            if not isinstance(item, Mapping):
                raise WorldEffectOperationPlanError(
                    f"desired_state[{index}] must be an object"
                )
            _json_copy(item, f"desired_state[{index}]")
        for index, activation in enumerate(self.tool_activations):
            if not isinstance(activation, PlanningToolActivation):
                raise WorldEffectOperationPlanError(
                    f"tool_activations[{index}] must be PlanningToolActivation"
                )
        for index, blocker in enumerate(self.activation_blockers):
            _identifier(blocker, f"activation_blockers[{index}]")

    @property
    def planning_ready(self) -> bool:
        return bool(self.tool_activations and not self.activation_blockers)

    def related_entity_ids(self) -> tuple[str, ...]:
        entity_ids: set[str] = set()
        for predicate in self.desired_state:
            subject_id = predicate.get("subject_id")
            reference_id = predicate.get("reference_id")
            if isinstance(subject_id, str):
                entity_ids.add(subject_id)
            if isinstance(reference_id, str):
                entity_ids.add(reference_id)
        return tuple(sorted(entity_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "session_observation_id": self.session_observation_id,
            "session_candidate_id": self.session_candidate_id,
            "provider_id": self.provider_id,
            "graph_id": self.graph_id,
            "membership_lease_id": self.membership_lease_id,
            "goal_id": self.goal_id,
            "world_capability_id": self.world_capability_id,
            "desired_state": [
                _json_copy(item, "desired_state") for item in self.desired_state
            ],
            "related_entity_ids": list(self.related_entity_ids()),
            "tool_activations": [
                item.to_dict() for item in self.tool_activations
            ],
            "activation_blockers": list(self.activation_blockers),
            "planning_ready": self.planning_ready,
            "planning_provider_instantiated": True,
            "execution_provider_created": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def _activation_from_advertisement(
    *,
    requirement_id: str,
    source_tool_id: str,
    advertisement: Mapping[str, Any],
    factory_instantiated: bool,
    required_tags: set[str],
) -> PlanningToolActivation:
    if not isinstance(advertisement, Mapping):
        raise WorldEffectOperationPlanError(
            "factory activator must return tool advertisement objects"
        )
    activated_tool_id = advertisement.get("executor_id", advertisement.get("tool_id"))
    tool_family = advertisement.get("tool_family")
    raw_tags = advertisement.get("capability_tags")
    activated_tool_id = _identifier(activated_tool_id, "activated_tool_id")
    tool_family = _identifier(tool_family, "tool_family")
    if not isinstance(raw_tags, (list, tuple)) or isinstance(raw_tags, (str, bytes)):
        raise WorldEffectOperationPlanError(
            "activated tool capability_tags must be an array"
        )
    capability_tags = tuple(raw_tags)
    if not required_tags.issubset(capability_tags):
        raise WorldEffectOperationPlanError(
            f"activated tool {activated_tool_id!r} lacks required capability tags"
        )
    return PlanningToolActivation(
        requirement_id=requirement_id,
        source_tool_id=source_tool_id,
        activated_tool_id=activated_tool_id,
        tool_family=tool_family,
        capability_tags=capability_tags,
        tool_advertisement=_json_copy(advertisement, "tool_advertisement"),
        factory_instantiated=factory_instantiated,
    )


def build_planning_world_effect_provider_instance(
    candidate_set: WorldEffectSessionCandidateSet,
    decision: WorldEffectSessionDecision,
    runtime_tools: Sequence[RuntimeToolCapability],
    factory_catalog: PlanningToolFactoryCatalog,
) -> PlanningWorldEffectProviderInstance:
    """Instantiate selected factory specs without binding any execution handler."""
    if decision.decision != "select_provider":
        raise WorldEffectOperationPlanError(
            "planning provider requires a select_provider session decision"
        )
    selected = next(
        (
            item
            for item in candidate_set.candidates
            if item.candidate_id == decision.candidate_id
            and item.provider_id == decision.provider_id
        ),
        None,
    )
    if selected is None:
        raise WorldEffectOperationPlanError(
            "session decision is absent from the exact candidate set"
        )
    runtime_by_id = {item.tool_id: item for item in runtime_tools}
    if len(runtime_by_id) != len(runtime_tools):
        raise WorldEffectOperationPlanError("runtime tool ids must be unique")
    activations: list[PlanningToolActivation] = []
    blockers: list[str] = []
    for binding in selected.requirement_bindings:
        requirement_id = _identifier(binding.get("requirement_id"), "requirement_id")
        source_tool_id = _identifier(binding.get("tool_id"), "source_tool_id")
        raw_required_tags = binding.get("required_capability_tags")
        if not isinstance(raw_required_tags, list):
            raise WorldEffectOperationPlanError(
                "requirement required_capability_tags must be an array"
            )
        required_tags = {str(item) for item in raw_required_tags}
        activation_status = binding.get("activation_status")
        if activation_status == "active":
            runtime_tool = runtime_by_id.get(source_tool_id)
            if runtime_tool is None:
                blockers.append(f"{requirement_id}.active_tool_missing")
                continue
            activations.append(
                _activation_from_advertisement(
                    requirement_id=requirement_id,
                    source_tool_id=source_tool_id,
                    advertisement=runtime_tool.to_dict(),
                    factory_instantiated=False,
                    required_tags=required_tags,
                )
            )
            continue
        if activation_status != "factory_available":
            blockers.append(f"{requirement_id}.unsupported_activation_status")
            continue
        factory = factory_catalog.resolve(source_tool_id)
        if factory is None:
            blockers.append(f"{requirement_id}.factory_not_registered")
            continue
        if not required_tags.issubset(factory.capability_tags):
            blockers.append(f"{requirement_id}.factory_capability_mismatch")
            continue
        advertisements = factory.activator()
        if isinstance(advertisements, (str, bytes)) or not isinstance(
            advertisements, Sequence
        ):
            raise WorldEffectOperationPlanError(
                "factory activator must return an array of advertisements"
            )
        matched = 0
        for advertisement in advertisements:
            try:
                activation = _activation_from_advertisement(
                    requirement_id=requirement_id,
                    source_tool_id=source_tool_id,
                    advertisement=advertisement,
                    factory_instantiated=True,
                    required_tags=required_tags,
                )
            except WorldEffectOperationPlanError:
                continue
            activations.append(activation)
            matched += 1
        if not matched:
            blockers.append(f"{requirement_id}.factory_produced_no_matching_tool")
    activated_requirements = {item.requirement_id for item in activations}
    expected_requirements = {
        str(item["requirement_id"]) for item in selected.requirement_bindings
    }
    blockers.extend(
        f"{requirement_id}.not_activated"
        for requirement_id in sorted(expected_requirements - activated_requirements)
        if not any(item.startswith(f"{requirement_id}.") for item in blockers)
    )
    instance_seed = json.dumps(
        {
            "session_decision": decision.to_dict(),
            "candidate": selected.to_dict(),
            "activations": [item.to_dict() for item in activations],
            "blockers": sorted(blockers),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    instance_id = "planning-provider:" + hashlib.sha256(
        instance_seed.encode("utf-8")
    ).hexdigest()[:16]
    return PlanningWorldEffectProviderInstance(
        instance_id=instance_id,
        session_observation_id=decision.observation_id,
        session_candidate_id=selected.candidate_id,
        provider_id=selected.provider_id,
        graph_id=selected.graph_id,
        membership_lease_id=selected.membership_lease_id,
        goal_id=selected.goal_id,
        world_capability_id=selected.world_capability_id,
        desired_state=selected.desired_state,
        tool_activations=tuple(
            sorted(
                activations,
                key=lambda item: (
                    item.requirement_id,
                    item.activated_tool_id,
                ),
            )
        ),
        activation_blockers=tuple(sorted(set(blockers))),
    )


@dataclass(frozen=True)
class WorldEffectOperationCandidate:
    operation_candidate_id: str
    provider_instance_id: str
    requirement_id: str
    tool_id: str
    tool_family: str
    capability_tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_candidate_id": self.operation_candidate_id,
            "provider_instance_id": self.provider_instance_id,
            "requirement_id": self.requirement_id,
            "tool_id": self.tool_id,
            "tool_family": self.tool_family,
            "capability_tags": list(self.capability_tags),
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class WorldEffectOperationCandidateSet:
    observation_id: str
    provider_instance_id: str
    related_entity_ids: tuple[str, ...]
    candidates: tuple[WorldEffectOperationCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_OPERATION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "provider_instance_id": self.provider_instance_id,
            "related_entity_ids": list(self.related_entity_ids),
            "candidates": [item.to_dict() for item in self.candidates],
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def build_world_effect_operation_candidates(
    instance: PlanningWorldEffectProviderInstance,
    inventory: Mapping[str, Any],
) -> WorldEffectOperationCandidateSet:
    """Expose one semantic candidate per planning-only activated tool spec."""
    raw_entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(raw_entities, list):
        raise WorldEffectOperationPlanError("inventory entities must be an array")
    inventory_ids: set[str] = set()
    for index, item in enumerate(raw_entities):
        if not isinstance(item, Mapping):
            raise WorldEffectOperationPlanError(
                f"inventory entities[{index}] must be an object"
            )
        entity_id = _identifier(
            item.get("entity_id"), f"inventory entities[{index}].entity_id"
        )
        if entity_id in inventory_ids:
            raise WorldEffectOperationPlanError(
                f"inventory contains duplicate entity id {entity_id!r}"
            )
        inventory_ids.add(entity_id)
    related_ids = instance.related_entity_ids()
    if not set(related_ids).issubset(inventory_ids):
        raise WorldEffectOperationPlanError(
            "provider instance references entities absent from fresh inventory"
        )
    candidates: list[WorldEffectOperationCandidate] = []
    if instance.planning_ready:
        for activation in instance.tool_activations:
            candidate_seed = (
                f"{instance.instance_id}:{activation.requirement_id}:"
                f"{activation.activated_tool_id}"
            )
            candidate_id = "effect-operation:" + hashlib.sha256(
                candidate_seed.encode("utf-8")
            ).hexdigest()[:16]
            candidates.append(
                WorldEffectOperationCandidate(
                    operation_candidate_id=candidate_id,
                    provider_instance_id=instance.instance_id,
                    requirement_id=activation.requirement_id,
                    tool_id=activation.activated_tool_id,
                    tool_family=activation.tool_family,
                    capability_tags=activation.capability_tags,
                )
            )
    observation_seed = json.dumps(
        {
            "provider_instance": instance.to_dict(),
            "inventory": _json_copy(inventory, "inventory"),
            "candidates": [item.to_dict() for item in candidates],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    observation_id = "effect-operation-observation:" + hashlib.sha256(
        observation_seed.encode("utf-8")
    ).hexdigest()[:16]
    return WorldEffectOperationCandidateSet(
        observation_id=observation_id,
        provider_instance_id=instance.instance_id,
        related_entity_ids=related_ids,
        candidates=tuple(candidates),
    )


@dataclass(frozen=True)
class WorldEffectOperationDecision:
    observation_id: str
    decision: str
    operation_candidate_id: str | None
    requirement_id: str | None
    tool_id: str | None
    purpose: str | None
    target_entity_ids: tuple[str, ...]
    desired_outcome: str | None
    stop_condition: str | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_OPERATION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "operation_candidate_id": self.operation_candidate_id,
            "requirement_id": self.requirement_id,
            "tool_id": self.tool_id,
            "purpose": self.purpose,
            "target_entity_ids": list(self.target_entity_ids),
            "desired_outcome": self.desired_outcome,
            "stop_condition": self.stop_condition,
            "confidence": self.confidence,
            "reason": self.reason,
            "tool_called": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


class WorldEffectOperationGate:
    """Validate one semantic proposal without exposing an execution path."""

    def __init__(self, candidate_set: WorldEffectOperationCandidateSet) -> None:
        self.candidate_set = candidate_set
        self._triples = {
            (item.operation_candidate_id, item.requirement_id, item.tool_id)
            for item in candidate_set.candidates
        }

    def dispatch(self, payload: Mapping[str, Any]) -> WorldEffectOperationDecision:
        if not isinstance(payload, Mapping):
            raise WorldEffectOperationPlanError("operation proposal must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "operation_candidate_id",
            "requirement_id",
            "tool_id",
            "purpose",
            "target_entity_ids",
            "desired_outcome",
            "stop_condition",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldEffectOperationPlanError(
                f"operation proposal contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldEffectOperationPlanError(
                f"operation proposal is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_EFFECT_OPERATION_SCHEMA_VERSION:
            raise WorldEffectOperationPlanError("operation schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.candidate_set.observation_id:
            raise WorldEffectOperationPlanError("stale operation observation_id")
        decision = _text(payload["decision"], "decision")
        if decision not in WORLD_EFFECT_OPERATION_DECISIONS:
            raise WorldEffectOperationPlanError(
                f"unsupported operation decision {decision!r}"
            )
        proposal_fields = (
            "operation_candidate_id",
            "requirement_id",
            "tool_id",
            "purpose",
            "desired_outcome",
            "stop_condition",
        )
        raw_targets = payload["target_entity_ids"]
        if not isinstance(raw_targets, list):
            raise WorldEffectOperationPlanError("target_entity_ids must be an array")
        target_ids = tuple(
            _identifier(item, f"target_entity_ids[{index}]")
            for index, item in enumerate(raw_targets)
        )
        if len(set(target_ids)) != len(target_ids):
            raise WorldEffectOperationPlanError(
                "target_entity_ids must not contain duplicates"
            )
        if decision == "propose_operation":
            operation_candidate_id = _identifier(
                payload["operation_candidate_id"], "operation_candidate_id"
            )
            requirement_id = _identifier(payload["requirement_id"], "requirement_id")
            tool_id = _identifier(payload["tool_id"], "tool_id")
            if (operation_candidate_id, requirement_id, tool_id) not in self._triples:
                raise WorldEffectOperationPlanError(
                    "selected operation candidate/requirement/tool was not advertised"
                )
            purpose = _text(payload["purpose"], "purpose")
            if purpose not in WORLD_EFFECT_OPERATION_PURPOSES:
                raise WorldEffectOperationPlanError(
                    f"unsupported operation purpose {purpose!r}"
                )
            if not target_ids or not set(target_ids).issubset(
                self.candidate_set.related_entity_ids
            ):
                raise WorldEffectOperationPlanError(
                    "operation targets must be non-empty related entity ids"
                )
            desired_outcome = _text(payload["desired_outcome"], "desired_outcome")
            stop_condition = _text(payload["stop_condition"], "stop_condition")
        else:
            if any(payload[field] is not None for field in proposal_fields):
                raise WorldEffectOperationPlanError(
                    f"decision {decision!r} requires null proposal fields"
                )
            if target_ids:
                raise WorldEffectOperationPlanError(
                    f"decision {decision!r} requires empty target_entity_ids"
                )
            operation_candidate_id = None
            requirement_id = None
            tool_id = None
            purpose = None
            desired_outcome = None
            stop_condition = None
        return WorldEffectOperationDecision(
            observation_id=observation_id,
            decision=decision,
            operation_candidate_id=operation_candidate_id,
            requirement_id=requirement_id,
            tool_id=tool_id,
            purpose=purpose,
            target_entity_ids=target_ids,
            desired_outcome=desired_outcome,
            stop_condition=stop_condition,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def world_effect_operation_json_schema(
    candidate_set: WorldEffectOperationCandidateSet,
) -> dict[str, Any]:
    candidate_ids = sorted(
        item.operation_candidate_id for item in candidate_set.candidates
    )
    requirement_ids = sorted(
        {item.requirement_id for item in candidate_set.candidates}
    )
    tool_ids = sorted({item.tool_id for item in candidate_set.candidates})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "observation_id",
            "decision",
            "operation_candidate_id",
            "requirement_id",
            "tool_id",
            "purpose",
            "target_entity_ids",
            "desired_outcome",
            "stop_condition",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_EFFECT_OPERATION_SCHEMA_VERSION},
            "observation_id": {"const": candidate_set.observation_id},
            "decision": {"enum": sorted(WORLD_EFFECT_OPERATION_DECISIONS)},
            "operation_candidate_id": {
                "type": ["string", "null"],
                "enum": [None, *candidate_ids],
            },
            "requirement_id": {
                "type": ["string", "null"],
                "enum": [None, *requirement_ids],
            },
            "tool_id": {"type": ["string", "null"], "enum": [None, *tool_ids]},
            "purpose": {
                "type": ["string", "null"],
                "enum": [None, *sorted(WORLD_EFFECT_OPERATION_PURPOSES)],
            },
            "target_entity_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": list(candidate_set.related_entity_ids)},
            },
            "desired_outcome": {"type": ["string", "null"]},
            "stop_condition": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_world_effect_operation_prompt(
    *,
    instruction: str,
    inventory: Mapping[str, Any],
    instance: PlanningWorldEffectProviderInstance,
    candidate_set: WorldEffectOperationCandidateSet,
) -> str:
    """Ask for the first provider operation as a non-executable semantic outcome."""
    instruction = _text(instruction, "instruction")
    return f"""Propose the first semantic operation for a planning-only world-effect
provider instance using the attached fresh observation.

Human instruction:
{instruction}

Fresh semantic scene inventory:
{json.dumps(_json_copy(inventory, "inventory"), indent=2)}

Planning-only provider instance:
{json.dumps(instance.to_dict(), indent=2)}

Runtime-advertised operation candidates:
{json.dumps(candidate_set.to_dict(), indent=2)}

Choose propose_operation only for one exact advertised
operation_candidate_id/requirement_id/tool_id triple. Select its semantic
purpose, only related target entities, a desired observable outcome, and the
evidence condition at which the bounded operation must stop and return a fresh
observation. Use observe_again when current evidence is insufficient or blocked
when no candidate can advance the selected goal.

This proposal does not call the named tool. Declarative factory specifications
are instantiated, but no handler is bound and dispatch is disabled. Do not
output tool arguments, poses, trajectories, actuator commands, joint values, or
motor commands. Return exactly one JSON object matching this schema, with no
Markdown:
{json.dumps(world_effect_operation_json_schema(candidate_set), indent=2, sort_keys=True)}
"""
