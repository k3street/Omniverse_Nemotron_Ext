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
    """Apply a single gradient step to move analytical model parameters toward zero bias.

    Each parameter is adjusted by ``-learning_rate × mean_delta`` where ``mean_delta``
    is the average of ``row["delta"]`` (simulated − analytical) across all rows.
    Returns *current_params* unchanged when inputs are empty.

    Args:
        rows (List[Dict[str, Any]]): Gap-log rows for the target dimension;
            each must contain a ``"delta"`` key (float).
        current_params (Dict[str, float]): Parameter values to adjust.
        learning_rate (float, optional): Step size for the gradient update. Defaults to 0.1.

    Returns:
        Dict[str, float]: Updated parameter dict with the same keys as *current_params*.
    """
    if not rows or not current_params:
        return current_params
    deltas = [r["delta"] for r in rows]
    mean_delta = sum(deltas) / len(deltas)
    return {k: v - learning_rate * mean_delta for k, v in current_params.items()}
