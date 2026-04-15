# Phase 10 — Autonomous Multi-Step Workflows

**Status:** Not implemented  
**Depends on:** Phase 6-9 functional (tools work, governance works, data capture works)  
**Research:** `research_reports/rev2/brainstorm_autonomous_workflows.md`, `rev2/brainstorm_phase10_plus.md`

---

## Overview

User gives a high-level goal → system plans and executes a multi-step workflow autonomously → governance checkpoints at critical decisions → user approves/rejects at phase boundaries, not per tool call.

**"Set up a complete RL training pipeline for this robot"** → system creates env, imports robot, generates reward, launches training, monitors, reports back. One sentence in, trained policy out.

---

## Architecture: Templates + Dynamic Subplanning

**NOT pure LLM planning** (too unstructured for a system where checkpoints must fire at specific semantic boundaries).

```
WorkflowOrchestrator
  ├── WorkflowTemplate (hardcoded phases + checkpoint placement)
  │     ├── Phase 1: [LLM generates patches]
  │     ├── CHECKPOINT: user approves/rejects
  │     ├── Phase 2: [LLM generates patches]
  │     ├── CHECKPOINT: user approves/rejects
  │     └── ...
  └── Existing infrastructure:
        ├── Pipeline Executor (executes patches within a phase)
        ├── Governance Engine (approval dialogs)
        └── Snapshot Manager (rollback per phase)
```

**Key insight:** Pipeline mode already auto-executes code patches. The workflow orchestrator is a layer ABOVE it that chains multiple pipeline runs with governance gates between them.

---

## MVP Workflows (V1: implement these two first)

### W1 — Full RL Training Pipeline

**Trigger:** "Set up RL training for this robot to [task]"

| Phase | What Happens | Checkpoint? |
|-------|-------------|:-----------:|
| 1. Scene audit | Read stage, identify robot, objects, physics config | No |
| 2. Env scaffolding | Generate IsaacLab env code (7A) | **Yes** — user reviews env config |
| 3. Reward generation | Generate reward function (7E if available, else template) | **Yes** — user reviews reward |
| 4. Training launch | Start subprocess, stream metrics | No (long-running, progress via SSE) |
| 5. Results report | Show reward curve, success rate, video | **Yes** — user decides: deploy, iterate, or discard |
| 6. Policy deployment | Load trained policy into scene (7A.4) | **Yes** — user sees robot move |

