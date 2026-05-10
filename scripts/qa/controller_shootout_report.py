"""controller_shootout_report.py — Phase 6 M4 deliverable.

Compares controller modes (curobo / spline / native / builtin / sensor_gated /
diffik / osc / ros2_cmd) across canonicals using multi_run_regression baselines.

Output: docs/research/controller_shootout.md — table of:
  controller × robot_family × stable_ok rate × mean_cycle_time × plan_fail_rate

Data sources:
  - workspace/baselines/*.json (per-run results from multi_run_regression)
  - workspace/templates/CP-*.json (target_source + robot_family per CP)

Usage:
  python scripts/qa/controller_shootout_report.py [--baseline-tag TAG]

If --baseline-tag is given, only baselines with matching tag are aggregated.
Otherwise all baselines are pooled.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINES_DIR = REPO_ROOT / "workspace/baselines"
TEMPLATES_DIR = REPO_ROOT / "workspace/templates"


def _infer_controller(template_path: Path) -> Dict[str, str]:
    """Extract target_source and robot_family from template.code."""
    try:
        d = json.loads(template_path.read_text())
    except Exception:
        return {"target_source": "unknown", "robot_family": "unknown"}
    code = d.get("code", "")
    m_ts = re.search(r'target_source\s*=\s*["\']([\w]+)["\']', code)
    target_source = m_ts.group(1) if m_ts else "curobo"  # default per executor

    m_rf = re.search(r'robot_family\s*=\s*["\']([\w]+)["\']', code)
    if m_rf:
        rf = m_rf.group(1)
    else:
        # Try robot_wizard's robot_name as proxy
        m_rn = re.search(r'robot_name\s*=\s*["\']([\w]+)["\']', code)
        rf = m_rn.group(1) if m_rn else "franka_panda"
    return {"target_source": target_source, "robot_family": rf}


def _load_baselines(tag: Optional[str]) -> List[Dict[str, Any]]:
    rows = []
    if not BASELINES_DIR.exists():
        return rows
    for f in sorted(BASELINES_DIR.glob("*.json")):
        if tag and tag not in f.name:
            continue
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        for r in data.get("results", []):
            r["_baseline_file"] = f.name
            rows.append(r)
    return rows


def _aggregate(baselines: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Group by canonical, then bucket by inferred (controller, family)."""
    by_canonical = defaultdict(list)
    for r in baselines:
        cp = r.get("canonical") or r.get("label")
        if cp:
            by_canonical[cp].append(r)

    by_bucket: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "canonicals": set(),
        "verdicts": [],
        "cycle_times": [],
        "plan_fails": [],
        "n_runs": 0,
    })

    for cp, runs in by_canonical.items():
        tpl = TEMPLATES_DIR / f"{cp}.json"
        info = _infer_controller(tpl)
        bucket_key = f"{info['target_source']}/{info['robot_family']}"
        b = by_bucket[bucket_key]
        b["canonicals"].add(cp)
        for r in runs:
            verdict = r.get("verdict") or r.get("status")
            if verdict in ("stable_ok", "stable_fail", "flaky", "no_result"):
                b["verdicts"].append(verdict)
            ct = r.get("mean_cycle_time")
            if ct is not None: b["cycle_times"].append(float(ct))
            pf = r.get("plan_fail_rate")
            if pf is not None: b["plan_fails"].append(float(pf))
            b["n_runs"] += 1

    return by_bucket


def _render(by_bucket: Dict[str, Dict[str, Any]], output: Path) -> None:
    lines = ["# Controller Shootout Report",
             "",
             "Comparison of controller modes (target_source × robot_family) across",
             "available baseline runs from `workspace/baselines/`.",
             ""]

    if not by_bucket:
        lines.append("**No baselines found.** Run multi_run_regression.py first.")
        output.write_text("\n".join(lines))
        return

    # Sort buckets by count
    sorted_buckets = sorted(by_bucket.items(), key=lambda kv: -kv[1]["n_runs"])

    lines.append("## Summary")
    lines.append("")
    lines.append("| controller / family | n_canonicals | n_runs | stable_ok | stable_fail | flaky | mean cycle (s) | plan_fail rate |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for key, b in sorted_buckets:
        n_cps = len(b["canonicals"])
        n_runs = b["n_runs"]
        verdicts = b["verdicts"]
        ok = verdicts.count("stable_ok")
        fail = verdicts.count("stable_fail")
        flaky = verdicts.count("flaky")
        ct_str = "—"
        if b["cycle_times"]:
            ct_str = f"{mean(b['cycle_times']):.2f}"
            if len(b["cycle_times"]) > 1:
                ct_str += f"±{stdev(b['cycle_times']):.2f}"
        pf_str = f"{mean(b['plan_fails']):.2f}" if b["plan_fails"] else "—"
        lines.append(f"| {key} | {n_cps} | {n_runs} | {ok} | {fail} | {flaky} | {ct_str} | {pf_str} |")

    lines.append("")
    lines.append("## Per-bucket canonical lists")
    lines.append("")
    for key, b in sorted_buckets:
        lines.append(f"### {key} ({len(b['canonicals'])} CPs)")
        for cp in sorted(b["canonicals"]):
            lines.append(f"- {cp}")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- This is a snapshot. As more controllers are exercised across the")
    lines.append("  canonical set, the table fills out and ranking becomes meaningful.")
    lines.append("- For Phase 9 (M4 cuMotion-as-MoveIt), the `ros2_cmd` row will appear")
    lines.append("  once CP-87 (and any successors) run with the cumotion_moveit pipeline.")
    lines.append("- Cycle-time is per-run mean; meaningful only when canonicals deliver")
    lines.append("  cubes. For plumbing-only canonicals (CP-NEW-plc-conveyor etc) cycle")
    lines.append("  time is N/A.")

    output.write_text("\n".join(lines))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline-tag", default=None,
                   help="Only aggregate baselines whose filename contains this tag")
    p.add_argument("--output", default=None,
                   help="Where to write report (default docs/research/controller_shootout.md)")
    args = p.parse_args()

    baselines = _load_baselines(args.baseline_tag)
    by_bucket = _aggregate(baselines)
    out = Path(args.output) if args.output else REPO_ROOT / "docs/research/controller_shootout.md"
    _render(by_bucket, out)
    print(f"Wrote {out.relative_to(REPO_ROOT)}")
    print(f"  {len(baselines)} run records → {len(by_bucket)} (controller/family) buckets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
