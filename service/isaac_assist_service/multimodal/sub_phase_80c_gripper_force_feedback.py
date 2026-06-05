"""Phase 80c — gripper force feedback.

Sub-phase scaffold for spec coverage.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 80c.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = "80c"
PHASE_TITLE = "gripper force feedback"
PHASE_STATUS = "scaffold"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 80c",
    }
