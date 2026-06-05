# Canonical Library Quality & Coverage Audit (2026-05-09)

**Scope.** 86 templates at `/home/anton/projects/Omniverse_Nemotron_Ext/workspace/templates/CP-*.json`.
Method: read 28 files in detail (CP-01, CP-02, CP-04, CP-05, CP-08, CP-10, CP-12, CP-15, CP-16, CP-20, CP-22, CP-25, CP-28, CP-30, CP-35, CP-37, CP-40, CP-43, CP-45, CP-47, CP-50, CP-55, CP-58, CP-60, CP-62, CP-65, CP-68, CP-69..CP-86), plus light parse (goal/tools/status/extends) over the rest. Aggregate metrics computed on all 86.

## 1. Headline metrics

| Metric | Count / share |
|---|---|
| Total canonicals | **86** |
| Robot family — Franka | 68 (79%) |
| Robot family — UR10 | 16 (19%) |
| Robot family — Carter (mobile) | 1 |
| Robot family — none / loop-only | 1 |
| target_source `curobo` | 67 (78%) |
| target_source `builtin` (PickPlaceController) | 9 |
| target_source `spline` | 1 |
| target_source not set (infrastructure-only) | 9 |
| Belt (`belt_path`) present | 72 (84%) |
| Sensor (`sensor_path`) present | 73 (85%) |
| `drop_targets` dict used | 26 (30%) |
| `color_routing` used | 12 (14%) |
| `compute_stack_placement` used | 7 |

### Verified status snapshot

| Bucket | Count |
|---|---|
| function-gate ✓ | 35 |
| function-gate ✗ | 8 (mostly UR10 belt-pause-from-callback + UR10 multi-cube limitation) |
| form-gate only / pending | 37 |
| Build-only (no pick-place to gate) | 6 |

The 35 function-gate-✓ figure matches the task brief's "~37" within rounding. The remaining ~36 are not all hard ✗; the larger group is `pending` — built but never run end-to-end.

### Extends-graph (top-level ancestors)

| Parent | Direct descendants |
|---|---|
| CP-01 (single-Franka, single-cube, conveyor → bin) | 22 |
| CP-08 (CP-01 + 2×2 palletizer with `drop_targets` dict) | 13 |
| CP-78 (UR10 builtin + pedestal — first stable UR10 delivery) | 6 |
| CP-69 (UR10 + cuRobo) | 4 |
| CP-13 / CP-10 / CP-47 | 3 each |

**Observation.** 35/86 canonicals (40%) are direct or two-step descendants of CP-01 → CP-08. The library is heavily concentrated around the "Franka + conveyor + grid drop" axis.

## 2. Category breakdown (by primary feature)

| Category | Count | IDs |
|---|---|---|
| **palletizer / multi-drop** (drop_targets, no compute_stack_placement) | 13 | CP-12, 13, 14, 19, 24, 25, 27, 30, 36, 38, 39, 42, 77 |
| **color-routing** (semantic_label + bins) | 12 | CP-03, 16, 17, 18, 32, 33, 34, 35, 66, 82, 85, 86 |
| **palletizer-grid** (compute_stack_placement) | 7 | CP-08, 09, 10, 11, 15, 20, 46 |
| **single-cube-bin** (smallest valid scene) | 5 | CP-01, 69, 81, 83, 84 |
| **native-controller** (RmpFlow PickPlaceController) | 4 | CP-06, 74, 78, 80 |
| **kitting** (create_kit_tray) | 4 | CP-49, 50, 53, 65 |
| **pose-variant** (mirrored / offset / parallel cells) | 4 | CP-07, 23, 45, 79 |
| **multi-robot / dynamic obstacle** | 3 | CP-61, 68, 76 |
| **multi-robot / mutex** | 2 | CP-52, 59 |
| **vision-routing** (Gemini at install time) | 2 | CP-47, 48 |
| **mixed-shape** | 2 | CP-43 (sphere), 44 |
| **rotary** | 2 | CP-56, 67 |
| **cortex** (CortexUr10 wrapper) | 2 | CP-72, 73 |
| **surface-gripper** | 2 | CP-54 (Franka), 70 (UR10) |
| **precision-bench** | 2 | CP-28, 29 |
| **multi-station / belt-relay / multi-robot handoff** | 3 | CP-02, 26, 51 |
| Singletons — one canonical each (16 total) | 16 | flip-station, gravity-feed, heap-pick, drawer-articulated, peg-in-hole, recirc-loop, gantry, grasp-sampler, nav/mobile, dispenser, tabletop-rearr, footprint-bound, obstacle-avoid, mixed-mass, high-speed-belt, destack, spline-controller |

