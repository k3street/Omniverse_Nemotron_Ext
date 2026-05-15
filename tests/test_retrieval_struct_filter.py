"""
test_retrieval_struct_filter.py
--------------------------------
Round 14: Structural-filter-first retrieval benchmark.

Exercises `retrieve_with_intent_filter` directly (approach B — parallel
harness) rather than modifying `test_retrieval_benchmark.py`.

Rationale for approach B:
  The original harness calls `retrieve_templates_with_scores` unconditionally.
  The env flag MULTIMODAL_TEXT_INTENT only affects the orchestrator layer —
  it is invisible to the harness test. Instead of patching the production
  harness (risk of changing baseline semantics), this parallel script runs
  the same 30 prompts through the orchestrator-equivalent path:
    1. extract_intent_rules(prompt) → Intent
    2. retrieve_with_intent_filter(intent_dump, top_k=3)  (Stage 1+2)
    3. If Stage 1 returns 0 candidates → fallback to retrieve_templates_with_scores

This mirrors what orchestrator.py:895-920 does when MULTIMODAL_TEXT_INTENT=on.

Run with:
    python tests/test_retrieval_struct_filter.py

Output written to:
    workspace/benchmarks/retrieval_30prompts_struct_on_2026-05-15.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_BENCHMARK_DIR = _REPO_ROOT / "workspace" / "benchmarks"
_CORPUS_FILE = _BENCHMARK_DIR / "retrieval_30prompts.json"
_RESULTS_FILE = _BENCHMARK_DIR / "retrieval_30prompts_struct_on_post_r15_2026-05-15.json"
_BASELINE_FILE = _BENCHMARK_DIR / "retrieval_30prompts_baseline_2026-05-15.json"

_CANONICAL_MIN_SIM = 0.45
_CANONICAL_MIN_MARGIN = 0.20


def _load_corpus() -> List[Dict]:
    with open(_CORPUS_FILE) as f:
        data = json.load(f)
    return data["prompts"]


def _run_struct_retrieval(prompt: str, top_k: int = 3) -> tuple[List[Dict], float, str]:
    """Run struct-filter-first retrieval. Returns (scored_list, latency_ms, path_taken).

    path_taken: 'struct_filter' | 'fallback_embedding'
    """
    from service.isaac_assist_service.multimodal.text_modality import produce_layout_spec_from_text
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_with_intent_filter,
        retrieve_templates_with_scores,
    )

    t0 = time.perf_counter()

    spec = produce_layout_spec_from_text(prompt)
    intent_dump = spec.intent.model_dump(mode="json")

    # retrieve_with_intent_filter has fallback_to_embedding_only=True by default.
    # R15 fix: pass original_query=prompt so Stage 2 and fallback use the
    # user prompt for embedding (not the structured fingerprint).
    scored = retrieve_with_intent_filter(intent_dump, top_k=top_k, original_query=prompt)

    # Determine which path was actually used — proxy: if struct filter candidates
    # exist (intent-matched), it was struct; otherwise fallback.
    from service.isaac_assist_service.chat.tools.template_retriever import filter_templates_by_intent
    candidates = filter_templates_by_intent(intent_dump, count_tolerance=0)
    path_taken = "struct_filter" if candidates else "fallback_embedding"

    latency_ms = (time.perf_counter() - t0) * 1000.0
    return scored, latency_ms, path_taken


def _evaluate_prompt(entry: Dict) -> Dict:
    prompt_id = entry["id"]
    prompt_text = entry["prompt"]
    ground_truth_ids: List[Optional[str]] = entry.get("ground_truth", [])
    acceptable_alts: List[str] = entry.get("acceptable_alternatives", [])
    expected_action: str = entry.get("expected_action", "few_shot")
    category: str = entry.get("category", "unknown")
    complexity: str = entry.get("complexity", "unknown")

    gt_ids = {g for g in ground_truth_ids if g is not None}
    all_acceptable = gt_ids | set(acceptable_alts)

    scored, latency_ms, path_taken = _run_struct_retrieval(prompt_text, top_k=3)

    top1 = scored[0] if scored else None
    top2 = scored[1] if len(scored) > 1 else None

    top1_id = top1["task_id"] if top1 else None
    top1_sim = top1["similarity"] if top1 else 0.0
    top2_sim = top2["similarity"] if top2 else 0.0
    margin = top1_sim - top2_sim

    confident = top1_sim >= _CANONICAL_MIN_SIM and margin >= _CANONICAL_MIN_MARGIN
    actual_action = "hard_instantiate" if confident else "few_shot"

    is_null_gt = all(g is None for g in ground_truth_ids)
    if is_null_gt:
        hit_at_1 = not confident
        hit_at_3 = not confident
    else:
        hit_at_1 = top1_id in all_acceptable if top1_id else False
        top3_ids = {r["task_id"] for r in scored}
        hit_at_3 = bool(top3_ids & all_acceptable)

    mode_correct = actual_action == expected_action

    top3_details = []
    for r in scored:
        top3_details.append({
            "task_id": r["task_id"],
            "similarity": round(r["similarity"], 4),
            "distance": round(r["distance"], 4),
        })

    return {
        "id": prompt_id,
        "prompt": prompt_text,
        "category": category,
        "complexity": complexity,
        "ground_truth": ground_truth_ids,
        "acceptable_alternatives": acceptable_alts,
        "expected_action": expected_action,
        "actual_action": actual_action,
        "top1_id": top1_id,
        "top1_sim": round(top1_sim, 4),
        "margin": round(margin, 4),
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "mode_correct": mode_correct,
        "latency_ms": round(latency_ms, 2),
        "path_taken": path_taken,
        "top3": top3_details,
    }


def _compute_category_stats(results: List[Dict]) -> Dict:
    cats: Dict[str, List] = defaultdict(list)
    for r in results:
        cats[r["category"]].append(r)
    cat_stats = {}
    for cat, items in sorted(cats.items()):
        n = len(items)
        cat_stats[cat] = {
            "n": n,
            "hit_at_1": round(sum(i["hit_at_1"] for i in items) / n, 3),
            "hit_at_3": round(sum(i["hit_at_3"] for i in items) / n, 3),
            "mode_correct": round(sum(i["mode_correct"] for i in items) / n, 3),
            "avg_top1_sim": round(sum(i["top1_sim"] for i in items) / n, 3),
        }
    return cat_stats


def _compute_latency_percentiles(results: List[Dict]) -> Dict:
    latencies = sorted(r["latency_ms"] for r in results)
    n = len(latencies)
    p50 = latencies[n // 2] if n > 0 else 0.0
    p95_idx = int(n * 0.95)
    p95 = latencies[min(p95_idx, n - 1)] if n > 0 else 0.0
    return {"p50_ms": round(p50, 2), "p95_ms": round(p95, 2)}


def run():
    corpus = _load_corpus()
    assert len(corpus) == 30, f"Expected 30 prompts, got {len(corpus)}"

    # Ensure collection/cache initialized
    from service.isaac_assist_service.chat.tools.template_retriever import (
        _get_collection, _template_cache
    )
    col = _get_collection()
    n_indexed = col.count() if col else 0
    n_with_intent = sum(1 for t in _template_cache.values() if t.get("intent"))

    print(f"\n{'='*70}")
    print("Isaac Assist Retrieval Benchmark — Round 15 STRUCT FILTER ON (prompt-query fix)")
    print(f"Path: retrieve_with_intent_filter (structural-filter-first)")
    print(f"Coverage: {n_with_intent}/{n_indexed} templates have intent field")
    print(f"Thresholds: sim>={_CANONICAL_MIN_SIM}, margin>={_CANONICAL_MIN_MARGIN}")
    print(f"{'='*70}\n")

    results = []
    print(f"{'ID':5s} {'Category':20s} {'H@1':4s} {'H@3':4s} {'Mode':8s} {'Path':10s} {'Top-1':25s} {'Sim':6s} {'Margin':7s}")
    print("-" * 100)

    for entry in corpus:
        r = _evaluate_prompt(entry)
        results.append(r)
        h1_mark = "Y" if r["hit_at_1"] else "N"
        h3_mark = "Y" if r["hit_at_3"] else "N"
        mode_mark = "Y" if r["mode_correct"] else "N"
        print(
            f"{r['id']:5s} {r['category']:20s} {h1_mark:4s} {h3_mark:4s} "
            f"{mode_mark:8s} {r['path_taken']:10s} {str(r['top1_id'] or 'None'):25s} "
            f"{r['top1_sim']:6.3f} {r['margin']:7.3f}"
        )

    n = len(results)
    hit_at_1 = sum(r["hit_at_1"] for r in results) / n
    hit_at_3 = sum(r["hit_at_3"] for r in results) / n
    mode_accuracy = sum(r["mode_correct"] for r in results) / n
    hard_instantiate_rate = sum(r["actual_action"] == "hard_instantiate" for r in results) / n
    cat_stats = _compute_category_stats(results)
    latency_pcts = _compute_latency_percentiles(results)

    struct_count = sum(1 for r in results if r["path_taken"] == "struct_filter")
    fallback_count = sum(1 for r in results if r["path_taken"] == "fallback_embedding")

    print(f"\n{'='*70}")
    print("AGGREGATE METRICS")
    print(f"{'='*70}")
    print(f"  hit@1:              {hit_at_1:.3f}  ({hit_at_1*100:.1f}%)")
    print(f"  hit@3:              {hit_at_3:.3f}  ({hit_at_3*100:.1f}%)")
    print(f"  mode_accuracy:      {mode_accuracy:.3f}  ({mode_accuracy*100:.1f}%)")
    print(f"  hard_instantiate_rate: {hard_instantiate_rate:.3f}  ({hard_instantiate_rate*100:.1f}%)")
    print(f"  struct_filter_path: {struct_count}/{n} prompts  fallback: {fallback_count}/{n}")
    print(f"  latency p50:        {latency_pcts['p50_ms']:.1f} ms")
    print(f"  latency p95:        {latency_pcts['p95_ms']:.1f} ms")

    # Load baseline for delta comparison
    baseline = None
    if _BASELINE_FILE.exists():
        with open(_BASELINE_FILE) as f:
            baseline = json.load(f)
        b_agg = baseline["aggregate"]
        print(f"\n{'='*70}")
        print("DELTA vs BASELINE (embedding-only)")
        print(f"{'='*70}")
        print(f"  hit@1:         {hit_at_1:.3f} vs {b_agg['hit_at_1']:.3f}  delta={hit_at_1 - b_agg['hit_at_1']:+.3f}")
        print(f"  hit@3:         {hit_at_3:.3f} vs {b_agg['hit_at_3']:.3f}  delta={hit_at_3 - b_agg['hit_at_3']:+.3f}")
        print(f"  mode_accuracy: {mode_accuracy:.3f} vs {b_agg['mode_accuracy']:.3f}  delta={mode_accuracy - b_agg['mode_accuracy']:+.3f}")

    print(f"\n{'='*70}")
    print("PER-CATEGORY BREAKDOWN")
    print(f"{'='*70}")
    print(f"{'Category':25s} {'N':4s} {'hit@1':7s} {'hit@3':7s} {'mode%':7s} {'avg_sim':7s}")
    print("-" * 62)
    for cat, stats in cat_stats.items():
        suffix = ""
        if baseline and cat in baseline.get("category_breakdown", {}):
            b_cat = baseline["category_breakdown"][cat]
            d1 = stats["hit_at_1"] - b_cat["hit_at_1"]
            suffix = f"  (h@1 delta={d1:+.3f})"
        print(
            f"{cat:25s} {stats['n']:4d} {stats['hit_at_1']:7.3f} "
            f"{stats['hit_at_3']:7.3f} {stats['mode_correct']:7.3f} "
            f"{stats['avg_top1_sim']:7.3f}{suffix}"
        )

    print(f"\n{'='*70}")
    print("PROMPT-LEVEL CHANGES vs BASELINE")
    print(f"{'='*70}")
    if baseline:
        baseline_by_id = {r["id"]: r for r in baseline.get("per_prompt", [])}
        for r in results:
            b = baseline_by_id.get(r["id"])
            if b:
                if r["hit_at_1"] != b["hit_at_1"]:
                    direction = "GAINED" if r["hit_at_1"] else "LOST"
                    print(
                        f"  {direction} [{r['id']}] {r['category']:20s} "
                        f"gt={r['ground_truth']}  now={r['top1_id']}({r['top1_sim']:.3f}) "
                        f"was={b['top1_id']}({b['top1_sim']:.3f})  path={r['path_taken']}"
                    )
        print("  (no changes = struct filter produces identical ranking for these prompts)")

    # Build output
    output = {
        "benchmark": "retrieval_30prompts_struct_filter",
        "date": "2026-05-15",
        "round": 15,
        "retrieval_path": "retrieve_with_intent_filter (struct-first, fallback to embedding)",
        "env_flag": "MULTIMODAL_TEXT_INTENT=on (equivalent)",
        "coverage": {
            "n_templates_with_intent": n_with_intent,
            "n_templates_total": n_indexed,
            "coverage_pct": round(n_with_intent / n_indexed * 100, 1) if n_indexed else 0,
        },
        "thresholds": {
            "sim_min": _CANONICAL_MIN_SIM,
            "margin_min": _CANONICAL_MIN_MARGIN,
        },
        "aggregate": {
            "hit_at_1": round(hit_at_1, 4),
            "hit_at_3": round(hit_at_3, 4),
            "mode_accuracy": round(mode_accuracy, 4),
            "hard_instantiate_rate": round(hard_instantiate_rate, 4),
            "struct_filter_path_count": struct_count,
            "fallback_embedding_count": fallback_count,
            "latency_p50_ms": latency_pcts["p50_ms"],
            "latency_p95_ms": latency_pcts["p95_ms"],
        },
        "baseline_delta": {
            "hit_at_1": round(hit_at_1 - (baseline["aggregate"]["hit_at_1"] if baseline else hit_at_1), 4),
            "hit_at_3": round(hit_at_3 - (baseline["aggregate"]["hit_at_3"] if baseline else hit_at_3), 4),
            "mode_accuracy": round(mode_accuracy - (baseline["aggregate"]["mode_accuracy"] if baseline else mode_accuracy), 4),
        } if baseline else None,
        "category_breakdown": cat_stats,
        "per_prompt": results,
    }

    _BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    with open(_RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results written to: {_RESULTS_FILE}")
    print(f"\nBENCHMARK RESULT: hit@1={hit_at_1:.3f} hit@3={hit_at_3:.3f} "
          f"mode_accuracy={mode_accuracy:.3f} hard_instantiate_rate={hard_instantiate_rate:.3f}")
    print(f"  struct_path={struct_count}/{n}  fallback={fallback_count}/{n}")


if __name__ == "__main__":
    run()
