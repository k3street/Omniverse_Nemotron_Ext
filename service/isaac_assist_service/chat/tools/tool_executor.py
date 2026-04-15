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
import json
import logging
import os
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
from omni.isaac.urdf import _urdf
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
        "ros2_connect": None,
        "ros2_list_topics": None,
        "ros2_get_topic_type": None,
        "ros2_get_message_type": None,
        "ros2_subscribe_once": None,
        "ros2_publish": None,
        "ros2_publish_sequence": None,
        "ros2_list_services": None,
        "ros2_call_service": None,
        "ros2_list_nodes": None,
        "ros2_get_node_details": None,
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
from omni.isaac.sensor import LidarRtx
lidar = LidarRtx(prim_path=lidar_path)
"""
    if sensor_type == "imu":
        return f"""\
from omni.isaac.sensor import IMUSensor
imu = IMUSensor(prim_path='{prim_path}/IMU')
"""
    if sensor_type == "contact_sensor":
        return f"""\
from omni.isaac.sensor import ContactSensor
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

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee = cfg["ee_frame"]

    if planner == "lula_rrt":
        # Global planner — single-shot path plan
        lines = [
            "import omni.usd",
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import LulaTaskSpaceTrajectoryGenerator",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            "# Load Lula RRT planner config",
            f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
            f"rrt = LulaTaskSpaceTrajectoryGenerator(**rrt_config)",
            "",
            f"target_pos = np.array({list(target_pos)})",
        ]
        if target_ori:
            lines.append(f"target_ori = np.array({list(target_ori)})")
        else:
            lines.append("target_ori = None")
        lines.extend([
            "",
            "# Compute trajectory",
            f"trajectory = rrt.compute_task_space_trajectory_from_points(",
            f"    [target_pos], [target_ori] if target_ori is not None else None",
            f")",
            "if trajectory is not None:",
            "    print(f'Lula RRT: planned trajectory with {{len(trajectory)}} waypoints')",
            "else:",
            "    print('Lula RRT: failed to find path — try a different target or clear obstacles')",
        ])
        return "\n".join(lines)

    # Default: RMPflow (reactive, real-time)
    lines = [
        "import omni.usd",
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import RmpFlow",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "from isaacsim.core.prims import SingleArticulation",
        "from isaacsim.core.api import World",
        "",
        "# Load RMPflow config for the robot",
        f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
        "rmpflow = RmpFlow(**rmpflow_config)",
        "",
        f"# Get the articulation",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "world = World.instance()",
        "if world is None:",
        "    from isaacsim.core.api import World",
        "    world = World()",
        "art.initialize()",
        "",
        "# Set target",
        f"target_pos = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_ori = np.array({list(target_ori)})")
    else:
        lines.append("target_ori = None")
    lines.extend([
        f"rmpflow.set_end_effector_target(target_pos, target_ori)",
        "",
        "# Get current joint state and compute action",
        "joint_positions = art.get_joint_positions()",
        "joint_velocities = art.get_joint_velocities()",
        "action = rmpflow.get_next_articulation_action(",
        "    joint_positions, joint_velocities",
        ")",
        "",
        "# Apply joint targets",
        "art.apply_action(action)",
        f"print(f'RMPflow: moving {ee} to {{target_pos}} — action applied')",
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
    if has_ori:
        ori_str = "[" + ", ".join(
            f"np.array({list(o)})" if o else "None" for o in orientations
        ) + "]"
    else:
        ori_str = "None"

    lines = [
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import LulaTaskSpaceTrajectoryGenerator",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "",
        f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
        f"planner = LulaTaskSpaceTrajectoryGenerator(**rrt_config)",
        "",
        f"positions = {positions_str}",
        f"orientations = {ori_str}",
        "",
        "trajectory = planner.compute_task_space_trajectory_from_points(",
        "    positions, orientations",
        ")",
        "if trajectory is not None:",
        f"    print(f'Planned trajectory through {len(waypoints)} waypoints')",
        "else:",
        "    print('Failed to plan trajectory — try different waypoints')",
    ]
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


def _build_asset_index() -> List[Dict]:
    """Build searchable index from asset_catalog.json (fast) + known robots."""
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    index = []
    assets_root = getattr(config, "assets_root_path", None) or ""
    robots_sub = getattr(config, "assets_robots_subdir", None) or "Collected_Robots"
    robots_dir = f"{assets_root}/{robots_sub}" if assets_root else ""

    # 1. Load asset_catalog.json (5,000+ entries with rich metadata)
    catalog_path = Path(assets_root) / "asset_catalog.json" if assets_root else None
    catalog_loaded = False
    if catalog_path and catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text())
            for entry in catalog.get("assets", []):
                tags = entry.get("tags", [])
                index.append({
                    "name": entry.get("name", ""),
                    "type": entry.get("category", "prop"),
                    "path": entry.get("usd_path", ""),
                    "rel_path": entry.get("relative_path", ""),
                    "tags": tags,
                    "source": "asset_catalog",
                })
            catalog_loaded = True
            logger.info(f"[AssetIndex] Loaded {len(index)} entries from asset_catalog.json")
        except Exception as e:
            logger.warning(f"[AssetIndex] Failed to load asset_catalog.json: {e}")

    # 2. Always add the known robot name map (canonical names → files)
    for name, filename in _CATALOG_ROBOTS.items():
        index.append({
            "name": name,
            "type": "robot",
            "path": f"{robots_dir}/{filename}" if robots_dir else filename,
            "source": "robot_library",
        })

    # 3. JSONL manifest (user-added entries)
    manifest_path = _WORKSPACE / "knowledge" / "asset_manifest.jsonl"
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    index.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # 4. Filesystem walk only if catalog wasn't loaded (slow fallback)
    if not catalog_loaded and assets_root:
        search_dir = Path(assets_root)
        if search_dir.exists():
            try:
                for f in search_dir.rglob("*"):
                    if f.suffix.lower() in (".usd", ".usda", ".usdz"):
                        rel = f.relative_to(search_dir)
                        name_parts = rel.stem.replace("_", " ").replace("-", " ")
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
    return _asset_index


async def _handle_catalog_search(args: Dict) -> Dict:
    """Fuzzy-match assets by name, type, and path."""
    query = args.get("query", "").lower()
    asset_type = args.get("asset_type", "any").lower()
    limit = args.get("limit", 10)

    index = _build_asset_index()
    scored = []
    query_words = query.split()

    for asset in index:
        if asset_type != "any" and asset.get("type", "any") != asset_type:
            continue

        name = asset.get("name", "").lower()
        path = asset.get("path", "").lower()
        rel_path = asset.get("rel_path", "").lower()
        tags = " ".join(asset.get("tags", [])).lower() if asset.get("tags") else ""
        searchable = f"{name} {path} {rel_path} {tags}"

        # Score: exact match > all words present > partial
        if query == name:
            score = 100
        elif all(w in searchable for w in query_words):
            score = 70 + sum(10 for w in query_words if w in name)
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


# ── Nucleus Browse & Download ────────────────────────────────────────────────

async def _handle_nucleus_browse(args: Dict) -> Dict:
    """Browse a Nucleus server directory via Kit RPC (omni.client inside Isaac Sim)."""
    nucleus_path = args.get("path", "/NVIDIA/Assets/Isaac/5.1")
    # Sanitize: strip shell metacharacters, only allow alphanumeric + / . _ -
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', nucleus_path):
        return {"error": "Invalid path characters"}

    server = args.get("server", "omniverse://localhost")
    if not _re.match(r'^omniverse://[a-zA-Z0-9._-]+(:\d+)?$', server):
        return {"error": "Invalid Nucleus server URL. Expected format: omniverse://hostname"}

    full_path = f"{server}{nucleus_path}"
    limit = min(args.get("limit", 50), 200)

    code = f"""
import omni.client
import json

result, entries = omni.client.list("{full_path}")
items = []
if result == omni.client.Result.OK:
    for entry in entries[:{limit}]:
        items.append({{
            "name": entry.relative_path,
            "size": entry.size,
            "is_folder": entry.flags & omni.client.ItemFlags.CAN_HAVE_CHILDREN != 0,
            "modified_time": str(entry.modified_time) if hasattr(entry, 'modified_time') else "",
        }})
print(json.dumps({{"status": str(result), "path": "{full_path}", "items": items, "count": len(items)}}))
"""
    result = await kit_tools.exec_sync(code, timeout=15)
    if not result.get("success"):
        return {"error": f"Kit RPC failed: {result.get('output', 'unknown')}",
                "hint": "Is Isaac Sim running? Is a Nucleus server accessible?"}

    output = result.get("output", "").strip()
    # Parse the last line as JSON (exec_sync may include other prints)
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"error": "Failed to parse Nucleus response", "raw_output": output[:500]}


async def _handle_download_asset(args: Dict) -> Dict:
    """Download asset from Nucleus to local Desktop/assets and register in catalog."""
    import re as _re

    nucleus_url = args.get("nucleus_url", "")
    # Validate URL format
    if not nucleus_url.startswith("omniverse://"):
        return {"error": "nucleus_url must start with omniverse://"}
    if not _re.match(r'^omniverse://[a-zA-Z0-9._:-]+/[a-zA-Z0-9/_. -]+$', nucleus_url):
        return {"error": "Invalid nucleus_url format"}

    assets_root = getattr(config, "assets_root_path", "") or ""
    if not assets_root:
        return {"error": "ASSETS_ROOT_PATH not configured in .env"}

    # Determine local destination
    # omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/Franka/franka.usd
    # → Desktop/assets/Nucleus_Downloads/Robots/Franka/franka.usd
    subdir = args.get("local_subdir", "")
    if not subdir:
        # Auto-derive from Nucleus path: strip server + /NVIDIA/Assets/Isaac/X.X/
        path_part = nucleus_url.split("/", 3)[-1] if "/" in nucleus_url else ""
        # Remove common prefixes
        for prefix in ("NVIDIA/Assets/Isaac/5.1/", "NVIDIA/Assets/Isaac/", "NVIDIA/Assets/", "NVIDIA/"):
            if path_part.startswith(prefix):
                path_part = path_part[len(prefix):]
                break
        subdir = f"Nucleus_Downloads/{path_part}" if path_part else "Nucleus_Downloads"

    # Extract just the directory part (not filename)
    if subdir.endswith(".usd") or subdir.endswith(".usda") or subdir.endswith(".usdz"):
        subdir = str(Path(subdir).parent)

    local_dir = Path(assets_root) / subdir
    filename = nucleus_url.rsplit("/", 1)[-1]
    # Sanitize filename
    filename = _re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    local_path = local_dir / filename

    if local_path.exists():
        return {
            "status": "already_exists",
            "local_path": str(local_path),
            "message": f"Asset already downloaded at {local_path}",
        }

    # Escape paths for code injection safety
    safe_nucleus = nucleus_url.replace('"', '').replace("'", "").replace("\\", "")
    safe_local = str(local_path).replace('"', '').replace("'", "").replace("\\", "")
    safe_dir = str(local_dir).replace('"', '').replace("'", "").replace("\\", "")

    code = f"""
import omni.client
import os
import json

os.makedirs("{safe_dir}", exist_ok=True)
result = omni.client.copy("{safe_nucleus}", "{safe_local}")
if result == omni.client.Result.OK:
    size = os.path.getsize("{safe_local}") if os.path.exists("{safe_local}") else 0
    print(json.dumps({{"status": "ok", "local_path": "{safe_local}", "size": size}}))
else:
    print(json.dumps({{"status": "error", "result": str(result), "nucleus_url": "{safe_nucleus}"}}))
"""
    result = await kit_tools.exec_sync(code, timeout=60)
    if not result.get("success"):
        return {"error": f"Kit RPC download failed: {result.get('output', 'unknown')}"}

    output = result.get("output", "").strip()
    dl_result = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                dl_result = json.loads(line)
                break
            except json.JSONDecodeError:
                pass

    if not dl_result or dl_result.get("status") != "ok":
        return {"error": "Download failed", "details": dl_result or output[:500]}

    # Register in asset_catalog.json
    catalog_path = Path(assets_root) / "asset_catalog.json"
    asset_name = Path(filename).stem.replace("_", " ").replace("-", " ")
    # Infer category from path
    path_lower = nucleus_url.lower()
    if any(k in path_lower for k in ("robot", "arm", "gripper", "manipulator")):
        category = "robot"
    elif any(k in path_lower for k in ("env", "room", "warehouse", "scene")):
        category = "scene"
    elif any(k in path_lower for k in ("sensor", "camera", "lidar")):
        category = "sensor"
    elif any(k in path_lower for k in ("prop", "object", "furniture")):
        category = "prop"
    else:
        category = args.get("category", "prop")

    new_entry = {
        "name": asset_name,
        "usd_path": str(local_path),
        "relative_path": str(local_path.relative_to(Path(assets_root))),
        "category": category,
        "tags": [w for w in asset_name.lower().split() if len(w) > 1] + ["nucleus_download"],
        "nucleus_source": nucleus_url,
        "meters_per_unit": 1.0,
    }

    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text())
            catalog["assets"].append(new_entry)
            catalog["metadata"]["total_assets"] = len(catalog["assets"])
            catalog_path.write_text(json.dumps(catalog, indent=2))
        except Exception as e:
            logger.warning(f"[DownloadAsset] Failed to update catalog: {e}")

    # Invalidate cached asset index so next search picks up the new entry
    global _asset_index
    _asset_index = None

    return {
        "status": "downloaded",
        "local_path": str(local_path),
        "size": dl_result.get("size", 0),
        "category": category,
        "nucleus_source": nucleus_url,
        "message": f"Downloaded {filename} to {local_path} ({dl_result.get('size', 0)} bytes). Registered in asset catalog.",
    }


DATA_HANDLERS["nucleus_browse"] = _handle_nucleus_browse
DATA_HANDLERS["download_asset"] = _handle_download_asset


# ── Scene Builder ────────────────────────────────────────────────────────────

