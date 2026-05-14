"""Phase 63b — cuRoboV2 backend: B-spline trajectories + depth-fused TSDF + constrained planning.

Pure-Python SPEC/MATH layer.  GPU/Warp-backed cuRobo planning is opus-runtime gated
and not imported here.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63b.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


PHASE_ID = "63b"
PHASE_TITLE = "cuRoboV2 backend: B-spline trajectories + depth-fused TSDF + constrained planning"
PHASE_STATUS = "landed"

Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BSplineConfig:
    """Configuration for B-spline curve generation."""
    degree: int = 3
    num_control_points: int = 8
    knot_vector: list[float] = field(default_factory=list)
    clamped: bool = True


@dataclass
class TSDFConfig:
    """Configuration for depth-fused truncated signed-distance field."""
    voxel_size_m: float = 0.02
    truncation_distance_m: float = 0.04
    depth_max_m: float = 4.0
    integrate_color: bool = False


@dataclass
class ConstraintSpec:
    """Specification for a single planning constraint."""
    name: str
    kind: Literal["joint_limit", "obstacle", "orientation", "velocity", "jerk"]
    threshold: float
    soft: bool = False


# ---------------------------------------------------------------------------
# Helper math utilities
# ---------------------------------------------------------------------------

def linspace(a: float, b: float, n: int) -> list[float]:
    """Return n evenly spaced values from a to b inclusive."""
    if n < 2:
        return [float(a)] if n == 1 else []
    step = (b - a) / (n - 1)
    return [a + step * i for i in range(n)]


def vec_distance(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two 3-D points."""
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


# ---------------------------------------------------------------------------
# B-spline curve
# ---------------------------------------------------------------------------

class BSplineCurve:
    """Uniform B-spline curve defined by control points.

    Uses de Boor's algorithm for numerically stable evaluation.
    """

    def __init__(self, control_points: list[Vec3], config: Optional[BSplineConfig] = None) -> None:
        self.control_points: list[Vec3] = list(control_points)
        self.config: BSplineConfig = config or BSplineConfig(
            num_control_points=len(control_points)
        )
        # If no knot vector supplied, generate clamped one automatically.
        if not self.config.knot_vector:
            self.config = BSplineConfig(
                degree=self.config.degree,
                num_control_points=len(control_points),
                knot_vector=self.generate_clamped_knot_vector(),
                clamped=self.config.clamped,
            )

    # ------------------------------------------------------------------
    def generate_clamped_knot_vector(self) -> list[float]:
        """Build a clamped (open) uniform knot vector.

        For degree d and n control points the vector has length n + d + 1.
        Structure: [0]*(d+1)  +  interior_knots  +  [1]*(d+1)

        Interior knots are evenly spaced in (0, 1):
            i / (n - d)  for i in 1 .. (n - d - 1)
        """
        d = self.config.degree
        n = len(self.control_points)
        interior = [i / (n - d) for i in range(1, n - d)]
        knots = [0.0] * (d + 1) + interior + [1.0] * (d + 1)
        return knots

    # ------------------------------------------------------------------
    def de_boor(self, t: float) -> Vec3:
        """Evaluate the B-spline at parameter t ∈ [0, 1] via de Boor's algorithm."""
        knots = self.config.knot_vector
        d = self.config.degree
        pts = self.control_points

        # Clamp t to valid range
        t = max(0.0, min(1.0, t))

        # Find the knot span index k such that knots[k] <= t < knots[k+1].
        # For t == 1.0 use the last valid span.
        n = len(pts) - 1
        k = d  # start of valid range
        for i in range(d, n + 1):
            if knots[i] <= t < knots[i + 1]:
                k = i
                break
            if i == n:
                # t == 1.0 (or very close): use the last span that covers 1.0
                k = i

        # De Boor recursion — work on local copy of d+1 control points
        work: list[list[float]] = [list(pts[j]) for j in range(k - d, k + 1)]

        for r in range(1, d + 1):
            for j in range(d, r - 1, -1):
                left_knot = knots[k - d + j]
                right_knot = knots[k + 1 + j - r]
                denom = right_knot - left_knot
                if denom == 0.0:
                    alpha = 1.0
                else:
                    alpha = (t - left_knot) / denom
                for dim in range(3):
                    work[j][dim] = (1.0 - alpha) * work[j - 1][dim] + alpha * work[j][dim]

        result = tuple(work[d])  # type: ignore[return-value]
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    def arc_length_estimate(self, n_samples: int = 100) -> float:
        """Estimate arc length by summing chord lengths along sampled points."""
        if len(self.control_points) < 2:
            return 0.0
        params = linspace(0.0, 1.0, n_samples)
        total = 0.0
        prev = self.de_boor(params[0])
        for t in params[1:]:
            curr = self.de_boor(t)
            total += vec_distance(prev, curr)
            prev = curr
        return total

    # ------------------------------------------------------------------
    def sample(self, n: int = 50) -> list[Vec3]:
        """Sample n evenly-spaced points along the parameter domain."""
        return [self.de_boor(t) for t in linspace(0.0, 1.0, n)]


