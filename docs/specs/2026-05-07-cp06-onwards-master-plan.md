# CP-06 onwards — Master plan for 33 canonical scenarios

Living document. Updated as canonicals ship.

## Goal

Expand from CP-01..CP-05 (5 verified pick-place canonicals) to **33 canonicals** covering all major industrial manipulation patterns. Each canonical is a deterministic build template (`workspace/templates/CP-NN.json`) that the orchestrator's hard-instantiate path matches against user prompts.

## Decision: scope, role, ordering

- **Role of CP-N system**: retrieval-fallback for general manipulation prompts (B), with secondary value as honesty-test substrate (D). Not optimizing for G1-pipeline scaffolding (C) since that's a separate Phase 9 axis Kimate drives himself.
- **Build order**: cluster by shared tools, build cheap ones first to validate reverse-engineer flow, save tool-novel ones for after the simple ports prove the pipeline.
- **Cap on ambition**: 33 scenarios is the goal; if we hit hard physics-tuning issues (CP-05-style), document and probe-mark rather than block.

## Phase plan (5 sprints)

### Sprint 1 — Trivial ports (CP-06, CP-07, CP-08)
**Goal**: validate reverse-engineering flow with no new tools.
**Canonicals**:
- CP-06: UR10PickPlace — port `/standalone_examples/.../universal_robots/pick_place.py`, ~10 tool calls
- CP-07: CobottaPro900PickPlace — same shape, Denso 6-DoF arm
- CP-08: FrankaRoboFactory — 4× parallel Franka stacking; tests `clone_envs` offset

**Output**: 3 new canonicals, function-gate verified, hard-instantiate matched.

### Sprint 2 — Tier A tool burst
**Goal**: build `compute_stack_placement` + `add_vision_classifier_gate` (+ `setup_robot_claim_mutex` if time). Each unlocks 4-7 canonicals.
**Tool builds**:
1. `compute_stack_placement(target_path, layer_pattern, cube_size)` — emits placement coords for layered/columnar stacking. Used by 7 scenarios.
2. `add_vision_classifier_gate(camera_path, class_to_destination)` — wraps `vision_detect_objects` in routing state machine. Used by 6.
3. `setup_robot_claim_mutex(robots, claim_table_attr)` — shared-resource arbitration. Used by 4.

**Canonicals enabled (6+ new)**:
- CP-09: Inspect-and-Reject Divert (vision gate)
- CP-10: Vision Quality Gate 3-path
- CP-11: Size+Color 4-Class Sorter (vision gate × 2 axes)
- CP-12: Nested-Box Packer (stack placement + count gate)
- CP-13: Graduated Tower (stack placement + priority sort)
- CP-14: Pinwheel Palletizer (stack placement + gap forbidden zone)

### Sprint 3 — Surface gripper + Cortex cluster
**Goal**: support suction/vacuum end-effectors + Cortex behavior trees.
**Tool builds**:
1. `surface_gripper(robot_path, attach_offset)` — vacuum/magnetic adhesion (4 scenarios)
2. `setup_cortex_behavior(robot, behavior_module, obstacles, task_class)` — Kit-safe wrapper around `world.step()` (3 scenarios)
3. `set_gripper_rotation(robot, yaw_deg)` — wrist rotation for layer-alternating palletizing (3)
4. `register_moving_obstacle(robot, obstacle_prim)` — Cortex dynamic-obstacle pattern (2)

**Canonicals enabled (4 new)**:
- CP-15: UR10BinFilling (gravity dispenser + surface gripper)
- CP-16: SurfaceGripperGantry (linear-axis robot + surface gripper)
- CP-17: FrankaCortexBlockStacking (Cortex framework first port)
- CP-18: UR10BinStacking (Cortex + conveyor + dynamic spawn + flip station)

### Sprint 4 — Multi-robot coordination cluster
**Goal**: build coordination primitives that several scenarios share.
**Tool builds**:
1. `setup_robot_handoff_signal(giver, receiver, transfer_pose)` — force/signal-triggered dual-grip handoff (3)
2. `create_kit_tray(tray_path, slot_layout)` + `track_slot_occupancy(tray_path)` — kitting (3 scenarios paired)
3. `create_articulated_joint(joint_path, parent, child, type, axis, limits, drive)` — drawer, rotary table, active flipper (2+)

**Canonicals enabled (6 new)**:
- CP-19: Kitting Multi-Source-to-Tray
- CP-20: Two-Cell Kit-Tray Relay (NIST)
- CP-21: Fixed-Point Robot-to-Robot Handoff
- CP-22: Producer/Consumer Bounded Buffer
- CP-23: Leader/Follower Rotary Station
- CP-24: Recipe-Based Kitting

