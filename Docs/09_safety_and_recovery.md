# 09 — Safety & Recovery

The safety story isn't a single layer — it's redundant rings, each independent.

## Safety Rings (outer to inner)

```
Ring 1: Hardware E-stop                 (operator hardware button, physical)
Ring 2: Low-level controller limits     (joint torque, velocity limits in firmware)
Ring 3: Action Arbitration limiters     (workspace, self-collision, jerk)
Ring 4: Policy safety wrapper           (action bounds, NaN checks)
Ring 5: Continuity Manager predicates   (force_exceeded, duration_exceeded)
Ring 6: Pi0.5 plan validation           (workspace_bounds, semantic sanity)
```

Rings 1–3 are mandatory always-on. Rings 4–6 are part of the manipulation stack and must remain enabled in any deployment build.

## Force / Torque Watchdog

Independent ROS2 node, not part of any policy or planner. Subscribes to F/T topics, publishes a `/safety/ft_violation` latch when:

- Per-axis force > skill-configured `max_force_n`.
- Per-axis torque > 5 Nm absolute, regardless of skill.
- F/T NaN or stale (no update for > 100 ms).

Latch triggers:
- Action Arbitration switches to SAFE_HOLD.
- Continuity Manager moves to ESCALATING (if recoverable) or fails to operator.

## Recovery Primitives

Pre-baked, deterministic — not learned:

| Primitive | When | Behavior |
|---|---|---|
| `freeze_in_place` | F/T violation, IK fail storm | Hold all joint positions. Open grippers if not currently carrying. |
| `retract_to_safe` | After freeze, before re-engaging | Both arms slowly to retract pose. Carries are dropped over a soft target zone if possible, or held. |
| `release_and_retract` | Object stuck or unsafe | Open carrying gripper, then retract. Used when the carry itself is the hazard. |
| `rescan_workspace` | Object lost | Hold arms steady, request fresh perception sweep, return updated scene to Pi0.5. |

These are callable by the Continuity Manager; they don't have learned variants.

## Re-engagement Policy

After SAFE_HOLD or `freeze_in_place`, the Continuity Manager does NOT auto-resume. Required steps:

1. Operator acknowledgment (UI button or programmatic clearance).
2. Fresh perception scan.
3. Pi0.5 replan from current state.
4. Resume from the new plan, not the old one.

The cost of being conservative here is one extra prompt. The cost of auto-resuming after a force violation is bent fingers.

## Object Drop Handling

If `object_attached` predicate flips to false unexpectedly mid-phase (carry dropped):

1. Continuity Manager escalates immediately with `failure_reason: "carry_lost"`.
2. Pi0.5 replans, typically with a new pick phase.
3. Recovery happens through normal phase execution, not a special path.

This keeps the failure-handling code one path, not two.

## Operator Override

A topic the operator can publish to at any time:
```
/safety/operator_command : { command: PAUSE | RESUME | ABORT | SAFE_HOLD }
```

- `PAUSE` — Continuity Manager freezes; Action Arbitration holds.
- `RESUME` — only valid from PAUSE state.
- `ABORT` — drop task, retract, return to IDLE.
- `SAFE_HOLD` — same as failure-triggered SAFE_HOLD.

These are blocking against any policy decision — operator override always wins over RL or Pi0.5 output.

## Workspace Bounds Enforcement

Workspace bounds are computed from:
- Telescope height (read from ROS).
- Embodiment kinematic reach.
- Static obstacles in the environment (operator-configured).

Republished whenever telescope height changes. The Action Arbitration node refuses TCP commands outside bounds — clamps and logs.

The Continuity Manager refuses to start a task if any phase's `semantic_target` lies outside current workspace bounds.

## Logging & Black Box

Every safety event logs with high detail:
- 5 s pre-event observation buffer (proprioception + F/T + downsampled images).
- Active phase, skill, policy version.
- Action commanded vs action published (diff after limiters).
- Predicate trace.

Stored to disk per-event. Rotated by size (10 GB total cap by default).

## What This Stack Does NOT Do for Safety

- It does not certify safety claims for human-shared workspaces. That's a hardware + safety-rated controller question.
- It does not detect humans entering the workspace. Add a separate safety camera + ISO 10218 / ISO 13482 layer if you operate around people.
- It does not prevent all collisions. The bimanual collision check is approximate, not certified.

These are honest scope limits. Treat them as work items if your deployment context requires them.
