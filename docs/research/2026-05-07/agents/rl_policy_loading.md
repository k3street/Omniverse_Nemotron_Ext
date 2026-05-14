# RL Policy Loading Research

For canonicals using trained policies (FrankaDrawerOpen, G1 locomotion, GR00T eval).

## Loading methods

| Method | Format | Backend | Use |
|---|---|---|---|
| `PolicyController.load_policy()` | TorchScript `.pt` | `torch.jit.load` | Bundled Isaac Sim examples (Franka, H1, Spot, Anymal) |
| ONNX runtime | `.onnx` | `onnxruntime.InferenceSession` | Cross-framework deployable; not yet implemented |
| GR00T N1 | HuggingFace ckpt | `gr00t.deploy.policy_server` gRPC | Foundation model; separate process, 24+ GB VRAM |

## How FrankaOpenDrawerPolicy works

Source: `/mnt/shared_data/isaac-sim/exts/isaacsim.robot.policy.examples/`

**Init** (`PolicyController.__init__` + `FrankaOpenDrawerPolicy.__init__`):
- Adds Franka USD to stage, wraps as `SingleArticulation`
- `load_policy(policy.pt, env.yaml)`: reads `.pt` via `omni.client.read_file` into BytesIO, then `torch.jit.load` (works with Nucleus paths)
- Parses `env.yaml` (IsaacLab format) via `config_loader` for: decimation, dt, stiffness/damping/effort/velocity limits, default joint positions
- Stores cabinet ref, looks up `panda_hand` and `drawer_handle_top` prims

**`_compute_observation()` → 31-dim vector**:
- `[0:9]` joint positions minus default_pos
- `[9:18]` joint velocities minus default_vel
- `[18:19]` cabinet drawer joint position
- `[19:20]` cabinet drawer joint velocity
- `[20:23]` world-space delta from EE TCP to drawer handle (3D)
- `[23:31]` previous 8 joint actions

**`forward(dt)` (every physics step)**:
- Every `_decimation` steps: `obs = _compute_observation()`, `action = _compute_action(obs)` (wraps `policy(obs).detach().numpy()`), stores as `_previous_action`
- Adds `default_pos` (policy outputs deltas), duplicates finger DOF 8→9, applies via `robot.apply_action(ArticulationAction(joint_positions=...))`

**Physics wiring** (franka_example.py):
```python
world.add_physics_callback("physics_step", callback_fn=self.on_physics_step)
# first tick: franka.initialize() + post_reset() (PD gains, solver iters)
# subsequent ticks: franka.forward(step_size)
```

`initialize()` switches to `"force"` control mode, sets PD gains + solver iterations (32 pos, 4 vel) from env.yaml, then sets joint drive to position mode.

## New tools to add

### `load_onnx_policy(policy_path, name)`
- `ort.InferenceSession(policy_path)` stored in `builtins.__policy_registry__[name]`
- Returns input/output tensor names and shapes
- For: G1 ONNX locomotion

### `load_pt_policy(policy_path, env_yaml_path, name)`
- Mirrors `PolicyController.load_policy()` — `torch.jit.load` + `parse_env_config`
- Stores in same registry; returns decimation, obs-dim, action-dim
- For: FrankaDrawerOpen, H1 locomotion

### `wire_policy_to_robot(policy_name, robot_path, obs_spec, action_spec)`
- Reads joint state via `SingleArticulation`, builds obs vector per spec, runs policy, applies `ArticulationAction`
- Installs physics step callback (replaces RMPflow)
- `obs_spec` fields: `joint_pos`, `joint_vel`, `ee_pose`, `target_pose`, `previous_action`, `gravity_vec`, `command`
- `action_spec`: `joint_position_delta` | `joint_position_absolute` | `ee_delta_pose`

### `setup_policy_step_subscription(policy_name)`
- Wraps `world.add_physics_callback(f"policy_{name}", fn)` where fn does `obs = read_obs(); action = policy(obs); apply(action)` at `_decimation` rate

## Obs/action spec patterns

**Typical inputs (31-69 dims)**:
- Joint positions relative to default (n_joints)
- Joint velocities (n_joints)
- Base linear/angular velocity in body frame (6)
- Gravity vector in body frame (3) — locomotion only
- Velocity command (vx, vy, ωz) — locomotion only
- EE TCP world position relative to target (3) — manipulation
- Object/target joint state (1-2) — drawer, door
- Previous action (n_joints)

**Typical outputs**:
- Joint position deltas (manipulation: 7-9 DOF) — most common
- Joint position targets absolute (locomotion: 12-19 DOF)
- Scaled by `action_scale` (0.5 for H1, 1.0 for Franka)

## Existing policy tools in registry

| Tool | Type | What it does |
|---|---|---|
| `load_groot_policy` | DATA | Returns HuggingFace download + gRPC launch cmds; **out-of-process** |
| `evaluate_groot` | CODE_GEN | Subprocess eval script for IsaacLab tasks |
| `finetune_groot` | CODE_GEN | Fine-tune config + subprocess launch |
| `set_motion_policy` | CODE_GEN | Configures RMPflow (unrelated to RL) |
| `setup_whole_body_control` | CODE_GEN | HOVER + Pink-IK ActionGroupCfg |
| `export_policy` | CODE_GEN | JIT/ONNX export script |
| `launch_training` | CODE_GEN | IsaacLab RSL-RL training subprocess |
| `diagnose_training` | DATA | Reads TensorBoard scalars |

**Gap**: none load a policy in-process and wire to Kit physics step. `load_groot_policy` is intentionally out-of-process. **No in-Kit policy runner today**.

## Mapping to canonicals

Of ~33 canonicals, estimate 3-5 truly require policy runner:

| Canonical | Policy | Type |
|---|---|---|
| CP-28 FrankaDrawerOpen | `FrankaOpenDrawerPolicy` | TorchScript `.pt` + env.yaml |
| G1 locomotion (HOVER flat/rough) | HOVER ckpt | ONNX or `.pt` |
| GR00T eval/finetune | already scaffolded out-of-process | gRPC server |
| Spot/Anymal flat terrain | possible | `.pt` + env.yaml |

All pick-place / sort / kit / palletize / SDG canonicals use scripted controllers (RMPflow/cuRobo/spline) — no RL runner needed.

**Minimal viable for CP-28 (FrankaDrawerOpen)**: add `load_pt_policy` + `setup_policy_step_subscription` as CODE_GEN handlers emitting `PolicyController` subclass instantiation + `world.add_physics_callback` call pattern (~15 lines, mirrors `franka_example.py`).

Source: Sonnet agent `a1d5ebae0d72d0f9a` 2026-05-07.
