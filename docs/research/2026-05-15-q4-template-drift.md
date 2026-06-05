# Q4: Template Drift — Authoring Cohorts, Verification Status, and Remediation Plan

**Date:** 2026-05-15
**Researcher:** Sonnet agent (Phase 2 / Question 4)
**Prior context:**
- `docs/research/2026-05-15-q2-canonical-format.md` — field-presence table (DO NOT rebuild)
- `docs/research/2026-05-15-q3-retrieval-quality.md` — 30 ghost TP-* IDs
**Scope:** 321 templates in `workspace/templates/`

---

## 1. Authoring-Cohort Timeline

All 321 templates were authored by a single git author (Anton, antonbj3@msn.com). There is no multi-author disagreement; the drift observed is intra-session schema evolution, not cross-author divergence.

### 1.1 Commit Activity by Date

| Date | Commits to templates/ | Templates Added | Session Character |
|------|----------------------|-----------------|-------------------|
| 2026-04-18 | 15 | 193 (batch) | Phase 12 QA corpus — 163 specs + 173 templates in one shot (commit `7e65b27`) |
| 2026-04-19 | 11 | 19 | Per-task additions (AD-16..23, D-13/14, K-12, L-11, M-14..17, P-12, S-12, T-14) |
| 2026-05-04 | 1 | 1 | CP-01 first draft (commit `3445b0b`) |
| 2026-05-06 | 3 | 1 | CP-01 canonical verified + CP-02 added (commits `1211ee3`, `c921239`) |
| 2026-05-07 | 20 | 7 | CP-03..09 added, settle_state migrations, form-gate runs (commits `46fafe1`..`ed80bc5`) |
| 2026-05-08 | 95 | 77 | Main canonical sprint — CP-10..87, all UR10 variants, vision CPs (commits `1093970`..`5a1ee7b`) |
| 2026-05-09 | 19 | 0 | Function-gate unlock sweep — batch patches to existing CPs, no new files |
| 2026-05-10 | 25 | 23 | CP-NEW-* yrkesroll drafts (20 of them), CP-87, industrial bridge CPs (commits `0ea366c`..`3b3c86a`) |
| 2026-05-11 | 15 | 0 | Block 1B role-template fields grafted onto CP-01..05 (commit `2744bb6`); overnight sweep patches |

**Total commits touching templates/: 204**
**Total templates added: 321** (193 T2 batch + 128 T1 CP additions)

### 1.2 Cohort Definitions

**Cohort A — Phase 12 QA Corpus (2026-04-18):** 193 T2 templates added in a single commit `7e65b27`. These are persona-task templates with the minimal 6-field schema (`task_id`, `goal`, `tools_used`, `thoughts`, `code`, `failure_modes`). Homogeneous shape. Authored under time pressure to reach the 163-spec QA corpus target.

**Cohort B — QA Patch Wave (2026-04-19):** 19 T2 templates added across 11 commits as individual QA tasks were refined (adversarial tasks AD-16..23, measurement tasks D-13/14, etc.). Same 6-field schema as Cohort A; same quality.

**Cohort C — CP Pioneer Sprint (2026-05-04..07):** CP-01..09 added across 4 days. Schema evolved visibly across this cohort: CP-01 gained `benchmark_vs_alternatives` + `verified_date` + `verified_metrics` (unique to it); CP-03..05 gained `intent`/`roles`/`role_defaults`; settle_state added via patch commits. This is the highest-drift cohort.

**Cohort D — Main Canonical Marathon (2026-05-08):** 77 CPs added in a single day (`1093970`..`5a1ee7b`). Established the stable 13-field T1 schema. Most templates born with `verified_status: "build-spec-2026-05-08; form-gate verification pending"` — mass-authored then retroactively updated as verification ran. Some remain pending to this day (see §2).

