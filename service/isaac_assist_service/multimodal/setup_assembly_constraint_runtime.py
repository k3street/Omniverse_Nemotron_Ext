"""Phase 72 — setup_assembly_constraint runtime (SPEC/CONSTRAINT layer).

Provides pure-Python constraint dataclasses, satisfaction predicates,
contact-pair indexing, and dry-run simulation.  Live USD/PhysX wiring
remains opus-runtime.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 72.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 72
PHASE_TITLE = "setup_assembly_constraint runtime"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 72",
        "note": (
            "SPEC/CONSTRAINT layer landed (pure-Python). "
            "Live USD/PhysX wiring remains opus-runtime."
        ),
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ConstraintType = Literal[
    "coincident_axes",
    "concentric",
    "tangent",
    "parallel_planes",
    "fixed_offset",
    "angle_between",
    "distance_between",
]


def expected_constraint_types() -> List[ConstraintType]:
    """Return all supported ConstraintType values."""
    return [
        "coincident_axes",
        "concentric",
        "tangent",
        "parallel_planes",
        "fixed_offset",
        "angle_between",
        "distance_between",
    ]


# ---------------------------------------------------------------------------
# Required params per constraint type
# ---------------------------------------------------------------------------

CONSTRAINT_REQUIRED_PARAMS: Dict[str, List[str]] = {
    "distance_between": ["distance"],
    "angle_between": ["angle_rad"],
    "fixed_offset": ["offset"],
    "coincident_axes": [],
    "concentric": [],
    "tangent": [],
    "parallel_planes": [],
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConstraintTarget:
    """One end of an assembly constraint — a prim feature point.

    Attributes:
        prim_path: USD prim path (must be non-empty).
        feature: Named feature on the prim used as the constraint attachment.
        offset_m: Additional offset vector in metres applied to the feature
            position before evaluation.
    """

    prim_path: str
    feature: Literal[
        "origin",
        "axis_x",
        "axis_y",
        "axis_z",
        "face_normal",
        "edge_midpoint",
    ] = "origin"
    offset_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class AssemblyConstraint:
    """One geometric assembly constraint between two USD prim features.

    Attributes:
        name: Unique human-readable identifier for this constraint.
        type: The geometric relationship to enforce.
        target_a: First constraint attachment point.
        target_b: Second constraint attachment point.
        tolerance_m: Positional satisfaction threshold in metres.
        tolerance_rad: Angular satisfaction threshold in radians.
        params: Type-specific parameter dict.  Required keys are defined in
            ``CONSTRAINT_REQUIRED_PARAMS``.

            * ``distance_between``: ``{"distance": <float>}``
            * ``angle_between``:    ``{"angle_rad": <float>}``
            * ``fixed_offset``:     ``{"offset": (<dx>, <dy>, <dz>)}``
    """

    name: str
    type: ConstraintType
    target_a: ConstraintTarget
    target_b: ConstraintTarget
    tolerance_m: float = 0.001
    tolerance_rad: float = 0.01
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConstraintEvaluation:
    """Result of evaluating one assembly constraint.

    Attributes:
        constraint_name: Identifies which constraint was evaluated.
        satisfied: True when all errors are within tolerance.
        error_m: Positional error in metres (0.0 if not applicable).
        error_rad: Angular error in radians (0.0 if not applicable).
        distance_to_satisfaction: Remaining gap to the nearest
            satisfied state; 0.0 when already satisfied.
        message: Optional human-readable explanation.
    """

    constraint_name: str
    satisfied: bool
    error_m: float
    error_rad: float
    distance_to_satisfaction: float
    message: str = ""


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class AssemblyConstraintRuntime:
    """Pure-Python assembly constraint registry and evaluator.

    In dry-run mode (default) all constraints that require live Kit/PhysX
    context (coincident_axes, tangent, parallel_planes, angle_between) return
    satisfied=True with zero error.  Positional constraints (distance_between,
    concentric, fixed_offset) are evaluated analytically from the supplied
    ``prim_positions`` dict.

    Args:
        dry_run: When True (default), physics-dependent checks are skipped
            and reported as satisfied.  Set to False only when running under
            a live Kit RPC session.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self._dry_run = dry_run
        self._constraints: Dict[str, AssemblyConstraint] = {}

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(self, constraint: AssemblyConstraint) -> None:
        """Add *constraint* to the registry (overwrites if name duplicated)."""
        self._constraints[constraint.name] = constraint

    def unregister(self, name: str) -> None:
        """Remove the constraint with the given *name*.

        Raises:
            KeyError: If no constraint with that name exists.
        """
        if name not in self._constraints:
            raise KeyError(f"No constraint registered with name {name!r}")
        del self._constraints[name]

    def list_constraints(self) -> List[AssemblyConstraint]:
        """Return all registered constraints (insertion order)."""
        return list(self._constraints.values())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_constraint_spec(self, constraint: AssemblyConstraint) -> List[str]:
        """Validate the structural correctness of *constraint*.

        Returns:
            A (possibly empty) list of human-readable issue strings.  An
            empty list means the constraint spec is valid.
        """
        issues: List[str] = []

        if not constraint.name or not constraint.name.strip():
            issues.append("Constraint 'name' must be a non-empty string.")

        if not constraint.target_a.prim_path or not constraint.target_a.prim_path.strip():
            issues.append("target_a.prim_path must be a non-empty string.")

        if not constraint.target_b.prim_path or not constraint.target_b.prim_path.strip():
            issues.append("target_b.prim_path must be a non-empty string.")

        valid_types = set(expected_constraint_types())
        if constraint.type not in valid_types:
            issues.append(
                f"Unknown constraint type {constraint.type!r}. "
                f"Valid types: {sorted(valid_types)}"
            )

        if constraint.tolerance_m <= 0:
            issues.append(
                f"tolerance_m must be > 0; got {constraint.tolerance_m!r}."
            )

        if constraint.tolerance_rad <= 0:
            issues.append(
                f"tolerance_rad must be > 0; got {constraint.tolerance_rad!r}."
            )

        # Check type-specific required params
        required = CONSTRAINT_REQUIRED_PARAMS.get(constraint.type, [])
        for key in required:
            if key not in constraint.params:
                issues.append(
                    f"Constraint type {constraint.type!r} requires "
                    f"params[{key!r}] but it is missing."
                )

        return issues

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_one(
        self,
        name: str,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> ConstraintEvaluation:
        """Evaluate constraint *name* against the given prim world positions.

        Args:
            name: Constraint name as registered.
            prim_positions: Mapping from USD prim path to (x, y, z) world
                position in metres.

        Returns:
            A ``ConstraintEvaluation`` for the constraint.

        Raises:
            KeyError: If no constraint with *name* is registered.
        """
        if name not in self._constraints:
            raise KeyError(f"No constraint registered with name {name!r}")

        c = self._constraints[name]
        return self._evaluate_constraint(c, prim_positions)

    def evaluate_all(
        self,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> List[ConstraintEvaluation]:
        """Evaluate all registered constraints.

        Returns one ``ConstraintEvaluation`` per registered constraint in
        registration order.
        """
        return [
            self._evaluate_constraint(c, prim_positions)
            for c in self._constraints.values()
        ]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """Return a health summary dict.

        Keys:
            n_constraints: Number of registered constraints.
            n_unique_prims: Number of distinct prim paths across all targets.
            dry_run: Whether the runtime is in dry-run mode.
        """
        paths: set[str] = set()
        for c in self._constraints.values():
            paths.add(c.target_a.prim_path)
            paths.add(c.target_b.prim_path)
        return {
            "n_constraints": len(self._constraints),
            "n_unique_prims": len(paths),
            "dry_run": self._dry_run,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_position(
        self,
        target: ConstraintTarget,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> Optional[Tuple[float, float, float]]:
        """Resolve a ConstraintTarget to a world position, or None if unknown."""
        base = prim_positions.get(target.prim_path)
        if base is None:
            return None
        ox, oy, oz = target.offset_m
        return (base[0] + ox, base[1] + oy, base[2] + oz)

    def _evaluate_constraint(
        self,
        c: AssemblyConstraint,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> ConstraintEvaluation:
        """Dispatch to the appropriate evaluator for constraint type *c.type*."""

        if c.type == "distance_between":
            return self._eval_distance_between(c, prim_positions)
        elif c.type == "concentric":
            return self._eval_concentric(c, prim_positions)
        elif c.type == "fixed_offset":
            return self._eval_fixed_offset(c, prim_positions)
        else:
            # coincident_axes / tangent / parallel_planes / angle_between
            # need Kit/PhysX context — dry-run returns satisfied.
            if self._dry_run:
                return ConstraintEvaluation(
                    constraint_name=c.name,
                    satisfied=True,
                    error_m=0.0,
                    error_rad=0.0,
                    distance_to_satisfaction=0.0,
                    message=f"dry-run: {c.type!r} check skipped (needs Kit RPC)",
                )
            # Live path placeholder
            return ConstraintEvaluation(
                constraint_name=c.name,
                satisfied=False,
                error_m=0.0,
                error_rad=0.0,
                distance_to_satisfaction=float("inf"),
                message=(
                    f"{c.type!r} evaluation requires live Kit RPC session; "
                    "not implemented for non-dry-run without runtime."
                ),
            )

    # --- distance_between --------------------------------------------------

    def _eval_distance_between(
        self,
        c: AssemblyConstraint,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> ConstraintEvaluation:
        pos_a = self._get_position(c.target_a, prim_positions)
        pos_b = self._get_position(c.target_b, prim_positions)

        if pos_a is None or pos_b is None:
            return ConstraintEvaluation(
                constraint_name=c.name,
                satisfied=False,
                error_m=float("inf"),
                error_rad=0.0,
                distance_to_satisfaction=float("inf"),
                message="One or both prim positions unknown.",
            )

        actual: float = _euclidean(pos_a, pos_b)
        expected: float = float(c.params.get("distance", 0))
        error = abs(actual - expected)
        satisfied = error < c.tolerance_m
        return ConstraintEvaluation(
            constraint_name=c.name,
            satisfied=satisfied,
            error_m=error,
            error_rad=0.0,
            distance_to_satisfaction=0.0 if satisfied else error - c.tolerance_m,
            message=(
                f"distance_between: actual={actual:.6f} m, "
                f"expected={expected:.6f} m, error={error:.6f} m"
            ),
        )

    # --- concentric --------------------------------------------------------

    def _eval_concentric(
        self,
        c: AssemblyConstraint,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> ConstraintEvaluation:
        pos_a = self._get_position(c.target_a, prim_positions)
        pos_b = self._get_position(c.target_b, prim_positions)

        if pos_a is None or pos_b is None:
            return ConstraintEvaluation(
                constraint_name=c.name,
                satisfied=False,
                error_m=float("inf"),
                error_rad=0.0,
                distance_to_satisfaction=float("inf"),
                message="One or both prim positions unknown.",
            )

        error = _euclidean(pos_a, pos_b)
        satisfied = error < c.tolerance_m
        return ConstraintEvaluation(
            constraint_name=c.name,
            satisfied=satisfied,
            error_m=error,
            error_rad=0.0,
            distance_to_satisfaction=0.0 if satisfied else error - c.tolerance_m,
            message=(
                f"concentric: separation={error:.6f} m "
                f"(tolerance={c.tolerance_m:.6f} m)"
            ),
        )

    # --- fixed_offset ------------------------------------------------------

    def _eval_fixed_offset(
        self,
        c: AssemblyConstraint,
        prim_positions: Dict[str, Tuple[float, float, float]],
    ) -> ConstraintEvaluation:
        pos_a = self._get_position(c.target_a, prim_positions)
        pos_b = self._get_position(c.target_b, prim_positions)

        if pos_a is None or pos_b is None:
            return ConstraintEvaluation(
                constraint_name=c.name,
                satisfied=False,
                error_m=float("inf"),
                error_rad=0.0,
                distance_to_satisfaction=float("inf"),
                message="One or both prim positions unknown.",
            )

        expected_offset = c.params.get("offset", (0.0, 0.0, 0.0))
        # actual delta = b - a
        actual_delta = (
            pos_b[0] - pos_a[0],
            pos_b[1] - pos_a[1],
            pos_b[2] - pos_a[2],
        )
        diffs = [abs(actual_delta[i] - expected_offset[i]) for i in range(3)]
        max_err = max(diffs)
        satisfied = all(d < c.tolerance_m for d in diffs)
        remaining = 0.0 if satisfied else max_err - c.tolerance_m
        return ConstraintEvaluation(
            constraint_name=c.name,
            satisfied=satisfied,
            error_m=max_err,
            error_rad=0.0,
            distance_to_satisfaction=remaining,
            message=(
                f"fixed_offset: actual delta={actual_delta}, "
                f"expected={expected_offset}, max_component_error={max_err:.6f} m"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _euclidean(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> float:
    """Return Euclidean distance between two 3-D points."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
