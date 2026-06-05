# R15b — Stage 1 Filter Tuning + Intent-Field Hygiene

**Date:** 2026-05-15
**Scope:** Investigate why prompts whose ground_truth has `intent` field
still get lost in struct-filter even after R15 query-bug fix.

## Diagnosis

After R15 closed the query-construction bugs, hit@1 was 0.600 — still
−23pp vs baseline (0.833). Spot-investigation of remaining LOST prompts
(B04, B30, B19, B27) identified two distinct issues:

1. **Stage 1 filter rejects valid candidates when spec emits schema
   default.** The rule-based text-intent extractor returns
   `destination_kind="single_bin"` as a fallback when it cannot infer
   the destination type from natural language. Stage 1 then requires
   exact match — so templates with `destination_kind="fixture"` or
   `"n_bins_routed"` get filtered OUT even when they're the correct
   ground_truth.

2. **Two templates had small intent-field bugs from the mass migration:**
   - CP-04: `has_footprint_constraint` (typo) → should be
     `has_bounded_footprint` (the actual schema key in
     `MULTIMODAL_TEXT_INTENT_SCHEMA`)
   - CP-55: `pattern_hint: "reorient"` was wrong — CP-55 is drawer-extends,
     semantically pick-place (the gripper picks the drawer handle and
     extends it). Added `has_articulated_mechanism: true` to capture
     the unique aspect.

## Fix

`template_retriever.py` (+14 LOC): introduce `_UNCONSTRAINED_DEFAULTS`
dict mapping intent fields to their schema-default values. When the
spec carries the default (meaning "extractor couldn't infer"), skip
that field's comparison in Stage 1 admission.

```python
_UNCONSTRAINED_DEFAULTS = {"destination_kind": "single_bin"}
# ...
if _UNCONSTRAINED_DEFAULTS.get(key) == spec_v:
    continue  # spec is unconstrained on this field
```

Plus the two template field hygiene fixes (CP-04 + CP-55).

## Validation

Re-ran R14/R15 benchmark:

| Metric | Baseline (off) | R14 (bugged) | R15 (query fix) | R15b (Stage 1 + hygiene) |
|---|---|---|---|---|
| hit@1 | 0.833 | 0.233 | 0.600 | **0.667** |
| hit@3 | 0.933 | 0.367 | 0.633 | **0.700** |
| mode_accuracy | 0.700 | 0.533 | 0.633 | 0.600 |
| hard_instantiate_rate | 0.167 | 0.000 | 0.100 | 0.133 |

**Cumulative recovery from R14 bugged-state:** +43pp hit@1 (0.233 → 0.667).
**Still −17pp vs baseline (0.833)** — flag-flip blocker remains.

## What's still wrong

Remaining LOSSES:
- B19 (rl_training, gt=M-08): M-08 is a T2 dialogue template (lacks
  `intent` field). Struct-filter path forwards the prompt to fallback,
  which still loses it.
- B27 (contact_rich, gt=CP-58): CP-58 is deferred (peg-in-hole physics-
  instability, no intent field). Same fallback issue.

Both LOSSES target templates **without** intent fields. The fallback
ranking when ground_truth lacks intent is the remaining gap → R15c
scope.

## Verdict

Do NOT flip `MULTIMODAL_TEXT_INTENT=on` default yet. Need R15c (fallback
ranking parity with baseline when ground_truth lacks intent) before
hit@1 can match baseline 0.833.

## Tests

All 59 retrieval/lint/dispatch tests pass. No regressions.

`workspace/benchmarks/retrieval_30prompts_struct_on_post_r15_2026-05-15.json`
updated with R15b results.

## Follow-up

- **R15c (deferred):** when struct-filter fallback path runs, it should
  produce identical ranking to default-off retrieval, not the current
  mixed behavior. Likely 1-line fix in the fallback short-circuit.
- **R15d (deferred):** revisit CP-55 `pattern_hint` decision —
  "drawer extends" could plausibly be a new pattern_hint value rather
  than overloading pick_place.
