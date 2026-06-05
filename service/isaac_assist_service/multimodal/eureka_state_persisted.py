"""Phase 64 — Eureka run state actually persisted.

Landed 2026-05-14 (post-migration backlog Wave 3d). The writers live
in ``service/isaac_assist_service/chat/tools/handlers/training.py``:
- ``_handle_generate_reward`` initialises ``EUREKA.runs[run_id]``.
- ``_handle_iterate_reward`` increments iteration + tracks best fitness.

This module is a metadata stub used by spec-coverage audits; the
actual run state lives in ``handlers/_state.py::EUREKA.runs``.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 64.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = 64
PHASE_TITLE = "Eureka run state actually persisted"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits.

    Returns:
        Dict[str, Any] with keys ``phase``, ``title``, ``status``,
        ``spec_ref``. ``status`` is ``"landed"`` since 2026-05-14.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 64",
    }
