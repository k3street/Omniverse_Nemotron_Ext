"""RGB-D object-axis tracking from a synchronized depth image and object mask.

The estimator intentionally consumes only camera observations: calibrated
depth plus a detector/instance-segmentation mask.  Simulator rigid-body poses
are not inputs.  The major 3D principal axis is sign-invariant, which is the
observable orientation needed to catch rotation or slip of elongated objects.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np

try:
    from rgbd_collision_safety import backproject_depth
except ModuleNotFoundError:  # imported as scripts.rgbd_object_axis_tracking
    from scripts.rgbd_collision_safety import backproject_depth


@dataclass(frozen=True)
class RGBDObjectAxisObservation:
    center_camera_m: tuple[float, float, float]
    major_axis_camera: tuple[float, float, float]
    point_count: int
    principal_spread_m: float
    transverse_spread_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_camera_m": list(self.center_camera_m),
            "major_axis_camera": list(self.major_axis_camera),
            "point_count": self.point_count,
            "principal_spread_m": self.principal_spread_m,
            "transverse_spread_m": self.transverse_spread_m,
        }


def estimate_masked_object_axis(
    depth_m: np.ndarray,
    instance_mask: np.ndarray,
    intrinsics: np.ndarray,
    *,
    stride: int = 4,
    minimum_points: int = 30,
) -> RGBDObjectAxisObservation:
    """Estimate an object's camera-frame center and major 3D principal axis."""
    depth = np.asarray(depth_m)
    mask = np.asarray(instance_mask, dtype=bool)
    matrix = np.asarray(intrinsics, dtype=np.float64)
    if depth.ndim != 2 or mask.shape != depth.shape:
        raise ValueError("depth and instance_mask must be matching HxW arrays")
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("intrinsics must be a finite 3x3 matrix")
    if stride <= 0 or minimum_points < 3:
        raise ValueError("stride and minimum_points are invalid")
    points = backproject_depth(
        depth,
        matrix,
        mask=mask,
        stride=stride,
    ).astype(np.float64, copy=False)
    if len(points) < minimum_points:
        raise ValueError(
            f"object mask has only {len(points)} valid depth points; "
            f"need {minimum_points}"
        )
    center = np.median(points, axis=0)
    centered = points - center
    covariance = centered.T @ centered / float(len(centered))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    axis = eigenvectors[:, order[0]]
    axis_norm = float(np.linalg.norm(axis))
    if not math.isfinite(axis_norm) or axis_norm < 1.0e-9:
        raise ValueError("object point cloud has no observable principal axis")
    axis = axis / axis_norm
    principal_spread = float(math.sqrt(eigenvalues[0]))
    transverse_spread = float(math.sqrt(eigenvalues[1]))
    if principal_spread < max(0.003, transverse_spread * 1.15):
        raise ValueError("object point cloud orientation is geometrically ambiguous")
    return RGBDObjectAxisObservation(
        center_camera_m=tuple(float(value) for value in center),
        major_axis_camera=tuple(float(value) for value in axis),
        point_count=int(len(points)),
        principal_spread_m=principal_spread,
        transverse_spread_m=transverse_spread,
    )


def sign_invariant_axis_error_deg(
    reference_axis: np.ndarray | tuple[float, float, float],
    observed_axis: np.ndarray | tuple[float, float, float],
) -> float:
    """Return the smallest angle between two unoriented 3D axes."""
    reference = np.asarray(reference_axis, dtype=np.float64)
    observed = np.asarray(observed_axis, dtype=np.float64)
    if reference.shape != (3,) or observed.shape != (3,):
        raise ValueError("axes must be XYZ vectors")
    reference_norm = float(np.linalg.norm(reference))
    observed_norm = float(np.linalg.norm(observed))
    if min(reference_norm, observed_norm) < 1.0e-9:
        raise ValueError("axes must be non-zero")
    cosine = abs(
        float(np.dot(reference / reference_norm, observed / observed_norm))
    )
    return float(np.rad2deg(np.arccos(np.clip(cosine, 0.0, 1.0))))


def instance_mask_for_prim_label(
    instance_ids: np.ndarray,
    info: Mapping[str, Any] | None,
    prim_label_fragment: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Resolve a raw instance-id image to a mask using renderer/ROS labels."""
    ids = np.asarray(instance_ids).squeeze()
    if ids.ndim != 2:
        raise ValueError("instance_ids must be an HxW array")
    if not isinstance(prim_label_fragment, str) or not prim_label_fragment:
        raise ValueError("prim_label_fragment must be non-empty")
    mapping = None if info is None else info.get("idToLabels")
    if not isinstance(mapping, Mapping):
        raise ValueError("instance label mapping is unavailable")
    matched_ids: list[int] = []
    matched_labels: list[str] = []
    for raw_id, raw_label in mapping.items():
        label = str(raw_label)
        if prim_label_fragment not in label:
            continue
        try:
            matched_ids.append(int(raw_id))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid instance id {raw_id!r}") from exc
        matched_labels.append(label)
    if not matched_ids:
        raise ValueError(
            f"no rendered instance label contains {prim_label_fragment!r}"
        )
    mask = np.isin(ids, np.asarray(matched_ids, dtype=ids.dtype))
    if not bool(mask.any()):
        raise ValueError("tracked object instance is not visible in the RGB-D frame")
    return mask, {
        "matched_instance_ids": matched_ids,
        "matched_instance_labels": matched_labels,
        "pixel_count": int(np.count_nonzero(mask)),
    }
