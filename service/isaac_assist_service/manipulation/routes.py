"""
routes.py — Manipulation Planning API
---------------------------------------
Implements the Pi0.5-compatible planning endpoints plus task management.

  POST /plan           — LLM builds a TaskSpec from goal + scene snapshot
  POST /replan         — LLM replans from current phase after a failure
  GET  /tasks/{id}     — poll Continuity Manager state
  POST /tasks/{id}/abort  — send SAFE_HOLD to CM
  POST /tasks/{id}/submit — forward an approved TaskSpec to CM for execution
  POST /telemetry      — ingest CM phase telemetry into the Knowledge Base
  GET  /skills         — list canonical skill registry
  GET  /policies       — list policies loaded in the Policy Bank
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..config import config
from ..knowledge.knowledge_base import KnowledgeBase
from .task_spec_schema import (
    PlanRequest,
    PhaseTelemetry,
    ReplanRequest,
    TaskSpec,
)
from .skill_registry import SKILL_NAMES, skills_for_prompt, is_valid_skill
from .client import ContinuityManagerClient, PolicyBankClient

router = APIRouter()
logger = logging.getLogger(__name__)
_kb = KnowledgeBase()

# Simple in-process plan cache: key → (timestamp, TaskSpec dict)
_plan_cache: Dict[str, tuple[float, Dict]] = {}
_CACHE_TTL = 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_key(goal_text: str, scene_snapshot: Optional[Any]) -> str:
    objects = []
    if scene_snapshot:
        objects = [o.model_dump() for o in scene_snapshot.objects]
    canonical = json.dumps({"goal": goal_text, "objects": objects}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _build_plan_prompt(goal_text: str, scene_snapshot: Optional[Any], prior_attempt: Optional[str]) -> str:
    embodiment_id = config.manipulation_embodiment_id
    scene_text = "No scene data available."
    if scene_snapshot and scene_snapshot.objects:
        obj_lines = []
        for o in scene_snapshot.objects:
            pos = o.pose.position
            obj_lines.append(
                f"  - {o.object_id} (class={o.cls}, "
                f"pos=[{pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}], conf={o.confidence:.2f})"
            )
        scene_text = "Objects in scene:\n" + "\n".join(obj_lines)

    prior_text = ""
    if prior_attempt:
        prior_text = f"\nPrior attempt failure: {prior_attempt}\n"

    return f"""You are a manipulation task planner for a bimanual robot with embodiment {embodiment_id}.
You receive a goal in natural language and a scene description.
You output a JSON Task Spec describing an ordered sequence of manipulation phases.

Hard rules:
- Use ONLY skills from this registry:
{skills_for_prompt()}

- Targets are in world frame (base_link), never joint space.
- Each phase has exactly one arm with role LEAD. The other arm is IDLE or ASSIST.
- Prefer single-arm decomposition. Use bimanual skills only when single-arm fails.
- Success and failure predicates must use observable predicate types.
- phase_index values must be contiguous starting at 0.
- Only reference object_ids that appear in the scene snapshot.

Output ONLY valid JSON matching this structure (no markdown, no explanation):
{{
  "spec_version": "1.0",
  "task_id": "<uuid>",
  "goal_text": "<the goal>",
  "embodiment_id": "{embodiment_id}",
  "scene_snapshot": {{
    "timestamp": <unix_ts>,
    "frame": "base_link",
    "objects": [ {{"object_id": "...", "class": "...", "pose": {{"position": [x,y,z], "quaternion": [0,0,0,1]}}, "bbox_3d": [w,d,h], "confidence": 0.9}} ]
  }},
  "phases": [
    {{
      "phase_index": 0,
      "skill_name": "<skill>",
      "hand_assignment": {{"right": "LEAD", "left": "IDLE"}},
      "semantic_target": {{ ... }},
      "constraints": {{"max_force_n": 15.0, "max_duration_s": 8.0}},
      "success_predicate": {{ ... }},
      "failure_predicate": {{ "type": "OR", "clauses": [{{"type": "duration_exceeded", "threshold_s": 10.0}}] }}
    }}
  ]
}}

