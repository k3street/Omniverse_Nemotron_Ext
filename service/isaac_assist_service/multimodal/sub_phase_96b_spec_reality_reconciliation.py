"""Phase 96b — Spec-reality reconciliation workflow.

Compares the declared state in ``specs/phase_metadata.yaml`` against what
actually exists on disk:

* ``find_status_mismatches``    — "landed" phases whose declared files are absent
* ``find_broken_blocker_chains`` — phases whose ``blocked_by`` IDs are not in metadata
* ``find_file_orphans``         — ``sub_phase_*.py`` / ``phase_*.py`` files on disk
                                  that no phase entry claims in its ``files`` list

``reconcile`` composes all three into a single ``ReconciliationReport``.
``default_reconcile`` is a convenience wrapper using the repo-default paths.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 96b.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "96b"
PHASE_TITLE = "Spec-reality reconciliation workflow"
PHASE_STATUS = "landed"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_METADATA_DEFAULT = _REPO_ROOT / "specs" / "phase_metadata.yaml"
_SCAN_DIR_DEFAULT = Path(__file__).resolve().parent


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 96b",
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SpecRealityFinding:
    """One discrepancy found between the spec and the filesystem state."""

    category: Literal["spec_orphan", "file_orphan", "status_mismatch", "blocker_chain_broken"]
    phase_id: Optional[str]
    detail: str
    severity: Literal["info", "warn", "error"]


@dataclass
class ReconciliationReport:
    """Aggregated result of a full spec-reality reconciliation pass."""

    total_phases: int
    total_findings: int
    by_category: Dict[str, int]
    findings: List[SpecRealityFinding]
    scanned_at: str  # ISO-8601 UTC timestamp


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------

class SpecRealityReconciler:
    """Reconciles ``phase_metadata.yaml`` against on-disk reality.

    Args:
        metadata_path: Path to ``specs/phase_metadata.yaml``.
        spec_path:     Optional path to the canonical spec markdown (not yet
                       used for active checks but retained for future orphan
                       line scanning).
    """

    def __init__(
        self,
        metadata_path: Path,
        spec_path: Optional[Path] = None,
    ) -> None:
        """Initialise the reconciler.

        Args:
            metadata_path (Path): Path to ``specs/phase_metadata.yaml``.
            spec_path (Path, optional): Path to the canonical spec markdown.
                Retained for future orphan-line scanning; not actively used.
        """
        self._metadata_path = Path(metadata_path)
        self._spec_path = Path(spec_path) if spec_path else None
        self._raw: Dict[str, Any] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load and cache the YAML metadata from disk (idempotent — no-op if already loaded)."""
        if self._loaded:
            return
        if self._metadata_path.exists():
            with self._metadata_path.open(encoding="utf-8") as fh:
                self._raw = yaml.safe_load(fh) or {}
        self._loaded = True

    def _phases(self) -> Dict[str, Any]:
        """Return the raw mapping of phase_id to phase_dict from the YAML metadata."""
        self._load()
        return {
            str(k): v
            for k, v in self._raw.items()
            if isinstance(v, dict)
        }

    def _all_declared_files(self) -> set[str]:
        """Collect every filename declared in any phase's ``files`` list."""
        declared: set[str] = set()
        for phase_dict in self._phases().values():
            for file_path in phase_dict.get("files") or []:
                # Store only the basename for matching against scan results.
                declared.add(Path(file_path).name)
        return declared

    # ------------------------------------------------------------------
    # Public check methods
    # ------------------------------------------------------------------

    def find_status_mismatches(self) -> List[SpecRealityFinding]:
        """For each "landed" phase, verify that its declared ``files`` exist.

        A landing phase with a non-empty ``files`` list where *none* of the
        declared files exist on disk gets a ``status_mismatch`` warning.
        Severity is ``warn`` rather than ``error`` because files may be
        intentionally loaded at runtime (e.g. template data files).

        Returns:
            List of :class:`SpecRealityFinding` with
            ``category="status_mismatch"`` and ``severity="warn"``.
        """
        findings: List[SpecRealityFinding] = []
        for phase_id, phase_dict in self._phases().items():
            if phase_dict.get("status") != "landed":
                continue
            files: List[str] = phase_dict.get("files") or []
            if not files:
                # Empty files list is fine — not all phases produce deliverable
                # files (e.g. pure-data or template phases).
                continue
            missing = [f for f in files if not _REPO_ROOT.joinpath(f).exists()]
            if len(missing) == len(files):
                # None of the declared files exist.
                detail = (
                    f"Phase {phase_id} is 'landed' but none of its declared "
                    f"files exist on disk: {missing}"
                )
                findings.append(
                    SpecRealityFinding(
                        category="status_mismatch",
                        phase_id=phase_id,
                        detail=detail,
                        severity="warn",
                    )
                )
        return findings

    def find_broken_blocker_chains(self) -> List[SpecRealityFinding]:
        """For each phase with ``blocked_by``, verify every blocker ID exists.

        If a blocker ID is absent from the metadata entirely, that is a
        structural error in the spec — the dependency graph is broken.

        Returns:
            List of :class:`SpecRealityFinding` with
            ``category="blocker_chain_broken"`` and ``severity="error"``.
        """
        phases = self._phases()
        findings: List[SpecRealityFinding] = []
        for phase_id, phase_dict in phases.items():
            blockers: List[Any] = phase_dict.get("blocked_by") or []
            for blocker_id in blockers:
                blocker_key = str(blocker_id)
                if blocker_key not in phases:
                    detail = (
                        f"Phase {phase_id} declares blocked_by={blocker_key!r} "
                        f"but that phase ID does not exist in metadata."
                    )
                    findings.append(
                        SpecRealityFinding(
                            category="blocker_chain_broken",
                            phase_id=phase_id,
                            detail=detail,
                            severity="error",
                        )
                    )
        return findings

    def find_file_orphans(self, scan_dir: Path) -> List[SpecRealityFinding]:
        """Find Python files on disk that no phase claims in its ``files`` list.

        Scans *scan_dir* for files matching ``sub_phase_*.py`` or
        ``phase_*.py`` and checks whether the filename appears in any phase's
        ``files`` list.

        Args:
            scan_dir: Directory to search (non-recursive).

        Returns:
            List of :class:`SpecRealityFinding` with
            ``category="file_orphan"`` and ``severity="info"``.
        """
        scan_dir = Path(scan_dir)
        declared_names = self._all_declared_files()
        findings: List[SpecRealityFinding] = []

        candidates = list(scan_dir.glob("sub_phase_*.py")) + list(
            scan_dir.glob("phase_*.py")
        )
        for candidate in sorted(candidates):
            if candidate.name not in declared_names:
                findings.append(
                    SpecRealityFinding(
                        category="file_orphan",
                        phase_id=None,
                        detail=(
                            f"File '{candidate}' matches phase-file pattern "
                            f"but is not referenced in any phase's 'files' list."
                        ),
                        severity="info",
                    )
                )
        return findings

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def reconcile(self, scan_dir: Optional[Path] = None) -> ReconciliationReport:
        """Run all checks and return a combined :class:`ReconciliationReport`.

        Args:
            scan_dir: Directory to scan for orphan files.  Defaults to the
                      directory containing this module.

        Returns:
            :class:`ReconciliationReport` with all findings and summary counts.
        """
        if scan_dir is None:
            scan_dir = _SCAN_DIR_DEFAULT

        all_findings: List[SpecRealityFinding] = []
        all_findings.extend(self.find_status_mismatches())
        all_findings.extend(self.find_broken_blocker_chains())
        all_findings.extend(self.find_file_orphans(scan_dir))

        by_category: Dict[str, int] = {}
        for finding in all_findings:
            by_category[finding.category] = by_category.get(finding.category, 0) + 1

        scanned_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return ReconciliationReport(
            total_phases=len(self._phases()),
            total_findings=len(all_findings),
            by_category=by_category,
            findings=all_findings,
            scanned_at=scanned_at,
        )


# ---------------------------------------------------------------------------
# Default convenience wrapper
# ---------------------------------------------------------------------------

def default_reconcile() -> ReconciliationReport:
    """Run reconciliation with repo-default paths.

    Uses:
    * ``specs/phase_metadata.yaml`` for metadata
    * The directory containing this module as the scan root
    """
    reconciler = SpecRealityReconciler(metadata_path=_METADATA_DEFAULT)
    return reconciler.reconcile(scan_dir=_SCAN_DIR_DEFAULT)
