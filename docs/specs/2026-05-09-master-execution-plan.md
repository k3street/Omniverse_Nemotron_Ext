# Master Execution Plan — Multimodal × Controller-Logic × Diagnostic

**Date:** 2026-05-09
**Status:** active — Phase 0 in progress, Phase 1 modules pre-built (await baseline lock)
**Purpose:** authoritative ordering, testing, evaluation, and diagnostic strategy across all in-flight specs. Single source of truth for "what comes next, why, and how we know it worked".

**Live status (2026-05-10 afternoon — UPDATED):**

**Phase 0 ✓ DONE** — baseline frozen (1 stable_ok CP-22 + 1 flaky CP-59 + 22 stable_fail), N=5 timeout fix (Phase 0.7) restored CP-65 from NO_RESULT, total stable post-restoration: 2.

**Phase 1 ✓ DONE** — diagnose_scene_feasibility infrastructure live (1100 LOC + 96 unit tests), wired (`bfce6a8`), MCP schemas + auto_judge axis. 64 templates got AST-extracted diagnose_args. Tool accuracy 68% vs baseline (acceptable hint, not authoritative).

**Phase 2 prep ✓ DONE** — phase2_triage + phase2_action_plan + phase2_safe_execute (revert-safety) committed.

**Phase 4 PARTIAL — 3 unlocks delivered 2026-05-10:**
- 3D-aware reach check in cuRobo's `_cube_to_pick` (commit `7c0ad42`):
  EE travel distance is `sqrt(xy_dist² + h1_offset²)` not just xy. CP-37 with
  `EE_INITIAL_HEIGHT=1.30` had cubes at xy=0.797m → 3D=0.97m beyond 0.855m
  reach. **CP-37: stable_fail → stable_ok** (5/5 verified).
- Multi-cube simulate_args fix: `cube_path` (single) → `cube_paths` (any cube
  delivered counts). Applied to CP-65, CP-52, CP-53, CP-67, CP-76. **CP-65
  restored, CP-53 unlocked.**
- ctrl:plan_calls / plan_fails / last_fail_goal counters added (commit `e72946f`).

**Phase 6 M1 ✓ DONE** — ROS2 production parity tools + CP-87 template (`bfce6a8` + recovery `0a510e6`).

**Phase 6 M2 ✓ DONE 2026-05-10** — Modbus-TCP bridge primitive (commit `0763ff8`):
- modbus_tcp_bridge_attach + diagnose_modbus_bridge + detach handlers
- pymodbus 3.11 supervised subprocess (zombie-aware diagnose)
- 8 unit tests green, end-to-end smoke against mock server
- CP-NEW-plc-conveyor template (first plumbing-only canonical)

**Industrial-expansion Phase 8/9/10 still spec-only** — 2 of M1-M5 milestones now done.

**Net stable_ok count post-2026-05-10:** 4 in patched-set (CP-22, CP-37, CP-53, CP-59, CP-65) — up from 2 in Phase 0 baseline.

---

## Anchors (read these first if you only have 5 minutes)

- `docs/specs/2026-05-08-multimodal-foundation-spec.md` — multimodal arch (Block 1-5)
- `docs/specs/2026-05-09-block-1a-status.md` — what already shipped in multimodal Block 1A
- `docs/specs/2026-05-09-multi-session-coordination.md` — file ownership split
- `docs/specs/2026-05-09-scenario-profile-controller-config.md` — branched controller config
- `docs/specs/2026-05-09-diagnose-scene-feasibility.md` — pre-flight validator
- `docs/specs/2026-05-09-industrial-expansion-spec.md` — ROS2/OPC-UA bridges + yrkesroll canonicals (Phases 6, 8, 9, 10 below)
- `data/regression_post_patches_regression_post_patches.json` — current baseline (49 ✓ stable / 25 patched-set)

---

## Guiding principles