### Sprint 5 — Specialty + advanced
**Goal**: remaining one-off tools, complex scenarios.
**Tool builds (per scenario)**:
- `add_force_torque_sensor` (peg-in-hole)
- `setup_assembly_constraint` (peg-in-hole)
- `barcode_reader_sensor` (postal sorter)
- `nir_material_sensor` (recycling)
- `load_rl_policy` (drawer)
- `setup_grasp_pose_sampler` (grasp SDG)
- `setup_nav_robot` (mixed fleet)

**Canonicals enabled (9 new)**:
- CP-25: Peg-in-Hole Insertion Array
- CP-26: Postal Cross-Belt Sorter
- CP-27: Recycling Multi-Sensor Stream Splitter
- CP-28: FrankaDrawerOpen (RL policy + articulated joint)
- CP-29: GraspingWorkflowSDG
- CP-30: RoboParty (mixed fleet)
- CP-31: CP-09 Dual-Robot Dynamic Fixture Hold
- CP-32: Parallel Picking Duo (Zone Belt)
- CP-33: Brick-Layer Palletizer (compute_stack_placement reuse)
- CP-34: Mixed-SKU Palletizing Column
- CP-35: Parcel Singulation + Heap (`create_heap_zone`)

(Numbering: CP-25..CP-38 — adjust as we ship.)

## Tool build summary

| Tier | Tool | Scenarios using | Build phase |
|------|------|------------------|-------------|
| A | `compute_stack_placement` | 7 | Sprint 2 |
| A | `add_vision_classifier_gate` | 6 | Sprint 2 |
| A | `setup_robot_claim_mutex` | 4 | Sprint 2 |
| A | `create_kit_tray` + `track_slot_occupancy` | 3 (paired) | Sprint 4 |
| B | `surface_gripper` | 4 | Sprint 3 |
| B | `setup_cortex_behavior` | 3 | Sprint 3 |
| B | `setup_robot_handoff_signal` | 3 | Sprint 4 |
| B | `set_gripper_rotation` | 3 | Sprint 3 |
| B | `register_moving_obstacle` | 2 | Sprint 3 |
| B | `create_articulated_joint` | 2 | Sprint 4 |
| B | `barcode_reader_sensor` | 2 | Sprint 5 |
| C | each remaining single-use tool | 1 | Sprint 5 |

## Composition DAG

User insight: canonicals can **combine** to generate novel scenes. Match-spectrum T3 ("Composed") is the agent's path forward when no single canonical exactly matches.

Edges:
- CP-01 (assembly line) + CP-03 (color sort) → CP-09 (color-routed assembly with reject lane)
- CP-04 (compact) + CP-25 (peg-in-hole) → compact assembly cell
- CP-15 (gravity dispenser) + CP-19 (kitting) → multi-fed kitting station
- CP-02 (relay) + CP-09 (vision gate) → quality-checked relay (good cubes pass through, bad rejected mid-relay)
- CP-17 (Cortex) + CP-25 (peg-in-hole) → Cortex-controlled assembly
- CP-29 (grasp SDG) + any pickplace canonical → SDG dataset for that task

The orchestrator's hard-instantiate path needs T3 logic: when no canonical matches > threshold, retrieve top-K matches and propose a **composition** (which 2-3 canonicals chained or merged covers the prompt).

## Verification per canonical

Each shipped canonical:
- ✅ Build via `execute_template_canonical` — n_ok = n_calls
- ✅ Form-gate via `verify_pickplace_pipeline` — pipeline_ok=true
- ✅ Function-gate via `simulate_traversal_check` — cube delivered
- ✅ Hard-instantiate match via `template_retriever` — sim ≥ 0.45, margin ≥ 0.20
- ✅ Multi-cube delivery via `function_gate_multi_cube` (where applicable)
- ✅ Visual review by user

## Progress tracking

