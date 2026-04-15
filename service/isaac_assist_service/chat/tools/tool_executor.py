"""
tool_executor.py
-----------------
Dispatches LLM tool-calls to the appropriate backend:
  - Kit RPC (port 8001) for live scene operations
  - Local data lookups (sensor specs, deformable presets)
  - Code generation for complex operations sent to Kit for approval

All handlers return a dict that gets fed back to the LLM as a tool result.
"""
from __future__ import annotations
import difflib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from . import kit_tools
from .patch_validator import validate_patch, format_issues_for_llm, has_blocking_issues
from ...config import config

logger = logging.getLogger(__name__)

# ── Paths to knowledge files ─────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[4] / "workspace"
_SENSOR_SPECS_PATH = _WORKSPACE / "knowledge" / "sensor_specs.jsonl"
_DEFORMABLE_PRESETS_PATH = _WORKSPACE / "knowledge" / "deformable_presets.json"

# Cache loaded once
_sensor_specs: Optional[List[Dict]] = None
_deformable_presets: Optional[Dict] = None


def _load_sensor_specs() -> List[Dict]:
    global _sensor_specs
    if _sensor_specs is not None:
        return _sensor_specs
    specs = []
    if _SENSOR_SPECS_PATH.exists():
        for line in _SENSOR_SPECS_PATH.read_text().splitlines():
            line = line.strip()
            if line:
                specs.append(json.loads(line))
    _sensor_specs = specs
    return specs


def _load_deformable_presets() -> Dict:
    global _deformable_presets
    if _deformable_presets is not None:
        return _deformable_presets
    if _DEFORMABLE_PRESETS_PATH.exists():
        _deformable_presets = json.loads(_DEFORMABLE_PRESETS_PATH.read_text())
    else:
        _deformable_presets = {"presets": {}}
    return _deformable_presets


# ── Safe xform helper (inlined into generated code) ─────────────────────────
# Referenced USD assets (e.g. robots) often already have xform ops.
# Calling AddTranslateOp() again crashes with "Error in AddXformOp".
# This snippet is injected into generated code to safely set transforms.

_SAFE_XFORM_SNIPPET = '''\

def _safe_set_translate(prim, pos):
    """Set translate, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op.Set(Gf.Vec3d(*pos))
            return
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))

def _safe_set_scale(prim, s):
    """Set scale, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            op.Set(Gf.Vec3d(*s))
            return
    xf.AddScaleOp().Set(Gf.Vec3d(*s))

def _safe_set_rotate_xyz(prim, r):
    """Set rotateXYZ, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
            op.Set(Gf.Vec3d(*r))
            return
    xf.AddRotateXYZOp().Set(Gf.Vec3d(*r))
'''


# ── Code generation helpers ──────────────────────────────────────────────────

def _gen_create_prim(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_type = args["prim_type"]
    pos = args.get("position")
    scale = args.get("scale")
    rot = args.get("rotation_euler")
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.DefinePrim('{prim_path}', '{prim_type}')",
    ]
    if pos:
        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
    if scale:
        lines.append(f"_safe_set_scale(prim, ({scale[0]}, {scale[1]}, {scale[2]}))")
    if rot:
        lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
    return "\n".join(lines)


def _gen_delete_prim(args: Dict) -> str:
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"stage.RemovePrim('{args['prim_path']}')"
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


def _gen_add_reference(args: Dict) -> str:
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        f"prim.GetReferences().AddReference('{args['reference_path']}')"
    )


def _gen_apply_api_schema(args: Dict) -> str:
    schema = args['schema_name']
    prim_path = args['prim_path']
    # Map common schema names to their pxr module + class
    SCHEMA_MAP = {
        "PhysicsRigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "UsdPhysics.RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "PhysicsCollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "UsdPhysics.CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "PhysicsMassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "UsdPhysics.MassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "PhysxDeformableBodyAPI": ("pxr.PhysxSchema", "PhysxDeformableBodyAPI"),
        "PhysxCollisionAPI": ("pxr.PhysxSchema", "PhysxCollisionAPI"),
    }
    if schema in SCHEMA_MAP:
        mod, cls = SCHEMA_MAP[schema]
        return (
            f"from {mod} import {cls}\n"
            "import omni.usd\n"
            f"stage = omni.usd.get_context().get_stage()\n"
            f"prim = stage.GetPrimAtPath('{prim_path}')\n"
            f"{cls}.Apply(prim)"
        )
    # Fallback: try Kit command with correct name
    return (
        "import omni.usd\n"
        "import omni.kit.commands\n"
        f"stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        f"omni.kit.commands.execute('ApplyAPISchemaCommand', api='{schema}', prim=prim)"
    )


def _gen_clone_prim(args: Dict) -> str:
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


def _gen_deformable(args: Dict) -> str:
    """Generate PhysX deformable body/surface code from presets."""
    prim_path = args["prim_path"]
    sbt = args["soft_body_type"]

    presets = _load_deformable_presets().get("presets", {})

    # Map user-friendly names to preset keys
    preset_map = {
        "cloth": "cloth_cotton",
        "sponge": "sponge_soft",
        "rubber": "rubber_soft",
        "gel": "gel_soft",
        "rope": "rope_nylon",
    }
    preset_key = preset_map.get(sbt, f"{sbt}_soft")
    preset = presets.get(preset_key, {})
    params = preset.get("params", {})

    # Allow user overrides
    if args.get("youngs_modulus"):
        params["youngs_modulus"] = args["youngs_modulus"]
    if args.get("poissons_ratio"):
        params["poissons_ratio"] = args["poissons_ratio"]
    if args.get("damping"):
        params["damping"] = args["damping"]
    if args.get("self_collision") is not None:
        params["self_collision"] = args["self_collision"]

    api_type = preset.get("api", "PhysxDeformableBodyAPI")
    density = preset.get("density_kg_m3", 1000)

    if "Surface" in api_type:
        return _gen_deformable_surface(prim_path, params, density)
    return _gen_deformable_body(prim_path, params, density)


def _gen_deformable_body(prim_path: str, params: Dict, density: float) -> str:
    ym = params.get("youngs_modulus", 10000)
    pr = params.get("poissons_ratio", 0.3)
    damp = params.get("damping", 0.01)
    sc = str(params.get("self_collision", True))
    iters = params.get("solver_position_iteration_count", 32)
    vvd = params.get("vertex_velocity_damping", 0.05)

    return f"""\
import omni.usd
import numpy as np
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Gf, Vt, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure prim is a valid subdivided Mesh (PhysX requires triangle data)
if not prim.IsA(UsdGeom.Mesh):
    # Replace implicit surface (Plane, Cube, etc.) with a subdivided Mesh
    xform = UsdGeom.Xformable(prim)
    pos = xform.GetLocalTransformation().IsIdentity() and Gf.Vec3d(0,0,0) or \\
          xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    stage.RemovePrim('{prim_path}')
    prim = stage.DefinePrim('{prim_path}', 'Mesh')

mesh = UsdGeom.Mesh(prim)
pts = mesh.GetPointsAttr().Get()
if pts is None or len(pts) < 9:
    # Generate a 10x10 subdivided plane mesh
    res = 10
    size = 1.0
    verts = []
    for j in range(res + 1):
        for i in range(res + 1):
            x = (i / res - 0.5) * size
            y = (j / res - 0.5) * size
            verts.append(Gf.Vec3f(x, y, 0.0))
    faces = []
    counts = []
    for j in range(res):
        for i in range(res):
            v0 = j * (res + 1) + i
            v1 = v0 + 1
            v2 = v0 + (res + 1) + 1
            v3 = v0 + (res + 1)
            faces.extend([v0, v1, v2])
            faces.extend([v0, v2, v3])
            counts.extend([3, 3])
    mesh.GetPointsAttr().Set(Vt.Vec3fArray(verts))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(faces))

# Apply deformable body
deformable_api = PhysxSchema.PhysxDeformableBodyAPI.Apply(prim)
deformable_api.CreateSolverPositionIterationCountAttr({iters})
deformable_api.CreateVertexVelocityDampingAttr({vvd})
deformable_api.CreateSelfCollisionAttr({sc})

# Material
mat_path = '{prim_path}/DeformableMaterial'
mat_prim = stage.DefinePrim(mat_path, 'PhysxDeformableBodyMaterial')
mat_api = PhysxSchema.PhysxDeformableBodyMaterialAPI.Apply(mat_prim)
mat_api.CreateYoungsModulusAttr({ym})
mat_api.CreatePoissonsRatioAttr({pr})
mat_api.CreateDampingAttr({damp})
mat_api.CreateDensityAttr({density})

# Bind material
from pxr import UsdShade
UsdShade.MaterialBindingAPI(prim).Bind(
    UsdShade.Material(stage.GetPrimAtPath(mat_path)),
    UsdShade.Tokens.strongerThanDescendants)
"""


def _gen_deformable_surface(prim_path: str, params: Dict, density: float) -> str:
    ss = params.get("stretch_stiffness", 10000)
    bs = params.get("bend_stiffness", 0.02)
    damp = params.get("damping", 0.005)
    sc = str(params.get("self_collision", True))
    scfd = params.get("self_collision_filter_distance", 0.002)

    return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Gf, Vt, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure prim is a valid subdivided Mesh (PhysX cloth requires triangle data)
if not prim.IsA(UsdGeom.Mesh):
    xform = UsdGeom.Xformable(prim)
    pos = xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    stage.RemovePrim('{prim_path}')
    prim = stage.DefinePrim('{prim_path}', 'Mesh')
    UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(pos[0], pos[1], pos[2]))

mesh = UsdGeom.Mesh(prim)
pts = mesh.GetPointsAttr().Get()
if pts is None or len(pts) < 9:
    # Generate a 20x20 subdivided plane mesh for cloth simulation
    res = 20
    size = 1.0
    verts = []
    for j in range(res + 1):
        for i in range(res + 1):
            x = (i / res - 0.5) * size
            y = (j / res - 0.5) * size
            verts.append(Gf.Vec3f(x, y, 0.0))
    faces = []
    counts = []
    for j in range(res):
        for i in range(res):
            v0 = j * (res + 1) + i
            v1 = v0 + 1
            v2 = v0 + (res + 1) + 1
            v3 = v0 + (res + 1)
            faces.extend([v0, v1, v2])
            faces.extend([v0, v2, v3])
            counts.extend([3, 3])
    mesh.GetPointsAttr().Set(Vt.Vec3fArray(verts))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(faces))

# Apply deformable surface (cloth)
surface_api = PhysxSchema.PhysxDeformableSurfaceAPI.Apply(prim)
surface_api.CreateSelfCollisionAttr({sc})
surface_api.CreateSelfCollisionFilterDistanceAttr({scfd})

# Material
mat_path = '{prim_path}/ClothMaterial'
mat_prim = stage.DefinePrim(mat_path, 'PhysxDeformableSurfaceMaterial')
mat_api = PhysxSchema.PhysxDeformableSurfaceMaterialAPI.Apply(mat_prim)
mat_api.CreateStretchStiffnessAttr({ss})
mat_api.CreateBendStiffnessAttr({bs})
mat_api.CreateDampingAttr({damp})
mat_api.CreateDensityAttr({density})

# Bind material
from pxr import UsdShade
UsdShade.MaterialBindingAPI(prim).Bind(
    UsdShade.Material(stage.GetPrimAtPath(mat_path)),
    UsdShade.Tokens.strongerThanDescendants)
