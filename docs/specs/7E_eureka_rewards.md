# Phase 7E — LLM Reward Generation (Eureka)

**Status:** Not implemented  
**Depends on:** Phase 7A (create_isaaclab_env, launch_training — bugs must be fixed first)  
**Research:** `research_reports/7E_eureka_rewards.md`, `rev2/research_7E_approaches.md`

---

## Overview

User describes a robot task → LLM generates reward function → trains RL policy → user gives feedback → LLM refines reward → iterates.

**Correct repo:** `isaac-sim/IsaacLabEureka` (NOT `isaac-sim/Eureka` which doesn't exist)

---

## Architecture: Phased Approach

### V1 — MVP: IsaacLabEureka + Feedback Injection

IsaacLabEureka is the only approach natively compatible with IsaacLab's `DirectRLEnv`.

**Algorithm (correctly described):**
1. Generate K reward function candidates (K=4-8 for workstation, not paper's K=16)
2. Train each candidate for fixed N steps
3. **Greedy-select the best** by fitness metric (NOT evolutionary — no crossover, no population)
4. Feed winning reward code + per-component training metrics back to LLM
5. LLM generates next K candidates informed by reflection
6. Repeat for 5-10 iterations

**User feedback injection:** Convert chat text ("it keeps dropping the handle") into a structured mutation instruction appended to the Eureka reflection prompt.

```python
def user_critique_to_mutation_prompt(critique: str, prev_reward: str, metrics: dict) -> str:
    return f"""
    Previous reward function:
    {prev_reward}

    Training metrics per component:
    {format_component_metrics(metrics)}

    User feedback: {critique}

    Based on this feedback, modify the reward function to address the issue.
    """
```

### V2 — Top-N Survivors

Replace greedy-best-only with top-N survivors across iterations (REvolve's insight). ~20-line change to the selection logic. Maintains diversity in the reward population.

### V3 — Domain Randomization (if sim-to-real needed)

Port DrEureka's RAPP mechanism from IsaacGym to IsaacLab. Generates physics-parameter DR distributions alongside the reward. ~2-4 weeks porting effort. Blocked until IsaacGym→IsaacLab migration is done.

---

## Tools

### 7E.1 `generate_reward(task_description, env_source_path)`

**Type:** DATA handler

**Critical:** Must inject full environment source code into LLM context (not just task description + obs space). IsaacLabEureka reads the raw Python source of the env file.

**Parameters:**
- `task_description` (string): NL task description
- `env_source_path` (string): Path to the `DirectRLEnv` Python file
- `num_candidates` (int, default 4): K candidates per iteration
- `num_iterations` (int, default 5): Eureka iterations

**Constraint:** Only works with `DirectRLEnv` environments. `ManagerBasedRLEnv` is NOT supported by IsaacLabEureka.

### 7E.2 `evaluate_reward(reward_code, env, num_episodes)`

**Type:** Subprocess (async, long-running)

**Must return per-component timeseries**, not just aggregate fitness:
```json
{
  "fitness": 0.85,
  "components": {
    "grasp_reward": {"mean": [0.1, 0.3, 0.5, ...], "converged": true},
    "orientation_reward": {"mean": [0.0, 0.01, 0.02, ...], "converged": false}
  },
  "task_success_rate": 0.42,
  "status": "completed"
}
```

**Task success criterion must be defined independently of the reward** — otherwise no signal to detect reward hacking.

### 7E.3 `iterate_reward(prev_reward_code, component_timeseries, task_success_rate, user_feedback)`

**Corrected signature** — must pass per-component data, not just "training curves."

### 7E.4 User Flow (realistic)

```
User: "teach my robot to open the cabinet"
→ LLM generates env if not exists (7A)
→ generate_reward(task_description, env_path, num_candidates=4)
→ [4-8 hours of training across 4 candidates × 5 iterations]
→ show_training_metrics(run_id) — streamed via SSE
→ User: "it keeps dropping the handle"
→ iterate_reward(prev_code, metrics, success_rate, "it keeps dropping the handle")
→ [another training cycle]
```

**Note:** This is NOT interactive/real-time. Each cycle is hours. The UX must make this clear with progress streaming.

---

## Computational Requirements

- K=4 candidates × 1000 RL steps each × 5 iterations = 20,000 total training runs
- Single RTX 4090: ~4-8 hours per full Eureka run
- 8×A100 (paper setup): ~1-2 hours
- **Training is always async** — stream progress to chat via SSE

---

## Prerequisites

1. Fix 7A bugs (IsaacLabEureka will crash without working env code generation)
2. Decide: all 7E tasks must be `DirectRLEnv` (ManagerBased excluded)
3. Define task success criteria independently per task type

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Reward code injection into prompt | L0 | Verify env source included |
| User feedback → mutation prompt | L0 | String formatting correctness |
| Component timeseries parsing | L0 | JSON structure validation |
| Candidate selection (greedy) | L0 | Verify best-of-K logic |
| Full Eureka run | L3 | Requires IsaacLab + GPU, hours |

## Known Limitations

- `DirectRLEnv` only — most IsaacLab built-in tasks use ManagerBased
- Hours per run — not interactive
- Greedy selection loses diversity (V2 addresses this)
- No sim-to-real DR generation until V3
- LLM has biases in reward design — can converge on wrong behavior
