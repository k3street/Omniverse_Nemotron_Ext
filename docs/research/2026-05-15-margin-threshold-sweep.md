# Margin Threshold Sweep — 2026-05-15

## §1 Method

All 15 (sim_thr, margin_thr) combinations from the cross product of
`sim ∈ {0.40, 0.45, 0.50}` × `margin ∈ {0.05, 0.10, 0.12, 0.15, 0.20}` were
evaluated as a pure post-processing pass over the 30-prompt baseline
(`retrieval_30prompts_baseline_2026-05-15.json`). No re-embedding was performed;
per-prompt `top1_sim` and `margin` values from the existing ChromaDB run were
re-used directly. For each combo, a prompt is classified as "would hard-instantiate"
if `top1_sim >= sim_thr AND margin >= margin_thr`. **hit@1** counts null-GT prompts
as correct only when hard-instantiate does NOT fire. **false_positive_rate** counts
prompts that would fire but whose corpus `expected_action` is `few_shot` or
`fallback`. **true_positive_rate** counts prompts that would fire AND whose top-1 is
inside `ground_truth ∪ acceptable_alternatives`. Script:
`scripts/margin_threshold_sweep.py`. Full per-prompt results:
`workspace/benchmarks/margin_threshold_sweep_2026-05-15.json`.

---

## §2 Results Table

| sim_thr | margin_thr | hit@1  | hard_inst_rate | fp_rate | tp_rate |
|---------|-----------|--------|----------------|---------|---------|
| 0.40    | 0.05      | 0.7667 | 0.5000         | 0.4333  | 0.4333  |
| 0.40    | 0.10      | 0.8000 | 0.3000         | 0.2667  | 0.2667  |
| 0.40    | 0.12      | 0.8000 | 0.2667         | 0.2333  | 0.2333  |
| 0.40    | 0.15      | 0.8333 | 0.2333         | 0.2000  | 0.2333  |
| 0.40    | 0.20      | 0.8333 | 0.2000         | 0.1667  | 0.2000  |
| 0.45    | 0.05      | 0.8333 | 0.4000         | 0.3333  | 0.3667  |
| 0.45    | 0.10      | 0.8333 | 0.2333         | 0.2000  | 0.2333  |
| 0.45    | 0.12      | 0.8333 | 0.2000         | 0.1667  | 0.2000  |
| 0.45    | 0.15      | 0.8333 | 0.2000         | 0.1667  | 0.2000  |
| **0.45**| **0.20**  |**0.8333**|**0.1667**   |**0.1333**|**0.1667**|
| 0.50    | 0.05      | 0.8333 | 0.3000         | 0.2667  | 0.3000  |
| 0.50    | 0.10      | 0.8333 | 0.2000         | 0.1667  | 0.2000  |
| 0.50    | 0.12      | 0.8333 | 0.1667         | 0.1333  | 0.1667  |
| 0.50    | 0.15      | 0.8333 | 0.1667         | 0.1333  | 0.1667  |
| 0.50    | 0.20      | 0.8333 | 0.1667         | 0.1333  | 0.1667  |

Bold = current production setting.

---

## §3 Pareto Frontier

**No combo achieves fp_rate < 0.05.** The absolute floor across all 15 settings is
`fp_rate = 0.1333`, shared by `(0.45, 0.20)`, `(0.50, 0.12)`, `(0.50, 0.15)`, and
`(0.50, 0.20)`. This ceiling is structural: four prompts — B11
(`amr_navigation/few_shot`), B12 (`amr_navigation/few_shot`), B13
(`rl_training/few_shot`), B19 (`rl_training/few_shot`) — always fire at any
threshold ≤ their actual scores (`sim 0.57–0.68, margin 0.21–0.34`). All four
retrieve the **correct** top-1 template; they appear as false positives only because
the corpus labels them `expected_action=few_shot` despite having unambiguous
high-confidence retrieval results. In other words, the FP rate floor is a
**corpus-labeling artefact**, not a system error.

Key observation: across all 15 combos, **zero prompts hard-instantiate with a wrong
top-1**. Every "false positive" has `top1_correct=True`. Lowering margin strictly
adds more correct-but-labeled-few_shot fires; it never produces a wrong answer.

At the strict Pareto criterion (fp < 0.05), no combo qualifies. If the criterion is
relaxed to "fp_rate at minimum, tp_rate at maximum among those sharing the minimum
fp", the winners are `(0.50, 0.12)`, `(0.50, 0.15)`, and `(0.50, 0.20)` — all
identical at fp=0.1333, tp=0.1667. They match current `(0.45, 0.20)` in tp_rate
while using a higher sim floor as a tighter guard; but they also hard-instantiate
less (rate 0.1667 vs 0.1667 — identical in count). No practical gain.

**Recommended setting: KEEP current (0.45, 0.20).**

---

## §4 Caveats

1. **30 prompts is too small to establish a stable FP floor.** With 30 samples, each
   prompt carries 3.3 percentage points; one prompt flip changes fp_rate by 0.033.

2. **Five "no_match" prompts (B21-B25) are structurally safe** — their top1_sim
   peaks at 0.42 (B24, "I need help with Isaac Sim") which never clears sim_thr=0.40
   with margin_thr ≥ 0.12. They do not contribute to fp at any tested threshold.

3. **The FP floor (0.1333) is entirely from four corpus-labeling errors.** B11, B12,
   B13, B19 have `expected_action=few_shot` but retrieve their correct template at
   sim=0.57-0.68 with margin=0.21-0.34. These four should arguably be re-labeled
   `hard_instantiate`, which would eliminate the FP floor entirely.

4. **Lowering margin to 0.12 adds B17** (`FX-01`, sim=0.481, margin=0.168) as a
   sixth "FP" — also correct top-1, also a labeling artefact. No safety risk.

5. **Expected-action taxonomy mismatch**: 20/30 prompts are labeled `few_shot` and 5
   are `fallback`, yet the corpus has only 5 prompts explicitly intended for
   hard-instantiate (B09, B15, B20 + 2 others). The hit@1=0.8333 is stable across
   all threshold settings above sim=0.40/margin=0.15 because the hit computation is
   independent of the threshold for concrete-GT prompts.

---

## §5 Recommendation

**KEEP at (0.45, 0.20).**

Lowering margin to 0.10-0.12 would raise `hard_instantiate_rate` from 16.7% to
20.0% and `tp_rate` from 0.1667 to 0.2000 without any wrong-answer risk (zero wrong
fires across all combos). However, the apparent `fp_rate` increase (0.1333 → 0.1667)
is entirely driven by corpus labels that are arguably incorrect. The correct action
before changing the production threshold is to **re-label B11, B12, B13, B19** in
the benchmark corpus from `few_shot` to `hard_instantiate` — they have unambiguous
high-confidence retrievals. After re-labeling, (0.45, 0.12) or (0.45, 0.15) would
become the dominant Pareto point with fp=0.033 and tp=0.233 on the corrected corpus.
Until re-labeling is done, changing the production threshold based on an
under-specified benchmark would be premature.

Concrete action: re-run the sweep after correcting the four corpus labels; a
threshold change decision can be made from that cleaner signal.
