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
from typing import Optional, Dict, List, Tuple

try:
    import carb.settings
    HAS_CARB_SETTINGS = True
except Exception:
    HAS_CARB_SETTINGS = False

from ..service_client import AssistServiceClient
from ..webrtc_client import ViewportWebRTCClient
from .verbs import verb_for
from . import animations as anim

logger = logging.getLogger(__name__)

# ── Text scaling (Phase 7) ───────────────────────────────────────────────
# Seven discrete steps. Default = 100%. v1 scales font sizes only —
# widget widths and heights stay fixed, which is acceptable at this
# range (80-175%) and avoids a full UI rebuild on scale change.
SCALE_STEPS = [0.80, 0.90, 1.00, 1.10, 1.25, 1.50, 1.75]
SCALE_LABELS = ["80%", "90%", "100%", "110%", "125%", "150%", "175%"]
DEFAULT_SCALE_INDEX = 2
SCALE_SETTING_KEY = "/persistent/exts/omni.isaac.assist/text_scale_index"

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

# ── Undo button styles (defined here so both _attach and state transitions
# read from one source of truth — easier to retune the visual). Solid
# backgrounds + bigger fonts so the button reads as an actual button,
# not as decorative text in the bubble.
UNDO_BTN_STYLE_IDLE = {
    "font_size": 12,
    "color": COL_TEXT,
    "background_color": 0xFF3A3E43,  # dark grey, lighter than bubble bg
    "border_radius": 4,
}
UNDO_BTN_STYLE_CONFIRM = {
    "font_size": 13,
    "color": 0xFF000000,             # near-black on amber for max contrast
    "background_color": COL_AMBER,   # solid amber — "click to commit destructive"
    "border_radius": 4,
}
UNDO_BTN_STYLE_PENDING = {
    "font_size": 12,
    "color": COL_TEXT_SUBTLE,
    "background_color": 0xFF2A2E33,
    "border_radius": 4,
}
UNDO_BTN_STYLE_FAILED = {
    "font_size": 12,
    "color": COL_TEXT,
    "background_color": COL_RED,     # solid red — failure state, click to retry
    "border_radius": 4,
}

# ── Spinner glyphs (ASCII; bundled font lacks Braille) ──────────────────
SPINNER_GLYPHS = "|/-\\"
SPIN_INTERVAL_S = 0.20
SLOW_THRESHOLD_S = 10.0
VERY_SLOW_THRESHOLD_S = 20.0


# ── Natural-language phrasing for live progress rows ────────────────────
# We compose ONE phrase per tool call (e.g. "Adding Franka Panda") instead
# of showing tool name + args separately. The technical detail (function
# name, full arg dict) lives in the tooltip. This matches the "Thinking..."
# style — a single readable status line.
def _path_leaf(p: str) -> str:
    if not p:
        return ""
    return p.rsplit("/", 1)[-1] if "/" in p else p