| CP-NN | Name | Status | Sprint | Notes |
|-------|------|--------|--------|-------|
| CP-01 | Assembly line | ✅ shipped | (prior) | 4 cubes 100% delivery |
| CP-02 | Multi-robot relay | ✅ shipped | (prior) | 3 cubes through full relay |
| CP-03 | Color-routed sort | ✅ shipped | (prior) | red→Red, blue→Blue |
| CP-04 | Compact 2x2m | ✅ shipped | (prior) | 4 cubes 100% |
| CP-05 | Reorient (passive flip) | ⚠️ probe | (prior) | physics tuning gap |
| CP-06 | Franka + bundled PickPlaceController | 🛑 blocked | Sprint 1 | infra built; cube transport fails (FixedJoint missing). Postponed to Sprint 3. See `workspace/templates/CP-06.json#blocked`. |
| CP-07 | 4× Franka factory (multi-robot scoping) | ✅ form-gate shipped | Sprint 1 | 127/127 build, 16 cubes + 4 conveyors via settle_state, pipeline_ok=True. Function-gate testing pending. |
| CP-08 | 2x2 grid palletizer | ✅ shipped | Sprint 2 | First user of `compute_stack_placement` + `drop_targets`. 37/37 build, form-gate ✓, function-gate ✓ on Cube_1. |
| CP-09 | Graduated tower (5-cube column) | ⚠️ probe | Sprint 2 | Column-stack vertical motion. 43/43 build, form-gate ✓, function-gate stochastic (cuRobo seed × narrow placement margin). |
| CP-10 | 3x3 grid palletizer (9 cubes) | ✅ form-gate shipped | Sprint 2 | Scales CP-08 pattern; 9-entry drop_targets dict, 67/67 build, form-gate ✓. |
| CP-11 | Pinwheel palletizer (donut_3x3) | ✅ form-gate shipped | Sprint 2 | First user of compute_stack_placement v2's `donut_RxC` (8 cubes + center gap). 61/61 build, form-gate ✓. |
| CP-12 | Mixed-SKU palletizer (3 cubes, 5/8/10cm) | ✅ form-gate shipped | Sprint 2 | Per-cube drop-z based on cube size. 33/33 build, form-gate ✓. Function-gate likely fails on 10cm cube (gripper width limit). |
| CP-13 | 2-cube column stack (cube on cube) | ✅ form-gate shipped | Sprint 2 | Smallest multi-layer stack. 27/27 build, form-gate ✓. PalletBase 15×15cm; Cube_2 lands on Cube_1 at z=0.875. |
| CP-14 | 2-robot relay-stacker | ✅ form-gate shipped | Sprint 2 | Combines CP-02 multi-robot + CP-13 column. 29/29 build, form-gate ✓. Per-robot source_paths + drop_targets. |
| CP-15 | Mixed-SKU graduated tower | ✅ form-gate shipped | Sprint 2 | First user of compute_stack_placement v3's `cube_sizes` (cumulative-z column). 3 cubes 10/8/5cm descending. 32/32 build, form-gate ✓. |
| CP-16 | 4-color sorter | ✅ form-gate shipped | Sprint 2 | Scaled CP-03 from 2 to 4 colors. 57/57 build. color_routing dict 4 entries. |
| CP-17 | 3-class semantic sorter (size labels) | ✅ form-gate shipped | Sprint 2 | Validates Semantics_class routing (not just color). 70/70 build. semantic_type='class' works identically. |
| CP-18 | Inspect-and-reject station | ✅ form-gate shipped | Sprint 2 | Demonstrates color_routing fall-through to destination_path for unlabeled cubes. 4 good + 1 reject. 58/58 build. |
| CP-19 | Twin-pallet feeder | ✅ form-gate shipped | Sprint 2 | drop_targets fan-out to 2 pallets (3 cubes each). 53/53 build. Validates multi-destination dispatch within single controller. |
| CP-20 | Brick-layer palletizer (18 cubes 3x3x2) | ✅ form-gate shipped | Sprint 2 | Largest stack canonical. Research-spec brick palletizer with layer_rotation_deg=90. 123/123 build. |
| CP-21 | Gravity-feed station | ✅ form-gate shipped | Sprint 2 | Cubes spawn 17cm above belt, fall, then picked. Tests cube-with-velocity handling. 38/38 build. |
| CP-22..CP-38 | TBD | 📋 planned | — | — |

**Final form-gate sweep CP-07..CP-21 (2026-05-08)** — 14/14 PASS, 825 build calls, 91 cubes:
```
CP-07: 127/127  cubes=16   PASS
CP-08:  39/39   cubes=4    PASS
CP-09:  45/45   cubes=5    PASS
CP-10:  69/69   cubes=9    PASS
CP-11:  63/63   cubes=8    PASS
CP-12:  33/33   cubes=3    PASS
CP-13:  27/27   cubes=2    PASS
CP-14:  29/29   cubes=2    PASS
CP-15:  32/32   cubes=3    PASS
CP-16:  57/57   cubes=4    PASS
CP-17:  70/70   cubes=6    PASS
CP-18:  58/58   cubes=5    PASS
CP-19:  53/53   cubes=6    PASS
CP-20: 123/123  cubes=18   PASS
CP-21:  38/38   cubes=4    PASS  (added post-sweep)
```

