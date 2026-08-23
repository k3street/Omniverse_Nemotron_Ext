"""Thread-safe, process-local approval decisions for pending patch plans."""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional
from uuid import uuid4

from .models import ApprovalDecision

_DECISIONS: Dict[str, ApprovalDecision] = {}
_LOCK = Lock()


def record_decision(decision: ApprovalDecision) -> str:
    decision_id = str(uuid4())
    with _LOCK:
        _DECISIONS[decision_id] = decision.model_copy(deep=True)
    return decision_id


def get_decision(decision_id: str) -> Optional[ApprovalDecision]:
    with _LOCK:
        decision = _DECISIONS.get(decision_id)
        return decision.model_copy(deep=True) if decision else None


def clear_decisions() -> None:
    """Test helper; production decisions live for the process lifetime."""
    with _LOCK:
        _DECISIONS.clear()
