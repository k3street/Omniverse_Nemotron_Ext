"""
negotiator.py
-------------
For prompts classified `complexity == "complex"`, runs a SECOND fast-LLM call
BEFORE the tool-loop to detect missing required information. If something
critical is missing, the agent's first reply asks the user for it instead
of guessing or stopping silently.

Why this exists
---------------
Empirical evidence from 2026-05-04 broad-persona canary (51 tasks across
15 personas, 9/51 = 18% direct-mode pass rate vs 30/31 = 97% on
bread-and-butter): the dominant failure mode on HARD tasks is
"agent stops to ask for input" or "agent silently skips the ask and
hallucinates" — exactly the negotiation gap.

What it is, what it isn't
-------------------------
This is a thin "ask-for-missing-params" gate. It is:
- LLM-based (the same fast classifier-LLM, NOT a slow planner)
- Fail-open (any error → proceed without negotiation, no regression)
- Domain-agnostic (no hardcoded param schemas; the LLM judges from text)
- Single-turn (returns a list of questions; doesn't loop)

It is NOT a full spec_generator. There is no plan output, no post-conditions,
no gap-analyzer over the tool catalog. Those belong to a later phase. This
gate only addresses the symptom that surfaced in canary data: the agent
attempts complex work without confirming it has the inputs it needs.

How it integrates with template_retriever
------------------------------------------
The previous spec-first attempt (b93bcca, reverted d61f76c on 2026-04-19)
collided with template_retriever — both layers wrote competing structured
content into rag_text and the agent picked-mixed. This negotiator avoids
that failure mode by:
  1. Running BEFORE template_retriever (intercepts the turn entirely)
  2. Returning a reply that asks the user — no rag_text injection
  3. Stopping the turn at "questions asked" — template_retriever runs
     next turn when params are filled in
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class NegotiationResult(TypedDict):
    needs_clarification: bool
    questions: list[str]   # one short question per missing piece, agent-ready
    reasoning: str         # one-line explanation, for telemetry / debug


NEGOTIATOR_SYSTEM = """You are an INTENT clarification gate for an AI assistant in NVIDIA Isaac Sim.

The user request has been classified as COMPLEX (multi-component scene, domain-
specific work, or pedagogical walkthrough).

Your job: decide if the user's INTENT is genuinely ambiguous in a way that
would lead the assistant to do the wrong thing. If so, ask short questions
to disambiguate. Otherwise, let the assistant proceed.

DO ask when intent is unclear:
- Scope is unbounded: "Set up an RL training scene" — for what robot? what task?
- Ambiguous noun: "a robot" or "a sensor" without context — only ask if the
  default would meaningfully change the outcome
- Conflicting framings: "build a small factory" — small how, by what dimension?
- Open-ended pedagogy: "explain this for someone new" — to what depth?

DO NOT ask for plumbing inputs the assistant can fetch with its own tools:
- File paths (URDF, USD, STEP, IFC) — the assistant has list_files,
  catalog_search, nucleus_browse, find_prims_by_name. It should TRY to find
  the file first and only ask if discovery fails. Never ask for paths upfront.
- Default poses, joint configurations, brand-standard params — these are in
  knowledge bases the assistant can consult.
- Things present in the current stage — scene_summary will surface them.
- Things template_retriever or knowledge KB will provide.

