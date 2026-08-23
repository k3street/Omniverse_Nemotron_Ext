"""Phase 63c — contact pose candidate validation and fusion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple

PHASE_ID = "63c"
PHASE_TITLE = "contact sequence pose estimation"
PHASE_STATUS = "landed"

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class ContactPoseCandidate:
    position_m: Vec3
    surface_normal: Vec3
    confidence: float
    source: str

    def __post_init__(self) -> None:
        values = (*self.position_m, *self.surface_normal, self.confidence)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("pose candidate values must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True)
class ContactPoseEstimate:
    position_m: Vec3
    surface_normal: Vec3
    confidence: float
    candidate_count: int


def _unit(vector: Vec3) -> Vec3:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        raise ValueError("surface normal cannot be zero")
    return tuple(value / norm for value in vector)  # type: ignore[return-value]


def estimate_contact_pose(candidates: Iterable[ContactPoseCandidate]) -> ContactPoseEstimate:
    """Fuse candidates using confidence weights and normalize the final normal."""
    items = list(candidates)
    if not items:
        raise ValueError("at least one pose candidate is required")
    weight = sum(item.confidence for item in items)
    if weight <= 0:
        raise ValueError("candidate confidence sum must be positive")
    position = tuple(
        sum(item.position_m[axis] * item.confidence for item in items) / weight
        for axis in range(3)
    )
    normal = _unit(tuple(
        sum(_unit(item.surface_normal)[axis] * item.confidence for item in items)
        for axis in range(3)
    ))
    confidence = min(1.0, weight / len(items))
    return ContactPoseEstimate(position, normal, confidence, len(items))  # type: ignore[arg-type]


def get_phase_metadata() -> Dict[str, Any]:
    return {"phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
            "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63c"}
