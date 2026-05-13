"""Phase 54 — gap log: structured record of analytical-vs-sim deltas.

Every dual-estimate (Phase 53) appends a row to the gap log. The gap
analyzer (Phase 55) consumes this to identify systematic errors.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 54.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List


class GapLog:
    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []

    def record(self, dimension: str, analytical: float, simulated: float,
                context: Dict[str, Any]) -> None:
        self._rows.append({
            "dimension": dimension,
            "analytical_value": analytical,
            "simulated_value": simulated,
            "delta": simulated - analytical,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def for_dimension(self, dimension: str) -> List[Dict[str, Any]]:
        return [r for r in self._rows if r["dimension"] == dimension]
