"""Phase 52 — diagnose dimension: workspace utilization.

Score 0..1 of how much of the robot's reachable workspace is occupied
by useful task elements. Low → empty/wasteful scene.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 52.
"""
from typing import Any, Dict


def workspace_utilization(reach_volume_m3: float,
                           occupied_volume_m3: float) -> Dict[str, Any]:
    """Compute a workspace utilization score as the ratio of occupied to reachable volume.

    A score near 1.0 means the robot's reachable envelope is well-filled with task
    elements.  A low score indicates a sparse or wasteful scene configuration.

    Args:
        reach_volume_m3 (float): Total reachable volume of the robot arm in m³.
        occupied_volume_m3 (float): Volume occupied by task-relevant scene objects in m³.

    Returns:
        Dict[str, Any]: Keys ``utilization`` (float, 0–1, rounded to 3 d.p.) and
            ``valid`` (bool; ``False`` when ``reach_volume_m3 <= 0``).
    """
    if reach_volume_m3 <= 0:
        return {"utilization": 0.0, "valid": False}
    util = min(occupied_volume_m3 / reach_volume_m3, 1.0)
    return {"utilization": round(util, 3), "valid": True}
