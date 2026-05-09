# ROS2 / OpenPLC integration — strategic assessment

**Date:** 2026-05-09
**Status:** translated to spec → `docs/specs/2026-05-09-industrial-expansion-spec.md` (Phases 6, 8, 9, 10 of master plan).
**Question (paraphrased):** Should we deepen ROS2 / OpenPLC support in Isaac Assist, or are the current Python controllers (cuRobo / RmpFlow / native PickPlaceController / spline / OSC / DiffIK) "interchangeable enough" that industrial users can self-bridge?

**TL;DR:** ROS2 support is already substantial — far further along than memory or the user expected. **Industry uses Isaac Sim mostly through ROS2-bridge + topic_based_ros2_control + an external MoveIt2/cuMotion stack, NOT through our in-Kit Python controllers.** OpenPLC specifically is a hobby/teaching tool — the wedge an industrial customer cares about is **OPC-UA / Modbus-TCP / Sparkplug-MQTT** to a *real* PLC (Siemens TIA, Beckhoff TwinCAT, Rockwell Studio 5000) for **virtual commissioning**. The right move is not "add OpenPLC" — it is to (a) consolidate the existing-but-fragmented ROS2 surface into a *first-class direct-eval target*, (b) add a thin **Modbus-TCP I/O brick** that handles 80% of PLC integration cases, and (c) add an **OPC-UA bridge primitive** that mirrors the F-02 hand-rolled pattern. Build OpenPLC support only as a free byproduct of Modbus-TCP — not as a goal.

---

## 1. Current-state assessment — we have more than expected

Searching the repo turned up an unexpectedly mature ROS2 surface, badly underdocumented in PLAN.md / README.md.

### 1a. Live, executing, end-to-end-real ROS2 surface

- **`service/.../tools/ros_mcp_tools.py`** (485 LOC) — full async wrapper around `ros-mcp` WebSocketManager. Implements `ros2_connect`, `ros2_list_topics / list_services / list_nodes`, `ros2_get_topic_type / get_message_type / get_node_details`, `ros2_publish`, `ros2_publish_sequence`, `ros2_subscribe_once`, `ros2_call_service`. These are **real handlers, not stubs** — they speak rosbridge over WebSocket at `ROSBRIDGE_HOST:ROSBRIDGE_PORT` (default 127.0.0.1:9090).
- **OmniGraph ROS2 bridge code-gen** in `tool_executor.py`:
  - `setup_ros2_bridge(profile, robot_path)` with 4 profiles: `ur10e_moveit2`, `jetbot_nav2`, `franka_moveit2`, `amr_full`. Generates a complete OmniGraph using `isaacsim.ros2.bridge.*` nodes (Context / PublishJointState / SubscribeJointState / PublishTF / DifferentialController etc.).
  - `setup_pick_place_ros2_bridge` — wires `/isaac/robot/joint_states`, `/isaac/clock`, subscribes `/isaac/robot/target_twist`. Used as the digital-twin / HIL alternative to the Python controllers.
  - `configure_ros2_bridge`, `configure_ros2_time`, `diagnose_ros2`, `fix_ros2_qos`, `show_tf_tree`, `check_tf_health`, hardware-error handling for `AMENT_PREFIX_PATH` not set.
- **`target_source="ros2_cmd"`** is a first-class mode of `setup_pick_place_controller` (alongside `cube_tracking`, `sensor_gated`, `fixed_poses`, `native`, `spline`, `curobo`, `diffik`, `osc`). `_gen_pick_place_ros2_cmd` wires the OmniGraph stub. Listed in `_CONTROLLER_METADATA` with use-case-fit `["digital_twin", "plc_in_loop", "external_moveit"]`.
- **8 ROS2 OmniGraph templates** in `create_graph` — clock, joint_state, camera, lidar, cmd_vel, tf, imu, odom — each idempotent and ready to wire.
- **15 of 298 canonical templates** explicitly reference ROS2 (D-05/09/11/12/14, S-01/02/07/09/10/11/12, E-07, F-02 bridges to ROS2/OPC-UA).

