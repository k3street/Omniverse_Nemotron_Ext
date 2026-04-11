"""
prim_properties.py
------------------
Reads the currently selected prim's USD attributes and applied schemas,
returning a structured dict the LLM can summarise or reason about.
"""
from __future__ import annotations
import carb
from typing import Dict, Any, List


def get_selected_prim_properties() -> Dict[str, Any]:
    """
    Returns a dict with all attribute values for the first selected prim.
    Includes physics schema info if applicable.
    """
    try:
        import omni.usd
        from pxr import Usd, UsdGeom, UsdPhysics, Gf

        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            return {"error": "No stage loaded"}

        selected = ctx.get_selection().get_selected_prim_paths()
        if not selected:
            return {"error": "No prim selected"}

        prim_path = selected[0]
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return {"error": f"Invalid prim: {prim_path}"}

        # ── Basic info ────────────────────────────────────────────────────────
        result: Dict[str, Any] = {
            "path": prim_path,
            "type": prim.GetTypeName(),
            "active": prim.IsActive(),
            "schemas": [str(s) for s in prim.GetAppliedSchemas()],
        }

        # ── All authored attributes ───────────────────────────────────────────
        attrs: Dict[str, Any] = {}
        for attr in prim.GetAttributes():
            if attr.HasAuthoredValue():
                val = attr.Get()
                attrs[attr.GetName()] = _serialize_val(val)
        result["attributes"] = attrs

        # ── Transform ────────────────────────────────────────────────────────
        if prim.IsA(UsdGeom.Xformable):
            xf = UsdGeom.XformCache(0).GetLocalToWorldTransform(prim)
            t = xf.ExtractTranslation()
            result["world_position"] = [t[0], t[1], t[2]]

        # ── Physics summary ───────────────────────────────────────────────────
        physics_info: Dict[str, Any] = {}
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rb = UsdPhysics.RigidBodyAPI(prim)
            physics_info["rigid_body"] = {
                "enabled": rb.GetRigidBodyEnabledAttr().Get(),
            }
        if prim.HasAPI(UsdPhysics.MassAPI):
            mass = UsdPhysics.MassAPI(prim)
            physics_info["mass"] = mass.GetMassAttr().Get()
        if physics_info:
            result["physics"] = physics_info

        return result

    except Exception as e:
        carb.log_warn(f"[IsaacAssist] prim_properties error: {e}")
        return {"error": str(e)}


def _serialize_val(val: Any) -> Any:
    """Convert USD/Gf types to JSON-safe primitives."""
    if val is None:
        return None
    if isinstance(val, (bool, int, float, str)):
        return val
    if hasattr(val, "__iter__") and not isinstance(val, str):
        try:
            return list(val)
        except Exception:
            pass
    return str(val)
