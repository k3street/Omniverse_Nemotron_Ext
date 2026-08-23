"""Phase 24b compatibility facade for canvas confirm/revise routes."""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = "24b"
PHASE_TITLE = "agent confirm bar revise"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 24b",
        "implementation": "multimodal.routes.commit_canvas/reject_canvas",
    }


async def commit_canvas(*args, **kwargs):
    """Forward to the operational canvas commit route without eager imports."""
    from .routes import commit_canvas as implementation
    return await implementation(*args, **kwargs)


async def reject_canvas(*args, **kwargs):
    """Forward to the operational canvas reject/revise route."""
    from .routes import reject_canvas as implementation
    return await implementation(*args, **kwargs)
