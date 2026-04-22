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
import contextlib
import io
import json
import queue as stdlib_queue
import threading
import carb
from typing import Optional

_PATCH_QUEUE: asyncio.Queue = asyncio.Queue()  # pending code patches awaiting approval
_server_instance: Optional["KitRPCServer"] = None

# ── Synchronous execution queue (main-thread dispatch) ────────────────────────
# Items are (code: str, result_holder: dict) where result_holder has:
#   "result": None | {"success": bool, "output": str}
#   "event":  threading.Event
_SYNC_EXEC_QUEUE: stdlib_queue.Queue = stdlib_queue.Queue()
_exec_sub = None  # Kit update subscription handle


def _kit_exec_tick(event):
    """
    Called every frame on Kit's main thread via the update event stream.
    Drains the sync execution queue and runs code in Kit's Python context.
    """
    while True:
        try:
            code, result_holder = _SYNC_EXEC_QUEUE.get_nowait()
        except stdlib_queue.Empty:
            break

        output_buf = io.StringIO()
        success = False
        try:
            with contextlib.redirect_stdout(output_buf), contextlib.redirect_stderr(output_buf):
                exec_globals = {"__builtins__": __builtins__}
                exec(code, exec_globals)
            success = True
        except Exception as e:
            output_buf.write(f"\nError: {e}")

        result_holder["result"] = {
            "success": success,
            "output": output_buf.getvalue(),
        }
        result_holder["event"].set()


def start_exec_tick():
    """Register the main-thread execution tick. Call from extension on_startup."""
    global _exec_sub
    try:
        import omni.kit.app
        _exec_sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(_kit_exec_tick, name="IsaacAssist-ExecSync")
        )
        carb.log_warn("[IsaacAssist] Registered main-thread exec_sync tick")
    except Exception as e:
        carb.log_warn(f"[IsaacAssist] Failed to register exec_sync tick: {e}")


