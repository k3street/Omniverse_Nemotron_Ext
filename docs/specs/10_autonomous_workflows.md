# Phase 10 — Autonomous Multi-Step Workflows

**Status:** Not implemented  
**Depends on:** Phase 6-9 functional (tools work, governance works, data capture works)  
**Research:** `research_reports/rev2/brainstorm_autonomous_workflows.md`, `rev2/brainstorm_phase10_plus.md`
**Depends on:** Phase 6-9 functional  
**Research:** `rev2/brainstorm_autonomous_workflows.md`, `rev2/brainstorm_steal_features.md`

---

## Overview

User gives a high-level goal → system plans and executes a multi-step workflow autonomously → governance checkpoints at critical decisions → user approves/rejects at phase boundaries, not per tool call.

**"Set up a complete RL training pipeline for this robot"** → system creates env, imports robot, generates reward, launches training, monitors, reports back. One sentence in, trained policy out.

---

## Architecture: Templates + Dynamic Subplanning

**NOT pure LLM planning** (too unstructured for a system where checkpoints must fire at specific semantic boundaries).
User gives a high-level goal → system plans and executes a multi-step workflow autonomously → governance checkpoints at critical decisions.

**Three core patterns (from cross-industry research):**
1. **Plan as editable artifact** (from Copilot Workspace) — show the full plan before any code runs, let user edit
2. **Grounded citations** (from Perplexity) — every API reference has a source, preventing hallucinations
3. **Autonomous error-fix loop** (from Replit Agent) — generate → run → crash → read error → fix → run again, without human intervention

---

## Architecture: Templates + Dynamic Subplanning + Error Recovery

```
WorkflowOrchestrator
  ├── WorkflowTemplate (hardcoded phases + checkpoint placement)
  │     ├── Phase 1: [LLM generates patches]
  │     ├── CHECKPOINT: user approves/rejects
  │     ├── Phase 2: [LLM generates patches]
  │     ├── CHECKPOINT: user approves/rejects
  │     ├── Phase 1: [LLM generates plan — SHOWN TO USER AS EDITABLE ARTIFACT]
  │     ├── CHECKPOINT: user reviews/edits plan
  │     ├── Phase 2: [LLM generates code from approved plan]
  │     │     └── ERROR-FIX LOOP: run → crash → diagnose → fix → run (max 3 retries)
  │     ├── CHECKPOINT: user reviews result
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
        ├── Snapshot Manager (rollback per phase)
        └── PhysX Error Interceptor (from Phase 2 addendum)
```

---

## Pattern 1: Plan as Editable Artifact

Before generating any code, show the user what will happen:

```
User: "Set up RL training for this robot to pick up the cup"

Isaac Assist: Here's my plan:

PLAN (edit before I proceed):
┌─────────────────────────────────────────┐
│ 1. Create IsaacLab env "FrankaCupPick"  │
│    - Robot: /World/Franka (Panda)       │
│    - Object: /World/Table/Cup           │
│    - Obs: joint_pos, ee_pos, cup_pos    │
│    - Reward: distance_to_cup + grasp    │
│    - 64 parallel envs, spacing 2.5m     │
│                                         │
│ 2. Train with PPO (rsl_rl)              │
│    - 5000 iterations                    │
│    - ~45 min on your GPU                │
│                                         │
│ 3. Evaluate: 100 episodes, report       │
│    success rate                         │
│                                         │
│ [Edit plan]  [Approve & run]  [Cancel]  │
└─────────────────────────────────────────┘
```

User can edit (e.g., "change to 128 envs", "add orientation reward") before any code is generated. Prevents wasting GPU-hours on wrong configs.

---

## Pattern 2: Grounded Citations

Every API reference in generated code includes a source:

```python
# Source: IsaacLab docs — create_direct_rl_env.html
# Verified for: IsaacLab 2.x, Isaac Sim 5.1
from isaaclab.utils import configclass

# Source: isaaclab.envs.mdp API reference
# Function signature: joint_pos(env) -> Tensor[num_envs, num_joints]
joint_pos: ObsTerm = ObsTerm(func=mdp.joint_pos)
```

**Implementation:** System prompt instructs LLM to cite sources. Post-generation: validate cited APIs exist via `get_supported_robot_policy_pairs()` and tool schema registry.

