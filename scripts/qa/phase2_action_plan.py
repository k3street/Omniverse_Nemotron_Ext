"""phase2_action_plan.py — turn phase2_triage.json into actionable work items.

Consumes:
  - workspace/baselines/phase2_triage.json (from phase2_triage.py)
  - workspace/baselines/feasibility/<cp_id>.json (from feasibility_baseline.py)

Emits:
  - workspace/baselines/phase2_action_plan.json (structured)
  - stdout: human-readable per-class action plan with concrete deltas

For each canonical, surfaces:
  - 2a-TEMPLATE_FIX: which violation axes; suggested template-field changes
  - 2b-TEMPLATE_TUNE: which obstacle/sensor params to adjust + by how much
  - 2c-CONTROLLER_TUNE: input data for Phase 4 scenario-profile tuning
                        (which profile candidate, current vs target reach)
  - 2d-CONTROLLER_BUG: bug category (Mode B FJ / drop precision /
                       multi-robot relay / sensor-gate); priority order

Usage:
  python scripts/qa/phase2_action_plan.py
  python scripts/qa/phase2_action_plan.py --filter 2a    # only TEMPLATE_FIX
  python scripts/qa/phase2_action_plan.py --filter 2d    # only CONTROLLER_BUG
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_triage(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"[FAIL] triage file missing: {path}", file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text()).get("rows", [])


def _load_feasibility_detail(label: str, base_dir: Path) -> Optional[Dict[str, Any]]:
    f = base_dir / f"{label}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _action_2a(detail: Dict[str, Any]) -> Dict[str, Any]:
    """TEMPLATE_FIX: scene has CRITICAL violations — rewrite required."""
    actions: List[Dict[str, Any]] = []
    for v in (detail.get("violations") or []):
        if v.get("severity") != "CRITICAL":
            continue
        axis = v.get("axis")
        if axis == "ik_feasible":
            actions.append({
                "do": "Pose unreachable. Move pick/drop closer to robot OR use longer-reach robot.",
                "axis": axis,
                "details": v.get("details", {}),
            })
        elif axis == "inside_obstacle_bbox":
            d = v.get("details") or {}
            ax = d.get("suggest_axis", "z")
            dm = d.get("suggest_delta_m", 0.05)
            actions.append({
                "do": f"Drop pose inside obstacle '{v.get('value')}'. Shift {dm:.2f}m along +{ax}.",
                "axis": axis,
                "delta": {"axis": ax, "shift_m": dm},
            })
        elif axis == "reach_utilization":
            actions.append({
                "do": f"Pose at {(v.get('value') or 0):.0%} of reach. Move closer to robot base.",
                "axis": axis,
            })
        elif axis == "collision_distance":
            actions.append({
                "do": f"Robot starts in collision (dist={v.get('value'):.3f}m). Reposition robot or shrink obstacle.",
                "axis": axis,
            })
    if not actions:
        actions.append({"do": "Generic infeasibility — manual review of scene template required."})
    return {"class": "2a-TEMPLATE_FIX", "actions": actions}


def _action_2b(detail: Dict[str, Any]) -> Dict[str, Any]:
    """TEMPLATE_TUNE: ERROR violations — reposition obstacles/sensor."""
    actions: List[Dict[str, Any]] = []
    for v in (detail.get("violations") or []):
        if v.get("severity") != "ERROR":
            continue
        axis = v.get("axis")
        if axis == "clearance_pct":
            pct = v.get("value", 0)
            actions.append({
                "do": f"Transit corridor only {pct:.0f}% clear. Move blocking obstacle out of robot path OR add way-point in trajectory.",
                "axis": axis,
                "current": pct,
                "target": ">= 90",
            })
        elif axis == "collision_distance":
            actions.append({
                "do": f"Pose too close to obstacle (dist={v.get('value'):.3f}m, need >0.005m). Increase clearance.",
                "axis": axis,
            })
        elif axis == "cube_in_sensor_zone_at_settle":
            actions.append({
                "do": "No cube reaches sensor at settle-tick. Either move sensor closer to cube spawn OR widen sensor radius.",
                "axis": axis,
            })
        elif axis == "mutex_conflict":
            actions.append({
                "do": "Multi-robot corridors overlap without mutex. Add MUTEX_PATH OR separate workspaces by ≥0.30m.",
                "axis": axis,
            })
    if not actions:
        actions.append({"do": "Generic overconstraint — review template's obstacle placement."})
    return {"class": "2b-TEMPLATE_TUNE", "actions": actions}


def _action_2c(detail: Dict[str, Any], gate_rate: Optional[float]) -> Dict[str, Any]:
    """CONTROLLER_TUNE: tightly_feasible + still failing → Phase 4 candidate."""
    actions: List[Dict[str, Any]] = []
    metrics = detail.get("metrics") or {}
    pick_reach = metrics.get("pick_reach_utilization")
    drop_reach = metrics.get("drop_reach_utilization")
    pick_manip = metrics.get("pick_manipulability")
    drop_manip = metrics.get("drop_manipulability")
    if pick_reach and pick_reach > 0.90:
        actions.append({
            "do": f"Pick at {pick_reach:.0%} reach — controller may IK-fail near edge. Profile candidate: high-reach scenario.",
            "axis": "reach_utilization", "pose": "pick", "value": pick_reach,
        })
    if drop_reach and drop_reach > 0.90:
        actions.append({
            "do": f"Drop at {drop_reach:.0%} reach — IK fragile. Profile candidate: high-reach.",
            "axis": "reach_utilization", "pose": "drop", "value": drop_reach,
        })
    if pick_manip and pick_manip < 0.10:
        actions.append({
            "do": f"Pick manipulability {pick_manip:.3f} — near-singular config. Reorient EE or use different IK seed.",
            "axis": "manipulability",
        })
    if drop_manip and drop_manip < 0.10:
        actions.append({
            "do": f"Drop manipulability {drop_manip:.3f} — near-singular. Tilt EE.",
            "axis": "manipulability",
        })
    actions.append({
        "do": f"Failing baseline rate {gate_rate or 0:.2f}. Auto-tune candidate for Phase 4 scenario-profile sweep.",
        "phase": "4-scenario-profile",
    })
    return {"class": "2c-CONTROLLER_TUNE", "actions": actions}


def _action_2d(label: str, gate_status: Optional[str]) -> Dict[str, Any]:
    """CONTROLLER_BUG: feasible but failing → real platform bug."""
    # Bug-category heuristic by canonical name patterns
    actions: List[Dict[str, Any]] = []
    bug_categories: List[str] = []
    if any(label.startswith(p) for p in ("CP-51", "CP-53", "CP-65", "CP-67", "CP-68", "CP-73", "CP-76")):
        bug_categories.append("multi_robot_relay")
        actions.append({
            "do": "Multi-robot CP — investigate handoff timing, MUTEX_PATH spline injection, robot-B sensor zone.",
            "category": "multi_robot_relay",
        })
    if any(label.startswith(p) for p in ("CP-74", "CP-80", "CP-84", "CP-85")):
        bug_categories.append("ur10_grip")
        actions.append({
            "do": "UR10 CP — IsaacSurfaceGripper articulation-link bug. Check raycast workaround + FixedJoint.",
            "category": "ur10_grip",
        })
    if any(label.startswith(p) for p in ("CP-37", "CP-46", "CP-48")):
        bug_categories.append("obstacle_rich")
        actions.append({
            "do": "Obstacle-rich CP — investigate sensor-gate factor + scene-collision exclude_floor policy.",
            "category": "obstacle_rich",
        })
    if not bug_categories:
        actions.append({
            "do": f"Feasible but {gate_status or 'failing'}. Investigate Mode B FJ at grip_close, drop precision, or settle-tick.",
            "category": "general",
        })
    return {"class": "2d-CONTROLLER_BUG", "actions": actions, "bug_categories": bug_categories}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--triage", default=str(REPO_ROOT / "workspace/baselines/phase2_triage.json"))
    p.add_argument("--feasibility-dir", default=str(REPO_ROOT / "workspace/baselines/feasibility"))
    p.add_argument("--out", default=str(REPO_ROOT / "workspace/baselines/phase2_action_plan.json"))
    p.add_argument("--filter", default=None,
                   help="Only emit one class (2a/2b/2c/2d/MARGINAL_OK/STABLE_OK)")
    args = p.parse_args()

    rows = _load_triage(Path(args.triage))
    feas_dir = Path(args.feasibility_dir)

    plans: List[Dict[str, Any]] = []
    for r in rows:
        cls = r.get("triage_class", "")
        label = r.get("label")
        gate_rate = r.get("function_gate_rate")
        gate_status = r.get("function_gate_status")
        if args.filter and not cls.startswith(args.filter):
            continue

        detail = _load_feasibility_detail(label, feas_dir) or {}

        if cls == "2a-TEMPLATE_FIX":
            ap = _action_2a(detail)
        elif cls == "2b-TEMPLATE_TUNE":
            ap = _action_2b(detail)
        elif cls == "2c-CONTROLLER_TUNE":
            ap = _action_2c(detail, gate_rate)
        elif cls == "2d-CONTROLLER_BUG":
            ap = _action_2d(label, gate_status)
        else:
            ap = {"class": cls, "actions": [{"do": "no action — passing"}]}

        plans.append({"label": label, "triage_class": cls,
                      "function_gate_rate": gate_rate, **ap})

    # Pretty-print
    by_cls: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p_ in plans:
        by_cls[p_["triage_class"]].append(p_)

    print(f"Phase 2 Action Plan — {len(plans)} canonicals across {len(by_cls)} class(es)")
    print("=" * 78)
    for cls in ("2a-TEMPLATE_FIX", "2d-CONTROLLER_BUG", "2b-TEMPLATE_TUNE",
                "2c-CONTROLLER_TUNE", "MARGINAL_OK", "STABLE_OK",
                "UNKNOWN_NEED_DIAGNOSE", "UNCLASSIFIED"):
        bucket = by_cls.get(cls, [])
        if not bucket:
            continue
        print(f"\n[{cls}]  {len(bucket)} CP(s)")
        for p_ in bucket:
            actions = p_.get("actions") or []
            print(f"  {p_['label']}:")
            for a in actions:
                line = a.get("do") or json.dumps(a)
                print(f"    • {line}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_plans": len(plans),
        "by_class": {k: len(v) for k, v in by_cls.items()},
        "plans": plans,
    }
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
