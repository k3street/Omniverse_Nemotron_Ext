import json
from pathlib import Path

import cv2
import h5py
import numpy as np
import pytest

from scripts.export_gemini_episode_dataset import (
    SOURCE_MANIFEST_SCHEMA,
    build_source_manifest,
    content_digest,
    discover_episode_pairs,
    sha256_file,
    write_immutable_json,
)
from scripts.gemini_episode_dataset import ACTION_SEMANTICS


def _write_video(path: Path, frames: int = 41) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (16, 8)
    )
    assert writer.isOpened()
    for index in range(frames):
        writer.write(np.full((8, 16, 3), index, dtype=np.uint8))
    writer.release()


def _write_legacy_episode(root: Path) -> None:
    hdf5_path = root / "run_0.hdf5"
    with h5py.File(hdf5_path, "w") as target:
        demo = target.create_group("data/demo_0")
        demo.attrs["success"] = True
        demo.attrs["num_samples"] = 41
        demo.attrs["quaternion_convention"] = "wxyz"
        demo.attrs["source_policy"] = "gemini_robotics_test"
        demo.attrs["movable_object_asset"] = "blue_cube"
        demo.attrs["target_receptacle_asset"] = "bin"
        demo.attrs["episode_metadata_json"] = json.dumps(
            {"task": "CleanTableTask", "instruction": "Put the cube in the bin"}
        )
        demo.create_dataset("actions", data=np.zeros((41, 8), dtype=np.float32))
        demo.create_dataset(
            "states/articulation/robot/joint_position",
            data=np.zeros((41, 8), dtype=np.float32),
        )
        demo.create_dataset(
            "ee_pose/position", data=np.zeros((41, 3), dtype=np.float32)
        )
        orientation = np.zeros((41, 4), dtype=np.float32)
        orientation[:, 0] = 1.0
        demo.create_dataset("ee_pose/orientation", data=orientation)

    video_path = root / "episode_000000_policy.mp4"
    _write_video(video_path)
    row = {
        "episode_index": 0,
        "status": "success",
        "hdf5": hdf5_path.name,
        "video": video_path.name,
        "samples": 41,
    }
    (root / "collection_manifest.jsonl").write_text(
        json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_source_manifest_is_deterministic_and_hashes_canonical_artifacts(tmp_path):
    _write_legacy_episode(tmp_path)
    before = {
        path.name: sha256_file(path)
        for path in sorted(tmp_path.iterdir())
        if path.is_file()
    }

    first, pairs = build_source_manifest(tmp_path)
    second, _ = build_source_manifest(tmp_path)

    assert first == second
    assert first["schema_version"] == SOURCE_MANIFEST_SCHEMA
    assert first["snapshot_id"] == content_digest(
        {key: value for key, value in first.items() if key != "snapshot_id"}
    )
    assert [pair.episode_index for pair in pairs] == [0]
    episode = first["episodes"][0]
    assert episode["hdf5"]["sha256"] == before["run_0.hdf5"]
    assert episode["video"]["sha256"] == before["episode_000000_policy.mp4"]
    assert episode["hdf5"]["action_semantics"] == ACTION_SEMANTICS
    assert not episode["hdf5"]["action_semantics_declared_in_source"]
    assert before == {
        path.name: sha256_file(path)
        for path in sorted(tmp_path.iterdir())
        if path.is_file()
    }


def test_immutable_manifest_accepts_identical_content_and_refuses_replacement(tmp_path):
    path = tmp_path / "manifest.json"
    write_immutable_json(path, {"snapshot": "one"})
    write_immutable_json(path, {"snapshot": "one"})

    with pytest.raises(FileExistsError, match="immutable manifest"):
        write_immutable_json(path, {"snapshot": "two"})

    assert json.loads(path.read_text()) == {"snapshot": "one"}


def test_pair_discovery_rejects_unpaired_video(tmp_path):
    (tmp_path / "run_0.hdf5").write_bytes(b"placeholder")
    (tmp_path / "episode_000000_policy.mp4").write_bytes(b"placeholder")
    (tmp_path / "episode_000001_policy.mp4").write_bytes(b"unpaired")

    with pytest.raises(ValueError, match="unpaired canonical episode videos"):
        discover_episode_pairs(tmp_path)
