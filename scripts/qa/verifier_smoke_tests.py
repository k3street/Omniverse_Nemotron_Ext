#!/usr/bin/env python
"""
verifier_smoke_tests.py — regression boundary for verify_pickplace_pipeline.

Per docs/specs/2026-05-08-next-session-autonomous-plan.md Phase 0.3.

Exercises the verifier against three fixtures:
  - known_good_cp01            — full CP-01 build (controller + active belt)
  - known_broken_no_velocity   — CP-01 + conveyor surfaceVelocity zeroed
  - known_broken_no_controller — CP-01 minus setup_pick_place_controller

Prints a fixture × pipeline_ok × len(issues) table, then exits 0.

CURRENT BEHAVIOR (intentional EXPOSURE): the production verifier is
form-shallow — it only checks reach distances + handoff gap thresholds,
neither of which depend on conveyor activity or controller installation.
All three fixtures will report pipeline_ok=true on today's HEAD. Phase
1.1 of the plan will strengthen verify_pickplace_pipeline with three new
form checks (conveyor_active, controller_installed, cube_source_bridged);
once those land, the broken rows should flip to pipeline_ok=false. This
smoke test is the regression boundary that makes that transition explicit.

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC on :8001)

Usage:
    python scripts/qa/verifier_smoke_tests.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["AUTO_APPROVE"] = "true"

from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
from service.isaac_assist_service.chat.tools import kit_tools


# ── Reset (mirrors scripts/qa/run_cp01.py's reset-harness) ──────────────────

RESET_CODE = """
import omni.usd, omni.timeline, builtins
from pxr import UsdGeom, UsdPhysics, UsdLux, UsdShade, Sdf, PhysxSchema, Gf

omni.timeline.get_timeline_interface().stop()

_pp_prefixes = ("_curobo_pp_", "_native_pp_", "_spline_pp_", "_diffik_pp_", "_osc_pp_",
                "_pick_place_", "_sensor_gated_", "_fixed_poses_pp_", "_sensor_")
for k in list(vars(builtins).keys()):
    if k.startswith(_pp_prefixes):
        v = getattr(builtins, k, None)
        try:
            if hasattr(v, "unsubscribe"):
                v.unsubscribe()
        except Exception:
            pass
        try:
            delattr(builtins, k)
        except Exception:
            pass

mgr = getattr(builtins, "_scene_reset_manager", None)
if mgr is not None:
    for _hn in list(getattr(mgr, "_hooks", {}).keys()):
        try:
            mgr.unregister(_hn)
        except Exception:
            pass

omni.usd.get_context().new_stage()
"""


CP01_STAGES = [{
    "robot_path": "/World/Franka",
    "pick_path": "/World/ConveyorBelt",
    "place_path": "/World/Bin",
    "robot_kind": "franka_panda",
}]


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _call(name: str, args: dict) -> dict:
    res = await execute_tool_call(name, args)
    if res.get("type") == "error":
        raise RuntimeError(f"{name} error: {res.get('error', '?')}")
    if res.get("type") == "code_patch" and res.get("success") is False:
        raise RuntimeError(f"{name} failed: {(res.get('output') or '').strip()[:300]}")
    return res


async def _reset_scene() -> None:
    res = await kit_tools.exec_sync(RESET_CODE, timeout=10)
    if not res.get("success"):
        raise RuntimeError(f"reset failed: {(res.get('output') or '')[:300]}")


async def _build_cp01(*, install_controller: bool = True) -> None:
    """Replicate run_cp01.py main() build sequence, optionally skipping the
    final setup_pick_place_controller call (for the no-controller fixture)."""
    cube_paths = [f"/World/Cube_{i+1}" for i in range(4)]

    await _call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await _call("set_attribute", {"prim_path": "/World/DomeLight",
                                   "attr_name": "inputs:intensity", "value": 1000.0})
    await _call("create_prim", {"prim_path": "/World/Cell", "prim_type": "Xform"})
    await _call("create_prim", {"prim_path": "/World/Table", "prim_type": "Cube",
                                 "position": [0, 0, 0.375], "scale": [1.0, 0.5, 0.375]})
    await _call("apply_api_schema", {"prim_path": "/World/Table",
                                      "schema_name": "PhysicsCollisionAPI"})
    await _call("set_physics_scene_config",
                {"config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"}})
    await _call("robot_wizard", {
        "robot_name": "franka_panda", "dest_path": "/World/Franka",
        "position": [0, 0, 0.75], "orientation": [0.7071068, 0, 0, 0.7071068],
    })
    await _call("create_conveyor", {
        "prim_path": "/World/ConveyorBelt",
        "position": [0.0, 0.4, 0.78], "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })
    for i, x in enumerate([-1.4, -1.15, -0.9, -0.65]):
        await _call("create_prim", {
            "prim_path": cube_paths[i], "prim_type": "Cube",
            "position": [x, 0.4, 0.835], "size": 0.05,
        })
        for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI",
                    "PhysicsMassAPI", "PhysxRigidBodyAPI"):
            await _call("apply_api_schema",
                        {"prim_path": cube_paths[i], "schema_name": api})
    await _call("bulk_set_attribute", {
        "prim_paths": cube_paths,
        "attr": "physxRigidBody:sleepThreshold", "value": 0.0,
    })
    for path in cube_paths:
        await _call("apply_physics_material",
                    {"prim_path": path, "material_name": "rubber"})
    await _call("create_bin", {
        "prim_path": "/World/Bin",
        "position": [0, -0.4, 0.75], "size": [0.3, 0.3, 0.15],
    })
    await _call("add_proximity_sensor", {
        "sensor_path": "/World/PickSensor",
        "position": [0.4, 0.4, 0.835], "size": [0.06, 0.06, 0.06],
    })
    if install_controller:
        await _call("setup_pick_place_controller", {
            "robot_path": "/World/Franka", "target_source": "curobo",
            "sensor_path": "/World/PickSensor", "belt_path": "/World/ConveyorBelt",
            "source_paths": cube_paths, "destination_path": "/World/Bin",
            "planning_obstacles": ["/World/Table", "/World/ConveyorBelt", "/World/Bin"],
        })


async def _settle_for_verify(*, conveyor_vel: tuple) -> None:
    """Make the scene state deterministic before calling verify:
      1. Force-stop the timeline (setup_pick_place_controller starts it).
      2. Restore CP-01 cube authored positions (physics may have drifted them
         while controller was paused-installing).
      3. Set the conveyor's surface velocity to the design-intent value
         (the cuRobo controller's _pause_belt may have zeroed it during
         install; we restore for known_good cases or set explicit 0 for
         the broken_no_velocity fixture)."""
    cv = tuple(float(x) for x in conveyor_vel)
    code = f"""
