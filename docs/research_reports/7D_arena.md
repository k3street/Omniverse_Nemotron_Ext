# Phase 7D — IsaacLab-Arena Composable Environments: Critique

**Agent:** Research 7D Arena  
**Date:** 2026-04-15  
**Status:** Complete

## Project Status: Active and Real

- Repo: `github.com/isaac-sim/IsaacLab-Arena` — 362 stars, pushed today
- HuggingFace LeRobot integration live

## Critical Flaw: Tool Interface vs. Reality

Arena is **compile-time composition** (Scene + Embodiment + Task → config → Gymnasium env), not runtime imperative. There is no mutable arena handle. `add_arena_robot` to a live sim is not possible — env is fixed after `env.reset()`.

## Heterogeneous Robots Not Supported

NVIDIA explicitly states: "supports homogeneous parallel environments." Different robot morphologies break the batched tensor assumption. Listed as "near future."

## Recommendations

- **7D.1:** Reframe `create_arena` as config generation returning env_id
- **7D.2:** Drop `add_arena_robot` or redesign as `create_arena_variant`
- **7D.3:** Scope as sequential per-robot benchmarks, not simultaneous
- **7D.4:** Leaderboard is post-hoc aggregation, not live competition

## Sources
- [IsaacLab-Arena GitHub](https://github.com/isaac-sim/IsaacLab-Arena)
- [NVIDIA Developer Blog](https://developer.nvidia.com/blog/simplify-generalist-robot-policy-evaluation-in-simulation-with-nvidia-isaac-lab-arena/)
- [HARL-A (heterogeneous multi-agent)](https://github.com/DIRECTLab/IsaacLab-HARL)
