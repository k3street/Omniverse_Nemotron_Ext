# Phase 7A — IsaacLab Reinforcement Learning

**Status:** Partially implemented (code generators exist but have 5 confirmed bugs)  
**Depends on:** Phase 3 (import_robot, sim_control, clone_prim)  
**Blocks:** Phase 7D (Arena), Phase 7E (Eureka), Phase 7G (GR00T)  
**Research:** `research_reports/7A_isaaclab_rl.md`, `rev2/verify_7A_bugs.md`

---

## Overview

Scaffold RL training environments, launch training runs, and stream live metrics — all from the chat panel. Integrates with IsaacLab 2.x.

---

## Critical: Fix Existing Bugs First

Five confirmed structural bugs in `_generate_isaaclab_env_code` (tool_executor.py):

| Bug | Current Code | Correct Pattern |
|-----|-------------|-----------------|
| ObservationGroupCfg | `ObservationGroupCfg({"joint_pos": ObsTerm(...)})` | Nested `@configclass` subclassing `ObsGroup` with class-level `ObsTerm` attributes |
| Actions | `actions = mdp.joint_positions` | `@configclass` with `JointPositionActionCfg` attributes |
| Rewards | `rewards = {"reach_target": RewTerm(...)}` | `@configclass` with typed `RewTerm` attributes |
| @configclass | Missing decorator | Add `from isaaclab.utils import configclass` + `@configclass` on all config classes |
| gym.register | Not generated | Generate `__init__.py` with `gymnasium.register()` alongside env_cfg.py |

**Note:** `mdp.joint_pos` IS a valid API function (rev2 verified). Rev1 was wrong on this point.

---

## Tools

### 7A.1 `create_isaaclab_env(task_name, num_envs, env_spacing, params)`

**Type:** DATA handler (returns structured data, no Kit call)

**Correct generated code structure:**
```python
from isaaclab.utils import configclass
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.envs.mdp import ObsGroup, ObsTerm
import isaaclab.envs.mdp as mdp

@configclass
class ObservationsCfg:
    @configclass
    class PolicyCfg(ObsGroup):
        joint_pos: ObsTerm = ObsTerm(func=mdp.joint_pos)
        joint_vel: ObsTerm = ObsTerm(func=mdp.joint_vel_rel)
    policy: PolicyCfg = PolicyCfg()

@configclass
class ActionsCfg:
    joint_positions: mdp.JointPositionActionCfg = mdp.JointPositionActionCfg(
        asset_name="robot", joint_names=[".*"]
    )

@configclass
class RewardsCfg:
    reach_target: mdp.RewardTermCfg = mdp.RewardTermCfg(
        func=mdp.reward_reaching_target, weight=1.0
    )

@configclass
class MyEnvCfg(ManagerBasedRLEnvCfg):
    observations: ObservationsCfg = ObservationsCfg()
    actions: ActionsCfg = ActionsCfg()
    rewards: RewardsCfg = RewardsCfg()
    scene: InteractiveSceneCfg = InteractiveSceneCfg(num_envs=64, env_spacing=2.5)
```

**Must also generate `__init__.py`:**
```python
import gymnasium
gymnasium.register(id="MyTask-v0", entry_point="isaaclab.envs:ManagerBasedRLEnv", kwargs={"env_cfg_entry_point": "my_task:MyEnvCfg"})
```

**Parameters:**
- `task_name` (string, required): Name for the env, becomes class name + gym ID
- `num_envs` (int, default 64): Number of parallel environments
- `env_spacing` (float, default 2.5): Meters between env origins
- `task_type` (enum: manipulation/locomotion/navigation/custom): Template selection
- `robot_asset` (string, optional): USD path to robot — extracted from current scene if omitted
- `reward_terms` (dict, optional): Override template rewards

### 7A.2 `launch_training(task, algo, num_steps, checkpoint_dir)`

**Type:** CODE_GEN handler

**Critical fix:** `python -m isaaclab.train` does NOT exist. Correct invocation:
```python
# The generated code must use the correct script path:
cmd = [
    "isaaclab.sh", "-p",
    "scripts/reinforcement_learning/rsl_rl/train.py",  # or skrl/train.py
    "--task", task_name,
    "--num_envs", str(num_envs),
    "--max_iterations", str(num_steps),
    "--log_root_path", checkpoint_dir,  # NOT --log_dir
]
```

**Algo mapping:**
| Algo | Library | Script |
|------|---------|--------|
| ppo | rsl_rl | `rsl_rl/train.py` |
| sac | skrl | `skrl/train.py` |
| td3 | skrl | `skrl/train.py` |
| ppo_rl_games | rl_games | `rl_games/train.py` |
| ppo_sb3 | sb3 | `sb3/train.py` |

**Training must run as separate subprocess**, not inside Kit. Use `subprocess.Popen` with stdout streaming.

### 7A.3 `show_training_metrics(run_id)`

**Type:** DATA handler

**Implementation:** Use `tensorboard.backend.event_processing.event_accumulator.EventAccumulator` to read `events.out.tfevents.*` files. Poll every 5-10s.

**Returns:** `{reward_curve: [...], episode_length: [...], value_loss: [...], step: int}`

### 7A.4 `deploy_policy(checkpoint, articulation_path)`

**Type:** CODE_GEN handler

**Correction:** NOT OmniGraph tick loop. Generate a Python `PolicyController` class:

```python
import torch

class PolicyController:
    def __init__(self, checkpoint_path, articulation):
        self.policy = torch.jit.load(checkpoint_path)
        self.art = articulation

    def forward(self, dt):
        obs = self._build_obs()
        action = self.policy(obs)
        self.art.set_joint_position_targets(action.numpy())

    def _build_obs(self):
        # Build observation tensor from articulation state
        ...

# Register as physics callback
world.add_physics_callback("policy_step", controller.forward)
```

### 7A.5 `evaluate_policy(checkpoint, num_episodes)`

**Type:** CODE_GEN handler — runs headless via subprocess, returns metrics.

### 7A.6 RL Task Template Library

Pre-defined templates: pick-and-place, locomotion, cabinet open/close, in-hand reorientation. Each provides reward terms, observation terms, and success criteria.

---

## Dependencies

- Phase 3: `import_robot` for asset loading
- Phase 8A: `clone_envs` (upgrade path from Phase 3's naive clone — use `GridCloner` with `replicate_physics=True`)
- IsaacLab 2.x installed alongside Isaac Sim

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Env code generation | L0 | compile() all templates, verify @configclass, ObsGroup, gym.register |
| launch_training command | L0 | Verify correct script path, flag names, algo mapping |
| deploy_policy code | L0 | compile(), verify physics_callback pattern (NOT OmniGraph) |
| Template coverage | L0 | Each task_type produces valid code |
| Training subprocess | L3 | Requires IsaacLab + GPU |

## Known Limitations

- `ManagerBasedRLEnv` only — `DirectRLEnv` requires different scaffolding
- Training is always a separate process (cannot run inside Kit)
- Checkpoint format varies by library (rsl_rl: .pt, skrl: different structure)
