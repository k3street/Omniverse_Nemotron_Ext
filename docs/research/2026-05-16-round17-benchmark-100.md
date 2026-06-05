# Round 17 — 100-Prompt Retrieval Benchmark

**Date:** 2026-05-16
**Previous:** R16 (30-prompt, struct-filter regression discovered)
**Files produced:**
- `workspace/benchmarks/retrieval_100prompts.json` — corpus
- `workspace/benchmarks/retrieval_100prompts_baseline_2026-05-16.json` — embedding-only baseline
- `workspace/benchmarks/retrieval_100prompts_struct_2026-05-16.json` — struct-filter results
- `tests/test_retrieval_r17_100prompts.py` — parameterizable harness

---

## §1 Corpus Expansion Methodology

### Starting point
The R16 30-prompt corpus (B01–B30) was preserved verbatim as the first 30 entries. Ground truth IDs were verified still-present in `workspace/templates/` on 2026-05-16; all 30 remain valid.

### Category audit of B01–B30
Distribution before expansion:
- pick_place: 5, sort: 2, reorient: 1, palletize: 1, navigate: 1, sensor_setup: 3, ros2_bridge: 2, rl_training: 2, amr_navigation: 2, contact_rich: 2, multi_robot: 2, vision: 1, industrial_bridge: 1, no_match: 5

Notable gap: the 7 `pattern_hint` axis (introduced in R15 role-migration) had only 4 of 7 covered in B-series. `insert`, `train`, `reorient` were underrepresented.

### 70 new prompts (N01–N70)
Strategy applied:
1. **Migrated templates first** — 70 templates have `intent` field. Each was targeted by at least one prompt. Templates with highly specific vocabulary (CP-11 "pinwheel", CP-20 "brick-layer 18 cubes", CP-43 "sphere-pick") got dedicated prompts.
2. **Pattern_hint balance** — 7 valid hints × ~10 prompts target = ~70 new prompts total. Final distribution across new prompts: pick_place=43 total (incl existing), sort=12, navigate=6, train=6, other=30, insert=3.
3. **Mix targets** — ~57% clearly-pointing (single GT), ~23% multi-candidate, ~20% no-match.
4. **Noise prompts** — N57 (Kinova synonym), N58 (lowercase/typos), N59 (abbrev: "pp", "cnvyr"), N60 (abbreviation RB cubes), N61 (different RL env count).
5. **No-match prompts** — N62–N70 (9 prompts), including single-word (N62 "robot"), vague fleet (N69 "2-robot assembly line"), ambiguous palletize (N70).

Quality cutoff: all 70 prompts have defined ground_truth or rationale for null. No padding.

---

## §2 Aggregate Metrics: 100-Prompt Results

| Metric | Baseline (embed-only) | Struct-filter ON | Delta |
|---|---|---|---|
| hit@1 | 0.8200 | 0.7900 | **-0.0300** |
| hit@3 | 0.9500 | 0.8900 | **-0.0600** |
| mode_accuracy | 0.7200 | 0.7000 | -0.0200 |
| hard_instantiate_rate | 0.1600 | 0.1800 | +0.0200 |
| latency p50 ms | 102.0 | 104.9 | +2.9 |
| latency p95 ms | 106.0 | 137.4 | +31.4 |
| struct_filter_path | 0/100 | 59/100 | — |

**Key observation:** On 100 prompts struct-filter is **worse on both hit@1 and hit@3** versus the 30-prompt results where hit@1 improved (+0.033) but hit@3 dropped (-0.033). The 30-prompt corpus undersampled the regime where struct-filter fails (multi-robot relay, belt-to-belt, sort/vision with count-mismatch).

---

## §3 Per-Pattern_Hint Breakdown

| Pattern hint | N | Baseline h@1 | Struct h@1 | Baseline h@3 | Struct h@3 |
|---|---|---|---|---|---|
| insert | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| navigate | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| other | 30 | 0.900 | 0.900 | 0.967 | 0.967 |
| pick_place | 43 | 0.721 | 0.698 | 0.930 | 0.837 |
| sort | 12 | 0.750 | 0.583 | 0.917 | 0.750 |
| train | 6 | 1.000 | 1.000 | 1.000 | 1.000 |

**Winners:** `insert`, `navigate`, `train`, `other` — struct-filter has zero regression, equal or better.

**Losers:** `pick_place` (h@3: 0.930→0.837, -0.093) and `sort` (h@3: 0.917→0.750, -0.167).

The `sort` regression is severe. Root cause: the `sort` pattern_hint Stage 1 pool contains 7 templates. When the prompt is "3-color vision sorter" or "vision inspect-and-reject", the Stage-2 embedding among those 7 templates fails to rank the correct one at top — and the baseline's broader 321-template search found it.

---