import omni.usd, omni.timeline, omni.kit.commands
from pxr import Gf

try:
    omni.kit.commands.execute('StopAnimation')
except Exception:
    pass
tl = omni.timeline.get_timeline_interface()
tl.stop()
tl.set_current_time(0.0)

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('/World/ConveyorBelt')
if prim and prim.IsValid():
    attr = prim.GetAttribute('physxSurfaceVelocity:surfaceVelocity')
    if attr and attr.IsValid():
        attr.Set(Gf.Vec3f({cv[0]}, {cv[1]}, {cv[2]}))

# Restore CP-01 cube authored positions in case physics drifted them
for i, x in enumerate([-1.4, -1.15, -0.9, -0.65]):
    cube = stage.GetPrimAtPath(f'/World/Cube_{{i+1}}')
    if cube and cube.IsValid():
        attr = cube.GetAttribute('xformOp:translate')
        if attr and attr.IsValid():
            attr.Set(Gf.Vec3d(x, 0.4, 0.835))
"""
    res = await kit_tools.exec_sync(code, timeout=10)
    if not res.get("success"):
        raise RuntimeError(
            f"settle_for_verify failed: {(res.get('output') or '')[:300]}"
        )


async def _verify(stages: list, cube_path: str = "/World/Cube_1") -> tuple[bool, list]:
    """Call verify_pickplace_pipeline and parse the JSON line from output.
    Returns (pipeline_ok, issues)."""
    res = await execute_tool_call("verify_pickplace_pipeline", {
        "stages": stages,
        # cube_path is ignored by today's shallow handler; Phase 1.1 will use it.
        "cube_path": cube_path,
    })
    if res.get("type") == "error":
        raise RuntimeError(f"verify error: {res.get('error', '?')}")
    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
    if parsed is None:
        raise RuntimeError(f"could not parse verify output: {out[:300]!r}")
    return bool(parsed.get("pipeline_ok")), list(parsed.get("issues", []))


# ── Fixtures ────────────────────────────────────────────────────────────────

async def fix_known_good_cp01() -> tuple[bool, list]:
    await _reset_scene()
    await _build_cp01(install_controller=True)
    await _settle_for_verify(conveyor_vel=(0.2, 0.0, 0.0))
    return await _verify(CP01_STAGES)


async def fix_known_broken_no_velocity() -> tuple[bool, list]:
    await _reset_scene()
    await _build_cp01(install_controller=True)
    await _settle_for_verify(conveyor_vel=(0.0, 0.0, 0.0))
    return await _verify(CP01_STAGES)


async def fix_known_broken_no_controller() -> tuple[bool, list]:
    await _reset_scene()
    await _build_cp01(install_controller=False)
    await _settle_for_verify(conveyor_vel=(0.2, 0.0, 0.0))
    return await _verify(CP01_STAGES)


FIXTURES = [
    ("known_good_cp01",            fix_known_good_cp01),
    ("known_broken_no_velocity",   fix_known_broken_no_velocity),
    ("known_broken_no_controller", fix_known_broken_no_controller),
]


# ── Driver ──────────────────────────────────────────────────────────────────

async def main() -> int:
    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at http://127.0.0.1:8001")
        print("       Launch Isaac Sim with the Isaac Assist extension first.")
        return 1

    rows: list[tuple[str, bool, int, list]] = []
    for name, fn in FIXTURES:
        print(f"  fixture: {name} ...")
        try:
            ok, issues = await fn()
        except Exception as e:
            print(f"\n[FAIL] fixture {name!r} crashed: {e}")
            return 2
        rows.append((name, ok, len(issues), issues))

    width = max(len(r[0]) for r in rows)
    print()
    print(f"{'fixture':<{width}}  pipeline_ok  issues")
    print("-" * (width + 22))
    for name, ok, n_issues, _ in rows:
        flag = "true " if ok else "false"
        print(f"{name:<{width}}  {flag:<11}  {n_issues}")
    print()
    print("Expected (after Phase 1.1 form checks landed):")
    print("  known_good_cp01            pipeline_ok=true,  issues=0")
    print("  known_broken_no_velocity   pipeline_ok=false, issues>=1 (cube_source_bridged)")
    print("  known_broken_no_controller pipeline_ok=false, issues>=1 (controller_installed)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
