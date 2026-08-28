"""GR00T-compatible recording for successful Gemini-supervised Sim episodes.

The live runner intentionally disables RoboLab's legacy Sim 5 recorder.  This
collector records the Sim 6 tensors directly and publishes an episode only
after the physical success checks pass.  Partial/failed runs never appear as
training HDF5 files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import h5py
import numpy as np

try:
    from franka_sensor_schema import (
        SensorFrame,
        SensorCaptureBuffer,
        empty_sensor_frame,
        sensor_frame_from_isaac_env,
        summarize_contact_telemetry,
        write_sensor_group,
    )
except ModuleNotFoundError:
    from scripts.franka_sensor_schema import (
        SensorFrame,
        SensorCaptureBuffer,
        empty_sensor_frame,
        sensor_frame_from_isaac_env,
        summarize_contact_telemetry,
        write_sensor_group,
    )


@dataclass
class GeminiEpisodeDatasetRecorder:
    """Buffer state/actions and stream the paired two-camera episode video."""

    output_dir: Path
    episode_index: int
    metadata: dict[str, Any]
    video_writer_factory: Callable[[str, int], Any]
    unpack_images: Callable[..., dict[str, np.ndarray]]
    fps: int = 15
    video_scale: float = 0.5
    require_contact_telemetry: bool = False
    minimum_contact_coverage: float = 0.95
    minimum_touch_samples: int = 1
    _actions: list[np.ndarray] = field(default_factory=list, init=False)
    _joints: list[np.ndarray] = field(default_factory=list, init=False)
    _eef_positions: list[np.ndarray] = field(default_factory=list, init=False)
    _eef_quaternions: list[np.ndarray] = field(default_factory=list, init=False)
    _banana_poses: list[np.ndarray] = field(default_factory=list, init=False)
    _plate_poses: list[np.ndarray] = field(default_factory=list, init=False)
    _sensors: SensorCaptureBuffer = field(default_factory=SensorCaptureBuffer, init=False)
    _video: Any = field(default=None, init=False)
    _partial_video_path: Path = field(init=False)
    _final_video_path: Path = field(init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.episode_index < 0:
            raise ValueError("episode_index must be non-negative")
        if self.fps <= 0 or self.video_scale <= 0:
            raise ValueError("fps and video_scale must be positive")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"episode_{self.episode_index:06d}_policy"
        self._partial_video_path = self.output_dir / f"{stem}.partial.mp4"
        self._final_video_path = self.output_dir / f"{stem}.mp4"
        # A partial file without either published companion can only belong to
        # an interrupted attempt at this exact index; it is never admissible.
        if (
            self._partial_video_path.exists()
            and not self.hdf5_path.exists()
            and not self._final_video_path.exists()
        ):
            self._partial_video_path.unlink()
        for path in (self.hdf5_path, self._final_video_path, self._partial_video_path):
            if path.exists():
                raise FileExistsError(f"refusing to overwrite episode artifact: {path}")
        self._video = self.video_writer_factory(str(self._partial_video_path), self.fps)

    @property
    def sample_count(self) -> int:
        return len(self._actions)

    @property
    def hdf5_path(self) -> Path:
        return self.output_dir / f"run_{self.episode_index}.hdf5"

    @property
    def video_path(self) -> Path:
        return self._final_video_path

    @staticmethod
    def _tensor_numpy(value: Any) -> np.ndarray:
        value = getattr(value, "torch", value)
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        return np.asarray(value)

    @staticmethod
    def _xyzw_to_wxyz(quaternion: np.ndarray) -> np.ndarray:
        return quaternion[[3, 0, 1, 2]]

    def append(
        self,
        env: Any,
        action: Any,
        observation: dict[str, Any],
        *,
        eef_position: np.ndarray,
        eef_quaternion_wxyz: np.ndarray,
        sensor_frame: SensorFrame | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("cannot append to a closed episode recorder")
        robot = env.scene["robot"]
        joints = self._tensor_numpy(robot.data.joint_pos)[0]
        banana = env.scene["banana"].data.root_pose_w
        plate = env.scene["plate_large"].data.root_pose_w
        banana = self._tensor_numpy(banana)[0].astype(np.float32, copy=True)
        plate = self._tensor_numpy(plate)[0].astype(np.float32, copy=True)
        # Isaac Sim 6 tensors are xyzw; the RoboLab/GR00T bridge records wxyz.
        banana[3:7] = self._xyzw_to_wxyz(banana[3:7])
        plate[3:7] = self._xyzw_to_wxyz(plate[3:7])
        self._actions.append(
            self._tensor_numpy(action)[0].astype(np.float32, copy=True)
        )
        self._joints.append(joints.astype(np.float32, copy=True))
        self._eef_positions.append(np.asarray(eef_position, dtype=np.float32).copy())
        self._eef_quaternions.append(
            np.asarray(eef_quaternion_wxyz, dtype=np.float32).copy()
        )
        self._banana_poses.append(banana)
        self._plate_poses.append(plate)
        if sensor_frame is None:
            try:
                sensor_frame = sensor_frame_from_isaac_env(env)
            except Exception:
                sensor_frame = empty_sensor_frame()
        self._sensors.append(sensor_frame, (self.sample_count - 1) / self.fps)
        combined = self.unpack_images(
            observation, scale=self.video_scale, env_id=0
        )["combined_image"]
        self._video.write(np.asarray(combined, dtype=np.uint8))

    def _close_video(self) -> None:
        if self._video is not None:
            self._video.release()
            self._video = None

    def contact_telemetry_summary(self) -> dict[str, Any]:
        values, validity, _ = self._sensors.arrays()
        return summarize_contact_telemetry(
            values,
            validity,
            minimum_coverage=self.minimum_contact_coverage,
            minimum_touch_samples=self.minimum_touch_samples,
        )

    def publish_success(self, *, trace_path: Path) -> dict[str, Any]:
        """Atomically publish one successful training episode and manifest row."""
        if self._closed:
            raise RuntimeError("episode recorder is already closed")
        if self.sample_count < 41:
            raise ValueError(
                "successful episode is too short for GR00T's 40-step action horizon"
            )
        contact_summary = self.contact_telemetry_summary()
        if self.require_contact_telemetry and not contact_summary["passed"]:
            raise ValueError(
                "contact telemetry admission gate failed: "
                f"coverage={contact_summary['coverage']:.3f}, "
                f"touch_samples={contact_summary['touch_samples']}"
            )
        self._close_video()
        if not self._partial_video_path.is_file():
            raise FileNotFoundError(
                f"episode video encoder produced no file: {self._partial_video_path}"
            )
        temporary_hdf5 = self.hdf5_path.with_suffix(".hdf5.tmp")
        with h5py.File(temporary_hdf5, "w") as target:
            target.attrs["total"] = 1
            demo = target.create_group("data/demo_0")
            demo.attrs["success"] = True
            demo.attrs["num_samples"] = self.sample_count
            demo.attrs["source_policy"] = "gemini_robotics_er2_supervised_local_se3_ik"
            demo.attrs["quaternion_convention"] = "wxyz"
            demo.attrs["episode_metadata_json"] = json.dumps(self.metadata, sort_keys=True)
            demo.create_dataset("actions", data=np.stack(self._actions), compression="gzip")
            states = demo.create_group("states")
            articulation = states.create_group("articulation").create_group("robot")
            articulation.create_dataset(
                "joint_position", data=np.stack(self._joints), compression="gzip"
            )
            rigid_objects = states.create_group("rigid_object")
            rigid_objects.create_group("banana").create_dataset(
                "root_pose", data=np.stack(self._banana_poses), compression="gzip"
            )
            rigid_objects.create_group("plate_large").create_dataset(
                "root_pose", data=np.stack(self._plate_poses), compression="gzip"
            )
            ee_pose = demo.create_group("ee_pose")
            ee_pose.create_dataset(
                "position", data=np.stack(self._eef_positions), compression="gzip"
            )
            ee_pose.create_dataset(
                "orientation", data=np.stack(self._eef_quaternions), compression="gzip"
            )
            values, validity, timestamps = self._sensors.arrays()
            write_sensor_group(
                demo,
                values,
                validity,
                timestamps,
                source="gemini_supervised_isaac_sim_6",
            )
        temporary_hdf5.replace(self.hdf5_path)
        self._partial_video_path.replace(self._final_video_path)
        row = {
            "episode_index": self.episode_index,
            "status": "success",
            "source_policy": "gemini_robotics_er2_supervised_local_se3_ik",
            "hdf5": self.hdf5_path.name,
            "video": self.video_path.name,
            "trace": str(trace_path),
            "samples": self.sample_count,
            "contact_telemetry": contact_summary,
            **self.metadata,
        }
        with (self.output_dir / "collection_manifest.jsonl").open(
            "a", encoding="utf-8"
        ) as manifest:
            manifest.write(json.dumps(row, sort_keys=True) + "\n")
        self._closed = True
        return row

    def discard(self) -> None:
        """Close and remove only this recorder's unpublished partial artifacts."""
        if self._closed:
            return
        self._close_video()
        self._partial_video_path.unlink(missing_ok=True)
        self.hdf5_path.with_suffix(".hdf5.tmp").unlink(missing_ok=True)
        self._closed = True
