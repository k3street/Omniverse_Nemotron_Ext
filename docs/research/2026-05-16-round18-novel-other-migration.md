# Round 18 — Novel-Pattern "Other" Cluster Migration

**Date:** 2026-05-16
**Scope:** 14 templates with `migration_deferred.reason == "novel_pattern"` → `pattern_hint="other"` + `structural_tags`

---

## §1 Per-Template Migration Summary

| ID | pattern_hint | structural_tags (key 3) | destination_kind | Result |
|----|-------------|------------------------|-----------------|--------|
| CP-48 | other | perception.vision_classifier, routing.color_based_reject, topology.inspect_and_reject | n_bins_routed | PASS |
| CP-57 | other | topology.heap_singulation, workpiece.heap_zone, transport.heap_surface | single_bin | PASS |
| CP-59 | other | perception.vision_classifier, topology.dual_robot, coordination.mutex | n_bins_routed | PASS |
| CP-60 | other | topology.recirculation_loop, transport.closed_loop, topology.no_robot | (removed) | PASS |
| CP-61 | other | execution.cortex_behavior_tree, topology.single_station, coordination.moving_obstacle | single_bin | PASS |
| CP-65 | other | topology.kit_tray_relay, coordination.handoff_signal, workpiece.kit_tray | single_bin | PASS |
| CP-67 | other | topology.rotary_table, topology.dual_robot, coordination.leader_follower | single_bin | PASS |
| CP-73 | other | robot.fixed_base.ur10, execution.cortex_behavior_tree, robot.import_asset_library | single_bin | PASS |
| CP-87 | other | bridge.ros2_control, execution.external_moveit2, integration.topic_based_ros2 | single_bin | PASS |
| CP-NEW-amr-pickup-handoff | other | robot.mobile.amr, topology.arm_amr_handoff, coordination.dock_and_pickup | single_bin | PASS |
| CP-NEW-cross-belt-sorter | other | topology.cross_belt_sorter, topology.postal_sortation, routing.vision_label_divert | n_bins_routed | PASS |
| CP-NEW-multi-amr-corridor | other | robot.mobile.amr, topology.multi_amr_fleet, coordination.collision_avoidance | (removed) | PASS |
| CP-NEW-opcua-12conveyors | other | bridge.opcua, integration.live_tag_control, topology.industrial_bridge_validation | (removed) | PASS |
| CP-NEW-plc-conveyor | other | bridge.modbus_tcp, integration.plc_in_the_loop, topology.industrial_bridge_validation | (removed) | PASS |

All 14 templates passed `instantiate_role_based_code` substitution (no unfilled `{{}}` placeholders) and equivalence test (captured tool calls match legacy `code` field exactly).

---

## §2 Reverts

None. All 14 templates migrated successfully on first attempt.

**One destination_kind fix pass required** after initial apply: the migration script used non-schema values (`'bin'`, `'loop'`, `'amr_bin'`, `'chute'`, `'waypoint'`, `'none'`). All corrected to schema-valid values (`single_bin`, `n_bins_routed`) or removed (for no-robot/no-destination topologies). This was caught immediately by lint.

---

## §3 Lint Baseline Counts

| Metric | Before R18 | After R18 | Delta |
|--------|-----------|-----------|-------|
| Templates scanned | 321 | 321 | 0 |
| OK | 265 | 266 | +1 |
| ERROR | 0 | 0 | 0 |
| WARN | 55 | 55 | 0 |
| INFO | 95 | 67 | -28 |
| R1_MISSING_INTENT | 39 | 25 | **-14** |

Each migrated template removes 2 INFO items (R1_MISSING_INTENT + R2_NO_ROLE_FIELDS), hence −28 INFO for 14 templates.

---

## §4 Structural_Tags Taxonomy — Patterns Observed

Three primary namespaces emerged from this "other" cluster:

**`isaac:topology.*`** (most common — 11/14 templates use at least one)
- Captures scene-level structural shape: `recirculation_loop`, `heap_singulation`, `cross_belt_sorter`, `dual_robot`, `kit_tray_relay`, `rotary_table`, `arm_amr_handoff`, `multi_amr_fleet`, `corridor_navigation`, `no_robot`, `single_station`, `postal_sortation`, `industrial_bridge_validation`
- This namespace is the primary retrieval discriminator for "other" templates

**`isaac:execution.*`** (distinct from topology)
- `cortex_behavior_tree` (CP-61, CP-73) — behavior-tree wrapping vs cuRobo direct
- `external_moveit2` (CP-87) — Isaac Sim as hardware-in-the-loop for ROS2 planner
- Discriminates execution backend, not scene topology

**`isaac:bridge.*` / `isaac:integration.*`** (industrial connectivity)
- `bridge.opcua`, `bridge.ros2_control`, `bridge.modbus_tcp`
- `integration.live_tag_control`, `integration.plc_in_the_loop`, `integration.topic_based_ros2`
- Two sub-levels: protocol-level (`bridge.*`) + deployment-pattern (`integration.*`)
- Pattern: `bridge.*` names the protocol; `integration.*` names the operational pattern

**`isaac:coordination.*`** (multi-agent coordination)
- `mutex`, `handoff_signal`, `leader_follower`, `moving_obstacle`, `collision_avoidance`, `dock_and_pickup`
- Used when multiple agents (robots, AMRs) must share resources or avoid each other

**`isaac:perception.*`** (sensor-driven control)
- `vision_classifier` (CP-48, CP-59) — Gemini vision gate in the pick-place loop
- Distinct from `sensor.*` (CP-58's force_torque, assembly_constraint)

**`isaac:robot.mobile.*`** (AMR sub-namespace)
- `amr` appears in both CP-NEW-amr-pickup-handoff and CP-NEW-multi-amr-corridor
- Distinct from `robot.fixed_base.arm` and `robot.fixed_base.ur10`

---

## §5 Still Deferred After R18

Remaining `migration_deferred` templates (not in scope for R18):

```
reason: "draft"           — templates not yet code-complete
reason: "asset_blocked"   — requires Nucleus assets not locally available
reason: "blocked"         — explicit dependency on unresolved issue
reason: "duplicate"       — superseded by another template
reason: "train_pattern_*" — RL training templates (future round)
```

The `novel_pattern` backlog is now at **0** (all 14 cleared). The `R1_MISSING_INTENT` count of 25 consists entirely of non-CP templates (Y-*, DR-*, etc.) that are migration-pending for future rounds.

---

## Implementation Notes

- **CP-57 code_template**: heap item paths (`/World/CubeHeap/Item_1..5`) are hardcoded literals rather than `{{#each}}` blocks because `create_heap_zone` creates them internally — no role_defaults entry can enumerate sub-paths of a composite tool.
- **CP-NEW-opcua-12conveyors / CP-NEW-plc-conveyor**: Python `for` loops and dict comprehensions preserved in `code_template` (not role-parameterized) because the loop bounds are structural constants, not configurable roles. Single tool-call equivalence maintained.
- **CP-59 Robot B**: Legacy `code` only sets up Robot A's vision controller; Robot B `setup_pick_place_with_vision` is absent. The `code_template` faithfully reproduces this (Robot B is declared in `roles` but its controller is not called — matching the draft-state legacy code).
