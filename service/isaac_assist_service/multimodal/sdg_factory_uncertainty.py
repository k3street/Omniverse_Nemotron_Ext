"""Phase 59 — SDG: factory_under_uncertainty DR preset.

A domain-randomization preset that simulates factory-environment
uncertainty (lighting variation, conveyor speed jitter, object pose
noise) for SDG dataset generation.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 59.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = 59
PHASE_TITLE = "factory_under_uncertainty DR preset"
PHASE_STATUS = "landed"


FACTORY_UNDER_UNCERTAINTY_PRESET: Dict[str, Any] = {
    "name": "factory_under_uncertainty",
    "description": "Factory-realism DR: lighting, conveyor jitter, pose noise, occluders",
    "ranges": {
        "lighting_lux": [200, 3000],
        "lighting_color_temp_k": [3000, 7500],
        "conveyor_speed_m_s": [0.1, 0.4],
        "conveyor_jitter_m_s": [0.0, 0.02],
        "object_pose_x_noise_m": [-0.005, 0.005],
        "object_pose_y_noise_m": [-0.005, 0.005],
        "object_pose_yaw_deg": [-5, 5],
        "occluder_count": [0, 3],
        "ambient_dust_density": [0.0, 0.3],
    },
    "sampling": "uniform",
    "correlations": [
        {"vars": ["lighting_lux", "lighting_color_temp_k"], "rho": 0.5},
    ],
    "num_samples": 5000,
}


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 59.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 59",
    }


def get_preset() -> Dict[str, Any]:
    """Return a fresh copy of the ``factory_under_uncertainty`` DR preset dict."""
    return dict(FACTORY_UNDER_UNCERTAINTY_PRESET)
