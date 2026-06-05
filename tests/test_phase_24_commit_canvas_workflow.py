"""Phase 24 — Agent confirm bar wired to workflow lifecycle.

Tests for the Python backend side of Phase 24:
  - POST /{session_id}/commit with workflow_id → approval forwarded
  - POST /{session_id}/commit without workflow_id → clean commit
  - POST /{session_id}/commit with unknown workflow_id → warning, no failure
  - POST /{session_id}/reject with workflow_id → rejection forwarded
  - POST /{session_id}/reject captures feedback in the workflow record

Gate: pytest tests/test_phase_24_commit_canvas_workflow.py
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.l1, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Helpers — build a populated MultimodalStore backed by a tmp SQLite DB.
# ---------------------------------------------------------------------------


def _make_store(tmp_path):
    """Return a fresh MultimodalStore backed by a temp DB."""
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore
    db = tmp_path / "test.db"
    return MultimodalStore(db_path=db)


def _minimal_layout_spec_dict() -> dict:
    return {
        "version": "1.0",
        "intent": {"pattern_hint": "pick_place"},
        "objects": [],
        "source": {
            "modality": "drag_drop",
            "confidence": 1.0,
        },
        "parameters": {},
    }


async def _seed_session(store, session_id: str) -> int:
    """Persist a minimal LayoutSpec and return its revision."""
    from service.isaac_assist_service.multimodal.types import LayoutSpec
    spec = LayoutSpec.model_validate(_minimal_layout_spec_dict())
    saved = await store.save_with_cas(session_id, spec, 0)
    return saved.revision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return _make_store(tmp_path)


@pytest.fixture()
def override_store(store):
    """Monkey-patch routes.get_store() to return the test store."""
    import service.isaac_assist_service.multimodal.routes as routes_mod
    original = routes_mod.get_store
    routes_mod.get_store = lambda: store
    yield store
    routes_mod.get_store = original


@pytest.fixture()
def clean_workflows():
    """Ensure _WORKFLOWS is empty before and after each test."""
    from service.isaac_assist_service.chat.tools.handlers._state import _WORKFLOWS
    _WORKFLOWS.clear()
    yield _WORKFLOWS
    _WORKFLOWS.clear()


# ---------------------------------------------------------------------------
# 1. commit_canvas WITHOUT workflow_id — clean commit, no workflow side-effect
# ---------------------------------------------------------------------------


class TestCommitCanvasNoWorkflow:

    async def test_commit_returns_committed_true(self, override_store):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_no_wf"
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(sid, routes_mod.CommitRequest())
        assert resp["committed"] is True

    async def test_commit_returns_revision(self, override_store):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_rev"
        rev = await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(sid, routes_mod.CommitRequest())
        assert resp["revision"] == rev

    async def test_commit_no_workflow_warning(self, override_store):
        """No workflow_id → no workflow_warning key in response."""
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_no_warn"
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(sid, routes_mod.CommitRequest())
        assert "workflow_warning" not in resp
        assert "workflow_approved" not in resp

    async def test_commit_emits_event(self, override_store):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_event"
        await _seed_session(override_store, sid)
        await routes_mod.commit_canvas(sid, routes_mod.CommitRequest())
        events = override_store.list_events(sid)
        assert any(e["event_type"] == "canvas_commit" for e in events)


# ---------------------------------------------------------------------------
# 2. commit_canvas WITH known workflow_id — approval forwarded
# ---------------------------------------------------------------------------


class TestCommitCanvasWithWorkflow:

    async def test_commit_approves_workflow(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_wf_ok"
        wf_id = "wf-approve-001"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id=wf_id)
        )
        assert resp["committed"] is True
        assert resp.get("workflow_approved") == wf_id
        assert "workflow_warning" not in resp

    async def test_commit_records_decision_in_workflow(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_wf_dec"
        wf_id = "wf-approve-002"
        clean_workflows[wf_id] = {
            "current_phase": "reward",
            "status": "awaiting_reward_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        await _seed_session(override_store, sid)
        await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id=wf_id)
        )
        decisions = clean_workflows[wf_id]["checkpoint_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["action"] == "approve"
        assert decisions[0]["phase"] == "reward"

    async def test_commit_sets_workflow_status(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_wf_status"
        wf_id = "wf-approve-003"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        await _seed_session(override_store, sid)
        await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id=wf_id)
        )
        assert clean_workflows[wf_id]["status"] == "approved_via_canvas"


# ---------------------------------------------------------------------------
# 3. commit_canvas with UNKNOWN workflow_id — returns warning, commit succeeds
# ---------------------------------------------------------------------------


class TestCommitCanvasUnknownWorkflow:

    async def test_commit_succeeds_with_unknown_workflow(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_unk"
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id="nonexistent-wf")
        )
        assert resp["committed"] is True

    async def test_commit_unknown_workflow_returns_warning(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_unk_warn"
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id="ghost-wf-999")
        )
        assert "workflow_warning" in resp
        assert "ghost-wf-999" in resp["workflow_warning"]

    async def test_commit_unknown_workflow_no_workflow_approved(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_unk_no_approved"
        await _seed_session(override_store, sid)
        resp = await routes_mod.commit_canvas(
            sid, routes_mod.CommitRequest(workflow_id="ghost-wf-000")
        )
        assert "workflow_approved" not in resp


# ---------------------------------------------------------------------------
# 4. reject_canvas WITH workflow_id — rejection forwarded
# ---------------------------------------------------------------------------


class TestRejectCanvasWithWorkflow:

    async def test_reject_returns_rejected_true(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_rej_ok"
        wf_id = "wf-reject-001"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        resp = await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(workflow_id=wf_id, feedback="Not good enough"),
        )
        assert resp["rejected"] is True

    async def test_reject_forwards_to_workflow(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_rej_fwd"
        wf_id = "wf-reject-002"
        clean_workflows[wf_id] = {
            "current_phase": "reward",
            "status": "awaiting_reward_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        resp = await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(workflow_id=wf_id, feedback="wrong reward"),
        )
        assert resp.get("workflow_rejected") == wf_id
        assert "workflow_warning" not in resp

    async def test_reject_sets_workflow_cancelled(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_rej_cancel"
        wf_id = "wf-reject-003"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(workflow_id=wf_id, feedback="bad plan"),
        )
        assert clean_workflows[wf_id]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 5. reject_canvas captures feedback in the workflow record
# ---------------------------------------------------------------------------


class TestRejectCanvasFeedback:

    async def test_reject_feedback_stored_in_decision(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_fb"
        wf_id = "wf-feedback-001"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        feedback_text = "The robot arm collides with the bin wall — move it 30cm right"
        await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(workflow_id=wf_id, feedback=feedback_text),
        )
        decisions = clean_workflows[wf_id]["checkpoint_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["feedback"] == feedback_text

    async def test_reject_feedback_in_response(self, override_store, clean_workflows):
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_fb_resp"
        wf_id = "wf-feedback-002"
        clean_workflows[wf_id] = {
            "current_phase": "plan",
            "status": "awaiting_plan_approval",
            "checkpoint_decisions": [],
            "events": [],
        }
        fb = "needs more shelves"
        resp = await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(workflow_id=wf_id, feedback=fb),
        )
        assert resp["feedback"] == fb

    async def test_reject_without_workflow_id_still_succeeds(self, override_store):
        """reject_canvas with no workflow_id is a valid call — no crash."""
        import service.isaac_assist_service.multimodal.routes as routes_mod
        sid = "sess_no_wf_rej"
        resp = await routes_mod.reject_canvas(
            sid,
            routes_mod.RejectCanvasRequest(feedback="local undo only"),
        )
        assert resp["rejected"] is True
        assert "workflow_warning" not in resp
        assert "workflow_rejected" not in resp
