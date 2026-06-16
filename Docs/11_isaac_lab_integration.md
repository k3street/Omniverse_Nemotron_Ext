# 11 — Isaac Lab Integration (Training Path)

How `08_training_infrastructure.md` actually compiles to running code. Isaac Lab is the sim, ROS2 is **not** in this loop — Isaac Lab trains policies in-process with vectorized GPU envs at thousands of FPS; injecting ROS2 here would destroy throughput.

ROS2 enters at the **inference-in-sim** path covered in `12_isaac_sim_digital_twin.md`.

## Why no ROS2 during training

Isaac Lab's `ManagerBasedRLEnv` is designed to run thousands of parallel environments on GPU and step them at ≥ 50× real-time. PPO at 4096 envs hits ~150,000 sim steps/sec on an H100. Pushing every observation through ROS2 messaging would:

- Force CPU-GPU round trips for every obs term.
- Serialize message construction across envs.
- Drop throughput by 10–100×.
- Add stack complexity for zero benefit — the policy doesn't need ROS2 to learn.

**Rule:** the trained policy should never know whether observations came from Isaac Lab in-process or from ROS2 on real hardware. The policy consumes a fixed-shape observation vector defined in its `config.yaml` (see `04`). That vector is built two different ways: by `ObservationManager` terms in Isaac Lab during training, and by the Observation Pipeline (`06`) during deployment. Both produce the same vector.

This invariance is what makes sim-to-real possible and what makes the same `policy.onnx` work in both worlds.

## Versioning Lockstep

The policy's `config.yaml` must declare its training environment so the wrong env isn't accidentally used at fine-tune time:

```yaml
trained_in: isaac_lab
isaac_lab_version: "2.1.0"
isaac_sim_version: "5.0.0"
sim_env_hash: ab12cd34   # hash of env config + USD assets + reward terms
```

Mismatch → policy refuses to load. This is enforced by `policy_loader.py` in the Policy Bank.

## Environment Config Pattern (per skill)

Each skill in the Policy Bank gets exactly one Isaac Lab env. Naming: `manipulation_stack/isaac_lab_envs/<skill_name>_env.py`.

The env's `ObservationsCfg.PolicyCfg` defines obs terms in **the same order** as the policy's `config.yaml` `observation.components`. If they desync, the policy reads garbage.

### `pick_rigid_env.py` — full config skeleton

