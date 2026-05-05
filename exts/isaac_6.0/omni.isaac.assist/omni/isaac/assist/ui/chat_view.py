"""Chat panel for the Isaac Assist extension.

Phase 3 of the live-progress UI. The window streams events from the
backend's SSE channel and renders a live strip below the chat history
showing each tool call as it happens (verb-first phrasing, args
preview, elapsed time, spinner glyph that cycles every 200 ms). On
turn completion the strip clears and the assistant's reply lands in
the chat scroll with a brief border pulse.

Phases 4-7 will layer Stop button state, diff chip, undo, and text
scaling on top of this foundation. Pre-existing functionality (New
Scene, LiveKit Vision toggle) is preserved.
"""
import omni.ui as ui
import asyncio
import logging
import os
import json
import time
from typing import Optional, Dict

from ..service_client import AssistServiceClient
from ..webrtc_client import ViewportWebRTCClient
from .verbs import verb_for
from . import animations as anim

logger = logging.getLogger(__name__)

# ── Color palette (omni.ui ABGR: 0xAABBGGRR) ─────────────────────────────
COL_BG_USER         = 0xFF2A2E33
COL_BG_ASSIST       = 0xFF1E2125
COL_BG_LIVE_STRIP   = 0xFF181A1D
COL_TEXT            = 0xFFDDDDDD
COL_TEXT_DIM        = 0xFF8A8E92
COL_TEXT_SUBTLE    = 0xFF666A6E
COL_NV_GREEN        = 0xFF00B976  # NVIDIA #76B900 → ABGR
COL_AMBER           = 0xFF00A8FF
COL_AMBER_DIM       = 0xFF0078B0
COL_RED             = 0xFF4444FF
COL_DOT_GOOD        = 0xFF00B976
COL_DOT_WARN        = 0xFF00A8FF
COL_DOT_BAD         = 0xFF4444FF
COL_BORDER_PULSE    = 0xFF00B976
COL_BORDER_NEUTRAL  = 0x00000000

# ── Spinner glyphs (Braille; slow + calm cadence for peripheral panel) ──
SPINNER_GLYPHS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_INTERVAL_S = 0.20
SLOW_THRESHOLD_S = 10.0
VERY_SLOW_THRESHOLD_S = 20.0


