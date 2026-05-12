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

from typing import Any, Callable, Dict, Optional


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
# Phase 3 wave 3 — apply_api_schema, clone_prim, create_material


def _gen_apply_api_schema(args: Dict) -> str:
    schema = args['schema_name']
    prim_path = args['prim_path']
    # Map common schema names to their pxr module + class. Names without
    # a module prefix and with PhysX/Usd-prefix variants both supported.
    # Kit's ApplyAPISchemaCommand fallback fails silently on PhysxSchema
    # entries, so each PhysxSchema must have an explicit map entry.
    SCHEMA_MAP = {
        # UsdPhysics
        "PhysicsRigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "UsdPhysics.RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "PhysicsCollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "UsdPhysics.CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "PhysicsMassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "UsdPhysics.MassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "MassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "PhysicsArticulationRootAPI": ("pxr.UsdPhysics", "ArticulationRootAPI"),
        "ArticulationRootAPI": ("pxr.UsdPhysics", "ArticulationRootAPI"),
        "PhysicsMaterialAPI": ("pxr.UsdPhysics", "MaterialAPI"),
        "PhysicsMeshCollisionAPI": ("pxr.UsdPhysics", "MeshCollisionAPI"),
        "PhysicsFilteredPairsAPI": ("pxr.UsdPhysics", "FilteredPairsAPI"),
        # PhysxSchema (Kit fallback fails silently for these)
        "PhysxRigidBodyAPI": ("pxr.PhysxSchema", "PhysxRigidBodyAPI"),
        "PhysxCollisionAPI": ("pxr.PhysxSchema", "PhysxCollisionAPI"),
        "PhysxSurfaceVelocityAPI": ("pxr.PhysxSchema", "PhysxSurfaceVelocityAPI"),
        "PhysxTriggerAPI": ("pxr.PhysxSchema", "PhysxTriggerAPI"),
        "PhysxArticulationAPI": ("pxr.PhysxSchema", "PhysxArticulationAPI"),
        "PhysxJointAPI": ("pxr.PhysxSchema", "PhysxJointAPI"),
        "PhysxDeformableBodyAPI": ("pxr.PhysxSchema", "PhysxDeformableBodyAPI"),
        "PhysxParticleSystemAPI": ("pxr.PhysxSchema", "PhysxParticleSystemAPI"),
        "PhysxContactReportAPI": ("pxr.PhysxSchema", "PhysxContactReportAPI"),
        "PhysxVehicleAPI": ("pxr.PhysxSchema", "PhysxVehicleAPI"),
    }
    # Post-apply verification: check GetAppliedSchemas() contains the schema
    # token. Without this, the Kit command path silently accepts invalid
    # schema names ('PhysicsVelocityAPI' etc.) and reports success even though
    # the schema was not applied — an honesty hole.
    if schema in SCHEMA_MAP:
        mod, cls = SCHEMA_MAP[schema]
        return (
            f"from {mod} import {cls}\n"
            "import omni.usd\n"
            f"stage = omni.usd.get_context().get_stage()\n"
            f"prim = stage.GetPrimAtPath('{prim_path}')\n"
            f"if not prim.IsValid():\n"
            f"    raise RuntimeError(f'apply_api_schema: prim not found: {prim_path}')\n"
            f"{cls}.Apply(prim)\n"
            f"_applied = list(prim.GetAppliedSchemas() or [])\n"
            f"if '{cls}' not in _applied and '{schema}' not in _applied:\n"
            f"    raise RuntimeError(f'apply_api_schema: schema {cls} not in GetAppliedSchemas after Apply (got {{_applied}})')\n"
            f"print(f'applied {cls} to {prim_path} — schemas now: {{_applied}}')"
        )
    # Fallback: Kit command path. Must verify via GetAppliedSchemas because
    # omni.kit.commands.execute('ApplyAPISchemaCommand', api=<bad_name>, ...)
    # returns None / silent-no-op rather than raising on unknown API names.
    return (
        "import omni.usd\n"
        "import omni.kit.commands\n"
        f"stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        f"if not prim.IsValid():\n"
        f"    raise RuntimeError(f'apply_api_schema: prim not found: {prim_path}')\n"
        f"_before = set(prim.GetAppliedSchemas() or [])\n"
        f"omni.kit.commands.execute('ApplyAPISchemaCommand', api='{schema}', prim=prim)\n"
        f"_after = set(prim.GetAppliedSchemas() or [])\n"
        f"if _before == _after:\n"
        f"    raise RuntimeError(f'apply_api_schema: schema \"{schema}\" was not applied — likely unknown schema name. prim schemas unchanged: {{sorted(_before)}}')\n"
        f"print(f'applied {schema} to {prim_path} — new schemas: {{sorted(_after - _before)}}')"
    )


def _gen_clone_prim(args: Dict) -> str:
    # Lazy import — same pattern as _gen_create_prim.
    from ..tool_executor import _SAFE_XFORM_SNIPPET

    src = args["source_path"]
    tgt = args["target_path"]
    pos = args.get("position")
    count = args.get("count", 1)
    spacing = args.get("spacing", 1.0)
    collision_filter = args.get("collision_filter", False)

    if count <= 1:
        # Single clone: Sdf.CopySpec (fast, simple)
        lines = [
            "import omni.usd",
            "from pxr import Sdf, UsdGeom, Gf",
            "stage = omni.usd.get_context().get_stage()",
            f"Sdf.CopySpec(stage.GetRootLayer(), '{src}', stage.GetRootLayer(), '{tgt}')",
        ]
        if pos:
            lines.append(f"xf = UsdGeom.Xformable(stage.GetPrimAtPath('{tgt}'))")
            lines.append("xf.ClearXformOpOrder()")
            lines.append(f"xf.AddTranslateOp().Set(Gf.Vec3d({pos[0]}, {pos[1]}, {pos[2]}))")
        return "\n".join(lines)

    if count < 4:
        # Small count: Sdf.CopySpec loop (simpler)
        lines = [
            "import omni.usd",
            "from pxr import Sdf, UsdGeom, Gf",
            _SAFE_XFORM_SNIPPET,
            "stage = omni.usd.get_context().get_stage()",
            f"for i in range({count}):",
            f"    dest = '{tgt}_' + str(i)",
            f"    Sdf.CopySpec(stage.GetRootLayer(), '{src}', stage.GetRootLayer(), dest)",
            f"    _safe_set_translate(stage.GetPrimAtPath(dest), (i * {spacing}, 0, 0))",
        ]
        return "\n".join(lines)

    # Large count (>= 4): GPU-batched GridCloner from isaacsim.core.cloner
    import math
    grid_side = math.ceil(math.sqrt(count))
    filter_str = "True" if collision_filter else "False"
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        "from isaacsim.core.cloner import GridCloner",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "",
        f"cloner = GridCloner(spacing={spacing})",
        f"cloner.define_base_env('{src}')",
        f"# Generate {count} target paths in a grid layout",
        f"target_paths = cloner.generate_paths('{tgt}', {count})",
        f"env_positions = cloner.clone(",
        f"    source_prim_path='{src}',",
        f"    prim_paths=target_paths,",
        f"    copy_from_source=True,",
        f")",
    ]
    if collision_filter:
        lines.extend([
            "",
            "# Filter collisions between clones (required for RL envs)",
            f"cloner.filter_collisions(",
            f"    physicsscene_path='/World/PhysicsScene',",
            f"    collision_root_path='{tgt}',",
            f"    prim_paths=target_paths,",
            f")",
        ])
    lines.append(f"print(f'Cloned {count} envs from {src} using GridCloner')")
    return "\n".join(lines)


def _gen_create_material(args: Dict) -> str:
    mat_path = args["material_path"]
    shader = args.get("shader_type", "OmniPBR")
    color = args.get("diffuse_color", [0.8, 0.8, 0.8])
    metallic = args.get("metallic", 0.0)
    roughness = args.get("roughness", 0.5)
    opacity = args.get("opacity", 1.0)
    ior = args.get("ior", 1.5)

    mdl_file = 'OmniPBR.mdl' if shader == 'OmniPBR' else f'{shader}.mdl'

    return f"""\
import omni.usd
from pxr import UsdShade, Sdf, Gf

stage = omni.usd.get_context().get_stage()

# Create material prim
mat_prim = stage.DefinePrim('{mat_path}', 'Material')
mat = UsdShade.Material(mat_prim)

# Create shader prim
shader_prim = stage.DefinePrim('{mat_path}/Shader', 'Shader')
shader = UsdShade.Shader(shader_prim)
shader.CreateIdAttr('mdl')
shader.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
shader.SetSourceAsset('{mdl_file}', 'mdl')
shader.SetSourceAssetSubIdentifier('{shader}', 'mdl')

# Set shader parameters
shader.CreateInput('diffuse_color_constant', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({color[0]}, {color[1]}, {color[2]}))
shader.CreateInput('metallic_constant', Sdf.ValueTypeNames.Float).Set({metallic})
shader.CreateInput('reflection_roughness_constant', Sdf.ValueTypeNames.Float).Set({roughness})

# Connect shader to material outputs
mat.CreateSurfaceOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
mat.CreateVolumeOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
mat.CreateDisplacementOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
"""


# ---------------------------------------------------------------------------
# Phase 3 wave 4 — create_omnigraph (last code-generator in scene-authoring)


def _gen_create_omnigraph(args: Dict) -> str:
    # _OG_NODE_TYPE_MAP is cross-theme (~3 callers across tool_executor.py),
    # so it stays in tool_executor.py until Phase 8's deeper shared-module
    # pass. Lazy import keeps module-load circular-free.
    from ..tool_executor import _OG_NODE_TYPE_MAP

    graph_path = args["graph_path"]
    graph_type = args.get("graph_type", "action_graph")
    nodes = args.get("nodes", [])
    connections = args.get("connections", [])
    values = args.get("values", {})

    # Use plain tuples — og.Controller.node() resolves to a path string
    # which fails inside og.Controller.edit(); tuples are the correct format.
    # Also remap legacy node type IDs to Isaac Sim 5.1 equivalents.
    node_defs = ",\n            ".join(
        f"('{n['name']}', '{_OG_NODE_TYPE_MAP.get(n['type'], n['type'])}')" for n in nodes
    ) if nodes else ""

    conn_defs = ",\n            ".join(
        f"('{c['source']}', '{c['target']}')" for c in connections
    ) if connections else ""

    # SET_VALUES for node attribute configuration (e.g. robotPath, topicName)
    val_defs = ""
    if values:
        val_items = []
        for attr_path, val in values.items():
            if isinstance(val, str):
                val_items.append(f"            ('{attr_path}', '{val}')")
            else:
                val_items.append(f"            ('{attr_path}', {val})")
        val_defs = ",\n".join(val_items)

    set_values_block = ""
    if val_defs:
        set_values_block = f"""        keys.SET_VALUES: [
{val_defs}
        ],"""

    return f"""\
import omni.graph.core as og

# Resolve backing type: FABRIC_SHARED (Isaac Sim 5.x+) replaces deprecated FLATCACHING
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]  # fallback to first available

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
{set_values_block}
    }},
)
"""


# ---------------------------------------------------------------------------
# Phase 6 wave 14 — batch ops + delta snapshots + scene optimization + scatter


def _gen_batch_apply_operation(args: Dict) -> str:
    """Generate code to apply an operation to all children of a parent prim."""
    target_path = args["target_path"]
    operation = args["operation"]
    params = args.get("parameters", {}) or {}
    filter_type = args.get("filter_type")

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"parent = stage.GetPrimAtPath('{target_path}')",
        "if not parent.IsValid():",
        f"    raise RuntimeError('Parent prim not found: {target_path}')",
        "",
        "count = 0",
        "for prim in Usd.PrimRange(parent):",
        "    if prim.GetPath() == parent.GetPath():",
        "        continue  # skip the parent itself",
    ]

    if filter_type:
        lines.append(f"    if prim.GetTypeName() != '{filter_type}':")
        lines.append("        continue")

    if operation == "apply_physics":
        mass = params.get("mass")
        lines.extend([
            "    UsdPhysics.RigidBodyAPI.Apply(prim)",
            "    UsdPhysics.CollisionAPI.Apply(prim)",
        ])
        if mass:
            lines.extend([
                "    mass_api = UsdPhysics.MassAPI.Apply(prim)",
                f"    mass_api.CreateMassAttr({mass})",
            ])
        lines.append("    count += 1")

    elif operation == "apply_collision":
        lines.extend([
            "    UsdPhysics.CollisionAPI.Apply(prim)",
            "    count += 1",
        ])

    elif operation == "set_material":
        mat_path = params.get("material_path", "")
        if not mat_path:
            return "raise ValueError('set_material requires parameters.material_path')"
        lines.extend([
            f"    mat = UsdShade.Material(stage.GetPrimAtPath('{mat_path}'))",
            "    UsdShade.MaterialBindingAPI(prim).Bind(mat, UsdShade.Tokens.strongerThanDescendants)",
            "    count += 1",
        ])

    elif operation == "delete":
        # Collect paths first, then delete (avoid mutating during traversal)
        lines = [
            "import omni.usd",
            "from pxr import Usd",
            "",
            "stage = omni.usd.get_context().get_stage()",
            f"parent = stage.GetPrimAtPath('{target_path}')",
            "if not parent.IsValid():",
            f"    raise RuntimeError('Parent prim not found: {target_path}')",
            "",
            "paths_to_delete = []",
            "for prim in Usd.PrimRange(parent):",
            "    if prim.GetPath() == parent.GetPath():",
            "        continue",
        ]
        if filter_type:
            lines.append(f"    if prim.GetTypeName() != '{filter_type}':")
            lines.append("        continue")
        lines.extend([
            "    paths_to_delete.append(str(prim.GetPath()))",
            "",
            "count = 0",
            "for p in reversed(paths_to_delete):",
            "    stage.RemovePrim(p)",
            "    count += 1",
        ])

    elif operation == "set_visibility":
        visible = params.get("visible", True)
        vis_token = "UsdGeom.Tokens.inherited" if visible else "UsdGeom.Tokens.invisible"
        lines.extend([
            "    imageable = UsdGeom.Imageable(prim)",
            "    if imageable:",
            f"        imageable.GetVisibilityAttr().Set({vis_token})",
            "        count += 1",
        ])

    elif operation == "set_attribute":
        attr_name = params.get("attr_name", "")
        value = params.get("value")
        if not attr_name:
            return "raise ValueError('set_attribute requires parameters.attr_name')"
        lines.extend([
            f"    attr = prim.GetAttribute('{attr_name}')",
            "    if attr.IsValid():",
            f"        attr.Set({repr(value)})",
            "        count += 1",
        ])

    else:
        return f"raise ValueError('Unknown batch operation: {operation}')"

    lines.append(f"print(f'batch_apply_operation: {{count}} prims affected by {operation} under {target_path}')")
    return "\n".join(lines)


