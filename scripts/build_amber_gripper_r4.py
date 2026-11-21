"""Parametric bilateral CAD, internal drive & qualification generator for
Amber v0.5 `tool_gripper_r4` docked onto `Wrist_Tool_Changer_R2` (`cad_creator` read-only).

Matches the exact architectural geometry & posture of the CSU Chico MECA-440B ("Team 10 Things")
Capture Glove (`Slide 11` final CAD) while preserving 100% of R2/R3 engineering lessons:

1. **Glove-Matched Anthropomorphic C-Mouth Chassis & Digit Architecture**:
   - **Winged Bent-Angle Side Cheek Plates** (`radial` & `ulnar`) with angled lower wrist skirts,
     elevated horizontal **MCP Knuckle Shaft (`Ø13 mm`)**, and **Forward Palmar Apron Shelf**.
   - **Dual Framed Side Cheek Pods**: Radial angled `ArduCam` RGB wrist camera wedge housing
     aimed into the pinch workspace + Ulnar framed electronics/sensor cheek pod.
   - **Exposed 6-Notched Rotary Joint Encoder Hubs** (`5` absolute joint angle hubs matching the
     glove's 5 Bourns rotary sensors for 1:1 teleop parity).
   - **Digit 2 (`ulnar`, 52 mm wide Coupled Middle-Fourth-Pinkie)**: Open **Twin-Strut Ladder
     Proximal Frame** + overhanging **Wide Chiseled / Beveled White Distal Paddle Block** carrying
     `3` flush Contactile `3DFBS-12T-50N` tactile domes.
   - **Digit 1 (`index`, 24 mm wide Single Index)**: Open **Twin-Bar Clevis Proximal Frame** +
     overhanging **Single Chiseled / Beveled White Distal Block** carrying `1` flush Contactile dome.
   - **Digit 3 (`thumb`, 28 mm wide Opposing Thumb)**: Low radial-palmar motorized worm-locked
     `-20° / 0° / +20°` 3-position opposition turret + **Enlarged Pentagonal Chiseled Thumb Block**
     with dorsal alignment tab and `1` flush Contactile dome.

2. **Full R2/R3 Engineering Inheritance Preserved**:
   - **Enclosed 2-Layer, 3-Deck Self-Locking Worm Spool Drives** (`floor_deck`, `mid_deck`,
     `top_deck` on `4×` steel columns tucked inside the lower wrist-palm belly): `6` Pololu HPCB
     12V encoder gearmotors -> `WORM3` self-locking worms -> `CuSn12` bronze worm wheels ->
     dual-groove SK78 Dyneema tendon drums + `7th` worm locking the Thumb `-20°/0°/+20°` gear ring
     + `500 Hz` `< 5 ms` local reflex arbiter PCB.
   - **Contoured `Shore A 60` PU Palmar Stability Saddle + Chamfered MCP Palmar Pad** with
     `1.0×0.5 mm` anti-peel groove and `4× Ohmite FSR07` palm force zones (`9` total tactile zones).
   - **Removable `M1.6` Dorsal Tip Service Crowns**, protected `8-way` FFC dorsal strut channels,
     flat `0.500 mm` table-pinch tip bevel (`GR3-PEN-01`), `1 L` wet bottle wrap, `Ø50.8 mm` ADA
     door-pull hook, and **un-mirrored `Ø52×6 mm` `base_disc`** (`R = 22 mm`, `30°/150°/225°` M3)
     docked onto `Wrist_Tool_Changer_R2` across both Right and Left hands.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

REPO_ROOT = Path(__file__).resolve().parents[1]
CAD_CREATOR_ROOT = Path(r"C:\Users\Owner\Documents\Github\cad_creator")
OUT_ROOT = REPO_ROOT / "workspace" / "amber_gripper_r4"

DEG = math.pi / 180.0

DENSITIES_KG_M3: dict[str, float] = {
    "7075-T6": 2810.0,
    "nylon_12_sls": 1010.0,
    "steel_303": 7900.0,
    "cusn12_bronze": 8800.0,
    "pu_shore_a60": 1200.0,
    "fr4_pcb": 1850.0,
    "pololu_hpcb_motor": 4100.0,
    "contactile_3dfbs": 1450.0,
    "polyimide_ffc": 1420.0,
    "arducam_optomech": 1650.0,
}

PART_COLORS_RGB: dict[str, tuple[float, float, float]] = {
    "palm_shell_offwhite": (0.87, 0.87, 0.86),  # Crisp architectural glove-matched matte white SLS (non-clipping)
    "cheek_pod_grey": (0.63, 0.65, 0.69),       # Distinct medium-light grey side camera/sensor pods (matches glove)
    "rotary_hub_dark": (0.25, 0.27, 0.30),      # Crisp charcoal 6-notched Bourns/encoder rotary joint caps
    "palm_skin_pu": (0.42, 0.46, 0.52),         # Cast Shore A 60 PU contoured palmar stability saddle
    "finger_skin_pu": (0.34, 0.38, 0.44),       # Flush Shore A 60 PU grip soles under chiseled fingertips
    "alu_7075": (0.72, 0.75, 0.79),             # Clear-anodized 7075-T6 internal decks & clevis pins
    "alu_dark": (0.20, 0.22, 0.26),             # Hard-anodized 7075-T6 base_disc
    "tip_crown": (0.84, 0.85, 0.86),            # Removable M1.6 dorsal tip service crowns
    "contactile_dome": (0.72, 0.78, 0.84),      # Flush Contactile 3DFBS-12T-50N tactile domes
    "fsr_subpad": (0.14, 0.62, 0.58),           # Embedded Ohmite FSR07 palm force sensors
    "ffc_guard": (0.82, 0.60, 0.24),            # Polyimide 8-way FFC dorsal strut channels
    "tendon_sk78": (0.26, 0.52, 0.86),          # Coated Dyneema SK78 antagonistic tendons
    "worm_bronze": (0.78, 0.54, 0.24),          # CuSn12 worm wheels & thumb opposition gear ring
    "steel": (0.68, 0.71, 0.75),                # WORM3 worms, shafts, columns & fasteners
    "motor_body": (0.28, 0.31, 0.36),           # Pololu HPCB 12V micro metal gearmotors w/ encoders
    "pcb_green": (0.15, 0.50, 0.28),            # 500 Hz reflex arbiter PCB & ArduCam board
    "lens_black": (0.12, 0.13, 0.16),           # Radial ArduCam M12 lens barrel
    "wtc_master": (0.16, 0.18, 0.21),           # Docked Wrist Tool Changer R2 housing
    "wtc_tool": (0.24, 0.27, 0.31),             # Docked Wrist Tool Changer R2 tool plate
    "wtc_gold": (0.88, 0.72, 0.20),             # Mill-Max spring-pin contacts
    "test_object": (0.28, 0.62, 0.42),          # Benchmark test payloads (1L bottle, pen, ADA bar)
}


# ---------------------------------------------------------------------------
# SE(3) & OpenCASCADE Helpers
# ---------------------------------------------------------------------------

def rot_x(deg: float) -> np.ndarray:
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=float)


def rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=float)


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def make_T(R: np.ndarray, t_mm: tuple[float, float, float] | list[float] | np.ndarray) -> np.ndarray:
    T = np.eye(4, dtype=float)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(t_mm, dtype=float)
    return T


def mat_to_rpy(R: np.ndarray) -> tuple[float, float, float]:
    sy = math.hypot(R[0, 0], R[1, 0])
    if sy > 1e-7:
        return (math.atan2(R[2, 1], R[2, 2]), math.atan2(-R[2, 0], sy), math.atan2(R[1, 0], R[0, 0]))
    return (math.atan2(-R[1, 2], R[1, 1]), math.atan2(-R[2, 0], sy), 0.0)


def transform_shape(shape: cq.Shape, T: np.ndarray) -> cq.Shape:
    m = cq.Matrix(
        [
            [float(T[0, 0]), float(T[0, 1]), float(T[0, 2]), float(T[0, 3])],
            [float(T[1, 0]), float(T[1, 1]), float(T[1, 2]), float(T[1, 3])],
            [float(T[2, 0]), float(T[2, 1]), float(T[2, 2]), float(T[2, 3])],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    return shape.transformGeometry(m)


def make_box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Solid:
    return cq.Solid.makeBox(
        abs(x1 - x0),
        abs(y1 - y0),
        abs(z1 - z0),
        cq.Vector(min(x0, x1), min(y0, y1), min(z0, z1)),
    )


def make_cyl(start_mm: tuple[float, float, float], axis: tuple[float, float, float], r: float, length: float) -> cq.Solid:
    return cq.Solid.makeCylinder(r, length, cq.Vector(*start_mm), cq.Vector(*axis))


def min_distance_mm(a: cq.Shape, b: cq.Shape) -> float:
    ext = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    ext.Perform()
    if not ext.IsDone():
        return 999.0
    return float(ext.Value())


# ---------------------------------------------------------------------------
# Glove-Matched Anthropomorphic Layout & Benchmark Poses
# ---------------------------------------------------------------------------

def r4_layout(z0: float, hand: str = "right") -> dict[str, Any]:
    """Anatomical right-hand (`s = +1`) / left-hand (`s = -1`) frame above `Wrist_Tool_Changer_R2`:
      - `+Z`: Wrist-to-distal axis (`z0 = 18.95 mm` changer top, `zb = 24.95 mm` base_disc top)
      - `-X`: Palmar / grasp-mouth front; `+X`: Dorsal / MCP knuckle shaft side
      - `-s * Y`: Radial side (`Index` & `Thumb`); `+s * Y`: Ulnar side (`Wide Coupled 3-Finger`)
    """
    s = 1.0 if hand.lower() == "right" else -1.0
    zb = z0 + 6.0  # 24.95 mm

    # Shared horizontal MCP knuckle bar at elevated dorsal ridge
    x_mcp = 22.0
    z_mcp = zb + 64.0  # 88.95 mm

    # Proximal & distal lengths sized so max Z above z0 <= 193.0 mm (`GRP-REACH-01`)
    L1_ulnar = 56.0
    L2_ulnar = 54.0
    L1_index = 55.0
    L2_index = 50.0

    # Distal palmar grip sole offset below distal link axis
    e_dist = 13.0

    # Thumb 3-position (-20 / 0 / +20 deg) opposition turret at low radial-palmar apron corner
    x_th_turret = -26.0
    y_th_turret = -s * 24.0
    z_th_turret = zb + 24.0  # 48.95 mm
    L1_thumb = 22.0
    L2_thumb = 36.5
    e_thumb = 11.5

    # Y-centers & widths matching Slide 11 Capture Glove:
    #   Index (radial): center `-s * 26.5`, width `24.0` -> spans `[-38.5, -14.5]` (for right hand)
    #   Coupled Middle-Fourth-Pinkie (ulnar): center `+s * 14.5`, width `52.0` -> spans `[-11.5, +40.5]`
    #   Thumb: turret at `-s * 24.0`, width `28.0` -> at 0° spans `[-38.0, -10.0]`; at +20° swings across midline!
    return {
        "hand": hand.lower(),
        "s": s,
        "z0": z0,
        "zb": zb,
        "x_mcp": x_mcp,
        "z_mcp": z_mcp,
        "L1": L1_ulnar,
        "L2": L2_ulnar,
        "L1_ulnar": L1_ulnar,
        "L2_ulnar": L2_ulnar,
        "L1_index": L1_index,
        "L2_index": L2_index,
        "e": e_dist,
        "D": 70.0,
        "index_y": -s * 26.5,
        "index_w": 24.0,
        "ulnar_y": s * 14.5,
        "ulnar_w": 52.0,
        "x_th_turret": x_th_turret,
        "y_th_turret": y_th_turret,
        "z_th_turret": z_th_turret,
        "L1_thumb": L1_thumb,
        "L2_thumb": L2_thumb,
        "e_thumb": e_thumb,
        "thumb_w": 28.0,
        "palm_width_mm": 98.0,
        "tool_point_z_above_z0_mm": 176.4,
        "pinch_alpha_deg": 88.0,
    }


def benchmark_poses(lay: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """6 engineering qualification & teleop poses:
    Angles `(mcp_deg, pip_deg)`:
      - For Index & Coupled Ulnar: `mcp_deg = 0` points `+Z`, positive flexes forward (`-X`);
        `mcp + pip = 90°` places the chiseled distal block horizontal over the palm (`-X`)!
      - For Thumb: `mcp_deg = 0` points forward (`-X`), positive tilts UP (`+Z`) toward the fingers!
    """
    zb = lay["zb"]
    return {
        "glove_showcase": {
            # Exact resting C-posture shown in `media_1795214756074_c806eaec.jpg` (Slide 11)!
            "thumb_opp_deg": 0.0,
            "ulnar": (8.0, 80.0),
            "index": (34.0, 52.0),
            "thumb": (10.0, 36.0),
        },
        "open": {
            # Wide pre-grasp C-mouth opening (> 95 mm vertical aperture)
            "thumb_opp_deg": 0.0,
            "ulnar": (-2.0, 58.0),
            "index": (8.0, 52.0),
            "thumb": (2.0, 22.0),
        },
        "thumb_slot_neg20": {
            # Fine radial/lateral pinch slot (-20 deg thumb opposition)
            "thumb_opp_deg": -20.0,
            "ulnar": (8.0, 80.0),
            "index": (36.0, 54.0),
            "thumb": (14.0, 36.0),
        },
        "wrap_bottle_1l": {
            # 1.04 kg wet Ø81 mm (r = 40.5 mm) bottle cradled on the Shore A 60 PU palmar saddle,
            # wrapped from above by Index + Coupled Ulnar and wedged from front-below by +20° Thumb!
            "thumb_opp_deg": 20.0,
            "ulnar": (6.0, 80.5),
            "index": (6.0, 79.5),
            "thumb": (18.0, 44.0),
            "payload": {
                "kind": "bottle_1l",
                "x": -22.5,
                "z": zb + 36.5 + 40.5,  # Seated directly on palmar PU saddle (zb + 36.5 mm)!
                "r": 40.5,
                "len": 116.0,
            },
        },
        "hook_bar_max": {
            # Unpowered worm-locked ADA 505.7 handrail hook (Ø50.8 mm bar, r = 25.4 mm, door 38.1 mm beyond)
            "thumb_opp_deg": -20.0,
            "ulnar": (10.0, 82.0),
            "index": (10.0, 82.0),
            "thumb": (54.0, 22.0),
            "payload": {
                "kind": "ada_bar",
                "x": -18.0,
                "z": zb + 80.5,
                "r": 25.4,
                "door_x": -18.0 - 25.4 - 38.1,  # -81.5 mm (> 15 mm finger & thumb clearance!)
            },
        },
        "pinch_pen": {
            # GR3-PEN-01: Ø8.0 mm pen (r = 4.0 mm) resting on a flat tabletop (`table_x = -83.46 mm`),
            # pinched between Thumb (+20° slot overlapping both Index & Ulnar) and overhanging chiseled
            # Index + Ulnar blocks, with flat-beveled front noses standing 0.500 mm clear of the table!
            "thumb_opp_deg": 20.0,
            "ulnar": (65.5, 24.5),
            "index": (72.0, 22.2),
            "thumb": (51.8, -32.0),
            "payload": {
                "kind": "pen_on_table",
                "x": -79.46,
                "z": zb + 70.8,
                "r": 4.0,
                "table_x": -83.46,
            },
        },
    }


# ---------------------------------------------------------------------------
# Detailed Glove-Matched B-Rep Component Builders
# ---------------------------------------------------------------------------

def _make_notched_rotary_hub(center_mm: tuple[float, float, float], y_dir: float, r_outer: float = 7.6, thick: float = 4.2) -> cq.Shape:
    """Signature dark-grey 6-notched Bourns/encoder rotary joint cap visible at the glove's
    MCP, PIP, and thumb joints (`media_1795214756074_c806eaec.jpg`).
    """
    cx, cy, cz = center_mm
    disc: cq.Shape = make_cyl((cx, cy, cz), (0.0, y_dir, 0.0), r_outer, thick)
    for k in range(6):
        ang = (k * 60.0 + 15.0) * DEG
        nx = cx + (r_outer + 0.3) * math.cos(ang)
        nz = cz + (r_outer + 0.3) * math.sin(ang)
        notch = make_cyl((nx, cy - 0.5 * y_dir, nz), (0.0, y_dir, 0.0), 1.65, thick + 1.0)
        disc = disc.cut(notch)
    pin = make_cyl((cx, cy, cz), (0.0, y_dir, 0.0), 2.4, thick + 1.1)
    return disc.fuse(pin)


def _make_winged_cheek_plate(zb: float, y0: float, y1: float) -> cq.Shape:
    """Exact bent-wing side cheek plate profile from `media_1795214756074_c806eaec.jpg`:
    - Lower angled wrist skirt sweeping forward to `(-16, zb - 7)`
    - Forward palmar apron support out to `(-38, zb + 33)`
    - C-throat concave bend at `(-8, zb + 34)` rising at ~60 deg to the MCP shaft boss `(+22, zb + 64)`
    - Sloped dorsal rear edge `(+37, zb + 40)` back to the wrist heel `(+19, zb - 3)`.
    """
    pts_xz = [
        (-38.0, zb + 33.0),
        (-8.0, zb + 34.0),
        (14.5, zb + 72.0),
        (29.5, zb + 72.0),
        (37.5, zb + 41.0),
        (19.0, zb - 3.0),
        (-15.5, zb - 7.5),
        (-38.0, zb + 24.5),
    ]
    y_min, y_max = min(y0, y1), max(y0, y1)
    wp = cq.Workplane("XZ").workplane(offset=-y_max).polyline(pts_xz).close().extrude(y_max - y_min)
    try:
        return wp.edges("|Y").fillet(3.2).val()
    except Exception:
        return wp.val()


def _build_cheek_pods(zb: float, s: float) -> list[dict[str, Any]]:
    """Build BOTH framed square side cheek pods seen in `media_1795214756074_c806eaec.jpg`:
      1. Radial angled `ArduCam` wrist RGB camera wedge housing aimed into the C-mouth pinch zone.
      2. Ulnar framed square sensor/MCU side pod (with recessed pocket, round boss & connector slot).
    """
    parts: list[dict[str, Any]] = []

    # 1. Ulnar side pod (`y = +s * 44.5 mm`, parallel to upper cheek slope ~28 deg)
    T_uln = make_T(rot_y(-28.0), (14.0, s * 44.5, zb + 43.0))
    frame_u = make_box(-14.5, 14.5, 0.0, s * 5.2, -13.5, 13.5)
    pocket_u = make_box(-10.5, 10.5, s * 2.2, s * 6.0, -9.5, 9.5)
    frame_u = frame_u.cut(pocket_u)
    boss_u = make_cyl((0.0, s * 2.2, 1.5), (0.0, s, 0.0), 4.2, 3.8)
    slot_u = make_box(-5.5, 5.5, s * 2.2, s * 4.4, -7.8, -4.2)
    pod_u = transform_shape(frame_u.fuse(boss_u).fuse(slot_u), T_uln)
    parts.append(
        {
            "name": "r4_ulnar_cheek_electronics_pod",
            "group": "R4_Glove_Matched_Palm_Chassis",
            "shape": pod_u,
            "color_key": "cheek_pod_grey",
            "material": "nylon_12_sls",
            "is_shell": False,
        }
    )

    # 2. Radial angled ArduCam wedge pod (`y = -s * 44.5 mm`, canted inward + upward toward pinch mouth!)
    R_cam = rot_z(s * 18.0) @ rot_y(-24.0)
    T_rad = make_T(R_cam, (6.0, -s * 44.5, zb + 41.0))
    wedge = make_box(-14.5, 14.5, -s * 6.5, 0.5 * s, -14.0, 14.0)
    recess = make_box(-11.0, 11.0, -s * 7.0, -s * 3.0, -10.5, 10.5)
    wedge = wedge.cut(recess)
    pcb = transform_shape(make_box(-10.2, 10.2, -s * 4.2, -s * 2.8, -9.8, 9.8), T_rad)
    lens_housing = make_box(-5.5, 5.5, -s * 8.2, -s * 4.0, -5.5, 5.5)
    lens_barrel = make_cyl((0.0, -s * 8.0, 0.0), (-0.75, s * 0.25, 0.60), 4.2, 4.5)
    parts.append(
        {
            "name": "r4_radial_arducam_wedge_shroud",
            "group": "R4_Wrist_Vision_And_Tactile",
            "shape": transform_shape(wedge.fuse(lens_housing), T_rad),
            "color_key": "cheek_pod_grey",
            "material": "nylon_12_sls",
            "is_shell": False,
        }
    )
    parts.append(
        {
            "name": "r4_radial_arducam_pcb",
            "group": "R4_Wrist_Vision_And_Tactile",
            "shape": pcb,
            "color_key": "pcb_green",
            "material": "fr4_pcb",
            "is_shell": False,
        }
    )
    parts.append(
        {
            "name": "r4_radial_arducam_m12_lens",
            "group": "R4_Wrist_Vision_And_Tactile",
            "shape": transform_shape(lens_barrel, T_rad),
            "color_key": "lens_black",
            "material": "arducam_optomech",
            "is_shell": False,
        }
    )
    return parts


def _build_palm_chassis_and_2layer_drives(
    lay: dict[str, Any],
    screw_angles_deg: list[float],
) -> tuple[list[dict[str, Any]], list[cq.Shape]]:
    """Build:
    1. Un-mirrored `Ø52×6 mm` 7075-T6 `base_disc` (`R = 22 mm`, `30°/150°/225°` M3 circle).
    2. Compact 2-layer, 3-deck (`floor_deck`, `mid_deck`, `top_deck`) self-locking worm-spool
       drive module (`6×` Pololu HPCB worm-locked SK78 spools + `7th` thumb-opposition worm +
       `500 Hz` reflex PCB) inside the lower wrist-palm drive belly (`is_shell = True`).
    3. Glove-matched Winged Side Cheek Plates + Forward Palmar Apron Shelf + Chamfered Upper
       MCP Palmar Block + Horizontal White Cylindrical MCP Shaft + 6-notched Rotary Hubs.
    4. Moulded Twin-Eminence `Shore A 60` PU Palmar Stability Saddle + `4× Ohmite FSR07` sensors.
    """
    z0, zb, s = lay["z0"], lay["zb"], lay["s"]
    x_mcp, z_mcp = lay["x_mcp"], lay["z_mcp"]
    parts: list[dict[str, Any]] = []
    palm_link_shapes: list[cq.Shape] = []

    # 1. Standardized un-mirrored 7075-T6 `base_disc`
    base_disc: cq.Shape = make_cyl((0.0, 0.0, z0), (0.0, 0.0, 1.0), 26.0, 6.0)
    base_disc = base_disc.cut(make_cyl((0.0, 0.0, z0 - 0.5), (0.0, 0.0, 1.0), 6.0, 7.0))
    for i, a_deg in enumerate(screw_angles_deg):
        sx = 22.0 * math.cos(a_deg * DEG)
        sy = 22.0 * math.sin(a_deg * DEG)
        base_disc = base_disc.cut(make_cyl((sx, sy, z0 - 0.5), (0.0, 0.0, 1.0), 1.7, 7.0))
        bolt = make_cyl((sx, sy, z0 - 4.0), (0.0, 0.0, 1.0), 1.5, 9.5).fuse(
            make_cyl((sx, sy, zb - 2.6), (0.0, 0.0, 1.0), 2.75, 2.5)
        )
        parts.append(
            {
                "name": f"r4_changer_mount_m3_{i}",
                "group": "R4_Changer_Interface",
                "shape": bolt,
                "color_key": "steel",
                "material": "steel_303",
                "is_shell": False,
            }
        )
    parts.append(
        {
            "name": "r4_base_disc_7075_unmirrored",
            "group": "R4_Changer_Interface",
            "shape": base_disc,
            "color_key": "alu_dark",
            "material": "7075-T6",
            "is_shell": False,
        }
    )
    palm_link_shapes.append(base_disc)

    # 2. Internal 2-Layer, 3-Deck Worm-Locked Spool Drive Stack (`z = zb + 1 .. zb + 33 mm`)
    for dname, z_d in (("floor_deck", zb + 1.0), ("mid_deck", zb + 16.5), ("top_deck", zb + 31.0)):
        deck = cq.Workplane("XY").workplane(offset=z_d).rect(38.0, 68.0).extrude(2.2).edges("|Z").fillet(4.0).val()
        deck = transform_shape(deck, make_T(np.eye(3), (5.0, 0.0, 0.0)))
        deck = deck.cut(make_cyl((0.0, 0.0, z_d - 0.2), (0.0, 0.0, 1.0), 5.5, 2.8))
        parts.append(
            {
                "name": f"r4_palm_{dname}_7075",
                "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                "shape": deck,
                "color_key": "alu_7075",
                "material": "7075-T6",
                "is_shell": False,
            }
        )

    for ci, (cx, cy) in enumerate([(-10.0, -29.0), (-10.0, 29.0), (20.0, -29.0), (20.0, 29.0)]):
        col = make_cyl((cx, cy, zb + 1.0), (0.0, 0.0, 1.0), 2.75, 32.2)
        parts.append(
            {
                "name": f"r4_deck_column_{ci}",
                "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                "shape": col,
                "color_key": "steel",
                "material": "steel_303",
                "is_shell": False,
            }
        )

    pcb = make_box(-8.0, 18.0, -24.0, 24.0, zb + 3.6, zb + 5.2)
    parts.append(
        {
            "name": "r4_main_reflex_controller_pcb_500hz",
            "group": "R4_Internal_2Layer_Worm_Spool_Drives",
            "shape": pcb,
            "color_key": "pcb_green",
            "material": "fr4_pcb",
            "is_shell": False,
        }
    )

    drive_specs = [
        ("thumb_base", zb + 6.0, -5.0, -s * 20.0, 1.0),
        ("index_base", zb + 6.0, 12.0, -s * 18.0, -1.0),
        ("ulnar_base", zb + 6.0, 12.0, s * 16.0, -1.0),
        ("thumb_distal", zb + 19.2, -5.0, -s * 8.0, 1.0),
        ("index_distal", zb + 19.2, 13.0, -s * 18.0, -1.0),
        ("ulnar_distal", zb + 19.2, 13.0, s * 16.0, -1.0),
    ]
    for dkey, z_layer, sx, sy, mdir in drive_specs:
        wheel = make_cyl((sx, sy, z_layer + 0.5), (0.0, 0.0, 1.0), 7.2, 4.2)
        drum = make_cyl((sx, sy, z_layer + 4.7), (0.0, 0.0, 1.0), 5.0, 5.2)
        worm = make_cyl((sx + mdir * 8.8, sy - 7.5, z_layer + 2.8), (0.0, 1.0, 0.0), 2.5, 13.0)
        motor = make_box(sx + mdir * 4.5, sx + mdir * 14.5, sy + 5.5, sy + 18.5, z_layer + 0.4, z_layer + 9.8)
        parts.extend(
            [
                {
                    "name": f"r4_drive_{dkey}_worm_wheel_cusn12",
                    "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                    "shape": wheel,
                    "color_key": "worm_bronze",
                    "material": "cusn12_bronze",
                    "is_shell": False,
                },
                {
                    "name": f"r4_drive_{dkey}_sk78_drum",
                    "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                    "shape": drum,
                    "color_key": "tendon_sk78",
                    "material": "7075-T6",
                    "is_shell": False,
                },
                {
                    "name": f"r4_drive_{dkey}_worm_shaft",
                    "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                    "shape": worm,
                    "color_key": "steel",
                    "material": "steel_303",
                    "is_shell": False,
                },
                {
                    "name": f"r4_drive_{dkey}_pololu_hpcb_motor",
                    "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                    "shape": motor,
                    "color_key": "motor_body",
                    "material": "pololu_hpcb_motor",
                    "is_shell": False,
                },
            ]
        )

    # 7th motorized self-locking worm drive for Thumb 3-position (-20/0/+20 deg) turret
    xt, yt, zt = lay["x_th_turret"], lay["y_th_turret"], lay["z_th_turret"]
    opp_gear = make_cyl((xt, yt, zt - 4.5), (0.0, 0.0, 1.0), 11.5, 4.2)
    opp_worm = make_cyl((xt + 12.2, yt - 7.0, zt - 2.4), (0.0, 1.0, 0.0), 2.5, 14.0)
    opp_motor = make_box(xt + 7.0, xt + 19.0, yt + 6.5, yt + 20.5, zt - 6.8, zt + 2.2)
    parts.extend(
        [
            {
                "name": "r4_thumb_opp_gear_ring_cusn12",
                "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                "shape": opp_gear,
                "color_key": "worm_bronze",
                "material": "cusn12_bronze",
                "is_shell": False,
            },
            {
                "name": "r4_thumb_opp_worm_3pos_lock",
                "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                "shape": opp_worm,
                "color_key": "steel",
                "material": "steel_303",
                "is_shell": False,
            },
            {
                "name": "r4_thumb_opp_pololu_hpcb_motor",
                "group": "R4_Internal_2Layer_Worm_Spool_Drives",
                "shape": opp_motor,
                "color_key": "motor_body",
                "material": "pololu_hpcb_motor",
                "is_shell": False,
            },
        ]
    )

    # Hollow contoured lower drive belly shell (`is_shell = True` so cutaway view hides it cleanly!)
    belly_outer = (
        cq.Workplane("XY")
        .workplane(offset=zb)
        .rect(52.0, 78.2)
        .extrude(34.5)
        .edges("|Z")
        .fillet(6.5)
        .val()
    )
    belly_outer = transform_shape(belly_outer, make_T(np.eye(3), (2.0, 0.0, 0.0)))
    belly_inner = (
        cq.Workplane("XY")
        .workplane(offset=zb + 2.2)
        .rect(45.0, 72.5)
        .extrude(30.2)
        .edges("|Z")
        .fillet(4.5)
        .val()
    )
    belly_inner = transform_shape(belly_inner, make_T(np.eye(3), (3.0, 0.0, 0.0)))
    belly_shell = belly_outer.cut(belly_inner)
    parts.append(
        {
            "name": "r4_lower_palm_drive_belly_shell",
            "group": "R4_Glove_Matched_Palm_Chassis",
            "shape": belly_shell,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
            "is_shell": True,
        }
    )
    palm_link_shapes.append(belly_shell)

    # 3. Glove-Matched Winged Side Cheek Plates (`radial` & `ulnar`)
    cheek_rad = _make_winged_cheek_plate(zb, -s * 44.5, -s * 39.0)
    cheek_uln = _make_winged_cheek_plate(zb, s * 39.0, s * 44.5)
    parts.extend(
        [
            {
                "name": "r4_radial_winged_cheek_plate",
                "group": "R4_Glove_Matched_Palm_Chassis",
                "shape": cheek_rad,
                "color_key": "palm_shell_offwhite",
                "material": "nylon_12_sls",
                "is_shell": False,
            },
            {
                "name": "r4_ulnar_winged_cheek_plate",
                "group": "R4_Glove_Matched_Palm_Chassis",
                "shape": cheek_uln,
                "color_key": "palm_shell_offwhite",
                "material": "nylon_12_sls",
                "is_shell": False,
            },
        ]
    )
    palm_link_shapes.extend([cheek_rad, cheek_uln])

    # Forward Palmar Bridge + Signature Sloped Front Apron Plate (matching Slide 11 foreground apron!)
    palmar_bridge = make_box(-38.0, 6.0, -39.2, 39.2, zb + 28.5, zb + 34.5)
    # Forward-sloping ulnar/central apron lip extending to x = -54 mm (stepped clear of radial thumb!)
    apron_pts_xz = [
        (-36.5, zb + 34.5),
        (-54.0, zb + 27.5),
        (-52.0, zb + 23.0),
        (-35.0, zb + 28.5),
    ]
    apron_y0, apron_y1 = (-6.0, 39.2) if s > 0 else (-39.2, 6.0)
    apron_lip = (
        cq.Workplane("XZ")
        .workplane(offset=-apron_y1)
        .polyline(apron_pts_xz)
        .close()
        .extrude(apron_y1 - apron_y0)
        .val()
    )
    palmar_apron = palmar_bridge.fuse(apron_lip)
    parts.append(
        {
            "name": "r4_forward_palmar_apron_shelf",
            "group": "R4_Glove_Matched_Palm_Chassis",
            "shape": palmar_apron,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
            "is_shell": False,
        }
    )
    palm_link_shapes.append(palmar_apron)

    # Chamfered Upper MCP Palmar Block (beneath the MCP knuckle shaft, exactly as in Slide 11!)
    mcp_pad_pts_xz = [
        (7.5, zb + 48.0),
        (13.0, zb + 57.5),
        (27.5, zb + 57.5),
        (29.0, zb + 44.0),
        (12.5, zb + 41.5),
    ]
    mcp_palm_block = (
        cq.Workplane("XZ")
        .workplane(offset=-39.0)
        .polyline(mcp_pad_pts_xz)
        .close()
        .extrude(78.0)
        .val()
    )
    parts.append(
        {
            "name": "r4_chamfered_mcp_palmar_block",
            "group": "R4_Glove_Matched_Palm_Chassis",
            "shape": mcp_palm_block,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
            "is_shell": False,
        }
    )
    palm_link_shapes.append(mcp_palm_block)

    # Shared Horizontal White Cylindrical MCP Knuckle Shaft + 2x Outer 6-Notched Rotary Hubs
    mcp_shaft = make_cyl((x_mcp, -44.5, z_mcp), (0.0, 1.0, 0.0), 6.4, 89.0)
    hub_mcp_uln = _make_notched_rotary_hub((x_mcp, s * 44.5, z_mcp), s, r_outer=7.8, thick=4.2)
    hub_mcp_rad = _make_notched_rotary_hub((x_mcp, -s * 44.5, z_mcp), -s, r_outer=7.8, thick=4.2)
    parts.extend(
        [
            {
                "name": "r4_mcp_knuckle_shaft_sleeve",
                "group": "R4_Glove_Matched_Palm_Chassis",
                "shape": mcp_shaft,
                "color_key": "palm_shell_offwhite",
                "material": "nylon_12_sls",
                "is_shell": False,
            },
            {
                "name": "r4_ulnar_mcp_rotary_encoder_hub",
                "group": "R4_Wrist_Vision_And_Tactile",
                "shape": hub_mcp_uln,
                "color_key": "rotary_hub_dark",
                "material": "nylon_12_sls",
                "is_shell": False,
            },
            {
                "name": "r4_radial_mcp_rotary_encoder_hub",
                "group": "R4_Wrist_Vision_And_Tactile",
                "shape": hub_mcp_rad,
                "color_key": "rotary_hub_dark",
                "material": "nylon_12_sls",
                "is_shell": False,
            },
        ]
    )
    palm_link_shapes.extend([mcp_shaft, hub_mcp_uln, hub_mcp_rad])

    # Side cheek pods (radial ArduCam + ulnar electronics pod)
    pods = _build_cheek_pods(zb, s)
    parts.extend(pods)
    palm_link_shapes.extend([p["shape"] for p in pods])

    # 4. Contoured Twin-Eminence `Shore A 60` PU Palmar Stability Saddle + 4x Ohmite FSR07 Zones
    #    Inset on top of `palmar_bridge` (`z = zb + 34.5 .. zb + 36.5 mm`, with lateral twin-eminence ribs!)
    saddle_base = (
        cq.Workplane("XY")
        .workplane(offset=zb + 34.2)
        .rect(36.0, 68.0)
        .extrude(2.3)
        .edges("|Z")
        .fillet(5.0)
        .val()
    )
    saddle_base = transform_shape(saddle_base, make_T(np.eye(3), (-16.0, 0.0, 0.0)))
    # Twin lateral stability eminences (+13% axial bottle hold, `PALM_STABILITY.md`)
    rib_front = (
        cq.Workplane("XY")
        .workplane(offset=zb + 36.2)
        .rect(6.5, 64.0)
        .extrude(1.8)
        .edges("|Z")
        .fillet(2.5)
        .val()
    )
    rib_back = transform_shape(rib_front, make_T(np.eye(3), (-3.0, 0.0, 0.0)))
    rib_front = transform_shape(rib_front, make_T(np.eye(3), (-29.0, 0.0, 0.0)))
    pu_saddle = saddle_base.fuse(rib_front).fuse(rib_back)
    parts.append(
        {
            "name": "r4_palm_trough_pu_a60_skin",
            "group": "R4_Palm_Stability_Trough_Pad",
            "shape": pu_saddle,
            "color_key": "palm_skin_pu",
            "material": "pu_shore_a60",
            "is_shell": False,
        }
    )
    palm_link_shapes.append(pu_saddle)

    for fi, (fx, fy) in enumerate([(-24.0, -18.0), (-24.0, 18.0), (-8.0, -18.0), (-8.0, 18.0)]):
        fsr = make_cyl((fx, fy, zb + 33.6), (0.0, 0.0, 1.0), 6.5, 0.6)
        parts.append(
            {
                "name": f"r4_palm_fsr07_quadrant_{fi}",
                "group": "R4_Wrist_Vision_And_Tactile",
                "shape": fsr,
                "color_key": "fsr_subpad",
                "material": "polyimide_ffc",
                "is_shell": False,
            }
        )

    return parts, palm_link_shapes


def _build_overhanging_glove_digit_local(
    dkey: str,
    L1: float,
    L2: float,
    width_y: float,
    e_dist: float,
    n_domes: int,
    s_outer_hub: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build an upper finger (`index` or `ulnar`) in local joint frames matching `media_1795214756074_c806eaec.jpg`:
    - Proximal link frame: origin at MCP `(0,0,0)`, `+Z` along link toward PIP `(0, 0, L1)`, `-X` palmar.
      Uses **Twin-Strut Open Ladder Frame** (cylindrical MCP sleeve + 2 parallel side struts + PIP cross-bar)!
    - Distal link frame: origin at PIP `(0,0,0)`, `+Z` along link toward tip `(0, 0, L2)`, `-X` palmar
      (so when `mcp + pip = 90 deg`, `+Z_local` points forward `-X_world` and `-X_local` points DOWN `-Z_world`!).
      Uses narrower distal neck arm(s) stepping into the **Bold Chiseled / Chamfered White Distal Block**
      with removable `M1.6` dorsal crown, `Shore A 60` PU grip sole at `x_local = -e_dist`, and flush
      Contactile `3DFBS-12T-50N` tactile dome(s)!
    """
    hw = 0.5 * width_y
    prox_parts: list[dict[str, Any]] = []
    dist_parts: list[dict[str, Any]] = []

    # --- PROXIMAL TWIN-STRUT OPEN LADDER / CLEVIS FRAME ---
    barrel_w = width_y - 4.0
    mcp_barrel = make_cyl((0.0, -0.5 * barrel_w, 0.0), (0.0, 1.0, 0.0), 7.8, barrel_w)
    strut_t = 7.2 if width_y > 35.0 else 5.4
    y_in = hw - 2.0 - strut_t
    y_out = hw - 2.0
    strut_l = make_box(-4.8, 5.2, -y_out, -y_in, 4.0, L1 - 2.0)
    strut_r = make_box(-4.8, 5.2, y_in, y_out, 4.0, L1 - 2.0)
    pip_cross = make_box(-5.5, 5.5, -y_out, y_out, L1 - 11.5, L1 - 1.5)
    pip_cheek_l = make_cyl((0.0, -y_out, L1), (0.0, 1.0, 0.0), 6.8, strut_t)
    pip_cheek_r = make_cyl((0.0, y_in, L1), (0.0, 1.0, 0.0), 6.8, strut_t)

    prox_frame = mcp_barrel.fuse(strut_l).fuse(strut_r).fuse(pip_cross).fuse(pip_cheek_l).fuse(pip_cheek_r)
    prox_parts.append(
        {
            "sub": "prox_twin_strut_frame",
            "shape": prox_frame,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
        }
    )

    # Protected SK78 tendon guide + dorsal 8-way FFC strip along outer strut
    ffc_y = s_outer_hub * (y_out + 0.35)
    ffc_prox = make_box(1.5, 4.5, min(ffc_y, ffc_y - s_outer_hub * 0.7), max(ffc_y, ffc_y - s_outer_hub * 0.7), 6.0, L1 - 2.0)
    prox_parts.append(
        {
            "sub": "prox_ffc_guard",
            "shape": ffc_prox,
            "color_key": "ffc_guard",
            "material": "polyimide_ffc",
        }
    )
    # Inner PU proximal hook/wrap cushion on cross-bar (`hook_bar_max` & large cylinder support)
    prox_pu = make_box(-7.2, -5.2, -(y_out - 2.0), y_out - 2.0, L1 - 16.0, L1 - 3.0)
    prox_parts.append(
        {
            "sub": "prox_pu_skin",
            "shape": prox_pu,
            "color_key": "finger_skin_pu",
            "material": "pu_shore_a60",
        }
    )

    # Exposed 6-notched dark-grey PIP rotary sensor hub on outer side!
    pip_hub = _make_notched_rotary_hub((0.0, s_outer_hub * (y_out + 0.2), L1), s_outer_hub, r_outer=7.2, thick=3.8)
    prox_parts.append(
        {
            "sub": "pip_rotary_encoder_hub",
            "shape": pip_hub,
            "color_key": "rotary_hub_dark",
            "material": "nylon_12_sls",
        }
    )

    # --- DISTAL NECK ARM + CHISELED / CHAMFERED WHITE BLOCK HEAD ---
    z_block_start = 16.0
    if width_y > 35.0:
        # Wide Coupled Paddle: twin distal neck arms connecting PIP inside cheeks to the 52 mm paddle
        arm_l = make_box(-4.2, 4.8, -(y_in - 0.5), -(y_in - 7.0), -2.0, z_block_start + 3.0)
        arm_r = make_box(-4.2, 4.8, y_in - 7.0, y_in - 0.5, -2.0, z_block_start + 3.0)
        hub_l = make_cyl((0.0, -(y_in - 0.5), 0.0), (0.0, 1.0, 0.0), 5.6, 6.5)
        hub_r = make_cyl((0.0, y_in - 7.0, 0.0), (0.0, 1.0, 0.0), 5.6, 6.5)
        dist_neck = arm_l.fuse(arm_r).fuse(hub_l).fuse(hub_r)
    else:
        # Single Index: clean central clevis neck arm between the twin proximal cheeks!
        nw = min(11.0, 2.0 * y_in - 1.6)
        dist_neck = make_box(-4.2, 4.8, -0.5 * nw, 0.5 * nw, -2.0, z_block_start + 3.0).fuse(
            make_cyl((0.0, -0.5 * nw, 0.0), (0.0, 1.0, 0.0), 5.6, nw)
        )

    # Exact chiseled polygonal profile of the Slide 11 fingertip block in `(X_local, Z_local)`:
    #   +X_local = dorsal top (`+7.5 mm` crown split at `+4.5 mm`)
    #   -X_local = palmar pinching sole (`-e_dist + 2.0 mm` core, `-e_dist` PU sole!)
    #   Front tip nose at `z = L2` beveled so at `theta12 = 90 deg` it stands `0.500 mm` clear of table!
    x_dorsal_split = 4.6
    x_core_sole = -(e_dist - 2.0)  # -11.0 mm
    block_pts_xz = [
        (x_dorsal_split, z_block_start),
        (x_dorsal_split, L2 - 2.5),
        (-1.5, L2),                       # Upper front nose facet
        (x_core_sole, L2 - 1.4),          # Flat table-pinch lower front bevel (`GR3-PEN-01`)!
        (x_core_sole, z_block_start + 5.5),
        (-4.2, z_block_start),            # Rear lower chamfer matching `media_1795214756074_c806eaec.jpg`!
    ]
    chiseled_core = (
        cq.Workplane("XZ")
        .workplane(offset=-hw)
        .polyline(block_pts_xz)
        .close()
        .extrude(width_y)
        .val()
    )
    dist_body = dist_neck.fuse(chiseled_core)
    dist_parts.append(
        {
            "sub": "dist_7075",
            "shape": dist_body,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
        }
    )

    # Removable M1.6 dorsal service crown (`x = +4.6 .. +8.2 mm`) giving flat blocky dorsal top!
    crown = make_box(x_dorsal_split, 8.2, -hw, hw, z_block_start, L2 - 2.5)
    dist_parts.append(
        {
            "sub": "dist_service_crown_m16",
            "shape": crown,
            "color_key": "tip_crown",
            "material": "nylon_12_sls",
        }
    )

    # Flush `Shore A 60` PU palmar pinching sole (`x_local = -e_dist .. -(e_dist - 2.0)`)
    pu_sole = make_box(-e_dist, x_core_sole, -(hw - 0.8), hw - 0.8, z_block_start + 5.8, L2 - 1.5)
    dist_parts.append(
        {
            "sub": "dist_pu_skin",
            "shape": pu_sole,
            "color_key": "finger_skin_pu",
            "material": "pu_shore_a60",
        }
    )

    # Flush Contactile `3DFBS-12T-50N` tactile domes (`1` on Index, `3` across Wide Coupled Paddle)
    dome_ys = [0.0] if n_domes == 1 else [-16.5, 0.0, 16.5]
    z_dome = 0.5 * (z_block_start + 6.0 + L2 - 5.0)
    for di, dy in enumerate(dome_ys):
        dome = make_cyl((-e_dist - 0.35, dy, z_dome), (1.0, 0.0, 0.0), 4.8, 2.4)
        dist_parts.append(
            {
                "sub": f"contactile_3dfbs_dome_{di}",
                "shape": dome,
                "color_key": "contactile_dome",
                "material": "contactile_3dfbs",
            }
        )

    return prox_parts, dist_parts


