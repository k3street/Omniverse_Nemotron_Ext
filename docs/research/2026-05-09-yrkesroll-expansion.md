# Yrkesroll-driven canonical expansion — research & roadmap input

**Date:** 2026-05-09
**Status:** translated to spec → `docs/specs/2026-05-09-industrial-expansion-spec.md` (Top-5 → Phase 8, Top 6-15 → Phase 9, Top 16-20 → Phase 10).
**Scope:** Identify gaps in the canonical template library (currently 86 CPs + 197 other-prefix templates) when measured against the real workflows of eight professional-role personas using Isaac Sim today. This is research input to a roadmap, not a roadmap itself.

## Library audit — what already covers which role?

The 298 templates in `workspace/templates/` already cover several roles incidentally. Mapping by prefix:

| Prefix | Count | Implicit role |
|---|---:|---|
| **CP** | 86 | Manipulation engineer / line designer (pickplace surface) |
| **A / AM / J** | 32 | Educator / hobbyist (entry-level) |
| **AD** | 23 | QA engineer (adversarial honesty) |
| **AL** | 10 | Solutions architect (deployment) |
| **D** | 14 | ROS2 / Nav2 sim engineer |
| **E** | 11 | Demo / executive (risk-screening) |
| **F** | 10 | Logistics / warehouse twin engineer |
| **K / L / Y** | 32 | CAD-to-USD / mech-CAD engineer |
| **M / FX / G** | 28 | URDF & physics import engineer |
| **P** | 12 | SDG / dataset engineer |
| **R / S** | 22 | API-migration / ROS-debug engineer |
| **T** | 14 | Safety / standards engineer |

**Key insight:** the library is already broader than CP-* alone suggests. The next question isn't "do we have category X" — it's "are the existing templates of the right *shape* for the role's daily work?" CP-* templates are scene-builders that end in successful function-gate. Most other prefixes are diagnose / honesty / planning shapes. Several roles below need *new shapes*, not just new scenes.

---

## Role 1 — Robotics engineers (R&D, integration)

**Existing coverage:** strong on import (M-*), kinematics fix (FX-*), API migration (R-*). Weak on motion-planning benchmarks, controller comparisons beyond pickplace, contact-rich tasks.

**Web evidence:** Isaac Lab ships Factory tasks (PegInsert, GearMesh, NutThread), AutoMate, FORGE — all force-aware contact-rich assembly that Anthropic's CP library has zero coverage of [Isaac Lab Environments](https://isaac-sim.github.io/IsaacLab/main/source/overview/environments.html). cuRobo benchmarking exists in CP-01..05 but no controller-shootout canonical.

**Gap canonicals:**
1. **CP-NEW: Controller-shootout reach benchmark** — same Franka, same target, run cuRobo / spline / RmpFlow / OSC sequentially, log path-length, jerk, time. A "Phoronix-style" comparison artefact.
2. **CP-NEW: Joint-limit boundary probe** — drive each joint to ±5° from URDF limit, verify limit enforcement, surface PhysX warning if exceeded. Shape: physics health check.
3. **CP-NEW: Self-collision regression suite** — articulation in 8 known-bad poses (folded, wrist-through-elbow), assert PhysX self-collision filter catches all. Maps to FX-* shape.
4. **CP-NEW: cuRobo cold-start hang detection** — first cuRobo invocation after cold CUDA driver hangs ~90s (already noted in CP-01 thoughts). Make it a canonical AD-shape: agent must report hang, not silently wait.
5. **CP-NEW: Singularity navigation test** — UR10 driven through wrist-singular pose; verify warning surface + graceful jacobian-conditioning fallback.
6. **CP-NEW: Peg-in-hole insertion** (Isaac-Factory-PegInsert port) — 5mm clearance, force-threshold-gated APPROACH→ALIGN→INSERT. Already scoped in `2026-05-07/sonnet_industrial_scenarios.md` as CP-08, never built.
7. **CP-NEW: Cable-routing through fixture** — deformable cable + clip pass-through; tests deformable-rigid coupling.
8. **CP-NEW: Drawer / cabinet open** (Isaac-Open-Drawer-Franka port). Articulated environment object. Already flagged as top pick in `2026-05-07/sonnet_isaac_sim_scenes.md`.

---

## Role 2 — Manufacturing engineers (factory automation, line design)

**Existing coverage:** F-* covers warehouse twin layout. CP-* covers cell-level pick-place patterns (sortation, palletizing, kitting). Weak on **line-balancing**, OEE, throughput, cycle-time variance, multi-station handoff at scale.

