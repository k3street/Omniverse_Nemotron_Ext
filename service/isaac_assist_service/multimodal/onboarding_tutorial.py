"""Phase 103 — User-facing onboarding tutorial.

Scaffold for spec coverage. Implementation deferred to runtime/release
work.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 103.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = 103
PHASE_TITLE = "User-facing onboarding tutorial"
PHASE_STATUS = "scaffold"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 103",
    }
