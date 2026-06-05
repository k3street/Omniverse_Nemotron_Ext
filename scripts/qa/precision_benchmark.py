"""
precision_benchmark.py — measure cuRobo drop precision empirically.

Runs CP-28 (single-cube drop benchmark) N times and reports the
distribution of (cube_final - drop_target) across runs.

Usage:
    python scripts/qa/precision_benchmark.py             # 5 runs (default)
    python scripts/qa/precision_benchmark.py --runs 10
    python scripts/qa/precision_benchmark.py --canonical CP-28
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from statistics import mean, stdev

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402
from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call  # noqa: E402


RESET_CODE = """
import omni.usd
ctx = omni.usd.get_context()
ctx.new_stage()
from pxr import UsdGeom
UsdGeom.Xform.Define(ctx.get_stage(), '/World')
"""


async def run_once(template_path: Path, drop_target: list, target_path: str,
                    duration_s: int = 45) -> dict:
    """Build canonical, simulate, return cube_final + drift metrics."""
    await kit_tools.exec_sync(RESET_CODE, timeout=10)
    cp = json.loads(template_path.read_text())
    await execute_template_canonical(cp)
    await settle_after_canonical(cp)
    res = await execute_tool_call("simulate_traversal_check", {
        "cube_path": cp["simulate_args"]["cube_path"],
        "target_path": cp["simulate_args"]["target_path"],
        "duration_s": duration_s,
    })
    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        if line.strip().startswith("{") and "success" in line:
            try:
                parsed = json.loads(line)
                break
            except Exception:
                continue
    if parsed is None:
        return {"error": "no result parsed"}

    cube_final = parsed.get("cube_final")
    if not cube_final:
        return {"error": "no cube_final"}
    dx = cube_final[0] - drop_target[0]
    dy = cube_final[1] - drop_target[1]
    dz = cube_final[2] - drop_target[2]
    dist_xy = math.sqrt(dx * dx + dy * dy)
    return {
        "cube_final": [round(c, 4) for c in cube_final],
        "dx": round(dx, 4),
        "dy": round(dy, 4),
        "dz": round(dz, 4),
        "dist_xy": round(dist_xy, 4),
        "in_target_xy": parsed.get("in_target_xy"),
        "at_rest": parsed.get("at_rest"),
        "ctrl_done": parsed.get("ctrl_is_done", parsed.get("delivered_at_t")),
    }


def summarize(results: list, drop_target: list) -> str:
    valid = [r for r in results if "error" not in r]
    if not valid:
        return "All runs failed."
    lines = []
    lines.append(f"=== Precision benchmark (N={len(valid)}/{len(results)} runs) ===")
    lines.append(f"drop_target: {drop_target}")
    lines.append("")
    lines.append("Run | cube_final | dx | dy | dz | dist_xy | in_xy | at_rest")
    for i, r in enumerate(valid):
        lines.append(
            f"  {i+1:2d} | {r['cube_final']} | "
            f"{r['dx']:+.3f} | {r['dy']:+.3f} | {r['dz']:+.3f} | "
            f"{r['dist_xy']:.3f} | {r['in_target_xy']} | {r['at_rest']}"
        )
    if len(valid) >= 2:
        dxs = [r["dx"] for r in valid]
        dys = [r["dy"] for r in valid]
        dists = [r["dist_xy"] for r in valid]
        lines.append("")
        lines.append("Statistics:")
        lines.append(f"  dx:    mean={mean(dxs):+.3f}  std={stdev(dxs):.3f}  min={min(dxs):+.3f}  max={max(dxs):+.3f}")
        lines.append(f"  dy:    mean={mean(dys):+.3f}  std={stdev(dys):.3f}  min={min(dys):+.3f}  max={max(dys):+.3f}")
        lines.append(f"  dist:  mean={mean(dists):.3f}   std={stdev(dists):.3f}  min={min(dists):.3f}   max={max(dists):.3f}")
        in_xy_count = sum(1 for r in valid if r["in_target_xy"])
        lines.append(f"  in_target_xy: {in_xy_count}/{len(valid)} ({100*in_xy_count/len(valid):.0f}%)")
    return "\n".join(lines)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=5, help="Number of runs (default 5)")
    p.add_argument("--canonical", default="CP-28", help="Canonical id (default CP-28)")
    p.add_argument("--duration", type=int, default=45, help="Sim duration per run (s)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001")
        return 2

    template_path = REPO_ROOT / f"workspace/templates/{args.canonical}.json"
    if not template_path.exists():
        print(f"[FAIL] template not found: {template_path}")
        return 2

    cp = json.loads(template_path.read_text())
    # Look up drop_target — handle both top-level drop_target and per-cube drop_targets
    drop_target = None
    code = cp.get("code", "")
    # Heuristic: parse simulate_args target_path then look in template's
    # drop_targets dict for cube_path; fall back to common drop_target=[...]
    cube_path = cp["simulate_args"]["cube_path"]
    # Parse drop_targets / drop_target from code (best-effort regex)
    import re as _re
    m = _re.search(r"drop_target=\[([^\]]+)\]", code)
    if m:
        drop_target = [float(x.strip()) for x in m.group(1).split(",")]
    else:
        # Look for drop_targets entry for cube_path
        m2 = _re.search(rf'"{_re.escape(cube_path)}":\s*\[([^\]]+)\]', code)
        if m2:
            drop_target = [float(x.strip()) for x in m2.group(1).split(",")]
    if drop_target is None:
        print(f"[WARN] Could not parse drop_target for {cube_path} from code; defaulting to [0, -0.4, 0.825]")
        drop_target = [0.0, -0.4, 0.825]

    print(f"Running {args.runs} iterations of {args.canonical} (duration {args.duration}s each)...")
    print(f"Cube: {cube_path}, drop_target: {drop_target}")
    print()

    results = []
    for i in range(args.runs):
        print(f"  Run {i+1}/{args.runs}...", flush=True)
        r = await run_once(template_path, drop_target, cp["simulate_args"]["target_path"], args.duration)
        results.append(r)

    print()
    print(summarize(results, drop_target))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
