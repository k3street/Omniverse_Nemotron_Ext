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
