"""The door's geometry (door_open_planner): the swing about the hinge, and the
handle bar's frame - its axis, the door's outward normal, the opening sense,
the gap behind the bar."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from door_open_planner import body_boxes, body_hulls, body_name, cabinet_of, door_frame, grip_rotation, push_approaches, push_needed, rot_about, swing  # noqa: E402


def _fridge_door():
    """A 0.6 m door in the x-z plane, front at y = 0 facing -y, hinged on x = 0;
    a bar handle 2.6 cm deep standing 2.3 cm off it on posts at x 0.55."""
    door = np.array([[x, y, z] for x in np.linspace(0.0, 0.6, 31) for y in (0.0, 0.05) for z in np.linspace(0.75, 1.7, 20)])
    bar = np.array([[x, y, z] for x in (0.545, 0.571) for y in np.linspace(-0.049, -0.023, 4) for z in np.linspace(0.8, 1.05, 26)])
    posts = np.array([[x, y, z] for x in (0.545, 0.571) for y in np.linspace(-0.023, 0.0, 4) for z in (0.79, 0.8, 1.05, 1.06)])
    return door, np.vstack([bar, posts]), np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.025, 0.0])


def test_rot_about_and_swing_turn_points_round_the_hinge():
    R = rot_about([0, 0, 1], np.pi / 2)
    assert np.allclose(R @ [1, 0, 0], [0, 1, 0])
    assert np.allclose(swing([1.0, 0.0, 0.5], [0.0, 0.0, 0.0], [0, 0, 1], np.pi / 2), [[0.0, 1.0, 0.5]])


def test_door_frame_finds_the_bar_the_outward_normal_the_sense_and_the_gap():
    door, handle, axis, hinge = _fridge_door()
    fr = door_frame(handle, door, hinge, axis)
    assert np.allclose(fr["a"], [0, 0, 1], atol=1e-6)                   # the bar runs up
    assert np.allclose(fr["n"], [0, -1, 0], atol=1e-6)                  # out of the door, toward the room
    # opening carries the handle along +n (out of the door's plane): rotating by sense*angle about the axis
    h0 = handle.mean(0)
    h1 = swing(h0, hinge, axis, fr["sense"] * np.radians(5))[0]
    assert (h1 - h0) @ fr["n"] > 0
    assert abs(fr["gap"] - 0.023) < 0.002 and abs((fr["front"] - fr["back"]) - 0.026) < 0.002
    assert abs(fr["radius"] - 0.558) < 0.01


def test_the_grip_puts_the_palm_onto_the_door_and_the_fingers_across_the_bar():
    R = grip_rotation(np.array([0, 0, 1.0]), np.array([0, -1.0, 0]), 1.0, 0.0)
    assert np.allclose(R[:, 1], [0, 1, 0]) and np.allclose(R[:, 0], [0, 0, 1])
    assert abs(np.linalg.det(R) - 1.0) < 1e-9 and abs(R[:, 2] @ [0, 0, 1]) < 1e-9


def test_a_doors_own_edge_as_the_handle_faces_away_from_the_cabinet():
    door, _, axis, hinge = _fridge_door()
    edge = door[door[:, 0] > 0.57]                                       # the free edge's strip, in the door's own plane
    cabinet_centre = np.array([0.3, 0.35, 1.2])                          # the cabinet behind the door (+y)
    fr = door_frame(edge, door, hinge, axis, cabinet_centre)
    assert np.allclose(fr["n"], [0, -1, 0], atol=1e-6)
    h0 = edge.mean(0)
    assert (swing(h0, hinge, axis, fr["sense"] * np.radians(5))[0] - h0) @ fr["n"] > 0


def test_a_cabinet_is_slabs_so_its_proud_panel_does_not_fill_the_door_recess():
    # a microwave's cabinet: its front at y 0.416 behind the door (x 0.83-1.29), its
    # control panel proud at y 0.458 (x 0.71-0.83); behind a popped door is free space
    rng = np.random.default_rng(0)
    body = np.c_[rng.uniform(0.83, 1.29, 4000), rng.uniform(0.03, 0.416, 4000), rng.uniform(0.9, 1.23, 4000)]
    panel = np.c_[rng.uniform(0.71, 0.83, 2000), rng.uniform(0.03, 0.458, 2000), rng.uniform(0.9, 1.23, 2000)]
    boxes = body_boxes(np.vstack([body, panel]), "E_body_12")
    behind_door = np.array([1.0, 0.44, 1.05])                            # 2.4 cm in front of the recess
    in_any = any(np.all(behind_door >= b["min"]) and np.all(behind_door <= b["max"]) for b in boxes)
    assert not in_any
    in_panel = np.array([0.75, 0.45, 1.05])
    assert any(np.all(in_panel >= b["min"]) and np.all(in_panel <= b["max"]) for b in boxes)
    assert all(b["id"].startswith("door:E_body_12:") for b in boxes)


def test_a_robot_collider_is_named_by_its_link():
    assert body_name("/aida_revz71_r5_articulated/Links/amber_left_arm_link_4/commissioning_collision") == "amber_left_arm_link_4"
    assert body_name("door:E_body_12:0") == "door:E_body_12:0"
    assert body_name("/World/Env/SM_kitchen_02_0") == "SM_kitchen_02_0"


def test_staging_from_above_stands_above_the_push():
    inward = np.array([0.97, -0.23, 0.0])
    o = np.array([0.85, 0.5, 1.05])
    ways = {name: (d, b) for name, d, b in push_approaches(inward, 0.15, 0.3)}
    d, b = ways["from above"]
    assert (o - d * b)[2] > o[2] + 0.29                                    # staged above, travelling down into the gap
    d, b = ways["beyond the edge"]
    assert (o - d * b) @ inward < o @ inward - 0.14                         # staged beyond the free edge
    d, b = ways["above and beyond"]
    assert (o - d * b)[2] > o[2] and (o - d * b) @ inward < o @ inward


def test_a_cabinets_hulls_leave_the_door_recess_free():
    from tool_grip_planner import inside
    rng = np.random.default_rng(1)
    body = np.c_[rng.uniform(0.83, 1.29, 6000), rng.uniform(0.03, 0.416, 6000), rng.uniform(0.9, 1.23, 6000)]
    panel = np.c_[rng.uniform(0.71, 0.83, 3000), rng.uniform(0.03, 0.458, 3000), rng.uniform(0.9, 1.23, 3000)]
    hulls = body_hulls(np.vstack([body, panel]))
    assert not inside(np.array([[0.86, 0.44, 1.05], [1.0, 0.43, 1.05]]), hulls).any()   # in front of the recess, beside the panel
    assert inside(np.array([[0.75, 0.40, 1.05]]), hulls).all()                          # inside the panel


def test_the_cabinet_is_the_biggest_body_that_is_not_the_door():
    others = {"E_door_1": np.zeros((9000, 3)), "E_body_12": np.ones((6000, 3)), "E_switch_11": np.ones((700, 3))}
    assert cabinet_of(others, "E_door_1").shape == (6000, 3)
    assert cabinet_of({k: v for k, v in others.items() if k != "E_door_1"}).shape == (6000, 3)


def test_door_angles_are_the_doors_not_the_pushs():
    assert push_needed(85.0, 45.0, 0.0) == (85.0, 45.0)                      # a shut door: the whole swing
    assert push_needed(85.0, 45.0, 19.8) == (85.0 - 19.8, 45.0 - 19.8)       # a microwave's after its pop
    assert push_needed(85.0, 45.0, 50.0)[1] == 10.0                          # already past its least: still a push