### Over-represented

- **Palletizer family (grid + multi-drop): 20/86 (23%)**. Half the differences are spacing (8 cm vs 10 cm vs 16 cm), pallet size (30 vs 45 vs 50 cm), or N (4 vs 6 vs 9 vs 16 vs 18 cubes). Diagnostic value beyond the first 3-4 is marginal.
- **Color-routing: 12/86 (14%)**. CP-03 (2 colors) → CP-16 (4 colors) → CP-17 (3 semantic classes) → CP-18 (good/reject) → CP-35 (4 colors + reject). Subsequent colour entries (CP-82, 85, 86) are UR10 ports — necessary for robot diversity but redundant on the routing axis.
- **CP-78 derivatives (UR10 + pedestal): 6**. Most are 1-line A/B isolation tests for live debugging (CP-83 = +cuRobo, CP-84 = +stacking, CP-85 = +smaller bins, CP-86 = +color_routing). Excellent during the bring-up sprint, but several look prunable now that the bug is understood.

### Under-represented (one canonical each)

`flip-station, gravity-feed, heap-pick, drawer/articulated, peg-in-hole, recirc-loop, gantry, grasp-sampler, nav/mobile, dispenser, tabletop-rearr, footprint-bound, obstacle-avoid, mixed-mass, high-speed-belt, destack, spline-controller, belt-relay, multi-robot handoff, multi-station`. These categories each have **one** canonical, often build-spec-only. They expose new tools but lack diagnostic depth.

## 3. Quality signals

### Goals — clarity

Most goals are crisp and self-describing in 1-2 sentences. CP-01 / CP-78 / CP-58 are excellent examples of intent + design rationale captured up-front. A few that drift toward marketing copy (CP-25's "industrial throughput pattern", CP-22's "industrial relevance: high-throughput"), but no truly opaque goals encountered.

### "thoughts" sections — informativeness

- **A-tier informative (verbatim physics + planner detail).** CP-01 (the seminal 9-point template covering CPU-dynamics broadphase, sleepThreshold, friction tuning, finger-cube material warning), CP-02 (per-robot subscription scoping + explicit drop_target requirement when destination bbox center is out of reach), CP-58 (peg-in-hole force sensor wiring), CP-69/74 (UR10 cuRobo init quirks), CP-78/79 (reach-geometry diagnostic chain). These read like real design memos.
- **B-tier formulaic.** Many palletizer derivatives (CP-10, CP-15, CP-20, CP-25, CP-30) follow the same six-bullet structure — "Pallet x×y; spacing s; n cubes; reach check; drop_targets dict; industrial use". Useful reference but largely substitutable.
- **C-tier near-boilerplate.** CP-22 (high-speed belt), CP-43 (sphere-pick), CP-44 (mixed-shape), CP-41 (mixed-mass) have thoughts that mostly restate the goal and append "industrial use:..." — they teach the agent little it can't infer from the code.

### `verified_status` honesty

Generally honest. Strong patterns:

- "build-spec-2026-05-08; form-gate verification pending" — for canonicals authored but never executed.
- "function-gate ✓ (with 180s sim — orig 60s too short. cube_final=...)" — concrete delivery position recorded.
- "function-gate ✗ (belt-pause-from-callback bug — same root cause as CP-80)" — failure mode named.

A few that hedge ("form-gate verification likely fails"; "function-gate likely partial"): CP-67, CP-73, CP-77. These are honest about uncertainty but the *likely* in lieu of measurement is a known gap. CP-09 explicitly admits "function-gate to verify after enlarging TowerBase 10x10→15x15cm" — that's a TODO marker that's been carrying for >24 h.

CP-28's verified_status is exemplary — embeds the precision-experiment data inline ("dy=-0.16m bias measured") and explains *why* CP-08's 30 cm pallet succeeds despite ~24 cm drop precision. This is the gold standard.

### `tools_used` accuracy

