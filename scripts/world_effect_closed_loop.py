"""Fresh-evidence progression for bounded world-effect execution sequences.

One dispatch permit can authorize exactly one invocation.  This module owns the
boundary after that invocation: it re-evaluates the selected world goal from a
fresh semantic inventory and decides whether the same admitted graph may plan
another operation.  It never chooses a tool, mechanism, pose, or actuator
command and it never grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

try:
    from .world_goal_activation import (
        GoalActivationCandidateSet,
        WorldCapabilityRegistry,
        build_goal_activation_candidates,
    )
    from .world_goal_graph_contract import WorldGoalGraph
    from .world_goal_graph_membership import (
        SceneMembershipLease,
        SceneMembershipLeaseAssessment,
    )
    from .world_predicate_evaluator_registry import (
        WorldPredicateEvaluation,
        WorldPredicateEvaluatorRegistry,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_goal_activation import (  # type: ignore[no-redef]
        GoalActivationCandidateSet,
        WorldCapabilityRegistry,
        build_goal_activation_candidates,
    )
    from world_goal_graph_contract import WorldGoalGraph  # type: ignore[no-redef]
    from world_goal_graph_membership import (  # type: ignore[no-redef]
        SceneMembershipLease,
        SceneMembershipLeaseAssessment,
    )
    from world_predicate_evaluator_registry import (  # type: ignore[no-redef]
        WorldPredicateEvaluation,
        WorldPredicateEvaluatorRegistry,
    )


WORLD_EFFECT_PROGRESS_SCHEMA_VERSION = "world-effect-progress.v1"
WORLD_EFFECT_SEQUENCE_SCHEMA_VERSION = "world-effect-sequence.v1"
WORLD_EFFECT_PROGRESS_STATUSES = frozenset(
    {
        "continue_selected_goal",
        "selected_goal_completed",
        "fresh_graph_required",
        "observe_again",
        "blocked",
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectClosedLoopError(ValueError):
    """Raised when sequence evidence violates the closed-loop contract."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectClosedLoopError(f"{path} has an invalid format")
    return value


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 14:
        raise WorldEffectClosedLoopError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectClosedLoopError(f"{path} contains non-finite data")
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
                raise WorldEffectClosedLoopError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectClosedLoopError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _goal_evaluations(
    graph: WorldGoalGraph,
    goal_id: str,
    predicate_registry: WorldPredicateEvaluatorRegistry,
    inventory: Mapping[str, Any],
) -> tuple[WorldPredicateEvaluation, ...]:
    goal = next((item for item in graph.goals if item.goal_id == goal_id), None)
    if goal is None:
        raise WorldEffectClosedLoopError(
            f"selected_goal_id {goal_id!r} is absent from the graph"
        )
    return tuple(
        predicate_registry.evaluate(predicate, inventory)
        for predicate in goal.desired_state
    )


def _goal_satisfied(graph: WorldGoalGraph, goal_id: str, values: Sequence[bool | None]) -> bool | None:
    goal = next(item for item in graph.goals if item.goal_id == goal_id)
    if goal.completion_policy == "all":
        if all(value is True for value in values):
            return True
        if any(value is False for value in values):
            return False
        return None
    if any(value is True for value in values):
        return True
    if all(value is False for value in values):
        return False
    return None


@dataclass(frozen=True)
class WorldEffectProgressAssessment:
    """Non-authoritative next-step evidence after one consumed dispatch."""

    observation_id: str
    operation_index: int
    selected_goal_id: str
    status: str
    reason: str
    membership_assessment: SceneMembershipLeaseAssessment
    selected_goal_satisfied: bool | None
    selected_goal_evaluations: tuple[WorldPredicateEvaluation, ...]
    continuation_candidates: GoalActivationCandidateSet | None

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation_id")
        if isinstance(self.operation_index, bool) or not isinstance(
            self.operation_index, int
        ) or self.operation_index < 1:
            raise WorldEffectClosedLoopError(
                "operation_index must be a positive integer"
            )
        _identifier(self.selected_goal_id, "selected_goal_id")
        if self.status not in WORLD_EFFECT_PROGRESS_STATUSES:
            raise WorldEffectClosedLoopError(
                f"unsupported progress status {self.status!r}"
            )
        _identifier(self.reason, "reason")

    @property
    def may_plan_another_operation(self) -> bool:
        return self.status == "continue_selected_goal"

    @property
    def requires_fresh_graph(self) -> bool:
        return self.status in {
            "selected_goal_completed",
            "fresh_graph_required",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_PROGRESS_SCHEMA_VERSION,
            "observation_id": self.observation_id,
            "operation_index": self.operation_index,
            "selected_goal_id": self.selected_goal_id,
            "status": self.status,
            "reason": self.reason,
            "membership_assessment": self.membership_assessment.to_dict(),
            "selected_goal_satisfied": self.selected_goal_satisfied,
            "selected_goal_evaluations": [
                item.to_dict() for item in self.selected_goal_evaluations
            ],
            "continuation_candidates": (
                None
                if self.continuation_candidates is None
                else self.continuation_candidates.to_dict()
            ),
            "may_plan_another_operation": self.may_plan_another_operation,
            "requires_fresh_graph": self.requires_fresh_graph,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        }


