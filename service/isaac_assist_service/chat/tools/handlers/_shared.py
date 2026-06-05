"""Cross-handler shared utilities.

Phase 8 contract per spec — this module is the *future* home for the
twelve cross-handler utilities currently living at module-level inside
`tool_executor.py`. As each theme moves out of the monolith (Phases
3-7), the utilities its handlers depended on move here.

The Phase 2b cross-reference audit identified ten high-fan-in
utilities (each called by ≥3 handlers — `docs/audits/handler_cross_refs.md`
for the live list):

  execute_tool_call          — dispatcher entry, stays in tool_executor
                                (special case — not migrated here)
  _get_viewport_bytes        — viewport capture helper (vision theme)
  _get_vision_provider       — vision-LLM provider selector
  _query_run_ipc             — training IPC helper
  _resolve_run_id            — training run-id resolver
  _check_real_data_path      — finetune / sim data validator
  _wf_now_iso                — workflow timestamp helper
  _parse_last_json_line      — subprocess output parser
  _safe_robot_name           — USD-path sanitiser
  _validate_env_id           — IsaacLab env-id validator

Phase 8's "Files (changes)" — the import-swap from `tool_executor`
globals to `handlers._shared` — requires the themed modules to actually
contain handler code first. That's Phases 3-7. Until then, this module
is a documented re-export façade: themed modules can already
`from ._shared import _safe_robot_name` and the import resolves to the
existing implementation in `tool_executor.py`, so no behaviour change.

Once Phase 3-7 land, the re-exports in this file are replaced by the
moved function bodies; tool_executor.py loses those module-level
definitions; nothing in the consumer themed modules has to change
(their imports already point here).

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only re-exports for theme handlers that want signatures
    # without circular-import risk.
    pass


# ---------------------------------------------------------------------------
# Read-only cross-handler constants (Phase 8 §Risk mitigation: keep
# read-only constants here, NOT in `_state.py`'s mutable dataclasses).
# Populated as Phase 3-7 lift them out of tool_executor.py.

CONSTANTS: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Utility surface contract — names that consumer themed modules expect
# to import. Until Phase 3-7 actually move the implementations,
# `_resolve_from_legacy` is the bridge: it pulls the name out of
# `tool_executor.py`'s module namespace and re-exports it under
# `_shared.<name>`.
#
# This is a deliberate bridge — when a theme like handlers/training.py
# does `from ._shared import _resolve_run_id`, the import resolves
# transparently regardless of whether the function has moved yet.

_LEGACY_REEXPORT_NAMES: tuple[str, ...] = (
    "_get_viewport_bytes",
    "_get_vision_provider",
    "_query_run_ipc",
    "_resolve_run_id",
    "_check_real_data_path",
    "_wf_now_iso",
    "_parse_last_json_line",
    "_safe_robot_name",
    "_validate_env_id",
)


def _resolve_from_legacy(name: str):
    """Pull `name` from `tool_executor.py`'s module namespace.

    Used by __getattr__ below to provide transparent re-exports for any
    high-fan-in utility that hasn't been physically moved out of the
    monolith yet.
    """
    # Lazy import — avoid pulling tool_executor at module load (it's
    # 35k lines and loading it eagerly would change import-time cost
    # for any _shared consumer that doesn't actually use the legacy
    # utilities).
    from .. import tool_executor as _te  # type: ignore[no-redef]

    return getattr(_te, name)


def __getattr__(name: str):  # PEP 562 module-level __getattr__
    """Lazy re-export for legacy-named utilities.

    Phase 3-7 will replace this with direct function definitions in
    this module body. Until then, `from ._shared import <name>` for any
    name in `_LEGACY_REEXPORT_NAMES` resolves to the corresponding
    function in `tool_executor.py`.
    """
    if name in _LEGACY_REEXPORT_NAMES:
        return _resolve_from_legacy(name)
    raise AttributeError(
        f"module 'service.isaac_assist_service.chat.tools.handlers._shared' "
        f"has no attribute {name!r}. If this is a future-Phase-3-7 utility "
        f"that should be re-exported, add it to _LEGACY_REEXPORT_NAMES."
    )


__all__ = [
    "CONSTANTS",
    # Plus the lazy-imported names; importers see them via __getattr__.
    *_LEGACY_REEXPORT_NAMES,
]
