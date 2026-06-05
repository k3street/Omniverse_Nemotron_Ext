"""regression_post_patches.py — regression for patches applied 2026-05-09.

Reads each canonical's simulate_args + verify_args directly from the
template JSON and runs simulate_traversal_check after instantiation.
Reports per-canonical verdict so we can compare before/after the
multi-robot, UR10 builtin, and edge-case patches.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.canonical_instantiator import (  # noqa: E402
    execute_template_canonical, settle_after_canonical,
)
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402

# Canonicals we patched. Each entry uses the template's own simulate_args.
# Multi-robot: CP-51, 52, 53, 67, 68, 76. UR10 builtin: CP-74, 80, 84, 85.
# Edge: CP-22, 37, 58, 65, 73. Vision/special: CP-40, 59, 60.
TARGETS = [
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


async def run_one(label: str) -> Dict:
    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        return {"label": label, "verdict": "TEMPLATE_NOT_FOUND"}
    template = json.loads(template_path.read_text())
    sim_args = template.get("simulate_args", {})
    if not sim_args:
        return {"label": label, "verdict": "NO_SIMULATE_ARGS"}

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

    res = await execute_tool_call("simulate_traversal_check", sim_args)
    out = (res.get("output") or "").strip()
    json_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        return {"label": label, "verdict": "NO_RESULT",
                "tail": out[-200:]}
    d = json.loads(json_lines[-1])
    final = d.get("cube_final")
    return {
        "label": label,
        "build": f"{build_res.get('n_ok')}/{build_res.get('n_calls')}",
        "success": d.get("success"),
        "in_xy": d.get("in_target_xy"),
        "above": d.get("above_floor"),
        "rest": d.get("at_rest"),
        "speed": round(d.get("cube_speed", 0), 3),
        "final": [round(c, 3) for c in final] if final else None,
        "target_bbox": d.get("target_bbox"),
        "per_cube": d.get("per_cube_status"),
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=None,
                   help="Run only one canonical (e.g. CP-22)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001")
        return 2

    targets = [args.only] if args.only else TARGETS
    print(f"running {len(targets)} canonicals: {', '.join(targets)}")
    print("-" * 72)

    results: List[Dict] = []
    for label in targets:
        try:
            r = await asyncio.wait_for(run_one(label), timeout=420)
        except asyncio.TimeoutError:
            r = {"label": label, "verdict": "TIMEOUT_420"}
        except Exception as e:
            r = {"label": label, "verdict": "EXC", "err": str(e)[:100]}
        results.append(r)
        # Concise per-canonical line
        if r.get("success") is True:
            mark = "OK"
        elif r.get("success") is False:
            mark = "FAIL"
        else:
            mark = r.get("verdict", "?")
        print(f"  {label:7s} {mark:6s}  build={r.get('build','-'):6s}  "
              f"in_xy={r.get('in_xy','-')}  above={r.get('above','-')}  "
              f"rest={r.get('rest','-')}  spd={r.get('speed','-')}")

    print("-" * 72)
    n_ok = sum(1 for r in results if r.get("success") is True)
    print(f"summary: {n_ok}/{len(results)} delivered")
    out_path = REPO_ROOT / f"data/regression_post_patches_{Path(__file__).stem}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
