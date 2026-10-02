#!/usr/bin/env python3
"""Animate every joint of an articulated asset in PhysX and record it.

Headless Isaac Sim (run with the build's python.sh, under the machine's
single Isaac slot). For each moving joint the asset declares, drive it
through its range and back while a camera records, and log the joint's
MEASURED position every frame. The video is for people; the log is the
evidence: tracking error per joint, and for gated joints proof that the
gate holds.

Gates (customData `simReady:gate` on a joint, written by add_mechanism.py)
order the motion: before a gated joint moves, it is first pushed with its
actuator at rest (it must not open), then the actuator is worked and the
joint swings, the actuator is released, and the joint returns (a latch cams
shut on its own). Joints that follow another (PhysX mimic joints) are never
driven directly.

Usage:
    python.sh scripts/animate_asset.py <queue_asset_id | file.usd> [--out DIR]
        [--seconds-per-joint 6] [--fps 30] [--width 1280 --height 720]

Writes <out>/<asset>.mp4, frames/, joints.csv, contact_sheet.png and
summary.json (per-joint reach and tracking error; gate check result).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
OUT_ROOT = REPO / "workspace" / "asset_animations"

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("asset")
ap.add_argument("--out", type=Path)
ap.add_argument("--seconds-per-joint", type=float, default=6.0)
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
ap.add_argument("--push-torque", type=float, default=80.0,
                help="N m (revolute) used to test a gate: about a firm one-hand shove on a door")
ap.add_argument("--push-force", type=float, default=100.0, help="N (prismatic) used to test a gate")
args = ap.parse_args()

if args.asset.endswith((".usd", ".usda", ".usdc", ".usdz")):
    usd_path, asset_id = str(Path(args.asset).resolve()), Path(args.asset).stem
else:
    asset_id = args.asset
    usd_path = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())["file"]
out_dir = (args.out or OUT_ROOT / asset_id).resolve()
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

omni.usd.get_context().open_stage(usd_path)
for _ in range(20):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
PHYSICS_HZ = 60
DT = 1.0 / PHYSICS_HZ


# --- what moves ---------------------------------------------------------------

def unit_rot(m: Gf.Matrix4d) -> Gf.Rotation:
    m = Gf.Matrix4d(m)
    m.Orthonormalize()
    return m.ExtractRotation()


def _authored_drive(prim, revolute):
    d = UsdPhysics.DriveAPI.Get(prim, "angular" if revolute else "linear")
    if not d:
        return None
    return (d.GetStiffnessAttr().Get() or 0.0, d.GetDampingAttr().Get() or 0.0)


xf = UsdGeom.XformCache(Usd.TimeCode.Default())
joints = []
for prim in stage.Traverse():
    if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
        continue
    j = UsdPhysics.Joint(prim)
    b0, b1 = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
    if not b0 or not b1:
        continue
    revolute = prim.IsA(UsdPhysics.RevoluteJoint)
    lower = prim.GetAttribute("physics:lowerLimit").Get()
    upper = prim.GetAttribute("physics:upperLimit").Get()
    if lower is None or upper is None or not math.isfinite(lower) or not math.isfinite(upper):
        lower, upper = (-180.0, 180.0) if revolute else (-0.1, 0.1)
    gate = prim.GetCustomDataByKey("simReady:gate")
    w0, w1 = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(b0[0])), xf.GetLocalToWorldTransform(stage.GetPrimAtPath(b1[0]))
    joints.append({
        "name": prim.GetName(), "path": str(prim.GetPath()), "revolute": revolute,
        "axis": {"X": 0, "Y": 1, "Z": 2}[prim.GetAttribute("physics:axis").Get() or "X"],
        "lower": float(lower), "upper": float(upper),
        "body0": str(b0[0]), "body1": str(b1[0]),
        # bodies carry the wrapper's scale; PhysX poses do not, so keep it
        "scale0": [Gf.Vec3d(*w0.GetRow3(k)).GetLength() for k in range(3)],
        "scale1": [Gf.Vec3d(*w1.GetRow3(k)).GetLength() for k in range(3)],
        "lp0": Gf.Vec3d(j.GetLocalPos0Attr().Get()), "lp1": Gf.Vec3d(j.GetLocalPos1Attr().Get()),
        "lr0": Gf.Rotation(Gf.Quatd(j.GetLocalRot0Attr().Get())), "lr1": Gf.Rotation(Gf.Quatd(j.GetLocalRot1Attr().Get())),
        "follower": any(s.startswith("PhysxMimicJointAPI") for s in prim.GetAppliedSchemas()),
        "gate": json.loads(gate) if isinstance(gate, str) else gate,
        "authored": _authored_drive(prim, revolute),
    })
by_name = {j["name"]: j for j in joints}
actuators = {j["gate"]["actuator_joint"] for j in joints if j["gate"]}
primaries = [j for j in joints if not j["follower"] and j["name"] not in actuators]
if not joints:
    raise SystemExit(f"{asset_id}: no revolute or prismatic joints to animate")


def body_pose(path: str):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return Gf.Vec3d(*r["position"]), Gf.Rotation(Gf.Quatd(q[3], q[0], q[1], q[2]))


def measure(j) -> float:
    """Joint position from the two bodies' live poses and the joint frames."""
    p0, r0 = body_pose(j["body0"])
    p1, r1 = body_pose(j["body1"])
    f0 = j["lr0"] * r0
    f1 = j["lr1"] * r1
    if j["revolute"]:
        rel = (f1 * f0.GetInverse()).GetQuat()
        axis_w = f0.TransformDir(Gf.Vec3d(*[1.0 if k == j["axis"] else 0.0 for k in range(3)]))
        imag = rel.GetImaginary()
        a = math.degrees(2.0 * math.atan2(Gf.Dot(imag, axis_w), rel.GetReal()))
        # poses only give the angle mod 360; a screw turns many times, so
        # unwrap against the previous frame's reading
        prev = j.get("_last")
        if prev is not None:
            a += 360.0 * round((prev - a) / 360.0)
        j["_last"] = a
        return a
    a0 = p0 + r0.TransformDir(Gf.CompMult(j["lp0"], Gf.Vec3d(*j["scale0"])))
    a1 = p1 + r1.TransformDir(Gf.CompMult(j["lp1"], Gf.Vec3d(*j["scale1"])))
    axis_w = f0.TransformDir(Gf.Vec3d(*[1.0 if k == j["axis"] else 0.0 for k in range(3)]))
    return Gf.Dot(a1 - a0, axis_w)


