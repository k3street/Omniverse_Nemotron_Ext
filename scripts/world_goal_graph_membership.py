"""Fresh-scene scope admission and membership leases for world goal graphs.

The task reasoner accounts for every observed entity.  This module verifies
that coverage and binds a graph to the entity membership of one fresh semantic
inventory.  The lease expires on membership change or goal completion, forcing
fresh task reasoning without encoding a task or embodiment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

try:
    from .world_goal_graph_contract import (
        WorldGoalGraph,
        semantic_scene_inventory_entity_ids,
    )
    from .world_intent_contract import WorldPredicate
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_goal_graph_contract import (  # type: ignore[no-redef]
        WorldGoalGraph,
        semantic_scene_inventory_entity_ids,
    )
    from world_intent_contract import WorldPredicate  # type: ignore[no-redef]


class SceneMembershipLeaseError(ValueError):
    """Raised when task scope cannot be bound to a fresh scene inventory."""


def _inventory_statuses(inventory: Mapping[str, Any]) -> dict[str, str]:
    raw_entities = inventory.get("entities") if isinstance(inventory, Mapping) else None
    if not isinstance(raw_entities, list):
        raise SceneMembershipLeaseError("inventory entities must be an array")
    result: dict[str, str] = {}
    for index, entity in enumerate(raw_entities):
        if not isinstance(entity, Mapping):
            raise SceneMembershipLeaseError(
                f"inventory entities[{index}] must be an object"
            )
        entity_id = entity.get("entity_id")
        status = entity.get("observation_status")
        if not isinstance(entity_id, str) or not entity_id:
            raise SceneMembershipLeaseError(
                f"inventory entities[{index}].entity_id must be non-empty"
            )
        if not isinstance(status, str) or not status:
            raise SceneMembershipLeaseError(
                f"inventory entities[{index}].observation_status must be non-empty"
            )
        if entity_id in result:
            raise SceneMembershipLeaseError(
                f"duplicate inventory entity id {entity_id!r}"
            )
        result[entity_id] = status
    return result


def _verified_temporal_occlusion(entity: Mapping[str, Any]) -> bool:
    """Accept only bounded, non-geometric runtime occlusion evidence."""
    if entity.get("observation_status") != "temporarily_occluded_rgbd":
        return False
    geometry = entity.get("geometry")
    evidence = entity.get("temporal_presence_evidence")
    if not isinstance(geometry, Mapping) or geometry:
        return False
    if not isinstance(evidence, Mapping):
        return False
    missed = evidence.get("missed_observations")
    maximum = evidence.get("maximum_missed_observations")
    independently_present = evidence.get("independently_present")
    if (
        isinstance(missed, bool)
        or not isinstance(missed, int)
        or missed < 1
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum < 0
        or not isinstance(independently_present, bool)
    ):
        return False
    return bool(
        evidence.get("schema_version") == "temporal-scene-inventory.v1"
        and evidence.get("fresh_rgbd_geometry_available") is False
        and evidence.get("cached_geometry_exposed") is False
        and evidence.get("completion_evidence") is False
        and evidence.get("execution_authority") is False
        and (independently_present or missed <= maximum)
    )


def scene_membership_fingerprint(inventory: Mapping[str, Any]) -> str:
    """Hash membership and visibility state, deliberately excluding live pose."""
    statuses = _inventory_statuses(inventory)
    role_bindings = inventory.get("role_bindings", [])
    if not isinstance(role_bindings, list):
        raise SceneMembershipLeaseError("inventory role_bindings must be an array")
    normalized_roles: list[dict[str, str]] = []
    for index, binding in enumerate(role_bindings):
        if not isinstance(binding, Mapping):
            raise SceneMembershipLeaseError(
                f"inventory role_bindings[{index}] must be an object"
            )
        role_id = binding.get("role_id")
        entity_id = binding.get("entity_id")
        if not isinstance(role_id, str) or not role_id:
            raise SceneMembershipLeaseError("role binding role_id must be non-empty")
        if not isinstance(entity_id, str) or not entity_id:
            raise SceneMembershipLeaseError("role binding entity_id must be non-empty")
        normalized_roles.append({"role_id": role_id, "entity_id": entity_id})
    payload = {
        "schema_version": inventory.get("schema_version"),
        "entities": [
            {"entity_id": entity_id, "observation_status": statuses[entity_id]}
            for entity_id in sorted(statuses)
        ],
        "role_bindings": sorted(
            normalized_roles,
            key=lambda item: (item["role_id"], item["entity_id"]),
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:16]


def _executable_predicates(graph: WorldGoalGraph) -> tuple[WorldPredicate, ...]:
    predicates: list[WorldPredicate] = list(graph.constraints)
    for goal in graph.goals:
        predicates.extend(goal.desired_state)
        predicates.extend(goal.valid_while)
    return tuple(predicates)


@dataclass(frozen=True)
class WorldGoalGraphSceneScopeAdmission:
    admitted: bool
    resolved_subset_admitted: bool
    scope_resolution_complete: bool
    inventory_fingerprint: str
    inventory_entity_ids: tuple[str, ...]
    included_entity_ids: tuple[str, ...]
    context_entity_ids: tuple[str, ...]
    excluded_entity_ids: tuple[str, ...]
    unknown_entity_ids: tuple[str, ...]
    missing_scope_entity_ids: tuple[str, ...]
    extra_scope_entity_ids: tuple[str, ...]
    predicate_scope_conflicts: tuple[str, ...]
    included_without_graph_relation: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "resolved_subset_admitted": self.resolved_subset_admitted,
            "scope_resolution_complete": self.scope_resolution_complete,
            "inventory_fingerprint": self.inventory_fingerprint,
            "inventory_entity_ids": list(self.inventory_entity_ids),
            "included_entity_ids": list(self.included_entity_ids),
            "context_entity_ids": list(self.context_entity_ids),
            "excluded_entity_ids": list(self.excluded_entity_ids),
            "unknown_entity_ids": list(self.unknown_entity_ids),
            "deferred_unknown_entity_ids": list(self.unknown_entity_ids),
            "task_completion_allowed": self.scope_resolution_complete,
            "missing_scope_entity_ids": list(self.missing_scope_entity_ids),
            "extra_scope_entity_ids": list(self.extra_scope_entity_ids),
            "predicate_scope_conflicts": list(self.predicate_scope_conflicts),
            "included_without_graph_relation": list(
                self.included_without_graph_relation
            ),
            "authority": "fresh_semantic_scene_membership",
        }


def assess_world_goal_graph_scene_scope(
    graph: WorldGoalGraph,
    inventory: Mapping[str, Any],
) -> WorldGoalGraphSceneScopeAdmission:
    """Require an explicit, consistent scope decision for every fresh entity."""
    if not isinstance(graph, WorldGoalGraph):
        raise SceneMembershipLeaseError("graph must be a WorldGoalGraph")
    inventory_ids = semantic_scene_inventory_entity_ids(inventory)
    by_scope = {item.entity_id: item for item in graph.entity_scope}
    scope_ids = set(by_scope)
    missing = sorted(inventory_ids - scope_ids)
    extra = sorted(scope_ids - inventory_ids)
    included = sorted(
        entity_id
        for entity_id, decision in by_scope.items()
        if decision.status == "included"
    )
    context = sorted(
        entity_id
        for entity_id, decision in by_scope.items()
        if decision.status == "context"
    )
    excluded = sorted(
        entity_id
        for entity_id, decision in by_scope.items()
        if decision.status == "excluded"
    )
    unknown = sorted(
        entity_id
        for entity_id, decision in by_scope.items()
        if decision.status == "unknown"
    )
    used_entity_ids: set[str] = set()
    conflicts: set[str] = set()
    for predicate in _executable_predicates(graph):
        used_entity_ids.add(predicate.subject_id)
        if predicate.reference_id is not None:
            used_entity_ids.add(predicate.reference_id)
        for entity_id in (predicate.subject_id, predicate.reference_id):
            if entity_id is None:
                continue
            decision = by_scope.get(entity_id)
            if decision is not None and decision.status in {"excluded", "unknown"}:
                conflicts.add(entity_id)
    included_without_relation = sorted(set(included) - used_entity_ids)
    if graph.status not in {"ready", "complete"}:
        included_without_relation = []
    admitted = not any(
        (
            missing,
            extra,
            unknown,
            conflicts,
            included_without_relation,
        )
    )
    required_observation_entity_ids = {
        entity_id
        for predicate in graph.required_observations
        for entity_id in (predicate.subject_id, predicate.reference_id)
        if entity_id is not None
    }
    resolved_subset_admitted = bool(
        not admitted
        and unknown
        and graph.status == "needs_observation"
        and set(unknown).issubset(required_observation_entity_ids)
        and not any((missing, extra, conflicts, included_without_relation))
        and used_entity_ids
    )
    return WorldGoalGraphSceneScopeAdmission(
        admitted=admitted,
        resolved_subset_admitted=resolved_subset_admitted,
        scope_resolution_complete=not unknown,
        inventory_fingerprint=scene_membership_fingerprint(inventory),
        inventory_entity_ids=tuple(sorted(inventory_ids)),
        included_entity_ids=tuple(included),
        context_entity_ids=tuple(context),
        excluded_entity_ids=tuple(excluded),
        unknown_entity_ids=tuple(unknown),
        missing_scope_entity_ids=tuple(missing),
        extra_scope_entity_ids=tuple(extra),
        predicate_scope_conflicts=tuple(sorted(conflicts)),
        included_without_graph_relation=tuple(included_without_relation),
    )


@dataclass(frozen=True)
class SceneMembershipLeaseAssessment:
    valid: bool
    reasons: tuple[str, ...]
    baseline_fingerprint: str
    current_fingerprint: str
    added_entity_ids: tuple[str, ...]
    removed_entity_ids: tuple[str, ...]
    observation_status_changes: tuple[Mapping[str, str], ...]
    transient_occlusion_status_changes: tuple[Mapping[str, str], ...]
    completed_goal_id: str | None
    task_completion_requested: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "baseline_fingerprint": self.baseline_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "added_entity_ids": list(self.added_entity_ids),
            "removed_entity_ids": list(self.removed_entity_ids),
            "observation_status_changes": [
                dict(item) for item in self.observation_status_changes
            ],
            "transient_occlusion_status_changes": [
                dict(item) for item in self.transient_occlusion_status_changes
            ],
            "temporarily_occluded_entity_ids": [
                item["entity_id"]
                for item in self.transient_occlusion_status_changes
            ],
            "membership_preserved_by_temporal_evidence": bool(
                self.transient_occlusion_status_changes
            ),
            "completed_goal_id": self.completed_goal_id,
            "task_completion_requested": self.task_completion_requested,
        }


@dataclass(frozen=True)
class SceneMembershipLease:
    """Bind one graph to scene membership until any goal or membership changes."""

    lease_id: str
    graph_id: str
    graph_status: str
    inventory_fingerprint: str
    entity_statuses: Mapping[str, str]
    deferred_unknown_entity_ids: tuple[str, ...] = ()
    replan_after_goal_completion: bool = True
    fresh_graph_required_before_task_completion: bool = True

    @classmethod
    def issue(
        cls,
        graph: WorldGoalGraph,
        inventory: Mapping[str, Any],
        admission: WorldGoalGraphSceneScopeAdmission | None = None,
    ) -> "SceneMembershipLease":
        admission = admission or assess_world_goal_graph_scene_scope(graph, inventory)
        if not (admission.admitted or admission.resolved_subset_admitted):
            raise SceneMembershipLeaseError(
                "cannot issue a membership lease for an unadmitted scene scope"
            )
        fingerprint = scene_membership_fingerprint(inventory)
        lease_seed = f"{graph.graph_id}:{fingerprint}".encode("utf-8")
        return cls(
            lease_id="scene-membership:" + hashlib.sha256(lease_seed).hexdigest()[:16],
            graph_id=graph.graph_id,
            graph_status=graph.status,
            inventory_fingerprint=fingerprint,
            entity_statuses=_inventory_statuses(inventory),
            deferred_unknown_entity_ids=admission.unknown_entity_ids,
        )

    def assess(
        self,
        current_inventory: Mapping[str, Any],
        *,
        completed_goal_id: str | None = None,
        task_completion_requested: bool = False,
    ) -> SceneMembershipLeaseAssessment:
        current_statuses = _inventory_statuses(current_inventory)
        current_entities = {
            str(item["entity_id"]): item
            for item in current_inventory.get("entities", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("entity_id"), str)
        }
        baseline_ids = set(self.entity_statuses)
        current_ids = set(current_statuses)
        added = tuple(sorted(current_ids - baseline_ids))
        removed = tuple(sorted(baseline_ids - current_ids))
        all_status_changes = tuple(
            {
                "entity_id": entity_id,
                "before": self.entity_statuses[entity_id],
                "after": current_statuses[entity_id],
            }
            for entity_id in sorted(baseline_ids & current_ids)
            if self.entity_statuses[entity_id] != current_statuses[entity_id]
        )
        transient_occlusion_status_changes = tuple(
            item
            for item in all_status_changes
            if _verified_temporal_occlusion(
                current_entities.get(item["entity_id"], {})
            )
        )
        status_changes = tuple(
            item
            for item in all_status_changes
            if not _verified_temporal_occlusion(
                current_entities.get(item["entity_id"], {})
            )
        )
        reasons: list[str] = []
        if added:
            reasons.append("scene_entity_added")
        if removed:
            reasons.append("scene_entity_removed")
        if status_changes:
            reasons.append("scene_entity_observation_status_changed")
        if completed_goal_id is not None and self.replan_after_goal_completion:
            reasons.append("goal_completion_requires_fresh_graph")
        if (
            task_completion_requested
            and self.fresh_graph_required_before_task_completion
            and self.graph_status != "complete"
        ):
            reasons.append("task_completion_requires_fresh_complete_graph")
        if task_completion_requested and self.deferred_unknown_entity_ids:
            reasons.append("task_completion_requires_resolved_scope")
        return SceneMembershipLeaseAssessment(
            valid=not reasons,
            reasons=tuple(reasons),
            baseline_fingerprint=self.inventory_fingerprint,
            current_fingerprint=scene_membership_fingerprint(current_inventory),
            added_entity_ids=added,
            removed_entity_ids=removed,
            observation_status_changes=status_changes,
            transient_occlusion_status_changes=(
                transient_occlusion_status_changes
            ),
            completed_goal_id=completed_goal_id,
            task_completion_requested=task_completion_requested,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "graph_id": self.graph_id,
            "graph_status": self.graph_status,
            "inventory_fingerprint": self.inventory_fingerprint,
            "entity_statuses": dict(sorted(self.entity_statuses.items())),
            "deferred_unknown_entity_ids": list(
                self.deferred_unknown_entity_ids
            ),
            "scope_resolution_complete": not self.deferred_unknown_entity_ids,
            "task_completion_allowed": (
                not self.deferred_unknown_entity_ids
                and self.graph_status == "complete"
            ),
            "replan_after_goal_completion": self.replan_after_goal_completion,
            "fresh_graph_required_before_task_completion": (
                self.fresh_graph_required_before_task_completion
            ),
            "authority": "fresh_semantic_scene_membership",
        }
