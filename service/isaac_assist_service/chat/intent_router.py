"""
intent_router.py
-----------------
Single-LLM-call classifier that maps a user message to one of 8 intents.
Uses a fast, cheap model call (can be overridden to run a local classifier).
"""
from __future__ import annotations
import logging
import json
from typing import Literal

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
]

INTENT_SYSTEM = """You are an intent classifier for an AI assistant embedded in NVIDIA Isaac Sim.

Classify the user message into EXACTLY ONE of these intents:
- general_query     : General robotics / USD / Omniverse questions that don't need live scene data
- scene_diagnose    : Asks why something is wrong with the scene, robot, or simulation behaviour
- vision_inspect    : Wants to see the viewport or asks what the AI can see visually
- prim_inspect      : Asks about a specific prim's properties, attributes, transforms
- patch_request     : Wants to fix, change, or apply a modification to the scene/code
- physics_query     : Asks about joint states, forces, velocities, rigid body data
- console_review    : Asks about errors, warnings, or logs in the console
- navigation        : Wants to select, move, focus on, or navigate to a prim

Reply with ONLY valid JSON: {"intent": "<one of the above>", "confidence": 0.0-1.0}
"""

INTENT_EXAMPLES = [
    ("what is a USD prim?", "general_query"),
    ("why is my robot floating above the ground?", "scene_diagnose"),
    ("what do you see right now?", "vision_inspect"),
    ("show me the viewport", "vision_inspect"),
    ("what are the properties of /World/Robot", "prim_inspect"),
    ("fix the joint damping", "patch_request"),
    ("apply a gravity correction", "patch_request"),
    ("launch rviz", "patch_request"),
    ("start rviz2 with camera topics", "patch_request"),
    ("what are the joint velocities?", "physics_query"),
    ("any errors in the console?", "console_review"),
    ("select the robot arm", "navigation"),
    ("go to /World/Cube", "navigation"),
]


async def classify_intent(message: str, provider) -> Intent:
    """
    Calls the LLM with a minimal few-shot prompt to classify the intent.
    Falls back to 'general_query' on any error.
    """
    few_shot = "\n".join(
        f'User: "{u}" → {{"intent": "{i}"}}'
        for u, i in INTENT_EXAMPLES[:6]  # keep it short
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

    # Override the system prompt just for this call
    original_system = getattr(provider, "_system_override", None)
    try:
        # Temporarily inject classifier system prompt
        provider._system_override = INTENT_SYSTEM
        response = await provider.complete(messages, {})
        raw = response.text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        intent = parsed.get("intent", "general_query")
        logger.info(f"[IntentRouter] '{message[:60]}' → {intent} ({parsed.get('confidence', '?')})")
        return intent  # type: ignore

    except Exception as e:
        logger.warning(f"[IntentRouter] Classification failed ({e}), defaulting to general_query")
        return "general_query"
    finally:
        if original_system is None and hasattr(provider, "_system_override"):
            del provider._system_override
        elif original_system is not None:
            provider._system_override = original_system
