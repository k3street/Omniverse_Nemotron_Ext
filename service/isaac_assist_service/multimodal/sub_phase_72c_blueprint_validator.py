"""Phase 72c — blueprint validator.

Validates a scene-blueprint dict produced by `spec_to_blueprint` (Phase 27)
or loaded from `workspace/scene_blueprints/`. Four checks are run in order:

1. **Required fields** — `name`, `objects`, `physics_settings` must be present.
   Missing → hard ERROR (`blueprint.missing_required_field`).
2. **Invalid object_class** — each object's `asset_name` must be a key in
   `PALETTE`. Unknown class → hard ERROR (`blueprint.unknown_object_class`).
3. **AABB overlap** — pairs of objects whose XY footprints intersect → soft
   WARNING (`blueprint.aabb_overlap`). Uses half-extents from `PALETTE`.
4. **Room bounds** (soft) — if `room_dims` is present, any object whose
   position lies outside [0, room_x] × [0, room_y] → soft WARNING
   (`blueprint.object_out_of_room`).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 72c.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

from service.isaac_assist_service.types.violations import (
    ConstraintViolation,
    ValidationResult,
)
from service.isaac_assist_service.types.uncertainty import GradedScale
from service.isaac_assist_service.multimodal.object_palette import PALETTE

PHASE_ID = "72c"
PHASE_TITLE = "blueprint validator"
PHASE_STATUS = "landed"

__all__ = [
    "BlueprintValidator",
    "get_phase_metadata",
    "PHASE_ID",
    "PHASE_TITLE",
    "PHASE_STATUS",
]

_REQUIRED_FIELDS = ("name", "objects", "physics_settings")


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 72c",
    }


def _aabb_xy(
    obj: Dict[str, Any], footprint_xy_m: Tuple[float, float]
) -> Tuple[float, float, float, float]:
    """Return (x1, y1, x2, y2) AABB in XY for *obj*.

    *obj* must have a `position` list/tuple with at least 2 elements
    (x, y).  The footprint is the full size in metres; the AABB is
    centred on the object's position.

    Returns (x1, y1, x2, y2) where x1 < x2, y1 < y2.
    """
    pos = obj.get("position", [0.0, 0.0, 0.0])
    cx = float(pos[0]) if len(pos) > 0 else 0.0
    cy = float(pos[1]) if len(pos) > 1 else 0.0
    hw = footprint_xy_m[0] / 2.0
    hd = footprint_xy_m[1] / 2.0
    return (cx - hw, cy - hd, cx + hw, cy + hd)


def _aabbs_overlap(a: Tuple[float, float, float, float],
                   b: Tuple[float, float, float, float]) -> bool:
    """Return True if two XY AABBs overlap (strict interior intersection).

    Touching edges are *not* considered overlapping (open intervals).
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    # No overlap if one box is fully to the left/right/above/below the other.
    if ax2 <= bx1 or bx2 <= ax1:
        return False
    if ay2 <= by1 or by2 <= ay1:
        return False
    return True


