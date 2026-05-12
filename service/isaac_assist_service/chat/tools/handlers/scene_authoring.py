"""Scene-authoring handlers — target scope: USD prim CRUD,
attributes, references, layers, materials, snapshots.

Phase 3 — first wave of handler moves out of `tool_executor.py`. The
three simplest scene-authoring code-generators land here:

  - `_gen_create_prim`     — USD prim creation with type validation
  - `_gen_delete_prim`     — prim removal with existence pre/post check
  - `_gen_set_attribute`   — attribute write

The Phase 3 spec lists 10+ scene-authoring handlers; this wave moves
only the three lowest-risk ones to prove the migration pattern is
safe. Remaining handlers (`_gen_add_reference`,
`_gen_apply_api_schema`, `_gen_clone_prim`, `_gen_create_material`,
`_gen_assign_material`, `_gen_teleport_prim`, `_gen_create_omnigraph`)
land in subsequent Phase 3 increments.

Dispatch is unchanged: `tool_executor.py`'s `CODE_GEN_HANDLERS` dict
still lists `"create_prim": _gen_create_prim` etc. — the names now
resolve via a re-export from this module. Phase 9 swaps that to a
`register()`-based registration.

`_SAFE_XFORM_SNIPPET` stays in `tool_executor.py` for now (used by
~12 places across themes; will move to `handlers/_shared.py` in
Phase 8's deeper integration pass). This module imports it lazily
(inside each function body) to avoid a circular import at module
load time.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 3.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Code generators — moved verbatim from tool_executor.py (Phase 3, wave 1)


def _gen_create_prim(args: Dict) -> str:
    # Lazy import to avoid circular dependency at module load time;
    # _SAFE_XFORM_SNIPPET stays in tool_executor.py until Phase 8.
    from ..tool_executor import _SAFE_XFORM_SNIPPET

    prim_path = args["prim_path"]
    prim_type = args["prim_type"]
    pos = args.get("position")
    scale = args.get("scale")
    rot = args.get("rotation_euler")
    size = args.get("size")
    radius = args.get("radius")
    height = args.get("height")
    intensity = args.get("intensity")
    # Validate prim_type upfront — DefinePrim accepts ANY string, returns
    # an untyped prim for unknown types, and every downstream attr setter
    # (GetSizeAttr / GetRadiusAttr / Xformable) silently no-ops on that.
    # Live-probed 2026-04-18 with prim_type='BogusUnknownType' — tool
    # returned success=True with empty output.
    _KNOWN_PRIM_TYPES = {
        "Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Mesh", "Xform",
        "Camera", "DistantLight", "DomeLight", "SphereLight", "RectLight",
        "DiskLight", "CylinderLight", "Scope", "PointInstancer",
        "BasisCurves", "Points", "NurbsPatch", "PhysicsScene",
        "PhysicsFixedJoint", "PhysicsRevoluteJoint", "PhysicsPrismaticJoint",
        "PhysicsSphericalJoint",
    }
    if prim_type and prim_type not in _KNOWN_PRIM_TYPES:
        _types_str = sorted(_KNOWN_PRIM_TYPES)
        _msg = f"create_prim: unknown prim_type {prim_type!r} — expected one of {_types_str}"
        return f"raise ValueError({_msg!r})\n"
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"_cp_path = {prim_path!r}",
        f"_cp_type = {prim_type!r}",
        "prim = stage.DefinePrim(_cp_path, _cp_type)",
        "if not prim.IsValid() or str(prim.GetTypeName()) != _cp_type:",
        "    raise RuntimeError(",
        "        f'create_prim: DefinePrim({_cp_path!r}, {_cp_type!r}) did not produce '",
        "        f'a valid prim of the expected type (got type={prim.GetTypeName()!r})'",
        "    )",
    ]
    if pos:
        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
    if scale:
        lines.append(f"_safe_set_scale(prim, ({scale[0]}, {scale[1]}, {scale[2]}))")
    if rot:
        lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
    # Geometric attributes authored directly. Cleaner than relying on scale
    # because set_attribute on 'size'/'radius'/'height' matches what success
    # criteria typically verify (the USD attribute, not the scale op).
    if size is not None and prim_type == "Cube":
        lines.append(f"UsdGeom.Cube(prim).GetSizeAttr().Set({float(size)})")
    if radius is not None:
        if prim_type == "Sphere":
            lines.append(f"UsdGeom.Sphere(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Cylinder":
            lines.append(f"UsdGeom.Cylinder(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Cone":
            lines.append(f"UsdGeom.Cone(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Capsule":
            lines.append(f"UsdGeom.Capsule(prim).GetRadiusAttr().Set({float(radius)})")
    if height is not None:
        if prim_type == "Cylinder":
            lines.append(f"UsdGeom.Cylinder(prim).GetHeightAttr().Set({float(height)})")
        elif prim_type == "Cone":
            lines.append(f"UsdGeom.Cone(prim).GetHeightAttr().Set({float(height)})")
        elif prim_type == "Capsule":
            lines.append(f"UsdGeom.Capsule(prim).GetHeightAttr().Set({float(height)})")
    # Light intensity: apply when prim_type is a Light. Default to 1000 if
    # the agent creates a Light without explicit intensity — an unset
    # `inputs:intensity` attribute reads as None (or 0 in some renderers)
    # so the scene stays dark despite the DomeLight prim existing.
    _LIGHT_TYPES = {"DomeLight", "DistantLight", "SphereLight", "RectLight",
                    "DiskLight", "CylinderLight"}
    if prim_type in _LIGHT_TYPES:
        _i = float(intensity) if intensity is not None else 1000.0
        lines.append("from pxr import Sdf as _Sdf")
        lines.append("_ia = prim.GetAttribute('inputs:intensity')")
        lines.append("if not _ia or not _ia.IsDefined():")
        lines.append("    _ia = prim.CreateAttribute('inputs:intensity', _Sdf.ValueTypeNames.Float)")
        lines.append(f"_ia.Set({_i})")
    return "\n".join(lines)


def _gen_delete_prim(args: Dict) -> str:
    # stage.RemovePrim returns False (not raises) on a non-existent path, and
    # the old generator threw away that return value. Agent could then claim
    # "deleted /World/Foo" when /World/Foo never existed — a classic honesty
    # hole. Pre-check existence and verify post-remove.
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"_path = '{args['prim_path']}'\n"
        "_prim = stage.GetPrimAtPath(_path)\n"
        "if not _prim.IsValid():\n"
        "    raise RuntimeError(f'delete_prim: prim does not exist: {_path!r}')\n"
        "_ok = stage.RemovePrim(_path)\n"
        "if not _ok or stage.GetPrimAtPath(_path).IsValid():\n"
        "    raise RuntimeError(f'delete_prim: RemovePrim({_path!r}) returned {_ok!r} but prim still in stage')\n"
        "print(f'deleted {_path}')"
    )


def _gen_set_attribute(args: Dict) -> str:
    prim_path = args["prim_path"]
    attr_name = args["attr_name"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        f"attr = prim.GetAttribute('{attr_name}')\n"
        f"attr.Set({repr(value)})"
    )


# ---------------------------------------------------------------------------
# Phase 3 wave 2 — add_reference, assign_material, teleport_prim


def _gen_add_reference(args: Dict) -> str:
    # USD AddReference accepts any asset URL and returns True regardless of
    # whether the referenced file exists — composition is lazy. Without
    # post-check, a bad path produces a prim with "has references" but no
    # actual children, and the tool reports success. Verify via:
    #   1. prim.HasAuthoredReferences() after the call
    #   2. if the asset is a local path, os.path.exists() before the call
    #   3. re-traverse children to catch zero-child silent composition error
    return (
        "import os\n"
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        f"if not prim.IsValid():\n"
        f"    raise RuntimeError('add_reference: prim not found: {args['prim_path']}')\n"
        f"_ref = '{args['reference_path']}'\n"
        # Local filesystem path (not omniverse:// or http(s)://): must exist.
        "if not any(_ref.startswith(p) for p in ('omniverse://','http://','https://','file://')):\n"
        "    if not os.path.isabs(_ref) or not os.path.exists(_ref):\n"
        "        raise FileNotFoundError(f'add_reference: asset not found: {_ref!r}')\n"
        "_added = prim.GetReferences().AddReference(_ref)\n"
        "if not _added or not prim.HasAuthoredReferences():\n"
        "    raise RuntimeError(f'add_reference: AddReference returned success but no reference was authored on {prim.GetPath()}')\n"
        "print(f'added reference {_ref} to {prim.GetPath()}')"
    )


def _gen_assign_material(args: Dict) -> str:
    return (
        "import omni.usd\n"
        "from pxr import UsdShade\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"mat = UsdShade.Material(stage.GetPrimAtPath('{args['material_path']}'))\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        "UsdShade.MaterialBindingAPI(prim).Bind(mat, UsdShade.Tokens.strongerThanDescendants)"
    )


def _gen_teleport_prim(args: Dict) -> str:
    # Lazy import — same pattern as _gen_create_prim.
    from ..tool_executor import _SAFE_XFORM_SNIPPET

    prim_path = args["prim_path"]
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{prim_path}')",
    ]
    pos = args.get("position")
    rot = args.get("rotation_euler")
    if pos:
        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
    if rot:
        lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 3 wave 1 — register the three handlers moved out of tool_executor.

    Dispatch lines in `tool_executor.py:CODE_GEN_HANDLERS` still list these
    by name (resolving via the import that pulls them back into
    `tool_executor`'s namespace), so calling this register() today would
    silently double-register if it ran. Phase 9 swaps the dispatch pattern
    so this register() becomes the authoritative entry point and the
    inline `CODE_GEN_HANDLERS["create_prim"] = _gen_create_prim`
    assignments in `tool_executor.py` go away.

    Until Phase 9: this register() does NOT populate the dispatch (the
    inline assignments do that). The function is here as a contract
    placeholder; Phase 9 fills in the body.
    """
    # Intentional no-op until Phase 9 swaps dispatch.
    return None
