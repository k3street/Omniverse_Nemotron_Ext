"""
probe_builtin_singlearticulation.py — verify the full bundled-controller
pipeline works when bypassing World() and using SingleArticulation directly.

Builds minimal Franka pick-place scene, instantiates the full controller
stack manually (no World involvement), runs physics for N seconds, samples
cube position at end. Pass criterion: cube reaches bin xy.

Diagnostic-only (no production code change). If this passes, refactor
_gen_pick_place_builtin to use this same pattern.

Pattern verified by probe_world_physics_view.py sequence G:
- skip World
- SimulationManager.initialize_physics()
- SingleArticulation(prim_path).initialize(psv)
- joint_pos reads correctly

This probe extends that to include ParallelGripper + PickPlaceController +
physics_step subscription + cube delivery loop.

Usage:
  python scripts/qa/probe_builtin_singlearticulation.py
  python scripts/qa/probe_builtin_singlearticulation.py --duration 45
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


async def reset_and_build():
    """Build minimal CP-01-style scene: Franka + conveyor + 1 cube + bin."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "qa"))
    import verifier_smoke_tests as vs
    await vs._reset_scene()
    await vs._build_cp01(install_controller=False)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=30,
                   help="Sim duration in seconds (default 30)")
    args = p.parse_args()

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive")
        return 2

    print("=== build minimal CP-01 scene (no controller) ===")
    await reset_and_build()
    # Settle to deterministic state
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "qa"))
    import verifier_smoke_tests as vs
    await vs._settle_for_verify(conveyor_vel=(0.2, 0.0, 0.0))

    print("\n=== install builtin controller via SingleArticulation pattern ===")
    SETUP = """
import omni.usd, omni.timeline, omni.kit.app, json, builtins, traceback
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

    # Ensure timeline plays for SimulationManager to bind PhysX
    tl.play()
    for _ in range(10): app.update()
    if SimulationManager.get_physics_sim_view() is None:
        SimulationManager.initialize_physics()
    psv = SimulationManager.get_physics_sim_view()
    steps.append(("psv", "ok" if psv is not None else "None"))

    # Tear down any prior probe sub
    for _attr in [k for k in vars(builtins).keys() if k.startswith("_probe_builtin_pp_sub_")]:
        try: getattr(builtins, _attr).unsubscribe()
        except Exception: pass
        try: delattr(builtins, _attr)
        except Exception: pass

    # SingleArticulation
    art = SingleArticulation(prim_path="/World/Franka", name="probe_builtin_franka")
    art.initialize(psv)
    steps.append(("art_init", "ok"))
    jp = art.get_joint_positions()
    steps.append(("joint_pos_initial_dof", str(len(jp)) if jp is not None else "None"))

    # Manual ParallelGripper construction (mirrors what Franka() class does internally)
    gripper = ParallelGripper(
        end_effector_prim_path="/World/Franka/panda_hand",
        joint_prim_names=["panda_finger_joint1", "panda_finger_joint2"],
        joint_opened_positions=np.array([0.04, 0.04]),
        joint_closed_positions=np.array([0.0, 0.0]),
        action_deltas=np.array([0.05, 0.05]),
    )
    gripper.initialize(physics_sim_view=psv, articulation_apply_action_func=art.apply_action,
                       get_joint_positions_func=art.get_joint_positions,
                       set_joint_positions_func=art.set_joint_positions,
                       dof_names=art.dof_names)
    steps.append(("gripper_init", "ok"))

    # PickPlaceController
    ctrl = PickPlaceController(
        name="probe_builtin_pp",
        gripper=gripper,
        robot_articulation=art,
    )
    art_ctrl = art.get_articulation_controller()
    steps.append(("controller_built", "ok"))

    # Cube position helper
    def cube_pos(path):
        p = stage.GetPrimAtPath(path)
        if not p or not p.IsValid(): return None
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        b = cache.ComputeWorldBound(p).ComputeAlignedRange()
        if b.IsEmpty(): return None
        c = b.GetMidpoint()
        return np.array([float(c[0]), float(c[1]), float(c[2])])

    # Per-step state — pick Cube_1 → drop in Bin
    S = {"done": False, "delivered_at_t": None, "samples": []}
    def on_step(dt):
        try:
            cp = cube_pos("/World/Cube_1")
            bp = cube_pos("/World/Bin")
            if cp is None or bp is None: return
            jp = art.get_joint_positions()
            if jp is None: return
            actions = ctrl.forward(
                picking_position=cp,
                placing_position=bp,
                current_joint_positions=jp,
                end_effector_offset=np.array([0, 0, 0.02]),
            )
            if actions is not None:
                art_ctrl.apply_action(actions)
            if ctrl.is_done() and not S["done"]:
                S["done"] = True
                S["delivered_at_t"] = float(tl.get_current_time())
        except Exception as _e:
            S.setdefault("step_errors", []).append(f"{type(_e).__name__}: {str(_e)[:120]}")

    import omni.physx
    sub = omni.physx.get_physx_interface().subscribe_physics_step_events(on_step)
    setattr(builtins, "_probe_builtin_pp_sub_World_Franka", sub)
    steps.append(("sub_installed", "ok"))

    # Run for DURATION seconds
    DURATION = {duration}
    real_start = __import__("time").time()
    while True:
        app.update()
        t = float(tl.get_current_time())
        if t >= DURATION: break
        if __import__("time").time() - real_start > DURATION + 90: break

    # Final cube position
    final = cube_pos("/World/Cube_1")
    bin_b = cache_bin = None
    p = stage.GetPrimAtPath("/World/Bin")
    if p and p.IsValid():
        cache_bin = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bb = cache_bin.ComputeWorldBound(p).ComputeAlignedRange()
        if not bb.IsEmpty():
            bin_b = {"min": [float(bb.GetMin()[i]) for i in range(3)],
                     "max": [float(bb.GetMax()[i]) for i in range(3)]}
    steps.append(("final_pos", str([round(float(x),3) for x in final]) if final is not None else "None"))
    steps.append(("bin_bbox", str(bin_b)))
    steps.append(("ctrl_is_done", str(ctrl.is_done())))
    steps.append(("delivered_at_t", str(S.get("delivered_at_t"))))
    steps.append(("step_errors", str(S.get("step_errors", []))[:200]))

    # Cleanup
    try: sub.unsubscribe()
    except Exception: pass
    delattr(builtins, "_probe_builtin_pp_sub_World_Franka")
    tl.stop()

except Exception as e:
    steps.append(("fatal", f"{{type(e).__name__}}: {{str(e)[:300]}}"))
    traceback.print_exc()

print(json.dumps(steps))
""".replace("{duration}", str(args.duration))

    res = await kit_tools.exec_sync(SETUP, timeout=args.duration + 120)
    out = (res.get("output") or "").strip()
    parsed = None
    for line in out.splitlines():
        if line.strip().startswith("[") and ", " in line and "(" in line:
            try:
                parsed = json.loads(line)
            except Exception:
                pass
    if not parsed:
        print(f"raw output: {out[-2500:]}")
        return 1

    print("\n=== pipeline trace ===")
    for step, val in parsed:
        marker = "✓" if step != "fatal" else "✗"
        print(f"  {marker} {step}: {val[:200]}")

    # Verdict: cube_final inside bin xy?
    final_pos = next((eval(v) for s, v in parsed if s == "final_pos" and v != "None"), None)
    bin_b = next((eval(v) for s, v in parsed if s == "bin_bbox" and v != "None"), None)
    print()
    if final_pos and bin_b:
        in_xy = (
            bin_b["min"][0] - 0.05 <= final_pos[0] <= bin_b["max"][0] + 0.05
            and bin_b["min"][1] - 0.05 <= final_pos[1] <= bin_b["max"][1] + 0.05
        )
        verdict = "DELIVERED" if in_xy else "NOT delivered"
        print(f"VERDICT: {verdict} (final {final_pos}, bin {bin_b})")
        return 0 if in_xy else 1
    print("VERDICT: insufficient data to verify delivery")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
