"""Stress-test LLM/judge variance on the stochastic-failure canary tasks.

Runs a specified task N times in strict sequence (Kit RPC is single-tenant)
and reports the pass-rate + fabrication-count distribution. Useful when a
task oscillates between ✓ and ✗ across canary runs and we want a quantified
baseline before deciding whether the task is stochastic-hard or really-broken.

Usage:
    python -m scripts.qa.variance_probe --task AD-14 --n 5
    python -m scripts.qa.variance_probe --tasks AD-14,C-01,AD-18 --n 5

Each task is run, judged, and the outcome logged. Final summary:
  - AD-14: 3/5 pass, fab_dist=[0, 0, 3, 0, 2]
  - C-01:  2/5 pass, fab_dist=[0, 0, 0, 0, 0]
  - AD-18: 5/5 pass, fab_dist=[0, 0, 0, 0, 0]

Use the fab distribution to decide:
  - 0/N pass → the task tests something the LLM genuinely can't do; capability-bound
  - fab oscillating but pass ratio >60% → stochastic-but-passable, acceptable
  - fab always >0 but pass flips → judge-variance dominated (improve judge)
  - fab 0 but pass flips → criterion-variance dominated (tighten success criterion)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_single(task_id: str) -> dict:
    """Run one direct_eval + judge cycle for a task. Returns summary dict."""
    # direct_eval
    de = subprocess.run(
        [sys.executable, "-m", "scripts.qa.direct_eval", "--tasks", task_id],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if de.returncode != 0:
        return {"error": f"direct_eval failed: {de.stderr[:200]}"}

    # Find the freshest campaign file
    runs = sorted(
        (REPO_ROOT / "workspace" / "qa_runs").glob("campaign_direct_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not runs:
        return {"error": "no campaign file found"}
    campaign = runs[0]

    # judge
    j = subprocess.run(
        [sys.executable, "-m", "scripts.qa.ground_truth_judge", "--campaign", str(campaign)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if j.returncode != 0:
        return {"error": f"judge failed: {j.stderr[:200]}"}

    # parse groundtruth JSONL
    gt = campaign.with_name(campaign.stem + "_groundtruth.jsonl")
    if not gt.exists():
        return {"error": "groundtruth file missing"}
    for line in gt.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        v = d.get("verdict", {})
        return {
            "pass": bool(v.get("real_success")),
            "fab_count": len(v.get("fabricated_claims") or []),
            "miss_count": len(v.get("criteria_misses") or []),
        }
    return {"error": "empty groundtruth file"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", help="single task id")
    p.add_argument("--tasks", help="comma-separated task ids")
    p.add_argument("--n", type=int, default=5, help="number of repetitions per task")
    args = p.parse_args()

    if args.task:
        task_ids = [args.task]
    elif args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    else:
        p.print_help()
        return

    results: dict[str, list[dict]] = {tid: [] for tid in task_ids}
    for tid in task_ids:
        print(f"=== variance probe: {tid} (N={args.n}) ===", flush=True)
        for i in range(args.n):
            t0 = time.time()
            r = _run_single(tid)
            elapsed = time.time() - t0
            results[tid].append(r)
            tag = "✓" if r.get("pass") else "✗" if "pass" in r else "ERR"
            fab = r.get("fab_count", "-")
            miss = r.get("miss_count", "-")
            print(f"  [{i+1}/{args.n}] {tag} fab={fab} miss={miss} ({elapsed:.0f}s)", flush=True)

    print()
    print("=== summary ===")
    for tid, runs in results.items():
        passes = sum(1 for r in runs if r.get("pass"))
        fab_dist = [r.get("fab_count", -1) for r in runs]
        miss_dist = [r.get("miss_count", -1) for r in runs]
        print(f"  {tid}: {passes}/{len(runs)} pass  fab={fab_dist}  miss={miss_dist}")


if __name__ == "__main__":
    main()
