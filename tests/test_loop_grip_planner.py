"""The loop grip's fit (loop_grip_planner): the palm pose that puts each digit
its own depth into its loop, and a digit's section centred in its hole."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from loop_grip_planner import palm_pose_for, section_offset  # noqa: E402


def _cfg(thumb_lead=0.0):
    # the palm frame: digits along -z, the thumb tip 7.5 cm from the index tip along x
    d = np.array([0.0, 0.0, -1.0])
    ti = np.array([0.0, 0.0, -0.10])
    tt = np.array([0.075, 0.0, -0.10 - thumb_lead])
    return (np.zeros(6), ti, d, tt, d)


def test_each_digit_goes_its_own_depth_and_the_configuration_keeps_its_tip_lead():
    up = np.array([0.0, 0.0, 1.0])
    hole_i, hole_t = np.array([1.0, 0.0, 0.78]), np.array([1.075, 0.0, 0.78])
    # level tips, thumb wanted 1.4 cm in and the index 2.5: the index sets the depth, the thumb follows it to 2.5
    R, t, err = palm_pose_for(_cfg(0.0), hole_t, hole_i, up, 0.025, 0.79, thumb_insert_m=0.014)
    tips = np.array([_cfg()[1], _cfg()[3]]) @ R.T + t
    assert err < 1e-6 and np.allclose(tips[:, 2], [0.79 - 0.025, 0.79 - 0.025])
    # the thumb tip 1.1 cm behind the index's: each at its own depth exactly
    R, t, err = palm_pose_for(_cfg(-0.011), hole_t, hole_i, up, 0.025, 0.79, thumb_insert_m=0.014)
    c = _cfg(-0.011)
    tips = np.array([c[1], c[3]]) @ R.T + t
    assert err < 1e-6 and np.allclose(tips[:, 2], [0.79 - 0.025, 0.79 - 0.014])


def test_the_sections_not_the_tips_are_centred_when_offsets_are_given():
    up = np.array([0.0, 0.0, 1.0])
    hole_i, hole_t = np.array([1.0, 0.0, 0.78]), np.array([1.075, 0.0, 0.78])
    off_i = np.array([0.0, 0.008, 0.0])                                   # the index's section 8 mm off its tip point's line
    R, t, err = palm_pose_for(_cfg(), hole_t, hole_i, up, 0.025, 0.79, offsets=(np.array([0.0, 0.008, 0.0]), off_i))
    tip_i = _cfg()[1] @ R.T + t
    assert err < 1e-6 and np.allclose(tip_i[:2], (hole_i - off_i)[:2])
    W = np.array([[1.002, 0.009, 0.785], [0.998, 0.007, 0.775], [1.0, 0.0, 0.70]])
    off = section_offset(W, hole_i, up, (0.77, 0.79))
    assert np.allclose(off, [0.0, 0.008, 0.0])
    assert section_offset(W, hole_i, up, (0.9, 1.0)) is None


def test_a_digits_core_is_its_section_centroid_behind_the_tip():
    from loop_grip_planner import digit_core
    d = np.array([0.0, 0.0, -1.0])
    tip = np.array([0.0, 0.0, -0.10])
    # a digit whose cross-section sits 8 mm off the line through its tip point, 1-3 cm back
    P = np.array([[0.0, 0.008 + dy, -0.10 + s] for s in np.linspace(0.0, 0.04, 41) for dy in (-0.006, 0.006)])
    core = digit_core(P, tip, d, 0.017)
    assert np.allclose(core, [0.0, 0.008, -0.083], atol=1e-9)


def test_the_cores_land_on_the_hole_centres_at_the_planes_mid_height():
    from loop_grip_planner import palm_pose_for_cores
    up = np.array([0.0, 0.0, 1.0])
    d = np.array([0.0, 0.0, -1.0])
    ct, ci = np.array([0.075, 0.008, -0.09]), np.array([0.0, 0.008, -0.083])
    hole_t, hole_i = np.array([1.075, 0.0, 0.77]), np.array([1.0, 0.0, 0.78])
    R, t, err = palm_pose_for_cores(ct, ci, d, hole_t, hole_i, up, 0.778)
    got = np.array([ct, ci]) @ R.T + t
    assert err < 0.004                                                   # the cores' 7 mm height difference is split
    assert np.allclose(got[:, :2], [[1.075, 0.0], [1.0, 0.0]], atol=2e-3)   # by a slight tilt: 1 mm off each centre


def _ring_occupancy():
    from loop_grip_planner import FlatOccupancy
    # a flat ring in z [0, 0.016]: outer 6 x 4 cm, a 4 x 2.6 cm hole
    pts = [[x, y, z] for x in np.arange(-0.03, 0.0301, 0.001) for y in np.arange(-0.02, 0.0201, 0.001) for z in (0.0, 0.016)
           if not (abs(x) < 0.02 and abs(y) < 0.013)]
    pts = np.asarray(pts, float)
    frame = (pts.mean(0), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    return FlatOccupancy(pts, frame, clearance=0.0)


def test_hook_flex_finds_the_curl_that_puts_the_digit_under_the_rim():
    from loop_grip_planner import hook_flex
    occ = _ring_occupancy()
    # a digit through the hole, its tip 1.2 cm below the ring; curling swings its lower part toward +x
    def digit_at(fl):
        s = np.linspace(0.0, 0.05, 51)                                   # 5 cm of digit, tip first
        pts = np.c_[0.016 * np.sin(fl - 0.3) * (s < 0.012) * (0.012 - s) / 0.012 * 30, np.zeros_like(s), -0.012 + s]
        return pts
    hk = hook_flex(digit_at, 0.3, occ, step=0.05, max_extra=1.2, min_under=3)
    assert hk is not None and hk[0] > 0 and hk[1] >= 3
    # a digit that only presses the wall on the way never hooks
    def wall_at(fl):
        s = np.linspace(0.0, 0.05, 51)
        return np.c_[np.full_like(s, 0.012 + 0.02 * (fl - 0.3)), np.zeros_like(s), -0.012 + s]
    assert hook_flex(wall_at, 0.3, occ, step=0.05, max_extra=1.2, min_under=3) is None
