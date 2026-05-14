"""Phase 54 — gap log: structured record of analytical-vs-sim deltas.

Every dual-estimate (Phase 53) appends a row to the gap log. The gap
analyzer (Phase 55) consumes this to identify systematic errors.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 54.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List


class GapLog:
    """In-memory log of analytical-vs-simulation delta records."""

    def __init__(self) -> None:
        """Initialise an empty gap log."""
        self._rows: List[Dict[str, Any]] = []

    def record(self, dimension: str, analytical: float, simulated: float,
                context: Dict[str, Any]) -> None:
        """Append one delta record to the log.

        Args:
            dimension (str): Dimension name, e.g. ``"cycle_time"``.
            analytical (float): Value produced by the analytical estimator.
            simulated (float): Value measured from the physics simulation.
            context (Dict[str, Any]): Arbitrary metadata (robot name, scenario, etc.).
        """
        self._rows.append({
            "dimension": dimension,
            "analytical_value": analytical,
            "simulated_value": simulated,
            "delta": simulated - analytical,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def rows(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of all logged rows in insertion order.

        Returns:
            List[Dict[str, Any]]: All gap-log records.
        """
        return list(self._rows)

    def for_dimension(self, dimension: str) -> List[Dict[str, Any]]:
        """Return all rows whose ``dimension`` field matches *dimension*.

        Args:
            dimension (str): Dimension name to filter by.

        Returns:
            List[Dict[str, Any]]: Matching rows, or an empty list.
        """
        return [r for r in self._rows if r["dimension"] == dimension]
