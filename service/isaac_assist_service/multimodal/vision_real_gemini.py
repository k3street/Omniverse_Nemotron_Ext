"""Phase 76 compatibility facade for the wired async Gemini vision provider."""

from __future__ import annotations

from typing import Any, Dict

PHASE_ID = 76
PHASE_TITLE = "Vision: real Gemini Vision integration"
PHASE_STATUS = "landed"
PROVIDER_CLASS = "service.isaac_assist_service.chat.vision_gemini.GeminiVisionProvider"


def create_live_provider(*args, **kwargs):
    """Create the concrete provider lazily so metadata imports need no aiohttp."""
    try:
        from ..chat.vision_gemini import GeminiVisionProvider
    except ImportError as exc:
        raise RuntimeError(
            "Gemini vision requires the core service dependencies; install the package first"
        ) from exc
    return GeminiVisionProvider(*args, **kwargs)


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 76",
        "provider_class": PROVIDER_CLASS,
        "live_verification": "gemini_live",
    }
