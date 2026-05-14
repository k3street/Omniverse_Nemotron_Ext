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
    """Mark a LayoutSpec as freeform — skip template ratification and role-binding.

    The returned dict wraps the spec with ``freeform=True`` so the instantiator
    bypasses all template invariant checks.  A human-readable warning is included
    so the caller can surface it in the UI.

    Args:
        spec (Any): A LayoutSpec object or plain dict describing the scene.
        reason (str, optional): Short code for why freeform was triggered,
            e.g. ``"user_request"`` or ``"no_template_match"``. Defaults to
            ``"user_request"``.

    Returns:
        Dict[str, Any]: Keys ``spec`` (normalised dict), ``freeform`` (``True``),
            ``reason``, ``template_id`` (``None``), and ``warning`` (str).
    """
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
