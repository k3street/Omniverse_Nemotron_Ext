"""
probe_builtin_tabletop.py — verify the builtin pipeline on a STATIC tabletop
scene (no conveyor). PickPlaceController is designed for static cubes, not
moving conveyor cubes.

Pass criterion: cube starts at (0.4, 0.0, 0.4), ends within bin xy
[(0.0, -0.4), 0.4×0.4 footprint].

Diagnostic-only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.tools import kit_tools  # noqa: E402
from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    execute_tool_call,
)


SETUP_STAGE = """
import omni.usd
ctx = omni.usd.get_context()
ctx.new_stage()
from pxr import UsdGeom
UsdGeom.Xform.Define(ctx.get_stage(), '/World')
"""


async def reset_and_build():
    """Build minimal tabletop pick-place scene: ground + Franka + 1 static cube + target zone."""
    res = await kit_tools.exec_sync(SETUP_STAGE, timeout=15)
    if not res.get("success"):
        raise RuntimeError(f"reset failed: {res.get('output')}")

    # Foundation
    await execute_tool_call("create_prim", {"prim_path": "/World/DomeLight", "prim_type": "DomeLight"})
    await execute_tool_call("set_attribute", {"prim_path": "/World/DomeLight",
                                                "attr_name": "inputs:intensity", "value": 1000.0})
    await execute_tool_call("create_prim", {"prim_path": "/World/Ground",
                                              "prim_type": "Cube",
                                              "position": [0, 0, -0.5],
                                              "scale": [10, 10, 1]})
    await execute_tool_call("apply_api_schema", {"prim_path": "/World/Ground",
                                                   "schema_name": "PhysicsCollisionAPI"})
    await execute_tool_call("set_physics_scene_config",
                              {"config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"}})

    # Franka at origin
    await execute_tool_call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [0, 0, 0],
        "orientation": [1.0, 0, 0, 0],
    })

    # Single static cube at typical PickPlace task position: (0.3, 0.3, 0.025)
    await execute_tool_call("create_prim", {
        "prim_path": "/World/Cube",
        "prim_type": "Cube",
        "position": [0.3, 0.3, 0.025],
        "size": 0.05,
    })
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"):
        await execute_tool_call("apply_api_schema",
                                  {"prim_path": "/World/Cube", "schema_name": api})

    # Target zone (just for verification — not a physical bin)
    await execute_tool_call("create_prim", {
        "prim_path": "/World/Target",
        "prim_type": "Cube",
        "position": [0.7, 0.7, 0.025],
        "size": 0.05,
    })
    await execute_tool_call("apply_api_schema", {"prim_path": "/World/Target",
                                                   "schema_name": "PhysicsCollisionAPI"})


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=30)
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive")
        return 2

    print("=== build static tabletop Franka scene ===")
    await reset_and_build()

    print("\n=== install builtin controller via SingleArticulation ===")
    SETUP = """
import omni.usd, omni.timeline, omni.kit.app, json, builtins, traceback, time as _t
import numpy as np
from pxr import UsdGeom, Usd
from isaacsim.core.prims import SingleArticulation
from isaacsim.robot.manipulators.grippers.parallel_gripper import ParallelGripper
from isaacsim.robot.manipulators.examples.franka.controllers.pick_place_controller import PickPlaceController
try:
    from isaacsim.core.simulation_manager import SimulationManager
except Exception:
    from isaacsim.core.api.simulation_manager import SimulationManager

