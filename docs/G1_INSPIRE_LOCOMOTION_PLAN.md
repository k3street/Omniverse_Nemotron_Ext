# G1 + Inspire Hand Locomotion — Implementation Plan

**Robot:** Unitree G1 humanoid + Inspire Hand (5-finger dexterous)  
**Goal:** Reliable walking in Isaac Sim, progressing to whole-body loco-manipulation  
**Date:** April 2026  
**Isaac Lab:** 2.3 ✅ installed  

---

## Reality Check

No single off-the-shelf checkpoint covers G1 walking + Inspire Hand control today:

| What exists | Gap |
|---|---|
| `unitree_rl_gym` `motion.pt` — 12 DOF leg policy | Hand ignored; Isaac Gym (not Lab) |
| Isaac Lab 2.3 PRs #3242 / #3440 — G1+Inspire teleop | Walking + hand teleop only, no autonomous policy |
| GR00T N1.7 — foundation VLA model, G1 supported | No G1+Inspire checkpoint; WBC repo still on N1.5/N1.6 |
| GEAR-SONIC / CuRobo | Manipulation planning, not locomotion |

**Strategy:** Three sequential phases. Each phase is independently useful and feeds the next.

---

## Phase 1 — Stable Walking (Legs Only)

**Goal:** G1 walks reliably in the Isaac Sim scene. Hand hangs passively.  
**ETA:** 1–2 days  
**Blocker:** None — checkpoint exists today

### 1.1 Install Isaac Lab

```bash
git clone https://github.com/isaac-sim/IsaacLab
cd IsaacLab
./isaaclab.sh --install
# Verify
./isaaclab.sh -p scripts/tutorials/00_sim/create_empty.py
```

### 1.2 Clone unitree_rl_gym

```bash
git clone https://github.com/unitreerobotics/unitree_rl_gym
cd unitree_rl_gym
pip install -e .
```

### 1.3 Run inference with the pre-trained checkpoint

```bash
# Flat-ground velocity tracking — the most stable starting policy
python scripts/play.py \
  --task=Isaac-Velocity-Flat-G1-v0 \
  --num_envs=1 \
  --checkpoint=logs/g1/flat/model_*.pt
```

> The checkpoint is at `deploy/pre_train/g1/motion.pt`. If the play script doesn't find it automatically, pass `--checkpoint deploy/pre_train/g1/motion.pt`.

### 1.4 Load your existing scene USD

Replace the default G1 environment with your scene:

```python
# In the task config (unitree_rl_gym/envs/g1/g1_config.py)
# Set scene.usd_path to your warehouse/office scene USD
scene = SceneCfg(usd_path="/path/to/your/scene.usd")
```

### 1.5 Wire into Isaac Assist (service-side)

Isaac Assist needs an RL policy runner tool. Add to `tool_executor.py`:

```
handle_deploy_rl_policy(args):
  - args: task_name, checkpoint_path, num_envs
  - spawns isaaclab play.py as a subprocess
  - streams stdout back as tool result
```

Map to new tool schema `deploy_rl_policy` in `tool_schemas.py`.

**Test prompt:** `"Load the G1 walking policy and play the simulation"`

### 1.6 Known issues to watch

- **29-DOF vs 12-DOF confusion** — issues #54, #82, #94 in unitree_rl_gym. The `motion.pt` checkpoint controls 12 DOF (legs only). If you get joint count mismatches, check the `G1Cfg` in the task config.
- **Inspire Hand joints** — the hand adds DOFs beyond what the policy expects. Zero-out hand joint targets or use a separate PD controller holding the hand in a neutral pose.
- **Motor overheating** (real-world) — not a sim issue, but relevant if deploying to hardware after sim validation.

---

## Phase 2 — Teleop + Demo Collection (Walking + Hand)

**Goal:** Teleoperate G1 walking while controlling the Inspire Hand. Record demonstrations for Phase 3 fine-tuning.  
**ETA:** 3–5 days (Isaac Lab 2.3 already installed — main blocker cleared)  
**Blocker:** ~~Isaac Lab 2.3 install~~ ✅ Done — hardware or VR controller for teleop is the remaining gating item

### 2.1 ~~Upgrade to Isaac Lab 2.3~~ — Already done

### 2.2 Apply G1 + Inspire Hand PRs

Both are merged into Isaac Lab main as of early 2026:

```bash
# PR #3242 — G1+Inspire PickPlace task + retargeting
# PR #3440 — USD path fixes + joint damping corrections for Inspire Hand
# Both included in Isaac Lab 2.3+, no manual cherry-pick needed
```

