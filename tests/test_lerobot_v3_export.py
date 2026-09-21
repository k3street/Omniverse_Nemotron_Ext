"""
L0 tests for the LeRobot v3.0 export layout.

Frame content comes from the shared v2.1 code path; what is new here is the
v3.0 metadata surface, whose paths and column names readers depend on.
Loading a real dataset requires Isaac-recorded HDF5 plus lerobot, so these
tests pin the contract that can be checked without them.
"""
import pytest

pytestmark = pytest.mark.l0

from scripts.convert_robolab_demo_to_lerobot_v3 import (
    CODEBASE_VERSION,
    DATA_PATH,
    DEFAULT_CHUNK_SIZE,
    EPISODES_PATH,
    VIDEO_PATH,
    _feature_stats,
    _flatten_stats,
    update_chunk_file_indices,
)


def test_path_templates_match_lerobot_v3():
    # lerobot/datasets/utils.py: these exact templates are written into
    # info.json and used by readers to locate files.
    assert DATA_PATH == "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
    assert VIDEO_PATH == (
        "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    assert EPISODES_PATH == (
        "meta/episodes/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
    )
    assert CODEBASE_VERSION == "v3.0"
    # v2.1 put the camera key after the chunk directory; v3.0 puts it before.
    assert VIDEO_PATH.index("{video_key}") < VIDEO_PATH.index("chunk-")


def test_chunk_file_rollover_matches_lerobot():
    assert update_chunk_file_indices(0, 0) == (0, 1)
    assert update_chunk_file_indices(0, DEFAULT_CHUNK_SIZE - 2) == (
        0,
        DEFAULT_CHUNK_SIZE - 1,
    )
    # The last file in a chunk rolls over to the next chunk, not file 1000.
    assert update_chunk_file_indices(0, DEFAULT_CHUNK_SIZE - 1) == (1, 0)
    assert update_chunk_file_indices(3, DEFAULT_CHUNK_SIZE - 1) == (4, 0)


def test_feature_stats_shape_and_quantiles():
    import numpy as np

    values = np.arange(40, dtype=np.float32).reshape(10, 4)
    result = _feature_stats(values)
    assert set(result) == {
        "min", "max", "mean", "std", "count",
        "q01", "q10", "q50", "q90", "q99",
    }
    assert result["count"] == [10]
    for key in ("min", "max", "mean", "std", "q50"):
        assert len(result[key]) == 4, key
    assert result["min"] == [0.0, 1.0, 2.0, 3.0]
    assert result["max"] == [36.0, 37.0, 38.0, 39.0]


def test_feature_stats_accepts_one_dimensional_input():
    import numpy as np

    result = _feature_stats(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert result["count"] == [3]
    assert result["mean"] == [2.0]


def test_stats_are_flattened_into_episode_columns():
    flattened = _flatten_stats({"action": {"mean": [1.0], "count": [2]}})
    # lerobot stores per-episode stats as stats/<feature>/<statistic> columns
    # and drops them on load, so the prefix must be exact.
    assert flattened == {"stats/action/mean": [1.0], "stats/action/count": [2]}
