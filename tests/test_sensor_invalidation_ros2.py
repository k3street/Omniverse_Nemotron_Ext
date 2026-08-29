from __future__ import annotations

import pytest

from scripts.sensor_invalidation_ros2 import (
    LatestSensorObservationBuffer,
    ROS2SensorIngressConfig,
    decode_contact_status,
    decode_motion_status,
    decode_rgbd_status,
    decode_tracked_object_status,
    overlay_sensor_observations,
)
from scripts.sensor_invalidation_registry import SensorObservationSnapshot


def test_contact_json_normalizes_touch_and_force_channels():
    rows = decode_contact_status(
        {"touch": True, "net_force_n": 2.5, "frame_id": "tool0"},
        sequence=4,
        received_at_s=10.0,
    )
    by_channel = {row.channel_id: row for row in rows}
    assert by_channel["gripper.touch"].value is True
    assert by_channel["gripper.contact_force_n"].value == 2.5
    assert {row.source_id for row in rows} == {"ros2.gripper_contact_status"}


def test_rgbd_json_normalizes_visibility_stop_and_clearance():
    rows = decode_rgbd_status(
        {
            "detections": [
                {"label": "banana"},
                {"label": "plate"},
                {"label": "banana"},
            ],
            "stopped": False,
            "minimum_clearance_m": 0.07,
        },
        sequence=5,
        received_at_s=11.0,
    )
    values = {row.channel_id: row.value for row in rows}
    assert values == {
        "rgbd.visible_object_ids": ["banana", "plate"],
        "scene.collision_stop": False,
        "scene.observed_clearance_m": 0.07,
    }


def test_rgbd_status_derives_nearest_clearance_from_collision_predictions():
    rows = decode_rgbd_status(
        {
            "stopped": True,
            "predictions": [
                {"label": "cup", "clearance_m": 0.08},
                {"label": "hand", "clearance_m": -0.01},
            ],
        },
        sequence=6,
        received_at_s=11.1,
    )
    values = {row.channel_id: row.value for row in rows}
    assert values["scene.collision_stop"] is True
    assert values["scene.observed_clearance_m"] == -0.01


def test_tracked_object_pose_errors_are_invalid_when_object_not_visible():
    rows = decode_tracked_object_status(
        {
            "object_id": "yellow banana",
            "visible": False,
            "orientation_error_deg": 31.0,
            "translation_error_m": 0.04,
        },
        sequence=8,
        received_at_s=12.0,
    )
    assert {row.channel_id for row in rows} == {
        "rgbd.object_orientation_error_deg",
        "object.tracked_translation_error_m",
    }
    assert all(not row.valid for row in rows)
    assert all(row.source_id.endswith("yellow_banana") for row in rows)


def test_motion_status_requires_non_negative_integer_count():
    row = decode_motion_status(
        {"stalled_observation_count": 3}, sequence=2, received_at_s=2.0
    )[0]
    assert row.channel_id == "motion.stalled_observation_count"
    assert row.value == 3
    with pytest.raises(ValueError):
        decode_motion_status(
            {"stalled_observation_count": 1.5},
            sequence=3,
            received_at_s=3.0,
        )


def test_latest_buffer_is_channel_deduplicated_and_reports_topic_counts():
    buffer = LatestSensorObservationBuffer()
    older = decode_motion_status(
        {"stalled_observation_count": 1}, sequence=1, received_at_s=1.0
    )[0]
    newer = decode_motion_status(
        {"stalled_observation_count": 0}, sequence=2, received_at_s=2.0
    )[0]
    buffer.update((newer,), topic="/motion")
    buffer.update((older,), topic="/motion")
    assert buffer.snapshot().get("motion.stalled_observation_count") == newer
    assert buffer.status()["message_counts"] == {"/motion": 2}


def test_topic_configuration_rejects_relative_topics():
    with pytest.raises(ValueError, match="absolute ROS topic"):
        ROS2SensorIngressConfig(touch_topic="relative")


def test_published_ros_channel_overrides_only_its_local_fallback():
    fallback = (
        decode_contact_status(
            {"touch": False, "contact_force_n": 0.5},
            sequence=1,
            received_at_s=1.0,
        )
    )
    ros_touch = decode_contact_status(
        {"touch": True}, sequence=2, received_at_s=2.0
    )
    merged = overlay_sensor_observations(
        fallback, SensorObservationSnapshot(ros_touch)
    )
    by_channel = {row.channel_id: row for row in merged}
    assert by_channel["gripper.touch"].value is True
    assert by_channel["gripper.touch"].timestamp_s == 2.0
    assert by_channel["gripper.contact_force_n"].value == 0.5
