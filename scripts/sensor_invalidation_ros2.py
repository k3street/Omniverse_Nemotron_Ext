#!/usr/bin/env python3
"""Optional ROS 2 ingress for normalized motion-lease observations.

The control loop consumes :class:`SensorObservation` records and has no ROS
message-type knowledge.  This adapter owns the ROS subscriptions, converts
standard messages and small JSON status messages into that contract, and keeps
only the latest record for each channel in a thread-safe buffer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
import threading
import time
from typing import Any, Iterable, Mapping

try:
    from .sensor_invalidation_registry import (
        SensorObservation,
        SensorObservationSnapshot,
    )
except ImportError:  # executed as a standalone script/module on sys.path
    from sensor_invalidation_registry import (
        SensorObservation,
        SensorObservationSnapshot,
    )

try:  # pragma: no cover - availability depends on the active ROS environment
    import rclpy
    from geometry_msgs.msg import WrenchStamped
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from std_msgs.msg import Bool, String

    ROS_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - exercised on hosts without ROS 2
    rclpy = None
    WrenchStamped = Bool = String = None
    SingleThreadedExecutor = None
    Node = object
    qos_profile_sensor_data = None
    ROS_IMPORT_ERROR = str(exc)


_SOURCE_FRAGMENT = re.compile(r"[^A-Za-z0-9_.:/-]+")


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result


def _optional_number(body: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in body and body[key] is not None:
            return _finite_number(body[key], key)
    return None


def _json_object(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    body = json.loads(payload) if isinstance(payload, str) else dict(payload)
    if not isinstance(body, dict):
        raise ValueError("sensor status payload must be a JSON object")
    return body


def _source_fragment(value: Any) -> str:
    normalized = _SOURCE_FRAGMENT.sub("_", str(value).strip()).strip("_")
    return normalized[:48] or "unknown"


@dataclass(frozen=True)
class ROS2SensorIngressConfig:
    """Runtime-configurable ROS topic surface; no task or embodiment fields."""

    touch_topic: str = "/isaac_assist/gripper_touch"
    contact_wrench_topic: str = "/isaac_assist/gripper_contact_wrench"
    contact_status_topic: str = "/isaac_assist/gripper_contact_status"
    rgbd_status_topic: str = "/isaac_assist/rgbd_collision_status"
    safety_stop_topic: str = "/isaac_assist/safety_stop"
    tracked_object_status_topic: str = "/isaac_assist/tracked_object_status"
    motion_status_topic: str = "/isaac_assist/motion_status"
    node_name: str = "robolab_sensor_invalidation_ingress"

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if name.endswith("_topic") and not value.startswith("/"):
                raise ValueError(f"{name} must be an absolute ROS topic")


class LatestSensorObservationBuffer:
    """Latest-channel buffer safe for ROS executor and simulator threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, SensorObservation] = {}
        self._message_counts: dict[str, int] = {}

    def update(
        self,
        observations: Iterable[SensorObservation],
        *,
        topic: str | None = None,
    ) -> None:
        rows = tuple(observations)
        if not all(isinstance(item, SensorObservation) for item in rows):
            raise TypeError("observations must contain SensorObservation records")
        with self._lock:
            for item in rows:
                previous = self._latest.get(item.channel_id)
                if previous is None or (
                    item.timestamp_s,
                    item.sequence,
                ) >= (previous.timestamp_s, previous.sequence):
                    self._latest[item.channel_id] = item
            if topic is not None:
                self._message_counts[topic] = self._message_counts.get(topic, 0) + 1

    def snapshot(self) -> SensorObservationSnapshot:
        with self._lock:
            return SensorObservationSnapshot(tuple(self._latest.values()))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "channels": sorted(self._latest),
                "sources": sorted(
                    {item.source_id for item in self._latest.values()}
                ),
                "message_counts": dict(sorted(self._message_counts.items())),
            }


def overlay_sensor_observations(
    fallback: Iterable[SensorObservation],
    ros_snapshot: SensorObservationSnapshot,
) -> tuple[SensorObservation, ...]:
    """Overlay published ROS channels onto explicit local fallback adapters."""
    by_channel = {item.channel_id: item for item in fallback}
    for channel in ros_snapshot.channels():
        item = ros_snapshot.get(channel)
        if item is not None:
            by_channel[channel] = item
    return tuple(by_channel[channel] for channel in sorted(by_channel))


