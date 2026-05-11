#!/usr/bin/env python
"""
Deterministic CP-01 build — replays workspace/templates/CP-01.json `code` field
against Kit RPC (:8001) via the tool_executor library, bypassing the FastAPI
chat stack and the LLM agent.

Usage:
    python scripts/qa/run_cp01.py

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)
    - run from any directory; the script self-locates the repo root

After it finishes successfully, press Stop+Play in the Isaac Sim viewport and
watch 4 cubes ride the belt into the bin (~5-7s/cube, target ~90% grip rate).
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

# Clear stale pickplace state — second install inherits dead subscriptions otherwise.
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

    # DomeLight — without this the viewport renders black after new_stage()
    await call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })

    # Parent Xform
    await call("create_prim", {"prim_path": "/World/Cell", "prim_type": "Xform"})

    # Table — Cube default size=2, scale to 2x1x0.75m with top at z=0.75
    await call("create_prim", {
        "prim_path": "/World/Table", "prim_type": "Cube",
        "position": [0, 0, 0.375], "scale": [1.0, 0.5, 0.375],
    })
    await call("apply_api_schema", {"prim_path": "/World/Table", "schema_name": "PhysicsCollisionAPI"})

    # CPU dynamics required for PhysxSurfaceVelocityAPI
    await call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })

    # Franka on table top, oriented so cube/belt at world +Y is robot's base +X
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })

    # Conveyor belt in front of robot (apparent speed ~3x nominal due to PhysX stick-slip)
    await call("create_conveyor", {
        "prim_path": "/World/ConveyorBelt",
        "position": [0.0, 0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Cubes start OUTSIDE robot's 0.70m pick reach so belt has visible work.
    # z=0.835 = belt top (0.805) + 5mm clearance + cube half-height — avoids MTD-jitter on Stop+Play.
    cube_paths = [f"/World/Cube_{i+1}" for i in range(4)]
    for i, x in enumerate([-1.4, -1.15, -0.9, -0.65]):
        await call("create_prim", {
            "prim_path": cube_paths[i], "prim_type": "Cube",
            "position": [x, 0.4, 0.835], "size": 0.05,
        })
        for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
            await call("apply_api_schema", {"prim_path": cube_paths[i], "schema_name": api})

    # sleepThreshold=0 so cubes never deactivate on a paused belt
    await call("bulk_set_attribute", {
        "prim_paths": cube_paths,
        "attr": "physxRigidBody:sleepThreshold",
        "value": 0.0,
    })

    # Rubber on cubes only — combined-friction with default-mu fingers gives ~0.75 effective.
    # Binding material to Franka fingers fires "triangle mesh on dynamic body" warnings that
    # block the viewport on Play. CP-02 uses this same cube-only pattern.
    for path in cube_paths:
        await call("apply_physics_material", {"prim_path": path, "material_name": "rubber"})

    # Bin behind robot, floor flush on table top
    await call("create_bin", {
        "prim_path": "/World/Bin",
        "position": [0, -0.4, 0.75],
        "size": [0.3, 0.3, 0.15],
    })

    # Pick sensor inside robot reach
    await call("add_proximity_sensor", {
        "sensor_path": "/World/PickSensor",
        "position": [0.4, 0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })

    # cuRobo controller — best motion quality
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka",
        "target_source": "curobo",
        "sensor_path": "/World/PickSensor",
        "belt_path": "/World/ConveyorBelt",
        "source_paths": cube_paths,
        "destination_path": "/World/Bin",
        "planning_obstacles": ["/World/Table", "/World/ConveyorBelt", "/World/Bin"],
    })

    print("\nCP-01 build complete. Press Stop+Play in Isaac Sim to run the cycle.")


if __name__ == "__main__":
    asyncio.run(main())
