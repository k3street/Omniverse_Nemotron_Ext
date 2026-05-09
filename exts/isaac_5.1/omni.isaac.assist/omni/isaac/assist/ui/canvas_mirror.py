"""
Canvas-mirror panel for the Kit Workspace — read-only 2D preview of the
current LayoutSpec, dockable next to the chat panel + 3D viewport.

Per docs/specs/2026-05-08-multimodal-foundation-spec.md §11.5: three-state
visibility model (Hidden / Proposed / Live). Panel auto-shows when the
backend emits SSE `canvas/preview_updated` and refreshes its `ui.Image`
widget bound to `~/.isaac_assist/canvas_preview.png` (or the
session-scoped variant under workspace/multimodal/previews/).

Discipline-required (§11.5.3): mirror is **strictly read-only forever**.
No pan, no zoom, no selection. Click on the mirror calls
`webbrowser.open(.../floorplan?session=...)` to focus the editing surface
in the browser tab. Editing happens only in the browser tab.
"""
from __future__ import annotations

import logging
import os
import webbrowser
from pathlib import Path
from typing import Optional

import omni.ui as ui

logger = logging.getLogger(__name__)

# Default preview path — matches the canvas/routes.py preview_path() helper.
# Resolves relative to the repo root so the panel finds the same file the
# backend writes.
def _default_preview_path(session_id: str) -> Path:
    # Walk up from this file to find the repo root (workspace/ sibling to exts/)
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        cand = ancestor / "workspace" / "multimodal" / "previews"
        if (ancestor / "workspace").exists():
            cand.mkdir(parents=True, exist_ok=True)
            return cand / f"{session_id}.png"
    # Fallback to user home if repo workspace not found
    home = Path.home() / ".isaac_assist" / "previews"
    home.mkdir(parents=True, exist_ok=True)
    return home / f"{session_id}.png"


# State machine per spec §11.5.1:
# Hidden  → no LayoutSpec produced this session
# Proposed → modality emitted spec; render uses ghost-styling overlay
# Live    → build completed; render is solid (no confirm bar)
STATE_HIDDEN = "hidden"
STATE_PROPOSED = "proposed"
STATE_LIVE = "live"


class CanvasMirrorWindow(ui.Window):
    """`omni.ui.Window` displaying the rendered LayoutSpec preview.

    Lifecycle:
        - Constructed once per Kit session; `visible=False` by default
          (Hidden state).
        - `show_with_preview(path, state)` reveals the panel and loads
          the PNG. Called by the chat extension's SSE handler when
          `canvas/preview_updated` arrives.
        - User can manually close via standard window controls. Auto-show
          fires next time a preview event arrives.
    """

    WINDOW_TITLE = "Canvas (read-only preview)"

    def __init__(self, session_id: str = "default_session", **kwargs):
        super().__init__(
            self.WINDOW_TITLE,
            width=480,
            height=360,
            visible=False,
            dockPreference=ui.DockPreference.RIGHT_BOTTOM,
            **kwargs,
        )
        self.session_id = session_id
        self._state = STATE_HIDDEN
        self._preview_path = _default_preview_path(session_id)
        self._image_widget: Optional[ui.Image] = None
        self._status_label: Optional[ui.Label] = None
        self._build_ui()

    def _build_ui(self) -> None:
        with self.frame:
            with ui.VStack(spacing=4):
                # Header chrome
                with ui.HStack(height=22, spacing=4):
                    ui.Spacer(width=8)
                    self._status_label = ui.Label(
                        "Hidden — no LayoutSpec produced",
                        style={"font_size": 11, "color": 0xFF8A8E92},
                    )
                    ui.Spacer()
                    ui.Button(
                        "Edit in browser",
                        width=120, height=20,
                        clicked_fn=self._open_in_browser,
                        style={"font_size": 10},
                        tooltip="Open the canvas editor in browser tab",
                    )
                    ui.Spacer(width=4)

                # Preview image
                self._image_widget = ui.Image(
                    "",
                    fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
                    style={"background_color": 0xFF111214},
                )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def show_with_preview(
        self,
        preview_path: Optional[Path] = None,
        state: str = STATE_PROPOSED,
        revision: int = 0,
    ) -> None:
        """Reveal the panel and load the given PNG preview.

        Args:
            preview_path: filesystem path to the PNG. Defaults to the
                session-scoped path resolved at construction time.
            state: STATE_PROPOSED | STATE_LIVE — affects status label
                styling. STATE_HIDDEN is set via `hide()`.
            revision: LayoutSpec revision number — surfaced in the status
                label for telemetry.
        """
        if preview_path is not None:
            self._preview_path = Path(preview_path)
        self._state = state

        if self._image_widget is not None and self._preview_path.exists():
            try:
                self._image_widget.source_url = str(self._preview_path)
            except Exception as e:
                logger.warning(
                    f"[CanvasMirror] failed to set image source: {e}"
                )

        if self._status_label is not None:
            label_text, color = self._status_label_for_state(state, revision)
            self._status_label.text = label_text
            self._status_label.style = {"font_size": 11, "color": color}

        self.visible = True

    def hide(self) -> None:
        self._state = STATE_HIDDEN
        self.visible = False

    @property
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _status_label_for_state(state: str, revision: int) -> tuple:
        """Return (label_text, color) for the panel header chrome."""
        if state == STATE_PROPOSED:
            # Amber per spec — user must accept/reject
            return f"Proposed — review and accept (rev={revision})", 0xFF00A5FF
        if state == STATE_LIVE:
            # NVIDIA green per spec — committed + built
            return f"Live — scene built (rev={revision})", 0xFF00B976
        return "Hidden — no LayoutSpec produced", 0xFF8A8E92

    def _open_in_browser(self) -> None:
        """Click handler — opens the editing surface in the browser tab.

        Per spec §11.5.3: editing happens only in the browser. The mirror
        is read-only forever; this button is the escape hatch to the
        editor when the user wants to change something.
        """
        url = (
            f"http://localhost:8000/floorplan"
            f"?session={self.session_id}"
        )
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f"[CanvasMirror] webbrowser.open failed: {e}")
