"""Phase 66 — Live spawn validation: add_usd_reference post-checks.

Pure-function validator that operates on a synthetic ``USDReferenceState``
representing what ``add_usd_reference`` should produce in USD.  No Kit RPC
or GPU dependency — the validator is exercised entirely in unit tests.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 66.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 66
PHASE_TITLE = "Live spawn validation: add_usd_reference"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 66",
    }


# ---------------------------------------------------------------------------
# Valid USD-reference file extensions
# ---------------------------------------------------------------------------

_USD_EXTENSIONS: frozenset[str] = frozenset({".usd", ".usda", ".usdc", ".usdz"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class USDReferenceState:
    """Synthetic schema representing the result of ``add_usd_reference``.

    Attributes
    ----------
    prim_path:
        USD path of the prim that holds the reference
        (e.g. ``/World/Robot``).
    reference_target:
        Asset path or URL that was passed as the reference target
        (e.g. ``omniverse://localhost/Assets/robot.usd``).
    asset_exists:
        ``True`` when the resolver confirmed the asset is reachable.
        Set to ``False`` to model a dangling reference.
    asset_size_bytes:
        Size of the referenced asset in bytes.  ``0`` means unknown / not
        measured.
    prim_type_after:
        USD ``typeName`` of ``prim_path`` after the reference was applied.
        ``None`` means the reference did not yield a typed prim (e.g. the
        root-layer override never resolved a type).
    parent_path:
        USD path of the immediate parent prim.  Required when ``prim_path``
        is nested (contains more than one path component after the root
        slash).  ``None`` or empty means "no parent recorded".
    depth:
        Reference nesting depth — how many reference layers deep this
        reference sits.  ``1`` is a direct reference; deeper values occur
        when the referenced file itself contains further references.
    is_circular:
        ``True`` when the resolver detected a circular reference chain
        (A→B→A or longer cycle).
    """

    prim_path: str
    reference_target: str
    asset_exists: bool = True
    asset_size_bytes: int = 0
    prim_type_after: Optional[str] = None
    parent_path: Optional[str] = None
    depth: int = 1
    is_circular: bool = False


@dataclass
class USDReferenceFinding:
    """Single check result emitted by :class:`USDReferenceValidator`.

    Attributes
    ----------
    check_id:
        Machine-readable identifier for the check
        (e.g. ``"asset_exists"``).
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


class USDReferenceValidator:
    """Post-spawn validator for ``add_usd_reference`` results.

    Parameters
    ----------
    strict:
        When ``True``, every ``"warn"``-severity finding is promoted to
        ``"error"``, making :meth:`passed` return ``False`` for any advisory
        issue.
    max_depth:
        Maximum permitted reference nesting depth.  References deeper than
        this value trigger a ``depth_within_limit`` error.  Default ``8``.
    max_size_mb:
        Maximum permitted asset size in megabytes.  Assets larger than this
        value trigger an ``asset_too_large`` warning.  Default ``500``.
    """

    def __init__(
        self,
        strict: bool = False,
        max_depth: int = 8,
        max_size_mb: int = 500,
    ) -> None:
        self.strict = strict
        self.max_depth = max_depth
        self.max_size_mb = max_size_mb

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, state: USDReferenceState) -> List[USDReferenceFinding]:
        """Run all checks against *state* and return a list of findings.

        Checks run in a fixed order; all checks always execute (no early-exit)
        so callers receive a complete diagnostic picture in one pass.
        """
        findings: List[USDReferenceFinding] = []

        self._check_target_set(state, findings)
        self._check_asset_exists(state, findings)
        self._check_asset_too_large(state, findings)
        self._check_prim_type_resolved(state, findings)
        self._check_parent_exists(state, findings)
        self._check_depth_within_limit(state, findings)
        self._check_not_circular(state, findings)
        self._check_target_extension(state, findings)

        if self.strict:
            findings = [
                USDReferenceFinding(
                    check_id=f.check_id,
                    severity="error" if f.severity == "warn" else f.severity,
                    message=f.message,
                )
                for f in findings
            ]

        return findings

    def validate_batch(
        self, states: List[USDReferenceState]
    ) -> Dict[str, List[USDReferenceFinding]]:
        """Validate a list of reference states and return a mapping keyed by
        ``prim_path``.
        """
        return {state.prim_path: self.validate(state) for state in states}

    @staticmethod
    def passed(findings: List[USDReferenceFinding]) -> bool:
        """Return ``True`` iff there are no ``"error"``-severity findings."""
        return all(f.severity != "error" for f in findings)

    # ------------------------------------------------------------------
    # Individual checks (private helpers)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_target_set(
        state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """reference_target must be a non-empty string."""
        if not state.reference_target:
            findings.append(
                USDReferenceFinding(
                    check_id="target_set",
                    severity="error",
                    message=(
                        f"reference_target is empty on prim '{state.prim_path}'. "
                        "add_usd_reference requires a non-empty asset path or URL."
                    ),
                )
            )

    @staticmethod
    def _check_asset_exists(
        state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """The referenced asset must be resolvable (no dangling reference)."""
        if not state.asset_exists:
            findings.append(
                USDReferenceFinding(
                    check_id="asset_exists",
                    severity="error",
                    message=(
                        f"Asset '{state.reference_target}' referenced by prim "
                        f"'{state.prim_path}' could not be resolved. "
                        "The reference is dangling — check the path and Nucleus mount."
                    ),
                )
            )

    def _check_asset_too_large(
        self, state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """Warn when the asset exceeds the size budget."""
        limit_bytes = self.max_size_mb * 1024 * 1024
        if state.asset_size_bytes > limit_bytes:
            size_mb = state.asset_size_bytes / (1024 * 1024)
            findings.append(
                USDReferenceFinding(
                    check_id="asset_too_large",
                    severity="warn",
                    message=(
                        f"Asset '{state.reference_target}' is {size_mb:.1f} MB, "
                        f"which exceeds the {self.max_size_mb} MB advisory limit. "
                        "Large references can cause significant stage-load latency."
                    ),
                )
            )

    @staticmethod
    def _check_prim_type_resolved(
        state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """Warn when the reference did not yield a typed prim."""
        if state.prim_type_after is None:
            findings.append(
                USDReferenceFinding(
                    check_id="prim_type_resolved",
                    severity="warn",
                    message=(
                        f"Prim '{state.prim_path}' has no typeName after reference "
                        f"'{state.reference_target}' was applied. "
                        "The reference may not have resolved a concrete type — "
                        "verify the default-prim of the referenced layer."
                    ),
                )
            )

    @staticmethod
    def _is_nested(prim_path: str) -> bool:
        """Return True when prim_path has a parent other than the stage root."""
        # Paths like /World are top-level (parent = stage root pseudo-prim).
        # Paths like /World/Robot or /World/Robot/Link are nested.
        stripped = prim_path.lstrip("/")
        return "/" in stripped

    def _check_parent_exists(
        self, state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """When the prim is nested, parent_path must be recorded."""
        if self._is_nested(state.prim_path) and not state.parent_path:
            findings.append(
                USDReferenceFinding(
                    check_id="parent_exists",
                    severity="error",
                    message=(
                        f"Prim '{state.prim_path}' is nested but parent_path is "
                        "not recorded. The parent prim must exist before a "
                        "reference can be added to a child prim."
                    ),
                )
            )

    def _check_depth_within_limit(
        self, state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """Reference nesting depth must not exceed max_depth."""
        if state.depth > self.max_depth:
            findings.append(
                USDReferenceFinding(
                    check_id="depth_within_limit",
                    severity="error",
                    message=(
                        f"Reference depth {state.depth} on prim '{state.prim_path}' "
                        f"exceeds the maximum allowed depth of {self.max_depth}. "
                        "Deep reference chains degrade composition performance."
                    ),
                )
            )

    @staticmethod
    def _check_not_circular(
        state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """The reference chain must not contain a cycle."""
        if state.is_circular:
            findings.append(
                USDReferenceFinding(
                    check_id="not_circular",
                    severity="error",
                    message=(
                        f"Circular reference detected on prim '{state.prim_path}' "
                        f"via '{state.reference_target}'. "
                        "Circular references cause infinite composition loops and "
                        "must be removed immediately."
                    ),
                )
            )

    @staticmethod
    def _check_target_extension(
        state: USDReferenceState, findings: List[USDReferenceFinding]
    ) -> None:
        """Warn when reference_target does not end in a known USD extension."""
        if not state.reference_target:
            # target_set check already covers this; avoid double-reporting
            return
        lower = state.reference_target.lower()
        # Strip query strings / fragments that may appear in Nucleus URLs
        base = lower.split("?")[0].split("#")[0]
        has_usd_ext = any(base.endswith(ext) for ext in _USD_EXTENSIONS)
        if not has_usd_ext:
            findings.append(
                USDReferenceFinding(
                    check_id="target_extension",
                    severity="warn",
                    message=(
                        f"Reference target '{state.reference_target}' does not end "
                        f"with a recognised USD extension "
                        f"({', '.join(sorted(_USD_EXTENSIONS))}). "
                        "Verify the path is correct — non-USD files cannot be "
                        "composed by the USD runtime."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Spec-coverage helper
# ---------------------------------------------------------------------------


def expected_validator_checks() -> List[str]:
    """Return the ordered list of check_ids this validator implements.

    Useful for spec-coverage assertions — callers can verify every check
    documented in Phase 66 is present.
    """
    return [
        "target_set",
        "asset_exists",
        "asset_too_large",
        "prim_type_resolved",
        "parent_exists",
        "depth_within_limit",
        "not_circular",
        "target_extension",
    ]
