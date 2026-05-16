# A12 Decision — GR00T Fine-Tune Canonical

**Date**: 2026-05-16  
**Agent**: A12  
**Backlog source**: `groot-finetune-n10-demos-001` (priority_tier=1, category=gr00t-finetune)

---

## §1 Candidate + GR00T Context

**Candidate**: `groot-finetune-n10-demos-001`  
**Template ID**: `CP-NEW-groot-finetune-n10-demos`

Task: Fine-tune GR00T N1.6-3B on 10 teleoperated Franka pick-and-place demonstrations.
This is the first canonical for the `gr00t-finetune` category (all 10 backlog entries were queued).

GR00T N1.6-3B is NVIDIA's 3-billion-parameter Vision-Language-Action (VLA) foundation model.
It maps camera observations + language instructions to robot joint targets at inference time.
Fine-tuning adapts the pre-trained policy to a specific robot embodiment and task distribution.

The canonical covers the standard NVIDIA-recommended fine-tuning pipeline:
1. `suggest_finetune_config` — LoRA layer freeze strategy for available GPU / demo count
2. `suggest_data_mix` — validate 10-demo corpus, recommend sim augmentation (1:1 real:neural)
3. `finetune_groot` — run LoRA fine-tuning subprocess (5000 steps, LeRobot v2 format)
4. `monitor_forgetting` — post-training VQA regression vs. base model (>20% drop = alert)
5. `evaluate_groot` — 20 closed-loop evaluation episodes on `Isaac-GR00T-PickPlace-v0`
6. `finetune_stats` — aggregate training metadata

Success criterion: `evaluate_groot` returns `success_rate >= 0.50`.
Result persisted to `/tmp/groot_finetune_result.json`.

---

## §2 Tool Schemas Pre-Looked-Up

All models from `service/isaac_assist_service/chat/tools/handlers/_models.py`:

| Tool | Required args | Key optional args |
|------|--------------|-------------------|
| `suggest_finetune_config` | `task_type`, `hardware` | `data_size` |
| `suggest_data_mix` | `task_type`, `available_data` | — |
| `finetune_groot` | `demo_data` | `model_id`, `num_steps`, `lora`, `output_dir` |
| `monitor_forgetting` | `checkpoint_dir`, `base_model` | — |
| `evaluate_groot` | `task` | `model_id`, `num_episodes`, `checkpoint` |
| `finetune_stats` | _(none)_ | — |

All tool calls in the template use only schema-valid kwargs. `finetune_stats` takes no args
(empty BaseModel with `pass`) and is called as `finetune_stats()` — consistent with its schema.

---

## §3 Honest Blockers

The backlog entry listed `blockers: []`. After schema verification, three hard blockers exist:

**BLOCKER 1 — Model weights (~50 GB, HuggingFace)**  
`finetune_groot` and `evaluate_groot` call into the Isaac-GR00T subprocess which downloads
`nvidia/GR00T-N1.6-3B` from HuggingFace on first use. Requires:
- Valid `HUGGINGFACE_TOKEN` environment variable
- ~50 GB disk and internet access for initial pull
- Subsequent runs use local cache

**BLOCKER 2 — GPU (24 GB VRAM minimum for LoRA)**  
The template uses `lora=True` to stay within 24 GB. Full fine-tuning needs A100/H100 (80 GB).
Local dev GPU is RTX 5070 (16 GB effective available) — borderline for even LoRA.
A cloud instance (e.g., AWS `p3.2xlarge` / V100 16 GB) is the recommended path for
function-gate validation.

**BLOCKER 3 — Demo dataset (LeRobot v2 HDF5)**  
`finetune_groot(demo_data="workspace/demo_data/franka_pick_place_n10")` requires a real
LeRobot v2 HDF5 dataset at that path. This is not in the repo. Must be generated via
`record_teleop_demo` (10 sessions) or downloaded from a dataset registry.

**Verdict**: Canonical is drafted and structurally complete. Function-gate cannot run in
CI or locally until all three blockers are resolved. `verified_status` reflects this honestly.

---

## §4 Form-Gate Results

Both form-gates ran with 0 ERROR:

```
$ python scripts/lint_canonical_templates.py workspace/templates/CP-NEW-groot-finetune-n10-demos.json
1 templates scanned: 0 OK, 0 ERROR, 1 WARN, 0 INFO
WARN T1_MISSING_SETTLE_STATE — expected for training canonicals (no sim settle)

$ python scripts/lint_canonical_templates.py --validate-tool-calls workspace/templates/CP-NEW-groot-finetune-n10-demos.json
1 templates scanned: 0 OK, 0 ERROR, 1 WARN, 0 INFO
```

The single WARN (`T1_MISSING_SETTLE_STATE`) is expected: this is a pure training canonical
with no Isaac Sim scene to settle — `settle_state` is not applicable.

All tool calls validate against their Pydantic models. `finetune_stats()` (no args) is
correctly parsed as a zero-argument call matching `FinetuneStatsArgs` (empty BaseModel).
