# Controller matrix — operator cheat sheet

Companion to `controller_matrix_plan.md` (the implementation plan).
This file is the **operator reference**: what tool-family to reach for
given a scenario, what `target_source` to set, and how the families
relate.

See also:
- `controller_matrix_plan.md` — phased implementation plan
- `conveyor_pick_place_incidents.md` — debugging history (I-01..I-25)
- `ctrl_attrs_schema.md` *(TBD in FAS 1)* — `ctrl:*` USD-attr contract

## Big picture: three families, not one

Picking what orchestrates a robot's motion is a **family** choice, not
a single switch:

1. **`setup_pick_place_controller`** — built-in pick-place orchestration
   with multiple `target_source` implementations. Installs a
   physics-callback, keeps scene-graph state, emits `ctrl:*` USD attrs.
   Use when the task IS pick-and-place (conveyor → bin, bin → table,
   fixed sequence, etc.).
2. **ROS2 family** — `ros2_connect`, `setup_ros2_bridge`,
   `configure_ros2_bridge`, `setup_pick_place_ros2_bridge`. Use when
   external ROS2 planners / MoveIt / PLC drive the robot and Isaac Sim
   is a digital twin.
3. **Learned-policy family** — `load_groot_policy`, `evaluate_groot`,
   `finetune_groot`. Use when a neural-net policy emits joint targets
   directly (no hand-written orchestration).
4. **Teleop family** — `start_teleop_session`,
   `configure_teleop_mapping`, `record_teleop_demo`. Use when a human
   drives the robot via gamepad/VR for demo collection or manual test.

The controllers below are all **within** family (1):
`setup_pick_place_controller`'s `target_source` param. Family (2) is
reachable via `target_source="ros2_cmd"` inside pick-place, or
standalone if you don't want the pick-place state-machine.

## `target_source` matrix (existing + planned)

| target_source | Status | Hardware | Cycle | Collision-aware | Motion | Use for |
|---|---|---|---|---|---|---|
| `native` | ✅ | CPU (Franka only) | 8-15s | Partial (RmpFlow obstacles) | Reactive | **Default** when robot is Franka; dynamic targets, moving cubes |
| `sensor_gated` | ✅ | CPU | 10-15s | No | Reactive + teach-replay | Non-Franka arms; PLC-mimic sim2real; pre-taught PICK/DROP poses |
| `fixed_poses` | ✅ | CPU | varies | No | Pose-replay | Deterministic cycle-time demos, validation runs |
| `cube_tracking` | ✅ | CPU | 8-12s | No | Reactive + omniscient | ML research demo-gen only (NOT sim2real — cheats using ground-truth cube pose) |
| `ros2_cmd` | ✅ | External | varies | Depends | External | Digital twin, PLC-in-loop, external MoveIt |
| `spline` | ✅ FAS 2 (3/4) | CPU | 10-12s | Pre-check only | Deterministic smooth (IK-chained) | Repetitive identical cycles; sim2real; CPU-only benchmarks |
| `curobo` | 🔜 FAS 6 | NVIDIA GPU ≥Volta, 4GB VRAM | 3-5s | **Yes** (Cuboid/SDF/mesh) | Globally optimized | Obstacle-rich scenes; production quality; tight cycle-time |
| `diffik` | 🔜 FAS 7a | CPU, Isaac Lab | 12-18s | No | Stateless Jacobian | Teleop; Cartesian RL observation; simple free-motion |
| `osc` | 🔜 FAS 7b | CPU, Isaac Lab | 20-30s | No | Task-space impedance (torque) | Contact-rich tasks (polishing, assembly). **Experimental.** |
| `auto` | 🔜 FAS 5 | any | varies | varies | Best-available | Default when hardware unknown; agent selects dynamically |

### Decision flowchart

