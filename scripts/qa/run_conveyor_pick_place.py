"""Deterministic end-to-end test of conveyor_pick_place.

Bypasses agent variability — builds the scene via a sequence of Kit RPC
patches that encode all known-good values from overnight debugging, then
installs the native PickPlaceController and verifies that 4/4 cubes
land in the bin.

This is the de-facto verified template: the ordered sequence of patches
below IS what needs to run for this scenario to work. A future
ChromaDB-backed template would retrieve and replay these same patches.

Usage:
    python -m scripts.qa.run_conveyor_pick_place [--wait 120]
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

KIT_RPC = "http://127.0.0.1:8001/exec_sync"


def kit(code: str, timeout: float = 60) -> dict:
    with httpx.Client(timeout=timeout + 10) as c:
        r = c.post(KIT_RPC, json={"code": code, "timeout": timeout})
        r.raise_for_status()
        return r.json()


# ═══════════════════════════════════════════════════════════════════════
# Patch 1: full stage reset + timeline stopped
# ═══════════════════════════════════════════════════════════════════════
PATCH_RESET = """
import omni.usd, omni.timeline, omni.kit.app, builtins
from pxr import UsdGeom
from omni.physx import get_physx_simulation_interface
tl = omni.timeline.get_timeline_interface()
tl.stop()
for _ in range(10): omni.kit.app.get_app().update()

# Clean all lingering subscriptions
for k in list(vars(builtins).keys()):
    if k.startswith(("_native_pp_", "_pick_place_", "_sensor_gated_",
                     "_motion_observer_", "_pick_sensor_")):
        s = getattr(builtins, k, None)
        if s and hasattr(s, "unsubscribe"):
            try: s.unsubscribe()
            except Exception:
                try: get_physx_simulation_interface().unsubscribe_physics_trigger_report_events(s)
                except Exception: pass
        try: delattr(builtins, k)
        except Exception: pass

ctx = omni.usd.get_context()
ctx.new_stage()
stage = ctx.get_stage()
for p in list(stage.Traverse()):
    s = str(p.GetPath())
    if s in ("/", "/World") or s.startswith(("/Render", "/OmniKit", "/OmniverseKit")):
        continue
    stage.RemovePrim(p.GetPath())
UsdGeom.Xform.Define(stage, "/World")
print("reset ok, timeline stopped")
"""


# ═══════════════════════════════════════════════════════════════════════
# Patch 2: build static scene (physics scene, ground, table, belt, bin,
# cubes, sensor, dome light) with timeline stopped
# ═══════════════════════════════════════════════════════════════════════
PATCH_SCENE = """
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, UsdLux, PhysxSchema, Sdf, Gf

stage = omni.usd.get_context().get_stage()

# ─── Physics scene ───
UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")

# ─── Ground plane (safety floor) ───
gp = UsdGeom.Cube.Define(stage, "/World/GroundPlane")
UsdGeom.Xformable(gp).AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.025))
UsdGeom.Xformable(gp).AddScaleOp().Set(Gf.Vec3f(5, 5, 0.025))
gp.CreateDisplayColorAttr([Gf.Vec3f(0.55, 0.55, 0.58)])
UsdPhysics.CollisionAPI.Apply(gp.GetPrim())

# ─── Table: 2×1×0.75 at (0,0,0.375), top at z=0.75 ───
tb = UsdGeom.Cube.Define(stage, "/World/Table")
UsdGeom.Xformable(tb).AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.375))
UsdGeom.Xformable(tb).AddScaleOp().Set(Gf.Vec3f(1.0, 0.5, 0.375))
tb.CreateDisplayColorAttr([Gf.Vec3f(0.6, 0.45, 0.35)])
UsdPhysics.CollisionAPI.Apply(tb.GetPrim())

# ─── Conveyor belt: 1.6×0.3×0.1 at (0,0.3,0.80), top at z=0.85 ───
cb = UsdGeom.Cube.Define(stage, "/World/ConveyorBelt")
UsdGeom.Xformable(cb).AddTranslateOp().Set(Gf.Vec3d(0, 0.3, 0.80))
UsdGeom.Xformable(cb).AddScaleOp().Set(Gf.Vec3f(0.8, 0.15, 0.05))
cb.CreateDisplayColorAttr([Gf.Vec3f(0.25, 0.25, 0.3)])
cb_prim = cb.GetPrim()
UsdPhysics.CollisionAPI.Apply(cb_prim)
rb = UsdPhysics.RigidBodyAPI.Apply(cb_prim)
rb.CreateKinematicEnabledAttr(True)
sv = PhysxSchema.PhysxSurfaceVelocityAPI.Apply(cb_prim)
sv.CreateSurfaceVelocityAttr(Gf.Vec3f(0.2, 0, 0))
sv.CreateSurfaceVelocityEnabledAttr(True)

