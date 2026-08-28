from __future__ import annotations

import h5py
import numpy as np
import pytest

from scripts.audit_real_droid_episode import audit_episode


pytestmark = pytest.mark.l0


def _episode(tmp_path, *, frames: int = 45):
    trajectory = tmp_path / "trajectory.h5"
    exterior = tmp_path / "exterior.mp4"
    wrist = tmp_path / "wrist.mp4"
    exterior.touch()
    wrist.touch()
    with h5py.File(trajectory, "w") as target:
        target.attrs["success"] = True
        robot = target.create_group("observation/robot_state")
        robot.create_dataset("cartesian_position", data=np.zeros((frames, 6)))
        robot.create_dataset(
            "joint_positions",
            data=np.tile([0.0, -0.5, 0.0, -1.5, 0.0, 1.5, 0.0], (frames, 1)),
        )
        robot.create_dataset("gripper_position", data=np.zeros((frames, 1)))
        robot.create_dataset("motor_torques_measured", data=np.ones((frames, 7)))
        robot.create_dataset("motor_torques_external", data=np.full((frames, 7), 2.0))
        action = target.create_group("action")
        action.create_dataset("cartesian_position", data=np.zeros((frames, 6)))
        action.create_dataset(
            "joint_position",
            data=np.tile([0.0, -0.5, 0.0, -1.5, 0.0, 1.5, 0.0], (frames, 1)),
        )
        action.create_dataset("gripper_position", data=np.zeros((frames, 1)))
        timestamp = target.create_group("observation/timestamp")
        timestamp.create_dataset(
            "robot_timestamp_seconds", data=np.arange(frames, dtype=np.float64) / 15.0
        )
        timestamp.create_dataset("robot_timestamp_nanos", data=np.zeros(frames))
    return {
        "trajectory": trajectory,
        "exterior_video": exterior,
        "wrist_video": wrist,
        "instruction": "Pick up the banana",
    }


def test_ready_pilot_passes_all_conversion_gates(tmp_path):
    spec = _episode(tmp_path)

    def probe(_):
        return {"frames": 45, "fps": 15.0, "width": 320, "height": 180}

    report = audit_episode(spec, require_success=True, video_probe=probe)
    assert report["status"] == "pass"
    assert report["ready_for_conversion"] is True
    assert report["sensor_coverage"]["joint_torque_measured"] == 1.0
    assert report["sensor_coverage"]["joint_torque_external"] == 1.0


def test_unaligned_video_and_missing_external_torque_fail(tmp_path):
    spec = _episode(tmp_path)
    with h5py.File(spec["trajectory"], "r+") as target:
        del target["observation/robot_state/motor_torques_external"]

    def probe(_):
        return {"frames": 90, "fps": 30.0, "width": 640, "height": 360}

    report = audit_episode(spec, video_probe=probe)
    assert report["status"] == "fail"
    failures = {item["name"] for item in report["checks"] if item["status"] == "fail"}
    assert "sensors.external_torque" in failures
    assert "video.frame_alignment" in failures
    assert "video.frame_rate" in failures


def test_missing_timestamps_can_be_warn_only_for_legacy_data(tmp_path):
    spec = _episode(tmp_path)
    with h5py.File(spec["trajectory"], "r+") as target:
        del target["observation/timestamp"]

    def probe(_):
        return {"frames": 45, "fps": 15.0, "width": 320, "height": 180}

    report = audit_episode(spec, require_timestamps=False, video_probe=probe)
    assert report["status"] == "warn"
    assert report["ready_for_conversion"] is True
