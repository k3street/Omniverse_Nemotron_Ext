"""Phase 68 — Live spawn validation: apply_api_schema post-checks.

Pure-function validator that operates on a synthetic ``APISchemaState``
representing what ``apply_api_schema`` should produce in USD.  No Kit
RPC or GPU dependency — the validator can be exercised entirely in unit
tests.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 68.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 68
PHASE_TITLE = "Live spawn validation: apply_api_schema post-checks"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 68",
    }


# ---------------------------------------------------------------------------
# Known API schemas
# ---------------------------------------------------------------------------

KNOWN_API_SCHEMAS: set[str] = {
    "PhysicsRigidBodyAPI",
    "PhysicsCollisionAPI",
    "PhysicsMassAPI",
    "PhysicsArticulationRootAPI",
    "PhysicsRevoluteJoint",
    "PhysicsPrismaticJoint",
    "PhysxRigidBodyAPI",
    "PhysxJointAPI",
    "PhysxArticulationAPI",
    "MaterialBindingAPI",
    "SemanticsAPI",
    "KindAPI",
    "XformCommonAPI",
    "LightAPI",
    "MeshSimplificationAPI",
}

# ---------------------------------------------------------------------------
# Schema conflicts
# ---------------------------------------------------------------------------

#: Map from schema_name → list of schema names that must NOT be co-applied
#: on the **same** prim.
SCHEMA_CONFLICTS: Dict[str, List[str]] = {
    "PhysicsArticulationRootAPI": ["PhysicsRigidBodyAPI"],
    "PhysicsRevoluteJoint": ["PhysicsPrismaticJoint"],
    "PhysicsPrismaticJoint": ["PhysicsRevoluteJoint"],
}

# ---------------------------------------------------------------------------
# Required attributes per schema
# ---------------------------------------------------------------------------

#: Map from schema_name → list of attribute names that must be present
#: for the schema to be considered correctly applied.
SCHEMA_REQUIRED_ATTRS: Dict[str, List[str]] = {
    "PhysicsRigidBodyAPI": ["physics:rigidBodyEnabled"],
    "PhysicsMassAPI": ["physics:mass"],
    "PhysicsRevoluteJoint": ["physics:axis", "physics:body0"],
    "MaterialBindingAPI": ["material:binding"],
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class APISchemaState:
    """Synthetic schema representing the USD state after ``apply_api_schema``.

    Attributes
    ----------
    prim_path:
        USD path of the target prim (e.g. ``/World/Robot/Link0``).
    schema_name:
        The API schema that was applied (e.g. ``"PhysicsRigidBodyAPI"``).
    applied:
        ``True`` when the schema was successfully applied on the prim.
    required_attributes_present:
        List of required attribute names that are authored on the prim.
    required_attributes_missing:
        List of required attribute names that are absent from the prim.
    conflicts_with:
        List of schema names also applied on the same prim that may conflict
        with ``schema_name``.
    prim_type:
        USD type name of the target prim (e.g. ``"Xform"``, ``"Mesh"``).
        ``None`` if not recorded.
    """

    prim_path: str
    schema_name: str
    applied: bool = True
    required_attributes_present: List[str] = field(default_factory=list)
    required_attributes_missing: List[str] = field(default_factory=list)
    conflicts_with: List[str] = field(default_factory=list)
    prim_type: Optional[str] = None


@dataclass
class APISchemaFinding:
    """Single check result emitted by :class:`APISchemaValidator`.

    Attributes
    ----------
    check_id:
        Machine-readable identifier for the check (e.g. ``"schema_known"``).
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


