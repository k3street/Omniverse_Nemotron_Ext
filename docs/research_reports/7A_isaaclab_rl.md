# Phase 7A — IsaacLab Reinforcement Learning: Critique

**Agent:** Research 7A IsaacLab RL  
**Date:** 2026-04-15  
**Status:** Complete

## Critical Code Bugs (6 found in existing implementation)

1. **`ObservationGroupCfg` structure wrong** — puts `ObsTerm` in dict, should be nested `@configclass` with `ObsGroup`
2. **`actions = mdp.joint_positions` is gibberish** — must be `JointPositionActionCfg`
3. **Rewards as dict, not dataclass** — must be `@configclass` with typed `RewTerm` attrs
4. **`@configclass` decorator missing** — required on all `ManagerBasedRLEnvCfg` subclasses
5. **No `gym.register()` generated** — env is useless without Gymnasium registration
6. **`mdp.joint_pos` is not a real API name** — actual names are `mdp.joint_pos_rel`, `mdp.root_lin_vel_b`, etc.

## launch_training Is Broken

- `isaaclab.train` module **doesn't exist** — correct invocation is `isaaclab.sh -p scripts/.../train.py`
- `--log_dir` flag doesn't exist — correct: `--log_root_path`

## RL Library Status

- **rsl_rl, rl_games, skrl** — all still valid and maintained
- **Stable-Baselines3** — officially supported but missing from spec
- **skrl** added JAX backend support

## Policy Deployment — OmniGraph Claim Is Wrong

OmniGraph is NOT the deployment mechanism. The actual pattern is a pure Python `PolicyController` class with `forward()` method hooked into the physics step.

## Missing Considerations

- Environment registration prerequisite
- Multi-GPU training (`--distributed` flag)
- Checkpoint format differences across libraries
- Training must be separate process, not inside Kit
- `episode_length_s` calculation is wrong

## Sources
- [Creating a Direct Workflow RL Environment](https://isaac-sim.github.io/IsaacLab/main/source/tutorials/03_envs/create_direct_rl_env.html)
- [RL Library Comparison](https://isaac-sim.github.io/IsaacLab/main/source/overview/reinforcement-learning/rl_frameworks.html)
- [Policy Inference in USD](https://isaac-sim.github.io/IsaacLab/main/source/tutorials/03_envs/policy_inference_in_usd.html)
- [Registering an Environment](https://isaac-sim.github.io/IsaacLab/main/source/tutorials/03_envs/register_rl_env_gym.html)
