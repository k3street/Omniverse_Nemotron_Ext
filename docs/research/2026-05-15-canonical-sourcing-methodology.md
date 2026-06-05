# Canonical Sourcing Methodology — 2026-05-15

**Backlog file:** `config/canonical_backlog.yaml`
**Entries written:** 100 (30 yrkesroll, 20 industrial, 15 research, 15 rl-training, 10 gr00t-finetune, 10 ros2-bridge)

---

## §1 Sources Mined

| Source tag | What was used | URL |
|---|---|---|
| `ONET` | SOC codes 51-2028 (assembler), 51-2091 (palletizer), 51-3093 (machine tender), 51-4121 (welder), 51-4193 (painter), 53-7051 (forklift), 53-7064 (packer), 49-9071 (maintenance) — occupation descriptions used to define scenario goals | https://www.onetonline.org/ |
| `RT-X` | Open X-Embodiment paper task list (1M+ episodes, 527 skills); specific episode classes: pick_up_sponge, drawer_manipulation, fold_cloth, sweep_into_dustpan | https://arxiv.org/abs/2310.08864 |
| `ManiSkill` | ManiSkill2 benchmark task registry: PickCube-v0, StackCube-v0, PegInsertionSide-v0, TurnFaucet-v0 | https://github.com/haosulab/ManiSkill |
| `RoboHive` | ADROIT suite: door-open, relocate-pen (24-DOF hand tasks) | https://github.com/vikashplus/robohive |
| `RoboArena` | RoCo Challenge AAAI 2026 bimanual assembly tasks | https://arxiv.org/html/2603.15469 |
| `RoboArena` | Isaac Lab-Arena composable benchmark (Object+Scene+Embodiment+Task Lego blocks) | https://github.com/isaac-sim/IsaacLab-Arena |
| `RT-X` | Isaac Lab eval task suite (FrankaCabinet, Humanoid locomotion, CartPole baseline) | https://github.com/isaac-sim/IsaacLabEvalTasks |
| `RoboHive` | VLABench ICCV 2025 long-horizon tasks (pour water) | https://openaccess.thecvf.com/content/ICCV2025/papers/Zhang_VLABench... |
| `industrial-asset` | Prior project research: Q6 pipeline doc §5 Tier-1 list, Q2 field survey, 2026-05-09 yrkesroll-expansion.md | local docs |
| `retrieval-miss` | RL/GR00T/middleware entries from prior-knowledge of Isaac Assist tool registry (no external URL — these fill known tool-coverage gaps) | — |

Honesty note: `retrieval-miss` entries are generated from prior knowledge of the tool registry (`VALID_MOTION_CONTROLLER_NAMES`, `MOTION_PLANNING_TOOL_PREFIXES`) and the existing CP-NEW template set, not from web scraping. They represent workflows the tool registry can serve but no existing template exercises.

---

## §2 Ranking Criteria

**Asset availability locally.** Templates requiring only Franka, UR10, or conveyor primitives (all available locally without Nucleus) are ranked Tier 1 or 2. Templates requiring Carter, Yaskawa, FANUC, G1 humanoid, or Avatar SimReady USDs are Tier 2–3 because these assets are Nucleus-hosted. The Q6 pipeline doc explicitly identifies this as the #1 blocker for yrkesroll expansion: zero CP-NEW templates that required Nucleus-hosted USDs achieved function-gate-verified status in the 2026-05-10 session.

**Market signal (O*NET employment counts + retrieval demand).** ONET SOC codes were checked for employment size in the US. Palletizers (51-2091: ~100k workers), assemblers (51-2028: ~200k), and machine tenders (51-3093: ~300k) represent large pools of workers whose tasks are automation targets. These are ranked Tier 1–2. Niche roles (ergonomic lift-assist, spray-paint surface-following) are Tier 3 because lower market volume makes them lower ROI for early shipping.

**Retrieval gap (no existing canonical covers this pattern).** The ChromaDB library has strong coverage of single-robot Franka pick-place (CP-01..CP-87). It is thin on: multi-rate ROS2, MQTT Sparkplug-B, GR00T finetune workflows, and OXE benchmark task parity. These gaps directly lower retrieval recall for real user queries in those domains, so covering them has outsized embedding-recall benefit relative to adding another Franka pick-place variant.

**Complexity gradient (simple → complex in each category).** Each category contains at least one `simple` or `medium` entry so the cron pipeline can produce early wins (30-60 min estimated agent time) before tackling 240-minute complex entries. The rl-training category has an explicit CartPole sanity check entry (`rl-isaaclab-cartpole-baseline-001`) as its baseline: if CartPole fails, the pipeline is broken, and all higher-complexity RL entries should be blocked.

---

## §3 Distribution Rationale

Final distribution: 30 yrkesroll, 20 industrial, 15 research, 15 rl-training, 10 gr00t-finetune, 10 ros2-bridge = 100 entries.

Yrkesroll is largest because it is the primary differentiation strategy (occupation-grounded scenarios have clear market relevance and O*NET sourcing credibility). Industrial is second-largest because production-cell patterns exercise the most tool-registry surface area per entry. Research benchmarks provide cross-benchmark parity and academic credibility. RL-training and GR00T-finetune are smaller cohorts because they depend on external packages (Isaac Lab, GR00T base checkpoint) that are not confirmed installed. ROS2-bridge is capped at 10 because each entry requires an external service (broker, PLC emulator, ROS2) making function-gate verification environment-dependent.

---

## §4 Top 10 "Ship in First Batch" Recommendations

