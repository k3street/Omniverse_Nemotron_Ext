# Phase 10 — Autonomous Multi-Step Workflows: Design Brainstorm

**Author:** AI Agent Architect (Claude Sonnet 4.6)
**Date:** 2026-04-15
**Status:** Brainstorm / Design Draft

---

## Framing

Isaac Assist today is a powerful command-response loop: the user types, the agent responds with a proposal, the user approves, one patch executes. Pipeline mode already auto-executes a *sequence of pre-planned code patches within a single plan* after one approval. Phase 10 extends this upward: the user states a goal ("train a pick-and-place policy for the Franka") and the agent operates autonomously across multiple high-level planning cycles — each potentially spawning its own patch plan, tool calls, and validation — while surfacing governance checkpoints at semantically meaningful decision boundaries rather than at every micro-action.

The key distinction from pipeline mode:
- **Pipeline mode**: one plan, N actions, one approval gate, all actions execute sequentially.
- **Autonomous workflow**: N plans, each potentially involving diagnosis + planning + approval + validation + iteration, coordinated by a workflow-level orchestrator that understands goal state and can branch, retry, and escalate.

---

## 1. The Five Most Valuable Autonomous Workflows

Ranked by: user time saved, frequency of use, and whether the task is genuinely multi-plan (not just multi-action).

### W1 — "Set up complete RL training pipeline for this robot"

**User prompt:** "Set up an RL training pipeline for the Franka Panda doing pick-and-place."

**Value:** Currently requires 10-15 manual steps spanning USD authoring, Python scaffolding, environment registration, library config, and launch — each step blocking on the prior. Total expert time: 2-4 hours. This is the single highest-value workflow.

### W2 — "Import this robot and make it ready to control"

**User prompt:** "Import this URDF and configure it for motion planning."

**Value:** Import → validate physics → tune PD gains → configure end-effectors → verify in simulation. Each step requires results from the previous to parameterize correctly. Currently 6-10 sequential manual steps. Frequency: every new robot.

### W3 — "Generate a synthetic dataset for this object"

**User prompt:** "Generate 10,000 training images of this YCB mug with randomized lighting and camera poses."

**Value:** Scene setup → annotator config → domain randomization → multi-run execution → output verification. A full SDG pipeline today requires Replicator expertise that most robotics engineers don't have. The agent can encode best-practice defaults.

### W4 — "Debug why my simulation is behaving wrong"

**User prompt:** "My robot arm is shaking violently at startup. Fix it."

**Value:** Multi-hypothesis diagnosis (PD gains too high? mass properties wrong? physics time step too coarse? joint limits violated?) → iterative fix-and-validate until stable. Currently the user must understand enough physics to form hypotheses; the agent can enumerate and rule out systematically.

### W5 — "Generate and iterate a reward function for this task"

**User prompt:** "I want the robot to learn to push a box to a target. Generate a reward function and refine it until training converges."

**Value:** Eureka-style iterative reward synthesis with real training feedback. Each iteration is a full training run (potentially minutes to hours); the agent monitors metrics and mutates the reward function accordingly. No human RL expertise required.

---

## 2. Workflow Step-by-Step Designs with Governance Checkpoints

### W1 — Full RL Training Pipeline

**Goal state:** Training script is running, TensorBoard shows reward increasing.

