"""Phase 36 — workflow template: generate_sdg_dataset.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 36.
"""
GENERATE_SDG_DATASET_TEMPLATE = {
    "name": "generate_sdg_dataset",
    "description": "Synthetic data pipeline: scene → DR ranges → render → export",
    "phases": [
        {"name": "configure_scene", "checkpoint": True, "error_fix": False},
        {"name": "configure_dr_ranges", "checkpoint": True, "error_fix": False},
        {"name": "preview_render", "checkpoint": True, "error_fix": True},
        {"name": "generate_dataset", "checkpoint": False, "error_fix": False},
        {"name": "validate_annotations", "checkpoint": True, "error_fix": True},
        {"name": "export", "checkpoint": True, "error_fix": False},
    ],
    "default_params": {"num_samples": 1000, "writer_format": "coco"},
}
