#!/usr/bin/env python3
"""Convert RoboLab DROID demos to a LeRobot **v3.0** dataset.

``convert_robolab_demo_to_groot`` writes the v2.1 layout that GR00T N1.7
consumes. Post-training Cosmos 3 with ``cosmos-framework`` needs v3.0, whose
layout differs in every metadata surface:

===============  ==========================================  ==============================================
surface          v2.1                                        v3.0
===============  ==========================================  ==============================================
data             ``data/chunk-000/episode_000000.parquet``   ``data/chunk-000/file-000.parquet``
video            ``videos/chunk-000/<key>/episode_*.mp4``    ``videos/<key>/chunk-000/file-000.mp4``
episode index    ``meta/episodes.jsonl``                     ``meta/episodes/chunk-000/file-000.parquet``
tasks            ``meta/tasks.jsonl``                        ``meta/tasks.parquet`` (index = task string)
===============  ==========================================  ==============================================

v3.0 packs several episodes per data/video file and records each episode's
span with ``dataset_from_index``/``dataset_to_index`` and per-video
``from_timestamp``/``to_timestamp``. Readers follow those pointers rather than
assuming a file holds exactly one episode, so this writer emits **one episode
per file** and sets the spans accordingly: the layout is spec-compliant and
the episode videos stay byte-identical to what the camera split produces, with
no re-encoding to concatenate them.

The frame content, action semantics, sensor block, and ``modality.json`` are
produced by the shared v2.1 code path, so both exports describe the same
episodes.

Two constraints matter when feeding ``cosmos-framework``'s
``DROIDLeRobotDataset``, both verified by loading an export through it:

* It derives its feature mapping from ``os.path.basename(root)`` and rejects
  any name outside its ``LEROBOT_ROOTS`` table. Only
  ``droid_lerobot_20260115_no_noops`` expects the feature names written here
  (``observation.state``, ``action``, ``observation.images.*``), so the export
  directory must carry that name to be consumed.
* It reads three cameras -- wrist, ``exterior_image_1_left`` and
  ``exterior_image_2_left``. A recording with the two-camera combined video
  produces the first two, which satisfies ``viewpoint="wrist_view"`` but not
  ``"concat_view"`` or ``"third_person_view"``; those additionally query the
  right camera and fail on the missing column. Recording the right
  over-shoulder view (the ``WRIST_LEFT_RIGHT`` preset) is what unlocks them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

try:
    from franka_sensor_schema import (
        SENSOR_COLUMN,
        SENSOR_DIM,
        SENSOR_SCHEMA_VERSION,
        SIGNAL_SPECS,
        VALIDITY_COLUMN,
        VALIDITY_DIM,
        masked_sensor_stats,
        sensor_modality_metadata,
    )
except ModuleNotFoundError:  # Support ``python -m scripts...``.
    from scripts.franka_sensor_schema import (
        SENSOR_COLUMN,
        SENSOR_DIM,
        SENSOR_SCHEMA_VERSION,
        SIGNAL_SPECS,
        VALIDITY_COLUMN,
        VALIDITY_DIM,
        masked_sensor_stats,
        sensor_modality_metadata,
    )

CODEBASE_VERSION = "v3.0"
# lerobot/datasets/utils.py path templates and defaults.
DATA_PATH = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
EPISODES_PATH = "meta/episodes/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_DATA_FILE_SIZE_IN_MB = 100
DEFAULT_VIDEO_FILE_SIZE_IN_MB = 200
QUANTILES = (0.01, 0.10, 0.50, 0.90, 0.99)

EXTERIOR_VIDEO_KEY = "observation.images.exterior_image_1_left"
WRIST_VIDEO_KEY = "observation.images.wrist_image_left"
VIDEO_KEYS = (EXTERIOR_VIDEO_KEY, WRIST_VIDEO_KEY)


def update_chunk_file_indices(
    chunk_index: int, file_index: int, chunks_size: int = DEFAULT_CHUNK_SIZE
) -> tuple[int, int]:
    """Advance (chunk, file) exactly as lerobot's helper of the same name."""
    if file_index == chunks_size - 1:
        return chunk_index + 1, 0
    return chunk_index, file_index + 1


