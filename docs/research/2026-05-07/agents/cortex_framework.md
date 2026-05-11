# Cortex Framework Deep-Dive

For porting Cortex-based scenes (#27 UR10BinStacking, #28 FrankaCortexBlockStacking, #33 demo_ur10_conveyor).

## Core concepts

**CortexWorld**: extends Isaac `World` with 3-phase per-step pipeline: logical state monitors → behaviors (decisions) → robot commanders. `world.run(simulation_app)` blocks until exit, calling `world.step()` at 60 Hz.

**DfNetwork / behavior tree**: wraps a root `DfDecider`. Each step descends tree via `df_descend()`, calling `enter()`/`decide()`/`exit()` as active path changes. Leaves are `DfAction` (issue commands), internals return `DfDecision("child_name")`. Reactive by default. `DfRldsDecider` implements RLDS priority chain.

**CortexRobot wrapper**: base → `MotionCommandedRobot` (RmpFlow `arm` commander) → `CortexFranka` / `CortexUr10` (gripper/suction). API: `robot.arm.send_end_effector(target_pose)`, `robot.gripper.close()`. Helpers: `add_franka_to_stage()`, `add_ur10_to_stage()`.

**register_obstacle**: `robot.register_obstacle(prim)` calls `arm_commander.add_obstacle(prim)` → RmpFlow. Dynamic toggle via `arm.disable_obstacle(obs)`/`enable_obstacle(obs)` from logical state monitors. `ObstacleMonitor` helper auto-toggles.

**Step loop**: `CortexWorld.run()` calls `world.step()` each cycle. Phases: (1) `LogicalStateMonitor.pre_step()`; (2) `Behavior.pre_step()` calls `DfNetwork.step()` descending decider tree; (3) robot `pre_step()` translates commands via RmpFlow → PhysX.

## Cortex vs PickPlaceController vs cuRobo

| Aspect | Cortex | PickPlace | cuRobo |
|--------|--------|-----------|--------|
| Motion planner | RmpFlow (RMPflowCortex) | RmpFlow built-in | cuRobo trajectory optimizer |
| Control | `arm.send_end_effector()` per step | `controller.forward()` per step | full traj pre-computed + replayed |
| Behavior logic | DfDecider tree, reactive, RLDS | simple state machine | none — pure motion |
| Obstacle avoidance | Dynamic enable/disable per monitor | Static, registered at setup | World YAML, rebuilt each solve |
| Reset | `world.reset_cortex()` | `controller.reset()` | rebuild motion graph |

## Tool calls needed for canonical scaffold

```python
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.robot import add_franka_to_stage  # or add_ur10_to_stage
from isaacsim.cortex.framework.df import DfNetwork, DfDecider, DfAction, DfDecision

world = CortexWorld()
world.scene.add_default_ground_plane()
robot = world.add_robot(add_franka_to_stage(name="franka", prim_path="/World/Franka"))

obj = world.scene.add(DynamicCuboid(prim_path="/World/Obs/Block", ...))
robot.register_obstacle(obj)

decider_network = behavior_module.make_decider_network(robot)
world.add_decider_network(decider_network)
```

## Mapping to existing infra

**Don't extend setup_pick_place_controller** — Cortex's architecture is fundamentally different (world-level pipeline, behavior module injection). Add separate tool:

```python
setup_cortex_behavior(
    robot_path,
    behavior_module,        # e.g. "isaacsim.cortex.behaviors.franka.block_stacking_behavior"
    obstacle_paths=[],
    task_class=None,        # optional BaseTask subclass for spawning (UR10 conveyor)
)
```

**Per-step subscription** (Kit-safe, non-blocking — replaces `world.run()`):
```python
import omni.physx
_cortex_world = CortexWorld.instance()
def _cortex_step(dt):
    _cortex_world.step(render=False, step_sim=False)  # Cortex pipeline only; Kit owns physics
_cortex_pp_sub_<TAG> = omni.physx.get_physx_interface().subscribe_physics_step_events(_cortex_step)
builtins._cortex_pp_sub_<TAG> = _cortex_pp_sub_<TAG>
```

**Cleanup integration**: add `"_cortex_pp_sub_"` to `_PP_SUB_PREFIXES` tuple (line 29866 of tool_executor.py) — stale-sub scan auto-handles.

## Port strategy

Port FrankaCortexBlockStacking first — simplest (1 robot, 4 obstacles, 1 behavior, 65 lines).

Behavior class: `isaacsim.cortex.behaviors.franka.block_stacking_behavior.make_decider_network(robot)`.

Source: Sonnet agent `a4f6dda94f3fdb4c6` 2026-05-07.
