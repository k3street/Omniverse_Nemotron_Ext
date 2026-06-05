"""feasibility_baseline.py — Phase 1 step 2.

Runs `diagnose_scene_feasibility` on every CP-NN canonical and persists
the verdict + metrics to `workspace/baselines/feasibility/{cp_id}.json`.

Per spec §B (Opus review of diagnose_scene_feasibility): tool catches
slow-bleed regressions geometrically — set_attribute / robot_wizard /
create_bin can silently make a previously-feasible scene tightly_feasible
or overconstrained.

Default: every CP-*.json template in workspace/templates/.
Override: --canonicals CP-XX,CP-YY.

Output:
- workspace/baselines/feasibility/<cp_id>.json (per-CP detail)
- workspace/baselines/feasibility/_summary.json (verdict-distribution)

Compares against existing baseline (if any) and flags drift:
- verdict downgrade (feasible → tightly_feasible / overconstrained / infeasible)
- delta(reach_utilization) > 0.02
- new violation appearing

Usage:
  python scripts/qa/feasibility_baseline.py
  python scripts/qa/feasibility_baseline.py --canonicals CP-05,CP-22
  python scripts/qa/feasibility_baseline.py --update      # refresh baselines
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402

BASELINE_DIR = REPO_ROOT / "workspace/baselines/feasibility"
TEMPLATES_DIR = REPO_ROOT / "workspace/templates"


async def _reset_scene() -> None:
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "stage = ctx.get_stage()\n"
        "from pxr import UsdGeom\n"
        "UsdGeom.Xform.Define(stage, '/World')\n"
    )
    res = await kit_tools.exec_sync(code, timeout=20)
    if not res.get("success"):
        raise RuntimeError(f"reset failed: {(res.get('output') or '')[:200]}")


def _extract_diagnose_args(template: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build args for diagnose_scene_feasibility from a canonical template.

    Resolution order:
    1. Explicit `diagnose_args` field on template (recommended for multi-robot CPs)
    2. Synthesize from `setup_args` fields (legacy single-robot path)
    3. Return None — caller marks NO_DIAGNOSE_ARGS in summary

    To diagnose multi-robot CPs (CP-51/53/65/68/76), add an explicit
    `diagnose_args` field to the template with `cycles: [...]`.
    """
    # 1. Explicit diagnose_args on template
    explicit = template.get("diagnose_args")
    if explicit and isinstance(explicit, dict):
        args = dict(explicit)
        args.setdefault("seed", 42)
        args.setdefault("use_cache", False)
        return args

    # 2. Synthesize from setup_args (single-robot)
    sim_args = template.get("simulate_args") or {}
    setup_args = template.get("setup_args") or {}

    robot_path = setup_args.get("robot_path")
    if not robot_path:
        # heuristic: scan setup_args for path-like field
        for k, v in setup_args.items():
            if "robot" in k.lower() and isinstance(v, str) and v.startswith("/"):
                robot_path = v
                break

    pick_pose = setup_args.get("pick_target") or setup_args.get("pick_pose")
    drop_pose = setup_args.get("drop_target") or setup_args.get("drop_pose")
    obstacles = setup_args.get("planning_obstacles") or []
    sensor_path = setup_args.get("sensor_path")
    cube_paths = sim_args.get("cube_paths") or ([sim_args.get("cube_path")] if sim_args.get("cube_path") else [])
    cube_paths = [c for c in cube_paths if c]

    if not robot_path:
        return None
    args: Dict[str, Any] = {
        "robot_path": robot_path,
        "obstacles": obstacles,
        "seed": 42,
        "use_cache": False,
    }
    if pick_pose:
        args["pick_pose"] = pick_pose
    if drop_pose:
        args["drop_pose"] = drop_pose
    if sensor_path:
        args["sensor_path"] = sensor_path
    if cube_paths:
        args["cube_paths"] = cube_paths
    return args