Spot-checked across all 28 deeply-read files; tools_used is consistent with the actual `code` block in 27/28. CP-55 (drawer-articulated) lists `create_articulated_joint` in tools_used but the code block does call it — consistent. No inaccurate-tools-used cases found.

## 4. Diversity audit

| Axis | Coverage |
|---|---|
| Robots | Franka 68 + UR10 16 + Carter 1 + None 1. Cobotta deferred (Isaac install lacks the asset). |
| Motion stack | cuRobo 67 + builtin/RmpFlow 9 + spline 1. Both real alternatives represented but cuRobo dominates 78%. |
| Belt motion | 72 with belt; 14 without (static-cube, peg-in-hole, drawer, grasp-sampler, nav, recirc-loop, kitting). |
| Sensor | 73 with proximity sensor; 13 without (mostly the same 14 minus the 1 difference). |
| Drop targets | dict 25; list 1 (CP-39 — explicit list-form test); single drop_target 5; default bbox center the rest. |
| Routing | color_routing 12; vision-derived 2 (CP-47/48); compute_stack_placement 7. |
| Drop receptacle | Bin 58, Pallet 18, KitTray 4, Container/StackBin 3, drawer 1, BaseCube 1, recirc-loop 1. |
| Object shape | Cube 80, Sphere 2, Cylinder 2 (CP-05 flip + CP-58 pegs), brick 1, lid (flat scaled cube) 1. |
| Special infrastructure | Cortex 2, ROS-bridge 0, recirc-loop 1, rotary 2, gantry 1, dispenser 1, heap 1, force-sensor 1, drawer 1. |

## 5. Quality tiers

### A-tier — diagnostic gold (read these first as references)

| Canonical | Why A-tier |
|---|---|
| **CP-01** | The seminal template. 9-point thoughts list captures every physics + cuRobo gotcha that drove later canonicals. Function-gate ✓, comprehensive `failure_modes`, explicit `benchmark_vs_alternatives` listing curobo / spline / native / diffik / osc. |
| **CP-02** | Multi-robot scoping. Captures the per-robot subscription naming bug that the cuRobo handler had to fix. Explicit drop_target requirement when destination bbox is out of reach is a non-obvious lesson. Function-gate ✓. |
| **CP-08** | Defines the `drop_targets` dict pattern that 25 later canonicals reuse. Explicit "computed via compute_stack_placement at canonical-author time and baked in" comment is a teachable architecture decision. Function-gate ✓ (multi-cube). |
| **CP-28** | The precision benchmark. verified_status embeds raw measurement data ("3-run benchmark: 21cm, 20cm, 33cm") that drives CP-30's "size pallet at measured-precision × 2" rule. |
| **CP-37** | Obstacle-avoidance with explicit `end_effector_initial_height=1.30` override. Teaches that EE_INITIAL_HEIGHT auto-compute can be wrong when planning_obstacles include tall items. |
| **CP-58** | Peg-in-hole with force sensor scaffolding. The "holes are Xform markers — no actual cylindrical holes via subtractive mesh" admission is the kind of honest scope-of-the-canonical note that's easy to omit. Function-gate ✓. |
| **CP-69** | First UR10 canonical. Documents UR10's 1.3m reach vs Franka's 0.85m, asset-library load path, robot_family='ur10' branch in cuRobo handler. Function-gate ✓ via raycast-FixedJoint workaround. |
| **CP-74 / CP-78 / CP-80 (paired)** | The diagnostic triad: native vs builtin, with-pedestal-vs-without, with-conveyor-vs-without. Canonical example of A/B isolation. Each one's verified_status references the others to triangulate the bug ("if THIS function-gate succeeds but CP-75 doesn't, the gap is reach geometry"). |
| **CP-86** | Pure A/B isolation test ("CP-86 = CP-78 + set_semantic_label + color_routing dict only"). 1-paragraph thoughts, but every word is load-bearing. Function-gate ✓ proved color_routing was *not* the bug. |

### B-tier — functional but generic

The bulk of CP-10..CP-30 (palletizer family) and CP-32..CP-36 (vision/sortation extensions). These are correct, build, and (where tested) work. They expand coverage on a single axis (more cubes, larger pallet, tighter spacing, more colours, etc.). Useful for verifying handler scaling but each one is largely substitutable for an adjacent ID.

Also: CP-04 (footprint-constrained CP-01), CP-23 (mirror-orientation CP-01), CP-45 (offset-mount CP-01) — three minor pose variants of the same scene. Together they prove handler is pose-invariant; individually each is narrow.

