#!/usr/bin/env python3
"""ROS 2 RGB-D detection-to-robot collision monitor.

The node synchronizes RGB, registered depth, ``vision_msgs/Detection2DArray``,
and optional robot/instance masks. Camera calibration comes from CameraInfo and
camera/link poses come from TF. A latched Bool stop, JSON status, and annotated
RGB image are published for every evaluated frame.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

try:
    from rgbd_collision_safety import (
        draw_collision_overlay,
        fuse_detection_with_depth,
        predict_detection_collisions,
        transform_matrix_from_pose_xyzw,
    )
except ModuleNotFoundError:  # imported as scripts.rgbd_collision_monitor_ros2
    from scripts.rgbd_collision_safety import (
        draw_collision_overlay,
        fuse_detection_with_depth,
        predict_detection_collisions,
        transform_matrix_from_pose_xyzw,
    )

try:
    import message_filters
    import rclpy
    from cv_bridge import CvBridge
    from rclpy.duration import Duration
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.time import Time
    from sensor_msgs.msg import CameraInfo, Image
    from std_msgs.msg import Bool, Float32MultiArray, String
    from std_srvs.srv import SetBool, Trigger
    from tf2_ros import Buffer, TransformException, TransformListener
    from vision_msgs.msg import Detection2DArray

    ROS_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - host without ROS 2
    message_filters = None
    rclpy = None
    Node = object
    ROS_IMPORT_ERROR = str(exc)


@dataclass(frozen=True)
class CapsuleSpec:
    start_frame: str
    end_frame: str
    radius_m: float


@dataclass(frozen=True)
class MonitorConfig:
    rgb_topic: str
    depth_topic: str
    camera_info_topic: str
    detections_topic: str
    robot_self_mask_topic: str | None
    instance_mask_topic: str | None
    phase_topic: str
    proposed_capsules_topic: str
    stop_topic: str
    status_topic: str
    overlay_topic: str
    stop_service: str | None
    reset_service: str
    base_frame: str
    camera_frame: str | None
    capsules: tuple[CapsuleSpec, ...]
    score_threshold: float
    minimum_clearance_m: float
    capsule_uncertainty_m: float
    depth_scale_16u: float
    depth_tolerance_m: float
    point_stride: int
    minimum_points: int
    sync_queue: int
    sync_slop_s: float
    tf_timeout_s: float
    prediction_horizon_s: float
    proposed_capsule_timeout_s: float
    clear_frames_to_reset: int
    latch_stop: bool
    frame_timeout_s: float
    fail_safe_on_invalid_input: bool
    allowed_contacts_by_phase: dict[str, tuple[str, ...]]


def _topic(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{name} must be an absolute ROS topic")
    return value


def load_monitor_config(path: Path) -> MonitorConfig:
    """Load and strictly validate a JSON monitor configuration."""
    body = json.loads(path.expanduser().read_text())
    topics = body.get("topics", {})
    frames = body.get("frames", {})
    safety = body.get("safety", {})
    capsule_rows = body.get("capsules", [])
    if not isinstance(capsule_rows, list) or not capsule_rows:
        raise ValueError("config must contain at least one robot capsule")
    capsules = []
    for index, row in enumerate(capsule_rows):
        try:
            spec = CapsuleSpec(
                start_frame=str(row["start_frame"]),
                end_frame=str(row["end_frame"]),
                radius_m=float(row["radius_m"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid capsule {index}: {exc}") from exc
        if not spec.start_frame or not spec.end_frame or spec.radius_m <= 0:
            raise ValueError(f"invalid capsule {index}: frames/radius")
        capsules.append(spec)
    allowed_rows = body.get("allowed_contacts_by_phase", {})
    if not isinstance(allowed_rows, dict):
        raise ValueError("allowed_contacts_by_phase must be an object")
    allowed = {}
    for phase, labels in allowed_rows.items():
        if not isinstance(labels, list) or not all(
            isinstance(label, str) and label for label in labels
        ):
            raise ValueError(f"allowed contacts for {phase!r} must be strings")
        allowed[str(phase)] = tuple(labels)
    config = MonitorConfig(
        rgb_topic=str(_topic(topics.get("rgb"), "topics.rgb")),
        depth_topic=str(_topic(topics.get("depth"), "topics.depth")),
        camera_info_topic=str(
            _topic(topics.get("camera_info"), "topics.camera_info")
        ),
        detections_topic=str(
            _topic(topics.get("detections"), "topics.detections")
        ),
        robot_self_mask_topic=_topic(
            topics.get("robot_self_mask"), "topics.robot_self_mask", optional=True
        ),
        instance_mask_topic=_topic(
            topics.get("instance_mask"), "topics.instance_mask", optional=True
        ),
        phase_topic=str(
            _topic(topics.get("phase", "/isaac_assist/task_phase"), "topics.phase")
        ),
        proposed_capsules_topic=str(
            _topic(
                topics.get(
                    "proposed_capsules", "/isaac_assist/proposed_link_capsules"
                ),
                "topics.proposed_capsules",
            )
        ),
        stop_topic=str(
            _topic(topics.get("stop", "/isaac_assist/safety_stop"), "topics.stop")
        ),
        status_topic=str(
            _topic(
                topics.get("status", "/isaac_assist/rgbd_collision_status"),
                "topics.status",
            )
        ),
        overlay_topic=str(
            _topic(
                topics.get("overlay", "/isaac_assist/rgbd_collision_overlay"),
                "topics.overlay",
            )
        ),
        stop_service=_topic(
            topics.get("stop_service"), "topics.stop_service", optional=True
        ),
        reset_service=str(
            _topic(
                topics.get("reset_service", "/isaac_assist/reset_safety_stop"),
                "topics.reset_service",
            )
        ),
        base_frame=str(frames.get("base", "panda_link0")),
        camera_frame=(
            str(frames["camera"]) if frames.get("camera") else None
        ),
        capsules=tuple(capsules),
        score_threshold=float(safety.get("score_threshold", 0.45)),
        minimum_clearance_m=float(safety.get("minimum_clearance_m", 0.03)),
        capsule_uncertainty_m=float(safety.get("capsule_uncertainty_m", 0.015)),
        depth_scale_16u=float(safety.get("depth_scale_16u", 0.001)),
        depth_tolerance_m=float(safety.get("depth_tolerance_m", 0.08)),
        point_stride=int(safety.get("point_stride", 2)),
        minimum_points=int(safety.get("minimum_points", 8)),
        sync_queue=int(safety.get("sync_queue", 12)),
        sync_slop_s=float(safety.get("sync_slop_s", 0.05)),
        tf_timeout_s=float(safety.get("tf_timeout_s", 0.05)),
        prediction_horizon_s=float(safety.get("prediction_horizon_s", 0.15)),
        proposed_capsule_timeout_s=float(
            safety.get("proposed_capsule_timeout_s", 0.5)
        ),
        clear_frames_to_reset=int(safety.get("clear_frames_to_reset", 5)),
        latch_stop=bool(safety.get("latch_stop", True)),
        frame_timeout_s=float(safety.get("frame_timeout_s", 0.5)),
        fail_safe_on_invalid_input=bool(
            safety.get("fail_safe_on_invalid_input", True)
        ),
        allowed_contacts_by_phase=allowed,
    )
    finite_positive = {
        "minimum_clearance_m": config.minimum_clearance_m,
        "capsule_uncertainty_m": config.capsule_uncertainty_m,
        "depth_scale_16u": config.depth_scale_16u,
        "depth_tolerance_m": config.depth_tolerance_m,
        "sync_slop_s": config.sync_slop_s,
        "tf_timeout_s": config.tf_timeout_s,
        "prediction_horizon_s": config.prediction_horizon_s,
        "proposed_capsule_timeout_s": config.proposed_capsule_timeout_s,
        "frame_timeout_s": config.frame_timeout_s,
    }
    if not all(math.isfinite(value) and value > 0 for value in finite_positive.values()):
        raise ValueError(f"safety values must be finite and positive: {finite_positive}")
    if not 0.0 <= config.score_threshold <= 1.0:
        raise ValueError("score_threshold must be within [0, 1]")
    if min(
        config.point_stride,
        config.minimum_points,
        config.sync_queue,
        config.clear_frames_to_reset,
    ) <= 0:
        raise ValueError("integer safety values must be positive")
    if not config.base_frame:
        raise ValueError("frames.base must not be empty")
    return config


def depth_array_to_meters(
    depth: np.ndarray, encoding: str, *, scale_16u: float = 0.001
) -> np.ndarray:
    """Normalize common ROS depth encodings to float32 meters."""
    array = np.asarray(depth)
    normalized = encoding.upper()
    if normalized in {"16UC1", "MONO16"}:
        result = array.astype(np.float32) * scale_16u
        result[array == 0] = np.nan
        return result
    if normalized in {"32FC1", "32FC"}:
        result = array.astype(np.float32, copy=True)
        result[result <= 0] = np.nan
        return result
    raise ValueError(f"unsupported depth encoding {encoding!r}")


def bbox_xyxy_from_detection(detection: Any) -> tuple[float, float, float, float]:
    """Extract an XYXY pixel box from ROS 2 vision_msgs variants."""
    bbox = detection.bbox
    center = bbox.center
    if hasattr(center, "position"):
        cx, cy = float(center.position.x), float(center.position.y)
    else:  # older vision_msgs Pose2D layout
        cx, cy = float(center.x), float(center.y)
    sx, sy = float(bbox.size_x), float(bbox.size_y)
    if sx <= 0 or sy <= 0 or not np.isfinite([cx, cy, sx, sy]).all():
        raise ValueError("detection bounding box is invalid")
    return (cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2)


def label_score_from_detection(detection: Any) -> tuple[str, float]:
    """Return the highest-scoring vision_msgs hypothesis."""
    if not detection.results:
        label = str(getattr(detection, "id", "") or "unknown")
        return label, 1.0
    result = max(
        detection.results,
        key=lambda item: float(item.hypothesis.score),
    )
    label = str(result.hypothesis.class_id or getattr(detection, "id", "") or "unknown")
    return label, float(result.hypothesis.score)


def decode_proposed_capsules(
    values: Iterable[float], expected_capsules: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode flattened ``[start_xyz,end_xyz,radius]`` capsule rows."""
    array = np.asarray(list(values), dtype=np.float64)
    if array.shape != (expected_capsules * 7,) or not np.isfinite(array).all():
        raise ValueError(
            f"proposed capsule payload must contain {expected_capsules * 7} finite values"
        )
    rows = array.reshape(expected_capsules, 7)
    if np.any(rows[:, 6] <= 0):
        raise ValueError("proposed capsule radii must be positive")
    return rows[:, :3], rows[:, 3:6], rows[:, 6]


