"""phase2_triage.py — Master Plan Phase 2 entrypoint.

Cross-products feasibility verdicts (Phase 1, from feasibility_baseline.py)
with function-gate baseline (Phase 0, from multi_run_regression.py) and
emits a per-CP triage classification + action recommendation.

Triage classes (mapped to master plan Phase 2 sub-phases):
  2a  infeasible            → TEMPLATE_FIX     (rewrite scene)
  2b  overconstrained        → TEMPLATE_TUNE   (reposition obstacles, widen sensor)
  2c  tightly_feasible (✗)   → CONTROLLER_TUNE (Phase 4 scenario-profile candidate)
  2d  feasible (✗)           → CONTROLLER_BUG  (real platform bug — Mode B FJ etc)
  --  feasible (✓)           → STABLE_OK       (no action needed)
  --  tightly_feasible (✓)   → MARGINAL_OK     (passes today, watch for drift)

Usage:
  python scripts/qa/phase2_triage.py                                        # default paths
  python scripts/qa/phase2_triage.py \
      --feasibility workspace/baselines/feasibility/_summary.json \
      --function-gate workspace/baselines/2026-05-09-baseline.json \
      --out workspace/baselines/phase2_triage.json

Outputs:
  - workspace/baselines/phase2_triage.json  (per-CP rows with class)
  - stdout summary table grouped by class, sorted by impact
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_feasibility(path: Path) -> Dict[str, str]:
    """Read feasibility/_summary.json or per-CP files; return {cp_id: verdict}."""
    out: Dict[str, str] = {}
    if path.is_file() and path.name == "_summary.json":
        # _summary.json doesn't have per-CP rows; read sibling files
        per_dir = path.parent
        for f in sorted(per_dir.glob("CP-*.json")):
            try:
                d = json.loads(f.read_text())
                v = d.get("verdict")
                if v:
                    out[f.stem] = v
            except Exception:
                continue
    elif path.is_dir():
        for f in sorted(path.glob("CP-*.json")):
            try:
                d = json.loads(f.read_text())
                v = d.get("verdict")
                if v:
                    out[f.stem] = v
            except Exception:
                continue
    elif path.is_file():
        # Maybe a baseline-comparison-shaped file with "results"
        try:
            payload = json.loads(path.read_text())
            for r in payload.get("results", []):
                lbl = r.get("label")
                v = r.get("verdict")
                if lbl and v:
                    out[lbl] = v
        except Exception:
            pass
    return out


def _load_function_gate(path: Path) -> Dict[str, Tuple[str, float]]:
    """Read multi_run_regression baseline; return {cp_id: (status, success_rate)}."""
    out: Dict[str, Tuple[str, float]] = {}
    if not path.exists():
        return out
    payload = json.loads(path.read_text())
    for r in payload.get("results", []):
        lbl = r.get("label")
        st = r.get("status") or r.get("verdict")
        rate = r.get("success_rate") or 0.0
        if lbl and st:
            out[lbl] = (st, float(rate))
    return out


def _classify(feas: Optional[str], fg_status: Optional[str]) -> Tuple[str, str]:
    """Return (class_label, action). Either input may be None."""
    if feas == "infeasible":
        return "2a-TEMPLATE_FIX", "Rewrite scene; unreachable goal or in-collision start"
    if feas == "overconstrained":
        return "2b-TEMPLATE_TUNE", "Reposition obstacles, widen sensor zones, adjust drop_target"

    if fg_status == "stable_ok":
        if feas == "tightly_feasible":
            return "MARGINAL_OK", "Passes today, watch for drift; auto-tune candidate"
        return "STABLE_OK", "No action"

    if fg_status in ("stable_fail", "flaky") or fg_status is None:
        if feas == "tightly_feasible":
            return "2c-CONTROLLER_TUNE", "Auto-tune controller params (Phase 4 scenario-profile)"
        if feas == "feasible":
            return "2d-CONTROLLER_BUG", "Real platform bug; targeted fix (Mode B FJ, drop precision, multi-robot)"
        # No feasibility info but failing
        return "UNKNOWN_NEED_DIAGNOSE", "Run diagnose_scene_feasibility first"

    return "UNCLASSIFIED", "Investigate manually"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--feasibility", default=str(REPO_ROOT / "workspace/baselines/feasibility/_summary.json"))
    p.add_argument("--function-gate", default=str(REPO_ROOT / "workspace/baselines/2026-05-09-baseline.json"))
    p.add_argument("--out", default=str(REPO_ROOT / "workspace/baselines/phase2_triage.json"))
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    feas_map = _load_feasibility(Path(args.feasibility))
    fg_map = _load_function_gate(Path(args.function_gate))

    if not feas_map and not fg_map:
        print("[FAIL] no input data found. Provide --feasibility and --function-gate paths.",
              file=sys.stderr)
        return 2

    all_labels = sorted(set(feas_map) | set(fg_map))

    rows: List[Dict[str, Any]] = []
    for label in all_labels:
        feas = feas_map.get(label)
        fg = fg_map.get(label)
        fg_status, fg_rate = fg if fg else (None, None)
        cls, action = _classify(feas, fg_status)
        rows.append({
            "label": label,
            "feasibility": feas,
            "function_gate_status": fg_status,
            "function_gate_rate": fg_rate,
            "triage_class": cls,
            "action": action,
        })

    # Group + summarize
    by_class: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        by_class[r["triage_class"]].append(r)

    if not args.quiet:
        print(f"phase2_triage — {len(rows)} canonicals")
        print(f"  feasibility verdicts: {sum(1 for r in rows if r['feasibility'])}")
        print(f"  function-gate baseline: {sum(1 for r in rows if r['function_gate_status'])}")
        print("=" * 74)

        # Order classes by master-plan phase priority
        order = ["2a-TEMPLATE_FIX", "2d-CONTROLLER_BUG", "2b-TEMPLATE_TUNE",
                 "2c-CONTROLLER_TUNE", "MARGINAL_OK", "STABLE_OK",
                 "UNKNOWN_NEED_DIAGNOSE", "UNCLASSIFIED"]
        for cls in order:
            bucket = by_class.get(cls, [])
            if not bucket:
                continue
            print(f"\n[{cls}]  {len(bucket)} CP(s)")
            for r in bucket:
                rate_s = f"{r['function_gate_rate']:.2f}" if r["function_gate_rate"] is not None else " - "
                print(f"  {r['label']:7s}  feas={r['feasibility'] or '?':18s}  "
                      f"gate={r['function_gate_status'] or '?':12s}  rate={rate_s}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_canonicals": len(rows),
        "class_distribution": {k: len(v) for k, v in by_class.items()},
        "rows": rows,
    }
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
