"""
Telemetry event types + emit helpers per spec §17.

Wraps `MultimodalStore.append_event` with strongly-typed event constants
and payload shapes. Use `emit(store, session_id, event_type, **payload)`
or one of the named convenience functions (`emit_modality_invoked`,
`emit_intent_extracted`, etc.) per spec §17.1 event schema.

Aggregator: `scripts/qa/analyze_multimodal_usage.py`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .persistence import MultimodalStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types per spec §17.1
# ---------------------------------------------------------------------------

EVENT_MODALITY_INVOKED = "modality_invoked"
EVENT_INTENT_EXTRACTED = "intent_extracted"
EVENT_RETRIEVAL_COMPLETED = "retrieval_completed"
EVENT_RATIFY_COMPLETED = "ratify_completed"
EVENT_REBIND_ROLE = "rebind_role"
EVENT_BUILD_STARTED = "build_started"
EVENT_BUILD_PROGRESS = "build_progress"
EVENT_BUILD_COMPLETED = "build_completed"
EVENT_VERIFY_CHECK_RUN = "verify_check_run"
EVENT_CANVAS_PROPOSED_RESOLVED = "canvas_proposed_resolved"
EVENT_CANONICAL_MATCH_SHOWN = "canonical_match_shown"
EVENT_CANONICAL_MATCH_RESOLVED = "canonical_match_resolved"
EVENT_USER_CORRECTION = "user_correction"

# Kit Supervisor events (spec 2026-05-11 v2 §9.1)
EVENT_SUPERVISOR_STARTED = "supervisor_started"
EVENT_SUPERVISOR_STOPPED = "supervisor_stopped"
EVENT_SUPERVISOR_DRIFT_CLASSIFICATION = "supervisor_drift_classification"
EVENT_SUPERVISOR_DRIFT_DETECTED = "supervisor_drift_detected"
EVENT_SUPERVISOR_RESTART_DECISION = "supervisor_restart_decision"
EVENT_SUPERVISOR_RESTART_STARTED = "supervisor_restart_started"
EVENT_SUPERVISOR_RESTART_COMPLETED = "supervisor_restart_completed"
EVENT_SUPERVISOR_RESTART_FAILED = "supervisor_restart_failed"
EVENT_SUPERVISOR_SOFT_RESET = "supervisor_soft_reset"
EVENT_SUPERVISOR_MEMORY_GROWTH = "supervisor_memory_growth"
EVENT_SUPERVISOR_RUNNER_EXCEPTION = "supervisor_runner_exception"
EVENT_SUPERVISOR_ABORT = "supervisor_abort"

# Contact-rich manipulation compliance events (CRM spec §8)
EVENT_COMPLIANCE_INSTALLED = "compliance_installed"
EVENT_COMPLIANCE_PARAMS_UPDATED = "compliance_params_updated"
EVENT_COMPLIANCE_RELEASED = "compliance_released"
EVENT_FT_SENSOR_ATTACHED = "ft_sensor_attached"
EVENT_CONTACT_PHASE_ENTERED = "contact_phase_entered"
EVENT_CONTACT_PHASE_EXITED = "contact_phase_exited"
EVENT_INSERTION_SUCCEEDED = "insertion_succeeded"
EVENT_INSERTION_FAILED = "insertion_failed"

ALL_EVENT_TYPES: List[str] = [
    EVENT_MODALITY_INVOKED,
    EVENT_INTENT_EXTRACTED,
    EVENT_RETRIEVAL_COMPLETED,
    EVENT_RATIFY_COMPLETED,
    EVENT_REBIND_ROLE,
    EVENT_BUILD_STARTED,
    EVENT_BUILD_PROGRESS,
    EVENT_BUILD_COMPLETED,
    EVENT_VERIFY_CHECK_RUN,
    EVENT_CANVAS_PROPOSED_RESOLVED,
    EVENT_CANONICAL_MATCH_SHOWN,
    EVENT_CANONICAL_MATCH_RESOLVED,
    EVENT_USER_CORRECTION,
    EVENT_SUPERVISOR_STARTED,
    EVENT_SUPERVISOR_STOPPED,
    EVENT_SUPERVISOR_DRIFT_CLASSIFICATION,
    EVENT_SUPERVISOR_DRIFT_DETECTED,
    EVENT_SUPERVISOR_RESTART_DECISION,
    EVENT_SUPERVISOR_RESTART_STARTED,
    EVENT_SUPERVISOR_RESTART_COMPLETED,
    EVENT_SUPERVISOR_RESTART_FAILED,
    EVENT_SUPERVISOR_SOFT_RESET,
    EVENT_SUPERVISOR_MEMORY_GROWTH,
    EVENT_SUPERVISOR_RUNNER_EXCEPTION,
    EVENT_SUPERVISOR_ABORT,
    EVENT_COMPLIANCE_INSTALLED,
    EVENT_COMPLIANCE_PARAMS_UPDATED,
    EVENT_COMPLIANCE_RELEASED,
    EVENT_FT_SENSOR_ATTACHED,
    EVENT_CONTACT_PHASE_ENTERED,
    EVENT_CONTACT_PHASE_EXITED,
    EVENT_INSERTION_SUCCEEDED,
    EVENT_INSERTION_FAILED,
]


# ---------------------------------------------------------------------------
# Generic emit
# ---------------------------------------------------------------------------

def emit(
    store: "MultimodalStore",
    session_id: str,
    event_type: str,
    **payload: Any,
) -> Optional[int]:
    """Append one event. Returns event_id, or None on failure.

    Failures are logged + swallowed: telemetry MUST NOT break the calling
    code path. If the events table is unavailable the application keeps
    running.
    """
    if event_type not in ALL_EVENT_TYPES:
        logger.warning(
            f"telemetry: unknown event_type {event_type!r}; "
            f"valid: {ALL_EVENT_TYPES}"
        )
    try:
        return store.append_event(session_id, event_type, payload)
    except Exception as e:
        logger.warning(f"telemetry: append_event failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Named convenience emitters — one per event type per spec §17.1
# ---------------------------------------------------------------------------

def emit_modality_invoked(
    store: "MultimodalStore",
    session_id: str,
    modality: str,
    duration_ms: float,
    **extra: Any,
) -> Optional[int]:
    """Emit a ``modality_invoked`` event recording which modality ran and how long it took.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        modality (str): Name of the modality, e.g. ``"text"``, ``"vlm"``.
        duration_ms (float): Wall-clock duration in milliseconds.
        **extra: Additional key-value pairs appended to the payload.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_MODALITY_INVOKED,
        modality=modality, ms=duration_ms, **extra,
    )