```python
"""
Isaac Lab env for pick_rigid skill on tenthings_v1_open_arm_bimanual.
Run with:
    ./isaaclab.sh -p scripts/rsl_rl/train.py \
        --task TenThings-PickRigid-v0 --num_envs 4096 --headless
"""
from __future__ import annotations
import math
import torch

import isaaclab.sim as sim_utils
from isaaclab.assets import ArticulationCfg, RigidObjectCfg, AssetBaseCfg
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.managers import EventTermCfg as EventTerm
from isaaclab.managers import ObservationGroupCfg as ObsGroup
from isaaclab.managers import ObservationTermCfg as ObsTerm
from isaaclab.managers import RewardTermCfg as RewTerm
from isaaclab.managers import TerminationTermCfg as DoneTerm
from isaaclab.managers import SceneEntityCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.utils import configclass
from isaaclab.sensors import CameraCfg, ContactSensorCfg

# Custom MDP terms for this skill (see mdp/ directory in same package)
from . import mdp


# -----------------------------------------------------------------------------
# Embodiment configuration — tenthings_v1_open_arm_bimanual
# -----------------------------------------------------------------------------
TENTHINGS_BIMANUAL_CFG = ArticulationCfg(
    prim_path="{ENV_REGEX_NS}/Robot",
    spawn=sim_utils.UsdFileCfg(
        usd_path="${ASSETS}/tenthings/v1_open_arm_bimanual.usd",
        rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=True,
            solver_position_iteration_count=12,
            solver_velocity_iteration_count=4,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.0),
        joint_pos={  # retract pose
            "left_shoulder_pan": 0.0, "left_shoulder_lift": -0.8,
            "left_elbow": 1.5,        "left_wrist_1": -0.7,
            "left_wrist_2": 0.0,      "left_wrist_3": 0.0,
            "left_gripper":  0.04,
            "right_shoulder_pan": 0.0, "right_shoulder_lift": -0.8,
            "right_elbow": 1.5,        "right_wrist_1": -0.7,
            "right_wrist_2": 0.0,      "right_wrist_3": 0.0,
            "right_gripper": 0.04,
        },
    ),
    actuators={
        "arms": sim_utils.ImplicitActuatorCfg(
            joint_names_expr=[".*shoulder.*", ".*elbow.*", ".*wrist.*"],
            stiffness=400.0, damping=40.0,
            effort_limit=87.0, velocity_limit=2.5,
        ),
        "grippers": sim_utils.ImplicitActuatorCfg(
            joint_names_expr=[".*gripper.*"],
            stiffness=1000.0, damping=100.0,
            effort_limit=20.0, velocity_limit=0.2,
        ),
    },
)


# -----------------------------------------------------------------------------
# Scene
# -----------------------------------------------------------------------------
@configclass
class PickRigidSceneCfg(InteractiveSceneCfg):
    ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
    light = AssetBaseCfg(
        prim_path="/World/light",
        spawn=sim_utils.DomeLightCfg(color=(0.9, 0.9, 0.9), intensity=1000.0),
    )
    table = AssetBaseCfg(
        prim_path="{ENV_REGEX_NS}/Table",
        spawn=sim_utils.UsdFileCfg(usd_path="${ASSETS}/props/table.usd"),
    )
    robot: ArticulationCfg = TENTHINGS_BIMANUAL_CFG
    target_object = RigidObjectCfg(
        prim_path="{ENV_REGEX_NS}/Object",
        spawn=sim_utils.MultiAssetSpawnerCfg(
            assets_cfg=[
                sim_utils.UsdFileCfg(usd_path="${ASSETS}/objects/mug_red.usd"),
                sim_utils.UsdFileCfg(usd_path="${ASSETS}/objects/can_blue.usd"),
                sim_utils.UsdFileCfg(usd_path="${ASSETS}/objects/box_small.usd"),
            ],
            random_choice=True,
        ),
    )
    wrist_cam_right = CameraCfg(
        prim_path="{ENV_REGEX_NS}/Robot/right_wrist/camera",
        offset=CameraCfg.OffsetCfg(pos=(0.0, 0.0, 0.05), rot=(1, 0, 0, 0)),
        spawn=sim_utils.PinholeCameraCfg(focal_length=18.0),
        width=84, height=84, data_types=["rgb"],
        update_period=1 / 30,
    )
    contact_right_grip = ContactSensorCfg(
        prim_path="{ENV_REGEX_NS}/Robot/right_gripper/.*",
        track_air_time=True, history_length=3,
    )


# -----------------------------------------------------------------------------
# Actions
# -----------------------------------------------------------------------------
@configclass
class ActionsCfg:
    """Delta TCP pose + gripper width on the LEAD arm (right). Idle arm holds retract."""
    arm_right_action = mdp.DeltaTCPActionCfg(
        asset_name="robot",
        body_name="right_tool0",
        scale=(0.02, 0.05),  # 2 cm dpos, 0.05 rad drot per step
        controller="ik_rmp",
    )
    gripper_right_action = mdp.GripperWidthActionCfg(
        asset_name="robot",
        joint_names=["right_gripper"],
        open_command_value=0.085,
        close_command_value=0.0,
    )
    # NOTE: idle arm (left) is NOT an action — handled by FixedPoseControllerCfg below.


# -----------------------------------------------------------------------------
# Observations — MUST match policy config.yaml ordering for pick_rigid v0.3.1
# -----------------------------------------------------------------------------
@configclass
class ObservationsCfg:
    @configclass
    class PolicyCfg(ObsGroup):
        # Order is load-bearing — policy expects these exact slots.
        tcp_pose_lead = ObsTerm(
            func=mdp.body_pose_b,
            params={"asset_cfg": SceneEntityCfg("robot", body_names=["right_tool0"])},
        )
        tcp_vel_lead = ObsTerm(
            func=mdp.body_vel_b,
            params={"asset_cfg": SceneEntityCfg("robot", body_names=["right_tool0"])},
        )
        gripper_state_lead = ObsTerm(
            func=mdp.gripper_state,
            params={"asset_cfg": SceneEntityCfg("robot", joint_names=["right_gripper"])},
        )
        object_pose_target = ObsTerm(
            func=mdp.target_object_pose_b,
            params={"asset_cfg": SceneEntityCfg("target_object")},
        )
        ft_lead = ObsTerm(
            func=mdp.contact_wrench,
            params={"sensor_cfg": SceneEntityCfg("contact_right_grip")},
        )
        rgb_wrist_lead = ObsTerm(
            func=mdp.wrist_camera_features,  # frozen ResNet → 256-dim
            params={"sensor_cfg": SceneEntityCfg("wrist_cam_right")},
        )
        phase_progress = ObsTerm(func=mdp.phase_progress)

        def __post_init__(self):
            self.enable_corruption = True   # observation noise during training
            self.concatenate_terms = True

    policy: PolicyCfg = PolicyCfg()


# -----------------------------------------------------------------------------
# Rewards — match shaping in 08_training_infrastructure.md
# -----------------------------------------------------------------------------
@configclass
class RewardsCfg:
    approach_progress = RewTerm(
        func=mdp.approach_progress, weight=1.0,
        params={"target_asset": "target_object", "tcp_body": "right_tool0"},
    )
    grasp_axis_alignment = RewTerm(
        func=mdp.grasp_axis_alignment, weight=0.5,
        params={"approach_axis": (0.0, 0.0, -1.0), "tcp_body": "right_tool0"},
    )
    grasp_made = RewTerm(
        func=mdp.grasp_made, weight=5.0,
        params={"contact_sensor": "contact_right_grip", "object": "target_object"},
    )
    lift_clearance_achieved = RewTerm(
        func=mdp.lift_clearance, weight=10.0,
        params={"object": "target_object", "min_clearance_m": 0.05},
    )
    action_norm = RewTerm(func=mdp.action_l2, weight=-0.1)
    force_violation = RewTerm(
        func=mdp.force_violation_indicator, weight=-1.0,
        params={"sensor_cfg": SceneEntityCfg("contact_right_grip"), "threshold_n": 15.0},
    )
    dropped = RewTerm(
        func=mdp.object_dropped, weight=-50.0,
        params={"object": "target_object"},
    )


# -----------------------------------------------------------------------------
# Terminations — mirror failure_predicate from Task Spec
# -----------------------------------------------------------------------------
@configclass
class TerminationsCfg:
    time_out = DoneTerm(func=mdp.time_out, time_out=True)
    success = DoneTerm(
        func=mdp.lift_clearance_predicate,
        params={"object": "target_object", "min_clearance_m": 0.05},
    )
    force_exceeded = DoneTerm(
        func=mdp.force_exceeded,
        params={"sensor_cfg": SceneEntityCfg("contact_right_grip"), "threshold_n": 15.0},
    )
    object_dropped = DoneTerm(
        func=mdp.object_dropped_terminate,
        params={"object": "target_object"},
    )


# -----------------------------------------------------------------------------
# Domain randomization — see 08, randomization budget
# -----------------------------------------------------------------------------
@configclass
class EventCfg:
    reset_object_pose = EventTerm(
        func=mdp.reset_object_within_workspace,
        mode="reset",
        params={
            "object": "target_object",
            "x_range": (0.30, 0.70), "y_range": (-0.30, 0.30), "z": 0.82,
            "yaw_range": (-math.pi, math.pi),
        },
    )
    randomize_object_scale = EventTerm(
        func=mdp.randomize_rigid_body_scale,
        mode="reset",
        params={"object": "target_object", "scale_range": (0.85, 1.15)},
    )
    randomize_object_mass = EventTerm(
        func=mdp.randomize_rigid_body_mass,
        mode="reset",
        params={"object": "target_object", "mass_range": (0.08, 0.40)},
    )
    randomize_friction = EventTerm(
        func=mdp.randomize_friction,
        mode="reset",
        params={"object": "target_object", "friction_range": (0.4, 1.2)},
    )
    randomize_camera_extrinsics = EventTerm(
        func=mdp.randomize_camera_pose,
        mode="reset",
        params={"sensor": "wrist_cam_right",
                "pos_jitter_m": 0.01, "rot_jitter_rad": 0.035},
    )
    randomize_lighting = EventTerm(
        func=mdp.randomize_dome_light,
        mode="reset",
        params={"intensity_range": (500.0, 1500.0)},
    )
    randomize_action_delay = EventTerm(
        func=mdp.randomize_action_delay,
        mode="reset",
        params={"delay_range_s": (0.0, 0.05)},
    )


# -----------------------------------------------------------------------------
# Top-level env config
# -----------------------------------------------------------------------------
@configclass
class PickRigidEnvCfg(ManagerBasedRLEnvCfg):
    scene: PickRigidSceneCfg = PickRigidSceneCfg(num_envs=4096, env_spacing=2.5)
    observations: ObservationsCfg = ObservationsCfg()
    actions: ActionsCfg = ActionsCfg()
    rewards: RewardsCfg = RewardsCfg()
    terminations: TerminationsCfg = TerminationsCfg()
    events: EventCfg = EventCfg()

    def __post_init__(self):
        self.decimation = 4
        self.sim.dt = 1 / 200          # 200 Hz physics
        self.sim.render_interval = self.decimation  # render only when policy ticks
        # Effective control rate: 200 / 4 = 50 Hz, matching policy config.yaml
        self.episode_length_s = 8.0
        self.viewer.eye = (1.5, 1.5, 1.5)
```

