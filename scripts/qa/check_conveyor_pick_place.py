"""Verify-check runner for the conveyor_pick_place scenario.

Usage:
    python scripts/qa/check_conveyor_pick_place.py [--dynamic]

Reads the live Kit stage via the service's Kit RPC and runs the 8
verify-checks documented in docs/qa/scenarios/conveyor_pick_place.md.
Prints a pass/fail summary and a final score.

``--dynamic`` enables the three dynamic checks (C6/C7/C8) which depend
on running the simulation for 3+/10+/30+ seconds and sampling cube
positions over time. Without the flag only the 5 structural checks
(C1-C5) run — fast, and sufficient for "did agent set up the cell?".

Design note — why a standalone script (not a pytest): the checks need
the live Isaac Sim stage, which means Kit RPC, which means this can't
run in L0/L1 test tiers. Also, rerun value is high: after every agent
attempt the user runs the script manually, captures the score, iterates.
Making it a script makes that loop obvious.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the service package importable
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Check primitives — run arbitrary Python inside Kit via exec_sync,
# parse the last JSON-line of stdout, return a {ok, value, ...} dict.
# ──────────────────────────────────────────────────────────────────────

async def run_kit(script: str) -> Dict[str, Any]:
    """Send a script to Kit and parse its last JSON line."""
    rpc = await kit_tools.exec_sync(script, timeout=30)
    if not rpc.get("success"):
        return {"ok": False, "error": f"kit exec failed: {rpc.get('output', '')[:300]}"}
    out = (rpc.get("output") or "").strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except Exception:
            continue
    return {"ok": False, "error": f"no JSON line in output: {out[:200]}"}


# ──────────────────────────────────────────────────────────────────────
# Check definitions — one per verify-check in the scenario spec.
# Each check returns CheckResult(passed, details).
# ──────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    id: str
    name: str
    passed: bool
    details: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


async def c1_table_exists() -> CheckResult:
    """A prim with bbox top between Z=0.7 and Z=0.8 under /World/."""
    script = """
import json
import omni.usd
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
candidates = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    if prim.IsA(UsdGeom.Imageable):
        bb = UsdGeom.Imageable(prim).ComputeWorldBound(0, UsdGeom.Tokens.default_)
        r = bb.ComputeAlignedRange()
        top_z = r.GetMax()[2]
        if 0.6 <= top_z <= 0.9:
            candidates.append({"path": p, "top_z": top_z, "type": prim.GetTypeName()})
print(json.dumps({"ok": True, "candidates": candidates}))
"""
    r = await run_kit(script)
    candidates = r.get("candidates") or []
    passed = len(candidates) >= 1
    return CheckResult(
        id="C1", name="Table exists (top Z≈0.75)",
        passed=passed,
        details=f"Found {len(candidates)} candidate(s): {candidates[:3]}",
        raw=r,
    )


async def c2_belt_surface_velocity() -> CheckResult:
    """A prim with Collision + kinematic RigidBody + SurfaceVelocityAPI + non-zero velocity."""
    script = """
import json
import omni.usd
from pxr import UsdPhysics, PhysxSchema
stage = omni.usd.get_context().get_stage()
belts = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    applied = prim.GetAppliedSchemas()
    has_col = "PhysicsCollisionAPI" in applied
    has_rb = "PhysicsRigidBodyAPI" in applied
    has_sv = "PhysxSurfaceVelocityAPI" in applied
    if not (has_col and has_rb and has_sv):
        continue
    # Kinematic flag
    kin_attr = prim.GetAttribute("physics:kinematicEnabled")
    kin = kin_attr.Get() if kin_attr else False
    # Surface velocity non-zero
    sv_attr = prim.GetAttribute("physxSurfaceVelocity:surfaceVelocity")
    sv = tuple(sv_attr.Get()) if sv_attr else (0, 0, 0)
    sv_enabled_attr = prim.GetAttribute("physxSurfaceVelocity:surfaceVelocityEnabled")
    sv_en = sv_enabled_attr.Get() if sv_enabled_attr else False
    sv_nonzero = any(abs(v) > 1e-6 for v in sv)
    belts.append({
        "path": p, "kinematic": kin, "velocity": list(sv),
        "enabled": sv_en, "velocity_nonzero": sv_nonzero,
    })
