"""
test_retrieval_benchmark.py
---------------------------
30-prompt retrieval benchmark for Isaac Assist template retriever.

This is a MEASUREMENT tool, not a pass/fail gate. pytest exit code is always 0.
Writes results to workspace/benchmarks/retrieval_30prompts_baseline_<date>.json.

Run with:
    python -m pytest tests/test_retrieval_benchmark.py -s -v -m ''

The -m '' is required to bypass the l0 marker filter in pytest.ini.

Infrastructure requirement: chromadb must be installed (pip install chromadb).
No Kit RPC or running Isaac Sim needed — retrieval is pure ChromaDB + sentence-transformers.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure service package is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_BENCHMARK_DIR = _REPO_ROOT / "workspace" / "benchmarks"
_CORPUS_FILE = _BENCHMARK_DIR / "retrieval_30prompts.json"
_RESULTS_FILE = _BENCHMARK_DIR / "retrieval_30prompts_baseline_2026-05-15.json"

# Hard-instantiate thresholds (mirrors orchestrator.py:870-874)
_CANONICAL_MIN_SIM = 0.45
_CANONICAL_MIN_MARGIN = 0.20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_corpus() -> List[Dict]:
    """Load the 30-prompt corpus from disk."""
    with open(_CORPUS_FILE) as f:
        data = json.load(f)
    return data["prompts"]


def _run_retrieval(prompt: str, top_k: int = 3) -> tuple[List[Dict], float]:
    """Call the production retriever. Returns (scored_list, latency_ms).

    scored_list: [{template, task_id, distance, similarity}, ...]
    """
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_templates_with_scores,
    )
    t0 = time.perf_counter()
    scored = retrieve_templates_with_scores(prompt, top_k=top_k)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return scored, latency_ms


def _evaluate_prompt(entry: Dict) -> Dict:
    """Run retrieval for one prompt entry and compute all metrics.

    Returns a per-prompt result dict compatible with the JSON output schema.
    """
    prompt_id = entry["id"]
    prompt_text = entry["prompt"]
    ground_truth_ids: List[Optional[str]] = entry.get("ground_truth", [])
    acceptable_alts: List[str] = entry.get("acceptable_alternatives", [])
    expected_action: str = entry.get("expected_action", "few_shot")
    category: str = entry.get("category", "unknown")
    complexity: str = entry.get("complexity", "unknown")
    notes: str = entry.get("notes", "")

    # Set of acceptable IDs (ground_truth ∪ acceptable_alternatives, excluding None)
    gt_ids = {g for g in ground_truth_ids if g is not None}
    all_acceptable = gt_ids | set(acceptable_alts)

    # Run retrieval
    scored, latency_ms = _run_retrieval(prompt_text, top_k=3)

    # Extract top results
    top1 = scored[0] if scored else None
    top2 = scored[1] if len(scored) > 1 else None
    top3 = scored[2] if len(scored) > 2 else None

    top1_id = top1["task_id"] if top1 else None
    top1_sim = top1["similarity"] if top1 else 0.0
    top2_sim = top2["similarity"] if top2 else 0.0
    margin = top1_sim - top2_sim

    # Tier classification (mirrors orchestrator.py:921-928)
    confident = top1_sim >= _CANONICAL_MIN_SIM and margin >= _CANONICAL_MIN_MARGIN
    actual_action = "hard_instantiate" if confident else "few_shot"

    # Hit computation
    # For null ground truth (fallback prompts), "hit" means we DID NOT hard-instantiate
    is_null_gt = all(g is None for g in ground_truth_ids)
    if is_null_gt:
        # Correct outcome: system should NOT hard-instantiate
        hit_at_1 = not confident
        hit_at_3 = not confident  # same criterion for null prompts
    else:
        hit_at_1 = top1_id in all_acceptable if top1_id else False
        top3_ids = {r["task_id"] for r in scored}
        hit_at_3 = bool(top3_ids & all_acceptable)

    # Mode accuracy: did the system choose the right action?
    mode_correct = actual_action == expected_action

    # Build top-3 details list
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
        "top3": top3_details,
        "notes": notes,
    }


def _compute_category_stats(results: List[Dict]) -> Dict:
    """Compute hit@1, hit@3, mode_correct per category."""
    from collections import defaultdict
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
    """Return p50 and p95 latency in ms."""
    latencies = sorted(r["latency_ms"] for r in results)
    n = len(latencies)
    p50 = latencies[n // 2] if n > 0 else 0.0
    p95_idx = int(n * 0.95)
    p95 = latencies[min(p95_idx, n - 1)] if n > 0 else 0.0
    return {"p50_ms": round(p50, 2), "p95_ms": round(p95, 2)}


def _identify_failure_cases(results: List[Dict]) -> List[Dict]:
    """Return up to 10 failure cases sorted by severity."""
    failures = [r for r in results if not r["hit_at_1"]]
    # Sort by: wrong action first, then by top1_sim descending (high sim + wrong answer = worst)
    failures.sort(key=lambda r: (r["mode_correct"], -r["top1_sim"]))
    return failures[:10]


# ---------------------------------------------------------------------------
# Pytest benchmark test (marked '' to bypass l0 filter — run explicitly)
# ---------------------------------------------------------------------------

class TestRetrievalBenchmark:
    """Benchmark suite — always passes. Results written to JSON."""

    def test_retrieval_benchmark_runs(self):
        """Run the 30-prompt benchmark and write results to disk.

        This test always passes. It is a measurement, not an assertion.
        """
        # Ensure the corpus exists
        assert _CORPUS_FILE.exists(), (
            f"Corpus file not found: {_CORPUS_FILE}\n"
            "Run task to generate workspace/benchmarks/retrieval_30prompts.json first."
        )

        corpus = _load_corpus()
        assert len(corpus) == 30, f"Expected 30 prompts, got {len(corpus)}"

        print(f"\n{'='*70}")
        print("Isaac Assist Retrieval Benchmark — 2026-05-15")
        print(f"Corpus: {_CORPUS_FILE}")
        print(f"Templates directory: {_REPO_ROOT / 'workspace' / 'templates'}")
        print(f"Thresholds: sim>={_CANONICAL_MIN_SIM}, margin>={_CANONICAL_MIN_MARGIN}")
        print(f"{'='*70}\n")

        # Verify ChromaDB is available and the index has all 321 templates
        try:
            from service.isaac_assist_service.chat.tools.template_retriever import (
                _get_collection,
            )
            col = _get_collection()
            n_indexed = col.count() if col else 0
            print(f"ChromaDB collection: {n_indexed} templates indexed")
            if n_indexed < 300:
                print(
                    f"WARNING: Only {n_indexed} templates indexed (expected 321). "
                    "Run rebuild_index() to re-index all templates."
                )
        except Exception as e:
            print(f"WARNING: Could not check collection count: {e}")
            n_indexed = -1

        # Run all 30 prompts
        results = []
        print(f"{'ID':5s} {'Category':20s} {'H@1':4s} {'H@3':4s} {'Mode':8s} {'Top-1':25s} {'Sim':6s} {'Margin':7s} {'ms':6s}")
        print("-" * 95)

        for entry in corpus:
            r = _evaluate_prompt(entry)
            results.append(r)
            h1_mark = "✓" if r["hit_at_1"] else "✗"
            h3_mark = "✓" if r["hit_at_3"] else "✗"
            mode_mark = "✓" if r["mode_correct"] else "✗"
            print(
                f"{r['id']:5s} {r['category']:20s} {h1_mark:4s} {h3_mark:4s} "
                f"{mode_mark:8s} {str(r['top1_id'] or 'None'):25s} "
                f"{r['top1_sim']:6.3f} {r['margin']:7.3f} {r['latency_ms']:6.1f}"
            )

        # Aggregate metrics
        n = len(results)
        hit_at_1 = sum(r["hit_at_1"] for r in results) / n
        hit_at_3 = sum(r["hit_at_3"] for r in results) / n
        mode_accuracy = sum(r["mode_correct"] for r in results) / n
        hard_instantiate_rate = sum(
            r["actual_action"] == "hard_instantiate" for r in results
        ) / n
        avg_latency = sum(r["latency_ms"] for r in results) / n
        latency_pcts = _compute_latency_percentiles(results)
        cat_stats = _compute_category_stats(results)
        failures = _identify_failure_cases(results)

        print(f"\n{'='*70}")
        print("AGGREGATE METRICS")
        print(f"{'='*70}")
        print(f"  hit@1:              {hit_at_1:.3f}  ({hit_at_1*100:.1f}%)")
        print(f"  hit@3:              {hit_at_3:.3f}  ({hit_at_3*100:.1f}%)")
        print(f"  mode_accuracy:      {mode_accuracy:.3f}  ({mode_accuracy*100:.1f}%)")
        print(f"  hard_instantiate_rate: {hard_instantiate_rate:.3f}  ({hard_instantiate_rate*100:.1f}%)")
        print(f"  latency p50:        {latency_pcts['p50_ms']:.1f} ms")
        print(f"  latency p95:        {latency_pcts['p95_ms']:.1f} ms")

        print(f"\n{'='*70}")
        print("PER-CATEGORY BREAKDOWN")
        print(f"{'='*70}")
        print(f"{'Category':25s} {'N':4s} {'hit@1':7s} {'hit@3':7s} {'mode%':7s} {'avg_sim':7s}")
        print("-" * 62)
        for cat, stats in cat_stats.items():
            print(
                f"{cat:25s} {stats['n']:4d} {stats['hit_at_1']:7.3f} "
                f"{stats['hit_at_3']:7.3f} {stats['mode_correct']:7.3f} "
                f"{stats['avg_top1_sim']:7.3f}"
            )

        print(f"\n{'='*70}")
        print(f"TOP FAILURE CASES (hit@1=False, sorted by severity)")
        print(f"{'='*70}")
        for r in failures[:5]:
            gt_str = str(r["ground_truth"])
            print(
                f"  [{r['id']}] {r['category']:20s} "
                f"expected:{gt_str:30s} got:{str(r['top1_id']):25s} "
                f"sim:{r['top1_sim']:.3f}"
            )
            if r["top3"]:
                top3_str = ", ".join(f"{t['task_id']}({t['similarity']:.2f})" for t in r["top3"])
                print(f"         top3: {top3_str}")

        # Build output JSON
        output = {
            "benchmark": "retrieval_30prompts",
            "date": "2026-05-15",
            "n_prompts": n,
            "n_templates_indexed": n_indexed,
            "thresholds": {
                "sim_min": _CANONICAL_MIN_SIM,
                "margin_min": _CANONICAL_MIN_MARGIN,
            },
            "aggregate": {
                "hit_at_1": round(hit_at_1, 4),
                "hit_at_3": round(hit_at_3, 4),
                "mode_accuracy": round(mode_accuracy, 4),
                "hard_instantiate_rate": round(hard_instantiate_rate, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "latency_p50_ms": latency_pcts["p50_ms"],
                "latency_p95_ms": latency_pcts["p95_ms"],
            },
            "category_breakdown": cat_stats,
            "failure_cases": [
                {
                    "id": r["id"],
                    "category": r["category"],
                    "prompt": r["prompt"],
                    "ground_truth": r["ground_truth"],
                    "top1_id": r["top1_id"],
                    "top1_sim": r["top1_sim"],
                    "top3": r["top3"],
                }
                for r in failures
            ],
            "per_prompt": results,
        }

        # Write to disk
        _BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
        with open(_RESULTS_FILE, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\n  Results written to: {_RESULTS_FILE}")
        print(f"\n{'='*70}")

        # The benchmark always passes — it's a measurement, not a gate.
        # We assert only the most basic invariants:
        assert n == 30, "Expected 30 results"
        assert hit_at_1 >= 0.0
        assert hit_at_3 >= hit_at_1

        # Print a final summary line for easy parsing by CI or parent agents
        print(
            f"\nBENCHMARK RESULT: hit@1={hit_at_1:.3f} hit@3={hit_at_3:.3f} "
            f"mode_accuracy={mode_accuracy:.3f} hard_instantiate_rate={hard_instantiate_rate:.3f}"
        )


# ---------------------------------------------------------------------------
# Standalone entrypoint (python tests/test_retrieval_benchmark.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    benchmark = TestRetrievalBenchmark()
    benchmark.test_retrieval_benchmark_runs()
