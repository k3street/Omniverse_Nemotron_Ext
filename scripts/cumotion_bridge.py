#!/usr/bin/env python3
"""Call cuMotion's reach service (cumotion_reach.py, Isaac venv) from a
planner in HomeHero's venv, and keep only the paths HomeHero's own mesh
checker accepts.

    from cumotion_bridge import cumotion_reach
    path, info = cumotion_reach(req, start8, goals8, checker, hand, screen)

`path` is the first candidate (densified to 0.02 rad steps) that passes
`screen(checker, ...)`, or None; `info` carries the service's timings and
every candidate's verdict.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
ISAAC = Path("/home/kimate/Documents/Github/isaacsim/_build/linux-aarch64/release")
ISAAC_PY = Path("/home/kimate/isaacsim-6.0.1-venv/bin/python")
CUMOTION = ISAAC / "exts/isaacsim.robot_motion.cumotion/pip_prebundle"


def cumotion_reach(req: dict, start, goals, checker, hand, screen, seeds: int = 8, step_rad: float = 0.02,
                   timeout_s: float = 300.0):
    payload = {"base_pose": req["base_pose"], "joint_positions": req["joint_positions"], "obstacles": req["obstacles"],
               "start": np.asarray(start, float)[:8].tolist(), "goals": [np.asarray(g, float)[:8].tolist() for g in goals],
               "hand": np.asarray(hand, float).tolist(), "seeds": seeds, "slide_top": float(req.get("slide_top", -0.0095)),
               # the planner's edge step: at 0.05 rad a fingertip 0.9 m out sweeps 4.5 cm between checks, more
               # than a pawn (measured: thumb-through-piece paths the mesh screen rejected); 0.01 rad is 9 mm
               "step_size": float(req.get("cumotion_step_rad", 0.01))}
    env = dict(os.environ, LD_LIBRARY_PATH=str(CUMOTION / "_cumotion_libs"), PYTHONPATH=str(CUMOTION))
    t0 = time.perf_counter()
    r = subprocess.run([str(ISAAC_PY), str(REPO / "scripts" / "cumotion_reach.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=timeout_s)
    if r.returncode != 0:
        return None, {"error": r.stderr[-800:], "seconds": round(time.perf_counter() - t0, 3)}
    reply = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])
    info = {"service": reply["world"], "subprocess_s": round(time.perf_counter() - t0, 3), "candidates": []}
    hand = np.asarray(hand, float)
    for gi, g in enumerate(reply["goals"]):
        for pi, P in enumerate(g["paths"]):
            P = np.asarray(P, float)
            dense = [P[0]]
            for a, b in zip(P[:-1], P[1:]):
                n = max(1, int(np.ceil(np.max(np.abs(b - a)) / step_rad)))
                dense += [a * (1 - f) + b * f for f in np.linspace(0, 1, n + 1)[1:]]
            res = screen(checker, [np.r_[q, hand] for q in dense])
            verdict = {"goal": gi, "candidate": pi, "waypoints": len(P), "coal": res["passed"],
                       "first_collision": (res["collisions"][0]["pairs"][:1] if res["collisions"] else None)}
            info["candidates"].append(verdict)
            if res["passed"]:
                # the graph planner's path is not smoothed: greedy shortcuts
                # over checked straight segments (the RRT's own paths are
                # shortcut; this one was 1700 ticks of reach at per-tick limits)
                short = shortcut(dense, checker, hand, step_rad)
                verdict["shortcut_waypoints"] = len(short)
                info["chosen"] = verdict
                info["tries"] = g["tries"]
                return short, info
        info.setdefault("tries", []).extend(g["tries"])
    return None, info


def shortcut(dense, checker, hand, step_rad=0.02, passes=3):
    """Greedy shortcuts: from each waypoint, jump to the farthest later one
    reachable by a collision-free straight segment (checked every step_rad)."""
    hand = np.asarray(hand, float)
    pts = [np.asarray(q, float) for q in dense]

    def clear(a, b):
        n = max(1, int(np.ceil(np.max(np.abs(b - a)) / step_rad)))
        return all(checker(np.r_[a * (1 - f) + b * f, hand]) for f in np.linspace(0, 1, n + 1)[1:])
    for _ in range(passes):
        out, i = [pts[0]], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not clear(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        if len(out) == len(pts):
            break
        pts = out
    # re-densify so every tick's step stays within the per-tick limits downstream
    dense_out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.max(np.abs(b - a)) / step_rad)))
        dense_out += [a * (1 - f) + b * f for f in np.linspace(0, 1, n + 1)[1:]]
    return dense_out
