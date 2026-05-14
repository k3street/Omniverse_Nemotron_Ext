"""Phase 80c — Curved-belt + belt-junction geometry primitives.

Pure-math layer: arc-length, centerline sampling, junction kinematics.
Live USD/PhysX placement is handled at opus-runtime.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 80c.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Tuple


PHASE_ID = "80c"
PHASE_TITLE = "Curved-belt + belt-junction primitives"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 80c",
    }


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Vec3 = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CurvedBeltSpec:
    """Specification for a single curved conveyor belt segment.

    The belt is an arc in the XY plane centred on *center_xy*, with the
    belt surface at height *belt_height_m*.

    Args:
        name: Human-readable identifier.
        inner_radius_m: Radius of the inner belt edge (metres).
        outer_radius_m: Radius of the outer belt edge (metres).
        center_xy: (x, y) centre of the arc circle (metres).
        start_angle_deg: Angle of the arc start point, measured CCW from +X.
        sweep_angle_deg: Signed angular sweep.  Positive = CCW, negative = CW.
        belt_height_m: Z-height of the belt surface (metres).
        surface_speed_mps: Belt surface speed at the centreline (m/s).
        direction: ``"ccw"`` (positive sweep) or ``"cw"`` (negative sweep).
    """

    name: str
    inner_radius_m: float
    outer_radius_m: float
    center_xy: Tuple[float, float]
    start_angle_deg: float
    sweep_angle_deg: float
    belt_height_m: float
    surface_speed_mps: float = 0.5
    direction: Literal["cw", "ccw"] = "ccw"


@dataclass
class BeltJunctionSpec:
    """Specification for a junction that connects two belt segments.

    Args:
        junction_id: Unique identifier.
        incoming_belt: Name of the belt feeding into the junction.
        outgoing_belt: Name of the belt leaving the junction.
        transfer_angle_deg: Angle between incoming and outgoing tangent
            directions at the junction point (degrees).
        drop_height_m: Vertical drop from incoming to outgoing belt
            surface (metres, non-negative).
        gap_m: Horizontal gap between the end of the incoming belt and
            the start of the outgoing belt (metres).
    """

    junction_id: str
    incoming_belt: str
    outgoing_belt: str
    transfer_angle_deg: float
    drop_height_m: float = 0.0
    gap_m: float = 0.005


# ---------------------------------------------------------------------------
# CurvedBeltGeometry
# ---------------------------------------------------------------------------

class CurvedBeltGeometry:
    """Geometric computations for a single curved belt segment.

    Args:
        spec: CurvedBeltSpec describing the belt.
    """

    def __init__(self, spec: CurvedBeltSpec) -> None:
        self.spec = spec

    # ------------------------------------------------------------------
    # Basic derived quantities
    # ------------------------------------------------------------------

    def centerline_radius_m(self) -> float:
        """Average of inner and outer radii."""
        return (self.spec.inner_radius_m + self.spec.outer_radius_m) / 2.0

    def arc_length_m(self) -> float:
        """Arc length of the centreline (metres).

        Computed as ``sweep_rad * centerline_radius``.  The sign of the
        sweep is absorbed by taking the absolute value so arc length is
        always non-negative.
        """
        sweep_rad = math.radians(abs(self.spec.sweep_angle_deg))
        return sweep_rad * self.centerline_radius_m()

    def belt_width_m(self) -> float:
        """Width of the belt surface (outer_radius - inner_radius)."""
        return self.spec.outer_radius_m - self.spec.inner_radius_m

    def belt_area_m2(self) -> float:
        """Approximate surface area of the belt (arc_length * belt_width)."""
        return self.arc_length_m() * self.belt_width_m()

    # ------------------------------------------------------------------
    # Angular speed
    # ------------------------------------------------------------------

    def angular_speed_rad_per_s(self) -> float:
        """Angular speed of the belt (rad/s).

        Derived from the surface speed at the centreline:
        ``omega = surface_speed / centerline_radius``.
        """
        r = self.centerline_radius_m()
        if r == 0.0:
            return 0.0
        return self.spec.surface_speed_mps / r

    # ------------------------------------------------------------------
    # Speed at arbitrary radius
    # ------------------------------------------------------------------

    def surface_speed_at(self, radius_m: float) -> float:
        """Belt surface speed (m/s) at *radius_m* from the arc centre.

        The belt is a rigid body rotating about the arc centre, so
        ``v(r) = omega * r``.  Points on the outer edge move faster than
        points on the inner edge.

        Args:
            radius_m: Radial distance from the arc centre (metres).
        """
        return self.angular_speed_rad_per_s() * radius_m

    # ------------------------------------------------------------------
    # Parametric geometry
    # ------------------------------------------------------------------

    def _angle_at_param(self, arc_param: float) -> float:
        """Return the arc angle (radians) at normalised parameter *arc_param*.

        *arc_param* is clamped to [0, 1].  The effective sweep direction
        respects ``spec.direction``:

        * ``"ccw"`` uses a positive (counter-clockwise) sweep.
        * ``"cw"`` uses a negative (clockwise) sweep.
        """
        t = max(0.0, min(1.0, arc_param))
        start_rad = math.radians(self.spec.start_angle_deg)
        sweep_rad = math.radians(abs(self.spec.sweep_angle_deg))
        if self.spec.direction == "cw":
            sweep_rad = -sweep_rad
        return start_rad + t * sweep_rad

    def position_at(self, arc_param: float) -> Vec3:
        """3-D position on the centreline at normalised parameter *arc_param*.

        ``arc_param=0.0`` is the belt start; ``arc_param=1.0`` is the end.
        The returned point lies at ``belt_height_m`` on the Z axis.

        Args:
            arc_param: Normalised arc parameter in [0, 1].
        """
        angle = self._angle_at_param(arc_param)
        cx, cy = self.spec.center_xy
        r = self.centerline_radius_m()
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        return (x, y, self.spec.belt_height_m)

    def tangent_at(self, arc_param: float) -> Vec3:
        """Unit tangent vector to the centreline at normalised *arc_param*.

        The tangent points in the direction of increasing parameter
        (i.e. towards the end of the belt).

        Args:
            arc_param: Normalised arc parameter in [0, 1].
        """
        angle = self._angle_at_param(arc_param)
        sweep_rad = math.radians(abs(self.spec.sweep_angle_deg))
        sign = -1.0 if self.spec.direction == "cw" else 1.0

        # Derivative of (cos θ, sin θ) with respect to θ is (-sin θ, cos θ).
        # Multiply by sign to reflect direction.
        tx = sign * (-math.sin(angle))
        ty = sign * math.cos(angle)
        tz = 0.0

        norm = math.sqrt(tx * tx + ty * ty + tz * tz)
        if norm < 1e-12:
            return (1.0, 0.0, 0.0)
        return (tx / norm, ty / norm, tz / norm)

    # ------------------------------------------------------------------
    # Sampled centreline
    # ------------------------------------------------------------------

    def sample_centerline(self, n_points: int = 32) -> List[Vec3]:
        """Return *n_points* evenly-spaced positions along the centreline.

        The first point is at ``arc_param=0`` and the last at
        ``arc_param=1``.

        Args:
            n_points: Number of sample points (must be >= 2).
        """
        if n_points < 2:
            n_points = 2
        step = 1.0 / (n_points - 1)
        return [self.position_at(i * step) for i in range(n_points)]


# ---------------------------------------------------------------------------
# BeltJunction
# ---------------------------------------------------------------------------

class BeltJunction:
    """Kinematics of a junction between two belt segments.

    Args:
        spec: BeltJunctionSpec describing the junction.
        incoming: CurvedBeltSpec for the belt feeding into the junction.
        outgoing: CurvedBeltSpec for the belt leaving the junction.
    """

    def __init__(
        self,
        spec: BeltJunctionSpec,
        incoming: CurvedBeltSpec,
        outgoing: CurvedBeltSpec,
    ) -> None:
        self.spec = spec
        self.incoming = incoming
        self.outgoing = outgoing

        self._incoming_geom = CurvedBeltGeometry(incoming)
        self._outgoing_geom = CurvedBeltGeometry(outgoing)

    def transfer_point(self) -> Vec3:
        """3-D coordinates of the transfer point.

        Defined as the start of the outgoing belt with *drop_height_m*
        subtracted from Z (items fall from the end of the incoming belt
        down to the surface of the outgoing belt).

        Returns:
            ``(x, y, z)`` in metres.
        """
        x, y, z = self._outgoing_geom.position_at(0.0)
        return (x, y, z - self.spec.drop_height_m)

    def is_continuous(self) -> bool:
        """Return True when the junction is essentially gap- and drop-free.

        The junction is considered continuous when both
        ``gap_m < 0.01`` and ``drop_height_m < 0.01``.
        """
        return self.spec.gap_m < 0.01 and self.spec.drop_height_m < 0.01

    def transition_jerk_estimate_mps2(self) -> float:
        """Naive jerk estimate at the junction (m/s²).

        Computes the lateral velocity change a parcel experiences as it
        crosses the junction.  The formula is::

            jerk ≈ incoming_speed * sin(|transfer_angle_deg|) + drop_penalty

        where ``drop_penalty = drop_height_m * 9.81 / 0.1`` approximates
        the deceleration of a 100 mm drop under gravity.

        The result is always non-negative.
        """
        v_in = self.incoming.surface_speed_mps
        angle_rad = math.radians(abs(self.spec.transfer_angle_deg))
        lateral = v_in * math.sin(angle_rad)
        drop_penalty = self.spec.drop_height_m * 9.81 / 0.1
        return abs(lateral) + abs(drop_penalty)


# ---------------------------------------------------------------------------
# Demo factory layout
# ---------------------------------------------------------------------------

def make_demo_factory_belt_layout() -> Tuple[List[CurvedBeltSpec], List[BeltJunctionSpec]]:
    """Compose a U-shape factory belt layout: 3 curved belts + 2 junctions.

    Layout description:

    * **belt_a** — 90-degree CCW turn (entry arm, bottom-left to bottom-right).
    * **belt_b** — 180-degree CCW turn (the U-bend at the right end).
    * **belt_c** — 90-degree CCW turn (exit arm, top-right to top-left).

    The two junctions connect belt_a→belt_b and belt_b→belt_c.

    Returns:
        Tuple of (list[CurvedBeltSpec], list[BeltJunctionSpec]).
    """
    belt_a = CurvedBeltSpec(
        name="belt_a",
        inner_radius_m=0.4,
        outer_radius_m=0.7,
        center_xy=(0.0, 0.0),
        start_angle_deg=270.0,   # start pointing down (−Y direction)
        sweep_angle_deg=90.0,    # sweep 90° CCW to point right (+X)
        belt_height_m=0.05,
        surface_speed_mps=0.4,
        direction="ccw",
    )

    belt_b = CurvedBeltSpec(
        name="belt_b",
        inner_radius_m=0.4,
        outer_radius_m=0.7,
        center_xy=(2.0, 0.55),   # right-side U-turn centre
        start_angle_deg=0.0,     # start pointing right (+X)
        sweep_angle_deg=180.0,   # 180° CCW U-turn
        belt_height_m=0.05,
        surface_speed_mps=0.4,
        direction="ccw",
    )

    belt_c = CurvedBeltSpec(
        name="belt_c",
        inner_radius_m=0.4,
        outer_radius_m=0.7,
        center_xy=(0.0, 1.1),    # exit arm centre
        start_angle_deg=180.0,   # start pointing left (−X)
        sweep_angle_deg=90.0,    # sweep 90° CCW to point up (+Y)
        belt_height_m=0.05,
        surface_speed_mps=0.4,
        direction="ccw",
    )

    junction_ab = BeltJunctionSpec(
        junction_id="jct_a_b",
        incoming_belt="belt_a",
        outgoing_belt="belt_b",
        transfer_angle_deg=0.0,   # tangents aligned at handoff
        drop_height_m=0.0,
        gap_m=0.004,
    )

    junction_bc = BeltJunctionSpec(
        junction_id="jct_b_c",
        incoming_belt="belt_b",
        outgoing_belt="belt_c",
        transfer_angle_deg=0.0,
        drop_height_m=0.0,
        gap_m=0.004,
    )

    return ([belt_a, belt_b, belt_c], [junction_ab, junction_bc])
