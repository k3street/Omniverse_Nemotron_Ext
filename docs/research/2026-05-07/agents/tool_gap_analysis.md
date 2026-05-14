# Tool Gap Analysis — 33 Simulation Canonical Scenarios

## Existing tools (recap)
`robot_wizard`, `import_robot`, `create_conveyor`, `create_bin`, `add_proximity_sensor`, `setup_pick_place_controller` (with native/spline/curobo/diffik/osc/sensor_gated), `verify_pickplace_pipeline`, `simulate_traversal_check`, `clone_envs`, `apply_physics_material`, `create_material`, `assign_material`, `set_semantic_label`, `bulk_set_attribute`, `bulk_apply_schema`, `apply_force`, `set_joint_targets`, `set_joint_velocity_limit`, `create_omnigraph`, `add_node`, `connect_nodes`, `vision_detect_objects`, `vision_bounding_boxes`, `capture_viewport`, `set_attribute`, `apply_api_schema`, `create_prim`, `setup_contact_sensors`, `setup_ros2_bridge`, `create_conveyor_track`, `setup_pick_place_ros2_bridge`, `assemble_robot`, `create_wheeled_robot`

## Scenario Table

| # | Scenario | New tools needed | Cmplx | Reuses |
|---|---|---|---|---|
| 1 | FrankaPickPlace (single cube) | none | 1 | CP-01 full |
| 2 | UR10PickPlace | none | 1 | CP-01 full |
| 3 | CobottaPro900PickPlace | none | 1 | CP-01 full |
| 4 | CP-06 Inspect-and-Reject Divert | `add_vision_classifier_gate` | 2 | CP-01, CP-03 |
| 5 | CP-07 Kitting (multi-source to tray) | `create_kit_tray`, `track_slot_occupancy` | 3 | CP-01, CP-02 |
| 6 | CP-08 Two-Cell Kit-Tray Relay | `create_kit_tray`, `track_slot_occupancy`, `setup_robot_handoff_signal` | 4 | CP-02, CP-04 |
| 7 | CP-09 Dual-Robot Dynamic Fixture Hold | `setup_robot_handoff_signal`, `register_moving_obstacle` | 5 | CP-04 |
| 8 | CP-10 Parcel Singulation + Sort | `create_heap_zone`, `add_vision_classifier_gate` | 3 | CP-03 |
| 9 | CP-11 Mixed-SKU Palletizer | `compute_stack_placement`, `set_gripper_rotation` | 3 | CP-01, CP-02 |
| 10 | Parallel Picking Duo (Zone Belt) | `setup_zone_partition`, `setup_robot_claim_mutex` | 4 | CP-04, CP-02 |
| 11 | Fixed-Point Robot-to-Robot Handoff | `setup_robot_handoff_signal` | 3 | CP-04 |
| 12 | Producer/Consumer Bounded Buffer | `create_staging_rack`, `setup_robot_claim_mutex` | 4 | CP-04 |
| 13 | Leader/Follower Rotary Station | `create_rotary_table`, `setup_robot_claim_mutex` | 5 | CP-04 |
| 14 | Vision-Gated Bin Picking Duo | `setup_robot_claim_mutex`, `add_vision_classifier_gate` | 4 | CP-04, CP-05 |
| 15 | Vision Quality Gate (3-path) | `add_vision_classifier_gate` | 2 | CP-03 |
| 16 | Size+Color 4-Class Sorter | `add_vision_classifier_gate` | 2 | CP-03 |
| 17 | Postal Cross-Belt Sorter | `barcode_reader_sensor`, `add_vision_classifier_gate`, `create_recirculation_loop` | 4 | CP-03, CP-02 |
| 18 | Recycling Multi-Sensor | `nir_material_sensor`, `barcode_reader_sensor` | 4 | CP-03 |
| 19 | Kitting (recipe-based) | `create_kit_tray`, `track_slot_occupancy` | 3 | CP-01, CP-05 |
| 20 | Brick-Layer Palletizer | `compute_stack_placement`, `set_gripper_rotation` | 3 | CP-01 |
| 21 | Nested-Box Packer | `compute_stack_placement` | 2 | CP-01, CP-05 |
| 22 | Peg-in-Hole Array | `add_force_torque_sensor`, `setup_assembly_constraint` | 4 | CP-01 |
| 23 | Graduated Tower | `compute_stack_placement` | 2 | CP-01, CP-05 |
| 24 | Pinwheel Palletizer | `compute_stack_placement` | 3 | CP-01 |
| 25 | UR10BinFilling (gravity dispenser) | `create_gravity_dispenser`, `surface_gripper` | 4 | CP-02 |
| 26 | FrankaRoboFactory (4× parallel) | clone_envs offset check | 2 | CP-01 × N |
| 27 | UR10BinStacking (Cortex) | `compute_stack_placement`, `set_gripper_rotation`, `surface_gripper`, `setup_cortex_behavior` | 4 | CP-02, CP-01 |
| 28 | FrankaCortexBlockStacking | `register_moving_obstacle`, `setup_cortex_behavior` | 3 | CP-01 |
| 29 | SurfaceGripperGantry | `create_linear_axis_robot`, `surface_gripper` | 4 | none |
| 30 | FrankaDrawerOpen | `create_articulated_joint`, `load_rl_policy` | 3 | CP-01 |
| 31 | RoboParty (mixed fleet) | `setup_nav_robot` | 4 | CP-04 |
| 32 | GraspingWorkflow SDG | `setup_grasp_pose_sampler` | 3 | CP-01 |
| 33 | UR10ConveyorCortex (standalone) | `compute_stack_placement`, `surface_gripper`, `setup_cortex_behavior` | 3 | CP-02, CP-01 |

