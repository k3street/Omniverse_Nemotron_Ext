"""Benchmark pick-place controllers serially against the conveyor scenario.

Runs `run_conveyor_pick_place` for each (controller, run_index) pair,
captures BENCHMARK_JSON output, aggregates mean/std per controller, and
writes a JSON report.

Serial execution is mandatory — Kit RPC is single-tenant, parallel
runs race on stage state (memory: feedback_isaac_assist_kit_concurrency).

Usage:
    python -m scripts.qa.benchmark_controllers \\
        --controllers native,spline \\
        --n-runs 3 \\
        --out /tmp/bench_$(date +%Y%m%d_%H%M).json

Output structure:
    {
      "runs": [{"controller": "native", "run": 0, "n_in_bin": 1, ...}, ...],
      "per_controller": {
        "native": {"cubes_mean": 1.0, "cubes_std": 0.0, "runs": 3, ...},
        "spline": {"cubes_mean": 3.0, "cubes_std": 0.0, "runs": 3, ...}
      },
      "winner": {"controller": "spline", "reason": "cubes_mean highest"}
    }
"""
from __future__ import annotations
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def run_one(controller: str, wait: int) -> dict:
    """Invoke run_conveyor_pick_place as subprocess, parse BENCHMARK_JSON."""
    cmd = [
        sys.executable, "-m", "scripts.qa.run_conveyor_pick_place",
        "--controller", controller,
        "--wait", str(wait),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=str(REPO),
        capture_output=True, text=True,
        timeout=wait + 180,
    )
    elapsed = time.monotonic() - t0
    out = proc.stdout or ""
    # Find last BENCHMARK_JSON line
    bm_lines = [ln for ln in out.split("\n") if ln.startswith("BENCHMARK_JSON:")]
    if bm_lines:
        data = json.loads(bm_lines[-1][len("BENCHMARK_JSON:"):])
    else:
        data = {"n_in_bin": 0, "_parse_failed": True}
    data["_wall_elapsed_s"] = round(elapsed, 2)
    data["_exit_code"] = proc.returncode
    return data


def aggregate(runs: list[dict]) -> dict:
    if not runs: return {"runs_n": 0}
    n_bins = [int(r.get("n_in_bin", 0)) for r in runs]
    ticks = [int(r.get("tick_count") or 0) for r in runs]
    errs = [int(r.get("error_count") or 0) for r in runs]
    delivered = [int(r.get("cubes_delivered") or 0) for r in runs]
    wall = [float(r.get("_wall_elapsed_s") or 0.0) for r in runs]
    return {
        "runs_n": len(runs),
        "cubes_in_bin_mean": round(statistics.mean(n_bins), 3),
        "cubes_in_bin_std": round(statistics.pstdev(n_bins), 3) if len(n_bins) > 1 else 0.0,
        "cubes_in_bin_min": min(n_bins),
        "cubes_in_bin_max": max(n_bins),
        "internal_delivered_mean": round(statistics.mean(delivered), 3),
        "error_count_mean": round(statistics.mean(errs), 3),
        "tick_count_mean": round(statistics.mean(ticks), 1),
        "wall_elapsed_mean_s": round(statistics.mean(wall), 1),
    }


def pick_winner(per_controller: dict) -> dict:
    if not per_controller:
        return {"controller": None, "reason": "no results"}
    # Rank by cubes_in_bin_mean (higher=better), break ties by
    # fewer errors → lower wall time.
    ranked = sorted(
        per_controller.items(),
        key=lambda kv: (
            -kv[1].get("cubes_in_bin_mean", 0),
             kv[1].get("error_count_mean", 999),
             kv[1].get("wall_elapsed_mean_s", 9e9),
        ),
    )
    winner_name, winner_data = ranked[0]
    return {
        "controller": winner_name,
        "reason": f"cubes_in_bin_mean={winner_data['cubes_in_bin_mean']} "
                  f"(max {winner_data['cubes_in_bin_max']}, "
                  f"std {winner_data['cubes_in_bin_std']})",
        "ranking": [{"controller": n, **d} for n, d in ranked],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--controllers", default="native,spline",
                    help="CSV list of controllers to benchmark")
    ap.add_argument("--n-runs", type=int, default=3,
                    help="Runs per controller")
    ap.add_argument("--wait", type=int, default=120,
                    help="Seconds to wait after install per run")
    ap.add_argument("--out", type=str, default="/tmp/bench_controllers.json")
    args = ap.parse_args()

    controllers = [c.strip() for c in args.controllers.split(",") if c.strip()]
    all_runs = []
    per_controller = {}

    for ctrl in controllers:
        print(f"\n═══ Controller: {ctrl} ═══")
        runs = []
        for i in range(args.n_runs):
            print(f"  Run {i+1}/{args.n_runs} …", flush=True)
            try:
                data = run_one(ctrl, args.wait)
            except subprocess.TimeoutExpired:
                print("    TIMEOUT")
                data = {"n_in_bin": 0, "_timeout": True}
            data["controller"] = ctrl
            data["run"] = i
            runs.append(data)
            all_runs.append(data)
            print(f"    → n_in_bin={data.get('n_in_bin', 0)} "
                  f"delivered={data.get('cubes_delivered')} "
                  f"errors={data.get('error_count')} "
                  f"wall={data.get('_wall_elapsed_s'):.1f}s")
        per_controller[ctrl] = aggregate(runs)
        print(f"  Aggregate: {per_controller[ctrl]}")

    winner = pick_winner(per_controller)
    report = {
        "controllers": controllers,
        "n_runs": args.n_runs,
        "wait_s": args.wait,
        "runs": all_runs,
        "per_controller": per_controller,
        "winner": winner,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(f"\n═══ Benchmark complete ═══")
    print(f"Winner: {winner['controller']} — {winner['reason']}")
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
