# Round 2 / Audit 4 — Metric Delta After Canonical-Migration Session

**Date:** 2026-05-15  
**Auditor:** Sonnet 4.6 (read-only run)  
**Scope:** Retrieval quality delta after session work: 62 motion_controller migrations, 15 CPs Wilson-status, 212 T2 qa_status, 3 CPs role-migration (CP-09/10/11), 4 corpus re-labels (B11/B12/B13/B19), 30 ghost-corpus remaps.  
**Pre-session baseline:** `workspace/benchmarks/retrieval_30prompts_PRE_SESSION_2026-05-15.json` (written 15:40)  
**Post-session run:** `workspace/benchmarks/retrieval_30prompts_baseline_2026-05-15.json` (run during this audit, 16:30+)  

---

### §1 Metric Delta Summary

| Metric | Baseline (initial, 15:40) | After session (re-run) | Δ | Significance |
|---|---|---|---|---|
| hit@1 | 0.8333 (25/30) | 0.8333 (25/30) | **0.0** | No change — expected (index unchanged) |
| hit@3 | 0.9333 (28/30) | 0.9333 (28/30) | **0.0** | No change — expected |
| mode_accuracy | 0.5667 (17/30) | 0.7000 (21/30) | **+0.1333 (+13.3pp)** | Real — pure relabeling effect on 4 prompts |
| hard_instantiate_rate | 0.1667 (5/30) | 0.1667 (5/30) | **0.0** | System behavior unchanged — confirmed |
| avg_latency_ms | 101.28 | 105.07 | +3.8 ms | Noise — within run-to-run variance |

**Note on sample size:** 30 prompts → 1 prompt = 3.3pp. Any delta ≤1 prompt (≤3.3pp) is noise. The hit@1/hit@3 zero-deltas are structurally certain (not noise), and the +13.3pp mode_accuracy delta is exactly 4 prompts (confirmed below). No ambiguity.

---

### §2 Per-Category Breakdown

| Category | n | PRE hit@1 | POST hit@1 | Δh@1 | PRE hit@3 | POST hit@3 | Δh@3 | PRE mode | POST mode | Δmode |
|---|---|---|---|---|---|---|---|---|---|---|
| amr_navigation | 2 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 0.000 | **1.000** | **+1.000** |
| contact_rich | 2 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| industrial_bridge | 1 | 0.000 | 0.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| multi_robot | 2 | 0.500 | 0.500 | 0 | 1.000 | 1.000 | 0 | 0.000 | 0.000 | 0 |
| navigate | 1 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| no_match | 5 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 0.000 | 0.000 | 0 |
| palletize | 1 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| pick_place | 5 | 0.600 | 0.600 | 0 | 0.800 | 0.800 | 0 | 0.800 | 0.800 | 0 |
| reorient | 1 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| rl_training | 2 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 0.000 | **1.000** | **+1.000** |
| ros2_bridge | 2 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 0.500 | 0.500 | 0 |
| sensor_setup | 3 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| sort | 2 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 | 1.000 | 1.000 | 0 |
| vision | 1 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | 0 | 1.000 | 1.000 | 0 |

**Mechanically explained:** The 4 re-labeled prompts were B11 (amr_navigation), B12 (amr_navigation), B13 (rl_training), B19 (rl_training). All four had `expected_action` changed from `few_shot` to `hard_instantiate`. The system was already hard-instantiating all four correctly (margins 0.2096–0.3448, all above the 0.20 gate). The corpus labels were the incorrect part, not the retrieval.

**Remaining open failures (unchanged after session):**

| ID | Category | actual_action | expected_action | top1_id | top1_sim | margin |
|---|---|---|---|---|---|---|
| B01 | pick_place | few_shot | hard_instantiate | CP-77 | 0.4784 | 0.0820 |
| B02 | multi_robot | few_shot | hard_instantiate | CP-51 | 0.5479 | 0.0309 |
| B06 | pick_place | few_shot | few_shot | CP-07 | 0.5785 | 0.0027 |
| B10 | vision | few_shot | few_shot | CP-34 | 0.5544 | 0.0006 |
| B28 | industrial_bridge | few_shot | few_shot | F-02 | 0.3088 | — |

