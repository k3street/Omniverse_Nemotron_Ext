# Phase 7E — Eureka LLM Reward Generation: Critique

**Agent:** Research 7E Eureka Rewards  
**Date:** 2026-04-15  
**Status:** Complete

## 1. Wrong Repo URL

`isaac-sim/Eureka` does not exist. Correct: `isaac-sim/IsaacLabEureka`.

## 2. Algorithm Description Is Misleading

Not "generate K, select, mutate" — it's greedy-best-only. No crossover, no population. REvolve (ICLR 2025) adds real evolutionary operators.

## 3. Hard Interface Constraint

IsaacLabEureka only works with `DirectRLEnv`. The full environment source code must be injected into the LLM context. `generate_reward` signature must include this.

## 4. Known Failure Modes

- Reward component imbalance
- Reward desert initialization
- Specification gaming
- Static specification problem

## 5. Computational Cost

Each Eureka run: hours of GPU time. Not interactive. Must be async with progress streaming.

## 6. Better Alternatives in 2026

- **DrEureka** (RSS 2024) — also generates domain randomization
- **REvolve** (ICLR 2025) — true population-based evolution + human feedback
- **MIRA** (MDPI 2025) — dual-loop metacognitive architecture

## Sources
- [isaac-sim/IsaacLabEureka](https://github.com/isaac-sim/IsaacLabEureka)
- [DrEureka](https://eureka-research.github.io/dr-eureka/)
- [REvolve (ICLR 2025)](https://openreview.net/forum?id=cJPUpL8mOw)
