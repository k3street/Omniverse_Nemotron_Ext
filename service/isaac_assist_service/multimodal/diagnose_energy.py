"""Phase 49 — diagnose dimension: energy estimate.

Estimates energy consumption per cycle based on joint torque
trajectories. Useful for sim-real validity checks.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 49.
"""
from typing import Any, Dict, List


def estimate_energy(joint_torques: List[List[float]],
                     joint_velocities: List[List[float]],
                     dt: float = 1.0 / 60.0) -> Dict[str, Any]:
    if not joint_torques or not joint_velocities:
        return {"energy_j": 0.0, "valid": False}
    n_joints = min(len(joint_torques[0]), len(joint_velocities[0]))
    energy = 0.0
    for t in range(min(len(joint_torques), len(joint_velocities))):
        for j in range(n_joints):
            energy += abs(joint_torques[t][j] * joint_velocities[t][j]) * dt
    return {"energy_j": round(energy, 3), "n_joints": n_joints, "valid": True}
