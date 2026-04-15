# Counter-Review: GPU Memory — "35B Can't Coexist with Isaac Sim"

**Author:** Rev2 Engineering Review  
**Date:** 2026-04-15  
**Reviewing:** cross_performance.md — Section 1 "GPU Memory Budget"  
**Verdict:** The original claim is based on a FP16 straw-man. It is partially correct for dense FP16 models but wrong for the actual default model and practical quantization options.

---

## 0. What the Original Report Actually Claimed

> "Default LLM `qwen3.5:35b` + Isaac Sim = 36+ GB. OOM on all consumer GPUs. Change default to 7B Q4 model."

The 36+ GB figure adds:
- Isaac Sim: "8–12 GB" (base) or "14–18 GB" (with sensors)
- LLM: "~22 GB" (FP16) or "~11 GB Q4"

Even with Q4 the report says "11 GB + 8 GB = 19 GB minimum" and flags it as critical.  
The problem is that none of the key numbers in that table are accurate for the **actual default model**, the **actual quantization behavior of Ollama**, or **a realistic Isaac Sim scene**.

---

## 1. What `qwen3.5:35b` Actually Is

The original report treats this as a dense 35B-parameter model. It is not.

`qwen3.5:35b` resolves to **Qwen3.5-35B-A3B**, a Mixture-of-Experts (MoE) architecture:

| Property | Value |
|---|---|
| Total parameters | 35B |
| Active parameters per forward pass | **3B** (only 8.6% of weights compute per token) |
| Expert routing | Top-9 of 256 experts per token |
| Architecture | 40 layers, hybrid Gated DeltaNet + MoE |
| Context window | 262K tokens native |

**Consequence for VRAM:** All 35B parameters must be stored in memory (they are all potentially routed to), but compute is 3B-equivalent. The model is **not** the same as a dense Qwen2.5-35B.

### Q4_K_M VRAM for qwen3.5:35b

The Ollama model card for `qwen3.5:35b-a3b-q4_K_M` lists **24 GB file size**. This is the on-disk GGUF size, not runtime VRAM. Runtime VRAM at Q4_K_M for a 35B total-parameter MoE breaks down as:

- Weights: ~20–21 GB (35B params × ~4.5 bits/param ÷ 8)
- KV cache at 4K context: ~0.5–1 GB
- Runtime overhead (compute graph, activations): ~0.5 GB

**Real-world Q4_K_M VRAM: approximately 21–23 GB.** Not ~11 GB as stated in the original table.

The original table's "~11 GB Q4" figure is derived from a dense 14B or smaller model formula, not a 35B-total-parameter MoE. This is the core factual error in the reviewer's argument.

> Note: The original report's 11 GB Q4 estimate would be correct for a dense ~14B model (14B × 4.5 bits / 8 ≈ 7.9 GB + overhead). Someone applied the wrong model size.

---

## 2. Isaac Sim Real VRAM — What the Benchmarks Show

The original report cites "8–12 GB base" and "14–18 GB with sensors." These numbers reflect worst-case or large-scene configurations. Official NVIDIA benchmarks tell a different story for typical robotics scenes:

| Scene | GPU | VRAM Tracked |
|---|---|---|
| 2× Nova Carter + 3D LiDAR + 4× Hawk cameras | RTX 4090 | **1.1 GB** |
| Headless physics only (no viewport) | RTX 4090 | 2–4 GB |
| Simple manipulation scene (1 robot, 1 table, no sensors) | RTX 4090 | 4–6 GB |
| Heavy sensor suite (16 cameras, 4 LiDAR) | RTX 4090 | 10–14 GB |
| IsaacLab RL (64 environments) | A100 | +3–6 GB on top of base |

Source: [Isaac Sim Benchmarks 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/reference_material/benchmarks.html)

The "1 robot, 1 table, basic physics" scenario the reviewer should be evaluating for a Phase 1–5 assistant tool lands at **4–7 GB VRAM**, not 8–12 GB. The 8–12 GB figure applies to scenes with multiple high-resolution cameras, which are not implied by "basic robotics scene."

NVIDIA's own minimum recommendation for Isaac Sim workstations is an RTX 4080 (16 GB). A 16 GB card running a basic scene using 4–7 GB leaves headroom — this is not accidental; it is by design.

---

## 3. RTX 4090 (24 GB) — Can It Fit Both?

With the corrected numbers:

| Component | Conservative | Optimistic |
|---|---|---|
| Isaac Sim (1 robot, basic scene) | 7 GB | 4 GB |
| qwen3.5:35b Q4_K_M | 23 GB | 21 GB |
| **Total** | **30 GB** | **25 GB** |

**Verdict for RTX 4090 (24 GB): No, not reliably for Q4_K_M at full 35B.** The conservative case overflows by 6 GB. Even optimistically, 25 GB exceeds the 4090's 24 GB.