passing = [b for b in belts if b["kinematic"] and b["velocity_nonzero"] and b["enabled"]]
print(json.dumps({"ok": True, "all_belts": belts, "passing": passing}))
"""
    r = await run_kit(script)
    passing = r.get("passing") or []
    return CheckResult(
        id="C2", name="Belt has surface-velocity combo (kinematic RB + SVAPI + velocity)",
        passed=len(passing) >= 1,
        details=f"Passing belts: {passing or '(none)'}; all: {r.get('all_belts') or []}",
        raw=r,
    )


async def c3_cubes_on_belt() -> CheckResult:
    """≥4 Cube prims sitting on belt top (Z ~0.80-0.90), small mass, small size."""
    script = """
import json
import omni.usd
from pxr import UsdGeom, UsdPhysics
stage = omni.usd.get_context().get_stage()
cubes = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    if not prim.IsA(UsdGeom.Cube):
        continue
    # World bbox bottom
    bb = UsdGeom.Imageable(prim).ComputeWorldBound(0, UsdGeom.Tokens.default_)
    r = bb.ComputeAlignedRange()
    bottom_z = r.GetMin()[2]
    size_world = r.GetMax()[2] - r.GetMin()[2]
    # Mass if present
    mass_api = UsdPhysics.MassAPI(prim)
    mass = mass_api.GetMassAttr().Get() if mass_api.GetMassAttr() else None
    on_belt = 0.75 <= bottom_z <= 0.95
    small = size_world <= 0.08
    cubes.append({
        "path": p, "bottom_z": bottom_z, "size_world": size_world,
        "mass": mass, "on_belt": on_belt, "small": small,
    })
qualifying = [c for c in cubes if c["on_belt"] and c["small"]]
print(json.dumps({"ok": True, "cubes": cubes, "qualifying_count": len(qualifying)}))
"""
    r = await run_kit(script)
    count = r.get("qualifying_count") or 0
    return CheckResult(
        id="C3", name="≥4 cubes on belt (size ≤ 0.08, on belt top surface)",
        passed=count >= 4,
        details=f"{count} qualifying of {len(r.get('cubes') or [])} total cubes",
        raw=r,
    )


async def c4_franka_imported() -> CheckResult:
    """Franka under /World/ with ArticulationRootAPI, base Z ≈ 0.75."""
    script = """
import json
import omni.usd
from pxr import UsdPhysics, UsdGeom
stage = omni.usd.get_context().get_stage()
robots = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    if "PhysicsArticulationRootAPI" not in prim.GetAppliedSchemas():
        continue
    name_lower = p.lower()
    is_franka = any(k in name_lower for k in ("franka", "panda"))
    xf = UsdGeom.Xformable(prim)
    wt = xf.ComputeLocalToWorldTransform(0).ExtractTranslation() if xf else None
    robots.append({
        "path": p,
        "is_franka": is_franka,
        "base_z": wt[2] if wt else None,
    })
franka = [r for r in robots if r["is_franka"]]
on_table = [r for r in franka if r["base_z"] is not None and 0.65 <= r["base_z"] <= 0.85]
print(json.dumps({"ok": True, "robots": robots, "franka": franka, "on_table_count": len(on_table)}))
"""
    r = await run_kit(script)
    on_table = r.get("on_table_count") or 0
    return CheckResult(
        id="C4", name="Franka imported on table (ArticulationRoot, base Z 0.65-0.85)",
        passed=on_table >= 1,
        details=f"Franka-like robots on table: {on_table}; all articulations: {r.get('robots') or []}",
        raw=r,
    )


async def c5_bin_structure() -> CheckResult:
    """A prim named bin/box/container with ≥5 collision-enabled children (floor + 4 walls)."""
    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
bins = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    name_lower = p.lower().rsplit("/", 1)[-1]
    if not any(k in name_lower for k in ("bin", "container", "tray")):
        continue
    children = list(prim.GetAllChildren())
    col_children = [c for c in children if "PhysicsCollisionAPI" in c.GetAppliedSchemas()]
    bins.append({
        "path": p,
        "child_count": len(children),
        "col_children": len(col_children),
    })
qualifying = [b for b in bins if b["col_children"] >= 5]
print(json.dumps({"ok": True, "bins": bins, "qualifying_count": len(qualifying)}))
"""
    r = await run_kit(script)
    q = r.get("qualifying_count") or 0
    return CheckResult(
        id="C5", name="Bin has ≥5 collision-enabled children",
        passed=q >= 1,
        details=f"Qualifying bins: {q}; found: {r.get('bins') or []}",
        raw=r,
    )


