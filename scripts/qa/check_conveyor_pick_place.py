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
    """Franka under /World/ with ArticulationRootAPI AND ≥10 children (real
    model has many links) AND base Z between 0.65 and 0.85.

    Hardened 2026-04-19: previous check only required ArticulationRootAPI,
    which falsely passed on empty Xforms where the USD reference had
    404'd at composition time. A real Franka Panda has ~15 link prims
    (panda_link0 ... panda_link8 + gripper + fingers + joints subtree).
    Setting the threshold to ≥10 is conservative and catches the silent-
    load failure while tolerating minor hierarchy differences between
    asset versions.
    """
    script = """
import json
import omni.usd
from pxr import UsdPhysics, UsdGeom, Usd
stage = omni.usd.get_context().get_stage()
robots = []
for prim in stage.Traverse():
    p = prim.GetPath().pathString
    if not p.startswith("/World/"):
        continue
    if "PhysicsArticulationRootAPI" not in prim.GetAppliedSchemas():
        continue
    name_lower = p.lower()
    # Match by prim name OR by any descendant whose name contains
    # franka-specific tokens. quick_demo names the parent /World/Robot
    # and the real Franka links are panda_link0..7 as descendants, so
    # the parent alone wouldn't match "franka". Deep check handles both.
    self_match = any(k in name_lower for k in ("franka", "panda"))
    descendants = list(Usd.PrimRange(prim))[1:]
    descendant_match = any(
        any(k in d.GetPath().pathString.lower() for k in ("franka", "panda_link", "panda_hand"))
        for d in descendants
    )
    is_franka = self_match or descendant_match
    xf = UsdGeom.Xformable(prim)
    wt = xf.ComputeLocalToWorldTransform(0).ExtractTranslation() if xf else None
    robots.append({
        "path": p,
        "is_franka": is_franka,
        "base_z": wt[2] if wt else None,
        "descendant_count": len(descendants),
    })
franka = [r for r in robots if r["is_franka"]]
on_table_and_loaded = [r for r in franka
                       if r["base_z"] is not None
                       and 0.65 <= r["base_z"] <= 0.85
                       and r["descendant_count"] >= 10]
print(json.dumps({"ok": True, "robots": robots, "franka": franka,
                  "qualifying_count": len(on_table_and_loaded)}))
"""
    r = await run_kit(script)
    q = r.get("qualifying_count") or 0
    return CheckResult(
        id="C4", name="Franka imported on table with ≥10 descendant prims (verified load)",
        passed=q >= 1,
        details=f"Qualifying (on-table AND loaded): {q}; all Franka-like: {r.get('franka') or []}",
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
    """After 3s simulation, at least one cube's X changed by ≥ 0.1m.

    Uses manual omni.physx.update_simulation(dt) stepping instead of
    timeline.play + time.sleep. Verified 2026-04-19: inside exec_sync a
    falling-cube test dropped 0m under timeline.play + time.sleep(2),
    dropped 4.987m under 60 manual steps at dt=1/60. Manual stepping
    also fires physics-step callbacks installed by
    setup_pick_place_controller, so robot state machines run too."""
    script = """
import json
import omni.usd
import omni.physx
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
cubes = [p for p in stage.Traverse() if p.GetPath().pathString.startswith("/World/")
         and p.IsA(UsdGeom.Cube) and "bin" not in p.GetPath().pathString.lower()
         and "conveyor" not in p.GetPath().pathString.lower()
         and "belt" not in p.GetPath().pathString.lower()]
initial_x = {}
for c in cubes:
    wt = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
    initial_x[c.GetPath().pathString] = wt[0]
phx = omni.physx.get_physx_interface()
for i in range(180):
    phx.update_simulation(elapsedStep=1/60, currentTime=i*1/60)
phx.update_transformations(updateToFastCache=True, updateToUsd=True, updateVelocitiesToUsd=True)
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
    import omni.physx
    phx = omni.physx.get_physx_interface()
    min_dist = 1e9
    # 600 steps at dt=1/60 = 10s sim. Sample EE-to-cube distance every 15 steps (~0.25s).
    for i in range(600):
        phx.update_simulation(elapsedStep=1/60, currentTime=i*1/60)
        if i % 15 == 0:
            phx.update_transformations(updateToFastCache=True, updateToUsd=True, updateVelocitiesToUsd=True)
            ee_pos = UsdGeom.Xformable(ee).ComputeLocalToWorldTransform(0).ExtractTranslation()
            for c in cubes:
                cp = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
                d = (Gf.Vec3d(ee_pos) - Gf.Vec3d(cp)).GetLength()
                if d < min_dist:
                    min_dist = d
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
    import omni.physx
    phx = omni.physx.get_physx_interface()
    for i in range(1800):  # 30s at 60Hz
        phx.update_simulation(elapsedStep=1/60, currentTime=i*1/60)
    phx.update_transformations(updateToFastCache=True, updateToUsd=True, updateVelocitiesToUsd=True)
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

async def c0_scene_has_light() -> CheckResult:
    """At least one UsdLux light prim exists. Without a light the viewport
    is black regardless of how correct the geometry is. This is the #1
    'scene looks broken' miss (2026-04-19 scenario runs had 5/5 geometry
    checks pass but zero lights authored)."""
    script = """
import json
import omni.usd
stage = omni.usd.get_context().get_stage()
lights = []
for prim in stage.Traverse():
    t = str(prim.GetTypeName())
    # UsdLux types: DomeLight, DistantLight, SphereLight, RectLight, DiskLight, CylinderLight
    if "Light" in t and t != "":
        a = prim.GetAttribute("inputs:intensity")
        intensity = None
        if a and a.HasAuthoredValue():
            intensity = float(a.Get())
        lights.append({"path": prim.GetPath().pathString, "type": t, "intensity": intensity})
print(json.dumps({"ok": True, "lights": lights}))
"""
    r = await run_kit(script)
    lights = r.get("lights") or []
    passed = len(lights) >= 1
    return CheckResult(
        id="C0", name="Scene has at least one light (viewport not black)",
        passed=passed,
        details=f"Found {len(lights)} light(s): {lights}" if lights else "NO lights authored — viewport will be black",
        raw=r,
    )


STRUCTURAL_CHECKS = [
    c0_scene_has_light,
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


def analyze_trace(session_id: str = "default_session") -> Dict[str, Any]:
    """Pull recent tool-call patterns from the session trace.

    Reads the per-session JSONL and summarizes the LAST turn (from the
    most recent non-slash user_msg onward): which tools were called,
    which succeeded vs failed, whether the agent reached for the right
    high-level entry points (create_conveyor_track, lookup_product_spec,
    lookup_api_deprecation), and whether the reply itself contains any
    deprecated-4x imports that slipped past the inline validator.

    This is orthogonal to the structural checks — those verify the
    stage; this verifies agent BEHAVIOR. Both matter for scoring.
    """
    trace_path = REPO / "workspace" / "session_traces" / f"{session_id}.jsonl"
    if not trace_path.exists():
        return {"error": "no trace file"}
    events = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    # Slice from the most recent non-slash user_msg onward
    last_user = None
    for i, e in enumerate(events):
        if e.get("type") != "user_msg":
            continue
        txt = (e.get("payload") or {}).get("text", "") or ""
        if txt.lstrip().startswith("/"):
            continue
        last_user = i
    if last_user is None:
        return {"error": "no non-slash user_msg found"}
    turn_events = events[last_user:]

    tools_called: Dict[str, Dict[str, int]] = {}
    for e in turn_events:
        if e.get("type") != "tool_call":
            continue
        pl = e.get("payload") or {}
        name = pl.get("tool") or "?"
        ok = pl.get("success")
        d = tools_called.setdefault(name, {"total": 0, "success": 0, "fail": 0, "null": 0})
        d["total"] += 1
        if ok is True:
            d["success"] += 1
        elif ok is False:
            d["fail"] += 1
        else:
            d["null"] += 1

    patches = [e for e in turn_events if e.get("type") == "patch_executed"]
    patch_success = sum(1 for p in patches if (p.get("payload") or {}).get("success") is True)
    patch_fail = sum(1 for p in patches if (p.get("payload") or {}).get("success") is False)

    # Reply text scan for deprecated-4x tells (agent posted code the user
    # might copy-paste even though it's broken).
    reply_text = ""
    for e in reversed(turn_events):
        if e.get("type") == "agent_reply":
            reply_text = (e.get("payload") or {}).get("text", "") or ""
            break
    deprecated_mentions = []
    for token in ("omni.isaac.franka", "omni.isaac.core", "omni.isaac.ros2_bridge",
                  "omni.isaac.urdf", "omni.isaac.kit"):
        if token in reply_text:
            deprecated_mentions.append(token)

    multi_step = any(e.get("type") == "multi_step_detected" for e in turn_events)
    retry_halt = any(e.get("type") == "retry_spam_halt" for e in turn_events)
    thought_count = sum(1 for e in turn_events if e.get("type") == "agent_thought")
    turn_diff = None
    for e in reversed(turn_events):
        if e.get("type") == "turn_diff_computed":
            turn_diff = e.get("payload")
            break

    expected_high_level = {"create_conveyor_track", "lookup_product_spec",
                           "lookup_api_deprecation", "create_conveyor"}
    used_high_level = sorted(expected_high_level & set(tools_called))
    missed_high_level = sorted(expected_high_level - set(tools_called))

    return {
        "turn_event_count": len(turn_events),
        "tools_called": tools_called,
        "patch_success": patch_success,
        "patch_fail": patch_fail,
        "multi_step_detected": multi_step,
        "retry_spam_halt": retry_halt,
        "agent_thought_count": thought_count,
        "turn_diff": turn_diff,
        "high_level_tools_used": used_high_level,
        "high_level_tools_missed": missed_high_level,
        "deprecated_4x_in_reply": deprecated_mentions,
    }


def print_trace_analysis(info: Dict[str, Any]) -> None:
    print()
    print("=" * 70)
    print("TRACE / BEHAVIOR ANALYSIS")
    print("=" * 70)
    if "error" in info:
        print(f"  (no trace: {info['error']})")
        return
    tc = info.get("tools_called") or {}
    print(f"  Tool calls: {sum(d['total'] for d in tc.values())} total across {len(tc)} distinct tools")
    for name, d in sorted(tc.items(), key=lambda x: -x[1]["total"]):
        print(f"    {name}: total={d['total']} ok={d['success']} fail={d['fail']} pending={d['null']}")
    print(f"  patch_executed: {info.get('patch_success',0)} ok / {info.get('patch_fail',0)} fail")
    print(f"  multi_step_detected: {info.get('multi_step_detected')}")
    print(f"  retry_spam_halt: {info.get('retry_spam_halt')}")
    print(f"  agent_thought parts: {info.get('agent_thought_count')}")
    if info.get("turn_diff"):
        print(f"  turn_diff: {info['turn_diff']}")
    print(f"  high-level tools used:   {info.get('high_level_tools_used')}")
    print(f"  high-level tools missed: {info.get('high_level_tools_missed')}")
    if info.get("deprecated_4x_in_reply"):
        print(f"  ⚠️ deprecated 4.x in reply text: {info['deprecated_4x_in_reply']}")


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
    trace_info = analyze_trace()
    print_trace_analysis(trace_info)
    out = save_result(results, include_dynamic=args.dynamic)
    # Also persist the trace analysis alongside the structural scores.
    if "error" not in trace_info:
        trace_out = out.with_suffix(".trace.json")
        trace_out.write_text(json.dumps(trace_info, indent=2), encoding="utf-8")
        print(f"Trace analysis: {trace_out.relative_to(REPO)}")
    print(f"\nResult saved: {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
