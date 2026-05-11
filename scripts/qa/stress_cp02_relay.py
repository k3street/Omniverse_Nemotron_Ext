"""
stress_cp02_relay.py — multi-cube CP-02 relay stress test.

CP-02 canonically validates ONE cube traversing Conv1→R1→Conv2→R2→Bin.
This script extends the test by spawning N cubes on Conv1 and verifying
all N reach the final Bin end-to-end.

What this stresses (NOT duplicating CP-02):
- 2 cuRobo controllers each managing a multi-cube queue
- Robot1 places cube_i on Conv2 while cube_i+1 is still picking up
- Robot2 starts picking when first cube arrives, before Robot1 finishes queue
- Inter-belt timing: Conv1 paused while R1 picks; Conv2 keeps moving
- Per-controller subscription scoping (different _ROBOT_TAG) under load

Usage:
  python scripts/qa/stress_cp02_relay.py
  python scripts/qa/stress_cp02_relay.py --cubes 3 --duration 240
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)


async def _reset_scene() -> None:
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "from pxr import UsdGeom\n"
        "UsdGeom.Xform.Define(ctx.get_stage(), '/World')\n"
    )
    await kit_tools.exec_sync(code, timeout=20)


async def _call(tool: str, args: Dict) -> Dict:
    res = await execute_tool_call(tool, args)
    if res.get("type") == "error":
        raise RuntimeError(f"{tool}: {res.get('error', '?')[:200]}")
    return res


async def build_cp02_with_n_cubes(n_cubes: int) -> List[str]:
    """Replicate CP-02 layout but with n_cubes on Conv1 instead of 1."""
    cube_paths = [f"/World/Cube_{i+1}" for i in range(n_cubes)]
    # Cube spacing on Conv1: 0.3m apart at conveyor x=[-3,0]
    cube_x_positions = [round(-2.5 + 0.30 * i, 3) for i in range(n_cubes)]

    # Foundation
    await _call("create_prim", {"prim_path": "/World/DomeLight",
                                 "prim_type": "DomeLight"})
    await _call("set_attribute", {"prim_path": "/World/DomeLight",
                                   "attr_name": "inputs:intensity",
                                   "value": 1000.0})
    await _call("create_prim", {"prim_path": "/World/Ground",
                                 "prim_type": "Cube",
                                 "position": [0, 0, -0.5],
                                 "scale": [20, 20, 1]})
    await _call("apply_api_schema", {"prim_path": "/World/Ground",
                                      "schema_name": "PhysicsCollisionAPI"})
    await _call("create_prim", {"prim_path": "/World/Table",
                                 "prim_type": "Cube",
                                 "position": [0, 0, 0.375],
                                 "scale": [1.5, 0.5, 0.375]})
    await _call("apply_api_schema", {"prim_path": "/World/Table",
                                      "schema_name": "PhysicsCollisionAPI"})
    await _call("set_physics_scene_config",
                {"config": {"enable_gpu_dynamics": False,
                            "broadphase_type": "MBP"}})
    # Two robots
    await _call("robot_wizard", {
        "robot_name": "franka_panda", "dest_path": "/World/Franka1",
        "position": [-1.0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, 0.7071068],
    })
    await _call("robot_wizard", {
        "robot_name": "franka_panda", "dest_path": "/World/Franka2",
        "position": [1.0, 0, 0.75],
        "orientation": [0.7071068, 0, 0, -0.7071068],
    })
    # Two conveyors
    await _call("create_conveyor", {
        "prim_path": "/World/Conv1",
        "position": [-1.5, 0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })
    await _call("create_conveyor", {
        "prim_path": "/World/Conv2",
        "position": [0.0, -0.4, 0.78],
        "size": [3.0, 0.4, 0.05],
        "surface_velocity": [0.2, 0, 0],
    })
    # Cubes on Conv1
    for i, cx in enumerate(cube_x_positions):
        await _call("create_prim", {
            "prim_path": cube_paths[i], "prim_type": "Cube",
            "position": [cx, 0.4, 0.835], "size": 0.05,
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
    # Bin + sensors
    await _call("create_bin", {
        "prim_path": "/World/Bin",
        "position": [1.0, 0.4, 0.75],
        "size": [0.3, 0.3, 0.15],
    })
    await _call("add_proximity_sensor", {
        "sensor_path": "/World/Sensor1",
        "position": [-1.0, 0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })
    await _call("add_proximity_sensor", {
        "sensor_path": "/World/Sensor2",
        "position": [1.0, -0.4, 0.835],
        "size": [0.06, 0.06, 0.06],
    })
    # Two controllers — both see ALL cubes
    await _call("setup_pick_place_controller", {
        "robot_path": "/World/Franka1", "target_source": "curobo",
        "sensor_path": "/World/Sensor1", "belt_path": "/World/Conv1",
        "source_paths": cube_paths, "destination_path": "/World/Conv2",
        "drop_target": [-1.0, -0.4, 0.94],
        "planning_obstacles": ["/World/Table", "/World/Conv1",
                                "/World/Conv2", "/World/Bin"],
    })
    await _call("setup_pick_place_controller", {
        "robot_path": "/World/Franka2", "target_source": "curobo",
        "sensor_path": "/World/Sensor2", "belt_path": "/World/Conv2",
        "source_paths": cube_paths, "destination_path": "/World/Bin",
        "planning_obstacles": ["/World/Table", "/World/Conv1",
                                "/World/Conv2", "/World/Bin"],
    })
    return cube_paths


async def settle_cp02(cube_paths: List[str]) -> None:
    """Restore conveyor velocities + cube positions after install."""
    cube_xs = [round(-2.5 + 0.30 * i, 3) for i in range(len(cube_paths))]
    code = f"""