```
PHASE 0 — DISCOVERY (autonomous, no checkpoint)
  Step 1.  Read current stage: find robot prim, identify joint count,
           joint names, end-effector candidates.
  Step 2.  Identify what already exists: any existing task files?
           Any gym registration? Any trained checkpoints?
  Step 3.  Ask the user one clarifying question if robot not found:
           "I see no robot in the scene. Import one first, or specify a prim path?"
           → If robot found: proceed autonomously.

CHECKPOINT A — "Here's my plan" (REQUIRED user acknowledgment)
  Present:
    - Detected robot: /World/Franka, 9 DOF, end-effector at /World/Franka/panda_hand
    - Task type: pick-and-place (inferred from user prompt)
    - RL library: rsl_rl (default) — user can override
    - Files to be created: [list with paths]
    - Estimated GPU time for first training run: ~20 min
    - Governance risk level: MEDIUM (file creation + subprocess launch)
  User must click "Proceed" or modify parameters.

PHASE 1 — SCAFFOLD (autonomous after checkpoint A)
  Step 4.  Generate IsaacLab task config (ObservationGroupCfg, RewardsCfg,
           EventCfg, TerminationCfg) using validated API patterns.
  Step 5.  Generate gym registration block in __init__.py.
  Step 6.  Validate generated code with patch_validator (OmniGraph, PhysX rules).
           → If validation fails: auto-repair up to 3 times, then checkpoint.
  Step 7.  Write files (patch plan, approved by checkpoint A).
  Step 8.  Verify imports work by running: `python -c "import <task_module>"`.
           → If import fails: diagnose, repair, re-verify (max 2 retries).

CHECKPOINT B — "Ready to launch training" (REQUIRED before subprocess)
  Present:
    - Generated task files (diff view)
    - Validation result: PASS / issues list
    - Training command that will be run
    - Where logs will go
    - Reminder: this will use the GPU and take ~N minutes
  User must click "Launch Training" — launching a subprocess is HIGH-RISK.

PHASE 2 — TRAINING MONITOR (semi-autonomous)
  Step 9.  Launch training subprocess (isaaclab.sh -p train.py ...).
  Step 10. Stream stdout/stderr to chat in real time.
  Step 11. Every 5 minutes: parse TensorBoard log, report key metrics
           (episode_reward_mean, policy_loss, value_loss).
  Step 12. Detect known failure signatures:
           - Reward stuck at 0 for >10k steps → flag "possible reward shaping issue"
           - NaN in gradients → flag "numerical instability, consider lr reduction"
           - CUDA OOM → flag "reduce num_envs"
           → Each detection posts an advisory message; does NOT auto-stop training.

CHECKPOINT C — "Training anomaly detected" (CONDITIONAL, only if anomaly)
  Present: what was detected, suggested fix (e.g., modify reward scale),
  options: [Ignore, Stop and Modify, Stop Training].
  → If user selects "Stop and Modify": agent repairs the config, restarts.

PHASE 3 — COMPLETION
  Step 13. On training completion: locate checkpoint file, summarize metrics.
  Step 14. Offer: "Deploy this policy to the simulator? [Yes / No]"
           → Deployment is a separate action, not auto-executed.
```

**Governance checkpoints:** 3 mandatory (A, B, C-conditional). A and B are always shown. C only fires if anomaly detected.

---

### W2 — Robot Import and Configuration

**Goal state:** Robot is in the scene, physics validates, PD gains are tuned, end-effector is configured.

```
PHASE 0 — IMPORT (one checkpoint)
CHECKPOINT A — "Import this URDF?" (LOW risk but irreversible until rollback)
  Present: file path, detected DOF count, detected mesh count, preview of
  what will be created in the stage.
  → Snapshot created automatically.

PHASE 1 — PHYSICS VALIDATION (autonomous)
  Step 1.  Run stage analyzer on imported robot:
           - Check all joints have valid drive/limit APIs
           - Check all collision meshes are convex or have SDF enabled
           - Check mass properties (detect zero-mass links)
           - Check articulation root placement
  Step 2.  Run simulation for 2 seconds (headless, physics only).
  Step 3.  Capture articulation state: are joints moving? Any NaN positions?
           Any penetration contacts at rest pose?
  Step 4.  If robot is unstable: classify failure mode.

CHECKPOINT B — "Physics issues found" (CONDITIONAL)
  Present findings list with severity. For each:
    - AUTO-FIXABLE: "missing convex decomposition on mesh X" — show patch
    - NEEDS GUIDANCE: "zero inertia on link Y — set to auto-compute or specify value?"
  User approves auto-fixes and answers guided questions.

PHASE 2 — PD GAIN TUNING (autonomous)
  Step 5.  Call isaacsim.robot_setup.gain_tuner with detected DOF config.
  Step 6.  Run step response test: command each joint ±10° from rest.
  Step 7.  Evaluate: overshoot <5%? Settle time <500ms? Steady-state error <1%?
           → If gains fail criteria: iteratively adjust (max 5 iterations).
  Step 8.  If auto-tuner fails to converge: escalate to checkpoint.

CHECKPOINT C — "Gain tuning result" (ALWAYS shown, brief)
  Present: before/after metrics, final gain values, step response plots (if
  viewport capture is feasible). User confirms or overrides manually.

PHASE 3 — END-EFFECTOR CONFIGURATION (one checkpoint)
  Step 9.  Detect end-effector candidates: terminal links with no children,
           or links named "hand", "gripper", "tcp", "ee", "tool".
  Step 10. If one candidate: proceed. If multiple: ask user.

CHECKPOINT D — "Configure end-effector" (REQUIRED if ambiguous)
  "Detected 2 candidates: /panda_hand and /tool_center_point.
  Which is the active end-effector?"

  Step 11. Apply isaacsim.robot.manipulators config.
  Step 12. Final validation: move to a reachable pose, confirm no collision.
```