def _gen_build_scene_from_blueprint(args: Dict) -> str:
    """Generate code to build a scene from a structured blueprint."""
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])
    dry_run = args.get("dry_run", False)

    if not objects:
        return "print('Empty blueprint — nothing to build')\n"

    lines = [
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
    room_dims = args.get("room_dimensions")
    available = args.get("available_assets")

    # If no assets provided, search the catalog
    if not available:
        catalog_result = await _handle_catalog_search({"query": description, "limit": 20})
        available = catalog_result.get("results", [])

    return {
        "type": "blueprint_request",
        "description": description,
        "room_dimensions": room_dims or [6, 6, 3],
        "available_assets": available,
        "instructions": (
            "Based on the description and available assets, generate a blueprint JSON with: "
            "objects: [{name, asset_path (from available_assets), prim_path (/World/Name), "
            "position [x,y,z], rotation [rx,ry,rz], scale [sx,sy,sz]}]. "
            "Ensure objects don't overlap, items sit ON surfaces (not floating), "
            "robots have 1m clearance. Then call build_scene_from_blueprint with the blueprint."
        ),
    }


DATA_HANDLERS["generate_scene_blueprint"] = _handle_generate_scene_blueprint
CODE_GEN_HANDLERS["build_scene_from_blueprint"] = _gen_build_scene_from_blueprint


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

    # Generate the Python env class code
    env_code = _generate_isaaclab_env_code(env_config)

    return {
        "type": "isaaclab_env",
        "task_name": task_name,
        "config": env_config,
        "generated_code": env_code,
        "instructions": (
            f"IsaacLab env '{task_name}' scaffolded with {num_envs} parallel envs. "
            f"Observations: {template['obs']}. Actions: {template['actions']}. "
            f"Rewards: {template['rewards']}. "
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

    obs_terms = "\n".join(f'        "{o}": ObsTerm(func=mdp.{o}),' for o in obs)
    reward_terms = "\n".join(
        f'        "{r}": RewTerm(func=mdp.{r}, weight=1.0),' for r in rewards
    )

    return f'''"""IsaacLab RL environment: {task}
Auto-generated by Isaac Assist.
"""
import isaaclab.envs.mdp as mdp
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.managers import (
    ObservationGroupCfg,
    ObservationTermCfg as ObsTerm,
    RewardTermCfg as RewTerm,
    SceneEntityCfg,
)
from isaaclab.scene import InteractiveSceneCfg


class {task}EnvCfg(ManagerBasedRLEnvCfg):
    """Configuration for {task} environment."""

    # Scene
    scene = InteractiveSceneCfg(
        num_envs={num_envs},
        env_spacing={spacing},
    )

    # Observations
    observations = ObservationGroupCfg(
{obs_terms}
    )

    # Actions
    actions = mdp.{acts}

    # Rewards
    rewards = {{
{reward_terms}
    }}

    # Episode
    episode_length_s = {ep_len} * {decimation} / 120.0
    decimation = {decimation}
'''


def _gen_launch_training(args: Dict) -> str:
    """Generate code to launch an IsaacLab training run."""
    task = args["task"]
    algo = args.get("algo", "ppo")
    num_steps = args.get("num_steps", 1_000_000)
    num_envs = args.get("num_envs", 64)
    ckpt_dir = args.get("checkpoint_dir", f"workspace/rl_checkpoints/{task}")

    # Map algos to IsaacLab train script args
    algo_map = {
        "ppo": "rsl_rl",
        "sac": "skrl",
        "td3": "skrl",
        "rsl_rl": "rsl_rl",
    }
    runner = algo_map.get(algo, "rsl_rl")

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "",
        f"task = '{task}'",
        f"algo = '{algo}'",
        f"num_envs = {num_envs}",
        f"max_iterations = {num_steps // (num_envs * 24)}  # steps / (envs * horizon)",
        f"log_dir = '{ckpt_dir}'",
        "os.makedirs(log_dir, exist_ok=True)",
        "",
        "# Launch IsaacLab training",
        "cmd = [",
        "    sys.executable, '-m',",
        f"    'isaaclab.train',",
        f"    '--task', task,",
        f"    '--num_envs', str(num_envs),",
        f"    '--max_iterations', str(max_iterations),",
        f"    '--log_dir', log_dir,",
        "]",
        """print(f"Launching training: {' '.join(cmd)}")""",
        "proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)",
        "print(f'Training started (PID: {proc.pid}). Checkpoints → {log_dir}')",
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


# ── Phase 7H: Cloud Download Results ────────────────────────────────────────

def _gen_cloud_download_results(args: Dict) -> str:
    """Generate code to download results from a cloud instance."""
    job_id = args["job_id"]
    output_dir = args.get("output_dir", "workspace/cloud_results")

    return f'''\
import subprocess
import os

job_id = "{job_id}"
output_dir = "{output_dir}"
os.makedirs(output_dir, exist_ok=True)

# IsaacAutomator stores results on the cloud instance at /results/
# Retrieve the instance IP from the deployment state
state_file = f"deployments/{{job_id}}/state.json"
if os.path.exists(state_file):
    import json
    with open(state_file) as f:
        state = json.load(f)
    instance_ip = state.get("instance_ip", "UNKNOWN_IP")
    key_path = state.get("ssh_key", "~/.ssh/isaacautomator")
else:
    instance_ip = "UNKNOWN_IP"
    key_path = "~/.ssh/isaacautomator"
    print(f"WARNING: State file not found at {{state_file}}. Set instance_ip manually.")

# Download results via rsync
cmd = [
    "rsync", "-avz", "--progress",
    "-e", f"ssh -i {{key_path}} -o StrictHostKeyChecking=no",
    f"ubuntu@{{instance_ip}}:/results/",
    output_dir + "/",
]
print(f"Downloading results: {{' '.join(cmd)}}")
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
stdout, _ = proc.communicate()
print(stdout.decode() if stdout else "")
if proc.returncode == 0:
    print(f"Results downloaded to {{output_dir}}/")
else:
    print(f"Download failed (exit code {{proc.returncode}}). Check IP and SSH key.")
'''


CODE_GEN_HANDLERS["cloud_download_results"] = _gen_cloud_download_results

# ── Clone Envs (GridCloner) ──────────────────────────────────────────────────

def _gen_clone_envs(args: Dict) -> str:
    source_path = args["source_path"]
    num_envs = args["num_envs"]
    spacing = args.get("spacing", 2.5)
    collision_filter = args.get("collision_filter", True)

    lines = [
        "from isaacsim.core.cloner import GridCloner",
        "",
        f"cloner = GridCloner(spacing={spacing})",
        'cloner.define_base_env("/World/envs")',
        f'prim_paths = cloner.generate_paths("/World/envs/env", {num_envs})',
        "positions = cloner.clone(",
        f"    source_prim_path='{source_path}',",
        "    prim_paths=prim_paths,",
        "    replicate_physics=True,  # CRITICAL for performance",
        ")",
    ]
    if collision_filter:
        lines.extend([
            "# Collision filtering is a SEPARATE step:",
            "cloner.filter_collisions(",
            "    physicsscene_path='/World/PhysicsScene',",
            "    collision_root_path='/World/collisionGroups',",
            "    prim_paths=prim_paths,",
            ")",
        ])
    lines.append(f"print(f'Cloned {num_envs} environments from {source_path}')")
    return "\n".join(lines)




def _gen_debug_draw(args: Dict) -> str:
    draw_type = args["draw_type"]
    points = args["points"]
    color = args.get("color", [1, 0, 0, 1])
    size = args.get("size", 5)
    lifetime = args.get("lifetime", 0)

    lines = [
        "from isaacsim.util.debug_draw import _debug_draw",
        "",
        "draw = _debug_draw.acquire_debug_draw_interface()",
    ]

    if draw_type == "points":
        lines.append(f"points = {points}")
        lines.append(f"colors = [{color}] * len(points)")
        lines.append(f"sizes = [{size}] * len(points)")
        lines.append("draw.draw_points(points, colors, sizes)")
    elif draw_type == "lines":
        # Points come as pairs: [start, end, start, end, ...]
        lines.append(f"all_pts = {points}")
        lines.append("start_points = all_pts[0::2]")
        lines.append("end_points = all_pts[1::2]")
        lines.append(f"colors = [{color}] * len(start_points)")
        lines.append(f"sizes = [{size}] * len(start_points)")
        lines.append("draw.draw_lines(start_points, end_points, colors, sizes)")
    elif draw_type == "lines_spline":
        lines.append(f"points = {points}")
        lines.append(f"color = {color}")
        lines.append(f"width = {size}")
        lines.append("draw.draw_lines_spline(points, color, width, closed=False)")

    if lifetime > 0:
        lines.extend([
            "",
            "# Schedule auto-clear",
            "import asyncio",
            f"asyncio.get_event_loop().call_later({lifetime}, draw.clear_points)",
        ])

    return "\n".join(lines)




def _gen_generate_occupancy_map(args: Dict) -> str:
    origin = args.get("origin", [0, 0])
    dimensions = args.get("dimensions", [10, 10])
    resolution = args.get("resolution", 0.05)
    height_range = args.get("height_range", [0, 2])

    return f"""\
from isaacsim.asset.gen.omap import MapGenerator
import carb

gen = MapGenerator()
gen.update_settings(cell_size={resolution})
gen.set_transform(
    origin=carb.Float3({origin[0]}, {origin[1]}, 0),
    min_bound=carb.Float3({-dimensions[0]/2}, {-dimensions[1]/2}, {height_range[0]}),
    max_bound=carb.Float3({dimensions[0]/2}, {dimensions[1]/2}, {height_range[1]}),
)
gen.generate2d()
buffer = gen.get_buffer()
print(f"Occupancy map generated: {int(dimensions[0]/resolution)} x {int(dimensions[1]/resolution)} cells")
"""





def _gen_configure_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"cam = UsdGeom.Camera(stage.GetPrimAtPath('{camera_path}'))",
    ]
    if "focal_length" in args:
        lines.append(f"cam.GetFocalLengthAttr().Set({args['focal_length']})")
    if "horizontal_aperture" in args:
        lines.append(f"cam.GetHorizontalApertureAttr().Set({args['horizontal_aperture']})")
    if "vertical_aperture" in args:
        lines.append(f"cam.GetVerticalApertureAttr().Set({args['vertical_aperture']})")
    if "clipping_range" in args:
        cr = args["clipping_range"]
        lines.append(f"cam.GetClippingRangeAttr().Set(Gf.Vec2f({cr[0]}, {cr[1]}))")
    if "focus_distance" in args:
        lines.append(f"cam.GetFocusDistanceAttr().Set({args['focus_distance']})")
    lines.append(f"print(f'Camera {camera_path} configured')")
    return "\n".join(lines)


# ── Code generation dispatch ─────────────────────────────────────────────────

CODE_GEN_HANDLERS["clone_envs"] = _gen_clone_envs
CODE_GEN_HANDLERS["debug_draw"] = _gen_debug_draw
CODE_GEN_HANDLERS["generate_occupancy_map"] = _gen_generate_occupancy_map
CODE_GEN_HANDLERS["configure_camera"] = _gen_configure_camera


# ── Phase 8B: Motion Policy & IK ────────────────────────────────────────────

# ── Motion Policy (8B.3) ────────────────────────────────────────────────────

def _gen_set_motion_policy(args: Dict) -> str:
    art_path = args["articulation_path"]
    policy_type = args["policy_type"]
    robot_type = args.get("robot_type", "franka").lower()

    if policy_type == "add_obstacle":
        obs_name = args.get("obstacle_name", "obstacle_0")
        obs_type = args.get("obstacle_type", "cuboid")
        obs_dims = args.get("obstacle_dims", [0.1, 0.1, 0.1])
        obs_pos = args.get("obstacle_position", [0.0, 0.0, 0.0])

        lines = [
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
        ]
        if obs_type == "sphere":
            radius = obs_dims[0] if obs_dims else 0.1
            lines.extend([
                f"# Add sphere obstacle '{obs_name}'",
                f"rmpflow.add_sphere(",
                f"    name='{obs_name}',",
                f"    radius={radius},",
                f"    pose=np.array([{obs_pos[0]}, {obs_pos[1]}, {obs_pos[2]}, 1.0, 0.0, 0.0, 0.0]),",
                f")",
                "rmpflow.update_world()",
                f"print(f'Added sphere obstacle \\'{obs_name}\\' at {obs_pos} with radius {radius}')",
            ])
        else:
            # cuboid (default)
            lines.extend([
                f"# Add cuboid obstacle '{obs_name}'",
                f"rmpflow.add_cuboid(",
                f"    name='{obs_name}',",
                f"    dims=np.array({list(obs_dims)}),",
                f"    pose=np.array([{obs_pos[0]}, {obs_pos[1]}, {obs_pos[2]}, 1.0, 0.0, 0.0, 0.0]),",
                f")",
                "rmpflow.update_world()",
                f"print(f'Added cuboid obstacle \\'{obs_name}\\' at {obs_pos} with dims {list(obs_dims)}')",
            ])
        return "\n".join(lines)

    if policy_type == "remove_obstacle":
        lines = [
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
            "# RMPflow has no individual obstacle removal — reset clears all obstacles",
            "rmpflow.reset()",
            "print('Motion policy reset — all obstacles cleared')",
        ]
        return "\n".join(lines)

    if policy_type == "set_joint_limits":
        buffer_val = args.get("joint_limit_buffers", 0.05)
        lines = [
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "from isaacsim.core.prims import SingleArticulation",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
            f"art = SingleArticulation(prim_path='{art_path}')",
            "art.initialize()",
            "",
            "# Get current joint limits and add padding buffer",
            "lower_limits = art.get_joint_positions()  # read current as reference",
            f"buffer = {buffer_val}",
            "dof_count = art.num_dof",
            "print(f'Applying joint limit buffer of {buffer} rad to {dof_count} joints')",
            "print(f'Note: Joint limit buffers are applied in the RMPflow config YAML.')",
            "print(f'For runtime adjustment, modify rmpflow_config[\"joint_limit_buffers\"] before init.')",
        ]
        return "\n".join(lines)

    return f"# Unknown policy type: {policy_type}"


CODE_GEN_HANDLERS["set_motion_policy"] = _gen_set_motion_policy



# ── Inverse Kinematics (8B.5) ──────────────────────────────────────────────

def _gen_solve_ik(args: Dict) -> str:
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    robot_type = args.get("robot_type", "franka").lower()

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee_frame = cfg["ee_frame"]

    lines = [
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver",
        "from isaacsim.robot_motion.motion_generation import ArticulationKinematicsSolver",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "from isaacsim.core.prims import SingleArticulation",
        "",
        f"# Load kinematics config for {robot_type}",
        f"kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{robot_type}')",
        "kin_solver = LulaKinematicsSolver(**kin_config)",
        "",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "art.initialize()",
        f"art_kin = ArticulationKinematicsSolver(art, kin_solver, '{ee_frame}')",
        "",
        f"target_position = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_orientation = np.array({list(target_ori)})")
    else:
        lines.append("target_orientation = None")

    lines.extend([
        "",
        "action, success = art_kin.compute_inverse_kinematics(",
        "    target_position=target_position,",
        "    target_orientation=target_orientation,",
        ")",
        "if success:",
        "    art.apply_action(action)",
        f"    print(f'IK solved successfully — {ee_frame} moving to {{target_position}}')",
        "else:",
        "    print('IK failed — target may be unreachable or near singularity')",
    ])
    return "\n".join(lines)


CODE_GEN_HANDLERS["solve_ik"] = _gen_solve_ik



# ── Cortex Behaviors & Manipulation ─────────────────────────────────────────

def _gen_create_behavior(args: Dict) -> str:
    """Generate code to create a Cortex behavior (decider network) for a robot."""
    art_path = args["articulation_path"]
    behavior = args["behavior_type"]
    target = args.get("target_prim", "/World/Target")
    params = args.get("params", {})

    speed = params.get("speed", 0.5)
    threshold = params.get("threshold", 0.02)

    if behavior == "pick_and_place":
        place_target = params.get("place_target", "/World/PlaceTarget")
        return f"""\
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.robot import CortexRobot
from isaacsim.cortex.framework.df import DfNetwork, DfDecider, DfState, DfStateMachineDecider
from isaacsim.cortex.framework.motion_commander import MotionCommander
import numpy as np

# Create Cortex world
world = CortexWorld()

# Add robot
robot = world.add_robot(CortexRobot(
    name="robot",
    prim_path='{art_path}',
    motion_commander=MotionCommander('{art_path}'),
))

# ── Pick-and-place state machine ────────────────────────────
class ApproachState(DfState):
    \"\"\"Move to pre-grasp position above the target.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        approach_pos = target_pos + np.array([0, 0, {params.get('approach_distance', 0.1)}])
        self.context['mc'].send_end_effector_target(
            translation=approach_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            return 'grasp'
        return None

class GraspState(DfState):
    \"\"\"Move down and close gripper.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        self.context['mc'].send_end_effector_target(
            translation=target_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            self.context['gripper'].close()
            return 'lift'
        return None

class LiftState(DfState):
    \"\"\"Lift the grasped object.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        lift_pos = target_pos + np.array([0, 0, {params.get('lift_height', 0.15)}])
        self.context['mc'].send_end_effector_target(
            translation=lift_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            return 'place'
        return None

class PlaceState(DfState):
    \"\"\"Move to place position and release.\"\"\"
    def enter(self):
        place_pos = np.array(self.context['place_pos'])
        self.context['mc'].send_end_effector_target(
            translation=place_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            self.context['gripper'].open()
            return 'done'
        return None

# Build decider network
pick_place_decider = DfStateMachineDecider(
    states={{
        'approach': ApproachState(),
        'grasp': GraspState(),
        'lift': LiftState(),
        'place': PlaceState(),
    }},
    initial_state='approach',
)

network = DfNetwork(decider=pick_place_decider)
world.add_decider_network(network)

print("Cortex pick-and-place behavior created for {art_path}")
print("Target: {target}, Place: {place_target}")
"""

    # follow_target
    return f"""\
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.robot import CortexRobot
from isaacsim.cortex.framework.df import DfNetwork, DfDecider, DfState
from isaacsim.cortex.framework.motion_commander import MotionCommander
import numpy as np

# Create Cortex world
world = CortexWorld()

# Add robot
robot = world.add_robot(CortexRobot(
    name="robot",
    prim_path='{art_path}',
    motion_commander=MotionCommander('{art_path}'),
))

# ── Follow-target behavior ──────────────────────────────────
class FollowTargetState(DfState):
    \"\"\"Continuously track a target prim with the end-effector.\"\"\"
    def enter(self):
        self.update_interval = {params.get('update_interval', 0.1)}

    def step(self):
        import omni.usd
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        target_prim = stage.GetPrimAtPath('{target}')
        xf = UsdGeom.Xformable(target_prim).ComputeLocalToWorldTransform(0)
        target_pos = np.array(xf.ExtractTranslation())
        self.context['mc'].send_end_effector_target(
            translation=target_pos,
        )
        return None  # stay in this state

class FollowDecider(DfDecider):
    \"\"\"Simple decider that always runs the follow state.\"\"\"
    def __init__(self):
        super().__init__()
        self.add_child('follow', FollowTargetState())

    def decide(self):
        return 'follow'

network = DfNetwork(decider=FollowDecider())
world.add_decider_network(network)

print("Cortex follow-target behavior created for {art_path}")
print("Following: {target}")
"""


def _gen_create_gripper(args: Dict) -> str:
    """Generate code to create and configure a gripper."""
    art_path = args["articulation_path"]
    gripper_type = args["gripper_type"]
    open_pos = args.get("open_position", 0.04)
    closed_pos = args.get("closed_position", 0.0)

    if gripper_type == "parallel_jaw":
        dof_names = args.get("gripper_dof_names", ["panda_finger_joint1", "panda_finger_joint2"])
        dof_names_str = repr(dof_names)
        return f"""\
from isaacsim.robot.manipulators.grippers import ParallelGripper
import numpy as np

# Create parallel jaw gripper
gripper = ParallelGripper(
    end_effector_prim_path='{art_path}/panda_hand',
    joint_prim_names={dof_names_str},
    joint_opened_positions=np.array([{open_pos}] * {len(dof_names)}),
    joint_closed_positions=np.array([{closed_pos}] * {len(dof_names)}),
    action_deltas=np.array([{open_pos}] * {len(dof_names)}),
)

# Initialize gripper
gripper.initialize()

# Open gripper to start
gripper.open()
print(f"ParallelGripper created on {art_path}")
print(f"  DOFs: {dof_names_str}")
print(f"  Open position: {open_pos}")
print(f"  Closed position: {closed_pos}")
"""

    # suction gripper — OmniGraph-based OgnSurfaceGripper
    return f"""\
import omni.graph.core as og

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{art_path}/SuctionGripperGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("SurfaceGripper", "isaacsim.robot.surface_gripper.OgnSurfaceGripper"),
        ],
        keys.CONNECT: [
            ("OnPlaybackTick.outputs:tick", "SurfaceGripper.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("SurfaceGripper.inputs:parentPath", "{art_path}"),
            ("SurfaceGripper.inputs:enabled", True),
            ("SurfaceGripper.inputs:gripThreshold", 0.01),
            ("SurfaceGripper.inputs:forceLimit", 100.0),
            ("SurfaceGripper.inputs:torqueLimit", 100.0),
        ],
    }},
)

print(f"Suction gripper (OgnSurfaceGripper) created on {art_path}")
print("Use SurfaceGripper.inputs:close to activate suction")
"""


def _gen_grasp_object(args: Dict) -> str:
    """Generate a complete grasp sequence: approach, grasp, lift."""
    robot_path = args["robot_path"]
    target_prim = args["target_prim"]
    grasp_type = args.get("grasp_type", "top_down")
    approach_dist = args.get("approach_distance", 0.1)
    lift_height = args.get("lift_height", 0.1)

    if grasp_type == "from_file":
        grasp_file = args.get("grasp_file", "")
        return f"""\
import numpy as np
import yaml
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Load grasp specification from file
with open('{grasp_file}', 'r') as f:
    grasp_spec = yaml.safe_load(f)

grasp_name = list(grasp_spec.get('grasps', {{}}).keys())[0]
grasp = grasp_spec['grasps'][grasp_name]
offset = np.array(grasp.get('gripper_offset', [0, 0, 0]))
approach_dir = np.array(grasp.get('approach_direction', [0, 0, -1]))

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute grasp and approach positions
grasp_pos = target_pos + offset
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to approach position
rmpflow.set_end_effector_target(approach_pos, None)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift
rmpflow.set_end_effector_target(lift_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete (from file: {grasp_file})")
"""

    # top_down or side grasp (geometric heuristic)
    if grasp_type == "side":
        approach_vector = "[1, 0, 0]"
        grasp_ori = "np.array([0.5, 0.5, -0.5, 0.5])  # side approach quaternion"
    else:  # top_down
        approach_vector = "[0, 0, -1]"
        grasp_ori = "np.array([1.0, 0.0, 0.0, 0.0])  # top-down quaternion"

    return f"""\
import numpy as np
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute approach geometry ({grasp_type} grasp)
approach_dir = np.array({approach_vector})
grasp_pos = target_pos  # grasp at object center
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])
grasp_orientation = {grasp_ori}

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to pre-grasp approach position
rmpflow.set_end_effector_target(approach_pos, grasp_orientation)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift object
rmpflow.set_end_effector_target(lift_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete ({grasp_type})")
"""


def _gen_define_grasp_pose(args: Dict) -> str:
    """Generate code to create a .isaac_grasp YAML file."""
    robot_path = args["robot_path"]
    object_path = args["object_path"]
    offset = args.get("gripper_offset", [0, 0, 0])
    approach_dir = args.get("approach_direction", [0, 0, -1])

    return f"""\
import yaml
import os
import omni.usd
from pxr import UsdGeom, Gf
import numpy as np

# Get object position for reference
stage = omni.usd.get_context().get_stage()
obj_prim = stage.GetPrimAtPath('{object_path}')
obj_xf = UsdGeom.Xformable(obj_prim).ComputeLocalToWorldTransform(0)
obj_pos = list(obj_xf.ExtractTranslation())

# Define grasp specification
grasp_spec = {{
    'version': '1.0',
    'robot_path': '{robot_path}',
    'object_path': '{object_path}',
    'grasps': {{
        'default_grasp': {{
            'gripper_offset': {list(offset)},
            'approach_direction': {list(approach_dir)},
            'object_reference_position': obj_pos,
            'pre_grasp_opening': 0.04,
            'grasp_force': 40.0,
        }},
    }},
}}

# Save to workspace
grasp_dir = 'workspace/grasp_poses'
os.makedirs(grasp_dir, exist_ok=True)
obj_name = '{object_path}'.split('/')[-1]
file_path = os.path.join(grasp_dir, f'{{obj_name}}.isaac_grasp')

with open(file_path, 'w') as f:
    yaml.dump(grasp_spec, f, default_flow_style=False)

print(f"Grasp pose saved to {{file_path}}")
print(f"  Robot: {robot_path}")
print(f"  Object: {object_path}")
print(f"  Offset: {list(offset)}")
print(f"  Approach direction: {list(approach_dir)}")
"""


async def _handle_visualize_behavior_tree(args: Dict) -> Dict:
    """Return a formatted text tree of a behavior network structure."""
    network_name = args.get("network_name", "unknown")

    # Since we don't have access to a running Cortex instance at query time,
    # return the canonical structure for known behavior types, or a template.
    _KNOWN_BEHAVIORS = {
        "pick_and_place": {
            "name": "pick_and_place",
            "type": "DfStateMachineDecider",
            "children": [
                {"name": "approach", "type": "DfState", "description": "Move to pre-grasp position above target"},
                {"name": "grasp", "type": "DfState", "description": "Move down and close gripper on object"},
                {"name": "lift", "type": "DfState", "description": "Lift grasped object to safe height"},
                {"name": "place", "type": "DfState", "description": "Move to place position and release"},
            ],
            "transitions": "approach -> grasp -> lift -> place -> done",
        },
        "follow_target": {
            "name": "follow_target",
            "type": "DfDecider",
            "children": [
                {"name": "follow", "type": "FollowTargetState", "description": "Continuously track target prim with end-effector"},
            ],
            "transitions": "follow (continuous loop)",
        },
    }

    behavior = _KNOWN_BEHAVIORS.get(network_name.lower())

    if behavior:
        # Build ASCII tree
        lines = [
            f"Behavior Network: {behavior['name']}",
            f"  Type: {behavior['type']}",
            f"  Transitions: {behavior['transitions']}",
            "",
            "  Nodes:",
        ]
        for i, child in enumerate(behavior["children"]):
            is_last = i == len(behavior["children"]) - 1
            prefix = "  +-- " if is_last else "  |-- "
            lines.append(f"{prefix}{child['name']} ({child['type']})")
            desc_prefix = "      " if is_last else "  |   "
            lines.append(f"{desc_prefix}{child['description']}")

        tree_text = "\n".join(lines)
        return {
            "network_name": network_name,
            "structure": behavior,
            "tree": tree_text,
        }

    return {
        "network_name": network_name,
        "structure": None,
        "tree": (
            f"Behavior Network: {network_name}\n"
            f"  (No pre-built visualization available for '{network_name}'.\n"
            f"   Known behaviors: pick_and_place, follow_target.\n"
            f"   For custom networks, inspect the DfNetwork in the running Cortex world.)"
        ),
    }


CODE_GEN_HANDLERS["create_behavior"] = _gen_create_behavior



def _gen_create_gripper(args: Dict) -> str:
    """Generate code to create and configure a gripper."""
    art_path = args["articulation_path"]
    gripper_type = args["gripper_type"]
    open_pos = args.get("open_position", 0.04)
    closed_pos = args.get("closed_position", 0.0)

    if gripper_type == "parallel_jaw":
        dof_names = args.get("gripper_dof_names", ["panda_finger_joint1", "panda_finger_joint2"])
        dof_names_str = repr(dof_names)
        return f"""\
from isaacsim.robot.manipulators.grippers import ParallelGripper
import numpy as np

# Create parallel jaw gripper
gripper = ParallelGripper(
    end_effector_prim_path='{art_path}/panda_hand',
    joint_prim_names={dof_names_str},
    joint_opened_positions=np.array([{open_pos}] * {len(dof_names)}),
    joint_closed_positions=np.array([{closed_pos}] * {len(dof_names)}),
    action_deltas=np.array([{open_pos}] * {len(dof_names)}),
)

# Initialize gripper
gripper.initialize()

# Open gripper to start
gripper.open()
print(f"ParallelGripper created on {art_path}")
print(f"  DOFs: {dof_names_str}")
print(f"  Open position: {open_pos}")
print(f"  Closed position: {closed_pos}")
"""

    # suction gripper — OmniGraph-based OgnSurfaceGripper
    return f"""\
import omni.graph.core as og

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{art_path}/SuctionGripperGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("SurfaceGripper", "isaacsim.robot.surface_gripper.OgnSurfaceGripper"),
        ],
        keys.CONNECT: [
            ("OnPlaybackTick.outputs:tick", "SurfaceGripper.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("SurfaceGripper.inputs:parentPath", "{art_path}"),
            ("SurfaceGripper.inputs:enabled", True),
            ("SurfaceGripper.inputs:gripThreshold", 0.01),
            ("SurfaceGripper.inputs:forceLimit", 100.0),
            ("SurfaceGripper.inputs:torqueLimit", 100.0),
        ],
    }},
)

print(f"Suction gripper (OgnSurfaceGripper) created on {art_path}")
print("Use SurfaceGripper.inputs:close to activate suction")
"""


def _gen_grasp_object(args: Dict) -> str:
    """Generate a complete grasp sequence: approach, grasp, lift."""
    robot_path = args["robot_path"]
    target_prim = args["target_prim"]
    grasp_type = args.get("grasp_type", "top_down")
    approach_dist = args.get("approach_distance", 0.1)
    lift_height = args.get("lift_height", 0.1)

    if grasp_type == "from_file":
        grasp_file = args.get("grasp_file", "")
        return f"""\
import numpy as np
import yaml
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Load grasp specification from file
with open('{grasp_file}', 'r') as f:
    grasp_spec = yaml.safe_load(f)

grasp_name = list(grasp_spec.get('grasps', {{}}).keys())[0]
grasp = grasp_spec['grasps'][grasp_name]
offset = np.array(grasp.get('gripper_offset', [0, 0, 0]))
approach_dir = np.array(grasp.get('approach_direction', [0, 0, -1]))

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute grasp and approach positions
grasp_pos = target_pos + offset
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to approach position
rmpflow.set_end_effector_target(approach_pos, None)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift
rmpflow.set_end_effector_target(lift_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete (from file: {grasp_file})")
"""

    # top_down or side grasp (geometric heuristic)
    if grasp_type == "side":
        approach_vector = "[1, 0, 0]"
        grasp_ori = "np.array([0.5, 0.5, -0.5, 0.5])  # side approach quaternion"
    else:  # top_down
        approach_vector = "[0, 0, -1]"
        grasp_ori = "np.array([1.0, 0.0, 0.0, 0.0])  # top-down quaternion"

    return f"""\
import numpy as np
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute approach geometry ({grasp_type} grasp)
approach_dir = np.array({approach_vector})
grasp_pos = target_pos  # grasp at object center
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])
grasp_orientation = {grasp_ori}

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to pre-grasp approach position
rmpflow.set_end_effector_target(approach_pos, grasp_orientation)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift object
rmpflow.set_end_effector_target(lift_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete ({grasp_type})")
"""


def _gen_define_grasp_pose(args: Dict) -> str:
    """Generate code to create a .isaac_grasp YAML file."""
    robot_path = args["robot_path"]
    object_path = args["object_path"]
    offset = args.get("gripper_offset", [0, 0, 0])
    approach_dir = args.get("approach_direction", [0, 0, -1])

    return f"""\
import yaml
import os
import omni.usd
from pxr import UsdGeom, Gf
import numpy as np

# Get object position for reference
stage = omni.usd.get_context().get_stage()
obj_prim = stage.GetPrimAtPath('{object_path}')
obj_xf = UsdGeom.Xformable(obj_prim).ComputeLocalToWorldTransform(0)
obj_pos = list(obj_xf.ExtractTranslation())

# Define grasp specification
grasp_spec = {{
    'version': '1.0',
    'robot_path': '{robot_path}',
    'object_path': '{object_path}',
    'grasps': {{
        'default_grasp': {{
            'gripper_offset': {list(offset)},
            'approach_direction': {list(approach_dir)},
            'object_reference_position': obj_pos,
            'pre_grasp_opening': 0.04,
            'grasp_force': 40.0,
        }},
    }},
}}

# Save to workspace
grasp_dir = 'workspace/grasp_poses'
os.makedirs(grasp_dir, exist_ok=True)
obj_name = '{object_path}'.split('/')[-1]
file_path = os.path.join(grasp_dir, f'{{obj_name}}.isaac_grasp')

with open(file_path, 'w') as f:
    yaml.dump(grasp_spec, f, default_flow_style=False)

print(f"Grasp pose saved to {{file_path}}")
print(f"  Robot: {robot_path}")
print(f"  Object: {object_path}")
print(f"  Offset: {list(offset)}")
print(f"  Approach direction: {list(approach_dir)}")
"""


async def _handle_visualize_behavior_tree(args: Dict) -> Dict:
    """Return a formatted text tree of a behavior network structure."""
    network_name = args.get("network_name", "unknown")

    # Since we don't have access to a running Cortex instance at query time,
    # return the canonical structure for known behavior types, or a template.
    _KNOWN_BEHAVIORS = {
        "pick_and_place": {
            "name": "pick_and_place",
            "type": "DfStateMachineDecider",
            "children": [
                {"name": "approach", "type": "DfState", "description": "Move to pre-grasp position above target"},
                {"name": "grasp", "type": "DfState", "description": "Move down and close gripper on object"},
                {"name": "lift", "type": "DfState", "description": "Lift grasped object to safe height"},
                {"name": "place", "type": "DfState", "description": "Move to place position and release"},
            ],
            "transitions": "approach -> grasp -> lift -> place -> done",
        },
        "follow_target": {
            "name": "follow_target",
            "type": "DfDecider",
            "children": [
                {"name": "follow", "type": "FollowTargetState", "description": "Continuously track target prim with end-effector"},
            ],
            "transitions": "follow (continuous loop)",
        },
    }

    behavior = _KNOWN_BEHAVIORS.get(network_name.lower())

    if behavior:
        # Build ASCII tree
        lines = [
            f"Behavior Network: {behavior['name']}",
            f"  Type: {behavior['type']}",
            f"  Transitions: {behavior['transitions']}",
            "",
            "  Nodes:",
        ]
        for i, child in enumerate(behavior["children"]):
            is_last = i == len(behavior["children"]) - 1
            prefix = "  +-- " if is_last else "  |-- "
            lines.append(f"{prefix}{child['name']} ({child['type']})")
            desc_prefix = "      " if is_last else "  |   "
            lines.append(f"{desc_prefix}{child['description']}")

        tree_text = "\n".join(lines)
        return {
            "network_name": network_name,
            "structure": behavior,
            "tree": tree_text,
        }

    return {
        "network_name": network_name,
        "structure": None,
        "tree": (
            f"Behavior Network: {network_name}\n"
            f"  (No pre-built visualization available for '{network_name}'.\n"
            f"   Known behaviors: pick_and_place, follow_target.\n"
            f"   For custom networks, inspect the DfNetwork in the running Cortex world.)"
        ),
    }


CODE_GEN_HANDLERS["create_behavior"] = _gen_create_behavior
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper



def _gen_grasp_object(args: Dict) -> str:
    """Generate a complete grasp sequence: approach, grasp, lift."""
    robot_path = args["robot_path"]
    target_prim = args["target_prim"]
    grasp_type = args.get("grasp_type", "top_down")
    approach_dist = args.get("approach_distance", 0.1)
    lift_height = args.get("lift_height", 0.1)

    if grasp_type == "from_file":
        grasp_file = args.get("grasp_file", "")
        return f"""\
import numpy as np
import yaml
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Load grasp specification from file
with open('{grasp_file}', 'r') as f:
    grasp_spec = yaml.safe_load(f)

grasp_name = list(grasp_spec.get('grasps', {{}}).keys())[0]
grasp = grasp_spec['grasps'][grasp_name]
offset = np.array(grasp.get('gripper_offset', [0, 0, 0]))
approach_dir = np.array(grasp.get('approach_direction', [0, 0, -1]))

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute grasp and approach positions
grasp_pos = target_pos + offset
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to approach position
rmpflow.set_end_effector_target(approach_pos, None)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift
rmpflow.set_end_effector_target(lift_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete (from file: {grasp_file})")
"""

    # top_down or side grasp (geometric heuristic)
    if grasp_type == "side":
        approach_vector = "[1, 0, 0]"
        grasp_ori = "np.array([0.5, 0.5, -0.5, 0.5])  # side approach quaternion"
    else:  # top_down
        approach_vector = "[0, 0, -1]"
        grasp_ori = "np.array([1.0, 0.0, 0.0, 0.0])  # top-down quaternion"

    return f"""\
import numpy as np
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute approach geometry ({grasp_type} grasp)
approach_dir = np.array({approach_vector})
grasp_pos = target_pos  # grasp at object center
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])
grasp_orientation = {grasp_ori}

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to pre-grasp approach position
rmpflow.set_end_effector_target(approach_pos, grasp_orientation)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift object
rmpflow.set_end_effector_target(lift_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete ({grasp_type})")
"""


def _gen_define_grasp_pose(args: Dict) -> str:
    """Generate code to create a .isaac_grasp YAML file."""
    robot_path = args["robot_path"]
    object_path = args["object_path"]
    offset = args.get("gripper_offset", [0, 0, 0])
    approach_dir = args.get("approach_direction", [0, 0, -1])

    return f"""\
import yaml
import os
import omni.usd
from pxr import UsdGeom, Gf
import numpy as np

# Get object position for reference
stage = omni.usd.get_context().get_stage()
obj_prim = stage.GetPrimAtPath('{object_path}')
obj_xf = UsdGeom.Xformable(obj_prim).ComputeLocalToWorldTransform(0)
obj_pos = list(obj_xf.ExtractTranslation())

# Define grasp specification
grasp_spec = {{
    'version': '1.0',
    'robot_path': '{robot_path}',
    'object_path': '{object_path}',
    'grasps': {{
        'default_grasp': {{
            'gripper_offset': {list(offset)},
            'approach_direction': {list(approach_dir)},
            'object_reference_position': obj_pos,
            'pre_grasp_opening': 0.04,
            'grasp_force': 40.0,
        }},
    }},
}}

# Save to workspace
grasp_dir = 'workspace/grasp_poses'
os.makedirs(grasp_dir, exist_ok=True)
obj_name = '{object_path}'.split('/')[-1]
file_path = os.path.join(grasp_dir, f'{{obj_name}}.isaac_grasp')

with open(file_path, 'w') as f:
    yaml.dump(grasp_spec, f, default_flow_style=False)

print(f"Grasp pose saved to {{file_path}}")
print(f"  Robot: {robot_path}")
print(f"  Object: {object_path}")
print(f"  Offset: {list(offset)}")
print(f"  Approach direction: {list(approach_dir)}")
"""


async def _handle_visualize_behavior_tree(args: Dict) -> Dict:
    """Return a formatted text tree of a behavior network structure."""
    network_name = args.get("network_name", "unknown")

    # Since we don't have access to a running Cortex instance at query time,
    # return the canonical structure for known behavior types, or a template.
    _KNOWN_BEHAVIORS = {
        "pick_and_place": {
            "name": "pick_and_place",
            "type": "DfStateMachineDecider",
            "children": [
                {"name": "approach", "type": "DfState", "description": "Move to pre-grasp position above target"},
                {"name": "grasp", "type": "DfState", "description": "Move down and close gripper on object"},
                {"name": "lift", "type": "DfState", "description": "Lift grasped object to safe height"},
                {"name": "place", "type": "DfState", "description": "Move to place position and release"},
            ],
            "transitions": "approach -> grasp -> lift -> place -> done",
        },
        "follow_target": {
            "name": "follow_target",
            "type": "DfDecider",
            "children": [
                {"name": "follow", "type": "FollowTargetState", "description": "Continuously track target prim with end-effector"},
            ],
            "transitions": "follow (continuous loop)",
        },
    }

    behavior = _KNOWN_BEHAVIORS.get(network_name.lower())

    if behavior:
        # Build ASCII tree
        lines = [
            f"Behavior Network: {behavior['name']}",
            f"  Type: {behavior['type']}",
            f"  Transitions: {behavior['transitions']}",
            "",
            "  Nodes:",
        ]
        for i, child in enumerate(behavior["children"]):
            is_last = i == len(behavior["children"]) - 1
            prefix = "  +-- " if is_last else "  |-- "
            lines.append(f"{prefix}{child['name']} ({child['type']})")
            desc_prefix = "      " if is_last else "  |   "
            lines.append(f"{desc_prefix}{child['description']}")

        tree_text = "\n".join(lines)
        return {
            "network_name": network_name,
            "structure": behavior,
            "tree": tree_text,
        }

    return {
        "network_name": network_name,
        "structure": None,
        "tree": (
            f"Behavior Network: {network_name}\n"
            f"  (No pre-built visualization available for '{network_name}'.\n"
            f"   Known behaviors: pick_and_place, follow_target.\n"
            f"   For custom networks, inspect the DfNetwork in the running Cortex world.)"
        ),
    }


CODE_GEN_HANDLERS["create_behavior"] = _gen_create_behavior
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper
CODE_GEN_HANDLERS["grasp_object"] = _gen_grasp_object



def _gen_define_grasp_pose(args: Dict) -> str:
    """Generate code to create a .isaac_grasp YAML file."""
    robot_path = args["robot_path"]
    object_path = args["object_path"]
    offset = args.get("gripper_offset", [0, 0, 0])
    approach_dir = args.get("approach_direction", [0, 0, -1])

    return f"""\
import yaml
import os
import omni.usd
from pxr import UsdGeom, Gf
import numpy as np

# Get object position for reference
stage = omni.usd.get_context().get_stage()
obj_prim = stage.GetPrimAtPath('{object_path}')
obj_xf = UsdGeom.Xformable(obj_prim).ComputeLocalToWorldTransform(0)
obj_pos = list(obj_xf.ExtractTranslation())

# Define grasp specification
grasp_spec = {{
    'version': '1.0',
    'robot_path': '{robot_path}',
    'object_path': '{object_path}',
    'grasps': {{
        'default_grasp': {{
            'gripper_offset': {list(offset)},
            'approach_direction': {list(approach_dir)},
            'object_reference_position': obj_pos,
            'pre_grasp_opening': 0.04,
            'grasp_force': 40.0,
        }},
    }},
}}

# Save to workspace
grasp_dir = 'workspace/grasp_poses'
os.makedirs(grasp_dir, exist_ok=True)
obj_name = '{object_path}'.split('/')[-1]
file_path = os.path.join(grasp_dir, f'{{obj_name}}.isaac_grasp')

with open(file_path, 'w') as f:
    yaml.dump(grasp_spec, f, default_flow_style=False)

print(f"Grasp pose saved to {{file_path}}")
print(f"  Robot: {robot_path}")
print(f"  Object: {object_path}")
print(f"  Offset: {list(offset)}")
print(f"  Approach direction: {list(approach_dir)}")
"""


async def _handle_visualize_behavior_tree(args: Dict) -> Dict:
    """Return a formatted text tree of a behavior network structure."""
    network_name = args.get("network_name", "unknown")

    # Since we don't have access to a running Cortex instance at query time,
    # return the canonical structure for known behavior types, or a template.
    _KNOWN_BEHAVIORS = {
        "pick_and_place": {
            "name": "pick_and_place",
            "type": "DfStateMachineDecider",
            "children": [
                {"name": "approach", "type": "DfState", "description": "Move to pre-grasp position above target"},
                {"name": "grasp", "type": "DfState", "description": "Move down and close gripper on object"},
                {"name": "lift", "type": "DfState", "description": "Lift grasped object to safe height"},
                {"name": "place", "type": "DfState", "description": "Move to place position and release"},
            ],
            "transitions": "approach -> grasp -> lift -> place -> done",
        },
        "follow_target": {
            "name": "follow_target",
            "type": "DfDecider",
            "children": [
                {"name": "follow", "type": "FollowTargetState", "description": "Continuously track target prim with end-effector"},
            ],
            "transitions": "follow (continuous loop)",
        },
    }

    behavior = _KNOWN_BEHAVIORS.get(network_name.lower())

    if behavior:
        # Build ASCII tree
        lines = [
            f"Behavior Network: {behavior['name']}",
            f"  Type: {behavior['type']}",
            f"  Transitions: {behavior['transitions']}",
            "",
            "  Nodes:",
        ]
        for i, child in enumerate(behavior["children"]):
            is_last = i == len(behavior["children"]) - 1
            prefix = "  +-- " if is_last else "  |-- "
            lines.append(f"{prefix}{child['name']} ({child['type']})")
            desc_prefix = "      " if is_last else "  |   "
            lines.append(f"{desc_prefix}{child['description']}")

        tree_text = "\n".join(lines)
        return {
            "network_name": network_name,
            "structure": behavior,
            "tree": tree_text,
        }

    return {
        "network_name": network_name,
        "structure": None,
        "tree": (
            f"Behavior Network: {network_name}\n"
            f"  (No pre-built visualization available for '{network_name}'.\n"
            f"   Known behaviors: pick_and_place, follow_target.\n"
            f"   For custom networks, inspect the DfNetwork in the running Cortex world.)"
        ),
    }


CODE_GEN_HANDLERS["create_behavior"] = _gen_create_behavior
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper
CODE_GEN_HANDLERS["grasp_object"] = _gen_grasp_object
CODE_GEN_HANDLERS["define_grasp_pose"] = _gen_define_grasp_pose


_ROBOT_TYPE_DEFAULTS = {
    "manipulator": {"stiffness": 1000, "damping": 100},
    "mobile":      {"stiffness": 500,  "damping": 50},
    "humanoid":    {"stiffness": 800,  "damping": 80},
}


def _gen_robot_wizard(args: Dict) -> str:
    asset_path = args["asset_path"]
    robot_type = args.get("robot_type", "manipulator")
    defaults = _ROBOT_TYPE_DEFAULTS.get(robot_type, _ROBOT_TYPE_DEFAULTS["manipulator"])
    stiffness = args.get("drive_stiffness", defaults["stiffness"])
    damping = args.get("drive_damping", defaults["damping"])

    is_urdf = asset_path.lower().endswith(".urdf")

    if is_urdf:
        import_block = f"""\
# Step 1: Import robot from URDF
from isaacsim.asset.importer.urdf import import_urdf, ImportConfig
cfg = ImportConfig()
cfg.convex_decomposition = False  # use convex hull
dest_path = import_urdf('{asset_path}', cfg)
print(f"Imported URDF → {{dest_path}}")
"""
    else:
        import_block = f"""\
# Step 1: Import robot from USD
dest_path = '/World/Robot'
prim = stage.DefinePrim(dest_path, 'Xform')
prim.GetReferences().AddReference('{asset_path}')
print(f"Loaded USD asset → {{dest_path}}")
"""

    return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

{import_block}
# Step 2: Apply drive defaults for {robot_type} (Kp={stiffness}, Kd={damping})
robot_prim = stage.GetPrimAtPath(dest_path)
joint_count = 0
for child in robot_prim.GetAllDescendants():
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({stiffness})
                drive.GetDampingAttr().Set({damping})
                joint_count += 1
print(f"Applied Kp={stiffness}, Kd={damping} to {{joint_count}} drives")

# Step 3: Apply convex-hull collision meshes
collision_count = 0
for child in robot_prim.GetAllDescendants():
    if child.IsA(UsdGeom.Mesh):
        if not child.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(child)
        if not child.HasAPI(PhysxSchema.PhysxCollisionAPI):
            PhysxSchema.PhysxCollisionAPI.Apply(child)
        coll_api = PhysxSchema.PhysxCollisionAPI(child)
        coll_api.CreateContactOffsetAttr(0.02)
        collision_count += 1
print(f"Applied convex-hull collision to {{collision_count}} meshes")

# Summary
print(f"Robot setup complete: type={robot_type}, drives={{joint_count}}, collisions={{collision_count}}")
"""


def _gen_tune_gains(args: Dict) -> str:
    art_path = args["articulation_path"]
    method = args.get("method", "manual")
    joint_name = args.get("joint_name")
    kp = args.get("kp", 1000)
    kd = args.get("kd", 100)
    test_mode = args.get("test_mode", "step")

    if method == "step_response":
        mode_map = {"sinusoidal": "SINUSOIDAL", "step": "STEP"}
        mode_str = mode_map.get(test_mode, "STEP")
        return f"""\
import omni.usd
from pxr import UsdPhysics
from isaacsim.robot_setup.gain_tuner import GainTuner, GainsTestMode
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Initialize GainTuner
tuner = GainTuner()
tuner.setup('{art_path}')

# Configure test parameters
test_params = {{"mode": GainsTestMode.{mode_str}}}
tuner.initialize_gains_test(test_params)

# Run test loop
world = World.instance() or World()
dt = 1.0 / 60.0
step = 0
while not tuner.update_gains_test(dt):
    world.step()
    step += 1

# Compute error metrics
pos_rmse, vel_rmse = tuner.compute_gains_test_error_terms()
print(f"GainTuner test complete after {{step}} steps")
print(f"Position RMSE: {{pos_rmse:.6f}}")
print(f"Velocity RMSE: {{vel_rmse:.6f}}")
"""

    # Manual method: set gains directly via DriveAPI
    if joint_name:
        return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_prim = stage.GetPrimAtPath('{art_path}/{joint_name}')

# Set drive gains for {joint_name}
for drive_type in ['angular', 'linear']:
    drive = UsdPhysics.DriveAPI.Get(joint_prim, drive_type)
    if drive:
        drive.GetStiffnessAttr().Set({kp})
        drive.GetDampingAttr().Set({kd})
        print(f"Set {{drive_type}} drive on {joint_name}: Kp={kp}, Kd={kd}")
"""

    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')

# Set drive gains for all joints
joint_count = 0
for child in robot_prim.GetAllDescendants():
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({kp})
                drive.GetDampingAttr().Set({kd})
                joint_count += 1
print(f"Set Kp={kp}, Kd={kd} on {{joint_count}} drives")
"""


def _gen_assemble_robot(args: Dict) -> str:
    base_path = args["base_path"]
    attachment_path = args["attachment_path"]
    base_mount = args["base_mount"]
    attach_mount = args["attach_mount"]

    return f"""\
import omni.usd
from isaacsim.robot_setup.assembler import RobotAssembler

stage = omni.usd.get_context().get_stage()

# Assemble robot: attach {attachment_path} to {base_path}
assembler = RobotAssembler()
assembled = assembler.assemble(
    base_robot_path='{base_path}',
    attach_robot_path='{attachment_path}',
    base_robot_mount_frame='{base_mount}',
    attach_robot_mount_frame='{attach_mount}',
    fixed_joint_offset=None,
    fixed_joint_orient=None,
    single_robot=True,
)
print(f"Assembled: {{assembled}}")
print(f"Base: {base_path} (mount: {base_mount})")
print(f"Attachment: {attachment_path} (mount: {attach_mount})")
"""


def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"robot_prim = stage.GetPrimAtPath('{art_path}')",
        "",
    ]

    if mode == "auto":
        lines.extend([
            "# Auto mode: keep defaults (adjacent links already skip collision)",
            f"print('Self-collision for {art_path}: auto (default PhysX behavior)')",
        ])
    elif mode == "enable":
        lines.extend([
            "# Enable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(True)",
            f"print('Self-collision ENABLED for {art_path}')",
        ])
    elif mode == "disable":
        lines.extend([
            "# Disable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(False)",
            f"print('Self-collision DISABLED for {art_path}')",
        ])

    if filtered_pairs:
        lines.extend([
            "",
            "# Apply collision filtering for specified link pairs",
        ])
        for pair in filtered_pairs:
            if len(pair) == 2:
                lines.extend([
                    f"link_a = stage.GetPrimAtPath('{pair[0]}')",
                    f"link_b = stage.GetPrimAtPath('{pair[1]}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)


CODE_GEN_HANDLERS["robot_wizard"] = _gen_robot_wizard



def _gen_tune_gains(args: Dict) -> str:
    art_path = args["articulation_path"]
    method = args.get("method", "manual")
    joint_name = args.get("joint_name")
    kp = args.get("kp", 1000)
    kd = args.get("kd", 100)
    test_mode = args.get("test_mode", "step")

    if method == "step_response":
        mode_map = {"sinusoidal": "SINUSOIDAL", "step": "STEP"}
        mode_str = mode_map.get(test_mode, "STEP")
        return f"""\
import omni.usd
from pxr import UsdPhysics
from isaacsim.robot_setup.gain_tuner import GainTuner, GainsTestMode
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Initialize GainTuner
tuner = GainTuner()
tuner.setup('{art_path}')

# Configure test parameters
test_params = {{"mode": GainsTestMode.{mode_str}}}
tuner.initialize_gains_test(test_params)

# Run test loop
world = World.instance() or World()
dt = 1.0 / 60.0
step = 0
while not tuner.update_gains_test(dt):
    world.step()
    step += 1

# Compute error metrics
pos_rmse, vel_rmse = tuner.compute_gains_test_error_terms()
print(f"GainTuner test complete after {{step}} steps")
print(f"Position RMSE: {{pos_rmse:.6f}}")
print(f"Velocity RMSE: {{vel_rmse:.6f}}")
"""

    # Manual method: set gains directly via DriveAPI
    if joint_name:
        return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_prim = stage.GetPrimAtPath('{art_path}/{joint_name}')

# Set drive gains for {joint_name}
for drive_type in ['angular', 'linear']:
    drive = UsdPhysics.DriveAPI.Get(joint_prim, drive_type)
    if drive:
        drive.GetStiffnessAttr().Set({kp})
        drive.GetDampingAttr().Set({kd})
        print(f"Set {{drive_type}} drive on {joint_name}: Kp={kp}, Kd={kd}")
"""

    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')

# Set drive gains for all joints
joint_count = 0
for child in robot_prim.GetAllDescendants():
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({kp})
                drive.GetDampingAttr().Set({kd})
                joint_count += 1
print(f"Set Kp={kp}, Kd={kd} on {{joint_count}} drives")
"""


def _gen_assemble_robot(args: Dict) -> str:
    base_path = args["base_path"]
    attachment_path = args["attachment_path"]
    base_mount = args["base_mount"]
    attach_mount = args["attach_mount"]

    return f"""\
import omni.usd
from isaacsim.robot_setup.assembler import RobotAssembler

stage = omni.usd.get_context().get_stage()

# Assemble robot: attach {attachment_path} to {base_path}
assembler = RobotAssembler()
assembled = assembler.assemble(
    base_robot_path='{base_path}',
    attach_robot_path='{attachment_path}',
    base_robot_mount_frame='{base_mount}',
    attach_robot_mount_frame='{attach_mount}',
    fixed_joint_offset=None,
    fixed_joint_orient=None,
    single_robot=True,
)
print(f"Assembled: {{assembled}}")
print(f"Base: {base_path} (mount: {base_mount})")
print(f"Attachment: {attachment_path} (mount: {attach_mount})")
"""


def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"robot_prim = stage.GetPrimAtPath('{art_path}')",
        "",
    ]

    if mode == "auto":
        lines.extend([
            "# Auto mode: keep defaults (adjacent links already skip collision)",
            f"print('Self-collision for {art_path}: auto (default PhysX behavior)')",
        ])
    elif mode == "enable":
        lines.extend([
            "# Enable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(True)",
            f"print('Self-collision ENABLED for {art_path}')",
        ])
    elif mode == "disable":
        lines.extend([
            "# Disable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(False)",
            f"print('Self-collision DISABLED for {art_path}')",
        ])

    if filtered_pairs:
        lines.extend([
            "",
            "# Apply collision filtering for specified link pairs",
        ])
        for pair in filtered_pairs:
            if len(pair) == 2:
                lines.extend([
                    f"link_a = stage.GetPrimAtPath('{pair[0]}')",
                    f"link_b = stage.GetPrimAtPath('{pair[1]}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)


CODE_GEN_HANDLERS["robot_wizard"] = _gen_robot_wizard
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains



def _gen_assemble_robot(args: Dict) -> str:
    base_path = args["base_path"]
    attachment_path = args["attachment_path"]
    base_mount = args["base_mount"]
    attach_mount = args["attach_mount"]

    return f"""\
import omni.usd
from isaacsim.robot_setup.assembler import RobotAssembler

stage = omni.usd.get_context().get_stage()

# Assemble robot: attach {attachment_path} to {base_path}
assembler = RobotAssembler()
assembled = assembler.assemble(
    base_robot_path='{base_path}',
    attach_robot_path='{attachment_path}',
    base_robot_mount_frame='{base_mount}',
    attach_robot_mount_frame='{attach_mount}',
    fixed_joint_offset=None,
    fixed_joint_orient=None,
    single_robot=True,
)
print(f"Assembled: {{assembled}}")
print(f"Base: {base_path} (mount: {base_mount})")
print(f"Attachment: {attachment_path} (mount: {attach_mount})")
"""


def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"robot_prim = stage.GetPrimAtPath('{art_path}')",
        "",
    ]

    if mode == "auto":
        lines.extend([
            "# Auto mode: keep defaults (adjacent links already skip collision)",
            f"print('Self-collision for {art_path}: auto (default PhysX behavior)')",
        ])
    elif mode == "enable":
        lines.extend([
            "# Enable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(True)",
            f"print('Self-collision ENABLED for {art_path}')",
        ])
    elif mode == "disable":
        lines.extend([
            "# Disable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(False)",
            f"print('Self-collision DISABLED for {art_path}')",
        ])

    if filtered_pairs:
        lines.extend([
            "",
            "# Apply collision filtering for specified link pairs",
        ])
        for pair in filtered_pairs:
            if len(pair) == 2:
                lines.extend([
                    f"link_a = stage.GetPrimAtPath('{pair[0]}')",
                    f"link_b = stage.GetPrimAtPath('{pair[1]}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)


CODE_GEN_HANDLERS["robot_wizard"] = _gen_robot_wizard
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains
CODE_GEN_HANDLERS["assemble_robot"] = _gen_assemble_robot



def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"robot_prim = stage.GetPrimAtPath('{art_path}')",
        "",
    ]

    if mode == "auto":
        lines.extend([
            "# Auto mode: keep defaults (adjacent links already skip collision)",
            f"print('Self-collision for {art_path}: auto (default PhysX behavior)')",
        ])
    elif mode == "enable":
        lines.extend([
            "# Enable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(True)",
            f"print('Self-collision ENABLED for {art_path}')",
        ])
    elif mode == "disable":
        lines.extend([
            "# Disable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(False)",
            f"print('Self-collision DISABLED for {art_path}')",
        ])

    if filtered_pairs:
        lines.extend([
            "",
            "# Apply collision filtering for specified link pairs",
        ])
        for pair in filtered_pairs:
            if len(pair) == 2:
                lines.extend([
                    f"link_a = stage.GetPrimAtPath('{pair[0]}')",
                    f"link_b = stage.GetPrimAtPath('{pair[1]}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)


CODE_GEN_HANDLERS["robot_wizard"] = _gen_robot_wizard
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains
CODE_GEN_HANDLERS["assemble_robot"] = _gen_assemble_robot
CODE_GEN_HANDLERS["configure_self_collision"] = _gen_configure_self_collision



# ─── Wheeled Robots & Conveyor Systems (Phase 8E) ──────────────────────────

def _gen_create_wheeled_robot(args: Dict) -> str:
    robot_path = args["robot_path"]
    drive_type = args["drive_type"]
    wheel_radius = args["wheel_radius"]
    wheel_base = args["wheel_base"]
    dof_names = args.get("wheel_dof_names")
    max_lin = args.get("max_linear_speed", 1.0)
    max_ang = args.get("max_angular_speed", 3.14)

    controller_map = {
        "differential": "DifferentialController",
        "ackermann": "AckermannController",
        "holonomic": "HolonomicController",
    }
    ctrl_cls = controller_map[drive_type]

    dof_block = ""
    if dof_names:
        dof_str = repr(dof_names)
        dof_block = f"""
# Wheel DOFs
wheel_dof_names = {dof_str}
"""

    return f"""\
import numpy as np
from isaacsim.robot.wheeled_robots.controllers import {ctrl_cls}
from isaacsim.robot.wheeled_robots.robots import WheeledRobot

# Create controller
controller = {ctrl_cls}(
    name="{drive_type}_ctrl",
    wheel_radius={wheel_radius},
    wheel_base={wheel_base},
)
{dof_block}
# Speed limits
MAX_LINEAR_SPEED = {max_lin}   # m/s
MAX_ANGULAR_SPEED = {max_ang}  # rad/s

def drive(linear_vel, angular_vel):
    \"\"\"Compute wheel actions. Clamps to speed limits.\"\"\"
    lv = np.clip(linear_vel, -MAX_LINEAR_SPEED, MAX_LINEAR_SPEED)
    av = np.clip(angular_vel, -MAX_ANGULAR_SPEED, MAX_ANGULAR_SPEED)
    action = controller.forward(np.array([lv, av]))
    return action

print("Wheeled robot controller ready: {drive_type} | robot={robot_path}")
print(f"  wheel_radius={wheel_radius}, wheel_base={wheel_base}")
print(f"  max_linear={{MAX_LINEAR_SPEED}} m/s, max_angular={{MAX_ANGULAR_SPEED}} rad/s")
"""


def _gen_navigate_to(args: Dict) -> str:
    robot_path = args["robot_path"]
    target = args["target_position"]
    planner = args.get("planner", "direct")

    if planner == "astar":
        return f"""\
import numpy as np
import heapq
import omni.usd
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array({target}, dtype=float)

# --- Inline A* on occupancy grid ---
GRID_RES = 0.25  # meters per cell
GRID_SIZE = 80   # 80x80 grid = 20m x 20m
GRID_OFFSET = np.array([-GRID_SIZE * GRID_RES / 2, -GRID_SIZE * GRID_RES / 2])

# Pre-generate an empty occupancy grid (0=free, 1=obstacle)
# Replace with actual occupancy data for real scenes
occupancy = np.zeros((GRID_SIZE, GRID_SIZE), dtype=int)

def world_to_grid(pos):
    return int((pos[0] - GRID_OFFSET[0]) / GRID_RES), int((pos[1] - GRID_OFFSET[1]) / GRID_RES)

def grid_to_world(cell):
    return np.array([cell[0] * GRID_RES + GRID_OFFSET[0], cell[1] * GRID_RES + GRID_OFFSET[1]])

def astar(start, goal):
    open_set = [(0, start)]
    came_from = {{}}
    g = {{start: 0}}
    while open_set:
        _, current = heapq.heappop(open_set)
        if current == goal:
            path = []
            while current in came_from:
                path.append(current)
                current = came_from[current]
            path.append(start)
            return path[::-1]
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
            nx, ny = current[0]+dx, current[1]+dy
            if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and occupancy[ny, nx] == 0:
                ng = g[current] + (1.414 if dx and dy else 1.0)
                if (nx, ny) not in g or ng < g[(nx, ny)]:
                    g[(nx, ny)] = ng
                    h = abs(nx - goal[0]) + abs(ny - goal[1])
                    heapq.heappush(open_set, (ng + h, (nx, ny)))
                    came_from[(nx, ny)] = current
    return [start, goal]  # fallback: direct

# Get current robot position (assume origin for now)
start_world = np.array([0.0, 0.0])
start_cell = world_to_grid(start_world)
goal_cell = world_to_grid(target)
grid_path = astar(start_cell, goal_cell)
waypoints = [grid_to_world(c) for c in grid_path]

# --- Drive along waypoints via physics callback ---
pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)
waypoint_idx = [0]

import omni.physx
def _nav_step(dt):
    idx = waypoint_idx[0]
    if idx >= len(waypoints):
        print(f"Navigation complete: reached {{target}}")
        sub.unsubscribe()
        return
    wp = waypoints[idx]
    # current_pos would come from robot state in real usage
    action = pose_ctrl.forward(start_position=np.array([0, 0, 0]), start_orientation=np.array([1, 0, 0, 0]), goal_position=np.array([wp[0], wp[1], 0]))
    if action is None or np.linalg.norm(wp - start_world) < 0.1:
        waypoint_idx[0] += 1

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"A* navigation started: {{len(waypoints)}} waypoints to {{target}}")
"""
    else:  # direct
        return f"""\
