"""Phase 80 contract tests — surface gripper force/hold-capacity model."""
from __future__ import annotations

import math
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Convenience import helpers
# ---------------------------------------------------------------------------

def _import_module():
    from service.isaac_assist_service.multimodal import surface_gripper_suction as m
    return m


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

def test_phase_80_model_metadata():
    m = _import_module()
    md = m.get_phase_metadata()
    assert md["phase"] == 80
    assert md["status"] == "landed"
    assert md["title"] == "Surface gripper + suction modeling"
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. SuctionCupSpec defaults
# ---------------------------------------------------------------------------

def test_suction_cup_spec_defaults():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0)
    assert spec.cup_count == 1
    assert spec.max_vacuum_kpa == 80.0
    assert spec.flow_rate_lpm == 100.0
    assert spec.cup_material == "nitrile"


# ---------------------------------------------------------------------------
# 3. compute_cup_area — single 50 mm diameter cup (r=25 mm)
# ---------------------------------------------------------------------------

def test_compute_cup_area_single_50mm():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1)
    model = m.SuctionGripperModel(spec)
    area = model.compute_cup_area_m2()
    expected = math.pi * (0.025) ** 2
    assert area == pytest.approx(expected, rel=1e-6)
    # roughly 0.00196 m²
    assert abs(area - 0.00196) < 1e-4


# ---------------------------------------------------------------------------
# 4. compute_cup_area — quad 25 mm diameter cups (r=12.5 mm, n=4)
# ---------------------------------------------------------------------------

def test_compute_cup_area_quad_25mm():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=12.5, cup_count=4)
    model = m.SuctionGripperModel(spec)
    area = model.compute_cup_area_m2()
    expected = 4 * math.pi * (0.0125) ** 2
    assert area == pytest.approx(expected, rel=1e-6)
    # Same total area as single 25mm-radius cup
    single_area = math.pi * (0.025) ** 2
    assert area == pytest.approx(single_area, rel=1e-6)


# ---------------------------------------------------------------------------
# 5. compute_holding_force — glass at 80 kPa, single 50 mm cup
# ---------------------------------------------------------------------------

def test_holding_force_glass_50mm():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    force = model.compute_holding_force_N("glass")
    # F = π × 0.025² × 80000 × 1.0 ≈ 157.08 N
    expected = math.pi * (0.025) ** 2 * 80_000 * 1.0
    assert force == pytest.approx(expected, rel=1e-6)
    assert 150 < force < 165  # sanity band


# ---------------------------------------------------------------------------
# 6. compute_holding_force — fabric much lower than glass (effectiveness 0.4)
# ---------------------------------------------------------------------------

def test_holding_force_fabric_much_lower_than_glass():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    force_glass = model.compute_holding_force_N("glass")
    force_fabric = model.compute_holding_force_N("fabric")
    # glass eff=1.0, fabric eff=0.4 → ratio should be exactly 0.4/1.0
    assert force_fabric == pytest.approx(force_glass * 0.4, rel=1e-6)
    assert force_fabric < force_glass


# ---------------------------------------------------------------------------
# 7. recommended_payload_kg — safety_factor=2
# ---------------------------------------------------------------------------

def test_recommended_payload_safety_factor_2():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    force = model.compute_holding_force_N("glass")
    payload = model.recommended_payload_kg("glass", safety_factor=2.0)
    assert payload == pytest.approx(force / (2.0 * 9.81), rel=1e-6)


# ---------------------------------------------------------------------------
# 8. leak_risk for glass = low
# ---------------------------------------------------------------------------

def test_leak_risk_glass_is_low():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0)
    model = m.SuctionGripperModel(spec)
    assert model.leak_risk_for("glass") == "low"
    assert model.leak_risk_for("smooth_plastic") == "low"


# ---------------------------------------------------------------------------
# 9. leak_risk for fabric = high
# ---------------------------------------------------------------------------

