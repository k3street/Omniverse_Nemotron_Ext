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
    _KEYWORD_RULES,
    RULE_MULTI_STEP_PLAN,
)
from .tools.kit_tools import (
    get_stage_context,
    format_stage_context_for_llm,
    is_kit_rpc_alive,
)
from .tools.tool_schemas import ISAAC_SIM_TOOLS
from .tools.tool_executor import execute_tool_call
from .tools.descriptions import describe as _describe_tool
from .slash_commands import parse_slash, execute_slash
from .session_trace import emit as _trace_emit
from .cancel_registry import (
    is_cancelled as _is_cancelled,
    clear as _cancel_clear,
)
import time as _t
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


import re as _re_mod


# NOTE: path was previously `(?P<path>...)?` which, combined with the
# preceding non-greedy `{0,200}?`, meant the engine always picked 0 chars
# and no path — the extractor then dropped the match for lack of a path.
# Net effect: count-claim verification was silently disabled. Making path
# non-optional forces the engine to consume enough to reach a /World/...
# reference; matches without a path no longer produce a count claim.
#
# The gap-fill char class uses `[^.\n]` instead of `[^\n]` so the regex
# can NOT cross a period — stops the count+path linkage from jumping
# sentence boundaries (e.g. "16 robots. /World/envs is empty." no
# longer produces a spurious (16, "robots", "/World/envs") claim).
# Commas are still allowed as intra-sentence separators.
_COUNT_PAT = _re_mod.compile(
    r"\b(?P<n>\d{1,4})\s+(?P<noun>arms?|robots?|clones?|copies|instances?|envs?|environments?|cubes?|spheres?|cameras?)\b[^.\n]{0,200}?(?P<path>/World[/A-Za-z0-9_]+)",
    _re_mod.I,
)
_POSE_PAT = _re_mod.compile(
    r"(?P<path>/World[/A-Za-z0-9_]+)[^\n]{0,180}?"
    r"(?:at|to|positioned at|located at|moved to|placed at|transform)\s*"
    r"\(?\s*(?P<x>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<y>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<z>-?\d+(?:\.\d+)?)\s*\)?",
    _re_mod.I,
)
_SCHEMA_PAT = _re_mod.compile(
    r"(?P<schema>(?:Physics|Physx|UsdPhysics\.|PhysxSchema\.)?\w+API)"
    r"[^\n]{0,120}?"
    r"(?P<path>/World[/A-Za-z0-9_]+)"
    r"|"
    r"(?P<path2>/World[/A-Za-z0-9_]+)"
    r"[^\n]{0,120}?"
    r"(?P<schema2>(?:Physics|Physx|UsdPhysics\.|PhysxSchema\.)?\w+API)",
    _re_mod.I,
)
_ATTR_SEP = r"(?:\s*[=:]\s*|\s+(?:is|of|set to|=)\s+)"
_ATTR_WORDS = r"mass|friction|restitution|damping|stiffness|radius|height|density|size"
_ATTR_PAT_PATH_FIRST = _re_mod.compile(
    r"(?P<path>/World[/A-Za-z0-9_]+)"
    r"[^\n]{0,120}?"
    r"(?P<attr>" + _ATTR_WORDS + r")"
    + _ATTR_SEP +
    r"(?P<val>-?\d+(?:\.\d+)?)",
    _re_mod.I,
)
_ATTR_PAT_ATTR_FIRST = _re_mod.compile(
    r"\b(?P<attr>" + _ATTR_WORDS + r")\b"
    r"[^\n]{0,20}?"
    r"(?:on\s+|of\s+|for\s+)?"
    r"(?P<path>/World[/A-Za-z0-9_]+)"
    r"[^\n]{0,40}?"
    r"(?:is|of|set to|=|:)\s*"
    r"(?P<val>-?\d+(?:\.\d+)?)",
    _re_mod.I,
)
# attr → (connector) → value → (on/of/for) → path
# Covers "height set to 2.0 on /World/Cylinder" — the val-before-path
# phrasing that neither path-first nor attr-first (val-after-path)
# patterns match. Surfaced as an xfail in the edge-case tests.
_ATTR_PAT_VAL_BEFORE_PATH = _re_mod.compile(
    r"\b(?P<attr>" + _ATTR_WORDS + r")\b"
    r"[^\n]{0,20}?"
    r"(?:is|of|set to|=|:)\s*"
    r"(?P<val>-?\d+(?:\.\d+)?)"
    r"[^\n]{0,30}?"
    r"(?:on\s+|of\s+|for\s+)"
    r"(?P<path>/World[/A-Za-z0-9_]+)",
    _re_mod.I,
)
_ATTR_NAME_MAP = {
    "mass": "physics:mass",
    "friction": "physics:dynamicFriction",
    "restitution": "physics:restitution",
    "damping": "drive:angular:physics:damping",
    "stiffness": "drive:angular:physics:stiffness",
    "radius": "radius",
    "height": "height",
    "density": "physics:density",
    "size": "size",
}


def _extract_count_claims(reply: str) -> List[tuple]:
    """Extract (n, noun, path) count claims from a reply.

    Matches patterns like "16 arms under /World/envs" or "cloned 16 Franka".
    Claims without a /World/... path are discarded — counts without a place
    can't be verified. Deduped on (n, path) keeping first-seen noun.
    """
    seen = set()
    out: List[tuple] = []
    for m in _COUNT_PAT.finditer(reply or ""):
        path = m.group("path")
        if not path:
            continue
        n = int(m.group("n"))
        key = (n, path)
        if key in seen:
            continue
        seen.add(key)
        out.append((n, m.group("noun"), path))
    return out


