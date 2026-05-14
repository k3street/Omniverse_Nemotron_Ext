"""Phase 94b — KB freshness audit.

Audits knowledge-base files on disk and classifies each entry as fresh or
stale based on its filesystem modification time (mtime).  Provides:

- ``KBEntryAge``        — dataclass for a single document age record
- ``FreshnessReport``   — dataclass summarising a full audit run
- ``KBFreshnessAuditor``— audits a directory of .json / .md KB files
- ``audit_default_kb_dir`` — convenience wrapper

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 94b.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PHASE_ID = "94b"
PHASE_TITLE = "KB freshness audit"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 94b",
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class KBEntryAge:
    """Age record for a single knowledge-base document.

    Attributes:
        doc_id:      File stem (name without extension) used as the document
                     identifier.
        last_update: ISO-8601 string of the file's last modification time
                     (UTC).
        age_days:    Whole days since ``last_update``.
        stale:       ``True`` when ``age_days`` exceeds the audit threshold.
    """

    doc_id: str
    last_update: str
    age_days: int
    stale: bool


@dataclass
class FreshnessReport:
    """Summary produced by a single ``KBFreshnessAuditor.audit()`` run.

    Attributes:
        total_docs:           Total number of KB files scanned.
        fresh_docs:           Count of non-stale documents.
        stale_docs:           Count of stale documents.
        oldest_doc_id:        ``doc_id`` of the oldest document found, or
                              ``None`` when ``total_docs == 0``.
        oldest_age_days:      Age in days of the oldest document (0 when
                              ``total_docs == 0``).
        stale_threshold_days: The threshold that was applied during this run.
        scanned_at:           ISO-8601 UTC timestamp of when the audit ran.
    """

    total_docs: int
    fresh_docs: int
    stale_docs: int
    oldest_doc_id: Optional[str]
    oldest_age_days: int
    stale_threshold_days: int
    scanned_at: str


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class KBFreshnessAuditor:
    """Audit a directory of knowledge-base files for freshness.

    Scans all ``*.json`` and ``*.md`` files directly inside *kb_dir*
    (non-recursive) and classifies each as fresh or stale based on the
    file's mtime relative to *stale_threshold_days*.

    Usage::

        auditor = KBFreshnessAuditor(Path("/var/kb/docs"))
        report  = auditor.audit()
        stale   = auditor.list_stale()
    """

    _EXTENSIONS = {"*.json", "*.md"}

    def __init__(
        self,
        kb_dir: Path,
        stale_threshold_days: int = 90,
    ) -> None:
        """Initialise the auditor.

        Args:
            kb_dir (Path): Root directory containing knowledge-base documents
                (``*.json`` and ``*.md`` files are scanned recursively).
            stale_threshold_days (int): Number of days since last modification
                after which a document is considered stale.  Defaults to 90.
        """
        self._kb_dir = kb_dir
        self._threshold = stale_threshold_days

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit(self, now: datetime | None = None) -> FreshnessReport:
        """Walk *kb_dir* and return a :class:`FreshnessReport`.

        Args:
            now: Reference timestamp used as "current time" when computing
                 ages.  Defaults to ``datetime.now(timezone.utc)`` when
                 ``None``.  Pass an explicit value in tests to make
                 boundary-day assertions deterministic.

        Handles a missing or empty directory gracefully (all counts zero).
        """
        if now is None:
            now = datetime.now(timezone.utc)
        entries = self._scan(now=now)
        now_iso = now.isoformat()

        total = len(entries)
        stale_count = sum(1 for e in entries if e.stale)
        fresh_count = total - stale_count

        oldest_id: Optional[str] = None
        oldest_age = 0
        if entries:
            oldest = max(entries, key=lambda e: e.age_days)
            oldest_id = oldest.doc_id
            oldest_age = oldest.age_days

        return FreshnessReport(
            total_docs=total,
            fresh_docs=fresh_count,
            stale_docs=stale_count,
            oldest_doc_id=oldest_id,
            oldest_age_days=oldest_age,
            stale_threshold_days=self._threshold,
            scanned_at=now_iso,
        )

    def list_stale(self, now: datetime | None = None) -> List[KBEntryAge]:
        """Return only stale entries (age > threshold), sorted oldest-first.

        Args:
            now: Reference timestamp for age computation.  Defaults to
                 ``datetime.now(timezone.utc)`` when ``None``.
        """
        return sorted(
            (e for e in self._scan(now=now) if e.stale),
            key=lambda e: e.age_days,
            reverse=True,
        )

    def list_all(self, now: datetime | None = None) -> List[KBEntryAge]:
        """Return every entry (fresh + stale), sorted oldest-first.

        Args:
            now: Reference timestamp for age computation.  Defaults to
                 ``datetime.now(timezone.utc)`` when ``None``.
        """
        return sorted(
            self._scan(now=now),
            key=lambda e: e.age_days,
            reverse=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan(self, now: datetime | None = None) -> List[KBEntryAge]:
        """Collect :class:`KBEntryAge` records for every KB file found.

        Args:
            now: Reference timestamp used as "current time".  Defaults to
                 ``datetime.now(timezone.utc)`` when ``None``.
        """
        if not self._kb_dir.exists():
            return []

        if now is None:
            now = datetime.now(timezone.utc)
        results: List[KBEntryAge] = []

        for pattern in self._EXTENSIONS:
            for path in sorted(self._kb_dir.glob(pattern)):
                if not path.is_file():
                    continue
                mtime_ts = os.path.getmtime(path)
                mtime_dt = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
                age_days = (now - mtime_dt).days
                stale = age_days > self._threshold
                results.append(
                    KBEntryAge(
                        doc_id=path.stem,
                        last_update=mtime_dt.isoformat(),
                        age_days=age_days,
                        stale=stale,
                    )
                )

        return results


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def audit_default_kb_dir(
    kb_dir: Optional[Path] = None,
    threshold_days: int = 90,
) -> FreshnessReport:
    """Run a freshness audit against *kb_dir* (or a built-in default).

    Args:
        kb_dir:         Directory containing KB files.  When ``None`` a
                        sensible default relative to this module is used.
        threshold_days: Files older than this are classified as stale.

    Returns:
        A :class:`FreshnessReport` summarising the audit.
    """
    if kb_dir is None:
        kb_dir = Path(__file__).parent.parent.parent.parent / "data" / "kb"
    auditor = KBFreshnessAuditor(kb_dir=kb_dir, stale_threshold_days=threshold_days)
    return auditor.audit()
