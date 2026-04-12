import omni.ui as ui
import asyncio
from typing import Callable
from ..service_client import AssistServiceClient
from ..webrtc_client import ViewportWebRTCClient
import logging
import os
import io
import sys
import contextlib
from ..telemetry import trace_error

logger = logging.getLogger(__name__)

class ChatViewWindow(ui.Window):
    def __init__(self, title: str, delegate=None, **kwargs):
        super().__init__(title, **kwargs)
        self.delegate = delegate
        self.service = AssistServiceClient()
        self.webrtc = None
        try:
            self._build_ui()
        except Exception as e:
            logger.error(f"[IsaacAssist] _build_ui() failed: {e}", exc_info=True)

    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=5):
                # Header tools
                with ui.HStack(height=30, spacing=4):
                    ui.Label("Isaac Assist (Local Mode)", width=0, style={"color": 0xFF888888})
                    ui.Spacer()
                    
                    # Settings Toggle
                    ui.Button("⚙", width=30, clicked_fn=self._spawn_settings_window)

                    # LiveKit Stream Toggle
                    self.btn_livekit = ui.Button("Start Vision / Voice", width=150, clicked_fn=self._toggle_livekit)
                    
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

        # Capture current selection on the Kit main thread (reliable)
        selected_prim_info = self._get_selected_prim_info()
        selected_prim_path = selected_prim_info.get("path") if selected_prim_info else None

        # Add to UI immediately with selection chip
        if selected_prim_path:
            self._add_chat_bubble("You", f"[{selected_prim_path}] {text}", is_user=True)
        else:
            self._add_chat_bubble("You", text, is_user=True)

        # Dispatch async to service: use generate_plan for patching!
        # If it looks like a patching command, we trigger Swarm. Otherwise just general chat.
        if text.lower().startswith("patch") or text.lower().startswith("fix"):
            asyncio.ensure_future(self._handle_swarm_request(text))
        else:
            asyncio.ensure_future(self._handle_service_request(text, selected_prim_info=selected_prim_info))

    def _get_selected_prim_path(self):
        """Get the currently selected prim path, or None."""
        try:
            import omni.usd
            ctx = omni.usd.get_context()
            selection = ctx.get_selection()
            paths = selection.get_selected_prim_paths()
            if paths:
                return paths[0]
        except Exception:
            pass
        return None

    def _get_selected_prim_info(self):
        """Get full properties of the selected prim (runs on Kit main thread)."""
        try:
            from ..context.prim_properties import get_selected_prim_properties
            info = get_selected_prim_properties()
            if "error" not in info:
                return info
        except Exception:
            pass
        return None

    def _spawn_settings_window(self):
        self._settings_window = ui.Window("Isaac Assist Settings", width=400, height=300)
        with self._settings_window.frame:
            with ui.VStack(spacing=10, margin=15):
                ui.Label("Engine Configuration", style={"font_size": 16, "color": 0xFF00FF00, "font_weight": "bold"})
                ui.Spacer(height=5)
                
                with ui.HStack(height=20):
                    ui.Label("OpenAI API Base:", width=150)
                    self.api_base_field = ui.StringField()
                    self.api_base_field.model.set_value(os.environ.get("OPENAI_API_BASE", "http://localhost:11434/v1"))
                    
                with ui.HStack(height=20):
                    ui.Label("API Key:", width=150)
                    self.api_key_field = ui.StringField(password_mode=True)
                    self.api_key_field.model.set_value(os.environ.get("OPENAI_API_KEY", ""))
                    
                with ui.HStack(height=20):
                    ui.Label("LLM Model:", width=150)
                    self.model_field = ui.StringField()
                    self.model_field.model.set_value(os.environ.get("CLOUD_MODEL_NAME", "deepseek-coder"))
                
                with ui.HStack(height=20):
                    self.contribute_cb = ui.CheckBox()
                    is_contrib = os.environ.get("CONTRIBUTE_DATA", "false").lower() == "true"
                    self.contribute_cb.model.set_value(is_contrib)
                    ui.Label("Contribute Fine-Tuning Data (Opt-In)", width=0)
                    
                ui.Spacer(height=10)
                
                with ui.HStack(height=30, spacing=5):
                    ui.Button("Save Settings", style={"background_color": 0xFF22AA22}, clicked_fn=self._save_settings)
                    ui.Button("Export Training Data", clicked_fn=self._export_data)

    def _save_settings(self):
        payload = {
            "OPENAI_API_BASE": self.api_base_field.model.get_value_as_string(),
            "OPENAI_API_KEY": self.api_key_field.model.get_value_as_string(),
            "CLOUD_MODEL_NAME": self.model_field.model.get_value_as_string(),
            "CONTRIBUTE_DATA": "true" if self.contribute_cb.model.get_value_as_bool() else "false"
        }
        self._add_chat_bubble("System", "Saving engine settings...", is_user=False)
        asyncio.ensure_future(self._handle_save_settings(payload))

    async def _handle_save_settings(self, payload: dict):
        resp = await self.service.update_settings(payload)
        if "error" in resp:
            self._add_chat_bubble("System", f"Failed to save settings: {resp['error']}", is_user=False, error=True)
        else:
            self._add_chat_bubble("System", "Settings successfully updated dynamically.", is_user=False)

    def _export_data(self):
        self._add_chat_bubble("System", "Triggering local Knowledge Base export for Fine-tuning...", is_user=False)
        asyncio.ensure_future(self._handle_export())

    async def _handle_export(self):
        resp = await self.service.export_knowledge()
        if "error" in resp:
            self._add_chat_bubble("System", f"Export failed: {resp['error']}", is_user=False, error=True)
        else:
            msg = resp.get("message", "Export complete.")
            self._add_chat_bubble("System", f"Export successful. {msg}", is_user=False)

    async def _handle_swarm_request(self, text: str):
        self._add_chat_bubble("System", "Submitting query to the Coder/QA/Critic multi-agent swarm. Please wait (this can take 1-3 minutes)...", is_user=False)
        response = await self.service.generate_plan(user_query=text)
        
        if "error" in response:
            self._add_chat_bubble("Agent Swarm", response["error"], is_user=False, error=True)
        else:
            actions = response.get("actions", [])
            conf = response.get("overall_confidence", 0.0)
            desc = response.get("description", "")
            
            if not actions:
                self._add_chat_bubble("Agent Swarm", f"Swarm analysis completed but no code patches generated:\n{desc}", is_user=False)
                return
                
            for action in actions:
                self._render_patch_action(action, conf)

    def _render_patch_action(self, action: dict, confidence: float):
        script_code = action.get("new_value", "No code provided.")
        reasoning = action.get("reasoning", "")
        
        with self.chat_layout:
            with ui.ZStack():
                ui.Rectangle(style={"background_color": 0xFF2B3A42, "border_radius": 5})
                with ui.VStack(margin=5, spacing=4):
                    ui.Label("SWARM EXECUTABLE PATCH", height=15, style={"color": 0xFF00FF00, "font_size": 12, "font_weight": "bold"})
                    ui.Label(f"Confidence: {confidence*100:.1f}%", height=15, style={"color": 0xFFAAAAAA, "font_size": 11})
                    ui.Label(f"Reasoning: {reasoning}", word_wrap=True, style={"color": 0xFFDDDDDD})
                    
                    ui.Spacer(height=5)
                    # Render code block
                    with ui.ZStack():
                        ui.Rectangle(style={"background_color": 0xFF111111, "border_radius": 3})
                        with ui.ScrollingFrame(height=150, horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                            ui.Label(script_code, style={"color": 0xFFDDDDFF, "font_family": "Courier"})
                            
                    ui.Spacer(height=5)
                    ui.Button("Review & Approve Execution", height=25, style={"background_color": 0xFF22AA22}, clicked_fn=lambda: self._prompt_approval(script_code))

    def _prompt_approval(self, script_code: str):
        # Implementation of step 4: Spawn approval window
        self._approval_window = ui.Window("Policy Approval - Execute Swarm Patch", width=600, height=400)
        with self._approval_window.frame:
            with ui.VStack(margin=10, spacing=10):
                ui.Label("WARNING: The AI swarm has generated the following Python patch. Please review to ensure it is safe before executing within the Omniverse Sandbox.", word_wrap=True, style={"color": 0xFF8888FF})
                with ui.ScrollingFrame():
                    ui.Label(script_code, style={"color": 0xFFDDDDFF, "font_family": "Courier"})
                with ui.HStack(height=30, spacing=10):
                    ui.Button("EXECUTE PATCH", style={"background_color": 0xFFAA2222}, clicked_fn=lambda: self._execute_patch(script_code))
                    ui.Button("REJECT", clicked_fn=lambda: self._close_approval_window())

    def _close_approval_window(self):
        if hasattr(self, '_approval_window') and self._approval_window:
            self._approval_window.destroy()
            self._approval_window = None

    def _execute_patch(self, script_code: str):
        """Defers execution to next frame to avoid omni.ui draw-callback restrictions."""
        asyncio.ensure_future(self._execute_patch_async(script_code))

    @trace_error("Swarm_Execution_Event")
    async def _execute_patch_async(self, script_code: str):
        self._close_approval_window()
        await asyncio.sleep(0)  # yield to exit draw callback
        self._add_chat_bubble("System", "Executing patch within Omniverse...", is_user=False)
        
        output_buffer = io.StringIO()
        success = False
        captured_text = ""
        
        try:
            with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(output_buffer):
                # Native executing inside Omniverse
                exec(script_code)
                
            success = True
            captured_text = output_buffer.getvalue().strip()
            self._add_chat_bubble("System", "Execution Successful.", is_user=False)
            
        except Exception as e:
            captured_text = output_buffer.getvalue().strip()
            captured_text += f"\nRuntime Exception: {str(e)}"
            self._add_chat_bubble("System", f"Execution Failed during runtime: {str(e)}", is_user=False, error=True)

        # Truncate and feed back the logs silently to the Swarm!
        if captured_text:
            self._add_chat_bubble("Script Output", captured_text[:1500] + ("...\n[TRUNCATED]" if len(captured_text)>1500 else ""), is_user=False)
            feedback_msg = f"System Report: The patch executed with the following output logs:\n```\n{captured_text}\n```"
            await self._handle_service_request(feedback_msg)

    async def _handle_service_request(self, text: str, selected_prim_info: dict = None):
        context = {}
        if selected_prim_info:
            context["selected_prim"] = selected_prim_info
            context["selected_prim_path"] = selected_prim_info.get("path")
        response = await self.service.send_message(text, context=context)

        if "error" in response:
            self._add_chat_bubble("System", response["error"], is_user=False, error=True)
        else:
            # Parse responses
            for msg in response.get("response_messages", []):
                self._add_chat_bubble("Isaac Assist", msg.get("content", ""), is_user=False)

            # Show approvable code patches
            actions = response.get("actions_to_approve") or []
            for action in actions:
                code = action.get("code", "")
                desc = action.get("description", "")
                self._render_code_patch(code, desc)

    def _add_chat_bubble(self, sender: str, text: str, is_user: bool, error: bool = False):
        with self.chat_layout:
            bg_color = 0xFF444444 if is_user else 0xFF222222
            text_color = 0xFF8888FF if error else 0xFFDDDDDD
            
            with ui.ZStack():
                ui.Rectangle(style={"background_color": bg_color, "border_radius": 5})
                with ui.VStack(margin=5):
                    ui.Label(sender, height=15, style={"color": 0xFFAAAAAA, "font_size": 12})
                    ui.Label(text, word_wrap=True, style={"color": text_color})

    def _render_code_patch(self, code: str, description: str):
        """Render a code patch card with approve/reject buttons."""
        with self.chat_layout:
            with ui.ZStack():
                ui.Rectangle(style={"background_color": 0xFF1A2A35, "border_radius": 5})
                with ui.VStack(margin=5, spacing=4):
                    ui.Label("TOOL-GENERATED PATCH", height=15,
                             style={"color": 0xFF00CCFF, "font_size": 12, "font_weight": "bold"})
                    if description:
                        ui.Label(description, word_wrap=True, style={"color": 0xFFBBBBBB, "font_size": 11})
                    with ui.ZStack():
                        ui.Rectangle(style={"background_color": 0xFF111111, "border_radius": 3})
                        with ui.ScrollingFrame(height=120,
                                               horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                            ui.Label(code, style={"color": 0xFFDDDDFF, "font_family": "Courier"})
                    with ui.HStack(height=25, spacing=8):
                        ui.Button("Approve & Execute", style={"background_color": 0xFF22AA22},
                                  clicked_fn=lambda c=code: self._execute_patch(c))
                        ui.Button("Reject", style={"background_color": 0xFF666666},
                                  clicked_fn=lambda: asyncio.ensure_future(self._deferred_chat_bubble("System", "Patch rejected.", is_user=False)))

    async def _deferred_chat_bubble(self, sender: str, text: str, is_user: bool, error: bool = False):
        """Defers _add_chat_bubble to next frame to avoid draw-callback restrictions."""
        await asyncio.sleep(0)
        self._add_chat_bubble(sender, text, is_user=is_user, error=error)

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
