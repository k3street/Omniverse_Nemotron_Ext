"""
spec_generator.py
-----------------
For prompts classified `complexity == "complex"` AND with no high-similarity
template match, generate a structured plan that the executor LLM uses as a
required-tools checklist.

Why this exists
---------------
Empirical evidence from 2026-05-04 broad-persona canary (51 tasks, 9/51 first
pass): 17/42 fails (40%) were "skipped_required_tool" — the agent emitted a
generic reply without calling tools the task-spec mandated. Examples:
  L-03: "did not use the required tools (measure_distance, list_all_prims) ..."
  M-03: "failed to use the required diagnostic tools (diagnose_physics_error,
         run_stage_analysis) and missed several technical requirements..."
  Y-01: "failed to perform any look-dev verification (render mode, viewport
         capture) and did not use the requested material tools..."

The pattern is: agent generates a text-only or 1-2-tool reply when 5+ tool
calls are required by success_criteria. A structured pre-execution spec
lists those tool calls explicitly, in order, with post-conditions per step.

Why v2 not v1
-------------
v1 (commit b93bcca, reverted d61f76c on 2026-04-19) wrote spec content and
template_retriever output both into rag_text — competing structures, agent
picked-mixed, CP-01 score crashed 5/5 → 0/5. v2 differs:
  1. Runs only when complexity=="complex" AND template_retriever returned
     no high-similarity match (>0.85 cosine). Templates win when they exist.
  2. Output goes into a DEDICATED checklist message in the system-prompt
     layer, NOT into rag_text. Templates and spec never share a slot.
  3. Spec lists tools by NAME only (not full handler code) — agent reads it
     as a required-tool-chain enumeration, not as a parallel template.
  4. Failure mode is non-fatal: if spec generation errors, proceed with the
     normal template_retriever path — no regression risk.

How it integrates with negotiator
---------------------------------
Negotiator (clarification gate) runs FIRST. If it intercepts, the turn ends
with questions to the user — spec_generator never sees the prompt because
the agent doesn't have enough info to plan yet. After the user answers in
turn N+1, complexity is re-evaluated, and if still complex, spec_generator
runs against the now-clarified prompt.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TypedDict

logger = logging.getLogger(__name__)


class SpecStep(TypedDict):
    n: int                # 1-indexed step number
    action: str           # short imperative — what this step does
    expected_tool: str    # tool name OR "reply"/"reasoning" for non-tool steps
    post_condition: str   # what should be true after this step


class StructuredSpec(TypedDict):
    goal: str
    steps: list[SpecStep]
    components: list[str]      # things that must exist when goal is met
    success_criteria: list[str]
    reasoning: str             # one-line explanation, telemetry/debug


SPEC_GENERATOR_SYSTEM = """You are a planning module for an AI assistant in NVIDIA Isaac Sim.

The user request has been classified as COMPLEX and no near-perfect template
match exists. Your job: produce a structured execution plan the assistant
will follow as a CHECKLIST during the tool-loop.

Plans must be CONCRETE and TOOL-NAMING — list specific tool names from the
Isaac Sim catalog when you know them. The assistant has tools like:
  Scene construction:    create_prim, robot_wizard, create_conveyor,
                         create_bin, add_proximity_sensor, add_default_light
  Physics + schemas:     apply_api_schema, apply_physics_material,
                         set_physics_params, configure_self_collision
  Robot + articulation:  import_robot, set_drive_gains, anchor_robot,
                         get_articulation_state, get_joint_positions
  Inspection:            scene_summary, list_all_prims, find_prims_by_name,
                         find_prims_by_schema, prim_exists, get_attribute,
                         get_world_transform, get_bounding_box, measure_distance
  Diagnostics:           diagnose_physics_error, check_physics_health,
                         check_collisions, check_collision_mesh, run_stage_analysis
  Motion + control:      setup_pick_place_controller, plan_trajectory, solve_ik,
                         move_to_pose, sim_control
  Scene discovery:       list_local_files, catalog_search, nucleus_browse
  Knowledge:             lookup_knowledge, lookup_api_deprecation, lookup_material
  Vision/SDG:            configure_sdg, configure_camera, capture_viewport,
                         vision_analyze_scene, render_video, set_camera_look_at
  ROS2:                  setup_ros2_bridge, configure_ros2_bridge, ros2_publish

