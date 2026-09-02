#!/usr/bin/env python3
"""Publish immutable, model-specific exports from canonical Gemini episodes.

The HDF5 and combined-camera MP4 pairs in ``--input-dir`` are treated as
immutable source artifacts.  This command hashes and validates them, writes a
content-addressed source snapshot, builds a GR00T N1.7 DROID LeRobot-v2.1
projection in a staging directory, runs NVIDIA's relative-statistics tool and
loader, and only then atomically publishes the export.

There is deliberately no overwrite option.  A new source snapshot or transform
configuration receives a new content-addressed export directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import h5py
import numpy as np
import pandas as pd

try:
    from convert_robolab_demo_to_groot import convert_many
    from gemini_episode_dataset import ACTION_SEMANTICS
except ModuleNotFoundError:  # Support ``python -m scripts...``.
    from scripts.convert_robolab_demo_to_groot import convert_many
    from scripts.gemini_episode_dataset import ACTION_SEMANTICS


SOURCE_MANIFEST_SCHEMA = "robolab-canonical-source.v1"
EXPORT_MANIFEST_SCHEMA = "robolab-derived-export.v1"
VALIDATION_SCHEMA = "groot-n17-droid-export-validation.v1"
EXPORTER_VERSION = "groot-n17-droid-v21.1"
TARGET_NAME = "groot-n17-droid-lerobot-v2.1"
EMBODIMENT_TAG = "OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT"
REQUIRED_VIDEO_KEYS = (
    "observation.images.exterior_image_1_left",
    "observation.images.wrist_image_left",
)
REQUIRED_LOW_DIMENSIONS = {
    "observation.state": 17,
    "observation.sensors": 38,
    "observation.sensor_validity": 7,
    "action": 17,
}


@dataclass(frozen=True)
class EpisodePair:
    episode_index: int
    hdf5_path: Path
    video_path: Path
    demo_key: str = "demo_0"


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def content_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def artifact_descriptor(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _json_attr(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _video_descriptor(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open episode video: {path}")
    descriptor = artifact_descriptor(path)
    descriptor.update(
        {
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
            "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        }
    )
    capture.release()
    if descriptor["width"] <= 0 or descriptor["height"] <= 0:
        raise ValueError(f"invalid episode video dimensions: {path}")
    if descriptor["frames"] <= 0 or descriptor["fps"] <= 0:
        raise ValueError(f"invalid episode video timing: {path}")
    if descriptor["width"] % 2:
        raise ValueError(f"combined episode video must have even width: {path}")
    return descriptor


def discover_episode_pairs(input_dir: Path) -> list[EpisodePair]:
    input_dir = input_dir.resolve()
    hdf5_by_index: dict[int, Path] = {}
    for path in input_dir.glob("run_*.hdf5"):
        try:
            index = int(path.stem.split("_")[-1])
        except ValueError as error:
            raise ValueError(f"invalid canonical episode filename: {path.name}") from error
        if index in hdf5_by_index:
            raise ValueError(f"duplicate canonical episode index: {index}")
        hdf5_by_index[index] = path.resolve()
    if not hdf5_by_index:
        raise ValueError(f"no run_*.hdf5 episodes found in {input_dir}")

    pairs: list[EpisodePair] = []
    for index, hdf5_path in sorted(hdf5_by_index.items()):
        video_path = input_dir / f"episode_{index:06d}_policy.mp4"
        if not video_path.is_file():
            raise FileNotFoundError(
                f"canonical episode {index} is missing video {video_path.name}"
            )
        pairs.append(EpisodePair(index, hdf5_path, video_path.resolve()))

    paired_videos = {pair.video_path.name for pair in pairs}
    unpaired = sorted(
        path.name
        for path in input_dir.glob("episode_*_policy.mp4")
        if path.name not in paired_videos
    )
    if unpaired:
        raise ValueError(f"unpaired canonical episode videos: {unpaired}")
    return pairs


def _load_collection_rows(input_dir: Path, pairs: list[EpisodePair]) -> tuple[dict, dict[int, dict]]:
    path = input_dir / "collection_manifest.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"canonical collection manifest is missing: {path}")
    rows: dict[int, dict] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid JSON in {path.name} line {line_number}"
            ) from error
        index = int(row["episode_index"])
        if index in rows:
            raise ValueError(f"duplicate collection-manifest episode index: {index}")
        if row.get("status") != "success":
            raise ValueError(f"episode {index} is not successful in collection manifest")
        rows[index] = row
    pair_indices = {pair.episode_index for pair in pairs}
    if set(rows) != pair_indices:
        raise ValueError(
            "collection manifest and canonical episode pairs disagree: "
            f"manifest={sorted(rows)}, pairs={sorted(pair_indices)}"
        )
    for pair in pairs:
        row = rows[pair.episode_index]
        if row.get("hdf5") != pair.hdf5_path.name:
            raise ValueError(f"episode {pair.episode_index} HDF5 name disagrees with manifest")
        if row.get("video") != pair.video_path.name:
            raise ValueError(f"episode {pair.episode_index} video name disagrees with manifest")
    return artifact_descriptor(path), rows


def _episode_descriptor(pair: EpisodePair, collection_row: dict) -> dict[str, Any]:
    with h5py.File(pair.hdf5_path, "r") as source:
        demo_path = f"data/{pair.demo_key}"
        if demo_path not in source:
            raise ValueError(f"missing {demo_path} in {pair.hdf5_path}")
        demo = source[demo_path]
        if not bool(demo.attrs.get("success", False)):
            raise ValueError(f"canonical episode is not marked successful: {pair.hdf5_path}")
        required = {
            "actions": 8,
            "ee_pose/position": 3,
            "ee_pose/orientation": 4,
            "states/articulation/robot/joint_position": None,
        }
        lengths: set[int] = set()
        for key, width in required.items():
            if key not in demo:
                raise ValueError(f"missing {key} in {pair.hdf5_path}:{demo_path}")
            values = np.asarray(demo[key])
            if values.ndim != 2 or (width is not None and values.shape[1] != width):
                raise ValueError(
                    f"invalid {key} shape in {pair.hdf5_path}: {values.shape}"
                )
            if not np.isfinite(values).all():
                raise ValueError(f"non-finite values in {pair.hdf5_path}:{key}")
            lengths.add(len(values))
        if len(lengths) != 1:
            raise ValueError(f"unaligned canonical arrays in {pair.hdf5_path}: {lengths}")
        samples = lengths.pop()
        if samples < 41:
            raise ValueError(
                f"episode {pair.episode_index} is shorter than N1.7's 40-step horizon"
            )
        declared_samples = int(demo.attrs.get("num_samples", samples))
        if declared_samples != samples:
            raise ValueError(
                f"episode {pair.episode_index} num_samples={declared_samples}, arrays={samples}"
            )
        quaternion_convention = str(_json_attr(demo.attrs.get("quaternion_convention", "")))
        if quaternion_convention != "wxyz":
            raise ValueError(
                f"episode {pair.episode_index} quaternion convention is not wxyz"
            )
        metadata_raw = _json_attr(demo.attrs.get("episode_metadata_json", "{}"))
        metadata = json.loads(str(metadata_raw))
        declared_action_semantics = demo.attrs.get("action_semantics")
        action_semantics = (
            str(_json_attr(declared_action_semantics))
            if declared_action_semantics is not None
            else ACTION_SEMANTICS
        )
        if action_semantics != ACTION_SEMANTICS:
            raise ValueError(
                f"unsupported action semantics in episode {pair.episode_index}: "
                f"{action_semantics}"
            )
        hdf5_descriptor = artifact_descriptor(pair.hdf5_path)
        hdf5_descriptor.update(
            {
                "demo_key": pair.demo_key,
                "samples": samples,
                "success": True,
                "episode_schema_version": str(
                    _json_attr(demo.attrs.get("episode_schema_version", "legacy-unversioned"))
                ),
                "action_semantics": action_semantics,
                "action_semantics_declared_in_source": declared_action_semantics is not None,
                "source_policy": str(_json_attr(demo.attrs.get("source_policy", "unknown"))),
                "quaternion_convention": quaternion_convention,
                "movable_object_asset": str(
                    _json_attr(demo.attrs.get("movable_object_asset", "unknown"))
                ),
                "target_receptacle_asset": str(
                    _json_attr(demo.attrs.get("target_receptacle_asset", "unknown"))
                ),
            }
        )

    video_descriptor = _video_descriptor(pair.video_path)
    if video_descriptor["frames"] != samples:
        raise ValueError(
            f"episode {pair.episode_index} video frames={video_descriptor['frames']}, "
            f"samples={samples}"
        )
    return {
        "episode_index": pair.episode_index,
        "hdf5": hdf5_descriptor,
        "video": video_descriptor,
        "capture": {
            "task": metadata.get("task"),
            "instruction": metadata.get("instruction"),
            "sim_version": metadata.get("sim_version"),
            "movable_object_offset_xy_m": metadata.get("movable_object_offset_xy_m"),
            "movable_object_yaw_deg": metadata.get("movable_object_yaw_deg"),
            "target_receptacle_offset_xy_m": metadata.get(
                "target_receptacle_offset_xy_m"
            ),
            "appearance": metadata.get("appearance"),
        },
        "collection_manifest_row_digest": content_digest(collection_row),
    }


def build_source_manifest(input_dir: Path) -> tuple[dict[str, Any], list[EpisodePair]]:
    input_dir = input_dir.resolve()
    pairs = discover_episode_pairs(input_dir)
    collection_descriptor, rows = _load_collection_rows(input_dir, pairs)
    core = {
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "collection_manifest": collection_descriptor,
        "episodes": [
            _episode_descriptor(pair, rows[pair.episode_index]) for pair in pairs
        ],
    }
    return {"snapshot_id": content_digest(core), **core}, pairs


def write_immutable_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != value:
            raise FileExistsError(f"immutable manifest already exists with different data: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"refusing to replace existing manifest staging file: {temporary}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run_nvidia_stats(
    dataset_path: Path, *, groot_root: Path, groot_python: Path
) -> dict[str, Any]:
    command = [
        str(groot_python),
        "gr00t/data/stats.py",
        "--dataset-path",
        str(dataset_path),
        "--embodiment-tag",
        EMBODIMENT_TAG,
    ]
    result = subprocess.run(
        command,
        cwd=groot_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "NVIDIA statistics tool failed "
            f"(exit {result.returncode}):\n"
            f"stdout:\n{result.stdout.strip()}\n"
            f"stderr:\n{result.stderr.strip()}"
        )
    relative_stats_path = dataset_path / "meta/relative_stats.json"
    if not relative_stats_path.is_file():
        raise RuntimeError("NVIDIA statistics tool did not produce meta/relative_stats.json")
    return {
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _validate_relative_stats(dataset_path: Path) -> dict[str, Any]:
    stats = json.loads((dataset_path / "meta/relative_stats.json").read_text())
    expected = {"eef_9d": (40, 9), "joint_position": (40, 7)}
    result: dict[str, Any] = {}
    for key, shape in expected.items():
        if key not in stats:
            raise ValueError(f"relative stats are missing {key}")
        key_result = {}
        for statistic in ("mean", "std", "min", "max", "q01", "q99"):
            values = np.asarray(stats[key].get(statistic), dtype=np.float64)
            if values.shape != shape:
                raise ValueError(
                    f"relative stats {key}.{statistic} has shape {values.shape}, expected {shape}"
                )
            if not np.isfinite(values).all():
                raise ValueError(f"relative stats {key}.{statistic} contains non-finite values")
            key_result[statistic] = {"shape": list(values.shape), "finite": True}
        result[key] = key_result
    return result


def _validate_structural_export(dataset_path: Path) -> dict[str, Any]:
    meta_dir = dataset_path / "meta"
    required_meta = (
        "info.json",
        "episodes.jsonl",
        "tasks.jsonl",
        "modality.json",
        "stats.json",
        "relative_stats.json",
    )
    missing = [name for name in required_meta if not (meta_dir / name).is_file()]
    if missing:
        raise ValueError(f"export is missing metadata files: {missing}")
    info = json.loads((meta_dir / "info.json").read_text())
    if info.get("codebase_version") != "v2.1":
        raise ValueError(f"unexpected LeRobot version: {info.get('codebase_version')}")
    features = info.get("features", {})
    for key in REQUIRED_VIDEO_KEYS:
        if features.get(key, {}).get("dtype") != "video":
            raise ValueError(f"missing GR00T video feature: {key}")
    for key, width in REQUIRED_LOW_DIMENSIONS.items():
        feature = features.get(key)
        if feature is None or feature.get("shape") != [width]:
            raise ValueError(f"invalid feature contract for {key}: {feature}")

    episodes = [
        json.loads(line)
        for line in (meta_dir / "episodes.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len(episodes) != int(info["total_episodes"]):
        raise ValueError("episode metadata count disagrees with info.json")
    total_rows = 0
    episode_results = []
    for episode in episodes:
        index = int(episode["episode_index"])
        chunk = index // int(info["chunks_size"])
        parquet_path = dataset_path / info["data_path"].format(
            episode_chunk=chunk, episode_index=index
        )
        frame = pd.read_parquet(parquet_path)
        length = int(episode["length"])
        if len(frame) != length:
            raise ValueError(f"episode {index} parquet rows={len(frame)}, expected={length}")
        low_dimensional = {}
        for key, width in REQUIRED_LOW_DIMENSIONS.items():
            values = np.stack(frame[key].to_numpy())
            if values.shape != (length, width):
                raise ValueError(f"episode {index} {key} shape={values.shape}")
            if values.dtype != np.float32 or not np.isfinite(values).all():
                raise ValueError(f"episode {index} {key} is not finite float32")
            low_dimensional[key] = {"shape": list(values.shape), "finite": True}
        timestamps = frame["timestamp"].to_numpy()
        if length > 1 and not np.all(np.diff(timestamps) > 0):
            raise ValueError(f"episode {index} timestamps are not strictly increasing")
        if not np.array_equal(frame["frame_index"].to_numpy(), np.arange(length)):
            raise ValueError(f"episode {index} frame indices are not contiguous")

        videos = {}
        for key in REQUIRED_VIDEO_KEYS:
            video_path = dataset_path / info["video_path"].format(
                episode_chunk=chunk, video_key=key, episode_index=index
            )
            descriptor = _video_descriptor(video_path)
            if descriptor["frames"] != length:
                raise ValueError(
                    f"episode {index} {key} frames={descriptor['frames']}, expected={length}"
                )
            if descriptor["width"] != 320 or descriptor["height"] != 180:
                raise ValueError(f"episode {index} {key} has unexpected dimensions")
            if abs(descriptor["fps"] - float(info["fps"])) > 0.01:
                raise ValueError(f"episode {index} {key} FPS disagrees with info.json")
            videos[key] = descriptor
        total_rows += length
        episode_results.append(
            {
                "episode_index": index,
                "length": length,
                "low_dimensional": low_dimensional,
                "videos": videos,
            }
        )
    if total_rows != int(info["total_frames"]):
        raise ValueError("total Parquet rows disagree with info.json")
    return {
        "episodes": episode_results,
        "total_episodes": len(episodes),
        "total_frames": total_rows,
        "fps": float(info["fps"]),
        "relative_stats": _validate_relative_stats(dataset_path),
    }


def _validate_with_nvidia_loader(dataset_path: Path, groot_root: Path) -> dict[str, Any]:
    groot_root_text = str(groot_root.resolve())
    if groot_root_text not in sys.path:
        sys.path.insert(0, groot_root_text)
    from gr00t.configs.data.embodiment_configs import MODALITY_CONFIGS
    from gr00t.data.dataset.lerobot_episode_loader import LeRobotEpisodeLoader
    from gr00t.data.embodiment_tags import EmbodimentTag

    tag = EmbodimentTag.OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT
    loader = LeRobotEpisodeLoader(dataset_path, MODALITY_CONFIGS[tag.value])
    if len(loader) == 0:
        raise ValueError("NVIDIA loader found no episodes")
    parquet_rows = []
    for index in range(len(loader)):
        frame = loader._load_parquet_data(index)
        parquet_rows.append(len(frame))
    first = loader[0]
    expected_columns = {
        "state.eef_9d",
        "state.gripper_position",
        "state.joint_position",
        "action.eef_9d",
        "action.gripper_position",
        "action.joint_position",
        "video.exterior_image_1_left",
        "video.wrist_image_left",
        "language.annotation.language.language_instruction",
    }
    if not expected_columns.issubset(first.columns):
        raise ValueError(
            f"NVIDIA loader output is missing columns: {sorted(expected_columns - set(first.columns))}"
        )
    statistics = loader.get_dataset_statistics()
    if "relative_action" not in statistics:
        raise ValueError("NVIDIA loader did not admit relative action statistics")
    return {
        "embodiment_tag": tag.value,
        "episodes": len(loader),
        "parquet_rows": parquet_rows,
        "full_multimodal_episode_0_rows": len(first),
        "full_multimodal_episode_0_columns": list(first.columns),
        "dataset_statistics_keys": sorted(statistics),
    }


def _tree_artifacts(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _git_provenance(repo_root: Path) -> dict[str, Any]:
    def command(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    status = command("status", "--porcelain")
    return {"commit": command("rev-parse", "HEAD"), "dirty": bool(status)}


def export_groot_n17(
    *,
    input_dir: Path,
    store_root: Path,
    instruction: str,
    action_mode: str,
    groot_root: Path,
    groot_python: Path,
) -> Path:
    if not instruction.strip():
        raise ValueError("instruction must not be empty")
    input_dir = input_dir.resolve()
    store_root = store_root.resolve()
    groot_root = groot_root.resolve()
    # Do not resolve this symlink: uv/venv Python launchers use their invoked
    # path to discover the virtual environment. Following the link selects the
    # base interpreter and silently drops GR00T's installed dependencies.
    groot_python = Path(os.path.abspath(os.fspath(groot_python.expanduser())))
    if not (groot_root / "gr00t/data/stats.py").is_file():
        raise FileNotFoundError(f"invalid Isaac-GR00T root: {groot_root}")
    if not groot_python.is_file():
        raise FileNotFoundError(f"GR00T Python executable does not exist: {groot_python}")

    source_manifest, pairs = build_source_manifest(input_dir)
    snapshot_id = source_manifest["snapshot_id"]
    source_manifest_path = (
        store_root / "source_manifests" / f"canonical-{snapshot_id}.json"
    )
    write_immutable_json(source_manifest_path, source_manifest)

    transform = {
        "exporter_version": EXPORTER_VERSION,
        "target": TARGET_NAME,
        "embodiment_tag": EMBODIMENT_TAG,
        "instruction": instruction,
        "action_mode": action_mode,
        "source_snapshot_id": snapshot_id,
        "source_action_semantics": ACTION_SEMANTICS,
        "derived_action_semantics": {
            "eef_9d": "next_observed_pose_then_relative_to_current_state",
            "gripper_position": "recorded_absolute_binary_target",
            "joint_position": "next_observed_position_then_relative_to_current_state",
        },
    }
    transform_id = content_digest(transform)
    target = (
        store_root
        / "exports"
        / TARGET_NAME
        / f"{snapshot_id[:16]}-{transform_id[:16]}"
    )
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing immutable export: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent))

    try:
        converter_pairs = [
            (pair.hdf5_path, pair.video_path, pair.demo_key) for pair in pairs
        ]
        convert_many(converter_pairs, staging, instruction, action_mode)
        stats_run = _run_nvidia_stats(
            staging, groot_root=groot_root, groot_python=groot_python
        )
        structural = _validate_structural_export(staging)
        nvidia_loader = _validate_with_nvidia_loader(staging, groot_root)

        source_after, _ = build_source_manifest(input_dir)
        originals_unchanged = source_after == source_manifest
        if not originals_unchanged:
            raise RuntimeError("canonical source artifacts changed during export")

        repo_root = Path(__file__).resolve().parents[1]
        validation_report = {
            "schema_version": VALIDATION_SCHEMA,
            "passed": True,
            "source_snapshot_id": snapshot_id,
            "canonical_originals_unchanged": originals_unchanged,
            "structural": structural,
            "nvidia_loader": nvidia_loader,
            "nvidia_stats": stats_run,
        }
        (staging / "validation_report.json").write_text(
            json.dumps(validation_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts = _tree_artifacts(staging)
        export_manifest = {
            "schema_version": EXPORT_MANIFEST_SCHEMA,
            "export_id": transform_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_snapshot_id": snapshot_id,
            "source_manifest": {
                "path": str(source_manifest_path.relative_to(store_root)),
                "sha256": sha256_file(source_manifest_path),
            },
            "source_root_hint": str(input_dir),
            "transform": transform,
            "tooling": {
                "repository": _git_provenance(repo_root),
                "exporter": {
                    "path": str(Path(__file__).resolve().relative_to(repo_root)),
                    "sha256": sha256_file(Path(__file__).resolve()),
                },
                "converter": {
                    "path": "scripts/convert_robolab_demo_to_groot.py",
                    "sha256": sha256_file(
                        repo_root / "scripts/convert_robolab_demo_to_groot.py"
                    ),
                },
                "isaac_groot_root_hint": str(groot_root),
            },
            "validation": {
                "passed": True,
                "report": "validation_report.json",
            },
            "artifacts": artifacts,
        }
        (staging / "export_manifest.json").write_text(
            json.dumps(export_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--store-root", required=True, type=Path)
    parser.add_argument("--groot-root", required=True, type=Path)
    parser.add_argument("--groot-python", type=Path)
    parser.add_argument(
        "--instruction", required=True, help="Language instruction stored in the model export"
    )
    parser.add_argument(
        "--action-mode", choices=("absolute_ik", "joint"), default="absolute_ik"
    )
    args = parser.parse_args()
    groot_python = args.groot_python or args.groot_root / ".venv/bin/python"
    output = export_groot_n17(
        input_dir=args.input_dir,
        store_root=args.store_root,
        instruction=args.instruction,
        action_mode=args.action_mode,
        groot_root=args.groot_root,
        groot_python=groot_python,
    )
    print(
        json.dumps(
            {"status": "published", "export": str(output)},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
