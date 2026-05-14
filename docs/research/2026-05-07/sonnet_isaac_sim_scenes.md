# Ready-made Isaac Sim scenes for reverse-engineering — Sonnet research 2026-05-07

12 scenes inventoried via local Isaac Sim install + web. All Python source files are open Apache-2.0 readable. USD stage files are binary Nucleus assets but the controller/task Python files driving them are the reverse-engineering target.

---

## Scene 1: FrankaPickPlace (single cube)
- **Source**: `/mnt/shared_data/isaac-sim/standalone_examples/api/isaacsim.robot.manipulators/franka/pick_place.py`
- **Robot asset**: `…/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd`
- **Pattern**: canonical tabletop pick-and-place
- **Elements**: Franka Panda, parallel gripper, 1 cube, 1 target zone, no conveyor
- **Tool-call estimate**: <10
- **Why interesting**: simplest correct baseline; matches CP-01 pattern

## Scene 2: UR10PickPlace
- **Source**: `/mnt/shared_data/isaac-sim/standalone_examples/api/isaacsim.robot.manipulators/universal_robots/pick_place.py`
- **Pattern**: tabletop pick-place with UR10e variant
- **Why interesting**: alternative arm family; PickPlaceController is robot-agnostic

## Scene 3: UR10BinFilling (gravity-fed dispenser)
- **Source**: `…/exts/isaacsim.robot.manipulators.examples/.../universal_robots/tasks/bin_filling.py`
- **Stage**: `…/Isaac/Samples/Leonardo/Stage/ur10_bin_filling.usd`
- **Props**: `…/Isaac/Props/Flip_Stack/{screw_95, screw_99, large_corner_bracket, small_corner_bracket, t_connector}_physics.usd`
- **Pattern**: robot positions bin under pipe; pipe gravity-feeds mixed parts (screws, brackets) into bin
- **Elements**: UR10 + surface gripper, packing bin, up to 100 random small parts
- **Tool-call estimate**: 30-100
- **Why interesting**: Only bundled scene with multi-object gravity-fill physics + `controller.pause()` mid-task. Event-driven controller interruption — absent from CP-01..05.

## Scene 4: FrankaRoboFactory (4× parallel stacking)
- **Source**: `…/exts/isaacsim.examples.interactive/.../robo_factory/robo_factory.py`
- **Pattern**: factory floor with N robots stacking in parallel
- **Elements**: 4× Franka, each with own StackingController, 3 cubes per station
- **Tool-call estimate**: 10-30
- **Why interesting**: First bundled multi-robot pattern. Demos offset-based environment cloning (`offset=np.array([0,(i*2)-3,0])`).

## Scene 5: UR10BinStacking / Palletizing (Cortex + conveyor)  ★ TOP PICK
- **Source**: `…/ur10_palletizing/ur10_palletizing.py`
- **Stage**: `…/Isaac/Samples/Leonardo/Stage/ur10_bin_stacking_short_suction.usd`
- **Props**: `…/Isaac/Props/KLT_Bin/small_KLT.usd`
- **Background**: `…/Isaac/Environments/Simple_Warehouse/warehouse.usd`
- **Pattern**: conveyor → robot → pallet stacker with flip station
- **Elements**: UR10 + suction, live conveyor (-0.30 m/s), KLT bins spawn dynamically, flip station (orientation-aware), pallet target, 4 named obstacle volumes
- **Tool-call estimate**: 30-100
- **Why interesting**: Only bundled scene with running conveyor + dynamic spawn + orientation-aware gripper logic. Maps to CP-02/CP-03 patterns. Flip station is sub-pattern not covered elsewhere.

## Scene 6: FrankaCortexBlockStacking (reactive obstacle-aware)
- **Source**: `…/standalone_examples/api/isaacsim.cortex.framework/franka_examples_main.py`
- **Pattern**: Franka stacks 4 colored cubes, replans around obstacles
- **Elements**: Franka, 4 DynamicCuboids R/G/B/Y, `robot.register_obstacle()`, CortexWorld decider network
- **Tool-call estimate**: 10-30
- **Why interesting**: Only local example showing Cortex decider-network wiring + per-object obstacle registration. `register_obstacle` pattern needed for safe multi-object scenes.

## Scene 7: SurfaceGripperGantry (3-axis linear robot)
- **Source**: `…/exts/isaacsim.examples.interactive/data/SurfaceGripper_gantry.usda` (USDA, fully readable)
- **Pattern**: XYZ gantry with suction end-effector
- **Elements**: 3 linear-axis articulation + joints, OmniGlass + OmniPBR materials, ground with CollisionPlane
- **Tool-call estimate**: 30-100
- **Why interesting**: Only bundled scene where USD source is local + readable. Gantry (not 6-DOF arm) is a pattern CP-01..05 don't cover. Surface gripper physics distinct from parallel.

