"""Phase 56b — recalibration log.

Persistent NDJSON log for recalibration events produced by the
recalibrate_loop (Phase 56).  Each event records which dimension was
recalibrated, the old/new parameter sets, summary statistics, and an
optional trigger label so the history can later be queried per-dimension
or summarised across the full run.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 56b.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PHASE_ID = "56b"
PHASE_TITLE = "recalibration log"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 56b",
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RecalibrationEvent:
    """One recalibration step for a single analytical dimension."""

    dimension: str
    old_params: Dict[str, float]
    new_params: Dict[str, float]
    mean_delta: float
    n_samples: int
    trigger: str  # e.g. "systematic_bias", "scheduled", "manual"
    notes: str = ""
    # Auto-populated fields
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RecalibrationEvent":
        """Reconstruct from a plain dict (e.g. parsed from NDJSON)."""
        return cls(
            event_id=d["event_id"],
            timestamp=d["timestamp"],
            dimension=d["dimension"],
            old_params=d["old_params"],
            new_params=d["new_params"],
            mean_delta=d["mean_delta"],
            n_samples=d["n_samples"],
            trigger=d["trigger"],
            notes=d.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Log manager
# ---------------------------------------------------------------------------

class RecalibrationLog:
    """Persistent NDJSON log for :class:`RecalibrationEvent` objects.

    Each call to :meth:`record` appends one JSON line to the backing file.
    Query methods parse the file on demand — suitable for log files that
    grow to tens of thousands of events (re-reading is fast; a write-through
    cache would be an optimisation if needed later).

    Parameters
    ----------
    log_path:
        Path to the NDJSON file.  The file and its parent directories are
        created automatically on first write.
    """

    def __init__(self, log_path: Path) -> None:
        self._path = Path(log_path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, event: RecalibrationEvent) -> str:
        """Append *event* to the log file and return its ``event_id``."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return event.event_id

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _iter_events(self) -> List[RecalibrationEvent]:
        """Return all events in file order (oldest first)."""
        if not self._path.exists():
            return []
        events: List[RecalibrationEvent] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(RecalibrationEvent.from_dict(json.loads(raw)))
                except (json.JSONDecodeError, KeyError) as exc:
                    raise ValueError(
                        f"Malformed NDJSON at line {lineno} in {self._path}: {exc}"
                    ) from exc
        return events

    def all_events(self) -> List[RecalibrationEvent]:
        """Return all recorded events (oldest first)."""
        return self._iter_events()

    def for_dimension(self, dimension: str) -> List[RecalibrationEvent]:
        """Return all events whose ``dimension`` matches *dimension* (oldest first)."""
        return [e for e in self._iter_events() if e.dimension == dimension]

    def latest_for_dimension(self, dimension: str) -> Optional[RecalibrationEvent]:
        """Return the most-recent event for *dimension*, or ``None``."""
        matches = self.for_dimension(dimension)
        return matches[-1] if matches else None

    def summary(self) -> Dict[str, int]:
        """Return a mapping of ``{dimension: event_count}``."""
        counts: Dict[str, int] = {}
        for event in self._iter_events():
            counts[event.dimension] = counts.get(event.dimension, 0) + 1
        return counts
