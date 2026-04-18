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

Response discipline:
- Match the user's intent. Action phrasing ("do X", "make X", "drop it in", "run the import",
  "just give me the script") means CALL TOOLS or RETURN CODE — keep prose to one short line or
  skip it entirely.
- Do not pad action requests with background explanation the user did not ask for. Do not ask for
  confirmation when the user has already specified what they want — just do it.
- Only explain at length when the user asked a question ("what is…", "why…", "how does…") or
  when there is a genuine ambiguity that would change the action. Ambiguous? Ask ONE concise
  clarifying question, do not write a tutorial.
- If the user says "less prose" or similar, drop all prose for the rest of the session and emit
  only code / tool calls / one-line confirmations.
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

        # Retrieve workflow templates (pre-generated offline from task specs).
        # Injected as few-shot patterns to stop the LLM from inventing a fresh
        # tool chain each turn. Adds to patterns_text (part of system prompt).
        try:
            from .tools.template_retriever import retrieve_templates, format_for_prompt
            templates = retrieve_templates(user_message, top_k=3)
            if templates:
                tpl_text = format_for_prompt(templates)
                if patterns_text:
                    patterns_text = patterns_text + "\n\n" + tpl_text
                else:
                    patterns_text = tpl_text
        except Exception as e:
            logger.warning(f"[{session_id}] Template retrieval failed: {e}")

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

                entry = {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
                }
                # Preserve thought_signature (Gemini 3.x requires it on continuation)
                ts = tc.get("thought_signature")
                if ts:
                    entry["thought_signature"] = ts
                assistant_tool_calls.append(entry)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, default=str),
                })

            # Single assistant message for all parallel tool calls in this round
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_tool_calls,
            })
            messages.extend(tool_results)
        else:
            logger.warning(f"[{session_id}] Hit max tool rounds ({max_rounds})")

        reply = response.text or ""

        # Anti-fabrication: if the last round of tool calls contained failures
        # (success=false or executed=false) and the reply doesn't acknowledge
        # them — i.e. it sounds like the effect succeeded — force a rewrite.
        # This is the motion-fabrication pattern seen in R-02 / AM-01: Assist
        # says "callback registered, hit Play" after run_usd_script failed.
        if reply.strip() and executed_tools:
            last_round_fails = [
                t for t in executed_tools[-6:]  # last ~round of calls
                if not t.get("result", {}).get("success", True)
                or t.get("result", {}).get("executed") is False
            ]
            reply_l = reply.lower()
            ack_words = ("fail", "error", "didn't", "did not", "couldn't",
                         "could not", "not applied", "not authored", "not registered",
                         "did not succeed", "was not", "wasn't")
            claims_success = any(w in reply_l for w in
                ("ready", "registered", "loaded", "is set", "configured",
                 "applied", "hit play", "press play", "done"))
            acks_failure = any(w in reply_l for w in ack_words)
            if last_round_fails and claims_success and not acks_failure:
                logger.warning(
                    f"[{session_id}] Reply claims success with {len(last_round_fails)} tool failures — forcing honesty rewrite"
                )
                failed_names = ", ".join(
                    f"{t['tool']}(success={t['result'].get('success')}, executed={t['result'].get('executed')})"
                    for t in last_round_fails[:4]
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous reply described the effect as succeeded "
                        f"('ready' / 'registered' / 'hit Play' / similar), but "
                        f"these tool calls FAILED: {failed_names}. "
                        "Rewrite the reply honestly: say exactly which step failed "
                        "and why, do NOT tell the user to hit Play if no working "
                        "drive/callback was actually installed, and propose one "
                        "concrete next step. Do NOT call more tools. Keep it tight."
                    ),
                })
                try:
                    rewrite_ctx = dict(context) if isinstance(context, dict) else {}
                    rewrite_ctx["tools"] = []
                    rewrite_response = await self.llm_provider.complete(messages, rewrite_ctx)
                    rewrite = (rewrite_response.text or "").strip()
                    if rewrite:
                        reply = rewrite
                except Exception as e:
                    logger.warning(f"[{session_id}] Honesty rewrite failed: {e}")

        # Anti-ghosting: if the LLM finished calling tools but never produced
        # visible text, the user sees a blank reply and assumes we died. Force
        # one more call with tools disabled, asking for a concise summary.
        if not reply.strip() and executed_tools:
            logger.warning(f"[{session_id}] Empty reply with {len(executed_tools)} tools — forcing summary")
            messages.append({
                "role": "user",
                "content": (
                    "You just ran tools but didn't write a reply to the user. "
                    "Now write ONE concise paragraph summarising (a) what you did, "
                    "(b) what the user should see in the viewport or stage right now, "
                    "(c) any caveats or next step you'd suggest. "
                    "Do NOT call more tools. If the viewport might not show the result "
                    "(e.g., camera not framed on the new prim), say so explicitly and "
                    "tell them to frame-focus on the prim path."
                ),
            })
            try:
                summary_ctx = dict(context) if isinstance(context, dict) else {}
                summary_ctx["tools"] = []  # disable tool-calling for summary
                summary_response = await self.llm_provider.complete(messages, summary_ctx)
                reply = (summary_response.text or "").strip()
            except Exception as e:
                logger.warning(f"[{session_id}] Anti-ghosting summary failed: {e}")
            if not reply:
                # Fallback — synthesize from tool names so the user sees something
                tools_run = ", ".join({t.get("tool") for t in executed_tools if t.get("tool")})
                reply = (
                    f"I ran these tools: {tools_run}. "
                    "Check the stage tree / viewport for the result; frame-focus on the new "
                    "prim if the camera isn't on it yet."
                )

        # Anti-fabricated-menu-path: Kit menu paths like
        # "Window > Extensions" or "**Isaac Utils** > **Common Samples** > ..."
        # are a common hallucination (AM-01 T5). Flag any such path in reply
        # that did NOT appear in a tool result this session.
        if reply.strip():
            try:
                import re as _re
                tool_result_blob = " ".join(
                    json.dumps(t.get("result", {}), default=str)
                    for t in executed_tools
                )
                # Bold-markdown menu paths: **X** > **Y** > **Z** (2+ hops)
                bold_menu = _re.findall(
                    r"\*\*([A-Z][A-Za-z0-9 _/-]{1,40})\*\*\s*[>›→]\s*\*\*([A-Z][A-Za-z0-9 _/-]{1,40})\*\*(?:\s*[>›→]\s*\*\*([A-Z][A-Za-z0-9 _/-]{1,40})\*\*)?",
                    reply,
                )
                # Plain menu paths starting with known Kit roots
                _KIT_ROOTS = ("Window", "File", "Edit", "Tools", "Layout",
                              "Help", "Create", "Isaac Utils", "Isaac Examples",
                              "Extension Manager")
                plain_menu = _re.findall(
                    rf"\b({'|'.join(_re.escape(r) for r in _KIT_ROOTS)})\s*[>›→]\s*([A-Z][A-Za-z0-9 _/-]{{1,40}})(?:\s*[>›→]\s*([A-Z][A-Za-z0-9 _/-]{{1,40}}))?",
                    reply,
                )
                unverified = []
                for parts in bold_menu + plain_menu:
                    nonempty = [p for p in parts if p]
                    if len(nonempty) < 2:
                        continue
                    path_str = " > ".join(nonempty)
                    # Verified if ALL path components appear in some tool result
                    if not all(p in tool_result_blob for p in nonempty):
                        unverified.append(path_str)
                if unverified:
                    logger.warning(
                        f"[{session_id}] Unverified menu paths in reply: {unverified[:3]}"
                    )
                    warn = (
                        "\n\n⚠️ The menu path(s) above ("
                        + "; ".join(f"'{p}'" for p in unverified[:3])
                        + ") were not retrieved from any tool this session — "
                        "they may not exist in your Kit build. Verify via the "
                        "Extension Manager or share a screenshot of your toolbar."
                    )
                    reply = reply + warn
            except Exception as e:
                logger.warning(f"[{session_id}] menu-path validation failed: {e}")

        # Verify-before-assert contract: check every scene-state claim in the
        # reply against ground truth by auto-invoking the verify primitives.
        # This is the structural (Fas 2) replacement for Fix B's keyword
        # heuristic — no prompt engineering, model-agnostic, runs after the
        # LLM has committed to its answer.
        #
        # Two claim classes we can verify deterministically right now:
        #   (a) Prim-path claims — reply mentions `/World/X` → verify prim_exists
        #   (b) Count claims     — "I cloned N ..." or "N arms" + a path → verify count
        if reply.strip() and executed_tools is not None:
            try:
                import re as _re
                from .tools.tool_executor import execute_tool_call as _exec
                verify_warnings = []

                # (a) All USD-like paths mentioned in the reply
                claimed_paths = set(_re.findall(r"(?<![A-Za-z0-9_])/World[/A-Za-z0-9_]+", reply))
                # Skip paths that already appeared in any tool output this turn
                # (those were grounded in real tool observations).
                tool_output_blob = " ".join(
                    json.dumps(t.get("result", {}), default=str)
                    for t in executed_tools
                )
                unverified_paths = [p for p in claimed_paths if p not in tool_output_blob]
                # Cap at 4 verifications to keep cost bounded
                for p in sorted(unverified_paths)[:4]:
                    try:
                        ver = await _exec("prim_exists", {"prim_path": p})
                        # DATA_HANDLERS return dict with 'output' (json string) OR direct fields
                        out = ver.get("output") if isinstance(ver, dict) else None
                        if isinstance(out, str) and out.strip().startswith("{"):
                            try:
                                parsed = json.loads(out)
                            except Exception:
                                parsed = {}
                        else:
                            parsed = ver if isinstance(ver, dict) else {}
                        exists = parsed.get("exists")
                        if exists is False:
                            verify_warnings.append(f"`{p}` does not exist in the stage")
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify prim_exists({p}) failed: {e}")

                # (b) Count claims: "N arms", "N robots", "N clones", etc. paired with a path
                # Matches patterns like "16 arms ... at /World/envs" or "cloned 16 Franka"
                count_pat = _re.compile(
                    r"\b(?P<n>\d{1,4})\s+(?P<noun>arms?|robots?|clones?|copies|instances?|envs?|environments?|cubes?|spheres?|cameras?)\b[^\n]{0,200}?(?P<path>/World[/A-Za-z0-9_]+)?",
                    _re.I,
                )
                matched_counts = set()
                for m in count_pat.finditer(reply):
                    n = int(m.group("n"))
                    path = m.group("path")
                    key = (n, path)
                    if key in matched_counts or not path:
                        continue
                    matched_counts.add(key)
                    if len(matched_counts) > 2:
                        break
                    try:
                        ver = await _exec("count_prims_under_path", {
                            "parent_path": path, "recursive": False,
                        })
                        out = ver.get("output") if isinstance(ver, dict) else None
                        parsed = {}
                        if isinstance(out, str) and out.strip().startswith("{"):
                            try: parsed = json.loads(out)
                            except: pass
                        actual = parsed.get("count")
                        if isinstance(actual, int) and actual != n:
                            verify_warnings.append(
                                f"reply claims {n} {m.group('noun')} under `{path}`, "
                                f"but count_prims_under_path found {actual}"
                            )
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify count({path}) failed: {e}")

                if verify_warnings:
                    logger.warning(
                        f"[{session_id}] verify-contract mismatches: {verify_warnings[:3]}"
                    )
                    warn = (
                        "\n\n⚠️ Verification mismatch — I checked my own claims against the stage and found: "
                        + "; ".join(verify_warnings[:3])
                        + ". Treat the summary above as provisional and re-run the failing step."
                    )
                    reply = reply + warn
            except Exception as e:
                logger.warning(f"[{session_id}] verify-contract failed: {e}")

        # Anti-ModuleNotFoundError: scan inline ```python blocks in the reply
        # for deprecated `omni.isaac.*` imports before handing to user. Prepend
        # a visible warning if found — user was about to copy-paste broken code.
        if reply.strip():
            try:
                import re as _re
                code_blocks = _re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply, _re.S)
                from .tools.api_validator import validate_code as _api_validate
                bad = []
                for blk in code_blocks:
                    _ok, issues = _api_validate(blk)
                    for i in issues:
                        if i.get("severity") == "deprecated":
                            bad.append(i.get("message", "deprecated import"))
                if bad:
                    warn = (
                        "\n\n⚠️ The code above uses deprecated 4.x namespaces "
                        "and will raise ModuleNotFoundError on Isaac Sim 5.x: "
                        + "; ".join(bad[:3])
                        + ". Ask me to regenerate it with `isaacsim.*` modules."
                    )
                    reply = reply + warn
            except Exception as e:
                logger.warning(f"[{session_id}] inline-code validation failed: {e}")

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