Verify the assets are present:

```bash
ls IsaacLab/source/extensions/omni.isaac.lab_assets/omni/isaac/lab_assets/robots/unitree/
# Should contain: g1.py, g1_inspire.py
```

### 2.3 Run the G1+Inspire teleop environment

```bash
./isaaclab.sh -p scripts/demos/g1_inspire_teleop.py \
  --num_envs=1 \
  --teleop_device=keyboard   # or: spacemouse, gamepad, quest3
```

The teleop script uses **Pink IK** (whole-body IK) for natural posture — torso rotation and arm motion are coordinated with leg stepping.

### 2.4 Hand retargeting

Isaac Lab 2.3 includes a retargeting pipeline for Inspire FTP (5-finger):

```python
from omni.isaac.lab.utils.retargeting import InspireHandRetargeter

retargeter = InspireHandRetargeter(
    hand_urdf="inspire_ftp.urdf",
    target_dofs=["J1", "J2", "J3", "J4", "J5"],  # Inspire finger joints
)
hand_targets = retargeter.retarget(motion_capture_data)
```

### 2.5 Record demonstrations

```bash
# Record to HDF5 (Isaac Lab native format, compatible with GR00T fine-tuning)
./isaaclab.sh -p scripts/demos/g1_inspire_teleop.py \
  --record \
  --output=demos/g1_inspire_walk_grab_$(date +%Y%m%d).hdf5
```

**Target:** 50–200 demonstrations of the target task (e.g., walk to object, pick up, carry). GR00T N1.7's EgoScale pretraining significantly reduces the data requirement vs. training from scratch.

### 2.6 Scene Workspace integration

Use the Isaac Assist `SceneWorkspace` module to track demo files per scene:

```
POST /api/v1/scenes/init  {"usd_path": "/path/to/warehouse.usd"}
POST /api/v1/scenes/warehouse/files
  {"category": "config", "filename": "teleop_config.yaml", "content": "..."}
```

Demo HDF5 files → store under `workspace/scenes/warehouse/config/`.

---

## Phase 3 — GR00T N1.7 Whole-Body Policy

**Goal:** Autonomous loco-manipulation: G1 walks to a target, picks it up with the Inspire Hand, without separate walking and hand policies.  
**ETA:** 4–8 weeks (dominated by demo collection + fine-tuning compute time)  
**Blocker:** Phase 2 demo dataset + GPU with 40GB+ VRAM for fine-tuning

### 3.1 Install Isaac GR00T

```bash
git clone https://github.com/NVIDIA/Isaac-GR00T
cd Isaac-GR00T
pip install -e ".[train]"
```

### 3.2 Verify N1.7 model

```bash
# Download base checkpoint from HuggingFace
huggingface-cli download nvidia/GR00T-N1.7-2B --local-dir checkpoints/groot_n17
```

N1.7 specifics:
- **Backbone:** Cosmos-Reason2-2B (Qwen3-VL), replaces Eagle2
- **Pretraining data:** 20K hours EgoScale human video + BONES-SEED (142K+ G1 motions)
- **Key improvement:** Flexible image resolution (no padding), better generalization
- **Inference VRAM:** 16GB minimum (RTX 4090 / L40 / H100 / Jetson AGX Orin)
- **Fine-tuning VRAM:** 40GB+ per GPU (A100/H100 recommended)

### 3.3 Convert Phase 2 demos to GR00T format

```bash
python Isaac-GR00T/scripts/convert_dataset.py \
  --input demos/g1_inspire_walk_grab_*.hdf5 \
  --robot g1_inspire \
  --output datasets/g1_inspire_groot/
```

GR00T expects LeRobot-format HDF5. Isaac Lab 2.3 records in compatible format — verify with:

```python
import h5py
with h5py.File("demo.hdf5") as f:
    print(list(f.keys()))
    # Expected: ["action", "obs/images", "obs/state", "obs/joint_pos"]
```

### 3.4 Fine-tune N1.7 on G1+Inspire demos

```bash
python Isaac-GR00T/scripts/train.py \
  --config configs/finetune_g1_inspire.yaml \
  --checkpoint checkpoints/groot_n17/model.pt \
  --dataset datasets/g1_inspire_groot/ \
  --output checkpoints/groot_n17_g1_inspire/ \
  --num_gpus 1          # A100 or H100
```

Expected fine-tune time: 6–24 hours depending on dataset size and GPU.

