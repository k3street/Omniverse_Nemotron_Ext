# Round 14: Structural-Filter-First Retrieval Benchmark

**Date**: 2026-05-15
**Intent coverage at run time**: 65/321 templates (20.2%)
**Baseline**: `retrieval_30prompts_baseline_2026-05-15.json` — hit@1=0.833, hit@3=0.933, mode_accuracy=0.700
**Results**: `retrieval_30prompts_struct_on_2026-05-15.json`

---

## §1 Setup

### Harness approach chosen: (B) — parallel script

The original harness (`tests/test_retrieval_benchmark.py`) calls
`retrieve_templates_with_scores` directly and is not affected by `MULTIMODAL_TEXT_INTENT`.
Rather than patching the production harness (which would break baseline semantics and
confuse future comparison runs), a parallel script `tests/test_retrieval_struct_filter.py`
was written. It mirrors exactly what `orchestrator.py:895-920` does when the flag is on:

1. `produce_layout_spec_from_text(prompt)` → `Intent` (rule-based extractor)
2. `intent.model_dump(mode="json")` → `intent_dump`
3. `retrieve_with_intent_filter(intent_dump, top_k=3)` — Stage 1 structural filter + Stage 2 embedding

If Stage 1 returns 0 candidates (no template has a matching `pattern_hint`), the function
falls back to embedding-only over the full corpus — but critically, using the **canonical
structural fingerprint as the query text**, not the original user prompt.

### Coverage stats

- 65/321 templates have `intent` field (20.2%)
- Of 22 benchmark ground-truth templates, 10 have intent: CP-01, CP-02, CP-03, CP-04,
  CP-05, CP-82, CP-NEW-3station-oee, CP-47, CP-64, CP-55
- 12 ground-truth templates have no intent: CP-52, CP-NEW-amr-pickup-handoff,
  CP-NEW-multi-amr-corridor, CP-NEW-rl-clone-env, CP-87, S-07, M-01, FX-01, P-01,
  M-08, CP-58, CP-NEW-opcua-12conveyors

---

## §2 Aggregate Results

| Metric                 | Baseline (embed-only) | Struct filter ON | Delta      |
|------------------------|-----------------------|------------------|------------|
| hit@1                  | 0.833                 | 0.233            | **-0.600** |
| hit@3                  | 0.933                 | 0.267            | **-0.667** |
| mode_accuracy          | 0.700                 | 0.533            | **-0.167** |
| hard_instantiate_rate  | 0.167                 | 0.000            | -0.167     |
| struct_filter_path     | —                     | 16/30            | —          |
| fallback_embedding     | —                     | 14/30            | —          |

The structural filter causes a catastrophic regression. hit@1 drops from 83.3% to 23.3%.

---

## §3 Per-Category Breakdown

| Category           | N | Baseline h@1 | Struct h@1 | Delta     |
|--------------------|---|--------------|------------|-----------|
| amr_navigation     | 2 | 1.000        | 0.000      | -1.000    |
| contact_rich       | 2 | 1.000        | 0.000      | -1.000    |
| industrial_bridge  | 1 | 0.000        | 0.000      | 0.000     |
| multi_robot        | 2 | 0.500        | 0.000      | -0.500    |
| navigate           | 1 | 1.000        | 0.000      | -1.000    |
| no_match           | 5 | 1.000        | 1.000      | 0.000     |
| palletize          | 1 | 1.000        | 1.000      | 0.000     |
| pick_place         | 5 | 0.600        | 0.000      | -0.600    |
| reorient           | 1 | 1.000        | 0.000      | -1.000    |
| rl_training        | 2 | 1.000        | 0.000      | -1.000    |
| ros2_bridge        | 2 | 1.000        | 0.000      | -1.000    |
| sensor_setup       | 3 | 1.000        | 0.000      | -1.000    |
| sort               | 2 | 1.000        | 0.500      | -0.500    |
| vision             | 1 | 0.000        | 0.000      | 0.000     |

Only `no_match` and `palletize` are unaffected. Every category with a
positive baseline regresses. No category gains.

---

## §4 Root Cause Analysis

Two independent failure modes cause the regression:

### Failure Mode A: Struct-filter returns wrong candidates (16 prompts)

When Stage 1 finds matching templates (same `pattern_hint`), Stage 2 embeds the
**canonical structural fingerprint** against only those candidates. The fingerprint
(`pattern_hint=pick_place; features=destination_kind=single_bin,n_robot_stations=1,...`)
is a structured fact string, NOT the natural language goal text that the ChromaDB
embeddings were built from. The sentence-transformer model returns near-random rankings
when queried with a structured fact string against natural language documents.

Example — B30 (contact_rich, gt=CP-55):
- Struct path returns CP-44 (sim=0.387, pick_place topology)
- Embedding path returns CP-55 (sim=0.766, reorient/compliance template)
- CP-55 HAS an intent field and IS in the 65-template set, yet the fingerprint
  query fails to surface it because the fingerprint text doesn't semantically
  match the template's goal text in embedding space.

Another example — B04 (pick_place, gt=CP-04):
- Stage 1 returns 1 candidate: CP-11 (the only template with `has_footprint_constraint=true`)
- But CP-04's intent has `has_footprint_constraint` as a feature flag, while
  the extracted spec features from "compact pick-and-place cell 2x2 meter footprint"
  do NOT fire `has_footprint_constraint` (the rule-based extractor uses `has_bounded_footprint`,
  a different key) — so the filter passes CP-11 but not CP-04.

