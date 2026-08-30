"""Runtime-discovered evaluators for embodiment-neutral world predicates.

Goal graphs describe desired world state.  This registry separately advertises
which predicate forms the active sensing runtime can verify and evaluates those
forms from a fresh semantic scene inventory.  It contains no task sequence,
robot, actuator, or controller policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Callable, Mapping

try:
    from .world_goal_graph_contract import WorldGoalGraph
    from .world_intent_contract import WorldPredicate
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_goal_graph_contract import WorldGoalGraph  # type: ignore[no-redef]
    from world_intent_contract import WorldPredicate  # type: ignore[no-redef]


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_EVALUATION_STATUSES = frozenset({"satisfied", "unsatisfied", "unknown", "error"})
_AUTHORITIES = frozenset({"completion", "advisory"})


class WorldPredicateEvaluatorError(ValueError):
    """Raised when a predicate-evaluator plugin violates its runtime contract."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldPredicateEvaluatorError(f"{path} has an invalid format")
    return value


def _nonempty_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldPredicateEvaluatorError(f"{path} must be non-empty text")
    return value.strip()


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldPredicateEvaluatorError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldPredicateEvaluatorError(f"{path} must contain finite numbers")
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
                raise WorldPredicateEvaluatorError(f"{path} keys must be non-empty")
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    if hasattr(value, "tolist"):
        return _json_copy(value.tolist(), path, depth=depth + 1)
    raise WorldPredicateEvaluatorError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class WorldPredicateEvaluation:
    """Fresh evidence for one model-defined world predicate."""

    evaluator_id: str | None
    status: str
    reason: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.evaluator_id is not None:
            _identifier(self.evaluator_id, "evaluator_id")
        if self.status not in _EVALUATION_STATUSES:
            raise WorldPredicateEvaluatorError(
                f"unsupported predicate evaluation status {self.status!r}"
            )
        _identifier(self.reason, "reason")
        _json_copy(self.evidence, "evidence")

    @property
    def satisfied(self) -> bool | None:
        if self.status == "satisfied":
            return True
        if self.status == "unsatisfied":
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_id": self.evaluator_id,
            "status": self.status,
            "satisfied": self.satisfied,
            "reason": self.reason,
            "evidence": _json_copy(self.evidence, "evidence"),
        }


PredicateMatcher = Callable[[WorldPredicate], bool]
PredicateEvaluator = Callable[
    [WorldPredicate, Mapping[str, Any]], WorldPredicateEvaluation
]


@dataclass(frozen=True)
class WorldPredicateEvaluatorSpec:
    """One sensing plugin's advertised predicate forms and evaluator."""

    evaluator_id: str
    description: str
    authority: str
    evidence_source: str
    supported_predicate_forms: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    matcher: PredicateMatcher
    evaluator: PredicateEvaluator

    def __post_init__(self) -> None:
        _identifier(self.evaluator_id, "evaluator_id")
        _nonempty_text(self.description, "description")
        if self.authority not in _AUTHORITIES:
            raise WorldPredicateEvaluatorError(
                f"unsupported evaluator authority {self.authority!r}"
            )
        _identifier(self.evidence_source, "evidence_source")
        if not self.supported_predicate_forms:
            raise WorldPredicateEvaluatorError(
                "supported_predicate_forms must not be empty"
            )
        for index, form in enumerate(self.supported_predicate_forms):
            _json_copy(form, f"supported_predicate_forms[{index}]")
        for index, limitation in enumerate(self.limitations):
            _nonempty_text(limitation, f"limitations[{index}]")
        if not callable(self.matcher) or not callable(self.evaluator):
            raise WorldPredicateEvaluatorError(
                "matcher and evaluator must be callable"
            )

    def advertisement(self) -> dict[str, Any]:
        return {
            "evaluator_id": self.evaluator_id,
            "description": self.description,
            "authority": self.authority,
            "evidence_source": self.evidence_source,
            "supported_predicate_forms": [
                _json_copy(item, "supported_predicate_form")
                for item in self.supported_predicate_forms
            ],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class WorldGoalGraphPredicateAdmission:
    """Whether every executable graph predicate has completion evidence."""

    admitted: bool
    resolved_subset_admitted: bool
    graph_status: str
    predicate_count: int
    admitted_predicate_count: int
    unsupported_predicates: tuple[Mapping[str, Any], ...]
    current_evaluations: tuple[Mapping[str, Any], ...]
    required_observations: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "resolved_subset_admitted": self.resolved_subset_admitted,
            "graph_status": self.graph_status,
            "predicate_count": self.predicate_count,
            "admitted_predicate_count": self.admitted_predicate_count,
            "unsupported_predicates": [
                _json_copy(item, "unsupported_predicate")
                for item in self.unsupported_predicates
            ],
            "current_evaluations": [
                _json_copy(item, "current_evaluation")
                for item in self.current_evaluations
            ],
            "required_observations": [
                _json_copy(item, "required_observation")
                for item in self.required_observations
            ],
            "authority": "runtime_advertised_world_predicate_evidence",
        }


