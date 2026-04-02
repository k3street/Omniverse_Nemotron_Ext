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

    def on_shutdown(self):
        logger.info("[omni.isaac.assist] Isaac Assist shutdown")
        if self._window:
            self._window.destroy()
            self._window = None
