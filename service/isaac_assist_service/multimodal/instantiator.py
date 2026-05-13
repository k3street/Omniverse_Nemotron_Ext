"""Phase 19 — Kit RPC execution path for apply_layout_spec_to_scene.

Block 1B closes the open seam between canvas ratification and actual
scene mutation. The ratifier (`ratify.py`) produces a LayoutSpec; the
instantiator walks the spec, emits canonical USD-Python patches, and
posts them via `kit_tools.queue_exec_patch`.

This is a scaffold deliverable for Phase 19. The full implementation
needs runtime testing against a live Kit instance to verify each
canonical object class lands at its expected USD path with the right
schema. Until that happens, `instantiate()` honors `dry_run=True` so
callers can validate the generated patch shape without executing.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 19.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InstantiateResult:
    """Outcome of an instantiate() call."""
    build_id: Optional[str] = None
    status: str = "unknown"  # "ok" | "no_objects" | "dry_run" | "error"
    message: str = ""
    generated_code: Optional[str] = None
    placed: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    raw_result: Optional[Dict[str, Any]] = None

    @classmethod
    def from_exec(cls, result: Dict[str, Any]) -> "InstantiateResult":
        success = bool(result.get("success", False))
        return cls(
            build_id=result.get("build_id"),
            status="ok" if success else "error",
            message=result.get("output", "") if not success else "",
            raw_result=result,
        )


def _build_canonical_code(spec, template_id: Optional[str]) -> str:
    """Build the USD-Python patch that materialises spec.objects.

    Phase 19 scaffold: emits a stub patch that imports `omni.usd` and
    calls `get_stage()`. Real per-class branching (franka_panda → asset
    reference, cube → UsdGeom.Cube + translate, etc.) is the Phase 19
    "real implementation" work — needs Kit RPC to verify each class.
    """
    objects = getattr(spec, "objects", None) or []
    lines = [
        "import omni.usd",
        "from pxr import Sdf, UsdGeom, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"# template_id={template_id!r}",
        f"# {len(objects)} objects in spec",
    ]
    for i, obj in enumerate(objects):
        obj_class = getattr(obj, "object_class", None) or obj.get("object_class", "unknown")
        position = getattr(obj, "position", None) or obj.get("position", [0, 0, 0])
        prim_path = f"/World/{obj_class}_{i + 1}"
        lines.append(f"# Object {i}: {obj_class} @ {position}")
        lines.append(f"# TODO Phase 19 full: emit canonical {obj_class} patch at {prim_path}")
    lines.append("print('Phase 19 scaffold — no objects materialised')")
    return "\n".join(lines)


async def instantiate(
    spec: Any,
    template_id: Optional[str] = None,
    dry_run: bool = False,
) -> InstantiateResult:
    """Walk a LayoutSpec, emit per-object USD patches, dispatch to Kit.

    Args:
        spec: A ratified LayoutSpec.
        template_id: Optional canonical template binding the spec.
        dry_run: If True, return the generated code without executing.

    Returns:
        InstantiateResult with build_id (when executed), status, per-object
        placement results.
    """
    objects = getattr(spec, "objects", None)
    if objects is None:
        return InstantiateResult(
            status="no_objects",
            message="LayoutSpec has only intent — canonical pipeline supplies positions",
        )

    code = _build_canonical_code(spec, template_id)

    if dry_run:
        return InstantiateResult(
            status="dry_run",
            generated_code=code,
            message="dry_run — code generated, not executed",
        )

    # Live path
    try:
        from ..chat.tools import kit_tools
        result = await kit_tools.queue_exec_patch(code, description=f"Phase 19: instantiate {template_id or 'spec'}")
        return InstantiateResult.from_exec(result if isinstance(result, dict) else {"output": str(result)})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Phase19 instantiate] queue_exec_patch failed: {type(e).__name__}: {e}")
        return InstantiateResult(
            status="error",
            message=f"{type(e).__name__}: {e}",
            generated_code=code,
        )
