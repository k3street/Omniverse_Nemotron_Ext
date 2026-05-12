"""
L0 tests for the generic constraint-violation framework (spec Phase 11b).

Covers the two Pydantic models in
`service.isaac_assist_service.types.violations`:

- `ConstraintViolation` — one violation of one constraint.
- `ValidationResult`     — aggregate verdict; `valid` derives from
  the `category="hard"` count, NOT from `severity`.

Key invariants exercised here:

1. Hard violations flip `valid` to False.
2. Soft violations leave `valid` True.
3. Severity is independent — two soft `CRITICAL` violations do NOT
   flip `valid`. (Per spec: severity prioritises, category blocks.)
4. JSON round-trip via `model_dump_json()` / `model_validate_json()`
   is lossless for both shapes.
5. Pydantic rejects categories outside the `Literal["hard", "soft"]`
   set, so typos like `"medium"` raise `ValidationError`.
6. `severity` has no default — the caller must pass a `GradedScale`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0

from service.isaac_assist_service.types import (
    ConstraintViolation,
    GradedScale,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# from_violations — category-driven `valid` verdict
# ---------------------------------------------------------------------------

class TestFromViolationsVerdict:
    def test_single_hard_flips_valid_to_false(self):
        """Spec §Test.1: one hard violation → valid=False, n_hard=1."""
        v = ConstraintViolation(
            constraint_id="x.y",
            category="hard",
            severity=GradedScale.ERROR,
            message="m",
        )
        r = ValidationResult.from_violations([v])
        assert r.valid is False
        assert r.n_hard == 1
        assert r.n_soft == 0
        assert len(r.violations) == 1

    def test_single_soft_keeps_valid_true(self):
        """Spec §Test.2: one soft violation → valid=True, n_soft=1."""
        v = ConstraintViolation(
            constraint_id="x.y",
            category="soft",
            severity=GradedScale.WARNING,
            message="m",
        )
        r = ValidationResult.from_violations([v])
        assert r.valid is True
        assert r.n_hard == 0
        assert r.n_soft == 1
        assert len(r.violations) == 1

    def test_mixed_two_hard_three_soft(self):
        """2 hard + 3 soft → valid=False, n_hard=2, n_soft=3, len=5."""
        vs = [
            ConstraintViolation(
                constraint_id=f"hard.{i}",
                category="hard",
                severity=GradedScale.ERROR,
                message=f"h{i}",
            )
            for i in range(2)
        ] + [
            ConstraintViolation(
                constraint_id=f"soft.{i}",
                category="soft",
                severity=GradedScale.WARNING,
                message=f"s{i}",
            )
            for i in range(3)
        ]
        r = ValidationResult.from_violations(vs)
        assert r.valid is False
        assert r.n_hard == 2
        assert r.n_soft == 3
        assert len(r.violations) == 5

    def test_empty_violations_list(self):
        """Empty input → valid=True with zero counts."""
        r = ValidationResult.from_violations([])
        assert r.valid is True
        assert r.n_hard == 0
        assert r.n_soft == 0
        assert r.violations == []


# ---------------------------------------------------------------------------
# Severity is independent of `valid`
# ---------------------------------------------------------------------------

class TestSeverityVsCategory:
    """Per spec: 'severity is for prioritisation, category is for
    blocking'. Two soft violations of CRITICAL severity must NOT flip
    `valid`.
    """

    def test_two_soft_criticals_keep_valid_true(self):
        vs = [
            ConstraintViolation(
                constraint_id="crit.a",
                category="soft",
                severity=GradedScale.CRITICAL,
                message="a",
            ),
            ConstraintViolation(
                constraint_id="crit.b",
                category="soft",
                severity=GradedScale.CRITICAL,
                message="b",
            ),
        ]
        r = ValidationResult.from_violations(vs)
        assert r.valid is True
        assert r.n_hard == 0
        assert r.n_soft == 2

    def test_one_hard_info_flips_valid_false(self):
        """The mirror case: a hard violation at the *lowest* severity
        still blocks. Category alone drives `valid`.
        """
        r = ValidationResult.from_violations([
            ConstraintViolation(
                constraint_id="trivial.but_blocking",
                category="hard",
                severity=GradedScale.INFO,
                message="m",
            )
        ])
        assert r.valid is False
        assert r.n_hard == 1


# ---------------------------------------------------------------------------
# Severity is required (no default inferred)
# ---------------------------------------------------------------------------

class TestSeverityIsRequired:
    def test_critical_accepted(self):
        v = ConstraintViolation(
            constraint_id="x",
            category="hard",
            severity=GradedScale.CRITICAL,
            message="m",
        )
        assert v.severity == GradedScale.CRITICAL

    def test_missing_severity_raises(self):
        """Severity has no default — caller must pass one."""
        with pytest.raises(ValidationError):
            ConstraintViolation(  # type: ignore[call-arg]
                constraint_id="x",
                category="hard",
                message="m",
            )


# ---------------------------------------------------------------------------
# Category Literal enforcement
# ---------------------------------------------------------------------------

class TestCategoryEnforcement:
    def test_invalid_category_rejected(self):
        """Spec uses `Literal['hard', 'soft']`; typos like 'medium'
        must raise ValidationError.
        """
        with pytest.raises(ValidationError):
            ConstraintViolation(
                constraint_id="x",
                category="medium",  # type: ignore[arg-type]
                severity=GradedScale.WARNING,
                message="m",
            )

    def test_hard_accepted(self):
        v = ConstraintViolation(
            constraint_id="x",
            category="hard",
            severity=GradedScale.ERROR,
            message="m",
        )
        assert v.category == "hard"

    def test_soft_accepted(self):
        v = ConstraintViolation(
            constraint_id="x",
            category="soft",
            severity=GradedScale.NOTICE,
            message="m",
        )
        assert v.category == "soft"


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:
    """Spec §Test.3: JSON round-trip via `model_dump_json()` works
    for both shapes.
    """

    def test_constraint_violation_round_trip(self):
        v = ConstraintViolation(
            constraint_id="blueprint.aabb_overlap",
            category="soft",
            severity=GradedScale.WARNING,
            message="bin overlaps conveyor end-stop by 0.012 m",
            affected_paths=["/World/Bin_3", "/World/Conveyor_A/EndStop"],
            diagnostics={"overlap_m": 0.012, "axis": "x"},
            fix_hint="shift /World/Bin_3 by (-0.02, 0, 0) m",
        )
        as_json = v.model_dump_json()
        v2 = ConstraintViolation.model_validate_json(as_json)
        assert v2 == v

    def test_constraint_violation_round_trip_minimal(self):
        """Minimal-fields round-trip (defaults for the optional fields)."""
        v = ConstraintViolation(
            constraint_id="x.y",
            category="hard",
            severity=GradedScale.ERROR,
            message="m",
        )
        v2 = ConstraintViolation.model_validate_json(v.model_dump_json())
        assert v2 == v
        assert v2.affected_paths == []
        assert v2.diagnostics == {}
        assert v2.fix_hint is None

    def test_validation_result_round_trip(self):
        vs = [
            ConstraintViolation(
                constraint_id="patch.unknown_path",
                category="hard",
                severity=GradedScale.ERROR,
                message="/World/Foo not found",
                affected_paths=["/World/Foo"],
            ),
            ConstraintViolation(
                constraint_id="blueprint.aabb_overlap",
                category="soft",
                severity=GradedScale.WARNING,
                message="minor overlap",
                diagnostics={"overlap_m": 0.005},
                fix_hint="ignore — under 1 cm",
            ),
        ]
        r = ValidationResult.from_violations(vs)
        as_json = r.model_dump_json()
        r2 = ValidationResult.model_validate_json(as_json)
        assert r2 == r
        # Spot-check the recomputed-on-build counts survived the trip.
        assert r2.valid is False
        assert r2.n_hard == 1
        assert r2.n_soft == 1
        assert len(r2.violations) == 2

    def test_validation_result_round_trip_empty(self):
        r = ValidationResult.from_violations([])
        r2 = ValidationResult.model_validate_json(r.model_dump_json())
        assert r2 == r
        assert r2.valid is True
        assert r2.violations == []
