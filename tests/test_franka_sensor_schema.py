from __future__ import annotations

from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from scripts.franka_sensor_schema import (
    SENSOR_DIM,
    SIGNAL_SLICES,
    SIGNAL_SPECS,
    VALIDITY_DIM,
    SensorCaptureBuffer,
    load_sensor_block,
    masked_sensor_stats,
    sensor_frame_from_isaac_env,
    sensor_frame_from_robot_state,
    summarize_contact_telemetry,
    write_sensor_group,
)
from scripts.robolab_contact_telemetry import (
    GRIPPER_CONTACT_PRIM_PATH,
    GRIPPER_CONTACT_SENSOR_NAME,
    install_sim6_gripper_contact_sensor,
)
from scripts.patch_droid_external_torque import patch_checkout


pytestmark = pytest.mark.l0


def test_polymetis_state_captures_external_torque_without_fabricating_touch():
    state = SimpleNamespace(
        motor_torques_measured=np.arange(7, dtype=np.float32),
        joint_torques_computed=np.arange(7, dtype=np.float32) + 10,
        motor_torques_external=np.arange(7, dtype=np.float32) + 20,
    )
    frame = sensor_frame_from_robot_state(state)
    np.testing.assert_array_equal(
        frame.values[SIGNAL_SLICES["joint_torque_external"]],
        np.arange(7, dtype=np.float32) + 20,
    )
    np.testing.assert_array_equal(frame.validity[:3], np.ones(3, dtype=np.float32))
    np.testing.assert_array_equal(frame.validity[3:], np.zeros(4, dtype=np.float32))


def test_raw_droid_aliases_load_with_per_signal_validity(tmp_path):
    path = tmp_path / "trajectory.h5"
    with h5py.File(path, "w") as target:
        demo = target.create_group("demo")
        robot = demo.create_group("observation/robot_state")
        robot.create_dataset("motor_torques_measured", data=np.ones((4, 7)))
        robot.create_dataset("motor_torques_external", data=np.full((4, 7), 2.0))
        block = load_sensor_block(demo, 5)
    assert block.values.shape == (5, SENSOR_DIM)
    assert block.validity.shape == (5, VALIDITY_DIM)
    np.testing.assert_array_equal(block.validity[:4, 0], np.ones(4))
    np.testing.assert_array_equal(block.validity[:4, 2], np.ones(4))
    assert block.validity[4].sum() == 0
    assert block.coverage["joint_torque_measured"] == pytest.approx(0.8)
    assert block.source_paths["joint_torque_external"].endswith("motor_torques_external")


def test_canonical_writer_and_explicit_mask_round_trip(tmp_path):
    path = tmp_path / "episode.hdf5"
    values = np.arange(3 * SENSOR_DIM, dtype=np.float32).reshape(3, SENSOR_DIM)
    validity = np.ones((3, VALIDITY_DIM), dtype=np.float32)
    validity[1, 2] = 0.0
    with h5py.File(path, "w") as target:
        demo = target.create_group("data/demo_0")
        demo.attrs["num_samples"] = 3
        write_sensor_group(
            demo,
            values,
            validity,
            np.arange(3, dtype=np.float64) / 15,
            source="unit_test",
        )
        block = load_sensor_block(demo, 3)
        assert demo["sensors/franka"].attrs["schema_version"] == "2.0"
    assert block.validity[1, 2] == 0.0
    np.testing.assert_array_equal(
        block.values[1, SIGNAL_SLICES["joint_torque_external"]], np.zeros(7)
    )


def test_masked_stats_ignore_zero_fill_from_missing_rows():
    values = np.zeros((3, SENSOR_DIM), dtype=np.float32)
    validity = np.zeros((3, VALIDITY_DIM), dtype=np.float32)
    values[1:, SIGNAL_SLICES["joint_torque_measured"]] = np.array([[2] * 7, [4] * 7])
    validity[1:, 0] = 1
    result = masked_sensor_stats(values, validity)
    assert result["mean"][:7] == pytest.approx([3.0] * 7)
    # A completely absent signal gets neutral normalization, not zero std.
    external = SIGNAL_SLICES["joint_torque_external"]
    assert result["std"][external.start:external.stop] == pytest.approx([1.0] * 7)


