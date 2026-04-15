"""
context_distiller.py
--------------------
Pre-processes each user request to produce a compact context package for
the main LLM call.  Reduces prompt size by:

  1. Selecting only relevant tool schemas  (44 → ~8-15)
  2. Selecting only relevant system-prompt rules
  3. Compressing conversation history into a state summary (via small LLM)
  4. Filtering scene context to prim paths that matter

Two-stage pipeline:
  Stage 1 (this module — deterministic + small LLM):
      analyse intent/keywords → select tools & rules → summarise history
  Stage 2 (orchestrator — main LLM):
      receive compact system prompt + slim tool list → generate response
"""
from __future__ import annotations

import logging
import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .tools.tool_schemas import ISAAC_SIM_TOOLS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool categorisation  (tool name → set of categories)
# ---------------------------------------------------------------------------
TOOL_CATEGORIES: Dict[str, List[str]] = {
    "usd_core": [
        "create_prim", "delete_prim", "set_attribute", "add_reference",
        "apply_api_schema", "teleport_prim", "list_all_prims",
    ],
    "physics": [
        "apply_api_schema", "set_physics_params", "create_deformable_mesh",
    ],
    "material": ["create_material", "assign_material"],
    "robot": [
        "import_robot", "anchor_robot", "set_joint_targets",
        "get_articulation_state",
    ],
    "omnigraph_ros2": ["create_omnigraph", "ros2_list_topics", "ros2_publish"],
    "camera_viewport": [
        "create_prim", "set_viewport_camera", "capture_viewport",
    ],
    "sensor": ["add_sensor_to_prim", "lookup_product_spec"],
    "sim_control": ["sim_control"],
    "clone": ["clone_prim"],
    "motion_planning": ["move_to_pose", "plan_trajectory"],
    "scene_query": [
        "scene_summary", "list_all_prims", "measure_distance",
        "get_debug_info",
    ],
    "vision": [
        "vision_detect_objects", "vision_bounding_boxes",
        "vision_plan_trajectory", "vision_analyze_scene",
    ],
    "console": ["get_console_errors", "explain_error"],
    "knowledge": ["lookup_knowledge"],
    "scene_builder": [
        "generate_scene_blueprint", "build_scene_from_blueprint",
        "catalog_search",
    ],
    "rl_training": ["create_isaaclab_env", "launch_training"],
    "sdg": ["configure_sdg"],
    "export": ["export_scene_package"],
    "scripting": ["run_usd_script"],
}

# Keyword patterns mapped to tool categories
_KEYWORD_CATEGORIES: List[tuple] = [
    (re.compile(r"robot|franka|ur10|ur5|panda|carter|jetbot|nova|spot|anymal|cobotta", re.I),
     {"robot", "usd_core"}),
    (re.compile(r"ros2?|omnigraph|graph|publish|subscribe|topic|/cmd_vel|/joint|twist|odom", re.I),
     {"omnigraph_ros2", "scripting"}),
    (re.compile(r"physics|rigid.?body|collision|collider|gravity|deform|cloth|soft|mass", re.I),
     {"physics", "usd_core"}),
    (re.compile(r"material|color|red|blue|green|glass|metal|opaque|transparent|pbr", re.I),
     {"material", "usd_core"}),
    (re.compile(r"camera|viewport|screenshot|image|render.?product", re.I),
     {"camera_viewport"}),
    (re.compile(r"sensor|realsense|lidar|velodyne|imu|d435|d455", re.I),
     {"sensor"}),
    (re.compile(r"play|pause|stop|step|reset|simulat", re.I),
     {"sim_control"}),
    (re.compile(r"clone|duplicate|copy|grid|batch", re.I),
     {"clone", "usd_core"}),
    (re.compile(r"motion|trajectory|waypoint|rmp|planner|reach|pick.?and.?place|end.?effector", re.I),
     {"motion_planning", "robot"}),
    (re.compile(r"vision|detect|bounding.?box|spatial|what.?do.?you.?see|look.?at", re.I),
     {"vision"}),
    (re.compile(r"error|warning|console|log|debug|crash", re.I),
     {"console"}),
    (re.compile(r"knowledge|document|api.?usage", re.I),
     {"knowledge"}),
    (re.compile(r"warehouse|kitchen|scene.?build|blueprint|layout|design.?a", re.I),
     {"scene_builder", "usd_core"}),
    (re.compile(r"isaaclab|reinforcement|rl|train|gymnasium", re.I),
     {"rl_training"}),
    (re.compile(r"replicator|synth|dataset|annotator|sdg", re.I),
     {"sdg"}),
    (re.compile(r"export|package|save.?scene|project.?files", re.I),
     {"export"}),
    (re.compile(r"move|teleport|position|translate|scale|rotate|transform", re.I),
     {"usd_core"}),
    (re.compile(r"cube|sphere|cylinder|cone|plane|mesh|prim|xform|light|dome", re.I),
     {"usd_core"}),
    (re.compile(r"delete|remove", re.I),
     {"usd_core"}),
    (re.compile(r"joint|articulation|drive|wheel|anchor", re.I),
     {"robot"}),
    (re.compile(r"summary|what.*scene|list.*prim|how.?many", re.I),
     {"scene_query"}),
    (re.compile(r"distance|measure|far|close|between", re.I),
     {"scene_query"}),
    (re.compile(r"search|find|catalog|asset|library", re.I),
     {"scene_builder"}),
]