1. **Diagnostic before fix.** Each phase produces (or extends) a measurement that proves the next phase's effect. No tuning without metric.
2. **Determinism first.** Random seeds set everywhere. Multi-run for stochastic outcomes (N-of-M criterion). Single-run results are flagged as "preliminary".
3. **Snapshots are sacred.** Capture pre/post state for every phase. Regression detection is a workflow, not an afterthought.
4. **Spec compliance, not improvisation.** If a phase's actions diverge from the spec, the spec gets updated and reviewed BEFORE code lands.
5. **Two sessions, sectional ownership.** Per `multi-session-coordination.md` — controller-logic and multimodal don't touch each other's code.

---

## Phase order (sequential, each gates next)

### Phase 0 — Stabilize + lock baseline (1-2 sessions)

**Goal:** end the regression churn from this session. Establish reproducible 49 ✓ floor.

**Tasks:**
- Revert any in-flight per-canonical experimental edits to the v10 baseline shape
- Add `seed` and `n_runs` parameters to `simulate_traversal_check`. Default `n_runs=1, seed=42`.
- Run `n_runs=5` against patched-set; freeze the 5-run majority result as `workspace/baselines/2026-05-09-baseline.json` (cube_final per CP, ctrl:* attrs, success rate over 5 runs).
- Add `scripts/qa/baseline_compare.py` — diffs current run against baseline, flags regressions with severity.

**Tests:**
- Unit test: `simulate_traversal_check` with same `seed` produces identical `cube_final` (within 1e-3) across two consecutive calls.
- Multi-run: 25 canonicals × 5 runs = 125 sims. Record per-CP success rate. Stable ✓ = ≥4/5; flaky = 1-3/5; stable ✗ = 0/5.

**Exit criterion:** baseline file committed; per-CP success rate ∈ {stable_ok, flaky, stable_fail} with explicit category.

