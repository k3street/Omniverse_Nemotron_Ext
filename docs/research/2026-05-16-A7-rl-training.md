# A7 — RL-Training Canonical Draft Decision

**Date:** 2026-05-16
**Agent:** A7
**Template:** `CP-NEW-eureka-pick-place-reward`

---

## §1 Candidate + Measurable Success Criterion

**Backlog ID:** `rl-eureka-reward-pick-place-001`
- Priority tier 1, no blockers, Franka only, complexity: medium
- Title: "Eureka reward shaping — automated pick-place reward function"

**Success criterion:**
`evaluate_reward(reward_id=..., n_eval_episodes=5)` returns a dict with key `"score"`.
SUCCESS = `score >= 0.50` (normalized 0-1 scale). This float is written to
`/tmp/eureka_pp_result.json` and checkpointed to `/tmp/eureka_pp_checkpoint/`.
The assertion `assert eval_result["score"] >= 0.50` in the code block is the
gate that determines PASS or FAIL. The deliverable is a file on disk.

**Why this candidate over others:**
- `rl-isaaclab-cartpole-baseline-001` (tier 1, simple) has blocker `isaac_lab_install_required`
- `rl-parallel-env-scaling-32-001` (tier 2) measures throughput — "GPU utilization > X" is
  not a meaningful function-gate for a canonical delivery test
- `rl-eureka-reward-pick-place-001` (tier 1, no blockers) has the Eureka `evaluate_reward`
  handler which returns a concrete numeric score — the clearest delivery criterion in the
  rl-training category

---

## §2 Avoiding `train_pattern_no_delivery_criterion`

The three deferred templates fail the delivery-criterion test in different ways:

| Template | Why deferred |
|---|---|
| `CP-NEW-rl-clone-env` | Success = "reward curve trends upward over 60s" — qualitative, not a threshold test |
| `CP-NEW-sim2real-gap` | Success = JSON gap-metric file written — no accept/reject threshold |
| `CP-NEW-defect-sdg` | No robot; no delivery; COCO output validation only |

`CP-NEW-eureka-pick-place-reward` avoids this because:
1. `evaluate_reward` is a **handler that returns a dict**, not a side-effect.
2. The dict's `"score"` key is a **float with a defined threshold** (>= 0.50).
3. The code contains an explicit `assert score >= threshold` gate.
4. The deliverable is `/tmp/eureka_pp_result.json` — a file that exists or does not exist,
   independently checkable by `os.path.exists(...)`.
5. `checkpoint_training` saves the reward code itself — this is the exported artifact,
   analogous to "cube in bin" for pick-place canonicals.

The success criterion is therefore **measurable, boolean, and handler-derived** — not a
visual judgment call or a "pipeline ran without crashing" assertion.

---

## §3 Roles + Structural Tags Chosen

**Roles (3 roles):**

| Role | Purpose |
|---|---|
| `primary_robot` | Franka Panda arm — the robot being trained |
| `training_environment` | IsaacLab Gym env — the rollout environment Eureka evaluates against |
| `success_metric` | Numeric threshold — `evaluate_reward score >= 0.50` |

The `success_metric` role is novel for the rl-training category. It makes the threshold
explicit in `role_defaults` so consumers can filter by it or override it without touching code.

**Pattern hint:** `train` (valid per R12 schema update, 2026-05-16)

**Structural tags:**
- `isaac:rl.eureka_reward` — discriminates from PPO/SAC training canonicals
- `isaac:rl.reward_shaping` — retrieval signal for reward-engineering queries
- `isaac:topology.single_station` — single arm, no conveyor
- `isaac:robot.fixed_base.arm` — controller-filter compatible
- `isaac:training.eureka_loop` — generate→iterate→evaluate workflow signal

---

## §4 Form-Gate Result

```
workspace/templates/CP-NEW-eureka-pick-place-reward.json: OK

1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Command: `python scripts/lint_canonical_templates.py workspace/templates/CP-NEW-eureka-pick-place-reward.json`

No errors. No warnings. Template passes all C1–C4, T1, R1–R3, motion_controllers,
and intent rules.
