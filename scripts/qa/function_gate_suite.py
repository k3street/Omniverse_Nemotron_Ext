"""
function_gate_suite.py — exercises simulate_traversal_check on all
production canonicals to confirm each one actually DELIVERS its cube
to its bin under physics simulation.

Companion to verify_pickplace_pipeline (form-gate). Where form-gate
says "the scene matches the structural blueprint", function-gate says
"the cube actually arrives at the destination". Together they give
the dual-gate verification described in
docs/specs/2026-05-08-harness-layers-and-failure-modes.md.

Built 2026-05-07 after fixing the BBoxCache stale-read bug (commit
8025170) and adding stale-subscription cleanup at install time
(commit 755effb). Pre-fix, this script reported every canonical as
non-delivering — function-gate returned cube_speed=0 and cube_final
= cube_initial regardless of actual sim behavior.

Usage:
    python scripts/qa/function_gate_suite.py               # all 5
    python scripts/qa/function_gate_suite.py --only CP-01

Requires: Kit RPC alive at 127.0.0.1:8001 with Isaac Assist extension.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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

# (label, template_path, cube_path, target_path, duration_s, extra_args)
SUITE: List[Tuple[str, str, str, str, int, Dict]] = [
    ("CP-01",
     "workspace/templates/CP-01.json",
     "/World/Cube_1", "/World/Bin", 45, {}),
    ("CP-02",
     "workspace/templates/CP-02.json",
     "/World/Cube_1", "/World/Bin", 45, {}),
    ("CP-03 red",
     "workspace/templates/CP-03.json",
     "/World/Cube_red", "/World/RedBin", 45, {}),
    ("CP-03 blue",
     "workspace/templates/CP-03.json",
     "/World/Cube_blue", "/World/BlueBin", 45, {}),
    ("CP-04",
     "workspace/templates/CP-04.json",
     "/World/Cube_1", "/World/Bin", 45, {}),
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


async def run_one(
    label: str, template_path: str, cube: str, target: str,
    duration_s: int = 45, extra: Optional[Dict] = None,
) -> Dict:
    await _reset_scene()
    template = json.loads((REPO_ROOT / template_path).read_text())
    build_res = await execute_template_canonical(template)
    if not build_res.get("instantiated"):
        return {"label": label, "verdict": "BUILD_FAILED",
                "errors": build_res.get("errors", [])[:3]}
    await settle_after_canonical(template)

    args = {"cube_path": cube, "target_path": target, "duration_s": duration_s}
    if extra:
        args.update(extra)
    res = await execute_tool_call("simulate_traversal_check", args)
    out = (res.get("output") or "").strip()
    json_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        return {"label": label, "verdict": "NO_RESULT",
                "build": f"{build_res.get('n_ok')}/{build_res.get('n_calls')}"}
    d = json.loads(json_lines[-1])
    return {
        "label": label,
        "build": f"{build_res.get('n_ok')}/{build_res.get('n_calls')}",
        "success": d.get("success"),
        "in_xy": d.get("in_target_xy"),
        "above_floor": d.get("above_floor"),
        "at_rest": d.get("at_rest"),
        "speed": round(d.get("cube_speed", 0), 3),
        "final": [round(c, 3) for c in d["cube_final"]] if d.get("cube_final") else None,
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=None,
                   help="Run only the canonical with this label (e.g., 'CP-01')")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001")
        return 2

    suite = SUITE
    if args.only:
        suite = [s for s in SUITE if s[0] == args.only]
        if not suite:
            print(f"[FAIL] no canonical labelled {args.only!r}")
            return 2

    rows: List[Dict] = []
    for label, tpath, cube, target, dur, extra in suite:
        print(f"  running {label}...")
        try:
            r = await run_one(label, tpath, cube, target, duration_s=dur, extra=extra)
        except Exception as e:
            r = {"label": label, "verdict": f"EXC: {str(e)[:80]}"}
        rows.append(r)

    # Render summary
    print()
    print(f"{'label':<13} {'build':<7} {'success':<8} {'final':<32} {'in_xy':<7} {'speed':<6}")
    print("-" * 80)
    n_pass = 0
    for r in rows:
        if r.get("success") is True:
            n_pass += 1
        print(f"{r.get('label', '?'):<13} "
              f"{str(r.get('build', '?')):<7} "
              f"{str(r.get('success', r.get('verdict', '?'))):<8} "
              f"{str(r.get('final', '?')):<32} "
              f"{str(r.get('in_xy', '?')):<7} "
              f"{str(r.get('speed', '?')):<6}")
    print()
    print(f"Result: {n_pass}/{len(rows)} canonicals delivered")
    return 0 if n_pass == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
