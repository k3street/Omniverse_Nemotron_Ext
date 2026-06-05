"""Phase 63b — cuRoboV2 backend: B-spline + TSDF + constraint tests.

Gate: pytest — B-spline evaluation matches analytical results;
      constraint validation catches real issues.
"""
from __future__ import annotations

import math
import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _mod():
    from service.isaac_assist_service.multimodal.sub_phase_63b_curobo_v2_backend import (
        BSplineConfig,
        BSplineCurve,
        ConstraintSpec,
        CuRoboV2Backend,
        TSDFConfig,
        get_phase_metadata,
        linspace,
        vec_distance,
    )
    return (
        BSplineConfig, BSplineCurve, ConstraintSpec, CuRoboV2Backend,
        TSDFConfig, get_phase_metadata, linspace, vec_distance,
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_63b_metadata():
    """Phase identifier and status are correct."""
    _, _, _, _, _, get_phase_metadata, _, _ = _mod()
    md = get_phase_metadata()
    assert md["phase"] == "63b"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. linspace
# ---------------------------------------------------------------------------

def test_linspace_endpoints():
    """linspace must include both endpoints exactly."""
    _, _, _, _, _, _, linspace, _ = _mod()
    values = linspace(0.0, 1.0, 5)
    assert len(values) == 5
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)


def test_linspace_single():
    """linspace with n=1 returns [a]."""
    _, _, _, _, _, _, linspace, _ = _mod()
    assert linspace(3.0, 7.0, 1) == [3.0]


def test_linspace_spacing():
    """linspace values are evenly spaced."""
    _, _, _, _, _, _, linspace, _ = _mod()
    values = linspace(0.0, 4.0, 5)
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    for d in diffs:
        assert d == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 3. vec_distance
# ---------------------------------------------------------------------------

def test_vec_distance_3_4_5():
    """3-4-5 right triangle: distance from origin to (3,4,0) = 5."""
    _, _, _, _, _, _, _, vec_distance = _mod()
    assert vec_distance((0.0, 0.0, 0.0), (3.0, 4.0, 0.0)) == pytest.approx(5.0)


