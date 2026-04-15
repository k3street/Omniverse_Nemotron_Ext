# Cross-Cutting: Performance Review

**Agent:** Performance Review  
**Date:** 2026-04-15  
**Status:** Complete

## 1. GPU Memory Budget

| Component | VRAM (FP16) | VRAM (Q4) |
|---|---|---|
| Isaac Sim 5.1 base | 8–12 GB | — |
| Isaac Sim + sensors | 14–18 GB | — |
| LLM (qwen3.5:35b default) | ~22 GB | ~11 GB Q4 |
| TripoSR (6B) | ~6 GB | ~3 GB |
| Trellis 2 | 12–24 GB | — |
| IsaacLab RL (64 envs) | +3–6 GB | — |
| IsaacLab RL (1024 envs) | +15–30 GB | — |

**Critical:** Default LLM `qwen3.5:35b` + Isaac Sim = 36+ GB. OOM on all consumer GPUs. **Change default to 7B Q4 model.**

## 2. Chat Response Latency

- Simple USD operation (1 tool round): **6–18 seconds** (local 35B)
- Motion planning ("move arm to grab cup"): **15–35 seconds**
- Scene building (6A): **45–120 seconds** (3–5 tool rounds)
- Image-to-3D (6B): **10–30 seconds** (TripoSR)

**Kit RPC timeout hardcoded to 8 seconds** — will kill motion planning and large scene ops.

## 3. Viewport Capture Mismatch

PLAN.md says 512px. Code defaults to **1280px**. Cost overrun with cloud APIs.

## 4. Code Bugs Found

- **`replicate_physics=True` missing** in GridCloner codegen — difference between 5s and 60s+
- **Viewport capture 5-second polling loop** — naive busy-wait, could use event notification
- **SDG `run_until_complete()` blocks Kit UI** — freezes for large N
- **Tool calls execute serially** despite being collected in a list — parallelize independent tools

## 5. Recommended Hardware

| Scenario | Min GPU |
|---|---|
| Phase 1–5 (chat + USD) | RTX 4080 16 GB + 7B Q4 LLM |
| Phase 6B TripoSR | RTX 4090 24 GB |
| Phase 7A RL (1024 envs) | A100 80 GB |
| Full stack | Multi-GPU A100 cluster |

**Best single change:** Offload LLM inference to separate machine. FastAPI already runs as separate process.

## Sources
- [Isaac Sim Requirements](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
- [Isaac Sim Benchmarks](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/reference_material/benchmarks.html)
- [IsaacLab VRAM #3917](https://github.com/isaac-sim/IsaacLab/issues/3917)
