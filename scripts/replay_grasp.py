#!/usr/bin/env python3
"""Replay a recorded robot grasp in a built scene and film it (Kit).

The spec's grasp_replay names a recording: per-tick targets for the arm and
the hand's actuated channels (HomeHero's R5 reference pickup: 8 left-arm
joints, then index, middle, ring, pinky, thumb flexion, thumb rotation), at
control_hz. Each tick the targets go to PhysX's articulation view - finger
distal joints follow their proximal ones by the linkage coefficient, the
shoulder lift is raised by lift_offset_m (this table is higher than the
recording's) - and physics runs 1/control_hz of simulated time. Then the
final targets are held for hold_s.

Filmed every tick from three cameras: a close-up on the grasp with the
robot's head camera inset (grasp.mp4) and the room overview
(grasp_overview.mp4). Measured: how high the target rose and whether it
stayed with the palm through the hold.

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/replay_grasp.py workspace/scenes/<name>
writes <scene>/grasp/{grasp.mp4, grasp_overview.mp4, contact_sheet.png, grasp.json, log.json}
and prints a GRASP PASS|FAIL line.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--subframes", type=int, default=4)
ap.add_argument("--pathtrace", action="store_true")
ap.add_argument("--spp", type=int, default=16)
ap.add_argument("--settle", type=float, default=1.5)
ap.add_argument("--no-video", action="store_true", help="physics and the outcome only (minutes, not half an hour)")
args = ap.parse_args()

scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())
gr = spec["grasp_replay"]
out = scene_dir / "grasp"
frames = {k: out / f"frames_{k}" for k in ("close", "head", "overview")}
for d in frames.values():
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import numpy as np  # noqa: E402
import omni.physics.tensors as tensors  # noqa: E402
import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # noqa: E402

omni.usd.get_context().open_stage(str(scene_dir / "scene.usda"))
for _ in range(30):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
if args.pathtrace:
    s = carb.settings.get_settings()
    s.set("/rtx/rendermode", "PathTracing")
    s.set("/rtx/pathtracing/spp", args.spp)
    s.set("/rtx/pathtracing/totalSpp", args.spp)
    s.set("/rtx/pathtracing/optixDenoiser/enabled", True)

LINKS = "/World/Robot/Links"
target = next(p for p in stage.GetPrimAtPath("/World/Objects").GetChildren() if p.GetCustomDataByKey("er:target"))
target_body = next((q for q in Usd.PrimRange(target) if q.GetAttribute("physics:mass")), target)


def body_pose(path):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]
    return np.array(r["position"], float), np.array([q[3], q[0], q[1], q[2]], float)


def quat_to_R(wxyz):
    w, x, y, z = wxyz
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def Rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def Ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def Rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def target_centre():
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    return np.array(bc.ComputeWorldBound(target).ComputeAlignedRange().GetMidpoint(), float)


# --- start: posture, then settle --------------------------------------------------------
timeline = omni.timeline.get_timeline_interface()
timeline.play()
for _ in range(10):
    app.update()
sim_view = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
sim_view.set_subspace_roots("/")
art = sim_view.create_articulation_view(f"{LINKS}/amber_base_link")
dof = {n: i for i, n in enumerate(art.shared_metatype.dof_names)}
posture = spec["robot"]["posture_rad_or_m"]
idx = np.arange(1, dtype=np.int32)
q = art.get_dof_positions().copy()
for n, v in posture.items():
    q[0, dof[n]] = float(v)
art.set_dof_positions(q, idx)
art.set_dof_position_targets(q.copy(), idx)
art.set_dof_velocities(np.zeros_like(q), idx)
# physics is stepped here, not by the timeline: a headless timeline over a
# stage with no time range does not advance (measured: 0 s per update), so
# app updates alone simulate nothing. The timeline only attaches PhysX.
PHYS_HZ = int(spec.get("physics_hz", 120))
DT = 1.0 / PHYS_HZ
CLOCK = [0.0]


def simulate(seconds):
    for _ in range(max(1, round(seconds * PHYS_HZ))):
        px.update_simulation(DT, CLOCK[0])
        CLOCK[0] += DT
    px.update_transformations(False, True, False, False)


simulate(args.settle)

# --- the recording ---------------------------------------------------------------------
ref = np.load(Path(gr["reference"]).expanduser())["targets"]
robot = json.loads(Path(gr["robot"]).expanduser().read_text())
arm_names = robot["arm_names"]
F = float(gr.get("finger_q2_coefficient", 1.05851325))
lift = float(gr.get("lift_offset_m", 0.0))
fingers = ("index", "middle", "ring", "pinky")


pre = gr.get("preload_rad") or {}
start_hand, final_hand = ref[0, 8:13].astype(float), ref[-1, 8:13].astype(float)


def closure(row):
    """How far the hand has closed, 0 (open) to 1 (the recording's grip)."""
    span = np.where(np.abs(final_hand - start_hand) > 1e-6, final_hand - start_hand, 1.0)
    return float(np.clip(np.max((np.asarray(row[8:13], float) - start_hand) / span), 0.0, 1.0))


def to_targets(row):
    row = np.asarray(row, float).copy()
    c = closure(row)
    row[8:12] += float(pre.get("fingers", 0.0)) * c        # past contact: a squeeze, ramped in as the hand closes
    row[12] += float(pre.get("thumb_flex", 0.0)) * c
    t = {n: float(v) for n, v in zip(arm_names, row[:8])}
    t["amber_left_shoulder_lift_joint"] += lift
    for k, f in enumerate(fingers):
        t[f"psyonic_left_{f}_q1"] = float(row[8 + k])
        t[f"psyonic_left_{f}_q2"] = float(row[8 + k]) * F
    t["psyonic_left_thumb_q2"] = float(row[12])          # flexion
    t["psyonic_left_thumb_q1"] = float(row[13])          # rotation
    return t


# --- cameras -----------------------------------------------------------------------------
hc = spec["head_camera"]
link = body_pose(f"{LINKS}/{hc['mount_link']}")
R_link = quat_to_R(link[1])
r_, p_, y_ = hc["mount_rpy"]
R_opt = R_link @ (Rz(y_) @ Ry(p_) @ Rx(r_)) @ Rz(float(hc.get("optical_roll_rad", 0.0)))
t_opt = link[0] + R_link @ np.array(hc["mount_xyz"], float)
T = np.eye(4)
T[:3, :3], T[:3, 3] = R_opt @ Rx(math.pi), t_opt
xf = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Cameras/HeadRGB"))
xf.ClearXformOpOrder()
xf.AddTransformOp().Set(Gf.Matrix4d(T.T.tolist()))       # the base is fixed and the head still: one pose
products = {} if args.no_video else {
    "close": rep.create.render_product("/World/Cameras/GraspCloseup", (1280, 720)),
    "head": rep.create.render_product("/World/Cameras/HeadRGB", (640, 480)),
    "overview": rep.create.render_product("/World/Cameras/Overview", (1280, 720)),
}
annot = {}
for k, rp in products.items():
    a = rep.AnnotatorRegistry.get_annotator("rgb")
    a.attach([rp])
    annot[k] = a


def capture(i):
    if args.no_video:
        return
    rep.orchestrator.step(rt_subframes=max(1, args.subframes), delta_time=0.0, pause_timeline=False)
    for k, a in annot.items():
        Image.fromarray(a.get_data()[:, :, :3]).save(frames[k] / f"f{i:05d}.png")


# --- replay -------------------------------------------------------------------------------
hz = float(gr.get("control_hz", 30))
rest_c = target_centre()
rows = list(ref) + [ref[-1]] * int(float(gr.get("hold_s", 1.5)) * hz)
log = []
sim_t0 = CLOCK[0]
px.update_transformations(False, True, False, False)
for _ in range(3):                                      # the renderer catches up with the posture first
    app.update()                                        # (the first film's frame 0 showed the arm still down)
capture(0)
for i, row in enumerate(rows):
    tg = to_targets(row)
    qt = art.get_dof_position_targets().copy()
    for n, v in tg.items():
        qt[0, dof[n]] = v
    art.set_dof_position_targets(qt, idx)
    simulate(1.0 / hz)
    c = target_centre()
    palm = body_pose(f"{LINKS}/psyonic_left_base")
    qn = art.get_dof_positions()
    log.append({"tick": i, "t": round(i / hz, 3), "phase": "replay" if i < len(ref) else "hold",
                "target_rise_m": round(float(c[2] - rest_c[2]), 4),
                "palm_to_target_m": round(float(np.linalg.norm(palm[0] - c)), 4) if palm else None,
                "lift_m": round(float(qn[0, dof["amber_left_shoulder_lift_joint"]]), 4),
                "fingers_q1": [round(float(qn[0, dof[f"psyonic_left_{f}_q1"]]), 3) for f in fingers]})
    capture(i + 1)

# --- outcome ------------------------------------------------------------------------------
rise = [r["target_rise_m"] for r in log]
held = [r for r in log if r["phase"] == "hold"]
peak = max(rise) if rise else 0.0
end = rise[-1] if rise else 0.0
near = held[-1]["palm_to_target_m"] if held else None
lifted = peak >= 0.05
kept = end >= 0.05 and (near is None or near < 0.15)
result = {"scene": spec["name"], "target": target.GetCustomDataByKey("er:label"),
          "recording": gr["reference"], "ticks": len(rows), "control_hz": hz,
          "sim_seconds": round(CLOCK[0] - sim_t0, 3),
          "target_peak_rise_m": round(peak, 4), "target_end_rise_m": round(end, 4),
          "palm_to_target_end_m": near, "lifted": lifted, "held": kept, "pass": bool(lifted and kept)}
(out / "grasp.json").write_text(json.dumps(result, indent=1))
(out / "log.json").write_text(json.dumps(log))

# --- video --------------------------------------------------------------------------------
if args.no_video:
    print(f"GRASP {'PASS' if result['pass'] else 'FAIL'} {spec['name']} (no video): rose {100 * peak:.1f} cm at peak, "
          f"{100 * end:.1f} cm at the end; palm {near} m from it", flush=True)
    print("GRASP_RESULT " + json.dumps(result), flush=True)
    app.close()
    raise SystemExit(0)
comp = out / "frames_comp"
shutil.rmtree(comp, ignore_errors=True)
comp.mkdir()
n = len(rows) + 1
for i in range(n):
    big = Image.open(frames["close"] / f"f{i:05d}.png").convert("RGB")
    head = Image.open(frames["head"] / f"f{i:05d}.png").convert("RGB").resize((384, 288))
    big.paste(head, (big.width - 384 - 16, big.height - 288 - 16))
    d = ImageDraw.Draw(big)
    d.rectangle([big.width - 384 - 16, big.height - 288 - 16, big.width - 16, big.height - 16], outline=(255, 255, 255), width=2)
    rr = log[i - 1] if i else {"t": 0.0, "phase": "start", "target_rise_m": 0.0}
    d.text((20, 18), f"Aida R5  t={rr['t']:.2f}s  {rr['phase']}  mug rise {100 * rr['target_rise_m']:.1f} cm", fill=(255, 255, 255))
    d.text((big.width - 384 - 10, big.height - 288 - 32), "head camera", fill=(255, 255, 255))
    big.save(comp / f"f{i:05d}.png")
ff = shutil.which("ffmpeg") or "ffmpeg"
for name, src in (("grasp.mp4", comp), ("grasp_overview.mp4", frames["overview"])):
    subprocess.run([ff, "-v", "error", "-y", "-framerate", str(int(hz)), "-i", str(src / "f%05d.png"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(out / name)], check=False)
picks = [round((n - 1) * f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
tiles = [Image.open(comp / f"f{p:05d}.png").resize((640, 360)) for p in picks]
sheet = Image.new("RGB", (3 * 640, 2 * 360))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % 3) * 640, (i // 3) * 360))
sheet.save(out / "contact_sheet.png")
for d in list(frames.values()) + [comp]:
    shutil.rmtree(d, ignore_errors=True)
print(f"GRASP {'PASS' if result['pass'] else 'FAIL'} {spec['name']}: {result['target']} rose {100 * peak:.1f} cm at peak, "
      f"{100 * end:.1f} cm at the end of the hold; palm {near} m from it", flush=True)
print("GRASP_RESULT " + json.dumps(result), flush=True)
app.close()
