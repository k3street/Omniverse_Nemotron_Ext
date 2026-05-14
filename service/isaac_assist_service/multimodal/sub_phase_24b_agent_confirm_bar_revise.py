"""Phase 24b — agent confirm bar revise.

Sub-phase scaffold for spec coverage.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 24b.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = "24b"
PHASE_TITLE = "agent confirm bar revise"
PHASE_STATUS = "scaffold"


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
    }