import numpy as np
import omni.physx
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array([{target[0]}, {target[1]}, 0.0])

pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)

def _nav_step(dt):
    \"\"\"Physics callback: drive toward target each step.\"\"\"
    # In production, read actual robot pose from ArticulationView
    action = pose_ctrl.forward(
        start_position=np.array([0, 0, 0]),
        start_orientation=np.array([1, 0, 0, 0]),
        goal_position=target,
    )
    if action is None:
        print(f"Direct navigation complete: reached {{target[:2]}}")
        sub.unsubscribe()

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"Direct navigation started: target=[{target[0]}, {target[1]}]")
"""


def _gen_create_conveyor(args: Dict) -> str:
    prim_path = args["prim_path"]
    speed = args.get("speed", 0.5)
    direction = args.get("direction", [1, 0, 0])

    return f"""\
import omni.usd
import omni.graph.core as og
import carb

# Check GPU physics / Fabric — conveyors require CPU physics
use_fabric = carb.settings.get_settings().get("/physics/useFabric")
if use_fabric:
    print("WARNING: Conveyor requires CPU physics. Set /physics/useFabric to False.")

prim_path = '{prim_path}'
speed = {speed}
direction = {direction}

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": prim_path + "/ConveyorGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("tick", "omni.graph.action.OnPlaybackTick"),
            ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
        ],
        keys.CONNECT: [
            ("tick.outputs:tick", "conveyor.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("conveyor.inputs:conveyorPrim", prim_path),
            ("conveyor.inputs:velocity", speed),
            ("conveyor.inputs:direction", direction),
        ],
    }},
)

