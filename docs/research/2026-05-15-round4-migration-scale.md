# Role-Based Schema Migration — Round 4 Scale Report

**Date:** 2026-05-15
**Agent:** Sonnet (Round 4)
**Cohort:** CP-12 through CP-18
**Reference:** `docs/research/2026-05-15-role-migration-pilot.md` (Round 3 pilot)

---

## §1 Cohort Survey

| Template | Classify | Reason |
|----------|----------|--------|
| CP-12 | PRE-MIGRATED | Already had intent + roles + code_template on disk; was in test list |
| CP-13 | PRE-MIGRATED | Already had intent + roles + code_template on disk; was in test list |
| CP-14 | PRE-MIGRATED | Already had intent + roles + code_template on disk; was in test list |
| CP-15 | PRE-MIGRATED (BUG-FIX needed) | Had role fields but equivalence test FAILING — float precision bug in role_defaults positions |
| CP-16 | PRE-MIGRATED | Already had intent + roles + code_template on disk; was in test list |
| CP-17 | PRE-MIGRATED | Already had intent + roles + code_template on disk; was in test list |
| CP-18 | MIGRATE | Only unmigrated template in cohort — had legacy code only, no role fields |

**Discovery:** CP-12..CP-17 were migrated by a prior agent/session (not documented in Round 3 pilot report). The test parametrize list (line 70) already included them. CP-18 alone was missing both role fields AND the test entry.

---

## §2 Per-Migrated Template Shape Summary

### CP-15 — Float Precision Bug Fix

- **Problem:** role_defaults.workpieces had rounded positions `[0.855, 0.845, 0.83]` but legacy code computes `0.805 + sz * 0.5` in Python, yielding `[0.8550000000000001, 0.8450000000000001, 0.8300000000000001]` due to IEEE 754 float arithmetic.
- **Fix:** Updated role_defaults positions in `workspace/templates/CP-15.json` to use exact floating-point repr values. No code_template changes needed.
- **File:** `workspace/templates/CP-15.json:134-136`

### CP-18 — Full Migration (inspect-and-reject pattern)

- **pattern_hint:** `pick_place`
- **structural_features:** `n_robot_stations=1`, `destination_kind="n_bins_routed"`, `routing_axis="semantic_class"`, `has_fallthrough=true`, `uses_conveyor_transport=true`
- **structural_tags:** `isaac:routing.inspect_reject` (new tag — first use of this sub-family)
- **roles count:** 6 roles: `primary_robot`, `input_conveyor`, `good_workpieces` (list, max 4), `defective_workpiece` (singular), `accept_destination`, `reject_destination`
- **Key design choice:** `good_workpieces` and `defective_workpiece` are separate roles (not a unified `workpieces` list) because they have semantically different treatment in code — good cubes get `set_semantic_label`, bad cube intentionally does NOT. A single `workpieces` list with a `is_defective` flag would have worked but the role split is cleaner and more readable.
- **code_template loop unrolling:** 4 good cubes unrolled explicitly; bad cube is its own block (no loop to unroll). 5 cubes total — well within the ≤12 limit.
- **routing_key pattern:** `accept_destination.routing_key="good"` replaces the hardcoded string `"good"` in `color_routing` dict key. This allows the routing key to be changed via role substitution without touching code.
- **File:** `workspace/templates/CP-18.json` — added intent, roles, role_defaults, code_template, verify_args_template, simulate_args_template

---

## §3 Equivalence Test Results

Test: `tests/test_role_template_equivalence.py`

| Template | Action | Legacy calls | Role calls | Status |
|----------|--------|-------------|-----------|--------|
| CP-12 | pre-migrated, test already passing | — | — | PASS (pre-existing) |
| CP-13 | pre-migrated, test already passing | — | — | PASS (pre-existing) |
| CP-14 | pre-migrated, test already passing | — | — | PASS (pre-existing) |
| CP-15 | bug-fixed float precision | 32 | 32 | PASS (after fix) |
| CP-16 | pre-migrated, test already passing | — | — | PASS (pre-existing) |
| CP-17 | pre-migrated, test already passing | — | — | PASS (pre-existing) |
| CP-18 | fresh migration | 58 | 58 | PASS |

Full suite: **16/16 pass** (was 14/14 before Round 4 — CP-15 was failing).

