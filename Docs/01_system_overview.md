# 01 — System Overview

## Runtime Topology

Three processes, all on the on-board compute (e.g., Jetson Thor or DGX Spark):

```
┌──────────────────────────────────────────────────────────────────┐
│                     ROS2 Domain (existing)                       │
│  /base_stationary  /tf  /joint_states  /camera/*  /telescope/*   │
└──────────────────────────────────────────────────────────────────┘
            │                      │                      │
            ▼                      ▼                      ▼
   ┌────────────────┐    ┌──────────────────┐    ┌──────────────────┐
   │ Observation    │    │ Continuity       │    │ Action           │
   │ Pipeline       │───▶│ Manager          │───▶│ Arbitration      │
   │ (ROS2 node)    │    │ (Python service) │    │ (ROS2 node)      │
   └────────────────┘    └──────────────────┘    └──────────────────┘
                                │      ▲
                       task_spec│      │ phase_event
                                ▼      │
                         ┌──────────────────┐
                         │ Pi0.5 Planner    │  HTTP @ localhost:7100
                         │ Service          │  (1–5 Hz on demand)
                         └──────────────────┘
                                ▲
                                │ phase + obs
                                ▼
                         ┌──────────────────┐
                         │ RL Policy Bank   │  HTTP @ localhost:7101
                         │ (per-skill)      │  (30–100 Hz steady state)
                         └──────────────────┘
```

**Why three processes:** Pi0.5 holds GPU memory for the VLA. RL policies hold separate model weights. The Continuity Manager stays light, deterministic, and crash-resistant — if a policy server dies, the Continuity Manager catches the timeout and triggers safe stop, not a process-wide failure.

## Data Flow per Task

1. Operator (or upstream task scheduler) issues a goal: `"pick up the red mug and place it on the tray"`. ROS has already positioned the platform.
2. Continuity Manager receives goal + current scene. Calls Pi0.5 Planner.
3. Pi0.5 returns a Task Spec: ordered phases, hand roles, semantic targets, success predicates.
4. Continuity Manager enters phase 0, looks up the skill (e.g., `pick_rigid_right`), routes observations to RL Policy Bank.
5. RL policy emits actions at 30–100 Hz. Action Arbitration merges single-arm output with the idle/assist arm's hold-pose controller and publishes joint commands.
6. Continuity Manager monitors success predicate. On success, advance phase. On failure pattern (timeout, force violation, predicate stall), escalate to Pi0.5 for replanning.
7. Final phase success → return result to caller.

## Single-arm vs Bimanual Mode

- **Single-arm phase:** one arm's `role = LEAD`, the other's `role = IDLE`. IDLE arm holds a safe retract pose via a fixed PD controller, NOT a learned policy.
- **Assist phase:** one arm `LEAD`, the other `ASSIST`. ASSIST runs a separate stabilization policy (e.g., `hold_cloth_taut`). Two policies, one phase, coordinated via the Task Spec.
- **Synchronized bimanual:** one phase, one bimanual policy that takes both arms' observations and emits both arms' actions. Used for handovers and tasks where decoupling fails. This is the most expensive to train; default to ASSIST decomposition first.

## Compute Budget (target)

| Component | Latency | Frequency | Notes |
|---|---|---|---|
| Pi0.5 inference | 200–800 ms | On-demand (per phase boundary, plus replanning) | Async; Continuity Manager doesn't block on it during steady-state execution. |
| RL policy inference | 5–15 ms | 30–100 Hz | Quantized ONNX or TensorRT. |
| Observation pipeline | 10–20 ms | 30 Hz | Bounded by camera rate. |
| Action arbitration | <2 ms | matches RL rate | |

## Failure Domains (early-warning list — see 09 for handling)

- Pi0.5 timeout or malformed output → fall back to last-known plan, escalate to operator if no plan.
- RL policy NaN or out-of-distribution observation → freeze, retract IDLE-style.
- Object lost (no detection across N frames) → escalate to Pi0.5 with "object missing" annotation.
- Force/torque violation → immediate soft stop, escalate.
- ROS `/base_stationary` drops mid-phase → soft stop, hold, wait for re-latch.
