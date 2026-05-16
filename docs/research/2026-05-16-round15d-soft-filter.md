# Round 15d — Soft-Filter Hybrid Retrieval

**Date:** 2026-05-16  
**Prior round:** R17 (100-prompt benchmark, struct-filter regression confirmed)  
**Branch state:** post-R17 revert, `MULTIMODAL_TEXT_INTENT` default=`off`

---

## §1 Algorithm Description

### Problem (R17)
Hard-filter (`retrieve_with_intent_filter`) restricts Stage-1 pool to 7–60 templates, then
queries ChromaDB with `$in` filter. Two failure modes:
1. ChromaDB HNSW `$in` truncation — when the candidate set is small (≤7), fewer results
   than requested are silently returned.
2. Stage-2 re-ranking within the restricted pool reorders templates differently from
   full-corpus ranking — the correct template can fall below rank 3 even though it was
   in the pool.

### Soft-Filter Pseudocode

```
def retrieve_with_intent_soft_filter(spec_intent, top_k, query, boost, oversample):
    # Step 1: full-corpus extended fetch — no $in, no truncation
    full_results = retrieve_templates_with_scores(query, top_k=top_k * oversample)

    # Step 2: build boost set — templates whose intent.pattern_hint matches spec
    is_null = _spec_is_null_signal(spec_intent)          # all-default → no boost
    spec_pattern = spec_intent["pattern_hint"] if not is_null else None
    boost_set = {tid for tid, tmpl in cache.items()
                 if tmpl.get("intent", {}).get("pattern_hint") == spec_pattern}

    # Step 3: apply boost multiplier to matching candidates
    for entry in full_results:
        if entry["task_id"] in boost_set and spec_pattern is not None:
            entry["similarity_boosted"] = entry["similarity"] * boost
            entry["boost_applied"] = True
        else:
            entry["similarity_boosted"] = entry["similarity"]
            entry["boost_applied"] = False

    # Step 4: re-sort by boosted similarity, return top_k
    return sorted(full_results, key=lambda x: x["similarity_boosted"], reverse=True)[:top_k]
```

Key properties:
- Full-corpus query: ChromaDB `$in` truncation bypassed entirely.
- Non-matching templates can still appear if their baseline similarity is high enough.
- Null-signal specs (extractor default, all-zero counts) get no boost → identical to baseline.
- Legacy templates (no `intent` field) neither boosted nor penalized.
- Returns extra fields `similarity_boosted` and `boost_applied` for diagnostics.

---

## §2 Files Modified + LOC Delta

| File | Change | LOC delta |
|---|---|---|
| `service/isaac_assist_service/chat/tools/template_retriever.py` | Add `retrieve_with_intent_soft_filter` function | +86 |
| `service/isaac_assist_service/chat/orchestrator.py` | Wire `MULTIMODAL_TEXT_INTENT=soft` → soft-filter path | +20 / -10 |
| `tests/test_retrieval_r17_100prompts.py` | Add soft mode support, `_get_intent_mode()`, `_run_soft_filter_retrieval()` | +40 |
| `tests/test_soft_filter_retrieval.py` | New unit test file (6 tests) | +207 |

**Net new code:** ~353 LOC. `retrieve_with_intent_filter` (hard) preserved unchanged for backward compat.

---

## §3 100-Prompt Aggregate Results

| Metric | Baseline (embed-only) | Hard-filter (TIMT=on) | Soft-filter (TIMT=soft, boost=1.10) | Soft vs baseline |
|---|---|---|---|---|
| hit@1 | 0.8200 | 0.7900 | **0.8400** | **+0.0200** |
| hit@3 | 0.9500 | 0.8900 | 0.9400 | -0.0100 |
| mode_accuracy | 0.7200 | 0.7000 | 0.7200 | 0.0000 |
| hard_instantiate_rate | 0.1600 | 0.1800 | 0.1600 | 0.0000 |
| latency p50 ms | 102.0 | 104.9 | ~110* | +8* |
| struct_filter_path | 0/100 | 59/100 | 0†/100 | — |

*Latency not re-measured in sweep; estimated from per-prompt overhead.  
†Soft-filter always queries full corpus; `path_taken` reports `soft_filter` or `soft_filter_null`.

### Boost sweep summary (hit@3 identical across oversample values 2–5):

| boost | hit@1 | hit@3 | delta h@1 | delta h@3 |
|---|---|---|---|---|
| 1.05 | 0.8300 | 0.9400 | +0.0100 | -0.0100 |
| **1.10** | **0.8400** | **0.9400** | **+0.0200** | **-0.0100** |
| 1.15 | 0.8400 | 0.9400 | +0.0200 | -0.0100 |
| 1.20 | 0.8300 | 0.9300 | +0.0100 | -0.0200 |
| 1.30 | 0.8200 | 0.9200 | 0.0000 | -0.0300 |
| 1.50 | 0.8000 | 0.9200 | -0.0200 | -0.0300 |

**Winner: boost=1.10** (tied at aggregate with 1.15 but fewer TOP1_SAME_TOP3_DIFF changes: 2 vs 8).

---

## §4 Per-Pattern_Hint Breakdown

| Pattern hint | N | Base h@1 | Hard h@1 | Soft h@1 | Base h@3 | Hard h@3 | Soft h@3 |
|---|---|---|---|---|---|---|---|
| insert | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigate | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| other | 30 | 0.900 | 0.900 | 0.900 | 0.967 | 0.967 | 0.967 |
| pick_place | 43 | 0.721 | 0.698 | **0.791** | **0.930** | 0.837 | 0.907 |
| sort | 12 | 0.750 | 0.583 | 0.667 | 0.917 | 0.750 | 0.917 |
| train | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

