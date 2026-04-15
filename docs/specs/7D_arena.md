# Phase 7D — IsaacLab-Arena Composable Environments

**Status:** Not implemented  
**Depends on:** Phase 7A (launch_training, deploy_policy)  
**Research:** `research_reports/7D_arena.md`

---

## Overview

One-command benchmark evaluation using IsaacLab-Arena's composable Scene + Embodiment + Task architecture.

**Critical architectural correction:** Arena is **compile-time composition**, not runtime imperative. There is no mutable arena handle. Environments are fixed after `env.reset()`.

**Heterogeneous robots (different morphologies) in the same env are NOT supported** — NVIDIA lists this as "near future." Benchmarking uses sequential runs with external comparison.

---

## Tools (Corrected from PLAN.md)

### 7D.1 `create_arena(scene_type, robot_asset, task, num_envs)` → `env_id`

**Correction:** Single `robot_asset`, not plural `robots`. Returns a Gymnasium env_id string.

**Implementation:** Generates an Arena config using `ArenaEnvBuilder.combine_configclass_instances()`:
```python
from isaaclab_arena import ArenaEnvBuilder
env_cfg = ArenaEnvBuilder.combine(scene_cfg, embodiment_cfg, task_cfg)
gymnasium.register(id=env_id, entry_point=..., kwargs={"env_cfg_entry_point": env_cfg})
```

**Available scenes (real, not speculated):** tabletop pick-and-place, kitchen, Galileo environment. Maze/warehouse/obstacle_course require custom USD scenes.

### 7D.2 `create_arena_variant(base_env_id, robot_asset)` → `variant_env_id`

**Replaces `add_arena_robot`.** Creates a new config with a different embodiment for sequential comparison. Each variant is a separate simulation launch (~30-90s overhead).

### 7D.3 `run_arena_benchmark(env_id, num_episodes, metrics)` → results

**Type:** Subprocess launch. Each call = separate IsaacLab process.

**Returns:** `{success_rate, object_moved, episode_length, custom_metrics}`

Cross-robot comparison: loop over `run_arena_benchmark` calls, aggregate externally.

### 7D.4 Leaderboard View

Post-hoc aggregation of sequential benchmark runs. Render as formatted table in chat.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Config generation | L0 | Verify ArenaEnvBuilder output structure |
| Leaderboard formatting | L0 | Table rendering from results dict |
| Full benchmark | L3 | Requires IsaacLab + Arena + GPU |

## Known Limitations

- Heterogeneous robots not supported (same observation/action space required)
- Each benchmark run = separate Isaac Sim process launch (30-90s overhead)
- Arena has limited built-in scenes — custom scenes need USD authoring