# ─── Bin: 0.3×0.3×0.15 at (0,-0.4,0.75), bottom flush on table ───
# Parent Xform + 5 child Cubes (floor + 4 walls).
bin_x = UsdGeom.Xform.Define(stage, "/World/Bin")
UsdGeom.Xformable(bin_x).AddTranslateOp().Set(Gf.Vec3d(0, -0.4, 0.75))
parts = [
    # name, local translate, local scale (half-extents of Cube size=2)
    ("Floor", (0, 0, 0.005), (0.15, 0.15, 0.005)),
    ("WallN", (0, 0.15, 0.075), (0.15, 0.005, 0.075)),
    ("WallS", (0, -0.15, 0.075), (0.15, 0.005, 0.075)),
    ("WallE", (0.15, 0, 0.075), (0.005, 0.15, 0.075)),
    ("WallW", (-0.15, 0, 0.075), (0.005, 0.15, 0.075)),
]
for name, (lx, ly, lz), (sx, sy, sz) in parts:
    w = UsdGeom.Cube.Define(stage, f"/World/Bin/{name}")
    UsdGeom.Xformable(w).AddTranslateOp().Set(Gf.Vec3d(lx, ly, lz))
    UsdGeom.Xformable(w).AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))
    w.CreateDisplayColorAttr([Gf.Vec3f(0.35, 0.35, 0.4)])
    UsdPhysics.CollisionAPI.Apply(w.GetPrim())

# ─── 4 cubes on belt ───
# Use Cube size=default(2) + scale 0.025 → final 5cm per side.
# Center at z=0.876: bottom at 0.851, 1mm above belt top (0.85).
# PhysxRigidBodyAPI with sleepThreshold=0 — critical! Default PhysX
# sleeps resting bodies, which then ignore belt surface velocity.
for i, x in enumerate([-0.6, -0.4, -0.2, 0.0], 1):
    c = UsdGeom.Cube.Define(stage, f"/World/Cube_{i}")
    UsdGeom.Xformable(c).AddTranslateOp().Set(Gf.Vec3d(x, 0.3, 0.876))
    UsdGeom.Xformable(c).AddScaleOp().Set(Gf.Vec3f(0.025, 0.025, 0.025))
    c.CreateDisplayColorAttr([Gf.Vec3f(0.9, 0.2, 0.2)])
    cp = c.GetPrim()
    UsdPhysics.CollisionAPI.Apply(cp)
    UsdPhysics.RigidBodyAPI.Apply(cp)
    UsdPhysics.MassAPI.Apply(cp).CreateMassAttr(0.1)
    pxrb = PhysxSchema.PhysxRigidBodyAPI.Apply(cp)
    pxrb.CreateSleepThresholdAttr().Set(0.0)
    pxrb.CreateStabilizationThresholdAttr().Set(0.0)

# ─── Dome light ───
dl = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
dl.CreateIntensityAttr(1000.0)

# ─── Proximity sensor: 8cm trigger volume at (0.3,0.3,0.86) ───
sensor = UsdGeom.Cube.Define(stage, "/World/PickSensor")
UsdGeom.Xformable(sensor).AddTranslateOp().Set(Gf.Vec3d(0.3, 0.3, 0.86))
UsdGeom.Xformable(sensor).AddScaleOp().Set(Gf.Vec3f(0.04, 0.04, 0.04))
sp = sensor.GetPrim()
try: UsdGeom.Imageable(sp).MakeInvisible()
except Exception: pass
UsdPhysics.CollisionAPI.Apply(sp)
UsdPhysics.RigidBodyAPI.Apply(sp).CreateKinematicEnabledAttr(True)
PhysxSchema.PhysxTriggerAPI.Apply(sp)
sp.CreateAttribute("isaac_sensor:triggered", Sdf.ValueTypeNames.Bool).Set(False)
sp.CreateAttribute("isaac_sensor:last_triggered_path", Sdf.ValueTypeNames.String).Set("")

# ─── Trigger callback ───
import omni.physx, builtins
from omni.physx import get_physx_simulation_interface
from omni.physx.bindings._physx import TriggerEventType
_a_trig = sp.GetAttribute("isaac_sensor:triggered")
_a_last = sp.GetAttribute("isaac_sensor:last_triggered_path")
def _trig_cb(event):
    try:
        op = str(event.other_usd_path) if hasattr(event, 'other_usd_path') else ''
        et = int(event.event_type) if hasattr(event, 'event_type') else -1
        if et == int(TriggerEventType.TRIGGER_ON) and op.startswith('/World/Cube_'):
            _a_trig.Set(True); _a_last.Set(op)
        elif et == int(TriggerEventType.TRIGGER_OFF):
            _a_trig.Set(False)
    except Exception: pass
