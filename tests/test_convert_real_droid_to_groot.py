from __future__ import annotations

import h5py
import numpy as np
import pytest

from scripts import convert_real_droid_to_groot as converter
from scripts.franka_sensor_schema import SIGNAL_SLICES


pytestmark = pytest.mark.l0


def test_cartesian_xyzrpy_to_eef_9d_identity_rotation():
    pose = np.array([[0.1, -0.2, 0.3, 0.0, 0.0, 0.0]], dtype=np.float32)
    converted = converter.cartesian_xyzrpy_to_eef_9d(pose)
    np.testing.assert_allclose(
        converted,
        [[0.1, -0.2, 0.3, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]],
    )


def test_read_raw_episode_keeps_real_torque_and_masks_missing_touch(
    tmp_path, monkeypatch
):
    frames = 45
    trajectory = tmp_path / "trajectory.h5"
    with h5py.File(trajectory, "w") as target:
        robot = target.create_group("observation/robot_state")
        robot.create_dataset("cartesian_position", data=np.zeros((frames, 6)))
        robot.create_dataset("joint_positions", data=np.zeros((frames, 7)))
        robot.create_dataset("gripper_position", data=np.zeros((frames, 1)))
        robot.create_dataset("motor_torques_measured", data=np.ones((frames, 7)))
        robot.create_dataset("motor_torques_external", data=np.full((frames, 7), 2.0))

    monkeypatch.setattr(converter, "video_info", lambda _: (frames, 15.0))
    episode = converter.read_raw_episode(
        trajectory, tmp_path / "exterior.mp4", tmp_path / "wrist.mp4"
    )

    assert episode["state"].shape == (frames, 17)
    assert episode["action"].shape == (frames, 17)
    np.testing.assert_array_equal(
        episode["sensors"][:, SIGNAL_SLICES["joint_torque_measured"]], 1.0
    )
    np.testing.assert_array_equal(
        episode["sensors"][:, SIGNAL_SLICES["joint_torque_external"]], 2.0
    )
    np.testing.assert_array_equal(
        episode["validity"][:, :3], np.tile([1.0, 0.0, 1.0], (frames, 1))
    )
    np.testing.assert_array_equal(episode["validity"][:, 3:], 0.0)


def test_read_raw_episode_rejects_unaligned_camera_frames(tmp_path, monkeypatch):
    frames = 45
    trajectory = tmp_path / "trajectory.h5"
    with h5py.File(trajectory, "w") as target:
        robot = target.create_group("observation/robot_state")
        robot.create_dataset("cartesian_position", data=np.zeros((frames, 6)))
        robot.create_dataset("joint_positions", data=np.zeros((frames, 7)))
        robot.create_dataset("gripper_position", data=np.zeros((frames, 1)))

    counts = iter(((frames * 2, 30.0), (frames * 2, 30.0)))
    monkeypatch.setattr(converter, "video_info", lambda _: next(counts))
    with pytest.raises(ValueError, match="one frame per trajectory row"):
        converter.read_raw_episode(
            trajectory, tmp_path / "exterior.mp4", tmp_path / "wrist.mp4"
        )
