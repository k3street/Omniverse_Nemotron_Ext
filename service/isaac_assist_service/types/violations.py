"""
Shared constraint-violation primitives for IA — Phase 11b.

This module unifies the "warning vs error" reporting shape that every
IA validator (patch validator, blueprint validator, route validators,
governance policy gates, diagnose dimension severities) was previously
emitting as ad-hoc dicts / tuples. Phase 11b ships the *type* — the
phase-by-phase migration of existing consumers is deferred.

`ConstraintViolation` carries one violation of one constraint; an
aggregate `ValidationResult` collects violations and computes a single
`valid` verdict: True iff there are zero `category="hard"` violations
in the list. Severity (the `GradedScale` from Phase 8c) is independent
— two `category="soft"` violations of `CRITICAL` severity do NOT flip
`valid`. Per spec:

    severity is for prioritisation, category is for blocking.

Zero internal IA dependencies beyond the Phase 8c primitives that this
package re-exports — see __init__.py for the import-purity contract.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from service.isaac_assist_service.types.uncertainty import GradedScale


# ---------------------------------------------------------------------------
# ConstraintViolation
# ---------------------------------------------------------------------------

class ConstraintViolation(BaseModel):
    """One violation of one constraint.

    Attributes:
        constraint_id: dotted constraint identifier, e.g.
            `"blueprint.aabb_overlap"` or `"patch.unknown_prim_path"`.
            Free-form by design — registries are owned by consumers.
        category: `"hard"` if this violation must block the operation,
            `"soft"` if it is advisory only. `ValidationResult.valid`
            looks ONLY at this field; severity is independent.
        severity: ordinal `GradedScale` (INFO < NOTICE < WARNING <
            ERROR < CRITICAL) used for sorting / prioritisation /
            chat-side rendering. The caller MUST pass an explicit
            value — there is no default.
        message: human-readable one-line description.
        affected_paths: USD prim paths the violation references. Empty
            list when the violation is scene-global rather than
            prim-specific.
        diagnostics: free-form per-constraint detail dictionary
            (numbers, mesh ids, link names — whatever the constraint
            wants to surface).
        fix_hint: optional one-line action suggestion the chat panel
            can show ("expand the bin by 0.05 m", "lower drive gain
            on shoulder_pan_joint").
    """

    model_config = ConfigDict(frozen=True)

    constraint_id: str = Field(min_length=1)
    category: Literal["hard", "soft"]
    severity: GradedScale
    message: str
    affected_paths: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    fix_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class ValidationResult(BaseModel):
    """Aggregate verdict from a validator.

    Build via `ValidationResult.from_violations(vs)` — the constructor
    computes `valid`, `n_hard`, and `n_soft` from the list. Direct
    construction with mismatched counts is allowed (Pydantic does not
    cross-validate fields), so callers that build manually MUST keep
    the counts in sync.

    `valid` is True iff *every* violation has `category="soft"`. Two
    soft violations of `CRITICAL` severity do NOT flip `valid` (per
    spec: severity is for prioritisation, category is for blocking).
    """

    model_config = ConfigDict(frozen=True)

    valid: bool
    violations: list[ConstraintViolation] = Field(default_factory=list)
    n_hard: int = 0
    n_soft: int = 0

    @classmethod
    def from_violations(
        cls, vs: list[ConstraintViolation]
    ) -> "ValidationResult":
        """Build a ValidationResult from a list of violations.

        `valid` is True iff no entry in `vs` has `category="hard"`.
        `n_hard` and `n_soft` count exactly the entries of each
        category; their sum equals `len(vs)`.
        """
        n_hard = sum(1 for v in vs if v.category == "hard")
        n_soft = sum(1 for v in vs if v.category == "soft")
        return cls(
            valid=(n_hard == 0),
            violations=list(vs),
            n_hard=n_hard,
            n_soft=n_soft,
        )