{scene_text}
{prior_text}
Goal: {goal_text}"""


def _build_replan_prompt(req: ReplanRequest, scene_snapshot: Optional[Any]) -> str:
    scene_text = ""
    if scene_snapshot and scene_snapshot.objects:
        obj_lines = [
            f"  - {o.object_id} ({o.cls}) at {o.pose.position}"
            for o in scene_snapshot.objects
        ]
        scene_text = "Current scene:\n" + "\n".join(obj_lines)

    return f"""Replanning task {req.task_id} from phase {req.current_phase}.
Failure reason: {req.failure_reason}

{scene_text}

Generate a new TaskSpec with phases starting at index {req.current_phase}.
Apply the replanning_hints from the original plan.
Output ONLY valid JSON (no markdown).
Available skills:
{skills_for_prompt()}"""


def _validate_task_spec_json(raw: str, scene_snapshot: Optional[Any]) -> TaskSpec:
    """Strip fences, parse JSON, validate with Pydantic. Raises ValueError on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    data = json.loads(text)

    # Inject scene snapshot from request if LLM omitted or produced a stub
    if scene_snapshot and (
        not data.get("scene_snapshot", {}).get("objects")
    ):
        data["scene_snapshot"] = scene_snapshot.model_dump()

    return TaskSpec.model_validate(data)


async def _call_llm(prompt: str) -> str:
    from ..chat.provider_factory import get_llm_provider
    provider = get_llm_provider()
    response = await provider.complete(
        [{"role": "user", "content": prompt}],
        {"system_override": (
            "You are a robotic manipulation task planner. "
            "Output only valid JSON Task Specs. No commentary."
        )},
    )
    return response.text


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/plan")
async def plan_task(req: PlanRequest) -> Dict:
    """
    Generate a Task Spec from a goal + optional scene snapshot.

    If scene_snapshot is omitted the planner uses whatever objects were last
    detected (the caller should capture viewport first if possible).
    """
    cache_key = _cache_key(req.goal_text, req.scene_snapshot)
    now = time.time()
    if cache_key in _plan_cache and now - _plan_cache[cache_key][0] < _CACHE_TTL:
        logger.info("[manipulation/plan] cache hit for '%s'", req.goal_text[:60])
        return _plan_cache[cache_key][1]

    prompt = _build_plan_prompt(req.goal_text, req.scene_snapshot, req.prior_attempt)
    last_error: Optional[str] = None

    for attempt in range(3):
        try:
            if last_error:
                # Feed validation error back to the LLM for self-correction
                prompt += f"\n\nPrevious attempt was invalid: {last_error}\nPlease fix and output corrected JSON only."

            raw = await _call_llm(prompt)
            spec = _validate_task_spec_json(raw, req.scene_snapshot)

            # Validate all skills are known
            unknown = [p.skill_name for p in spec.phases if not is_valid_skill(p.skill_name)]
            if unknown:
                raise ValueError(f"Unknown skills in phases: {unknown}")

            result = spec.model_dump()
            _plan_cache[cache_key] = (now, result)
            logger.info(
                "[manipulation/plan] '%s' → %d phases (attempt %d)",
                req.goal_text[:60], len(spec.phases), attempt + 1,
            )
            return result

        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_error = str(e)
            logger.warning("[manipulation/plan] attempt %d failed: %s", attempt + 1, last_error)

    raise HTTPException(
        status_code=422,
        detail=f"Planner failed to produce a valid Task Spec after 3 attempts. Last error: {last_error}",
    )


@router.post("/replan")
async def replan_task(req: ReplanRequest) -> Dict:
    """
    Replan from the current phase after a failure.
    Bypasses the plan cache.
    """
    prompt = _build_replan_prompt(req, req.scene_snapshot)

    for attempt in range(2):
        try:
            raw = await _call_llm(prompt)
            spec = _validate_task_spec_json(raw, req.scene_snapshot)
            unknown = [p.skill_name for p in spec.phases if not is_valid_skill(p.skill_name)]
            if unknown:
                raise ValueError(f"Unknown skills: {unknown}")
            logger.info("[manipulation/replan] task %s → %d phases", req.task_id, len(spec.phases))
            return spec.model_dump()
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            prompt += f"\n\nPrevious attempt invalid: {e}\nFix and output JSON only."

    raise HTTPException(status_code=422, detail="Replanner failed to produce valid Task Spec")


