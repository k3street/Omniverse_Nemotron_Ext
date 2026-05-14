"""
probe_world_physics_view.py — minimal repro for the World.physics_sim_view = None
problem in CP-06 / _gen_pick_place_builtin.

Goal: find a sequence of (timeline.play/stop, World creation, world.reset, scene.add)
that leaves world.physics_sim_view as a valid (non-None) object so that
SingleArticulation.initialize() doesn't crash with
'NoneType has no attribute is_homogeneous'.

This is a DIAGNOSTIC ONLY script — does not modify any production code.

Usage:
  python scripts/qa/probe_world_physics_view.py
"""
from __future__ import annotations

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


SETUP_FRANKA = """
import omni.usd
ctx = omni.usd.get_context()
ctx.new_stage()
from pxr import UsdGeom
UsdGeom.Xform.Define(ctx.get_stage(), '/World')
"""


async def reset_and_build():
    """Fresh stage with just a Franka via robot_wizard."""
    res = await kit_tools.exec_sync(SETUP_FRANKA, timeout=15)
    if not res.get("success"):
        raise RuntimeError(f"reset failed: {res.get('output')}")
    await execute_tool_call("create_prim", {
        "prim_path": "/World/DomeLight", "prim_type": "DomeLight",
    })
    await execute_tool_call("set_attribute", {
        "prim_path": "/World/DomeLight", "attr_name": "inputs:intensity", "value": 1000.0,
    })
    await execute_tool_call("set_physics_scene_config", {
        "config": {"enable_gpu_dynamics": False, "broadphase_type": "MBP"},
    })
    await execute_tool_call("robot_wizard", {
        "robot_name": "franka_panda",
        "dest_path": "/World/Franka",
        "position": [0, 0, 0.0],
        "orientation": [1.0, 0, 0, 0],
    })