import omni.usd, omni.timeline
from pxr import Gf
omni.timeline.get_timeline_interface().stop()
omni.timeline.get_timeline_interface().set_current_time(0.0)
stage = omni.usd.get_context().get_stage()
for path in ['/World/Conv1', '/World/Conv2']:
    p = stage.GetPrimAtPath(path)
    if p and p.IsValid():
        a = p.GetAttribute('physxSurfaceVelocity:surfaceVelocity')
        if a and a.IsValid():
            a.Set(Gf.Vec3f(0.2, 0, 0))
for path, x in zip({cube_paths}, {cube_xs}):
    p = stage.GetPrimAtPath(path)
    if p and p.IsValid():
        a = p.GetAttribute('xformOp:translate')
        if a and a.IsValid():
            a.Set(Gf.Vec3d(x, 0.4, 0.835))
"""
    await kit_tools.exec_sync(code, timeout=10)


async def play_and_track(duration_s: int, cube_paths: List[str]) -> Dict:
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
    return {{'min': [float(b.GetMin()[i]) for i in range(3)],
             'max': [float(b.GetMax()[i]) for i in range(3)]}}

cube_paths = {cube_paths}
initial = {{c: world_pos(c) for c in cube_paths}}
bin_bbox = world_bbox('/World/Bin')

tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()
tl.stop(); tl.set_current_time(0.0); tl.play()

# Track per-cube delivery time (sim t when cube enters bin xy)
deliveries = {{c: None for c in cube_paths}}

real_start = _t.time()
last_log = 0
while True:
    app.update()
    cur_t = float(tl.get_current_time())
    if int(cur_t / 30) > last_log:
        last_log = int(cur_t / 30)
        # Snapshot every ~30s
    for c in cube_paths:
        if deliveries[c] is None:
            pos = world_pos(c)
            if pos and bin_bbox:
                in_xy = (
                    bin_bbox['min'][0] - 0.05 <= pos[0] <= bin_bbox['max'][0] + 0.05
                    and bin_bbox['min'][1] - 0.05 <= pos[1] <= bin_bbox['max'][1] + 0.05
                )
                above = pos[2] >= bin_bbox['min'][2] - 0.10
                if in_xy and above:
                    deliveries[c] = round(cur_t, 1)
    if cur_t >= {duration_s}: break
    if _t.time() - real_start > {duration_s} + 120: break

final = {{c: world_pos(c) for c in cube_paths}}
tl.stop()
print(json.dumps({{
    'deliveries': deliveries,
    'final': final,
    'bin_bbox': bin_bbox,
    'sim_t_reached': cur_t,
}}))
"""
    res = await kit_tools.exec_sync(code, timeout=duration_s + 180)
    out = (res.get("output") or "").strip()
    for line in out.splitlines():
        if line.strip().startswith("{") and "deliveries" in line:
            return json.loads(line)
    return {"error": "no result"}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cubes", type=int, default=2,
                   help="Number of cubes on Conv1 (default 2; CP-02 canonically uses 1)")
    p.add_argument("--duration", type=int, default=180,
                   help="Sim duration (default 180s — CP-02 single-cube takes ~45s)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive")
        return 2

    print(f"=== build CP-02 with {args.cubes} cubes ===")
    await _reset_scene()
    cube_paths = await build_cp02_with_n_cubes(args.cubes)
    print(f"  built {len(cube_paths)} cubes: {cube_paths}")

    print(f"\n=== settle + play ({args.duration}s sim) ===")
    await settle_cp02(cube_paths)
    track = await play_and_track(args.duration, cube_paths)
    if "error" in track:
        print(f"  TRACK FAILED: {track['error']}")
        return 1

    deliveries = track["deliveries"]
    finals = track["final"]
    print()
    print(f"{'Cube':<14} {'Delivered at sim_t':<22} {'Final pos':<32}")
    print("-" * 75)
    n_total = len(cube_paths)
    n_delivered = 0
    for c in cube_paths:
        t_delivered = deliveries[c]
        pos = finals[c]
        pos_str = f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})" if pos else "?"
        if t_delivered is not None:
            n_delivered += 1
            print(f"{c.split('/')[-1]:<14} {t_delivered:>6}s ✓             {pos_str}")
        else:
            print(f"{c.split('/')[-1]:<14} NOT DELIVERED          {pos_str}")
    print()
    print(f"Sim time reached: {track.get('sim_t_reached', '?')}s")
    print(f"OVERALL: {n_delivered}/{n_total} cubes delivered through full relay")
    return 0 if n_delivered == n_total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
