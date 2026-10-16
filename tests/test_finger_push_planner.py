"""The finger push's measured geometry (finger_push_planner): the handle's
outer wall from the body's points, and the finger laid along the handle."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from finger_push_planner import finger_rotation, open_direction, ordered_fits, outer_wall  # noqa: E402


def _oval_ring(centre, a=0.028, b=0.0065, rim=0.004, thick=0.010, n=720):
    """A handle loop: an oval hole (half-axes a along y, b along x) with a rim, `thick` along z."""
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pts = []
    for r in (0.0, rim):
        for z in np.linspace(-thick / 2, thick / 2, 5):
            pts.append(np.c_[(b + r) * np.cos(ang), (a + r) * np.sin(ang), np.full(n, z)])
    return np.vstack(pts) + np.asarray(centre)


def test_outer_wall_is_the_rim_against_the_motion_not_a_circle():
    loop = np.array([1.0, 2.0, 0.5])
    ring = _oval_ring(loop)
    axis = np.array([0.0, 0.0, 1.0])
    t_hat = np.array([1.0, 0.0, 0.0])          # the push along +x: the wall is the rim's -x side, 6.5 + 4 mm out
    s_hat = np.cross(t_hat, axis)
    wall = outer_wall(ring, loop, axis, t_hat, s_hat, band_m=0.004, window_m=0.010)
    assert abs((loop - wall) @ t_hat - 0.0105) < 1e-6 and abs((wall - loop) @ axis) < 0.004
    t_hat = np.array([0.0, 1.0, 0.0])          # along the long axis the wall is 28 + 4 mm away
    wall = outer_wall(ring, loop, axis, t_hat, np.cross(t_hat, axis))
    assert abs((loop - wall) @ t_hat - 0.032) < 1e-6
    assert outer_wall(ring, loop + [0.0, 0.0, 0.2], axis, t_hat, np.cross(t_hat, axis)) is None


def test_finger_rotation_lays_the_pad_on_the_push_and_the_finger_along_the_handle():
    f = np.array([0.88, -0.47, 0.03]); f /= np.linalg.norm(f)
    pad = np.array([0.47, 0.88, 0.0]); pad -= f * (pad @ f); pad /= np.linalg.norm(pad)
    triad = (f, pad, np.cross(f, pad))
    t_hat = np.array([0.0, 0.0, 1.0]); s_hat = np.array([0.0, 1.0, 0.0])
    for yaw, f_world in ((0.0, s_hat), (np.pi, -s_hat), (np.pi / 2, np.cross(t_hat, s_hat)), (-np.pi / 2, -np.cross(t_hat, s_hat))):
        R = finger_rotation(triad, t_hat, s_hat, yaw)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
        assert np.allclose(R @ pad, t_hat, atol=1e-9)                # the pad faces the push
        assert np.allclose(R @ f, f_world, atol=1e-9)                # the finger turned about the push
    R = finger_rotation(triad, t_hat, s_hat, 0.0, face="back")
    assert np.allclose(R @ -pad, t_hat, atol=1e-9) and np.allclose(R @ f, s_hat, atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9   # the hook: the back faces the push


def test_ordered_fits_tries_each_face_and_direction_before_repeating_one():
    yaw = np.radians
    fits = [(0.10, yaw(90), "pad", 0, 0.0, "a"), (0.11, yaw(90), "pad", 1, 0.0, "b"), (0.12, yaw(90), "pad", 2, 0.0, "c"),
            (0.30, yaw(45), "pad", 3, 0.0, "d"), (0.50, yaw(0), "back", 4, 0.0, "e"), (0.35, yaw(45), "pad", 5, 0.0, "f")]
    got = [f[-1] for f in ordered_fits(fits)]
    assert got[:3] == ["a", "d", "e"]                 # the best of each group by score
    assert got[3:5] == ["b", "f"] and got[5] == "c"    # then the seconds, then the third


def test_finger_rotation_is_a_rotation_when_the_handle_is_not_square_to_the_push():
    f = np.array([1.0, 0.0, 0.0]); pad = np.array([0.0, 1.0, 0.0])
    triad = (f, pad, np.cross(f, pad))
    t_hat = np.array([0.0, 0.0, 1.0]); s_hat = np.array([0.0, 0.8, 0.6])     # 37 deg off square
    R = finger_rotation(triad, t_hat, s_hat, 0.3, face="pad")
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
    assert np.allclose(R @ pad, t_hat, atol=1e-9)


def test_open_direction_aims_through_the_grasp_when_it_turns_the_hinge():
    """A hinge at the origin turning about z; the holding grasp above it;
    the free half's contact out to the side. Through the grasp: no moment
    about the grasp, and a moment about the hinge in the opening sense."""
    pivot, axis = np.zeros(3), np.array([0.0, 0.0, 1.0])
    grasp = np.array([0.0, 0.08, 0.0])
    contact = np.array([0.07, 0.05, 0.0])
    tangent = -np.cross(axis, contact - pivot)                        # opening moves the free loop away from the held one: clockwise here
    tangent /= np.linalg.norm(tangent)
    d, arm, mode = open_direction(grasp, contact, pivot, axis, tangent)
    assert mode == "through_grasp"
    assert np.linalg.norm(np.cross(contact - grasp, d)) < 1e-12       # the line of action passes through the grasp
    assert np.cross(contact - pivot, d) @ axis < 0 and abs(arm - abs(np.cross(contact - pivot, d) @ axis)) < 1e-12
    # the grasp beyond the contact on the far side: through it the hinge would turn the wrong way -> the tangent
    d2, _, mode2 = open_direction(np.array([0.2, -0.1, 0.0]), contact, pivot, axis, tangent)
    assert mode2 == "tangent" and np.allclose(d2, tangent)