def assess_world_effect_progress(
    *,
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    selected_goal_id: str,
    predicate_registry: WorldPredicateEvaluatorRegistry,
    capability_registry: WorldCapabilityRegistry,
    inventory: Mapping[str, Any],
    operation_index: int,
    completed_goal_ids: Sequence[str] = (),
) -> WorldEffectProgressAssessment:
    """Re-observe one selected goal and fail closed before another operation."""
    if not isinstance(graph, WorldGoalGraph):
        raise WorldEffectClosedLoopError("graph must be a WorldGoalGraph")
    if not isinstance(membership_lease, SceneMembershipLease):
        raise WorldEffectClosedLoopError(
            "membership_lease must be a SceneMembershipLease"
        )
    if membership_lease.graph_id != graph.graph_id:
        raise WorldEffectClosedLoopError(
            "membership lease graph_id does not match the graph"
        )
    selected_goal_id = _identifier(selected_goal_id, "selected_goal_id")
    if isinstance(operation_index, bool) or not isinstance(operation_index, int) or operation_index < 1:
        raise WorldEffectClosedLoopError(
            "operation_index must be a positive integer"
        )

    evaluations = _goal_evaluations(
        graph, selected_goal_id, predicate_registry, inventory
    )
    satisfied = _goal_satisfied(
        graph,
        selected_goal_id,
        tuple(item.satisfied for item in evaluations),
    )
    baseline_membership = membership_lease.assess(inventory)
    observation_id = "world-effect-progress:" + _digest(
        {
            "graph": graph.to_dict(),
            "membership_lease": membership_lease.to_dict(),
            "selected_goal_id": selected_goal_id,
            "operation_index": operation_index,
            "inventory": inventory,
            "evaluations": [item.to_dict() for item in evaluations],
        }
    )

    if not baseline_membership.valid:
        return WorldEffectProgressAssessment(
            observation_id=observation_id,
            operation_index=operation_index,
            selected_goal_id=selected_goal_id,
            status="fresh_graph_required",
            reason="scene_membership_changed",
            membership_assessment=baseline_membership,
            selected_goal_satisfied=satisfied,
            selected_goal_evaluations=evaluations,
            continuation_candidates=None,
        )

    if satisfied is True:
        completion_membership = membership_lease.assess(
            inventory,
            completed_goal_id=selected_goal_id,
        )
        return WorldEffectProgressAssessment(
            observation_id=observation_id,
            operation_index=operation_index,
            selected_goal_id=selected_goal_id,
            status="selected_goal_completed",
            reason="fresh_predicates_satisfied",
            membership_assessment=completion_membership,
            selected_goal_satisfied=True,
            selected_goal_evaluations=evaluations,
            continuation_candidates=None,
        )

    continuation = build_goal_activation_candidates(
        graph,
        membership_lease,
        predicate_registry,
        capability_registry,
        inventory,
        completed_goal_ids=completed_goal_ids,
    )
    candidate_ids = {item.goal_id for item in continuation.candidates}
    if selected_goal_id in candidate_ids and satisfied is False:
        status = "continue_selected_goal"
        reason = "selected_goal_remains_unsatisfied"
    elif satisfied is None:
        status = "observe_again"
        reason = "selected_goal_evidence_unknown"
    else:
        status = "blocked"
        reason = (
            "selected_goal_evidence_blocked"
            if selected_goal_id in continuation.evidence_blocked_goal_ids
            else "selected_goal_not_continuable"
        )
    return WorldEffectProgressAssessment(
        observation_id=observation_id,
        operation_index=operation_index,
        selected_goal_id=selected_goal_id,
        status=status,
        reason=reason,
        membership_assessment=baseline_membership,
        selected_goal_satisfied=satisfied,
        selected_goal_evaluations=evaluations,
        continuation_candidates=continuation,
    )


@dataclass(frozen=True)
class WorldEffectSequenceBudget:
    """Runtime-owned bound; model output cannot enlarge the operation budget."""

    maximum_operations: int

    def __post_init__(self) -> None:
        if isinstance(self.maximum_operations, bool) or not isinstance(
            self.maximum_operations, int
        ) or self.maximum_operations < 1:
            raise WorldEffectClosedLoopError(
                "maximum_operations must be a positive integer"
            )

    def allows(self, next_operation_index: int) -> bool:
        if isinstance(next_operation_index, bool) or not isinstance(
            next_operation_index, int
        ) or next_operation_index < 1:
            raise WorldEffectClosedLoopError(
                "next_operation_index must be a positive integer"
            )
        return next_operation_index <= self.maximum_operations

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_SEQUENCE_SCHEMA_VERSION,
            "maximum_operations": self.maximum_operations,
            "authority": "runtime_configuration",
        }
