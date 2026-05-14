"""
function_gate_consistency.py — run function_gate_suite N times and
report per-canonical pass-rate with Wilson confidence intervals.

Helps separate "deterministic delivery" from "stochastic delivery
that happens to pass once". Especially useful for tail behaviors
(controller takes near max sim time, motion-planning seed variance,
etc).

Usage:
  python scripts/qa/function_gate_consistency.py --runs 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "qa"))

import _stats  # type: ignore  # noqa: E402
from function_gate_suite import SUITE, run_one  # type: ignore  # noqa: E402
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=5,
                   help="Number of repetitions (default 5)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001")
        return 2

    # Per-canonical accumulator: {label: [success_bool, ...]}
    results: Dict[str, List[bool]] = {label: [] for label, *_ in SUITE}

    for run_idx in range(1, args.runs + 1):
        print(f"\n=== run {run_idx}/{args.runs} ===")
        for label, tpath, cube, target, dur, extra in SUITE:
            try:
                r = await run_one(label, tpath, cube, target,
                                  duration_s=dur, extra=extra)
                ok = bool(r.get("success"))
            except Exception as e:
                print(f"  {label}: EXCEPTION: {str(e)[:80]}")
                ok = False
            results[label].append(ok)
            print(f"  {label}: {'PASS' if ok else 'FAIL'} "
                  f"({sum(results[label])}/{len(results[label])} so far)")

    print()
    print(f"=== summary across {args.runs} runs ===")
    print(f"{'canonical':<14} {'passes':<10} {'rate':<8} {'wilson 95% CI':<20}")
    print("-" * 55)
    for label, *_ in SUITE:
        passes = sum(results[label])
        n = len(results[label])
        rate = passes / n if n > 0 else 0
        lo, hi = _stats.wilson(passes, n)
        ci = f"[{int(100*lo)}%, {int(100*hi)}%]"
        print(f"{label:<14} {passes}/{n:<8} {100*rate:>5.0f}%   {ci:<20}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
