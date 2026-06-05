"""Phase 21 — Role library: scene-template index by role.

Provides an inverted index mapping industrial robot roles to scene-template
descriptors. Covers welding, picking, assembly, inspection, palletizing,
machine tending, packing, AMR driving, dispensing, and kitting operations.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 21.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


PHASE_ID = "21"
PHASE_TITLE = "Role library: scene-template index by role"
PHASE_STATUS = "landed"


@dataclass
class RoleTemplateEntry:
    """Descriptor linking an industrial role to a scene template."""

    template_id: str
    role: str
    sub_role: str | None = None
    robot_class: str | None = None
    gripper: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Registry — ≥30 entries spanning 10 industrial roles
# ---------------------------------------------------------------------------

ROLE_TEMPLATE_INDEX: list[RoleTemplateEntry] = [
    # ------------------------------------------------------------------
    # welder (4 entries)
    # Remapped 2026-05-15: no TP-WLD-* files exist; remapped to closest
    # existing CPs that demonstrate fixture-hold / precision industrial
    # manipulation. Track F backlog: author dedicated weld-cell templates.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-76",  # dual-robot fixture hold — closest industrial precision cell
        role="welder",
        sub_role="spot_welder",
        robot_class="fanuc_arc_mate",
        gripper="spot_welding_gun",
        tags=["welding", "spot", "automotive"],
        notes="Single-spot resistance welding on sheet-metal panels.",
    ),
    RoleTemplateEntry(
        template_id="CP-02",  # multi-station assembly line — industrial multi-robot workflow [REVIEW]
        role="welder",
        sub_role="mig_welder",
        robot_class="kuka_kr16",
        gripper="mig_torch",
        tags=["welding", "mig", "structural"],
        notes="MIG welding for structural steel frames.",
    ),
    RoleTemplateEntry(
        template_id="CP-24",  # narrow-slot insertion — precision placement analog [REVIEW]
        role="welder",
        sub_role="tig_welder",
        robot_class="yaskawa_gp8",
        gripper="tig_torch",
        tags=["welding", "tig", "stainless", "precision"],
        notes="TIG welding for stainless-steel food-grade enclosures.",
    ),
    RoleTemplateEntry(
        template_id="CP-69",  # UR10 cuRobo — ur10e robot class match; collaborative scale
        role="welder",
        sub_role="robotic_arm_welder",
        robot_class="ur10e",
        gripper="collaborative_welding_torch",
        tags=["welding", "collaborative", "small_batch"],
        notes="Flexible welding arm for small-batch mixed-alloy parts.",
    ),
    # ------------------------------------------------------------------
    # picker (4 entries)
    # Remapped 2026-05-15: TP-PCK-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-01",  # canonical Franka bin-pick from conveyor, parallel jaw
        role="picker",
        sub_role="bin_picker",
        robot_class="franka_panda",
        gripper="parallel_jaw",
        tags=["picking", "bin", "disordered"],
        notes="Bin-picking from disordered totes using depth-camera pose estimation.",
    ),
    RoleTemplateEntry(
        template_id="CP-54",  # Franka surface_gripper (suction) pick, closest to suction_cup_array
        role="picker",
        sub_role="mixed_sku",
        robot_class="ur5e",
        gripper="suction_cup_array",
        tags=["picking", "mixed_sku", "logistics"],
        notes="Mixed-SKU order fulfillment with vision-based SKU classification.",
    ),
    RoleTemplateEntry(
        template_id="CP-35",  # industrial sortation cell, 8 SKUs, barcode_reader + color_routing
        role="picker",
        sub_role="parcel_sorter",
        robot_class="fanuc_m20id",
        gripper="vacuum_gripper",
        tags=["picking", "sorting", "parcel", "conveyor"],
        notes="Parcel sorting from conveyor with barcode-guided destination bins.",
    ),
    RoleTemplateEntry(
        template_id="CP-08",  # 2×2 grid palletizer — closest depalletizing analog
        role="picker",
        sub_role="pallet_picker",
        robot_class="kuka_kr120",
        gripper="layer_gripper",
        tags=["picking", "pallet", "heavy_payload"],
        notes="Full-layer pallet depalletizing onto conveyor.",
    ),
    # ------------------------------------------------------------------
    # assembler (3 entries)
    # Remapped 2026-05-15: TP-ASM-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-24",  # narrow-slot insertion, ±precision placement — closest PCB analog [REVIEW]
        role="assembler",
        sub_role="pcb_assembler",
        robot_class="scara_r6",
        gripper="precision_vacuum_nozzle",
        tags=["assembly", "pcb", "electronics", "precision"],
        notes="PCB component placement with ±0.05 mm repeatability.",
    ),
    RoleTemplateEntry(
        template_id="CP-02",  # multi-station assembly line with two robots and transfer conveyor
        role="assembler",
        sub_role="panel_assembler",
        robot_class="ur10e",
        gripper="magnetic_gripper",
        tags=["assembly", "panel", "sheet_metal"],
        notes="Sheet-metal panel assembly with magnetic pick for thin stock.",
    ),
    RoleTemplateEntry(
        template_id="CP-58",  # peg-in-hole insertion array — bolt/clip/snap-fit analog
        role="assembler",
        sub_role="sub_assembly",
        robot_class="franka_panda",
        gripper="parallel_jaw",
        tags=["assembly", "sub_assembly", "bolt_insertion"],
        notes="Sub-assembly insertion tasks: bolts, clips, snap-fits.",
    ),
    # ------------------------------------------------------------------
    # inspector (3 entries)
    # Remapped 2026-05-15: TP-INS-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-18",  # inspect-and-reject station, semantic good/defect labeling
        role="inspector",
        sub_role="surface_inspector",
        robot_class="fanuc_lrmate200id",
        gripper="structured_light_sensor",
        tags=["inspection", "surface", "defect_detection"],
        notes="Surface scan for scratches, dents, and coating defects.",
    ),
    RoleTemplateEntry(
        template_id="CP-18",  # same station; dimensional checks share inspect-reject workflow [REVIEW]
        role="inspector",
        sub_role="dimensional_inspector",
        robot_class="ur5e",
        gripper="laser_profilometer",
        tags=["inspection", "dimensional", "metrology"],
        notes="Go/no-go dimensional checks against CAD tolerance bands.",
    ),
    RoleTemplateEntry(
        template_id="CP-48",  # TRUE runtime-vision inspect-reject, AI anomaly classifier
        role="inspector",
        sub_role="vision_inspector",
        robot_class="kuka_kr6_r900",
        gripper="machine_vision_camera",
        tags=["inspection", "vision", "ai_defect"],
        notes="AI vision inspection with anomaly segmentation model.",
    ),
    # ------------------------------------------------------------------
    # palletizer (3 entries)
    # Remapped 2026-05-15: TP-PAL-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-08",  # 2×2 grid palletizer — single-layer grid stacking
        role="palletizer",
        sub_role="bag_palletizer",
        robot_class="fanuc_m410ib",
        gripper="clam_gripper",
        tags=["palletizing", "bag", "food_and_beverage"],
        notes="50 kg bag palletizing with clam-shell gripper.",
    ),
    RoleTemplateEntry(
        template_id="CP-10",  # 3×3 grid palletizer — layer pattern
        role="palletizer",
        sub_role="box_palletizer",
        robot_class="kuka_kr120",
        gripper="layer_gripper",
        tags=["palletizing", "box", "consumer_goods"],
        notes="Mixed-height box palletizing with optimised layer-pattern.",
    ),
    RoleTemplateEntry(
        template_id="CP-12",  # mixed-SKU palletizer — 3 different cube sizes
        role="palletizer",
        sub_role="mixed_palletizer",
        robot_class="yaskawa_mv1000",
        gripper="adaptive_gripper",
        tags=["palletizing", "mixed", "ecommerce"],
        notes="Mixed-SKU palletizing with real-time stack stability check.",
    ),
    # ------------------------------------------------------------------
    # machine_tender (3 entries)
    # Remapped 2026-05-15: TP-MCT-* files never existed; no dedicated
    # machine-tending CPs exist. Mapped to closest load/unload analogs.
    # Track F backlog: author CNC/lathe/press tending templates.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-31",  # pick-from-pile — closest load/unload cycle analog [REVIEW]
        role="machine_tender",
        sub_role="cnc_loader",
        robot_class="fanuc_m10ia",
        gripper="pneumatic_chuck",
        tags=["machine_tending", "cnc", "load_unload"],
        notes="CNC machine tending: raw-stock loading + finished-part unloading.",
    ),
    RoleTemplateEntry(
        template_id="CP-69",  # UR10 cuRobo — ur10e robot class match; single-pick cycle
        role="machine_tender",
        sub_role="lathe_tender",
        robot_class="ur10e",
        gripper="parallel_jaw",
        tags=["machine_tending", "lathe", "turning"],
        notes="Collaborative lathe tending with door-interlock safety logic.",
    ),
    RoleTemplateEntry(
        template_id="CP-76",  # dual-robot fixture hold — fixture-interaction closest to press loading [REVIEW]
        role="machine_tender",
        sub_role="press_operator",
        robot_class="kuka_kr16",
        gripper="magnetic_gripper",
        tags=["machine_tending", "press", "stamping"],
        notes="Stamping press loading with die-protection sensor integration.",
    ),
    # ------------------------------------------------------------------
    # packer (3 entries)
    # Remapped 2026-05-15: TP-PKR-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-57",  # parcel-singulation-from-heap — high-speed pick and place
        role="packer",
        sub_role="bagger",
        robot_class="delta_robot_ir800",
        gripper="vacuum_gripper",
        tags=["packing", "bagging", "food_and_beverage"],
        notes="High-speed delta robot bagging for snack foods.",
    ),
    RoleTemplateEntry(
        template_id="CP-77",  # nested-box packer with lid seal — cartoning analog
        role="packer",
        sub_role="cartoner",
        robot_class="fanuc_m1ia",
        gripper="vacuum_cup",
        tags=["packing", "cartoning", "pharma"],
        notes="Pharmaceutical cartoning with tamper-evident seal verification.",
    ),
    RoleTemplateEntry(
        template_id="CP-49",  # kitting station, 4-slot tray, BOM-driven pick — kit-pack analog
        role="packer",
        sub_role="kit_packer",
        robot_class="franka_panda",
        gripper="multi_finger_gripper",
        tags=["packing", "kitting", "retail"],
        notes="Retail gift-kit packing with per-order bill-of-materials.",
    ),
    # ------------------------------------------------------------------
    # AMR_driver (3 entries)
    # Remapped 2026-05-15: TP-AMR-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-64",  # Carter Nav2 mobile robot — tugger / intralogistics navigation
        role="AMR_driver",
        sub_role="tugger",
        robot_class="mir250",
        gripper=None,
        tags=["amr", "tugger", "intralogistics"],
        notes="Tugger AMR pulling cart trains between production cells.",
    ),
    RoleTemplateEntry(
        template_id="CP-NEW-amr-pickup-handoff",  # Nova Carter docks + Franka handoff — bin transport
        role="AMR_driver",
        sub_role="bin_mover",
        robot_class="otto_1500",
        gripper=None,
        tags=["amr", "bin_mover", "warehouse"],
        notes="Autonomous bin transport with dynamic obstacle avoidance.",
    ),
    RoleTemplateEntry(
        template_id="CP-NEW-multi-amr-corridor",  # 3 Nova Carters navigate + handoff — mobile manip [REVIEW]
        role="AMR_driver",
        sub_role="mobile_manip",
        robot_class="mir250_franka",
        gripper="parallel_jaw",
        tags=["amr", "mobile_manipulation", "fetch_and_place"],
        notes="Mobile manipulator: navigate + pick tasks in a shared workspace.",
    ),
    # ------------------------------------------------------------------
    # dispenser (2 entries)
    # Remapped 2026-05-15: TP-DSP-* files never existed; no dedicated
    # dispensing CPs exist. Mapped to closest precision-path analogs.
    # Track F backlog: author glue/sealant dispensing templates.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-58",  # peg-in-hole precision placement — closest controlled-path analog [REVIEW]
        role="dispenser",
        sub_role="glue_dispenser",
        robot_class="fanuc_lrmate200id",
        gripper="glue_nozzle",
        tags=["dispensing", "glue", "bonding"],
        notes="Precision bead dispensing for structural adhesive bonding.",
    ),
    RoleTemplateEntry(
        template_id="CP-69",  # UR10 cuRobo — ur10e robot class match; automotive scale [REVIEW]
        role="dispenser",
        sub_role="sealant_dispenser",
        robot_class="ur10e",
        gripper="sealant_nozzle",
        tags=["dispensing", "sealant", "sealing", "automotive"],
        notes="Weather-seal sealant bead for automotive body panels.",
    ),
    # ------------------------------------------------------------------
    # kitter (2 entries)
    # Remapped 2026-05-15: TP-KIT-* files never existed.
    # ------------------------------------------------------------------
    RoleTemplateEntry(
        template_id="CP-49",  # kitting station, 4-slot kit tray, JIT sequenced pick
        role="kitter",
        sub_role="parts_to_tray",
        robot_class="ur5e",
        gripper="vacuum_gripper",
        tags=["kitting", "tray", "sequencing"],
        notes="Part-to-tray kitting for sequenced JIT delivery to assembly.",
    ),
    RoleTemplateEntry(
        template_id="CP-50",  # vision-driven kitting, 2-color BOM routing into kit tray
        role="kitter",
        sub_role="kit_assembly",
        robot_class="franka_panda",
        gripper="multi_finger_gripper",
        tags=["kitting", "kit_assembly", "bom_driven"],
        notes="BOM-driven kit assembly: picks components into carrier trays.",
    ),
]


# ---------------------------------------------------------------------------
# Index class
# ---------------------------------------------------------------------------

class RoleTemplateIndex:
    """Inverted index over a :data:`ROLE_TEMPLATE_INDEX`-compatible list."""

    def __init__(self, entries: list[RoleTemplateEntry] | None = None) -> None:
        """Initialise the index with an optional entry list; defaults to the module-level catalogue."""
        self._entries: list[RoleTemplateEntry] = (
            list(entries) if entries is not None else list(ROLE_TEMPLATE_INDEX)
        )

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def by_role(self, role: str) -> list[RoleTemplateEntry]:
        """Return all entries whose *role* exactly matches *role*."""
        return [e for e in self._entries if e.role == role]

    def by_robot_class(self, robot_class: str) -> list[RoleTemplateEntry]:
        """Return all entries whose *robot_class* exactly matches *robot_class*."""
        return [e for e in self._entries if e.robot_class == robot_class]

    def by_tag(self, tag: str) -> list[RoleTemplateEntry]:
        """Return all entries that carry *tag* in their tags list."""
        return [e for e in self._entries if tag in e.tags]

    def all_roles(self) -> list[str]:
        """Return sorted list of unique role names present in the index."""
        return sorted({e.role for e in self._entries})

    def count_by_role(self) -> Dict[str, int]:
        """Return a mapping of role → entry count."""
        counts: Dict[str, int] = {}
        for e in self._entries:
            counts[e.role] = counts.get(e.role, 0) + 1
        return counts


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 21",
        "entry_count": len(ROLE_TEMPLATE_INDEX),
    }
