# Test-Suite Validation — 2026-05-16

Post-change regression check covering R11b, R17b, R18, A1 (palletizer canonical), and peg-in-hole delete.

---

## §1 Test-suite results

| File | Tests | Passed | Failed | Notes |
|---|---:|---:|---:|---|
| test_template_cache_rehydration.py | 9 | 9 | 0 | 1 warning (urllib3 compat) |
| test_motion_controller_retrieval_filter.py | 19 | 19 | 0 | |
| test_struct_filter_query_construction.py | 7 | 7 | 0 | |
| test_soft_filter_retrieval.py | 6 | 6 | 0 | |
| test_mc_filter_orchestrator_wire.py | 25 | 25 | 0 | |
| test_canonical_lint.py | 24 | 24 | 0 | |
| test_role_template_equivalence.py | 85 | 85 | 0 | |
| test_role_based_code_dispatch.py | 7 | 7 | 0 | 1 warning |
| test_pattern_hint_extension.py | 39 | 39 | 0 | |
| test_loop_substitution.py | 12 | 12 | 0 | |
| test_multimodal_text_intent_flag.py | 27 | 27 | 0 | |
| test_workflow_template_registration.py | 5 | 5 | 0 | |
| **TOTAL** | **265** | **265** | **0** | |

---

## §2 Benchmark comparison

### Fresh run (post-35b0c12 corpus fix)

| Metric | Soft-filter (production) | Baseline (embedding-only) |
|---|---:|---:|
| hit@1 | **0.8400** | 0.8200 |
| hit@3 | **0.9400** | 0.9500 |
| mode_accuracy | 0.7100 | 0.7100 |
| latency p50 | 98 ms | 108 ms |

### vs Ratchet baselines (§8 of 2026-05-16-migration-phase-closeout.md)

| Metric | Ratchet floor | Soft actual | Status |
|---|---:|---:|---|
| hit@1 ≥ 0.84 | 0.84 | **0.84** | PASS (at floor) |
| hit@3 ≥ 0.94 | 0.94 | **0.94** | PASS (at floor) |

### vs Baseline sanity thresholds (from task spec)

| Metric | Sanity floor | Baseline actual | Status |
|---|---:|---:|---|
| Baseline hit@1 ≥ 0.82 | 0.82 | **0.82** | PASS (at floor) |
| Baseline hit@3 ≥ 0.95 | 0.95 | **0.95** | PASS (at floor) |

### Notes on mode_accuracy

mode_accuracy=0.71 for both modes — this metric is corpus-level (what fraction of prompts
the correct `action_mode` is predicted). No ratchet floor was set for it. Stable across
runs at 0.71-0.72, consistent with R15d/R17 session notes.

---

## §3 Verdict

**GREEN — no regression.**

All 265 unit tests pass (0 failures). Benchmark metrics meet or exactly hit every ratchet
floor. The soft-filter hits its 0.84/0.94 ceilings and the baseline holds 0.82/0.95.

---

## §4 Bug found: _load_corpus KeyError (not a regression on metrics, but blocks re-runs)

**Commit that caused it:** `35b0c12` (Delete CP-NEW-peg-in-hole-single duplicate; remap refs to CP-58)

**What happened:** The commit rewrote `workspace/benchmarks/retrieval_100prompts.json`
from `{"version":"1.1","prompts":[...]}` (dict) to a plain `[...]` (list). The
`_load_corpus` function in `tests/test_retrieval_r17_100prompts.py` hardcoded
`return data["prompts"]`, causing `TypeError: list indices must be integers or slices, not str`
when attempting to re-run the benchmark.

**The benchmark result files** (`retrieval_100prompts_soft_2026-05-16.json`,
`retrieval_100prompts_baseline_2026-05-16.json`) were generated at 10:28-10:33,
before the 11:46 corpus rewrite commit, so they are valid and unaffected.

**Fix applied** (this session): `_load_corpus` now detects list vs dict:
```python
if isinstance(data, list):
    return data
return data["prompts"]
```

Fix confirmed: fresh benchmark re-run succeeds and reproduces identical metrics.

---

## §5 Next validation cadence

Recommended cadence for this test suite:

| Trigger | Action |
|---|---|
| Any commit touching `template_retriever.py`, `orchestrator.py`, or `text_modality.py` | Run all 12 test files + soft benchmark |
| Any canonical template add/delete/migrate | Run `test_canonical_lint.py` + `test_role_template_equivalence.py` |
| Weekly (Friday before session close) | Full 12-file suite + both benchmarks |
| Before any MULTIMODAL_TEXT_INTENT default change | Both benchmarks, compare delta vs ratchet |
| Before PR to upstream | Full suite + both benchmarks, embed results in PR body |

The `_load_corpus` fix should be committed before the next benchmark run so CI scripts
are not silently blocked.
