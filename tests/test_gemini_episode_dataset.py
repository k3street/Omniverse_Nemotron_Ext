import json
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from scripts.convert_robolab_demo_to_groot import episode_provenance
from scripts.franka_sensor_schema import (
    SENSOR_DIM,
    SIGNAL_SLICES,
    VALIDITY_DIM,
    SensorFrame,
)
from scripts.gemini_episode_dataset import GeminiEpisodeDatasetRecorder


class FakeVideoWriter:
    def __init__(self, path: str, fps: int):
        self.path = path
        self.frames = 0

    def write(self, frame):
        self.frames += 1

    def release(self):
        with open(self.path, "wb") as output:
            output.write(b"fake-mp4")


def fake_unpack(observation, **kwargs):
    return {"combined_image": np.zeros((8, 16, 3), dtype=np.uint8)}


def fake_env():
    robot = SimpleNamespace(
        data=SimpleNamespace(
            joint_pos=np.zeros((1, 8), dtype=np.float32),
            joint_names=[f"panda_joint{i}" for i in range(1, 8)] + ["finger_joint"],
        )
    )
    banana = SimpleNamespace(
        data=SimpleNamespace(
            root_pose_w=np.array([[0.4, 0.1, 0.02, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
        )
    )
    bagel = SimpleNamespace(
        data=SimpleNamespace(
            root_pose_w=np.array([[0.3, -0.1, 0.02, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
        )
    )
    plate = SimpleNamespace(
        data=SimpleNamespace(
            root_pose_w=np.array([[0.6, 0.2, 0.0, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
        )
    )
    return SimpleNamespace(
        scene={
            "robot": robot,
            "banana": banana,
            "bagel_06": bagel,
            "plate_large": plate,
        }
    )


def append_samples(recorder, count=41, sensor_frame=None):
    env = fake_env()
    for _ in range(count):
        recorder.append(
            env,
            np.zeros((1, 8), dtype=np.float32),
            {},
            eef_position=np.array([0.4, 0.1, 0.2], dtype=np.float32),
            eef_quaternion_wxyz=np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
            sensor_frame=sensor_frame,
        )


def test_successful_gemini_completion_publishes_convertible_pair(tmp_path):
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=7,
        metadata={"banana_yaw_deg": 45.0},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
    )
    append_samples(recorder)
    row = recorder.publish_success(trace_path=tmp_path / "trace.json")

    assert row["samples"] == 41
    assert (tmp_path / "run_7.hdf5").is_file()
    assert (tmp_path / "episode_000007_policy.mp4").is_file()
    with h5py.File(tmp_path / "run_7.hdf5", "r") as source:
        demo = source["data/demo_0"]
        assert bool(demo.attrs["success"])
        assert demo.attrs["source_policy"].startswith("gemini_robotics")
        assert demo["actions"].shape == (41, 8)
        assert demo["ee_pose/orientation"].shape == (41, 4)
        assert np.array_equal(
            demo["states/rigid_object/banana/root_pose"][0, 3:7],
            np.array([1.0, 0.0, 0.0, 0.0]),
        )
    manifest = json.loads((tmp_path / "collection_manifest.jsonl").read_text())
    assert manifest["status"] == "success"
    assert manifest["banana_yaw_deg"] == 45.0
    provenance = episode_provenance(tmp_path / "run_7.hdf5", "demo_0")
    assert provenance["source_policy"].startswith("gemini_robotics")
    assert provenance["quaternion_convention"] == "wxyz"
    assert provenance["collection"]["banana_yaw_deg"] == 45.0


def test_recorder_publishes_selected_object_identity_instead_of_banana(tmp_path):
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=8,
        metadata={"instruction": "Put the bagel on the plate"},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
        movable_object_asset="bagel_06",
        target_receptacle_asset="plate_large",
    )
    append_samples(recorder)
    row = recorder.publish_success(trace_path=tmp_path / "trace.json")

    assert row["scene_roles"] == {
        "movable_object": "bagel_06",
        "target_receptacle": "plate_large",
    }
    with h5py.File(tmp_path / "run_8.hdf5", "r") as source:
        demo = source["data/demo_0"]
        assert demo.attrs["movable_object_asset"] == "bagel_06"
        assert "states/rigid_object/bagel_06/root_pose" in demo
        assert "states/rigid_object/banana/root_pose" not in demo


def test_failed_completion_discards_partial_and_never_creates_hdf5(tmp_path):
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=3,
        metadata={},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
    )
    append_samples(recorder, count=5)
    recorder.discard()

    assert not (tmp_path / "run_3.hdf5").exists()
    assert not (tmp_path / "episode_000003_policy.mp4").exists()
    assert not (tmp_path / "episode_000003_policy.partial.mp4").exists()
    assert not (tmp_path / "collection_manifest.jsonl").exists()


def test_converter_rejects_an_episode_not_marked_successful(tmp_path):
    path = tmp_path / "failed.hdf5"
    with h5py.File(path, "w") as target:
        demo = target.create_group("data/demo_0")
        demo.attrs["success"] = False
    with pytest.raises(ValueError, match="not marked successful"):
        episode_provenance(path, "demo_0")


def test_new_attempt_removes_only_unpublished_stale_partial(tmp_path):
    stale = tmp_path / "episode_000005_policy.partial.mp4"
    stale.write_bytes(b"interrupted")
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=5,
        metadata={},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
    )
    recorder.discard()
    assert not stale.exists()


def test_contact_gate_rejects_zero_validity_episode(tmp_path):
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=8,
        metadata={},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
        require_contact_telemetry=True,
    )
    empty = SensorFrame(
        values=np.zeros(SENSOR_DIM, dtype=np.float32),
        validity=np.zeros(VALIDITY_DIM, dtype=np.float32),
    )
    append_samples(recorder, sensor_frame=empty)
    with pytest.raises(ValueError, match="contact telemetry admission gate failed"):
        recorder.publish_success(trace_path=tmp_path / "trace.json")
    recorder.discard()
    assert not recorder.hdf5_path.exists()


def test_contact_gate_publishes_episode_with_valid_touch(tmp_path):
    values = np.zeros(SENSOR_DIM, dtype=np.float32)
    validity = np.zeros(VALIDITY_DIM, dtype=np.float32)
    values[SIGNAL_SLICES["gripper_contact_force"]] = [0.0, 0.0, 2.0]
    values[SIGNAL_SLICES["gripper_touch"]] = 1.0
    validity[5:7] = 1.0
    recorder = GeminiEpisodeDatasetRecorder(
        output_dir=tmp_path,
        episode_index=9,
        metadata={},
        video_writer_factory=FakeVideoWriter,
        unpack_images=fake_unpack,
        require_contact_telemetry=True,
    )
    append_samples(recorder, sensor_frame=SensorFrame(values, validity))
    row = recorder.publish_success(trace_path=tmp_path / "trace.json")
    assert row["contact_telemetry"]["passed"]
    assert row["contact_telemetry"]["touch_samples"] == 41
