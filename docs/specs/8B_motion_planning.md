# Phase 8B — Motion Planning: RMPflow, Lula & cuMotion

**Status:** Partially implemented (code generators exist but RMPflow code is broken)  
**Depends on:** Phase 3 (import_robot), Phase 8A (debug_draw for trajectory viz)  
**Blocks:** Phase 8C (grasp_object uses move_to_pose)  
**Research:** `research_reports/8B_motion_planning.md`, `rev2/verify_8B_rmpflow.md`

---

## Critical: Fix RMPflow Code

**Confirmed broken by both rev1 and rev2.** The generated code calls `get_next_articulation_action()` once — but RMPflow is a step-wise reactive policy that must run every physics step until convergence.

**Correct pattern:**
```python
from isaacsim.robot_motion.motion_generation import RmpFlow, ArticulationMotionPolicy
from isaacsim.robot_motion.motion_generation import interface_config_loader

# Load config (use correct function name)
rmpflow_config = interface_config_loader.load_supported_motion_policy_config(
    robot_type, "RMPflow"
)
rmpflow = RmpFlow(**rmpflow_config)
rmpflow.set_end_effector_target(target_position, target_orientation)

# MUST run per physics step:
def on_physics_step(dt):
    joint_positions = art.get_joint_positions()
    joint_velocities = art.get_joint_velocities()
    rmpflow.update_world()  # REQUIRED for dynamic obstacles
    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
    art.apply_action(action)

    # Convergence check
    ee_pos = art.get_world_pose()[0]
    if np.linalg.norm(ee_pos - target_position) < position_threshold:
        world.remove_physics_callback("rmpflow_step")

world.add_physics_callback("rmpflow_step", on_physics_step)
```

**Additional fixes needed:**
- Function name: `load_supported_motion_policy_config()` (NOT `load_supported_motion_gen_config`)
- Must call `rmpflow.update_world()` every step for obstacle avoidance
- Must call `rmpflow.set_robot_base_pose()` if robot is not at origin
- Add convergence threshold + max_steps timeout as parameters

---

## Planners

### RMPflow (reactive, real-time)
- Step-wise policy, called every physics frame
- Good for: smooth, reactive motion with obstacle avoidance
- Pre-configured for 19 robots (check via `get_supported_robot_policy_pairs()`)

### Lula RRT (global, static environments)
- One-shot path planning in C-space
- **Does NOT support orientation targets** — only position
- For static environments only — no dynamic obstacle avoidance
- Class: `LulaRRTMotionPolicy` (NOT `LulaTaskSpaceTrajectoryGenerator`)

### cuMotion/cuRobo (GPU-accelerated — RECOMMENDED for new development)
- **Module:** `isaacsim.robot_motion.curobo`
- GPU-accelerated trajectory optimization
- Dynamic obstacle avoidance via nvblox
- Accepts XRDF files directly
- NVIDIA explicitly recommends this over RMPflow for new development

**Remove `lula_cspace` from planner options** — it's a trajectory post-processing step, not a peer planner.

---

## Tools

### 8B.1 `move_to_pose(articulation_path, target_position, target_orientation, planner)`

**Planners:** `rmpflow` (default), `lula_rrt`, `curobo`

**Parameters:**
- `position_threshold` (float, default 0.01): Convergence distance in meters
- `max_steps` (int, default 1000): Timeout
- `orientation` required for rmpflow/curobo, **ignored by lula_rrt** (log warning)

**Pre-flight:** Check `get_supported_robot_policy_pairs()` for known robots. Unknown robot → graceful error with instructions to run Robot Description Editor.

**Pre-flight:** Check sim is playing (`timeline.is_playing()`). Silent failure if paused.

### 8B.2 `plan_trajectory(articulation_path, waypoints, planner)`

Returns waypoints without executing. Preview via `debug_draw` (8A.2) before execution. Separate `execute_trajectory` step.

### 8B.3 `set_motion_policy(articulation_path, policy_type, params)`

**Realistic scope for chat tool:**
- Add/remove obstacle primitives: `rmpflow.add_cuboid(name, dims, pose)`, `rmpflow.add_sphere(...)`
- Prim → obstacle helper: read `UsdGeomBBoxCache` → register as cuboid
- Joint limit padding: `rmpflow.set_param()` with `joint_limit_buffers`

**NOT exposable via chat:** Individual collision sphere tuning, self-collision masks, per-link radius adjustments.

### 8B.4 `generate_robot_description(articulation_path, output_path)`

**`isaacsim.robot_setup.xrdf_editor` has NO headless API** (confirmed).

**Realistic implementation:**
1. Check `get_supported_robot_policy_pairs()` — if robot is pre-supported, return existing config paths
2. If not → return step-by-step guide: "Run the Robot Description Editor in Isaac Sim UI"
3. Do NOT claim auto-generation for custom robots

### 8B.5 IK Solver

**API:** `LulaKinematicsSolver` + `ArticulationKinematicsSolver`

```python
action, success = art_kinematics.compute_inverse_kinematics(
    target_position=pos, target_orientation=quat
)
# MUST check success flag — convergence not guaranteed near singularities
```

**Caveats to document:** No warm-start control, no redundancy resolution for 7-DOF, no singularity detection.

---

## XRDF vs URDF (clarification)

Both needed simultaneously:
- **URDF:** kinematics (links, joints, collision geometry)
- **XRDF:** motion planning supplement (actuated joints, collision spheres, joint limits, tool frame)
- XRDF does NOT replace URDF

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| RMPflow codegen | L0 | Verify `add_physics_callback`, `update_world`, convergence check |
| Lula RRT codegen | L0 | Verify no orientation target, correct class name |
| cuMotion codegen | L0 | Verify `isaacsim.robot_motion.curobo` namespace |
| Robot support check | L0 | Known vs unknown robot branching |
| IK success flag | L0 | Generated code checks `success` |
| Trajectory execution | L3 | Requires Kit + physics sim |

## Known Limitations

- 19 pre-supported robots only for auto-config (custom needs manual Editor)
- RMPflow is reactive/best-effort, not guaranteed collision-free
- Lula RRT: no orientation targets, static environments only
- No singularity detection or reporting API
- Physics sim must be running (silent failure if paused)
