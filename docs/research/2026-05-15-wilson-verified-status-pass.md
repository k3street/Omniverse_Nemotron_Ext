# Wilson Verified-Status Pass — 2026-05-15

**Agent:** Sonnet 4.6 (Week 1 / Track A continuation)  
**Scope:** 15 T1 CP templates stuck in `"form-gate verification pending"` since 2026-05-08  
**Method:** Aggregate run data from `workspace/baselines/*.json` (label-field match) → Wilson lower-bound (z=1.96) → decision tree  
**Threshold:** `wilson_lower ≥ 0.70` → verified-wilson; `passes=0` → stable_fail; else → draft  

---

## Decision Table

| id | prior_status | new_status | evidence_source | wilson_lower | reasoning |
|----|-------------|------------|-----------------|-------------|-----------|
| CP-22 | form-gate verification pending | draft | baselines aggregate (35 files) | 0.4327 | 31/55 passes (56.4%) — high volume but WL only 0.43; high-speed belt stochastic |
| CP-35 | form-gate verification pending | draft | baselines aggregate (6 files) | 0.0137 | 1/13 passes (7.7%) — nearly always fails; multi-cube placement issue |
| CP-37 | form-gate verification pending | draft | baselines aggregate (27 files) | 0.1884 | 17/59 passes (28.8%) — obstacle-avoidance scene hits planning gaps; not reliable |
| CP-40 | form-gate verification pending | draft | baselines aggregate (6 files) | 0.0127 | 1/14 passes (7.1%) — near-zero pass rate; cube-path variant of CP-35 issue |
| CP-46 | form-gate verification pending | draft | baselines aggregate (12 files) | 0.0924 | 5/24 passes (20.8%) — multi-cube reference baseline itself fails 79% of runs |
| CP-48 | form-gate verification pending | draft | baselines aggregate (15 files) | 0.0385 | 3/27 passes (11.1%) — runtime-vision integration too fragile |
| CP-51 | form-gate verification pending | draft | baselines aggregate (15 files) | 0.2759 | 12/27 passes (44.4%) — robot-to-robot handoff via fixed-point marker is flaky |
| CP-52 | form-gate verification pending | draft | baselines aggregate (13 files) | 0.3866 | 12/20 passes (60.0%) — best non-zero sub-threshold; dual-target drop precision |
| CP-53 | form-gate verification pending | draft | baselines aggregate (9 files) | 0.2447 | 9/21 passes (42.9%) — producer/consumer with kit-tray staging; medium flake |
| CP-57 | form-gate verification pending | draft | baselines aggregate (8 files) | 0.1018 | 4/16 passes (25.0%) — multi-cube sweep variant; planning collision sensitivity |
| CP-60 | form-gate verification pending | stable_fail | baselines aggregate (10 files) | 0.0 | 0/14 passes — recirculation-loop cube corner-transition always fails |
| CP-62 | form-gate verification pending | stable_fail | baselines aggregate (11 files) | 0.0 | 0/15 passes — surface-gripper gantry slider doesn't actually move robot |
| CP-65 | form-gate verification pending | draft | baselines aggregate (13 files) | 0.690 | 21/24 passes (87.5%) — borderline; WL=0.6900 < 0.70; needs ~3 more passing runs |
| CP-68 | form-gate verification pending | draft | baselines aggregate (14 files) | 0.2554 | 11/26 passes (42.3%) — robot-to-robot handoff variant; drop precision blocks |
| CP-76 | form-gate verification pending | stable_fail | baselines aggregate (14 files) | 0.0 | 0/22 passes — dual-robot fixture hold never delivers; pedestal mutex conflict |

---

## Summary

- **15 templates audited** (the full Sub-group D2 cohort from Q4 §2.2)
- **0 upgraded to verified-wilson** — no template reached wilson_lower ≥ 0.70
- **12 downgraded to draft** — have non-zero passes but insufficient reliability
- **3 downgraded to stable_fail** — CP-60, CP-62, CP-76 each have 0 passes across 14-22 runs
- **0 marked plumbing_only** — all have simulate_args and real run data
- **0 marked broken** — code is syntactically valid; failures are physics/controller, not code errors
- **0 unchanged** — all 15 had misleading "pending" status and all were updated

---

## Surprising Findings

1. **CP-46 claimed "reference baseline canonical"** in its status string yet had only 5/24 passes (WL=0.092). It was self-promoted as a reference for other CPs to compare against — but it fails 79% of runs.

2. **CP-65 is 1 threshold away**: 21/24 passes gives WL=0.6900, just 0.01 short of 0.70. Three more passing runs would cross the threshold. It was mislabeled as "pending" while nearly meeting the bar for full verification.

3. **CP-60 and CP-62 had 0/14 and 0/15 passes respectively** yet both claimed "form-gate verification pending" — implying runs hadn't been attempted. The baselines show they were run extensively and never passed; the "pending" label actively obscured known stable_fail status.

---

## New Fields Added to Each Template

Each of the 15 templates now carries:
- `verified_status`: human-readable decision string with passes, n, wilson_lower, and demotion date
- `verified_wilson_lower`: float (machine-readable)
- `verified_runs`: object with `{passes, n, z, lower, upper, evidence_date, evidence_source}`

---

## Lint Baseline (post-edit)

```
321 templates scanned: 213 OK, 0 ERROR, 117 WARN, 225 INFO
```

No new ERRORs introduced. WARN/INFO counts unchanged from pre-edit baseline.

---

## Files Modified

**15 template files edited:**  
`workspace/templates/CP-22.json`, `CP-35.json`, `CP-37.json`, `CP-40.json`, `CP-46.json`,  
`CP-48.json`, `CP-51.json`, `CP-52.json`, `CP-53.json`, `CP-57.json`,  
`CP-60.json`, `CP-62.json`, `CP-65.json`, `CP-68.json`, `CP-76.json`

**1 research doc created:**  
`docs/research/2026-05-15-wilson-verified-status-pass.md` (this file)

---

*Evidence: `workspace/baselines/` (150+ files, label-field aggregation). Wilson formula: `_stats.py::wilson_lower`. Threshold: 0.70 per task spec.*