def emit_intent_extracted(
    store: "MultimodalStore",
    session_id: str,
    modality: str,
    intent_summary: Dict[str, Any],
) -> Optional[int]:
    """Emit an ``intent_extracted`` event with the structured intent produced by a modality.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        modality (str): Name of the modality that produced the intent.
        intent_summary (Dict[str, Any]): Parsed intent dict, e.g. ``{"task": "pick_place", ...}``.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_INTENT_EXTRACTED,
        modality=modality, intent_summary=intent_summary,
    )


def emit_retrieval_completed(
    store: "MultimodalStore",
    session_id: str,
    top_k_with_scores: List[Dict[str, Any]],
    tier: str,
) -> Optional[int]:
    """Emit a ``retrieval_completed`` event with ranked retrieval results.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        top_k_with_scores (List[Dict[str, Any]]): Ordered list of ``{"id": ..., "score": ...}``
            dicts, best match first.
        tier (str): Retrieval tier label, e.g. ``"dense"``, ``"sparse"``, ``"hybrid"``.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_RETRIEVAL_COMPLETED,
        top_k_with_scores=top_k_with_scores, tier=tier,
    )


def emit_ratify_completed(
    store: "MultimodalStore",
    session_id: str,
    status: str,
    n_diagnostics: int,
    **extra: Any,
) -> Optional[int]:
    """Emit a ``ratify_completed`` event recording the outcome of the ratify pipeline.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        status (str): Final ratify status, e.g. ``"ok"``, ``"amended"``, ``"rejected"``.
        n_diagnostics (int): Number of diagnostic messages produced by ratify.
        **extra: Additional payload key-value pairs.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_RATIFY_COMPLETED,
        status=status, n_diagnostics=n_diagnostics, **extra,
    )


def emit_rebind_role(
    store: "MultimodalStore",
    session_id: str,
    role: str,
    object_id: str,
    source: str,
) -> Optional[int]:
    """Emit a ``rebind_role`` event when a role binding is changed during a session.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        role (str): Name of the role being rebound, e.g. ``"robot"``, ``"target_bin"``.
        object_id (str): New prim path or object identifier assigned to the role.
        source (str): What triggered the rebind, e.g. ``"user_correction"``, ``"auto_resolve"``.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_REBIND_ROLE,
        role=role, object_id=object_id, source=source,
    )


