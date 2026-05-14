# Compliance Tuning Guide

**Audience:** Template authors, workflow engineers, robot programmers.
**Spec reference:** `docs/specs/2026-05-11-contact-rich-manipulation-spec.md`

---

## 1. Overview

Compliance control lets the robot yield to external contact forces
instead of slamming blindly into an obstacle. The robot behaves like a
virtual spring-damper at its end-effector (EE), absorbing force without
destabilizing.

**When to use it:**

- Peg-in-hole or shaft insertion — small misalignments need to be
  absorbed by lateral compliance.
- Surface-following / wiping — EE must maintain constant normal force
  on an uneven surface.
- Cooperative assembly — robot holds a part while a human aligns it.
- Any task where the goal region is smaller than the robot's position
  repeatability.

**When NOT to use compliance** — see §8 of this guide.

### 1.1 The spring-damper model (spec §1.2)

The underlying control law:

```
F = K · (x_desired - x_actual) - D · v_actual + F_external_feedback
```

Where:

| Symbol | Meaning | Unit |
|---|---|---|
| `K` | Stiffness matrix (virtual spring) | N/m (translational), N·m/rad (rotational) |
| `D` | Damping matrix (virtual damper) | N·s/m (translational), N·m·s/rad (rotational) |
| `F_external_feedback` | F/T sensor reading fed back into the loop | N |
| `x_desired` | Desired EE pose (from trajectory) | m |
| `x_actual` | Actual EE pose | m |
| `v_actual` | Actual EE velocity | m/s |

Lowering K makes the robot softer (more compliant). Raising D adds
damping, reducing oscillation. The virtual mass M (if your controller
exposes it) governs inertia felt by the environment.

---

## 2. Mode selection: admittance vs impedance vs FDCC

All three implement the spring-damper model but differ in which
direction the signal flows and which hardware they require.

### 2.1 Decision tree

```
Is the robot in torque-control mode (e.g. Franka FCI libfranka)?
├─ YES → Do you need the lowest possible latency or real-robot sim2real?
│         ├─ YES, Franka FCI → use  franka_cartesian_impedance
│         └─ YES, other torque-mode → use  cartesian_impedance
└─ NO (position-mode: UR10e, Franka position-mode, Kinova, etc.)
      Does the task need an explicit target-force profile
      (e.g. press with exactly 10 N)?
      ├─ YES → use  FDCC  (cartesian_compliance_fdcc)
      └─ NO  → use  admittance   ← DEFAULT for most tasks
```

If you have no preference and the task has a contact phase, the
auto-picker (§7 of this guide, spec §4) selects `admittance`
automatically. You never need to touch this unless you have a specific
reason to override.

### 2.2 Variant comparison (spec §3)

| Variant | Signal direction | Hardware requirement | Typical use |
|---|---|---|---|
| `admittance` | force→motion | Position-mode + F/T sensor | DEFAULT. Any robot, peg-in-hole, wiping |
| `FDCC` | hybrid motion+force | Position-mode | Explicit wrench tracking (press-fit, grinding) |
| `cartesian_impedance` | motion→torque | Torque-mode robot | Non-Franka torque-mode robots |
| `franka_cartesian_impedance` | motion→torque | Franka FCI only | Best sim2real fidelity for real Franka |
| `variable_impedance` | K varies with phase | Any | Peg search (low K) → insertion settle (high K) |
| `null` | none (rigid) | Any | Free-space tasks, A/B comparison baseline |

**Cross-reference:** spec §3 contains the full variant matrix with
upstream package links (ros2_controllers, fzi cartesian_controllers,
matthias-mayr Cartesian-Impedance-Controller).

### 2.3 Concrete examples

**Example A — UR10e peg insertion:**
UR10e is position-mode only. Auto-pick → `admittance`. You never need
to set `compliance_mode` explicitly.

**Example B — Franka peg insertion (sim only):**
Auto-pick → `admittance` (safe sim default even though FCI torque-mode
is available in Isaac Sim).

**Example C — Franka real-robot deployment:**
Add `"real_robot_deployment"` structural tag to intent → auto-pick →
`franka_cartesian_impedance`. Hardware-tuned gains transfer better.

**Example D — Grinding / force-controlled surface contact:**
Target is 15 N normal force (not target position). Use `FDCC`. Set
`stiffness_xyz` high in Z (pressing axis) and provide a wrench
reference trajectory alongside the pose trajectory.

