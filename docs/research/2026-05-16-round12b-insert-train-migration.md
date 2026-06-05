# Round 12b — Insert/Train Novel-Pattern Migration

**Date:** 2026-05-16
**Status:** Complete. 0 commits (Anton reviews before commit).
**Depends on:** R12 (e9b7a05) — `insert` + `train` added to `VALID_PATTERN_HINTS`.

---

## §1 Per-Template Classification

### Insert cluster

| Template | Decision | Reason |
|----------|----------|--------|
| **CP-58** | MIGRATE | Has robot + conveyor + 4 pegs + HolePanel + `verify_args` + `simulate_args`. Clear role structure: `primary_robot`, `input_conveyor`, `hole_panel`, `pick_sensor`, `ft_sensor`, `workpieces[4]`, `hole_markers[4]`. `code_template` unrolls all for-loops; equivalence PASS (48 calls → 48 calls). |
| **CP-NEW-tactile-insertion** | MIGRATE | Has robot + single peg + HolePanel + `simulate_args`. No conveyor (`belt_path=None`). Simple role structure: `primary_robot`, `hole_panel`, `pick_sensor`, `ft_sensor`, `workpieces[1]`. `code_template` straightforward; equivalence PASS (19 calls → 19 calls). `verify_args: null` (stable_fail) — acceptable. |
| **CP-NEW-peg-in-hole-single** | SKIP-PERMANENTLY | Strict structural duplicate of CP-58 (same Franka + conveyor + single-peg scene, same PhysX stable_fail). `verify_args: null`. No distinct scenario value over CP-58. `migration_deferred.reason` updated from `novel_pattern` → `duplicate_of_cp58`. |

### Train cluster

| Template | Decision | Reason |
|----------|----------|--------|
| **CP-NEW-rl-clone-env** | SKIP-PERMANENTLY | `pattern_hint=train` now valid but role-based schema requires delivery success criterion. This template's success = reward-curve trend, not cube-at-destination. `simulate_args: null`. `clone_envs`/`launch_training` have no structural match in roles schema. Reason updated: `train_pattern_no_delivery_criterion`. |
| **CP-NEW-defect-sdg** | SKIP-PERMANENTLY | `pattern_hint=train` applicable in spirit but template has **no robot** — pure SDG pipeline (`configure_sdg` + `add_domain_randomizer` + `create_sdg_pipeline`). Role-based schema requires `primary_robot`. Reason updated: `train_pattern_no_robot`. |
| **CP-NEW-sim2real-gap** | SKIP-PERMANENTLY | `pattern_hint=train` applicable but this is a measurement/diagnostic task (`replay_rosbag` + `measure_sim_real_gap`). `simulate_args: null`. Success = `/tmp/sim2real_gap.json` file, not delivery. Reason updated: `train_pattern_diagnostic_only`. |

---

## §2 Reverts

None. Both MIGRATE templates passed equivalence on first attempt (after fixing `destination_kind` from `fixture_panel` → `fixture` per valid enum).

---

## §3 Equivalence Test Results

| Template | Legacy calls | Role calls | Result |
|----------|-------------|------------|--------|
| CP-58 | 48 | 48 | PASS |
| CP-NEW-tactile-insertion | 19 | 19 | PASS |

Both templates added to `tests/test_role_template_equivalence.py` parametrize list.

Combined suite: `pytest tests/test_role_template_equivalence.py tests/test_pattern_hint_extension.py tests/test_canonical_lint.py` → **131 passed** (was 129 before R12b; +2 for new equivalence cases).

---

## §4 Lint Counts

| Metric | Pre-R12b | Post-R12b | Delta |
|--------|----------|-----------|-------|
| R1_MISSING_INTENT | 44 | 42 | −2 |
| ERROR | 0 | 0 | 0 |
| WARN | 55 | 55 | 0 |
| INFO | 105 | 101 | −4 |

INFO drop = 4 × R1_MISSING_INTENT cleared (2 migrated → intent added; 4 SKIP-PERMANENTLY still missing intent, as expected — they can't be role-migrated).

Note: The 4 SKIP-PERMANENTLY templates still have `migration_deferred` set, so they still appear as R1_MISSING_INTENT. This is intentional — they are not yet structurally migratable.

---

## §5 CP-NEW-peg-in-hole-single — Delete Recommendation

**Recommendation: DELETE.**

Evidence:
1. **Structural duplicate**: Tool call sequence matches CP-58 pattern (same Franka + peg-on-conveyor + HolePanel + FT sensor + assembly_constraint). The only differences are array size (1 peg vs 4) and peg radius (0.018 vs 0.02) — trivially parametrizable from CP-58's `role_defaults`.
2. **Same failure mode**: Both are `stable_fail` due to PhysX numerical instability during grip (cube velocity > 200km/s). Fixing one fixes the other.
3. **verify_args: null**: No function-gate path defined. Cannot be independently validated.
4. **No retrieval value**: The `insert` pattern is fully covered by CP-58. A 1-peg variant adds no distinct scenario coverage at retrieval time that isn't already differentiated by `role_defaults.workpieces` length.
5. **Duplication cost**: Keeping it adds maintenance burden — any CP-58 code fix must be mirrored here.

**To delete (when ready):** `rm workspace/templates/CP-NEW-peg-in-hole-single.json` — no test references it, it's not in the equivalence parametrize list, and the lint count will drop by 1 R1_MISSING_INTENT.

---

## Files Changed

- `workspace/templates/CP-58.json` — added `intent`, `roles`, `role_defaults`, `code_template`; removed `migration_deferred`
- `workspace/templates/CP-NEW-tactile-insertion.json` — added `intent`, `roles`, `role_defaults`, `code_template`; removed `migration_deferred`
- `workspace/templates/CP-NEW-peg-in-hole-single.json` — updated `migration_deferred.reason` to `duplicate_of_cp58`
- `workspace/templates/CP-NEW-rl-clone-env.json` — updated `migration_deferred.reason` to `train_pattern_no_delivery_criterion`
- `workspace/templates/CP-NEW-defect-sdg.json` — updated `migration_deferred.reason` to `train_pattern_no_robot`
- `workspace/templates/CP-NEW-sim2real-gap.json` — updated `migration_deferred.reason` to `train_pattern_diagnostic_only`
- `tests/test_role_template_equivalence.py` — added `CP-58` and `CP-NEW-tactile-insertion` to parametrize list
