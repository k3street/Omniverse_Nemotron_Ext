# Phase 7G — GR00T N1 Foundation Policy Evaluation: Critique

**Agent:** Research 7G GR00T N1  
**Date:** 2026-04-15  
**Status:** Complete

## Key Facts

- **Fully open**: HuggingFace weights + GitHub scripts. Not API-locked.
- **Current version:** GR00T N1.6 (3B params), not N1.
- **Supports:** Not just humanoids — Panda, WidowX, G1 all natively supported.
- **No native Kit extension** — runs as separate policy server process.

## Hardware Requirements

- Inference: **24 GB+ VRAM** — RTX 5070 (12 GB) insufficient
- Fine-tuning: **25–48 GB VRAM** (H100/L40/A6000 class)
- 7G.3 is functionally dependent on 7H (cloud compute)

## Fine-tuning Constraint

Requires **LeRobot v2 format**. Phase 7C.3 must output this format (not USD TimeSamples).

## GR00T vs RL Comparison

Methodologically different observation/action spaces. Frame as capability-profile comparison, not head-to-head benchmark.

## Sources
- [NVIDIA/Isaac-GR00T](https://github.com/NVIDIA/Isaac-GR00T)
- [nvidia/GR00T-N1.6-3B](https://huggingface.co/nvidia/GR00T-N1.6-3B)
- [IsaacLabEvalTasks](https://github.com/isaac-sim/IsaacLabEvalTasks)
