#!/usr/bin/env python3
"""Reach planning for the R5 arm with NVIDIA cuMotion, as a service the
grasp planners call (they run in HomeHero's venv; this runs in the Isaac
venv, where cuMotion lives): one JSON request on stdin, one reply on stdout.

Request: {"base_pose", "joint_positions", "obstacles" (world boxes),
          "start" (8 arm joints), "hand" (6 actuated channels, held),
          "goals": [[8]...], "seeds": 8,
          "slide_top": -0.0095, "step_size": 0.05}
Reply:   {"goals": [{"paths": [[...8-vectors...], ...], "tries": [...],
          "seconds": ...}, ...], "world": {...}}

Per goal, up to `seeds` planner runs with different seeds; every path is
screened with cuMotion's robot-world inspector (world and self-collision at
each interpolated waypoint) and the slide headroom, and the clean ones are
returned in the order found - the caller screens them again with its own
mesh checker (the shoulder housing is not world-checked here; see
cumotion_r5.MOUNT) and takes the first that passes.

    LD_LIBRARY_PATH=<cumotion libs> <isaac venv python> scripts/cumotion_reach.py < request.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from cumotion_r5 import ARM, PALM, Boxes, hand_joint_positions, load  # noqa: E402


def main() -> int:
    req = json.loads(sys.stdin.read())
    t0 = time.perf_counter()
    hand = hand_joint_positions(req["hand"]) if req.get("hand") else {}
    cumotion, desc = load(joint_positions={**req.get("joint_positions", {}), **hand})
    kin = desc.kinematics()
    assert [desc.cspace_coord_name(i) for i in range(8)] == ARM
    boxes = Boxes(cumotion, req["base_pose"])
    for o in req["obstacles"]:
        boxes.add(o["min"], o["max"], inflate=float(req.get("inflate", 0.003)))
    n_static = boxes.add_static_parts(desc)
    n_sdf = boxes.add_sdf_parts(desc)
    view = boxes.view()
    insp = cumotion.create_robot_world_inspector(desc, view)
    cfg = cumotion.create_default_motion_planner_config(desc, PALM, view)
    PV = cumotion.MotionPlannerConfig.ParamValue
    cfg.set_param("step_size", PV(float(req.get("step_size", 0.05))))
    cfg.set_param("max_iterations", PV(int(req.get("max_iterations", 50000))))
    slide_top = float(req.get("slide_top", -0.0095))
    seeds = int(req.get("seeds", 8))
    q0 = np.asarray(req["start"], float)[:8]
    setup_s = time.perf_counter() - t0
    status0 = {"obstacle": bool(insp.in_collision_with_obstacle(q0)), "self": bool(insp.in_self_collision(q0))}
    out = {"world": {"scene_boxes": len(req["obstacles"]), "static_boxes": n_static, "sdf_parts": n_sdf,
                     "setup_s": round(setup_s, 3), "start": status0}, "goals": []}
    for goal in req["goals"]:
        q1 = np.asarray(goal, float)[:8].copy()
        q1[0] = min(q1[0], slide_top)
        tg = time.perf_counter()
        paths, tries = [], []
        if insp.in_collision_with_obstacle(q1) or insp.in_self_collision(q1):
            tries.append("goal in collision")
        else:
            for seed in range(seeds):
                cfg.set_param("seed", PV(123456 + seed))
                planner = cumotion.create_motion_planner(cfg)
                res = planner.plan_to_cspace_target(q0, q1, generate_interpolated_path=True)
                if not res.path_found:
                    tries.append("no path")
                    continue
                P = np.asarray(res.interpolated_path if len(res.interpolated_path) else res.path)
                bad_self = int(sum(1 for q in P if insp.in_self_collision(q)))
                bad_obs = int(sum(1 for q in P if insp.in_collision_with_obstacle(q)))
                over = int(np.sum(P[:, 0] > max(slide_top, float(q0[0])) + 1e-6))
                tries.append(f"self {bad_self}, obstacle {bad_obs}, slide over {over}")
                if not (bad_self or bad_obs or over):
                    paths.append(P.tolist())
        out["goals"].append({"paths": paths, "tries": tries, "seconds": round(time.perf_counter() - tg, 3)})
    out["world"]["total_s"] = round(time.perf_counter() - t0, 3)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
