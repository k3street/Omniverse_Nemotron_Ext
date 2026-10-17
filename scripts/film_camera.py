#!/usr/bin/env python3
"""Aim a film camera at a recorded episode's work by line of sight: from a
replay's log (the robot's logged joint angles, the work's logged points) the
robot's links are posed by forward kinematics (HomeHero's geometry and its
collision cache), and each candidate eye is scored by the fraction of sight
lines - to every work point, every sampled tick of the chosen stages - that
pass clear of every link. A camera aimed by guesswork filmed an empty hook
for 40 s, and the next guess sat behind the room's wall (2026-10-15).

    film_camera.py <scene_dir> --stages push,open --points loop_world,tip_right_world,tip_world.index
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))


def box_surface_points(box, spacing=0.02):
    """A solid box's six faces as points `spacing` apart (xmin,xmax,ymin,ymax,
    zmin,zmax): obstacles for sight lines that no robot link covers."""
    b = [float(v) for v in box]
    xs, ys, zs = (np.arange(b[2 * k], b[2 * k + 1] + 1e-9, spacing) for k in range(3))
    out = []
    for k, (lo_, hi_) in enumerate(((b[0], b[1]), (b[2], b[3]), (b[4], b[5]))):
        for v in (lo_, hi_):
            grids = [xs, ys, zs]
            grids[k] = np.array([v])
            out.append(np.stack(np.meshgrid(*grids, indexing="ij"), -1).reshape(-1, 3))
    return np.vstack(out)


def sight_clear(eye, targets, obstacles, radius=0.015):
    """For each target: does the segment eye -> target pass farther than
    radius from every obstacle point (the last 3 cm before the target
    excepted: the target's own surroundings - the finger on the loop)?"""
    eye = np.asarray(eye, float)
    out = []
    for t in np.atleast_2d(targets):
        d = t - eye
        L = float(np.linalg.norm(d))
        u = d / L
        s = (obstacles - eye) @ u
        m = (s > 0.05) & (s < L - 0.03)
        if not m.any():
            out.append(True)
            continue
        perp = np.linalg.norm((obstacles[m] - eye) - np.outer(s[m], u), axis=1)
        out.append(bool(perp.min() > radius))
    return np.asarray(out)


def _get(entry, key):
    v = entry
    for k in key.split("."):
        v = v.get(k) if isinstance(v, dict) else None
        if v is None:
            return None
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--stages", required=True, help="comma list of the log's stage names to film")
    ap.add_argument("--points", required=True, help="comma list of logged world points (dotted keys)")
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--room", default="", help="xmin,xmax,ymin,ymax,zmin,zmax the eye must stay inside")
    ap.add_argument("--distance", default="0.40,0.55,0.70")
    ap.add_argument("--exclude", action="append", default=[],
                    help="xmin,xmax,ymin,ymax,zmin,zmax a solid box (an appliance): no eye inside it, no sight line through it (repeatable)")
    args = ap.parse_args()
    from pinch_place_planner import CACHE
    from r5_arm_side import both_arms_geometry

    d = Path(args.scene_dir)
    spec = json.loads((d / "spec.json").read_text())
    log = json.loads((d / "grasp" / "log.json").read_text())
    stages = set(args.stages.split(","))
    rows = [r for r in log if r["stage"] in stages][::args.every]
    if not rows:
        raise SystemExit("no logged ticks in those stages")
    rb = spec["robot"]
    yaw = np.radians(rb["cad_yaw_deg"])
    g = both_arms_geometry(os.path.expanduser(rb["urdf"]), list(rb["position"]) + [0, 0, float(np.sin(yaw / 2)), float(np.cos(yaw / 2))])
    man = json.loads(CACHE.with_suffix(".json").read_text())
    data = np.load(CACHE.with_suffix(".npz"))
    rng = np.random.default_rng(0)
    links = {}
    for i, sh in enumerate(man["shapes"]):
        links.setdefault(sh["link"], []).append(data[f"p{i}"])
    for k, v in links.items():
        P = np.vstack(v)
        links[k] = P[rng.choice(len(P), min(len(P), 250), replace=False)]
    keys = args.points.split(",")
    ticks = []
    for r in rows:
        q = np.asarray(g.rest, float).copy()
        if r.get("arm_q"):
            q[:8] = r["arm_q"]
        if r.get("arm_right_q"):
            q[14:22] = r["arm_right_q"]
        g.forward(q)
        obs = np.vstack([p + P @ R.T for ln, P in links.items() for p, R in [g.pose(ln)]])
        pts = [np.asarray(_get(r, k), float) for k in keys if _get(r, k) is not None]
        if pts:
            ticks.append((obs, np.vstack(pts)))
    allpts = np.vstack([t for _, t in ticks])
    target = allpts.mean(0)
    room = [float(v) for v in args.room.split(",")] if args.room else None
    # solid boxes the robot's links do not cover (a refrigerator: the first aim put the
    # eye inside it, 2026-10-16): their faces sampled as obstacle points for every tick
    boxes = [[float(v) for v in e.split(",")] for e in args.exclude]
    box_pts = [box_surface_points(b) for b in boxes]
    if box_pts:
        extra = np.vstack(box_pts)
        ticks = [(np.vstack([o, extra]), t) for o, t in ticks]
    best = []
    for dist in [float(v) for v in args.distance.split(",")]:
        for az in np.radians(np.arange(0, 360, 15)):
            for el in np.radians([0, 15, 30, 45]):
                eye = target + dist * np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
                if room and not (room[0] < eye[0] < room[1] and room[2] < eye[1] < room[3] and room[4] < eye[2] < room[5]):
                    continue
                if any(b[0] < eye[0] < b[1] and b[2] < eye[1] < b[3] and b[4] < eye[2] < b[5] for b in boxes):
                    continue
                score = float(np.mean(np.concatenate([sight_clear(eye, t, o) for o, t in ticks])))
                best.append((score, dist, np.degrees(az), np.degrees(el), eye))
    best.sort(key=lambda b: (-b[0], b[1]))
    print(f"[film] {len(ticks)} ticks of {sorted(stages)}, {len(keys)} points each; aim at {np.round(target, 3).tolist()}", flush=True)
    for sc, dist, az, el, eye in best[:8]:
        print(f"  clear {sc:.3f}  distance {dist:.2f} azimuth {az:5.0f} elevation {el:3.0f}  eye {np.round(eye, 3).tolist()}", flush=True)
    sc, dist, az, el, eye = best[0]
    print("FILM_CAMERA " + json.dumps({"eye": np.round(eye, 3).tolist(), "target": np.round(target, 3).tolist(), "clear": round(sc, 3)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
