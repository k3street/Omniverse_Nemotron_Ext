"""Phase 71 — Yaskawa GP25 onboarding (SPEC/DATA layer).

Provides the static spec data and onboarding checklist for the Yaskawa
Motoman GP25 industrial 6-axis robot. Runtime steps (Kit RPC, Nucleus
import, URDF→USD conversion) remain scaffolded — they require live
infrastructure. Spec data and registry entry are fully landed.

Real Yaskawa-published specs (GP25 product page / spec sheet):
  - Payload: 25 kg
  - Reach: 1730 mm (1.730 m)
  - Repeatability: ±0.06 mm
  - DOF: 6
  - Mass: 300 kg
  - Controller: YRC1000

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 71.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Tuple


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 71
PHASE_TITLE = "Yaskawa GP25 onboarding"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits.

    Note: PHASE_STATUS = "landed" for the SPEC/DATA layer. Runtime-dependent
    onboarding execution (Nucleus import, USD validation in Kit) remains
    scaffold until live infrastructure is available.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 71",
        "note": (
            "Spec/data layer landed. Runtime onboarding (Nucleus import, "
            "Kit RPC validation) is scaffolded — requires runtime infrastructure."
        ),
    }


# ---------------------------------------------------------------------------
# YaskawaGP25Spec dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class YaskawaGP25Spec:
    """Frozen dataclass holding published Yaskawa GP25 specifications.

    All values sourced from Yaskawa's public GP25 product documentation
    and the Yaskawa Motoman GP25 spec sheet.
    """

    name: str
    manufacturer: str
    model: str

    # Performance specs
    payload_kg: float          # 25.0 kg
    reach_m: float             # 1.730 m
    repeatability_mm: float    # ±0.06 mm

    # Mechanical
    dof: int                   # 6
    weight_kg: float           # 300.0 kg

    # Per-link mass estimates (kg). Approximate, based on typical GP25 link geometry.
    # Keys: link_1 through link_6.
    mass_distribution: Dict[str, float]

    # Joint limits in degrees. 6 entries as (lower_deg, upper_deg).
    # Sourced from Yaskawa GP25 spec sheet joint motion range table.
    joint_limits_deg: List[Tuple[float, float]]

    # Maximum joint velocity limits in degrees per second.
    # 6 entries corresponding to joints S, L, U, R, B, T.
    joint_velocity_limits_dps: List[float]

    # Controller
    controller_model: str      # "YRC1000"

    # Communication protocols supported by YRC1000
    protocol_options: List[str]

    # Asset paths
    nucleus_asset_path: str
    urdf_path: str

    # Metadata
    target_industries: List[str]
    recommended_grippers: List[str]
    notes: str


# ---------------------------------------------------------------------------
# GP25 canonical spec instance
# ---------------------------------------------------------------------------

GP25_SPEC = YaskawaGP25Spec(
    name="Yaskawa GP25",
    manufacturer="Yaskawa",
    model="GP25",

    # Published performance specs
    payload_kg=25.0,
    reach_m=1.730,
    repeatability_mm=0.06,

    # Mechanical
    dof=6,
    weight_kg=300.0,

    # Per-link mass distribution (kg) — approximate based on GP25 geometry.
    # Link 1 (base rotation, S-axis): heaviest segment containing drive motor.
    # Links decrease toward the wrist.
    mass_distribution={
        "link_1": 75.0,   # S-axis base + motor
        "link_2": 65.0,   # L-axis upper arm
        "link_3": 55.0,   # U-axis forearm
        "link_4": 40.0,   # R-axis wrist roll
        "link_5": 35.0,   # B-axis wrist bend
        "link_6": 30.0,   # T-axis wrist twist (flange end)
    },

    # Joint motion ranges (degrees) — from Yaskawa GP25 spec sheet.
    # Order: S, L, U, R, B, T
    # S: ±180°, L: +155°/-90°, U: +255°/-175°, R: ±200°, B: ±145°, T: ±360°
    joint_limits_deg=[
        (-180.0,  180.0),   # S — base rotation
        ( -90.0,  155.0),   # L — lower arm swing
        (-175.0,  255.0),   # U — upper arm swing
        (-200.0,  200.0),   # R — wrist roll
        (-145.0,  145.0),   # B — wrist bend
        (-360.0,  360.0),   # T — flange rotation
    ],

    # Maximum joint velocities (deg/s) from GP25 spec sheet.
    # Order: S, L, U, R, B, T
    joint_velocity_limits_dps=[
        220.0,   # S
        200.0,   # L
        220.0,   # U
        410.0,   # R
        410.0,   # B
        610.0,   # T
    ],

    # YRC1000 controller
    controller_model="YRC1000",

    # Communication options for YRC1000
    protocol_options=["MotoPlus", "Ethernet/IP", "PROFINET"],

    # USD and URDF asset paths
    nucleus_asset_path="omniverse://localhost/Robots/Yaskawa/GP25/gp25.usd",
    urdf_path="robots/yaskawa/gp25/gp25.urdf",

    # Application domains
    target_industries=[
        "automotive",
        "metal_fabrication",
        "general_assembly",
        "material_handling",
        "palletizing",
    ],

    # Compatible end-effectors at GP25 payload class
    recommended_grippers=[
        "Schunk PGN-plus 160",
        "Robotiq 2F-140",
        "OnRobot VGC10",
        "ATI Omega-160 F/T sensor",
    ],

    notes=(
        "GP25 is Yaskawa's 25 kg payload, 1730 mm reach 6-axis industrial "
        "manipulator in the Motoman GP-series. YRC1000 controller supports "
        "MotoPlus, Ethernet/IP, and PROFINET. ROS-Industrial support via "
        "motoman_gp25_support package. Asset import from ROS-Industrial "
        "URDF requires Phase 35 validate_robot_import workflow."
    ),
)


# ---------------------------------------------------------------------------
# OnboardingStep dataclass + checklist
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OnboardingStep:
    """One step in the GP25 onboarding checklist."""

    step_id: int
    title: str
    category: str          # "precondition" | "asset" | "validation" | "configuration" | "registration"
    required: bool
    runtime_needed: bool   # True if step requires live Kit RPC / Nucleus


ONBOARDING_CHECKLIST: List[OnboardingStep] = [
    OnboardingStep(
        step_id=1,
        title="Verify Nucleus reachability",
        category="precondition",
        required=True,
        runtime_needed=True,
    ),
    OnboardingStep(
        step_id=2,
        title="Download GP25 USD asset to Nucleus",
        category="asset",
        required=True,
        runtime_needed=True,
    ),
    OnboardingStep(
        step_id=3,
        title="Validate URDF against schema",
        category="validation",
        required=True,
        runtime_needed=False,
    ),
    OnboardingStep(
        step_id=4,
        title="Apply joint limits from spec",
        category="configuration",
        required=True,
        runtime_needed=False,
    ),
    OnboardingStep(
        step_id=5,
        title="Run import smoke test",
        category="validation",
        required=True,
        runtime_needed=True,
    ),
    OnboardingStep(
        step_id=6,
        title="Configure controller protocol",
        category="configuration",
        required=True,
        runtime_needed=False,
    ),
    OnboardingStep(
        step_id=7,
        title="Run pick-place demo",
        category="validation",
        required=False,
        runtime_needed=True,
    ),
    OnboardingStep(
        step_id=8,
        title="Register in robot wizard",
        category="registration",
        required=True,
        runtime_needed=False,
    ),
    OnboardingStep(
        step_id=9,
        title="Verify joint velocity limits in USD overlay",
        category="validation",
        required=False,
        runtime_needed=True,
    ),
    OnboardingStep(
        step_id=10,
        title="Confirm catalog YAML entry",
        category="registration",
        required=True,
        runtime_needed=False,
    ),
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def steps_without_runtime() -> List[OnboardingStep]:
    """Return the subset of onboarding steps that do not require runtime.

    These steps can be validated statically (schema checks, spec data
    round-trips, registry registration) without a live Kit RPC connection
    or Nucleus instance.
    """
    return [step for step in ONBOARDING_CHECKLIST if not step.runtime_needed]


def gp25_to_robot_wizard_entry() -> Dict[str, Any]:
    """Convert GP25_SPEC into the dict shape used by _ROBOT_WIZARD_REGISTRY.

    The returned dict mirrors the structure of existing entries in
    handlers/_shared.py::_ROBOT_WIZARD_REGISTRY (e.g. franka_panda,
    ur10e). Keys include at minimum: name, manufacturer, model,
    urdf_path, nucleus_asset_path, plus the standard wizard keys
    (robot_type, payload_kg, reach_m, dof, controller_model).
    """
    spec = GP25_SPEC
    return {
        "name": spec.name,
        "manufacturer": spec.manufacturer,
        "model": spec.model,
        "robot_type": "manipulator",
        "payload_kg": spec.payload_kg,
        "reach_m": spec.reach_m,
        "repeatability_mm": spec.repeatability_mm,
        "dof": spec.dof,
        "weight_kg": spec.weight_kg,
        "controller_model": spec.controller_model,
        "protocol_options": list(spec.protocol_options),
        "nucleus_asset_path": spec.nucleus_asset_path,
        "urdf_path": spec.urdf_path,
        "rel_path": "Robots/Yaskawa/GP25/gp25.usd",
        "cloud_url": (
            "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
            "/Assets/Isaac/5.1/Isaac/Robots/Yaskawa/GP25/gp25.usd"
        ),
        "joint_limits_deg": list(spec.joint_limits_deg),
        "joint_velocity_limits_dps": list(spec.joint_velocity_limits_dps),
        "target_industries": list(spec.target_industries),
        "recommended_grippers": list(spec.recommended_grippers),
        "notes": spec.notes,
    }
