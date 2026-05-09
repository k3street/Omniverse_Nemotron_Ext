# Industrial-Expansion Spec — ROS2/OPC-UA Bridges × Yrkesroll Canonicals

**Date:** 2026-05-09
**Status:** active (deferred until Phase 5 of master plan completes)
**Owner:** controller-logic session
**Origin:** Two research outputs that never crossed into the master plan:
- `docs/research/2026-05-09-ros2-openplc-integration.md` — Plays A/B/C, milestones M1-M5
- `docs/research/2026-05-09-yrkesroll-expansion.md` — 8 yrkesroller, Top-20 canonicals

Without this spec the master plan only covers internal controller-logic + multimodal foundation. Industry actually uses Isaac Sim through ROS2 + ros2_control + MoveIt2 (with cuMotion as planner) and connects to real PLCs via OPC-UA / Modbus-TCP / MQTT-Sparkplug — none of which is currently a first-class direct-eval target.

---

## 1. Why this spec exists

Two research findings, both well-evidenced (BMW, Foxconn, Siemens, Beckhoff, FANUC, ABB case studies + first-party NVIDIA tutorials), agree on the following gaps:

1. **ROS2 plumbing is mature but evidence-eval-incomplete.** Handlers exist (`ros_mcp_tools.py`, 8 OmniGraph templates, 9 controller modes including `ros2_cmd`), but no canonical *requires* ROS2 as the primary architecture, so direct_eval never measures the agent's ROS2-mode-selection accuracy. Topic naming also diverges from the de-facto `topic_based_ros2_control` convention.

2. **Industrial customers don't run PLC code in the simulator.** They run real PLCs (Siemens S7, Beckhoff CX, Rockwell ControlLogix) and connect them to Omniverse over OPC-UA / Modbus-TCP / Sparkplug-MQTT. We have ZERO turnkey bridge primitives — F-02 hand-rolls the pattern in agent prose.

3. **The canonical library is internally well-shaped (86 CPs) but role-narrow.** It covers *manipulation engineer + line designer* densely; force-aware contact (Isaac Lab Factory ports), articulated environment objects (drawers/doors/valves), dexterous in-hand, quadruped/bipedal locomotion, RL training scaffolds, AMR fleet, virtual commissioning, OEE metrics, tactile sensing, operator-in-loop ergonomics — all ZERO coverage.

4. **The Top-5 next canonicals each unlock a different role.** G1 bimanual (ML/RL), RL clone-env (ML/RL, but different sub-shape), AMR handoff (logistics), drawer-open (robotics R&D), peg-insert (robotics R&D, contact-rich). One CP per role × 5 roles = high coverage delta per session.

This spec turns those findings into deliverable milestones and slots them into the master plan.

---

## 2. Scope

### In scope

**Protocol-bridge track (Plays A/B/C from ROS2 research):**
- M1: `setup_ros2_control_compat` + `emit_ros2_control_yaml` + `precheck_ros2_environment` + CP-87 (Franka pick-place via external MoveIt2 + topic_based_ros2_control)
- M2: `modbus_tcp_bridge_attach` + 1 CP (PLC-in-the-loop conveyor)
- M3: `opcua_bridge_attach` + F-02 promotion (12-state OPC-UA conveyor)
- M4: `setup_isaac_ros_cumotion_moveit` + controller-shootout artefact across `ros2_cmd + cumotion` vs in-Kit `curobo`
- M5: `openplc_runtime_attach` (P3, opportunistic, byproduct of M2) + `mqtt_sparkplug_bridge_attach` (P2, opportunistic) + 1 CP each