### 3.5 Deploy inference server → Isaac Sim (ZMQ bridge)

GR00T uses a server-client ZMQ architecture:

```bash
# Terminal 1: GR00T inference server
python Isaac-GR00T/scripts/serve.py \
  --checkpoint checkpoints/groot_n17_g1_inspire/best.pt \
  --port 5555

# Terminal 2: Isaac Sim client (runs inside sim Python env)
python Isaac-GR00T/deploy/isaac_sim_client.py \
  --robot g1_inspire \
  --server_port 5555
```

The client sends: `{rgb_image, depth_image, joint_positions, language_instruction}` → receives: `{joint_targets}` at ~10 Hz.

### 3.6 Wire ZMQ bridge into Isaac Assist

Add `IsaacSimZMQ` tool to `tool_executor.py`:

```
handle_start_groot_inference(args):
  - args: checkpoint_path, instruction (natural language)
  - starts GR00T server subprocess
  - starts ZMQ client in Isaac Sim via Kit RPC
  - streams robot state back to chat
```

**Test prompt:** `"Walk to the red box and pick it up"`

### 3.7 Evaluation

Use `IsaacLabEvalTasks` for standardized evaluation:

```bash
./isaaclab.sh -p eval/g1_inspire_pickplace.py \
  --policy groot \
  --checkpoint checkpoints/groot_n17_g1_inspire/best.pt \
  --num_episodes=50
```

Track: success rate, steps-to-success, failure modes.

---

## Dependency Map

```
Phase 1 (walking)
  └─ unitree_rl_gym motion.pt  ──► Prove locomotion works in your scene
       │
       ▼
Phase 2 (teleop + data)
  └─ Isaac Lab 2.3 ✅ already installed
  └─ PR #3242/#3440 — verify included in current install
  └─ VR/gamepad controller for teleop  ◄── remaining gating item
  └─ 50–200 recorded HDF5 demos  ──► Dataset for Phase 3
       │
       ▼
Phase 3 (GR00T N1.7 whole-body)
  └─ Isaac-GR00T repo
  └─ 40GB+ VRAM GPU for fine-tuning
  └─ ZMQ bridge (IsaacSimZMQ)  ──► Autonomous loco-manipulation
```

---

## Isaac Assist Integration Checklist

| Tool to add | Phase | File | Status |
|---|---|---|---|
| `deploy_rl_policy` — launch unitree_rl_gym policy | 1 | `tool_executor.py`, `tool_schemas.py` | ⬜ Not started |
| RL policy deployment via `isaacsim.robot.policy.examples` | 1 | `tool_executor.py` | ⬜ Not started |
| `start_groot_inference` — launch GR00T ZMQ server+client | 3 | `tool_executor.py`, `tool_schemas.py` | ⬜ Not started |
| `IsaacSimZMQ` bridge helper | 3 | new `chat/tools/zmq_bridge.py` | ⬜ Not started |
| Demo recording tool — `record_teleop_demo` | 2 | `tool_executor.py`, `tool_schemas.py` | ⬜ Not started |
| GR00T fine-tuning launcher — `finetune_groot` | 3 | `tool_executor.py`, `tool_schemas.py` | ⬜ Not started |

---

## Key Repos & PRs

| Resource | URL |
|---|---|
| unitree_rl_gym (walking checkpoint) | https://github.com/unitreerobotics/unitree_rl_gym |
| unitree_rl_lab (Isaac Lab training) | https://github.com/unitreerobotics/unitree_rl_lab |
| Isaac Lab | https://github.com/isaac-sim/IsaacLab |
| Isaac Lab PR #3242 — G1+Inspire PickPlace | https://github.com/isaac-sim/IsaacLab/pull/3242 |
| Isaac Lab PR #3440 — G1+Inspire USD fixes | https://github.com/isaac-sim/IsaacLab/pull/3440 |
| Isaac GR00T (N1.7) | https://github.com/NVIDIA/Isaac-GR00T |
| GR00T Whole-Body Control (N1.5/N1.6) | https://github.com/NVlabs/GR00T-WholeBodyControl |
| GR00T-N1.7 HuggingFace | https://huggingface.co/nvidia/GR00T-N1.7-2B |
| GEAR-SONIC (dexterous manipulation) | https://nvlabs.github.io/GEAR-SONIC/ |
| Isaac Lab 2.3 WBC blog | https://developer.nvidia.com/blog/streamline-robot-learning-with-whole-body-control-and-enhanced-teleoperation-in-nvidia-isaac-lab-2-3/ |