---

## 3. Tuning K/D/M for common tasks

The parameters are set when calling `setup_admittance_controller` or
`setup_impedance_controller`, and can be mutated live via
`set_compliance_params`.

Parameter naming convention across all tools:

- `stiffness_xyz` / `stiffness_rot` → K (translational / rotational)
- `damping_xyz` / `damping_rot` → D
- `mass_xyz` / `mass_rot` → M (virtual inertia — admittance only)

All lists are 3-element `[x, y, z]`.

**Critical damping condition:**
```
D_critical = 2 * sqrt(K * M)
```
Set D ≥ D_critical to avoid oscillation. A factor of 0.7–1.0× critical
is a good starting point.

---

### 3.1 Peg-in-hole insertion

The peg must search laterally for the hole, then lock in and seat fully.
Use `variable_impedance` for the best results, or manually shift K in
two phases.

**Phase 1 — search (soft, position error tolerance):**

| Parameter | Value | Notes |
|---|---|---|
| `stiffness_xyz` | `[100, 100, 300]` | Low X/Y → absorbs lateral misalign; higher Z → maintains downward progress |
| `damping_xyz` | `[20, 20, 40]` | 2× sqrt(K×M) for critical damping with M=1 |
| `mass_xyz` | `[1.0, 1.0, 1.0]` | Default virtual inertia |
| `stiffness_rot` | `[30, 30, 10]` | Low yaw rotation stiffness → allows self-centering |
| `damping_rot` | `[6, 6, 2]` | |
| `compliance_handoff_at` | `0.6` | Start compliance early, before first contact |

**Phase 2 — insertion settle (stiffer, lock orientation):**

After first contact is detected (F/T sensor exceeds contact threshold),
call `set_compliance_params` to shift K:

| Parameter | Value | Notes |
|---|---|---|
| `stiffness_xyz` | `[400, 400, 600]` | Stiffer → faster seating convergence |
| `damping_xyz` | `[40, 40, 60]` | |
| `stiffness_rot` | `[60, 60, 20]` | Tighter rotation lock |

**Tuning rules of thumb (peg-in-hole):**

1. If the peg deflects too much and misses the hole → raise K_xy slightly
   (10–20% steps).
2. If the peg jams at the chamfer → lower K_z; the robot is pressing
   too rigidly in Z.
3. If `compliance_handoff_at` is too late (robot contacts hole edge
   rigid) → move it earlier, toward 0.4.
4. K_xyz values above 800 N/m in sim often cause PhysX instability —
   check `get_contact_report` for bounce artifacts.

---

### 3.2 Surface-following / wiping

The EE must maintain a constant normal force and track a surface
trajectory without losing contact or pressing too hard.

**Starting values:**

| Parameter | Value | Notes |
|---|---|---|
| `stiffness_xyz` | `[800, 800, 150]` | High X/Y → tight lateral tracking; LOW Z → soft normal direction |
| `damping_xyz` | `[60, 60, 25]` | Z damping reduces normal-force chattering |
| `mass_xyz` | `[0.5, 0.5, 0.5]` | Lower M → faster response to surface undulation |
| `stiffness_rot` | `[80, 80, 10]` | High Rx/Ry → maintains surface-perpendicular orientation; low Rz |
| `damping_rot` | `[8, 8, 2]` | |
| `compliance_handoff_at` | `0.3` | Compliance on early, before surface contact |

**Tuning rules of thumb (surface-following):**

1. Too much normal-force variation → raise D_z (not K_z).
2. EE lifts off on surface dips → lower K_z further (toward 80 N/m).
3. Lateral slip during wiping stroke → raise K_xy.
4. Chatter / high-frequency oscillation on surface → lower K_z,
   raise D_z. See §4 (Troubleshooting: Oscillation).
5. Wiping path shows wave artifact at trajectory waypoints → the
   waypoint spacing may be too coarse; increase waypoint density or
   lower `velocity_scaling`.

---

### 3.3 Cooperative assembly

A human or second robot holds a mating part and the primary robot
inserts a sub-component. The primary robot must be very soft
(compliant to hand forces) in all directions while maintaining approach
direction.

**Starting values:**