def test_vec_distance_identity():
    """Distance from a point to itself is 0."""
    _, _, _, _, _, _, _, vec_distance = _mod()
    assert vec_distance((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == pytest.approx(0.0)


def test_vec_distance_3d():
    """Full 3-D distance: sqrt(1+1+1)."""
    _, _, _, _, _, _, _, vec_distance = _mod()
    assert vec_distance((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) == pytest.approx(math.sqrt(3))


# ---------------------------------------------------------------------------
# 4. BSplineCurve — knot vector
# ---------------------------------------------------------------------------

def test_generate_clamped_knot_vector_shape():
    """degree=3, n=8 → knot vector length n+d+1 = 12."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), 0.0, 0.0) for i in range(8)]
    cfg = BSplineConfig(degree=3, num_control_points=8)
    curve = BSplineCurve(pts, cfg)
    knots = curve.config.knot_vector
    assert len(knots) == 12


def test_generate_clamped_knot_vector_values():
    """degree=3, n=8 → [0,0,0,0, 0.2,0.4,0.6,0.8, 1,1,1,1]."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), 0.0, 0.0) for i in range(8)]
    cfg = BSplineConfig(degree=3, num_control_points=8)
    curve = BSplineCurve(pts, cfg)
    knots = curve.config.knot_vector
    expected = [0.0, 0.0, 0.0, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.0, 1.0, 1.0]
    for k, e in zip(knots, expected):
        assert k == pytest.approx(e, abs=1e-9)


# ---------------------------------------------------------------------------
# 5. BSplineCurve — de Boor endpoint interpolation
# ---------------------------------------------------------------------------

def test_de_boor_at_t0_returns_first_control_point():
    """Clamped B-spline at t=0 passes through first control point."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), float(i * 2), 0.0) for i in range(6)]
    cfg = BSplineConfig(degree=3, num_control_points=6)
    curve = BSplineCurve(pts, cfg)
    result = curve.de_boor(0.0)
    for r, e in zip(result, pts[0]):
        assert r == pytest.approx(e, abs=1e-9)


def test_de_boor_at_t1_returns_last_control_point():
    """Clamped B-spline at t=1 passes through last control point."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), 0.0, float(i)) for i in range(6)]
    cfg = BSplineConfig(degree=3, num_control_points=6)
    curve = BSplineCurve(pts, cfg)
    result = curve.de_boor(1.0)
    for r, e in zip(result, pts[-1]):
        assert r == pytest.approx(e, abs=1e-9)


# ---------------------------------------------------------------------------
# 6. BSplineCurve — monotone x for linear control points
# ---------------------------------------------------------------------------

def test_de_boor_monotone_x_for_linear_control_points():
    """B-spline through collinear x-axis points should be monotone in x."""
    BSplineConfig, BSplineCurve, _, _, _, _, linspace, _ = _mod()
    n = 8
    pts = [(float(i), 0.0, 0.0) for i in range(n)]
    cfg = BSplineConfig(degree=3, num_control_points=n)
    curve = BSplineCurve(pts, cfg)
    x_values = [curve.de_boor(t)[0] for t in linspace(0.0, 1.0, 40)]
    # Each successive x must be >= previous (monotone non-decreasing)
    for i in range(1, len(x_values)):
        assert x_values[i] >= x_values[i - 1] - 1e-9


# ---------------------------------------------------------------------------
# 7. BSplineCurve — arc_length_estimate
# ---------------------------------------------------------------------------

def test_arc_length_estimate_positive_finite():
    """arc_length_estimate for unit-spaced control points returns finite positive."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), 0.0, 0.0) for i in range(6)]
    cfg = BSplineConfig(degree=3, num_control_points=6)
    curve = BSplineCurve(pts, cfg)
    length = curve.arc_length_estimate(n_samples=100)
    assert math.isfinite(length)
    assert length > 0.0


# ---------------------------------------------------------------------------
# 8. BSplineCurve — sample
# ---------------------------------------------------------------------------

def test_sample_returns_correct_count():
    """sample(n) returns exactly n Vec3 points."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), 0.0, 0.0) for i in range(5)]
    cfg = BSplineConfig(degree=3, num_control_points=5)
    curve = BSplineCurve(pts, cfg)
    samples = curve.sample(n=30)
    assert len(samples) == 30


def test_sample_each_point_is_vec3():
    """Each sample point is a 3-tuple."""
    BSplineConfig, BSplineCurve, _, _, _, _, _, _ = _mod()
    pts = [(float(i), float(i), 0.0) for i in range(5)]
    cfg = BSplineConfig(degree=3, num_control_points=5)
    curve = BSplineCurve(pts, cfg)
    for pt in curve.sample(n=10):
        assert len(pt) == 3


# ---------------------------------------------------------------------------
# 9. validate_constraints
# ---------------------------------------------------------------------------

def test_validate_constraints_clean():
    """No issues for valid, non-duplicate constraints."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    constraints = [
        ConstraintSpec(name="jl1", kind="joint_limit", threshold=1.57),
        ConstraintSpec(name="obs1", kind="obstacle", threshold=0.0),
        ConstraintSpec(name="vel1", kind="velocity", threshold=2.0),
    ]
    issues = backend.validate_constraints(constraints)
    assert issues == []


def test_validate_constraints_duplicate_name():
    """Duplicate constraint name should be reported as an issue."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    constraints = [
        ConstraintSpec(name="c1", kind="joint_limit", threshold=1.0),
        ConstraintSpec(name="c1", kind="velocity", threshold=2.0),
    ]
    issues = backend.validate_constraints(constraints)
    assert any("Duplicate" in msg or "duplicate" in msg.lower() for msg in issues)


def test_validate_constraints_unknown_kind():
    """Unknown constraint kind should be reported as an issue."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    constraints = [
        ConstraintSpec(name="bad_kind", kind="torque", threshold=1.0),  # type: ignore[arg-type]
    ]
    issues = backend.validate_constraints(constraints)
    assert any("torque" in msg or "Unknown" in msg or "unknown" in msg.lower() for msg in issues)


def test_validate_constraints_nonpositive_threshold_non_obstacle():
    """Non-obstacle constraint with threshold <= 0 is an issue."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    constraints = [
        ConstraintSpec(name="bad_thresh", kind="joint_limit", threshold=0.0),
    ]
    issues = backend.validate_constraints(constraints)
    assert len(issues) >= 1


def test_validate_constraints_obstacle_zero_threshold_ok():
    """obstacle kind with threshold=0 is valid."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    constraints = [
        ConstraintSpec(name="obs_zero", kind="obstacle", threshold=0.0),
    ]
    issues = backend.validate_constraints(constraints)
    assert issues == []


# ---------------------------------------------------------------------------
# 10. evaluate_constraint
# ---------------------------------------------------------------------------

def test_evaluate_constraint_soft_always_true():
    """Soft constraints always evaluate to True regardless of value."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    c = ConstraintSpec(name="soft_jl", kind="joint_limit", threshold=1.0, soft=True)
    assert backend.evaluate_constraint(c, observed_value=999.0) is True


def test_evaluate_constraint_hard_within_threshold():
    """Hard constraint passes when observed <= threshold."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    c = ConstraintSpec(name="hard_vel", kind="velocity", threshold=2.0, soft=False)
    assert backend.evaluate_constraint(c, observed_value=1.5) is True


def test_evaluate_constraint_hard_at_threshold():
    """Hard constraint passes exactly at the threshold."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    c = ConstraintSpec(name="hard_vel_eq", kind="velocity", threshold=2.0, soft=False)
    assert backend.evaluate_constraint(c, observed_value=2.0) is True


def test_evaluate_constraint_hard_above_threshold():
    """Hard constraint fails when observed > threshold."""
    _, _, ConstraintSpec, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    c = ConstraintSpec(name="hard_jerk", kind="jerk", threshold=0.5, soft=False)
    assert backend.evaluate_constraint(c, observed_value=0.7) is False


# ---------------------------------------------------------------------------
# 11. make_trajectory_from_waypoints
# ---------------------------------------------------------------------------

def test_make_trajectory_from_waypoints_returns_bspline():
    """make_trajectory_from_waypoints returns a BSplineCurve."""
    BSplineConfig, BSplineCurve, _, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    waypoints = [(float(i), 0.0, 0.0) for i in range(5)]
    traj = backend.make_trajectory_from_waypoints(waypoints, num_control_points=8)
    assert isinstance(traj, BSplineCurve)


def test_make_trajectory_from_waypoints_control_point_count():
    """The returned BSplineCurve has exactly num_control_points control points."""
    _, BSplineCurve, _, CuRoboV2Backend, _, _, _, _ = _mod()
    backend = CuRoboV2Backend()
    waypoints = [(float(i), float(i), 0.0) for i in range(4)]
    traj = backend.make_trajectory_from_waypoints(waypoints, num_control_points=6)
    assert len(traj.control_points) == 6


def test_make_trajectory_endpoints_near_waypoints():
    """With clamped knots the spline endpoints should be near the first/last waypoints."""
    _, BSplineCurve, _, CuRoboV2Backend, _, _, _, vec_distance = _mod()
    backend = CuRoboV2Backend()
    waypoints = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)]
    traj = backend.make_trajectory_from_waypoints(waypoints, num_control_points=8)
    start = traj.de_boor(0.0)
    end = traj.de_boor(1.0)
    assert vec_distance(start, waypoints[0]) == pytest.approx(0.0, abs=1e-9)
    assert vec_distance(end, waypoints[-1]) == pytest.approx(0.0, abs=1e-9)
