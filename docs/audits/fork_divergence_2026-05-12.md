# Fork Divergence Audit — 2026-05-12

**Base (working branch):** `anton/feat/multimodal-foundation`
**Head (k3street fork):** `origin/master`
**Total divergent commits:** 178

**Verdict counts:**
- `adopt`: 8
- `defer`: 3
- `unknown`: 167
- `merged`: 0
- `reject`: 0

**Advisory only.** No commits are auto-cherry-picked. Review each row; promote `unknown` rows to one of the four other verdicts; incorporate stable patterns back into `_SUBJECT_RULES` in `scripts/audit_fork_divergence.py` for next run.

## `adopt` (8)

| sha | date | subject | hint |
|---|---|---|---|
| `819a4244` | 2026-04-30 | feat: multi-provider vision (Ollama primary + Gemini fallback), doc FTS, KB feedback tests, auto-inject viewport | evaluate against Phase 76 (vision tool handlers) |
| `7d421b73` | 2026-04-23 | feat(tools): add hardware-accelerated isaac_ros_image_pipeline scaffolding tool | evaluate against Phase 7 (ros2 themed module) |
| `12f58218` | 2026-04-19 | feat: deploy_rl_policy tool — Isaac Lab keyboard-driven locomotion from chat | evaluate as Phase 79b sibling (RL policy deploy path) |
| `79f7fe0c` | 2026-04-19 | feat: add SceneWorkspace module — per-scene companion file storage | evaluate against Phase 26 (CAS-versioned LayoutSpec history) |
| `b053d500` | 2026-04-19 | feat: RViz2 auto-launcher + RTX LiDAR sensor tools + ROS bridge readiness LiDAR checks | evaluate against Phase 7 / 7b ROS2 split |
| `62ea63cd` | 2026-04-19 | feat: add robot motion diagnostic validator | evaluate against Phase 63c (per-robot cuRobo debug) |
| `2ac702c6` | 2026-04-18 | feat: RViz2 auto-launch tool + ROS2 routing fixes + PLAN update | evaluate against Phase 7 / 7b ROS2 split |
| `fb383bd2` | 2026-04-16 | feat: preflight_check tool with 23 checks across 4 tiers | evaluate against Phase 7b / scene-feasibility coverage |

## `defer` (3)

| sha | date | subject | hint |
|---|---|---|---|
| `c602f968` | 2026-04-22 | Configure Gemini as primary LLM provider and update MediaPipe teleop documentation/tests | evaluate against Phase 79b (locomanip teleop) |
| `8fd79a45` | 2026-04-21 | feat(ros2-autonomy): add lingbot tools, test suite, provider factory updates | vendor-specific; out of scope for current epoch |
| `c59d09ed` | 2026-04-18 | feat: add IRA actor control tools (setup_ira_simulation, inject_actor_command, actor_goto, list_ira_agents, configure_actor_commands) | evaluate against teleop track (Phase 79b) |

## `unknown` (167)