class APISchemaValidator:
    """Post-spawn validator for ``apply_api_schema`` results.

    Parameters
    ----------
    strict:
        When ``True``, every ``"warn"``-severity finding is promoted to
        ``"error"``, making :meth:`passed` return ``False`` for any advisory
        issue.
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialise the validator.

        Args:
            strict (bool): When ``True``, every ``"warn"``-severity finding is
                promoted to ``"error"`` so :meth:`passed` returns ``False`` for
                any advisory issue.  Defaults to ``False``.
        """
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, state: APISchemaState) -> List[APISchemaFinding]:
        """Run all checks against *state* and return a list of findings.

        Checks run in a fixed order; all checks always execute (no early-exit)
        so callers receive a complete diagnostic picture in one pass.
        """
        findings: List[APISchemaFinding] = []

        self._check_schema_known(state, findings)
        self._check_applied(state, findings)
        self._check_required_attrs_present(state, findings)
        self._check_no_conflicts(state, findings)
        self._check_prim_type_set(state, findings)
        self._check_attr_count_matches(state, findings)

        if self.strict:
            findings = [
                APISchemaFinding(
                    check_id=f.check_id,
                    severity="error" if f.severity == "warn" else f.severity,
                    message=f.message,
                )
                for f in findings
            ]

        return findings

    def validate_batch(
        self, states: List[APISchemaState]
    ) -> Dict[str, List[APISchemaFinding]]:
        """Validate a list of API schema states and return a mapping keyed by
        ``prim_path``.
        """
        return {state.prim_path: self.validate(state) for state in states}

    @staticmethod
    def passed(findings: List[APISchemaFinding]) -> bool:
        """Return ``True`` iff there are no ``"error"``-severity findings."""
        return all(f.severity != "error" for f in findings)

    # ------------------------------------------------------------------
    # Individual checks (private helpers)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_schema_known(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append an error finding if ``state.schema_name`` is not in KNOWN_API_SCHEMAS."""
        if state.schema_name not in KNOWN_API_SCHEMAS:
            findings.append(
                APISchemaFinding(
                    check_id="schema_known",
                    severity="error",
                    message=(
                        f"Unknown API schema '{state.schema_name}' applied to "
                        f"'{state.prim_path}'. "
                        f"Expected one of: {sorted(KNOWN_API_SCHEMAS)}."
                    ),
                )
            )

    @staticmethod
    def _check_applied(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append an error finding if the schema was not successfully applied to the prim."""
        if not state.applied:
            findings.append(
                APISchemaFinding(
                    check_id="applied",
                    severity="error",
                    message=(
                        f"Schema '{state.schema_name}' was NOT successfully applied "
                        f"to prim '{state.prim_path}'. "
                        "apply_api_schema returned without marking the schema as applied."
                    ),
                )
            )

    @staticmethod
    def _check_required_attrs_present(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append an error finding if any required attributes are missing from the prim."""
        if state.required_attributes_missing:
            findings.append(
                APISchemaFinding(
                    check_id="required_attrs_present",
                    severity="error",
                    message=(
                        f"Schema '{state.schema_name}' on '{state.prim_path}' is "
                        f"missing required attributes: {state.required_attributes_missing}. "
                        "These must be authored for the schema to function correctly."
                    ),
                )
            )

    @staticmethod
    def _check_no_conflicts(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append an error finding if the schema conflicts with another co-applied schema."""
        conflict_partners = SCHEMA_CONFLICTS.get(state.schema_name, [])
        active_conflicts = [
            s for s in state.conflicts_with
            if s in KNOWN_API_SCHEMAS and s in conflict_partners
        ]
        if active_conflicts:
            findings.append(
                APISchemaFinding(
                    check_id="no_conflicts",
                    severity="error",
                    message=(
                        f"Schema '{state.schema_name}' on '{state.prim_path}' "
                        f"conflicts with co-applied schemas: {active_conflicts}. "
                        "These schemas must not be applied together on the same prim."
                    ),
                )
            )

    @staticmethod
    def _check_prim_type_set(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append a warning finding if ``prim_type`` is ``None`` (aids diagnostics)."""
        if state.prim_type is None:
            findings.append(
                APISchemaFinding(
                    check_id="prim_type_set",
                    severity="warn",
                    message=(
                        f"prim_type is not set for prim '{state.prim_path}'. "
                        "Recording the USD type name aids diagnostics and schema "
                        "compatibility checks."
                    ),
                )
            )

    @staticmethod
    def _check_attr_count_matches(
        state: APISchemaState, findings: List[APISchemaFinding]
    ) -> None:
        """Append an informational finding comparing present vs. expected attribute count."""
        expected_attrs = SCHEMA_REQUIRED_ATTRS.get(state.schema_name, [])
        if not expected_attrs:
            return
        present_count = len(state.required_attributes_present)
        expected_count = len(expected_attrs)
        if present_count >= expected_count:
            findings.append(
                APISchemaFinding(
                    check_id="attr_count_matches",
                    severity="info",
                    message=(
                        f"Schema '{state.schema_name}' on '{state.prim_path}' "
                        f"has {present_count}/{expected_count} required attributes "
                        "authored — all accounted for."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Spec-coverage helper
# ---------------------------------------------------------------------------


def expected_validator_checks() -> List[str]:
    """Return the ordered list of check_ids this validator implements.

    Useful for spec-coverage assertions — callers can verify every check
    documented in Phase 68 is present.
    """
    return [
        "schema_known",
        "applied",
        "required_attrs_present",
        "no_conflicts",
        "prim_type_set",
        "attr_count_matches",
    ]