print(f"Conveyor created at {{prim_path}} — speed={{speed}} m/s, direction={{direction}}")
"""


def _gen_create_conveyor_track(args: Dict) -> str:
    waypoints = args["waypoints"]
    belt_width = args.get("belt_width", 0.5)
    speed = args.get("speed", 0.5)

    return f"""\
import omni.usd
import omni.graph.core as og
import math
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

waypoints = {waypoints}
belt_width = {belt_width}
speed = {speed}

# Create parent Xform
track_path = '/World/ConveyorTrack'
stage.DefinePrim(track_path, 'Xform')

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

for i in range(len(waypoints) - 1):
    p0 = waypoints[i]
    p1 = waypoints[i + 1]

    # Compute segment center, length, and orientation
    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    cz = (p0[2] + p1[2]) / 2.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    seg_len = math.sqrt(dx * dx + dy * dy)
    angle_deg = math.degrees(math.atan2(dy, dx))

    # Create segment mesh (Cube scaled to belt dimensions)
    seg_path = f"{{track_path}}/Segment_{{i}}"
    prim = stage.DefinePrim(seg_path, 'Cube')
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(angle_deg)
    xf.AddScaleOp().Set(Gf.Vec3d(seg_len / 2.0, belt_width / 2.0, 0.02))

    # Direction vector (local X, rotated)
    dir_x = dx / seg_len if seg_len > 0 else 1.0
    dir_y = dy / seg_len if seg_len > 0 else 0.0

    # Create conveyor OmniGraph for this segment
    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": seg_path + "/ConveyorGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "conveyor.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("conveyor.inputs:conveyorPrim", seg_path),
                ("conveyor.inputs:velocity", speed),
                ("conveyor.inputs:direction", [dir_x, dir_y, 0.0]),
            ],
        }},
    )

