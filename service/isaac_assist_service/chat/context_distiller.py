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
    "omnigraph_ros2": [
        "create_omnigraph", "ros2_connect", "ros2_list_topics",
        "ros2_get_topic_type", "ros2_get_message_type",
        "ros2_subscribe_once", "ros2_publish", "ros2_publish_sequence",
        "ros2_list_services", "ros2_call_service",
        "ros2_list_nodes", "ros2_get_node_details",
    ],
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
        "catalog_search", "nucleus_browse", "download_asset",
    ],
    "rl_training": ["create_isaaclab_env", "launch_training"],
    "sdg": ["configure_sdg"],
    "export": ["export_scene_package"],
    "stage_analysis": ["run_stage_analysis"],
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
    (re.compile(r"nucleus|content.?library|browse.?asset|download.?asset|omniverse://|pull.?asset", re.I),
     {"scene_builder"}),
    (re.compile(r"isaaclab|reinforcement|rl|train|gymnasium", re.I),
     {"rl_training"}),
    (re.compile(r"replicator|synth|dataset|annotator|sdg", re.I),
     {"sdg"}),
    (re.compile(r"export|package|save.?scene|project.?files", re.I),
     {"export"}),
    (re.compile(r"diagnos|validat|analyz|analyse|check.?error|what.?s.?wrong|stage.?analys|health.?check|scan.?scene", re.I),
     {"stage_analysis", "scene_query", "console"}),
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
    "scene_diagnose":  {"scene_query", "console", "scripting", "stage_analysis"},
    "vision_inspect":  {"vision", "camera_viewport"},
    "prim_inspect":    {"scene_query", "usd_core"},
    "patch_request":   {"usd_core", "scripting"},
    "physics_query":   {"physics", "robot", "scene_query"},
    "console_review":  {"console"},
    "navigation":      {"usd_core", "scene_query"},
}

# Always-included tools regardless of category (cheap, essential)
_ALWAYS_TOOLS = {
    "run_usd_script", "scene_summary", "lookup_knowledge", "lookup_api_deprecation",
    # Typed-variable resolvers — meta-tools that gate other tools' inputs.
    # Always relevant when their linguistic pattern fires. They compete
    # poorly against action-tools in embedding-rank (action verbs dominate),
    # so include them unconditionally rather than rely on retrieval.
    "place_on_top_of", "resolve_prim_reference",
    "resolve_size_adjective", "resolve_count_vagueness", "resolve_robot_class",
    "resolve_material_properties", "resolve_constraint_phrase",
    "resolve_sequence_phrase", "resolve_context_reference",
    "resolve_skill_composition",
    "resolve_success_condition", "verify_pickplace_pipeline",
    "resolve_coordinate_reference", "resolve_relational_property",
}

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

EXECUTION DISCIPLINE (read first):
- If the user's message describes a concrete task with a measurable outcome ("place X at Y", "clone N", "anchor the robot", "hide these prims"), you MUST execute the FULL plan end-to-end in this turn, not just the first step.
- Internally articulate the plan as a short list before tool calls: e.g. "plan: (1) confirm target exists, (2) run the mutation, (3) verify result". Then EXECUTE every step via tools.
- Do NOT stop after one exploratory tool call ("I confirmed the robot's initial state") and hand the task back to the user — that is a failure mode. Keep going until the measurable outcome is authored in the stage.
- For mutations: pair every write with a matching read — e.g. after set_attribute, call get_attribute; after clone, call count_prims_under_path; after creating a prim, call prim_exists or get_world_transform. Never declare success without a backing read.

HONESTY DISCIPLINE (overrides everything else):
1. Tool failure = effect did NOT happen. If a tool call returned executed=false, success=false, or an error:
   - Do NOT write "ready", "loaded", "registered", "set", "configured", "applied", "done" about that effect.
   - State explicitly: "The <tool> call failed: <short reason>. <effect> was NOT applied."
   - Offer a concrete next step (retry with different args, alternate tool, or ask user).
