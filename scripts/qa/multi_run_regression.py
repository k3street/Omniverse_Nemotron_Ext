"""multi_run_regression.py — Phase 0 baseline harness.

For each canonical in TARGETS, build the scene once, then call
simulate_traversal_check with n_runs=N seed=SEED so the kit-side
script repeatedly resets cube xforms + ctrl:* attrs + joint state and
replays. Writes the result to workspace/baselines/<TAG>.json with
per-CP success_rate + status (stable_ok | flaky | stable_fail).

This is the multi-run counterpart to regression_post_patches.py — it
shares the build-and-call structure but uses n_runs to capture the
flakiness signal that single-run regressions miss.

Default TARGETS = patched-set (25 CPs from the 2026-05-09 session).
Override with --canonicals CP-XX,CP-YY or --all (full 86).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402

# Patched-set: the 25 canonicals from 2026-05-09 controller-logic session.
PATCHED_SET = [
    "CP-05", "CP-06", "CP-22", "CP-35", "CP-37", "CP-40",
    "CP-46", "CP-48", "CP-51", "CP-52", "CP-53", "CP-57",
    "CP-58", "CP-59", "CP-60", "CP-62", "CP-65", "CP-67",
    "CP-68", "CP-73", "CP-74", "CP-76", "CP-80", "CP-84", "CP-85",
]


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


async def _run_one(label: str, n_runs: int, seed: int) -> Dict:
    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        return {"label": label, "verdict": "TEMPLATE_NOT_FOUND"}
    template = json.loads(template_path.read_text())
    sim_args = dict(template.get("simulate_args") or {})
    if not sim_args:
        return {"label": label, "verdict": "NO_SIMULATE_ARGS"}

    # Inject Phase 0 multi-run params
    sim_args["n_runs"] = n_runs
    sim_args["seed"] = seed

    try:
        await _reset_scene()
    except Exception as e:
        return {"label": label, "verdict": "RESET_FAILED", "err": str(e)[:120]}

    try:
        build_res = await execute_template_canonical(template)
    except Exception as e:
        return {"label": label, "verdict": "BUILD_EXC", "err": str(e)[:120]}

    if not build_res.get("instantiated"):
        return {
            "label": label,
            "verdict": "BUILD_FAILED",
            "n": f"{build_res.get('n_ok')}/{build_res.get('n_calls')}",
            "errs": (build_res.get("errors") or [])[:2],
        }
    try:
        await settle_after_canonical(template)
    except Exception:
        pass

    t0 = time.time()
    res = await execute_tool_call("simulate_traversal_check", sim_args)
    elapsed = time.time() - t0
    out = (res.get("output") or "").strip()
    json_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        return {
            "label": label, "verdict": "NO_RESULT",
            "tail": out[-200:], "elapsed_s": round(elapsed, 1),
        }
    d = json.loads(json_lines[-1])
    runs = d.get("runs") or []
    return {
        "label": label,
        "build": f"{build_res.get('n_ok')}/{build_res.get('n_calls')}",
        "status": d.get("status"),
        "success_rate": d.get("success_rate"),
        "n_ok": d.get("n_ok"),
        "n_runs": d.get("n_runs"),
        "seed_base": d.get("seed_base"),
        "target_bbox": d.get("target_bbox"),
        "per_run": [
            {
                "seed": r.get("seed"),
                "success": r.get("success"),
                "cube_final": [round(c, 4) for c in r["cube_final"]] if r.get("cube_final") else None,
                "speed": round(r.get("cube_speed", 0) or 0, 4),
                "in_xy": r.get("in_target_xy"),
                "above_floor": r.get("above_floor"),
                "at_rest": r.get("at_rest"),
            }
            for r in runs
        ],
        "elapsed_s": round(elapsed, 1),
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--canonicals", default=None,
                   help="Comma-separated CP-XX list. Default = patched-set (25).")
    p.add_argument("--all", action="store_true",
                   help="Run ALL CP-* canonicals from workspace/templates/")
    p.add_argument("--n-runs", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=None,
                   help="Output JSON path. Default = workspace/baselines/{TIMESTAMP}-baseline.json")
    p.add_argument("--tag", default=None,
                   help="Filename tag override (replaces timestamp portion).")
    p.add_argument("--per-cp-timeout", type=int, default=900,
                   help="Per-canonical timeout in seconds (default 900 = 15 min for n_runs=5).")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001")
        return 2

    if args.canonicals:
        targets = [s.strip() for s in args.canonicals.split(",") if s.strip()]
    elif args.all:
        targets = sorted(p.stem for p in (REPO_ROOT / "workspace/templates").glob("CP-*.json"))
    else:
        targets = list(PATCHED_SET)

    print(f"running {len(targets)} canonicals @ n_runs={args.n_runs} seed={args.seed}")
    print(f"target list: {', '.join(targets)}")
    print("-" * 78)

    results: List[Dict] = []
    suite_start = time.time()
    for label in targets:
        try:
            r = await asyncio.wait_for(_run_one(label, args.n_runs, args.seed),
                                       timeout=args.per_cp_timeout)
        except asyncio.TimeoutError:
            r = {"label": label, "verdict": f"TIMEOUT_{args.per_cp_timeout}"}
        except Exception as e:
            r = {"label": label, "verdict": "EXC", "err": str(e)[:100]}
        results.append(r)
        st = r.get("status") or r.get("verdict", "?")
        sr = r.get("success_rate")
        sr_s = f"{sr:.2f}" if sr is not None else "-"
        print(f"  {label:7s} {st:14s} rate={sr_s:>5s}  "
              f"runs={r.get('n_ok','?')}/{r.get('n_runs','?')}  "
              f"build={r.get('build','-'):>6s}  elapsed={r.get('elapsed_s','-')}s")

    print("-" * 78)
    total = time.time() - suite_start
    n_stable_ok = sum(1 for r in results if r.get("status") == "stable_ok")
    n_flaky    = sum(1 for r in results if r.get("status") == "flaky")
    n_fail     = sum(1 for r in results if r.get("status") == "stable_fail")
    n_other    = sum(1 for r in results if r.get("status") not in ("stable_ok", "flaky", "stable_fail"))
    print(f"summary: stable_ok={n_stable_ok}  flaky={n_flaky}  stable_fail={n_fail}  "
          f"other={n_other}  total={len(results)}  suite_time={total:.0f}s")

    out_dir = REPO_ROOT / "workspace/baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out)
    elif args.tag:
        out_path = out_dir / f"{args.tag}-baseline.json"
    else:
        out_path = out_dir / f"{datetime.now().strftime('%Y-%m-%d')}-baseline.json"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_runs": args.n_runs,
        "seed": args.seed,
        "n_canonicals": len(results),
        "summary": {
            "stable_ok": n_stable_ok,
            "flaky": n_flaky,
            "stable_fail": n_fail,
            "other": n_other,
        },
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
