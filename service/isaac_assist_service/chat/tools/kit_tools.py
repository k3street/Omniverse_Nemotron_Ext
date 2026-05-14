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
_KIT_RPC_DEFAULT = "http://127.0.0.1:8001"


def _get_kit_rpc_base() -> str:
    """Read the port the Kit RPC server actually bound to (written to /tmp at startup)."""
    try:
        with open("/tmp/isaac_assist_rpc_port") as f:
            port = int(f.read().strip())
            return f"http://127.0.0.1:{port}"
    except Exception:
        return _KIT_RPC_DEFAULT


KIT_RPC_BASE = _KIT_RPC_DEFAULT  # kept for backward compat; _get_kit_rpc_base() used at call time


async def _get(path: str, params: Dict = None) -> Dict[str, Any]:
    base = _get_kit_rpc_base()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base}{path}", params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {"error": f"Kit RPC {path} returned {resp.status}"}
                return await resp.json()
    except Exception as e:
        logger.warning(f"[KitTools] GET {path} failed: {e}")
        return {"error": str(e)}


async def _post(path: str, body: Dict) -> Dict[str, Any]:
    base = _get_kit_rpc_base()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}{path}", json=body, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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


async def get_viewport_image(max_dim: int = 512) -> Dict[str, Any]:
    """Capture the active viewport and return base64 PNG.

    Default 512px: fits in 1-2 Anthropic vision tiles (~1700-3400 tokens as a
    proper image block vs ~150 K+ tokens if the base64 leaks into text).
    Hard-capped at 768px — higher resolutions rarely improve LLM scene
    understanding and dramatically inflate context size.
    """
    capped = min(max_dim, 768)
    return await _get("/capture", params={"max_dim": str(capped)})


async def queue_exec_patch(code: str, description: str = "") -> Dict[str, Any]:
    """
    Send Python patch code to Kit's approval queue.
    The extension UI will show a confirmation dialog before executing.

    When AUTO_APPROVE=true in env, bypass the queue and execute immediately
    via /exec_sync — required for MCP flows where no approval UI drains the queue.
    """
    import os
    if os.environ.get("AUTO_APPROVE", "false").lower() == "true":
        result = await exec_sync(code)
        success = result.get("success", False)
        output = result.get("output", "")
        out: Dict[str, Any] = {
            "queued": False,
            "executed": True,
            "success": success,
            "output": output,
        }
        if not success and "error" not in out:
            out["error"] = (output or "").strip() or "Kit RPC execution failed"
        return out
    result = await _post("/exec_patch", {"code": code, "description": description})
    if isinstance(result, dict) and result.get("success") is False and "error" not in result:
        result["error"] = (result.get("output") or "").strip() or "Kit RPC patch failed"
    return result


async def exec_sync(code: str, timeout: float = 300) -> Dict[str, Any]:
    """
    Execute Python code synchronously on Kit's main thread.
    Returns {"success": bool, "output": str}.
    Used by the pipeline executor for phased execution with verification.
    """
    try:
        import aiohttp
        base = _get_kit_rpc_base()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/exec_sync",
                json={"code": code, "timeout": timeout},
                timeout=aiohttp.ClientTimeout(total=timeout + 5),
            ) as resp:
                if resp.status != 200:
                    return {"success": False, "output": f"Kit RPC /exec_sync returned {resp.status}"}
                return await resp.json()
    except Exception as e:
        logger.warning(f"[KitTools] exec_sync failed: {e}")
        return {"success": False, "output": str(e)}


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
        total = stage.get("prim_count", None)
        user_n, system_n = _count_user_vs_system_prims(stage.get("tree", []))
        if total is not None and (user_n + system_n) > 0:
            # Break the total down so the agent doesn't quote "15 prims" to the
            # user when only 3 of them are user-authored. Observed 2026-04-19:
            # agent said "scene has 15 prims" after user created 2 cubes + 1 dome,
            # triggering confusion ("det är väl bara 3?"). The system prim count
            # includes /Render, /OmniverseKit_*, /persistent — default stage.
            lines.append(
                f"- Prim count: {total} total — {user_n} user-authored (under /World), "
                f"{system_n} default/system (render settings, cameras, etc.)"
            )
        else:
            lines.append(f"- Prim count: {total if total is not None else '?'}")
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


# Prim-path prefixes that Kit / Isaac Sim authors automatically on any fresh
# stage. Anything under these is "system" (render settings, default cameras,
# persistent layer machinery) — not part of what the user created.
_SYSTEM_PRIM_PREFIXES = (
    "/Render",
    "/OmniverseKit",
    "/omni",
    "/persistent",
)
# Note: /Environment is user-authored (create_hdri_skydome lands lights there)
# and intentionally NOT in _SYSTEM_PRIM_PREFIXES — it counts toward user prims.


def _count_user_vs_system_prims(nodes) -> tuple[int, int]:
    """Walk the tree from stage_reader and split nodes into user vs system.
    User = under /World (or any other non-system top-level). System = under
    the _SYSTEM_PRIM_PREFIXES list. Root-level default prims (like /cameras)
    also count as system.
    """
    user = 0
    system = 0

    def _walk(node):
        nonlocal user, system
        path = node.get("path", "")
        if path:
            # Allow prefix followed by '/' (child under prefix), '_' (Kit
            # style /OmniverseKit_Persp), or exact match (the root prim itself).
            is_sys = any(
                path == p or path.startswith(p + "/") or path.startswith(p + "_")
                for p in _SYSTEM_PRIM_PREFIXES
            )
            if is_sys:
                system += 1
            else:
                user += 1
        for child in node.get("children", []) or []:
            _walk(child)

    for top in nodes or []:
        _walk(top)
    return user, system


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
