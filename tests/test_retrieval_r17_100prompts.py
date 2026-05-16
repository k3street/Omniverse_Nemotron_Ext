"""
test_retrieval_r17_100prompts.py
---------------------------------
Round 17: 100-prompt retrieval benchmark.

Parameterizable via environment variable:
    BENCHMARK_CORPUS   — path to corpus JSON (default: retrieval_100prompts.json)
    MULTIMODAL_TEXT_INTENT — 'off' → embedding-only baseline; anything else → struct-filter ON
    RESULTS_FILE       — override output file path

Run baseline (embedding-only):
    MULTIMODAL_TEXT_INTENT=off python tests/test_retrieval_r17_100prompts.py > /tmp/r17_baseline.txt 2>&1

Run struct-filter (default ON):
    python tests/test_retrieval_r17_100prompts.py > /tmp/r17_struct.txt 2>&1

Output written to workspace/benchmarks/ based on mode and date.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_BENCHMARK_DIR = _REPO_ROOT / "workspace" / "benchmarks"
_TODAY = "2026-05-16"
_CANONICAL_MIN_SIM = 0.45
_CANONICAL_MIN_MARGIN = 0.20


def _get_corpus_path() -> Path:
    env = os.environ.get("BENCHMARK_CORPUS")
    if env:
        return Path(env)
    return _BENCHMARK_DIR / "retrieval_100prompts.json"


def _get_struct_flag() -> bool:
    """Return True if struct-filter mode is active (default ON)."""
    val = os.environ.get("MULTIMODAL_TEXT_INTENT", "on").lower().strip()
    return val not in ("off", "0", "false", "no")


def _get_results_path(struct_on: bool) -> Path:
    env = os.environ.get("RESULTS_FILE")
    if env:
        return Path(env)
    suffix = "struct" if struct_on else "baseline"
    return _BENCHMARK_DIR / f"retrieval_100prompts_{suffix}_{_TODAY}.json"


def _get_baseline_path() -> Optional[Path]:
    p = _BENCHMARK_DIR / f"retrieval_100prompts_baseline_{_TODAY}.json"
    return p if p.exists() else None


def _load_corpus(path: Path) -> List[Dict]:
    with open(path) as f:
        data = json.load(f)
    return data["prompts"]


def _run_struct_retrieval(prompt: str, top_k: int = 3) -> tuple[List[Dict], float, str]:
    """Run struct-filter-first retrieval. Returns (scored_list, latency_ms, path_taken)."""
    from service.isaac_assist_service.multimodal.text_modality import produce_layout_spec_from_text
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_with_intent_filter,
        filter_templates_by_intent,
    )

    t0 = time.perf_counter()
    spec = produce_layout_spec_from_text(prompt)
    intent_dump = spec.intent.model_dump(mode="json")
    scored = retrieve_with_intent_filter(intent_dump, top_k=top_k, original_query=prompt)
    candidates = filter_templates_by_intent(intent_dump, count_tolerance=0)
    path_taken = "struct_filter" if candidates else "fallback_embedding"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return scored, latency_ms, path_taken


def _run_embedding_retrieval(prompt: str, top_k: int = 3) -> tuple[List[Dict], float, str]:
    """Run embedding-only retrieval (baseline). Returns (scored_list, latency_ms, path_taken)."""
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_templates_with_scores,
    )

    t0 = time.perf_counter()
    scored = retrieve_templates_with_scores(prompt, top_k=top_k)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return scored, latency_ms, "embedding_only"


def _evaluate_prompt(entry: Dict, struct_on: bool) -> Dict:
    prompt_id = entry["id"]
    prompt_text = entry["prompt"]
    ground_truth_ids: List[Optional[str]] = entry.get("ground_truth", [])
    acceptable_alts: List[str] = entry.get("acceptable_alternatives", [])
    expected_action: str = entry.get("expected_action", "few_shot")
    category: str = entry.get("category", "unknown")
    complexity: str = entry.get("complexity", "unknown")
    pattern_hint: str = entry.get("pattern_hint", "")

    gt_ids = {g for g in ground_truth_ids if g is not None}
    all_acceptable = gt_ids | set(acceptable_alts)

    if struct_on:
        scored, latency_ms, path_taken = _run_struct_retrieval(prompt_text, top_k=3)
    else:
        scored, latency_ms, path_taken = _run_embedding_retrieval(prompt_text, top_k=3)

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

    result = {
        "id": prompt_id,
        "prompt": prompt_text,
        "category": category,
        "pattern_hint": pattern_hint,
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
    return result


def _compute_category_stats(results: List[Dict], key: str = "category") -> Dict:
    cats: Dict[str, List] = defaultdict(list)
    for r in results:
        cats[r.get(key, "unknown")].append(r)
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


def _per_prompt_diff(results: List[Dict], baseline_by_id: Dict[str, Dict]) -> List[Dict]:
    """Categorize each result relative to baseline."""
    diffs = []
    for r in results:
        b = baseline_by_id.get(r["id"])
        if not b:
            diffs.append({"id": r["id"], "category_change": "NO_BASELINE"})
            continue

        r_h1 = r["hit_at_1"]
        b_h1 = b["hit_at_1"]
        r_h3 = r["hit_at_3"]
        b_h3 = b["hit_at_3"]
        r_top3 = {x["task_id"] for x in r.get("top3", [])}
        b_top3 = {x["task_id"] for x in b.get("top3", [])}

        if r_h1 and not b_h1:
            change = "GAINED_AT_1"
        elif b_h1 and not r_h1:
            change = "LOST_AT_1"
        elif r_h3 and not b_h3:
            change = "GAINED_AT_3"
        elif b_h3 and not r_h3:
            change = "LOST_AT_3"
        elif r_top3 == b_top3:
            change = "IDENTICAL_TOP3"
        elif r["top1_id"] == b["top1_id"] and r_top3 != b_top3:
            change = "TOP1_SAME_TOP3_DIFF"
        else:
            change = "OTHER_DIFF"

        diffs.append({
            "id": r["id"],
            "category_change": change,
            "struct_hit_at_1": r_h1,
            "base_hit_at_1": b_h1,
            "struct_hit_at_3": r_h3,
            "base_hit_at_3": b_h3,
            "struct_top3": list(r_top3),
            "base_top3": list(b_top3),
            "struct_top1": r["top1_id"],
            "base_top1": b["top1_id"],
            "struct_path": r.get("path_taken", "?"),
        })
    return diffs


def run():
    corpus_path = _get_corpus_path()
    struct_on = _get_struct_flag()
    results_path = _get_results_path(struct_on)
    baseline_path = _get_baseline_path()

    mode_label = "STRUCT-FILTER ON" if struct_on else "EMBEDDING-ONLY BASELINE"
    print(f"\n{'='*70}")
    print(f"Isaac Assist Retrieval Benchmark — Round 17  [{mode_label}]")
    print(f"Corpus: {corpus_path}")
    print(f"Output: {results_path}")
    print(f"Thresholds: sim>={_CANONICAL_MIN_SIM}, margin>={_CANONICAL_MIN_MARGIN}")
    print(f"{'='*70}\n")

    corpus = _load_corpus(corpus_path)
    n_total = len(corpus)
    print(f"Loaded {n_total} prompts")

    # Warm up collection
    from service.isaac_assist_service.chat.tools.template_retriever import (
        _get_collection, _template_cache
    )
    col = _get_collection()
    n_indexed = col.count() if col else 0
    n_with_intent = sum(1 for t in _template_cache.values() if t.get("intent"))
    print(f"Template index: {n_indexed} total, {n_with_intent} with intent field\n")

    results = []
    print(f"{'ID':6s} {'Cat':20s} {'Hint':12s} {'H@1':4s} {'H@3':4s} {'Mode':5s} {'Path':10s} {'Top-1':28s} {'Sim':6s} {'Margin':7s}")
    print("-" * 110)

    for entry in corpus:
        r = _evaluate_prompt(entry, struct_on)
        results.append(r)
        h1 = "Y" if r["hit_at_1"] else "N"
        h3 = "Y" if r["hit_at_3"] else "N"
        m = "Y" if r["mode_correct"] else "N"
        print(
            f"{r['id']:6s} {r['category']:20s} {r.get('pattern_hint','?'):12s} "
            f"{h1:4s} {h3:4s} {m:5s} {r['path_taken']:10s} "
            f"{str(r['top1_id'] or 'None'):28s} {r['top1_sim']:6.3f} {r['margin']:7.3f}"
        )

    n = len(results)
    hit_at_1 = sum(r["hit_at_1"] for r in results) / n
    hit_at_3 = sum(r["hit_at_3"] for r in results) / n
    mode_accuracy = sum(r["mode_correct"] for r in results) / n
    hard_rate = sum(r["actual_action"] == "hard_instantiate" for r in results) / n
    cat_stats = _compute_category_stats(results, "category")
    hint_stats = _compute_category_stats(results, "pattern_hint")
    latency_pcts = _compute_latency_percentiles(results)

    struct_count = sum(1 for r in results if r["path_taken"] == "struct_filter")
    fallback_count = sum(1 for r in results if r["path_taken"] in ("fallback_embedding", "embedding_only"))

    print(f"\n{'='*70}")
    print("AGGREGATE METRICS")
    print(f"{'='*70}")
    print(f"  hit@1:              {hit_at_1:.4f}  ({hit_at_1*100:.1f}%)")
    print(f"  hit@3:              {hit_at_3:.4f}  ({hit_at_3*100:.1f}%)")
    print(f"  mode_accuracy:      {mode_accuracy:.4f}  ({mode_accuracy*100:.1f}%)")
    print(f"  hard_instantiate:   {hard_rate:.4f}  ({hard_rate*100:.1f}%)")
    print(f"  struct_path:        {struct_count}/{n}   fallback: {fallback_count}/{n}")
    print(f"  latency p50:        {latency_pcts['p50_ms']:.1f} ms")
    print(f"  latency p95:        {latency_pcts['p95_ms']:.1f} ms")

    # Load baseline for delta comparison (only when running struct mode)
    baseline = None
    if struct_on and baseline_path and baseline_path.exists():
        with open(baseline_path) as f:
            baseline = json.load(f)
        b_agg = baseline["aggregate"]
        print(f"\n{'='*70}")
        print("DELTA vs BASELINE (embedding-only)")
        print(f"{'='*70}")
        print(f"  hit@1:         {hit_at_1:.4f} vs {b_agg['hit_at_1']:.4f}  delta={hit_at_1 - b_agg['hit_at_1']:+.4f}")
        print(f"  hit@3:         {hit_at_3:.4f} vs {b_agg['hit_at_3']:.4f}  delta={hit_at_3 - b_agg['hit_at_3']:+.4f}")
        print(f"  mode_accuracy: {mode_accuracy:.4f} vs {b_agg['mode_accuracy']:.4f}  delta={mode_accuracy - b_agg['mode_accuracy']:+.4f}")

    print(f"\n{'='*70}")
    print("PER-CATEGORY BREAKDOWN")
    print(f"{'='*70}")
    print(f"{'Category':25s} {'N':4s} {'hit@1':7s} {'hit@3':7s} {'mode%':7s} {'avg_sim':7s}")
    print("-" * 62)
    for cat, stats in cat_stats.items():
        print(f"{cat:25s} {stats['n']:4d} {stats['hit_at_1']:7.3f} {stats['hit_at_3']:7.3f} {stats['mode_correct']:7.3f} {stats['avg_top1_sim']:7.3f}")

    print(f"\n{'='*70}")
    print("PER-PATTERN_HINT BREAKDOWN")
    print(f"{'='*70}")
    print(f"{'Pattern hint':20s} {'N':4s} {'hit@1':7s} {'hit@3':7s} {'mode%':7s} {'avg_sim':7s}")
    print("-" * 55)
    for ph, stats in hint_stats.items():
        print(f"{ph:20s} {stats['n']:4d} {stats['hit_at_1']:7.3f} {stats['hit_at_3']:7.3f} {stats['mode_correct']:7.3f} {stats['avg_top1_sim']:7.3f}")

    # Per-prompt diff vs baseline
    per_prompt_diffs = []
    if baseline:
        baseline_by_id = {r["id"]: r for r in baseline.get("per_prompt", [])}
        per_prompt_diffs = _per_prompt_diff(results, baseline_by_id)

        print(f"\n{'='*70}")
        print("PROMPT-LEVEL CHANGES vs BASELINE")
        print(f"{'='*70}")
        change_counts: Dict[str, int] = defaultdict(int)
        for d in per_prompt_diffs:
            change_counts[d["category_change"]] += 1
        for change, count in sorted(change_counts.items()):
            print(f"  {change}: {count}")

        print("\n  Detail (non-IDENTICAL):")
        for d in per_prompt_diffs:
            if d["category_change"] not in ("IDENTICAL_TOP3", "NO_BASELINE"):
                r = next((x for x in results if x["id"] == d["id"]), {})
                print(
                    f"  [{d['category_change']:20s}] {d['id']:6s} "
                    f"gt={r.get('ground_truth','?')}  "
                    f"struct_top1={d['struct_top1']}  base_top1={d['base_top1']}  "
                    f"path={d['struct_path']}"
                )

    # Identify LOST_AT_3 prompts (hit@3 regression)
    lost_at_3 = [d for d in per_prompt_diffs if d["category_change"] in ("LOST_AT_3", "LOST_AT_1")]
    if lost_at_3:
        print(f"\n{'='*70}")
        print(f"HIT@3 REGRESSION ANALYSIS ({len(lost_at_3)} prompts lost from top-3)")
        print(f"{'='*70}")
        for d in lost_at_3:
            r = next((x for x in results if x["id"] == d["id"]), {})
            b_r = (baseline or {}).get("per_prompt", [])
            b = next((x for x in b_r if x["id"] == d["id"]), {})
            print(f"\n  Prompt {d['id']}: {r.get('prompt','')[:70]}")
            print(f"    gt={r.get('ground_truth')}  path={d['struct_path']}")
            print(f"    baseline top3: {d['base_top3']}")
            print(f"    struct   top3: {d['struct_top3']}")
            print(f"    Analysis: struct-filter Stage 1 pattern_hint='{r.get('pattern_hint','?')}' "
                  f"restricted candidates, excluding baseline's correct top-3 member")

    # Build output
    baseline_delta = None
    if baseline:
        b_agg = baseline["aggregate"]
        baseline_delta = {
            "hit_at_1": round(hit_at_1 - b_agg["hit_at_1"], 4),
            "hit_at_3": round(hit_at_3 - b_agg["hit_at_3"], 4),
            "mode_accuracy": round(mode_accuracy - b_agg["mode_accuracy"], 4),
        }

    output = {
        "benchmark": "retrieval_100prompts",
        "date": _TODAY,
        "round": 17,
        "mode": "struct_filter" if struct_on else "embedding_only_baseline",
        "retrieval_path": "retrieve_with_intent_filter (struct-first, fallback to embedding)" if struct_on else "retrieve_templates_with_scores (embedding-only)",
        "env_flag": f"MULTIMODAL_TEXT_INTENT={'on' if struct_on else 'off'}",
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
            "hard_instantiate_rate": round(hard_rate, 4),
            "struct_filter_path_count": struct_count,
            "fallback_embedding_count": fallback_count,
            "latency_p50_ms": latency_pcts["p50_ms"],
            "latency_p95_ms": latency_pcts["p95_ms"],
        },
        "baseline_delta": baseline_delta,
        "category_breakdown": cat_stats,
        "pattern_hint_breakdown": hint_stats,
        "per_prompt_diffs": per_prompt_diffs if per_prompt_diffs else None,
        "per_prompt": results,
    }

    _BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results written to: {results_path}")
    print(f"\nBENCHMARK RESULT: hit@1={hit_at_1:.4f} hit@3={hit_at_3:.4f} "
          f"mode_accuracy={mode_accuracy:.4f} hard_rate={hard_rate:.4f}")
    print(f"  struct_path={struct_count}/{n}  fallback={fallback_count}/{n}")
    if baseline_delta:
        print(f"  delta vs baseline: hit@1={baseline_delta['hit_at_1']:+.4f} "
              f"hit@3={baseline_delta['hit_at_3']:+.4f}")


if __name__ == "__main__":
    run()