**Yrkesroll-canonicals track (Top-20 from yrkesroll research):**
- Top-5 build-first: G1 bimanual tabletop, RL clone-env training scaffold (64 envs), AMR pickup-from-cell handoff, drawer/cabinet open, peg-in-hole insertion (Factory port)
- Top 6-15: 3-station serial line + OEE log, inspect-and-reject divert, defect-introduction SDG, Y-merge conveyor singulation, domain-randomization curriculum, multi-camera triangulation, controller-shootout reach benchmark, PLC-in-the-loop fixture (overlaps M2's CP), sim-to-real gap measurement (rosbag replay), cross-belt 8-chute sorter
- Top 16-20: multi-AMR corridor, CAD revision-drift, operator-in-loop ergonomics, tactile insertion (TacEx), brick-stacking baseplate

### Out of scope

- IEC-61131-3 runtime *inside* Kit (OpenPLC already exists for that; bridge to it as external process if needed)
- Full PLCopen / TwinCAT / TIA-Portal connector (Siemens Tecnomatix Connector + Beckhoff TF6100 already ship; we are the adapter, not the standard)
- Safety-rated PLC behavior (Isaac Sim is not safety-rated — F-08 explicitly forbids; honesty-tier templates already enforce)
- Sparkplug as P0 (lower density of customer demand than OPC-UA + Modbus combined; ship after M2 + M3)
- Surgical / agriculture verticals (out of scope per yrkesroll research recommendation; park until external signal)
- "Isaac Assist proprietary protocol" — any tool name starting with `isaac_` and ending in `_protocol` is a yellow flag

---

## 3. Phase mapping into master plan

Master plan currently has 8 phases (0-7). This spec adds 4 industrial-track phases. Multimodal phases shift one slot down to keep parallelism.

| Old phase | New phase | Content | Owner |
|---|---|---|---|
| Phase 5 | Phase 5 | 100% function-gate drive on existing 86 CPs (UNCHANGED) | controller-logic |
| (new) | **Phase 6** | **M1 — ROS2 production parity** | controller-logic |
| Phase 6 | Phase 7 | Multimodal Block 2 + 3 (was Phase 6, parallel track) | multimodal |
| (new) | **Phase 8** | **M2-M3 (Modbus + OPC-UA bridges) + Top-5 yrkesroll-canonicals** | controller-logic |
| (new) | **Phase 9** | **M4 (cuMotion-as-MoveIt) + Top 6-15 yrkesroll-canonicals** | controller-logic |
| Phase 7 | **Phase 10** | M5 (OpenPLC + Sparkplug, opportunistic) + Top 16-20 yrkesroll + multimodal Block 4 + 5 | both |

### Why this ordering

1. **Phase 5 must finish first.** 100% function-gate on the existing 86 CPs is the regression-floor. M1+ adds new CPs whose direct_eval scores depend on the in-Kit controllers we just stabilized. Ordering reversed = can't tell if a new CP fails because of the new bridge or the old controller.

2. **Phase 6 (M1) is mostly repackaging.** Topic-naming compat layer + YAML emitter + precheck tool — all wrap existing OmniGraph nodes. Adds CP-87. Low risk, low effort, biggest payoff: ROS2 finally appears in direct_eval as a first-class architecture.

3. **Phase 8 bundles M2-M3 + Top-5 yrkesroll.** Bridges open the F-* and K-* personas; Top-5 unblocks 5 different roles in parallel. Combining them is justified because the Top-5 *don't* depend on bridges (they exercise in-Kit controllers + assets) — they can ship in parallel with M2-M3 development. Splitting them adds coordination cost without parallelism gain.

4. **Phase 9 is the "controller-shootout" payoff.** M4 plus the comparison artefact across all 9 controller modes × 4 robot types is the answer to "which controller for which scenario?" — the question that drives Phase 4 (scenario-profile) decisions and that direct_eval can't currently answer because `ros2_cmd + cumotion` was never measured.

5. **Phase 10 is opportunistic.** OpenPLC + Sparkplug are byproducts of M2-M3 — half a day each. Top 16-20 are the long-tail role coverage. Multimodal Block 4-5 finishes at the same time. Whoever finishes their main work first picks up the next opportunistic item.

---

## 4. Per-phase detail

### Phase 6 — M1: ROS2 production parity

**Goal:** elevate ROS2 from "documented possible" to "scored as first-class architecture in direct_eval." Zero greenfield code; pure repackaging + one new CP.

**Tools to add (`tool_executor.py`):**
1. `setup_ros2_control_compat(robot_path, joint_states_topic="/isaac_joint_states", joint_commands_topic="/isaac_joint_commands", controller_type="joint_trajectory_controller")` — wraps the existing `setup_ros2_bridge` profile but with the standard topic names per `topic_based_ros2_control` convention.
2. `emit_ros2_control_yaml(robot_path, controller_type, output_path)` — generates the YAML that the user runs `colcon build && ros2 launch` against. Includes `controller_manager`, `joint_state_broadcaster`, `joint_trajectory_controller` with `topic_based_ros2_control/TopicBasedSystem` plugin.
3. `precheck_ros2_environment()` — verifies AMENT_PREFIX_PATH, rosbridge port (default 9090), ROS_DOMAIN_ID consistency between Kit + external env. Honest failure surface BEFORE expensive scene build.

**Canonical to add:** `CP-87-ros2-moveit2-franka-pickplace` — Franka + cube + bin + `target_source="ros2_cmd"` + the new compat layer. Test rig launches `ros2 launch` with the emitted YAML and a tiny pick-place client.

**Tests:**
- Unit: `setup_ros2_control_compat` emits OmniGraph with the standard topic names. YAML emitter produces `colcon`-buildable file.
- Integration: precheck reports correct status given (a) AMENT_PREFIX_PATH unset, (b) port 9090 unbound, (c) all good.
- Direct-eval: CP-87 scores ≥ 4/5 against a live `ros2 launch` test rig.

**Exit criterion:** `PLAN.md` 4B/8F updated to "Schema + handlers + ROS2-control compat shipped". CP-87 ships with non-zero direct-eval score.

**Effort:** small. **Risk:** low.

---

### Phase 8 — M2-M3 (bridges) + Top-5 yrkesroll

**Goal:** ship two protocol bridges + 5 yrkesroll-canonicals that each unlock a different role.

**Bridge tools to add (controller-logic, ~3 days):**
1. `modbus_tcp_bridge_attach(host, port, register_map, mode="server"|"client", rate_hz=10)` — pymodbus; bidirectional. Holding registers ↔ joint targets; input registers ← simulated sensor states. Supervised subprocess via `dispatch_async_task` (avoid in-Kit threading per silent-success-audit lessons).
2. `opcua_bridge_attach(server_url, tag_to_attribute_map, rate_hz=1)` — asyncua client; polls tags at `rate_hz`, writes to USD attributes (belt-speed, light-emissive, kinematic translate). Same supervised-subprocess pattern.
3. `diagnose_modbus_bridge(host, port)` + `diagnose_opcua_bridge(server_url)` — honesty-tier counterparts; report transient connection failures.

**Bridge canonicals (1 each, ~1 day):**
- `CP-NEW-plc-conveyor` — Isaac Sim conveyor + 4 digital I/O (start/stop/jam/reset) bridged to OPC-UA mock (Python mock server, not real PLC). Pause within 100ms of Modbus coil flip.
- `F-02-promoted` — F-02 stops being a "explain the pattern" honesty template and becomes a runnable canonical: 12 conveyor states wired to OPC-UA. Reuses M3's primitive.

**Top-5 yrkesroll-canonicals (5 × ~1.5 days):**
1. **CP-NEW-g1-bimanual-tabletop** (ML/RL) — Unitree G1 in tabletop scene, bimanual reach. Maps to GR00T sim-to-real. Asset: G1 SimReady from NVIDIA catalog.
2. **CP-NEW-rl-clone-env** (ML/RL) — 64 parallel Franka-reach envs via `clone_envs`. Launch RL-Games or RSL-RL. 60s sanity train. Reward curve emitted.
3. **CP-NEW-amr-pickup-handoff** (logistics) — Nova Carter docks at cell, Franka places into onboard bin, AMR departs.
4. **CP-NEW-drawer-open** (robotics R&D) — Articulated cabinet + Franka pulls drawer. Direct port from Isaac Lab Open-Drawer-Franka.
5. **CP-NEW-peg-in-hole** (robotics R&D, contact-rich) — 5mm clearance + force-threshold-gated APPROACH→ALIGN→INSERT. Direct port from Isaac Lab Factory PegInsert.

**Tests:**
- Unit: each bridge primitive launches its subprocess, tears it down cleanly on Kit shutdown.
- Integration: M2 conveyor pauses ≤100ms after Modbus coil flip, resumes ≤100ms after clear (measure via timestamped state log).
- Direct-eval: each Top-5 canonical scores ≥ 4/5.
- Regression: existing 86 CPs unchanged in success rate (compare against Phase 0 baseline + Phase 5 final).

**Exit criterion:** 2 bridge tools ship + 7 new canonicals (1 OPC-UA + 1 F-02-promoted + Top-5) — total 86 → 93 canonicals. Direct-eval baseline updated.

**Effort:** medium-large (~7 sessions). **Risk:** medium — async subprocess lifecycle inside Kit needs care.

---

### Phase 9 — M4 (cuMotion-MoveIt) + Top 6-15 yrkesroll

**Goal:** answer "which controller for which scenario?" via measurable controller-shootout artefact + 10 more yrkesroll-canonicals.

**M4 tool:**
- `setup_isaac_ros_cumotion_moveit(robot_path, planner_topic="/move_group/plan")` — wires NVIDIA's `isaac_ros_cumotion_moveit` package (cuRobo-as-MoveIt-OMPL-replacement). External-process planner via ROS2; agent perspective: same `/move_group/plan` action, faster planner.
- Re-runs S-01, S-02, S-09, S-11 in `ros2_cmd + cumotion` mode.
- Generates `workspace/scenario_results/controller_shootout_ros2.json` — comparison across 9 in-Kit modes + this new external mode, per-scenario.

**Top 6-15 canonicals:**
6. **CP-NEW-3station-oee** (mfg eng) — 3-station serial line + per-station idle/busy log + JSON {availability, performance, quality} emitter.
7. **CP-NEW-inspect-reject** (quality) — vision gate → divert chute. Already scoped, never built.
8. **CP-NEW-defect-sdg** (quality) — load asset, randomly add 0-3 surface defects per render, COCO export.
9. **CP-NEW-y-merge-singulation** (logistics) — two infeed lanes merge via Y; assert non-overlap on merged line.
10. **CP-NEW-dr-curriculum** (ML/RL) — randomize friction/mass/lighting/camera FOV per reset; emit randomization-coverage histogram.
11. **CP-NEW-multicam-triangulation** (quality) — 3 cameras at 120°, 3D defect localization.
12. **CP-NEW-controller-shootout** (robotics R&D) — complementary scene to M4's artefact. Same Franka, same target, run cuRobo / spline / RmpFlow / OSC sequentially, log path-length / jerk / time.
13. **CP-NEW-plc-fixture** (sim eng) — overlap with M2's `CP-NEW-plc-conveyor` but adds different fixture topology; test PLC bridging across 2 cell layouts.
14. **CP-NEW-sim2real-gap** (ML/RL) — load real-robot rosbag, replay in sim, compute action/observation delta. AD-tier honesty: agent must NOT claim zero-gap.
15. **CP-NEW-cross-belt-sorter** (logistics) — 1 in-feed, 8 chutes, barcode-driven routing. Builds on CP-60 loop.

**Tests:**
- M4: controller-shootout artefact lives at `workspace/scenario_results/controller_shootout_ros2.json` with non-empty per-scenario rows for all 10 modes (9 in-Kit + cumotion).
- Direct-eval: each Top 6-15 canonical scores ≥ 4/5.

**Exit criterion:** 1 new tool + 10 new canonicals (93 → 103). Controller-shootout artefact published. Comparable to Phoronix-style benchmark blog post.

**Effort:** large (~6 sessions). **Risk:** medium — controller-shootout demands running 4-5 robots × 10 modes × 5-10 scenarios; resource-heavy.

---

### Phase 10 — M5 + Top 16-20 + Multimodal Block 4-5

**Goal:** finish industrial-track tail + multimodal foundation.

**M5 (controller-logic, ~3 days):**
- `openplc_runtime_attach(runtime_url, st_program=None, register_map=None)` — convenience wrapper over Modbus-TCP. ~50 LOC + 1 CP ("teach-PLC: Franka picks while OpenPLC ladder gates the conveyor").
- `mqtt_sparkplug_bridge_attach(broker, group_id, edge_node_id, metric_map)` — Sparkplug B encoded; KION/GXO warehouse pattern. ~1-2 days. 1 CP (warehouse-twin AMR fleet status reporting).

**Top 16-20 canonicals (5 × ~1.5 days):**
16. **CP-NEW-multi-amr-corridor** (logistics) — 3 AMRs sharing 2m corridor, no collision over 60s.
17. **CP-NEW-cad-revision-drift** (sim eng) — load STEP rev A then rev B; diff bbox/mass/collision-mesh; flag changes >5%.
18. **CP-NEW-operator-ergonomics** (mfg eng) — animated human worker at one station with reach/pose envelope; assert robot stays out of envelope.
19. **CP-NEW-tactile-insertion** (ML/RL) — peg insertion with GelSight tactile feedback simulation. TacEx port.
20. **CP-NEW-brick-stacking** (construction wedge) — Franka + LEGO-like bricks, snap-fit baseplate. BrickSim port.

**Multimodal (multimodal session):**
- Block 4 (canvas wiring) — conditional per multimodal-foundation-spec §22.
- Block 5 (hardening) — final integration.

**Tests + exit criterion:** 2 new tools + 7 new canonicals (5 from Top 16-20 + 2 from M5) → 110 canonicals total. All direct-eval baselines refreshed. Multimodal canvas demo runnable.

**Effort:** medium (~5 sessions controller-logic + ~3 sessions multimodal, parallel). **Risk:** low.

---

## 5. Tools added (cumulative summary)

| Phase | Tool | LOC est. |
|---|---|---:|
| 6 | `setup_ros2_control_compat` | ~80 |
| 6 | `emit_ros2_control_yaml` | ~120 |
| 6 | `precheck_ros2_environment` | ~60 |
| 8 | `modbus_tcp_bridge_attach` | ~250 |
| 8 | `opcua_bridge_attach` | ~250 |
| 8 | `diagnose_modbus_bridge` + `diagnose_opcua_bridge` | ~80 |
| 9 | `setup_isaac_ros_cumotion_moveit` | ~150 |
| 10 | `openplc_runtime_attach` | ~50 |
| 10 | `mqtt_sparkplug_bridge_attach` | ~250 |

Total industrial-track tool surface: **~1300 LOC** spread across phases 6, 8, 9, 10.

## 6. Canonicals added (cumulative summary)

| Phase | Canonical | Role |
|---|---|---|
| 6 | CP-87 ROS2-MoveIt2-Franka pick-place | manipulation eng |
| 8 | CP-NEW-plc-conveyor | sim eng |
| 8 | F-02-promoted | sim eng |
| 8 | CP-NEW-g1-bimanual-tabletop | ML/RL |
| 8 | CP-NEW-rl-clone-env | ML/RL |
| 8 | CP-NEW-amr-pickup-handoff | logistics |
| 8 | CP-NEW-drawer-open | robotics R&D |
| 8 | CP-NEW-peg-in-hole | robotics R&D |
| 9 | CP-NEW-3station-oee | mfg eng |
| 9 | CP-NEW-inspect-reject | quality |
| 9 | CP-NEW-defect-sdg | quality |
| 9 | CP-NEW-y-merge-singulation | logistics |
| 9 | CP-NEW-dr-curriculum | ML/RL |
| 9 | CP-NEW-multicam-triangulation | quality |
| 9 | CP-NEW-controller-shootout | robotics R&D |
| 9 | CP-NEW-plc-fixture | sim eng |
| 9 | CP-NEW-sim2real-gap | ML/RL |
| 9 | CP-NEW-cross-belt-sorter | logistics |
| 10 | CP-NEW-multi-amr-corridor | logistics |
| 10 | CP-NEW-cad-revision-drift | sim eng |
| 10 | CP-NEW-operator-ergonomics | mfg eng |
| 10 | CP-NEW-tactile-insertion | ML/RL |
| 10 | CP-NEW-brick-stacking | construction |
| 10 | CP-NEW-openplc-conveyor (M5) | demo |
| 10 | CP-NEW-mqtt-warehouse-fleet (M5) | logistics |

Library size growth: **86 → 110 canonicals** (+28%).

## 7. Validation criteria (cross-cutting)

- Each new tool has a unit test exercising its primary code path.
- Each new canonical scores ≥ 4/5 in direct_eval (multi-run N=5 per Phase 0 protocol).
- Bridge tools (Modbus / OPC-UA / Sparkplug) verify subprocess lifecycle: launch → run → graceful shutdown on Kit close → no zombie process.
- Controller-shootout artefact (`controller_shootout_ros2.json`) has rows for ALL combinations exercised; missing combinations are explicitly flagged with reason.
- `PLAN.md` 4B + 8F + 9E updated when ROS2/bridge code lands (currently outdated).
- Existing 86 CPs maintain their Phase 5 success rate — measured against Phase 0 baseline at end of each industrial phase.

## 8. Open questions / decisions deferred

1. **Robot-asset licensing for Top-5.** Unitree G1, Nova Carter, Franka — all NVIDIA-published SimReady. dVRK / surgical: out of scope so deferred. Confirm asset paths exist before Phase 8 starts.
2. **External test rigs.** M1 + M4 require `ros2 launch` running outside Kit. Decide: dockerize? require user-side install? CI implication.
3. **Bridge subprocess vs in-Kit thread.** Spec says "supervised subprocess via dispatch_async_task" per silent-success-audit lessons. Verify dispatch_async_task can host long-lived subprocesses (vs one-shot tasks) before M2 starts.
4. **CP-NEW-plc-fixture vs CP-NEW-plc-conveyor overlap.** They both exercise PLC bridging. Resolve scope split (different cell topologies? different I/O density?) before Phase 9.
5. **Yrkesroll Top 16-20 scope cuts if Phase 10 runs over.** If running short on time, drop tactile (cutting-edge, low cost-of-deferral) and brick-stacking (new vertical wedge, exploratory) first; keep multi-AMR-corridor + CAD-drift + operator-ergonomics (they unblock named personas).

## 9. References

- `docs/research/2026-05-09-ros2-openplc-integration.md` — full strategic assessment, sources, tool priority list (P0-P3)
- `docs/research/2026-05-09-yrkesroll-expansion.md` — full role-by-role gap analysis, Top 20 ranking, coverage gaps
- `docs/specs/2026-05-09-master-execution-plan.md` — orchestrating master plan (this spec inserts new phases 6, 8, 9, 10)
- `docs/specs/2026-05-09-multi-session-coordination.md` — sectional ownership protocol; ROS2 + bridge handlers go in `tool_executor.py` controller-logic section
- `docs/qa/tasks/F-02.md`, `docs/qa/tasks/F-08.md` — existing OPC-UA/MQTT honesty-pattern templates that M3 promotes to runnable
- `docs/qa/scenarios/conveyor_pick_place.md` — overlap reference for M2 PLC-in-loop conveyor scene
- NVIDIA ROS 2 + MoveIt2 tutorial: https://developer.nvidia.com/blog/create-realistic-robotics-simulations-with-ros-2-moveit-and-nvidia-isaac-sim/
- topic_based_hardware_interfaces: https://control.ros.org/master/doc/topic_based_hardware_interfaces/doc/index.html
- isaac_ros_cumotion_moveit: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_cumotion/isaac_ros_cumotion_moveit/index.html
