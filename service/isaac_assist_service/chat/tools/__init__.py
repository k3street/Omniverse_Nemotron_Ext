"""Tool dispatch entry points for the Isaac Assist chat service.

Phase 2 re-export: makes the themed handler modules importable as
`from service.isaac_assist_service.chat.tools.handlers import
scene_authoring` (and equivalent for each theme). The legacy
`tool_executor.py` symbols (`DATA_HANDLERS`, `CODE_GEN_HANDLERS`,
`execute_tool_call`) remain accessible via their direct paths and
are unaffected by this re-export.

Phase 9 ("Swap dispatch pattern") makes
`handlers._dispatch.register_handlers(...)` the canonical entry
point. Until then, the legacy dispatch dicts in `tool_executor.py`
remain authoritative.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from . import handlers as handlers  # re-export the themed handler subpackage

__all__ = ["handlers"]
