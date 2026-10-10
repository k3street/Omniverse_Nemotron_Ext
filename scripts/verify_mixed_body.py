#!/usr/bin/env python3
"""Verify a mixed body in PhysX: drop it, the soft parts must deform and stay
attached while the rigid part lands.

Runs headless in Isaac Sim (the deformables are cooked and simulated by
PhysX, which needs Kit). The asset, authored by soft_body_parts.py, is set
5 cm above a floor and released; a camera records. Measured:

  landed      the rigid base comes to rest on the floor, upright
  deformed    every soft part's skinned mesh moves relative to the base (a
              rigid shell would not), but not by more than half its size
  attached    the soft part's middle stays where it sat on the base; drift
              past a tenth of its size is a detachment
  finite      no NaN: the solver did not blow up

Attachments are completed in Kit (PhysX's auto attachment fills in the
vertex pairs) and the result written back to the asset, so the file is
complete for anyone who loads it afterwards.

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/verify_mixed_body.py <asset_id>
Writes workspace/asset_animations/<id>/mixed/{mixed.mp4, contact_sheet.png, summary.json}
and prints a MIXED PASS|FAIL line.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE = REPO / "workspace" / "review_queue"
OUT_ROOT = REPO / "workspace" / "asset_animations"

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("asset_id")
ap.add_argument("--seconds", type=float, default=3.0)
ap.add_argument("--fps", type=int, default=24)
ap.add_argument("--drop", type=float, default=0.05, help="metres above the floor it starts")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
args = ap.parse_args()

entry = json.loads((QUEUE / f"{args.asset_id}.json").read_text())
soft_rec = entry.get("soft_body_parts")
if not soft_rec:
    raise SystemExit(f"{args.asset_id}: no soft body parts authored (run soft_body_parts.py first)")
usd_path = entry["file"]
out_dir = OUT_ROOT / args.asset_id / "mixed"
frames_dir = out_dir / "frames"
shutil.rmtree(frames_dir, ignore_errors=True)
frames_dir.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import carb  # noqa: E402
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image  # noqa: E402
from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

omni.usd.get_context().open_stage(usd_path)
for _ in range(20):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
HZ = 120
DT = 1.0 / HZ

# --- scene: GPU physics (deformables are GPU-only), floor, light, camera ---------
bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
rng = bbox.ComputeWorldBound(stage.GetDefaultPrim() or stage.GetPseudoRoot()).ComputeAlignedRange()
lo, hi = rng.GetMin(), rng.GetMax()
center, size = (lo + hi) * 0.5, hi - lo
radius = 0.5 * size.GetLength()
with Usd.EditContext(stage, stage.GetSessionLayer()):
    # the asset's own physics scene, if it has one (ingest writes one): a
    # second scene steps apart from it, and the deformables land in the one
    # without GPU dynamics (contact buffer overflow, no cloth)
    scenes = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    scene = UsdPhysics.Scene(scenes[0]) if scenes else UsdPhysics.Scene.Define(stage, "/MixedView/PhysicsScene")
    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr().Set(9.81)
    sp = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
    sp.CreateEnableGPUDynamicsAttr().Set(True)
    sp.CreateBroadphaseTypeAttr().Set("GPU")
    sp.CreateTimeStepsPerSecondAttr().Set(HZ)
    sp.CreateGpuMaxDeformableSurfaceContactsAttr().Set(8 * 1048576)
    sp.CreateGpuCollisionStackSizeAttr().Set(256 * 1024 * 1024)
    for extra in scenes[1:]:
        extra.SetActive(False)
    UsdLux.DomeLight.Define(stage, "/MixedView/Dome").CreateIntensityAttr(1200)
    key = UsdLux.DistantLight.Define(stage, "/MixedView/Key")
    key.CreateIntensityAttr(2500)
    UsdGeom.XformCommonAPI(key.GetPrim()).SetRotate(Gf.Vec3f(-45, 0, -35))
    z0 = lo[2] - args.drop
    f = 4.0 * radius
    floor = UsdGeom.Mesh.Define(stage, "/MixedView/Floor")
    floor.CreatePointsAttr([(center[0] - f, center[1] - f, z0), (center[0] + f, center[1] - f, z0),
                            (center[0] + f, center[1] + f, z0), (center[0] - f, center[1] + f, z0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    floor.CreateDisplayColorAttr([(0.35, 0.35, 0.37)])
    ground = UsdGeom.Cube.Define(stage, "/MixedView/Ground")
    ground.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetTranslate(Gf.Vec3d(center[0], center[1], z0 - 0.05))
    UsdGeom.XformCommonAPI(ground.GetPrim()).SetScale(Gf.Vec3f(2 * f, 2 * f, 0.1))
    ground.CreateVisibilityAttr("invisible")
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    cam = UsdGeom.Camera.Define(stage, "/MixedView/Camera")
    cam.CreateFocalLengthAttr(18.0)
    cam.CreateHorizontalApertureAttr(20.955)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
    hfov = 2 * math.atan(20.955 / (2 * 18.0))
    vfov = 2 * math.atan(math.tan(hfov / 2) * args.height / args.width)
    dist = 1.3 * radius / math.tan(min(hfov, vfov) / 2)
    el = math.radians(28)
    az = math.radians(-55)
    target = Gf.Vec3d(center[0], center[1], lo[2] + 0.4 * size[2])
    eye = target + Gf.Vec3d(dist * math.cos(el) * math.cos(az), dist * math.cos(el) * math.sin(az), dist * math.sin(el))
    UsdGeom.Xformable(cam.GetPrim()).AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())

# --- complete the attachments in Kit, and keep them in the file -----------------
completed = []
try:
    from omni.physx.scripts.ifaces import get_physx_attachment_private_interface   # where PhysX's own utils find it
    iface = get_physx_attachment_private_interface()
    for prim in stage.Traverse():
        if "PhysxAutoDeformableAttachmentAPI" in prim.GetAppliedSchemas():
            try:
                ok = iface.setup_auto_deformable_attachment(str(prim.GetPath()))
                completed.append((str(prim.GetPath()), bool(ok)))
            except Exception as ex:  # noqa: BLE001
                completed.append((str(prim.GetPath()), f"error: {str(ex)[:80]}"))
except Exception as ex:  # noqa: BLE001 - PhysX cooks auto attachments at load as well
    completed.append(("interface", f"unavailable: {str(ex)[:80]}"))
if any(v is True for _, v in completed):
    try:
        stage.GetRootLayer().Save()               # the vertex pairs PhysX wrote, for whoever loads it next
    except Exception:  # noqa: BLE001
        pass

# --- what to measure ------------------------------------------------------------
base_path = soft_rec["base"]
soft_bodies = soft_rec["soft"]
rigid_parts = [r for r in soft_rec.get("rigid", []) if r != base_path]   # must ride with the base, still
xf = UsdGeom.XformCache(Usd.TimeCode.Default())
root_prim = stage.GetPrimAtPath(entry.get("part_survey", {}).get("root") or str(stage.GetPrimAtPath(base_path).GetParent().GetPath()))


def base_pose():
    r = px.get_rigidbody_transformation(base_path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return Gf.Vec3d(*r["position"]), Gf.Rotation(Gf.Quatd(q[3], q[0], q[1], q[2]))


def _world_points(prim):
    pts = UsdGeom.Mesh(prim).GetPointsAttr().Get()
    if not pts:
        return None
    m = xf.GetLocalToWorldTransform(prim)
    return np.array([list(m.Transform(Gf.Vec3d(p))) for p in pts])


def render_points(body):
    """The skinned copy's points, in world space."""
    src = stage.GetPrimAtPath(f"{body['body']}/{Path(body['source']).name}")
    if not src:
        kids = [c for c in stage.GetPrimAtPath(body["body"]).GetChildren() if c.IsA(UsdGeom.Mesh) and c.GetName() != "simMesh"]
        src = kids[0] if kids else None
    return _world_points(src) if src else None