def decode_contact_status(
    payload: str | Mapping[str, Any],
    *,
    sequence: int,
    received_at_s: float,
) -> tuple[SensorObservation, ...]:
    body = _json_object(payload)
    frame_id = body.get("frame_id")
    rows: list[SensorObservation] = []
    if "touch" in body:
        if not isinstance(body["touch"], bool):
            raise ValueError("touch must be boolean")
        rows.append(
            SensorObservation(
                "gripper.touch",
                "ros2.gripper_contact_status",
                sequence,
                received_at_s,
                body["touch"],
                frame_id=frame_id,
            )
        )
    force = _optional_number(body, "contact_force_n", "net_force_n")
    if force is not None:
        rows.append(
            SensorObservation(
                "gripper.contact_force_n",
                "ros2.gripper_contact_status",
                sequence,
                received_at_s,
                force,
                frame_id=frame_id,
            )
        )
    if not rows:
        raise ValueError("contact status contains neither touch nor contact force")
    return tuple(rows)


def decode_rgbd_status(
    payload: str | Mapping[str, Any],
    *,
    sequence: int,
    received_at_s: float,
) -> tuple[SensorObservation, ...]:
    body = _json_object(payload)
    rows: list[SensorObservation] = []
    detections = body.get("detections")
    if detections is not None:
        if not isinstance(detections, list):
            raise ValueError("detections must be a list")
        labels = []
        for item in detections:
            if not isinstance(item, Mapping):
                raise ValueError("detections entries must be objects")
            label = item.get("label")
            if isinstance(label, str) and label:
                labels.append(label)
        rows.append(
            SensorObservation(
                "rgbd.visible_object_ids",
                "ros2.rgbd_collision_monitor",
                sequence,
                received_at_s,
                sorted(set(labels)),
                frame_id=body.get("camera_frame") or None,
            )
        )
    stopped = body.get("stopped")
    if stopped is not None:
        if not isinstance(stopped, bool):
            raise ValueError("stopped must be boolean")
        rows.append(
            SensorObservation(
                "scene.collision_stop",
                "ros2.rgbd_collision_monitor",
                sequence,
                received_at_s,
                stopped,
            )
        )
    clearance = _optional_number(body, "minimum_clearance_m", "clearance_m")
    if clearance is None and isinstance(body.get("predictions"), list):
        prediction_clearances = [
            _finite_number(item["clearance_m"], "predictions[].clearance_m")
            for item in body["predictions"]
            if isinstance(item, Mapping) and item.get("clearance_m") is not None
        ]
        if prediction_clearances:
            clearance = min(prediction_clearances)
    if clearance is not None:
        rows.append(
            SensorObservation(
                "scene.observed_clearance_m",
                "ros2.rgbd_collision_monitor",
                sequence,
                received_at_s,
                clearance,
            )
        )
    if not rows:
        raise ValueError("RGB-D status has no supported observations")
    return tuple(rows)


def decode_tracked_object_status(
    payload: str | Mapping[str, Any],
    *,
    sequence: int,
    received_at_s: float,
) -> tuple[SensorObservation, ...]:
    body = _json_object(payload)
    object_id = _source_fragment(body.get("object_id", "unknown"))
    source_id = f"ros2.rgbd_object_tracker.{object_id}"
    frame_id = body.get("frame_id") or None
    visible = body.get("visible", True)
    if not isinstance(visible, bool):
        raise ValueError("visible must be boolean")
    rows: list[SensorObservation] = []
    orientation_error = _optional_number(body, "orientation_error_deg")
    if orientation_error is not None:
        rows.append(
            SensorObservation(
                "rgbd.object_orientation_error_deg",
                source_id,
                sequence,
                received_at_s,
                orientation_error,
                valid=visible,
                frame_id=frame_id,
            )
        )
    translation_error = _optional_number(body, "translation_error_m")
    if translation_error is not None:
        rows.append(
            SensorObservation(
                "object.tracked_translation_error_m",
                source_id,
                sequence,
                received_at_s,
                translation_error,
                valid=visible,
                frame_id=frame_id,
            )
        )
    if not rows:
        raise ValueError("tracked-object status has no pose errors")
    return tuple(rows)