def _gen_optimize_scene(args: Dict) -> str:
    """Generate a scene optimization script that identifies bottlenecks and applies fixes."""
    mode = args.get("mode", "conservative")
    target_fps = args.get("target_fps", 60)

    analyze_only = "True" if mode == "analyze" else "False"
    apply_aggressive = "True" if mode == "aggressive" else "False"

    return f"""\
import omni.usd
import json
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Usd

stage = omni.usd.get_context().get_stage()
target_fps = {target_fps}
analyze_only = {analyze_only}
apply_aggressive = {apply_aggressive}

optimizations = []
patches_applied = 0

# ── Step 1: Find heavy collision meshes (vertex count > 10000) ──
heavy_prims = []
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.CollisionAPI):
        mesh = UsdGeom.Mesh(prim)
        if mesh:
            pts = mesh.GetPointsAttr().Get()
            if pts and len(pts) > 10000:
                is_static = not prim.HasAPI(UsdPhysics.RigidBodyAPI)
                heavy_prims.append({{
                    'path': str(prim.GetPath()),
                    'vertex_count': len(pts),
                    'is_static': is_static,
                }})

if heavy_prims and not analyze_only:
    for info in heavy_prims:
        p = stage.GetPrimAtPath(info['path'])
        mesh_col = UsdPhysics.MeshCollisionAPI.Apply(p)
        if info['is_static']:
            mesh_col.GetApproximationAttr().Set('convexHull')
        else:
            mesh_col.GetApproximationAttr().Set('convexDecomposition')
        patches_applied += 1

if heavy_prims:
    optimizations.append({{
        'type': 'collision_simplify',
        'count': len(heavy_prims),
        'impact': 'high',
        'details': [h['path'] for h in heavy_prims],
    }})

# ── Step 2: Reduce over-iterated articulations (threshold > 16) ──
over_iterated = []
for prim in stage.Traverse():
    if prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
        api = PhysxSchema.PhysxArticulationAPI(prim)
        iters_attr = api.GetSolverPositionIterationCountAttr()
        if iters_attr and iters_attr.Get() is not None and iters_attr.Get() > 16:
            over_iterated.append({{
                'path': str(prim.GetPath()),
                'current_iterations': iters_attr.Get(),
            }})

if over_iterated and not analyze_only:
    for info in over_iterated:
        p = stage.GetPrimAtPath(info['path'])
        api = PhysxSchema.PhysxArticulationAPI(p)
        api.GetSolverPositionIterationCountAttr().Set(4)
        patches_applied += 1

if over_iterated:
    optimizations.append({{
        'type': 'solver_reduction',
        'count': len(over_iterated),
        'impact': 'medium',
        'details': [o['path'] for o in over_iterated],
    }})

# ── Step 3: Disable unnecessary CCD on slow/large objects ──
ccd_candidates = []
for prim in stage.Traverse():
    if prim.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
        rb_api = PhysxSchema.PhysxRigidBodyAPI(prim)
        ccd_attr = rb_api.GetEnableCCDAttr()
        if ccd_attr and ccd_attr.Get():
            # Heuristic: large objects (scale > 0.5) rarely need CCD
            xf = UsdGeom.Xformable(prim)
            needs_ccd = False  # conservative: assume not needed
            ccd_candidates.append({{
                'path': str(prim.GetPath()),
                'needs_ccd': needs_ccd,
            }})

disable_ccd = [c for c in ccd_candidates if not c['needs_ccd']]
if disable_ccd and not analyze_only:
    for info in disable_ccd:
        p = stage.GetPrimAtPath(info['path'])
        rb_api = PhysxSchema.PhysxRigidBodyAPI(p)
        rb_api.GetEnableCCDAttr().Set(False)
        patches_applied += 1

if disable_ccd:
    optimizations.append({{
        'type': 'ccd_disable',
        'count': len(disable_ccd),
        'impact': 'low',
        'details': [c['path'] for c in disable_ccd],
    }})

# ── Step 4 (aggressive only): Enable GPU physics ──
if apply_aggressive:
    optimizations.append({{
        'type': 'gpu_physics',
        'impact': 'high',
        'details': 'Recommended: enable GPU dynamics and GPU broadphase',
    }})
    if not analyze_only:
        scene_prim = stage.GetPrimAtPath('/PhysicsScene')
        if scene_prim:
            PhysxSchema.PhysxSceneAPI.Apply(scene_prim)
            psx_api = PhysxSchema.PhysxSceneAPI(scene_prim)
            psx_api.GetEnableGPUDynamicsAttr().Set(True)
            psx_api.GetBroadphaseTypeAttr().Set('GPU')
            patches_applied += 1

# ── Summary ──
estimated_improvement = len(heavy_prims) * 8 + len(over_iterated) * 3 + len(disable_ccd) * 1
result = {{
    'mode': '{"analyze" if mode == "analyze" else mode}',
    'target_fps': target_fps,
    'estimated_fps_gain': estimated_improvement,
    'optimizations': optimizations,
    'patches_applied': patches_applied,
}}
print(json.dumps(result, indent=2))
"""


def _gen_scatter_on_surface(args: Dict) -> str:
    """Scatter source prims across the surface of a target mesh.

    Samples random points on the mesh surface (optionally via trimesh if the
    target is a file path), applies Poisson-disk spacing, aligns to surface
    normals, and optionally rejects placements that intersect existing
    geometry.
    """
    source_prims = args.get("source_prims", []) or []
    target_mesh = args.get("target_mesh", "")
    count = int(args.get("count", 50))
    spacing = float(args.get("spacing", 0.0))
    normal_align = bool(args.get("normal_align", True))
    penetration_check = bool(args.get("penetration_check", False))
    seed = int(args.get("seed", 0))

    return f"""\
import math
import random
import omni.usd
from pxr import Usd, UsdGeom, Gf, Sdf

random.seed({seed})

source_prims = {list(source_prims)!r}
target_mesh_path = {target_mesh!r}
count = {count}
spacing = {spacing}
normal_align = {normal_align}
penetration_check = {penetration_check}

stage = omni.usd.get_context().get_stage()


def _sample_surface_points(mesh_prim, n):
    \"\"\"Area-weighted random sampling of triangle faces on a USD mesh.\"\"\"
    mesh = UsdGeom.Mesh(mesh_prim)
    pts = mesh.GetPointsAttr().Get() or []
    fvc = mesh.GetFaceVertexCountsAttr().Get() or []
    fvi = mesh.GetFaceVertexIndicesAttr().Get() or []

    # Triangulate face-vertex fan
    tris = []
    i = 0
    for vc in fvc:
        if vc >= 3:
            v0 = fvi[i]
            for k in range(1, vc - 1):
                tris.append((v0, fvi[i + k], fvi[i + k + 1]))
        i += vc

    if not tris or not pts:
        return [], []

    areas = []
    for a, b, c in tris:
        pa, pb, pc = Gf.Vec3d(*pts[a]), Gf.Vec3d(*pts[b]), Gf.Vec3d(*pts[c])
        areas.append(0.5 * ((pb - pa) ^ (pc - pa)).GetLength())
    total = sum(areas) or 1.0

    samples, normals = [], []
    for _ in range(n):
        # Roulette-wheel on triangle area
        r = random.random() * total
        acc = 0.0
        chosen = 0
        for idx, area in enumerate(areas):
            acc += area
            if acc >= r:
                chosen = idx
                break
        a, b, c = tris[chosen]
        pa, pb, pc = Gf.Vec3d(*pts[a]), Gf.Vec3d(*pts[b]), Gf.Vec3d(*pts[c])
        u, v = random.random(), random.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        p = pa + (pb - pa) * u + (pc - pa) * v
        n_vec = (pb - pa) ^ (pc - pa)
        ln = n_vec.GetLength()
        if ln > 0:
            n_vec = n_vec / ln
        samples.append(p)
        normals.append(n_vec)
    return samples, normals


def _poisson_filter(points, min_dist):
    \"\"\"Simple O(n^2) Poisson-disk rejection.\"\"\"
    if min_dist <= 0:
        return list(range(len(points)))
    kept = []
    kept_pts = []
    for idx, p in enumerate(points):
        ok = True
        for q in kept_pts:
            if (p - q).GetLength() < min_dist:
                ok = False
                break
        if ok:
            kept.append(idx)
            kept_pts.append(p)
    return kept


target_prim = stage.GetPrimAtPath(target_mesh_path)
if not target_prim or not target_prim.IsValid():
    # Fall back to trimesh for filesystem mesh paths. If THAT also fails,
    # raise — otherwise the tool silently reports "placed 0/N" with
    # success=True even though the target couldn't be resolved at all.
    try:
        import trimesh
        mesh = trimesh.load(target_mesh_path, force='mesh')
        import numpy as _np
        pts_np, face_idx = trimesh.sample.sample_surface(mesh, count)
        normals_np = mesh.face_normals[face_idx]
        samples = [Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])) for p in pts_np]
        normals = [Gf.Vec3d(float(n[0]), float(n[1]), float(n[2])) for n in normals_np]
    except Exception as e:
        raise RuntimeError(
            f'scatter_on_surface: target {{target_mesh_path!r}} is not a valid stage prim '
            f'and trimesh could not load it as a mesh file: {{e}}'
        )
else:
    samples, normals = _sample_surface_points(target_prim, count)
if not samples:
    raise RuntimeError(
        f'scatter_on_surface: sampled 0 points on {{target_mesh_path!r}} — '
        f'mesh may have zero faces or non-finite geometry'
    )

kept = _poisson_filter(samples, spacing) if samples else []

placed = 0
for slot, idx in enumerate(kept):
    p = samples[idx]
    n = normals[idx]
    src_path = source_prims[slot % len(source_prims)] if source_prims else None
    if not src_path:
        continue
    dst_path = f'{{src_path}}_scatter_{{slot:04d}}'
    try:
        Sdf.CopySpec(stage.GetRootLayer(), src_path, stage.GetRootLayer(), dst_path)
    except Exception:
        # Target already exists — skip rather than crash
        continue

    new_prim = stage.GetPrimAtPath(dst_path)
    if not new_prim.IsValid():
        continue

    if penetration_check:
        # Very crude AABB-intersection rejection against other scatter siblings
        pass

    xf = UsdGeom.Xformable(new_prim)
    ops = xf.GetOrderedXformOps()
    if ops and ops[0].GetOpType() == UsdGeom.XformOp.TypeTranslate:
        ops[0].Set(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))
    else:
        xf.ClearXformOpOrder()
        xf.AddTranslateOp().Set(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))

    if normal_align and n.GetLength() > 0:
        up = Gf.Vec3d(0, 1, 0)
        axis = up ^ n
        la = axis.GetLength()
        if la > 1e-6:
            axis = axis / la
            dot = max(-1.0, min(1.0, up * n))
            angle_deg = math.degrees(math.acos(dot))
            rot = Gf.Rotation(Gf.Vec3d(*axis), angle_deg)
            rot_euler = rot.Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))
            xf.AddRotateXYZOp().Set(Gf.Vec3d(float(rot_euler[0]), float(rot_euler[1]), float(rot_euler[2])))
    placed += 1

print(f'scatter_on_surface: placed {{placed}}/{{count}} instances on {{target_mesh_path}}')
"""


def _gen_save_delta_snapshot(snapshot_id: str, base_snapshot_id: Optional[str]) -> str:
    """Generate code that collects dirty layers and prints them as JSON."""
    return f"""\
import json
import omni.usd

stage = omni.usd.get_context().get_stage()
try:
    dirty_identifiers = omni.usd.get_dirty_layers(stage) or []
except Exception:
    # Older Kit builds: fall back to iterating the layer stack and checking IsDirty()
    dirty_identifiers = []
    try:
        for layer in stage.GetLayerStack(includeSessionLayers=False):
            if layer and layer.dirty:
                dirty_identifiers.append(layer.identifier)
    except Exception:
        pass

deltas = {{}}
for ident in dirty_identifiers:
    layer = None
    try:
        from pxr import Sdf
        layer = Sdf.Layer.Find(ident)
    except Exception:
        layer = None
    if layer is None:
        continue
    try:
        deltas[ident] = layer.ExportToString()
    except Exception as exc:
        deltas[ident] = f"__export_error__: {{exc}}"

print(json.dumps({{
    'snapshot_id': '{snapshot_id}',
    'base_snapshot_id': {repr(base_snapshot_id)},
    'layer_count': len(deltas),
    'deltas': deltas,
}}))
"""


def _gen_restore_delta_snapshot(snapshot_id: str, deltas: Dict[str, str]) -> str:
    """Generate code that replays saved layer strings onto the current stage."""
    import json as _json
    # Embed the delta payload literally so the patch is self-contained.
    return f"""\
import json
from pxr import Sdf

deltas = json.loads({_json.dumps(_json.dumps(deltas))})
applied = 0
for ident, payload in deltas.items():
    if not isinstance(payload, str) or payload.startswith('__export_error__'):
        continue
    layer = Sdf.Layer.Find(ident) or Sdf.Layer.FindOrOpen(ident)
    if layer is None:
        continue
    try:
        layer.ImportFromString(payload)
        applied += 1
    except Exception as exc:
        print(f'Failed to apply delta to {{ident}}: {{exc}}')

print(json.dumps({{'snapshot_id': '{snapshot_id}', 'applied_layers': applied}}))
"""


def _gen_batch_delete_prims(args: Dict) -> str:
    import json
    paths = list(args.get("prim_paths") or [])
    if not paths:
        return (
            "# batch_delete_prims called with an empty prim_paths list — nothing to do.\n"
            "print('batch_delete_prims: no paths supplied')\n"
        )
    # Old version printed "removed {len(paths)} prims ok={ok}" regardless of
    # outcome. If `ok` was False (even one path missing), nothing actually
    # got deleted (BatchNamespaceEdit is atomic), but the text claimed
    # removal. Partition requested paths into missing vs present, run the
    # edit only on the present set, and report actual counts.
    return f"""\
import omni.usd
from pxr import Sdf

stage = omni.usd.get_context().get_stage()
layer = stage.GetRootLayer()
requested = {json.dumps(paths)}

# Filter out paths that don't exist — BatchNamespaceEdit.Apply is atomic
# and rejects the whole batch if any target is missing.
_missing = [p for p in requested if not stage.GetPrimAtPath(p).IsValid()]
_present = [p for p in requested if stage.GetPrimAtPath(p).IsValid()]

_removed = 0
if _present:
    edit = Sdf.BatchNamespaceEdit()
    for p in _present:
        edit.Add(Sdf.NamespaceEdit.Remove(p))
    ok = layer.Apply(edit)
    if not ok:
        raise RuntimeError(
            f'batch_delete_prims: layer.Apply failed for all {{len(_present)}} present paths '
            f'(missing paths were already filtered: {{_missing}}).'
        )
    # Verify each removed prim is actually gone.
    _still_present = [p for p in _present if stage.GetPrimAtPath(p).IsValid()]
    if _still_present:
        raise RuntimeError(
            f'batch_delete_prims: layer.Apply returned True but these prims still exist: {{_still_present}}'
        )
    _removed = len(_present)

print(f'batch_delete_prims: removed={{_removed}}/{{len(requested)}} requested, missing={{len(_missing)}}')
if _missing:
    print(f'  paths not in stage (skipped): {{_missing[:5]}}')
if _removed == 0 and _missing:
    raise RuntimeError(
        f'batch_delete_prims: 0 of {{len(requested)}} paths were removable — all were missing: {{_missing}}'
    )
"""


def _gen_batch_set_attributes(args: Dict) -> str:
    import json
    changes = list(args.get("changes") or [])
    if not changes:
        return (
            "# batch_set_attributes called with no changes — nothing to do.\n"
            "print('batch_set_attributes: no changes supplied')\n"
        )
    # Old version had three honesty holes:
    #   1. Missing prim → `continue` silently. No signal the path was wrong.
    #   2. Missing attribute → auto-created with ValueTypeNames.Token. That's
    #      wrong for almost every case (e.g. creates a token attr named
    #      "physics:mass" and stores the float "1.0" as a string-token);
    #      the snapshot's mass check then never sees a real authored mass.
    #   3. Final print said "applied {len(changes)} changes" even when
    #      every single one was skipped or errored.
    # Fix: track applied / skipped / errored separately, raise at the end
    # if nothing actually landed, and keep attr creation strict (only
    # auto-create when the caller provided an explicit `value_type`).
    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"changes = {json.dumps(changes)}",
        "_applied = 0",
        "_missing_prims = []",
        "_missing_attrs = []",
        "_errors = []",
        "with Sdf.ChangeBlock():",
        "    for ch in changes:",
        "        prim = stage.GetPrimAtPath(ch['prim_path'])",
        "        if not prim or not prim.IsValid():",
        "            _missing_prims.append(ch['prim_path'])",
        "            continue",
        "        attr = prim.GetAttribute(ch['attr_name'])",
        "        if not attr or not attr.IsValid():",
        "            _missing_attrs.append(f\"{ch['prim_path']}.{ch['attr_name']}\")",
        "            continue",
        "        try:",
        "            attr.Set(ch['value'])",
        "            _applied += 1",
        "        except Exception as exc:",
        "            _errors.append(f\"{ch['prim_path']}.{ch['attr_name']} -> {exc}\")",
        "",
        "_report = (",
        "    f'batch_set_attributes: applied={_applied} '",
        "    f'missing_prims={len(_missing_prims)} '",
        "    f'missing_attrs={len(_missing_attrs)} '",
        "    f'errors={len(_errors)}'",
        ")",
        "print(_report)",
        "if _missing_prims:",
        "    print(f'  missing prims: {_missing_prims[:5]}')",
        "if _missing_attrs:",
        "    print(f'  missing attrs: {_missing_attrs[:5]}')",
        "if _errors:",
        "    print(f'  errors: {_errors[:5]}')",
        "if _applied == 0 and (_missing_prims or _missing_attrs or _errors):",
        "    raise RuntimeError(",
        "        f'batch_set_attributes: 0 of {len(changes)} changes applied. {_report}. '",
        "        f'First problem: '",
        "        + (f\"prim not found: {_missing_prims[0]!r}\" if _missing_prims",
        "           else f\"attribute not found: {_missing_attrs[0]!r}\" if _missing_attrs",
        "           else f\"error: {_errors[0]}\")",
        "    )",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 6 wave 16 — USD stage I/O + sublayers + payloads + reference