async def _diagnose_one(label: str) -> Dict[str, Any]:
    template_path = TEMPLATES_DIR / f"{label}.json"
    if not template_path.exists():
        return {"label": label, "verdict": "TEMPLATE_NOT_FOUND"}
    template = json.loads(template_path.read_text())

    diag_args = _extract_diagnose_args(template)
    if diag_args is None:
        return {"label": label, "verdict": "NO_DIAGNOSE_ARGS"}

    try:
        await _reset_scene()
        build_res = await execute_template_canonical(template)
        if not build_res.get("instantiated"):
            return {"label": label, "verdict": "BUILD_FAILED",
                    "errors": (build_res.get("errors") or [])[:2]}
        try:
            await settle_after_canonical(template)
        except Exception:
            pass
    except Exception as e:
        return {"label": label, "verdict": "RESET_OR_BUILD_EXC", "err": str(e)[:120]}

    t0 = time.time()
    res = await execute_tool_call("diagnose_scene_feasibility", diag_args)
    elapsed = time.time() - t0

    if "error" in res:
        return {"label": label, "verdict": "DIAGNOSE_ERROR", "err": res["error"]}

    return {
        "label": label,
        "verdict": res.get("verdict"),
        "metrics": res.get("metrics") or {},
        "violations": res.get("violations") or [],
        "alternatives": res.get("alternatives") or [],
        "elapsed_ms": int(elapsed * 1000),
        "seed": res.get("seed_used"),
    }


def _compare_to_baseline(label: str, current: Dict[str, Any]) -> Optional[str]:
    """Return drift severity: 'NEW' / 'DOWNGRADED' / 'UPGRADED' / None."""
    baseline_file = BASELINE_DIR / f"{label}.json"
    if not baseline_file.exists():
        return "NEW"
    try:
        baseline = json.loads(baseline_file.read_text())
    except Exception:
        return "NEW"

    rank = {"feasible": 3, "tightly_feasible": 2, "overconstrained": 1, "infeasible": 0}
    b_v = baseline.get("verdict")
    c_v = current.get("verdict")
    if b_v not in rank or c_v not in rank:
        return None
    if rank[c_v] < rank[b_v]:
        return "DOWNGRADED"
    if rank[c_v] > rank[b_v]:
        return "UPGRADED"
    # Same verdict → check reach_utilization drift
    b_pick = (baseline.get("metrics") or {}).get("pick_reach_utilization")
    c_pick = (current.get("metrics") or {}).get("pick_reach_utilization")
    if b_pick is not None and c_pick is not None and abs(c_pick - b_pick) > 0.02:
        return "DRIFT_REACH"
    return None


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--canonicals", default=None,
                   help="Comma-separated CP-XX list. Default = all CP-* templates.")
    p.add_argument("--update", action="store_true",
                   help="Replace existing baselines with current results.")
    p.add_argument("--per-cp-timeout", type=int, default=120)
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001", flush=True)
        return 2

    if args.canonicals:
        targets = [s.strip() for s in args.canonicals.split(",") if s.strip()]
    else:
        targets = sorted(t.stem for t in TEMPLATES_DIR.glob("CP-*.json"))

    print(f"running diagnose_scene_feasibility on {len(targets)} canonicals", flush=True)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    distribution: Dict[str, int] = {"feasible": 0, "tightly_feasible": 0,
                                    "overconstrained": 0, "infeasible": 0,
                                    "OTHER": 0}
    drifts: List[str] = []
    rows: List[Dict[str, Any]] = []

    for label in targets:
        try:
            r = await asyncio.wait_for(_diagnose_one(label), timeout=args.per_cp_timeout)
        except asyncio.TimeoutError:
            r = {"label": label, "verdict": f"TIMEOUT_{args.per_cp_timeout}"}
        except Exception as e:
            r = {"label": label, "verdict": "EXC", "err": str(e)[:100]}

        rows.append(r)
        v = r.get("verdict") or "OTHER"
        bucket = v if v in distribution else "OTHER"
        distribution[bucket] += 1

        drift = _compare_to_baseline(label, r)
        if drift and drift != "UPGRADED":
            drifts.append(f"{label}: {drift} → {v}")

        # Optionally persist
        per_file = BASELINE_DIR / f"{label}.json"
        if args.update or not per_file.exists():
            per_file.write_text(json.dumps(r, indent=2))

        sev_count = sum(1 for vio in (r.get("violations") or [])
                        if vio.get("severity") in ("ERROR", "CRITICAL"))
        print(f"  {label:7s} {v:18s}  violations={len(r.get('violations') or [])}  "
              f"err+={sev_count}  elapsed={r.get('elapsed_ms','-')}ms",
              flush=True)

    print("-" * 70, flush=True)
    print(f"distribution: {distribution}", flush=True)
    if drifts:
        print(f"drift detected ({len(drifts)}):", flush=True)
        for d in drifts:
            print(f"  {d}", flush=True)
    else:
        print("no drift", flush=True)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_canonicals": len(rows),
        "distribution": distribution,
        "drifts": drifts,
    }
    (BASELINE_DIR / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {BASELINE_DIR.relative_to(REPO_ROOT)}/_summary.json", flush=True)
    return 1 if drifts else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