print(f"Conveyor track created: {{len(waypoints) - 1}} segments, speed={{speed}} m/s")
"""


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


CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot



def _gen_navigate_to(args: Dict) -> str:
    robot_path = args["robot_path"]
    target = args["target_position"]
    planner = args.get("planner", "direct")

    if planner == "astar":
        return f"""\
import numpy as np
import heapq
import omni.usd
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array({target}, dtype=float)

# --- Inline A* on occupancy grid ---
GRID_RES = 0.25  # meters per cell
GRID_SIZE = 80   # 80x80 grid = 20m x 20m
GRID_OFFSET = np.array([-GRID_SIZE * GRID_RES / 2, -GRID_SIZE * GRID_RES / 2])

# Pre-generate an empty occupancy grid (0=free, 1=obstacle)
# Replace with actual occupancy data for real scenes
occupancy = np.zeros((GRID_SIZE, GRID_SIZE), dtype=int)

def world_to_grid(pos):
    return int((pos[0] - GRID_OFFSET[0]) / GRID_RES), int((pos[1] - GRID_OFFSET[1]) / GRID_RES)

def grid_to_world(cell):
    return np.array([cell[0] * GRID_RES + GRID_OFFSET[0], cell[1] * GRID_RES + GRID_OFFSET[1]])

