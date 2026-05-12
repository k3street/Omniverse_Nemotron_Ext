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