def extrapolate_capsules(
    current_starts: np.ndarray,
    current_ends: np.ndarray,
    previous_starts: np.ndarray | None,
    previous_ends: np.ndarray | None,
    *,
    frame_dt_s: float | None,
    horizon_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Short-horizon constant-velocity fallback when no planned capsules arrive."""
    starts = np.asarray(current_starts, dtype=np.float64)
    ends = np.asarray(current_ends, dtype=np.float64)
    if previous_starts is None or previous_ends is None or frame_dt_s is None:
        return starts.copy(), ends.copy()
    if frame_dt_s <= 1.0e-4 or frame_dt_s > 1.0 or horizon_s <= 0:
        return starts.copy(), ends.copy()
    previous_starts = np.asarray(previous_starts, dtype=np.float64)
    previous_ends = np.asarray(previous_ends, dtype=np.float64)
    if previous_starts.shape != starts.shape or previous_ends.shape != ends.shape:
        return starts.copy(), ends.copy()
    return (
        starts + (starts - previous_starts) * (horizon_s / frame_dt_s),
        ends + (ends - previous_ends) * (horizon_s / frame_dt_s),
    )


class StopLatch:
    """Immediate stop with manual or debounced automatic clearing."""

    def __init__(self, *, latch: bool, clear_frames_to_reset: int) -> None:
        if clear_frames_to_reset <= 0:
            raise ValueError("clear_frames_to_reset must be positive")
        self.latch = latch
        self.clear_frames_to_reset = clear_frames_to_reset
        self.stopped = False
        self.clear_frames = 0
        self.reason = "initializing"

    def update(self, collision: bool, reason: str) -> bool:
        if collision:
            self.stopped = True
            self.clear_frames = 0
            self.reason = reason
        else:
            self.clear_frames += 1
            if not self.latch and self.clear_frames >= self.clear_frames_to_reset:
                self.stopped = False
                self.reason = "clear"
        return self.stopped

    def force_stop(self, reason: str) -> bool:
        self.stopped = True
        self.clear_frames = 0
        self.reason = reason
        return True

    def reset(self) -> tuple[bool, str]:
        if self.clear_frames < self.clear_frames_to_reset:
            return False, (
                f"need {self.clear_frames_to_reset} clear frames; "
                f"have {self.clear_frames}"
            )
        self.stopped = False
        self.reason = "manual_reset_after_clear"
        return True, self.reason


class RGBDCollisionMonitorNode(Node):  # pragma: no cover - requires live ROS graph
    def __init__(self, config: MonitorConfig) -> None:
        super().__init__("isaac_assist_rgbd_collision_monitor")
        self.config = config
        self.bridge = CvBridge()
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.stop_latch = StopLatch(
            latch=config.latch_stop,
            clear_frames_to_reset=config.clear_frames_to_reset,
        )
        self.camera_info: CameraInfo | None = None
        self.phase = "unknown"
        self.last_frame_monotonic: float | None = None
        self.previous_capsules: tuple[np.ndarray, np.ndarray, float] | None = None
        self.proposed_capsules: tuple[np.ndarray, np.ndarray, np.ndarray, float] | None = None
        self.frame_index = 0
        self.stop_pub = self.create_publisher(Bool, config.stop_topic, 10)
        self.status_pub = self.create_publisher(String, config.status_topic, 10)
        self.overlay_pub = self.create_publisher(Image, config.overlay_topic, 2)
        self.stop_client = (
            self.create_client(SetBool, config.stop_service)
            if config.stop_service
            else None
        )
        self.stop_service_engaged = False
        self.create_subscription(
            CameraInfo,
            config.camera_info_topic,
            self._on_camera_info,
            qos_profile_sensor_data,
        )
        self.create_subscription(String, config.phase_topic, self._on_phase, 10)
        self.create_subscription(
            Float32MultiArray,
            config.proposed_capsules_topic,
            self._on_proposed_capsules,
            10,
        )
        self.create_service(Trigger, config.reset_service, self._on_reset)
        subscribers = [
            message_filters.Subscriber(
                self, Image, config.rgb_topic, qos_profile=qos_profile_sensor_data
            ),
            message_filters.Subscriber(
                self, Image, config.depth_topic, qos_profile=qos_profile_sensor_data
            ),
            message_filters.Subscriber(
                self,
                Detection2DArray,
                config.detections_topic,
                qos_profile=qos_profile_sensor_data,
            ),
        ]
        self.has_self_mask = config.robot_self_mask_topic is not None
        self.has_instance_mask = config.instance_mask_topic is not None
        if config.robot_self_mask_topic:
            subscribers.append(
                message_filters.Subscriber(
                    self,
                    Image,
                    config.robot_self_mask_topic,
                    qos_profile=qos_profile_sensor_data,
                )
            )
        if config.instance_mask_topic:
            subscribers.append(
                message_filters.Subscriber(
                    self,
                    Image,
                    config.instance_mask_topic,
                    qos_profile=qos_profile_sensor_data,
                )
            )
        self.sync = message_filters.ApproximateTimeSynchronizer(
            subscribers,
            queue_size=config.sync_queue,
            slop=config.sync_slop_s,
        )
        self.sync.registerCallback(self._on_synchronized)
        self.create_timer(max(0.05, config.frame_timeout_s / 2), self._watchdog)
        self.get_logger().info(
            f"RGB-D collision monitor ready: base={config.base_frame} "
            f"capsules={len(config.capsules)} latch_stop={config.latch_stop}"
        )

    def _on_camera_info(self, message: CameraInfo) -> None:
        if message.width > 0 and message.height > 0 and len(message.k) == 9:
            self.camera_info = message

    def _on_phase(self, message: String) -> None:
        self.phase = message.data.strip() or "unknown"

    def _on_proposed_capsules(self, message: Float32MultiArray) -> None:
        try:
            starts, ends, radii = decode_proposed_capsules(
                message.data, len(self.config.capsules)
            )
            self.proposed_capsules = (starts, ends, radii, time.monotonic())
        except ValueError as exc:
            self.get_logger().warning(f"Rejected proposed capsule payload: {exc}")

    def _on_reset(self, _request: Trigger.Request, response: Trigger.Response):
        success, message = self.stop_latch.reset()
        response.success = success
        response.message = message
        if success:
            self.stop_service_engaged = False
            self._publish_stop(False)
            if self.stop_client is not None:
                request = SetBool.Request()
                request.data = False
                self.stop_client.call_async(request)
        return response

    def _lookup_position(self, frame: str, stamp: Any) -> np.ndarray:
        transform = self.tf_buffer.lookup_transform(
            self.config.base_frame,
            frame,
            Time.from_msg(stamp),
            timeout=Duration(seconds=self.config.tf_timeout_s),
        )
        translation = transform.transform.translation
        return np.array([translation.x, translation.y, translation.z], dtype=np.float64)

    def _camera_to_base(self, camera_frame: str, stamp: Any) -> np.ndarray:
        transform = self.tf_buffer.lookup_transform(
            self.config.base_frame,
            camera_frame,
            Time.from_msg(stamp),
            timeout=Duration(seconds=self.config.tf_timeout_s),
        ).transform
        return transform_matrix_from_pose_xyzw(
            np.array(
                [transform.translation.x, transform.translation.y, transform.translation.z]
            ),
            np.array(
                [
                    transform.rotation.x,
                    transform.rotation.y,
                    transform.rotation.z,
                    transform.rotation.w,
                ]
            ),
        )

    def _current_capsules(
        self, stamp: Any
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        starts, ends, radii = [], [], []
        for capsule in self.config.capsules:
            starts.append(self._lookup_position(capsule.start_frame, stamp))
            ends.append(self._lookup_position(capsule.end_frame, stamp))
            radii.append(capsule.radius_m + self.config.capsule_uncertainty_m)
        return np.asarray(starts), np.asarray(ends), np.asarray(radii)

    def _proposed_or_extrapolated(
        self,
        starts: np.ndarray,
        ends: np.ndarray,
        radii: np.ndarray,
        now: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
        if self.proposed_capsules is not None:
            p_starts, p_ends, p_radii, received = self.proposed_capsules
            if now - received <= self.config.proposed_capsule_timeout_s:
                # Never shrink below the configured uncertainty-inflated body.
                return p_starts, p_ends, np.maximum(radii, p_radii), "planned_capsules"
        previous_starts = previous_ends = None
        frame_dt = None
        if self.previous_capsules is not None:
            previous_starts, previous_ends, previous_time = self.previous_capsules
            frame_dt = now - previous_time
        proposed_starts, proposed_ends = extrapolate_capsules(
            starts,
            ends,
            previous_starts,
            previous_ends,
            frame_dt_s=frame_dt,
            horizon_s=self.config.prediction_horizon_s,
        )
        return proposed_starts, proposed_ends, radii, "velocity_extrapolation"

    def _publish_stop(self, stopped: bool) -> None:
        message = Bool()
        message.data = stopped
        self.stop_pub.publish(message)

    def _engage_controller_stop(self) -> None:
        if self.stop_client is None or self.stop_service_engaged:
            return
        request = SetBool.Request()
        request.data = True
        self.stop_client.call_async(request)
        self.stop_service_engaged = True

    def _publish_status(self, payload: dict[str, Any]) -> None:
        message = String()
        message.data = json.dumps(payload, separators=(",", ":"))
        self.status_pub.publish(message)

    def _fail_safe(self, reason: str) -> None:
        stopped = self.stop_latch.force_stop(reason)
        self._publish_stop(stopped)
        self._engage_controller_stop()
        self._publish_status(
            {
                "schema_version": 1,
                "timestamp_monotonic": time.monotonic(),
                "safe": False,
                "stopped": True,
                "reason": reason,
                "phase": self.phase,
            }
        )

    def _watchdog(self) -> None:
        if self.last_frame_monotonic is None:
            return
        age = time.monotonic() - self.last_frame_monotonic
        if age > self.config.frame_timeout_s and self.config.fail_safe_on_invalid_input:
            self._fail_safe(f"rgbd_frame_timeout:{age:.3f}s")

    def _on_synchronized(self, *messages: Any) -> None:
        rgb_message, depth_message, detections_message = messages[:3]
        cursor = 3
        self_mask_message = None
        instance_mask_message = None
        if self.has_self_mask:
            self_mask_message = messages[cursor]
            cursor += 1
        if self.has_instance_mask:
            instance_mask_message = messages[cursor]
        now = time.monotonic()
        try:
            if self.camera_info is None:
                raise RuntimeError("camera_info_unavailable")
            rgb = self.bridge.imgmsg_to_cv2(rgb_message, desired_encoding="rgb8")
            depth_raw = self.bridge.imgmsg_to_cv2(
                depth_message, desired_encoding="passthrough"
            )
            depth = depth_array_to_meters(
                depth_raw,
                depth_message.encoding,
                scale_16u=self.config.depth_scale_16u,
            )
            if rgb.shape[:2] != depth.shape:
                raise RuntimeError(
                    f"rgb_depth_shape_mismatch:{rgb.shape[:2]}!={depth.shape}"
                )
            robot_mask = None
            if self_mask_message is not None:
                robot_mask = self.bridge.imgmsg_to_cv2(
                    self_mask_message, desired_encoding="passthrough"
                )
                robot_mask = np.asarray(robot_mask).squeeze() != 0
                if robot_mask.shape != depth.shape:
                    raise RuntimeError("robot_self_mask_shape_mismatch")
            instance_map = None
            if instance_mask_message is not None:
                instance_map = self.bridge.imgmsg_to_cv2(
                    instance_mask_message, desired_encoding="passthrough"
                )
                instance_map = np.asarray(instance_map).squeeze()
                if instance_map.shape != depth.shape:
                    raise RuntimeError("instance_mask_shape_mismatch")
            intrinsics = np.asarray(self.camera_info.k, dtype=np.float64).reshape(3, 3)
            camera_frame = (
                self.config.camera_frame
                or rgb_message.header.frame_id
                or self.camera_info.header.frame_id
            )
            if not camera_frame:
                raise RuntimeError("camera_frame_unavailable")
            camera_to_base = self._camera_to_base(
                camera_frame, rgb_message.header.stamp
            )
            starts, ends, radii = self._current_capsules(rgb_message.header.stamp)
            proposed_starts, proposed_ends, radii, prediction_source = (
                self._proposed_or_extrapolated(starts, ends, radii, now)
            )
            detections_3d = []
            rejected = []
            for detection in detections_message.detections:
                label, score = label_score_from_detection(detection)
                if score < self.config.score_threshold:
                    continue
                instance_mask = None
                detection_id = str(getattr(detection, "id", ""))
                if instance_map is not None and detection_id:
                    try:
                        instance_mask = instance_map == int(detection_id)
                    except ValueError:
                        rejected.append(
                            {"label": label, "reason": "non_integer_instance_id"}
                        )
                try:
                    detections_3d.append(
                        fuse_detection_with_depth(
                            label=label,
                            score=score,
                            xyxy=bbox_xyxy_from_detection(detection),
                            depth_m=depth,
                            intrinsics=intrinsics,
                            camera_to_base=camera_to_base,
                            instance_mask=instance_mask,
                            exclusion_mask=robot_mask,
                            stride=self.config.point_stride,
                            depth_tolerance_m=self.config.depth_tolerance_m,
                            minimum_points=self.config.minimum_points,
                        )
                    )
                except ValueError as exc:
                    rejected.append({"label": label, "reason": str(exc)})
            allowed = self.config.allowed_contacts_by_phase.get(self.phase, ())
            predictions = predict_detection_collisions(
                detections_3d,
                starts,
                ends,
                radii,
                proposed_segment_starts=proposed_starts,
                proposed_segment_ends=proposed_ends,
                minimum_clearance_m=self.config.minimum_clearance_m,
                allowed_contact_labels=allowed,
            )
            colliding = [item for item in predictions if item.potential_collision]
            reason = (
                "predicted_collision:"
                + ",".join(
                    f"{item.label}:{item.clearance_m:.3f}m" for item in colliding
                )
                if colliding
                else "clear"
            )
            was_stopped = self.stop_latch.stopped
            stopped = self.stop_latch.update(bool(colliding), reason)
            self._publish_stop(stopped)
            if stopped and not was_stopped:
                self._engage_controller_stop()
            overlay = draw_collision_overlay(rgb, predictions)
            overlay_message = self.bridge.cv2_to_imgmsg(overlay, encoding="rgb8")
            overlay_message.header = rgb_message.header
            self.overlay_pub.publish(overlay_message)
            self.frame_index += 1
            self._publish_status(
                {
                    "schema_version": 1,
                    "frame_index": self.frame_index,
                    "safe": not colliding,
                    "stopped": stopped,
                    "stop_reason": self.stop_latch.reason,
                    "clear_frames": self.stop_latch.clear_frames,
                    "phase": self.phase,
                    "allowed_contact_labels": list(allowed),
                    "prediction_source": prediction_source,
                    "detections": [item.summary() for item in detections_3d],
                    "predictions": [item.to_dict() for item in predictions],
                    "rejected_detections": rejected,
                }
            )
            self.previous_capsules = (starts, ends, now)
            self.last_frame_monotonic = now
        except (RuntimeError, ValueError, TransformException) as exc:
            self.last_frame_monotonic = now
            reason = f"invalid_safety_input:{type(exc).__name__}:{exc}"
            self.get_logger().error(reason)
            if self.config.fail_safe_on_invalid_input:
                self._fail_safe(reason)
            else:
                self._publish_status(
                    {"schema_version": 1, "safe": None, "stopped": False, "reason": reason}
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "rgbd_collision_monitor.example.json",
    )
    parser.add_argument("--check-config", action="store_true")
    args, ros_args = parser.parse_known_args(argv)
    config = load_monitor_config(args.config)
    if args.check_config:
        print(json.dumps(asdict(config), indent=2))
        return 0
    if ROS_IMPORT_ERROR is not None:
        raise RuntimeError(f"ROS 2 dependencies are unavailable: {ROS_IMPORT_ERROR}")
    rclpy.init(args=ros_args)
    node = RGBDCollisionMonitorNode(config)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