1. `yrkesroll-inspector-reject-divert-001` — Tier-1, Franka-only, exercises sort pattern not yet in library; smoke-test foundation (CP-NEW-inspect-reject) already exists.
2. `yrkesroll-kit-prep-operator-001` — Tier-1, Franka-only, multi-source pick_place gap in library, ONET 51-2098 market signal.
3. `yrkesroll-3station-oee-cell-001` — Tier-1, Franka-only, CP-NEW-3station-oee smoke-test ✓ 1/1 (90s); adds metric-emission pattern not yet in library.
4. `yrkesroll-y-merge-singulation-001` — Tier-1, conveyor-primitive-only, CP-NEW-y-merge-singulation smoke-test ✓ 1/1 (53s); covers conveyor merge geometry.
5. `research-maniskill-pick-cube-001` — Tier-1, Franka-only, simplest ManiSkill parity check; 30 min estimated; validates cross-benchmark retrieval.
6. `yrkesroll-controller-benchmark-shootout-001` — Tier-1, Franka-only, CP-NEW-controller-shootout-cp smoke-test ✓ 1/1; fills controller-comparison gap.
7. `yrkesroll-multi-cam-triangulation-001` — Tier-1, no asset dependency, CP-NEW-multi-cam-triangulation smoke-test ✓ 1/1 (21s); fastest to verify.
8. `groot-finetune-n10-demos-001` — Tier-1, GR00T finetuning is a top user request per retrieval-miss signal; exercises the most GR00T tool calls per entry.
9. `ros2-bridge-setup-franka-001` — Tier-1, simple, Franka+ROS2 only; foundational bridge pattern required before any other ros2-bridge entry can build on it.
10. `rl-eureka-reward-pick-place-001` — Tier-1, Franka-only, exercises full Eureka reward loop which has no existing canonical; high retrieval-miss signal.

---

## §5 Known Blockers

**Nucleus-only assets (12 entries affected):**
`yrkesroll-welder-mig-tack-001`, `yrkesroll-welder-seam-track-002`, `yrkesroll-paint-sprayer-trajectory-001`, `yrkesroll-ergonomics-lift-assist-001`, `yrkesroll-forklift-amr-pallet-001`, `yrkesroll-multi-amr-corridor-001`, `industrial-forklift-handoff-arm-001`, `industrial-occupancy-map-nav-001`, `ros2-nav2-integration-001`, `research-robohive-door-open-001`, `research-robohive-relocate-pen-001`, `research-isaaclab-humanoid-locomotion-001`, `rl-eureka-locomotion-reward-001`, `rl-loco-manip-whole-body-001`.
These require Yaskawa GP25, FANUC M710, Carter, G1, or Avatar SimReady USDs hosted on NVIDIA Nucleus. No workaround until Nucleus enterprise access is confirmed or Phase 78c mock-fallbacks land.

**Physics instability (9 entries affected):**
`yrkesroll-assembler-peg-bushing-001`, `yrkesroll-assembler-snap-fit-002`, `yrkesroll-packer-box-seal-001`, `yrkesroll-deformable-bag-place-001`, `yrkesroll-welder-seam-track-002`, `industrial-liquid-pouring-tilt-001`, `research-maniskill-peg-insertion-001`, `research-rtx-drawer-open-close-001`, `research-rtx-fold-cloth-001`, `research-vlabench-pour-water-001`, `research-oxe-sweep-into-dustpan-001`.
These involve contact-rich manipulation (peg-in-hole, deformable meshes, liquid pouring) where PhysX numerical explosion is a documented stable_fail pattern. Blocked until Phase 78b contact-aware planner or convex-hull fix is applied.

**External package dependencies:**
- `isaac_lab_install_required`: 5 entries need `IsaacLab` Python package installed locally.
- `rsl_rl_not_installed`: 1 entry needs RSL-RL package.
- `hardware_spacemouse`: 1 entry needs physical SpaceMouse device for teleop recording.
- `data_dependency_real_rosbag`: 2 entries need a real recorded rosbag file.

---

## §6 What Is Deferred to Phase 2

**G1 humanoid whole-body tasks** — 3 entries in backlog are queued but Nucleus-blocked. A Phase 2 batch should run after Nucleus access is confirmed.

**RLBench benchmark parity** — RLBench (https://github.com/stepjam/RLBench) has 100+ tasks not covered here. Deferred because RLBench requires CoppeliaSim as backend, making direct Isaac Sim mapping non-trivial.

**Agricultural / outdoor scenes** — OXE has outdoor manipulation episodes (BerkeleyBridge, etc.). Deferred: no outdoor USD assets available locally, and outdoor physics (wind, terrain) is out of scope for current Kit session budget.

**Dexterous hand tasks (ADROIT full suite)** — only 2 ADROIT tasks included here. The full ADROIT suite (hammer, relocate, pen, door) requires dexterous hand USD + 24-DOF control not yet exercised in Isaac Assist. Deferred until Shadow Hand or Allegro USD is available.

**Legged robot locomotion curricula** — excluded from this batch because they require Isaac Lab + G1/H1 USD. A dedicated locomotion backlog should be authored after those dependencies are confirmed.

**Video-derived canonicals (Source C in Q6 doc)** — CadCreator video-to-toolcall pipeline is ~6-12 months from production use. Excluded entirely from this batch.

**400+ additional yrkesroll expansion** — the 30 yrkesroll entries here cover the highest-priority roles from the O*NET robotics-relevant occupations list. ~70 additional roles (food processing, pharmaceutical, semiconductor, automotive) exist in O*NET but were not prioritized due to asset uncertainty and lower retrieval-demand signal. Target for Phase 2 batch after function-gate pipeline is stable.
