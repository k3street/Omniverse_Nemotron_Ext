# Master Execution Plan — Multimodal × Controller-Logic × Diagnostic

**Date:** 2026-05-09
**Status:** active
**Purpose:** authoritative ordering, testing, evaluation, and diagnostic strategy across all in-flight specs. Single source of truth for "what comes next, why, and how we know it worked".

---

## Anchors (read these first if you only have 5 minutes)

- `docs/specs/2026-05-08-multimodal-foundation-spec.md` — multimodal arch (Block 1-5)
- `docs/specs/2026-05-09-block-1a-status.md` — what already shipped in multimodal Block 1A
- `docs/specs/2026-05-09-multi-session-coordination.md` — file ownership split
- `docs/specs/2026-05-09-scenario-profile-controller-config.md` — branched controller config
- `docs/specs/2026-05-09-diagnose-scene-feasibility.md` — pre-flight validator
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

---

### Phase 6 — Multimodal Block 2 + 3 (multimodal session)

Text-prompt + sketch/photo modalities. Use `diagnose_scene_feasibility` as agent pre-flight. Coordination via `multi-session-coordination.md`.

---

### Phase 7 — Block 4 (canvas wiring) + Block 5 (hardening)

Final integration phases. Canvas SPA wired through canonical pipeline.

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

| Phase | Sessions | Calendar |
|---|---|---|
| 0 | 1-2 | 1 day |
| 1 | 2-3 | 2-3 days |
| 2 | 3-5 | 4-7 days |
| 3 | (multimodal session) | parallel |
| 4 | 2 | 2 days |
| 5 | 2-3 | 2-3 days |
| 6+7 | (multimodal session) | parallel |

Plus integration: ~2 sessions for handoff/QA between phases.

**Total controller-logic track:** 12-15 sessions / ~15-20 calendar days.
**Total multimodal track (parallel):** Block 1B-5 ≈ 10-15 sessions.
**Critical path:** Phase 0 → Phase 1 → Phase 2 → Phase 4 → Phase 5 (this is sequential per controller-logic ownership; can't parallelize within track).

## Risk register

1. **Phase 0 reveals deeper stochasticity than expected** (e.g. 8/25 are flaky, not 2/25). Mitigation: extend Phase 0 with seed-pinning + cuda-graph-determinism investigation. Add 1 session.
2. **Phase 1 reveals diagnose tool too slow (>5s per scene).** Mitigation: cache reachability map per robot config; sub-second possible.
3. **Phase 2c canonicals don't respond to scenario-profile (Phase 4 doesn't unlock them).** Mitigation: NSGA-II auto-tune as fallback; expect 2-extra sessions.
4. **Block 1B template format change breaks Phase 1+4 selectors.** Mitigation: dual-path support in selectors (per Block 1A spec); coordination doc tracks the format-change boundary.
5. **Coordination doc divergence between sessions.** Mitigation: PR-comment when one session edits the other's owned files.

## Definition of done (whole project)

- 86/86 stable ✓ on canonical function-gate (or ≤6 explicit `expect_pass=False`)
- `diagnose_scene_feasibility` returns verdict <2s per scene, used in agent pre-flight + canonical CI
- Scenario-profile branches selected automatically; per-profile fixture canonicals exist
- Multimodal Block 5 complete (Block 4 conditional — depends on canvas-strategic decision per spec §22)
- Coordination doc reflects post-merge state with no open ownership conflicts

