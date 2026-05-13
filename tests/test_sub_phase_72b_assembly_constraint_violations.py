"""Phase 72b — assembly constraint violation tests.

Gate: pytest — ConstraintViolation subclass for assembly constraints;
emits via Phase 11b channel.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.sub_phase_72b_assembly_constraint_violations import (
    AssemblyConstraint,
    AssemblyConstraintValidator,
    get_phase_metadata,
    validate_constraints,
)
from service.isaac_assist_service.types.violations import (
    ConstraintViolation,
    ValidationResult,
)
from service.isaac_assist_service.types.uncertainty import GradedScale


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_72b_metadata():
    md = get_phase_metadata()
    assert md["phase"] == "72b"
    assert md["status"] == "landed"
    assert "assembly" in md["title"].lower() or "constraint" in md["title"].lower()


# ---------------------------------------------------------------------------
# 2. Clean coincident passes
# ---------------------------------------------------------------------------


def test_clean_coincident_passes():
    c = AssemblyConstraint(
        constraint_id="c-001",
        kind="coincident",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is True
    assert result.n_hard == 0
    assert result.n_soft == 0
    assert result.violations == []


# ---------------------------------------------------------------------------
# 3. Missing prim_path_a fires hard ERROR
# ---------------------------------------------------------------------------


def test_missing_prim_path_a_fires_hard():
    c = AssemblyConstraint(
        constraint_id="c-002",
        kind="coincident",
        prim_path_a="",
        prim_path_b="/World/PartB",
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is False
    assert result.n_hard == 1
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.missing_prim_path" in ids
    v = next(v for v in result.violations if v.constraint_id == "assembly.missing_prim_path")
    assert v.category == "hard"
    assert v.severity == GradedScale.ERROR


# ---------------------------------------------------------------------------
# 4. Binary kind without prim_path_b fires hard ERROR
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["coincident", "concentric", "tangent", "parallel", "perpendicular", "distance", "angle"],
)
def test_binary_kind_missing_prim_path_b_fires_hard(kind):
    params: dict = {}
    if kind == "distance":
        params = {"distance_m": 1.0}
    if kind == "angle":
        params = {"angle_deg": 45.0}
    c = AssemblyConstraint(
        constraint_id=f"c-{kind}",
        kind=kind,  # type: ignore[arg-type]
        prim_path_a="/World/PartA",
        prim_path_b=None,
        parameters=params,
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is False
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.binary_kind_missing_prim_path_b" in ids
    v = next(v for v in result.violations if v.constraint_id == "assembly.binary_kind_missing_prim_path_b")
    assert v.category == "hard"


# ---------------------------------------------------------------------------
# 5. Distance kind without parameter fires hard ERROR
# ---------------------------------------------------------------------------


def test_distance_missing_parameter_fires_hard():
    c = AssemblyConstraint(
        constraint_id="c-003",
        kind="distance",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
        parameters={},
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is False
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.distance_missing_parameter" in ids
    v = next(v for v in result.violations if v.constraint_id == "assembly.distance_missing_parameter")
    assert v.category == "hard"
    assert v.severity == GradedScale.ERROR


def test_distance_non_positive_parameter_fires_hard():
    c = AssemblyConstraint(
        constraint_id="c-004",
        kind="distance",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
        parameters={"distance_m": -0.5},
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is False
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.distance_missing_parameter" in ids


def test_distance_positive_parameter_passes():
    c = AssemblyConstraint(
        constraint_id="c-005",
        kind="distance",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
        parameters={"distance_m": 0.25},
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is True
    assert result.n_hard == 0


# ---------------------------------------------------------------------------
# 6. Angle out of range emits soft WARNING (not hard)
# ---------------------------------------------------------------------------


def test_angle_out_of_range_soft_warning():
    c = AssemblyConstraint(
        constraint_id="c-006",
        kind="angle",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
        parameters={"angle_deg": 270.0},
    )
    result = AssemblyConstraintValidator().validate(c)
    # valid=True because no hard violations
    assert result.valid is True
    assert result.n_soft == 1
    assert result.n_hard == 0
    v = result.violations[0]
    assert v.constraint_id == "assembly.angle_out_of_range"
    assert v.category == "soft"
    assert v.severity == GradedScale.WARNING


def test_angle_in_range_passes():
    c = AssemblyConstraint(
        constraint_id="c-007",
        kind="angle",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartB",
        parameters={"angle_deg": -90.0},
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is True
    assert result.n_soft == 0


def test_angle_boundary_passes():
    for deg in [-180.0, 0.0, 180.0]:
        c = AssemblyConstraint(
            constraint_id=f"c-angle-{deg}",
            kind="angle",
            prim_path_a="/World/PartA",
            prim_path_b="/World/PartB",
            parameters={"angle_deg": deg},
        )
        result = AssemblyConstraintValidator().validate(c)
        assert result.n_soft == 0, f"angle_deg={deg} should be in range"


# ---------------------------------------------------------------------------
# 7. Self-reference fires hard ERROR
# ---------------------------------------------------------------------------


def test_self_reference_fires_hard():
    c = AssemblyConstraint(
        constraint_id="c-008",
        kind="parallel",
        prim_path_a="/World/PartA",
        prim_path_b="/World/PartA",
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is False
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.self_reference" in ids
    v = next(v for v in result.violations if v.constraint_id == "assembly.self_reference")
    assert v.category == "hard"
    assert v.severity == GradedScale.ERROR


# ---------------------------------------------------------------------------
# 8. Multiple constraints aggregate correctly
# ---------------------------------------------------------------------------


def test_multiple_constraints_aggregate():
    constraints = [
        # Valid
        AssemblyConstraint(
            constraint_id="agg-01",
            kind="coincident",
            prim_path_a="/World/A",
            prim_path_b="/World/B",
        ),
        # Hard error — missing prim_path_a
        AssemblyConstraint(
            constraint_id="agg-02",
            kind="tangent",
            prim_path_a="",
            prim_path_b="/World/B",
        ),
        # Soft warning — angle out of range
        AssemblyConstraint(
            constraint_id="agg-03",
            kind="angle",
            prim_path_a="/World/A",
            prim_path_b="/World/C",
            parameters={"angle_deg": 200.0},
        ),
    ]
    result = validate_constraints(constraints)
    assert result.valid is False  # because agg-02 has hard error
    assert result.n_hard >= 1
    assert result.n_soft >= 1


def test_validate_constraints_all_valid():
    constraints = [
        AssemblyConstraint(
            constraint_id=f"ok-{i}",
            kind="coincident",
            prim_path_a=f"/World/Part{i}A",
            prim_path_b=f"/World/Part{i}B",
        )
        for i in range(3)
    ]
    result = validate_constraints(constraints)
    assert result.valid is True
    assert result.n_hard == 0
    assert result.n_soft == 0


def test_validate_constraints_empty_list():
    result = validate_constraints([])
    assert result.valid is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# 9. Fixed kind does NOT require prim_path_b
# ---------------------------------------------------------------------------


def test_fixed_kind_does_not_require_prim_path_b():
    c = AssemblyConstraint(
        constraint_id="c-fixed-01",
        kind="fixed",
        prim_path_a="/World/PartA",
        prim_path_b=None,
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is True
    assert result.n_hard == 0
    ids = [v.constraint_id for v in result.violations]
    assert "assembly.binary_kind_missing_prim_path_b" not in ids


def test_ridebar_kind_does_not_require_prim_path_b():
    c = AssemblyConstraint(
        constraint_id="c-ridebar-01",
        kind="ridebar",
        prim_path_a="/World/Rail",
        prim_path_b=None,
    )
    result = AssemblyConstraintValidator().validate(c)
    assert result.valid is True
    assert result.n_hard == 0


# ---------------------------------------------------------------------------
# 10. ValidationResult carries ConstraintViolation instances (type check)
# ---------------------------------------------------------------------------


def test_violations_are_constraint_violation_instances():
    c = AssemblyConstraint(
        constraint_id="c-type-check",
        kind="concentric",
        prim_path_a="/World/PartA",
        prim_path_b=None,
    )
    result = AssemblyConstraintValidator().validate(c)
    for v in result.violations:
        assert isinstance(v, ConstraintViolation)
