"""
function_gate_multi_cube.py — stress-test delivery rate per canonical
by tracking ALL cubes (not just Cube_1) and reporting per-canonical
N_delivered / N_total.

Why this matters: function_gate_suite checks one cube per canonical.
CP-01 + CP-04 have 4-5 cubes; passing the suite proves Cube_1 was
delivered, NOT that the controller can sustain a multi-cube cycle.
A controller that delivers Cube_1 perfectly but stalls before Cube_2
would still pass the existing suite — undetected partial-success.

Method: build canonical → settle → play for N seconds → sample every
cube's final position → check if in its expected bin xy. For CP-03,
red cubes expected in RedBin and blue cubes in BlueBin.

Usage:
  python scripts/qa/function_gate_multi_cube.py
  python scripts/qa/function_gate_multi_cube.py --duration 90
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
from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


CANONICALS = [
    {
        "label": "CP-01",
        "template": "workspace/templates/CP-01.json",
        "routing": [{"target": "/World/Bin", "match": lambda path: True}],
    },
    {
        "label": "CP-02",
        "template": "workspace/templates/CP-02.json",
        "routing": [{"target": "/World/Bin", "match": lambda path: True}],
    },
    {
        "label": "CP-03",
        "template": "workspace/templates/CP-03.json",
        "routing": [
            {"target": "/World/RedBin", "match": lambda path: "red" in path.lower()},
            {"target": "/World/BlueBin", "match": lambda path: "blue" in path.lower()},
        ],
    },
    {
        "label": "CP-04",
        "template": "workspace/templates/CP-04.json",
        "routing": [{"target": "/World/Bin", "match": lambda path: True}],
    },
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


async def _multi_cube_track(duration_s: int) -> Dict:
    """Play timeline for duration_s, then return all cube positions
    + bin bboxes. Run as one Kit RPC call to avoid per-cube setup
    overhead."""
    code = f"""
import omni.usd, omni.timeline, omni.kit.app, json, time as _t
from pxr import UsdGeom, Usd

stage = omni.usd.get_context().get_stage()

def world_pos(path):
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid(): return None
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty(): return None
    c = b.GetMidpoint()
    return [float(c[0]), float(c[1]), float(c[2])]

def world_bbox(path):
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid(): return None
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty(): return None
    mn = b.GetMin(); mx = b.GetMax()
    return {{
        'min': [float(mn[0]), float(mn[1]), float(mn[2])],
        'max': [float(mx[0]), float(mx[1]), float(mx[2])],
    }}

# Discover all /World/Cube* prims (cubes are typed Cube; sometimes Xform with Cube children)
cube_paths = []
for p in stage.Traverse():
    path = str(p.GetPath())
    typename = str(p.GetTypeName())
    if typename == 'Cube' and '/World/' in path and 'Cube' in path:
        cube_paths.append(path)
        continue
    # CP-03 paths like /World/Cube_red are Cube type with name match
    if 'Cube' in path.split('/')[-1] and len(path.split('/')) <= 3:
        if path not in cube_paths:
            # Skip the conveyor — also a Cube prim_type but shouldn't be tracked
            if 'Conveyor' in path or 'Belt' in path:
                continue
            cube_paths.append(path)

# Discover bin paths
bin_paths = []
for p in stage.Traverse():
    path = str(p.GetPath())
    if 'Bin' in path.split('/')[-1] and len(path.split('/')) <= 3:
        bin_paths.append(path)

initial = {{c: world_pos(c) for c in cube_paths}}

tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()
tl.stop()
tl.set_current_time(0.0)
tl.play()

real_start = _t.time()
while True:
    app.update()
    cur_t = float(tl.get_current_time())
    if cur_t >= {duration_s}: break
    if _t.time() - real_start > {duration_s} + 60: break

final = {{c: world_pos(c) for c in cube_paths}}
bins = {{b: world_bbox(b) for b in bin_paths}}
tl.stop()