@router.post("/tasks/{task_id}/submit")
async def submit_task(task_id: str, task_spec: Dict) -> Dict:
    """
    Forward an approved TaskSpec to the Continuity Manager for execution.
    Requires auto_approve=true or explicit user approval for safety.
    """
    if not config.auto_approve:
        raise HTTPException(
            status_code=403,
            detail="Task submission requires AUTO_APPROVE=true or explicit approval.",
        )

    cm = ContinuityManagerClient()
    if not await cm.is_healthy():
        raise HTTPException(status_code=503, detail="Continuity Manager is not reachable")

    result = await cm.submit_task(task_spec)
    if result is None:
        raise HTTPException(status_code=502, detail="Continuity Manager rejected the Task Spec")
    return result


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str) -> Dict:
    """Poll the Continuity Manager for the current task state."""
    cm = ContinuityManagerClient()
    status = await cm.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or CM unreachable")
    return status


@router.post("/tasks/{task_id}/abort")
async def abort_task(task_id: str) -> Dict:
    """Transition the task to SAFE_HOLD via the Continuity Manager."""
    cm = ContinuityManagerClient()
    result = await cm.abort_task(task_id)
    if result is None:
        raise HTTPException(status_code=502, detail="Abort request failed")
    return result


@router.post("/telemetry")
async def ingest_telemetry(record: PhaseTelemetry) -> Dict:
    """
    Ingest a phase outcome record from the Continuity Manager.

    Stores in the Knowledge Base so the planner learns from execution results.
    Negative outcomes become KB negative patterns.
    """
    instruction = (
        f"Manipulation phase {record.phase_index} ({record.skill_name}) "
        f"for goal: {record.goal_text}"
    )
    response = json.dumps({
        "skill_name": record.skill_name,
        "outcome": record.outcome,
        "duration_s": record.duration_s,
        "ft_peak_n": record.ft_peak_n,
    })

    _kb.add_entry(
        version=config.manipulation_embodiment_id,
        instruction=instruction,
        response=response,
        source="auto_success_learning" if record.outcome == "success" else "auto_error_learning",
    )

    if record.outcome != "success" and record.failure_reason:
        try:
            _kb.add_negative_pattern(
                version=config.manipulation_embodiment_id,
                error_signature=f"{record.skill_name}:{record.outcome}:{record.failure_reason[:80]}",
                failing_code=f"skill={record.skill_name} in task {record.task_id}",
                root_cause=record.failure_reason,
                fix_applied="",
            )
        except Exception:
            pass  # KB negative patterns are best-effort

    logger.info(
        "[manipulation/telemetry] task=%s phase=%d skill=%s outcome=%s",
        record.task_id, record.phase_index, record.skill_name, record.outcome,
    )
    return {"stored": True, "outcome": record.outcome}


@router.get("/skills")
async def list_skills() -> Dict:
    """Return the canonical skill registry."""
    from .skill_registry import SKILL_REGISTRY
    return {
        "skills": [
            {
                "name": spec.name,
                "arm_mode": spec.arm_mode,
                "description": spec.description,
                "assist_role": spec.assist_role,
                "sim_gpu_hr": spec.sim_gpu_hr,
                "real_demos": spec.real_demos,
            }
            for spec in SKILL_REGISTRY.values()
        ],
        "count": len(SKILL_REGISTRY),
    }


@router.get("/policies")
async def list_policies() -> Dict:
    """Return policies currently loaded in the Policy Bank."""
    pb = PolicyBankClient()
    if not await pb.is_healthy():
        return {"policies": [], "policy_bank_reachable": False}
    policies = await pb.list_policies()
    return {"policies": policies, "policy_bank_reachable": True}
