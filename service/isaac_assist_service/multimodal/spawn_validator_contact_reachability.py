"""Phase 69 — Live spawn validation: contact reachability.

Pure-Python feasibility checker that operates on synthetic geometry tuples.
No Kit RPC or GPU dependency — the validator can be exercised entirely in
unit tests.

Checks performed per contact point:
- ``within_reach``: contact is within [min_reach + margin, max_reach - margin]
- ``not_below_min_reach``: contact is strictly beyond minimum reach
- ``not_occluded``: no axis-aligned bounding-box occluder sits between the
  robot base and the contact point (slab-method AABB ray test)
- ``no_joint_violations``: the robot spec reports no joint-limit violations
- ``normal_valid``: the contact surface normal has non-zero magnitude

Real Kit raycast integration remains scaffold; the reachability logic here
runs entirely on synthetic geometry tuples and is the production-realistic
feasibility gate.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 69.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 69
PHASE_TITLE = "Live spawn validation: contact reachability"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 69",
    }


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Vec3 = tuple[float, float, float]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ContactPoint:
    """A point on a surface that a robot gripper/tool must reach.

    Attributes
    ----------
    position:
        World-space (x, y, z) of the contact point in metres.
    normal:
        Outward surface normal at the contact point.  Defaults to ``(0, 0, 1)``
        (upward-facing flat surface).  A zero-magnitude normal triggers a
        ``normal_valid`` warning.
    surface_id:
        Optional identifier for the surface prim or object — used in messages.
    """

    position: Vec3
    normal: Vec3 = (0.0, 0.0, 1.0)
    surface_id: str = "default"


@dataclass
class RobotReachSpec:
    """Kinematic reach envelope for a single robot arm.

    Attributes
    ----------
    base_position:
        World-space (x, y, z) of the robot base / shoulder origin in metres.
    max_reach_m:
        Maximum reach radius in metres.
    min_reach_m:
        Minimum reach radius (inner dead-zone) in metres.  Defaults to 0.0.
    joint_limit_violations:
        List of joint names that are currently violating their configured
        limits.  Non-empty list triggers the ``no_joint_violations`` check.
    """

    base_position: Vec3
    max_reach_m: float
    min_reach_m: float = 0.0
    joint_limit_violations: List[str] = field(default_factory=list)


@dataclass
class OccluderBox:
    """Axis-aligned bounding box used to model an obstacle in the scene.

    Attributes
    ----------
    center:
        World-space (x, y, z) of the box centre in metres.
    half_extents:
        (hx, hy, hz) — half-widths along each axis.  All values must be
        positive.
    """

    center: Vec3
    half_extents: Vec3


@dataclass
class ReachabilityFinding:
    """Single check result emitted by :class:`ContactReachabilityValidator`.

    Attributes
    ----------
    check_id:
        Machine-readable identifier (e.g. ``"within_reach"``).
    severity:
        ``"error"`` halts acceptance; ``"warn"`` is advisory; ``"info"`` is
        informational only.
    message:
        Human-readable description suitable for error logs / UI display.
    """

    check_id: str
    severity: Literal["error", "warn", "info"]
    message: str


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

# Small epsilon used to protect slab divisions from division-by-zero
_EPSILON = 1e-9


class ContactReachabilityValidator:
    """Post-spawn validator for contact-point reachability.

    Parameters
    ----------
    reach_margin_m:
        Safety margin (metres) applied symmetrically to both ends of the
        reach envelope.  A contact at exactly ``max_reach_m`` distance is
        *not* considered reachable — it must be within
        ``max_reach_m - reach_margin_m``.  Default is 0.05 m (5 cm).
    """

    def __init__(self, reach_margin_m: float = 0.05) -> None:
        self.reach_margin_m = reach_margin_m

    # ------------------------------------------------------------------
    # Geometric helpers (static / pure)
    # ------------------------------------------------------------------

    @staticmethod
    def distance(a: Vec3, b: Vec3) -> float:
        """Euclidean distance between two 3-D points."""
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def is_within_reach(self, contact: ContactPoint, robot: RobotReachSpec) -> bool:
        """Return ``True`` iff *contact* is inside the robot's effective reach envelope.

        The envelope is ``[min_reach + margin, max_reach - margin]``.  Contacts
        too close (inner dead-zone) or too far are rejected.
        """
        d = self.distance(contact.position, robot.base_position)
        lo = robot.min_reach_m + self.reach_margin_m
        hi = robot.max_reach_m - self.reach_margin_m
        return lo <= d <= hi

    @staticmethod
    def ray_intersects_aabb(start: Vec3, end: Vec3, box: OccluderBox) -> bool:
        """Slab-method AABB intersection test for a line segment.

        Returns ``True`` iff the segment ``[start, end]`` passes through or
        touches the axis-aligned bounding box defined by *box*.

        The test uses the standard slab method: for each pair of parallel
        planes the ray's parametric interval ``[t_min, t_max]`` is computed
        and intersected.  The ray is considered to intersect when the
        accumulated interval overlaps ``[0, 1]`` (the segment parameter range).
        """
        t_min = 0.0
        t_max = 1.0

        cx, cy, cz = box.center
        hx, hy, hz = box.half_extents

        box_min = (cx - hx, cy - hy, cz - hz)
        box_max = (cx + hx, cy + hy, cz + hz)

        for axis in range(3):
            d = end[axis] - start[axis]
            s = start[axis]
            blo = box_min[axis]
            bhi = box_max[axis]

            if abs(d) < _EPSILON:
                # Ray is parallel to this slab — check if origin is inside
                if s < blo or s > bhi:
                    return False  # parallel and outside
            else:
                t1 = (blo - s) / d
                t2 = (bhi - s) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                t_min = max(t_min, t1)
                t_max = min(t_max, t2)
                if t_min > t_max:
                    return False

        return True

    def is_occluded(
        self,
        contact: ContactPoint,
        robot: RobotReachSpec,
        occluders: List[OccluderBox],
    ) -> bool:
        """Return ``True`` iff any occluder lies on the path from robot base to *contact*.

        The line segment tested runs from ``robot.base_position`` to
        ``contact.position``.  An occluder that begins at or beyond the
        contact position (i.e. behind the target) does *not* count as
        occluding.
        """
        if not occluders:
            return False

        base = robot.base_position
        pos = contact.position

        # Distance from base to contact
        d_total = self.distance(base, pos)
        if d_total < _EPSILON:
            # Degenerate case: contact is at the robot base — not occluded
            return False

        for box in occluders:
            # Check the full segment [base → contact].
            # We want to exclude occluders whose centre is beyond the contact
            # point (behind the target).  We do this by checking the
            # parametric t-value at the box centre projected onto the ray.
            dx = pos[0] - base[0]
            dy = pos[1] - base[1]
            dz = pos[2] - base[2]

            # Project box centre onto ray to get parameter t ∈ [0,1]
            bx, by, bz = box.center
            t_centre = (
                (bx - base[0]) * dx
                + (by - base[1]) * dy
                + (bz - base[2]) * dz
            ) / (d_total * d_total)

            # Only consider occluders whose centre is *between* base and
            # contact (t in (0, 1)) — occluders at t >= 1 are behind the target.
            if t_centre >= 1.0:
                continue

            if self.ray_intersects_aabb(base, pos, box):
                return True

        return False

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        contact: ContactPoint,
        robot: RobotReachSpec,
        occluders: Optional[List[OccluderBox]] = None,
    ) -> List[ReachabilityFinding]:
        """Run all reachability checks and return the complete finding list.

        All checks always execute (no early-exit) so callers receive a full
        diagnostic picture in one pass.

        Parameters
        ----------
        contact:
            The contact point to validate.
        robot:
            The robot's kinematic reach specification.
        occluders:
            Optional list of AABB obstacles to test for line-of-sight
            occlusion.
        """
        _occluders: List[OccluderBox] = occluders if occluders is not None else []
        findings: List[ReachabilityFinding] = []

        # --- within_reach ---
        if not self.is_within_reach(contact, robot):
            d = self.distance(contact.position, robot.base_position)
            lo = robot.min_reach_m + self.reach_margin_m
            hi = robot.max_reach_m - self.reach_margin_m
            findings.append(
                ReachabilityFinding(
                    check_id="within_reach",
                    severity="error",
                    message=(
                        f"Contact '{contact.surface_id}' at distance {d:.4f} m is "
                        f"outside the effective reach envelope [{lo:.4f}, {hi:.4f}] m."
                    ),
                )
            )

        # --- not_below_min_reach ---
        d = self.distance(contact.position, robot.base_position)
        if d <= robot.min_reach_m:
            findings.append(
                ReachabilityFinding(
                    check_id="not_below_min_reach",
                    severity="error",
                    message=(
                        f"Contact '{contact.surface_id}' at distance {d:.4f} m is "
                        f"within or at the minimum reach radius {robot.min_reach_m:.4f} m "
                        "(inner dead-zone)."
                    ),
                )
            )

        # --- not_occluded ---
        if self.is_occluded(contact, robot, _occluders):
            findings.append(
                ReachabilityFinding(
                    check_id="not_occluded",
                    severity="error",
                    message=(
                        f"Line-of-reach from robot base to contact "
                        f"'{contact.surface_id}' is blocked by at least one "
                        "occluder in the scene."
                    ),
                )
            )

        # --- no_joint_violations ---
        if robot.joint_limit_violations:
            joints = ", ".join(robot.joint_limit_violations)
            findings.append(
                ReachabilityFinding(
                    check_id="no_joint_violations",
                    severity="error",
                    message=(
                        f"Robot has joint-limit violations that prevent reaching "
                        f"contact '{contact.surface_id}': [{joints}]."
                    ),
                )
            )

        # --- normal_valid ---
        nx, ny, nz = contact.normal
        normal_mag = math.sqrt(nx * nx + ny * ny + nz * nz)
        if normal_mag <= 0.0:
            findings.append(
                ReachabilityFinding(
                    check_id="normal_valid",
                    severity="warn",
                    message=(
                        f"Contact '{contact.surface_id}' has a zero-magnitude "
                        "surface normal — approach direction cannot be determined."
                    ),
                )
            )

        return findings

    def validate_batch(
        self,
        contacts: List[ContactPoint],
        robot: RobotReachSpec,
        occluders: Optional[List[OccluderBox]] = None,
    ) -> Dict[int, List[ReachabilityFinding]]:
        """Validate a list of contact points and return findings keyed by index.

        Parameters
        ----------
        contacts:
            Ordered list of contact points.
        robot:
            Robot reach spec applied to every contact.
        occluders:
            Shared occluder list (applied to every contact).

        Returns
        -------
        dict[int, list[ReachabilityFinding]]
            Mapping from contact index to its finding list.
        """
        return {
            idx: self.validate(contact, robot, occluders)
            for idx, contact in enumerate(contacts)
        }

    @staticmethod
    def passed(findings: List[ReachabilityFinding]) -> bool:
        """Return ``True`` iff there are no ``"error"``-severity findings."""
        return all(f.severity != "error" for f in findings)


# ---------------------------------------------------------------------------
# Spec-coverage helper
# ---------------------------------------------------------------------------


def expected_validator_checks() -> List[str]:
    """Return the ordered list of check_ids this validator implements.

    Useful for spec-coverage assertions — callers can verify every check
    documented in Phase 69 is present.
    """
    return [
        "within_reach",
        "not_below_min_reach",
        "not_occluded",
        "no_joint_violations",
        "normal_valid",
    ]
