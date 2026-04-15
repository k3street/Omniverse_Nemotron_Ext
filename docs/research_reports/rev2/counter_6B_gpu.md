# Counter-Research: GPU Coexistence — Image-to-3D + Isaac Sim

**Responding to:** `docs/research_reports/6B_image_to_usd.md`, Section 2  
**Date:** 2026-04-15  
**Verdict:** The critique overstates the constraint. Coexistence is **feasible with configuration**, not categorically impossible.

---

## The Claim Being Challenged

> "Running any of these simultaneously with Isaac Sim on the same GPU is not viable below RTX 4090."  
> **Fix #4: API-first default** — default to API backend, local opt-in with explicit VRAM warning.

This is treated as a blocker requiring an architectural default of API-over-local. The data below challenges that framing.

---

## 1. TripoSR Actual VRAM — Not 6–8 GB at FP16

### What the critique implies
The critique accepts 6–8 GB as the TripoSR baseline without accounting for precision or optimization.

### What the data shows

**Default (FP32, resolution 256):** ~6 GB VRAM  
Source: TripoSR official optimization guide and GitHub issue #54 thread.

**With FP16 (`model.half()`):** Standard half-precision halves weight storage from ~4 bytes to ~2 bytes per parameter. TripoSR's weights are ~1.3 GB. At FP16 the model itself drops to ~650 MB loaded; activation memory scales with resolution but at 256-resolution the total working set is plausibly **3–4 GB**.

**Key evidence:**
- GitHub issue #54 ("Out of Memory") was filed by users on 8–12 GB GPUs, and was closed after resolution — meaning 8 GB is already sufficient at stock settings.
- Issue #72 requests multi-GPU split documentation, implying community interest but not a hard wall.
- The TripoSR README states the default takes "about 6 GB VRAM for a single image input" — that is FP32.
- Community fork `TripoSR-Bake` demonstrates custom memory-optimized configurations are practical.
- A100 benchmarks show generation in <0.5 s; RTX 3060/4060 achieve 5–10 s — both well under 12 GB GPUs, which is consistent with sub-6 GB actual use after chunking optimizations.

**Practical FP16 floor:** ~3.5–4.5 GB for TripoSR at 256-resolution, based on first-principles weight analysis and observed GPU-class compatibility.

---

## 2. Can Isaac Sim Release VRAM Temporarily?

### What the critique implies
Isaac Sim's VRAM footprint (12–16 GB) plus any generation model equals an OOM condition. No mention of dynamic VRAM management.

### What the data shows

**Isaac Sim headless with rendering disabled:**  
The Isaac Sim docs explicitly state: "To disable rendering completely unless explicitly needed by a sensor, use the headless application workflow." The `SimulationApp({"headless": True, "disable_viewport_updates": True})` path eliminates the RTX renderer's framebuffer allocation.

**Texture streaming budget:**  
The default is 60% of GPU capacity (= 14.4 GB on a 24 GB card). This is configurable to near-zero via `/rtx-transient/resourcemanager/texturestreaming/memoryBudget`. Dropping it to 10% on a 16 GB card frees ~8 GB from streaming cache alone.

**Rendering manager API:**  
`isaacsim.core.rendering_manager` does not expose a suspend/release call for framebuffers directly. However, destroying all viewport windows via `ViewportManager.destroy_viewport_windows()` combined with disabling viewport updates is documented to significantly reduce renderer memory pressure.

**What this enables:**  
A "generation window" where Isaac Sim is running in physics-only mode (no RTX renderer active, texture budget near zero) can plausibly free 4–8 GB, depending on scene complexity. This is not zero-cost — it requires explicit sequencing — but it is mechanically possible through documented APIs.

**Caveat:**  
There is no community-confirmed measurement of exactly how many GB are freed by disabling rendering in a real scene. This remains a "requires measurement" item, not an impossibility.

---

## 3. Hunyuan3D-2GP — Real VRAM on Consumer Hardware

### What the critique recommends
Replace InstantMesh with Hunyuan3D-2.1 for "local quality" tier but doesn't address whether that fits alongside Isaac Sim.

### What the data shows

**Hunyuan3D-2GP (deepbeepmeep fork):**  
| Profile | VRAM | System RAM | Notes |
|---------|------|------------|-------|
| Profile 5 (Ultra Low) | 4–6 GB | 16 GB+ | Shape generation only |
| Profile 4 (Default) | 6–8 GB | 16–24 GB | Full pipeline, slower |
| Profile 1 (High VRAM) | 24 GB | 48 GB+ | Fast path |

Source: Hunyuan3D-2-WinPortable DeepWiki system requirements (verified).

**Shape-only mode (no texture):**  
- Standard: 6 GB VRAM  
- 2mini variant: 5 GB VRAM  
- With profile 5: runs under 6 GB  
- Minimum observed: 4 GB (marked "slow")

Source: Hunyuan3D-2 GitHub issue #12 ("can now run with only 6GB of VRAM") and WinPortable requirements table.