### 1b. What is *not* there

- **Zero OpenPLC mentions** anywhere in the repo. Zero IEC-61131-3 mentions. Zero ladder-logic awareness.
- **OPC-UA is mentioned in F-02 / F-08 as a pattern only** (asyncua client → write USD attrs); no executor tools, no schemas, no ready-made bridge process. F-02 explicitly tells the LLM "say honestly there is no turnkey OPC-UA → USD extension."
- **Modbus-TCP is mentioned only inside `robotics_lab` welding-cell config (gitignored)** — no first-class tool, no schema.
- **Sparkplug-MQTT** is mentioned in F-08 thoughts but has no tool surface.
- **`PLAN.md` 4B / 8F** mark ROS2 as `Schema only` / handlers `None` — **outdated**: the handlers exist (`ros_mcp_tools.py`) and code-gen exists. PLAN.md needs an update pass.
- **No CP-* template uses `ros2_cmd` as default architecture.** The 86 CPs all use in-Kit Python controllers. ROS2 is treated as an alt-mode for the digital-twin persona, not the primary surface.

**Bottom line:** ROS2 is plumbing-complete, evidence-eval-incomplete. We have not measured how often the agent picks ROS2 modes correctly under direct_eval, because no canonical *requires* it as the primary architecture.

---

## 2. How industry actually uses Isaac Sim

Based on first-party NVIDIA documentation, MoveIt docs, BMW / FANUC / ABB / Siemens / Beckhoff product literature, and academic virtual-commissioning papers.

### 2a. The dominant pattern: topic_based_ros2_control + MoveIt2

This is the canonical Isaac Sim consumption pattern that almost every public tutorial and case study converges on:

- Isaac Sim publishes `/isaac_joint_states`, subscribes `/isaac_joint_commands` over the `isaacsim.ros2.bridge` OmniGraph nodes.
- A separate ROS2 process loads `topic_based_ros2_control` (a `ros2_control` hardware plugin from the `topic_based_hardware_interfaces` package) configured with those two topics — it presents Isaac Sim *as if it were a physical robot* to any ros2_control-aware controller.
- MoveIt2 (or any other ros2_control client) plans against this virtual hardware exactly the same way it would against a real arm.
- For motion planning, NVIDIA's `isaac_ros_cumotion_moveit` package replaces the default MoveIt OMPL planner with cuRobo running outside Kit. The agent's perspective: same `/move_group/plan` action, faster planner.

**Implication for us:** the user-perceived integration unit is *not* "controller X vs controller Y inside Kit." It is "wire Isaac Sim into ros2_control + MoveIt2 + cuMotion." The Kit-internal controllers (RmpFlow / curobo / native PickPlaceController) are only used by NVIDIA's *demo* code; production users move that logic outside Kit.

### 2b. Virtual commissioning: PLC + simulator, not PLC inside simulator

The BMW / Siemens / Beckhoff / Foxconn pattern is consistent:

- **BMW iFACTORY (Debrecen, Munich)**: NVIDIA Omniverse hosts the visual + physics layer; *real* PLCs (Siemens S7-1500 / Allen-Bradley) execute the *real* PLC code; data goes through TCP/IP, OPC-UA, or MQTT. Validated PLC code is then deployed to physical floor unchanged.
- **Siemens Tecnomatix Connector for Omniverse / Process Simulate**: live bidirectional sync; Process Simulate / Plant Simulation owns the discrete-event logic, Omniverse owns the rendering + physics check.
- **Beckhoff TwinCAT 3 + TF6100 OPC-UA Server / TS6100 OPC-UA Server**: TwinCAT is the controller; the digital twin (RoboDK, Visual Components, machineering iPhysics, increasingly Isaac Sim via custom bridges) is on the other side of OPC-UA or ADS.
- **FANUC + NVIDIA (Dec 2025)**: FANUC's entire robot lineup ships as OpenUSD SimReady assets; the PLC / FANUC controller logic stays where it is; Isaac Sim hosts the digital twin.
- **University research (MDPI 2024 / 2025)**: virtual commissioning of linked cells uses Omniverse + real PLC + OPC-UA / TCP/IP; nobody is running PLC code *inside* the simulator.

