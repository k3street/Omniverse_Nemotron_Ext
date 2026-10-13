#!/usr/bin/env python3
"""Settle an egocentric test scene in PhysX and render what the robot sees,
with ground truth for scoring an embodied-reasoning model (Kit).

Takes a scene from build_er_scene.py. The robot starts in its posture (joint
states and drive targets authored by the build), objects drop the 2 mm onto
their support, and PhysX runs for settle_s. Then the head camera is posed
from the measured head link - the mount chain from the spec, the optical
roll correction, ROS optical -> USD camera - and rendered with depth and
instance segmentation. Measured:

  settled      every object came to rest on the support (not fallen, not
               floating), the robot base moved less than 2 cm
  visible      how many pixels of each object the head camera sees; the
               target must be seen
  ground truth the target's visible mask, its 2D box and centre in pixels
               and in the 0-1000 [y, x] convention Gemini Robotics-ER
               answers in, and its 3D box in world and camera frames

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/render_er_scene.py workspace/scenes/<name>
writes <scene dir>/render/{head_rgb.png, head_depth.npy, head_depth.png,
target_mask.png, head_gt_overlay.png, overview.png, gt.json} and prints an
ER_SCENE OK|FAIL line.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--subframes", type=int, default=64, help="RTX subframes per image (lighting convergence)")
ap.add_argument("--pathtrace", action="store_true", help="path-traced render instead of real-time ray tracing")
ap.add_argument("--spp", type=int, default=64)
args = ap.parse_args()

scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())
out = scene_dir / "render"
out.mkdir(parents=True, exist_ok=True)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import carb  # noqa: E402
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # noqa: E402

omni.usd.get_context().open_stage(str(scene_dir / "scene.usda"))
for _ in range(30):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
HZ = int(spec.get("physics_hz", 120))
DT = 1.0 / HZ
render = spec.get("render") or {}
if args.pathtrace or render.get("mode") == "pathtrace":
    spp = args.spp if args.pathtrace else int(render.get("spp", args.spp))
    s = carb.settings.get_settings()
    s.set("/rtx/rendermode", "PathTracing")
    s.set("/rtx/pathtracing/spp", spp)
    s.set("/rtx/pathtracing/totalSpp", spp)
    s.set("/rtx/pathtracing/optixDenoiser/enabled", True)

ROBOT = "/World/Robot"
LINKS = f"{ROBOT}/Links"
objects = [p for p in stage.GetPrimAtPath("/World/Objects").GetChildren()]
target = next(p for p in objects if p.GetCustomDataByKey("er:target"))
top = float(stage.GetPrimAtPath("/World").GetCustomDataByKey("er:support_top_m"))


def body_pose(path):
    r = px.get_rigidbody_transformation(path)
    if not r.get("ret_val", True):
        return None
    q = r["rotation"]                      # x, y, z, w
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


def boxes():
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    return {p.GetName(): bc.ComputeWorldBound(p).ComputeAlignedRange() for p in objects}


# --- settle ------------------------------------------------------------------------
import omni.timeline  # noqa: E402

timeline = omni.timeline.get_timeline_interface()
timeline.play()                                         # the articulation view needs the stage attached to PhysX
for _ in range(10):
    app.update()
# the posture through PhysX's articulation view: joint states authored in USD
# set the head but left the arms at zero (the measured palm sat where zero-arm
# kinematics put it), so positions and targets are written here as well
import omni.physics.tensors as tensors  # noqa: E402

sim_view = tensors.create_simulation_view("numpy", stage_id=omni.usd.get_context().get_stage_id())
sim_view.set_subspace_roots("/")
art = sim_view.create_articulation_view(f"{LINKS}/amber_base_link")
dof_names = list(art.shared_metatype.dof_names)
posture = spec["robot"].get("posture_rad_or_m", {})
q0 = art.get_dof_positions().copy()
start_q = {n: round(float(q0[0, i]), 4) for i, n in enumerate(dof_names) if n in posture}
q = q0.copy()
for i, n in enumerate(dof_names):
    if n in posture:
        q[0, i] = float(posture[n])
idx = np.arange(1, dtype=np.int32)
art.set_dof_positions(q, idx)
art.set_dof_position_targets(q, idx)
art.set_dof_velocities(np.zeros_like(q), idx)
missing = [n for n in posture if n not in dof_names]
before = boxes()
# physics stepped directly: a headless timeline over a stage with no time
# range does not advance, so app updates alone never ran PhysX (the objects
# stayed at their 2 mm drop height until this was found)
clock = 0.0
for _ in range(int(float(spec.get("settle_s", 2.0)) * HZ)):
    px.update_simulation(DT, clock)
    clock += DT
px.update_transformations(False, True, False, False)
q1 = art.get_dof_positions()
for _ in range(30):                                     # let streamed textures (the sky's HDRI) finish loading
    app.update()
held_q = {n: {"want": round(float(posture[n]), 4), "start": start_q.get(n), "settled": round(float(q1[0, i]), 4)}
          for i, n in enumerate(dof_names) if n in posture}
after = boxes()
base1 = body_pose(f"{LINKS}/amber_base_link")
spawn = np.array(spec["robot"]["position"], float)
base_drift = float(np.linalg.norm(base1[0][:2] - spawn[:2])) if base1 else None     # in the floor plane
settle = {}
for name, r in after.items():
    b = before[name]
    settle[name] = {"min_z_m": round(float(r.GetMin()[2]), 4),
                    "moved_m": round(float((r.GetMidpoint() - b.GetMidpoint()).GetLength()), 4),
                    "on_support": bool(top - 0.01 <= r.GetMin()[2] <= top + 0.01)}

# --- posture check: the measured links against forward kinematics of the URDF -----------
def urdf_fk(urdf_path, base_pose, joints_q, link):
    """World pose of `link` from the base link's measured pose and the
    posture's joint values, through the URDF chain (fixed joints included)."""
    import xml.etree.ElementTree as ET

    chain = {j.find("child").get("link"): j for j in ET.parse(urdf_path).getroot().findall("joint")}
    steps = []
    while link in chain:
        j = chain[link]
        steps.append(j)
        link = j.find("parent").get("link")
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = quat_to_R(base_pose[1]), base_pose[0]
    for j in reversed(steps):
        o = j.find("origin")
        xyz = np.array([float(v) for v in (o.get("xyz", "0 0 0") if o is not None else "0 0 0").split()])
        rpy = [float(v) for v in (o.get("rpy", "0 0 0") if o is not None else "0 0 0").split()]
        M = np.eye(4)
        M[:3, :3], M[:3, 3] = Rz(rpy[2]) @ Ry(rpy[1]) @ Rx(rpy[0]), xyz
        T = T @ M
        q = float(joints_q.get(j.get("name"), 0.0))
        if j.get("type") in ("revolute", "continuous", "prismatic") and q:
            ax = j.find("axis")
            a = np.array([float(v) for v in (ax.get("xyz") if ax is not None else "1 0 0").split()])
            a = a / np.linalg.norm(a)
            J = np.eye(4)
            if j.get("type") == "prismatic":
                J[:3, 3] = a * q
            else:
                K_ = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
                J[:3, :3] = np.eye(3) + math.sin(q) * K_ + (1 - math.cos(q)) * K_ @ K_
            T = T @ J
    return T