# --- drives (session layer only: the asset file is never changed) --------------

def set_drive(j, target, stiffness=None, damping=None, max_force=None):
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(j["path"])
        d = UsdPhysics.DriveAPI.Apply(prim, "angular" if j["revolute"] else "linear")
        d.CreateTargetPositionAttr().Set(float(target))
        if stiffness is not None:
            d.CreateStiffnessAttr().Set(float(stiffness))
        if damping is not None:
            d.CreateDampingAttr().Set(float(damping))
        if max_force is not None:
            d.CreateMaxForceAttr().Set(float(max_force))


def gains(j):
    # stiff enough to track a smooth sweep; per degree for revolute drives
    return (30.0, 3.0) if j["revolute"] else (2.0e4, 400.0)


def rest_of(j):
    return min(max(0.0, j["lower"]), j["upper"])


def ease(a, b, u):
    u = min(1.0, max(0.0, u))
    return a + (b - a) * u * u * (3 - 2 * u)


# --- the plan: a list of (seconds, {joint: (target_fn(u), force_cap)}) --------

segments = []  # each: dict(seconds, label, moves={name: (start, end, cap)})
T = args.seconds_per_joint
for j in primaries:
    rest = rest_of(j)
    far = j["upper"] if abs(j["upper"] - rest) >= abs(j["lower"] - rest) else j["lower"]
    if j["gate"]:
        a = by_name[j["gate"]["actuator_joint"]]
        engage = float(j["gate"]["engage"])
        try_cap = args.push_torque if j["revolute"] else args.push_force
        mid = rest + 0.6 * (far - rest)
        segments += [
            {"seconds": 1.0, "label": f"{j['name']}: at rest", "moves": {}},
            {"seconds": 1.5, "label": f"{j['name']}: pushed with {a['name']} at rest (gate check)",
             "moves": {j["name"]: (rest, mid, try_cap)}, "gate_check": j["name"]},
            {"seconds": 0.6, "label": f"{a['name']} engaged", "moves": {a["name"]: (rest_of(a), engage, None)}},
            {"seconds": 0.4 * T, "label": f"{j['name']} opens", "moves": {j["name"]: (rest, far, None)}},
            # released onto its own spring, so a closing latch can cam it back
            {"seconds": 0.5, "label": f"{a['name']} released", "moves": {a["name"]: (engage, rest_of(a), None)},
             "authored_gains": [a["name"]]},
            {"seconds": 0.8, "label": "hold open", "moves": {}},
            # aim past closed, as a door closer's preload does: the limit
            # stops the leaf and the leftover torque finishes camming the latch
            {"seconds": 0.4 * T, "label": f"{j['name']} closes (re-latches)",
             "moves": {j["name"]: (far, rest - 0.1 * (far - rest), None)}},
            {"seconds": 1.0, "label": "closed", "moves": {}},
        ]
    elif not j["revolute"] and abs(j["upper"] - j["lower"]) <= 0.01:
        # a button or a short slider: a quick press, not a slow sweep (a
        # remote has dozens)
        segments += [
            {"seconds": 0.3, "label": f"{j['name']} pressed", "moves": {j["name"]: (rest, far, None)}},
            {"seconds": 0.2, "label": "hold", "moves": {}},
            {"seconds": 0.3, "label": f"{j['name']} released", "moves": {j["name"]: (far, rest, None)}},
        ]
    else:
        segments += [
            {"seconds": 0.5, "label": f"{j['name']}: at rest", "moves": {}},
            {"seconds": 0.4 * T, "label": f"{j['name']} to {far:g}", "moves": {j["name"]: (rest, far, None)}},
            {"seconds": 0.5, "label": "hold", "moves": {}},
            {"seconds": 0.4 * T, "label": f"{j['name']} back", "moves": {j["name"]: (far, rest, None)}},
        ]

