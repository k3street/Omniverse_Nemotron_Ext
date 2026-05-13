"""Phase 80c contract tests — curved-belt + belt-junction geometry primitives."""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

MODULE = "service.isaac_assist_service.multimodal.sub_phase_80c_curved_belt_primitives"


def _import():
    """Lazy import so import errors surface inside the test that triggers them."""
    import importlib
    return importlib.import_module(MODULE)


def _make_quarter_circle(
    inner=0.5,
    outer=1.0,
    start_angle_deg=0.0,
    sweep_angle_deg=90.0,
    direction="ccw",
    speed=0.5,
):
    """Return a CurvedBeltSpec for a 90-degree arc with given parameters."""
    m = _import()
    return m.CurvedBeltSpec(
        name="test_belt",
        inner_radius_m=inner,
        outer_radius_m=outer,
        center_xy=(0.0, 0.0),
        start_angle_deg=start_angle_deg,
        sweep_angle_deg=sweep_angle_deg,
        belt_height_m=0.1,
        surface_speed_mps=speed,
        direction=direction,
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_80c_metadata():
    m = _import()
    md = m.get_phase_metadata()
    assert md["phase"] == "80c"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


def test_phase_status_constant():
    m = _import()
    assert m.PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. centerline_radius
# ---------------------------------------------------------------------------

def test_centerline_radius_average():
    """inner=0.5, outer=1.0 → centrelineradius=0.75."""
    m = _import()
    spec = _make_quarter_circle(inner=0.5, outer=1.0)
    geom = m.CurvedBeltGeometry(spec)
    assert geom.centerline_radius_m() == pytest.approx(0.75)


def test_centerline_radius_equal_radii():
    """Degenerate case: inner == outer → centrelineradius == that value."""
    m = _import()
    spec = _make_quarter_circle(inner=1.0, outer=1.0)
    geom = m.CurvedBeltGeometry(spec)
    assert geom.centerline_radius_m() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 3. arc_length
# ---------------------------------------------------------------------------

def test_arc_length_quarter_circle_unit_radius():
    """90-degree sweep, centerline_radius=1.0 → arc_length = π/2 ≈ 1.5708."""
    m = _import()
    # inner=0.5, outer=1.5 → centerline=1.0
    spec = m.CurvedBeltSpec(
        name="unit",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.0,
    )
    geom = m.CurvedBeltGeometry(spec)
    assert geom.arc_length_m() == pytest.approx(math.pi / 2, rel=1e-6)


def test_arc_length_half_circle():
    """180-degree sweep, centerline_radius=1.0 → arc_length = π."""
    m = _import()
    spec = m.CurvedBeltSpec(
        name="half",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=180.0,
        belt_height_m=0.0,
    )
    geom = m.CurvedBeltGeometry(spec)
    assert geom.arc_length_m() == pytest.approx(math.pi, rel=1e-6)


# ---------------------------------------------------------------------------
# 4. belt_width
# ---------------------------------------------------------------------------

def test_belt_width():
    m = _import()
    spec = _make_quarter_circle(inner=0.3, outer=0.8)
    geom = m.CurvedBeltGeometry(spec)
    assert geom.belt_width_m() == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 5. belt_area
# ---------------------------------------------------------------------------

def test_belt_area():
    """area = arc_length * belt_width."""
    m = _import()
    spec = _make_quarter_circle(inner=0.3, outer=0.8)
    geom = m.CurvedBeltGeometry(spec)
    expected = geom.arc_length_m() * geom.belt_width_m()
    assert geom.belt_area_m2() == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# 6. sample_centerline returns n_points
# ---------------------------------------------------------------------------

def test_sample_centerline_count():
    m = _import()
    spec = _make_quarter_circle()
    geom = m.CurvedBeltGeometry(spec)
    for n in (2, 8, 32, 100):
        pts = geom.sample_centerline(n_points=n)
        assert len(pts) == n, f"Expected {n} points, got {len(pts)}"


def test_sample_centerline_each_point_is_vec3():
    m = _import()
    spec = _make_quarter_circle()
    geom = m.CurvedBeltGeometry(spec)
    pts = geom.sample_centerline(n_points=16)
    for pt in pts:
        assert len(pt) == 3
        assert all(isinstance(v, float) for v in pt)


# ---------------------------------------------------------------------------
# 7. position_at(0.0) == start, position_at(1.0) == end
# ---------------------------------------------------------------------------

def test_position_at_start():
    """arc_param=0.0 should equal start angle on the centreline circle."""
    m = _import()
    # start_angle_deg=0 → start point should be at (centerline_radius, 0, height)
    spec = m.CurvedBeltSpec(
        name="start_test",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.2,
    )
    geom = m.CurvedBeltGeometry(spec)
    x, y, z = geom.position_at(0.0)
    assert x == pytest.approx(1.0, abs=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)
    assert z == pytest.approx(0.2, abs=1e-9)


def test_position_at_end():
    """arc_param=1.0 should equal start+sweep on the centreline circle."""
    m = _import()
    spec = m.CurvedBeltSpec(
        name="end_test",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.0,
    )
    geom = m.CurvedBeltGeometry(spec)
    x, y, z = geom.position_at(1.0)
    # end angle = 90 degrees → (0, centerline_radius, 0)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 8. tangent_at returns unit vector
# ---------------------------------------------------------------------------

def test_tangent_at_is_unit_vector():
    m = _import()
    spec = _make_quarter_circle()
    geom = m.CurvedBeltGeometry(spec)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        tx, ty, tz = geom.tangent_at(t)
        norm = math.sqrt(tx * tx + ty * ty + tz * tz)
        assert norm == pytest.approx(1.0, abs=1e-9), f"Tangent norm at t={t}: {norm}"


def test_tangent_at_start_ccw():
    """For a CCW belt starting at angle 0, the tangent at start should point +Y."""
    m = _import()
    spec = m.CurvedBeltSpec(
        name="tang_test",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.0,
        direction="ccw",
    )
    geom = m.CurvedBeltGeometry(spec)
    tx, ty, tz = geom.tangent_at(0.0)
    # At angle=0, d(cos θ)/dθ = 0, d(sin θ)/dθ = 1 → tangent is (0, 1, 0)
    assert tx == pytest.approx(0.0, abs=1e-9)
    assert ty == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 9. surface_speed_at: outer > inner
# ---------------------------------------------------------------------------

def test_surface_speed_outer_greater_than_inner():
    """Outer edge moves faster than inner edge on a curved belt."""
    m = _import()
    spec = _make_quarter_circle(inner=0.4, outer=1.2, speed=0.5)
    geom = m.CurvedBeltGeometry(spec)
    v_inner = geom.surface_speed_at(spec.inner_radius_m)
    v_outer = geom.surface_speed_at(spec.outer_radius_m)
    assert v_outer > v_inner


def test_surface_speed_proportional_to_radius():
    """surface_speed_at(r) == angular_speed * r."""
    m = _import()
    spec = _make_quarter_circle(inner=0.5, outer=1.5, speed=0.8)
    geom = m.CurvedBeltGeometry(spec)
    omega = geom.angular_speed_rad_per_s()
    for r in (0.3, 0.7, 1.0, 1.5):
        assert geom.surface_speed_at(r) == pytest.approx(omega * r, rel=1e-9)


# ---------------------------------------------------------------------------
# 10. angular_speed_rad_per_s correct
# ---------------------------------------------------------------------------

def test_angular_speed_correct():
    """omega = surface_speed / centerline_radius."""
    m = _import()
    spec = _make_quarter_circle(inner=0.5, outer=1.5, speed=1.0)
    geom = m.CurvedBeltGeometry(spec)
    # centerline = 1.0, speed = 1.0 → omega = 1.0
    assert geom.angular_speed_rad_per_s() == pytest.approx(1.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 11. BeltJunction.transfer_point
# ---------------------------------------------------------------------------

def test_belt_junction_transfer_point_no_drop():
    """transfer_point returns outgoing-belt start when drop_height_m == 0."""
    m = _import()
    incoming_spec = _make_quarter_circle(start_angle_deg=0.0, sweep_angle_deg=90.0)
    outgoing_spec = m.CurvedBeltSpec(
        name="out",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=90.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.1,
    )
    spec = m.BeltJunctionSpec(
        junction_id="jct1",
        incoming_belt="test_belt",
        outgoing_belt="out",
        transfer_angle_deg=0.0,
        drop_height_m=0.0,
    )
    jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
    tp = jct.transfer_point()
    out_geom = m.CurvedBeltGeometry(outgoing_spec)
    expected = out_geom.position_at(0.0)
    assert tp[0] == pytest.approx(expected[0], abs=1e-9)
    assert tp[1] == pytest.approx(expected[1], abs=1e-9)
    assert tp[2] == pytest.approx(expected[2], abs=1e-9)


def test_belt_junction_transfer_point_with_drop():
    """transfer_point Z is lowered by drop_height_m."""
    m = _import()
    incoming_spec = _make_quarter_circle()
    outgoing_spec = m.CurvedBeltSpec(
        name="out",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=90.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.5,
    )
    spec = m.BeltJunctionSpec(
        junction_id="jct_drop",
        incoming_belt="test_belt",
        outgoing_belt="out",
        transfer_angle_deg=5.0,
        drop_height_m=0.2,
    )
    jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
    _, _, tz = jct.transfer_point()
    out_geom = m.CurvedBeltGeometry(outgoing_spec)
    _, _, expected_z = out_geom.position_at(0.0)
    assert tz == pytest.approx(expected_z - 0.2, abs=1e-9)


# ---------------------------------------------------------------------------
# 12. is_continuous
# ---------------------------------------------------------------------------

def test_is_continuous_tight_junction():
    """Small gap + zero drop → continuous."""
    m = _import()
    incoming_spec = _make_quarter_circle()
    outgoing_spec = _make_quarter_circle(start_angle_deg=90.0)
    spec = m.BeltJunctionSpec(
        junction_id="tight",
        incoming_belt="test_belt",
        outgoing_belt="test_belt",
        transfer_angle_deg=0.0,
        drop_height_m=0.0,
        gap_m=0.004,
    )
    jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
    assert jct.is_continuous() is True


def test_is_continuous_large_gap():
    """Gap >= 0.01 → NOT continuous."""
    m = _import()
    incoming_spec = _make_quarter_circle()
    outgoing_spec = _make_quarter_circle(start_angle_deg=90.0)
    spec = m.BeltJunctionSpec(
        junction_id="gap",
        incoming_belt="test_belt",
        outgoing_belt="test_belt",
        transfer_angle_deg=0.0,
        drop_height_m=0.0,
        gap_m=0.05,
    )
    jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
    assert jct.is_continuous() is False


def test_is_continuous_large_drop():
    """Drop >= 0.01 → NOT continuous even with tiny gap."""
    m = _import()
    incoming_spec = _make_quarter_circle()
    outgoing_spec = _make_quarter_circle(start_angle_deg=90.0)
    spec = m.BeltJunctionSpec(
        junction_id="drop",
        incoming_belt="test_belt",
        outgoing_belt="test_belt",
        transfer_angle_deg=0.0,
        drop_height_m=0.05,
        gap_m=0.001,
    )
    jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
    assert jct.is_continuous() is False


# ---------------------------------------------------------------------------
# 13. transition_jerk_estimate non-negative
# ---------------------------------------------------------------------------

def test_transition_jerk_non_negative():
    """Jerk estimate is always >= 0."""
    m = _import()
    incoming_spec = _make_quarter_circle(speed=0.6)
    outgoing_spec = _make_quarter_circle(start_angle_deg=90.0, speed=0.6)
    for angle, drop, gap in [
        (0.0, 0.0, 0.005),
        (30.0, 0.0, 0.005),
        (90.0, 0.1, 0.02),
        (0.0, 0.5, 0.005),
    ]:
        spec = m.BeltJunctionSpec(
            junction_id="jerk_test",
            incoming_belt="test_belt",
            outgoing_belt="test_belt",
            transfer_angle_deg=angle,
            drop_height_m=drop,
            gap_m=gap,
        )
        jct = m.BeltJunction(spec, incoming_spec, outgoing_spec)
        assert jct.transition_jerk_estimate_mps2() >= 0.0


def test_transition_jerk_increases_with_angle():
    """Larger transfer angle → more jerk."""
    m = _import()
    incoming_spec = _make_quarter_circle(speed=1.0)
    outgoing_spec = _make_quarter_circle(start_angle_deg=90.0, speed=1.0)
    spec_small = m.BeltJunctionSpec(
        junction_id="jerk_small",
        incoming_belt="test_belt",
        outgoing_belt="test_belt",
        transfer_angle_deg=10.0,
    )
    spec_large = m.BeltJunctionSpec(
        junction_id="jerk_large",
        incoming_belt="test_belt",
        outgoing_belt="test_belt",
        transfer_angle_deg=60.0,
    )
    jerk_small = m.BeltJunction(spec_small, incoming_spec, outgoing_spec).transition_jerk_estimate_mps2()
    jerk_large = m.BeltJunction(spec_large, incoming_spec, outgoing_spec).transition_jerk_estimate_mps2()
    assert jerk_large > jerk_small


# ---------------------------------------------------------------------------
# 14. make_demo_factory_belt_layout returns 3 belts + 2 junctions
# ---------------------------------------------------------------------------

def test_demo_layout_counts():
    m = _import()
    belts, junctions = m.make_demo_factory_belt_layout()
    assert len(belts) == 3, f"Expected 3 belts, got {len(belts)}"
    assert len(junctions) == 2, f"Expected 2 junctions, got {len(junctions)}"


def test_demo_layout_belt_types():
    m = _import()
    belts, junctions = m.make_demo_factory_belt_layout()
    for b in belts:
        assert isinstance(b, m.CurvedBeltSpec)
    for j in junctions:
        assert isinstance(j, m.BeltJunctionSpec)


def test_demo_layout_junction_links():
    """Junction incoming/outgoing names reference existing belts."""
    m = _import()
    belts, junctions = m.make_demo_factory_belt_layout()
    belt_names = {b.name for b in belts}
    for j in junctions:
        assert j.incoming_belt in belt_names, f"{j.incoming_belt} not in belts"
        assert j.outgoing_belt in belt_names, f"{j.outgoing_belt} not in belts"


# ---------------------------------------------------------------------------
# 15. direction "cw" vs "ccw" produces flipped tangent
# ---------------------------------------------------------------------------

def test_cw_vs_ccw_tangent_flipped():
    """Switching direction from ccw to cw flips the tangent sign."""
    m = _import()
    spec_ccw = m.CurvedBeltSpec(
        name="ccw",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.0,
        direction="ccw",
    )
    spec_cw = m.CurvedBeltSpec(
        name="cw",
        inner_radius_m=0.5,
        outer_radius_m=1.5,
        center_xy=(0.0, 0.0),
        start_angle_deg=0.0,
        sweep_angle_deg=90.0,
        belt_height_m=0.0,
        direction="cw",
    )
    geom_ccw = m.CurvedBeltGeometry(spec_ccw)
    geom_cw = m.CurvedBeltGeometry(spec_cw)

    tx_ccw, ty_ccw, _ = geom_ccw.tangent_at(0.0)
    tx_cw, ty_cw, _ = geom_cw.tangent_at(0.0)

    # CW tangent should be the negation of CCW tangent in XY
    assert tx_cw == pytest.approx(-tx_ccw, abs=1e-9)
    assert ty_cw == pytest.approx(-ty_ccw, abs=1e-9)