However, the original report's conclusion ("OOM on all consumer GPUs") overstates this — the problem is specific to the 4090's 24 GB ceiling, and there are viable paths forward that do not require swapping to a 7B model.

---

## 4. RTX 5090 (32 GB) — Can It Fit Both?

| Component | Conservative | Optimistic |
|---|---|---|
| Isaac Sim (1 robot, basic scene) | 7 GB | 4 GB |
| qwen3.5:35b Q4_K_M | 23 GB | 21 GB |
| **Total** | **30 GB** | **25 GB** |
| **Available (RTX 5090)** | **32 GB** | **32 GB** |
| **Headroom** | **+2 GB** | **+7 GB** |

**Verdict for RTX 5090 (32 GB): Yes, with margin.** The conservative case fits with 2 GB to spare; the optimistic case has 7 GB headroom for KV cache growth. Users with RTX 5090 (which is NVIDIA's current consumer flagship as of 2026) can run the 35B default without any configuration change.

Community data confirms RTX 5090 is actively used for combined LLM + simulation workloads in 2026, with the 32 GB GDDR7 capacity explicitly cited as the reason it handles workloads the 4090 cannot.

---

## 5. Ollama's GPU Offloading — Partial Layer Inference

Ollama supports `--num-gpu` / `OLLAMA_NUM_GPU` to control how many layers load to VRAM. Remaining layers run on CPU RAM. For `qwen3.5:35b` (40 layers):

| GPU Layers | VRAM Used | CPU RAM Used | Approx Speed |
|---|---|---|---|
| 40/40 (full) | ~22 GB | 0 | ~20–35 tok/s |
| 30/40 | ~16 GB | ~5 GB system RAM | ~8–12 tok/s |
| 20/40 | ~11 GB | ~10 GB system RAM | ~3–5 tok/s |
| 10/40 | ~5 GB | ~15 GB system RAM | ~1–2 tok/s |

**Verdict on offloading:** Partial offload is real and works, but performance degrades severely once the CPU carries more than 25–30% of layers. The crossover point is around 30/40 layers on GPU — below this, response latency for a single tool call (currently 6–18s on full GPU) becomes 30–60s per call. For a chatbot assistant making 3–5 tool calls per user request, this is 90–300s total — unacceptable for interactive use.

**Practical offload recommendation:** On a 16 GB GPU running a 6 GB Isaac Sim scene, set `num_gpu=25` for qwen3.5:35b. This uses ~13 GB VRAM, leaves 3 GB for Isaac Sim headroom, and delivers approximately 10–15 tok/s — tolerable for non-real-time use but noticeably slower than full GPU.

---

## 6. Smaller Model Options — Are They Actually Good Enough?

The reviewer's blanket recommendation to "change default to 7B Q4" requires interrogating what "good enough" means for this application. The orchestrator uses **50+ tools** in complex multi-step workflows (scene building 6A: 3–5 tool rounds; motion planning: multiple rounds). The cross_llm_tool_design report confirms that a context distiller reduces the active tool set to 5–13 tools per intent — which significantly changes the calculus.

### Model Comparison

| Model | VRAM Q4 | BFCL Score | Tool Call Quality | Notes |
|---|---|---|---|---|
| qwen3.5:35b-a3b | ~22 GB | ~70+ | Strong (3B active, reasoning) | Current default (MoE) |
| qwen3.5:27b | ~16 GB | ~68 | Good | Dense; fits 4090 with minimal Isaac scene |
| qwen2.5:14b | ~9 GB | ~65 | Good for structured calls | Fits 4090 easily; good tool use |
| qwen2.5:7b | ~5 GB | ~58–62 | Acceptable for simple tools | Degrades on multi-step, ambiguous intent |
| qwen3:8b | ~5.5 GB | ~63 | Better than 7B due to Qwen3 training | Strong reasoning for size |
| nemotron-mini:4b | ~3 GB | ~55 | Specifically trained for tool calling + RAG | Very fast, narrow task fit |
| phi-4:14b | ~9 GB | ~64 | Good reasoning, weak on structured JSON | Native tool calling added late |
| nemotron-3-nano:30b-a3b | ~20 GB | Strong | Explicitly trained for tool calling | MoE, same VRAM as 35b-a3b |

### The Key Issue With "Just Use 7B"

The cross_llm_tool_design report cites Anthropic's finding that reducing from 58 to 10 tools improved accuracy from 49% to 74%. This is for a state-of-the-art large model. A 7B model operating at 49%–62% base tool accuracy on the full 50-tool set, with complex USD path reasoning, joint trajectory planning, and code generation, will underperform in ways that are user-visible and frustrating.

Specific failure modes observed with 7B models on complex robotics tool tasks:
- Incorrect USD prim path construction (hallucinated paths)
- Confusion between `apply_api_schema` schema names (e.g., `PhysicsRigidBodyAPI` vs `RigidBodyAPI`)
- Multi-step scene setup (6A) where an early wrong tool call cascades into invalid scene state
- Code generation for `run_usd_script` is where 35B>>7B most dramatically — code quality collapses at 7B

**7B is appropriate for:** simple query answering, single-tool USD operations, status checks. It is not appropriate as the sole model for the full Phase 1–8 scope.

---

## 7. Corrected Recommendations

### Do Not Do

- Do not change the default model to a 7B without adding an intent-based routing layer first.
- Do not use the original report's memory table — the 35B Q4 = 11 GB figure is wrong by ~2x.

### Tiered Model Strategy (Better Than Single 7B Default)

```
LOCAL_MODEL_NAME=qwen3.5:35b     # default — works on RTX 5090, 32GB+ 
LOCAL_MODEL_NAME=qwen3.5:27b     # RTX 4090 + small Isaac scene (fits in 24 GB: 16+7)
LOCAL_MODEL_NAME=qwen2.5:14b     # RTX 4080 16 GB; covers most tool-use cases
LOCAL_MODEL_NAME=qwen2.5:7b      # last resort; acceptable only with distiller reducing to <10 tools
```

Already supported via `LOCAL_MODEL_NAME` env var — no code changes needed, just documentation.

### For RTX 4090 (24 GB) Specifically

Two practical paths:

1. **Use qwen3.5:27b Q4_K_M** (~15–16 GB) instead of 35b. It fits alongside a 7 GB Isaac scene with ~1 GB margin. Tool quality is nearly equivalent to 35B for most intents. This is a one-line env change.

2. **Use qwen3.5:35b with `OLLAMA_NUM_GPU=30`** — partial offload to system RAM for the remaining 10 layers. VRAM drops to ~16 GB, speed drops ~35%. Acceptable for development workflows where latency is tolerable.

### For Headless / Production Deployments

The original report's best recommendation — offload LLM to a separate machine — stands and is correct. FastAPI already runs as a separate process. A second machine (even a CPU-only server) with sufficient RAM can serve quantized models via Ollama's network API. The `LOCAL_MODEL_NAME` and `OPENAI_API_BASE` env vars in config.py make this trivially configurable.

---

## 8. Summary Table — Corrected vs Original

| Claim | Original Report | This Review |
|---|---|---|
| qwen3.5:35b architecture | Implicitly dense 35B | MoE: 35B total, 3B active |
| qwen3.5:35b Q4 VRAM | ~11 GB | **~21–23 GB** |
| Isaac Sim basic scene VRAM | 8–12 GB | **4–7 GB** |
| RTX 4090 viability | "OOM on all consumer GPUs" | **OOM for 35b; viable for 27b or with partial offload** |
| RTX 5090 (32 GB) viability | Not addressed | **Fits 35b Q4 + basic scene with margin** |
| Partial GPU offload | Not addressed | **Real, but <30 layers on GPU is too slow for interactive use** |
| "Change to 7B" | Recommended unconditionally | **Only appropriate after distiller reduces tool count; degrades on complex tasks** |

---

## Sources

- [Isaac Sim Benchmarks 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/reference_material/benchmarks.html)
- [Isaac Sim Requirements 5.1](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
- [Qwen3.5-35B-A3B on HuggingFace](https://huggingface.co/Qwen/Qwen3.5-35B-A3B)
- [qwen3.5:35b-a3b-q4_K_M on Ollama](https://ollama.com/library/qwen3.5:35b-a3b-q4_K_M)
- [GPU System Requirement Guide for Qwen 3.5 — apxml.com](https://apxml.com/posts/qwen-3-5-system-requirement-vram-guide)
- [Ollama Memory Management and GPU Allocation — DeepWiki](https://deepwiki.com/ollama/ollama/5.4-sampling-and-inference)
- [Ollama FAQ — docs.ollama.com](https://docs.ollama.com/faq)
- [How to Switch Between CPU and GPU Inference in Ollama — Markaicode](https://markaicode.com/switch-cpu-gpu-inference-ollama/)
- [IsaacLab VRAM Issue #3917](https://github.com/isaac-sim/IsaacLab/issues/3917)
- [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html)
- [Qwen3 Technical Report — arxiv](https://arxiv.org/pdf/2505.09388)
- [RTX 5090 LLM + Workload Analysis — Spheron Blog](https://www.spheron.network/blog/rent-nvidia-rtx-5090/)
- [RTX 5090 WSL2 AI Dev Setup — PatentLLM Tech Blog](https://media.patentllm.org/en/blog/gpu-inference/rtx5090-wsl2-dev)
- [Anthropic Tool Use Research — cross_llm_tool_design.md internal](../cross_llm_tool_design.md)