# Intent → default tool categories (always included for that intent)
_INTENT_CATEGORIES: Dict[str, Set[str]] = {
    "general_query":   {"scene_query", "knowledge"},
    "scene_diagnose":  {"scene_query", "console", "scripting"},
    "vision_inspect":  {"vision", "camera_viewport"},
    "prim_inspect":    {"scene_query", "usd_core"},
    "patch_request":   {"usd_core", "scripting"},
    "physics_query":   {"physics", "robot", "scene_query"},
    "console_review":  {"console"},
    "navigation":      {"usd_core", "scene_query"},
}

# Always-included tools regardless of category (cheap, essential)
_ALWAYS_TOOLS = {"run_usd_script", "scene_summary", "lookup_knowledge"}

# Build a fast name→schema lookup
_TOOL_BY_NAME: Dict[str, Dict] = {
    t["function"]["name"]: t for t in ISAAC_SIM_TOOLS
}

# ---------------------------------------------------------------------------
# System-prompt rule sections  (keyed by topic, selected per request)
# ---------------------------------------------------------------------------
RULE_BASE = """\
You are Isaac Assist, an expert AI embedded inside NVIDIA Isaac Sim — authored by 10Things, Inc. (www.10things.tech).
You help robotics engineers build, diagnose, and control simulations using natural language.

When the user asks you to modify the scene, use the provided tools. For complex operations combine tools or use run_usd_script.
Always use proper USD paths starting with '/'. Be concise. When you generate code, use the Kit/pxr Python APIs.

CRITICAL:
- NEVER call AddTranslateOp()/AddRotateXYZOp()/AddScaleOp() on prims that already have xformOps.
  Reuse existing ops via xformable.GetOrderedXformOps().
- Always import: import omni.usd; from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf
- Always get stage via: stage = omni.usd.get_context().get_stage()
- For transforms on referenced prims, check if xformOps exist first."""

RULE_OMNIGRAPH = """\
OmniGraph rules (Isaac Sim 5.1):
- Node paths use tuples: ("graph_path", "node_name"), NOT "graph_path/node_name"
- Node types use isaacsim.* namespace (NOT omni.isaac.*):
  isaacsim.ros2.bridge.ROS2PublishJointState, isaacsim.ros2.bridge.ROS2SubscribeJointState,
  isaacsim.core.nodes.IsaacArticulationController, isaacsim.ros2.bridge.ROS2Context
- ArticulationController: set robot path via SET_VALUES with "inputs:robotPath"
- TYPE COMPATIBILITY: ROS2SubscribeTwist outputs double3, DifferentialController expects scalar double.
  Use Break3Vector node to extract x/z components. Cannot wire directly."""

RULE_ROBOT = """\
Robot rules:
- STATIONARY robots (Franka arm): use anchor_robot with fixedBase=True. NEVER move ArticulationRootAPI off root prim.
- WHEELED/MOBILE robots (Nova Carter, Jetbot): do NOT set fixedBase=True. Delete rootJoint, add rigid body + colliders.
  Use DifferentialController for wheeled robots."""

RULE_NOVA_CARTER = """\
Nova Carter specifics:
- Differential-drive: 2 powered front wheels + 2 free-spinning rear casters.
- Drive joints: joint_wheel_left, joint_wheel_right. Casters: joint_caster_swivel_left/right, joint_caster_wheel_left/right.
- Wheel collision: set physics:approximation='convexHull' on all 6 wheel/caster prims (PhysX rejects triangle mesh on dynamic bodies).
- DifferentialController targets only the two front drive joints."""

RULE_CLONER = """\
Cloning: isaacsim.core.cloner.GridCloner for batch cloning (≥4 copies), Sdf.CopySpec for small counts."""