def _extract_pose_claims(reply: str) -> List[tuple]:
    """Extract (path, (x, y, z)) TRANSLATION pose claims. Coordinates rounded
    to 3 dp. Rotation-verb phrasings ("rotated to (0, 90, 0)") are excluded
    because the orchestrator's verify-contract (c) cross-checks against
    get_world_transform's TRANSLATION field — matching rotation tuples there
    would produce false-positive mismatch warnings (AD-21 surfaced this).

    Matches "/World/X at (1, 2, 3)", "... moved to (a, b, c)", etc. Deduped
    on the full (path, claim) tuple.
    """
    seen = set()
    out: List[tuple] = []
    for m in _POSE_PAT.finditer(reply or ""):
        # Exclude rotation-verb phrasings. The verb alternation includes
        # "to", which matches inside "rotated to" — so the regex picks up
        # rotation claims we don't want to cross-check via translation.
        # Inspect the text from path-start to verb-start for a rotation
        # marker.
        span_text = (reply or "")[m.start():m.end()]
        if _re_mod.search(r"\brotat(?:ed|ion|e)\b", span_text, _re_mod.I):
            continue
        path = m.group("path")
        claim = (
            round(float(m.group("x")), 3),
            round(float(m.group("y")), 3),
            round(float(m.group("z")), 3),
        )
        key = (path, claim)
        if key in seen:
            continue
        seen.add(key)
        out.append((path, claim))
    return out


def _extract_schema_claims(reply: str) -> List[tuple]:
    """Extract (schema, path) API-application claims.

    Handles both directions: "RigidBodyAPI applied to /World/X" and
    "/World/X has CollisionAPI". Schema name is normalized — the
    UsdPhysics./PhysxSchema. prefixes and trailing punctuation are stripped.
    Deduped on (schema, path).
    """
    seen = set()
    out: List[tuple] = []
    for m in _SCHEMA_PAT.finditer(reply or ""):
        schema = (m.group("schema") or m.group("schema2") or "")
        schema = schema.lstrip("UsdPhysics.").lstrip("PhysxSchema.").rstrip(".,;:")
        path = m.group("path") or m.group("path2")
        if not schema or not path:
            continue
        key = (schema, path)
        if key in seen:
            continue
        seen.add(key)
        out.append((schema, path))
    return out


def _extract_attr_claims(reply: str) -> List[tuple]:
    """Extract (path, attr_short, value) attribute-value claims.

    Handles both phrasings: path-first ("/World/X has mass=1.0") and
    attr-first ("mass on /World/X is 1.0"). Returns the short attr word
    (lowercase, e.g. "mass") — callers map to the full USD attribute name
    via _ATTR_NAME_MAP before calling get_attribute. Deduped on
    (path, attr, value).
    """
    seen = set()
    out: List[tuple] = []
    for m in (
        list(_ATTR_PAT_PATH_FIRST.finditer(reply or ""))
        + list(_ATTR_PAT_ATTR_FIRST.finditer(reply or ""))
        + list(_ATTR_PAT_VAL_BEFORE_PATH.finditer(reply or ""))
    ):
        path = m.group("path")
        attr = m.group("attr").lower()
        claim = round(float(m.group("val")), 3)
        key = (path, attr, claim)
        if key in seen:
            continue
        seen.add(key)
        out.append((path, attr, claim))
    return out


_CREATION_VERB_PAT = _re_mod.compile(
    r"\b(?:placed?|placerat?|placerade|created?|skapat?|skapade|added?|"
    r"lade\s+till|lagt\s+till|authored?|genererat|genererade)\b",
    _re_mod.I,
)
_BACKTICK_NAME_PAT = _re_mod.compile(r"`([A-Z][A-Za-z0-9_]{0,40})`")


def _extract_bare_prim_name_claims(reply: str) -> List[str]:
    """Extract bare backtick-quoted prim names used in a creation context.

    Catches the 2026-04-19 failure: reply says
        "placerat två nya kuber (`Cube_3` och `Cube_4`)"
    while real prims landed at /Cube, /Cube_01 at root.
    The (a) path-check only sees /World/... paths; these bare names slip
    through. Returned as /World/<Name> so the caller can probe prim_exists.

    Gating: only names that appear within 80 chars of a creation verb
    (placed/placerat/created/skapat/added/lade till/...) and look like
    prim identifiers (CapCase, alphanum+underscore). Paths already
    fully-qualified (starting with `/`) are skipped — they're handled
    by the existing /World/... extractor.
    """
    out: List[str] = []
    seen: set = set()
    text = reply or ""
    verb_spans = [m.start() for m in _CREATION_VERB_PAT.finditer(text)]
    if not verb_spans:
        return out
    for m in _BACKTICK_NAME_PAT.finditer(text):
        name = m.group(1)
        # Skip path-like tokens — handled by the /World/... extractor.
        if "/" in name:
            continue
        # Require a creation verb within 80 chars before or after.
        near = any(abs(vs - m.start()) <= 80 for vs in verb_spans)
        if not near:
            continue
        path = f"/World/{name}"
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _partition_path_existence(
    executed_tools: List[Dict[str, Any]],
) -> tuple[set, set]:
    """Parse a turn's tool results into (confirmed_present, confirmed_absent)
    prim-path sets.

    Used by the Fas 2 verify-contract item (a) path check to decide which
    claimed paths can be skipped (already grounded in tool observation as
    present), flagged immediately (observed absent), or need a fresh
    prim_exists probe (unverified).

    A tool result counts toward existence evidence when its payload carries
    both a ``prim_path`` (starting with ``/World``) and a boolean ``exists``
    field. Data handlers wrap their payload in ``{"output": "<json>"}``,
    so we try parsing that first and also inspect the top-level dict.
    """
    confirmed_absent: set = set()
    confirmed_present: set = set()
    for t in executed_tools or []:
        result = t.get("result") or {}
        payloads: List[Any] = []
        out_str = result.get("output") if isinstance(result, dict) else None
        if isinstance(out_str, str) and out_str.strip().startswith("{"):
            try:
                payloads.append(json.loads(out_str))
            except Exception:
                pass
        if isinstance(result, dict):
            payloads.append(result)
        for pl in payloads:
            if not isinstance(pl, dict):
                continue
            pp = pl.get("prim_path")
            ex = pl.get("exists")
            if isinstance(pp, str) and pp.startswith("/World"):
                if ex is False:
                    confirmed_absent.add(pp)
                elif ex is True:
                    confirmed_present.add(pp)
    return confirmed_present, confirmed_absent


