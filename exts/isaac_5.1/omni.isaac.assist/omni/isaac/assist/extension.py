import omni.ext
import omni.ui as ui
import carb

# Module-level refs prevent garbage collection
_window = None
_rpc_server = None


class IsaacAssistExtension(omni.ext.IExt):
    """
    Omniverse Extension entry point for Isaac Assist.
    Compatible with Isaac Sim 5.1 and 6.0.
    """

    def on_startup(self, ext_id):
        global _window, _rpc_server
        carb.log_warn("[IsaacAssist] on_startup")

        # ── 1. Attach console log listener ───────────────────────────────────
        try:
            from .context.console_log import attach_log_listener
            attach_log_listener()
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Log listener skipped: {e}")

        # ── 2. Start Kit RPC server (port 8001) ───────────────────────────────
        try:
            from .context.kit_rpc import KitRPCServer
            _rpc_server = KitRPCServer()
            _rpc_server.start()
            self._rpc_server = _rpc_server
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Kit RPC skipped: {e}")
            self._rpc_server = None

        # ── 3. Lazy-init telemetry ────────────────────────────────────────────
        try:
            from .telemetry import init_telemetry
            init_telemetry()
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Telemetry skipped: {e}")

        # ── 4. Build the chat window ──────────────────────────────────────────
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

        # ── 5. Register Window menu entry ─────────────────────────────────────
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
        global _window, _rpc_server
        carb.log_warn("[IsaacAssist] on_shutdown")

        # Stop RPC server
        if self._rpc_server is not None:
            try:
                self._rpc_server.stop()
            except Exception:
                pass
            self._rpc_server = None
            _rpc_server = None

        # Detach log listener
        try:
            from .context.console_log import detach_log_listener
            detach_log_listener()
        except Exception:
            pass

        # Remove menu
        if self._menu is not None:
            try:
                import omni.kit.menu.utils as mu
                mu.remove_menu_items(self._menu, "Window")
            except Exception:
                pass

        # Destroy window
        if self._window is not None:
            self._window.destroy()
            self._window = None
        _window = None
