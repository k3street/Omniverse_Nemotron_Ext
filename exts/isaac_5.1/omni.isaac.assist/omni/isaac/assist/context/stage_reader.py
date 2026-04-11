"""
stage_reader.py
---------------
Reads the active USD stage inside Isaac Sim and returns a JSON-serialisable
representation of the scene hierarchy.

All functions are synchronous and safe to call from Kit's main thread.
"""
from __future__ import annotations
import carb
from typing import Dict, List, Any

_MAX_PRIMS = 500  # hard cap to avoid megabyte payloads


def get_stage_summary() -> Dict[str, Any]:
    """
    Lightweight 1-liner snapshot: prim count, selected paths, stage path.
    Suitable for injecting into every chat turn (~50 tokens).
    """
    try:
        import omni.usd
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            return {"error": "No stage loaded"}

        selection = ctx.get_selection().get_selected_prim_paths()
        prim_count = sum(1 for _ in stage.Traverse())
        stage_url = stage.GetRootLayer().identifier

        return {
            "stage_url": stage_url,
            "prim_count": prim_count,
            "selected_paths": list(selection),
        }
    except Exception as e:
        carb.log_warn(f"[IsaacAssist] stage_summary error: {e}")
        return {"error": str(e)}


def get_stage_tree(max_depth: int = 6) -> Dict[str, Any]:
    """
    Full hierarchical tree up to max_depth, capped at _MAX_PRIMS nodes.
    Returns a nested dict suitable for LLM context injection.
    """
    try:
        import omni.usd
        from pxr import Usd, UsdGeom

        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            return {"error": "No stage loaded"}

        prim_count = 0
        truncated = False

        def _serialize(prim: Usd.Prim, depth: int) -> Dict | None:
            nonlocal prim_count, truncated
            if depth > max_depth or prim_count >= _MAX_PRIMS:
                truncated = True
                return None
            prim_count += 1

            node: Dict[str, Any] = {
                "path": str(prim.GetPath()),
                "type": prim.GetTypeName() or "Scope",
                "active": prim.IsActive(),
            }

            # Tag visibility
            if prim.IsA(UsdGeom.Imageable):
                vis_attr = UsdGeom.Imageable(prim).GetVisibilityAttr()
                if vis_attr:
                    node["visibility"] = vis_attr.Get()

            children = []
            for child in prim.GetChildren():
                child_node = _serialize(child, depth + 1)
                if child_node:
                    children.append(child_node)
            if children:
                node["children"] = children
            return node

        root = stage.GetPseudoRoot()
        tree = []
        for top in root.GetChildren():
            node = _serialize(top, depth=0)
            if node:
                tree.append(node)

        return {
            "stage_url": stage.GetRootLayer().identifier,
            "prim_count": prim_count,
            "truncated": truncated,
            "tree": tree,
        }

    except Exception as e:
        carb.log_warn(f"[IsaacAssist] stage_tree error: {e}")
        return {"error": str(e)}