steps = []
try:
    stage = omni.usd.get_context().get_stage()
    tl = omni.timeline.get_timeline_interface()
    app = omni.kit.app.get_app()

    tl.play()
    for _ in range(10): app.update()
    if SimulationManager.get_physics_sim_view() is None:
        SimulationManager.initialize_physics()
    psv = SimulationManager.get_physics_sim_view()
    steps.append(("psv", "ok" if psv else "None"))

    for _attr in [k for k in vars(builtins).keys() if "_probe" in k]:
        try: getattr(builtins, _attr).unsubscribe()
        except Exception: pass
        try: delattr(builtins, _attr)
        except Exception: pass

    art = SingleArticulation(prim_path="/World/Franka", name="probe_tt_franka")
    art.initialize(psv)
    steps.append(("art_init", "ok"))

    gripper = ParallelGripper(
        end_effector_prim_path="/World/Franka/panda_hand",
        joint_prim_names=["panda_finger_joint1", "panda_finger_joint2"],
        joint_opened_positions=np.array([0.04, 0.04]),
        joint_closed_positions=np.array([0.0, 0.0]),
        action_deltas=np.array([0.05, 0.05]),
    )
    gripper.initialize(physics_sim_view=psv,
                       articulation_apply_action_func=art.apply_action,
                       get_joint_positions_func=art.get_joint_positions,
                       set_joint_positions_func=art.set_joint_positions,
                       dof_names=art.dof_names)
    steps.append(("gripper_init", "ok"))

    ctrl = PickPlaceController(name="probe_tt_pp", gripper=gripper, robot_articulation=art)
    art_ctrl = art.get_articulation_controller()

    def cube_pos(path):
        p = stage.GetPrimAtPath(path)
        if not p or not p.IsValid(): return None
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        b = cache.ComputeWorldBound(p).ComputeAlignedRange()
        if b.IsEmpty(): return None
        c = b.GetMidpoint()
        return np.array([float(c[0]), float(c[1]), float(c[2])])

    pick_pos = cube_pos("/World/Cube")
    place_pos = cube_pos("/World/Target")
    steps.append(("pick_pos_initial", str([round(float(x),3) for x in pick_pos])))
    steps.append(("place_pos", str([round(float(x),3) for x in place_pos])))

    S = {"done": False, "delivered_at": None}
    def on_step(dt):
        try:
            jp = art.get_joint_positions()
            if jp is None: return
            actions = ctrl.forward(
                picking_position=pick_pos,
                placing_position=place_pos,
                current_joint_positions=jp,
                end_effector_offset=np.array([0, 0, 0.02]),
            )
            if actions is not None:
                art_ctrl.apply_action(actions)
            if ctrl.is_done() and not S["done"]:
                S["done"] = True
                S["delivered_at"] = float(tl.get_current_time())
        except Exception as _e:
            S.setdefault("err", []).append(f"{type(_e).__name__}: {str(_e)[:120]}")

    import omni.physx
    sub = omni.physx.get_physx_interface().subscribe_physics_step_events(on_step)
    setattr(builtins, "_probe_tt_pp_sub", sub)
    steps.append(("sub_installed", "ok"))

    DURATION = {duration}
    real_start = _t.time()
    while True:
        app.update()
        t = float(tl.get_current_time())
        if t >= DURATION: break
        if _t.time() - real_start > DURATION + 90: break

    final = cube_pos("/World/Cube")
    steps.append(("final_pos", str([round(float(x),3) for x in final]) if final is not None else "None"))
    steps.append(("ctrl_is_done", str(ctrl.is_done())))
    steps.append(("delivered_at", str(S.get("delivered_at"))))
    steps.append(("errors", str(S.get("err", []))[:200]))

    try: sub.unsubscribe()
    except Exception: pass
    delattr(builtins, "_probe_tt_pp_sub")
    tl.stop()
except Exception as e:
    steps.append(("fatal", f"{type(e).__name__}: {str(e)[:300]}"))
    traceback.print_exc()
print(json.dumps(steps))
""".replace("{duration}", str(args.duration))

    res = await kit_tools.exec_sync(SETUP, timeout=args.duration + 120)
    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        if line.strip().startswith("[") and "(" in line:
            try: parsed = json.loads(line)
            except Exception: pass
    if not parsed:
        print(f"raw: {out[-2500:]}")
        return 1

    print()
    for step, val in parsed:
        print(f"  {step}: {val[:200]}")

    final = next((eval(v) for s, v in parsed if s == "final_pos" and v != "None"), None)
    place = next((eval(v) for s, v in parsed if s == "place_pos"), None)
    print()
    if final and place:
        dist_xy = ((final[0]-place[0])**2 + (final[1]-place[1])**2) ** 0.5
        print(f"Final → Target xy distance: {dist_xy:.3f}m")
        verdict = "DELIVERED" if dist_xy < 0.10 else f"NOT delivered (dist={dist_xy:.3f}m)"
        print(f"VERDICT: {verdict}")
        return 0 if dist_xy < 0.10 else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
