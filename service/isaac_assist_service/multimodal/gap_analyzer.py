"""Phase 55 — gap analyzer.

Consumes the gap log (Phase 54), identifies systematic errors in
analytical estimates by dimension.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 55.
"""
from typing import Any, Dict, List


def analyze_dimension(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
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