2. Before claiming a robot will MOVE on Play, verify that drive targets / physics callbacks / joint velocity setpoints actually exist — do not infer from a scene-populate tool alone.
3. Never invent Kit UI menu paths, extension IDs, or click sequences. If you don't have a verified source, say "I don't know the exact menu path for your build — open Extension Manager or share a screenshot."
4. Isaac Sim 5.x uses `isaacsim.*` namespace. Never generate code importing `omni.isaac.core`, `omni.isaac.franka`, `omni.isaac.kit`, `omni.isaac.urdf`, or other deprecated 4.x paths. If unsure of the modern name, say so and call lookup_knowledge.
5. Do NOT contradict yourself across turns. If turn N-1 said "script errored", turn N must not then say "it's ready" without new successful tool output proving it.
6. The orchestrator cross-checks your reply against the live stage (Fas 2 verify-contract). It auto-invokes `prim_exists`, `count_prims_under_path` (shallow AND recursive), `get_world_transform`, `list_applied_schemas`, and `get_attribute` on any /World/... paths, numeric counts, pose tuples, schema names, or attribute=value claims in your reply. If a check fails, a ⚠️ Verification mismatch block is appended for the user to see. Your best strategy: verify BEFORE you write each claim — cheaper to call `get_attribute` once than to be publicly corrected by the mismatch block.
7. If you DON'T know the current state of a prim/attribute/schema the user is asking about, do NOT guess. Call the read tool (`prim_exists`, `get_attribute`, `list_applied_schemas`, `get_world_transform`) first, then answer with the returned value. "Just a quick yes or no" or demo-time pressure are NOT reasons to skip verification — honest two-line answers with real data beat confident single-line fabrications.
8. Narrative precision around idempotent operations. If you ran a script with a `if not stage.GetPrimAtPath(...): <create>` guard or called a tool that is idempotent (DefinePrim on existing prim, Apply on prim that already has the API), do NOT narrate "Created X" or "Added X" without a post-check that you actually added something. If you don't know whether the branch fired, say "ensured X is present" instead of "created X".
9. Issue-list sanity check. When a diagnostic tool like `check_physics_health` returns issues containing a scene-level prim path (e.g. "Missing PhysicsScene at /World/PhysicsScene"), verify with a fast read (`prim_exists`) before acting on the framing. Diagnostic tools can report false-positives when their search scope is narrower than the prim being looked for — the user-visible effect is "you said you created X but I see X was already there".
10. A user *assertion* about current state ("X is rotated 90°", "Y has mass=2.0", "Z is at the origin") is NOT an instruction to make the assertion true. It is a CLAIM to verify. If the stage disagrees with the assertion, the correct action is to report the actual state and ask whether the user wants it changed — do NOT silently author the asserted value. The failure mode: user says "Cylinder is rotated 90° like I set it", agent runs a script that adds a 90° rotateY op, then reports "Yes, rotated 90°". That's two lies glued together (the initial agreement, and concealing that the agent authored the state). Verify FIRST, then act ONLY with explicit user intent to mutate.

CODE RULES:
- NEVER call AddTranslateOp()/AddRotateXYZOp()/AddScaleOp() on prims that already have xformOps.
  Reuse existing ops via xformable.GetOrderedXformOps().
- Always import: import omni.usd; from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf
- Always get stage via: stage = omni.usd.get_context().get_stage()
- For transforms on referenced prims, check if xformOps exist first.

PRIM CREATION RULES (run_usd_script or code_patch):
- ALWAYS create new prims under /World/<Name> with an explicit path. NEVER let the path default.
  Correct:   cube = UsdGeom.Cube.Define(stage, "/World/Cube_3")
  Correct:   prim = stage.DefinePrim("/World/Cube_3", "Cube")
  FORBIDDEN: omni.kit.commands.execute("CreatePrim", prim_type="Cube") without prim_path=...
             — this defaults to "/Cube", "/Cube_01" at origin, ignoring any xform code you
             wrote for "/World/Cube_3". You will report success while the actual prim is
             at root with wrong position.
- Set the xform on the SAME path you just defined. After creation:
    xform = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Cube_3"))
    _safe_set_translate(xform.GetPrim(), (x, y, z))   # or reuse existing ops
- Before claiming "placed <X> at <pos>" in your reply, call `get_world_transform("/World/<X>")`
  and quote the actual returned coordinates. If the read fails, the prim wasn't created where you
  think — do NOT fabricate success."""

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

RULE_MULTI_STEP_PLAN = """\
Multi-step composition discipline (when the user asks for ≥3 linked actions, or uses "sen"/"och sedan"/"tills"/"om X finns så gör Y"):

