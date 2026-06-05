"""Phase 55 — gap analyzer.

Consumes the gap log (Phase 54), identifies systematic errors in
analytical estimates by dimension.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 55.
"""
from typing import Any, Dict, List


def analyze_dimension(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics over gap-log rows for a single dimension.

    A systematic bias is flagged when the absolute mean delta exceeds
    50 % of the maximum absolute delta, indicating the errors are not random.

    Args:
        rows (List[Dict[str, Any]]): Gap-log rows for one dimension; each must
            contain a ``"delta"`` key (float: simulated − analytical).

    Returns:
        Dict[str, Any]: Keys ``mean_delta`` (float), ``max_delta`` (float),
            ``n_samples`` (int), and ``systematic_bias`` (bool).
    """
    if not rows:
        return {"mean_delta": 0.0, "max_delta": 0.0, "n_samples": 0, "systematic_bias": False}
    deltas = [r["delta"] for r in rows]
    mean = sum(deltas) / len(deltas)
    max_d = max(abs(d) for d in deltas)
    systematic = abs(mean) > max_d * 0.5
    return {
        "mean_delta": round(mean, 3),
        "max_delta": round(max_d, 3),
        "n_samples": len(rows),
        "systematic_bias": systematic,
    }
