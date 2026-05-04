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


NEGOTIATOR_SYSTEM = """You are a clarification gate for an AI assistant in NVIDIA Isaac Sim.

The user request has been classified as COMPLEX (multi-component scene, domain-
specific work, or pedagogical walkthrough).

Your single job: decide if the request is missing **required** inputs that the
assistant cannot reasonably guess. If so, list short specific questions for the
user. If the request is self-sufficient, say so and let the assistant proceed.

REQUIRED inputs include:
- File paths the assistant must consume (URDF, USD, STEP, IFC) when the user
  says "import this" / "use my asset" but provides no path
- Asset choice when the user says "a robot" or "a sensor" without specifying
  brand/model (Franka, UR10e, RealSense, etc) AND no scene-state hint exists
- Geometry/scale parameters for "build a factory" / "lay out a cell" prompts
  (area, product, volume) when fully unspecified
- Regulatory context for safety/refusal tasks ("certify this for ISO/TS 15066")
  when the standards version is not given

NOT required (don't ask):
- Things the assistant can reasonably default (lighting, camera placement,
  default Franka if asked for "a robot arm" in casual context)
- Information already in the prompt
- Things that template_retriever or scene_summary will surface
- Cosmetic preferences

Reply with ONLY valid JSON:
{
  "needs_clarification": <true|false>,
  "questions": ["question 1", "question 2"],   // empty list if not needed
  "reasoning": "one-line explanation"
}

Keep questions SHORT and concrete. Maximum 3 questions. Never ask philosophical
or open-ended questions ("what are your goals?"). Ask for specific identifiers,
paths, or numeric values.

Examples:

User: "Import this UR10 and verify clearance to the cabinet"
→ {"needs_clarification": true,
   "questions": ["Which UR10 source? Path to URDF/USD, or use the default Nucleus UR10e asset?",
                 "Where is the cabinet — already in the scene, or do I create one?"],
   "reasoning": "import target ambiguous; cabinet existence unknown"}

User: "Build a pick-and-place cell with conveyor, Franka, sensor-gated bin"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "all components named; defaults are reasonable"}

User: "Set up an RL training scene"
→ {"needs_clarification": true,
   "questions": ["Which robot? (Franka, Nova Carter, G1, custom)",
                 "What task — locomotion, manipulation, navigation?",
                 "Any specific obstacles or environment style?"],
   "reasoning": "RL training scope is unbounded without robot+task"}

User: "First-lecture hello-robot demo for undergrads"
→ {"needs_clarification": false,
   "questions": [],
   "reasoning": "pedagogical default scene + commentary is reasonable"}
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