class WorldPredicateEvaluatorRegistry:
    """Discover evaluators and fail closed on unsupported goal predicates."""

    def __init__(self) -> None:
        self._specs: dict[str, WorldPredicateEvaluatorSpec] = {}

    def register(self, spec: WorldPredicateEvaluatorSpec) -> None:
        if not isinstance(spec, WorldPredicateEvaluatorSpec):
            raise WorldPredicateEvaluatorError(
                "predicate registration requires WorldPredicateEvaluatorSpec"
            )
        if spec.evaluator_id in self._specs:
            raise WorldPredicateEvaluatorError(
                f"predicate evaluator {spec.evaluator_id!r} is already registered"
            )
        self._specs[spec.evaluator_id] = spec

    def specs(self) -> tuple[WorldPredicateEvaluatorSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def advertisement(self) -> dict[str, Any]:
        return {
            "source": "active_runtime_world_predicate_registry",
            "completion_requires_advertised_evaluator": True,
            "evaluators": [spec.advertisement() for spec in self.specs()],
        }

    def supporting_specs(
        self,
        predicate: WorldPredicate,
        *,
        completion_only: bool = False,
    ) -> tuple[WorldPredicateEvaluatorSpec, ...]:
        if not isinstance(predicate, WorldPredicate):
            raise WorldPredicateEvaluatorError(
                "predicate must be a WorldPredicate instance"
            )
        matches: list[WorldPredicateEvaluatorSpec] = []
        for spec in self.specs():
            if completion_only and spec.authority != "completion":
                continue
            try:
                supported = spec.matcher(predicate)
            except Exception:
                supported = False
            if supported:
                matches.append(spec)
        return tuple(matches)

    def evaluate(
        self,
        predicate: WorldPredicate,
        inventory: Mapping[str, Any],
    ) -> WorldPredicateEvaluation:
        matches = self.supporting_specs(predicate, completion_only=True)
        if not matches:
            return WorldPredicateEvaluation(
                evaluator_id=None,
                status="unknown",
                reason="predicate_evaluator_not_advertised",
                evidence={"predicate": predicate.to_dict()},
            )
        spec = matches[0]
        try:
            result = spec.evaluator(predicate, inventory)
            if not isinstance(result, WorldPredicateEvaluation):
                raise WorldPredicateEvaluatorError(
                    "evaluator must return WorldPredicateEvaluation"
                )
            if result.evaluator_id != spec.evaluator_id:
                raise WorldPredicateEvaluatorError(
                    "evaluation evaluator_id does not match registered spec"
                )
            return result
        except Exception as error:  # Runtime plugins fail closed.
            return WorldPredicateEvaluation(
                evaluator_id=spec.evaluator_id,
                status="error",
                reason="predicate_evaluation_error",
                evidence={
                    "predicate": predicate.to_dict(),
                    "error": f"{type(error).__name__}: {error}",
                },
            )

    def assess_graph(
        self,
        graph: WorldGoalGraph,
        inventory: Mapping[str, Any],
    ) -> WorldGoalGraphPredicateAdmission:
        if not isinstance(graph, WorldGoalGraph):
            raise WorldPredicateEvaluatorError("graph must be a WorldGoalGraph")
        located: list[tuple[str, WorldPredicate]] = []
        for index, predicate in enumerate(graph.constraints):
            located.append((f"constraints[{index}]", predicate))
        for goal_index, goal in enumerate(graph.goals):
            for index, predicate in enumerate(goal.desired_state):
                located.append(
                    (f"goals[{goal_index}].desired_state[{index}]", predicate)
                )
            for index, predicate in enumerate(goal.valid_while):
                located.append(
                    (f"goals[{goal_index}].valid_while[{index}]", predicate)
                )

        unsupported: list[dict[str, Any]] = []
        current_evaluations: list[dict[str, Any]] = []
        admitted_count = 0
        for path, predicate in located:
            matches = self.supporting_specs(predicate, completion_only=True)
            if not matches:
                unsupported.append(
                    {"path": path, "predicate": predicate.to_dict()}
                )
                continue
            admitted_count += 1
            current_evaluations.append(
                {
                    "path": path,
                    "predicate": predicate.to_dict(),
                    "evaluation": self.evaluate(predicate, inventory).to_dict(),
                }
            )
        status_admitted = graph.status in {"ready", "complete"}
        resolved_subset_admitted = bool(
            graph.status == "needs_observation"
            and located
            and not unsupported
            and graph.required_observations
        )
        return WorldGoalGraphPredicateAdmission(
            admitted=bool(status_admitted and not unsupported),
            resolved_subset_admitted=resolved_subset_admitted,
            graph_status=graph.status,
            predicate_count=len(located),
            admitted_predicate_count=admitted_count,
            unsupported_predicates=tuple(unsupported),
            current_evaluations=tuple(current_evaluations),
            required_observations=tuple(
                predicate.to_dict() for predicate in graph.required_observations
            ),
        )


def _inventory_entities(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(raw_entities, list):
        raise WorldPredicateEvaluatorError("inventory entities must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, entity in enumerate(raw_entities):
        if not isinstance(entity, Mapping):
            raise WorldPredicateEvaluatorError(
                f"inventory entities[{index}] must be an object"
            )
        entity_id = _identifier(entity.get("entity_id"), f"entities[{index}].entity_id")
        if entity_id in result:
            raise WorldPredicateEvaluatorError(
                f"duplicate inventory entity id {entity_id!r}"
            )
        result[entity_id] = entity
    return result


def _inside_predicate_matcher(predicate: WorldPredicate) -> bool:
    if predicate.reference_id is None:
        return False
    if (
        predicate.attribute == "inside"
        and predicate.operator in {"==", "equals"}
        and predicate.value is True
    ):
        return True
    return bool(
        predicate.attribute == "spatial_relation"
        and predicate.operator in {"==", "equals"}
        and predicate.value == "inside"
    )


def _vector3(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise WorldPredicateEvaluatorError(f"{path} must be a three-vector")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise WorldPredicateEvaluatorError(f"{path} must contain finite values")
    return result  # type: ignore[return-value]


def _rgbd_inside_evaluator(
    predicate: WorldPredicate,
    inventory: Mapping[str, Any],
) -> WorldPredicateEvaluation:
    evaluator_id = "rgbd.visible_geometry_inside"
    entities = _inventory_entities(inventory)
    subject = entities.get(predicate.subject_id)
    reference = entities.get(str(predicate.reference_id))
    if subject is None or reference is None:
        return WorldPredicateEvaluation(
            evaluator_id=evaluator_id,
            status="unknown",
            reason="predicate_entity_absent",
            evidence={"predicate": predicate.to_dict()},
        )
    subject_status = subject.get("observation_status")
    reference_status = reference.get("observation_status")
    if subject_status != "visible_rgbd" or reference_status != "visible_rgbd":
        return WorldPredicateEvaluation(
            evaluator_id=evaluator_id,
            status="unknown",
            reason="predicate_geometry_not_fresh_visible",
            evidence={
                "predicate": predicate.to_dict(),
                "subject_observation_status": subject_status,
                "reference_observation_status": reference_status,
                "stale_geometry_accepted": False,
            },
        )
    subject_geometry = subject.get("geometry")
    reference_geometry = reference.get("geometry")
    if not isinstance(subject_geometry, Mapping) or not isinstance(
        reference_geometry, Mapping
    ):
        return WorldPredicateEvaluation(
            evaluator_id=evaluator_id,
            status="unknown",
            reason="predicate_geometry_unavailable",
            evidence={"predicate": predicate.to_dict()},
        )
    required = (
        "visible_aabb_min_base_m",
        "visible_aabb_max_base_m",
    )
    if any(key not in subject_geometry for key in required) or any(
        key not in reference_geometry for key in required
    ):
        return WorldPredicateEvaluation(
            evaluator_id=evaluator_id,
            status="unknown",
            reason="predicate_geometry_unavailable",
            evidence={"predicate": predicate.to_dict()},
        )
    subject_min = _vector3(
        subject_geometry["visible_aabb_min_base_m"], "subject_aabb_min"
    )
    subject_max = _vector3(
        subject_geometry["visible_aabb_max_base_m"], "subject_aabb_max"
    )
    reference_min = _vector3(
        reference_geometry["visible_aabb_min_base_m"], "reference_aabb_min"
    )
    reference_max = _vector3(
        reference_geometry["visible_aabb_max_base_m"], "reference_aabb_max"
    )
    tolerance_m = 0.005
    axis_contained = [
        subject_min[axis] >= reference_min[axis] - tolerance_m
        and subject_max[axis] <= reference_max[axis] + tolerance_m
        for axis in range(3)
    ]
    inside = all(axis_contained)
    return WorldPredicateEvaluation(
        evaluator_id=evaluator_id,
        status="satisfied" if inside else "unsatisfied",
        reason="visible_geometry_inside" if inside else "visible_geometry_not_inside",
        evidence={
            "subject_id": predicate.subject_id,
            "reference_id": predicate.reference_id,
            "frame": inventory.get("frame"),
            "axis_contained": axis_contained,
            "tolerance_m": tolerance_m,
            "subject_aabb_min_m": list(subject_min),
            "subject_aabb_max_m": list(subject_max),
            "reference_aabb_min_m": list(reference_min),
            "reference_aabb_max_m": list(reference_max),
        },
    )


def rgbd_world_predicate_evaluator_registry() -> WorldPredicateEvaluatorRegistry:
    """Register only predicates supported by the current synchronized RGB-D view."""
    registry = WorldPredicateEvaluatorRegistry()
    registry.register(
        WorldPredicateEvaluatorSpec(
            evaluator_id="rgbd.visible_geometry_inside",
            description=(
                "Determine whether one visible entity's complete observed AABB "
                "is inside another visible entity's observed AABB."
            ),
            authority="completion",
            evidence_source="synchronized_rgbd_instance_geometry",
            supported_predicate_forms=(
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
                "both entities must have fresh visible RGB-D geometry",
                "visible AABB containment does not estimate container capacity",
                "occlusion may make the relation unknown",
            ),
            matcher=_inside_predicate_matcher,
            evaluator=_rgbd_inside_evaluator,
        )
    )
    return registry
