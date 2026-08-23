"""Phase 63d — contact sequence telemetry aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from .execute_contact_sequence_runtime import ContactStepResult

PHASE_ID = "63d"
PHASE_TITLE = "contact sequence telemetry"
PHASE_STATUS = "landed"


@dataclass(frozen=True)
class ContactSequenceTelemetry:
    total_steps: int
    successful_steps: int
    total_duration_s: float
    peak_force_n: float
    peak_torque_nm: float
    errors: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.successful_steps / self.total_steps if self.total_steps else 0.0


def aggregate_contact_telemetry(results: Iterable[ContactStepResult]) -> ContactSequenceTelemetry:
    items = list(results)
    return ContactSequenceTelemetry(
        total_steps=len(items),
        successful_steps=sum(result.success for result in items),
        total_duration_s=sum(max(0.0, result.duration_s) for result in items),
        peak_force_n=max((abs(result.observation.observed_force_N) for result in items), default=0.0),
        peak_torque_nm=max((abs(result.observation.observed_torque_Nm) for result in items), default=0.0),
        errors=[result.error for result in items if result.error],
    )


def get_phase_metadata() -> Dict[str, Any]:
    return {"phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
            "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63d"}
