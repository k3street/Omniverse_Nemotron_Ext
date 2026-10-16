"""The planner geometry for either arm (r5_arm_side): the right arm's names mirror the left's."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import r5_arm_side  # noqa: E402  (puts HomeHero's script dirs on the path)
pytest.importorskip("homehero_core.aida_can_baseline")
from r5_arm_side import side_names  # noqa: E402


def test_right_names_mirror_left_names():
    arm_l, hand_l, palm_l = side_names("left")
    arm_r, hand_r, palm_r = side_names("right")
    assert len(arm_l) == len(arm_r) == 8 and len(hand_l) == len(hand_r)
    assert all("left" in n for n in arm_l) and all("right" in n for n in arm_r)
    assert [n.replace("left", "right") for n in arm_l] == list(arm_r)
    assert palm_l == "psyonic_left_base" and palm_r == "psyonic_right_base"


def test_set_active_checks_the_right_arm_against_the_torso():
    """The push planner's checker: the right arm's own self-collision pairs
    (HomeHero's Checker builds them for the left arm only), and the arm forward
    and rolled across the body meets the torso's casing, as PhysX found."""
    import json

    import numpy as np

    pytest.importorskip("coal")
    from aida_r5_motion import Checker
    from pinch_place_planner import CACHE
    from r5_arm_side import LEFT_LINKS, RIGHT_LINKS, both_arms_geometry, set_active, side_names

    urdf = json.loads(CACHE.with_suffix(".json").read_text())["physics"]["urdf"]
    g = both_arms_geometry(urdf, [0.0, 0.0, 0.403, 0.0, 0.0, 0.0, 1.0])
    checker = Checker(g, CACHE, [])
    n_left = len(checker.pairs)
    assert set_active(checker, LEFT_LINKS) == n_left                      # the Checker's own rule, reproduced
    n_right = set_active(checker, RIGHT_LINKS)
    paths = [x["path"] for x in checker.shapes]
    pairs = {(int(a), int(b)) for a, b in checker.pairs} | {(int(b), int(a)) for a, b in checker.pairs}
    i2 = [i for i, p in enumerate(paths) if p.endswith("amber_right_arm_link_2/commissioning_collision")][0]
    i49 = [i for i, p in enumerate(paths) if p.endswith("amber_base_link/cad_part_collision_049")][0]
    i1 = [i for i, p in enumerate(paths) if "/amber_right_arm_link_1/" in p][0]
    assert (i2, i49) in pairs and (i1, i2) not in pairs and n_right > 1000
    assert all(checker.shapes[i]["link"].startswith(RIGHT_LINKS) for i in checker.active)
    arm, _, _ = side_names("right")
    q = np.asarray(g.rest, float).copy()
    assert checker(q), checker(q, True)[:4]                               # the rest posture is clear
    q[14 + arm.index("amber_right_shoulder_pitch_joint")] = -1.0
    q[14 + arm.index("amber_right_shoulder_roll_joint")] = 0.35
    hits = checker(q, True)
    assert any("cad_part_collision_049" in a + b for a, b in hits), hits[:4]
