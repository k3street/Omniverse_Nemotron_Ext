import omni.ui as ui
import asyncio
from typing import Callable
from ..service_client import AssistServiceClient
from ..webrtc_client import ViewportWebRTCClient
import logging
import os

logger = logging.getLogger(__name__)

class ChatViewWindow(ui.Window):
    def __init__(self, title: str, delegate=None, **kwargs):
        super().__init__(title, **kwargs)
        self.delegate = delegate
        self.service = AssistServiceClient()
        self.webrtc = None
        self._build_ui()

    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=5):
                # Header tools
                with ui.HStack(height=30, spacing=4):
                    ui.Label("Isaac Assist (Local Mode)", width=0, style={"color": 0xFF888888})
                    ui.Spacer()

                    # New Scene — clears stage + chat history
                    ui.Button("New Scene", width=100, clicked_fn=self._new_scene)
                    
                    # LiveKit Stream Toggle (disabled — untested)
                    # self.btn_livekit = ui.Button("Start Vision / Voice", width=150, clicked_fn=self._toggle_livekit)
                    
                ui.Spacer(height=5)
                
                # Chat History Area
                self.scroll = ui.ScrollingFrame(
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                )
                with self.scroll:
                    self.chat_layout = ui.VStack(spacing=10)
                        
                ui.Spacer(height=5)
                
                # Input Area
                with ui.HStack(height=30, spacing=5):
                    self.input_field = ui.StringField(multiline=False)
                    self.input_field.model.add_value_changed_fn(self._on_input_changed)
                    ui.Button("Send", width=60, clicked_fn=self._submit_message)

    def _on_input_changed(self, model):
        # Optional placeholder logic
        pass

    def _submit_message(self):
        text = self.input_field.model.get_value_as_string().strip()
        if not text:
            return
            
        # Clear input
        self.input_field.model.set_value("")
        
        # Add to UI immediately
        self._add_chat_bubble("You", text, is_user=True)
        
        # Dispatch async to service
        asyncio.ensure_future(self._handle_service_request(text))

    async def _handle_service_request(self, text: str):
        response = await self.service.send_message(text)
        
        if "error" in response:
            self._add_chat_bubble("System", response["error"], is_user=False, error=True)
        else:
            # Parse responses
            for msg in response.get("response_messages", []):
                self._add_chat_bubble("Isaac Assist", msg.get("content", ""), is_user=False)

    def _add_chat_bubble(self, sender: str, text: str, is_user: bool, error: bool = False):
        with self.chat_layout:
            bg_color = 0xFF444444 if is_user else 0xFF222222
            text_color = 0xFF8888FF if error else 0xFFDDDDDD
            
            with ui.ZStack():
                ui.Rectangle(style={"background_color": bg_color, "border_radius": 5})
                with ui.VStack(margin=5):
                    ui.Label(sender, height=15, style={"color": 0xFFAAAAAA, "font_size": 12})
                    ui.Label(text, word_wrap=True, style={"color": text_color})

    def _new_scene(self):
        """Clear the stage and reset conversation history."""
        asyncio.ensure_future(self._new_scene_async())

    async def _new_scene_async(self):
        # 1. Open a fresh USD stage
        try:
            import omni.usd
            omni.usd.get_context().new_stage()
        except Exception as e:
            logger.error(f"[IsaacAssist] new_stage failed: {e}")

        # 2. Clear server-side conversation history
        try:
            resp = await self.service.reset_session()
            if "error" in resp:
                logger.warning(f"[IsaacAssist] reset_session error: {resp['error']}")
        except Exception as e:
            logger.warning(f"[IsaacAssist] reset_session call failed: {e}")

        # 3. Clear the chat UI
        self.chat_layout.clear()
        await asyncio.sleep(0)
        self._add_chat_bubble("System", "New scene created. Chat history cleared.", is_user=False)

    def _toggle_livekit(self):
        if self.webrtc and self.webrtc._streaming:
            # Stop streaming
            self.btn_livekit.text = "Start Vision / Voice"
            asyncio.ensure_future(self.webrtc.disconnect())
            self._add_chat_bubble("System", "Vision streaming disconnected.", is_user=False)
        else:
            # Start streaming
            self.btn_livekit.text = "Stop Vision"
            
            # Use fallback values if environment is missing
            url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
            key = os.environ.get("LIVEKIT_API_KEY", "devkey")
            secret = os.environ.get("LIVEKIT_API_SECRET", "secret")
            
            if not self.webrtc:
                self.webrtc = ViewportWebRTCClient(url, key, secret)
                
            asyncio.ensure_future(self.webrtc.connect_and_publish())
            self._add_chat_bubble("System", "Connected to LiveKit. The AI can now see your screen and talk to you.", is_user=False)

    def destroy(self):
        if self.webrtc:
            asyncio.ensure_future(self.webrtc.disconnect())
        super().destroy()
