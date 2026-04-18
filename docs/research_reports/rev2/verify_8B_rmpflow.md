# Verification Report: RMPflow "Architecturally Broken" Claim
**File under review:** `service/isaac_assist_service/chat/tools/tool_executor.py`
**Function reviewed:** `_gen_move_to_pose` (line 1544), RMPflow branch (line 1585–1627)
**Date:** 2026-04-15
**Reviewer:** Claude (fact-checker, not original reviewer)

---

## The Reviewer's Claim

> "The RMPflow code is architecturally broken because it calls `get_next_articulation_action()` only once instead of per-step."

---

## The Generated Code (verbatim, from tool_executor.py lines 1586–1626)

```python
import omni.usd
import numpy as np
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Load RMPflow config for the robot
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

# Get the articulation
art = SingleArticulation(prim_path='/World/Franka')
world = World.instance()
if world is None:
    from isaacsim.core.api import World
    world = World()
art.initialize()

# Set target
target_pos = np.array([0.4, 0.0, 0.3])
target_ori = None
rmpflow.set_end_effector_target(target_pos, target_ori)

# Get current joint state and compute action
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(
    joint_positions, joint_velocities
)

# Apply joint targets
art.apply_action(action)
print(f'RMPflow: moving panda_hand to {target_pos} — action applied')
```

---

## Fact-Finding

### 1. What does this code actually do?

It is a flat, synchronous script — not a loop. It:
1. Initializes `RmpFlow` and `SingleArticulation`
2. Sets a target once
3. Calls `get_next_articulation_action()` exactly once with the current joint state
4. Applies the resulting action to the articulation
5. Prints a success message and exits

There is no loop, no physics callback, no `world.step()`, and no convergence check.

### 2. How is this code executed — via exec() inside Kit?

Yes. The code is sent as a string to Kit's RPC endpoint (`/exec_patch` or `/exec_sync`). On the Kit side, `kit_rpc.py` executes it via:

```python
exec(code, exec_globals)
```

This runs in `_kit_exec_tick`, which is registered to Kit's **update event stream** (`omni.kit.app.get_app().get_update_event_stream()`). The tick fires once per Kit frame — it drains the execution queue and runs each queued code block exactly once.

**Critically:** the code block itself is run once and discarded. There is no mechanism in `kit_rpc.py` or `_kit_exec_tick` to re-run the generated code each physics step. The update subscription executes queued items, not repeated callbacks.

### 3. Does RMPflow require per-step calling?

**Yes — unambiguously.** The official Isaac Sim tutorial (`manipulators/manipulators_rmpflow.html`, all versions 4.2–6.0) shows the canonical pattern as an `update(step: float)` method called every simulation frame:

```python
def update(self, step: float):
    target_position, target_orientation = self._target.get_world_pose()
    self._rmpflow.set_end_effector_target(target_position, target_orientation)
    self._rmpflow.update_world()
    robot_base_translation, robot_base_orientation = self._articulation.get_world_pose()
    self._rmpflow.set_robot_base_pose(robot_base_translation, robot_base_orientation)
    action = self._articulation_rmpflow.get_next_articulation_action(step)
    self._articulation.apply_action(action)
```

This is called once per physics frame for the duration of motion. RMPflow is a **reactive, real-time controller**:

- It reads fresh joint state each frame
- It computes a small velocity/torque step toward the target (not a full trajectory)
- It internally sub-steps at up to 300 Hz when Isaac Sim runs at 60 Hz
- It needs `update_world()` called per-step for obstacle avoidance
- A single call produces at most one tiny step toward the goal — the robot will not move meaningfully

The standard pattern (non-tutorial) also wraps this in a `while world.is_playing()` loop with `world.step()`.

### 4. Does ArticulationMotionPolicy handle stepping internally?

No. `ArticulationMotionPolicy` is a **thin adapter** that maps joint names between `RmpFlow` and the `Articulation` object. Its `get_next_articulation_action(step)` method:
- Reads joint state from the articulation
- Calls `rmpflow.compute_joint_targets()`
- Returns an `ArticulationAction`

It performs no internal looping or stepping. The caller is responsible for calling it every physics frame.

The generated code does not use `ArticulationMotionPolicy` at all — it calls `rmpflow.get_next_articulation_action(joint_positions, joint_velocities)` directly. This API variant (passing state explicitly rather than reading from the articulation) is lower-level and still requires per-frame calling.

### 5. Is there a "fire and forget" interpretation that saves the code?

**No.** For this pattern to work, the generated code would need to register its own physics callback before exiting:

```python
def _rmp_step(step_size):
    joint_positions = art.get_joint_positions()
    joint_velocities = art.get_joint_velocities()
    rmpflow.update_world()
    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
    art.apply_action(action)

world.add_physics_callback("rmp_control", _rmp_step)
```

The generated code does not do this. It applies one action and exits. There is nothing in `kit_rpc.py`'s execution mechanism that repeats the code block on subsequent frames.

---

## Verdict

**The reviewer's claim is CORRECT.**

The generated RMPflow code is architecturally broken. One call to `get_next_articulation_action()` produces a single infinitesimal torque/velocity step toward the target. With no per-step loop, the robot moves a fraction of a millimeter and stops. This is not a "fire and forget" that queues a physics callback — the code block runs once and exits.

---

## Additional Bugs Confirmed (originally flagged in 8B_motion_planning.md)

| Bug | Status |
|-----|--------|
| `get_next_articulation_action()` called once instead of per-step | **CONFIRMED critical** |
| `load_supported_motion_gen_config` — wrong function name (should be `load_supported_motion_policy_config`) | **CONFIRMED** — verified against Isaac Sim 5.x/6.x module docs |
| `rmpflow.update_world()` missing | **CONFIRMED** — required per-step for obstacle avoidance |
| No physics callback registered | **CONFIRMED** — the generated code has no `add_physics_callback` call |
| No convergence check / timeout | **CONFIRMED** |
| No `set_robot_base_pose()` call | **CONFIRMED** — required if robot base can move (mobile manipulators) |

---

## What Correct Code Must Do

The generated code for `move_to_pose` with RMPflow must:

1. Initialize `RmpFlow` and `SingleArticulation` (same as current)
2. Call `set_end_effector_target()` (same as current)
3. **Register a physics callback** via `world.add_physics_callback()` containing:
   - `rmpflow.update_world()` — refresh obstacle state
   - `rmpflow.set_robot_base_pose()` — update base transform
   - `rmpflow.get_next_articulation_action(step)` — compute this frame's action
   - `art.apply_action(action)` — apply it
   - Convergence check to remove the callback when target is reached (or timeout)
4. Ensure the simulation is playing (`world.play()` or verify it is already running)

Without step 3, RMPflow is initialized, armed with a target, fires once, and goes silent. The arm does not move to the target.

---

## Sources

- [Lula RMPflow — Isaac Sim 6.0 Documentation](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/manipulators/manipulators_rmpflow.html)
- [Motion Policy Algorithm — Isaac Sim 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/manipulators/concepts/motion_policy.html)
- [RMPflow Concept — Isaac Sim 4.5](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/manipulators/concepts/rmpflow.html)
- [RMPflow Tutorial — Isaac Sim cuMotion 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/cumotion/tutorial_rmpflow.html)
- [isaacsim.robot_motion.motion_generation API — Isaac Sim 5.0](https://docs.isaacsim.omniverse.nvidia.com/5.0.0/py/source/extensions/isaacsim.robot_motion.motion_generation/docs/index.html)
- [Using RMPFlow to Control Manipulators — Simulately](https://simulately.wiki/docs/snippets/isaac-sim/rmpflow/)
