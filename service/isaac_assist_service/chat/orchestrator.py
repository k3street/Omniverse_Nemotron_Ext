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
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .provider_factory import get_llm_provider, get_distiller_provider
from .intent_router import classify_intent
from ..config import config
from .context_distiller import (
    ConversationKnowledge,
    DistilledContext,
    distill_context,
    update_knowledge_from_tool,
)
from .tools.kit_tools import (
    get_stage_context,
    format_stage_context_for_llm,
    is_kit_rpc_alive,
    get_viewport_image,
)
from .tools.tool_schemas import ISAAC_SIM_TOOLS
from .tools.tool_executor import execute_tool_call
from ..retrieval.context_retriever import (
    retrieve_context,
    format_retrieved_context,
    find_matching_patterns,
    format_code_patterns,
    detect_isaac_version,
)
from ..governance.audit_log import AuditLogger
from ..governance.models import AuditEntry
from ..knowledge.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

_audit = AuditLogger()
_kb = KnowledgeBase()

# Max chars per tool result (non-image) before truncation
_MAX_TOOL_RESULT_CHARS = 80_000
# Image keys that may appear in tool results
_IMAGE_KEYS = ("image_base64", "image_data", "png_data", "jpeg_data")


def _tool_result_content(result: Dict) -> Any:
    """
    Build the tool result `content` value for the messages list.

    - If the result contains a base64 image, return a list of content blocks
      (Anthropic multimodal format): one image block + one text block with
      the remaining metadata.  This is how the LLM actually *sees* the image.
    - Otherwise return a plain JSON string, capped at 80 K chars.
    """
    image_key = next((k for k in _IMAGE_KEYS if k in result), None)
    if image_key:
        b64 = result[image_key]
        meta = {k: v for k, v in result.items() if k not in _IMAGE_KEYS}
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            },
            {"type": "text", "text": json.dumps(meta, default=str)[:2000]},
        ]
    serialized = json.dumps(result, default=str)
    if len(serialized) > _MAX_TOOL_RESULT_CHARS:
        return serialized[:_MAX_TOOL_RESULT_CHARS] + '... [truncated]"}'
    return serialized


def _scrub_images_from_messages(messages: List[Dict]) -> None:
    """
    Replace image content blocks in tool-result messages with a short
    summary string.  Called after each LLM round so images don't
    accumulate across rounds and blow up the context window.
    """
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            has_image = any(
                isinstance(block, dict) and block.get("type") == "image"
                for block in content
            )
            if has_image:
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                msg["content"] = "[image captured — " + (text_parts[0][:200] if text_parts else "no metadata") + "]"

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

CRITICAL API RULES for Isaac Sim 5.1:
- NEVER call AddTranslateOp()/AddRotateXYZOp()/AddScaleOp() on prims that already have xformOps
  (e.g. referenced robots). Instead, reuse existing ops: xformable.GetOrderedXformOps() and call Set() on them.
  Use helper: op = next((o for o in xformable.GetOrderedXformOps() if o.GetOpType() == UsdGeom.XformOp.TypeTranslate), None)
- Always import modules at the top of generated code:
  import omni.usd; from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf
- Always get stage via: stage = omni.usd.get_context().get_stage()
- OmniGraph node paths must use tuples: ("graph_path", "node_name"), NOT "graph_path/node_name"
- isaacsim.core.cloner.GridCloner for batch cloning (≥4 copies), Sdf.CopySpec for small counts
- For transforms on referenced prims, check if xformOps exist first, reuse them, only add new ops on freshly-defined prims
- ROBOT IMPORT: To import a robot (Franka, Carter, Jetbot, etc.) ALWAYS use the `import_robot` tool.
  NEVER hardcode Nucleus paths like `omniverse://localhost/NVIDIA/Assets/Isaac/4.5/...` or any version.
  The tool resolves the correct local asset path automatically from ASSETS_ROOT_PATH config.
  Local asset root: /home/kimate/Desktop/assets/Collected_Robots/
  Example filenames: franka.usd, nova_carter.usd, jetbot.usd, go2.usd, g1.usd
- ROBOT ANCHORING: To anchor a stationary robot (e.g., Franka arm), use the `anchor_robot` tool with
  fixedBase=True. NEVER move ArticulationRootAPI off the root prim — it MUST stay on the robot root
  (e.g., /World/Franka) or the PhysX tensor API pattern matching will fail.
  For WHEELED/MOBILE robots (e.g., Nova Carter, Jetbot): do NOT set fixedBase=True — they need to move.
  Instead just delete the rootJoint and ensure rigid body + colliders are on the chassis and wheels.
  Nova Carter is a differential-drive robot: 2 powered front wheels + 2 free-spinning rear caster wheels.
  Use DifferentialController for wheeled robots, NOT fixedBase anchoring.
