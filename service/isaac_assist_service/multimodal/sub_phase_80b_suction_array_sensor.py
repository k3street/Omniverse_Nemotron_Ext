"""Phase 80b — suction array sensor.

Models an array of N suction cups, each with its own vacuum reading.
Provides pickup detection, pattern classification, and centroid computation.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 80b.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


PHASE_ID = "80b"
PHASE_TITLE = "suction array sensor"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 80b",
    }


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SuctionCup:
    """Static configuration for a single suction cup in the array."""

    cup_id: str
    position_mm: Tuple[float, float, float]
    radius_mm: float = 15.0
    vacuum_threshold_kpa: float = -20.0  # more-negative pressure = contact


@dataclass
class SuctionReading:
    """Sensor reading from a single suction cup at one instant."""

    cup_id: str
    vacuum_kpa: float
    contact: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Array class
# ---------------------------------------------------------------------------

class SuctionArray:
    """Models a planar array of suction cups for vacuum-based grippers.

    Args:
        array_id: Unique identifier for this array instance.
        cups: Ordered list of SuctionCup configurations.
    """

    def __init__(self, array_id: str, cups: List[SuctionCup]) -> None:
        self.array_id = array_id
        self.cups: List[SuctionCup] = cups
        # Build a fast lookup from cup_id → SuctionCup
        self._cup_map: Dict[str, SuctionCup] = {c.cup_id: c for c in cups}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def read_state(
        self,
        vacuum_readings: Dict[str, float],
    ) -> List[SuctionReading]:
        """Convert raw vacuum measurements into structured SuctionReadings.

        For each cup whose cup_id appears in *vacuum_readings*, a reading is
        produced.  Contact is ``True`` when the measured vacuum is at or below
        (more negative than) the cup's ``vacuum_threshold_kpa``.

        Cups whose cup_id is absent from *vacuum_readings* are silently skipped
        so partial-array reads are valid.

        Args:
            vacuum_readings: Mapping of cup_id → current vacuum in kPa.
                Atmospheric pressure is 0 kPa; suction creates negative values.

        Returns:
            List of SuctionReading, one per cup present in *vacuum_readings*,
            ordered by the original cup list order.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        readings: List[SuctionReading] = []

        for cup in self.cups:
            if cup.cup_id not in vacuum_readings:
                continue
            vac = vacuum_readings[cup.cup_id]
            # Contact when vacuum is at or more-negative than the threshold
            contact = vac <= cup.vacuum_threshold_kpa
            readings.append(
                SuctionReading(
                    cup_id=cup.cup_id,
                    vacuum_kpa=vac,
                    contact=contact,
                    timestamp=now,
                )
            )
        return readings

    def pickup_count(self, readings: List[SuctionReading]) -> int:
        """Return the number of cups that have active contact."""
        return sum(1 for r in readings if r.contact)

    def pickup_pattern(self, readings: List[SuctionReading]) -> str:
        """Classify the pickup pattern from the current readings.

        Returns one of:
        - ``"none"``      — no cups have contact
        - ``"single"``    — exactly one cup has contact
        - ``"full"``      — every cup in *readings* has contact
        - ``"partial:M/N"`` — M cups have contact out of N cups in *readings*
        """
        n_total = len(readings)
        if n_total == 0:
            return "none"

        n_contact = self.pickup_count(readings)

        if n_contact == 0:
            return "none"
        if n_contact == 1:
            return "single"
        if n_contact == n_total:
            return "full"
        return f"partial:{n_contact}/{n_total}"

    def centroid_of_active(
        self,
        readings: List[SuctionReading],
    ) -> Optional[Tuple[float, float, float]]:
        """Return the averaged 3-D position (mm) of cups that have contact.

        Uses the static ``position_mm`` from each cup's configuration.
        Returns ``None`` when no cup has contact or when a cup_id in the
        readings is not registered in this array.
        """
        active_positions = []
        for r in readings:
            if not r.contact:
                continue
            cup = self._cup_map.get(r.cup_id)
            if cup is None:
                continue
            active_positions.append(cup.position_mm)

        if not active_positions:
            return None

        n = len(active_positions)
        cx = sum(p[0] for p in active_positions) / n
        cy = sum(p[1] for p in active_positions) / n
        cz = sum(p[2] for p in active_positions) / n
        return (cx, cy, cz)
