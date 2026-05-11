#!/usr/bin/env python
"""
Deterministic CP-04 build — replays workspace/templates/CP-04.json `code` field
against Kit RPC (:8001). Compact 2×2 m pick-and-place cell for CONSTRAINT-01.

Usage:
    python scripts/qa/run_cp04.py

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)

After it finishes, press Stop+Play in the Isaac Sim viewport. Expect 4 cubes
to be delivered to the bin within ~30-50s, similar to CP-01 but with the
shorter conveyor giving cubes less travel time. Reach is the constraint —
verify with footprint_bounds=[[-1,-1],[1,1]] catches any out-of-bounds drift.
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
        print("       Launch Isaac Sim with the Isaac Assist extension first.")
        raise SystemExit(1)

    await reset_scene()

    # DomeLight
    await call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })

    # Parent Xform
    await call("create_prim", {"prim_path": "/World/Cell", "prim_type": "Xform"})

    # Compact 1×1×0.75m table (vs CP-01's 2×1)
    await call("create_prim", {
        "prim_path": "/World/Table", "prim_type": "Cube",
        "position": [0, 0, 0.375], "scale": [0.5, 0.5, 0.375],
    })
    await call("apply_api_schema", {"prim_path": "/World/Table", "schema_name": "PhysicsCollisionAPI"})

    # CPU dynamics for PhysxSurfaceVelocityAPI
    await call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })

    # Franka — same orientation as CP-01
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })

    # Compact 1.6m conveyor (vs CP-01's 3m). bbox xy [-0.8, 0.25]→[0.8, 0.55]
    await call("create_conveyor", {
        "prim_path": "/World/ConveyorBelt",
        "position": [0.0, 0.4, 0.78],
        "size": [1.6, 0.3, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Cubes outside Franka's 0.70m reach but on the conveyor.
    # x positions chosen so each cube starts |x| > 0.575 (out of reach
    # at y=0.4) and inside the conveyor's [-0.8, 0.8] x bbox.
    cube_paths = [f"/World/Cube_{i+1}" for i in range(4)]
    for i, x in enumerate([-0.75, -0.6, -0.45, -0.3]):
        await call("create_prim", {
            "prim_path": cube_paths[i], "prim_type": "Cube",
            "position": [x, 0.4, 0.835], "size": 0.05,
        })
        for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
            await call("apply_api_schema", {"prim_path": cube_paths[i], "schema_name": api})

    await call("bulk_set_attribute", {
        "prim_paths": cube_paths,
        "attr": "physxRigidBody:sleepThreshold",
        "value": 0.0,
    })

    # Rubber on cubes only (CP-01 pattern)
    for path in cube_paths:
        await call("apply_physics_material", {"prim_path": path, "material_name": "rubber"})

    # Bin behind robot
    await call("create_bin", {
        "prim_path": "/World/Bin",
        "position": [0, -0.4, 0.75],
        "size": [0.3, 0.3, 0.15],
    })

    # Pick sensor inside reach
    await call("add_proximity_sensor", {
        "sensor_path": "/World/PickSensor",
        "position": [0.4, 0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })

    # cuRobo controller
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka", "target_source": "curobo",
        "sensor_path": "/World/PickSensor", "belt_path": "/World/ConveyorBelt",
        "source_paths": cube_paths, "destination_path": "/World/Bin",
        "planning_obstacles": ["/World/Table", "/World/ConveyorBelt", "/World/Bin"],
    })

    print("\nCP-04 build complete (compact 2×2 m). Press Stop+Play to deliver cubes.")


if __name__ == "__main__":
    asyncio.run(main())