---

## Pattern 3: Autonomous Error-Fix Loop

When generated code crashes, the system reads the error and fixes it automatically (up to 3 retries):

```
[Attempt 1] Running IsaacLab env creation...
❌ Error: AttributeError: module 'isaaclab.envs.mdp' has no attribute 'joint_positions'

[Auto-fix] The correct function is mdp.joint_pos (not joint_positions).
  Source: isaaclab.envs.mdp API — confirmed via code search.

[Attempt 2] Running with fix...
❌ Error: PhysX: Invalid inertia tensor on /World/Franka/link3

[Auto-fix] Link3 has zero mass from URDF import. Setting minimum mass=0.01 kg.
  Source: Phase 2 physics health check — known URDF import issue.

[Attempt 3] Running with fix...
✅ Success. IsaacLab env "FrankaCupPick" created with 64 envs.
```

**Implementation:** 
1. Run code via Kit RPC `exec_sync`
2. If error: capture via PhysX error event stream (Phase 2 addendum) + Python traceback
3. Feed error to LLM with context: "this code crashed with this error, fix it"
4. LLM generates patched code
5. Retry (max 3 attempts)
6. If still failing after 3: stop, show all errors, ask user

---

## MVP Workflows

### W1 — Full RL Training Pipeline

| Phase | What | Checkpoint? | Error-Fix? |
|-------|------|:-----------:|:----------:|
| 1. Plan | Show plan artifact | **Yes** — user edits | No |
| 2. Env creation | Generate IsaacLab env code | No | **Yes** — up to 3 retries |
| 3. Reward | Generate reward function | **Yes** — user reviews | No |
| 4. Training | Launch subprocess, stream metrics | No | No (long-running) |
| 5. Results | Show reward curve, success rate | **Yes** — deploy/iterate/discard | No |
| 6. Deploy | Load policy into scene | **Yes** — user sees robot move | No |

### W4 — Simulation Debugging (Error-Fix Loop Native)

| Phase | What | Checkpoint? | Error-Fix? |
|-------|------|:-----------:|:----------:|
| 1. Diagnose | Read console, physics state | No | No |
| 2. Hypothesis | LLM identifies likely cause | No | No |
| 3. Fix | Generate patch | **Yes** — user reviews | **Yes** — auto-retry if fix doesn't resolve |
| 4. Verify | Run sim, check if error persists | No | Loop to phase 2 if still failing (max 3) |
| 5. Report | "Fixed: ground plane had no collision mesh" | No | No |

### W2 — Robot Import & Configuration

| Phase | What | Checkpoint? | Error-Fix? |
|-------|------|:-----------:|:----------:|
| 1. Import | URDF/MJCF import | No | **Yes** — common import errors auto-fixed |
| 2. Verify | Run collision mesh quality check | No | No |
| 3. Auto-fix | Apply robot-specific fix profile | **Yes** — user reviews fixes | No |
| 4. Motion planning | Generate robot description, test IK | No | **Yes** — retry with different config |
| 5. Report | "Robot ready. 7 DOF, RMPflow configured, collision meshes verified." | No | No |

---

## Safety Rails

1. **Blast-radius limit:** Max 50 prims modified per phase
2. **Scope-prim boundary:** Workflow operates under declared root prim only
3. **Pre-workflow snapshot:** Single rollback point
4. **Error-fix loop limit:** Max 3 retries, then stop and ask human
5. **No silent long-running work:** Progress notification every 30s
6. **Grounded citations:** LLM must cite API source — uncitable claims flagged

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
### New API endpoints:
- `POST /api/v1/workflow/start` — start workflow with plan artifact
- `POST /api/v1/workflow/{id}/edit_plan` — user edits plan before execution  
- `GET /api/v1/workflow/{id}/status` — SSE stream of events
- `POST /api/v1/workflow/{id}/checkpoint/{phase}/approve` — approve/reject
- `POST /api/v1/workflow/{id}/cancel` — cancel and rollback