B01 fails because CP-77 (nested box packer) outscores CP-01 in the 321-template corpus — embedding collision from shared "Franka+conveyor+bin" vocabulary. B02 fails because CP-51 outscores CP-02 (margin 0.03, under the 0.20 gate). B06 and B10 are hit@1 misses on acceptable margins but hit@3 catches them. B28 (industrial_bridge) is a hit@3 but sim 0.27 below the hard-instantiate floor.

---

### §3 Structural-Filter Test

**Coverage:** After session, 8/321 templates have `intent` field (CP-01..05, CP-09, CP-10, CP-11). Pre-session it was 5/321.

**Test result:** NOT RUNNABLE against the benchmark harness. The benchmark calls `retrieve_templates_with_scores` directly (`tests/test_retrieval_benchmark.py:62`) — it does not exercise the `MULTIMODAL_TEXT_INTENT=on` path. The env flag is only checked in `orchestrator.py:895` (production chat path), not in the test harness.

**Structural filter bug found:** `filter_templates_by_intent` reads from `_template_cache` (module-level dict), which is only populated by `_build_index` (`template_retriever.py:101`). When the collection already exists (born 2026-05-07), `_get_collection` calls `get_collection` without `_build_index` (`template_retriever.py:49`). Result: `_template_cache` is empty, structural filter always returns 0 candidates, `retrieve_with_intent_filter` always falls back to embedding-only. This is a silent no-op — not a crash, not a logged error, but the structural filter never actually fires in production.

**Manual test (post rebuild_index):** With `_template_cache` populated, `filter_templates_by_intent({'pattern_hint': 'pick_place', ...})` correctly returns 8 candidates (CP-01..05, CP-09..11). The filter logic is correct; only the cache-load path is broken for the persistent-index case.

**Quantitative structural-filter impact:** Cannot be measured without the MULTIMODAL_TEXT_INTENT path working end-to-end (requires LLM call to produce `spec_intent` from natural language). At 8/321 coverage, the filter would only help for pick_place-pattern prompts — 5 of 30 benchmark prompts (B01, B02, B04, B09, and pick_place subset). Even if it fired, it would narrow the candidate set to ≤8 before embedding similarity, which could hurt (excludes non-intent-tagged pick_place variants like CP-07, CP-22, CP-77) or help (focuses on verified pick_place canonicals).

---

### §4 Embedding-Cache Freshness

**Status: STALE for session changes — but irrelevant to retrieval accuracy.**

**Evidence:**
- ChromaDB SQLite born: 2026-05-07 02:19 (`stat workspace/tool_index/chroma.sqlite3`)
- Templates modified today: 15:52–15:59 (CP-09/10/11 role migration), 15:41 (15 CPs Wilson), ~15:40 (212 T2 qa_status), ~14:31 (30 ghost remaps)
- Index built from disk: only on collection-first-creation (2026-05-07)
- No `rebuild_index()` call anywhere in the benchmark test or corpus (`tests/test_retrieval_benchmark.py:1-250`, `grep`-verified)

**Why staleness doesn't matter here:**  
The embed document for each template is `goal + thoughts + tools_used` (`template_retriever.py:95`). Every session change was to fields OUTSIDE this triple:
- `motion_controllers` field (new field, not in embed)
- `verified_status`, `verified_wilson_lower`, `verified_runs` (not in embed)
- `qa_status`, `qa_status_meta` (not in embed)
- `intent`, `roles`, `role_defaults`, `code_template`, `verify_args_template` (not in embed)

None of the session's 321 template touches changed `goal`, `thoughts`, or `tools_used`. The embeddings at index-build time (2026-05-07) are identical to what would be re-embedded from the current disk state. The index is stale with respect to the new fields but **fresh with respect to the only fields that drive retrieval**.

**File citations:**
- Embed doc construction: `template_retriever.py:95` — `doc = f"{goal}\n\n{thoughts}\n\n{tools}".strip()`
- Lazy-load short-circuit: `template_retriever.py:39` — `if _collection is not None: return _collection`
- Rebuild path: `template_retriever.py:217-230` — `rebuild_index()` (not called by harness)

