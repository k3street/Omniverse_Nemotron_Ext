"""Deterministic spatial-relation reasoning for floor-plan scene builds.

Cosmos Reason and local LLMs may propose relations such as "fruit in bowl" or
"robot on table".  This module turns those proposals into Isaac-safe placement
relations and emits diagnostics when a proposal needs review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .object_palette import get_class


PARENT_RELATIONS = {"on_top_of", "inside", "stacked_above", "mounted_to", "beside"}
VALID_RELATION_KINDS = {
    "on_top_of",
    "inside",
    "contains",
    "supports",
    "attached_to",
    "mounted_to",
    "beside",
    "near",
    "left_of",
    "right_of",
    "front_of",
    "behind",
    "stacked_above",
}
RELATION_ALIASES = {
    "on": "on_top_of",
    "on top": "on_top_of",
    "on top of": "on_top_of",
    "upon": "on_top_of",
    "in": "inside",
    "inside of": "inside",
    "within": "inside",
    "contains": "inside",
    "supports": "on_top_of",
    "attached": "attached_to",
    "attached to": "attached_to",
    "mounted": "mounted_to",
    "mounted to": "mounted_to",
    "beside": "beside",
    "next to": "beside",
    "near": "near",
    "in front of": "front_of",
    "front": "front_of",
    "behind": "behind",
    "left of": "left_of",
    "right of": "right_of",
    "above": "stacked_above",
    "stacked above": "stacked_above",
}

SUPPORT_CLASSES = {
    "table_small", "table_medium", "table_large",
    "counter", "kitchen_counter",
    "conveyor", "conveyor_short", "conveyor_long",
    "plate", "shelf", "kit_tray", "rotary_table",
}
CONTAINER_CLASSES = {"bowl", "bin", "bin_large", "kit_tray", "microwave"}
SMALL_OBJECT_CLASSES = {
    "cube", "cube_small", "cube_medium", "cube_large",
    "cylinder_small", "cylinder_medium", "cylinder_large",
    "sphere", "fruit", "apple", "orange", "hamburger",
    "screw", "nut", "bolt",
}
CLASS_HEIGHTS_M = {
    "table_small": 0.75, "table_medium": 0.75, "table_large": 0.75,
    "counter": 0.9, "kitchen_counter": 0.9,
    "bin": 0.3, "bin_large": 0.45, "bowl": 0.12, "plate": 0.03,
    "microwave": 0.35,
    "cube": 0.05, "cube_small": 0.05, "cube_medium": 0.08, "cube_large": 0.15,
    "fruit": 0.07, "apple": 0.07, "orange": 0.08, "hamburger": 0.08,
    "sphere": 0.05,
    "conveyor": 0.35, "conveyor_short": 0.35, "conveyor_long": 0.35,
    "franka_panda": 1.0, "ur5e": 1.0, "ur10e": 1.1, "ur10": 1.1,
}
SUPPORT_TOP_M = {
    "table_small": 0.75, "table_medium": 0.75, "table_large": 0.75,
    "counter": 0.9, "kitchen_counter": 0.9,
    "plate": 0.03,
    "conveyor": 0.35, "conveyor_short": 0.35, "conveyor_long": 0.35,
}
INTERIOR_FLOOR_OFFSET_M = {
    "bin": 0.05, "bin_large": 0.07, "bowl": 0.03, "microwave": 0.08,
}


@dataclass
class RelationDiagnostic:
    """One relation reasoning diagnostic."""

    severity: str
    code: str
    message: str
    subject_id: str = ""
    object_id: str = ""
    relation: str = ""
    normalized_relation: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "subject_id": self.subject_id,
            "object_id": self.object_id,
            "relation": self.relation,
            "normalized_relation": self.normalized_relation,
        }


@dataclass
class NormalizedRelation:
    """A normalized directed relation."""

    subject_id: str
    relation: str
    object_id: str
    confidence: float = 1.0
    source: str = "reasoned"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "relation": self.relation,
            "object_id": self.object_id,
            "confidence": self.confidence,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class RelationReasoningResult:
    """Normalized relations plus diagnostics."""

    relations: List[NormalizedRelation]
    diagnostics: List[RelationDiagnostic] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    def diagnostics_as_dicts(self) -> List[Dict[str, Any]]:
        return [d.as_dict() for d in self.diagnostics]


def _obj_get(obj: Any, attr: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        if attr == "object_class":
            return obj.get("object_class", obj.get("class", default))
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _rel_get(rel: Any, attr: str, default: Any = None) -> Any:
    if isinstance(rel, dict):
        return rel.get(attr, default)
    return getattr(rel, attr, default)


def object_class(obj: Any) -> str:
    return str(_obj_get(obj, "object_class", "") or _obj_get(obj, "class", "") or "")


def object_category(obj: Any) -> str:
    cls = object_class(obj)
    palette = get_class(cls)
    if palette:
        return palette.category
    if cls in CONTAINER_CLASSES:
        return "fixture"
    if cls in SUPPORT_CLASSES:
        return "fixture"
    return "prop"


def normalize_relation_kind(kind: str) -> str:
    key = str(kind or "").strip().lower().replace("_", " ")
    return RELATION_ALIASES.get(key, key.replace(" ", "_"))


def _can_support(parent: Any) -> bool:
    cls = object_class(parent)
    palette = get_class(cls)
    if cls in SUPPORT_CLASSES:
        return True
    return bool(palette and palette.category == "fixture" and cls not in CONTAINER_CLASSES)


def _can_contain(parent: Any) -> bool:
    return object_class(parent) in CONTAINER_CLASSES


def _is_robot(obj: Any) -> bool:
    return object_category(obj) == "robot"


def _is_small_object(obj: Any) -> bool:
    cls = object_class(obj)
    if cls in SMALL_OBJECT_CLASSES:
        return True
    palette = get_class(cls)
    if not palette:
        return False
    return palette.category == "prop" and max(palette.footprint_xy_m) <= 0.25


def _relation_metadata(rel: Any) -> Dict[str, Any]:
    value = _rel_get(rel, "metadata", {}) or {}
    return dict(value) if isinstance(value, dict) else {}


def _metadata(obj: Any) -> Dict[str, Any]:
    value = _obj_get(obj, "metadata", {}) or {}
    return dict(value) if isinstance(value, dict) else {}


def _position3(obj: Any) -> List[float]:
    pos = _obj_get(obj, "position", [0.0, 0.0, 0.0])
    if hasattr(pos, "x") and hasattr(pos, "y"):
        return [float(pos.x), float(pos.y), 0.0]
    if isinstance(pos, dict):
        return [
            float(pos.get("x", 0.0)),
            float(pos.get("y", 0.0)),
            float(pos.get("z", 0.0)),
        ]
    try:
        seq = list(pos)
    except Exception:
        seq = [0.0, 0.0, 0.0]
    while len(seq) < 3:
        seq.append(0.0)
    return [float(seq[0]), float(seq[1]), float(seq[2])]


def _scale3(obj: Any) -> List[float]:
    size = _obj_get(obj, "size", None)
    if hasattr(size, "w") and hasattr(size, "h"):
        return [float(size.w), float(size.h), _height_m(obj)]
    if isinstance(size, dict) and "w" in size and "h" in size:
        return [float(size["w"]), float(size["h"]), _height_m(obj)]
    scale = _obj_get(obj, "scale", [1.0, 1.0, 1.0])
    try:
        seq = list(scale)
    except Exception:
        seq = [1.0, 1.0, _height_m(obj)]
    while len(seq) < 3:
        seq.append(_height_m(obj))
    return [float(seq[0]), float(seq[1]), float(seq[2])]


def _height_m(obj: Any) -> float:
    metadata = _metadata(obj)
    value = metadata.get("height_m") or metadata.get("asset_height_m")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    cls = object_class(obj)
    if cls in CLASS_HEIGHTS_M:
        return CLASS_HEIGHTS_M[cls]
    return 0.1


def _support_top_m(obj: Any) -> float:
    metadata = _metadata(obj)
    value = metadata.get("support_surface_z_m") or metadata.get("top_z_m")
    if isinstance(value, (int, float)):
        return float(value)
    return SUPPORT_TOP_M.get(object_class(obj), _height_m(obj))


def _interior_floor_offset_m(obj: Any) -> float:
    metadata = _metadata(obj)
    value = metadata.get("interior_floor_z_m")
    if isinstance(value, (int, float)):
        return float(value)
    return INTERIOR_FLOOR_OFFSET_M.get(object_class(obj), 0.02)


def normalize_spatial_relations(spec_or_objects: Any, relations: Optional[Iterable[Any]] = None) -> RelationReasoningResult:
    """Normalize and validate spatial relations.

    Args:
        spec_or_objects: Either a LayoutSpec-like object with ``objects`` and
            ``relations`` attributes, or an iterable of objects.
        relations: Optional explicit relation iterable when the first argument
            is an object list.
    """
    if relations is None and hasattr(spec_or_objects, "objects"):
        objects = getattr(spec_or_objects, "objects", None) or []
        raw_relations = getattr(spec_or_objects, "relations", None) or []
    else:
        objects = spec_or_objects or []
        raw_relations = relations or []

    object_by_id = {
        str(_obj_get(obj, "id", "")): obj
        for obj in objects
        if str(_obj_get(obj, "id", ""))
    }
    normalized: List[NormalizedRelation] = []
    diagnostics: List[RelationDiagnostic] = []
    parent_by_subject: Dict[str, str] = {}

    for raw in raw_relations:
        subject_id = str(_rel_get(raw, "subject_id", ""))
        object_id = str(_rel_get(raw, "object_id", ""))
        original = str(_rel_get(raw, "relation", ""))
        relation = normalize_relation_kind(original)
        original_key = original.strip().lower().replace("_", " ")
        if original_key == "contains":
            subject_id, object_id = object_id, subject_id
            relation = "inside"
        elif original_key == "supports":
            subject_id, object_id = object_id, subject_id
            relation = "on_top_of"
        confidence = float(_rel_get(raw, "confidence", 1.0) or 1.0)
        source = str(_rel_get(raw, "source", "reasoned") or "reasoned")
        metadata = _relation_metadata(raw)
        metadata.setdefault("original_relation", original)

        subject = object_by_id.get(subject_id)
        parent = object_by_id.get(object_id)
        if subject is None or parent is None:
            diagnostics.append(RelationDiagnostic(
                severity="error",
                code="relation.unknown_object",
                message="Relation references an object id that is not present in the layout.",
                subject_id=subject_id,
                object_id=object_id,
                relation=original,
                normalized_relation=relation,
            ))
            continue
        if subject_id == object_id:
            diagnostics.append(RelationDiagnostic(
                severity="error",
                code="relation.self_reference",
                message="An object cannot have a spatial relation to itself.",
                subject_id=subject_id,
                object_id=object_id,
                relation=original,
                normalized_relation=relation,
            ))
            continue

        if relation == "inside" and not _can_contain(parent):
            diagnostics.append(RelationDiagnostic(
                severity="warning",
                code="relation.container_mismatch",
                message=(
                    f"{object_class(parent)!r} is not a known container; "
                    "normalized the relation to near for review."
                ),
                subject_id=subject_id,
                object_id=object_id,
                relation=original,
                normalized_relation="near",
            ))
            relation = "near"

        if relation in {"on_top_of", "stacked_above"}:
            if _is_robot(subject):
                if _can_support(parent):
                    diagnostics.append(RelationDiagnostic(
                        severity="warning",
                        code="relation.robot_on_support",
                        message=(
                            "A robot arm should not be treated like a small object on a support; "
                            "normalized to mounted_to so the build can anchor it instead of stacking it."
                        ),
                        subject_id=subject_id,
                        object_id=object_id,
                        relation=original,
                        normalized_relation="mounted_to",
                    ))
                    relation = "mounted_to"
                else:
                    diagnostics.append(RelationDiagnostic(
                        severity="warning",
                        code="relation.robot_parent_mismatch",
                        message="A robot placement claim needs a support fixture; normalized to beside.",
                        subject_id=subject_id,
                        object_id=object_id,
                        relation=original,
                        normalized_relation="beside",
                    ))
                    relation = "beside"
            elif not _can_support(parent):
                diagnostics.append(RelationDiagnostic(
                    severity="warning",
                    code="relation.support_mismatch",
                    message=(
                        f"{object_class(parent)!r} is not a known support surface; "
                        "normalized the relation to near for review."
                    ),
                    subject_id=subject_id,
                    object_id=object_id,
                    relation=original,
                    normalized_relation="near",
                ))
                relation = "near"

        if relation == "mounted_to" and not _can_support(parent):
            diagnostics.append(RelationDiagnostic(
                severity="warning",
                code="relation.mount_parent_mismatch",
                message="Mounted relations require a support fixture; normalized to beside.",
                subject_id=subject_id,
                object_id=object_id,
                relation=original,
                normalized_relation="beside",
            ))
            relation = "beside"

        if relation == "inside" and not _is_small_object(subject) and not metadata.get("allow_large_inside"):
            diagnostics.append(RelationDiagnostic(
                severity="warning",
                code="relation.large_inside_container",
                message="Large objects inside containers require review before Isaac placement.",
                subject_id=subject_id,
                object_id=object_id,
                relation=original,
                normalized_relation=relation,
            ))

        if relation in PARENT_RELATIONS:
            old_parent = parent_by_subject.get(subject_id)
            if old_parent and old_parent != object_id:
                diagnostics.append(RelationDiagnostic(
                    severity="error",
                    code="relation.multiple_parents",
                    message="Only one vertical/container parent relation is allowed per object.",
                    subject_id=subject_id,
                    object_id=object_id,
                    relation=original,
                    normalized_relation=relation,
                ))
                continue
            parent_by_subject[subject_id] = object_id

        normalized.append(NormalizedRelation(
            subject_id=subject_id,
            relation=relation,
            object_id=object_id,
            confidence=confidence,
            source=source,
            metadata=metadata,
        ))

    return RelationReasoningResult(relations=normalized, diagnostics=diagnostics)


def predict_relation_positions(spec_or_objects: Any, relations: Optional[Iterable[Any]] = None) -> Dict[str, List[float]]:
    """Predict 3D positions from the normalized relation graph."""
    if relations is None and hasattr(spec_or_objects, "objects"):
        objects = getattr(spec_or_objects, "objects", None) or []
    else:
        objects = spec_or_objects or []

    reasoning = normalize_spatial_relations(spec_or_objects, relations)
    object_by_id = {
        str(_obj_get(obj, "id", "")): obj
        for obj in objects
        if str(_obj_get(obj, "id", ""))
    }
    parent_relation_by_subject = {
        rel.subject_id: rel
        for rel in reasoning.relations
        if rel.relation in PARENT_RELATIONS
    }
    computed: Dict[str, List[float]] = {}

    def resolve(obj: Any, stack: Optional[set[str]] = None) -> List[float]:
        obj_id = str(_obj_get(obj, "id", ""))
        if obj_id in computed:
            return computed[obj_id]
        if stack is None:
            stack = set()
        if obj_id in stack:
            return _position3(obj)
        stack.add(obj_id)

        own_position = _position3(obj)
        relation = parent_relation_by_subject.get(obj_id)
        if not relation:
            computed[obj_id] = own_position
            return own_position

        parent = object_by_id.get(relation.object_id)
        if parent is None:
            computed[obj_id] = own_position
            return own_position

        parent_position = resolve(parent, stack)
        child_height = _height_m(obj)
        if relation.relation == "mounted_to" and _is_robot(obj):
            z = parent_position[2] + _support_top_m(parent)
            computed[obj_id] = [parent_position[0], parent_position[1], round(z, 4)]
        elif relation.relation in {"on_top_of", "stacked_above", "mounted_to"}:
            z = parent_position[2] + _support_top_m(parent) + child_height / 2.0
            computed[obj_id] = [parent_position[0], parent_position[1], round(z, 4)]
        elif relation.relation == "inside":
            parent_height = _height_m(parent)
            z = (
                parent_position[2]
                - parent_height / 2.0
                + _interior_floor_offset_m(parent)
                + child_height / 2.0
            )
            computed[obj_id] = [parent_position[0], parent_position[1], round(z, 4)]
        elif relation.relation == "beside":
            parent_scale = _scale3(parent)
            child_scale = _scale3(obj)
            x = parent_position[0] + parent_scale[0] / 2.0 + child_scale[0] / 2.0 + 0.15
            computed[obj_id] = [round(x, 4), parent_position[1], own_position[2]]
        else:
            computed[obj_id] = own_position
        return computed[obj_id]

    for obj in objects:
        resolve(obj)
    return computed


def verify_relation_geometry(
    spec_or_objects: Any,
    relations: Optional[Iterable[Any]] = None,
    *,
    actual_positions: Optional[Dict[str, Iterable[float]]] = None,
    tolerance_m: float = 0.02,
) -> Dict[str, Any]:
    """Verify normalized spatial relations against predicted or actual positions.

    ``actual_positions`` is intentionally optional.  Current dry-run/campaign
    flows use predicted positions; a later Isaac live-stage readback can pass
    measured prim translations through the same verifier.
    """
    if relations is None and hasattr(spec_or_objects, "objects"):
        objects = getattr(spec_or_objects, "objects", None) or []
    else:
        objects = spec_or_objects or []
    object_by_id = {
        str(_obj_get(obj, "id", "")): obj
        for obj in objects
        if str(_obj_get(obj, "id", ""))
    }
    reasoning = normalize_spatial_relations(spec_or_objects, relations)
    predicted = predict_relation_positions(spec_or_objects, relations)
    positions: Dict[str, List[float]] = {}
    if actual_positions:
        for key, value in actual_positions.items():
            seq = list(value)
            while len(seq) < 3:
                seq.append(0.0)
            positions[str(key)] = [float(seq[0]), float(seq[1]), float(seq[2])]
    else:
        positions = predicted

    checks: List[Dict[str, Any]] = []
    for rel in reasoning.relations:
        subject = object_by_id.get(rel.subject_id)
        parent = object_by_id.get(rel.object_id)
        child_pos = positions.get(rel.subject_id)
        parent_pos = positions.get(rel.object_id)
        status = "pass"
        error_m = 0.0
        expected: Optional[List[float]] = None
        if subject is None or parent is None or child_pos is None or parent_pos is None:
            status = "fail"
            message = "relation endpoint missing from position set"
        elif rel.relation == "mounted_to" and _is_robot(subject):
            expected = [
                parent_pos[0],
                parent_pos[1],
                parent_pos[2] + _support_top_m(parent),
            ]
            error_m = max(abs(child_pos[i] - expected[i]) for i in range(3))
            message = "robot mount root position verified"
        elif rel.relation in {"on_top_of", "stacked_above", "mounted_to"}:
            expected = [
                parent_pos[0],
                parent_pos[1],
                parent_pos[2] + _support_top_m(parent) + _height_m(subject) / 2.0,
            ]
            error_m = max(abs(child_pos[i] - expected[i]) for i in range(3))
            message = "support relation position verified"
        elif rel.relation == "inside":
            expected = [
                parent_pos[0],
                parent_pos[1],
                parent_pos[2] - _height_m(parent) / 2.0
                + _interior_floor_offset_m(parent)
                + _height_m(subject) / 2.0,
            ]
            error_m = max(abs(child_pos[i] - expected[i]) for i in range(3))
            message = "container relation position verified"
        elif rel.relation == "beside":
            parent_scale = _scale3(parent)
            child_scale = _scale3(subject)
            min_dx = parent_scale[0] / 2.0 + child_scale[0] / 2.0
            actual_dx = abs(child_pos[0] - parent_pos[0])
            error_m = max(0.0, min_dx - actual_dx, abs(child_pos[1] - parent_pos[1]))
            message = "beside relation clearance verified"
        else:
            message = "relation has no deterministic geometry check"

        if error_m > tolerance_m:
            status = "fail"
        checks.append({
            "subject_id": rel.subject_id,
            "relation": rel.relation,
            "object_id": rel.object_id,
            "status": status,
            "message": message,
            "error_m": round(error_m, 4),
            "expected_position": [round(v, 4) for v in expected] if expected else None,
            "actual_position": [round(v, 4) for v in child_pos] if child_pos else None,
        })

    failed = [check for check in checks if check["status"] == "fail"]
    error_diags = [diag for diag in reasoning.diagnostics if diag.severity == "error"]
    warning_diags = [diag for diag in reasoning.diagnostics if diag.severity == "warning"]
    if failed or error_diags:
        status = "fail"
    elif warning_diags:
        status = "warning"
    else:
        status = "pass"
    return {
        "status": status,
        "check_count": len(checks),
        "failed_count": len(failed),
        "checks": checks,
        "predicted_positions": predicted,
        "diagnostics": reasoning.diagnostics_as_dicts(),
    }


__all__ = [
    "NormalizedRelation",
    "RelationDiagnostic",
    "RelationReasoningResult",
    "normalize_relation_kind",
    "normalize_spatial_relations",
    "predict_relation_positions",
    "verify_relation_geometry",
    "VALID_RELATION_KINDS",
]