def _build_glove_thumb_local(
    L1_th: float,
    L2_th: float,
    width_y: float,
    e_th: float,
    s: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the low radial-palmar 3-position (`-20°/0°/+20°`) opposing Thumb matching
    `media_1795214756074_c806eaec.jpg` (bottom-left) & `glove_cad_edges_zoom.png`:
      - Turret frame: rotates around `+Z` at `(x_th_turret, y_th_turret, z_th_turret)`.
      - Proximal frame: `+X_local` = forward (`-X_world` at 0° opp), `+Z_local` = up (`+Z_world`).
        WAIT: to keep standard `rot_y` joint math simple, we define in thumb local link frames:
        `+Z_local` = along thumb link, and `+X_local` = UPWARD pinch normal toward the overhanging fingers!
    """
    hw = 0.5 * width_y
    turret_parts: list[dict[str, Any]] = []
    prox_parts: list[dict[str, Any]] = []
    dist_parts: list[dict[str, Any]] = []

    # 1. Rotary opposition drum + 6-notched encoder cap on radial side
    drum = make_cyl((0.0, 0.0, -3.5), (0.0, 0.0, 1.0), 10.5, 9.5)
    clevis = make_box(-11.0, 2.0, -9.5, 9.5, -1.0, 7.5)
    rot_cap = _make_notched_rotary_hub((-6.0, -s * 10.0, 3.0), -s, r_outer=6.8, thick=3.6)
    turret_parts.extend(
        [
            {
                "sub": "opp_turntable_7075",
                "shape": drum.fuse(clevis),
                "color_key": "palm_shell_offwhite",
                "material": "nylon_12_sls",
            },
            {
                "sub": "opp_rotary_encoder_hub",
                "shape": rot_cap,
                "color_key": "rotary_hub_dark",
                "material": "nylon_12_sls",
            },
        ]
    )

    # 2. Stout proximal thumb strut (`+Z_local` along link `0 .. L1_th`, `+X_local` pinch normal)
    p_strut = make_box(-4.8, 4.8, -7.5, 7.5, 0.0, L1_th).fuse(
        make_cyl((0.0, -8.5, 0.0), (0.0, 1.0, 0.0), 5.2, 17.0)
    ).fuse(make_cyl((0.0, -8.5, L1_th), (0.0, 1.0, 0.0), 5.2, 17.0))
    prox_parts.append(
        {
            "sub": "prox_7075",
            "shape": p_strut,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
        }
    )

    # 3. ENLARGED PENTAGONAL CHISELED WHITE THUMB BLOCK (`L2_th = 34 mm`, `width_y = 28 mm`)
    #    `+X_local` (`+e_th`) = upward pinch face aimed at Index & Ulnar blocks!
    #    `-X_local` (`-11.5 mm`) = faceted outer/lower thumb heel!
    x_pinch_core = e_th - 2.0  # +9.5 mm (with +9.5..+11.5 mm PU pinch sole!)
    th_pts_xz = [
        (-5.5, 0.0),
        (5.5, 2.0),
        (x_pinch_core, 8.5),
        (x_pinch_core, L2_th - 1.4),
        (1.5, L2_th),                  # Pentagonal apex nose (`media_1795214756074_c806eaec.jpg` bottom-left!)
        (-10.5, L2_th - 7.5),          # Crisp lower-front pentagonal facet
        (-11.5, 6.0),                  # Lower heel facet
    ]
    th_block = (
        cq.Workplane("XZ")
        .workplane(offset=-hw)
        .polyline(th_pts_xz)
        .close()
        .extrude(width_y)
        .val()
    )
    # Signature raised rectangular alignment tab on top-radial ridge (visible in `media_1795214756074_c806eaec.jpg`!)
    tab_y0 = -s * (hw - 1.5)
    tab_y1 = -s * (hw - 7.5)
    ridge_tab = make_box(x_pinch_core - 3.0, e_th + 2.8, min(tab_y0, tab_y1), max(tab_y0, tab_y1), 10.5, 23.5)
    dist_parts.append(
        {
            "sub": "dist_7075",
            "shape": th_block,
            "color_key": "palm_shell_offwhite",
            "material": "nylon_12_sls",
        }
    )
    dist_parts.append(
        {
            "sub": "dist_service_crown_m16",
            "shape": ridge_tab,
            "color_key": "tip_crown",
            "material": "nylon_12_sls",
        }
    )

    # Flush `Shore A 60` PU pinch sole (inset cleanly beside the radial ridge tab!)
    pu_y0 = -hw + 7.8 if s > 0 else -hw + 1.0
    pu_y1 = hw - 1.0 if s > 0 else hw - 7.8
    th_pu = make_box(x_pinch_core, e_th, pu_y0, pu_y1, 9.0, L2_th - 1.5)
    dist_parts.append(
        {
            "sub": "dist_pu_skin",
            "shape": th_pu,
            "color_key": "finger_skin_pu",
            "material": "pu_shore_a60",
        }
    )

    # Flush Contactile `3DFBS-12T-50N` thumb tactile dome
    th_dome = make_cyl((e_th - 2.0, 0.5 * (pu_y0 + pu_y1), 20.5), (1.0, 0.0, 0.0), 4.8, 2.35)
    dist_parts.append(
        {
            "sub": "contactile_3dfbs_dome_0",
            "shape": th_dome,
            "color_key": "contactile_dome",
            "material": "contactile_3dfbs",
        }
    )

    return turret_parts, prox_parts, dist_parts


# ---------------------------------------------------------------------------
# Complete Pose Assembly & Kinematic Tree Builder
# ---------------------------------------------------------------------------

def build_r4_complete_pose(
    lay: dict[str, Any],
    pose_spec: dict[str, Any],
    screw_angles_deg: list[float],
    include_payload: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    s = lay["s"]
    parts, palm_shapes = _build_palm_chassis_and_2layer_drives(lay, screw_angles_deg)

    links: dict[str, dict[str, Any]] = {
        "r4_palm_link": {
            "local_shape": cq.Compound.makeCompound(palm_shapes),
            "color_key": "palm_shell_offwhite",
            "density": 1450.0,
        }
    }

    # --- 1. Overhanging Upper Digits (`index` & `ulnar`) on Shared MCP Shaft ---
    for dkey, y_c, w_y, L1, L2, n_domes, s_hub in (
        ("index", lay["index_y"], lay["index_w"], lay["L1_index"], lay["L2_index"], 1, -s),
        ("ulnar", lay["ulnar_y"], lay["ulnar_w"], lay["L1_ulnar"], lay["L2_ulnar"], 3, s),
    ):
        mcp_deg, pip_deg = pose_spec[dkey]
        prox_local, dist_local = _build_overhanging_glove_digit_local(
            dkey, L1, L2, w_y, lay["e"], n_domes, s_hub
        )
        # Positive `rot_y(-deg)` rotates `+Z` toward `-X` (`rot_y(-90) @ [0,0,1] = [-1, 0, 0]`)!
        T_mcp_0 = make_T(np.eye(3), (lay["x_mcp"], y_c, lay["z_mcp"]))
        T_prox = T_mcp_0 @ make_T(rot_y(-mcp_deg), (0.0, 0.0, 0.0))
        T_pip_rel = make_T(np.eye(3), (0.0, 0.0, L1))
        T_dist = T_prox @ T_pip_rel @ make_T(rot_y(-pip_deg), (0.0, 0.0, 0.0))

        gname = f"R4_Digit_{dkey.capitalize()}"
        for rec in prox_local:
            parts.append(
                {
                    "name": f"r4_{dkey}_{rec['sub']}",
                    "group": gname,
                    "shape": transform_shape(rec["shape"], T_prox),
                    "color_key": rec["color_key"],
                    "material": rec["material"],
                    "is_shell": False,
                }
            )
        for rec in dist_local:
            parts.append(
                {
                    "name": f"r4_{dkey}_{rec['sub']}",
                    "group": gname,
                    "shape": transform_shape(rec["shape"], T_dist),
                    "color_key": rec["color_key"],
                    "material": rec["material"],
                    "is_shell": False,
                }
            )

        links[f"r4_{dkey}_prox_link"] = {
            "local_shape": cq.Compound.makeCompound([r["shape"] for r in prox_local]),
            "color_key": "palm_shell_offwhite",
            "density": 1250.0,
            "parent": "r4_palm_link",
            "joint_name": f"r4_{dkey}_mcp_joint",
            "origin_xyz_mm": T_mcp_0[:3, 3].tolist(),
            "origin_rpy": mat_to_rpy(T_mcp_0[:3, :3]),
            "axis": [0.0, -1.0, 0.0],
            "limits_rad": [-10.0 * DEG, 75.0 * DEG],
        }
        links[f"r4_{dkey}_dist_link"] = {
            "local_shape": cq.Compound.makeCompound([r["shape"] for r in dist_local]),
            "color_key": "palm_shell_offwhite",
            "density": 1250.0,
            "parent": f"r4_{dkey}_prox_link",
            "joint_name": f"r4_{dkey}_pip_joint",
            "origin_xyz_mm": [0.0, 0.0, L1],
            "origin_rpy": (0.0, 0.0, 0.0),
            "axis": [0.0, -1.0, 0.0],
            "limits_rad": [0.0, 105.0 * DEG],
        }

    # --- 2. Low Radial-Palmar 3-Position (`-20°/0°/+20°`) Opposing Thumb ---
    opp_deg = float(pose_spec["thumb_opp_deg"])
    th_mcp_deg, th_ip_deg = pose_spec["thumb"]
    tur_local, th_prox_local, th_dist_local = _build_glove_thumb_local(
        lay["L1_thumb"], lay["L2_thumb"], lay["thumb_w"], lay["e_thumb"], s
    )

    # At `opp_deg = 0`, turret sits at `(x_th_turret, y_th_turret, z_th_turret)`;
    # `rot_z(-s * opp_deg)` swings `+20 deg` inward across midline (`y -> 0`) and `-20 deg` radially!
    T_tur_0 = make_T(np.eye(3), (lay["x_th_turret"], lay["y_th_turret"], lay["z_th_turret"]))
    T_tur = T_tur_0 @ make_T(rot_z(-s * opp_deg), (0.0, 0.0, 0.0))

    # Base of thumb proximal pivot at `(-8.0, 0.0, +4.0)` in turret frame:
    # At `th_mcp_deg = 0`, `+Z_local` points forward `(-1, 0, 0)` and `+X_local` points UP `(0, 0, +1)`
    # (`rot_y(-90)` maps `+Z -> -X` and `+X -> +Z`!). Increasing `th_mcp_deg > 0` tilts thumb UPWARD
    # (`rot_y(-(90 - th_mcp_deg))`)!
    T_th_mcp_rel = make_T(rot_y(-90.0), (-8.0, 0.0, 4.0))
    T_th_prox = T_tur @ T_th_mcp_rel @ make_T(rot_y(th_mcp_deg), (0.0, 0.0, 0.0))
    T_th_ip_rel = make_T(np.eye(3), (0.0, 0.0, lay["L1_thumb"]))
    T_th_dist = T_th_prox @ T_th_ip_rel @ make_T(rot_y(th_ip_deg), (0.0, 0.0, 0.0))

    for rec in tur_local:
        parts.append(
            {
                "name": f"r4_thumb_{rec['sub']}",
                "group": "R4_Digit_Thumb_3Pos_Turntable",
                "shape": transform_shape(rec["shape"], T_tur),
                "color_key": rec["color_key"],
                "material": rec["material"],
                "is_shell": False,
            }
        )
    for rec in th_prox_local:
        parts.append(
            {
                "name": f"r4_thumb_{rec['sub']}",
                "group": "R4_Digit_Thumb_3Pos_Turntable",
                "shape": transform_shape(rec["shape"], T_th_prox),
                "color_key": rec["color_key"],
                "material": rec["material"],
                "is_shell": False,
            }
        )
    for rec in th_dist_local:
        parts.append(
            {
                "name": f"r4_thumb_{rec['sub']}",
                "group": "R4_Digit_Thumb_3Pos_Turntable",
                "shape": transform_shape(rec["shape"], T_th_dist),
                "color_key": rec["color_key"],
                "material": rec["material"],
                "is_shell": False,
            }
        )

    links["r4_thumb_turntable_link"] = {
        "local_shape": cq.Compound.makeCompound([r["shape"] for r in tur_local]),
        "color_key": "palm_shell_offwhite",
        "density": 1350.0,
        "parent": "r4_palm_link",
        "joint_name": "r4_thumb_opposition_joint",
        "origin_xyz_mm": T_tur_0[:3, 3].tolist(),
        "origin_rpy": (0.0, 0.0, 0.0),
        "axis": [0.0, 0.0, -float(s)],
        "limits_rad": [-20.0 * DEG, 20.0 * DEG],
    }
    links["r4_thumb_prox_link"] = {
        "local_shape": cq.Compound.makeCompound([r["shape"] for r in th_prox_local]),
        "color_key": "palm_shell_offwhite",
        "density": 1250.0,
        "parent": "r4_thumb_turntable_link",
        "joint_name": "r4_thumb_mcp_joint",
        "origin_xyz_mm": T_th_mcp_rel[:3, 3].tolist(),
        "origin_rpy": mat_to_rpy(T_th_mcp_rel[:3, :3]),
        "axis": [0.0, 1.0, 0.0],
        "limits_rad": [-10.0 * DEG, 70.0 * DEG],
    }
    links["r4_thumb_dist_link"] = {
        "local_shape": cq.Compound.makeCompound([r["shape"] for r in th_dist_local]),
        "color_key": "palm_shell_offwhite",
        "density": 1250.0,
        "parent": "r4_thumb_prox_link",
        "joint_name": "r4_thumb_pip_joint",
        "origin_xyz_mm": [0.0, 0.0, lay["L1_thumb"]],
        "origin_rpy": (0.0, 0.0, 0.0),
        "axis": [0.0, 1.0, 0.0],
        "limits_rad": [0.0, 75.0 * DEG],
    }

    if include_payload and "payload" in pose_spec:
        pl = pose_spec["payload"]
        if pl["kind"] == "bottle_1l":
            cyl = make_cyl((pl["x"], -0.5 * pl["len"], pl["z"]), (0.0, 1.0, 0.0), pl["r"], pl["len"])
            parts.append(
                {
                    "name": "r4_benchmark_1l_bottle",
                    "group": "R4_Qualification_Payload",
                    "shape": cyl,
                    "color_key": "test_object",
                    "material": "nylon_12_sls",
                    "is_shell": False,
                }
            )
        elif pl["kind"] == "pen_on_table":
            pen = make_cyl((pl["x"], -55.0, pl["z"]), (0.0, 1.0, 0.0), pl["r"], 110.0)
            parts.append(
                {
                    "name": "r4_benchmark_8mm_pen",
                    "group": "R4_Qualification_Payload",
                    "shape": pen,
                    "color_key": "test_object",
                    "material": "nylon_12_sls",
                    "is_shell": False,
                }
            )

    return parts, links


# ---------------------------------------------------------------------------
# Exact OpenCASCADE Mass Properties & 6-Benchmark Qualification Audit
# ---------------------------------------------------------------------------

def compute_shape_mass_props(shape: cq.Shape, density_kg_m3: float) -> dict[str, Any]:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    vol_mm3 = float(props.Mass())
    mass_kg = max(vol_mm3 * 1e-9, 1e-12) * density_kg_m3
    com = props.CentreOfMass()
    mat = props.MatrixOfInertia()
    scale = density_kg_m3 * 1e-15
    return {
        "volume_mm3": round(vol_mm3, 2),
        "mass_kg": round(mass_kg, 5),
        "com_mm": [round(com.X(), 3), round(com.Y(), 3), round(com.Z(), 3)],
        "inertia_kg_m2": {
            "ixx": max(float(mat.Value(1, 1)) * scale, 1e-8),
            "iyy": max(float(mat.Value(2, 2)) * scale, 1e-8),
            "izz": max(float(mat.Value(3, 3)) * scale, 1e-8),
            "ixy": float(mat.Value(1, 2)) * scale,
            "ixz": float(mat.Value(1, 3)) * scale,
            "iyz": float(mat.Value(2, 3)) * scale,
        },
    }


def qualify_all_r4_benchmarks(lay: dict[str, Any], screw_angles_deg: list[float]) -> dict[str, Any]:
    poses = benchmark_poses(lay)
    results: dict[str, Any] = {}
    check_pairs = [
        ("r4_thumb_dist_7075", "r4_index_dist_7075"),
        ("r4_thumb_dist_7075", "r4_ulnar_dist_7075"),
        ("r4_index_dist_7075", "r4_ulnar_dist_7075"),
        ("r4_index_prox_twin_strut_frame", "r4_ulnar_prox_twin_strut_frame"),
        ("r4_thumb_dist_7075", "r4_forward_palmar_apron_shelf"),
        ("r4_index_dist_7075", "r4_chamfered_mcp_palmar_block"),
        ("r4_ulnar_dist_7075", "r4_chamfered_mcp_palmar_block"),
    ]

    for pname, pspec in poses.items():
        parts, _ = build_r4_complete_pose(lay, pspec, screw_angles_deg, include_payload=False)
        by_name = {p["name"]: p["shape"] for p in parts}

        interferences = []
        clearances: dict[str, float] = {}
        for a, b in check_pairs:
            d = min_distance_mm(by_name[a], by_name[b])
            clearances[f"{a}_vs_{b}"] = round(d, 3)
            if d < 0.05:
                interferences.append({"a": a, "b": b, "distance_mm": round(d, 4)})

        extra: dict[str, Any] = {}
        ok_extra = True
        if pname == "wrap_bottle_1l":
            pl = pspec["payload"]
            bottle = make_cyl((pl["x"], -60.0, pl["z"]), (0.0, 1.0, 0.0), pl["r"], 120.0)
            g_palm = min_distance_mm(by_name["r4_palm_trough_pu_a60_skin"], bottle)
            g_th = min_distance_mm(by_name["r4_thumb_dist_pu_skin"], bottle)
            g_ix = min_distance_mm(by_name["r4_index_dist_pu_skin"], bottle)
            g_ul = min_distance_mm(by_name["r4_ulnar_dist_pu_skin"], bottle)
            extra = {
                "bottle_to_contoured_palm_skin_mm": round(g_palm, 3),
                "bottle_to_thumb_skin_mm": round(g_th, 3),
                "bottle_to_index_skin_mm": round(g_ix, 3),
                "bottle_to_ulnar_skin_mm": round(g_ul, 3),
            }
            ok_extra = max(g_palm, g_th, g_ix, g_ul) <= 1.2
        elif pname == "pinch_pen":
            pl = pspec["payload"]
            pen = make_cyl((pl["x"], -60.0, pl["z"]), (0.0, 1.0, 0.0), pl["r"], 120.0)
            table_slab = make_box(pl["table_x"] - 25.0, pl["table_x"], -80.0, 80.0, lay["zb"], lay["zb"] + 160.0)
            g_th_pen = min_distance_mm(by_name["r4_thumb_dist_pu_skin"], pen)
            g_ix_pen = min_distance_mm(by_name["r4_index_dist_pu_skin"], pen)
            g_ul_pen = min_distance_mm(by_name["r4_ulnar_dist_pu_skin"], pen)
            g_table = min(
                min_distance_mm(by_name["r4_thumb_dist_7075"], table_slab),
                min_distance_mm(by_name["r4_index_dist_7075"], table_slab),
                min_distance_mm(by_name["r4_ulnar_dist_7075"], table_slab),
            )
            extra = {
                "thumb_to_pen_mm": round(g_th_pen, 3),
                "index_to_pen_mm": round(g_ix_pen, 3),
                "ulnar_to_pen_mm": round(g_ul_pen, 3),
                "fingertips_to_table_gap_mm": round(g_table, 3),
            }
            ok_extra = (max(g_th_pen, g_ix_pen, g_ul_pen) <= 1.0) and (g_table >= 0.35)
        elif pname == "hook_bar_max":
            pl = pspec["payload"]
            bar = make_cyl((pl["x"], -70.0, pl["z"]), (0.0, 1.0, 0.0), pl["r"], 140.0)
            door_slab = make_box(pl["door_x"] - 30.0, pl["door_x"], -80.0, 80.0, lay["zb"], lay["zb"] + 170.0)
            g_ix_bar = min(
                min_distance_mm(by_name["r4_index_prox_pu_skin"], bar),
                min_distance_mm(by_name["r4_index_dist_pu_skin"], bar),
            )
            g_ul_bar = min(
                min_distance_mm(by_name["r4_ulnar_prox_pu_skin"], bar),
                min_distance_mm(by_name["r4_ulnar_dist_pu_skin"], bar),
            )
            g_door = min(
                min_distance_mm(by_name["r4_thumb_dist_7075"], door_slab),
                min_distance_mm(by_name["r4_index_dist_7075"], door_slab),
                min_distance_mm(by_name["r4_ulnar_dist_7075"], door_slab),
            )
            extra = {
                "index_to_ada_bar_mm": round(g_ix_bar, 3),
                "ulnar_to_ada_bar_mm": round(g_ul_bar, 3),
                "clearance_to_ada_door_wall_mm": round(g_door, 3),
            }
            ok_extra = (max(g_ix_bar, g_ul_bar) <= 1.2) and (g_door >= 10.0)

        results[pname] = {
            "self_interference_count": len(interferences),
            "interferences": interferences,
            "clearances_mm": clearances,
            "task_contacts_mm": extra,
            "pass": (len(interferences) == 0) and ok_extra,
        }
    return results


# ---------------------------------------------------------------------------
# Read-Only Loader for `cad_creator` Wrist Tool Changer R2 (Cached)
# ---------------------------------------------------------------------------

def load_wrist_tool_changer_r2_readonly() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cache_dir = OUT_ROOT / "cad" / "parts_wtc_shared"
    cache_json = cache_dir / "wtc_r2_cache_manifest.json"
    if cache_json.exists():
        cached = json.loads(cache_json.read_text(encoding="utf-8"))
        changer_parts = []
        for rec in cached["parts"]:
            shape = cq.importers.importStep(rec["step_path"]).val().wrapped
            changer_parts.append(
                {
                    "name": rec["name"],
                    "group": rec["group"],
                    "shape": cq.Shape.cast(shape),
                    "color_key": rec["color_key"] if rec["color_key"] in PART_COLORS_RGB else "wtc_master",
                    "material": rec["material"],
                    "step_path": rec["step_path"],
                    "is_shell": False,
                }
            )
        return cached["meta"], changer_parts

    if str(CAD_CREATOR_ROOT) not in sys.path:
        sys.path.insert(0, str(CAD_CREATOR_ROOT))
    from infrastructure.qualification import tool_changer_geometry as tc_geo
    from infrastructure.qualification import tool_changer_r2_geometry as tcr2

    L = tcr2.layout(CAD_CREATOR_ROOT)
    _, el = tcr2.build(CAD_CREATOR_ROOT, L, pose="locked", boards=None)
    cache_dir.mkdir(parents=True, exist_ok=True)
    changer_parts, cache_records = [], []
    idx = 0
    for name, elem in el.items():
        if elem.domain == "wiring" or elem.role in ("swept_envelope", "vendor_reference"):
            continue
        if name.startswith(("hand_", "research_wrist")):
            continue
        ckey = "wtc_tool" if name == "tool_plate" else ("worm_bronze" if "idler" in name or "pinion" in name else "wtc_master")
        p_step = cache_dir / f"{idx:02d}_wtc_{name}.step"
        if not p_step.exists():
            cq.exporters.export(elem.shape, str(p_step))
        mat_name = elem.metadata.get("material", "7075-T6")
        changer_parts.append(
            {
                "name": f"wtc_{name}",
                "group": "Wrist_Tool_Changer_R2_Locked",
                "shape": elem.shape,
                "color_key": ckey,
                "material": mat_name,
                "step_path": str(p_step),
                "is_shell": False,
            }
        )
        cache_records.append(
            {
                "name": f"wtc_{name}",
                "group": "Wrist_Tool_Changer_R2_Locked",
                "color_key": ckey,
                "material": mat_name,
                "step_path": str(p_step),
            }
        )
        idx += 1

    meta = {
        "z_tool_top_mm": float(L["z_tool_top"]),
        "z_pilot_bottom_mm": float(L["z_pilot_bottom"]),
        "body_od_mm": float(L["r2"]["body_od_mm"]),
        "tool_plate_od_mm": float(L["r2"]["r1_od_mm"]),
        "added_length_mm": float(L["added_length_mm"]),
        "tool_screw_angles_deg": [float(a) for a in tc_geo.TOOL_SCREW_ANGLES],
        "tool_screw_pcd_r_mm": 22.0,
        "central_bore_d_mm": 12.0,
    }
    cache_json.write_text(json.dumps({"meta": meta, "parts": cache_records}, indent=2), encoding="utf-8")
    return meta, changer_parts


def export_colored_step_assembly(parts: list[dict[str, Any]], step_path: Path, root_name: str) -> None:
    step_path.parent.mkdir(parents=True, exist_ok=True)
    assy = cq.Assembly(name=root_name)
    groups: dict[str, cq.Assembly] = {}
    for p in parts:
        gname = p["group"]
        if gname not in groups:
            groups[gname] = cq.Assembly(name=gname)
        r, g, b = PART_COLORS_RGB[p["color_key"]]
        groups[gname].add(cq.Workplane(obj=p["shape"]), name=p["name"], color=cq.Color(r, g, b, 1.0))
    for gname, sub in groups.items():
        assy.add(sub, name=gname)
    assy.save(str(step_path), exportType="STEP")


def export_urdf_and_link_meshes(
    hand: str,
    links: dict[str, dict[str, Any]],
    changer_parts: list[dict[str, Any]],
    r4_parts: list[dict[str, Any]],
    out_dir: Path,
) -> dict[str, Any]:
    links_dir = out_dir / "cad" / f"links_{hand}"
    links_dir.mkdir(parents=True, exist_ok=True)
    urdf_dir = out_dir / "urdf"
    urdf_dir.mkdir(parents=True, exist_ok=True)

    r4_mass_kg = 0.0
    for p in r4_parts:
        dens = DENSITIES_KG_M3.get(p["material"], 1200.0)
        r4_mass_kg += compute_shape_mass_props(p["shape"], dens)["mass_kg"]

    wtc_compound = cq.Compound.makeCompound([p["shape"] for p in changer_parts])
    wtc_stl = links_dir / "wtc_r2_base_link.stl"
    cq.exporters.export(wtc_compound, str(wtc_stl), tolerance=0.08, angularTolerance=0.15)
    wtc_props = compute_shape_mass_props(wtc_compound, 2600.0)

    urdf_lines = [
        '<?xml version="1.0"?>',
        f'<robot name="amber_v05_tool_gripper_r4_{hand}">',
        '  <link name="wtc_r2_base_link">',
        "    <inertial>",
        f'      <origin xyz="{wtc_props["com_mm"][0]*1e-3:.5f} {wtc_props["com_mm"][1]*1e-3:.5f} {wtc_props["com_mm"][2]*1e-3:.5f}" rpy="0 0 0"/>',
        f'      <mass value="{wtc_props["mass_kg"]:.5f}"/>',
        f'      <inertia ixx="{wtc_props["inertia_kg_m2"]["ixx"]:.7f}" ixy="0" ixz="0" iyy="{wtc_props["inertia_kg_m2"]["iyy"]:.7f}" iyz="0" izz="{wtc_props["inertia_kg_m2"]["izz"]:.7f}"/>',
        "    </inertial>",
        "    <visual>",
        f'      <geometry><mesh filename="../cad/links_{hand}/wtc_r2_base_link.stl" scale="0.001 0.001 0.001"/></geometry>',
        '      <material name="wtc_metal"><color rgba="0.18 0.20 0.24 1.0"/></material>',
        "    </visual>",
        "    <collision>",
        f'      <geometry><mesh filename="../cad/links_{hand}/wtc_r2_base_link.stl" scale="0.001 0.001 0.001"/></geometry>',
        "    </collision>",
        "  </link>",
        '  <joint name="wtc_r2_to_r4_lock_joint" type="fixed">',
        '    <parent link="wtc_r2_base_link"/>',
        '    <child link="r4_palm_link"/>',
        '    <origin xyz="0 0 0" rpy="0 0 0"/>',
        "  </joint>",
    ]

    for lname, info in links.items():
        stl_p = links_dir / f"{lname}.stl"
        cq.exporters.export(info["local_shape"], str(stl_p), tolerance=0.06, angularTolerance=0.12)
        mp = compute_shape_mass_props(info["local_shape"], info["density"])
        r, g, b = PART_COLORS_RGB[info["color_key"]]
        cx, cy, cz = [v * 1e-3 for v in mp["com_mm"]]
        ine = mp["inertia_kg_m2"]
        urdf_lines.extend(
            [
                f'  <link name="{lname}">',
                "    <inertial>",
                f'      <origin xyz="{cx:.5f} {cy:.5f} {cz:.5f}" rpy="0 0 0"/>',
                f'      <mass value="{mp["mass_kg"]:.5f}"/>',
                f'      <inertia ixx="{ine["ixx"]:.7f}" ixy="{ine["ixy"]:.7f}" ixz="{ine["ixz"]:.7f}" iyy="{ine["iyy"]:.7f}" iyz="{ine["iyz"]:.7f}" izz="{ine["izz"]:.7f}"/>',
                "    </inertial>",
                "    <visual>",
                f'      <geometry><mesh filename="../cad/links_{hand}/{lname}.stl" scale="0.001 0.001 0.001"/></geometry>',
                f'      <material name="mat_{lname}"><color rgba="{r:.3f} {g:.3f} {b:.3f} 1.0"/></material>',
                "    </visual>",
                "    <collision>",
                f'      <geometry><mesh filename="../cad/links_{hand}/{lname}.stl" scale="0.001 0.001 0.001"/></geometry>',
                "    </collision>",
                "  </link>",
            ]
        )
        if "joint_name" in info:
            ox, oy, oz = [v * 1e-3 for v in info["origin_xyz_mm"]]
            rr, rp, ry = info["origin_rpy"]
            ax, ay, az = info["axis"]
            lo, hi = info["limits_rad"]
            urdf_lines.extend(
                [
                    f'  <joint name="{info["joint_name"]}" type="revolute">',
                    f'    <parent link="{info["parent"]}"/>',
                    f'    <child link="{lname}"/>',
                    f'    <origin xyz="{ox:.5f} {oy:.5f} {oz:.5f}" rpy="{rr:.5f} {rp:.5f} {ry:.5f}"/>',
                    f'    <axis xyz="{ax:.4f} {ay:.4f} {az:.4f}"/>',
                    f'    <limit lower="{lo:.5f}" upper="{hi:.5f}" effort="8.0" velocity="3.14"/>',
                    '    <dynamics damping="0.08" friction="0.02"/>',
                    "  </joint>",
                ]
            )

    urdf_lines.append("</robot>\n")
    urdf_path = urdf_dir / f"amber_gripper_r4_{hand}.urdf"
    urdf_path.write_text("\n".join(urdf_lines), encoding="utf-8")
    return {"urdf_path": str(urdf_path), "r4_gripper_only_mass_kg": round(r4_mass_kg, 4), "wtc_r2_mass_kg": wtc_props["mass_kg"]}


def write_teleop_and_inheritance_contract(out_dir: Path, changer_meta: dict[str, Any], lay: dict[str, Any]) -> Path:
    teleop_dir = out_dir / "teleop"
    teleop_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "schema": "amber_v0.5.capture_glove_to_r4_teleop_and_r2_r3_inheritance.v3",
        "glove_visual_and_kinematic_parity": {
            "reference": "CSU Chico MECA-440B Team 10 Things Capture Glove (Slide 11 Final CAD)",
            "palm_chassis": "Winged bent-angle radial & ulnar cheek plates + forward sloped palmar apron + elevated horizontal Ø13 mm MCP knuckle shaft + chamfered upper MCP palmar block + dual side cheek camera/sensor pods",
            "upper_digits": "24 mm Index (twin-bar clevis + chiseled distal block) + 52 mm Coupled Middle-Fourth-Pinkie (twin-strut open ladder + wide chiseled distal paddle) overhanging the C-mouth",
            "opposing_thumb": "Low radial-palmar worm-locked -20/0/+20 deg 3-position opposition turret + enlarged 28 mm pentagonal chiseled thumb block with dorsal alignment ridge",
            "rotary_hubs": "5 exposed 6-notched dark-grey rotary joint encoder caps matching the glove's 5 Bourns rotary sensors",
        },
        "inherited_engineering_rules_preserved": {
            "sizing_and_reach": {
                "L1_mm": lay["L1"],
                "L2_mm": lay["L2"],
                "palm_width_mm": lay["palm_width_mm"],
                "tool_point_reach_mm": lay["tool_point_z_above_z0_mm"],
                "limit_mm": 193.0,
            },
            "palm_stability_surface": "Moulded twin-eminence Shore A 60 PU palmar saddle + 4x Ohmite FSR07 force zones + chamfered MCP palmar block",
            "transmission_and_palm_packaging": "two_drives_per_finger + drives_fixed_in_palm inside enclosed 2-layer 3-deck lower wrist-palm belly (6x Pololu HPCB worm-locked SK78 tendon spools + 7th thumb opposition worm + 500 Hz reflex PCB)",
            "serviceability_and_table_pinch": "Removable M1.6 dorsal tip service crowns + 1+1+3 flush Contactile 3DFBS-12T-50N domes + 0.50 mm flat-bevel table pen pinch (GR3-PEN-01)",
            "bolt_interface": {
                "angles_deg": changer_meta["tool_screw_angles_deg"],
                "pcd_radius_mm": changer_meta["tool_screw_pcd_r_mm"],
                "bilateral_rule": "Un-mirrored Ø52x6 mm base_disc so both Right and Left hands lock onto any standardized Wrist_Tool_Changer_R2",
            },
        },
    }
    out_p = teleop_dir / "glove_to_r4_teleop_contract.json"
    out_p.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return out_p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["all", "right", "left", "pair_step", "pose_meshes"],
        default="all",
    )
    parser.add_argument("--pose", default="glove_showcase")
    args = parser.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    qual_dir = OUT_ROOT / "qualification"
    qual_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "all":
        for sub in ("right", "left", "pair_step", "pose_meshes"):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--mode", sub, "--pose", args.pose], check=True)
        m_r = json.loads((qual_dir / "r4_hand_right.json").read_text(encoding="utf-8"))
        m_l = json.loads((qual_dir / "r4_hand_left.json").read_text(encoding="utf-8"))
        manifest = {
            "project": "Amber v0.5 Tool Gripper R4 (True Capture-Glove C-Mouth Architecture + Full R2/R3 Engineering Inheritance + Wrist Tool Changer R2)",
            "cad_creator_readonly": True,
            "default_cad_pose": args.pose,
            "wrist_tool_changer_r2": m_r["wrist_tool_changer_r2"],
            "sizing_and_reach": m_r["sizing_and_reach"],
            "hands": {"right": m_r["hand_summary"], "left": m_l["hand_summary"]},
            "bilateral_pair_step_path": str(OUT_ROOT / "cad" / "amber_gripper_r4_bilateral_pair_with_changers.step"),
            "teleop_contract_path": m_r["teleop_contract_path"],
        }
        (qual_dir / "r4_design_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        sys.stdout.flush()
        os._exit(0)

    changer_meta, changer_parts = load_wrist_tool_changer_r2_readonly()
    z0 = changer_meta["z_tool_top_mm"]
    screw_angles = changer_meta["tool_screw_angles_deg"]

    if args.mode == "pair_step":
        shifted = []
        for h in ("right", "left"):
            lay = r4_layout(z0, h)
            r4_p, _ = build_r4_complete_pose(lay, benchmark_poses(lay)[args.pose], screw_angles)
            T_sh = make_T(np.eye(3), (0.0, 95.0 if h == "right" else -95.0, 0.0))
            for p in changer_parts + r4_p:
                shifted.append({**p, "name": f"{h}_{p['name']}", "group": f"{h.capitalize()}_{p['group']}", "shape": transform_shape(p["shape"], T_sh)})
        export_colored_step_assembly(shifted, OUT_ROOT / "cad" / "amber_gripper_r4_bilateral_pair_with_changers.step", "Amber_v05_Bilateral_R4_Pair")
        sys.stdout.flush()
        os._exit(0)

    if args.mode == "pose_meshes":
        lay = r4_layout(z0, "right")
        bposes = benchmark_poses(lay)
        for pk in ("wrap_bottle_1l", "pinch_pen"):
            pdir = OUT_ROOT / "cad" / f"pose_stl_{pk}"
            if pdir.exists():
                for old_f in pdir.glob("*.stl"):
                    old_f.unlink()
            pdir.mkdir(parents=True, exist_ok=True)
            r4_p, _ = build_r4_complete_pose(lay, bposes[pk], screw_angles, include_payload=True)
            recs = []
            for idx, p in enumerate(changer_parts + r4_p):
                sp = pdir / f"{idx:03d}_{p['name']}.stl"
                cq.exporters.export(p["shape"], str(sp), tolerance=0.10, angularTolerance=0.18)
                recs.append(
                    {
                        "name": p["name"],
                        "stl_path": str(sp),
                        "rgb": list(PART_COLORS_RGB[p["color_key"]]),
                        "material": p["material"],
                        "is_shell": bool(p.get("is_shell", False)),
                    }
                )
            (OUT_ROOT / "cad" / f"pose_meshes_{pk}.json").write_text(json.dumps(recs, indent=2), encoding="utf-8")
        sys.stdout.flush()
        os._exit(0)

    h = args.mode
    lay = r4_layout(z0, h)
    bposes = benchmark_poses(lay)
    r4_parts, links = build_r4_complete_pose(lay, bposes[args.pose], screw_angles)
    full_parts = changer_parts + r4_parts

    step_path = OUT_ROOT / "cad" / f"amber_gripper_r4_{h}_with_changer.step"
    export_colored_step_assembly(full_parts, step_path, f"Amber_v05_Gripper_R4_{h.capitalize()}_With_WTC_R2")

    urdf_info = export_urdf_and_link_meshes(h, links, changer_parts, r4_parts, OUT_ROOT)
    qual = qualify_all_r4_benchmarks(lay, screw_angles)

    parts_dir = OUT_ROOT / "cad" / f"parts_{h}_r4"
    if parts_dir.exists():
        for old_s in parts_dir.glob("*.step"):
            old_s.unlink()
    parts_dir.mkdir(parents=True, exist_ok=True)
    fcstd_index = [
        {
            "name": p["name"],
            "group": p["group"],
            "step_path": p["step_path"],
            "rgb": list(PART_COLORS_RGB[p["color_key"]]),
            "material": p["material"],
            "is_shell": False,
        }
        for p in changer_parts
    ]
    base_idx = len(fcstd_index)
    for off, p in enumerate(r4_parts):
        p_step = parts_dir / f"{base_idx + off:03d}_{p['name']}.step"
        cq.exporters.export(p["shape"], str(p_step))
        fcstd_index.append(
            {
                "name": p["name"],
                "group": p["group"],
                "step_path": str(p_step),
                "rgb": list(PART_COLORS_RGB[p["color_key"]]),
                "material": p["material"],
                "is_shell": bool(p.get("is_shell", False)),
            }
        )
    (OUT_ROOT / "cad" / f"fcstd_parts_{h}.json").write_text(json.dumps(fcstd_index, indent=2), encoding="utf-8")

    contract_p = write_teleop_and_inheritance_contract(OUT_ROOT, changer_meta, lay)
    rec = {
        "wrist_tool_changer_r2": changer_meta,
        "sizing_and_reach": {
            "L1_mm": lay["L1"],
            "L2_mm": lay["L2"],
            "D_mm": lay["D"],
            "tool_point_reach_mm": lay["tool_point_z_above_z0_mm"],
            "reach_limit_mm": 193.0,
            "palm_width_mm": lay["palm_width_mm"],
            "width_limit_mm": 125.0,
        },
        "teleop_contract_path": str(contract_p),
        "hand_summary": {
            "step_with_changer_path": str(step_path),
            "urdf_path": urdf_info["urdf_path"],
            "r4_gripper_only_mass_kg": urdf_info["r4_gripper_only_mass_kg"],
            "total_with_changer_mass_kg": round(urdf_info["r4_gripper_only_mass_kg"] + urdf_info["wtc_r2_mass_kg"], 4),
            "part_count_total": len(full_parts),
            "part_count_r4": len(r4_parts),
            "part_count_changer_r2": len(changer_parts),
            "qualification": qual,
            "all_benchmarks_passed": all(v["pass"] for v in qual.values()),
        },
    }
    (qual_dir / f"r4_hand_{h}.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
