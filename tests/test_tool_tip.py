"""A held tool's tips as the pinch (pinch_place_planner.tool_tip_pinch) and the
tool's own clearance (tool_clear): the tips' midpoint and axis carried into the
palm's frame at the measured hand, the hold as 'open' and that hold squeezed
as 'closed', the tool's points against the obstacle boxes but the piece's."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pinch_place_planner import tool_clear, tool_tip_pinch  # noqa: E402


class _G:
    """A palm at p, turned 90 deg about z, wherever the arm is."""
    def __init__(self, p):
        self.p = np.asarray(p, float)
        self.R = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])

    def forward(self, q):
        self.q = q

    def pose(self, link=None):
        return self.p, self.R


def test_the_tips_become_the_pinch_in_the_palm_frame_and_the_hold_is_open():
    g = _G([1.0, 2.0, 1.0])
    start = np.r_[np.zeros(8), [0.9, 1.0, 1.5, 1.5, 0.85, -1.3]]
    tp = {"tips_world": [[1.0, 2.1, 0.9], [1.0, 2.116, 0.9]], "squeeze_rad": 0.08, "squeeze_digits": [0, 1, 4],
          "points_world": [[1.0, 2.0, 0.95], [1.0, 2.1, 0.9]]}
    pin = tool_tip_pinch(g, start, tp)
    assert np.allclose(pin["midpoint_palm"], g.R.T @ (np.array([1.0, 2.108, 0.9]) - g.p))
    assert np.allclose(pin["axis_palm"], g.R.T @ np.array([0.0, 1.0, 0.0]))
    assert abs(pin["tip_gap_m"] - 0.016) < 1e-9
    assert np.allclose(pin["open"], start[8:]) and np.allclose(pin["closed"] - pin["open"], [0.08, 0.08, 0, 0, 0.08, 0])
    assert pin["points_palm"].shape == (2, 3)


def test_the_tool_clears_the_scene_but_the_piece_it_takes():
    g = _G([0.0, 0.0, 0.0])
    pin = {"points_palm": np.array([[0.0, 0.0, -0.1], [0.0, 0.0, -0.2]])}
    obst = [{"id": "payload:Tablet1:Tablet", "min": [-0.01, -0.01, -0.21], "max": [0.01, 0.01, -0.19]},
            {"id": "obj:Vial:Body", "min": [-0.01, -0.01, -0.11], "max": [0.01, 0.01, -0.09]},
            {"id": "ReceivingBench:Top", "min": [1, 1, 1], "max": [2, 2, 2]}]
    assert tool_clear(g, np.zeros(14), pin, obst, spare=["payload:"]) == ["obj:Vial:Body"]
    assert tool_clear(g, np.zeros(14), {"points_palm": None}, obst) == []


def test_a_cap_is_turned_in_whole_strokes_and_a_remainder():
    from pinch_place_planner import cap_strokes
    assert cap_strokes(350.0, 80.0) == [80.0, 80.0, 80.0, 80.0, 30.0]
    assert cap_strokes(-160.0, 80.0) == [-80.0, -80.0]
    assert cap_strokes(50.0, -80.0) == [50.0]


def test_the_tips_pinch_on_the_inner_faces_a_millimetre_from_the_ends():
    import math
    from tool_tip_then import arm_tips
    up = {"p": [0.0, 0.0, 0.0007], "q_wxyz": [1.0, 0.0, 0.0, 0.0]}
    lo = {"p": [0.0, 0.0, -0.0007], "q_wxyz": [1.0, 0.0, 0.0, 0.0]}
    a, b = arm_tips([up, lo], 0.1, 0.001, 0.001)
    assert abs(a[0] - 0.099) < 1e-12 and abs(a[2] - 0.0002) < 1e-12 and abs(b[2] + 0.0002) < 1e-12
    # an arm turned 90 deg about z points its tip along +y
    s = math.sqrt(0.5)
    a, _ = arm_tips([{"p": [0.0, 0.0, 0.0], "q_wxyz": [s, 0.0, 0.0, s]}, lo], 0.1, 0.001, 0.001)
    assert abs(a[1] - 0.099) < 1e-9 and abs(a[0]) < 1e-9