```
User prompt mentions...
├── "ROS2" / "MoveIt" / "PLC" / "external planner"
│     → setup_ros2_bridge + setup_pick_place_controller(target_source="ros2_cmd")
├── "GR00T" / "foundation model" / "policy" / "learned"
│     → load_groot_policy + evaluate_groot (no pick-place controller)
├── "teleop" / "human driving" / "demo collection"
│     → start_teleop_session (no pick-place controller)
├── "RTX" / "CUDA" / "GPU" / "industrial quality" / "obstacles"
│     → setup_pick_place_controller(target_source="curobo")
├── "no GPU" / "CPU-only" / "laptop" / "deterministic"
│     → setup_pick_place_controller(target_source="spline")
├── "Franka" + "pick and place" + no special hardware
│     → setup_pick_place_controller(target_source="native")
├── "conveyor" + "sensor" + "non-Franka"
│     → setup_pick_place_controller(target_source="sensor_gated")
└── Anything else / uncertain
      → setup_pick_place_controller(target_source="auto")
```

Agent can (after FAS 4) call `list_available_controllers` first to
probe the live Kit env and rule out unavailable options before picking.

## Controller cross-reference

### `native` — canonical PickPlaceController (Franka)
- Wraps `isaacsim.robot.manipulators.examples.franka.controllers.PickPlaceController`
- Uses built-in RmpFlowController + Lula IK
- **Requires** Franka (hardcoded EE link, finger joints)
- **10 embedding fixes** documented in `project_isaac_assist_native_pickplace.md`
- Scene Reset Manager hook: `native_pp`
- Baseline run `scripts/qa/run_conveyor_pick_place.py`: **3/4 cubes** (one misses bin on avg)

### `sensor_gated` — RmpFlow + teach-replay
- Generic (not Franka-specific) but needs RmpFlow YAML
- State machine: wait_sensor → pick → lift → drop → home
- Pre-taught PICK/DROP/HOME poses via `teach_robot_pose`
- Belt pause/resume logic built in
- Scene Reset Manager hook: `sensor_gated`

### `fixed_poses` — pose-list replay
- Feed a list of joint configs, controller cycles through them
- No sensor, no IK, no error recovery
- Use to measure "does the robot even reach these poses" cycle-time

### `cube_tracking` — omniscient reactive (NOT sim2real)
- Polls cube world-pose each frame, retargets RmpFlow
- Cheats: uses ground-truth pose, not a sensor
- Fast, but unrealistic; use only for ML demo-gen

### `ros2_cmd` — external orchestration
- Subscribes to `/isaac/robot/target_pose` + gripper topic
- All decisions external (MoveIt, PLC, custom ROS2 node)
- Pair with `setup_ros2_bridge` / `configure_ros2_bridge`

## Related tool families (not duplicated in target_source)

### ROS2 family
- `ros2_connect` — establish ROS2 connection
- `setup_ros2_bridge` — omni.isaac.ros2_bridge OmniGraph
- `configure_ros2_bridge` — QoS, topic mapping
- `setup_pick_place_ros2_bridge` — pick-place-specific topic pairs
- `configure_ros2_time` — sim vs wall-clock time source
- `ros2_publish*`, `ros2_list_topics`, `ros2_get_*` — runtime RPC

### GR00T family (learned policy)
- `load_groot_policy` — download + configure GR00T-N1.6-3B (>=24GB VRAM)
- `evaluate_groot` — closed-loop eval on IsaacLab task
- `finetune_groot` — LoRA / full fine-tune on LeRobot v2 demo data
- `compare_policies` — side-by-side eval results

### Teleop family
- `start_teleop_session` — gamepad/VR/SpaceMouse input
- `configure_teleop_mapping` — axis mapping
- `record_teleop_demo` — capture joint trajectory
- `validate_teleop_demo` — sanity-check demo for training
- `export_teleop_mapping` — save mapping
- `stop_teleop_session`, `summarize_teleop_session`

### Motion primitives (building blocks)
- `move_to_pose` — single Cartesian move (no orchestration)
- `plan_trajectory` — one trajectory, no state machine
- `solve_ik` — single IK call
- `check_singularity` — preflight check
- `grasp_object` — gripper-only action

## Baseline snapshot (FAS 0 + FAS 1 regress)

**Date:** 2026-04-21
**Script:** `scripts/qa/run_conveyor_pick_place.py --wait 120`
**Controller:** `native`
**Result (2 back-to-back runs, deterministic):**
- Controller internal counter `cubes_delivered`: 4 (state machine completed 4 cycles)
- Physical: **1 / 4 cube in bin** (Cube_4 only)
- Cubes 1-3 ended up at x≈0.87 on +X edge of belt (slipped past gripper, fell off belt)
- `error_count`: 0
- `tick_count`: ~7195 (identical across runs — physics deterministic)