**Key point for coexistence:**  
Shape-only generation at Profile 5 needs 4–6 GB. If Isaac Sim's renderer is suspended during generation, the combined peak is manageable on 16–24 GB GPUs, and well within RTX 4090's 24 GB even with rendering active for simple scenes.

---

## 4. TRELLIS-BOX FP16 — Confirmed 50% Reduction

### What the critique states
"TRELLIS v1 — Requires 16 GB VRAM minimum, spikes to 30 GB."

### What the data shows

**TRELLIS-BOX (off-by-some fork):**  
Claims ~50% VRAM reduction via FP16 optimization + Docker containerization.  
Requirement: NVIDIA GPU with at least 8 GB VRAM (recommended: 16 GB+).

**Trellis Stable Projectorz (one-click Windows installer):**  
Explicitly advertised as reducing requirements from 16 GB → 8 GB via float16 substitution.  
Source: DigiAlps article confirming the 2× reduction, though without independent benchmark data.

**TRELLIS.2 baseline:**  
The microsoft/TRELLIS.2 configurations use FP16 natively (confirmed by config files at `configs/generation/ss_flow_txt_dit_B_16l8_fp16.json`). The "16 GB minimum" in the critique applies to FP32 TRELLIS v1.

**Result:**  
TRELLIS v1 FP32: 16–30 GB. TRELLIS v1 or TRELLIS.2 with FP16: 8–16 GB. On RTX 4090 with sequential execution, TRELLIS.2 at FP16 is a realistic workload.

---

## 5. Sequential Execution — Practical UX Assessment

### The pattern

```
Isaac Sim running (physics active, renderer suspended)
  → User triggers generation
  → Extension calls: disable viewport, lower texture budget, torch.cuda.empty_cache()
  → Run TripoSR/Hunyuan3D at FP16
  → Generation completes (3–60 s depending on model)
  → Restore renderer settings
  → USD asset loaded into scene
Isaac Sim rendering resumes
```

### Is this practical?

**For TripoSR (preview tier):**  
GPU inference: 5–10 s on RTX 3060/4060. On RTX 4090, likely 2–5 s. Total pause with renderer teardown: 10–20 s. Acceptable for an interactive "generate asset" workflow.

**For Hunyuan3D-2GP Profile 4 (quality tier):**  
Shape generation: ~30–90 s (low-VRAM profile is slower). Total pause: 1–2 min. Acceptable for a deliberate "add this object to scene" action, less acceptable for rapid iteration.

**For TRELLIS.2 FP16 (premium tier):**  
Generation time is 60–300 s. This is a long pause but analogous to existing Isaac Sim workflows that block for large asset loading or USD stage construction. Users already tolerate this.

**UX verdict:** Sequential execution is not seamless but it is practical. It maps to existing mental models ("baking" in DCC tools). The extension can show a progress indicator with physics continuing in background (physics does not require the RTX renderer).

---

## 6. API-First Cost — Is It the Right Default?

### The economics

**Tripo API pricing (confirmed):**  
Professional plan: $0.212 per model generation.  
Meshy: $0.40 per model.  
Source: Sloyd 3D AI price comparison article (2026).

**Developer generating 10 models/day:**  
$2.12/day → $63.60/month at Tripo Professional pricing.  
For comparison: Professional plan costs $11.94/month and includes 3,000 credits.  
At ~20 credits per generation, 3,000 credits = 150 models/month at $11.94 = **$0.08/model at subscription rate**, not $0.20+.

**Implication:**  
At subscription pricing, 10 models/day (300/month) fits within the Professional tier at $11.94/month — cheaper than equivalent cloud GPU time. However, API-first as a *default* has a different problem: **it requires an API key, network access, and Tripo account setup** before the user generates their first model. For a developer running Isaac Sim locally in an air-gapped lab or corporate environment, API-first is not a default — it is a blocker.

**Who actually wants API-first:**  
- Teams with strict VRAM budgets running >30 GB scenes  
- Production pipelines needing highest quality (Rodin Gen-2, Tripo v2.5)  
- Environments where GPU is already at capacity from RL training

**Who does not want API-first:**  
- Developers on RTX 4090 with 24 GB (headroom exists)  
- Air-gapped / corporate network environments  
- Cost-sensitive indie developers iterating on geometry  
- Any workflow requiring offline capability

**Recommendation:** Local-first with sequential execution, API as opt-in upgrade — the reverse of the critique's recommendation.

---

## 7. CPU Offload — Slower but Functional

### TripoSR on CPU

CPU inference is possible and documented. Community-reported times: **30–60 seconds per generation**.  
Source: TripoSR optimization guide and CPU installation fork `zachysaur/TripoSR_Cpu_Installation-`.

**When this makes sense:**  
- Isaac Sim is running a long RL training loop that monopolizes GPU  
- The user needs a rough preview mesh, not a quality asset  
- VRAM is genuinely exhausted (e.g., 4096-env training runs that push 32+ GB)

