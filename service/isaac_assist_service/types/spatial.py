"""
Shared spatial primitives for IA — Vec3, Pose6D, Bbox3.

Pydantic v2 models. Zero internal dependencies on other
`service.isaac_assist_service.*` modules (see __init__.py for the
import-purity contract). Consumers in `multimodal/`, `diagnose/`,
and `governance/` migrate to these incrementally per spec
Phase 8c's *additive-only* changes.

Conventions:
- All lengths in meters; angles in radians.
- Right-handed coordinates; rotations applied as roll (X) → pitch (Y)
  → yaw (Z), intrinsic. `to_matrix()` returns a 4x4 homogeneous
  transform `T = [[R, t], [0, 1]]` such that `world = T @ local`.
- Quaternion convention: (x, y, z, w) — Hamilton, scalar-last.
  Matches `usdrt.Gf.Quatf` and ROS2 `geometry_msgs/Quaternion`.
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Vec3
# ---------------------------------------------------------------------------

class Vec3(BaseModel):
    """A 3D vector or point. Units are meters when used as a position."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    z: float

    def to_tuple(self) -> Tuple[float, float, float]:
        """Return `(x, y, z)` as a plain tuple — useful for USD/numpy interop."""
        return (self.x, self.y, self.z)

    @classmethod
    def from_tuple(cls, t: Tuple[float, float, float]) -> "Vec3":
        """Build a Vec3 from a 3-tuple `(x, y, z)`."""
        if len(t) != 3:
            raise ValueError(f"Vec3.from_tuple expects len-3 tuple, got len {len(t)}")
        return cls(x=float(t[0]), y=float(t[1]), z=float(t[2]))


# ---------------------------------------------------------------------------
# Pose6D
# ---------------------------------------------------------------------------

