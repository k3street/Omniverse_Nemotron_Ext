"""
orchestrator.py
---------------
Multi-turn chat session manager with tool-calling support.
Flow:
  1. Inject stage context + selection info into system prompt
  2. Send messages + tool schemas to the LLM
  3. If LLM returns tool_calls → execute via tool_executor → feed results back
  4. If LLM returns text → return to user (with any pending code patches)
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from .provider_factory import get_llm_provider
from .intent_router import classify_intent
from .tools.kit_tools import (
    get_stage_context,
    format_stage_context_for_llm,
    is_kit_rpc_alive,
)
from .tools.tool_schemas import ISAAC_SIM_TOOLS
from .tools.tool_executor import execute_tool_call

logger = logging.getLogger(__name__)

# Maximum tool-call rounds per user message to prevent infinite loops
MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = """\
You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim — authored by 10Things, Inc. (www.10things.tech).
You help robotics engineers build, diagnose, and control simulations using natural language.

Capabilities:
- Create, modify, and delete USD prims (meshes, lights, cameras, xforms)
- Apply physics (rigid body, deformable cloth/sponge/rubber/gel, colliders)
- Build OmniGraph action graphs and sensor pipelines
- Import robots from URDF/MJCF/USD or the asset library
- Configure materials (OmniPBR, OmniGlass)
- Control simulation (play/pause/stop/step/reset)
- Capture viewport screenshots and read console errors
- Look up real sensor product specifications (cameras, lidar, IMU, grippers)
- Generate synthetic data with Omniverse Replicator

When the user asks you to modify the scene, use the provided tools. For complex operations combine tools or use run_usd_script.
Always use proper USD paths starting with '/'. Be concise. When you generate code, use the Kit/pxr Python APIs.
"""


class ChatOrchestrator:
    """
    Manages multi-turn chat sessions, injects stage context, and calls the
    configured LLM provider with tool-calling support.
    """

    def __init__(self):
        self.llm_provider = get_llm_provider()
        self._history: Dict[str, List[Dict]] = {}

    async def handle_message(
        self,
        session_id: str,
        user_message: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Primary entry point called by the route handler.

        Returns a structured response dict:
        {
            "intent": str,
            "reply": str,           # final assistant text
            "tool_calls": [...],    # tools that were executed
            "code_patches": [...],  # code patches queued for approval
        }
        """
        history = self._history.setdefault(session_id, [])
        logger.info(f"[{session_id}] USER: {user_message}")

        # ── 1. Classify intent ───────────────────────────────────────────────
        intent = await classify_intent(user_message, self.llm_provider)

        # ── 2. Gather live scene context if Kit is reachable ─────────────────
        scene_context_text = ""
        if await is_kit_rpc_alive():
            try:
                ctx = await get_stage_context(full=(intent in ("scene_diagnose", "prim_inspect")))
                scene_context_text = format_stage_context_for_llm(ctx)
            except Exception as e:
                logger.warning(f"[{session_id}] Context fetch failed: {e}")

        # ── 3. Build system prompt with live context ─────────────────────────
        system_content = SYSTEM_PROMPT
        if scene_context_text:
            system_content += f"\n\n--- LIVE SCENE CONTEXT ---\n{scene_context_text}"
        if context and context.get("selected_prim_path"):
            system_content += f"\n\nUser's current selection: {context['selected_prim_path']}"

        # ── 4. Build message list ────────────────────────────────────────────
        messages = [{"role": "system", "content": system_content}]
        messages.extend(history[-10:])  # rolling context window
        messages.append({"role": "user", "content": user_message})

        # ── 5. Tool-calling loop ─────────────────────────────────────────────
        executed_tools: List[Dict] = []
        code_patches: List[Dict] = []

        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.llm_provider.complete(
                    messages, {"tools": ISAAC_SIM_TOOLS}
                )
            except Exception as e:
                logger.error(f"LLM provider error: {e}")
                raise

            # Check if the LLM wants to call tools
            tool_calls = getattr(response, "tool_calls", None) or response.actions
            if not tool_calls or not isinstance(tool_calls, list):
                # No tool calls — we have the final text response
                break

            # Only process actual tool-call dicts (not code_snippet actions)
            real_tool_calls = [
                tc for tc in tool_calls
                if isinstance(tc, dict) and tc.get("type") != "code_snippet"
            ]
            if not real_tool_calls:
                break

            # Execute each tool call
            for tc in real_tool_calls:
                fn_name = tc.get("function", {}).get("name") or tc.get("name", "")
                fn_args_raw = tc.get("function", {}).get("arguments") or tc.get("arguments", "{}")
                fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                tc_id = tc.get("id", f"call_{round_idx}_{fn_name}")

                logger.info(f"[{session_id}] TOOL CALL: {fn_name}({json.dumps(fn_args)[:150]})")
                result = await execute_tool_call(fn_name, fn_args)

                executed_tools.append({
                    "tool": fn_name,
                    "arguments": fn_args,
                    "result": result,
                })

                if result.get("type") == "code_patch":
                    code_patches.append({
                        "code": result.get("code", ""),
                        "description": result.get("description", ""),
                    })

                # Append tool call + result to message history for next LLM round
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
                    }],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, default=str),
                })
        else:
            logger.warning(f"[{session_id}] Hit max tool rounds ({MAX_TOOL_ROUNDS})")

        reply = response.text or ""
        logger.info(f"[{session_id}] ASSISTANT: {reply[:200]}")

        # ── 6. Persist to session history ────────────────────────────────────
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        return {
            "intent": intent,
            "reply": reply,
            "tool_calls": executed_tools,
            "code_patches": code_patches,
        }
