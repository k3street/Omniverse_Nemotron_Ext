"""Phase 57 — patch validator runtime telemetry.

Each rule fire emits a telemetry record (rule_id, severity, code_hash,
hit/miss). Aggregated to track rule effectiveness in production.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 57.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List


class ValidatorTelemetry:
    """In-memory telemetry sink for patch-validator rule firings."""

    def __init__(self) -> None:
        """Initialise an empty telemetry store."""
        self._records: List[Dict[str, Any]] = []

    def record_fire(self, rule_id: str, severity: str, code_hash: str) -> None:
        """Record a single rule-fire event with a UTC timestamp.

        Args:
            rule_id (str): Identifier of the rule that fired, e.g. ``"R01_imports"``.
            severity (str): Severity level of the finding (e.g. ``"error"``, ``"warn"``).
            code_hash (str): Short hash of the patch code that triggered the rule.
        """
        self._records.append({
            "rule_id": rule_id,
            "severity": severity,
            "code_hash": code_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def rule_fire_count(self, rule_id: str) -> int:
        """Return the number of times *rule_id* has been recorded.

        Args:
            rule_id (str): Rule identifier to count.

        Returns:
            int: Number of matching records.
        """
        return sum(1 for r in self._records if r["rule_id"] == rule_id)

    def all_records(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of all telemetry records (oldest first).

        Returns:
            List[Dict[str, Any]]: All recorded rule-fire events.
        """
        return list(self._records)