"""


# Isaac Sim 5.1 OmniGraph node type mapping:
# The LLM often uses legacy omni.isaac.* prefixes. Remap to the correct isaacsim.* types.
_OG_NODE_TYPE_MAP = {
    # ROS2 bridge nodes (Isaac Sim 5.1 uses isaacsim.ros2.bridge.*)
    "omni.isaac.ros2_bridge.ROS2Context": "isaacsim.ros2.bridge.ROS2Context",
    "omni.isaac.ros2_bridge.ROS2PublishClock": "isaacsim.ros2.bridge.ROS2PublishClock",
    "omni.isaac.ros2_bridge.ROS2PublishJointState": "isaacsim.ros2.bridge.ROS2PublishJointState",
    "omni.isaac.ros2_bridge.ROS2SubscribeJointState": "isaacsim.ros2.bridge.ROS2SubscribeJointState",
    "omni.isaac.ros2_bridge.ROS2PublishTransformTree": "isaacsim.ros2.bridge.ROS2PublishTransformTree",
    "omni.isaac.ros2_bridge.ROS2PublishImage": "isaacsim.ros2.bridge.ROS2PublishImage",
    # ArticulationController is in core.nodes, NOT ros2.bridge
    "omni.isaac.ros2_bridge.ROS2ArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
    "isaacsim.ros2.bridge.ROS2ArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
    "omni.isaac.core_nodes.IsaacArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
}


def _gen_create_omnigraph(args: Dict) -> str:
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


def _gen_assign_material(args: Dict) -> str:
    return (
        "import omni.usd\n"
        "from pxr import UsdShade\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"mat = UsdShade.Material(stage.GetPrimAtPath('{args['material_path']}'))\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        "UsdShade.MaterialBindingAPI(prim).Bind(mat, UsdShade.Tokens.strongerThanDescendants)"
    )


def _gen_sim_control(args: Dict) -> str:
    action = args["action"]
    if action == "play":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().play()"
    if action == "pause":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().pause()"
    if action == "stop":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().stop()"
    if action == "step":
        count = args.get("step_count", 1)
        return f"""\
import omni.timeline
tl = omni.timeline.get_timeline_interface()
for _ in range({count}):
    tl.forward_one_frame()
"""
    if action == "reset":
        return (
            "import omni.timeline\n"
            "tl = omni.timeline.get_timeline_interface()\n"
            "tl.stop()\n"
            "tl.set_current_time(0)"
        )
    return f"# Unknown sim action: {action}"


def _gen_set_physics_params(args: Dict) -> str:
    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, Gf",
        "stage = omni.usd.get_context().get_stage()",
        "scene = UsdPhysics.Scene.Get(stage, '/PhysicsScene') or UsdPhysics.Scene.Define(stage, '/PhysicsScene')",
    ]
    if "gravity_direction" in args and "gravity_magnitude" in args:
        d = args["gravity_direction"]
        m = args["gravity_magnitude"]
        lines.append(f"scene.GetGravityDirectionAttr().Set(Gf.Vec3f({d[0]}, {d[1]}, {d[2]}))")
        lines.append(f"scene.GetGravityMagnitudeAttr().Set({m})")
    elif "gravity_magnitude" in args:
        lines.append(f"scene.GetGravityMagnitudeAttr().Set({args['gravity_magnitude']})")
    if "time_step" in args:
        lines.append(f"# Note: Physics time step is set via settings")
        lines.append(f"import carb.settings")
        lines.append(f"carb.settings.get_settings().set('/persistent/physics/updateToUsd', True)")
        lines.append(f"carb.settings.get_settings().set('/persistent/physics/timeStepsPerSecond', int(1.0/{args['time_step']}))")
    return "\n".join(lines)


def _gen_teleport_prim(args: Dict) -> str:
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


def _gen_set_joint_targets(args: Dict) -> str:
    art_path = args["articulation_path"]
    joint = args.get("joint_name", "")
    pos = args.get("target_position")
    vel = args.get("target_velocity")
    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics",
        "stage = omni.usd.get_context().get_stage()",
    ]
    if joint:
        lines.append(f"joint_prim = stage.GetPrimAtPath('{art_path}/{joint}')")
        lines.append("drive = UsdPhysics.DriveAPI.Get(joint_prim, 'angular')")
        if pos is not None:
            lines.append(f"drive.GetTargetPositionAttr().Set({pos})")
        if vel is not None:
            lines.append(f"drive.GetTargetVelocityAttr().Set({vel})")
    return "\n".join(lines)


def _gen_import_robot(args: Dict) -> str:
    file_path = args["file_path"]
    fmt = args.get("format", "usd")
    dest = args.get("dest_path", "/World/Robot")

    # ── Asset directory from config (supports local path or Nucleus URL) ──
    _LOCAL_ASSETS = config.assets_root_path
    _ROBOTS_SUBDIR = config.assets_robots_subdir
    _ROBOTS_DIR = f"{_LOCAL_ASSETS}/{_ROBOTS_SUBDIR}" if _LOCAL_ASSETS else ""

    # Map common names → USD filenames within the robots subdirectory
    _ROBOT_NAME_MAP = {
        "franka": "franka.usd",
        "panda": "franka.usd",
        "franka_emika": "franka.usd",
        "spot": "spot.usd",
        "spot_with_arm": "spot_with_arm.usd",
        "carter": "carter_v1.usd",
        "nova_carter": "nova_carter.usd",
        "carter_v2": "carter_v2.usd",
        "jetbot": "jetbot.usd",
        "kaya": "kaya.usd",
        "ur10": "ur10.usd",
        "ur5": "ur5e.usd",
        "ur5e": "ur5e.usd",
        "anymal": "anymal_c.usd",
        "anymal_c": "anymal_c.usd",
        "anymal_d": "anymal_d.usd",
        "a1": "a1.usd",
        "go1": "go1.usd",
        "go2": "go2.usd",
        "h1": "h1.usd",
        "allegro": "allegro_hand.usd",
        "ridgeback_franka": "ridgeback_franka.usd",
        "humanoid": "humanoid.usd",
    }

    if fmt == "urdf":
        return f"""\
from isaacsim.asset.importer.urdf import _urdf
import omni.kit.commands
result, prim_path = omni.kit.commands.execute(
    "URDFParseAndImportFile",
    urdf_path="{file_path}",
    dest_path="{dest}",
)
"""

    # Resolve robot name for asset_library or named imports
    name_lower = file_path.lower().replace(" ", "_").replace("-", "_")
    local_file = _ROBOT_NAME_MAP.get(name_lower)

    if not _LOCAL_ASSETS and (fmt == "asset_library" or local_file):
        return (
            "# ERROR: ASSETS_ROOT_PATH is not configured in .env\n"
            "# Set ASSETS_ROOT_PATH to your local assets folder or Nucleus URL.\n"
            "# Example (local):   ASSETS_ROOT_PATH=/home/user/Desktop/assets\n"
            "# Example (Nucleus): ASSETS_ROOT_PATH=omniverse://localhost/NVIDIA/Assets/Isaac/5.1\n"
            "raise RuntimeError('ASSETS_ROOT_PATH not set in .env — cannot resolve robot assets')"
        )

    is_nucleus = _LOCAL_ASSETS.startswith("omniverse://")

    if fmt == "asset_library" or local_file:
        if local_file:
            resolved = f"{_ROBOTS_DIR}/{local_file}"
        else:
            resolved = f"{_ROBOTS_DIR}/{file_path}.usd"

        if is_nucleus:
            # Nucleus URL — no local file check, USD resolves directly
            return (
                "import omni.usd\n"
                "from pxr import UsdGeom, Gf\n"
                + _SAFE_XFORM_SNIPPET +
                "\nstage = omni.usd.get_context().get_stage()\n"
                f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
                f"prim.GetReferences().AddReference('{resolved}')\n"
                f"_safe_set_translate(prim, (0, 0, 0))"
            )
        else:
            # Local filesystem — validate the file exists
            return (
                "import omni.usd\n"
                "from pxr import UsdGeom, Gf\n"
                "import os\n"
                + _SAFE_XFORM_SNIPPET +
                "\nstage = omni.usd.get_context().get_stage()\n"
                f"asset_path = '{resolved}'\n"
                "if not os.path.exists(asset_path):\n"
                f"    raise FileNotFoundError(f'Robot asset not found: {{asset_path}}')\n"
                f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
                "prim.GetReferences().AddReference(asset_path)\n"
                f"_safe_set_translate(prim, (0, 0, 0))"
            )

    # Default: USD reference (absolute path or URL)
    return (
        "import omni.usd\n"
        "from pxr import UsdGeom, Gf\n"
        + _SAFE_XFORM_SNIPPET +
        "\nstage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
        f"prim.GetReferences().AddReference('{file_path}')\n"
        f"_safe_set_translate(prim, (0, 0, 0))"
    )


# ── Robot anchoring ──────────────────────────────────────────────────────────
# Isaac Sim robot USD assets contain a "rootJoint" (6-DOF free joint) that
# allows them to float freely. To anchor a robot:
# 1. Set PhysxArticulationAPI.fixedBase = True (keeps ArticulationRootAPI on root)
# 2. Delete the rootJoint (free joint)
# 3. Optionally create a FixedJoint to attach to a specific surface
# CRITICAL: Do NOT move ArticulationRootAPI — it must stay on the root prim
# or the tensor API pattern '/World/Robot' will fail with
# "Pattern did not match any articulations".

def _gen_anchor_robot(args: Dict) -> str:
    robot_path = args["robot_path"]
    anchor_surface = args.get("anchor_surface_path", "")
    base_link = args.get("base_link_name", "panda_link0")
    position = args.get("position")  # world position where robot sits

    # Build optional FixedJoint block for anchoring to a surface
    fixed_joint_block = ""
    if anchor_surface:
        local_pos_line = ""
        if position:
            local_pos_line = f"\n    anchor_prim.GetAttribute('physics:localPos0').Set(Gf.Vec3f({position[0]}, {position[1]}, {position[2]}))"
        fixed_joint_block = f"""
# Step 3: Create FixedJoint to attach to surface (excluded from articulation tree)
anchor_path = robot_path + '/AnchorJoint'
anchor_prim = stage.GetPrimAtPath(anchor_path)
if not anchor_prim.IsValid():
    anchor_prim = stage.DefinePrim(anchor_path, 'PhysicsFixedJoint')
    print(f"Created FixedJoint at {{anchor_path}}")
else:
    print(f"Reconfigured existing FixedJoint at {{anchor_path}}")

body0_rel = anchor_prim.GetRelationship('physics:body0')
if not body0_rel:
    body0_rel = anchor_prim.CreateRelationship('physics:body0')
body0_rel.SetTargets([Sdf.Path('{anchor_surface}')])

body1_rel = anchor_prim.GetRelationship('physics:body1')
if not body1_rel:
    body1_rel = anchor_prim.CreateRelationship('physics:body1')
body1_rel.SetTargets([Sdf.Path(base_link_path)])

anchor_prim.GetAttribute('physics:excludeFromArticulation').Set(True)
anchor_prim.GetAttribute('physics:jointEnabled').Set(True){local_pos_line}
print(f"Anchored to {anchor_surface}")
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema, Gf, Sdf

stage = omni.usd.get_context().get_stage()
robot_path = '{robot_path}'
base_link_path = robot_path + '/{base_link}'
robot_prim = stage.GetPrimAtPath(robot_path)

# Step 1: Set fixedBase=True on PhysxArticulationAPI
# This tells PhysX the root link is immovable (no need to move ArticulationRootAPI)
if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)
artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)
artic_api.CreateFixedBaseAttr(True)
print("Set fixedBase=True on PhysxArticulationAPI")

