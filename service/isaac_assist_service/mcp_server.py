"""
mcp_server.py
-------------
Model Context Protocol (MCP) server that exposes Isaac Assist tools to
external agent frameworks (OpenClaw, NemoClaw, Claude Desktop, etc.).

Runs as a standalone SSE or stdio transport alongside the main FastAPI app.
Converts our existing ISAAC_SIM_TOOLS (OpenAI function-calling format) into
MCP-compatible tool definitions and routes calls through tool_executor.

Usage:
  # SSE transport (recommended for OpenClaw / remote agents)
  python -m service.isaac_assist_service.mcp_server --transport sse --port 8002

  # Stdio transport (for local MCP clients like Claude Desktop)
  python -m service.isaac_assist_service.mcp_server --transport stdio
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP protocol primitives (no external dependency required)
# ---------------------------------------------------------------------------

class MCPServer:
    """
    Lightweight MCP server that translates Isaac Assist tools into the
    Model Context Protocol wire format.  Supports both SSE and stdio
    transports.
    """

    SERVER_INFO = {
        "name": "isaac-assist",
        "version": "1.0.0",
    }

    def __init__(self) -> None:
        from .chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        from .chat.tools.tool_executor import execute_tool_call
        from .settings.manager import SettingsManager
        self._openai_tools = ISAAC_SIM_TOOLS
        self._executor = execute_tool_call
        self._settings = SettingsManager()
        self._mcp_tools = self._convert_tools()

    # ── Tool conversion ─────────────────────────────────────────────────

    def _convert_tools(self) -> List[Dict[str, Any]]:
        """Convert OpenAI function-calling schemas to MCP tool format."""
        mcp_tools = []
        for tool in self._openai_tools:
            fn = tool.get("function", {})
            mcp_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

        # Settings management tools for MCP clients
        mcp_tools.append({
            "name": "get_settings",
            "description": "Retrieve current Isaac Assist configuration (LLM mode, model, auto-approve, etc.)",
            "inputSchema": {"type": "object", "properties": {}},
        })
        mcp_tools.append({
            "name": "update_settings",
            "description": "Update Isaac Assist configuration. Keys: OPENAI_API_BASE, OPENAI_API_KEY, CLOUD_MODEL_NAME, LLM_MODE, CONTRIBUTE_DATA, AUTO_APPROVE",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "description": "Key-value pairs to update, e.g. {\"AUTO_APPROVE\": \"true\"}",
                        "additionalProperties": {"type": "string"},
                    }
                },
                "required": ["settings"],
            },
        })
        return mcp_tools

    # ── JSON-RPC dispatch ───────────────────────────────────────────────

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single JSON-RPC 2.0 request and return the response.
        """
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method == "ping":
                result = {}
            else:
                return self._error_response(req_id, -32601, f"Method not found: {method}")
            return self._success_response(req_id, result)
        except Exception as e:
            logger.exception("MCP request failed: %s", method)
            return self._error_response(req_id, -32603, str(e))

    def _handle_initialize(self, params: Dict) -> Dict:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": self.SERVER_INFO,
        }

    def _handle_tools_list(self, params: Dict) -> Dict:
        return {"tools": self._mcp_tools}

    async def _handle_tools_call(self, params: Dict) -> Dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Validate tool exists
        valid_names = {t["name"] for t in self._mcp_tools}
        if tool_name not in valid_names:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

        # Handle settings tools locally
        if tool_name == "get_settings":
            data = self._settings.get_settings()
            return {
                "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
                "isError": False,
            }
        if tool_name == "update_settings":
            settings_dict = arguments.get("settings", {})
            ok = self._settings.update_settings(settings_dict)
            if not ok:
                return {
                    "content": [{"type": "text", "text": "Failed to update settings"}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(self._settings.get_settings(), indent=2)}],
                "isError": False,
            }

        result = await asyncio.to_thread(self._executor, tool_name, arguments)

        if result.get("type") == "error":
            return {
                "content": [{"type": "text", "text": result.get("error", "Unknown error")}],
                "isError": True,
            }

        # Format code patches and data results into MCP content blocks
        content = []
        if result.get("type") == "code_patch":
            content.append({
                "type": "text",
                "text": f"Generated code for `{tool_name}`:\n```python\n{result.get('code', '')}\n```\n\n{result.get('description', '')}",
            })
        elif result.get("type") == "data":
            content.append({
                "type": "text",
                "text": json.dumps(result.get("data", {}), indent=2),
            })
        else:
            content.append({"type": "text", "text": json.dumps(result, indent=2)})

        return {"content": content, "isError": False}

    # ── JSON-RPC helpers ────────────────────────────────────────────────

    @staticmethod
    def _success_response(req_id: Any, result: Any) -> Dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error_response(req_id: Any, code: int, message: str) -> Dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Transport: stdio
# ---------------------------------------------------------------------------

async def run_stdio(server: MCPServer) -> None:
    """Read JSON-RPC from stdin, write responses to stdout."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout.buffer
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

    buffer = b""
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        buffer += chunk
        # Process complete JSON objects separated by newlines
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip notifications (no id)
            if "id" not in request:
                continue

            response = await server.handle_request(request)
            response_bytes = json.dumps(response).encode() + b"\n"
            writer.write(response_bytes)
            await writer.drain()


# ---------------------------------------------------------------------------
# Transport: SSE over HTTP (aiohttp)
# ---------------------------------------------------------------------------

async def run_sse(server: MCPServer, host: str, port: int) -> None:
    """SSE transport — POST /mcp for requests, GET /mcp/sse for event stream."""
    try:
        from aiohttp import web
    except ImportError:
        logger.error("aiohttp is required for SSE transport: pip install aiohttp")
        return

    pending_responses: asyncio.Queue = asyncio.Queue()

    async def handle_sse(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)

        # Send endpoint info
        await resp.write(f"event: endpoint\ndata: /mcp\n\n".encode())

        while True:
            try:
                msg = await asyncio.wait_for(pending_responses.get(), timeout=30)
                await resp.write(f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
            except asyncio.TimeoutError:
                # Keep-alive ping
                await resp.write(b":ping\n\n")
            except ConnectionResetError:
                break
        return resp

    async def handle_post(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status=400,
            )
        response = await server.handle_request(body)
        await pending_responses.put(response)
        return web.json_response({"status": "accepted"})

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "server": "isaac-assist-mcp", "version": "1.0.0"})

    app = web.Application()
    app.router.add_get("/mcp/sse", handle_sse)
    app.router.add_post("/mcp", handle_post)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    logger.info("MCP SSE server listening on %s:%d", host, port)
    await site.start()

    # Run forever
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Isaac Assist MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="sse")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    server = MCPServer()
    logger.info("Loaded %d tools for MCP", len(server._mcp_tools))

    if args.transport == "stdio":
        asyncio.run(run_stdio(server))
    else:
        asyncio.run(run_sse(server, args.host, args.port))


if __name__ == "__main__":
    main()