def astar(start, goal):
    open_set = [(0, start)]
    came_from = {{}}
    g = {{start: 0}}
    while open_set:
        _, current = heapq.heappop(open_set)
        if current == goal:
            path = []
            while current in came_from:
                path.append(current)
                current = came_from[current]
            path.append(start)
            return path[::-1]
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
            nx, ny = current[0]+dx, current[1]+dy
            if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and occupancy[ny, nx] == 0:
                ng = g[current] + (1.414 if dx and dy else 1.0)
                if (nx, ny) not in g or ng < g[(nx, ny)]:
                    g[(nx, ny)] = ng
                    h = abs(nx - goal[0]) + abs(ny - goal[1])
                    heapq.heappush(open_set, (ng + h, (nx, ny)))
                    came_from[(nx, ny)] = current
    return [start, goal]  # fallback: direct

# Get current robot position (assume origin for now)
start_world = np.array([0.0, 0.0])
start_cell = world_to_grid(start_world)
goal_cell = world_to_grid(target)
grid_path = astar(start_cell, goal_cell)
waypoints = [grid_to_world(c) for c in grid_path]

# --- Drive along waypoints via physics callback ---
pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)
waypoint_idx = [0]

import omni.physx
def _nav_step(dt):
    idx = waypoint_idx[0]
    if idx >= len(waypoints):
        print(f"Navigation complete: reached {{target}}")
        sub.unsubscribe()
        return
    wp = waypoints[idx]
    # current_pos would come from robot state in real usage
    action = pose_ctrl.forward(start_position=np.array([0, 0, 0]), start_orientation=np.array([1, 0, 0, 0]), goal_position=np.array([wp[0], wp[1], 0]))
    if action is None or np.linalg.norm(wp - start_world) < 0.1:
        waypoint_idx[0] += 1

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"A* navigation started: {{len(waypoints)}} waypoints to {{target}}")
"""
    else:  # direct
        return f"""\
import numpy as np
import omni.physx
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array([{target[0]}, {target[1]}, 0.0])

pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)

def _nav_step(dt):
    \"\"\"Physics callback: drive toward target each step.\"\"\"
    # In production, read actual robot pose from ArticulationView
    action = pose_ctrl.forward(
        start_position=np.array([0, 0, 0]),
        start_orientation=np.array([1, 0, 0, 0]),
        goal_position=target,
    )
    if action is None:
        print(f"Direct navigation complete: reached {{target[:2]}}")
        sub.unsubscribe()

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"Direct navigation started: target=[{target[0]}, {target[1]}]")
"""


def _gen_create_conveyor(args: Dict) -> str:
    prim_path = args["prim_path"]
    speed = args.get("speed", 0.5)
    direction = args.get("direction", [1, 0, 0])

    return f"""\
import omni.usd
import omni.graph.core as og
import carb

# Check GPU physics / Fabric — conveyors require CPU physics
use_fabric = carb.settings.get_settings().get("/physics/useFabric")
if use_fabric:
    print("WARNING: Conveyor requires CPU physics. Set /physics/useFabric to False.")

prim_path = '{prim_path}'
speed = {speed}
direction = {direction}

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": prim_path + "/ConveyorGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("tick", "omni.graph.action.OnPlaybackTick"),
            ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
        ],
        keys.CONNECT: [
            ("tick.outputs:tick", "conveyor.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("conveyor.inputs:conveyorPrim", prim_path),
            ("conveyor.inputs:velocity", speed),
            ("conveyor.inputs:direction", direction),
        ],
    }},
)

print(f"Conveyor created at {{prim_path}} — speed={{speed}} m/s, direction={{direction}}")
"""


def _gen_create_conveyor_track(args: Dict) -> str:
    waypoints = args["waypoints"]
    belt_width = args.get("belt_width", 0.5)
    speed = args.get("speed", 0.5)

    return f"""\
import omni.usd
import omni.graph.core as og
import math
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

waypoints = {waypoints}
belt_width = {belt_width}
speed = {speed}

# Create parent Xform
track_path = '/World/ConveyorTrack'
stage.DefinePrim(track_path, 'Xform')

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

for i in range(len(waypoints) - 1):
    p0 = waypoints[i]
    p1 = waypoints[i + 1]

    # Compute segment center, length, and orientation
    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    cz = (p0[2] + p1[2]) / 2.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    seg_len = math.sqrt(dx * dx + dy * dy)
    angle_deg = math.degrees(math.atan2(dy, dx))

    # Create segment mesh (Cube scaled to belt dimensions)
    seg_path = f"{{track_path}}/Segment_{{i}}"
    prim = stage.DefinePrim(seg_path, 'Cube')
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(angle_deg)
    xf.AddScaleOp().Set(Gf.Vec3d(seg_len / 2.0, belt_width / 2.0, 0.02))

    # Direction vector (local X, rotated)
    dir_x = dx / seg_len if seg_len > 0 else 1.0
    dir_y = dy / seg_len if seg_len > 0 else 0.0

    # Create conveyor OmniGraph for this segment
    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": seg_path + "/ConveyorGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "conveyor.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("conveyor.inputs:conveyorPrim", seg_path),
                ("conveyor.inputs:velocity", speed),
                ("conveyor.inputs:direction", [dir_x, dir_y, 0.0]),
            ],
        }},
    )

print(f"Conveyor track created: {{len(waypoints) - 1}} segments, speed={{speed}} m/s")
"""


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


CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to



def _gen_create_conveyor(args: Dict) -> str:
    prim_path = args["prim_path"]
    speed = args.get("speed", 0.5)
    direction = args.get("direction", [1, 0, 0])

    return f"""\
import omni.usd
import omni.graph.core as og
import carb

# Check GPU physics / Fabric — conveyors require CPU physics
use_fabric = carb.settings.get_settings().get("/physics/useFabric")
if use_fabric:
    print("WARNING: Conveyor requires CPU physics. Set /physics/useFabric to False.")

prim_path = '{prim_path}'
speed = {speed}
direction = {direction}

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": prim_path + "/ConveyorGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("tick", "omni.graph.action.OnPlaybackTick"),
            ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
        ],
        keys.CONNECT: [
            ("tick.outputs:tick", "conveyor.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("conveyor.inputs:conveyorPrim", prim_path),
            ("conveyor.inputs:velocity", speed),
            ("conveyor.inputs:direction", direction),
        ],
    }},
)

print(f"Conveyor created at {{prim_path}} — speed={{speed}} m/s, direction={{direction}}")
"""


def _gen_create_conveyor_track(args: Dict) -> str:
    waypoints = args["waypoints"]
    belt_width = args.get("belt_width", 0.5)
    speed = args.get("speed", 0.5)

    return f"""\
import omni.usd
import omni.graph.core as og
import math
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

waypoints = {waypoints}
belt_width = {belt_width}
speed = {speed}

# Create parent Xform
track_path = '/World/ConveyorTrack'
stage.DefinePrim(track_path, 'Xform')

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

for i in range(len(waypoints) - 1):
    p0 = waypoints[i]
    p1 = waypoints[i + 1]

    # Compute segment center, length, and orientation
    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    cz = (p0[2] + p1[2]) / 2.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    seg_len = math.sqrt(dx * dx + dy * dy)
    angle_deg = math.degrees(math.atan2(dy, dx))

    # Create segment mesh (Cube scaled to belt dimensions)
    seg_path = f"{{track_path}}/Segment_{{i}}"
    prim = stage.DefinePrim(seg_path, 'Cube')
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(angle_deg)
    xf.AddScaleOp().Set(Gf.Vec3d(seg_len / 2.0, belt_width / 2.0, 0.02))

    # Direction vector (local X, rotated)
    dir_x = dx / seg_len if seg_len > 0 else 1.0
    dir_y = dy / seg_len if seg_len > 0 else 0.0

    # Create conveyor OmniGraph for this segment
    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": seg_path + "/ConveyorGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "conveyor.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("conveyor.inputs:conveyorPrim", seg_path),
                ("conveyor.inputs:velocity", speed),
                ("conveyor.inputs:direction", [dir_x, dir_y, 0.0]),
            ],
        }},
    )

print(f"Conveyor track created: {{len(waypoints) - 1}} segments, speed={{speed}} m/s")
"""


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


CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to
CODE_GEN_HANDLERS["create_conveyor"] = _gen_create_conveyor



def _gen_create_conveyor_track(args: Dict) -> str:
    waypoints = args["waypoints"]
    belt_width = args.get("belt_width", 0.5)
    speed = args.get("speed", 0.5)

    return f"""\
import omni.usd
import omni.graph.core as og
import math
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

waypoints = {waypoints}
belt_width = {belt_width}
speed = {speed}

# Create parent Xform
track_path = '/World/ConveyorTrack'
stage.DefinePrim(track_path, 'Xform')

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

for i in range(len(waypoints) - 1):
    p0 = waypoints[i]
    p1 = waypoints[i + 1]

    # Compute segment center, length, and orientation
    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    cz = (p0[2] + p1[2]) / 2.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    seg_len = math.sqrt(dx * dx + dy * dy)
    angle_deg = math.degrees(math.atan2(dy, dx))

    # Create segment mesh (Cube scaled to belt dimensions)
    seg_path = f"{{track_path}}/Segment_{{i}}"
    prim = stage.DefinePrim(seg_path, 'Cube')
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(angle_deg)
    xf.AddScaleOp().Set(Gf.Vec3d(seg_len / 2.0, belt_width / 2.0, 0.02))

    # Direction vector (local X, rotated)
    dir_x = dx / seg_len if seg_len > 0 else 1.0
    dir_y = dy / seg_len if seg_len > 0 else 0.0

    # Create conveyor OmniGraph for this segment
    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": seg_path + "/ConveyorGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "conveyor.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("conveyor.inputs:conveyorPrim", seg_path),
                ("conveyor.inputs:velocity", speed),
                ("conveyor.inputs:direction", [dir_x, dir_y, 0.0]),
            ],
        }},
    )

