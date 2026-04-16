# Phase 7G Addendum — GR00T Advanced Tooling

**Enhances:** Phase 7G (GR00T N1 Foundation Policy)  
**Source:** Persona P10 (Jin, GR00T humanoid ML engineer)  
**Research:** `rev2/research_groot_tooling.md`

---

## Overview

GR00T N1.6 has zero built-in diagnostic tooling. These features fill the gap — answering "why did the policy fail?" and "how do I fix it?" without days of ablation.

---

## Tools

### 7G.X1 — Attention Visualization

**"What is the robot looking at during a failed grasp?"**

Extract cross-attention maps from GR00T's 32-layer DiT — shows which visual patches and language tokens the policy attends to per action step.

**Implementation:**
```python
# SigLIP-2 vision encoder is standard HuggingFace ViT
# Disable SDPA fusion, tap attn_drop nodes
from torch.fx import create_feature_extractor
features = create_feature_extractor(model.vision_encoder, 
    return_nodes={"encoder.layers.12.self_attn.attn_drop": "attn_12"})

# Cross-attention in DiT: which visual tokens drive each action
attn_maps = extract_dit_cross_attention(model, observation)
# Overlay on viewport image: heatmap of attention
```

**Effort:** ~2-3 days implementation. GR00T paper discloses features from layer 12 specifically.

### 7G.X2 — Out-of-Distribution Detection

**"Warn me when the real robot sees something the policy wasn't trained on"**

Three tiers, increasing cost/reliability:

| Tier | Method | Overhead | Reliability |
|------|--------|----------|-------------|
| 1 | Action instability: variance + autocorrelation (A-VI, A-AI) | Zero | Moderate |
| 2 | 4-sample DiT variance check | +15ms latency | Good |
| 3 | Mahalanobis distance on 12th-layer embeddings | Requires calibration | Best |

**Tier 1 (always on):**
```python
# Compute on every inference step
action_variance = torch.var(action_sequence, dim=0)  # A-VI
action_autocorr = torch.corrcoef(actions[:-1], actions[1:])  # A-AI
if action_variance.max() > threshold:
    warn("OOD: Action instability detected — policy may be extrapolating")
```

**Tier 3 calibration:** One-time pass over training data → compute mean + covariance of 12th-layer embeddings. At inference: Mahalanobis distance > threshold = OOD.

### 7G.X3 — Data Mixing Guidance

**"What ratio of sim/real/video data should I use for fine-tuning?"**

**NVIDIA's validated recipe:** 1:1 real-to-neural-trajectory (40% performance gain over real-only).

**Tool:** `suggest_data_mix(task_type, available_data)`

```
Task: tabletop pick-and-place
Available: 200 real demos, 5000 sim demos, 0 video

Recommendation:
- Use all 200 real demos
- Sample 200 sim demos (match 1:1 ratio)
- Spatial DR dominates appearance DR 3:1 — prioritize table height + camera pose variation
- Consider collecting 50 video demos for visual diversity
```

### 7G.X4 — Layer-wise Fine-Tuning Advisor

**"Which layers should I freeze?"**

**Tool:** `suggest_finetune_config(task_type, hardware, data_size)`

| Task Type | Freeze | Tune | Notes |
|-----------|--------|------|-------|
| Similar to pretraining (tabletop) | Vision + Language | DiT + connectors | NVIDIA's own recipe |
| New visual domain | Language only | Vision + DiT + connectors | Cuts batch from 200→16 on A6000 |
| New embodiment | Nothing | All (LoRA rank 16) | Fits on RTX 4080 <8 GB |

**Warning from research:** "Don't Blind Your VLA" — unfreezing vision encoder for task-specific data causes OOD generalization loss.

### 7G.X5 — Catastrophic Forgetting Monitor

**"Is fine-tuning breaking the pretrained capabilities?"**

**Tool:** `monitor_forgetting(checkpoint_dir, base_model)`

At each checkpoint:
1. Run 30-example VQA regression suite (MMMU, MMStar, RealWorldQA)
2. Compare scores against base model
3. Compute per-layer weight drift: `||W_finetuned - W_pretrained||_F`
4. Alert if any VQA score drops >20% or vision encoder drift exceeds threshold

**Finding from research:** Standard fine-tuning can produce near-zero scores on 6+ benchmarks — collapses silently without external checks.

### 7G.X6 — Latency-Aware Export

**Tool:** `export_policy(checkpoint, target_device, inference_budget_ms)`

| Target | Format | Expected Hz | Notes |
|--------|--------|-------------|-------|
| Jetson AGX Orin | TensorRT bf16 | 5.8 Hz | Official pipeline |
| Jetson Orin NX | TensorRT bf16 | ~3 Hz | FP8 NOT supported (needs SM89+) |
| x86 + RTX 4090 | TensorRT bf16 | ~15 Hz | Best desktop performance |

**Warning:** FP8 and NVFP4 fail on Jetson Orin NX Super (SM87). Hard-capped at bf16.

### 7G.X7 — Checkpoint Analysis

**Tool:** `analyze_checkpoint(checkpoint_path)`

**Returns:**
```json
{
  "embodiment": "UNITREE_G1",
  "training_steps": 15000,
  "layer_drift": {
    "vision_encoder": 0.02,  // low = frozen, good
    "dit_layers": 0.45,      // high = well-targeted
    "adapter_mlps": 0.38,    // high = expected
    "language_model": 0.001  // near-zero = frozen, good
  },
  "action_statistics": {
    "mean_per_joint": [...],
    "std_per_joint": [...]
  },
  "risk_assessment": "Vision encoder drift is low — forgetting risk is minimal. DiT is well-tuned."
}
```

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Attention extraction | L0 | Mock model, verify layer tap works |
| OOD metrics (Tier 1) | L0 | Known actions → correct variance/autocorr |
| Data mix suggestion | L0 | Known inputs → correct ratio |
| Finetune config | L0 | Task type → correct freeze/tune |
| Weight drift computation | L0 | Two checkpoints → correct Frobenius norm |
| Export pipeline | L3 | Requires GPU + GR00T checkpoint |