**Governance checkpoints:** 2 always-shown (A, C), 2 conditional (B, D).

---

### W3 — Synthetic Data Generation

**Goal state:** N annotated images in output directory, format ready for training.

```
PHASE 0 — CONFIGURATION (one checkpoint)
CHECKPOINT A — "SDG plan" (REQUIRED)
  Agent presents:
    - Target object(s): detected in scene or user-specified
    - Number of images: from prompt or default 1000
    - Annotators: RGB, depth, bounding_box_2d_tight, instance_segmentation (defaults)
    - Domain randomization plan: lighting (on by default), camera poses, distractors
    - Output directory and format (KITTI / COCO / custom)
    - Estimated time based on image count and scene complexity
  User can modify any parameter before proceeding.

PHASE 1 — SCENE SETUP (autonomous)
  Step 1.  Verify target object has correct semantic labels (class="mug", etc.)
           → Auto-fix missing labels if detectable.
  Step 2.  Add Replicator scatter surface if not present.
  Step 3.  Configure camera dome with poses from fibonacci sphere distribution.
  Step 4.  Add HDR dome light variants (default: 5 from NVIDIA sample set).
  Step 5.  Configure BasicWriter with selected annotators.
  Step 6.  Write Replicator graph (validate OmniGraph structure).

CHECKPOINT B — "Preview first frame" (ALWAYS shown)
  Render 3 sample frames at different camera/lighting settings.
  Show annotator overlays in viewport. User confirms scene looks correct.
  → If user requests changes: agent modifies randomization params and re-previews.

PHASE 2 — GENERATION (autonomous, monitored)
  Step 7.  Launch generation run (rep.orchestrator.run()).
  Step 8.  Monitor: report progress every 100 images. Detect:
           - Render stalls (no output for >30s)
           - Annotation format errors in output
           - Disk space warnings (<5GB remaining)

PHASE 3 — VALIDATION (autonomous)
  Step 9.  Sample 50 random output images, verify:
           - All annotation files present
           - Bounding boxes are within image bounds
           - Segmentation masks cover expected pixel count
  Step 10. Report: "Generated 10,000 images. Validation: 50/50 samples pass.
           Output at /path/to/output. Total size: 4.2GB."
```

**Governance checkpoints:** 2 always-shown (A, B). B is the critical "eyes on scene" gate before running.

---

### W4 — Simulation Debugging

**Goal state:** Agent has identified root cause(s), proposed verified fixes, simulation is stable.

```
PHASE 0 — OBSERVATION (autonomous, no checkpoint)
  Step 1.  Capture viewport screenshot.
  Step 2.  Read console log (last 200 lines).
  Step 3.  Read physics simulation state: joint positions, velocities, contacts.
  Step 4.  Run stage analyzer: physics validation rules, schema checks.
  Step 5.  Formulate hypothesis list ranked by prior probability:
           H1: PD gains too high (most common for "shaking")
           H2: Physics time step too coarse
           H3: Mass/inertia misconfigured
           H4: Joint limits violated at rest pose
           H5: Floating root (missing fixed joint to world)
           H6: Collision mesh interpenetration at spawn

PHASE 1 — HYPOTHESIS TESTING (autonomous, iterative)
  Step 6.  Test H5 first (cheap, structural): check articulation root API.
  Step 7.  Test H4: check rest pose against joint limits.
  Step 8.  Test H3: check mass properties against expected range for robot type.
  Step 9.  Test H6: check contact report at t=0.
  Step 10. Test H1: run short sim, measure joint velocity oscillation frequency.
  Step 11. Test H2: compare current dt against recommended for robot DOF count.
  → Stop testing when one hypothesis is confirmed with high confidence (>0.8).

CHECKPOINT A — "Root cause identified" (ALWAYS shown)
  Present:
    - Most likely cause(s) with confidence scores
    - Evidence that led to the diagnosis (log lines, state values)
    - Proposed fix(es) with patch plan
    - Snapshot will be created
  If multiple causes: present in priority order. User can deselect fixes.

PHASE 2 — FIX AND VERIFY (autonomous after approval)
  Step 12. Apply approved patches.
  Step 13. Run simulation for 5 seconds.
  Step 14. Capture new viewport + physics state.
  Step 15. Check: is shaking gone? Is robot at stable rest pose?

CHECKPOINT B — "Fix result" (ALWAYS shown)
  "Before: oscillation amplitude 0.8 rad/s. After: 0.02 rad/s. Robot stable."
  OR
  "Fix applied but instability persists. Running secondary hypothesis..."
  → If fix fails: re-enter hypothesis loop with H1..H6 minus confirmed-false hypotheses.

ESCALATION (after 3 failed fix cycles)
  If no fix has stabilized the simulation after 3 iterations:
  "I've exhausted high-confidence hypotheses. Creating escalation bundle."
  → Package: stage snapshot, console log, all attempted fixes and results,
    recommended NVIDIA Forum search terms.
```

