"""Phase 56 — recalibration loop.

When the gap analyzer reports systematic bias > threshold for N
samples, an automated tuning loop adjusts the analytical model's
parameters to reduce the bias.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 56.
"""
from typing import Any, Dict, List


def recalibrate(rows: List[Dict[str, Any]],
                 current_params: Dict[str, float],
                 learning_rate: float = 0.1) -> Dict[str, float]:
    """Naive gradient step toward zero mean delta."""
    if not rows or not current_params:
        return current_params
    deltas = [r["delta"] for r in rows]
    mean_delta = sum(deltas) / len(deltas)
    return {k: v - learning_rate * mean_delta for k, v in current_params.items()}