BEFORE any stage-mutating tool call, you MUST:
1. Scan the tool catalog for the HIGHEST-LEVEL tool that matches each step. The catalog has tools like create_conveyor_track (creates both geometry AND motion logic), create_hdri_skydome (one call for dome light), apply_physics_material, clone_envs, etc. If such a tool exists for a step, use it instead of composing the same behavior from raw USD script. Missing the right high-level tool was the 2026-04-19 failure mode — agent built a conveyor manually and lost the motion logic.
2. Articulate a numbered plan in your reply, with a VERIFIABLE post-condition per step (what tool call + expected output proves the step succeeded). Example:
   Step 1: create_conveyor_track(waypoints=[...], belt_width=1.0, speed=1.0) → verify `/World/ConveyorTrack/Segment_0` exists + has ConveyorGraph child.
   Step 2: Measure conveyor top surface Z via get_world_transform + get_bounding_box → obtain top_z.
   Step 3: Scale each cube to 0.2m via set_attribute(size=0.2) → verify size attr reads 0.2.
   Step 4: Position cubes on top of conveyor at top_z + half_cube_size → verify translate reads target.
   Step 5: Start simulation via sim_control(action="play") → verify timeline is playing.
   Step 6: Monitor cube positions in a bounded loop (max_ticks=300) until all cubes' x > conveyor_end OR z < ground → verify via get_world_transform.
3. Stop after each executed step and confirm the post-condition BEFORE proceeding. If a step fails, report the failure + stop — do NOT plow ahead to step N+1 on broken state.
4. "Conditional" phrasing ("om sådan finns", "om det behövs") means: probe the catalog FIRST (e.g. check if `create_conveyor_track` exists in your tools list), then proceed. Do NOT assume tool existence and guess at function calls.
5. Open-ended steps ("kanske behövs en sensor") mean: propose one concrete option + alternatives + pick one with justification. Do NOT execute without picking.

The goal is to give the user a plan they can SEE and approve or correct before stage state changes — same principle as an approval button, but for the plan itself. A plan with a wrong post-condition is cheap to fix; a stage half-mutated with silent tool-call failures is not."""


RULE_DETERMINISM = """\
Deterministic replay / reproducibility (Isaac Sim 5.x) — WHEN THE USER ASKS ABOUT DETERMINISM, START by calling `lookup_api_deprecation(query="deterministic replay")` to fetch the canonical cite-row. The tool returns tool_5x, deprecated_4x, cite paragraph, caveats — quote them verbatim. THEN cite these names in your reply:

**Required citations** (do NOT paraphrase these — use the exact names):
1. The correct 5.x tool is `enable_deterministic_mode` (takes seed, physics_dt, solver_iterations). This IS a registered tool — call it, don't just mention it.
2. The deprecated 4.x API is `omni.isaac.core.SimulationContext.set_deterministic` — REMOVED in 5.0, raises ImportError. Flag it explicitly as deprecated if the user mentions it.
3. Protocol name: "archive-with-version-pin" — pin the Kit build hash, solver config, and seed alongside the USD for CI rosbag-diff reproducibility.

**Technical facts to back the citations:**
- `enable_deterministic_mode` authors TGS solver + fixed timestep (60 Hz default) + CPU dynamics on /World/PhysicsScene.
- GPU dynamics (PhysX tensor view) is NOT fully deterministic across runs even on identical hardware. CPU dynamics required for bit-identical replay.
- Determinism is HARDWARE-DEPENDENT for floating-point ordering: same seed + same code → bit-identical only on same GPU model + same CUDA driver.
- Never claim "fully deterministic" without the hardware-pin caveat.

If asked for a cite-able statement, produce a paragraph the user can paste, containing the tool name + the deprecated-API warning + the hardware caveat + the archive-with-version-pin protocol.