**Governance checkpoints:** A (always, before any mutation), B (always, after fix). The loop can repeat but never mutates without going through a fresh A-checkpoint.

---

### W5 — Eureka Reward Generation and Iteration

**Goal state:** Reward function that produces measurable task progress within N training runs.

```
PHASE 0 — SETUP (one checkpoint)
CHECKPOINT A — "Reward design brief" (REQUIRED)
  Agent presents:
    - Detected task type (from scene analysis: box on table, target marker)
    - Proposed reward components: task_progress, action_penalty, contact_force_penalty
    - Environment class to be used (must be DirectRLEnv subclass)
    - Number of Eureka iterations planned (default: 5)
    - Training budget per iteration (default: 500k steps)
    - GPU time estimate: ~N hours total
  User confirms or adjusts.

PHASE 1 — INITIAL REWARD GENERATION (autonomous)
  Step 1.  Inject full environment source into LLM context.
  Step 2.  Generate reward function with component-wise outputs
           (required by Eureka for fitness evaluation).
  Step 3.  Validate: Python syntax check, import check, component naming.
  Step 4.  Patch reward function into task file.

PHASE 2 — TRAINING AND EVALUATION (autonomous, async)
  Step 5.  Launch training subprocess (500k steps).
  Step 6.  On completion: parse TensorBoard → extract component-wise rewards,
           episode length, success rate.
  Step 7.  Compute Eureka fitness: success_rate weighted by episode_length.

PHASE 3 — ITERATION LOOP (autonomous, up to N iterations)
  Step 8.  Build mutation prompt: current reward function + fitness score +
           component breakdown + failure analysis (which components are sparse/dense).
  Step 9.  Generate mutated reward function.
  Step 10. Apply, train, evaluate. Update best-so-far if fitness improves.
  Step 11. Post progress update to chat after each iteration:
           "Iteration 2/5: success rate 12% → 31%. Reward shaping improved
           task_progress component."

CHECKPOINT B — "Mid-run checkpoint" (AFTER EACH ITERATION)
  Brief status card: iteration N, current best fitness, convergence trend.
  User can: [Continue], [Stop and Deploy Best], [Adjust strategy].
  → Does NOT block — user can ignore; training continues. But user CAN intervene.
  This is a "transparent update" checkpoint, not a blocking gate.

CHECKPOINT C — "Final result" (ALWAYS shown at end)
  Present: best reward function, training curve, success rate, deployment option.
  → "Deploy this policy?" is a separate, explicit action.

ESCALATION
  If after N iterations success rate is <5%:
  "Reward generation did not converge. Possible causes: [list]. Recommend:
  trying REvolve (population-based) or manually specifying a shaped reward."
```

**Governance checkpoints:** A (always, blocking), B (non-blocking status updates per iteration), C (always, blocking before deployment).

---

## 3. Failure Handling Mid-Workflow

### Taxonomy of mid-workflow failures

| Failure Type | Example | Agent Response |
|---|---|---|
| **Tool call failure** | `gain_tuner` API returns error | Retry once with different parameters; if fails again, log and skip step, continue workflow with degraded output |
| **Validation failure** | Generated code fails patch_validator | Auto-repair loop (max 3 attempts); if still failing, pause workflow and show diagnostic checkpoint |
| **Unexpected state** | Robot prim moved/deleted during workflow | Detect at next stage-read step; checkpoint "Scene state changed unexpectedly. Continue from current state or restart?" |
| **Resource exhaustion** | CUDA OOM during training | Detect from stderr; checkpoint "GPU OOM detected. Reduce num_envs from 4096 to 1024?" |
| **Subprocess crash** | Training process exits with non-zero | Capture exit code + last 50 lines stderr; diagnose known exit patterns; checkpoint with diagnosis |
| **Regression** | Fix introduces new errors | Post-apply validation detects new findings; auto-rollback + checkpoint "Fix introduced regression, rolled back. Trying alternative..." |
| **Confidence collapse** | No hypothesis above 0.5 confidence | Escalate: "I cannot confidently diagnose this issue. Creating repro bundle." |
| **Timeout** | Step takes >5 minutes (unexpected) | Soft interrupt: checkpoint "Step X has been running for 5 minutes. Continue waiting, cancel, or skip?" |