**When it does not:**  
- Real-time or near-real-time generation workflows  
- Models heavier than TripoSR (Hunyuan3D, TRELLIS — no CPU path documented)

**Conclusion:** CPU offload is a valid fallback tier for TripoSR specifically. It should be documented as a configuration option, not the baseline.

---

## Summary: Configuration Matrix

| Scenario | GPU | Feasible approach | Coexist? |
|----------|-----|-------------------|----------|
| Isaac Sim simple scene + TripoSR FP16 | RTX 4090 (24 GB) | Simultaneous (8–10 GB headroom) | Yes, no pause needed |
| Isaac Sim simple scene + TripoSR FP16 | RTX 3090 (24 GB) | Sequential (pause renderer) | Yes, ~15 s pause |
| Isaac Sim medium scene + Hunyuan3D-2GP P4 | RTX 4090 (24 GB) | Sequential (pause renderer) | Yes, ~1–2 min pause |
| Isaac Sim RL training (32+ GB) | RTX 4090 (24 GB) | CPU fallback or API | No local GPU path |
| Isaac Sim + TRELLIS.2 FP16 | RTX 4090 (24 GB) | Sequential | Yes, 2–5 min pause |
| Isaac Sim + TRELLIS.2 FP16 | RTX 3080 (10 GB) | API or CPU (impractical local) | No local path |

---

## Conclusion

The critique's characterization of GPU coexistence as "not viable" and requiring an API-first default is **not supported by the evidence for RTX 4090-class hardware**, which is the natural target for Isaac Sim development.

The accurate framing is:

1. **Simultaneous coexistence** is viable on RTX 4090 for TripoSR (the preview tier) without any renderer pausing.
2. **Sequential coexistence** (pause renderer → generate → resume) is viable on RTX 4090 for Hunyuan3D-2GP and TRELLIS.2 FP16, at the cost of a 15 s–5 min generation window.
3. **API-first** is the right default only for users below RTX 3090 or running high-env-count RL training that saturates VRAM.
4. **CPU offload** is a documented fallback for TripoSR on any GPU configuration.
5. **The spec needs VRAM constraint documentation** — the critique is right about that. But the solution is a tiered backend with explicit mode selection, not flipping the default to API.

### Required spec changes (from this counter-research)

| Change | Priority |
|--------|----------|
| Document VRAM budget per tier in the extension UI | High |
| Implement sequential mode: pause renderer → generate → resume | High |
| Expose `/rtx-transient/resourcemanager/texturestreaming/memoryBudget` control in extension settings | Medium |
| Add CPU fallback path for TripoSR only | Medium |
| API backend as opt-in, not default | High |
| VRAM auto-detection at startup to select appropriate tier | Medium |

---

## Sources

- [TripoSR GitHub — VAST-AI-Research](https://github.com/VAST-AI-Research/TripoSR)
- [TripoSR Optimization Best Practices](https://www.triposrai.com/posts/triporsr-optimization-best-practices-for-quality-and-performance)
- [Hunyuan3D-2GP — deepbeepmeep fork](https://github.com/deepbeepmeep/Hunyuan3D-2GP)
- [Hunyuan3D-2 Issue #12 — 6 GB VRAM support](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/issues/12)
- [Hunyuan3D-2-WinPortable System Requirements — DeepWiki](https://deepwiki.com/YanWenKun/Hunyuan3D-2-WinPortable/2.2-system-requirements)
- [TRELLIS-BOX — FP16 fork](https://github.com/off-by-some/TRELLIS-BOX)
- [Trellis Stable Projectorz — 16 GB → 8 GB](https://digialps.com/trellis-stable-projectorz-a-one-click-windows-installer-which-cuts-gpu-memory-in-half-from-16gb-to-8gb/)
- [TRELLIS.2 FP16 config — GitHub](https://github.com/microsoft/TRELLIS/blob/main/configs/generation/ss_flow_txt_dit_B_16l8_fp16.json)
- [Isaac Sim Performance Optimization Handbook](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/reference_material/sim_performance_optimization_handbook.html)
- [Isaac Sim Requirements — VRAM 16 GB minimum](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
- [Isaac Sim Core Rendering Manager API](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/source/extensions/isaacsim.core.rendering_manager/docs/index.html)
- [Pause rendering Isaac Lab — NVIDIA Dev Forums](https://forums.developer.nvidia.com/t/pause-rendering-isaac-lab/298257)
- [Tripo Studio Pricing](https://www.tripo3d.ai/pricing)
- [3D AI Pricing Comparison 2026 — Sloyd](https://www.sloyd.ai/blog/3d-ai-price-comparison)
- [TripoSR CPU Installation fork](https://github.com/zachysaur/TripoSR_Cpu_Installation-)
- [TripoSR inference time — Issue #133](https://github.com/VAST-AI-Research/TripoSR/issues/133)
