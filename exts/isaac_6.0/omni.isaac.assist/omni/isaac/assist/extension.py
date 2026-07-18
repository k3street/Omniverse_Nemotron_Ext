import omni.ext
import carb

# Module-level refs prevent Kit from garbage-collecting UI/RPC objects.
_window = None
_rpc_server = None
_ros2_bridge = None
_physics_watchdog = None

class IsaacAssistExtension(omni.ext.IExt):
    """
    Omniverse Extension entry point for Isaac Assist on Isaac Sim 6.0.
    """
    def on_startup(self, ext_id):
        global _window, _rpc_server, _ros2_bridge, _physics_watchdog
        carb.log_warn("[IsaacAssist] on_startup")
        self._menu = None

        try:
            from .context.console_log import attach_log_listener
            attach_log_listener()
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Log listener skipped: {e}")

        try:
            from .context.kit_rpc import KitRPCServer, start_exec_tick
            _rpc_server = KitRPCServer()
            _rpc_server.start()
            start_exec_tick()
            self._rpc_server = _rpc_server
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Kit RPC skipped: {e}")
            self._rpc_server = None

        try:
            from .context.physics_watchdog import get_physics_watchdog
            _physics_watchdog = get_physics_watchdog()
            _physics_watchdog.start()
            self._physics_watchdog = _physics_watchdog
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Physics watchdog skipped: {e}")
            self._physics_watchdog = None

        try:
            from .telemetry import init_telemetry
            init_telemetry()
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] Telemetry skipped: {e}")

        try:
            from .ros2_control_bridge import get_bridge
            _ros2_bridge = get_bridge()
            bridge_state = _ros2_bridge.start()
            carb.log_warn(f"[IsaacAssist] ros2_control bridge: {bridge_state}")
            self._ros2_bridge = _ros2_bridge
        except Exception as e:
            carb.log_warn(f"[IsaacAssist] ros2_control bridge skipped: {e}")
            self._ros2_bridge = None

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
        global _window, _rpc_server, _ros2_bridge, _physics_watchdog
        carb.log_warn("[IsaacAssist] on_shutdown")

        if getattr(self, "_rpc_server", None) is not None:
            try:
                from .context.kit_rpc import stop_exec_tick
                stop_exec_tick()
                self._rpc_server.stop()
            except Exception:
                pass
            self._rpc_server = None
            _rpc_server = None

        if getattr(self, "_physics_watchdog", None) is not None:
            try:
                self._physics_watchdog.stop()
            except Exception:
                pass
            self._physics_watchdog = None
            _physics_watchdog = None

        try:
            from .context.console_log import detach_log_listener
            detach_log_listener()
        except Exception:
            pass

        if getattr(self, "_ros2_bridge", None) is not None:
            try:
                self._ros2_bridge.stop()
            except Exception:
                pass
            self._ros2_bridge = None
            _ros2_bridge = None

        if self._menu is not None:
            try:
                import omni.kit.menu.utils as mu
                mu.remove_menu_items(self._menu, "Window")
            except Exception:
                pass
            self._menu = None

        if self._window is not None:
            self._window.destroy()
            self._window = None
        _window = None
