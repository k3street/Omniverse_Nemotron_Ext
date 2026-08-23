from __future__ import annotations

import pytest
from fastapi import HTTPException

from service.isaac_assist_service.governance.decision_store import (
    clear_decisions,
    record_decision,
)
from service.isaac_assist_service.governance.models import ApprovalDecision
from service.isaac_assist_service.planner.routes import PlanOutcomeRequest, notify_applied

pytestmark = pytest.mark.l0


@pytest.fixture(autouse=True)
def clean_decisions():
    clear_decisions()
    yield
    clear_decisions()


def _outcome(**updates):
    data = {
        "plan_id": "plan-1",
        "success": True,
        "decision_id": None,
        "snapshot_id": "snapshot-real-1",
    }
    data.update(updates)
    return PlanOutcomeRequest(**data)


@pytest.mark.asyncio
async def test_success_requires_approval_decision():
    with pytest.raises(HTTPException) as exc:
        await notify_applied("plan-1", _outcome())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_rejection_cannot_authorize_apply():
    decision_id = record_decision(ApprovalDecision(request_id="plan-1", decision="rejected"))
    with pytest.raises(HTTPException) as exc:
        await notify_applied("plan-1", _outcome(decision_id=decision_id))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_decision_must_match_plan():
    decision_id = record_decision(ApprovalDecision(request_id="plan-2", decision="approved"))
    with pytest.raises(HTTPException) as exc:
        await notify_applied("plan-1", _outcome(decision_id=decision_id))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_success_requires_real_snapshot():
    decision_id = record_decision(ApprovalDecision(request_id="plan-1", decision="approved"))
    with pytest.raises(HTTPException) as exc:
        await notify_applied("plan-1", _outcome(decision_id=decision_id, snapshot_id=None))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_approved_plan_with_snapshot_is_recorded():
    decision_id = record_decision(ApprovalDecision(request_id="plan-1", decision="approved"))
    result = await notify_applied("plan-1", _outcome(decision_id=decision_id))
    assert result == {"status": "success", "snapshot_id": "snapshot-real-1"}