**Copy-paste template** (adapt the bracketed parts to the user's context, but DO NOT remove the API names or caveats):

> "In Isaac Sim 5.x, reproducible replay is set up via the `enable_deterministic_mode(seed=..., physics_dt=..., solver_iterations=...)` tool, which authors the TGS solver + fixed timestep + CPU dynamics on /World/PhysicsScene. The 4.x `omni.isaac.core.SimulationContext.set_deterministic()` method [that <PM/user> referenced] was removed in 5.0 and raises ImportError. Note that PhysX float ordering is hardware-dependent: bit-identical replay is only guaranteed on the same GPU model + CUDA driver, so the CI rig must be pinned. For rosbag-diff regression, follow the 'archive-with-version-pin' protocol — ship the Kit build hash, solver config, and seed alongside the USD stage as CI artifacts."

If the user asks for cite material, START YOUR REPLY WITH THIS PARAGRAPH (modified for their specific scenario), then add any clarifying caveats. Do not write a general PhysX determinism essay as a substitute."""

# Keyword patterns → which rule sections to include
_KEYWORD_RULES: List[tuple] = [
    # Multi-step composition: Swedish "sen / och sedan / tills / om X finns",
    # English "then / and then / until / if X exists". Also triggered if the
    # prompt contains ≥3 distinct action verbs in sequence. The 2026-04-19
    # conveyor test failed because agent never PLANNED — ran 10 tool calls,
    # 4+ failures, missed `create_conveyor_track` tool, and settled for a
    # static mock when the user asked for a moving belt.
    (re.compile(
        r"\bsen\s+(?:skalar?|placerar?|kör|sätter?|startar?|aktiverar?|lägger?|"
        r"skapar?|mäter?|flyttar?|ändrar?|uppdaterar?|verifierar?)\b|"
        r"\boch\s+sedan\b|\btills?\s+(?:alla|de|den|alla\s+boxes)\b|"
        r"\bom\s+(?:sådan|den|något)\s+(?:finns|behövs)\b|"
        r"\bthen\s+(?:scale|place|run|start|add|create|measure|move|change|update|verify)\b|"
        r"\band\s+then\b|\buntil\s+(?:all|the|every)\b|"
        r"\bif\s+(?:such|one|any|it|a|the)\s+\w+(?:\s+\w+){0,3}\s+(?:exists?|needed|available)\b|"
        r"\b(?:behövs|needs?)\s+(?:kanske|maybe|possibly)\b",
        re.I,
    ), [RULE_MULTI_STEP_PLAN]),
    (re.compile(r"omnigraph|ros2?|graph|publish|subscribe|topic|twist|odom|clock|joint.?state", re.I),
     [RULE_OMNIGRAPH]),
    (re.compile(r"robot|franka|ur10|panda|anchor|articulation|fixed.?base", re.I),
     [RULE_ROBOT]),
    (re.compile(r"carter|nova.?carter|wheeled|differential|caster", re.I),
     [RULE_NOVA_CARTER, RULE_ROBOT]),
    (re.compile(r"clone|grid|batch|replicate", re.I),
     [RULE_CLONER]),
    (re.compile(
        r"determini(?:stic|sm)|repeatab|bit.?identical|reproducib|"
        r"ci\s*regression|rosbag\s*diff|same\s*seed|fixed\s*timestep",
        re.I,
    ), [RULE_DETERMINISM]),
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

    # 4. Semantic retrieval first (research-preferred narrowing): get the
    # top-20 tools whose descriptions best match the user message. This is
    # REPLACE mode — retrieval is the primary filter, not an addition.
    tool_names: Set[str] = set(_ALWAYS_TOOLS)
    try:
        from .tools.tool_retriever import retrieve_tools
        semantic = retrieve_tools(message, top_k=20)
        tool_names.update(semantic)
    except Exception as e:
        logger.warning(f"[Distiller] Semantic tool retrieval failed, falling back to categories: {e}")
        # Only use categories when retrieval is unavailable
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

    # Extract prim paths mentioned in the message.
    # Using a strict char class (not \S+) so apostrophe-s contractions
    # like "/World/Robot's" don't pollute the path — previously the
    # mentioned set would contain "/World/Robot's" which never matches
    # the scene-context lines (they contain "/World/Robot"), and the
    # filter silently dropped the relevant context.
    mentioned = set()
    for match in re.finditer(r"/World/[A-Za-z0-9_/]+", message):
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
    negative_patterns_text: str = "",
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
    if negative_patterns_text and len(negative_patterns_text) < 1200:
        system += f"\n\n{negative_patterns_text}"

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