Plan structure:
- 3-10 steps. Each step has a single tool OR a single reasoning/reply action.
- Diagnostic and inspection tools FIRST when the user asserts something about
  the current scene — never trust user state without verifying.
- Construction steps in dependency order (e.g., light before inspecting a
  scene that needs lighting; floor before robot if the robot stands on it).
- Final step is "reply" with a summary listing what was checked / built.

Reply with ONLY valid JSON:
{
  "goal": "one-sentence restatement of what the assistant must achieve",
  "steps": [
    {"n": 1, "action": "...", "expected_tool": "tool_name", "post_condition": "..."},
    ...
  ],
  "components": ["thing1", "thing2"],
  "success_criteria": ["criterion 1", "criterion 2"],
  "reasoning": "one-line note on why this plan"
}

NEVER return free-text outside the JSON. NEVER more than 10 steps.

Examples:

User: "Maya: Franka URDF import + collision-mesh audit"
{
  "goal": "Import a Franka Panda from URDF and audit its collision meshes for issues",
  "steps": [
    {"n": 1, "action": "Import Franka URDF", "expected_tool": "import_robot", "post_condition": "/World/Franka exists with ArticulationRootAPI"},
    {"n": 2, "action": "Verify import succeeded", "expected_tool": "verify_import", "post_condition": "all 7 arm joints + 2 finger joints present"},
    {"n": 3, "action": "Run collision-mesh quality check", "expected_tool": "check_collision_mesh", "post_condition": "report shows convex-hull issues per link"},
    {"n": 4, "action": "Diagnose any flagged links", "expected_tool": "diagnose_physics_error", "post_condition": "categorized issue list with fixes"},
    {"n": 5, "action": "Reply with audit summary", "expected_tool": "reply", "post_condition": "user has actionable list of mesh issues"}
  ],
  "components": ["/World/Franka", "collision audit report"],
  "success_criteria": [
    "Robot is articulated and visible",
    "Reply includes per-link collision-mesh diagnostic",
    "Any convex-hull warnings cited with fix recommendations"
  ],
  "reasoning": "Maya's M-01 task: URDF import + diagnostic walkthrough; importer + check_collision_mesh + diagnostic chain"
}

