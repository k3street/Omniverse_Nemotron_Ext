#!/usr/bin/env python3
"""Convert raw real-robot DROID trajectories to sensor-aware GR00T data."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import cv2
import h5py
import numpy as np

try:
    from convert_robolab_demo_to_groot import stats
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
except ModuleNotFoundError:  # Support imports as scripts.convert_real_droid_to_groot.
    from scripts.convert_robolab_demo_to_groot import stats
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


def _dataset(group: Any, *paths: str) -> np.ndarray | None:
    for path in paths:
        if path in group:
            return np.asarray(group[path])
    return None


def _required(group: Any, *paths: str) -> np.ndarray:
    value = _dataset(group, *paths)
    if value is None:
        raise KeyError(f"none of the required DROID datasets exist: {paths}")
    return value


def cartesian_xyzrpy_to_eef_9d(cartesian: np.ndarray) -> np.ndarray:
    pose = np.asarray(cartesian, dtype=np.float64)
    if pose.ndim != 2 or pose.shape[1] != 6:
        raise ValueError(f"Cartesian pose must have shape (T, 6), got {pose.shape}")
    roll, pitch, yaw = pose[:, 3], pose[:, 4], pose[:, 5]
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    rotation = np.empty((len(pose), 3, 3), dtype=np.float64)
    # R = Rz(yaw) @ Ry(pitch) @ Rx(roll), inverse of scipy as_euler("xyz").
    rotation[:, 0, 0] = cy * cp
    rotation[:, 0, 1] = cy * sp * sr - sy * cr
    rotation[:, 0, 2] = cy * sp * cr + sy * sr
    rotation[:, 1, 0] = sy * cp
    rotation[:, 1, 1] = sy * sp * sr + cy * cr
    rotation[:, 1, 2] = sy * sp * cr - cy * sr
    rotation[:, 2, 0] = -sp
    rotation[:, 2, 1] = cp * sr
    rotation[:, 2, 2] = cp * cr
    return np.concatenate([pose[:, :3], rotation[:, :2, :].reshape(-1, 6)], axis=1).astype(
        np.float32
    )


def video_info(path: Path) -> tuple[int, float]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open DROID video: {path}")
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    capture.release()
    if frames <= 0 or fps <= 0.0:
        raise ValueError(
            f"video has invalid metadata: {path} (frames={frames}, fps={fps})"
        )
    return frames, fps


def write_scaled_video(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            "scale=320:180:flags=bilinear",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        check=True,
    )


def read_raw_episode(
    trajectory: Path, exterior_video: Path, wrist_video: Path
) -> dict[str, Any]:
    exterior_frames, exterior_fps = video_info(exterior_video)
    wrist_frames, wrist_fps = video_info(wrist_video)
    if abs(exterior_fps - wrist_fps) > 0.01:
        raise ValueError(
            f"camera FPS mismatch: exterior={exterior_fps}, wrist={wrist_fps}"
        )
    with h5py.File(trajectory, "r") as source:
        cartesian = _required(source, "observation/robot_state/cartesian_position")
        joints = _required(source, "observation/robot_state/joint_positions")
        gripper = _required(source, "observation/robot_state/gripper_position")
        action_cartesian = _dataset(source, "action/cartesian_position")
        action_joints = _dataset(source, "action/joint_position")
        action_gripper = _dataset(source, "action/gripper_position")
        lengths = [len(cartesian), len(joints), len(gripper)]
        lengths.extend(
            len(value)
            for value in (action_cartesian, action_joints, action_gripper)
            if value is not None
        )
        length = min(lengths)
        if length < 41:
            raise ValueError(f"episode is too short for GR00T's 40-step horizon: {length}")
        if exterior_frames != length or wrist_frames != length:
            raise ValueError(
                "DROID videos must be timestep-aligned with one frame per trajectory row; "
                f"trajectory={length}, exterior={exterior_frames}, wrist={wrist_frames}. "
                "Align the camera streams from their timestamps before conversion."
            )
        sensor_block = load_sensor_block(source, length)

    state_eef = cartesian_xyzrpy_to_eef_9d(cartesian[:length])
    state_joint = np.asarray(joints[:length, :7], dtype=np.float32)
    state_gripper = (
        np.asarray(gripper[:length], dtype=np.float32).reshape(length, -1)[:, :1]
    )
    observation_state = np.concatenate([state_eef, state_gripper, state_joint], axis=1)

    if action_cartesian is None:
        action_eef = np.concatenate([state_eef[1:], state_eef[-1:]], axis=0)
    else:
        action_eef = cartesian_xyzrpy_to_eef_9d(action_cartesian[:length])
    if action_joints is None:
        action_joint = np.concatenate([state_joint[1:], state_joint[-1:]], axis=0)
    else:
        action_joint = np.asarray(action_joints[:length, :7], dtype=np.float32)
    if action_gripper is None:
        action_grip = state_gripper
    else:
        action_grip = (
            np.asarray(action_gripper[:length], dtype=np.float32)
            .reshape(length, -1)[:, :1]
        )
    action = np.concatenate([action_eef, action_grip, action_joint], axis=1)
    return {
        "state": observation_state,
        "action": action,
        "sensors": sensor_block.values,
        "validity": sensor_block.validity,
        "coverage": sensor_block.coverage,
        "source_paths": sensor_block.source_paths,
        "length": length,
        "fps": exterior_fps,
    }


def _resolve_manifest_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_episode_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.manifest:
        manifest = args.manifest.resolve()
        records = json.loads(manifest.read_text())
        if not isinstance(records, list) or not records:
            raise ValueError("manifest must be a non-empty JSON list")
        result = []
        for record in records:
            result.append(
                {
                    "trajectory": _resolve_manifest_path(
                        manifest.parent, record["trajectory"]
                    ),
                    "exterior_video": _resolve_manifest_path(
                        manifest.parent, record["exterior_video"]
                    ),
                    "wrist_video": _resolve_manifest_path(
                        manifest.parent, record["wrist_video"]
                    ),
                    "instruction": record.get("instruction", args.instruction),
                }
            )
        return result
    required = (args.trajectory, args.exterior_video, args.wrist_video)
    if any(value is None for value in required):
        raise ValueError(
            "provide --manifest, or --trajectory, --exterior-video, and --wrist-video"
        )
    return [
        {
            "trajectory": args.trajectory.resolve(),
            "exterior_video": args.exterior_video.resolve(),
            "wrist_video": args.wrist_video.resolve(),
            "instruction": args.instruction,
        }
    ]


def convert(specs: list[dict[str, Any]], output: Path) -> None:
    import pandas as pd

    data_dir = output / "data/chunk-000"
    video_dir = output / "videos/chunk-000"
    meta_dir = output / "meta"
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    relative_stats = meta_dir / "relative_stats.json"
    if relative_stats.exists():
        relative_stats.unlink()
    all_state, all_action, all_sensors, all_validity = [], [], [], []
    episodes, task_to_index = [], {}
    frame_offset = 0
    dataset_fps = None
    for episode_index, spec in enumerate(specs):
        for key in ("trajectory", "exterior_video", "wrist_video"):
            if not spec[key].is_file():
                raise FileNotFoundError(f"missing {key}: {spec[key]}")
        episode = read_raw_episode(
            spec["trajectory"], spec["exterior_video"], spec["wrist_video"]
        )
        fps = episode["fps"]
        if dataset_fps is None:
            dataset_fps = fps
        elif abs(dataset_fps - fps) > 0.01:
            raise ValueError(f"mixed episode FPS: {dataset_fps} and {fps}")
        instruction = str(spec["instruction"])
        task_index = task_to_index.setdefault(instruction, len(task_to_index))
        length = episode["length"]
        frame = pd.DataFrame(
            {
                "observation.state": list(episode["state"]),
                SENSOR_COLUMN: list(episode["sensors"]),
                VALIDITY_COLUMN: list(episode["validity"]),
                "action": list(episode["action"]),
                "timestamp": np.arange(length, dtype=np.float32) / fps,
                "frame_index": np.arange(length, dtype=np.int64),
                "episode_index": np.full(length, episode_index, dtype=np.int64),
                "index": np.arange(frame_offset, frame_offset + length, dtype=np.int64),
                "task_index": np.full(length, task_index, dtype=np.int64),
            }
        )
        name = f"episode_{episode_index:06d}"
        frame.to_parquet(data_dir / f"{name}.parquet", index=False)
        write_scaled_video(
            spec["exterior_video"],
            video_dir / "observation.images.exterior_image_1_left" / f"{name}.mp4",
        )
        write_scaled_video(
            spec["wrist_video"],
            video_dir / "observation.images.wrist_image_left" / f"{name}.mp4",
        )
        all_state.append(episode["state"])
        all_action.append(episode["action"])
        all_sensors.append(episode["sensors"])
        all_validity.append(episode["validity"])
        episodes.append(
            {
                "episode_index": episode_index,
                "tasks": [instruction],
                "length": length,
                "sensor_schema_version": SENSOR_SCHEMA_VERSION,
                "sensor_coverage": episode["coverage"],
                "sensor_source_paths": episode["source_paths"],
                "source_trajectory": str(spec["trajectory"]),
            }
        )
        frame_offset += length

    assert dataset_fps is not None
    state = np.concatenate(all_state)
    action = np.concatenate(all_action)
    sensors = np.concatenate(all_sensors)
    validity = np.concatenate(all_validity)
    info = {
        "codebase_version": "v2.1",
        "robot_type": "droid",
        "total_episodes": len(episodes),
        "total_frames": frame_offset,
        "fps": dataset_fps,
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "chunks_size": 1000,
        "splits": {"train": f"0:{len(episodes)}"},
        "features": {
            "observation.images.exterior_image_1_left": {
                "dtype": "video",
                "shape": [180, 320, 3],
            },
            "observation.images.wrist_image_left": {
                "dtype": "video",
                "shape": [180, 320, 3],
            },
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
            "exterior_image_1_left": {
                "original_key": "observation.images.exterior_image_1_left"
            },
            "wrist_image_left": {
                "original_key": "observation.images.wrist_image_left"
            },
        },
        "annotation": {"language.language_instruction": {"original_key": "task_index"}},
    }
    (meta_dir / "info.json").write_text(json.dumps(info, indent=2) + "\n")
    (meta_dir / "modality.json").write_text(json.dumps(modality, indent=2) + "\n")
    (meta_dir / "episodes.jsonl").write_text(
        "".join(json.dumps(episode) + "\n" for episode in episodes)
    )
    ordered_tasks = sorted(task_to_index.items(), key=lambda item: item[1])
    (meta_dir / "tasks.jsonl").write_text(
        "".join(
            json.dumps({"task_index": index, "task": task}) + "\n"
            for task, index in ordered_tasks
        )
    )
    (meta_dir / "stats.json").write_text(
        json.dumps(
            {
                "observation.state": stats(state),
                SENSOR_COLUMN: masked_sensor_stats(sensors, validity),
                VALIDITY_COLUMN: stats(validity, unit_std_for_constant=True),
                "action": stats(action),
            },
            indent=2,
        )
        + "\n"
    )
    coverage = {
        spec.name: float(validity[:, index].mean())
        for index, spec in enumerate(SIGNAL_SPECS)
    }
    print(
        f"Converted {len(episodes)} real DROID episodes / {frame_offset} frames to {output}; "
        f"sensor_coverage={coverage}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--trajectory", type=Path)
    parser.add_argument("--exterior-video", type=Path)
    parser.add_argument("--wrist-video", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--instruction", default="Perform the demonstrated manipulation task"
    )
    args = parser.parse_args()
    convert(load_episode_specs(args), args.output.expanduser().resolve())


if __name__ == "__main__":
    main()
