"""Merge several LeRobot v3.0 datasets into one multi-task dataset.

The Cosmos post-training recipe reads a single dataset root. Training on
demonstrations from several tasks therefore means either editing the
experiment's dataloader on the training machine -- where every mistake costs
rented GPU time -- or handing it one root that already contains every task.
This does the latter.

Merging is mostly reindexing, and every layer has to agree:

* frames carry ``episode_index``, a global ``index`` and a ``task_index``;
* episode rows carry their own index, the data/video file they live in, and
  the ``dataset_from_index``/``dataset_to_index`` frame span;
* ``tasks.parquet`` maps each instruction to the ``task_index`` frames cite.

Per-episode stats travel with their episode untouched. Dataset-level stats are
recomputed from the merged frames rather than averaged, because quantiles
cannot be combined from summaries.

    python scripts/merge_lerobot_v3_datasets.py \\
        --input <root-a> <root-b> --output <merged-root>
"""
import argparse
import json
import shutil
from pathlib import Path

# numpy, pandas and pyarrow are imported where they are used so the pure
# reindexing helpers below stay importable -- and testable -- without the
# dataset stack installed.

TASK_COLUMN = "__index_level_0__"


def assign_task_indices(task_lists) -> dict[str, int]:
    """Number the instructions, first appearance first.

    Order is stable across runs so a merge is reproducible, and duplicates
    collapse: two datasets sharing an instruction must share its index, or
    frames from one of them cite a task that describes the other.
    """
    indices: dict[str, int] = {}
    for tasks in task_lists:
        for name in tasks:
            indices.setdefault(name, len(indices))
    return indices


def episode_spans(lengths) -> list[tuple[int, int]]:
    """Half-open [from, to) frame spans laid end to end.

    Readers slice frames by these rather than by counting files, so a gap or
    an overlap silently hands an episode somebody else's frames.
    """
    spans, start = [], 0
    for length in lengths:
        spans.append((start, start + length))
        start += length
    return spans