def emit_build_started(
    store: "MultimodalStore",
    session_id: str,
    build_id: str,
    template_id: Optional[str] = None,
) -> Optional[int]:
    """Emit a ``build_started`` event marking the beginning of a scene-build sequence.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        build_id (str): Unique identifier for this build attempt.
        template_id (str, optional): Canonical template ID being instantiated, if any.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_BUILD_STARTED,
        build_id=build_id, template_id=template_id,
    )


def emit_build_progress(
    store: "MultimodalStore",
    session_id: str,
    build_id: str,
    tool: str,
    status: str,
    duration_ms: float,
) -> Optional[int]:
    """Emit a ``build_progress`` event for a single tool step within a build sequence.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        build_id (str): Build attempt identifier matching the ``build_started`` event.
        tool (str): Name of the tool that ran, e.g. ``"create_prim"``.
        status (str): Step outcome, e.g. ``"ok"``, ``"error"``.
        duration_ms (float): Time taken by this step in milliseconds.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_BUILD_PROGRESS,
        build_id=build_id, tool=tool, status=status, ms=duration_ms,
    )


def emit_build_completed(
    store: "MultimodalStore",
    session_id: str,
    build_id: str,
    status: str,
    n_tools: int,
    **extra: Any,
) -> Optional[int]:
    """Emit a ``build_completed`` event summarising an entire build sequence.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        build_id (str): Build attempt identifier matching the ``build_started`` event.
        status (str): Final build outcome, e.g. ``"success"``, ``"partial"``, ``"failed"``.
        n_tools (int): Total number of tool steps executed during the build.
        **extra: Additional payload key-value pairs.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_BUILD_COMPLETED,
        build_id=build_id, status=status, n_tools=n_tools, **extra,
    )


def emit_verify_check_run(
    store: "MultimodalStore",
    session_id: str,
    check_id: str,
    status: str,
    duration_ms: float,
    **extra: Any,
) -> Optional[int]:
    """Emit a ``verify_check_run`` event for one verifier check execution.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        check_id (str): Namespaced check identifier, e.g. ``"verify:reach"``.
        status (str): Check outcome: ``"pass"``, ``"fail"``, or ``"skipped"``.
        duration_ms (float): Time taken by the check in milliseconds.
        **extra: Additional payload key-value pairs.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_VERIFY_CHECK_RUN,
        check_id=check_id, status=status, ms=duration_ms, **extra,
    )


def emit_canonical_match_shown(
    store: "MultimodalStore",
    session_id: str,
    template_id: str,
    score: float,
) -> Optional[int]:
    """Emit a ``canonical_match_shown`` event when a template suggestion is displayed to the user.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        template_id (str): ID of the canonical template surfaced to the user.
        score (float): Similarity score that ranked this match at the top.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_CANONICAL_MATCH_SHOWN,
        template_id=template_id, score=score,
    )


def emit_canonical_match_resolved(
    store: "MultimodalStore",
    session_id: str,
    action: str,
    **extra: Any,
) -> Optional[int]:
    """Emit a ``canonical_match_resolved`` event when the user accepts or dismisses a suggestion.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        action (str): User action taken: ``"accept"``, ``"reject"``, or ``"ignore"``.
        **extra: Additional payload key-value pairs.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_CANONICAL_MATCH_RESOLVED,
        action=action, **extra,
    )


def emit_user_correction(
    store: "MultimodalStore",
    session_id: str,
    surface: str,
    what: str,
) -> Optional[int]:
    """Emit a ``user_correction`` event when the user overrides a system decision.

    Args:
        store (MultimodalStore): Active session store.
        session_id (str): Current session identifier.
        surface (str): UI surface where correction was made, e.g. ``"canvas"``, ``"chat"``.
        what (str): Short description of what was corrected, e.g. ``"role_binding"``.

    Returns:
        Optional[int]: Event ID, or ``None`` if the emit failed.
    """
    return emit(
        store, session_id, EVENT_USER_CORRECTION,
        surface=surface, what=what,
    )


def emit_canvas_proposed_resolved(
    store: "MultimodalStore",
    session_id: str,
    action: str,
) -> Optional[int]:
    """action ∈ {"accept", "reject", "refine"} per spec §17.1."""
    if action not in ("accept", "reject", "refine"):
        logger.warning(f"telemetry: invalid canvas-resolved action: {action!r}")
    return emit(
        store, session_id, EVENT_CANVAS_PROPOSED_RESOLVED,
        action=action,
    )
