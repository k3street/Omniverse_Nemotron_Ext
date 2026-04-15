# Phase 8C — Cortex Behaviors & Manipulation

**Status:** Not implemented  
**Depends on:** Phase 8B (move_to_pose for grasp approach), Phase 3 (import_robot)  
**Research:** `research_reports/8C_cortex_behaviors.md`, `rev2/verify_8C_cortex.md`

---

## Overview

Enable reactive, task-level robot control through Cortex decider networks and built-in manipulation abstractions.

**Correction from rev2:** Cortex IS compatible with Kit extensions (Tutorial 7 shows the pattern). Rev1 overstated the incompatibility. `CortexWorld` registers `step_async` as a Kit physics callback — Kit owns the step.

**Still true from rev1:** Behaviors are demos (not a factory API), gripper auto-detect doesn't exist, grasp editor is GUI-only, NL→BT is research.

---

## Tools

### 8C.1 `create_behavior(articulation_path, behavior_type, params)`

**Type:** CODE_GEN handler (generates Cortex decider network script)

**Implementation:** Generate a Python script that:
1. Creates `CortexWorld` and registers `step_async` as physics callback
2. Defines a `DfNetwork` with appropriate `DfDecider`/`DfAction` nodes
3. Hooks into the articulation

**Behavior types — what actually exists vs. what must be written:**

| Type | Built-in? | Notes |
|------|-----------|-------|
| pick_and_place | Demo exists (UR10, scene-specific) | Must generalize + re-parameterize |
| stacking | Demo exists (Franka, scene-specific) | Must generalize |
| peg_insertion | **Does not exist** | Must write from scratch |
| reactive_avoid | **Not a Cortex behavior** — handled by RMPflow (8B) | Remove from list |
| follow_target | **Does not exist** | Simple to write (~50 lines) |

**Realistic V1:** Support `pick_and_place` and `follow_target` only. Others are V2.

### 8C.2 `create_gripper(articulation_path, gripper_type, params)`

**Type:** CODE_GEN handler

**Corrections:**
- **No auto-detection** of gripper joints. User must provide `gripper_dof_names`, `open_position`, `closed_position`
- `ParallelGripper` — Python class, requires explicit config
- `SurfaceGripper` — OmniGraph node-based (`OgnSurfaceGripper`), different architecture. Generate OmniGraph code, not Python class instantiation
- **`magnetic` does not exist** — remove from gripper_type enum

**Gripper types:**
| Type | API | Architecture |
|------|-----|--------------|
| parallel_jaw | `isaacsim.robot.manipulators.ParallelGripper` | Python class |
| suction | `isaacsim.robot.surface_gripper.OgnSurfaceGripper` | OmniGraph node |

### 8C.3 `grasp_object(robot_path, target_prim, grasp_type)`

**Type:** CODE_GEN handler

**Correction:** "Auto-computed approach vectors" does NOT exist. Implementation options:

1. **Pre-authored grasps:** Load from `.isaac_grasp` YAML file via `import_grasps_from_file()`. Requires file to exist for the target object.
2. **Geometric heuristic:** Top-down grasp = approach from +Z, side grasp = approach from nearest clear direction. Simple but limited.
3. **ML-based (future):** FoundationPose/GraspNet/ContactGraspNet — separate ML pipeline, not built into Isaac Sim.

**V1:** Geometric heuristics + optional `.isaac_grasp` file loading. ML-based grasping is V2/V3.

**Execution sequence (uses Phase 8B):**
```
compute approach pose → move_to_pose (pre-grasp) → linear approach → close gripper → lift
```

### 8C.4 `define_grasp_pose(robot_path, object_path, gripper_offset, approach_dir)`

**Correction:** Grasp Editor is GUI-only — no API to drive it programmatically.

**Realistic implementation:** Generate an `.isaac_grasp` YAML template file that the user can:
- Edit manually with correct gripper-to-object transform
- Or refine using the Grasp Editor UI in Isaac Sim

### 8C.5 Behavior Tree Visualization (read-only)

**Rescoped from "NL modifies behavior tree" (research problem) to "read-only visualization."**

- Walk the `DfNetwork` DAG, extract node names and parent-child relationships
- Render as formatted text/tree in chat
- NL modification is a future research track, not a V1 feature

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Behavior script codegen | L0 | compile(), verify CortexWorld + physics_callback pattern |
| Gripper codegen (parallel) | L0 | Verify ParallelGripper with explicit joint names |
| Gripper codegen (suction) | L0 | Verify OmniGraph OgnSurfaceGripper pattern |
| Grasp YAML generation | L0 | Valid YAML, correct schema |
| BT visualization | L0 | Tree rendering from mock DAG |
| Full behavior execution | L3 | Requires Kit + Cortex |

## Known Limitations

- Behaviors are robot-specific — must be re-parameterized per robot
- No auto-detect for gripper joints
- No auto-computed grasps (geometric heuristics or pre-authored only)
- Behavior tree modification via NL is research, not product
- Cortex is on deprecated trajectory (still in 5.1/6.0, but no active development)
