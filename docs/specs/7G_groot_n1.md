# Phase 7G — GR00T N1 Foundation Policy Evaluation

**Status:** Not implemented  
**Depends on:** Phase 7A (deploy_policy), Phase 7C (teleop demos for fine-tuning)  
**Research:** `research_reports/7G_groot_n1.md`

---

## Overview

Deploy NVIDIA GR00T N1.6 foundation robot policies in Isaac Sim, benchmark on tasks, optionally fine-tune on teleop demonstrations.

**Key facts:**
- Model is fully open: HuggingFace weights (`nvidia/GR00T-N1.6-3B`) + GitHub scripts (`NVIDIA/Isaac-GR00T`)
- Current version: **N1.6** (3B params), not N1
- Supports: Panda, WidowX, Unitree G1, custom embodiments — not humanoid-only
- **No native Kit extension** — runs as separate policy server process
- **24 GB+ VRAM** for inference — RTX 5070 (12 GB) insufficient

---

## Tools

### 7G.1 `load_groot_policy(model_id, robot_path)`

**Type:** Process orchestration (NOT a Kit RPC call)

**Implementation:**
1. Download checkpoint via `huggingface_hub.snapshot_download("nvidia/GR00T-N1.6-3B")`
2. Launch GR00T policy server as subprocess (from `Isaac-GR00T/scripts/deployment/`)
3. Configure network bridge between policy server and Isaac Sim
4. Load appropriate embodiment config (pre-registered: `LIBERO_PANDA`, `OXE_WIDOWX`, `UNITREE_G1`, or custom)

**Hardware gate:** If local VRAM < 24 GB, return error with recommendation to use cloud compute (Phase 7H) or a remote GPU.

### 7G.2 `evaluate_groot(model_id, task, num_episodes)`

**Type:** Subprocess — uses IsaacLabEvalTasks for closed-loop evaluation

**Scope:** Pre-built evaluation tasks (IsaacLabEvalTasks), NOT arbitrary user scenes. Custom scene support is a stretch goal requiring custom IsaacLab task environment authoring.

**Returns:** `{success_rate, task_metrics, num_episodes, model_id}`

### 7G.3 `finetune_groot(model_id, demo_data, num_steps)`

**Type:** Subprocess (long-running, async)

**Prerequisites:**
- Demo data in **LeRobot v2 format** (Phase 7C.3 must output HDF5 → convert to LeRobot v2)
- **25-48 GB VRAM** — requires cloud compute (Phase 7H) or A6000/H100 class GPU
- LoRA fine-tuning possible on 2× RTX 4090

**This task is functionally dependent on Phase 7H (cloud compute)** unless user has high-end local hardware.

### 7G.4 Comparison Dashboard

**Framing:** Capability-profile comparison, NOT head-to-head benchmark.

GR00T (VLA model, vision+language input, generalization) and custom RL (proprioceptive, task-specific, asymptotic performance) solve problems differently. Present as "where each approach shines":

| Dimension | GR00T N1.6 | Custom RL |
|-----------|-----------|-----------|
| Zero-shot generalization | Strong | None |
| Single-task performance | Moderate | Strong |
| Training data needed | Demonstrations | Reward function |
| Observation type | RGB + language | Proprioceptive state |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Checkpoint download | L0 | Mock huggingface_hub, verify path logic |
| Embodiment config lookup | L0 | Verify pre-registered names |
| VRAM check | L0 | Verify hardware gate logic |
| Policy server launch | L1 | Mock subprocess |
| Full evaluation | L3 | Requires GPU + IsaacLabEvalTasks |

## Known Limitations

- 24 GB+ VRAM minimum for inference
- Fine-tuning needs cloud GPU (25-48 GB)
- LeRobot v2 format conversion required from HDF5 demos
- Zero-shot performance is limited — model is a fine-tuning base, not a drop-in generalist
- Documentation for sim+GR00T integration is acknowledged as incomplete by NVIDIA
