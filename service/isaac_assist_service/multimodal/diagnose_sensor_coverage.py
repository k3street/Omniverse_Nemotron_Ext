"""Phase 50 — diagnose dimension: sensor coverage.

Score 0..1 of how much of the workspace is covered by the scene's
sensors (cameras, lidars). Low score = blindspot risk.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 50.
"""
from typing import Any, Dict, List


def estimate_sensor_coverage(sensors: List[Dict[str, Any]],
                              workspace_volume_m3: float = 4.0) -> Dict[str, Any]:
    """Estimate the fraction of the workspace volume covered by the scene's sensors.

    Sums ``fov_volume_m3`` across all sensor dicts (defaults to 1.0 per sensor when
    the key is absent) then divides by the workspace volume.  The result is clamped to [0, 1].

    Args:
        sensors (List[Dict[str, Any]]): Sensor descriptors; each may contain
            ``fov_volume_m3`` (float).
        workspace_volume_m3 (float, optional): Total workspace volume to normalise against.
            Defaults to 4.0 m³.

    Returns:
        Dict[str, Any]: Keys ``coverage_score`` (float, 0–1) and ``n_sensors`` (int).
    """
    total_fov_m3 = 0.0
    for s in sensors:
        fov_m3 = s.get("fov_volume_m3", 1.0)
        total_fov_m3 += fov_m3
    coverage = min(total_fov_m3 / workspace_volume_m3, 1.0) if workspace_volume_m3 > 0 else 0.0
    return {"coverage_score": round(coverage, 3), "n_sensors": len(sensors)}