**Cohort E — Yrkesroll Draft Wave (2026-05-10):** 22 CP-NEW-* templates added across 3 commits (`0ea366c`, `b76ce47`, `3b3c86a`), plus CP-87 via `0a510e6`. All admitted as explicit drafts, most with `stable_fail` or `BUILD_OK` status. Admitted under a "scaffold first" policy.

**Cohort F — Block 1B Role Graft (2026-05-11):** Not a new-file addition — commit `2744bb6` grafted `intent`/`roles`/`role_defaults`/`code_template`/`verify_args_template`/`simulate_args_template` onto CP-01..05, making them the lone super-shape.

---

## 2. Shippable vs Draft Status

### 2.1 Status Category Counts

For the 109 T1 CP templates (the only ones with a `verified_status` field):

| Status Category | Count | Definition |
|-----------------|-------|-----------|
| **Function-gate verified (stable_ok)** | 58 | `verified_status` contains `function-gate ✓` — delivery confirmed in Kit RPC sim |
| **Form-gate pending** | 15 | `verified_status: "build-spec-2026-05-08; form-gate verification pending"` — build ran, no gate run |
| **Build-only / smoke-ok** | 14 | `BUILD_OK` or `smoke-test ✓` with no delivery evidence (CP-NEW-* only) |
| **Function-gate explicitly failed** | 6 | `function-gate ✗` — known root cause documented, not yet fixed |
| **Stable_fail** | 8 | `stable_fail` — build runs but physics/controller prevents delivery |
| **Form-gate only (benchmarks)** | 2 | Form-gate passed; function-gate data exists but is failure-documenting (CP-28, CP-29) |
| **Physics-tuning-required** | 1 | CP-05 — passive flip geometry sensitivity |
| **Blocked** | 1 | CP-06 — infrastructure built, controller delivery never confirmed |
| **Experiment / false positive** | 1 | CP-58 — earlier ✓ retracted as false positive |
| **Draft (explicit)** | 1 | CP-87 — single template with `verified_status: "draft"` |
| **No verified_status field** | 0 | None missing (CP-06 is the one exception but it has `blocked` instead) |

For the 212 T2 templates:

| Status Category | Count |
|-----------------|-------|
| No `verified_status` field (by design — T2 schema) | 212 |
| Appear in at least one QA campaign run | 110 |
| Never appear in any QA run | 102 |

For the 5 CP-01..05 role-based templates: all are `function-gate ✓`.

**Overall shippable count (production-ready):** 63 templates (58 fn-verified CPs + 5 role-based CPs)

### 2.2 Sample Per Status

**Function-gate verified (sample):**
- `workspace/templates/CP-01.json` — Franka cuRobo 4-cube pick-place; 180s sim confirmed; `build-spec-2026-04-XX; form-gate ✓; function-gate ✓`
- `workspace/templates/CP-26.json` — belt-to-belt handoff; `function-gate ✓ (belt-pause pre-step subscribe)`
- `workspace/templates/CP-55.json` — drawer-open station; `function-gate ✓ (drawer extends + Drawer prim is target)`

**Form-gate pending (sample):**
- `workspace/templates/CP-22.json` — high-speed belt stress test; `function-gate likely lower delivery rate than CP-01 due to high speed`
- `workspace/templates/CP-51.json` — robot-to-robot handoff via fixed-point marker; no gate run
- `workspace/templates/CP-76.json` — dual-robot fixture hold; `form-gate verification pending`

**Stable fail (sample):**
- `workspace/templates/CP-NEW-brick-stacking.json` — persistent PhysX numerical explosion (cube velocity blows to >200k m/s), 3 variants tried
- `workspace/templates/CP-NEW-drawer-open.json` — pick-place controller cannot drive PrismaticJoint; needs constraint-aware controller
- `workspace/templates/CP-NEW-g1-bimanual-tabletop.json` — G1 SimReady asset missing on local Nucleus; gate stable_fail

---

## 3. Structural-Shape Cluster Report

### 3.1 All 321 Templates by JSON Key Shape

