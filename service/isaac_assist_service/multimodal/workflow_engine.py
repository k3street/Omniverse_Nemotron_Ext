"""Phase 33 — workflow engine: generalize lifecycle ops.

Refactors workflow.py:start/approve/reject/revise handlers around a
typed WorkflowEngine class that owns transitions + audit log.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 33.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkflowPhaseStatus(str, Enum):
    """String-valued enum of all legal workflow lifecycle states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    REVISED = "revised"
    CANCELLED = "cancelled"


@dataclass
class WorkflowEvent:
    """An immutable record of one workflow lifecycle transition."""
    event_type: str
    timestamp: str
    payload: Dict[str, Any] = field(default_factory=dict)


class WorkflowEngine:
    """Typed lifecycle transitions for a workflow record."""

    def __init__(self) -> None:
        """Initialise the engine with an empty event log."""
        self.events: List[WorkflowEvent] = []

    def transition(self, current: WorkflowPhaseStatus,
                   action: str) -> WorkflowPhaseStatus:
        """Apply a transition. Returns new status or raises ValueError."""
        valid = {
            WorkflowPhaseStatus.PENDING: {"start": WorkflowPhaseStatus.IN_PROGRESS,
                                           "cancel": WorkflowPhaseStatus.CANCELLED},
            WorkflowPhaseStatus.IN_PROGRESS: {"checkpoint": WorkflowPhaseStatus.AWAITING_APPROVAL,
                                                "cancel": WorkflowPhaseStatus.CANCELLED,
                                                "complete": WorkflowPhaseStatus.COMPLETED},
            WorkflowPhaseStatus.AWAITING_APPROVAL: {"approve": WorkflowPhaseStatus.COMPLETED,
                                                     "reject": WorkflowPhaseStatus.REJECTED,
                                                     "revise": WorkflowPhaseStatus.REVISED},
            WorkflowPhaseStatus.REVISED: {"start": WorkflowPhaseStatus.IN_PROGRESS},
        }
        next_status = valid.get(current, {}).get(action)
        if next_status is None:
            raise ValueError(f"Invalid transition: {current} + {action}")
        self.events.append(WorkflowEvent(
            event_type=f"{current.value}->{next_status.value}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={"action": action},
        ))
        return next_status
