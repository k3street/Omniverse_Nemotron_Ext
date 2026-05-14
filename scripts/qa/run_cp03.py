#!/usr/bin/env python
"""
Deterministic CP-03 build — color-sorting station for SORT-01.

Single Franka, single conveyor, two color-tagged cubes (red + blue),
two color-tinted bins. Controller installed with color_routing arg
dispatching destination per cube based on Semantics_color class_name.

Usage:
    python scripts/qa/run_cp03.py

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)

After it finishes, press Stop+Play. Expect: red cube delivered to RedBin
(at +x), blue cube delivered to BlueBin (at -x). Routing is determined
by each cube's Semantics_color label, not by physical position.
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

    # DomeLight
    await call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })

    # Parent + table
    await call("create_prim", {"prim_path": "/World/Cell", "prim_type": "Xform"})
    await call("create_prim", {
        "prim_path": "/World/Table", "prim_type": "Cube",
        "position": [0, 0, 0.375], "scale": [1.0, 0.5, 0.375],
    })
    await call("apply_api_schema", {"prim_path": "/World/Table", "schema_name": "PhysicsCollisionAPI"})

    await call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })

    # Franka
    await call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })

    # Conveyor
    await call("create_conveyor", {
        "prim_path": "/World/ConveyorBelt",
        "position": [0.0, 0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })

    # Red cube — material + semantic_color="red"
    await call("create_prim", {
        "prim_path": "/World/Cube_red", "prim_type": "Cube",
        "position": [-1.4, 0.4, 0.835], "size": 0.05,
    })
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        await call("apply_api_schema", {"prim_path": "/World/Cube_red", "schema_name": api})
    await call("create_material", {
        "material_path": "/World/Materials/RedMat",
        "shader_type": "OmniPBR",
        "diffuse_color": [1.0, 0.0, 0.0],
    })
    await call("assign_material", {
        "prim_path": "/World/Cube_red", "material_path": "/World/Materials/RedMat",
    })
    await call("set_semantic_label", {
        "prim_path": "/World/Cube_red", "class_name": "red", "semantic_type": "color",
    })

    # Blue cube — material + semantic_color="blue"
    await call("create_prim", {
        "prim_path": "/World/Cube_blue", "prim_type": "Cube",
        "position": [-1.0, 0.4, 0.835], "size": 0.05,
    })
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        await call("apply_api_schema", {"prim_path": "/World/Cube_blue", "schema_name": api})
    await call("create_material", {
        "material_path": "/World/Materials/BlueMat",
        "shader_type": "OmniPBR",
        "diffuse_color": [0.0, 0.0, 1.0],
    })
    await call("assign_material", {
        "prim_path": "/World/Cube_blue", "material_path": "/World/Materials/BlueMat",
    })
    await call("set_semantic_label", {
        "prim_path": "/World/Cube_blue", "class_name": "blue", "semantic_type": "color",
    })

    # sleepThreshold + rubber for both cubes
    await call("bulk_set_attribute", {
        "prim_paths": ["/World/Cube_red", "/World/Cube_blue"],
        "attr": "physxRigidBody:sleepThreshold",
        "value": 0.0,
    })
    await call("apply_physics_material", {"prim_path": "/World/Cube_red", "material_name": "rubber"})
    await call("apply_physics_material", {"prim_path": "/World/Cube_blue", "material_name": "rubber"})

    # Two color-tinted bins on opposite sides
    await call("create_bin", {
        "prim_path": "/World/RedBin", "position": [0.4, -0.4, 0.75], "size": [0.3, 0.3, 0.15],
    })
    await call("assign_material", {
        "prim_path": "/World/RedBin", "material_path": "/World/Materials/RedMat",
    })
    await call("create_bin", {
        "prim_path": "/World/BlueBin", "position": [-0.4, -0.4, 0.75], "size": [0.3, 0.3, 0.15],
    })
    await call("assign_material", {
        "prim_path": "/World/BlueBin", "material_path": "/World/Materials/BlueMat",
    })

    # Sensor inside reach
    await call("add_proximity_sensor", {
        "sensor_path": "/World/PickSensor",
        "position": [0.4, 0.4, 0.835], "size": [0.06, 0.06, 0.06],
    })

    # cuRobo controller with color_routing
    await call("setup_pick_place_controller", {
        "robot_path": "/World/Franka",
        "target_source": "curobo",
        "sensor_path": "/World/PickSensor",
        "belt_path": "/World/ConveyorBelt",
        "source_paths": ["/World/Cube_red", "/World/Cube_blue"],
        "destination_path": "/World/RedBin",
        "color_routing": {"red": "/World/RedBin", "blue": "/World/BlueBin"},
        "planning_obstacles": ["/World/Table", "/World/ConveyorBelt",
                                "/World/RedBin", "/World/BlueBin"],
    })

    print("\nCP-03 build complete. Press Stop+Play. Red cube → RedBin (+x), Blue cube → BlueBin (-x).")


if __name__ == "__main__":
    asyncio.run(main())
