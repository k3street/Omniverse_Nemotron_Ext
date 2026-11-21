"""Waits for a user-launched Isaac Sim GUI (:8001) to finish full startup (past Kit's initial
`World0.usd` reset), then loads `r2_hybrid_workcell.usda`, verifies it stays open, wires the
ROS 2 Jazzy `/tf` + `/clock` ActionGraph, frames the camera, and starts continuous looping playback.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import urllib.request

from scripts.cad_to_sim_pipeline import _resolve_kit_rpc_url

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKCELL_USD = (
    REPO_ROOT
    / "workspace"
    / "r2_hybrid_showcase"
    / "03_isaac_sim_7a_workcell"
    / "r2_hybrid_workcell.usda"
)


def _post_json(url: str, payload: dict, timeout: float = 45.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    print("Waiting for Isaac Sim + omni.isaac.assist RPC server (:8001) to finish full startup...")
    deadline = time.monotonic() + 240.0
    rpc_url = _resolve_kit_rpc_url()
    ready = False
    while time.monotonic() < deadline:
        rpc_url = _resolve_kit_rpc_url()
        try:
            with urllib.request.urlopen(f"{rpc_url}/context", timeout=3) as resp:
                ctx_data = json.loads(resp.read().decode("utf-8"))
                # Wait until Kit has created its initial startup stage (prim_count > 0)
                if resp.status == 200 and int(ctx_data.get("stage", {}).get("prim_count", 0)) > 0:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(2.0)

    if not ready:
        print("Timed out waiting for Isaac Sim RPC server.")
        return 1

    # Give Kit 2 extra seconds in case startup post-load hooks are still settling
    time.sleep(2.0)
    usd_posix = WORKCELL_USD.as_posix()

    open_and_graph_code = f"""
import json
import omni.usd
import omni.graph.core as og
from pxr import Sdf

ctx = omni.usd.get_context()
if not ctx.get_stage_url().replace("\\\\", "/").endswith("r2_hybrid_workcell.usda"):
    ctx.open_stage("{usd_posix}")

stage = ctx.get_stage()
keys = og.Controller.Keys
graph_path = "/World/ROS2R2GripperGraph"
if stage and not stage.GetPrimAtPath(graph_path).IsValid():
    og.Controller.edit(
        {{"graph_path": graph_path, "evaluator_name": "execution"}},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
                ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("OnPlaybackTick.outputs:tick", "PublishTF.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
                ("ROS2Context.outputs:context", "PublishTF.inputs:context"),
                ("ReadSimTime.outputs:simulationTime", "PublishClock.inputs:timeStamp"),
                ("ReadSimTime.outputs:simulationTime", "PublishTF.inputs:timeStamp"),
            ],
        }},
    )
    tf_prim = stage.GetPrimAtPath(graph_path + "/PublishTF")
    if tf_prim.IsValid():
        rel = tf_prim.GetRelationship("inputs:targetPrims")
        if not rel:
            rel = tf_prim.CreateRelationship("inputs:targetPrims")
        rel.SetTargets([
            Sdf.Path("/World/R2Workcell/palm_body"),
            Sdf.Path("/World/R2Workcell/thumb_dist"),
            Sdf.Path("/World/R2Workcell/finger_a_dist"),
            Sdf.Path("/World/R2Workcell/finger_b_dist"),
            Sdf.Path("/World/R2Workcell/r2_telemetry_cartridge"),
            Sdf.Path("/World/R2Workcell/r2_mating_dock"),
        ])
print(json.dumps({{"stage_url": ctx.get_stage_url(), "prims": len(list(stage.Traverse())) if stage else 0}}))
"""

    # Retry up to 4 times if Kit's late startup `new_stage()` resets the stage back to anon:World0.usd
    for _ in range(4):
        _post_json(f"{rpc_url}/exec_sync", {"code": open_and_graph_code, "timeout": 45}, timeout=50)
        time.sleep(1.5)
        with urllib.request.urlopen(f"{rpc_url}/context", timeout=5) as resp:
            cur_url = json.loads(resp.read().decode("utf-8")).get("stage", {}).get("stage_url", "")
        if cur_url.replace("\\", "/").endswith("r2_hybrid_workcell.usda"):
            break

    _post_json(
        f"{rpc_url}/set_camera_pose",
        {"eye": [0.42, -0.46, 0.33], "target": [0.065, 0.0, 0.115]},
        timeout=15,
    )

    play_code = """
import json, omni.timeline, omni.usd
tl = omni.timeline.get_timeline_interface()
tl.set_looping(True)
tl.set_end_time(142.0 / 30.0)
tl.play()
print(json.dumps({"stage_url": omni.usd.get_context().get_stage_url(), "playing": tl.is_playing()}))
"""
    res = _post_json(f"{rpc_url}/exec_sync", {"code": play_code, "timeout": 15}, timeout=20)
    print("Isaac Sim R2 Gripper workcell loaded, framed, and playing live:", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
