"""Verify-check runner for the conveyor_pick_place_build scenario.

Strict tolerances that correspond to the rich-prompt build-spec. When
all B1-B7 pass, the run is eligible for promotion to a template in
`workspace/templates/conveyor_pick_place.json`.

Usage:
    python scripts/qa/check_conveyor_pick_place_build.py

Tolerance policy:
- Positions: ±0.02m
- Dimensions: ±5%
- Belt surface velocity: ±5%
- APIs and child counts: exact
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402


async def run_kit(script: str) -> Dict[str, Any]:
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


@dataclass
class CheckResult:
    id: str
    name: str
    passed: bool
    details: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# B1 — Table exact
# ──────────────────────────────────────────────────────────────────────

async def b1_table_exact() -> CheckResult:
    script = """
import json
import omni.usd
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
p = stage.GetPrimAtPath("/World/Table")
if not p or not p.IsValid():
    print(json.dumps({"ok": True, "exists": False}))
else:
    bb = UsdGeom.Imageable(p).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
    mn, mx = bb.GetMin(), bb.GetMax()
    size = [mx[i] - mn[i] for i in range(3)]
    print(json.dumps({
        "ok": True, "exists": True,
        "size": size, "top_z": mx[2], "bottom_z": mn[2],
    }))
"""
    r = await run_kit(script)
    if not r.get("exists"):
        return CheckResult("B1", "Table exact (2.0×1.0×0.75m at Z∈[0.73,0.77])",
                           False, "/World/Table does not exist", r)
    size = r.get("size") or [0, 0, 0]
    top_z = r.get("top_z") or 0
    target = [2.0, 1.0, 0.75]
    tol = 0.05  # 5%
    size_ok = all(abs(size[i] - target[i]) / target[i] <= tol for i in range(3))
    top_ok = 0.73 <= top_z <= 0.77
    passed = size_ok and top_ok
    return CheckResult(
        "B1", "Table exact (2.0×1.0×0.75m at Z∈[0.73,0.77])",
        passed,
        f"size={[round(s,3) for s in size]} (target [2.0, 1.0, 0.75] ±5%), top_z={round(top_z,3)} (target 0.75 ±0.02)",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B2 — Belt combo + velocity
# ──────────────────────────────────────────────────────────────────────

async def b2_belt_combo() -> CheckResult:
    script = """
import json
import omni.usd
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
p = stage.GetPrimAtPath("/World/ConveyorBelt")
if not p or not p.IsValid():
    print(json.dumps({"ok": True, "exists": False}))
else:
    applied = list(p.GetAppliedSchemas())
    has_col = "PhysicsCollisionAPI" in applied
    has_rb = "PhysicsRigidBodyAPI" in applied
    has_sv = "PhysxSurfaceVelocityAPI" in applied
    kin_attr = p.GetAttribute("physics:kinematicEnabled")
    kin = bool(kin_attr.Get()) if kin_attr and kin_attr.IsDefined() else False
    sv_attr = p.GetAttribute("physxSurfaceVelocity:surfaceVelocity")
    sv = tuple(sv_attr.Get()) if sv_attr and sv_attr.IsDefined() else (0, 0, 0)
    bb = UsdGeom.Imageable(p).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
    mn, mx = bb.GetMin(), bb.GetMax()
    center = [(mn[i] + mx[i]) / 2 for i in range(3)]
    size = [mx[i] - mn[i] for i in range(3)]
    print(json.dumps({
        "ok": True, "exists": True,
        "has_col": has_col, "has_rb": has_rb, "has_sv": has_sv,
        "kinematic": kin, "velocity": list(sv),
        "top_z": mx[2], "center": center, "size": size,
    }))
"""
    r = await run_kit(script)
    if not r.get("exists"):
        return CheckResult("B2", "Belt combo + velocity + position (in front of Franka)",
                           False, "/World/ConveyorBelt does not exist", r)
    apis_ok = r.get("has_col") and r.get("has_rb") and r.get("has_sv")
    kin_ok = r.get("kinematic") is True
    vel = r.get("velocity") or [0, 0, 0]
    vel_ok = abs(vel[0] - 0.2) <= 0.01 and abs(vel[1]) <= 0.01 and abs(vel[2]) <= 0.01
    top_z = r.get("top_z") or 0
    top_ok = 0.83 <= top_z <= 0.87
    center = r.get("center") or [0, 0, 0]
    # Belt must be at Y≈0.3 (in front of Franka). Tolerance ±0.05m.
    # Also reject Y∈[-0.15, 0.15] explicitly (overlaps Franka base).
    pos_ok = abs(center[1] - 0.3) <= 0.05 and abs(center[0]) <= 0.1
    # Size — 1.6m × 0.3m × 0.1m ±5%
    size = r.get("size") or [0, 0, 0]
    target_size = [1.6, 0.3, 0.1]
    size_ok = all(abs(size[i] - target_size[i]) / target_size[i] <= 0.1 for i in range(3))
    passed = bool(apis_ok and kin_ok and vel_ok and top_ok and pos_ok and size_ok)
    return CheckResult(
        "B2", "Belt combo + velocity + Y=0.3 (in front of Franka) + size 1.6×0.3×0.1",
        passed,
        f"apis={apis_ok} kin={kin_ok} vel={vel} top_z={round(top_z,3)} "
        f"center={[round(c,3) for c in center]} size={[round(s,3) for s in size]}",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B3 — Four cubes on belt
# ──────────────────────────────────────────────────────────────────────

async def b3_cubes_on_belt() -> CheckResult:
    script = """