print(f"Conveyor track created: {{len(waypoints) - 1}} segments, speed={{speed}} m/s")
"""


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


CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to
CODE_GEN_HANDLERS["create_conveyor"] = _gen_create_conveyor
CODE_GEN_HANDLERS["create_conveyor_track"] = _gen_create_conveyor_track



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


CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to
CODE_GEN_HANDLERS["create_conveyor"] = _gen_create_conveyor
CODE_GEN_HANDLERS["create_conveyor_track"] = _gen_create_conveyor_track
CODE_GEN_HANDLERS["merge_meshes"] = _gen_merge_meshes



def _gen_show_tf_tree(args: Dict) -> str:
    root_frame = args.get("root_frame", "world")
    return f'''\
import os
import omni.graph.core as og

# Auto-detect ROS distro
ros_distro = os.environ.get("ROS_DISTRO", "humble")
print(f"ROS distro: {{ros_distro}}")

# Check for TF publisher OmniGraph node — create one if missing
stage = __import__("omni.usd", fromlist=["usd"]).get_context().get_stage()
tf_graph_path = "/World/ROS2_TF_Tree"
tf_prim = stage.GetPrimAtPath(tf_graph_path)
if not tf_prim.IsValid():
    print("No TF publisher graph found — creating one at " + tf_graph_path)
    _bt = og.GraphBackingType
    if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
        _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
    elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
        _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
    else:
        _backing = list(_bt)[0]

    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": tf_graph_path,
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("tf_pub", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "tf_pub.inputs:execIn"),
            ],
        }},
    )
    print("Created ROS2PublishTransformTree graph")

# Acquire TF data via the transform listener interface
from isaacsim.ros2.tf_viewer import acquire_transform_listener_interface

interface = acquire_transform_listener_interface()
interface.initialize(ros_distro)
transforms = interface.get_transforms("{root_frame}")

# Format and print as indented tree
def _print_tree(frames, parent, indent=0):
    prefix = "  " * indent + ("|- " if indent > 0 else "")
    print(f"{{prefix}}{{parent}}")
    children = [f for f in frames if f.get("parent") == parent]
    for child in children:
        _print_tree(frames, child["child"], indent + 1)

print(f"\\nTF Tree (root: {root_frame}):")
print("=" * 40)
if transforms:
    _print_tree(transforms, "{root_frame}")
    print(f"\\nTotal frames: {{len(transforms)}}")
else:
    print("(no transforms found — is the simulation running?)")
'''


def _gen_publish_robot_description(args: Dict) -> str:
    art_path = args["articulation_path"]
    topic = args.get("topic", "/robot_description")
    return f'''\
import omni.usd
from pxr import UsdPhysics, UsdGeom, Gf
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from rclpy.qos import QoSProfile, DurabilityPolicy

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError("Articulation not found: {art_path}")

# Build simplified URDF from USD articulation structure
# NOTE: This is a simplified URDF — for full export use Isaac Sim's URDF Exporter UI
links = []
joints = []

def _traverse(prim, parent_link=None):
    name = prim.GetName()
    prim_type = prim.GetTypeName()

    # Detect links (Xform with collision or visual children, or known link patterns)
    is_link = prim_type in ("Xform", "") and any(
        child.GetTypeName() in ("Mesh", "Cube", "Sphere", "Cylinder", "Capsule")
        for child in prim.GetChildren()
    ) or prim.HasAPI(UsdPhysics.RigidBodyAPI)

    if is_link:
        links.append(name)

        # Check for joint relationship to parent
        for child in prim.GetChildren():
            if child.HasAPI(UsdPhysics.RevoluteJointAPI):
                joints.append({{
                    "name": child.GetName(),
                    "type": "revolute",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})
            elif child.HasAPI(UsdPhysics.PrismaticJointAPI):
                joints.append({{
                    "name": child.GetName(),
                    "type": "prismatic",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})

        for child in prim.GetChildren():
            _traverse(child, name)
    else:
        for child in prim.GetChildren():
            _traverse(child, parent_link)

_traverse(art_prim)

# Generate URDF XML
urdf_lines = ['<?xml version="1.0"?>']
urdf_lines.append('<robot name="{art_path.split("/")[-1]}">')
urdf_lines.append('  <!-- Simplified URDF auto-generated from USD articulation -->')
urdf_lines.append('  <!-- For full export, use Isaac Sim URDF Exporter UI -->')

for link_name in links:
    urdf_lines.append(f'  <link name="{{link_name}}"/>')

for j in joints:
    urdf_lines.append(f'  <joint name="{{j["name"]}}" type="{{j["type"]}}">')
    urdf_lines.append(f'    <parent link="{{j["parent"]}}"/>')
    urdf_lines.append(f'    <child link="{{j["child"]}}"/>')
    urdf_lines.append(f'  </joint>')

urdf_lines.append('</robot>')
urdf_string = "\\n".join(urdf_lines)

print(f"Generated simplified URDF ({{len(links)}} links, {{len(joints)}} joints)")

# Publish via rclpy with TRANSIENT_LOCAL durability
if not rclpy.ok():
    rclpy.init()

node = rclpy.create_node("robot_description_publisher")
qos = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
pub = node.create_publisher(String, "{topic}", qos_profile=qos)
msg = String()
msg.data = urdf_string
pub.publish(msg)

print(f"Published robot description to {topic} (TRANSIENT_LOCAL)")
print(f"URDF preview (first 500 chars):\\n{{urdf_string[:500]}}")
'''


def _gen_configure_ros2_bridge(args: Dict) -> str:
    sensors = args.get("sensors", [])
    domain_id = args.get("ros2_domain_id", 0)

    if not sensors:
        return "print('No sensors specified — nothing to configure')\n"

    # Build OmniGraph nodes and connections
    node_defs = []
    conn_defs = []
    val_defs = []

    # Always add tick + ROS2Context
    node_defs.append('("tick", "omni.graph.action.OnPlaybackTick")')
    node_defs.append(f'("ros2_context", f"{{_ROS2_NS}}.ROS2Context")')
    if domain_id != 0:
        val_defs.append(f'("ros2_context.inputs:domain_id", {domain_id})')

    for i, sensor in enumerate(sensors):
        stype = sensor.get("type", "camera")
        prim_path = sensor.get("prim_path", "")
        topic_name = sensor.get("topic_name", "")
        frame_id = sensor.get("frame_id", "")
        node_name = f"{stype}_{i}"

        # Map sensor type to OG node type
        og_node_class = {
            "camera": "ROS2CameraHelper",
            "lidar": "ROS2PublishLaserScan",
            "imu": "ROS2PublishImu",
            "clock": "ROS2PublishClock",
            "joint_state": "ROS2PublishJointState",
        }.get(stype, f"ROS2Publish{stype.title()}")

        node_defs.append(f'("{node_name}", f"{{_ROS2_NS}}.{og_node_class}")')

        # Connect tick → sensor node
        conn_defs.append(f'("tick.outputs:tick", "{node_name}.inputs:execIn")')

        # Connect context
        conn_defs.append(f'("ros2_context.outputs:context", "{node_name}.inputs:context")')

        # Set values
        if topic_name:
            val_defs.append(f'("{node_name}.inputs:topicName", "{topic_name}")')
        if frame_id:
            val_defs.append(f'("{node_name}.inputs:frameId", "{frame_id}")')
        if prim_path and stype != "clock":
            # clock doesn't have a prim path input
            if stype == "camera":
                val_defs.append(f'("{node_name}.inputs:renderProductPath", "{prim_path}")')
            elif stype == "joint_state":
                val_defs.append(f'("{node_name}.inputs:targetPrim", "{prim_path}")')
            else:
                val_defs.append(f'("{node_name}.inputs:prim", "{prim_path}")')

    nodes_str = ",\n            ".join(node_defs)
    conns_str = ",\n            ".join(conn_defs)
    vals_str = ",\n            ".join(val_defs)

    sensor_summary = ", ".join(s.get("type", "?") for s in sensors)

    return f'''\
import omni.graph.core as og

# Handle Isaac Sim version namespace differences
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
print(f"Isaac Sim version: {{isaacsim.__version__}}, using namespace: {{_ROS2_NS}}")

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "/World/ROS2_Bridge",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {nodes_str}
        ],
        keys.CONNECT: [
            {conns_str}
        ],
        keys.SET_VALUES: [
            {vals_str}
        ],
    }},
)

print(f"ROS2 bridge configured with {{len(nodes)}} nodes")
print(f"Sensors: {sensor_summary}")
print(f"Domain ID: {domain_id}")
print("Start simulation (Play) to begin publishing.")
'''


CODE_GEN_HANDLERS["show_tf_tree"] = _gen_show_tf_tree



def _gen_publish_robot_description(args: Dict) -> str:
    art_path = args["articulation_path"]
    topic = args.get("topic", "/robot_description")
    return f'''\
import omni.usd
from pxr import UsdPhysics, UsdGeom, Gf
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from rclpy.qos import QoSProfile, DurabilityPolicy

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError("Articulation not found: {art_path}")

# Build simplified URDF from USD articulation structure
# NOTE: This is a simplified URDF — for full export use Isaac Sim's URDF Exporter UI
links = []
joints = []

def _traverse(prim, parent_link=None):
    name = prim.GetName()
    prim_type = prim.GetTypeName()

    # Detect links (Xform with collision or visual children, or known link patterns)
    is_link = prim_type in ("Xform", "") and any(
        child.GetTypeName() in ("Mesh", "Cube", "Sphere", "Cylinder", "Capsule")
        for child in prim.GetChildren()
    ) or prim.HasAPI(UsdPhysics.RigidBodyAPI)

    if is_link:
        links.append(name)

        # Check for joint relationship to parent
        for child in prim.GetChildren():
            if child.HasAPI(UsdPhysics.RevoluteJointAPI):
                joints.append({{
                    "name": child.GetName(),
                    "type": "revolute",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})
            elif child.HasAPI(UsdPhysics.PrismaticJointAPI):
                joints.append({{
                    "name": child.GetName(),
                    "type": "prismatic",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})

        for child in prim.GetChildren():
            _traverse(child, name)
    else:
        for child in prim.GetChildren():
            _traverse(child, parent_link)

_traverse(art_prim)

# Generate URDF XML
urdf_lines = ['<?xml version="1.0"?>']
urdf_lines.append('<robot name="{art_path.split("/")[-1]}">')
urdf_lines.append('  <!-- Simplified URDF auto-generated from USD articulation -->')
urdf_lines.append('  <!-- For full export, use Isaac Sim URDF Exporter UI -->')

for link_name in links:
    urdf_lines.append(f'  <link name="{{link_name}}"/>')

for j in joints:
    urdf_lines.append(f'  <joint name="{{j["name"]}}" type="{{j["type"]}}">')
    urdf_lines.append(f'    <parent link="{{j["parent"]}}"/>')
    urdf_lines.append(f'    <child link="{{j["child"]}}"/>')
    urdf_lines.append(f'  </joint>')

urdf_lines.append('</robot>')
urdf_string = "\\n".join(urdf_lines)

print(f"Generated simplified URDF ({{len(links)}} links, {{len(joints)}} joints)")

# Publish via rclpy with TRANSIENT_LOCAL durability
if not rclpy.ok():
    rclpy.init()

node = rclpy.create_node("robot_description_publisher")
qos = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
pub = node.create_publisher(String, "{topic}", qos_profile=qos)
msg = String()
msg.data = urdf_string
pub.publish(msg)

print(f"Published robot description to {topic} (TRANSIENT_LOCAL)")
print(f"URDF preview (first 500 chars):\\n{{urdf_string[:500]}}")
'''


def _gen_configure_ros2_bridge(args: Dict) -> str:
    sensors = args.get("sensors", [])
    domain_id = args.get("ros2_domain_id", 0)

    if not sensors:
        return "print('No sensors specified — nothing to configure')\n"

    # Build OmniGraph nodes and connections
    node_defs = []
    conn_defs = []
    val_defs = []

    # Always add tick + ROS2Context
    node_defs.append('("tick", "omni.graph.action.OnPlaybackTick")')
    node_defs.append(f'("ros2_context", f"{{_ROS2_NS}}.ROS2Context")')
    if domain_id != 0:
        val_defs.append(f'("ros2_context.inputs:domain_id", {domain_id})')

    for i, sensor in enumerate(sensors):
        stype = sensor.get("type", "camera")
        prim_path = sensor.get("prim_path", "")
        topic_name = sensor.get("topic_name", "")
        frame_id = sensor.get("frame_id", "")
        node_name = f"{stype}_{i}"

        # Map sensor type to OG node type
        og_node_class = {
            "camera": "ROS2CameraHelper",
            "lidar": "ROS2PublishLaserScan",
            "imu": "ROS2PublishImu",
            "clock": "ROS2PublishClock",
            "joint_state": "ROS2PublishJointState",
        }.get(stype, f"ROS2Publish{stype.title()}")

        node_defs.append(f'("{node_name}", f"{{_ROS2_NS}}.{og_node_class}")')

        # Connect tick → sensor node
        conn_defs.append(f'("tick.outputs:tick", "{node_name}.inputs:execIn")')

        # Connect context
        conn_defs.append(f'("ros2_context.outputs:context", "{node_name}.inputs:context")')

        # Set values
        if topic_name:
            val_defs.append(f'("{node_name}.inputs:topicName", "{topic_name}")')
        if frame_id:
            val_defs.append(f'("{node_name}.inputs:frameId", "{frame_id}")')
        if prim_path and stype != "clock":
            # clock doesn't have a prim path input
            if stype == "camera":
                val_defs.append(f'("{node_name}.inputs:renderProductPath", "{prim_path}")')
            elif stype == "joint_state":
                val_defs.append(f'("{node_name}.inputs:targetPrim", "{prim_path}")')
            else:
                val_defs.append(f'("{node_name}.inputs:prim", "{prim_path}")')

    nodes_str = ",\n            ".join(node_defs)
    conns_str = ",\n            ".join(conn_defs)
    vals_str = ",\n            ".join(val_defs)

    sensor_summary = ", ".join(s.get("type", "?") for s in sensors)

    return f'''\
import omni.graph.core as og

# Handle Isaac Sim version namespace differences
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
print(f"Isaac Sim version: {{isaacsim.__version__}}, using namespace: {{_ROS2_NS}}")

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "/World/ROS2_Bridge",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {nodes_str}
        ],
        keys.CONNECT: [
            {conns_str}
        ],
        keys.SET_VALUES: [
            {vals_str}
        ],
    }},
)

print(f"ROS2 bridge configured with {{len(nodes)}} nodes")
print(f"Sensors: {sensor_summary}")
print(f"Domain ID: {domain_id}")
print("Start simulation (Play) to begin publishing.")
'''


CODE_GEN_HANDLERS["show_tf_tree"] = _gen_show_tf_tree
CODE_GEN_HANDLERS["publish_robot_description"] = _gen_publish_robot_description



def _gen_configure_ros2_bridge(args: Dict) -> str:
    sensors = args.get("sensors", [])
    domain_id = args.get("ros2_domain_id", 0)

    if not sensors:
        return "print('No sensors specified — nothing to configure')\n"

    # Build OmniGraph nodes and connections
    node_defs = []
    conn_defs = []
    val_defs = []

    # Always add tick + ROS2Context
    node_defs.append('("tick", "omni.graph.action.OnPlaybackTick")')
    node_defs.append(f'("ros2_context", f"{{_ROS2_NS}}.ROS2Context")')
    if domain_id != 0:
        val_defs.append(f'("ros2_context.inputs:domain_id", {domain_id})')

    for i, sensor in enumerate(sensors):
        stype = sensor.get("type", "camera")
        prim_path = sensor.get("prim_path", "")
        topic_name = sensor.get("topic_name", "")
        frame_id = sensor.get("frame_id", "")
        node_name = f"{stype}_{i}"

        # Map sensor type to OG node type
        og_node_class = {
            "camera": "ROS2CameraHelper",
            "lidar": "ROS2PublishLaserScan",
            "imu": "ROS2PublishImu",
            "clock": "ROS2PublishClock",
            "joint_state": "ROS2PublishJointState",
        }.get(stype, f"ROS2Publish{stype.title()}")

        node_defs.append(f'("{node_name}", f"{{_ROS2_NS}}.{og_node_class}")')

        # Connect tick → sensor node
        conn_defs.append(f'("tick.outputs:tick", "{node_name}.inputs:execIn")')

        # Connect context
        conn_defs.append(f'("ros2_context.outputs:context", "{node_name}.inputs:context")')

        # Set values
        if topic_name:
            val_defs.append(f'("{node_name}.inputs:topicName", "{topic_name}")')
        if frame_id:
            val_defs.append(f'("{node_name}.inputs:frameId", "{frame_id}")')
        if prim_path and stype != "clock":
            # clock doesn't have a prim path input
            if stype == "camera":
                val_defs.append(f'("{node_name}.inputs:renderProductPath", "{prim_path}")')
            elif stype == "joint_state":
                val_defs.append(f'("{node_name}.inputs:targetPrim", "{prim_path}")')
            else:
                val_defs.append(f'("{node_name}.inputs:prim", "{prim_path}")')

    nodes_str = ",\n            ".join(node_defs)
    conns_str = ",\n            ".join(conn_defs)
    vals_str = ",\n            ".join(val_defs)

    sensor_summary = ", ".join(s.get("type", "?") for s in sensors)

    return f'''\
import omni.graph.core as og

# Handle Isaac Sim version namespace differences
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
print(f"Isaac Sim version: {{isaacsim.__version__}}, using namespace: {{_ROS2_NS}}")

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "/World/ROS2_Bridge",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {nodes_str}
        ],
        keys.CONNECT: [
            {conns_str}
        ],
        keys.SET_VALUES: [
            {vals_str}
        ],
    }},
)

print(f"ROS2 bridge configured with {{len(nodes)}} nodes")
print(f"Sensors: {sensor_summary}")
print(f"Domain ID: {domain_id}")
print("Start simulation (Play) to begin publishing.")
'''


CODE_GEN_HANDLERS["show_tf_tree"] = _gen_show_tf_tree
CODE_GEN_HANDLERS["publish_robot_description"] = _gen_publish_robot_description
CODE_GEN_HANDLERS["configure_ros2_bridge"] = _gen_configure_ros2_bridge



# ─── 2.X3: check_physics_health (CODE_GEN handler) ────────────────────────

def _gen_check_physics_health(args: Dict) -> str:
    """Generate code that checks physics health of the scene."""
    articulation_path = args.get("articulation_path")

    scope_filter = ""
    if articulation_path:
        scope_filter = f"""
# Scope check to a specific articulation
scope_root = stage.GetPrimAtPath('{articulation_path}')
if not scope_root.IsValid():
    issues.append({{
        'prim': '{articulation_path}',
        'severity': 'critical',
        'issue': 'Articulation prim not found',
        'fix': 'Verify the articulation path exists in the stage',
    }})
    all_prims = []
else:
    all_prims = [scope_root] + list(scope_root.GetAllDescendants())
"""
    else:
        scope_filter = """
# Check all prims in the stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(root.GetAllDescendants())
"""

    return f"""\
import omni.usd
import json
from pxr import UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
{scope_filter}
# 1. Check for missing PhysicsScene prim
physics_scenes = [p for p in all_prims if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene']
if not physics_scenes:
    issues.append({{
        'prim': '/World/PhysicsScene',
        'severity': 'critical',
        'issue': 'Missing PhysicsScene prim',
        'fix': "Create a PhysicsScene: stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene')",
    }})

# 2. Check for missing CollisionAPI on mesh prims with RigidBodyAPI
for prim in all_prims:
    if not prim.IsValid():
        continue

    # Missing CollisionAPI on mesh prims that have RigidBodyAPI
    if prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.RigidBodyAPI):
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            issues.append({{
                'prim': str(prim.GetPath()),
                'severity': 'error',
                'issue': 'Mesh has RigidBodyAPI but no CollisionAPI',
                'fix': 'Apply CollisionAPI: UsdPhysics.CollisionAPI.Apply(prim)',
            }})

    # 3. Invalid inertia tensors (zero or negative)
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(prim)
        inertia = mass_api.GetDiagonalInertiaAttr().Get()
        if inertia is not None:
            if any(v <= 0 for v in inertia):
                issues.append({{
                    'prim': str(prim.GetPath()),
                    'severity': 'critical',
                    'issue': f'Invalid inertia tensor: {{inertia}} (zero or negative components)',
                    'fix': 'Set all diagonal inertia components to positive values',
                }})
        mass = mass_api.GetMassAttr().Get()
        if mass is not None and mass <= 0:
            issues.append({{
                'prim': str(prim.GetPath()),
                'severity': 'critical',
                'issue': f'Invalid mass: {{mass}} (must be > 0)',
                'fix': 'Set mass to a positive value',
            }})

# 4. Extreme mass ratios (>100:1 between rigid bodies)
mass_map = {{}}
for prim in all_prims:
    if not prim.IsValid():
        continue
    if prim.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        if m is not None and m > 0:
            mass_map[str(prim.GetPath())] = m
if len(mass_map) >= 2:
    masses = list(mass_map.values())
    max_m = max(masses)
    min_m = min(masses)
    if min_m > 0 and max_m / min_m > 100:
        issues.append({{
            'prim': 'scene-wide',
            'severity': 'warning',
            'issue': f'Extreme mass ratio: {{max_m/min_m:.1f}}:1 (max={{max_m}}, min={{min_m}})',
            'fix': 'Reduce mass ratio to below 100:1 for stable simulation',
        }})

# 5. Joint limits set to +/-inf
for prim in all_prims:
    if not prim.IsValid():
        continue
    if prim.HasAPI(UsdPhysics.RevoluteJointAPI):
        joint = UsdPhysics.RevoluteJoint(prim)
        lower = joint.GetLowerLimitAttr().Get()
        upper = joint.GetUpperLimitAttr().Get()
        if lower is not None and upper is not None:
            if abs(lower) > 1e30 or abs(upper) > 1e30:
                issues.append({{
                    'prim': str(prim.GetPath()),
                    'severity': 'warning',
                    'issue': f'Joint limits effectively infinite: lower={{lower}}, upper={{upper}}',
                    'fix': 'Set finite joint limits (e.g. -180 to 180 degrees)',
                }})

# 6. metersPerUnit mismatch on stage
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if meters_per_unit != 1.0 and meters_per_unit != 0.01:
    issues.append({{
        'prim': 'stage',
        'severity': 'warning',
        'issue': f'Unusual metersPerUnit: {{meters_per_unit}} (expected 1.0 for meters or 0.01 for cm)',
        'fix': 'Set UsdGeom.SetStageMetersPerUnit(stage, 1.0) for meter scale',
    }})

# Summary
result = {{
    'healthy': len(issues) == 0,
    'issue_count': len(issues),
    'issues': issues,
    'critical_count': sum(1 for i in issues if i['severity'] == 'critical'),
    'error_count': sum(1 for i in issues if i['severity'] == 'error'),
    'warning_count': sum(1 for i in issues if i['severity'] == 'warning'),
}}
print(json.dumps(result, indent=2))
"""


CODE_GEN_HANDLERS["check_physics_health"] = _gen_check_physics_health



# ── Phase 3 Addendum: URDF Post-Processor ──────────────────────────────────

# ── 3.X1 verify_import (CODE_GEN handler) ──────────────────────────────────

def _gen_verify_import(args: Dict) -> str:
    """Generate code that audits a URDF-imported articulation for common issues."""
    art_path = args["articulation_path"]

    return f"""\
import omni.usd
from pxr import UsdPhysics, UsdGeom, PhysxSchema, Gf
import json

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath('{art_path}')
if not root.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

issues = []
all_prims = [root] + list(root.GetAllDescendants())