def humanize_tool_action(tool_name: str, args: dict) -> str:
    """Return one natural-language phrase describing what the tool is
    about to do, using args for context. Falls back to verb_for() +
    path leaf for tools without a special case."""
    args = args or {}

    if tool_name == "robot_wizard":
        product = (
            args.get("product_name") or args.get("robot") or "robot"
        ).replace("_", " ").title()
        return f"Adding {product}"

    if tool_name == "create_prim":
        ptype = args.get("type") or args.get("prim_type", "prim")
        leaf = _path_leaf(args.get("prim_path") or args.get("path", ""))
        return f"Creating {ptype} {leaf}".strip() if leaf else f"Creating {ptype}"

    if tool_name == "delete_prim":
        leaf = _path_leaf(args.get("prim_path") or args.get("path", ""))
        return f"Removing {leaf}" if leaf else "Removing prim"

    if tool_name == "duplicate_prims" or tool_name == "clone_prim":
        leaf = _path_leaf(args.get("prim_path") or args.get("path", ""))
        return f"Cloning {leaf}" if leaf else "Cloning prim"

    if tool_name == "apply_physics_material":
        mat = args.get("material") or args.get("preset", "physics")
        leaf = _path_leaf(args.get("prim_path", ""))
        return f"Adding {mat} physics to {leaf}" if leaf else f"Adding {mat} physics"

    if tool_name == "apply_dr_preset":
        preset = args.get("preset_name") or args.get("preset", "randomization")
        return f"Applying {preset} randomization"

    if tool_name == "anchor_robot":
        anchor_leaf = _path_leaf(
            args.get("anchor_to") or args.get("anchor_path") or args.get("target", "")
        )
        return f"Anchoring robot to {anchor_leaf}" if anchor_leaf else "Anchoring robot"

    if tool_name == "run_usd_script":
        desc = (args.get("description") or "").strip()
        if desc and len(desc) < 70:
            # First-letter-capitalize without breaking acronyms
            return desc[0].upper() + desc[1:] if desc[0].isalpha() else desc
        return "Running script"

    if tool_name == "queue_exec_patch":
        desc = (args.get("description") or "").strip()
        if desc and len(desc) < 70:
            return desc[0].upper() + desc[1:] if desc[0].isalpha() else desc
        return "Applying patch"

    if tool_name == "scatter_on_surface":
        n = args.get("count") or args.get("num_objects", "")
        return f"Scattering {n} objects".strip() if n else "Scattering objects"

    if tool_name == "set_camera_look_at":
        leaf = _path_leaf(args.get("target_path") or args.get("target", ""))
        return f"Aiming camera at {leaf}" if leaf else "Aiming camera"

    if tool_name == "set_attribute":
        attr = args.get("attribute") or args.get("attr_name", "attribute")
        leaf = _path_leaf(args.get("prim_path", ""))
        return f"Setting {attr} on {leaf}" if leaf else f"Setting {attr}"

    if tool_name == "get_attribute":
        attr = args.get("attribute") or args.get("attr_name", "attribute")
        leaf = _path_leaf(args.get("prim_path", ""))
        return f"Reading {attr} of {leaf}" if leaf else f"Reading {attr}"

    if tool_name in ("scene_summary", "scene_diff"):
        return "Reading scene"

    if tool_name in ("get_viewport_image", "capture_viewport"):
        return "Capturing viewport"

    if tool_name in ("lookup_knowledge", "lookup_product_spec", "lookup_material"):
        q = args.get("query") or args.get("name") or args.get("product_name", "")
        return f"Looking up {q}" if q else "Looking up reference"

    if tool_name == "import_robot":
        leaf = _path_leaf(args.get("urdf_path") or args.get("file_path", ""))
        return f"Importing {leaf}" if leaf else "Importing robot"

    if tool_name in ("save_delta_snapshot", "restore_delta_snapshot"):
        return "Saving snapshot" if "save" in tool_name else "Restoring snapshot"

    if tool_name == "preflight_check":
        return "Running pre-flight checks"

    if tool_name == "find_prims_by_name" or tool_name == "find_prims_by_schema":
        q = args.get("name") or args.get("schema") or args.get("pattern", "")
        return f"Finding {q}" if q else "Finding prims"

    if tool_name == "list_all_prims":
        return "Listing scene prims"

    if tool_name == "build_stage_index":
        return "Indexing stage"

    if tool_name == "console_error_autodetect":
        return "Checking console for errors"

    if tool_name.startswith("ros2_"):
        topic = args.get("topic") or args.get("service") or args.get("node", "")
        action = tool_name.replace("ros2_", "").replace("_", " ")
        return f"ROS2 {action}{(' ' + topic) if topic else ''}"

    # Fallback — verbs.py + path leaf if available
    verb = verb_for(tool_name)
    leaf = ""
    for k in ("prim_path", "path", "prim", "target", "target_path"):
        v = args.get(k)
        if v:
            leaf = _path_leaf(str(v))
            break
    return f"{verb} {leaf}".strip() if leaf else verb


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

        # Undo state (Phase 6). Bubbles eligible for undo, in
        # chronological order. Latest entry = the only one with a visible
        # ↶ button. SSE undo_applied pops + dims; the new latest gets
        # the button.
        self._undoable_bubbles: list = []
        self._undo_progress_row = None
        self._undo_handled_via_sse = False
        self._clear_chat_confirm = False

        # Text scaling (Phase 7). Loaded from carb settings if available,
        # else defaults to 100%. _scaled_labels is the registry: each
        # entry is (Label, base_font_size_int). _change_scale walks it
        # and mutates label.style in place — no UI rebuild required.
        self._settings = carb.settings.get_settings() if HAS_CARB_SETTINGS else None
        self._scale_index = self._load_scale_index()
        self._scale = SCALE_STEPS[self._scale_index]
        self._scaled_labels: List[Tuple[ui.Label, int]] = []
        self._scale_popup: Optional[ui.Window] = None
        self._scale_lbl: Optional[ui.Label] = None
        # Debounce: rapid A+/A- clicks coalesce into one apply task.
        self._scale_apply_task: Optional[asyncio.Task] = None

        self._build_ui()
        self.service.start_stream(self._on_sse_event)
        self._spin_task = asyncio.ensure_future(self._tick_loop())
        # Pre-warm the slow paths that hit on first scale change:
        # carb settings flush to disk + omni.ui style-mutation layout pass.
        # Boot already takes seconds; user won't notice another ~100ms here,
        # but they'll thank us when A+/A- responds instantly later.
        asyncio.ensure_future(self._prewarm_scale_paths())

    async def _prewarm_scale_paths(self):
        # Yield once so widget construction finishes before we mutate.
        await asyncio.sleep(0.0)
        try:
            self._save_scale_index(self._scale_index)
            self._apply_scale_to_all()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════════════
    # Text scaling (Phase 7)
    # ═══════════════════════════════════════════════════════════════════════
    def _sz(self, n: int) -> int:
        """Scale a font size by current factor; never below 1."""
        return max(1, int(round(n * self._scale)))

    def _track_label(self, label, base_font_size: int):
        """Register a label so subsequent scale changes will update its font."""
        self._scaled_labels.append((label, base_font_size))
        return label

    def _L(self, text: str, font_size: int = 12, **kw):
        """Construct a Label with scale-aware font size and register it.

        kw["style"] (if present) is merged with the scaled font size.
        Returns the constructed Label.
        """
        style = dict(kw.pop("style", {}))
        style["font_size"] = self._sz(font_size)
        lbl = ui.Label(text, style=style, **kw)
        self._track_label(lbl, font_size)
        return lbl

    def _load_scale_index(self) -> int:
        if not self._settings:
            return DEFAULT_SCALE_INDEX
        try:
            idx = self._settings.get(SCALE_SETTING_KEY)
        except Exception:
            return DEFAULT_SCALE_INDEX
        if not isinstance(idx, int) or not (0 <= idx < len(SCALE_STEPS)):
            return DEFAULT_SCALE_INDEX
        return idx

    def _save_scale_index(self, idx: int):
        if not self._settings:
            return
        try:
            self._settings.set(SCALE_SETTING_KEY, idx)
        except Exception:
            pass

    def _change_scale(self, delta: int):
        """delta = +1 / -1 / 0 (reset). No-op during a turn.

        Click handling is split into three concerns:
          1. State update + popup-label refresh — INSTANT, so rapid clicks
             feel responsive even if the actual mutation is heavier.
          2. Disk write (carb settings) — debounced behind a 100 ms timer
             so a burst of clicks results in one disk flush.
          3. Widget mutations — debounced + chunked + yields to the UI
             loop between chunks so the panel stays interactive.
        """
        if self._turn_active:
            return
        if delta == 0:
            new_idx = DEFAULT_SCALE_INDEX
        else:
            new_idx = max(0, min(len(SCALE_STEPS) - 1, self._scale_index + delta))
        if new_idx == self._scale_index:
            return
        self._scale_index = new_idx
        self._scale = SCALE_STEPS[new_idx]
        # Instant feedback: popup-label updates RIGHT NOW (no I/O, no
        # widget walk). Even if the actual font mutations take a beat,
        # the user sees their click took effect.
        if self._scale_lbl:
            try:
                self._scale_lbl.text = SCALE_LABELS[new_idx]
            except Exception:
                pass
        # Debounce: cancel any in-flight apply, schedule a new one. Last
        # click within the window wins; intermediate states are skipped.
        if self._scale_apply_task and not self._scale_apply_task.done():
            self._scale_apply_task.cancel()
        self._scale_apply_task = asyncio.ensure_future(self._scale_apply_debounced())

    async def _scale_apply_debounced(self):
        try:
            # Coalesce window — rapid clicks land here and the previous
            # task gets cancelled before it reaches the work below.
            await asyncio.sleep(0.10)
            self._save_scale_index(self._scale_index)
            await self._apply_scale_to_all_async()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"Scale apply failed: {e}")

    def _apply_scale_to_all(self):
        """Synchronous fallback (used by pre-warm and other call sites
        that aren't async). Walks the registry once with no yields."""
        new_size_for = self._sz
        survivors: List[Tuple] = []
        for lbl, base in self._scaled_labels:
            try:
                cur = lbl.style or {}
                lbl.style = {**cur, "font_size": new_size_for(base)}
                survivors.append((lbl, base))
            except Exception:
                pass
        self._scaled_labels = survivors

    async def _apply_scale_to_all_async(self):
        """Same work as _apply_scale_to_all but yields to the asyncio
        loop every CHUNK labels so omni.ui can process layout passes
        progressively. The text grows over ~100-200 ms instead of
        freezing the panel for the full duration."""
        CHUNK = 4
        new_size_for = self._sz
        survivors: List[Tuple] = []
        work = list(self._scaled_labels)
        for i in range(0, len(work), CHUNK):
            for lbl, base in work[i : i + CHUNK]:
                try:
                    cur = lbl.style or {}
                    lbl.style = {**cur, "font_size": new_size_for(base)}
                    survivors.append((lbl, base))
                except Exception:
                    pass
            # Yield once after each chunk — lets omni.ui run a layout
            # pass and lets other async tasks (spinner tick, SSE) breathe.
            await asyncio.sleep(0)
        self._scaled_labels = survivors

    def _open_scale_popup(self):
        """Small floating popup with [A-] [label] [A+] [Close]."""
        # Toggle: if already visible, close it (so the Aa header button
        # also acts as a dismiss).
        if self._scale_popup is not None:
            try:
                self._scale_popup.visible = not self._scale_popup.visible
                return
            except Exception:
                self._scale_popup = None
        # Keep title bar so the OS X-button is available; user-friendly
        # close via header button or by clicking Aa again.
        self._scale_popup = ui.Window(
            "Text size",
            width=180,
            height=110,
            flags=ui.WINDOW_FLAGS_NO_RESIZE | ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        with self._scale_popup.frame:
            with ui.VStack(spacing=4):
                ui.Spacer(height=4)
                with ui.HStack(spacing=6):
                    ui.Spacer(width=6)
                    ui.Label(
                        "Text size",
                        style={"color": COL_TEXT_DIM, "font_size": self._sz(10)},
                    )
                    ui.Spacer()
                with ui.HStack(spacing=6):
                    ui.Spacer(width=6)
                    ui.Button(
                        "A-",
                        width=28,
                        clicked_fn=lambda: self._change_scale(-1),
                        style={"font_size": self._sz(12)},
                    )
                    self._scale_lbl = ui.Label(
                        SCALE_LABELS[self._scale_index],
                        style={"color": COL_TEXT, "font_size": self._sz(12)},
                        alignment=ui.Alignment.CENTER,
                    )
                    ui.Button(
                        "A+",
                        width=28,
                        clicked_fn=lambda: self._change_scale(1),
                        style={"font_size": self._sz(12)},
                    )
                    ui.Spacer(width=6)
                with ui.HStack(spacing=6):
                    ui.Spacer(width=6)
                    ui.Button(
                        "Reset",
                        clicked_fn=lambda: self._change_scale(0),
                        style={"font_size": self._sz(11)},
                    )
                    ui.Button(
                        "Close",
                        clicked_fn=self._close_scale_popup,
                        style={"font_size": self._sz(11)},
                    )
                    ui.Spacer(width=6)
                ui.Spacer(height=4)

    def _close_scale_popup(self):
        if self._scale_popup is not None:
            try:
                self._scale_popup.visible = False
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=4):
                self._build_header()
                self._build_chat_area()
                self._build_live_strip()
                self._build_chips()
                self._build_input()

    def _build_header(self):
        with ui.HStack(height=26, spacing=6):
            # Panel title is CHROME — does NOT scale with the text-size
            # control (otherwise it dominates the panel at 175 %).
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
            self.btn_scale = ui.Button(
                "Aa",
                width=24,
                height=22,
                clicked_fn=self._open_scale_popup,
                style={"font_size": 11},
                tooltip="Text size",
            )
            self.btn_new = ui.Button(
                "New",
                width=40,
                height=22,
                clicked_fn=self._new_scene,
                style={"font_size": 11},
                tooltip="Wipe stage AND chat. Confirm required.",
            )
            self.btn_clear = ui.Button(
                "Clear",
                width=44,
                height=22,
                clicked_fn=self._on_clear_chat_clicked,
                style={"font_size": 11},
                tooltip="Clear chat history (keeps stage and undo). Confirm required.",
            )
            self.btn_livekit = ui.Button(
                "Vision",
                width=54,
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
                    # Strip header is CHROME — fixed size.
                    ui.Label(
                        "Live",
                        width=0,
                        style={"color": COL_TEXT_SUBTLE, "font_size": 10},
                    )
                    ui.Spacer()
                self.live_rows_layout = ui.VStack(spacing=2)
                ui.Spacer(height=4)

    def _build_chips(self):
        """Empty-state suggestion chips. Visible until first message ever
        sent; clicking a chip fills the input field. Disappears after
        first send and stays hidden for the window's lifetime."""
        self.chips_container = ui.HStack(height=22, spacing=4)
        self._chips_shown = True
        with self.chips_container:
            ui.Spacer(width=2)
            for label in (
                "Build a pick-and-place scene",
                "Add a Franka arm",
                "Inspect the stage",
            ):
                ui.Button(
                    label,
                    height=20,
                    clicked_fn=lambda t=label: self._on_chip(t),
                    style={
                        "font_size": 10,
                        "background_color": 0xFF2A2E33,
                        "color": COL_TEXT_DIM,
                    },
                )

    def _build_input(self):
        with ui.HStack(height=28, spacing=4):
            self.input_field = ui.StringField(multiline=False, style={"font_size": 12})
            # Enter (end-edit on a single-line StringField) submits the
            # message — same as clicking Send. Loses focus afterwards
            # which is the standard chat UX.
            self.input_field.model.add_end_edit_fn(self._on_input_enter)
            # Same button doubles as Send (idle) / Stop (turn active) /
            # "Stopping..." (cancel sent, waiting for orchestrator return).
            self.btn_send = ui.Button(
                "Send",
                width=64,
                height=24,
                clicked_fn=self._on_send_or_stop,
                style={"font_size": 12},
            )
            self._btn_state = "idle"

    def _on_input_enter(self, model):
        # Triggered by Enter key OR by focus loss. Only treat as a submit
        # when there's actual text and we're not mid-turn — otherwise
        # focus changes would silently submit empty / racing messages.
        text = model.get_value_as_string().strip()
        if not text or self._turn_active:
            return
        self._submit_message()

    # ═══════════════════════════════════════════════════════════════════════
    # Send / Stop button state machine
    # ═══════════════════════════════════════════════════════════════════════
    def _set_button_state(self, state: str):
        """state ∈ {idle, busy, stopping}."""
        self._btn_state = state
        if state == "idle":
            self.btn_send.text = "Send"
            self.btn_send.enabled = True
            self.btn_send.style = {"font_size": 12}
        elif state == "busy":
            self.btn_send.text = "Stop"
            self.btn_send.enabled = True
            # Amber to read as "interruption affordance" without screaming red
            self.btn_send.style = {"font_size": 12, "color": COL_AMBER}
        elif state == "stopping":
            self.btn_send.text = "Stopping..."
            self.btn_send.enabled = False
            self.btn_send.style = {"font_size": 12, "color": COL_TEXT_SUBTLE}

    def _on_send_or_stop(self):
        if self._btn_state == "idle":
            self._submit_message()
        elif self._btn_state == "busy":
            self._set_button_state("stopping")
            asyncio.ensure_future(self.service.cancel_turn())
        # "stopping" → button is disabled; clicks are no-ops.

    # ═══════════════════════════════════════════════════════════════════════
    # Submit / receive
    # ═══════════════════════════════════════════════════════════════════════
    def _submit_message(self):
        text = self.input_field.model.get_value_as_string().strip()
        if not text:
            return
        if self._turn_active:
            return
        self.input_field.model.set_value("")
        self._add_user_bubble(text)
        self._hide_chips()
        self._turn_active = True
        self._turn_rendered_via_sse = False
        self._set_button_state("busy")
        asyncio.ensure_future(self._handle_service_request(text))

    def _on_chip(self, text: str):
        self.input_field.model.set_value(text)

    def _hide_chips(self):
        if self._chips_shown:
            self.chips_container.visible = False
            self.chips_container.height = ui.Pixel(0)
            self._chips_shown = False

    async def _handle_service_request(self, text: str):
        try:
            response = await self.service.send_message(text)
            if not self._turn_rendered_via_sse:
                # SSE didn't deliver agent_reply (drop, slow); render from POST
                self._render_assistant_from_post(response)
                self._collapse_live_strip()
        finally:
            self._turn_active = False
            self._set_button_state("idle")

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
            elif evt_type == "cancel_acknowledged":
                self._on_cancel_ack(payload)
            elif evt_type == "turn_diff_computed":
                # Stash; attach to the assistant bubble when agent_reply
                # arrives (event order is not strictly guaranteed).
                self._pending_diff = payload
            elif evt_type == "agent_reply":
                self._on_agent_reply(payload)
            elif evt_type == "undo_started":
                self._on_undo_started(payload)
            elif evt_type == "undo_applied":
                self._on_undo_applied(payload)
            elif evt_type == "undo_failed":
                self._on_undo_failed(payload)
            elif evt_type == "chat_cleared":
                pass  # UI handled it locally — server confirmation only
        except Exception as e:
            logger.exception(f"SSE handler failed for {evt_type}: {e}")

    def _on_cancel_ack(self, payload):
        """Server confirmed it has stopped issuing tool calls. Show a
        dim "stopped" indicator in the live strip; the agent_reply event
        that follows shortly carries the canned 'Stopped' summary."""
        with self.live_rows_layout:
            with ui.HStack(height=14):
                ui.Spacer(width=10)
                ui.Label(
                    "[stopped by user]",
                    style={"color": COL_TEXT_DIM, "font_size": 11},
                )

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
            phrase = humanize_tool_action(tool, payload.get("args_full", {}))
            existing["verb_lbl"].text = f"{phrase} (attempt {existing['attempts']})"
            # Reset to default text colour (a previous failure may have
            # left the label red — we're retrying, not still failing).
            existing["verb_lbl"].style = {
                **(existing["verb_lbl"].style or {}),
                "color": COL_TEXT,
            }
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
            # ✓ U+2713 — confirmed renders OK in this font (used on Confirm Undo button)
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
            row["spinner_lbl"].text = "X"  # ✗ U+2717 — uncertain in font, ASCII safer
            row["spinner_lbl"].style = {"color": COL_RED, "font_size": 13}
            # Append a short failure tag to the existing phrase rather than
            # overwriting it (the phrase IS the user's mental anchor for
            # what just failed). Full error stays in the tooltip.
            err = payload.get("error", "")
            current = row["verb_lbl"].text
            if err:
                err_short = (str(err)[:40] + "...") if len(str(err)) > 40 else str(err)
                row["verb_lbl"].text = f"{current}  -  failed: {err_short}"
            else:
                row["verb_lbl"].text = f"{current}  -  failed"
            row["verb_lbl"].style = {
                **(row["verb_lbl"].style or {}),
                "color": COL_RED,
            }

    def _on_spam_halt(self, payload):
        with self.live_rows_layout:
            with ui.HStack(height=14):
                ui.Spacer(width=10)
                n = payload.get("consecutive_fails", 0)
                ui.Label(
                    f"[!] Stopped after {n} failed attempts",
                    style={"color": COL_AMBER, "font_size": 11},
                )

    def _on_agent_reply(self, payload):
        text = payload.get("text", "")
        has_snapshot = payload.get("has_snapshot", False)
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
        # Attach the diff chip if a turn_diff_computed event preceded us.
        diff = getattr(self, "_pending_diff", None)
        if diff and bubble:
            self._attach_diff_chip(bubble, diff)
            # Undo eligibility: turn must have mutated the stage AND a
            # snapshot must have been captured. The latest mutating
            # bubble is the only one with a visible ↶ button.
            if has_snapshot and diff.get("total_changes", 0) > 0:
                self._transfer_undo_button(bubble, diff)
            self._pending_diff = None
        self._collapse_live_strip()
        self._turn_rendered_via_sse = True

    def _attach_diff_chip(self, bubble: dict, diff: dict):
        """Render a small 'Changed: +N added −N removed' chip in the
        bubble's diff_slot. Hover shows the full path lists."""
        if not bubble or "diff_slot" not in bubble:
            return
        if diff.get("total_changes", 0) == 0:
            return
        slot = bubble["diff_slot"]
        try:
            slot.clear()
            slot.height = ui.Pixel(20)
        except Exception:
            return
        added = diff.get("added_paths", [])
        rem = diff.get("removed_paths", [])
        mod = diff.get("modified_paths", [])
        parts = []
        if added:
            parts.append(f"+{len(added)} added")
        if rem:
            parts.append(f"-{len(rem)} removed")
        if mod:
            parts.append(f"~{len(mod)} modified")
        full_paths = ""
        if added:
            full_paths += "Added:\n  " + "\n  ".join(added[:8])
        if rem:
            full_paths += ("\n\n" if full_paths else "") + "Removed:\n  " + "\n  ".join(rem[:8])
        if mod:
            full_paths += ("\n\n" if full_paths else "") + "Modified:\n  " + "\n  ".join(mod[:8])
        with slot:
            with ui.HStack(height=18):
                ui.Spacer(width=8)
                bubble["diff_chip_lbl"] = self._L(
                    "Changed: " + " ".join(parts),
                    font_size=10,
                    style={"color": COL_NV_GREEN},
                    tooltip=full_paths,
                )
                ui.Spacer()

    def _on_connection_state(self, state):
        # Cancel any in-flight pulse before switching state, so a previous
        # reconnecting-pulse doesn't keep mutating the dot after we go green.
        prev_task = getattr(self, "_dot_pulse_task", None)
        if prev_task and not prev_task.done():
            prev_task.cancel()
        if state == "connected":
            self.conn_dot.style = {
                "background_color": COL_DOT_GOOD,
                "border_radius": 3,
            }
        elif state == "reconnecting":
            # Steady amber baseline + slow pulse to amber-bright. Reads as
            # "trying" without screaming for attention.
            self.conn_dot.style = {
                "background_color": COL_DOT_WARN,
                "border_radius": 3,
            }
            self._dot_pulse_task = asyncio.ensure_future(
                self._pulse_conn_dot_until_destroyed()
            )
        elif state in ("stopped", "error"):
            self.conn_dot.style = {
                "background_color": COL_DOT_BAD,
                "border_radius": 3,
            }

    async def _pulse_conn_dot_until_destroyed(self):
        # ABGR amber-bright vs amber-dim
        bright = 0xFF00C8FF
        dim = 0xFF005A80
        while not self._destroyed:
            try:
                await anim.lerp_color(
                    lambda c: self.conn_dot.__setattr__(
                        "style", {"background_color": c, "border_radius": 3}
                    ),
                    dim,
                    bright,
                    ms=750,
                )
                await anim.lerp_color(
                    lambda c: self.conn_dot.__setattr__(
                        "style", {"background_color": c, "border_radius": 3}
                    ),
                    bright,
                    dim,
                    ms=750,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                return

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
                spin = self._L(
                    SPINNER_GLYPHS[0],
                    font_size=13,
                    width=14,
                    style={"color": COL_NV_GREEN},
                )
                lbl = self._L(
                    "Thinking...",
                    font_size=12,
                    width=0,
                    style={"color": COL_TEXT_DIM},
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
        # ONE natural-language phrase ("Adding Franka Panda") replaces the
        # old verb + args two-column layout. Tool-name + raw-args go to
        # the tooltip — engineers can still inspect, normal users see only
        # what they need. Same one-line shape as the "Thinking..." row.
        phrase = humanize_tool_action(tool, args_full)
        full_args_json = json.dumps(args_full, default=str)[:500]
        tooltip_parts = []
        if description:
            tooltip_parts.append(description)
        tooltip_parts.append(f"{tool}({full_args_json})")
        phrase_tooltip = "\n\n".join(tooltip_parts)

        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                spinner_lbl = self._L(
                    SPINNER_GLYPHS[0],
                    font_size=13,
                    width=14,
                    style={"color": COL_NV_GREEN},
                )
                verb_lbl = self._L(
                    phrase,
                    font_size=12,
                    width=0,
                    style={"color": COL_TEXT},
                    tooltip=phrase_tooltip,
                )
                ui.Spacer()
                elapsed_lbl = self._L(
                    "0.0s",
                    font_size=10,
                    width=40,
                    style={"color": COL_TEXT_SUBTLE},
                )
                ui.Spacer(width=4)

        self._live_rows[tc_id] = {
            "row": row,
            "spinner_lbl": spinner_lbl,
            "verb_lbl": verb_lbl,
            # args_lbl alias points at the same widget so the existing
            # failure / retry-compression code paths keep working without
            # a separate column. They mutate the visible phrase directly.
            "args_lbl": verb_lbl,
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
        # Bubble auto-fits content height. Pattern: ZStack(height=0) over
        # Rectangle + VStack(margin) → ZStack sizes to VStack's content,
        # Rectangle fills the ZStack as background. Without height=0 the
        # ZStack defaults to fractional fill and the bubble grows to
        # consume the entire scroll area when there are few messages.
        with self.chat_layout:
            with ui.HStack(height=0):
                ui.Spacer(width=24)  # right-bias so user msgs are visually distinct
                with ui.ZStack(height=0):
                    ui.Rectangle(
                        style={"background_color": COL_BG_USER, "border_radius": 6}
                    )
                    with ui.VStack(spacing=2):
                        ui.Spacer(height=4)
                        with ui.HStack(height=14):
                            ui.Spacer(width=8)
                            self._L(
                                "You",
                                font_size=10,
                                width=0,
                                style={"color": COL_TEXT_SUBTLE},
                            )
                            ui.Spacer()
                            ui.Button(
                                "R",
                                width=18,
                                height=14,
                                clicked_fn=lambda t=text: self._rerun(t),
                                style={
                                    "color": COL_TEXT_SUBTLE,
                                    "font_size": 10,
                                    "background_color": 0x00000000,
                                },
                                tooltip="Re-run this prompt",
                            )
                            ui.Spacer(width=4)
                        with ui.HStack(height=0):
                            ui.Spacer(width=8)
                            self._L(
                                text,
                                font_size=12,
                                word_wrap=True,
                                style={"color": COL_TEXT},
                            )
                            ui.Spacer(width=8)
                        ui.Spacer(height=4)

    def _rerun(self, text: str):
        self.input_field.model.set_value(text)

    def _add_assistant_bubble(self, text: str, error: bool = False) -> Optional[Dict]:
        # Same height=0 pattern as user bubble — fits content rather than
        # filling the available scroll area.
        bubble_refs: Dict = {}
        with self.chat_layout:
            with ui.ZStack(height=0):
                # Border rect for pulse animation (sits behind body).
                bubble_refs["border_rect"] = ui.Rectangle(
                    style={
                        "background_color": COL_BORDER_NEUTRAL,
                        "border_radius": 8,
                    }
                )
                with ui.HStack(height=0):
                    ui.Spacer(width=2)
                    with ui.ZStack(height=0):
                        # Inner bg — what dims when the bubble is undone (Phase 6).
                        bubble_refs["inner_bg_rect"] = ui.Rectangle(
                            style={
                                "background_color": COL_BG_ASSIST,
                                "border_radius": 6,
                            }
                        )
                        with ui.VStack(spacing=2):
                            ui.Spacer(height=4)
                            with ui.HStack(height=14):
                                ui.Spacer(width=8)
                                bubble_refs["header_lbl"] = self._L(
                                    "Isaac Assist",
                                    font_size=10,
                                    width=0,
                                    style={"color": COL_TEXT_SUBTLE},
                                )
                                ui.Spacer()
                            with ui.HStack(height=0):
                                ui.Spacer(width=8)
                                body_color = COL_RED if error else COL_TEXT
                                bubble_refs["body_lbl"] = self._L(
                                    text,
                                    font_size=12,
                                    word_wrap=True,
                                    style={"color": body_color},
                                )
                                ui.Spacer(width=8)
                            # Diff chip + undo button slot. Height grows
                            # when populated by _attach_diff_chip.
                            bubble_refs["diff_slot"] = ui.VStack(spacing=0, height=0)
                            ui.Spacer(height=4)
                    ui.Spacer(width=24)
        return bubble_refs

    # ═══════════════════════════════════════════════════════════════════════
    # Undo (per-bubble ↶, latest-only, soft-confirm)
    # ═══════════════════════════════════════════════════════════════════════
    # Snapshot stack lives on disk in turn_snapshot.py (auto-pruned by
    # restore). UI just tracks which bubbles correspond to undoable
    # turns. The latest entry has the ↶ button; on undo_applied we
    # pop + dim, then attach the button to the new latest.
    def _transfer_undo_button(self, new_bubble: dict, diff: dict):
        if self._undoable_bubbles:
            prev = self._undoable_bubbles[-1]
            self._remove_undo_button(prev)
        new_bubble["diff_summary"] = diff
        new_bubble["undo_state"] = "idle"
        self._attach_undo_button(new_bubble)
        self._undoable_bubbles.append(new_bubble)

    def _attach_undo_button(self, bubble: dict):
        slot = bubble.get("diff_slot")
        if not slot:
            return
        # Solid-background button (was transparent + dim text — read as
        # decoration, not as an interactive control). Same row as the
        # diff chip, right-aligned. Bigger font + padding so it actually
        # looks like a button.
        with slot:
            with ui.HStack(height=26) as undo_row:
                ui.Spacer()
                btn = ui.Button(
                    "Undo",
                    width=64,
                    height=22,
                    clicked_fn=lambda b=bubble: self._on_undo_clicked(b),
                    style=UNDO_BTN_STYLE_IDLE,
                    tooltip=self._undo_tooltip(bubble.get("diff_summary", {})),
                )
                ui.Spacer(width=8)
        bubble["undo_btn"] = btn
        bubble["undo_row"] = undo_row

    def _undo_tooltip(self, diff: dict) -> str:
        a = diff.get("added_paths", [])
        r = diff.get("removed_paths", [])
        m = diff.get("modified_paths", [])
        parts = []
        if a:
            parts.append(f"+{len(a)} added")
        if r:
            parts.append(f"-{len(r)} removed")
        if m:
            parts.append(f"~{len(m)} modified")
        head = (
            "Undo this turn - will revert: " + " ".join(parts)
            if parts
            else "Undo this turn"
        )
        if a:
            head += "\n\nAdded:\n  " + "\n  ".join(a[:8])
        if r:
            head += "\n\nRemoved:\n  " + "\n  ".join(r[:8])
        if m:
            head += "\n\nModified:\n  " + "\n  ".join(m[:8])
        return head

    def _remove_undo_button(self, bubble: dict):
        # omni.ui has no clean "remove single child" — hide the row.
        row = bubble.get("undo_row")
        if row:
            try:
                row.visible = False
                row.height = ui.Pixel(0)
            except Exception:
                pass

    def _on_undo_clicked(self, bubble: dict):
        if self._turn_active:
            return  # never undo during a live turn
        state = bubble.get("undo_state", "idle")
        btn = bubble.get("undo_btn")
        if state == "idle":
            # Confirm state: solid amber background, bigger text, checkmark.
            # Reads unmistakably as "click here to commit, this is a
            # destructive action." 3-second window then revert to idle.
            bubble["undo_state"] = "confirm"
            btn.text = "Confirm Undo  ✓"
            btn.style = UNDO_BTN_STYLE_CONFIRM
            btn.width = ui.Pixel(130)
            asyncio.ensure_future(self._reset_undo_after(bubble, 3.0))
        elif state == "confirm":
            bubble["undo_state"] = "pending"
            btn.text = "Reverting..."
            btn.style = UNDO_BTN_STYLE_PENDING
            btn.enabled = False
            asyncio.ensure_future(self._do_undo())

    async def _reset_undo_after(self, bubble: dict, sec: float):
        await asyncio.sleep(sec)
        if bubble.get("undo_state") == "confirm":
            bubble["undo_state"] = "idle"
            btn = bubble.get("undo_btn")
            if btn:
                btn.text = "Undo"
                btn.style = UNDO_BTN_STYLE_IDLE
                btn.width = ui.Pixel(64)

    async def _do_undo(self):
        # Show progress in the live strip — restore can take a few seconds
        # on large stages and the user needs to know it's working.
        self._show_live_strip()
        with self.live_rows_layout:
            with ui.HStack(height=18, spacing=6) as row:
                ui.Spacer(width=6)
                ui.Label("⠋", width=14, style={"color": COL_AMBER, "font_size": 13})
                ui.Label(
                    "Reverting...",
                    style={"color": COL_TEXT_DIM, "font_size": 12},
                )
        self._undo_progress_row = row
        result = await self.service.undo_turn(steps=1)
        # Server emits undo_applied/undo_failed via SSE; if POST returned
        # an error and SSE didn't deliver, surface it locally.
        if not result.get("ok") and not self._undo_handled_via_sse:
            self._on_undo_failed({"error": result.get("error", "unknown")})

    def _on_undo_started(self, payload):
        # If undo was triggered via /undo slash command (not via the
        # button), there's no "Reverting..." row yet. Render one.
        if not self._undo_progress_row:
            self._show_live_strip()
            with self.live_rows_layout:
                with ui.HStack(height=18, spacing=6) as row:
                    ui.Spacer(width=6)
                    ui.Label("⠋", width=14, style={"color": COL_AMBER, "font_size": 13})
                    ui.Label(
                        "Reverting...",
                        style={"color": COL_TEXT_DIM, "font_size": 12},
                    )
            self._undo_progress_row = row

    def _on_undo_applied(self, payload):
        self._undo_handled_via_sse = True
        steps = payload.get("steps", 1)
        for _ in range(min(steps, len(self._undoable_bubbles))):
            popped = self._undoable_bubbles.pop()
            self._dim_bubble_as_undone(popped)
        if self._undoable_bubbles:
            self._attach_undo_button(self._undoable_bubbles[-1])
        asyncio.ensure_future(self._collapse_undo_progress())

    def _on_undo_failed(self, payload):
        self._undo_handled_via_sse = True
        err = payload.get("error", "unknown error")
        if self._undoable_bubbles:
            b = self._undoable_bubbles[-1]
            b["undo_state"] = "idle"
            btn = b.get("undo_btn")
            if btn:
                btn.text = "Retry Undo"
                btn.width = ui.Pixel(96)
                btn.enabled = True
                btn.style = UNDO_BTN_STYLE_FAILED
                btn.tooltip = f"Undo failed: {err}\nClick to retry."
        if self._undo_progress_row:
            try:
                self._undo_progress_row.clear()
                with self._undo_progress_row:
                    ui.Spacer(width=6)
                    ui.Label("✗", width=14, style={"color": COL_RED, "font_size": 13})
                    ui.Label(
                        f"Undo failed: {str(err)[:50]}",
                        style={"color": COL_RED, "font_size": 12},
                    )
            except Exception:
                pass
        asyncio.ensure_future(self._collapse_undo_progress(delay=3.0))

    async def _collapse_undo_progress(self, delay: float = 0.5):
        await asyncio.sleep(delay)
        self._collapse_live_strip()
        self._undo_progress_row = None
        self._undo_handled_via_sse = False

    def _dim_bubble_as_undone(self, bubble: dict):
        """Visually mark this bubble as undone: ~50% bg alpha, dim text,
        '(undone)' header tag, recolor diff chip, remove ↶ button.
        Bubble stays in the chat scroll as a record of what was tried."""
        r = bubble.get("inner_bg_rect")
        if r:
            try:
                r.style = {"background_color": 0x801E2125, "border_radius": 6}
            except Exception:
                pass
        for key in ("body_lbl", "header_lbl"):
            lbl = bubble.get(key)
            if lbl:
                try:
                    cur = lbl.style or {}
                    lbl.style = {**cur, "color": COL_TEXT_DIM}
                except Exception:
                    pass
        hl = bubble.get("header_lbl")
        if hl:
            try:
                hl.text = "Isaac Assist (undone)"
            except Exception:
                pass
        chip = bubble.get("diff_chip_lbl")
        if chip:
            try:
                chip.style = {"color": COL_TEXT_DIM, "font_size": 10}
            except Exception:
                pass
        self._remove_undo_button(bubble)

    # ═══════════════════════════════════════════════════════════════════════
    # Clear chat (soft-confirm)
    # ═══════════════════════════════════════════════════════════════════════
    def _on_clear_chat_clicked(self):
        if self._turn_active:
            return  # never clear during a live turn
        if not self._clear_chat_confirm:
            self._clear_chat_confirm = True
            self.btn_clear.text = "Confirm?"
            self.btn_clear.style = {"font_size": 11, "color": COL_AMBER}
            asyncio.ensure_future(self._reset_clear_chat_after(3.0))
        else:
            self._clear_chat_confirm = False
            self.btn_clear.text = "Clear"
            self.btn_clear.style = {"font_size": 11}
            asyncio.ensure_future(self._do_clear_chat())

    async def _reset_clear_chat_after(self, sec: float):
        await asyncio.sleep(sec)
        if self._clear_chat_confirm:
            self._clear_chat_confirm = False
            self.btn_clear.text = "Clear"
            self.btn_clear.style = {"font_size": 11}

    async def _do_clear_chat(self):
        try:
            await self.service.clear_chat()
        except Exception as e:
            logger.warning(f"clear_chat failed: {e}")
        self.chat_layout.clear()
        self._undoable_bubbles = []
        self._chips_shown = True
        self.chips_container.visible = True
        self.chips_container.height = ui.Pixel(22)
        self._collapse_live_strip()

    # ═══════════════════════════════════════════════════════════════════════
    # New scene (soft-confirm) + LiveKit toggle (existing behavior preserved)
    # ═══════════════════════════════════════════════════════════════════════
    def _new_scene(self):
        # Two-click commit pattern: first click changes the button to
        # "Confirm?" for 3s; a second click within that window actually
        # wipes; otherwise it reverts to "New". Avoids destroying work
        # on accidental clicks without a heavy modal dialog.
        if not getattr(self, "_new_scene_confirm", False):
            self._new_scene_confirm = True
            self.btn_new.text = "Confirm?"
            self.btn_new.style = {"font_size": 11, "color": COL_AMBER}
            asyncio.ensure_future(self._reset_new_scene_after(3.0))
        else:
            self._new_scene_confirm = False
            self.btn_new.text = "New"
            self.btn_new.style = {"font_size": 11}
            asyncio.ensure_future(self._do_new_scene())

    async def _reset_new_scene_after(self, sec: float):
        await asyncio.sleep(sec)
        if self._new_scene_confirm:
            self._new_scene_confirm = False
            self.btn_new.text = "New"
            self.btn_new.style = {"font_size": 11}

    async def _do_new_scene(self):
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
        self._undoable_bubbles = []
        self._collapse_live_strip()
        # Re-show the empty-state chips after a wipe — feels like a fresh start.
        self._chips_shown = True
        self.chips_container.visible = True
        self.chips_container.height = ui.Pixel(22)

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
