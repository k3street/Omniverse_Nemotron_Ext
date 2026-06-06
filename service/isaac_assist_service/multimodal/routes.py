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

import base64
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
from .cosmos3_adapter import (
    CosmosSceneObservation,
    cosmos_observation_to_layout_spec,
)
from .cosmos3_runtime import (
    CosmosRuntimeError,
    build_cosmos3_reasoner,
)
from .asset_resolution import resolve_layout_assets
from .instantiator import instantiate
from .ratify import ratify
from .render import render_layout_spec_to_file
from .scenario_campaign import build_campaign_plan, materialize_campaign
from .types import LayoutSpec
from .validate import validate_layout_spec
from .relation_reasoning import normalize_spatial_relations

logger = logging.getLogger(__name__)

# Routes mounted at /api/v1/canvas via include_router prefix in main.py.
router = APIRouter(tags=["canvas"])


# Singleton store; lazily instantiated. Same store as multimodal_handlers
# (process-wide). For test isolation, override _store via dependency-inject.
_store: Optional[MultimodalStore] = None


def get_store() -> MultimodalStore:
    """Return the process-wide singleton :class:`MultimodalStore`, creating it on first call."""
    global _store
    if _store is None:
        _store = MultimodalStore()
    return _store


# Default preview output directory — kept under workspace/ which is gitignored.
def _preview_path(session_id: str) -> Path:
    """Return the PNG preview file path for a session under ``workspace/previews/``."""
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
    """Request body for the canvas ``/build`` endpoint.

    Attributes:
        template_id: Optional canonical template to instantiate.
        force_freeform: When True, skip template lookup and generate a freeform canvas.
        dry_run: When True, return generated Kit code without mutating Isaac Sim.
    """

    template_id: Optional[str] = None
    force_freeform: bool = False
    dry_run: bool = True


class CampaignPlanRequest(BaseModel):
    """Request a deterministic scenario-variant execution plan."""

    workspace_root: Optional[str] = Field(default=None)


class CosmosProposalRequest(BaseModel):
    """Save a Cosmos 3 Reasoner scene observation as a canvas proposal."""

    observation: CosmosSceneObservation
    parent_revision: int = Field(default=0, ge=0)


class CosmosObserveRequest(BaseModel):
    """Call a Cosmos Reasoner runtime and save the resulting canvas proposal."""

    prompt: str = Field(default="Reconstruct this robotics scene.")
    image_base64: Optional[str] = Field(default=None)
    mime_type: str = Field(default="image/png")
    input_kind: str = Field(default="photo")
    parent_revision: int = Field(default=0, ge=0)


class CosmosViewportObserveRequest(BaseModel):
    """Capture the live Isaac viewport, call Cosmos, and save a proposal."""

    prompt: str = Field(default="Reconstruct the current Isaac Sim viewport as a robotics floor plan.")
    max_dim: int = Field(default=1280, ge=64, le=4096)
    parent_revision: int = Field(default=0, ge=0)


class ClientErrorReport(BaseModel):
    """Error report submitted by the UI client for server-side logging.

    Attributes:
        message: Human-readable error description.
        stack: Optional JavaScript / Python stack trace string.
        context: Optional extra key/value pairs for diagnostics.
    """

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


@router.post("/{session_id}/cosmos/propose")
async def propose_canvas_from_cosmos(
    session_id: str,
    body: CosmosProposalRequest,
) -> Dict[str, Any]:
    """Convert a Cosmos 3 observation into a CAS-guarded LayoutSpec proposal.

    The route accepts already-structured Cosmos output.  Actual Cosmos runtime
    invocation belongs upstream so the canvas API stays deterministic and
    testable across Isaac Sim 5.1 and 6.0.
    """
    store = get_store()
    spec = cosmos_observation_to_layout_spec(
        body.observation,
        session_id=session_id,
    )
    relation_reasoning = normalize_spatial_relations(spec)

    validation = validate_layout_spec(spec)
    if not validation.valid:
        return {
            "valid": False,
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in validation.issues
            ],
        }

    try:
        saved = await store.save_with_cas(session_id, spec, body.parent_revision)
    except RevisionConflictError as e:
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

    store.append_event(session_id, "cosmos_canvas_proposal", {
        "revision": saved.revision,
        "input_kind": body.observation.input_kind,
        "object_count": len(saved.objects or []),
        "relation_diagnostics": relation_reasoning.diagnostics_as_dicts(),
    })
    return {
        "valid": True,
        "revision": saved.revision,
        "spec": saved.model_dump(mode="json"),
        "relation_diagnostics": relation_reasoning.diagnostics_as_dicts(),
    }


@router.post("/{session_id}/cosmos/observe")
async def observe_canvas_from_cosmos(
    session_id: str,
    body: CosmosObserveRequest,
) -> Dict[str, Any]:
    """Call Cosmos 3 Reasoner, then save the inferred LayoutSpec proposal."""
    image_bytes: Optional[bytes] = None
    if body.image_base64:
        raw = body.image_base64
        if "," in raw and raw.lstrip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"image_base64 is not valid base64: {exc}",
            )

    reasoner = build_cosmos3_reasoner()
    try:
        observation = await reasoner.observe_scene(
            prompt=body.prompt,
            image_bytes=image_bytes,
            mime_type=body.mime_type,
            input_kind=body.input_kind,
        )
    except CosmosRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    proposal = CosmosProposalRequest(
        observation=observation,
        parent_revision=body.parent_revision,
    )
    response = await propose_canvas_from_cosmos(session_id, proposal)
    response["observation"] = observation.model_dump(mode="json")
    return response


