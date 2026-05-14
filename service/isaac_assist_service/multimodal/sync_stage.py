"""Phase 22 — sync_from_stage round-trip.

Reads Kit stage under a scope_prim, returns a LayoutSpec mirroring the
current state. Pairs with Phase 19 instantiator for canvas/Kit
bidirectional sync.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 22.

Phase 22 SPEC/PARSER layer (pure Python, no live Kit RPC required):
  - PrimRecord          — canonical "stage prim" representation
  - LayoutClass         — Literal of known USD class names
  - LayoutEntry         — parsed, classified prim with transform
  - StagePrimClassifier — maps USD type names → LayoutClass
  - StageToLayoutSpecParser — parse PrimRecords → LayoutSpec dict
  - synthetic_stage_records_demo — 5+ example prims for testing/demo
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase status flag (checked by phase_metadata tooling)
# ---------------------------------------------------------------------------
PHASE_STATUS = "landed"

# ---------------------------------------------------------------------------
# LayoutClass — known USD primitive / light / camera types
# ---------------------------------------------------------------------------
LayoutClass = Literal[
    "Cube",
    "Sphere",
    "Cylinder",
    "Cone",
    "Plane",
    "Camera",
    "DistantLight",
    "SphereLight",
    "DomeLight",
    "Xform",
    "Reference",
    "unknown",
]

# ---------------------------------------------------------------------------
# PrimRecord — canonical representation of a single stage prim
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PrimRecord:
    """Canonical representation of a USD stage prim returned by Kit RPC.

    All transform values are in USD scene-units (metres / degrees).
    """
    path: str
    type_name: str
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotate_xyz_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    attrs: dict = dataclasses.field(default_factory=dict)
    kind: Optional[str] = None
    references: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# LayoutEntry — classified, parsed prim ready for LayoutSpec
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LayoutEntry:
    """A classified prim entry in a LayoutSpec produced by the parser."""
    class_name: LayoutClass
    prim_path: str
    position: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    scale: tuple[float, float, float]
    source_attrs: dict = dataclasses.field(default_factory=dict)
    references: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# StagePrimClassifier
# ---------------------------------------------------------------------------

class StagePrimClassifier:
    """Maps USD type names to LayoutClass strings.

    Priority order:
      1. Non-empty ``references`` list → "Reference"
      2. type_name in TYPE_NAME_TO_CLASS → mapped value
      3. default → "unknown"
    """

    TYPE_NAME_TO_CLASS: dict[str, LayoutClass] = {
        "Cube": "Cube",
        "Sphere": "Sphere",
        "Cylinder": "Cylinder",
        "Cone": "Cone",
        "Plane": "Plane",
        "Camera": "Camera",
        "DistantLight": "DistantLight",
        "SphereLight": "SphereLight",
        "DomeLight": "DomeLight",
        "Xform": "Xform",
    }

    def classify(self, record: PrimRecord) -> LayoutClass:
        """Return the LayoutClass for *record*.

        References take priority so that any prim that pulls in external
        USD assets is tagged "Reference" regardless of its base type.
        """
        if record.references:
            return "Reference"
        return self.TYPE_NAME_TO_CLASS.get(record.type_name, "unknown")


# ---------------------------------------------------------------------------
# StageToLayoutSpecParser
# ---------------------------------------------------------------------------

class StageToLayoutSpecParser:
    """Parse a list of PrimRecords into LayoutEntry objects / LayoutSpec dict."""

    def __init__(self, classifier: Optional[StagePrimClassifier] = None) -> None:
        """Initialise the parser with an optional prim classifier; defaults to :class:`StagePrimClassifier`."""
        self._classifier = classifier if classifier is not None else StagePrimClassifier()

    # ------------------------------------------------------------------
    # Single-record helpers
    # ------------------------------------------------------------------

    def parse_record(self, record: PrimRecord) -> LayoutEntry:
        """Convert one PrimRecord into a classified LayoutEntry."""
        class_name = self._classifier.classify(record)
        return LayoutEntry(
            class_name=class_name,
            prim_path=record.path,
            position=tuple(record.translate),  # type: ignore[arg-type]
            rotation_deg=tuple(record.rotate_xyz_deg),  # type: ignore[arg-type]
            scale=tuple(record.scale),  # type: ignore[arg-type]
            source_attrs=dict(record.attrs),
            references=list(record.references),
        )

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def parse_records(self, records: list[PrimRecord]) -> list[LayoutEntry]:
        """Parse multiple PrimRecords; returns empty list for empty input."""
        return [self.parse_record(r) for r in records]

    def parse_records_to_layout_spec(self, records: list[PrimRecord]) -> dict:
        """Return a LayoutSpec dict with version, timestamp and entries.

        Shape::

            {
                "version": 1,
                "generated_at": "<ISO-8601>",
                "entries": [<LayoutEntry as dict>, ...]
            }
        """
        entries = self.parse_records(records)
        return {
            "version": 1,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "entries": [dataclasses.asdict(e) for e in entries],
        }

    def filter_known(self, records: list[PrimRecord]) -> list[PrimRecord]:
        """Return only records that classify to a known (non-"unknown") class."""
        return [r for r in records if self._classifier.classify(r) != "unknown"]


# ---------------------------------------------------------------------------
# Demo / testing helper
# ---------------------------------------------------------------------------

def synthetic_stage_records_demo() -> list[PrimRecord]:
    """Return 5+ example PrimRecords spanning major LayoutClass categories.

    Useful for unit tests and manual round-trip demos.
    """
    return [
        PrimRecord(
            path="/World/Layout/WorkCube",
            type_name="Cube",
            translate=(0.5, 0.0, 0.4),
            rotate_xyz_deg=(0.0, 0.0, 45.0),
            scale=(0.1, 0.1, 0.1),
            attrs={"physics:rigidBodyEnabled": True},
        ),
        PrimRecord(
            path="/World/Layout/BallSphere",
            type_name="Sphere",
            translate=(-0.3, 0.0, 0.3),
            rotate_xyz_deg=(0.0, 0.0, 0.0),
            scale=(0.05, 0.05, 0.05),
        ),
        PrimRecord(
            path="/World/Layout/OverheadLight",
            type_name="DistantLight",
            translate=(0.0, 5.0, 5.0),
            rotate_xyz_deg=(-45.0, 0.0, 0.0),
            attrs={"intensity": 3000.0},
        ),
        PrimRecord(
            path="/World/Layout/FrontCamera",
            type_name="Camera",
            translate=(0.0, -2.0, 1.5),
            rotate_xyz_deg=(30.0, 0.0, 0.0),
        ),
        PrimRecord(
            path="/World/Layout/FrankaRobot",
            type_name="Xform",
            translate=(0.0, 0.0, 0.0),
            references=[
                "omniverse://localhost/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"
            ],
            kind="assembly",
        ),
        PrimRecord(
            path="/World/Layout/ConveyorBelt",
            type_name="Xform",
            translate=(1.0, 0.0, 0.0),
            references=[
                "omniverse://localhost/Isaac/Props/Conveyor/conveyor_belt.usd"
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Legacy scaffold helpers (preserved for backward-compat)
# ---------------------------------------------------------------------------

def _classify_prim(prim: Dict[str, Any]) -> Optional[str]:
    """Map a Kit prim entry to a canonical object_class.

    Phase 22 scaffold: matches by USD reference URL prefix. The
    classification heuristic improves over Phase 25 (object palette
    expansion).
    """
    ref = prim.get("reference_url", "") or ""
    if "FrankaPanda/franka" in ref:
        return "franka_panda"
    if "UR10" in ref:
        return "ur10"
    if "UR5" in ref:
        return "ur5e"
    usd_type = prim.get("usd_type", "")
    if usd_type == "Cube":
        return "cube"
    if usd_type == "Cylinder":
        return "cylinder"
    return None


def _prim_to_typed_object(prim: Dict[str, Any], klass: str) -> Dict[str, Any]:
    """Convert a raw prim dict and its class label into a typed layout-object dict."""
    return {
        "object_class": klass,
        "position": prim.get("position", [0.0, 0.0, 0.0]),
        "prim_path": prim.get("path", ""),
    }


async def sync_from_stage(
    session_id: str,
    scope_prim: str = "/World/Layout",
) -> Dict[str, Any]:
    """Read Kit stage prims, build a LayoutSpec.

    Phase 22 scaffold: returns a dict shape compatible with LayoutSpec.
    Real Kit RPC integration is daytime work.
    """
    # Scaffold: returns empty LayoutSpec.
    return {
        "intent": {"pattern_hint": "pick_place"},
        "objects": [],
        "source": {"modality": "viewport", "confidence": 1.0},
        "scope_prim": scope_prim,
        "synced_at_session": session_id,
    }
