"""Pure decision logic for bounded carried-object recovery."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class RecoveryHoldAssessment:
    strategy: str
    safe_to_resume: bool
    hold_grasp_drift_m: float
    object_lift_m: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SupportContactMonitor:
    """Detect support from height or a stalled, downward set-down motion.

    An elongated object can touch the table after rotating while its center is
    still well above its original center height. The absolute-height test is
    therefore complemented by a conservative progress test: while a downward
    target remains, both the end effector and object must stop descending for
    several consecutive samples inside a bounded support envelope.
    """

    object_initial_z: float
    set_down_clearance_m: float
    maximum_oriented_center_lift_m: float = 0.120
    minimum_downward_progress_m: float = 0.0015
    consecutive_stall_samples: int = 3
    _previous_object_z: float | None = None
    _previous_eef_z: float | None = None
    _stall_samples: int = 0

    def __post_init__(self) -> None:
        values = np.asarray(
            [
                self.object_initial_z,
                self.set_down_clearance_m,
                self.maximum_oriented_center_lift_m,
                self.minimum_downward_progress_m,
            ],
            dtype=np.float64,
        )
        if not np.isfinite(values).all():
            raise ValueError("support-contact monitor values must be finite")
        if self.set_down_clearance_m < 0:
            raise ValueError("set-down clearance must be non-negative")
        if self.maximum_oriented_center_lift_m <= 0:
            raise ValueError("oriented center lift envelope must be positive")
        if self.minimum_downward_progress_m <= 0:
            raise ValueError("minimum downward progress must be positive")
        if self.consecutive_stall_samples < 2:
            raise ValueError("at least two consecutive stall samples are required")

    def update(
        self,
        *,
        object_z: float,
        eef_z: float,
        target_eef_z: float,
        target_tolerance_m: float,
    ) -> dict[str, object] | None:
        values = np.asarray(
            [object_z, eef_z, target_eef_z, target_tolerance_m],
            dtype=np.float64,
        )
        if not np.isfinite(values).all():
            raise ValueError("support-contact samples must be finite")
        if target_tolerance_m <= 0:
            raise ValueError("target tolerance must be positive")

        direct = object_support_contact_event(
            object_z=object_z,
            object_initial_z=self.object_initial_z,
            set_down_clearance_m=self.set_down_clearance_m,
        )
        object_lift = float(object_z - self.object_initial_z)
        eef_progress = None
        object_progress = None
        if self._previous_eef_z is not None and self._previous_object_z is not None:
            eef_progress = float(self._previous_eef_z - eef_z)
            object_progress = float(self._previous_object_z - object_z)
            target_remains = float(eef_z - target_eef_z) > target_tolerance_m
            within_support_envelope = (
                object_lift <= self.maximum_oriented_center_lift_m
            )
            motion_stalled = (
                eef_progress < self.minimum_downward_progress_m
                and object_progress < self.minimum_downward_progress_m
            )
            if target_remains and within_support_envelope and motion_stalled:
                self._stall_samples += 1
            else:
                self._stall_samples = 0

        self._previous_eef_z = float(eef_z)
        self._previous_object_z = float(object_z)
        if direct is not None:
            return direct
        if self._stall_samples < self.consecutive_stall_samples:
            return None
        return {
            "converged": True,
            "reason": "set_down_motion_stalled_at_support_envelope",
            "object_lift_m": object_lift,
            "eef_z_m": float(eef_z),
            "target_eef_z_m": float(target_eef_z),
            "eef_downward_progress_m": eef_progress,
            "object_downward_progress_m": object_progress,
            "consecutive_stall_samples": self._stall_samples,
            "maximum_oriented_center_lift_m": self.maximum_oriented_center_lift_m,
        }


def assess_recovery_hold(
    *,
    offset_before: np.ndarray,
    offset_after: np.ndarray,
    object_z_after: float,
    object_initial_z: float,
    maximum_hold_drift_m: float = 0.008,
    minimum_carried_lift_m: float = 0.030,
) -> RecoveryHoldAssessment:
    """Choose relatch/resume or set-down/regrasp after a closed-grip hold."""
    before = np.asarray(offset_before, dtype=np.float64)
    after = np.asarray(offset_after, dtype=np.float64)
    scalars = np.asarray(
        [
            object_z_after,
            object_initial_z,
            maximum_hold_drift_m,
            minimum_carried_lift_m,
        ],
        dtype=np.float64,
    )
    if before.shape != (3,) or after.shape != (3,):
        raise ValueError("grasp offsets must have shape (3,)")
    if not np.isfinite(before).all() or not np.isfinite(after).all() or not np.isfinite(scalars).all():
        raise ValueError("recovery values must be finite")
    if maximum_hold_drift_m <= 0 or minimum_carried_lift_m <= 0:
        raise ValueError("recovery thresholds must be positive")
    drift = float(np.linalg.norm(after - before))
    lift = float(object_z_after - object_initial_z)
    reasons = []
    if drift > maximum_hold_drift_m:
        reasons.append("continued_slip_during_hold")
    if lift < minimum_carried_lift_m:
        reasons.append("object_not_securely_lifted")
    safe = not reasons
    return RecoveryHoldAssessment(
        strategy="relatch_and_resume" if safe else "set_down_and_regrasp",
        safe_to_resume=safe,
        hold_grasp_drift_m=drift,
        object_lift_m=lift,
        reasons=tuple(reasons),
    )


def support_aligned_object_quaternion_wxyz(
    object_quaternion_wxyz: np.ndarray,
) -> np.ndarray:
    """Project an object's live orientation onto support-plane yaw.

    Recovery grasps must remain top-down even when a slipped elongated object
    rests with substantial roll or pitch.  Preserving yaw still aligns the
    gripper with the object's in-plane heading without copying that unsafe
    tilt into the end-effector target.
    """
    quaternion = np.asarray(object_quaternion_wxyz, dtype=np.float64)
    if quaternion.shape != (4,) or not np.isfinite(quaternion).all():
        raise ValueError("object quaternion must be a finite wxyz vector")
    norm = float(np.linalg.norm(quaternion))
    if norm <= 1.0e-9:
        raise ValueError("object quaternion norm must be positive")
    w, x, y, z = quaternion / norm
    yaw = float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))
    half = 0.5 * yaw
    return np.asarray([np.cos(half), 0.0, 0.0, np.sin(half)], dtype=np.float32)


def placement_completion_event(
    *,
    object_xyz: np.ndarray,
    target_xyz: np.ndarray,
    maximum_xy_error_m: float = 0.120,
    maximum_contact_height_m: float = 0.100,
    settled_displacement_m: float = 0.0,
    maximum_settling_motion_m: float = 0.010,
) -> dict[str, object] | None:
    """Corroborate target overlap and stable support after a recovery set-down."""
    object_position = np.asarray(object_xyz, dtype=np.float64)
    target_position = np.asarray(target_xyz, dtype=np.float64)
    thresholds = np.asarray(
        [
            maximum_xy_error_m,
            maximum_contact_height_m,
            settled_displacement_m,
            maximum_settling_motion_m,
        ],
        dtype=np.float64,
    )
    if (
        object_position.shape != (3,)
        or target_position.shape != (3,)
        or not np.isfinite(object_position).all()
        or not np.isfinite(target_position).all()
        or not np.isfinite(thresholds).all()
    ):
        raise ValueError("placement positions and thresholds must be finite 3D values")
    if (
        maximum_xy_error_m <= 0
        or maximum_contact_height_m <= 0
        or settled_displacement_m < 0
        or maximum_settling_motion_m <= 0
    ):
        raise ValueError("placement thresholds must be positive")
    xy_error = float(np.linalg.norm(object_position[:2] - target_position[:2]))
    height = float(object_position[2] - target_position[2])
    if (
        xy_error > maximum_xy_error_m
        or not 0.0 <= height <= maximum_contact_height_m
        or settled_displacement_m > maximum_settling_motion_m
    ):
        return None
    return {
        "completed": True,
        "reason": "object_target_contact",
        "object_target_xy_error_m": xy_error,
        "object_height_above_target_m": height,
        "maximum_xy_error_m": maximum_xy_error_m,
        "maximum_contact_height_m": maximum_contact_height_m,
        "settled_displacement_m": settled_displacement_m,
        "maximum_settling_motion_m": maximum_settling_motion_m,
    }


def object_support_contact_event(
    *,
    object_z: float,
    object_initial_z: float,
    set_down_clearance_m: float,
    support_tolerance_m: float = 0.010,
) -> dict[str, object] | None:
    """Return a successful early-stop event once the object reaches support."""
    values = np.asarray(
        [object_z, object_initial_z, set_down_clearance_m, support_tolerance_m],
        dtype=np.float64,
    )
    if not np.isfinite(values).all():
        raise ValueError("set-down contact values must be finite")
    if set_down_clearance_m < 0 or support_tolerance_m <= 0:
        raise ValueError("set-down clearance must be non-negative and tolerance positive")
    lift = float(object_z - object_initial_z)
    threshold = max(0.015, set_down_clearance_m + support_tolerance_m)
    if lift > threshold:
        return None
    return {
        "converged": True,
        "reason": "object_support_contact",
        "object_lift_m": lift,
        "maximum_set_down_lift_m": threshold,
    }