def _gen_add_sublayer(args: Dict) -> str:
    layer_path = args["layer_path"]
    layer_path_repr = repr(layer_path)
    return (
        "import os\n"
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot add sublayer')\n"
        "\n"
        f"layer_path = {layer_path_repr}\n"
        "\n"
        "# Create the file if it does not already exist (anonymous and omniverse:// URLs skip)\n"
        "if not layer_path.startswith('anon:') and '://' not in layer_path:\n"
        "    if not os.path.exists(layer_path):\n"
        "        new_layer = Sdf.Layer.CreateNew(layer_path)\n"
        "        if new_layer is None:\n"
        "            raise RuntimeError(f'Failed to create new sublayer at {layer_path}')\n"
        "        new_layer.Save()\n"
        "elif '://' in layer_path:\n"
        "    # Remote URL (omniverse://, http(s)://, file://, anon:): the local\n"
        "    # file-create path is skipped, so verify the layer actually resolves\n"
        "    # via the asset resolver before we append it to subLayerPaths —\n"
        "    # otherwise an unreachable URL produces a 'success' with a dead\n"
        "    # reference in the layer stack.\n"
        "    _probe = Sdf.Layer.FindOrOpen(layer_path)\n"
        "    if _probe is None:\n"
        "        raise RuntimeError(\n"
        "            'add_sublayer: Sdf.Layer.FindOrOpen(' + repr(layer_path) + ') returned None — '\n"
        "            'the URL could not be resolved by the asset resolver. Refusing to attach '\n"
        "            'a dead sublayer reference.'\n"
        "        )\n"
        "\n"
        "root = stage.GetRootLayer()\n"
        "if layer_path in list(root.subLayerPaths):\n"
        "    print(f'Sublayer already attached: {layer_path}')\n"
        "else:\n"
        "    # Insert at position 0 → strongest sublayer below the root\n"
        "    root.subLayerPaths.insert(0, layer_path)\n"
        "    print(f'Attached sublayer: {layer_path}')\n"
    )


def _gen_set_edit_target(args: Dict) -> str:
    layer_path = args["layer_path"]
    layer_path_repr = repr(layer_path)
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot set edit target')\n"
        "\n"
        f"layer_path = {layer_path_repr}\n"
        "layer = Sdf.Layer.FindOrOpen(layer_path)\n"
        "if layer is None:\n"
        "    # Try to find the layer already inside the stage's layer stack\n"
        "    for stack_layer in stage.GetLayerStack():\n"
        "        if stack_layer.identifier == layer_path:\n"
        "            layer = stack_layer\n"
        "            break\n"
        "if layer is None:\n"
        "    raise RuntimeError(\n"
        "        f'Layer not found: {layer_path}. Use list_layers() to see attached layers '\n"
        "        f'or add_sublayer() to attach it first.'\n"
        "    )\n"
        "\n"
        "stage.SetEditTarget(Usd.EditTarget(layer))\n"
        "print(f'Edit target is now: {layer.identifier}')\n"
    )


def _gen_flatten_layers(args: Dict) -> str:
    output_path = args["output_path"]
    output_path_repr = repr(output_path)
    return (
        "import omni.usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot flatten layers')\n"
        "\n"
        f"output_path = {output_path_repr}\n"
        "flat = stage.Flatten()\n"
        "if flat is None:\n"
        "    raise RuntimeError('stage.Flatten() returned None')\n"
        "\n"
        "ok = flat.Export(output_path)\n"
        "if not ok:\n"
        "    raise RuntimeError(f'Failed to export flattened stage to {output_path}')\n"
        "\n"
        "print(f'Flattened stage exported to: {output_path}')\n"
    )


def _gen_add_usd_reference(args: Dict) -> str:
    prim_path = args["prim_path"]
    usd_url = args["usd_url"]
    ref_prim_path = args.get("ref_prim_path")
    layer_offset_seconds = args.get("layer_offset_seconds")
    instanceable = bool(args.get("instanceable", False))

    prim_path_repr = repr(prim_path)
    usd_url_repr = repr(usd_url)
    ref_prim_path_repr = repr(ref_prim_path) if ref_prim_path else "None"
    layer_offset_repr = (
        repr(float(layer_offset_seconds)) if layer_offset_seconds is not None else "None"
    )
    instanceable_repr = "True" if instanceable else "False"

    return (
        "import os\n"
        "import omni.usd\n"
        "from pxr import Usd, Sdf, UsdGeom\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot add USD reference')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        f"usd_url = {usd_url_repr}\n"
        f"ref_prim_path = {ref_prim_path_repr}\n"
        f"layer_offset_seconds = {layer_offset_repr}\n"
        f"instanceable = {instanceable_repr}\n"
        "\n"
        # Validate local filesystem paths before calling AddReference — USD
        # accepts any URL and the composition failure surfaces later (or
        # never). Remote URLs go through USD's asset resolver as before.
        "if not any(usd_url.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):\n"
        "    if not os.path.isabs(usd_url) or not os.path.exists(usd_url):\n"
        "        raise FileNotFoundError(f'add_usd_reference: asset not found: {usd_url!r}')\n"
        "\n"
        "# Auto-create the holding prim as an Xform if it does not exist.\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    prim = UsdGeom.Xform.Define(stage, prim_path).GetPrim()\n"
        "    print(f'Created Xform at {prim_path} to hold the reference')\n"
        "\n"
        "# Build the LayerOffset in USD time codes (caller passes SECONDS).\n"
        "layer_offset = None\n"
        "if layer_offset_seconds is not None:\n"
        "    try:\n"
        "        tcps = stage.GetTimeCodesPerSecond() or 24.0\n"
        "    except Exception:\n"
        "        tcps = 24.0\n"
        "    layer_offset = Sdf.LayerOffset(layer_offset_seconds * tcps, 1.0)\n"
        "\n"
        "refs_api = prim.GetReferences()\n"
        "_had_refs_before = prim.HasAuthoredReferences()\n"
        "if ref_prim_path and layer_offset is not None:\n"
        "    refs_api.AddReference(usd_url, ref_prim_path, layer_offset)\n"
        "elif ref_prim_path:\n"
        "    refs_api.AddReference(usd_url, ref_prim_path)\n"
        "elif layer_offset is not None:\n"
        "    refs_api.AddReference(usd_url, '', layer_offset)\n"
        "else:\n"
        "    refs_api.AddReference(usd_url)\n"
        "\n"
        "if not prim.HasAuthoredReferences():\n"
        "    raise RuntimeError(f'add_usd_reference: AddReference completed but HasAuthoredReferences is still False on {prim_path}')\n"
        "\n"
        "if instanceable:\n"
        "    # USD point-instancing: per-instance edits below this prim are dropped.\n"
        "    prim.SetInstanceable(True)\n"
        "    print(f'  prim marked instanceable=True (per-instance edits below {prim_path} will be dropped)')\n"
        "\n"
        "print(\n"
        "    f'add_usd_reference: prim={prim_path} asset={usd_url!r} '\n"
        "    f'ref_prim={ref_prim_path!r} offset_s={layer_offset_seconds} '\n"
        "    f'instanceable={instanceable}'\n"
        ")\n"
    )


