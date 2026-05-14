"""wbc locomotion

Back-compat shim. Canonical implementation lives at
`service.isaac_assist_service.multimodal.sub_phase_79b_isaaclab_g1_locomanip`.

This file exists for spec-historical naming; new code should import
from the canonical module directly.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 79b.
"""
from __future__ import annotations

from typing import Any, Dict

from .sub_phase_79b_isaaclab_g1_locomanip import *  # noqa: F401, F403

PHASE_ID = '79b'
PHASE_TITLE = "wbc locomotion"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 79b",
        "canonical_module": "service.isaac_assist_service.multimodal.sub_phase_79b_isaaclab_g1_locomanip",
    }