**Implication for us:** customers are not asking "can I run my PLC code in Isaac Assist?" — they are asking "can Isaac Assist's scene talk to my Beckhoff / Siemens / Rockwell PLC over OPC-UA or Modbus while it runs?" That is a *protocol bridge* problem, not a *PLC-runtime* problem.

### 2c. OpenPLC's actual industrial niche

OpenPLC is an open-source IEC-61131-3 runtime that exposes Modbus-TCP for I/O. Real-world usage:

- **Education** — university labs that can't afford a S7-1200 license run OpenPLC + Factory I/O over Modbus-TCP. Practical for teaching ladder, ST, FBD.
- **Cybersecurity research** — the "Virtual Industrial Cybersecurity Lab" series, ICS-CERT-style honeypots; OpenPLC is the cheapest plausible target.
- **Hobbyist / Maker** — Raspberry Pi industrial automation, home-lab conveyor demos.
- **Light commercial** — small OEMs that need IEC-61131 semantics on a Linux box, accepting that it's not safety-rated.

**OpenPLC is not what BMW / Siemens / Foxconn / John Deere customers run.** They run Siemens S7 / Beckhoff CX / Rockwell ControlLogix / Mitsubishi MELSEC. What OpenPLC and the big-vendor PLCs *share* is **Modbus-TCP** and **OPC-UA** — and that is the real interface contract.

### 2d. Concrete industry case studies (verified)

| Org | Pattern | Source |
|---|---|---|
| BMW Group (Debrecen, Munich) | Omniverse hosts twin, real PLC on TCP/IP/OPC-UA/MQTT, validated PLC code deployed unchanged | press.bmwgroup.com (T0411467EN), nvidia.com BMW case studies |
| Foxconn / Fii | Omniverse Mega blueprint, large-scale fleet sim before line build-out | blogs.nvidia.com (Mega Omniverse blueprint, Foxconn) |
| FANUC | OpenUSD SimReady assets in Isaac Sim, Physical AI partnership Dec 2025 | fanuc.co.jp 202512_robot_physicalai |
| Siemens | Tecnomatix Connector + TIA Portal + PLCSIM Advanced; bidirectional sync | plm.sw.siemens.com Tecnomatix Connector for Omniverse |
| Beckhoff | TwinCAT 3 + TF6100 OPC-UA Server bridges into third-party twins (RoboDK, machineering iPhysics, increasingly Isaac Sim via custom bridges) | infosys.beckhoff.com TF6100 |
| ABB | OPC-UA + MQTT IoT Gateway; ABB IRB 2600 Digital Twin via MQTT | new.abb.com/products/robotics/controllers/opc-ua |

---

## 3. Are our controllers interchangeable? (audit)

**Inside Kit, yes — abstraction is good.** `setup_pick_place_controller(target_source=...)` dispatches to 9 implementations behind the same I/O contract: `robot_path`, `source_paths`, `destination_path`, `pick_target` / `drop_target`, `ee_offset`, optional `sensor_path` / `belt_path`. `_resolve_auto_target_source` even probes hardware (CUDA, Volta+, Isaac Lab) and picks the best available. Switching from `curobo` to `native` to `spline` is a one-arg change. **This abstraction is mature.**

**Outside Kit, no — there is a hole between "Python controller" and "ROS2 + ros2_control + MoveIt2."** Our `ros2_cmd` mode subscribes to `/isaac/robot/target_pose` and `/isaac/robot/gripper_cmd` — but the rest of the ROS2 ecosystem expects `/isaac_joint_states` ↔ `/isaac_joint_commands` (the topic_based_ros2_control convention). They are not compatible without a translator. A user pointing MoveIt2 at our `ros2_cmd` setup will see no /joint_command topic and conclude we're broken — when really we just chose different topic names.

