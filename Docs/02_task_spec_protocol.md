# 02 — Task Spec Protocol

The schema bridging Pi0.5 → Continuity Manager → RL Policy Bank. **This is the most important file in the spec.** Every other module either produces or consumes this schema.

## Design Principles

1. **No joint-space anything.** Pi0.5 cannot reason about your joint limits or kinematics reliably. Everything is in the world frame (ROS `map` or `base_link`).
2. **Phases are atomic.** Each phase has exactly one LEAD skill. A phase cannot mid-execution change its skill — that requires advancing to the next phase or escalating.
3. **Success predicates are checkable from observations alone.** Never "the user thinks it looks good." Predicates are functions of object pose, gripper state, force, and time.
4. **Embodiment-agnostic semantics, embodiment-specific skills.** The Task Spec doesn't say "left arm" — it says `hand_role: LEAD` on the arm best positioned for the target.

## Schema (JSON)

```json
{
  "spec_version": "1.0",
  "task_id": "uuid",
  "goal_text": "pick up the red mug and place it on the tray",
  "embodiment_id": "tenthings_v1_open_arm_bimanual",
  "scene_snapshot": {
    "timestamp": 1730000000.0,
    "frame": "base_link",
    "objects": [
      {
        "object_id": "red_mug",
        "class": "mug",
        "pose": {"position": [0.45, -0.10, 0.82], "quaternion": [0,0,0,1]},
        "bbox_3d": [0.08, 0.08, 0.12],
        "confidence": 0.93
      },
      {
        "object_id": "tray",
        "class": "tray",
        "pose": {"position": [0.50, 0.25, 0.78], "quaternion": [0,0,0,1]},
        "bbox_3d": [0.30, 0.20, 0.02],
        "confidence": 0.97
      }
    ]
  },
  "phases": [
    {
      "phase_index": 0,
      "skill_name": "pick_rigid",
      "hand_assignment": {"right": "LEAD", "left": "IDLE"},
      "semantic_target": {
        "type": "object_grasp",
        "object_id": "red_mug",
        "approach_axis": [0, 0, -1],
        "approach_offset_m": 0.08
      },
      "constraints": {
        "max_force_n": 15.0,
        "max_duration_s": 8.0,
        "approach_speed_max_mps": 0.15
      },
      "success_predicate": {
        "type": "AND",
        "clauses": [
          {"type": "gripper_closed", "arm": "right", "min_width_m": 0.02, "max_width_m": 0.10},
          {"type": "object_attached", "object_id": "red_mug", "arm": "right"},
          {"type": "lift_clearance", "object_id": "red_mug", "min_clearance_m": 0.05}
        ]
      },
      "failure_predicate": {
        "type": "OR",
        "clauses": [
          {"type": "force_exceeded", "threshold_n": 15.0},
          {"type": "duration_exceeded", "threshold_s": 10.0},
          {"type": "object_lost", "object_id": "red_mug", "missing_frames": 15}
        ]
      }
    },
    {
      "phase_index": 1,
      "skill_name": "transit_to_pose",
      "hand_assignment": {"right": "LEAD", "left": "IDLE"},
      "semantic_target": {
        "type": "world_pose",
        "pose": {"position": [0.50, 0.25, 0.95], "quaternion": [0,0,0,1]}
      },
      "constraints": {"max_duration_s": 4.0, "carry_object_id": "red_mug"},
      "success_predicate": {
        "type": "pose_reached",
        "tolerance_m": 0.02,
        "tolerance_rad": 0.05
      }
    },
    {
      "phase_index": 2,
      "skill_name": "place_on_surface",
      "hand_assignment": {"right": "LEAD", "left": "IDLE"},
      "semantic_target": {
        "type": "place_on_object",
        "carry_object_id": "red_mug",
        "support_object_id": "tray",
        "place_offset": [0.0, 0.0, 0.0]
      },
      "constraints": {"max_force_n": 8.0, "max_duration_s": 6.0},
      "success_predicate": {
        "type": "AND",
        "clauses": [
          {"type": "object_resting_on", "object_id": "red_mug", "support_id": "tray"},
          {"type": "gripper_open", "arm": "right", "min_width_m": 0.10}
        ]
      }
    }
  ],
  "global_constraints": {
    "workspace_bounds": {"x": [0.20, 0.80], "y": [-0.50, 0.50], "z": [0.40, 1.20]},
    "max_total_duration_s": 30.0
  },
  "replanning_hints": {
    "if_object_lost": "rescan_workspace",
    "if_grasp_fails_3x": "try_alternative_grasp_axis"
  }
}
```

## Skill Names (canonical registry)

The Continuity Manager validates `skill_name` against this registry at Task Spec ingest. Unknown skills → escalate immediately.

### Single-arm skills
- `pick_rigid` — pick rigid object via top, side, or angled grasp.
- `pick_deformable` — pick cloth, soft objects.
- `place_on_surface` — place currently grasped object on a support.
- `transit_to_pose` — move gripper to a pose with carry semantics.
- `push_object` — push along a vector.
- `press_button` — press a target point with force feedback.
- `open_drawer` — pull along an axis with compliance.

### Bimanual / assist skills
- `stabilize_object` (ASSIST) — hold an object steady at a pose.
- `hold_cloth_taut` (ASSIST) — pin a fabric corner with controlled tension.
- `bimanual_handover` (LEAD+LEAD synchronized) — pass an object between hands.
- `bimanual_fold` (LEAD+ASSIST) — primary fold motion + corner stabilization.

Adding a new skill is a code change in `04_rl_policy_bank.md`, not a Pi0.5 prompt change in isolation.

## Predicate Reference

All predicates are evaluated by the Continuity Manager from the observation stream. Definitions:

| Predicate | Inputs | True when |
|---|---|---|
| `gripper_closed` | `arm`, `min_width_m`, `max_width_m` | Gripper width in range and closing velocity ≈ 0 |
| `gripper_open` | `arm`, `min_width_m` | Gripper width ≥ min and opening velocity ≈ 0 |
| `object_attached` | `object_id`, `arm` | Object pose tracks gripper pose within 2 cm for 5 frames |
| `object_resting_on` | `object_id`, `support_id` | Object pose stable (vel < 1 cm/s for 10 frames) AND z within bbox of support |
| `lift_clearance` | `object_id`, `min_clearance_m` | Object z exceeds initial z by ≥ min_clearance |
| `pose_reached` | `tolerance_m`, `tolerance_rad` | Lead-arm TCP within tolerance of semantic target |
| `force_exceeded` | `threshold_n` | F/T sensor magnitude > threshold |
| `duration_exceeded` | `threshold_s` | Phase wall-clock > threshold |
| `object_lost` | `object_id`, `missing_frames` | Object detector confidence < 0.3 for N consecutive frames |

## Versioning

`spec_version` is checked at ingest. Mismatch → reject. Bumping version requires updating: this file, the predicate evaluator, the Pi0.5 prompt template, and at least one regression task.