### Error-fix loop implementation:
```python
async def execute_with_retry(code, max_retries=3):
    for attempt in range(max_retries):
        result = await kit_rpc.exec_sync(code)
        if result.success:
            return result
        
        # Feed error to LLM for auto-fix
        fix_prompt = f"This code failed:\n{code}\n\nError:\n{result.error}\n\nFix the code."
        fixed_code = await llm.generate(fix_prompt)
        code = fixed_code
        
        yield WorkflowEvent(type="auto_fix", attempt=attempt+1, error=result.error, fix=fixed_code)
    
    # All retries failed
    yield WorkflowEvent(type="retry_exhausted", errors=[...])
```

---

## Proactive Agent Mode (No User Prompt)

Beyond user-initiated workflows — the assistant acts on its own when it detects opportunities or problems.

### Triggers (scene state → automatic action)

| Trigger | What Agent Does | Governance |
|---------|----------------|-----------|
| New scene opened | Run `preflight_check`, report issues | Show results, don't auto-fix |
| Robot imported | Run `verify_import` + collision mesh check | Show results, offer auto-fix |
| Console errors appear | Run `explain_error` with prim context | Show in chat proactively |
| Training started | Monitor entropy, reward, NaN every 60s | Alert only on anomaly |
| Training finished | Run `diagnose_training` + generate eval harness | Show report |
| Scene idle >5 min | Suggest next steps based on scene state | Non-intrusive suggestion |
| `sim_control("play")` | Run preflight if not run recently | Block on Tier 1 errors (configurable) |

### Implementation

```python
class ProactiveAgent:
    def __init__(self, orchestrator, config):
        self.enabled = config.get("PROACTIVE_MODE", False)
        self.triggers = {
            "scene_opened": self._on_scene_opened,
            "robot_imported": self._on_robot_imported,
            "console_error": self._on_console_error,
            "training_started": self._on_training_started,
            "sim_play": self._on_sim_play,
        }
    
    async def _on_scene_opened(self, scene_path):
        results = await self.orchestrator.call_tool("preflight_check")
        if results["tier1_errors"]:
            await self.chat.send("I found some issues with your scene:", results)
    
    async def _on_console_error(self, error_text):
        explanation = await self.orchestrator.call_tool("explain_error", error_text)
        await self.chat.send(f"⚠ {explanation['prim']}: {explanation['message']}")
    
    async def _on_training_started(self, run_dir):
        # Monitor every 60 seconds
        while training_active:
            await asyncio.sleep(60)
            diagnosis = await self.orchestrator.call_tool("diagnose_training", run_dir)
            if diagnosis["issues"]:
                await self.chat.send("Training alert:", diagnosis)
```

### Key Principle: Proactive ≠ Autonomous Modification

The proactive agent **observes and reports** — it does NOT auto-fix without user approval (unless preflight auto-fix is explicitly enabled). It's a smart monitoring layer, not an autonomous actor.

Exception: if `AUTO_PROACTIVE_FIX=true` is set AND the fix is Tier 1 crash-preventer (missing CollisionAPI etc.), auto-fix is applied with a chat notification: "Auto-fixed: added CollisionAPI to /World/Ground."

### Tool Combination Recipes

The proactive agent uses the same tools as manual workflows, but chains them automatically:

**"Can this robot do this task?"** (triggered when user places a target object near a robot)
→ `show_workspace` + `check_singularity` + `overlap_box` + `measure_distance`
→ "The cup is reachable but near the workspace edge. Consider moving it 5cm closer."

**"Why is the sim slow?"** (triggered when FPS drops below threshold)
→ `diagnose_performance` + `find_heavy_prims` + `suggest fix`
→ "FPS dropped to 8. Your new mesh has 300K collision triangles. Switch to convex hull?"

**"Training going well?"** (triggered periodically during RL training)
→ `diagnose_training` (entropy, reward, bimodal, NaN)
→ "Training looks healthy at step 3000. Reward trending up, entropy stable."

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
| Plan artifact generation | L0 | Known scene → correct plan text |
| Plan editing | L0 | Edit → correct updated plan |
| Error-fix loop | L0 | Known error → LLM generates fix (mock) |
| Retry limit | L0 | 3 failures → stops and reports |
| Checkpoint flow | L1 | Mock approval → correct state transitions |
| Full RL workflow | L3 | Requires Kit + IsaacLab |
