"""extract_poses_from_code.py — AST-based pose extractor for CP templates.

Most CP templates compute pick_target / drop_target inline in the code
field rather than passing as literals. To enable feasibility classification
we need the resolved pose values.

Strategy: parse the canonical's `code` string into Python AST, walk it
to find calls to setup_pick_place_controller and create_bin, then
evaluate any constant or named-variable kwarg values via a sandboxed
namespace built from prior assignments.

This is conservative: only supports simple cases (literal lists,
arithmetic on literals, references to earlier variable assignments).
For complex computations (loops, function calls, runtime properties)
we fall back to the suggest_diagnose_args output.

Output: per-CP, an enriched diagnose_args dict written to:
  workspace/baselines/feasibility/_extracted_poses.json
And optionally injected into the template's diagnose_args field.

Usage:
  python scripts/qa/extract_poses_from_code.py CP-37
  python scripts/qa/extract_poses_from_code.py --all --inject
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_eval_const(node: ast.AST, namespace: Dict[str, Any]) -> Any:
    """Best-effort evaluator for constant-like AST nodes. Returns None if
    we can't evaluate safely."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        elts = [_ast_eval_const(e, namespace) for e in node.elts]
        if any(e is None for e in elts):
            return None
        return elts
    if isinstance(node, ast.Tuple):
        elts = [_ast_eval_const(e, namespace) for e in node.elts]
        if any(e is None for e in elts):
            return None
        return tuple(elts)
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _ast_eval_const(node.operand, namespace)
        if isinstance(v, (int, float)):
            return -v
        return None
    if isinstance(node, ast.BinOp):
        l = _ast_eval_const(node.left, namespace)
        r = _ast_eval_const(node.right, namespace)
        if l is None or r is None:
            return None
        try:
            if isinstance(node.op, ast.Add): return l + r
            if isinstance(node.op, ast.Sub): return l - r
            if isinstance(node.op, ast.Mult): return l * r
            if isinstance(node.op, ast.Div): return l / r
        except Exception:
            return None
    return None


