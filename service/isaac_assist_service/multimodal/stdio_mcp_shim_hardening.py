"""Compatibility alias for the canonical Phase 87 implementation."""

from .stdio_mcp_shim import *  # noqa: F401,F403
from .stdio_mcp_shim import PHASE_ID, PHASE_STATUS, PHASE_TITLE, get_phase_metadata
