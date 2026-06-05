"""Phase 48 — diagnose dimension: cycle time estimate.

Estimates the cycle time for a single pick-place loop based on
trajectory length + IK solve time + grasp execution time.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 48.
"""
from typing import Any, Dict


def estimate_cycle_time(args: Dict[str, Any]) -> Dict[str, Any]:
    distance_m = args.get("distance_m", 0.5)
    ik_solve_ms = args.get("ik_solve_ms", 50)
    grasp_ms = args.get("grasp_ms", 500)
    speed_m_s = args.get("speed_m_s", 0.25)
    travel_s = distance_m / speed_m_s
    total_s = 2 * travel_s + 2 * grasp_ms / 1000 + ik_solve_ms / 1000
    return {
        "cycle_time_s": round(total_s, 3),
        "components": {
            "travel_s": round(travel_s, 3),
            "grasp_s": grasp_ms / 1000,
            "ik_s": ik_solve_ms / 1000,
        },
    }
