"""Phase 30 — multimodal force_freeform escape hatch.

When the canvas LLM cannot match any role-based template, the
`force_freeform` path allows the user to commit a LayoutSpec that
skips role-binding and goes straight to instantiation. The result is
an unmanaged scene (no template invariants), but the user is in
control.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 30.
"""
from __future__ import annotations

from typing import Any, Dict


def force_freeform(spec: Any, reason: str = "user_request") -> Dict[str, Any]:
    """Mark a LayoutSpec as freeform — skip template ratification."""
    return {
        "spec": spec if isinstance(spec, dict) else {
            "objects": getattr(spec, "objects", []),
            "intent": getattr(spec, "intent", None),
        },
        "freeform": True,
        "reason": reason,
        "template_id": None,
        "warning": (
            "freeform mode skips role-binding; the instantiator may "
            "produce unexpected layouts. Use only when no template fits."
        ),
    }