print(json.dumps({{'cubes': cube_paths, 'initial': initial, 'final': final, 'bins': bins}}))
"""
    res = await kit_tools.exec_sync(code, timeout=duration_s + 90)
    out = (res.get("output") or "").strip()
    for line in out.splitlines():
        if line.strip().startswith('{') and 'cubes' in line:
            return json.loads(line)
    return {"error": "no result", "raw": out[:500]}


def _check_cube_in_bin(
    cube_pos: Optional[List[float]],
    bin_bbox: Optional[Dict],
    xy_tol: float = 0.05,
    floor_tol: float = 0.10,
) -> bool:
    """True if cube is in bin xy + above floor (per simulate_traversal_check logic)."""
    if cube_pos is None or bin_bbox is None:
        return False
    in_xy = (
        bin_bbox["min"][0] - xy_tol <= cube_pos[0] <= bin_bbox["max"][0] + xy_tol
        and bin_bbox["min"][1] - xy_tol <= cube_pos[1] <= bin_bbox["max"][1] + xy_tol
    )
    above_floor = cube_pos[2] >= bin_bbox["min"][2] - floor_tol
    return bool(in_xy and above_floor)


async def run_canonical(canonical: Dict, duration_s: int) -> Dict:
    """Build, settle, play, sample all cubes."""
    label = canonical["label"]
    await _reset_scene()
    template = json.loads((REPO_ROOT / canonical["template"]).read_text())
    build = await execute_template_canonical(template)
    if not build.get("instantiated"):
        return {"label": label, "verdict": "BUILD_FAILED"}
    await settle_after_canonical(template)

    track = await _multi_cube_track(duration_s)
    if "error" in track:
        return {"label": label, "verdict": "TRACK_FAILED", "error": track["error"]}

    cubes = track.get("cubes") or []
    finals = track.get("final") or {}
    bins = track.get("bins") or {}
    routing = canonical["routing"]

    per_cube: List[Dict] = []
    for c in cubes:
        # Determine expected bin via routing rules
        target = None
        for rule in routing:
            if rule["match"](c):
                target = rule["target"]
                break
        bbox = bins.get(target) if target else None
        delivered = _check_cube_in_bin(finals.get(c), bbox)
        per_cube.append({
            "cube": c,
            "target": target,
            "delivered": delivered,
            "final": finals.get(c),
        })

    n_total = len(per_cube)
    n_delivered = sum(1 for c in per_cube if c["delivered"])
    return {
        "label": label,
        "n_cubes": n_total,
        "n_delivered": n_delivered,
        "rate": n_delivered / n_total if n_total else 0,
        "per_cube": per_cube,
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=60,
                   help="Sim duration per canonical (default 60s)")
    p.add_argument("--only", default=None,
                   help="Run only this canonical label")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive")
        return 2

    suite = CANONICALS
    if args.only:
        suite = [c for c in CANONICALS if c["label"] == args.only]

    rows: List[Dict] = []
    for c in suite:
        print(f"  running {c['label']} (duration={args.duration}s)...")
        try:
            r = await run_canonical(c, args.duration)
        except Exception as e:
            r = {"label": c["label"], "verdict": f"EXC: {str(e)[:80]}"}
        rows.append(r)

    print()
    print(f"{'canonical':<10} {'cubes':<8} {'delivered':<10} {'rate':<7} per-cube detail")
    print("-" * 90)
    total_delivered = 0
    total_cubes = 0
    for r in rows:
        if "verdict" in r:
            print(f"{r['label']:<10} {r['verdict']}")
            continue
        rate = r["rate"]
        total_delivered += r["n_delivered"]
        total_cubes += r["n_cubes"]
        details = " ".join(
            f"{c['cube'].split('/')[-1][:8]}={'✓' if c['delivered'] else '✗'}"
            for c in r["per_cube"]
        )
        print(f"{r['label']:<10} {r['n_cubes']:<8} "
              f"{r['n_delivered']}/{r['n_cubes']:<7} {100*rate:>3.0f}%   {details}")
    print()
    if total_cubes:
        print(f"OVERALL: {total_delivered}/{total_cubes} cubes delivered "
              f"({100*total_delivered/total_cubes:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
