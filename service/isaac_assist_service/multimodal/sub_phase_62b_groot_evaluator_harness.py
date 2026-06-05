"""groot evaluator harness

Back-compat shim. Canonical implementation lives at
`service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness`.

This file exists for spec-historical naming; new code should import
from the canonical module directly.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 62b.
"""
from __future__ import annotations

from typing import Any, Dict

from .sub_phase_62b_groot_n17_eval_harness import *  # noqa: F401, F403

PHASE_ID = '62b'
PHASE_TITLE = "groot evaluator harness"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 62b",
        "canonical_module": "service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness",
    }
