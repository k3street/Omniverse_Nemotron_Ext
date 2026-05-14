# Isaac Sim Asset Inventory — Local Install

Asset root: `Isaac/Robots/`, `Isaac/Props/`, `Isaac/Environments/`, `Isaac/Samples/` (in NVIDIA assets bundle).

## Robots (94 available)

**Manipulators (6-DoF / 7-DoF arms)**:
- FrankaPanda (FrankaRobotics, 7-DoF) — primary workhorse + FactoryFranka + FR3 variants
- UR3, UR3e, UR5, UR5e, UR10, UR10e, UR16e, UR20, UR30 (UniversalRobots — full family)
- xarm6, xarm7, uf850, lite6 (Ufactory) + matching xarm_gripper, lite6_gripper
- Kawasaki RS007L/N, RS013N, RS025N, RS080N (all paired with OnRobot RG2)
- Kinova Gen3 (7-DoF, instanceable)
- CobottaPro900/1300 (Denso, 6-DoF)
- CRX10IAL (Fanuc)
- FestoCobot, Flexiv Rizon4
- Sawyer (RethinkRobotics, 7-DoF, instanceable)
- Techman TM12 (6-DoF), Yasakawa NEX10
- Kuka KR210 L150 (6-DoF, large payload)
- Z1 (Unitree solo arm)

**Grippers (standalone)**:
- Robotiq 2F-85, 2F-140, Hand-E
- ShadowHand, AllegroHand (dexterous)
- Dex3, Dex5 (Unitree dexterous)

**Mobile robots**:
- NovaCarter, Carter v1 (NVIDIA, wheeled + lidar)
- Jetbot, Kaya, Leatherback (NVIDIA small wheeled)
- Forklift B/C (IsaacSim warehouse)

**Legged robots**:
- Spot, Spot-with-arm (BostonDynamics)
- Go1/Go2/A1/B2/aliengo/laikago (Unitree quadrupeds)
- ANYmal B/C/D (ANYbotics quadrupeds)

**Humanoids / bipedal**:
- H1 + hand payloads (Unitree)
- G1 (Unitree, 29-DoF, bimanual)
- Digit, Cassie (Agility)
- Neo (1X), Phoenix (SanctuaryAI), STAR1 (RobotEra)
- Valkyrie, Tien Kung, PX5

**Mobile-manipulator combos**:
- RidgebackFranka, RidgebackUr5 (Clearpath)

**Education tier**:
- Dofbot (Yahboom), so100/so101, limo, Turtlebot3

## Props/objects

| Type | Subfolder | Use case |
|---|---|---|
| Bins | `KLT_Bin/` (3 variants: full, visual, collision) | Pick-from-bin destinations |
| Conveyors | `Conveyors/` — ConveyorBelt_A01..A49 (49 variants) | All conveyor scenes |
| Pallets | `Pallet/` — pallet, holder, holder_short, o3dyn | AMR/palletizing |
| Crates | `PackingTable/props/` — SM_Crate_A07/A08, corrugated boxes b04..b35 | Packing |
| Industrial fasteners | `Factory/` — bolts/nuts M4..M20 (loose+tight) | Assembly |
| Assembly parts | `Flip_Stack/` — screws, brackets, t_connectors, caster | Assembly |
| IsaacLab Factory | `IsaacLab/Factory/` — bolt M16, gear (small/med/large), nut M16, peg+hole 8mm | Gear/peg insertion |
| AutoMate connectors | `IsaacLab/AutoMate/` — 10 plug+socket pairs | Connector insertion |
| Furniture | `Sektion_Cabinet/` + door physics configs | Drawer/door |
| Packing table | `PackingTable/` + container_h20 | Packing station |
| Blocks | `Blocks/` — colored block (R/G/B/Y), DexCube, MultiColorCube | Pick-place |
| YCB | `YCB/Axis_Aligned/` — 21 objects (soup can, cracker box, mustard bottle, drill, scissors, foam brick, etc.) | Grasping benchmarks |
| Shapes | `Shapes/` — cube, cylinder, sphere, cone, torus, disk, plane | Generic |
| Household | `Mugs/`, `Food/`, `Rubiks_Cube/` | Household manipulation |
| Vehicle | `Forklift/`, `Dolly/` | Warehouse |
| Deformable | `DeformableTube/` | Deformable object tasks |
| Camera prop | `Camera/` + checkerboard_6x10 | SDG calibration |
| Mounts | `Mounts/` | Robot mounting |

## Stages (pre-built scenes)

| Name | Path | Pattern |
|---|---|---|
| Simple Warehouse | `Environments/Simple_Warehouse/warehouse.usd` | Shelf-to-shelf, conveyor |
| Full Warehouse | `Environments/Simple_Warehouse/full_warehouse.usd` | Larger floor |
| Warehouse multi-shelves | `Environments/.../warehouse_multiple_shelves.usd` | SDG variety |
| Warehouse forklifts | `Environments/.../warehouse_with_forklifts.usd` | AMR scenario |
| Digital Twin Warehouse | `Environments/Digital_Twin_Warehouse/small_warehouse_digital_twin.usd` | Sim2real |
| Hospital | `Environments/Hospital/hospital.usd` | Service robot |
| Office | `Environments/Office/office.usd` | Desktop manipulation |
| Simple Room | `Environments/Simple_Room/simple_room.usd` | Generic indoor |
| Grid rooms | `Environments/Grid/{default, black, curved}` | RL training bg |
| Terrains | `Environments/Terrains/{flat, rough, slope, stairs}` | Legged training |
| Jetracer track | `Environments/Jetracer/jetracer_track_solid.usd` | Racing/nav |
| Cortex Franka BlocksWorld | `Samples/Cortex/Franka/BlocksWorld/cortex_franka_blocks.usd` | TAMP demo |
| Cortex UR10 Basic | `Samples/Cortex/UR10/Basic/cortex_ur10_basic.usd` | UR10 manip |
| FrankaNutBolt | `Samples/Examples/FrankaNutBolt/` | Bolt threading |
| Leonardo stacking conveyor | `Samples/Leonardo/Props/stacking_conveyor.usd` | Stacking SDG |

## Sensors supported

- RGB Camera (`UsdGeom.Camera`)
- Depth camera (RGB-D pipeline)
- RTX LiDAR (physx + RTX variants)
- Contact sensor (`ContactSensor` API schema)
- IMU (`Imu` sensor schema)
- Proximity / range (`ProximitySensor`)
- Azure Kinect (visual mesh; sensor via Camera prim)
- Nova Carter dev kit sensors (bundled in robot USD)

## Gaps

| Missing | Impact |
|---|---|
| Delta robot (ABB IRB 360, Fanuc M-1/M-2) | High-speed bin-picking — delta kinematics absent |
| ABB robots (IRB series) | Major industrial brand; only Fanuc CRX10IAL present |
| SCARA robots | Fast tabletop assembly |
| Yaskawa Motoman full (HC10, AR series) | NEX10 only |
| H1-2 / pre-assembled bimanual H1 | Base + hand payloads exist, no bundled scene |
| ConveyorBelt A35/A36 | Numbering jumps — two variants missing |
| Large/Euro KLT containers | Only `small_KLT` |
| Suction-cup standalone | Only parallel-jaw grippers; suction in legacy UR10 only |
| Assembly fixture/jig props | No generic jig USD |

Source: Sonnet agent `a731a902ed70d66e2` 2026-05-07.
