import math

import pytest

torch = pytest.importorskip("torch")      # the grasp code is torch throughout
pytest.importorskip("numpy")

from scripts.adaptive_pick_place import choose_grasp_yaw, yaw_quaternion_wxyz  # noqa: E402
from scripts.rgbd_collision_safety import (  # noqa: E402
    grasp_axis_finger_clearance,
    pregrasp_axis_alignment_observation,
)

# Robotiq 2F-85 finger bounds as the runtime publishes them (gripper-base frame;
# closing axis is local y, finger width is local z).
FINGERS = {
    "left_inner_finger": {"min_m": [0.093, 0.0417, -0.0135], "max_m": [0.150, 0.0730, 0.0135]},
    "right_inner_finger": {"min_m": [0.093, -0.0730, -0.0135], "max_m": [0.150, -0.0417, 0.0135]},
}
ACTUATOR = {
    "contact_body_bounds_local_m": FINGERS,
    "closing_axis_robot_root": [1.0, 0.0, 0.0],
    "closing_axis_local": [0.0, -1.0, 0.0],
}


def box(runtime_id, low, high, axes=None, centre=None):
    item = {
        "runtime_id": runtime_id,
        "visible_aabb_min_base_m": low,
        "visible_aabb_max_base_m": high,
    }
    if axes is not None:
        item["oriented_footprint_axes_base"] = axes
        item["oriented_footprint_extents_m"] = [0.045, 0.045]
        item["center_base_m"] = centre
    return item


BLOCK = box(
    "red_block", [0.4775, 0.1775, 0.008], [0.5225, 0.2225, 0.05],
    axes=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], centre=[0.5, 0.2, 0.05],
)
TABLE = box("table", [0.2, -0.4, -0.02], [0.9, 0.5, 0.004])


def scene(*neighbours):
    return {"geometries": [TABLE, BLOCK, *neighbours]}


def test_a_neighbour_beside_the_block_blocks_only_the_axis_pointing_at_it():
    cube = box("rubiks_cube", [0.41, 0.17, 0.01], [0.4735, 0.23, 0.061])  # 4 mm off the -x face
    result = grasp_axis_finger_clearance(
        scene_geometry=scene(cube), actuator_geometry=ACTUATOR, object_runtime_id="red_block"
    )
    along_x, along_y = result
    assert along_x["finger_clearance_m"] < 0 and along_x["nearest_obstruction"] == "rubiks_cube"
    assert along_y["finger_clearance_m"] > 0.01  # fingers pass the cube corner diagonally


def test_the_support_surface_is_not_an_obstruction():
    result = grasp_axis_finger_clearance(
        scene_geometry=scene(), actuator_geometry=ACTUATOR, object_runtime_id="red_block"
    )
    assert all(r["finger_clearance_m"] is None for r in result)


def test_alignment_evidence_names_the_clearest_axis():
    cube = box("rubiks_cube", [0.41, 0.17, 0.01], [0.4735, 0.23, 0.061])
    observation = pregrasp_axis_alignment_observation(
        scene_geometry=scene(cube), actuator_geometry=ACTUATOR,
        object_runtime_id="red_block", maximum_error_deg=12.0,
    )
    assert observation["best_object_axis_index"] == 0  # jaws currently close along x
    assert observation["clearest_object_axis_index"] == 1
    assert observation["axis_comparisons"][0]["nearest_obstruction"] == "rubiks_cube"


def test_malformed_finger_bounds_leave_alignment_intact():
    broken = {**ACTUATOR, "contact_body_bounds_local_m": {"left_inner_finger": {"min_m": [0.0]}}}
    observation = pregrasp_axis_alignment_observation(
        scene_geometry=scene(), actuator_geometry=broken,
        object_runtime_id="red_block", maximum_error_deg=12.0,
    )
    assert observation["available"] is True
    assert observation["clearest_object_axis_index"] is None


IDENTITY = torch.tensor([1.0, 0.0, 0.0, 0.0])
CLOSING_LOCAL = torch.tensor([0.0, 1.0, 0.0])  # closes along world y at identity
AXES = [[1.0, 0.0], [0.0, 1.0]]


def test_a_blocked_axis_is_swapped_for_the_clear_one_by_a_quarter_turn():
    quaternion, report = choose_grasp_yaw(
        IDENTITY, IDENTITY, CLOSING_LOCAL, AXES, [0.02, -0.03], quarter_turn_symmetric=True
    )
    assert report["chosen_object_axis_index"] == 0
    assert report["chosen_quarter_turns"] in (1, 3)
    turned = min(c["wrist_turn_deg"] for c in report["candidates"] if c["object_axis_index"] == 0)
    assert turned == pytest.approx(90.0, abs=1e-3)


def test_with_both_axes_clear_the_smallest_turn_wins():
    current = yaw_quaternion_wxyz(math.radians(80), like=IDENTITY)
    _, report = choose_grasp_yaw(
        IDENTITY, current, CLOSING_LOCAL, AXES, [0.02, 0.021], quarter_turn_symmetric=True
    )
    assert report["chosen_quarter_turns"] == 1  # 10 degrees away, not 80


def test_an_oblong_object_only_flips_half_turns():
    _, report = choose_grasp_yaw(
        IDENTITY, IDENTITY, CLOSING_LOCAL, AXES, [0.02, -0.03], quarter_turn_symmetric=False
    )
    assert [c["quarter_turns"] for c in report["candidates"]] == [0, 2]
    assert report["chosen_object_axis_index"] == 1  # the only axis it can close along


def test_unknown_clearance_counts_as_worst():
    _, report = choose_grasp_yaw(
        IDENTITY, IDENTITY, CLOSING_LOCAL, AXES, [None, -0.01], quarter_turn_symmetric=True
    )
    assert report["chosen_object_axis_index"] == 1
