# Round 3 Patch B — `_template_cache` Rehydration Fix

**Date**: 2026-05-15  
**Author**: Claude (Sonnet 4.6)  
**Status**: Implemented, tested, benchmark measured

---

## §1 Bug Repro

### Root Cause

`template_retriever.py:_get_collection()` has two execution paths:

| Path | Trigger | Calls `_build_index`? | `_template_cache` populated? |
|---|---|---|---|
| First-time build | Collection does not exist (exception at `get_collection`) | Yes (line 65) | Yes — `_build_index` populates it at line 132 |
| Orphan-empty state | Collection exists but `count() == 0` | Yes (line 55) | Yes |
| **Persistent load** | Collection exists with count > 0 | **No** | **No** |

The persistent-load path (the normal production path after first startup) only calls `get_collection()` and logs. `_template_cache` is never populated.

`filter_templates_by_intent` (line 396) iterates `_template_cache.items()` directly. With an empty cache, it returns `[]` for every query regardless of input.

### Verified Repro

```python
# Production index at workspace/tool_index/ has 321 entries
# Before fix — fresh module load against persisted index:
col = _get_collection()   # → loads existing collection, count=321
len(_template_cache)       # → 0  ← BUG
filter_templates_by_intent({"pattern_hint": "pick_place"})  # → []
```

```python
# After fix:
col = _get_collection()   # → loads, then calls _rehydrate_cache()
len(_template_cache)       # → 321
filter_templates_by_intent({"pattern_hint": "pick_place"})
# → ['CP-01', 'CP-02', 'CP-03', 'CP-04', 'CP-05', 'CP-09', 'CP-10', 'CP-11']
```

---

## §2 Approach Chosen

**Option (b): `_rehydrate_cache()` — disk scan without re-embedding.**

Three options were considered:

| Option | Description | Cost | Correctness |
|---|---|---|---|
| (a) Eager rebuild | Call `_build_index()` on persistent load | Re-embeds 321 templates + would fail with duplicate ChromaDB IDs | Incorrect |
| **(b) Rehydrate cache** | New `_rehydrate_cache()` reads disk → `_template_cache`, called on persistent-load path | O(n) disk reads, zero ChromaDB writes | Correct |
| (c) Bypass cache | `filter_templates_by_intent` scans disk directly | Changes the function signature; n disk reads per call | Correct but larger change |

Option (a) was immediately rejected: `_build_index` calls `_collection.add()`, which would throw on duplicate IDs for the existing 321 entries.

Option (b) is chosen: minimal change, correct, consistent with how `_build_index` populates the cache. Inserting the call into `_get_collection()` (rather than lazily inside `filter_templates_by_intent`) means any future function relying on `_template_cache` also benefits without additional changes.

---

## §3 Diff Summary

**File modified**: `service/isaac_assist_service/chat/tools/template_retriever.py`

| Change | Lines |
|---|---|
| New function `_rehydrate_cache()` | +22 lines (lines 69–91) |
| Added `else` branch in `_get_collection()` to call `_rehydrate_cache()` | +5 lines (lines 56–61) |
| **Total LOC delta** | **+27 lines** |

No other files modified (excluding test file and docs).

---

## §4 Test Coverage

**New test file**: `tests/test_template_cache_rehydration.py` — 9 tests, all marked `l0`.

| Test class | What it tests |
|---|---|
| `TestFirstTimeBuild::test_cache_populated_after_first_build` | After first-time `_build_index()`, `_template_cache` is non-empty |
| `TestFirstTimeBuild::test_intent_templates_in_cache_after_first_build` | All 8 intent-bearing templates (CP-01..05, 09..11) are in cache after first build |
| `TestPersistentIndexRehydration::test_cache_populated_on_persistent_load` | **Key regression test**: after module reload against persisted index, `_template_cache` is non-empty |
| `TestPersistentIndexRehydration::test_intent_templates_present_on_persistent_load` | All 8 intent-bearing templates present after rehydration |
| `TestFilterTemplatesByIntent::test_filter_returns_cp01_after_first_build` | `filter_templates_by_intent` returns CP-01 on first-build path |
| `TestFilterTemplatesByIntent::test_filter_returns_cp01_after_persistent_load` | `filter_templates_by_intent` returns CP-01 **after rehydration** |
| `TestFilterTemplatesByIntent::test_filter_returns_all_pick_place_templates` | Broad pick_place intent matches all 8 intent templates |
| `TestNoIntentTemplates::test_empty_result_when_no_intent_templates` | Returns `[]` (not crash) when templates dir has no intent fields |
| `TestNoIntentTemplates::test_no_crash_on_empty_cache` | No exception when templates dir is empty |

All 9 passed. Runtime: ~25 seconds (ChromaDB + sentence-transformers embed time for 3 index builds).

**Existing tests**: `test_canonical_lint.py` + `test_role_based_code_dispatch.py` — 31 passed, 0 failed.

---

## §5 Structural-Filter Benchmark Result

The 30-prompt benchmark (`test_retrieval_benchmark.py`) calls `retrieve_templates_with_scores` directly — it does NOT gate on `MULTIMODAL_TEXT_INTENT`. The env flag is wired only in `orchestrator.py:895`. Therefore, running the benchmark "with vs without the flag" produces identical results:

| Run | hit@1 | hit@3 | mode_accuracy |
|---|---|---|---|
| Without `MULTIMODAL_TEXT_INTENT` | 0.833 | 0.933 | 0.700 |
| With `MULTIMODAL_TEXT_INTENT=on` | 0.833 | 0.933 | 0.700 |

**This is expected**, not a sign the fix didn't work.

The structural filter's effect was measured directly:

- Before fix: `filter_templates_by_intent({"pattern_hint": "pick_place"})` → `[]` (cache empty)
- After fix: `filter_templates_by_intent({"pattern_hint": "pick_place"})` → 8 results including CP-01..05, 09..11

At 8/321 template coverage, the structural filter is active but narrow. It correctly narrows the candidate set for `pick_place` queries; non-pick_place queries (the majority of the 30-prompt corpus) are unaffected and fall back to embedding-only via the `fallback_to_embedding_only=True` default.

---

## §6 What This Unblocks

1. **Production correctness**: `MULTIMODAL_TEXT_INTENT=on` in `orchestrator.py` now functions correctly after every service restart, not just the first boot.

2. **Track B migration**: future intent field expansion to the remaining 313 templates will immediately engage the structural filter without code changes.

3. **Pattern_hint discrimination**: for queries where the structural filter has candidates, Stage 2 (ChromaDB similarity over canonical fingerprint) now operates over the structurally-narrowed set rather than the full 321, which is strictly better ranking signal.

4. **No regression**: the fix is purely additive — `_rehydrate_cache` only reads disk, never writes ChromaDB. The first-time build path, the orphan-empty state path, and the `rebuild_index()` path are all unchanged.
