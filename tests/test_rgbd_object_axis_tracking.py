from __future__ import annotations

import numpy as np
import pytest

from scripts.rgbd_object_axis_tracking import (
    estimate_masked_object_axis,
    instance_mask_for_prim_label,
    sign_invariant_axis_error_deg,
)


def _intrinsics() -> np.ndarray:
    return np.array(
        [[100.0, 0.0, 40.0], [0.0, 100.0, 40.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def test_masked_depth_recovers_major_axis_rotation():
    depth = np.ones((80, 80), dtype=np.float32)
    horizontal = np.zeros_like(depth, dtype=bool)
    horizontal[37:44, 12:69] = True
    vertical = np.zeros_like(depth, dtype=bool)
    vertical[12:69, 37:44] = True
    first = estimate_masked_object_axis(
        depth, horizontal, _intrinsics(), stride=1
    )
    second = estimate_masked_object_axis(
        depth, vertical, _intrinsics(), stride=1
    )
    error = sign_invariant_axis_error_deg(
        first.major_axis_camera, second.major_axis_camera
    )
    assert error == pytest.approx(90.0, abs=0.5)
    assert first.point_count > 300


def test_axis_error_is_sign_invariant_for_symmetric_objects():
    assert sign_invariant_axis_error_deg((1, 0, 0), (-1, 0, 0)) == pytest.approx(0.0)
    assert sign_invariant_axis_error_deg((1, 0, 0), (0, 1, 0)) == pytest.approx(90.0)


def test_instance_label_mapping_selects_all_matching_object_prims():
    ids = np.array([[0, 3, 4], [0, 4, 7]], dtype=np.int32)
    mask, info = instance_mask_for_prim_label(
        ids,
        {
            "idToLabels": {
                "0": "INVALID",
                "3": "/World/envs/env_0/scene/banana/mesh_a",
                "4": "/World/envs/env_0/scene/banana/mesh_b",
                "7": "/World/envs/env_0/scene/plate_large",
            }
        },
        "/scene/banana",
    )
    assert np.array_equal(
        mask,
        np.array([[False, True, True], [False, True, False]]),
    )
    assert info["matched_instance_ids"] == [3, 4]


def test_missing_or_ambiguous_visual_evidence_fails_closed():
    depth = np.ones((20, 20), dtype=np.float32)
    with pytest.raises(ValueError, match="not visible"):
        instance_mask_for_prim_label(
            np.zeros((20, 20), dtype=np.int32),
            {"idToLabels": {"2": "/scene/banana"}},
            "/scene/banana",
        )
    square = np.zeros_like(depth, dtype=bool)
    square[5:15, 5:15] = True
    with pytest.raises(ValueError, match="ambiguous"):
        estimate_masked_object_axis(
            depth,
            square,
            _intrinsics(),
            stride=1,
            minimum_points=20,
        )
