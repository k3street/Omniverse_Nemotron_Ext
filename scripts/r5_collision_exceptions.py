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
  Confirmed on the real robot (commissioning, every joint driven to its
  limits, the shoulders through 45 deg): the pitch is free. The right arm
  carries the same housing hull, so its pair is excepted too. The fix that
  belongs in the asset: a collision shape for part 054 that follows the
  housing (a convex decomposition), in place of its hull.
"""
from __future__ import annotations

# A LINK-level exception: every collision shape of the link against the other
# shape. The shoulder housings turn inside the chassis column, an SDF mesh
# (cad_part_collision_049, sdfResolution 512): at a shoulder roll of 0..0.35
# rad the right housing's cad parts 052, 057, 066, 072, 085, 086 and 099 all
# report contact in PhysX and the roll stalls at 0.00 rad with 200 Nm in
# hand, while coal with the exact triangles finds the whole sweep -0.3..0.45
# rad free (2026-10-15): the SDF's skin in a 1-2 mm clearance. The real
# shoulders roll through it (commissioning). The right fix belongs in the
# asset: a finer SDF or a decomposition of the column where the housings turn.
LINK_EXCEPTIONS = [
    {"a_link": "/aida_revz71_r5_articulated/Links/amber_right_arm_link_1",
     "b": "/aida_revz71_r5_articulated/Links/amber_base_link/cad_part_collision_049",
     "why": "the right shoulder housing's parts in the chassis column's SDF skin at shoulder roll 0..0.35 rad",
     "evidence": "probe 2026-10-15: 7 housing parts in contact with 049, roll stalled at 0.00; coal free across the sweep; commissioning"},
    {"a_link": "/aida_revz71_r5_articulated/Links/amber_left_arm_link_1",
     "b": "/aida_revz71_r5_articulated/Links/amber_base_link/cad_part_collision_049",
     "why": "the left housing, the mirror of the right", "evidence": "same column, same SDF; the left pitch bands were the first symptom"},
]

EXCEPTIONS = [
    {"a": "/aida_revz71_r5_articulated/Links/amber_left_arm_link_1/cad_part_collision_054",
     "b": "/aida_revz71_r5_articulated/Links/amber_base_link/cad_part_collision_049",
     "max_penetration_m": 0.0015, "why": "convex-hull slop of the shoulder housing in its chassis column",
     "evidence": "coal sweep 2026-10-14; the real shoulder pitches through 45 deg (commissioning to the joint limits)"},
    {"a": "/aida_revz71_r5_articulated/Links/amber_right_arm_link_1/cad_part_collision_054",
     "b": "/aida_revz71_r5_articulated/Links/amber_base_link/cad_part_collision_049",
     "max_penetration_m": 0.0015, "why": "the right arm's housing hull, the mirror of the left",
     "evidence": "same commissioning; the right arm is not planned here, but PhysX moves it"},
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
    for e in LINK_EXCEPTIONS:
        if e["b"] in paths:
            j = paths.index(e["b"])
            for i, pth in enumerate(paths):
                if pth.startswith(e["a_link"] + "/"):
                    drop.add((i, j))
                    drop.add((j, i))
    keep = [(i, j) for i, j in checker.pairs if (int(i), int(j)) not in drop]
    n = len(checker.pairs) - len(keep)
    checker.pairs = np.array(keep, dtype=int).reshape(-1, 2)
    return n


def physx_filtered_pairs(robot_prim_path: str = "/World/Robot", robot_root: str = "/aida_revz71_r5_articulated"):
    """The same exceptions as (prim, other shape prim) paths in a scene: a
    shape pair, or a LINK (its rigid body prim: PhysicsFilteredPairsAPI there
    filters every shape of the link) against a shape."""
    pairs = [(robot_prim_path + e["a"][len(robot_root):], robot_prim_path + e["b"][len(robot_root):]) for e in EXCEPTIONS]
    pairs += [(robot_prim_path + e["a_link"][len(robot_root):], robot_prim_path + e["b"][len(robot_root):]) for e in LINK_EXCEPTIONS]
    return pairs


def apply_to_stage(stage, robot_prim_path: str = "/World/Robot", robot_root: str = "/aida_revz71_r5_articulated"):
    """Filter the excepted pairs in a scene (PhysicsFilteredPairsAPI on the
    first shape, the second as its filtered pair); returns the pairs applied.
    A pair whose shapes the scene lacks (another robot, a trimmed asset) is
    skipped, not invented."""
    from pxr import Sdf
    applied = []
    for a, b in physx_filtered_pairs(robot_prim_path, robot_root):
        if not (stage.GetPrimAtPath(a) and stage.GetPrimAtPath(b)):
            continue
        pa = stage.OverridePrim(a)
        pa.AddAppliedSchema("PhysicsFilteredPairsAPI")
        rel = pa.GetRelationship("physics:filteredPairs") or pa.CreateRelationship("physics:filteredPairs")
        rel.AddTarget(Sdf.Path(b))
        applied.append([a, b])
    return applied


def missing_in_scene(stage, robot_prim_path: str = "/World/Robot", robot_root: str = "/aida_revz71_r5_articulated"):
    """The excepted pairs whose shapes the scene has but does not filter
    (either direction). A plan made with the exceptions dropped runs into
    exactly these in PhysX: the towel reach of 2026-10-15 jammed the shoulder
    at -31 deg, the edge of the first housing band, in a scene built without
    them while the planner had dropped them."""
    from pxr import Sdf

    def filtered(x, y):
        px = stage.GetPrimAtPath(x)
        if not px.HasAPI("PhysicsFilteredPairsAPI") and "PhysicsFilteredPairsAPI" not in px.GetAppliedSchemas():
            return False
        rel = px.GetRelationship("physics:filteredPairs")
        return bool(rel) and Sdf.Path(y) in rel.GetTargets()

    missing = []
    for a, b in physx_filtered_pairs(robot_prim_path, robot_root):
        if stage.GetPrimAtPath(a) and stage.GetPrimAtPath(b) and not (filtered(a, b) or filtered(b, a)):
            missing.append([a, b])
    return missing
