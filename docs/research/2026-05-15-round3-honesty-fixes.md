# Round 3 / Patch C — Honesty Gap Fixes (2026-05-15)

**Agent:** Claude Sonnet 4.6, patch mode  
**Input:** docs/research/2026-05-15-round2-audit-honesty.md  
**Scope:** M-01 (CP-61 overclaim), M-02/M-03 (ghost-corpus count + missing REVIEW flags), L-01 (7 smoke-test downgrade)  
**Output:** This file

---

## §1 Fixes Applied

### Fix 1 — CP-61 motion_controllers.verified

**File:** `workspace/templates/CP-61.json`

**Diagnosis:** `verified_status` explicitly says "form-gate expected to fail (Cortex doesn't install
pick-place controller); build-only verification". This is a *known failure*, not merely untested.
Placed in `failed` (not `untested`) because the failure mode is documented — form-gate would fail
deterministically, not stochastically.

**Before:**
```json
"motion_controllers": {
  "verified": ["cortex"],
  "untested": []
}
```

**After:**
```json
"motion_controllers": {
  "verified": [],
  "untested": [],
  "failed": {
    "cortex": "build-only verification 2026-05-08; form-gate expected to fail (Cortex does not install pick-place controller — uses RmpFlowCortex internally; no function-gate or form-gate run passed)"
  }
}
```

**Rule applied:** `verified` requires `function-gate ✓` or `form-gate ✓` in `verified_status`. CP-61 has
neither. The status says the form-gate is *expected* to fail, making this a known failure, not an
untested controller.

---

### Fix 2 — Ghost-corpus doc count + 2 missing REVIEW flags

**Files modified:**
- `docs/research/2026-05-15-ghost-corpus-remap-decisions.md`
- `service/isaac_assist_service/multimodal/role_template_index.py`

**Sub-fix 2a — Summary count corrected**

The Round 2 audit found the decision-table had 8 [REVIEW] entries but the summary said "7".
After adding TP-WLD-02 and TP-WLD-03 the correct count is 10.

Before: `- Flagged [REVIEW] for human follow-up: **7**`  
After:  `- Flagged [REVIEW] for human follow-up: **10** (8 original + TP-WLD-02 + TP-WLD-03 added by Round 3 patch C)`

**Sub-fix 2b — Decision table: add [REVIEW] to TP-WLD-02 and TP-WLD-03**

Before:
```
| TP-WLD-02 | welder | mig_welder | **CP-02** | Multi-station assembly line... | |
| TP-WLD-03 | welder | tig_welder | **CP-24** | Narrow-slot insertion...       | |
```

After:
```
| TP-WLD-02 | welder | mig_welder | **CP-02** | Multi-station assembly line... | [REVIEW] |
| TP-WLD-03 | welder | tig_welder | **CP-24** | Narrow-slot insertion...       | [REVIEW] |
```

Rationale: CP-02 is a multi-station conveyor pick-place (no MIG torch, no weld bead, no seam
tracking). CP-24 is narrow-slot precision insertion (no TIG torch, no shielding gas). Both are
semantically as weak as TP-DSP-02 (sealant_dispenser→CP-69) which WAS flagged [REVIEW] with the
note "CP-69 is a bin-pick; no sealant path." Consistency required applying the same flag.

**Sub-fix 2c — [REVIEW] section table: add 2 rows, fix internal header count**

The [REVIEW] section itself also said "These 7 entries" despite containing 8 rows. Fixed to
"These 10 entries" and added TP-WLD-02 and TP-WLD-03 rows with weakness descriptions.

**Sub-fix 2d — role_template_index.py inline comments**

Added `[REVIEW]` to the inline comments for TP-WLD-02 and TP-WLD-03 entries:

Before:
```python
template_id="CP-02",  # multi-station assembly line — industrial multi-robot workflow
...
template_id="CP-24",  # narrow-slot insertion — precision placement analog
```

After:
```python
template_id="CP-02",  # multi-station assembly line — industrial multi-robot workflow [REVIEW]
...
template_id="CP-24",  # narrow-slot insertion — precision placement analog [REVIEW]
```

No logic changes — inline comment only.

---

### Fix 3 — 7 CP-NEW smoke-test templates downgraded

