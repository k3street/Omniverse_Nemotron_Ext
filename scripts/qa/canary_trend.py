"""
Log a canary result to workspace/qa_runs/canary_trend.log and show recent trend.

Usage:
    # after running a canary + judge:
    python -m scripts.qa.canary_trend --campaign workspace/qa_runs/campaign_direct_<ts>.jsonl
    python -m scripts.qa.canary_trend --show
"""
from __future__ import annotations
import argparse
import json
import re
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TREND_LOG = REPO_ROOT / "workspace" / "qa_runs" / "canary_trend.log"


def _parse_gt(gt_path: Path):
    ok = 0
    total = 0
    fab_total = 0
    tasks = []
    for line in gt_path.read_text().splitlines():
        r = json.loads(line)
        v = r["verdict"]
        success = v.get("real_success")
        if "parse_error" in v and success is None:
            m = re.search(r'"real_success"\s*:\s*(true|false)', v.get("raw", ""))
            success = m and m.group(1) == "true"
        total += 1
        if success:
            ok += 1
        fab_total += len(v.get("fabricated_claims", []))
        tasks.append(f"{'✓' if success else '✗'}{r['task']}")
    return ok, total, fab_total, tasks


def log_canary(campaign_path: Path, note: str = "") -> None:
    gt = Path(str(campaign_path).replace(".jsonl", "_groundtruth.jsonl"))
    if not gt.exists():
        raise FileNotFoundError(f"No _groundtruth file for {campaign_path}. Run judge first.")
    ok, total, fab, tasks = _parse_gt(gt)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    TREND_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{ts} canary={ok}/{total} fab={fab} tasks={'|'.join(tasks)} note={note or '-'}"
    with TREND_LOG.open("a") as f:
        f.write(line + "\n")
    print(f"logged: {line}")


def show_trend(n: int = 10) -> None:
    if not TREND_LOG.exists():
        print("No trend log yet.")
        return
    lines = TREND_LOG.read_text().splitlines()[-n:]
    if not lines:
        print("No entries.")
        return
    for line in lines:
        print(line)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--campaign", help="Campaign summary JSONL to log")
    p.add_argument("--note", default="", help="Short note for context")
    p.add_argument("--show", action="store_true", help="Show recent trend only")
    p.add_argument("-n", type=int, default=10, help="Number of trend lines to show")
    args = p.parse_args()

    if args.campaign:
        log_canary(Path(args.campaign), note=args.note)
        print()
        print("recent trend:")
        show_trend(n=args.n)
    elif args.show:
        show_trend(n=args.n)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