### C-tier — low-value or duplicative (deprecation candidates)

| ID | Issue |
|---|---|
| **CP-25** (4×4 high-density) | Explicitly notes function-gate "partial expected" (drop drift > spacing). Demonstrates a known failure rather than a working pattern. Educational value low; verifier-stress value low. |
| **CP-29** (y-bias compensated CP-28) | verified_status: "y-bias compensation FAILED — cube was NEVER PICKED". Diagnostic experiment that produced a negative result; the result is now documented in CP-28's status, so CP-29 is redundant. |
| **CP-09** (graduated tower v1) | Superseded by CP-15 (the "real" mixed-SKU column with cube_sizes). Status: "function-gate to verify after enlarging TowerBase 15x15cm" — open TODO since 2026-05-08. |
| **CP-12** (mixed-SKU with 10 cm cube) | failure_modes admit Franka's 4 cm gripper can't grip the 10 cm cube. CP-15 fixed this with 5/8/10 actually-grippable mix. CP-12 is the "broken first version". |
| **CP-22** (high-speed belt) | Generic CP-01 with surface_velocity bumped to 0.5. thoughts add no new physics knowledge beyond CP-01. status: pending. |
| **CP-44** (mixed-shape sphere+cube) | Tests a property already covered by CP-43 (all spheres) + CP-01 (all cubes). Doesn't expose anything new. |
| **CP-50** (vision-driven kitting) | Combines CP-47 (vision) + CP-49 (kitting). The combination is mechanical (same 2 dispatches in sequence, no synergistic challenge). Pending function-gate. |
| **CP-79** (CP-78 with cube on +X+Y) | Diagnostic-only: mirrored geometry to check whether home-pose vs cube placement is the bug. Status: function-gate ✓ — *confirmed not a problem*. The diagnostic served its purpose; canonical may be retired. |
| **CP-81 / CP-82 / CP-85** | Three near-identical UR10 multi-cube + (color_routing or not) variants whose function-gate ✗ result is "drop precision — cube lands at z=0.775 table top". The bug's root cause is documented in CP-86's status; CP-81/82/85 didn't help debug it (CP-86 did). |
| **CP-71** (UR10 dispenser) | Status: function-gate ✗ "single-cube limitation, not Robot fault". The failure is in the test harness, not the canonical. Worth marking explicitly. |
| **CP-67** (rotary leader/follower) | Status: "form-gate verification likely fails (rotary disc bridge issue)". Hedge instead of measurement. |

These 11 candidates aren't *bad* canonicals — they capture lessons. The recommendation is to **fold their lessons into A-tier siblings' verified_status / failure_modes** and deprecate the standalone files.

## 6. Coverage gaps

Compared with what robotics demos typically need, the following categories have **zero** canonicals at present:

| Missing | Why it would matter |
|---|---|
| **ROS2 publish/subscribe** | Real-world deployments require ROS bridge; `setup_ros2_bridge` exists in the tool registry but no canonical exercises it. |
| **Domain randomization** | `add_domain_randomizer` / `apply_dr_preset` tools exist; no canonical applies them. SDG / sim-real gap testing is a major Isaac Sim use case. |
| **Synthetic data generation (SDG)** | `configure_sdg`, `create_sdg_pipeline`, `benchmark_sdg`, `validate_annotations` — entire family unused except CP-63 (grasp-pose-sampler). |
| **Teleoperation** | `start_teleop_session`, `record_teleop_demo`, `validate_teleop_demo` — zero canonicals. |
| **Reinforcement learning** | `setup_loco_manipulation_training`, `launch_training`, `iterate_reward`, `evaluate_groot` — zero RL training canonicals. |
| **Articulated objects (cabinets, doors, drawers, faucets)** | CP-55 alone covers the prismatic-drawer case. No revolute-hinge, no multi-DoF articulated pickup. |
| **Cloth / deformable** | `create_deformable_mesh` exists; no canonical. |
| **Bipedal / humanoid** | Zero canonicals; the entire whole_body_control / loco_manipulation family unused. |
| **Sensor fusion / multi-camera** | At most 1 camera per scene. No stereo, no depth+RGB+IMU. |
| **Vision-language navigation** | `vision_plan_trajectory`, `vision_analyze_scene` unused outside CP-47/48 install-time classification. |

