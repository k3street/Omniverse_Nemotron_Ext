# 03 — Pi0.5 Planner Service

A localhost HTTP service that wraps Pi0.5 (or any VLA fitting the same I/O contract). Stateless across requests except for an in-memory cache keyed by `(goal_text, scene_hash)`.

## Endpoints

```
POST /plan
  body:  { goal_text, scene_snapshot, embodiment_id, prior_attempt? }
  resp:  TaskSpec  (see 02)

POST /replan
  body:  { task_id, current_phase, failure_reason, scene_snapshot }
  resp:  TaskSpec  (with phases starting from current_phase)

GET /healthz
```

## Why a service, not in-process

- Pi0.5 holds 5–15 GB of GPU. Restarting the Continuity Manager shouldn't reload the model.
- Lets you swap Pi0.5 for GR00T or a fine-tuned variant by changing only this service.
- Crash isolation: a malformed VLA output that throws an exception doesn't take down the control stack.

## Prompt Construction

Pi0.5 (and most VLAs in this class) takes RGB(-D) + language. We need it to emit structured JSON conforming to `02_task_spec_protocol.md`. Two strategies, used together:

1. **In-context schema demonstration** — system prompt includes the schema and 2–3 worked examples.
2. **Constrained decoding** — if your Pi0.5 wrapper supports it, use a JSON schema constraint. If not, validate + retry up to 2x with the validation error fed back.

### System Prompt Skeleton

```
You are a manipulation task planner for a bimanual robot with embodiment {embodiment_id}.
You receive an RGB-D image of the workspace and a goal in natural language.
You output a JSON Task Spec describing an ordered sequence of phases.

Hard rules:
- Use only skills from the registry: {SKILL_REGISTRY_LIST}.
- Targets are in world frame, never joint space.
- Each phase has exactly one LEAD arm. Other arm is IDLE or ASSIST.
- Prefer single-arm decomposition. Use bimanual skills only when single-arm decomposition fails.
- Success and failure predicates must be observable.

Schema:
{TASK_SPEC_JSON_SCHEMA}

Examples:
{TWO_OR_THREE_EXAMPLES}

Goal: {goal_text}
Prior attempt failure (if any): {prior_attempt}
Scene objects: {scene_snapshot}
```

The scene snapshot is built by the Observation Pipeline (06) — Pi0.5 should not be re-running its own object detector if your perception stack already produces poses.

## Output Validation

Pipeline:
```
raw_output → strip_markdown_fences → json.loads → schema_validate → 
semantic_validate → return | retry_with_error_fed_back
```

`semantic_validate` checks:
- All `object_id`s referenced in phases exist in `scene_snapshot.objects`.
- All `skill_name`s are in the registry.
- Phase indices are contiguous starting at 0.
- Success predicates reference predicate types in the registry.
- `embodiment_id` matches the request.

## Caching

```python
cache_key = sha256(goal_text + canonical_json(scene_snapshot.objects))
# Cache TTL: 60 s. Scene changes invalidate.
```

Replanning bypasses cache.

## Failure Modes

| Mode | Detection | Response |
|---|---|---|
| VLA timeout | 5 s wall clock | Return 504; Continuity Manager escalates to operator |
| Schema fail x2 | After 2 retries with error feedback | Return 422; Continuity Manager escalates |
| Empty phases array | Validation | Return 422 |
| Unknown skill | Validation | Return 422 with skill name in message |
| Object hallucination | Validation | Return 422 |

## Reference Implementation Stub

```python
# pi05_service/server.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import hashlib, json, time
from .vla_runtime import VLARuntime  # wraps Pi0.5 inference
from .schema import TaskSpec, validate_semantic
from .prompts import build_planner_prompt, build_replanner_prompt

app = FastAPI()
vla = VLARuntime.load("pi05-base")
SKILL_REGISTRY = json.load(open("skills/registry.json"))
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 60.0


def _cache_key(goal_text: str, scene_snapshot: dict) -> str:
    canonical = json.dumps(
        {"goal": goal_text, "objects": scene_snapshot["objects"]},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@app.post("/plan")
def plan(req: PlanRequest) -> dict:
    key = _cache_key(req.goal_text, req.scene_snapshot)
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]

    prompt = build_planner_prompt(
        goal_text=req.goal_text,
        scene_snapshot=req.scene_snapshot,
        embodiment_id=req.embodiment_id,
        skill_registry=SKILL_REGISTRY,
    )
    for attempt in range(3):
        raw = vla.generate(prompt, image=req.scene_snapshot.get("rgb"))
        try:
            spec = TaskSpec.model_validate_json(strip_fences(raw))
            validate_semantic(spec, req.scene_snapshot, SKILL_REGISTRY)
            _cache[key] = (now, spec.model_dump())
            return spec.model_dump()
        except (ValueError, ValidationError) as e:
            prompt = augment_prompt_with_error(prompt, str(e))
    raise HTTPException(status_code=422, detail="planner failed schema after retries")
```

## What Pi0.5 should NOT do

- Pick joint waypoints.
- Estimate forces.
- Set RL policy hyperparameters.
- Decide whether the platform should move (that's ROS).
- Override the skill registry.