**2026-05-07/2026-05-08 progress** — 41+ atomic commits since structural work began. Smoke regression (6 fixtures) green throughout:

- **Phase A (settle_state)** — 9 commits. Replaced regex-based settle extraction with structural `settle_state` JSON field (kcode-spec sec 4 anti-fragility). CP-01..CP-05 migrated; CP-07 unblocked; CP-06 postponed.
- **Phase B (Tier A tools + canonicals + reliability)** — 32+ commits:
  - **`compute_stack_placement` v1+v2+v3** — pure-data placement computer. Patterns: `column`, `grid_RxC`, `donut_RxC` (RxC minus center). v3 adds per-cube `cube_sizes` list with cumulative-z column stacking. Tier A — 7 scenarios depend.
  - **`drop_targets` dict in cuRobo handler** — per-cube drop position dispatch. Falls through to drop_target → DEST_PATH bbox center for unmapped cubes. `_compute_h1` extended to clear all targets.
  - **8 new canonicals**: CP-08 (2x2), CP-09 (column tower), CP-10 (3x3), CP-11 (pinwheel donut), CP-12 (mixed-SKU diagonal), CP-13 (2-cube column), CP-14 (2-robot relay), CP-15 (graduated descending tower) — all form-gate-verified.
  - **function_gate_suite** extended with CP-08 (pass), CP-09/CP-12/CP-14/CP-15 (probe), CP-10/CP-11/CP-13 (pass) — 11 fixtures total.
  - **NaN/OOB-safety in cuRobo controller** — defensive guard against bad-seed trajectories. Skips apply_action when q7 contains NaN/Inf or values > ±5 rad.
  - **Ground plane fix** — added /World/Ground to CP-08..CP-15. Was the actual root cause of "stochastic blowup" (cube fell off conveyor end into infinite void). Now cubes land at_rest at z=0 floor.
  - **`add_vision_classifier_gate` v1** (Tier A #2) — wraps vision_detect_objects with cube↔class matching by left-to-right ordering. **Infrastructure-complete; KNOWN LIMITATION**: viewport capture in Kit RPC returns black-with-axes image; production usage needs Replicator-based capture or render-flush scaffolding TBD.

**Final form-gate sweep CP-07..CP-15 (2026-05-08)**:
```
CP-07: 127/127 build, 16 cubes  PASS
CP-08:  39/39  build, 4 cubes   PASS
CP-09:  45/45  build, 5 cubes   PASS
CP-10:  69/69  build, 9 cubes   PASS
CP-11:  63/63  build, 8 cubes   PASS
CP-12:  33/33  build, 3 cubes   PASS
CP-13:  27/27  build, 2 cubes   PASS
CP-14:  29/29  build, 2 cubes   PASS
CP-15:  32/32  build, 3 cubes   PASS
```

**Reliability post-Ground-fix**: cubes that miss target now land at_rest on Ground (z=0) instead of free-falling to z=-thousands. Stochastic-blowup mode eliminated; remaining function-gate gaps are precision/seed/gripper-width issues, manageable with multi-run N-of-M acceptance + scene tuning.

**Postponed (Sprint 3)**: CP-06 builtin handler (FixedJoint integration), full vision-gate canonicals (need Replicator capture), `set_gripper_rotation` (Tier B), `setup_robot_claim_mutex` (Tier A multi-robot).

## Source documents

- `docs/research/2026-05-07/agents/asset_inventory.md` — 94 robots + props catalog
- `docs/research/2026-05-07/agents/tool_gap_analysis.md` — 33-scenario tool gap table
- `docs/research/2026-05-07/agents/cortex_framework.md` — Cortex port strategy
- `docs/research/2026-05-07/agents/articulated_joints.md` — joint creation API
- `docs/research/2026-05-07/agents/camera_pipeline.md` — vision tools
- `docs/research/2026-05-07/agents/omnigraph_ros2.md` — ROS2 + OmniGraph patterns
- `docs/research/2026-05-07/sonnet_industrial_scenarios.md` — 21 industrial scenarios
- `docs/research/2026-05-07/sonnet_isaac_sim_scenes.md` — 12 Isaac Sim scenes
- `docs/research/PRIVATE/robot_lab_inspection.md` — gitignored, internal IP
