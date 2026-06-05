"""
FastAPI routes for the multimodal canvas modality.

Per spec §9.6:
    GET    /api/v1/canvas/{session_id}                   load LayoutSpec
    POST   /api/v1/canvas/{session_id}/patch             CAS-guarded mutation
    POST   /api/v1/canvas/{session_id}/commit            proposed → committed
    POST   /api/v1/canvas/{session_id}/preview_render    render + emit SSE
    POST   /api/v1/canvas/{session_id}/build             ratify + execute
    DELETE /api/v1/canvas/{session_id}                   reset session
    POST   /api/v1/canvas/{session_id}/sync_from_stage   read Kit RPC stage
                                                         → LayoutSpec
    POST   /api/v1/canvas/{session_id}/client_error      frontend error report

The canvas SPA in the browser tab is the primary client. The Kit
canvas-mirror panel reads the rendered PNG from disk on SSE
`canvas/preview_updated`.

Block 1A.3 scope: backend routes are wired against the multimodal
foundation (persistence + ratify + render). The actual Konva SPA is a
separate Vite project under web/floor-plan-ui/.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .persistence import (
    DEFAULT_DB_PATH,
    MultimodalStore,
    RevisionConflictError,
)
from .ratify import ratify
from .render import render_layout_spec_to_file
from .types import LayoutSpec
from .validate import validate_layout_spec

logger = logging.getLogger(__name__)

# Routes mounted at /api/v1/canvas via include_router prefix in main.py.
router = APIRouter(tags=["canvas"])


# Singleton store; lazily instantiated. Same store as multimodal_handlers
# (process-wide). For test isolation, override _store via dependency-inject.
_store: Optional[MultimodalStore] = None


def get_store() -> MultimodalStore:
    global _store
    if _store is None:
        _store = MultimodalStore()
    return _store


# Default preview output directory — kept under workspace/ which is gitignored.
def _preview_path(session_id: str) -> Path:
    base = DEFAULT_DB_PATH.parent / "previews"
    return base / f"{session_id}.png"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class PatchRequest(BaseModel):
    """One CAS-guarded mutation request. The full LayoutSpec to persist is
    constructed by the client and sent here; backend validates + persists."""
    spec: Dict[str, Any]
    parent_revision: int = Field(ge=0)


class CommitRequest(BaseModel):
    """Commit request — optionally carries a workflow_id for Phase 24 wiring.

    If workflow_id is present, the commit will additionally forward an
    approve decision to the workflow lifecycle via
    approve_workflow_checkpoint.  A missing / unknown workflow_id never
    blocks the commit — it returns a workflow_warning in the response.
    """
    workflow_id: Optional[str] = Field(
        default=None,
        description="Active workflow to approve after committing the canvas.",
    )


class RejectCanvasRequest(BaseModel):
    """Reject a canvas proposal and optionally forward the rejection to a
    workflow checkpoint.

    workflow_id: the workflow to reject.  Must be present for the workflow
        action to fire; if missing, the request is a no-op on the workflow
        side (the route still returns 200 so the caller doesn't need to
        guard).
    feedback: free-text reason forwarded verbatim to the workflow record.
    """
    workflow_id: Optional[str] = Field(default=None)
    feedback: str = Field(default="", description="User feedback / rejection reason.")


class BuildRequest(BaseModel):
    template_id: Optional[str] = None
    force_freeform: bool = False


class ClientErrorReport(BaseModel):
    message: str
    stack: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{session_id}")
async def get_canvas(session_id: str) -> Dict[str, Any]:
    """Return the latest LayoutSpec for the session, or a blank skeleton if
    none exists."""
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        return {
            "session_id": session_id,
            "spec": None,
            "revision": 0,
        }
    return {
        "session_id": session_id,
        "spec": spec.model_dump(mode="json"),
        "revision": spec.revision,
    }


@router.post("/{session_id}/patch")
async def patch_canvas(session_id: str, body: PatchRequest) -> Dict[str, Any]:
    """CAS-guarded save. Mismatch → 409 Conflict with current spec attached
    so the client can run its three-way merge UI without an extra round-trip.
    """
    store = get_store()

    try:
        new_spec = LayoutSpec.model_validate(body.spec)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "spec failed Pydantic validation",
                    "exception": str(e)},
        )

    # Cross-field validation (registry membership, feature consistency)
    validation = validate_layout_spec(new_spec)
    if not validation.valid:
        return {
            "valid": False,
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in validation.issues
            ],
        }

    try:
        saved = await store.save_with_cas(
            session_id, new_spec, body.parent_revision,
        )
    except RevisionConflictError as e:
        # 409 with structured payload for client-side merge UI
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "conflict": True,
                "expected_revision": e.expected,
                "actual_revision": e.actual,
                "current_spec": (
                    e.current_spec.model_dump(mode="json") if e.current_spec else None
                ),
            },
        )

    store.append_event(session_id, "canvas_patch", {
        "revision": saved.revision,
        "warnings": [
            {"code": i.code, "message": i.message}
            for i in validation.warnings
        ],
    })

    return {
        "valid": True,
        "revision": saved.revision,
        "spec": saved.model_dump(mode="json"),
    }


def _forward_workflow_approve(
    workflow_id: str,
    feedback: str = "canvas confirmed",
) -> Optional[str]:
    """Forward an approve decision to the in-process workflow registry.

    Returns None on success, or a warning string if the workflow_id is
    not found (callers return 200 with workflow_warning in this case).

    Intentionally does NOT raise — a missing workflow must never block the
    canvas commit from returning a success response.
    """
    try:
        from ..chat.tools.handlers._state import _WORKFLOWS  # noqa: PLC0415
        wf = _WORKFLOWS.get(workflow_id)
        if wf is None:
            return f"workflow_id '{workflow_id}' not found in active registry"
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        now = _dt.now(_tz.utc).isoformat()
        decision = {
            "phase": wf.get("current_phase", "unknown"),
            "action": "approve",
            "feedback": feedback,
            "at": now,
        }
        wf.setdefault("checkpoint_decisions", []).append(decision)
        wf.setdefault("events", []).append({"type": "checkpoint_decision", **decision})
        wf["updated_at"] = now
        wf["status"] = "approved_via_canvas"
        logger.info(
            f"[canvas-commit] forwarded approve to workflow {workflow_id!r}, "
            f"phase={decision['phase']!r}"
        )
        return None
    except Exception as exc:  # pragma: no cover — guard against import errors
        logger.warning(f"[canvas-commit] workflow approve forward failed: {exc}")
        return str(exc)


def _forward_workflow_reject(
    workflow_id: str,
    feedback: str,
) -> Optional[str]:
    """Forward a reject decision to the in-process workflow registry.

    Same soft-failure semantics as _forward_workflow_approve — never raises.
    """
    try:
        from ..chat.tools.handlers._state import _WORKFLOWS  # noqa: PLC0415
        wf = _WORKFLOWS.get(workflow_id)
        if wf is None:
            return f"workflow_id '{workflow_id}' not found in active registry"
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        now = _dt.now(_tz.utc).isoformat()
        decision = {
            "phase": wf.get("current_phase", "unknown"),
            "action": "reject",
            "feedback": feedback,
            "at": now,
        }
        wf.setdefault("checkpoint_decisions", []).append(decision)
        wf.setdefault("events", []).append({"type": "checkpoint_decision", **decision})
        wf["updated_at"] = now
        wf["status"] = "cancelled"
        logger.info(
            f"[canvas-reject] forwarded reject to workflow {workflow_id!r}, "
            f"feedback={feedback!r}"
        )
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[canvas-reject] workflow reject forward failed: {exc}")
        return str(exc)


@router.post("/{session_id}/commit")
async def commit_canvas(session_id: str, body: CommitRequest) -> Dict[str, Any]:
    """Mark the current LayoutSpec as committed.

    Phase 24 extension: if body.workflow_id is present, forward an approve
    decision to the in-process workflow registry after committing the canvas.
    An unknown workflow_id never blocks the commit — it surfaces as
    workflow_warning in the response instead.

    The proposed/committed distinction lives in the SPA UI state — backend
    emits a telemetry event for downstream consumers (canvas-mirror panel
    transition, auto-build trigger).
    """
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LayoutSpec to commit",
        )
    store.append_event(session_id, "canvas_commit", {
        "revision": spec.revision,
        "workflow_id": body.workflow_id,
    })

    response: Dict[str, Any] = {"committed": True, "revision": spec.revision}

    if body.workflow_id:
        warning = _forward_workflow_approve(body.workflow_id)
        if warning:
            response["workflow_warning"] = warning
        else:
            response["workflow_approved"] = body.workflow_id

    return response


@router.post("/{session_id}/reject")
async def reject_canvas(session_id: str, body: RejectCanvasRequest) -> Dict[str, Any]:
    """Reject a proposed canvas mutation and optionally forward the rejection
    to an active workflow checkpoint.

    Phase 24: the ConfirmBar calls this on "Reject" when a workflow is
    active.  If workflow_id is absent the route still returns 200 — the
    rejection is purely local (the SPA undoes the BulkUpdate command).

    feedback is forwarded verbatim to the workflow record so the LLM can
    regenerate the proposal with the user's guidance.
    """
    store = get_store()
    store.append_event(session_id, "canvas_reject", {
        "workflow_id": body.workflow_id,
        "feedback": body.feedback,
    })

    response: Dict[str, Any] = {"rejected": True, "feedback": body.feedback}

    if body.workflow_id:
        warning = _forward_workflow_reject(body.workflow_id, body.feedback)
        if warning:
            response["workflow_warning"] = warning
        else:
            response["workflow_rejected"] = body.workflow_id

    return response


@router.post("/{session_id}/preview_render")
async def preview_render(session_id: str) -> Dict[str, Any]:
    """Render the current LayoutSpec to a PNG snapshot. The Kit canvas-mirror
    panel reloads its `ui.Image(path)` on the SSE `canvas/preview_updated`
    event this emits."""
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LayoutSpec to render",
        )
    out_path = _preview_path(session_id)
    render_layout_spec_to_file(spec, out_path)
    store.append_event(session_id, "canvas_preview_updated", {
        "revision": spec.revision,
        "path": str(out_path),
    })
    return {
        "rendered": True,
        "revision": spec.revision,
        "path": str(out_path),
    }


@router.post("/{session_id}/build")
async def build_canvas(session_id: str, body: BuildRequest) -> Dict[str, Any]:
    """Ratify the LayoutSpec against the (matched or specified) template
    and report ratify status. Actual Kit RPC execution is the existing
    canonical-instantiator flow — wired in Block 1B alongside role-based
    template refactor."""
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LayoutSpec to build from",
        )

    if body.force_freeform:
        store.append_event(session_id, "canvas_build", {
            "revision": spec.revision,
            "path": "freeform_t5",
        })
        return {
            "ratified": False,
            "path": "freeform_t5",
            "next_step": (
                "agent should fall to free-form planning against this "
                "LayoutSpec; user requested force_freeform"
            ),
        }

    template = {"id": body.template_id or "<unspecified>"}
    result = ratify(template, spec)

    payload = {
        "ratified": result.status == "ok",
        "status": result.status,
        "diagnostics": [
            {"role": d.role_name, "object_id": d.object_id,
             "decision": d.decision, "reason": d.reason}
            for d in result.diagnostics
        ],
        "errors": [
            {"kind": e.kind, "role_name": e.role_name,
             "expected": e.expected, "got": e.got,
             "diagnosis": e.diagnosis}
            for e in result.errors
        ],
        "revision": spec.revision,
    }

    if result.status == "ok":
        payload["bindings"] = {
            role: {"object_id": b.object_id, "source": b.source}
            for role, b in result.bindings.items()
        }
    elif result.status == "needs_choice":
        payload["ambiguous_roles"] = [
            {"role": a.role_name,
             "candidates": a.candidate_object_ids,
             "constraints": a.role_constraints}
            for a in result.ambiguous_roles
        ]

    store.append_event(session_id, "canvas_build", {
        "revision": spec.revision,
        "ratify_status": result.status,
        "template_id": body.template_id,
    })
    return payload


@router.delete("/{session_id}")
async def delete_canvas(session_id: str) -> Dict[str, Any]:
    """Reset the session — delete all LayoutSpec history, bindings,
    build_log entries, and events for this session."""
    store = get_store()
    removed = store.delete_session(session_id)
    return {"deleted": True, "removed_revisions": removed}


@router.post("/{session_id}/client_error")
async def client_error(
    session_id: str, body: ClientErrorReport,
) -> Dict[str, Any]:
    """Frontend error reporter — browser SPA POSTs window.onerror /
    unhandledrejection here. Logged to events table + service log."""
    store = get_store()
    payload = {"message": body.message}
    if body.stack:
        payload["stack"] = body.stack[:4096]
    if body.context:
        payload["context"] = body.context
    store.append_event(session_id, "canvas_client_error", payload)
    logger.warning(
        f"[canvas-client-error] session={session_id}: {body.message}"
    )
    return {"logged": True}


@router.get("/{session_id}/build/{build_id}")
async def get_build_status(session_id: str, build_id: str) -> Dict[str, Any]:
    """Return current state of a build — used by the SPA + canvas-mirror
    panel to render the per-tool progress (`canvas_build_progress` events
    arrive via SSE; this is the polling fallback)."""
    store = get_store()
    b = store.get_build(build_id)
    if b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown build_id {build_id!r}",
        )
    if b["session_id"] != session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="build does not belong to this session",
        )
    return b