posture = spec["robot"].get("posture_rad_or_m", {})
urdf = str(Path(spec["robot"]["urdf"]).expanduser())
posture_check = {}
for check_link in spec["robot"].get("check_links", ["psyonic_left_base", "amber_head_pitch_link"]):
    meas = body_pose(f"{LINKS}/{check_link}")
    if meas is None or base1 is None:
        posture_check[check_link] = {"error": "no rigid body"}
        continue
    T = urdf_fk(urdf, base1, posture, check_link)
    posture_check[check_link] = {"measured": meas[0].round(4).tolist(), "fk": T[:3, 3].round(4).tolist(),
                                 "error_m": round(float(np.linalg.norm(meas[0] - T[:3, 3])), 4)}

# --- head camera from the measured head link ----------------------------------------
hc = spec["head_camera"]
link = body_pose(f"{LINKS}/{hc['mount_link']}")
if link is None:
    raise SystemExit(f"no rigid body at {LINKS}/{hc['mount_link']}")
R_link = quat_to_R(link[1])
r, p_, y_ = hc["mount_rpy"]
R_mount = Rz(y_) @ Ry(p_) @ Rx(r)                      # URDF rpy: fixed-axis X, Y, Z
R_opt = R_link @ R_mount @ Rz(float(hc.get("optical_roll_rad", 0.0)))
t_opt = link[0] + R_link @ np.array(hc["mount_xyz"], float)
R_cam = R_opt @ Rx(math.pi)                             # ROS optical (z ahead, y down) -> USD camera
T = np.eye(4)
T[:3, :3], T[:3, 3] = R_cam, t_opt
head_cam = stage.GetPrimAtPath("/World/Cameras/HeadRGB")
xf = UsdGeom.Xformable(head_cam)
xf.ClearXformOpOrder()
xf.AddTransformOp().Set(Gf.Matrix4d(T.T.tolist()))      # Gf: row vectors

