"""
tools/kit_tools.py
-------------------
Async wrappers that call the Kit RPC server (localhost:8001) to retrieve
live Isaac Sim scene data. All functions return structured dicts.
"""
from __future__ import annotations
import logging
import json
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
KIT_RPC_BASE = "http://127.0.0.1:8001"


async def _get(path: str, params: Dict = None) -> Dict[str, Any]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{KIT_RPC_BASE}{path}", params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {"error": f"Kit RPC {path} returned {resp.status}"}
                return await resp.json()
    except Exception as e:
        logger.warning(f"[KitTools] GET {path} failed: {e}")
        return {"error": str(e)}


async def _post(path: str, body: Dict) -> Dict[str, Any]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{KIT_RPC_BASE}{path}", json=body, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {"error": f"Kit RPC {path} returned {resp.status}"}
                return await resp.json()
    except Exception as e:
        logger.warning(f"[KitTools] POST {path} failed: {e}")
        return {"error": str(e)}


async def is_kit_rpc_alive() -> bool:
    result = await _get("/health")
    return result.get("ok", False)


async def get_stage_context(full: bool = False) -> Dict[str, Any]:
    """
    Pull stage summary (or full tree if full=True), selected prim
    properties, and recent warning/error logs from Kit.
    """
    return await _get("/context", params={"full": "true" if full else "false"})


async def get_viewport_image(max_dim: int = 1280) -> Dict[str, Any]:
    """Capture the active viewport and return base64 PNG."""
    return await _get("/capture", params={"max_dim": str(max_dim)})


async def queue_exec_patch(code: str, description: str = "") -> Dict[str, Any]:
    """
    Send Python patch code to Kit's approval queue.
    The extension UI will show a confirmation dialog before executing.
    """
    return await _post("/exec_patch", {"code": code, "description": description})


def format_stage_context_for_llm(ctx: Dict[str, Any]) -> str:
    """
    Converts the raw context snapshot into a compact text block
    suitable for injecting into an LLM system prompt (~300–800 tokens).
    """
    lines = []

    stage = ctx.get("stage", {})
    if "error" not in stage:
        lines.append(f"## Active Stage")
        lines.append(f"- URL: {stage.get('stage_url', 'unknown')}")
        lines.append(f"- Prim count: {stage.get('prim_count', '?')}")
        if stage.get("truncated"):
            lines.append("- (tree truncated at 500 prims)")
        if "tree" in stage:
            lines.append("- Tree (abbreviated):")
            lines.append(_tree_to_text(stage["tree"], indent=2, max_nodes=40))

    sel = ctx.get("selected_prim", {})
    if "error" not in sel and sel:
        lines.append(f"\n## Selected Prim: {sel.get('path', '?')}")
        lines.append(f"- Type: {sel.get('type', '?')}")
        if sel.get("world_position"):
            lines.append(f"- World position: {sel['world_position']}")
        if sel.get("physics"):
            lines.append(f"- Physics: {json.dumps(sel['physics'])}")
        attrs = sel.get("attributes", {})
        if attrs:
            # Only show first 8 attrs to stay concise
            preview = dict(list(attrs.items())[:8])
            lines.append(f"- Key attributes: {json.dumps(preview)}")

    logs = ctx.get("recent_logs", [])
    errors = [l for l in logs if l.get("level") in ("error", "fatal")]
    warnings = [l for l in logs if l.get("level") == "warning"]
    if errors:
        lines.append(f"\n## Console Errors ({len(errors)})")
        for e in errors[-5:]:
            lines.append(f"  [{e['source']}] {e['msg'][:120]}")
    if warnings:
        lines.append(f"\n## Console Warnings ({len(warnings)})")
        for w in warnings[-3:]:
            lines.append(f"  [{w['source']}] {w['msg'][:100]}")

    return "\n".join(lines) if lines else "(No scene context available)"


def _tree_to_text(nodes, indent=0, max_nodes=40, _count=[0]) -> str:
    lines = []
    prefix = " " * indent
    for node in nodes:
        if _count[0] >= max_nodes:
            lines.append(f"{prefix}...")
            break
        _count[0] += 1
        vis = "" if node.get("visibility") != "invisible" else " [hidden]"
        lines.append(f"{prefix}{node['path']} ({node['type']}){vis}")
        if "children" in node:
            lines.append(_tree_to_text(node["children"], indent + 2, max_nodes, _count))
    return "\n".join(lines)