| Cluster | Size | Key Signature | Representative |
|---------|------|---------------|----------------|
| **T2 Standard** | 212 | `{code, failure_modes, goal, task_id, thoughts, tools_used}` | `A-01.json` |
| **T1 Settled** | 78 | T2 + `{diagnose_args, extends, extension_notes, settle_state, simulate_args, verified_status, verify_args}` | `CP-09.json` |
| **T1 Unsettled** | 22 | T1 Settled minus `settle_state` | `CP-87.json` |
| **T1 Role v1** | 3 | T1 Settled + `{code_template, intent, role_defaults, roles, simulate_args_template, verify_args_template}` | `CP-03.json` |
| **T1 Role v2 (with verified_date)** | 1 | T1 Role v1 + `{extends, verified_date, verified_metrics}` minus `extends` | `CP-02.json` |
| **T1 Pioneer (richest)** | 1 | T1 Role v2 + `{benchmark_vs_alternatives, verified_date, verified_metrics}` minus `extends` | `CP-01.json` |
| **T1 Blocked** | 1 | `{blocked, code, cube_path, delivery, diagnose_args, extension_notes, failure_modes, goal, simulate_args, task_id, thoughts, tools_used, verify_args}` | `CP-06.json` |
| **T1 Early-legacy** | 1 | T1 Settled + `{cube_path, delivery, extension_notes}` minus `diagnose_args, extends` | `CP-07.json` |
| **T1 Memo-patched** | 1 | T1 Settled + `{compute_stack_placement_verified_2026_05_07}` | `CP-08.json` |
| **T1 Typo variant** | 1 | T1 Unsettled + `{extends_notes}` (alongside `extension_notes`) | `CP-NEW-multi-amr-corridor.json` |

**Total clusters: 10** (1 massive T2 cluster + 9 CP sub-clusters)

### 3.2 Rare Clusters Flagged for Re-Author Review

The following 5 single-template shapes each carry one-off deprecated fields (confirmed in Q2). All are re-author or mechanical-cleanup candidates:

| Template | Unique Fields | Issue |
|----------|--------------|-------|
| `CP-01.json` | `benchmark_vs_alternatives`, `verified_date`, `verified_metrics` | These three fields appear nowhere else; content belongs in docs, not template schema |
| `CP-02.json` | `verified_date`, `verified_metrics` | Partial copy of CP-01 experiment; missing `extends` |
| `CP-06.json` | `blocked`, `cube_path`, `delivery` | Experiment fields; should stay until unblocked, then migrate to T1 Settled |
| `CP-07.json` | `cube_path`, `delivery` | Leftover from CP-06 era; `cube_path` duplicates info in `simulate_args.cube_path` |
| `CP-08.json` | `compute_stack_placement_verified_2026_05_07` | Date-stamped memo field; belongs in `extension_notes` or `thoughts` |
| `CP-NEW-multi-amr-corridor.json` | `extends_notes` (typo of `extension_notes`) | Typo; content is duplicated in `extension_notes` |

### 3.3 T1 Unsettled vs T1 Settled Gap

The 22 "T1 Unsettled" templates (missing `settle_state`) are all Cohort E (CP-NEW-* drafts) plus CP-87. The lack of `settle_state` is not a typo — these templates were authored before it was clear what settle criteria they needed, given their `stable_fail` or `BUILD_OK` status. They need `settle_state` added before promotion to production.

---

## 4. Decision Per Cohort

### 4.1 Cohort A — Phase 12 QA Corpus (193 T2 templates)

**Verdict: MIGRATE MECHANICALLY (for the 110 QA-run templates) + RE-AUTHOR for the 102 never-run.**

- All 212 T2 templates share a single shape. No structural migration needed. They do not need `verified_status` (T2 schema omits it by design, per Q2).
- **110 QA-run templates** are exercised. These are shippable as-is for persona-eval use. Mechanical migration: add `verified_status: "qa-run-confirmed"` only if the field is adopted as T2 policy (currently not planned).
- **102 never-run templates** have never been exercised in any QA campaign. They are drafts in practice even though they carry no explicit draft marker. Recommended action: run each through at least one QA campaign pass before marking shippable. Until then they are "soft drafts."

