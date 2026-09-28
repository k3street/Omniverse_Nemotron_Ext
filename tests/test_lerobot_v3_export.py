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
    GRIPPER_ACTION_COLUMN,
    GRIPPER_SLICE,
    GRIPPER_STATE_COLUMN,
    JOINT_ACTION_COLUMN,
    JOINT_POSITION_DIM,
    JOINT_POSITION_SLICE,
    JOINT_STATE_COLUMN,
    VIDEO_PATH,
    _feature_stats,
    _features,
    _flatten_stats,
    _modality_slices,
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


def test_joint_pos_columns_are_declared_for_cosmos():
    # cosmos-framework's action_space="joint_pos" reads these four columns by
    # name; the packed vectors alone do not drive that branch.
    features = _features(state_dim=17, action_dim=17, height=360, width=640)
    for column in (JOINT_ACTION_COLUMN, JOINT_STATE_COLUMN):
        assert features[column]["shape"] == [JOINT_POSITION_DIM], column
    for column in (GRIPPER_ACTION_COLUMN, GRIPPER_STATE_COLUMN):
        assert features[column]["shape"] == [1], column
    # The packed layout stays, so one export serves both readers.
    assert features["action"]["shape"] == [17]
    assert features["observation.state"]["shape"] == [17]


def test_modality_slices_cover_the_packed_vector():
    slices = _modality_slices()
    assert slices["joint_position"] == {
        "start": JOINT_POSITION_SLICE.start, "end": JOINT_POSITION_SLICE.stop,
    }
    assert slices["gripper_position"] == {
        "start": GRIPPER_SLICE.start, "end": GRIPPER_SLICE.stop,
    }
    # Contiguous and exactly the 17 dimensions the vectors carry.
    bounds = sorted((v["start"], v["end"]) for v in slices.values())
    assert bounds[0][0] == 0
    assert bounds[-1][1] == 17
    for (_, end), (start, _) in zip(bounds, bounds[1:]):
        assert end == start
