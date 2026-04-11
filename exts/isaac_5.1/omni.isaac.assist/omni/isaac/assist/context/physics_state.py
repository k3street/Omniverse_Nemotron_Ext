"""
physics_state.py
-----------------
Reads robot / articulation physics state from inside the Kit process.
Uses the isaacsim.core high-level API where available, with a USD fallback.
"""
from __future__ import annotations
import carb
from typing import Dict, Any, List, Optional


def get_articulation_state(prim_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns joint positions, velocities, and world pose for the articulation
    at prim_path (or the first selected prim if prim_path is None).
    """
    try:
        import omni.usd
        from pxr import UsdPhysics

        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            return {"error": "No stage loaded"}

        if prim_path is None:
            selected = ctx.get_selection().get_selected_prim_paths()
            if not selected:
                return {"error": "No prim selected and no prim_path provided"}
            prim_path = selected[0]

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return {"error": f"Invalid prim: {prim_path}"}

        # Try high-level Articulation API first
        try:
            from isaacsim.core.prims import SingleArticulation
            articulation = SingleArticulation(prim_path=prim_path)
            articulation.initialize()
            positions = articulation.get_joint_positions()
            velocities = articulation.get_joint_velocities()
            dof_names = articulation.dof_names
            return {
                "prim_path": prim_path,
                "dof_names": list(dof_names),
                "joint_positions": positions.tolist() if hasattr(positions, "tolist") else list(positions),
                "joint_velocities": velocities.tolist() if hasattr(velocities, "tolist") else list(velocities),
            }
        except Exception as inner:
            carb.log_info(f"[IsaacAssist] High-level articulation unavailable ({inner}), using USD fallback")

        # USD attribute fallback — read DriveAPI targets
        joints: List[Dict] = []
        for child in stage.Traverse():
            if child.GetPath().HasPrefix(prim.GetPath()):
                if child.HasAPI(UsdPhysics.DriveAPI):
                    for token in ["angular", "linear"]:
                        drive = UsdPhysics.DriveAPI.Get(child, token)
                        if drive:
                            joints.append({
                                "path": str(child.GetPath()),
                                "drive_type": token,
                                "target_position": drive.GetTargetPositionAttr().Get(),
                                "target_velocity": drive.GetTargetVelocityAttr().Get(),
                                "stiffness": drive.GetStiffnessAttr().Get(),
                                "damping": drive.GetDampingAttr().Get(),
                            })

        return {"prim_path": prim_path, "joints_usd": joints}

    except Exception as e:
        carb.log_warn(f"[IsaacAssist] physics_state error: {e}")
        return {"error": str(e)}