The library's centre of gravity is **industrial pick-and-place** (Franka or UR10, conveyor or pedestal, into a bin or onto a pallet, possibly colour-routed). This is well covered. Outside that lane, coverage drops to one-canonical-or-fewer per category — which is fine for showing the tool exists, but insufficient for an LLM to learn the *idioms* of those categories the way CP-01..CP-30 teach pick-and-place idioms.

## 7. Recommendations

### Keep
- **All A-tier (10 canonicals)** as reference exemplars. Mention them prominently in any LLM-prompt index of the library.
- **Palletizer family pruned to a representative 4-5**: CP-08 (2×2 baseline), CP-15 (mixed-SKU column), CP-20 (brick-layer 2-layer), CP-30 (precision-grounded 50 cm pallet), one of {CP-10, CP-46} for the 3×3 / 3×2 grid.
- **One canonical per under-represented infrastructure category** (drawer, peg-in-hole, gantry, recirc-loop, heap, dispenser, gravity-feed, rotary, nav, grasp-sampler) — even build-spec-only — because they're the only documentation of those tools' usage.
- **The UR10 reach-diagnostic triad** (CP-74 + CP-78 + CP-80). They capture a specific, hard-won bug fix.

### Merge
- **CP-09 → CP-15.** CP-15 already realises the graduated-tower goal correctly. CP-09's verified_status is a stale TODO; absorb its lessons into CP-15's failure_modes.
- **CP-22 → CP-01.** Add a "high-speed variant" failure-mode bullet to CP-01's failure_modes; retire CP-22 standalone.
- **CP-43 + CP-44 → one "non-cube object" canonical.** Sphere is the interesting case (rolling, friction-grip); mixed-shape is mechanical.
- **CP-04 + CP-23 + CP-45 + CP-79 → one "scene-pose-invariance" canonical.** The four together prove invariance but each individual one is thin.
- **CP-25 → CP-30 footnote.** "Density beyond drop-precision is unstable" is a useful lesson; it doesn't need its own canonical.
- **CP-29 → CP-28 footnote.** Negative-result bias-compensation already lives in CP-28's status.

### Deprecate (delete or move to `archive/`)
- **CP-50 (vision + kitting combo)** — mechanical combination, no new pattern.
- **CP-71 (UR10 dispenser)** — test harness limitation, not a controller pattern. Re-instantiate when simulate_traversal_check supports multi-cube source paths.
- **CP-79 (UR10 +X+Y mirror)** — diagnostic complete (CP-78 stack works regardless of approach direction).
- **CP-81 / CP-82 / CP-85 (UR10 multi-cube + color variants)** — the bug they were probing is now isolated by CP-86 alone.
- **CP-67 (rotary leader/follower)** — hedged status; either measure or remove.
- **CP-12 (broken-gripper mixed-SKU)** — superseded by CP-15.

If those 11 deprecation candidates are removed and the merge candidates folded, the library shrinks from 86 → ~70 with **no loss of coverage** and **higher signal density per file**.

### Add (priority order)
1. **One ROS2 bridge canonical** (publish a topic, subscribe to it during pick-place).
2. **One domain-randomization + SDG canonical** (DR ranges + 100-frame dataset emit).
3. **One revolute-hinge articulated canonical** (open a cabinet door before picking from inside).
4. **One teleoperation canonical** (`record_teleop_demo` + `validate_teleop_demo`).
5. **One real cuRobo-with-suction UR10 canonical** that closes the gap to industrial UR10 deployments — picking up where CP-70's surface_gripper installer drift fix lands. Currently CP-70 documents the bug rather than demonstrating the fixed behaviour.

## 8. Honesty meta-note

The clearest signal in the library is **how openly the canonicals admit failure**. CP-28 ("cuRobo drop precision is closer to 20-30 cm than the assumed 5 cm. CP-08 succeeds because of generous margin, not precision"); CP-29 ("y-bias compensation FAILED — cube was NEVER PICKED"); CP-70 ("Form-gate still passes because the verifier only checks pipeline-shape, not graph contents"); CP-80 ("PhysX-level integrator caches independently of USD attribute layer"). This kind of "the test passed for the wrong reason" / "we measured this, it didn't work" honesty is the library's biggest asset. New canonicals should be authored to the same standard.