def test_isaac_capture_labels_applied_torque_as_commanded_only():
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_names=[*[f"panda_joint{i}" for i in range(1, 8)], "finger_joint"],
            applied_torque=np.array([[*range(1, 8), 0]], dtype=np.float32),
        )
    )
    contact = SimpleNamespace(
        data=SimpleNamespace(net_forces_w=np.array([[[0.0, 0.0, 2.0]]], dtype=np.float32))
    )

    class Scene(dict):
        sensors = {"gripper__all_objs": contact}

    frame = sensor_frame_from_isaac_env(SimpleNamespace(scene=Scene(robot=robot)))
    assert frame.validity[0] == 0.0
    assert frame.validity[1] == 1.0
    assert frame.validity[5] == 1.0
    assert frame.validity[6] == 1.0
    assert frame.values[SIGNAL_SLICES["gripper_touch"]][0] == 1.0


def test_two_finger_touch_does_not_disappear_when_opposing_forces_cancel():
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_names=[*[f"panda_joint{i}" for i in range(1, 8)], "finger_joint"],
            applied_torque=np.zeros((1, 8), dtype=np.float32),
        )
    )
    contact = SimpleNamespace(
        data=SimpleNamespace(
            net_forces_w=np.array(
                [[[2.0, 0.0, 0.0], [-2.0, 0.0, 0.0]]], dtype=np.float32
            )
        )
    )

    class Scene(dict):
        sensors = {"gripper__all_contacts": contact}

    frame = sensor_frame_from_isaac_env(SimpleNamespace(scene=Scene(robot=robot)))
    np.testing.assert_array_equal(
        frame.values[SIGNAL_SLICES["gripper_contact_force"]], np.zeros(3)
    )
    assert frame.values[SIGNAL_SLICES["gripper_touch"]][0] == 1.0


def test_contact_summary_requires_coverage_and_a_real_touch():
    values = np.zeros((4, SENSOR_DIM), dtype=np.float32)
    validity = np.zeros((4, VALIDITY_DIM), dtype=np.float32)
    validity[:, 5:7] = 1.0
    assert not summarize_contact_telemetry(values, validity)["passed"]
    values[2, SIGNAL_SLICES["gripper_touch"]] = 1.0
    summary = summarize_contact_telemetry(values, validity)
    assert summary["passed"]
    assert summary["coverage"] == 1.0
    assert summary["touch_samples"] == 1


def test_sim6_contact_installer_uses_unfiltered_two_finger_expression():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    env_cfg = SimpleNamespace(scene=SimpleNamespace())
    installed = install_sim6_gripper_contact_sensor(
        env_cfg, sensor_cfg_factory=factory
    )
    assert getattr(env_cfg.scene, GRIPPER_CONTACT_SENSOR_NAME) is installed
    assert captured["prim_path"] == GRIPPER_CONTACT_PRIM_PATH
    assert captured["filter_prim_paths_expr"] == []


def test_droid_external_torque_patcher_is_narrow_and_idempotent(tmp_path):
    root = tmp_path / "droid"
    target = root / "droid/franka/robot.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "state_dict = {\n"
        '            "motor_torques_measured": list(robot_state.motor_torques_measured),\n'
        "}\n"
    )
    first = patch_checkout(root)
    second = patch_checkout(root)
    assert first["changed"] is True
    assert second["changed"] is False
    assert target.read_text().count('"motor_torques_external":') == 1


def test_sensor_layout_has_stable_non_overlapping_widths():
    assert SENSOR_DIM == sum(spec.width for spec in SIGNAL_SPECS)
    assert VALIDITY_DIM == len(SIGNAL_SPECS)
    assert [SIGNAL_SLICES[spec.name].start for spec in SIGNAL_SPECS] == sorted(
        SIGNAL_SLICES[spec.name].start for spec in SIGNAL_SPECS
    )
