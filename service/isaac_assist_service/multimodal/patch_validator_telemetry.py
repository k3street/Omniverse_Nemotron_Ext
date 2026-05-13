"""Phase 57 — patch validator runtime telemetry.

Each rule fire emits a telemetry record (rule_id, severity, code_hash,
hit/miss). Aggregated to track rule effectiveness in production.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 57.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List


class ValidatorTelemetry:
    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def record_fire(self, rule_id: str, severity: str, code_hash: str) -> None:
        self._records.append({
            "rule_id": rule_id,
            "severity": severity,
            "code_hash": code_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def rule_fire_count(self, rule_id: str) -> int:
        return sum(1 for r in self._records if r["rule_id"] == rule_id)

    def all_records(self) -> List[Dict[str, Any]]:
        return list(self._records)