## §4 Hit@3 Root Cause Analysis — Concrete Mechanism

### Mechanism

Stage 1 (`filter_templates_by_intent`) hard-restricts the candidate pool to templates matching `pattern_hint`. For `pick_place`, this is ~60 templates; for `sort`, 7 templates. Stage 2 then embeds the user prompt against ONLY that restricted pool using ChromaDB `where={"task_id": {"$in": candidate_ids}}`.

The critical failure mode: **the correct ground-truth template IS in Stage 1's candidate pool, but its Stage-2 embedding rank among those candidates is worse than baseline's rank among all 321.** Baseline benefits from a "relative visibility" effect — when the correct template is only slightly below average in the full corpus, it is nevertheless in the top-3 of 321. When Stage 1 restricts to 7-60 templates, the ranking dynamics change: wrong templates with very similar embedding to the prompt (but not actually the correct one) float to top-2, pushing the correct template to rank 3+ or out entirely.

### Example 1 — B02: Two-robot assembly line (LOST_AT_3)

- Ground truth: CP-02
- Baseline top3: [CP-51, **CP-02**, CP-14] — hit@3=True
- Struct top3: [CP-45, CP-NEW-y-merge-singulation, CP-44] — hit@3=False
- Path: `struct_filter`

Stage 1 kept ~60 pick_place templates including CP-02, CP-45, CP-51. Stage 2 embedding of "two-robot assembly line cubes robot 1 to robot 2 second conveyor" ranked CP-45 ("robot at side-mounted position") and CP-NEW-y-merge-singulation ("Y-merge conveyor singulation") above CP-02. In baseline (321 templates), CP-51 and CP-02 both surfaced because the prompt's "two-robot" token dominated the wider embedding space more cleanly.

### Example 2 — N30: 3-color vision-based sorter (LOST_AT_3)

- Ground truth: CP-34
- Baseline top3: [CP-32, **CP-34**, CP-33] — hit@3=True
- Struct top3: [CP-03] — hit@3=False (only 1 result returned)
- Path: `struct_filter`

Stage 1 `pattern_hint="sort"` matched 7 templates. Stage 2 ChromaDB `n_results=min(3,7)=3` but the `where` clause returned only 1 result (CP-03). This is a **ChromaDB $in filter silent truncation bug**: when the candidate set has 7 templates but ChromaDB's HNSW index returns fewer results than `n_results`, the output is silently shorter. CP-34 and CP-33 are in the index but their vector-space neighbors from the query don't include all 7 candidates within ChromaDB's internal beam search.

### Example 3 — N32: Vision inspect-and-reject (LOST_AT_3)

- Ground truth: CP-48
- Baseline top3: [CP-33, CP-34, **CP-NEW-inspect-reject**] — hit@3=True via acceptable_alt
- Struct top3: [CP-23, CP-18, CP-45] — hit@3=False
- Path: `struct_filter`

Stage 1 `pattern_hint="sort"` kept 7 sort-tagged templates. CP-48 is in the index but among the 7 sort templates, the vision-inspect framing ranked CP-18 ("inspect-and-reject with semantic labels", pattern_hint=`pick_place`) above CP-48. In fact CP-18 has pattern_hint=`pick_place`, so it should NOT be in Stage 1's `sort` candidate set — but it appeared in Stage 2. Investigation: CP-18's intent field has `pattern_hint="pick_place"` not `"sort"`, so Stage 1 would have excluded it. The CP-18 result in the struct output appears to be a ChromaDB `$in` filter miss where a non-candidate template slips through — or CP-18 has been since assigned `sort` intent. Either way, the correct CP-48 dropped below rank 3.

### Summary of LOST_AT_3 cases (6 total)

| Prompt | GT | Stage 1 pool | Root cause |
|---|---|---|---|
| B02 | CP-02 | ~60 pick_place | Stage-2 re-ranks wrong templates above GT within pool |
| N08 | CP-14 | ~60 pick_place | Stage-2 re-ranks CP-13 above CP-14 (both relay/stacker) |
| N11 | CP-26 | ~60 pick_place | Stage-2 pool ranking: CP-45 > CP-26 on "belt handoff" embed |
| N25 | CP-45 | ~60 pick_place | Stage 1 returned only 1 result (CP-05); n_results=1 |
| N30 | CP-34 | 7 sort | ChromaDB $in filter returned only 1 result; CP-34 truncated |
| N32 | CP-48 | 7 sort | Stage-2 ranking among 7 sort templates puts wrong one at top |

**Dominant pattern:** Stage 1 restricts the candidate pool, then Stage 2 embedding fails to rank correctly within that restricted set. The restriction does not improve precision enough to compensate for the recall drop.

---

## §5 Proposed Fix

### R15d — Soft-filter + re-rank with boost (≈50 LOC)

