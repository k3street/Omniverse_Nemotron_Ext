"""Phase 19 — Kit RPC execution path for apply_layout_spec_to_scene.

Block 1B closes the open seam between canvas ratification and actual
scene mutation. The ratifier (`ratify.py`) produces a LayoutSpec; the
instantiator walks the spec, emits canonical USD-Python patches, and
posts them via `kit_tools.queue_exec_patch`.

Phase 19 CODE-GENERATOR upgrade: `LayoutSpecCodeGenerator` provides
rigorous per-class USD-Python code generation with dry-run validation.
The live Kit RPC execution path remains scaffold pending runtime testing.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 19.
"""
from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .asset_resolution import resolve_object_asset
from .relation_reasoning import normalize_spatial_relations, verify_relation_geometry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 19 CODE-GENERATOR layer
# ---------------------------------------------------------------------------

#: All prim classes handled by LayoutSpecCodeGenerator.
SUPPORTED_PRIM_CLASSES: set = {
    "Cube",
    "Sphere",
    "Cylinder",
    "Cone",
    "Plane",
    "DistantLight",
    "SphereLight",
    "DomeLight",
    "Camera",
    "Xform",
    "Reference",
}

PHASE_STATUS: str = "landed"


def get_phase_metadata() -> dict:
    """Return Phase 19 metadata dict."""
    return {
        "phase": 19,
        "title": "Implement Kit RPC execution path for apply_layout_spec_to_scene",
        "status": PHASE_STATUS,
        "agent_type": "sonnet-bounded",
        "note": "CODE-GENERATOR layer landed; live Kit RPC exec remains scaffold",
    }


# Prim classes that map to UsdGeom
_USDGEOM_CLASSES = {"Cube", "Sphere", "Cylinder", "Cone", "Plane", "Camera", "Xform"}
# Prim classes that map to UsdLux
_USDLUX_CLASSES = {"DistantLight", "SphereLight", "DomeLight"}