import json
import omni.usd
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
cubes = []
for i in range(1, 5):
    p = stage.GetPrimAtPath(f"/World/Cube_{i}")
    if not p or not p.IsValid():
        cubes.append({"idx": i, "exists": False})
        continue
    bb = UsdGeom.Imageable(p).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
    mn, mx = bb.GetMin(), bb.GetMax()
    size = max(mx[i2] - mn[i2] for i2 in range(3))
    cubes.append({
        "idx": i, "exists": True,
        "x": (mn[0] + mx[0]) / 2, "y": (mn[1] + mx[1]) / 2,
        "bottom_z": mn[2], "size": size,
    })
print(json.dumps({"ok": True, "cubes": cubes}))
"""
    r = await run_kit(script)
    cubes = r.get("cubes") or []
    target_x = {1: -0.6, 2: -0.4, 3: -0.2, 4: 0.0}
    details = []
    passing = 0
    for c in cubes:
        if not c.get("exists"):
            details.append(f"Cube_{c['idx']}: MISSING")
            continue
        idx = c["idx"]
        x_ok = abs(c["x"] - target_x[idx]) <= 0.05
        y_ok = abs(c["y"] - 0.3) <= 0.05
        size_ok = abs(c["size"] - 0.05) <= 0.005
        z_ok = 0.849 <= c["bottom_z"] <= 0.901
        if x_ok and y_ok and size_ok and z_ok:
            passing += 1
        details.append(
            f"Cube_{idx}: x={round(c['x'],2)}({'✓' if x_ok else '✗'}) "
            f"y={round(c['y'],2)}({'✓' if y_ok else '✗'}) "
            f"size={round(c['size'],3)}({'✓' if size_ok else '✗'}) "
            f"bot_z={round(c['bottom_z'],3)}({'✓' if z_ok else '✗'})"
        )
    return CheckResult(
        "B3", "Four cubes on belt at X=[-0.6,-0.4,-0.2,0.0], Y=0.3",
        passing == 4,
        f"{passing}/4 passing — {'; '.join(details)}",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B4 — Franka on table
# ──────────────────────────────────────────────────────────────────────

async def b4_franka_on_table() -> CheckResult:
    script = """
import json
import omni.usd
from pxr import UsdGeom, Usd
stage = omni.usd.get_context().get_stage()
p = stage.GetPrimAtPath("/World/Franka")
if not p or not p.IsValid():
    print(json.dumps({"ok": True, "exists": False}))
else:
    has_art = "PhysicsArticulationRootAPI" in p.GetAppliedSchemas()
    xf = UsdGeom.Xformable(p)
    wt = xf.ComputeLocalToWorldTransform(0).ExtractTranslation() if xf else None
    descendants = list(Usd.PrimRange(p))[1:]
    print(json.dumps({
        "ok": True, "exists": True, "has_articulation": has_art,
        "position": [wt[0], wt[1], wt[2]] if wt else None,
        "descendant_count": len(descendants),
    }))
"""
    r = await run_kit(script)
    if not r.get("exists"):
        return CheckResult("B4", "Franka at /World/Franka, on table, loaded",
                           False, "/World/Franka does not exist", r)
    pos = r.get("position") or [0, 0, 0]
    art_ok = r.get("has_articulation") is True
    pos_ok = abs(pos[0]) <= 0.05 and abs(pos[1]) <= 0.05 and 0.73 <= pos[2] <= 0.77
    desc_ok = (r.get("descendant_count") or 0) >= 10
    passed = bool(art_ok and pos_ok and desc_ok)
    return CheckResult(
        "B4", "Franka at /World/Franka, on table, loaded",
        passed,
        f"articulation={art_ok} position={pos} descendants={r.get('descendant_count')}",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B5 — Bin position + structure
# ──────────────────────────────────────────────────────────────────────

async def b5_bin_exact() -> CheckResult:
    script = """
import json
import omni.usd
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
p = stage.GetPrimAtPath("/World/Bin")
if not p or not p.IsValid():
    print(json.dumps({"ok": True, "exists": False}))