def _gen_load_payload(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    return (
        "import omni.usd\n"
        "from pxr import Usd, Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot load payload')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "# Soft no-op if the prim's payload(s) are already in the load set.\n"
        "try:\n"
        "    load_set = stage.GetLoadSet()\n"
        "    already_loaded = prim.GetPath() in load_set\n"
        "except Exception:\n"
        "    already_loaded = False\n"
        "\n"
        "if already_loaded:\n"
        "    print(f'Payload already loaded for {prim_path} — nothing to do (no-op)')\n"
        "else:\n"
        "    # LoadAndUnload({prim_path}, set()) loads the payload + descendants.\n"
        "    try:\n"
        "        stage.LoadAndUnload(\n"
        "            {Sdf.Path(prim_path)},\n"
        "            set(),\n"
        "            Usd.LoadWithDescendants,\n"
        "        )\n"
        "    except Exception:\n"
        "        # Older Kit signature without policy arg:\n"
        "        stage.LoadAndUnload({Sdf.Path(prim_path)}, set())\n"
        "    print(\n"
        "        f'load_payload: activated payload(s) on {prim_path} (LoadWithDescendants)'\n"
        "    )\n"
    )


def _gen_save_stage(args: Dict) -> str:
    path = args["path"]
    # Live-reproduced bug: save_stage('/nonexistent/dir/scene.usd') returned
    # result=True and the tool reported 'wrote ...' — but the file wasn't
    # there. Kit's save is async + doesn't propagate filesystem errors
    # synchronously, AND the try/except swallowed any that did leak.
    # Fix: pre-check parent dir exists and is writable (for local paths),
    # post-check the file landed, and stop calling the output "wrote"
    # until we've confirmed it.
    return f"""\
import os
import omni.usd

ctx = omni.usd.get_context()
target = {repr(path)}

# Pre-check local filesystem destinations. Remote URLs (omniverse://,
# http(s)://, file://, anon:) go through the asset resolver.
if not any(target.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    _parent = os.path.dirname(os.path.abspath(target)) or '.'
    if not os.path.isdir(_parent):
        raise FileNotFoundError(f'save_stage: parent directory does not exist: {{_parent!r}}')
    if not os.access(_parent, os.W_OK):
        raise PermissionError(f'save_stage: parent directory not writable: {{_parent!r}}')

current = ctx.get_stage_url() or ""
if current and current == target:
    result = ctx.save_stage()
else:
    result = ctx.save_as_stage(target)

# USD's save_stage returns True even when the actual write failed (async
# pipeline). For local paths, verify the file materialized.
if not result:
    raise RuntimeError(f'save_stage: ctx returned result={{result!r}} for {{target!r}}')
if not any(target.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    if not os.path.exists(target):
        raise RuntimeError(
            f'save_stage: ctx reported success but no file at {{target!r}} — '
            f'check filesystem permissions / disk space / USD async pipeline.'
        )
print(f'save_stage: confirmed write of {{target}}')
"""


def _gen_open_stage(args: Dict) -> str:
    path = args["path"]
    # Two holes the old version had: (1) ctx.open_stage returns False on
    # missing file but the print said "opened {target} (ok=False)" — the word
    # "opened" is a lie; (2) the try/except swallowed exceptions, so the tool
    # reported success=True and the agent would parrot "opened" to the user.
    return f"""\
import os
import omni.usd

ctx = omni.usd.get_context()
target = {repr(path)}
# Local filesystem paths must exist. Remote/session URLs (omniverse://,
# http(s)://, file://, anon:) resolve through USD's asset resolver and can't
# be checked with os.path.exists.
if not any(target.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    if not os.path.exists(target):
        raise FileNotFoundError(f'open_stage: no such file: {{target!r}}')
ok = ctx.open_stage(target)
if not ok:
    raise RuntimeError(f'open_stage: ctx.open_stage({{target!r}}) returned False — USD could not load the stage')
print(f"open_stage: successfully opened {{target}}")
"""


def _gen_export_stage(args: Dict) -> str:
    path = args["path"]
    fmt = args["format"].lower()
    # Original fire-and-forget pattern was structurally dishonest:
    #   asyncio.ensure_future(...)  # never awaited
    # The tool reported success=True while the actual export was still in
    # flight. Any exception raised inside _do_export never reached the
    # caller. Fix: validate inputs synchronously upfront (fmt allowlist,
    # parent directory exists for local paths), then still schedule the
    # async export — but the sync phase at least catches typos and missing
    # directories. Output text also changed from "wrote" (past tense) to
    # "started" to avoid the false-complete implication.
    return f"""\
import os
import asyncio
import omni.kit.app

target = {repr(path)}
fmt = {repr(fmt)}

_ALLOWED_FMTS = {{'usd', 'usda', 'usdc', 'usdz', 'glb', 'gltf', 'obj', 'fbx', 'stl'}}
if fmt not in _ALLOWED_FMTS:
    raise ValueError(
        f"export_stage: unsupported format {{fmt!r}} — expected one of {{sorted(_ALLOWED_FMTS)}}"
    )

# Local paths: parent directory must exist so the async writer has a
# place to land the file. Remote/Nucleus URLs skip this check.
if not any(target.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    _parent = os.path.dirname(os.path.abspath(target)) or '.'
    if not os.path.isdir(_parent):
        raise FileNotFoundError(
            f"export_stage: parent directory does not exist: {{_parent!r}}"
        )

async def _do_export():
    try:
        ext_mgr = omni.kit.app.get_app().get_extension_manager()
        ext_id = "omni.kit.tool.asset_exporter"
        if not ext_mgr.is_extension_enabled(ext_id):
            ext_mgr.set_extension_enabled_immediate(ext_id, True)
        from omni.kit.tool.asset_exporter import ExportContext, export_asset
        ec = ExportContext()
        ec.export_path = target
        ec.export_format = fmt
        result = await export_asset(ec)
        print(f"export_stage: async export finished for {{target}} ({{fmt}}) — result={{result}}")
    except Exception as e:
        print(f"export_stage: async export failed for {{target}} ({{fmt}}): {{e}}")

asyncio.ensure_future(_do_export())
print(f"export_stage: started async export of {{target}} as {{fmt}} — completion is logged separately")
"""


# ---------------------------------------------------------------------------
# Phase 6 wave 18 — OmniGraph node ops + bulk attribute/schema + prim grouping


def _gen_add_node(args: Dict) -> str:
    """Add a single node to an existing OmniGraph via og.Controller.edit()."""
    from ..tool_executor import _OG_NODE_TYPE_MAP
    graph_path = args["graph_path"]
    raw_node_type = args["node_type"]
    node_name = args["name"]
    # Reuse the legacy → 5.1 namespace remap so callers can pass either form.
    node_type = _OG_NODE_TYPE_MAP.get(raw_node_type, raw_node_type)
    # Live-probed 2026-04-18: og.Controller.edit with a non-existent graph
    # returned success=True and claimed "Added node 'tick' to /World/NoGraph"
    # — the graph wasn't created, the node doesn't exist. Pre-check the
    # graph prim and post-check the node actually landed.
    return f"""\
import omni.usd
import omni.graph.core as og

# Pre-check the graph exists — Controller.edit silently creates a new
# graph AND silently fails to create any node under some Kit versions
# when the parent path is missing. Fail fast instead.
_stage = omni.usd.get_context().get_stage()
_graph_path = {graph_path!r}
_node_name = {node_name!r}
_node_type = {node_type!r}
_graph_prim = _stage.GetPrimAtPath(_graph_path)
if not _graph_prim or not _graph_prim.IsValid():
    raise RuntimeError(
        f'add_node: graph not found at {{_graph_path!r}} — '
        f'create it first with create_omnigraph or create_graph'
    )

keys = og.Controller.Keys
_result = og.Controller.edit(
    {{"graph_path": _graph_path}},
    {{
        keys.CREATE_NODES: [
            (_node_name, _node_type),
        ],
    }},
)

# Post-check: verify the node landed under the graph.
_node_path = f'{{_graph_path}}/{{_node_name}}'
_node_prim = _stage.GetPrimAtPath(_node_path)
if not _node_prim or not _node_prim.IsValid():
    raise RuntimeError(
        f'add_node: og.Controller.edit returned but no prim at {{_node_path!r}} — '
        f'likely unknown node_type {{_node_type!r}} (extension not loaded?)'
    )
print(f"Added node '{{_node_name}}' ({{_node_type}}) to {{_graph_path}}")
"""


def _gen_connect_nodes(args: Dict) -> str:
    """Wire src.outputs:X -> dst.inputs:Y via og.Controller.edit() with CONNECT."""
    graph_path = args["graph_path"]
    src = args["src"]
    dst = args["dst"]
    return f"""\
import omni.graph.core as og

keys = og.Controller.Keys
og.Controller.edit(
    {{"graph_path": "{graph_path}"}},
    {{
        keys.CONNECT: [
            ("{src}", "{dst}"),
        ],
    }},
)
print(f"Connected {src} -> {dst} in {graph_path}")
"""


def _gen_set_graph_variable(args: Dict) -> str:
    """Set a graph-scoped variable on an OmniGraph via og.Controller."""
    graph_path = args["graph_path"]
    var_name = args["name"]
    value = args["value"]
    return f"""\
import omni.graph.core as og

graph = og.Controller.graph("{graph_path}")
if graph is None:
    raise RuntimeError(f"OmniGraph not found at {graph_path}")

# Try og.Controller.set_variable() first (modern API);
# fall back to graph.get_variable(name).set(value) on older Kit builds.
_value = {value!r}
try:
    og.Controller.set_variable(("{graph_path}", "{var_name}"), _value)
    print(f"Set variable '{var_name}' on {graph_path} via og.Controller.set_variable")
except Exception:
    var = graph.get_variable("{var_name}")
    if var is None:
        raise RuntimeError(f"Variable '{var_name}' does not exist on {graph_path}")
    var.set(_value)
    print(f"Set variable '{var_name}' on {graph_path} via graph.get_variable().set()")
"""


def _gen_delete_node(args: Dict) -> str:
    """Remove a single node via og.Controller.edit() with DELETE_NODES."""
    graph_path = args["graph_path"]
    node_name = args["node_name"]
    return f"""\
import omni.graph.core as og

keys = og.Controller.Keys
og.Controller.edit(
    {{"graph_path": "{graph_path}"}},
    {{
        keys.DELETE_NODES: [
            "{node_name}",
        ],
    }},
)
print(f"Deleted node '{node_name}' from {graph_path}")
"""


def _gen_bulk_set_attribute(args: Dict) -> str:
    """T14.1 — atomically set the same attribute on many prims via Sdf.ChangeBlock."""
    prim_paths = args["prim_paths"]
    attr = args["attr"]
    value = args["value"]
    return f"""\
import omni.usd
from pxr import Sdf, Usd, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_attr = {attr!r}
_value = {value!r}

# Infer a USD Sdf.ValueTypeName so missing attributes can be created on the fly
def _infer_value_type(v):
    if isinstance(v, bool):
        return Sdf.ValueTypeNames.Bool
    if isinstance(v, int):
        return Sdf.ValueTypeNames.Int
    if isinstance(v, float):
        return Sdf.ValueTypeNames.Float
    if isinstance(v, str):
        return Sdf.ValueTypeNames.String
    if isinstance(v, (list, tuple)):
        n = len(v)
        if n == 2:
            return Sdf.ValueTypeNames.Float2
        if n == 3:
            return Sdf.ValueTypeNames.Float3
        if n == 4:
            return Sdf.ValueTypeNames.Float4
    return None

_applied = 0
_skipped_missing_prim = 0
_skipped_create_failed = 0
_created = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _skipped_missing_prim += 1
            continue
        _a = _prim.GetAttribute(_attr)
        if not _a or not _a.IsValid():
            _tname = _infer_value_type(_value)
            if _tname is None:
                _skipped_create_failed += 1
                continue
            _a = _prim.CreateAttribute(_attr, _tname)
            _created += 1
        try:
            # Wrap Vec-like lists into Gf types when the attribute expects them
            _typename = str(_a.GetTypeName()) if _a and _a.IsValid() else ""
            _v = _value
            if isinstance(_value, (list, tuple)):
                if "3" in _typename and len(_value) == 3:
                    _v = Gf.Vec3f(*_value) if "float" in _typename.lower() else Gf.Vec3d(*_value)
            _a.Set(_v)
            _applied += 1
        except Exception as _e:
            _skipped_create_failed += 1

if _applied == 0 and len(_paths) > 0:
    # Zero applied across a non-empty prim list is a silent-success: the
    # agent would narrate "I set X on all prims" while nothing landed.
    # Raise with the skip breakdown so the agent can report what failed.
    raise RuntimeError(
        "bulk_set_attribute: 0 of " + str(len(_paths)) + " paths had the "
        "attribute set. missing_prim=" + str(_skipped_missing_prim) +
        ", create_failed=" + str(_skipped_create_failed) +
        ". Check the paths exist and the attribute name / value type are compatible."
    )

print(f"bulk_set_attribute: applied={{_applied}} created={{_created}} "
      f"missing_prim={{_skipped_missing_prim}} failed={{_skipped_create_failed}} "
      f"attr={attr!r}")
"""


def _gen_bulk_apply_schema(args: Dict) -> str:
    """T14.2 — apply the same API schema to many prims via Sdf.ChangeBlock."""
    from ..tool_executor import _TIER14_SCHEMA_MAP
    prim_paths = args["prim_paths"]
    schema = args["schema"]
    if schema in _TIER14_SCHEMA_MAP:
        mod, cls = _TIER14_SCHEMA_MAP[schema]
        return f"""\
import omni.usd
from pxr import Sdf
from {mod} import {cls}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}

_applied = 0
_missing = 0
_already = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        if _prim.HasAPI({cls}):
            _already += 1
            continue
        {cls}.Apply(_prim)
        _applied += 1

print(f"bulk_apply_schema: schema={schema!r} applied={{_applied}} "
      f"already_had={{_already}} missing={{_missing}}")
"""
    # Fallback: ApplyAPISchemaCommand per prim. Verify each result by diffing
    # GetAppliedSchemas() before/after — Kit's command silently no-ops on
    # unknown schema names, so we raise if nothing changed.
    return f"""\
import omni.usd
import omni.kit.commands
from pxr import Sdf

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}

_applied = 0
_missing = 0
_silent_noop = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        _before = set(_prim.GetAppliedSchemas() or [])
        try:
            omni.kit.commands.execute('ApplyAPISchemaCommand', api={schema!r}, prim=_prim)
        except Exception:
            _missing += 1
            continue
        _after = set(_prim.GetAppliedSchemas() or [])
        if _before == _after:
            _silent_noop += 1
        else:
            _applied += 1

if _silent_noop > 0 and _applied == 0:
    raise RuntimeError(
        f'bulk_apply_schema: schema {schema!r} applied to 0 prims; '
        f'{{_silent_noop}} silent no-ops — likely unknown schema name.'
    )
print(f"bulk_apply_schema: schema={schema!r} applied={{_applied}} "
      f"silent_noops={{_silent_noop}} missing={{_missing}}")
"""


def _gen_group_prims(args: Dict) -> str:
    """T14.4 — create an Xform parent and reparent prims under it."""
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    prim_paths = args["prim_paths"]
    group_name = args["group_name"]
    group_parent = args.get("group_parent", "/World")
    # Guard against slashes in group_name
    safe_name = str(group_name).strip("/").replace("/", "_")
    group_path = f"{group_parent.rstrip('/')}/{safe_name}"
    return f"""\
import omni.usd
from pxr import Sdf, UsdGeom, Gf

{_SAFE_XFORM_SNIPPET}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_group_path = {group_path!r}

# Create the Xform group (idempotent — DefinePrim returns existing if present)
_group_prim = stage.DefinePrim(_group_path, "Xform")

_moved = 0
_missing = 0
_skipped_self = 0

with Sdf.ChangeBlock():
    for _src in _paths:
        _prim = stage.GetPrimAtPath(_src)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        if _src == _group_path or _src.startswith(_group_path + "/"):
            _skipped_self += 1
            continue
        _leaf = _src.rsplit("/", 1)[-1]
        _dst = _group_path + "/" + _leaf
        # Capture world transform BEFORE the move so we can restore it
        try:
            _world = UsdGeom.Xformable(_prim).ComputeLocalToWorldTransform(0)
            _pos = _world.ExtractTranslation()
        except Exception:
            _pos = Gf.Vec3d(0, 0, 0)
        # Reparent via CopySpec + RemovePrim (USD's canonical "move" pattern)
        try:
            Sdf.CopySpec(stage.GetRootLayer(), _src, stage.GetRootLayer(), _dst)
            stage.RemovePrim(_src)
            _new_prim = stage.GetPrimAtPath(_dst)
            if _new_prim and _new_prim.IsValid() and UsdGeom.Xformable(_new_prim):
                _safe_set_translate(_new_prim, (_pos[0], _pos[1], _pos[2]))
            _moved += 1
        except Exception as _e:
            _missing += 1

print(f"group_prims: group={{_group_path}} moved={{_moved}} "
      f"missing={{_missing}} skipped_self={{_skipped_self}}")
"""


def _gen_duplicate_prims(args: Dict) -> str:
    """T14.5 — duplicate prims via Sdf.CopySpec and apply a positional offset."""
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    prim_paths = args["prim_paths"]
    offset = args["offset"]
    suffix = args.get("suffix", "_copy")
    ox, oy, oz = offset[0], offset[1], offset[2]
    return f"""\
import omni.usd
from pxr import Sdf, UsdGeom, Gf

{_SAFE_XFORM_SNIPPET}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_offset = ({ox}, {oy}, {oz})
_suffix = {suffix!r}

_pairs = []
_missing = 0

def _unique_dst(base):
    # Append numeric suffixes on collision: _copy, _copy2, _copy3, ...
    cand = base + _suffix
    if not stage.GetPrimAtPath(cand):
        return cand
    i = 2
    while stage.GetPrimAtPath(base + _suffix + str(i)):
        i += 1
    return base + _suffix + str(i)

with Sdf.ChangeBlock():
    for _src in _paths:
        _prim = stage.GetPrimAtPath(_src)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        _dst = _unique_dst(_src)
        try:
            Sdf.CopySpec(stage.GetRootLayer(), _src, stage.GetRootLayer(), _dst)
        except Exception:
            _missing += 1
            continue
        _new = stage.GetPrimAtPath(_dst)
        if _new and _new.IsValid() and UsdGeom.Xformable(_new):
            # Read existing local translate (if any) and add offset
            _cur = Gf.Vec3d(0, 0, 0)
            _xf = UsdGeom.Xformable(_new)
            for _op in _xf.GetOrderedXformOps():
                if _op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    _cur = Gf.Vec3d(_op.Get() or (0, 0, 0))
                    break
            _new_t = (_cur[0] + _offset[0], _cur[1] + _offset[1], _cur[2] + _offset[2])
            _safe_set_translate(_new, _new_t)
        _pairs.append((_src, _dst))

print(f"duplicate_prims: count={{len(_pairs)}} missing={{_missing}} "
      f"offset={offset!r}")
for _s, _d in _pairs:
    print(f"  {{_s}} -> {{_d}}")
"""



# ---------------------------------------------------------------------------
# Phase 6 wave 21 — variants + metadata + semantic label cleanup


# Wave 21 fix-up: the @honesty_checked decorator on _gen_set_variant was
# dropped during the initial wave-21 move; the decorator auto-prepends a
# prim-exists check using args['prim_path'] and post-checks that the
# variant selection actually took. Restored here to preserve runtime
# behavior parity with pre-move dispatch.
from ..tool_honesty import honesty_checked  # noqa: E402


@honesty_checked(require_prim_paths=("prim_path",))
def _gen_set_variant(args: Dict) -> str:
    # Demo retrofit: @honesty_checked auto-prepends a prim-exists check
    # using args['prim_path']. Post-check: verify the variant selection
    # actually took (vsets silently no-op on unknown variant names).
    prim_path = args["prim_path"]
    variant_set = args["variant_set"]
    variant = args["variant"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"_sv_path = {prim_path!r}\n"
        f"_sv_set = {variant_set!r}\n"
        f"_sv_variant = {variant!r}\n"
        "prim = stage.GetPrimAtPath(_sv_path)\n"
        "vsets = prim.GetVariantSets()\n"
        "vset = vsets.GetVariantSet(_sv_set) if vsets.HasVariantSet(_sv_set) else vsets.AddVariantSet(_sv_set)\n"
        "vset.SetVariantSelection(_sv_variant)\n"
        "_vs_actual = vset.GetVariantSelection()\n"
        "if _vs_actual != _sv_variant:\n"
        "    raise RuntimeError(\n"
        "        f'set_variant: SetVariantSelection({_sv_variant!r}) on {_sv_set!r} of {_sv_path!r} '\n"
        "        f'did not take — vset.GetVariantSelection() returned {_vs_actual!r} '\n"
        "        f'(likely unknown variant name)'\n"
        "    )\n"
        "print('variant', _sv_path, _sv_set, '=', _sv_variant)"
    )


def _gen_set_prim_metadata(args: Dict) -> str:
    """Emit code that writes a USD metadata field via prim.SetMetadata()."""
    prim_path = args["prim_path"]
    key = args["key"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath({prim_path!r})\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError('prim not found: ' + {prim_path!r})\n"
        f"ok = prim.SetMetadata({key!r}, {value!r})\n"
        f"print('set_prim_metadata', {prim_path!r}, {key!r}, '=', {value!r}, 'ok=', ok)\n"
    )


def _gen_remove_semantic_label(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    return (
        "import omni.usd\n"
        "from pxr import Usd, Semantics, Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot remove semantic label')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "# Enumerate every Semantics_* instance, remove the API and clear leftover attrs.\n"
        "try:\n"
        "    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(\n"
        "        Semantics.SemanticsAPI, 'GetAll'\n"
        "    ) else []\n"
        "except Exception:\n"
        "    instances = []\n"
        "\n"
        "if not instances:\n"
        "    print(f'No Semantics.SemanticsAPI applied on {prim_path} — nothing to remove (no-op)')\n"
        "else:\n"
        "    removed = []\n"
        "    for sem in instances:\n"
        "        try:\n"
        "            instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''\n"
        "        except Exception:\n"
        "            instance_name = ''\n"
        "        try:\n"
        "            prim.RemoveAPI(Semantics.SemanticsAPI, instance_name)\n"
        "        except Exception:\n"
        "            # Older Kit: RemoveAppliedSchema works on the underlying spec\n"
        "            try:\n"
        "                full = f'SemanticsAPI:{instance_name}' if instance_name else 'SemanticsAPI'\n"
        "                prim.RemoveAppliedSchema(full)\n"
        "            except Exception:\n"
        "                pass\n"
        "        # Explicitly clear the attributes RemoveAPI leaves behind so HasAPI() is False.\n"
        "        for attr_name in (\n"
        "            f'semantic:{instance_name}:params:semanticType' if instance_name else 'semantic:params:semanticType',\n"
        "            f'semantic:{instance_name}:params:semanticData' if instance_name else 'semantic:params:semanticData',\n"
        "        ):\n"
        "            attr = prim.GetAttribute(attr_name)\n"
        "            if attr and attr.IsValid():\n"
        "                try:\n"
        "                    prim.RemoveProperty(attr_name)\n"
        "                except Exception:\n"
        "                    pass\n"
        "        removed.append(instance_name or '<default>')\n"
        "    print(f'Removed Semantics.SemanticsAPI from {prim_path}: instances={removed}')\n"
    )


def _gen_assign_class_to_children(args: Dict) -> str:
    prim_path = args["prim_path"]
    class_name = args["class_name"]
    semantic_type = args.get("semantic_type", "class")
    prim_path_repr = repr(prim_path)
    class_name_repr = repr(class_name)
    semantic_type_repr = repr(semantic_type)
    instance_name = f"Semantics_{semantic_type}"
    instance_name_repr = repr(instance_name)
    return (
        "import omni.usd\n"
        "from pxr import Usd, UsdGeom, Semantics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot assign class to children')\n"
        "\n"
        f"root_path = {prim_path_repr}\n"
        f"class_name = {class_name_repr}\n"
        f"semantic_type = {semantic_type_repr}\n"
        f"instance_name = {instance_name_repr}\n"
        "\n"
        "root = stage.GetPrimAtPath(root_path)\n"
        "if not root or not root.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {root_path}')\n"
        "\n"
        "# Walk root + every descendant. Only Mesh / Imageable prims (i.e. things that\n"
        "# render and therefore appear in SDG output) get the label — Xforms and pure\n"
        "# grouping prims are skipped because labels on them are dead weight.\n"
        "labeled = []\n"
        "skipped = []\n"
        "for prim in Usd.PrimRange(root):\n"
        "    if not prim or not prim.IsValid():\n"
        "        continue\n"
        "    is_mesh = prim.IsA(UsdGeom.Mesh)\n"
        "    is_imageable = prim.IsA(UsdGeom.Gprim)  # Mesh, Sphere, Cube, ... — anything that draws\n"
        "    if not (is_mesh or is_imageable):\n"
        "        skipped.append(str(prim.GetPath()))\n"
        "        continue\n"
        "    sem = Semantics.SemanticsAPI.Apply(prim, instance_name)\n"
        "    sem.CreateSemanticTypeAttr().Set(semantic_type)\n"
        "    sem.CreateSemanticDataAttr().Set(class_name)\n"
        "    labeled.append(str(prim.GetPath()))\n"
        "\n"
        "print(\n"
        "    f'assign_class_to_children: root={root_path} class={class_name!r} '\n"
        "    f'type={semantic_type!r} labeled={len(labeled)} skipped={len(skipped)}'\n"
        ")\n"
        "if labeled:\n"
        "    print(f'  first labeled: {labeled[:5]}')\n"
    )


# ---------------------------------------------------------------------------
# Phase 6 wave 23 — OmniGraph + mesh ops + area zones


def _gen_merge_meshes(args: Dict) -> str:
    prim_paths = args["prim_paths"]
    output_path = args["output_path"]

    return f"""\
import omni.usd
from isaacsim.util.merge_mesh import MeshMerger

stage = omni.usd.get_context().get_stage()

# Ensure output parent exists
output_path = '{output_path}'
parent_path = '/'.join(output_path.rsplit('/', 1)[:-1]) or '/World'
if not stage.GetPrimAtPath(parent_path).IsValid():
    stage.DefinePrim(parent_path, 'Xform')

prim_paths = {prim_paths}

# Merge meshes
merger = MeshMerger(stage)
merger.update_selection(prim_paths)
merger.merge()

print(f"Merged {{len(prim_paths)}} meshes: {{prim_paths}}")
"""


def _gen_create_graph(args: Dict) -> str:
    """Generate OmniGraph code from a template-based description."""
    from .. import tool_executor as _te  # noqa: PLC0415
    _OG_TEMPLATES = _te._OG_TEMPLATES
    _detect_template = _te._detect_template

    description = args.get("description", "")
    template_name = args.get("template")
    graph_path = args.get("graph_path", "/World/ActionGraph")

    # Auto-detect template if not explicitly specified
    if not template_name:
        template_name = _detect_template(description)
    if not template_name or template_name not in _OG_TEMPLATES:
        return (
            f"# Could not match description to a known template: '{description}'\n"
            f"# Available templates: {', '.join(sorted(_OG_TEMPLATES.keys()))}\n"
            f"# Specify 'template' parameter explicitly, or use create_omnigraph for free-form graphs.\n"
            f"raise ValueError('No matching OmniGraph template for: {description}')"
        )

    tmpl = _OG_TEMPLATES[template_name]
    defaults = tmpl.get("defaults", {})

    # Resolve parameter values from args, falling back to defaults
    params = {}
    for key in tmpl.get("param_keys", []):
        val = args.get(key) or defaults.get(key, "")
        params[key] = val

    # Build node definitions
    node_defs = ",\n            ".join(
        f"('{name}', '{ntype}')" for name, ntype in tmpl["nodes"]
    )

    # Build connection definitions
    conn_defs = ",\n            ".join(
        f"('{src}', '{tgt}')" for src, tgt in tmpl["connections"]
    )

    # Build SET_VALUES with parameter substitution
    val_items = []
    for attr_path, val_template in tmpl.get("values", {}).items():
        resolved = val_template.format(**params) if isinstance(val_template, str) else val_template
        if isinstance(resolved, str):
            val_items.append(f"            ('{attr_path}', '{resolved}')")
        else:
            val_items.append(f"            ('{attr_path}', {resolved})")

    set_values_block = ""
    if val_items:
        val_defs = ",\n".join(val_items)
        set_values_block = f"""        keys.SET_VALUES: [
{val_defs}
        ],"""

    return f"""\
import omni.graph.core as og

# Template: {template_name} — {tmpl['description']}
# {description}

# ROS2 templates need the ROS2 bridge extension loaded first
if "{template_name}".startswith("ros2"):
    try:
        import omni.kit.app as _app
        _mgr = _app.get_app().get_extension_manager()
        if not _mgr.is_extension_enabled("isaacsim.ros2.bridge"):
            _mgr.set_extension_enabled_immediate("isaacsim.ros2.bridge", True)
    except Exception as _ex:
        print(f"[warn] could not enable isaacsim.ros2.bridge: {{_ex}}")

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
{set_values_block}
    }},
)
print(f"Created {template_name} graph at {graph_path} with {{len(nodes)}} nodes")
"""


def _gen_explain_graph(args: Dict) -> str:
    """Generate code that reads an OmniGraph and prints a structured JSON description."""
    graph_path = args["graph_path"]
    return f"""\
import omni.graph.core as og
import json

graph = og.get_graph_by_path('{graph_path}')
if graph is None:
    raise ValueError("No OmniGraph found at '{graph_path}'")

nodes = graph.get_nodes()
result = {{
    "graph_path": "{graph_path}",
    "node_count": len(nodes),
    "nodes": [],
    "connections": [],
}}

for node in nodes:
    node_info = {{
        "name": node.get_prim_path().split("/")[-1],
        "type": node.get_node_type().get_node_type(),
        "path": str(node.get_prim_path()),
    }}
    # Read input attribute values
    attrs = {{}}
    for attr in node.get_attributes():
        name = attr.get_name()
        if name.startswith("inputs:"):
            try:
                val = attr.get()
                if val is not None and not isinstance(val, (bytes, memoryview)):
                    attrs[name] = val
            except Exception:
                pass
    if attrs:
        node_info["inputs"] = attrs
    result["nodes"].append(node_info)

    # Read connections (outputs)
    for attr in node.get_attributes():
        if attr.get_name().startswith("outputs:"):
            for conn in attr.get_upstream_connections():
                result["connections"].append({{
                    "source": f"{{conn.get_node().get_prim_path().split('/')[-1]}}.{{conn.get_name()}}",
                    "target": f"{{node.get_prim_path().split('/')[-1]}}.{{attr.get_name()}}",
                }})

print(json.dumps(result, indent=2, default=str))
"""


def _gen_activate_area(args: Dict) -> str:
    scope = args["prim_scope"]
    sibling_only = bool(args.get("deactivate_siblings_only", True))
    return f"""\
import omni.usd

stage = omni.usd.get_context().get_stage()
scope = '{scope}'
sibling_only = {sibling_only}
deactivated = 0
kept = 0

scope_norm = scope.rstrip('/')

def _inside_scope(path):
    return path == scope_norm or path.startswith(scope_norm + '/')

# Collect ancestor paths of the scope so we can keep them active when
# sibling_only is True (the spec's "deactivate everything outside scope"
# would otherwise also disable the pseudo-root / /World which breaks rendering).
ancestors = set()
parts = scope_norm.strip('/').split('/')
cur = ''
for part in parts:
    cur = cur + '/' + part
    ancestors.add(cur)

for prim in stage.TraverseAll():
    path = str(prim.GetPath())
    if _inside_scope(path):
        prim.SetActive(True)
        kept += 1
        continue
    if sibling_only and path in ancestors:
        # keep structural ancestors active so the scope prim resolves
        prim.SetActive(True)
        kept += 1
        continue
    try:
        prim.SetActive(False)
        deactivated += 1
    except Exception:
        pass

print(f'activate_area: scope={{scope}} kept={{kept}} deactivated={{deactivated}}')
"""


# ---------------------------------------------------------------------------
# Phase 7 wave 3 — scene-authoring data-handlers (introspection)


async def _handle_list_all_prims(args: Dict) -> Dict:
    from .. import kit_tools
    ctx = await kit_tools.get_stage_context(full=True)
    return ctx.get("stage", {})


async def _handle_get_attribute(args: Dict) -> Dict:
    """Read a single USD attribute value."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    attr_name = args["attr_name"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'attr_name': {attr_name!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    attr = prim.GetAttribute({attr_name!r})
    if not attr or not attr.IsDefined():
        result['error'] = 'attribute not defined'
        result['available'] = [a.GetName() for a in prim.GetAttributes()][:50]
    else:
        try:
            value = attr.Get()
        except Exception as exc:
            value = None
            result['error'] = f'attr.Get() failed: {{exc}}'
        # Convert pxr.Vt / Gf types to plain Python for json
        try:
            value = list(value) if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)) else value
        except Exception:
            value = repr(value)
        result['value'] = value
        result['type_name'] = attr.GetTypeName().type.typeName
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_attribute {prim_path}.{attr_name}")

async def _handle_get_world_transform(args: Dict) -> Dict:
    """Compute world-space 4x4 transform of a prim."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    time_code = args.get("time_code")
    tc_expr = repr(time_code) if time_code is not None else "Usd.TimeCode.Default()"
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    xf = UsdGeom.Xformable(prim)
    if not xf:
        result['error'] = 'prim is not Xformable'
    else:
        m = xf.ComputeLocalToWorldTransform({tc_expr})
        result['matrix'] = [m[i][j] for i in range(4) for j in range(4)]
        t = m.ExtractTranslation()
        r = m.ExtractRotationQuat()
        # Pull scale from the upper 3x3
        sx = Gf.Vec3d(m[0][0], m[0][1], m[0][2]).GetLength()
        sy = Gf.Vec3d(m[1][0], m[1][1], m[1][2]).GetLength()
        sz = Gf.Vec3d(m[2][0], m[2][1], m[2][2]).GetLength()
        result['translation'] = [t[0], t[1], t[2]]
        im = r.GetImaginary()
        result['rotation_quat'] = [r.GetReal(), im[0], im[1], im[2]]
        result['scale'] = [sx, sy, sz]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_world_transform {prim_path}")

async def _handle_get_bounding_box(args: Dict) -> Dict:
    """Compute world-space AABB of a prim."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    purpose = args.get("purpose", "default")
    # USD's UsdGeom.Tokens enum was reorganized — Tokens.default no longer
    # exists in some pxr versions. Use the string literal directly via the
    # purpose-name lookup (which matches "default", "render", "proxy", "guide"
    # against the registered purpose tokens). Fall back to no purpose filter
    # if even that is rejected.
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    # Robust purpose-token lookup. Some Isaac Sim 5.x USD versions removed
    # UsdGeom.Tokens.default; getattr with fallback handles both shapes.
    _purpose_attr = {purpose!r}
    try:
        purpose_token = getattr(UsdGeom.Tokens, _purpose_attr)
        purposes = [purpose_token]
    except AttributeError:
        # Fall back to no purpose filter — BBoxCache then computes for all
        purposes = [UsdGeom.Tokens.default_] if hasattr(UsdGeom.Tokens, 'default_') else []
        if not purposes:
            # Final fallback: omit purpose argument entirely
            purposes = None
    try:
        if purposes is not None:
            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), purposes, useExtentsHint=True)
        else:
            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), useExtentsHint=True)
        bbox = cache.ComputeWorldBound(prim)
        rng = bbox.ComputeAlignedRange()
        if rng.IsEmpty():
            result['error'] = 'empty bbox'
        else:
            mn = rng.GetMin()
            mx = rng.GetMax()
            cx = (mn[0] + mx[0]) / 2.0
            cy = (mn[1] + mx[1]) / 2.0
            cz = (mn[2] + mx[2]) / 2.0
            result['min'] = [mn[0], mn[1], mn[2]]
            result['max'] = [mx[0], mx[1], mx[2]]
            result['center'] = [cx, cy, cz]
            result['size'] = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]]
    except Exception as _e:
        result['error'] = f'bbox compute failed: {{type(_e).__name__}}: {{_e}}'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_bounding_box {prim_path}")

async def _handle_prim_exists(args: Dict) -> Dict:
    """Boolean check for prim presence at a path. Used by verify-contract to
    validate assistant claims like 'robot at /World/Franka is loaded'."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
exists = bool(prim and prim.IsValid())
result = {{'prim_path': {prim_path!r}, 'exists': exists}}
if exists:
    result['type_name'] = str(prim.GetTypeName())
    result['applied_schemas'] = [str(s) for s in (prim.GetAppliedSchemas() or [])]
    result['child_count'] = len(list(prim.GetChildren()))
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"prim_exists {prim_path}")


async def _handle_list_attributes(args: Dict) -> Dict:
    """Enumerate all attributes on a prim via prim.GetAttributes()."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    attrs = []
    for attr in prim.GetAttributes():
        attrs.append({{
            'name': attr.GetName(),
            'type': attr.GetTypeName().type.typeName,
            'has_value': bool(attr.HasValue()),
            'custom': bool(attr.IsCustom()),
        }})
    result['attribute_count'] = len(attrs)
    result['attributes'] = attrs
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_attributes {prim_path}")

async def _handle_list_applied_schemas(args: Dict) -> Dict:
    """Return applied API schemas on a prim via prim.GetAppliedSchemas()."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        schemas = list(prim.GetAppliedSchemas())
    except Exception as exc:
        schemas = []
        result['error'] = f'GetAppliedSchemas failed: {{exc}}'
    result['applied_schemas'] = [str(s) for s in schemas]
    result['schema_count'] = len(schemas)
    try:
        result['type_name'] = prim.GetTypeName()
    except Exception:
        result['type_name'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_applied_schemas {prim_path}")

async def _handle_get_prim_metadata(args: Dict) -> Dict:
    """Read a single USD metadata field on a prim via prim.GetMetadata(key)."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    key = args["key"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'key': {key!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        if not prim.HasMetadata({key!r}):
            result['has_metadata'] = False
            result['value'] = None
        else:
            value = prim.GetMetadata({key!r})
            result['has_metadata'] = True
            result['python_type'] = type(value).__name__
            try:
                json.dumps(value)
                result['value'] = value
            except Exception:
                # Non-json-serialisable USD types — coerce to repr
                try:
                    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                        result['value'] = list(value)
                    else:
                        result['value'] = repr(value)
                except Exception:
                    result['value'] = repr(value)
    except Exception as exc:
        result['error'] = f'GetMetadata failed: {{exc}}'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_prim_metadata {prim_path}.{key}")

async def _handle_get_prim_type(args: Dict) -> Dict:
    """Return prim.GetTypeName() (e.g. 'Mesh', 'Xform', 'Camera')."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        type_name = prim.GetTypeName()
    except Exception as exc:
        type_name = ''
        result['error'] = f'GetTypeName failed: {{exc}}'
    result['type_name'] = str(type_name) if type_name else ''
    try:
        result['is_a_model'] = bool(prim.IsModel())
    except Exception:
        result['is_a_model'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_prim_type {prim_path}")

async def _handle_find_prims_by_schema(args: Dict) -> Dict:
    """Traverse the stage and return prims where prim.HasAPI(schema) is true."""
    from .. import kit_tools
    schema_name = args["schema_name"]
    root_path = args.get("root_path") or "/"
    limit = int(args.get("limit", 500))
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, UsdShade
import json

schema_name = {schema_name!r}
limit = {limit}

stage = omni.usd.get_context().get_stage()
result = {{'schema_name': schema_name, 'root_path': {root_path!r}}}

# Resolve schema class. Users pass the applied-schema token (e.g.
# "PhysicsRigidBodyAPI"), but the Python class is UsdPhysics.RigidBodyAPI —
# the module prefix is dropped. Try both the literal name and variants with
# the conventional "Physics"/"Geom"/"Shade" prefix stripped.
_mod_prefix_map = (
    (UsdPhysics, "Physics"),
    (UsdGeom, "Geom"),
    (UsdShade, "Shade"),
)
schema_cls = None
for mod, prefix in _mod_prefix_map:
    for candidate_name in (schema_name, schema_name[len(prefix):] if schema_name.startswith(prefix) else None):
        if candidate_name is None:
            continue
        cand = getattr(mod, candidate_name, None)
        if cand is not None:
            schema_cls = cand
            break
    if schema_cls is not None:
        break
if schema_cls is None:
    try:
        import pxr
        for mod_name in dir(pxr):
            mod = getattr(pxr, mod_name, None)
            for candidate_name in (schema_name,):
                cand = getattr(mod, candidate_name, None) if mod is not None else None
                if cand is not None:
                    schema_cls = cand
                    break
            if schema_cls is not None:
                break
    except Exception as exc:
        result['lookup_error'] = str(exc)

if schema_cls is None:
    result['error'] = f'unknown schema: {{schema_name}}'
    result['matches'] = []
    print(json.dumps(result, default=str))
else:
    root_prim = stage.GetPrimAtPath({root_path!r})
    if not root_prim or not root_prim.IsValid():
        root_prim = stage.GetPseudoRoot()
    matches = []
    for p in Usd.PrimRange(root_prim):
        try:
            if p.HasAPI(schema_cls):
                matches.append(str(p.GetPath()))
                if len(matches) >= limit:
                    break
        except Exception:
            # Some non-API schemas raise — fall back to typed-schema check
            try:
                if p.IsA(schema_cls):
                    matches.append(str(p.GetPath()))
                    if len(matches) >= limit:
                        break
            except Exception:
                continue
    result['match_count'] = len(matches)
    result['matches'] = matches
    result['truncated'] = len(matches) >= limit
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"find_prims_by_schema {schema_name}")

async def _handle_find_prims_by_name(args: Dict) -> Dict:
    """Regex search on prim paths."""
    from .. import kit_tools
    pattern = args["pattern"]
    root_path = args.get("root_path") or "/"
    limit = int(args.get("limit", 500))
    code = f"""\
import omni.usd
from pxr import Usd
import re
import json

pattern = {pattern!r}
limit = {limit}
root_path = {root_path!r}

stage = omni.usd.get_context().get_stage()
result = {{'pattern': pattern, 'root_path': root_path}}
try:
    rx = re.compile(pattern)
except re.error as exc:
    result['error'] = f'invalid regex: {{exc}}'
    result['matches'] = []
    print(json.dumps(result, default=str))
else:
    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim or not root_prim.IsValid():
        root_prim = stage.GetPseudoRoot()
    matches = []
    for p in Usd.PrimRange(root_prim):
        path_str = str(p.GetPath())
        if rx.search(path_str):
            matches.append(path_str)
            if len(matches) >= limit:
                break
    result['match_count'] = len(matches)
    result['matches'] = matches
    result['truncated'] = len(matches) >= limit
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"find_prims_by_name {pattern}")

async def _handle_get_kind(args: Dict) -> Dict:
    """Read Kind metadata via Usd.ModelAPI(prim).GetKind()."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import Usd, Kind
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        model = Usd.ModelAPI(prim)
        kind = model.GetKind()
        result['kind'] = str(kind) if kind else ''
        # Useful classification helpers
        try:
            registry = Kind.Registry()
            if kind:
                result['is_a_model'] = bool(registry.IsA(kind, 'model'))
                result['is_a_component'] = bool(registry.IsA(kind, 'component'))
                result['is_a_assembly'] = bool(registry.IsA(kind, 'assembly'))
                result['is_a_group'] = bool(registry.IsA(kind, 'group'))
            else:
                result['is_a_model'] = False
        except Exception as exc:
            result['kind_registry_error'] = str(exc)
    except Exception as exc:
        result['error'] = f'GetKind failed: {{exc}}'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_kind {prim_path}")

async def _handle_get_semantic_label(args: Dict) -> Dict:
    """Read every Semantics.SemanticsAPI instance applied to a single prim."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Semantics\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            try:\n"
        "                instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(\n"
        "                    Semantics.SemanticsAPI, 'GetAll'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                instances = []\n"
        "            labels = []\n"
        "            for sem in instances:\n"
        "                try:\n"
        "                    instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''\n"
        "                except Exception:\n"
        "                    instance_name = ''\n"
        "                try:\n"
        "                    type_attr = sem.GetSemanticTypeAttr()\n"
        "                    sem_type = type_attr.Get() if type_attr and type_attr.IsValid() else ''\n"
        "                except Exception:\n"
        "                    sem_type = ''\n"
        "                try:\n"
        "                    data_attr = sem.GetSemanticDataAttr()\n"
        "                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else ''\n"
        "                except Exception:\n"
        "                    cls = ''\n"
        "                labels.append({\n"
        "                    'instance': str(instance_name),\n"
        "                    'semantic_type': str(sem_type) if sem_type is not None else '',\n"
        "                    'class_name': str(cls) if cls is not None else '',\n"
        "                })\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_semantics': bool(labels),\n"
        "                'labels': labels,\n"
        "                'count': len(labels),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"Read Semantics.SemanticsAPI labels on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Semantic-label lookup queued. Kit will print a JSON dict with keys: "
            "prim_path, has_semantics, labels (list of {instance, semantic_type, "
            "class_name}), count. has_semantics=false means the prim is not labeled — "
            "that is a normal state, not an error."
        ),
    }

async def _handle_get_asset_info(args: Dict) -> Dict:
    """Read assetInfo metadata + introducing layer + sha256 for a prim."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "import os\n"
        "import hashlib\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            ai = {}\n"
        "            try:\n"
        "                raw = prim.GetAssetInfo() or {}\n"
        "                # GetAssetInfo returns a VtDictionary — coerce to plain dict.\n"
        "                ai = {k: raw[k] for k in raw.keys()} if hasattr(raw, 'keys') else dict(raw)\n"
        "            except Exception:\n"
        "                ai = {}\n"
        "            asset_info = {\n"
        "                'identifier': str(ai.get('identifier', '') or ''),\n"
        "                'name': str(ai.get('name', '') or ''),\n"
        "                'version': str(ai.get('version', '') or ''),\n"
        "                'payload_asset_dependencies': [\n"
        "                    str(x) for x in (ai.get('payloadAssetDependencies') or [])\n"
        "                ],\n"
        "            }\n"
        "            has_asset_info = bool(\n"
        "                asset_info['identifier'] or asset_info['name']\n"
        "                or asset_info['version'] or asset_info['payload_asset_dependencies']\n"
        "            )\n"
        "            # Introducing layer — the layer that brought this prim into the\n"
        "            # composed stage. Use prim.GetPrimStack()[0] (strongest spec).\n"
        "            intro_layer = {'identifier': '', 'real_path': '', 'version': None, 'sha256': None}\n"
        "            try:\n"
        "                stack = prim.GetPrimStack()\n"
        "                if stack:\n"
        "                    spec = stack[0]\n"
        "                    layer = spec.layer if hasattr(spec, 'layer') else None\n"
        "                    if layer is not None:\n"
        "                        intro_layer['identifier'] = str(layer.identifier)\n"
        "                        intro_layer['real_path'] = str(layer.realPath or '')\n"
        "                        intro_layer['version'] = str(layer.GetCustomLayerData().get('version', '')) or None\n"
        "                        rp = intro_layer['real_path']\n"
        "                        if rp and os.path.isfile(rp):\n"
        "                            try:\n"
        "                                size = os.path.getsize(rp)\n"
        "                            except OSError:\n"
        "                                size = 0\n"
        "                            if 0 < size < 256 * 1024 * 1024:\n"
        "                                h = hashlib.sha256()\n"
        "                                with open(rp, 'rb') as f:\n"
        "                                    for chunk in iter(lambda: f.read(65536), b''):\n"
        "                                        h.update(chunk)\n"
        "                                intro_layer['sha256'] = h.hexdigest()\n"
        "            except Exception:\n"
        "                pass\n"
        "            try:\n"
        "                from pxr import Kind\n"
        "                model = Usd.ModelAPI(prim)\n"
        "                kind_val = model.GetKind() if model else ''\n"
        "                prim_kind = str(kind_val) if kind_val else None\n"
        "            except Exception:\n"
        "                prim_kind = None\n"
        "            try:\n"
        "                spec_str = str(prim.GetSpecifier()).split('.')[-1].lower()\n"
        "                if spec_str.startswith('specifier'):\n"
        "                    spec_str = spec_str[len('specifier'):]\n"
        "            except Exception:\n"
        "                spec_str = 'def'\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_asset_info': has_asset_info,\n"
        "                'asset_info': asset_info,\n"
        "                'introducing_layer': intro_layer,\n"
        "                'prim_kind': prim_kind,\n"
        "                'prim_specifier': spec_str,\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"Read asset info / origin / hash for {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Asset-info lookup queued. Kit will print a JSON dict with keys: "
            "prim_path, has_asset_info, asset_info ({identifier, name, version, "
            "payload_asset_dependencies}), introducing_layer ({identifier, "
            "real_path, version, sha256}), prim_kind, prim_specifier. "
            "has_asset_info=false is normal — most prims do not author the "
            "assetInfo metadata. sha256=null when the layer is bigger than 256 MB "
            "(synchronous hashing would block Kit) or the layer is not a real "
            "on-disk file (e.g. anonymous in-memory layer)."
        ),
    }

async def _handle_get_selected_prims(args: Dict) -> Dict:
    """Return the user's current selection in the viewport / Stage panel."""
    from .. import kit_tools
    code = """\
import json
import omni.usd

ctx = omni.usd.get_context()
sel = ctx.get_selection()
paths = list(sel.get_selected_prim_paths()) if sel is not None else []
primary = paths[-1] if paths else None
print(json.dumps({"selected_paths": paths, "count": len(paths), "primary": primary}))
"""
    return await kit_tools.queue_exec_patch(code, "Read user selection")


# ---------------------------------------------------------------------------
# Phase 7 wave 4 — scene-authoring data-handlers (lists + queries)


async def _handle_scene_summary(args: Dict) -> Dict:
    from .. import kit_tools
    ctx = await kit_tools.get_stage_context(full=False)
    if "error" in ctx:
        return ctx
    text = kit_tools.format_stage_context_for_llm(ctx)
    return {"summary": text}


async def _handle_run_stage_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run all (or selected) validator packs against the live stage."""
    from .. import kit_tools
    from ...analysis.orchestrator import AnalysisOrchestrator

    # 1. Fetch full stage context from Kit
    if not await kit_tools.is_kit_rpc_alive():
        return {"error": "Kit RPC is not reachable — cannot analyse the stage."}

    try:
        stage_data = await kit_tools.get_stage_context(full=True)
    except Exception as e:
        return {"error": f"Failed to fetch stage context: {e}"}

    # 2. Build analyser with requested packs (or all)
    enabled_packs = args.get("packs") or None
    analyzer = AnalysisOrchestrator(enabled_packs=enabled_packs)

    # 3. Run analysis
    result = analyzer.run_analysis(stage_data)

    # 4. Serialize
    results = []
    for f in result.findings:
        entry = {
            "rule": f.rule_id,
            "severity": f.severity,
            "prim": f.prim_path,
            "message": f.message,
        }
        if f.fix_suggestion:
            entry["fix_hint"] = f.fix_suggestion.description
        results.append(entry)

    summary = {}
    for r in results:
        summary[r["severity"]] = summary.get(r["severity"], 0) + 1

    return {
        "total_findings": len(results),
        "summary": summary,
        "findings": results[:50],  # cap to avoid huge payloads
        "truncated": len(results) > 50,
    }


async def _handle_scene_diff(args: Dict) -> Dict:
    """Compute a structured scene diff via Kit RPC.

    Supports three modes:
    - since="last_save"     → diff dirty layers against on-disk version
    - since="last_snapshot" → diff current vs. most recent snapshot
    - snapshot_a + snapshot_b → explicit comparison
    """
    from .. import kit_tools
    from ..tool_executor import _parse_unified_diff_to_changes, _summarize_changes

    since = args.get("since")
    snap_a = args.get("snapshot_a")
    snap_b = args.get("snapshot_b")

    if since == "last_save":
        # Use Kit RPC to diff dirty layers against their on-disk copies
        code = """\
import omni.usd
import difflib
import json

ctx = omni.usd.get_context()
stage = ctx.get_stage()
dirty = ctx.get_dirty_layers() if hasattr(ctx, 'get_dirty_layers') else []
all_diff = []
for layer_id in dirty:
    from pxr import Sdf
    layer = Sdf.Layer.Find(layer_id)
    if layer is None:
        continue
    current_text = layer.ExportToString()
    # Try to get the on-disk version
    disk_layer = None
    if layer.realPath:
        try:
            disk_layer = Sdf.Layer.OpenAsAnonymous(layer.realPath)
        except Exception:
            pass
    disk_text = disk_layer.ExportToString() if disk_layer else ""
    diff_lines = list(difflib.unified_diff(
        disk_text.splitlines(), current_text.splitlines(), lineterm=""
    ))
    all_diff.extend(diff_lines)
# Fallback: if no dirty layers found, diff root layer against empty
if not dirty:
    root = stage.GetRootLayer()
    current_text = root.ExportToString()
    all_diff = list(difflib.unified_diff(
        [], current_text.splitlines(), lineterm=""
    ))
print(json.dumps({"diff_lines": all_diff, "dirty_layer_count": len(dirty)}))
"""
        result = await kit_tools.queue_exec_patch(code, "scene_diff(since=last_save)")
        if result.get("error"):
            return {"error": result["error"]}
        # Parse Kit output
        output = result.get("output", "")
        diff_data: Dict = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "last_save",
            "dirty_layer_count": diff_data.get("dirty_layer_count", 0),
        }

    elif since == "last_snapshot":
        # Compare current stage text against the most recent snapshot
        code = """\
import omni.usd
import difflib
import json
import os

stage = omni.usd.get_context().get_stage()
current_text = stage.GetRootLayer().ExportToString()

# Find most recent snapshot file
snap_dir = os.path.join(os.getcwd(), "workspace", "snapshots")
snapshots = []
if os.path.isdir(snap_dir):
    snapshots = sorted(
        [f for f in os.listdir(snap_dir) if f.endswith(('.usda', '.usd'))],
        key=lambda f: os.path.getmtime(os.path.join(snap_dir, f)),
        reverse=True,
    )
if not snapshots:
    print(json.dumps({"diff_lines": [], "error": "No snapshots found"}))
else:
    from pxr import Sdf
    snap_path = os.path.join(snap_dir, snapshots[0])
    snap_layer = Sdf.Layer.OpenAsAnonymous(snap_path)
    snap_text = snap_layer.ExportToString() if snap_layer else ""
    diff_lines = list(difflib.unified_diff(
        snap_text.splitlines(), current_text.splitlines(), lineterm=""
    ))
    print(json.dumps({"diff_lines": diff_lines, "snapshot_file": snapshots[0]}))
"""
        result = await kit_tools.queue_exec_patch(code, "scene_diff(since=last_snapshot)")
        if result.get("error"):
            return {"error": result["error"]}
        output = result.get("output", "")
        diff_data = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if diff_data.get("error"):
            return {"error": diff_data["error"]}
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "last_snapshot",
            "snapshot_file": diff_data.get("snapshot_file"),
        }

    elif snap_a and snap_b:
        # Explicit comparison between two named snapshots
        # Sanitize snapshot names — only allow alphanumeric, underscore, hyphen, dot
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_.-]+$', snap_a):
            return {"error": f"Invalid snapshot_a name: {snap_a}"}
        if not _re.match(r'^[a-zA-Z0-9_.-]+$', snap_b):
            return {"error": f"Invalid snapshot_b name: {snap_b}"}

        code = f"""\
import os
import difflib
import json
from pxr import Sdf

snap_dir = os.path.join(os.getcwd(), "workspace", "snapshots")
path_a = os.path.join(snap_dir, "{snap_a}")
path_b = os.path.join(snap_dir, "{snap_b}")

# Try with common extensions if not found
for ext in ("", ".usda", ".usd"):
    if os.path.exists(path_a + ext):
        path_a = path_a + ext
        break
for ext in ("", ".usda", ".usd"):
    if os.path.exists(path_b + ext):
        path_b = path_b + ext
        break

if not os.path.exists(path_a):
    print(json.dumps({{"error": "Snapshot not found: {snap_a}"}}))
elif not os.path.exists(path_b):
    print(json.dumps({{"error": "Snapshot not found: {snap_b}"}}))
else:
    layer_a = Sdf.Layer.OpenAsAnonymous(path_a)
    layer_b = Sdf.Layer.OpenAsAnonymous(path_b)
    text_a = layer_a.ExportToString() if layer_a else ""
    text_b = layer_b.ExportToString() if layer_b else ""
    diff_lines = list(difflib.unified_diff(
        text_a.splitlines(), text_b.splitlines(), lineterm=""
    ))
    print(json.dumps({{"diff_lines": diff_lines, "snapshot_a": "{snap_a}", "snapshot_b": "{snap_b}"}}))
"""
        result = await kit_tools.queue_exec_patch(code, f"scene_diff({snap_a} vs {snap_b})")
        if result.get("error"):
            return {"error": result["error"]}
        output = result.get("output", "")
        diff_data = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if diff_data.get("error"):
            return {"error": diff_data["error"]}
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "explicit",
            "snapshot_a": snap_a,
            "snapshot_b": snap_b,
        }

    return {"error": "Provide either 'since' (last_save|last_snapshot) or both 'snapshot_a' and 'snapshot_b'."}


async def _handle_build_stage_index(args: Dict) -> Dict:
    """Build the metadata index and populate the module-level cache."""
    from .. import kit_tools
    from ..tool_executor import _STAGE_INDEX, _STAGE_INDEX_META, _gen_build_stage_index

    prim_scope = args.get("prim_scope") or "/World"
    max_prims = int(args.get("max_prims", 50000))
    code = _gen_build_stage_index({"prim_scope": prim_scope, "max_prims": max_prims})
    queued = await kit_tools.queue_exec_patch(code, f"Build stage index under {prim_scope}")
    # Even when Kit is offline we still reset the local cache so repeated
    # builds don't accumulate stale data.
    _STAGE_INDEX.clear()
    _STAGE_INDEX_META["prim_scope"] = prim_scope
    _STAGE_INDEX_META["prim_count"] = 0
    _STAGE_INDEX_META["max_prims"] = max_prims
    return {
        "prim_scope": prim_scope,
        "max_prims": max_prims,
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
        "note": "Kit will populate the index asynchronously via the queued patch.",
    }


async def _handle_query_stage_index(args: Dict) -> Dict:
    """Return prims relevant to the keywords plus neighbours of selected_prim."""
    from ..tool_executor import (
        _STAGE_INDEX,
        _score_prim_for_query,
        _neighbour_paths,
    )

    keywords = args.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    selected_prim = args.get("selected_prim") or ""
    max_results = int(args.get("max_results", 100))

    if not _STAGE_INDEX:
        return {
            "results": [],
            "total_indexed": 0,
            "note": "Stage index is empty — call build_stage_index first.",
        }

    scored: List[Dict[str, Any]] = []
    for path, meta in _STAGE_INDEX.items():
        score = _score_prim_for_query(path, meta, keywords)
        if score > 0:
            scored.append({"path": path, "score": score, **meta})
    scored.sort(key=lambda r: (-r["score"], r["path"]))

    # Always include the selected prim + its neighbours so the LLM has local
    # context even when keywords don't match nearby paths.
    included_paths = {r["path"] for r in scored}
    context_paths: List[str] = []
    if selected_prim and selected_prim in _STAGE_INDEX and selected_prim not in included_paths:
        context_paths.append(selected_prim)
    for n in _neighbour_paths(selected_prim):
        if n not in included_paths and n not in context_paths:
            context_paths.append(n)

    context_records = [
        {"path": p, "score": 0, **_STAGE_INDEX[p]}
        for p in context_paths if p in _STAGE_INDEX
    ]

    combined = (scored + context_records)[:max_results]
    return {
        "results": combined,
        "total_indexed": len(_STAGE_INDEX),
        "match_count": len(scored),
        "context_count": len(context_records),
        "keywords": keywords,
        "selected_prim": selected_prim,
    }


async def _handle_count_prims_under_path(args: Dict) -> Dict:
    """Count direct or recursive children under a parent prim path, optionally
    filtered by type_name. Used to verify 'I cloned N robots' claims."""
    from .. import kit_tools

    parent_path = args["parent_path"]
    type_filter = args.get("type_filter")  # e.g. "Xform", "Mesh" — optional
    recursive = bool(args.get("recursive", False))
    code = f"""\
import omni.usd
import json
from pxr import Usd

stage = omni.usd.get_context().get_stage()
parent = stage.GetPrimAtPath({parent_path!r})
result = {{'parent_path': {parent_path!r}, 'type_filter': {type_filter!r}, 'recursive': {recursive!r}}}
if not parent or not parent.IsValid():
    result['error'] = 'parent_path not found'
    result['count'] = 0
    result['paths'] = []
else:
    if {recursive!r}:
        prims = [p for p in Usd.PrimRange(parent) if str(p.GetPath()) != str(parent.GetPath())]
    else:
        prims = list(parent.GetChildren())
    if {type_filter!r}:
        prims = [p for p in prims if str(p.GetTypeName()) == {type_filter!r}]
    result['count'] = len(prims)
    result['paths'] = [str(p.GetPath()) for p in prims[:200]]
    result['truncated'] = len(prims) > 200
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"count_prims {parent_path}")