RULE_SELECTION = """\
Selection awareness: When the user has selected a prim, its path/properties are in context.
"this", "it", "selected object", "make this bigger" all refer to the selected prim.
Use its path directly — do NOT ask the user to specify."""

# Keyword patterns → which rule sections to include
_KEYWORD_RULES: List[tuple] = [
    (re.compile(r"omnigraph|ros2?|graph|publish|subscribe|topic|twist|odom|clock|joint.?state", re.I),
     [RULE_OMNIGRAPH]),
    (re.compile(r"robot|franka|ur10|panda|anchor|articulation|fixed.?base", re.I),
     [RULE_ROBOT]),
    (re.compile(r"carter|nova.?carter|wheeled|differential|caster", re.I),
     [RULE_NOVA_CARTER, RULE_ROBOT]),
    (re.compile(r"clone|grid|batch|replicate", re.I),
     [RULE_CLONER]),
]


# ---------------------------------------------------------------------------
# Conversation knowledge — compact state object per session
# ---------------------------------------------------------------------------
@dataclass
class ConversationKnowledge:
    """Incrementally-updated compact knowledge of a conversation."""
    session_id: str
    turn_count: int = 0
    scene_prims: List[str] = field(default_factory=list)      # key prims in scene
    robots: List[str] = field(default_factory=list)            # robot prim paths
    ros2_topics: List[str] = field(default_factory=list)       # detected topics
    recent_actions: List[str] = field(default_factory=list)    # last N actions (compact)
    pending_issues: List[str] = field(default_factory=list)    # known problems
    compressed_history: str = ""                               # LLM-summarised history
    last_compressed_turn: int = 0                              # turn when last compressed

    def record_action(self, action: str):
        self.recent_actions.append(action)
        if len(self.recent_actions) > 8:
            self.recent_actions = self.recent_actions[-8:]

    def record_robot(self, path: str):
        if path not in self.robots:
            self.robots.append(path)

    def record_topic(self, topic: str):
        if topic not in self.ros2_topics:
            self.ros2_topics.append(topic)

    def to_context_string(self) -> str:
        parts = []
        if self.robots:
            parts.append(f"Robots in scene: {', '.join(self.robots)}")
        if self.ros2_topics:
            parts.append(f"ROS2 topics: {', '.join(self.ros2_topics)}")
        if self.recent_actions:
            parts.append(f"Recent actions: {'; '.join(self.recent_actions[-5:])}")
        if self.pending_issues:
            parts.append(f"Known issues: {'; '.join(self.pending_issues)}")
        if self.compressed_history:
            parts.append(f"Session summary: {self.compressed_history}")
        return "\n".join(parts)


@dataclass
class DistilledContext:
    """Minimal context package for the main LLM call."""
    system_prompt: str
    tools: List[Dict]
    messages: List[Dict]   # compressed history + current user message
    token_estimate: int = 0


# ---------------------------------------------------------------------------
# History compression (uses small / local LLM)
# ---------------------------------------------------------------------------
COMPRESS_SYSTEM = """\
You are a concise summariser. Given a conversation between a user and an AI assistant \
about NVIDIA Isaac Sim, produce a 2-4 sentence summary capturing:
1. What scene objects exist now (prims, robots, sensors)
2. What the user has been doing (the workflow so far)
3. Any unresolved problems or goals
Reply with ONLY the summary, no preamble."""

# Compress every N turns to avoid calling the small LLM on every message
COMPRESS_INTERVAL = 4


async def compress_history(
    history: List[Dict],
    knowledge: ConversationKnowledge,
    small_provider,
) -> str:
    """Use a small/local LLM to compress conversation history."""
    if not history:
        return knowledge.compressed_history

    # Only re-compress if enough new turns have accumulated
    new_turns = knowledge.turn_count - knowledge.last_compressed_turn
    if new_turns < COMPRESS_INTERVAL and knowledge.compressed_history:
        return knowledge.compressed_history

    # Build a compact version of history for the summariser
    compact = []
    for m in history[-12:]:  # last 12 messages max
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content:
            continue
        # Truncate long tool results
        if role == "tool":
            content = content[:300]
        elif len(content) > 500:
            content = content[:500] + "..."
        compact.append(f"{role}: {content}")

    prior = ""
    if knowledge.compressed_history:
        prior = f"Previous summary: {knowledge.compressed_history}\n\n"

    messages = [
        {"role": "user", "content": f"{prior}Conversation:\n" + "\n".join(compact) + "\n\nSummarise:"}
    ]

    try:
        original_system = getattr(small_provider, "_system_override", None)
        small_provider._system_override = COMPRESS_SYSTEM
        response = await small_provider.complete(messages, {})
        summary = response.text.strip()
        if summary:
            knowledge.compressed_history = summary
            knowledge.last_compressed_turn = knowledge.turn_count
            return summary
    except Exception as e:
        logger.warning(f"History compression failed: {e}")
    finally:
        if hasattr(small_provider, "_system_override"):
            try:
                del small_provider._system_override
            except Exception:
                pass

    return knowledge.compressed_history


