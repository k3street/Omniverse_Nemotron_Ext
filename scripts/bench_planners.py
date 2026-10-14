#!/usr/bin/env python3
"""Time NVIDIA cuMotion on the R5 reach that HomeHero's RRT could not make:
the arm from the rest pose (and from HomeHero's reset pose) to the staging
and pre-grasp configurations over the chessboard, among the scene's
obstacle boxes. The same start, goals and boxes HomeHero's planner was
given (bench_goals.json, written by the pinch planner's debugging).

Measured per problem: wall time of the planner call (the first call
separately - it carries CUDA start-up), whether a path was found, its
length in joint space, and every interpolated waypoint checked again with
cuMotion's robot-world inspector (obstacles and self-collision). The paths
are saved for HomeHero's coal checker to screen independently
(screen_bench_paths.py, in HomeHero's venv).

Also timed: cuMotion's collision-free IK to the same palm poses, from the
rest and reset seeds - the step a grasp planner would use in place of
pinocchio's unconstrained IK plus a separate collision check.

    LD_LIBRARY_PATH=<cumotion libs> <isaac venv python> scripts/bench_planners.py workspace/scenes/aida_r5_dining_chess
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
    scene = Path(sys.argv[1]).resolve()
    bench = json.loads((scene / "grasp_plan" / "bench_goals.json").read_text())
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    cumotion, desc = load(joint_positions={**bench.get("joint_positions", {}), **hand_joint_positions(bench["hand_open"])})
    cumotion.set_log_level(cumotion.LogLevel.ERROR) if hasattr(cumotion, "LogLevel") else None
    kin = desc.kinematics()
    assert [desc.cspace_coord_name(i) for i in range(8)] == ARM
    boxes = Boxes(cumotion, bench["base_pose"])
    for o in bench["obstacles"]:
        boxes.add(o["min"], o["max"])
    n_static = boxes.add_static_parts(desc)
    n_sdf = boxes.add_sdf_parts(desc)
    print(f"world: {len(bench['obstacles'])} scene boxes + {n_static} static robot part boxes + {n_sdf} SDF parts")
    view = boxes.view()
    insp = cumotion.create_robot_world_inspector(desc, view)
    starts = {"rest": np.asarray(bench["start"][:8], float), "reset": np.asarray(bench["reset"][:8], float)}
    goals = {}
    for i, g in enumerate(bench["goals"]):
        goals[f"g{i}_pre"] = np.asarray(g["q_pre"], float)
        if g.get("q_staging"):
            goals[f"g{i}_staging"] = np.asarray(g["q_staging"], float)
        goals[f"g{i}_grasp"] = np.asarray(g["q_grasp"], float)

    def status(q):
        return {"obstacle": bool(insp.in_collision_with_obstacle(q)), "self": bool(insp.in_self_collision(q)),
                "limits": bool(kin.within_cspace_limits(q, False))}
    print("start/goal status (cuMotion spheres):")
    for k, q in {**starts, **goals}.items():
        print(f"  {k:12} {status(q)}")

    cfg = cumotion.create_default_motion_planner_config(desc, PALM, view)
    PV = cumotion.MotionPlannerConfig.ParamValue
    # the graph planner checks the world, not the robot against itself
    # (measured: it planned straight to a self-colliding goal), and takes no
    # joint-limit override: each path is screened here with the inspector
    # (obstacles and self-collision, every interpolated waypoint) and the
    # slide's headroom, and the planner re-run with a new seed until one is
    # clean. Reported: the attempts and the total wall time.
    cfg.set_param("step_size", PV(float(bench.get("step_rad", 0.01))))   # see cumotion_bridge: fingertips between pieces
    SLIDE_TOP = float(bench.get("slide_top", -0.0095))
    TRIES = int(bench.get("tries", 12))

    def plan(q0, q1):
        t0 = time.perf_counter()
        tries = []
        for seed in range(TRIES):
            cfg.set_param("seed", PV(123456 + seed))
            planner = cumotion.create_motion_planner(cfg)
            res = planner.plan_to_cspace_target(q0, q1, generate_interpolated_path=True)
            if not res.path_found:
                tries.append("no path")
                continue
            P = np.asarray(res.interpolated_path if len(res.interpolated_path) else res.path)
            bad_self = sum(1 for q in P if insp.in_self_collision(q))
            bad_obs = sum(1 for q in P if insp.in_collision_with_obstacle(q))
            over = int(np.sum(P[:, 0] > max(SLIDE_TOP, float(q0[0])) + 1e-6))   # a start above the line keeps its height
            tries.append(f"self {bad_self}, obstacle {bad_obs}, slide over {over}")
            if not (bad_self or bad_obs or over):
                return P, time.perf_counter() - t0, tries
        return None, time.perf_counter() - t0, tries

    results = {"problems": [], "ik": [], "tries_max": TRIES, "slide_top": SLIDE_TOP}
    paths = {}
    for sname, q0 in starts.items():
        for gname, q1 in goals.items():
            q1 = q1.copy()
            q1[0] = min(q1[0], SLIDE_TOP)
            P, dt, tries = plan(q0, q1)
            if P is not None:
                paths[f"{sname}->{gname}"] = P.tolist()
            row = {"start": sname, "goal": gname, "found": P is not None, "wall_s": round(dt, 3), "attempts": len(tries),
                   "tries": tries, "path_length_rad": round(float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1))), 3) if P is not None else None,
                   "waypoints": 0 if P is None else len(P)}
            results["problems"].append(row)
            print(f"plan {sname:5} -> {gname:12}: {'found' if P is not None else 'NOT found'} in {dt:.3f} s, "
                  f"{len(tries)} attempt(s), length={row['path_length_rad']}, waypoints={row['waypoints']}; last try: {tries[-1]}", flush=True)

    # collision-free IK to the palm poses, world -> base frame
    ikcfg = cumotion.create_default_collision_free_ik_solver_config(desc, PALM, view)
    solver = cumotion.create_collision_free_ik_solver(ikcfg)
    R, t = boxes.R, boxes.t
    for i, g in enumerate(bench["goals"]):
        for which in ("palm_grasp", "palm_pre"):
            p_w, R_w = np.asarray(g[which]["p"], float), np.asarray(g[which]["R"], float)
            p_b, R_b = R.T @ (p_w - t), R.T @ R_w
            target = cumotion.CollisionFreeIkSolver.TaskSpaceTarget(
                cumotion.CollisionFreeIkSolver.TranslationConstraint.target(p_b),
                cumotion.CollisionFreeIkSolver.OrientationConstraint.target(cumotion.Rotation3.from_matrix(R_b)))
            times, n = [], 0
            for r in range(repeats):
                t0 = time.perf_counter()
                out = solver.solve(target, [starts["rest"], starts["reset"]])
                times.append(time.perf_counter() - t0)
                n = len(out.cspace_positions()) if out.status() == cumotion.CollisionFreeIkSolver.Results.Status.SUCCESS else 0
            err = None
            if n:
                q = out.cspace_positions()[0]
                err = round(float(np.linalg.norm(np.asarray(kin.position(q, PALM)) - p_b)), 5)
            results["ik"].append({"goal": f"g{i}_{which}", "solutions": n, "wall_s": [round(x, 3) for x in times], "position_error_m": err})
            print(f"ik   {which:10} g{i}: {n} solutions, times={results['ik'][-1]['wall_s']} s, error={err} m", flush=True)

    out = scene / "grasp_plan" / "bench_cumotion.json"
    out.write_text(json.dumps(results, indent=1))
    (scene / "grasp_plan" / "bench_cumotion_paths.json").write_text(json.dumps(paths))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
