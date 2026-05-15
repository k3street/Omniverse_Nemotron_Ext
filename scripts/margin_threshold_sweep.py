"""
margin_threshold_sweep.py
-------------------------
Post-processing sweep over (sim_thr, margin_thr) pairs using the pre-computed
per-prompt similarity scores from the 2026-05-15 baseline JSON.

No re-embedding. No ChromaDB. Pure arithmetic over the baseline data.

Usage:
    python scripts/margin_threshold_sweep.py

Output:
    workspace/benchmarks/margin_threshold_sweep_2026-05-15.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE = (
    _REPO_ROOT / "workspace" / "benchmarks" / "retrieval_30prompts_baseline_2026-05-15.json"
)
_OUTPUT = (
    _REPO_ROOT / "workspace" / "benchmarks" / "margin_threshold_sweep_2026-05-15.json"
)

# Cross-product thresholds to sweep
SIM_THRESHOLDS = [0.40, 0.45, 0.50]
MARGIN_THRESHOLDS = [0.05, 0.10, 0.12, 0.15, 0.20]


def _hit_at_1(prompt: Dict) -> bool:
    """Whether the prompt's top-1 result is in ground_truth ∪ acceptable_alternatives.

    For null-GT prompts, hit@1 is defined as NOT hard-instantiating — that
    check is done separately; here we return the baseline hit_at_1 value
    (which for null-GT is always True because few-shot fires).
    """
    return prompt["hit_at_1"]


def _is_null_gt(prompt: Dict) -> bool:
    """True when all ground_truth entries are None (no-match / fallback prompts)."""
    return all(g is None for g in prompt["ground_truth"])


def _expected_hi(prompt: Dict) -> bool:
    """True when the benchmark corpus says this prompt SHOULD hard-instantiate."""
    return prompt["expected_action"] == "hard_instantiate"


def _top1_correct(prompt: Dict) -> bool:
    """True when the top-1 retrieved template is in the acceptable set."""
    top1_id = prompt["top1_id"]
    gt_ids = {g for g in prompt["ground_truth"] if g is not None}
    alts = set(prompt.get("acceptable_alternatives", []))
    return top1_id in (gt_ids | alts)


def evaluate_combo(
    prompts: List[Dict], sim_thr: float, margin_thr: float
) -> Tuple[Dict, List[Dict]]:
    """Compute aggregate metrics for a single (sim_thr, margin_thr) pair.

    Returns (aggregate_dict, per_prompt_list).
    """
    per_prompt = []
    n = len(prompts)

    hi_fires = 0       # prompts where hard-instantiate would fire
    hi_tp = 0          # fires AND top-1 is correct
    hi_fp = 0          # fires BUT expected_action != 'hard_instantiate'
    hit1_count = 0     # hit@1 (using baseline definition — see note below)

    for p in prompts:
        top1_sim = p["top1_sim"]
        margin = p["margin"]

        would_fire = top1_sim >= sim_thr and margin >= margin_thr

        if would_fire:
            hi_fires += 1

        # hit@1: for null-GT prompts, correct = NOT firing hard-instantiate.
        # For concrete-GT prompts, correct = top-1 is in acceptable set.
        if _is_null_gt(p):
            h1 = not would_fire
        else:
            h1 = _top1_correct(p)
        if h1:
            hit1_count += 1

        # True positive: fires AND top-1 is correct
        if would_fire and _top1_correct(p):
            hi_tp += 1

        # False positive: fires BUT ground truth says it should not
        # (either null-GT, OR expected_action == 'few_shot' / 'fallback')
        if would_fire and not _expected_hi(p):
            hi_fp += 1

        per_prompt.append({
            "id": p["id"],
            "category": p["category"],
            "expected_action": p["expected_action"],
            "top1_id": p["top1_id"],
            "top1_sim": p["top1_sim"],
            "margin": p["margin"],
            "top1_correct": _top1_correct(p),
            "would_fire": would_fire,
            "hit_at_1": h1,
            "is_tp": would_fire and _top1_correct(p),
            "is_fp": would_fire and not _expected_hi(p),
        })

    aggregate = {
        "sim_thr": sim_thr,
        "margin_thr": margin_thr,
        "hit_at_1": round(hit1_count / n, 4),
        "hard_instantiate_rate": round(hi_fires / n, 4),
        "true_positive_rate": round(hi_tp / n, 4),
        "false_positive_rate": round(hi_fp / n, 4),
    }
    return aggregate, per_prompt


def main() -> None:
    if not _BASELINE.exists():
        print(f"ERROR: baseline not found at {_BASELINE}", file=sys.stderr)
        sys.exit(1)

    with open(_BASELINE) as f:
        baseline = json.load(f)

    prompts: List[Dict] = baseline["per_prompt"]
    assert len(prompts) == 30, f"Expected 30 prompts, got {len(prompts)}"

    combos_aggregate: List[Dict] = []
    combos_per_prompt: Dict[str, List[Dict]] = {}

    print(f"{'sim_thr':>8} {'margin_thr':>10} {'hit@1':>7} {'hi_rate':>8} {'tp_rate':>8} {'fp_rate':>8}")
    print("-" * 55)

    for sim_thr in SIM_THRESHOLDS:
        for margin_thr in MARGIN_THRESHOLDS:
            agg, per = evaluate_combo(prompts, sim_thr, margin_thr)
            combos_aggregate.append(agg)
            key = f"sim{sim_thr}_margin{margin_thr}"
            combos_per_prompt[key] = per
            print(
                f"{sim_thr:>8.2f} {margin_thr:>10.2f} "
                f"{agg['hit_at_1']:>7.4f} {agg['hard_instantiate_rate']:>8.4f} "
                f"{agg['true_positive_rate']:>8.4f} {agg['false_positive_rate']:>8.4f}"
            )

    # --- Pareto frontier: max true_positive_rate where false_positive_rate < 0.05 ---
    pareto = [
        a for a in combos_aggregate if a["false_positive_rate"] < 0.05
    ]
    pareto.sort(key=lambda x: (-x["true_positive_rate"], -x["hit_at_1"]))
    recommended = pareto[0] if pareto else None

    print()
    if recommended:
        print(
            f"Recommended: sim>={recommended['sim_thr']}, margin>={recommended['margin_thr']}  "
            f"hit@1={recommended['hit_at_1']:.4f}  "
            f"tp_rate={recommended['true_positive_rate']:.4f}  "
            f"fp_rate={recommended['false_positive_rate']:.4f}"
        )
    else:
        print("No combo meets fp < 0.05 — keep current (0.45, 0.20)")

    # Current baseline row for comparison
    current = next(
        (a for a in combos_aggregate if a["sim_thr"] == 0.45 and a["margin_thr"] == 0.20),
        None,
    )
    if current:
        print(
            f"Current  : sim>=0.45, margin>=0.20  "
            f"hit@1={current['hit_at_1']:.4f}  "
            f"tp_rate={current['true_positive_rate']:.4f}  "
            f"fp_rate={current['false_positive_rate']:.4f}"
        )

    output = {
        "meta": {
            "source_baseline": str(_BASELINE),
            "date": "2026-05-15",
            "n_prompts": 30,
            "sim_thresholds": SIM_THRESHOLDS,
            "margin_thresholds": MARGIN_THRESHOLDS,
            "note": (
                "Pure post-processing — no re-embedding. "
                "hit@1 for null-GT prompts = NOT hard-instantiating."
            ),
        },
        "combos": combos_aggregate,
        "recommended": recommended,
        "per_combo_per_prompt": combos_per_prompt,
    }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to: {_OUTPUT}")


if __name__ == "__main__":
    main()