def _feature_stats(values: np.ndarray) -> dict[str, list]:
    """Per-feature statistics in lerobot's episode-stats shape."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, None]
    result = {
        "min": np.min(array, axis=0).tolist(),
        "max": np.max(array, axis=0).tolist(),
        "mean": np.mean(array, axis=0).tolist(),
        "std": np.std(array, axis=0).tolist(),
        "count": [int(array.shape[0])],
    }
    for quantile in QUANTILES:
        result[f"q{int(quantile * 100):02d}"] = np.quantile(
            array, quantile, axis=0
        ).tolist()
    return result


def _flatten_stats(episode_stats: dict[str, dict[str, list]]) -> dict[str, list]:
    return {
        f"stats/{feature}/{statistic}": value
        for feature, feature_stats in episode_stats.items()
        for statistic, value in feature_stats.items()
    }


def _features(state_dim: int, action_dim: int, height: int, width: int) -> dict:
    video_shape = [height, width, 3]
    video_names = ["height", "width", "channels"]
    features = {
        EXTERIOR_VIDEO_KEY: {
            "dtype": "video", "shape": video_shape, "names": video_names,
        },
        WRIST_VIDEO_KEY: {
            "dtype": "video", "shape": video_shape, "names": video_names,
        },
        "observation.state": {
            "dtype": "float32", "shape": [state_dim], "names": None,
        },
        SENSOR_COLUMN: {"dtype": "float32", "shape": [SENSOR_DIM], "names": None},
        VALIDITY_COLUMN: {
            "dtype": "float32", "shape": [VALIDITY_DIM], "names": None,
        },
        "action": {"dtype": "float32", "shape": [action_dim], "names": None},
    }
    # lerobot merges these into every dataset; readers rely on them.
    features.update(
        {
            "timestamp": {"dtype": "float32", "shape": [1], "names": None},
            "frame_index": {"dtype": "int64", "shape": [1], "names": None},
            "episode_index": {"dtype": "int64", "shape": [1], "names": None},
            "index": {"dtype": "int64", "shape": [1], "names": None},
            "task_index": {"dtype": "int64", "shape": [1], "names": None},
        }
    )
    return features


def convert_many_v3(
    episodes: list[tuple[Path, Path, str]],
    output: Path,
    instruction: str,
    action_mode: str,
) -> None:
    import pandas as pd

    # Imported here so the path templates and statistics helpers above stay
    # importable without the recording stack (h5py, cv2).
    try:
        from convert_robolab_demo_to_groot import (
            episode_provenance,
            read_episode,
            stats,
            write_video_half,
        )
    except ModuleNotFoundError:  # Support ``python -m scripts...``.
        from scripts.convert_robolab_demo_to_groot import (
            episode_provenance,
            read_episode,
            stats,
            write_video_half,
        )

    if not episodes:
        raise ValueError("No HDF5/video episode pairs were found")

    output = Path(output)
    meta_dir = output / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    all_states: list[np.ndarray] = []
    all_actions: list[np.ndarray] = []
    all_sensors: list[np.ndarray] = []
    all_sensor_validity: list[np.ndarray] = []
    episode_rows: list[dict] = []
    global_index = 0
    dataset_fps: float | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    chunk_index, file_index = 0, 0

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
        half = width // 2
        if frame_width is None:
            frame_width, frame_height = half, height

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
                "index": np.arange(
                    global_index, global_index + length, dtype=np.int64
                ),
                "task_index": np.zeros(length, dtype=np.int64),
            }
        )
        data_file = output / DATA_PATH.format(
            chunk_index=chunk_index, file_index=file_index
        )
        data_file.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(data_file, index=False)

        for video_key, x_offset in (
            (EXTERIOR_VIDEO_KEY, 0),
            (WRIST_VIDEO_KEY, half),
        ):
            destination = output / VIDEO_PATH.format(
                video_key=video_key,
                chunk_index=chunk_index,
                file_index=file_index,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_video_half(
                video_path, destination, x=x_offset, width=half, height=height
            )

        episode_stats = {
            "observation.state": _feature_stats(observation_state),
            SENSOR_COLUMN: _feature_stats(sensor_values),
            VALIDITY_COLUMN: _feature_stats(sensor_validity),
            "action": _feature_stats(action),
        }
        row = {
            "episode_index": episode_index,
            "tasks": [instruction],
            "length": length,
            "data/chunk_index": chunk_index,
            "data/file_index": file_index,
            "dataset_from_index": global_index,
            "dataset_to_index": global_index + length,
            # One episode per file, so each episode spans its whole video.
            **{
                f"videos/{video_key}/{field}": value
                for video_key in VIDEO_KEYS
                for field, value in (
                    ("chunk_index", chunk_index),
                    ("file_index", file_index),
                    ("from_timestamp", 0.0),
                    ("to_timestamp", length / fps),
                )
            },
            "meta/episodes/chunk_index": 0,
            "meta/episodes/file_index": 0,
            "sensor_schema_version": SENSOR_SCHEMA_VERSION,
            "sensor_coverage": json.dumps(sensor_coverage),
            "sensor_source_paths": json.dumps(sensor_source_paths),
            **{
                key: json.dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in provenance.items()
            },
            **_flatten_stats(episode_stats),
        }
        episode_rows.append(row)

        all_states.append(observation_state)
        all_actions.append(action)
        all_sensors.append(sensor_values)
        all_sensor_validity.append(sensor_validity)
        global_index += length
        chunk_index, file_index = update_chunk_file_indices(
            chunk_index, file_index
        )

    assert dataset_fps is not None and frame_width is not None
    observation_state = np.concatenate(all_states)
    action = np.concatenate(all_actions)
    sensor_values = np.concatenate(all_sensors)
    sensor_validity = np.concatenate(all_sensor_validity)

    episodes_file = meta_dir / "episodes" / "chunk-000" / "file-000.parquet"
    episodes_file.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(episode_rows).to_parquet(episodes_file, index=False)

    # tasks.parquet is indexed by the task string; lookups use .loc[task].
    pd.DataFrame({"task_index": [0]}, index=[instruction]).to_parquet(
        meta_dir / "tasks.parquet"
    )

    info = {
        "codebase_version": CODEBASE_VERSION,
        "robot_type": "droid",
        "total_episodes": len(episodes),
        "total_frames": global_index,
        "total_tasks": 1,
        "chunks_size": DEFAULT_CHUNK_SIZE,
        "data_files_size_in_mb": DEFAULT_DATA_FILE_SIZE_IN_MB,
        "video_files_size_in_mb": DEFAULT_VIDEO_FILE_SIZE_IN_MB,
        "fps": int(round(dataset_fps)),
        "splits": {"train": f"0:{len(episodes)}"},
        "data_path": DATA_PATH,
        "video_path": VIDEO_PATH,
        "features": _features(
            observation_state.shape[1],
            action.shape[1],
            frame_height,
            frame_width,
        ),
        "sensor_schema": {
            "version": SENSOR_SCHEMA_VERSION,
            "missing_policy": "zero_fill_with_validity_mask",
            "signals": [
                {"name": spec.name, "width": spec.width} for spec in SIGNAL_SPECS
            ],
        },
    }
    (meta_dir / "info.json").write_text(json.dumps(info, indent=4) + "\n")

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
            "exterior_image_1_left": {"original_key": EXTERIOR_VIDEO_KEY},
            "wrist_image_left": {"original_key": WRIST_VIDEO_KEY},
        },
        "annotation": {
            "language.language_instruction": {"original_key": "task_index"},
        },
    }
    (meta_dir / "modality.json").write_text(json.dumps(modality, indent=2) + "\n")

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
    print(
        f"Converted {len(episodes)} episodes / {global_index} frames "
        f"to LeRobot {CODEBASE_VERSION} at {output}"
    )


def discover_episodes(input_dir: Path) -> list[tuple[Path, Path, str]]:
    """Pair recorded episodes with their policy videos, as the v2.1 CLI does."""
    import h5py

    episodes: list[tuple[Path, Path, str]] = []
    combined = input_dir / "data.hdf5"
    if combined.exists():
        with h5py.File(combined, "r") as source:
            demo_keys = sorted(
                source["data"], key=lambda key: int(key.split("_")[-1])
            )
        for demo_key in demo_keys:
            index = int(demo_key.split("_")[-1])
            video_path = input_dir / f"episode_{index:06d}_policy.mp4"
            if not video_path.exists():
                raise FileNotFoundError(
                    f"Missing video for {demo_key}: {video_path}"
                )
            episodes.append((combined, video_path, demo_key))
        return episodes
    for hdf5_path in sorted(
        input_dir.glob("run_*.hdf5"), key=lambda p: int(p.stem.split("_")[-1])
    ):
        index = int(hdf5_path.stem.split("_")[-1])
        video_path = input_dir / f"episode_{index:06d}_policy.mp4"
        if not video_path.exists():
            raise FileNotFoundError(
                f"Missing video for {hdf5_path.name}: {video_path}"
            )
        episodes.append((hdf5_path, video_path, "demo_0"))
    return episodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument(
        "--action-mode", choices=("absolute_ik", "joint"), default="absolute_ik"
    )
    args = parser.parse_args()

    convert_many_v3(
        discover_episodes(args.input_dir.resolve()),
        args.output.resolve(),
        args.instruction,
        args.action_mode,
    )


if __name__ == "__main__":
    main()
