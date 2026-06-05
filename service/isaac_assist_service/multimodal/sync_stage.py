"""Phase 22 — sync_from_stage round-trip.

Reads Kit stage under a scope_prim, returns a LayoutSpec mirroring the
current state. Pairs with Phase 19 instantiator for canvas/Kit
bidirectional sync.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 22.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _classify_prim(prim: Dict[str, Any]) -> Optional[str]:
    """Map a Kit prim entry to a canonical object_class.

    Phase 22 scaffold: matches by USD reference URL prefix. The
    classification heuristic improves over Phase 25 (object palette
    expansion).
    """
    ref = prim.get("reference_url", "") or ""
    if "FrankaPanda/franka" in ref:
        return "franka_panda"
    if "UR10" in ref:
        return "ur10"
    if "UR5" in ref:
        return "ur5e"
    usd_type = prim.get("usd_type", "")
    if usd_type == "Cube":
        return "cube"
    if usd_type == "Cylinder":
        return "cylinder"
    return None


def _prim_to_typed_object(prim: Dict[str, Any], klass: str) -> Dict[str, Any]:
    return {
        "object_class": klass,
        "position": prim.get("position", [0.0, 0.0, 0.0]),
        "prim_path": prim.get("path", ""),
    }


async def sync_from_stage(
    session_id: str,
    scope_prim: str = "/World/Layout",
) -> Dict[str, Any]:
    """Read Kit stage prims, build a LayoutSpec.

    Phase 22 scaffold: returns a dict shape compatible with LayoutSpec.
    Real Kit RPC integration is daytime work.
    """
    # Scaffold: returns empty LayoutSpec.
    return {
        "intent": {"pattern_hint": "pick_place"},
        "objects": [],
        "source": {"modality": "viewport", "confidence": 1.0},
        "scope_prim": scope_prim,
        "synced_at_session": session_id,
    }
