#!/usr/bin/env python3
"""Let a built scene's cloth come to rest in PhysX and write where it rests:
the planner plans the pinch against the draped shape, not the flat sheet it
was placed as. Rest, not a fixed time: a 3 s drape of the towel's flap was
still swinging in (the pinch of 2026-10-15 closed 2 cm in front of it after
the 21 s approach). Settling runs until no point moved more than --rest-mm
over the last two seconds, capped at --seconds; the time it took is recorded
and the replay settles at least that long before its approach. (Kit; the
Isaac slot.)

    source scripts/isaac_slot.sh && <isaac python.sh> scripts/settle_cloth.py workspace/scenes/<name> [--seconds 30] [--rest-mm 0.5]
writes <scene>/cloth_settled.npz (points: the render mesh in world, per deformable object)
   and <scene>/cloth_settled.json (settled_s, residual motion, the drape's trajectory)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scene_dir")
ap.add_argument("--seconds", type=float, default=30.0, help="the cap")
ap.add_argument("--min-seconds", type=float, default=3.0)
ap.add_argument("--rest-mm", type=float, default=0.5, help="at rest: the centroid drifted no more than this over the last 2 s (the lowest point twice that)")
args = ap.parse_args()
scene_dir = Path(args.scene_dir).resolve()
spec = json.loads((scene_dir / "spec.json").read_text())

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})
import numpy as np  # noqa: E402
import omni.physx  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # noqa: E402

omni.usd.get_context().open_stage(str(scene_dir / "scene.usda"))
for _ in range(30):
    app.update()
stage = omni.usd.get_context().get_stage()
px = omni.physx.get_physx_interface()
timeline = omni.timeline.get_timeline_interface()
timeline.play()
for _ in range(10):
    app.update()
HZ = int(spec.get("physics_hz", 120))
DT = 1.0 / HZ
cloths = {}
for o in stage.GetPrimAtPath("/World/Objects").GetChildren():
    if any("DeformableBody" in a for q in Usd.PrimRange(o) for a in q.GetAppliedSchemas()):
        sim = next((q for q in Usd.PrimRange(o) if q.GetName() == "simMesh"), None)
        mesh = next((q for q in Usd.PrimRange(o) if q.IsA(UsdGeom.Mesh) and q.GetName() != "simMesh"), None)
        cloths[o.GetName()] = (sim, mesh)
# cooking is asynchronous: wait for every simulation mesh to have points
for _ in range(600):
    app.update()
    if all(sim and UsdGeom.Mesh(sim).GetPointsAttr().Get() and len(UsdGeom.Mesh(sim).GetPointsAttr().Get()) > 3 for sim, _ in cloths.values()):
        break
cooked = {n: (len(UsdGeom.Mesh(sim).GetPointsAttr().Get()) if sim and UsdGeom.Mesh(sim).GetPointsAttr().Get() else 0) for n, (sim, _) in cloths.items()}
print("[cloth] simulation meshes:", cooked, flush=True)


def world_points():
    xc = UsdGeom.XformCache()
    out = {}
    for name, (sim, mesh) in cloths.items():
        M = xc.GetLocalToWorldTransform(mesh)
        out[name] = np.array([list(M.Transform(Gf.Vec3d(p))) for p in UsdGeom.Mesh(mesh).GetPointsAttr().Get()], float)
    return out


CHUNK = 0.25                                           # the motion is measured over each quarter second
WINDOW = 2.0                                           # rest is judged over the last two seconds
t = 0.0
prev = None
trajectory = []                                        # per chunk: t, per cloth the max vertex move, centroid and lowest point
rest_at = None
while t < args.seconds - 1e-9:
    for _ in range(int(round(CHUNK * HZ))):
        px.update_simulation(DT, t)
        t += DT
    px.update_transformations(False, True, False, False)
    app.update()
    P = world_points()
    move = {n: (float(np.linalg.norm(P[n] - prev[n], axis=1).max()) if prev else float("nan")) for n in P}
    prev = P
    trajectory.append({"t": round(t, 3), "max_move_mm": {n: round(1000 * m, 3) for n, m in move.items()},
                       "centroid": {n: P[n].mean(0).round(5).tolist() for n in P},
                       "lowest": {n: P[n][np.argmin(P[n][:, 2])].round(4).tolist() for n in P}})
    # at rest: over the last WINDOW the centroid moved no more than rest_mm and
    # the lowest point no more than twice that, for every cloth. The max vertex
    # move is logged, not judged: a hanging flap swings 2-3 mm a quarter second
    # for as long as it hangs (verify_cloth_friction.py), while a creeping sheet
    # shows in its centroid
    back = [r for r in trajectory if r["t"] <= t - WINDOW + 1e-6]
    if t >= args.min_seconds and back:
        ref = back[-1]
        drift = {n: 1000 * float(np.linalg.norm(np.asarray(trajectory[-1]["centroid"][n]) - np.asarray(ref["centroid"][n]))) for n in P}
        low = {n: 1000 * abs(trajectory[-1]["lowest"][n][2] - ref["lowest"][n][2]) for n in P}
        trajectory[-1]["drift_mm"] = {n: round(v, 3) for n, v in drift.items()}
        if all(drift[n] <= args.rest_mm and low[n] <= 2 * args.rest_mm for n in P):
            rest_at = t
            break
P = world_points()
residual_mm = trajectory[-1].get("drift_mm", {}) if trajectory else {}
for name, pts in P.items():
    print(f"[cloth] {name}: {len(pts)} points, x {pts[:, 0].min():.3f}..{pts[:, 0].max():.3f} y {pts[:, 1].min():.3f}..{pts[:, 1].max():.3f} "
          f"z {pts[:, 2].min():.3f}..{pts[:, 2].max():.3f}; lowest point {pts[np.argmin(pts[:, 2])].round(3).tolist()}", flush=True)
np.savez(scene_dir / "cloth_settled.npz", **P)
# the gap between the cloth and its support: the points over the support's top,
# against build.json's support_top_m (a 2 cm gap was PhysX's default contact
# offset holding the towel off the table, 2026-10-15)
gap_mm = {}
build = json.loads((scene_dir / "build.json").read_text()) if (scene_dir / "build.json").exists() else {}
if "support_top_m" in build and "support_range" in build:
    (x0, y0, _), (x1, y1, _) = build["support_range"]
    top = float(build["support_top_m"])
    for name, pts in P.items():
        over = pts[(pts[:, 0] > x0) & (pts[:, 0] < x1) & (pts[:, 1] > y0) & (pts[:, 1] < y1) & (pts[:, 2] > top - 0.01)]
        if len(over):
            gap_mm[name] = round(1000 * float(np.median(over[:, 2]) - top), 2)
            print(f"[cloth] {name} rests {gap_mm[name]:.2f} mm above the support (median of {len(over)} points over it)", flush=True)
meta = {"settled_s": round(t, 3), "at_rest": rest_at is not None, "rest_mm": args.rest_mm, "cap_s": args.seconds,
        "residual_move_mm": residual_mm, "gap_above_support_mm": gap_mm, "physics_hz": HZ, "cooked": cooked, "trajectory": trajectory}
(scene_dir / "cloth_settled.json").write_text(json.dumps(meta, indent=1))
print("CLOTH_SETTLED " + json.dumps({n: {"points": len(pts), "min_z": round(float(pts[:, 2].min()), 4), "cooked": cooked[n]} for n, pts in P.items()}
                                    | {"settled_s": round(t, 2), "at_rest": rest_at is not None, "residual_move_mm": residual_mm,
                                       "gap_above_support_mm": gap_mm}), flush=True)
app.close()
