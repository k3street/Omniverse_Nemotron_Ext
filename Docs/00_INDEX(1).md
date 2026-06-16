# Bimanual Manipulation Architecture: RL + Pi0.5 Hybrid

## Project Context

**Owner:** 10Things / Kimate Richards
**Target Embodiment:** Custom bimanual wheeled platform (open-arm reference: Enacting Open Arm), telescoping torso, ROS-controlled mobility and height.
**Scope:** Manipulation policy stack only. ROS handles navigation, base positioning, telescoping height. Once stationary at task pose, this stack takes over.

## Architectural Thesis

A two-tier manipulation policy:

1. **Pi0.5 (or VLA equivalent)** — task interpreter. Consumes goal language + RGB-D scene, produces a structured **task spec** (phase decomposition, hand role assignment, semantic waypoints, success predicates). Runs at 1–5 Hz.
2. **RL Policy Bank** — morphology-aware executor. Per-skill policies trained in sim, fine-tuned on real, conditioned on the task spec from Pi0.5. Runs at 30–100 Hz.

The two are bridged by a **Task Spec Protocol** and a **Continuity Manager** that holds ground-truth state (gripper poses, object poses, contact state, phase index) and decides when to advance, retry, or escalate back to Pi0.5 for replanning.

## Module Map

| File | Module | Role |
|------|--------|------|
| `00_INDEX.md` | This file | Navigation, context, glossary |
| `01_system_overview.md` | System Overview | Block diagram, data flow, runtime topology |
| `02_task_spec_protocol.md` | Task Spec Protocol | The schema bridging Pi0.5 → RL |
| `03_pi05_planner.md` | Pi0.5 Planner Service | VLA invocation, prompt structure, output parsing |
| `04_rl_policy_bank.md` | RL Policy Bank | Per-skill policy registry, gating, sim-to-real |
| `05_continuity_manager.md` | Continuity Manager | State tracking, phase advancement, failure escalation |
| `06_observation_pipeline.md` | Observation Pipeline | RGB-D, proprioception, force, object pose fusion |
| `07_action_arbitration.md` | Action Arbitration | Single-arm vs bimanual mode switching, joint command merge |
| `08_training_infrastructure.md` | Training Infrastructure | Isaac Lab environments, reward shaping, curriculum |
| `09_safety_and_recovery.md` | Safety & Recovery | Soft stops, collision shells, recovery primitives |
| `10_reference_code.md` | Reference Code | End-to-end Python skeleton, ROS2 nodes, Isaac Lab env |
| `11_isaac_lab_integration.md` | Isaac Lab Integration | Full env config, gym registration, ONNX export, eval harness (training path, no ROS2) |
| `12_isaac_sim_digital_twin.md` | Isaac Sim Digital Twin | Inference-in-sim via `isaacsim.ros2.bridge`, Action Graph, topic remap, sim/real switch (deployment path with ROS2) |

## Glossary

- **Task Spec** — structured JSON output of Pi0.5 describing phases, hand roles, semantic targets, success predicates.
- **Skill** — a single RL policy keyed by `(skill_name, embodiment_id)`. Examples: `pick_rigid_left`, `place_on_surface_right`, `stabilize_cloth_left`, `bimanual_handover`.
- **Phase** — a contiguous segment of execution governed by exactly one skill. Phases are the atomic unit advanced by the Continuity Manager.
- **Hand Role** — `LEAD`, `ASSIST`, or `IDLE` per arm per phase.
- **Embodiment ID** — string keying the morphology (`tenthings_v1_open_arm_bimanual`) so policies are not silently swapped across hardware revisions.
- **Approach Frame** — pre-grasp pose 5–10 cm offset from contact along the approach axis. RL policies seed from this frame.
- **Escalation** — Continuity Manager decision to bounce back to Pi0.5 for replanning rather than retry RL.

## Decision Boundaries (read this before changing anything)

- Pi0.5 NEVER emits joint-space trajectories. It emits semantic targets in the world frame.
- RL policies NEVER call Pi0.5 directly. They consume the current Task Spec phase from the Continuity Manager.
- Mobility and telescoping are out of scope. Inputs to this stack assume the platform is stationary and at correct height. ROS publishes a `/base_stationary` latch the Continuity Manager checks at phase entry.
- All policies are keyed by embodiment ID. Cross-embodiment policy reuse is a research question, not a deployment assumption.

## Read Order

For implementation: 01 → 02 → 06 → 04 → 03 → 05 → 07 → 08 → 11 → 12 → 09 → 10.
For review: 01 → 02 → 05 → 12 → 10.
For training-only work: 02 → 04 → 08 → 11.
For sim integration / deployment: 06 → 07 → 12.
