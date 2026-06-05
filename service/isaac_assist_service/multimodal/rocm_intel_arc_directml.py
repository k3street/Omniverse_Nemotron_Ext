"""Phase 89 — ROCm + Intel Arc + DirectML smoke tests.

Back-compat shim. The canonical implementation lives at
`service.isaac_assist_service.multimodal.gpu_vendor_detection` which
ships vendor detection, runtime selection, the compatibility matrix,
and a dry-run smoke runner.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 89.
"""
from __future__ import annotations

from typing import Any, Dict

from .gpu_vendor_detection import (  # noqa: F401
    GPUInfo,
    GPUSmokeRunner,
    GPUVendor,
    MLRuntime,
    RUNTIME_CAPABILITIES,
    RuntimeCapability,
    compatibility_matrix,
    detect_vendor_from_string,
    select_runtime,
)

PHASE_ID = 89
PHASE_TITLE = "ROCm + Intel Arc + DirectML smoke tests"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits.

    Canonical module is `gpu_vendor_detection`; this file is a re-export
    shim retained for the original spec path.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 89",
        "canonical_module": "service.isaac_assist_service.multimodal.gpu_vendor_detection",
    }
