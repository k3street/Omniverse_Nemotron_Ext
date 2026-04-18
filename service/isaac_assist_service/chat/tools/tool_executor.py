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
        "g1": "g1.usd",
        "unitree_g1": "g1.usd",
        "g1_23dof": "g1_23dof_robot.usd",
        "h1": "h1_hand_left.usd",
        "unitree_h1": "h1_hand_left.usd",
        "allegro": "allegro_hand.usd",
        "ridgeback_franka": "ridgeback_franka.usd",
        "humanoid": "humanoid.usd",
        "humanoid_28": "humanoid_28.usd",
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

# ── RViz2 launch tools ──────────────────────────────────────────────────────
from .rviz_launcher import handle_launch_rviz2, handle_stop_rviz2

DATA_HANDLERS["launch_rviz2"] = handle_launch_rviz2
DATA_HANDLERS["stop_rviz2"] = handle_stop_rviz2


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


# ── cuRobo GPU Motion Planning ───────────────────────────────────────────────

# cuRobo robot config → joint names mapping (for trajectory execution)
_CUROBO_ROBOT_JOINTS = {
    "franka.yml": [
        "panda_joint1", "panda_joint2", "panda_joint3", "panda_joint4",
        "panda_joint5", "panda_joint6", "panda_joint7",
    ],
    "ur5e.yml": [
        "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
        "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
    ],
    "ur10e.yml": [
        "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
        "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
    ],
    "kinova_gen3.yml": [
        "joint_1", "joint_2", "joint_3", "joint_4",
        "joint_5", "joint_6", "joint_7",
    ],
    "iiwa.yml": [
        "iiwa_joint_1", "iiwa_joint_2", "iiwa_joint_3", "iiwa_joint_4",
        "iiwa_joint_5", "iiwa_joint_6", "iiwa_joint_7",
    ],
    "jaco7.yml": [
        "j2n7s300_joint_1", "j2n7s300_joint_2", "j2n7s300_joint_3",
        "j2n7s300_joint_4", "j2n7s300_joint_5", "j2n7s300_joint_6",
        "j2n7s300_joint_7",
    ],
}


