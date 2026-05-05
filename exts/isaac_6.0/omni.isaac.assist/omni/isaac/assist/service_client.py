"""HTTP client used by the Isaac Assist extension to talk to the
FastAPI orchestrator service (default localhost:8000).

Three concerns:
  1. Per-extension session_id (UUID) — prevents two extension windows
     from interleaving each other's SSE streams on the same uvicorn.
  2. POST endpoints for message / reset / cancel.
  3. SSE consumer for live progress events with auto-reconnect on drop.
     Reconnect uses exponential backoff capped at 16 s; the chat still
     works POST-only if SSE never connects.
"""
import asyncio
import json
import logging
import uuid

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)


class AssistServiceClient:
    """Connects the extension UI to the external orchestrator service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        session_id: str | None = None,
    ):
        self.base_url = base_url
        # Per-extension UUID. The legacy "default_session" string is gone —
        # multiple extension windows on the same uvicorn now have isolated
        # SSE streams and conversation histories.
        self.session_id = session_id or f"ext_{uuid.uuid4().hex[:8]}"
        self._stream_task: asyncio.Task | None = None
        self._stream_stop = False

    # ── POST endpoints ──────────────────────────────────────────────────
    async def reset_session(self) -> dict:
        """Clear server-side conversation history. Stage is untouched."""
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/reset"
        payload = {"session_id": self.session_id}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def send_message(self, text: str) -> dict:
        """Send a chat message; returns the canonical reply blob."""
        if not HAS_AIOHTTP:
            logger.warning(
                "aiohttp not installed in the Isaac Sim python environment. "
                "Mocking response."
            )
            await asyncio.sleep(1)
            return {"response_messages": [{"role": "assistant", "content": f"Mock echo: {text}"}]}

        url = f"{self.base_url}/api/v1/chat/message"
        payload = {
            "session_id": self.session_id,
            "message": text,
            "context": {},
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.error(f"Service returned status: {response.status}")
                    return {"error": "Failed to communicate with service"}
        except Exception as e:
            logger.error(f"Error communicating with Assist Service: {e}")
            return {"error": str(e)}

    async def cancel_turn(self) -> dict:
        """Request cancellation of the in-flight turn for this session.

        Server sets a flag the orchestrator polls between rounds and
        between tools. The currently-executing tool finishes; subsequent
        ones are skipped and a canned "Stopped" reply is generated.
        """
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/cancel"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": self.session_id}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {"error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"error": str(e)}

    async def undo_turn(self, steps: int = 1) -> dict:
        """Revert the last N stage-mutating turns. Server emits
        undo_started/undo_applied/undo_failed via SSE — the UI listens
        and dims the corresponding bubble(s)."""
        if not HAS_AIOHTTP:
            return {"ok": False, "error": "aiohttp missing"}
        url = f"{self.base_url}/api/v1/chat/undo"
        payload = {"session_id": self.session_id, "steps": steps}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def clear_chat(self) -> dict:
        """Wipe conversation history without touching the stage or
        the snapshot stack. Distinct from reset_session which also
        opens a fresh stage."""
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/clear_chat"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": self.session_id}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {"error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"error": str(e)}

    # ── SSE consumer ────────────────────────────────────────────────────
    def start_stream(self, on_event):
        """Start the SSE consumer in the background. Idempotent.

        on_event(event_type: str, payload: dict, raw_event: dict) -> None
            Called from the Kit asyncio loop. Safe to mutate omni.ui
            widgets directly. Handler exceptions are logged but never
            kill the stream.

        The consumer reconnects with exponential backoff (1, 2, 4, 8, 16 s)
        if the stream drops. Connection state changes are surfaced via the
        synthetic event type "__connection__" with payload {"state":
        "connected" | "reconnecting" | "stopped"} so the UI can render a
        health dot.
        """
        if self._stream_task and not self._stream_task.done():
            return
        self._stream_stop = False
        self._stream_task = asyncio.ensure_future(self._stream_loop(on_event))

    def stop_stream(self):
        """Tell the SSE consumer to exit and cancel its task.

        Nulls the task ref so a follow-up start_stream() can spawn a fresh
        one without waiting for cancel propagation. start_stream's guard
        (`if self._stream_task and not self._stream_task.done()`) sees
        None and proceeds.
        """
        self._stream_stop = True
        if self._stream_task:
            self._stream_task.cancel()
            self._stream_task = None

    async def _stream_loop(self, on_event):
        if not HAS_AIOHTTP:
            on_event("__connection__", {"state": "stopped"}, {})
            return
        url = f"{self.base_url}/api/v1/chat/stream/{self.session_id}"
        backoff = 1.0
        while not self._stream_stop:
            try:
                # No total timeout; sock_read protects against truly dead
                # connections (>30 s of silence) — server sends keepalive
                # comments every 15 s so this normally never trips.
                timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"SSE returned HTTP {resp.status}")
                            on_event("__connection__", {"state": "reconnecting"}, {})
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 16.0)
                            continue
                        backoff = 1.0  # connected — reset
                        on_event("__connection__", {"state": "connected"}, {})
                        await self._consume_sse_lines(resp, on_event)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"SSE loop error: {e} — reconnecting in {backoff}s")
                on_event("__connection__", {"state": "reconnecting"}, {})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 16.0)
        on_event("__connection__", {"state": "stopped"}, {})

    async def _consume_sse_lines(self, resp, on_event):
        """Parse the text/event-stream framing line by line.

        SSE frame format: "event: <type>\\ndata: <json>\\n\\n", with
        optional ":" comment lines for keepalive. We track the most
        recent event: line and pair it with the next data: line.
        """
        current_event = None
        async for raw_line in resp.content:
            if self._stream_stop:
                return
            line = raw_line.decode("utf-8", errors="ignore").rstrip("\n").rstrip("\r")
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    raw_evt = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                evt_type = current_event or raw_evt.get("type", "")
                payload = raw_evt.get("payload", raw_evt)
                try:
                    on_event(evt_type, payload, raw_evt)
                except Exception as e:
                    logger.exception(f"on_event handler raised: {e}")
            elif line.startswith(":"):
                # SSE comment (keepalive) — ignore.
                pass
            elif line == "":
                # End of one event frame; reset event-name latch.
                current_event = None
