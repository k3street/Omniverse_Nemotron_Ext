# `ctrl:*` USD attr schema

**Purpose**: A uniform observability contract every pick-place
controller emits on the robot prim, so downstream consumers
(`diagnose_scene`, `auto_judge`, `benchmark_controllers`, agent tool
calls) can probe scene state without knowing which controller is
installed.

**Scope**: `setup_pick_place_controller` family. Other tool families
(ROS2 bridge, GR00T policy, teleop) have their own telemetry.

## Attrs

All attrs are written to the robot articulation root (same prim
passed as `robot_path` to `setup_pick_place_controller`).

| Attr | Type | Default | Semantics |
|---|---|---|---|
| `ctrl:mode` | String | `""` | Controller identity. One of `native \| sensor_gated \| fixed_poses \| cube_tracking \| ros2_cmd \| spline \| curobo \| diffik \| osc`. Set at install time, never changes. |
| `ctrl:phase` | String | `"wait_sensor"` | Current state-machine phase. Values are controller-specific (see per-controller table below). |
| `ctrl:cubes_delivered` | Int | `0` | Monotonic counter of cubes successfully delivered to `destination_path` since install. Reset to 0 on Stop+Play. |
| `ctrl:error_count` | Int | `0` | Monotonic counter of exceptions caught by the tick-callback. Reset to 0 on Stop+Play. |
| `ctrl:last_error` | String | `""` | Most recent error (`"TypeError: ..."`). Cleared on Stop+Play reset. |
| `ctrl:picked_path` | String | `""` | USD path of the cube currently being picked, or `""` if waiting. |
| `ctrl:tick_count` | Int | `0` | Physics ticks since install. Reset to 0 on Stop+Play. |

### Optional (controller-specific)

Controllers MAY emit additional attrs under the `ctrl:` namespace.
These SHOULD NOT break generic consumers (i.e. consumers must tolerate
unknown `ctrl:*` attrs). Suggested namespacing:

| Attr | Type | Written by | Semantics |
|---|---|---|---|
| `ctrl:plan_segment` | Int | curobo, spline | Which pre-planned segment (0..N-1) is currently executing |
| `ctrl:plan_waypoint` | Int | curobo, spline | Which waypoint within the current segment |
| `ctrl:ik_success_rate` | Float | diffik | Fraction of IK calls that converged in the last 100 ticks |
| `ctrl:torque_saturation` | Float | osc | Max abs(torque)/limit across arm joints (0..1) |

## `ctrl:phase` values per controller

| Controller | Valid phases |
|---|---|
| `native` | `wait_sensor`, `picking`, `idle` (controller-internal phases 0-9 are tracked via `controller._event`, not exposed) |
| `sensor_gated` | `home`, `wait_sensor`, `moving_to_pick`, `gripping`, `moving_to_drop`, `returning_home` |
| `fixed_poses` | `pose_N` (0-indexed), `cycle_done` |
| `cube_tracking` | `tracking`, `gripping`, `moving_to_drop`, `returning_home` |
| `ros2_cmd` | `idle`, `command_received`, `moving`, `gripper_open`, `gripper_close` |
| `spline` *(FAS 2)* | `wait_sensor`, `planning`, `executing`, `gripping`, `transit`, `releasing`, `returning` |
| `curobo` *(FAS 6)* | `wait_sensor`, `warmup`, `planning`, `executing_segment_N`, `gripping`, `released`, `home` |
| `diffik` *(FAS 7a)* | `wait_sensor`, `cartesian_move`, `gripping`, `transit`, `releasing`, `home` |
| `osc` *(FAS 7b)* | `wait_sensor`, `compliant_move`, `contact`, `gripping`, `lift`, `transit`, `release`, `home` |

## Reset semantics (Stop+Play)

When the user (or programmatic) issues Play after Stop, the Scene
Reset Manager (see below) calls each registered reset hook. On
successful reset, controllers SHOULD:

- `ctrl:cubes_delivered` → 0
- `ctrl:error_count` → 0
- `ctrl:tick_count` → 0
- `ctrl:last_error` → `""`
- `ctrl:picked_path` → `""`
- `ctrl:phase` → initial phase for that controller
- `ctrl:mode` **unchanged** (controller identity persists)

## Scene Reset Manager contract

**Installed at**: `builtins._scene_reset_manager` (idempotent singleton)

**Lifecycle**:
1. First controller that calls the install snippet creates the manager
2. Subsequent controllers use the existing instance
3. Each controller registers a reset hook: `mgr.register(name, fn)`
4. On Stop timeline event, manager sets `stopped=True`
5. On Play timeline event (if previously stopped), manager queues all
   registered names into `pending`
6. Each physics tick, manager calls each pending hook. If hook returns
   `True`, it's removed from pending; if `False`, retried next tick
7. Controllers call `mgr.unregister(name)` in their teardown

**Hook signature**:
```python
def reset_hook() -> bool:
    """Return True when reset complete, False to retry next tick.

    Manager retries until hook returns True or controller
    unregisters. No timeout — hooks must eventually return True or
    clean themselves up.
    """
```

**Why retry**: On Play, `SimulationManager.get_physics_sim_view()`
returns `None` for several ticks until the physics view is rebuilt.
Reset hooks probe this and retry until the view is valid.

**Why singleton**: Scenes can have multiple pick-place controllers
(e.g. 2 Frankas cooperating). They must all reset consistently on the
same Stop+Play event. A singleton ensures there's exactly one timeline
subscription, one tick callback, and consistent ordering.

## Consumer examples

### `diagnose_scene` (robot-agnostic probe)
```python
# Get current controller mode + progress without knowing which
# target_source is installed
robot = stage.GetPrimAtPath("/World/Franka")
mode = robot.GetAttribute("ctrl:mode").Get() or "unknown"
phase = robot.GetAttribute("ctrl:phase").Get() or "unknown"
cubes = robot.GetAttribute("ctrl:cubes_delivered").Get() or 0
errors = robot.GetAttribute("ctrl:error_count").Get() or 0
print(f"{mode} in phase {phase}: {cubes} cubes, {errors} errors")
```

### `benchmark_controllers` (per-run aggregate)
```python
# After 60s wait
final = {
    "mode": robot.GetAttribute("ctrl:mode").Get(),
    "cubes": robot.GetAttribute("ctrl:cubes_delivered").Get(),
    "errors": robot.GetAttribute("ctrl:error_count").Get(),
    "ticks": robot.GetAttribute("ctrl:tick_count").Get(),
}
```

### `auto_judge` (rubric application)
Use `ctrl:mode` + `ctrl:cubes_delivered` to classify run success
without parsing the controller install output.

## Revisions

- 2026-04-21 — v1. Initial contract. `native` is the only controller
  fully compliant; other existing controllers (sensor_gated,
  fixed_poses, cube_tracking, ros2_cmd) need to be audited for
  `ctrl:*` emission (planned FAS 1 follow-up or per-controller in
  FAS 2-7).
