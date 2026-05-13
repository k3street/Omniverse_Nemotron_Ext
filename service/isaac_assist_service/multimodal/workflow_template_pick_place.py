"""Phase 34 — workflow template: assemble_pick_place_cell.

Phases: load template → place objects → grasp pose teach → controller
setup → smoke test.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 34.
"""
ASSEMBLE_PICK_PLACE_CELL_TEMPLATE = {
    "name": "assemble_pick_place_cell",
    "description": "Build a pick-place cell from scratch: robot + workpiece + destination",
    "phases": [
        {"name": "load_template", "checkpoint": True, "error_fix": False},
        {"name": "place_objects", "checkpoint": False, "error_fix": True},
        {"name": "teach_grasp_pose", "checkpoint": True, "error_fix": False},
        {"name": "setup_controller", "checkpoint": False, "error_fix": True},
        {"name": "smoke_test", "checkpoint": True, "error_fix": True},
    ],
    "default_params": {
        "robot_class": "franka_panda",
        "workpiece_class": "cube_small",
        "destination_class": "bin",
    },
}
