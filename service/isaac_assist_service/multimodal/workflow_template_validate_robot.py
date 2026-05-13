"""Phase 35 — workflow template: validate_robot_import.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 35.
"""
VALIDATE_ROBOT_IMPORT_TEMPLATE = {
    "name": "validate_robot_import",
    "description": "Import robot + verify articulation + check collision meshes",
    "phases": [
        {"name": "import_robot", "checkpoint": False, "error_fix": True},
        {"name": "verify_articulation", "checkpoint": True, "error_fix": True},
        {"name": "check_collision_meshes", "checkpoint": False, "error_fix": True},
        {"name": "test_motion", "checkpoint": True, "error_fix": False},
    ],
    "default_params": {"robot_name": "franka_panda"},
}
