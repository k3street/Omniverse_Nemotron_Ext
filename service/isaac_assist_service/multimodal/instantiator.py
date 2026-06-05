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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .asset_resolution import resolve_object_asset

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
            lines.append(
                f"UsdGeom.XformCommonAPI(prim).SetScale("
                f"Gf.Vec3f({sx!r}, {sy!r}, {sz!r}))"
            )

        # Extra attributes
        if extra_attrs:
            for attr_name, attr_value in extra_attrs.items():
                if attr_name == "asset_path":
                    continue  # already handled above
                lines.append(
                    f"prim.GetPrim().GetAttribute({attr_name!r}).Set({attr_value!r})"
                )

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
                "from pxr import UsdGeom, UsdLux, Gf, Sdf",
                "stage = omni.usd.get_context().get_stage()",
                "",
            ]
        else:
            header_lines = [
                "from pxr import UsdGeom, UsdLux, Gf, Sdf",
                "# stage must be provided by caller",
                "",
            ]

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

    _CLASS_NORMALIZER = {
        "cube": "Cube", "sphere": "Sphere", "cylinder": "Cylinder",
        "cone": "Cone", "plane": "Plane",
        "camera": "Camera", "xform": "Xform",
        "distant_light": "DistantLight", "distantlight": "DistantLight",
        "sphere_light": "SphereLight", "spherelight": "SphereLight",
        "dome_light": "DomeLight", "domelight": "DomeLight",
        "cube_small": "Cube", "cube_medium": "Cube", "cube_large": "Cube",
        "table_small": "Cube", "table_medium": "Cube", "table_large": "Cube",
        "bin": "Cube", "bin_large": "Cube", "shelf": "Cube",
        "conveyor_short": "Cube", "conveyor_long": "Cube",
        "wall": "Cube", "fence": "Cube", "obstacle_box": "Cube",
        "obstacle_cylinder": "Cylinder",
        "groundplane": "Plane",
        "camera_overhead": "Camera", "camera_side": "Camera",
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

    prims: List[Dict[str, Any]] = []
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

        position = _position3(position)
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

        prim_desc: Dict[str, Any] = {
            "prim_class": normalized_class,
            "prim_path": f"/World/{normalized_class}_{i + 1}",
            "position": position,
            "rotation_euler_deg": rotation,
            "scale": scale,
            "_source_class": str(obj_class),
        }
        if normalized_class == "Reference" and asset_ref:
            prim_desc["extra_attrs"] = {"asset_path": str(asset_ref)}
        prims.append(prim_desc)

    generator = LayoutSpecCodeGenerator(use_get_context=True)
    header_comment = (
        f"# Phase 19 code-gen: {len(prims)} prims, template_id={template_id!r}\n"
    )
    source_comments = "\n".join(
        f"# object[{i}]: source_class={p['_source_class']!r} -> "
        f"prim_class={p['prim_class']!r}, path={p['prim_path']!r}"
        for i, p in enumerate(prims)
    )
    for p in prims:
        p.pop("_source_class", None)
    body = generator.generate_full_script(prims)
    return header_comment + (source_comments + "\n" if source_comments else "") + body


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
        )

    # Live path
    try:
        from ..chat.tools import kit_tools
        result = await kit_tools.queue_exec_patch(code, description=f"Phase 19: instantiate {template_id or 'spec'}")
        return InstantiateResult.from_exec(result if isinstance(result, dict) else {"output": str(result)})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Phase19 instantiate] queue_exec_patch failed: {type(e).__name__}: {e}")
        return InstantiateResult(
            status="error",
            message=f"{type(e).__name__}: {e}",
            generated_code=code,
        )
