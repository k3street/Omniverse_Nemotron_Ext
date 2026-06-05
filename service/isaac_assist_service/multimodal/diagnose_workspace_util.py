"""Phase 52 — diagnose dimension: workspace utilization.

Score 0..1 of how much of the robot's reachable workspace is occupied
by useful task elements. Low → empty/wasteful scene.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 52.
"""
from typing import Any, Dict


def workspace_utilization(reach_volume_m3: float,
                           occupied_volume_m3: float) -> Dict[str, Any]:
    if reach_volume_m3 <= 0:
        return {"utilization": 0.0, "valid": False}
    util = min(occupied_volume_m3 / reach_volume_m3, 1.0)
    return {"utilization": round(util, 3), "valid": True}