@router.post("/{session_id}/cosmos/observe_viewport")
async def observe_canvas_from_viewport(
    session_id: str,
    body: CosmosViewportObserveRequest,
) -> Dict[str, Any]:
    """Capture the active Isaac viewport and infer a floor-plan proposal."""
    from ..chat.tools import kit_tools  # noqa: PLC0415

    capture = await kit_tools.get_viewport_image(max_dim=body.max_dim)
    image_b64 = (
        capture.get("image_b64")
        or capture.get("image_base64")
        or capture.get("data")
    )
    if not image_b64:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=capture.get("error") or "Kit RPC viewport capture did not return image data",
        )

    observe_request = CosmosObserveRequest(
        prompt=body.prompt,
        image_base64=image_b64,
        mime_type="image/png",
        input_kind="screenshot",
        parent_revision=body.parent_revision,
    )
    response = await observe_canvas_from_cosmos(session_id, observe_request)
    response["viewport_capture"] = {
        "width": capture.get("width"),
        "height": capture.get("height"),
        "max_dim": body.max_dim,
    }
    return response


def _forward_workflow_approve(
    workflow_id: str,
    feedback: str = "canvas confirmed",
) -> Optional[str]:
    """Forward an approve decision to the in-process workflow registry.

    Returns None on success, or a warning string if the workflow_id is
    not found (callers return 200 with workflow_warning in this case).

    Intentionally does NOT raise — a missing workflow must never block the
    canvas commit from returning a success response.

    CONC-2b (2026-05-14): the read-modify-write on `wf` is protected by
    the per-workflow lock obtained via `_wf_lock_for(wf)` so concurrent
    approve calls on the same wf_id serialize properly.
    """
    try:
        from ..chat.tools.handlers._state import _WORKFLOWS  # noqa: PLC0415
        from ..chat.tools.handlers.workflow import _wf_lock_for  # noqa: PLC0415
        wf = _WORKFLOWS.get(workflow_id)
        if wf is None:
            return f"workflow_id '{workflow_id}' not found in active registry"
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        with _wf_lock_for(wf):
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

    CONC-2b (2026-05-14): the read-modify-write on `wf` is protected by
    the per-workflow lock obtained via `_wf_lock_for(wf)`.
    """
    try:
        from ..chat.tools.handlers._state import _WORKFLOWS  # noqa: PLC0415
        from ..chat.tools.handlers.workflow import _wf_lock_for  # noqa: PLC0415
        wf = _WORKFLOWS.get(workflow_id)
        if wf is None:
            return f"workflow_id '{workflow_id}' not found in active registry"
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        with _wf_lock_for(wf):
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
    asset_resolutions = resolve_layout_assets(spec.objects or [])

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
        "asset_resolutions": [
            {
                "object_id": item.object_id,
                "object_class": item.object_class,
                "usd_ref": item.usd_ref,
                "source": item.source,
                "needs_review": item.needs_review,
            }
            for item in asset_resolutions
        ],
    }

    if result.status == "ok":
        payload["bindings"] = {
            role: {"object_id": b.object_id, "source": b.source}
            for role, b in result.bindings.items()
        }
        instantiation = await instantiate(
            spec,
            template_id=body.template_id,
            dry_run=body.dry_run,
        )
        payload["instantiation"] = {
            "status": instantiation.status,
            "message": instantiation.message,
            "build_id": instantiation.build_id,
            "dry_run": body.dry_run,
            "generated_code": instantiation.generated_code if body.dry_run else None,
            "relation_summary": instantiation.relation_summary,
            "relation_diagnostics": instantiation.relation_diagnostics,
            "relation_verification": instantiation.relation_verification,
            "variant_summary": instantiation.variant_summary,
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


@router.post("/{session_id}/campaign/plan")
async def plan_canvas_campaign(
    session_id: str,
    body: CampaignPlanRequest,
) -> Dict[str, Any]:
    """Expand the current LayoutSpec into a scenario variant campaign plan."""
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LayoutSpec to plan variants from",
        )

    plan = build_campaign_plan(
        spec,
        session_id=session_id,
        workspace_root=Path(body.workspace_root) if body.workspace_root else None,
    )
    store.append_event(session_id, "canvas_campaign_plan", {
        "revision": spec.revision,
        "campaign_id": plan["campaign_id"],
        "variant_count": plan["variant_count"],
    })
    return plan


@router.post("/{session_id}/campaign/materialize")
async def materialize_canvas_campaign(
    session_id: str,
    body: CampaignPlanRequest,
) -> Dict[str, Any]:
    """Materialize the current LayoutSpec campaign to local files."""
    store = get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LayoutSpec to materialize variants from",
        )

    manifest = await materialize_campaign(
        spec,
        session_id=session_id,
        workspace_root=Path(body.workspace_root) if body.workspace_root else None,
    )
    store.append_event(session_id, "canvas_campaign_materialized", {
        "revision": spec.revision,
        "campaign_id": manifest["campaign_id"],
        "variant_count": manifest["variant_count"],
        "workspace_dir": manifest["workspace_dir"],
    })
    return manifest


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
