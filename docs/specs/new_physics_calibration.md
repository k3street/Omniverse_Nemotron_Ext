# New Capability — Physics Parameter Calibration

**Type:** New tool set (enhances Sim-to-Real gap tooling)  
**Source:** Personas P02 (Erik), P04 (Sarah), P10 (Jin)  
**Research:** `rev2/research_physics_calibration.md`

---

## Overview

"The single most expensive problem we have" — calibrating sim physics parameters from real robot data. Currently: guess and iterate for weeks. This provides structured calibration tools.

---

## Approach: Bayesian Optimization (not differentiable physics)

**PhysX is not differentiable** and will not be. No gradient hooks exist. The practical approach: black-box optimization (BO) that runs sim trials and minimizes trajectory mismatch.

**Already installed in isaac_lab_env:** Ray 2.52.1, OptunaSearch. The existing `scripts/reinforcement_learning/ray/tuner.py` infrastructure can be repurposed.

---

## Tools

### `calibrate_physics(real_data_path, articulation_path, parameters_to_calibrate)`

**Type:** Long-running subprocess (30-120 min headless)

**Input:** Real robot data in HDF5:
- Joint positions at 200-500 Hz
- Joint velocities
- Commanded torques (if available)
- 30-120 seconds of excitation trajectory

**What it does:**
```python
import ray
from ray.tune.search.optuna import OptunaSearch

def objective(config):
    # Set physics parameters in sim
    art.write_joint_friction_coefficient_to_sim(config["friction"])
    art.write_joint_damping_to_sim(config["damping"])
    art.set_masses(config["masses"])
    
    # Replay commanded torques in sim
    sim_trajectory = replay_trajectory(real_commands)
    
    # Compute trajectory mismatch
    error = trajectory_distance(sim_trajectory, real_trajectory)
    return {"loss": error}

# Bayesian optimization
analysis = ray.tune.run(
    objective,
    search_alg=OptunaSearch(),
    config={
        "friction": ray.tune.uniform(0.1, 2.0),
        "damping": ray.tune.uniform(0.01, 1.0),
        "masses": ray.tune.uniform(0.8, 1.2) * nominal_masses,
    },
    num_samples=100,
)
best = analysis.best_config
```

**Returns:**
```json
{
  "calibrated_parameters": {
    "joint_friction": [0.45, 0.52, 0.38, 0.61, 0.33, 0.29, 0.44],
    "joint_damping": [0.12, 0.15, 0.08, 0.18, 0.09, 0.07, 0.11],
    "link_masses": [3.2, 2.8, 1.9, 1.2, 0.8, 0.6, 0.3]
  },
  "trajectory_error_before": 12.4,
  "trajectory_error_after": 1.8,
  "suggested_dr_ranges": {
    "friction": "±30% of calibrated values",
    "damping": "±20%",
    "masses": "±5-10%"
  },
  "runtime_minutes": 45
}
```

### `quick_calibrate(real_data_path, articulation_path)`

**Type:** Faster version (~5 min) — calibrates only the most impactful parameters:

1. **Armature (rotor inertia)** — biggest sim-real gap for geared robots
2. **Coulomb friction** — most common parameter to be wrong
3. **Link masses** — if payload matters

Skips: contact stiffness, restitution (these should be randomized, not calibrated).

### `validate_calibration(calibrated_params, test_data_path)`

**Type:** DATA handler

Run a held-out test trajectory with calibrated parameters. Report:
- Trajectory tracking error (should be lower than before calibration)
- Per-joint error breakdown
- Contact force comparison (if F/T data available)

### What to Calibrate vs. What to Randomize

| Parameter | Calibrate? | Randomize? | Why |
|-----------|:---------:|:---------:|-----|
| Armature (rotor inertia) | Yes | ±10% | Biggest impact, measurable |
| Coulomb friction | Yes | ±30% | Varies with temperature/wear |
| Viscous friction | Yes | ±20% | Temperature dependent |
| Link masses | Yes (if payload) | ±5-10% | Measurable from CAD |
| Contact stiffness | No | Full range | Not identifiable from joint data |
| Restitution | No | Full range | Not identifiable from joint data |
| Surface friction | No | Full range | Varies too much with conditions |

---

## Advanced: ActuatorNet (Neural Calibration)

**For highest fidelity:** IsaacLab's `ActuatorNetLSTMCfg` trains a neural network on real `(q_target, q, q_dot, tau)` pairs. It learns friction, backlash, motor dynamics without needing individual parameter identification.

**Tool:** `train_actuator_net(real_data_path, articulation_path)`

This replaces physical parameter calibration with a learned actuator model. Better results but requires more data (minutes of diverse motion) and more compute.

---

## Data Requirements

| Method | Data Needed | Duration | Sensors |
|--------|-----------|----------|---------|
| Quick calibrate | Joint pos + vel + torque | 30s | Encoders + current sensing |
| Full calibrate | Same | 30-120s | Same |
| ActuatorNet | Same + diverse motions | 5-10 min | Same |
| Contact calibrate | Above + contact forces | 30s+ | Wrist F/T sensor |

**Format:** HDF5 with fields: `timestamps`, `joint_positions`, `joint_velocities`, `joint_torques_commanded`. Monotonic timestamps at 200-500 Hz.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| BO objective function | L0 | Mock sim → correct error computation |
| Parameter bounds validation | L0 | Mass/inertia scaling constraint |
| DR range suggestion | L0 | Calibrated value → correct ±% range |
| Full calibration | L3 | Requires IsaacLab + GPU + real data |