def _walk_code(code: str) -> Dict[str, Any]:
    """Walk the canonical's code AST. Build:
      - namespace: variable assignments (name → value or None)
      - calls: list of (func_name, kwargs dict)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"namespace": {}, "calls": []}

    namespace: Dict[str, Any] = {}
    calls: List[Dict[str, Any]] = []

    for stmt in tree.body:
        # Variable assignment
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            namespace[stmt.targets[0].id] = _ast_eval_const(stmt.value, namespace)
            continue
        # Direct function call as expression
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Name):
                func_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                func_name = call.func.attr
            else:
                continue
            kwargs = {}
            for kw in call.keywords:
                if kw.arg is None:
                    continue
                kwargs[kw.arg] = _ast_eval_const(kw.value, namespace)
            calls.append({"func": func_name, "kwargs": kwargs})

    return {"namespace": namespace, "calls": calls}


def _extract_poses(template: Dict[str, Any]) -> Dict[str, Any]:
    """Pull pick_pose / drop_pose / cube_path / bin_path / robot_base / max_reach
    from a canonical's code by evaluating its calls."""
    code = template.get("code") or ""
    walk = _walk_code(code)
    calls = walk["calls"]

    pick_pose: Optional[List[float]] = None
    drop_pose: Optional[List[float]] = None
    cube_path: Optional[str] = None
    bin_path: Optional[str] = None
    robot_path: Optional[str] = None
    robot_base: Optional[List[float]] = None
    max_reach: Optional[float] = None
    obstacles: List[str] = []
    sensor_path: Optional[str] = None

    for call in calls:
        f = call["func"]
        kw = call["kwargs"]
        if f == "robot_wizard":
            if not robot_path:
                robot_path = kw.get("dest_path")
            if not robot_base:
                robot_base = kw.get("position")
            if not max_reach and kw.get("max_reach") is not None:
                max_reach = float(kw["max_reach"])
        elif f == "setup_pick_place_controller":
            if not pick_pose:
                pick_pose = kw.get("pick_target")
            if not drop_pose:
                drop_pose = kw.get("drop_target")
            if not robot_path:
                robot_path = kw.get("robot_path")
            if not sensor_path:
                sensor_path = kw.get("sensor_path")
        elif f == "create_prim" and not pick_pose:
            path = kw.get("prim_path") or ""
            # cube position is candidate for pick_pose if no explicit pick_target.
            # Match /Cube, /Cube_1, /Cube_2, ... but exclude Bin/Pillar/Table.
            tail = path.rsplit("/", 1)[-1].lower()
            is_cube = tail == "cube" or tail.startswith("cube_") or tail.startswith("cube ")
            if is_cube and isinstance(kw.get("position"), list) and len(kw["position"]) == 3:
                pick_pose = kw["position"]
                cube_path = path
        elif f == "create_bin":
            if not drop_pose and isinstance(kw.get("position"), list) and len(kw["position"]) == 3:
                drop_pose = kw["position"]
                bin_path = kw.get("prim_path")
        elif f == "apply_api_schema" and kw.get("schema_name") == "PhysicsCollisionAPI":
            path = kw.get("prim_path")
            if path and not any(skip in path.lower() for skip in
                                  ("ground", "table", "belt", "floor", "conveyor", "cube")):
                if path not in obstacles:
                    obstacles.append(path)

    # Approach-pose offset: real picks/drops happen ABOVE the cube/bin
    # center, not at the geometric center (would be inside the object).
    # Extracted positions are object centers; add +5cm in z for realistic
    # IK feasibility checks.
    APPROACH_Z_OFFSET = 0.05

    out: Dict[str, Any] = {}
    if robot_path: out["robot_path"] = robot_path
    if pick_pose:
        out["pick_pose"] = [pick_pose[0], pick_pose[1], pick_pose[2] + APPROACH_Z_OFFSET]
    if drop_pose:
        out["drop_pose"] = [drop_pose[0], drop_pose[1], drop_pose[2] + APPROACH_Z_OFFSET]
    if obstacles: out["obstacles"] = obstacles
    if sensor_path: out["sensor_path"] = sensor_path
    if robot_base: out["robot_base"] = robot_base
    if max_reach: out["max_reach"] = max_reach
    return out


def _merge_diagnose_args(existing: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Merge: extracted values fill in missing fields, existing wins on conflict."""
    out = dict(existing or {})
    for k, v in extracted.items():
        if v is None:
            continue
        if k not in out or out[k] in (None, [], "", 0):
            out[k] = v
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("canonical", nargs="?", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--inject", action="store_true",
                   help="Write merged diagnose_args back to template files")
    args = p.parse_args()

    templates_dir = REPO_ROOT / "workspace/templates"
    if args.all:
        targets = sorted(templates_dir.glob("CP-*.json"))
    elif args.canonical:
        targets = [templates_dir / f"{args.canonical}.json"]
    else:
        print("usage: extract_poses_from_code.py CP-37  |  --all", file=sys.stderr)
        return 2

    n_with_poses = 0
    n_total = 0
    n_injected = 0
    extracted_all: Dict[str, Any] = {}

    for t_path in targets:
        if not t_path.exists():
            continue
        n_total += 1
        try:
            template = json.loads(t_path.read_text())
        except Exception:
            continue
        extracted = _extract_poses(template)
        if extracted.get("pick_pose") or extracted.get("drop_pose"):
            n_with_poses += 1
        extracted_all[t_path.stem] = extracted

        if args.inject:
            existing = template.get("diagnose_args") or {}
            merged = _merge_diagnose_args(existing, extracted)
            if merged != existing:
                template["diagnose_args"] = merged
                t_path.write_text(json.dumps(template, indent=2))
                n_injected += 1
                print(f"[INJ] {t_path.stem}: + {[k for k in extracted if k not in existing]}")

    out_path = REPO_ROOT / "workspace/baselines/feasibility/_extracted_poses.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(extracted_all, indent=2))

    print("-" * 70)
    print(f"summary: total={n_total} with_pick_or_drop={n_with_poses} injected={n_injected}")
    print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