**Soft vs baseline by category:**

| Pattern hint | h@1 delta | h@3 delta | Verdict |
|---|---|---|---|
| insert | 0.000 | 0.000 | Neutral (tied) |
| navigate | 0.000 | 0.000 | Neutral (tied) |
| other | 0.000 | 0.000 | Neutral (tied) |
| pick_place | **+0.070** | -0.023 | Net win (h@1 large gain, h@3 small loss) |
| sort | -0.083 | 0.000 | Regression on h@1, neutral h@3 |
| train | 0.000 | 0.000 | Neutral (tied) |

**Categories where soft ≥ baseline:** insert (h@1 ✓, h@3 ✓), navigate (✓ ✓), other (✓ ✓), train (✓ ✓) = **4/6** on both metrics; pick_place (h@1 ✓, h@3 ✗) = **1/6 mixed**; sort (h@1 ✗, h@3 ✓) = **1/6 mixed**.

**Net: 5/6 categories have soft h@3 ≥ hard h@3. 4/6 categories have soft ≥ baseline on both metrics.**

### Residual regressions (boost=1.10, 1 LOST_AT_3, 1 LOST_AT_1 vs baseline)

- **N01** (LOST_AT_3): "2×2 grid palletizer, 4 cubes from conveyor onto pallet". GT=CP-08.
  Baseline top3: [CP-25, CP-19, CP-08]. Soft top3: [CP-25, CP-20, CP-19]. CP-20 (brick-layer 18 cubes,
  pick_place, boosted) pushed CP-08 out of top-3. Boost over-promotes CP-20.
- **B07** (LOST_AT_1 only): "UR10 routes colored cubes to color-match bins". GT=CP-82 (sort).
  Baseline top1=CP-82. Soft top1=CP-03 (pick_place). The pick_place boost elevates CP-03 above CP-82.
  CP-82 still in top-3 (not LOST_AT_3).

Both regressions are boost-caused: a different pattern_hint candidate is over-promoted.

### Gains (3 GAINED_AT_1 vs baseline)

- **B02**: Two-robot assembly → CP-02 now top-1 (was CP-51). Boost correctly promotes CP-02.
- **B06**: Single-station pick-place → CP-01 now top-1 (was CP-07). Correct promotion.
- **N11**: Belt handoff → CP-26 now top-1 (was CP-51). Correct promotion.

---

## §5 Verdict: Flag-Flip Candidate?

**Short answer: Not yet. Needs one more iteration.**

### Strengths of soft@1.10 vs baseline

- hit@1: **+0.020** (0.820 → 0.840) — clear improvement, 3 GAINED vs 1 LOST.
- pick_place h@1: **+0.070** (0.721 → 0.791) — the dominant category benefits strongly.
- Hard-filter regressions (pick_place h@3: -0.093, sort h@3: -0.167) are both recovered by soft.
- ChromaDB `$in` truncation bug is fully bypassed.
- 93/100 prompts return IDENTICAL_TOP3 to baseline — minimal disruption.

### Remaining gap

- hit@3: **-0.010** (0.950 → 0.940). The target is hit@3 ≥ 0.950 (baseline).
- Two root-cause regressions remain (N01, B07): boost over-promotes pick_place candidates into
  sort/palletize queries because pattern_hint matching is binary (same → boost) not weighted.
- Sort h@1: -0.083 (0.750 → 0.667). Sort is a small category (12 prompts) but the drop is real.

### Root cause of residual hit@3 gap

The oversample mechanism works correctly. The residual issue is that the boost is applied to ALL
pattern_hint-matching templates uniformly. CP-20 (brick-layer 18 cubes, pick_place) is boosted
equally with CP-08 (2×2 palletizer, pick_place) even though CP-20 is a poor match for N01's
prompt. The boost doesn't discriminate within the pattern_hint class.

### Proposed fix for R15e

Add a second-stage discriminator: only boost candidates that pass `_features_compatible +
_counts_compatible` (the same Stage-1 filters from hard-filter), but still draw from the full
corpus. This turns the binary "in boost_set" check into a more precise "in soft-Stage-1 set":

```python
boost_set = {
    tid for tid, tmpl in cache.items()
    if (tmpl.get("intent", {}).get("pattern_hint") == spec_pattern
        and _features_compatible(spec_features, tmpl.get("intent", {}).get("structural_features", {}))
        and _counts_compatible(spec_counts, tmpl.get("intent", {}).get("counts", {}), tolerance=1))
}
```

This narrows the boost set from ~43 (all pick_place) to ~10–15 (feature-compatible pick_place),
reducing over-promotion while retaining the full-corpus recall.

---

## §6 Follow-Up Round (R15e)

**Status: NOT ready to flip `MULTIMODAL_TEXT_INTENT` default to `soft`.**

Criteria to flip:
- hit@3 ≥ 0.950 (baseline level) — currently 0.940
- No new LOST_AT_3 categories vs baseline — currently 1 LOST_AT_3

**Proposed R15e tasks:**
1. Implement `_soft_boost_set_with_features(spec_intent)` → returns only templates passing
   feature+count compatibility from Stage-1 logic.
2. Wire into `retrieve_with_intent_soft_filter` as opt-in via `use_stage1_boost_set=True`.
3. Re-run 100-prompt benchmark with feature-gated boost at boost ∈ {1.10, 1.15, 1.20}.
4. If hit@3 ≥ 0.950 confirmed, flip default to `soft`.

**If R15e also misses hit@3:** Consider per-category boost values (e.g., boost=1.0 for sort to
avoid over-promotion, boost=1.15 for pick_place) via a pattern_hint → boost map in config.
