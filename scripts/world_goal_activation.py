"""Shadow selection of one measurable goal and runtime-advertised capability.

This layer operates on world-state effects.  It does not expose body parts,
controllers, trajectories, or motor commands, and every decision is explicitly
non-authoritative until a separate runtime effect provider is admitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

try:
    from .world_entity_physical_evidence import (
        estimate_visible_destination_capacity,
    )
    from .world_goal_graph_contract import WorldGoalGraph, WorldGoalNode
    from .world_goal_graph_membership import SceneMembershipLease
    from .world_intent_contract import WorldPredicate
    from .world_predicate_evaluator_registry import (
        WorldPredicateEvaluation,
        WorldPredicateEvaluatorRegistry,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_entity_physical_evidence import (  # type: ignore[no-redef]
        estimate_visible_destination_capacity,
    )
    from world_goal_graph_contract import (  # type: ignore[no-redef]
        WorldGoalGraph,
        WorldGoalNode,
    )
    from world_goal_graph_membership import SceneMembershipLease  # type: ignore[no-redef]
    from world_intent_contract import WorldPredicate  # type: ignore[no-redef]
    from world_predicate_evaluator_registry import (  # type: ignore[no-redef]
        WorldPredicateEvaluation,
        WorldPredicateEvaluatorRegistry,
    )


WORLD_GOAL_ACTIVATION_SCHEMA_VERSION = "world-goal-activation.v1"
GOAL_ACTIVATION_DECISIONS = frozenset(
    {"select_goal", "observe_again", "blocked", "complete"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldGoalActivationError(ValueError):
    """Raised when a capability or activation response violates its contract."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldGoalActivationError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldGoalActivationError(f"{path} must be non-empty text")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldGoalActivationError("confidence must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldGoalActivationError("confidence must be a number in [0, 1]")
    return result


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldGoalActivationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldGoalActivationError(f"{path} must contain finite numbers")
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
                raise WorldGoalActivationError(f"{path} keys must be non-empty")
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    if hasattr(value, "tolist"):
        return _json_copy(value.tolist(), path, depth=depth + 1)
    raise WorldGoalActivationError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class WorldCapabilityAssessment:
    capability_id: str
    planning_ready: bool
    execution_ready: bool
    missing_evidence: tuple[str, ...]
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identifier(self.capability_id, "capability_id")
        if not isinstance(self.planning_ready, bool) or not isinstance(
            self.execution_ready, bool
        ):
            raise WorldGoalActivationError("capability readiness must be boolean")
        for index, item in enumerate(self.missing_evidence):
            _identifier(item, f"missing_evidence[{index}]")
        _json_copy(self.evidence, "capability evidence")
        if self.execution_ready and not self.planning_ready:
            raise WorldGoalActivationError(
                "execution-ready capability must also be planning-ready"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "planning_ready": self.planning_ready,
            "execution_ready": self.execution_ready,
            "missing_evidence": list(self.missing_evidence),
            "evidence": _json_copy(self.evidence, "capability evidence"),
        }


CapabilityMatcher = Callable[[WorldPredicate], bool]
CapabilityAssessor = Callable[
    [WorldGoalNode, Mapping[str, Any]], WorldCapabilityAssessment
]


@dataclass(frozen=True)
class WorldCapabilitySpec:
    capability_id: str
    description: str
    supported_effect_forms: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    matcher: CapabilityMatcher
    assessor: CapabilityAssessor

    def __post_init__(self) -> None:
        _identifier(self.capability_id, "capability_id")
        _text(self.description, "description")
        if not self.supported_effect_forms:
            raise WorldGoalActivationError("supported_effect_forms must not be empty")
        for index, item in enumerate(self.supported_effect_forms):
            _json_copy(item, f"supported_effect_forms[{index}]")
        for index, item in enumerate(self.limitations):
            _text(item, f"limitations[{index}]")
        if not callable(self.matcher) or not callable(self.assessor):
            raise WorldGoalActivationError("matcher and assessor must be callable")

    def advertisement(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "description": self.description,
            "supported_effect_forms": [
                _json_copy(item, "supported_effect_form")
                for item in self.supported_effect_forms
            ],
            "limitations": list(self.limitations),
        }