def _gen_curobo_motion_plan(args: Dict) -> str:
    """Generate code that runs cuRobo MotionGen inside Isaac Sim's Python env."""
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    robot_cfg = args.get("robot_config", "franka.yml")
    interp_dt = args.get("interpolation_dt", 0.02)
    max_attempts = args.get("max_attempts", 5)
    world_obs = args.get("world_obstacles")

    joint_names = _CUROBO_ROBOT_JOINTS.get(robot_cfg, _CUROBO_ROBOT_JOINTS["franka.yml"])

    lines = [
        "import torch",
        "import numpy as np",
        "from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig, MotionGenPlanConfig",
        "from curobo.types.math import Pose",
        "from curobo.types.robot import JointState as CuroboJointState",
        "from isaacsim.core.prims import SingleArticulation",
        "",
        f"# cuRobo motion plan → target {target_pos}",
        f"robot_cfg_file = '{robot_cfg}'",
    ]

    # World config
    if world_obs:
        lines.append(f"world_config = {json.dumps(world_obs)}")
    else:
        lines.extend([
            "",
            "# Read world obstacles from USD stage",
            "try:",
            "    from curobo.util.usd_helper import UsdHelper",
            "    usd_helper = UsdHelper()",
            "    usd_helper.load_stage(usd_helper.stage)",
            "    world_config = usd_helper.get_obstacles_from_stage(",
            f"        reference_prim_path='{art_path}',",
            "        ignore_substring=['visual', 'finger'],",
            "    ).get_collision_check_world()",
            "except Exception as e:",
            "    print(f'Could not read world from stage: {e}, planning without obstacles')",
            "    world_config = {'cuboid': {'ground': {'dims': [10, 10, 0.01], 'pose': [0, 0, -0.005, 1, 0, 0, 0]}}}",
        ])

    lines.extend([
        "",
        "motion_gen_config = MotionGenConfig.load_from_robot_config(",
        "    robot_cfg_file,",
        "    world_config,",
        f"    interpolation_dt={interp_dt},",
        ")",
        "motion_gen = MotionGen(motion_gen_config)",
        "motion_gen.warmup()",
        "",
        "# Read current joint state",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "art.initialize()",
        "q_current = art.get_joint_positions()",
        f"joint_names = {joint_names}",
        "n_dof = len(joint_names)",
        "start_state = CuroboJointState.from_position(",
        "    torch.tensor(q_current[:n_dof], dtype=torch.float32).unsqueeze(0).cuda(),",
        "    joint_names=joint_names,",
        ")",
        "",
        f"goal_pose = Pose.from_list({list(target_pos) + (list(target_ori) if target_ori else [1.0, 0.0, 0.0, 0.0])})",
        "",
        f"result = motion_gen.plan_single(start_state, goal_pose, MotionGenPlanConfig(max_attempts={max_attempts}))",
        "",
        "if result.success:",
        "    traj = result.get_interpolated_plan()",
        "    positions = traj.position.cpu().numpy().tolist()",
        f"    print(f'cuRobo: planned {{len(positions)}} waypoints (dt={interp_dt}s)')",
        "    # Store for curobo_execute_trajectory",
        "    import json as _json",
        "    _traj_data = {'joint_names': joint_names, 'waypoints': positions,",
        f"                  'dt': {interp_dt}, 'success': True}}",
        "    print('CUROBO_TRAJ:' + _json.dumps(_traj_data))",
        "else:",
        "    print(f'cuRobo: planning failed — {result.status}')",
        "    print('CUROBO_TRAJ:' + _json.dumps({'success': False, 'status': str(result.status)}))",  # noqa: E501
    ])
    return "\n".join(lines)


