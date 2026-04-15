# Phase 7B — Enhanced Replicator / SDG: Critique

**Agent:** Research 7B Replicator SDG  
**Date:** 2026-04-15  
**Status:** Complete

## 1. Wrong GitHub Link

Spec links to `OmniIsaacGymEnvs` — wrong repo (RL, not SDG), archived April 14, 2026. Correct: `omni.replicator.core` docs.

## 2. "OmniReplicator" Is Not a Product Name

The framework is called **Replicator**, accessed via `omni.replicator.core`. Import: `import omni.replicator.core as rep`.

## 3. Output Format Support

- **KITTI** — Native via `KittiWriter`, but `alpha`, `dimensions`, `location`, `rotation_y` all set to 0.0. Needs custom subclass.
- **COCO** — Native, works correctly.
- **TFRecord** — **Does not exist natively.** No `TFRecordWriter`. Requires custom writer + TF dependency.
- **Raw NumPy** — Achievable via `PytorchWriter`.

## 4. NL Domain Randomization

**Chat IRO** exists in Isaac Sim 6.0 (uses Llama 4 Maverick 17B via NIM) but is limited to IRO-scoped objects. General DR needs code-gen pattern.

Lux is not a directly settable unit in Replicator — you set `intensity` (nits/cd/m²).

## 5. Omniverse Farm

Available but not turnkey. Requires self-hosted setup + custom job submission. No native SDG-Farm SDK. NVIDIA Omniverse Launcher deprecated October 1, 2025.

## 6. Missing Features

- **Cosmos SDG** — new in Isaac Sim, not mentioned in spec
- **`isaacsim.replicator.grasping`** — in dependency table but absent from 7B task list
- **`keypoints` annotator** — not generic, requires IRA or custom USD rigs

## Sources
- [Synthetic Data Generation — Isaac Sim 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/synthetic_data_generation/index.html)
- [Chat IRO](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/action_and_event_data_generation/ext_replicator-object/ext_chat_iro.html)
- [KITTI Writer bugs — NVIDIA Forums](https://forums.developer.nvidia.com/t/kitti-writer-support/267909)
- [OmniIsaacGymEnvs — archived](https://github.com/isaac-sim/OmniIsaacGymEnvs)