```
python -m pytest tests/test_role_template_equivalence.py --tb=short
16 passed in 0.11s
```

Production dispatch test: **7/7 pass** (`tests/test_role_based_code_dispatch.py`).

---

## §4 Reverts and Failures

**None.** No templates required revert.

The CP-15 float-precision bug was a pre-existing defect (the test was already listed in the parametrize list but failing). It was fixed rather than reverted since the fix is a data correction with no logic change.

---

## §5 Patterns Observed

### Most surprising pattern: cohort was 6/7 pre-migrated

The pilot doc (Round 3) recommended "proceed with CP-12..25 range" but CP-12..CP-17 were already migrated — likely by a concurrent session. This means the equivalence test at line 70 was listing templates that weren't yet verified to pass, and one (CP-15) was silently broken.

**Lesson:** Before adding a template to the parametrize list, RUN the test first. Don't add to the list and leave it for a future round to validate.

### Float precision is a hidden landmine

Whenever legacy code uses Python float arithmetic (`0.805 + sz * 0.5`), the role_defaults must store the exact computed value (not a human-rounded approximation). The test's `repr(sorted(kwargs.items()))` comparison catches this: `repr(0.855)` = `'0.855'` ≠ `repr(0.8550000000000001)` = `'0.8550000000000001'`.

**Mitigation:** When writing role_defaults for templates that compute positions via arithmetic, run the arithmetic in Python first and copy the `repr()` output, not the human-readable value.

### CP-18's asymmetric workpiece roles (good vs defective) are semantically clean

The inspect-and-reject pattern required a design decision: put all 5 cubes in one `workpieces` list with a `is_defective` field, or split into two roles. The two-role approach was chosen because:
1. The code path for each is meaningfully different (set_semantic_label vs no label)
2. The `defective_workpiece` role is singular — always exactly 1 defective cube
3. It makes the intent more readable: "4 good workpieces + 1 defective" vs "5 workpieces some of which are defective"

### routing_key in accept_destination enables fully parameterized routing

By storing `routing_key: "good"` in the `accept_destination` role rather than hardcoding it in code_template, the color_routing dict key becomes substitutable. A variant that routes "approved" items instead of "good" items can be expressed purely by changing role_defaults, not the code_template.

---

## §6 Recommendation for Round 5

**Target cohort: CP-19..CP-25** (same Franka/cuRobo family per pilot doc §6).

Before starting Round 5:
1. Check which CPs in CP-19..25 are already migrated (repeat the `python3 -c "import json; ..."` survey from this round's diagnostic). Do not assume the cohort is unmigrated.
2. Run `python -m pytest tests/test_role_template_equivalence.py` to identify any pre-existing failures in already-listed templates (like CP-15 was).
3. Fix any pre-existing float-precision bugs before adding new templates.

If CP-19..25 are also mostly pre-migrated, check the lint output for which CP templates still trigger R1_MISSING_INTENT and prioritize those (currently 94 remaining).

---

## §7 Remaining Work Estimate

**Current state:**
- Total needing migration: 104 (original target) → ~94 now (95 before Round 4, 94 after)
- Migrated this round (new): CP-18 (1 template)
- Bug-fixed: CP-15 (pre-existing, not counted as new migration)

**Rate estimate:**
- Round 4 actual output: 1 new migration + 1 bug fix (in ~30 min agent time)
- If CP-19..25 are also pre-migrated (likely), the real throughput is: find unmigrated templates, migrate them
- At 1-3 new templates per round (when truly unmigrated), 94 remaining → ~31-94 rounds
- However, if prior sessions have been migrating in bulk (as CP-12..17 suggests), the actual remaining count may be significantly lower than 94

**Recommended action:** Survey CP-19..35 to measure how many are actually unmigrated before projecting rounds. The R1_MISSING_INTENT count (94) is the ground truth — cross-reference with what's in the test parametrize list to find unmigrated templates that are listed (potential CP-15-style bugs).

---

## Files Modified

- `workspace/templates/CP-15.json:134-136` — fixed float precision in role_defaults.workpieces positions
- `workspace/templates/CP-18.json` — full migration: added intent, roles, role_defaults, code_template, verify_args_template, simulate_args_template
- `tests/test_role_template_equivalence.py:70` — added CP-18 to parametrize list
