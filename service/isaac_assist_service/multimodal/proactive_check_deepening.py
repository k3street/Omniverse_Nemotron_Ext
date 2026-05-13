"""Phase 93 — proactive_check deepening.

Scaffold for spec coverage. Implementation deferred to runtime/release
work.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 93.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = 93
PHASE_TITLE = "proactive_check deepening"
PHASE_STATUS = "scaffold"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 93",
    }