class ChatViewWindow(ui.Window):
    def __init__(self, title: str, **kwargs):
        super().__init__(title, **kwargs)
        self.service = AssistServiceClient()  # generates per-instance UUID
        self.webrtc = None

        # Turn lifecycle state
        self._turn_active = False
        self._turn_rendered_via_sse = False

        # Live strip rows by tc_id (plus the special "__thinking__" row
        # placeholder shown between turn_started and the first real tool).
        self._live_rows: Dict[str, Dict] = {}
        self._live_strip_visible = False
        self._spin_task: Optional[asyncio.Task] = None
        self._destroyed = False

        self._build_ui()
        self.service.start_stream(self._on_sse_event)
        self._spin_task = asyncio.ensure_future(self._tick_loop())

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=4):
                self._build_header()
                self._build_chat_area()
                self._build_live_strip()
                self._build_input()

    def _build_header(self):
        with ui.HStack(height=26, spacing=6):
            ui.Label(
                "Isaac Assist",
                width=0,
                style={"color": COL_TEXT, "font_size": 13},
            )
            # 6 px connection-health dot. Green = SSE connected; amber =
            # reconnecting; red = exhausted/stopped. State transitions
            # are wired in Phase 5 polish.
            self.conn_dot = ui.Rectangle(
                width=6,
                height=6,
                style={"background_color": COL_DOT_GOOD, "border_radius": 3},
            )
            ui.Spacer()
            self.btn_new = ui.Button(
                "New",
                width=44,
                height=22,
                clicked_fn=self._new_scene,
                style={"font_size": 11},
            )
            self.btn_livekit = ui.Button(
                "Vision",
                width=58,
                height=22,
                clicked_fn=self._toggle_livekit,
                style={"font_size": 11},
            )

    def _build_chat_area(self):
        self.scroll = ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
        )
        with self.scroll:
            self.chat_layout = ui.VStack(spacing=8)

    def _build_live_strip(self):
        # Container collapses to height=0 between turns. ZStack lets the
        # background rectangle sit behind the row layout.
        self.live_strip_container = ui.ZStack(height=0)
        with self.live_strip_container:
            ui.Rectangle(
                style={"background_color": COL_BG_LIVE_STRIP, "border_radius": 4}
            )
            with ui.VStack(spacing=2):
                ui.Spacer(height=4)
                with ui.HStack(height=14):
                    ui.Spacer(width=6)
                    ui.Label(
                        "Live",
                        width=0,
                        style={"color": COL_TEXT_SUBTLE, "font_size": 10},
                    )
                    ui.Spacer()
                self.live_rows_layout = ui.VStack(spacing=2)
                ui.Spacer(height=4)

    def _build_input(self):
        with ui.HStack(height=28, spacing=4):
            self.input_field = ui.StringField(multiline=False, style={"font_size": 12})
            self.btn_send = ui.Button(
                "Send",
                width=64,
                height=24,
                clicked_fn=self._submit_message,
                style={"font_size": 12},
            )

    # ═══════════════════════════════════════════════════════════════════════
    # Submit / receive
    # ═══════════════════════════════════════════════════════════════════════
    def _submit_message(self):
        text = self.input_field.model.get_value_as_string().strip()
        if not text:
            return
        if self._turn_active:
            return  # one turn at a time; Phase 4 will add the Stop button
        self.input_field.model.set_value("")
        self._add_user_bubble(text)
        self._turn_active = True
        self._turn_rendered_via_sse = False
        asyncio.ensure_future(self._handle_service_request(text))

    async def _handle_service_request(self, text: str):
        try:
            response = await self.service.send_message(text)
            if not self._turn_rendered_via_sse:
                # SSE didn't deliver agent_reply (drop, slow); render from POST
                self._render_assistant_from_post(response)
                self._collapse_live_strip()
        finally:
            self._turn_active = False

    def _render_assistant_from_post(self, response: dict):
        if "error" in response:
            self._add_assistant_bubble(response["error"], error=True)
            return
        for msg in response.get("response_messages", []):
            content = msg.get("content", "")
            if content:
                self._add_assistant_bubble(content)

    # ═══════════════════════════════════════════════════════════════════════
    # SSE event router
    # ═══════════════════════════════════════════════════════════════════════
    def _on_sse_event(self, evt_type: str, payload: dict, raw: dict):
        try:
            if evt_type == "__connection__":
                self._on_connection_state(payload.get("state"))
            elif evt_type == "turn_started":
                self._on_turn_started(payload)
            elif evt_type == "tool_call_started":
                self._on_tool_started(payload)
            elif evt_type == "tool_call_finished":
                self._on_tool_finished(payload)
            elif evt_type == "retry_spam_halt":
                self._on_spam_halt(payload)
            elif evt_type == "agent_reply":
                self._on_agent_reply(payload)
        except Exception as e:
            logger.exception(f"SSE handler failed for {evt_type}: {e}")

    # ── Turn lifecycle ───────────────────────────────────────────────────
    def _on_turn_started(self, payload):
        self._show_live_strip()
        self._add_thinking_row()

    def _on_tool_started(self, payload):
        tc_id = payload.get("tc_id", f"unk_{time.time()}")
        tool = payload.get("tool", "unknown")
        args_preview = payload.get("args_preview", "")
        description = payload.get("description", "")

        self._remove_thinking_row()

        # Retry-storm compression: if the previous attempt of this same
        # tool failed, recycle that row and bump an attempts counter
        # rather than stacking N near-identical rows.
        existing = self._find_recyclable_row(tool)
        if existing is not None:
            existing["attempts"] += 1
            existing["spinner_lbl"].text = SPINNER_GLYPHS[0]
            existing["spinner_lbl"].style = {"color": COL_NV_GREEN, "font_size": 13}
            verb_text = f"{verb_for(tool)} ×{existing['attempts']}"
            existing["verb_lbl"].text = verb_text
            existing["args_lbl"].text = args_preview
            existing["state"] = "running"
            existing["started_at"] = time.monotonic()
            existing["tc_id"] = tc_id
            # Re-key in the dict
            self._live_rows[tc_id] = existing
            return

        self._make_live_row(tc_id, tool, args_preview, payload.get("args_full", {}), description)

    def _on_tool_finished(self, payload):
        tc_id = payload.get("tc_id", "")
        row = self._live_rows.get(tc_id)
        if not row:
            return
        success = payload.get("success", True)
        elapsed_ms = payload.get("elapsed_ms", 0)
        row["state"] = "done_ok" if success else "done_fail"
        row["elapsed_lbl"].text = f"{elapsed_ms / 1000:.1f}s"
        if success:
            row["spinner_lbl"].text = "✓"
            spinner = row["spinner_lbl"]
            verb = row["verb_lbl"]
            asyncio.ensure_future(
                anim.lerp_color(
                    lambda c, w=spinner: w.__setattr__(
                        "style", {**(w.style or {}), "color": c}
                    ),
                    COL_NV_GREEN,
                    COL_TEXT_DIM,
                    ms=400,
                )
            )
            asyncio.ensure_future(
                anim.lerp_color(
                    lambda c, w=verb: w.__setattr__(
                        "style", {**(w.style or {}), "color": c}
                    ),
                    COL_TEXT,
                    COL_TEXT_DIM,
                    ms=400,
                )
            )
        else:
            row["spinner_lbl"].text = "✗"
            row["spinner_lbl"].style = {"color": COL_RED, "font_size": 13}
            err = payload.get("error", "")
            if err:
                err_short = (str(err)[:50] + "…") if len(str(err)) > 50 else str(err)
                row["args_lbl"].text = err_short
                row["args_lbl"].style = {"color": COL_RED, "font_size": 11}

    def _on_spam_halt(self, payload):
        with self.live_rows_layout:
            with ui.HStack(height=14):
                ui.Spacer(width=10)
                n = payload.get("consecutive_fails", 0)
                ui.Label(
                    f"⚠ Stopped after {n} failed attempts",
                    style={"color": COL_AMBER, "font_size": 11},
                )

    def _on_agent_reply(self, payload):
        text = payload.get("text", "")
        bubble = self._add_assistant_bubble(text)
        # Pulse the bubble border to mark "turn complete" peripherally.
        if bubble and "border_rect" in bubble:
            asyncio.ensure_future(
                anim.pulse_widget(
                    bubble["border_rect"],
                    "background_color",
                    COL_BORDER_NEUTRAL,
                    COL_BORDER_PULSE,
                    up_ms=300,
                    down_ms=700,
                )
            )
        self._collapse_live_strip()
        self._turn_rendered_via_sse = True

    def _on_connection_state(self, state):
        # Color-only state for now. Phase 5 polish will add the slow-pulse
        # animation when reconnecting.
        if state == "connected":
            self.conn_dot.style = {
                "background_color": COL_DOT_GOOD,
                "border_radius": 3,
            }
        elif state == "reconnecting":
            self.conn_dot.style = {
                "background_color": COL_DOT_WARN,
                "border_radius": 3,
            }
        elif state in ("stopped", "error"):
            self.conn_dot.style = {
                "background_color": COL_DOT_BAD,
                "border_radius": 3,
            }

    # ═══════════════════════════════════════════════════════════════════════
    # Live strip operations
    # ═══════════════════════════════════════════════════════════════════════
    def _show_live_strip(self):
        self._live_strip_visible = True
        self._bump_strip_height()

    def _collapse_live_strip(self):
        self._live_strip_visible = False
        self.live_strip_container.height = ui.Pixel(0)
        self.live_rows_layout.clear()
        self._live_rows.clear()

    def _add_thinking_row(self):
        """Placeholder row shown between turn_started and the first tool."""
        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                spin = ui.Label(
                    SPINNER_GLYPHS[0],
                    width=14,
                    style={"color": COL_NV_GREEN, "font_size": 13},
                )
                lbl = ui.Label(
                    "Thinking…",
                    width=0,
                    style={"color": COL_TEXT_DIM, "font_size": 12},
                )
        self._live_rows["__thinking__"] = {
            "row": row,
            "spinner_lbl": spin,
            "verb_lbl": lbl,
            "args_lbl": lbl,  # alias
            "elapsed_lbl": lbl,
            "started_at": time.monotonic(),
            "state": "running",
            "tool": "__thinking__",
            "attempts": 1,
            "tc_id": "__thinking__",
        }
        self._bump_strip_height()

    def _remove_thinking_row(self):
        if "__thinking__" in self._live_rows:
            r = self._live_rows.pop("__thinking__")
            try:
                r["row"].visible = False
            except Exception:
                pass
            self._bump_strip_height()

    def _make_live_row(
        self,
        tc_id: str,
        tool: str,
        args_preview: str,
        args_full: dict,
        description: str = "",
    ):
        verb = verb_for(tool)
        full_args_json = json.dumps(args_full, default=str)[:500]
        # Tooltip layout: description on top (if any), then signature.
        tooltip_parts = []
        if description:
            tooltip_parts.append(description)
        tooltip_parts.append(f"{tool}({full_args_json})")
        verb_tooltip = "\n\n".join(tooltip_parts)

        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                spinner_lbl = ui.Label(
                    SPINNER_GLYPHS[0],
                    width=14,
                    style={"color": COL_NV_GREEN, "font_size": 13},
                )
                verb_lbl = ui.Label(
                    verb,
                    width=0,
                    style={"color": COL_TEXT, "font_size": 12},
                    tooltip=verb_tooltip,
                )
                args_lbl = ui.Label(
                    args_preview,
                    width=0,
                    style={"color": COL_TEXT_DIM, "font_size": 11},
                    tooltip=full_args_json,
                )
                ui.Spacer()
                elapsed_lbl = ui.Label(
                    "0.0s",
                    width=40,
                    style={"color": COL_TEXT_SUBTLE, "font_size": 10},
                )
                ui.Spacer(width=4)

        self._live_rows[tc_id] = {
            "row": row,
            "spinner_lbl": spinner_lbl,
            "verb_lbl": verb_lbl,
            "args_lbl": args_lbl,
            "elapsed_lbl": elapsed_lbl,
            "started_at": time.monotonic(),
            "state": "running",
            "tool": tool,
            "attempts": 1,
            "tc_id": tc_id,
        }
        self._bump_strip_height()

    def _find_recyclable_row(self, tool: str) -> Optional[dict]:
        candidates = [
            r for r in self._live_rows.values()
            if r.get("tool") == tool and r.get("state") == "done_fail"
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r["started_at"])

    def _bump_strip_height(self):
        n = sum(1 for r in self._live_rows.values() if getattr(r["row"], "visible", True))
        # 14 (header) + 18 per row + 8 padding
        self.live_strip_container.height = ui.Pixel(14 + n * 18 + 8) if (self._live_strip_visible and n > 0) else ui.Pixel(28 if self._live_strip_visible else 0)

    async def _tick_loop(self):
        """Drive spinner glyph + elapsed time for all running rows."""
        i = 0
        while not self._destroyed:
            i += 1
            glyph = SPINNER_GLYPHS[i % len(SPINNER_GLYPHS)]
            now = time.monotonic()
            for r in list(self._live_rows.values()):
                if r["state"] != "running":
                    continue
                try:
                    r["spinner_lbl"].text = glyph
                    elapsed = now - r["started_at"]
                    if r.get("elapsed_lbl") is not r.get("verb_lbl"):
                        r["elapsed_lbl"].text = f"{elapsed:.1f}s"
                    # Color escalation on slow tools
                    if elapsed > VERY_SLOW_THRESHOLD_S:
                        r["spinner_lbl"].style = {"color": COL_AMBER, "font_size": 13}
                    elif elapsed > SLOW_THRESHOLD_S:
                        r["spinner_lbl"].style = {"color": COL_AMBER_DIM, "font_size": 13}
                except Exception:
                    pass  # widget destroyed mid-tick
            await asyncio.sleep(SPIN_INTERVAL_S)

    # ═══════════════════════════════════════════════════════════════════════
    # Bubble rendering
    # ═══════════════════════════════════════════════════════════════════════
    def _add_user_bubble(self, text: str):
        with self.chat_layout:
            with ui.HStack():
                ui.Spacer(width=24)  # right-bias so user msgs visually distinct
                with ui.ZStack():
                    ui.Rectangle(
                        style={"background_color": COL_BG_USER, "border_radius": 6}
                    )
                    with ui.VStack():
                        ui.Spacer(height=4)
                        with ui.HStack():
                            ui.Spacer(width=8)
                            ui.Label(
                                "You",
                                width=0,
                                style={"color": COL_TEXT_SUBTLE, "font_size": 10},
                            )
                            ui.Spacer()
                        with ui.HStack():
                            ui.Spacer(width=8)
                            ui.Label(
                                text,
                                word_wrap=True,
                                style={"color": COL_TEXT, "font_size": 12},
                            )
                            ui.Spacer(width=8)
                        ui.Spacer(height=4)

    def _add_assistant_bubble(self, text: str, error: bool = False) -> Optional[Dict]:
        bubble_refs: Dict = {}
        with self.chat_layout:
            with ui.ZStack():
                # Border rect for pulse animation (sits behind body).
                bubble_refs["border_rect"] = ui.Rectangle(
                    style={
                        "background_color": COL_BORDER_NEUTRAL,
                        "border_radius": 8,
                    }
                )
                with ui.VStack():
                    ui.Spacer(height=2)
                    with ui.HStack():
                        ui.Spacer(width=2)
                        with ui.ZStack():
                            ui.Rectangle(
                                style={
                                    "background_color": COL_BG_ASSIST,
                                    "border_radius": 6,
                                }
                            )
                            with ui.VStack():
                                ui.Spacer(height=4)
                                with ui.HStack():
                                    ui.Spacer(width=8)
                                    ui.Label(
                                        "Isaac Assist",
                                        width=0,
                                        style={
                                            "color": COL_TEXT_SUBTLE,
                                            "font_size": 10,
                                        },
                                    )
                                    ui.Spacer()
                                with ui.HStack():
                                    ui.Spacer(width=8)
                                    body_color = COL_RED if error else COL_TEXT
                                    ui.Label(
                                        text,
                                        word_wrap=True,
                                        style={"color": body_color, "font_size": 12},
                                    )
                                    ui.Spacer(width=8)
                                ui.Spacer(height=4)
                        ui.Spacer(width=24)
                    ui.Spacer(height=2)
        return bubble_refs

    # ═══════════════════════════════════════════════════════════════════════
    # New scene + LiveKit toggle (existing behavior preserved)
    # ═══════════════════════════════════════════════════════════════════════
    def _new_scene(self):
        asyncio.ensure_future(self._new_scene_async())

    async def _new_scene_async(self):
        try:
            import omni.usd
            omni.usd.get_context().new_stage()
        except Exception as e:
            logger.error(f"[IsaacAssist] new_stage failed: {e}")
        try:
            resp = await self.service.reset_session()
            if "error" in resp:
                logger.warning(f"[IsaacAssist] reset_session error: {resp['error']}")
        except Exception as e:
            logger.warning(f"[IsaacAssist] reset_session call failed: {e}")
        self.chat_layout.clear()
        self._collapse_live_strip()

    def _toggle_livekit(self):
        if self.webrtc and self.webrtc._streaming:
            self.btn_livekit.text = "Vision"
            asyncio.ensure_future(self.webrtc.disconnect())
        else:
            self.btn_livekit.text = "Stop"
            url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
            key = os.environ.get("LIVEKIT_API_KEY", "devkey")
            secret = os.environ.get("LIVEKIT_API_SECRET", "secret")
            if not self.webrtc:
                self.webrtc = ViewportWebRTCClient(url, key, secret)
            asyncio.ensure_future(self.webrtc.connect_and_publish())

    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════
    def destroy(self):
        self._destroyed = True
        if self._spin_task:
            self._spin_task.cancel()
        try:
            self.service.stop_stream()
        except Exception:
            pass
        if self.webrtc:
            asyncio.ensure_future(self.webrtc.disconnect())
        super().destroy()