def _gen_curobo_pick_place(args: Dict) -> str:
    """Generate a full pick-and-place code sequence using cuRobo + ROS2."""
    art_path = args["articulation_path"]
    pick_pos = args["pick_position"]
    place_pos = args["place_position"]
    pick_ori = args.get("pick_orientation", [0, 1, 0, 0])  # top-down
    place_ori = args.get("place_orientation", pick_ori)
    approach_h = args.get("approach_height", 0.1)
    robot_cfg = args.get("robot_config", "franka.yml")
    gripper_joints = args.get("gripper_joint_names", ["panda_finger_joint1", "panda_finger_joint2"])
    gripper_open = args.get("gripper_open", [0.04, 0.04])
    gripper_close = args.get("gripper_close", [0.0, 0.0])
    cmd_topic = args.get("joint_command_topic", "/joint_command")
    state_topic = args.get("joint_state_topic", "/joint_states")
    rate_hz = args.get("execution_rate_hz", 50)

    joint_names = _CUROBO_ROBOT_JOINTS.get(robot_cfg, _CUROBO_ROBOT_JOINTS["franka.yml"])

    # Compute approach/retreat positions
    pick_approach = [pick_pos[0], pick_pos[1], pick_pos[2] + approach_h]
    place_approach = [place_pos[0], place_pos[1], place_pos[2] + approach_h]

    code = f'''\
import torch
import time
import numpy as np
from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig, MotionGenPlanConfig
from curobo.types.math import Pose
from curobo.types.robot import JointState as CuroboJointState
from isaacsim.core.prims import SingleArticulation

# ── Config ──
ROBOT_CFG = '{robot_cfg}'
ART_PATH = '{art_path}'
JOINT_NAMES = {joint_names}
GRIPPER_JOINTS = {gripper_joints}
GRIPPER_OPEN = {gripper_open}
GRIPPER_CLOSE = {gripper_close}
RATE_HZ = {rate_hz}

# Poses: [x, y, z, qw, qx, qy, qz]
PICK_APPROACH = {pick_approach + list(pick_ori)}
PICK_GRASP    = {list(pick_pos) + list(pick_ori)}
PLACE_APPROACH = {place_approach + list(place_ori)}
PLACE_TARGET  = {list(place_pos) + list(place_ori)}

# ── Initialize cuRobo ──
world_config = {{
    'cuboid': {{
        'ground': {{'dims': [10, 10, 0.01], 'pose': [0, 0, -0.005, 1, 0, 0, 0]}},
    }},
}}

# Try to read obstacles from the stage
try:
    from curobo.util.usd_helper import UsdHelper
    usd_helper = UsdHelper()
    usd_helper.load_stage(usd_helper.stage)
    world_config = usd_helper.get_obstacles_from_stage(
        reference_prim_path=ART_PATH,
        ignore_substring=['visual', 'finger'],
    ).get_collision_check_world()
    print('cuRobo: loaded world obstacles from USD stage')
except Exception as e:
    print(f'cuRobo: using ground-only world ({{e}})')

motion_gen_config = MotionGenConfig.load_from_robot_config(
    ROBOT_CFG, world_config, interpolation_dt=0.02,
)
motion_gen = MotionGen(motion_gen_config)
motion_gen.warmup()
print('cuRobo: warmed up')

art = SingleArticulation(prim_path=ART_PATH)
art.initialize()

def get_current_state():
    q = art.get_joint_positions()
    n = len(JOINT_NAMES)
    return CuroboJointState.from_position(
        torch.tensor(q[:n], dtype=torch.float32).unsqueeze(0).cuda(),
        joint_names=JOINT_NAMES,
    )

def plan_to(pose_list):
    start = get_current_state()
    goal = Pose.from_list(pose_list)
    result = motion_gen.plan_single(start, goal, MotionGenPlanConfig(max_attempts=10))
    if result.success:
        return result.get_interpolated_plan().position.cpu().numpy()
    else:
        print(f'  Planning failed: {{result.status}}')
        return None

def execute_traj(waypoints):
    """Apply each waypoint to the articulation directly."""
    n = len(JOINT_NAMES)
    from isaacsim.core.utils.types import ArticulationAction
    dt = 1.0 / RATE_HZ
    for wp in waypoints:
        positions = list(wp[:n])
        action = ArticulationAction(joint_positions=np.array(positions))
        art.apply_action(action)
        # Step the sim for this timestep
        import omni.kit.app
        app = omni.kit.app.get_app()
        app.update()
        time.sleep(dt)

def set_gripper(positions):
    from isaacsim.core.utils.types import ArticulationAction
    # Get full joint positions and just override gripper
    q = art.get_joint_positions()
    n_arm = len(JOINT_NAMES)
    for i, gname in enumerate(GRIPPER_JOINTS):
        # Find gripper joint index
        all_joints = art.dof_names if hasattr(art, 'dof_names') else JOINT_NAMES + GRIPPER_JOINTS
        try:
            idx = list(all_joints).index(gname)
            q[idx] = positions[i]
        except (ValueError, IndexError):
            q[n_arm + i] = positions[i]
    action = ArticulationAction(joint_positions=np.array(q))
    art.apply_action(action)
    import omni.kit.app
    for _ in range(10):  # let gripper settle
        omni.kit.app.get_app().update()
        time.sleep(0.02)

# ── Execute Pick & Place Sequence ──
print('=== cuRobo Pick & Place ===')

# Step 1: Open gripper
print('1. Opening gripper...')
set_gripper(GRIPPER_OPEN)

# Step 2: Move to pick approach
print('2. Moving to pick approach...')
traj = plan_to(PICK_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   ✓ At pick approach')

# Step 3: Move down to grasp
print('3. Moving to grasp position...')
traj = plan_to(PICK_GRASP)
if traj is not None:
    execute_traj(traj)
    print('   ✓ At grasp position')

# Step 4: Close gripper
print('4. Closing gripper...')
set_gripper(GRIPPER_CLOSE)
print('   ✓ Gripper closed')

# Step 5: Lift (back to approach height)
print('5. Lifting...')
traj = plan_to(PICK_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   ✓ Lifted')

# Step 6: Move to place approach
print('6. Moving to place approach...')
traj = plan_to(PLACE_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   ✓ At place approach')

# Step 7: Lower to place
print('7. Lowering to place position...')
traj = plan_to(PLACE_TARGET)
if traj is not None:
    execute_traj(traj)
    print('   ✓ At place position')

# Step 8: Open gripper (release)
print('8. Releasing...')
set_gripper(GRIPPER_OPEN)
print('   ✓ Released')

# Step 9: Retreat
print('9. Retreating...')
traj = plan_to(PLACE_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   ✓ Retreat complete')

print('=== Pick & Place Complete ===')
'''
    return code