def _summarize_args(args: Dict[str, Any]) -> str:
    """One-line summary of tool-call args for the live UI strip.

    Priority: prim_path leaf → path leaf → first non-empty value.
    Truncated to 30 chars. Full args still flow through args_full for
    the tooltip.
    """
    if not args:
        return ""
    for key in ("prim_path", "path", "prim", "target"):
        if key in args and args[key]:
            v = str(args[key])
            return v.rsplit("/", 1)[-1] if "/" in v else v[:30]
    for v in args.values():
        if v not in (None, "", [], {}):
            return str(v)[:30]
    return ""


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

        # Trace the user message (silent on IO failure)
        _trace_emit(session_id, "user_msg", {"text": user_message})
        # Live progress: signal turn start so the UI can show "Thinking…"
        # immediately, before the LLM call latency begins.
        _trace_emit(session_id, "turn_started", {
            "user_message_preview": (user_message or "")[:120],
        })
        # Drop any stale cancel flag from a previous turn before we start
        # polling it in the round loop.
        _cancel_clear(session_id)

        # ── 0. Slash-command interception ────────────────────────────────────
        # /note /block /pin /cite /help short-circuit the LLM — deterministic,
        # free, fast. Anything else falls through to the normal pipeline.
        _slash = parse_slash(user_message)
        if _slash is not None:
            def _slash_emit(ev_type: str, payload: Dict[str, Any]) -> None:
                _trace_emit(session_id, ev_type, payload)

            reply = await execute_slash(
                _slash["cmd"], _slash["arg"],
                history=history,
                emit_trace=_slash_emit,
                session_id=session_id,
            )
            # Record the slash in history so pin/next turn can see it
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": reply["reply"]})
            _trace_emit(session_id, "agent_reply", {
                "text": reply["reply"][:500],
                "intent": "slash_command",
            })
            return reply

        # ── 1. Classify intent ───────────────────────────────────────────────
        # Returns a dict {intent, multi_step, complexity, confidence}. multi_step
        # drives the round-0 read-only tool gate below (see _multi_step usage).
        # complexity gates the negotiator (Fas 2 of strategic-brain layer).
        intent_result = await classify_intent(user_message, self.llm_provider)
        intent = intent_result["intent"]
        intent_is_multi_step = intent_result.get("multi_step", False)
        intent_complexity = intent_result.get("complexity", "single")
        _trace_emit(session_id, "intent_complexity", {
            "complexity": intent_complexity,
            "multi_step": intent_is_multi_step,
            "intent": intent,
            "message_preview": user_message[:80],
        })

        # ── 1.2. Negotiator gate (only for complex prompts) ──────────────────
        # When the prompt is "complex", run a single fast-LLM clarification
        # check BEFORE template_retriever / tool_loop. If required inputs are
        # missing, reply with questions immediately and end the turn — the
        # user fills them in and the next turn proceeds normally.
        # Avoids the v1 template_retriever collision that killed b93bcca.
        # Env-gated: STRATEGIC_NEGOTIATOR=off skips entirely (default: on).
        # Disabled when intent is a pure question (general_query) — answering
        # comes first, clarification on doing-things only.
        import os as _os_mod
        _negotiator_enabled = _os_mod.environ.get(
            "STRATEGIC_NEGOTIATOR", "on"
        ).lower() != "off"
        # Skip negotiator if the previous assistant turn already issued a
        # clarification — the user is now ANSWERING our questions, not making
        # a fresh ambiguous request. Continuing to negotiate would loop
        # forever ("Before I start, I need a few things..." → user answers
        # → "Before I start, I need a few things..."). The orchestrator's
        # job in turn N+1 is to USE the answer, not re-litigate ambiguity.
        _last_assistant = None
        for h in reversed(history):
            if h.get("role") == "assistant":
                _last_assistant = h.get("content", "")
                break
        _prior_was_clarification = bool(_last_assistant) and (
            "Before I start, I need a few things" in _last_assistant
            or "Once you confirm, I'll continue" in _last_assistant
        )
        if (
            _negotiator_enabled
            and intent_complexity == "complex"
            and intent != "general_query"
            and not _prior_was_clarification
        ):
            try:
                from .negotiator import negotiate, format_clarification_reply
                neg = await negotiate(user_message, self.llm_provider)
                _trace_emit(session_id, "negotiator", {
                    "needs_clarification": neg["needs_clarification"],
                    "n_questions": len(neg["questions"]),
                    "reasoning": neg["reasoning"],
                })
                if neg["needs_clarification"]:
                    clarif_text = format_clarification_reply(neg, user_message)
                    history.append({"role": "user", "content": user_message})
                    history.append({"role": "assistant", "content": clarif_text})
                    _trace_emit(session_id, "agent_reply", {
                        "text": clarif_text[:500],
                        "intent": "negotiation_clarification",
                    })
                    return {
                        "intent": "negotiation_clarification",
                        "reply": clarif_text,
                        "tool_calls": [],
                        "code_patches": [],
                    }
            except Exception as _ne:
                # Fail-open — never let negotiation block normal flow
                logger.warning(f"[Negotiator] gate failed ({_ne}), proceeding")
        elif _prior_was_clarification:
            _trace_emit(session_id, "negotiator_skipped", {
                "reason": "prior turn was clarification — proceeding to act on user's answer",
            })

        # ── 1.5. Auto turn-snapshot for stage-mutating turns ─────────────────
        # Before running any tools that might write to the stage, save the
        # current root-layer USDA to disk. `/undo` restores this snapshot so
        # a user can revert a turn-worth of agent mischief in one shot —
        # exactly what was missing from the 2026-04-19 conveyor smoke-test
        # (scale + delete + conveyor-create was impossible to cleanly revert).
        # Non-mutating intents skip the snapshot to keep token/rpc cost low.
        # Hoisted out of the if so we can read it later when emitting agent_reply
        # — the UI uses has_snapshot to gate the per-bubble undo button.
        _snap_result: Optional[Dict[str, Any]] = None
        _MUTATING_INTENTS = {"patch_request", "scene_diagnose"}
        if intent in _MUTATING_INTENTS and await is_kit_rpc_alive():
            try:
                from .turn_snapshot import capture as _capture_turn
                _snap_label = _re_mod.sub(r"[^A-Za-z0-9]", "_", user_message[:30])
                _snap_result = await _capture_turn(session_id, label=_snap_label)
                if _snap_result.get("ok"):
                    _trace_emit(session_id, "turn_snapshot_saved", {
                        "path": _snap_result.get("path"),
                        "turn_index": _snap_result.get("turn_index"),
                        "layer_size": _snap_result.get("layer_size"),
                    })
                else:
                    logger.warning(
                        f"[{session_id}] turn snapshot failed: "
                        f"{_snap_result.get('error')}"
                    )
            except Exception as e:
                # Never crash a user turn because snapshotting failed.
                logger.warning(f"[{session_id}] turn snapshot exception: {e}")

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

        # ── 3.5. Strategic-brain Fas 3: spec_generator + gap_analyzer ────────
        # When complexity=="complex", produce a structured execution plan and
        # inject as a dedicated CHECKLIST in patterns_text. Templates still
        # flow normally — the spec is supplementary, with clear "REQUIRED"
        # framing that differentiates from "reference templates".
        # Empirically motivated: 40% of broad-canary fails were
        # "skipped_required_tool" — agent didn't call tools task-spec
        # mandates. The spec lists those tools by name as post-conditions.
        # Env-gated: STRATEGIC_SPEC=off disables.
        _spec_enabled = (
            _os_mod.environ.get("STRATEGIC_SPEC", "on").lower() != "off"
        )
        if (
            _spec_enabled
            and intent_complexity == "complex"
            and intent != "general_query"
        ):
            try:
                from .spec_generator import generate_spec, format_spec_as_checklist
                from .gap_analyzer import analyze as gap_analyze, get_registered_tools
                spec = await generate_spec(user_message, self.llm_provider)
                if spec:
                    registered = get_registered_tools()
                    gap = gap_analyze(spec.get("steps", []), registered)
                    checklist = format_spec_as_checklist(spec, gap_report=gap)
                    if patterns_text:
                        patterns_text = checklist + "\n\n---\n\n" + patterns_text
                    else:
                        patterns_text = checklist
                    _trace_emit(session_id, "spec_generated", {
                        "n_steps": len(spec.get("steps", [])),
                        "matched": len(gap.get("matched", [])),
                        "partial": len(gap.get("partial", {})),
                        "missing": len(gap.get("missing", [])),
                        "reasoning": spec.get("reasoning", "")[:120],
                    })
            except Exception as _se:
                # Fail-open — never block normal flow on spec generation
                logger.warning(f"[SpecGenerator] gate failed ({_se}), proceeding")

        # Auto-inject cite-index matches from deprecations.jsonl. Agents
        # routinely FAIL to call lookup_api_deprecation even when a rule
        # tells them to, and rule-injection alone has no enforcement. Pull
        # top-3 cite rows based on prompt keywords and prepend to rag_text
        # so the canonical recipes (conveyor surface-velocity combo, Franka
        # import URL, open-top bin structure, etc.) land in the system
        # prompt without requiring agent tool-call.
        try:
            from ..knowledge.deprecations_index import lookup as _cite_lookup
            cite_rows = _cite_lookup(user_message, top_k=3)
            if cite_rows:
                cite_parts = ["## Canonical API / pattern cites for this request", ""]
                for row in cite_rows:
                    cite_parts.append(f"### {row['id']}")
                    cite_parts.append(row.get("cite", "").strip())
                    if row.get("caveats"):
                        cite_parts.append("**Caveats:**")
                        for c in row["caveats"]:
                            cite_parts.append(f"- {c}")
                    cite_parts.append("")
                cite_preamble = "\n".join(cite_parts)
                rag_text = cite_preamble + ("\n\n" + rag_text if rag_text else "")
                _trace_emit(session_id, "cites_auto_injected", {
                    "row_ids": [r["id"] for r in cite_rows],
                    "chars": len(cite_preamble),
                })
        except Exception as e:
            logger.warning(f"[{session_id}] Cite auto-injection failed: {e}")

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

        # Retry-spam halt: count CONSECUTIVE failed patches in the turn.
        # Reset counter on any success. If ≥N failures in a row — regardless
        # of description — agent is stuck and we break the loop to force a
        # reasoned summary.
        #
        # Threshold history:
        #   - v1 (2026-04-19, thr=3): fired too early. In the conveyor test
        #     agent was mid-exploration on attempt 4 when halted, and never
        #     got to scale/place the cubes — giving HONEST partial success
        #     but LESS functional output than attempt 1 which had no halt.
        #   - v2 (2026-04-19, thr=6): current. Catches genuine spam (≥6 in
        #     a row all failing) but allows agent's natural explore-a-few-
        #     variants-then-adjust rhythm. In attempt 1 agent needed 5-7
        #     fails before hitting the working cube-mutation script; that's
        #     allowed now.
        _SPAM_HALT_THRESHOLD = 6
        consecutive_fail_count = 0
        first_failed_description: str = ""
        spam_halted = False

        # 2026-04-19 ROLLBACK: earlier this day we gated round 0 of multi-step
        # turns to a read-only tool subset, aiming to force the agent to plan
        # before mutating. Empirically this REGRESSED behavior — the agent
        # read the gate as "prepare a comprehensive mega-patch" and shoved
        # all steps (create conveyor, scale cubes, place cubes) into a single
        # atomic script. When the script failed, the subsequent retries
        # focused only on the last failing sub-step, and the earlier
        # successful-in-theory sub-steps (cube mutations) were silently
        # dropped. Reply then fabricated success for the dropped steps.
        # The stepwise "try small patches, keep what lands" pattern the
        # default (no gate) already produces beats this manual planning
        # attempt. Keep the LLM-based multi_step classification for trace
        # visibility, but don't let it change tool availability.
        # Use the LLM-based classifier result (set above by classify_intent).
        # Fall back to the regex keyword patterns if classifier failed or
        # returned low confidence — defense in depth for the phrasings the
        # LLM might miss (Swedish edge cases, idiomatic English).
        _multi_step = bool(intent_is_multi_step)
        if not _multi_step:
            # Regex fallback: cheap, catches the obvious sequencing phrases.
            _multi_step = any(
                pat.search(user_message or "")
                and RULE_MULTI_STEP_PLAN in rules
                for pat, rules in _KEYWORD_RULES
            )
        if _multi_step:
            _trace_emit(session_id, "multi_step_detected", {
                "prompt_prefix": (user_message or "")[:120],
                "source": "classifier" if intent_is_multi_step else "regex_fallback",
            })

        max_rounds = config.max_tool_rounds
        cancelled = False
        for round_idx in range(max_rounds):
            # Cancel check at the top of each round — between LLM rounds is
            # the natural decision point. If the user hit Stop, exit cleanly
            # before issuing another LLM call.
            if _is_cancelled(session_id):
                _trace_emit(session_id, "cancel_acknowledged", {"round": round_idx})
                cancelled = True
                break
            try:
                response = await self.llm_provider.complete(
                    messages, {"tools": selected_tools}
                )
            except Exception as e:
                logger.error(f"LLM provider error: {e}")
                raise

            # Capture Gemini chain-of-thought when GEMINI_EXPOSE_THOUGHTS=1.
            # Thoughts are stored in the session trace (not shown to user) so
            # we can post-hoc diagnose reasoning bugs like the 2026-04-19
            # Cube 3/4 swap without asking the user to repro.
            _thoughts = getattr(response, "thoughts", None)
            if _thoughts:
                for _th in _thoughts:
                    _trace_emit(session_id, "agent_thought", {
                        "round": round_idx,
                        "text": _th[:2000],
                    })
                logger.info(
                    f"[{session_id}] Captured {len(_thoughts)} thought-part(s) "
                    f"({sum(len(t) for t in _thoughts)} chars)"
                )

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
                # Inner cancel check — between tools within a round. Lets us
                # bail mid-round so we don't fire all queued tools after the
                # user hit Stop.
                if _is_cancelled(session_id):
                    _trace_emit(session_id, "cancel_acknowledged", {
                        "round": round_idx,
                        "remaining_in_round": len(real_tool_calls) - real_tool_calls.index(tc),
                    })
                    cancelled = True
                    break
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
                    # Truncation: 800 chars default, but for run_usd_script we need
                    # the FULL code to diagnose placement bugs (otherwise we never
                    # see what the agent actually emitted to Kit).
                    _limit = 4000 if fn_name == "run_usd_script" else 800
                    logger.info(f"[{session_id}] TOOL CALL: {fn_name}({json.dumps(fn_args)[:_limit]})")
                    # Live progress: bracket the tool call with started/finished
                    # events so the UI can render a row, spin during execution,
                    # then mark ✓/✗. tc_id ties them together.
                    _t0 = _t.monotonic()
                    _trace_emit(session_id, "tool_call_started", {
                        "tc_id": tc_id,
                        "tool": fn_name,
                        "args_preview": _summarize_args(fn_args),
                        "args_full": fn_args,
                        "description": _describe_tool(fn_name),
                    })
                    result = await execute_tool_call(fn_name, fn_args)
                    _elapsed_ms = int((_t.monotonic() - _t0) * 1000)
                    _success = (
                        result.get("success")
                        if "success" in result
                        else (result.get("type") != "error")
                    )
                    _trace_emit(session_id, "tool_call_finished", {
                        "tc_id": tc_id,
                        "tool": fn_name,
                        "success": bool(_success),
                        "elapsed_ms": _elapsed_ms,
                        "error": (result.get("error") if result.get("type") == "error" else None),
                    })

                executed_tools.append({
                    "tool": fn_name,
                    "arguments": fn_args,
                    "result": result,
                })

                if result.get("type") == "code_patch":
                    # If the patch already ran (AUTO_APPROVE=true or other
                    # auto-exec path), it's already in the stage — don't
                    # re-surface it as an Approve & Execute button, which
                    # confuses the user into clicking to redo completed work.
                    # The tool call record (executed_tools) still captures
                    # that it ran with success/output.
                    if result.get("executed"):
                        _trace_emit(session_id, "patch_executed", {
                            "description": result.get("description", ""),
                            "success": result.get("success"),
                        })
                        # Retry-spam halt: count CONSECUTIVE failures. Reset
                        # on any success. Description can vary between retries
                        # (agent often renames "Creating X..." → "Retrying X..."
                        # → "Setting attrs..." while failing for the same root
                        # cause) so we don't compare strings — just count.
                        if result.get("success") is False:
                            if consecutive_fail_count == 0:
                                first_failed_description = (
                                    result.get("description") or ""
                                )[:80]
                            consecutive_fail_count += 1
                        else:
                            consecutive_fail_count = 0
                            first_failed_description = ""
                    else:
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

            # If cancel fired inside the per-tool loop, break the round loop
            # too — don't issue another LLM call.
            if cancelled:
                break

            # Retry-spam halt: break out if ≥N consecutive patches have
            # returned success=false. Descriptions can vary (agent renames
            # the operation across retries) — count is the reliable signal.
            if consecutive_fail_count >= _SPAM_HALT_THRESHOLD:
                logger.warning(
                    f"[{session_id}] Retry-spam halt: {consecutive_fail_count} "
                    f"consecutive failed patches (first='{first_failed_description}') "
                    f"— breaking tool loop at round {round_idx}"
                )
                _trace_emit(session_id, "retry_spam_halt", {
                    "consecutive_fails": consecutive_fail_count,
                    "first_failed_description": first_failed_description,
                    "threshold": _SPAM_HALT_THRESHOLD,
                    "round": round_idx,
                })
                # Nudge the agent to stop and reason.
                messages.append({
                    "role": "user",
                    "content": (
                        f"HALT: you've run {consecutive_fail_count} patches in "
                        f"a row that all returned success=false. Stop calling "
                        f"tools now. In your reply: (1) summarize what failed "
                        f"and what specific error repeated across attempts, "
                        f"(2) state the most likely root cause, (3) propose ONE "
                        f"concrete next step. Do NOT retry the same pattern. "
                        f"If an earlier part of the user's multi-step request "
                        f"is already done (e.g. the conveyor geometry was "
                        f"created but the motion-logic failed), explicitly "
                        f"list what DID land vs what did NOT — do not claim "
                        f"success for steps that never executed."
                    ),
                })
                spam_halted = True
                break
        else:
            logger.warning(f"[{session_id}] Hit max tool rounds ({max_rounds})")

        # If halted by spam-detection, do one final LLM call WITHOUT tools to
        # produce the reasoned summary. Tools are excluded so the agent can't
        # sneak in another retry.
        if spam_halted:
            try:
                halt_ctx = dict(context) if isinstance(context, dict) else {}
                halt_ctx["tools"] = []
                halt_response = await self.llm_provider.complete(messages, halt_ctx)
                if halt_response.text and halt_response.text.strip():
                    response = halt_response  # reply = response.text on next line
            except Exception as e:
                logger.warning(f"[{session_id}] Post-halt summary failed: {e}")

        reply = response.text or ""

        # Cancel reply path: if the user hit Stop, short-circuit the
        # post-loop pipeline (verify, anti-fabrication, code-block check)
        # and return a canned summary. The agent_reply trace event still
        # fires below — the UI relies on it to clear the live strip.
        if cancelled:
            n = len(executed_tools or [])
            reply = (
                f"Stopped. Completed {n} step{'s' if n != 1 else ''} before stop. "
                "Type a new prompt to continue or refine."
            )
            _cancel_clear(session_id)

        # Anti-fabrication: if the last round of tool calls contained failures
        # (success=false or executed=false) and the reply doesn't acknowledge
        # them — i.e. it sounds like the effect succeeded — force a rewrite.
        # This is the motion-fabrication pattern seen in R-02 / AM-01: Assist
        # says "callback registered, hit Play" after run_usd_script failed.
        # Skip on cancel — the canned "Stopped" reply contains "completed"
        # which would falsely trigger the rewrite.
        if reply.strip() and executed_tools and not cancelled:
            last_round_fails = [
                t for t in executed_tools[-6:]  # last ~round of calls
                if not t.get("result", {}).get("success", True)
                or t.get("result", {}).get("executed") is False
            ]
            reply_l = reply.lower()
            ack_words = ("fail", "error", "didn't", "did not", "couldn't",
                         "could not", "not applied", "not authored", "not registered",
                         "did not succeed", "was not", "wasn't")
            # Success-claim keywords — expanded with common scene-mutation verbs
            # beyond the original "callback/play" family. Each word is a strong
            # signal of "effect landed" that the agent shouldn't be using when
            # a tool call in the same round came back success=False.
            claims_success = any(w in reply_l for w in
                ("ready", "registered", "loaded", "is set", "configured",
                 "applied", "hit play", "press play", "done",
                 "created", "placed", "anchored", "added", "removed",
                 "deleted", "imported", "attached", "enabled", "disabled",
                 "successfully", "completed"))
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

        # Anti-silent-execution: reply is a backend-error placeholder (503
        # from llm_gemini.py: "trouble reaching my reasoning backend", "backend
        # returned N", "backend is overloaded") but tool calls in this turn
        # DID execute successfully — often writing real changes to the stage
        # (AUTO_APPROVE=true path). The user sees the effect (ljuset tänds)
        # but the chat says "try again", and history stores the error string,
        # so the NEXT turn has no memory of what was just done and the agent
        # confidently says "Jag har inte gjort några ändringar". Rewrite the
        # reply with a synthesized summary built from executed_tools so both
        # the user AND future turns see ground truth.
        _ERROR_REPLY_MARKERS = (
            "trouble reaching my reasoning backend",
            "couldn't reach my reasoning backend",
            "couldn't complete that request (backend returned",
            "backend is overloaded",
        )
        _reply_is_error = any(m in reply.lower() for m in _ERROR_REPLY_MARKERS)
        _any_successful_tool = any(
            (t.get("result") or {}).get("success") is True
            or (t.get("result") or {}).get("executed") is True
            for t in (executed_tools or [])
        )
        if _reply_is_error and _any_successful_tool:
            logger.warning(
                f"[{session_id}] Reply is backend-error but {len(executed_tools)} "
                f"tools ran with successes — synthesizing from tool log"
            )
            lines = []
            for t in executed_tools or []:
                res = t.get("result") or {}
                name = t.get("tool") or "?"
                ok = res.get("success") is True or res.get("executed") is True
                desc = res.get("description") or res.get("code", "")[:80]
                mark = "✓" if ok else "✗"
                lines.append(f"  {mark} {name}({desc[:100]})")
            reply = (
                "The reasoning backend hiccupped mid-turn, but these tool calls "
                "DID run against the stage:\n"
                + "\n".join(lines[:8])
                + "\n\nThe effect is in your stage now. If the result looks wrong, "
                  "say what you see — I'll reconcile from there."
            )

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
                # Also catch bare backtick-quoted prim names used in
                # creation contexts (e.g. "placerat `Cube_3`" without path) —
                # the 2026-04-19 regression where agent named Cube_3/Cube_4
                # while actual prims landed at /Cube, /Cube_01 at root.
                for _p in _extract_bare_prim_name_claims(reply):
                    claimed_paths.add(_p)
                # See _partition_path_existence for the semantics: a path
                # observed with exists=false in any tool output counts as
                # CONFIRMED ABSENT and is flagged immediately; a path
                # observed with exists=true is CONFIRMED PRESENT and skipped;
                # everything else falls through to a fresh prim_exists probe.
                # This closes the inversion-of-meaning gap the prior dumb
                # substring skip missed.
                confirmed_present, confirmed_absent = _partition_path_existence(executed_tools)
                for p in claimed_paths & confirmed_absent:
                    verify_warnings.append(f"`{p}` does not exist in the stage")
                unverified_paths = [
                    p for p in claimed_paths
                    if p not in confirmed_present and p not in confirmed_absent
                ]
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

                # (b) Count claims: "N arms", "N robots", "N clones", etc.
                # Extraction is deduped + path-filtered in _extract_count_claims;
                # we cap verifications at 2 per turn for bounded cost.
                for _ci, (n, _noun, path) in enumerate(_extract_count_claims(reply)):
                    if _ci >= 2:
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
                        # Shallow mismatch — if the recursive count matches,
                        # the nesting is off rather than the total wrong; tell
                        # the user so they can re-parent or re-query.
                        if isinstance(actual, int) and actual != n:
                            rec_actual = None
                            try:
                                rec_ver = await _exec("count_prims_under_path", {
                                    "parent_path": path, "recursive": True,
                                })
                                rec_out = rec_ver.get("output") if isinstance(rec_ver, dict) else None
                                rec_parsed = {}
                                if isinstance(rec_out, str) and rec_out.strip().startswith("{"):
                                    try: rec_parsed = json.loads(rec_out)
                                    except: pass
                                rec_actual = rec_parsed.get("count")
                            except Exception:
                                pass
                            if isinstance(rec_actual, int) and rec_actual == n:
                                verify_warnings.append(
                                    f"reply claims {n} {_noun} under `{path}`, "
                                    f"but only {actual} are direct children; "
                                    f"the {n} match appears in the recursive count — "
                                    f"prims are nested deeper than the claim implies"
                                )
                            else:
                                rec_str = f", recursive={rec_actual}" if rec_actual is not None else ""
                                verify_warnings.append(
                                    f"reply claims {n} {_noun} under `{path}`, "
                                    f"but count_prims_under_path found {actual}"
                                    + rec_str
                                )
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify count({path}) failed: {e}")

                # (c) Transform / pose claims — extraction deduped in
                # _extract_pose_claims; cap at 2 verifications per turn.
                # 5cm tolerance on each axis matches most G-task criteria.
                for _pi, (path, claim) in enumerate(_extract_pose_claims(reply)):
                    if _pi >= 2:
                        break
                    try:
                        ver = await _exec("get_world_transform", {"prim_path": path})
                        out = ver.get("output") if isinstance(ver, dict) else None
                        parsed = {}
                        if isinstance(out, str) and out.strip().startswith("{"):
                            try: parsed = json.loads(out)
                            except: pass
                        tr = parsed.get("translation") or parsed.get("world_translation") or parsed.get("position")
                        if isinstance(tr, (list, tuple)) and len(tr) >= 3:
                            actual = (round(float(tr[0]), 3),
                                      round(float(tr[1]), 3),
                                      round(float(tr[2]), 3))
                            # 5cm tolerance on each axis — matches the tolerances
                            # used in most G-task success criteria.
                            if any(abs(a - c) > 0.05 for a, c in zip(actual, claim)):
                                verify_warnings.append(
                                    f"reply claims `{path}` at {claim}, "
                                    f"but get_world_transform returned {actual}"
                                )
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify pose({path}) failed: {e}")

                # (d) Schema / API application claims — extraction normalizes
                # the schema prefix and dedups in _extract_schema_claims; cap
                # at 3 verifications per turn.
                for _si, (schema, path) in enumerate(_extract_schema_claims(reply)):
                    if _si >= 3:
                        break
                    try:
                        ver = await _exec("list_applied_schemas", {"prim_path": path})
                        out = ver.get("output") if isinstance(ver, dict) else None
                        parsed = {}
                        if isinstance(out, str) and out.strip().startswith("{"):
                            try: parsed = json.loads(out)
                            except: pass
                        applied = parsed.get("applied_schemas") or parsed.get("schemas") or []
                        if applied and not any(schema in s for s in applied):
                            verify_warnings.append(
                                f"reply claims `{schema}` on `{path}`, "
                                f"but list_applied_schemas returned {applied}"
                            )
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify schema({path}/{schema}) failed: {e}")

                # (e) Attribute-value claims — extraction handles both
                # path-first and attr-first phrasings, dedups, and returns
                # short attr names. _ATTR_NAME_MAP is module-level; cap at
                # 3 verifications per turn.
                for _ai, (path, attr, claim) in enumerate(_extract_attr_claims(reply)):
                    if _ai >= 3:
                        break
                    try:
                        attr_name = _ATTR_NAME_MAP.get(attr, attr)
                        ver = await _exec("get_attribute", {
                            "prim_path": path, "attr_name": attr_name
                        })
                        out = ver.get("output") if isinstance(ver, dict) else None
                        parsed = {}
                        if isinstance(out, str) and out.strip().startswith("{"):
                            try: parsed = json.loads(out)
                            except: pass
                        actual = parsed.get("value")
                        if actual is None or parsed.get("error"):
                            continue  # attr missing — separate class, don't over-flag
                        try:
                            actual_num = round(float(actual), 3)
                        except (TypeError, ValueError):
                            continue
                        # 2% tolerance or 0.01 absolute, whichever larger
                        tol = max(0.01, abs(claim) * 0.02)
                        if abs(actual_num - claim) > tol:
                            verify_warnings.append(
                                f"reply claims `{path}` {attr}={claim}, "
                                f"but get_attribute returned {actual_num}"
                            )
                    except Exception as e:
                        logger.debug(f"[{session_id}] verify attr({path}/{attr}) failed: {e}")

                # (f) Snapshot-diff path-substantiation check. For mutation
                # turns (intent=patch_request/scene_diagnose) the agent's
                # reply mentioning a /World/... path is an implicit claim
                # that the path was relevant to the mutation. The diff tells
                # us which paths ACTUALLY changed. Paths mentioned but not
                # in the diff are unsubstantiated — likely fabrications.
                #
                # Language-agnostic by design: no verb regex, no noun-class
                # classification. Uses intent (LLM-classified) + path regex
                # (USD invariant) + stage diff (structural).
                if intent in ("patch_request", "scene_diagnose"):
                    try:
                        from .turn_diff import (
                            compute_diff as _compute_diff,
                            unsubstantiated_paths as _unsub_paths,
                        )
                        _diff = await _compute_diff(session_id)
                        if _diff.ok:
                            _trace_emit(session_id, "turn_diff_computed", {
                                "added": len(_diff.added),
                                "removed": len(_diff.removed),
                                "modified": len(_diff.modified),
                                "total_changes": _diff.total_changes,
                                # Path samples for the UI diff chip + tooltip
                                "added_paths": list(_diff.added)[:8],
                                "removed_paths": list(_diff.removed)[:8],
                                "modified_paths": list(_diff.modified.keys())[:8],
                            })
                            # Reuse the already-computed claimed_paths from
                            # the (a) check above. Caller extracted them
                            # from both /World/... literals and backtick-
                            # quoted bare names in creation contexts.
                            _unsub = _unsub_paths(claimed_paths, _diff)
                            for _p in _unsub[:3]:
                                verify_warnings.append(
                                    f"reply mentions `{_p}` but it was not "
                                    f"added, modified, or removed this turn"
                                )

                        # Silent-robot-import check: detect the specific
                        # robot_wizard / anchor_robot / add_reference
                        # silent-success pattern where the tool returns
                        # success=True but the composed prim has zero
                        # children (asset URL 404'd at composition).
                        # Observed 2026-04-19: agent imported Franka three
                        # runs in a row; all three times /World/Robot ended
                        # up as an empty Xform because the guessed asset
                        # URL was wrong. The tool reports success, the
                        # reply claims success, nothing else catches it.
                        _robot_tools_called = {
                            t.get("tool") for t in (executed_tools or [])
                            if t.get("tool") in {
                                "robot_wizard", "anchor_robot", "import_robot",
                                "add_reference", "add_usd_reference",
                            }
                        }
                        if _robot_tools_called:
                            try:
                                _robot_script = """
import json
import omni.usd
from pxr import Usd, UsdPhysics
stage = omni.usd.get_context().get_stage()
empty_robots = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    name_low = p.lower().rsplit('/', 1)[-1]
    has_art = 'PhysxArticulationAPI' in prim.GetAppliedSchemas() or \
              'PhysicsArticulationRootAPI' in prim.GetAppliedSchemas()
    is_robot_name = any(k in name_low for k in
                        ('robot', 'franka', 'panda', 'ur5', 'ur10',
                         'carter', 'jetbot'))
    if has_art or is_robot_name:
        child_count = len(list(prim.GetAllChildren()))
        if child_count == 0:
            empty_robots.append(p)
print(json.dumps({'empty_robots': empty_robots}))
"""
                                _ex = await kit_tools.exec_sync(_robot_script, timeout=15)
                                _out = (_ex.get('output') or '').strip()
                                for line in reversed(_out.splitlines()):
                                    line = line.strip()
                                    if line.startswith('{'):
                                        try:
                                            _parsed = json.loads(line)
                                        except Exception:
                                            _parsed = {}
                                        for _rp in (_parsed.get('empty_robots') or [])[:2]:
                                            verify_warnings.append(
                                                f"`{_rp}` appears to be a robot/articulation but has "
                                                f"ZERO children — the asset reference likely 404'd at "
                                                f"composition time despite the import tool reporting "
                                                f"success. Check the asset URL (see /cite franka import)."
                                            )
                                        break
                            except Exception as _e:
                                logger.debug(f"[{session_id}] empty-robot check failed: {_e}")

                        # Scene-lighting guard: if the turn added geometry but
                        # the stage has no UsdLux light prim, auto-author a
                        # DomeLight so the viewport is not black. Text cites
                        # + dedicated add_default_light tool alone did not
                        # reliably pull Gemini Flash into calling them for
                        # scene-construction prompts — the agent focuses on
                        # the "task words" (conveyor, robot, bin) and skips
                        # ambient infrastructure. This hook only runs when
                        # new prims were actually added this turn, so it
                        # does not spam lights into chat-only turns.
                        try:
                            if _diff and _diff.ok and len(_diff.added) > 0:
                                from .tools import kit_tools as _kit_tools
                                _light_check = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