Never-run templates by prefix (total: 102):
`A (2), AL (7), AM (7), D (8), E (6), F (6), J (7), K (7), L (6), M (8), P (7), R (8), S (8), T (8), Y (7)`

Pattern: for every prefix group of ~10 templates, tasks numbered 4+ were never run. The first 3 tasks of each persona were the most-exercised (often 80–120 runs each). Late-in-series tasks (e.g., `D-08..D-11`) are dead weight until exercised.

**Risk:** Low. T2 templates have no executable gate; a bad template just produces a bad agent answer, no system breakage.
**Effort:** ~1 agent-hour per prefix group to run a sweep campaign (15 groups × 1h = 15 agent-hours).

### 4.2 Cohort B — QA Patch Wave (19 T2 templates, 2026-04-19)

**Verdict: MIGRATE MECHANICALLY (identical shape, all QA-run).**

All 19 templates (AD-16..23, D-13/14, K-12, L-11, M-14..17, P-12, S-12, T-14) have appeared in QA campaigns. No structural issues. No action required beyond the T2-wide migrate path.

**Risk:** Negligible. **Effort:** Near-zero (already exercised).

### 4.3 Cohort C — CP Pioneer Sprint (CP-01..09, 2026-05-04..07)

**Verdict: SELECTIVE RE-AUTHOR for the deprecated-field holders (CP-01, CP-02, CP-07, CP-08); KEEP as-is for the rest.**

- **CP-01:** Richest shape. `benchmark_vs_alternatives`, `verified_date`, `verified_metrics` can be extracted to `docs/templates/CP-01-benchmark.md` and the template stripped to the standard T1 Role shape. Low urgency — CP-01 is stable_ok and widely referenced.
- **CP-02:** Same `verified_date`/`verified_metrics` fields. Also missing `extends` (unlike CP-03..05). Mechanical: add `extends: "CP-01"` and remove the two deprecated fields.
- **CP-07:** Has `cube_path` and `delivery` legacy fields from the CP-06 experiment era (committed `3d0cda4`). Mechanical strip.
- **CP-08:** Has `compute_stack_placement_verified_2026_05_07`. Mechanical: move text to `extension_notes`.
- **CP-03..05, CP-06, CP-09:** Acceptable; CP-06 stays blocked until unblocked.

**Risk:** Low — these are function-gate verified; stripping deprecated fields does not affect execution.
**Effort:** 0.5 agent-hours (4 templates, mechanical edits).

### 4.4 Cohort D — Main Canonical Marathon (CP-10..87, 2026-05-08)

This cohort splits into three sub-groups:

**Sub-group D1 (58 function-gate verified): KEEP. No migration needed.** These are stable_ok, have the standard T1 Settled or T1 Unsettled shape. The `settle_state` field is missing from ~7 early-numbered ones (CP-87 etc.) but this does not affect production behavior for already-verified CPs.

**Sub-group D2 (15 form-gate pending): RE-AUTHOR or ACCEPT-AS-PARTIAL.**
Templates: CP-22, CP-35, CP-37, CP-40, CP-46, CP-48, CP-51, CP-52, CP-53, CP-57, CP-60, CP-62, CP-65, CP-68, CP-76.
All carry `verified_status: "build-spec-2026-05-08; form-gate verification pending"` — unchanged since May 8. They were added with build evidence but never re-tested. Two options:
- Run the form-gate and upgrade their `verified_status` (1 Kit session per template, ~0.5h each = 7.5 agent-hours)
- Accept them as "build-verified, no delivery guarantee" — useful as reference patterns but not as performance baselines.

