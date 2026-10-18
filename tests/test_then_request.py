"""A stage after the first plans from where the stage before left the arm,
with its IK seeded by the first stage's seeds and the authored arm too
(plan_er_grasp.then_request; the drill's switch, 2026-10-17)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from plan_er_grasp import then_request  # noqa: E402

AUTHORED = [-0.015, 0.003, -0.314, -0.003, 0.009, 0.745, 0.238, 0.207, 0.15, 1.6, 1.6, 1.6, 0.6, -1.2]
RESET = [-0.005, -0.111, -1.054, 0.615, -0.545, 0.242, -0.653, 0.753]
AFTER_PRESS = [-0.101, -1.563, -1.381, -0.548, -1.03, 0.016, 1.355, -0.932, 0.15, 1.6, 1.6, 1.6, 0.6, -1.2]


def _base():
    return {"measured": AUTHORED, "task": {"kind": "button_press", "ik_seeds": [RESET]}, "ik_seeds": [[0.0] * 8],
            "joint_positions": {"amber_left_shoulder_lift_joint": -0.015}}


def test_the_start_is_where_the_stage_before_left_the_arm():
    req = then_request(_base(), AFTER_PRESS, {"kind": "tool_grip"}, "/x/then.npz")
    assert req["measured"] == AFTER_PRESS
    assert req["task"]["kind"] == "tool_grip"
    assert req["out_npz"] == "/x/then.npz"
    assert "ik_seeds" not in req  # the first stage's own top-level seeds do not carry over


def test_the_seeds_hold_the_first_stage_seeds_and_the_authored_arm():
    req = then_request(_base(), AFTER_PRESS, {"kind": "tool_grip"}, "/x/then.npz")
    assert RESET in req["task"]["ik_seeds"]
    assert AUTHORED in req["task"]["ik_seeds"]


def test_a_stage_own_seeds_come_first_and_the_inputs_are_not_changed():
    base, task = _base(), {"kind": "tool_grip", "ik_seeds": [[1.0] * 8]}
    req = then_request(base, AFTER_PRESS, task, "/x/then.npz")
    assert req["task"]["ik_seeds"][0] == [1.0] * 8
    assert task["ik_seeds"] == [[1.0] * 8]
    assert base["measured"] == AUTHORED and base["task"]["ik_seeds"] == [RESET]


def test_a_cylinder_with_vertices_only_at_its_ends_has_its_width_halfway_up():
    import math
    from plan_er_grasp import slice_width
    ring = [[0.03 * math.cos(2 * math.pi * k / 24), 0.03 * math.sin(2 * math.pi * k / 24)] for k in range(24)]
    pts = [[x, y, 0.0] for x, y in ring] + [[x, y, 0.09] for x, y in ring]
    w = slice_width(pts, [0.0, 0.0], 0.054)
    assert abs(w - 0.06) < 0.001
    # a cone narrows up its height
    cone = [[x, y, 0.0] for x, y in ring] + [[0.0, 0.0, 0.1]]
    assert abs(slice_width(cone, [0.0, 0.0], 0.05) - 0.03) < 0.001


def test_the_tweezers_grip_is_on_the_midline_across_the_arms_outer_faces():
    import math
    from make_pill_kit import tweezer_spring, TWEEZERS
    from plan_er_grasp import tool_grip_frame
    meta = {"spring": tweezer_spring()}
    p, ax, w = tool_grip_frame(meta, 0.055)
    th = math.radians(meta["spring"]["rest_angle_deg"])
    assert abs(p[0] - (0.015 + 0.055 * math.cos(th))) < 1e-12 and p[1] == 0 and p[2] == 0
    assert list(ax) == [0.0, 0.0, 1.0]
    # at the tips' reach (the full flexing length) the outer width is the tips' gap plus both arms' thickness
    _, _, w_tip = tool_grip_frame(meta, TWEEZERS["length"] - TWEEZERS["root"])
    assert abs(w_tip - (TWEEZERS["tip_gap"] + 2 * TWEEZERS["thick"])) < 3e-4
    assert w < w_tip


def test_a_tools_tip_stage_starts_from_the_digits_where_they_stopped_on_the_tool():
    from plan_er_grasp import hold_with_live_hand
    row = AFTER_PRESS[:8] + [1.74, 1.74, 1.5, 1.5, 1.74, -1.368]
    live = hold_with_live_hand(row, [0.833, 0.935, 1.5, 1.5, 0.834, -1.37])
    assert live[:8] == AFTER_PRESS[:8]
    assert live[8:] == [0.833, 0.935, 1.5, 1.5, 0.834, -1.37]
    assert row[8] == 1.74                                              # the plan's row is not changed
