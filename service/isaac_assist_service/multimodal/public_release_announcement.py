"""Phase 105 — Public release announcement + demos.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 105.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


PHASE_ID = 105
PHASE_TITLE = "Public release announcement + demos"
PHASE_STATUS = "landed"

# Path to the announcement markdown (relative to repo root, resolved at runtime)
ANNOUNCEMENT_PATH = Path(__file__).parent.parent.parent.parent / "docs" / "release" / "announcement_v1.md"


def load_announcement() -> str:
    """Return the full text of the release announcement markdown."""
    return ANNOUNCEMENT_PATH.read_text(encoding="utf-8")


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 105",
        "announcement_path": str(ANNOUNCEMENT_PATH),
    }