| Parameter | Value | Notes |
|---|---|---|
| `stiffness_xyz` | `[80, 80, 120]` | Very low → yields to human hands |
| `damping_xyz` | `[18, 18, 22]` | 2× sqrt(K×M) approx |
| `mass_xyz` | `[2.0, 2.0, 2.0]` | Higher M → smoother/slower response (feels safer) |
| `stiffness_rot` | `[20, 20, 20]` | Low rotation stiffness → soft to rotation |
| `damping_rot` | `[4, 4, 4]` | |
| `compliance_handoff_at` | `0.5` | Default; activate compliance at midpoint |

**Tuning rules of thumb (cooperative):**

1. If the robot feels jerky when a human pushes it → raise M_xyz
   (adds inertia, smooths perceived force response).
2. If the robot drifts slowly under gravity when a human lets go →
   raise K_xyz slightly (10 N/m steps) or add gravity compensation.
3. For prolonged cooperative holds (>10 s), watchdog-timer-based
   `release_compliance` call prevents indefinite drift.
4. Lower `velocity_scaling` to 0.3–0.5 for cooperative tasks — slower
   motion gives the human time to correct.

---

## 4. Troubleshooting

### 4.1 Oscillation at contact

**Symptoms:** EE bounces rapidly after touching the workpiece; F/T
sensor shows alternating positive/negative force. PhysX velocity may
spike in `get_contact_report`.

**Causes and fixes:**

| Check | Fix |
|---|---|
| D too low for current K | Raise D until ≥ 2×sqrt(K×M); start with 2× then tune down |
| K too high for the task | Lower K_xyz by 20–30% steps until bouncing stops |
| PhysX timestep too coarse | Check `get_physics_scene_config`; use 60Hz+ physics tick |
| `mass_xyz` too low (< 0.3) | Raise M; extremely low virtual mass causes near-zero damping time constants |

Quick formula: for K=400, M=1.0 → D_critical = 2×sqrt(400×1) = 40.
If D < 40, expect oscillation.

### 4.2 Drift under no external load

**Symptoms:** EE slowly creeps away from the desired pose when no
contact force is present. The compliance controller is "over-
compliant."

**Causes and fixes:**

| Check | Fix |
|---|---|
| K too low | Raise K_xyz (at least 200 N/m for most tasks) |
| Gravity not compensated | Enable gravity compensation in controller config or use `apply_physics_material` to cancel effective gravity in sim |
| F/T sensor DC offset | Calibrate F/T zero offset before activating compliance; pass `noise_std=0.0` in dev, add calibration offset in `publish_topic` pipeline |

### 4.3 Sluggish response (controller slow to react)

**Symptoms:** The EE takes 500+ ms to respond to a contact impulse.
The task looks correct but too slow.

**Causes and fixes:**

| Check | Fix |
|---|---|
| M too high | Lower M_xyz toward 0.5 kg |
| D too high (overdamped) | Reduce D until response time meets SLA; target <50ms handoff transition per spec §10 |
| ROS2 bridge latency | Check Option A (external graph) latency; if >10ms consistently, evaluate Option B (in-Kit port) per spec §13.1 |
| `velocity_scaling` too low | Raise toward 1.0 |

### 4.4 Velocity spikes during contact transition

**Symptoms:** Large velocity jump at the moment of first contact.
`get_contact_report` shows momentary forces > 10× steady-state.

**Root cause:** Phase 80b `grip_safe_mode` is not active. The
PhysX contact impulse is not clamped.

**Fix:**
1. Verify Phase 80b `grip_safe_mode=True` is set before compliance
   is installed. Call `check_physics_health` to confirm.
2. If spikes persist: lower `compliance_handoff_at` so the robot is
   already compliant before the expected contact point.
3. Lower approach `velocity_scaling` to 0.3 for the contact phase.

### 4.5 "Live mode raises NotImplementedError"

**Expected behavior.** The compliance handler returns:

```
NotImplementedError("requires Kit RPC + ros2_control bridge")
```

This is correct and by design (spec §5.1 / §19.3). The dry-run path
executes the spring-law math and returns a config dict. The live path
requires:

1. Kit RPC service running (see **CRM-A1** — `docs/specs/
   2026-05-11-contact-rich-manipulation-spec.md` §18.1)
2. ros2_control bridge active (Option A: ros2_bridge graph running;
   Option B: in-Kit port configured)
3. F/T sensor `publish_topic` wired up (see `add_force_torque_sensor`
   with `publish_topic` kwarg — spec §5.6)