def decode_motion_status(
    payload: str | Mapping[str, Any],
    *,
    sequence: int,
    received_at_s: float,
) -> tuple[SensorObservation, ...]:
    body = _json_object(payload)
    count = body.get("stalled_observation_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("stalled_observation_count must be a non-negative integer")
    return (
        SensorObservation(
            "motion.stalled_observation_count",
            "ros2.motion_feedback",
            sequence,
            received_at_s,
            count,
            frame_id=body.get("frame_id") or None,
        ),
    )


class ROS2SensorInvalidationSubscriber(Node):  # pragma: no cover - live ROS graph
    def __init__(
        self,
        config: ROS2SensorIngressConfig,
        buffer: LatestSensorObservationBuffer,
    ) -> None:
        super().__init__(config.node_name)
        self.config = config
        self.buffer = buffer
        self._sequence = 0
        self.create_subscription(
            Bool, config.touch_topic, self._on_touch, qos_profile_sensor_data
        )
        self.create_subscription(
            WrenchStamped,
            config.contact_wrench_topic,
            self._on_contact_wrench,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            String, config.contact_status_topic, self._on_contact_status, 10
        )
        self.create_subscription(
            String, config.rgbd_status_topic, self._on_rgbd_status, 10
        )
        self.create_subscription(
            Bool, config.safety_stop_topic, self._on_safety_stop, 10
        )
        self.create_subscription(
            String,
            config.tracked_object_status_topic,
            self._on_tracked_object_status,
            10,
        )
        self.create_subscription(
            String, config.motion_status_topic, self._on_motion_status, 10
        )

    def _next(self) -> tuple[int, float]:
        self._sequence += 1
        return self._sequence, time.monotonic()

    def _update_json(self, message: Any, topic: str, decoder: Any) -> None:
        sequence, received = self._next()
        try:
            self.buffer.update(
                decoder(message.data, sequence=sequence, received_at_s=received),
                topic=topic,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.get_logger().warning(f"Rejected {topic} message: {exc}")

    def _on_touch(self, message: Any) -> None:
        sequence, received = self._next()
        self.buffer.update(
            (
                SensorObservation(
                    "gripper.touch",
                    "ros2.gripper_touch",
                    sequence,
                    received,
                    bool(message.data),
                ),
            ),
            topic=self.config.touch_topic,
        )

    def _on_contact_wrench(self, message: Any) -> None:
        sequence, received = self._next()
        force = message.wrench.force
        magnitude = math.sqrt(force.x * force.x + force.y * force.y + force.z * force.z)
        self.buffer.update(
            (
                SensorObservation(
                    "gripper.contact_force_n",
                    "ros2.gripper_contact_wrench",
                    sequence,
                    received,
                    magnitude,
                    frame_id=message.header.frame_id or None,
                ),
            ),
            topic=self.config.contact_wrench_topic,
        )

    def _on_contact_status(self, message: Any) -> None:
        self._update_json(message, self.config.contact_status_topic, decode_contact_status)

    def _on_rgbd_status(self, message: Any) -> None:
        self._update_json(message, self.config.rgbd_status_topic, decode_rgbd_status)

    def _on_tracked_object_status(self, message: Any) -> None:
        self._update_json(
            message,
            self.config.tracked_object_status_topic,
            decode_tracked_object_status,
        )

    def _on_motion_status(self, message: Any) -> None:
        self._update_json(message, self.config.motion_status_topic, decode_motion_status)

    def _on_safety_stop(self, message: Any) -> None:
        sequence, received = self._next()
        self.buffer.update(
            (
                SensorObservation(
                    "scene.collision_stop",
                    "ros2.rgbd_collision_stop",
                    sequence,
                    received,
                    bool(message.data),
                ),
            ),
            topic=self.config.safety_stop_topic,
        )


class ROS2SensorIngress:
    """Lifecycle handle for an optional background ROS subscriber executor."""

    def __init__(self, config: ROS2SensorIngressConfig) -> None:
        self.config = config
        self.buffer = LatestSensorObservationBuffer()
        self.node: ROS2SensorInvalidationSubscriber | None = None
        self.executor: Any = None
        self.thread: threading.Thread | None = None
        self.owns_rclpy_context = False
        self.available = False
        self.error = ROS_IMPORT_ERROR

    def start(self) -> "ROS2SensorIngress":
        if rclpy is None:
            return self
        try:
            if not rclpy.ok():
                rclpy.init(args=None)
                self.owns_rclpy_context = True
            self.node = ROS2SensorInvalidationSubscriber(self.config, self.buffer)
            self.executor = SingleThreadedExecutor()
            self.executor.add_node(self.node)
            self.thread = threading.Thread(
                target=self.executor.spin,
                name="ros2-sensor-invalidation-ingress",
                daemon=True,
            )
            self.thread.start()
            self.available = True
            self.error = None
        except Exception as exc:  # fail open to the explicit simulator adapters
            self.error = f"{type(exc).__name__}: {exc}"
            self.stop()
        return self

    def stop(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(timeout_sec=1.0)
        if self.node is not None:
            self.node.destroy_node()
        if self.owns_rclpy_context and rclpy is not None and rclpy.ok():
            rclpy.shutdown()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.node = None
        self.executor = None
        self.thread = None
        self.available = False

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self.error,
            "topics": {
                name: value
                for name, value in asdict(self.config).items()
                if name.endswith("_topic")
            },
            **self.buffer.status(),
        }


def start_ros2_sensor_ingress(
    config: ROS2SensorIngressConfig | None = None,
) -> ROS2SensorIngress:
    return ROS2SensorIngress(config or ROS2SensorIngressConfig()).start()