def rider_prims(body):
    """The trims skinned along with the soft part (fur, bows, a label)."""
    main = Path(body["source"]).name
    return [c for c in stage.GetPrimAtPath(body["body"]).GetChildren()
            if c.IsA(UsdGeom.Mesh) and c.GetName() not in ("simMesh", main)]


def part_offset(path, pose):
    """A rigid part's position in the base's frame."""
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True) or not pose:
        return None
    p0, r0 = pose
    return r0.GetInverse().TransformDir(Gf.Vec3d(*r["position"]) - p0)


def in_base_frame(pts, pose):
    p0, r0 = pose
    inv = r0.GetInverse()
    return np.array([list(inv.TransformDir(Gf.Vec3d(*p) - p0)) for p in pts])


# --- run: let PhysX cook, then drop ---------------------------------------------
px.start_simulation()
cooked = False
for _ in range(600):                          # cooking is asynchronous: wait for a simulation mesh
    app.update()
    xf.Clear()
    sim_ok = []
    for b in soft_bodies:
        sm = stage.GetPrimAtPath(f"{b['body']}/simMesh")
        pts = UsdGeom.Mesh(sm).GetPointsAttr().Get() if sm else None
        sim_ok.append(bool(pts) and len(pts) > 3)
    if all(sim_ok):
        cooked = True
        break