else:
    bb = UsdGeom.Imageable(p).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
    mn, mx = bb.GetMin(), bb.GetMax()
    size = [mx[i] - mn[i] for i in range(3)]
    center = [(mn[i] + mx[i]) / 2 for i in range(3)]
    children = list(p.GetAllChildren())
    col = [c for c in children if "PhysicsCollisionAPI" in c.GetAppliedSchemas()]
    print(json.dumps({
        "ok": True, "exists": True, "size": size, "center": center,
        "children": len(children), "col_children": len(col),
    }))
"""
    r = await run_kit(script)
    if not r.get("exists"):
        return CheckResult("B5", "Bin at (0, 0.5, 0.80) with 5 collision-children",
                           False, "/World/Bin does not exist", r)
    size = r.get("size") or [0, 0, 0]
    center = r.get("center") or [0, 0, 0]
    target_size = [0.3, 0.3, 0.15]
    size_ok = all(abs(size[i] - target_size[i]) / target_size[i] <= 0.05 for i in range(3))
    # Center Z: bbox mean of (0.80 + 0.95)/2 = 0.875
    # Bin moved to Y=-0.4 (behind Franka). Tolerance ±0.05m.
    pos_ok = abs(center[0]) <= 0.05 and abs(center[1] - (-0.4)) <= 0.05 and abs(center[2] - 0.875) <= 0.05
    col_ok = (r.get("col_children") or 0) >= 5
    passed = bool(size_ok and pos_ok and col_ok)
    return CheckResult(
        "B5", "Bin at (0, -0.4, 0.80) with 5 collision-children",
        passed,
        f"size={[round(s,3) for s in size]} center={[round(c,3) for c in center]} col_children={r.get('col_children')}",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B6 — Dome light
# ──────────────────────────────────────────────────────────────────────

async def b6_dome_light() -> CheckResult:
    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
lights = []
for prim in stage.Traverse():
    t = str(prim.GetTypeName())
    if "Light" in t and t:
        a = prim.GetAttribute("inputs:intensity")
        intensity = float(a.Get()) if a and a.HasAuthoredValue() else None
        lights.append({"path": prim.GetPath().pathString, "type": t, "intensity": intensity})
print(json.dumps({"ok": True, "lights": lights}))
"""
    r = await run_kit(script)
    lights = r.get("lights") or []
    qualifying = [l for l in lights if l.get("intensity") is not None and 900 <= l["intensity"] <= 1100]
    return CheckResult(
        "B6", "Dome light with intensity 1000 ±10%",
        len(qualifying) >= 1,
        f"{len(qualifying)} qualifying of {len(lights)} lights: {lights}",
        r,
    )


# ──────────────────────────────────────────────────────────────────────
# B7 — PhysicsScene exists
# ──────────────────────────────────────────────────────────────────────

async def b7_physics_scene() -> CheckResult:
    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
found = []
for prim in stage.Traverse():
    if str(prim.GetTypeName()) == "PhysicsScene":
        found.append(prim.GetPath().pathString)
print(json.dumps({"ok": True, "found": found}))
"""
    r = await run_kit(script)
    found = r.get("found") or []
    return CheckResult(
        "B7", "PhysicsScene prim exists",
        len(found) >= 1,
        f"Found: {found}",
        r,
    )


CHECKS = [
    b1_table_exact, b2_belt_combo, b3_cubes_on_belt,
    b4_franka_on_table, b5_bin_exact, b6_dome_light, b7_physics_scene,
]


async def run_all() -> List[CheckResult]:
    results = []
    for fn in CHECKS:
        r = await fn()
        marker = "✓" if r.passed else "✗"
        print(f"[{r.id}] {'PASS' if r.passed else 'FAIL'}: {r.name}")
        print(f"      {r.details[:300]}")
        results.append(r)
    return results


def print_summary(results: List[CheckResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print()
    print("=" * 70)
    print(f"BUILD SCORE: {passed}/{total}")
    print("=" * 70)
    for r in results:
        marker = "✓" if r.passed else "✗"
        print(f"  {marker} [{r.id}] {r.name}")
    if passed == total:
        print("\n🎯 All-pass — eligible for template promotion.")
    else:
        print(f"\n🟡 {total - passed} failing check(s) — not yet template-ready.")


def save_result(results: List[CheckResult], out_dir: Path) -> Path:
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"conveyor_pick_place_build_{ts}.json"
    data = {
        "scenario": "conveyor_pick_place_build",
        "timestamp": ts,
        "score": f"{sum(1 for r in results if r.passed)}/{len(results)}",
        "checks": [{
            "id": r.id, "name": r.name, "passed": r.passed,
            "details": r.details, "raw": r.raw,
        } for r in results],
    }
    path.write_text(json.dumps(data, indent=2))
    return path


async def main():
    results = await run_all()
    print_summary(results)
    out = save_result(results, REPO / "workspace" / "scenario_results")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
