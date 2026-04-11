import omni.ext
import omni.ui as ui
import carb

# Module-level ref prevents garbage collection
_window = None


class IsaacAssistExtension(omni.ext.IExt):
    """
    Omniverse Extension entry point for Isaac Assist.
    Compatible with Isaac Sim 5.1 and 6.0.
    """

    def on_startup(self, ext_id):
        global _window
        carb.log_warn("[IsaacAssist] on_startup")

        # Lazy-import telemetry so any failure is isolated
        try:
            from .telemetry import init_telemetry
            init_telemetry()
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Telemetry skipped: {e}")

        # Build the window
        try:
            from .ui import ChatViewWindow
            _window = ChatViewWindow("Isaac Assist AI", width=440, height=660)
            _window.visible = True
            self._window = _window
            carb.log_warn("[IsaacAssist] ChatViewWindow ready")
        except Exception as e:
            carb.log_error(f"[IsaacAssist] Window creation failed: {e}")
            import traceback
            carb.log_error(traceback.format_exc())
            self._window = None

        # Register Window menu entry (optional — graceful fallback)
        self._menu = None
        try:
            import omni.kit.menu.utils as mu
            self._menu = mu.add_menu_items(
                [mu.MenuItemDescription(
                    name="Isaac Assist",
                    onclick_fn=self._show_window,
                    appear_after="Replicator",
                )],
                "Window",
            )
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Menu registration skipped: {e}")

    def _show_window(self):
        if self._window:
            self._window.visible = True

    def on_shutdown(self):
        global _window
        carb.log_warn("[IsaacAssist] on_shutdown")
        if self._menu is not None:
            try:
                import omni.kit.menu.utils as mu
                mu.remove_menu_items(self._menu, "Window")
            except Exception:
                pass
        if self._window is not None:
            self._window.destroy()
            self._window = None
        _window = None
