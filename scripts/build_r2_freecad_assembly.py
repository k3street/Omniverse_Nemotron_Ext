"""Build a native FreeCAD 1.0 multi-part B-Rep assembly (`.FCStd`) AND a single-file
multi-body `.step` assembly (`r2_hybrid_full_assembly.step`, ready for Fusion 360 / SolidWorks)
showing the CAD Creator R2 3-Finger Tactile Gripper grasping the telemetry cartridge over the
pickup pedestal and chamfered 6061-T6 mating dock.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import FreeCAD as App
import Part

REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_DIR = REPO_ROOT / "workspace" / "r2_hybrid_showcase"
MANIFEST_PATH = SHOWCASE_DIR / "01_cad_creator_r2" / "cad_creator_r2_manifest.json"
TRAJ_PATH = SHOWCASE_DIR / "02_mujoco_tactile_sim" / "mujoco_r2_trajectory.json"
OUT_DIR = SHOWCASE_DIR / "00_cad_assembly"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    traj = json.loads(TRAJ_PATH.read_text(encoding="utf-8"))
    mid_idx = int(traj.get("mid_grasp_frame_index", 81))
    frame_poses = traj["frames"][mid_idx]["bodies"]

    doc = App.newDocument("R2_Tactile_Gripper_Workcell")
    grp_gripper = doc.addObject("App::DocumentObjectGroup", "R2_3Finger_Tactile_Gripper")
    grp_gripper.Label = "R2_3Finger_Tactile_Gripper (0.735 kg)"
    grp_workcell = doc.addObject("App::DocumentObjectGroup", "Workcell_Payload_And_Fixtures")
    grp_workcell.Label = "Workcell_Payload_And_Fixtures"

    gripper_order = [
        ("palm_body", "Palm_And_SpoolDrive_Housing"),
        ("turntable_a", "Turntable_A_7075"),
        ("turntable_b", "Turntable_B_7075"),
        ("thumb_prox", "Thumb_Proximal_7075_PU"),
        ("thumb_dist", "Thumb_Distal_Contactile_3DFBS"),
        ("finger_a_prox", "FingerA_Proximal_7075_PU"),
        ("finger_a_dist", "FingerA_Distal_Contactile_3DFBS"),
        ("finger_b_prox", "FingerB_Proximal_7075_PU"),
        ("finger_b_dist", "FingerB_Distal_Contactile_3DFBS"),
    ]
    workcell_order = [
        ("r2_pickup_pedestal", "Pickup_Pedestal_6061"),
        ("r2_telemetry_cartridge", "Telemetry_Cartridge_PLA"),
        ("r2_mating_dock", "Chamfered_Mating_Dock_6061"),
    ]

    all_records = dict(manifest["gripper_links"])
    all_records["r2_telemetry_cartridge"] = manifest["workpiece"]
    all_records["r2_pickup_pedestal"] = manifest["pickup_pedestal"]
    all_records["r2_mating_dock"] = manifest["mating_dock"]

    placed_shapes = []

    for key, label in gripper_order + workcell_order:
        rec = all_records[key]
        step_file = Path(rec["step_path"])
        shape = Part.read(str(step_file))

        pose = frame_poses[key]
        px_mm, py_mm, pz_mm = [float(v) * 1000.0 for v in pose["pos"]]
        qw, qx, qy, qz = [float(v) for v in pose["quat_wxyz"]]
        placement = App.Placement(App.Vector(px_mm, py_mm, pz_mm), App.Rotation(qx, qy, qz, qw))

        feat = doc.addObject("Part::Feature", key)
        feat.Label = label
        feat.Shape = shape
        feat.Placement = placement

        if key in manifest["gripper_links"]:
            grp_gripper.addObject(feat)
        else:
            grp_workcell.addObject(feat)

        placed_copy = shape.copy()
        placed_copy.Placement = placement
        placed_shapes.append(placed_copy)

    doc.recompute()

    fcstd_path = OUT_DIR / "r2_hybrid_cad_assembly.FCStd"
    if fcstd_path.exists():
        fcstd_path.unlink()
    doc.saveAs(str(fcstd_path))

    step_assy_path = OUT_DIR / "r2_hybrid_full_assembly.step"
    compound = Part.makeCompound(placed_shapes)
    compound.exportStep(str(step_assy_path))

    print(
        json.dumps(
            {
                "fcstd_path": str(fcstd_path),
                "fusion_step_assembly_path": str(step_assy_path),
                "part_count": len(placed_shapes),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
