"""Phase 48 — diagnose dimension: cycle time estimate.

Estimates the cycle time for a single pick-place loop based on
trajectory length + IK solve time + grasp execution time.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 48.
"""
from typing import Any, Dict


def estimate_cycle_time(args: Dict[str, Any]) -> Dict[str, Any]:
    """Estimate the cycle time for a single pick-place loop from kinematic parameters.

    Total time is computed as two traversal legs (pick + return) plus grasp time and
    IK solve time: ``2 * (distance / speed) + 2 * grasp_ms/1000 + ik_ms/1000``.

    Args:
        args (Dict[str, Any]): Keyword arguments:
            - ``distance_m`` (float): One-way travel distance in metres. Defaults to 0.5.
            - ``ik_solve_ms`` (float): IK solver latency in milliseconds. Defaults to 50.
            - ``grasp_ms`` (float): Grasp/release execution time in milliseconds. Defaults to 500.
            - ``speed_m_s`` (float): End-effector travel speed in m/s. Defaults to 0.25.

    Returns:
        Dict[str, Any]: Keys ``cycle_time_s`` (float) and ``components`` (dict with
            ``travel_s``, ``grasp_s``, ``ik_s`` sub-keys), all rounded to 3 d.p.
    """
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