**Why it gates next:** without this, every subsequent phase chases ghosts (this session's net = +1 ✓ but it took 16 regression rounds to figure out — most regressions were stochastic noise).

---

### Phase 1 — Implement `diagnose_scene_feasibility` (2-3 sessions)

**Goal:** ship the install-time validator. Use it to classify the 22 ✗ canonicals.

**Tasks:**
- Implement per `docs/specs/2026-05-09-diagnose-scene-feasibility.md`
- Tool registered as MCP-tool + as DATA_HANDLER in `tool_executor.py`
- Add `format_for_user(report)` Swedish/English summary
- Add `diagnose_layout_spec(spec_dict)` entrypoint per Opus review
- Output JSON + persisted to `workspace/feasibility_reports/{cp_id}_{timestamp}.json`

**Tests:**
- Unit: 10 cases per Opus review's test plan (out-of-reach goal, drop in obstacle, etc).
- Integration: run on all 86 canonicals in `workspace/templates/CP-*.json`. Classify each as `feasible | tightly_feasible | overconstrained | infeasible`. Verdict-distribution snapshot.
- Determinism: same scene + same `seed` → identical verdict + metric values to 4 decimals.

**Exit criterion:** all 22 ✗ canonicals classified. Distribution committed. Manual spot-check 5 random verdicts vs known issues — agreement.

**Why it gates next:** without classification we don't know which ✗ are scene-bugs (template fix) vs controller-bugs (planner-tune) vs platform-bugs (Mode B FJ etc).

---

### Phase 2 — Per-class triage + fixes (3-5 sessions)

**Goal:** drive 49 → ≥65 stable ✓ via targeted per-class work.

**Sub-phases (per Phase 1 classification):**

**2a. `infeasible` canonicals (template-fix):** rewrite template scene. Re-run `diagnose_scene_feasibility` until verdict ≠ infeasible. Then run simulate_traversal_check N=5. Any that pass = unlock. Any that don't = move to 2b/2c.

**2b. `overconstrained` canonicals (template-tune):** reposition obstacles, widen sensor zones, adjust drop_target. Same gate as 2a.

**2c. `tightly_feasible` canonicals (controller-tune):** these are auto-tune candidates — prep input for Phase 4 (scenario-profile).

**2d. `feasible` canonicals that still ✗ (controller-bug):** these are real platform bugs (Mode B FJ, drop precision, multi-robot relay completion). Targeted fixes per category. Each fix gated by N=5 multi-run.

**Tests:**
- Phase 2 entry: per-CP classification frozen.
- Per-fix: pre/post N=5 success-rate. Δ-success must be positive AND not regress any other CP. Compare against Phase 0 baseline.
- Multi-run consistency: ≥4/5 to claim "stable ✓".

**Exit criterion:** ≥16 of 22 newly ✓; ≤2 regressions tolerated (with explanation in commit message).

**Why it gates next:** Phase 4 (scenario-profile) only makes sense with concrete tuning data from Phase 2c. Implementing scenario-profile blind would just rebuild this session's sensor-gate misadventure.

---

### Phase 3 — Multimodal Block 1B (other session, held until Phase 0 done)

**Owner:** multimodal session. Per `multi-session-coordination.md`.

**Tasks:** role-based CP-01..05 refactor. Tested via existing form-gate + function-gate.

**Coordination:** controller-logic session must NOT touch CP-01..05 templates after Block 1B starts. shared `tool_executor.py` sectional ownership applies.

**Exit criterion:** Block 1B complete, function-gate maintains ≥ Phase 2 levels.

**Why it gates next:** Phase 4 (scenario-profile) reads role-based template features cleanly; pre-1B templates don't have `roles` field so profile-detection has to dual-path.

---

### Phase 4 — Scenario-profile controller config (2 sessions)

**Goal:** implement `2026-05-09-scenario-profile-controller-config.md` per its 11 Opus-review fixes. Migrate cuRobo + builtin handlers.

**Tasks:**
- Profile selector at `_gen_pick_place_*` install time
- Per-profile config dict (sensor_gate_factor, settle_ticks, scene_collision policy, lookahead_x, mutex_required)
- `ctrl:profile` attr written at install
- Profile selector unit-tests in `tests/test_scenario_profile.py` (synthetic profile dicts → branch assertion)
- Per-profile fixture canonical mapping committed

**Tests:**
- Unit: 5+ synthetic scenes per profile → correct branch.
- Integration: full canonical suite N=5. Each profile's fixture-canonical must remain ✓.
- Regression: any CP that was ✓ in Phase 2 must remain ✓ post-profile.

**Exit criterion:** ≥3 additional unlocks via auto-applied profile config. Zero regressions.

**Why it gates next:** Phase 5 (100% drive) leans on profile-aware retry. Without profiles, the 100% drive becomes per-CP hand-tuning forever.

---

### Phase 5 — 100% function-gate drive (2-3 sessions)

**Goal:** close remaining gap to 86/86. Multi-run statistical validation throughout.

**Tasks:**
- Iterate on remaining ✗ in priority order: highest-impact (Phase 1 verdict + Phase 2c + Phase 4 profile-coverage).
- For each: hypothesis → fix → N=5 verify → commit.
- Mark `expect_pass=False` for canonicals fundamentally outside test scope (CP-60 build-only, possibly CP-05 cylinder-flip if physics-tuning escapes session timing).
- Optional: NSGA-II auto-tune loop for `tightly_feasible` canonicals (per scenario-profile spec future-spur).

**Tests:**
- Final: N=10 across all 86. Stable ✓ = ≥8/10. Total stable ✓ count.
- Variance: per-CP std-dev across 10 runs. CPs with high variance flagged for further investigation.

**Exit criterion:** ≥80/86 stable ✓ OR explicit `expect_pass=False` for the ≤6 outside-scope.

**Why it gates next:** Phase 6 (M1 ROS2 production parity) introduces the first new canonical (CP-87) that depends on the in-Kit controllers we just stabilized. With existing 86 still flaky, no baseline to attribute new-CP failures against.

---

### Phase 6 — M1: ROS2 production parity (1-2 sessions)

**Spec:** `docs/specs/2026-05-09-industrial-expansion-spec.md` § Phase 6.

**Goal:** elevate ROS2 from "documented possible" to "scored as first-class architecture in direct_eval." Mostly repackaging — zero greenfield code.

**Tasks:**
- `setup_ros2_control_compat(robot_path, joint_states_topic, joint_commands_topic, controller_type)` — standard `topic_based_ros2_control` topic names.
- `emit_ros2_control_yaml(robot_path, controller_type, output_path)` — `colcon`-buildable YAML with `controller_manager` + `joint_state_broadcaster` + `joint_trajectory_controller`.
- `precheck_ros2_environment()` — verify AMENT_PREFIX_PATH, rosbridge port (default 9090), ROS_DOMAIN_ID consistency.
- New canonical: `CP-87-ros2-moveit2-franka-pickplace`.
- Update `PLAN.md` 4B + 8F + 9E to reflect new compat shipped.

**Tests:**
- Unit: tools emit standard topic names + valid YAML. Precheck reports correct status under (AMENT unset / port unbound / all good).
- Direct-eval: CP-87 scores ≥ 4/5 against live `ros2 launch` test rig.
- Regression: existing 86 CPs maintain Phase 5 success rate.

**Exit criterion:** 3 new tools shipped + CP-87 ships with non-zero direct-eval. PLAN.md updated. Library: 86 → 87.

**Why it gates next:** without M1's compat layer, multimodal session (Phase 7) cannot demonstrate the agent picking ROS2 modes correctly, and Phase 8 bridge work (M2-M3) lacks the scaffolding to combine bridge + ROS2-control in CP-NEW-plc-conveyor.

---

### Phase 7 — Multimodal Block 2 + 3 (multimodal session, parallel)

**Owner:** multimodal session. Per `multi-session-coordination.md`.

**Tasks:** text-prompt + sketch/photo modalities. Use `diagnose_scene_feasibility` as agent pre-flight. (Was Phase 6 in the pre-industrial-expansion plan.)

**Coordination:** runs parallel to Phase 6 (M1) since the multimodal session and controller-logic session edit disjoint files. No blocking dependency between them.

**Exit criterion:** Block 2 + 3 complete; multimodal demos show text → scene + sketch → scene flows working.

---

### Phase 8 — M2-M3 bridges + Top-5 yrkesroll-canonicals (5-7 sessions)

**Spec:** `docs/specs/2026-05-09-industrial-expansion-spec.md` § Phase 8.

**Goal:** ship two protocol bridges (Modbus + OPC-UA) + 5 yrkesroll-canonicals each unlocking a different role. Library: 87 → 93+.

**Tasks (controller-logic):**
- `modbus_tcp_bridge_attach(host, port, register_map, mode, rate_hz)` — pymodbus, supervised subprocess via `dispatch_async_task`.
- `opcua_bridge_attach(server_url, tag_to_attribute_map, rate_hz)` — asyncua, same supervised pattern.
- `diagnose_modbus_bridge` + `diagnose_opcua_bridge` — honesty-tier counterparts.
- 7 new canonicals:
  - CP-NEW-plc-conveyor (M2)
  - F-02-promoted (M3)
  - CP-NEW-g1-bimanual-tabletop (Top-5 ML/RL)
  - CP-NEW-rl-clone-env (Top-5 ML/RL)
  - CP-NEW-amr-pickup-handoff (Top-5 logistics)
  - CP-NEW-drawer-open (Top-5 robotics R&D)
  - CP-NEW-peg-in-hole (Top-5 robotics R&D, contact-rich)

**Tests:**
- Unit: each bridge tool launches subprocess, tears down cleanly on Kit shutdown.
- Integration: M2 conveyor pauses ≤100ms after Modbus coil flip, resumes ≤100ms after clear (timestamped state log).
- Direct-eval: each new canonical scores ≥ 4/5.
- Regression: existing 87 CPs maintain Phase 6 success rate.

**Exit criterion:** 4 new tools + 7 new canonicals shipped. Library: 87 → 94. Bridge subprocess lifecycle verified zombie-free.

**Why it gates next:** Phase 9's controller-shootout needs the same tool/canonical surface to compare against. Without Top-5 + bridges, controller-shootout has nothing to score.

---

### Phase 9 — M4 cuMotion-MoveIt + Top 6-15 yrkesroll (4-6 sessions)

**Spec:** `docs/specs/2026-05-09-industrial-expansion-spec.md` § Phase 9.

**Goal:** ship `setup_isaac_ros_cumotion_moveit` + 10 more yrkesroll-canonicals + the controller-shootout artefact (`controller_shootout_ros2.json`) covering 9 in-Kit modes + 1 external (`ros2_cmd + cumotion`).

**Tasks (controller-logic):**
- `setup_isaac_ros_cumotion_moveit(robot_path, planner_topic="/move_group/plan")` — wires NVIDIA `isaac_ros_cumotion_moveit` package.
- Re-run S-01, S-02, S-09, S-11 in `ros2_cmd + cumotion` mode for shootout artefact.
- 10 new canonicals (Top 6-15 from yrkesroll spec): 3-station OEE, inspect-reject, defect-SDG, Y-merge, DR-curriculum, multi-cam triangulation, controller-shootout, PLC-fixture, sim2real-gap, cross-belt sorter.

**Tests:**
- Controller-shootout artefact: `workspace/scenario_results/controller_shootout_ros2.json` with non-empty rows for 10 modes × scenarios. Missing combinations flagged with reason.
- Direct-eval: each new canonical scores ≥ 4/5.

**Exit criterion:** 1 new tool + 10 new canonicals (94 → 104). Controller-shootout artefact published.

**Why it gates next:** Phase 10 wraps the long tail; M5 is opportunistic. Without M4's shootout + Top 6-15, Phase 10's M5 add-ons have nothing strategic to bolt onto.

---

### Phase 10 — M5 + Top 16-20 yrkesroll + Multimodal Block 4-5 (3-5 sessions)

**Spec:** `docs/specs/2026-05-09-industrial-expansion-spec.md` § Phase 10.

**Goal:** finish industrial-track tail + multimodal foundation. Library: 104 → 110+.

**Tasks (controller-logic):**
- `openplc_runtime_attach` (M5, P3, opportunistic) — convenience wrapper over Modbus-TCP bridge. ~50 LOC + 1 CP.
- `mqtt_sparkplug_bridge_attach` (M5, P2, opportunistic) — Sparkplug B; warehouse-twin pattern. ~250 LOC + 1 CP.
- 5 new canonicals (Top 16-20): multi-AMR corridor, CAD revision-drift, operator ergonomics, tactile insertion (TacEx), brick-stacking baseplate.

**Tasks (multimodal session, parallel):**
- Block 4 (canvas wiring) — conditional per multimodal-foundation-spec §22.
- Block 5 (hardening) — final integration. Canvas SPA wired through canonical pipeline.

**Tests + exit criterion:** 2 new tools + 7 new canonicals (5 from Top 16-20 + 2 from M5). Library: 104 → 111. Multimodal canvas demo runnable. All direct-eval baselines refreshed.

**Why it's last:** opportunistic adds + role-coverage tail; nothing downstream depends on it. Yrkesroll Top 16-20 are explicitly cuttable if running short (drop tactile + brick-stacking first; keep multi-AMR + CAD-drift + operator-ergonomics).

---

## Cross-cutting: testing infrastructure

Build these once in Phase 0 + Phase 1, reuse everywhere:

| Tool | Purpose | Path |
|---|---|---|
| `simulate_traversal_check_n_runs(N, seed)` | Multi-run aggregation | `tool_executor.py` |
| `baseline_compare.py` | Diff current run against frozen baseline | `scripts/qa/` |
| `scripts/qa/multi_run_regression.py` | N=5 across all CPs, summary report | `scripts/qa/` |
| `format_diagnose_for_chat()` | Human-readable feasibility report | `tool_executor.py` (with diagnose) |
| `feasibility_baseline.py` | Frozen feasibility-verdict per CP | `scripts/qa/` |

## Cross-cutting: diagnostic infrastructure

Already in place this session, keep/extend:

- `ctrl:phase`, `ctrl:cubes_delivered`, `ctrl:cycles_attempted`, `ctrl:tick_count`, `ctrl:error_count`, `ctrl:last_error` (controller-runtime)
- `simulate_traversal_check` `cube_final` + `per_cube_status` (delivery-truth)

To add per Phase 1+2:
- `ctrl:profile` (Phase 4)
- `feasibility_report.json` per scene-build (Phase 1)
- `simulate_traversal_check` accepts `seed` (Phase 0)

## Cross-cutting: coordination protocol

Update `multi-session-coordination.md` AT EACH PHASE BOUNDARY. The doc is short by design — each phase changes 2-3 lines (current phase, current owner, blocking dependencies).

## Realistic time estimate

| Phase | Sessions | Notes |
|---|---|---|
| 0 — Stabilize baseline | 1-2 | seed/n_runs + baseline_compare |
| 1 — diagnose_scene_feasibility | 2-3 | classify all 86 |
| 2 — Per-class triage | 3-5 | 49 → ≥65 stable ✓ |
| 3 — Multimodal Block 1B | (multimodal track) | parallel |
| 4 — Scenario-profile config | 2 | per-profile branching |
| 5 — 100% function-gate drive | 2-3 | ≥80/86 stable ✓ on existing 86 |
| **6 — M1 ROS2 production parity** | **1-2** | **3 tools + CP-87** |
| 7 — Multimodal Block 2 + 3 | (multimodal track) | parallel to 6 |
| **8 — M2-M3 bridges + Top-5 yrkesroll** | **5-7** | **4 tools + 7 canonicals (94 total)** |
| **9 — M4 cuMotion-MoveIt + Top 6-15** | **4-6** | **1 tool + 10 canonicals (104 total)** |
| **10 — M5 + Top 16-20 + Multimodal Block 4-5** | **3-5** | **2 tools + 7 canonicals (111 total)** |

Plus integration: ~2 sessions for handoff/QA between phases.

**Total controller-logic track:** 23-31 sessions (was 12-15 before industrial-expansion).
**Total multimodal track (parallel):** Block 1B-5 ≈ 10-15 sessions.
**Critical path:** Phase 0 → 1 → 2 → 4 → 5 → 6 → 8 → 9 → 10 (sequential within controller-logic). Phases 3 + 7 + part of 10 run parallel on multimodal track.

**Library size growth:** 86 → 111 canonicals (+29%) by end of Phase 10. New tools added: ~9 industrial-bridge primitives + ~1300 LOC.

## Risk register

1. **Phase 0 reveals deeper stochasticity than expected** (e.g. 8/25 are flaky, not 2/25). Mitigation: extend Phase 0 with seed-pinning + cuda-graph-determinism investigation. Add 1 session.
2. **Phase 1 reveals diagnose tool too slow (>5s per scene).** Mitigation: cache reachability map per robot config; sub-second possible.
3. **Phase 2c canonicals don't respond to scenario-profile (Phase 4 doesn't unlock them).** Mitigation: NSGA-II auto-tune as fallback; expect 2-extra sessions.
4. **Block 1B template format change breaks Phase 1+4 selectors.** Mitigation: dual-path support in selectors (per Block 1A spec); coordination doc tracks the format-change boundary.
5. **Coordination doc divergence between sessions.** Mitigation: PR-comment when one session edits the other's owned files.

## Definition of done (whole project)

**Controller-logic correctness (Phase 0-5):**
- 86/86 stable ✓ on canonical function-gate (or ≤6 explicit `expect_pass=False`)
- `diagnose_scene_feasibility` returns verdict <2s per scene, used in agent pre-flight + canonical CI
- Scenario-profile branches selected automatically; per-profile fixture canonicals exist

**Industrial-expansion (Phase 6, 8, 9, 10):**
- ROS2 a first-class direct-eval target (CP-87 + per-shootout coverage in artefact)
- Modbus-TCP + OPC-UA bridge primitives ship; subprocess lifecycle zombie-free
- Library: 86 → 111 canonicals across 8 yrkesroller (Top-20 from yrkesroll spec)
- Controller-shootout artefact (`workspace/scenario_results/controller_shootout_ros2.json`) covers 10 modes
- `PLAN.md` 4B + 8F + 9E updated to reflect ROS2/bridge surface

**Multimodal:**
- Block 5 complete (Block 4 conditional — depends on canvas-strategic decision per multimodal-foundation-spec §22)

**Coordination:**
- Coordination doc reflects post-merge state with no open ownership conflicts

