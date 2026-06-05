"""Central handler registry — the dispatch pattern that replaces
the inline `DATA_HANDLERS["X"] = _handle_X` assignments in
`tool_executor.py` over the course of Phases 3-9.

Phase 2: each theme module's `register()` is a no-op, so
`register_handlers(data, codegen)` is currently a no-op too. It is
NOT yet called from `tool_executor.py`; the legacy dispatch dicts
remain authoritative. Phase 9 ("Swap dispatch pattern") flips that.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phases 2 + 9.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from . import (
    arena,
    diagnostics,
    physics,
    resolve,
    robot,
    ros2,
    scene_authoring,
    scene_blueprints,
    sdg,
    sensors,
    teleop,
    training,
    vision,
    workflow,
)

# Order matters once registers do real work: themes with no internal
# state come first; workflow / resolve / vision (which may rely on
# others) come last. Until Phase 3+ this ordering is moot.
_THEME_MODULES = (
    scene_authoring,
    physics,
    robot,
    sensors,
    sdg,
    training,
    ros2,
    teleop,
    scene_blueprints,
    diagnostics,
    arena,
    vision,
    workflow,
    resolve,
)


def register_handlers(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Invoke each theme module's `register(data, codegen)`.

    Phase 2 invariant: every theme's `register()` is a no-op, so
    this function as a whole is a no-op. The presence of the entry
    point and the package skeleton is the deliverable for Phase 2;
    Phase 9 ("Swap dispatch pattern") makes it the *only* entry
    point by re-routing `tool_executor.py` to call this function
    instead of building dispatch dicts inline.

    Args:
        data:    the `DATA_HANDLERS` dict to populate.
        codegen: the `CODE_GEN_HANDLERS` dict to populate.
    """
    for module in _THEME_MODULES:
        module.register(data, codegen)
