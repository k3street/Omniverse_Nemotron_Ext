"""The film camera's line of sight (film_camera.sight_clear): a segment from
the eye to a work point is clear when no obstacle point lies within the
radius of it, the target's own last 3 cm excepted."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from film_camera import sight_clear  # noqa: E402


def test_an_arm_between_the_eye_and_the_work_blocks_the_view():
    eye = np.zeros(3)
    targets = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    arm = np.array([[0.5, 0.005, 0.0], [0.5, -0.004, 0.01]])        # across the first sight line, nowhere near the second
    assert sight_clear(eye, targets, arm).tolist() == [False, True]


def test_the_work_points_own_surroundings_do_not_block_it():
    eye = np.zeros(3)
    finger_on_the_loop = np.array([[0.98, 0.0, 0.0]])               # 2 cm short of the target, on the line
    assert sight_clear(eye, np.array([[1.0, 0.0, 0.0]]), finger_on_the_loop).tolist() == [True]


def test_a_solid_box_blocks_the_sight_lines_through_it():
    from film_camera import box_surface_points, sight_clear
    P = box_surface_points([-0.3, 0.3, -0.3, 0.3, 0.0, 1.0], 0.05)
    assert P[:, 0].min() == -0.3 and P[:, 2].max() == 1.0
    eye, target = np.array([-2.0, 0.0, 0.5]), np.array([2.0, 0.0, 0.5])
    assert not sight_clear(eye, target, P)[0]                             # straight through the box
    assert sight_clear(eye, target + [0, 0, 1.0], P + [0, 5.0, 0])[0]     # the box elsewhere