def stop_exec_tick():
    """Unregister the main-thread execution tick."""
    global _exec_sub
    _exec_sub = None


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
        self._serve_task = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="IsaacAssist-RPC")
        self._thread.start()
        _server_instance = self
        carb.log_warn(f"[IsaacAssist] Kit RPC server starting on {self.host}:{self.port}")

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            # Cancel the serve task so _runner.cleanup() runs inside the loop
            def _cancel():
                if self._serve_task and not self._serve_task.done():
                    self._serve_task.cancel()
            self._loop.call_soon_threadsafe(_cancel)
        if self._thread:
            self._thread.join(timeout=5)
        # Remove the port file
        try:
            import os
            os.unlink("/tmp/isaac_assist_rpc_port")
        except Exception:
            pass
        carb.log_warn("[IsaacAssist] Kit RPC server stopped")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except RuntimeError:
            pass  # loop stopped before future completed — expected on shutdown
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/context", self._handle_context)
        app.router.add_get("/capture", self._handle_capture)
        app.router.add_post("/exec_patch", self._handle_exec_patch)
        app.router.add_post("/exec_sync", self._handle_exec_sync)
        app.router.add_get("/selection", self._handle_selection)
        app.router.add_post("/sim_control", self._handle_sim_control)
        app.router.add_post("/set_viewport_camera", self._handle_set_viewport_camera)
        app.router.add_get("/list_prims", self._handle_list_prims)
        app.router.add_post("/check_placement", self._handle_check_placement)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        # Try the configured port first, then fall back to the next 9 ports
        bound_port = None
        for candidate in range(self.port, self.port + 10):
            try:
                site = web.TCPSite(self._runner, self.host, candidate,
                                   reuse_address=True)
                await site.start()
                bound_port = candidate
                break
            except OSError:
                carb.log_warn(f"[IsaacAssist] Port {candidate} in use, trying next...")

        if bound_port is None:
            carb.log_error("[IsaacAssist] Could not bind Kit RPC server on any port in range "
                           f"{self.port}-{self.port + 9}. Kit RPC disabled.")
            return

        self.port = bound_port
        # Write bound port to a well-known file so the FastAPI service can discover it
        try:
            with open("/tmp/isaac_assist_rpc_port", "w") as _pf:
                _pf.write(str(bound_port))
        except Exception:
            pass
        carb.log_warn(f"[IsaacAssist] Kit RPC listening on http://{self.host}:{self.port}")

        # Keep the server alive until cancelled
        self._serve_task = asyncio.current_task()
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

    async def _handle_exec_sync(self, request) -> "web.Response":
        """
        Execute Python code synchronously on Kit's main thread and return
        stdout/stderr + success flag.  Used by the pipeline executor.
        Body: {"code": "...", "timeout": 30}
        """
        from aiohttp import web
        try:
            body = await request.json()
            code = body.get("code", "").strip()
            timeout = body.get("timeout", 30)
            if not code:
                return web.json_response({"error": "No code provided"}, status=400)

            result_holder = {"result": None, "event": threading.Event()}
            _SYNC_EXEC_QUEUE.put((code, result_holder))

            # Wait on a thread so we don't block the aiohttp event loop
            loop = asyncio.get_event_loop()
            completed = await loop.run_in_executor(
                None, lambda: result_holder["event"].wait(timeout=timeout)
            )

            if not completed or result_holder["result"] is None:
                return web.json_response(
                    {"success": False, "output": "Execution timed out", "error": "timeout"},
                    status=504,
                )

            return web.json_response(result_holder["result"])
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_selection(self, request) -> "web.Response":
        """Return the currently selected prim path(s) and properties."""
        from aiohttp import web
        try:
            from .prim_properties import get_selected_prim_properties
            sel = get_selected_prim_properties()
            return web.json_response(sel)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_sim_control(self, request) -> "web.Response":
        """
        Control the simulation timeline.
        Body: {"action": "play"|"pause"|"stop"|"step"|"reset", "step_count": N}
        """
        from aiohttp import web
        try:
            body = await request.json()
            action = body.get("action", "").lower()
            code_map = {
                "play": "import omni.timeline; omni.timeline.get_timeline_interface().play()",
                "pause": "import omni.timeline; omni.timeline.get_timeline_interface().pause()",
                "stop": "import omni.timeline; omni.timeline.get_timeline_interface().stop()",
                "reset": (
                    "import omni.timeline\n"
                    "tl = omni.timeline.get_timeline_interface()\n"
                    "tl.stop(); tl.set_current_time(0)"
                ),
            }
            if action == "step":
                n = body.get("step_count", 1)
                code = f"import omni.timeline\ntl = omni.timeline.get_timeline_interface()\nfor _ in range({n}): tl.forward_one_frame()"
            elif action in code_map:
                code = code_map[action]
            else:
                return web.json_response({"error": f"Unknown action: {action}"}, status=400)

            await _PATCH_QUEUE.put({"code": code, "description": f"sim_control: {action}", "auto_approve": True})
            return web.json_response({"ok": True, "action": action})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_set_viewport_camera(self, request) -> "web.Response":
        """
        Switch the active viewport camera.
        Body: {"camera_path": "/World/Camera"}
        """
        from aiohttp import web
        try:
            body = await request.json()
            camera_path = body.get("camera_path", "")
            if not camera_path:
                return web.json_response({"error": "No camera_path provided"}, status=400)
            code = (
                "import omni.kit.viewport.utility\n"
                f"omni.kit.viewport.utility.get_active_viewport().camera_path = '{camera_path}'"
            )
            await _PATCH_QUEUE.put({"code": code, "description": f"Set viewport camera to {camera_path}", "auto_approve": True})
            return web.json_response({"ok": True, "camera_path": camera_path})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_list_prims(self, request) -> "web.Response":
        """
        List prims, optionally filtered by type.
        Query params: ?filter_type=Mesh&under_path=/World
        """
        from aiohttp import web
        try:
            from .stage_reader import get_stage_tree
            tree = get_stage_tree()
            filter_type = request.rel_url.query.get("filter_type")
            under_path = request.rel_url.query.get("under_path", "/")

            # Flatten tree to list of paths with types
            prims = []
            def _flatten(nodes, depth=0):
                for n in nodes:
                    path = n.get("path", "")
                    ptype = n.get("type", "")
                    if path.startswith(under_path):
                        if not filter_type or ptype == filter_type:
                            prims.append({"path": path, "type": ptype})
                    _flatten(n.get("children", []), depth + 1)

            _flatten(tree.get("tree", []))
            return web.json_response({"prims": prims, "count": len(prims)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_placement(self, request) -> "web.Response":
        """
        Test if a box at a given position collides with anything in the scene.
        Uses PhysX overlap_box — no prim creation needed, runs in microseconds.
        Requires PhysicsScene and existing objects with CollisionAPI.
        """
        from aiohttp import web
        try:
            data = await request.json()
            half_extents = data["half_extents"]  # [x, y, z]
            position = data["position"]          # [x, y, z]
            rotation = data.get("rotation", [0, 0, 0, 1])  # quaternion xyzw

            code = (
                "import json\n"
                "from omni.physx import get_physx_scene_query_interface\n"
                "import carb\n"
                "\n"
                "_collisions = []\n"
                "def _report_hit(hit):\n"
                "    _collisions.append(str(hit.rigid_body))\n"
                "    return True\n"
                "\n"
                "sq = get_physx_scene_query_interface()\n"
                f"sq.overlap_box(\n"
                f"    carb.Float3({half_extents[0]}, {half_extents[1]}, {half_extents[2]}),\n"
                f"    carb.Float3({position[0]}, {position[1]}, {position[2]}),\n"
                f"    carb.Float4({rotation[0]}, {rotation[1]}, {rotation[2]}, {rotation[3]}),\n"
                f"    _report_hit\n"
                f")\n"
                "print(json.dumps({'collisions': _collisions, 'clear': len(_collisions) == 0}))"
            )

            result_holder = {"result": None, "event": threading.Event()}
            _SYNC_EXEC_QUEUE.put((code, result_holder))

            loop = asyncio.get_event_loop()
            completed = await loop.run_in_executor(
                None, lambda: result_holder["event"].wait(timeout=5)
            )

            if not completed or result_holder["result"] is None:
                return web.json_response(
                    {"collisions": [], "clear": True, "warning": "PhysX query timed out"},
                )

            # Parse the JSON output from the executed code
            output = result_holder["result"].get("output", "").strip()
            try:
                parsed = json.loads(output)
                return web.json_response(parsed)
            except (json.JSONDecodeError, ValueError):
                return web.json_response({"collisions": [], "clear": True, "raw_output": output})

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