**Specifically missing for plug-and-play with external MoveIt2:**
1. A `setup_ros2_control_compat` mode (or option flag on `ros2_cmd`) that publishes `/isaac_joint_states` and subscribes `/isaac_joint_commands` per the `topic_based_ros2_control` standard.
2. A docs note + tool that emits the matching ros2_control YAML (`controller_manager`, `joint_state_broadcaster`, `joint_trajectory_controller` with `topic_based_ros2_control/TopicBasedSystem` plugin) that the user runs *outside* Kit.
3. A tool that launches a colocated rosbridge_server check (we already require it) AND verifies AMENT_PREFIX_PATH visible to Kit before promising the bridge will work. We already have the AMENT_PREFIX_PATH guard in `_gen_setup_ros2_bridge`; promote it to a precheck tool.

---

## 4. Top 3 integration plays — ranked by ROI

### Play A — "ROS2 production parity" (highest ROI, lowest novelty risk)

**What:** elevate the existing ROS2 surface from "plumbing exists" to "first-class direct-eval target." Two concrete moves:

1. **Add `setup_ros2_control_compat` tool** wrapping the existing `setup_ros2_bridge` profiles with the **standard topic names** `/isaac_joint_states` and `/isaac_joint_commands`. Emit the matching YAML so the user can `colcon build && ros2 launch`. Maps 1:1 to the de-facto MoveIt2 + Isaac Sim tutorial.
2. **Promote 3-5 of the existing CP scenarios to a `*-ros2` variant** that uses `target_source="ros2_cmd"` + an external `topic_based_ros2_control` controller. CP-01 (Franka pick-place), CP-30 (UR10 pick-place), and a Carter mobile scenario are obvious candidates. Score them in direct_eval the same way we score in-Kit modes. This is what produces the Phoronix-style answer to *"are controllers interchangeable for industrial users?"*

**Why high ROI:** zero greenfield development — the OmniGraph nodes, the rosbridge wrapper, and the controller-mode dispatch all exist. We are documenting + repackaging + adding a single shape to direct_eval.

**Effort:** small. Risk: low.

### Play B — "OPC-UA / Modbus-TCP I/O bricks" (medium ROI, opens new persona)

**What:** add three first-class I/O-bridge tools that mirror the F-02 hand-rolled pattern but as canonical primitives:

1. **`opcua_bridge_attach(server_url, tag_to_attribute_map, rate_hz)`** — runs an `asyncua` client in a Kit-side thread, polls tags at `rate_hz`, writes to USD attributes (belt-speed, light-emissive, kinematic translate). Mirrors F-02's pattern but turnkey.
2. **`modbus_tcp_bridge_attach(server_host, port, register_map, rate_hz)`** — `pymodbus` server *or* client, configurable. The same primitive that the Shirokuma blog uses for OnRobot 2FG7 gripper control. Bidirectional: holding registers ↔ joint targets, input registers ← simulated sensor states.
3. **`mqtt_sparkplug_bridge_attach(broker, group_id, edge_node_id, metric_map)`** — same shape, Sparkplug B encoded; this is the protocol the GXO / KION warehouse blueprint runs on.

