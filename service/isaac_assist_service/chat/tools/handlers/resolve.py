"""Resolve handlers — target scope: the 10 `resolve_*`
natural-language disambiguation handlers (resolve_constraint_phrase,
resolve_context_reference, resolve_coordinate_reference,
resolve_count_vagueness, resolve_material_properties,
resolve_prim_reference, resolve_relational_property,
resolve_robot_class, resolve_sequence_phrase,
resolve_size_adjective, resolve_skill_composition,
resolve_success_condition).

Phase 2 stub: empty module with a no-op `register()`. Handlers
for this theme will move from `tool_executor.py` into here in
Phase 16 ("Move resolve handlers + lookup handlers").

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """No-op stub — populated by Phase 16."""
    return None