Until the bridge is live, dry-run mode is useful for verifying parameter
config and testing templates offline.

---

## 5. Cross-reference to Phase 63b — trajectory handoff

The `follow_trajectory_with_compliance` tool is the bridge between
Phase 63b (constrained trajectory planning) and this compliance layer.

**The key invariant:**

```
compliance_handoff_at == lock_orientation_from
```

Phase 63b's `plan_constrained_trajectory` returns a trajectory that
has its orientation axis locked starting at the fraction
`lock_orientation_from` (typically 0.5). This spec's
`follow_trajectory_with_compliance` switches from rigid to compliant
control at `compliance_handoff_at`. Both must use the same fraction
for a seamless transition.

**Concrete example — peg-in-hole with Phase 63b trajectory:**

```python
# Step 1: plan constrained trajectory (Phase 63b)
traj_result = await tool_executor.execute_tool_call(
    "plan_constrained_trajectory",
    {
        "robot_path": "/World/Franka",
        "goal_pose": {"position": [0.5, 0.0, 0.1]},
        "lock_axis": "z",
        "lock_orientation_from": 0.5,     # <-- Phase 63b sets this
    }
)
trajectory = traj_result["trajectory"]

# Step 2: install admittance compliance
await tool_executor.execute_tool_call(
    "setup_admittance_controller",
    {
        "robot_path": "/World/Franka",
        "stiffness_xyz": [100, 100, 300],
        "damping_xyz": [20, 20, 40],
        "ft_sensor_path": "/World/Franka/ft_sensor",
    }
)

# Step 3: execute with handoff
result = await tool_executor.execute_tool_call(
    "follow_trajectory_with_compliance",
    {
        "trajectory": trajectory,
        "robot_path": "/World/Franka",
        "compliance_handoff_at": 0.5,     # <-- MUST match lock_orientation_from
        "compliance_controller": "admittance",
    }
)
# result keys: ok, t_handoff_observed, contact_detected_at, final_pose, ft_at_handoff
```

**What happens if `compliance_handoff_at` != `lock_orientation_from`:**

The tool emits a `handoff_mismatch_warning` in the result but does NOT
fail hard. The mismatch means the orientation-locked segment and the
compliance-active segment are out of phase — the robot may wobble at
the transition. Align the fractions to remove the warning.

**Template-level shortcut:** if you set `compliance_handoff_at` in the
template JSON, Phase 63b defaults its `lock_orientation_from` to the
same value automatically (spec §6).

---

## 6. Tool reference

Five tools are landed by this spec. Quick reference:

