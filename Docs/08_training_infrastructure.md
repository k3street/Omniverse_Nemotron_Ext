# 08 — Training Infrastructure

Where the RL skills come from. Isaac Lab is the default sim; Isaac Sim provides the physics. Real-robot fine-tuning happens in a separate stage.

## Sim-to-Real Pipeline

```
Stage 1: Sim training (Isaac Lab)        ─┐
                                          ├─▶ Stage 3: Real fine-tune (residual RL)
Stage 2: Sim-to-sim domain randomization ─┘
```

### Stage 1: Sim training

Isaac Lab environment per skill. Each environment exposes:
- Observation matching the policy's `config.yaml` schema exactly.
- Reward shaped per skill (see below).
- Termination conditions matching the failure predicates from `02_task_spec_protocol.md`.
- Curriculum staging.

PPO with these defaults as a starting point:
```yaml
algorithm: PPO
num_envs: 4096
rollout_length: 64
minibatch_size: 16384
learning_rate: 3.0e-4
gamma: 0.99
gae_lambda: 0.95
clip_range: 0.2
entropy_coef: 0.005
value_coef: 1.0
total_steps: 50_000_000   # for medium-difficulty skills
```

Tune per skill, but resist the urge to fork these per-skill until you have evidence that defaults fail.

### Stage 2: Domain randomization

Per-episode randomization over:
- Object pose (within workspace bounds).
- Object scale (±15%).
- Object mass (±30%) and friction.
- Camera extrinsics (small jitter, ±1 cm position, ±2° rotation).
- Lighting intensity and color temperature.
- Visual texture (procedural per-episode).
- Sensor noise (Gaussian on joint positions, F/T).
- Action delay (0–50 ms).
- Initial joint configuration.

If a skill works in sim but fails on real hardware, the answer 90% of the time is "increase randomization on the dimension that differs between sim and real." Track which randomization axes matter via ablations.

### Stage 3: Real fine-tune

Two strategies:

**Residual RL (default):**
- Freeze sim policy as base.
- Train a small residual policy (~100K params) that adds to base actions.
- On-policy collection on real robot, ~2000–10000 steps depending on skill.
- Reward: same as sim, evaluated from real observations + scene tracker.

**Demo-conditioned offline + online (for tricky skills):**
- Collect 100–500 teleoperated demos.
- Behavior cloning warm start.
- IQL or Cal-QL offline pretrain.
- Short on-policy fine-tune (~1000 steps) for OOD recovery.

## Reward Shaping (per skill, examples)

### `pick_rigid`
```
r =  + 1.0 * approach_progress           # decreasing distance to grasp pose
     + 0.5 * grasp_axis_alignment        # cosine to approach axis
     + 5.0 * grasp_made                  # gripper closed on object
     + 10.0 * lift_clearance_achieved
     - 0.1 * action_norm                 # prefer small actions
     - 1.0 * force_violation_indicator
     - 50.0 * dropped                    # binary, if object falls after grasp
```

### `place_on_surface`
```
r =  + 1.0 * approach_progress_to_place_pose
     + 0.5 * orientation_alignment
     + 5.0 * contact_made_on_support
     + 10.0 * release_completed_with_object_resting
     - 0.1 * action_norm
     - 5.0 * placed_off_support
```

### `bimanual_handover`
```
r =  + 1.0 * approach_progress (both arms toward handover region)
     + 2.0 * pose_match_in_handover_zone     # arms aligned for transfer
     + 5.0 * second_grasp_acquired
     + 5.0 * first_release_after_second_grasp
     + 10.0 * stable_carry_in_receiving_arm
     - 50.0 * dropped
```

Reward shaping is iterative. Start sparse (success only), add shaping when sample efficiency demands it. Over-shaped rewards produce policies that game the proxy and fail on real.

## Curriculum

Each skill has a difficulty schedule:

```yaml
pick_rigid:
  stages:
    - name: easy
      until_success_rate: 0.90
      randomization:
        object_pose_xy_range: 0.05    # 5 cm
        object_yaw_range: 0.3
        clutter_objects: 0
    - name: medium
      until_success_rate: 0.85
      randomization:
        object_pose_xy_range: 0.20
        object_yaw_range: 1.5
        clutter_objects: 2
    - name: hard
      until_steps: 20_000_000
      randomization:
        object_pose_xy_range: 0.40
        object_yaw_range: 3.14
        clutter_objects: 5
        object_geometry_set: full
```

Advance when success rate threshold met for 5 consecutive evaluations.

## Eval Suite

Per skill, before declaring a policy ready for real:

| Metric | Target | Measurement |
|---|---|---|
| Sim success rate (held-out objects) | ≥ 90% | 1000 episodes, novel object set |
| Sim success rate (held-out poses) | ≥ 85% | Object poses outside training distribution |
| Force compliance | 99% within max_force_n | Per-step F/T monitoring |
| Action smoothness | Mean jerk < threshold | Action time-series analysis |
| Predicate-conformant termination | ≥ 95% | End condition matches success_predicate |

Failing eval blocks promotion to real fine-tuning.

## Compute Footprint

For a skill at 10M sim steps, 4096 parallel envs, on a single H100 or DGX Spark:
- Isaac Lab + PPO: 8–16 GPU-hours typical.
- Bimanual sync skills: 30–50 GPU-hours.

Plan: train 2–3 skills in parallel on separate GPUs; the bottleneck for production isn't compute, it's reward shaping iteration.

## Versioning & Reproducibility

Every trained policy gets a card (`card.md`):

```markdown
# pick_rigid v0.3.1

- Embodiment: tenthings_v1_open_arm_bimanual
- Sim env: isaac_lab_v4.5, env hash ab12cd34
- Algorithm: PPO, config hash 7e8f9a01
- Total training steps: 10,000,000
- Final sim eval: 92.3% success on held-out 1000-episode set
- Real fine-tune: residual RL, 4500 steps, +6 demos
- Real eval (n=50): 88% success
- Known failures: thin objects (<2 cm), reflective surfaces
- Predecessor: v0.2.4 (sim-only, 81% real)
```

Without a card, a policy doesn't deploy.
