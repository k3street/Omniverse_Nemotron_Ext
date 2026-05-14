#!/usr/bin/env python
"""
Deterministic CP-05 build — passive flip-station + delivery for REORIENT-01.

Cube starts ON ITS SIDE on a conveyor. Conveyor pushes cube into a flip-wall
that tips it upright. Robot picks the upright cube from a landing zone and
places it in a destination bin.

WARNING: passive physics flips are sensitive to geometry + friction. This
canonical is a STARTING POINT — flip-wall position + height + cube initial
orientation may need iteration. See workspace/templates/CP-05.json
failure_modes for tuning hints.

Usage:
    python scripts/qa/run_cp05.py

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)

After build, press Stop+Play. Expect (if tuning is good):
    1. Cube on conveyor drifts toward flip-wall
    2. Cube hits wall, tips forward, lands upright on landing zone
    3. Robot picks upright cube
    4. Robot places upright in bin

If cube doesn't flip reliably, see failure_modes in CP-05.json for fixes.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["AUTO_APPROVE"] = "true"

from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
from service.isaac_assist_service.chat.tools import kit_tools


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
    for _hn in list(getattr(mgr, "hooks", {}).keys()):
        try:
            mgr.unregister(_hn)
        except Exception:
            pass

omni.usd.get_context().new_stage()
"""


async def reset_scene():
    print("[reset] new_stage + clear pickplace builtins")
    res = await kit_tools.exec_sync(RESET_CODE, timeout=10)
    if not res.get("success", False):
        print(f"[reset] FAILED: {res.get('output', '?')[:300]}")
        raise SystemExit(1)


async def call(name: str, args: dict):
    result = await execute_tool_call(name, args)
    rtype = result.get("type")
    if rtype == "error":
        print(f"[FAIL] {name}: {result.get('error', '?')}")
        raise SystemExit(1)
    if rtype == "code_patch" and result.get("success") is False:
        out = (result.get("output") or "").strip()[:400]
        print(f"[FAIL] {name}: {out}")
        raise SystemExit(1)
    print(f"[OK]   {name}")
    return result


async def main():
    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at http://127.0.0.1:8001")
        raise SystemExit(1)

    await reset_scene()

    # DomeLight + Ground
    await call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })
    await call("create_prim", {
        "prim_path": "/World/Ground", "prim_type": "Cube",
        "position": [0, 0, -0.5], "scale": [20, 20, 1],
    })
    await call("apply_api_schema", {"prim_path": "/World/Ground", "schema_name": "PhysicsCollisionAPI"})

    # Wider table
    await call("create_prim", {
        "prim_path": "/World/Table", "prim_type": "Cube",
        "position": [0, 0, 0.375], "scale": [1.5, 0.5, 0.375],
    })
    await call("apply_api_schema", {"prim_path": "/World/Table", "schema_name": "PhysicsCollisionAPI"})

    await call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })

    # Franka at +X (picks AFTER flip)
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [1.0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })

    # Conveyor — 2m, spans cube start + flip-wall + landing zone.
    # cube_source_bridged needs the conveyor's bbox to contain both
    # cube xy and pick xy; long enough belt makes that work.
    await call("create_conveyor", {
        "prim_path": "/World/Conveyor",
        "position": [0.0, 0.4, 0.78],
        "size": [2.0, 0.3, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Cube on its side (rotation_euler=[90, 0, 0] = 90° around X-axis)
    await call("create_prim", {
        "prim_path": "/World/Cube", "prim_type": "Cube",
        "position": [-0.9, 0.4, 0.835], "size": 0.05,
        "rotation_euler": [90.0, 0.0, 0.0],
    })
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        await call("apply_api_schema", {"prim_path": "/World/Cube", "schema_name": api})

    await call("bulk_set_attribute", {
        "prim_paths": ["/World/Cube"],
        "attr": "physxRigidBody:sleepThreshold",
        "value": 0.0,
    })
    await call("apply_physics_material", {"prim_path": "/World/Cube", "material_name": "rubber"})

    # Flip-wall — thin tall collider on belt. Tips cube forward at impact.
    await call("create_prim", {
        "prim_path": "/World/FlipWall", "prim_type": "Cube",
        "position": [0.4, 0.4, 0.83],
        "scale": [0.01, 0.15, 0.025],
    })
    await call("apply_api_schema", {"prim_path": "/World/FlipWall", "schema_name": "PhysicsCollisionAPI"})

    # Landing-zone marker — INSIDE the conveyor's bbox so verify's
    # cube_source_bridged sees the conveyor as the bridge.
    await call("create_prim", {
        "prim_path": "/World/LandingZone", "prim_type": "Cube",
        "position": [0.6, 0.4, 0.81],
        "scale": [0.05, 0.05, 0.001],
    })
    await call("apply_api_schema", {"prim_path": "/World/LandingZone", "schema_name": "PhysicsCollisionAPI"})

    # Bin behind robot
    await call("create_bin", {
        "prim_path": "/World/Bin", "position": [1.0, -0.4, 0.75], "size": [0.3, 0.3, 0.15],
    })

    # Pick sensor centered on landing zone
    await call("add_proximity_sensor", {
        "sensor_path": "/World/PickSensor",
        "position": [0.6, 0.4, 0.835], "size": [0.06, 0.06, 0.06],
    })

    # cuRobo controller — picks at landing zone, places in bin
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka",
        "target_source": "curobo",
        "sensor_path": "/World/PickSensor",
        "belt_path": "/World/Conveyor",
        "source_paths": ["/World/Cube"],
        "destination_path": "/World/Bin",
        "planning_obstacles": ["/World/Table", "/World/Conveyor",
                                "/World/FlipWall", "/World/LandingZone",
                                "/World/Bin"],
    })

    print("\nCP-05 build complete. Press Stop+Play. Expect cube to tip upright at flip-wall.")
    print("If cube doesn't flip reliably, see CP-05.json failure_modes for tuning.")


if __name__ == "__main__":
    asyncio.run(main())
