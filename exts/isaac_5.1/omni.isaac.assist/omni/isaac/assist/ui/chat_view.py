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
        self._auto_approve = os.environ.get("AUTO_APPROVE", "false").lower() == "true"
        self._busy = False
        self._cancel_event = asyncio.Event()
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

                    # New Scene — clears stage + chat history
                    ui.Button("New Scene", width=100, clicked_fn=self._new_scene)
                    
                    # Settings Toggle
                    ui.Button("Cfg", width=30, clicked_fn=self._spawn_settings_window)

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
                    self.send_btn = ui.Button("Send", width=60, clicked_fn=self._submit_message)
                    self.stop_btn = ui.Button("Stop", width=40,
                                             style={"background_color": 0xFF882222},
                                             clicked_fn=self._cancel_request)
                    self.stop_btn.enabled = False

    def _on_input_changed(self, model):
        # Optional placeholder logic
        pass

    def _cancel_request(self):
        """Signal the running async handler to stop after the current step."""
        if self._busy:
            self._cancel_event.set()
            self._add_chat_bubble("System", "[Stop] Request received — finishing current step...", is_user=False)

    def _set_busy(self, busy: bool):
        self._busy = busy
        if not busy:
            self._cancel_event.clear()
        if hasattr(self, "send_btn"):
            self.send_btn.enabled = not busy
        if hasattr(self, "stop_btn"):
            self.stop_btn.enabled = busy

    def _submit_message(self):
        text = self.input_field.model.get_value_as_string().strip()
        if not text:
            return

        # Track for audit/knowledge logging
        self._last_user_message = text

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

        # Dispatch async to service: route by prefix
        if text.lower().startswith("pipeline:") or text.lower().startswith("pipeline "):
            pipeline_prompt = text.split(":", 1)[1].strip() if ":" in text else text.split(" ", 1)[1].strip()
            asyncio.ensure_future(self._handle_pipeline_request(pipeline_prompt))
        elif text.lower().startswith("patch") or text.lower().startswith("fix"):
            asyncio.ensure_future(self._handle_swarm_request(text))
        else:
            asyncio.ensure_future(self._handle_service_request(text, selected_prim_info=selected_prim_info))
        self._set_busy(True)

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

    # LLM mode → (display label, env key used for API key, placeholder model name)
    _LLM_MODES = [
        ("local  — Ollama",     "local",      "LOCAL_MODEL_NAME",   "LOCAL_MODEL_NAME",    "qwen3.5:35b"),
        ("google — Gemini",     "google",     "GEMINI_API_KEY",     "GEMINI_MODEL_NAME",   "gemini-3.1-pro-preview"),
        ("anthropic — Claude",  "anthropic",  "ANTHROPIC_API_KEY",  "CLOUD_MODEL_NAME",    "claude-sonnet-4-6"),
        ("openai — GPT",        "openai",     "OPENAI_API_KEY",     "CLOUD_MODEL_NAME",    "gpt-5.4"),
        ("grok   — xAI",        "grok",       "GROK_API_KEY",       "CLOUD_MODEL_NAME",    "grok-3"),
    ]

    def _spawn_settings_window(self):
        current_mode = os.environ.get("LLM_MODE", "local").strip().lower()
        mode_labels = [m[0] for m in self._LLM_MODES]
        mode_keys   = [m[1] for m in self._LLM_MODES]
        current_idx = mode_keys.index(current_mode) if current_mode in mode_keys else 0

        self._settings_window = ui.Window("Isaac Assist Settings", width=440, height=380)
        with self._settings_window.frame:
            with ui.VStack(spacing=10, margin=15):
                ui.Label("Engine Configuration", style={"font_size": 16, "color": 0xFF00FF00, "font_weight": "bold"})
                ui.Spacer(height=5)

                # ── LLM Provider ─────────────────────────────────────
                with ui.HStack(height=22):
                    ui.Label("LLM Provider:", width=150)
                    self.llm_mode_combo = ui.ComboBox(current_idx, *mode_labels)

                # ── API Key (context-sensitive label) ─────────────────
                with ui.HStack(height=22):
                    self.api_key_label = ui.Label("API Key:", width=150)
                    self.api_key_field = ui.StringField(password_mode=True)
                    # Show the key for whichever mode is currently active
                    _, _, key_env, model_env, _ = self._LLM_MODES[current_idx]
                    self.api_key_field.model.set_value(os.environ.get(key_env, ""))

                def _on_mode_changed(model, _):
                    idx = model.get_item_value_model().as_int
                    _, mode, key_env, model_env, placeholder = self._LLM_MODES[idx]
                    self.api_key_label.text = f"{key_env}:"
                    self.api_key_field.model.set_value(os.environ.get(key_env, ""))
                    # Sync model name: use the per-mode env var, fall back to placeholder
                    if hasattr(self, "model_field"):
                        saved = os.environ.get(model_env, "")
                        self.model_field.model.set_value(saved if saved else placeholder)

                self.llm_mode_combo.model.add_item_changed_fn(_on_mode_changed)
                # Fire once to sync label
                _on_mode_changed(self.llm_mode_combo.model, None)

                # ── Model name ────────────────────────────────────────
                with ui.HStack(height=22):
                    ui.Label("Model Name:", width=150)
                    self.model_field = ui.StringField()
                    _, cur_mode, _, cur_model_env, cur_placeholder = self._LLM_MODES[current_idx]
                    saved_model = os.environ.get(cur_model_env, "")
                    self.model_field.model.set_value(saved_model if saved_model else cur_placeholder)
                # Fire again now that model_field exists to finish syncing
                _on_mode_changed(self.llm_mode_combo.model, None)

                # ── Ollama base (local mode only) ─────────────────────
                with ui.HStack(height=22):
                    ui.Label("Ollama Base URL:", width=150)
                    self.api_base_field = ui.StringField()
                    self.api_base_field.model.set_value(os.environ.get("OPENAI_API_BASE", "http://localhost:11434/v1"))

                ui.Separator()

                with ui.HStack(height=22):
                    self.contribute_cb = ui.CheckBox()
                    self.contribute_cb.model.set_value(os.environ.get("CONTRIBUTE_DATA", "false").lower() == "true")
                    ui.Label("Contribute Fine-Tuning Data (Opt-In)", width=0)

                with ui.HStack(height=22):
                    self.auto_approve_cb = ui.CheckBox()
                    self.auto_approve_cb.model.set_value(self._auto_approve)
                    ui.Label("Auto-Approve Code Patches (skip approval dialog)", width=0)

                with ui.HStack(height=22):
                    ui.Label("Max Tool Rounds:", width=150)
                    self.max_tool_rounds_field = ui.IntField()
                    self.max_tool_rounds_field.model.set_value(int(os.environ.get("MAX_TOOL_ROUNDS", "10")))

                ui.Spacer(height=10)

                with ui.HStack(height=30, spacing=5):
                    ui.Button("Save Settings", style={"background_color": 0xFF22AA22}, clicked_fn=self._save_settings)
                    ui.Button("Export Training Data", clicked_fn=self._export_data)

    def _save_settings(self):
        self._auto_approve = self.auto_approve_cb.model.get_value_as_bool()

        # Resolve selected mode
        idx = self.llm_mode_combo.model.get_item_value_model().as_int
        _, mode, key_env, model_env, _ = self._LLM_MODES[idx]
        api_key_value = self.api_key_field.model.get_value_as_string()

        payload = {
            "LLM_MODE":  mode,
            key_env:     api_key_value,          # write to the right env var
            model_env:   self.model_field.model.get_value_as_string(),  # per-mode model key
            "OPENAI_API_BASE": self.api_base_field.model.get_value_as_string(),
            "CONTRIBUTE_DATA": "true" if self.contribute_cb.model.get_value_as_bool() else "false",
            "AUTO_APPROVE":    "true" if self._auto_approve else "false",
            "MAX_TOOL_ROUNDS": str(self.max_tool_rounds_field.model.get_value_as_int()),
        }
        self._add_chat_bubble("System", f"Switching to {mode} provider…", is_user=False)
        asyncio.ensure_future(self._handle_save_settings(payload))

    async def _handle_save_settings(self, payload: dict):
        resp = await self.service.update_settings(payload)
        if "error" in resp:
            self._add_chat_bubble("System", f"Failed to save settings: {resp['error']}", is_user=False, error=True)
            return

        # Hot-switch the LLM provider — this reloads the orchestrator's provider
        mode = payload.get("LLM_MODE", "")
        if mode:
            switch_resp = await self.service.switch_llm_mode(mode)
            if "error" in switch_resp:
                self._add_chat_bubble("System", f"Settings saved but provider switch failed: {switch_resp['error']}", is_user=False, error=True)
            else:
                model = switch_resp.get("model", "")
                self._add_chat_bubble("System", f"Provider switched to {mode} ({model}). Ready.", is_user=False)
        else:
            self._add_chat_bubble("System", "Settings saved.", is_user=False)

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
        self._set_busy(True)
        try:
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
                    if self._cancel_event.is_set():
                        self._add_chat_bubble("System", "[Stop] Halted before executing swarm patch.", is_user=False)
                        break
                    if self._auto_approve:
                        script_code = action.get("new_value", "")
                        if script_code:
                            self._add_chat_bubble("System", f"Auto-approved swarm patch (confidence: {conf*100:.1f}%)", is_user=False)
                            self._execute_patch(script_code)
                    else:
                        self._render_patch_action(action, conf)
        finally:
            self._set_busy(False)

    # ── Pipeline Executor ────────────────────────────────────────────────────

    async def _handle_pipeline_request(self, prompt: str):
        """
        Autonomous multi-phase pipeline executor.
        1. Gets a structured plan from the service
        2. Executes each phase sequentially with verification
        3. On failure, asks the LLM to fix and retries once
        """
        self._set_busy(True)
        try:
            await self._run_pipeline(prompt)
        finally:
            self._set_busy(False)

    async def _run_pipeline(self, prompt: str):

        self._add_chat_bubble("Pipeline", f"Generating pipeline plan for: {prompt}", is_user=False)
        if "error" in plan:
            self._add_chat_bubble("Pipeline", f"Planning failed: {plan['error']}", is_user=False, error=True)
            return

        title = plan.get("title", "Unnamed Pipeline")
        phases = plan.get("phases", [])
        total = len(phases)
        source = plan.get("source", "unknown")

        self._add_chat_bubble("Pipeline",
            f"Plan: {title}  ({total} phases, source: {source})",
            is_user=False)

        # Step 2: Execute each phase
        results = []
        for phase in phases:
            phase_id = phase.get("id", "?")
            phase_name = phase.get("name", "Unnamed")
            phase_prompt = phase.get("prompt", "")
            verification = phase.get("verification")
            retry_hint = phase.get("retry_hint")
            is_data_only = phase.get("is_data_only", False)

            self._add_chat_bubble("Pipeline",
                f"Phase {phase_id}/{total}: {phase_name}",
                is_user=False)

            # Send phase prompt to the regular chat orchestrator
            response = await self.service.send_message(phase_prompt)

            if "error" in response:
                self._add_chat_bubble("Pipeline",
                    f"Phase {phase_id} service error: {response['error']}",
                    is_user=False, error=True)
                results.append({"phase": phase_id, "name": phase_name, "status": "error"})
                continue

            # Show the LLM's reply
            for msg in response.get("response_messages", []):
                content = msg.get("content", "")
                if content:
                    self._add_chat_bubble("Isaac Assist", content, is_user=False)

            # Execute code patches (if any)
            patches = response.get("actions_to_approve") or []
            phase_success = True
            phase_output = []

            for i, action in enumerate(patches):
                code = action.get("code", "")
                desc = action.get("description", "")
                if not code:
                    continue

                self._add_chat_bubble("Pipeline",
                    f"  Executing patch {i+1}/{len(patches)}: {desc[:80]}",
                    is_user=False)

                # Execute synchronously on main thread and capture output
                success, output = await self._execute_patch_sync(code)

                if output:
                    self._add_chat_bubble("Script Output",
                        output[:800] + ("..." if len(output) > 800 else ""),
                        is_user=False)

                # Log to knowledge base
                try:
                    await self.service.log_execution(
                        code=code, success=success,
                        output=output[:2000], user_message=phase_prompt,
                    )
                except Exception:
                    pass

                if not success:
                    phase_success = False
                    phase_output.append(f"FAILED: {desc} — {output[:200]}")

                    # Retry once with the error + hint
                    if retry_hint:
                        self._add_chat_bubble("Pipeline",
                            f"  Retrying with fix hint...",
                            is_user=False)
                        retry_prompt = (
                            f"The previous phase '{phase_name}' had an error:\n"
                            f"{output[:500]}\n\n"
                            f"Hint: {retry_hint}\n\n"
                            f"Please fix the issue and regenerate the code."
                        )
                        retry_resp = await self.service.send_message(retry_prompt)
                        retry_patches = retry_resp.get("actions_to_approve") or []
                        for rp in retry_patches:
                            rcode = rp.get("code", "")
                            if rcode:
                                rsuccess, routput = await self._execute_patch_sync(rcode)
                                if rsuccess:
                                    phase_success = True
                                    self._add_chat_bubble("Pipeline",
                                        f"  Retry successful!",
                                        is_user=False)
                                    break
                else:
                    phase_output.append(f"OK: {desc}")

            # Verification (optional)
            if verification and not is_data_only and phase_success:
                self._add_chat_bubble("Pipeline",
                    f"  Verifying: {verification[:100]}",
                    is_user=False)
                verify_resp = await self.service.send_message(
                    f"Verify: {verification} Check the scene summary and report any issues."
                )
                for msg in verify_resp.get("response_messages", []):
                    content = msg.get("content", "")
                    if content:
                        self._add_chat_bubble("Verification", content, is_user=False)

            status = "ok" if phase_success else "failed"
            status_icon = "[OK]" if phase_success else "[X]"
            self._add_chat_bubble("Pipeline",
                f"{status_icon} Phase {phase_id}: {phase_name} - {status}",
                is_user=False)
            results.append({"phase": phase_id, "name": phase_name, "status": status})

            # Allow Kit to process a frame between phases
            await asyncio.sleep(0.1)

            if self._cancel_event.is_set():
                self._add_chat_bubble("Pipeline", "[Stop] Pipeline cancelled by user.", is_user=False)
                break

        # Step 3: Summary
        ok_count = sum(1 for r in results if r["status"] == "ok")
        fail_count = total - ok_count
        summary = f"Pipeline complete: {ok_count}/{total} phases succeeded"
        if fail_count:
            summary += f", {fail_count} failed"
            failed_names = [r["name"] for r in results if r["status"] != "ok"]
            summary += f" ({', '.join(failed_names)})"

        self._add_chat_bubble("Pipeline", summary, is_user=False)

    async def _execute_patch_sync(self, code: str) -> tuple:
        """
        Execute code on the main thread and return (success: bool, output: str).
        Unlike _execute_patch_async, this does NOT trigger feedback messages —
        the pipeline executor manages the conversation flow itself.
        """
        await asyncio.sleep(0)  # yield to exit any draw callback

        output_buffer = io.StringIO()
        success = False
        try:
            with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(output_buffer):
                exec_globals = {"__builtins__": __builtins__}
                exec(code, exec_globals)
            success = True
        except Exception as e:
            output_buffer.write(f"\nRuntime Exception: {e}")

        return success, output_buffer.getvalue().strip()

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
                    swarm_btn = ui.Button("Review & Approve Execution", height=25, style={"background_color": 0xFF22AA22}, clicked_fn=lambda: self._prompt_approval(script_code))
                    def _on_swarm_approve(btn=swarm_btn):
                        btn.text = "Review opened..."
                        btn.enabled = False
                        btn.set_style({"background_color": 0xFF666666})
                        self._prompt_approval(script_code)
                    swarm_btn.set_clicked_fn(_on_swarm_approve)

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
                # Use an explicit globals dict so that functions defined in the
                # script can see top-level imports (e.g. UsdGeom, Gf).  A bare
                # exec(code) inside an async method puts imports into the
                # *function* locals — inner defs can't close over those.
                exec_globals = {"__builtins__": __builtins__}
                exec(script_code, exec_globals)
                
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

        # ── Log to audit trail + knowledge base ──────────────────────────
        try:
            user_msg = getattr(self, '_last_user_message', '')
            await self.service.log_execution(
                code=script_code,
                success=success,
                output=captured_text[:2000],
                user_message=user_msg,
            )
        except Exception as log_err:
            import carb
            carb.log_warn(f"[IsaacAssist] Failed to log execution: {log_err}")

    async def _handle_service_request(self, text: str, selected_prim_info: dict = None):
        self._set_busy(True)
        try:
            context = {}
            if selected_prim_info:
                context["selected_prim"] = selected_prim_info
                context["selected_prim_path"] = selected_prim_info.get("path")
            response = await self.service.send_message(text, context=context)

            if "error" in response:
                self._add_chat_bubble("System", response["error"], is_user=False, error=True)
            else:
                for msg in response.get("response_messages", []):
                    self._add_chat_bubble("Isaac Assist", msg.get("content", ""), is_user=False)

                actions = response.get("actions_to_approve") or []
                for action in actions:
                    if self._cancel_event.is_set():
                        self._add_chat_bubble("System", "[Stop] Halted before executing patch.", is_user=False)
                        break
                    code = action.get("code", "")
                    desc = action.get("description", "")
                    if self._auto_approve:
                        self._add_chat_bubble("System", f"Auto-approved: {desc}", is_user=False)
                        self._execute_patch(code)
                    else:
                        self._render_code_patch(code, desc)
        finally:
            self._set_busy(False)

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
                        approve_btn = ui.Button("Approve & Execute", style={"background_color": 0xFF22AA22},
                                  clicked_fn=lambda c=code: self._execute_patch(c))
                        reject_btn = ui.Button("Reject", style={"background_color": 0xFF666666},
                                  clicked_fn=lambda: asyncio.ensure_future(self._deferred_chat_bubble("System", "Patch rejected.", is_user=False)))
                        # Capture refs for post-click feedback
                        def _on_approve(btn=approve_btn, rej=reject_btn, c=code):
                            btn.text = "Executing..."
                            btn.enabled = False
                            btn.set_style({"background_color": 0xFF666666})
                            rej.visible = False
                            self._execute_patch(c)
                        approve_btn.set_clicked_fn(_on_approve)

    async def _deferred_chat_bubble(self, sender: str, text: str, is_user: bool, error: bool = False):
        """Defers _add_chat_bubble to next frame to avoid draw-callback restrictions."""
        await asyncio.sleep(0)
        self._add_chat_bubble(sender, text, is_user=is_user, error=error)

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