DO NOT ask on inspection / audit / diagnosis prompts. When the user asks the
assistant to LOOK AT existing state and report (verbs: inspect, review,
audit, diagnose, analyze, find issues, suggest improvements, summarize,
what's wrong with…), the right response is to call scene_summary /
list_all_prims / check_physics_health and answer based on what's there. The
assistant has no reason to ask "what should I focus on?" — the answer is
"everything that's broken or missing." Asking instead of inspecting wastes
the user's turn and drops the task.

Posing-rule: when you DO ask, frame it as INTENT-disambiguation, not data-fetch.
- ❌ "What is the file path to the STEP file?"
- ✅ "Should I use the local workspace assets, or are you bringing your own?"
- ❌ "What are the joint angles for the stow position?"
- ✅ (don't ask — UR10e stow pose is documented; assistant can look it up)

Reply with ONLY valid JSON:
{
  "needs_clarification": <true|false>,
  "questions": ["question 1", "question 2"],   // empty list if not needed
  "reasoning": "one-line explanation"
}

Maximum 2 questions. SHORT. Never ask philosophical or open-ended questions.

Examples:

User: "Import this UR10 STEP file and verify clearance to the cabinet"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "intent clear: import STEP + clearance check; assistant should
                 discover STEP path via list_files / catalog_search and ask
                 only if discovery fails (that's a tool concern, not intent)"}

User: "Set up an RL training scene"
→ {"needs_clarification": true,
   "questions": ["Which task — locomotion, manipulation, or navigation?",
                 "Which robot — Franka, Nova Carter, G1, or do you want a default?"],
   "reasoning": "RL scope is unbounded; robot + task choice changes everything"}

User: "Build a pick-and-place cell with conveyor, Franka, sensor-gated bin"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "components named, defaults reasonable, no intent ambiguity"}

User: "First-lecture hello-robot demo for undergrads"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "pedagogical default scene with commentary is the obvious shape"}

User: "Build a small factory"
→ {"needs_clarification": true,
   "questions": ["What does the factory produce, roughly? (decides the cell layout)",
                 "Do you have site dimensions in mind, or should I propose?"],
   "reasoning": "'small factory' product + scale undecided; default would arbitrary"}

User: "Set up ROS2 bridge for Nav2"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "specific task, established defaults; assistant can proceed"}

User: "Agent inspects an existing partial scene, identifies issues, and suggests prioritized improvements"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "inspection task — call scene_summary/list_all_prims and report what's broken; nothing to ask"}

User: "Review my scene and tell me what's missing for RL training"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "audit prompt — discover via tools, don't ask"}
"""


async def negotiate(message: str, provider) -> NegotiationResult:
    """
    Run a single fast-LLM clarification check on a complex prompt.

    Fail-open: any error returns needs_clarification=False so the orchestrator
    proceeds normally. The cost of a missed clarification (extra failure)
    is much smaller than the cost of a falsely-emitted clarification
    (annoys the user and breaks trust).
    """
    messages = [{"role": "user", "content": f'Classify: "{message}"'}]

    try:
        response = await provider.complete(messages, {"system_override": NEGOTIATOR_SYSTEM})
        raw = response.text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        needs = bool(parsed.get("needs_clarification", False))
        questions = parsed.get("questions", []) or []
        reasoning = str(parsed.get("reasoning", ""))[:200]

        if not isinstance(questions, list):
            questions = []
        questions = [str(q).strip() for q in questions if str(q).strip()][:3]

        # If LLM said "needs" but provided no questions, that's contradictory —
        # treat as not-needed rather than block silently.
        if needs and not questions:
            needs = False

        logger.info(
            f"[Negotiator] '{message[:50]}…' → needs={needs} "
            f"q={len(questions)} reason={reasoning[:80]}"
        )

        return {
            "needs_clarification": needs,
            "questions": questions,
            "reasoning": reasoning,
        }

    except Exception as e:
        logger.warning(f"[Negotiator] failed ({e}), proceeding without clarification")
        return {
            "needs_clarification": False,
            "questions": [],
            "reasoning": f"negotiator error: {e}",
        }


def format_clarification_reply(result: NegotiationResult, original_message: str) -> str:
    """
    Build the user-facing reply when negotiation requests clarification.
    Kept short and direct — no preamble, no apologies.
    """
    if not result["questions"]:
        return ""

    lines = ["Before I start, I need a few things:"]
    for q in result["questions"]:
        lines.append(f"- {q}")
    lines.append("")
    lines.append("Once you confirm, I'll continue with the original request.")
    return "\n".join(lines)
