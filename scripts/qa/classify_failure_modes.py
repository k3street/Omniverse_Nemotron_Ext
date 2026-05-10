"""classify_failure_modes.py — categorize failing CPs into actionable buckets.

Reads docs/research/2026-05-10-failure-modes/*.json (probe_ctrl_telemetry
outputs) and classifies each by failure-mode pattern using:
- plan_calls / plan_fails (cuRobo planning behavior)
- cube_delivered_final, cycles_attempted_final
- last_phase
- cube trajectory deltas
- last_errors

Categories (priority order — earlier = more actionable):

A-PLAN_FAILS_HIGH    plan_calls > 0, plan_fail_rate > 0.5
                     → cuRobo can't reach goal; needs predictive planning
                       or scenario-profile (CP-37 was this)

B-PARTIAL_DELIVERY   cubes_delivered >= 1 but < expected
                     → controller works but loses cubes; investigate
                       gripper / drop precision / multi-cube cycling

C-CUBE_FELL_OFF      any cube z < 0.5 in trajectory
                     → physics issue; cube falls off belt edge before
                       robot can pick. Template-side: belt geometry
                       or sensor zone position.

D-NO_PLAN_NO_PICK    plan_calls = 0, cycles_attempted = 0
                     → controller never engaged; sensor never triggered
                       or filtered out by reach check (could be P1 needed)

E-EVENT_CYCLE        last_phase contains "event=N"
                     → builtin event-state handler stuck cycling

F-BUILD_FAILED       no summary or summary contains "BUILD_FAIL"
                     → template build broken
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
RCA_DIR = REPO_ROOT / "docs/research/2026-05-10-failure-modes"


def _classify(label: str, data: Dict[str, Any]) -> Dict[str, Any]:
    summary = data.get("summary") or {}
    cube_traj = data.get("cube_trajectories") or []

    if not summary:
        return {"label": label, "category": "F-BUILD_FAILED",
                "details": "no_summary",
                "plan_calls": 0, "plan_fails": 0, "plan_fail_rate": None,
                "cubes_delivered": 0, "cycles_attempted": 0, "n_cubes": 0,
                "last_phase": {}, "last_errors": [], "last_fail_goal": "",
                "fallen_off": [], "notes": ["no probe data"]}

    plan_calls = summary.get("plan_calls", 0) or 0
    plan_fails = summary.get("plan_fails", 0) or 0
    fail_rate = (plan_fails / plan_calls) if plan_calls else None
    cubes = summary.get("cubes_delivered_final", 0) or 0
    cycles = summary.get("cycles_attempted_final", 0) or 0
    last_phase = summary.get("last_phase") or {}
    last_errors = summary.get("last_errors") or []
    last_fail_goal = summary.get("last_fail_goal", "")
    cube_paths = summary.get("cube_paths") or []

    # Cube features
    fallen_off = []
    for cp in cube_paths:
        positions = [s.get(cp) for s in cube_traj if s.get(cp) is not None]
        if not positions:
            continue
        if positions[-1][2] < 0.5:
            fallen_off.append(cp.split("/")[-1])

    # Event-cycle in builtin handler
    has_event_phase = any(
        "event=" in str(p) for p in (last_phase.values() if isinstance(last_phase, dict) else [])
    )

    # Categorization (priority order)
    cats = []
    notes = []
    if plan_calls > 0 and fail_rate is not None and fail_rate > 0.5:
        cats.append("A-PLAN_FAILS_HIGH")
        notes.append(f"plan_fail_rate={fail_rate:.2f} ({plan_fails}/{plan_calls})")
    if cubes >= 1 and cubes < len(cube_paths):
        cats.append("B-PARTIAL_DELIVERY")
        notes.append(f"delivered={cubes}/{len(cube_paths)}")
    if fallen_off:
        cats.append(f"C-CUBE_FELL_OFF({len(fallen_off)})")
        notes.append(f"fallen={fallen_off[:3]}")
    if plan_calls == 0 and cycles == 0:
        cats.append("D-NO_PLAN_NO_PICK")
    if has_event_phase:
        cats.append("E-EVENT_CYCLE")

    if not cats:
        cats.append("Z-OTHER")

    return {
        "label": label,
        "category": " | ".join(cats),
        "plan_calls": plan_calls,
        "plan_fails": plan_fails,
        "plan_fail_rate": round(fail_rate, 2) if fail_rate is not None else None,
        "cubes_delivered": cubes,
        "cycles_attempted": cycles,
        "n_cubes": len(cube_paths),
        "last_phase": last_phase,
        "last_errors": last_errors[:2],
        "last_fail_goal": last_fail_goal,
        "fallen_off": fallen_off,
        "notes": notes,
    }


def main() -> int:
    rca_files = sorted(RCA_DIR.glob("CP-*.json"))
    if not rca_files:
        print(f"No probes in {RCA_DIR}", file=sys.stderr)
        return 2

    rows = []
    for f in rca_files:
        text = f.read_text()
        json_start = text.find("{")
        if json_start < 0:
            data = {}
        else:
            try:
                data = json.loads(text[json_start:])
            except Exception:
                data = {}
        rows.append(_classify(f.stem, data))

    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    # Render
    lines = ["# Failure Modes Classification — 2026-05-10", ""]
    lines.append(f"Probed {len(rows)} failing canonicals (post 3D reach + plan_calls counters).")
    lines.append("")
    lines.append("## Distribution")
    for cat, bucket in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- **{cat}**: {len(bucket)} CP(s)")
    lines.append("")

    for cat in sorted(by_cat.keys(), key=lambda c: ('Z' if c.startswith('Z') else c)):
        bucket = by_cat[cat]
        lines.append(f"## {cat} ({len(bucket)})")
        for r in bucket:
            lines.append(f"### {r['label']}")
            lines.append(f"  - plan_calls={r['plan_calls']} plan_fails={r['plan_fails']} "
                         f"rate={r['plan_fail_rate']}")
            lines.append(f"  - delivered={r['cubes_delivered']}/{r['n_cubes']} cycles={r['cycles_attempted']}")
            lines.append(f"  - last_phase={r['last_phase']}")
            if r['last_fail_goal']:
                lines.append(f"  - last_fail_goal={r['last_fail_goal']}")
            for note in r['notes']:
                lines.append(f"  - {note}")
            for e in r['last_errors']:
                lines.append(f"  - error: `{str(e)[:120]}`")
            lines.append("")
    lines.append("")

    # Action recommendations
    lines.append("## Actionable next steps per category")
    lines.append("")
    lines.append("- **A-PLAN_FAILS_HIGH**: predictive planning (project cube_pos by belt_v × plan_horizon). Or per-CP scenario_profile with looser reach margin if 3D reach was too aggressive.")
    lines.append("- **B-PARTIAL_DELIVERY**: investigate gripper-release (Mode B FJ) and drop precision. Often timing-related between segments.")
    lines.append("- **C-CUBE_FELL_OFF**: template-side fix. Belt too short OR sensor too far from robot.")
    lines.append("- **D-NO_PLAN_NO_PICK**: 3D reach check might be too aggressive. Reduce safety margin or implement P1 predictive.")
    lines.append("- **E-EVENT_CYCLE**: builtin handler-specific. Investigate event-state machine for stuck transitions.")
    lines.append("- **F-BUILD_FAILED**: rebuild canonical, check for tool-call errors during install.")

    out_path = REPO_ROOT / "docs/research/2026-05-10-failure-modes-synthesis.md"
    out_path.write_text("\n".join(lines))

    print(f"Distribution ({len(rows)} CPs):")
    for cat, bucket in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat}: {len(bucket)}")
    print(f"\nWrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