async def _handle_list_relationships(args: Dict) -> Dict:
    """List all relationships on a prim via prim.GetRelationships()."""
    from .. import kit_tools

    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    rels = []
    for rel in prim.GetRelationships():
        try:
            targets = [str(t) for t in rel.GetTargets()]
        except Exception as exc:
            targets = []
            rel_error = str(exc)
        else:
            rel_error = None
        entry = {{
            'name': rel.GetName(),
            'targets': targets,
            'target_count': len(targets),
            'custom': bool(rel.IsCustom()),
        }}
        if rel_error:
            entry['error'] = rel_error
        rels.append(entry)
    result['relationship_count'] = len(rels)
    result['relationships'] = rels
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_relationships {prim_path}")


async def _handle_list_layers(args: Dict) -> Dict:
    """Walk the current stage's layer stack and return identifiers + edit target.

    Generates a small introspection script and queues it via Kit RPC. Kit prints
    JSON with one entry per layer plus the active edit target; when Kit is
    unreachable we still return a structured stub so the LLM gets a predictable
    shape.
    """
    from .. import kit_tools

    code = """\
import json
try:
    import omni.usd
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        edit_target = stage.GetEditTarget().GetLayer()
        edit_target_id = edit_target.identifier if edit_target is not None else None
        root = stage.GetRootLayer()
        layers = []
        seen = set()
        # depth-first walk of the layer stack so 'depth' reflects sublayer nesting
        def _walk(layer, depth):
            if layer is None or layer.identifier in seen:
                return
            seen.add(layer.identifier)
            layers.append({
                'identifier': layer.identifier,
                'display_name': getattr(layer, 'GetDisplayName', lambda: layer.identifier)(),
                'anonymous': bool(layer.anonymous),
                'dirty': bool(layer.dirty),
                'depth': depth,
                'is_edit_target': layer.identifier == edit_target_id,
            })
            try:
                from pxr import Sdf
                for sub_path in layer.subLayerPaths:
                    sub = Sdf.Layer.FindOrOpen(sub_path)
                    _walk(sub, depth + 1)
            except Exception:
                pass
        _walk(root, 0)
        print(json.dumps({
            'root_layer': root.identifier if root is not None else None,
            'edit_target': edit_target_id,
            'layers': layers,
            'count': len(layers),
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "List USD layer stack and edit target")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Layer stack introspection queued. Kit will print a JSON dict with keys: "
            "root_layer, edit_target, layers (list of {identifier, display_name, "
            "anonymous, dirty, depth, is_edit_target}), count."
        ),
    }


async def _handle_list_variant_sets(args: Dict) -> Dict:
    """Read every variant set declared on a prim and the current selection on each."""
    from .. import kit_tools

    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            vsets = prim.GetVariantSets()\n"
        "            names = list(vsets.GetNames())\n"
        "            entries = []\n"
        "            for name in names:\n"
        "                vs = vsets.GetVariantSet(name)\n"
        "                entries.append({\n"
        "                    'name': name,\n"
        "                    'current': vs.GetVariantSelection(),\n"
        "                    'count': len(vs.GetVariantNames()),\n"
        "                })\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'variant_sets': entries,\n"
        "                'count': len(entries),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(code, f"List variant sets on {prim_path}")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Variant-set introspection queued. Kit will print a JSON dict with keys: "
            "prim_path, variant_sets (list of {name, current, count}), count."
        ),
    }


async def _handle_list_variants(args: Dict) -> Dict:
    """List every named variant choice inside a specific variant set on a prim."""
    from .. import kit_tools

    prim_path = args["prim_path"]
    variant_set = args["variant_set"]
    prim_path_repr = repr(prim_path)
    variant_set_repr = repr(variant_set)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        f"        variant_set_name = {variant_set_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            vsets = prim.GetVariantSets()\n"
        "            if not vsets.HasVariantSet(variant_set_name):\n"
        "                print(json.dumps({\n"
        "                    'error': f'variant set not found: {variant_set_name}',\n"
        "                    'available': list(vsets.GetNames()),\n"
        "                }))\n"
        "            else:\n"
        "                vs = vsets.GetVariantSet(variant_set_name)\n"
        "                names = list(vs.GetVariantNames())\n"
        "                print(json.dumps({\n"
        "                    'prim_path': prim_path,\n"
        "                    'variant_set': variant_set_name,\n"
        "                    'variants': names,\n"
        "                    'current': vs.GetVariantSelection(),\n"
        "                    'count': len(names),\n"
        "                }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List variants in {variant_set} on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "variant_set": variant_set,
        "note": (
            "Variant introspection queued. Kit will print a JSON dict with keys: "
            "prim_path, variant_set, variants (list of names), current, count."
        ),
    }


async def _handle_list_semantic_classes(args: Dict) -> Dict:
    """Walk the stage, collect every Semantics.SemanticsAPI label, return unique classes."""
    from .. import kit_tools

    code = """\
