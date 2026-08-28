"""Camera-agnostic RGB-D fusion and local manipulation safety primitives.

The functions in this module deliberately do not depend on Isaac Sim or ROS.
They can be fed synchronized images from either an Isaac Lab camera or a real
RGB-D camera after the caller supplies calibrated intrinsics and the optical
camera-to-robot-base transform.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

import numpy as np


def transform_matrix_from_pose_xyzw(
    position: np.ndarray, quaternion_xyzw: np.ndarray
) -> np.ndarray:
    """Return a homogeneous transform from translation and an XYZW quaternion."""
    translation = np.asarray(position, dtype=np.float64)
    quaternion = np.asarray(quaternion_xyzw, dtype=np.float64)
    if translation.shape != (3,) or quaternion.shape != (4,):
        raise ValueError("position and quaternion must have shapes (3,) and (4,)")
    if not np.isfinite(translation).all() or not np.isfinite(quaternion).all():
        raise ValueError("pose must be finite")
    norm = float(np.linalg.norm(quaternion))
    if norm < 1.0e-12:
        raise ValueError("quaternion norm must be non-zero")
    x, y, z, w = quaternion / norm
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    return transform


def bounding_box_mask(
    image_shape: tuple[int, int],
    xyxy: tuple[float, float, float, float],
    *,
    padding_pixels: int = 0,
) -> np.ndarray:
    """Rasterize a clipped XYXY detection box into a boolean image mask."""
    height, width = image_shape
    if height <= 0 or width <= 0 or padding_pixels < 0:
        raise ValueError("image dimensions must be positive and padding non-negative")
    values = np.asarray(xyxy, dtype=np.float64)
    if values.shape != (4,) or not np.isfinite(values).all():
        raise ValueError("xyxy must contain four finite values")
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        raise ValueError("bounding box must have positive area")
    x0 = max(0, int(np.floor(x0)) - padding_pixels)
    y0 = max(0, int(np.floor(y0)) - padding_pixels)
    x1 = min(width, int(np.ceil(x1)) + padding_pixels)
    y1 = min(height, int(np.ceil(y1)) + padding_pixels)
    mask = np.zeros((height, width), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def depth_gate_detection_mask(
    depth_m: np.ndarray,
    xyxy: tuple[float, float, float, float],
    *,
    instance_mask: np.ndarray | None = None,
    exclusion_mask: np.ndarray | None = None,
    padding_pixels: int = 0,
    seed_quantile: float = 0.25,
    depth_tolerance_m: float = 0.08,
    minimum_depth_m: float = 0.05,
    maximum_depth_m: float = 5.0,
) -> tuple[np.ndarray, float]:
    """Fuse a 2D detection with depth while rejecting box background pixels.

    An instance/segmentation mask is preferred. If only a bounding box is
    available, a robust near-depth seed from the central half of the box is
    used to reject background surfaces captured by the rectangle.
    """
    depth = np.asarray(depth_m, dtype=np.float64)
    if depth.ndim != 2:
        raise ValueError("depth must be HxW")
    if not 0.0 <= seed_quantile <= 1.0 or depth_tolerance_m <= 0:
        raise ValueError("invalid seed quantile or depth tolerance")
    box = bounding_box_mask(depth.shape, xyxy, padding_pixels=padding_pixels)
    valid = np.isfinite(depth)
    valid &= (depth >= minimum_depth_m) & (depth <= maximum_depth_m)
    if exclusion_mask is not None:
        excluded = np.asarray(exclusion_mask, dtype=bool)
        if excluded.shape != depth.shape:
            raise ValueError("exclusion mask and depth shapes differ")
        valid &= ~excluded
    if instance_mask is not None:
        supplied_mask = np.asarray(instance_mask, dtype=bool)
        if supplied_mask.shape != depth.shape:
            raise ValueError("instance mask and depth shapes differ")
        selected = box & supplied_mask & valid
        if not bool(selected.any()):
            raise ValueError("detection has no valid masked depth samples")
        seed_depth = float(np.median(depth[selected]))
        return selected, seed_depth

    values = np.asarray(xyxy, dtype=np.float64)
    x0, y0, x1, y1 = values
    central_xyxy = (
        x0 + 0.25 * (x1 - x0),
        y0 + 0.25 * (y1 - y0),
        x1 - 0.25 * (x1 - x0),
        y1 - 0.25 * (y1 - y0),
    )
    central = bounding_box_mask(depth.shape, central_xyxy)
    seed_values = depth[central & valid]
    if not len(seed_values):
        seed_values = depth[box & valid]
    if not len(seed_values):
        raise ValueError("detection bounding box has no valid depth samples")
    seed_depth = float(np.quantile(seed_values, seed_quantile))
    selected = box & valid & (np.abs(depth - seed_depth) <= depth_tolerance_m)
    if not bool(selected.any()):
        raise ValueError("depth gating removed every detection sample")
    return selected, seed_depth


def backproject_depth(
    depth_m: np.ndarray,
    intrinsics: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    camera_to_base: np.ndarray | None = None,
    stride: int = 1,
    minimum_depth_m: float = 0.05,
    maximum_depth_m: float = 5.0,
) -> np.ndarray:
    """Back-project a depth image into base-frame points.

    The camera frame is the standard optical convention: +X right, +Y down,
    +Z forward. ``camera_to_base`` must encode that calibrated convention.
    """
    depth = np.asarray(depth_m, dtype=np.float64)
    matrix = np.asarray(intrinsics, dtype=np.float64)
    if depth.ndim != 2 or matrix.shape != (3, 3):
        raise ValueError("depth must be HxW and intrinsics must be 3x3")
    if stride <= 0 or minimum_depth_m <= 0 or maximum_depth_m <= minimum_depth_m:
        raise ValueError("invalid sampling or depth range")
    if mask is None:
        selected = np.ones(depth.shape, dtype=bool)
    else:
        selected = np.asarray(mask, dtype=bool)
        if selected.shape != depth.shape:
            raise ValueError("mask and depth shapes differ")
    sampled = np.zeros(depth.shape, dtype=bool)
    sampled[::stride, ::stride] = True
    selected &= sampled & np.isfinite(depth)
    selected &= (depth >= minimum_depth_m) & (depth <= maximum_depth_m)
    rows, columns = np.nonzero(selected)
    z = depth[rows, columns]
    fx, fy = matrix[0, 0], matrix[1, 1]
    cx, cy = matrix[0, 2], matrix[1, 2]
    if fx <= 0 or fy <= 0:
        raise ValueError("camera focal lengths must be positive")
    points = np.column_stack(
        ((columns - cx) * z / fx, (rows - cy) * z / fy, z)
    )
    if camera_to_base is not None:
        transform = np.asarray(camera_to_base, dtype=np.float64)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError("camera_to_base must be a finite 4x4 matrix")
        homogeneous = np.column_stack((points, np.ones(len(points))))
        points = (homogeneous @ transform.T)[:, :3]
    return points.astype(np.float32)


@dataclass(frozen=True)
class RGBDDetection:
    """A 2D detection fused into a compact base-frame 3D observation."""

    label: str
    score: float
    xyxy: tuple[float, float, float, float]
    points_base: np.ndarray
    depth_seed_m: float

    @property
    def center_base(self) -> np.ndarray:
        return np.median(self.points_base, axis=0)

    @property
    def aabb_min_base(self) -> np.ndarray:
        return np.min(self.points_base, axis=0)

    @property
    def aabb_max_base(self) -> np.ndarray:
        return np.max(self.points_base, axis=0)

    def summary(self) -> dict[str, object]:
        return {
            "label": self.label,
            "score": self.score,
            "xyxy": list(self.xyxy),
            "point_count": len(self.points_base),
            "depth_seed_m": self.depth_seed_m,
            "center_base_m": self.center_base.tolist(),
            "aabb_min_base_m": self.aabb_min_base.tolist(),
            "aabb_max_base_m": self.aabb_max_base.tolist(),
        }


def fuse_detection_with_depth(
    *,
    label: str,
    score: float,
    xyxy: tuple[float, float, float, float],
    depth_m: np.ndarray,
    intrinsics: np.ndarray,
    camera_to_base: np.ndarray,
    instance_mask: np.ndarray | None = None,
    exclusion_mask: np.ndarray | None = None,
    stride: int = 2,
    depth_tolerance_m: float = 0.08,
    minimum_points: int = 8,
) -> RGBDDetection:
    """Produce a base-frame 3D detection from synchronized box/mask and depth."""
    if not label or not np.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("detection label and score are invalid")
    if minimum_points <= 0:
        raise ValueError("minimum_points must be positive")
    mask, depth_seed = depth_gate_detection_mask(
        depth_m,
        xyxy,
        instance_mask=instance_mask,
        exclusion_mask=exclusion_mask,
        depth_tolerance_m=depth_tolerance_m,
    )
    points = backproject_depth(
        depth_m,
        intrinsics,
        mask=mask,
        camera_to_base=camera_to_base,
        stride=stride,
    )
    if len(points) < minimum_points:
        raise ValueError(
            f"detection has only {len(points)} valid 3D points; need {minimum_points}"
        )
    return RGBDDetection(
        label=label,
        score=float(score),
        xyxy=tuple(float(value) for value in xyxy),
        points_base=points,
        depth_seed_m=depth_seed,
    )


def capsule_point_clearance(
    points: np.ndarray,
    segment_starts: np.ndarray,
    segment_ends: np.ndarray,
    radii_m: np.ndarray,
) -> float:
    """Minimum signed clearance from a point cloud to robot-link capsules."""
    points = np.asarray(points, dtype=np.float64)
    starts = np.asarray(segment_starts, dtype=np.float64)
    ends = np.asarray(segment_ends, dtype=np.float64)
    radii = np.asarray(radii_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    if starts.shape != ends.shape or starts.ndim != 2 or starts.shape[1] != 3:
        raise ValueError("capsule endpoints must both have shape (L, 3)")
    if radii.shape != (len(starts),) or np.any(radii < 0):
        raise ValueError("radii must be non-negative with shape (L,)")
    if not len(points) or not len(starts):
        return float("inf")
    directions = ends - starts
    length_squared = np.sum(directions * directions, axis=1)
    offsets = points[:, None, :] - starts[None, :, :]
    projection = np.sum(offsets * directions[None, :, :], axis=2)
    projection /= np.maximum(length_squared[None, :], 1.0e-12)
    projection = np.clip(projection, 0.0, 1.0)
    closest = starts[None, :, :] + projection[..., None] * directions[None, :, :]
    distances = np.linalg.norm(points[:, None, :] - closest, axis=2)
    return float(np.min(distances - radii[None, :]))


def swept_capsule_clearance(
    points: np.ndarray,
    current_starts: np.ndarray,
    current_ends: np.ndarray,
    proposed_starts: np.ndarray,
    proposed_ends: np.ndarray,
    radii_m: np.ndarray,
    *,
    samples: int = 5,
) -> float:
    """Conservatively sample link capsules along a proposed short motion."""
    if samples < 2:
        raise ValueError("samples must be at least 2")
    clearances = []
    for alpha in np.linspace(0.0, 1.0, samples):
        starts = (1.0 - alpha) * current_starts + alpha * proposed_starts
        ends = (1.0 - alpha) * current_ends + alpha * proposed_ends
        clearances.append(capsule_point_clearance(points, starts, ends, radii_m))
    return min(clearances)


@dataclass(frozen=True)
class CollisionPrediction:
    """Clearance result for one RGB-D detection and a short robot motion."""

    label: str
    score: float
    xyxy: tuple[float, float, float, float]
    clearance_m: float
    minimum_clearance_m: float
    potential_collision: bool
    allowed_contact: bool
    point_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def predict_detection_collisions(
    detections: Iterable[RGBDDetection],
    current_segment_starts: np.ndarray,
    current_segment_ends: np.ndarray,
    radii_m: np.ndarray,
    *,
    proposed_segment_starts: np.ndarray | None = None,
    proposed_segment_ends: np.ndarray | None = None,
    minimum_clearance_m: float = 0.03,
    allowed_contact_labels: Iterable[str] = (),
    swept_samples: int = 5,
) -> list[CollisionPrediction]:
    """Compare detected 3D surfaces with current or swept robot capsules.

    ``allowed_contact_labels`` is phase-specific. For example, the banana may
    be allowed during grasp but must not suppress table or human detections.
    """
    if minimum_clearance_m < 0:
        raise ValueError("minimum_clearance_m must be non-negative")
    one_proposed = proposed_segment_starts is not None
    if one_proposed != (proposed_segment_ends is not None):
        raise ValueError("both proposed capsule endpoint arrays are required")
    allowed = set(allowed_contact_labels)
    results = []
    for detection in detections:
        if one_proposed:
            clearance = swept_capsule_clearance(
                detection.points_base,
                current_segment_starts,
                current_segment_ends,
                np.asarray(proposed_segment_starts),
                np.asarray(proposed_segment_ends),
                radii_m,
                samples=swept_samples,
            )
        else:
            clearance = capsule_point_clearance(
                detection.points_base,
                current_segment_starts,
                current_segment_ends,
                radii_m,
            )
        contact_allowed = detection.label in allowed
        results.append(
            CollisionPrediction(
                label=detection.label,
                score=detection.score,
                xyxy=detection.xyxy,
                clearance_m=clearance,
                minimum_clearance_m=minimum_clearance_m,
                potential_collision=(
                    clearance < minimum_clearance_m and not contact_allowed
                ),
                allowed_contact=contact_allowed,
                point_count=len(detection.points_base),
            )
        )
    return results


def draw_collision_overlay(
    rgb: np.ndarray, predictions: Sequence[CollisionPrediction]
) -> np.ndarray:
    """Draw green/amber/red detection boxes with metric capsule clearance."""
    import cv2

    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("rgb must have shape HxWx3+")
    overlay = np.ascontiguousarray(image[..., :3].astype(np.uint8, copy=True))
    for prediction in predictions:
        x0, y0, x1, y1 = (int(round(value)) for value in prediction.xyxy)
        if prediction.potential_collision:
            color = (255, 32, 32)
            state = "STOP"
        elif prediction.allowed_contact:
            color = (255, 191, 0)
            state = "CONTACT OK"
        else:
            color = (32, 220, 80)
            state = "CLEAR"
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2)
        text = (
            f"{prediction.label} {state} "
            f"{prediction.clearance_m * 100:.1f}cm"
        )
        cv2.putText(
            overlay,
            text,
            (max(0, x0), max(14, y0 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
    return overlay


@dataclass(frozen=True)
class MotionSafetyAssessment:
    safe: bool
    reasons: tuple[str, ...]
    grasp_translation_drift_m: float
    object_lift_m: float
    rgbd_clearance_m: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def assess_motion_safety(
    *,
    phase: str,
    eef_xyz: np.ndarray,
    object_xyz: np.ndarray,
    reference_eef_minus_object: np.ndarray | None,
    object_initial_z: float,
    rgbd_clearance_m: float | None = None,
    maximum_grasp_drift_m: float = 0.025,
    minimum_carried_lift_m: float = 0.030,
    minimum_rgbd_clearance_m: float = 0.030,
) -> MotionSafetyAssessment:
    """Fuse grasp stability and RGB-D clearance into a local stop decision."""
    eef = np.asarray(eef_xyz, dtype=np.float64)
    obj = np.asarray(object_xyz, dtype=np.float64)
    if eef.shape != (3,) or obj.shape != (3,) or not np.isfinite(eef).all() or not np.isfinite(obj).all():
        raise ValueError("EEF and object positions must be finite 3-vectors")
    object_lift = float(obj[2] - object_initial_z)
    drift = 0.0
    reasons = []
    if reference_eef_minus_object is not None:
        reference = np.asarray(reference_eef_minus_object, dtype=np.float64)
        if reference.shape != (3,) or not np.isfinite(reference).all():
            raise ValueError("reference grasp offset must be a finite 3-vector")
        drift = float(np.linalg.norm((eef - obj) - reference))
        if drift > maximum_grasp_drift_m:
            reasons.append("grasp_transform_drift")
    if phase == "above_plate" and object_lift < minimum_carried_lift_m:
        reasons.append("object_drop")
    if rgbd_clearance_m is not None:
        if not np.isfinite(rgbd_clearance_m):
            raise ValueError("RGB-D clearance must be finite when provided")
        if rgbd_clearance_m < minimum_rgbd_clearance_m:
            reasons.append("predicted_collision")
    return MotionSafetyAssessment(
        safe=not reasons,
        reasons=tuple(reasons),
        grasp_translation_drift_m=drift,
        object_lift_m=object_lift,
        rgbd_clearance_m=rgbd_clearance_m,
    )
