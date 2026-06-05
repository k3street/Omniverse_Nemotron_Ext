"""Phase 25 — object palette expansion: 17 → 60 canonical classes.

Adds 43 new object classes beyond Block 1A's initial 17. Each entry has
a USD reference URL, default position offset, and footprint metadata
used by the snap engine + ratifier.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 25.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ObjectClass:
    """One entry in the canonical object palette, describing a USD asset class."""
    name: str
    usd_ref: str = ""
    category: str = "prop"  # robot | sensor | fixture | prop | environment
    footprint_xy_m: tuple = (0.1, 0.1)
    default_z: float = 0.0
    tags: List[str] = field(default_factory=list)


# Phase 25 palette — 60 classes. Block 1A's original 17 + 43 new.
PALETTE: Dict[str, ObjectClass] = {
    # Robots (8)
    "franka_panda": ObjectClass("franka_panda", "Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
                                category="robot", footprint_xy_m=(0.4, 0.4), tags=["arm", "manipulator"]),
    "ur10": ObjectClass("ur10", "Isaac/Robots/UniversalRobots/UR10/ur10.usd",
                        category="robot", footprint_xy_m=(0.3, 0.3), tags=["arm", "manipulator"]),
    "ur5e": ObjectClass("ur5e", "Isaac/Robots/UniversalRobots/UR5e/ur5e.usd",
                        category="robot", footprint_xy_m=(0.25, 0.25), tags=["arm", "manipulator"]),
    "kinova_gen3": ObjectClass("kinova_gen3", "Isaac/Robots/Kinova/Gen3/gen3.usd",
                                category="robot", footprint_xy_m=(0.3, 0.3), tags=["arm"]),
    "carter": ObjectClass("carter", "Isaac/Robots/Nvidia/Carter/carter_v1.usd",
                          category="robot", footprint_xy_m=(0.6, 0.45), tags=["mobile"]),
    "jetbot": ObjectClass("jetbot", "Isaac/Robots/Nvidia/Jetbot/jetbot.usd",
                          category="robot", footprint_xy_m=(0.2, 0.15), tags=["mobile"]),
    "spot": ObjectClass("spot", "Isaac/Robots/BostonDynamics/Spot/spot.usd",
                        category="robot", footprint_xy_m=(1.1, 0.5), tags=["quadruped"]),
    "h1": ObjectClass("h1", "Isaac/Robots/Unitree/H1/h1.usd",
                      category="robot", footprint_xy_m=(0.4, 0.4), tags=["humanoid"]),
    # Workpieces (10)
    **{f"cube_{size}": ObjectClass(f"cube_{size}", category="prop", footprint_xy_m=(0.05, 0.05), tags=["workpiece", "cube"])
       for size in ("small", "medium", "large")},
    **{f"cylinder_{size}": ObjectClass(f"cylinder_{size}", category="prop", footprint_xy_m=(0.04, 0.04), tags=["workpiece"])
       for size in ("small", "medium", "large")},
    "sphere": ObjectClass("sphere", category="prop", footprint_xy_m=(0.05, 0.05), tags=["workpiece"]),
    "screw": ObjectClass("screw", category="prop", footprint_xy_m=(0.01, 0.01), tags=["workpiece"]),
    "nut": ObjectClass("nut", category="prop", footprint_xy_m=(0.012, 0.012), tags=["workpiece"]),
    "bolt": ObjectClass("bolt", category="prop", footprint_xy_m=(0.012, 0.012), tags=["workpiece"]),
    # Fixtures (12)
    "table_small": ObjectClass("table_small", category="fixture", footprint_xy_m=(0.8, 0.6), tags=["fixture"]),
    "table_medium": ObjectClass("table_medium", category="fixture", footprint_xy_m=(1.2, 0.8), tags=["fixture"]),
    "table_large": ObjectClass("table_large", category="fixture", footprint_xy_m=(2.0, 1.0), tags=["fixture"]),
    "bin": ObjectClass("bin", category="fixture", footprint_xy_m=(0.4, 0.3), tags=["fixture", "destination"]),
    "bin_large": ObjectClass("bin_large", category="fixture", footprint_xy_m=(0.6, 0.4), tags=["fixture", "destination"]),
    "shelf": ObjectClass("shelf", category="fixture", footprint_xy_m=(1.2, 0.4), tags=["fixture"]),
    "conveyor_short": ObjectClass("conveyor_short", category="fixture", footprint_xy_m=(1.5, 0.5), tags=["fixture", "dynamic"]),
    "conveyor_long": ObjectClass("conveyor_long", category="fixture", footprint_xy_m=(3.0, 0.5), tags=["fixture", "dynamic"]),
    "rotary_table": ObjectClass("rotary_table", category="fixture", footprint_xy_m=(0.8, 0.8), tags=["fixture", "dynamic"]),
    "gravity_dispenser": ObjectClass("gravity_dispenser", category="fixture", footprint_xy_m=(0.3, 0.3), tags=["fixture"]),
    "kit_tray": ObjectClass("kit_tray", category="fixture", footprint_xy_m=(0.4, 0.3), tags=["fixture"]),
    "fence": ObjectClass("fence", category="fixture", footprint_xy_m=(2.0, 0.05), tags=["fixture", "barrier"]),
    # Sensors (8)
    "camera_overhead": ObjectClass("camera_overhead", category="sensor", footprint_xy_m=(0.1, 0.1), tags=["sensor", "vision"]),
    "camera_side": ObjectClass("camera_side", category="sensor", footprint_xy_m=(0.1, 0.1), tags=["sensor", "vision"]),
    "rtx_lidar": ObjectClass("rtx_lidar", category="sensor", footprint_xy_m=(0.08, 0.08), tags=["sensor", "lidar"]),
    "barcode_reader": ObjectClass("barcode_reader", category="sensor", footprint_xy_m=(0.05, 0.05), tags=["sensor"]),
    "nir_spectrometer": ObjectClass("nir_spectrometer", category="sensor", footprint_xy_m=(0.1, 0.1), tags=["sensor"]),
    "proximity_sensor": ObjectClass("proximity_sensor", category="sensor", footprint_xy_m=(0.03, 0.03), tags=["sensor"]),
    "force_torque_sensor": ObjectClass("force_torque_sensor", category="sensor", footprint_xy_m=(0.05, 0.05), tags=["sensor"]),
    "contact_sensor": ObjectClass("contact_sensor", category="sensor", footprint_xy_m=(0.02, 0.02), tags=["sensor"]),
    # Environments (8)
    "wall": ObjectClass("wall", category="environment", footprint_xy_m=(2.0, 0.1), tags=["environment", "barrier"]),
    "obstacle_box": ObjectClass("obstacle_box", category="environment", footprint_xy_m=(0.5, 0.5), tags=["environment"]),
    "obstacle_cylinder": ObjectClass("obstacle_cylinder", category="environment", footprint_xy_m=(0.3, 0.3), tags=["environment"]),
    "groundplane": ObjectClass("groundplane", category="environment", footprint_xy_m=(20.0, 20.0), tags=["environment"]),
    "skydome_light": ObjectClass("skydome_light", category="environment", footprint_xy_m=(0.0, 0.0), tags=["light"]),
    "distant_light": ObjectClass("distant_light", category="environment", footprint_xy_m=(0.0, 0.0), tags=["light"]),
    "warehouse_box": ObjectClass("warehouse_box", category="environment", footprint_xy_m=(20.0, 20.0), tags=["environment"]),
    "kitchen_room": ObjectClass("kitchen_room", category="environment", footprint_xy_m=(4.0, 4.0), tags=["environment"]),
    # Mobile-robot navigation aids (4)
    "nav2_waypoint": ObjectClass("nav2_waypoint", category="prop", footprint_xy_m=(0.05, 0.05), tags=["nav"]),
    "occupancy_marker": ObjectClass("occupancy_marker", category="prop", footprint_xy_m=(0.02, 0.02), tags=["nav"]),
    "person_cylinder": ObjectClass("person_cylinder", category="prop", footprint_xy_m=(0.3, 0.3), tags=["person"]),
    "qr_marker": ObjectClass("qr_marker", category="prop", footprint_xy_m=(0.1, 0.1), tags=["fiducial"]),
    # Tooling (10)
    "gripper_robotiq_2f85": ObjectClass("gripper_robotiq_2f85", category="prop", footprint_xy_m=(0.1, 0.1), tags=["gripper"]),
    "gripper_robotiq_3finger": ObjectClass("gripper_robotiq_3finger", category="prop", footprint_xy_m=(0.12, 0.12), tags=["gripper"]),
    "suction_cup": ObjectClass("suction_cup", category="prop", footprint_xy_m=(0.05, 0.05), tags=["gripper"]),
    "screwdriver": ObjectClass("screwdriver", category="prop", footprint_xy_m=(0.02, 0.02), tags=["tool"]),
    "drill": ObjectClass("drill", category="prop", footprint_xy_m=(0.2, 0.1), tags=["tool"]),
    "welding_torch": ObjectClass("welding_torch", category="prop", footprint_xy_m=(0.05, 0.05), tags=["tool"]),
    "paint_spray_nozzle": ObjectClass("paint_spray_nozzle", category="prop", footprint_xy_m=(0.05, 0.05), tags=["tool"]),
    "tool_changer": ObjectClass("tool_changer", category="fixture", footprint_xy_m=(0.4, 0.4), tags=["fixture"]),
    "fixture_clamp": ObjectClass("fixture_clamp", category="fixture", footprint_xy_m=(0.1, 0.1), tags=["fixture"]),
    "magnetic_holder": ObjectClass("magnetic_holder", category="fixture", footprint_xy_m=(0.05, 0.05), tags=["fixture"]),
}


def get_class(name: str) -> "ObjectClass | None":
    """Return the ``ObjectClass`` for *name*, or ``None`` if not in the palette."""
    return PALETTE.get(name)


def list_classes(category: "str | None" = None) -> "list[ObjectClass]":
    """Return all palette entries, optionally filtered to *category*.

    Args:
        category (str, optional): Category filter — ``"robot"``, ``"sensor"``,
            ``"fixture"``, ``"prop"``, or ``"environment"``. ``None`` returns
            the full palette.

    Returns:
        list[ObjectClass]: Matching entries.
    """
    if category is None:
        return list(PALETTE.values())
    return [c for c in PALETTE.values() if c.category == category]