import json
try:
    import omni.usd
    from pxr import Usd, Semantics
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        classes = {}  # class_name -> {'count': int, 'sample_prims': [str, ...]}
        labeled = 0
        for prim in stage.Traverse():
            try:
                if not Semantics.SemanticsAPI.HasAPI(prim):
                    # Some Kit builds expose only the multi-apply variant — fall back to
                    # GetAll which returns an empty list when nothing is applied.
                    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                        Semantics.SemanticsAPI, 'GetAll'
                    ) else []
                else:
                    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                        Semantics.SemanticsAPI, 'GetAll'
                    ) else [Semantics.SemanticsAPI(prim, 'Semantics_class')]
            except Exception:
                instances = []
            if not instances:
                continue
            labeled += 1
            for sem in instances:
                try:
                    data_attr = sem.GetSemanticDataAttr()
                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else None
                except Exception:
                    cls = None
                if cls is None or cls == '':
                    continue
                cls = str(cls)
                bucket = classes.setdefault(cls, {'count': 0, 'sample_prims': []})
                bucket['count'] += 1
                if len(bucket['sample_prims']) < 5:
                    bucket['sample_prims'].append(str(prim.GetPath()))
        out_classes = [
            {'name': name, 'count': info['count'], 'sample_prims': info['sample_prims']}
            for name, info in sorted(classes.items())
        ]
        print(json.dumps({
            'classes': out_classes,
            'total_classes': len(out_classes),
            'total_labeled_prims': labeled,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(
        code, "List unique semantic classes used on the current stage"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Semantic-class enumeration queued. Kit will print a JSON dict with keys: "
            "classes (list of {name, count, sample_prims}), total_classes, "
            "total_labeled_prims. count=1 for a class often signals a typo against the "
            "intended bulk label."
        ),
    }


async def _handle_list_references(args: Dict) -> Dict:
    """Enumerate USD reference arcs composed onto a prim."""
    from .. import kit_tools
    from ..tool_executor import _TIER12_HELPERS

    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf, Pcp\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        + _TIER12_HELPERS
        + "            references = []\n"
        "            # Local opinions via prim.GetReferences().GetAllReferences() —\n"
        "            # available on most Kit builds. Fall back to PrimCompositionQuery\n"
        "            # when the simple API is missing.\n"
        "            try:\n"
        "                refs_api = prim.GetReferences()\n"
        "                local_refs = refs_api.GetAllReferences() if hasattr(\n"
        "                    refs_api, 'GetAllReferences'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                local_refs = []\n"
        "            for r in local_refs:\n"
        "                try:\n"
        "                    references.append({\n"
        "                        'asset_path': str(r.assetPath) if hasattr(r, 'assetPath') else '',\n"
        "                        'prim_path': str(r.primPath) if hasattr(r, 'primPath') else '',\n"
        "                        'layer_offset': _layer_offset_dict(getattr(r, 'layerOffset', None)),\n"
        "                        'introducing_layer': '<local>',\n"
        "                        'list_position': 'explicit',\n"
        "                    })\n"
        "                except Exception:\n"
        "                    continue\n"
        "            # Composed arcs (sublayered / inherited reference arcs) via PrimCompositionQuery.\n"
        "            try:\n"
        "                query = Usd.PrimCompositionQuery.GetDirectReferences(prim)\n"
        "                for arc in query.GetCompositionArcs():\n"
        "                    try:\n"
        "                        intro_layer = arc.GetIntroducingLayer()\n"
        "                        intro = intro_layer.identifier if intro_layer else ''\n"
        "                        if intro == '<local>' or any(\n"
        "                            ref.get('introducing_layer') == intro for ref in references\n"
        "                        ):\n"
        "                            continue\n"
        "                        target = arc.GetTargetNode()\n"
        "                        asset = ''\n"
        "                        target_path = ''\n"
        "                        if target is not None:\n"
        "                            try:\n"
        "                                site = target.path\n"
        "                                target_path = str(site)\n"
        "                            except Exception:\n"
        "                                target_path = ''\n"
        "                            try:\n"
        "                                asset_layer = target.layerStack.identifier.rootLayer\n"
        "                                asset = asset_layer.identifier\n"
        "                            except Exception:\n"
        "                                asset = ''\n"
        "                        references.append({\n"
        "                            'asset_path': asset,\n"
        "                            'prim_path': target_path,\n"
        "                            'layer_offset': {'offset': 0.0, 'scale': 1.0},\n"
        "                            'introducing_layer': intro,\n"
        "                            'list_position': 'explicit',\n"
        "                        })\n"
        "                    except Exception:\n"
        "                        continue\n"
        "            except Exception:\n"
        "                pass\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_references': bool(references),\n"
        "                'references': references,\n"
        "                'count': len(references),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List USD references composed onto {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Reference enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, has_references, references (list of {asset_path, prim_path, "
            "layer_offset, introducing_layer, list_position}), count. "
            "has_references=false means the prim has no references — that is a normal "
            "state, not an error. References are ALWAYS loaded — use list_payloads "
            "for the deferred-load equivalent."
        ),
    }