# Step 2: Delete the rootJoint (6-DOF free joint that lets the robot float)
root_joint_path = robot_path + '/rootJoint'
rj = stage.GetPrimAtPath(root_joint_path)
if rj.IsValid():
    stage.RemovePrim(root_joint_path)
    print(f"Deleted {{root_joint_path}} (6-DOF free joint)")
{fixed_joint_block}
print(f"Robot at {{robot_path}} is now anchored (fixedBase=True)")
print(f"ArticulationRootAPI remains on {{robot_path}} — tensor API patterns will work")
"""


def _gen_set_viewport_camera(args: Dict) -> str:
    return (
        "import omni.kit.viewport.utility\n"
        "vp_api = omni.kit.viewport.utility.get_active_viewport()\n"
        f"vp_api.camera_path = '{args['camera_path']}'"
    )


def _gen_configure_sdg(args: Dict) -> str:
    annotators = args.get("annotators", ["rgb", "bounding_box_2d"])
    num_frames = args.get("num_frames", 10)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    resolution = args.get("resolution", [1280, 720])

    ann_lines = "\n    ".join(
        f'rp.AnnotatorRegistry.get_annotator("{a}")' for a in annotators
    )

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.get.camera()
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir="{output_dir}", rgb=True,
                      bounding_box_2d={'bounding_box_2d' in annotators},
                      semantic_segmentation={'semantic_segmentation' in annotators},
                      instance_segmentation={'instance_segmentation' in annotators},
                      normals={'normals' in annotators},
                      distance_to_camera={'distance_to_camera' in annotators})
    writer.attach([rp])

    rep.orchestrator.run_until_complete(num_frames={num_frames})
"""


# ── Code generation dispatch ─────────────────────────────────────────────────

CODE_GEN_HANDLERS = {
    "create_prim": _gen_create_prim,
    "delete_prim": _gen_delete_prim,
    "set_attribute": _gen_set_attribute,
    "add_reference": _gen_add_reference,
    "apply_api_schema": _gen_apply_api_schema,
    "clone_prim": _gen_clone_prim,
    "create_deformable_mesh": _gen_deformable,
    "create_omnigraph": _gen_create_omnigraph,
    "create_material": _gen_create_material,
    "assign_material": _gen_assign_material,
    "sim_control": _gen_sim_control,
    "set_physics_params": _gen_set_physics_params,
    "teleport_prim": _gen_teleport_prim,
    "set_joint_targets": _gen_set_joint_targets,
    "import_robot": _gen_import_robot,
    "anchor_robot": _gen_anchor_robot,
    "set_viewport_camera": _gen_set_viewport_camera,
    "configure_sdg": _gen_configure_sdg,
}


# ── Spec / data lookup handlers (no code gen, just return data) ──────────────

async def _handle_lookup_product_spec(args: Dict) -> Dict:
    """Fuzzy-match a product name against the sensor specs database."""
    query = args.get("product_name", "").lower()
    specs = _load_sensor_specs()
    # Exact match first
    for s in specs:
        if s["product"].lower() == query:
            return {"found": True, "spec": s}
    # Substring match
    matches = [s for s in specs if query in s["product"].lower() or
               any(query in w.lower() for w in s["product"].split())]
    if matches:
        return {"found": True, "spec": matches[0], "alternatives": [m["product"] for m in matches[1:4]]}
    # Fuzzy by manufacturer or type
    by_type = [s for s in specs if query in s.get("type", "") or query in s.get("subtype", "")]
    if by_type:
        return {"found": False, "suggestions": [s["product"] for s in by_type[:5]],
                "message": f"No exact match for '{args['product_name']}'. Did you mean one of these?"}
    return {"found": False, "message": f"No sensor specs found for '{args['product_name']}'"}


async def _handle_scene_summary(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=False)
    if "error" in ctx:
        return ctx
    text = kit_tools.format_stage_context_for_llm(ctx)
    return {"summary": text}


async def _handle_capture_viewport(args: Dict) -> Dict:
    max_dim = args.get("max_dim", 1280)
    return await kit_tools.get_viewport_image(max_dim=max_dim)


async def _handle_get_console_errors(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=False)
    logs = ctx.get("recent_logs", [])
    min_level = args.get("min_level", "warning")
    level_order = ["verbose", "info", "warning", "error", "fatal"]
    min_idx = level_order.index(min_level) if min_level in level_order else 2
    filtered = [l for l in logs if level_order.index(l.get("level", "info")) >= min_idx]
    last_n = args.get("last_n", 50)
    return {"errors": filtered[-last_n:], "total_count": len(filtered)}


async def _handle_get_articulation_state(args: Dict) -> Dict:
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import UsdPhysics
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
joints = []
for child in prim.GetAllChildren():
    if child.HasAPI(UsdPhysics.RevoluteJointAPI) or child.HasAPI(UsdPhysics.PrismaticJointAPI):
        joints.append({{'name': child.GetName(), 'path': str(child.GetPath())}})
result = {{'articulation_path': '{prim_path}', 'joints': joints}}
print(json.dumps(result))
"""
    return await kit_tools.queue_exec_patch(code, f"Read articulation state for {prim_path}")


async def _handle_list_all_prims(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=True)
    return ctx.get("stage", {})


async def _handle_measure_distance(args: Dict) -> Dict:
    prim_a = args["prim_a"]
    prim_b = args["prim_b"]
    code = f"""\
