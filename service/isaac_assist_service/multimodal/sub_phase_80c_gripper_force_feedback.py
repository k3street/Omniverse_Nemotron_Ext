"""Phase 80c — deterministic gripper force-feedback processing."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Literal, Tuple

PHASE_ID = "80c"
PHASE_TITLE = "gripper force feedback"
PHASE_STATUS = "landed"


@dataclass(frozen=True)
class ForceSample:
    timestamp_s: float
    left_force_n: float
    right_force_n: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.timestamp_s, self.left_force_n, self.right_force_n)):
            raise ValueError("force samples must be finite")


@dataclass(frozen=True)
class ForceFeedback:
    state: Literal["open", "contact", "overload", "imbalanced"]
    mean_force_n: float
    imbalance_n: float
    should_stop: bool


class GripperForceMonitor:
    def __init__(
        self,
        contact_threshold_n: float = 1.0,
        overload_threshold_n: float = 40.0,
        imbalance_threshold_n: float = 8.0,
        window_size: int = 5,
    ) -> None:
        if not 0 <= contact_threshold_n < overload_threshold_n:
            raise ValueError("force thresholds are invalid")
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self.contact_threshold_n = contact_threshold_n
        self.overload_threshold_n = overload_threshold_n
        self.imbalance_threshold_n = imbalance_threshold_n
        self._samples: Deque[ForceSample] = deque(maxlen=window_size)

    def observe(self, sample: ForceSample) -> ForceFeedback:
        if self._samples and sample.timestamp_s < self._samples[-1].timestamp_s:
            raise ValueError("force sample timestamps must be monotonic")
        self._samples.append(sample)
        left = sum(s.left_force_n for s in self._samples) / len(self._samples)
        right = sum(s.right_force_n for s in self._samples) / len(self._samples)
        mean = (left + right) / 2.0
        imbalance = abs(left - right)
        if max(left, right) >= self.overload_threshold_n:
            state = "overload"
        elif imbalance >= self.imbalance_threshold_n and mean >= self.contact_threshold_n:
            state = "imbalanced"
        elif mean >= self.contact_threshold_n:
            state = "contact"
        else:
            state = "open"
        return ForceFeedback(state, mean, imbalance, state in {"overload", "imbalanced"})


def get_phase_metadata() -> Dict[str, Any]:
    return {"phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
            "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 80c"}
