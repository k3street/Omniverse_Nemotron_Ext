"""execute_contact_sequence_plan runtime

Back-compat shim. Canonical implementation lives at
`service.isaac_assist_service.multimodal.execute_contact_sequence_runtime`.

This file exists for spec-historical naming; new code should import
from the canonical module directly.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63.
"""
from __future__ import annotations

from typing import Any, Dict

from .execute_contact_sequence_runtime import *  # noqa: F401, F403

PHASE_ID = 63
PHASE_TITLE = "execute_contact_sequence_plan runtime"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata. See canonical module for the real impl."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63",
        "canonical_module": "service.isaac_assist_service.multimodal.execute_contact_sequence_runtime",
    }
