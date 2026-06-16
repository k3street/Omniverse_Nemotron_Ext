# 05 — Continuity Manager

The orchestrator. Holds the Task Spec, tracks phase state, evaluates predicates, decides advance/retry/escalate, and shepherds observations between the Observation Pipeline and the policy services.

**This is the only stateful component in the manipulation stack.** Pi0.5 is stateless across requests; RL policies are stateless across phases. State lives here.

## State Machine

```
                ┌─────────────┐
                │  IDLE       │◀──────────────┐
                └──────┬──────┘               │
                       │ goal received        │
                       ▼                      │
                ┌─────────────┐               │
                │  PLANNING   │               │
                └──────┬──────┘               │
                       │ task_spec valid      │
                       ▼                      │
              ┌────────────────┐              │
        ┌────▶│  EXECUTING     │              │
        │     └────┬───────────┘              │
        │          │                          │
        │   ┌──────┼──────┬─────────┐         │
        │   │      │      │         │         │
        │   ▼      ▼      ▼         ▼         │
        │ success failure timeout  fault      │
        │   │      │      │         │         │
        │   │      │      │         ▼         │
        │   │      │      │   ┌──────────┐    │
        │   │      │      └──▶│ SAFE_HOLD│    │
        │   │      │          └────┬─────┘    │
        │   │      ▼               │          │
        │   │ ┌──────────┐         │          │
        │   │ │ESCALATING│         │          │
        │   │ └────┬─────┘         │          │
        │   │      │ replan ok     │          │
        │   └──────┘               │          │
        │   advance phase          │          │
        │                          │          │
        │ all phases done          ▼          │
        │                   ┌──────────┐      │
        └──────────────────▶│ COMPLETE │──────┘
                            └──────────┘
```

States:

| State | Meaning |
|---|---|
| `IDLE` | No active task. Awaiting goal. |
| `PLANNING` | Pi0.5 in flight. |
| `EXECUTING` | RL policy active for current phase. |
| `ESCALATING` | Pi0.5 replanning from current state. RL frozen. |
| `SAFE_HOLD` | Both arms at retract pose. Operator-visible. |
| `COMPLETE` | Final phase predicate satisfied. |

## Per-Phase Loop

```python
def execute_phase(phase, scene_tracker, policy_bank, obs_pipeline):
    policy_bank.reset(skill=phase.skill_name, embodiment=EMBODIMENT_ID)
    phase_start = time.time()
    last_clamp_count = 0
    consecutive_clamps = 0

    while True:
        obs = obs_pipeline.latest()                      # blocks up to 1/control_rate
        scene_tracker.update(obs)                        # tracks object poses

        # Failure first — fail-fast beats run-on
        fail = evaluate_predicate(phase.failure_predicate, obs, scene_tracker, phase_start)
        if fail.matched:
            return PhaseResult.FAIL(reason=fail.clause, evidence=fail.evidence)

        # Success
        success = evaluate_predicate(phase.success_predicate, obs, scene_tracker, phase_start)
        if success.matched:
            return PhaseResult.SUCCESS()

        # Step the policy
        phase_context = build_phase_context(phase, scene_tracker, phase_start)
        act_resp = policy_bank.act(
            skill_name=phase.skill_name,
            embodiment_id=EMBODIMENT_ID,
            observation=obs.to_policy_input(),
            phase_context=phase_context,
        )

        # Detect persistent clamping → policy is misbehaving
        if act_resp.info.get("clamped"):
            consecutive_clamps += 1
            if consecutive_clamps >= 3:
                return PhaseResult.FAIL(reason="policy_persistent_clamp")
        else:
            consecutive_clamps = 0

        action_arbitration.publish(
            lead_arm=phase.hand_assignment.lead_arm(),
            action=act_resp.action,
            assist_action=maybe_assist_action(phase, obs),
        )
```

## Failure → Escalation Decision

Not every phase failure goes back to Pi0.5. Triage:

| Failure reason | Action |
|---|---|
| `force_exceeded` | Soft stop, retract, then escalate. |
| `duration_exceeded` | Escalate with "took too long, suggest alternative grasp." |
| `object_lost` | Re-run perception fresh. If still lost, escalate. |
| `pose_unreachable` | Escalate immediately — likely a planning error, not an execution error. |
| `policy_persistent_clamp` | Fail to operator. Don't replan — policy itself is wrong. |
| `predicate_stall` (no progress for N seconds) | Retry phase once. Then escalate. |

Replan budget per task: **2 escalations max**. Beyond that, fail to operator.

## Scene Tracker

A small in-memory store keyed by `object_id`:

```python
@dataclass
class TrackedObject:
    object_id: str
    cls: str
    pose: Pose
    pose_history: deque[Pose]   # last 30
    last_seen: float
    confidence: float
    attached_to: Optional[str]  # arm name if grasped
```

Updated each control tick from the Observation Pipeline. Predicates query this rather than re-running perception.

When the Continuity Manager detects `object_attached`, it sets `attached_to = arm` and from then on, `object.pose` is computed from gripper TCP + grasp offset until `gripper_open` flips it back.

## Action Arbitration Coupling

The Continuity Manager doesn't publish joint commands — it asks the Action Arbitration node (07) to. For phases with `hand_assignment.left = IDLE`, Continuity Manager sends only the LEAD action; arbitration applies the IDLE hold-pose for the other arm. For ASSIST phases, two `/act` calls are issued in parallel (LEAD policy + ASSIST policy), arbitration merges.

## Telemetry

Every phase logs:
- Phase start/end timestamps
- Outcome (SUCCESS / FAIL / ESCALATE)
- Predicate evaluation trace (which clauses matched and when)
- Action bounds clamp count
- F/T peak, max gripper force
- Object pose deltas

Stored as JSONL per task. Becomes training signal for predicate tuning, replay for debugging, and dataset for offline RL fine-tuning.

## Why This Component is Conservative

Pi0.5 is creative and occasionally hallucinates. RL policies are aggressive and occasionally OOD. The Continuity Manager is the boring adult — it checks predicates strictly, advances only on observable success, and escalates on observable failure. Don't put learning here. If you find yourself wanting to learn here, you want a new RL skill in the Policy Bank instead.