| sha | date | subject | hint |
|---|---|---|---|
| `7082aded` | 2026-05-04 | feat: Phase 1 RL+VLA manipulation pipeline integration |  |
| `5f6e2f72` | 2026-04-22 | Fix Gemini tool calling by injecting thought_signature and add Gemini test script |  |
| `e921b58d` | 2026-04-21 | add stop/cancel button to chat UI; fix gpt-5.x max_completion_tokens; fix deformable cloth KB patterns; fix broken Unicode icons; update T01-T19 test status; add KB corrective patterns from T1-T19 audit |  |
| `ca592521` | 2026-04-21 | rename LLM_MODE 'cloud' → 'google' for Gemini provider |  |
| `df3a7469` | 2026-04-21 | feat(ros2-autonomy): Phase 9 planning + autonomy tool stubs [UNTESTED] |  |
| `e8eb4532` | 2026-04-20 | fix: IsaacLab discovery via config + move demo script into project |  |
| `842708d9` | 2026-04-20 | fix: G1 arm pose matches training defaults + generic robot support |  |
| `f9e0c324` | 2026-04-20 | fix: correct script paths and add pretrained checkpoint download for G1 walking |  |
| `090285b9` | 2026-04-20 | perf: reduce capture_viewport default resolution to 512px, cap at 768px |  |
| `e4516a18` | 2026-04-20 | fix: prevent context overflow from capture_viewport base64 images |  |
| `bd4f8ce4` | 2026-04-20 | fix: Gemini "model is required" error — settings live-patch + model name auto-sync |  |
| `ab419276` | 2026-04-20 | docs: README — ISAACLAB_PATH setup + G1 locomotion quickstart |  |
| `c595ba5b` | 2026-04-20 | fix: point rl_policy_runner at existing IsaacLab install |  |
| `38655ddd` | 2026-04-20 | fix: G1 CG + sim restart — freeze arms down before policy launch |  |
| `9825dbb6` | 2026-04-20 | fix: LLM provider switch broken in UI + G1 falls with Inspire Hand |  |
| `54b354e6` | 2026-04-19 | docs: update G1 plan — Isaac Lab 2.3 already installed, Phase 2 unblocked |  |
| `87a19efe` | 2026-04-19 | docs: G1 + Inspire Hand locomotion plan — 3-phase roadmap |  |
| `cc4be422` | 2026-04-19 | docs: add scene creation demo script for video production |  |
| `eefcb5b4` | 2026-04-19 | feat: cloud LLM routing for agent swarm + extension 422 fix + diagnostics |  |
| `d112e603` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/phase12-qa-tasks-expansion' |  |
| `7bef3bf5` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/phase12-agent-driven-qa' |  |
| `844e8654` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier15-18-misc' |  |
| `461eb763` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier14-bulk' |  |
| `77faf760` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier13-rl-runtime' |  |
| `97fb1b31` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier12-asset-mgmt' |  |
| `d27a2d7a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier11-sdg' |  |
| `f1cb70cd` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier10-animation' |  |
| `5dfb7c42` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier9-layers' |  |
| `146136e5` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier8-render' |  |
| `6e22d50e` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier7-camera' |  |
| `685ab92c` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier6-lighting' |  |
| `7bc196e1` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier5-omnigraph' |  |
| `8575473b` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier4-geometry' |  |
| `0c392fee` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier3-articulation' |  |
| `f52169de` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier2-physics' |  |
| `4590a025` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier1-usd-core' |  |
| `68fbfb83` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/atomic-tier0-foundation' |  |
| `7d35f54f` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-safety-compliance-v2' |  |
| `b715a3f1` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase5-pedagogy-uncertainty-v2' |  |
| `b27f5333` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase7G-groot-tooling-v2' |  |
| `4e79fa3b` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-sim-to-real-gap-v2' |  |
| `18edf9c5` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-quick-demo-builder-v2' |  |
| `47f62116` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-community-remote-v2' |  |
| `19878492` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-collision-mesh-quality-v2' |  |
| `b6038f46` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/phase10-autonomous-workflows' |  |
| `78be4818` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-humanoid-advanced' |  |
| `888d2f99` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-physics-calibration' |  |
| `38307c0f` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-clearance-detection' |  |
| `12893ff1` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-dr-advanced' |  |
| `e66c2d94` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-ros2-nav2' |  |
| `e2d37e5a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-enterprise-scale' |  |
| `1b690358` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase7B-sdg-advanced' |  |
| `7b0c6d01` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase7C-teleop-quality' |  |
| `9ee4698e` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase7A-rl-debugging' |  |
| `ebd33f04` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/preflight-check-23' |  |
| `df86fe40` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-interactive-teaching' |  |
| `6685f0f5` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-omnigraph-assistant' |  |
| `6553766a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-onboarding' |  |
| `bdd16849` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-auto-simplification' |  |
| `080dc892` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-scene-diff' |  |
| `288d6f7f` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-material-database' |  |
| `dee09007` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/new-performance-diagnostics' |  |
| `2d46d5ac` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase8B-workspace-singularity-v2' |  |
| `2942147e` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase8F-ros2-quality' |  |
| `a34e1c4d` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase7B-sdg-quality' |  |
| `1ece5961` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase3-urdf-postprocessor' |  |
| `182bc367` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/addendum-phase2-smart-debugging' |  |
| `d164e6a4` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/9-finetune-flywheel' |  |
| `41121d76` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8F-ros2-deep' |  |
| `4d52a2fe` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8E-wheeled-robots' |  |
| `c737a31a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8D-robot-setup' |  |
| `f9ae1819` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/6A-physx-validation' |  |
| `6030a0fe` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8C-cortex-v2' |  |
| `edb2c38a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8B-motion-planning-complete' |  |
| `d987e9e0` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/8A-quick-wins' |  |
| `cbcb1137` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7H-cloud-deployment' |  |
| `369340ea` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7G-groot-n1' |  |
| `29952f27` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7F-zmq-bridge' |  |
| `06df45d3` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7E-eureka-rewards' |  |
| `664c7276` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7D-arena' |  |
| `8195cfb6` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7C-xr-teleoperation' |  |
| `c940721a` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/7B-replicator-sdg-v2' |  |
| `6266c1e1` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/documentation-site' |  |
| `4904a408` | 2026-04-18 | Merge remote-tracking branch 'origin/feat/test-framework' |  |
| `1e734801` | 2026-04-18 | Merge PR #78: fix(chat): retry Gemini transient errors + graceful user-facing message |  |
| `8f012129` | 2026-04-18 | Merge PR #77: fix(chat): remove race on provider._system_override in classify_intent |  |
| `f266d279` | 2026-04-18 | Merge PR #76: fix(tools): replace deprecated omni.isaac.* imports with isaacsim.* for 5.x |  |
| `fec7f03e` | 2026-04-18 | Merge PR #13: fix: correct Lula RRT config loader function name |  |
| `3d78b4d4` | 2026-04-18 | Update copyright year in README.md to 2026 |  |
| `739af15d` | 2026-04-17 | Add Apache 2.0 license, attribution, and fix Nova Carter OmniGraph config |  |
| `5c2d557c` | 2026-04-17 | feat: cuRobo GPU motion planning + vision-guided pick-and-place tools |  |
| `c486de3d` | 2026-04-17 | fix: launch_all.sh wait strategy + install argcomplete for rosbridge |  |
| `93263694` | 2026-04-17 | feat: GUI mode picker + custom icon for desktop launcher |  |
| `eac4377b` | 2026-04-17 | feat: all-in-one launcher + desktop shortcut + fix websocket |  |
| `3bc516c1` | 2026-04-17 | fix: filter poisoned intent-echo messages from conversation history |  |
| `ffc73205` | 2026-04-17 | fix: Anthropic/Gemini providers now use distilled system prompt |  |
| `62c9fd77` | 2026-04-17 | feat: upgrade default model to Claude Opus 4.7 |  |
| `f964b0aa` | 2026-04-17 | fix: relax code pattern auto-capture dedup thresholds |  |
| `3711d7b7` | 2026-04-17 | feat(qa): expand Phase 12 task set from 10 → 160 (15 personas × ~10 tasks each) |  |
| `96b2a963` | 2026-04-17 | fix(chat): retry Gemini transient errors + graceful user-facing message |  |
| `b86f9f8a` | 2026-04-17 | fix(chat): remove race on provider._system_override in classify_intent |  |
| `87ab53fa` | 2026-04-17 | fix(tools): replace deprecated omni.isaac.* imports with isaacsim.* for 5.x |  |
| `477bba62` | 2026-04-16 | feat: phase 12 — agent-driven QA infrastructure (personas, tasks, launcher, judge, aggregator) |  |
| `8bd0e85a` | 2026-04-16 | feat: atomic tools tiers 15-18 — viewport, persistence, extensions, audio |  |
| `854e7eeb` | 2026-04-16 | feat: Tier 14 atomic bulk operations (5 tools) |  |
| `2d081c13` | 2026-04-16 | feat: Tier 13 — IsaacLab RL runtime introspection (5 atomic tools) |  |
| `fba3c33a` | 2026-04-16 | feat(tier12): atomic Asset Management tools — 5 tools (list_references, add_usd_reference, list_payloads, load_payload, get_asset_info) |  |
| `40643770` | 2026-04-16 | feat(tier11): atomic SDG Annotation tools — 5 tools (list_semantic_classes, get_semantic_label, remove_semantic_label, assign_class_to_children, validate_semantic_labels) |  |
| `5e3bf1a2` | 2026-04-16 | feat(tier10): atomic Animation & Timeline tools — 5 tools (get_timeline_state, set_timeline_range, set_keyframe, list_keyframes, play_animation) |  |
| `89f28a4b` | 2026-04-16 | feat(tier9): atomic USD Layers & Variants tools — 6 tools (list_layers, add_sublayer, set_edit_target, list_variant_sets, list_variants, flatten_layers) |  |
| `f1b9ba65` | 2026-04-16 | feat(tier8): atomic Render Settings tools — 5 tools (get/set render config, resolution, post-process, environment background) |  |
| `6bb1ba94` | 2026-04-16 | feat: Tier 7 atomic camera tools (5) — list, get, set, capture, look_at |  |
| `1ff61518` | 2026-04-16 | feat: atomic Tier 6 lighting tools (5 tools with rich descriptions) |  |
| `42c7fb5d` | 2026-04-16 | feat: atomic Tier 5 OmniGraph tools (6 low-level operations) |  |
| `99569499` | 2026-04-16 | feat: Tier 4 atomic tools — 7 Geometry & Spatial Analysis primitives |  |
| `0b486592` | 2026-04-16 | feat: Tier 3 atomic tools — 9 Articulation & Joints primitives |  |
| `b8bf922e` | 2026-04-16 | feat: Tier 2 atomic tools — 10 Physics Bodies & Scene primitives |  |
| `00542736` | 2026-04-16 | feat: Tier 1 atomic tools — 10 USD Core inspection primitives |  |
| `a9567be9` | 2026-04-16 | feat: Tier 0 atomic tools — 12 foundation primitives |  |
| `c2d65bfc` | 2026-04-16 | feat: Safety & Compliance addendum — enable_deterministic_mode (replaces #43) |  |
| `c927182d` | 2026-04-16 | feat: Phase 5 addendum — create_broken_scene per spec (replaces #41) |  |
| `afd6d9fa` | 2026-04-16 | feat: GR00T Advanced Tooling addendum — 7 tools per spec (replaces #38) |  |
| `3df516c5` | 2026-04-16 | feat: Sim-to-Real Gap Tooling — 4 tools per spec (replaces #35) |  |
| `d43b51db` | 2026-04-16 | feat: Quick Demo Builder — quick_demo + record_demo_video (per spec) |  |
| `d202cde7` | 2026-04-16 | feat: addendum C — community & remote access tools (8 new tools) |  |
| `532ff81f` | 2026-04-16 | feat: Collision Mesh Quality addendum — 3 tools (check/fix/visualize) |  |
| `92ad5e7d` | 2026-04-16 | feat(phase10): autonomous multi-step workflows with editable plans + error-fix loop |  |
| `a69051b5` | 2026-04-16 | feat: humanoid advanced tooling addendum (H.1-H.4) |  |
| `ee4744e8` | 2026-04-16 | feat: physics parameter calibration toolset (BO + ActuatorNet) |  |
| `78f9a89e` | 2026-04-16 | feat: Clearance Detection addendum — 3 tools (set_clearance_monitor, visualize_clearance, check_path_clearance) |  |
| `75d68381` | 2026-04-16 | feat: DR Advanced addendum — 5 tools + 53 tests |  |
| `046ccb09` | 2026-04-16 | feat: ROS2 Nav2 integration addendum (Persona P12) |  |
| `226d9611` | 2026-04-16 | feat: enterprise scale addendum — scoped traversal, batch ops, delta snapshots |  |
| `6543b75b` | 2026-04-16 | feat: Phase 7B addendum — advanced SDG (5 tools + 42 tests) |  |
| `c4a5a8d1` | 2026-04-16 | feat: Phase 7C addendum — teleop quality gates (5 tools) |  |
| `fc7b311b` | 2026-04-16 | feat(rl): Phase 7A addendum — RL training debugging & quality tools |  |
| `0ec6b4d8` | 2026-04-16 | feat: interactive robot teaching tools (drag_target, keyboard, spacemouse, gravity_comp) |  |
| `1e70c4ac` | 2026-04-16 | feat: OmniGraph Assistant — 3 tools + 8 templates for NL graph creation |  |
| `bfc4722a` | 2026-04-16 | feat: onboarding tools — starter prompts, hardware check, slash commands, error autodetect, suggestions, templates |  |
| `f7a56a71` | 2026-04-16 | feat: automatic scene simplification tools (optimize_scene, simplify_collision, suggest_physics_settings) |  |
| `53bc1391` | 2026-04-16 | feat: scene_diff + watch_changes tools for USD scene change tracking |  |
| `049a6cd3` | 2026-04-16 | feat: physics material database — lookup_material + apply_physics_material tools |  |
| `f0d7f80f` | 2026-04-16 | feat: add performance diagnostics tools (diagnose_performance, find_heavy_prims, optimize_collision) |  |
| `6021a73a` | 2026-04-16 | feat: Phase 8B addendum — Workspace Visualization & Singularity Detection (3 tools + 10 tests) |  |
| `798017f6` | 2026-04-16 | feat: Phase 8F addendum — ROS2 quality diagnostics (3 tools, 11 tests) |  |
| `dc7ff82f` | 2026-04-16 | feat: SDG quality tools — validate_annotations, analyze_randomization, diagnose_domain_gap |  |
| `9e5ee084` | 2026-04-16 | feat: Phase 3 URDF post-processor tools (verify_import, apply_robot_fix_profile) + Phase 2 addendum implementations |  |
| `39a5a961` | 2026-04-16 | feat: Phase 2 addendum — Smart Debugging (3 tools + 13 tests) |  |
| `9b967334` | 2026-04-16 | feat: Phase 9 — Fine-Tune Flywheel (4 tools + 47 tests) |  |
| `c6b7d297` | 2026-04-16 | feat: Phase 8F — ROS2 Deep Integration (3 tools + 8 tests) |  |
| `f65cc777` | 2026-04-16 | feat: Phase 8E — Wheeled Robots & Conveyor Systems (5 tools + 14 tests) |  |
| `1e53daaf` | 2026-04-16 | feat: Phase 8D — Robot Setup Suite (4 tools + 20 tests) |  |
| `449059de` | 2026-04-16 | feat: 6A addendum — PhysX pre-flight collision validation |  |
| `e8efab6c` | 2026-04-16 | feat: Phase 8C — Cortex Behaviors & Manipulation (5 tools + 17 tests) |  |
| `6c7e639c` | 2026-04-15 | feat: Phase 8B complete — Motion Policy, Robot Description, IK Solver (3 tools + 12 tests) |  |
| `afa49bd5` | 2026-04-15 | fix: correct Lula RRT config loader function name |  |
| `78ec179d` | 2026-04-15 | feat: Phase 8A — Cloner, Debug Draw, Occupancy Map, Camera (5 tools + 26 tests) |  |
| `17f770d1` | 2026-04-15 | feat: Phase 7H — IsaacAutomator Cloud Deployment (5 tools + 15 tests) |  |
| `47e7be07` | 2026-04-15 | feat: Phase 7G — GR00T N1.6 Foundation Policy (4 tools + 16 tests) |  |
| `951311fa` | 2026-04-15 | feat: Phase 7F — ZMQ Sensor Streaming (1 tool + 6 tests) |  |
| `213c1141` | 2026-04-15 | feat: Phase 7E — LLM Reward Generation / Eureka (4 tools + 12 tests) |  |
| `00760c77` | 2026-04-15 | feat: Phase 7D — IsaacLab-Arena Composable Environments (4 tools + 13 tests) |  |
| `f5dd77a3` | 2026-04-15 | feat: Phase 7C — XR Teleoperation (5 tools + 16 tests) |  |
| `eeb3a494` | 2026-04-15 | test: add L0 tests for Phase 7B Replicator/SDG tools |  |
| `41e4886f` | 2026-04-15 | feat: Phase 7B — Enhanced Replicator / Synthetic Data Generation |  |
| `7bae0060` | 2026-04-15 | feat: MkDocs Material documentation site (22 pages) |  |
| `4b95f842` | 2026-04-15 | feat: comprehensive pytest test framework with 652 L0 tests |  |