SEQUENCES = [
    {
        "name": "A_baseline_no_play",
        "desc": "World.instance() or World(); add Franka; world.reset() — no timeline play",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json, traceback
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
tl.stop()
app = omni.kit.app.get_app()
for _ in range(3): app.update()

steps = []
try:
    world = World.instance() or World()
    steps.append(("world", str(type(world).__name__)))
    steps.append(("psv_before_reset", str(world.physics_sim_view)))
    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    steps.append(("franka", "ok"))
    world.scene.add(f)
    steps.append(("scene.add", "ok"))
    world.reset()
    steps.append(("reset", "ok"))
    steps.append(("psv_after_reset", str(world.physics_sim_view)[:80]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))

print(json.dumps(steps))
""",
    },
    {
        "name": "B_play_before_reset",
        "desc": "World; add Franka; tl.play() + pump; world.reset()",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
tl.stop()
app = omni.kit.app.get_app()
for _ in range(3): app.update()

steps = []
try:
    world = World.instance() or World()
    steps.append(("world", "ok"))
    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    world.scene.add(f)
    steps.append(("scene.add", "ok"))
    tl.play()
    for _ in range(8): app.update()
    steps.append(("play_pumped", "ok"))
    steps.append(("psv_before_reset", str(world.physics_sim_view)[:80]))
    world.reset()
    steps.append(("reset", "ok"))
    steps.append(("psv_after_reset", str(world.physics_sim_view)[:80]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))

print(json.dumps(steps))
""",
    },
    {
        "name": "C_world_reset_first_then_add",
        "desc": "World; world.reset() FIRST (init psv); then add Franka; second reset",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
tl.stop()
app = omni.kit.app.get_app()
for _ in range(3): app.update()

steps = []
try:
    world = World.instance() or World()
    steps.append(("world", "ok"))
    world.reset()
    steps.append(("reset_first", "ok"))
    steps.append(("psv_after_first_reset", str(world.physics_sim_view)[:80]))
    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    world.scene.add(f)
    steps.append(("scene.add", "ok"))
    world.reset()
    steps.append(("reset_second", "ok"))
    steps.append(("psv_after_second_reset", str(world.physics_sim_view)[:80]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))

print(json.dumps(steps))
""",
    },
    {
        "name": "D_simulation_manager_init_first",
        "desc": "SimulationManager.initialize_physics() before World",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka
try:
    from isaacsim.core.simulation_manager import SimulationManager
except Exception:
    from isaacsim.core.api.simulation_manager import SimulationManager

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
tl.stop()
app = omni.kit.app.get_app()
for _ in range(3): app.update()

steps = []
try:
    SimulationManager.initialize_physics()
    steps.append(("sim_mgr_init", "ok"))
    psv_pre = SimulationManager.get_physics_sim_view()
    steps.append(("psv_after_sm_init", str(psv_pre)[:80]))

    world = World.instance() or World()
    steps.append(("world", "ok"))
    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    world.scene.add(f)
    steps.append(("scene.add", "ok"))
    world.reset()
    steps.append(("reset", "ok"))
    steps.append(("psv_after_reset", str(world.physics_sim_view)[:80]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))

print(json.dumps(steps))
""",
    },
    {
        "name": "E_play_init_stop_then_setup",
        "desc": "Play+stop cycle to init psv, then world.reset",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka

stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()

steps = []
try:
    # Play once to ensure physics is initialized
    tl.play()
    for _ in range(10): app.update()
    steps.append(("play_initial", "ok"))

    world = World.instance() or World()
    steps.append(("world", "ok"))
    steps.append(("psv_after_world", str(world.physics_sim_view)[:80]))

    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    world.scene.add(f)
    steps.append(("scene.add", "ok"))

    world.reset()
    steps.append(("reset", "ok"))
    steps.append(("psv_after_reset", str(world.physics_sim_view)[:80]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))

print(json.dumps(steps))
""",
    },
]


async def main() -> int:
    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive")
        return 2

    # New sequence F based on diagnostic insights from D/E:
    # psv is valid after SimulationManager.init OR play. But articulation's
    # _physics_view._backend is None until PhysX has actually instantiated
    # the articulation (which happens after physics_step events fire on a
    # playing timeline). So: play BEFORE adding to scene + reset.
    SEQUENCES.append({
        "name": "F_play_before_scene_add",
        "desc": "tl.play+pump → World → scene.add → reset (lets PhysX bind articulation)",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.api import World
from isaacsim.robot.manipulators.examples.franka import Franka

PROBE_NAME = "probe_F"
stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()

steps = []
try:
    tl.play()
    for _ in range(15): app.update()
    steps.append(("play_pumped", "ok"))
    world = World.instance() or World()
    steps.append(("world", "ok"))
    steps.append(("psv_pre_add", str(world.physics_sim_view)[:80]))
    f = Franka(prim_path="/World/Franka", name=PROBE_NAME)
    world.scene.add(f)
    steps.append(("scene.add", "ok"))
    # Pump more to let PhysX bind the articulation
    for _ in range(8): app.update()
    world.reset()
    steps.append(("reset", "ok"))
    steps.append(("psv_after_reset", str(world.physics_sim_view)[:80]))
    # Verify articulation is bound
    try:
        jp = f.get_joint_positions()
        steps.append(("joint_pos", str(jp)[:120] if jp is not None else "None"))
    except Exception as e:
        steps.append(("joint_pos_err", str(e)[:200]))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))
print(json.dumps(steps))
""",
    })
    SEQUENCES.append({
        "name": "G_no_world_at_all",
        "desc": "Skip World entirely; play timeline, init SimulationManager, init Franka via SingleArticulation only",
        "code": """
import omni.usd, omni.timeline, omni.kit.app, json
from isaacsim.core.prims import SingleArticulation
try:
    from isaacsim.core.simulation_manager import SimulationManager
except Exception:
    from isaacsim.core.api.simulation_manager import SimulationManager

PROBE_NAME = "probe_G"
stage = omni.usd.get_context().get_stage()
tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()

steps = []
try:
    tl.play()
    for _ in range(10): app.update()
    if SimulationManager.get_physics_sim_view() is None:
        SimulationManager.initialize_physics()
    psv = SimulationManager.get_physics_sim_view()
    steps.append(("psv", str(psv)[:80]))
    art = SingleArticulation(prim_path="/World/Franka", name=PROBE_NAME)
    art.initialize(psv)
    steps.append(("art_init", "ok"))
    jp = art.get_joint_positions()
    steps.append(("joint_pos", str(jp)[:120] if jp is not None else "None"))
except Exception as e:
    steps.append(("error", f"{type(e).__name__}: {str(e)[:200]}"))
print(json.dumps(steps))
""",
    })

    # Add unique PROBE_NAME prefix per sequence
    for i, seq in enumerate(SEQUENCES):
        if "PROBE_NAME" not in seq["code"]:
            seq["code"] = f'PROBE_NAME = "probe_{seq["name"][:1]}_{i}"\n' + seq["code"]

    results = []
    for seq in SEQUENCES:
        print(f"\n=== {seq['name']} ===")
        print(f"    {seq['desc']}")
        await reset_and_build()
        res = await kit_tools.exec_sync(seq["code"], timeout=30)
        out = (res.get("output") or "").strip()
        last_json = None
        for line in out.splitlines():
            if line.strip().startswith("[") and "(" in line:
                try:
                    last_json = json.loads(line)
                except Exception:
                    pass
        if last_json:
            for step, val in last_json:
                marker = "✓" if step != "error" else "✗"
                print(f"  {marker} {step}: {val[:100]}")
            ok = not any(s == "error" for s, _ in last_json)
            psv_after = next((v for s, v in last_json if "psv_after_reset" in s), None)
            results.append({"seq": seq["name"], "ok": ok, "psv_after_reset": psv_after})
        else:
            print(f"  no JSON parsed; raw: {out[:300]}")
            results.append({"seq": seq["name"], "ok": False, "psv_after_reset": None})

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        ok_marker = "✓" if r["ok"] else "✗"
        psv = r["psv_after_reset"] or "(none)"
        print(f"{ok_marker} {r['seq']:<35} psv_after_reset = {psv[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
