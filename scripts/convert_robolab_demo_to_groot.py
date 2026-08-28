#!/usr/bin/env python3
"""Convert a RoboLab DROID HDF5 demo plus its two-camera video to GR00T LeRobot v2.1."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import h5py
import numpy as np

try:
    from franka_sensor_schema import (
        SENSOR_COLUMN,
        SENSOR_DIM,
        SENSOR_SCHEMA_VERSION,
        SIGNAL_SPECS,
        VALIDITY_COLUMN,
        VALIDITY_DIM,
        load_sensor_block,
        masked_sensor_stats,
        sensor_modality_metadata,
    )
except ModuleNotFoundError:  # Support imports as scripts.convert_robolab_demo_to_groot.
    from scripts.franka_sensor_schema import (
        SENSOR_COLUMN,
        SENSOR_DIM,
        SENSOR_SCHEMA_VERSION,
        SIGNAL_SPECS,
        VALIDITY_COLUMN,
        VALIDITY_DIM,
        load_sensor_block,
        masked_sensor_stats,
        sensor_modality_metadata,
    )


DROID_EEF_ROTATION_CORRECT = np.array(
    [[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=np.float64
)


def episode_provenance(hdf5_path: Path, demo_key: str) -> dict:
    """Read admission/provenance metadata and reject non-successful demos."""
    with h5py.File(hdf5_path, "r") as source:
        demo = source[f"data/{demo_key}"]
        if not bool(demo.attrs.get("success", False)):
            raise ValueError(f"Episode is not marked successful: {hdf5_path}:{demo_key}")
        convention = str(demo.attrs.get("quaternion_convention", "wxyz"))
        if convention != "wxyz":
            raise ValueError(
                f"Unsupported recorded quaternion convention {convention!r}: "
                f"{hdf5_path}:{demo_key}"
            )
        raw_metadata = demo.attrs.get("episode_metadata_json", "{}")
        if isinstance(raw_metadata, bytes):
            raw_metadata = raw_metadata.decode("utf-8")
        try:
            metadata = json.loads(str(raw_metadata))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid episode_metadata_json in {hdf5_path}:{demo_key}"
            ) from error
        if not isinstance(metadata, dict):
            raise ValueError("episode_metadata_json must contain a JSON object")
        return {
            "source_policy": str(demo.attrs.get("source_policy", "unknown")),
            "quaternion_convention": convention,
            "collection": metadata,
        }


def quat_wxyz_to_matrix(quaternions: np.ndarray) -> np.ndarray:
    q = np.asarray(quaternions, dtype=np.float64)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = np.moveaxis(q, -1, 0)
    matrices = np.empty((*q.shape[:-1], 3, 3), dtype=np.float64)
    matrices[..., 0, 0] = 1 - 2 * (y * y + z * z)
    matrices[..., 0, 1] = 2 * (x * y - z * w)
    matrices[..., 0, 2] = 2 * (x * z + y * w)
    matrices[..., 1, 0] = 2 * (x * y + z * w)
    matrices[..., 1, 1] = 1 - 2 * (x * x + z * z)
    matrices[..., 1, 2] = 2 * (y * z - x * w)
    matrices[..., 2, 0] = 2 * (x * z - y * w)
    matrices[..., 2, 1] = 2 * (y * z + x * w)
    matrices[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return matrices


def eef_9d(positions: np.ndarray, quaternions_wxyz: np.ndarray) -> np.ndarray:
    rotation = quat_wxyz_to_matrix(quaternions_wxyz) @ DROID_EEF_ROTATION_CORRECT
    rot6d = rotation[..., :2, :].reshape(-1, 6)
    return np.concatenate([positions, rot6d], axis=-1).astype(np.float32)


def video_info(path: Path) -> tuple[int, int, int, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    cap.release()
    return width, height, frames, fps


def write_video_half(source: Path, output: Path, *, x: int, width: int, height: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    vf = f"crop={width}:{height}:{x}:0,scale=320:180:flags=bilinear"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
            "-vf", vf, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
        ],
        check=True,
    )


def stats(
    values: np.ndarray, *, unit_std_for_constant: bool = False
) -> dict[str, list[float]]:
    values = np.asarray(values, dtype=np.float32)
    std = values.std(axis=0)
    if unit_std_for_constant:
        std = np.where(std < 1.0e-8, 1.0, std)
    return {
        "mean": values.mean(axis=0).tolist(),
        "std": std.tolist(),
        "min": values.min(axis=0).tolist(),
        "max": values.max(axis=0).tolist(),
        "q01": np.quantile(values, 0.01, axis=0).tolist(),
        "q99": np.quantile(values, 0.99, axis=0).tolist(),
    }


def read_episode(
    hdf5_path: Path, video_path: Path, *, demo_key: str, action_mode: str
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    dict[str, float],
    dict[str, str | None],
    int,
    int,
    float,
]:
    width, height, video_frames, fps = video_info(video_path)
    if width % 2:
        raise ValueError(f"Expected an even-width two-camera video, got {width}x{height}")

    with h5py.File(hdf5_path, "r") as source:
        demo = source[f"data/{demo_key}"]
        actions = np.asarray(demo["actions"], dtype=np.float32)
        joints = np.asarray(demo["states/articulation/robot/joint_position"], dtype=np.float32)
        ee_position = np.asarray(demo["ee_pose/position"], dtype=np.float32)
        ee_quaternion = np.asarray(demo["ee_pose/orientation"], dtype=np.float32)
        length = min(video_frames, len(actions), len(joints), len(ee_position))
        if length < 41:
            raise ValueError(f"Episode is too short for GR00T's 40-step action horizon: {length}")
        sensor_block = load_sensor_block(demo, length)

    state_eef = eef_9d(ee_position[:length], ee_quaternion[:length])
    state_joint = joints[:length, :7]
    state_gripper = np.clip(joints[:length, 7:8] / (np.pi / 4), 0.0, 1.0).astype(np.float32)
    observation_state = np.concatenate([state_eef, state_gripper, state_joint], axis=-1)

    # The matching Cartesian target is the next observed EE pose; the final
    # frame repeats the last available pose.
    action_eef = np.concatenate([state_eef[1:], state_eef[-1:]], axis=0)
    action_gripper = np.clip(actions[:length, 7:8], 0.0, 1.0).astype(np.float32)
    if action_mode == "absolute_ik":
        action_joint = np.concatenate([state_joint[1:], state_joint[-1:]], axis=0)
    else:
        action_joint = actions[:length, :7].astype(np.float32)
    action = np.concatenate([action_eef, action_gripper, action_joint], axis=-1)
    return (
        observation_state,
        action,
        sensor_block.values,
        sensor_block.validity,
        sensor_block.coverage,
        sensor_block.source_paths,
        width,
        height,
        fps,
    )


def convert_many(
    episodes: list[tuple[Path, Path, str]], output: Path, instruction: str, action_mode: str
) -> None:
    import pandas as pd

    if not episodes:
        raise ValueError("No HDF5/video episode pairs were found")

    data_dir = output / "data" / "chunk-000"
    meta_dir = output / "meta"
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    relative_stats = meta_dir / "relative_stats.json"
    if relative_stats.exists():
        relative_stats.unlink()

    videos = output / "videos" / "chunk-000"
    all_states: list[np.ndarray] = []
    all_actions: list[np.ndarray] = []
    all_sensors: list[np.ndarray] = []
    all_sensor_validity: list[np.ndarray] = []
    episode_metadata: list[dict] = []
    global_index = 0
    dataset_fps: float | None = None
    for episode_index, (hdf5_path, video_path, demo_key) in enumerate(episodes):
        provenance = episode_provenance(hdf5_path, demo_key)
        (
            observation_state,
            action,
            sensor_values,
            sensor_validity,
            sensor_coverage,
            sensor_source_paths,
            width,
            height,
            fps,
        ) = read_episode(
            hdf5_path, video_path, demo_key=demo_key, action_mode=action_mode
        )
        if dataset_fps is None:
            dataset_fps = fps
        elif abs(dataset_fps - fps) > 0.01:
            raise ValueError(f"Mixed video FPS ({dataset_fps} and {fps})")
        length = len(action)
        frame = pd.DataFrame(
            {
                "observation.state": list(observation_state),
                SENSOR_COLUMN: list(sensor_values),
                VALIDITY_COLUMN: list(sensor_validity),
                "action": list(action),
                "timestamp": np.arange(length, dtype=np.float32) / fps,
                "frame_index": np.arange(length, dtype=np.int64),
                "episode_index": np.full(length, episode_index, dtype=np.int64),
                "index": np.arange(global_index, global_index + length, dtype=np.int64),
                "task_index": np.zeros(length, dtype=np.int64),
            }
        )
        name = f"episode_{episode_index:06d}"
        frame.to_parquet(data_dir / f"{name}.parquet", index=False)
        half = width // 2
        write_video_half(video_path, videos / "observation.images.exterior_image_1_left" / f"{name}.mp4", x=0, width=half, height=height)
        write_video_half(video_path, videos / "observation.images.wrist_image_left" / f"{name}.mp4", x=half, width=half, height=height)
        all_states.append(observation_state)
        all_actions.append(action)
        all_sensors.append(sensor_values)
        all_sensor_validity.append(sensor_validity)
        episode_metadata.append({
            "episode_index": episode_index,
            "tasks": [instruction],
            "length": length,
            "sensor_schema_version": SENSOR_SCHEMA_VERSION,
            "sensor_coverage": sensor_coverage,
            "sensor_source_paths": sensor_source_paths,
            **provenance,
        })
        global_index += length

    assert dataset_fps is not None
    observation_state = np.concatenate(all_states)
    action = np.concatenate(all_actions)
    sensor_values = np.concatenate(all_sensors)
    sensor_validity = np.concatenate(all_sensor_validity)

    info = {
        "codebase_version": "v2.1",
        "robot_type": "droid",
        "total_episodes": len(episodes),
        "total_frames": global_index,
        "fps": dataset_fps,
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "chunks_size": 1000,
        "splits": {"train": f"0:{len(episodes)}"},
        "features": {
            "observation.images.exterior_image_1_left": {"dtype": "video", "shape": [180, 320, 3]},
            "observation.images.wrist_image_left": {"dtype": "video", "shape": [180, 320, 3]},
            "observation.state": {"dtype": "float32", "shape": [17]},
            SENSOR_COLUMN: {"dtype": "float32", "shape": [SENSOR_DIM]},
            VALIDITY_COLUMN: {"dtype": "float32", "shape": [VALIDITY_DIM]},
            "action": {"dtype": "float32", "shape": [17]},
            "task_index": {"dtype": "int64", "shape": [1]},
        },
        "sensor_schema": {
            "version": SENSOR_SCHEMA_VERSION,
            "missing_policy": "zero_fill_with_validity_mask",
            "signals": [
                {"name": spec.name, "width": spec.width} for spec in SIGNAL_SPECS
            ],
        },
    }
    modality = {
        "state": {
            "eef_9d": {"start": 0, "end": 9},
            "gripper_position": {"start": 9, "end": 10},
            "joint_position": {"start": 10, "end": 17},
            **sensor_modality_metadata(),
        },
        "action": {
            "eef_9d": {"start": 0, "end": 9},
            "gripper_position": {"start": 9, "end": 10},
            "joint_position": {"start": 10, "end": 17},
        },
        "video": {
            "exterior_image_1_left": {"original_key": "observation.images.exterior_image_1_left"},
            "wrist_image_left": {"original_key": "observation.images.wrist_image_left"},
        },
        "annotation": {
            "language.language_instruction": {"original_key": "task_index"},
        },
    }
    (meta_dir / "info.json").write_text(json.dumps(info, indent=2) + "\n")
    (meta_dir / "modality.json").write_text(json.dumps(modality, indent=2) + "\n")
    (meta_dir / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": instruction}) + "\n"
    )
    (meta_dir / "episodes.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in episode_metadata)
    )
    (meta_dir / "stats.json").write_text(
        json.dumps(
            {
                "observation.state": stats(observation_state),
                SENSOR_COLUMN: masked_sensor_stats(sensor_values, sensor_validity),
                VALIDITY_COLUMN: stats(sensor_validity, unit_std_for_constant=True),
                "action": stats(action),
            },
            indent=2,
        )
        + "\n"
    )
    coverage = {
        spec.name: float(sensor_validity[:, index].mean())
        for index, spec in enumerate(SIGNAL_SPECS)
    }
    print(
        f"Converted {len(episodes)} episodes / {global_index} frames to {output}; "
        f"sensor_coverage={coverage}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hdf5", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--instruction", default="Pick up the banana and put it on the plate"
    )
    parser.add_argument("--action-mode", choices=("absolute_ik", "joint"), default="absolute_ik")
    args = parser.parse_args()
    if args.input_dir:
        input_dir = args.input_dir.resolve()
        episodes = []
        combined_hdf5 = input_dir / "data.hdf5"
        if combined_hdf5.exists():
            with h5py.File(combined_hdf5, "r") as source:
                demo_keys = sorted(source["data"], key=lambda key: int(key.split("_")[-1]))
            for demo_key in demo_keys:
                index = int(demo_key.split("_")[-1])
                video_path = input_dir / f"episode_{index:06d}_policy.mp4"
                if not video_path.exists():
                    raise FileNotFoundError(f"Missing video for {demo_key}: {video_path}")
                episodes.append((combined_hdf5, video_path, demo_key))
        else:
            hdf5_files = sorted(input_dir.glob("run_*.hdf5"), key=lambda p: int(p.stem.split("_")[-1]))
            for hdf5_path in hdf5_files:
                index = int(hdf5_path.stem.split("_")[-1])
                video_path = input_dir / f"episode_{index:06d}_policy.mp4"
                if not video_path.exists():
                    raise FileNotFoundError(f"Missing video for {hdf5_path.name}: {video_path}")
                episodes.append((hdf5_path, video_path, "demo_0"))
    elif args.hdf5 and args.video:
        episodes = [(args.hdf5.resolve(), args.video.resolve(), "demo_0")]
    else:
        parser.error("provide --input-dir, or both --hdf5 and --video")
    convert_many(episodes, args.output.resolve(), args.instruction, args.action_mode)


if __name__ == "__main__":
    main()
