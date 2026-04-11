"""
kit_rpc.py
-----------
A lightweight aiohttp HTTP server that runs INSIDE the Kit (Isaac Sim) process
on port 8001. The FastAPI service (port 8000) calls this server inward to pull
scene context, request viewport captures, and push patch code for approval.

Lifecycle:
  - Start from extension.on_startup()  → KitRPCServer().start()
  - Stop from extension.on_shutdown()  → KitRPCServer().stop()
"""
from __future__ import annotations
import asyncio
import json
import threading
import carb
from typing import Optional

_PATCH_QUEUE: asyncio.Queue = asyncio.Queue()  # pending code patches awaiting approval
_server_instance: Optional["KitRPCServer"] = None


def get_server() -> Optional["KitRPCServer"]:
    return _server_instance


def pop_pending_patch() -> Optional[str]:
    """Extension UI calls this to check if there's a patch waiting for approval."""
    try:
        return _PATCH_QUEUE.get_nowait()
    except Exception:
        return None


class KitRPCServer:
    """
    Runs aiohttp in a background thread so it doesn't block Kit's main loop.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8001):
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._runner = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        global _server_instance
        try:
            import aiohttp
        except ImportError:
            carb.log_warn("[IsaacAssist] aiohttp not found — Kit RPC disabled. "
                          "Install with: pip install aiohttp")
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="IsaacAssist-RPC")
        self._thread.start()
        _server_instance = self
        carb.log_warn(f"[IsaacAssist] Kit RPC server starting on {self.host}:{self.port}")

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
        carb.log_warn("[IsaacAssist] Kit RPC server stopped")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/context", self._handle_context)
        app.router.add_get("/capture", self._handle_capture)
        app.router.add_post("/exec_patch", self._handle_exec_patch)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        carb.log_warn(f"[IsaacAssist] Kit RPC listening on http://{self.host}:{self.port}")

        # Keep the server alive until the event loop is stopped
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self._runner.cleanup()

    # ── Route handlers ────────────────────────────────────────────────────────

    async def _handle_health(self, request) -> "web.Response":
        from aiohttp import web
        return web.json_response({"ok": True, "service": "isaac-assist-kit-rpc"})

    async def _handle_context(self, request) -> "web.Response":
        """
        Returns a combined context snapshot:
        stage summary, selected prim properties, and recent warning/error logs.
        """
        from aiohttp import web

        # Run Kit API calls on the main asyncio loop (this IS the background loop).
        # Since we are not on Kit's main thread, use simple synchronous calls
        # which are safe for read-only USD access.
        try:
            from .stage_reader import get_stage_summary, get_stage_tree
            from .prim_properties import get_selected_prim_properties
            from .console_log import get_recent_logs

            full = request.rel_url.query.get("full", "false").lower() == "true"
            stage_data = get_stage_tree() if full else get_stage_summary()

            payload = {
                "stage": stage_data,
                "selected_prim": get_selected_prim_properties(),
                "recent_logs": get_recent_logs(n=30, min_level="warning"),
            }
            return web.json_response(payload)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_capture(self, request) -> "web.Response":
        """Triggers a viewport screenshot and returns base64 PNG."""
        from aiohttp import web
        try:
            from .viewport_capture import capture_viewport_png
            max_dim = int(request.rel_url.query.get("max_dim", "1280"))
            result = await capture_viewport_png(max_dim=max_dim)
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_exec_patch(self, request) -> "web.Response":
        """
        Queues a Python code patch for approval in the extension UI.
        Body: {"code": "...", "description": "..."}
        """
        from aiohttp import web
        try:
            body = await request.json()
            code = body.get("code", "").strip()
            if not code:
                return web.json_response({"error": "No code provided"}, status=400)
            await _PATCH_QUEUE.put(body)
            carb.log_warn(f"[IsaacAssist] Patch queued for approval: {code[:80]}...")
            return web.json_response({"queued": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