def read_meta(root: Path) -> tuple[dict, dict, list[dict]]:
    import pyarrow.parquet as pq

    info = json.loads((root / "meta" / "info.json").read_text())
    tasks = pq.read_table(root / "meta" / "tasks.parquet").to_pydict()
    names = tasks[TASK_COLUMN]
    by_index = {i: n for i, n in zip(tasks["task_index"], names)}
    rows = []
    for path in sorted((root / "meta" / "episodes").rglob("*.parquet")):
        table = pq.read_table(path).to_pylist()
        rows.extend(table)
    rows.sort(key=lambda r: r["episode_index"])
    return info, by_index, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    import numpy as np
    import pandas as pd
    import pyarrow as pa  # noqa: F401  (schema reuse below)
    import pyarrow.parquet as pq

    from convert_robolab_demo_to_lerobot_v3 import (
        CODEBASE_VERSION,
        DATA_PATH,
        EPISODES_PATH,
        VIDEO_PATH,
        _feature_stats,
        update_chunk_file_indices,
    )

    sources = [(root, *read_meta(root)) for root in args.input]

    reference = sources[0][1]
    for root, info, _, _ in sources[1:]:
        if info["features"] != reference["features"]:
            raise ValueError(f"{root} has different features to {args.input[0]}")
        if info["fps"] != reference["fps"]:
            raise ValueError(f"{root} runs at {info['fps']} fps, not {reference['fps']}")

    video_keys = [k for k, v in reference["features"].items() if v.get("dtype") == "video"]

    out = args.output
    if out.exists():
        shutil.rmtree(out)
    (out / "meta" / "episodes").mkdir(parents=True)

    # Stable task ordering: first appearance across the inputs, in the order given.
    task_index = assign_task_indices(
        row["tasks"] for _, _, _, rows in sources for row in rows
    )

    episode_rows: list[dict] = []
    frame_total = 0
    episode_total = 0
    data_chunk = data_file = 0
    ep_chunk = ep_file = 0
    video_counters = {key: [0, 0] for key in video_keys}
    frames_by_feature: dict[str, list[np.ndarray]] = {}

    for root, info, _, rows in sources:
        for row in rows:
            source_data = root / DATA_PATH.format(
                chunk_index=row["data/chunk_index"], file_index=row["data/file_index"]
            )
            table = pq.read_table(source_data)
            columns = table.to_pydict()
            length = len(columns["frame_index"])

            # Reindex the frames onto the merged timeline.
            columns["episode_index"] = [episode_total] * length
            columns["index"] = list(range(frame_total, frame_total + length))
            columns["task_index"] = [task_index[row["tasks"][0]]] * length

            merged = dict(row)
            merged["episode_index"] = episode_total
            merged["dataset_from_index"] = frame_total
            merged["dataset_to_index"] = frame_total + length
            merged["data/chunk_index"], merged["data/file_index"] = data_chunk, data_file

            destination = out / DATA_PATH.format(chunk_index=data_chunk, file_index=data_file)
            destination.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pydict(columns, schema=table.schema), destination)
            data_chunk, data_file = update_chunk_file_indices(data_chunk, data_file)

            for key in video_keys:
                chunk, index = video_counters[key]
                source_video = root / VIDEO_PATH.format(
                    video_key=key,
                    chunk_index=row[f"videos/{key}/chunk_index"],
                    file_index=row[f"videos/{key}/file_index"],
                )
                target = out / VIDEO_PATH.format(
                    video_key=key, chunk_index=chunk, file_index=index
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_video, target)
                merged[f"videos/{key}/chunk_index"] = chunk
                merged[f"videos/{key}/file_index"] = index
                video_counters[key] = list(update_chunk_file_indices(chunk, index))

            merged["meta/episodes/chunk_index"] = ep_chunk
            merged["meta/episodes/file_index"] = ep_file
            episode_rows.append(merged)

            for feature, spec in reference["features"].items():
                if spec.get("dtype") == "video" or feature not in columns:
                    continue
                if not isinstance(columns[feature][0], (list, tuple)):
                    continue
                frames_by_feature.setdefault(feature, []).append(
                    np.asarray(columns[feature], dtype=np.float32)
                )

            frame_total += length
            episode_total += 1
            ep_chunk, ep_file = update_chunk_file_indices(ep_chunk, ep_file)

    episodes_path = out / EPISODES_PATH.format(chunk_index=0, file_index=0)
    episodes_path.parent.mkdir(parents=True, exist_ok=True)
    for row in episode_rows:
        row["meta/episodes/chunk_index"] = 0
        row["meta/episodes/file_index"] = 0
    pd.DataFrame(episode_rows).to_parquet(episodes_path, index=False)

    ordered = sorted(task_index.items(), key=lambda kv: kv[1])
    # tasks.parquet is INDEXED BY the task string -- readers resolve a frame's
    # task_index through .loc[task]. Writing the string as an ordinary column
    # instead loses the pandas index metadata, and every sample then reports
    # its task as the bare index number.
    pd.DataFrame(
        {"task_index": [i for _, i in ordered]},
        index=[name for name, _ in ordered],
    ).to_parquet(out / "meta" / "tasks.parquet")

    info = dict(reference)
    info["codebase_version"] = CODEBASE_VERSION
    info["total_episodes"] = episode_total
    info["total_frames"] = frame_total
    info["total_tasks"] = len(task_index)
    info["splits"] = {"train": f"0:{episode_total}"}
    (out / "meta" / "info.json").write_text(json.dumps(info, indent=4) + "\n")

    shutil.copy2(args.input[0] / "meta" / "modality.json", out / "meta" / "modality.json")
    (out / "meta" / "stats.json").write_text(
        json.dumps(
            {k: _feature_stats(np.concatenate(v)) for k, v in frames_by_feature.items()},
            indent=2,
        )
        + "\n"
    )

    print(
        f"Merged {len(args.input)} datasets -> {episode_total} episodes / "
        f"{frame_total} frames / {len(task_index)} tasks at {out}"
    )
    for name, index in ordered:
        print(f"  task {index}: {name}")


if __name__ == "__main__":
    main()
