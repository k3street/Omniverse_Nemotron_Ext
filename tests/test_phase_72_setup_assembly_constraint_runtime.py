"""Phase 72 — setup_assembly_constraint runtime tests (SPEC/CONSTRAINT layer).

Gate: pytest — constraint validates required fields; satisfaction check tracks
per-constraint state; dry-run simulator advances state.

All tests are pure-Python; no Kit RPC or USD runtime required.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.setup_assembly_constraint_runtime import (
    CONSTRAINT_REQUIRED_PARAMS,
    PHASE_STATUS,
    AssemblyConstraint,
    AssemblyConstraintRuntime,
    ConstraintEvaluation,
    ConstraintTarget,
    expected_constraint_types,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(prim_path: str, feature: str = "origin") -> ConstraintTarget:
    return ConstraintTarget(prim_path=prim_path, feature=feature)  # type: ignore[arg-type]


def _make_distance_constraint(name: str, distance: float = 1.0) -> AssemblyConstraint:
    return AssemblyConstraint(
        name=name,
        type="distance_between",
        target_a=_make_target("/World/A"),
        target_b=_make_target("/World/B"),
        tolerance_m=0.005,
        params={"distance": distance},
    )


def _make_concentric_constraint(name: str) -> AssemblyConstraint:
    return AssemblyConstraint(
        name=name,
        type="concentric",
        target_a=_make_target("/World/C"),
        target_b=_make_target("/World/D"),
        tolerance_m=0.001,
    )


def _make_fixed_offset_constraint(
    name: str, offset: tuple = (1.0, 0.0, 0.0)
) -> AssemblyConstraint:
    return AssemblyConstraint(
        name=name,
        type="fixed_offset",
        target_a=_make_target("/World/E"),
        target_b=_make_target("/World/F"),
        tolerance_m=0.005,
        params={"offset": offset},
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_72_runtime_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == 72


def test_phase_72_runtime_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"


def test_phase_72_runtime_metadata_has_spec_ref():
    md = get_phase_metadata()
    assert "spec_ref" in md
    assert "72" in md["spec_ref"]


def test_phase_72_runtime_phase_status_constant():
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. expected_constraint_types ≥ 7 types
# ---------------------------------------------------------------------------


def test_phase_72_runtime_expected_constraint_types_count():
    types = expected_constraint_types()
    assert len(types) >= 7


def test_phase_72_runtime_expected_constraint_types_contains_required():
    types = expected_constraint_types()
    required = {
        "coincident_axes",
        "concentric",
        "tangent",
        "parallel_planes",
        "fixed_offset",
        "angle_between",
        "distance_between",
    }
    assert required.issubset(set(types))


# ---------------------------------------------------------------------------
# 3. AssemblyConstraint dataclass
# ---------------------------------------------------------------------------


def test_phase_72_runtime_assembly_constraint_dataclass():
    c = AssemblyConstraint(
        name="bolt_align",
        type="concentric",
        target_a=ConstraintTarget(prim_path="/World/Bolt", feature="axis_z"),
        target_b=ConstraintTarget(prim_path="/World/Hole", feature="axis_z"),
        tolerance_m=0.001,
        tolerance_rad=0.01,
    )
    assert c.name == "bolt_align"
    assert c.type == "concentric"
    assert c.target_a.prim_path == "/World/Bolt"
    assert c.target_b.feature == "axis_z"
    assert c.tolerance_m == 0.001


def test_phase_72_runtime_constraint_target_default_offset():
    t = ConstraintTarget(prim_path="/World/P")
    assert t.offset_m == (0.0, 0.0, 0.0)
    assert t.feature == "origin"


# ---------------------------------------------------------------------------
# 4. validate_constraint_spec: clean → empty issues
# ---------------------------------------------------------------------------


def test_phase_72_runtime_validate_clean_constraint():
    rt = AssemblyConstraintRuntime()
    c = _make_distance_constraint("clean_ok")
    issues = rt.validate_constraint_spec(c)
    assert issues == []


# ---------------------------------------------------------------------------
# 5. validate_constraint_spec: empty name → issue
# ---------------------------------------------------------------------------


def test_phase_72_runtime_validate_empty_name():
    rt = AssemblyConstraintRuntime()
    c = AssemblyConstraint(
        name="",
        type="concentric",
        target_a=_make_target("/World/A"),
        target_b=_make_target("/World/B"),
    )
    issues = rt.validate_constraint_spec(c)
    assert any("name" in i.lower() for i in issues)


def test_phase_72_runtime_validate_empty_prim_path_a():
    rt = AssemblyConstraintRuntime()
    c = AssemblyConstraint(
        name="bad_a",
        type="concentric",
        target_a=_make_target(""),
        target_b=_make_target("/World/B"),
    )
    issues = rt.validate_constraint_spec(c)
    assert any("target_a" in i for i in issues)


# ---------------------------------------------------------------------------
# 6. validate_constraint_spec: missing required params → issue
# ---------------------------------------------------------------------------


def test_phase_72_runtime_validate_missing_distance_param():
    rt = AssemblyConstraintRuntime()
    c = AssemblyConstraint(
        name="dist_no_param",
        type="distance_between",
        target_a=_make_target("/World/A"),
        target_b=_make_target("/World/B"),
        params={},  # missing "distance"
    )
    issues = rt.validate_constraint_spec(c)
    assert any("distance" in i for i in issues)


def test_phase_72_runtime_validate_missing_angle_param():
    rt = AssemblyConstraintRuntime()
    c = AssemblyConstraint(
        name="angle_no_param",
        type="angle_between",
        target_a=_make_target("/World/A"),
        target_b=_make_target("/World/B"),
        params={},  # missing "angle_rad"
    )
    issues = rt.validate_constraint_spec(c)
    assert any("angle_rad" in i for i in issues)


def test_phase_72_runtime_validate_missing_offset_param():
    rt = AssemblyConstraintRuntime()
    c = AssemblyConstraint(
        name="offset_no_param",
        type="fixed_offset",
        target_a=_make_target("/World/A"),
        target_b=_make_target("/World/B"),
        params={},  # missing "offset"
    )
    issues = rt.validate_constraint_spec(c)
    assert any("offset" in i for i in issues)


def test_phase_72_runtime_constraint_required_params_has_required_keys():
    assert "distance" in CONSTRAINT_REQUIRED_PARAMS["distance_between"]
    assert "angle_rad" in CONSTRAINT_REQUIRED_PARAMS["angle_between"]
    assert "offset" in CONSTRAINT_REQUIRED_PARAMS["fixed_offset"]


# ---------------------------------------------------------------------------
# 7. register + list_constraints
# ---------------------------------------------------------------------------


def test_phase_72_runtime_register_and_list():
    rt = AssemblyConstraintRuntime()
    c1 = _make_distance_constraint("c1")
    c2 = _make_concentric_constraint("c2")
    rt.register(c1)
    rt.register(c2)
    listed = rt.list_constraints()
    assert len(listed) == 2
    names = [c.name for c in listed]
    assert "c1" in names
    assert "c2" in names


def test_phase_72_runtime_register_overwrite():
    rt = AssemblyConstraintRuntime()
    c_old = _make_distance_constraint("same")
    c_new = _make_concentric_constraint("same")
    rt.register(c_old)
    rt.register(c_new)
    listed = rt.list_constraints()
    assert len(listed) == 1
    assert listed[0].type == "concentric"


# ---------------------------------------------------------------------------
# 8. unregister removes
# ---------------------------------------------------------------------------


def test_phase_72_runtime_unregister_removes():
    rt = AssemblyConstraintRuntime()
    c = _make_distance_constraint("removable")
    rt.register(c)
    rt.unregister("removable")
    assert rt.list_constraints() == []


def test_phase_72_runtime_unregister_unknown_raises():
    rt = AssemblyConstraintRuntime()
    with pytest.raises(KeyError):
        rt.unregister("nonexistent")


# ---------------------------------------------------------------------------
# 9. evaluate_one distance_between satisfied within tolerance
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_distance_satisfied():
    rt = AssemblyConstraintRuntime()
    # A at origin, B at (1.0, 0, 0) → actual distance = 1.0 m
    c = _make_distance_constraint("dist_ok", distance=1.0)
    rt.register(c)
    positions = {"/World/A": (0.0, 0.0, 0.0), "/World/B": (1.0, 0.0, 0.0)}
    ev = rt.evaluate_one("dist_ok", positions)
    assert isinstance(ev, ConstraintEvaluation)
    assert ev.satisfied is True
    assert ev.error_m < c.tolerance_m
    assert ev.distance_to_satisfaction == 0.0


# ---------------------------------------------------------------------------
# 10. evaluate_one distance_between violated outside tolerance
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_distance_violated():
    rt = AssemblyConstraintRuntime()
    # Expected 1.0, actual 2.0 → error = 1.0 >> tolerance 0.005
    c = _make_distance_constraint("dist_bad", distance=1.0)
    rt.register(c)
    positions = {"/World/A": (0.0, 0.0, 0.0), "/World/B": (2.0, 0.0, 0.0)}
    ev = rt.evaluate_one("dist_bad", positions)
    assert ev.satisfied is False
    assert ev.error_m > c.tolerance_m
    assert ev.distance_to_satisfaction > 0.0


# ---------------------------------------------------------------------------
# 11. evaluate_one concentric on coincident points → satisfied
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_concentric_satisfied():
    rt = AssemblyConstraintRuntime()
    c = _make_concentric_constraint("conc_ok")
    rt.register(c)
    # Both prims at same position
    positions = {"/World/C": (5.0, 3.0, 1.0), "/World/D": (5.0, 3.0, 1.0)}
    ev = rt.evaluate_one("conc_ok", positions)
    assert ev.satisfied is True
    assert ev.error_m == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 12. evaluate_one concentric on offset points → violated
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_concentric_violated():
    rt = AssemblyConstraintRuntime()
    c = _make_concentric_constraint("conc_bad")
    rt.register(c)
    # 10 mm apart — well outside 1 mm tolerance
    positions = {"/World/C": (0.0, 0.0, 0.0), "/World/D": (0.010, 0.0, 0.0)}
    ev = rt.evaluate_one("conc_bad", positions)
    assert ev.satisfied is False
    assert ev.error_m == pytest.approx(0.010, abs=1e-6)


# ---------------------------------------------------------------------------
# 13. evaluate_one fixed_offset satisfied
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_fixed_offset_satisfied():
    rt = AssemblyConstraintRuntime()
    c = _make_fixed_offset_constraint("offset_ok", offset=(2.0, 0.0, 0.0))
    rt.register(c)
    # B - A = (2.0, 0.0, 0.0) exactly — within 5 mm tolerance
    positions = {"/World/E": (1.0, 0.0, 0.0), "/World/F": (3.0, 0.0, 0.0)}
    ev = rt.evaluate_one("offset_ok", positions)
    assert ev.satisfied is True
    assert ev.error_m == pytest.approx(0.0, abs=1e-9)


def test_phase_72_runtime_evaluate_fixed_offset_violated():
    rt = AssemblyConstraintRuntime()
    c = _make_fixed_offset_constraint("offset_bad", offset=(2.0, 0.0, 0.0))
    rt.register(c)
    # B - A = (1.5, 0, 0) — 0.5 m error >> 5 mm tolerance
    positions = {"/World/E": (0.0, 0.0, 0.0), "/World/F": (1.5, 0.0, 0.0)}
    ev = rt.evaluate_one("offset_bad", positions)
    assert ev.satisfied is False
    assert ev.error_m == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# 14. evaluate_all returns one ConstraintEvaluation per registered constraint
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_all_count():
    rt = AssemblyConstraintRuntime()
    rt.register(_make_distance_constraint("d1"))
    rt.register(_make_concentric_constraint("d2"))
    rt.register(_make_fixed_offset_constraint("d3"))
    positions = {
        "/World/A": (0.0, 0.0, 0.0),
        "/World/B": (1.0, 0.0, 0.0),
        "/World/C": (0.0, 0.0, 0.0),
        "/World/D": (0.0, 0.0, 0.0),
        "/World/E": (0.0, 0.0, 0.0),
        "/World/F": (1.0, 0.0, 0.0),
    }
    results = rt.evaluate_all(positions)
    assert len(results) == 3
    for ev in results:
        assert isinstance(ev, ConstraintEvaluation)


def test_phase_72_runtime_evaluate_all_names_match():
    rt = AssemblyConstraintRuntime()
    rt.register(_make_distance_constraint("x1"))
    rt.register(_make_concentric_constraint("x2"))
    positions = {
        "/World/A": (0.0, 0.0, 0.0),
        "/World/B": (1.0, 0.0, 0.0),
        "/World/C": (0.0, 0.0, 0.0),
        "/World/D": (0.0, 0.0, 0.0),
    }
    results = rt.evaluate_all(positions)
    names = [ev.constraint_name for ev in results]
    assert "x1" in names
    assert "x2" in names


# ---------------------------------------------------------------------------
# 15. health_check returns required keys
# ---------------------------------------------------------------------------


def test_phase_72_runtime_health_check_keys():
    rt = AssemblyConstraintRuntime()
    hc = rt.health_check()
    assert "n_constraints" in hc
    assert "n_unique_prims" in hc
    assert "dry_run" in hc


def test_phase_72_runtime_health_check_dry_run_true():
    rt = AssemblyConstraintRuntime(dry_run=True)
    hc = rt.health_check()
    assert hc["dry_run"] is True


def test_phase_72_runtime_health_check_dry_run_false():
    rt = AssemblyConstraintRuntime(dry_run=False)
    hc = rt.health_check()
    assert hc["dry_run"] is False


def test_phase_72_runtime_health_check_counts():
    rt = AssemblyConstraintRuntime()
    rt.register(_make_distance_constraint("h1"))
    rt.register(_make_concentric_constraint("h2"))
    hc = rt.health_check()
    assert hc["n_constraints"] == 2
    # d1: /World/A, /World/B; concentric: /World/C, /World/D → 4 unique
    assert hc["n_unique_prims"] == 4


# ---------------------------------------------------------------------------
# 16. dry-run returns satisfied for Kit-dependent types
# ---------------------------------------------------------------------------


def test_phase_72_runtime_dry_run_coincident_axes_satisfied():
    rt = AssemblyConstraintRuntime(dry_run=True)
    c = AssemblyConstraint(
        name="coincident_dry",
        type="coincident_axes",
        target_a=_make_target("/World/X"),
        target_b=_make_target("/World/Y"),
    )
    rt.register(c)
    ev = rt.evaluate_one("coincident_dry", {})
    assert ev.satisfied is True
    assert ev.error_m == 0.0
    assert "dry-run" in ev.message


def test_phase_72_runtime_dry_run_parallel_planes_satisfied():
    rt = AssemblyConstraintRuntime(dry_run=True)
    c = AssemblyConstraint(
        name="pp_dry",
        type="parallel_planes",
        target_a=_make_target("/World/X"),
        target_b=_make_target("/World/Y"),
    )
    rt.register(c)
    ev = rt.evaluate_one("pp_dry", {})
    assert ev.satisfied is True


# ---------------------------------------------------------------------------
# 17. evaluate_one raises KeyError for unknown name
# ---------------------------------------------------------------------------


def test_phase_72_runtime_evaluate_unknown_raises():
    rt = AssemblyConstraintRuntime()
    with pytest.raises(KeyError):
        rt.evaluate_one("does_not_exist", {})