---

### §5 Interpretation

**What changed for the better:**  
`mode_accuracy` increased from 56.7% to 70.0% (+13.3pp). This is a real, mechanically certain improvement — 4 prompts that were misclassified as `few_shot` expected (despite the system correctly hard-instantiating with high margins) are now labeled `hard_instantiate` expected. The corpus labels were wrong; the system was right all along. The re-label confirms the retrieval system was already performing better than the corpus credit implied.

**What got worse:** Nothing. No regression in any metric.

**What changed but is invisible:** The 62 motion_controller migrations, 15 Wilson-status changes, 212 qa_status additions, and 30 ghost-corpus remaps are metadata improvements with zero retrieval impact — by design. They enrich the template schema for downstream use (role-based instantiation, honest status tracking, role-template-index), not for embedding similarity.

**Real vs noise:**  
- hit@1 +0.0 = structural zero, not noise  
- hit@3 +0.0 = structural zero, not noise  
- mode_accuracy +13.3pp = 4/30 prompts, deterministically explained, real  
- hard_instantiate_rate +0.0 = system behavior unchanged, confirmed identical similarity/margin values in per-prompt data  

**Remaining retrieval problems (pre-existing, not introduced):**  
B01 (CP-77 occludes CP-01) and B02 (CP-51 occludes CP-02) are the main hit@1 misses. Both are embedding-collision problems caused by the 321-template corpus having many pick_place and multi_robot variants with overlapping vocabulary. These are not addressed by any session work and require either re-embedding with discriminating goal text or threshold tuning.

---

### §6 Round 3 Backlog

**P0 — structural-filter cache bug (blocks intent-filter from ever working):**  
`template_retriever._build_index` must be called whenever the collection loads from disk, OR `_template_cache` must be populated by reading template JSONs from `_TEMPLATES_DIR` on `_get_collection` path without re-embedding. Current code only populates `_template_cache` as a side-effect of embedding (`_build_index:101`). Fix: add a cache-warm pass at `_get_collection` line 56 (after successful `get_collection`) that reads `*.json` from `_TEMPLATES_DIR` into `_template_cache` without calling ChromaDB add.  
**File/line:** `template_retriever.py:49-60`

**P1 — B01 embedding collision (CP-77 occludes CP-01):**  
B01 "Build a Franka pick-place cell with a conveyor belt and a single bin" retrieves CP-77 (sim 0.4784, margin 0.082) instead of CP-01. Root cause: CP-77's `thoughts` tokens include "Franka", "conveyor", "bin" at high frequency. Fix options: (a) add discriminating tokens to CP-01 `thoughts` (e.g., "single-station", "standard Franka cell", "baseline pick-place"), or (b) force-rebuild index after goal/thoughts edits.  
**Impact:** 1/30 hit@1 miss + 1/30 hit@3 miss (B01 is not in top-3 for CP-01 at all)

**P2 — Expand benchmark to 100 prompts:**  
At n=30, one prompt = 3.3pp noise floor. The +13.3pp mode_accuracy gain required careful causal tracing (it happened to be cleanly explained by exactly 4 relabels). Future metric deltas that are 1–2 prompts (3–7pp) will be ambiguous. A 100-prompt benchmark reduces the noise floor to 1pp and allows reliable detection of smaller improvements.

**P3 — Delta-CI check on each commit:**  
Add a GitHub Actions step that runs `python -m pytest tests/test_retrieval_benchmark.py -s -m ''` and asserts `hit@1 >= 0.83` and `hit@3 >= 0.93` (current floor). Fail the step if either regresses by >3pp. This would have caught B01 regression if it were introduced by template edits.

**P4 — Structural filter end-to-end test (no LLM required):**  
The `retrieve_with_intent_filter` function accepts a `spec_intent` dict directly — it does not require an LLM. Add a unit test that calls `rebuild_index()`, then `retrieve_with_intent_filter` with a hand-crafted `spec_intent` for CP-01's intent shape, and asserts CP-01 appears in top-3. This would immediately expose the `_template_cache` staleness bug.