**Known limitations:**
- Gripper-close timing vs cube arrival at sensor — first 3 cubes slip through
  (friction grip fails, gripper closes too late / doesn't clamp)
- Only the last cube (Cube_4, slowest on belt due to starting x=0.0) gets picked successfully
- Motion quality "janky" (RmpFlow + per-tick IK-guide branch-hopping)
- Stop+Play recovery fragile

**This 1/4 physical delivery is the floor any new controller must beat.**
FAS 9 benchmark will record 5 runs × 5 controllers and pick a winner.
Spline (pre-planned waypoints + warm-start IK chaining) and cuRobo (global
optimization + collision-aware) should both substantially improve this.

## Current leaderboard (post stub-upgrade — all real implementations)

| Controller | Physical cubes in bin | Internal cubes_delivered | Motion | Notes |
|---|---|---|---|---|
| `native` | **3/4** (det., 2 runs, fixed 2026-04-21) | 4/4 | IK+RmpFlow hybrid | Regression I-36 fixed: 4-part fix (Scene Reset cleanup + right_gripper frame + belt-freeze + finger gains) |
| `spline` | **3/4** (det., 2 runs) | 4/4 | Smooth (CubicSpline + IK chain) | **Winner.** Used in CP-01 template. 4th cube usually misses bin by ~20cm |
| `diffik` | **0/4** (det., 1 run) | 4/4 | Per-tick Jacobian | Isaac Lab bridged. State machine runs cleanly, but grip timing mismatches diffik's PD-converging behavior — see I-32 |
| `osc` | **0/4** (det., 1 run) | 0/4 | Torque-mode Cartesian impedance | Isaac Lab bridged; arm switches to effort mode. Simplified OSC (no inertial decoupling, no gravity comp). Expected 0-2/4 for standard pick-place — I-33 |
| `curobo` | **0/4** (planning + execution OK, grip-mystery I-39) | 4/4 cykler | GPU MotionPlanner, 5-segment plans, hög lift (40cm) | I-34 unblocked 2026-04-21 (cuda-core + content sync). I-37 quat-fix gjord men I-38 visade Warp 1.8.2 ↛ 1.9 API-break förhindrar collision-aware planning helt. Workaround: hög lift istället för obstacles. Hand når drop-target inom 3mm men kub greppas aldrig (I-39, ej rotorsaken klarlagd). Spline med samma params får 3/4 — något subtle skiljer. Future: hybrid (cuRobo för transit, Lula-IK för pick) eller Warp-uppgradering. |

**Benchmark:** `/tmp/bench_fas9.json` (native/spline, 2 runs). Spline
winner. diffik/osc added 2026-04-21 (stub upgrade) — full benchmark
across 5 controllers pending.

## Verified template

`workspace/templates/CP-01.json` — captures the winning build
sequence + `target_source='auto'` choice. ChromaDB retrieval picks
it up for queries like "set up a pick-place cell on a table" or
"franka picks cubes from a belt".

## Observability contract (preview — full spec in FAS 1)

Every controller MUST emit these USD attrs on the robot prim for
downstream tools (diagnose_scene, auto_judge, benchmark_controllers):

- `ctrl:mode` — `native | sensor_gated | spline | curobo | diffik | osc | ...`
- `ctrl:phase` — current state-machine phase
- `ctrl:cubes_delivered` — int counter
- `ctrl:error_count` — int counter
- `ctrl:tick_count` — physics ticks since install
- `ctrl:picked_path` — current target cube USD path
- `ctrl:last_error` — most recent error string (if any)

See `ctrl_attrs_schema.md` (FAS 1) for the authoritative spec.

## Scene Reset Manager (shared across controllers)

Stop+Play lifecycle is handled by a **robot-agnostic singleton**
registered in `builtins._scene_reset_manager`. Each controller
registers a reset hook by name; manager calls hooks on Play event
until each returns `True` (success) or timeout.

See `tool_executor.py` `_gen_pick_place_native` for the current
inline snippet; FAS 1 will extract to a shared module-level string.

## Revision log

- 2026-04-21 — initial file (FAS 0). Documents existing controllers +
  ROS2/GR00T/teleop families, snapshots 3/4 baseline.
