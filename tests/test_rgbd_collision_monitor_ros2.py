import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.rgbd_collision_monitor_ros2 import (
    StopLatch,
    bbox_xyxy_from_detection,
    decode_proposed_capsules,
    depth_array_to_meters,
    extrapolate_capsules,
    label_score_from_detection,
    load_monitor_config,
)


def test_example_config_is_deployable_and_strict():
    path = Path("config/rgbd_collision_monitor.example.json")
    config = load_monitor_config(path)
    assert config.base_frame == "panda_link0"
    assert len(config.capsules) == 8
    assert config.latch_stop is True
    assert config.allowed_contacts_by_phase["grasp"] == ("banana",)


def test_depth_encodings_normalize_to_meters():
    millimeters = np.array([[0, 250, 1000]], dtype=np.uint16)
    meters = depth_array_to_meters(millimeters, "16UC1")
    assert np.isnan(meters[0, 0])
    assert np.allclose(meters[0, 1:], [0.25, 1.0])
    float_depth = depth_array_to_meters(
        np.array([[0.0, 0.5]], dtype=np.float32), "32FC1"
    )
    assert np.isnan(float_depth[0, 0])
    assert float_depth[0, 1] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        depth_array_to_meters(millimeters, "8UC1")


def test_vision_detection_contract_extracts_box_and_best_hypothesis():
    detection = SimpleNamespace(
        id="17",
        bbox=SimpleNamespace(
            center=SimpleNamespace(position=SimpleNamespace(x=20.0, y=30.0)),
            size_x=10.0,
            size_y=20.0,
        ),
        results=[
            SimpleNamespace(hypothesis=SimpleNamespace(class_id="cup", score=0.8)),
            SimpleNamespace(hypothesis=SimpleNamespace(class_id="bowl", score=0.2)),
        ],
    )
    assert bbox_xyxy_from_detection(detection) == (15.0, 20.0, 25.0, 40.0)
    assert label_score_from_detection(detection) == ("cup", 0.8)


def test_capsule_contract_and_velocity_fallback():
    values = [0, 0, 0, 1, 0, 0, 0.1, 1, 0, 0, 2, 0, 0, 0.2]
    starts, ends, radii = decode_proposed_capsules(values, 2)
    assert starts.shape == (2, 3)
    assert np.allclose(radii, [0.1, 0.2])
    proposed_starts, proposed_ends = extrapolate_capsules(
        starts + 0.1,
        ends + 0.1,
        starts,
        ends,
        frame_dt_s=0.1,
        horizon_s=0.2,
    )
    assert np.allclose(proposed_starts, starts + 0.3)
    assert np.allclose(proposed_ends, ends + 0.3)


def test_stop_latch_requires_clear_frames_and_manual_reset():
    latch = StopLatch(latch=True, clear_frames_to_reset=3)
    assert latch.update(True, "human:0.01m") is True
    assert latch.update(False, "clear") is True
    success, _ = latch.reset()
    assert success is False
    latch.update(False, "clear")
    latch.update(False, "clear")
    success, _ = latch.reset()
    assert success is True
    assert latch.stopped is False
