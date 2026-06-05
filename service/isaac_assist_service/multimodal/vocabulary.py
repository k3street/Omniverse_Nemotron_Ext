"""
Structural-tags vocabulary registry.

Append-only registry that lists every accepted `isaac:` and `cad:` namespaced
tag. `user:` namespaced tags pass-through without registry lookup
(observability-only — never read by retrieval/instantiation/verification).

Removing a tag from the registry is forbidden; mark `status: "deprecated"`
instead. Old data referencing deprecated tags continues to load (warning
emitted; retrieval may downgrade quality but never crashes).

Spec §3.5.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# Default registry path — ships with the module under the multimodal package.
# This file is tracked in git; it is the controlled vocabulary that defines
# what tags `validate.py` accepts. Runtime extensions are appended in-place;
# removals are forbidden by `StructuralTagRegistry.deprecate()`.
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent / "structural_tags.registry.json"
)


TagStatus = Literal["active", "deprecated"]


@dataclass(frozen=True)
class TagEntry:
    """Immutable record for one tag in the structural-tags vocabulary registry."""
    tag: str
    status: TagStatus
    description: str
    added_in_version: str = "1.0"
    deprecated_in_version: Optional[str] = None
    replaced_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Default seeded registry
# ---------------------------------------------------------------------------
# Materialized on first run if registry file does not exist. Subsequent edits
# happen via append-only updates to the on-disk file; this constant is the
# bootstrap content.

_DEFAULT_TAGS: List[TagEntry] = [
    # Transport
    TagEntry("isaac:transport.conveyor", "active",
             "Object placed on a conveyor for translation between stations."),
    TagEntry("isaac:transport.belt", "active",
             "Generic belt-driven transport surface."),

    # Robot kinematics
    TagEntry("isaac:robot.fixed_base.arm", "active",
             "Robot arm with a fixed base (Franka, UR, Kinova, IIWA, Jaco7)."),
    TagEntry("isaac:robot.mobile.wheeled", "active",
             "Wheeled mobile platform (Nova Carter etc)."),
    TagEntry("isaac:robot.mobile.legged", "active",
             "Legged mobile platform (quadruped, humanoid). Reserved."),

    # Topology
    TagEntry("isaac:topology.linear_pipeline", "active",
             "Linear in→through→out pipeline of stations."),
    TagEntry("isaac:topology.parallel_lanes", "active",
             "Multiple parallel processing lanes."),
    TagEntry("isaac:topology.hub_spoke", "active",
             "Central hub with spoke branches. Reserved."),

    # Routing semantics
    TagEntry("isaac:routing.semantic_label.color", "active",
             "Routing decisions driven by color semantic labels."),
    TagEntry("isaac:routing.semantic_label.shape", "active",
             "Routing decisions driven by shape labels."),
    TagEntry("isaac:routing.semantic_label.size", "active",
             "Routing decisions driven by size labels."),

    # Invariants (verifier-relevant)
    TagEntry("isaac:invariant.cube_upright", "active",
             "Cube must end up upright (orientation invariant). CP-05 pattern."),
    TagEntry("isaac:invariant.no_collision_human", "active",
             "Robot must not collide with human in workspace. Reserved."),

    # Stations / fixtures
    TagEntry("isaac:station.passive_flip_wall", "active",
             "Passive flip station — gravity + ramp + end-stop. CP-05 pattern."),
    TagEntry("isaac:station.active_flipper", "active",
             "Active flip station — robotic actuator. Reserved."),

    # Constraints
    TagEntry("isaac:constraint.footprint_xy_box", "active",
             "Layout must fit inside a fixed XY footprint box. CP-04 pattern."),
    TagEntry("isaac:constraint.reach_limited", "active",
             "Operations limited by robot reach radius."),

    # Sensor families
    TagEntry("isaac:sensor.camera.rgb", "active",
             "RGB camera sensor."),
    TagEntry("isaac:sensor.camera.depth", "active",
             "Depth camera sensor."),
    TagEntry("isaac:sensor.lidar", "active",
             "Lidar sensor."),

    # Agent / human
    TagEntry("isaac:agent.human.passive", "active",
             "Passive human present in workspace (reserved for CP-06)."),
    TagEntry("isaac:agent.human.active", "active",
             "Active human collaborating with robot. Reserved."),

    # Behavior flags
    TagEntry("isaac:behavior.robot_stop_on_human", "active",
             "Robot stops when human enters safety zone. Reserved."),

    # CAD-imported provenance
    TagEntry("cad:imported.fusion360", "active",
             "Layout imported from Fusion 360 model."),
    TagEntry("cad:imported.solidworks", "active",
             "Layout imported from SolidWorks model."),
    TagEntry("cad:imported.onshape", "active",
             "Layout imported from Onshape model."),
]


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------

@dataclass
class StructuralTagRegistry:
    """In-memory registry of accepted structural_tag vocabulary entries."""
    entries: Dict[str, TagEntry] = field(default_factory=dict)

    def is_active(self, tag: str) -> bool:
        """True iff `tag` is registered and currently active.

        Tags in `user:` namespace are NOT in the registry; they pass through
        validation separately as observability-only. Use `is_registered`
        for membership-only check independent of namespace.
        """
        entry = self.entries.get(tag)
        return entry is not None and entry.status == "active"

    def is_registered(self, tag: str) -> bool:
        """True iff `tag` exists in registry, regardless of status."""
        return tag in self.entries

    def get(self, tag: str) -> Optional[TagEntry]:
        """Return the ``TagEntry`` for *tag*, or ``None`` if not registered."""
        return self.entries.get(tag)

    def add(self, entry: TagEntry) -> None:
        """Add a new tag. Appending only — replacing an existing entry with
        a different definition is forbidden.

        To deprecate a tag, use `deprecate(tag, replaced_by=..., version=...)`.
        """
        if entry.tag in self.entries:
            existing = self.entries[entry.tag]
            if existing != entry:
                raise ValueError(
                    f"tag {entry.tag!r} already registered with different "
                    f"definition; registry is append-only"
                )
            return
        self.entries[entry.tag] = entry

    def deprecate(self, tag: str, deprecated_in_version: str,
                  replaced_by: Optional[str] = None) -> None:
        """Mark a tag as deprecated. Cannot remove the entry; old LayoutSpecs
        referencing this tag must continue to validate."""
        existing = self.entries.get(tag)
        if existing is None:
            raise ValueError(f"cannot deprecate unknown tag {tag!r}")
        if existing.status == "deprecated":
            return  # idempotent
        self.entries[tag] = TagEntry(
            tag=existing.tag,
            status="deprecated",
            description=existing.description,
            added_in_version=existing.added_in_version,
            deprecated_in_version=deprecated_in_version,
            replaced_by=replaced_by,
        )

    def list_active(self) -> List[TagEntry]:
        """Return all currently active (non-deprecated) tag entries."""
        return [e for e in self.entries.values() if e.status == "active"]

    def list_deprecated(self) -> List[TagEntry]:
        """Return all tag entries whose status is ``"deprecated"``."""
        return [e for e in self.entries.values() if e.status == "deprecated"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _registry_to_json(registry: StructuralTagRegistry) -> Dict:
    """Serialise *registry* to the on-disk JSON format.

    Returns:
        Dict: JSON-serialisable dict with ``"version"`` and ``"tags"`` keys.
    """
    return {
        "version": "1.0",
        "tags": [
            {
                "tag": e.tag,
                "status": e.status,
                "description": e.description,
                "added_in_version": e.added_in_version,
                "deprecated_in_version": e.deprecated_in_version,
                "replaced_by": e.replaced_by,
            }
            for e in registry.entries.values()
        ],
    }


def _registry_from_json(data: Dict) -> StructuralTagRegistry:
    """Deserialise a ``StructuralTagRegistry`` from the on-disk JSON format.

    Args:
        data (Dict): Parsed JSON dict with a ``"tags"`` list.

    Returns:
        StructuralTagRegistry: Registry populated from *data*.
    """
    registry = StructuralTagRegistry()
    for raw in data.get("tags", []):
        entry = TagEntry(
            tag=raw["tag"],
            status=raw.get("status", "active"),
            description=raw.get("description", ""),
            added_in_version=raw.get("added_in_version", "1.0"),
            deprecated_in_version=raw.get("deprecated_in_version"),
            replaced_by=raw.get("replaced_by"),
        )
        registry.entries[entry.tag] = entry
    return registry


def load_default_registry(
    path: Optional[Path] = None,
    create_if_missing: bool = True,
) -> StructuralTagRegistry:
    """Load the structural-tags registry from disk; seed with defaults on
    first run.

    Args:
        path: Override the default registry path. Mainly for tests.
        create_if_missing: If True (default), seed the on-disk file with
            the bootstrap default tags when it doesn't exist.

    Returns:
        Loaded registry.
    """
    target_path = path or _DEFAULT_REGISTRY_PATH

    if not target_path.exists():
        if not create_if_missing:
            raise FileNotFoundError(
                f"structural-tags registry not found at {target_path}"
            )
        logger.info(
            f"Seeding default structural-tags registry at {target_path}"
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        registry = StructuralTagRegistry(
            entries={e.tag: e for e in _DEFAULT_TAGS}
        )
        target_path.write_text(
            json.dumps(_registry_to_json(registry), indent=2),
            encoding="utf-8",
        )
        return registry

    data = json.loads(target_path.read_text(encoding="utf-8"))
    return _registry_from_json(data)


def save_registry(
    registry: StructuralTagRegistry,
    path: Optional[Path] = None,
) -> None:
    """Persist *registry* to disk in the standard JSON format.

    Args:
        registry (StructuralTagRegistry): Registry to save.
        path (Path, optional): Override the default registry path. Mainly for tests.
    """
    target_path = path or _DEFAULT_REGISTRY_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(_registry_to_json(registry), indent=2),
        encoding="utf-8",
    )
