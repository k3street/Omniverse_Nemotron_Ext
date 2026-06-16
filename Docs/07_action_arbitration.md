# 07 — Action Arbitration

A ROS2 node that takes per-arm action commands from the Continuity Manager and produces low-level joint commands for both arms. Handles single-arm, ASSIST, and synchronized bimanual modes uniformly.

## Inputs

```
/manipulation/action_lead    → ArmAction  (action + arm_id)
/manipulation/action_assist  → ArmAction  (optional)
/manipulation/mode           → Mode       (SINGLE / ASSIST / SYNC_BIMANUAL / SAFE_HOLD)
```

## Outputs

```
/arm_left/joint_command      → JointTrajectoryPoint (or compatible low-level)
/arm_right/joint_command     → JointTrajectoryPoint
/gripper_left/command        → GripperCommand
/gripper_right/command       → GripperCommand
```

## Mode Behaviors

### SINGLE
- One arm's action comes from RL.
- Other arm holds a fixed retract pose via PD control. Retract pose per embodiment in `config/retract_poses.yaml`.
- IDLE arm is NOT a learned policy — it's a deterministic fallback. Treat the IDLE arm as out of the way and predictable.

### ASSIST
- LEAD arm: RL action from `action_lead`.
- ASSIST arm: RL action from `action_assist` (different policy).
- Both arms publish independently. No coupling at this level.

### SYNC_BIMANUAL
- Single policy emits actions for both arms in one `/act` call.
- Continuity Manager publishes both as `action_lead` (with arm_id="both").
- Arbitration splits and forwards.

### SAFE_HOLD
- Both arms freeze at current joint position.
- Grippers maintain current state.
- Triggered on F/T violation, ROS `/base_stationary` drop, or operator override.

## Action → Joint Command

Each policy emits action in its declared format (see `04`):

| action.type | Format | Resolver |
|---|---|---|
| `delta_tcp_pose_plus_gripper` | (3 dpos, 3 drot, 1 grip_width) | IK on (current_tcp ⊕ delta), publish joint targets + gripper |
| `joint_delta` | (n_dof) | Add to current joint positions, publish |
| `joint_position` | (n_dof) | Publish directly |
| `tcp_pose_plus_gripper` | (3 pos, 4 quat, 1 grip_width) | IK absolute, publish |

For TCP-space actions, IK uses cuRobo or TracIK with the current joint config as seed. IK failure → publish hold-current-position for that tick + flag the Continuity Manager (returns to RL as `info.ik_fail = True`).

## Action Bounds & Limiters

Applied **after** the policy's own safety wrapper, as a second line of defense:

1. **Joint limit clamp** — never command outside hardware joint limits.
2. **Velocity limit** — joint velocity from previous to commanded, clipped to per-joint max.
3. **Workspace clamp** — TCP position clamped to global workspace bounds (read from telescope-aware config).
4. **Self-collision avoidance** — quick check via cuRobo collision world; if collision predicted, freeze that arm for the tick.
5. **Bimanual collision avoidance** — both arms checked against each other every tick. The geometry of an open-arm-style bimanual platform creates real collision risk between elbows and forearms, especially at certain torso heights.

## Gripper Commands

Gripper commands are decoupled from arm motion in the controller layer — the gripper has its own command topic. Action Arbitration translates `grip_width` field of the action into the gripper's native command format (e.g., position + max_force).

Gripper force ceiling per skill is enforced here using the phase's `max_force_n` constraint — Action Arbitration receives this from Continuity Manager via the `mode` message context.

## Hand-Off Frames

When transitioning phase N (LEAD=right) → phase N+1 (LEAD=left), there's a moment where the previously-LEAD arm becomes IDLE. Sequence:

1. Continuity Manager publishes phase boundary event.
2. Arbitration freezes the now-IDLE arm at its current pose for 200 ms.
3. Arbitration smoothly interpolates that arm to its retract pose over 1.5 s.
4. New LEAD arm starts receiving RL actions immediately.

This avoids the "both arms move suddenly" failure mode at phase boundaries.

## Failure Domains

| Symptom | Response |
|---|---|
| IK fail rate > 30% over 0.5 s window | Soft stop, escalate `pose_unreachable`. |
| Self-collision predicted | Freeze offending arm, log, notify Continuity Manager. |
| Joint velocity clamp engaged > 50% of ticks | Log; usually means RL policy needs retraining or your control rate is too low. |
| Lost arm state (joint_states timeout) | SAFE_HOLD immediately. |
