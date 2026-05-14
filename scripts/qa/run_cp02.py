#!/usr/bin/env python
"""
Deterministic CP-02 build — replays workspace/templates/CP-02.json `code` field
against Kit RPC (:8001) via the tool_executor library, bypassing the FastAPI
chat stack and the LLM agent.

Multi-station assembly: Cube_1 traverses Conv1 → Robot 1 → Conv2 → Robot 2 → Bin.

Usage:
    python scripts/qa/run_cp02.py

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)

After it finishes, press Stop+Play in the Isaac Sim viewport and watch the
single cube travel end-to-end through both stations.
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

    # Foundational scene — DomeLight + Ground
    await call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })
    await call("create_prim", {
        "prim_path": "/World/Ground", "prim_type": "Cube",
        "position": [0, 0, -0.5], "scale": [20, 20, 1],
    })
    await call("apply_api_schema", {"prim_path": "/World/Ground", "schema_name": "PhysicsCollisionAPI"})

    # Wide table covering both stations
    await call("create_prim", {
        "prim_path": "/World/Table", "prim_type": "Cube",
        "position": [0, 0, 0.375], "scale": [1.5, 0.5, 0.375],
    })
    await call("apply_api_schema", {"prim_path": "/World/Table", "schema_name": "PhysicsCollisionAPI"})

    # CPU dynamics for PhysxSurfaceVelocityAPI
    await call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })

    # Robot 1 — picks Conv1 (y=+0.4), drops on Conv2 (y=-0.4); +90° around Z
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka1",
        "position": [-1.0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })

    # Robot 2 — picks Conv2 (y=-0.4), drops in Bin (y=+0.4); -90° around Z
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka2",
        "position": [1.0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, -0.7071068],
    })

    # Conv1 — input belt to Robot 1's pick zone at x=-1
    await call("create_conveyor", {
        "prim_path": "/World/Conv1",
        "position": [-1.5, 0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Conv2 — transfer belt from Robot 1's drop to Robot 2's pick at x=+1
    await call("create_conveyor", {
        "prim_path": "/World/Conv2",
        "position": [0.0, -0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Single cube starts at far left of Conv1
    await call("create_prim", {
        "prim_path": "/World/Cube_1", "prim_type": "Cube",
        "position": [-2.5, 0.4, 0.835], "size": 0.05,
    })
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        await call("apply_api_schema", {"prim_path": "/World/Cube_1", "schema_name": api})

    await call("bulk_set_attribute", {
        "prim_paths": ["/World/Cube_1"],
        "attr": "physxRigidBody:sleepThreshold",
        "value": 0.0,
    })

    # Rubber on cube only — combined-friction with default-mu fingers gives ~0.75 effective
    await call("apply_physics_material", {"prim_path": "/World/Cube_1", "material_name": "rubber"})

    # Bin near Robot 2 (final destination)
    await call("create_bin", {
        "prim_path": "/World/Bin",
        "position": [1.0, 0.4, 0.75],
        "size": [0.3, 0.3, 0.15],
    })

    # Per-station sensors at each robot's pick zone
    await call("add_proximity_sensor", {
        "sensor_path": "/World/Sensor1",
        "position": [-1.0, 0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })
    await call("add_proximity_sensor", {
        "sensor_path": "/World/Sensor2",
        "position": [1.0, -0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })

    # Robot 1 controller — destination=Conv2, explicit drop_target inside reach
    # at Conv2's input edge. z=0.94: hand at 0.94, fingertips at 0.835, ~10mm above belt top.
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka1",
        "target_source": "curobo",
        "sensor_path": "/World/Sensor1",
        "belt_path": "/World/Conv1",
        "source_paths": ["/World/Cube_1"],
        "destination_path": "/World/Conv2",
        "drop_target": [-1.0, -0.4, 0.94],
        "planning_obstacles": ["/World/Table", "/World/Conv1", "/World/Conv2", "/World/Bin"],
    })

    # Robot 2 controller — picks Cube_1 from Conv2, drops in Bin
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka2",
        "target_source": "curobo",
        "sensor_path": "/World/Sensor2",
        "belt_path": "/World/Conv2",
        "source_paths": ["/World/Cube_1"],
        "destination_path": "/World/Bin",
        "planning_obstacles": ["/World/Table", "/World/Conv1", "/World/Conv2", "/World/Bin"],
    })

    print("\nCP-02 build complete. Press Stop+Play in Isaac Sim to run the pipeline.")


if __name__ == "__main__":
    asyncio.run(main())
