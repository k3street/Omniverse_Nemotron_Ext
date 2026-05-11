"""
intent_router.py
-----------------
Single-LLM-call classifier that maps a user message to one of 8 intents
AND flags whether the request is multi-step (requires planning before
execution). The multi-step flag drives the orchestrator's read-only tool
gate on round 0 — see orchestrator.py `_multi_step` usage.

Classifying multi-step via LLM (rather than regex on "sen"/"then"/"tills")
was added 2026-04-19 because the regex missed phrasings without linking
words — e.g. "create a conveyor, scale cubes, start simulation" has three
actions but no keyword the regex detects. LLM semantic classification
handles this for free in the same round-trip.
"""
from __future__ import annotations
import logging
import json
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

Intent = Literal[
    "general_query",
    "scene_diagnose",
    "vision_inspect",
    "prim_inspect",
    "patch_request",
    "physics_query",
    "console_review",
    "navigation",
    "manipulation_task",
]


Complexity = Literal["single", "multi", "complex"]


class IntentClassification(TypedDict):
    intent: Intent
    multi_step: bool
    complexity: Complexity
    confidence: float


INTENT_SYSTEM = """You are an intent classifier for an AI assistant embedded in NVIDIA Isaac Sim.

Classify the user message on THREE axes:

1. intent — exactly ONE of:
- general_query     : General robotics / USD / Omniverse questions that don't need live scene data
- scene_diagnose    : Asks why something is wrong with the scene, robot, or simulation behaviour
- vision_inspect    : Wants to see the viewport or asks what the AI can see visually
- prim_inspect      : Asks about a specific prim's properties, attributes, transforms
- patch_request     : Wants to fix, change, or apply a modification to the scene/code
- physics_query     : Asks about joint states, forces, velocities, rigid body data
- console_review    : Asks about errors, warnings, or logs in the console
- navigation        : Wants to select, move, focus on, or navigate to a prim
- manipulation_task : Wants to pick, place, grasp, fold, or execute a physical manipulation task on the robot

2. multi_step — true if the request has ≥2 linked actions with dependencies,
   conditionals ("if X exists"), sequencing ("then X, then Y"), or termination
   conditions ("run until all fall off"). false if it's a single discrete action
   or a pure question.

3. complexity — exactly ONE of:
- "single"  : One discrete action with a single tool call, no dependencies, or a
              pure question. Example: "place a cube at (1,2,3)" or "what is USD?"
- "multi"   : 2-4 chained actions with simple sequential dependencies. No formal
              spec needed; step-by-step tool calls suffice. Example: "add a cube
              then scale it" or "create conveyor, place cubes, run sim".
- "complex" : Industrial-realistic composition with 5+ components, multiple
              subsystems (physics + robot + sensor + I/O), domain-specific
              vocabulary (CATIA, ROS2 bridge, RL training, IFC), or pedagogical
              walkthroughs. Likely needs negotiation for missing inputs and a
              structured plan before tool execution.
              Examples:
                "Build a pick-and-place cell with conveyor, Franka, sensor, bin"
                "Set up RL training environment for Nova Carter navigation"
                "Import this STEP end-of-arm tool and run a clearance audit"
                "First-lecture hello-robot demo for undergrads"

Reply with ONLY valid JSON:
{"intent": "<one>", "multi_step": <bool>, "complexity": "<single|multi|complex>", "confidence": 0.0-1.0}
"""

INTENT_EXAMPLES = [
    # (user_message, intent, multi_step, complexity)
    ("what is a USD prim?", "general_query", False, "single"),
    ("why is my robot floating above the ground?", "scene_diagnose", False, "single"),
    ("show me the viewport", "vision_inspect", False, "single"),
    ("fix the joint damping", "patch_request", False, "single"),
    ("add a cube at (1, 2, 3)", "patch_request", False, "single"),
    ("create a conveyor, scale the cubes to fit, run it until they fall off",
     "patch_request", True, "multi"),
    ("if a conveyor tool exists use it, then place cubes on top",
     "patch_request", True, "multi"),
    ("add 4 cubes, then stack them, then add a light above them",
     "patch_request", True, "multi"),
    ("Build a pick-and-place cell: Franka on a table, conveyor, 4 cubes, sensor-gated bin",
     "patch_request", True, "complex"),
    ("Set up an RL training scene for Nova Carter with randomised obstacles",
     "patch_request", True, "complex"),
    ("Import the UR10 from this STEP file and verify clearance to the cabinet",
     "patch_request", True, "complex"),
    ("any errors in the console?", "console_review", False, "single"),
    ("select the robot arm", "navigation", False, "single"),
    ("pick up the cube and place it on the table", "manipulation_task", True, "multi"),
    ("grasp the red block", "manipulation_task", False, "single"),
]


async def classify_intent(message: str, provider) -> IntentClassification:
    """
    Calls the LLM with a minimal few-shot prompt to classify intent AND
    multi-step status. Falls back to general_query + multi_step=False on
    any error. Returns a TypedDict, not a bare string — callers should
    read the `intent` and `multi_step` fields explicitly.
    """
    few_shot = "\n".join(
        f'User: "{u}" → {{"intent": "{i}", "multi_step": {str(ms).lower()}, "complexity": "{c}"}}'
        for u, i, ms, c in INTENT_EXAMPLES[:10]
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"Examples:\n{few_shot}\n\n"
                f'Now classify: "{message}"\n'
                f"Reply with JSON only."
            ),
        }
    ]

    # Pass the classifier system prompt per-call via context, never mutate the
    # shared provider — concurrent chat requests caused intent JSON to leak
    # into user-facing replies (race on the instance attribute).
    try:
        response = await provider.complete(messages, {"system_override": INTENT_SYSTEM})
        raw = response.text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        intent = parsed.get("intent", "general_query")
        multi_step = bool(parsed.get("multi_step", False))
        # complexity defaults to "multi" if multi_step else "single" — handles
        # older classifier responses that don't include the field
        default_complexity = "multi" if multi_step else "single"
        complexity = parsed.get("complexity", default_complexity)
        if complexity not in ("single", "multi", "complex"):
            complexity = default_complexity
        confidence = float(parsed.get("confidence", 0.5))
        logger.info(
            f"[IntentRouter] '{message[:60]}' → {intent} "
            f"(multi_step={multi_step}, complexity={complexity}, conf={confidence:.2f})"
        )
        return {
            "intent": intent,
            "multi_step": multi_step,
            "complexity": complexity,
            "confidence": confidence,
        }  # type: ignore

    except Exception as e:
        logger.warning(f"[IntentRouter] Classification failed ({e}), defaulting to general_query")
        return {
            "intent": "general_query",
            "multi_step": False,
            "complexity": "single",
            "confidence": 0.0,
        }
