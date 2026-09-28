#!/usr/bin/env python3
"""Measure the RoboLab planner's pass rate against a frozen acceptance file.

One success is an anecdote. This runs a fixed set of seeded scene variations,
one Isaac process each, grades every episode from its trace with
planner_eval_gate (not the runner's own PASS), and reports the pass rate with a
95% interval alongside model calls and spend.

Two splits: `development` for iterating on the planner, and `final`, which is
refused until the acceptance file is frozen and the working tree is clean, and
is recorded in a ledger so that reusing its seeds is visible in every report.

    scripts/evaluate_planner.py plan   config/planner_eval/blocks_in_bin_astra_v1.json --split development
    scripts/evaluate_planner.py run    config/planner_eval/blocks_in_bin_astra_v1.json --split development --output runs/eval-dev
    scripts/evaluate_planner.py score  runs/eval-dev
    scripts/evaluate_planner.py freeze config/planner_eval/blocks_in_bin_astra_v1.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from planner_eval_gate import (  # noqa: E402
    acceptance_digest,
    check_episode,
    is_frozen,
    wilson_interval,
)
from run_gemini_groot_campaign import plan_variations  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "launch_gemini_robotics_robolab.sh"
STALE_KIT_LOCK = Path("/dev/shm/sem.carbonite-sharedmemory")


def load_acceptance(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def episodes_for(acceptance: dict[str, Any], split: str) -> list[dict[str, Any]]:
    spec = acceptance["splits"][split]
    variation = acceptance["variation"]
    launch = acceptance["launch"]
    light_min, light_max = variation["light_intensity"]
    return plan_variations(
        spec["episodes"],
        seed=spec["seed"],
        object_xy=variation["object_xy_m"],
        plate_xy=variation["receptacle_xy_m"],
        yaw_degrees=variation["object_yaw_deg"],
        light_min=light_min,
        light_max=light_max,
        movable_object_assets=(launch["movable_object_asset"],),
        target_receptacle_assets=(launch["target_receptacle_asset"],),
    )


def command_for(
    acceptance: dict[str, Any],
    episode: dict[str, Any],
    artifact_dir: Path,
    overrides: dict[str, str] | None = None,
) -> list[str]:
    launch = {**acceptance["launch"], **(overrides or {})}
    command = [
        str(LAUNCHER),
        "--viz", "none",
        "--provider", launch["provider"],
        "--model", launch["model"],
        "--budget-usd", str(launch["per_episode_budget_usd"]),
        "--task", launch["task"],
        "--movable-object-asset", launch["movable_object_asset"],
        "--target-receptacle-asset", launch["target_receptacle_asset"],
        "--movable-object-offset", *(f"{v:.8f}" for v in episode["movable_object_offset_xy_m"]),
        "--plate-offset", *(f"{v:.8f}" for v in episode["plate_offset_xy_m"]),
        "--movable-object-yaw-deg", f"{episode['movable_object_yaw_deg']:.8f}",
        "--light-intensity", f"{episode['sphere_light_intensity']:.8f}",
        "--appearance-seed", str(episode["appearance_seed"]),
        "--artifact-dir", str(artifact_dir),
    ]
    if acceptance["variation"]["randomize_background"]:
        command.append("--randomize-background")
    return command


def git_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def ledger_path(config: Path) -> Path:
    return config.with_suffix(".final_runs.jsonl")


def score_run(output: Path) -> dict[str, Any]:
    meta = json.loads((output / "run.json").read_text())
    acceptance = meta["acceptance"]
    episodes = []
    for episode in meta["episodes"]:
        directory = output / f"episode_{episode['attempt']:03d}"
        trace_path = directory / "sequence_trace.json"
        log_path = directory / "run.log"
        if not log_path.exists():
            continue  # not attempted yet
        trace = json.loads(trace_path.read_text()) if trace_path.exists() else None
        passed, details = check_episode(trace, log_path.read_text(errors="replace"), acceptance)
        result_path = directory / "result.json"
        extra = json.loads(result_path.read_text()) if result_path.exists() else {}
        episodes.append({**episode, **extra, "passed": passed, **details})
    passes = sum(e["passed"] for e in episodes)
    low, high = wilson_interval(passes, len(episodes))
    costs = [e["cost_usd"] for e in episodes if e.get("cost_usd") is not None]
    calls = [e["model_calls"] for e in episodes if e.get("model_calls") is not None]
    failures: dict[str, int] = {}
    for e in episodes:
        for name in e.get("failed", []):
            failures[name] = failures.get(name, 0) + 1
    summary = {
        "name": acceptance["name"],
        "split": meta["split"],
        "provider": meta.get("overrides", {}).get("provider", acceptance["launch"]["provider"]),
        "model": meta.get("overrides", {}).get("model", acceptance["launch"]["model"]),
        "acceptance_frozen": is_frozen(acceptance),
        "planner_commit": meta["git"],
        "final_seeds_previously_used": meta.get("final_seeds_previously_used", False),
        "episodes_run": len(episodes),
        "episodes_planned": len(meta["episodes"]),
        "passes": passes,
        "pass_rate": passes / len(episodes) if episodes else None,
        "pass_rate_95ci": [low, high],
        "failure_counts": failures,
        "cost_usd_total": sum(costs),
        "cost_usd_per_episode_mean": sum(costs) / len(costs) if costs else None,
        "model_calls_mean": sum(calls) / len(calls) if calls else None,
    }
    if meta["split"] == "final":
        target = acceptance["final_target"]
        summary["final_target"] = target
        summary["meets_final_target"] = (
            len(episodes) == target["episodes"] and passes >= target["min_passes"]
        )
    (output / "score.json").write_text(json.dumps({"summary": summary, "episodes": episodes}, indent=1) + "\n")
    return summary


def cmd_plan(args: argparse.Namespace) -> int:
    acceptance = load_acceptance(args.config)
    for episode in episodes_for(acceptance, args.split):
        print(" ".join(command_for(acceptance, episode, Path(f"<output>/episode_{episode['attempt']:03d}"))))
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    acceptance = load_acceptance(args.config)
    if is_frozen(acceptance):
        print(f"already frozen at {acceptance['frozen_utc']}")
        return 0
    acceptance["frozen_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    acceptance["frozen_sha256"] = acceptance_digest(acceptance)
    args.config.write_text(json.dumps(acceptance, indent=2) + "\n")
    print(f"frozen {acceptance['name']} at {acceptance['frozen_utc']} sha256={acceptance['frozen_sha256']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    acceptance = load_acceptance(args.config)
    git = git_state()
    repeated = False
    overrides = {k: v for k, v in (("provider", args.provider), ("model", args.model)) if v}
    if overrides and args.split == "final":
        sys.exit("final split refused: it measures the model the acceptance file names, not an override")
    if args.limit is not None and args.split == "final":
        sys.exit("final split refused: a partial final run would read as a pass rate over fewer seeds")
    if args.split == "final":
        if not is_frozen(acceptance):
            sys.exit("final split refused: acceptance file is not frozen, or was edited after freezing")
        if git["dirty"]:
            sys.exit("final split refused: commit the planner first, so the result names what was measured")
        ledger = ledger_path(args.config)
        previous = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        if previous and not args.allow_repeat_final:
            sys.exit(
                f"final split refused: its seeds were already used ({len(previous)} run(s) in {ledger}). "
                "Pass --allow-repeat-final to rerun; the report will say so."
            )
        repeated = bool(previous)
        with ledger.open("a") as handle:
            handle.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), "commit": git["commit"],
                                     "output": str(args.output)}) + "\n")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_meta_path = output / "run.json"
    episodes = episodes_for(acceptance, args.split)[: args.limit]
    if run_meta_path.exists():
        previous_meta = json.loads(run_meta_path.read_text())
        if (
            previous_meta["acceptance"] != acceptance
            or previous_meta["split"] != args.split
            or previous_meta.get("overrides", {}) != overrides
        ):
            sys.exit(f"{output} holds a run with a different acceptance file, split or model; use a new --output")
    else:
        run_meta_path.write_text(json.dumps({
            "split": args.split, "acceptance": acceptance, "overrides": overrides, "git": git,
            "final_seeds_previously_used": repeated, "episodes": episodes,
            "started_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=1) + "\n")

    environment = os.environ.copy()
    environment["ROBOT_SEQUENCE_CRITIC"] = "0"  # lessons must not carry between episodes
    spent = 0.0
    for episode in episodes:
        directory = output / f"episode_{episode['attempt']:03d}"
        if (directory / "result.json").exists():
            continue  # resume: finished episodes are kept, not rerun
        if spent >= args.max_total_usd:
            print(f"stopping: ${spent:.2f} spent of the ${args.max_total_usd:.2f} run cap")
            break
        if STALE_KIT_LOCK.exists() and not subprocess.run(
            ["pgrep", "-f", "kit/python/bin/python3"], capture_output=True
        ).stdout:
            sys.exit(f"{STALE_KIT_LOCK} is held with no Kit process alive; remove it or every launch will hang")
        directory.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with (directory / "run.log").open("w") as log:
            try:
                completed = subprocess.run(
                    command_for(acceptance, episode, directory, overrides), cwd=REPO_ROOT, env=environment,
                    stdout=log, stderr=subprocess.STDOUT, timeout=acceptance["launch"]["episode_timeout_s"],
                )
                returncode: int | None = completed.returncode
            except subprocess.TimeoutExpired:
                returncode = None
        (directory / "result.json").write_text(json.dumps(
            {"returncode": returncode, "timed_out": returncode is None, "wall_s": time.time() - started}) + "\n")
        summary = score_run(output)
        spent = summary["cost_usd_total"]
        print(f"episode {episode['attempt']}: {summary['passes']}/{summary['episodes_run']} passed, ${spent:.2f} spent",
              flush=True)
    summary = score_run(output)
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("config", type=Path)
    plan.add_argument("--split", choices=("development", "final"), required=True)
    run = sub.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("--split", choices=("development", "final"), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-total-usd", type=float, default=100.0)
    run.add_argument("--allow-repeat-final", action="store_true")
    run.add_argument("--provider", help="development only: screen another provider")
    run.add_argument("--model", help="development only: screen another model")
    run.add_argument("--limit", type=int, help="development only: run just the first N episodes")
    score = sub.add_parser("score")
    score.add_argument("output", type=Path)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("config", type=Path)
    args = parser.parse_args()
    if args.command == "score":
        print(json.dumps(score_run(args.output), indent=1))
        return 0
    return {"plan": cmd_plan, "run": cmd_run, "freeze": cmd_freeze}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
