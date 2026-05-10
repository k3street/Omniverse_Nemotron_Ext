"""sweep_summary.py — summarize a baseline JSON into stable_ok/fail breakdown.

Usage:
  python scripts/qa/sweep_summary.py workspace/baselines/full-n1-overnight-baseline.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: sweep_summary.py <baseline.json>")
        return 2
    path = Path(sys.argv[1])
    d = json.loads(path.read_text())

    by_status = defaultdict(list)
    for r in d.get("results", []):
        s = r.get("status") or r.get("verdict") or "?"
        label = r.get("label", "?")
        by_status[s].append(label)

    total = sum(len(v) for v in by_status.values())
    print(f"=== {path.name} — {total} canonicals ===\n")
    for status in sorted(by_status, key=lambda x: ('ZZZ' if x == 'stable_ok' else x)):
        items = sorted(by_status[status])
        print(f"## {status}: {len(items)}")
        for i in range(0, len(items), 8):
            print("  " + ", ".join(items[i:i+8]))
        print()

    n_stable_ok = len(by_status.get("stable_ok", []))
    n_build_ok = len(by_status.get("BUILD_OK", []))
    n_fail = len(by_status.get("stable_fail", []))
    n_other = total - n_stable_ok - n_build_ok - n_fail

    print("=" * 50)
    print(f"GREEN (stable_ok + BUILD_OK): {n_stable_ok + n_build_ok}/{total}")
    print(f"  stable_ok:   {n_stable_ok}")
    print(f"  BUILD_OK:    {n_build_ok}")
    print(f"  stable_fail: {n_fail}")
    print(f"  other:       {n_other}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