has_light = False
for prim in stage.Traverse():
    if 'Light' in str(prim.GetTypeName()):
        has_light = True
        break
print(json.dumps({'has_light': has_light}))
"""
                                _ex = await _kit_tools.exec_sync(_light_check, timeout=10)
                                _out = (_ex.get('output') or '').strip()
                                _has_light = False
                                for line in reversed(_out.splitlines()):
                                    line = line.strip()
                                    if line.startswith('{'):
                                        try:
                                            _has_light = bool(json.loads(line).get('has_light'))
                                        except Exception:
                                            pass
                                        break
                                if not _has_light:
                                    from .tools.tool_executor import _gen_add_default_light
                                    _light_code = _gen_add_default_light({})
                                    _r = await _kit_tools.exec_sync(_light_code, timeout=15)
                                    _trace_emit(session_id, "auto_light_authored", {
                                        "reason": "scene-construction turn produced geometry but no light was authored",
                                        "light_path": "/World/DomeLight",
                                        "intensity": 1000.0,
                                        "kit_success": bool(_r.get("success")),
                                    })
                                    logger.info(
                                        f"[{session_id}] auto-authored /World/DomeLight "
                                        f"(turn had {len(_diff.added)} prims added, no light present)"
                                    )
                        except Exception as _e:
                            logger.debug(f"[{session_id}] scene-light guard failed: {_e}")
                    except Exception as e:
                        logger.debug(f"[{session_id}] turn_diff verify failed: {e}")

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

        # Trace the structured turn outcome (for /stuck, /report, debugging)
        for _tc in executed_tools or []:
            _payload = {
                "tool": _tc.get("tool"),
                "args_keys": list((_tc.get("arguments") or {}).keys()),
                "success": (_tc.get("result") or {}).get("success"),
            }
            # For run_usd_script, capture the first 600 chars of emitted code so
            # we can diagnose placement/xform bugs post-hoc without asking the
            # user to reproduce. 600 is enough to see DefinePrim paths + xform.
            if _tc.get("tool") == "run_usd_script":
                _code = (_tc.get("arguments") or {}).get("code", "")
                if _code:
                    _payload["code_preview"] = _code[:600]
            _trace_emit(session_id, "tool_call", _payload)
        _trace_emit(session_id, "agent_reply", {
            "text": reply[:500],
            "intent": intent,
            "tool_count": len(executed_tools or []),
            "patch_count": len(code_patches or []),
            # UI gates the per-bubble undo button on this — only mutating
            # turns with a successfully captured snapshot are undoable.
            "has_snapshot": bool(_snap_result and _snap_result.get("ok")),
        })

        return {
            "intent": intent,
            "reply": reply,
            "tool_calls": executed_tools,
            "code_patches": code_patches,
        }
