"""Phase 67 — Live spawn validation: create_articulated_joint post-checks.

Pure-function validator that operates on a synthetic ``JointPrimState`` dict
representing what ``create_articulated_joint`` should produce in USD.  No Kit
RPC or GPU dependency — the validator can be exercised entirely in unit tests.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 67.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 67
PHASE_TITLE = "Live spawn validation: create_articulated_joint"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 67",
    }


# ---------------------------------------------------------------------------
# Known joint types
# ---------------------------------------------------------------------------

_KNOWN_JOINT_TYPES: frozenset[str] = frozenset(
    {"revolute", "prismatic", "fixed", "spherical"}
)

# Joint types that require an explicit drive axis
_AXIS_REQUIRED_TYPES: frozenset[str] = frozenset({"revolute", "prismatic"})

# Joint types for which body1 is mandatory (warn only for fixed — fixed to
# world anchor is a valid use-case)
_BODY1_REQUIRED_TYPES: frozenset[str] = frozenset({"revolute", "prismatic", "spherical"})

_VALID_AXES: frozenset[str] = frozenset({"X", "Y", "Z"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class JointPrimState:
    """Synthetic schema representing what ``create_articulated_joint`` should
    produce, mimicking a USD-prim's typeName + attributes.

    Attributes
    ----------
    prim_path:
        USD path of the joint prim (e.g. ``/World/Robot/Joint_0``).
    joint_type:
        Joint flavour as a lowercase string.
    body0:
        Relationship target for the first rigid body (parent).
    body1:
        Relationship target for the second rigid body (child).
    axis:
        Drive axis — ``"X"``, ``"Y"``, or ``"Z"``.  Required for revolute
        and prismatic joints.
    lower_limit:
        Lower position / angle limit (degrees for revolute, metres for
        prismatic).
    upper_limit:
        Upper position / angle limit.
    articulation_root_path:
        USD path of the ArticulationRoot ancestor, if known.
    exists:
        Set to ``False`` to model the case where the prim was never created
        (e.g. Kit RPC returned an error).
    """

    prim_path: str
    joint_type: Literal["revolute", "prismatic", "fixed", "spherical"]
    body0: Optional[str] = None
    body1: Optional[str] = None
    axis: Optional[Literal["X", "Y", "Z"]] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    articulation_root_path: Optional[str] = None
    exists: bool = True


@dataclass
class JointValidationFinding:
    """Single check result emitted by :class:`JointSpawnValidator`.

    Attributes
    ----------
    check_id:
        Machine-readable identifier for the check (e.g. ``"prim_exists"``).
    severity:
        ``"error"`` halts acceptance; ``"warn"`` is advisory; ``"info"`` is
        informational only.
    message:
        Human-readable description suitable for error logs / UI display.
    """

    check_id: str
    severity: Literal["error", "warn", "info"]
    message: str


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class JointSpawnValidator:
    """Post-spawn validator for ``create_articulated_joint`` results.

    Parameters
    ----------
    strict:
        When ``True``, every ``"warn"``-severity finding is promoted to
        ``"error"``, making :meth:`passed` return ``False`` for any advisory
        issue.
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialise the joint validator.

        Args:
            strict (bool): When ``True``, every ``"warn"``-severity finding is
                promoted to ``"error"`` so :meth:`passed` returns ``False`` for
                any advisory issue.  Defaults to ``False``.
        """
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, state: JointPrimState) -> List[JointValidationFinding]:
        """Run all checks against *state* and return a list of findings.

        Checks run in a fixed order; all checks always execute (no early-exit)
        so callers receive a complete diagnostic picture in one pass.
        """
        findings: List[JointValidationFinding] = []

        self._check_prim_exists(state, findings)
        self._check_joint_type_known(state, findings)
        self._check_body0_set(state, findings)
        self._check_body1_set(state, findings)
        self._check_axis_set(state, findings)
        self._check_axis_valid(state, findings)
        self._check_limits_consistent(state, findings)
        self._check_articulation_root(state, findings)

        if self.strict:
            findings = [
                JointValidationFinding(
                    check_id=f.check_id,
                    severity="error" if f.severity == "warn" else f.severity,
                    message=f.message,
                )
                for f in findings
            ]

        return findings

    def validate_batch(
        self, states: List[JointPrimState]
    ) -> Dict[str, List[JointValidationFinding]]:
        """Validate a list of joint states and return a mapping keyed by
        ``prim_path``.
        """
        return {state.prim_path: self.validate(state) for state in states}

    @staticmethod
    def passed(findings: List[JointValidationFinding]) -> bool:
        """Return ``True`` iff there are no ``"error"``-severity findings."""
        return all(f.severity != "error" for f in findings)

    # ------------------------------------------------------------------
    # Individual checks (private helpers)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_prim_exists(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when the joint prim does not exist."""
        if not state.exists:
            findings.append(
                JointValidationFinding(
                    check_id="prim_exists",
                    severity="error",
                    message=(
                        f"Joint prim at '{state.prim_path}' was never created — "
                        "create_articulated_joint returned without a valid prim."
                    ),
                )
            )

    @staticmethod
    def _check_joint_type_known(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when ``joint_type`` is not in KNOWN_JOINT_TYPES."""
        if state.joint_type not in _KNOWN_JOINT_TYPES:
            findings.append(
                JointValidationFinding(
                    check_id="joint_type_known",
                    severity="error",
                    message=(
                        f"Unknown joint type '{state.joint_type}'. "
                        f"Expected one of: {sorted(_KNOWN_JOINT_TYPES)}."
                    ),
                )
            )

    @staticmethod
    def _check_body0_set(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when ``body0`` (parent rigid body) is not set."""
        if not state.body0:
            findings.append(
                JointValidationFinding(
                    check_id="body0_set",
                    severity="error",
                    message=(
                        f"body0 (parent rigid body) is not set on joint '{state.prim_path}'. "
                        "A joint must have at least one body relationship."
                    ),
                )
            )

    @staticmethod
    def _check_body1_set(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append a warning finding when ``body1`` (child rigid body) is absent where expected."""
        if state.joint_type in _BODY1_REQUIRED_TYPES and not state.body1:
            findings.append(
                JointValidationFinding(
                    check_id="body1_set",
                    severity="warn",
                    message=(
                        f"body1 (child rigid body) is not set on "
                        f"'{state.joint_type}' joint '{state.prim_path}'. "
                        "Missing body1 usually indicates an incomplete constraint."
                    ),
                )
            )
        elif state.joint_type == "fixed" and not state.body1:
            # Fixed joint anchored to world is valid — advisory only.
            findings.append(
                JointValidationFinding(
                    check_id="body1_set",
                    severity="warn",
                    message=(
                        f"body1 is not set on fixed joint '{state.prim_path}'. "
                        "This is valid for world-anchor constraints but may be "
                        "unintentional."
                    ),
                )
            )

    @staticmethod
    def _check_axis_set(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when ``axis`` is required for the joint type but not set."""
        if state.joint_type in _AXIS_REQUIRED_TYPES and not state.axis:
            findings.append(
                JointValidationFinding(
                    check_id="axis_set",
                    severity="error",
                    message=(
                        f"axis is required for '{state.joint_type}' joint "
                        f"'{state.prim_path}' but was not set."
                    ),
                )
            )

    @staticmethod
    def _check_axis_valid(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when the set ``axis`` value is not a valid axis token."""
        if state.axis is not None and state.axis not in _VALID_AXES:
            findings.append(
                JointValidationFinding(
                    check_id="axis_valid",
                    severity="error",
                    message=(
                        f"axis '{state.axis}' on joint '{state.prim_path}' is not valid. "
                        f"Must be one of: {sorted(_VALID_AXES)}."
                    ),
                )
            )

    @staticmethod
    def _check_limits_consistent(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append an error finding when ``lower_limit >= upper_limit``."""
        if state.lower_limit is not None and state.upper_limit is not None:
            if state.lower_limit >= state.upper_limit:
                findings.append(
                    JointValidationFinding(
                        check_id="limits_consistent",
                        severity="error",
                        message=(
                            f"Joint '{state.prim_path}' has inverted limits: "
                            f"lower_limit={state.lower_limit} >= upper_limit={state.upper_limit}. "
                            "lower_limit must be strictly less than upper_limit."
                        ),
                    )
                )

    @staticmethod
    def _check_articulation_root(
        state: JointPrimState, findings: List[JointValidationFinding]
    ) -> None:
        """Append a warning when no ``articulation_root_path`` was recorded for the joint."""
        if not state.articulation_root_path:
            findings.append(
                JointValidationFinding(
                    check_id="articulation_root",
                    severity="warn",
                    message=(
                        f"No articulation_root_path recorded for joint '{state.prim_path}'. "
                        "The joint may not participate in articulation simulation unless "
                        "an ArticulationRootAPI ancestor is present."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Spec-coverage helper
# ---------------------------------------------------------------------------


def expected_validator_checks() -> List[str]:
    """Return the ordered list of check_ids this validator implements.

    Useful for spec-coverage assertions — callers can verify every check
    documented in Phase 67 is present.
    """
    return [
        "prim_exists",
        "joint_type_known",
        "body0_set",
        "body1_set",
        "axis_set",
        "axis_valid",
        "limits_consistent",
        "articulation_root",
    ]
