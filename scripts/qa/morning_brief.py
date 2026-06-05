"""morning_brief.py — synthesize Phase 0 baseline + Phase 2d telemetry into
per-CP morning brief.

Reads:
  - workspace/baselines/2026-05-09-baseline.json (Phase 0 status)
  - /tmp/overnight_chain_v4.log + /tmp/morning_continuation_v2.log (telemetry probes)

Per-CP, emits:
  - Phase 0 status (stable_fail/flaky)
  - Telemetry summary (last_phase, plan_calls, cubes_delivered, last_errors)
  - Heuristic diagnosis (which phase stuck? planner failing? gripper?)
  - Recommended fix category (controller-bug / template / scenario-profile)

Output: data/2026-05-10-morning-brief.md (committed for review)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_telemetry_log(log_paths: List[Path]) -> Dict[str, Dict[str, Any]]:
    """Parse telemetry probe JSON outputs from chain logs.
    Returns {cp_label: telemetry_summary}.
    Each probe section starts with `$ python ... probe_ctrl_telemetry.py CP-XX`
    and ends with the next $ command or end of log.
    """
    by_cp: Dict[str, Dict[str, Any]] = {}
    for log_path in log_paths:
        if not log_path.exists():
            continue
        text = log_path.read_text()
        # Find probe invocations with CP names
        pattern = re.compile(r'\$\s+\S+\s+probe_ctrl_telemetry\.py\s+(CP-\d+)\s')
        starts = [(m.start(), m.group(1)) for m in pattern.finditer(text)]
        for i, (start, cp) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            section = text[start:end]
            # Look for the JSON output (lines starting with { containing "summary")
            # The JSON spans multiple lines — use brace balancing
            json_start = section.find('{\n  "samples":')
            if json_start == -1:
                json_start = section.find('{"samples":')
            if json_start == -1:
                continue
            depth = 0
            json_end = -1
            for j, ch in enumerate(section[json_start:], json_start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = j + 1
                        break
            if json_end == -1:
                continue
            try:
                payload = json.loads(section[json_start:json_end])
                summary = payload.get("summary", {})
                if cp not in by_cp:
                    by_cp[cp] = summary
            except Exception:
                continue
    return by_cp


def _diagnose_hypothesis(cp_label: str, base_status: str,
                         telem: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Heuristic diagnosis from baseline + telemetry."""
    hypothesis = []
    fix_category = None

    # Pattern detection by CP name (per session memory)
    is_multi_robot = cp_label in {"CP-51", "CP-53", "CP-65", "CP-67", "CP-68", "CP-73", "CP-76"}
    is_ur10 = cp_label in {"CP-69", "CP-70", "CP-71", "CP-72", "CP-73", "CP-74",
                            "CP-75", "CP-76", "CP-77", "CP-78", "CP-79", "CP-80",
                            "CP-81", "CP-82", "CP-83", "CP-84", "CP-85", "CP-86"}
    is_obstacle = cp_label in {"CP-37", "CP-46", "CP-48"}
    is_high_speed_belt = cp_label == "CP-22"

    if base_status == "stable_ok":
        return {"category": "no_action", "hypothesis": ["Already passing"]}

    if telem:
        last_phase = telem.get("last_phase", {})
        plan_calls = telem.get("plan_calls", 0)
        plan_fails = telem.get("plan_fails", 0)
        cubes = telem.get("cubes_delivered_final", 0)
        cycles = telem.get("cycles_attempted_final", 0)
        last_errors = telem.get("last_errors", [])

        if plan_calls == 0 and cubes == 0:
            hypothesis.append("0 plan_calls + 0 deliveries — controller never engaged. "
                              "Sensor never triggered or controller setup didn't subscribe.")
            fix_category = "controller_install"

        if plan_calls > 0 and plan_calls > 5 * (plan_fails + 1):
            fail_rate = plan_fails / plan_calls
            if fail_rate > 0.5:
                hypothesis.append(f"cuRobo plan_pose failing {fail_rate*100:.0f}% — "
                                  f"check pose feasibility, scene_cfg obstacles.")
                fix_category = "planner_tune"

        if cycles > 0 and cubes == 0:
            hypothesis.append(f"{cycles} cycles attempted, 0 delivered — gripper-release "
                              f"or drop-precision issue (Mode B FJ).")
            fix_category = "gripper_or_drop"

        for phase, robot_phases in last_phase.items() if isinstance(last_phase, dict) else []:
            if "seek_cube" in str(robot_phases):
                hypothesis.append(f"{phase} stuck in seek_cube — sensor zone never sees cube.")
                if not fix_category:
                    fix_category = "sensor_zone"

        if last_errors:
            hypothesis.append(f"runtime errors: {last_errors[:2]}")

    # Pattern-based fallback
    if is_ur10 and not fix_category:
        hypothesis.append("UR10 family — IsaacSurfaceGripper articulation-link bug. "
                          "Verify raycast→FixedJoint workaround applied.")
        fix_category = "ur10_grip"
    if is_multi_robot and not fix_category:
        hypothesis.append("Multi-robot relay — investigate MUTEX_PATH spline injection, "
                          "robot-B sensor zone, handoff timing.")
        fix_category = "multi_robot_relay"
    if is_obstacle and not fix_category:
        hypothesis.append("Obstacle-rich — sensor-gate factor + scene-collision exclude_floor "
                          "policy (per scenario-profile spec).")
        fix_category = "scenario_profile_obstacle"
    if is_high_speed_belt and base_status != "stable_ok" and not fix_category:
        hypothesis.append("High-speed belt — was stable_ok in baseline. Re-investigate.")

    if not hypothesis:
        hypothesis.append("No specific signal — needs manual investigation.")
    if not fix_category:
        fix_category = "investigate"

    return {"category": fix_category, "hypothesis": hypothesis}