_sim = get_physx_simulation_interface()
_sub = _sim.subscribe_physics_trigger_report_events(_trig_cb)
setattr(builtins, "_pick_sensor_trig_sub", _sub)

print("scene built: 4 cubes on belt, bin on table, sensor armed, light on")
"""


# ═══════════════════════════════════════════════════════════════════════
# Patch 3: import + configure Franka (position, orient, variant, gains,
# home pose)
# ═══════════════════════════════════════════════════════════════════════
PATCH_FRANKA = """
import omni.usd, omni.kit.app, time
from pxr import UsdGeom, UsdPhysics, Gf, Usd
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.storage.native import get_assets_root_path

stage = omni.usd.get_context().get_stage()

# Reference Franka USD
root = get_assets_root_path()
usd_url = root + "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"
add_reference_to_stage(usd_path=usd_url, prim_path="/World/Franka")

# Pump app to resolve the reference composition
app = omni.kit.app.get_app()
for _ in range(60):
    app.update()
    n = len(list(stage.GetPrimAtPath("/World/Franka").GetAllChildren()))
    if n >= 8: break
    time.sleep(0.02)

prim = stage.GetPrimAtPath("/World/Franka")
print(f"Franka loaded, children={len(list(prim.GetAllChildren()))}")

# Position + orientation (90° around Z → faces +Y)
xf = UsdGeom.Xformable(prim)
xf.ClearXformOpOrder()
xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.75))
q = Gf.Quatd(0.7071067811865476, Gf.Vec3d(0, 0, 0.7071067811865476))
xf.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(q)

# Variant: switch to AlternateFinger (Default doesn't render fingers)
vs = prim.GetVariantSets().GetVariantSet("Gripper")
if vs: vs.SetVariantSelection("AlternateFinger")

# ArticulationRoot
if not prim.HasAPI(UsdPhysics.ArticulationRootAPI):
    UsdPhysics.ArticulationRootAPI.Apply(prim)

# Drive gains: Franka needs kp=6000/kd=500 for crisp RmpFlow tracking
for j in range(1, 8):
    for l in range(7):
        p = stage.GetPrimAtPath(f"/World/Franka/panda_link{l}/panda_joint{j}")
        if p.IsValid():
            drive = UsdPhysics.DriveAPI.Get(p, "angular")
            if drive:
                drive.GetStiffnessAttr().Set(6000.0)
                drive.GetDampingAttr().Set(500.0)
            break

# Home pose handled by native controller install (franka.set_joint_positions).
# Drive targets left at USD defaults so they don't fight RmpFlow.

# Convex-hull collision on all mesh descendants
from pxr import PhysxSchema
coll_count = 0
for child in list(Usd.PrimRange(prim))[1:]:
    if child.IsA(UsdGeom.Mesh):
        if not child.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(child)
        if not child.HasAPI(PhysxSchema.PhysxCollisionAPI):
            PhysxSchema.PhysxCollisionAPI.Apply(child)
        coll_count += 1

# ── Persist home joint config as PhysX reset target ───────────────────
# Drive target positions are transient — CURRENT commands. The
# PhysX-level "initial state" (what Stop reverts to) is separate.
# Without set_joints_default_state, Stop+Play reverts joints to URDF
# defaults (mostly zeros = extended arm) = wildly out-of-workspace pose.
# THIS is the main cause of "robot starts at wrong position after Stop".
import numpy as np
from isaacsim.core.prims import SingleArticulation
_home_rad_full = np.array([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04], dtype=np.float32)
try:
    _art = SingleArticulation(prim_path="/World/Franka", name="__franka_for_defaults__")
    # Can't initialize without playing — but we can write the USD-level defaults
    # via SingleArticulation's method that walks the prim stack. Instead, author
    # the initialPosition on each drive (Isaac Sim honors this on reset).
    _home_deg_full = [float(np.degrees(v)) if i < 7 else float(v) for i, v in enumerate(_home_rad_full)]
    _joint_paths = []
    for j in range(1, 8):
        for l in range(7):
            p = stage.GetPrimAtPath(f"/World/Franka/panda_link{l}/panda_joint{j}")
            if p.IsValid():
                _joint_paths.append((j, p))
                break
    for j, p in _joint_paths:
        drive = UsdPhysics.DriveAPI.Get(p, "angular")
        if drive:
            # Setting the drive target also acts as the "rest position" the
            # PD controller settles into on reset. This is persistent.
            drive.GetTargetPositionAttr().Set(_home_deg_full[j-1])
    # Finger joints: linear drives
    for fj in (1, 2):
        p = stage.GetPrimAtPath(f"/World/Franka/panda_hand/panda_finger_joint{fj}")
        if p.IsValid():
            drive = UsdPhysics.DriveAPI.Get(p, "linear")
            if drive:
                drive.GetTargetPositionAttr().Set(0.04)  # open
    print("(home-joint drive targets persisted — Stop+Play reverts to home pose)")