### General failure-handling principles

1. **Prefer narrow retries over broad rollback.** Only rollback the specific action that failed, not the whole workflow.
2. **Preserve partial progress.** If steps 1-7 of 12 succeeded and step 8 fails, don't undo steps 1-7. Checkpoint and resume from step 8 after user input.
3. **Every destructive action is snapshotted.** Rollback is always available per step.
4. **Surface diagnostic context at checkpoints.** Don't just say "step failed" — always include: what was attempted, what the error was, what the agent believes caused it, what options exist.
5. **Distinguish permanent from transient failures.** A Python syntax error in generated code is recoverable; a missing system extension is not. Escalate the latter immediately.

### Workflow state machine

Each workflow runs as an explicit state machine with these states:

```
PLANNING → RUNNING → CHECKPOINT_WAITING → RUNNING → ... → COMPLETE
                  ↓                    ↑
              FAILED → RETRYING --------+
                    ↓
                ESCALATED
```

State is persisted to disk after every transition (not just on completion). If Isaac Sim crashes mid-workflow, the state can be loaded and the user can decide whether to resume or abandon.

---

## 4. When to Ask vs. Proceed Autonomously

### Decision framework: three factors

**Factor 1: Reversibility**
- Reversible action (has snapshot, can rollback): lean toward autonomous.
- Irreversible action (subprocess launch, network call, disk write without snapshot): require checkpoint.

**Factor 2: Confidence**
- Agent confidence >0.85 and single unambiguous interpretation: proceed.
- Confidence 0.6-0.85: proceed but log the ambiguity for the post-workflow summary.
- Confidence <0.6: checkpoint.