**Sub-group D3 (6 function-gate ✗ and 2 form-gate-only):**
- CP-58, CP-73, CP-74, CP-80: known root-cause bugs (belt-pause-from-callback, peg-in-hole false positive). **Keep as documented failure references**; do not claim verified.
- CP-84, CP-85: drop-precision failures. **Keep as precision-benchmark documentation.**
- CP-28, CP-29: precision experiment templates. **Keep as benchmarks**, not promoted to stable_ok.

**Risk for D2:** Medium. If form-gate is run and fails, `verified_status` must be updated to reflect failure. Do not leave as "pending" forever — it's misleading.
**Effort D2:** 7.5 agent-hours if all are re-run; near-zero if accepted as partial.

### 4.5 Cohort E — Yrkesroll Draft Wave (22 CP-NEW-*, 2026-05-10)

This is the highest-drift cohort relative to the stable T1 schema.

**Sub-group E1 (7 smoke-ok / build-ok templates): MIGRATE MECHANICALLY.**
Templates: CP-NEW-3station-oee, CP-NEW-cad-revision-drift, CP-NEW-controller-shootout-cp, CP-NEW-dr-curriculum, CP-NEW-inspect-reject, CP-NEW-multi-cam-triangulation, CP-NEW-y-merge-singulation.
All have `smoke-test ✓` but no function-gate. Need:
1. `settle_state` field added (they are T1 Unsettled)
2. Function-gate run to promote to stable_ok
**Risk:** Low. Mechanical + 1 Kit session each.
**Effort:** 3 agent-hours (7 templates).

**Sub-group E2 (3 plumbing-only templates): MIGRATE MECHANICALLY.**
Templates: CP-NEW-opcua-12conveyors, CP-NEW-plc-conveyor, CP-NEW-plc-fixture.
`BUILD_OK; plumbing-only (no cube delivery, no simulate_args)`. These document bridge-setup workflows; they legitimately lack `simulate_args` (no cube delivery to measure). They should be formally marked as `plumbing-only` in their shape (not expected to reach function-gate). Add `settle_state: null` and update `verified_status` to reflect plumbing-only intent.
**Effort:** 0.5 agent-hours.

**Sub-group E3 (4 asset-blocked templates): KEEP AS DRAFTS until assets arrive.**
Templates: CP-NEW-g1-bimanual-tabletop, CP-NEW-operator-ergonomics, CP-NEW-rl-clone-env, CP-NEW-sim2real-gap.
Blocked by missing assets (G1 SimReady, OperatorAvatar SimReady, RSL-RL, real rosbag). Cannot be verified until the assets are available.
**Verdict: Do not delete; add `draft: true` / `asset_dependency` notes. Re-evaluate when assets land.**

**Sub-group E4 (8 stable_fail templates): RE-AUTHOR or DOWNGRADE.**
Templates: CP-NEW-amr-pickup-handoff, CP-NEW-brick-stacking, CP-NEW-cross-belt-sorter, CP-NEW-drawer-open, CP-NEW-peg-in-hole-single, CP-NEW-tactile-insertion, plus CP-NEW-operator-ergonomics and CP-NEW-g1-bimanual-tabletop (already in E3).
Root causes are known: PhysX numerical instability on brick/peg, missing constraint-aware controller for drawer/AMR, missing robot scene for cross-belt.
Options:
  - Re-author with workarounds (e.g., FixedJoint grip for peg-in-hole, like CP-55/CP-58)
  - Accept as "documented failure templates" with explicit `expected_status: stable_fail` field
  - Delete if there is a better existing canonical (CP-58 already covers peg-in-hole)

The only clear delete candidate here is **CP-NEW-peg-in-hole-single** — it is a strict subset of CP-58, shares the same PhysX instability, and CP-58 already documents the failure mode.
**Effort for E4:** 4 agent-hours for re-authoring viable ones (brick-stacking most promising via FixedJoint); near-zero for keep-as-documented.

### 4.6 Cohort F — Block 1B Role Graft (CP-01..05, 2026-05-11)

