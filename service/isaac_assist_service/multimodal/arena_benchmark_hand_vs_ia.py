"""Phase 100 aggregation for hand-crafted versus IA-authored live runs."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import mean
from typing import Any, Dict, Sequence

PHASE_ID = 100
PHASE_TITLE = "Arena benchmark: hand-crafted vs IA"
PHASE_STATUS = "implemented_unvalidated"


@dataclass(frozen=True)
class ArenaRun:
    scenario_id: str
    author: str
    placement_errors_m: Sequence[float]
    scene_spawned: bool
    intent_fidelity: float
    time_to_build_s: float
    live_runtime: bool = True


def _rms(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("placement error samples are required")
    samples = tuple(float(value) for value in values)
    if any(not isfinite(value) or value < 0.0 for value in samples):
        raise ValueError("placement errors must be finite and non-negative")
    return sqrt(mean(value * value for value in samples))


def summarize_runs(runs: Sequence[ArenaRun]) -> Dict[str, Any]:
    """Compute the four metrics required by Phase 100 for one cohort."""
    if not runs:
        raise ValueError("at least one arena run is required")
    if any(not run.live_runtime for run in runs):
        raise ValueError("Phase 100 accepts live-runtime runs only")
    if any(not 0.0 <= run.intent_fidelity <= 1.0 for run in runs):
        raise ValueError("intent fidelity must be between 0 and 1")
    if any(run.time_to_build_s < 0.0 for run in runs):
        raise ValueError("time to build must be non-negative")
    return {
        "run_count": len(runs),
        "placement_rms_error_m": sqrt(mean(_rms(run.placement_errors_m) ** 2 for run in runs)),
        "scene_spawn_success_rate": mean(float(run.scene_spawned) for run in runs),
        "intent_fidelity": mean(run.intent_fidelity for run in runs),
        "mean_time_to_build_s": mean(run.time_to_build_s for run in runs),
    }


def compare_cohorts(hand_crafted: Sequence[ArenaRun], ia_authored: Sequence[ArenaRun]) -> Dict[str, Any]:
    """Return comparable Phase 100 summaries and IA-minus-baseline deltas."""
    hand = summarize_runs(hand_crafted)
    ia = summarize_runs(ia_authored)
    keys = (
        "placement_rms_error_m",
        "scene_spawn_success_rate",
        "intent_fidelity",
        "mean_time_to_build_s",
    )
    return {
        "hand_crafted": hand,
        "ia_authored": ia,
        "ia_minus_hand_crafted": {key: ia[key] - hand[key] for key in keys},
    }


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 100",
    }
