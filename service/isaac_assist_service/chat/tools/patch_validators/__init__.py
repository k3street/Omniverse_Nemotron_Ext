"""Patch validator pipeline — Phase 11.

Wraps the 22 `_check_*` functions in `patch_validator.py` as registered
rule classes. The pipeline runner aggregates rule outputs into a
`ValidationResult`.

Phase 11 ships the framework + per-rule wrappers as a parallel surface.
Existing callers of `patch_validator.validate_patch()` continue to work
unchanged. New callers can use `patch_validators.run_pipeline(code)`
for the typed contract.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 11.
"""
from __future__ import annotations

from .registry import (
    CitedFailure,
    PatchIssue,
    PatchValidatorRule,
    PipelineRunner,
    Severity,
    ValidationResult,
    register,
    REGISTRY,
    run_pipeline,
)

# Import all rule modules to trigger their registration via decorator.
from .rules import (  # noqa: F401
    omnigraph,
    pxr_imports,
    robot_specific,
    stage_mutation,
    usd_api,
)

__all__ = [
    "CitedFailure",
    "PatchIssue",
    "PatchValidatorRule",
    "PipelineRunner",
    "Severity",
    "ValidationResult",
    "register",
    "REGISTRY",
    "run_pipeline",
]