W, H = hc["resolution"]
fx = float(hc["focal_length_mm"]) / float(hc["horizontal_aperture_mm"]) * W
fy = fx                                                 # square pixels: vertical aperture follows the resolution
cx, cy = W / 2.0, H / 2.0
K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]


def project(pw):
    pc = R_opt.T @ (np.asarray(pw, float) - t_opt)       # optical frame: x right, y down, z ahead
    if pc[2] <= 1e-6:
        return None
    return fx * pc[0] / pc[2] + cx, fy * pc[1] / pc[2] + cy, float(pc[2])


# --- render --------------------------------------------------------------------------
head_rp = rep.create.render_product("/World/Cameras/HeadRGB", (W, H))
oc = spec["overview_camera"]
ov_rp = rep.create.render_product("/World/Cameras/Overview", tuple(oc["resolution"]))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([head_rp])
depth = rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
depth.attach([head_rp])
seg = rep.AnnotatorRegistry.get_annotator("instance_id_segmentation_fast", init_params={"colorize": False})
seg.attach([head_rp])
ov_rgb = rep.AnnotatorRegistry.get_annotator("rgb")
ov_rgb.attach([ov_rp])
rep.orchestrator.step(rt_subframes=max(1, args.subframes), delta_time=0.0, pause_timeline=False)
img = rgb.get_data()[:, :, :3]
Image.fromarray(img).save(out / "head_rgb.png")
Image.fromarray(ov_rgb.get_data()[:, :, :3]).save(out / "overview.png")
d = np.asarray(depth.get_data(), np.float32)
np.save(out / "head_depth.npy", d)
dv = np.nan_to_num(d, nan=0.0, posinf=0.0)
finite = dv[(dv > 0) & (dv < 20)]
lo_d, hi_d = (float(np.percentile(finite, 1)), float(np.percentile(finite, 99))) if finite.size else (0.0, 1.0)
Image.fromarray((255 * (1 - np.clip((dv - lo_d) / max(hi_d - lo_d, 1e-6), 0, 1))).astype(np.uint8)).save(out / "head_depth.png")
sd = seg.get_data()
ids = np.asarray(sd["data"]).reshape(H, W)
id_to_path = {int(k): v for k, v in (sd.get("info") or {}).get("idToLabels", {}).items()}


def mask_of(prefix):
    keep = [i for i, pth in id_to_path.items() if str(pth).startswith(prefix)]
    return np.isin(ids, keep) if keep else np.zeros_like(ids, bool)


