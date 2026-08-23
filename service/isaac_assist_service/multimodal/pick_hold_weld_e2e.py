"""Phase 99 live-run acceptance evaluator for the pick-hold-weld demo."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

PHASE_ID = 99
PHASE_TITLE = "Pick-hold-weld scenario end-to-end"
PHASE_STATUS = "implemented_unvalidated"


@dataclass(frozen=True)
class PickHoldWeldRun:
    live_runtime: bool
    layout_committed: bool
    plan_approved: bool
    spawned_robot_count: int
    diagnosis_ran: bool
    controller_installed: bool
    simulated_seconds: float
    weld_fired_at_contact: bool
    workpiece_landed: bool
    analytical_cycle_time_s: float
    simulated_cycle_time_s: float
    analytical_energy_j: float
    simulated_energy_j: float
    dataset_captured: bool


def _relative_gap(reference: float, observed: float) -> float:
    if reference <= 0.0:
        raise ValueError("analytical reference values must be positive")
    return abs(observed - reference) / reference


def evaluate_run(run: PickHoldWeldRun) -> Dict[str, Any]:
    """Apply the Phase 99 success criteria to collected runtime evidence."""
    cycle_gap = _relative_gap(run.analytical_cycle_time_s, run.simulated_cycle_time_s)
    energy_gap = _relative_gap(run.analytical_energy_j, run.simulated_energy_j)
    gates = {
        "live_runtime": run.live_runtime,
        "layout_committed": run.layout_committed,
        "plan_approved": run.plan_approved,
        "robots_spawned": run.spawned_robot_count >= 2,
        "diagnosis_ran": run.diagnosis_ran,
        "controller_installed": run.controller_installed,
        "simulated_60s": run.simulated_seconds >= 60.0,
        "weld_at_contact": run.weld_fired_at_contact,
        "workpiece_landed": run.workpiece_landed,
        "cycle_gap_within_10pct": cycle_gap <= 0.10,
        "energy_gap_within_10pct": energy_gap <= 0.10,
        "dataset_captured": run.dataset_captured,
    }
    return {
        "accepted": all(gates.values()),
        "gates": gates,
        "cycle_time_gap": cycle_gap,
        "energy_gap": energy_gap,
        "failed_gates": [name for name, passed in gates.items() if not passed],
    }


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 99",
    }