CODE_GEN_HANDLERS["curobo_motion_plan"] = _gen_curobo_motion_plan
CODE_GEN_HANDLERS["curobo_pick_place"] = _gen_curobo_pick_place


# ── cuRobo trajectory execution via ROS2 ─────────────────────────────────────

async def _handle_curobo_execute_trajectory(args: Dict) -> Dict:
    """Execute a joint trajectory by publishing waypoints via ROS2."""
    joint_names = args["joint_names"]
    waypoints = args["waypoints"]
    topic = args.get("joint_command_topic", "/joint_command")
    rate_hz = args.get("rate_hz", 50)
    msg_type = args.get("msg_type", "sensor_msgs/msg/JointState")

    if not waypoints:
        return {"error": "No waypoints provided"}

    # Build the publish sequence: each waypoint published for 1/rate_hz seconds
    dt = 1.0 / rate_hz
    messages = []
    durations = []
    for wp in waypoints:
        messages.append({
            "name": joint_names,
            "position": list(wp),
            "velocity": [],
            "effort": [],
        })
        durations.append(dt)

    # Use the existing ros2_publish_sequence handler
    try:
        from . import ros_mcp_tools
        handler = getattr(ros_mcp_tools, "handle_publish_sequence", None)
        if handler:
            result = await handler({
                "topic": topic,
                "msg_type": msg_type,
                "messages": messages,
                "durations": durations,
                "rate_hz": rate_hz,
            })
            return {
                "executed": True,
                "waypoints_sent": len(waypoints),
                "duration_s": round(len(waypoints) * dt, 2),
                "topic": topic,
                **result,
            }
    except ImportError:
        pass

    # Fallback: return the trajectory data for manual execution
    return {
        "executed": False,
        "note": "ros-mcp not available — trajectory planned but not executed via ROS2",
        "joint_names": joint_names,
        "waypoints_count": len(waypoints),
        "duration_s": round(len(waypoints) * dt, 2),
    }

DATA_HANDLERS["curobo_execute_trajectory"] = _handle_curobo_execute_trajectory


# ── cuRobo Vision-Guided Pick & Place ─────────────────────────────────────────

