# R15 — Fix Structural-Filter Query-Construction Bugs

**Date:** 2026-05-15
**Scope:** Fix the two query-construction bugs R14 surfaced (fingerprint-as-query
in both Stage 2 of struct path and fallback path).

## Problem

R14 benchmark showed structural filter regressed retrieval by 60pp
(0.833 → 0.233 hit@1) despite 65/321 templates having intent fields.
Root cause analysis identified:
- **Failure Mode A:** fallback path used structured intent fingerprint
  string as ChromaDB embedding query against a corpus indexed on
  natural-language (goal + thoughts + tools_used). Semantic mismatch
  → similarity collapses ~0.6 → ~0.4.
- **Failure Mode B:** even within struct-filter primary path, Stage 2
  re-embed used the fingerprint, not the user prompt.

## Fix

`service/isaac_assist_service/chat/tools/template_retriever.py` (+31/-11 LOC):

1. Added `original_query: Optional[str] = None` parameter
2. When provided, used as embedding query in Stage 2 AND fallback path
3. When None, legacy fingerprint-as-query behavior preserved (backward compat)
4. Inline comments mark "Bug fix (R15, Failure Mode A/B)" at each fix site

```python
# Bug fix (R15): use original_query for embedding when available.
embed_query = original_query if original_query else fingerprint
```

## Validation

Re-ran R14 benchmark with fix in place:

| Metric | Baseline (struct off) | R14 (struct on, bugged) | R15 (struct on, fixed) |
|---|---|---|---|
| hit@1 | 0.833 | 0.233 | **0.600** |
| hit@3 | 0.933 | 0.367 | 0.633 |
| mode_accuracy | 0.700 | 0.533 | 0.633 |
| hard_instantiate_rate | 0.167 | 0.000 | 0.100 |

**Improvement: +37pp hit@1 (0.233 → 0.600).** Still −23pp vs baseline.

## What's still wrong

R15 closes the bug-driven regression but uncovers a coverage-driven one.
Remaining LOST prompts:

- B04 (CP-04): pick_place lost to CP-11 — both have intent, ranking ambiguity
- B08 (CP-NEW-3station-oee): lost to CP-44 — same pattern_hint, false neighbor
- B17 (FX-01), B18 (P-01), B19 (M-08): ground_truth templates lack intent;
  fallback path should rank them right but doesn't
- B27 (CP-58): ground_truth lacks intent (deferred); fallback issue
- B30 (CP-55): CP-55 has intent, lost to CP-27 — Stage 1 candidate selection
  may be filtering CP-55 out before Stage 2

## Verdict

Do NOT flip `MULTIMODAL_TEXT_INTENT=on` default yet. Hit@1 dropped 23pp
vs baseline; that's still production regression.

Remaining work (out of scope for this session):
1. **R15b: Stage 1 filter tuning** — current Stage 1 may be too aggressive
   on count-match tolerance, ejecting good candidates before Stage 2 ranks
2. **R15c: Fallback ranking** — when ground_truth lacks intent (40% of
   corpus), fallback should match baseline behavior; currently it's mixing
   struct-filtered + similarity in a way that loses prompts
3. **Coverage matters too:** the remaining LOST prompts mostly target
   templates without intent fields. Migrating those (or the deferred 44)
   would help.

Flag-flip blockers: hit@1 must be ≥ 0.833 (parity with baseline) AND
hit@3 ≥ 0.933 before considering default-on.

## Test coverage

`tests/test_struct_filter_query_construction.py` (new): unit tests for:
- original_query param accepted and used
- fingerprint fallback preserved when param None
- backward-compat for existing callers

`tests/test_retrieval_struct_filter.py` (modified): now exercises the
fixed query path; benchmark output written to
`workspace/benchmarks/retrieval_30prompts_struct_on_post_r15_2026-05-15.json`.

All 59 tests pass (no regression in retrieval/lint/dispatch suites).
