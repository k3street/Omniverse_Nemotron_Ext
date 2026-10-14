#!/usr/bin/env python3
"""Screen the paths another planner found with HomeHero's own checker (coal,
the planning collision meshes, the scene's obstacle boxes): an independent
second opinion on a path planned against cuMotion's sphere model. Runs in
HomeHero's venv.

    <HomeHero>/.venv/bin/python scripts/screen_bench_paths.py workspace/scenes/aida_r5_dining_chess
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HH = Path("/home/kimate/Documents/Github/HomeHero")
sys.path.insert(0, str(HH / "src/homehero_core"))
sys.path.insert(0, str(HH / "scripts/rl_teacher"))


def main() -> int:
    from scipy.spatial.transform import Rotation
    from aida_r5_motion import Checker, measured_configuration, screen
    from prepare_aida_r5_transfer import Geometry

    scene = Path(sys.argv[1]).resolve()
    gp = scene / "grasp_plan"
    bench = json.loads((gp / "bench_goals.json").read_text())
    paths = json.loads((gp / "bench_cumotion_paths.json").read_text())
    req = json.loads((gp / "request.json").read_text())
    robot = json.loads((HH / "artifacts/isaac/aida_r5_20261004/reference_learning_v1/robot.json").read_text())
    cfg = json.loads(Path(req["config"]).read_text())
    g = Geometry(robot["physics"]["urdf"], bench["base_pose"])
    measured_configuration(g, bench["joint_positions"])
    checker = Checker(g, Path(cfg["planning"]["collision_cache"]), bench["obstacles"],
                      clearance=cfg["planning"]["table_margin_m"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from r5_collision_exceptions import apply_to_checker
    print("collision exceptions applied:", apply_to_checker(checker))
    hand = np.asarray(bench["hand_open"], float)
    out = {}
    for name, P in paths.items():
        P = np.asarray(P, float)
        # densified to 0.02 rad steps, as HomeHero's own edge checks are
        dense = [P[0]]
        for a, b in zip(P[:-1], P[1:]):
            n = max(1, int(np.ceil(np.max(np.abs(b - a)) / 0.02)))
            dense += [a * (1 - f) + b * f for f in np.linspace(0, 1, n + 1)[1:]]
        res = screen(checker, [np.r_[q, hand] for q in dense])
        out[name] = {"waypoints": len(P), "checked": len(dense), "passed": res["passed"],
                     "first_collision": (res["collisions"][0]["pairs"][:2] if res["collisions"] else None)}
        print(f"{name:22} {len(P):4} waypoints, {len(dense):5} checked: {'CLEAR' if res['passed'] else 'COLLIDES ' + str(out[name]['first_collision'])}")
    (gp / "bench_cumotion_screen.json").write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
