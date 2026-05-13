"""setup_assembly_constraint runtime

Back-compat shim. Canonical implementation lives at
`service.isaac_assist_service.multimodal.setup_assembly_constraint_runtime`.

This file exists for spec-historical naming; new code should import
from the canonical module directly.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 72.
"""
from __future__ import annotations

from typing import Any, Dict

from .setup_assembly_constraint_runtime import *  # noqa: F401, F403

PHASE_ID = 72
PHASE_TITLE = "setup_assembly_constraint runtime"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata. See canonical module for the real impl."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 72",
        "canonical_module": "service.isaac_assist_service.multimodal.setup_assembly_constraint_runtime",
    }
