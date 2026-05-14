import omni.ext
import omni.ui as ui
import logging
from .ui import ChatViewWindow

logger = logging.getLogger(__name__)

class IsaacAssistExtension(omni.ext.IExt):
    """
    Standard Omniverse Extension Entry Point (Compatible with 5.1 and 6.0 logic patterns)
    """
    def on_startup(self, ext_id):
        logger.info("[omni.isaac.assist] Isaac Assist startup")

        # Spawn the Chat Window
        self._window = ChatViewWindow("Isaac Assist AI", width=400, height=600)

        # In 5.1, explicitly docking is an optional omni.ui layout command.
        # We will let it default to a floating window so the user can drag it
        # alongside the Property panel naturally.

        # CRM-A1 — ros2_control bridge (Option A external graph hop).
        # Loaded but not started here: the compliance tool handlers call
        # get_bridge().start() lazily when they install the first
        # admittance / impedance controller, so the extension can load
        # cleanly without rclpy on stripped Kit builds.
        self._ros2_control_bridge = None
        try:
            from .ros2_control_bridge import get_bridge
            self._ros2_control_bridge = get_bridge()
            health = self._ros2_control_bridge.health_check()
            logger.info(
                "[omni.isaac.assist] ros2_control bridge loaded: available=%s",
                health.available,
            )
        except Exception:
            logger.exception(
                "[omni.isaac.assist] ros2_control bridge import failed; "
                "compliance tools will surface a clear error if invoked"
            )

    def on_shutdown(self):
        logger.info("[omni.isaac.assist] Isaac Assist shutdown")
        if self._window:
            self._window.destroy()
            self._window = None
        if self._ros2_control_bridge is not None:
            try:
                self._ros2_control_bridge.stop()
            except Exception:
                logger.exception(
                    "[omni.isaac.assist] ros2_control bridge stop failed"
                )
            self._ros2_control_bridge = None