- OmniGraph node types in Isaac Sim 5.1 use the `isaacsim.*` namespace, NOT legacy `omni.isaac.*`:
  • isaacsim.ros2.bridge.ROS2PublishJointState (NOT omni.isaac.ros2_bridge.ROS2PublishJointState)
  • isaacsim.ros2.bridge.ROS2SubscribeJointState (NOT omni.isaac.ros2_bridge.ROS2SubscribeJointState)
  • isaacsim.core.nodes.IsaacArticulationController (NOT omni.isaac.ros2_bridge.ROS2ArticulationController)
  • isaacsim.ros2.bridge.ROS2Context for ROS2 clock/context setup
- OmniGraph ArticulationController: Set the robot path via SET_VALUES with "inputs:robotPath",
  NOT "inputs:usePath" (which does not exist as an attribute).
- OmniGraph TYPE COMPATIBILITY: ROS2SubscribeTwist outputs double3 vectors (linearVelocity, angularVelocity)
  but DifferentialController expects scalar doubles. You CANNOT wire them directly.
  Use an OmniGraph Break3Vector node to extract components: linearVelocity.x → linear speed,
  angularVelocity.z → angular speed. Or use a Python script node to extract the scalar values.
- COLLISION APPROXIMATION on robot wheels: Isaac Sim robot USD wheel meshes use triangle mesh collision
  by default, which PhysX rejects for dynamic bodies. Always set collision approximation to "convexHull"
  on wheel prims: `prim.GetAttribute("physics:approximation").Set("convexHull")`
  Affected parts on Nova Carter: wheel_left, wheel_right, caster_swivel_left, caster_swivel_right,
  caster_wheel_left, caster_wheel_right.
- Nova Carter joint names: front drive wheels are "joint_wheel_left" and "joint_wheel_right".
  Rear casters are "joint_caster_swivel_left/right" and "joint_caster_wheel_left/right" (passive).
  DifferentialController should target only the two front drive joints.

