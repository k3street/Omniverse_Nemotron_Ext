"""Phase 79 whole-body-control acceptance evidence.

The controller/configuration implementation lives in the robot and training
handlers.  This module owns the phase's measurable acceptance gate: a Unitree
G1 must replay a recorded manipulation demonstration with less than 5 cm RMS
end-effector position error in a live Isaac Lab run.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Dict, Iterable, Sequence

PHASE_ID = 79
PHASE_TITLE = "Whole-body control humanoid"
PHASE_STATUS = "implemented_unvalidated"
MAX_RMS_ERROR_M = 0.05


@dataclass(frozen=True)
class WBCReplayEvidence:
    robot: str
    demo_id: str
    position_errors_m: Sequence[float]
    live_runtime: bool
    controller_spawned: bool
    completed: bool


def rms_position_error(errors_m: Iterable[float]) -> float:
    """Return RMS position error, rejecting empty or invalid evidence."""
    values = tuple(float(value) for value in errors_m)
    if not values:
        raise ValueError("at least one position-error sample is required")
    if any(not isfinite(value) or value < 0.0 for value in values):
        raise ValueError("position-error samples must be finite and non-negative")
    return sqrt(sum(value * value for value in values) / len(values))


def evaluate_replay(evidence: WBCReplayEvidence) -> Dict[str, Any]:
    """Evaluate the exact Phase 79 live G1 replay acceptance gate."""
    rms_m = rms_position_error(evidence.position_errors_m)
    reasons = []
    if evidence.robot.strip().lower() not in {"unitree g1", "unitree_g1", "g1"}:
        reasons.append("fixture robot is not Unitree G1")
    if not evidence.live_runtime:
        reasons.append("evidence was not collected from a live runtime")
    if not evidence.controller_spawned:
        reasons.append("whole-body controller did not spawn")
    if not evidence.completed:
        reasons.append("recorded demonstration replay did not complete")
    if rms_m >= MAX_RMS_ERROR_M:
        reasons.append(f"RMS error {rms_m:.6f} m is not below {MAX_RMS_ERROR_M:.2f} m")
    return {
        "accepted": not reasons,
        "rms_error_m": rms_m,
        "threshold_m": MAX_RMS_ERROR_M,
        "sample_count": len(evidence.position_errors_m),
        "reasons": reasons,
    }


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 79",
        "acceptance_gate": "live Unitree G1 replay with RMS error < 0.05 m",
    }
