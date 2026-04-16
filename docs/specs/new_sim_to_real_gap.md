# New Capability — Sim-to-Real Gap Analysis

**Type:** New tool set
**Source:** Personas P03 (Sofia — robotics researcher), P08 (Alex — RL engineer), P09 (Fatima — digital twin engineer)

---

## Overview

"How well will my sim policy transfer to the real robot?" -- system analyzes domain randomization coverage, physics fidelity, sensor noise settings, and actuator modeling to identify sim-to-real gaps. Generates a transfer readiness report with actionable recommendations.

---

## Tools

### `analyze_sim_to_real_gap(robot_path, real_robot_type)`

**Type:** DATA handler (analyzes the scene and returns a structured gap report)

Inspects the current scene for a robot and evaluates how well the simulation setup matches real-world conditions across multiple axes: physics parameters, sensor noise, actuator modeling, domain randomization coverage, and environment fidelity.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `robot_path` | string | Yes | USD path to the robot articulation, e.g. '/World/Franka' |
| `real_robot_type` | string | No | Real robot model for reference specs. Default: auto-detect from USD |

**Returns:**
```json
{
  "overall_score": 72,
  "gaps": [
    {"category": "domain_randomization", "severity": "high", "detail": "No randomization on friction, mass, or joint damping detected"},
    {"category": "sensor_noise", "severity": "medium", "detail": "Camera has no noise model — real cameras have Gaussian noise ~0.01"},
    {"category": "actuator_model", "severity": "low", "detail": "Using ideal torque control — consider adding motor dynamics"},
    {"category": "physics_fidelity", "severity": "medium", "detail": "Solver iterations=4 — increase to 16+ for manipulation tasks"}
  ],
  "recommendations": [
    "Add domain randomization for friction (0.5-1.5x), mass (0.8-1.2x), joint damping (0.5-2.0x)",
    "Enable camera noise model: Gaussian sigma=0.01",
    "Switch to DC motor actuator model with velocity limits",
    "Increase solver iterations to 16 for manipulation fidelity"
  ]
}
```

### `apply_domain_randomization(prim_path, randomization_type, params)`

**Type:** CODE_GEN handler (generates Replicator randomization code)

Applies domain randomization to scene objects using Omniverse Replicator randomizers. Supports physics properties, visual appearance, lighting, and sensor noise.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `prim_path` | string | Yes | USD path to the prim to randomize |
| `randomization_type` | string | Yes | Type: 'physics', 'visual', 'lighting', 'sensor_noise' |
| `params` | object | No | Randomization parameters (type-specific) |

**Physics params:** `friction_range` [min, max], `mass_scale_range` [min, max], `joint_damping_range` [min, max], `joint_stiffness_range` [min, max]
**Visual params:** `color_range` [[r_min,g_min,b_min], [r_max,g_max,b_max]], `texture_randomize` bool, `roughness_range` [min, max]
**Lighting params:** `intensity_range` [min, max], `color_temp_range` [min, max], `position_range` [min, max] per axis
**Sensor noise params:** `noise_type` ('gaussian', 'salt_pepper', 'uniform'), `noise_sigma` float, `depth_noise_sigma` float

### `configure_actuator_model(robot_path, actuator_type, params)`

**Type:** CODE_GEN handler (generates actuator configuration code)

Configures the actuator model for a robot to better match real-world motor behavior. Real robots have motor dynamics, torque limits, velocity limits, and friction that ideal simulation controllers lack.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `robot_path` | string | Yes | USD path to the robot articulation |
| `actuator_type` | string | Yes | Type: 'ideal', 'dc_motor', 'position_pid', 'velocity_pid', 'implicit_spring_damper' |
| `params` | object | No | Actuator parameters: `stiffness`, `damping`, `max_torque`, `max_velocity`, `friction` |

### `generate_transfer_report(robot_path, output_format)`

**Type:** DATA handler (generates a comprehensive sim-to-real transfer report)

Combines gap analysis, current randomization settings, physics config, and actuator models into a single report with a transfer readiness score.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `robot_path` | string | Yes | USD path to the robot articulation |
| `output_format` | string | No | Format: 'summary', 'detailed', 'json'. Default: 'summary' |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Gap analysis report structure | L0 | Returns valid gap categories and scores |
| Domain randomization code gen | L0 | Each randomization type generates valid Python |
| Actuator model code gen | L0 | Each actuator type generates valid Python |
| Transfer report generation | L0 | Produces complete report with score |
| Default parameter handling | L0 | Missing optional params use sensible defaults |