render_product = rep.create.render_product("/MixedView/Camera", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([render_product])

pose0 = base_pose()
rest_rigid = {r: part_offset(r, pose0) for r in rigid_parts}
rigid_drift = {r: 0.0 for r in rigid_parts}
rest = {}
rider_rest, rider_peak = {}, {}
for b in soft_bodies:
    pts = render_points(b)
    rest[b["body"]] = in_base_frame(pts, pose0) if (pts is not None and pose0) else None
    for r in rider_prims(b):
        rp = _world_points(r)
        if rp is not None and pose0:
            rider_rest[str(r.GetPath())] = in_base_frame(rp, pose0)
            rider_peak[str(r.GetPath())] = 0.0
steps_per_frame = max(1, HZ // args.fps)
n_frames = int(args.seconds * args.fps)
log = []
clock = 0.0
for i in range(n_frames):
    for _ in range(steps_per_frame):
        px.update_simulation(DT, clock)
        clock += DT
    px.update_transformations(False, True, False, False)
    xf.Clear()
    rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=False)
    Image.fromarray(rgb.get_data()[:, :, :3]).save(frames_dir / f"f{i:05d}.png")
    pose = base_pose()
    row = {"t": round(i / args.fps, 3), "base_z": None, "soft": {}}
    if pose:
        for r in rigid_parts:
            o = part_offset(r, pose)
            if o is not None and rest_rigid.get(r) is not None:
                rigid_drift[r] = max(rigid_drift[r], (o - rest_rigid[r]).GetLength())
        row["base_z"] = round(float(pose[0][2]), 4)
        up = pose[1].TransformDir(Gf.Vec3d(0, 0, 1))
        row["base_tilt_deg"] = round(math.degrees(math.acos(max(-1.0, min(1.0, up[2])))), 2)
        for b in soft_bodies:
            pts = render_points(b)
            r0 = rest.get(b["body"])
            if pts is None or r0 is None or len(pts) != len(r0):
                continue
            now = in_base_frame(pts, pose)
            d = np.linalg.norm(now - r0, axis=1)
            row["soft"][b["body"]] = {"mean_m": round(float(np.nanmean(d)), 5), "max_m": round(float(np.nanmax(d)), 5),
                                      "drift_m": round(float(np.linalg.norm(now.mean(0) - r0.mean(0))), 5),
                                      "finite": bool(np.isfinite(now).all())}
            if i >= args.fps // 2:                 # after the first half second: skinning has settled
                for r in rider_prims(b):
                    rp = _world_points(r)
                    r0r = rider_rest.get(str(r.GetPath()))
                    if rp is not None and r0r is not None and len(rp) == len(r0r):
                        dd = np.linalg.norm(in_base_frame(rp, pose) - r0r, axis=1)
                        rider_peak[str(r.GetPath())] = max(rider_peak[str(r.GetPath())],
                                                           float(np.nanmax(dd)) if np.isfinite(dd).all() else float("inf"))
    log.append(row)

# --- verdict --------------------------------------------------------------------
final = log[-1] if log else {}
z_first = next((r["base_z"] for r in log if r["base_z"] is not None), None)
landed = bool(final.get("base_z") is not None and z_first is not None
              and (z_first - final["base_z"]) <= args.drop + 0.02 and final.get("base_tilt_deg", 90) < 15.0
              and abs(final["base_z"] - log[-max(1, args.fps // 2)]["base_z"]) < 0.002)
soft_results = {}
for b in soft_bodies:
    series = [r["soft"].get(b["body"]) for r in log if r["soft"].get(b["body"])]
    ext = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(
        stage.GetPrimAtPath(b["body"])).ComputeAlignedRange().GetSize()
    sz = float(max(ext)) if max(ext) > 0 else 0.1
    if not series:
        soft_results[b["body"]] = {"ok": False, "why": "no skinned points read back (not cooked, or not published)"}
        continue
    peak = max(s["max_m"] for s in series)
    drift = series[-1]["drift_m"]
    finite = all(s["finite"] for s in series)
    deformed = peak > 0.001
    attached = drift < 0.1 * sz
    bounded = peak < 0.5 * sz
    # the trims skinned with it must stay with it too (a 70-vertex heel label
    # skinned to a far triangle swung out as spikes the size of the sole)
    wild = {Path(k).name: (round(v, 4) if np.isfinite(v) else "inf") for k, v in rider_peak.items()
            if k.startswith(b["body"] + "/") and (not np.isfinite(v) or v > 0.5 * sz)}
    riders_ok = not wild
    soft_results[b["body"]] = {"ok": bool(finite and deformed and attached and bounded and riders_ok), "role": b["role"],
                               "peak_m": round(peak, 5), "drift_m": round(drift, 5), "size_m": round(sz, 4),
                               "finite": finite, "deformed": deformed, "attached": attached, "bounded": bounded,
                               "riders_ok": riders_ok, "riders_wild": wild,
                               "rider_peaks_m": {Path(k).name: (round(v, 4) if np.isfinite(v) else "inf")
                                                 for k, v in rider_peak.items() if k.startswith(b["body"] + "/")}}
loose_rigid = {Path(r).name: round(d, 4) for r, d in rigid_drift.items() if d > max(0.01, 0.02 * radius)}
ok = cooked and landed and bool(soft_results) and all(v["ok"] for v in soft_results.values()) and not loose_rigid
summary = {"asset": args.asset_id, "cooked": cooked, "landed": landed, "attachments": completed,
           "rigid_parts_moved": loose_rigid,
           "base": {"z_first": z_first, "z_final": final.get("base_z"), "tilt_deg": final.get("base_tilt_deg")},
           "soft": soft_results, "pass": ok, "frames": len(log), "fps": args.fps}
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
(out_dir / "log.json").write_text(json.dumps(log))
ff = shutil.which("ffmpeg") or "ffmpeg"
mp4 = out_dir / "mixed.mp4"
subprocess.run([ff, "-v", "error", "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "f%05d.png"),
                "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(mp4)], check=False)
picks = [round((len(log) - 1) * f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)] if log else []
if picks:
    tiles = [Image.open(frames_dir / f"f{p:05d}.png").resize((args.width // 2, args.height // 2)) for p in picks]
    sheet = Image.new("RGB", (3 * tiles[0].width, 2 * tiles[0].height))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 3) * t.width, (i // 3) * t.height))
    sheet.save(out_dir / "contact_sheet.png")
why = [] if ok else [k for k in ("cooked", "landed") if not summary[k]] + (
    [f"rigid parts moved against the base: {loose_rigid}"] if loose_rigid else []) + [
    f"{Path(k).name}: " + ", ".join(w for w in ("finite", "deformed", "attached", "bounded", "riders_ok") if v.get(w) is False)
    + (f" (wild trims {v['riders_wild']})" if v.get("riders_wild") else "")
    + (f" {v.get('why')}" if v.get("why") else "") for k, v in soft_results.items() if not v["ok"]]
print(f"MIXED {'PASS' if ok else 'FAIL'} {args.asset_id}: " + ("; ".join(why) or "soft parts deform and stay attached"),
      flush=True)
print("MIXED_SUMMARY " + json.dumps(summary, default=str), flush=True)
app.close()
