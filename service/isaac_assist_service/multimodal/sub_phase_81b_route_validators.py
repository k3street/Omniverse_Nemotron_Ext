"""Phase 81b — route validators.

Provides `RouteValidator`, a concrete validator that extends the Phase 11b
`ConstraintViolation` / `ValidationResult` framework.  It checks four
conditions:

1. **route.inverted_direction** (hard/ERROR): last waypoint x < first
   waypoint x AND route_id contains the string "forward".
2. **route.duplicate_waypoint** (soft/WARNING): same (x, y, z) triple
   appears more than once.
3. **route.insufficient_waypoints** (hard/ERROR): fewer than 2 waypoints.
4. **route.zero_distance_segment** (soft/WARNING): consecutive waypoints
   closer than 1e-6 (euclidean distance).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 81b.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List

from service.isaac_assist_service.types.violations import (
    ConstraintViolation,
    ValidationResult,
)
from service.isaac_assist_service.types.uncertainty import GradedScale


PHASE_ID = "81b"
PHASE_TITLE = "route validators"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 81b",
    }


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class Waypoint:
    """A single named point on a route in 3-D space (x, y, z)."""

    x: float
    y: float
    z: float = 0.0
    label: str = ""

    def as_xyz(self) -> tuple[float, float, float]:
        """Return the waypoint coordinates as a plain ``(x, y, z)`` tuple."""
        return (self.x, self.y, self.z)


@dataclass
class Route:
    """An ordered sequence of waypoints with an identifier."""

    route_id: str
    waypoints: List[Waypoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class RouteValidator:
    """Validate a `Route` against four structural constraints.

    Usage::

        validator = RouteValidator()
        result = validator.validate(route)
        if not result.valid:
            for v in result.violations:
                if v.category == "hard":
                    ...
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, route: Route) -> ValidationResult:
        """Validate *route* and return an aggregate `ValidationResult`.

        Checks are applied in this order:
        1. insufficient_waypoints (hard — short-circuits further checks)
        2. inverted_direction     (hard)
        3. duplicate_waypoint     (soft)
        4. zero_distance_segment  (soft)

        The insufficient-waypoints check runs first; a route with < 2
        waypoints cannot meaningfully be checked for direction or
        segments, so those checks are skipped.
        """
        violations: list[ConstraintViolation] = []

        insufficient = self._check_insufficient_waypoints(route)
        if insufficient is not None:
            violations.append(insufficient)
            # Cannot meaningfully check direction or segments.
            return ValidationResult.from_violations(violations)

        inverted = self._check_inverted_direction(route)
        if inverted is not None:
            violations.append(inverted)

        violations.extend(self._check_duplicate_waypoints(route))
        violations.extend(self._check_zero_distance_segments(route))

        return ValidationResult.from_violations(violations)

    # ------------------------------------------------------------------
    # Individual checks — return None or one/more ConstraintViolation(s)
    # ------------------------------------------------------------------

    def _check_insufficient_waypoints(
        self, route: Route
    ) -> ConstraintViolation | None:
        """Return a hard ``ConstraintViolation`` when the route has fewer than 2 waypoints."""
        if len(route.waypoints) < 2:
            n = len(route.waypoints)
            return ConstraintViolation(
                constraint_id="route.insufficient_waypoints",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Route '{route.route_id}' has {n} waypoint(s); "
                    "at least 2 are required."
                ),
                diagnostics={"waypoint_count": n, "route_id": route.route_id},
                fix_hint="Add waypoints until the route has at least 2 entries.",
            )
        return None

    def _check_inverted_direction(
        self, route: Route
    ) -> ConstraintViolation | None:
        """Check 1: last.x < first.x AND 'forward' in route_id → hard ERROR."""
        if "forward" not in route.route_id:
            return None
        first = route.waypoints[0]
        last = route.waypoints[-1]
        if last.x < first.x:
            return ConstraintViolation(
                constraint_id="route.inverted_direction",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Route '{route.route_id}' is labelled 'forward' but the "
                    f"last waypoint x ({last.x}) is less than the first "
                    f"waypoint x ({first.x})."
                ),
                diagnostics={
                    "route_id": route.route_id,
                    "first_x": first.x,
                    "last_x": last.x,
                },
                fix_hint=(
                    "Reverse the waypoint list or rename the route if "
                    "backward travel is intended."
                ),
            )
        return None

    def _check_duplicate_waypoints(
        self, route: Route
    ) -> list[ConstraintViolation]:
        """Check 2: same (x, y, z) appears more than once → soft WARNING.

        One violation per *duplicate coordinate set* (not per occurrence).
        """
        seen: dict[tuple[float, float, float], int] = {}
        for wp in route.waypoints:
            key = wp.as_xyz()
            seen[key] = seen.get(key, 0) + 1

        violations: list[ConstraintViolation] = []
        for xyz, count in seen.items():
            if count > 1:
                violations.append(
                    ConstraintViolation(
                        constraint_id="route.duplicate_waypoint",
                        category="soft",
                        severity=GradedScale.WARNING,
                        message=(
                            f"Waypoint {xyz} appears {count} times in "
                            f"route '{route.route_id}'."
                        ),
                        diagnostics={
                            "route_id": route.route_id,
                            "xyz": list(xyz),
                            "count": count,
                        },
                        fix_hint="Remove duplicate waypoints from the route.",
                    )
                )
        return violations

    def _check_zero_distance_segments(
        self, route: Route
    ) -> list[ConstraintViolation]:
        """Check 4: consecutive pair with euclidean distance < 1e-6 → soft WARNING."""
        violations: list[ConstraintViolation] = []
        wps = route.waypoints
        for i in range(len(wps) - 1):
            a, b = wps[i], wps[i + 1]
            dist = math.sqrt(
                (b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2
            )
            if dist < 1e-6:
                violations.append(
                    ConstraintViolation(
                        constraint_id="route.zero_distance_segment",
                        category="soft",
                        severity=GradedScale.WARNING,
                        message=(
                            f"Segment {i}→{i+1} in route '{route.route_id}' "
                            f"has near-zero length ({dist:.2e} m)."
                        ),
                        diagnostics={
                            "route_id": route.route_id,
                            "segment_index": i,
                            "distance": dist,
                            "waypoint_a": list(a.as_xyz()),
                            "waypoint_b": list(b.as_xyz()),
                        },
                        fix_hint=(
                            f"Remove or merge the duplicate/near-duplicate "
                            f"waypoints at indices {i} and {i+1}."
                        ),
                    )
                )
        return violations