def test_leak_risk_fabric_is_high():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0)
    model = m.SuctionGripperModel(spec)
    assert model.leak_risk_for("fabric") == "high"
    assert model.leak_risk_for("wet_surface") == "high"
    assert model.leak_risk_for("porous") == "high"


# ---------------------------------------------------------------------------
# 10. leak_risk medium materials
# ---------------------------------------------------------------------------

def test_leak_risk_medium_materials():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0)
    model = m.SuctionGripperModel(spec)
    assert model.leak_risk_for("rough_metal") == "medium"
    assert model.leak_risk_for("cardboard") == "medium"


# ---------------------------------------------------------------------------
# 11. evaluate returns GripForceResult with safety_margin > 0
# ---------------------------------------------------------------------------

def test_evaluate_returns_grip_force_result():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    result = model.evaluate("smooth_plastic")
    assert isinstance(result, m.GripForceResult)
    assert result.holding_force_N > 0
    assert result.safety_margin > 0
    assert result.recommended_payload_kg > 0
    assert result.leak_risk == "low"
    assert len(result.notes) > 0


# ---------------------------------------------------------------------------
# 12. GRIPPER_TYPE_REGISTRY has ≥8 entries
# ---------------------------------------------------------------------------

def test_gripper_registry_has_at_least_8_entries():
    m = _import_module()
    assert len(m.GRIPPER_TYPE_REGISTRY) >= 8


# ---------------------------------------------------------------------------
# 13. get_gripper returns a SuctionCupSpec
# ---------------------------------------------------------------------------

def test_get_gripper_single_50mm_silicone():
    m = _import_module()
    spec = m.get_gripper("single_50mm_silicone")
    assert isinstance(spec, m.SuctionCupSpec)
    assert spec.cup_radius_mm == 25.0
    assert spec.cup_count == 1
    assert spec.cup_material == "silicone"


# ---------------------------------------------------------------------------
# 14. get_gripper raises KeyError for unknown names
# ---------------------------------------------------------------------------

def test_get_gripper_nonexistent_raises():
    m = _import_module()
    with pytest.raises(KeyError):
        m.get_gripper("nonexistent_gripper_xyz")


# ---------------------------------------------------------------------------
# 15. list_grippers returns a sorted list
# ---------------------------------------------------------------------------

def test_list_grippers_sorted():
    m = _import_module()
    names = m.list_grippers()
    assert names == sorted(names)
    assert len(names) >= 8
    assert "single_50mm_silicone" in names


# ---------------------------------------------------------------------------
# 16. vacuum_pct=0.5 halves the holding force
# ---------------------------------------------------------------------------

def test_vacuum_pct_half_halves_force():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    force_full = model.compute_holding_force_N("glass", vacuum_pct=1.0)
    force_half = model.compute_holding_force_N("glass", vacuum_pct=0.5)
    assert force_half == pytest.approx(force_full * 0.5, rel=1e-6)


# ---------------------------------------------------------------------------
# 17. evaluate with explicit payload_kg sets safety_margin correctly
# ---------------------------------------------------------------------------

def test_evaluate_with_explicit_payload():
    m = _import_module()
    spec = m.SuctionCupSpec(cup_radius_mm=25.0, cup_count=1, max_vacuum_kpa=80.0)
    model = m.SuctionGripperModel(spec)
    force_n = model.compute_holding_force_N("glass")
    payload_kg = 5.0
    result = model.evaluate("glass", payload_kg=payload_kg)
    expected_margin = force_n / (payload_kg * 9.81)
    assert result.safety_margin == pytest.approx(expected_margin, rel=1e-6)


# ---------------------------------------------------------------------------
# 18. All 8 registry entries produce positive force on smooth_plastic
# ---------------------------------------------------------------------------

def test_all_registry_entries_produce_positive_force():
    m = _import_module()
    for name, spec in m.GRIPPER_TYPE_REGISTRY.items():
        model = m.SuctionGripperModel(spec)
        force = model.compute_holding_force_N("smooth_plastic")
        assert force > 0, f"Gripper '{name}' produced zero/negative force"
