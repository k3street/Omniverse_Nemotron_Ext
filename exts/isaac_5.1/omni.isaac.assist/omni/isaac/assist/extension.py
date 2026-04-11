import omni.ext
import omni.ui as ui
import omni.kit.pipapi
import logging
from .ui import ChatViewWindow
from .telemetry import init_telemetry, trace_error

logger = logging.getLogger(__name__)

class IsaacAssistExtension(omni.ext.IExt):
    """
    Standard Omniverse Extension Entry Point (Compatible with 5.1 and 6.0 logic patterns)
    """
    @trace_error("Extension_Startup")
    def on_startup(self, ext_id):
        logger.info("[omni.isaac.assist] Isaac Assist startup")

        # Boot OpenTelemetry dependencies securely
        omni.kit.pipapi.install("opentelemetry-api")
        omni.kit.pipapi.install("opentelemetry-sdk")
        init_telemetry()

        # Spawn the Chat Window
        self._window = ChatViewWindow("Isaac Assist AI", width=400, height=600)
        
        # In 5.1, explicitly docking is an optional omni.ui layout command.
        # We will let it default to a floating window so the user can drag it
        # alongside the Property panel naturally.

    def on_shutdown(self):
        logger.info("[omni.isaac.assist] Isaac Assist shutdown")
        if self._window:
            self._window.destroy()
            self._window = None
