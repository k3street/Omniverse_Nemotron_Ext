#!/usr/bin/env python3
"""Pourable materials in Newton (headless MPM): granular contents, and what it cannot do for water.

Newton's implicit MPM solver treats a liquid as particles on a background
grid; sim-ready assets are its colliders. No Isaac/Kit needed (Warp on the
GPU), so this runs beside an open Isaac session.

  pour <cup_asset_id> [--fill 0.7] [--tilt 115]
        fill the cup's cavity with water to a fraction of its depth, tip it
        over its base edge on the side away from the handle, and measure: the
        volume kept, poured and lost THROUGH the walls (a leak is a
        collider failure, not a pour).

Evidence (volumes in mL, particle counts) goes to the queue entry under
"liquid_newton"; side-view frames go to workspace/liquid_tests/.

WHAT THIS SOLVER CAN AND CANNOT DO (measured 2026-10-03 on white_mug):
Newton's implicit MPM is a granular / viscoplastic model (sand, mud, snow,
beads). It is NOT a water simulator:
  - an upright mug's water column settles to 55-65% of its height whatever
    the viscosity or tensile yield (a packing-fraction density constraint,
    not incompressibility);
  - a container that MOVES does not carry it: tilting the mug 20 deg over
    3 s let half the water run out under the lifting floor (the static mug
    holds it, with a watertight collider >= 2.5 cells thick).
Use it for granular contents (a bean bag's beads, pills, sugar); water
belongs to PhysX PBD particle fluids in Isaac Sim.

Run with the Newton venv:
    .venv-newton/bin/python scripts/liquid_newton.py pour white_mug
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
QUEUE_DIR = REPO / "workspace" / "review_queue"
OUT = REPO / "workspace" / "liquid_tests"

WATER = {"density": 1000.0, "viscosity": 0.5, "tensile_yield_ratio": 1.0, "friction": 0.0}


def cup_cavity(points: np.ndarray):
    """Axis, inner radius, floor and rim of a cup from its vertices: the rim
    ring's centre is the axis (a handle makes the box off-centre); the inner
    wall at the rim is the smallest radius there; the floor is the highest
    point near the axis in the lower half."""
    lo, hi = points.min(0), points.max(0)
    top = points[points[:, 2] > hi[2] - 0.02 * (hi[2] - lo[2])]
    centre = np.median(top[:, :2], axis=0)
    r = np.linalg.norm(points[:, :2] - centre, axis=1)
    r_top = np.linalg.norm(top[:, :2] - centre, axis=1)
    r_outer = float(np.percentile(r_top, 95))
    inner = r_top[r_top < 0.97 * r_outer]
    r_inner = float(inner.min()) if len(inner) else 0.85 * r_outer
    near = (r < 0.3 * r_inner) & (points[:, 2] < lo[2] + 0.5 * (hi[2] - lo[2]))
    floor = float(points[near, 2].max()) if near.any() else float(lo[2])
    return centre, r_inner, r_outer, floor, float(hi[2])


def revolve(profile_rz, segments=48):
    """A closed surface of revolution about +Z from a profile polyline in
    (r, z) that starts and ends on the axis (r = 0): a watertight solid with
    outward normals - what an MPM collider needs and an artist's mug is not."""
    prof = [tuple(map(float, p)) for p in profile_rz]
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    verts, rings = [], []
    for r, z in prof:
        if r == 0.0:
            rings.append([len(verts)])
            verts.append((0.0, 0.0, z))
        else:
            rings.append(list(range(len(verts), len(verts) + segments)))
            verts += [(r * np.cos(t), r * np.sin(t), z) for t in a]
    tris = []
    for ra, rb in zip(rings[:-1], rings[1:]):
        for j in range(segments):
            k = (j + 1) % segments
            if len(ra) == 1 and len(rb) == 1:
                continue
            if len(ra) == 1:
                tris.append((ra[0], rb[k], rb[j]))
            elif len(rb) == 1:
                tris.append((ra[j], ra[k], rb[0]))
            else:
                tris += [(ra[j], ra[k], rb[k]), (ra[j], rb[k], rb[j])]
    v, t = np.array(verts), np.array(tris)
    # outward: the divergence theorem gives a positive volume
    vol = np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6.0
    return v, (t if vol > 0 else t[:, ::-1])


def cup_collider(r_in, r_out, floor, rim, base=0.0):
    """The cup as liquid sees it: a solid floor, a wall from r_in to r_out up
    to the rim. Profile: axis bottom -> out -> up the outside -> over the rim
    -> down the inside -> across the floor -> axis."""
    return revolve([(0.0, base), (r_out, base), (r_out, rim), (r_in, rim), (r_in, floor), (0.0, floor)])


def fill_points(centre, r_in, floor, level, spacing, margin):
    """A jittered grid of particles filling the cavity cylinder to `level`."""
    xs = np.arange(-r_in, r_in + 1e-9, spacing)
    zs = np.arange(floor + margin, level, spacing)
    g = np.stack(np.meshgrid(xs, xs, zs, indexing="ij"), -1).reshape(-1, 3)
    g = g[np.hypot(g[:, 0], g[:, 1]) < r_in - margin]
    rng = np.random.default_rng(7)
    g += (rng.random(g.shape) - 0.5) * 0.3 * spacing
    g[:, :2] += centre
    return g


