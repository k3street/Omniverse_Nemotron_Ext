#!/usr/bin/env python3
"""Spawn a walking person in the LIVE Isaac Sim session (Kit RPC :8001).

Builds a fresh stage: ground + lights + the Biped_Setup character with a
walk cycle bound directly to its skeleton, timeline playing on loop. The
walk clips carry root motion — the person actually crosses the floor.

Hard-won bindings knowledge (why this script looks the way it does):
  - Biped_Setup's AnimationGraph owns the skeleton via a SESSION-LAYER
    skel:animationSource -> AnimGraphOutputPose; root-layer overrides
    lose. Bind the clip in the session layer and remove
    AnimationGraphAPI from the SkelRoot.
  - The stock DHGen characters (Collected_People/*.usd) use a DIFFERENT
    78-joint rig than the 81-joint Biped clips — they need
    omni.anim.retarget / IRA; the Biped mannequin plays clips natively.
  - Isaac's RTX startup dies at ~12 s if vLLM holds its GPU reservation
    on the GB10 — stop the judge stack before launching Isaac.

Usage (Isaac must be running):
    python scripts/spawn_walking_person.py [--clip stand_walk_1] [--port 8001]
Clips: stand_walk_1..5,7 (+_mirror), stand_idle_loop, LookAround, Sit,
       stand_idle_wave_loop
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

PEOPLE = "/home/kimate/Desktop/assets/Collected_People"

SCENE_CODE = '''
import omni.usd, omni.timeline
from pxr import Usd, UsdGeom, UsdSkel, UsdLux, Sdf
ctx = omni.usd.get_context()
ctx.new_stage()
stage = ctx.get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
world = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(world.GetPrim())
UsdLux.DistantLight.Define(stage, "/World/Sun").CreateIntensityAttr(3000)
UsdLux.DomeLight.Define(stage, "/World/Dome").CreateIntensityAttr(800)
ground = UsdGeom.Cube.Define(stage, "/World/Ground")
ground.GetSizeAttr().Set(1.0)
gx = UsdGeom.XformCommonAPI(ground.GetPrim())
gx.SetScale((8.0, 8.0, 0.02))
gx.SetTranslate((0.0, 0.0, -0.01))
person = stage.DefinePrim("/World/Person", "Xform")
person.GetReferences().AddReference("{people}/Biped_Setup.usd")
UsdGeom.XformCommonAPI(person).SetRotate((90, 0, 0))  # Y-up asset, Z-up stage
sr = stage.GetPrimAtPath("/World/Person/biped_demo_meters")
skel = stage.GetPrimAtPath("/World/Person/biped_demo_meters/Root")
sr.RemoveAppliedSchema("AnimationGraphAPI")
clip = Sdf.Path("/World/Person/CharacterAnimation/Animation/{clip}_skelanim")
with Usd.EditContext(stage, stage.GetSessionLayer()):
    UsdSkel.BindingAPI.Apply(skel).CreateAnimationSourceRel().SetTargets([clip])
stage.SetStartTimeCode(0)
stage.SetEndTimeCode(776)
stage.SetTimeCodesPerSecond(30)
cam = UsdGeom.Camera.Define(stage, "/World/ShotCam")
from pxr import Gf
view = Gf.Matrix4d()
view.SetLookAt(Gf.Vec3d(9.0, -9.0, 4.5), Gf.Vec3d(0.0, 3.0, 0.9), Gf.Vec3d(0, 0, 1))
xf = UsdGeom.Xformable(cam.GetPrim())
xf.ClearXformOpOrder()
xf.AddTransformOp().Set(view.GetInverse())
import omni.kit.viewport.utility as vu
vu.get_active_viewport().camera_path = "/World/ShotCam"
tl = omni.timeline.get_timeline_interface()
tl.set_start_time(0.0)
tl.set_end_time(776.0 / 30.0)
tl.set_looping(True)
tl.play()
print("person walking:", tl.is_playing(), "| clip: {clip}")
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clip", default="stand_walk_1")
    ap.add_argument("--port", default="8001")
    args = ap.parse_args()
    code = SCENE_CODE.format(people=PEOPLE, clip=args.clip)
    req = urllib.request.Request(
        f"http://127.0.0.1:{args.port}/exec_sync",
        json.dumps({"code": code, "description": "spawn walking person"}).encode(),
        {"Content-Type": "application/json"})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=180).read())
    except OSError as e:
        print(f"Isaac Kit RPC not reachable on :{args.port} — launch Isaac "
              f"first (./launch_isaac.sh; stop vLLM judges if RTX crashes "
              f"at startup): {e}")
        return 1
    print(out.get("output", out))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