# ---------------------------------------------------------------------------
# Tool selection  (deterministic — no LLM needed)
# ---------------------------------------------------------------------------
def select_tools(
    intent: str,
    message: str,
    knowledge: ConversationKnowledge,
) -> List[Dict]:
    """Return only the tool schemas relevant to this request."""
    categories: Set[str] = set()

    # 1. Intent-based defaults
    categories.update(_INTENT_CATEGORIES.get(intent, {"usd_core", "scripting"}))

    # 2. Keyword-based additions
    for pattern, cats in _KEYWORD_CATEGORIES:
        if pattern.search(message):
            categories.update(cats)

    # 3. Context-based: if robots in scene, include robot tools
    if knowledge.robots:
        categories.add("robot")
    if knowledge.ros2_topics:
        categories.add("omnigraph_ros2")

    # 4. Collect tool names from selected categories
    tool_names: Set[str] = set(_ALWAYS_TOOLS)
    for cat in categories:
        tool_names.update(TOOL_CATEGORIES.get(cat, []))

    # 5. Resolve to actual schemas
    tools = [_TOOL_BY_NAME[name] for name in tool_names if name in _TOOL_BY_NAME]

    logger.info(
        f"[Distiller] Selected {len(tools)}/{len(ISAAC_SIM_TOOLS)} tools "
        f"(categories: {sorted(categories)})"
    )
    return tools


# ---------------------------------------------------------------------------
# Rule selection  (deterministic)
# ---------------------------------------------------------------------------
def select_rules(
    intent: str,
    message: str,
    knowledge: ConversationKnowledge,
    has_selection: bool = False,
) -> str:
    """Build a minimal system prompt with only relevant rules."""
    parts = [RULE_BASE]

    # Keyword-driven rule sections
    combined = message
    if knowledge.robots:
        combined += " " + " ".join(knowledge.robots)

    for pattern, rules in _KEYWORD_RULES:
        if pattern.search(combined):
            for r in rules:
                if r not in parts:
                    parts.append(r)

    # Always include selection awareness if something is selected
    if has_selection:
        parts.append(RULE_SELECTION)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Scene context filtering
# ---------------------------------------------------------------------------
def filter_scene_context(
    full_context: str,
    message: str,
    knowledge: ConversationKnowledge,
    max_lines: int = 40,
) -> str:
    """Trim scene context to only the parts relevant to the user's request."""
    if not full_context:
        return ""

    lines = full_context.strip().split("\n")
    if len(lines) <= max_lines:
        return full_context

    # Extract prim paths mentioned in the message
    mentioned = set()
    for match in re.finditer(r"/World/\S+", message):
        mentioned.add(match.group().rstrip(",.;:'\""))
    for robot in knowledge.robots:
        mentioned.add(robot)

    # Keep: header lines, mentioned prims, summary stats
    kept = []
    for line in lines:
        # Always keep summary/header lines
        if any(kw in line.lower() for kw in ("prim count", "total", "physics", "summary", "---")):
            kept.append(line)
            continue
        # Keep lines mentioning relevant prims
        if any(p in line for p in mentioned):
            kept.append(line)
            continue
        # Keep the first few lines regardless (usually scene overview)
        if len(kept) < 5:
            kept.append(line)

    if len(kept) > max_lines:
        kept = kept[:max_lines]

    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Main distillation entry point