except Exception as _e:
    print(f"(home-joint persistence soft-fail: {_e})")

print(f"Franka configured: pos=(0,0,0.75), orient=+90°Z, variant=AlternateFinger, gains=6000/500, meshes_with_collision={coll_count}")
"""


# ═══════════════════════════════════════════════════════════════════════
# Patch 4: install native pick-place controller (uses generator)
# ═══════════════════════════════════════════════════════════════════════
def patch_controller(mode: str = "native", **extra) -> str:
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _gen_setup_pick_place_controller,
    )
    args = {
        "robot_path": "/World/Franka",
        "target_source": mode,
        "sensor_path": "/World/PickSensor",
        "belt_path": "/World/ConveyorBelt",
        "source_paths": [f"/World/Cube_{i}" for i in (1, 2, 3, 4)],
        "destination_path": "/World/Bin",
    }
    args.update(extra)
    return _gen_setup_pick_place_controller(args)


SNAPSHOT = """
import omni.usd, json
from pxr import UsdGeom
stage = omni.usd.get_context().get_stage()
f = stage.GetPrimAtPath("/World/Franka")
bp = stage.GetPrimAtPath("/World/Bin")
bb = UsdGeom.Imageable(bp).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
mn, mx = bb.GetMin(), bb.GetMax()
out = {}
for n in ["mode", "phase", "tick_count", "cubes_delivered", "error_count", "last_error"]:
    a = f.GetAttribute(f"ctrl:{n}")
    out[n] = a.Get() if a and a.IsDefined() else None
in_bin = []
for i in range(1, 5):
    c = stage.GetPrimAtPath(f"/World/Cube_{i}")
    t = UsdGeom.Xformable(c).ComputeLocalToWorldTransform(0).ExtractTranslation()
    pos = [round(float(t[j]), 3) for j in range(3)]
    is_in = (mn[0] <= t[0] <= mx[0] and mn[1] <= t[1] <= mx[1] and mn[2] <= t[2] <= mx[2] + 0.05)
    out[f"Cube_{i}"] = {"pos": pos, "in_bin": is_in}
    if is_in: in_bin.append(f"Cube_{i}")
out["cubes_in_bin"] = in_bin
out["n_in_bin"] = len(in_bin)
# Single-line JSON for machine parsing + indented for humans
print("BENCHMARK_JSON:" + json.dumps(out))
print(json.dumps(out, indent=2))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=120,
                    help="Seconds to wait for pick-place cycles after install")
    ap.add_argument("--controller", default="native",
                    choices=["native", "spline", "sensor_gated", "cube_tracking", "fixed_poses", "ros2_cmd", "curobo", "diffik", "osc", "auto"],
                    help="target_source to install")
    args = ap.parse_args()

    print("[1/5] reset stage")
    r = kit(PATCH_RESET)
    print("   ", (r.get("output") or "").strip())
    if not r.get("success"): return 1

    print("[2/5] build scene (table, belt, bin, cubes, sensor, light)")
    r = kit(PATCH_SCENE)
    print("   ", (r.get("output") or "").strip()[:300])
    if not r.get("success"):
        print("FAILED")
        print(r.get("output"))
        return 1

    print("[3/5] import + configure Franka")
    r = kit(PATCH_FRANKA, timeout=120)
    print("   ", (r.get("output") or "").strip()[:400])
    if not r.get("success"):
        print("FAILED")
        return 1

    print(f"[4/5] install {args.controller} pick-place controller")
    r = kit(patch_controller(mode=args.controller), timeout=60)
    out = (r.get("output") or "").strip()
    # Print status lines only, skip Kit UI noise
    for ln in out.split("\n"):
        if any(kw in ln for kw in ("physics body", "home joint", "rmpflow base",
                                     "IK solver", '"ok":', "warning", "spline",
                                     "scene reset", "scipy")):
            print("   ", ln[:200])
    if not r.get("success"):
        print("FAILED")
        return 1

    print(f"[5/5] wait {args.wait}s for pick cycles (each cycle ~15s)")
    time.sleep(args.wait)

    print("\n[snapshot]")
    r = kit(SNAPSHOT)
    out = (r.get("output") or "").strip()
    print(out)
    # Extract single-line BENCHMARK_JSON for machine consumption
    bm_lines = [ln for ln in out.split("\n") if ln.startswith("BENCHMARK_JSON:")]
    if bm_lines:
        data = json.loads(bm_lines[0][len("BENCHMARK_JSON:"):])
    else:
        data = {}
    n_bin = data.get("n_in_bin", 0)
    print(f"\nVERDICT: {n_bin}/4 cubes delivered to bin")
    return 0 if n_bin == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
