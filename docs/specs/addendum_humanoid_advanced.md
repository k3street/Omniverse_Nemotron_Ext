# Humanoid Advanced Tooling Addendum

**Enhances:** Phase 7A (RL), Phase 7G (GR00T), Phase 8B (Motion Planning)  
**Source:** Persona P10 (Jin), feature extraction categories 5/6  
**Research:** `rev2/research_gpu_contact_sensor.md`, `rev2/research_whole_body_planning.md`, `rev2/research_loco_manipulation.md`

---

## Overview

Three "research problems" that turned out to have practical, feature-bar components. Each addresses a specific humanoid development pain point.

---

## H.1 — GPU ContactSensor Setup (Fingertip Sensing at Scale)

**Pain:** Adding contact sensing to fingertips for 4096 envs requires knowing buffer sizes, sensor config patterns, and PhysX limits. Users get silent zero forces or crashes.

**Tool:** `setup_contact_sensors(articulation_path, body_names, num_envs)`

"Add contact sensing to the Allegro hand fingertips for 4096 envs"

**Generated code:**
```python
# One ContactSensorCfg per fingertip body (mandatory — one-to-many constraint)
for fingertip in ["thumb_tip", "index_tip", "middle_tip", "ring_tip"]:
    ContactSensorCfg(
        prim_path=f"{{ENV_REGEX_NS}}/Robot/{fingertip}",
        update_period=0.0,  # every physics step
        history_length=1,
        track_air_time=False,
    )

# Critical: increase GPU buffers to avoid silent overflow
PhysxCfg(
    gpu_max_rigid_contact_count=2**24,   # 16M (default 8M insufficient)
    gpu_max_rigid_patch_count=2**23,     # 8M
)
```

**Cheap alternative** (when you just need "is there contact?"):
```python
# Joint reaction forces — zero sensor overhead, already available
joint_forces = articulation.root_physx_view.get_link_incoming_joint_force()
fingertip_forces = joint_forces[:, fingertip_body_ids]
# Includes gravity/inertia contributions — not pure contact, but useful signal
```

---

## H.2 — Whole-Body Motion Planning Setup

**Pain:** No one-command setup for humanoid arm + leg coordination. Users manually wire Pink-IK + HOVER which takes days.

**Tool:** `setup_whole_body_control(articulation_path, locomotion_policy, arm_planner)`

"Set up whole-body control for the G1 with HOVER locomotion and Pink-IK arms"

**Generated code:**
```python
# Locomotion: HOVER RL policy for lower body
locomotion_cfg = LocomotionPolicyCfg(
    checkpoint="hover_g1_flat.pt",
    action_space="lower_body_joints",
    command_type="velocity",
)

# Arm: Pink-IK controller (Pinocchio + QP)
arm_cfg = PinkIKControllerCfg(
    robot_model=articulation_path,
    ee_frame="left_hand",
    tasks=[
        FrameTask(frame="left_hand", position_cost=1.0, orientation_cost=0.5),
        PostureTask(cost=0.01),  # null-space regularization
        DampingTask(cost=0.001),
    ],
)

# Combine in ActionGroupCfg
action_cfg = ActionGroupCfg(
    lower_body=locomotion_cfg,
    upper_body=arm_cfg,
)
```

**Pre-configured profiles:**
| Robot | Locomotion | Arm | Status |
|-------|-----------|-----|--------|
| Unitree G1 | HOVER flat | Pink-IK | Working (IsaacLab 2.3) |
| Unitree H1 | HOVER rough | Pink-IK | Working |
| Figure 02 | Custom | Pink-IK | Manual config required |

**Tool:** `diagnose_whole_body(articulation_path)`

"Why does the robot fall when reaching?"

Checks: balance margin during arm motion, CoM projection vs support polygon, arm payload effect on locomotion policy, EE acceleration during gait.

---

## H.3 — Loco-Manipulation RL Training Advisor

**Pain:** Joint locomotion + manipulation RL training diverges because rewards interfere. Users waste GPU-days on wrong configurations.

**Tool:** `setup_loco_manipulation_training(task_description, robot, approach)`

"Train the G1 to walk to a table and pick up a cup"

**Approach selection:**
| Approach | When | Complexity |
|----------|------|-----------|
| Decoupled (HOVER + IK) | Slow deliberate tasks | Low — already in IsaacLab |
| Hierarchical dual-agent | Dynamic tasks | Medium — SoFTA/FALCON pattern |
| Joint end-to-end | Maximum performance | High — needs reward curriculum |

**Reward mixing advisor:**
```
Your reward terms:
- forward_velocity: weight 1.0
- reach_target: weight 1.0
- grasp_success: weight 5.0

⚠ WARNING: Manipulation reward (grasp_success=5.0) outweighs locomotion.
Early training will optimize grasping at the expense of balance.

Recommendation: 
- Phase 1 (0-2000 iters): locomotion_weight=5.0, manipulation_weight=0.5
- Phase 2 (2000-5000): locomotion_weight=2.0, manipulation_weight=1.0
- Phase 3 (5000+): locomotion_weight=1.0, manipulation_weight=2.0
```

**Tool:** `setup_rsi_from_demos(demo_path, env_cfg)`

Reference State Initialization — highest-impact technique for loco-manipulation. Starts each episode from a state sampled from demonstrations instead of the default pose.

```python
# RSI config addition to env
InitialStateCfg(
    mode="demo_sampling",
    demo_path=demo_path,
    noise_std=0.05,  # small perturbation around demo states
)
```

---

## H.4 — Multi-Rate Policy Wrapper

**Pain:** Lower body needs 50 Hz (locomotion RL). Upper body needs 100 Hz (manipulation IK). IsaacLab runs one rate.

**Tool:** `setup_multi_rate(lower_rate_hz, upper_rate_hz)`

Generate `DualRateVecEnvWrapper`:
```python
class DualRateWrapper(gym.Wrapper):
    def step(self, action):
        # Upper body acts every step (100 Hz)
        upper_action = action[:, :upper_dof]
        
        # Lower body acts every 2nd step (50 Hz)
        if self.step_count % 2 == 0:
            lower_action = action[:, upper_dof:]
            self._cached_lower = lower_action
        else:
            lower_action = self._cached_lower
        
        full_action = torch.cat([upper_action, lower_action], dim=-1)
        return self.env.step(full_action)
```

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| ContactSensor config gen | L0 | Correct buffer sizes, per-body cfg |
| Pink-IK config gen | L0 | Correct task list, frame names |
| Reward phase scheduler | L0 | Known training step → correct weights |
| Multi-rate wrapper | L0 | Known step count → correct action caching |
| RSI demo sampling | L0 | Known demo file → valid initial states |
| Full whole-body control | L3 | Requires IsaacLab + GPU + humanoid asset |
