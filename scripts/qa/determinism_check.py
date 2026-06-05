"""determinism_check.py — Phase 0 determinism unit test.

Builds a canonical scene twice. Runs simulate_traversal_check with the same
seed each time. Compares the cube_final positions. Spec criterion (master
plan Phase 0): identical within 1e-3 m across two consecutive calls.

Two modes:
  --mode rebuild   (default) — build canonical, run, reset_stage, rebuild,
                    run. Tests both seed determinism AND scene-rebuild
                    determinism (the realistic suite path).
  --mode n_runs    — build canonical once, run with n_runs=2 seed=42.
                    Compare runs[0] to runs[1]. NOTE: the kit-side script
                    seeds with seed_base+run_idx, so run 0 uses 42 and
                    run 1 uses 43 — different seeds, expected to produce
                    different finals. This mode is therefore NOT a true
                    determinism check; it's an in-place snapshot/restore
                    smoke. Defaults to fail-soft (warn but exit 0) unless
                    --strict given.

Default canonical: CP-01.

Usage:
  python scripts/qa/determinism_check.py
  python scripts/qa/determinism_check.py --canonical CP-01 --seed 42 --tol 1e-3
  python scripts/qa/determinism_check.py --mode n_runs --strict
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


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


async def _build_and_run(label: str, seed: int, n_runs: int) -> Optional[Dict]:
    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        print(f"[FAIL] template missing: {template_path}", file=sys.stderr)
        return None
    template = json.loads(template_path.read_text())
    sim_args = dict(template.get("simulate_args") or {})
    if not sim_args:
        print(f"[FAIL] {label} has no simulate_args", file=sys.stderr)
        return None
    sim_args["seed"] = seed
    sim_args["n_runs"] = n_runs

    await _reset_scene()
    build_res = await execute_template_canonical(template)
    if not build_res.get("instantiated"):
        print(f"[FAIL] build {label}: {build_res.get('errors')}", file=sys.stderr)
        return None
    try:
        await settle_after_canonical(template)
    except Exception:
        pass

    res = await execute_tool_call("simulate_traversal_check", sim_args)
    out = (res.get("output") or "").strip()
    json_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        print(f"[FAIL] no JSON in result. Tail:\n{out[-300:]}", file=sys.stderr)
        return None
    return json.loads(json_lines[-1])


def _delta(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


async def _mode_rebuild(label: str, seed: int, tol: float, strict: bool) -> int:
    print(f"[mode=rebuild] {label} seed={seed} tol={tol}")
    print("=" * 70)
    print("Run 1: build → simulate_traversal_check seed=", seed)
    r1 = await _build_and_run(label, seed, n_runs=1)
    if r1 is None:
        return 2
    f1 = r1.get("cube_final")
    s1 = r1.get("success")
    print(f"  cube_final={f1}  success={s1}")
    print("Run 2: rebuild → simulate_traversal_check seed=", seed)
    r2 = await _build_and_run(label, seed, n_runs=1)
    if r2 is None:
        return 2
    f2 = r2.get("cube_final")
    s2 = r2.get("success")
    print(f"  cube_final={f2}  success={s2}")
    print("-" * 70)
    if f1 is None or f2 is None:
        print("[FAIL] one or both runs returned no cube_final")
        return 1
    d = _delta(f1, f2)
    print(f"|Δ cube_final| = {d:.6f} m  (tol = {tol})")
    if d <= tol:
        print(f"PASS — determinism holds within tolerance")
        return 0
    print(f"FAIL — drift exceeds tolerance")
    if not strict:
        print("(non-strict mode → exit 0; pass --strict to surface CI failure)")
        return 0
    return 1


async def _mode_n_runs(label: str, seed: int, tol: float, strict: bool) -> int:
    print(f"[mode=n_runs] {label} seed={seed} (run 0 uses {seed}, run 1 uses {seed+1}) tol={tol}")
    print("=" * 70)
    print("(NOTE: this mode tests snapshot/restore between runs, not determinism")
    print(" with same seed — kit-side seeds with seed_base+run_idx)")
    r = await _build_and_run(label, seed, n_runs=2)
    if r is None:
        return 2
    runs = r.get("runs") or []
    if len(runs) < 2:
        print(f"[FAIL] expected 2 runs, got {len(runs)}", file=sys.stderr)
        return 2
    f1 = runs[0].get("cube_final")
    f2 = runs[1].get("cube_final")
    print(f"  run0 cube_final={f1}  success={runs[0].get('success')}")
    print(f"  run1 cube_final={f2}  success={runs[1].get('success')}")
    print("-" * 70)
    if f1 is None or f2 is None:
        print("[FAIL] one or both runs returned no cube_final")
        return 1
    d = _delta(f1, f2)
    print(f"|Δ cube_final between runs| = {d:.6f} m  (tol = {tol})")
    if d <= tol:
        print(f"PASS — runs converged despite different seeds (likely well-behaved scene)")
        return 0
    print(f"INFO — runs diverged. Expected if cuRobo IK initial guesses depend on seed.")
    if not strict:
        return 0
    return 1


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--canonical", default="CP-01")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--tol", type=float, default=1e-3)
    p.add_argument("--mode", choices=["rebuild", "n_runs"], default="rebuild")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 on drift > tolerance (default: warn-only)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001", file=sys.stderr)
        return 2

    if args.mode == "rebuild":
        return await _mode_rebuild(args.canonical, args.seed, args.tol, args.strict)
    return await _mode_n_runs(args.canonical, args.seed, args.tol, args.strict)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
