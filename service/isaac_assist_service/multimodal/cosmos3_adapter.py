"""Cosmos 3 reasoner adapter for photo/screenshot-to-layout proposals.

This module is deliberately runtime-light: it does not import Cosmos, CUDA,
Diffusers, vLLM, or NIM clients.  Its job is to define the contract between a
Cosmos 3 Reasoner response and Isaac Assist's deterministic ``LayoutSpec``.

Cosmos should infer objects, roles, and spatial relationships.  The floor-plan
canvas remains the review/correction surface, and Isaac Sim execution remains
owned by the version-aware harnesses.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from .object_palette import get_class
from .types import (
    Counts,
    Intent,
    LayoutSpec,
    Position,
    RoleBinding,
    Size,
    Source,
    StructuralFeatures,
    TypedObject,
)


CosmosInputKind = Literal["photo", "screenshot", "render", "video_frame", "prompt"]


class CosmosObjectProposal(BaseModel):
    """One object proposal emitted by a Cosmos 3 Reasoner workflow."""

    label: str = Field(description="Human-readable detected object label.")
    role: Optional[str] = Field(
        default=None,
        description="Optional Isaac Assist role hint, e.g. primary_robot.",
    )
    asset_hint: Optional[str] = Field(
        default=None,
        description="Optional Isaac/Omniverse asset hint or class name.",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    position_xy_m: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Estimated world/floor-plan x/y in metres.",
    )
    bbox_xyxy_norm: Optional[Tuple[float, float, float, float]] = Field(
        default=None,
        description="Normalized image bbox [x1, y1, x2, y2] in [0, 1].",
    )
    size_xy_m: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Estimated floor footprint in metres.",
    )
    rotation_deg: float = Field(default=0.0, ge=0.0, lt=360.0)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    notes: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _label_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be empty")
        return value

    @field_validator("bbox_xyxy_norm")
    @classmethod
    def _bbox_is_ordered(
        cls,
        value: Optional[Tuple[float, float, float, float]],
    ) -> Optional[Tuple[float, float, float, float]]:
        if value is None:
            return value
        x1, y1, x2, y2 = value
        if not all(0.0 <= v <= 1.0 for v in value):
            raise ValueError("bbox_xyxy_norm values must be in [0, 1]")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox_xyxy_norm must be ordered x1< x2 and y1< y2")
        return value


class CosmosRelationProposal(BaseModel):
    """One spatial/semantic relation inferred by Cosmos."""

    subject: str
    predicate: str
    object: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CosmosSceneObservation(BaseModel):
    """Structured Cosmos 3 Reasoner output accepted by Isaac Assist."""

    input_kind: CosmosInputKind = "photo"
    prompt: str = ""
    summary: str = ""
    pattern_hint: str = "pick_place"
    workspace_size_xy_m: Tuple[float, float] = Field(default=(4.0, 4.0))
    confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    objects: List[CosmosObjectProposal] = Field(default_factory=list)
    relations: List[CosmosRelationProposal] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


LABEL_CLASS_ALIASES: Dict[str, str] = {
    "bin": "bin",
    "box": "obstacle_box",
    "conveyor": "conveyor_short",
    "cube": "cube_medium",
    "floor": "groundplane",
    "franka": "franka_panda",
    "franka panda": "franka_panda",
    "light": "distant_light",
    "panda": "franka_panda",
    "plane": "groundplane",
    "robot arm": "franka_panda",
    "shelf": "shelf",
    "table": "table_medium",
    "target bin": "bin",
    "ur10": "ur10",
    "ur5": "ur5e",
    "ur5e": "ur5e",
}

ROLE_ALIASES: Dict[str, str] = {
    "bin": "target",
    "destination": "target",
    "floor": "workspace",
    "object": "workpiece",
    "pick": "workpiece",
    "robot": "primary_robot",
    "target bin": "target",
    "workpiece": "workpiece",
}

PATTERN_HINTS = {"pick_place", "sort", "reorient", "navigate", "insert", "train", "other"}


def normalize_object_class(proposal: CosmosObjectProposal) -> str:
    """Map Cosmos labels/hints into the local object palette."""

    candidates = [
        proposal.asset_hint or "",
        proposal.label,
        (proposal.role or "").replace("_", " "),
    ]
    for candidate in candidates:
        key = candidate.strip().lower()
        if not key:
            continue
        direct_key = key.replace(" ", "_").replace("-", "_")
        if get_class(direct_key):
            return direct_key
        if key in LABEL_CLASS_ALIASES:
            return LABEL_CLASS_ALIASES[key]

    label = proposal.label.lower()
    for needle, object_class in LABEL_CLASS_ALIASES.items():
        if needle in label:
            return object_class
    return "obstacle_box"


def normalize_role(proposal: CosmosObjectProposal, object_class: str) -> Optional[str]:
    """Normalize a role hint while keeping unknown roles out of bindings."""

    candidates = [proposal.role or "", proposal.label]
    for candidate in candidates:
        key = candidate.strip().lower().replace("_", " ")
        if key in ROLE_ALIASES:
            return ROLE_ALIASES[key]

    palette_entry = get_class(object_class)
    if palette_entry and palette_entry.category == "robot":
        return "primary_robot"
    if object_class.startswith("cube_"):
        return "workpiece"
    return None


def _safe_name(label: str, index: int) -> str:
    """Create a USD-safe object name from a proposal label."""

    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", label.strip()).strip("_")
    if not cleaned:
        cleaned = "Object"
    if not cleaned[0].isalpha():
        cleaned = f"Object_{cleaned}"
    return f"{cleaned}_{index + 1}"


def _position_for(
    proposal: CosmosObjectProposal,
    workspace_size_xy_m: Tuple[float, float],
) -> Position:
    if proposal.position_xy_m is not None:
        return Position(x=proposal.position_xy_m[0], y=proposal.position_xy_m[1])

    if proposal.bbox_xyxy_norm is not None:
        x1, y1, x2, y2 = proposal.bbox_xyxy_norm
        width_m, height_m = workspace_size_xy_m
        x = ((x1 + x2) / 2.0 - 0.5) * width_m
        y = (0.5 - (y1 + y2) / 2.0) * height_m
        return Position(x=x, y=y)

    return Position(x=0.0, y=0.0)


def _size_for(proposal: CosmosObjectProposal, object_class: str) -> Size:
    if proposal.size_xy_m is not None:
        w, h = proposal.size_xy_m
        return Size(w=max(float(w), 0.001), h=max(float(h), 0.001))

    palette_entry = get_class(object_class)
    if palette_entry:
        w, h = palette_entry.footprint_xy_m
        return Size(w=max(float(w), 0.001), h=max(float(h), 0.001))

    return Size(w=0.2, h=0.2)


def _counts_for(objects: List[TypedObject]) -> Counts:
    counts = Counts()
    for obj in objects:
        cls = obj.object_class
        palette_entry = get_class(cls)
        category = palette_entry.category if palette_entry else "prop"
        if category == "robot":
            counts.robots += 1
        elif category == "sensor":
            counts.sensors += 1
        elif "conveyor" in cls:
            counts.conveyors += 1
        elif "bin" in cls:
            counts.bins += 1
        elif "cube" in cls:
            counts.cubes += 1
    return counts


def _features_for(objects: List[TypedObject]) -> StructuralFeatures:
    classes = {obj.object_class for obj in objects}
    has_conveyor = any("conveyor" in cls for cls in classes)
    n_destinations = max(1, sum(1 for cls in classes if "bin" in cls or cls == "shelf"))
    n_robot_stations = sum(
        1
        for obj in objects
        if _is_robot_class(obj.object_class)
    )
    return StructuralFeatures(
        n_robot_stations=max(1, n_robot_stations),
        n_destinations=n_destinations,
        destination_kind="single_bin" if n_destinations <= 1 else "n_bins_routed",
        uses_conveyor_transport=has_conveyor,
    )


def _is_robot_class(object_class: str) -> bool:
    palette_entry = get_class(object_class)
    return bool(palette_entry and palette_entry.category == "robot")


def cosmos_observation_to_layout_spec(
    observation: CosmosSceneObservation,
    *,
    session_id: Optional[str] = None,
) -> LayoutSpec:
    """Convert a Cosmos scene observation into a reviewable LayoutSpec."""

    typed_objects: List[TypedObject] = []
    bindings: Dict[str, RoleBinding] = {}
    timestamp = datetime.now(timezone.utc)

    for index, proposal in enumerate(observation.objects):
        object_class = normalize_object_class(proposal)
        role = normalize_role(proposal, object_class)
        object_id = str(uuid.uuid4())
        metadata = {
            "cosmos_label": proposal.label,
            "cosmos_asset_hint": proposal.asset_hint,
            "cosmos_confidence": proposal.confidence,
            **proposal.metadata,
        }
        typed = TypedObject(
            id=object_id,
            **{
                "class": object_class,
                "name": _safe_name(proposal.label, index),
                "position": _position_for(proposal, observation.workspace_size_xy_m),
                "rotation": proposal.rotation_deg,
                "size": _size_for(proposal, object_class),
                "color": proposal.color,
                "notes": proposal.notes,
                "metadata": metadata,
                "role_hint": role,
                "layer": "cosmos_proposal",
            },
        )
        typed_objects.append(typed)
        if role:
            bindings[role] = RoleBinding(
                object_id=object_id,
                source="modality_emitted",
                confidence=proposal.confidence,
                timestamp=timestamp,
            )

    constraints: List[Dict[str, Any]] = [
        {
            "type": "cosmos_relation",
            "subject": rel.subject,
            "predicate": rel.predicate,
            "object": rel.object,
            "confidence": rel.confidence,
        }
        for rel in observation.relations
    ]

    intent = Intent(
        pattern_hint=(
            observation.pattern_hint
            if observation.pattern_hint in PATTERN_HINTS
            else "other"
        ),
        counts=_counts_for(typed_objects),
        structural_features=_features_for(typed_objects),
        structural_tags=["isaac:robot.fixed_base.arm"] if any(
            _is_robot_class(obj.object_class)
            for obj in typed_objects
        ) else [],
    )

    metadata = {
        "provider": "cosmos3",
        "adapter": "cosmos3_reasoner_layout_v1",
        "input_kind": observation.input_kind,
        "summary": observation.summary,
        "session_id": session_id,
        **observation.metadata,
    }

    return LayoutSpec(
        intent=intent,
        objects=typed_objects,
        constraints=constraints or None,
        bindings=bindings or None,
        parameters={
            "workspace_size_xy_m": observation.workspace_size_xy_m,
            "requires_user_review": True,
        },
        source=Source(
            modality="photo" if observation.input_kind in {"photo", "screenshot", "render", "video_frame"} else "text",
            confidence=observation.confidence,
            timestamp=timestamp,
            raw_input={"prompt": observation.prompt} if observation.prompt else None,
            metadata=metadata,
        ),
        revision=1,
    )


__all__ = [
    "CosmosObjectProposal",
    "CosmosRelationProposal",
    "CosmosSceneObservation",
    "cosmos_observation_to_layout_spec",
    "normalize_object_class",
    "normalize_role",
]