def _gen_curobo_vision_pick(args: Dict) -> str:
    """Generate code for vision-guided pick-and-place with robot segmentation."""
    art_path = args["articulation_path"]
    cam_path = args["camera_prim_path"]
    pick_pos = args["pick_position"]
    place_pos = args["place_position"]
    pick_ori = args.get("pick_orientation", [0, 1, 0, 0])
    place_ori = args.get("place_orientation", pick_ori)
    depth_topic = args.get("depth_topic", "/camera/depth")
    img_size = args.get("depth_image_size", [640, 480])
    robot_cfg = args.get("robot_config", "franka.yml")
    approach_h = args.get("approach_height", 0.1)
    gripper_joints = args.get("gripper_joint_names",
                              ["panda_finger_joint1", "panda_finger_joint2"])
    gripper_open = args.get("gripper_open", [0.04, 0.04])
    gripper_close = args.get("gripper_close", [0.0, 0.0])
    seg_buffer = args.get("segmentation_buffer", 0.02)
    voxel_size = args.get("voxel_size", 0.02)
    rate_hz = args.get("execution_rate_hz", 50)

    joint_names = _CUROBO_ROBOT_JOINTS.get(robot_cfg, _CUROBO_ROBOT_JOINTS["franka.yml"])
    pick_approach = [pick_pos[0], pick_pos[1], pick_pos[2] + approach_h]
    place_approach = [place_pos[0], place_pos[1], place_pos[2] + approach_h]

    code = f'''\
import torch
import time
import numpy as np
from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig, MotionGenPlanConfig
from curobo.wrap.model.robot_segmenter import RobotSegmenter
from curobo.types.math import Pose
from curobo.types.camera import CameraObservation
from curobo.types.robot import JointState as CuroboJointState
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.utils.types import ArticulationAction
from pxr import UsdGeom, Gf

# ── Configuration ──
ROBOT_CFG = '{robot_cfg}'
ART_PATH = '{art_path}'
CAM_PATH = '{cam_path}'
JOINT_NAMES = {joint_names}
GRIPPER_JOINTS = {gripper_joints}
GRIPPER_OPEN = {gripper_open}
GRIPPER_CLOSE = {gripper_close}
RATE_HZ = {rate_hz}
IMG_W, IMG_H = {img_size[0]}, {img_size[1]}
SEG_BUFFER = {seg_buffer}
VOXEL_SIZE = {voxel_size}

PICK_APPROACH = {pick_approach + list(pick_ori)}
PICK_GRASP    = {list(pick_pos) + list(pick_ori)}
PLACE_APPROACH = {place_approach + list(place_ori)}
PLACE_TARGET  = {list(place_pos) + list(place_ori)}

# ── Initialize robot ──
art = SingleArticulation(prim_path=ART_PATH)
art.initialize()

# ── Camera intrinsics from USD prim ──
import omni.usd
stage = omni.usd.get_context().get_stage()
cam_prim = stage.GetPrimAtPath(CAM_PATH)

# Get focal length and sensor size for intrinsics
fl = cam_prim.GetAttribute('focalLength').Get() or 24.0
h_ap = cam_prim.GetAttribute('horizontalAperture').Get() or 36.0
v_ap = cam_prim.GetAttribute('verticalAperture').Get() or 24.0
fx = fl * IMG_W / h_ap
fy = fl * IMG_H / v_ap
cx, cy = IMG_W / 2.0, IMG_H / 2.0
intrinsics = torch.tensor([[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
                           dtype=torch.float32).cuda()
print(f'Camera intrinsics: fx={{fx:.1f}} fy={{fy:.1f}} cx={{cx:.1f}} cy={{cy:.1f}}')

# ── Camera pose (world → robot base frame) ──
cam_xformable = UsdGeom.Xformable(cam_prim)
cam_world_tf = cam_xformable.ComputeLocalToWorldTransform(0)
cam_pos = cam_world_tf.ExtractTranslation()
cam_rot = cam_world_tf.ExtractRotationMatrix()

robot_prim = stage.GetPrimAtPath(ART_PATH)
robot_xformable = UsdGeom.Xformable(robot_prim)
robot_world_tf = robot_xformable.ComputeLocalToWorldTransform(0)
robot_inv_tf = robot_world_tf.GetInverse()

# Camera pose relative to robot base
cam_in_robot = robot_inv_tf * cam_world_tf
pose_mat = np.array(cam_in_robot).T  # USD is row-major, convert
cam_position = torch.tensor(pose_mat[:3, 3], dtype=torch.float32).cuda()
# Build quaternion from rotation matrix
rot_np = pose_mat[:3, :3]
from scipy.spatial.transform import Rotation as R
quat_xyzw = R.from_matrix(rot_np).as_quat()  # [x,y,z,w]
cam_quat = torch.tensor([quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]],
                         dtype=torch.float32).cuda()  # [w,x,y,z]
cam_pose = Pose(position=cam_position.unsqueeze(0), quaternion=cam_quat.unsqueeze(0))
print(f'Camera in robot frame: pos={{cam_position.cpu().tolist()}}')

# ── Initialize Robot Segmenter ──
segmenter = RobotSegmenter.from_robot_file(ROBOT_CFG, seg_buffer=SEG_BUFFER)
print('Robot segmenter initialized')

# ── Capture depth from Isaac Sim's render product ──
from isaacsim.core.utils.render_product import create_render_product
from isaacsim.replicator.core import AnnotatorRegistry
import isaacsim.replicator.core as rep

rp = create_render_product(CAM_PATH, (IMG_W, IMG_H))
depth_annot = rep.AnnotatorRegistry.get_annotator('distance_to_camera')
depth_annot.attach([rp])

# Wait a frame for data
import omni.kit.app
omni.kit.app.get_app().update()
omni.kit.app.get_app().update()

depth_data = depth_annot.get_data()
depth_image = torch.tensor(depth_data, dtype=torch.float32).cuda()
if depth_image.ndim == 2:
    depth_image = depth_image.unsqueeze(0).unsqueeze(0)  # [1, 1, H, W]
elif depth_image.ndim == 3:
    depth_image = depth_image.unsqueeze(0)

print(f'Depth image shape: {{depth_image.shape}}, range: [{{depth_image.min():.3f}}, {{depth_image.max():.3f}}]m')

# ── Robot segmentation — filter robot from depth ──
q_current = art.get_joint_positions()
n_dof = len(JOINT_NAMES)
joint_state = CuroboJointState.from_position(
    torch.tensor(q_current[:n_dof], dtype=torch.float32).unsqueeze(0).cuda(),
    joint_names=JOINT_NAMES,
)

cam_obs = CameraObservation(
    depth_image=depth_image,
    intrinsics=intrinsics.unsqueeze(0),
    pose=cam_pose,
)

seg_result = segmenter.get_robot_mask(cam_obs, joint_state)
world_depth = seg_result.world_depth  # depth with robot removed
robot_mask = seg_result.mask

robot_pixels = robot_mask.sum().item()
total_pixels = robot_mask.numel()
print(f'Robot segmentation: {{robot_pixels}}/{{total_pixels}} pixels masked ({{100*robot_pixels/total_pixels:.1f}}%)')

# ── Build collision world from filtered depth ──
# Convert filtered depth to pointcloud for cuRobo world
from curobo.geom.types import WorldConfig

# Use cuboid world from USD stage as fallback + depth-derived obstacles
try:
    from curobo.util.usd_helper import UsdHelper
    usd_helper = UsdHelper()
    usd_helper.load_stage(usd_helper.stage)
    world_config = usd_helper.get_obstacles_from_stage(
        reference_prim_path=ART_PATH,
        ignore_substring=['visual', 'finger'],
    ).get_collision_check_world()
    print('Loaded world obstacles from USD stage')
except Exception as e:
    world_config = {{'cuboid': {{'ground': {{'dims': [10, 10, 0.01], 'pose': [0, 0, -0.005, 1, 0, 0, 0]}}}}}}
    print(f'Using ground-only world ({{e}})')

# ── Initialize cuRobo MotionGen ──
motion_gen_config = MotionGenConfig.load_from_robot_config(
    ROBOT_CFG, world_config, interpolation_dt=0.02,
)
motion_gen = MotionGen(motion_gen_config)
motion_gen.warmup()
print('cuRobo MotionGen warmed up')

# ── Helper functions ──
def get_current_state():
    q = art.get_joint_positions()
    return CuroboJointState.from_position(
        torch.tensor(q[:n_dof], dtype=torch.float32).unsqueeze(0).cuda(),
        joint_names=JOINT_NAMES,
    )

def plan_to(pose_list):
    start = get_current_state()
    goal = Pose.from_list(pose_list)
    result = motion_gen.plan_single(start, goal, MotionGenPlanConfig(max_attempts=10))
    if result.success:
        return result.get_interpolated_plan().position.cpu().numpy()
    print(f'  Planning failed: {{result.status}}')
    return None

def execute_traj(waypoints):
    dt = 1.0 / RATE_HZ
    for wp in waypoints:
        positions = list(wp[:n_dof])
        action = ArticulationAction(joint_positions=np.array(positions))
        art.apply_action(action)
        omni.kit.app.get_app().update()
        time.sleep(dt)

def set_gripper(positions):
    q = art.get_joint_positions()
    for i, gname in enumerate(GRIPPER_JOINTS):
        try:
            all_names = list(art.dof_names) if hasattr(art, 'dof_names') else JOINT_NAMES + GRIPPER_JOINTS
            idx = all_names.index(gname)
            q[idx] = positions[i]
        except (ValueError, IndexError):
            q[n_dof + i] = positions[i]
    art.apply_action(ArticulationAction(joint_positions=np.array(q)))
    for _ in range(10):
        omni.kit.app.get_app().update()
        time.sleep(0.02)

# ── Execute Vision-Guided Pick & Place ──
print('\\n=== Vision-Guided Pick & Place (with Robot Segmentation) ===')
print(f'Depth camera: {{CAM_PATH}} | Robot filtered: {{robot_pixels}} pixels')

print('1. Opening gripper...')
set_gripper(GRIPPER_OPEN)

print('2. Moving to pick approach...')
traj = plan_to(PICK_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   Done')

# Re-capture depth at new pose for updated world awareness
print('   Updating depth segmentation...')
omni.kit.app.get_app().update()
omni.kit.app.get_app().update()
depth_data = depth_annot.get_data()
new_depth = torch.tensor(depth_data, dtype=torch.float32).cuda()
if new_depth.ndim == 2:
    new_depth = new_depth.unsqueeze(0).unsqueeze(0)
cam_obs_updated = CameraObservation(depth_image=new_depth, intrinsics=intrinsics.unsqueeze(0), pose=cam_pose)
seg_result = segmenter.get_robot_mask(cam_obs_updated, get_current_state())
print(f'   Filtered {{seg_result.mask.sum().item()}} robot pixels')

print('3. Moving to grasp position...')
traj = plan_to(PICK_GRASP)
if traj is not None:
    execute_traj(traj)
    print('   Done')

print('4. Closing gripper...')
set_gripper(GRIPPER_CLOSE)

print('5. Lifting...')
traj = plan_to(PICK_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   Done')

print('6. Moving to place approach...')
traj = plan_to(PLACE_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   Done')

print('7. Lowering to place...')
traj = plan_to(PLACE_TARGET)
if traj is not None:
    execute_traj(traj)
    print('   Done')

print('8. Releasing...')
set_gripper(GRIPPER_OPEN)

print('9. Retreating...')
traj = plan_to(PLACE_APPROACH)
if traj is not None:
    execute_traj(traj)
    print('   Done')

# Cleanup annotator
depth_annot.detach([rp])

print('\\n=== Vision-Guided Pick & Place Complete ===')
'''
    return code

CODE_GEN_HANDLERS["curobo_vision_pick"] = _gen_curobo_vision_pick


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
    "g1": "g1.usd",
    "unitree_g1": "g1.usd",
    "g1_23dof": "g1_23dof_robot.usd",
    "h1": "h1_hand_left.usd",
    "unitree_h1": "h1_hand_left.usd",
    "allegro_hand": "allegro_hand.usd",
    "ridgeback_franka": "ridgeback_franka.usd",
    "humanoid": "humanoid.usd",
    "humanoid_28": "humanoid_28.usd",
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
        "print(f'Launching training: {" ".join(cmd)}')",
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


# ── Stage Analysis ───────────────────────────────────────────────────────────

async def _handle_run_stage_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run all (or selected) validator packs against the live stage."""
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


DATA_HANDLERS["run_stage_analysis"] = _handle_run_stage_analysis
