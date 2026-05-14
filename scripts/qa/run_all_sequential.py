"""Run all (persona, task) pairs sequentially, one at a time.

Each session starts with a fresh Isaac Sim stage. Writes a summary JSONL when done.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.qa.multi_turn_session import run_session, REPO_ROOT


TASK_PERSONA_MAP = [
    ("01_maya", "M-01"),
    ("01_maya", "M-02"),
    ("02_erik", "E-01"),
    ("03_kenji", "K-01"),
    ("04_sarah", "S-01"),
    ("05_priya", "P-01"),
    ("07_thomas", "T-01"),
    ("08_alex", "A-01"),
    ("10_raj", "R-01"),
    ("12_dimitri", "D-01"),
]


def main() -> None:
    runs_dir = REPO_ROOT / "workspace" / "qa_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    campaign_id = time.strftime("campaign_%Y%m%dT%H%M%S")
    summary_path = runs_dir / f"{campaign_id}.jsonl"

    print(f"Campaign: {campaign_id}")
    print(f"Tasks: {len(TASK_PERSONA_MAP)}")
    print(f"Summary: {summary_path}")

    total_cost = 0.0
    for i, (persona, task) in enumerate(TASK_PERSONA_MAP, start=1):
        print(f"\n[{i}/{len(TASK_PERSONA_MAP)}] {persona} × {task}")
        t0 = time.time()
        try:
            res = run_session(persona, task, runs_dir, seed=42)
            cost = res.get("cost", 0.0)
            total_cost += cost
            elapsed = time.time() - t0
            print(f"  done turns={res.get('turns')} cost=${cost:.2f} time={elapsed:.0f}s")
            entry = {
                "persona": persona, "task": task,
                "turns": res.get("turns"), "cost_usd": cost,
                "elapsed_s": elapsed, "transcript": res.get("transcript"),
            }
        except Exception as e:
            entry = {"persona": persona, "task": task, "error": str(e)}
            print(f"  ERROR: {e}")
        with summary_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    print(f"\n=== Campaign done — total persona cost: ${total_cost:.2f} ===")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
