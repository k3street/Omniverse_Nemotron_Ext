"""Bounded temporal persistence for synchronized semantic scene inventories.

RGB-D instance geometry is intentionally strict: an occluded entity disappears
from a single snapshot.  Scene membership, however, represents stable world
identity rather than one camera's instantaneous visibility.  This module fuses
fresh inventory snapshots with runtime tracker presence and a bounded miss
window.  Persisted entities expose no cached geometry, so pose-dependent
predicates remain unknown until fresh RGB-D evidence returns.

The memory is task- and embodiment-neutral.  It never selects an entity, goal,
tool, motion, or actuator command and grants no execution authority.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping


TEMPORAL_SCENE_INVENTORY_SCHEMA_VERSION = "temporal-scene-inventory.v1"
TEMPORARILY_OCCLUDED_STATUS = "temporarily_occluded_rgbd"


class TemporalSceneInventoryError(ValueError):
    """Raised when temporal inventory evidence violates its contract."""


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 14:
        raise TemporalSceneInventoryError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TemporalSceneInventoryError(
                f"{path} contains non-finite data"
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
                raise TemporalSceneInventoryError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise TemporalSceneInventoryError(f"{path} must be JSON-compatible")


def _entity_map(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(inventory, Mapping):
        raise TemporalSceneInventoryError("inventory must be an object")
    raw_entities = inventory.get("entities")
    if not isinstance(raw_entities, list):
        raise TemporalSceneInventoryError("inventory.entities must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, raw_entity in enumerate(raw_entities):
        if not isinstance(raw_entity, Mapping):
            raise TemporalSceneInventoryError(
                f"inventory.entities[{index}] must be an object"
            )
        entity_id = raw_entity.get("entity_id")
        status = raw_entity.get("observation_status")
        if not isinstance(entity_id, str) or not entity_id:
            raise TemporalSceneInventoryError(
                f"inventory.entities[{index}].entity_id must be non-empty"
            )
        if not isinstance(status, str) or not status:
            raise TemporalSceneInventoryError(
                f"inventory.entities[{index}].observation_status must be non-empty"
            )
        if entity_id in result:
            raise TemporalSceneInventoryError(
                f"duplicate inventory entity id {entity_id!r}"
            )
        copied = _json_copy(raw_entity, f"inventory.entities[{index}]")
        if not isinstance(copied, dict):  # Defensive; mapping always copies to dict.
            raise TemporalSceneInventoryError("entity copy must be an object")
        result[entity_id] = copied
    return result


def _is_fresh_rgbd_entity(entity: Mapping[str, Any]) -> bool:
    return entity.get("observation_status") == "visible_rgbd"


def _occluded_entity(
    previous: Mapping[str, Any],
    *,
    missed_observations: int,
    maximum_missed_observations: int,
    independently_present: bool,
) -> dict[str, Any]:
    entity = deepcopy(dict(previous))
    entity["observation_status"] = TEMPORARILY_OCCLUDED_STATUS
    entity["geometry"] = {}
    entity.pop("physical_evidence", None)
    entity["temporal_presence_evidence"] = {
        "schema_version": TEMPORAL_SCENE_INVENTORY_SCHEMA_VERSION,
        "fresh_rgbd_geometry_available": False,
        "missed_observations": missed_observations,
        "maximum_missed_observations": maximum_missed_observations,
        "independently_present": independently_present,
        "presence_source": (
            "runtime_entity_pose_tracker"
            if independently_present
            else "bounded_rgbd_miss_window"
        ),
        "cached_geometry_exposed": False,
        "completion_evidence": False,
        "execution_authority": False,
    }
    return entity


@dataclass(frozen=True)
class TemporalSceneInventoryUpdate:
    """One fused inventory and an audit of how identities were retained."""

    sequence: int
    inventory: Mapping[str, Any]
    fresh_visible_entity_ids: tuple[str, ...]
    temporarily_occluded_entity_ids: tuple[str, ...]
    tracker_confirmed_entity_ids: tuple[str, ...]
    grace_retained_entity_ids: tuple[str, ...]
    expired_entity_ids: tuple[str, ...]
    newly_observed_entity_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEMPORAL_SCENE_INVENTORY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "fresh_visible_entity_ids": list(self.fresh_visible_entity_ids),
            "temporarily_occluded_entity_ids": list(
                self.temporarily_occluded_entity_ids
            ),
            "tracker_confirmed_entity_ids": list(
                self.tracker_confirmed_entity_ids
            ),
            "grace_retained_entity_ids": list(self.grace_retained_entity_ids),
            "expired_entity_ids": list(self.expired_entity_ids),
            "newly_observed_entity_ids": list(self.newly_observed_entity_ids),
            "stale_geometry_exposed": False,
            "completion_authority": False,
            "execution_authority": False,
        }


class TemporalSceneInventoryMemory:
    """Fuse stable entity identity across brief or tracker-confirmed occlusion."""

    def __init__(
        self,
        initial_inventory: Mapping[str, Any],
        *,
        maximum_missed_observations: int = 3,
    ) -> None:
        if (
            isinstance(maximum_missed_observations, bool)
            or not isinstance(maximum_missed_observations, int)
            or maximum_missed_observations < 0
        ):
            raise TemporalSceneInventoryError(
                "maximum_missed_observations must be a non-negative integer"
            )
        initial_entities = _entity_map(initial_inventory)
        self.maximum_missed_observations = maximum_missed_observations
        self._known_entities = deepcopy(initial_entities)
        self._missed_observations = {entity_id: 0 for entity_id in initial_entities}
        self._sequence = 0
        self._latest_update: TemporalSceneInventoryUpdate | None = None

    @property
    def known_entity_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._known_entities))

    @property
    def latest_update(self) -> TemporalSceneInventoryUpdate | None:
        return self._latest_update

    def update(
        self,
        current_inventory: Mapping[str, Any],
        *,
        independently_present_entity_ids: Iterable[str] = (),
    ) -> TemporalSceneInventoryUpdate:
        """Fuse one fresh inventory without ever reusing cached geometry."""
        current_entities = _entity_map(current_inventory)
        present_ids: set[str] = set()
        for raw_entity_id in independently_present_entity_ids:
            if not isinstance(raw_entity_id, str) or not raw_entity_id:
                raise TemporalSceneInventoryError(
                    "independently present entity ids must be non-empty strings"
                )
            present_ids.add(raw_entity_id)

        previous_known_ids = set(self._known_entities)
        newly_observed = set(current_entities) - previous_known_ids
        fused_entities: dict[str, dict[str, Any]] = {}
        visible_ids: set[str] = set()
        temporarily_occluded_ids: set[str] = set()
        tracker_confirmed_ids: set[str] = set()
        grace_retained_ids: set[str] = set()
        expired_ids: set[str] = set()

        for entity_id, current in current_entities.items():
            previous = self._known_entities.get(entity_id)
            if _is_fresh_rgbd_entity(current) or previous is None:
                fused_entities[entity_id] = deepcopy(current)
                self._known_entities[entity_id] = deepcopy(current)
                self._missed_observations[entity_id] = 0
                if _is_fresh_rgbd_entity(current):
                    visible_ids.add(entity_id)
                continue

            if _is_fresh_rgbd_entity(previous):
                missed = self._missed_observations.get(entity_id, 0) + 1
                independently_present = entity_id in present_ids
                if independently_present or missed <= self.maximum_missed_observations:
                    fused_entities[entity_id] = _occluded_entity(
                        previous,
                        missed_observations=missed,
                        maximum_missed_observations=(
                            self.maximum_missed_observations
                        ),
                        independently_present=independently_present,
                    )
                    temporarily_occluded_ids.add(entity_id)
                    if independently_present:
                        tracker_confirmed_ids.add(entity_id)
                    else:
                        grace_retained_ids.add(entity_id)
                    self._missed_observations[entity_id] = missed
                    continue

            fused_entities[entity_id] = deepcopy(current)
            self._known_entities[entity_id] = deepcopy(current)
            self._missed_observations[entity_id] = 0

        for entity_id in sorted(previous_known_ids - set(current_entities)):
            previous = self._known_entities[entity_id]
            missed = self._missed_observations.get(entity_id, 0) + 1
            independently_present = entity_id in present_ids
            if independently_present or missed <= self.maximum_missed_observations:
                fused_entities[entity_id] = _occluded_entity(
                    previous,
                    missed_observations=missed,
                    maximum_missed_observations=self.maximum_missed_observations,
                    independently_present=independently_present,
                )
                temporarily_occluded_ids.add(entity_id)
                if independently_present:
                    tracker_confirmed_ids.add(entity_id)
                else:
                    grace_retained_ids.add(entity_id)
            else:
                expired_ids.add(entity_id)
            self._missed_observations[entity_id] = missed

        fused_inventory = _json_copy(current_inventory, "current_inventory")
        if not isinstance(fused_inventory, dict):
            raise TemporalSceneInventoryError("inventory copy must be an object")
        fused_inventory["entities"] = [
            fused_entities[entity_id] for entity_id in sorted(fused_entities)
        ]
        limitations = list(fused_inventory.get("limitations", []))
        limitations.extend(
            (
                "temporarily occluded identities expose no cached geometry",
                "tracker-confirmed presence does not provide completion evidence",
            )
        )
        fused_inventory["limitations"] = list(dict.fromkeys(limitations))
        fused_inventory["temporal_scene_evidence"] = {
            "schema_version": TEMPORAL_SCENE_INVENTORY_SCHEMA_VERSION,
            "maximum_missed_observations": self.maximum_missed_observations,
            "stale_geometry_exposed": False,
        }

        self._sequence += 1
        update = TemporalSceneInventoryUpdate(
            sequence=self._sequence,
            inventory=fused_inventory,
            fresh_visible_entity_ids=tuple(sorted(visible_ids)),
            temporarily_occluded_entity_ids=tuple(
                sorted(temporarily_occluded_ids)
            ),
            tracker_confirmed_entity_ids=tuple(sorted(tracker_confirmed_ids)),
            grace_retained_entity_ids=tuple(sorted(grace_retained_ids)),
            expired_entity_ids=tuple(sorted(expired_ids)),
            newly_observed_entity_ids=tuple(sorted(newly_observed)),
        )
        self._latest_update = update
        return update

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEMPORAL_SCENE_INVENTORY_SCHEMA_VERSION,
            "maximum_missed_observations": self.maximum_missed_observations,
            "known_entity_ids": list(self.known_entity_ids),
            "latest_update": (
                None
                if self._latest_update is None
                else self._latest_update.to_dict()
            ),
            "stale_geometry_exposed": False,
            "task_authority": False,
            "execution_authority": False,
        }
