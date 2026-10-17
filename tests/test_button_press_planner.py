"""The press's geometry (button_press_planner): the button's face and press
direction, and the hand turned so the index points along it."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from button_press_planner import finger_rotation, press_frame, press_depth  # noqa: E402


def test_the_face_is_the_buttons_outer_side_and_the_press_runs_inward():
    # a 7.5 x 0.2 x 3 cm bar, its outer face at y = 0.47, travelling along -y (into a panel behind it)
    B = np.array([[x, y, z] for x in np.linspace(-0.27, -0.195, 8) for y in (0.468, 0.47) for z in np.linspace(0.913, 0.942, 4)])
    face, u = press_frame(B, [0.0, 1.0, 0.0], -1.0)
    assert np.allclose(u, [0, -1, 0]) and abs(face[1] - 0.47) < 1e-9
    assert abs(face[0] + 0.2325) < 1e-6 and abs(face[2] - 0.9275) < 1e-6


def test_the_hand_turns_the_finger_onto_the_press_line_whatever_the_roll():
    d = np.array([0.1, 0.2, 0.97])                       # the finger's line in the palm frame
    u = np.array([0.0, -1.0, 0.0])
    for roll in np.radians([0, 90, 200]):
        R = finger_rotation(d, u, roll)
        assert np.allclose(R @ (d / np.linalg.norm(d)), u, atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
    Rt = finger_rotation(d, u, 0.0, (np.radians(15), 0.0))
    assert abs(np.degrees(np.arccos(np.clip((Rt @ (d / np.linalg.norm(d))) @ u, -1, 1))) - 15) < 1e-6


def test_a_release_button_is_pressed_to_its_trip_not_through_its_bezel():
    assert press_depth(0.010, {}) == 0.012                              # a plain button: its travel and 2 mm
    assert press_depth(0.010, {"press_m": 0.008}) == 0.008              # the microwave's: trip 6 mm + 2, short of the bezel at 8.76