class BlueprintValidator:
    """Validate a scene-blueprint dict.

    Usage::

        bv = BlueprintValidator()
        result = bv.validate(blueprint)
        if not result.valid:
            for v in result.violations:
                print(v.message)
    """

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def validate(self, blueprint: Dict[str, Any]) -> ValidationResult:
        """Run all four checks and return an aggregate ValidationResult."""
        violations: List[ConstraintViolation] = []
        violations.extend(self._check_required_fields(blueprint))
        # Only run further checks when required fields are present.
        if not any(v.constraint_id == "blueprint.missing_required_field"
                   and v.diagnostics.get("field") in ("objects",)
                   for v in violations):
            violations.extend(self._check_unknown_object_class(blueprint))
            violations.extend(self._check_aabb_overlap(blueprint))
        violations.extend(self._check_room_bounds(blueprint))
        return ValidationResult.from_violations(violations)

    # ------------------------------------------------------------------
    # Check 1 — required fields
    # ------------------------------------------------------------------

    def _check_required_fields(
        self, blueprint: Dict[str, Any]
    ) -> List[ConstraintViolation]:
        vs: List[ConstraintViolation] = []
        for field in _REQUIRED_FIELDS:
            if field not in blueprint:
                vs.append(
                    ConstraintViolation(
                        constraint_id="blueprint.missing_required_field",
                        category="hard",
                        severity=GradedScale.ERROR,
                        message=(
                            f"Blueprint is missing required field '{field}'."
                        ),
                        affected_paths=[],
                        diagnostics={"field": field},
                        fix_hint=f"Add the '{field}' key to the blueprint dict.",
                    )
                )
        return vs

    # ------------------------------------------------------------------
    # Check 2 — unknown object_class / asset_name
    # ------------------------------------------------------------------

    def _check_unknown_object_class(
        self, blueprint: Dict[str, Any]
    ) -> List[ConstraintViolation]:
        vs: List[ConstraintViolation] = []
        objects = blueprint.get("objects", [])
        if not isinstance(objects, list):
            return vs
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            asset_name = obj.get("asset_name", "")
            if asset_name not in PALETTE:
                prim_path = obj.get("prim_path", "")
                vs.append(
                    ConstraintViolation(
                        constraint_id="blueprint.unknown_object_class",
                        category="hard",
                        severity=GradedScale.ERROR,
                        message=(
                            f"Object '{obj.get('name', prim_path)}' references"
                            f" unknown asset_name '{asset_name}'."
                        ),
                        affected_paths=[prim_path] if prim_path else [],
                        diagnostics={
                            "asset_name": asset_name,
                            "known_count": len(PALETTE),
                        },
                        fix_hint=(
                            f"Replace '{asset_name}' with a valid PALETTE key."
                        ),
                    )
                )
        return vs

    # ------------------------------------------------------------------
    # Check 3 — AABB overlap (XY footprint)
    # ------------------------------------------------------------------

    def _check_aabb_overlap(
        self, blueprint: Dict[str, Any]
    ) -> List[ConstraintViolation]:
        vs: List[ConstraintViolation] = []
        objects = blueprint.get("objects", [])
        if not isinstance(objects, list):
            return vs

        # Build list of (obj, aabb) only for objects with known palette entries.
        obj_aabbs: List[Tuple[Dict[str, Any], Tuple[float, float, float, float]]] = []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            asset_name = obj.get("asset_name", "")
            palette_entry = PALETTE.get(asset_name)
            if palette_entry is None:
                continue  # unknown — already flagged by check 2
            footprint = palette_entry.footprint_xy_m
            # Skip zero-footprint objects (lights, virtual markers).
            if footprint[0] == 0.0 and footprint[1] == 0.0:
                continue
            aabb = _aabb_xy(obj, footprint)
            obj_aabbs.append((obj, aabb))

        for (obj_a, aabb_a), (obj_b, aabb_b) in combinations(obj_aabbs, 2):
            if _aabbs_overlap(aabb_a, aabb_b):
                path_a = obj_a.get("prim_path", obj_a.get("name", "?"))
                path_b = obj_b.get("prim_path", obj_b.get("name", "?"))
                vs.append(
                    ConstraintViolation(
                        constraint_id="blueprint.aabb_overlap",
                        category="soft",
                        severity=GradedScale.WARNING,
                        message=(
                            f"Objects '{path_a}' and '{path_b}' have overlapping"
                            f" XY footprints."
                        ),
                        affected_paths=[path_a, path_b],
                        diagnostics={
                            "aabb_a": aabb_a,
                            "aabb_b": aabb_b,
                        },
                        fix_hint=(
                            "Separate the two objects so their footprints do not"
                            " overlap."
                        ),
                    )
                )
        return vs

    # ------------------------------------------------------------------
    # Check 4 — room bounds (soft)
    # ------------------------------------------------------------------

    def _check_room_bounds(
        self, blueprint: Dict[str, Any]
    ) -> List[ConstraintViolation]:
        vs: List[ConstraintViolation] = []
        room_dims = blueprint.get("room_dims")
        if room_dims is None:
            return vs
        if not (isinstance(room_dims, (list, tuple)) and len(room_dims) >= 2):
            return vs

        room_x = float(room_dims[0])
        room_y = float(room_dims[1])

        objects = blueprint.get("objects", [])
        if not isinstance(objects, list):
            return vs

        for obj in objects:
            if not isinstance(obj, dict):
                continue
            pos = obj.get("position", [0.0, 0.0, 0.0])
            if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
                continue
            ox = float(pos[0])
            oy = float(pos[1])
            if ox < 0.0 or ox > room_x or oy < 0.0 or oy > room_y:
                prim_path = obj.get("prim_path", obj.get("name", "?"))
                vs.append(
                    ConstraintViolation(
                        constraint_id="blueprint.object_out_of_room",
                        category="soft",
                        severity=GradedScale.WARNING,
                        message=(
                            f"Object '{prim_path}' at ({ox:.2f}, {oy:.2f}) is"
                            f" outside room bounds ({room_x:.2f} × {room_y:.2f})."
                        ),
                        affected_paths=[prim_path],
                        diagnostics={
                            "position_x": ox,
                            "position_y": oy,
                            "room_x": room_x,
                            "room_y": room_y,
                        },
                        fix_hint=(
                            f"Move the object inside the room bounds"
                            f" (0–{room_x:.2f} × 0–{room_y:.2f})."
                        ),
                    )
                )
        return vs