class WorldCapabilityRegistry:
    """Match desired effects to runtime-advertised world capabilities."""

    def __init__(self) -> None:
        self._specs: dict[str, WorldCapabilitySpec] = {}

    def register(self, spec: WorldCapabilitySpec) -> None:
        if not isinstance(spec, WorldCapabilitySpec):
            raise WorldGoalActivationError(
                "capability registration requires WorldCapabilitySpec"
            )
        if spec.capability_id in self._specs:
            raise WorldGoalActivationError(
                f"capability {spec.capability_id!r} is already registered"
            )
        self._specs[spec.capability_id] = spec

    def specs(self) -> tuple[WorldCapabilitySpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def advertisement(self) -> dict[str, Any]:
        return {
            "source": "active_runtime_world_capability_registry",
            "execution_authority": False,
            "capabilities": [spec.advertisement() for spec in self.specs()],
        }

    def assess_goal(
        self,
        goal: WorldGoalNode,
        inventory: Mapping[str, Any],
    ) -> tuple[WorldCapabilityAssessment, ...]:
        if not isinstance(goal, WorldGoalNode):
            raise WorldGoalActivationError("goal must be a WorldGoalNode")
        assessments: list[WorldCapabilityAssessment] = []
        for spec in self.specs():
            try:
                supports_all = all(spec.matcher(item) for item in goal.desired_state)
            except Exception:
                supports_all = False
            if not supports_all:
                continue
            try:
                assessment = spec.assessor(goal, inventory)
                if not isinstance(assessment, WorldCapabilityAssessment):
                    raise WorldGoalActivationError(
                        "capability assessor must return WorldCapabilityAssessment"
                    )
                if assessment.capability_id != spec.capability_id:
                    raise WorldGoalActivationError(
                        "assessment capability_id does not match registered spec"
                    )
            except Exception as error:
                assessment = WorldCapabilityAssessment(
                    capability_id=spec.capability_id,
                    planning_ready=False,
                    execution_ready=False,
                    missing_evidence=("capability_assessment_error",),
                    evidence={"error": f"{type(error).__name__}: {error}"},
                )
            assessments.append(assessment)
        return tuple(assessments)


@dataclass(frozen=True)
class GoalActivationCandidate:
    goal_id: str
    desired_state: tuple[Mapping[str, Any], ...]
    dependency_goal_ids: tuple[str, ...]
    predicate_evaluations: tuple[Mapping[str, Any], ...]
    capability_assessments: tuple[WorldCapabilityAssessment, ...]

    def planning_capability_ids(self) -> tuple[str, ...]:
        return tuple(
            item.capability_id
            for item in self.capability_assessments
            if item.planning_ready
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "desired_state": [
                _json_copy(item, "desired_state") for item in self.desired_state
            ],
            "dependency_goal_ids": list(self.dependency_goal_ids),
            "predicate_evaluations": [
                _json_copy(item, "predicate_evaluation")
                for item in self.predicate_evaluations
            ],
            "capability_assessments": [
                item.to_dict() for item in self.capability_assessments
            ],
            "planning_capability_ids": list(self.planning_capability_ids()),
            "execution_authority": False,
        }


@dataclass(frozen=True)
class GoalActivationBlocker:
    """Structured evidence explaining why a dependency-ready goal cannot activate."""

    goal_id: str
    reason_codes: tuple[str, ...]
    desired_state_evaluations: tuple[Mapping[str, Any], ...]
    valid_while_evaluations: tuple[Mapping[str, Any], ...]
    capability_assessments: tuple[WorldCapabilityAssessment, ...]

    def __post_init__(self) -> None:
        _identifier(self.goal_id, "goal_id")
        if not self.reason_codes:
            raise WorldGoalActivationError("blocker reason_codes must not be empty")
        for index, reason_code in enumerate(self.reason_codes):
            _identifier(reason_code, f"reason_codes[{index}]")
        for index, item in enumerate(self.desired_state_evaluations):
            _json_copy(item, f"desired_state_evaluations[{index}]")
        for index, item in enumerate(self.valid_while_evaluations):
            _json_copy(item, f"valid_while_evaluations[{index}]")
        for item in self.capability_assessments:
            if not isinstance(item, WorldCapabilityAssessment):
                raise WorldGoalActivationError(
                    "blocker capability assessments must be "
                    "WorldCapabilityAssessment instances"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "reason_codes": list(self.reason_codes),
            "desired_state_evaluations": [
                _json_copy(item, "desired_state_evaluation")
                for item in self.desired_state_evaluations
            ],
            "valid_while_evaluations": [
                _json_copy(item, "valid_while_evaluation")
                for item in self.valid_while_evaluations
            ],
            "capability_assessments": [
                item.to_dict() for item in self.capability_assessments
            ],
            "execution_authority": False,
        }


@dataclass(frozen=True)
class GoalActivationCandidateSet:
    graph_id: str
    membership_lease_id: str
    candidates: tuple[GoalActivationCandidate, ...]
    satisfied_goal_ids: tuple[str, ...]
    dependency_blocked_goal_ids: tuple[str, ...]
    evidence_blocked_goal_ids: tuple[str, ...]
    evidence_blockers: tuple[GoalActivationBlocker, ...]
    completion_blocked_goal_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "membership_lease_id": self.membership_lease_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "satisfied_goal_ids": list(self.satisfied_goal_ids),
            "dependency_blocked_goal_ids": list(
                self.dependency_blocked_goal_ids
            ),
            "evidence_blocked_goal_ids": list(self.evidence_blocked_goal_ids),
            "evidence_blockers": [
                item.to_dict() for item in self.evidence_blockers
            ],
            "completion_blocked_goal_ids": list(
                self.completion_blocked_goal_ids
            ),
            "execution_authority": False,
        }