def main() -> int:
    bl_path = REPO_ROOT / "workspace/baselines/2026-05-09-baseline.json"
    if not bl_path.exists():
        print(f"[FAIL] baseline missing: {bl_path}", file=sys.stderr)
        return 2
    baseline = json.loads(bl_path.read_text())
    rows = baseline.get("results", [])

    log_paths = [
        Path("/tmp/overnight_chain_v4.log"),
        Path("/tmp/morning_continuation_v2.log"),
        Path("/tmp/overnight_chain_v3.log"),
    ]
    telemetry = _parse_telemetry_log(log_paths)
    print(f"parsed telemetry for {len(telemetry)} CPs", flush=True)

    # Build per-category buckets
    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        label = r.get("label")
        status = r.get("status") or r.get("verdict") or "unknown"
        rate = r.get("success_rate")
        telem = telemetry.get(label)
        diag = _diagnose_hypothesis(label, status, telem)
        cat = diag["category"]
        by_category[cat].append({
            "label": label,
            "status": status,
            "rate": rate,
            "telemetry": bool(telem),
            "hypothesis": diag["hypothesis"],
        })

    # Render markdown
    lines = ["# Morning Brief — 2026-05-10", ""]
    lines.append(f"Generated from Phase 0 baseline + autonomous telemetry collection.")
    lines.append("")
    lines.append(f"## Summary")
    summary = baseline.get("summary", {})
    lines.append(f"- stable_ok: **{summary.get('stable_ok', 0)}**")
    lines.append(f"- flaky:     {summary.get('flaky', 0)}")
    lines.append(f"- stable_fail: {summary.get('stable_fail', 0)}")
    lines.append(f"- other:     {summary.get('other', 0)}")
    lines.append(f"- total CPs in patched-set: {len(rows)}")
    lines.append(f"- telemetry probes available: {len(telemetry)}")
    lines.append("")

    cat_order = ["controller_install", "planner_tune", "gripper_or_drop",
                  "sensor_zone", "ur10_grip", "multi_robot_relay",
                  "scenario_profile_obstacle", "investigate", "no_action"]
    for cat in cat_order:
        bucket = by_category.get(cat, [])
        if not bucket:
            continue
        lines.append(f"## {cat} ({len(bucket)} CPs)")
        for cp in bucket:
            telem_mark = "📡" if cp["telemetry"] else "—"
            rate_s = f"{cp['rate']:.2f}" if cp["rate"] is not None else "?"
            lines.append(f"### {cp['label']} {telem_mark} status={cp['status']} rate={rate_s}")
            for h in cp["hypothesis"]:
                lines.append(f"  - {h}")
            lines.append("")
    lines.append("")
    lines.append("## How to act tomorrow")
    lines.append("- **controller_install**: probably means the canonical's controller setup is wrong; check setup_pick_place_controller args.")
    lines.append("- **planner_tune**: cuRobo failing >50%. Check scene_cfg obstacles, pose feasibility. Consider Phase 4 scenario-profile.")
    lines.append("- **gripper_or_drop**: Mode B FJ release or drop-precision. Targeted fix in tool_executor's pick-place handler.")
    lines.append("- **ur10_grip**: verify raycast→FixedJoint workaround. CP-74/80 specific belt-pause-from-callback bug remains.")
    lines.append("- **multi_robot_relay**: MUTEX_PATH spline injection + sensor zone for robot B. Phase 4 scenario-profile candidate.")
    lines.append("- **scenario_profile_obstacle**: sensor-gate factor + scene-collision exclude_floor — Phase 4 candidate.")
    lines.append("- **investigate**: needs manual run + look at simulate_traversal_check output for pattern.")
    lines.append("")

    out_path = REPO_ROOT / "docs/research/2026-05-10-morning-brief.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    print(f"wrote {out_path.relative_to(REPO_ROOT)}", flush=True)
    print(f"category distribution: {dict((k, len(v)) for k, v in by_category.items())}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
