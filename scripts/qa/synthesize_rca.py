"""synthesize_rca.py — analyze docs/research/2026-05-10-rca/*.json and
emit a categorized summary of failure patterns.

Reads each per-CP probe JSON, extracts:
  - last_phase per robot
  - phase histogram (top phase + percentage)
  - cube trajectory deltas (stuck / fell / passed-without-trigger)
  - last_errors

Categorizes into patterns:
  P-WALL_STUCK: cube stuck against an obstacle (Δ<5cm)
  P-FELL_OFF_BELT: cube z<0.5
  P-SENSOR_NEVER_FIRES: cube travelled but plan_calls=0
  P-PLAN_FAIL: ctrl:last_error = planning failed
  P-MULTI_ROBOT: multiple robots, both stuck after some delivery
  P-UR10_EVENT_CYCLE: UR10 event-state cycling
  P-BUILD_FAIL: probe couldn't even build scene

Outputs: docs/research/2026-05-10-rca-synthesis.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
RCA_DIR = REPO_ROOT / "docs/research/2026-05-10-rca"


def _classify(label: str, data: Dict[str, Any]) -> Dict[str, Any]:
    summary = data.get("summary") or {}
    cube_traj = data.get("cube_trajectories") or []

    if not summary or "error" in data:
        return {"category": "P-BUILD_FAIL", "details": data.get("error") or "no summary",
                "cubes_delivered": 0, "plan_calls": 0,
                "stuck": 0, "fallen": 0, "passed": 0,
                "last_phase": {}, "errors": []}

    cube_paths = summary.get("cube_paths") or []
    last_phase = summary.get("last_phase") or {}
    plan_calls = summary.get("plan_calls", 0)
    last_errors = summary.get("last_errors") or []
    duration = summary.get("duration_s") or 1
    cubes_delivered = summary.get("cubes_delivered_final", 0)

    # Cube-trajectory features
    stuck_cubes: List[str] = []
    fallen_cubes: List[str] = []
    passed_cubes: List[str] = []
    for cp in cube_paths:
        positions = [s.get(cp) for s in cube_traj if s.get(cp) is not None]
        if len(positions) < 3:
            continue
        init = positions[0]
        final = positions[-1]
        dx = final[0] - init[0]
        dy = final[1] - init[1]
        dz = final[2] - init[2]
        dist = (dx**2 + dy**2 + dz**2) ** 0.5
        if dist < 0.05:
            stuck_cubes.append(cp)
        if final[2] < 0.5:
            fallen_cubes.append(cp)
        if abs(dx) > 1.0 and dist > 1.0:
            passed_cubes.append(cp)

    # Phase patterns
    n_robots = len(last_phase) if isinstance(last_phase, dict) else 0
    is_multi_robot = n_robots >= 2
    has_ur10 = any("UR10" in str(rpath) for rpath in last_phase) if isinstance(last_phase, dict) else False
    has_event_cycle = any("event=" in str(p) for p in (last_phase.values() if isinstance(last_phase, dict) else []))

    has_plan_fail_error = any("planning failed" in str(e).lower() for e in last_errors)

    # Categorization (priority order)
    cats: List[str] = []
    if has_plan_fail_error:
        cats.append("P-PLAN_FAIL")
    if fallen_cubes:
        cats.append(f"P-FELL_OFF_BELT({len(fallen_cubes)} cubes)")
    if stuck_cubes:
        cats.append(f"P-WALL_STUCK({len(stuck_cubes)} cubes)")
    if has_ur10 and has_event_cycle:
        cats.append("P-UR10_EVENT_CYCLE")
    if is_multi_robot and cubes_delivered >= 1:
        cats.append("P-MULTI_ROBOT_PARTIAL")
    if not cats and passed_cubes and plan_calls == 0:
        cats.append("P-SENSOR_NEVER_FIRES")
    if not cats:
        cats.append("P-OTHER")

    return {
        "category": " | ".join(cats),
        "stuck": len(stuck_cubes),
        "fallen": len(fallen_cubes),
        "passed": len(passed_cubes),
        "plan_calls": plan_calls,
        "cubes_delivered": cubes_delivered,
        "last_phase": last_phase,
        "errors": last_errors[:2],
    }


def main() -> int:
    if not RCA_DIR.exists():
        print(f"missing {RCA_DIR}", file=sys.stderr)
        return 2

    rca_files = sorted(RCA_DIR.glob("CP-*.json"))
    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rows: List[Dict[str, Any]] = []

    for f in rca_files:
        label = f.stem
        try:
            text = f.read_text()
            # Strip leading non-JSON lines (ros-mcp warning, etc)
            json_start = text.find("{")
            if json_start >= 0:
                data = json.loads(text[json_start:])
            else:
                data = {"error": "no_json_in_file"}
        except Exception as e:
            data = {"error": f"json_parse_failed: {e}"}
        c = _classify(label, data)
        c["label"] = label
        rows.append(c)
        by_category[c["category"]].append(c)

    lines = ["# RCA Synthesis — 2026-05-10", ""]
    lines.append(f"Probed {len(rows)} CPs from patched-set + control samples.")
    lines.append("")
    lines.append("## Pattern Distribution")
    for cat, bucket in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- **{cat}**: {len(bucket)} CPs")
        for c in bucket[:3]:
            lines.append(f"  - {c['label']} (delivered={c['cubes_delivered']})")
    lines.append("")
    lines.append("## Detail per CP")
    for c in rows:
        lines.append(f"### {c['label']} — `{c['category']}`")
        lines.append(f"  - last_phase: `{c.get('last_phase')}`")
        lines.append(f"  - plan_calls={c.get('plan_calls')} cubes_delivered={c.get('cubes_delivered')}")
        if c.get("stuck") or c.get("fallen") or c.get("passed"):
            lines.append(f"  - cube features: stuck={c.get('stuck')} fallen={c.get('fallen')} passed_no_trigger={c.get('passed')}")
        if c.get("errors"):
            for e in c["errors"]:
                lines.append(f"  - error: `{str(e)[:120]}`")
        lines.append("")

    out_path = REPO_ROOT / "docs/research/2026-05-10-rca-synthesis.md"
    out_path.write_text("\n".join(lines))

    # Also print summary to stdout
    print(f"Pattern Distribution ({len(rows)} CPs):")
    for cat, bucket in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat}: {len(bucket)}")
    print(f"\nWrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
