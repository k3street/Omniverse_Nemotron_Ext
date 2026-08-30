"""Embodiment-neutral causal goal graphs grounded in a semantic scene inventory.

The graph describes observable outcomes and causal dependencies only.  It does
not select an embodiment, controller, actuator, trajectory, or motor command.
Runtime capability discovery and execution remain separate concerns.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Iterable, Mapping, Sequence

try:
    from .world_intent_contract import (
        REOBSERVE_POLICIES,
        WorldIntentValidationError,
        WorldPredicate,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_intent_contract import (  # type: ignore[no-redef]
        REOBSERVE_POLICIES,
        WorldIntentValidationError,
        WorldPredicate,
    )


WORLD_GOAL_GRAPH_SCHEMA_VERSION = "world-goal-graph.v1"
SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION = "semantic-scene-inventory.v1"
WORLD_GOAL_GRAPH_STATUSES = frozenset(
    {"ready", "needs_observation", "infeasible", "complete"}
)
GOAL_COMPLETION_POLICIES = frozenset({"all", "any"})
ENTITY_SCOPE_STATUSES = frozenset({"included", "context", "excluded", "unknown"})
MAX_GOAL_NODES = 64


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldIntentValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _text_array(value: Any, path: str, *, maximum: int = MAX_GOAL_NODES) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorldIntentValidationError(f"{path} must be an array")
    if len(value) > maximum:
        raise WorldIntentValidationError(f"{path} may contain at most {maximum} items")
    result = tuple(
        _required_text(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )
    if len(result) != len(set(result)):
        raise WorldIntentValidationError(f"{path} must not contain duplicates")
    return result


def _predicate_array(
    value: Any,
    path: str,
    *,
    maximum: int = 32,
) -> tuple[WorldPredicate, ...]:
    if not isinstance(value, list):
        raise WorldIntentValidationError(f"{path} must be an array")
    if len(value) > maximum:
        raise WorldIntentValidationError(f"{path} may contain at most {maximum} items")
    return tuple(
        WorldPredicate.from_mapping(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )


def _finite_confidence(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldIntentValidationError(f"{path} must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldIntentValidationError(f"{path} must be a number in [0, 1]")
    return result


@dataclass(frozen=True)
class WorldGoalNode:
    """One observable outcome and the outcomes that causally precede it."""

    goal_id: str
    desired_state: tuple[WorldPredicate, ...]
    depends_on: tuple[str, ...]
    valid_while: tuple[WorldPredicate, ...]
    completion_policy: str
    reobserve_after: str
    rationale: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], path: str) -> "WorldGoalNode":
        if not isinstance(payload, Mapping):
            raise WorldIntentValidationError(f"{path} must be an object")
        allowed = {
            "goal_id",
            "desired_state",
            "depends_on",
            "valid_while",
            "completion_policy",
            "reobserve_after",
            "rationale",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldIntentValidationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldIntentValidationError(
                f"{path} is missing required fields: {sorted(missing)}"
            )
        desired_state = _predicate_array(
            payload["desired_state"], f"{path}.desired_state"
        )
        if not desired_state:
            raise WorldIntentValidationError(
                f"{path}.desired_state must contain at least one predicate"
            )
        completion_policy = _required_text(
            payload["completion_policy"], f"{path}.completion_policy"
        )
        if completion_policy not in GOAL_COMPLETION_POLICIES:
            raise WorldIntentValidationError(
                f"{path}.completion_policy must be all or any"
            )
        reobserve_after = _required_text(
            payload["reobserve_after"], f"{path}.reobserve_after"
        )
        if reobserve_after not in REOBSERVE_POLICIES:
            raise WorldIntentValidationError(
                f"{path}.reobserve_after has unsupported policy {reobserve_after!r}"
            )
        return cls(
            goal_id=_required_text(payload["goal_id"], f"{path}.goal_id"),
            desired_state=desired_state,
            depends_on=_text_array(payload["depends_on"], f"{path}.depends_on"),
            valid_while=_predicate_array(
                payload["valid_while"], f"{path}.valid_while"
            ),
            completion_policy=completion_policy,
            reobserve_after=reobserve_after,
            rationale=_required_text(payload["rationale"], f"{path}.rationale"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "desired_state": [item.to_dict() for item in self.desired_state],
            "depends_on": list(self.depends_on),
            "valid_while": [item.to_dict() for item in self.valid_while],
            "completion_policy": self.completion_policy,
            "reobserve_after": self.reobserve_after,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class WorldGoalGraphEntityScope:
    """The reasoning model's task-scope decision for one observed entity."""

    entity_id: str
    status: str
    reason: str

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        path: str,
    ) -> "WorldGoalGraphEntityScope":
        if not isinstance(payload, Mapping):
            raise WorldIntentValidationError(f"{path} must be an object")
        allowed = {"entity_id", "status", "reason"}
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldIntentValidationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldIntentValidationError(
                f"{path} is missing required fields: {sorted(missing)}"
            )
        status = _required_text(payload["status"], f"{path}.status")
        if status not in ENTITY_SCOPE_STATUSES:
            raise WorldIntentValidationError(
                f"{path}.status has unsupported value {status!r}"
            )
        return cls(
            entity_id=_required_text(payload["entity_id"], f"{path}.entity_id"),
            status=status,
            reason=_required_text(payload["reason"], f"{path}.reason"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "entity_id": self.entity_id,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorldGoalGraph:
    """A validated causal graph of desired world-state outcomes."""

    graph_id: str
    status: str
    root_goal_ids: tuple[str, ...]
    goals: tuple[WorldGoalNode, ...]
    entity_scope: tuple[WorldGoalGraphEntityScope, ...]
    constraints: tuple[WorldPredicate, ...]
    required_observations: tuple[WorldPredicate, ...]
    confidence: float
    reason: str
    schema_version: str = WORLD_GOAL_GRAPH_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WorldGoalGraph":
        if not isinstance(payload, Mapping):
            raise WorldIntentValidationError("world goal graph must be an object")
        allowed = {
            "schema_version",
            "graph_id",
            "status",
            "root_goal_ids",
            "goals",
            "entity_scope",
            "constraints",
            "required_observations",
            "confidence",
            "reason",
        }
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise WorldIntentValidationError(
                f"world goal graph contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise WorldIntentValidationError(
                f"world goal graph is missing required fields: {sorted(missing)}"
            )
        version = _required_text(payload["schema_version"], "schema_version")
        if version != WORLD_GOAL_GRAPH_SCHEMA_VERSION:
            raise WorldIntentValidationError(
                f"unsupported schema_version {version!r}; "
                f"expected {WORLD_GOAL_GRAPH_SCHEMA_VERSION!r}"
            )
        status = _required_text(payload["status"], "status")
        if status not in WORLD_GOAL_GRAPH_STATUSES:
            raise WorldIntentValidationError(f"unsupported graph status {status!r}")
        raw_goals = payload["goals"]
        if not isinstance(raw_goals, list):
            raise WorldIntentValidationError("goals must be an array")
        if len(raw_goals) > MAX_GOAL_NODES:
            raise WorldIntentValidationError(
                f"goals may contain at most {MAX_GOAL_NODES} nodes"
            )
        goals = tuple(
            WorldGoalNode.from_mapping(item, f"goals[{index}]")
            for index, item in enumerate(raw_goals)
        )
        goal_ids = tuple(goal.goal_id for goal in goals)
        if len(goal_ids) != len(set(goal_ids)):
            raise WorldIntentValidationError("goal_id values must be unique")
        root_goal_ids = _text_array(payload["root_goal_ids"], "root_goal_ids")
        cls._validate_graph(status, goals, root_goal_ids)
        raw_entity_scope = payload["entity_scope"]
        if not isinstance(raw_entity_scope, list):
            raise WorldIntentValidationError("entity_scope must be an array")
        if len(raw_entity_scope) > 256:
            raise WorldIntentValidationError(
                "entity_scope may contain at most 256 decisions"
            )
        entity_scope = tuple(
            WorldGoalGraphEntityScope.from_mapping(
                item,
                f"entity_scope[{index}]",
            )
            for index, item in enumerate(raw_entity_scope)
        )
        scope_entity_ids = tuple(item.entity_id for item in entity_scope)
        if len(scope_entity_ids) != len(set(scope_entity_ids)):
            raise WorldIntentValidationError(
                "entity_scope entity_id values must be unique"
            )
        required_observations = _predicate_array(
            payload["required_observations"], "required_observations"
        )
        if status == "needs_observation" and not required_observations:
            raise WorldIntentValidationError(
                "needs_observation requires at least one required observation"
            )
        return cls(
            graph_id=_required_text(payload["graph_id"], "graph_id"),
            status=status,
            root_goal_ids=root_goal_ids,
            goals=goals,
            entity_scope=entity_scope,
            constraints=_predicate_array(payload["constraints"], "constraints"),
            required_observations=required_observations,
            confidence=_finite_confidence(payload["confidence"], "confidence"),
            reason=_required_text(payload["reason"], "reason"),
            schema_version=version,
        )

    @staticmethod
    def _validate_graph(
        status: str,
        goals: tuple[WorldGoalNode, ...],
        root_goal_ids: tuple[str, ...],
    ) -> None:
        by_id = {goal.goal_id: goal for goal in goals}
        if status in {"ready", "complete"} and (not goals or not root_goal_ids):
            raise WorldIntentValidationError(
                f"status {status!r} requires goals and root_goal_ids"
            )
        unknown_roots = set(root_goal_ids) - set(by_id)
        if unknown_roots:
            raise WorldIntentValidationError(
                f"root_goal_ids reference unknown goals: {sorted(unknown_roots)}"
            )
        for goal in goals:
            unknown = set(goal.depends_on) - set(by_id)
            if unknown:
                raise WorldIntentValidationError(
                    f"goal {goal.goal_id!r} depends on unknown goals: {sorted(unknown)}"
                )
            if goal.goal_id in goal.depends_on:
                raise WorldIntentValidationError(
                    f"goal {goal.goal_id!r} cannot depend on itself"
                )

        visit_state: dict[str, int] = {}

        def visit(goal_id: str) -> None:
            state = visit_state.get(goal_id, 0)
            if state == 1:
                raise WorldIntentValidationError("goal dependency graph contains a cycle")
            if state == 2:
                return
            visit_state[goal_id] = 1
            for dependency in by_id[goal_id].depends_on:
                visit(dependency)
            visit_state[goal_id] = 2

        for goal_id in by_id:
            visit(goal_id)
        if root_goal_ids:
            reachable: set[str] = set()

            def mark(goal_id: str) -> None:
                if goal_id in reachable:
                    return
                reachable.add(goal_id)
                for dependency in by_id[goal_id].depends_on:
                    mark(dependency)

            for root_goal_id in root_goal_ids:
                mark(root_goal_id)
            orphaned = set(by_id) - reachable
            if orphaned:
                raise WorldIntentValidationError(
                    f"goals are not causal prerequisites of a root: {sorted(orphaned)}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "status": self.status,
            "root_goal_ids": list(self.root_goal_ids),
            "goals": [goal.to_dict() for goal in self.goals],
            "entity_scope": [item.to_dict() for item in self.entity_scope],
            "constraints": [item.to_dict() for item in self.constraints],
            "required_observations": [
                item.to_dict() for item in self.required_observations
            ],
            "confidence": self.confidence,
            "reason": self.reason,
        }


def parse_world_goal_graph_json(text: str) -> WorldGoalGraph:
    if not isinstance(text, str) or not text.strip():
        raise WorldIntentValidationError(
            "world goal graph response must be non-empty JSON"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise WorldIntentValidationError(
            f"invalid world goal graph JSON: {error.msg}"
        ) from error
    return WorldGoalGraph.from_mapping(payload)


def _predicate_schema() -> dict[str, Any]:
    return {
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


def world_goal_graph_json_schema() -> dict[str, Any]:
    predicate = _predicate_schema()
    goal = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "goal_id",
            "desired_state",
            "depends_on",
            "valid_while",
            "completion_policy",
            "reobserve_after",
            "rationale",
        ],
        "properties": {
            "goal_id": {"type": "string", "minLength": 1},
            "desired_state": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": predicate,
            },
            "depends_on": {
                "type": "array",
                "maxItems": MAX_GOAL_NODES,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "valid_while": {
                "type": "array",
                "maxItems": 32,
                "items": predicate,
            },
            "completion_policy": {"enum": sorted(GOAL_COMPLETION_POLICIES)},
            "reobserve_after": {"enum": sorted(REOBSERVE_POLICIES)},
            "rationale": {"type": "string", "minLength": 1},
        },
    }
    entity_scope = {
        "type": "object",
        "additionalProperties": False,
        "required": ["entity_id", "status", "reason"],
        "properties": {
            "entity_id": {"type": "string", "minLength": 1},
            "status": {"enum": sorted(ENTITY_SCOPE_STATUSES)},
            "reason": {"type": "string", "minLength": 1},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WorldGoalGraph",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "graph_id",
            "status",
            "root_goal_ids",
            "goals",
            "entity_scope",
            "constraints",
            "required_observations",
            "confidence",
            "reason",
        ],
        "properties": {
            "schema_version": {"const": WORLD_GOAL_GRAPH_SCHEMA_VERSION},
            "graph_id": {"type": "string", "minLength": 1},
            "status": {"enum": sorted(WORLD_GOAL_GRAPH_STATUSES)},
            "root_goal_ids": {
                "type": "array",
                "maxItems": MAX_GOAL_NODES,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "goals": {
                "type": "array",
                "maxItems": MAX_GOAL_NODES,
                "items": goal,
            },
            "entity_scope": {
                "type": "array",
                "maxItems": 256,
                "items": entity_scope,
            },
            "constraints": {
                "type": "array",
                "maxItems": 32,
                "items": predicate,
            },
            "required_observations": {
                "type": "array",
                "maxItems": 32,
                "items": predicate,
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1},
        },
    }


_GEOMETRY_KEYS = (
    "center_base_m",
    "visible_aabb_min_base_m",
    "visible_aabb_max_base_m",
    "visible_extent_base_m",
    "principal_axes_base",
    "principal_spreads_m",
    "oriented_footprint_axes_base",
    "oriented_footprint_extents_m",
    "support_plane_normal_base",
)


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldIntentValidationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldIntentValidationError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _json_copy(item, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        return {
            _required_text(key, f"{path}.key"): _json_copy(
                item, f"{path}.{key}", depth=depth + 1
            )
            for key, item in value.items()
        }
    if hasattr(value, "tolist"):
        return _json_copy(value.tolist(), path, depth=depth + 1)
    raise WorldIntentValidationError(f"{path} must be JSON-compatible")


def semantic_scene_inventory_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Expose all observed geometry through stable, task-neutral entity ids."""
    if not isinstance(state, Mapping):
        raise WorldIntentValidationError("state must be an object")
    rgbd = state.get("rgbd_scene_geometry")
    if not isinstance(rgbd, Mapping):
        rgbd = {}
    raw_geometries = rgbd.get("geometries", [])
    if not isinstance(raw_geometries, list):
        raise WorldIntentValidationError(
            "state.rgbd_scene_geometry.geometries must be an array"
        )
    entities: dict[str, dict[str, Any]] = {
        "observed_scene": {
            "entity_id": "observed_scene",
            "label": "observed scene",
            "observation_status": "scope",
            "geometry": {},
        }
    }
    for index, raw in enumerate(raw_geometries):
        if not isinstance(raw, Mapping):
            raise WorldIntentValidationError(
                f"state.rgbd_scene_geometry.geometries[{index}] must be an object"
            )
        entity_id = _required_text(
            raw.get("runtime_id"),
            f"state.rgbd_scene_geometry.geometries[{index}].runtime_id",
        )
        if entity_id in entities:
            raise WorldIntentValidationError(
                f"duplicate semantic scene entity id {entity_id!r}"
            )
        geometry = {
            key: _json_copy(raw[key], f"entities.{entity_id}.geometry.{key}")
            for key in _GEOMETRY_KEYS
            if key in raw
        }
        entities[entity_id] = {
            "entity_id": entity_id,
            "label": entity_id.replace("_", " "),
            "observation_status": "visible_rgbd",
            "geometry": geometry,
        }

    role_bindings: list[dict[str, str]] = []
    raw_roles = state.get("scene_roles", {})
    if isinstance(raw_roles, Mapping):
        for role_id, raw_role in sorted(raw_roles.items()):
            if not isinstance(raw_role, Mapping):
                continue
            entity_id = raw_role.get("asset")
            if not isinstance(entity_id, str) or not entity_id.strip():
                continue
            entity_id = entity_id.strip()
            label = raw_role.get("label")
            if not isinstance(label, str) or not label.strip():
                label = entity_id.replace("_", " ")
            label = label.strip()
            role_bindings.append(
                {
                    "role_id": _required_text(role_id, "scene_roles.role_id"),
                    "entity_id": entity_id,
                }
            )
            if entity_id in entities:
                entities[entity_id]["label"] = label
            else:
                entities[entity_id] = {
                    "entity_id": entity_id,
                    "label": label,
                    "observation_status": "role_bound_not_visible",
                    "geometry": {},
                }

    raw_physical_evidence = state.get("entity_physical_evidence", {})
    if raw_physical_evidence is None:
        raw_physical_evidence = {}
    if not isinstance(raw_physical_evidence, Mapping):
        raise WorldIntentValidationError(
            "state.entity_physical_evidence must be an object"
        )
    for raw_entity_id, raw_evidence in raw_physical_evidence.items():
        entity_id = _required_text(
            raw_entity_id, "state.entity_physical_evidence key"
        )
        if entity_id not in entities:
            raise WorldIntentValidationError(
                "physical evidence references an entity absent from the current "
                f"semantic inventory: {entity_id!r}"
            )
        if not isinstance(raw_evidence, Mapping):
            raise WorldIntentValidationError(
                f"state.entity_physical_evidence.{entity_id} must be an object"
            )
        evidence_entity_id = raw_evidence.get("entity_id")
        if evidence_entity_id != entity_id:
            raise WorldIntentValidationError(
                f"physical evidence entity_id mismatch for {entity_id!r}"
            )
        entities[entity_id]["physical_evidence"] = _json_copy(
            raw_evidence,
            f"entities.{entity_id}.physical_evidence",
        )

    return {
        "schema_version": SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION,
        "available": bool(rgbd.get("available") and raw_geometries),
        "source": str(rgbd.get("source", "runtime_scene_state")),
        "frame": str(rgbd.get("frame", "unknown")),
        "entities": [entities[key] for key in sorted(entities)],
        "role_bindings": role_bindings,
        "limitations": [
            "entities without live physics evidence retain unknown mobility and mass",
            "visible exterior geometry provides only a destination-capacity upper bound",
            "physics metadata does not prove fragility or a collision-free manipulation path",
            "occluded entities may be absent from this snapshot",
        ],
    }


def semantic_scene_inventory_entity_ids(
    inventory: Mapping[str, Any],
) -> frozenset[str]:
    entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(entities, list):
        raise WorldIntentValidationError("scene inventory entities must be an array")
    result: set[str] = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, Mapping):
            raise WorldIntentValidationError(
                f"scene inventory entities[{index}] must be an object"
            )
        entity_id = _required_text(
            entity.get("entity_id"), f"scene inventory entities[{index}].entity_id"
        )
        if entity_id in result:
            raise WorldIntentValidationError(
                f"duplicate scene inventory entity id {entity_id!r}"
            )
        result.add(entity_id)
    return frozenset(result)


def _graph_predicates(graph: WorldGoalGraph) -> Iterable[WorldPredicate]:
    yield from graph.constraints
    yield from graph.required_observations
    for goal in graph.goals:
        yield from goal.desired_state
        yield from goal.valid_while


def validate_world_goal_graph_entity_references(
    graph: WorldGoalGraph,
    inventory: Mapping[str, Any],
) -> None:
    """Fail closed when a graph invents entities absent from fresh perception."""
    entity_ids = semantic_scene_inventory_entity_ids(inventory)
    unknown: set[str] = set()
    for predicate in _graph_predicates(graph):
        if predicate.subject_id not in entity_ids:
            unknown.add(predicate.subject_id)
        if predicate.reference_id is not None and predicate.reference_id not in entity_ids:
            unknown.add(predicate.reference_id)
    if unknown:
        raise WorldIntentValidationError(
            f"world goal graph references entities absent from inventory: {sorted(unknown)}"
        )


def validate_world_goal_graph_revision(
    previous_graph: WorldGoalGraph,
    revised_graph: WorldGoalGraph,
    evidence_blocked_goal_ids: Sequence[str],
    *,
    preserve_included_entity_ids: Sequence[str] | None = None,
) -> None:
    """Fail closed when a revision hides previously included blocked outcomes."""
    if revised_graph.graph_id == previous_graph.graph_id:
        raise WorldIntentValidationError(
            "revised world goal graph must use a fresh graph_id"
        )
    previous_goals = {goal.goal_id: goal for goal in previous_graph.goals}
    unknown_blocked = set(evidence_blocked_goal_ids) - set(previous_goals)
    if unknown_blocked:
        raise WorldIntentValidationError(
            "revision blocker context references unknown previous goals: "
            f"{sorted(unknown_blocked)}"
        )
    previous_included = (
        set(preserve_included_entity_ids)
        if preserve_included_entity_ids is not None
        else {
            item.entity_id
            for item in previous_graph.entity_scope
            if item.status == "included"
        }
    )
    revised_scope = {item.entity_id: item.status for item in revised_graph.entity_scope}
    hidden_included = sorted(
        entity_id
        for entity_id in previous_included
        if revised_scope.get(entity_id) != "included"
    )
    if hidden_included:
        raise WorldIntentValidationError(
            "revised world goal graph cannot hide previously included entities: "
            f"{hidden_included}"
        )
    blocked_subject_ids = {
        predicate.subject_id
        for goal_id in evidence_blocked_goal_ids
        for predicate in previous_goals[goal_id].desired_state
    }
    revised_subject_ids = {
        predicate.subject_id for predicate in _graph_predicates(revised_graph)
    }
    missing_blocked_subjects = sorted(blocked_subject_ids - revised_subject_ids)
    if missing_blocked_subjects:
        raise WorldIntentValidationError(
            "revised world goal graph must preserve unresolved blocked subjects: "
            f"{missing_blocked_subjects}"
        )
    if evidence_blocked_goal_ids and revised_graph.status == "complete":
        raise WorldIntentValidationError(
            "revised world goal graph cannot claim complete while blocker evidence "
            "remains unresolved"
        )


def build_world_goal_graph_prompt(
    instruction: str,
    inventory: Mapping[str, Any],
    predicate_evaluator_advertisement: Mapping[str, Any] | None = None,
    *,
    revision_context: Mapping[str, Any] | None = None,
) -> str:
    """Request a causal outcome graph grounded only in the supplied inventory."""
    instruction = _required_text(instruction, "instruction")
    entity_ids = sorted(semantic_scene_inventory_entity_ids(inventory))
    inventory_json = json.dumps(_json_copy(inventory, "inventory"), indent=2)
    evaluators_json = json.dumps(
        _json_copy(
            predicate_evaluator_advertisement or {
                "completion_requires_advertised_evaluator": False,
                "evaluators": [],
            },
            "predicate_evaluator_advertisement",
        ),
        indent=2,
    )
    schema_json = json.dumps(world_goal_graph_json_schema(), indent=2, sort_keys=True)
    revision_instructions = ""
    if revision_context is not None:
        revision_json = json.dumps(
            _json_copy(revision_context, "revision_context"),
            indent=2,
        )
        revision_instructions = f"""
This is a bounded revision because the previous graph failed the supplied
evidence gate. The previous graph and exact runtime blocker evidence or
scope-audit evidence are:
{revision_json}

Return a complete replacement graph, not a patch, and use a fresh graph_id.
Preserve complete scene membership coverage: every inventory entity still
requires exactly one entity_scope decision. Do not remove an entity, silently
exclude it, or claim its outcome is satisfied merely because the supplied
evidence blocks a capability. Keep each unresolved outcome explicit or request
the observation needed to resolve it.

When one goal combines independent world-state changes and evidence blocks
only some of them, separate those changes into independently activatable goals.
Use dependencies only when one outcome is a real causal prerequisite for
another. This lets an independently feasible outcome remain available without
misrepresenting a blocked outcome. Choose a different desired relation or
reference only when the fresh inventory and an advertised evaluator support
it; do not invent an unobserved destination or suppress blocker evidence.

When the evidence contains a task-membership audit, preserve its exact entity
scope decisions in the replacement graph. Membership describes whether the
human outcome covers an entity and remains independent of physical feasibility;
size, mass, reachability, capacity, or provider availability may block a goal
later but cannot reclassify a covered entity as context or excluded.
"""
    return f"""Translate the instruction and attached fresh observation into a causal
graph of observable world-state outcomes.

Instruction:
{instruction}

Fresh semantic scene inventory:
{inventory_json}

Use only these stable entity identifiers in predicate subject_id and
reference_id fields:
{json.dumps(entity_ids)}

The inventory's role_bindings are observation hints from the current scene
configuration, not a limit on which entities belong to the instruction. A
collective instruction must consider every relevant observed entity.

Provide exactly one entity_scope decision for every stable entity identifier.
Use included when the desired outcome applies to an entity as a task member,
context when it is a surface, destination, observation scope, or other relevant
scene structure that should remain, excluded only when it is confidently
outside the instruction, and unknown when its task membership is uncertain.
Explain every decision. Any unknown scope requires status
needs_observation; do not silently omit visible entities from a collective task.

Runtime-advertised world-predicate evaluators:
{evaluators_json}

When completion_requires_advertised_evaluator is true, every predicate in
desired_state, valid_while, and constraints must match a supported predicate
form from a completion-authority evaluator. Do not invent a predicate evaluator.
If an essential outcome cannot be expressed through an advertised form, set
status to needs_observation and describe the missing measurable relation in
required_observations rather than claiming the graph is ready.

Each goal must describe a testable desired world state. Use depends_on only for
causal prerequisites: for example, an inaccessible entity may depend on a
blocking entity no longer restricting access. Put conditions that must remain
true while pursuing a goal in valid_while. Put task-wide conditions in
constraints. Express missing evidence as required_observations and set status
to needs_observation instead of guessing.

For a collective instruction whose membership can change, make the root outcome
re-evaluable from a future scene observation. Current member goals may be
included as causal prerequisites, but completing only the initially enumerated
members must not falsely complete the collective instruction. Do not assume
that every visible entity is eligible, movable, safe, or compatible with the
destination when the inventory does not establish that fact.
{revision_instructions}

Describe outcomes only. Do not name or select mechanisms, body parts,
controllers, trajectories, or motor commands. The graph has no execution
authority. Return exactly one JSON object matching this schema, with no
Markdown:
{schema_json}
"""