visible = {p.GetName(): int(mask_of(str(p.GetPath()) + "/").sum() + mask_of(str(p.GetPath())).sum() // 2)
           for p in objects}
np.save(out / "head_instance_ids.npy", ids.astype(np.uint32))      # what every pixel is (for scoring answers)
(out / "head_instance_paths.json").write_text(json.dumps({str(k): str(v) for k, v in id_to_path.items()}))
tmask = mask_of(str(target.GetPath()))
Image.fromarray((tmask * 255).astype(np.uint8)).save(out / "target_mask.png")

# --- ground truth --------------------------------------------------------------------
tr = after[target.GetName()]
corners = [Gf.Vec3d(x, y, z) for x in (tr.GetMin()[0], tr.GetMax()[0]) for y in (tr.GetMin()[1], tr.GetMax()[1])
           for z in (tr.GetMin()[2], tr.GetMax()[2])]
proj = [project(c) for c in corners]
proj = [q for q in proj if q]
gt = {"scene": str(scene_dir / "scene.usda"), "image": "head_rgb.png", "width": W, "height": H,
      "intrinsics": {"fx": fx, "fy": fy, "cx": cx, "cy": cy, "K": K, "focal_length_mm": hc["focal_length_mm"],
                     "horizontal_aperture_mm": hc["horizontal_aperture_mm"],
                     "hfov_deg": round(math.degrees(2 * math.atan(W / (2 * fx))), 2)},
      "camera_world": {"position": t_opt.round(5).tolist(), "R_optical": R_opt.round(6).tolist(),
                       "convention": "ROS optical: x right, y down, z ahead"},
      "target": {"name": target.GetName(), "asset_id": target.GetCustomDataByKey("er:asset_id"),
                 "label": target.GetCustomDataByKey("er:label"),
                 "bbox_world": [[round(v, 4) for v in tr.GetMin()], [round(v, 4) for v in tr.GetMax()]],
                 "centre_world": [round(v, 4) for v in tr.GetMidpoint()],
                 "visible_px": int(tmask.sum())},
      "objects": {p.GetName(): {"asset_id": p.GetCustomDataByKey("er:asset_id"), "label": p.GetCustomDataByKey("er:label"),
                                "visible_px": visible[p.GetName()], **settle[p.GetName()]} for p in objects},
      "robot": {"base_drift_m": round(base_drift, 4) if base_drift is not None else None,
                "base_position": base1[0].round(4).tolist() if base1 else None,
                "posture_check": posture_check, "joints": held_q, "posture_joints_not_in_articulation": missing},
      "depth_range_m": [round(lo_d, 3), round(hi_d, 3)]}
if tmask.any():
    ys, xs = np.nonzero(tmask)
    box = [int(ys.min()), int(xs.min()), int(ys.max()), int(xs.max())]
    # what else the camera sees inside the target's box: occluders, or a
    # segmentation that splits the target (attributed by prim path)
    inside = ids[box[0]:box[2] + 1, box[1]:box[3] + 1]
    vals, counts = np.unique(inside, return_counts=True)
    gt["target"]["box_contents_px"] = {str(id_to_path.get(int(v), int(v)))[-80:]: int(c)
                                       for v, c in sorted(zip(vals, counts), key=lambda vc: -vc[1])[:8]}
    cy_m, cx_m = float(ys.mean()), float(xs.mean())
    gt["target"].update(
        box_px_yxyx=box, point_px_yx=[round(cy_m, 1), round(cx_m, 1)],
        box_yxyx_1000=[round(box[0] / H * 1000), round(box[1] / W * 1000), round(box[2] / H * 1000), round(box[3] / W * 1000)],
        point_yx_1000=[round(cy_m / H * 1000), round(cx_m / W * 1000)],
        depth_at_point_m=round(float(dv[int(cy_m), int(cx_m)]), 4))
if proj:
    us, vs = [q[0] for q in proj], [q[1] for q in proj]
    gt["target"]["projected_bbox3d_px_xyxy"] = [round(min(us), 1), round(min(vs), 1), round(max(us), 1), round(max(vs), 1)]
c = project(tr.GetMidpoint())
if c:
    gt["target"]["projected_centre_px_xy"] = [round(c[0], 1), round(c[1], 1)]
    gt["target"]["range_m"] = round(c[2], 4)
(out / "gt.json").write_text(json.dumps(gt, indent=1))

overlay = Image.fromarray(img.copy())
dr = ImageDraw.Draw(overlay)
if "box_px_yxyx" in gt["target"]:
    y0, x0, y1, x1 = gt["target"]["box_px_yxyx"]
    dr.rectangle([x0, y0, x1, y1], outline=(0, 255, 0), width=3)
    py, pxx = gt["target"]["point_px_yx"]
    dr.ellipse([pxx - 6, py - 6, pxx + 6, py + 6], outline=(255, 255, 0), width=3)
overlay.save(out / "head_gt_overlay.png")

problems = []
if not tmask.any():
    problems.append("the head camera does not see the target")
fell = [n for n, v in settle.items() if not v["on_support"]]
if fell:
    problems.append(f"not resting on the support after settling: {fell}")
if base_drift is not None and base_drift > 0.02:
    problems.append(f"robot base drifted {base_drift:.3f} m")
off = {k: v.get("error_m") for k, v in posture_check.items() if v.get("error_m") is None or v["error_m"] > 0.03}
if off:
    problems.append(f"posture not held (measured link vs URDF FK, m): {off}")
ok = not problems
print(f"ER_SCENE {'OK' if ok else 'FAIL'} {spec['name']}: " + ("; ".join(problems) or
      f"target {gt['target']['label']} visible ({int(tmask.sum())} px) at yx_1000 {gt['target'].get('point_yx_1000')}, "
      f"{gt['target'].get('range_m')} m from the head camera"), flush=True)
print("ER_SCENE_GT " + json.dumps({k: gt[k] for k in ("target", "robot")}, default=str), flush=True)
app.close()
