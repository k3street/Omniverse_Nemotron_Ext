"""
Aggregate tool-call failures across all graded campaigns.

Usage:
    python -m scripts.qa.aggregate_failures
    python -m scripts.qa.aggregate_failures --top 20 --since 2026-04-18
    python -m scripts.qa.aggregate_failures --tool anchor_robot

Reads all workspace/qa_runs/campaign_*_groundtruth.jsonl + referenced
transcripts, groups tool errors by tool name, prints the top-N most-failed
tools with their dominant error signatures. Use to pick next target for
tool-audit work (see AUTONOMOUS_PLAN.md §7).
"""
from __future__ import annotations
import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[2]


def aggregate(since: Optional[str] = None, tool_filter: Optional[str] = None):
    tool_stats = defaultdict(lambda: {"calls": 0, "fails": 0, "errors": Counter(),
                                      "campaigns": set()})
    for gt in sorted(glob.glob(str(REPO_ROOT / "workspace/qa_runs/campaign_*_groundtruth.jsonl"))):
        if since and since not in gt:
            # Best-effort date filter: files are timestamped in filename
            # campaign_direct_20260418T001234_groundtruth.jsonl
            ts_parts = [p for p in Path(gt).stem.split("_") if p.startswith("2026")]
            if ts_parts and ts_parts[0][:8] < since.replace("-", ""):
                continue
        for line in Path(gt).read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            tr = Path(r.get("transcript", ""))
            if not tr.exists():
                continue
            for tline in tr.read_text().splitlines():
                try:
                    d = json.loads(tline)
                except json.JSONDecodeError:
                    continue
                if d.get("event") != "isaac_assist_reply":
                    continue
                for tc in d.get("tool_calls", []):
                    name = tc.get("tool", "?")
                    if tool_filter and name != tool_filter:
                        continue
                    result = tc.get("result", {})
                    tool_stats[name]["calls"] += 1
                    tool_stats[name]["campaigns"].add(Path(gt).stem)
                    if result.get("success") is False or result.get("executed") is False:
                        tool_stats[name]["fails"] += 1
                        err = str(result.get("output") or result.get("error") or "")[:150]
                        tool_stats[name]["errors"][err] += 1
    return tool_stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=15, help="Top-N failed tools to show")
    p.add_argument("--since", default=None, help="Only campaigns after this date (YYYY-MM-DD)")
    p.add_argument("--tool", default=None, help="Only this tool name")
    args = p.parse_args()

    stats = aggregate(since=args.since, tool_filter=args.tool)
    if not stats:
        print("No data.")
        return

    ranked = sorted(stats.items(), key=lambda x: -x[1]["fails"])
    if args.tool:
        ranked = [r for r in ranked if r[0] == args.tool]

    print(f"{'tool':40s} {'calls':>6s} {'fails':>6s}  top error")
    print("-" * 100)
    for name, s in ranked[: args.top]:
        if s["fails"] == 0 and not args.tool:
            break
        top_err = s["errors"].most_common(1)[0][0] if s["errors"] else "(no errors)"
        print(f"{name:40s} {s['calls']:>6d} {s['fails']:>6d}  {top_err[:80]}")

    if args.tool and ranked:
        name, s = ranked[0]
        print(f"\nAll distinct error signatures for {name} ({len(s['errors'])}):")
        for err, count in s["errors"].most_common():
            print(f"  [{count}]  {err}")


if __name__ == "__main__":
    main()