**Verdict: KEEP AS-IS.** The role graft is clean. Five templates have a super-shape but it is internally consistent (all 5 have all 6 role fields). The only issue is that CP-03..05 lack `verified_date`/`verified_metrics` which CP-01/02 have. This is fine — CP-01 and CP-02 are the reference implementations.

---

## 5. Templates to Delete

### 5.1 Ghost Corpus — 30 TP-* IDs (Q3 finding, no files)

The `role_template_index.py` (commit unknown, file exists at `service/isaac_assist_service/multimodal/role_template_index.py`) registers 30 TP-* template IDs in `ROLE_TEMPLATE_INDEX`:

```
TP-WLD-01..04, TP-PCK-01..04, TP-ASM-01..03, TP-INS-01..03,
TP-PAL-01..03, TP-MCT-01..03, TP-PKR-01..03, TP-AMR-01..03,
TP-DSP-01..02, TP-KIT-01..02
```

**None of these 30 files exist** in `workspace/templates/`. Verified by listing:
```
ls workspace/templates/TP-*.json → no matches
```

The `retrieve_template_by_role` handler (via `RoleRetriever`) returns these `template_id` strings to the LLM as if they were loadable templates. If anything downstream tries to load a TP-* template from disk, it will fail silently or throw. These are **ghost corpus entries** — they should either be backed by actual template files (requiring authoring 30 new templates) or the `ROLE_TEMPLATE_INDEX` entries should be repointed to existing CP templates (e.g., TP-WLD-01 → CP-54 for surface_gripper, TP-PAL-01 → CP-08 for palletizer).

**Recommended action:** Do NOT delete the registry entries — delete is the wrong frame. Instead, map each TP-* ID to the closest existing CP template and either alias or rewrite the registry. This is a re-author task, not a delete task.

### 5.2 Concrete Delete Candidates

The following templates are candidates for deletion:

| Template | Path | Reason | Confidence |
|----------|------|--------|-----------|
| `CP-NEW-peg-in-hole-single.json` | `workspace/templates/CP-NEW-peg-in-hole-single.json` | Strict subset of CP-58 (same PhysX instability, same root cause); no unique coverage | High |
| `CP-NEW-multi-amr-corridor.json` | `workspace/templates/CP-NEW-multi-amr-corridor.json` | Has `extends_notes` typo field alongside `extension_notes`; duplicate content. If kept, needs mechanical fix. Can be merged with CP-NEW-amr-pickup-handoff scope | Medium |

No other templates meet the delete bar. The rationale:
- Stable_fail templates with known root causes are valuable documentation; do not delete.
- Never-run T2 templates have no verified content but are cheap to keep (6-field schema, low maintenance burden).
- CP-28/CP-29 (precision experiment failures) are explicitly labeled as benchmarks; they serve a documentary purpose.

### 5.3 Templates That Should Be Clearly Downgraded (Not Deleted)

These templates currently claim verification but the evidence is contested or partial:

| Template | Current Status Claim | Actual Evidence | Recommended Status Update |
|----------|---------------------|----------------|--------------------------|
| `CP-59.json` | `build-attempted-2026-05-08; build 51/52, form-gate FAIL` | Vision heap geometry causes 0 detections | Already honest — keep as-is |
| `CP-61.json` | `form-gate expected to fail (Cortex doesn't install pick-place controller)` | Build-only; Cortex infrastructure limitation documented | Already honest |
| `CP-67.json` | `form-gate verification likely fails (rotary disc bridge issue)` | Not confirmed either way | Needs a fresh form-gate run or deletion |
| `CP-73.json` | `function-gate ✗ (Cortex+conveyor — multi-cube limitation + belt-pause bug)` | Two known bugs blocking; unlikely to pass without handler fix | Keep as documented failure |

---

## 6. Risk and Effort Summary

