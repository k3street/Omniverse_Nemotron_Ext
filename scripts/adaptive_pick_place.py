"""Pure live-pose target planning helpers for adaptive pick and place."""
from __future__ import annotations

import torch


ADAPTIVE_PHASES = {
    "approach_banana",
    "descend",
    "grasp",
    "lift",
    "above_plate",
}


def normalize_quaternion_wxyz(quaternion: torch.Tensor) -> torch.Tensor:
    """Return a unit quaternion in RoboLab's recorded ``(w, x, y, z)`` order."""
    if quaternion.shape != (4,):
        raise ValueError(f"quaternion must have shape (4,), got {tuple(quaternion.shape)}")
    if not bool(torch.isfinite(quaternion).all()):
        raise ValueError("quaternion contains a non-finite value")
    norm = torch.linalg.vector_norm(quaternion)
    if float(norm) <= 1.0e-8:
        raise ValueError("quaternion norm must be non-zero")
    return quaternion / norm


def quaternion_conjugate_wxyz(quaternion: torch.Tensor) -> torch.Tensor:
    quaternion = normalize_quaternion_wxyz(quaternion)
    return torch.cat((quaternion[:1], -quaternion[1:]))


def quaternion_multiply_wxyz(left: torch.Tensor, right: torch.Tensor) -> torch.Tensor:
    """Compose two rotations represented as ``(w, x, y, z)`` quaternions."""
    left = normalize_quaternion_wxyz(left)
    right = normalize_quaternion_wxyz(right)
    lw, lx, ly, lz = left.unbind()
    rw, rx, ry, rz = right.unbind()
    result = torch.stack(
        (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        )
    )
    return normalize_quaternion_wxyz(result)


def rotate_vector_wxyz(quaternion: torch.Tensor, vector: torch.Tensor) -> torch.Tensor:
    """Rotate a 3-vector by a ``(w, x, y, z)`` quaternion."""
    quaternion = normalize_quaternion_wxyz(quaternion)
    if vector.shape != (3,):
        raise ValueError(f"vector must have shape (3,), got {tuple(vector.shape)}")
    if not bool(torch.isfinite(vector).all()):
        raise ValueError("vector contains a non-finite value")
    # Use the equivalent vector form of q * (0, v) * conjugate(q).
    w, xyz = quaternion[0], quaternion[1:]
    rotated = (
        vector
        + 2.0 * w * torch.linalg.cross(xyz, vector)
        + 2.0 * torch.linalg.cross(xyz, torch.linalg.cross(xyz, vector))
    )
    return rotated


def yaw_quaternion_wxyz(yaw_radians: float, *, like: torch.Tensor) -> torch.Tensor:
    if not torch.isfinite(torch.tensor(yaw_radians)):
        raise ValueError("yaw_radians must be finite")
    half = like.new_tensor(yaw_radians * 0.5)
    return torch.stack((torch.cos(half), half.new_zeros(()), half.new_zeros(()), torch.sin(half)))


def quaternion_error_axis_angle_wxyz(
    target: torch.Tensor, current: torch.Tensor
) -> torch.Tensor:
    """Shortest world-frame axis-angle rotation from ``current`` to ``target``."""
    error = quaternion_multiply_wxyz(target, quaternion_conjugate_wxyz(current))
    if float(error[0]) < 0.0:
        error = -error
    vector_norm = torch.linalg.vector_norm(error[1:])
    if float(vector_norm) < 1.0e-7:
        return 2.0 * error[1:]
    angle = 2.0 * torch.atan2(vector_norm, torch.clamp(error[0], min=1.0e-8))
    return error[1:] * (angle / vector_norm)