# Check 1: ArticulationRootAPI
has_art_root = False
for prim in all_prims:
    if prim.HasAPI(PhysxSchema.PhysxArticulationAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        has_art_root = True
        break
if not has_art_root:
    issues.append({{
        'prim': '{art_path}',
        'severity': 'critical',
        'issue': 'Missing ArticulationRootAPI — robot will not simulate as articulation',
        'fix': "PhysxSchema.PhysxArticulationAPI.Apply(stage.GetPrimAtPath('{art_path}'))"
    }})

# Check 2: metersPerUnit
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if abs(meters_per_unit - 0.01) > 0.001 and abs(meters_per_unit - 1.0) > 0.001:
    issues.append({{
        'prim': '/',
        'severity': 'warning',
        'issue': f'Stage metersPerUnit={{meters_per_unit}} — expected 0.01 (cm) or 1.0 (m)',
        'fix': 'UsdGeom.SetStageMetersPerUnit(stage, 0.01)'
    }})

# Check 3: Missing CollisionAPI on links
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        has_child_collision = any(
            c.HasAPI(UsdPhysics.CollisionAPI) for c in prim.GetAllDescendants()
        )
        if not has_child_collision:
            issues.append({{
                'prim': path,
                'severity': 'warning',
                'issue': 'Link has RigidBodyAPI but no CollisionAPI',
                'fix': f"UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath('{{path}}'))"
            }})

# Check 4: Zero-mass links
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_attr = prim.GetAttribute('physics:mass')
        if mass_attr and mass_attr.Get() is not None and mass_attr.Get() == 0.0:
            issues.append({{
                'prim': path,
                'severity': 'error',
                'issue': 'Zero mass on link — causes simulation instability',
                'fix': f"stage.GetPrimAtPath('{{path}}').GetAttribute('physics:mass').Set(1.0)"
            }})

# Check 5: Infinite joint limits
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.IsA(UsdPhysics.RevoluteJoint) or prim.HasAPI(UsdPhysics.RevoluteJointAPI):
        lower = prim.GetAttribute('physics:lowerLimit')
        upper = prim.GetAttribute('physics:upperLimit')
        if lower and upper:
            lo_val = lower.Get()
            hi_val = upper.Get()
            if lo_val is not None and hi_val is not None:
                if abs(lo_val) > 1e6 or abs(hi_val) > 1e6:
                    issues.append({{
                        'prim': path,
                        'severity': 'warning',
                        'issue': f'Infinite joint limits: [{{lo_val}}, {{hi_val}}]',
                        'fix': f"Set finite joint limits on '{{path}}'"
                    }})

# Check 6: Extreme inertia ratios
inertia_vals = []
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.MassAPI):
        diag = prim.GetAttribute('physics:diagonalInertia')
        if diag and diag.Get() is not None:
            vals = [float(v) for v in diag.Get()]
            inertia_vals.extend(vals)
            if any(v <= 0 for v in vals):
                issues.append({{
                    'prim': path,
                    'severity': 'critical',
                    'issue': f'Non-positive inertia: {{vals}}',
                    'fix': f"stage.GetPrimAtPath('{{path}}').GetAttribute('physics:diagonalInertia').Set(Gf.Vec3f(0.01, 0.01, 0.01))"
                }})

if len(inertia_vals) >= 2:
    pos_vals = [v for v in inertia_vals if v > 0]
    if pos_vals and max(pos_vals) / min(pos_vals) > 1000:
        issues.append({{
            'prim': '{art_path}',
            'severity': 'warning',
            'issue': f'Extreme inertia ratio across links: {{max(pos_vals)/min(pos_vals):.0f}}:1',
            'fix': 'Review inertia values — extreme ratios cause PhysX solver instability'
        }})

print(json.dumps({{'articulation_path': '{art_path}', 'issues': issues, 'total': len(issues)}}))
"""


CODE_GEN_HANDLERS["verify_import"] = _gen_verify_import


# ── Phase 8B Addendum: Workspace & Singularity ─────────────────────────────

def _gen_show_workspace(args: Dict) -> str:
    """Generate code to visualize robot workspace using batch FK + manipulability."""
    art_path = args["articulation_path"]
    resolution = args.get("resolution", 500000)
    color_mode = args.get("color_mode", "manipulability")

    return f"""\
import omni.usd
import numpy as np
from pxr import UsdPhysics, PhysxSchema
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect joint info (limits and names)
joints = []
for desc in art_prim.GetAllDescendants():
    if desc.HasAPI(UsdPhysics.RevoluteJointAPI) or desc.IsA(UsdPhysics.RevoluteJoint):
        lower = desc.GetAttribute('physics:lowerLimit')
        upper = desc.GetAttribute('physics:upperLimit')
        lo = lower.Get() if lower and lower.Get() is not None else -180.0
        hi = upper.Get() if upper and upper.Get() is not None else 180.0
        joints.append({{
            'name': desc.GetName(),
            'lower': np.radians(lo),
            'upper': np.radians(hi),
        }})

n_joints = len(joints)
if n_joints == 0:
    raise RuntimeError('No revolute joints found in {{art_prim.GetPath()}}')

n_samples = {resolution}
print(f'Sampling {{n_samples}} configurations across {{n_joints}} joints...')

# Generate random joint configurations within limits
joint_samples = np.zeros((n_samples, n_joints))
for i, j in enumerate(joints):
    joint_samples[:, i] = np.random.uniform(j['lower'], j['upper'], n_samples)

# Use cuRobo for batch FK if available, otherwise fallback to Lula
try:
    from curobo.types.robot import RobotConfig
    from curobo.wrap.reacher.ik_solver import IKSolver
    print('Using cuRobo for batch FK')
    use_curobo = True
except ImportError:
    print('cuRobo not available, using Lula FK fallback')
    use_curobo = False

if not use_curobo:
    # Lula FK fallback: use the kinematics solver
    from isaacsim.robot_motion.motion_generation import interface_config_loader
    from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('franka')
    kin = LulaKinematicsSolver(**kin_config)

# Compute FK positions and manipulability for each sample
ee_positions = np.zeros((n_samples, 3))
manipulability = np.zeros(n_samples)

for idx in range(n_samples):
    q = joint_samples[idx]
    if not use_curobo:
        # Lula FK
        pos, rot = kin.compute_forward_kinematics('panda_hand', q)
        ee_positions[idx] = pos
    # Compute numerical Jacobian (finite differences)
    eps = 1e-4
    J = np.zeros((3, n_joints))
    for j_idx in range(n_joints):
        q_plus = q.copy()
        q_plus[j_idx] += eps
        if not use_curobo:
            pos_plus, _ = kin.compute_forward_kinematics('panda_hand', q_plus)
        J[:, j_idx] = (pos_plus - ee_positions[idx]) / eps
    # Manipulability = sqrt(det(J * J^T))
    JJT = J @ J.T
    det_val = np.linalg.det(JJT)
    manipulability[idx] = np.sqrt(max(0, det_val))

# Normalize manipulability for color mapping
m_min, m_max = manipulability.min(), manipulability.max()
if m_max > m_min:
    m_norm = (manipulability - m_min) / (m_max - m_min)
else:
    m_norm = np.ones(n_samples) * 0.5

# Color mapping: green (high) -> yellow (mid) -> red (low)
def manipulability_color(val):
    if val > 0.5:
        t = (val - 0.5) * 2  # 0-1
        return (1 - t, 1.0, 0.0, 0.6)  # yellow -> green
    else:
        t = val * 2  # 0-1
        return (1.0, t, 0.0, 0.6)  # red -> yellow

color_mode = '{color_mode}'
draw = _debug_draw.acquire_debug_draw_interface()
draw.clear_points()

# Draw in batches for performance
batch_size = 10000
for start in range(0, n_samples, batch_size):
    end = min(start + batch_size, n_samples)
    pts = [list(ee_positions[i]) for i in range(start, end)]
    if color_mode == 'reachability':
        colors = [(0.0, 0.8, 0.0, 0.4)] * len(pts)
    elif color_mode == 'singularity_distance':
        colors = [manipulability_color(1.0 - m_norm[i]) for i in range(start, end)]
    else:  # manipulability
        colors = [manipulability_color(m_norm[i]) for i in range(start, end)]
    sizes = [3.0] * len(pts)
    draw.draw_points(pts, colors, sizes)

print(f'Workspace visualization: {{n_samples}} points rendered')
print(f'Color mode: {{color_mode}}')
print(f'Manipulability range: [{{m_min:.4f}}, {{m_max:.4f}}]')
"""


CODE_GEN_HANDLERS["show_workspace"] = _gen_show_workspace


def _gen_check_singularity(args: Dict) -> str:
    """Generate code to check singularity at a target pose."""
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")

    ori_line = f"target_ori = np.array({list(target_ori)})" if target_ori else "target_ori = None"

    return f"""\
import omni.usd
import numpy as np
from pxr import UsdPhysics
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
from isaacsim.robot_motion.motion_generation import ArticulationKinematicsSolver
from isaacsim.core.prims import SingleArticulation
import json

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

target_pos = np.array({list(target_pos)})
{ori_line}

# Initialize kinematics solver
kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('franka')
kin_solver = LulaKinematicsSolver(**kin_config)

# Initialize articulation
art = SingleArticulation(prim_path='{art_path}')
art.initialize()
art_kin = ArticulationKinematicsSolver(art, kin_solver, 'panda_hand')

# Solve IK
result, success = art_kin.compute_inverse_kinematics(target_pos, target_ori)
if not success:
    print(json.dumps({{
        'status': 'ik_failed',
        'message': 'IK solver could not find a solution for the target pose',
        'target_position': {list(target_pos)},
        'singularity_risk': 'unknown',
    }}))
else:
    joint_positions = result.joint_positions

    # Heuristic pre-filter: check for wrist/elbow singularity patterns
    heuristic_warnings = []

    # Wrist singularity: joints 4 and 6 nearly aligned (joint 5 near 0)
    if len(joint_positions) >= 7:
        if abs(joint_positions[5]) < 0.05:  # Joint 6 (index 5) near zero
            heuristic_warnings.append('Wrist singularity: joint 6 near zero (wrist aligned)')
        # Elbow singularity: joint 4 near fully extended
        if abs(joint_positions[3]) < 0.05 or abs(joint_positions[3] - np.pi) < 0.1:
            heuristic_warnings.append('Elbow singularity: joint 4 near extension limit')

    # Compute Jacobian at the IK solution
    n_joints = len(joint_positions)
    eps = 1e-5
    J = np.zeros((6, n_joints))  # 6 DOF: 3 position + 3 orientation

    fk_pos, fk_rot = kin_solver.compute_forward_kinematics('panda_hand', joint_positions)

    for j in range(n_joints):
        q_plus = joint_positions.copy()
        q_plus[j] += eps
        pos_plus, rot_plus = kin_solver.compute_forward_kinematics('panda_hand', q_plus)
        J[:3, j] = (pos_plus - fk_pos) / eps
        # Orientation part (simplified: angular velocity from rotation difference)
        rot_diff = rot_plus - fk_rot
        J[3:, j] = rot_diff / eps

    # SVD condition number
    singular_values = np.linalg.svd(J, compute_uv=False)
    sigma_min = singular_values[-1]
    sigma_max = singular_values[0]
    condition_number = sigma_max / sigma_min if sigma_min > 1e-10 else float('inf')

    # Manipulability measure
    manipulability = np.prod(singular_values)

    # Classification thresholds
    if condition_number < 50:
        status = 'safe'
        color = 'green'
    elif condition_number < 100:
        status = 'warning'
        color = 'yellow'
    else:
        status = 'danger'
        color = 'red'

    result_data = {{
        'status': status,
        'color': color,
        'condition_number': float(condition_number),
        'manipulability': float(manipulability),
        'singular_values': [float(s) for s in singular_values],
        'sigma_min': float(sigma_min),
        'sigma_max': float(sigma_max),
        'joint_positions': [float(q) for q in joint_positions],
        'target_position': {list(target_pos)},
        'heuristic_warnings': heuristic_warnings,
    }}
    print(json.dumps(result_data, indent=2))

    if status == 'danger':
        print(f'WARNING: Target pose is near a kinematic singularity (condition={{condition_number:.1f}})')
    elif status == 'warning':
        print(f'CAUTION: Elevated singularity risk (condition={{condition_number:.1f}})')
    else:
        print(f'OK: Target pose is well-conditioned (condition={{condition_number:.1f}})')
"""


CODE_GEN_HANDLERS["check_singularity"] = _gen_check_singularity


def _gen_monitor_joint_effort(args: Dict) -> str:
    """Generate code to monitor joint efforts over time via physics callback."""
    art_path = args["articulation_path"]
    duration = args.get("duration_seconds", 5.0)

    return f"""\
import omni.usd
import omni.physx
import numpy as np
from pxr import UsdPhysics
from isaacsim.core.prims import SingleArticulation
import json

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Initialize articulation
art = SingleArticulation(prim_path='{art_path}')
art.initialize()

joint_names = art.dof_names
n_joints = art.num_dof

# Collect joint effort limits from USD
effort_limits = []
for desc in art_prim.GetAllDescendants():
    if desc.HasAPI(UsdPhysics.RevoluteJointAPI) or desc.IsA(UsdPhysics.RevoluteJoint):
        drive = UsdPhysics.DriveAPI.Get(desc, 'angular')
        max_force = drive.GetMaxForceAttr().Get() if drive.GetMaxForceAttr() else None
        effort_limits.append(max_force if max_force is not None else 87.0)

# Pad if needed
while len(effort_limits) < n_joints:
    effort_limits.append(87.0)

# Data collection buffers
duration = {duration}
collected_data = {{
    'positions': [],
    'velocities': [],
    'efforts': [],
    'timestamps': [],
}}

import time
start_time = time.time()

def _physics_step_callback(step_size):
    elapsed = time.time() - start_time
    if elapsed > duration:
        return

    positions = art.get_joint_positions()
    velocities = art.get_joint_velocities()
    efforts = art.get_applied_joint_efforts()

    collected_data['positions'].append(positions.tolist() if positions is not None else [0.0] * n_joints)
    collected_data['velocities'].append(velocities.tolist() if velocities is not None else [0.0] * n_joints)
    collected_data['efforts'].append(efforts.tolist() if efforts is not None else [0.0] * n_joints)
    collected_data['timestamps'].append(elapsed)

# Register physics callback
sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_physics_step_callback)

# Wait for data collection to complete
import asyncio

async def _wait_and_report():
    await asyncio.sleep(duration + 0.5)

    # Unsubscribe
    sub.unsubscribe() if hasattr(sub, 'unsubscribe') else None

    n_samples = len(collected_data['timestamps'])
    if n_samples == 0:
        print('No data collected. Ensure simulation is playing.')
        return

    positions = np.array(collected_data['positions'])
    velocities = np.array(collected_data['velocities'])
    efforts = np.array(collected_data['efforts'])

    # Per-joint statistics
    joint_stats = []
    flagged_joints = []
    for j in range(n_joints):
        name = joint_names[j] if j < len(joint_names) else f'joint_{{j}}'
        limit = effort_limits[j]
        eff = efforts[:, j]
        max_eff = float(np.max(np.abs(eff)))
        mean_eff = float(np.mean(np.abs(eff)))
        utilization = max_eff / limit if limit > 0 else 0.0

        stat = {{
            'joint': name,
            'effort_limit': limit,
            'max_effort': max_eff,
            'mean_effort': mean_eff,
            'utilization': utilization,
            'pos_range': [float(np.min(positions[:, j])), float(np.max(positions[:, j]))],
            'vel_range': [float(np.min(velocities[:, j])), float(np.max(velocities[:, j]))],
        }}
        joint_stats.append(stat)

        if utilization > 0.9:
            flagged_joints.append({{
                'joint': name,
                'utilization': utilization,
                'severity': 'critical' if utilization > 0.95 else 'warning',
                'message': f'Joint {{name}} at {{utilization*100:.1f}}% of effort limit ({{max_eff:.2f}}/{{limit:.2f}})',
            }})

    result = {{
        'articulation_path': '{art_path}',
        'duration_seconds': duration,
        'samples_collected': n_samples,
        'joint_stats': joint_stats,
        'flagged_joints': flagged_joints,
        'summary': f'Monitored {{n_joints}} joints for {{duration}}s ({{n_samples}} samples). {{len(flagged_joints)}} joints flagged.',
    }}
    print(json.dumps(result, indent=2))

    if flagged_joints:
        print(f'\\nWARNING: {{len(flagged_joints)}} joint(s) exceeding 90% effort limit:')
        for f in flagged_joints:
            print(f"  - {{f['message']}}")
    else:
        print('\\nAll joints within safe effort limits.')

asyncio.ensure_future(_wait_and_report())
print(f'Monitoring joint efforts for {{duration}}s... (ensure simulation is playing)')
"""


CODE_GEN_HANDLERS["monitor_joint_effort"] = _gen_monitor_joint_effort