## Scene 8: FrankaDrawerOpen (articulated furniture)  ★ TOP PICK
- **Source**: `…/exts/isaacsim.examples.interactive/.../franka/franka_example.py`
- **Cabinet**: `…/Isaac/Props/Sektion_Cabinet/sektion_cabinet_instanceable.usd`
- **Pattern**: Franka opens articulated cabinet drawer using trained policy
- **Elements**: Franka, FrankaOpenDrawerPolicy (RL), Sektion cabinet as SingleArticulation, physics_dt=1/400
- **Tool-call estimate**: <10
- **Why interesting**: Only bundled example with articulated environment object (drawer). Canonical for "open cabinet / interact with articulated prop" prompts. Pattern absent from all CPs.

## Scene 9: CobottaPro900PickPlace
- **Source**: `…/standalone_examples/api/isaacsim.robot.manipulators/cobotta_900/...`
- **Robot**: `…/Isaac/Robots/Denso/CobottaPro900/cobotta_pro_900.usd`
- **Pattern**: compact desktop arm pick-place with RMPflow
- **Why interesting**: Third arm family (Denso). Useful as "desktop cobot" canonical.

## Scene 10: RoboParty (mixed fleet)
- **Source**: `…/exts/isaacsim.examples.interactive/.../robo_party/robo_party.py`
- **Pattern**: Franka stacking + UR10 stacking + Kaya holonomic + Jetbot differential, all co-simulated
- **Elements**: Franka, UR10, Kaya (`…/Isaac/Robots/NVIDIA/Kaya/kaya.usd`), Jetbot (`…/Isaac/Robots/NVIDIA/Jetbot/jetbot.usd`)
- **Tool-call estimate**: 30-100
- **Why interesting**: Only scene combining manipulation + mobile navigation in one world. Mobile alongside arm is absent from all CPs.

## Scene 11: GraspingWorkflowSDG (grasp-pose generation + SDG)  ★ TOP PICK
- **Source**: `…/standalone_examples/api/isaacsim.replicator.grasping/grasping_workflow_sdg.py`
- **Pattern**: automated grasp-pose sampling + SDG image generation loop
- **Elements**: any robot + gripper, GraspingManager, replicator data writer
- **Tool-call estimate**: 10-30
- **Why interesting**: Only example closing the loop sim → training data. Canonical for "generate grasp dataset" prompts.

## Scene 12: UR10ConveyorCortex (standalone bin stacking)
- **Source**: `…/standalone_examples/api/isaacsim.cortex.framework/demo_ur10_conveyor_main.py`
- **Pattern**: pure-Python standalone version of Scene 5
- **Why interesting**: Best basis for headless canonical template — entire world setup explicit in one file.

---

## Pattern coverage gaps

| New pattern | Scene(s) |
|---|---|
| Articulated environment object (drawer, cabinet) | #8 FrankaDrawerOpen |
| Gantry / linear-axis robot (not 6-DOF arm) | #7 SurfaceGripperGantry |
| Gravity-fill with mixed small-part physics | #3 UR10BinFilling |
| Orientation-aware gripper + flip station | #5/#12 UR10BinStacking |
| Multi-robot parallel (N arms, same task) | #4 RoboFactory |
| Mixed fleet: arm + mobile robot co-sim | #10 RoboParty |
| Grasp-pose dataset generation loop | #11 GraspingWorkflowSDG |
| Cortex decider-network + obstacle registration | #6 FrankaCortexBlockStacking |

---

## Top 3 picks for immediate reverse-engineering

1. **UR10BinStacking** (#5/#12) — conveyor + dynamic spawn + flip station; richest single-robot pattern
2. **FrankaDrawerOpen** (#8) — articulated prop interaction; zero overlap with existing CPs; 60-line source
3. **GraspingWorkflowSDG** (#11) — closes sim-to-training-data loop; entirely new capability class

---

## Sources
- [Franka Pick and Place — Isaac Sim Docs](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/examples/manipulation_franka_pick_place.html)
- [UR10 Bin Stacking — Isaac Sim Docs](https://docs.isaacsim.omniverse.nvidia.com/latest/cortex_tutorials/tutorial_cortex_5_ur10_bin_stacking.html)
- [Surface Gripper Extension](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/robot_simulation/ext_isaacsim_robot_surface_gripper.html)
- [GitHub — IsaacSim](https://github.com/isaac-sim/IsaacSim)
- [GitHub — IsaacLab](https://github.com/isaac-sim/IsaacLab)
- [Standalone Examples List](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/introduction/standalone_examples_list.html)
- [UR10 Palletizing Tutorial](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/replicator_tutorials/tutorial_replicator_ur10_palletizing.html)