# ──────────────────────────────────────────────────────────────────────
# Dynamic checks — require timeline to be running. Run via --dynamic.
# ──────────────────────────────────────────────────────────────────────

async def c6_belt_moves_cubes() -> CheckResult:
    """After 3s simulation, at least one cube's X changed by ≥ 0.1m."""
    script = """
import json, time
import omni.usd
import omni.timeline
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
# Snapshot initial X positions of all /World/Cube* prims
cubes = [p for p in stage.Traverse() if p.GetPath().pathString.startswith("/World/")
         and p.IsA(UsdGeom.Cube) and "bin" not in p.GetPath().pathString.lower()
         and "conveyor" not in p.GetPath().pathString.lower()
         and "belt" not in p.GetPath().pathString.lower()]
initial_x = {}
for c in cubes:
    wt = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
    initial_x[c.GetPath().pathString] = wt[0]
# Play, wait 3s, stop, re-read
tl = omni.timeline.get_timeline_interface()
tl.play()
time.sleep(3.0)
tl.stop()
final_x = {}
for c in cubes:
    wt = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
    final_x[c.GetPath().pathString] = wt[0]
deltas = {p: final_x[p] - initial_x[p] for p in initial_x}
moved = {p: d for p, d in deltas.items() if abs(d) >= 0.1}
print(json.dumps({"ok": True, "deltas": deltas, "moved_count": len(moved)}))
"""
    r = await run_kit(script)
    moved = r.get("moved_count") or 0
    return CheckResult(
        id="C6", name="Belt moves cubes ≥ 0.1m in 3s",
        passed=moved >= 1,
        details=f"{moved} cubes moved ≥ 0.1m; deltas: {r.get('deltas') or {}}",
        raw=r,
    )


async def c7_robot_reaches_cube() -> CheckResult:
    """After 10s, end-effector has been within 0.1m of any cube."""
    script = """
import json, time
import omni.usd
import omni.timeline
from pxr import UsdGeom, Gf
stage = omni.usd.get_context().get_stage()

def find_ee():
    for prim in stage.Traverse():
        nm = prim.GetName().lower()
        if any(k in nm for k in ("panda_hand", "tcp", "end_effector", "gripper_center")):
            return prim
    return None

def cube_paths():
    out = []
    for p in stage.Traverse():
        pp = p.GetPath().pathString
        if not pp.startswith("/World/"):
            continue
        if not p.IsA(UsdGeom.Cube):
            continue
        if any(k in pp.lower() for k in ("bin", "conveyor", "belt", "table")):
            continue
        out.append(p)
    return out

ee = find_ee()
cubes = cube_paths()
if ee is None:
    print(json.dumps({"ok": True, "error": "no end-effector found", "min_dist": None}))
else:
    tl = omni.timeline.get_timeline_interface()
    tl.play()
    min_dist = 1e9
    for _ in range(40):  # 40 samples over ~10s
        time.sleep(0.25)
        ee_pos = UsdGeom.Xformable(ee).ComputeLocalToWorldTransform(0).ExtractTranslation()
        for c in cubes:
            cp = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
            d = (Gf.Vec3d(ee_pos) - Gf.Vec3d(cp)).GetLength()
            if d < min_dist:
                min_dist = d
    tl.stop()
    print(json.dumps({"ok": True, "min_dist": min_dist, "ee_path": str(ee.GetPath())}))
"""
    r = await run_kit(script)
    md = r.get("min_dist")
    passed = md is not None and md <= 0.1
    return CheckResult(
        id="C7", name="Robot EE within 0.1m of a cube during 10s play",
        passed=passed,
        details=f"min distance: {md}; EE: {r.get('ee_path')}",
        raw=r,
    )