# ---------------------------------------------------------------------------
async def distill_context(
    *,
    intent: str,
    user_message: str,
    history: List[Dict],
    knowledge: ConversationKnowledge,
    scene_context: str = "",
    selected_prim: Optional[Dict] = None,
    selected_prim_path: Optional[str] = None,
    isaac_version: str = "",
    rag_text: str = "",
    patterns_text: str = "",
    error_learnings_text: str = "",
    success_learnings_text: str = "",
    small_provider=None,
) -> DistilledContext:
    """
    Main entry point: produce a compact context package.

    Parameters mirror what orchestrator.handle_message currently collects.
    Returns a DistilledContext ready for the main LLM call.
    """
    knowledge.turn_count += 1

    # ── 1. Compress history (async, uses small LLM if available) ──────────
    if small_provider and history:
        await compress_history(history, knowledge, small_provider)

    # ── 2. Select tools ──────────────────────────────────────────────────
    tools = select_tools(intent, user_message, knowledge)

    # ── 3. Build minimal system prompt ───────────────────────────────────
    has_selection = bool(selected_prim or selected_prim_path)
    system = select_rules(intent, user_message, knowledge, has_selection)

    if isaac_version:
        system += f"\nIsaac Sim version: {isaac_version}"

    # Filtered scene context
    filtered_scene = filter_scene_context(scene_context, user_message, knowledge)
    if filtered_scene:
        system += f"\n\n--- SCENE STATE ---\n{filtered_scene}"

    # Selected prim (compact)
    if selected_prim:
        sel = selected_prim
        sel_parts = [f"Selected: {sel.get('path', '?')} ({sel.get('type', '?')})"]
        if sel.get("world_position"):
            sel_parts.append(f"pos={sel['world_position']}")
        if sel.get("schemas"):
            sel_parts.append(f"schemas={','.join(sel['schemas'][:5])}")
        system += "\n\n" + " | ".join(sel_parts)
        system += '\n"this"/"it" = the selected prim above.'
    elif selected_prim_path:
        system += f"\n\nSelected prim: {selected_prim_path}"
        system += '\n"this"/"it" = the selected prim.'

    # Conversation state (compact summary, not raw history)
    conv_state = knowledge.to_context_string()
    if conv_state:
        system += f"\n\n--- SESSION STATE ---\n{conv_state}"

    # RAG / KB entries (already filtered to top-N by orchestrator)
    if rag_text:
        system += f"\n\n{rag_text}"
    if patterns_text:
        system += f"\n\n{patterns_text}"
    # Only include error/success learnings if they're compact
    if error_learnings_text and len(error_learnings_text) < 1500:
        system += f"\n\n{error_learnings_text}"
    if success_learnings_text and len(success_learnings_text) < 1000:
        system += f"\n\n{success_learnings_text}"

    # ── 4. Build message list (compressed) ───────────────────────────────
    messages = [{"role": "system", "content": system}]

    # Include only the last 2 raw exchanges + the compressed summary is in system
    recent_raw = []
    for m in history[-4:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            content = m["content"]
            if len(content) > 800:
                content = content[:800] + "..."
            recent_raw.append({"role": m["role"], "content": content})
    messages.extend(recent_raw)
    messages.append({"role": "user", "content": user_message})

    # ── 5. Estimate token count ──────────────────────────────────────────
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    total_chars += sum(len(json.dumps(t)) for t in tools)
    token_estimate = total_chars // 4  # rough char→token estimate

    logger.info(
        f"[Distiller] ~{token_estimate} tokens | "
        f"{len(tools)} tools | {len(messages)} msgs | "
        f"system={len(system)} chars"
    )

    return DistilledContext(
        system_prompt=system,
        tools=tools,
        messages=messages,
        token_estimate=token_estimate,
    )


# ---------------------------------------------------------------------------
# Helpers for orchestrator to update knowledge from tool results
# ---------------------------------------------------------------------------
_ROBOT_PATTERN = re.compile(
    r"(?:franka|ur10|ur5|panda|carter|nova.?carter|jetbot|spot|anymal|cobotta)",
    re.I,
)
_TOPIC_PATTERN = re.compile(r"/(cmd_vel|joint_\w+|odom|clock|camera/\w+|tf)", re.I)


def update_knowledge_from_tool(
    knowledge: ConversationKnowledge,
    tool_name: str,
    tool_args: Dict,
    tool_result: Dict,
):
    """Extract signal from a tool call/result and update session knowledge."""
    # Track actions
    desc = tool_args.get("description", "") or tool_name
    knowledge.record_action(f"{tool_name}: {desc[:60]}")

    # Detect robots
    for key in ("prim_path", "robot_path", "articulation_path", "dest_path"):
        val = tool_args.get(key, "")
        if val and _ROBOT_PATTERN.search(val):
            knowledge.record_robot(val)

    if tool_name == "import_robot":
        dest = tool_args.get("dest_path", tool_args.get("file_path", ""))
        if dest:
            knowledge.record_robot(dest)

    # Detect ROS2 topics
    raw = json.dumps(tool_args)
    for m in _TOPIC_PATTERN.finditer(raw):
        knowledge.record_topic(m.group())

    # Track scene prims from results
    code = tool_result.get("code", "")
    for m in re.finditer(r"'/World/[A-Za-z0-9_/]+'", code):
        path = m.group().strip("'")
        if path not in knowledge.scene_prims:
            knowledge.scene_prims.append(path)
            if len(knowledge.scene_prims) > 30:
                knowledge.scene_prims = knowledge.scene_prims[-30:]