| Cohort | Templates | Action | Estimated Effort | Risk |
|--------|-----------|--------|-----------------|------|
| A (T2 Phase 12 corpus) | 110 QA-run | Keep, no migration needed | 0 | None |
| A (T2 never-run) | 102 | Run QA sweep campaigns | 15 agent-hours | Low |
| B (QA Patch Wave T2) | 19 | Keep | 0 | None |
| C (CP Pioneer, deprecated fields) | 4 (CP-01,02,07,08) | Mechanical strip of deprecated fields | 0.5 agent-hours | Low |
| D1 (CP fn-verified) | 58 | Keep | 0 | None |
| D2 (CP form-pending) | 15 | Re-run form-gate OR accept as partial | 7.5 OR 0 agent-hours | Medium if not run |
| D3 (CP fn-failed / experiments) | 8 | Keep as documented failure refs | 0 | None |
| E1 (CP-NEW smoke-ok) | 7 | Add settle_state + run function-gate | 3 agent-hours | Low |
| E2 (CP-NEW plumbing-only) | 3 | Mark as plumbing-only | 0.5 agent-hours | Low |
| E3 (CP-NEW asset-blocked) | 4 | Keep as explicit drafts | 0 | None (blocked externally) |
| E4 (CP-NEW stable_fail) | 8 | Re-author key ones (brick-stacking); mark rest as documented failures | 4 agent-hours | Medium |
| F (CP-01..05 role graft) | 5 | Keep | 0 | None |
| Ghost corpus (TP-*) | 30 IDs | Re-map to existing CPs | 2 agent-hours | High if left as-is |
| Delete candidates | 1 clear + 1 optional | Delete CP-NEW-peg-in-hole-single | 0.1 agent-hours | None |

**Total estimated work to fully remediate: ~33 agent-hours**
**Minimum viable remediation (eliminate active misleading states): ~6 agent-hours**
- Fix deprecated fields in CP-01/02/07/08 (0.5h)
- Promote E2 plumbing-only templates (0.5h)
- Remap TP-* ghost corpus (2h)
- Delete CP-NEW-peg-in-hole-single (0.1h)
- Update CP-67 status (0.1h)
- Run form-gate on highest-priority D2 templates (CP-22, CP-46, CP-51) (3h)

---

## 7. Key Findings Summary

1. **Single-author repo, zero cross-author conflict.** All drift is intra-session schema evolution by one author. The schema stabilized after the 2026-05-08 marathon sprint.

2. **212 T2 templates are structurally uniform (one shape).** 102 are never-run soft drafts but carry no incorrect claims; the risk is incompleteness, not incorrectness.

3. **63 of 109 T1 CP templates are fully shippable** (function-gate ✓). 15 have been in "form-gate pending" state since 2026-05-08 — these are the most misleading entries, claiming "build-spec" without any gate evidence.

4. **CP-06 is the only genuinely blocked template.** Its `blocked` object documents a known controller failure and marks it safe to leave in tree without confusing downstream tools.

5. **All T2 tools referenced in templates exist in the handler registry** (via `data[]` or `codegen[]` in the handlers). No orphaned tool references found.

6. **The 30 TP-* ghost corpus IDs are the highest-risk structural issue.** The `retrieve_template_by_role` handler surfaces these IDs to LLMs as if loadable. Remapping to existing CPs is the correct fix, not deletion.

7. **CP-NEW-peg-in-hole-single is the only clean delete.** All other stable_fail and experimental templates serve a documentary function that exceeds their deletion cost.

8. **Verified_date and verified_metrics appear on only 2 templates** (CP-01, CP-02). Their absence from CP-03..87 is not drift; they were a Cohort C experiment never carried forward, confirmed not part of the T1 standard schema.

---

*Cites: Q2 field-presence table at `docs/research/2026-05-15-q2-canonical-format.md`; Q3 ghost-corpus finding at `docs/research/2026-05-15-q3-retrieval-quality.md`. Key commits: `7e65b27` (Phase 12 T2 batch), `3445b0b` (CP-01 first draft), `2744bb6` (Block 1B role graft), `0ea366c`/`b76ce47`/`3b3c86a` (yrkesroll wave). Role index file: `service/isaac_assist_service/multimodal/role_template_index.py`.*
