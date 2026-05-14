"""Phase 83 — Governance: overnight patches run through policy_engine.

Every patch submitted outside business hours is evaluated by
``OvernightPatchPolicy``.  If the patch is also high-risk (any validation
issue with severity ERROR or CRITICAL) the policy vetoes execution and
requires human approval before the patch reaches Kit RPC.

``PolicyAuditLog`` provides a persistent NDJSON record of every policy
decision and a fast in-process view of recent vetoes.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 83.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PHASE_ID = 83
PHASE_TITLE = "Governance: every overnight patch runs through policy_engine"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 83",
    }


# ---------------------------------------------------------------------------
# OvernightPatchPolicy
# ---------------------------------------------------------------------------

class OvernightPatchPolicy:
    """Evaluate whether an overnight patch requires human approval.

    Parameters
    ----------
    business_hours_local:
        ``(start_hour, end_hour)`` in local time, both inclusive-exclusive
        (i.e. the default ``(8, 18)`` covers 08:00–17:59 local).  Patches
        submitted *outside* this window are considered overnight.

    Design notes
    ------------
    * ``is_overnight`` works on local time so that lab operators in any
      timezone get correct day/night semantics without additional config.
    * ``policy_check`` is pure: given the same arguments it always returns
      the same result.  No side-effects — audit-logging is the caller's job
      (see ``PolicyAuditLog``).
    """

    def __init__(
        self,
        business_hours_local: tuple[int, int] = (8, 18),
    ) -> None:
        """Configure the overnight-patch policy with local business hours.

        Args:
            business_hours_local (tuple[int, int], optional): Start and end hour
                (24-h clock, local time, end exclusive), e.g. ``(8, 18)`` covers
                08:00–17:59. Defaults to ``(8, 18)``.

        Raises:
            ValueError: If the tuple does not have exactly 2 elements, or if
                ``start >= end`` or values are outside [0, 24].
        """
        if len(business_hours_local) != 2:
            raise ValueError("business_hours_local must be a 2-tuple (start, end)")
        start, end = business_hours_local
        if not (0 <= start < end <= 24):
            raise ValueError(
                f"Invalid business hours {business_hours_local}; "
                "require 0 <= start < end <= 24"
            )
        self.business_hours_local = business_hours_local

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_overnight(self, timestamp_iso: Optional[str] = None) -> bool:
        """Return ``True`` if *timestamp_iso* falls outside business hours.

        Parameters
        ----------
        timestamp_iso:
            ISO-8601 string (e.g. ``"2026-05-13T02:30:00"``).  If *None*
            the current local time is used.  Timezone-aware strings are
            converted to local time for the hour comparison.
        """
        if timestamp_iso is not None:
            dt = datetime.fromisoformat(timestamp_iso)
            # If the string is timezone-aware, convert to local time so the
            # hour comparison is always in local-wall-clock terms.
            if dt.tzinfo is not None:
                dt = dt.astimezone()
        else:
            dt = datetime.now()

        start, end = self.business_hours_local
        return not (start <= dt.hour < end)

    def policy_check(
        self,
        patch_code: str,
        validation_issues: List[Any],
        timestamp_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate a patch against the overnight-governance policy.

        Parameters
        ----------
        patch_code:
            The patch source code being evaluated (reserved for future
            content-aware rules; not inspected by the current implementation).
        validation_issues:
            List of validation findings.  Each item may be a
            ``ConstraintViolation`` / ``ValidationIssue`` dataclass/Pydantic
            model, or a plain ``dict`` with a ``"severity"`` key.  Case-
            insensitive comparison is used so both ``"error"`` and ``"ERROR"``
            are treated identically.
        timestamp_iso:
            Timestamp of the patch submission.  If *None*, ``datetime.now()``
            is used (see ``is_overnight``).

        Returns
        -------
        dict with keys:

        ``overnight``
            ``bool`` — True if the timestamp falls outside business hours.
        ``high_risk``
            ``bool`` — True if any issue has severity in {ERROR, CRITICAL}.
        ``veto``
            ``bool`` — True when overnight AND high_risk.  A vetoed patch
            must not be forwarded to Kit RPC without human approval.
        ``reason``
            Human-readable string summarising the decision.
        ``required_action``
            One of ``"execute"`` | ``"require_human_approval"`` |
            ``"log_and_continue"``.

        *Action semantics*:

        * ``veto=True`` → ``"require_human_approval"``
        * ``veto=False, overnight=True`` → ``"log_and_continue"`` (low-risk
          overnight — execute after audit record)
        * Otherwise → ``"execute"``
        """
        overnight = self.is_overnight(timestamp_iso)
        high_risk = _has_high_risk_issue(validation_issues)
        veto = overnight and high_risk

        if veto:
            reason = (
                "Patch submitted outside business hours with high-risk validation "
                "issues (severity ERROR or CRITICAL). Human approval required."
            )
            required_action = "require_human_approval"
        elif overnight:
            reason = (
                "Patch submitted outside business hours but no high-risk issues "
                "detected. Execution permitted after audit log."
            )
            required_action = "log_and_continue"
        else:
            reason = "Patch submitted during business hours. Normal execution path."
            required_action = "execute"

        return {
            "overnight": overnight,
            "high_risk": high_risk,
            "veto": veto,
            "reason": reason,
            "required_action": required_action,
        }


