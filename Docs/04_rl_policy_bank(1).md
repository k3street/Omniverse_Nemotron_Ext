# 04 — RL Policy Bank

A localhost service hosting the per-skill RL policies. Multiple policies loaded simultaneously; routed by `(skill_name, embodiment_id)`.

## Endpoints

```
POST /act
  body:  { skill_name, embodiment_id, observation, phase_context }
  resp:  { action, value_estimate, info }

POST /reset
  body:  { skill_name, embodiment_id }     # clears recurrent state if any

GET /policies
  resp:  list of loaded (skill_name, embodiment_id, version, sha256)

GET /healthz
```

`/act` is hot-path: target p99 < 15 ms.

## Policy Format

Each policy is a directory:

```
policies/
  pick_rigid/
    tenthings_v1_open_arm_bimanual/
      v0.3.1/
        policy.onnx           # or .engine for TensorRT
        normalizer.npz        # observation mean/std
        config.yaml           # action space, obs space, control rate, frame
        card.md               # training data, sim env hash, eval results
```

`config.yaml` is the contract:

```yaml
skill_name: pick_rigid
embodiment_id: tenthings_v1_open_arm_bimanual
version: 0.3.1
control_rate_hz: 50
observation:
  components:
    - name: tcp_pose_lead         # 7 (xyz + quat)
      frame: base_link
    - name: tcp_vel_lead          # 6
    - name: gripper_state_lead    # 2 (width, force)
    - name: object_pose_target    # 7, from phase_context.semantic_target
    - name: f_t_lead              # 6
    - name: rgb_wrist_lead        # 84x84x3 (encoded by frozen ResNet to 256-dim feature)
    - name: phase_progress        # 1, 0..1 from Continuity Manager
action:
  type: delta_tcp_pose_plus_gripper
  dim: 7   # 3 dpos, 3 drot (axis-angle), 1 gripper_target_width
  bounds:
    dpos_max_m: 0.02
    drot_max_rad: 0.05
    gripper_min: 0.0
    gripper_max: 0.085
hidden_state: false
trained_in: isaac_lab_v4.5
sim_env_hash: ab12cd34
```

The Action Arbitration node (07) reads `action.type` and applies action bounds before passing to the low-level controller.

## Skill Inventory & Training Notes

| Skill | Difficulty | Sim training time | Real fine-tune | Notes |
|---|---|---|---|---|
| `pick_rigid` | Medium | ~12 GPU-hr | 100–300 demos | First skill to land. Curriculum: top-down → angled → cluttered. |
| `place_on_surface` | Easy | ~6 GPU-hr | 50 demos | Reuse `pick_rigid` value function as warm start. |
| `transit_to_pose` | Easy | ~3 GPU-hr | 0 demos (motion-planned) | Often replaceable by RRT/cuRobo; keep RL for cluttered cases. |
| `pick_deformable` | Hard | ~30 GPU-hr | 200+ demos | FleX or warp-based deformable sim required. |
| `push_object` | Easy | ~4 GPU-hr | 50 demos | |
| `press_button` | Easy | ~3 GPU-hr | 30 demos | Force feedback critical. |
| `open_drawer` | Medium | ~10 GPU-hr | 100 demos | Compliance + discovery of axis. |
| `stabilize_object` (ASSIST) | Medium | ~8 GPU-hr | 50 demos | Trained against perturbations. |
| `hold_cloth_taut` (ASSIST) | Hard | ~25 GPU-hr | 100+ demos | Co-trained with `bimanual_fold` LEAD. |
| `bimanual_handover` | Hard | ~40 GPU-hr | 200+ demos | Synchronized policy; both arms are inputs and outputs. |
| `bimanual_fold` | Hard | ~30 GPU-hr | 200+ demos | Trained as LEAD with ASSIST stub or paired ASSIST. |

## Algorithm Choice

- **Default: PPO** for single-arm skills with ~10M steps in Isaac Lab. Stable, well-understood, hyperparameters portable across skills.
- **For contact-rich skills** (`press_button`, `open_drawer`): **SAC** or **DrQ-v2** with image augmentations; better for sparse reward + force feedback.
- **For bimanual synchronized** (`bimanual_handover`, `bimanual_fold`): **PPO with shared encoder** for both arms' observations, separate action heads. Avoid MAPPO/QMIX unless decentralized execution is required (it isn't here — single policy, single process).
- **Fine-tuning on real**: ResiP / Residual RL on top of frozen sim policy, OR offline RL (IQL) on collected demos + on-policy correction. Prefer Residual RL — faster, less data.

## Phase Context Injection

The RL policy doesn't get the full Task Spec. The Continuity Manager extracts the relevant fields and passes them as `phase_context`:

```python
phase_context = {
    "skill_name": phase.skill_name,
    "semantic_target": resolve_target(phase.semantic_target, scene),  # absolute pose
    "approach_offset_m": phase.semantic_target.get("approach_offset_m"),
    "max_force_n": phase.constraints["max_force_n"],
    "phase_progress": elapsed / phase.constraints["max_duration_s"],
    "carry_object_pose": current_carry_pose_or_None,
}
```

The policy's observation builder consumes `phase_context` to produce the fixed-dim observation vector. Each policy has its own observation builder co-located with the policy in the bank — this is how schema differences between skills are handled.

## Reset Semantics

`/reset` is called by the Continuity Manager at every phase boundary. For stateless policies it's a no-op. For recurrent policies (none currently — keep it that way unless you have a reason), it zeros hidden state.

## Safety Wrapping

Every policy output passes through a safety wrapper before leaving the service:

```python
def safe_action(raw_action, config, obs):
    a = clip_to_bounds(raw_action, config.action.bounds)
    a = clip_to_workspace(a, obs.tcp_pose_lead, GLOBAL_WORKSPACE_BOUNDS)
    a = limit_jerk(a, last_action, MAX_JERK)
    return a
```

Workspace bounds and jerk limits are global (not policy-specific). A policy that produces out-of-bound actions repeatedly should be flagged in `info`; the Continuity Manager escalates after 3 consecutive clamps.

## Loading Strategy

On startup, load all policies registered in `policies/manifest.yaml`. Policies under 200 MB (typical for these action spaces) — loading 10 of them is ~2 GB GPU. If GPU-constrained, lazy-load on first `/act` call and LRU-evict.

## Versioning

Policy version is part of the routing key. Continuity Manager pins to a specific version per skill at task start (read from `policies/active.yaml`). No silent upgrades mid-task.
