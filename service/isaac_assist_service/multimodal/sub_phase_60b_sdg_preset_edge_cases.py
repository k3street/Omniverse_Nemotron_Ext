"""Phase 60b — SDG preset edge cases.

Defines edge-case domain randomization presets that stress-test downstream
training pipelines beyond normal operating ranges.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 60b.
"""
from __future__ import annotations
from typing import Any, Dict


PHASE_ID = "60b"
PHASE_TITLE = "SDG extreme domain randomization (EDR) preset edge cases"
PHASE_STATUS = "landed"

# Each preset follows the same shape as sdg_5_more_presets.FIVE_MORE_PRESETS:
#   { name, ranges, num_samples }
EDGE_CASE_PRESETS: Dict[str, Dict[str, Any]] = {
    "extreme_lighting": {
        "name": "extreme_lighting",
        "ranges": {
            # lux: 50-10000 (vs normal 200-3000)
            "lighting_lux": [50, 10000],
            # color temperature in Kelvin: 2000-10000 (vs normal 3000-7500)
            "color_temp_k": [2000, 10000],
            # emissive surfaces can bloom the camera
            "emissive_intensity_scale": [0.0, 20.0],
        },
        "num_samples": 5000,
    },
    "high_occlusion": {
        "name": "high_occlusion",
        "ranges": {
            "occluder_count": [5, 15],
            "occluder_size_m": [0.05, 0.6],
            "occluder_opacity": [0.5, 1.0],
            "occluder_material": ["opaque", "translucent"],
        },
        "num_samples": 4000,
    },
    "noisy_sensors": {
        "name": "noisy_sensors",
        "ranges": {
            "camera_noise_sigma": [0.0, 0.1],
            "imu_bias_drift": [0.0, 0.05],
            "lidar_dropout_rate": [0.0, 0.3],
            "depth_noise_m": [0.0, 0.05],
        },
        "num_samples": 6000,
    },
    "physics_extreme": {
        "name": "physics_extreme",
        "ranges": {
            "friction": [0.05, 2.5],
            # gravity_m_s2: Mars (3.7) to Jupiter (24.8)
            "gravity_m_s2": [3.7, 24.8],
            "damping_scale": [0.1, 3.0],
            "restitution": [0.0, 1.0],
        },
        "num_samples": 5000,
    },
    "actuator_failure": {
        "name": "actuator_failure",
        "ranges": {
            "joint_dropout_prob": [0.0, 0.05],
            "joint_lag_ms": [0, 500],
            "joint_dead_zone_rad": [0.0, 0.1],
            "joint_gain_scale": [0.5, 1.5],
        },
        "num_samples": 4000,
    },
}

# Severity classification map — keys are substrings matched against preset name
_SEVERITY_MAP: list[tuple[str, str]] = [
    ("lighting", "extreme"),
    ("occlusion", "extreme"),
    ("noisy", "noisy"),
    ("sensor", "noisy"),
    ("physics", "physics_outlier"),
    ("actuator", "failure"),
    ("failure", "failure"),
]


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 60b",
        "preset_count": len(EDGE_CASE_PRESETS),
    }


def list_presets() -> list[str]:
    """Return sorted list of edge-case preset names."""
    return sorted(EDGE_CASE_PRESETS.keys())


def get_preset(name: str) -> Dict[str, Any]:
    """Return the preset dict for *name*, or empty dict if unknown."""
    return EDGE_CASE_PRESETS.get(name, {})


def severity_of_preset(name: str) -> str:
    """Classify preset severity.

    Returns one of: "extreme" | "noisy" | "failure" | "physics_outlier".
    Falls back to "extreme" for unknown names.
    """
    lower = name.lower()
    for fragment, severity in _SEVERITY_MAP:
        if fragment in lower:
            return severity
    return "extreme"
