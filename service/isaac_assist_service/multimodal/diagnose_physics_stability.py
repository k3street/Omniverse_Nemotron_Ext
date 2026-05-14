"""Phase 51 — diagnose dimension: physics stability index.

Reads PhysX warnings + solver iteration counts; produces an index 0..1
representing simulation stability.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 51.
"""
from typing import Any, Dict, List


def physics_stability_index(physx_warnings: List[str],
                             solver_iterations_per_step: int = 4) -> Dict[str, Any]:
    """Compute a physics-simulation stability index in the range [0, 1].

    A higher index indicates a more stable simulation.  The baseline score is 0.7;
    each PhysX warning penalises 0.05 (capped at 0.5), and extra solver iterations
    beyond the default 4 each add 0.05 (capped at 0.3).

    Args:
        physx_warnings (List[str]): List of PhysX warning strings for the current step.
        solver_iterations_per_step (int, optional): Number of solver iterations configured
            in the physics scene. Defaults to 4.

    Returns:
        Dict[str, Any]: Keys ``stability_index`` (float, 0–1) and ``warnings`` (int count).
    """
    warning_penalty = min(len(physx_warnings) * 0.05, 0.5)
    solver_bonus = min((solver_iterations_per_step - 4) * 0.05, 0.3)
    index = max(0.0, min(1.0, 0.7 - warning_penalty + solver_bonus))
    return {"stability_index": round(index, 3), "warnings": len(physx_warnings)}