### Failure Mode B: Fallback uses fingerprint as query (14 prompts)

When Stage 1 finds 0 candidates, `retrieve_with_intent_filter` falls back to
`retrieve_templates_with_scores(fingerprint, top_k=top_k)` — embedding search
using the fingerprint string rather than the original user prompt. This degrades
ALL fallback cases because the fingerprint is not semantically equivalent to the
prompt in embedding space.

Example — B05 (reorient, gt=CP-05):
- fingerprint: `pattern_hint=reorient; features=destination_kind=single_bin,...`
- Original prompt yields CP-05 at sim=0.569
- Fingerprint yields CP-51 at sim=0.416, CP-05 not in top-3

Example — B11 (amr_navigation, gt=CP-NEW-amr-pickup-handoff):
- Fingerprint query loses CP-NEW-amr-pickup-handoff (sim drops from 0.571 to not-ranked)
- This template has no intent field, so it could only win via the fallback path

### Summary of failure modes

| Root cause                                  | Prompts affected | Hits lost |
|---------------------------------------------|------------------|-----------|
| Struct filter: fingerprint query degrades ranking | 10 struct_filter prompts | ~10 |
| Fallback: fingerprint used instead of original prompt | 14 fallback prompts | ~8 additional |
| Struct filter: feature key mismatch (extractor vs template schema) | B04 | 1 |

---

## §5 Honest Verdict: Flip Default to ON?

**NO. Hard stop. The struct-filter path is not production-ready.**

Three independent data points support this verdict:

1. **hit@1 drops 0.600 points** — from 83.3% to 23.3%. Even a single prompt
   regressing would be a concern; 18/30 prompts actively worsening is disqualifying.

2. **The fallback is broken by design.** `retrieve_with_intent_filter` uses the
   fingerprint as the fallback query. For 14/30 prompts where Stage 1 returned
   no candidates, the fallback was worse than vanilla embedding. The fix is to
   preserve the original prompt and use it in the fallback:
   ```python
   # In retrieve_with_intent_filter, the signature should be:
   def retrieve_with_intent_filter(spec_intent, top_k=3, count_tolerance=0,
       original_query=None, fallback_to_embedding_only=True):
       ...
       if not candidates:
           query = original_query or fingerprint  # <-- use original when available
           return retrieve_templates_with_scores(query, top_k=top_k)
   ```

3. **The fingerprint is not an embedding query string.** ChromaDB was indexed with
   natural-language goal + thoughts + tools. A structured fact string like
   `pattern_hint=pick_place; features=destination_kind=single_bin` returns
   semantically irrelevant embeddings. Stage 2 needs to either: (a) embed the
   fingerprint at index time and store as a separate embedding field, or
   (b) use the original user prompt for Stage 2 similarity within the candidate set.

4. **20.2% coverage is too sparse** for the filter to be useful even if the
   above bugs were fixed. Of 10 ground-truth templates with intent, the filter
   only correctly surfaces them for 1/10 benchmark prompts (B06 gt=CP-01 reaches
   CP-01 via struct but wrong answer surfaces first).

---

## §6 Follow-Up Work Needed

Priority order for making structural filter useful:

### P0 — Fix the fallback query (1-line fix, low risk)

In `retrieve_with_intent_filter` (template_retriever.py:523), add `original_query`
parameter and pass it to the fallback `retrieve_templates_with_scores` call.
The orchestrator already has the user message; it needs to thread it through.

### P1 — Fix Stage 2 query for struct-path prompts

Stage 2 currently embeds the structural fingerprint against the natural-language
corpus. Options:
- (a) Use original user prompt for Stage 2 similarity within the candidate set
  (simplest; reuses existing embedding quality)
- (b) Index templates with a dual embedding: goal-text + fingerprint-text
  (more infrastructure; payoff is better long-term)

Option (a) is 5 lines of change: pass `original_query` to Stage 2 ChromaDB query.

### P2 — Align rule-based extractor feature keys with template intent keys

The extractor fires `has_bounded_footprint` (text_modality.py:86) but templates
use `has_footprint_constraint` (visible in CP-04's intent). A single-pass audit
of the 65 migrated templates' feature key names vs extractor output keys would
surface all such mismatches.

### P3 — Expand intent coverage before re-testing

At 20.2% coverage, the structural filter has too few candidates to be useful.
The analysis above shows that even fixing P0+P1, the filter would only improve
~8 of the 30 benchmark prompts (those whose GT templates have intent fields AND
whose extracted intent matches). Target 60%+ before a meaningful re-evaluation.

### P4 — Add count fields to migrated templates

All 65 migrated templates have `counts: null`. The rule-based extractor infers
counts from the prompt (e.g., "single bin" → bins=1, "conveyor belt" → conveyors=1).
With strict count matching (tolerance=0), nearly every count-bearing prompt fails
Stage 1. Either populate template counts during migration or set default
`count_tolerance=1` in the orchestrator call.

---

**Decision**: Keep `MULTIMODAL_TEXT_INTENT` default as `off`. Fix P0+P1 before
any further testing. Revisit when coverage exceeds 60% and both query-path bugs
are resolved.