## Gym Registration

```python
# manipulation_stack/isaac_lab_envs/__init__.py
import gymnasium as gym
from . import pick_rigid_env

gym.register(
    id="TenThings-PickRigid-v0",
    entry_point="isaaclab.envs:ManagerBasedRLEnv",
    kwargs={"env_cfg_entry_point": pick_rigid_env.PickRigidEnvCfg},
    disable_env_checker=True,
)
```

## Training Run

Use `rsl_rl` (default in Isaac Lab) or `skrl`:

```bash
./isaaclab.sh -p scripts/rsl_rl/train.py \
    --task TenThings-PickRigid-v0 \
    --num_envs 4096 \
    --max_iterations 5000 \
    --headless \
    --logger wandb --log_project_name tenthings-pick-rigid
```

## Export to ONNX

After training, export to the Policy Bank format:

```python
# scripts/export_to_policy_bank.py
import torch, numpy as np, yaml, hashlib, shutil
from pathlib import Path
from rsl_rl.runners import OnPolicyRunner

def export(checkpoint: Path, env_cfg, out_dir: Path, version: str):
    runner = OnPolicyRunner.load(checkpoint)
    policy = runner.alg.actor_critic.actor   # deterministic actor

    obs_dim = env_cfg.observation_space["policy"].shape[0]
    dummy = torch.zeros(1, obs_dim)
    out_dir.mkdir(parents=True, exist_ok=True)

    torch.onnx.export(
        policy, dummy, out_dir / "policy.onnx",
        input_names=["obs"], output_names=["action"],
        dynamic_axes={"obs": {0: "B"}, "action": {0: "B"}},
        opset_version=17,
    )
    np.savez(out_dir / "normalizer.npz",
             mean=runner.alg.obs_normalizer.mean.cpu().numpy(),
             std=runner.alg.obs_normalizer.var.sqrt().cpu().numpy())

    config = build_config_yaml(env_cfg, version)
    (out_dir / "config.yaml").write_text(yaml.safe_dump(config))
    write_card(out_dir / "card.md", env_cfg, runner)

if __name__ == "__main__":
    export(
        checkpoint=Path("logs/rsl_rl/pick_rigid/2026-05-03_12-00-00/model_4999.pt"),
        env_cfg=PickRigidEnvCfg(),
        out_dir=Path("policies/pick_rigid/tenthings_v1_open_arm_bimanual/v0.3.1"),
        version="0.3.1",
    )
```