def _goal_satisfaction(
    goal: WorldGoalNode,
    predicate_registry: WorldPredicateEvaluatorRegistry,
    inventory: Mapping[str, Any],
) -> tuple[bool | None, tuple[WorldPredicateEvaluation, ...]]:
    evaluations = tuple(
        predicate_registry.evaluate(predicate, inventory)
        for predicate in goal.desired_state
    )
    values = tuple(item.satisfied for item in evaluations)
    if goal.completion_policy == "all":
        satisfied = True if all(value is True for value in values) else (
            False if any(value is False for value in values) else None
        )
    else:
        satisfied = True if any(value is True for value in values) else (
            False if all(value is False for value in values) else None
        )
    return satisfied, evaluations


def retained_attachment_completion_blockers(
    graph: WorldGoalGraph,
    inventory: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Map goals whose subject is still owned by an active attachment.

    A world predicate such as ``inside`` may become geometrically true while a
    transport provider still retains the subject.  Runtime-owned continuation
    evidence is explicitly non-completion evidence, so the relation may remain
    selectable for the provider's release/finalization operation without
    changing the predicate's measured truth value.
    """
    evidence = inventory.get("world_effect_continuation_evidence")
    if evidence is None:
        return {}
    if not isinstance(evidence, Mapping):
        raise WorldGoalActivationError(
            "world_effect_continuation_evidence must be an object"
        )
    if evidence.get("gripper_engaged") is not True:
        return {}
    if evidence.get("task_completion_allowed") is not False:
        return {}
    goal_id = evidence.get("selected_goal_id")
    if not isinstance(goal_id, str):
        raise WorldGoalActivationError(
            "retained attachment evidence requires selected_goal_id"
        )
    goal = next((item for item in graph.goals if item.goal_id == goal_id), None)
    if goal is None:
        raise WorldGoalActivationError(
            "retained attachment evidence selected_goal_id is absent from graph"
        )
    raw_entity_ids = evidence.get("attachment_entity_ids")
    if not isinstance(raw_entity_ids, list):
        raise WorldGoalActivationError(
            "retained attachment evidence attachment_entity_ids must be an array"
        )
    attachment_entity_ids = {
        _identifier(item, "attachment_entity_ids[]") for item in raw_entity_ids
    }
    subject_ids = {predicate.subject_id for predicate in goal.desired_state}
    blockers = tuple(sorted(attachment_entity_ids & subject_ids))
    return {goal_id: blockers} if blockers else {}


def build_goal_activation_candidates(
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    predicate_registry: WorldPredicateEvaluatorRegistry,
    capability_registry: WorldCapabilityRegistry,
    inventory: Mapping[str, Any],
    *,
    completed_goal_ids: Sequence[str] = (),
) -> GoalActivationCandidateSet:
    """Expose dependency-ready, unsatisfied goals without choosing one locally."""
    if graph.graph_id != membership_lease.graph_id:
        raise WorldGoalActivationError(
            "membership lease graph_id does not match the goal graph"
        )
    lease_assessment = membership_lease.assess(inventory)
    if not lease_assessment.valid:
        raise WorldGoalActivationError(
            "scene membership lease is invalid: "
            + ",".join(lease_assessment.reasons)
        )
    declared_ids = {goal.goal_id for goal in graph.goals}
    completed = set(completed_goal_ids)
    unknown_completed = completed - declared_ids
    if unknown_completed:
        raise WorldGoalActivationError(
            f"completed_goal_ids contains unknown goals: {sorted(unknown_completed)}"
        )
    satisfaction: dict[str, tuple[bool | None, tuple[WorldPredicateEvaluation, ...]]] = {
        goal.goal_id: _goal_satisfaction(goal, predicate_registry, inventory)
        for goal in graph.goals
    }
    completion_blockers = retained_attachment_completion_blockers(
        graph, inventory
    )
    completion_blocked_ids = set(completion_blockers)
    satisfied_ids = {
        goal_id for goal_id, (value, _) in satisfaction.items() if value is True
    } | completed
    satisfied_ids -= completion_blocked_ids
    candidates: list[GoalActivationCandidate] = []
    dependency_blocked: list[str] = []
    evidence_blocked: list[str] = []
    evidence_blockers: list[GoalActivationBlocker] = []
    for goal in graph.goals:
        current_satisfaction, evaluations = satisfaction[goal.goal_id]
        if goal.goal_id in satisfied_ids:
            continue
        if not set(goal.depends_on).issubset(satisfied_ids):
            dependency_blocked.append(goal.goal_id)
            continue
        valid_while_evaluations = tuple(
            predicate_registry.evaluate(predicate, inventory)
            for predicate in goal.valid_while
        )
        capabilities = capability_registry.assess_goal(goal, inventory)
        reason_codes: list[str] = []
        if any(item.satisfied is not True for item in valid_while_evaluations):
            reason_codes.append("valid_while_not_satisfied")
        if not capabilities or not any(item.planning_ready for item in capabilities):
            reason_codes.append("no_planning_ready_capability")
        if reason_codes:
            evidence_blocked.append(goal.goal_id)
            evidence_blockers.append(
                GoalActivationBlocker(
                    goal_id=goal.goal_id,
                    reason_codes=tuple(reason_codes),
                    desired_state_evaluations=tuple(
                        {
                            "predicate": predicate.to_dict(),
                            "evaluation": evaluation.to_dict(),
                        }
                        for predicate, evaluation in zip(
                            goal.desired_state, evaluations, strict=True
                        )
                    ),
                    valid_while_evaluations=tuple(
                        {
                            "predicate": predicate.to_dict(),
                            "evaluation": evaluation.to_dict(),
                        }
                        for predicate, evaluation in zip(
                            goal.valid_while,
                            valid_while_evaluations,
                            strict=True,
                        )
                    ),
                    capability_assessments=capabilities,
                )
            )
            continue
        candidates.append(
            GoalActivationCandidate(
                goal_id=goal.goal_id,
                desired_state=tuple(item.to_dict() for item in goal.desired_state),
                dependency_goal_ids=goal.depends_on,
                predicate_evaluations=tuple(
                    {
                        "predicate": predicate.to_dict(),
                        "evaluation": evaluation.to_dict(),
                    }
                    for predicate, evaluation in zip(
                        goal.desired_state, evaluations, strict=True
                    )
                ),
                capability_assessments=capabilities,
            )
        )
    return GoalActivationCandidateSet(
        graph_id=graph.graph_id,
        membership_lease_id=membership_lease.lease_id,
        candidates=tuple(candidates),
        satisfied_goal_ids=tuple(sorted(satisfied_ids)),
        dependency_blocked_goal_ids=tuple(sorted(dependency_blocked)),
        evidence_blocked_goal_ids=tuple(sorted(evidence_blocked)),
        evidence_blockers=tuple(
            sorted(evidence_blockers, key=lambda item: item.goal_id)
        ),
        completion_blocked_goal_ids=tuple(sorted(completion_blocked_ids)),
    )


@dataclass(frozen=True)
class WorldGoalActivationDecision:
    observation_id: str
    decision: str
    goal_id: str | None
    capability_id: str | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_GOAL_ACTIVATION_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "decision": self.decision,
            "goal_id": self.goal_id,
            "capability_id": self.capability_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "execution_authority": False,
        }


class WorldGoalActivationGate:
    """Validate a shadow choice against the exact fresh candidate set."""

    def __init__(
        self,
        observation_id: str,
        candidate_set: GoalActivationCandidateSet,
    ) -> None:
        self.observation_id = _identifier(observation_id, "observation_id")
        self.candidate_set = candidate_set
        self._pairs = {
            (candidate.goal_id, capability_id)
            for candidate in candidate_set.candidates
            for capability_id in candidate.planning_capability_ids()
        }

    def dispatch(self, payload: Mapping[str, Any]) -> WorldGoalActivationDecision:
        if not isinstance(payload, Mapping):
            raise WorldGoalActivationError("goal activation response must be an object")
        allowed = {
            "schema_version",
            "observation_id",
            "decision",
            "goal_id",
            "capability_id",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldGoalActivationError(
                f"goal activation contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldGoalActivationError(
                f"goal activation is missing fields: {sorted(missing)}"
            )
        if payload["schema_version"] != WORLD_GOAL_ACTIVATION_SCHEMA_VERSION:
            raise WorldGoalActivationError("goal activation schema_version mismatch")
        observation_id = _identifier(payload["observation_id"], "observation_id")
        if observation_id != self.observation_id:
            raise WorldGoalActivationError("stale goal activation observation_id")
        decision = _text(payload["decision"], "decision")
        if decision not in GOAL_ACTIVATION_DECISIONS:
            raise WorldGoalActivationError(f"unsupported decision {decision!r}")
        goal_id = payload["goal_id"]
        capability_id = payload["capability_id"]
        if decision == "select_goal":
            goal_id = _identifier(goal_id, "goal_id")
            capability_id = _identifier(capability_id, "capability_id")
            if (goal_id, capability_id) not in self._pairs:
                raise WorldGoalActivationError(
                    "selected goal/capability pair was not advertised"
                )
        elif goal_id is not None or capability_id is not None:
            raise WorldGoalActivationError(
                f"decision {decision!r} requires null goal_id and capability_id"
            )
        if decision == "complete" and (
            self.candidate_set.candidates
            or self.candidate_set.dependency_blocked_goal_ids
            or self.candidate_set.evidence_blocked_goal_ids
        ):
            raise WorldGoalActivationError(
                "complete is invalid while unresolved goals remain"
            )
        return WorldGoalActivationDecision(
            observation_id=observation_id,
            decision=decision,
            goal_id=goal_id,
            capability_id=capability_id,
            confidence=_confidence(payload["confidence"]),
            reason=_text(payload["reason"], "reason"),
        )


def goal_activation_json_schema(
    observation_id: str,
    candidate_set: GoalActivationCandidateSet,
) -> dict[str, Any]:
    goal_ids = sorted(item.goal_id for item in candidate_set.candidates)
    capability_ids = sorted(
        {
            capability_id
            for item in candidate_set.candidates
            for capability_id in item.planning_capability_ids()
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
            "goal_id",
            "capability_id",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_GOAL_ACTIVATION_SCHEMA_VERSION},
            "observation_id": {"const": observation_id},
            "decision": {"enum": sorted(GOAL_ACTIVATION_DECISIONS)},
            "goal_id": {"type": ["string", "null"], "enum": [None, *goal_ids]},
            "capability_id": {
                "type": ["string", "null"],
                "enum": [None, *capability_ids],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def build_world_goal_activation_prompt(
    *,
    instruction: str,
    observation_id: str,
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    inventory: Mapping[str, Any],
    capability_advertisement: Mapping[str, Any],
    candidate_set: GoalActivationCandidateSet,
) -> str:
    """Ask a reasoner to select one advertised outcome/capability pair."""
    instruction = _text(instruction, "instruction")
    schema = goal_activation_json_schema(observation_id, candidate_set)
    return f"""Select the next high-level world-state goal from a fresh observation.

Human instruction:
{instruction}

Fresh observation token:
{observation_id}

Validated world goal graph:
{json.dumps(graph.to_dict(), indent=2)}

Fresh scene membership lease:
{json.dumps(membership_lease.to_dict(), indent=2)}

Fresh semantic scene inventory:
{json.dumps(_json_copy(inventory, "inventory"), indent=2)}

Runtime-advertised world capabilities:
{json.dumps(_json_copy(capability_advertisement, "capabilities"), indent=2)}

Dependency-ready, currently-unsatisfied candidates:
{json.dumps(candidate_set.to_dict(), indent=2)}

Choose select_goal only for an advertised goal/capability pair whose
planning_ready value is true. Use the fresh image and geometry to prefer a safe,
reachable-looking, unobstructed next outcome, but do not invent physical
evidence. If execution_ready is false, explicitly acknowledge its
missing_evidence; this shadow selection does not dispatch it. Use observe_again
for insufficient visual evidence, blocked when no proposed capability can
advance the task, or complete only when no unresolved goal remains.

Describe only goal selection and evidence. Do not output mechanisms, body
parts, controllers, trajectories, poses, or motor commands. Return exactly one
JSON object matching this schema, with no Markdown:
{json.dumps(schema, indent=2, sort_keys=True)}
"""


def _inside_effect_matcher(predicate: WorldPredicate) -> bool:
    if predicate.reference_id is None:
        return False
    return bool(
        (
            predicate.attribute == "inside"
            and predicate.operator in {"==", "equals"}
            and predicate.value is True
        )
        or (
            predicate.attribute == "spatial_relation"
            and predicate.operator in {"==", "equals"}
            and predicate.value == "inside"
        )
    )


def _inventory_by_id(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(entities, list):
        raise WorldGoalActivationError("inventory entities must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, entity in enumerate(entities):
        if not isinstance(entity, Mapping):
            raise WorldGoalActivationError(
                f"inventory entities[{index}] must be an object"
            )
        entity_id = _identifier(entity.get("entity_id"), f"entities[{index}].entity_id")
        result[entity_id] = entity
    return result


def _retained_attachment_subject_ids(
    goal: WorldGoalNode,
    inventory: Mapping[str, Any],
) -> frozenset[str]:
    """Validate planning-only continuation evidence for occluded subjects."""
    evidence = inventory.get("world_effect_continuation_evidence")
    if not isinstance(evidence, Mapping):
        return frozenset()
    retained_mode = bool(
        evidence.get("retained_contact_supported") is True
        and evidence.get("recovery_actuator_only") is False
    )
    recovery_mode = bool(
        evidence.get("retained_contact_supported") is False
        and evidence.get("recovery_actuator_only") is True
    )
    if not bool(
        evidence.get("schema_version")
        == "world-effect-continuation-evidence.v1"
        and evidence.get("selected_goal_id") == goal.goal_id
        and evidence.get("planning_continuation_allowed") is True
        and evidence.get("gripper_engaged") is True
        and (retained_mode or recovery_mode)
        and evidence.get("completion_evidence") is False
        and evidence.get("task_completion_allowed") is False
        and evidence.get("dispatch_enabled") is False
        and evidence.get("motion_authority") is False
        and evidence.get("execution_authority") is False
        and evidence.get("authority_scope") == []
    ):
        return frozenset()
    raw_attachment_ids = evidence.get("attachment_entity_ids")
    raw_tracked_ids = evidence.get("tracked_present_entity_ids")
    if not isinstance(raw_attachment_ids, list) or not isinstance(
        raw_tracked_ids, list
    ):
        return frozenset()
    attachment_ids = {
        item for item in raw_attachment_ids if isinstance(item, str) and item
    }
    tracked_ids = {
        item for item in raw_tracked_ids if isinstance(item, str) and item
    }
    subject_ids = {item.subject_id for item in goal.desired_state}
    if not subject_ids or not subject_ids.issubset(attachment_ids & tracked_ids):
        return frozenset()
    entities = _inventory_by_id(inventory)
    for subject_id in subject_ids:
        entity = entities.get(subject_id, {})
        status = entity.get("observation_status")
        if status == "visible_rgbd":
            continue
        temporal = entity.get("temporal_presence_evidence")
        geometry = entity.get("geometry")
        if not bool(
            status == "temporarily_occluded_rgbd"
            and isinstance(geometry, Mapping)
            and not geometry
            and isinstance(temporal, Mapping)
            and temporal.get("independently_present") is True
            and temporal.get("cached_geometry_exposed") is False
            and temporal.get("completion_evidence") is False
            and temporal.get("execution_authority") is False
        ):
            return frozenset()
    return frozenset(subject_ids)


def _inside_capability_assessor(
    goal: WorldGoalNode,
    inventory: Mapping[str, Any],
    effect_provider_assessment: Mapping[str, Any] | None = None,
) -> WorldCapabilityAssessment:
    capability_id = "world_relation.realize_inside"
    entities = _inventory_by_id(inventory)
    subject_ids = {item.subject_id for item in goal.desired_state}
    reference_ids = {
        item.reference_id
        for item in goal.desired_state
        if item.reference_id is not None
    }
    related_ids = subject_ids | reference_ids
    retained_attachment_subject_ids = _retained_attachment_subject_ids(
        goal,
        inventory,
    )
    visible = {
        entity_id: bool(
            entity_id in entities
            and entities[entity_id].get("observation_status") == "visible_rgbd"
            and isinstance(entities[entity_id].get("geometry"), Mapping)
            and bool(entities[entity_id].get("geometry"))
        )
        for entity_id in sorted(related_ids)
    }
    planning_entity_ready = {
        entity_id: bool(
            visible[entity_id]
            or (
                entity_id in subject_ids
                and entity_id in retained_attachment_subject_ids
            )
        )
        for entity_id in sorted(related_ids)
    }
    planning_ready = bool(
        planning_entity_ready and all(planning_entity_ready.values())
    )
    missing = [
        entity_id.replace("-", "_") + ".visible_geometry"
        for entity_id, available in visible.items()
        if not available
    ]
    subject_physical_evidence: dict[str, Any] = {}
    mobility_unknown = False
    mass_unknown = False
    planning_blockers: list[str] = []
    for subject_id in sorted(subject_ids):
        entity = entities.get(subject_id, {})
        raw_physical = entity.get("physical_evidence", {})
        if not isinstance(raw_physical, Mapping):
            raw_physical = {}
        raw_mobility = raw_physical.get("mobility", {})
        raw_mass = raw_physical.get("mass", {})
        mobility_status = (
            raw_mobility.get("status")
            if isinstance(raw_mobility, Mapping)
            else None
        )
        mass_available = bool(
            isinstance(raw_mass, Mapping)
            and raw_mass.get("available") is True
            and isinstance(raw_mass.get("mass_kg"), (int, float))
            and not isinstance(raw_mass.get("mass_kg"), bool)
            and math.isfinite(float(raw_mass["mass_kg"]))
        )
        subject_physical_evidence[subject_id] = {
            "mobility_status": mobility_status or "unknown",
            "mobility_available": mobility_status in {"dynamic", "deformable"},
            "mass_available": mass_available,
            "mass_kg": float(raw_mass["mass_kg"]) if mass_available else None,
            "source": raw_physical.get("source"),
        }
        if mobility_status not in {"dynamic", "deformable"}:
            if mobility_status in {"fixed", "kinematic"}:
                planning_blockers.append(
                    f"{subject_id}.mobility_status={mobility_status}"
                )
            else:
                mobility_unknown = True
        if not mass_available:
            mass_unknown = True

    if mobility_unknown:
        missing.append("subject_mobility")
    if mass_unknown:
        missing.append("subject_mass")

    capacity_estimates: list[dict[str, Any]] = []
    capacity_unknown = False
    interior_clearance_unknown = False
    for predicate in goal.desired_state:
        if predicate.reference_id is None:
            continue
        subject = entities.get(predicate.subject_id, {})
        reference = entities.get(predicate.reference_id, {})
        subject_geometry = subject.get("geometry", {})
        reference_geometry = reference.get("geometry", {})
        if not isinstance(subject_geometry, Mapping):
            subject_geometry = {}
        if not isinstance(reference_geometry, Mapping):
            reference_geometry = {}
        estimate = estimate_visible_destination_capacity(
            subject_geometry,
            reference_geometry,
        )
        capacity_estimates.append(
            {
                "subject_id": predicate.subject_id,
                "reference_id": predicate.reference_id,
                **estimate,
            }
        )
        if not estimate.get("available"):
            capacity_unknown = True
        elif estimate.get("subject_fits_observed_envelope") is not True:
            planning_blockers.append(
                f"{predicate.subject_id}.does_not_fit_observed_envelope_of."
                f"{predicate.reference_id}"
            )
        elif estimate.get("interior_clearance_observed") is not True:
            interior_clearance_unknown = True

    if capacity_unknown:
        missing.append("destination_capacity")
    if interior_clearance_unknown:
        missing.append("destination_interior_clearance")
    provider_evidence = (
        dict(effect_provider_assessment)
        if isinstance(effect_provider_assessment, Mapping)
        else {
            "binding_ready": False,
            "active_binding_ready": False,
            "reason": "runtime effect-provider assessment was not supplied",
            "execution_authority": False,
        }
    )
    if provider_evidence.get("binding_ready") is not True:
        missing.append("runtime_effect_provider_binding")
    elif provider_evidence.get("active_binding_ready") is not True:
        missing.append("runtime_effect_provider_activation")
    planning_ready = planning_ready and not planning_blockers
    return WorldCapabilityAssessment(
        capability_id=capability_id,
        planning_ready=planning_ready,
        execution_ready=False,
        missing_evidence=tuple(missing),
        evidence={
            "related_entity_visibility": visible,
            "related_entity_planning_ready": planning_entity_ready,
            "retained_attachment_subject_ids": sorted(
                retained_attachment_subject_ids
            ),
            "subject_physical_evidence": subject_physical_evidence,
            "destination_capacity_estimates": capacity_estimates,
            "runtime_effect_provider_assessment": provider_evidence,
            "planning_blockers": planning_blockers,
            "effect_supported": True,
            "shadow_only": True,
        },
    )


def shadow_world_capability_registry(
    *,
    effect_provider_assessment: Mapping[str, Any] | None = None,
) -> WorldCapabilityRegistry:
    """Advertise measurable effects while execution provider binding is pending."""
    registry = WorldCapabilityRegistry()
    registry.register(
        WorldCapabilitySpec(
            capability_id="world_relation.realize_inside",
            description=(
                "Propose establishing an inside relation between two observed "
                "entities; no physical effect provider is bound yet."
            ),
            supported_effect_forms=(
                {
                    "attribute": "inside",
                    "operator": "==",
                    "value": True,
                    "reference_id": "required_inventory_entity_id",
                },
                {
                    "attribute": "spatial_relation",
                    "operator": "equals",
                    "value": "inside",
                    "reference_id": "required_inventory_entity_id",
                },
            ),
            limitations=(
                "entities without runtime physics metadata retain unknown mobility or mass",
                "visible destination bounds are a planning-only capacity upper bound",
                "destination interior clearance and insertion paths are not yet observed",
                "dynamic runtime effect-provider binding is not connected",
            ),
            matcher=_inside_effect_matcher,
            assessor=(
                lambda goal, inventory: _inside_capability_assessor(
                    goal,
                    inventory,
                    effect_provider_assessment,
                )
            ),
        )
    )
    return registry
