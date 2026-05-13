"""Phase 72b — assembly constraint violations.

Provides `AssemblyConstraintValidator`, a concrete validator that extends the
Phase 11b `ConstraintViolation` / `ValidationResult` framework.  It checks
five conditions on `AssemblyConstraint` instances:

1. **assembly.missing_prim_path** (hard/ERROR): `prim_path_a` is empty.
2. **assembly.binary_kind_missing_prim_path_b** (hard/ERROR): a binary
   constraint kind (coincident / concentric / tangent / parallel /
   perpendicular / distance / angle) requires a non-empty `prim_path_b`.
3. **assembly.distance_missing_parameter** (hard/ERROR): the ``distance``
   kind requires `parameters["distance_m"]` to be a positive float.
4. **assembly.angle_out_of_range** (soft/WARNING): the ``angle`` kind
   requires `parameters["angle_deg"]` in [-180, 180].
5. **assembly.self_reference** (hard/ERROR): `prim_path_a == prim_path_b`
   for binary constraint kinds.

`validate_constraints` aggregates multiple constraints into a single
`ValidationResult`.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 72b.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from service.isaac_assist_service.types.violations import (
    ConstraintViolation,
    ValidationResult,
)
from service.isaac_assist_service.types.uncertainty import GradedScale


PHASE_ID = "72b"
PHASE_TITLE = "assembly constraint violations"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 72b",
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AssemblyConstraintKind = Literal[
    "coincident",
    "concentric",
    "tangent",
    "parallel",
    "perpendicular",
    "distance",
    "angle",
    "fixed",
    "ridebar",
]

# Kinds that require a second prim (prim_path_b).
_BINARY_KINDS: frozenset[str] = frozenset(
    {
        "coincident",
        "concentric",
        "tangent",
        "parallel",
        "perpendicular",
        "distance",
        "angle",
    }
)


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass
class AssemblyConstraint:
    """One assembly constraint between one or two USD prims.

    Attributes:
        constraint_id: Unique identifier for this constraint instance.
        kind: The geometric relationship this constraint enforces.
        prim_path_a: USD prim path for the first (primary) entity.
        prim_path_b: USD prim path for the second entity.  Required for
            binary constraint kinds; may be ``None`` for unary kinds
            (``fixed``, ``ridebar``).
        parameters: Kind-specific parameter dict.  ``distance`` kind
            requires ``{"distance_m": <positive float>}``; ``angle``
            kind requires ``{"angle_deg": <float in [-180, 180]>}``.
    """

    constraint_id: str
    kind: AssemblyConstraintKind
    prim_path_a: str
    prim_path_b: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class AssemblyConstraintValidator:
    """Validate an `AssemblyConstraint` against five structural checks.

    Usage::

        validator = AssemblyConstraintValidator()
        result = validator.validate(constraint)
        if not result.valid:
            for v in result.violations:
                if v.category == "hard":
                    ...
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, constraint: AssemblyConstraint) -> ValidationResult:
        """Validate *constraint* and return an aggregate `ValidationResult`.

        Checks are applied in this order:

        1. missing_prim_path (hard — short-circuits when prim_path_a empty)
        2. binary_kind_missing_prim_path_b (hard)
        3. distance_missing_parameter (hard)
        4. angle_out_of_range (soft)
        5. self_reference (hard)
        """
        violations: list[ConstraintViolation] = []

        # Check 1 — missing prim_path_a (hard, short-circuits)
        missing = self._check_missing_prim_path(constraint)
        if missing is not None:
            violations.append(missing)
            return ValidationResult.from_violations(violations)

        # Check 2 — binary kind needs prim_path_b
        binary_missing = self._check_binary_kind_needs_prim_path_b(constraint)
        if binary_missing is not None:
            violations.append(binary_missing)

        # Check 3 — distance parameter
        dist_err = self._check_distance_needs_parameter(constraint)
        if dist_err is not None:
            violations.append(dist_err)

        # Check 4 — angle range (soft)
        angle_warn = self._check_angle_range(constraint)
        if angle_warn is not None:
            violations.append(angle_warn)

        # Check 5 — self-reference
        self_ref = self._check_self_reference(constraint)
        if self_ref is not None:
            violations.append(self_ref)

        return ValidationResult.from_violations(violations)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_missing_prim_path(
        self, c: AssemblyConstraint
    ) -> ConstraintViolation | None:
        """Check 1: empty prim_path_a → hard ERROR."""
        if not c.prim_path_a:
            return ConstraintViolation(
                constraint_id="assembly.missing_prim_path",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Constraint '{c.constraint_id}' has an empty "
                    "prim_path_a; a USD prim path is required."
                ),
                diagnostics={"constraint_id": c.constraint_id, "kind": c.kind},
                fix_hint="Provide a valid USD prim path for prim_path_a.",
            )
        return None

    def _check_binary_kind_needs_prim_path_b(
        self, c: AssemblyConstraint
    ) -> ConstraintViolation | None:
        """Check 2: binary kind without prim_path_b → hard ERROR."""
        if c.kind in _BINARY_KINDS and not c.prim_path_b:
            return ConstraintViolation(
                constraint_id="assembly.binary_kind_missing_prim_path_b",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Constraint '{c.constraint_id}' has kind '{c.kind}' "
                    "which requires prim_path_b, but prim_path_b is missing "
                    "or empty."
                ),
                affected_paths=[c.prim_path_a],
                diagnostics={"constraint_id": c.constraint_id, "kind": c.kind},
                fix_hint=(
                    f"Provide a USD prim path for prim_path_b when using "
                    f"constraint kind '{c.kind}'."
                ),
            )
        return None

    def _check_distance_needs_parameter(
        self, c: AssemblyConstraint
    ) -> ConstraintViolation | None:
        """Check 3: distance kind requires parameters["distance_m"] > 0 → hard ERROR."""
        if c.kind != "distance":
            return None
        raw = c.parameters.get("distance_m")
        # Must be present and a positive number.
        try:
            val = float(raw)  # type: ignore[arg-type]
            ok = val > 0
        except (TypeError, ValueError):
            ok = False
            val = raw

        if not ok:
            return ConstraintViolation(
                constraint_id="assembly.distance_missing_parameter",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Constraint '{c.constraint_id}' (kind='distance') requires "
                    f"parameters['distance_m'] to be a positive float; got {val!r}."
                ),
                affected_paths=[p for p in [c.prim_path_a, c.prim_path_b] if p],
                diagnostics={
                    "constraint_id": c.constraint_id,
                    "distance_m": raw,
                },
                fix_hint="Set parameters['distance_m'] to a positive float (metres).",
            )
        return None

    def _check_angle_range(
        self, c: AssemblyConstraint
    ) -> ConstraintViolation | None:
        """Check 4: angle kind requires parameters["angle_deg"] in [-180, 180] → soft WARNING."""
        if c.kind != "angle":
            return None
        raw = c.parameters.get("angle_deg")
        try:
            val = float(raw)  # type: ignore[arg-type]
            in_range = -180.0 <= val <= 180.0
        except (TypeError, ValueError):
            return None  # malformed — not the angle-range check's concern

        if not in_range:
            return ConstraintViolation(
                constraint_id="assembly.angle_out_of_range",
                category="soft",
                severity=GradedScale.WARNING,
                message=(
                    f"Constraint '{c.constraint_id}' (kind='angle') has "
                    f"angle_deg={val!r} which is outside the valid range "
                    "[-180, 180]."
                ),
                affected_paths=[p for p in [c.prim_path_a, c.prim_path_b] if p],
                diagnostics={
                    "constraint_id": c.constraint_id,
                    "angle_deg": val,
                },
                fix_hint="Clamp angle_deg to the range [-180, 180].",
            )
        return None

    def _check_self_reference(
        self, c: AssemblyConstraint
    ) -> ConstraintViolation | None:
        """Check 5: prim_path_a == prim_path_b for binary kinds → hard ERROR."""
        if c.kind not in _BINARY_KINDS:
            return None
        if c.prim_path_b and c.prim_path_a == c.prim_path_b:
            return ConstraintViolation(
                constraint_id="assembly.self_reference",
                category="hard",
                severity=GradedScale.ERROR,
                message=(
                    f"Constraint '{c.constraint_id}' references the same prim "
                    f"'{c.prim_path_a}' on both sides; a constraint must link "
                    "two distinct prims."
                ),
                affected_paths=[c.prim_path_a],
                diagnostics={
                    "constraint_id": c.constraint_id,
                    "kind": c.kind,
                    "prim_path": c.prim_path_a,
                },
                fix_hint=(
                    "Set prim_path_b to a different prim than prim_path_a."
                ),
            )
        return None


# ---------------------------------------------------------------------------
# Aggregate helper
# ---------------------------------------------------------------------------


def validate_constraints(
    constraints: List[AssemblyConstraint],
) -> ValidationResult:
    """Validate a list of `AssemblyConstraint` objects and aggregate results.

    Each constraint is validated independently; all violations are merged
    into a single `ValidationResult`.  The result is `valid` iff there are
    zero hard violations across all constraints.
    """
    validator = AssemblyConstraintValidator()
    all_violations: list[ConstraintViolation] = []
    for c in constraints:
        result = validator.validate(c)
        all_violations.extend(result.violations)
    return ValidationResult.from_violations(all_violations)
