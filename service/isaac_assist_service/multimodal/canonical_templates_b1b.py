"""Phase 28 — Block 1B canonical templates with role-based definitions.

Migrates CP-01..CP-05 (the top 5 canonical templates used by
simulate_traversal_check) to a role-bearing format.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 28.
"""
from __future__ import annotations

from typing import Dict, List


# Role-based canonical templates for Phase 28.
B1B_CANONICAL_TEMPLATES: Dict[str, Dict] = {
    "CP-01": {
        "task_id": "CP-01",
        "name": "single_arm_pick_place",
        "roles": {
            "primary_robot": {
                "constraints": ["franka_panda", "ur5e", "ur10e", "kinova_gen3"],
                "expected_count": 1,
                "required": True,
                "disambiguator": "nearest_to_origin",
            },
            "workpiece": {"constraints": ["cube_small", "cube_medium"], "expected_count": 1, "required": True},
            "destination": {"constraints": ["bin"], "expected_count": 1, "required": True},
        },
        "role_index": ["pick_place"],
    },
    "CP-02": {
        "task_id": "CP-02",
        "name": "dual_arm_handoff",
        "roles": {
            "robot_a": {"constraints": ["franka_panda", "ur5e"], "expected_count": 1, "required": True},
            "robot_b": {"constraints": ["franka_panda", "ur5e"], "expected_count": 1, "required": True},
            "workpiece": {"constraints": ["cube_small"], "expected_count": 1, "required": True},
        },
        "role_index": ["pick_place", "handoff"],
    },
    "CP-03": {
        "task_id": "CP-03",
        "name": "conveyor_pick_place",
        "roles": {
            "primary_robot": {"constraints": ["franka_panda", "ur10e"], "expected_count": 1, "required": True},
            "conveyor": {"constraints": ["conveyor_short", "conveyor_long"], "expected_count": 1, "required": True},
            "destination": {"constraints": ["bin", "bin_large"], "expected_count": 1, "required": True},
        },
        "role_index": ["pick_place", "dynamic"],
    },
    "CP-04": {
        "task_id": "CP-04",
        "name": "mobile_navigation",
        "roles": {
            "mobile_robot": {"constraints": ["carter", "jetbot"], "expected_count": 1, "required": True},
            "obstacles": {"constraints": ["obstacle_box", "obstacle_cylinder"], "expected_count": 3, "required": False},
        },
        "role_index": ["navigation"],
    },
    "CP-05": {
        "task_id": "CP-05",
        "name": "inspection_cell",
        "roles": {
            "primary_robot": {"constraints": ["ur5e", "franka_panda"], "expected_count": 1, "required": True},
            "camera": {"constraints": ["camera_overhead", "camera_side"], "expected_count": 1, "required": True},
            "workpiece": {"constraints": ["cube_small", "cylinder_small"], "expected_count": 3, "required": True},
        },
        "role_index": ["inspection"],
    },
}


def get_template(task_id: str) -> Dict:
    return B1B_CANONICAL_TEMPLATES.get(task_id, {})


def list_templates() -> List[Dict]:
    return list(B1B_CANONICAL_TEMPLATES.values())
