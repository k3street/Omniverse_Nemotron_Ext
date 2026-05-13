"""Phase 80 — back-compat shim for surface_gripper_model.

The canonical implementation lives in ``surface_gripper_suction``.
This module is a thin re-export alias kept for backwards compatibility
with any code that imported from ``surface_gripper_model``.
"""
from .surface_gripper_suction import *  # noqa: F401, F403
from .surface_gripper_suction import (  # noqa: F401  (explicit for tools/IDEs)
    PHASE_ID,
    PHASE_TITLE,
    PHASE_STATUS,
    get_phase_metadata,
    SurfaceMaterial,
    CupMaterial,
    LeakRisk,
    SuctionCupSpec,
    GripForceResult,
    SuctionGripperModel,
    GRIPPER_TYPE_REGISTRY,
    get_gripper,
    list_grippers,
)