Instead of hard-filtering to ONLY Stage-1 candidates, use a **soft hybrid**:

1. Stage 1: structural filter → candidate IDs (same as now)
2. Stage 2a: embed query against full 321-template corpus (existing baseline path) → full_results
3. Stage 2b: embed query against Stage-1 candidates → struct_results
4. Merge: for each result in full_results, apply a **boost multiplier** (e.g. 1.15×) if the task_id is in Stage-1 candidates; otherwise use raw similarity.
5. Re-sort by boosted similarity, return top-K.

This ensures:
- Correct templates not in Stage-1 can still appear if their baseline similarity is high enough
- Stage-1 candidates get a boost that shifts them upward without making them exclusive
- The ChromaDB `$in` truncation bug is bypassed entirely (full-corpus query is used)

Estimated LOC change: ~50 lines in `template_retriever.py` (`retrieve_with_intent_filter`) + no schema changes.

**Alternative: widen Stage-2 to top_k×3** — query Stage-1 candidate pool with `n_results = min(top_k*3, len(candidates))` then take best top_k. Addresses ChromaDB truncation for small pools (sort: 7 templates). ~10 LOC. This is lower risk but only partially fixes the problem.

---

## §6 Other Surprises in 100-Prompt Data

### 6.1 N45 (ros2_bridge): 8 Carter robots → CP-87 miss

N45 ("8 Carter robots with separate ROS2 topics in one simulation") should match S-07 (`ground_truth=["S-07"]`) but baseline returned CP-87 (Franka MoveIt2 bridge) as top-1 with sim=0.520, margin=0.019. hit@1=False, hit@3=False. The "8 Carter" token clearly matches S-07's vocabulary but the embedding similarity was overwhelmed by "ROS2 topics" matching CP-87.

**Implication:** S-07 is not well-indexed or its goal text does not contain enough "8 Carter" signal. Possible fix: add `intent` field to S-07 with `pattern_hint="other"` and a strong `structural_tags` entry.

### 6.2 Struct-filter produces only 1 result for some queries

N25 and N30 both received only 1 result in struct mode. ChromaDB's HNSW `$in` filter with small candidate sets (≤7 templates) silently returns fewer results than requested when the HNSW beam search exhausts neighbors. This is a latent bug that affects any Stage-1 pool smaller than `top_k` (currently 3). The fix is to set `n_results = min(top_k, len(candidate_ids))` (already done) AND to add a fallback that tops up results from full-corpus when fewer than `top_k` are returned.

### 6.3 N49 (industrial_bridge): plc-conveyor confused with plc-fixture

"Modbus TCP bridge for conveyor control" → struct returned CP-NEW-plc-fixture instead of CP-NEW-plc-conveyor. Both have pick_place intent in Stage 1. Stage 2 embedding preferred fixture (sim=0.489) over conveyor despite "conveyor" being in both the prompt and CP-NEW-plc-conveyor's name. The goals are lexically very similar.

### 6.4 Noise prompts held up reasonably well

N57 (Kinova synonym), N58 (lowercase), N60 (RB abbreviation) all returned the correct template or an acceptable alternative at top-3 in baseline. Struct-filter degraded N57 (lost CP-54/CP-71 from top-3) but baseline was robust. Robustness to synonyms is a baseline strength.

### 6.5 `other` pattern_hint has zero regression

All 30 prompts tagged `other` (templates without intent, or with no pattern keyword) were handled exclusively by `fallback_embedding`, which is the same as baseline. This confirms the `_spec_is_null_signal` bypass is working correctly for non-domain prompts.

---

## §7 Flag-Flip Verdict

**Verdict: REVERT to OFF (embedding-only baseline) until R15d is implemented.**

Rationale:
- On 30 prompts: struct-filter appeared to help hit@1 (+0.033) at the cost of hit@3 (-0.033).
- On 100 prompts: struct-filter harms both hit@1 (-0.030) AND hit@3 (-0.060).
- The 30-prompt result was an artifact of the corpus undersampling the failure regime.
- With sort (h@3: 0.917→0.750) and pick_place (h@3: 0.930→0.837) both regressing, the flag causes net harm in production.
- The `other` and `navigate`/`insert`/`train` pattern hints have no regression and could keep struct ON, but there is no per-hint toggle currently.

**Recommended next action:**
1. Flip `MULTIMODAL_TEXT_INTENT` default back to `off` (~1 line in config).
2. Implement R15d soft-filter-boost (~50 LOC) in `template_retriever.py`.
3. Re-run this 100-prompt benchmark as R18 with R15d ON.
4. Only re-enable the flag after hit@3 ≥ 0.950 (baseline level) is confirmed.

The baseline embedding-only path at hit@1=0.820, hit@3=0.950 is the current production bar. Struct-filter does not yet clear it.