**Failure handling:**
- Phase 2 fails (code doesn't compile) → retry with different template, max 3 attempts
- Phase 4 fails (training diverges) → report metrics, suggest reward adjustment, return to phase 3
- Any phase fails irrecoverably → rollback to pre-workflow snapshot, report what went wrong

### W4 — Simulation Debugging

**Trigger:** "The robot keeps falling through the floor" or "Help me debug this scene"

| Phase | What Happens | Checkpoint? |
|-------|-------------|:-----------:|
| 1. Diagnosis | Read console errors, physics state, collision setup | No |
| 2. Root cause | LLM analyzes symptoms, identifies most likely cause | No |
| 3. Proposed fix | Generate code patch | **Yes** — user reviews fix |
| 4. Verify | Execute, check if error persists | No |
| 5. Report | "Fixed: ground plane had no collision mesh" or "Still failing, trying next hypothesis" | If still failing → loop to phase 2 (max 3 iterations) |

---

## Future Workflows (V2+)

### W2 — Robot Import & Configuration
"Import my_robot.urdf and make it ready for manipulation"
→ Import → collision setup → drive tuning → motion planning config → verification

### W3 — Synthetic Data Generation
"Generate 10,000 training images of this object with randomized lighting"
→ Setup Replicator pipeline → preview → approve → run → export

### W5 — Eureka Reward Iteration
"Teach this robot to open the cabinet"
→ Generate env → generate reward → train → show results → user feedback → iterate reward → retrain

---

## Ask vs. Proceed Framework

Before a workflow starts, the orchestrator asks ONE batched clarification:

```
I'll set up RL training for the Franka arm. My plan:
- Task: pick-and-place (based on the cup on the table)
- Algorithm: PPO via rsl_rl
- Environments: 64 parallel
- Training: 5000 iterations (~45 min on your GPU)

Should I proceed, or do you want to adjust anything?
```

**During execution:** proceed autonomously within each phase. Only stop at marked checkpoints. No sequential questioning.

**Three factors for ask vs. proceed:**
1. **Reversible?** Yes → proceed. No → checkpoint.
2. **Confident?** High → proceed. Low → checkpoint.
3. **User needs to review output?** Yes → checkpoint. No → proceed.

---

## Safety Rails (Beyond Existing Governance)

1. **Blast-radius limit per phase:** Max 50 prims modified per phase. Larger changes require explicit approval.
2. **Scope-prim boundary:** Workflow operates only under a declared root prim (e.g., `/World/TrainingSetup`). Cannot modify prims outside scope.
3. **Pre-workflow snapshot:** Single rollback point to "before the workflow started."
4. **Subprocess isolation:** Training processes (7A, 7E) run with disk quotas and timeout.
5. **No silent long-running work:** Any phase taking > 30s must post a progress notification.
6. **Idempotency:** Retry of a failed phase must not duplicate work (check for existing prims/files before creating).

---

## Implementation

### New components:

```python
class WorkflowOrchestrator:
    def execute(self, workflow_id: str, params: dict) -> AsyncGenerator[WorkflowEvent]:
        template = WORKFLOW_TEMPLATES[workflow_id]
        pre_snapshot = snapshot_manager.create("pre_workflow")

        for phase in template.phases:
            # LLM generates patches for this phase
            patches = await llm.plan_phase(phase, stage_context)

            if phase.has_checkpoint:
                yield WorkflowEvent(type="checkpoint", phase=phase, patches=patches)
                approval = await wait_for_user_approval()
                if not approval:
                    snapshot_manager.restore(pre_snapshot)
                    yield WorkflowEvent(type="rolled_back")
                    return

            # Execute via existing pipeline executor
            result = await pipeline_executor.execute(patches)

            if not result.success:
                if phase.retry_count < phase.max_retries:
                    phase.retry_count += 1
                    continue  # retry phase
                else:
                    yield WorkflowEvent(type="phase_failed", phase=phase, error=result.error)
                    # Offer rollback or continue
                    ...

        yield WorkflowEvent(type="completed")

WORKFLOW_TEMPLATES = {
    "rl_pipeline": WorkflowTemplate(phases=[...]),
    "debug_scene": WorkflowTemplate(phases=[...]),
    ...
}
```

### New API endpoints:

- `POST /api/v1/workflow/start` — start a workflow
- `GET /api/v1/workflow/{id}/status` — SSE stream of workflow events
- `POST /api/v1/workflow/{id}/checkpoint/{phase}/approve` — approve/reject at checkpoint
- `POST /api/v1/workflow/{id}/cancel` — cancel and rollback

### Chat integration:

LLM detects workflow-level intent ("set up RL training", "debug this scene") and offers to launch the workflow instead of executing individual tools.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Template phase ordering | L0 | Verify checkpoint placement, dependency order |
| Ask-vs-proceed logic | L0 | Reversibility × confidence matrix |
| Blast-radius enforcement | L0 | Prim count check |
| Scope-prim boundary | L0 | Reject out-of-scope modifications |
| Retry logic | L0 | Max retries, failure escalation |
| Rollback on cancel | L1 | Snapshot restore via mock |
| Full RL workflow | L3 | Requires Kit + IsaacLab |

---

## Known Limitations

- Workflows are template-driven — adding a new workflow requires code, not just configuration
- LLM planning within phases may still hallucinate — governance catches this at checkpoints
- Long-running phases (training) block the workflow — async with SSE progress
- V1 supports 2 workflows only (RL pipeline + debugging) — expand based on usage data
- No multi-workflow parallelism — one workflow at a time per session