class Pose6D(BaseModel):
    """A 6-DOF rigid pose: translation `(x, y, z)` + Euler angles
    `(roll, pitch, yaw)` in radians.

    The Euler convention is intrinsic XYZ (roll about X, then pitch
    about new Y, then yaw about new Z). `to_matrix()` and `from_matrix()`
    are mutual inverses to numerical precision.
    """

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    z: float
    roll: float = 0.0   # rotation about X, radians
    pitch: float = 0.0  # rotation about Y, radians
    yaw: float = 0.0    # rotation about Z, radians

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_quaternion(
        cls,
        x: float,
        y: float,
        z: float,
        w: float,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> "Pose6D":
        """Construct from a quaternion `(qx, qy, qz, qw)` plus an
        optional position. Quaternion order is scalar-last to match
        ROS2 / usdrt convention.

        Position defaults to the origin when only orientation is known.
        """
        # Normalize to keep tan2 arguments well-conditioned.
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        if norm <= 0.0:
            raise ValueError("from_quaternion: zero-norm quaternion is invalid")
        qx, qy, qz, qw = x / norm, y / norm, z / norm, w / norm

        # Intrinsic XYZ Euler extraction (roll, pitch, yaw).
        sinr_cosp = 2.0 * (qw * qx + qy * qz)
        cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (qw * qy - qz * qx)
        if abs(sinp) >= 1.0:
            pitch = math.copysign(math.pi / 2.0, sinp)  # gimbal-lock clamp
        else:
            pitch = math.asin(sinp)

        siny_cosp = 2.0 * (qw * qz + qx * qy)
        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return cls(
            x=float(position[0]),
            y=float(position[1]),
            z=float(position[2]),
            roll=roll,
            pitch=pitch,
            yaw=yaw,
        )

    @classmethod
    def from_matrix(cls, m: np.ndarray) -> "Pose6D":
        """Recover a Pose6D from a 4x4 homogeneous transform.

        Inverse of `to_matrix`: round-trips within ~1e-12 for
        non-degenerate poses (away from pitch = ±π/2 gimbal lock).
        """
        m = np.asarray(m, dtype=float)
        if m.shape != (4, 4):
            raise ValueError(f"from_matrix expects 4x4 array, got shape {m.shape}")

        tx, ty, tz = float(m[0, 3]), float(m[1, 3]), float(m[2, 3])

        # Intrinsic XYZ extraction from the 3x3 rotation block.
        # R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
        # -> R[2,0] = -sin(pitch)
        sp = -m[2, 0]
        # Clamp for numerical safety
        sp = max(-1.0, min(1.0, float(sp)))
        pitch = math.asin(sp)

        if abs(sp) < 1.0 - 1e-9:
            roll = math.atan2(float(m[2, 1]), float(m[2, 2]))
            yaw = math.atan2(float(m[1, 0]), float(m[0, 0]))
        else:
            # Gimbal lock: assign yaw=0, fold remaining rotation into roll.
            roll = math.atan2(-float(m[1, 2]), float(m[1, 1]))
            yaw = 0.0

        return cls(x=tx, y=ty, z=tz, roll=roll, pitch=pitch, yaw=yaw)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def to_matrix(self) -> np.ndarray:
        """Return the 4x4 homogeneous transform for this pose.

        R = Rz(yaw) @ Ry(pitch) @ Rx(roll) (intrinsic XYZ).
        """
        cr, sr = math.cos(self.roll), math.sin(self.roll)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)

        # Composed rotation, expanded so we don't allocate three 3x3s.
        r00 = cy * cp
        r01 = cy * sp * sr - sy * cr
        r02 = cy * sp * cr + sy * sr
        r10 = sy * cp
        r11 = sy * sp * sr + cy * cr
        r12 = sy * sp * cr - cy * sr
        r20 = -sp
        r21 = cp * sr
        r22 = cp * cr

        m = np.array(
            [
                [r00, r01, r02, self.x],
                [r10, r11, r12, self.y],
                [r20, r21, r22, self.z],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        return m


# ---------------------------------------------------------------------------
# Bbox3
# ---------------------------------------------------------------------------

class Bbox3(BaseModel):
    """Axis-aligned 3D bounding box. `min` and `max` are corner points;
    each coordinate of `min` must be ≤ the corresponding coordinate of
    `max`.
    """

    model_config = ConfigDict(frozen=True)

    min: Vec3 = Field(...)
    max: Vec3 = Field(...)

    @model_validator(mode="after")
    def _check_ordering(self) -> "Bbox3":
        if (
            self.min.x > self.max.x
            or self.min.y > self.max.y
            or self.min.z > self.max.z
        ):
            raise ValueError(
                f"Bbox3: min {self.min.to_tuple()} must be ≤ max "
                f"{self.max.to_tuple()} componentwise"
            )
        return self

    def intersects(self, other: "Bbox3") -> bool:
        """AABB overlap test. Touching faces (zero-volume overlap)
        count as intersecting.
        """
        return (
            self.min.x <= other.max.x and self.max.x >= other.min.x
            and self.min.y <= other.max.y and self.max.y >= other.min.y
            and self.min.z <= other.max.z and self.max.z >= other.min.z
        )

    def volume_m3(self) -> float:
        """Volume in cubic meters. Always non-negative."""
        dx = self.max.x - self.min.x
        dy = self.max.y - self.min.y
        dz = self.max.z - self.min.z
        return float(dx * dy * dz)

    def expand(self, margin_m: float) -> "Bbox3":
        """Return a NEW Bbox3 with every face translated outward by
        `margin_m`. Negative margins shrink the box; the validator
        rejects the result if shrinking collapses an axis.
        """
        return Bbox3(
            min=Vec3(
                x=self.min.x - margin_m,
                y=self.min.y - margin_m,
                z=self.min.z - margin_m,
            ),
            max=Vec3(
                x=self.max.x + margin_m,
                y=self.max.y + margin_m,
                z=self.max.z + margin_m,
            ),
        )