## Tool Tiers

**Tier A — needed by 5+ scenarios (build first)**
- `compute_stack_placement` — #9, #20, #21, #23, #24, #27, #33 (7)
- `add_vision_classifier_gate` — #4, #8, #14, #15, #16, #17 (6)
- `setup_robot_claim_mutex` — #10, #12, #13, #14 (4)
- `create_kit_tray` + `track_slot_occupancy` — #5, #6, #19 (paired, 3)

**Tier B — needed by 2-4 scenarios**
- `surface_gripper` — #25, #27, #29, #33 (4)
- `setup_cortex_behavior` — #27, #28, #33 (3)
- `setup_robot_handoff_signal` — #6, #7, #11 (3)
- `set_gripper_rotation` — #9, #20, #27 (3)
- `barcode_reader_sensor` — #17, #18 (2)
- `register_moving_obstacle` — #7, #28 (2)
- `create_articulated_joint` — #30, #13 rotary (2)

**Tier C — single-use** (build only when scenario prioritized)
- `create_gravity_dispenser` (#25), `create_heap_zone` (#8), `create_recirculation_loop` (#17), `nir_material_sensor` (#18), `add_force_torque_sensor` (#22), `setup_assembly_constraint` (#22), `create_linear_axis_robot` (#29), `load_rl_policy` (#30), `setup_grasp_pose_sampler` (#32), `setup_nav_robot` (#31), `create_staging_rack` (#12), `create_rotary_table` (#13), `setup_zone_partition` (#10)

## Build Order

1. **Burst-1: Trivial ports** (no new tools): #1, #2, #3 → CP-06, CP-07, CP-08 baseline alt-arm coverage. Validate reverse-engineer flow.
2. **Burst-2: Tier A** — `compute_stack_placement` + `add_vision_classifier_gate` unlocks 13 scenarios at once.
3. **Burst-3: Surface-gripper cluster** — `surface_gripper` + `set_gripper_rotation` + `setup_cortex_behavior` unlocks #27, #29, #33 + Cortex family (#28).
4. **Burst-4: Multi-robot cluster** — `setup_robot_claim_mutex` + `setup_robot_handoff_signal` unlocks #6, #7, #10, #11, #12, #13.
5. **Burst-5: Specialty tools** as needed for remaining scenarios.

Source: Sonnet agent `a71ef673179c89fbb` 2026-05-07.
