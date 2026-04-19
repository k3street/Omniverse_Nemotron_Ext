"""Structured-spec generation for complex user requests.

When intent_router classifies a request as complexity="complex" (industrial
cells, RL environments, digital twins, multi-subsystem scenes), the
orchestrator calls this module to produce a machine-readable spec BEFORE
the tool-loop runs. The spec is then passed to gap_analyzer.py which
compares each step's expected_tool against the registered CODE_GEN_HANDLERS
and DATA_HANDLERS. Gaps surface as trace events (missing tools) and as
context for the agent.

Model choice (hybrid pattern):
  Spec generation is a REASONING task; tool-loop is an EXECUTION task.
  Different tasks benefit from different models — Cursor uses Claude
  for planning and GPT-4 for edits; we follow the same pattern.

  - SPEC_LLM_MODE=<anthropic|cloud|openai|grok> and SPEC_LLM_MODEL=<model_id>
    env vars configure a DEDICATED spec-generator provider (e.g. Claude
    Opus for reasoning).
  - If unset, falls back to the orchestrator's main provider (typically
    the fast Flash-tier model — suboptimal for spec quality but works).

Design:
- Separate LLM call, no tools exposed → cheap + fast, forced to emit JSON
- Few-shot with one conveyor_pick_place example so the model learns the shape
- Parse failure returns parse_ok=False; orchestrator falls through to
  normal path without breaking the turn

The spec format is intentionally simple (no nested plans, no branching).
A "complex" task has a flat list of steps; more elaborate composition can
come later if real usage shows the need.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_spec_llm_provider(fallback_provider: Any = None) -> Any:
    """Return the LLM provider dedicated to spec generation.

    Resolution order:
      1. SPEC_LLM_MODE + SPEC_LLM_MODEL env vars → dedicated provider
         (recommended: SPEC_LLM_MODE=anthropic, SPEC_LLM_MODEL=claude-opus-4-7)
      2. fallback_provider (usually the orchestrator's main LLM)

    Never raises — if the dedicated provider fails to construct, logs a
    warning and returns the fallback. Spec generation must never block
    the normal turn path.
    """
    mode = os.environ.get("SPEC_LLM_MODE")
    model = os.environ.get("SPEC_LLM_MODEL")
    if not mode or not model:
        return fallback_provider

    try:
        if mode == "anthropic":
            from .llm_anthropic import AnthropicProvider
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                logger.warning("[spec_generator] SPEC_LLM_MODE=anthropic but ANTHROPIC_API_KEY missing; using fallback")
                return fallback_provider
            return AnthropicProvider(api_key=key, model=model)
        if mode == "cloud":
            from .llm_gemini import GeminiProvider
            key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("API_KEY_GEMINI", "")
            if not key:
                return fallback_provider
            return GeminiProvider(api_key=key, model=model)
        if mode == "openai":
            from .llm_openai_compat import OpenAICompatProvider, PROVIDER_URLS
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                return fallback_provider
            return OpenAICompatProvider(api_key=key, model=model, base_url=PROVIDER_URLS["openai"])
        logger.warning(f"[spec_generator] unknown SPEC_LLM_MODE={mode!r}; using fallback")
        return fallback_provider
    except Exception as e:
        logger.warning(f"[spec_generator] dedicated provider init failed ({e}); using fallback")
        return fallback_provider


@dataclass
class SpecStep:
    """One step in a structured spec.

    ``expected_tool`` is the NAME the agent should call to author this
    step; it may or may not exist in the current tool catalog —
    gap_analyzer decides. ``post_condition`` is the human-readable +
    ideally machine-checkable state after the step completes.
    """
    n: int
    intent: str
    expected_tool: Optional[str] = None
    post_condition: str = ""


@dataclass
class StructuredSpec:
    goal: str
    steps: List[SpecStep] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    raw_response: str = ""
    parse_ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {"n": s.n, "intent": s.intent,
                 "expected_tool": s.expected_tool,
                 "post_condition": s.post_condition}
                for s in self.steps
            ],
            "components": list(self.components),
            "success_criteria": list(self.success_criteria),
            "parse_ok": self.parse_ok,
        }


SPEC_SYSTEM = """You are a spec generator for NVIDIA Isaac Sim scene setups.

Given a user request classified as complex (multi-subsystem, industrial, or
RL-scale), produce a FLAT structured plan as JSON. Each step names an
intent, the tool that should author it, and a verifiable post-condition.

Rules:
- 4-10 steps. Do NOT include "press Play" / "look at scene" — those are user actions.
- expected_tool is the NAME (snake_case) of an Isaac Assist tool likely to
  fit. You don't need to verify it exists — gap_analyzer does that.
- post_condition must be VERIFIABLE — mention prim paths, APIs, attribute
  values, or counts where possible. Avoid vague "X is set up correctly".
- components lists the key prim paths that will exist after all steps run.
- success_criteria are the top-level checks a user would run to know the
  whole scene works (usually 2-5 items).

Schema (output ONLY this JSON, nothing else):
{
  "goal": "one-sentence restatement",
  "steps": [
    {"n": 1, "intent": "...", "expected_tool": "...", "post_condition": "..."},
    ...
  ],
  "components": ["/World/Table", "/World/Conveyor", ...],
  "success_criteria": ["criterion 1", "criterion 2", ...]
}
"""


SPEC_FEW_SHOT = """
Example — user request: "Pick-and-place cell on a table: conveyor moving
cubes to a Franka arm, which picks each and drops into an open-top bin."

Output:
{
  "goal": "Set up an industrial pick-and-place cell with a moving conveyor delivering cubes to a Franka Panda, which picks and drops each cube into an open-top bin on the table.",
  "steps": [
    {"n": 1, "intent": "Create a table as the cell base",
     "expected_tool": "create_prim",
     "post_condition": "/World/Table exists, type Cube, top surface Z between 0.70 and 0.80"},
    {"n": 2, "intent": "Create a moving conveyor on the table",
     "expected_tool": "create_conveyor",
     "post_condition": "/World/Conveyor has PhysicsCollisionAPI + kinematic RigidBodyAPI + PhysxSurfaceVelocityAPI with non-zero velocity"},
    {"n": 3, "intent": "Spawn 4 small cubes on the conveyor",
     "expected_tool": "run_usd_script",
     "post_condition": "4 Cube prims with size ≤ 0.08m, RigidBodyAPI + CollisionAPI + MassAPI, world bbox bottom within ±0.01m of conveyor top"},
    {"n": 4, "intent": "Import the Franka Panda robot on the table",
     "expected_tool": "robot_wizard",
     "post_condition": "/World/Robot has ArticulationRootAPI + ≥10 descendant link prims, base Z between 0.65 and 0.85"},
    {"n": 5, "intent": "Add a proximity sensor at the pick station",
     "expected_tool": "add_proximity_sensor",
     "post_condition": "Sensor prim at pick world-position with PhysxTriggerAPI and isaac_sensor:triggered attribute"},
    {"n": 6, "intent": "Create open-top bin on table",
     "expected_tool": "run_usd_script",
     "post_condition": "/World/Bin Xform with ≥5 child Cube prims, each with CollisionAPI, forming floor+4 walls"},
    {"n": 7, "intent": "Teach the robot's pick/drop/home poses",
     "expected_tool": "teach_robot_pose",
     "post_condition": "pose JSONs exist at workspace/robot_poses/World_Robot/{pick,drop,home}.json"},
    {"n": 8, "intent": "Install the sensor-gated pick-place controller",
     "expected_tool": "setup_pick_place_controller",
     "post_condition": "Physics callback 'pick_place_sensor_gated' registered; belt pauses on sensor trigger"}
  ],
  "components": ["/World/Table", "/World/Conveyor", "/World/Cube_0..3", "/World/Robot", "/World/PickSensor", "/World/Bin"],
  "success_criteria": [
    "Belt moves cubes toward the pick station while idle (sim playing)",
    "Sensor triggers and belt pauses when a cube enters the pick zone",
    "Robot executes pick pose, closes gripper, lifts, moves to drop pose, releases, returns home",
    "At least one cube ends up inside the bin bbox after the sequence"
  ]
}
""".strip()


async def generate_spec(
    user_message: str,
    available_tool_names: Optional[List[str]] = None,
    llm_provider: Any = None,
) -> StructuredSpec:
    """Produce a structured plan for a complex user request.

    ``available_tool_names`` is the list of registered tool handler names
    (CODE_GEN_HANDLERS + DATA_HANDLERS keys). Pass it in so the generator
    can bias toward real tools; omit to let the generator suggest freely
    (gap_analyzer will catch missing ones downstream).

    Returns a StructuredSpec even on parse failure — parse_ok=False flags
    that the raw_response couldn't be interpreted, so the orchestrator
    can fall through to the normal path without breaking the turn.
    """
    if llm_provider is None:
        return StructuredSpec(goal="", parse_ok=False, raw_response="no llm provider")

    tool_hint = ""
    if available_tool_names:
        # Trim to a manageable list; the LLM doesn't need all ~400
        tool_hint = (
            "\n\nAvailable tool names (pick from these when possible):\n"
            + ", ".join(sorted(set(available_tool_names))[:200])
        )

    messages = [
        {
            "role": "user",
            "content": (
                f"{SPEC_FEW_SHOT}\n\n"
                f"Now produce the spec JSON for this user request:\n"
                f'"{user_message}"{tool_hint}\n\n'
                f"Output JSON only."
            ),
        }
    ]

    try:
        response = await llm_provider.complete(
            messages, {"system_override": SPEC_SYSTEM}
        )
        raw = (response.text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        return _parse_spec(raw)
    except Exception as e:
        logger.warning(f"[spec_generator] LLM call failed: {e}")
        return StructuredSpec(goal="", parse_ok=False, raw_response=str(e))


def _parse_spec(raw: str) -> StructuredSpec:
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"[spec_generator] JSON parse failed: {e}")
        return StructuredSpec(goal="", parse_ok=False, raw_response=raw)

    if not isinstance(data, dict):
        return StructuredSpec(goal="", parse_ok=False, raw_response=raw)

    steps: List[SpecStep] = []
    for s in data.get("steps") or []:
        if not isinstance(s, dict):
            continue
        try:
            steps.append(SpecStep(
                n=int(s.get("n", len(steps) + 1)),
                intent=str(s.get("intent") or "").strip(),
                expected_tool=str(s.get("expected_tool") or "").strip() or None,
                post_condition=str(s.get("post_condition") or "").strip(),
            ))
        except Exception:
            continue

    return StructuredSpec(
        goal=str(data.get("goal") or "").strip(),
        steps=steps,
        components=[str(c) for c in (data.get("components") or []) if isinstance(c, (str,))],
        success_criteria=[str(c) for c in (data.get("success_criteria") or []) if isinstance(c, (str,))],
        raw_response=raw,
        parse_ok=True,
    )