def pour(asset_id: str, fill: float, tilt_deg: float, seconds: float, voxel: float) -> str:
    import newton
    import warp as wp
    from newton.solvers import SolverImplicitMPM

    from cloth_proxy import posed_mesh

    entry = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())
    pts, tris = posed_mesh(entry["file"])
    pts = pts - np.array([0.0, 0.0, pts[:, 2].min()])        # standing on z = 0
    centre, r_in, r_out, floor, rim = cup_cavity(pts)
    level = floor + fill * (rim - floor)
    spacing = voxel / 3.0                                       # 3 particles per cell per axis, as Newton's examples
    water = fill_points(centre, r_in, floor, level, spacing, margin=voxel)
    vol_ml = math.pi * (r_in - voxel) ** 2 * (level - floor - voxel) * 1e6
    m_each = WATER["density"] * vol_ml * 1e-6 / len(water)

    # tip about the rim on the side away from the handle (where the box
    # reaches less far from the axis)
    lo, hi = pts.min(0), pts.max(0)
    away = -1.0 if (hi[0] - centre[0]) > (centre[0] - lo[0]) else 1.0
    # pivot on the BASE edge: tipping about a rim point swung the body down
    # through the floor; a cup knocked over turns about the edge it rests on
    pivot = np.array([centre[0] + away * r_out, centre[1], 0.0])

    builder = newton.ModelBuilder()
    SolverImplicitMPM.register_custom_attributes(builder)
    cup = builder.add_body(xform=wp.transform(wp.vec3(*pivot), wp.quat_identity()), mass=0.3,
                           inertia=wp.mat33(np.eye(3) * 1e-3), label="cup")
    # the liquid collides with a clean revolved shell of the measured cavity;
    # the artist's mesh (not watertight, walls barely a grid cell thick)
    # leaked 15% of an upright mug's water
    # ...and it must be at least ~2.5 grid cells thick or the liquid tunnels
    # through (an upright mug's 3-5 mm floor let the water drop to the
    # ground): thicken outward and downward, never into the cavity
    wall = max(r_out - r_in, 2.5 * voxel)
    cv, ct = cup_collider(r_in, r_in + wall, floor, rim, base=min(0.0, floor - 2.5 * voxel))
    cv[:, :2] += centre
    local = (cv - pivot).astype(np.float32)
    builder.add_shape_mesh(body=cup, mesh=newton.Mesh(local, ct.reshape(-1).astype(np.int32),
                                                       compute_inertia=False, is_solid=False),
                           cfg=newton.ModelBuilder.ShapeConfig(mu=0.1))
    builder.add_particles(pos=water.tolist(), vel=np.zeros_like(water).tolist(),
                          mass=[m_each] * len(water), radius=[0.5 * spacing] * len(water))
    builder.add_ground_plane()
    model = builder.finalize()
    model.set_gravity((0.0, 0.0, -9.81))
    model.mpm.viscosity.fill_(WATER["viscosity"])
    model.mpm.tensile_yield_ratio.fill_(WATER["tensile_yield_ratio"])
    model.mpm.friction.fill_(WATER["friction"])
    cfg = SolverImplicitMPM.Config()
    cfg.voxel_size = voxel
    cfg.max_iterations = 100
    cfg.tolerance = 1e-5
    solver = SolverImplicitMPM(model, config=cfg)
    s0, s1 = model.state(), model.state()

    fps, sub = 120, 2
    dt = 1.0 / fps / sub
    tip_s = 0.6 * seconds
    frames = []
    # a +turn about +Y carries points above the pivot toward +X: tip toward `away`
    axis = np.array([0.0, away, 0.0])

    def set_cup(t, state):
        a = math.radians(tilt_deg) * min(1.0, t / tip_s)
        w = math.radians(tilt_deg) / tip_s if t < tip_s else 0.0
        q = wp.quat_from_axis_angle(wp.vec3(*axis), a)
        state.body_q.assign(np.array([[*pivot, q[0], q[1], q[2], q[3]]], dtype=np.float32))
        state.body_qd.assign(np.array([[0.0, 0.0, 0.0, *(axis * w)]], dtype=np.float32))  # (linear, angular)
        return a

    t = 0.0
    for f in range(int(seconds * fps)):
        for _ in range(sub):
            set_cup(t, s0)
            solver.step(s0, s1, None, None, dt)
            s0, s1 = s1, s0
            t += dt
        if f % 12 == 0:
            frames.append((t, s0.particle_q.numpy().copy(), set_cup(t, s0)))
    q = s0.particle_q.numpy()
    if not np.isfinite(q).all():
        return f"ERROR {asset_id}: liquid solve diverged"

    # where the water ended: in the cup (in its frame, inside the cavity),
    # or out - and if out, did it leave over the rim or through a wall?
    a = math.radians(tilt_deg)
    rot = np.array(wp.quat_to_matrix(wp.quat_from_axis_angle(wp.vec3(*axis), a))).reshape(3, 3)
    in_cup_frame = (q - pivot) @ rot + pivot                   # undo the tilt
    rr = np.linalg.norm(in_cup_frame[:, :2] - centre, axis=1)
    kept = (rr < r_in + voxel) & (in_cup_frame[:, 2] > floor - voxel) & (in_cup_frame[:, 2] < rim + voxel)
    out = ~kept
    poured = out & (q[:, 2] < rim)                             # out and down: poured
    stuck = out & ~poured
    ml = vol_ml / len(water)
    level_end = float(np.percentile(in_cup_frame[kept, 2], 98)) if kept.any() else 0.0
    evidence = {"date": date.today().isoformat(), "method": "newton_implicit_mpm_pour",
                "particles": int(len(water)), "fill_ml": round(vol_ml, 1), "tilt_deg": tilt_deg,
                "kept_ml": round(kept.sum() * ml, 1), "poured_ml": round(poured.sum() * ml, 1),
                "elsewhere_ml": round(stuck.sum() * ml, 1),
                "level_mm_start": round(level * 1000, 1), "level_mm_end": round(level_end * 1000, 1),
                "cavity": {"inner_radius_mm": round(r_in * 1000, 1), "floor_mm": round(floor * 1000, 1),
                           "rim_mm": round(rim * 1000, 1)}}
    _frames_png(asset_id, frames, pts, pivot, axis)
    entry["liquid_newton"] = evidence
    (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return (f"{asset_id}: {len(water)} particles, {vol_ml:.0f} mL in a {r_in * 2000:.0f} mm cavity; "
            f"tipped {tilt_deg:g} deg: kept {evidence['kept_ml']} mL, poured {evidence['poured_ml']} mL, "
            f"elsewhere {evidence['elsewhere_ml']} mL; surface {level * 1000:.0f} -> {level_end * 1000:.0f} mm")


def _frames_png(asset_id, frames, pts, pivot, axis):
    """Side views (x-z) of the cup and the water at a few moments. The Newton
    venv has no plotting library: the frames are saved as arrays and drawn by
    the system python's Pillow."""
    import subprocess

    import warp as wp

    OUT.mkdir(parents=True, exist_ok=True)
    n = min(6, len(frames))
    pick = [frames[int(i * (len(frames) - 1) / max(1, n - 1))] for i in range(n)]
    cups, waters, labels = [], [], []
    for t, q, a in pick:
        rot = np.array(wp.quat_to_matrix(wp.quat_from_axis_angle(wp.vec3(*axis), a))).reshape(3, 3)
        cups.append(((pts[::5] - pivot) @ rot.T + pivot)[:, [0, 2]])
        waters.append(q[:, [0, 2]])
        labels.append(f"t={t:.2f}s {math.degrees(a):.0f}deg")
    npz = OUT / f"{asset_id}_pour_frames.npz"
    np.savez(npz, **{f"cup{i}": c for i, c in enumerate(cups)}, **{f"water{i}": w for i, w in enumerate(waters)},
             labels=np.array(labels))
    png = OUT / f"{asset_id}_pour.png"
    draw = f"""
import numpy as np
from PIL import Image, ImageDraw
d = np.load({str(npz)!r}); labels = list(d["labels"]); n = len(labels)
W, H = 320, 300
allp = np.concatenate([d[k] for k in d.files if k != "labels"])
lo, hi = allp.min(0), allp.max(0); lo[1] = max(lo[1], -0.01)
s = min((W - 20) / (hi[0] - lo[0]), (H - 40) / (hi[1] - lo[1]))
img = Image.new("RGB", (W * n, H), "white"); g = ImageDraw.Draw(img)
for i in range(n):
    for key, col in ((f"cup{{i}}", (150, 150, 150)), (f"water{{i}}", (30, 100, 220))):
        for x, z in d[key]:
            u, v = i * W + 10 + (x - lo[0]) * s, H - 10 - (z - lo[1]) * s
            if 0 <= v < H: g.point((u, v), fill=col)
    g.line([(i * W + 5, H - 10), (i * W + W - 5, H - 10)], fill=(90, 70, 50))
    g.text((i * W + 8, 6), labels[i], fill=(0, 0, 0))
img.save({str(png)!r})
"""
    subprocess.run(["/usr/bin/python3", "-c", draw], check=False)
    return png


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pour")
    p.add_argument("asset")
    p.add_argument("--fill", type=float, default=0.7)
    p.add_argument("--tilt", type=float, default=115.0)
    p.add_argument("--seconds", type=float, default=2.5)
    p.add_argument("--voxel", type=float, default=0.004)
    p.add_argument("--viscosity", type=float, default=WATER["viscosity"])
    p.add_argument("--tyr", type=float, default=WATER["tensile_yield_ratio"], help="tensile yield ratio")
    args = ap.parse_args()
    import warp as wp
    wp.init()
    if args.cmd == "pour":
        WATER.update(viscosity=args.viscosity, tensile_yield_ratio=args.tyr)
        print(pour(args.asset, args.fill, args.tilt, args.seconds, args.voxel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
