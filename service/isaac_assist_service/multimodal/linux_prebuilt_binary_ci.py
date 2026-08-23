"""Compatibility import for the canonical Phase 88 CI implementation."""

from .linux_ci_pipeline import (  # noqa: F401
    BUILD_TARGETS,
    PHASE_ID,
    PHASE_STATUS,
    PHASE_TITLE,
    CIBuildTarget,
    CIMatrixEntry,
    LinuxCIMatrix,
    get_phase_metadata,
    parse_workflow_yaml,
    validate_workflow_matrix_matches_spec,
)

__all__ = [
    "BUILD_TARGETS",
    "PHASE_ID",
    "PHASE_STATUS",
    "PHASE_TITLE",
    "CIBuildTarget",
    "CIMatrixEntry",
    "LinuxCIMatrix",
    "get_phase_metadata",
    "parse_workflow_yaml",
    "validate_workflow_matrix_matches_spec",
]