**Web evidence:** [BMW Omniverse case study](https://www.nvidia.com/en-us/case-studies/paving-the-future-of-factories-with-nvidia-omniverse-enterprise/), [Foxconn Fii Digital Twin](https://blogs.nvidia.com/blog/omniverse-digital-twins-taiwan-manufacturers-physical-ai/), [Mega Omniverse Blueprint](https://blogs.nvidia.com/blog/mega-omniverse-blueprint/). Manufacturing engineers want **discrete-event-style metrics** (OEE, bottleneck identification, line balancing) baked into the simulation, not just "did the pick succeed". [Autodesk on DES bottlenecks](https://www.autodesk.com/blogs/design-and-manufacturing/discrete-event-simulation-to-identify-factory-bottlenecks/).

**Gap canonicals:**
1. **CP-NEW: 3-station serial line with cycle-time logging** — feeder → pick → inspect → place; log per-station idle/busy time, surface bottleneck.
2. **CP-NEW: OEE dashboard scene** — same 3-station line + 60s run + emit JSON {availability, performance, quality} consumable by a downstream metric collector. Shape new: scene + metric writer.
3. **CP-NEW: Line-balancing what-if** — same scene, parameterized cycle-time on station 2, run 30s × 3 settings, compare throughput. Probes parameter sweep tooling.
4. **CP-NEW: Buffer-overflow / starvation pattern** — bounded buffer between two robots; deliberately mis-tune rates; observe overflow vs starvation. Already scoped in research doc, never built.
5. **CP-NEW: Operator-in-loop ergonomics** — animated human worker at one station with reach/pose envelope; assert robot stays out of envelope. Shape: safety + animation.
6. **CP-NEW: Tool-changeover pattern** — robot drops parallel gripper, picks suction. Models real changeover downtime in OEE.
7. **CP-NEW: Quick-change fixture validation** — same robot, two fixture variants on rotary; cycle through both verifying cell still works. Maps to commissioning flow.

---

## Role 3 — ML/RL researchers (sim-to-real, policy training)

**Existing coverage:** P-* covers SDG (10 templates). Zero CP-* templates for **batched RL envs**, no `clone_envs` canonical, no domain-randomization curriculum, no policy export → ONNX → runner. K-* and S-* have no sim-to-real-gap measurement shape.

**Web evidence:** Isaac Lab ships 30+ RL envs; the [GR00T N1.6 sim-to-real workflow](https://developer.nvidia.com/blog/building-generalist-humanoid-capabilities-with-nvidia-isaac-gr00t-n1-6-using-a-sim-to-real-workflow/) is the reference; [Bridging the Sim-to-Real Gap blog](https://developer.nvidia.com/blog/bridging-the-sim-to-real-gap-for-industrial-robotic-assembly-applications-using-nvidia-isaac-lab/) makes domain randomization (joint friction, damping, controller gains, observation noise) the core RL ergonomics. [Isaac Lab-Arena](https://developer.nvidia.com/blog/simplify-generalist-robot-policy-evaluation-in-simulation-with-nvidia-isaac-lab-arena/) is the policy-eval benchmark.

**Gap canonicals:**
1. **CP-NEW: Cloned-env training scaffold** — 64 parallel Franka-reach envs via `clone_envs`, RL-Games or RSL-RL launch, 60s sanity train, reward curve emitted.
2. **CP-NEW: Domain-randomization curriculum** — same Franka, randomize friction / mass / lighting / camera FOV per reset; emit randomization-coverage histogram.
3. **CP-NEW: Policy export → ONNX runner round-trip** — train tiny policy, export ONNX, reload via standalone runner, verify identical action on identical observation.
4. **CP-NEW: Imitation learning from teleop demo** — record 5 demos, replay-train BC policy, eval. Hooks Isaac Lab Mimic.
5. **CP-NEW: Sim-to-real gap measurement** — load real-robot rosbag, replay in sim, compute action / observation delta; AD-tier honesty: agent must NOT claim zero-gap.
6. **CP-NEW: Whole-body G1 loco-manipulation eval** — pre-trained GR00T-style policy on Unitree G1 in kitchen scene; eval against task suite. Maps directly to Kimate's north-star (per `2026-05-07/sonnet_kimate_priorities.md`).
7. **CP-NEW: Reward-curriculum staging** — same env, three reward variants (sparse / shaped / shaped+penalty); compare sample-efficiency.
8. **CP-NEW: Tactile insertion (TacEx)** — peg insertion with GelSight tactile feedback simulation. Cutting-edge cited in [TacEx paper](https://arxiv.org/html/2411.04776v1).

---

## Role 4 — Simulation engineers (digital twin, CAD validation)

**Existing coverage:** K-* (12), L-* (11), Y-* (10), M-* (17) cover STEP / SolidWorks / URDF / MDL import flows. Strong shape coverage. Weak on **virtual commissioning** (PLC-style I/O loop), CAD-version-drift detection, mass-properties round-trip.

**Web evidence:** [PTC Onshape → Isaac Sim CAD-to-OpenUSD bridge](https://nvidianews.nvidia.com/news/nvidia-and-global-industrial-software-giants-bring-design-engineering-and-manufacturing-into-the-ai-era), [FANUC / ABB / YASKAWA / KUKA virtual commissioning](https://blogs.nvidia.com/blog/ai-manufacturing-hannover-messe/), [skill-based engineering feasibility checks](https://link.springer.com/chapter/10.1007/978-3-032-02106-9_46). Simulation engineers want **closed-loop PLC-in-the-loop** simulation + CAD-revision regression.

**Gap canonicals:**
1. **CP-NEW: PLC-in-the-loop fixture** — Isaac Sim conveyor + 4 digital I/O signals (start/stop/jam/reset) bridged to OPC-UA mock; run a 30s cycle.
2. **CP-NEW: CAD revision-drift detection** — load STEP rev A, then rev B; diff bbox / mass / collision-mesh; flag changes >5% as risk.
3. **CP-NEW: Mass-properties round-trip** — assert URDF mass / inertia / CoM survive USD import + URDFExport round-trip within 0.1% tolerance.
4. **CP-NEW: Tolerance-stack reachability check** — 6 candidate fixture poses + 5mm position tolerance per pose; assert IK feasible across full Monte-Carlo (1000 samples).
5. **CP-NEW: Cell-clearance audit** — robot in stow + worst-case extended pose; sweep against fixture; report min clearance vs ISO/TS 15066 PFL targets. Maps to T-* safety shape.
6. **CP-NEW: Multi-CAD assembly validation** — 3 STEP files (gripper from L-01, robot from M-01, fixture from K-01) loaded together; verify no z-fighting, no negative-mass, no overlapping collision.

---

## Role 5 — Quality engineers (inspection, defect detection)

**Existing coverage:** P-* covers SDG annotation pipelines. Zero CP-* covering **inspection station, defect generation, pass/fail decisioning, vision-confidence routing**.

**Web evidence:** [TCS Mobility AI defect-detection synthetic pipelines](https://thinkrobotics.com/blogs/indepths/nvidia-robotics-platform-complete-guide-to-ai-powered-robot-development), [Omniverse Replicator defect-detection blog](https://developer.nvidia.com/blog/how-to-train-a-defect-detection-model-using-synthetic-data-with-nvidia-omniverse-replicator/), [wafer-scratch SDG case study](https://link.springer.com/article/10.1007/s41060-026-01034-8). Quality engineers want a closed loop: synthetic defect generation → train detector → sim deployment → pass/fail.

**Gap canonicals:**
1. **CP-NEW: Defect-introduction SDG** — load asset, randomly add 0..3 surface defects (scratch / dent / discolor) per render, COCO export.
2. **CP-NEW: Inspect-and-reject divert** (already scoped CP-06 in `2026-05-07/sonnet_industrial_scenarios.md`). Build it.
3. **CP-NEW: 3-class quality gate** (pass / rework / reject) — already scoped, build it.
4. **CP-NEW: Vision confidence-threshold routing** — same gate but with rejected-confidence-band → manual-review chute. Tests sensor-confidence as routing input.
5. **CP-NEW: Multi-camera triangulation** — 3 cameras at 120°, fuse for 3D defect localization, report mm-accurate defect coordinate.
6. **CP-NEW: Class-balance enforcement** — SDG run that emits balanced 5-class dataset (1000 each); shape: SDG + post-validation.
7. **CP-NEW: Annotation-stability under DR** — 100 frames, randomized lighting/pose, assert annotation IDs stable (already scoped in P-01).

---

## Role 6 — Logistics / warehouse (beyond pickplace)

**Existing coverage:** F-* covers warehouse twin layout (10 templates). CP-60 has recirculation loop. Weak on **conveyor merging** (Y / T / FORK_MERGE per [Isaac Sim docs](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/digital_twin/warehouse_logistics/ext_isaacsim_asset_gen_conveyor.html)), parcel singulation, AMR fleet routing.

**Web evidence:** [KION + GXO warehouse blueprint](https://www.automatedwarehouseonline.com/mega-framework-in-nvidia-omniverse-to-enable-warehouse-optimization/), [Amazon Proteus AMR](https://www.nextrongroup.com/news/web-news-industry/amr-industry-news), [Isaac AMR platform](https://blogs.nvidia.com/blog/isaac-amr-nova-orin-autonomous-mobile-robots/), [Fraunhofer IML AMR research](https://www.robotics247.com/article/fraunhofer_iml_research_uses_nvidia_isaac_sim_on_omniverse_to_advance_robot_design_with_simulation/warehouse). Logistics is **AMR fleet + conveyor topology** — almost untouched in CP-*.

**Gap canonicals:**
1. **CP-NEW: Y-merge conveyor singulation** — two infeed lanes merge into one via Y-merge; assert non-overlap on merged line via timing.
2. **CP-NEW: Cross-belt sorter (multi-chute)** — 1 in-feed, 8 chutes, barcode-driven routing. Build out CP-60 loop into actual sorter.
3. **CP-NEW: AMR pickup-from-cell handoff** — Nova Carter docks at cell, Franka places into onboard bin, AMR departs. Maps directly to Kimate's Phase 9 priority.
4. **CP-NEW: Multi-AMR collision-avoidance corridor** — 3 AMRs sharing a 2m corridor; assert no collision over 60s.
5. **CP-NEW: Dynamic obstacle avoidance** — AMR navigates while pedestrian (animated) walks across path.
6. **CP-NEW: Warehouse fleet routing (cuOpt)** — 10-pickup-task, 4-AMR fleet; cuOpt routing; assert all 10 completed within deadline.
7. **CP-NEW: Mixed-SKU palletizing column** — already scoped (CP-11 in research doc), build it.
8. **CP-NEW: Parcel singulation from heap** — 10 overlapping parcels in input zone, robot picks one at a time; tests unstructured input.

---

## Role 7 — Surgical / medical robotics

**Existing coverage:** Zero. No T-tier (safety) template targets surgical specifically; T-01 is ISO/TS 15066 PFL only.

**Web evidence:** [NVIDIA Isaac for Healthcare](https://developer.nvidia.com/blog/introducing-nvidia-isaac-for-healthcare-an-ai-powered-medical-robotics-development-platform/), [ORBIT-Surgical](https://orbit-surgical.github.io/) ships dVRK + 12 laparoscopic maneuvers, [Virtual Incision MIRA](https://virtualincision.com/virtual-incision-explores-nvidia-isaac-for-healthcare-in-surgical-robotics/) explores Isaac. Asset catalog + biomechanical sim are first-party.

**Gap canonicals:**
1. **CP-NEW: dVRK needle-pickup** — dVRK PSM in tabletop; pick suturing needle from tray.
2. **CP-NEW: Bimanual needle-handoff** — two PSMs pass needle. Maps to ORBIT-Surgical canonical maneuver.
3. **CP-NEW: Suction-irrigation tool change** — dVRK tool swap mid-task; tests articulated-tool changeover.
4. **CP-NEW: Telesurgery teleop loop** — record dVRK demo via teleop, replay; tests Isaac-for-Healthcare workflow.
5. **CP-NEW: Soft-tissue retraction** — deformable mesh + grasp + retract; pure deformable-rigid contact test.

**Verdict:** medical is a **separate vertical** — recommend a new prefix (e.g. `MED-*`) rather than expanding CP-*. Do *not* invest until Kimate signals demand.

---

## Role 8 — Construction / agriculture robotics

**Existing coverage:** Zero CP-* construction or agriculture.

**Web evidence:** [BrickSim brick-laying simulator](https://arxiv.org/abs/2603.16853) is built on Isaac Sim; agriculture has [zero-shot fruit harvesting RL](https://arxiv.org/html/2505.08458), [strawberry sim2real](https://www.mdpi.com/2624-7402/7/3/81), [occlusion-aware fruit localization](https://arxiv.org/html/2505.16547v1). Both verticals have research traction, neither in CP-* library.

**Gap canonicals:**
1. **CP-NEW: Brick-stacking on baseplate** — Franka picks LEGO-like bricks, snap-fits onto baseplate. Direct BrickSim port.
2. **CP-NEW: Bimanual brick coop assembly** — two Frankas hold + insert one brick into another. BrickSim canonical.
3. **CP-NEW: Excavator scoop cycle** — Komatsu / CAT-style articulated excavator scoops dirt-pile (particle-set); deposits in truck bed.
4. **CP-NEW: Orchard fruit-pick** — UR5 + RealSense + tree-NeRF asset; pick apple. Direct port from FF3D paper.
5. **CP-NEW: Strawberry harvest lane** — mobile platform + manipulator picks strawberries from row.

**Verdict:** construction + agriculture are early-stage but real verticals. Recommend folding into CP-* with sub-prefix (e.g. `CP-AG-*`, `CP-CN-*`) rather than top-level new prefix.

---

## Top 20 highest-priority NEW canonicals across roles

Ranked by (a) Kimate-priority signal from `2026-05-07/sonnet_kimate_priorities.md`, (b) maturity of upstream Isaac Sim asset, (c) gap severity in current library, (d) reusability across roles.

| # | Canonical | Primary role | Justification |
|---:|---|---|---|
| 1 | **G1 bimanual tabletop** | ML/RL | North-star (Inference 1, Kimate priorities doc) |
| 2 | **Cloned-env RL training scaffold (64 envs)** | ML/RL | P0 gap, unblocks GR00T fine-tune |
| 3 | **AMR pickup-from-cell handoff** | Logistics | Phase 9 north-star, no CP-* coverage |
| 4 | **Drawer / cabinet open** | Robotics eng | Top pick from sonnet_isaac_sim_scenes; zero overlap |
| 5 | **Peg-in-hole insertion (Factory port)** | Robotics eng | Force-aware contact gap; Isaac Lab first-party |
| 6 | **3-station serial line + OEE log** | Mfg eng | Closes "no throughput metric" gap |
| 7 | **Inspect-and-reject divert** | Quality | Already scoped, never built |
| 8 | **Defect-introduction SDG** | Quality | Direct match to TCS / Omniverse Replicator blog |
| 9 | **Y-merge conveyor singulation** | Logistics | Ext supports it, no canonical exercises it |
| 10 | **Domain-randomization curriculum** | ML/RL | Standard sim-to-real ergonomic, zero coverage |
| 11 | **Multi-camera triangulation** | Quality | Real factory pattern, sensor-fusion shape gap |
| 12 | **Controller-shootout reach benchmark** | Robotics eng | Decision-support artefact for cuRobo vs spline vs RmpFlow |
| 13 | **PLC-in-the-loop fixture** | Sim eng | Virtual-commissioning is the FANUC/ABB/KUKA wedge |
| 14 | **Sim-to-real gap measurement (rosbag replay)** | ML/RL | AD-tier honesty applied to sim2real |
| 15 | **Cross-belt 8-chute sorter** | Logistics | Builds on CP-60 loop infrastructure |
| 16 | **Multi-AMR corridor collision-avoid** | Logistics | Mega blueprint scenario |
| 17 | **CAD revision-drift detection** | Sim eng | New shape: regression vs prior asset |
| 18 | **Operator-in-loop ergonomics** | Mfg eng | Maps T-* (ISO 10218 / 15066) into a CP scene |
| 19 | **Tactile insertion (TacEx)** | ML/RL | Cutting-edge, low-cost integration |
| 20 | **Brick-stacking on baseplate** | Construction | BrickSim port; new vertical wedge |

---

## Coverage gaps where current library is empty (zero templates)

- **Force-aware / contact-rich** assembly (peg, gear, nut, screw) — Isaac Lab ships, we have zero
- **Articulated environment objects** (drawers, doors, valves) — zero
- **Dexterous in-hand manipulation** (Allegro, Shadow) — zero
- **Quadruped / bipedal locomotion** (Spot, ANYmal, H1, G1, Digit) — zero
- **Aerial / multirotor** (Crazyflie, ARL Robot) — zero (less critical for Kimate's roadmap)
- **Surgical** — zero
- **Construction / brick assembly** — zero
- **Agriculture / orchard / row-crop** — zero
- **Conveyor merge / divert / sortation topology** — zero (CP-60 loop is closest)
- **Multi-AMR fleet routing** — zero
- **PLC / OPC-UA / virtual commissioning** — zero
- **Discrete-event metrics (OEE, cycle-time, throughput logging)** — zero
- **Tactile sensing (GelSight, force-torque)** — zero
- **Operator / human-in-loop ergonomics** — zero (T-* discusses safety but no scene)
- **Tool-changeover** — zero

---

## Recommendation

Build the **Top-5** first (G1 bimanual, RL clone-env, AMR handoff, drawer-open, peg-insert). Each unblocks a different role and a different upstream asset (humanoid / RL / mobile / articulation / contact-rich). Then sweep one canonical per remaining role to anchor the next 15. Construction + surgical stay parked until external signal.

For shape additions, the largest single gap is **discrete-event metrics** (OEE / throughput / cycle-time). Adding a `metrics_emit` shape to canonicals (alongside `verify_args` / `simulate_args`) would convert ~20 existing CP-* into manufacturing-engineer-grade artefacts at near-zero cost.
