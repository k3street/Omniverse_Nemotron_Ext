#!/usr/bin/env python3
"""Collision-model exceptions for the Aida R5 asset, measured and recorded.

Each entry is one pair of collision shapes that the model reports touching
where the mechanism is, in fact, free. They are applied the same way
everywhere a plan is checked - HomeHero's coal checker (scripts/rl_teacher/
aida_r5_motion.Checker, by dropping the pair), and PhysX (a filtered pair
on the shape, build_er_scene.py) - so the planner and the simulation agree.

  shoulder housing hull vs chassis: amber_left_arm_link_1/cad_part_collision_054
  is a convex hull of the housing that turns inside the chassis column
  (amber_base_link/cad_part_collision_049, the SDF mesh) with 0.8-1.7 mm of
  clearance. Swept over the shoulder pitch with coal (scratch: penetration.py,
  2026-10-14) the hull enters the mesh by at most 1.5 mm, at -45 deg, in four
  60-deg bands (30-60, 120-150 and their mirrors) - a bearing fit jammed by
  hull slop, which confined every plan so far to the band holding the rest
  pose (-30..30 deg). The housing's other 99 shapes stay checked against the
  chassis, so pitching the arm into the chassis top is still caught.
"""
from __future__ import annotations

EXCEPTIONS = [
    {"a": "/aida_revz71_r5_articulated/Links/amber_left_arm_link_1/cad_part_collision_054",
     "b": "/aida_revz71_r5_articulated/Links/amber_base_link/cad_part_collision_049",
     "max_penetration_m": 0.0015, "why": "convex-hull slop of the shoulder housing in its chassis column"},
]


def apply_to_checker(checker) -> int:
    """Drop the excepted shape pairs from a HomeHero Checker; returns how many."""
    import numpy as np
    paths = [s["path"] for s in checker.shapes]
    drop = set()
    for e in EXCEPTIONS:
        if e["a"] in paths and e["b"] in paths:
            i, j = paths.index(e["a"]), paths.index(e["b"])
            drop.add((i, j))
            drop.add((j, i))
    keep = [(i, j) for i, j in checker.pairs if (int(i), int(j)) not in drop]
    n = len(checker.pairs) - len(keep)
    checker.pairs = np.array(keep, dtype=int).reshape(-1, 2)
    return n


def physx_filtered_pairs(robot_prim_path: str = "/World/Robot", robot_root: str = "/aida_revz71_r5_articulated"):
    """The same exceptions as (shape prim, other shape prim) paths in a scene."""
    return [(robot_prim_path + e["a"][len(robot_root):], robot_prim_path + e["b"][len(robot_root):]) for e in EXCEPTIONS]