**Factor 3: User cost**
- Will the user need to review this anyway? (e.g., generated code they'll later edit): show it.
- Is the action invisible to the user otherwise? (e.g., setting a USD property deep in the hierarchy): show it.
- Is it a well-understood default the user would expect? (e.g., enabling collision on a mesh): proceed.

### Decision table

| Situation | Ask? |
|---|---|
| Ambiguous user intent ("set up the robot" — which robot?) | Always ask first |
| Multiple valid options with different tradeoffs (rsl_rl vs. rl_games) | Ask at planning checkpoint |
| Default is clearly correct (enable collision on mesh imported without collision) | Proceed, note in summary |
| Action is HIGH-RISK per governance classification | Always checkpoint |
| Subprocess launch | Always checkpoint |
| Auto-repair of known bug class (e.g., missing @configclass) | Proceed, show in diff |
| Auto-repair failed after 3 retries | Checkpoint |
| External resource required (download HDRI from Nucleus) | Checkpoint (network policy) |
| Simulation result differs from expected (training diverged) | Post advisory, don't block |

### The "1 clarifying question" rule

Before any multi-step workflow begins, the agent is allowed exactly one turn to ask clarifying questions — but it must batch ALL ambiguities into a single structured question card, not ask sequentially. Example:

```
Before I start, I need to confirm 3 things:
  1. Robot: I see /World/Franka and /World/UR10. Which should I set up? [Franka | UR10]
  2. Task: you said "pick-and-place" — target object? [YCB Cracker Box | Other]
  3. RL library: I'll default to rsl_rl. Is that OK? [Yes | Use rl_games | Use skrl]
```

After the user responds, no more questions until a checkpoint.

---

## 5. The Right Abstraction

### Recommendation: Workflow Templates + Dynamic Sub-planning

Three-layer architecture:

```
Layer 3: WORKFLOW LAYER
  WorkflowDefinition {
    id, name, trigger_intents, goal_state_checker,
    phases: [Phase], max_retries: int, escalation_policy
  }
  Phase {
    id, name, steps: [Step | DynamicSubplan],
    entry_checkpoint: CheckpointDef | None,
    exit_checkpoint: CheckpointDef | None
  }

Layer 2: SUBPLAN LAYER (dynamic, LLM-generated)
  For each Phase: the agent uses LLM to generate the specific
  PatchPlan for the current state. The workflow template defines
  WHAT category of action happens; the LLM determines the
  SPECIFIC actions given the live stage state.

Layer 1: ACTION LAYER (existing)
  PatchPlan → PatchAction[] (already implemented)
```

**Why templates rather than pure LLM planning?**

Pure LLM planning (generate entire workflow from scratch) is fragile:
- Hard to enforce governance checkpoints at the right places
- No guaranteed termination
- State persistence is difficult
- Hard to test

Pure behavior trees are too rigid:
- Can't adapt to robot-specific state (7-DOF vs. 12-DOF)
- Brittle when APIs change

Templates give the right balance:
- Checkpoints are hardcoded at semantically meaningful boundaries
- The *specific* patches within each phase are LLM-generated from current state
- The template can be unit-tested (phase transitions, checkpoint firing)
- New workflows can be added by writing a new template, not modifying core

**What NOT to use:** Behavior trees as the agent control structure. BTs are excellent for *robot behavior* but awkward for *agent task orchestration* — they assume a tick-rate loop and stateless conditions, while the agent workflow is asynchronous and stateful. Finite state machines with explicit state persistence are better here.

### Concrete data model sketch

```python
@dataclass
class WorkflowTemplate:
    workflow_id: str          # "rl_pipeline", "robot_import", "sdg", "debug", "eureka"
    name: str
    trigger_phrases: List[str]  # For intent routing
    phases: List[WorkflowPhase]
    goal_state_checker: str   # Name of callable that returns bool: goal reached?
    max_total_retries: int    # Across all phases
    escalation_after_n_failures: int

@dataclass
class WorkflowPhase:
    phase_id: str
    name: str
    entry_checkpoint: Optional[CheckpointSpec]   # None = no gate
    steps: List[WorkflowStep]
    exit_checkpoint: Optional[CheckpointSpec]
    on_failure: str   # "retry_phase" | "checkpoint" | "escalate" | "skip"
    max_retries: int

@dataclass
class WorkflowStep:
    step_id: str
    description: str
    step_type: str   # "subplan" | "tool_call" | "subprocess" | "monitor" | "validate"
    tool_name: Optional[str]          # For tool_call steps
    subplan_prompt: Optional[str]     # For subplan steps: prompt template for LLM
    success_condition: str            # Callable name or expression
    timeout_seconds: Optional[int]

@dataclass
class WorkflowRun:
    run_id: str
    workflow_id: str
    started_at: datetime
    state: str   # "planning" | "running" | "checkpoint_waiting" | "complete" | "failed" | "escalated"
    current_phase_id: str
    current_step_id: str
    phase_results: Dict[str, PhaseResult]
    snapshot_ids: List[str]   # One per destructive phase
    checkpoint_decisions: List[CheckpointDecision]
    retry_counts: Dict[str, int]
```

---

## 6. Relationship to Existing Pipeline Mode

### What pipeline mode is today

Pipeline mode (from the Patch Planner / governance docs) is: "approve once, execute N sequential code patches." It operates within a single `PatchPlan` — one approval dialog, one snapshot, N `PatchAction` objects executed in dependency order.

### How Phase 10 workflows extend this

| Dimension | Pipeline Mode | Autonomous Workflow |
|---|---|---|
| Scope | One PatchPlan | Multiple PatchPlans across multiple planning cycles |
| Approval granularity | One approval for all actions in the plan | Per-phase checkpoints (may approve a whole phase at once) |
| State awareness | Reads stage state once at plan generation | Re-reads state after every phase to parameterize next phase |
| Iteration | None — execute and done | Can loop back (e.g., Eureka reward iteration, debug retry) |
| Subprocess | Never — USD/Python writes only | Can launch training subprocesses (W1, W5) |
| Goal model | "Apply this patch" | "Reach this goal state" |

### How they compose

Pipeline mode becomes the *action execution mechanism inside a workflow phase*. The workflow orchestrator calls `POST /api/v1/plans/generate` to get a PatchPlan for the current phase, then calls `POST /api/v1/plans/approve` + `POST /api/v1/plans/apply` exactly as today. The workflow adds:
1. The outer loop that calls these endpoints multiple times
2. Goal-state checking between phases
3. The checkpoint framework that gates transitions
4. State persistence across the loop

This means the entire existing Patch Planner and Governance engine can be reused unchanged. The autonomous workflow layer is a new orchestrator *above* these, not a replacement.

### Pipeline mode as workflow phase

An existing pipeline-mode invocation (user manually triggers) is isomorphic to a single-phase, single-checkpoint workflow:

```
WorkflowTemplate {
  phases: [
    WorkflowPhase {
      entry_checkpoint: StandardApprovalCheckpoint,
      steps: [SubplanStep(prompt=user_request)],
      exit_checkpoint: None
    }
  ]
}
```

So Phase 10 is a strict superset of pipeline mode.

---

## 7. Additional Safety Rails Beyond Existing Governance

The existing governance system (module 07) covers per-action risk classification, approval dialogs, audit trail, and secret redaction. Autonomous workflows need additional safeguards at the workflow level:

### 7.1 Blast Radius Limits

**Problem:** A workflow with N phases can make N * M changes before the user sees a result. If the workflow has a bug or bad judgment, the cumulative blast radius is large.

**New rail:** `max_mutations_per_phase` (default: 5). If a generated subplan exceeds this, the phase is automatically downgraded to explain-only and a checkpoint is forced. Prevents runaway edit loops.

### 7.2 Workflow Scope Boundaries

**Problem:** A debugging workflow might "helpfully" modify files or prims outside the robot being debugged.

**New rail:** `scope_prims: List[str]` declared at workflow start. Any PatchAction targeting a prim outside this scope requires explicit user acknowledgment even in semi-autonomous mode. The workflow orchestrator cross-checks every generated action against the declared scope.

### 7.3 Checkpoint Fatigue Prevention

**Problem:** If every micro-decision requires a checkpoint, the user learns to click "Approve" without reading. This defeats governance.

**New rail:** Enforce minimum information density at checkpoints. A checkpoint must present at least one of: (a) a diff with >0 lines changed, (b) a metric with a specific value, (c) a question the user must answer. Checkpoints that are just "Click OK to continue" are prohibited in the template spec.

### 7.4 Subprocess Isolation

**Problem:** Training subprocesses (W1, W5) run outside Isaac Sim's process and outside the existing governance sandbox. They can write arbitrary files, consume unbounded GPU/disk.

**New rails:**
- All training subprocesses launched with an explicit `--log_root_path` pointing to a governed output directory.
- Disk quota check before launch (reject if <10GB free).
- Process group created so all child processes can be killed as a unit on workflow abort.
- No subprocess can write to the Isaac Sim scene directory.

### 7.5 Idempotency Guarantees for Retries

**Problem:** If a phase is retried after partial execution, running steps again may produce duplicate prims, duplicate files, or conflicting patches.

**New rail:** Each step must declare whether it is idempotent. Before retrying a non-idempotent step, the workflow must roll back to the snapshot taken at the phase boundary. This is enforced by the orchestrator — the template author does not need to handle this.

### 7.6 Workflow-Level Rollback

**Problem:** Snapshot manager today holds per-plan snapshots. A multi-phase workflow may have 5+ snapshots. "Undo the whole workflow" means restoring to the pre-workflow state.

**New rail:** `WorkflowRun` records the `pre_workflow_snapshot_id` taken immediately before Phase 0. The chat UI exposes "Undo Entire Workflow" as a single button, which calls rollback to this specific snapshot, regardless of how many intermediate snapshots exist.

### 7.7 No Silent Long-Running Work

**Problem:** A training run that takes 2 hours might complete while the user is away. The agent should not take any action on the result without the user being present to approve it.

**New rail:** Any step that spawns async work (training subprocess, long validation run) must complete by posting a *notification checkpoint* to the chat — a message that requires explicit acknowledgment before any downstream step runs. This prevents "surprise actions" the user didn't watch happen.

---

## 8. Comparison with Devin, Claude Code, and Cursor

### Claude Code (most directly comparable)

**How it handles multi-step work:** Pure dynamic planning. User gives goal; Claude Code decides every step using its own judgment, calling tools (Bash, Read, Edit, Grep) until goal is reached. No predefined workflow templates. Governance is at the tool-permission level (asks before running bash commands, before editing files) — but the *sequence* of tool calls is entirely LLM-determined.

**Strengths:**
- Maximum flexibility — handles tasks the designer didn't anticipate
- No template maintenance overhead
- Good at one-shot novel tasks ("refactor this module to use async")

**Weaknesses:**
- No persistent state across sessions — can't resume a workflow if Claude Code crashes
- Governance checkpoints are ad hoc, not at semantically meaningful phase boundaries
- No goal-state checker — relies on LLM to decide when it's done (can over-run)
- No parallel monitoring (can't watch a training run while doing other things)

**Relevance to Isaac Assist Phase 10:** Claude Code's dynamic planning is good inspiration for the *subplan generation within each phase*. But the outer loop should NOT be pure-LLM-dynamic because Isaac Sim workflows have known structure, and governance must fire at specific semantic boundaries (before subprocess launch, before destructive USD writes) — not at "whenever the LLM decides to ask."

### Devin

**How it handles multi-step work:** Task decomposition into a sequence of sub-tasks, each executed by a specialized agent (coder, tester, browser, etc.) with a shared memory and "planner" coordinating. Governance is minimal — Devin largely auto-executes. The user is notified of progress but rarely asked for approval mid-task. Checkpoints happen when Devin is genuinely stuck (no path forward) or when it detects a high-stakes action (PR merge, deploy).

**Strengths:**
- Very low friction for the user — truly autonomous for long stretches
- Good at software tasks where "wrong but recoverable" is acceptable

**Weaknesses:**
- For Isaac Sim: "wrong but recoverable" is not always true. A bad physics configuration can corrupt hours of downstream work. Devin's low-friction model is unsafe here.
- No domain-specific workflow knowledge — treats Isaac Sim like any Python project

**Relevance to Isaac Assist Phase 10:** Devin's non-blocking progress notifications (post updates, don't demand approval) are the right model for monitoring phases (W1 training, W3 generation). Devin's "ask when stuck" approach is a useful fallback mechanism but should be a floor, not the only governance layer.

### Cursor

**How it handles multi-step work:** Primarily single-file, single-turn edits with some "apply all" for multi-file changes. Cursor Composer (the closer analog) allows multi-file edits but still operates as a single planning turn — not iterative. No persistent workflow state, no subprocess management, no monitoring. The "Accept All" / "Reject All" buttons are the governance model.

**Strengths:**
- Very tight editor integration makes diffs easy to review
- Per-hunk accept/reject is ergonomic

**Weaknesses:**
- Not designed for async/long-running work at all
- No concept of goal state or iteration
- Governance is entirely at the diff-review level — nothing at the semantic/phase level

**Relevance to Isaac Assist Phase 10:** Cursor's diff-review UX is already embodied in the existing `diff_card` in module 10. Not much to learn for the workflow orchestration layer specifically.

### Synthesis: what Phase 10 should borrow from each

| From | Borrow |
|---|---|
| Claude Code | LLM-dynamic subplan generation within each phase; tool-call loop for step execution |
| Devin | Non-blocking progress notifications during async phases; "ask when stuck" as fallback |
| Cursor | Per-diff accept/reject at checkpoints (already in place); compact "apply all" for low-risk phases |
| None | Pure LLM determination of checkpoint placement — always hardcode governance gates at phase boundaries |

---

## 9. Summary: Phase 10 Design Principles

1. **Templates for structure, LLM for content.** Workflow phases and checkpoint placement are hardcoded in templates. The specific patches and diagnoses within each phase are LLM-generated from live state.

2. **Every phase boundary is a potential checkpoint.** The template declares whether each phase entry/exit requires user acknowledgment. The policy: any phase that mutates state and cannot be auto-validated requires an entry checkpoint.

3. **Goal-state-driven, not step-count-driven.** The workflow terminates when the goal state checker returns true, not when a fixed number of steps complete. This enables iteration (Eureka, debugging) and early exit (if the problem was simpler than expected).

4. **Blast radius is bounded.** Per-phase mutation limits, scope boundaries, subprocess quotas, and workflow-level rollback prevent runaway damage.

5. **Workflow state persists.** Checkpoint decisions, phase results, and retry counts survive Isaac Sim restarts. The user can resume interrupted workflows.

6. **Autonomous workflow is an overlay on the existing system.** The Patch Planner, Governance Engine, and Snapshot Manager are reused unchanged. The workflow orchestrator calls their endpoints; it does not replace them.

7. **Non-blocking monitoring.** Async phases (training runs, data generation) post progress updates to chat but do not block until they require a decision (anomaly detected, phase complete).

8. **Escalation is a first-class outcome.** Every workflow template must define an escalation path. Reaching max retries without goal state does not silently fail — it produces a repro bundle and a clear explanation.

---

*End of brainstorm. Next step: select W1 (RL pipeline) and W4 (debugging) as the Phase 10 MVP workflows, define their WorkflowTemplate data models concretely, and spec the WorkflowOrchestrator API endpoints.*