**Files modified:** (7 templates)
- `workspace/templates/CP-NEW-3station-oee.json`
- `workspace/templates/CP-NEW-cad-revision-drift.json`
- `workspace/templates/CP-NEW-controller-shootout-cp.json`
- `workspace/templates/CP-NEW-dr-curriculum.json`
- `workspace/templates/CP-NEW-inspect-reject.json`
- `workspace/templates/CP-NEW-multi-cam-triangulation.json`
- `workspace/templates/CP-NEW-y-merge-singulation.json`

All 7 had identical pattern: `verified: ["curobo"]`, `untested: ["rmpflow", "moveit2"]`  
with `verified_status: "build-spec-2026-05-10; smoke-test ✓ 1/1 (...)"` — no function-gate or form-gate.

**Before (all 7):**
```json
"motion_controllers": {
  "verified": ["curobo"],
  "untested": ["rmpflow", "moveit2"]
}
```

**After (all 7):**
```json
"motion_controllers": {
  "verified": [],
  "untested": ["curobo", "rmpflow", "moveit2"]
}
```

`curobo` is not deleted — it remains in `untested` where the association is preserved without
overclaiming. The smoke run is real evidence; the controller is plausible; but it does not meet
the `function-gate ✓` or `form-gate ✓` threshold for `verified`.

---

## §2 Additional Overclaims Found in Broader Audit

A full scan of all 321 templates for `motion_controllers.verified` entries without gate evidence
was performed. The scan checked all 55 templates with non-empty `verified` lists.

**Result: No additional overclaims found.**

All 55 remaining verified entries (CP-01 through CP-86) contain either `function-gate ✓` or
`form-gate ✓` in their `verified_status` text. The only exception was CP-61 (fixed above).
CP-28 and CP-29 have `form-gate ✓` without `function-gate ✓` — this is compliant with the rule
(either gate suffices).

CP-72 has `verified: ["curobo", "cortex"]` — checked: its `verified_status` includes
"function-gate ✓ (multi-cube cube_paths — 3/4 cubes delivered via UR10 Cortex)". Honest. Not
downgraded.

---

## §3 Ghost-Corpus Doc Count Reconciliation

| State | REVIEW count in decision table | Summary line | [REVIEW] section header |
|-------|-------------------------------|-------------|------------------------|
| Original (Round 1) | 8 | **7** (wrong) | "7 entries" (wrong) |
| Round 3 target | 10 | **10** (fixed) | "10 entries" (fixed) |

The original "7" was doubly wrong: the table already had 8 entries (Round 2 finding), and
TP-WLD-02/TP-WLD-03 should have been flagged from the start (Round 3 finding).

---

## §4 Lint Status

**Before patches:** 321 templates scanned: 219 OK, 0 ERROR, 55 WARN, 219 INFO  
**After patches:**  321 templates scanned: 219 OK, **0 ERROR**, 55 WARN, 219 INFO

No new ERRORs introduced. WARN/INFO counts unchanged (pre-existing settle_state and
intent-migration warnings).

---

## §5 Non-Honesty Issues for Round 4 Awareness

1. **CP-28 / CP-29 `verified: ["curobo"]` with form-gate only** — These two have `form-gate ✓`
   but no `function-gate ✓` (noted as precision experiment scaffolds). Technically compliant with
   the "either gate" rule, but the verified_status for CP-29 explicitly says the precision experiment
   FAILED. The `verified` key may mislead consumers who expect delivery success, not just build
   success. Low severity — recommend Round 4 auditor check if `form-gate ✓` alone is sufficient
   evidence for these templates or if they should note the functional limitation.

2. **7 CP-NEW templates have `verified: []`** — After the downgrade, these templates now show
   `verified: []` which means controller-filtered retrieval will not surface them. This is honest
   but may affect recall. Round 4 should consider whether to either (a) upgrade after a 3-run
   function-gate, or (b) run the function-gate as a follow-up task.

3. **`untested: []` in CP-61** — After the fix, CP-61 has `verified: []`, `untested: []`,
   `failed: {cortex: ...}`. The `untested` list is empty, meaning no controllers are candidates
   for future testing. This is accurate (Cortex is the only plausible controller for this template)
   but may warrant a note that alternative controllers (e.g., rmpflow) were not evaluated because
   Cortex is architecturally required by the scenario.