async def c8_cube_in_bin() -> CheckResult:
    """After 30s simulation, ≥1 cube inside bin bbox."""
    script = """
import json, time
import omni.usd
import omni.timeline
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()

def find_bin():
    for prim in stage.Traverse():
        nm = prim.GetPath().pathString.lower().rsplit("/",1)[-1]
        if any(k in nm for k in ("bin", "container", "tray")):
            return prim
    return None

bin_prim = find_bin()
if bin_prim is None:
    print(json.dumps({"ok": True, "error": "no bin", "in_bin": 0}))
else:
    bb = UsdGeom.Imageable(bin_prim).ComputeWorldBound(0, UsdGeom.Tokens.default_)
    bin_range = bb.ComputeAlignedRange()
    b_min, b_max = bin_range.GetMin(), bin_range.GetMax()
    tl = omni.timeline.get_timeline_interface()
    tl.play()
    time.sleep(30.0)
    tl.stop()
    # count cubes inside bin bbox
    in_bin = 0
    cube_info = []
    for prim in stage.Traverse():
        pp = prim.GetPath().pathString
        if not pp.startswith("/World/") or not prim.IsA(UsdGeom.Cube):
            continue
        if any(k in pp.lower() for k in ("bin", "conveyor", "belt", "table")):
            continue
        wt = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0).ExtractTranslation()
        inside = (b_min[0] <= wt[0] <= b_max[0] and
                  b_min[1] <= wt[1] <= b_max[1] and
                  b_min[2] <= wt[2] <= b_max[2])
        if inside:
            in_bin += 1
        cube_info.append({"path": pp, "pos": list(wt), "inside": inside})
    print(json.dumps({"ok": True, "in_bin": in_bin, "cubes": cube_info}))
"""
    r = await run_kit(script)
    n = r.get("in_bin") or 0
    return CheckResult(
        id="C8", name="≥1 cube inside bin after 30s sim",
        passed=n >= 1,
        details=f"{n} cubes in bin; positions: {r.get('cubes') or []}",
        raw=r,
    )


# ──────────────────────────────────────────────────────────────────────
# Runner + CLI
# ──────────────────────────────────────────────────────────────────────

STRUCTURAL_CHECKS = [
    c1_table_exists,
    c2_belt_surface_velocity,
    c3_cubes_on_belt,
    c4_franka_imported,
    c5_bin_structure,
]
DYNAMIC_CHECKS = [
    c6_belt_moves_cubes,
    c7_robot_reaches_cube,
    c8_cube_in_bin,
]


async def run_all(include_dynamic: bool) -> List[CheckResult]:
    results = []
    for fn in STRUCTURAL_CHECKS:
        r = await fn()
        print(f"[{r.id}] {'PASS' if r.passed else 'FAIL'}: {r.name}")
        print(f"       {r.details[:200]}")
        results.append(r)
    if include_dynamic:
        for fn in DYNAMIC_CHECKS:
            print(f"[{fn.__name__}] running (may take up to 30s)...")
            r = await fn()
            print(f"[{r.id}] {'PASS' if r.passed else 'FAIL'}: {r.name}")
            print(f"       {r.details[:200]}")
            results.append(r)
    return results


def print_summary(results: List[CheckResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print()
    print("=" * 70)
    print(f"SCORE: {passed}/{total} checks passed")
    print("=" * 70)
    for r in results:
        marker = "✓" if r.passed else "✗"
        print(f"  {marker} [{r.id}] {r.name}")
    if passed == total:
        print("\n🎯 Perfect run — scenario fully satisfied.")
    elif passed >= total * 0.75:
        print("\n👍 Strong partial success.")
    elif passed >= total * 0.5:
        print("\n🟡 Half-passing — likely structural-only success.")
    else:
        print("\n🔴 Substantial gaps — investigate agent thoughts + tool choices.")


def save_result(results: List[CheckResult], include_dynamic: bool) -> Path:
    """Persist to workspace/scenario_results/ for history tracking."""
    out_dir = REPO / "workspace" / "scenario_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = "_dynamic" if include_dynamic else "_structural"
    path = out_dir / f"conveyor_pick_place_{ts}{suffix}.json"
    payload = {
        "scenario": "conveyor_pick_place",
        "timestamp": ts,
        "include_dynamic": include_dynamic,
        "score": f"{sum(1 for r in results if r.passed)}/{len(results)}",
        "checks": [
            {"id": r.id, "name": r.name, "passed": r.passed, "details": r.details}
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Run conveyor_pick_place verify-checks against the live stage.")
    ap.add_argument("--dynamic", action="store_true",
                    help="Run the 3 dynamic checks (requires Play-able stage; adds ~45s).")
    args = ap.parse_args()

    results = asyncio.run(run_all(include_dynamic=args.dynamic))
    print_summary(results)
    out = save_result(results, include_dynamic=args.dynamic)
    print(f"\nResult saved: {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