# --- scene dressing and camera (session layer) --------------------------------

bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
rng = bbox.ComputeWorldBound(stage.GetDefaultPrim() or stage.GetPseudoRoot()).ComputeAlignedRange()
lo, hi = rng.GetMin(), rng.GetMax()
center = (lo + hi) * 0.5
size = hi - lo
radius = 0.5 * size.GetLength()
# Room for the moving parts: a door swings out by its own width.
reach = max(size[0], size[1])
with Usd.EditContext(stage, stage.GetSessionLayer()):
    UsdLux.DomeLight.Define(stage, "/AnimView/Dome").CreateIntensityAttr(1200)
    key = UsdLux.DistantLight.Define(stage, "/AnimView/Key")
    key.CreateIntensityAttr(2500)
    UsdGeom.XformCommonAPI(key.GetPrim()).SetRotate(Gf.Vec3f(-45, 0, -35))
    floor = UsdGeom.Mesh.Define(stage, "/AnimView/Floor")  # visual only: no collider
    f = 4.0 * (radius + reach)
    z0 = lo[2] - 0.001
    floor.CreatePointsAttr([(center[0] - f, center[1] - f, z0), (center[0] + f, center[1] - f, z0),
                            (center[0] + f, center[1] + f, z0), (center[0] - f, center[1] + f, z0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    floor.CreateDisplayColorAttr([(0.35, 0.35, 0.37)])
    # An asset that is not anchored to the world needs something to stand on;
    # an anchored one (a door frame) does not, and a collider flush with a
    # leaf's bottom edge would drag on it.
    anchored = any(
        p.IsA(UsdPhysics.Joint) and not UsdPhysics.Joint(p).GetBody0Rel().GetTargets()
        and UsdPhysics.Joint(p).GetBody1Rel().GetTargets() for p in stage.Traverse())
    if not anchored:
        ground = UsdGeom.Cube.Define(stage, "/AnimView/Ground")
        ground.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(ground.GetPrim()).SetTranslate(Gf.Vec3d(center[0], center[1], z0 - 0.05))
        UsdGeom.XformCommonAPI(ground.GetPrim()).SetScale(Gf.Vec3f(2 * f, 2 * f, 0.1))
        ground.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    cam = UsdGeom.Camera.Define(stage, "/AnimView/Camera")
    cam.CreateFocalLengthAttr(18.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
cam_xf = UsdGeom.Xformable(cam.GetPrim())


def view_azimuth() -> float:
    """Look from the side a revolute leaf swings AWAY from, so it opens into
    view instead of edge-on; otherwise a standard 3/4 view."""
    for j in primaries:
        if j["revolute"] and j["axis"] == 2:
            far = j["upper"] if abs(j["upper"]) >= abs(j["lower"]) else j["lower"]
            p1 = body_pose(j["body1"])
            if p1:
                # swing direction of the child's centre about the hinge
                c1 = bbox.ComputeWorldBound(stage.GetPrimAtPath(j["body1"])).ComputeAlignedRange().GetMidpoint()
                hinge = xf.GetLocalToWorldTransform(stage.GetPrimAtPath(j["body0"])).Transform(j["lp0"])
                r = (c1[0] - hinge[0], c1[1] - hinge[1])
                swing = (-r[1], r[0]) if far > 0 else (r[1], -r[0])
                return math.degrees(math.atan2(-swing[1], -swing[0])) + 30.0
    return -55.0


AZ0 = view_azimuth()
hfov = 2 * math.atan(20.955 / (2 * 18.0))
DIST = 1.6 * (radius + 0.5 * reach) / math.tan(hfov / 2)
ELEV = math.radians(50)  # high enough that swings read from above, not edge-on


def place_camera(u: float):
    az = math.radians(AZ0 - 10 + 20 * u)  # a gentle orbit: before/after frames stay comparable
    target = Gf.Vec3d(center[0], center[1], lo[2] + 0.5 * size[2])
    eye = target + Gf.Vec3d(DIST * math.cos(ELEV) * math.cos(az), DIST * math.cos(ELEV) * math.sin(az), DIST * math.sin(ELEV))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        cam_xf.ClearXformOpOrder()
        cam_xf.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())


place_camera(0.0)
render_product = rep.create.render_product("/AnimView/Camera", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([render_product])

# --- run ------------------------------------------------------------------------

for j in joints:
    if j["follower"]:
        continue
    k, c = j["authored"] if j["name"] in actuators and j["authored"] else gains(j)
    set_drive(j, rest_of(j), k, c, 1.0e6)
px.start_simulation()
clock = 0.0
for _ in range(PHYSICS_HZ):  # settle at rest before the first frame
    px.update_simulation(DT, clock)
    clock += DT

from PIL import Image  # noqa: E402

total = sum(s["seconds"] for s in segments)
steps_per_frame = max(1, round(PHYSICS_HZ / args.fps))
log_rows, frame_i, elapsed = [], 0, 0.0
commanded = {j["name"]: rest_of(j) for j in joints}
gate_results = {}
for seg in segments:
    n = max(1, round(seg["seconds"] * args.fps))
    for name, (_, _, cap) in seg["moves"].items():
        jn = by_name[name]
        k, c = jn["authored"] if name in seg.get("authored_gains", ()) and jn["authored"] else gains(jn)
        set_drive(jn, commanded[name], k, c, cap if cap is not None else 1.0e6)
    peak = 0.0
    for i in range(n):
        u = (i + 1) / n
        for name, (a, b, _) in seg["moves"].items():
            commanded[name] = ease(a, b, u)
            set_drive(by_name[name], commanded[name])
        for _ in range(steps_per_frame):
            px.update_simulation(DT, clock)
            clock += DT
        # stepping PhysX directly does not publish poses to the renderer
        px.update_transformations(False, True, False, False)
        place_camera(min(1.0, (elapsed + i / args.fps) / total))
        rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=False)
        Image.fromarray(rgb.get_data()[:, :, :3]).save(frames_dir / f"f{frame_i:05d}.png")
        measured = {j["name"]: measure(j) for j in joints}
        if seg.get("gate_check"):
            g = by_name[seg["gate_check"]]
            peak = max(peak, abs(measured[g["name"]] - rest_of(g)))
        log_rows.append({"t": round(frame_i / args.fps, 3), "segment": seg["label"],
                         **{f"{n}_cmd": round(commanded[n], 4) for n in commanded},
                         **{f"{n}_meas": round(v, 4) for n, v in measured.items()}})
        frame_i += 1
    if seg.get("gate_check"):
        g = by_name[seg["gate_check"]]
        tried = abs(seg["moves"][g["name"]][1] - rest_of(g))
        gate_results[g["name"]] = {"tried": round(tried, 3), "moved": round(peak, 3),
                                   "held": peak < 0.1 * tried,
                                   "push": args.push_torque if g["revolute"] else args.push_force}
        # free the joint again for the real motion
        set_drive(g, rest_of(g), *gains(g), 1.0e6)
        commanded[g["name"]] = seg["moves"][g["name"]][0]
    elapsed += seg["seconds"]

# --- evidence -------------------------------------------------------------------

with open(out_dir / "joints.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(log_rows[0]))
    w.writeheader()
    w.writerows(log_rows)
summary = {"asset": asset_id, "usd": usd_path, "frames": frame_i, "fps": args.fps,
           "joints": {}, "gates": gate_results}
for j in joints:
    meas = [r[f"{j['name']}_meas"] for r in log_rows]
    entry = {"type": "revolute" if j["revolute"] else "prismatic", "limits": [j["lower"], j["upper"]],
             "measured_range": [round(min(meas), 4), round(max(meas), 4)], "follower": j["follower"]}
    if not j["follower"]:
        # tracking error outside gate checks, where the joint is meant to be stopped
        # against the target the limits allow (a closer preload aims past them)
        clamp = lambda v: min(max(v, j["lower"]), j["upper"])  # noqa: E731
        errs = [abs(r[f"{j['name']}_meas"] - clamp(r[f"{j['name']}_cmd"])) for r in log_rows
                if "gate check" not in r["segment"]]
        entry["max_tracking_error"] = round(max(errs), 4)
        entry["final_error"] = round(errs[-1], 4)
    summary["joints"][j["name"]] = entry
(out_dir / "summary.json").write_text(json.dumps(summary, indent=1))

mp4 = out_dir / f"{asset_id}.mp4"
ff = shutil.which("ffmpeg") or "ffmpeg"
subprocess.run([ff, "-v", "error", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "f%05d.png"),
                "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(mp4)], check=True)
picks = [round(frame_i * f) for f in (0.05, 0.25, 0.45, 0.6, 0.8, 0.97)]
tiles = [Image.open(frames_dir / f"f{min(p, frame_i - 1):05d}.png").resize((args.width // 2, args.height // 2)) for p in picks]
sheet = Image.new("RGB", (3 * tiles[0].width, 2 * tiles[0].height))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % 3) * t.width, (i // 3) * t.height))
sheet.save(out_dir / "contact_sheet.png")
print("ANIMATION", json.dumps({"mp4": str(mp4), **summary}, default=str), flush=True)
app.close()