# ---------------------------------------------------------------------------
# PolicyAuditLog
# ---------------------------------------------------------------------------

class PolicyAuditLog:
    """Persistent NDJSON audit log for policy decisions.

    Each call to ``record_decision`` appends one JSON line to the log file.
    ``recent_vetoes`` scans the in-memory append list (no disk re-read
    required for recent entries).

    Parameters
    ----------
    log_path:
        Path to the ``.ndjson`` log file.  The parent directory is created
        if it does not exist.
    """

    def __init__(self, log_path: Path) -> None:
        """Initialise the audit log, creating the parent directory if needed.

        Args:
            log_path (Path): Path to the ``.ndjson`` audit file; parent dirs
                are created automatically.
        """
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # In-memory list mirrors what was appended this session.
        self._records: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_decision(self, decision: Dict[str, Any]) -> None:
        """Append *decision* to the NDJSON audit log.

        A ``"recorded_at"`` timestamp (UTC ISO-8601) is injected if not
        already present so every row is independently timestamped.

        Parameters
        ----------
        decision:
            Typically the dict returned by ``OvernightPatchPolicy.policy_check``.
        """
        row: Dict[str, Any] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **decision,
        }
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        self._records.append(row)

    def recent_vetoes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent vetoed decisions (``veto=True``).

        Scans the in-memory list only — no disk read.  If the process was
        restarted and the in-memory list is empty, callers should re-read
        the NDJSON file directly.

        Parameters
        ----------
        limit:
            Maximum number of veto records to return, newest first.
        """
        vetoes = [r for r in self._records if r.get("veto")]
        return vetoes[-limit:]

    def all_records(self) -> List[Dict[str, Any]]:
        """Return all in-memory records (vetoed and non-vetoed)."""
        return list(self._records)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_high_risk_issue(validation_issues: List[Any]) -> bool:
    """Return True if any issue has severity ERROR or CRITICAL (case-insensitive)."""
    high_risk_levels = {"error", "critical"}
    for issue in validation_issues:
        sev = _extract_severity(issue)
        if sev is not None and sev.lower() in high_risk_levels:
            return True
    return False


def _extract_severity(issue: Any) -> Optional[str]:
    """Extract the severity string from a validation issue of any shape."""
    # Pydantic model / dataclass with .severity attribute
    sev = getattr(issue, "severity", None)
    if sev is not None:
        # GradedScale IntEnum: convert to name string
        if hasattr(sev, "name"):
            return sev.name
        return str(sev)
    # Plain dict
    if isinstance(issue, dict):
        sev = issue.get("severity")
        if sev is not None:
            if hasattr(sev, "name"):
                return sev.name
            return str(sev)
    return None
