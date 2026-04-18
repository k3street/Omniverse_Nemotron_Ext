"""
Aggregate tool-call failures across graded campaigns.

Defaults to the last 24 hours of campaigns (by mtime) so historical runs
and Kit-RPC-disconnect storms don't drown the signal. Infrastructure-level
errors (Kit RPC down, network) are filtered out of the "top error" column
because they reflect harness/env state, not tool bugs.

Usage:
    python -m scripts.qa.aggregate_failures                      # last 24h
    python -m scripts.qa.aggregate_failures --since 2026-04-18   # from date
    python -m scripts.qa.aggregate_failures --hours 6            # last 6h
    python -m scripts.qa.aggregate_failures --all                # no time filter
    python -m scripts.qa.aggregate_failures --tool anchor_robot  # single tool
    python -m scripts.qa.aggregate_failures --include-infra      # keep Kit-down
"""
from __future__ import annotations
import argparse
import glob
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[2]

# Errors caused by harness / Kit-RPC / network state — not tool bugs.
# These pollute rankings because one Kit-RPC death produces N per-tool failures.
_INFRA_ERROR_SIGNATURES = (
    "Cannot connect to host 127.0.0.1",
    "Kit RPC not reachable",
    "ConnectionRefusedError",
    "ServerDisconnectedError",
    "exec_sync timed out",
    "KitRPC unavailable",
    "[Errno 111] Connect call failed",
    "All connection attempts failed",
)

_CAMPAIGN_TS_RE = re.compile(r"(\d{8})T(\d{6})")


def _campaign_epoch(gt_path: Path) -> float:
    """Return an epoch seconds estimate for this campaign.

    Prefer parsing the timestamp embedded in the filename (authoritative for
    when the campaign started). Fall back to file mtime if the pattern
    doesn't match. Both are compared in seconds, so filtering is consistent.
    """
    m = _CAMPAIGN_TS_RE.search(gt_path.name)
    if m:
        try:
            return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
        except ValueError:
            pass
    try:
        return gt_path.stat().st_mtime
    except OSError:
        return 0.0


def _is_infra_error(err_text: str) -> bool:
    return any(sig in err_text for sig in _INFRA_ERROR_SIGNATURES)


def aggregate(
    since_epoch: Optional[float] = None,
    tool_filter: Optional[str] = None,
    include_infra: bool = False,
):
    tool_stats = defaultdict(lambda: {"calls": 0, "fails": 0, "errors": Counter(),
                                      "infra_fails": 0, "campaigns": set()})
    for gt in sorted(glob.glob(str(REPO_ROOT / "workspace/qa_runs/campaign_*_groundtruth.jsonl"))):
        gt_path = Path(gt)
        if since_epoch is not None and _campaign_epoch(gt_path) < since_epoch:
            continue
        try:
            gt_lines = gt_path.read_text().splitlines()
        except OSError:
            continue
        for line in gt_lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            tr = Path(r.get("transcript", ""))
            if not tr.exists():
                continue
            try:
                tr_lines = tr.read_text().splitlines()
            except OSError:
                continue
            for tline in tr_lines:
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
                    tool_stats[name]["campaigns"].add(gt_path.stem)
                    if result.get("success") is False or result.get("executed") is False:
                        err = str(result.get("output") or result.get("error") or "")[:150]
                        if _is_infra_error(err):
                            tool_stats[name]["infra_fails"] += 1
                            if include_infra:
                                tool_stats[name]["fails"] += 1
                                tool_stats[name]["errors"][err] += 1
                        else:
                            tool_stats[name]["fails"] += 1
                            tool_stats[name]["errors"][err] += 1
    return tool_stats


def _resolve_since_epoch(args) -> Optional[float]:
    if args.all:
        return None
    if args.since:
        try:
            return time.mktime(time.strptime(args.since, "%Y-%m-%d"))
        except ValueError:
            raise SystemExit(f"Invalid --since date (expect YYYY-MM-DD): {args.since}")
    # Default: hours back from now
    return time.time() - args.hours * 3600.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=15, help="Top-N failed tools to show")
    p.add_argument("--since", default=None, help="Only campaigns after this date (YYYY-MM-DD)")
    p.add_argument("--hours", type=float, default=24.0,
                   help="Default window: last N hours (ignored if --since or --all set)")
    p.add_argument("--all", action="store_true", help="Include all historical campaigns")
    p.add_argument("--tool", default=None, help="Only this tool name")
    p.add_argument("--include-infra", action="store_true",
                   help="Include Kit-RPC-down / connect errors (default: filter out)")
    args = p.parse_args()

    since_epoch = _resolve_since_epoch(args)
    stats = aggregate(since_epoch=since_epoch, tool_filter=args.tool,
                      include_infra=args.include_infra)
    if not stats:
        print("No data (try --all or a wider --hours window).")
        return

    if since_epoch is not None:
        window = time.strftime("%Y-%m-%d %H:%M", time.localtime(since_epoch))
        print(f"Window: campaigns since {window} ({'including' if args.include_infra else 'excluding'} infra errors)")
        print()

    ranked = sorted(stats.items(), key=lambda x: -x[1]["fails"])
    if args.tool:
        ranked = [r for r in ranked if r[0] == args.tool]

    print(f"{'tool':40s} {'calls':>6s} {'fails':>6s} {'infra':>6s}  top error")
    print("-" * 110)
    for name, s in ranked[: args.top]:
        if s["fails"] == 0 and not args.tool:
            break
        top_err = s["errors"].most_common(1)[0][0] if s["errors"] else "(no errors)"
        print(f"{name:40s} {s['calls']:>6d} {s['fails']:>6d} {s['infra_fails']:>6d}  {top_err[:70]}")

    if args.tool and ranked:
        name, s = ranked[0]
        print(f"\nAll distinct error signatures for {name} ({len(s['errors'])}):")
        for err, count in s["errors"].most_common():
            print(f"  [{count}]  {err}")


if __name__ == "__main__":
    main()