import omni.usd
from pxr import UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
xf_a = UsdGeom.Xformable(stage.GetPrimAtPath('{prim_a}')).ComputeLocalToWorldTransform(0)
xf_b = UsdGeom.Xformable(stage.GetPrimAtPath('{prim_b}')).ComputeLocalToWorldTransform(0)
pos_a = xf_a.ExtractTranslation()
pos_b = xf_b.ExtractTranslation()
dist = (pos_a - pos_b).GetLength()
print(json.dumps({{'prim_a': '{prim_a}', 'prim_b': '{prim_b}', 'distance_m': dist,
       'position_a': list(pos_a), 'position_b': list(pos_b)}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Measure distance {prim_a} ↔ {prim_b}")


async def _handle_get_debug_info(args: Dict) -> Dict:
    """Return perf metrics via Kit RPC /context fallback."""
    ctx = await kit_tools.get_stage_context(full=False)
    return {
        "prim_count": ctx.get("stage", {}).get("prim_count"),
        "stage_url": ctx.get("stage", {}).get("stage_url"),
        "note": "Full perf metrics require Kit-side instrumentation",
    }


async def _handle_lookup_knowledge(args: Dict) -> Dict:
    """Search the version-specific knowledge base for code patterns and docs."""
    from ...retrieval.context_retriever import (
        retrieve_context,
        find_matching_patterns,
        detect_isaac_version,
    )
    query = args.get("query", "")
    version = detect_isaac_version()

    # Search FTS index
    fts_results = retrieve_context(query, version=version, limit=3)

    # Search code patterns
    patterns = find_matching_patterns(query, version=version, limit=3)

    results = []
    for r in fts_results:
        results.append({
            "source": r.get("source_id", "docs"),
            "section": r.get("section_path", ""),
            "content": r.get("content", "")[:600],
        })
    for p in patterns:
        results.append({
            "source": "code_patterns",
            "title": p.get("title", ""),
            "code": p.get("code", ""),
            "note": p.get("note", ""),
        })

    return {
        "version": version,
        "query": query,
        "results": results,
        "count": len(results),
    }


# ── PhysX regex for filtering physics-specific errors ──────────────────────
_PHYSX_ERROR_RE = re.compile(
    r"physx.*?error|px.*?error|physics.*?simulation.*?error|"
    r"articulation.*?error|joint.*?error",
    re.IGNORECASE,
)


async def _handle_get_physics_errors(args: Dict) -> Dict:
    """Filter console logs for PhysX-specific errors and warnings."""
    ctx = await kit_tools.get_stage_context(full=False)
    logs = ctx.get("recent_logs", [])
    last_n = args.get("last_n", 20)

    physics_logs = []
    for entry in logs:
        msg = entry.get("msg", "")
        source = entry.get("source", "")
        # Match PhysX regex OR source contains physics/physx
        if (_PHYSX_ERROR_RE.search(msg) or
                "physx" in source.lower() or
                "physics" in source.lower()):
            physics_logs.append(entry)

    return {
        "physics_errors": physics_logs[-last_n:],
        "total_count": len(physics_logs),
        "note": "Filtered for PhysX/physics engine messages only",
    }


async def _handle_check_collisions(args: Dict) -> Dict:
    """Validate collision meshes on a prim via Kit RPC."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import UsdPhysics, UsdGeom, PhysxSchema
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
if not prim.IsValid():
    print(json.dumps({{"valid": False, "error": "Prim not found: {prim_path}"}}))
else:
    has_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
    has_rigid_body = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    has_mass = prim.HasAPI(UsdPhysics.MassAPI)

    # Count mesh children that could serve as collision geometry
    mesh_count = 0
    collision_children = 0
    for child in prim.GetAllDescendants():
        if child.IsA(UsdGeom.Mesh):
            mesh_count += 1
        if child.HasAPI(UsdPhysics.CollisionAPI):
            collision_children += 1

    # Check for explicit collision geometry (MeshCollisionAPI or simple shape)
    has_mesh_collision = prim.HasAPI(PhysxSchema.PhysxCollisionAPI)

    result = {{
        "valid": True,
        "prim_path": '{prim_path}',
        "has_collision_api": has_collision,
        "has_rigid_body_api": has_rigid_body,
        "has_mass_api": has_mass,
        "has_physx_collision": has_mesh_collision,
        "mesh_children": mesh_count,
        "children_with_collision": collision_children,
        "issues": [],
    }}
    if not has_collision and collision_children == 0:
        result["issues"].append("No CollisionAPI on prim or any children — physics contacts will not register")
    if has_rigid_body and not has_collision and collision_children == 0:
        result["issues"].append("RigidBodyAPI without any collision — prim will fall through everything")
    if mesh_count > 0 and not has_collision and collision_children == 0:
        result["issues"].append("Mesh geometry exists but no collision applied — apply CollisionAPI")

    print(json.dumps(result))
"""
    result = await kit_tools.exec_sync(code)
    if result.get("success") and result.get("output"):
        try:
            return {"type": "data", **json.loads(result["output"].strip())}
        except json.JSONDecodeError:
            pass
    return {"type": "data", "error": result.get("output", "Failed to check collisions")}


def _handle_fix_error(args: Dict) -> str:
    """Generate a fix code patch for a known physics/USD error pattern."""
    error_text = args.get("error_text", "")
    error_lower = error_text.lower()

    # ── Categorize the error ──────────────────────────────────────────────
    category = "unknown"
    if any(kw in error_lower for kw in ("collision", "collider", "collisionapi", "pass through")):
        category = "collision"
    elif any(kw in error_lower for kw in ("joint", "jointapi", "body0", "body1", "joint path")):
        category = "joint"
    elif any(kw in error_lower for kw in ("solver", "iteration", "diverge", "explod", "unstable")):
        category = "solver"
    elif any(kw in error_lower for kw in ("ground", "floor", "falling", "fall through")):
        category = "ground_plane"
    elif any(kw in error_lower for kw in ("omnigraph", "og.", "node type", "action graph")):
        category = "omnigraph"
    elif any(kw in error_lower for kw in ("articulation", "articulationapi")):
        category = "articulation"
    elif any(kw in error_lower for kw in ("usd", "prim", "attribute", "schema")):
        category = "usd"

    # ── Query knowledge base for known fixes ──────────────────────────────
    kb_snippets = []
    try:
        from ...retrieval.context_retriever import find_matching_patterns, detect_isaac_version
        version = detect_isaac_version()
        patterns = find_matching_patterns(error_text, version=version, limit=3)
        for p in patterns:
            if p.get("code"):
                kb_snippets.append(f"# KB pattern: {p.get('title', 'fix')}\n{p['code']}")
    except Exception:
        pass  # KB not available — fall back to built-in fixes

    # ── Generate fix code based on category ───────────────────────────────
    if category == "collision":
        code = """\
import omni.usd
from pxr import UsdPhysics, UsdGeom

stage = omni.usd.get_context().get_stage()
# Apply CollisionAPI to all Mesh prims missing it
fixed = []
for prim in stage.Traverse():
    if prim.IsA(UsdGeom.Mesh) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(prim)
        fixed.append(str(prim.GetPath()))
print(f"Applied CollisionAPI to {len(fixed)} prims: {fixed[:10]}")
"""

    elif category == "solver":
        code = """\
import omni.usd
from pxr import UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
# Find or create PhysicsScene and increase solver iterations
scene_prim = None
for prim in stage.Traverse():
    if prim.IsA(UsdPhysics.Scene):
        scene_prim = prim
        break
if scene_prim is None:
    scene_prim = UsdPhysics.Scene.Define(stage, '/PhysicsScene').GetPrim()

physx_scene = PhysxSchema.PhysxSceneAPI.Apply(scene_prim)
physx_scene.CreateMinPositionIterationCountAttr(16)
physx_scene.CreateMinVelocityIterationCountAttr(4)
physx_scene.CreateEnableStabilizationAttr(True)
print("Increased solver iterations and enabled stabilization")
"""

    elif category == "joint":
        code = """\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
# Scan joints and report broken body references
issues = []
for prim in stage.Traverse():
    joint = UsdPhysics.Joint(prim)
    if not joint:
        continue
    rel0 = prim.GetRelationship('physics:body0')
    rel1 = prim.GetRelationship('physics:body1')
    targets0 = rel0.GetTargets() if rel0 else []
    targets1 = rel1.GetTargets() if rel1 else []
    for t in targets0 + targets1:
        if not stage.GetPrimAtPath(t).IsValid():
            issues.append(f"Joint {prim.GetPath()} references missing prim: {t}")
print(f"Joint scan complete. Issues found: {len(issues)}")
for issue in issues:
    print(f"  - {issue}")
"""

    elif category == "ground_plane":
        code = """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf, Sdf

stage = omni.usd.get_context().get_stage()

# Create ground plane if none exists
ground_path = '/World/GroundPlane'
if not stage.GetPrimAtPath(ground_path).IsValid():
    xform = UsdGeom.Xform.Define(stage, ground_path)
    plane = UsdGeom.Mesh.Define(stage, f'{ground_path}/CollisionMesh')
    plane.GetPointsAttr().Set([(-50,-50,0),(50,-50,0),(50,50,0),(-50,50,0)])
    plane.GetFaceVertexCountsAttr().Set([4])
    plane.GetFaceVertexIndicesAttr().Set([0,1,2,3])
    UsdPhysics.CollisionAPI.Apply(plane.GetPrim())
    print(f"Created ground plane at {ground_path}")

# Also ensure PhysicsScene exists with gravity
scene_path = '/PhysicsScene'
if not stage.GetPrimAtPath(scene_path).IsValid():
    scene = UsdPhysics.Scene.Define(stage, scene_path)
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)
    print("Created PhysicsScene with gravity (0, 0, -9.81)")
"""

    elif category == "omnigraph":
        code = """\
import omni.graph.core as og

# List all graphs and their evaluation state
graphs = og.get_all_graphs()
for g in graphs:
    path = g.get_path_to_graph()
    valid = g.is_valid()
    nodes = g.get_nodes()
    print(f"Graph: {path}, valid={valid}, nodes={len(nodes)}")
    for n in nodes:
        print(f"  Node: {n.get_prim_path()}, type={n.get_type_name()}")
"""

    elif category == "articulation":
        code = """\
import omni.usd
from pxr import UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
# Find articulations and verify their setup
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        path = str(prim.GetPath())
        has_rb = prim.HasAPI(UsdPhysics.RigidBodyAPI)
        physx_art = PhysxSchema.PhysxArticulationAPI(prim) if prim.HasAPI(PhysxSchema.PhysxArticulationAPI) else None
        fixed = physx_art.GetArticulationEnabledAttr().Get() if physx_art else None
        print(f"Articulation: {path}, has_rigid_body={has_rb}, physx_enabled={fixed}")
        # Count joints
        joint_count = 0
        for child in prim.GetAllDescendants():
            if child.HasAPI(UsdPhysics.RevoluteJointAPI) or child.HasAPI(UsdPhysics.PrismaticJointAPI):
                joint_count += 1
        print(f"  Joints: {joint_count}")
"""

    else:
        # Unknown category — generate diagnostic code
        code = """\
import omni.usd
from pxr import UsdPhysics, UsdGeom

stage = omni.usd.get_context().get_stage()
# Diagnostic: scan scene for common physics issues
issues = []
mesh_no_collision = 0
rigid_no_collision = 0
for prim in stage.Traverse():
    if prim.IsA(UsdGeom.Mesh) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        mesh_no_collision += 1
    if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        rigid_no_collision += 1
        issues.append(f"RigidBody without collision: {prim.GetPath()}")

has_scene = any(p.IsA(UsdPhysics.Scene) for p in stage.Traverse())
print(f"Physics scene exists: {has_scene}")
print(f"Meshes without collision: {mesh_no_collision}")
print(f"RigidBodies without collision: {rigid_no_collision}")
for i in issues[:10]:
    print(f"  - {i}")
"""

    # Prepend KB snippets as comments if available
    if kb_snippets:
        kb_header = "\n".join(f"# {line}" for snippet in kb_snippets
                              for line in snippet.split("\n"))
        code = f"# Knowledge base matches for this error:\n{kb_header}\n\n{code}"

    return code


CODE_GEN_HANDLERS["fix_error"] = _handle_fix_error


# Data-only handlers (no code gen → return data directly to LLM)
DATA_HANDLERS = {
    "lookup_product_spec": _handle_lookup_product_spec,
    "scene_summary": _handle_scene_summary,
    "capture_viewport": _handle_capture_viewport,
    "get_console_errors": _handle_get_console_errors,
    "get_articulation_state": _handle_get_articulation_state,
    "list_all_prims": _handle_list_all_prims,
    "measure_distance": _handle_measure_distance,
    "get_debug_info": _handle_get_debug_info,
    "lookup_knowledge": _handle_lookup_knowledge,
    "explain_error": None,  # handled inline by LLM (no tool execution)
    "get_physics_errors": _handle_get_physics_errors,
    "check_collisions": _handle_check_collisions,
}

# ── ROS2 live handlers (via rosbridge / ros-mcp) ────────────────────────────
try:
    from .ros_mcp_tools import (
        handle_ros2_connect,
        handle_ros2_list_topics,
        handle_ros2_get_topic_type,
        handle_ros2_get_message_type,
        handle_ros2_subscribe_once,
        handle_ros2_publish,
        handle_ros2_publish_sequence,
        handle_ros2_list_services,
        handle_ros2_call_service,
        handle_ros2_list_nodes,
        handle_ros2_get_node_details,
    )
    DATA_HANDLERS.update({
        "ros2_connect": handle_ros2_connect,
        "ros2_list_topics": handle_ros2_list_topics,
        "ros2_get_topic_type": handle_ros2_get_topic_type,
        "ros2_get_message_type": handle_ros2_get_message_type,
        "ros2_subscribe_once": handle_ros2_subscribe_once,
        "ros2_publish": handle_ros2_publish,
        "ros2_publish_sequence": handle_ros2_publish_sequence,
        "ros2_list_services": handle_ros2_list_services,
        "ros2_call_service": handle_ros2_call_service,
        "ros2_list_nodes": handle_ros2_list_nodes,
        "ros2_get_node_details": handle_ros2_get_node_details,
    })
except ImportError:
    logger.warning("[ToolExecutor] ros-mcp not installed — ROS2 live tools disabled (pip install ros-mcp)")
    DATA_HANDLERS.update({
        "ros2_list_topics": None,
        "ros2_publish": None,
    })


# ── Main dispatch ────────────────────────────────────────────────────────────

async def execute_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a single tool call and return the result dict.

    Returns:
        {"type": "code_patch", "code": ..., "description": ...}  for code-gen tools
        {"type": "data", ...}                                      for data-lookup tools
        {"type": "error", "error": ...}                            on failure
    """
    logger.info(f"[ToolExecutor] Executing tool: {tool_name}({json.dumps(arguments)[:200]})")

    try:
        # 1. Data handlers — return result directly
        if tool_name in DATA_HANDLERS:
            handler = DATA_HANDLERS[tool_name]
            if handler is None:
                # Tool handled inline by LLM, no execution needed
                return {"type": "data", "note": f"{tool_name} is handled by the LLM reasoning, no live execution needed."}
            result = await handler(arguments)
            return {"type": "data", **result}

        # 2. run_usd_script — pass through to Kit
        if tool_name == "run_usd_script":
            code = arguments.get("code", "")
            desc = arguments.get("description", "Run custom script")
            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}
            result = await kit_tools.queue_exec_patch(code, desc)
            return {"type": "code_patch", "code": code, "description": desc, "queued": result.get("queued", False)}

        # 3. Code generation tools — generate code, send to Kit for approval
        if tool_name in CODE_GEN_HANDLERS:
            gen_fn = CODE_GEN_HANDLERS[tool_name]
            code = gen_fn(arguments)
            desc = f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(arguments.items())[:3])})"

            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}

            # Add sensor spec auto-lookup for add_sensor_to_prim
            if tool_name == "add_sensor_to_prim" and arguments.get("product_name"):
                spec_result = await _handle_lookup_product_spec({"product_name": arguments["product_name"]})
                if spec_result.get("found"):
                    return {
                        "type": "code_patch_with_spec",
                        "code": code,
                        "description": desc,
                        "product_spec": spec_result["spec"],
                    }

            result = await kit_tools.queue_exec_patch(code, desc)
            return {
                "type": "code_patch",
                "code": code,
                "description": desc,
                "queued": result.get("queued", False),
            }

        return {"type": "error", "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"[ToolExecutor] {tool_name} failed: {e}")
        return {"type": "error", "error": str(e)}


def _gen_add_sensor(args: Dict) -> str:
    """Generate code for adding a sensor based on type and optional product spec."""
    prim_path = args["prim_path"]
    sensor_type = args["sensor_type"]

    if sensor_type == "camera":
        fov = args.get("fov", 60)
        res = args.get("resolution", [1280, 720])
        return f"""\
import omni.usd
from pxr import UsdGeom, Sdf, Gf

stage = omni.usd.get_context().get_stage()
cam_path = '{prim_path}/Camera'
cam = UsdGeom.Camera.Define(stage, cam_path)
cam.GetHorizontalApertureAttr().Set(20.955)
cam.GetFocalLengthAttr().Set(10.0 * 20.955 / (2.0 * __import__('math').tan(__import__('math').radians({fov}/2))))
cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))
"""
    if sensor_type == "rtx_lidar":
        return f"""\
import omni.usd
from pxr import UsdGeom, Gf
{_SAFE_XFORM_SNIPPET}
stage = omni.usd.get_context().get_stage()
lidar_path = '{prim_path}/RTXLidar'
lidar_prim = stage.DefinePrim(lidar_path, 'Camera')
_safe_set_translate(lidar_prim, (0, 0, 0.1))

# Configure RTX Lidar via Isaac Sim extension
from isaacsim.sensor.schema import LidarRtx
lidar = LidarRtx(prim_path=lidar_path)
"""
    if sensor_type == "imu":
        return f"""\
from isaacsim.sensor.schema import IMUSensor
imu = IMUSensor(prim_path='{prim_path}/IMU')
"""
    if sensor_type == "contact_sensor":
        return f"""\
from isaacsim.sensor.schema import ContactSensor
contact = ContactSensor(prim_path='{prim_path}/ContactSensor')
"""
    return f"# Sensor type '{sensor_type}' not yet implemented"


# Register the sensor generator
CODE_GEN_HANDLERS["add_sensor_to_prim"] = _gen_add_sensor


# ── Motion Planning (RMPflow / Lula) ─────────────────────────────────────────

# Robot config map: robot_type → (rmpflow_config_dir, robot_description_path, urdf_path, end_effector_frame)
_MOTION_ROBOT_CONFIGS = {
    "franka": {
        "rmp_config": "franka/rmpflow",
        "desc": "franka/robot_descriptor.yaml",
        "urdf": "franka/lula_franka_gen.urdf",
        "ee_frame": "panda_hand",
    },
    "ur10": {
        "rmp_config": "universal_robots/ur10/rmpflow",
        "desc": "universal_robots/ur10/robot_descriptor.yaml",
        "urdf": "universal_robots/ur10/lula_ur10_gen.urdf",
        "ee_frame": "ee_link",
    },
    "ur5e": {
        "rmp_config": "universal_robots/ur5e/rmpflow",
        "desc": "universal_robots/ur5e/robot_descriptor.yaml",
        "urdf": "universal_robots/ur5e/lula_ur5e_gen.urdf",
        "ee_frame": "ee_link",
    },
    "cobotta": {
        "rmp_config": "denso/cobotta_pro_900/rmpflow",
        "desc": "denso/cobotta_pro_900/robot_descriptor.yaml",
        "urdf": "denso/cobotta_pro_900/lula_cobotta_gen.urdf",
        "ee_frame": "onrobot_rg6_base_link",
    },
}


def _gen_move_to_pose(args: Dict) -> str:
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    planner = args.get("planner", "rmpflow")
    robot_type = args.get("robot_type", "franka").lower()
    position_threshold = args.get("position_threshold", 0.01)
    max_steps = args.get("max_steps", 1000)

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee = cfg["ee_frame"]

    if planner == "lula_rrt":
        # Global planner — single-shot path plan using LulaRRTMotionPolicy
        # NOTE: Lula RRT does NOT support orientation targets — only position
        lines = [
            "import omni.usd",
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import LulaRRTMotionPolicy",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "from isaacsim.core.prims import SingleArticulation",
            "",
            "# Load Lula RRT planner config",
            f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
            f"rrt = LulaRRTMotionPolicy(**rrt_config)",
            "",
            f"target_pos = np.array({list(target_pos)})",
        ]
        if target_ori:
            lines.extend([
                f"# WARNING: Lula RRT does not support orientation targets — orientation will be ignored",
                f"print('WARNING: Lula RRT ignores orientation targets. Only position [{target_pos[0]}, {target_pos[1]}, {target_pos[2]}] will be used.')",
            ])
        lines.extend([
            "",
            f"# Get the articulation",
            f"art = SingleArticulation(prim_path='{art_path}')",
            "art.initialize()",
            "",
            "# Set position-only target",
            "rrt.set_end_effector_target(target_pos)",
            "",
            "# Compute and apply single-shot trajectory",
            "joint_positions = art.get_joint_positions()",
            "joint_velocities = art.get_joint_velocities()",
            "action = rrt.get_next_articulation_action(joint_positions, joint_velocities)",
            "art.apply_action(action)",
            f"print(f'Lula RRT: planned path to {{target_pos}} — action applied')",
        ])
        return "\n".join(lines)

    # Default: RMPflow (reactive, real-time)
    # RMPflow is a step-wise reactive policy — must run every physics step
    # until convergence or timeout
    lines = [
        "import omni.usd",
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import RmpFlow, ArticulationMotionPolicy",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "from isaacsim.core.prims import SingleArticulation",
        "from isaacsim.core.api import World",
        "",
        "# Load RMPflow config for the robot",
        f"rmpflow_config = interface_config_loader.load_supported_motion_policy_config('{robot_type}', 'RMPflow')",
        "rmpflow = RmpFlow(**rmpflow_config)",
        "",
        f"# Get the articulation",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "world = World.instance()",
        "if world is None:",
        "    world = World()",
        "art.initialize()",
        "",
        "# Set robot base pose if not at world origin",
        "robot_base_pos, robot_base_rot = art.get_world_pose()",
        "if np.linalg.norm(robot_base_pos) > 1e-6:",
        "    rmpflow.set_robot_base_pose(robot_base_pos, robot_base_rot)",
        "",
        "# Set target",
        f"target_pos = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_ori = np.array({list(target_ori)})")
    else:
        lines.append("target_ori = None")
    lines.extend([
        "rmpflow.set_end_effector_target(target_pos, target_ori)",
        "",
        "# RMPflow must run every physics step until convergence",
        f"_rmpflow_steps = 0",
        f"_rmpflow_max_steps = {max_steps}",
        f"_rmpflow_threshold = {position_threshold}",
        "",
        "def _on_rmpflow_physics_step(dt):",
        "    global _rmpflow_steps",
        "    _rmpflow_steps += 1",
        "    joint_positions = art.get_joint_positions()",
        "    joint_velocities = art.get_joint_velocities()",
        "    rmpflow.update_world()  # Required for dynamic obstacle avoidance",
        "    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)",
        "    art.apply_action(action)",
        "",
        "    # Convergence check: end-effector close enough to target",
        "    ee_pos, _ = art.get_world_pose()",
        "    dist = np.linalg.norm(ee_pos - target_pos)",
        "    if dist < _rmpflow_threshold:",
        f"        print(f'RMPflow: {ee} reached target (dist={{dist:.4f}}m) in {{_rmpflow_steps}} steps')",
        "        world.remove_physics_callback('rmpflow_step')",
        "    elif _rmpflow_steps >= _rmpflow_max_steps:",
        f"        print(f'RMPflow: timeout after {{_rmpflow_max_steps}} steps (dist={{dist:.4f}}m from target)')",
        "        world.remove_physics_callback('rmpflow_step')",
        "",
        "world.add_physics_callback('rmpflow_step', _on_rmpflow_physics_step)",
        f"print(f'RMPflow: physics callback registered — {ee} moving to {{target_pos}}')",
    ])
    return "\n".join(lines)


def _gen_plan_trajectory(args: Dict) -> str:
    art_path = args["articulation_path"]
    waypoints = args["waypoints"]
    robot_type = args.get("robot_type", "franka").lower()

    positions_str = "[" + ", ".join(
        f"np.array({list(wp['position'])})" for wp in waypoints
    ) + "]"
    orientations = [wp.get("orientation") for wp in waypoints]
    has_ori = any(o is not None for o in orientations)

    # Lula RRT does NOT support orientation targets — warn if provided
    lines = [
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import LulaRRTMotionPolicy",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "",
        f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
        f"planner = LulaRRTMotionPolicy(**rrt_config)",
        "",
        f"positions = {positions_str}",
    ]
    if has_ori:
        lines.extend([
            "",
            "# WARNING: Lula RRT does not support orientation targets — orientations ignored",
            "print('WARNING: Lula RRT ignores orientation targets. Only positions will be used.')",
        ])
    lines.extend([
        "",
        "# Plan through waypoints (position-only)",
        "planned_actions = []",
        "for i, pos in enumerate(positions):",
        "    planner.set_end_effector_target(pos)",
        "    print(f'Planned waypoint {i+1}/{len(positions)}: {pos}')",
        "",
        f"print(f'Planned trajectory through {len(waypoints)} waypoints')",
    ])
    return "\n".join(lines)


CODE_GEN_HANDLERS["move_to_pose"] = _gen_move_to_pose
CODE_GEN_HANDLERS["plan_trajectory"] = _gen_plan_trajectory


# ── Asset Catalog Search ─────────────────────────────────────────────────────

_asset_index: Optional[List[Dict]] = None

# Robot name map (module-level copy for catalog indexing)
_CATALOG_ROBOTS = {
    "franka": "franka.usd",
    "panda": "franka.usd",
    "spot": "spot.usd",
    "spot_with_arm": "spot_with_arm.usd",
    "carter": "carter_v1.usd",
    "jetbot": "jetbot.usd",
    "kaya": "kaya.usd",
    "ur10": "ur10.usd",
    "ur5e": "ur5e.usd",
    "anymal_c": "anymal_c.usd",
    "anymal_d": "anymal_d.usd",
    "a1": "a1.usd",
    "go1": "go1.usd",
    "go2": "go2.usd",
    "h1": "h1.usd",
    "allegro_hand": "allegro_hand.usd",
    "ridgeback_franka": "ridgeback_franka.usd",
    "humanoid": "humanoid.usd",
}


_ASSET_CACHE_PATH = _WORKSPACE / "knowledge" / "asset_index.jsonl"
_ASSET_CACHE_MAX_AGE_S = 86400  # 24 hours


def _build_asset_index() -> List[Dict]:
    """Walk asset directories and build a searchable index of USD files.

    Results are cached in-memory and persisted to a JSONL file at
    ``workspace/knowledge/asset_index.jsonl``.  If the cache file exists and is
    less than 24 h old it is loaded directly instead of re-scanning.

    NOTE: This uses filesystem walking (``Path.rglob``) which works when the
    service runs outside Kit.  Nucleus-hosted assets would need a Kit RPC proxy
    (``omni.client.list()`` only works inside the Kit process).
    """
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    # ── Try loading from persistent JSONL cache ───────────────────────────
    if _ASSET_CACHE_PATH.exists():
        try:
            age = time.time() - _ASSET_CACHE_PATH.stat().st_mtime
            if age < _ASSET_CACHE_MAX_AGE_S:
                cached: List[Dict] = []
                for line in _ASSET_CACHE_PATH.read_text().splitlines():
                    line = line.strip()
                    if line:
                        cached.append(json.loads(line))
                if cached:
                    _asset_index = cached
                    logger.debug("Loaded %d assets from cache (%s)", len(cached), _ASSET_CACHE_PATH)
                    return _asset_index
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Asset cache read failed, will re-scan: %s", exc)

    index: List[Dict] = []

    # 1. Robots from the known robot name map
    robots_dir = ""
    assets_root = getattr(config, "assets_root_path", None)
    robots_sub = getattr(config, "assets_robots_subdir", None)
    if assets_root and robots_sub:
        robots_dir = f"{assets_root}/{robots_sub}"
    for name, filename in _CATALOG_ROBOTS.items():
        index.append({
            "name": name,
            "type": "robot",
            "path": f"{robots_dir}/{filename}" if robots_dir else filename,
            "source": "robot_library",
        })

    # 2. Walk user asset dirs if configured
    search_dirs = []
    assets_root = getattr(config, "assets_root_path", None)
    if assets_root:
        search_dirs.append(Path(assets_root))

    # 3. Walk workspace/knowledge for any asset manifests
    manifest_path = _WORKSPACE / "knowledge" / "asset_manifest.jsonl"
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    index.append(entry)
                except json.JSONDecodeError:
                    pass

    # 4. Walk asset directories for USD/USDZ/USDA files
    for d in search_dirs:
        if not d.exists():
            continue
        try:
            for f in d.rglob("*"):
                if f.suffix.lower() in (".usd", ".usda", ".usdz"):
                    rel = f.relative_to(d)
                    name_parts = rel.stem.replace("_", " ").replace("-", " ")
                    # Infer type from path
                    path_str = str(rel).lower()
                    if any(k in path_str for k in ("robot", "arm", "gripper", "manipulator")):
                        atype = "robot"
                    elif any(k in path_str for k in ("env", "room", "warehouse", "house", "kitchen")):
                        atype = "environment"
                    elif any(k in path_str for k in ("sensor", "camera", "lidar")):
                        atype = "sensor"
                    elif any(k in path_str for k in ("material", "mdl", "texture")):
                        atype = "material"
                    else:
                        atype = "prop"
                    index.append({
                        "name": name_parts,
                        "type": atype,
                        "path": str(f),
                        "source": "filesystem",
                        "rel_path": str(rel),
                    })
        except PermissionError:
            pass

    _asset_index = index

    # ── Write persistent JSONL cache ──────────────────────────────────────
    try:
        _ASSET_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_ASSET_CACHE_PATH, "w") as fh:
            for entry in index:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        logger.debug("Wrote asset cache with %d entries to %s", len(index), _ASSET_CACHE_PATH)
    except OSError as exc:
        logger.warning("Failed to write asset cache: %s", exc)

    return _asset_index


async def _handle_catalog_search(args: Dict) -> Dict:
    """Fuzzy-match assets by name, type, and path.

    Scoring uses ``difflib.SequenceMatcher`` for fuzzy name matching so that
    queries like "frank" still match "franka" and minor typos are tolerated.
    """
    query = args.get("query", "").lower()
    asset_type = args.get("asset_type", "any").lower()
    limit = args.get("limit", 10)

    index = _build_asset_index()
    scored: List[tuple] = []
    query_words = query.split()

    for asset in index:
        if asset_type != "any" and asset.get("type", "any") != asset_type:
            continue

        name = asset.get("name", "").lower()
        path = asset.get("path", "").lower()
        rel_path = asset.get("rel_path", "").lower()
        searchable = f"{name} {path} {rel_path}"

        # --- Fuzzy name similarity via SequenceMatcher ---
        fuzzy_name = difflib.SequenceMatcher(None, query, name).ratio()
        fuzzy_path = difflib.SequenceMatcher(None, query, rel_path).ratio() if rel_path else 0.0

        # Score: exact match > all-words present > fuzzy similarity > partial words
        if query == name:
            score = 100.0
        elif all(w in searchable for w in query_words):
            score = 70.0 + sum(10 for w in query_words if w in name)
        elif fuzzy_name >= 0.6 or fuzzy_path >= 0.5:
            # Fuzzy match — scale ratio into 30-69 range
            score = 30.0 + max(fuzzy_name, fuzzy_path) * 40.0
        elif any(w in searchable for w in query_words):
            score = sum(10 for w in query_words if w in searchable)
        else:
            continue

        scored.append((score, asset))

    scored.sort(key=lambda x: -x[0])
    results = [a for _, a in scored[:limit]]

    return {
        "query": args.get("query", ""),
        "results": results,
        "total_matches": len(scored),
        "index_size": len(index),
    }


DATA_HANDLERS["catalog_search"] = _handle_catalog_search


# ── Scene Builder ────────────────────────────────────────────────────────────

def _gen_build_scene_from_blueprint(args: Dict) -> str:
    """Generate code to build a scene from a structured blueprint."""
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])
    dry_run = args.get("dry_run", False)

    if not objects:
        return "print('Empty blueprint — nothing to build')\n"

    lines = [
        "# SCENE BLUEPRINT BUILD — multi-object atomic operation.",
        "# A snapshot should be taken before execution so the entire build",
        "# can be rolled back as a single unit via the snapshot manager.",
        "import omni.usd",
        "from pxr import UsdGeom, Gf, Sdf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        "",
    ]

    for i, obj in enumerate(objects):
        name = obj.get("name", f"object_{i}")
        asset_path = obj.get("asset_path", "")
        prim_path = obj.get("prim_path", f"/World/{name}")
        pos = obj.get("position", [0, 0, 0])
        rot = obj.get("rotation", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])
        prim_type = obj.get("prim_type")  # for simple prims (Cube, etc.)

        lines.append(f"# --- {name} ---")
        if asset_path:
            # Import via USD reference
            lines.append(f"prim = stage.DefinePrim('{prim_path}', 'Xform')")
            lines.append(f"prim.GetReferences().AddReference('{asset_path}')")
        elif prim_type:
            lines.append(f"prim = stage.DefinePrim('{prim_path}', '{prim_type}')")
        else:
            lines.append(f"prim = stage.DefinePrim('{prim_path}', 'Xform')")

        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
        if rot != [0, 0, 0]:
            lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
        if scale != [1, 1, 1]:
            lines.append(f"_safe_set_scale(prim, ({scale[0]}, {scale[1]}, {scale[2]}))")
        lines.append("")

    lines.append(f"print('Scene built: {len(objects)} objects placed')")

    if dry_run:
        return f"# DRY RUN — code preview only\n" + "\n".join(lines)
    return "\n".join(lines)


async def _handle_generate_scene_blueprint(args: Dict) -> Dict:
    """Generate a scene blueprint (data, not code). The LLM fills in the spatial layout."""
    description = args.get("description", "")
    room_dims = args.get("room_dimensions") or [6, 6, 3]
    available = args.get("available_assets")

    # If no assets provided, search the catalog
    if not available:
        catalog_result = await _handle_catalog_search({"query": description, "limit": 20})
        available = catalog_result.get("results", [])

    return {
        "type": "blueprint_request",
        "description": description,
        "room_dimensions": room_dims,
        "available_assets": available,
        "instructions": (
            "All coordinates are in meters. Z-axis is up. "
            f"Room dimensions: {room_dims[0]}m x {room_dims[1]}m x {room_dims[2]}m (X x Y x Z). "
            "Generate a blueprint JSON following these steps in order:\n"
            "Step 1: Define room boundaries and anchor surfaces — place ground plane / floor at z=0, "
            "walls along room edges.\n"
            "Step 2: Place large fixed objects — tables, shelves, workbenches, fixtures. "
            "Keep within room bounds.\n"
            "Step 3: Place surface items — objects ON tables (z = table_height + object_half_height), "
            "items ON shelves at correct shelf heights. Nothing should float.\n"
            "Step 4: Place robot with at least 1m clearance on all sides from other objects.\n\n"
            "Output format: objects: [{name, asset_path (from available_assets), "
            "prim_path (/World/Name), position [x,y,z], rotation [rx,ry,rz], scale [sx,sy,sz]}]. "
            "Then call build_scene_from_blueprint with the blueprint."
        ),
    }


DATA_HANDLERS["generate_scene_blueprint"] = _handle_generate_scene_blueprint
CODE_GEN_HANDLERS["build_scene_from_blueprint"] = _gen_build_scene_from_blueprint


# ── Scene Templates (Phase 5.7) ────────────────────────────────────────────

_SCENE_TEMPLATES = {
    "tabletop_manipulation": {
        "description": "Table-top manipulation scene with a Franka robot arm, objects to grasp, and an overhead camera. Ideal for pick-and-place tasks.",
        "category": "manipulation",
        "room_dims": [4, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [5, 5, 1]},
            {"name": "Table", "prim_type": "Cube", "position": [0, 0, 0.4], "scale": [0.8, 0.6, 0.4]},
            {"name": "Franka", "prim_path": "/World/Franka", "asset_name": "franka", "position": [0, -0.3, 0.8], "scale": [1, 1, 1]},
            {"name": "Cube_Red", "prim_type": "Cube", "position": [0.15, 0.1, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cube_Green", "prim_type": "Cube", "position": [-0.1, 0.15, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cylinder_Blue", "prim_type": "Cylinder", "position": [0.05, -0.1, 0.85], "scale": [0.02, 0.02, 0.04]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 1.8], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1280x720)", "contact_sensor (gripper fingers)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
    "warehouse_picking": {
        "description": "Warehouse bin-picking scene with shelving units, a mobile robot, bins with objects, and an overhead camera. Good for logistics and order-fulfillment tasks.",
        "category": "warehouse",
        "room_dims": [10, 8, 4],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [12, 10, 1]},
            {"name": "Shelf_A", "prim_type": "Cube", "position": [-2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Shelf_B", "prim_type": "Cube", "position": [2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Bin_1", "prim_type": "Cube", "position": [-2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_2", "prim_type": "Cube", "position": [-2, 2, 0.8], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_3", "prim_type": "Cube", "position": [2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "MobileRobot", "prim_path": "/World/Carter", "asset_name": "carter", "position": [0, -1, 0], "scale": [1, 1, 1]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 3.5], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1920x1080)", "rtx_lidar (mobile robot)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "mobile_navigation": {
        "description": "Indoor navigation scene with a ground plane, walls, obstacles, and a wheeled robot with lidar. Good for SLAM and path-planning tasks.",
        "category": "mobile",
        "room_dims": [8, 8, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [10, 10, 1]},
            {"name": "Wall_North", "prim_type": "Cube", "position": [0, 4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_South", "prim_type": "Cube", "position": [0, -4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_East", "prim_type": "Cube", "position": [4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Wall_West", "prim_type": "Cube", "position": [-4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Obstacle_1", "prim_type": "Cylinder", "position": [1.5, 1.0, 0.5], "scale": [0.3, 0.3, 1.0]},
            {"name": "Obstacle_2", "prim_type": "Cube", "position": [-1.0, -1.5, 0.4], "scale": [0.6, 0.6, 0.8]},
            {"name": "Obstacle_3", "prim_type": "Cylinder", "position": [-2.0, 2.0, 0.5], "scale": [0.25, 0.25, 1.0]},
            {"name": "Jetbot", "prim_path": "/World/Jetbot", "asset_name": "jetbot", "position": [0, 0, 0.05], "scale": [1, 1, 1]},
        ],
        "suggested_sensors": ["rtx_lidar (robot-mounted, 360 deg)", "camera (front-facing)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "inspection_cell": {
        "description": "Automated inspection cell with a conveyor belt, inspection cameras, structured lighting, and sample objects. Good for quality-inspection and defect-detection tasks.",
        "category": "inspection",
        "room_dims": [6, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [8, 6, 1]},
            {"name": "Conveyor", "prim_type": "Cube", "position": [0, 0, 0.45], "scale": [3.0, 0.5, 0.05]},
            {"name": "ConveyorLegs_L", "prim_type": "Cube", "position": [-1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "ConveyorLegs_R", "prim_type": "Cube", "position": [1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "InspectionCamera_Top", "prim_type": "Camera", "position": [0, 0, 1.5], "rotation": [-90, 0, 0]},
            {"name": "InspectionCamera_Side", "prim_type": "Camera", "position": [0, -1.2, 0.8], "rotation": [0, 0, 0]},
            {"name": "Light_Bar_1", "prim_type": "RectLight", "position": [-0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "Light_Bar_2", "prim_type": "RectLight", "position": [0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "SampleObject_1", "prim_type": "Cube", "position": [-0.3, 0, 0.5], "scale": [0.08, 0.08, 0.08]},
            {"name": "SampleObject_2", "prim_type": "Cylinder", "position": [0.1, 0, 0.5], "scale": [0.04, 0.04, 0.06]},
            {"name": "SampleObject_3", "prim_type": "Sphere", "position": [0.4, 0, 0.52], "scale": [0.03, 0.03, 0.03]},
        ],
        "suggested_sensors": ["camera (top-down, high-res 4K)", "camera (side-view, 1280x720)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
}


async def _handle_list_scene_templates(args: Dict) -> Dict:
    """List available scene templates, optionally filtered by category."""
    category = args.get("category", "").lower()

    templates = []
    for name, tmpl in _SCENE_TEMPLATES.items():
        if category and tmpl.get("category", "") != category:
            continue
        templates.append({
            "name": name,
            "description": tmpl["description"],
            "category": tmpl.get("category", "general"),
            "object_count": len(tmpl["objects"]),
            "room_dims": tmpl["room_dims"],
        })

    return {
        "templates": templates,
        "count": len(templates),
        "total_available": len(_SCENE_TEMPLATES),
    }


async def _handle_load_scene_template(args: Dict) -> Dict:
    """Load a scene template by name. Returns a blueprint compatible with build_scene_from_blueprint."""
    template_name = args.get("template_name", "").lower().replace(" ", "_").replace("-", "_")

    if template_name not in _SCENE_TEMPLATES:
        available = list(_SCENE_TEMPLATES.keys())
        return {
            "error": f"Template '{template_name}' not found.",
            "available_templates": available,
        }

    tmpl = _SCENE_TEMPLATES[template_name]

    # Build a blueprint dict compatible with build_scene_from_blueprint
    blueprint_objects = []
    for obj in tmpl["objects"]:
        bp_obj = {
            "name": obj["name"],
            "prim_path": obj.get("prim_path", f"/World/{obj['name']}"),
            "position": obj.get("position", [0, 0, 0]),
            "rotation": obj.get("rotation", [0, 0, 0]),
            "scale": obj.get("scale", [1, 1, 1]),
        }
        if obj.get("prim_type"):
            bp_obj["prim_type"] = obj["prim_type"]
        if obj.get("asset_name"):
            bp_obj["asset_name"] = obj["asset_name"]
        if obj.get("asset_path"):
            bp_obj["asset_path"] = obj["asset_path"]
        blueprint_objects.append(bp_obj)

    blueprint = {
        "description": tmpl["description"],
        "room_dimensions": tmpl["room_dims"],
        "objects": blueprint_objects,
        "suggested_sensors": tmpl.get("suggested_sensors", []),
        "physics_settings": tmpl.get("physics_settings", {}),
    }

    return {
        "template_name": template_name,
        "blueprint": blueprint,
        "object_count": len(blueprint_objects),
        "message": (
            f"Template '{template_name}' loaded with {len(blueprint_objects)} objects. "
            "Call build_scene_from_blueprint with the 'blueprint' field to create the scene."
        ),
    }


DATA_HANDLERS["list_scene_templates"] = _handle_list_scene_templates
DATA_HANDLERS["load_scene_template"] = _handle_load_scene_template


# ── Batch Operations (Phase 5.6) ───────────────────────────────────────────

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


CODE_GEN_HANDLERS["batch_apply_operation"] = _gen_batch_apply_operation


# ── Scene Blueprint Validation (Phase 6A.3) ────────────────────────────────

async def _handle_validate_scene_blueprint(args: Dict) -> Dict:
    """Validate a scene blueprint before building. Checks for overlaps, floating objects, bad scales, and missing fields."""
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])

    issues: List[str] = []
    warnings: List[str] = []

    if not objects:
        issues.append("Blueprint has no objects.")
        return {"valid": False, "issues": issues, "warnings": warnings, "object_count": 0}

    # ── Check required fields on each object ────────────────────────────
    for i, obj in enumerate(objects):
        name = obj.get("name", f"object_{i}")
        if not obj.get("name"):
            warnings.append(f"Object [{i}] is missing a 'name' field.")
        if not obj.get("position"):
            issues.append(f"Object '{name}' is missing a 'position' field.")
        if not obj.get("prim_type") and not obj.get("asset_path") and not obj.get("asset_name"):
            issues.append(f"Object '{name}' has no 'prim_type', 'asset_path', or 'asset_name' — cannot create it.")

    # ── Check for unrealistic scales ────────────────────────────────────
    for obj in objects:
        name = obj.get("name", "unnamed")
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(scale, (list, tuple)):
            for j, s in enumerate(scale):
                axis = ["X", "Y", "Z"][j] if j < 3 else str(j)
                if abs(s) < 0.001:
                    issues.append(f"Object '{name}' has near-zero scale on {axis} axis ({s}) — likely an error.")
                elif abs(s) > 1000:
                    warnings.append(f"Object '{name}' has very large scale on {axis} axis ({s}) — is this intended?")

    # ── Check for floating objects (z > 0 without obvious support) ──────
    ground_level = 0.0
    # Find ground plane or lowest object to establish reference
    for obj in objects:
        name_lower = obj.get("name", "").lower()
        if any(k in name_lower for k in ("ground", "plane", "floor")):
            pos = obj.get("position", [0, 0, 0])
            ground_level = pos[2] if len(pos) > 2 else 0.0
            break

    for obj in objects:
        name = obj.get("name", "unnamed")
        name_lower = name.lower()
        pos = obj.get("position", [0, 0, 0])
        if len(pos) < 3:
            continue
        z = pos[2]
        # Skip ground planes, cameras, lights, overhead items — they are expected to be elevated
        if any(k in name_lower for k in ("ground", "plane", "floor", "camera", "light", "overhead", "ceiling", "lamp")):
            continue
        # Objects more than 0.5m above ground level may be floating
        if z > ground_level + 0.5:
            warnings.append(f"Object '{name}' is at z={z:.2f}m — may be floating without support.")

    # ── Check for AABB overlaps (simple distance-based) ─────────────────
    positioned_objects = []
    for obj in objects:
        pos = obj.get("position", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            # Approximate object radius from scale
            if isinstance(scale, (list, tuple)) and len(scale) >= 3:
                radius = max(abs(scale[0]), abs(scale[1]), abs(scale[2])) * 0.5
            else:
                radius = 0.5
            positioned_objects.append({
                "name": obj.get("name", "unnamed"),
                "pos": pos,
                "radius": radius,
            })

    for i in range(len(positioned_objects)):
        for j in range(i + 1, len(positioned_objects)):
            a = positioned_objects[i]
            b = positioned_objects[j]
            dx = a["pos"][0] - b["pos"][0]
            dy = a["pos"][1] - b["pos"][1]
            dz = a["pos"][2] - b["pos"][2]
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            min_dist = a["radius"] + b["radius"]
            if dist < min_dist * 0.7:  # 70% overlap threshold — some tolerance for surface items
                warnings.append(
                    f"Objects '{a['name']}' and '{b['name']}' may overlap "
                    f"(distance={dist:.3f}m, combined radius={min_dist:.3f}m)."
                )

    # ── Check for scale mismatches between objects ──────────────────────
    max_scales = []
    for obj in objects:
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(scale, (list, tuple)) and len(scale) >= 3:
            max_scales.append((obj.get("name", "unnamed"), max(abs(s) for s in scale[:3])))
        elif isinstance(scale, (int, float)):
            max_scales.append((obj.get("name", "unnamed"), abs(scale)))

    if len(max_scales) >= 2:
        all_vals = [s for _, s in max_scales]
        median_scale = sorted(all_vals)[len(all_vals) // 2]
        if median_scale > 0:
            for name, s in max_scales:
                ratio = s / median_scale
                if ratio > 50 or (median_scale > 0.01 and ratio < 0.02):
                    warnings.append(
                        f"Object '{name}' scale ({s:.3f}) differs vastly from "
                        f"median scale ({median_scale:.3f}) — possible unit mismatch."
                    )

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "object_count": len(objects),
    }


DATA_HANDLERS["validate_scene_blueprint"] = _handle_validate_scene_blueprint


# ── IsaacLab RL Training ─────────────────────────────────────────────────────

_RL_TASK_TEMPLATES = {
    "manipulation": {
        "obs": ["joint_pos", "joint_vel", "ee_pos", "ee_ori", "target_pos", "target_rel"],
        "actions": "joint_positions",
        "rewards": ["reach_target", "grasp_success", "action_penalty", "is_terminated"],
    },
    "locomotion": {
        "obs": ["base_lin_vel", "base_ang_vel", "projected_gravity", "joint_pos", "joint_vel", "actions"],
        "actions": "joint_positions",
        "rewards": ["track_lin_vel", "track_ang_vel", "feet_air_time", "action_rate", "is_terminated"],
    },
    "navigation": {
        "obs": ["base_pos", "base_ori", "base_lin_vel", "target_pos", "target_rel", "lidar_scan"],
        "actions": "base_velocity",
        "rewards": ["reach_goal", "collision_penalty", "progress_to_goal", "action_penalty"],
    },
    "custom": {
        "obs": ["joint_pos", "joint_vel"],
        "actions": "joint_positions",
        "rewards": ["task_success", "action_penalty"],
    },
}


async def _handle_create_isaaclab_env(args: Dict) -> Dict:
    """Generate an IsaacLab env scaffold — returns config as data for the LLM to refine."""
    task_name = args["task_name"]
    robot_path = args["robot_path"]
    task_type = args.get("task_type", "manipulation")
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.0)
    reward_terms = args.get("reward_terms")

    template = _RL_TASK_TEMPLATES.get(task_type, _RL_TASK_TEMPLATES["custom"])
    if reward_terms:
        template = {**template, "rewards": reward_terms}

    env_config = {
        "task_name": task_name,
        "robot_path": robot_path,
        "task_type": task_type,
        "num_envs": num_envs,
        "env_spacing": env_spacing,
        "observation_space": template["obs"],
        "action_space": template["actions"],
        "reward_terms": template["rewards"],
        "episode_length": 500,
        "decimation": 2,
        "physics_dt": 1.0 / 120.0,
    }

    # Generate the Python env class code and __init__.py registration
    env_code = _generate_isaaclab_env_code(env_config)
    init_code = _generate_isaaclab_init_code(env_config)

    return {
        "type": "isaaclab_env",
        "task_name": task_name,
        "config": env_config,
        "generated_code": env_code,
        "generated_init_code": init_code,
        "instructions": (
            f"IsaacLab env '{task_name}' scaffolded with {num_envs} parallel envs. "
            f"Observations: {template['obs']}. Actions: {template['actions']}. "
            f"Rewards: {template['rewards']}. "
            "Two files generated: env config module and __init__.py with gymnasium.register(). "
            "You can now call launch_training to start training, or refine the config."
        ),
    }


def _generate_isaaclab_env_code(cfg: Dict) -> str:
    """Generate a minimal IsaacLab ManagerBasedRLEnv config file."""
    task = cfg["task_name"]
    robot = cfg["robot_path"]
    obs = cfg["observation_space"]
    acts = cfg["action_space"]
    rewards = cfg["reward_terms"]
    num_envs = cfg["num_envs"]
    spacing = cfg["env_spacing"]
    ep_len = cfg["episode_length"]
    decimation = cfg["decimation"]

    # Build observation term attributes for nested configclass
    obs_attrs = "\n".join(
        f"        {o}: ObsTerm = ObsTerm(func=mdp.{o})" for o in obs
    )

    # Build reward term attributes for configclass
    reward_attrs = "\n".join(
        f"    {r}: RewTerm = RewTerm(func=mdp.{r}, weight=1.0)" for r in rewards
    )

    # Map action space name to the appropriate action config class
    action_cfg_map = {
        "joint_positions": "JointPositionActionCfg",
        "base_velocity": "DifferentialInverseKinematicsActionCfg",
    }
    action_cfg_cls = action_cfg_map.get(acts, "JointPositionActionCfg")

    return f'''"""IsaacLab RL environment: {task}
Auto-generated by Isaac Assist.
"""
import isaaclab.envs.mdp as mdp
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.envs.mdp import ObsGroup, ObsTerm
from isaaclab.managers import (
    RewardTermCfg as RewTerm,
    SceneEntityCfg,
)
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.utils import configclass


@configclass
class ObservationsCfg:
    """Observation groups for the environment."""

    @configclass
    class PolicyCfg(ObsGroup):
{obs_attrs}

    policy: PolicyCfg = PolicyCfg()


@configclass
class ActionsCfg:
    """Action configuration for the environment."""

    {acts}: mdp.{action_cfg_cls} = mdp.{action_cfg_cls}(
        asset_name="robot", joint_names=[".*"]
    )


@configclass
class RewardsCfg:
    """Reward terms for the environment."""

{reward_attrs}


@configclass
class {task}EnvCfg(ManagerBasedRLEnvCfg):
    """Configuration for {task} environment."""

    # Scene
    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs={num_envs},
        env_spacing={spacing},
    )

    # Observations
    observations: ObservationsCfg = ObservationsCfg()

    # Actions
    actions: ActionsCfg = ActionsCfg()

    # Rewards
    rewards: RewardsCfg = RewardsCfg()

    # Episode
    episode_length_s = {ep_len} * {decimation} / 120.0
    decimation = {decimation}
'''


def _generate_isaaclab_init_code(cfg: Dict) -> str:
    """Generate __init__.py with gymnasium.register() for the IsaacLab env."""
    task = cfg["task_name"]
    module_name = task.lower()

    return f'''"""Register {task} environment with Gymnasium."""
import gymnasium

gymnasium.register(
    id="{task}-v0",
    entry_point="isaaclab.envs:ManagerBasedRLEnv",
    kwargs={{"env_cfg_entry_point": "{module_name}:{task}EnvCfg"}},
)
'''


def _gen_launch_training(args: Dict) -> str:
    """Generate code to launch an IsaacLab training run."""
    task = args["task"]
    algo = args.get("algo", "ppo")
    num_steps = args.get("num_steps", 1_000_000)
    num_envs = args.get("num_envs", 64)
    ckpt_dir = args.get("checkpoint_dir", f"workspace/rl_checkpoints/{task}")

    # Map algos to RL library and corresponding train script
    algo_script_map = {
        "ppo": ("rsl_rl", "scripts/reinforcement_learning/rsl_rl/train.py"),
        "sac": ("skrl", "scripts/reinforcement_learning/skrl/train.py"),
        "td3": ("skrl", "scripts/reinforcement_learning/skrl/train.py"),
        "ppo_rl_games": ("rl_games", "scripts/reinforcement_learning/rl_games/train.py"),
        "ppo_sb3": ("sb3", "scripts/reinforcement_learning/sb3/train.py"),
        "rsl_rl": ("rsl_rl", "scripts/reinforcement_learning/rsl_rl/train.py"),
    }
    _library, train_script = algo_script_map.get(algo, algo_script_map["ppo"])

    lines = [
        "import subprocess",
        "import os",
        "",
        f"task = '{task}'",
        f"algo = '{algo}'",
        f"num_envs = {num_envs}",
        f"max_iterations = {num_steps // (num_envs * 24)}  # steps / (envs * horizon)",
        f"checkpoint_dir = '{ckpt_dir}'",
        "os.makedirs(checkpoint_dir, exist_ok=True)",
        "",
        "# Launch IsaacLab training via isaaclab.sh",
        "cmd = [",
        f"    'isaaclab.sh', '-p',",
        f"    '{train_script}',",
        f"    '--task', task,",
        f"    '--num_envs', str(num_envs),",
        f"    '--max_iterations', str(max_iterations),",
        f"    '--log_root_path', checkpoint_dir,",
        "]",
        """print(f'Launching training: {" ".join(cmd)}')""",
        "proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)",
        "print(f'Training started (PID: {proc.pid}). Checkpoints -> {checkpoint_dir}')",
    ]
    return "\n".join(lines)


DATA_HANDLERS["create_isaaclab_env"] = _handle_create_isaaclab_env
CODE_GEN_HANDLERS["launch_training"] = _gen_launch_training


# ─── Vision tools (Gemini Robotics-ER 1.6) ──────────────────────────────────

async def _get_viewport_bytes() -> tuple:
    """Capture the viewport and return (raw_bytes, mime_type)."""
    result = await kit_tools.get_viewport_image(max_dim=1280)
    b64 = result.get("image_b64") or result.get("data", "")
    if not b64:
        return None, None
    import base64
    return base64.b64decode(b64), "image/png"


def _get_vision_provider():
    from ..vision_gemini import GeminiVisionProvider
    return GeminiVisionProvider()


async def _handle_vision_detect_objects(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    labels = args.get("labels")
    max_obj = args.get("max_objects", 10)
    detections = await vp.detect_objects(img, mime, labels=labels, max_objects=max_obj)
    return {"detections": detections, "count": len(detections), "model": vp.model}


async def _handle_vision_bounding_boxes(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    boxes = await vp.detect_bounding_boxes(img, mime, max_objects=args.get("max_objects", 25))
    return {"bounding_boxes": boxes, "count": len(boxes), "model": vp.model}


async def _handle_vision_plan_trajectory(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    points = await vp.plan_trajectory(
        img, args["instruction"], num_points=args.get("num_points", 15), mime_type=mime,
    )
    return {"trajectory": points, "num_points": len(points), "model": vp.model}


async def _handle_vision_analyze_scene(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    analysis = await vp.analyze_scene(img, args["question"], mime_type=mime)
    return {"analysis": analysis, "model": vp.model}


DATA_HANDLERS["vision_detect_objects"] = _handle_vision_detect_objects
DATA_HANDLERS["vision_bounding_boxes"] = _handle_vision_bounding_boxes
DATA_HANDLERS["vision_plan_trajectory"] = _handle_vision_plan_trajectory
DATA_HANDLERS["vision_analyze_scene"] = _handle_vision_analyze_scene


# ── Scene Package Export ─────────────────────────────────────────────────────
# Collects all approved code patches from the audit log for a session,
# then writes:  scene_setup.py, ros2_launch.py (if ROS2 nodes present),
# README.md, and a ros2_topics.yaml listing detected topics.

async def _handle_export_scene_package(args: Dict) -> Dict:
    """Export the current session's scene setup as a reusable file package."""
    from pathlib import Path
    from datetime import datetime as _dt
    from ..routes import _audit

    session_id = args.get("session_id", "default_session")
    scene_name = args.get("scene_name", "exported_scene")
    # Sanitize scene_name for filesystem
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_name)

    out_dir = Path("workspace/scene_exports") / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect approved patches from audit log ──────────────────────────
    entries = _audit.query_logs(limit=500, event_type="patch_executed")
    patches = []
    for e in entries:
        meta = e.metadata or {}
        if meta.get("success") and meta.get("session_id", "default_session") == session_id:
            code = meta.get("code", "")
            if code:
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": code,
                })

    if not patches:
        # Fallback: grab all successful patches regardless of session
        for e in entries:
            meta = e.metadata or {}
            if meta.get("success") and meta.get("code"):
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": meta["code"],
                })

    # ── scene_setup.py ───────────────────────────────────────────────────
    setup_lines = [
        '"""',
        f'Scene Setup: {scene_name}',
        f'Auto-exported by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
        f'Patches: {len(patches)}',
        '"""',
        'import omni.usd',
        'from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf, Sdf, UsdShade',
        '',
        'stage = omni.usd.get_context().get_stage()',
        '',
    ]
    for i, p in enumerate(patches):
        desc = p["description"] or f"Step {i+1}"
        setup_lines.append(f'# ── Step {i+1}: {desc}')
        setup_lines.append(p["code"].rstrip())
        setup_lines.append('')
    setup_lines.append('print("Scene setup complete.")')
    scene_py = "\n".join(setup_lines)
    (out_dir / "scene_setup.py").write_text(scene_py, encoding="utf-8")

    # ── Detect ROS2 topics from OmniGraph patterns in code ───────────────
    import re as _re
    ros2_topics = set()
    og_node_types = set()
    robot_paths = set()
    for p in patches:
        code = p["code"]
        # topics: /joint_states, /joint_command, /clock, /tf, etc.
        ros2_topics.update(_re.findall(r"""['\"](/[a-zA-Z_][a-zA-Z0-9_/]*)['\"]\s*""", code))
        # OmniGraph node types
        og_node_types.update(_re.findall(r"""['\"](?:isaacsim|omni\.isaac)\.[a-zA-Z0-9_.]+['\"]""", code))
        # Robot paths
        robot_paths.update(_re.findall(r"""['\"](/World/[A-Z][a-zA-Z0-9_]*)['\"]\s*""", code))
    # Filter to ROS2-style topics only (not USD paths, not physics scene attrs)
    _NON_TOPIC_PREFIXES = ("/World", "/Physics", "/Collision", "/persistent", "/Render", "/OmniKit")
    ros2_topics = sorted(
        t for t in ros2_topics
        if not any(t.startswith(p) for p in _NON_TOPIC_PREFIXES)
        and len(t) > 2  # skip bare "/"
        and not t.endswith(".usd")
    )

    # ── ros2_topics.yaml ─────────────────────────────────────────────────
    if ros2_topics or og_node_types:
        topic_lines = [f"# ROS2 Topics detected in scene: {scene_name}", "topics:"]
        for t in sorted(ros2_topics):
            topic_lines.append(f"  - name: \"{t}\"")
        topic_lines.append("")
        topic_lines.append("omnigraph_node_types:")
        for nt in sorted(og_node_types):
            topic_lines.append(f"  - {nt}")
        (out_dir / "ros2_topics.yaml").write_text("\n".join(topic_lines) + "\n", encoding="utf-8")

    # ── ros2_launch.py (if ROS2 topics detected) ────────────────────────
    has_ros2 = bool(ros2_topics)
    if has_ros2:
        launch_lines = [
            '"""',
            f'ROS2 Launch File for scene: {scene_name}',
            f'Auto-generated by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
            '"""',
            'from launch import LaunchDescription',
            'from launch_ros.actions import Node',
            '',
            '',
            'def generate_launch_description():',
            '    return LaunchDescription([',
        ]
        # Add placeholder nodes for each topic
        for t in sorted(ros2_topics):
            node_name = t.strip("/").replace("/", "_")
            launch_lines.append(f'        # Topic: {t}')
            launch_lines.append(f'        # Node("{node_name}") — configure publisher/subscriber as needed')
        launch_lines.append('    ])')
        (out_dir / "ros2_launch.py").write_text("\n".join(launch_lines) + "\n", encoding="utf-8")

    # ── README.md ────────────────────────────────────────────────────────
    robot_list = ", ".join(f"`{r}`" for r in sorted(robot_paths)) or "None detected"
    topic_list = "\n".join(f"- `{t}`" for t in sorted(ros2_topics)) or "- None detected"
    readme = f"""# {scene_name}

Auto-exported by **Isaac Assist** on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}.

## Scene Summary

- **Patches applied:** {len(patches)}
- **Robots:** {robot_list}
- **ROS2 Topics:**
{topic_list}

## Files

| File | Description |
|------|-------------|
| `scene_setup.py` | All approved code patches as a single runnable script |
| `ros2_topics.yaml` | Detected ROS2 topics and OmniGraph node types |
{"| `ros2_launch.py` | ROS2 launch file template |" if has_ros2 else ""}
| `README.md` | This file |

## Usage

### Replay Scene in Isaac Sim
```python
# In Isaac Sim Script Editor or via Kit RPC:
exec(open("{out_dir}/scene_setup.py").read())
```

### ROS2 Topics
{"Launch the ROS2 nodes alongside Isaac Sim:" if has_ros2 else "No ROS2 topics detected in this scene."}
{"```bash" if has_ros2 else ""}
{"ros2 launch " + str(out_dir / "ros2_launch.py") if has_ros2 else ""}
{"```" if has_ros2 else ""}
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    files_written = ["scene_setup.py", "README.md"]
    if ros2_topics or og_node_types:
        files_written.append("ros2_topics.yaml")
    if has_ros2:
        files_written.append("ros2_launch.py")

    return {
        "export_dir": str(out_dir),
        "files": files_written,
        "patch_count": len(patches),
        "ros2_topics": ros2_topics,
        "robots_detected": sorted(robot_paths),
        "message": f"Exported {len(patches)} patches to {out_dir}/ — files: {', '.join(files_written)}",
    }


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package
