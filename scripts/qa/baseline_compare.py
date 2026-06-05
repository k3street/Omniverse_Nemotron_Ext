"""baseline_compare.py — diff a fresh multi-run-regression run against a frozen baseline.

Reads two JSON files in the same shape produced by multi_run_regression.py.
Reports per-canonical regressions and improvements with severity:

  REGRESSED_HARD   stable_ok    → stable_fail     (functional break)
  REGRESSED_DROP   stable_ok    → flaky           (success rate fell)
  REGRESSED_DROP   flaky        → stable_fail     (success rate fell)
  REGRESSED_RATE   any          → success_rate ↓ ≥ THRESHOLD (default 0.20)
  IMPROVED_HARD    stable_fail  → stable_ok       (functional unlock)
  IMPROVED_RISE    stable_fail  → flaky           (rate ↑)
  IMPROVED_RISE    flaky        → stable_ok       (rate ↑)
  IMPROVED_RATE    any          → success_rate ↑ ≥ THRESHOLD
  UNCHANGED        same status, same rate
  NEW              CP appears only in current
  REMOVED          CP appears only in baseline

Exit codes:
  0  no regressions worse than threshold
  1  regressions found
  2  argument error / unreadable file

Usage:
  python scripts/qa/baseline_compare.py \
      --baseline workspace/baselines/2026-05-09-baseline.json \
      --current  workspace/baselines/2026-05-12-baseline.json \
      [--threshold 0.20]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def _load(path: str) -> Dict:
    p = Path(path)
    if not p.exists():
        print(f"[FAIL] missing file: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"[FAIL] cannot parse {path}: {e}", file=sys.stderr)
        sys.exit(2)


def _index(payload: Dict) -> Dict[str, Dict]:
    return {r["label"]: r for r in payload.get("results", []) if r.get("label")}


_STATUS_RANK = {"stable_fail": 0, "flaky": 1, "stable_ok": 2}


def _classify(b_status: str, c_status: str, b_rate: float, c_rate: float, threshold: float) -> Tuple[str, str]:
    """Return (severity, label) for a single CP.
    severity ∈ {REGRESSED_HARD, REGRESSED_DROP, REGRESSED_RATE,
                IMPROVED_HARD, IMPROVED_RISE, IMPROVED_RATE, UNCHANGED}.
    """
    b_rank = _STATUS_RANK.get(b_status, -1)
    c_rank = _STATUS_RANK.get(c_status, -1)

    # Hard regressions / improvements first
    if b_status == "stable_ok" and c_status == "stable_fail":
        return "REGRESSED_HARD", "stable_ok → stable_fail"
    if b_status == "stable_fail" and c_status == "stable_ok":
        return "IMPROVED_HARD", "stable_fail → stable_ok"

    # Bucket changes
    if c_rank < b_rank:
        return "REGRESSED_DROP", f"{b_status} → {c_status}"
    if c_rank > b_rank:
        return "IMPROVED_RISE", f"{b_status} → {c_status}"

    # Same bucket — check rate change
    delta = c_rate - b_rate
    if delta <= -threshold:
        return "REGRESSED_RATE", f"rate {b_rate:.2f} → {c_rate:.2f} (Δ={delta:+.2f})"
    if delta >= threshold:
        return "IMPROVED_RATE", f"rate {b_rate:.2f} → {c_rate:.2f} (Δ={delta:+.2f})"
    return "UNCHANGED", f"{c_status} ({c_rate:.2f})"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True, help="Frozen baseline JSON.")
    p.add_argument("--current", required=True, help="Current run JSON to compare.")
    p.add_argument("--threshold", type=float, default=0.20,
                   help="Rate-delta threshold for REGRESSED_RATE / IMPROVED_RATE (default 0.20).")
    p.add_argument("--quiet", action="store_true", help="Skip UNCHANGED rows.")
    p.add_argument("--json", action="store_true", help="Emit JSON summary instead of human-readable.")
    args = p.parse_args()

    baseline = _load(args.baseline)
    current = _load(args.current)

    b_idx = _index(baseline)
    c_idx = _index(current)

    all_labels = sorted(set(b_idx) | set(c_idx))
    rows: List[Dict] = []

    for label in all_labels:
        b = b_idx.get(label)
        c = c_idx.get(label)
        if b is None and c is not None:
            rows.append({
                "label": label, "severity": "NEW", "detail": f"new in current ({c.get('status')})",
                "baseline_status": None, "current_status": c.get("status"),
                "baseline_rate": None, "current_rate": c.get("success_rate"),
            })
            continue
        if c is None and b is not None:
            rows.append({
                "label": label, "severity": "REMOVED", "detail": f"only in baseline ({b.get('status')})",
                "baseline_status": b.get("status"), "current_status": None,
                "baseline_rate": b.get("success_rate"), "current_rate": None,
            })
            continue
        b_status = b.get("status") or "?"
        c_status = c.get("status") or "?"
        b_rate = b.get("success_rate") or 0.0
        c_rate = c.get("success_rate") or 0.0
        severity, detail = _classify(b_status, c_status, b_rate, c_rate, args.threshold)
        rows.append({
            "label": label, "severity": severity, "detail": detail,
            "baseline_status": b_status, "current_status": c_status,
            "baseline_rate": b_rate, "current_rate": c_rate,
        })

    regressions = [r for r in rows if r["severity"].startswith("REGRESSED")]
    improvements = [r for r in rows if r["severity"].startswith("IMPROVED")]
    unchanged = [r for r in rows if r["severity"] == "UNCHANGED"]
    new_cps = [r for r in rows if r["severity"] == "NEW"]
    removed = [r for r in rows if r["severity"] == "REMOVED"]

    if args.json:
        print(json.dumps({
            "baseline_path": args.baseline,
            "current_path": args.current,
            "threshold": args.threshold,
            "summary": {
                "regressions": len(regressions),
                "improvements": len(improvements),
                "unchanged": len(unchanged),
                "new": len(new_cps),
                "removed": len(removed),
            },
            "rows": rows,
        }, indent=2))
    else:
        # Human-readable
        print(f"baseline: {args.baseline}")
        print(f"current:  {args.current}")
        print(f"threshold: Δrate ≥ {args.threshold:.2f} flagged as REGRESSED_RATE/IMPROVED_RATE")
        print("-" * 78)

        for r in rows:
            if args.quiet and r["severity"] == "UNCHANGED":
                continue
            sev = r["severity"]
            tag = "❌" if sev.startswith("REGRESSED") else (
                  "✅" if sev.startswith("IMPROVED") else (
                  "·" if sev == "UNCHANGED" else "+"))
            print(f"  {tag}  {r['label']:8s} {sev:18s}  {r['detail']}")

        print("-" * 78)
        print(f"summary: regressions={len(regressions)}  improvements={len(improvements)}  "
              f"unchanged={len(unchanged)}  new={len(new_cps)}  removed={len(removed)}")
        if regressions:
            print(f"  HARD={sum(1 for r in regressions if r['severity']=='REGRESSED_HARD')}  "
                  f"DROP={sum(1 for r in regressions if r['severity']=='REGRESSED_DROP')}  "
                  f"RATE={sum(1 for r in regressions if r['severity']=='REGRESSED_RATE')}")

    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
