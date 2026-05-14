"""Phase 53 — AVM-1 dual analytical+simulation contract.

Every diagnose dimension must produce BOTH an analytical estimate
(deterministic from inputs) AND a simulation-measured value. The
delta between them is the calibration signal.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 53.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DualEstimate:
    dimension: str
    analytical_value: float
    simulated_value: Optional[float] = None
    delta_abs: Optional[float] = None
    delta_pct: Optional[float] = None
    confidence: str = "unknown"  # "high" | "medium" | "low"


def compute_delta(d: DualEstimate) -> DualEstimate:
    """Fill ``delta_abs``, ``delta_pct``, and ``confidence`` on a DualEstimate in-place.

    If ``simulated_value`` is ``None`` the input is returned unchanged.
    Confidence thresholds: ``delta_pct > 0.50`` → *low*; ``0.15–0.50`` → *medium*;
    ``< 0.15`` → *high*.

    Args:
        d (DualEstimate): Estimate with at least ``analytical_value`` set.

    Returns:
        DualEstimate: The same object with delta fields populated.
    """
    if d.simulated_value is None:
        return d
    delta = d.simulated_value - d.analytical_value
    d.delta_abs = abs(delta)
    d.delta_pct = abs(delta) / abs(d.analytical_value) if d.analytical_value != 0 else None
    if d.delta_pct is None or d.delta_pct > 0.5:
        d.confidence = "low"
    elif d.delta_pct > 0.15:
        d.confidence = "medium"
    else:
        d.confidence = "high"
    return d
