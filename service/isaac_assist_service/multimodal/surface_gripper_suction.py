"""Phase 80 — Surface gripper + suction force/hold-capacity model.

Canonical module for the Phase 80 surface-gripper implementation.
Pure-Python force/hold-capacity model and gripper-type registry.
No runtime dependencies (Kit RPC, GPU, Gemini, etc.) required.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 80.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional


PHASE_ID = 80
PHASE_TITLE = "Surface gripper + suction modeling"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 80",
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SurfaceMaterial = Literal[
    "smooth_plastic",
    "rough_metal",
    "fabric",
    "cardboard",
    "glass",
    "wet_surface",
    "porous",
]

CupMaterial = Literal["nitrile", "silicone", "urethane"]

LeakRisk = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Effectiveness table (material → fraction of theoretical vacuum force)
# ---------------------------------------------------------------------------

_EFFECTIVENESS: Dict[str, float] = {
    "smooth_plastic": 0.95,
    "glass": 1.0,
    "rough_metal": 0.75,
    "cardboard": 0.7,
    "fabric": 0.4,
    "wet_surface": 0.3,
    "porous": 0.2,
}

_LEAK_RISK: Dict[str, str] = {
    "smooth_plastic": "low",
    "glass": "low",
    "rough_metal": "medium",
    "cardboard": "medium",
    "fabric": "high",
    "wet_surface": "high",
    "porous": "high",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SuctionCupSpec:
    """Static configuration for a suction cup or multi-cup gripper.

    Attributes:
        cup_radius_mm: Radius of one suction cup in millimetres.
        cup_count: Number of cups acting in parallel.
        max_vacuum_kpa: Maximum achievable vacuum (kPa above atmospheric,
            positive convention used in pump specs — 80 kPa ≈ 0.8 bar).
        flow_rate_lpm: Pump flow rate in litres per minute (governs leak
            tolerance, not modelled numerically here).
        cup_material: Elastomer used for cup lip seal.
    """

    cup_radius_mm: float
    cup_count: int = 1
    max_vacuum_kpa: float = 80.0
    flow_rate_lpm: float = 100.0
    cup_material: CupMaterial = "nitrile"


@dataclass
class GripForceResult:
    """Output of a holding-force evaluation.

    Attributes:
        holding_force_N: Total theoretical holding force in Newtons.
        safety_margin: Dimensionless ratio (holding_force / applied_weight).
            Values > 1 mean the grip can support the payload; values < 1
            mean the grip will fail.
        recommended_payload_kg: Maximum recommended payload given the
            configured safety factor.
        leak_risk: Qualitative leak-risk category for the surface material.
        notes: Human-readable summary string.
    """

    holding_force_N: float
    safety_margin: float
    recommended_payload_kg: float
    leak_risk: LeakRisk
    notes: str


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class SuctionGripperModel:
    """Physics-based suction-force model for a vacuum gripper.

    Args:
        spec: Cup specification (geometry, count, vacuum rating).
    """

    def __init__(self, spec: SuctionCupSpec) -> None:
        self.spec = spec

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def compute_cup_area_m2(self) -> float:
        """Total effective sealing area across all cups (m²).

        Formula: π × (r_mm / 1000)² × cup_count
        """
        r_m = self.spec.cup_radius_mm / 1000.0
        return math.pi * r_m ** 2 * self.spec.cup_count

    # ------------------------------------------------------------------
    # Force
    # ------------------------------------------------------------------

    def compute_holding_force_N(
        self,
        material: SurfaceMaterial,
        vacuum_pct: float = 1.0,
    ) -> float:
        """Compute theoretical holding force in Newtons.

        F = area_m² × vacuum_Pa × effectiveness[material]

        where ``vacuum_Pa = max_vacuum_kpa × 1000 × vacuum_pct``.

        Args:
            material: Target surface material.
            vacuum_pct: Fraction of maximum vacuum actually achieved
                (0.0–1.0).  Defaults to 1.0 (full vacuum).

        Returns:
            Holding force in Newtons.
        """
        area = self.compute_cup_area_m2()
        vacuum_pa = self.spec.max_vacuum_kpa * 1000.0 * vacuum_pct
        effectiveness = _EFFECTIVENESS[material]
        return area * vacuum_pa * effectiveness

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------

    def recommended_payload_kg(
        self,
        material: SurfaceMaterial,
        safety_factor: float = 2.0,
    ) -> float:
        """Maximum recommended payload in kg.

        payload = holding_force_N / (safety_factor × 9.81)

        Args:
            material: Target surface material.
            safety_factor: Design safety margin (default 2.0).

        Returns:
            Recommended payload mass in kg.
        """
        force = self.compute_holding_force_N(material)
        return force / (safety_factor * 9.81)

    # ------------------------------------------------------------------
    # Leak risk
    # ------------------------------------------------------------------

    def leak_risk_for(self, material: SurfaceMaterial) -> LeakRisk:
        """Return qualitative leak-risk category for *material*.

        - ``"low"``    — smooth_plastic, glass
        - ``"medium"`` — rough_metal, cardboard
        - ``"high"``   — fabric, wet_surface, porous
        """
        return _LEAK_RISK[material]  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Combined evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        material: SurfaceMaterial,
        payload_kg: Optional[float] = None,
        safety_factor: float = 2.0,
    ) -> GripForceResult:
        """Full holding-force evaluation for a given surface and payload.

        Args:
            material: Target surface material.
            payload_kg: Actual payload to be lifted (kg).  When provided,
                ``safety_margin`` is computed as
                ``holding_force / (payload_kg × 9.81)``.
                When ``None``, ``safety_margin`` is reported relative to
                ``recommended_payload_kg(material, safety_factor)``.
            safety_factor: Design safety factor for recommended-payload
                calculation (default 2.0).

        Returns:
            ``GripForceResult`` with all fields populated.
        """
        force_n = self.compute_holding_force_N(material)
        rec_payload = self.recommended_payload_kg(material, safety_factor)
        risk = self.leak_risk_for(material)

        if payload_kg is not None and payload_kg > 0:
            # Safety margin = available force / required force
            required_n = payload_kg * 9.81
            margin = force_n / required_n
        else:
            # Default: margin relative to recommended payload
            required_n = rec_payload * 9.81 if rec_payload > 0 else 1.0
            margin = force_n / required_n if required_n > 0 else 0.0

        eff = _EFFECTIVENESS[material]
        cup_label = (
            f"{self.spec.cup_count}×{self.spec.cup_radius_mm:.0f} mm"
            f" {self.spec.cup_material}"
        )
        notes = (
            f"{cup_label} on {material}: "
            f"F={force_n:.1f} N, "
            f"eff={eff:.0%}, "
            f"rec. payload≤{rec_payload:.2f} kg, "
            f"leak={risk}"
        )

        return GripForceResult(
            holding_force_N=force_n,
            safety_margin=margin,
            recommended_payload_kg=rec_payload,
            leak_risk=risk,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Gripper type registry
# ---------------------------------------------------------------------------

GRIPPER_TYPE_REGISTRY: Dict[str, SuctionCupSpec] = {
    # Single small nitrile cup — light electronics / PCB handling
    "single_30mm_nitrile": SuctionCupSpec(
        cup_radius_mm=15.0,
        cup_count=1,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=40.0,
        cup_material="nitrile",
    ),
    # Standard single silicone cup — general purpose
    "single_50mm_silicone": SuctionCupSpec(
        cup_radius_mm=25.0,
        cup_count=1,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=100.0,
        cup_material="silicone",
    ),
    # Heavy-duty large silicone cup — boxes / heavy flat goods
    "single_80mm_silicone_heavy": SuctionCupSpec(
        cup_radius_mm=40.0,
        cup_count=1,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=200.0,
        cup_material="silicone",
    ),
    # Dual urethane cups — mid-range deformable surfaces (cardboard cartons)
    "dual_40mm_urethane": SuctionCupSpec(
        cup_radius_mm=20.0,
        cup_count=2,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=120.0,
        cup_material="urethane",
    ),
    # Quad silicone cups — palletising / large flat panels
    "quad_25mm_silicone": SuctionCupSpec(
        cup_radius_mm=12.5,
        cup_count=4,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=150.0,
        cup_material="silicone",
    ),
    # 6-cup array — wide-area contact for sheet goods
    "array_6x40mm": SuctionCupSpec(
        cup_radius_mm=20.0,
        cup_count=6,
        max_vacuum_kpa=80.0,
        flow_rate_lpm=300.0,
        cup_material="silicone",
    ),
    # Bag lifter — single very large cup for flexible packaging
    "bag_lifter_120mm": SuctionCupSpec(
        cup_radius_mm=60.0,
        cup_count=1,
        max_vacuum_kpa=70.0,
        flow_rate_lpm=400.0,
        cup_material="silicone",
    ),
    # Fragile-part handler — small cup, low vacuum to avoid damage
    "fragile_20mm_low_vacuum": SuctionCupSpec(
        cup_radius_mm=10.0,
        cup_count=1,
        max_vacuum_kpa=20.0,
        flow_rate_lpm=20.0,
        cup_material="silicone",
    ),
}


def get_gripper(name: str) -> SuctionCupSpec:
    """Return the ``SuctionCupSpec`` for *name*.

    Raises:
        KeyError: When *name* is not in the registry.
    """
    if name not in GRIPPER_TYPE_REGISTRY:
        raise KeyError(f"Gripper '{name}' not in GRIPPER_TYPE_REGISTRY. "
                       f"Available: {sorted(GRIPPER_TYPE_REGISTRY)}")
    return GRIPPER_TYPE_REGISTRY[name]


def list_grippers() -> List[str]:
    """Return a sorted list of all registered gripper names."""
    return sorted(GRIPPER_TYPE_REGISTRY.keys())