Selection awareness: When the user has selected a prim in the viewport or stage tree, its path and
properties are included in the context below. References like "this", "it", "the selected object",
"make this bigger", "change its color", or "delete it" all refer to the selected prim.
Use the selected prim's path directly when calling tools — do NOT ask the user to specify the path.
"""


class ChatOrchestrator:
    """
    Manages multi-turn chat sessions, injects stage context, and calls the
    configured LLM provider with tool-calling support.

    Uses a two-stage context distillation pipeline:
      Stage 1 — deterministic tool/rule selection + small-LLM history compression
      Stage 2 — main LLM call with compact prompt (~40-60% fewer tokens)
    """

    def __init__(self):
        self.llm_provider = get_llm_provider()
        try:
            self._distiller_provider = get_distiller_provider()
        except Exception:
            self._distiller_provider = None
        self._history: Dict[str, List[Dict]] = {}
        self._knowledge: Dict[str, ConversationKnowledge] = {}

    def refresh_provider(self):
        """Reinitialize the LLM provider from current config (after settings change)."""
        self.llm_provider = get_llm_provider()
        try:
            self._distiller_provider = get_distiller_provider()
        except Exception:
            self._distiller_provider = None
        logger.info("LLM provider reinitialized: %s", type(self.llm_provider).__name__)

    def reset_session(self, session_id: str):
        """Clear in-memory conversation history for a session."""
        self._history.pop(session_id, None)
        self._knowledge.pop(session_id, None)

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
        knowledge = self._knowledge.setdefault(
            session_id, ConversationKnowledge(session_id=session_id)
        )
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

        # ── 3. Collect RAG / KB data (fetched before distillation) ───────────
        isaac_version = detect_isaac_version()
        rag_text = ""
        patterns_text = ""
        error_learnings_text = ""
        success_learnings_text = ""
        negative_patterns_text = ""

        try:
            rag_results = retrieve_context(user_message, version=isaac_version, limit=3)
            rag_text = format_retrieved_context(rag_results)
        except Exception as e:
            logger.warning(f"[{session_id}] RAG retrieval failed: {e}")

        try:
            patterns = find_matching_patterns(user_message, version=isaac_version, limit=5)
            patterns_text = format_code_patterns(patterns)
        except Exception as e:
            logger.warning(f"[{session_id}] Pattern matching failed: {e}")

        try:
            error_learnings = _kb.get_error_learnings(isaac_version, user_message, limit=3)
            error_learnings_text = _kb.format_error_learnings(error_learnings)
        except Exception as e:
            logger.warning(f"[{session_id}] Error learning retrieval failed: {e}")

        try:
            success_learnings = _kb.get_success_learnings(isaac_version, user_message, limit=2)
            success_learnings_text = _kb.format_success_learnings(success_learnings)
        except Exception as e:
            logger.warning(f"[{session_id}] Success learning retrieval failed: {e}")

        try:
            neg_patterns = _kb.get_negative_patterns(isaac_version, user_message, limit=2)
            negative_patterns_text = _kb.format_negative_patterns(neg_patterns)
        except Exception as e:
            logger.warning(f"[{session_id}] Negative pattern retrieval failed: {e}")

        # ── 4. DISTILL: build compact context via the distillation pipeline ──
        selected_prim = context.get("selected_prim") if context else None
        selected_prim_path = context.get("selected_prim_path") if context else None

        distilled = await distill_context(
            intent=intent,
            user_message=user_message,
            history=history,
            knowledge=knowledge,
            scene_context=scene_context_text,
            selected_prim=selected_prim,
            selected_prim_path=selected_prim_path,
            isaac_version=isaac_version,
            rag_text=rag_text,
            patterns_text=patterns_text,
            error_learnings_text=error_learnings_text,
            success_learnings_text=success_learnings_text,
            negative_patterns_text=negative_patterns_text,
            small_provider=self._distiller_provider,
        )

        messages = distilled.messages
        selected_tools = distilled.tools

        # ── 4b. Auto-inject viewport (if enabled) ────────────────────────────
        if config.auto_inject_viewport and await is_kit_rpc_alive():
            try:
                vp_result = await get_viewport_image(max_dim=1280)
                vp_b64 = vp_result.get("image_b64") or vp_result.get("data", "")
                if vp_b64:
                    # Find the last user message and upgrade its content to multimodal
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "user":
                            original_text = messages[i].get("content", "")
                            if isinstance(original_text, str):
                                messages[i] = {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/png;base64,{vp_b64}",
                                                "detail": "low",
                                            },
                                        },
                                        {"type": "text", "text": original_text},
                                    ],
                                }
                            break
                    logger.info(f"[{session_id}] Viewport auto-injected into turn")
            except Exception as e:
                logger.warning(f"[{session_id}] Viewport auto-inject failed: {e}")

        logger.info(
            f"[{session_id}] Distilled: ~{distilled.token_estimate} tokens, "
            f"{len(selected_tools)} tools"
        )

        # ── 5. Tool-calling loop ─────────────────────────────────────────────
        executed_tools: List[Dict] = []
        code_patches: List[Dict] = []

        # Per-tool call limits to prevent the LLM from spamming the same tool
        _TOOL_CALL_LIMITS = {"lookup_knowledge": 2}
        tool_call_counts: Dict[str, int] = {}

        max_rounds = config.max_tool_rounds
        for round_idx in range(max_rounds):
            try:
                response = await self.llm_provider.complete(
                    messages, {"tools": selected_tools}
                )
            except Exception as e:
                logger.error(f"LLM provider error: {e}")
                raise

            # Check if the LLM wants to call tools
            tool_calls = getattr(response, "tool_calls", None) or response.actions
            if not tool_calls or not isinstance(tool_calls, list):
                break

            real_tool_calls = [
                tc for tc in tool_calls
                if isinstance(tc, dict) and tc.get("type") != "code_snippet"
            ]
            if not real_tool_calls:
                break

            # Build one assistant message with ALL tool calls from this round,
            # then append individual tool-result messages (OpenAI API format).
            assistant_tool_calls = []
            tool_results = []

            for tc in real_tool_calls:
                fn_name = tc.get("function", {}).get("name") or tc.get("name", "")
                fn_args_raw = tc.get("function", {}).get("arguments") or tc.get("arguments", "{}")
                fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                tc_id = tc.get("id", f"call_{round_idx}_{fn_name}")

                # Enforce per-tool call limits
                tool_call_counts[fn_name] = tool_call_counts.get(fn_name, 0) + 1
                limit = _TOOL_CALL_LIMITS.get(fn_name)
                if limit and tool_call_counts[fn_name] > limit:
                    logger.info(f"[{session_id}] Throttled {fn_name} (called {tool_call_counts[fn_name]}x, limit {limit})")
                    result = {
                        "type": "error",
                        "error": f"{fn_name} already called {limit} times this turn. "
                                 "Use the results you already have and proceed with your answer.",
                    }
                else:
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

                update_knowledge_from_tool(knowledge, fn_name, fn_args, result)

                try:
                    _audit.log_entry(AuditEntry(
                        entry_id=str(uuid.uuid4()),
                        timestamp=datetime.utcnow(),
                        event_type="tool_call",
                        action_id=fn_name,
                        target=json.dumps(fn_args, default=str)[:500],
                        metadata={
                            "session_id": session_id,
                            "result_type": result.get("type", "unknown"),
                            "user_message": user_message[:200],
                        },
                    ))
                except Exception:
                    pass  # audit must never block chat

                assistant_tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
                })
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": _tool_result_content(result),
                })

            # Single assistant message for all parallel tool calls in this round
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_tool_calls,
            })
            messages.extend(tool_results)

            # Scrub image data from previous rounds — images are single-use;
            # keeping base64 blobs in the context across rounds wastes ~200 K
            # tokens per image and causes "prompt is too long" failures.
            _scrub_images_from_messages(messages[:-len(tool_results)])
        else:
            logger.warning(f"[{session_id}] Hit max tool rounds ({max_rounds})")

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