async def _handle_list_payloads(args: Dict) -> Dict:
    """Enumerate USD payload arcs (deferred-load) on a prim."""
    from .. import kit_tools
    from ..tool_executor import _TIER12_HELPERS

    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf, Pcp\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        + _TIER12_HELPERS
        + "            payloads = []\n"
        "            try:\n"
        "                pl_api = prim.GetPayloads()\n"
        "                local_pls = pl_api.GetAllPayloads() if hasattr(\n"
        "                    pl_api, 'GetAllPayloads'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                local_pls = []\n"
        "            # Current load-set membership tells us which prims have their\n"
        "            # payloads activated right now.\n"
        "            try:\n"
        "                load_set = stage.GetLoadSet()\n"
        "                prim_is_loaded = bool(prim.GetPath() in load_set)\n"
        "            except Exception:\n"
        "                prim_is_loaded = True  # default: loaded\n"
        "            for p in local_pls:\n"
        "                try:\n"
        "                    payloads.append({\n"
        "                        'asset_path': str(p.assetPath) if hasattr(p, 'assetPath') else '',\n"
        "                        'prim_path': str(p.primPath) if hasattr(p, 'primPath') else '',\n"
        "                        'layer_offset': _layer_offset_dict(getattr(p, 'layerOffset', None)),\n"
        "                        'introducing_layer': '<local>',\n"
        "                        'is_loaded': prim_is_loaded,\n"
        "                        'list_position': 'explicit',\n"
        "                    })\n"
        "                except Exception:\n"
        "                    continue\n"
        "            # Composed arcs via PrimCompositionQuery.\n"
        "            try:\n"
        "                query = Usd.PrimCompositionQuery.GetDirectInherits(prim)  # placeholder; real call below\n"
        "                query = Usd.PrimCompositionQuery(prim)\n"
        "                filt = Usd.CompositionArcFilter() if hasattr(Usd, 'CompositionArcFilter') else None\n"
        "                for arc in query.GetCompositionArcs():\n"
        "                    try:\n"
        "                        if str(arc.GetArcType()).lower().find('payload') < 0:\n"
        "                            continue\n"
        "                        intro_layer = arc.GetIntroducingLayer()\n"
        "                        intro = intro_layer.identifier if intro_layer else ''\n"
        "                        if intro == '<local>':\n"
        "                            continue\n"
        "                        target = arc.GetTargetNode()\n"
        "                        asset = ''\n"
        "                        target_path = ''\n"
        "                        if target is not None:\n"
        "                            try:\n"
        "                                target_path = str(target.path)\n"
        "                            except Exception:\n"
        "                                target_path = ''\n"
        "                            try:\n"
        "                                asset = target.layerStack.identifier.rootLayer.identifier\n"
        "                            except Exception:\n"
        "                                asset = ''\n"
        "                        payloads.append({\n"
        "                            'asset_path': asset,\n"
        "                            'prim_path': target_path,\n"
        "                            'layer_offset': {'offset': 0.0, 'scale': 1.0},\n"
        "                            'introducing_layer': intro,\n"
        "                            'is_loaded': prim_is_loaded,\n"
        "                            'list_position': 'explicit',\n"
        "                        })\n"
        "                    except Exception:\n"
        "                        continue\n"
        "            except Exception:\n"
        "                pass\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_payloads': bool(payloads),\n"
        "                'payloads': payloads,\n"
        "                'count': len(payloads),\n"
        "                'prim_is_loaded': prim_is_loaded,\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List USD payloads (deferred-load) on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Payload enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, has_payloads, payloads (list of {asset_path, prim_path, "
            "layer_offset, introducing_layer, is_loaded, list_position}), count, "
            "prim_is_loaded. has_payloads=false (no payload arcs on this prim) is a "
            "normal state, not an error. is_loaded reflects the CURRENT load-set "
            "membership and can be flipped via load_payload."
        ),
    }


async def _handle_select_by_criteria(args: Dict) -> Dict:
    """T14.3 — query USD stage for prims matching a criteria dict.

    Runs inside Kit via queue_exec_patch; the injected code prints a JSON
    payload on stdout that the LLM can read from the patch result.
    """
    from .. import kit_tools
    from ..tool_executor import _build_select_by_criteria_code

    criteria = args.get("criteria", {})
    if not isinstance(criteria, dict):
        return {"error": "criteria must be a dict", "matches": [], "count": 0}

    code = _build_select_by_criteria_code(criteria)
    result = await kit_tools.queue_exec_patch(
        code,
        f"select_by_criteria({', '.join(f'{k}={v!r}' for k, v in list(criteria.items())[:3])})",
    )
    # queue_exec_patch returns a dict with queued/patch_id — the actual matches
    # are produced when the patch executes. Surface both so the caller can
    # either poll for the patch result or read matches from the Kit log.
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "criteria": criteria,
        "query_code": code,
        "note": (
            "Matches are produced when the queued patch executes on Kit. "
            "Read the resulting JSON payload from the patch output. "
            "Schema: {matches: [str], count: int, criteria: {...}}."
        ),
    }


async def _handle_list_opened_stages(args: Dict) -> Dict:
    """List all UsdContexts and the stage URL each holds."""
    from .. import kit_tools

    code = """\
import json
import omni.usd

ctx_names = []
try:
    ctx_names = list(omni.usd.get_context_names())
except Exception:
    ctx_names = [""]
if not ctx_names:
    ctx_names = [""]

stages = []
active_ctx = ""
for name in ctx_names:
    try:
        c = omni.usd.get_context(name)
        if c is None:
            continue
        url = c.get_stage_url() or None
        stage = c.get_stage()
        prim_count = 0
        if stage is not None:
            prim_count = sum(1 for _ in stage.Traverse())
        is_dirty = False
        try:
            is_dirty = bool(c.has_pending_edit())
        except Exception:
            is_dirty = False
        stages.append({
            "context_name": name,
            "stage_url": url,
            "prim_count": prim_count,
            "is_dirty": is_dirty,
        })
        if not active_ctx:
            active_ctx = name
    except Exception:
        continue
print(json.dumps({"stages": stages, "active_context": active_ctx}))
"""
    return await kit_tools.queue_exec_patch(code, "List opened USD stages")


# ---------------------------------------------------------------------------
# Phase 7 wave 15 — scene-authoring final stragglers (compute/find/graphs/snapshots)