# ---------------------------------------------------------------------------
# cuRoboV2 backend
# ---------------------------------------------------------------------------

_KNOWN_KINDS: frozenset[str] = frozenset(
    {"joint_limit", "obstacle", "orientation", "velocity", "jerk"}
)


class CuRoboV2Backend:
    """Pure-Python wrapper providing cuRoboV2 planning utilities.

    When dry_run=True (default) all planning operations return stubs
    instead of invoking GPU-backed cuRobo.  GPU path is opus-runtime
    gated and not loaded here.
    """

    def __init__(
        self,
        bspline_config: Optional[BSplineConfig] = None,
        tsdf_config: Optional[TSDFConfig] = None,
        dry_run: bool = True,
    ) -> None:
        self.bspline_config: BSplineConfig = bspline_config or BSplineConfig()
        self.tsdf_config: TSDFConfig = tsdf_config or TSDFConfig()
        self.dry_run: bool = dry_run

    # ------------------------------------------------------------------
    def validate_constraints(self, constraints: list[ConstraintSpec]) -> list[str]:
        """Validate a list of constraint specs.

        Returns a list of issue strings (empty if all constraints are valid).

        Checks performed:
        - Names must be unique.
        - ``kind`` must be one of the known set.
        - ``threshold`` must be > 0 unless kind == "obstacle" (may be 0).
        """
        issues: list[str] = []
        seen_names: set[str] = set()

        for c in constraints:
            # Uniqueness
            if c.name in seen_names:
                issues.append(f"Duplicate constraint name: '{c.name}'")
            seen_names.add(c.name)

            # Known kind
            if c.kind not in _KNOWN_KINDS:
                issues.append(
                    f"Unknown constraint kind '{c.kind}' for '{c.name}'; "
                    f"expected one of {sorted(_KNOWN_KINDS)}"
                )

            # Threshold validity
            if c.kind != "obstacle" and c.threshold <= 0:
                issues.append(
                    f"Constraint '{c.name}' has non-positive threshold "
                    f"{c.threshold}; only 'obstacle' may be 0"
                )

        return issues

    # ------------------------------------------------------------------
    def evaluate_constraint(
        self, constraint: ConstraintSpec, observed_value: float
    ) -> bool:
        """Evaluate whether an observed value satisfies the constraint.

        Soft constraints always return True (violations are penalised, not
        rejected).  Hard constraints pass when observed_value <= threshold.
        """
        if constraint.soft:
            return True
        return observed_value <= constraint.threshold

    # ------------------------------------------------------------------
    def make_trajectory_from_waypoints(
        self, waypoints: list[Vec3], num_control_points: int = 8
    ) -> BSplineCurve:
        """Fit a B-spline trajectory through (or near) the given waypoints.

        Control points are obtained by linearly interpolating between the
        waypoints so that the num_control_points positions are evenly spread
        across the waypoint sequence.

        When num_control_points == len(waypoints) the control points equal
        the waypoints exactly.
        """
        if not waypoints:
            raise ValueError("waypoints must not be empty")

        n_wp = len(waypoints)

        # Build control points by lerp-sampling waypoints
        ctrl_pts: list[Vec3] = []
        for i in range(num_control_points):
            t = i / max(num_control_points - 1, 1)
            # Find position along waypoint sequence at fractional index
            frac_idx = t * (n_wp - 1)
            lo = int(math.floor(frac_idx))
            hi = min(lo + 1, n_wp - 1)
            alpha = frac_idx - lo
            wp_lo = waypoints[lo]
            wp_hi = waypoints[hi]
            pt: Vec3 = tuple(  # type: ignore[assignment]
                wp_lo[dim] * (1.0 - alpha) + wp_hi[dim] * alpha for dim in range(3)
            )
            ctrl_pts.append(pt)

        cfg = BSplineConfig(
            degree=self.bspline_config.degree,
            num_control_points=num_control_points,
            clamped=self.bspline_config.clamped,
        )
        return BSplineCurve(ctrl_pts, cfg)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63b",
        "agent_type": "sonnet-bounded",
        "scope": "SPEC/MATH layer — B-spline math, constraint validation, TSDF metadata config",
        "gpu_gated": "opus-runtime",
    }
