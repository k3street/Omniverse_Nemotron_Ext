"""Phase 104 — telemetry opt-in.

User-facing knob for anonymous telemetry. Default OFF.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 104.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict


PHASE_ID = 104
PHASE_TITLE = "Telemetry opt-in"
PHASE_STATUS = "landed"


def is_telemetry_enabled() -> bool:
    """Check whether the user has explicitly opted in."""
    env_val = os.environ.get("IA_TELEMETRY", "").lower()
    if env_val in ("1", "true", "on", "yes"):
        return True
    if env_val in ("0", "false", "off", "no"):
        return False
    # Check config file
    cfg_path = Path.home() / ".isaac_assist" / "telemetry.txt"
    if cfg_path.exists():
        try:
            return cfg_path.read_text().strip().lower() in ("enabled", "on", "1")
        except Exception:
            return False
    return False  # Default OFF


def set_telemetry_enabled(enabled: bool) -> None:
    """Persist the user's choice."""
    cfg_path = Path.home() / ".isaac_assist" / "telemetry.txt"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("enabled" if enabled else "disabled")


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 104.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 104",
    }