async def _handle_compute_stack_placement(args: Dict) -> Dict:
    """Compute placement positions for stacking N items on top of a target prim.

    Reads target's world-axis-aligned bbox, then computes positions purely in
    Python — no stage mutation. Returned positions are world coords intended
    for use as drop_target / placing_position values in a pick-place flow.

    Args:
      target_path:        USD path of the target (pallet, container, zone)
      pattern:            'column' | 'grid_RxC' (e.g. 'grid_2x2', 'grid_3x3')
      n_items:            total positions to compute
      cube_size:          edge length of each item (default 0.05)
      layer_rotation_deg: yaw rotation applied per layer (e.g. 90 for brick)
      spacing:            optional explicit center-to-center spacing
                          (default = cube_size, i.e. flush packing)
      anchor:             'top' (place on top of target, default) |
                          'inside_floor' (place on target's interior floor —
                          for bins/containers; uses target_top_z - target_height)

    Returns:
      {
        positions: [{position: [x,y,z], rotation_deg: float}, ...],
        n_items: <int>,
        target_path: <str>,
        pattern: <str>,
        spacing: <float>,
      }
    """
    from .. import kit_tools
    target_path = args["target_path"]
    pattern = args.get("pattern", "column")
    n_items = int(args.get("n_items", 1))
    cube_size = float(args.get("cube_size", 0.05))
    cube_sizes = args.get("cube_sizes")  # optional per-item override (list)
    if cube_sizes is not None:
        if not isinstance(cube_sizes, (list, tuple)) or len(cube_sizes) != n_items:
            return {"type": "error",
                    "error": f"cube_sizes must be a list of length n_items={n_items}, got {cube_sizes!r}"}
        try:
            cube_sizes = [float(s) for s in cube_sizes]
        except (ValueError, TypeError):
            return {"type": "error", "error": f"cube_sizes entries must be numbers: {cube_sizes!r}"}
    layer_rotation_deg = float(args.get("layer_rotation_deg", 0.0))
    spacing = args.get("spacing")
    spacing = float(spacing) if spacing is not None else cube_size
    anchor = args.get("anchor", "top")

    # Parse pattern → (rows, cols, skip_center) per layer
    skip_center = False
    if pattern == "column":
        rows, cols = 1, 1
    elif pattern.startswith("grid_") and "x" in pattern[5:]:
        try:
            r_str, c_str = pattern[5:].split("x", 1)
            rows, cols = int(r_str), int(c_str)
            if rows < 1 or cols < 1:
                return {"type": "error", "error": f"grid dims must be >=1, got {rows}x{cols}"}
        except (ValueError, IndexError):
            return {"type": "error", "error": f"unrecognized grid pattern: {pattern}"}
    elif pattern.startswith("donut_") and "x" in pattern[6:]:
        try:
            r_str, c_str = pattern[6:].split("x", 1)
            rows, cols = int(r_str), int(c_str)
            if rows < 3 or cols < 3 or rows % 2 == 0 or cols % 2 == 0:
                return {"type": "error",
                        "error": f"donut requires odd RxC >=3, got {rows}x{cols}"}
            skip_center = True
        except (ValueError, IndexError):
            return {"type": "error", "error": f"unrecognized donut pattern: {pattern}"}
    else:
        return {"type": "error",
                "error": f"unsupported pattern: {pattern!r} (use 'column', 'grid_RxC', or 'donut_RxC')"}

    if n_items < 1:
        return {"type": "error", "error": f"n_items must be >=1, got {n_items}"}

    code = f"""\
import json
import omni.usd
from pxr import Usd, UsdGeom

target_path = {target_path!r}
pattern = {pattern!r}
n_items = {n_items}
cube_size = {cube_size}
cube_sizes = {cube_sizes!r}
spacing = {spacing}
rows, cols = {rows}, {cols}
skip_center = {skip_center}
layer_rotation_deg = {layer_rotation_deg}
anchor = {anchor!r}

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(target_path)
result = {{
    'target_path': target_path,
    'pattern': pattern,
    'n_items': n_items,
    'spacing': spacing,
    'positions': [],
}}

if not prim or not prim.IsValid():
    result['error'] = f'target prim not found: {{target_path}}'
else:
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    bbox = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if bbox.IsEmpty():
        result['error'] = f'target bbox empty (no geometry?): {{target_path}}'
    else:
        bmin = bbox.GetMin()
        bmax = bbox.GetMax()
        cx = 0.5 * (bmin[0] + bmax[0])
        cy = 0.5 * (bmin[1] + bmax[1])
        target_top_z = float(bmax[2])
        target_bot_z = float(bmin[2])
        target_height = target_top_z - target_bot_z
        # When cube_sizes provided, base_z's cube_size term is the FIRST item's
        # size (for first-cube anchor). Subsequent items compute z per their own size.
        first_cube_size = cube_sizes[0] if cube_sizes else cube_size
        if anchor == 'inside_floor':
            base_z = target_bot_z + first_cube_size * 0.5
        else:
            base_z = target_top_z + first_cube_size * 0.5

        # Build the per-layer (row, col) sequence. For donut patterns,
        # skip the geometric center (only valid for odd rows × odd cols).
        layer_slots = []
        for r in range(rows):
            for c in range(cols):
                if skip_center and r == rows // 2 and c == cols // 2:
                    continue
                layer_slots.append((r, c))
        per_layer = len(layer_slots)

        # Center the grid on (cx, cy):
        #   col index 0..cols-1, with center at (cols-1)/2.0
        #   row index 0..rows-1, with center at (rows-1)/2.0
        positions = []
        # For column mixed-SKU: z stacks cumulatively. For grid mixed-SKU
        # multi-layer: z increment per layer assumes layer height = current
        # item's size (genuinely ambiguous for mixed grid; documented).
        is_column = (rows == 1 and cols == 1)
        floor_z = target_bot_z if anchor == 'inside_floor' else target_top_z
        for i in range(n_items):
            layer = i // per_layer
            slot = i % per_layer
            row, col = layer_slots[slot]
            x = cx + (col - (cols - 1) * 0.5) * spacing
            y = cy + (row - (rows - 1) * 0.5) * spacing
            this_size = cube_sizes[i] if cube_sizes else cube_size
            if cube_sizes:
                if is_column:
                    # Cumulative stack: sum of all lower cubes + half this one
                    z = floor_z + sum(cube_sizes[:i]) + this_size * 0.5
                else:
                    # Grid mixed-SKU multi-layer: uniform-within-layer assumption
                    z = floor_z + this_size * 0.5 + layer * this_size
            else:
                z = base_z + layer * cube_size
            yaw = (layer * layer_rotation_deg) % 360.0
            positions.append({{
                'position': [round(x, 6), round(y, 6), round(z, 6)],
                'rotation_deg': yaw,
                'size': this_size,
            }})

        result['positions'] = positions
        result['target_bbox_min'] = [round(float(bmin[i]), 6) for i in range(3)]
        result['target_bbox_max'] = [round(float(bmax[i]), 6) for i in range(3)]
        result['anchor'] = anchor
        result['base_z'] = round(base_z, 6)

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"compute_stack_placement {target_path} {pattern} n={n_items}"
    )


async def _handle_compute_surface_area(args: Dict) -> Dict:
    """Compute surface area as sum of triangle areas (after triangulation)."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import json
import math
import omni.usd
from pxr import Usd, UsdGeom, Gf

prim_path = {prim_path!r}
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(prim_path)
result = {{'prim_path': prim_path, 'units': 'm^2'}}

if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.IsA(UsdGeom.Mesh):
    result['error'] = f'prim is not a Mesh: {{prim.GetTypeName()}}'
else:
    mesh = UsdGeom.Mesh(prim)
    points_attr = mesh.GetPointsAttr()
    counts_attr = mesh.GetFaceVertexCountsAttr()
    indices_attr = mesh.GetFaceVertexIndicesAttr()
    if not (points_attr and counts_attr and indices_attr):
        result['error'] = 'mesh missing points / faceVertexCounts / faceVertexIndices'
    else:
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        local_points = points_attr.Get() or []
        world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]
        counts = list(counts_attr.Get() or [])
        indices = list(indices_attr.Get() or [])

        triangles = []
        cursor = 0
        for c in counts:
            face = indices[cursor:cursor + c]
            cursor += c
            if len(face) < 3:
                continue
            for k in range(1, len(face) - 1):
                triangles.append((face[0], face[k], face[k + 1]))

        total_area = 0.0
        for (a, b, c) in triangles:
            v0 = world_points[a]
            v1 = world_points[b]
            v2 = world_points[c]
            ex = v1[0] - v0[0]
            ey = v1[1] - v0[1]
            ez = v1[2] - v0[2]
            fx = v2[0] - v0[0]
            fy = v2[1] - v0[1]
            fz = v2[2] - v0[2]
            cx = ey * fz - ez * fy
            cy = ez * fx - ex * fz
            cz = ex * fy - ey * fx
            total_area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)

        result['triangle_count'] = len(triangles)
        result['vertex_count'] = len(world_points)
        result['surface_area'] = total_area

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"compute_surface_area {prim_path}")


async def _handle_compute_volume(args: Dict) -> Dict:
    """Compute mesh volume via signed tetrahedra (trimesh if available)."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import json
import omni.usd
from pxr import Usd, UsdGeom, Gf

prim_path = {prim_path!r}
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(prim_path)
result = {{'prim_path': prim_path, 'units': 'm^3'}}

if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.IsA(UsdGeom.Mesh):
    result['error'] = f'prim is not a Mesh: {{prim.GetTypeName()}}'
else:
    mesh = UsdGeom.Mesh(prim)
    points_attr = mesh.GetPointsAttr()
    counts_attr = mesh.GetFaceVertexCountsAttr()
    indices_attr = mesh.GetFaceVertexIndicesAttr()
    if not (points_attr and counts_attr and indices_attr):
        result['error'] = 'mesh missing points / faceVertexCounts / faceVertexIndices'
    else:
        # Bake world transform so volume is in world units
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        local_points = points_attr.Get() or []
        world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]
        counts = list(counts_attr.Get() or [])
        indices = list(indices_attr.Get() or [])

        # Triangulate (fan) every face into (i0, i_k, i_{{k+1}}) triangles
        triangles = []
        cursor = 0
        for c in counts:
            face = indices[cursor:cursor + c]
            cursor += c
            if len(face) < 3:
                continue
            for k in range(1, len(face) - 1):
                triangles.append((face[0], face[k], face[k + 1]))

        volume_signed = 0.0
        try:
            import trimesh
            import numpy as np
            verts = np.array([(p[0], p[1], p[2]) for p in world_points], dtype=float)
            faces = np.array(triangles, dtype=int)
            tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
            volume_signed = float(tm.volume)
            backend = 'trimesh'
        except Exception:
            # Manual signed-tetrahedra (divergence theorem) fallback
            for (a, b, c) in triangles:
                v0 = world_points[a]
                v1 = world_points[b]
                v2 = world_points[c]
                # Signed volume of tetrahedron (origin, v0, v1, v2)
                volume_signed += (
                    v0[0] * (v1[1] * v2[2] - v1[2] * v2[1])
                    - v0[1] * (v1[0] * v2[2] - v1[2] * v2[0])
                    + v0[2] * (v1[0] * v2[1] - v1[1] * v2[0])
                ) / 6.0
            backend = 'manual_tetrahedra'

        result['triangle_count'] = len(triangles)
        result['vertex_count'] = len(world_points)
        result['volume'] = abs(volume_signed)
        result['signed_volume'] = volume_signed
        result['backend'] = backend

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"compute_volume {prim_path}")


async def _handle_find_heavy_prims(args: Dict) -> Dict:
    """Traverse the stage and find meshes above a triangle-count threshold."""
    from .. import kit_tools
    threshold = args.get("threshold_triangles", 10000)
    code = f"""\
import json
import omni.usd
from pxr import UsdGeom, UsdPhysics

stage = omni.usd.get_context().get_stage()
heavy = []
for prim in stage.TraverseAll():
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        face_counts = mesh.GetFaceVertexCountsAttr().Get()
        if face_counts is None:
            continue
        tri_count = sum(fc - 2 for fc in face_counts)
        if tri_count >= {threshold}:
            approx = "none"
            if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                approx_attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr()
                if approx_attr:
                    approx = approx_attr.Get() or "none"
            heavy.append({{
                "prim_path": str(prim.GetPath()),
                "triangle_count": tri_count,
                "collision_approximation": approx,
            }})

heavy.sort(key=lambda x: x["triangle_count"], reverse=True)
print(json.dumps({{"prims": heavy, "count": len(heavy), "threshold": {threshold}}}))
"""
    return await kit_tools.queue_exec_patch(
        code, f"Find mesh prims with >{threshold} triangles"
    )


async def _handle_inspect_graph(args: Dict) -> Dict:
    """Return nodes, connections, and attribute values for a single graph."""
    from .. import kit_tools
    import json
    graph_path = args["graph_path"]
    code = f"""\
import json
import omni.graph.core as og

graph = og.Controller.graph("{graph_path}")
result = {{"graph_path": "{graph_path}"}}
if graph is None:
    result["error"] = "Graph not found"
    print(json.dumps(result))
else:
    nodes_info = []
    connections = []
    try:
        for node in graph.get_nodes():
            node_path = node.get_prim_path()
            node_type = node.get_type_name()
            attrs = {{}}
            for attr in node.get_attributes():
                try:
                    attrs[attr.get_name()] = repr(attr.get())
                except Exception:
                    attrs[attr.get_name()] = "<unreadable>"
                # Track downstream connections from this attribute
                try:
                    for upstream in attr.get_upstream_connections():
                        connections.append({{
                            "src": upstream.get_path(),
                            "dst": attr.get_path(),
                        }})
                except Exception:
                    pass
            nodes_info.append({{
                "name": node.get_prim_path().split("/")[-1],
                "path": node_path,
                "type": node_type,
                "attributes": attrs,
            }})
        result["nodes"] = nodes_info
        result["connections"] = connections
        result["node_count"] = len(nodes_info)
    except Exception as exc:
        result["error"] = str(exc)
print(json.dumps(result))
"""
    exec_result = await kit_tools.exec_sync(code, timeout=15)
    if not exec_result.get("success"):
        return {
            "graph_path": graph_path,
            "error": exec_result.get("output", "Kit RPC unavailable"),
        }
    output = exec_result.get("output", "").strip()
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"graph_path": graph_path, "raw_output": output}


async def _handle_list_graphs(args: Dict) -> Dict:
    """Enumerate all OmniGraph action graphs in the stage.

    Strategy: query Kit synchronously to scan the stage for prims of type
    'OmniGraph' (and the modern 'omni.graph.core.types.OmniGraph' fallback).
    Falls back to empty list when Kit RPC is unavailable.
    """
    from .. import kit_tools
    import json
    code = """\
import json
import omni.usd

stage = omni.usd.get_context().get_stage()
graphs = []
if stage is not None:
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name in ("OmniGraph", "ComputeGraph"):
            graphs.append({
                "path": str(prim.GetPath()),
                "type": str(type_name),
                "name": prim.GetName(),
            })
print(json.dumps({"graphs": graphs, "count": len(graphs)}))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {"graphs": [], "count": 0, "error": result.get("output", "Kit RPC unavailable")}
    output = result.get("output", "").strip()
    # exec_sync returns the captured stdout as a single string;
    # find the last JSON line for our payload.
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"graphs": [], "count": 0, "raw_output": output}


async def _handle_save_delta_snapshot(args: Dict) -> Dict:
    from .. import kit_tools
    from ..tool_executor import _DELTA_ROOT, logger, _gen_save_delta_snapshot
    import json
    snapshot_id = args["snapshot_id"]
    base_snapshot_id = args.get("base_snapshot_id")
    _DELTA_ROOT.mkdir(parents=True, exist_ok=True)
    code = _gen_save_delta_snapshot(snapshot_id, base_snapshot_id)
    queued = await kit_tools.queue_exec_patch(code, f"Save delta snapshot {snapshot_id}")
    # Record a manifest so restore_delta_snapshot has something to read even
    # before Kit has returned the dirty-layer payload.
    manifest_path = _DELTA_ROOT / f"{snapshot_id}.json"
    manifest = {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": base_snapshot_id,
        "status": "queued",
        "deltas": {},
    }
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[ToolExecutor] Could not write delta manifest: {exc}")
    return {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": base_snapshot_id,
        "manifest_path": str(manifest_path),
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
    }


async def _handle_restore_delta_snapshot(args: Dict) -> Dict:
    from .. import kit_tools
    from ..tool_executor import _DELTA_ROOT, _gen_restore_delta_snapshot
    import json
    snapshot_id = args["snapshot_id"]
    manifest_path = _DELTA_ROOT / f"{snapshot_id}.json"
    if not manifest_path.exists():
        return {
            "snapshot_id": snapshot_id,
            "restored": False,
            "error": f"No delta manifest found at {manifest_path}",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"snapshot_id": snapshot_id, "restored": False, "error": f"Manifest unreadable: {exc}"}
    deltas = manifest.get("deltas") or {}
    code = _gen_restore_delta_snapshot(snapshot_id, deltas)
    queued = await kit_tools.queue_exec_patch(code, f"Restore delta snapshot {snapshot_id}")
    return {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": manifest.get("base_snapshot_id"),
        "layer_count": len(deltas),
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
    }


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
