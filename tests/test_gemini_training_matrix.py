import copy
from pathlib import Path

import pytest

from scripts.run_gemini_training_matrix import (
    DEFAULT_SPEC,
    REPO_ROOT,
    SpecError,
    build_plan,
    load_spec,
    validate_spec,
)


def _example_spec():
    return load_spec(DEFAULT_SPEC)


def test_example_matrix_preserves_task_embodiment_separation():
    spec = _example_spec()
    indexes = validate_spec(spec)

    forbidden = {
        "arm",
        "arm_count",
        "controller",
        "embodiment",
        "embodiment_id",
        "end_effector",
        "gripper",
        "joint_names",
        "robot",
        "runtime_adapter_id",
    }
    assert indexes["tasks"]
    assert all(not (task.keys() & forbidden) for task in indexes["tasks"].values())
    assert indexes["embodiments"]["franka_robotiq_2f85"]["arm_count"] == 1
    assert indexes["embodiments"]["amber_revan_dual_psyonic"]["arm_count"] == 2


def test_task_contract_rejects_embodiment_fields():
    spec = _example_spec()
    spec["tasks"][0]["embodiment_id"] = "franka_robotiq_2f85"

    with pytest.raises(SpecError):
        validate_spec(spec)


def test_example_matrix_has_two_registered_runnable_cells_and_explicit_blockers(
    tmp_path,
):
    plan = build_plan(
        _example_spec(),
        output_root=tmp_path / "collection",
        repo_root=REPO_ROOT,
        python_executable="python-test",
    )
    cells = {cell["campaign_id"]: cell for cell in plan["cells"]}

    assert plan["summary"] == {"total": 8, "runnable": 2, "blocked": 6}
    runnable = cells["franka_cube_inside_bowl"]
    assert runnable["status"] == "runnable"
    command = runnable["command"]
    assert command[0] == "python-test"
    assert command[command.index("--task") + 1] == "RubiksCubeTask"
    assert command[command.index("--movable-object-asset") + 1] == "rubiks_cube"
    assert command[command.index("--target-receptacle-asset") + 1] == "bowl"
    assert command[command.index("--object-yaw-range-deg") + 1] == "180.0"
    assert "--visible-first" in command

    left_of = cells["franka_cube_left_of_bowl"]
    assert left_of["status"] == "runnable"
    assert left_of["command"][left_of["command"].index("--task") + 1] == (
        "RubiksCubeLeftOfBowlTask"
    )
    assert "--visible-first" in left_of["command"]

    spatial_codes = {
        blocker["code"]
        for blocker in cells["franka_cube_right_of_bowl"]["blockers"]
    }
    assert spatial_codes == {
        "world_capability_missing",
        "completion_evaluator_missing",
    }
    for campaign_id in (
        "vacuum_cube_inside_bowl",
        "dual_franka_cube_inside_bowl",
        "amber_revan_cube_inside_bowl",
    ):
        assert cells[campaign_id]["status"] == "blocked"
        assert "runtime_adapter_missing" in {
            blocker["code"] for blocker in cells[campaign_id]["blockers"]
        }
        assert "command" not in cells[campaign_id]


def test_matrix_selection_does_not_launch_or_include_other_cells(tmp_path):
    plan = build_plan(
        _example_spec(),
        output_root=tmp_path,
        selected_campaign_ids={"franka_cube_inside_bowl"},
    )

    assert plan["summary"] == {"total": 1, "runnable": 1, "blocked": 0}
    assert [cell["campaign_id"] for cell in plan["cells"]] == [
        "franka_cube_inside_bowl"
    ]


def test_scene_must_bind_each_task_semantic_role():
    spec = copy.deepcopy(_example_spec())
    del spec["scenes"][0]["task_bindings"]["cube_inside_bowl"]["roles"][
        "destination"
    ]

    with pytest.raises(SpecError, match="does not bind role destination"):
        validate_spec(spec)


def test_unknown_campaign_selection_fails_closed(tmp_path):
    with pytest.raises(SpecError, match="unknown selected campaigns"):
        build_plan(
            _example_spec(),
            output_root=Path(tmp_path),
            selected_campaign_ids={"not_a_campaign"},
        )