## Eval Harness

```bash
./isaaclab.sh -p scripts/rsl_rl/play.py \
    --task TenThings-PickRigid-v0 --num_envs 100 \
    --checkpoint policies/.../v0.3.1/policy.onnx \
    --eval_episodes 1000 --headless --record_metrics eval_v0.3.1.json
```

`eval_v0.3.1.json` gets pasted into the policy card. Below threshold (see `08`) → no real-robot promotion.

## Cross-Skill Considerations

- **Bimanual policies** (`bimanual_handover`, `bimanual_fold`): both arms in `ActionsCfg`, both arms' obs in `PolicyCfg`. Otherwise structurally identical.
- **ASSIST policies** trained in two regimes: (a) standalone with a scripted LEAD perturbation generator; (b) co-trained with paired LEAD using two policies in one env. Start with (a); move to (b) only if (a) under-generalizes.
- **Wrist camera features**: keep the encoder frozen across all skills to avoid feature drift between policy versions. Encoder lives in `mdp/` and is loaded once.

## Common Failure Modes

| Symptom | Diagnosis |
|---|---|
| Policy works in sim, fails on real | Domain randomization gap. Check what differs between sim and real, randomize that dimension. |
| Training diverges around step 2M | Reward shaping over-weighted. Drop shaping weights, raise sparse weight. |
| Success rate plateaus at ~50% | Curriculum stage transition too aggressive. Raise success threshold required to advance. |
| Action norm reward dominates | `action_norm` weight too high. Drop to -0.05 or -0.01. |
| Object frequently flies away on grasp | Gripper actuator stiffness or contact solver iter count too low. |