User: "Lisa: STEP end-of-arm tool clearance audit"
{
  "goal": "Import a STEP end-of-arm tool, attach to robot, run clearance verdict",
  "steps": [
    {"n": 1, "action": "Search for the STEP file locally", "expected_tool": "list_local_files", "post_condition": "STEP path identified or zero matches confirmed"},
    {"n": 2, "action": "If found, import the STEP geometry", "expected_tool": "add_reference", "post_condition": "/World/EndOfArm prim added"},
    {"n": 3, "action": "Verify the robot exists and is anchored", "expected_tool": "scene_summary", "post_condition": "robot articulation root confirmed"},
    {"n": 4, "action": "Run clearance check between robot and obstacles", "expected_tool": "check_path_clearance", "post_condition": "clearance distance values returned"},
    {"n": 5, "action": "Reply with the verdict", "expected_tool": "reply", "post_condition": "pass/fail + minimum distance reported"}
  ],
  "components": ["/World/EndOfArm", "robot articulation", "clearance verdict"],
  "success_criteria": [
    "STEP file found via search OR explicitly reported missing (no fabrication)",
    "Clearance verdict cites measured distances, not hand-wavy",
    "Reply mentions stow position assumption if pose was unknown"
  ],
  "reasoning": "L-01: discovery-first per asset_path_discovery cite, then clearance"
}
"""


async def generate_spec(message: str, provider) -> StructuredSpec | None:
    """
    Run a reasoning-LLM call to produce a structured execution plan.
    Returns None on any error (caller proceeds without spec — fail-open).

    Provider is the orchestrator's main LLM provider. The function reads
    SPEC_LLM_MODEL env var to pick a stronger reasoning model when available
    (e.g. gemini-pro for planning while Flash drives execution); falls back
    to the main provider if the env override isn't set or fails.
    """
    messages = [{"role": "user", "content": f'Plan: "{message}"'}]

    # Hybrid-model env-pattern: SPEC_LLM_MODEL=gemini-2.0-pro-exp lets ops point
    # the planner at a stronger model than the executor. Falls through to the
    # main provider if unset or the override fails.
    spec_model = os.environ.get("SPEC_LLM_MODEL", "").strip()

    try:
        kwargs = {"system_override": SPEC_GENERATOR_SYSTEM}
        if spec_model:
            kwargs["model_override"] = spec_model
        response = await provider.complete(messages, kwargs)
        raw = response.text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()

        parsed = json.loads(raw)
        steps_raw = parsed.get("steps", []) or []
        steps: list[SpecStep] = []
        for i, s in enumerate(steps_raw[:10], 1):
            steps.append({
                "n": int(s.get("n", i)),
                "action": str(s.get("action", "")).strip()[:200],
                "expected_tool": str(s.get("expected_tool", "")).strip()[:80],
                "post_condition": str(s.get("post_condition", "")).strip()[:200],
            })

        spec: StructuredSpec = {
            "goal": str(parsed.get("goal", ""))[:300],
            "steps": steps,
            "components": [str(c)[:120] for c in (parsed.get("components", []) or [])][:10],
            "success_criteria": [str(c)[:200] for c in (parsed.get("success_criteria", []) or [])][:10],
            "reasoning": str(parsed.get("reasoning", ""))[:200],
        }
        logger.info(
            f"[SpecGenerator] '{message[:50]}…' → {len(spec['steps'])} steps "
            f"(model={spec_model or 'default'})"
        )
        return spec

    except Exception as e:
        logger.warning(f"[SpecGenerator] failed ({e}), proceeding without spec")
        return None


def format_spec_as_checklist(spec: StructuredSpec, gap_report: dict | None = None) -> str:
    """
    Render the spec as a concise checklist that the executor LLM sees as part
    of its system instructions. Format chosen to be unambiguous about the
    required-tool obligation (the dominant fail mode at 40% was "skipped
    required tools"); mentions the tool by NAME under each step so the agent
    can't pick a generic alternative without flagging the substitution.
    """
    lines = [
        "## Required execution plan (CHECK EACH STEP BEFORE REPLYING)",
        "",
        f"**Goal:** {spec['goal']}",
        "",
        "**Steps — call each tool, then check the post-condition:**",
    ]
    for s in spec["steps"]:
        marker = ""
        if gap_report:
            tool = s["expected_tool"]
            if tool in (gap_report.get("matched") or []):
                marker = " ✓ tool registered"
            elif tool in (gap_report.get("partial") or {}):
                alt = gap_report["partial"][tool]
                marker = f" ⚠ closest: {alt}"
            elif tool in (gap_report.get("missing") or []):
                marker = " ✗ NOT in registry — improvise or report"
        lines.append(f"  {s['n']}. {s['action']}")
        lines.append(f"     → call **`{s['expected_tool']}`**{marker}")
        lines.append(f"     → after: {s['post_condition']}")
    if spec["components"]:
        lines.append("")
        lines.append(f"**Required components:** {', '.join(spec['components'])}")
    if spec["success_criteria"]:
        lines.append("")
        lines.append("**Success criteria — your reply must demonstrate:**")
        for c in spec["success_criteria"]:
            lines.append(f"  - {c}")
    lines.append("")
    lines.append(
        "Do NOT short-circuit. If a tool fails, report the failure honestly "
        "instead of skipping the step. If a tool is missing from the registry, "
        "explain what you would have called and why."
    )
    return "\n".join(lines)