def derive_object_relative_grasp(
    object_xyz: torch.Tensor,
    object_quaternion_wxyz: torch.Tensor,
    grasp_xyz: torch.Tensor,
    grasp_quaternion_wxyz: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Express a calibrated world/root-frame grasp pose in the object frame."""
    for name, value, size in (
        ("object_xyz", object_xyz, 3),
        ("grasp_xyz", grasp_xyz, 3),
    ):
        if value.shape != (size,) or not bool(torch.isfinite(value).all()):
            raise ValueError(f"{name} must be a finite ({size},) tensor")
    inverse_object = quaternion_conjugate_wxyz(object_quaternion_wxyz)
    offset_object = rotate_vector_wxyz(inverse_object, grasp_xyz - object_xyz)
    quaternion_object_to_grasp = quaternion_multiply_wxyz(
        inverse_object, grasp_quaternion_wxyz
    )
    return offset_object, quaternion_object_to_grasp


def apply_object_relative_grasp(
    object_xyz: torch.Tensor,
    object_quaternion_wxyz: torch.Tensor,
    offset_object: torch.Tensor,
    quaternion_object_to_grasp_wxyz: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Apply an object-relative grasp transform to a live object pose."""
    grasp_xyz = object_xyz + rotate_vector_wxyz(object_quaternion_wxyz, offset_object)
    grasp_quaternion = quaternion_multiply_wxyz(
        object_quaternion_wxyz, quaternion_object_to_grasp_wxyz
    )
    return grasp_xyz, grasp_quaternion


def derive_manipulation_feedback(
    *,
    gripper_closed_fraction: float,
    fingertip_object_distance_m: float,
    object_lift_m: float,
    object_target_xy_error_m: float,
    object_height_above_target_m: float,
    contact_height_m: float = 0.025,
) -> dict[str, bool]:
    """Fuse proprioceptive and geometric signals into semantic feedback."""
    values = (
        gripper_closed_fraction,
        fingertip_object_distance_m,
        object_lift_m,
        object_target_xy_error_m,
        object_height_above_target_m,
        contact_height_m,
    )
    if not all(float("-inf") < value < float("inf") for value in values):
        raise ValueError("manipulation feedback values must be finite")
    if not 0.0 <= gripper_closed_fraction <= 1.0:
        raise ValueError("gripper_closed_fraction must be within [0, 1]")
    if contact_height_m <= 0:
        raise ValueError("contact_height_m must be positive")
    closure_obstructed = 0.08 <= gripper_closed_fraction < 0.95
    grasp_candidate = closure_obstructed and fingertip_object_distance_m <= 0.06
    grasp_confirmed = object_lift_m >= 0.03
    object_target_contact_proxy = (
        object_target_xy_error_m <= 0.12
        and 0.0 <= object_height_above_target_m <= contact_height_m
    )
    return {
        "gripper_closure_obstructed": closure_obstructed,
        "grasp_candidate": grasp_candidate,
        "grasp_confirmed": grasp_confirmed,
        "object_target_contact_proxy": object_target_contact_proxy,
    }


def apply_lift_test_contract(
    phase: str,
    decision: dict[str, object],
    gripper_closed_fraction: float,
    *,
    minimum_contact_fraction: float = 0.10,
) -> dict[str, object]:
    """Turn an ambiguous lift retry into a measured attachment test.

    A grasped object prevents the fingers from reaching their fully closed
    position. Gemini may visually interpret that partial travel as open. The
    local contract only normalizes ``retry`` (never ``abort``), records the raw
    decision, and lets measured object lift determine physical success.
    """
    if not 0.0 <= gripper_closed_fraction <= 1.0:
        raise ValueError("gripper_closed_fraction must be within [0, 1]")
    normalized = dict(decision)
    if (
        phase == "lift"
        and normalized.get("decision") == "retry"
        and gripper_closed_fraction >= minimum_contact_fraction
    ):
        normalized["model_decision"] = "retry"
        normalized["decision"] = "execute"
        normalized["supervisor_contract"] = "contact_blocked_closure_lift_test"
        assessment = str(normalized.get("assessment", "")).strip()
        normalized["assessment"] = (
            "Contact-blocked closure meets the local lift-test threshold; "
            "executing one bounded lift so measured banana height can verify attachment. "
            + assessment
        ).strip()
    return normalized


def live_phase_target(
    phase: str,
    banana_xyz: torch.Tensor,
    plate_xyz: torch.Tensor,
    grasp_offset: torch.Tensor,
    *,
    eef_xyz: torch.Tensor | None = None,
    approach_clearance: float,
    lift_clearance: float,
    plate_hover_height: float,
) -> torch.Tensor:
    """Compute a Cartesian EEF target from the current object poses.

    All inputs and the returned target are expressed in robot-root coordinates.
    The target is intentionally position-only; the runtime preserves the proven
    downward grasp orientation while its local Jacobian controller translates.
    """
    vectors = {
        "banana_xyz": banana_xyz,
        "plate_xyz": plate_xyz,
        "grasp_offset": grasp_offset,
    }
    if eef_xyz is not None:
        vectors["eef_xyz"] = eef_xyz
    for name, value in vectors.items():
        if value.shape != (3,):
            raise ValueError(f"{name} must have shape (3,), got {tuple(value.shape)}")
        if not bool(torch.isfinite(value).all()):
            raise ValueError(f"{name} contains a non-finite value")
    clearances = {
        "approach_clearance": approach_clearance,
        "lift_clearance": lift_clearance,
        "plate_hover_height": plate_hover_height,
    }
    invalid = {name: value for name, value in clearances.items() if value <= 0}
    if invalid:
        raise ValueError(f"clearances must be positive: {invalid}")
    if phase not in ADAPTIVE_PHASES:
        raise ValueError(f"unsupported adaptive phase: {phase}")

    grasp_target = banana_xyz + grasp_offset
    if phase == "approach_banana":
        return grasp_target + grasp_target.new_tensor([0.0, 0.0, approach_clearance])
    if phase in {"descend", "grasp"}:
        return grasp_target
    if phase == "lift":
        return grasp_target + grasp_target.new_tensor([0.0, 0.0, lift_clearance])
    if eef_xyz is None:
        raise ValueError("eef_xyz is required for above_plate carry-offset compensation")
    # Preserve the measured grasp transform: translating the EEF by the
    # plate-minus-banana XY residual places the carried object over the plate,
    # even when the banana is held off-center or rotates during pickup.
    target = eef_xyz.clone()
    target[:2] += plate_xyz[:2] - banana_xyz[:2]
    target[2] = plate_xyz[2] + plate_hover_height
    return target