Each tool produces a **process-supervised bridge** (subprocess inside Kit's lifetime, or a sidecar via `dispatch_async_task`). Health-check via `diagnose_*_bridge` tools. Honest-mode error if Kit cannot reach the server.

**Why medium ROI:** opens the F-* persona (Fatima, simulation engineer, virtual-commissioning) and the K-* persona (CAD-to-USD with PLC-in-loop verification). Maps directly onto BMW / Siemens / Beckhoff workflows. Six new CPs immediately viable: PLC-in-the-loop conveyor (already scoped in `2026-05-09-yrkesroll-expansion.md` line 89), CAD-revision-drift, CIP/GMP-style audit trail, multi-station OPC-UA OEE, MQTT-Sparkplug AMR fleet, S7-1500 PLCSIM-Advanced HIL.

**Effort:** medium — three Python libraries (asyncua, pymodbus, paho-mqtt + tahu), three tool schemas, three handler implementations, three diagnose tools, six CP templates. Risk: medium — async lifecycle inside Kit needs care (we already learned the lesson via the silent-success audit; supervised subprocess is safer than in-Kit threads).

### Play C — "OpenPLC sandbox" (low ROI as a goal; free byproduct of B)

**What:** if Play B exists, OpenPLC support is an `openplc_runtime_attach(runtime_url, st_program, modbus_register_map)` convenience tool that assumes the existing Modbus-TCP bridge — about 50 LOC and one CP template ("teach-PLC: Franka picks while OpenPLC ladder gates the conveyor").

**Why low ROI as a primary goal:** OpenPLC's *audience* (educators, hobbyists, ICS-cybersec) is not the audience our Yrkesroll-expansion roadmap targets. The five top-priority new canonicals (G1 bimanual, RL clone-env, AMR handoff, drawer, peg-insert) are unrelated. Industrial customers do not deploy OpenPLC into production.

**When to build it:** as a **demo-friendly free** byproduct of Play B. Single tool, single CP, ~half a day of work. Skip if Play B isn't built.

---

## 5. Specific tool additions — ranked

| Priority | Tool | Why |
|---|---|---|
| **P0** | `setup_ros2_control_compat(robot_path, joint_states_topic="/isaac_joint_states", joint_commands_topic="/isaac_joint_commands")` | Closes the "MoveIt2 plug-and-play" gap. Standard topic names. Existing OmniGraph profile, just rebadged. |
| **P0** | `emit_ros2_control_yaml(robot_path, controller_type="joint_trajectory_controller")` | Generates the YAML the user runs *outside* Kit. Closes the gap by giving them both halves. |
| **P0** | `precheck_ros2_environment()` | Verifies AMENT_PREFIX_PATH, rosbridge port, ROS_DOMAIN_ID consistency. Honest failure before expensive scene build. |
| **P1** | `opcua_bridge_attach(server_url, tag_to_attribute_map, rate_hz=1)` | F-02 pattern, turnkey. Maps onto BMW / Siemens / Beckhoff. |
| **P1** | `modbus_tcp_bridge_attach(host, port, register_map, mode="server"|"client", rate_hz=10)` | Covers OpenPLC, FactoryIO, OnRobot grippers, most light-industrial PLCs. Lowest-effort highest-coverage protocol bridge. |
| **P2** | `mqtt_sparkplug_bridge_attach(broker, group_id, edge_node_id, metric_map)` | KION / GXO / ABB warehouse pattern. Less common than OPC-UA but unblocks the warehouse-twin persona. |
| **P2** | `diagnose_modbus_bridge(host, port)` / `diagnose_opcua_bridge(server_url)` | Honesty pair to the bridge primitives. |
| **P3** | `openplc_runtime_attach(runtime_url, st_program=None, register_map=None)` | Convenience wrapper over Modbus-TCP. Only build after P1 ships. |
| **P3** | `setup_isaac_ros_cumotion_moveit(robot_path, planner_topic="/move_group/plan")` | NVIDIA Isaac ROS cuMotion-as-MoveIt-planner. Unblocks the "production cuRobo via MoveIt2" path that several R-* and S-* canonicals imply. |

---

## 6. What we should NOT bother with

- **Embedding an IEC-61131-3 runtime inside Kit.** That is what OpenPLC already is, and that is not what industrial customers run; replicating it adds attack surface (Kit crashes drag the PLC down) and zero customer value. Bridge to it as an external process if needed.
- **A full PLCopen / TwinCAT / TIA-Portal connector.** Siemens already ships Tecnomatix Connector + Process Simulate. Beckhoff already ships TF6100. Re-implementing those is months of work with no advantage; we should *talk OPC-UA / ADS to them*, not replace them.
- **Implementing safety-rated PLC behavior.** Isaac Sim is not safety-rated and explicitly should not sit on a control path (F-08 says this). Honesty-tier templates already enforce this — do not weaken.
- **Sparkplug as a P0.** Lower density of customer demand than OPC-UA + Modbus combined; build OPC-UA first and Sparkplug later.
- **An "Isaac Assist proprietary protocol."** The dimension we win on is matching the *existing* protocols (OPC-UA, Modbus, ROS2). Any tool whose name starts with `isaac_` and ends with `_protocol` is a yellow flag — we should be the *adapter*, not the *standard*.
- **Surgical / agriculture verticals on the back of this work.** Out of scope; per the role expansion doc, those stay parked.

---

## 7. Concrete next 5 milestones if user picks this direction

Sequenced for delivery, each ends in a measurable artefact (test or direct-eval pass).

1. **M1 — Audit + repackage existing ROS2 surface (P0 only).** Update `PLAN.md` 4B / 8F / 9E to reflect that ROS2 handlers exist. Add `setup_ros2_control_compat`, `emit_ros2_control_yaml`, `precheck_ros2_environment`. Add one new CP (`CP-87-ros2-moveit2-franka-pickplace`) using `target_source="ros2_cmd"` + the new compat layer + an external `topic_based_ros2_control` test harness. **Done when:** direct_eval scores ≥ 4/5 on CP-87 against a live `ros2 launch` test rig.

2. **M2 — `modbus_tcp_bridge_attach` primitive + 1 CP.** Implement the pymodbus-based bridge as a supervised subprocess. Build CP-NEW: PLC-in-the-loop conveyor (already scoped in `2026-05-09-yrkesroll-expansion.md`). Wire to OpenPLC as the test PLC; write the integration test that runs OpenPLC under Docker and exercises start/stop/jam/reset over Modbus. **Done when:** the conveyor pauses within 100ms of a Modbus coil flip and resumes within 100ms of clear. Direct-eval scores ≥ 4/5.

3. **M3 — `opcua_bridge_attach` primitive + F-02 promotion.** Implement asyncua bridge. Promote F-02 from "explain the pattern" template to a *runnable* canonical that actually drives 12 conveyor states. Reuse the same supervised-subprocess infrastructure as M2. **Done when:** F-02 scores 5/5 in a direct_eval against a Python-hosted mock OPC-UA server.

4. **M4 — `setup_isaac_ros_cumotion_moveit` + R-* / S-* sweep.** Add the cuMotion-as-MoveIt-planner tool. Re-run S-01, S-02, S-09, S-11 in `ros2_cmd + cumotion` mode and compare scores to the baseline `curobo` mode. **Done when:** the comparison artefact (the "controller shootout" already prioritized #12 in the role-expansion doc) is generated, scored, and lives in `workspace/scenario_results/controller_shootout_ros2.json`.

5. **M5 — OpenPLC + MQTT-Sparkplug as opportunistic adds.** `openplc_runtime_attach` (P3) over the existing Modbus bridge — demo-friendly, half a day. `mqtt_sparkplug_bridge_attach` (P2) for the warehouse-twin persona — a couple of days. Each ships with one new CP. **Done when:** demo videos exist for both and one persona scenario passes in each direction.

---

## 8. Recommendation summary

- **The existing controllers ARE interchangeable internally** — `target_source` swap is a one-arg change across 9 implementations.
- **They are NOT interchangeable externally** — our `ros2_cmd` topic names diverge from the de-facto `topic_based_ros2_control` convention. **Fix this in M1.**
- **OpenPLC is a tactical add, not a strategic one.** Build Modbus-TCP first; OpenPLC follows for free.
- **The strategic wedge is OPC-UA + Modbus + ROS2-control compat** — that is what unlocks Fatima (F-*), Karim (K-*), and Magnus (M-*) personas, and what BMW / Siemens / FANUC / ABB / Beckhoff customers actually buy.
- **Effort is mostly in repackaging what exists** (M1 + M3-promoted F-02), not in greenfield code. The two genuinely-new bricks are M2 (Modbus) and M3 (OPC-UA), each ~1-2 days for the primitive and ~1 day for the test rig.

If only one play happens: **M1 alone**. It elevates production ROS2 use from "documented possible" to "scored as a first-class architecture in direct_eval," and it costs almost nothing because the plumbing already exists.

---

## Sources

- [Isaac Sim ROS 2 Bridge documentation](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_ros.html)
- [Create Realistic Robotics Simulations with ROS 2 MoveIt and NVIDIA Isaac Sim — NVIDIA Developer Blog](https://developer.nvidia.com/blog/create-realistic-robotics-simulations-with-ros-2-moveit-and-nvidia-isaac-sim/)
- [MoveIt2 — How To Command Simulated Isaac Robot tutorial](https://moveit.picknik.ai/main/doc/how_to_guides/isaac_panda/isaac_panda_tutorial.html)
- [topic_based_hardware_interfaces — ROS2_Control documentation](https://control.ros.org/master/doc/topic_based_hardware_interfaces/doc/index.html)
- [isaac_ros_cumotion_moveit](https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_cumotion/isaac_ros_cumotion_moveit/index.html)
- [Modbus TCP Isaac Sim Gripper Control (Shirokuma)](https://shirokuma.online/en/blog/2026-03-26_Modbus-TCP-Isaac-Sim-Gripper-Control)
- [Setting up OpenPLC with FactoryIO (Joseph Gardiner)](https://www.josephgardiner.com/setting-up-openplc-with-factoryio/)
- [Virtual Industrial Cybersecurity Lab Part 4 (Cantera)](https://rodrigocantera.com/en/virtual-industrial-cybersecurity-lab-part-4-program-the-control-logic/)
- [BMW Group scales Virtual Factory with Omniverse](https://www.press.bmwgroup.com/global/article/detail/T0450699EN/bmw-group-scales-virtual-factory)
- [BMW Plant Debrecen virtual production demo with NVIDIA](https://www.press.bmwgroup.com/global/article/detail/T0411467EN/bmw-group-at-nvidia-gtc:-virtual-production-under-way-in-future-plant-debrecen)
- [Paving the Future of Factories with NVIDIA Omniverse Enterprise (BMW case study)](https://www.nvidia.com/en-us/case-studies/paving-the-future-of-factories-with-nvidia-omniverse-enterprise/)
- [Virtual Commissioning of Linked Cells Using Digital Models (MDPI 2024)](https://www.mdpi.com/2673-4052/5/1/1)
- [Tecnomatix Connector for NVIDIA Omniverse (Siemens)](https://plm.sw.siemens.com/en-US/tecnomatix/products/tecnomatix-connector-nvidia-omniverse/)
- [Siemens / NVIDIA partnership announcement](https://blogs.sw.siemens.com/xcelerator/2023/10/11/siemens-xcelerator-and-nvidia-omniverse-enable-the-industrial-metaverse/)
- [Beckhoff TF6100 OPC-UA Server](https://infosys.beckhoff.com/content/1033/tf6100_tc3_opcua_server/15618696331.html)
- [FANUC + NVIDIA Physical AI partnership Dec 2025](https://www.fanuc.co.jp/en/product/new_product/2025/202512_robot_physicalai.html)
- [ABB OPC-UA / MQTT IoT Gateway for robotics](https://new.abb.com/products/robotics/controllers/opc-ua)
- [Internal: docs/research/2026-05-07/agents/omnigraph_ros2.md](2026-05-07/agents/omnigraph_ros2.md)
- [Internal: docs/research/2026-05-09-yrkesroll-expansion.md](2026-05-09-yrkesroll-expansion.md)
- [Internal: docs/qa/scenarios/conveyor_pick_place.md](../qa/scenarios/conveyor_pick_place.md)
- [Internal: docs/qa/tasks/F-02.md, F-08.md](../qa/tasks/F-02.md)
