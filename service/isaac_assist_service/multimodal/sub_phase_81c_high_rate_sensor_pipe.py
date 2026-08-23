"""Phase 81c — bounded, thread-safe high-rate sensor sample pipe."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque, Dict, Generic, List, TypeVar

PHASE_ID = "81c"
PHASE_TITLE = "high rate sensor pipe"
PHASE_STATUS = "landed"

T = TypeVar("T")


@dataclass(frozen=True)
class SequencedSample(Generic[T]):
    sequence: int
    timestamp_s: float
    payload: T


class HighRateSensorPipe(Generic[T]):
    """Bound memory while exposing sequence gaps and producer overruns."""

    def __init__(self, capacity: int = 4096) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._samples: Deque[SequencedSample[T]] = deque(maxlen=capacity)
        self._next_sequence = 0
        self._dropped = 0
        self._lock = Lock()

    def publish(self, timestamp_s: float, payload: T) -> SequencedSample[T]:
        with self._lock:
            if len(self._samples) == self._samples.maxlen:
                self._dropped += 1
            sample = SequencedSample(self._next_sequence, timestamp_s, payload)
            self._next_sequence += 1
            self._samples.append(sample)
            return sample

    def read_since(self, sequence: int, limit: int = 256) -> List[SequencedSample[T]]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            return [sample for sample in self._samples if sample.sequence > sequence][:limit]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "buffered": len(self._samples),
                "capacity": int(self._samples.maxlen or 0),
                "published": self._next_sequence,
                "dropped": self._dropped,
            }


def get_phase_metadata() -> Dict[str, Any]:
    return {"phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
            "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 81c"}
