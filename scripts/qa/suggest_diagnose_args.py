"""suggest_diagnose_args.py — extract diagnose_args from a template's code string.

Most CP templates today have empty `setup_args: {}` with all setup logic
embedded in the `code` field as Python tool calls. To run
feasibility_baseline.py against them we need either:

  (a) explicit `diagnose_args` field on the template, OR
  (b) a regex-parsed best-effort suggestion this script produces

This script reads each CP template, grep-parses the `code` string for
robot_wizard / create_bin / setup_pick_place_controller / Cube_N positions,
and emits a suggested `diagnose_args` dict. Operator pastes it into the
template `diagnose_args` field.

Usage:
  python scripts/qa/suggest_diagnose_args.py CP-37            # single CP
  python scripts/qa/suggest_diagnose_args.py --all > out.txt  # all CPs

Output is a JSON-shaped suggestion. Manual review required — the parser
matches plain literals only (no expressions, no f-strings).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_position(text: str, kw: str) -> Optional[List[float]]:
    """Find `kw=[x, y, z]` literal lists in text. Returns first match."""
    pattern = rf'{kw}\s*=\s*\[\s*([\d.\-eE+]+)\s*,\s*([\d.\-eE+]+)\s*,\s*([\d.\-eE+]+)\s*\]'
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return [float(m.group(1)), float(m.group(2)), float(m.group(3))]
    except ValueError:
        return None


def _find_robot_wizards(code: str) -> List[Dict[str, Any]]:
    """Find robot_wizard(...) calls and extract dest_path + position."""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r'robot_wizard\s*\(([^)]*)\)', code, flags=re.DOTALL):
        block = m.group(1)
        dp = re.search(r'dest_path\s*=\s*"([^"]+)"', block)
        pos = _parse_position(block, "position")
        max_reach = re.search(r'max_reach\s*=\s*([\d.eE+\-]+)', block)
        rname = re.search(r'robot_name\s*=\s*"([^"]+)"', block)
        if dp:
            out.append({
                "robot_path": dp.group(1),
                "robot_base": pos,
                "robot_name": rname.group(1) if rname else None,
                "max_reach": float(max_reach.group(1)) if max_reach else None,
            })
    return out


def _find_setup_pp_calls(code: str) -> List[Dict[str, Any]]:
    """Find setup_pick_place_controller(...) calls."""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r'setup_pick_place_controller\s*\(([^)]*)\)', code, flags=re.DOTALL):
        block = m.group(1)
        rp = re.search(r'robot_path\s*=\s*"([^"]+)"', block)
        # source_paths can be list or single
        srcs: List[str] = []
        for sm in re.finditer(r'source_paths\s*=\s*\[([^\]]*)\]', block):
            srcs.extend(re.findall(r'"([^"]+)"', sm.group(1)))
        sp_single = re.search(r'source_path\s*=\s*"([^"]+)"', block)
        if sp_single:
            srcs.append(sp_single.group(1))
        dest = re.search(r'destination_path\s*=\s*"([^"]+)"', block)
        pick_target = _parse_position(block, "pick_target")
        drop_target = _parse_position(block, "drop_target")
        sensor = re.search(r'sensor_path\s*=\s*"([^"]+)"', block)
        belt = re.search(r'belt_path\s*=\s*"([^"]+)"', block)
        out.append({
            "robot_path": rp.group(1) if rp else None,
            "source_paths": srcs,
            "destination_path": dest.group(1) if dest else None,
            "pick_pose": pick_target,
            "drop_pose": drop_target,
            "sensor_path": sensor.group(1) if sensor else None,
            "belt_path": belt.group(1) if belt else None,
        })
    return out


def _find_obstacles(code: str) -> List[str]:
    """Find prims with PhysicsCollisionAPI applied that look like obstacles
    (Pillar, Bin, Wall, etc — exclude Ground/Table/Belt which are floor)."""
    out: List[str] = []
    for m in re.finditer(r'apply_api_schema\s*\([^)]*prim_path\s*=\s*"([^"]+)"[^)]*PhysicsCollisionAPI',
                         code, flags=re.DOTALL):
        path = m.group(1)
        # Filter out floor primitives that the robot expects to interact with
        name = path.rsplit("/", 1)[-1].lower()
        if any(skip in name for skip in ("ground", "table", "belt", "floor", "conveyor")):
            continue
        out.append(path)
    return out


def _suggest_for_template(template: Dict[str, Any]) -> Dict[str, Any]:
    code = template.get("code") or ""
    robots = _find_robot_wizards(code)
    setup_calls = _find_setup_pp_calls(code)
    obstacles = _find_obstacles(code)

    if len(setup_calls) >= 2 and len(robots) >= 2:
        # Multi-robot: build cycles[]
        cycles: List[Dict[str, Any]] = []
        for sc in setup_calls:
            if not sc.get("robot_path"):
                continue
            # Find matching robot
            robot_match = next(
                (r for r in robots if r["robot_path"] == sc["robot_path"]), None,
            )
            cyc: Dict[str, Any] = {"robot_path": sc["robot_path"]}
            if sc.get("pick_pose"):
                cyc["pick_pose"] = sc["pick_pose"]
            if sc.get("drop_pose"):
                cyc["drop_pose"] = sc["drop_pose"]
            if robot_match and robot_match.get("robot_base"):
                cyc["robot_base"] = robot_match["robot_base"]
            if robot_match and robot_match.get("max_reach"):
                cyc["max_reach"] = robot_match["max_reach"]
            cycles.append(cyc)
        return {
            "_suggestion_kind": "multi_robot_cycles",
            "cycles": cycles,
            "obstacles": obstacles,
            "has_mutex": "mutex_path" in code,
            "_robots_found": len(robots),
            "_setup_calls": len(setup_calls),
        }

    if setup_calls:
        sc = setup_calls[0]
        rb = next(
            (r for r in robots if r["robot_path"] == sc.get("robot_path")), None,
        )
        out: Dict[str, Any] = {"_suggestion_kind": "single_robot"}
        if sc.get("robot_path"):
            out["robot_path"] = sc["robot_path"]
        if sc.get("pick_pose"):
            out["pick_pose"] = sc["pick_pose"]
        if sc.get("drop_pose"):
            out["drop_pose"] = sc["drop_pose"]
        if obstacles:
            out["obstacles"] = obstacles
        if sc.get("sensor_path"):
            out["sensor_path"] = sc["sensor_path"]
        if rb and rb.get("robot_base"):
            out["robot_base"] = rb["robot_base"]
        if rb and rb.get("max_reach"):
            out["max_reach"] = rb["max_reach"]
        return out

    return {"_suggestion_kind": "no_setup_calls_found",
            "_robots_found": len(robots),
            "obstacles": obstacles}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("canonical", nargs="?", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--filter-no-args", action="store_true",
                   help="Only emit CPs where existing setup_args is empty")
    args = p.parse_args()

    templates_dir = REPO_ROOT / "workspace/templates"
    if args.all:
        targets = sorted(templates_dir.glob("CP-*.json"))
    elif args.canonical:
        targets = [templates_dir / f"{args.canonical}.json"]
    else:
        print("usage: suggest_diagnose_args.py CP-37  |  --all", file=sys.stderr)
        return 2

    for t_path in targets:
        if not t_path.exists():
            print(f"# {t_path.stem}: NOT_FOUND", flush=True)
            continue
        try:
            template = json.loads(t_path.read_text())
        except Exception as e:
            print(f"# {t_path.stem}: PARSE_ERROR ({e})")
            continue

        if args.filter_no_args:
            sa = template.get("setup_args") or {}
            if sa:
                continue

        suggestion = _suggest_for_template(template)
        print(f"# {t_path.stem} — {suggestion.get('_suggestion_kind')}")
        clean = {k: v for k, v in suggestion.items() if not k.startswith("_")}
        print(json.dumps(clean, indent=2))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