| Tool | Purpose | Spec section |
|---|---|---|
| `setup_admittance_controller` | Install ros2_control admittance layer; configure K/D/M per §5.1 | [§5.1](../specs/2026-05-11-contact-rich-manipulation-spec.md#51-setup_admittance_controller) |
| `setup_impedance_controller` | Install Cartesian impedance (torque-mode only); configure Kx/Kr/Dx/Dr per §5.2 | [§5.2](../specs/2026-05-11-contact-rich-manipulation-spec.md#52-setup_impedance_controller) |
| `set_compliance_params` | Runtime mutation of K/D/M on an already-installed controller; used for variable-impedance K-schedule | [§5.3](../specs/2026-05-11-contact-rich-manipulation-spec.md#53-set_compliance_params) |
| `release_compliance` | Remove compliance layer; restore rigid joint-target path; idempotent | [§5.4](../specs/2026-05-11-contact-rich-manipulation-spec.md#54-release_compliance) |
| `follow_trajectory_with_compliance` | Execute Phase 63b constrained trajectory with rigid-to-compliant handoff at specified fraction | [§5.5](../specs/2026-05-11-contact-rich-manipulation-spec.md#55-follow_trajectory_with_compliance-phase-63b--layer-1-bridge) |

All tools operate in dry-run mode by default (return config dict,
compute spring-law math). Live execution requires Kit RPC + ros2_control
bridge (see CRM-A1).

---

## 7. Auto-pick reference

You do not need to call `setup_admittance_controller` explicitly in
most workflows. Phase 20's role-binder auto-picks `compliance_mode`
from the intent's structural features.

**How it works (spec §4 / CRM-C2):**

```python
# Simplified view of auto-pick logic (see role_retriever.py)
if not intent.structural_features.has_contact_phase:
    compliance_mode = None          # rigid — free-space task
elif robot_class == "franka_panda" and "real_robot_deployment" in tags:
    compliance_mode = "franka_cartesian_impedance"
else:
    compliance_mode = "admittance"  # safe default for all position-mode robots
```

The auto-pick runs as part of `apply_layout_spec_to_scene` before
the motion plan is generated. By the time the trajectory is planned,
the controller is already installed.

**Override example (rare):**

```jsonc
{
  "task_id": "CP-MY-TASK",
  "compliance_mode": "variable_impedance",   // explicit override
  "compliance_params": {
    "stiffness_xyz": [200, 200, 100]
  },
  "compliance_handoff_at": 0.55
}
```

Overrides are validated against ~20 hard-incompatibility rules (spec
§4.2). If you specify `cartesian_impedance` but the robot is
position-mode, the validator returns an actionable error before the
scene is built.

**Reference: CRM-C2** (`docs/specs/2026-05-11-contact-rich-manipulation-
spec.md` §18.3, task CRM-C2) — implements the auto-pick algorithm in
`role_retriever.py`.

---

## 8. When NOT to use compliance

Compliance adds latency and introduces spring-law dynamics. For
tasks where rigid joint-target control is correct, leave
`compliance_mode = null`.

**Skip compliance when:**

| Scenario | Reason |
|---|---|
| Free-space pick-and-place (object on flat surface, no hole) | No contact uncertainty; rigid is faster and exact |
| High-speed conveyor pick | Compliance lag conflicts with precise timing |
| Pure positioning task (move to pose, no contact) | The spring-damper adds positional error relative to commanded pose |
| Welding / cutting with known fixture | Part is hard-fixtured; contact force is not the control variable |
| A/B baseline comparison | Always run rigid baseline first (`compliance_mode=null`) to isolate compliance contribution |

**How to confirm rigid is safe:**
Run `check_collisions` after trajectory planning with rigid mode.
If no penetrations are predicted along the path, rigid is appropriate.

**Template declaration:**

```jsonc
{
  "task_id": "CP-FREE-SPACE-PICK",
  "compliance_mode": null        // explicit null = rigid; auto-pick would also select null
}
```

`compliance_mode: null` is the default when
`intent.structural_features.has_contact_phase = false`. No action
needed — omitting the field gives identical behavior.

---

## Appendix A — K_xyz / D_xyz quick reference

Typical starting values by task class. All units: N/m (stiffness),
N·s/m (damping), kg (mass).

| Task class | K_xyz (x, y, z) | D_xyz (x, y, z) | M_xyz |
|---|---|---|---|
| Peg search | 100, 100, 300 | 20, 20, 40 | 1, 1, 1 |
| Peg settle (post-contact) | 400, 400, 600 | 40, 40, 60 | 1, 1, 1 |
| Surface wiping | 800, 800, 150 | 60, 60, 25 | 0.5, 0.5, 0.5 |
| Cooperative hold | 80, 80, 120 | 18, 18, 22 | 2, 2, 2 |
| Stiff position (near-rigid) | 1200, 1200, 1200 | 80, 80, 80 | 1, 1, 1 |

Rotational stiffness (N·m/rad) is typically K_xyz / 10.
Rotational damping is typically D_xyz / 10.

---

## Appendix B — variable_impedance K-schedule

`variable_impedance` mode switches K automatically based on contact
phase. The schedule is two-phase:

1. **Search phase** (default K: low) — robot explores the contact region.
2. **Insertion settle** (K: high) — activated on first F/T threshold
   crossing.

You can manually replicate this with `set_compliance_params`:

```python
# Search phase: soft
await set_compliance_params(
    robot_path="/World/Franka",
    stiffness_xyz=[100, 100, 300],
    damping_xyz=[20, 20, 40],
)

# ... wait for contact detection (F/T > threshold) ...

# Insertion settle: stiffer
await set_compliance_params(
    robot_path="/World/Franka",
    stiffness_xyz=[400, 400, 600],
    damping_xyz=[40, 40, 60],
)
```

`variable_impedance` automates this transition. Manual `set_compliance_params`
calls are preferred when the contact threshold is task-specific and
not well-captured by the default.

---

*Guide covers spec CRM-D3. For the full compliance architecture, see
`docs/specs/2026-05-11-contact-rich-manipulation-spec.md`.*
