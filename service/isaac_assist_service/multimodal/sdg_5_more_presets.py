"""Phase 60 — SDG: 5 more DR presets.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 60.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = 60
PHASE_TITLE = "5 more SDG presets"
PHASE_STATUS = "landed"


FIVE_MORE_PRESETS: Dict[str, Dict[str, Any]] = {
    "warehouse_picking_dr": {
        "name": "warehouse_picking_dr",
        "ranges": {
            "shelf_layout_seed": [0, 100],
            "robot_start_xy_m": [[-2, -2], [2, 2]],
            "lighting_lux": [300, 2000],
            "object_count_per_bin": [1, 8],
        },
        "num_samples": 3000,
    },
    "inspection_cell_dr": {
        "name": "inspection_cell_dr",
        "ranges": {
            "camera_z_offset_m": [1.2, 2.0],
            "camera_yaw_deg": [-30, 30],
            "object_yaw_deg": [-180, 180],
            "lighting_directional_deg": [0, 360],
            "defect_count": [0, 3],
        },
        "num_samples": 5000,
    },
    "navigation_outdoor_dr": {
        "name": "navigation_outdoor_dr",
        "ranges": {
            "ground_friction": [0.3, 1.0],
            "obstacle_density": [0.1, 0.7],
            "weather": ["clear", "rain", "fog"],
            "time_of_day_h": [6, 20],
        },
        "num_samples": 10000,
    },
    "humanoid_locomotion_dr": {
        "name": "humanoid_locomotion_dr",
        "ranges": {
            "terrain_height_m": [0.0, 0.2],
            "terrain_friction": [0.4, 1.2],
            "actuator_gain_scale": [0.8, 1.2],
            "joint_damping_scale": [0.7, 1.3],
            "external_force_n": [0, 50],
        },
        "num_samples": 8000,
    },
    "assembly_precision_dr": {
        "name": "assembly_precision_dr",
        "ranges": {
            "part_pose_xy_noise_mm": [-2, 2],
            "part_pose_yaw_noise_deg": [-3, 3],
            "fixture_compliance_n_m": [100, 1000],
            "vision_noise_px": [0, 3],
        },
        "num_samples": 5000,
    },
}


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 60.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 60",
    }


def list_presets() -> list:
    """Return the list of available Phase 60 preset names."""
    return list(FIVE_MORE_PRESETS.keys())


def get_preset(name: str) -> Dict[str, Any]:
    """Return the preset dict for *name*, or an empty dict if not found."""
    return FIVE_MORE_PRESETS.get(name, {})