class LayoutSpecCodeGenerator:
    """Per-class USD-Python code generator for LayoutSpec objects.

    Produces valid Kit-executable Python snippets that import `omni.usd`,
    acquire the stage via `omni.usd.get_context().get_stage()`, and call
    the appropriate USD schema Define() method for each prim class.

    Args:
        use_get_context: When True (default) the header uses
            ``omni.usd.get_context().get_stage()``.  Set to False only for
            offline testing where the generated code will not be executed
            through Kit.
    """

    def __init__(self, use_get_context: bool = True) -> None:
        """Initialise the code generator.

        Args:
            use_get_context (bool, optional): When ``True`` (default), the generated
                script header uses ``omni.usd.get_context().get_stage()``.  Set to
                ``False`` for offline testing only.
        """
        self._use_get_context = use_get_context

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_for_prim(
        self,
        prim_class: str,
        prim_path: str,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation_euler_deg: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        scale: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        extra_attrs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate a USD-Python snippet that defines a single prim.

        The snippet assumes ``stage`` is already in scope (provided by
        ``generate_full_script``'s header, or prepended by the caller).

        Args:
            prim_class: Class name, e.g. "Cube", "DistantLight", "Reference".
            prim_path: Absolute USD path, e.g. "/World/MyCube".
            position: (x, y, z) translation in world units.
            rotation_euler_deg: (rx, ry, rz) Euler angles in degrees.
            scale: (sx, sy, sz) uniform or non-uniform scale.
            extra_attrs: Optional dict of additional USD attribute name → value
                pairs emitted as ``prim.GetAttribute("name").Set(value)``.

        Returns:
            A multi-line Python string (no leading ``stage = ...`` header).
        """
        lines: List[str] = []
        path_repr = repr(prim_path)

        if prim_class in _USDGEOM_CLASSES:
            if prim_class == "Xform":
                lines.append(f"prim = UsdGeom.Xform.Define(stage, {path_repr})")
            else:
                lines.append(
                    f"prim = UsdGeom.{prim_class}.Define(stage, {path_repr})"
                )
        elif prim_class in _USDLUX_CLASSES:
            lines.append(f"prim = UsdLux.{prim_class}.Define(stage, {path_repr})")
        elif prim_class == "Reference":
            lines.append(f"prim = UsdGeom.Xform.Define(stage, {path_repr})")
            asset_path = (extra_attrs or {}).get("asset_path", "")
            lines.append(
                f"prim.GetPrim().GetReferences().AddReference({asset_path!r})"
            )
        else:
            # Unknown class — fall back to Xform
            lines.append(f"# Unknown prim class '{prim_class}' — falling back to Xform")
            lines.append(f"prim = UsdGeom.Xform.Define(stage, {path_repr})")

        # Xform ops — translate
        px, py, pz = position
        lines.append(
            f"UsdGeom.XformCommonAPI(prim).SetTranslate("
            f"Gf.Vec3d({px!r}, {py!r}, {pz!r}))"
        )

        # Xform ops — rotate (XYZ Euler, degrees)
        rx, ry, rz = rotation_euler_deg
        if rx != 0.0 or ry != 0.0 or rz != 0.0:
            lines.append(
                f"UsdGeom.XformCommonAPI(prim).SetRotate("
                f"Gf.Vec3f({rx!r}, {ry!r}, {rz!r}))"
            )

        # Xform ops — scale
        sx, sy, sz = scale
        if (sx, sy, sz) != (1.0, 1.0, 1.0):
            lines.append(f"_set_xform_scale(prim, {sx!r}, {sy!r}, {sz!r})")

        physics = (extra_attrs or {}).get("_isaac_assist_physics", {})

        # Extra attributes
        if extra_attrs:
            for attr_name, attr_value in extra_attrs.items():
                if attr_name in {"asset_path", "_isaac_assist_physics"}:
                    continue  # already handled above
                if attr_name == "custom_data" and isinstance(attr_value, dict):
                    for key, value in attr_value.items():
                        lines.append(
                            f"prim.GetPrim().SetCustomDataByKey({str(key)!r}, {value!r})"
                        )
                    continue
                lines.append(
                    f"prim.GetPrim().GetAttribute({attr_name!r}).Set({attr_value!r})"
                )

        if isinstance(physics, dict):
            if physics.get("collision"):
                lines.append(f"_apply_collision(prim.GetPrim(), {prim_path!r})")
            if physics.get("rigid_body"):
                mass_kg = float(physics.get("mass_kg") or 0.05)
                lines.append(f"_apply_rigid_body(prim.GetPrim(), {mass_kg!r})")

        return "\n".join(lines)

    def generate_full_script(self, prims: List[Dict[str, Any]]) -> str:
        """Generate a complete, self-contained Kit-executable USD-Python script.

        Args:
            prims: List of prim descriptors.  Each dict must contain at least
                ``prim_class`` and ``prim_path``.  Optional keys:
                ``position``, ``rotation_euler_deg``, ``scale``,
                ``extra_attrs``.

        Returns:
            A complete Python script string ready for ``queue_exec_patch``.
        """
        if self._use_get_context:
            header_lines = [
                "import omni.usd",
                "import json",
                "from pxr import UsdGeom, UsdLux, Gf, Sdf, Usd, UsdPhysics",
                "stage = omni.usd.get_context().get_stage()",
                "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)",
                "UsdGeom.SetStageMetersPerUnit(stage, 1.0)",
                "",
            ]
        else:
            header_lines = [
                "import json",
                "from pxr import UsdGeom, UsdLux, Gf, Sdf, Usd, UsdPhysics",
                "# stage must be provided by caller",
                "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)",
                "UsdGeom.SetStageMetersPerUnit(stage, 1.0)",
                "",
            ]
        header_lines.extend([
            "def _set_xform_scale(prim, sx, sy, sz):",
            "    xformable = UsdGeom.Xformable(prim.GetPrim())",
            "    for op in xformable.GetOrderedXformOps():",
            "        if op.GetOpName() == 'xformOp:scale':",
            "            attr = op.GetAttr()",
            "            type_name = str(attr.GetTypeName()).lower()",
            "            if 'double' in type_name:",
            "                attr.Set(Gf.Vec3d(sx, sy, sz))",
            "            else:",
            "                attr.Set(Gf.Vec3f(sx, sy, sz))",
            "            return",
            "    UsdGeom.XformCommonAPI(prim).SetScale(Gf.Vec3f(sx, sy, sz))",
            "",
            "def _ensure_physics_scene():",
            "    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')",
            "    scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))",
            "    scene.CreateGravityMagnitudeAttr().Set(9.81)",
            "    return scene",
            "",
            "def _apply_collision(prim, label):",
            "    try:",
            "        UsdPhysics.CollisionAPI.Apply(prim)",
            "    except Exception as exc:",
            "        print(f'[Isaac Assist] collision warning for {label}: {exc}')",
            "",
            "def _apply_rigid_body(prim, mass_kg):",
            "    try:",
            "        UsdPhysics.RigidBodyAPI.Apply(prim)",
            "        mass_api = UsdPhysics.MassAPI.Apply(prim)",
            "        mass_api.CreateMassAttr().Set(float(mass_kg))",
            "    except Exception as exc:",
            "        print(f'[Isaac Assist] rigid-body warning for {prim.GetPath()}: {exc}')",
            "",
            "_ensure_physics_scene()",
            "ground = UsdGeom.Cube.Define(stage, '/World/GroundPlane')",
            "UsdGeom.XformCommonAPI(ground).SetTranslate(Gf.Vec3d(0.0, 0.0, -0.025))",
            "_set_xform_scale(ground, 10.0, 10.0, 0.025)",
            "ground.GetPrim().SetCustomDataByKey('isaac_assist:object_class', 'groundplane')",
            "ground.GetPrim().SetCustomDataByKey('isaac_assist:physics_enabled', True)",
            "_apply_collision(ground.GetPrim(), '/World/GroundPlane')",
            "",
        ])

        sections: List[str] = ["\n".join(header_lines)]

        for prim_desc in prims:
            prim_class = prim_desc.get("prim_class", "Xform")
            prim_path = prim_desc.get("prim_path", "/World/Prim")

            # Normalise position / rotation / scale — accept list or tuple
            raw_pos = prim_desc.get("position", [0.0, 0.0, 0.0])
            raw_rot = prim_desc.get("rotation_euler_deg", [0.0, 0.0, 0.0])
            raw_scale = prim_desc.get("scale", [1.0, 1.0, 1.0])

            position = tuple(float(v) for v in raw_pos)[:3]  # type: ignore[assignment]
            rotation_euler_deg = tuple(float(v) for v in raw_rot)[:3]  # type: ignore[assignment]
            scale = tuple(float(v) for v in raw_scale)[:3]  # type: ignore[assignment]

            extra_attrs = prim_desc.get("extra_attrs")

            snippet = self.generate_for_prim(
                prim_class=prim_class,
                prim_path=prim_path,
                position=position,  # type: ignore[arg-type]
                rotation_euler_deg=rotation_euler_deg,  # type: ignore[arg-type]
                scale=scale,  # type: ignore[arg-type]
                extra_attrs=extra_attrs,
            )
            sections.append(snippet)

        return "\n".join(sections)

    def validate_generated_code(self, code: str) -> List[str]:
        """Validate a generated code string against safety and correctness rules.

        Rules checked:

        1. Must contain ``omni.usd.get_context().get_stage()``  (gate criterion).
        2. Must not contain bare ``exec(``, ``eval(``, ``open(``,
           or ``subprocess``.
        3. Must contain at least one ``Define(stage, ...``.

        Args:
            code: The Python source string to validate.

        Returns:
            A list of human-readable issue strings.  Empty list means clean.
        """
        issues: List[str] = []

        if "omni.usd.get_context().get_stage()" not in code:
            issues.append(
                "missing required call: omni.usd.get_context().get_stage()"
            )

        forbidden = ["exec(", "eval(", "open(", "subprocess"]
        for term in forbidden:
            if term in code:
                issues.append(f"forbidden term detected: {term!r}")

        if "Define(stage," not in code:
            issues.append(
                "no Define(stage, ...) call found — no USD prim would be created"
            )

        return issues


@dataclass
class InstantiateResult:
    """Outcome of an instantiate() call."""
    build_id: Optional[str] = None
    status: str = "unknown"  # "ok" | "no_objects" | "dry_run" | "error"
    message: str = ""
    generated_code: Optional[str] = None
    placed: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    relation_summary: List[Dict[str, Any]] = field(default_factory=list)
    relation_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    relation_verification: Optional[Dict[str, Any]] = None
    variant_summary: Optional[Dict[str, Any]] = None
    raw_result: Optional[Dict[str, Any]] = None

    @classmethod
    def from_exec(cls, result: Dict[str, Any]) -> "InstantiateResult":
        """Construct an InstantiateResult from a raw Kit RPC exec response dict.

        Args:
            result (Dict[str, Any]): Raw response dict with at least a ``"success"`` key.

        Returns:
            InstantiateResult: Status is ``"ok"`` on success, ``"error"`` otherwise.
        """
        success = bool(result.get("success", False))
        return cls(
            build_id=result.get("build_id"),
            status="ok" if success else "error",
            message=result.get("output", "") if not success else "",
            raw_result=result,
        )


def _build_canonical_code(spec, template_id: Optional[str]) -> str:
    """Build the USD-Python patch that materialises spec.objects.

    Phase 19 LANDED: delegates to LayoutSpecCodeGenerator which emits
    real per-class USD Define() calls (UsdGeom for geometry/cameras/xforms,
    UsdLux for lights, AddReference for asset references). Returns a
    Kit-executable script with proper `omni.usd.get_context().get_stage()`
    header.
    """
    objects = getattr(spec, "objects", None) or []

    _CLASS_HEIGHTS_M = {
        "table_small": 0.75, "table_medium": 0.75, "table_large": 0.75,
        "counter": 0.9, "kitchen_counter": 0.9,
        "bin": 0.3, "bin_large": 0.45, "bowl": 0.12, "plate": 0.03,
        "microwave": 0.35,
        "cube": 0.05, "cube_small": 0.05, "cube_medium": 0.08, "cube_large": 0.15,
        "fruit": 0.07, "apple": 0.07, "orange": 0.08, "hamburger": 0.08,
        "conveyor": 0.35, "conveyor_short": 0.35, "conveyor_long": 0.35,
        "franka_panda": 1.0, "ur5e": 1.0, "ur10e": 1.1, "ur10": 1.1,
    }

    _SUPPORT_TOP_M = {
        "table_small": 0.75, "table_medium": 0.75, "table_large": 0.75,
        "counter": 0.9, "kitchen_counter": 0.9,
        "plate": 0.03,
        "conveyor": 0.35, "conveyor_short": 0.35, "conveyor_long": 0.35,
    }

    _INTERIOR_FLOOR_OFFSET_M = {
        "bin": 0.05, "bin_large": 0.07, "bowl": 0.03, "microwave": 0.08,
    }

    _CLASS_NORMALIZER = {
        "cube": "Cube", "sphere": "Sphere", "cylinder": "Cylinder",
        "cone": "Cone", "plane": "Plane",
        "camera": "Camera", "xform": "Xform",
        "distant_light": "DistantLight", "distantlight": "DistantLight",
        "sphere_light": "SphereLight", "spherelight": "SphereLight",
        "dome_light": "DomeLight", "domelight": "DomeLight",
        "cube_small": "Cube", "cube_medium": "Cube", "cube_large": "Cube",
        "table_small": "Cube", "table_medium": "Cube", "table_large": "Cube",
        "counter": "Cube", "kitchen_counter": "Cube",
        "bin": "Cube", "bin_large": "Cube", "shelf": "Cube",
        "microwave": "Cube",
        "bowl": "Cylinder", "plate": "Cylinder",
        "fruit": "Sphere", "apple": "Sphere", "orange": "Sphere",
        "hamburger": "Cylinder",
        "conveyor_short": "Cube", "conveyor_long": "Cube",
        "wall": "Cube", "fence": "Cube", "obstacle_box": "Cube",
        "obstacle_cylinder": "Cylinder",
        "groundplane": "Plane",
        "camera_overhead": "Camera", "camera_side": "Camera",
    }

    _ROBOT_CLASSES = {
        "franka_panda", "ur5e", "ur10e", "ur10",
        "kinova_gen3", "carter", "jetbot", "spot", "h1",
    }
    _STATIC_COLLIDER_CLASSES = {
        "table_small", "table_medium", "table_large",
        "counter", "kitchen_counter", "bin", "bin_large", "shelf",
        "conveyor", "conveyor_short", "conveyor_long", "rotary_table",
        "kit_tray", "fence", "wall", "obstacle_box", "obstacle_cylinder",
        "microwave", "plate", "bowl",
    }
    _RIGID_WORKPIECE_CLASSES = {
        "cube", "cube_small", "cube_medium", "cube_large",
        "cylinder_small", "cylinder_medium", "cylinder_large",
        "sphere", "fruit", "apple", "orange", "hamburger",
        "screw", "nut", "bolt",
    }

    def _obj_get(obj: Any, attr: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def _position3(value: Any) -> List[float]:
        if hasattr(value, "x") and hasattr(value, "y"):
            return [float(value.x), float(value.y), float(getattr(value, "z", 0.0))]
        try:
            seq = list(value)
        except Exception:
            seq = [0.0, 0.0, 0.0]
        while len(seq) < 3:
            seq.append(0.0)
        return [float(seq[0]), float(seq[1]), float(seq[2])]

    def _scale3(obj: Any, fallback: Any) -> List[float]:
        size = _obj_get(obj, "size", None)
        if hasattr(size, "w") and hasattr(size, "h"):
            return [float(size.w), float(size.h), 1.0]
        if isinstance(size, dict) and "w" in size and "h" in size:
            return [float(size["w"]), float(size["h"]), 1.0]
        try:
            seq = list(fallback)
        except Exception:
            seq = [1.0, 1.0, 1.0]
        while len(seq) < 3:
            seq.append(1.0)
        return [float(seq[0]), float(seq[1]), float(seq[2])]

    def _metadata(obj: Any) -> Dict[str, Any]:
        value = _obj_get(obj, "metadata", {}) or {}
        return value if isinstance(value, dict) else {}

    def _object_id(obj: Any) -> str:
        return str(_obj_get(obj, "id", ""))

    def _object_class(obj: Any) -> str:
        return str(_obj_get(obj, "object_class", "") or _obj_get(obj, "class", "") or "")

    def _object_name(obj: Any) -> str:
        return str(_obj_get(obj, "name", "") or _object_id(obj) or _object_class(obj))

    def _usd_identifier(value: str, fallback: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
        cleaned = "_".join(part for part in cleaned.split("_") if part)
        if not cleaned:
            cleaned = fallback
        if cleaned and cleaned[0].isdigit():
            cleaned = f"Obj_{cleaned}"
        return cleaned or fallback

    def _height_m(obj: Any, scale: Optional[List[float]] = None) -> float:
        metadata = _metadata(obj)
        value = metadata.get("height_m") or metadata.get("asset_height_m")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        obj_class = _object_class(obj).lower()
        if obj_class in _CLASS_HEIGHTS_M:
            return _CLASS_HEIGHTS_M[obj_class]
        if scale and len(scale) >= 3 and scale[2] > 0:
            return float(scale[2])
        return 0.1

    def _support_top_m(obj: Any) -> float:
        metadata = _metadata(obj)
        value = metadata.get("support_surface_z_m") or metadata.get("top_z_m")
        if isinstance(value, (int, float)):
            return float(value)
        return _SUPPORT_TOP_M.get(_object_class(obj).lower(), _height_m(obj))

    def _interior_floor_offset_m(obj: Any) -> float:
        metadata = _metadata(obj)
        value = metadata.get("interior_floor_z_m")
        if isinstance(value, (int, float)):
            return float(value)
        return _INTERIOR_FLOOR_OFFSET_M.get(_object_class(obj).lower(), 0.02)

    reasoning = normalize_spatial_relations(spec)
    relations = [rel.as_dict() for rel in reasoning.relations]
    object_by_id = {_object_id(obj): obj for obj in objects if _object_id(obj)}
    parent_relation_by_subject = {
        rel["subject_id"]: rel
        for rel in relations
        if rel["relation"] in {"on_top_of", "inside", "stacked_above", "mounted_to", "beside"}
    }

    def _base_position_for_object(
        obj: Any,
        computed: Dict[str, List[float]],
        stack: Optional[set[str]] = None,
    ) -> List[float]:
        obj_id = _object_id(obj)
        if obj_id in computed:
            return computed[obj_id]
        if stack is None:
            stack = set()
        if obj_id in stack:
            return _position3(_obj_get(obj, "position", [0.0, 0.0, 0.0]))
        stack.add(obj_id)

        own_position = _position3(_obj_get(obj, "position", [0.0, 0.0, 0.0]))
        scale = _scale3(obj, _obj_get(obj, "scale", [1.0, 1.0, 1.0]))
        relation = parent_relation_by_subject.get(obj_id)
        if not relation:
            computed[obj_id] = own_position
            return own_position

        parent = object_by_id.get(relation["object_id"])
        if parent is None:
            computed[obj_id] = own_position
            return own_position

        parent_position = _base_position_for_object(parent, computed, stack)
        parent_height = _height_m(parent)
        child_height = _height_m(obj, scale)
        relation_kind = relation["relation"]
        if relation_kind == "mounted_to" and _object_class(obj).lower() in _ROBOT_CLASSES:
            z = parent_position[2] + _support_top_m(parent)
        elif relation_kind in {"on_top_of", "supports", "stacked_above", "mounted_to"}:
            z = parent_position[2] + _support_top_m(parent) + child_height / 2.0
        elif relation_kind in {"inside", "contains"}:
            z = (
                parent_position[2]
                - parent_height / 2.0
                + _interior_floor_offset_m(parent)
                + child_height / 2.0
            )
        elif relation_kind == "beside":
            parent_scale = _scale3(parent, _obj_get(parent, "scale", [1.0, 1.0, 1.0]))
            clearance = 0.15
            x = parent_position[0] + parent_scale[0] / 2.0 + scale[0] / 2.0 + clearance
            computed[obj_id] = [round(x, 4), parent_position[1], own_position[2]]
            return computed[obj_id]
        else:
            z = own_position[2]

        computed[obj_id] = [parent_position[0], parent_position[1], round(z, 4)]
        return computed[obj_id]

    prims: List[Dict[str, Any]] = []
    computed_positions: Dict[str, List[float]] = {}
    used_prim_names: Dict[str, int] = {}
    for i, obj in enumerate(objects):
        asset_resolution = resolve_object_asset(obj)
        if hasattr(obj, "object_class"):
            obj_class = getattr(obj, "object_class", "unknown") or "unknown"
            position = getattr(obj, "position", None) or [0.0, 0.0, 0.0]
            rotation = getattr(obj, "rotation_euler_deg", None) or [0.0, 0.0, 0.0]
            scale = getattr(obj, "scale", None) or [1.0, 1.0, 1.0]
            asset_ref = getattr(obj, "asset_path", None) or getattr(obj, "asset_ref", None)
        else:
            obj_class = obj.get("object_class", "unknown")
            position = obj.get("position", [0.0, 0.0, 0.0])
            rotation = obj.get("rotation_euler_deg", [0.0, 0.0, 0.0])
            scale = obj.get("scale", [1.0, 1.0, 1.0])
            asset_ref = obj.get("asset_path") or obj.get("asset_ref")

        position = _base_position_for_object(obj, computed_positions)
        scale = _scale3(obj, scale)

        normalized_class = _CLASS_NORMALIZER.get(
            str(obj_class).lower(), str(obj_class)
        )
        if asset_resolution:
            asset_ref = asset_resolution.usd_ref
            normalized_class = "Reference"
        if normalized_class not in SUPPORTED_PRIM_CLASSES:
            if asset_ref:
                normalized_class = "Reference"
            else:
                normalized_class = "Xform"
        if normalized_class in {"Cube", "Sphere", "Cylinder", "Cone"}:
            scale[2] = _height_m(obj, scale)

        object_id = _object_id(obj)
        object_name = _object_name(obj)
        fallback_name = f"{normalized_class}_{i + 1}"
        prim_name = _usd_identifier(object_name, fallback_name)
        count = used_prim_names.get(prim_name, 0)
        used_prim_names[prim_name] = count + 1
        if count:
            prim_name = f"{prim_name}_{count + 1}"

        custom_data = {
            "isaac_assist:layout_id": object_id,
            "isaac_assist:layout_name": object_name,
            "isaac_assist:object_class": str(obj_class),
            "isaac_assist:role_hint": str(obj_class),
        }
        if asset_resolution:
            custom_data["isaac_assist:asset_source"] = asset_resolution.source
            custom_data["isaac_assist:asset_ref"] = asset_resolution.usd_ref

        prim_desc: Dict[str, Any] = {
            "prim_class": normalized_class,
            "prim_path": f"/World/{prim_name}",
            "position": position,
            "rotation_euler_deg": rotation,
            "scale": scale,
            "_source_class": str(obj_class),
            "_source_name": object_name,
            "_source_id": object_id,
            "extra_attrs": {
                "custom_data": custom_data,
                "_isaac_assist_physics": {
                    "collision": (
                        str(obj_class).lower() in _STATIC_COLLIDER_CLASSES
                        or str(obj_class).lower() in _RIGID_WORKPIECE_CLASSES
                    ),
                    "rigid_body": str(obj_class).lower() in _RIGID_WORKPIECE_CLASSES,
                    "mass_kg": float(_metadata(obj).get("mass_kg") or 0.05),
                },
            },
        }
        if str(obj_class).lower() in _ROBOT_CLASSES:
            prim_desc["extra_attrs"]["_isaac_assist_physics"] = {
                "collision": False,
                "rigid_body": False,
            }
        if normalized_class == "Reference" and asset_ref:
            prim_desc["extra_attrs"]["asset_path"] = str(asset_ref)
        prims.append(prim_desc)

    generator = LayoutSpecCodeGenerator(use_get_context=True)
    header_comment = (
        f"# Phase 19 code-gen: {len(prims)} prims, template_id={template_id!r}\n"
    )
    source_comments = "\n".join(
        f"# object[{i}]: source_id={p['_source_id']!r}, source_name={p['_source_name']!r}, "
        f"source_class={p['_source_class']!r} -> "
        f"prim_class={p['prim_class']!r}, path={p['prim_path']!r}"
        for i, p in enumerate(prims)
    )
    relation_comments = "\n".join(
        f"# relation: {rel['subject_id']} {rel['relation']} {rel['object_id']}"
        for rel in relations
    )
    diagnostic_comments = "\n".join(
        f"# relation_diagnostic: {diag.code} {diag.severity}: {diag.message}"
        for diag in reasoning.diagnostics
    )
    for p in prims:
        p.pop("_source_class", None)
        p.pop("_source_id", None)
        p.pop("_source_name", None)
    body = generator.generate_full_script(prims)
    verification = verify_relation_geometry(spec)
    live_verifier = _live_relation_verifier_code(verification)
    comments = "\n".join(
        part for part in (source_comments, relation_comments, diagnostic_comments)
        if part
    )
    return header_comment + (comments + "\n" if comments else "") + body + "\n\n" + live_verifier


def _live_relation_verifier_code(verification: Dict[str, Any]) -> str:
    """Return Kit-executable code that checks relation positions on the stage."""
    payload = {
        "checks": verification.get("checks", []),
        "predicted_positions": verification.get("predicted_positions", {}),
        "planner_status": verification.get("status"),
    }
    return f"""\
# Isaac Assist live relation readback.  The planner-side relation verifier
# predicts positions before execution; this block checks the actual USD stage
# after materialization and stores the report on /World custom data.
try:
    _relation_payload = {json.dumps(payload, sort_keys=True)!r}
    _relation_spec = json.loads(_relation_payload)
    _layout_positions = {{}}
    for _prim in stage.Traverse():
        try:
            _layout_id = _prim.GetCustomDataByKey("isaac_assist:layout_id")
        except Exception:
            _layout_id = None
        if not _layout_id:
            continue
        try:
            _xf = UsdGeom.Xformable(_prim)
            _mat = _xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            _tr = _mat.ExtractTranslation()
            _layout_positions[str(_layout_id)] = [float(_tr[0]), float(_tr[1]), float(_tr[2])]
        except Exception as _exc:
            _layout_positions[str(_layout_id)] = {{"error": str(_exc)}}
    _live_checks = []
    _failed = 0
    for _check in _relation_spec.get("checks", []):
        _subject_id = str(_check.get("subject_id", ""))
        _actual = _layout_positions.get(_subject_id)
        _expected = (
            _check.get("expected_position")
            or _relation_spec.get("predicted_positions", {{}}).get(_subject_id)
        )
        _status = "pass"
        _error_m = 0.0
        if not isinstance(_actual, list) or not isinstance(_expected, list):
            _status = "fail"
            _error_m = None
        else:
            _error_m = max(abs(float(_actual[_i]) - float(_expected[_i])) for _i in range(3))
            if _error_m > 0.02:
                _status = "fail"
        if _status == "fail":
            _failed += 1
        _live_checks.append({{
            "subject_id": _subject_id,
            "relation": _check.get("relation"),
            "object_id": _check.get("object_id"),
            "status": _status,
            "error_m": None if _error_m is None else round(float(_error_m), 4),
            "expected_position": _expected,
            "actual_position": _actual,
        }})
    _live_report = {{
        "status": "fail" if _failed else _relation_spec.get("planner_status", "pass"),
        "check_count": len(_live_checks),
        "failed_count": _failed,
        "checks": _live_checks,
        "actual_positions": _layout_positions,
    }}
    _world = stage.GetPrimAtPath("/World")
    if _world:
        _world.SetCustomDataByKey(
            "isaac_assist:relation_verification",
            json.dumps(_live_report, sort_keys=True),
        )
    print("[Isaac Assist] relation verification:", json.dumps(_live_report, sort_keys=True))
except Exception as _exc:
    print("[Isaac Assist] relation verification warning:", _exc)
"""


def relation_summary(spec: Any) -> List[Dict[str, Any]]:
    """Return a concise relation list for Preview Build diagnostics."""
    objects = getattr(spec, "objects", None) or []
    object_names: Dict[str, str] = {}
    for obj in objects:
        if isinstance(obj, dict):
            obj_id = str(obj.get("id", ""))
            name = str(obj.get("name") or obj_id)
        else:
            obj_id = str(getattr(obj, "id", ""))
            name = str(getattr(obj, "name", "") or obj_id)
        if obj_id:
            object_names[obj_id] = name

    rows: List[Dict[str, Any]] = []
    reasoning = normalize_spatial_relations(spec)
    for rel in reasoning.relations:
        subject_id = rel.subject_id
        object_id = rel.object_id
        relation = rel.relation
        if subject_id and object_id and relation:
            rows.append({
                "subject_id": subject_id,
                "subject_name": object_names.get(subject_id, subject_id),
                "relation": relation,
                "object_id": object_id,
                "object_name": object_names.get(object_id, object_id),
                "source": rel.source,
                "confidence": rel.confidence,
            })
    return rows


def relation_diagnostics(spec: Any) -> List[Dict[str, Any]]:
    """Return deterministic relation-reasoning diagnostics."""
    return normalize_spatial_relations(spec).diagnostics_as_dicts()


def relation_verification(spec: Any) -> Dict[str, Any]:
    """Return deterministic relation geometry verification."""
    return verify_relation_geometry(spec)


def variant_summary(spec: Any) -> Dict[str, Any]:
    """Return the variant campaign knobs in build-response form."""
    variants = getattr(spec, "scenario_variants", None)
    if variants is None:
        return {
            "enabled": False,
            "variant_count": 1,
            "seed": 1,
            "lighting": ["studio"],
            "cameras": ["overhead"],
            "actors": [],
            "circumstances": ["nominal"],
            "perturbations": {
                "enabled": True,
                "pose_jitter_m": 0.03,
                "rotation_jitter_deg": 5.0,
                "material_randomization": True,
                "sensor_noise": False,
            },
            "validation": {
                "require_relations": True,
                "require_visibility": True,
                "require_physics": True,
            },
        }
    if hasattr(variants, "model_dump"):
        data = variants.model_dump(mode="json")
    elif isinstance(variants, dict):
        data = dict(variants)
    else:
        data = {}
    data.setdefault("enabled", False)
    data.setdefault("variant_count", 1)
    data.setdefault("seed", 1)
    data.setdefault("lighting", ["studio"])
    data.setdefault("cameras", ["overhead"])
    data.setdefault("actors", [])
    data.setdefault("circumstances", ["nominal"])
    data.setdefault("perturbations", {})
    data.setdefault("validation", {})
    return data


async def instantiate(
    spec: Any,
    template_id: Optional[str] = None,
    dry_run: bool = False,
) -> InstantiateResult:
    """Walk a LayoutSpec, emit per-object USD patches, dispatch to Kit.

    Args:
        spec: A ratified LayoutSpec.
        template_id: Optional canonical template binding the spec.
        dry_run: If True, return the generated code without executing.

    Returns:
        InstantiateResult with build_id (when executed), status, per-object
        placement results.
    """
    objects = getattr(spec, "objects", None)
    if objects is None:
        return InstantiateResult(
            status="no_objects",
            message="LayoutSpec has only intent — canonical pipeline supplies positions",
        )

    code = _build_canonical_code(spec, template_id)

    if dry_run:
        return InstantiateResult(
            status="dry_run",
            generated_code=code,
            message="dry_run — code generated, not executed",
            relation_summary=relation_summary(spec),
            relation_diagnostics=relation_diagnostics(spec),
            relation_verification=relation_verification(spec),
            variant_summary=variant_summary(spec),
        )

    # Live path
    try:
        from ..chat.tools import kit_tools
        result = await kit_tools.queue_exec_patch(code, description=f"Phase 19: instantiate {template_id or 'spec'}")
        outcome = InstantiateResult.from_exec(result if isinstance(result, dict) else {"output": str(result)})
        outcome.relation_summary = relation_summary(spec)
        outcome.relation_diagnostics = relation_diagnostics(spec)
        outcome.relation_verification = relation_verification(spec)
        outcome.variant_summary = variant_summary(spec)
        return outcome
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Phase19 instantiate] queue_exec_patch failed: {type(e).__name__}: {e}")
        return InstantiateResult(
            status="error",
            message=f"{type(e).__name__}: {e}",
            generated_code=code,
            relation_summary=relation_summary(spec),
            relation_diagnostics=relation_diagnostics(spec),
            relation_verification=relation_verification(spec),
            variant_summary=variant_summary(spec),
        )
