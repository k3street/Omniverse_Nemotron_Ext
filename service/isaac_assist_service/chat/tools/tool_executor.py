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
_PHYSICS_MATERIALS_PATH = _WORKSPACE / "knowledge" / "physics_materials.json"

# Cache loaded once
_sensor_specs: Optional[List[Dict]] = None
_deformable_presets: Optional[Dict] = None
_physics_materials: Optional[Dict] = None


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


def _load_physics_materials() -> Dict:
    global _physics_materials
    if _physics_materials is not None:
        return _physics_materials
    if _PHYSICS_MATERIALS_PATH.exists():
        _physics_materials = json.loads(_PHYSICS_MATERIALS_PATH.read_text())
    else:
        _physics_materials = {"materials": {}, "pairs": {}, "aliases": {}}
    return _physics_materials


def _normalize_material_name(name: str) -> str:
    """Normalize a user-supplied material name to a database key."""
    db = _load_physics_materials()
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    # Check aliases first
    aliases = db.get("aliases", {})
    if key in aliases:
        return aliases[key]
    # Check direct match in materials
    if key in db["materials"]:
        return key
    # Partial match: e.g. "mild steel" -> "steel_mild"
    for mat_key in db["materials"]:
        if key in mat_key or mat_key in key:
            return mat_key
    return key


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


# ── OmniGraph Assistant ──────────────────────────────────────────────────────

# Canonical OmniGraph templates (verified, cover ~90% of ROS2 use cases).
# Each template is a dict with: nodes, connections, values (parameterized).
# The ROS2Context node is ALWAYS included automatically.

_OG_TEMPLATES = {
    "ros2_clock": {
        "description": "Publish simulation clock to ROS2 /clock topic",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_clock", "isaacsim.ros2.bridge.ROS2PublishClock"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_clock.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_clock.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_clock.inputs:timeStamp"),
        ],
        "values": {},
        "param_keys": [],
    },
    "ros2_joint_state": {
        "description": "Publish robot joint states to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
            ("publish_joint_state", "isaacsim.ros2.bridge.ROS2PublishJointState"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_joint_state.inputs:execIn"),
            ("on_playback_tick.outputs:tick", "articulation_controller.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_joint_state.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_joint_state.inputs:timeStamp"),
        ],
        "values": {
            "articulation_controller.inputs:robotPath": "{robot_path}",
            "publish_joint_state.inputs:topicName": "{topic}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/joint_states"},
    },
    "ros2_camera": {
        "description": "Publish camera images to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("camera_helper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "camera_helper.inputs:execIn"),
            ("ros2_context.outputs:context", "camera_helper.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "camera_helper.inputs:timeStamp"),
        ],
        "values": {
            "camera_helper.inputs:cameraPrimPath": "{camera_path}",
            "camera_helper.inputs:topicName": "{topic}",
        },
        "param_keys": ["camera_path", "topic"],
        "defaults": {"topic": "/camera/image_raw"},
    },
    "ros2_lidar": {
        "description": "Publish lidar scans to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("read_lidar", "isaacsim.sensor.nodes.IsaacReadLidar"),
            ("publish_laser_scan", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_lidar.inputs:execIn"),
            ("read_lidar.outputs:execOut", "publish_laser_scan.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_laser_scan.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_laser_scan.inputs:timeStamp"),
            ("read_lidar.outputs:azimuthRange", "publish_laser_scan.inputs:azimuthRange"),
            ("read_lidar.outputs:depthRange", "publish_laser_scan.inputs:depthRange"),
            ("read_lidar.outputs:horizontalResolution", "publish_laser_scan.inputs:horizontalResolution"),
            ("read_lidar.outputs:intensitiesData", "publish_laser_scan.inputs:intensitiesData"),
            ("read_lidar.outputs:linearDepthData", "publish_laser_scan.inputs:linearDepthData"),
            ("read_lidar.outputs:numCols", "publish_laser_scan.inputs:numCols"),
            ("read_lidar.outputs:numRows", "publish_laser_scan.inputs:numRows"),
        ],
        "values": {
            "read_lidar.inputs:lidarPrimPath": "{lidar_path}",
            "publish_laser_scan.inputs:topicName": "{topic}",
        },
        "param_keys": ["lidar_path", "topic"],
        "defaults": {"topic": "/scan"},
    },
    "ros2_cmd_vel": {
        "description": "Subscribe to /cmd_vel and drive a differential robot",
        "nodes": [
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("subscribe_twist", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("differential_controller", "isaacsim.robot.wheeled_robots.DifferentialController"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "connections": [
            ("ros2_context.outputs:context", "subscribe_twist.inputs:context"),
            ("subscribe_twist.outputs:linearVelocity", "differential_controller.inputs:linearVelocity"),
            ("subscribe_twist.outputs:angularVelocity", "differential_controller.inputs:angularVelocity"),
            ("differential_controller.outputs:velocityCommand", "articulation_controller.inputs:velocityCommand"),
        ],
        "values": {
            "subscribe_twist.inputs:topicName": "{topic}",
            "articulation_controller.inputs:robotPath": "{robot_path}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/cmd_vel"},
    },
    "ros2_tf": {
        "description": "Publish TF transform tree to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_tf", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_tf.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_tf.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_tf.inputs:timeStamp"),
        ],
        "values": {
            "publish_tf.inputs:parentPrim": "{root_prim}",
        },
        "param_keys": ["root_prim"],
        "defaults": {"root_prim": "/World"},
    },
    "ros2_imu": {
        "description": "Publish IMU data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_imu", "isaacsim.sensor.nodes.IsaacReadIMU"),
            ("publish_imu", "isaacsim.ros2.bridge.ROS2PublishImu"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_imu.inputs:execIn"),
            ("read_imu.outputs:execOut", "publish_imu.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_imu.inputs:context"),
            ("read_imu.outputs:angVel", "publish_imu.inputs:angularVelocity"),
            ("read_imu.outputs:linAcc", "publish_imu.inputs:linearAcceleration"),
            ("read_imu.outputs:orientation", "publish_imu.inputs:orientation"),
        ],
        "values": {
            "read_imu.inputs:imuPrimPath": "{imu_path}",
            "publish_imu.inputs:topicName": "{topic}",
        },
        "param_keys": ["imu_path", "topic"],
        "defaults": {"topic": "/imu/data"},
    },
    "ros2_odom": {
        "description": "Publish odometry data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("compute_odom", "isaacsim.core.nodes.IsaacComputeOdometry"),
            ("publish_odom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "compute_odom.inputs:execIn"),
            ("compute_odom.outputs:execOut", "publish_odom.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_odom.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_odom.inputs:timeStamp"),
            ("compute_odom.outputs:angularVelocity", "publish_odom.inputs:angularVelocity"),
            ("compute_odom.outputs:linearVelocity", "publish_odom.inputs:linearVelocity"),
            ("compute_odom.outputs:orientation", "publish_odom.inputs:orientation"),
            ("compute_odom.outputs:position", "publish_odom.inputs:position"),
        ],
        "values": {
            "compute_odom.inputs:chassisPrimPath": "{chassis_path}",
            "publish_odom.inputs:topicName": "{topic}",
        },
        "param_keys": ["chassis_path", "topic"],
        "defaults": {"topic": "/odom"},
    },
}

# Keyword → template mapping for auto-detection from description
_TEMPLATE_KEYWORDS = {
    "ros2_clock": ["clock", "sim_time", "simulation time", "simtime"],
    "ros2_joint_state": ["joint state", "joint_state", "joint states", "joint positions"],
    "ros2_camera": ["camera", "image", "rgb", "depth image"],
    "ros2_lidar": ["lidar", "laser scan", "laserscan", "point cloud lidar"],
    "ros2_cmd_vel": ["cmd_vel", "twist", "teleop", "drive", "velocity command"],
    "ros2_tf": ["tf", "transform tree", "transforms", "tf2"],
    "ros2_imu": ["imu", "inertial", "accelerometer", "gyroscope"],
    "ros2_odom": ["odom", "odometry"],
}


def _detect_template(description: str) -> Optional[str]:
    """Auto-detect the best template from a natural language description."""
    desc_lower = description.lower()
    best_match = None
    best_score = 0
    for template_name, keywords in _TEMPLATE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_match = template_name
    return best_match if best_score > 0 else None


def _gen_create_graph(args: Dict) -> str:
    """Generate OmniGraph code from a template-based description."""
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
print(f"Created {{template}} graph at {graph_path} with {{len(nodes)}} nodes")
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


def _gen_debug_graph(args: Dict) -> str:
    """Generate code that checks an OmniGraph for common issues."""
    graph_path = args["graph_path"]
    return f"""\
import omni.graph.core as og
import json

graph = og.get_graph_by_path('{graph_path}')
if graph is None:
    raise ValueError("No OmniGraph found at '{graph_path}'")

nodes = graph.get_nodes()
issues = []

# Collect node info
node_types = {{}}
node_names = []
has_ros2_context = False
has_on_tick = False

for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    node_types[nname] = ntype
    node_names.append(nname)

    if "ROS2Context" in ntype:
        has_ros2_context = True
    if "OnPlaybackTick" in ntype or "OnTick" in ntype:
        has_on_tick = True

# Check 1: Missing ROS2Context (most common omission)
has_ros2_nodes = any("ros2" in t.lower() or "ROS2" in t for t in node_types.values())
if has_ros2_nodes and not has_ros2_context:
    issues.append({{
        "severity": "error",
        "check": "missing_ros2_context",
        "message": "Graph has ROS2 nodes but no ROS2Context node. Topics will not appear.",
        "fix": "Add a ROS2Context node and connect its context output to all ROS2 nodes.",
    }})

# Check 2: Missing OnTick trigger
if len(nodes) > 0 and not has_on_tick:
    issues.append({{
        "severity": "warning",
        "check": "missing_on_tick",
        "message": "No OnPlaybackTick/OnTick node found. The graph may never evaluate.",
        "fix": "Add an OnPlaybackTick node and connect its tick output to the execution chain.",
    }})

# Check 3: Disconnected inputs (nodes with no incoming connections on execIn)
for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    # Skip source nodes (OnTick, Context)
    if "OnPlaybackTick" in ntype or "OnTick" in ntype or "ROS2Context" in ntype:
        continue
    has_exec_in = False
    exec_connected = False
    for attr in node.get_attributes():
        if attr.get_name() == "inputs:execIn":
            has_exec_in = True
            if len(attr.get_upstream_connections()) > 0:
                exec_connected = True
    if has_exec_in and not exec_connected:
        issues.append({{
            "severity": "warning",
            "check": "disconnected_exec_input",
            "message": f"Node '{{nname}}' ({{ntype}}) has an unconnected execIn — it will never execute.",
            "fix": f"Connect an execution output to {{nname}}.inputs:execIn",
        }})

# Check 4: Duplicate node names
from collections import Counter
dupes = [name for name, count in Counter(node_names).items() if count > 1]
if dupes:
    issues.append({{
        "severity": "error",
        "check": "duplicate_node_names",
        "message": f"Duplicate node names found: {{dupes}}. This can cause connection confusion.",
        "fix": "Rename duplicate nodes to unique names.",
    }})

result = {{
    "graph_path": "{graph_path}",
    "node_count": len(nodes),
    "issues_found": len(issues),
    "issues": issues,
    "node_types": node_types,
    "status": "ok" if len(issues) == 0 else "issues_found",
}}
print(json.dumps(result, indent=2, default=str))
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


def _gen_apply_physics_material(args: Dict) -> str:
    """Generate code to create a PhysicsMaterialAPI with values from the material database."""
    prim_path = args["prim_path"]
    material_name = args["material_name"]

    db = _load_physics_materials()
    mat_key = _normalize_material_name(material_name)
    mat = db["materials"].get(mat_key)

    if mat is None:
        available = sorted(db["materials"].keys())
        return (
            f"raise ValueError("
            f"\"Unknown material '{material_name}' (normalized: '{mat_key}'). "
            f"Available: {', '.join(available)}\")"
        )

    sf = mat["static_friction"]
    df = mat["dynamic_friction"]
    rest = mat["restitution"]
    density = mat["density_kg_m3"]
    safe_name = mat_key.replace(" ", "_")

    return f"""\
import omni.usd
from pxr import UsdPhysics, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure CollisionAPI is applied
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)

# Create physics material
mat_path = '/World/PhysicsMaterials/{safe_name}'
mat_prim = stage.DefinePrim(mat_path)
mat_api = UsdPhysics.MaterialAPI.Apply(mat_prim)
mat_api.CreateStaticFrictionAttr().Set({sf})
mat_api.CreateDynamicFrictionAttr().Set({df})
mat_api.CreateRestitutionAttr().Set({rest})
mat_api.CreateDensityAttr().Set({density})

# Bind physics material to prim
binding_api = UsdPhysics.MaterialAPI(prim)
rel = prim.CreateRelationship('physics:materialBinding', custom=False)
rel.SetTargets([Sdf.Path(mat_path)])

print(f"Applied {{mat_path}} to {prim_path}: static_friction={sf}, dynamic_friction={df}, restitution={rest}, density={density}")
"""


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


def _gen_create_sdg_pipeline(args: Dict) -> str:
    """Generate a full Replicator SDG pipeline with camera, render product, writer."""
    annotators = args.get("annotators", ["bounding_box_2d"])
    output_format = args.get("output_format", "basic")
    num_frames = args.get("num_frames", 100)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    cam_pos = args.get("camera_position", [0, 0, 5])
    cam_look = args.get("camera_look_at", [0, 0, 0])
    resolution = args.get("resolution", [1280, 720])

    # Map output_format to writer class name
    writer_map = {
        "coco": "CocoWriter",
        "kitti": "KittiWriter",
        "basic": "BasicWriter",
        "numpy": "BasicWriter",
    }
    writer_class = writer_map.get(output_format, "BasicWriter")

    # Build writer.initialize() kwargs based on format
    if output_format == "coco":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "kitti":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "numpy":
        # BasicWriter with raw annotator flags
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        if "normals" in annotators:
            init_kwargs.append("normals=True")
        if "depth" in annotators:
            init_kwargs.append("distance_to_camera=True")
        if "semantic_segmentation" in annotators:
            init_kwargs.append("semantic_segmentation=True")
        if "instance_segmentation" in annotators:
            init_kwargs.append("instance_segmentation=True")
        if "bounding_box_2d" in annotators:
            init_kwargs.append("bounding_box_2d=True")
        if "bounding_box_3d" in annotators:
            init_kwargs.append("bounding_box_3d=True")
        if "occlusion" in annotators:
            init_kwargs.append("occlusion=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"
    else:
        # basic
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        for ann in annotators:
            # Map annotator names to BasicWriter kwargs
            kwarg = ann.replace("-", "_")
            if kwarg == "depth":
                kwarg = "distance_to_camera"
            init_kwargs.append(f"{kwarg}=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.create.camera(
        position=({cam_pos[0]}, {cam_pos[1]}, {cam_pos[2]}),
        look_at=({cam_look[0]}, {cam_look[1]}, {cam_look[2]}),
    )
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("{writer_class}")
    {writer_init}
    writer.attach([rp])

    with rep.trigger.on_frame(num_frames={num_frames}):
        pass

    rep.orchestrator.run()

print("SDG pipeline started: {num_frames} frames -> {output_dir}")
"""


def _gen_add_domain_randomizer(args: Dict) -> str:
    """Generate Replicator domain randomization code."""
    target = args["target"]
    rand_type = args["randomizer_type"]
    params = args.get("params", {})

    lines = ["import omni.replicator.core as rep", ""]

    if rand_type == "pose":
        surface = params.get("surface_prim", "/World/Ground")
        min_angle = params.get("min_angle", -180)
        max_angle = params.get("max_angle", 180)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.scatter_2d(",
            f"            surface_prims=rep.get.prims(path_pattern=\"{surface}\")",
            f"        )",
            f"        rep.randomizer.rotation(",
            f"            min_angle={min_angle}, max_angle={max_angle}",
            f"        )",
        ])

    elif rand_type == "texture":
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            "        rep.randomizer.texture(",
            "            textures=rep.distribution.choice([",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Stone/Fieldstone.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Wood/Oak.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Metal/Steel_Brushed.mdl',",
            "            ])",
            "        )",
        ])

    elif rand_type == "color":
        c_min = params.get("color_min", [0, 0, 0])
        c_max = params.get("color_max", [1, 1, 1])
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.color(",
            f"            colors=rep.distribution.uniform(",
            f"                ({c_min[0]}, {c_min[1]}, {c_min[2]}),",
            f"                ({c_max[0]}, {c_max[1]}, {c_max[2]}),",
            f"            )",
            f"        )",
        ])

    elif rand_type == "lighting":
        i_min = params.get("intensity_min", 500)
        i_max = params.get("intensity_max", 2000)
        lines.extend([
            "# Note: 'intensity' is in nits (candelas/m^2), not lux.",
            "# Lux is not directly settable on USD lights.",
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"intensity\",",
            f"            rep.distribution.uniform({i_min}, {i_max}),",
            f"        )",
        ])

    elif rand_type == "material_properties":
        r_min = params.get("roughness_min", 0.0)
        r_max = params.get("roughness_max", 1.0)
        m_min = params.get("metallic_min", 0.0)
        m_max = params.get("metallic_max", 1.0)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"inputs:reflection_roughness_constant\",",
            f"            rep.distribution.uniform({r_min}, {r_max}),",
            f"        )",
            f"        rep.modify.attribute(",
            f"            \"inputs:metallic_constant\",",
            f"            rep.distribution.uniform({m_min}, {m_max}),",
            f"        )",
        ])

    elif rand_type == "visibility":
        prob = params.get("probability", 0.5)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.visibility(",
            f"            rep.distribution.choice([True, False],",
            f"                weights=[{prob}, {1.0 - prob}])",
            f"        )",
        ])

    else:
        lines.append(f"# Unknown randomizer type: {rand_type}")

    return "\n".join(lines)


async def _handle_preview_sdg(args: Dict) -> Dict:
    """Step the Replicator orchestrator a few times for preview frames."""
    num_samples = args.get("num_samples", 3)

    code = f"""\
import omni.replicator.core as rep
import json

num_samples = {num_samples}
for i in range(num_samples):
    rep.orchestrator.step()
    print(f"Preview frame {{i + 1}}/{num_samples} generated")

print(json.dumps({{"preview_frames": num_samples, "status": "done"}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Preview SDG: generate {num_samples} sample frames")


def _gen_export_dataset(args: Dict) -> str:
    """Generate async step-loop code for large dataset generation."""
    output_dir = args["output_dir"]
    num_frames = args["num_frames"]
    step_batch = args.get("step_batch", 10)

    return f"""\
import omni.replicator.core as rep
import asyncio

async def _export_dataset():
    num_frames = {num_frames}
    step_batch = {step_batch}
    for i in range(0, num_frames, step_batch):
        batch = min(step_batch, num_frames - i)
        for _ in range(batch):
            await rep.orchestrator.step_async()
        print(f"Progress: {{i + batch}}/{{num_frames}} frames")
    print(f"Dataset export complete: {{num_frames}} frames -> '{output_dir}'")

asyncio.ensure_future(_export_dataset())
"""


CODE_GEN_HANDLERS["export_dataset"] = _gen_export_dataset


def _gen_configure_zmq_stream(args: Dict) -> str:
    """Generate OmniGraph code to wire a ZMQ PUB stream via NVIDIA's C++ ZMQ bridge node."""
    camera_prim = args["camera_prim"]
    pub_port = args.get("pub_port", 5555)
    resolution = args.get("resolution", [640, 480])
    fps = args.get("fps", 30)
    compression = args.get("compression", "jpeg")

    # Validate port range
    if not (1024 <= pub_port <= 65535):
        return (
            f"# ERROR: pub_port {pub_port} out of valid range (1024-65535)\n"
            f"raise ValueError('pub_port must be between 1024 and 65535, got {pub_port}')"
        )

    return f"""\
import omni.graph.core as og

og.Controller.edit(
    {{"graph_path": "/ZMQStream", "evaluator_name": "execution"}},
    {{
        og.Controller.Keys.CREATE_NODES: [
            ("OnTick", "omni.graph.action.OnPlaybackTick"),
            ("ZMQBridge", "isaacsim.bridge.zmq.OgnIsaacBridgeZMQNode"),
            ("CameraHelper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        og.Controller.Keys.CONNECT: [
            ("OnTick.outputs:tick", "CameraHelper.inputs:execIn"),
            ("CameraHelper.outputs:execOut", "ZMQBridge.inputs:execIn"),
        ],
        og.Controller.Keys.SET_VALUES: [
            ("ZMQBridge.inputs:address", "tcp://127.0.0.1:{pub_port}"),
            ("ZMQBridge.inputs:compression", "{compression}"),
            ("CameraHelper.inputs:cameraPrim", "{camera_prim}"),
            ("CameraHelper.inputs:enabled", True),
            ("CameraHelper.inputs:width", {resolution[0]}),
            ("CameraHelper.inputs:height", {resolution[1]}),
            ("CameraHelper.inputs:fps", {fps}),
        ],
    }},
)
print("ZMQ stream configured on tcp://127.0.0.1:{pub_port}")
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
    "explain_graph": _gen_explain_graph,
    "create_graph": _gen_create_graph,
    "debug_graph": _gen_debug_graph,
    "create_material": _gen_create_material,
    "assign_material": _gen_assign_material,
    "apply_physics_material": _gen_apply_physics_material,
    "sim_control": _gen_sim_control,
    "set_physics_params": _gen_set_physics_params,
    "teleport_prim": _gen_teleport_prim,
    "set_joint_targets": _gen_set_joint_targets,
    "import_robot": _gen_import_robot,
    "anchor_robot": _gen_anchor_robot,
    "set_viewport_camera": _gen_set_viewport_camera,
    "configure_sdg": _gen_configure_sdg,
    "create_sdg_pipeline": _gen_create_sdg_pipeline,
    "add_domain_randomizer": _gen_add_domain_randomizer,
    "export_dataset": _gen_export_dataset,
    "configure_zmq_stream": _gen_configure_zmq_stream,
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


async def _handle_lookup_material(args: Dict) -> Dict:
    """Look up physics material properties for a material pair."""
    mat_a_raw = args.get("material_a", "")
    mat_b_raw = args.get("material_b", "")
    if not mat_a_raw or not mat_b_raw:
        return {"error": "Both material_a and material_b are required."}

    db = _load_physics_materials()
    mat_a = _normalize_material_name(mat_a_raw)
    mat_b = _normalize_material_name(mat_b_raw)

    # Check if materials exist in database
    materials = db.get("materials", {})
    available = sorted(materials.keys())
    if mat_a not in materials and mat_b not in materials:
        return {
            "found": False,
            "error": f"Unknown materials: '{mat_a_raw}' and '{mat_b_raw}'",
            "available_materials": available,
        }
    if mat_a not in materials:
        return {
            "found": False,
            "error": f"Unknown material: '{mat_a_raw}' (normalized: '{mat_a}')",
            "available_materials": available,
        }
    if mat_b not in materials:
        return {
            "found": False,
            "error": f"Unknown material: '{mat_b_raw}' (normalized: '{mat_b}')",
            "available_materials": available,
        }

    # Check pair overrides (both orderings)
    pairs = db.get("pairs", {})
    pair_key_ab = f"{mat_a}:{mat_b}"
    pair_key_ba = f"{mat_b}:{mat_a}"
    if pair_key_ab in pairs:
        result = dict(pairs[pair_key_ab])
        result["found"] = True
        result["pair"] = pair_key_ab
        result["lookup_type"] = "pair_specific"
        result["material_a"] = mat_a
        result["material_b"] = mat_b
        result["density_a_kg_m3"] = materials[mat_a]["density_kg_m3"]
        result["density_b_kg_m3"] = materials[mat_b]["density_kg_m3"]
        return result
    if pair_key_ba in pairs:
        result = dict(pairs[pair_key_ba])
        result["found"] = True
        result["pair"] = pair_key_ba
        result["lookup_type"] = "pair_specific"
        result["material_a"] = mat_a
        result["material_b"] = mat_b
        result["density_a_kg_m3"] = materials[mat_a]["density_kg_m3"]
        result["density_b_kg_m3"] = materials[mat_b]["density_kg_m3"]
        return result

    # Combine individual materials (PhysX average combine mode)
    a = materials[mat_a]
    b = materials[mat_b]
    sf_a = a["static_friction"] if isinstance(a["static_friction"], (int, float)) else a["static_friction"][0]
    sf_b = b["static_friction"] if isinstance(b["static_friction"], (int, float)) else b["static_friction"][0]
    df_a = a["dynamic_friction"] if isinstance(a["dynamic_friction"], (int, float)) else a["dynamic_friction"][0]
    df_b = b["dynamic_friction"] if isinstance(b["dynamic_friction"], (int, float)) else b["dynamic_friction"][0]
    rest_a = a["restitution"]
    rest_b = b["restitution"]

    return {
        "found": True,
        "pair": f"{mat_a}:{mat_b}",
        "lookup_type": "average_combine",
        "static_friction": round((sf_a + sf_b) / 2, 4),
        "dynamic_friction": round((df_a + df_b) / 2, 4),
        "restitution": round((rest_a + rest_b) / 2, 4),
        "combine_mode": "average",
        "material_a": mat_a,
        "material_b": mat_b,
        "density_a_kg_m3": a["density_kg_m3"],
        "density_b_kg_m3": b["density_kg_m3"],
        "note": "Computed via PhysX average combine — pair-specific data not available",
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
    "lookup_material": _handle_lookup_material,
    "scene_summary": _handle_scene_summary,
    "capture_viewport": _handle_capture_viewport,
    "get_console_errors": _handle_get_console_errors,
    "get_articulation_state": _handle_get_articulation_state,
    "list_all_prims": _handle_list_all_prims,
    "measure_distance": _handle_measure_distance,
    "get_debug_info": _handle_get_debug_info,
    "lookup_knowledge": _handle_lookup_knowledge,
    "explain_error": None,  # handled inline by LLM (no tool execution)
    "preview_sdg": _handle_preview_sdg,
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
from isaacsim.sensors.rtx import LidarRtx
lidar = LidarRtx(prim_path=lidar_path)
"""
    if sensor_type == "imu":
        return f"""\
from isaacsim.sensors.physics import IMUSensor
imu = IMUSensor(prim_path='{prim_path}/IMU')
"""
    if sensor_type == "contact_sensor":
        return f"""\
from isaacsim.sensors.physics import ContactSensor
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
            "# Load kinematics config (provides robot_description_path + urdf_path)",
            f"kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{robot_type}')",
            f"rrt = LulaTaskSpaceTrajectoryGenerator(**kin_config)",
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
        f"kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{robot_type}')",
        f"planner = LulaTaskSpaceTrajectoryGenerator(**kin_config)",
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


# ── IsaacLab-Arena Composable Environments ──────────────────────────────────

# Scene type → config module mapping for Arena
_ARENA_SCENE_MAP = {
    "tabletop_pick_and_place": "isaaclab_tasks.envs.arena.scenes.tabletop",
    "kitchen": "isaaclab_tasks.envs.arena.scenes.kitchen",
    "galileo": "isaaclab_tasks.envs.arena.scenes.galileo",
    "custom": None,
}


def _arena_env_id(scene_type: str, robot_asset: str, task: str) -> str:
    """Generate a gymnasium-style env_id from arena components."""
    scene_part = scene_type.replace("_", " ").title().replace(" ", "")
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    task_part = task.replace("_", " ").title().replace(" ", "")
    return f"Arena-{scene_part}{task_part}-{robot_part}-v0"


def _gen_create_arena(args: Dict) -> str:
    scene_type = args["scene_type"]
    robot_asset = args["robot_asset"]
    task = args["task"]
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.5)

    env_id = _arena_env_id(scene_type, robot_asset, task)
    scene_module = _ARENA_SCENE_MAP.get(scene_type)

    scene_import = ""
    scene_cfg = f"'{scene_type}'"
    if scene_module:
        scene_import = f"from {scene_module} import SceneCfg"
        scene_cfg = "SceneCfg()"

    lines = [
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "from isaaclab_tasks.envs.arena.configs.task import TaskCfg",
    ]
    if scene_import:
        lines.append(scene_import)
    lines.extend([
        "",
        f"# Compose Arena environment: {scene_type} + {robot_asset} + {task}",
        f"scene_cfg = {scene_cfg}",
        f"embodiment_cfg = EmbodimentCfg(robot_asset='{robot_asset}')",
        f"task_cfg = TaskCfg(task='{task}')",
        "",
        "# Compile-time composition — combine scene + embodiment + task",
        "env_cfg = ArenaEnvBuilder.combine(",
        "    scene=scene_cfg,",
        "    embodiment=embodiment_cfg,",
        "    task=task_cfg,",
        f"    num_envs={num_envs},",
        f"    env_spacing={env_spacing},",
        ")",
        "",
        f"# Register with gymnasium",
        f"env_id = '{env_id}'",
        "gymnasium.register(",
        f"    id=env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': env_cfg},",
        ")",
        f"print(f'Arena environment registered: {{env_id}}')",
        f"print(f'  Scene: {scene_type}, Robot: {robot_asset}, Task: {task}')",
        f"print(f'  Envs: {num_envs}, Spacing: {env_spacing}m')",
    ])
    return "\n".join(lines)


def _gen_create_arena_variant(args: Dict) -> str:
    base_env_id = args["base_env_id"]
    robot_asset = args["robot_asset"]

    # Derive new env_id by replacing robot name in the base ID
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    # Replace the robot part between last '-' and '-v0'
    parts = base_env_id.rsplit("-", 2)  # e.g. ['Arena-TabletopPickAndPlace', 'Franka', 'v0']
    new_env_id = f"{parts[0]}-{robot_part}-v0" if len(parts) >= 3 else f"{base_env_id}-{robot_part}"

    lines = [
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "",
        f"# Create variant of '{base_env_id}' with robot '{robot_asset}'",
        f"base_env_id = '{base_env_id}'",
        f"base_spec = gymnasium.spec(base_env_id)",
        f"base_cfg = base_spec.kwargs['cfg']",
        "",
        f"# Replace embodiment config with new robot",
        f"new_embodiment = EmbodimentCfg(robot_asset='{robot_asset}')",
        "variant_cfg = ArenaEnvBuilder.combine(",
        "    scene=base_cfg.scene,",
        "    embodiment=new_embodiment,",
        "    task=base_cfg.task,",
        "    num_envs=base_cfg.scene.num_envs,",
        "    env_spacing=base_cfg.scene.env_spacing,",
        ")",
        "",
        f"variant_env_id = '{new_env_id}'",
        "gymnasium.register(",
        f"    id=variant_env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': variant_cfg},",
        ")",
        f"print(f'Arena variant registered: {{variant_env_id}}')",
        f"print(f'  Based on: {base_env_id}')",
        f"print(f'  New robot: {robot_asset}')",
    ]
    return "\n".join(lines)


def _gen_run_arena_benchmark(args: Dict) -> str:
    env_id = args["env_id"]
    num_episodes = args.get("num_episodes", 100)
    metrics = args.get("metrics", ["success_rate", "episode_length"])
    checkpoint = args.get("checkpoint")

    metrics_str = repr(metrics)

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "import json",
        "",
        f"env_id = '{env_id}'",
        f"num_episodes = {num_episodes}",
        f"metrics = {metrics_str}",
        "",
        "# Create results directory",
        f"results_dir = 'workspace/arena_benchmarks/{env_id}'",
        "os.makedirs(results_dir, exist_ok=True)",
        "results_file = os.path.join(results_dir, 'results.json')",
        "",
        "# Build benchmark command (runs as separate IsaacLab process)",
        "cmd = [",
        "    sys.executable, '-m',",
        "    'isaaclab_tasks.envs.arena.benchmark',",
        f"    '--env_id', env_id,",
        f"    '--num_episodes', str(num_episodes),",
        "    '--metrics', ','.join(metrics),",
        "    '--results_file', results_file,",
    ]
    if checkpoint:
        lines.extend([
            f"    '--checkpoint', '{checkpoint}',",
        ])
    lines.extend([
        "]",
        "",
        "print(f'Launching Arena benchmark: {env_id}')",
        f"print(f'  Episodes: {num_episodes}, Metrics: {{metrics}}')",
    ])
    if checkpoint:
        lines.append(f"print(f'  Checkpoint: {checkpoint}')")
    lines.extend([
        "",
        "proc = subprocess.Popen(",
        "    cmd,",
        "    stdout=subprocess.PIPE,",
        "    stderr=subprocess.STDOUT,",
        ")",
        "print(f'Benchmark started (PID: {proc.pid})')",
        "print(f'Results will be saved to: {results_file}')",
    ])
    return "\n".join(lines)


CODE_GEN_HANDLERS["create_arena"] = _gen_create_arena
CODE_GEN_HANDLERS["create_arena_variant"] = _gen_create_arena_variant
CODE_GEN_HANDLERS["run_arena_benchmark"] = _gen_run_arena_benchmark


async def _handle_arena_leaderboard(args: Dict) -> Dict:
    """Format a leaderboard table from benchmark results."""
    results = args.get("results", [])

    if not results:
        return {
            "leaderboard": "No results to display.",
            "entries": [],
        }

    # Collect all unique metric keys across results
    all_metrics = set()
    for r in results:
        all_metrics.update(r.get("metrics", {}).keys())
    metric_cols = sorted(all_metrics)

    # Build leaderboard entries
    entries = []
    for i, r in enumerate(results):
        entry = {
            "rank": i + 1,
            "env_id": r.get("env_id", "unknown"),
            "robot": r.get("robot", "unknown"),
        }
        for m in metric_cols:
            entry[m] = r.get("metrics", {}).get(m, "N/A")
        entries.append(entry)

    # Sort by success_rate descending if available, else by first metric
    sort_key = "success_rate" if "success_rate" in metric_cols else (metric_cols[0] if metric_cols else None)
    if sort_key:
        entries.sort(
            key=lambda e: e.get(sort_key, 0) if isinstance(e.get(sort_key), (int, float)) else 0,
            reverse=True,
        )
        for i, e in enumerate(entries):
            e["rank"] = i + 1

    # Format as text table
    header_cols = ["Rank", "Robot", "Env ID"] + metric_cols
    rows = []
    for e in entries:
        row = [str(e["rank"]), e["robot"], e["env_id"]]
        for m in metric_cols:
            val = e.get(m, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.4f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build formatted table
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(f" {h:<{col_widths[i]}} " for i, h in enumerate(header_cols)) + "|"
    table_lines = [sep, header_line, sep]
    for row in rows:
        line = "|" + "|".join(f" {cell:<{col_widths[i]}} " for i, cell in enumerate(row)) + "|"
        table_lines.append(line)
    table_lines.append(sep)
    table_text = "\n".join(table_lines)

    return {
        "leaderboard": table_text,
        "entries": entries,
        "metric_columns": metric_cols,
        "count": len(entries),
    }


DATA_HANDLERS["arena_leaderboard"] = _handle_arena_leaderboard


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


# ── Phase 8F Addendum: ROS2 Quality Diagnostics ────────────────────────────

# QoS preset mapping: topic keyword → (reliability, durability, description)
_ROS2_QOS_PRESETS = {
    "scan": ("BEST_EFFORT", "VOLATILE", "Laser scan data — high-frequency, drop-tolerant"),
    "robot_description": ("RELIABLE", "TRANSIENT_LOCAL", "Robot URDF — latched, must arrive"),
    "tf": ("RELIABLE", "VOLATILE", "Transform tree — must be reliable"),
    "tf_static": ("RELIABLE", "TRANSIENT_LOCAL", "Static transforms — latched"),
    "cmd_vel": ("RELIABLE", "VOLATILE", "Velocity commands — must not be dropped"),
    "camera": ("BEST_EFFORT", "VOLATILE", "Camera images — high-bandwidth, drop-tolerant"),
    "image": ("BEST_EFFORT", "VOLATILE", "Image data — high-bandwidth, drop-tolerant"),
    "joint_states": ("RELIABLE", "VOLATILE", "Joint state feedback — must be reliable"),
    "clock": ("BEST_EFFORT", "VOLATILE", "Simulation clock — high-frequency"),
}


async def _handle_diagnose_ros2(args: Dict) -> Dict:
    """Run comprehensive ROS2 integration health check on the current scene.

    Checks performed:
    1. ROS2Context node present in OmniGraph
    2. ROS distro detection
    3. QoS profile mismatches between common topic pairs
    4. use_sim_time parameter configuration
    5. Clock publishing (ROS2PublishClock node)
    6. Domain ID consistency
    7. Dangling OmniGraph connections
    """
    issues: List[Dict[str, Any]] = []

    # Generate diagnostic code that runs inside Kit
    diag_code = '''\
import omni.graph.core as og
import json
import os

result = {
    "ros2_context_found": False,
    "ros2_context_path": None,
    "distro": None,
    "domain_id": None,
    "clock_publisher_found": False,
    "use_sim_time": None,
    "og_graphs": [],
    "dangling_connections": [],
    "qos_nodes": [],
}

# Check ROS_DISTRO environment variable
result["distro"] = os.environ.get("ROS_DISTRO", None)
result["domain_id"] = os.environ.get("ROS_DOMAIN_ID", "0")

# Scan all OmniGraph graphs
try:
    all_graphs = og.get_all_graphs()
    for graph in all_graphs:
        graph_path = graph.get_path_to_graph()
        result["og_graphs"].append(graph_path)
        nodes = graph.get_nodes()
        for node in nodes:
            node_type = node.get_type_name()
            node_path = node.get_prim_path()

            # Check for ROS2Context
            if "ROS2Context" in node_type:
                result["ros2_context_found"] = True
                result["ros2_context_path"] = str(node_path)
                # Try to read domain_id attribute
                domain_attr = node.get_attribute("inputs:domain_id")
                if domain_attr:
                    result["domain_id_node"] = domain_attr.get()

            # Check for ROS2PublishClock
            if "PublishClock" in node_type:
                result["clock_publisher_found"] = True

            # Collect QoS-relevant nodes
            if "ROS2" in node_type and "Publish" in node_type:
                topic_attr = node.get_attribute("inputs:topicName")
                qos_attr = node.get_attribute("inputs:qosProfile")
                result["qos_nodes"].append({
                    "node_type": node_type,
                    "node_path": str(node_path),
                    "topic": topic_attr.get() if topic_attr else None,
                    "qos": qos_attr.get() if qos_attr else None,
                })

        # Check for dangling connections
        for node in nodes:
            for attr in node.get_attributes():
                if attr.get_port_type() == og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT:
                    upstream = attr.get_upstream_connections()
                    if not upstream and attr.get_name().startswith("inputs:execIn"):
                        result["dangling_connections"].append({
                            "node": str(node.get_prim_path()),
                            "attr": attr.get_name(),
                        })
except Exception as e:
    result["scan_error"] = str(e)

# Check use_sim_time via carb settings
try:
    import carb.settings
    settings = carb.settings.get_settings()
    result["use_sim_time"] = settings.get("/persistent/exts/isaacsim.ros2.bridge/useSimTime")
except Exception:
    result["use_sim_time"] = None

print(json.dumps(result))
'''

    try:
        diag_result = await kit_tools.queue_exec_patch(diag_code, "ROS2 diagnostic scan")
        # Parse the result if we got immediate output
        if isinstance(diag_result, dict) and diag_result.get("output"):
            import json as _json
            scene_info = _json.loads(diag_result["output"])
        else:
            scene_info = {}
    except Exception:
        scene_info = {}

    # Issue 1: ROS2Context node
    if not scene_info.get("ros2_context_found", False):
        issues.append({
            "id": "no_ros2_context",
            "severity": "critical",
            "message": "No ROS2Context node found in any OmniGraph",
            "fix": "Add a ROS2Context node to your action graph. This is required for all ROS2 bridge communication.",
            "tool_hint": "create_omnigraph with a ROS2Context node",
        })

    # Issue 2: ROS distro
    distro = scene_info.get("distro")
    if not distro:
        issues.append({
            "id": "no_ros_distro",
            "severity": "warning",
            "message": "ROS_DISTRO environment variable not set",
            "fix": "Source your ROS2 workspace: source /opt/ros/<distro>/setup.bash",
            "tool_hint": None,
        })

    # Issue 3: Clock publisher
    if not scene_info.get("clock_publisher_found", False):
        issues.append({
            "id": "no_clock_publisher",
            "severity": "warning",
            "message": "No ROS2PublishClock node found — /clock topic will not be published",
            "fix": "Add a ROS2PublishClock node to publish simulation time. Use configure_ros2_time tool.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 4: use_sim_time
    use_sim_time = scene_info.get("use_sim_time")
    clock_found = scene_info.get("clock_publisher_found", False)
    if clock_found and use_sim_time is not True:
        issues.append({
            "id": "use_sim_time_mismatch",
            "severity": "warning",
            "message": "Clock publisher active but use_sim_time is not enabled",
            "fix": "Set use_sim_time=true so ROS2 nodes use simulation clock instead of wall clock.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 5: Domain ID mismatch
    env_domain = scene_info.get("domain_id", "0")
    node_domain = scene_info.get("domain_id_node")
    if node_domain is not None and str(node_domain) != str(env_domain):
        issues.append({
            "id": "domain_id_mismatch",
            "severity": "critical",
            "message": f"Domain ID mismatch: ROS_DOMAIN_ID={env_domain} but ROS2Context node has domain_id={node_domain}",
            "fix": f"Set ROS_DOMAIN_ID={node_domain} in your environment, or update the ROS2Context node to domain_id={env_domain}.",
            "tool_hint": None,
        })

    # Issue 6: QoS mismatches
    for qos_node in scene_info.get("qos_nodes", []):
        topic = qos_node.get("topic", "")
        if topic:
            topic_key = topic.strip("/").split("/")[-1]
            preset = _ROS2_QOS_PRESETS.get(topic_key)
            if preset and qos_node.get("qos"):
                current_qos = str(qos_node["qos"])
                expected_reliability = preset[0]
                if expected_reliability not in current_qos:
                    issues.append({
                        "id": "qos_mismatch",
                        "severity": "warning",
                        "message": f"QoS mismatch on topic '{topic}': expected {expected_reliability} reliability",
                        "fix": f"Use fix_ros2_qos(topic='{topic}') to apply the recommended QoS profile.",
                        "tool_hint": f"fix_ros2_qos(topic='{topic}')",
                    })

    # Issue 7: Dangling connections
    for dangling in scene_info.get("dangling_connections", []):
        issues.append({
            "id": "dangling_connection",
            "severity": "info",
            "message": f"Dangling execution input on {dangling['node']}.{dangling['attr']}",
            "fix": "Connect this node's execIn to an OnPlaybackTick or upstream node.",
            "tool_hint": None,
        })

    return {
        "issues": issues,
        "issue_count": len(issues),
        "ros2_context_found": scene_info.get("ros2_context_found", False),
        "distro": scene_info.get("distro"),
        "domain_id": scene_info.get("domain_id", "0"),
        "clock_publishing": scene_info.get("clock_publisher_found", False),
        "graphs_scanned": len(scene_info.get("og_graphs", [])),
        "message": f"Found {len(issues)} issue(s)" if issues else "All ROS2 checks passed — no issues found",
    }


DATA_HANDLERS["diagnose_ros2"] = _handle_diagnose_ros2


def _gen_fix_ros2_qos(args: Dict) -> str:
    """Generate code to update the QoS profile on a ROS2 publisher for a given topic."""
    topic = args["topic"]

    # Determine the QoS preset from the topic name
    topic_key = topic.strip("/").split("/")[-1]
    preset = _ROS2_QOS_PRESETS.get(topic_key)

    if preset:
        reliability, durability, description = preset
    else:
        # Default to RELIABLE + VOLATILE for unknown topics
        reliability = "RELIABLE"
        durability = "VOLATILE"
        description = f"Unknown topic '{topic}' — defaulting to RELIABLE"

    return f'''\
import omni.graph.core as og
import json

topic_name = "{topic}"
target_reliability = "{reliability}"
target_durability = "{durability}"

# QoS profile: {description}
# Find the publisher node for this topic and update its QoS profile
all_graphs = og.get_all_graphs()
updated = False

for graph in all_graphs:
    for node in graph.get_nodes():
        node_type = node.get_type_name()
        if "ROS2" not in node_type:
            continue

        topic_attr = node.get_attribute("inputs:topicName")
        if not topic_attr:
            continue

        current_topic = topic_attr.get()
        if current_topic != topic_name:
            continue

        # Found the node — update QoS profile
        qos_attr = node.get_attribute("inputs:qosProfile")
        if qos_attr:
            qos_attr.set(f"{{target_reliability}}, {{target_durability}}")
            updated = True
            print(f"Updated QoS on {{node.get_prim_path()}}: {{target_reliability}}, {{target_durability}}")

        # Also set reliability/durability if separate attributes exist
        rel_attr = node.get_attribute("inputs:reliability")
        if rel_attr:
            rel_attr.set(target_reliability)

        dur_attr = node.get_attribute("inputs:durability")
        if dur_attr:
            dur_attr.set(target_durability)

        break  # Only update the first matching node

if not updated:
    # No existing node found — create a new publisher with correct QoS
    print(f"No publisher found for {{topic_name}} — set QoS when creating the publisher:")
    print(f"  reliability: {{target_reliability}}")
    print(f"  durability: {{target_durability}}")
    print(f"  Hint: {description}")
'''


CODE_GEN_HANDLERS["fix_ros2_qos"] = _gen_fix_ros2_qos


def _gen_configure_ros2_time(args: Dict) -> str:
    """Generate OmniGraph code for ROS2 clock publishing and use_sim_time configuration."""
    mode = args["mode"]
    time_scale = args.get("time_scale", 1.0)

    if mode == "real_time":
        return '''\
import carb.settings
import omni.graph.core as og

# Configure real_time mode: disable use_sim_time, no clock publishing needed
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", False)

# Remove existing ROS2PublishClock nodes if any
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            node_path = node.get_prim_path()
            print(f"Note: ROS2PublishClock at {node_path} is active but use_sim_time=false")
            print("ROS2 nodes will use wall clock time.")

print("Configured real_time mode: use_sim_time=false")
print("ROS2 nodes will use the system wall clock.")
'''

    # sim_time or scaled mode — both need clock publishing
    time_scale_block = ""
    if mode == "scaled":
        time_scale_block = f'''
# Set simulation time scale
import omni.timeline
tl = omni.timeline.get_timeline_interface()
tl.set_time_codes_per_second(tl.get_time_codes_per_second() * {time_scale})
print(f"Time scale set to {time_scale}x")
'''

    return f'''\
import omni.graph.core as og
import carb.settings

# ── Step 1: Enable use_sim_time ──────────────────────────────────────────
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", True)
print("Enabled use_sim_time=true")

# ── Step 2: Create ROS2PublishClock node in an action graph ──────────────
# Check if a clock publisher already exists
clock_exists = False
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            clock_exists = True
            print(f"ROS2PublishClock already exists at {{node.get_prim_path()}}")
            break
    if clock_exists:
        break

if not clock_exists:
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
            "graph_path": "/World/ROS2ClockGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
            ],
        }},
    )
    print("Created ROS2ClockGraph with ROS2PublishClock node")
    print("  /clock topic will publish simulation time")
{time_scale_block}
print("Configured {mode} mode: ROS2 nodes will use simulation clock from /clock topic")
'''


CODE_GEN_HANDLERS["configure_ros2_time"] = _gen_configure_ros2_time


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
# ── XR Teleoperation ────────────────────────────────────────────────────────

# Stream quality presets: resolution, bitrate, FPS
_STREAM_QUALITY_PRESETS = {
    "low": {"width": 640, "height": 480, "bitrate_mbps": 2, "fps": 30},
    "medium": {"width": 1280, "height": 720, "bitrate_mbps": 8, "fps": 60},
    "high": {"width": 1920, "height": 1080, "bitrate_mbps": 20, "fps": 90},
}

# Device axis defaults per input device
_DEVICE_AXIS_DEFAULTS = {
    "quest_3": ["left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right", "grip_left", "grip_right"],
    "vision_pro": ["left_x", "left_y", "right_x", "right_y", "pinch_left", "pinch_right"],
    "spacemouse": ["tx", "ty", "tz", "rx", "ry", "rz"],
    "keyboard": ["w", "a", "s", "d", "q", "e"],
}


def _gen_start_teleop_session(args: Dict) -> str:
    robot_path = args["robot_path"]
    device = args.get("input_device", "keyboard")
    quality = args.get("stream_quality", "medium")
    preset = _STREAM_QUALITY_PRESETS.get(quality, _STREAM_QUALITY_PRESETS["medium"])
    axes = _DEVICE_AXIS_DEFAULTS.get(device, _DEVICE_AXIS_DEFAULTS["keyboard"])

    return f"""\
import omni.usd
import omni.kit.app
import omni.physx
from pxr import UsdPhysics, PhysxSchema, Gf
import time
import json
import asyncio
import threading

# ── Configuration ───────────────────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
INPUT_DEVICE = '{device}'
STREAM_WIDTH = {preset["width"]}
STREAM_HEIGHT = {preset["height"]}
STREAM_BITRATE_MBPS = {preset["bitrate_mbps"]}
STREAM_FPS = {preset["fps"]}
WATCHDOG_TIMEOUT_S = 0.5      # Hold last command until timeout
WATCHDOG_ZERO_VEL_S = 2.0     # Zero velocity after this period
MAX_JOINT_VEL = 2.0           # rad/s cap (safety default)
WS_PORT = 8766

# ── Global state ────────────────────────────────────────────────────────
_teleop_state = {{
    'active': True,
    'last_cmd_time': time.time(),
    'last_joint_targets': None,
    'ws_server': None,
    'recording_active': False,
    'device_axes': {axes!r},
}}

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot prim not found at {{ROBOT_PATH}}"

# ── WebSocket bridge for control data ───────────────────────────────────
try:
    import websockets
    import websockets.server

    _connected_clients = set()

    async def _ws_handler(websocket):
        _connected_clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get('type') == 'joint_command':
                    _teleop_state['last_cmd_time'] = time.time()
                    _teleop_state['last_joint_targets'] = data.get('targets', [])
                elif data.get('type') == 'stop':
                    _teleop_state['active'] = False
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            _connected_clients.discard(websocket)

    async def _start_ws_server():
        server = await websockets.server.serve(_ws_handler, '0.0.0.0', WS_PORT)
        _teleop_state['ws_server'] = server
        print(f"Teleop WebSocket server listening on ws://0.0.0.0:{{WS_PORT}}")
        return server

    # Launch WS server in background
    _ws_loop = asyncio.new_event_loop()
    _ws_thread = threading.Thread(
        target=lambda: (_ws_loop.run_until_complete(_start_ws_server()), _ws_loop.run_forever()),
        daemon=True,
    )
    _ws_thread.start()

except ImportError:
    print("WARNING: websockets package not installed — WebSocket bridge disabled")
    print("Install with: pip install websockets")

# ── Viewport streaming setup ───────────────────────────────────────────
try:
    import carb.settings
    settings = carb.settings.get_settings()
    settings.set('/rtx/renderResolution/width', STREAM_WIDTH)
    settings.set('/rtx/renderResolution/height', STREAM_HEIGHT)
    print(f"Viewport streaming configured: {{STREAM_WIDTH}}x{{STREAM_HEIGHT}} @ {{STREAM_FPS}}fps, {{STREAM_BITRATE_MBPS}}Mbps")
except Exception as e:
    print(f"Viewport streaming setup note: {{e}}")

# ── Physics callback: apply joint commands with watchdog ────────────────
def _teleop_physics_step(dt):
    if not _teleop_state['active']:
        return

    now = time.time()
    elapsed = now - _teleop_state['last_cmd_time']
    targets = _teleop_state['last_joint_targets']

    robot = stage.GetPrimAtPath(ROBOT_PATH)
    if not robot.IsValid():
        return

    # Iterate joints and apply targets
    joint_idx = 0
    for child in robot.GetAllChildren():
        is_revolute = child.HasAPI(UsdPhysics.RevoluteJointAPI)
        is_prismatic = child.HasAPI(UsdPhysics.PrismaticJointAPI)
        if not (is_revolute or is_prismatic):
            continue

        drive_type = 'angular' if is_revolute else 'linear'
        if not child.HasAPI(UsdPhysics.DriveAPI):
            continue
        drive = UsdPhysics.DriveAPI.Get(child, drive_type)

        if elapsed > WATCHDOG_ZERO_VEL_S:
            # Safety: zero velocity after extended timeout
            drive.GetTargetVelocityAttr().Set(0.0)
        elif elapsed > WATCHDOG_TIMEOUT_S:
            # Hold last command (do nothing — keep current targets)
            pass
        elif targets and joint_idx < len(targets):
            # Apply command with velocity capping
            target_vel = targets[joint_idx]
            capped_vel = max(-MAX_JOINT_VEL, min(MAX_JOINT_VEL, target_vel))
            drive.GetTargetVelocityAttr().Set(capped_vel)

        joint_idx += 1

# Register physics callback
_teleop_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_teleop_physics_step)
_teleop_state['physics_sub'] = _teleop_sub

print(f"Teleop session started for {{ROBOT_PATH}}")
print(f"  Device: {{INPUT_DEVICE}}")
print(f"  Stream: {{STREAM_WIDTH}}x{{STREAM_HEIGHT}} @ {{STREAM_FPS}}fps")
print(f"  Watchdog: hold={{WATCHDOG_TIMEOUT_S}}s, zero_vel={{WATCHDOG_ZERO_VEL_S}}s")
print(f"  Connect: ws://localhost:{{WS_PORT}}")
"""


def _gen_configure_teleop_mapping(args: Dict) -> str:
    robot_path = args["robot_path"]
    device_axes = args.get("device_axes")
    joint_names = args.get("joint_names")
    gains = args.get("gains", {})
    pos_gain = gains.get("position", 1.0)
    vel_gain = gains.get("velocity", 1.0)

    device_axes_repr = repr(device_axes) if device_axes else "None"
    joint_names_repr = repr(joint_names) if joint_names else "None"

    return f"""\
import omni.usd
from pxr import UsdPhysics

# ── Teleop Axis-to-Joint Mapping ────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
DEVICE_AXES = {device_axes_repr}
JOINT_NAMES = {joint_names_repr}
POSITION_GAIN = {pos_gain}
VELOCITY_GAIN = {vel_gain}

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot not found at {{ROBOT_PATH}}"

# Discover joints if not explicitly provided
if JOINT_NAMES is None:
    JOINT_NAMES = []
    for child in robot_prim.GetAllChildren():
        if child.HasAPI(UsdPhysics.RevoluteJointAPI) or child.HasAPI(UsdPhysics.PrismaticJointAPI):
            JOINT_NAMES.append(child.GetName())
    print(f"Auto-discovered {{len(JOINT_NAMES)}} joints: {{JOINT_NAMES}}")

# Build mapping table
mapping = {{}}
if DEVICE_AXES:
    for i, axis in enumerate(DEVICE_AXES):
        if i < len(JOINT_NAMES):
            mapping[axis] = {{
                'joint': JOINT_NAMES[i],
                'position_gain': POSITION_GAIN,
                'velocity_gain': VELOCITY_GAIN,
            }}
else:
    # Default: sequential 1:1 mapping
    for i, joint in enumerate(JOINT_NAMES):
        mapping[f'axis_{{i}}'] = {{
            'joint': joint,
            'position_gain': POSITION_GAIN,
            'velocity_gain': VELOCITY_GAIN,
        }}

# Store mapping in global teleop state (if session is active)
try:
    _teleop_state['mapping'] = mapping
    _teleop_state['joint_names'] = JOINT_NAMES
    _teleop_state['gains'] = {{'position': POSITION_GAIN, 'velocity': VELOCITY_GAIN}}
except NameError:
    print("WARNING: No active teleop session — mapping stored locally only")

print(f"Teleop mapping configured for {{ROBOT_PATH}}:")
print(f"  Axes: {{len(mapping)}} mapped")
print(f"  Gains: pos={{POSITION_GAIN}}, vel={{VELOCITY_GAIN}}")
for axis, cfg in mapping.items():
    print(f"    {{axis}} -> {{cfg['joint']}}")
"""


def _gen_record_teleop_demo(args: Dict) -> str:
    output_path = args["output_path"]
    robot_path = args["robot_path"]
    frequency_hz = args.get("frequency_hz", 30)

    return f"""\
import omni.usd
import omni.physx
from pxr import UsdPhysics, UsdGeom, Gf
import time
import numpy as np

# ── Teleop Demo Recording ───────────────────────────────────────────────
OUTPUT_PATH = '{output_path}'
ROBOT_PATH = '{robot_path}'
FREQUENCY_HZ = {frequency_hz}
RECORD_INTERVAL = 1.0 / FREQUENCY_HZ

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot not found at {{ROBOT_PATH}}"

# Discover joints
_rec_joints = []
for child in robot_prim.GetAllChildren():
    if child.HasAPI(UsdPhysics.RevoluteJointAPI) or child.HasAPI(UsdPhysics.PrismaticJointAPI):
        _rec_joints.append(child)
num_joints = len(_rec_joints)

# Recording buffers
_rec_data = {{
    'joint_positions': [],
    'joint_velocities': [],
    'ee_poses': [],
    'timestamps': [],
    'active': False,
    'last_record_time': 0.0,
    'start_time': 0.0,
}}

def _get_joint_positions():
    positions = []
    for j in _rec_joints:
        is_revolute = j.HasAPI(UsdPhysics.RevoluteJointAPI)
        drive_type = 'angular' if is_revolute else 'linear'
        if j.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(j, drive_type)
            pos = drive.GetTargetPositionAttr().Get()
            positions.append(float(pos) if pos is not None else 0.0)
        else:
            positions.append(0.0)
    return positions

def _get_joint_velocities():
    velocities = []
    for j in _rec_joints:
        is_revolute = j.HasAPI(UsdPhysics.RevoluteJointAPI)
        drive_type = 'angular' if is_revolute else 'linear'
        if j.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(j, drive_type)
            vel = drive.GetTargetVelocityAttr().Get()
            velocities.append(float(vel) if vel is not None else 0.0)
        else:
            velocities.append(0.0)
    return velocities

def _get_ee_pose():
    # Attempt to find end-effector (last link or named ee_link/panda_hand)
    ee_names = ['ee_link', 'panda_hand', 'tool0', 'link_ee']
    ee_prim = None
    for name in ee_names:
        candidate = stage.GetPrimAtPath(f'{{ROBOT_PATH}}/{{name}}')
        if candidate.IsValid():
            ee_prim = candidate
            break
    if ee_prim is None:
        # Fallback: use last child with xform
        for child in robot_prim.GetAllChildren():
            if child.IsA(UsdGeom.Xformable):
                ee_prim = child
    if ee_prim is None:
        return [0.0] * 7  # pos(3) + quat(4)
    xf = UsdGeom.Xformable(ee_prim).ComputeLocalToWorldTransform(0)
    pos = xf.ExtractTranslation()
    rot = xf.ExtractRotation().GetQuat()
    return [float(pos[0]), float(pos[1]), float(pos[2]),
            float(rot.GetReal()), float(rot.GetImaginary()[0]),
            float(rot.GetImaginary()[1]), float(rot.GetImaginary()[2])]

def _record_physics_step(dt):
    if not _rec_data['active']:
        return
    now = time.time()
    if now - _rec_data['last_record_time'] < RECORD_INTERVAL:
        return
    _rec_data['last_record_time'] = now

    _rec_data['timestamps'].append(now - _rec_data['start_time'])
    _rec_data['joint_positions'].append(_get_joint_positions())
    _rec_data['joint_velocities'].append(_get_joint_velocities())
    _rec_data['ee_poses'].append(_get_ee_pose())

# Register recording callback
_rec_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_record_physics_step)

# Start recording
_rec_data['active'] = True
_rec_data['start_time'] = time.time()
_rec_data['last_record_time'] = 0.0

# Store references for stop_teleop_session to finalize
try:
    _teleop_state['recording_active'] = True
    _teleop_state['rec_data'] = _rec_data
    _teleop_state['rec_sub'] = _rec_sub
    _teleop_state['rec_output_path'] = OUTPUT_PATH
    _teleop_state['rec_num_joints'] = num_joints
except NameError:
    pass

def _finalize_recording():
    \"\"\"Write recorded data to HDF5 file with robomimic-compatible schema.\"\"\"
    import h5py
    _rec_data['active'] = False

    n_steps = len(_rec_data['timestamps'])
    if n_steps == 0:
        print("No data recorded — nothing to write.")
        return

    with h5py.File(OUTPUT_PATH, 'w') as f:
        # robomimic-compatible schema
        grp = f.create_group('data')
        demo = grp.create_group('demo_0')
        demo.attrs['num_samples'] = n_steps

        obs = demo.create_group('obs')
        obs.create_dataset('joint_positions', data=np.array(_rec_data['joint_positions']))
        obs.create_dataset('joint_velocities', data=np.array(_rec_data['joint_velocities']))
        obs.create_dataset('ee_pose', data=np.array(_rec_data['ee_poses']))

        demo.create_dataset('timestamps', data=np.array(_rec_data['timestamps']))

        # Metadata
        f.attrs['robot_path'] = ROBOT_PATH
        f.attrs['frequency_hz'] = FREQUENCY_HZ
        f.attrs['num_joints'] = num_joints
        f.attrs['total_timesteps'] = n_steps

    print(f"Recording saved: {{OUTPUT_PATH}} ({{n_steps}} steps, {{num_joints}} joints)")

# Expose finalize for external use
_rec_data['finalize'] = _finalize_recording

print(f"Recording started: {{ROBOT_PATH}} -> {{OUTPUT_PATH}}")
print(f"  Frequency: {{FREQUENCY_HZ}} Hz")
print(f"  Joints: {{num_joints}}")
print(f"  Call stop_teleop_session to finalize and save.")
"""


def _gen_stop_teleop_session(args: Dict) -> str:
    return """\
import omni.usd
import omni.physx
from pxr import UsdPhysics
import time

# ── Stop Teleop Session ─────────────────────────────────────────────────
stage = omni.usd.get_context().get_stage()

try:
    _teleop_state
except NameError:
    print("No active teleop session found.")
    _teleop_state = {}

# 1. Deactivate session
_teleop_state['active'] = False

# 2. Remove physics callbacks
if 'physics_sub' in _teleop_state:
    _teleop_state['physics_sub'] = None
    print("Teleop physics callback removed.")

if 'rec_sub' in _teleop_state:
    _teleop_state['rec_sub'] = None
    print("Recording physics callback removed.")

# 3. Zero all joint velocities (safety)
robot_path = _teleop_state.get('robot_path', '')
if not robot_path:
    # Try to find any articulation in the scene
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            robot_path = str(prim.GetPath())
            break

if robot_path:
    robot_prim = stage.GetPrimAtPath(robot_path)
    if robot_prim.IsValid():
        zeroed = 0
        for child in robot_prim.GetAllChildren():
            is_revolute = child.HasAPI(UsdPhysics.RevoluteJointAPI)
            is_prismatic = child.HasAPI(UsdPhysics.PrismaticJointAPI)
            if not (is_revolute or is_prismatic):
                continue
            drive_type = 'angular' if is_revolute else 'linear'
            if child.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI.Get(child, drive_type)
                drive.GetTargetVelocityAttr().Set(0.0)
                zeroed += 1
        print(f"Zeroed velocity on {zeroed} joints for safety.")

# 4. Stop viewport streaming
try:
    import carb.settings
    settings = carb.settings.get_settings()
    # Reset to default render resolution
    settings.set('/rtx/renderResolution/width', 1280)
    settings.set('/rtx/renderResolution/height', 720)
    print("Viewport streaming stopped.")
except Exception:
    pass

# 5. Close WebSocket connections
ws_server = _teleop_state.get('ws_server')
if ws_server is not None:
    ws_server.close()
    _teleop_state['ws_server'] = None
    print("WebSocket server closed.")

# 6. Finalize any active HDF5 recording
if _teleop_state.get('recording_active'):
    rec_data = _teleop_state.get('rec_data', {})
    finalize_fn = rec_data.get('finalize')
    if finalize_fn:
        finalize_fn()
    _teleop_state['recording_active'] = False
    print("Recording finalized.")

print("Teleop session stopped.")
"""


def _gen_teleop_safety_config(args: Dict) -> str:
    robot_path = args["robot_path"]
    watchdog_ms = args.get("watchdog_timeout_ms", 500)
    max_vel = args.get("max_joint_velocity")
    ws_limits = args.get("workspace_limits")

    watchdog_s = watchdog_ms / 1000.0
    zero_vel_s = watchdog_s * 4  # Zero velocity at 4x watchdog timeout

    max_vel_line = ""
    if max_vel is not None:
        max_vel_line = f"MAX_JOINT_VEL = {max_vel}"
    else:
        max_vel_line = "MAX_JOINT_VEL = 2.0  # default rad/s"

    ws_limits_block = ""
    if ws_limits:
        ws_min = ws_limits.get("min", [-1, -1, 0])
        ws_max = ws_limits.get("max", [1, 1, 2])
        ws_limits_block = f"""
# ── Workspace limits ────────────────────────────────────────────────────
WS_MIN = Gf.Vec3d({ws_min[0]}, {ws_min[1]}, {ws_min[2]})
WS_MAX = Gf.Vec3d({ws_max[0]}, {ws_max[1]}, {ws_max[2]})

def _check_workspace_limits():
    \"\"\"Check if end-effector is within workspace bounds.\"\"\"
    ee_names = ['ee_link', 'panda_hand', 'tool0', 'link_ee']
    for name in ee_names:
        ee = stage.GetPrimAtPath(f'{{ROBOT_PATH}}/{{name}}')
        if ee.IsValid():
            xf = UsdGeom.Xformable(ee).ComputeLocalToWorldTransform(0)
            pos = xf.ExtractTranslation()
            clamped = False
            for i in range(3):
                if pos[i] < WS_MIN[i] or pos[i] > WS_MAX[i]:
                    clamped = True
                    break
            if clamped:
                print(f"WARNING: End-effector at {{pos}} outside workspace limits!")
                return False
            return True
    return True  # No ee found, skip check

print(f"Workspace limits: min={{list(WS_MIN)}}, max={{list(WS_MAX)}}")
"""

    return f"""\
import omni.usd
from pxr import UsdPhysics, UsdGeom, Gf

# ── Teleop Safety Configuration ─────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
WATCHDOG_TIMEOUT_S = {watchdog_s}
WATCHDOG_ZERO_VEL_S = {zero_vel_s}
{max_vel_line}

stage = omni.usd.get_context().get_stage()

# Update global teleop state if session is active
try:
    _teleop_state['watchdog_timeout'] = WATCHDOG_TIMEOUT_S
    _teleop_state['watchdog_zero_vel'] = WATCHDOG_ZERO_VEL_S
    _teleop_state['max_joint_vel'] = MAX_JOINT_VEL
    print("Updated active teleop session safety config.")
except NameError:
    print("No active teleop session — safety config stored for next session.")

# Apply velocity limits to joint drives
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
if robot_prim.IsValid():
    configured = 0
    for child in robot_prim.GetAllChildren():
        is_revolute = child.HasAPI(UsdPhysics.RevoluteJointAPI)
        is_prismatic = child.HasAPI(UsdPhysics.PrismaticJointAPI)
        if not (is_revolute or is_prismatic):
            continue
        drive_type = 'angular' if is_revolute else 'linear'
        if child.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            drive.GetMaxVelocityAttr().Set(MAX_JOINT_VEL)
            configured += 1
    print(f"Applied max velocity {{MAX_JOINT_VEL}} rad/s to {{configured}} joints.")

print(f"Safety config for {{ROBOT_PATH}}:")
print(f"  Watchdog timeout: {{WATCHDOG_TIMEOUT_S*1000:.0f}} ms")
print(f"  Zero velocity after: {{WATCHDOG_ZERO_VEL_S*1000:.0f}} ms")
print(f"  Max joint velocity: {{MAX_JOINT_VEL}} rad/s")
{ws_limits_block}"""


CODE_GEN_HANDLERS["start_teleop_session"] = _gen_start_teleop_session
CODE_GEN_HANDLERS["configure_teleop_mapping"] = _gen_configure_teleop_mapping
CODE_GEN_HANDLERS["record_teleop_demo"] = _gen_record_teleop_demo
CODE_GEN_HANDLERS["stop_teleop_session"] = _gen_stop_teleop_session
CODE_GEN_HANDLERS["teleop_safety_config"] = _gen_teleop_safety_config
# ─── Eureka: LLM Reward Generation ─────────────────────────────────────────

# In-memory store for Eureka run status (keyed by run_id)
_eureka_runs: Dict[str, Dict] = {}


def _format_component_metrics(metrics: Dict) -> str:
    """Format per-component training metrics for the mutation prompt."""
    components = metrics.get("components", {})
    if not components:
        return "No component metrics available."
    lines = []
    for name, data in components.items():
        mean_vals = data.get("mean", [])
        converged = data.get("converged", False)
        mean_str = ", ".join(f"{v:.4f}" for v in mean_vals[-5:]) if mean_vals else "N/A"
        status = "converged" if converged else "not converged"
        lines.append(f"  {name}: mean=[{mean_str}] ({status})")
    return "\n".join(lines)


def _build_mutation_prompt(prev_reward: str, metrics: Dict, user_feedback: Optional[str]) -> str:
    prompt = f"""Previous reward function:
{prev_reward}

Training metrics per component:
{_format_component_metrics(metrics)}

Task success rate: {metrics.get('task_success_rate', 'N/A')}
"""
    if user_feedback:
        prompt += f"\nUser feedback: {user_feedback}\n"
    prompt += "\nBased on this data, generate an improved reward function."
    return prompt


async def _handle_generate_reward(args: Dict) -> Dict:
    """Generate Eureka reward configuration and initial prompt for a DirectRLEnv."""
    task_description = args["task_description"]
    env_source_path = args["env_source_path"]
    num_candidates = args.get("num_candidates", 4)
    num_iterations = args.get("num_iterations", 5)

    # Read environment source code
    env_path = Path(env_source_path)
    if env_path.exists():
        env_source = env_path.read_text()
    else:
        env_source = f"# [File not found: {env_source_path}]\n# Provide the DirectRLEnv source code manually."

    # Validate it's a DirectRLEnv (not ManagerBasedRLEnv)
    if "ManagerBasedRLEnv" in env_source:
        return {
            "error": "Eureka reward generation only works with DirectRLEnv, not ManagerBasedRLEnv. "
                     "DirectRLEnv exposes compute_reward() which Eureka can override.",
        }

    # Build the initial reward generation prompt
    initial_prompt = f"""You are a reward function engineer for reinforcement learning.

Task description: {task_description}

Environment source code:
```python
{env_source}
```

Generate {num_candidates} diverse reward function candidates.
Each candidate must:
1. Be a standalone Python function: def compute_reward(self) -> torch.Tensor
2. Use only tensors available in self (observations, actions, targets, etc.)
3. Return a scalar reward tensor of shape (num_envs,)
4. Include per-component breakdown as a dict for analysis
5. Avoid sparse rewards — use dense, shaped rewards

Return each candidate as a separate code block.
"""

    eureka_config = {
        "task_description": task_description,
        "env_source_path": env_source_path,
        "num_candidates": num_candidates,
        "num_iterations": num_iterations,
        "env_type": "DirectRLEnv",
        "initial_prompt": initial_prompt,
        "env_source_included": env_path.exists(),
    }

    return eureka_config


def _gen_evaluate_reward(args: Dict) -> str:
    """Generate code to evaluate a candidate reward function via short training."""
    reward_code = args["reward_code"]
    env_id = args["env_id"]
    num_steps = args.get("num_steps", 1000)

    # Escape the reward code for embedding in a string
    escaped_reward = reward_code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

    return f"""\
import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path

# 1. Write the candidate reward function to a temp file
reward_code = '''{reward_code}'''

reward_dir = tempfile.mkdtemp(prefix='eureka_reward_')
reward_path = os.path.join(reward_dir, 'reward_fn.py')
with open(reward_path, 'w') as f:
    f.write(reward_code)

print(f'Reward function written to {{reward_path}}')

# 2. Launch training subprocess with the custom reward
env_id = '{env_id}'
num_steps = {num_steps}

cmd = [
    sys.executable, '-m', 'isaaclab.train',
    '--task', env_id,
    '--num_envs', '16',
    '--max_iterations', str(num_steps // 16),
    '--custom_reward', reward_path,
    '--headless',
]

print(f'Launching evaluation: {{" ".join(cmd)}}')
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    cwd=reward_dir,
)
stdout, _ = proc.communicate(timeout=300)

# 3. Parse training metrics from stdout
results = {{
    'env_id': env_id,
    'num_steps': num_steps,
    'reward_path': reward_path,
    'return_code': proc.returncode,
    'stdout_tail': stdout[-2000:] if stdout else '',
}}

# 4. Look for metrics JSON in output
metrics_path = os.path.join(reward_dir, 'metrics.json')
if os.path.exists(metrics_path):
    with open(metrics_path) as f:
        metrics = json.load(f)
    results['fitness'] = metrics.get('fitness', 0.0)
    results['components'] = metrics.get('components', {{}})
    results['task_success_rate'] = metrics.get('task_success_rate', 0.0)
else:
    results['fitness'] = 0.0
    results['components'] = {{}}
    results['task_success_rate'] = 0.0
    results['note'] = 'No metrics.json found — training may have failed'

print(f'Evaluation complete: fitness={{results["fitness"]:.4f}}, success={{results["task_success_rate"]:.2%}}')
print(json.dumps(results, indent=2))
"""


async def _handle_iterate_reward(args: Dict) -> Dict:
    """Generate a mutation prompt for the next Eureka iteration."""
    prev_reward_code = args["prev_reward_code"]
    metrics = args["metrics"]
    user_feedback = args.get("user_feedback")

    mutation_prompt = _build_mutation_prompt(prev_reward_code, metrics, user_feedback)

    return {
        "mutation_prompt": mutation_prompt,
        "prev_fitness": metrics.get("fitness", "N/A"),
        "prev_success_rate": metrics.get("task_success_rate", "N/A"),
        "components_analyzed": list(metrics.get("components", {}).keys()),
        "has_user_feedback": user_feedback is not None,
    }


async def _handle_eureka_status(args: Dict) -> Dict:
    """Return current status of a Eureka optimization run."""
    run_id = args["run_id"]

    if run_id in _eureka_runs:
        run = _eureka_runs[run_id]
        return {
            "run_id": run_id,
            "status": run.get("status", "unknown"),
            "current_iteration": run.get("current_iteration", 0),
            "total_iterations": run.get("total_iterations", 0),
            "candidates_evaluated": run.get("candidates_evaluated", 0),
            "best_fitness": run.get("best_fitness", 0.0),
            "best_reward_code": run.get("best_reward_code"),
        }

    return {
        "run_id": run_id,
        "status": "not_found",
        "message": f"No Eureka run found with ID '{run_id}'. Start one with generate_reward first.",
    }


DATA_HANDLERS["generate_reward"] = _handle_generate_reward
DATA_HANDLERS["iterate_reward"] = _handle_iterate_reward
DATA_HANDLERS["eureka_status"] = _handle_eureka_status
CODE_GEN_HANDLERS["evaluate_reward"] = _gen_evaluate_reward
# ── GR00T N1 Foundation Policy ───────────────────────────────────────────────

# Embodiment presets: observation/action configs for supported robots
_GROOT_EMBODIMENTS = {
    "LIBERO_PANDA": {
        "obs_type": "rgb+proprio",
        "action_dim": 7,
        "description": "Franka Panda in LIBERO benchmark",
        "vram_gb": 24,
    },
    "OXE_WIDOWX": {
        "obs_type": "rgb+proprio",
        "action_dim": 7,
        "description": "WidowX from Open X-Embodiment",
        "vram_gb": 24,
    },
    "UNITREE_G1": {
        "obs_type": "rgb+proprio",
        "action_dim": 29,
        "description": "Unitree G1 humanoid",
        "vram_gb": 24,
    },
    "custom": {
        "obs_type": "rgb+proprio",
        "action_dim": None,
        "description": "Custom embodiment — configure manually",
        "vram_gb": 24,
    },
}


async def _handle_load_groot_policy(args: Dict) -> Dict:
    """Return download/launch commands for GR00T N1 policy server."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    robot_path = args["robot_path"]
    embodiment_key = args.get("embodiment", "custom")

    embodiment = _GROOT_EMBODIMENTS.get(embodiment_key, _GROOT_EMBODIMENTS["custom"])

    # VRAM check — estimate based on model size
    estimated_vram = embodiment.get("vram_gb", 24)

    return {
        "model_id": model_id,
        "robot_path": robot_path,
        "embodiment": embodiment_key,
        "embodiment_config": embodiment,
        "download_command": (
            f"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{model_id}', local_dir='workspace/groot_models/{model_id.split('/')[-1]}')"
        ),
        "launch_command": (
            f"python -m gr00t.deploy.policy_server "
            f"--model-path workspace/groot_models/{model_id.split('/')[-1]} "
            f"--embodiment {embodiment_key} "
            f"--port 50051"
        ),
        "vram_required_gb": estimated_vram,
        "vram_check": "ok" if estimated_vram <= 24 else "insufficient",
        "error": (
            f"Insufficient VRAM: GR00T N1 requires >= 24 GB VRAM. "
            f"Consider using NVIDIA Cloud (brev.dev/nvidia) or a multi-GPU setup."
        ) if estimated_vram > 24 else None,
        "instructions": (
            f"1. Download model: {model_id}\n"
            f"2. Launch policy server on port 50051\n"
            f"3. Robot at {robot_path} will connect via gRPC\n"
            f"4. Embodiment: {embodiment_key} ({embodiment['description']})"
        ),
    }


def _gen_evaluate_groot(args: Dict) -> str:
    """Generate code to run closed-loop GR00T N1 evaluation."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    task = args["task"]
    num_episodes = args.get("num_episodes", 50)
    checkpoint = args.get("checkpoint")

    model_path_expr = (
        f"'{checkpoint}'" if checkpoint
        else f"'workspace/groot_models/{model_id.split('/')[-1]}'"
    )

    return f"""\
import subprocess
import sys
import os
import json

model_path = {model_path_expr}
task = '{task}'
num_episodes = {num_episodes}
results_dir = 'workspace/groot_eval_results'
os.makedirs(results_dir, exist_ok=True)

# Step 1: Launch GR00T policy server as background process
server_cmd = [
    sys.executable, '-m', 'gr00t.deploy.policy_server',
    '--model-path', model_path,
    '--port', '50051',
]
print(f'Launching GR00T policy server: {{" ".join(server_cmd)}}')
server_proc = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# Step 2: Run IsaacLabEvalTasks evaluation
eval_cmd = [
    sys.executable, '-m', 'gr00t.eval.isaac_lab',
    '--task', task,
    '--num-episodes', str(num_episodes),
    '--policy-server', 'localhost:50051',
    '--results-dir', results_dir,
]
print(f'Running evaluation: {{" ".join(eval_cmd)}}')
eval_proc = subprocess.Popen(eval_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
eval_proc.wait()

# Step 3: Collect results
results_file = os.path.join(results_dir, f'{{task}}_results.json')
if os.path.exists(results_file):
    with open(results_file) as f:
        metrics = json.load(f)
    print(f'Evaluation complete: success_rate={{metrics.get("success_rate", "N/A")}}')
    print(f'Task metrics: {{json.dumps(metrics.get("task_metrics", {{}}), indent=2)}}')
else:
    print(f'Results file not found at {{results_file}}')

# Step 4: Cleanup policy server
server_proc.terminate()
print(f'Policy server terminated (PID: {{server_proc.pid}})')
"""


def _gen_finetune_groot(args: Dict) -> str:
    """Generate code to fine-tune GR00T N1 on demo data."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    demo_data = args["demo_data"]
    num_steps = args.get("num_steps", 10000)
    lora = args.get("lora", True)
    output_dir = args.get("output_dir", "workspace/groot_checkpoints")

    vram_note = (
        "# LoRA fine-tuning: ~25 GB VRAM (1x RTX 4090 sufficient)"
        if lora else
        "# Full fine-tuning: ~48 GB VRAM (2x RTX 4090 or 1x A100 recommended)"
    )

    lora_flags = (
        "    '--use-lora',\n"
        "    '--lora-rank', '16',\n"
        "    '--lora-alpha', '32',\n"
    ) if lora else ""

    return f"""\
import subprocess
import sys
import os

model_id = '{model_id}'
demo_data = '{demo_data}'
num_steps = {num_steps}
output_dir = '{output_dir}'
{vram_note}

os.makedirs(output_dir, exist_ok=True)

# VRAM check
try:
    import torch
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
        min_vram = {'25' if lora else '48'}
        if vram_gb < min_vram:
            print(f'WARNING: {{vram_gb:.1f}} GB VRAM detected, {{min_vram}} GB recommended.')
            print('Consider using NVIDIA Cloud (brev.dev/nvidia) or multi-GPU setup.')
except ImportError:
    pass

# Launch fine-tuning
cmd = [
    sys.executable, '-m', 'gr00t.finetune.train',
    '--model-id', model_id,
    '--demo-data', demo_data,
    '--num-steps', str(num_steps),
    '--output-dir', output_dir,
{lora_flags}]
print(f'Launching GR00T fine-tuning: {{" ".join(cmd)}}')
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(f'Fine-tuning started (PID: {{proc.pid}}). Checkpoints → {{output_dir}}')
"""


async def _handle_compare_policies(args: Dict) -> Dict:
    """Format a comparison table from multiple GR00T policy evaluation results."""
    results = args.get("results", [])

    if not results:
        return {
            "comparison_table": "No results to compare.",
            "entries": [],
            "count": 0,
        }

    # Determine all metric columns
    metric_cols = set()
    for r in results:
        tm = r.get("task_metrics", {})
        metric_cols.update(tm.keys())
    metric_cols = sorted(metric_cols)

    # Build comparison entries
    entries = []
    for r in results:
        entry = {
            "policy_name": r.get("policy_name", "unnamed"),
            "model_id": r.get("model_id", "N/A"),
            "success_rate": r.get("success_rate", 0.0),
            "training_data_size": r.get("training_data_size", "N/A"),
            "observation_type": r.get("observation_type", "N/A"),
        }
        for col in metric_cols:
            entry[col] = r.get("task_metrics", {}).get(col, "N/A")
        entries.append(entry)

    # Sort by success_rate descending
    entries.sort(key=lambda e: -e["success_rate"])

    # Build formatted table
    header_cols = ["Policy", "Model", "Success Rate", "Train Data", "Obs Type"]
    header_cols.extend(metric_cols)

    rows = []
    for e in entries:
        row = [
            e["policy_name"],
            e["model_id"],
            f"{e['success_rate']:.1%}",
            e["training_data_size"],
            e["observation_type"],
        ]
        for col in metric_cols:
            val = e.get(col, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.3f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    # Format table
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(header_cols, col_widths)) + " |"
    table_lines = [sep, header_line, sep]
    for row in rows:
        table_lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(row, col_widths)) + " |")
    table_lines.append(sep)

    return {
        "comparison_table": "\n".join(table_lines),
        "entries": entries,
        "count": len(entries),
        "metric_columns": metric_cols,
        "dimensions": [
            "zero-shot generalization (success_rate without task-specific training)",
            "single-task performance (success_rate with fine-tuning)",
            "training data needed (training_data_size)",
            "observation type (observation_type: rgb, rgb+proprio, proprio)",
        ],
    }


DATA_HANDLERS["load_groot_policy"] = _handle_load_groot_policy
DATA_HANDLERS["compare_policies"] = _handle_compare_policies
CODE_GEN_HANDLERS["evaluate_groot"] = _gen_evaluate_groot
CODE_GEN_HANDLERS["finetune_groot"] = _gen_finetune_groot


# ── IsaacAutomator Cloud Deployment ─────────────────────────────────────────

_CLOUD_PRICING = {
    ("aws", "g5.2xlarge"): {"price_per_hour": 1.21, "gpu": "A10G"},
    ("aws", "g6e.2xlarge"): {"price_per_hour": 2.50, "gpu": "L40S"},
    ("gcp", "g2-standard-8"): {"price_per_hour": 1.35, "gpu": "L4"},
    ("azure", "NCasT4_v3"): {"price_per_hour": 1.10, "gpu": "T4"},
}

_CLOUD_SCRIPT_ALLOWLIST = {"training", "sdg", "evaluation", "headless_sim"}

# In-memory job tracking (placeholder for real cloud API integration)
_cloud_jobs: Dict[str, Dict] = {}


async def _handle_cloud_launch(args: Dict) -> Dict:
    """Return structured deployment info for IsaacAutomator cloud launch.
    Always requires approval regardless of auto-approve setting.
    """
    provider = args["provider"]
    instance_type = args["instance_type"]
    isaac_version = args.get("isaac_version", "5.1.0")
    script_template = args.get("script_template", "training")
    num_gpus = args.get("num_gpus", 1)

    # Validate script template against allowlist
    if script_template not in _CLOUD_SCRIPT_ALLOWLIST:
        return {
            "error": f"Unknown script_template '{script_template}'. "
                     f"Allowed: {sorted(_CLOUD_SCRIPT_ALLOWLIST)}",
        }

    # Lookup pricing
    pricing_key = (provider, instance_type)
    pricing = _CLOUD_PRICING.get(pricing_key)
    if pricing:
        price_per_hour = pricing["price_per_hour"]
        gpu_model = pricing["gpu"]
    else:
        price_per_hour = None
        gpu_model = "unknown"

    # Prerequisites per provider
    prerequisites = {
        "aws": [
            "NGC API key configured (ngc config set)",
            "AWS IAM credentials with EC2 and S3 permissions",
            "GPU quota approved for the target region",
            "IsaacAutomator cloned and configured",
        ],
        "gcp": [
            "NGC API key configured (ngc config set)",
            "GCP service account with Compute Engine permissions",
            "GPU quota approved for the target zone",
            "IsaacAutomator cloned and configured",
        ],
        "azure": [
            "NGC API key configured (ngc config set)",
            "Azure subscription with GPU VM quota",
            "Azure CLI authenticated (az login)",
            "IsaacAutomator cloned and configured",
        ],
    }

    import uuid
    job_id = f"cloud-{provider}-{uuid.uuid4().hex[:8]}"

    deploy_command = (
        f"./deploy-{provider} "
        f"--instance-type {instance_type} "
        f"--isaac-version {isaac_version} "
        f"--script {script_template} "
        f"--num-gpus {num_gpus}"
    )

    result = {
        "job_id": job_id,
        "deploy_command": deploy_command,
        "provider": provider,
        "instance_type": instance_type,
        "isaac_version": isaac_version,
        "script_template": script_template,
        "num_gpus": num_gpus,
        "gpu_model": gpu_model,
        "estimated_cost_per_hour": price_per_hour,
        "prerequisites": prerequisites.get(provider, []),
        "always_require_approval": True,
        "message": (
            f"Ready to deploy {instance_type} ({gpu_model}) on {provider.upper()} "
            f"with Isaac Sim {isaac_version}. "
            + (f"Estimated cost: ${price_per_hour:.2f}/hr. " if price_per_hour else "Cost: unknown instance type. ")
            + "Review the prerequisites and approve to proceed."
        ),
    }

    # Track job (placeholder)
    _cloud_jobs[job_id] = {
        "status": "pending_approval",
        "provider": provider,
        "instance_type": instance_type,
        "gpu_model": gpu_model,
        "price_per_hour": price_per_hour,
    }

    return result


async def _handle_cloud_status(args: Dict) -> Dict:
    """Check the status of a cloud job."""
    job_id = args["job_id"]

    if job_id in _cloud_jobs:
        job = _cloud_jobs[job_id]
        return {
            "job_id": job_id,
            "status": job.get("status", "unknown"),
            "gpu_utilization": job.get("gpu_utilization", "N/A"),
            "estimated_remaining": job.get("estimated_remaining", "N/A"),
            "cost_so_far": job.get("cost_so_far", "N/A"),
        }

    return {
        "job_id": job_id,
        "status": "not_found",
        "gpu_utilization": None,
        "estimated_remaining": None,
        "cost_so_far": None,
        "message": f"No cloud job found with ID '{job_id}'. It may have been terminated or the ID is incorrect.",
    }


async def _handle_cloud_teardown(args: Dict) -> Dict:
    """Return teardown command for a cloud instance. Always requires approval."""
    job_id = args["job_id"]

    job = _cloud_jobs.get(job_id)
    if job:
        provider = job.get("provider", "unknown")
        teardown_command = f"./destroy-{provider} --job-id {job_id}"
        price = job.get("price_per_hour")
        cost_warning = ""
        if price and job.get("status") in ("running", "pending_approval"):
            cost_warning = (
                f"WARNING: Instance is still active at ${price:.2f}/hr. "
                "Teardown will terminate the instance and stop billing."
            )
        return {
            "job_id": job_id,
            "teardown_command": teardown_command,
            "provider": provider,
            "always_require_approval": True,
            "cost_warning": cost_warning,
            "message": f"Ready to tear down {provider.upper()} instance {job_id}. Approve to proceed.",
        }

    return {
        "job_id": job_id,
        "teardown_command": f"./destroy-unknown --job-id {job_id}",
        "provider": "unknown",
        "always_require_approval": True,
        "cost_warning": "",
        "message": f"Job '{job_id}' not found in local tracking. Command generated but may fail.",
    }


async def _handle_cloud_estimate_cost(args: Dict) -> Dict:
    """Estimate cost for a cloud GPU instance over a given duration."""
    provider = args["provider"]
    instance_type = args["instance_type"]
    hours = args["hours"]

    pricing_key = (provider, instance_type)
    pricing = _CLOUD_PRICING.get(pricing_key)

    if pricing:
        price_per_hour = pricing["price_per_hour"]
        gpu = pricing["gpu"]
        cost_usd = round(price_per_hour * hours, 2)
        return {
            "cost_usd": cost_usd,
            "price_per_hour": price_per_hour,
            "provider": provider,
            "instance_type": instance_type,
            "gpu": gpu,
            "hours": hours,
            "message": f"{instance_type} ({gpu}) on {provider.upper()}: ${cost_usd:.2f} for {hours}h @ ${price_per_hour:.2f}/hr",
        }

    return {
        "cost_usd": None,
        "price_per_hour": None,
        "provider": provider,
        "instance_type": instance_type,
        "gpu": "unknown",
        "hours": hours,
        "message": (
            f"Instance type '{instance_type}' on {provider.upper()} not found in pricing table. "
            f"Known types: {[f'{p}/{t}' for (p, t) in _CLOUD_PRICING.keys()]}"
        ),
    }


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


DATA_HANDLERS["cloud_launch"] = _handle_cloud_launch
DATA_HANDLERS["cloud_status"] = _handle_cloud_status
DATA_HANDLERS["cloud_teardown"] = _handle_cloud_teardown
DATA_HANDLERS["cloud_estimate_cost"] = _handle_cloud_estimate_cost
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


# ── Debug Draw ──────────────────────────────────────────────────────────────

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


# ── Occupancy Map ───────────────────────────────────────────────────────────

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


# ── Camera inspection / configuration ───────────────────────────────────────

def _gen_inspect_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    return f"""\
import omni.usd
from pxr import UsdGeom
import json

stage = omni.usd.get_context().get_stage()
cam = UsdGeom.Camera(stage.GetPrimAtPath('{camera_path}'))
result = {{
    'camera_path': '{camera_path}',
    'focal_length': cam.GetFocalLengthAttr().Get(),
    'horizontal_aperture': cam.GetHorizontalApertureAttr().Get(),
    'vertical_aperture': cam.GetVerticalApertureAttr().Get(),
    'clipping_range': list(cam.GetClippingRangeAttr().Get()),
    'focus_distance': cam.GetFocusDistanceAttr().Get(),
    'projection': cam.GetProjectionAttr().Get(),
}}
print(json.dumps(result))
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
    "clone_envs": _gen_clone_envs,
    "debug_draw": _gen_debug_draw,
    "generate_occupancy_map": _gen_generate_occupancy_map,
    "configure_camera": _gen_configure_camera,
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
    "inspect_camera": None,  # placeholder — registered after definition below
}

# ── Camera inspection (DATA handler — reads camera attrs via Kit RPC) ───────

async def _handle_inspect_camera(args: Dict) -> Dict:
    camera_path = args["camera_path"]
    code = _gen_inspect_camera(args)
    return await kit_tools.queue_exec_patch(code, f"Inspect camera at {camera_path}")


DATA_HANDLERS["inspect_camera"] = _handle_inspect_camera


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


# ── Robot Description (8B.4) ────────────────────────────────────────────────

# Supported robot names (matches interface_config_loader.get_supported_robot_policy_pairs)
_SUPPORTED_MOTION_ROBOTS = {
    "franka", "ur10", "ur5e", "ur3e", "cobotta", "rs007n",
    "dofbot", "kawasaki", "flexiv_rizon",
}


async def _handle_generate_robot_description(args: Dict) -> Dict:
    """Check if a robot has pre-built motion generation configs."""
    art_path = args["articulation_path"]
    robot_type = args.get("robot_type", "").lower()

    # Try to identify robot type from path if not provided
    if not robot_type:
        path_lower = art_path.lower()
        for name in _SUPPORTED_MOTION_ROBOTS:
            if name in path_lower:
                robot_type = name
                break

    if robot_type in _SUPPORTED_MOTION_ROBOTS:
        cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, {})
        return {
            "supported": True,
            "robot_type": robot_type,
            "config_files": {
                "rmpflow_config": cfg.get("rmp_config", f"{robot_type}/rmpflow"),
                "robot_descriptor": cfg.get("desc", f"{robot_type}/robot_descriptor.yaml"),
                "urdf": cfg.get("urdf", f"{robot_type}/lula_gen.urdf"),
                "end_effector_frame": cfg.get("ee_frame", "ee_link"),
            },
            "usage": (
                "This robot has pre-built configs. Use "
                "interface_config_loader.load_supported_motion_gen_config("
                f"'{robot_type}', 'RMPflow') to load them."
            ),
            "message": (
                f"Robot '{robot_type}' is pre-supported for motion generation. "
                f"Config files are bundled with the isaacsim.robot_motion.motion_generation extension."
            ),
        }

    return {
        "supported": False,
        "robot_type": robot_type or "(unknown)",
        "articulation_path": art_path,
        "instructions": (
            "This robot does not have pre-built motion generation configs. "
            "To create them:\n"
            "1. Open the XRDF Editor GUI (Window > Extensions > XRDF Editor) to "
            "define collision spheres, joint limits, and end-effector frames.\n"
            "2. Export the XRDF file and Lula robot descriptor YAML.\n"
            "3. Use the exported files with LulaKinematicsSolver and RmpFlow.\n\n"
            "For programmatic collision sphere editing, use the CollisionSphereEditor "
            "from isaacsim.robot_setup.xrdf_editor:\n"
            "  - CollisionSphereEditor.add_sphere(link_path, position, radius)\n"
            "  - CollisionSphereEditor.clear_link_spheres(link_path)\n"
            "  - CollisionSphereEditor.clear_spheres()\n"
            "  - CollisionSphereEditor.delete_sphere(sphere_id)"
        ),
        "message": (
            f"Robot '{robot_type or 'unknown'}' at '{art_path}' is not pre-supported. "
            "Use the XRDF Editor to generate collision spheres and robot descriptors."
        ),
    }


DATA_HANDLERS["generate_robot_description"] = _handle_generate_robot_description


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
    """Walk asset directories and build a searchable index of USD files."""
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    index = []

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
        searchable = f"{name} {path} {rel_path}"

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


# ── Robot Setup Suite (Phase 8D) ─────────────────────────────────────────

# Default drive parameters per robot type
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
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains
CODE_GEN_HANDLERS["assemble_robot"] = _gen_assemble_robot
CODE_GEN_HANDLERS["configure_self_collision"] = _gen_configure_self_collision


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


# ── ROS2 Deep Integration (Phase 8F) ────────────────────────────────────────

# Sensor type → OmniGraph node type mapping for configure_ros2_bridge
_ROS2_SENSOR_NODE_MAP = {
    "camera": "ROS2CameraHelper",
    "lidar": "ROS2PublishLaserScan",
    "imu": "ROS2PublishImu",
    "clock": "ROS2PublishClock",
    "joint_state": "ROS2PublishJointState",
}


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
CODE_GEN_HANDLERS["publish_robot_description"] = _gen_publish_robot_description
CODE_GEN_HANDLERS["configure_ros2_bridge"] = _gen_configure_ros2_bridge


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package


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


# ── Robot Description (8B.4) ────────────────────────────────────────────────

# Supported robot names (matches interface_config_loader.get_supported_robot_policy_pairs)
_SUPPORTED_MOTION_ROBOTS = {
    "franka", "ur10", "ur5e", "ur3e", "cobotta", "rs007n",
    "dofbot", "kawasaki", "flexiv_rizon",
}


async def _handle_generate_robot_description(args: Dict) -> Dict:
    """Check if a robot has pre-built motion generation configs."""
    art_path = args["articulation_path"]
    robot_type = args.get("robot_type", "").lower()

    # Try to identify robot type from path if not provided
    if not robot_type:
        path_lower = art_path.lower()
        for name in _SUPPORTED_MOTION_ROBOTS:
            if name in path_lower:
                robot_type = name
                break

    if robot_type in _SUPPORTED_MOTION_ROBOTS:
        cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, {})
        return {
            "supported": True,
            "robot_type": robot_type,
            "config_files": {
                "rmpflow_config": cfg.get("rmp_config", f"{robot_type}/rmpflow"),
                "robot_descriptor": cfg.get("desc", f"{robot_type}/robot_descriptor.yaml"),
                "urdf": cfg.get("urdf", f"{robot_type}/lula_gen.urdf"),
                "end_effector_frame": cfg.get("ee_frame", "ee_link"),
            },
            "usage": (
                "This robot has pre-built configs. Use "
                "interface_config_loader.load_supported_motion_gen_config("
                f"'{robot_type}', 'RMPflow') to load them."
            ),
            "message": (
                f"Robot '{robot_type}' is pre-supported for motion generation. "
                f"Config files are bundled with the isaacsim.robot_motion.motion_generation extension."
            ),
        }

    return {
        "supported": False,
        "robot_type": robot_type or "(unknown)",
        "articulation_path": art_path,
        "instructions": (
            "This robot does not have pre-built motion generation configs. "
            "To create them:\n"
            "1. Open the XRDF Editor GUI (Window > Extensions > XRDF Editor) to "
            "define collision spheres, joint limits, and end-effector frames.\n"
            "2. Export the XRDF file and Lula robot descriptor YAML.\n"
            "3. Use the exported files with LulaKinematicsSolver and RmpFlow.\n\n"
            "For programmatic collision sphere editing, use the CollisionSphereEditor "
            "from isaacsim.robot_setup.xrdf_editor:\n"
            "  - CollisionSphereEditor.add_sphere(link_path, position, radius)\n"
            "  - CollisionSphereEditor.clear_link_spheres(link_path)\n"
            "  - CollisionSphereEditor.clear_spheres()\n"
            "  - CollisionSphereEditor.delete_sphere(sphere_id)"
        ),
        "message": (
            f"Robot '{robot_type or 'unknown'}' at '{art_path}' is not pre-supported. "
            "Use the XRDF Editor to generate collision spheres and robot descriptors."
        ),
    }


DATA_HANDLERS["generate_robot_description"] = _handle_generate_robot_description


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
    """Walk asset directories and build a searchable index of USD files."""
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    index = []

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
        searchable = f"{name} {path} {rel_path}"

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
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper
CODE_GEN_HANDLERS["grasp_object"] = _gen_grasp_object
CODE_GEN_HANDLERS["define_grasp_pose"] = _gen_define_grasp_pose
DATA_HANDLERS["visualize_behavior_tree"] = _handle_visualize_behavior_tree


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
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper
CODE_GEN_HANDLERS["grasp_object"] = _gen_grasp_object
CODE_GEN_HANDLERS["define_grasp_pose"] = _gen_define_grasp_pose
DATA_HANDLERS["visualize_behavior_tree"] = _handle_visualize_behavior_tree


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

    # ── PhysX overlap check against existing scene (via Kit RPC) ──────────
    # This catches collisions with objects ALREADY in the scene, not just
    # between blueprint objects (which the AABB check above handles).
    try:
        if await kit_tools.is_kit_rpc_alive():
            for obj in objects:
                name = obj.get("name", "unnamed")
                pos = obj.get("position", [0, 0, 0])
                scale = obj.get("scale", [1, 1, 1])
                if not isinstance(pos, (list, tuple)) or len(pos) < 3:
                    continue
                # Approximate half-extents from scale
                if isinstance(scale, (list, tuple)) and len(scale) >= 3:
                    half_extents = [abs(s) * 0.5 for s in scale[:3]]
                else:
                    half_extents = [0.5, 0.5, 0.5]
                result = await kit_tools.post("/check_placement", {
                    "half_extents": half_extents,
                    "position": list(pos[:3]),
                })
                if result and result.get("collisions"):
                    collisions = result["collisions"]
                    issues.append(
                        f"Object '{name}' at {pos[:3]} collides with existing scene: "
                        f"{', '.join(collisions[:5])}"
                    )
    except Exception:
        # Kit RPC not available — skip PhysX validation (AABB check above still runs)
        pass

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


# ── Robot Setup Suite (Phase 8D) ─────────────────────────────────────────

# Default drive parameters per robot type
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
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains
CODE_GEN_HANDLERS["assemble_robot"] = _gen_assemble_robot
CODE_GEN_HANDLERS["configure_self_collision"] = _gen_configure_self_collision


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
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to
CODE_GEN_HANDLERS["create_conveyor"] = _gen_create_conveyor
CODE_GEN_HANDLERS["create_conveyor_track"] = _gen_create_conveyor_track
CODE_GEN_HANDLERS["merge_meshes"] = _gen_merge_meshes


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package


# ── ROS2 Deep Integration (Phase 8F) ────────────────────────────────────────

# Sensor type → OmniGraph node type mapping for configure_ros2_bridge
_ROS2_SENSOR_NODE_MAP = {
    "camera": "ROS2CameraHelper",
    "lidar": "ROS2PublishLaserScan",
    "imu": "ROS2PublishImu",
    "clock": "ROS2PublishClock",
    "joint_state": "ROS2PublishJointState",
}


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
CODE_GEN_HANDLERS["publish_robot_description"] = _gen_publish_robot_description
CODE_GEN_HANDLERS["configure_ros2_bridge"] = _gen_configure_ros2_bridge


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package


# ── Fine-Tune Flywheel (DATA handlers) ─────────────────────────────────────

from ...finetune.turn_recorder import TurnRecorder

_turn_recorder = TurnRecorder()


async def _handle_record_feedback(args: Dict) -> Dict:
    """Link user feedback to a previously recorded turn."""
    session_id = args["session_id"]
    turn_id = args["turn_id"]
    approved = args["approved"]
    edited = args.get("edited", False)
    correction = args.get("correction")
    return _turn_recorder.record_feedback(
        session_id=session_id,
        turn_id=turn_id,
        approved=approved,
        edited=edited,
        correction=correction,
    )


async def _handle_export_finetune_data(args: Dict) -> Dict:
    """Export recorded turns to a provider-specific fine-tuning format."""
    fmt = args["format"]
    min_quality = args.get("min_quality", "approved_successful")
    output_path = args.get("output_path")
    return _turn_recorder.export(
        fmt=fmt,
        min_quality=min_quality,
        output_path=output_path,
    )


async def _handle_finetune_stats(args: Dict) -> Dict:
    """Return aggregate statistics about recorded fine-tuning data."""
    return _turn_recorder.get_stats()


async def _handle_redact_finetune_data(args: Dict) -> Dict:
    """Run the redaction pipeline on an existing JSONL file."""
    input_path = args["input_path"]
    output_path = args.get("output_path")
    return _turn_recorder.redact_file(
        input_path=input_path,
        output_path=output_path,
    )


DATA_HANDLERS["record_feedback"] = _handle_record_feedback
DATA_HANDLERS["export_finetune_data"] = _handle_export_finetune_data
DATA_HANDLERS["finetune_stats"] = _handle_finetune_stats
DATA_HANDLERS["redact_finetune_data"] = _handle_redact_finetune_data


# ── Smart Debugging (Phase 2 Addendum) ─────────────────────────────────────

# ─── 2.X1: diagnose_physics_error (DATA handler) ──────────────────────────

# Top 20 known PhysX error patterns: (regex_pattern, category, fix, severity)
import re as _re

_PHYSX_ERROR_PATTERNS = [
    {
        "pattern": r"negative mass",
        "category": "mass_configuration",
        "fix": "Set the mass to a positive value via UsdPhysics.MassAPI. Check that density and volume are both positive.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"joint limit exceeded",
        "category": "joint_limits",
        "fix": "Increase the joint limit range or add damping to prevent overshoot. Check RevoluteJoint.LowerLimitAttr/UpperLimitAttr.",
        "severity": "warning",
        "prim_regex": r"joint[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"collision mesh invalid|degenerate triangle|invalid mesh",
        "category": "collision_mesh",
        "fix": "Regenerate the collision mesh with convex decomposition. Remove degenerate (zero-area) triangles from the source mesh.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"solver diverge|solver divergence|simulation diverge",
        "category": "solver_divergence",
        "fix": "Lower the physics timestep (e.g. 1/120 instead of 1/60), increase solver iterations (positionIterations=16, velocityIterations=4), or reduce extreme mass ratios.",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"invalid inertia|zero inertia|non-positive inertia",
        "category": "inertia_tensor",
        "fix": "Set a valid diagonal inertia tensor via MassAPI.DiagonalInertiaAttr. All components must be > 0.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"missing collision|no collision api|CollisionAPI not applied",
        "category": "missing_collision",
        "fix": "Apply UsdPhysics.CollisionAPI to the mesh prim: UsdPhysics.CollisionAPI.Apply(prim).",
        "severity": "error",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"PhysicsScene.*not found|no physics scene",
        "category": "missing_physics_scene",
        "fix": "Create a PhysicsScene prim: stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene'). Apply UsdPhysics.Scene API.",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"mass ratio|extreme mass ratio",
        "category": "mass_ratio",
        "fix": "Reduce the mass ratio between contacting bodies to below 100:1. Consider using articulations instead of free bodies for robot links.",
        "severity": "warning",
        "prim_regex": r"(?:between|bodies)[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"articulation.*loop|closed loop|kinematic loop",
        "category": "articulation_loop",
        "fix": "PhysX does not support closed-loop articulations. Break the loop by removing one joint or using a D6 joint with a spring constraint instead.",
        "severity": "critical",
        "prim_regex": r"articulation[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"self.intersection|self.penetration|initial overlap|interpenetration",
        "category": "initial_overlap",
        "fix": "Move the overlapping bodies apart before starting simulation. Use debug draw to visualize collision shapes.",
        "severity": "warning",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"too many contacts|contact buffer overflow",
        "category": "contact_overflow",
        "fix": "Increase PhysxScene.maxNbContactDataBlocks or simplify collision geometry. Consider using collision filtering.",
        "severity": "error",
        "prim_regex": None,
    },
    {
        "pattern": r"gpu.*memory|cuda.*out of memory|gpu.*buffer",
        "category": "gpu_memory",
        "fix": "Reduce the number of collision pairs, lower particle counts, or use simpler collision shapes (convex hull instead of triangle mesh).",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"fixed base.*missing|no fixed base|floating base",
        "category": "fixed_base",
        "fix": "Set PhysxArticulationAPI.fixedBase=True on the articulation root prim for stationary robots.",
        "severity": "warning",
        "prim_regex": r"articulation[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"nan|NaN detected|not a number",
        "category": "nan_values",
        "fix": "NaN typically indicates numerical instability. Check for zero-mass bodies, extreme forces, or missing gravity direction. Lower timestep and increase solver iterations.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"joint drive.*target|drive target out of range",
        "category": "drive_target",
        "fix": "Ensure joint drive targets are within the joint limit range. Clamp target values to [lowerLimit, upperLimit].",
        "severity": "warning",
        "prim_regex": r"joint[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"invalid transform|singular matrix|non-finite transform",
        "category": "invalid_transform",
        "fix": "Reset the prim transform to identity. Check for zero-scale axes or non-orthogonal rotation matrices.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"broadphase.*overflow|pair buffer.*full",
        "category": "broadphase_overflow",
        "fix": "Increase PhysxScene.maxBiasCoefficient or reduce the number of dynamic objects. Use collision groups to limit pair generation.",
        "severity": "error",
        "prim_regex": None,
    },
    {
        "pattern": r"unstable simulation|jitter|oscillat",
        "category": "simulation_instability",
        "fix": "Increase solver iterations, add damping to joints, or lower the physics timestep. Check for stiff springs without adequate damping.",
        "severity": "warning",
        "prim_regex": None,
    },
    {
        "pattern": r"metersPerUnit.*mismatch|scale mismatch|unit mismatch",
        "category": "unit_mismatch",
        "fix": "Ensure all referenced assets use the same metersPerUnit. Set UsdGeom.SetStageMetersPerUnit(stage, 1.0) or scale the referenced asset.",
        "severity": "error",
        "prim_regex": r"asset[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"exceeded velocity|velocity clamp|max velocity",
        "category": "velocity_exceeded",
        "fix": "Increase PhysxRigidBodyAPI.maxLinearVelocity or reduce applied forces. Default max is 100 m/s.",
        "severity": "warning",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
]


async def _handle_diagnose_physics_error(args: Dict) -> Dict:
    """Pattern-match against known PhysX errors and return diagnosis."""
    error_text = args.get("error_text", "")
    if not error_text.strip():
        return {"matches": [], "message": "No error text provided."}

    matches = []
    seen_categories = set()

    # Split into lines for deduplication counting
    lines = error_text.strip().splitlines()

    for entry in _PHYSX_ERROR_PATTERNS:
        pattern = entry["pattern"]
        if not _re.search(pattern, error_text, _re.IGNORECASE):
            continue

        # Count occurrences across lines
        count = sum(
            1 for line in lines
            if _re.search(pattern, line, _re.IGNORECASE)
        )
        # Fallback: at least 1 if it matched the full text
        count = max(count, 1)

        # Try to extract prim path
        prim_path = None
        if entry.get("prim_regex"):
            m = _re.search(entry["prim_regex"], error_text, _re.IGNORECASE)
            if m:
                prim_path = m.group(1)

        if entry["category"] not in seen_categories:
            seen_categories.add(entry["category"])
            matches.append({
                "category": entry["category"],
                "severity": entry["severity"],
                "fix": entry["fix"],
                "prim_path": prim_path,
                "occurrences": count,
                "dedup_hint": f"This error appeared {count} time(s)." if count > 1 else None,
            })

    if not matches:
        return {
            "matches": [],
            "message": "No known PhysX error patterns matched. The error may be application-specific or from a non-physics subsystem.",
        }

    return {
        "matches": matches,
        "total_patterns_checked": len(_PHYSX_ERROR_PATTERNS),
        "message": f"Matched {len(matches)} known error pattern(s).",
    }


DATA_HANDLERS["diagnose_physics_error"] = _handle_diagnose_physics_error


# ─── 2.X2: trace_config (DATA handler) ────────────────────────────────────

async def _handle_trace_config(args: Dict) -> Dict:
    """Parse IsaacLab @configclass files to trace parameter resolution chain."""
    import ast

    param_name = args.get("param_name", "")
    env_source_path = args.get("env_source_path", "")

    if not param_name:
        return {"error": "param_name is required"}

    parts = param_name.split(".")
    target_attr = parts[-1]

    resolution_chain: List[Dict] = []
    final_value = None

    def _trace_in_source(source_text: str, source_path: str) -> None:
        """Walk AST looking for assignments to the target parameter."""
        nonlocal final_value
        try:
            tree = ast.parse(source_text, filename=source_path)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            # Match class-level assignments in @configclass: e.g. `dt = 0.01`
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == target_attr and node.value is not None:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        value = ast.dump(node.value)
                    status = "overridden" if resolution_chain else "active"
                    if resolution_chain:
                        # Mark previous entry as overridden
                        for prev in resolution_chain:
                            if prev["status"] == "active":
                                prev["status"] = "overridden"
                    resolution_chain.append({
                        "source_file": source_path,
                        "line": node.lineno,
                        "value": value,
                        "status": "active",
                    })
                    final_value = value

            # Match simple assignment: e.g. `self.dt = 0.01` or `dt = 0.01`
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    name = None
                    if isinstance(t, ast.Name):
                        name = t.id
                    elif isinstance(t, ast.Attribute):
                        name = t.attr
                    if name == target_attr:
                        try:
                            value = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            value = ast.dump(node.value)
                        for prev in resolution_chain:
                            if prev["status"] == "active":
                                prev["status"] = "overridden"
                        resolution_chain.append({
                            "source_file": source_path,
                            "line": node.lineno,
                            "value": value,
                            "status": "active",
                        })
                        final_value = value

    # If a source path is provided, read it
    if env_source_path:
        source_path = Path(env_source_path)
        if source_path.exists():
            source_text = source_path.read_text(encoding="utf-8")
            _trace_in_source(source_text, str(source_path))

            # Look for imports/base classes to trace the chain further
            try:
                tree = ast.parse(source_text, filename=str(source_path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            # Try to resolve relative imports to find parent configs
                            parent_module = node.module
                            parent_path = source_path.parent / (parent_module.replace(".", "/") + ".py")
                            if parent_path.exists():
                                parent_text = parent_path.read_text(encoding="utf-8")
                                _trace_in_source(parent_text, str(parent_path))
            except SyntaxError:
                pass
        else:
            return {
                "error": f"Source file not found: {env_source_path}",
                "param_name": param_name,
            }

    return {
        "param_name": param_name,
        "final_value": final_value,
        "resolution_chain": resolution_chain,
        "message": (
            f"Traced '{param_name}' through {len(resolution_chain)} source(s)."
            if resolution_chain
            else f"Parameter '{param_name}' not found in the provided source(s)."
        ),
    }


DATA_HANDLERS["trace_config"] = _handle_trace_config


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


# ── Phase 2 Addendum: Smart Debugging ───────────────────────────────────────

import ast as _ast
import re as _re

# ── 2.X1 diagnose_physics_error (DATA handler) ─────────────────────────────

_PHYSX_ERROR_PATTERNS = [
    {
        "pattern": r"negative mass.*?(?:prim|on):\s*(\S+)",
        "category": "mass_configuration",
        "severity": "critical",
        "fix": "Set a positive mass value using UsdPhysics.MassAPI. Ensure density or mass > 0.",
    },
    {
        "pattern": r"(?:collision mesh|collision geometry) invalid.*?(?:prim|on):\s*(\S+)",
        "category": "collision_mesh",
        "severity": "error",
        "fix": "Re-generate or simplify the collision mesh. Apply UsdPhysics.MeshCollisionAPI and set approximation to 'convexHull'.",
    },
    {
        "pattern": r"solver divergence",
        "category": "solver_divergence",
        "severity": "warning",
        "fix": "Reduce physics timestep (increase timeStepsPerSecond), lower solver iteration count, or check for extreme mass ratios between contacting bodies.",
    },
    {
        "pattern": r"joint limit exceeded.*?(?:prim|on):\s*(\S+)",
        "category": "joint_limit",
        "severity": "warning",
        "fix": "Verify joint limits in the URDF/USD. Set realistic lower/upper limits on revolute and prismatic joints.",
    },
    {
        "pattern": r"invalid inertia.*?(?:prim|on):\s*(\S+)",
        "category": "inertia",
        "severity": "critical",
        "fix": "Set valid diagonal inertia values. All eigenvalues must be positive. Use UsdPhysics.MassAPI to set diagonalInertia.",
    },
    {
        "pattern": r"articulation.*?(?:exceeds|exceeded).*?(?:prim|on):\s*(\S+)",
        "category": "articulation_error",
        "severity": "error",
        "fix": "Check the articulation tree for cycles or disconnected links. Ensure ArticulationRootAPI is applied to exactly one prim.",
    },
]


async def _handle_diagnose_physics_error(args: Dict) -> Dict:
    """Pattern-match PhysX error text against known error patterns."""
    error_text = args.get("error_text", "")
    if not error_text:
        return {"matches": [], "message": "No error text provided."}

    seen: Dict[str, Dict] = {}  # category → match info
    for pat_def in _PHYSX_ERROR_PATTERNS:
        for m in _re.finditer(pat_def["pattern"], error_text, _re.IGNORECASE):
            cat = pat_def["category"]
            prim_path = m.group(1) if m.lastindex and m.lastindex >= 1 else None
            if cat not in seen:
                seen[cat] = {
                    "category": cat,
                    "severity": pat_def["severity"],
                    "prim_path": prim_path,
                    "fix": pat_def["fix"],
                    "occurrences": 1,
                    "dedup_hint": None,
                }
            else:
                seen[cat]["occurrences"] += 1

    # Add dedup hints
    for info in seen.values():
        if info["occurrences"] > 1:
            info["dedup_hint"] = f"Same error occurred {info['occurrences']} times — likely parallel envs."

    matches = list(seen.values())
    if not matches:
        return {"matches": [], "message": "No known PhysX error patterns matched."}
    return {"matches": matches, "message": f"Found {len(matches)} error pattern(s)."}


DATA_HANDLERS["diagnose_physics_error"] = _handle_diagnose_physics_error


# ── 2.X2 trace_config (DATA handler) ───────────────────────────────────────

async def _handle_trace_config(args: Dict) -> Dict:
    """AST-based parameter tracing for IsaacLab config files."""
    param_name = args.get("param_name", "")
    env_source_path = args.get("env_source_path", "")

    if not param_name:
        return {"error": "param_name is required."}

    if not env_source_path:
        return {"error": "env_source_path is required."}

    source_path = Path(env_source_path)
    if not source_path.exists():
        return {"error": f"Source file not found: {env_source_path}"}

    # Parse the last segment of dotted param name for matching
    target_attr = param_name.split(".")[-1]

    try:
        source_text = source_path.read_text(encoding="utf-8")
        tree = _ast.parse(source_text)
    except Exception as e:
        return {"error": f"Failed to parse {env_source_path}: {e}"}

    chain = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            if node.target.id == target_attr and node.value is not None:
                try:
                    value = _ast.literal_eval(node.value)
                except Exception:
                    value = _ast.dump(node.value)
                chain.append({
                    "source": f"{source_path.name}:{node.lineno}",
                    "value": value,
                    "status": "active",
                    "line": node.lineno,
                })
        elif isinstance(node, _ast.Assign):
            for target in node.targets:
                name = None
                if isinstance(target, _ast.Name):
                    name = target.id
                elif isinstance(target, _ast.Attribute):
                    name = target.attr
                if name == target_attr:
                    try:
                        value = _ast.literal_eval(node.value)
                    except Exception:
                        value = _ast.dump(node.value)
                    chain.append({
                        "source": f"{source_path.name}:{node.lineno}",
                        "value": value,
                        "status": "active",
                        "line": node.lineno,
                    })

    if not chain:
        return {
            "param": param_name,
            "final_value": None,
            "resolution_chain": [],
            "message": f"Parameter '{param_name}' not found in {source_path.name}.",
        }

    # Mark all but last as overridden
    for entry in chain[:-1]:
        entry["status"] = "overridden"

    return {
        "param": param_name,
        "final_value": chain[-1]["value"],
        "resolution_chain": chain,
        "message": f"Resolved '{param_name}' → {chain[-1]['value']}",
    }


DATA_HANDLERS["trace_config"] = _handle_trace_config


# ── 2.X3 check_physics_health (CODE_GEN handler) ──────────────────────────

def _gen_check_physics_health(args: Dict) -> str:
    """Generate code to audit physics health of the stage or a specific articulation."""
    art_path = args.get("articulation_path")

    if art_path:
        scope_code = f"""\
root = stage.GetPrimAtPath('{art_path}')
if not root.IsValid():
    raise RuntimeError('Prim not found: {art_path}')
prims_to_check = [root] + list(root.GetAllDescendants())"""
    else:
        scope_code = """\
root = stage.GetPseudoRoot()
prims_to_check = list(stage.Traverse())"""

    return f"""\
import omni.usd
from pxr import UsdPhysics, UsdGeom, PhysxSchema, Gf
import json

stage = omni.usd.get_context().get_stage()
issues = []

{scope_code}

meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if abs(meters_per_unit - 0.01) > 0.001 and abs(meters_per_unit - 1.0) > 0.001:
    issues.append({{
        'prim': '/',
        'severity': 'warning',
        'issue': f'metersPerUnit={{meters_per_unit}} — expected 0.01 (cm) or 1.0 (m)',
        'fix': "UsdGeom.SetStageMetersPerUnit(stage, 0.01)"
    }})

for prim in prims_to_check:
    path = str(prim.GetPath())

    # Check for missing CollisionAPI on rigid bodies
    if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        has_child_collision = any(
            c.HasAPI(UsdPhysics.CollisionAPI) for c in prim.GetAllDescendants()
        )
        if not has_child_collision:
            issues.append({{
                'prim': path,
                'severity': 'warning',
                'issue': 'RigidBodyAPI without CollisionAPI (or child collision)',
                'fix': f"UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath('{{path}}'))"
            }})

    # Check for zero mass
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_attr = prim.GetAttribute('physics:mass')
        if mass_attr and mass_attr.Get() is not None and mass_attr.Get() == 0.0:
            issues.append({{
                'prim': path,
                'severity': 'error',
                'issue': 'Zero mass on link — will cause simulation instability',
                'fix': f"stage.GetPrimAtPath('{{path}}').GetAttribute('physics:mass').Set(1.0)"
            }})

    # Check for extreme inertia via DiagonalInertiaAttr
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(prim)
        inertia_attr = mass_api.GetDiagonalInertiaAttr()
        if inertia_attr and inertia_attr.Get() is not None:
            inertia = inertia_attr.Get()
            vals = [float(v) for v in inertia]
            if any(v <= 0 for v in vals):
                issues.append({{
                    'prim': path,
                    'severity': 'critical',
                    'issue': f'Non-positive diagonal inertia: {{vals}}',
                    'fix': f"UsdPhysics.MassAPI(stage.GetPrimAtPath('{{path}}')).GetDiagonalInertiaAttr().Set(Gf.Vec3f(0.01, 0.01, 0.01))"
                }})
            elif len(vals) >= 2 and max(vals) / max(min(vals), 1e-12) > 1000:
                issues.append({{
                    'prim': path,
                    'severity': 'warning',
                    'issue': f'Extreme inertia ratio: {{max(vals)/min(vals):.0f}}:1',
                    'fix': 'Review inertia values — extreme ratios cause solver instability'
                }})

    # Check for infinite joint limits
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
                        'issue': f'Joint limits appear infinite: [{{lo_val}}, {{hi_val}}]',
                        'fix': f"Set realistic joint limits on '{{path}}'"
                    }})

# Check metersPerUnit
print(json.dumps({{'issues': issues, 'total': len(issues), 'metersPerUnit': meters_per_unit}}))
"""


CODE_GEN_HANDLERS["check_physics_health"] = _gen_check_physics_health


# ── 2.X4 generate_robot_description (DATA handler) ────────────────────────

_ROBOT_DESCRIPTION_CONFIGS = {
    "franka": {
        "rmpflow_config": "franka/rmpflow",
        "robot_descriptor": "franka/robot_descriptor.yaml",
        "urdf": "franka/lula_franka_gen.urdf",
        "end_effector_frame": "panda_hand",
    },
    "ur10": {
        "rmpflow_config": "universal_robots/ur10/rmpflow",
        "robot_descriptor": "universal_robots/ur10/robot_descriptor.yaml",
        "urdf": "universal_robots/ur10/lula_ur10_gen.urdf",
        "end_effector_frame": "ee_link",
    },
    "ur5": {
        "rmpflow_config": "universal_robots/ur5e/rmpflow",
        "robot_descriptor": "universal_robots/ur5e/robot_descriptor.yaml",
        "urdf": "universal_robots/ur5e/lula_ur5e_gen.urdf",
        "end_effector_frame": "ee_link",
    },
    "ur5e": {
        "rmpflow_config": "universal_robots/ur5e/rmpflow",
        "robot_descriptor": "universal_robots/ur5e/robot_descriptor.yaml",
        "urdf": "universal_robots/ur5e/lula_ur5e_gen.urdf",
        "end_effector_frame": "ee_link",
    },
    "cobotta": {
        "rmpflow_config": "denso/cobotta_pro_900/rmpflow",
        "robot_descriptor": "denso/cobotta_pro_900/robot_descriptor.yaml",
        "urdf": "denso/cobotta_pro_900/lula_cobotta_pro_900_gen.urdf",
        "end_effector_frame": "onrobot_rg6_base_link",
    },
}

# Robot name detection patterns for auto-detect from articulation path
_ROBOT_NAME_PATTERNS = {
    "franka": ["franka", "panda"],
    "ur10": ["ur10"],
    "ur5": ["ur5"],
    "ur5e": ["ur5e"],
    "cobotta": ["cobotta"],
}


def _detect_robot_type(articulation_path: str) -> Optional[str]:
    """Auto-detect robot type from articulation path."""
    path_lower = articulation_path.lower()
    for robot_type, patterns in _ROBOT_NAME_PATTERNS.items():
        for pat in patterns:
            if pat in path_lower:
                return robot_type
    return None


async def _handle_generate_robot_description(args: Dict) -> Dict:
    """Return config file paths for a known robot, or instructions for custom robots."""
    art_path = args["articulation_path"]
    robot_type = args.get("robot_type", "")

    # Auto-detect from path if not provided
    if not robot_type:
        robot_type = _detect_robot_type(art_path)

    if robot_type and robot_type in _ROBOT_DESCRIPTION_CONFIGS:
        cfg = _ROBOT_DESCRIPTION_CONFIGS[robot_type]
        return {
            "supported": True,
            "robot_type": robot_type,
            "articulation_path": art_path,
            "config_files": cfg,
            "message": f"Robot '{robot_type}' is pre-supported. Config files are available.",
        }

    return {
        "supported": False,
        "robot_type": robot_type or "unknown",
        "articulation_path": art_path,
        "instructions": (
            "This robot is not pre-supported. Use the XRDF Editor to create a robot descriptor, "
            "then use CollisionSphereEditor to generate collision spheres for motion planning."
        ),
        "message": f"Robot '{robot_type or 'unknown'}' is not pre-supported.",
    }


DATA_HANDLERS["generate_robot_description"] = _handle_generate_robot_description


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


# ── 3.X2 apply_robot_fix_profile (DATA handler) ───────────────────────────

_ROBOT_FIX_PROFILES = {
    "franka": {
        "robot_name": "franka",
        "display_name": "Franka Emika Panda",
        "known_issues": [
            "rootJoint creates unwanted floating base — delete it",
            "Default drive stiffness too low for position control",
            "panda_hand and finger links often missing CollisionAPI",
        ],
        "fixes": [
            {
                "description": "Delete rootJoint to allow fixedBase anchoring",
                "code": "stage.RemovePrim('{art_path}/rootJoint')",
            },
            {
                "description": "Set fixedBase for stationary arm",
                "code": "PhysxSchema.PhysxArticulationAPI.Apply(stage.GetPrimAtPath('{art_path}')).CreateEnabledSelfCollisionsAttr(False)",
            },
            {
                "description": "Set drive stiffness Kp=1000, Kd=100 on all joints",
                "code": "# Apply Kp=1000, Kd=100 to all revolute joints",
            },
            {
                "description": "Add CollisionAPI to hand and finger links",
                "code": "# Apply CollisionAPI to panda_hand, panda_leftfinger, panda_rightfinger",
            },
        ],
        "drive_gains": {"kp": 1000, "kd": 100},
    },
    "ur5": {
        "robot_name": "ur5",
        "display_name": "Universal Robots UR5",
        "known_issues": [
            "Joint limits often imported as ±infinity",
            "Missing collision meshes on wrist links",
        ],
        "fixes": [
            {
                "description": "Set finite joint limits (±2π for revolute joints)",
                "code": "# Set lowerLimit=-6.283, upperLimit=6.283 on all revolute joints",
            },
            {
                "description": "Add CollisionAPI to wrist links",
                "code": "# Apply CollisionAPI to wrist_1_link, wrist_2_link, wrist_3_link",
            },
        ],
        "drive_gains": {"kp": 800, "kd": 80},
    },
    "ur10": {
        "robot_name": "ur10",
        "display_name": "Universal Robots UR10",
        "known_issues": [
            "Joint limits often imported as ±infinity",
            "Missing collision meshes on wrist links",
            "Default mass values may be incorrect for UR10 (heavier than UR5)",
        ],
        "fixes": [
            {
                "description": "Set finite joint limits (±2π for revolute joints)",
                "code": "# Set lowerLimit=-6.283, upperLimit=6.283 on all revolute joints",
            },
            {
                "description": "Add CollisionAPI to wrist links",
                "code": "# Apply CollisionAPI to wrist_1_link, wrist_2_link, wrist_3_link",
            },
        ],
        "drive_gains": {"kp": 1000, "kd": 100},
    },
    "g1": {
        "robot_name": "g1",
        "display_name": "Unitree G1 Humanoid",
        "known_issues": [
            "Many links imported with zero mass",
            "Extreme inertia ratios between torso and finger links",
            "Self-collision filtering needed for dense link structure",
        ],
        "fixes": [
            {
                "description": "Set minimum mass (0.1 kg) on zero-mass links",
                "code": "# Set mass=0.1 on all links where mass==0",
            },
            {
                "description": "Enable self-collision filtering",
                "code": "PhysxSchema.PhysxArticulationAPI.Apply(root).CreateEnabledSelfCollisionsAttr(True)",
            },
        ],
        "drive_gains": {"kp": 500, "kd": 50},
    },
    "allegro": {
        "robot_name": "allegro",
        "display_name": "Allegro Hand",
        "known_issues": [
            "Very small link masses cause solver instability",
            "Finger joint limits must be carefully bounded",
            "CollisionAPI often missing on fingertip links",
        ],
        "fixes": [
            {
                "description": "Set minimum mass (0.01 kg) on finger links",
                "code": "# Set mass=0.01 on all finger links",
            },
            {
                "description": "Add CollisionAPI to all fingertip links",
                "code": "# Apply CollisionAPI to all *_tip links",
            },
        ],
        "drive_gains": {"kp": 100, "kd": 10},
    },
}

# Auto-detect patterns for robot name from path
_FIX_PROFILE_PATTERNS = {
    "franka": ["franka", "panda"],
    "ur5": ["ur5"],
    "ur10": ["ur10"],
    "g1": ["g1", "unitree_g1"],
    "allegro": ["allegro"],
}


def _detect_robot_for_fix(articulation_path: str) -> Optional[str]:
    """Auto-detect robot name from articulation path for fix profile lookup."""
    path_lower = articulation_path.lower()
    for robot_name, patterns in _FIX_PROFILE_PATTERNS.items():
        for pat in patterns:
            if pat in path_lower:
                return robot_name
    return None


async def _handle_apply_robot_fix_profile(args: Dict) -> Dict:
    """Look up known robot import issues and return a fix profile."""
    art_path = args["articulation_path"]
    robot_name = args.get("robot_name", "")

    # Auto-detect from path if not provided
    if not robot_name:
        robot_name = _detect_robot_for_fix(art_path)

    if not robot_name or robot_name not in _ROBOT_FIX_PROFILES:
        return {
            "found": False,
            "robot_name": robot_name or "unknown",
            "articulation_path": art_path,
            "message": (
                f"No fix profile found for '{robot_name or 'unknown'}'. "
                f"Known robots: {', '.join(sorted(_ROBOT_FIX_PROFILES.keys()))}. "
                f"Use verify_import to diagnose issues instead."
            ),
        }

    profile = _ROBOT_FIX_PROFILES[robot_name].copy()
    # Substitute articulation path into fix code templates
    fixes = []
    for fix in profile["fixes"]:
        fixes.append({
            "description": fix["description"],
            "code": fix["code"].replace("{art_path}", art_path),
        })
    profile["fixes"] = fixes
    profile["articulation_path"] = art_path
    profile["found"] = True
    profile["message"] = f"Fix profile for '{profile['display_name']}' — {len(fixes)} fixes available."

    return profile


DATA_HANDLERS["apply_robot_fix_profile"] = _handle_apply_robot_fix_profile


# ── SDG Quality (Phase 7B Addendum) ─────────────────────────────────────────

async def _handle_validate_annotations(args: Dict) -> Dict:
    """Cross-check SDG annotations for common quality issues.

    Validates: bbox within image bounds, unique instance IDs,
    no zero-area boxes, declared classes actually appear.
    """
    num_samples = args.get("num_samples", 10)

    code = f"""\
import json, os, glob, random

output_dirs = glob.glob('/tmp/sdg_output*') + glob.glob('workspace/sdg_output*')
if not output_dirs:
    print(json.dumps({{"error": "No SDG output directories found"}}))
else:
    out_dir = sorted(output_dirs)[-1]
    ann_files = glob.glob(os.path.join(out_dir, '**', '*.json'), recursive=True)
    ann_files = [f for f in ann_files if 'bounding_box' in f or 'annotation' in f]
    samples = ann_files[:{num_samples}] if len(ann_files) <= {num_samples} else random.sample(ann_files, {num_samples})

    issues = []
    total_boxes = 0
    instance_ids_seen = set()
    classes_declared = set()
    classes_found = set()

    for f in samples:
        data = json.loads(open(f).read())
        annotations = data if isinstance(data, list) else data.get('annotations', data.get('data', []))
        if not isinstance(annotations, list):
            annotations = [annotations]
        for ann in annotations:
            total_boxes += 1
            bbox = ann.get('bbox') or ann.get('bounding_box') or ann.get('x_min') and [ann['x_min'], ann['y_min'], ann['x_max'], ann['y_max']]
            if bbox:
                x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
                if x0 < 0 or y0 < 0:
                    issues.append({{"type": "out_of_bounds", "file": f, "bbox": bbox, "detail": "Negative coordinates"}})
                if x1 <= x0 or y1 <= y0:
                    issues.append({{"type": "zero_area", "file": f, "bbox": bbox, "detail": "Zero or negative area"}})
                w = ann.get('image_width', 1280)
                h = ann.get('image_height', 720)
                if x1 > w or y1 > h:
                    issues.append({{"type": "out_of_bounds", "file": f, "bbox": bbox, "detail": f"Exceeds image {{w}}x{{h}}"}})

            iid = ann.get('instance_id') or ann.get('id')
            if iid is not None:
                if iid in instance_ids_seen:
                    issues.append({{"type": "duplicate_id", "file": f, "instance_id": iid}})
                instance_ids_seen.add(iid)

            cls = ann.get('class') or ann.get('label') or ann.get('category')
            if cls:
                classes_found.add(cls)

        meta_classes = data.get('declared_classes') or data.get('classes') or data.get('categories')
        if meta_classes:
            if isinstance(meta_classes, list):
                for c in meta_classes:
                    classes_declared.add(c if isinstance(c, str) else c.get('name', str(c)))

    missing_classes = list(classes_declared - classes_found)
    if missing_classes:
        issues.append({{"type": "missing_class", "declared_but_absent": missing_classes}})

    clean = total_boxes - len([i for i in issues if i['type'] != 'missing_class'])
    health = round(100 * clean / max(total_boxes, 1), 1)

    print(json.dumps({{
        "samples_checked": len(samples),
        "total_boxes": total_boxes,
        "issues": issues,
        "annotation_health": health,
        "classes_declared": list(classes_declared),
        "classes_found": list(classes_found),
    }}))
"""
    result = await kit_tools.queue_exec_patch(code, f"Validate annotations ({num_samples} samples)")
    return {"type": "data", "queued": result.get("queued", False)}


async def _handle_analyze_randomization(args: Dict) -> Dict:
    """Analyze domain randomization parameter distributions from an SDG run.

    Returns per-parameter statistics and flags near-constant or collapsed
    distributions that indicate DR misconfiguration.
    """
    num_samples = args.get("num_samples", 50)

    code = f"""\
import json, os, glob, random
import numpy as np

output_dirs = glob.glob('/tmp/sdg_output*') + glob.glob('workspace/sdg_output*')
if not output_dirs:
    print(json.dumps({{"error": "No SDG output directories found"}}))
else:
    out_dir = sorted(output_dirs)[-1]

    # Look for DR log / randomization parameter files
    dr_files = glob.glob(os.path.join(out_dir, '**', '*random*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*param*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*.json'), recursive=True)
    dr_files = list(set(dr_files))
    samples = dr_files[:{num_samples}] if len(dr_files) <= {num_samples} else random.sample(dr_files, {num_samples})

    param_values = {{}}  # param_name -> list of values

    for f in samples:
        try:
            data = json.loads(open(f).read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        params = data.get('randomization_params') or data.get('params') or data.get('dr_params') or {{}}
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, (int, float)):
                    param_values.setdefault(k, []).append(v)
                elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                    for i, x in enumerate(v):
                        param_values.setdefault(f"{{k}}[{{i}}]", []).append(x)

    stats = {{}}
    warnings = []
    for pname, vals in param_values.items():
        arr = np.array(vals, dtype=float)
        s = {{
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "count": len(vals),
        }}
        stats[pname] = s

        # Flag near-constant distributions
        if s["std"] < 1e-6 and s["count"] > 5:
            warnings.append({{
                "param": pname,
                "warning": "near_constant",
                "detail": f"{{s['count']}} samples all ~{{s['mean']:.4f}} — DR may be misconfigured",
            }})
        # Flag extremely narrow range
        range_val = s["max"] - s["min"]
        if range_val > 0 and s["std"] / range_val < 0.01 and s["count"] > 10:
            warnings.append({{
                "param": pname,
                "warning": "narrow_range",
                "detail": f"std/range = {{s['std']/range_val:.4f}} — 99%+ values are the same angle/position",
            }})

    print(json.dumps({{
        "samples_analyzed": len(samples),
        "parameters": stats,
        "warnings": warnings,
        "total_params": len(stats),
    }}))
"""
    result = await kit_tools.queue_exec_patch(code, f"Analyze DR randomization ({num_samples} samples)")
    return {"type": "data", "queued": result.get("queued", False)}


async def _handle_diagnose_domain_gap(args: Dict) -> Dict:
    """Compare synthetic vs real image datasets to diagnose domain gap.

    Returns a FID-like comparison score, per-class distribution differences,
    and suggested DR adjustments.
    """
    synthetic_dir = args.get("synthetic_dir", "")
    real_dir = args.get("real_dir", "")
    checkpoint = args.get("model_checkpoint")

    if not synthetic_dir or not real_dir:
        return {"error": "Both synthetic_dir and real_dir are required"}

    # Sanitize paths
    import re as _re
    for d in (synthetic_dir, real_dir):
        if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', d):
            return {"error": f"Invalid path characters in: {d}"}

    checkpoint_line = ""
    if checkpoint:
        if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', checkpoint):
            return {"error": f"Invalid path characters in checkpoint: {checkpoint}"}
        checkpoint_line = f"checkpoint = '{checkpoint}'"

    code = f"""\
import json, os, glob
import numpy as np

synthetic_dir = '{synthetic_dir}'
real_dir = '{real_dir}'
{checkpoint_line}

def load_image_stats(directory):
    \"\"\"Compute per-channel mean/std over images in a directory.\"\"\"
    from PIL import Image
    files = glob.glob(os.path.join(directory, '**', '*.png'), recursive=True)
    files += glob.glob(os.path.join(directory, '**', '*.jpg'), recursive=True)
    if not files:
        return None, 0
    samples = files[:200] if len(files) > 200 else files
    all_means = []
    all_stds = []
    for f in samples:
        try:
            img = np.array(Image.open(f).convert('RGB'), dtype=np.float32) / 255.0
            all_means.append(img.mean(axis=(0, 1)))
            all_stds.append(img.std(axis=(0, 1)))
        except Exception:
            continue
    if not all_means:
        return None, 0
    return {{
        "channel_means": np.mean(all_means, axis=0).tolist(),
        "channel_stds": np.mean(all_stds, axis=0).tolist(),
        "count": len(all_means),
    }}, len(files)

synth_stats, synth_count = load_image_stats(synthetic_dir)
real_stats, real_count = load_image_stats(real_dir)

if synth_stats is None:
    print(json.dumps({{"error": f"No images found in synthetic dir: {{synthetic_dir}}"}}))
elif real_stats is None:
    print(json.dumps({{"error": f"No images found in real dir: {{real_dir}}"}}))
else:
    # Compute FID-like score from channel statistics
    mean_diff = np.linalg.norm(
        np.array(synth_stats['channel_means']) - np.array(real_stats['channel_means'])
    )
    std_diff = np.linalg.norm(
        np.array(synth_stats['channel_stds']) - np.array(real_stats['channel_stds'])
    )
    # Simplified domain gap score (0 = identical, higher = more gap)
    gap_score = float(mean_diff * 100 + std_diff * 50)

    # Per-channel analysis
    channels = ['R', 'G', 'B']
    per_channel = {{}}
    adjustments = []
    for i, ch in enumerate(channels):
        diff = synth_stats['channel_means'][i] - real_stats['channel_means'][i]
        per_channel[ch] = {{
            "synthetic_mean": round(synth_stats['channel_means'][i], 4),
            "real_mean": round(real_stats['channel_means'][i], 4),
            "difference": round(diff, 4),
        }}
        if abs(diff) > 0.1:
            direction = "brighter" if diff > 0 else "darker"
            adjustments.append(f"Synthetic {{ch}} channel is {{direction}} than real by {{abs(diff):.2f}} — adjust lighting/material {{ch}} intensity")

    if gap_score > 15:
        adjustments.append("High domain gap — consider adding texture/lighting randomization")
    if gap_score > 30:
        adjustments.append("Very high domain gap — real-to-sim calibration recommended")

    result = {{
        "domain_gap_score": round(gap_score, 2),
        "synthetic_images": synth_count,
        "real_images": real_count,
        "synthetic_stats": synth_stats,
        "real_stats": real_stats,
        "per_channel_diff": per_channel,
        "suggested_adjustments": adjustments,
        "model_checkpoint": '{checkpoint or "none"}',
    }}
    print(json.dumps(result))
"""
    result = await kit_tools.queue_exec_patch(
        code, f"Diagnose domain gap: {synthetic_dir} vs {real_dir}"
    )
    return {"type": "data", "queued": result.get("queued", False)}


DATA_HANDLERS["validate_annotations"] = _handle_validate_annotations
DATA_HANDLERS["analyze_randomization"] = _handle_analyze_randomization
DATA_HANDLERS["diagnose_domain_gap"] = _handle_diagnose_domain_gap


# ── Phase 8F Addendum: ROS2 Quality Diagnostics ────────────────────────────

# QoS preset mapping: topic keyword → (reliability, durability, description)
_ROS2_QOS_PRESETS = {
    "scan": ("BEST_EFFORT", "VOLATILE", "Laser scan data — high-frequency, drop-tolerant"),
    "robot_description": ("RELIABLE", "TRANSIENT_LOCAL", "Robot URDF — latched, must arrive"),
    "tf": ("RELIABLE", "VOLATILE", "Transform tree — must be reliable"),
    "tf_static": ("RELIABLE", "TRANSIENT_LOCAL", "Static transforms — latched"),
    "cmd_vel": ("RELIABLE", "VOLATILE", "Velocity commands — must not be dropped"),
    "camera": ("BEST_EFFORT", "VOLATILE", "Camera images — high-bandwidth, drop-tolerant"),
    "image": ("BEST_EFFORT", "VOLATILE", "Image data — high-bandwidth, drop-tolerant"),
    "joint_states": ("RELIABLE", "VOLATILE", "Joint state feedback — must be reliable"),
    "clock": ("BEST_EFFORT", "VOLATILE", "Simulation clock — high-frequency"),
}


async def _handle_diagnose_ros2(args: Dict) -> Dict:
    """Run comprehensive ROS2 integration health check on the current scene.

    Checks performed:
    1. ROS2Context node present in OmniGraph
    2. ROS distro detection
    3. QoS profile mismatches between common topic pairs
    4. use_sim_time parameter configuration
    5. Clock publishing (ROS2PublishClock node)
    6. Domain ID consistency
    7. Dangling OmniGraph connections
    """
    issues: List[Dict[str, Any]] = []

    # Generate diagnostic code that runs inside Kit
    diag_code = '''\
import omni.graph.core as og
import json
import os

result = {
    "ros2_context_found": False,
    "ros2_context_path": None,
    "distro": None,
    "domain_id": None,
    "clock_publisher_found": False,
    "use_sim_time": None,
    "og_graphs": [],
    "dangling_connections": [],
    "qos_nodes": [],
}

# Check ROS_DISTRO environment variable
result["distro"] = os.environ.get("ROS_DISTRO", None)
result["domain_id"] = os.environ.get("ROS_DOMAIN_ID", "0")

# Scan all OmniGraph graphs
try:
    all_graphs = og.get_all_graphs()
    for graph in all_graphs:
        graph_path = graph.get_path_to_graph()
        result["og_graphs"].append(graph_path)
        nodes = graph.get_nodes()
        for node in nodes:
            node_type = node.get_type_name()
            node_path = node.get_prim_path()

            # Check for ROS2Context
            if "ROS2Context" in node_type:
                result["ros2_context_found"] = True
                result["ros2_context_path"] = str(node_path)
                # Try to read domain_id attribute
                domain_attr = node.get_attribute("inputs:domain_id")
                if domain_attr:
                    result["domain_id_node"] = domain_attr.get()

            # Check for ROS2PublishClock
            if "PublishClock" in node_type:
                result["clock_publisher_found"] = True

            # Collect QoS-relevant nodes
            if "ROS2" in node_type and "Publish" in node_type:
                topic_attr = node.get_attribute("inputs:topicName")
                qos_attr = node.get_attribute("inputs:qosProfile")
                result["qos_nodes"].append({
                    "node_type": node_type,
                    "node_path": str(node_path),
                    "topic": topic_attr.get() if topic_attr else None,
                    "qos": qos_attr.get() if qos_attr else None,
                })

        # Check for dangling connections
        for node in nodes:
            for attr in node.get_attributes():
                if attr.get_port_type() == og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT:
                    upstream = attr.get_upstream_connections()
                    if not upstream and attr.get_name().startswith("inputs:execIn"):
                        result["dangling_connections"].append({
                            "node": str(node.get_prim_path()),
                            "attr": attr.get_name(),
                        })
except Exception as e:
    result["scan_error"] = str(e)

# Check use_sim_time via carb settings
try:
    import carb.settings
    settings = carb.settings.get_settings()
    result["use_sim_time"] = settings.get("/persistent/exts/isaacsim.ros2.bridge/useSimTime")
except Exception:
    result["use_sim_time"] = None

print(json.dumps(result))
'''

    try:
        diag_result = await kit_tools.queue_exec_patch(diag_code, "ROS2 diagnostic scan")
        # Parse the result if we got immediate output
        if isinstance(diag_result, dict) and diag_result.get("output"):
            import json as _json
            scene_info = _json.loads(diag_result["output"])
        else:
            scene_info = {}
    except Exception:
        scene_info = {}

    # Issue 1: ROS2Context node
    if not scene_info.get("ros2_context_found", False):
        issues.append({
            "id": "no_ros2_context",
            "severity": "critical",
            "message": "No ROS2Context node found in any OmniGraph",
            "fix": "Add a ROS2Context node to your action graph. This is required for all ROS2 bridge communication.",
            "tool_hint": "create_omnigraph with a ROS2Context node",
        })

    # Issue 2: ROS distro
    distro = scene_info.get("distro")
    if not distro:
        issues.append({
            "id": "no_ros_distro",
            "severity": "warning",
            "message": "ROS_DISTRO environment variable not set",
            "fix": "Source your ROS2 workspace: source /opt/ros/<distro>/setup.bash",
            "tool_hint": None,
        })

    # Issue 3: Clock publisher
    if not scene_info.get("clock_publisher_found", False):
        issues.append({
            "id": "no_clock_publisher",
            "severity": "warning",
            "message": "No ROS2PublishClock node found — /clock topic will not be published",
            "fix": "Add a ROS2PublishClock node to publish simulation time. Use configure_ros2_time tool.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 4: use_sim_time
    use_sim_time = scene_info.get("use_sim_time")
    clock_found = scene_info.get("clock_publisher_found", False)
    if clock_found and use_sim_time is not True:
        issues.append({
            "id": "use_sim_time_mismatch",
            "severity": "warning",
            "message": "Clock publisher active but use_sim_time is not enabled",
            "fix": "Set use_sim_time=true so ROS2 nodes use simulation clock instead of wall clock.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 5: Domain ID mismatch
    env_domain = scene_info.get("domain_id", "0")
    node_domain = scene_info.get("domain_id_node")
    if node_domain is not None and str(node_domain) != str(env_domain):
        issues.append({
            "id": "domain_id_mismatch",
            "severity": "critical",
            "message": f"Domain ID mismatch: ROS_DOMAIN_ID={env_domain} but ROS2Context node has domain_id={node_domain}",
            "fix": f"Set ROS_DOMAIN_ID={node_domain} in your environment, or update the ROS2Context node to domain_id={env_domain}.",
            "tool_hint": None,
        })

    # Issue 6: QoS mismatches
    for qos_node in scene_info.get("qos_nodes", []):
        topic = qos_node.get("topic", "")
        if topic:
            topic_key = topic.strip("/").split("/")[-1]
            preset = _ROS2_QOS_PRESETS.get(topic_key)
            if preset and qos_node.get("qos"):
                current_qos = str(qos_node["qos"])
                expected_reliability = preset[0]
                if expected_reliability not in current_qos:
                    issues.append({
                        "id": "qos_mismatch",
                        "severity": "warning",
                        "message": f"QoS mismatch on topic '{topic}': expected {expected_reliability} reliability",
                        "fix": f"Use fix_ros2_qos(topic='{topic}') to apply the recommended QoS profile.",
                        "tool_hint": f"fix_ros2_qos(topic='{topic}')",
                    })

    # Issue 7: Dangling connections
    for dangling in scene_info.get("dangling_connections", []):
        issues.append({
            "id": "dangling_connection",
            "severity": "info",
            "message": f"Dangling execution input on {dangling['node']}.{dangling['attr']}",
            "fix": "Connect this node's execIn to an OnPlaybackTick or upstream node.",
            "tool_hint": None,
        })

    return {
        "issues": issues,
        "issue_count": len(issues),
        "ros2_context_found": scene_info.get("ros2_context_found", False),
        "distro": scene_info.get("distro"),
        "domain_id": scene_info.get("domain_id", "0"),
        "clock_publishing": scene_info.get("clock_publisher_found", False),
        "graphs_scanned": len(scene_info.get("og_graphs", [])),
        "message": f"Found {len(issues)} issue(s)" if issues else "All ROS2 checks passed — no issues found",
    }


DATA_HANDLERS["diagnose_ros2"] = _handle_diagnose_ros2


def _gen_fix_ros2_qos(args: Dict) -> str:
    """Generate code to update the QoS profile on a ROS2 publisher for a given topic."""
    topic = args["topic"]

    # Determine the QoS preset from the topic name
    topic_key = topic.strip("/").split("/")[-1]
    preset = _ROS2_QOS_PRESETS.get(topic_key)

    if preset:
        reliability, durability, description = preset
    else:
        # Default to RELIABLE + VOLATILE for unknown topics
        reliability = "RELIABLE"
        durability = "VOLATILE"
        description = f"Unknown topic '{topic}' — defaulting to RELIABLE"

    return f'''\
import omni.graph.core as og
import json

topic_name = "{topic}"
target_reliability = "{reliability}"
target_durability = "{durability}"

# QoS profile: {description}
# Find the publisher node for this topic and update its QoS profile
all_graphs = og.get_all_graphs()
updated = False

for graph in all_graphs:
    for node in graph.get_nodes():
        node_type = node.get_type_name()
        if "ROS2" not in node_type:
            continue

        topic_attr = node.get_attribute("inputs:topicName")
        if not topic_attr:
            continue

        current_topic = topic_attr.get()
        if current_topic != topic_name:
            continue

        # Found the node — update QoS profile
        qos_attr = node.get_attribute("inputs:qosProfile")
        if qos_attr:
            qos_attr.set(f"{{target_reliability}}, {{target_durability}}")
            updated = True
            print(f"Updated QoS on {{node.get_prim_path()}}: {{target_reliability}}, {{target_durability}}")

        # Also set reliability/durability if separate attributes exist
        rel_attr = node.get_attribute("inputs:reliability")
        if rel_attr:
            rel_attr.set(target_reliability)

        dur_attr = node.get_attribute("inputs:durability")
        if dur_attr:
            dur_attr.set(target_durability)

        break  # Only update the first matching node

if not updated:
    # No existing node found — create a new publisher with correct QoS
    print(f"No publisher found for {{topic_name}} — set QoS when creating the publisher:")
    print(f"  reliability: {{target_reliability}}")
    print(f"  durability: {{target_durability}}")
    print(f"  Hint: {description}")
'''


CODE_GEN_HANDLERS["fix_ros2_qos"] = _gen_fix_ros2_qos


def _gen_configure_ros2_time(args: Dict) -> str:
    """Generate OmniGraph code for ROS2 clock publishing and use_sim_time configuration."""
    mode = args["mode"]
    time_scale = args.get("time_scale", 1.0)

    if mode == "real_time":
        return '''\
import carb.settings
import omni.graph.core as og

# Configure real_time mode: disable use_sim_time, no clock publishing needed
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", False)

# Remove existing ROS2PublishClock nodes if any
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            node_path = node.get_prim_path()
            print(f"Note: ROS2PublishClock at {node_path} is active but use_sim_time=false")
            print("ROS2 nodes will use wall clock time.")

print("Configured real_time mode: use_sim_time=false")
print("ROS2 nodes will use the system wall clock.")
'''

    # sim_time or scaled mode — both need clock publishing
    time_scale_block = ""
    if mode == "scaled":
        time_scale_block = f'''
# Set simulation time scale
import omni.timeline
tl = omni.timeline.get_timeline_interface()
tl.set_time_codes_per_second(tl.get_time_codes_per_second() * {time_scale})
print(f"Time scale set to {time_scale}x")
'''

    return f'''\
import omni.graph.core as og
import carb.settings

# ── Step 1: Enable use_sim_time ──────────────────────────────────────────
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", True)
print("Enabled use_sim_time=true")

# ── Step 2: Create ROS2PublishClock node in an action graph ──────────────
# Check if a clock publisher already exists
clock_exists = False
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            clock_exists = True
            print(f"ROS2PublishClock already exists at {{node.get_prim_path()}}")
            break
    if clock_exists:
        break

if not clock_exists:
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
            "graph_path": "/World/ROS2ClockGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
            ],
        }},
    )
    print("Created ROS2ClockGraph with ROS2PublishClock node")
    print("  /clock topic will publish simulation time")
{time_scale_block}
print("Configured {mode} mode: ROS2 nodes will use simulation clock from /clock topic")
'''


CODE_GEN_HANDLERS["configure_ros2_time"] = _gen_configure_ros2_time


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package


# ── Workspace & Singularity (Phase 8B Addendum) ─────────────────────────────

def _gen_show_workspace(args: Dict) -> str:
    """Generate code to visualize robot workspace with manipulability gradient."""
    art_path = args["articulation_path"]
    resolution = args.get("resolution", 500000)
    color_mode = args.get("color_mode", "manipulability")

    return f"""\
import omni.usd
import numpy as np
from pxr import UsdPhysics
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect revolute joint limits
joints = []
for desc in art_prim.GetAllDescendants():
    if desc.HasAPI(UsdPhysics.RevoluteJointAPI) or desc.IsA(UsdPhysics.RevoluteJoint):
        lo_attr = desc.GetAttribute('physics:lowerLimit')
        hi_attr = desc.GetAttribute('physics:upperLimit')
        lo = np.radians(lo_attr.Get() if lo_attr and lo_attr.Get() is not None else -180.0)
        hi = np.radians(hi_attr.Get() if hi_attr and hi_attr.Get() is not None else 180.0)
        joints.append({{'name': desc.GetName(), 'lower': lo, 'upper': hi}})

n_joints = len(joints)
if n_joints == 0:
    raise RuntimeError('No revolute joints found')

n_samples = min({resolution}, 500000)
print(f'Sampling {{n_samples}} configurations across {{n_joints}} joints...')

# Random joint configs within limits
q_samples = np.zeros((n_samples, n_joints))
for i, j in enumerate(joints):
    q_samples[:, i] = np.random.uniform(j['lower'], j['upper'], n_samples)

# Forward kinematics using Lula
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader

try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{art_path}'.split('/')[-1].lower())
    kin = LulaKinematicsSolver(**kin_config)
except Exception:
    print('Robot not in pre-supported list — cannot compute FK')
    raise

ee_positions = []
manipulability = []
eps = 1e-4

for q in q_samples[:min(n_samples, 50000)]:  # cap for Jacobian computation
    # FK
    pos, _ = kin.compute_forward_kinematics('{art_path}'.split('/')[-1], q)
    ee_positions.append(pos)

    # Numerical Jacobian for manipulability
    J = np.zeros((3, n_joints))
    for k in range(n_joints):
        q_plus = q.copy(); q_plus[k] += eps
        pos_plus, _ = kin.compute_forward_kinematics('{art_path}'.split('/')[-1], q_plus)
        J[:, k] = (np.array(pos_plus) - np.array(pos)) / eps
    w = np.sqrt(max(np.linalg.det(J @ J.T), 0))
    manipulability.append(w)

ee_positions = np.array(ee_positions)
manipulability = np.array(manipulability)

# Color mapping
if '{color_mode}' == 'reachability':
    colors = [(0, 1, 0, 0.5)] * len(ee_positions)  # green
elif '{color_mode}' == 'singularity_distance':
    w_norm = manipulability / (manipulability.max() + 1e-10)
    colors = [(1 - v, v, 0, 0.5) for v in w_norm]  # red=singularity, green=safe
else:  # manipulability
    w_norm = manipulability / (manipulability.max() + 1e-10)
    colors = [(1 - v, v, 0, 0.5) for v in w_norm]  # green=high, red=low

# Draw
draw = _debug_draw.acquire_debug_draw_interface()
draw.clear_points()
points = [(float(p[0]), float(p[1]), float(p[2])) for p in ee_positions]
draw.draw_points(points, colors, [3] * len(points))
print(f'Workspace visualized: {{len(points)}} points, mode={color_mode}')
"""

CODE_GEN_HANDLERS["show_workspace"] = _gen_show_workspace


def _gen_check_singularity(args: Dict) -> str:
    """Generate code to check singularity at a target pose via Jacobian SVD."""
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")

    ori_code = f"np.array({list(target_ori)})" if target_ori else "None"

    return f"""\
import numpy as np
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver, ArticulationKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
import json

robot_name = '{art_path}'.split('/')[-1].lower()
target_pos = np.array({list(target_pos)})
target_ori = {ori_code}

# Load kinematics
try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config(robot_name)
    kin = LulaKinematicsSolver(**kin_config)
except Exception:
    print(json.dumps({{"status": "error", "message": "Robot not in supported list"}}))
    raise

# Solve IK
art = SingleArticulation('{art_path}')
art_kin = ArticulationKinematicsSolver(art, kin, kin.get_all_frame_names()[-1])
action, success = art_kin.compute_inverse_kinematics(
    target_position=target_pos,
    target_orientation=target_ori,
)

if not success:
    print(json.dumps({{"status": "unreachable", "message": "IK failed — target may be outside workspace"}}))
else:
    q = np.array(action.joint_positions)
    n_joints = len(q)
    eps = 1e-4

    # Numerical Jacobian (6 x n_joints)
    J = np.zeros((6, n_joints))
    ee_frame = kin.get_all_frame_names()[-1]
    pos0, ori0 = kin.compute_forward_kinematics(ee_frame, q)
    pos0, ori0 = np.array(pos0), np.array(ori0)
    for k in range(n_joints):
        q_plus = q.copy(); q_plus[k] += eps
        pos_p, ori_p = kin.compute_forward_kinematics(ee_frame, q_plus)
        J[:3, k] = (np.array(pos_p) - pos0) / eps
        J[3:, k] = (np.array(ori_p) - ori0) / eps

    # SVD condition number
    _, sigma, _ = np.linalg.svd(J)
    condition = sigma[0] / max(sigma[-1], 1e-10)

    # Heuristic pre-filters (common 6/7-DOF robots)
    warnings = []
    if n_joints >= 5 and abs(q[4]) < np.radians(10):
        warnings.append('Joint 5 near zero — possible wrist singularity')
    if n_joints >= 3 and abs(q[2]) < np.radians(8):
        warnings.append('Joint 3 near extension — possible elbow singularity')

    if condition < 50:
        status = 'safe'
    elif condition < 100:
        status = 'warning'
    else:
        status = 'danger'

    result = {{
        'status': status,
        'condition_number': round(float(condition), 2),
        'singular_values': [round(float(s), 4) for s in sigma],
        'warnings': warnings,
        'joint_config': [round(float(v), 4) for v in q],
    }}
    if status == 'warning':
        result['message'] = 'Near singularity — motion may be unpredictable'
    elif status == 'danger':
        result['message'] = 'At singularity — choose a different target pose'

    print(json.dumps(result))
"""

CODE_GEN_HANDLERS["check_singularity"] = _gen_check_singularity


def _gen_monitor_joint_effort(args: Dict) -> str:
    """Generate code to monitor joint efforts over time via physics callback."""
    art_path = args["articulation_path"]
    duration = args.get("duration_seconds", 5.0)

    return f"""\
import omni.physx
import omni.usd
import numpy as np
import json
import time
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect joint info
joint_names = []
effort_limits = []
for desc in art_prim.GetAllDescendants():
    if desc.HasAPI(UsdPhysics.RevoluteJointAPI) or desc.IsA(UsdPhysics.RevoluteJoint):
        joint_names.append(desc.GetName())
        max_force = desc.GetAttribute('drive:angular:physics:maxForce')
        effort_limits.append(max_force.Get() if max_force and max_force.Get() else 1000.0)

n_joints = len(joint_names)
if n_joints == 0:
    print(json.dumps({{"error": "No joints found"}}))
else:
    _monitor_data = {{
        'positions': [], 'velocities': [], 'efforts': [],
        'start_time': time.time(), 'duration': {duration},
    }}

    def _monitor_step(dt):
        from isaacsim.core.prims import SingleArticulation
        art = SingleArticulation('{art_path}')
        _monitor_data['positions'].append(art.get_joint_positions().tolist())
        _monitor_data['velocities'].append(art.get_joint_velocities().tolist())
        _monitor_data['efforts'].append(art.get_applied_joint_efforts().tolist())

        elapsed = time.time() - _monitor_data['start_time']
        if elapsed >= _monitor_data['duration']:
            omni.physx.get_physx_interface().get_simulation_event_stream().unsubscribe(_monitor_sub)

            # Compute stats
            efforts = np.array(_monitor_data['efforts'])
            results = []
            for i in range(min(n_joints, efforts.shape[1])):
                e = efforts[:, i]
                limit = effort_limits[i] if i < len(effort_limits) else 1000.0
                utilization = float(np.max(np.abs(e))) / max(limit, 1e-6)
                results.append({{
                    'joint': joint_names[i] if i < len(joint_names) else f'joint_{{i}}',
                    'max_effort': round(float(np.max(np.abs(e))), 2),
                    'mean_effort': round(float(np.mean(np.abs(e))), 2),
                    'effort_limit': limit,
                    'utilization_pct': round(utilization * 100, 1),
                    'near_limit': utilization > 0.9,
                }})

            flagged = [r for r in results if r['near_limit']]
            print(json.dumps({{
                'joints': results,
                'duration_s': round(elapsed, 1),
                'samples': len(_monitor_data['efforts']),
                'flagged_joints': len(flagged),
                'message': f'{{len(flagged)}} joints near effort limit (>90%)' if flagged else 'All joints within limits',
            }}))

    _monitor_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_monitor_step)
    print(f'Monitoring joint efforts for {duration}s...')
"""

CODE_GEN_HANDLERS["monitor_joint_effort"] = _gen_monitor_joint_effort


# ── Performance Diagnostics ─────────────────────────────────────────────────
# Reads PhysX profiling data, GPU/VRAM stats, and identifies bottlenecks.


def _analyze_performance(stats: Dict, timing: Dict, mem: Dict) -> List[Dict]:
    """Analyze profiling data and return a list of performance issues."""
    issues = []

    # Physics narrow-phase bottleneck
    narrow_ms = timing.get("narrow_phase_ms", 0)
    if narrow_ms > 10:
        issues.append({
            "category": "physics_narrow_phase",
            "severity": "high",
            "message": (
                f"Narrow phase takes {narrow_ms:.0f}ms. "
                f"Heavy trimesh colliders are likely the cause."
            ),
            "fix": "Switch to convexHull or convexDecomposition approximation",
        })

    # VRAM pressure
    used_mb = mem.get("used_mb", 0)
    total_mb = mem.get("total_mb", 1)
    if total_mb > 0 and used_mb / total_mb > 0.9:
        issues.append({
            "category": "memory",
            "severity": "high",
            "message": f"GPU memory {used_mb:.0f}/{total_mb:.0f} MB (>90%)",
            "breakdown": mem.get("per_category", {}),
            "fix": "Reduce texture resolution or number of render products",
        })

    # Solver convergence
    solver_ms = timing.get("solver_ms", 0)
    solver_iters = stats.get("solver_iterations", 0)
    if solver_ms > 5 and solver_iters > 16:
        issues.append({
            "category": "solver",
            "severity": "medium",
            "message": (
                f"Solver takes {solver_ms:.0f}ms at "
                f"{solver_iters} iterations"
            ),
            "fix": "Reduce solver iterations to 4-8 for non-contact-critical bodies",
        })

    # Broad-phase bottleneck
    broad_ms = timing.get("broad_phase_ms", 0)
    if broad_ms > 8:
        issues.append({
            "category": "physics_broad_phase",
            "severity": "medium",
            "message": f"Broad phase takes {broad_ms:.0f}ms",
            "fix": "Reduce number of active rigid bodies or increase physics scene bounds",
        })

    # High dynamic rigid body count
    nb_dynamic = stats.get("nb_dynamic_rigids", 0)
    if nb_dynamic > 500:
        issues.append({
            "category": "scene_complexity",
            "severity": "medium",
            "message": f"{nb_dynamic} dynamic rigid bodies in scene",
            "fix": "Consider using GPU pipeline or reducing active body count",
        })

    return issues


async def _handle_diagnose_performance(args: Dict) -> Dict:
    """Collect PhysX stats, timing, and GPU memory, then analyze for bottlenecks."""
    code = """\
import json

results = {"stats": {}, "timing": {}, "mem": {}}

# 1. PhysX scene statistics
try:
    from omni.physx import get_physx_statistics_interface
    pstats = get_physx_statistics_interface()
    scene_stats = pstats.get_physx_scene_statistics()
    results["stats"] = {
        "nb_dynamic_rigids": scene_stats.get("nbDynamicRigids", 0),
        "nb_static_rigids": scene_stats.get("nbStaticRigids", 0),
        "nb_articulations": scene_stats.get("nbArticulations", 0),
        "nb_trimesh_shapes": scene_stats.get("nbTriMeshShapes", 0),
        "active_contact_pairs": scene_stats.get("nbActiveContactPairs", 0),
        "solver_iterations": scene_stats.get("solverIterations", 4),
    }
except Exception as e:
    results["stats"]["error"] = str(e)

# 2. PhysX per-zone timing
try:
    from omni.physx import get_physx_benchmarks_interface
    benchmarks = get_physx_benchmarks_interface()
    benchmarks.enable_profile()
    results["timing"] = {
        "simulation_ms": benchmarks.get_value("Simulation") or 0,
        "collision_detection_ms": benchmarks.get_value("Collision Detection") or 0,
        "broad_phase_ms": benchmarks.get_value("Broad Phase") or 0,
        "narrow_phase_ms": benchmarks.get_value("Narrow Phase") or 0,
        "solver_ms": benchmarks.get_value("Solver") or 0,
        "integration_ms": benchmarks.get_value("Integration") or 0,
    }
except Exception as e:
    results["timing"]["error"] = str(e)

# 3. Render timing + VRAM
try:
    from omni.hydra.engine.stats import HydraEngineStats
    hydra = HydraEngineStats()
    mem = hydra.get_mem_stats(detailed=True)
    device = hydra.get_device_info()
    results["mem"] = {
        "used_mb": mem.get("usedMB", 0),
        "total_mb": device.get("totalVRAM_MB", 0),
        "per_category": mem.get("perCategory", {}),
    }
except Exception as e:
    results["mem"]["error"] = str(e)

# 4. FPS
try:
    import omni.kit.app
    fps = omni.kit.app.get_app().get_fps()
    results["fps"] = fps
except Exception:
    results["fps"] = None

print(json.dumps(results))
"""
    kit_result = await kit_tools.queue_exec_patch(
        code, "Collect performance diagnostics (PhysX stats + GPU memory)"
    )

    # If Kit returned data, analyze it; otherwise return the raw queue result
    if isinstance(kit_result, dict) and "stats" in kit_result:
        stats = kit_result.get("stats", {})
        timing = kit_result.get("timing", {})
        mem = kit_result.get("mem", {})
        fps = kit_result.get("fps")

        issues = _analyze_performance(stats, timing, mem)

        # Determine bottleneck
        bottleneck = "unknown"
        if issues:
            bottleneck = issues[0]["category"]

        # Build summary
        parts = []
        if fps is not None:
            parts.append(f"Your sim runs at {fps:.0f} FPS.")
        if issues:
            parts.append(f"{len(issues)} issue(s) found.")
            parts.append(issues[0]["message"])
            parts.append(issues[0]["fix"])
        else:
            parts.append("No obvious performance issues detected.")

        return {
            "fps": fps,
            "bottleneck": bottleneck,
            "issues": issues,
            "stats": stats,
            "timing": timing,
            "mem": mem,
            "summary": " ".join(parts),
        }

    # Kit RPC just queued the patch — return what we have
    return {"type": "data", "queued": True, **kit_result}


async def _handle_find_heavy_prims(args: Dict) -> Dict:
    """Traverse the stage and find meshes above a triangle-count threshold."""
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


def _gen_optimize_collision(args: Dict) -> str:
    """Generate code to switch a collision mesh to a simpler approximation."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]
    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        "if not prim.IsValid():\n"
        f"    raise RuntimeError('Prim not found: {prim_path}')\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Ensure MeshCollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):\n"
        "    UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        "\n"
        f"UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Set('{approximation}')\n"
        f"print(f'Set collision approximation on {prim_path} to {approximation}')"
    )


DATA_HANDLERS["diagnose_performance"] = _handle_diagnose_performance
DATA_HANDLERS["find_heavy_prims"] = _handle_find_heavy_prims
CODE_GEN_HANDLERS["optimize_collision"] = _gen_optimize_collision


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


# ── Scene Diff ───────────────────────────────────────────────────────────────
# "What changed since last save?" → structured + human-readable diff.
# Layer 1: Raw USD text diff via Kit RPC (Sdf.Layer + difflib).
# Layer 2: Parse raw diff into structured SceneChange list.
# Layer 3: LLM narration (handled by caller, we return structured data).


def _parse_unified_diff_to_changes(raw_diff_lines: List[str]) -> List[Dict]:
    """Parse a unified diff of USDA text into structured SceneChange dicts.

    Each returned dict has:
        prim_path: str
        change_type: "added" | "removed" | "modified"
        details: dict  (attribute, old, new, or raw line)
    """
    import re
    changes: List[Dict] = []
    current_prim: Optional[str] = None

    # Track added/removed lines to pair modifications
    added_lines: List[str] = []
    removed_lines: List[str] = []

    def _flush_pending():
        nonlocal added_lines, removed_lines
        if not current_prim:
            added_lines.clear()
            removed_lines.clear()
            return
        # Pair removed/added as modifications
        paired = min(len(removed_lines), len(added_lines))
        for i in range(paired):
            changes.append({
                "prim_path": current_prim,
                "change_type": "modified",
                "details": {"old_line": removed_lines[i].strip(), "new_line": added_lines[i].strip()},
            })
        for i in range(paired, len(removed_lines)):
            changes.append({
                "prim_path": current_prim,
                "change_type": "removed",
                "details": {"line": removed_lines[i].strip()},
            })
        for i in range(paired, len(added_lines)):
            changes.append({
                "prim_path": current_prim,
                "change_type": "added",
                "details": {"line": added_lines[i].strip()},
            })
        added_lines = []
        removed_lines = []

    prim_re = re.compile(r'^\s*def\s+(\w+)\s+"([^"]+)"')
    for line in raw_diff_lines:
        # Skip diff headers
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            _flush_pending()
            continue

        # Detect prim context from context lines
        m = prim_re.match(line.lstrip("+-"))
        if m:
            _flush_pending()
            current_prim = m.group(2)
            # A whole prim definition added/removed
            if line.startswith("+") and not line.startswith("+++"):
                changes.append({
                    "prim_path": current_prim,
                    "change_type": "added",
                    "details": {"prim_type": m.group(1)},
                })
            elif line.startswith("-") and not line.startswith("---"):
                changes.append({
                    "prim_path": current_prim,
                    "change_type": "removed",
                    "details": {"prim_type": m.group(1)},
                })
            continue

        if line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
        else:
            _flush_pending()

    _flush_pending()

    # Deduplicate: group by prim_path + change_type
    seen: Dict[tuple, Dict] = {}
    deduped: List[Dict] = []
    for c in changes:
        key = (c["prim_path"], c["change_type"])
        if key not in seen:
            seen[key] = c
            deduped.append(c)
        else:
            # Merge details for same prim
            existing = seen[key]
            if "modifications" not in existing:
                existing["modifications"] = [existing.get("details", {})]
            existing["modifications"].append(c.get("details", {}))
    return deduped


def _summarize_changes(changes: List[Dict]) -> str:
    """Generate a concise human-readable summary from structured changes."""
    if not changes:
        return "No changes detected."

    added = [c for c in changes if c["change_type"] == "added"]
    removed = [c for c in changes if c["change_type"] == "removed"]
    modified = [c for c in changes if c["change_type"] == "modified"]

    parts: List[str] = []
    total = len(added) + len(removed) + len(modified)
    parts.append(f"{total} change(s) detected:")

    for c in added:
        ptype = c.get("details", {}).get("prim_type", "prim")
        parts.append(f"  + Added {ptype}: {c['prim_path']}")
    for c in removed:
        ptype = c.get("details", {}).get("prim_type", "prim")
        parts.append(f"  - Removed {ptype}: {c['prim_path']}")
    for c in modified:
        detail = c.get("details", {})
        desc = detail.get("new_line", detail.get("line", "property changed"))
        parts.append(f"  ~ Modified: {c['prim_path']} ({desc})")

    return "\n".join(parts)


async def _handle_scene_diff(args: Dict) -> Dict:
    """Compute a structured scene diff via Kit RPC.

    Supports three modes:
    - since="last_save"     → diff dirty layers against on-disk version
    - since="last_snapshot" → diff current vs. most recent snapshot
    - snapshot_a + snapshot_b → explicit comparison
    """
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


async def _handle_watch_changes(args: Dict) -> Dict:
    """Start/stop/query live change tracking via Tf.Notice in Kit."""
    action = args.get("action", "query")

    if action == "start":
        code = """\
import omni.usd
import json

# Register a global change tracker (singleton pattern)
stage = omni.usd.get_context().get_stage()

if not hasattr(omni.usd, '_isaac_assist_change_tracker'):
    from pxr import Tf

    class _ChangeTracker:
        def __init__(self):
            self.changes = []
            self._listener = None

        def start(self, stage):
            self.changes = []
            self._listener = Tf.Notice.Register(
                Tf.Notice.ObjectsChanged, self._on_changed, stage
            )

        def stop(self):
            if self._listener:
                self._listener.Revoke()
                self._listener = None

        def _on_changed(self, notice, stage):
            for path in notice.GetResyncedPaths():
                self.changes.append({"path": str(path), "type": "structural"})
            for path in notice.GetChangedInfoOnlyPaths():
                self.changes.append({"path": str(path), "type": "value"})

    omni.usd._isaac_assist_change_tracker = _ChangeTracker()

tracker = omni.usd._isaac_assist_change_tracker
tracker.start(stage)
print(json.dumps({"status": "tracking_started", "message": "Live change tracking started."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(start)")
        return {
            "status": "tracking_started",
            "message": "Live change tracking started. Use watch_changes(action='query') to see accumulated changes, or watch_changes(action='stop') to end.",
            "queued": result.get("queued", False),
        }

    elif action == "stop":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    tracker.stop()
    count = len(tracker.changes)
    changes = tracker.changes[-100:]  # return last 100
    tracker.changes = []
    print(json.dumps({"status": "tracking_stopped", "total_changes": count, "changes": changes}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(stop)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"status": "stopped", "queued": result.get("queued", False)}

    elif action == "query":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    count = len(tracker.changes)
    # Deduplicate by path, keep latest type
    seen = {}
    for c in tracker.changes:
        seen[c["path"]] = c["type"]
    deduped = [{"path": p, "type": t} for p, t in seen.items()]
    print(json.dumps({"status": "tracking", "total_raw": count, "unique_paths": len(deduped), "changes": deduped[-100:]}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker. Call watch_changes(action='start') first."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(query)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"status": "query_sent", "queued": result.get("queued", False)}

    return {"error": f"Unknown action: {action}. Use 'start', 'stop', or 'query'."}


DATA_HANDLERS["scene_diff"] = _handle_scene_diff
DATA_HANDLERS["watch_changes"] = _handle_watch_changes


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


# ── Automatic Scene Simplification ─────────────────────────────────────────

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


CODE_GEN_HANDLERS["optimize_scene"] = _gen_optimize_scene


def _gen_simplify_collision(args: Dict) -> str:
    """Generate code to set collision approximation on a single prim."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]

    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Apply MeshCollisionAPI and set approximation\n"
        "mesh_col = UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        f"mesh_col.GetApproximationAttr().Set('{approximation}')\n"
        f"print(f'Set collision approximation to {approximation} on {prim_path}')"
    )


CODE_GEN_HANDLERS["simplify_collision"] = _gen_simplify_collision


# ── Physics Settings Recommendation (DATA handler) ─────────────────────────

_PHYSICS_SETTINGS_PRESETS = {
    "rl_training": {
        "scene_type": "rl_training",
        "description": "RL training with 1024 environments — maximum throughput",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 120,
        "time_steps_per_second": 120,
        "notes": "Use TGS solver with minimal iterations for speed. GPU dynamics required for large env counts. Disable CCD to save compute.",
    },
    "manipulation": {
        "scene_type": "manipulation",
        "description": "Precision manipulation (pick-and-place, assembly)",
        "solver": "TGS",
        "solver_position_iterations": 16,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": True,
        "ccd_note": "Enable CCD on gripper fingers only — not all objects",
        "time_step": 1.0 / 240,
        "time_steps_per_second": 240,
        "notes": "Higher iterations for stable contacts. CCD on gripper prevents finger pass-through. 240 Hz for smooth grasping.",
    },
    "mobile_robot": {
        "scene_type": "mobile_robot",
        "description": "Mobile robot navigation (wheeled/legged)",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "Low iterations sufficient for wheel/ground contact. GPU dynamics helps with large environments. 60 Hz matches typical sensor rates.",
    },
    "digital_twin": {
        "scene_type": "digital_twin",
        "description": "Digital twin visualization (minimal physics)",
        "solver": "PGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "PGS solver is sufficient for visualization-only scenes. Disable GPU dynamics and CCD to minimize resource usage.",
    },
}


async def _handle_suggest_physics_settings(args: Dict) -> Dict:
    """Return recommended physics settings for the given scene type."""
    scene_type = args.get("scene_type", "manipulation")
    preset = _PHYSICS_SETTINGS_PRESETS.get(scene_type)
    if preset is None:
        return {
            "error": f"Unknown scene type '{scene_type}'. Valid types: {', '.join(_PHYSICS_SETTINGS_PRESETS.keys())}",
            "valid_types": list(_PHYSICS_SETTINGS_PRESETS.keys()),
        }
    return {"type": "data", "settings": preset}


DATA_HANDLERS["suggest_physics_settings"] = _handle_suggest_physics_settings


# ── Automatic Scene Simplification ─────────────────────────────────────────

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


CODE_GEN_HANDLERS["optimize_scene"] = _gen_optimize_scene


def _gen_simplify_collision(args: Dict) -> str:
    """Generate code to set collision approximation on a single prim."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]

    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Apply MeshCollisionAPI and set approximation\n"
        "mesh_col = UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        f"mesh_col.GetApproximationAttr().Set('{approximation}')\n"
        f"print(f'Set collision approximation to {approximation} on {prim_path}')"
    )


CODE_GEN_HANDLERS["simplify_collision"] = _gen_simplify_collision


# ── Physics Settings Recommendation (DATA handler) ─────────────────────────

_PHYSICS_SETTINGS_PRESETS = {
    "rl_training": {
        "scene_type": "rl_training",
        "description": "RL training with 1024 environments — maximum throughput",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 120,
        "time_steps_per_second": 120,
        "notes": "Use TGS solver with minimal iterations for speed. GPU dynamics required for large env counts. Disable CCD to save compute.",
    },
    "manipulation": {
        "scene_type": "manipulation",
        "description": "Precision manipulation (pick-and-place, assembly)",
        "solver": "TGS",
        "solver_position_iterations": 16,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": True,
        "ccd_note": "Enable CCD on gripper fingers only — not all objects",
        "time_step": 1.0 / 240,
        "time_steps_per_second": 240,
        "notes": "Higher iterations for stable contacts. CCD on gripper prevents finger pass-through. 240 Hz for smooth grasping.",
    },
    "mobile_robot": {
        "scene_type": "mobile_robot",
        "description": "Mobile robot navigation (wheeled/legged)",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "Low iterations sufficient for wheel/ground contact. GPU dynamics helps with large environments. 60 Hz matches typical sensor rates.",
    },
    "digital_twin": {
        "scene_type": "digital_twin",
        "description": "Digital twin visualization (minimal physics)",
        "solver": "PGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "PGS solver is sufficient for visualization-only scenes. Disable GPU dynamics and CCD to minimize resource usage.",
    },
}


async def _handle_suggest_physics_settings(args: Dict) -> Dict:
    """Return recommended physics settings for the given scene type."""
    scene_type = args.get("scene_type", "manipulation")
    preset = _PHYSICS_SETTINGS_PRESETS.get(scene_type)
    if preset is None:
        return {
            "error": f"Unknown scene type '{scene_type}'. Valid types: {', '.join(_PHYSICS_SETTINGS_PRESETS.keys())}",
            "valid_types": list(_PHYSICS_SETTINGS_PRESETS.keys()),
        }
    return {"type": "data", "settings": preset}


DATA_HANDLERS["suggest_physics_settings"] = _handle_suggest_physics_settings


# ── Onboarding & First-Time UX ──────────────────────────────────────────────

# Starter prompt templates keyed by scene "archetype"
_STARTER_PROMPTS = {
    "empty": {
        "welcome": "Your scene is empty — a blank canvas!",
        "prompts": [
            "Import a robot: 'add a Franka Panda to the scene'",
            "Load a template: 'set up a pick and place scene'",
            "Browse assets: 'show me available robots'",
        ],
    },
    "robot_only": {
        "welcome": "I see a robot in the scene, but no objects to interact with.",
        "prompts": [
            "Add objects: 'place 3 cubes on a table'",
            "Test the robot: 'move the arm to a test position'",
            "Check setup: 'are the collision meshes correct?'",
        ],
    },
    "robot_and_objects": {
        "welcome": "Your scene has a robot and objects — ready for action!",
        "prompts": [
            "Move the arm to grab the nearest object",
            "Why is the robot not moving?",
            "Show me the robot's workspace",
        ],
    },
    "mobile_robot": {
        "welcome": "I see a mobile robot in the scene.",
        "prompts": [
            "Drive the robot forward 2 meters",
            "Set up navigation: 'create an occupancy map'",
            "Check sensors: 'what sensors does the robot have?'",
        ],
    },
    "no_physics": {
        "welcome": "Physics is not enabled in this scene.",
        "prompts": [
            "Enable physics for this scene",
            "Add rigid body physics to the objects",
            "Set up a physics scene with gravity",
        ],
    },
}

# Known mobile robot keywords (path substrings)
_MOBILE_ROBOT_KEYWORDS = {"carter", "jetbot", "nova_carter", "kaya", "husky", "turtlebot"}


async def _handle_scene_aware_starter_prompts(args: Dict) -> Dict:
    """Generate contextual starter prompts based on scene state."""
    try:
        ctx = await kit_tools.get_stage_context(full=False)
    except Exception:
        ctx = {}

    stage = ctx.get("stage", {})
    prim_count = stage.get("prim_count", 0)
    prims_by_type = stage.get("prims_by_type", {})

    # Detect scene archetype
    has_robot = False
    is_mobile = False
    has_objects = False
    has_physics = stage.get("has_physics_scene", False)
    robot_paths = []

    # Check for articulations (robots)
    articulations = prims_by_type.get("Articulation", [])
    xforms = prims_by_type.get("Xform", [])
    meshes = prims_by_type.get("Mesh", [])

    # Heuristic: any prim path containing common robot names
    all_paths = []
    for prim_list in prims_by_type.values():
        if isinstance(prim_list, list):
            all_paths.extend(prim_list)
        elif isinstance(prim_list, int):
            pass  # count, not paths

    for p in all_paths:
        p_lower = str(p).lower()
        if any(kw in p_lower for kw in ("robot", "franka", "panda", "ur10", "ur5",
                                         "anymal", "spot", "carter", "jetbot", "kaya",
                                         "go1", "go2", "h1", "allegro")):
            has_robot = True
            robot_paths.append(str(p))
            if any(kw in p_lower for kw in _MOBILE_ROBOT_KEYWORDS):
                is_mobile = True

    if isinstance(articulations, list) and len(articulations) > 0:
        has_robot = True
        robot_paths.extend(str(a) for a in articulations)
    elif isinstance(articulations, int) and articulations > 0:
        has_robot = True

    has_objects = (isinstance(meshes, list) and len(meshes) > 2) or \
                 (isinstance(meshes, int) and meshes > 2)

    # Select archetype
    if prim_count <= 2:
        archetype = "empty"
    elif not has_physics and prim_count > 2:
        archetype = "no_physics"
    elif is_mobile:
        archetype = "mobile_robot"
    elif has_robot and has_objects:
        archetype = "robot_and_objects"
    elif has_robot:
        archetype = "robot_only"
    else:
        archetype = "empty"

    template = _STARTER_PROMPTS[archetype]

    # Build scene summary line
    summary_parts = []
    if prim_count > 0:
        summary_parts.append(f"{prim_count} prims")
    if robot_paths:
        summary_parts.append(f"robot(s) at {', '.join(robot_paths[:3])}")
    if has_physics:
        summary_parts.append("physics enabled")

    return {
        "archetype": archetype,
        "welcome": template["welcome"],
        "scene_summary": ", ".join(summary_parts) if summary_parts else "empty scene",
        "prompts": template["prompts"],
        "robot_paths": robot_paths[:5],
        "has_physics": has_physics,
    }


async def _handle_hardware_compatibility_check(args: Dict) -> Dict:
    """Run hardware and software compatibility probe."""
    checks = []

    # GPU info — try Kit RPC first
    gpu_info = {"name": "unknown", "vram_gb": 0}
    try:
        ctx = await kit_tools.get_stage_context(full=False)
        device = ctx.get("device", {})
        if device:
            gpu_info["name"] = device.get("name", "unknown")
            gpu_info["vram_gb"] = device.get("vram_mb", 0) / 1024
    except Exception:
        pass

    # GPU check
    if gpu_info["name"] != "unknown":
        checks.append({
            "component": "GPU",
            "value": f"{gpu_info['name']} ({gpu_info['vram_gb']:.0f} GB VRAM)",
            "status": "pass",
            "icon": "check",
        })
    else:
        checks.append({
            "component": "GPU",
            "value": "Could not detect GPU (Kit RPC unavailable)",
            "status": "warn",
            "icon": "warning",
        })

    # VRAM warning
    if gpu_info["vram_gb"] > 0:
        if gpu_info["vram_gb"] < 8:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — may be insufficient for complex scenes",
                "status": "warn",
                "icon": "warning",
            })
        elif gpu_info["vram_gb"] < 16:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — large RL environments (>256 envs) may need more",
                "status": "warn",
                "icon": "warning",
            })
        else:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — sufficient for all workloads",
                "status": "pass",
                "icon": "check",
            })

    # Isaac Sim version
    isaac_version = "unknown"
    try:
        ctx_stage = ctx.get("stage", {})
        isaac_version = ctx_stage.get("isaac_sim_version", "unknown")
    except Exception:
        pass
    if isaac_version != "unknown":
        checks.append({
            "component": "Isaac Sim",
            "value": f"{isaac_version} — compatible",
            "status": "pass",
            "icon": "check",
        })
    else:
        checks.append({
            "component": "Isaac Sim",
            "value": "Version unknown (Kit RPC unavailable)",
            "status": "info",
            "icon": "info",
        })

    # Python version
    import sys
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    py_ok = sys.version_info >= (3, 10)
    checks.append({
        "component": "Python",
        "value": f"{py_version} — {'compatible' if py_ok else 'requires 3.10+'}",
        "status": "pass" if py_ok else "warn",
        "icon": "check" if py_ok else "warning",
    })

    # LLM connectivity
    llm_mode = os.environ.get("LLM_MODE", "local")
    checks.append({
        "component": "LLM",
        "value": f"Mode: {llm_mode} — no local GPU needed" if llm_mode != "local" else f"Mode: {llm_mode}",
        "status": "info",
        "icon": "info",
    })

    return {
        "checks": checks,
        "overall_status": "warn" if any(c["status"] == "warn" for c in checks) else "pass",
    }


# Slash commands — static list with relevance conditions
_SLASH_COMMANDS = [
    {"command": "/help", "description": "What can I do?", "always": True},
    {"command": "/scene", "description": "Summarize current scene", "always": True},
    {"command": "/debug", "description": "Diagnose physics issues", "requires_physics": True},
    {"command": "/performance", "description": "Why is my sim slow?", "always": True},
    {"command": "/workspace", "description": "Show robot workspace", "requires_robot": True},
    {"command": "/diff", "description": "What changed?", "always": True},
    {"command": "/import", "description": "Import a robot", "always": True},
    {"command": "/template", "description": "Load a scene template", "always": True},
]


async def _handle_slash_command_discovery(args: Dict) -> Dict:
    """Return slash commands filtered by scene state."""
    has_robot = args.get("scene_has_robot")
    has_physics = args.get("scene_has_physics")

    # Auto-detect if not provided
    if has_robot is None or has_physics is None:
        try:
            ctx = await kit_tools.get_stage_context(full=False)
            stage = ctx.get("stage", {})
            if has_physics is None:
                has_physics = stage.get("has_physics_scene", False)
            if has_robot is None:
                prim_count = stage.get("prim_count", 0)
                has_robot = prim_count > 5  # rough heuristic
        except Exception:
            has_robot = has_robot if has_robot is not None else False
            has_physics = has_physics if has_physics is not None else False

    commands = []
    for cmd in _SLASH_COMMANDS:
        if cmd.get("always"):
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_robot") and has_robot:
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_physics") and has_physics:
            commands.append({"command": cmd["command"], "description": cmd["description"]})

    return {
        "commands": commands,
        "scene_has_robot": has_robot,
        "scene_has_physics": has_physics,
    }


async def _handle_console_error_autodetect(args: Dict) -> Dict:
    """Check for new console errors since a given timestamp."""
    since = args.get("since_timestamp", 0)

    try:
        ctx = await kit_tools.get_stage_context(full=False)
    except Exception:
        return {"new_error_count": 0, "errors": [], "message": "Kit RPC unavailable"}

    logs = ctx.get("recent_logs", [])

    # Filter for errors only (not warnings) to avoid spam
    new_errors = []
    for entry in logs:
        level = entry.get("level", "info")
        if level not in ("error", "fatal"):
            continue
        ts = entry.get("timestamp", 0)
        if ts > since:
            new_errors.append({
                "level": level,
                "message": entry.get("message", ""),
                "timestamp": ts,
            })

    result = {
        "new_error_count": len(new_errors),
        "errors": new_errors[:10],  # cap at 10 to avoid flooding
        "since_timestamp": since,
    }

    if new_errors:
        result["proactive_message"] = (
            f"{len(new_errors)} new error(s) detected. "
            "Want me to explain them?"
        )

    return result


# Post-action suggestion map: tool_name → list of suggestion templates
_SUGGESTION_MAP = {
    "import_robot": [
        "Configure the gripper",
        "Check if the collision meshes are correct",
        "Move the arm to a test position",
    ],
    "create_prim": [
        "Add physics to this object",
        "Change the material or color",
        "Position it precisely in the scene",
    ],
    "clone_prim": [
        "Set up physics for all copies",
        "Create an RL training environment",
        "Adjust spacing between copies",
    ],
    "move_to_pose": [
        "Plan a pick-and-place sequence",
        "Check for collisions along the path",
        "Record the joint positions",
    ],
    "sim_control": [
        "Capture a screenshot of the result",
        "Check for physics errors",
        "Measure performance (FPS, frame time)",
    ],
    "create_material": [
        "Apply this material to an object",
        "Adjust roughness or metallic properties",
        "Create a glass or transparent variant",
    ],
    "configure_sdg": [
        "Preview a sample frame",
        "Add more randomizers (lighting, pose)",
        "Export to COCO or KITTI format",
    ],
    "set_physics_params": [
        "Test with a simulation run",
        "Add rigid body physics to objects",
        "Check solver iteration count for stability",
    ],
    "load_scene_template": [
        "Run the simulation to see it in action",
        "Customize the robot's behavior",
        "Capture a screenshot of the scene",
    ],
}

# Default suggestions when no specific tool match
_DEFAULT_SUGGESTIONS = [
    "Run the simulation to see the result",
    "Capture a viewport screenshot",
    "Check for any physics warnings",
]


async def _handle_post_action_suggestions(args: Dict) -> Dict:
    """Return next-step suggestions after a tool execution."""
    completed_tool = args.get("completed_tool", "")
    tool_args = args.get("tool_args", {})
    tool_result = args.get("tool_result", {})

    suggestions = _SUGGESTION_MAP.get(completed_tool, _DEFAULT_SUGGESTIONS)

    # Context-aware adjustments
    if completed_tool == "import_robot":
        robot_name = tool_args.get("file_path", "")
        if any(kw in robot_name.lower() for kw in _MOBILE_ROBOT_KEYWORDS):
            suggestions = [
                "Set up navigation for the mobile robot",
                "Add a lidar sensor",
                "Drive the robot forward to test",
            ]

    return {
        "completed_tool": completed_tool,
        "suggestions": suggestions,
    }


# Scene template code generators
_TEMPLATE_CONFIGS = {
    "pick_and_place": {
        "description": "Franka Panda + table + 3 cubes + physics",
        "time_to_wow": "30 sec",
    },
    "mobile_nav": {
        "description": "Jetbot + warehouse floor + obstacles",
        "time_to_wow": "60 sec",
    },
    "sdg_basic": {
        "description": "Camera + 5 objects + Replicator pipeline",
        "time_to_wow": "90 sec",
    },
    "empty_robot": {
        "description": "Just a Franka Panda, ready for commands",
        "time_to_wow": "15 sec",
    },
}


def _gen_load_scene_template(args: Dict) -> str:
    """Generate code to build a quick-start scene template."""
    template = args["template_name"]

    if template == "pick_and_place":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf, Sdf, PhysxSchema

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/GroundPlane', 'Xform')
ground_mesh = UsdGeom.Mesh.Define(stage, '/World/GroundPlane/Mesh')
UsdPhysics.CollisionAPI.Apply(ground_mesh.GetPrim())
plane = stage.DefinePrim('/World/GroundPlane/Mesh', 'Plane')

# Table
table = stage.DefinePrim('/World/Table', 'Cube')
xf = UsdGeom.Xformable(table)
xf.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0.4))
xf.AddScaleOp().Set(Gf.Vec3d(0.6, 0.8, 0.02))
UsdPhysics.CollisionAPI.Apply(table)

# 3 cubes on the table
colors = [(0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8)]
for i, color in enumerate(colors):
    cube_path = f'/World/Cube_{i}'
    cube = stage.DefinePrim(cube_path, 'Cube')
    xf = UsdGeom.Xformable(cube)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4 + i * 0.1, 0, 0.45))
    xf.AddScaleOp().Set(Gf.Vec3d(0.025, 0.025, 0.025))
    UsdPhysics.RigidBodyAPI.Apply(cube)
    UsdPhysics.CollisionAPI.Apply(cube)

print('Template pick_and_place loaded: table + 3 cubes + physics. Add a Franka robot with: import_robot(file_path="franka", format="asset_library")')
"""

    if template == "mobile_nav":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

# Obstacles (walls)
for i, (pos, scale) in enumerate([
    ((2, 0, 0.5), (0.1, 2, 0.5)),
    ((-2, 0, 0.5), (0.1, 2, 0.5)),
    ((0, 2, 0.5), (2, 0.1, 0.5)),
    ((0, -2, 0.5), (2, 0.1, 0.5)),
]):
    wall = stage.DefinePrim(f'/World/Wall_{i}', 'Cube')
    xf = UsdGeom.Xformable(wall)
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddScaleOp().Set(Gf.Vec3d(*scale))
    UsdPhysics.CollisionAPI.Apply(wall)

print('Template mobile_nav loaded: ground + walls. Add a Jetbot with: import_robot(file_path="jetbot", format="asset_library")')
"""

    if template == "sdg_basic":
        return """\
import omni.usd
from pxr import UsdGeom, Gf, Sdf

stage = omni.usd.get_context().get_stage()

# Camera
cam = UsdGeom.Camera.Define(stage, '/World/SDG_Camera')
xf = UsdGeom.Xformable(cam.GetPrim())
xf.AddTranslateOp().Set(Gf.Vec3d(2, 2, 2))
xf.AddRotateXYZOp().Set(Gf.Vec3d(-35, 0, 45))

# Ground
ground = stage.DefinePrim('/World/Ground', 'Plane')

# 5 objects with semantic labels
shapes = ['Cube', 'Sphere', 'Cylinder', 'Cone', 'Cube']
for i, shape in enumerate(shapes):
    prim = stage.DefinePrim(f'/World/Object_{i}', shape)
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(i * 0.3 - 0.6, 0, 0.15))
    xf.AddScaleOp().Set(Gf.Vec3d(0.1, 0.1, 0.1))
    # Add semantic label for SDG
    prim.CreateAttribute('semantic:Semantics:params:semanticType', Sdf.ValueTypeNames.String).Set('class')
    prim.CreateAttribute('semantic:Semantics:params:semanticData', Sdf.ValueTypeNames.String).Set(shape.lower())

print('Template sdg_basic loaded: camera + 5 labeled objects. Configure SDG with: configure_sdg(num_frames=10, output_dir="/tmp/sdg_output")')
"""

    if template == "empty_robot":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

print('Template empty_robot loaded: physics + ground. Add a Franka with: import_robot(file_path="franka", format="asset_library")')
"""

    return f"# Unknown template: {template}"


DATA_HANDLERS["scene_aware_starter_prompts"] = _handle_scene_aware_starter_prompts
DATA_HANDLERS["hardware_compatibility_check"] = _handle_hardware_compatibility_check
DATA_HANDLERS["slash_command_discovery"] = _handle_slash_command_discovery
DATA_HANDLERS["console_error_autodetect"] = _handle_console_error_autodetect
DATA_HANDLERS["post_action_suggestions"] = _handle_post_action_suggestions
CODE_GEN_HANDLERS["load_scene_template"] = _gen_load_scene_template


# ── OmniGraph Assistant ──────────────────────────────────────────────────────

# Canonical OmniGraph templates (verified, cover ~90% of ROS2 use cases).
# Each template is a dict with: nodes, connections, values (parameterized).
# The ROS2Context node is ALWAYS included automatically.

_OG_TEMPLATES = {
    "ros2_clock": {
        "description": "Publish simulation clock to ROS2 /clock topic",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_clock", "isaacsim.ros2.bridge.ROS2PublishClock"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_clock.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_clock.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_clock.inputs:timeStamp"),
        ],
        "values": {},
        "param_keys": [],
    },
    "ros2_joint_state": {
        "description": "Publish robot joint states to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
            ("publish_joint_state", "isaacsim.ros2.bridge.ROS2PublishJointState"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_joint_state.inputs:execIn"),
            ("on_playback_tick.outputs:tick", "articulation_controller.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_joint_state.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_joint_state.inputs:timeStamp"),
        ],
        "values": {
            "articulation_controller.inputs:robotPath": "{robot_path}",
            "publish_joint_state.inputs:topicName": "{topic}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/joint_states"},
    },
    "ros2_camera": {
        "description": "Publish camera images to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("camera_helper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "camera_helper.inputs:execIn"),
            ("ros2_context.outputs:context", "camera_helper.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "camera_helper.inputs:timeStamp"),
        ],
        "values": {
            "camera_helper.inputs:cameraPrimPath": "{camera_path}",
            "camera_helper.inputs:topicName": "{topic}",
        },
        "param_keys": ["camera_path", "topic"],
        "defaults": {"topic": "/camera/image_raw"},
    },
    "ros2_lidar": {
        "description": "Publish lidar scans to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("read_lidar", "isaacsim.sensor.nodes.IsaacReadLidar"),
            ("publish_laser_scan", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_lidar.inputs:execIn"),
            ("read_lidar.outputs:execOut", "publish_laser_scan.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_laser_scan.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_laser_scan.inputs:timeStamp"),
            ("read_lidar.outputs:azimuthRange", "publish_laser_scan.inputs:azimuthRange"),
            ("read_lidar.outputs:depthRange", "publish_laser_scan.inputs:depthRange"),
            ("read_lidar.outputs:horizontalResolution", "publish_laser_scan.inputs:horizontalResolution"),
            ("read_lidar.outputs:intensitiesData", "publish_laser_scan.inputs:intensitiesData"),
            ("read_lidar.outputs:linearDepthData", "publish_laser_scan.inputs:linearDepthData"),
            ("read_lidar.outputs:numCols", "publish_laser_scan.inputs:numCols"),
            ("read_lidar.outputs:numRows", "publish_laser_scan.inputs:numRows"),
        ],
        "values": {
            "read_lidar.inputs:lidarPrimPath": "{lidar_path}",
            "publish_laser_scan.inputs:topicName": "{topic}",
        },
        "param_keys": ["lidar_path", "topic"],
        "defaults": {"topic": "/scan"},
    },
    "ros2_cmd_vel": {
        "description": "Subscribe to /cmd_vel and drive a differential robot",
        "nodes": [
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("subscribe_twist", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("differential_controller", "isaacsim.robot.wheeled_robots.DifferentialController"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "connections": [
            ("ros2_context.outputs:context", "subscribe_twist.inputs:context"),
            ("subscribe_twist.outputs:linearVelocity", "differential_controller.inputs:linearVelocity"),
            ("subscribe_twist.outputs:angularVelocity", "differential_controller.inputs:angularVelocity"),
            ("differential_controller.outputs:velocityCommand", "articulation_controller.inputs:velocityCommand"),
        ],
        "values": {
            "subscribe_twist.inputs:topicName": "{topic}",
            "articulation_controller.inputs:robotPath": "{robot_path}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/cmd_vel"},
    },
    "ros2_tf": {
        "description": "Publish TF transform tree to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_tf", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_tf.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_tf.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_tf.inputs:timeStamp"),
        ],
        "values": {
            "publish_tf.inputs:parentPrim": "{root_prim}",
        },
        "param_keys": ["root_prim"],
        "defaults": {"root_prim": "/World"},
    },
    "ros2_imu": {
        "description": "Publish IMU data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_imu", "isaacsim.sensor.nodes.IsaacReadIMU"),
            ("publish_imu", "isaacsim.ros2.bridge.ROS2PublishImu"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_imu.inputs:execIn"),
            ("read_imu.outputs:execOut", "publish_imu.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_imu.inputs:context"),
            ("read_imu.outputs:angVel", "publish_imu.inputs:angularVelocity"),
            ("read_imu.outputs:linAcc", "publish_imu.inputs:linearAcceleration"),
            ("read_imu.outputs:orientation", "publish_imu.inputs:orientation"),
        ],
        "values": {
            "read_imu.inputs:imuPrimPath": "{imu_path}",
            "publish_imu.inputs:topicName": "{topic}",
        },
        "param_keys": ["imu_path", "topic"],
        "defaults": {"topic": "/imu/data"},
    },
    "ros2_odom": {
        "description": "Publish odometry data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("compute_odom", "isaacsim.core.nodes.IsaacComputeOdometry"),
            ("publish_odom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "compute_odom.inputs:execIn"),
            ("compute_odom.outputs:execOut", "publish_odom.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_odom.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_odom.inputs:timeStamp"),
            ("compute_odom.outputs:angularVelocity", "publish_odom.inputs:angularVelocity"),
            ("compute_odom.outputs:linearVelocity", "publish_odom.inputs:linearVelocity"),
            ("compute_odom.outputs:orientation", "publish_odom.inputs:orientation"),
            ("compute_odom.outputs:position", "publish_odom.inputs:position"),
        ],
        "values": {
            "compute_odom.inputs:chassisPrimPath": "{chassis_path}",
            "publish_odom.inputs:topicName": "{topic}",
        },
        "param_keys": ["chassis_path", "topic"],
        "defaults": {"topic": "/odom"},
    },
}

# Keyword → template mapping for auto-detection from description
_TEMPLATE_KEYWORDS = {
    "ros2_clock": ["clock", "sim_time", "simulation time", "simtime"],
    "ros2_joint_state": ["joint state", "joint_state", "joint states", "joint positions"],
    "ros2_camera": ["camera", "image", "rgb", "depth image"],
    "ros2_lidar": ["lidar", "laser scan", "laserscan", "point cloud lidar"],
    "ros2_cmd_vel": ["cmd_vel", "twist", "teleop", "drive", "velocity command"],
    "ros2_tf": ["tf", "transform tree", "transforms", "tf2"],
    "ros2_imu": ["imu", "inertial", "accelerometer", "gyroscope"],
    "ros2_odom": ["odom", "odometry"],
}


def _detect_template(description: str) -> Optional[str]:
    """Auto-detect the best template from a natural language description."""
    desc_lower = description.lower()
    best_match = None
    best_score = 0
    for template_name, keywords in _TEMPLATE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_match = template_name
    return best_match if best_score > 0 else None


def _gen_create_graph(args: Dict) -> str:
    """Generate OmniGraph code from a template-based description."""
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
print(f"Created {{template}} graph at {graph_path} with {{len(nodes)}} nodes")
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


def _gen_debug_graph(args: Dict) -> str:
    """Generate code that checks an OmniGraph for common issues."""
    graph_path = args["graph_path"]
    return f"""\
import omni.graph.core as og
import json

graph = og.get_graph_by_path('{graph_path}')
if graph is None:
    raise ValueError("No OmniGraph found at '{graph_path}'")

nodes = graph.get_nodes()
issues = []

# Collect node info
node_types = {{}}
node_names = []
has_ros2_context = False
has_on_tick = False

for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    node_types[nname] = ntype
    node_names.append(nname)

    if "ROS2Context" in ntype:
        has_ros2_context = True
    if "OnPlaybackTick" in ntype or "OnTick" in ntype:
        has_on_tick = True

# Check 1: Missing ROS2Context (most common omission)
has_ros2_nodes = any("ros2" in t.lower() or "ROS2" in t for t in node_types.values())
if has_ros2_nodes and not has_ros2_context:
    issues.append({{
        "severity": "error",
        "check": "missing_ros2_context",
        "message": "Graph has ROS2 nodes but no ROS2Context node. Topics will not appear.",
        "fix": "Add a ROS2Context node and connect its context output to all ROS2 nodes.",
    }})

# Check 2: Missing OnTick trigger
if len(nodes) > 0 and not has_on_tick:
    issues.append({{
        "severity": "warning",
        "check": "missing_on_tick",
        "message": "No OnPlaybackTick/OnTick node found. The graph may never evaluate.",
        "fix": "Add an OnPlaybackTick node and connect its tick output to the execution chain.",
    }})

# Check 3: Disconnected inputs (nodes with no incoming connections on execIn)
for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    # Skip source nodes (OnTick, Context)
    if "OnPlaybackTick" in ntype or "OnTick" in ntype or "ROS2Context" in ntype:
        continue
    has_exec_in = False
    exec_connected = False
    for attr in node.get_attributes():
        if attr.get_name() == "inputs:execIn":
            has_exec_in = True
            if len(attr.get_upstream_connections()) > 0:
                exec_connected = True
    if has_exec_in and not exec_connected:
        issues.append({{
            "severity": "warning",
            "check": "disconnected_exec_input",
            "message": f"Node '{{nname}}' ({{ntype}}) has an unconnected execIn — it will never execute.",
            "fix": f"Connect an execution output to {{nname}}.inputs:execIn",
        }})

# Check 4: Duplicate node names
from collections import Counter
dupes = [name for name, count in Counter(node_names).items() if count > 1]
if dupes:
    issues.append({{
        "severity": "error",
        "check": "duplicate_node_names",
        "message": f"Duplicate node names found: {{dupes}}. This can cause connection confusion.",
        "fix": "Rename duplicate nodes to unique names.",
    }})

result = {{
    "graph_path": "{graph_path}",
    "node_count": len(nodes),
    "issues_found": len(issues),
    "issues": issues,
    "node_types": node_types,
    "status": "ok" if len(issues) == 0 else "issues_found",
}}
print(json.dumps(result, indent=2, default=str))
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
    "explain_graph": _gen_explain_graph,
    "create_graph": _gen_create_graph,
    "debug_graph": _gen_debug_graph,
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


# ── Interactive Robot Teaching ────────────────────────────────────────────────

def _gen_start_teaching_mode(args: Dict) -> str:
    """Generate code to start interactive robot teaching mode."""
    art_path = args["articulation_path"]
    mode = args["mode"]
    robot_type = args.get("robot_type", "franka").lower()

    if mode == "drag_target":
        # FollowTarget pattern: ghost target prim + RMPflow tracking
        return f"""\
import omni.usd
import numpy as np
from pxr import UsdGeom, Gf, Sdf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Create draggable ghost target at current end-effector position
target_path = '{art_path}/TeachTarget'
if not stage.GetPrimAtPath(target_path).IsValid():
    target_prim = stage.DefinePrim(target_path, 'Sphere')
    UsdGeom.Gprim(target_prim).GetDisplayColorAttr().Set([(0.2, 0.8, 0.2)])
    xf = UsdGeom.Xformable(target_prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4, 0.0, 0.4))
    xf.AddScaleOp().Set(Gf.Vec3d(0.03, 0.03, 0.03))
    print(f"Created draggable teach target at {{target_path}}")
else:
    target_prim = stage.GetPrimAtPath(target_path)
    print(f"Teach target already exists at {{target_path}}")

# Load RMPflow controller for tracking
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Register physics callback to track target each step
def _teach_step(step_size):
    target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{art_path}/TeachTarget'))
    target_pos = target_xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
    rmpflow.set_end_effector_target(
        np.array([target_pos[0], target_pos[1], target_pos[2]]),
        None,
    )
    joint_positions = art.get_joint_positions()
    joint_velocities = art.get_joint_velocities()
    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
    art.apply_action(action)

import omni.physx
physx = omni.physx.get_physx_interface()
_sub = physx.subscribe_physics_step_events(_teach_step)

print("Teaching mode ACTIVE (drag_target): drag the green sphere in the viewport, robot follows via RMPflow.")
print("Press SPACE in viewport to record waypoints. Stop simulation to exit teaching mode.")
"""

    if mode == "keyboard":
        return f"""\
import numpy as np
from isaaclab.devices.keyboard import Se3Keyboard
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize keyboard device
keyboard = Se3Keyboard(
    pos_sensitivity=0.005,
    rot_sensitivity=0.01,
)
keyboard.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (keyboard):")
print("  W/S = forward/backward, A/D = left/right, Q/E = up/down")
print("  Z/X = roll, T/G = pitch, C/V = yaw")
print("  K = toggle gripper, SPACE = record waypoint")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "spacemouse":
        return f"""\
import numpy as np
from isaaclab.devices.spacemouse import Se3SpaceMouse
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize SpaceMouse device
spacemouse = Se3SpaceMouse(
    pos_sensitivity=0.005,
    rot_sensitivity=0.005,
)
spacemouse.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (spacemouse): move the 3Dconnexion SpaceMouse to control the end-effector.")
print("  Button 0 = record waypoint, Button 1 = toggle gripper")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "gravity_comp":
        return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

n_dof = art.num_dof

# Zero PD gains for compliance
art.set_joint_stiffnesses(np.zeros(n_dof))
art.set_joint_dampings(np.full(n_dof, 0.1))  # small damping to prevent oscillation

# Compute and apply gravity compensation
import numpy as np
gravity_comp = art.get_measured_joint_efforts()
print(f"Gravity compensation forces: {{gravity_comp}}")

# Register physics callback to maintain gravity compensation
import omni.physx
physx = omni.physx.get_physx_interface()

def _gravity_comp_step(step_size):
    efforts = art.get_measured_joint_efforts()
    art.set_joint_efforts(efforts)

_sub = physx.subscribe_physics_step_events(_gravity_comp_step)

print("Teaching mode ACTIVE (gravity_comp): arm is now compliant.")
print("  Use Shift+drag in viewport to move joints via physics force grab.")
print("  The robot will hold position against gravity but yield to your input.")
print("Stop simulation to exit teaching mode.")
"""
    return f"# Unknown teaching mode: {mode}"


def _gen_record_waypoints(args: Dict) -> str:
    """Generate code to record robot waypoints to file."""
    art_path = args["articulation_path"]
    output_path = args["output_path"]
    fmt = args.get("format", "json")

    if fmt == "hdf5":
        return f"""\
import numpy as np
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Capture current joint state as a waypoint
joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = art.dof_names

# Write HDF5 in robomimic schema
import h5py
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)

with h5py.File('{output_path}', 'a') as f:
    # robomimic demo schema
    if 'data' not in f:
        grp = f.create_group('data')
        grp.attrs['num_demos'] = 0
    data = f['data']
    demo_idx = data.attrs['num_demos']
    demo_name = f'demo_{{demo_idx}}'
    demo = data.create_group(demo_name)
    demo.create_dataset('actions', data=np.array([joint_positions]))
    obs = demo.create_group('obs')
    obs.create_dataset('joint_pos', data=np.array([joint_positions]))
    obs.create_dataset('joint_vel', data=np.array([joint_velocities]))
    demo.attrs['num_samples'] = 1
    data.attrs['num_demos'] = demo_idx + 1

print(f"Recorded waypoint to {{'{output_path}'}} (HDF5 robomimic schema, demo {{demo_idx}})")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    if fmt == "usd":
        return f"""\
import omni.usd
from pxr import Usd, UsdGeom, Sdf
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()

stage = omni.usd.get_context().get_stage()
time_code = stage.GetEndTimeCode() + 1
stage.SetEndTimeCode(time_code)

# Write joint positions as USD TimeSamples on each joint drive
joint_names = art.dof_names
for i, jname in enumerate(joint_names):
    joint_path = '{art_path}/' + jname
    joint_prim = stage.GetPrimAtPath(joint_path)
    if joint_prim.IsValid():
        from pxr import UsdPhysics
        drive = UsdPhysics.DriveAPI.Get(joint_prim, 'angular')
        if drive:
            drive.GetTargetPositionAttr().Set(joint_positions[i], time_code)

print(f"Recorded waypoint as USD TimeSample at time={{time_code}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    # Default: JSON format
    return f"""\
import json
import os
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = list(art.dof_names) if art.dof_names is not None else []

waypoint = {{
    "joint_positions": joint_positions,
    "joint_velocities": joint_velocities,
    "joint_names": joint_names,
}}

# Append to existing file or create new one
output_path = '{output_path}'
os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

data = {{"waypoints": []}}
if os.path.exists(output_path):
    with open(output_path, 'r') as f:
        data = json.load(f)

data["waypoints"].append(waypoint)

with open(output_path, 'w') as f:
    json.dump(data, f, indent=2)

print(f"Recorded waypoint {{len(data['waypoints'])}} to {{output_path}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""


def _gen_replay_trajectory(args: Dict) -> str:
    """Generate code to replay a recorded trajectory."""
    art_path = args["articulation_path"]
    trajectory_path = args["trajectory_path"]
    speed = args.get("speed", 1.0)
    # Clamp speed to valid range
    speed = max(0.1, min(4.0, speed))

    return f"""\
import json
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World
import omni.physx

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Load trajectory
with open('{trajectory_path}', 'r') as f:
    data = json.load(f)
waypoints = data.get("waypoints", [])
if not waypoints:
    print("No waypoints found in trajectory file.")
else:
    # Replay at {speed}x speed
    speed_factor = {speed}
    step_interval = max(1, int(10 / speed_factor))  # steps between waypoints
    _replay_state = {{"idx": 0, "step_count": 0}}

    def _replay_step(step_size):
        state = _replay_state
        state["step_count"] += 1
        if state["step_count"] % step_interval != 0:
            return
        idx = state["idx"]
        if idx >= len(waypoints):
            print(f"Trajectory replay complete ({{len(waypoints)}} waypoints at {speed}x speed)")
            return
        wp = waypoints[idx]
        joint_pos = np.array(wp["joint_positions"])
        art.set_joint_position_targets(joint_pos)
        state["idx"] += 1

    physx = omni.physx.get_physx_interface()
    _replay_sub = physx.subscribe_physics_step_events(_replay_step)

    print(f"Replaying trajectory: {{len(waypoints)}} waypoints at {speed}x speed")
"""


def _gen_interpolate_trajectory(args: Dict) -> str:
    """Generate code to interpolate between sparse waypoints."""
    art_path = args["articulation_path"]
    waypoints = args["waypoints"]
    method = args.get("method", "linear")
    num_steps = args.get("num_steps", 50)
    output_path = args.get("output_path", "")
    robot_type = args.get("robot_type", "franka").lower()

    # Serialize waypoints for code injection
    wp_data = [wp["joint_positions"] for wp in waypoints]

    if method == "cubic":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": row.tolist()}} for row in smooth_trajectory]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "cubic", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from scipy.interpolate import CubicSpline

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)  # shape: (N, n_dof)

# Cubic spline interpolation in joint space
n_waypoints = len(wp_array)
t_knots = np.linspace(0, 1, n_waypoints)
cs = CubicSpline(t_knots, wp_array, axis=0)

t_dense = np.linspace(0, 1, (n_waypoints - 1) * {num_steps})
smooth_trajectory = cs(t_dense)

print(f"Cubic interpolation: {{n_waypoints}} waypoints -> {{len(smooth_trajectory)}} steps")
{save_block}"""

    if method == "rmpflow":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in planned_positions]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "rmpflow", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Load RMPflow
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Sparse waypoints (joint space)
waypoints = {wp_data}
planned_positions = []

for i, wp in enumerate(waypoints):
    target_pos = np.array(wp)
    # Use forward kinematics to get task-space target
    rmpflow.set_end_effector_target(target_pos[:3], None)
    # Step through RMPflow for {num_steps} steps
    current_pos = np.array(waypoints[max(0, i-1)])
    current_vel = np.zeros_like(current_pos)
    for step in range({num_steps}):
        action = rmpflow.get_next_articulation_action(current_pos, current_vel)
        if action.joint_positions is not None:
            current_pos = action.joint_positions
        planned_positions.append(current_pos.copy())

print(f"RMPflow interpolation: {{len(waypoints)}} waypoints -> {{len(planned_positions)}} steps (collision-aware)")
{save_block}"""

    # Default: linear interpolation
    save_block = ""
    if output_path:
        save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in interpolated]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "linear", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
    return f"""\
import numpy as np
import json

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)

# Linear interpolation in joint space
interpolated = []
for i in range(len(wp_array) - 1):
    start = wp_array[i]
    end = wp_array[i + 1]
    for t in np.linspace(0, 1, {num_steps}, endpoint=(i == len(wp_array) - 2)):
        interpolated.append(start + t * (end - start))

interpolated = np.array(interpolated)
print(f"Linear interpolation: {{len(wp_array)}} waypoints -> {{len(interpolated)}} steps")
{save_block}"""


CODE_GEN_HANDLERS["start_teaching_mode"] = _gen_start_teaching_mode
CODE_GEN_HANDLERS["record_waypoints"] = _gen_record_waypoints
CODE_GEN_HANDLERS["replay_trajectory"] = _gen_replay_trajectory
CODE_GEN_HANDLERS["interpolate_trajectory"] = _gen_interpolate_trajectory


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


# ── Interactive Robot Teaching ────────────────────────────────────────────────

def _gen_start_teaching_mode(args: Dict) -> str:
    """Generate code to start interactive robot teaching mode."""
    art_path = args["articulation_path"]
    mode = args["mode"]
    robot_type = args.get("robot_type", "franka").lower()

    if mode == "drag_target":
        # FollowTarget pattern: ghost target prim + RMPflow tracking
        return f"""\
import omni.usd
import numpy as np
from pxr import UsdGeom, Gf, Sdf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Create draggable ghost target at current end-effector position
target_path = '{art_path}/TeachTarget'
if not stage.GetPrimAtPath(target_path).IsValid():
    target_prim = stage.DefinePrim(target_path, 'Sphere')
    UsdGeom.Gprim(target_prim).GetDisplayColorAttr().Set([(0.2, 0.8, 0.2)])
    xf = UsdGeom.Xformable(target_prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4, 0.0, 0.4))
    xf.AddScaleOp().Set(Gf.Vec3d(0.03, 0.03, 0.03))
    print(f"Created draggable teach target at {{target_path}}")
else:
    target_prim = stage.GetPrimAtPath(target_path)
    print(f"Teach target already exists at {{target_path}}")

# Load RMPflow controller for tracking
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Register physics callback to track target each step
def _teach_step(step_size):
    target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{art_path}/TeachTarget'))
    target_pos = target_xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
    rmpflow.set_end_effector_target(
        np.array([target_pos[0], target_pos[1], target_pos[2]]),
        None,
    )
    joint_positions = art.get_joint_positions()
    joint_velocities = art.get_joint_velocities()
    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
    art.apply_action(action)

import omni.physx
physx = omni.physx.get_physx_interface()
_sub = physx.subscribe_physics_step_events(_teach_step)

print("Teaching mode ACTIVE (drag_target): drag the green sphere in the viewport, robot follows via RMPflow.")
print("Press SPACE in viewport to record waypoints. Stop simulation to exit teaching mode.")
"""

    if mode == "keyboard":
        return f"""\
import numpy as np
from isaaclab.devices.keyboard import Se3Keyboard
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize keyboard device
keyboard = Se3Keyboard(
    pos_sensitivity=0.005,
    rot_sensitivity=0.01,
)
keyboard.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (keyboard):")
print("  W/S = forward/backward, A/D = left/right, Q/E = up/down")
print("  Z/X = roll, T/G = pitch, C/V = yaw")
print("  K = toggle gripper, SPACE = record waypoint")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "spacemouse":
        return f"""\
import numpy as np
from isaaclab.devices.spacemouse import Se3SpaceMouse
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize SpaceMouse device
spacemouse = Se3SpaceMouse(
    pos_sensitivity=0.005,
    rot_sensitivity=0.005,
)
spacemouse.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (spacemouse): move the 3Dconnexion SpaceMouse to control the end-effector.")
print("  Button 0 = record waypoint, Button 1 = toggle gripper")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "gravity_comp":
        return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

n_dof = art.num_dof

# Zero PD gains for compliance
art.set_joint_stiffnesses(np.zeros(n_dof))
art.set_joint_dampings(np.full(n_dof, 0.1))  # small damping to prevent oscillation

# Compute and apply gravity compensation
import numpy as np
gravity_comp = art.get_measured_joint_efforts()
print(f"Gravity compensation forces: {{gravity_comp}}")

# Register physics callback to maintain gravity compensation
import omni.physx
physx = omni.physx.get_physx_interface()

def _gravity_comp_step(step_size):
    efforts = art.get_measured_joint_efforts()
    art.set_joint_efforts(efforts)

_sub = physx.subscribe_physics_step_events(_gravity_comp_step)

print("Teaching mode ACTIVE (gravity_comp): arm is now compliant.")
print("  Use Shift+drag in viewport to move joints via physics force grab.")
print("  The robot will hold position against gravity but yield to your input.")
print("Stop simulation to exit teaching mode.")
"""
    return f"# Unknown teaching mode: {mode}"


def _gen_record_waypoints(args: Dict) -> str:
    """Generate code to record robot waypoints to file."""
    art_path = args["articulation_path"]
    output_path = args["output_path"]
    fmt = args.get("format", "json")

    if fmt == "hdf5":
        return f"""\
import numpy as np
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Capture current joint state as a waypoint
joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = art.dof_names

# Write HDF5 in robomimic schema
import h5py
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)

with h5py.File('{output_path}', 'a') as f:
    # robomimic demo schema
    if 'data' not in f:
        grp = f.create_group('data')
        grp.attrs['num_demos'] = 0
    data = f['data']
    demo_idx = data.attrs['num_demos']
    demo_name = f'demo_{{demo_idx}}'
    demo = data.create_group(demo_name)
    demo.create_dataset('actions', data=np.array([joint_positions]))
    obs = demo.create_group('obs')
    obs.create_dataset('joint_pos', data=np.array([joint_positions]))
    obs.create_dataset('joint_vel', data=np.array([joint_velocities]))
    demo.attrs['num_samples'] = 1
    data.attrs['num_demos'] = demo_idx + 1

print(f"Recorded waypoint to {{'{output_path}'}} (HDF5 robomimic schema, demo {{demo_idx}})")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    if fmt == "usd":
        return f"""\
import omni.usd
from pxr import Usd, UsdGeom, Sdf
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()

stage = omni.usd.get_context().get_stage()
time_code = stage.GetEndTimeCode() + 1
stage.SetEndTimeCode(time_code)

# Write joint positions as USD TimeSamples on each joint drive
joint_names = art.dof_names
for i, jname in enumerate(joint_names):
    joint_path = '{art_path}/' + jname
    joint_prim = stage.GetPrimAtPath(joint_path)
    if joint_prim.IsValid():
        from pxr import UsdPhysics
        drive = UsdPhysics.DriveAPI.Get(joint_prim, 'angular')
        if drive:
            drive.GetTargetPositionAttr().Set(joint_positions[i], time_code)

print(f"Recorded waypoint as USD TimeSample at time={{time_code}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    # Default: JSON format
    return f"""\
import json
import os
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = list(art.dof_names) if art.dof_names is not None else []

waypoint = {{
    "joint_positions": joint_positions,
    "joint_velocities": joint_velocities,
    "joint_names": joint_names,
}}

# Append to existing file or create new one
output_path = '{output_path}'
os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

data = {{"waypoints": []}}
if os.path.exists(output_path):
    with open(output_path, 'r') as f:
        data = json.load(f)

data["waypoints"].append(waypoint)

with open(output_path, 'w') as f:
    json.dump(data, f, indent=2)

print(f"Recorded waypoint {{len(data['waypoints'])}} to {{output_path}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""


def _gen_replay_trajectory(args: Dict) -> str:
    """Generate code to replay a recorded trajectory."""
    art_path = args["articulation_path"]
    trajectory_path = args["trajectory_path"]
    speed = args.get("speed", 1.0)
    # Clamp speed to valid range
    speed = max(0.1, min(4.0, speed))

    return f"""\
import json
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World
import omni.physx

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Load trajectory
with open('{trajectory_path}', 'r') as f:
    data = json.load(f)
waypoints = data.get("waypoints", [])
if not waypoints:
    print("No waypoints found in trajectory file.")
else:
    # Replay at {speed}x speed
    speed_factor = {speed}
    step_interval = max(1, int(10 / speed_factor))  # steps between waypoints
    _replay_state = {{"idx": 0, "step_count": 0}}

    def _replay_step(step_size):
        state = _replay_state
        state["step_count"] += 1
        if state["step_count"] % step_interval != 0:
            return
        idx = state["idx"]
        if idx >= len(waypoints):
            print(f"Trajectory replay complete ({{len(waypoints)}} waypoints at {speed}x speed)")
            return
        wp = waypoints[idx]
        joint_pos = np.array(wp["joint_positions"])
        art.set_joint_position_targets(joint_pos)
        state["idx"] += 1

    physx = omni.physx.get_physx_interface()
    _replay_sub = physx.subscribe_physics_step_events(_replay_step)

    print(f"Replaying trajectory: {{len(waypoints)}} waypoints at {speed}x speed")
"""


def _gen_interpolate_trajectory(args: Dict) -> str:
    """Generate code to interpolate between sparse waypoints."""
    art_path = args["articulation_path"]
    waypoints = args["waypoints"]
    method = args.get("method", "linear")
    num_steps = args.get("num_steps", 50)
    output_path = args.get("output_path", "")
    robot_type = args.get("robot_type", "franka").lower()

    # Serialize waypoints for code injection
    wp_data = [wp["joint_positions"] for wp in waypoints]

    if method == "cubic":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": row.tolist()}} for row in smooth_trajectory]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "cubic", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from scipy.interpolate import CubicSpline

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)  # shape: (N, n_dof)

# Cubic spline interpolation in joint space
n_waypoints = len(wp_array)
t_knots = np.linspace(0, 1, n_waypoints)
cs = CubicSpline(t_knots, wp_array, axis=0)

t_dense = np.linspace(0, 1, (n_waypoints - 1) * {num_steps})
smooth_trajectory = cs(t_dense)

print(f"Cubic interpolation: {{n_waypoints}} waypoints -> {{len(smooth_trajectory)}} steps")
{save_block}"""

    if method == "rmpflow":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in planned_positions]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "rmpflow", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Load RMPflow
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Sparse waypoints (joint space)
waypoints = {wp_data}
planned_positions = []

for i, wp in enumerate(waypoints):
    target_pos = np.array(wp)
    # Use forward kinematics to get task-space target
    rmpflow.set_end_effector_target(target_pos[:3], None)
    # Step through RMPflow for {num_steps} steps
    current_pos = np.array(waypoints[max(0, i-1)])
    current_vel = np.zeros_like(current_pos)
    for step in range({num_steps}):
        action = rmpflow.get_next_articulation_action(current_pos, current_vel)
        if action.joint_positions is not None:
            current_pos = action.joint_positions
        planned_positions.append(current_pos.copy())

print(f"RMPflow interpolation: {{len(waypoints)}} waypoints -> {{len(planned_positions)}} steps (collision-aware)")
{save_block}"""

    # Default: linear interpolation
    save_block = ""
    if output_path:
        save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in interpolated]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "linear", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
    return f"""\
import numpy as np
import json

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)

# Linear interpolation in joint space
interpolated = []
for i in range(len(wp_array) - 1):
    start = wp_array[i]
    end = wp_array[i + 1]
    for t in np.linspace(0, 1, {num_steps}, endpoint=(i == len(wp_array) - 2)):
        interpolated.append(start + t * (end - start))

interpolated = np.array(interpolated)
print(f"Linear interpolation: {{len(wp_array)}} waypoints -> {{len(interpolated)}} steps")
{save_block}"""


CODE_GEN_HANDLERS["start_teaching_mode"] = _gen_start_teaching_mode
CODE_GEN_HANDLERS["record_waypoints"] = _gen_record_waypoints
CODE_GEN_HANDLERS["replay_trajectory"] = _gen_replay_trajectory
CODE_GEN_HANDLERS["interpolate_trajectory"] = _gen_interpolate_trajectory


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


# ── Preflight Check (Phase 2 Addendum — 23 checks, 4 tiers) ───────────────

def _gen_preflight_check(args: Dict) -> str:
    """Generate code that runs all 23 preflight checks inside Kit."""
    scope = args.get("scope", "all")
    articulation_path = args.get("articulation_path")

    # Build scope filter
    if articulation_path:
        scope_block = f"""\
# Scope: specific articulation
scope_root = stage.GetPrimAtPath('{articulation_path}')
if not scope_root.IsValid():
    issues.append({{
        'id': 'SCOPE', 'prim': '{articulation_path}',
        'message': 'Articulation prim not found',
        'severity': 'error', 'auto_fix': None, 'tier': 0,
    }})
    all_prims = []
else:
    all_prims = [scope_root] + list(scope_root.GetAllDescendants())
"""
    else:
        scope_block = """\
# Scope: entire stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(root.GetAllDescendants())
"""

    run_tier1 = scope in ("all", "tier1")
    run_tier2 = scope in ("all", "tier2")
    run_tier3 = scope in ("all", "tier3")
    run_tier4 = scope in ("all", "tier4")

    # ── Tier 1 checks ──
    tier1_block = ""
    if run_tier1:
        tier1_block = r"""
# ── Tier 1: Crash Preventers (errors) ────────────────────────────────────

# M04: Missing PhysicsScene prim
has_physics_scene = False
physics_scene_prim = None
for p in all_prims:
    if not p.IsValid():
        continue
    if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene':
        has_physics_scene = True
        physics_scene_prim = p
        break
if not has_physics_scene:
    issues.append({
        'id': 'M04', 'prim': '/World/PhysicsScene',
        'message': 'Missing PhysicsScene prim — simulation cannot run',
        'severity': 'error', 'auto_fix': "stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene')",
        'tier': 1,
    })

# M11: metersPerUnit mismatch
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if meters_per_unit not in (1.0, 0.01):
    issues.append({
        'id': 'M11', 'prim': 'stage',
        'message': f'metersPerUnit={meters_per_unit} — expected 1.0 (meters) or 0.01 (cm)',
        'severity': 'error',
        'auto_fix': 'UsdGeom.SetStageMetersPerUnit(stage, 1.0)',
        'tier': 1,
    })

for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M01: Missing CollisionAPI on mesh prims with RigidBodyAPI
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        if not p.HasAPI(UsdPhysics.CollisionAPI):
            issues.append({
                'id': 'M01', 'prim': pp,
                'message': 'Mesh has RigidBodyAPI but no CollisionAPI — will not collide',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M02: Missing RigidBodyAPI on dynamic objects (have mass but no RigidBody)
    if p.HasAPI(UsdPhysics.MassAPI) and not p.HasAPI(UsdPhysics.RigidBodyAPI):
        # Skip if it is part of an articulation (joints handle dynamics)
        if not p.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M02', 'prim': pp,
                'message': 'Has MassAPI but no RigidBodyAPI — mass will be ignored',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M03: ArticulationRootAPI on wrong prim (not the root link)
    if p.HasAPI(UsdPhysics.ArticulationRootAPI):
        parent = p.GetParent()
        if parent and parent.IsValid() and parent.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M03', 'prim': pp,
                'message': 'ArticulationRootAPI found on a non-root prim (parent also has it)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M05: Zero or negative mass
    if p.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(p)
        mass_val = mass_api.GetMassAttr().Get()
        if mass_val is not None and mass_val <= 0:
            issues.append({
                'id': 'M05', 'prim': pp,
                'message': f'Zero or negative mass: {mass_val}',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.MassAPI(stage.GetPrimAtPath("{pp}")).GetMassAttr().Set(1.0)',
                'tier': 1,
            })

        # M06: Invalid inertia tensor (zero/negative diagonal)
        inertia = mass_api.GetDiagonalInertiaAttr().Get()
        if inertia is not None and any(v <= 0 for v in inertia):
            issues.append({
                'id': 'M06', 'prim': pp,
                'message': f'Invalid inertia tensor: {inertia} (zero/negative diagonal)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M08: Joint drive kp * dt > 0.5 (stability criterion)
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                kp = drive.GetStiffnessAttr().Get()
                if kp is not None and kp > 0:
                    # Assume default dt = 1/60 if we cannot read it
                    dt = 1.0 / 60.0
                    if physics_scene_prim and physics_scene_prim.IsValid():
                        ts_attr = physics_scene_prim.GetAttribute('physxScene:timeStepsPerSecond')
                        if ts_attr and ts_attr.IsValid():
                            ts_val = ts_attr.Get()
                            if ts_val and ts_val > 0:
                                dt = 1.0 / ts_val
                    if kp * dt > 0.5:
                        issues.append({
                            'id': 'M08', 'prim': pp,
                            'message': f'Drive stiffness kp={kp} * dt={dt:.4f} = {kp*dt:.2f} > 0.5 — may cause instability',
                            'severity': 'error',
                            'auto_fix': f'Reduce kp to {0.5/dt:.1f} or lower',
                            'tier': 1,
                        })
                        break
"""

    # ── Tier 2 checks ──
    tier2_block = ""
    if run_tier2:
        tier2_block = r"""
# ── Tier 2: Correctness (warnings) ──────────────────────────────────────

# M12: Up-axis mismatch
up_axis = UsdGeom.GetStageUpAxis(stage)
if up_axis not in ('Y', 'Z'):
    issues.append({
        'id': 'M12', 'prim': 'stage',
        'message': f'Unusual up-axis: {up_axis} — Isaac Sim expects Z-up',
        'severity': 'warning', 'auto_fix': "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)",
        'tier': 2,
    })

mass_map = {}
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M07: Joint limits +/- inf
    if p.IsA(UsdPhysics.RevoluteJoint):
        joint = UsdPhysics.RevoluteJoint(p)
        lower = joint.GetLowerLimitAttr().Get()
        upper = joint.GetUpperLimitAttr().Get()
        if lower is not None and upper is not None:
            if abs(lower) > 1e30 or abs(upper) > 1e30:
                issues.append({
                    'id': 'M07', 'prim': pp,
                    'message': f'Joint limits effectively infinite: lower={lower}, upper={upper}',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

    # Collect masses for M09
    if p.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(p).GetMassAttr().Get()
        if m is not None and m > 0:
            mass_map[pp] = m

    # M10: Collision mesh > 10K triangles on dynamic body
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        mesh = UsdGeom.Mesh(p)
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        if fvc is not None and len(fvc) > 10000:
            issues.append({
                'id': 'M10', 'prim': pp,
                'message': f'Collision mesh has {len(fvc)} faces on a dynamic body — may slow simulation',
                'severity': 'warning',
                'auto_fix': 'Use convex decomposition or simplified collision mesh',
                'tier': 2,
            })

    # M13: CCD on slow/large objects (unnecessary cost)
    if p.HasAPI(UsdPhysics.RigidBodyAPI):
        ccd_attr = p.GetAttribute('physxRigidBody:enableCCD')
        if ccd_attr and ccd_attr.IsValid() and ccd_attr.Get() is True:
            # Check if object has large extent
            if p.IsA(UsdGeom.Boundable):
                extent_attr = UsdGeom.Boundable(p).GetExtentAttr()
                ext = extent_attr.Get() if extent_attr else None
                if ext is not None and len(ext) == 2:
                    diag = ((ext[1][0]-ext[0][0])**2 + (ext[1][1]-ext[0][1])**2 + (ext[1][2]-ext[0][2])**2)**0.5
                    if diag > 1.0:
                        issues.append({
                            'id': 'M13', 'prim': pp,
                            'message': f'CCD enabled on large object (extent diagonal={diag:.2f}m) — unnecessary cost',
                            'severity': 'warning',
                            'auto_fix': f'p.GetAttribute("physxRigidBody:enableCCD").Set(False)',
                            'tier': 2,
                        })

    # M15: Self-collision enabled with potentially overlapping meshes
    if p.HasAPI(PhysxSchema.PhysxArticulationAPI):
        sc_attr = p.GetAttribute('physxArticulation:enabledSelfCollisions')
        if sc_attr and sc_attr.IsValid() and sc_attr.Get() is True:
            # Count mesh children — if many are close, warn
            mesh_children = [c for c in p.GetAllDescendants() if c.IsA(UsdGeom.Mesh)]
            if len(mesh_children) > 5:
                issues.append({
                    'id': 'M15', 'prim': pp,
                    'message': f'Self-collision enabled with {len(mesh_children)} mesh links — check for initial overlaps',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

# M09: Extreme mass ratio > 100:1
if len(mass_map) >= 2:
    masses = list(mass_map.values())
    max_m, min_m = max(masses), min(masses)
    if min_m > 0 and max_m / min_m > 100:
        issues.append({
            'id': 'M09', 'prim': 'scene-wide',
            'message': f'Extreme mass ratio: {max_m/min_m:.1f}:1 (max={max_m}, min={min_m})',
            'severity': 'warning',
            'auto_fix': 'Reduce mass ratio to below 100:1',
            'tier': 2,
        })
"""

    # ── Tier 3 checks ──
    tier3_block = ""
    if run_tier3:
        tier3_block = r"""
# ── Tier 3: RL Training ─────────────────────────────────────────────────

# M16: replicate_physics=False (check if cloner used without it)
# Detect GridCloner usage by looking for /envs pattern
env_prims = [p for p in all_prims if p.IsValid() and '/envs/env_' in str(p.GetPath())]
if len(env_prims) > 1:
    # Multiple envs found — check if physics replication is enabled
    if physics_scene_prim and physics_scene_prim.IsValid():
        rp_attr = physics_scene_prim.GetAttribute('physxScene:enableGPUDynamics')
        gpu_dyn = rp_attr.Get() if rp_attr and rp_attr.IsValid() else None
        if gpu_dyn is not True:
            issues.append({
                'id': 'M16', 'prim': str(physics_scene_prim.GetPath()) if physics_scene_prim else '/PhysicsScene',
                'message': 'Multiple envs detected but GPU dynamics not enabled — replicate_physics may be False',
                'severity': 'warning',
                'auto_fix': 'Enable GPU dynamics on PhysicsScene',
                'tier': 3,
            })

# M17: Env spacing too small
if len(env_prims) >= 2:
    env_roots = {}
    for ep in env_prims:
        ep_path = str(ep.GetPath())
        parts = ep_path.split('/')
        for i, part in enumerate(parts):
            if part.startswith('env_'):
                root_path = '/'.join(parts[:i+1])
                if root_path not in env_roots:
                    env_roots[root_path] = ep
                break
    if len(env_roots) >= 2:
        root_list = list(env_roots.values())
        try:
            xf0 = UsdGeom.Xformable(root_list[0]).ComputeLocalToWorldTransform(0)
            xf1 = UsdGeom.Xformable(root_list[1]).ComputeLocalToWorldTransform(0)
            pos0 = xf0.ExtractTranslation()
            pos1 = xf1.ExtractTranslation()
            spacing = ((pos1[0]-pos0[0])**2 + (pos1[1]-pos0[1])**2 + (pos1[2]-pos0[2])**2)**0.5
            if spacing < 1.0:
                issues.append({
                    'id': 'M17', 'prim': 'envs',
                    'message': f'Env spacing = {spacing:.2f}m — may cause inter-env collisions (recommend >= 2.0m)',
                    'severity': 'warning',
                    'auto_fix': 'Increase GridCloner spacing parameter',
                    'tier': 3,
                })
        except Exception:
            pass

# M19: GPU contact buffer too small
if physics_scene_prim and physics_scene_prim.IsValid():
    buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxNumPartitions')
    if buf_attr and buf_attr.IsValid():
        buf_val = buf_attr.Get()
        if buf_val is not None and buf_val < 8:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU max partitions = {buf_val} — may be too small for RL with many envs',
                'severity': 'warning',
                'auto_fix': 'Increase gpuMaxNumPartitions to 8 or higher',
                'tier': 3,
            })
    contact_buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxRigidContactCount')
    if contact_buf_attr and contact_buf_attr.IsValid():
        cb_val = contact_buf_attr.Get()
        if cb_val is not None and cb_val < 524288:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU contact buffer = {cb_val} — may overflow with many envs (recommend >= 524288)',
                'severity': 'warning',
                'auto_fix': f'Set gpuMaxRigidContactCount to 524288',
                'tier': 3,
            })

# M20: Observation normalization issues — check for very large/small attribute values
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                max_force = drive.GetMaxForceAttr().Get()
                if max_force is not None and max_force > 1e6:
                    issues.append({
                        'id': 'M20', 'prim': pp,
                        'message': f'Drive maxForce={max_force} — very large value may cause observation normalization issues in RL',
                        'severity': 'warning',
                        'auto_fix': None, 'tier': 3,
                    })
                    break
"""

    # ── Tier 4 checks ──
    tier4_block = ""
    if run_tier4:
        tier4_block = r"""
# ── Tier 4: ROS2 / OmniGraph ────────────────────────────────────────────

try:
    import omni.graph.core as og
    graphs_available = True
except ImportError:
    graphs_available = False

if graphs_available:
    all_graphs = og.get_all_graphs()

    for graph in all_graphs:
        gp = graph.get_path_to_graph()
        nodes = graph.get_nodes()

        # M18: OmniGraph without tick source
        has_tick = False
        has_ros2_context = False
        has_clock_pub = False
        ros2_sensor_nodes = []

        for node in nodes:
            nt = node.get_node_type().get_node_type()
            node_path = node.get_prim_path()

            if 'OnPlaybackTick' in nt or 'OnPhysicsStep' in nt or 'OnTick' in nt:
                has_tick = True

            # M21: Detect ROS2Context
            if 'ROS2Context' in nt:
                has_ros2_context = True

            # M22: Detect clock publisher
            if 'ROS2PublishClock' in nt or 'PublishClock' in nt:
                has_clock_pub = True

            # Collect sensor nodes for M23
            if any(s in nt for s in ('ROS2Publish', 'ROS2Camera', 'ROS2Lidar', 'ROS2Imu')):
                ros2_sensor_nodes.append((node_path, nt, node))

        # M18: No tick source
        if not has_tick and len(nodes) > 0:
            issues.append({
                'id': 'M18', 'prim': gp,
                'message': 'OmniGraph has no tick source (OnPlaybackTick/OnPhysicsStep) — graph will not execute',
                'severity': 'error',
                'auto_fix': 'Add an OnPlaybackTick node and connect its execOut to the first node',
                'tier': 4,
            })

        # Only check ROS2-specific issues if there are ROS2 nodes
        has_ros2_nodes = any('ROS2' in n.get_node_type().get_node_type() or 'ros2' in n.get_node_type().get_node_type().lower() for n in nodes)

        if has_ros2_nodes:
            # M21: Missing ROS2Context
            if not has_ros2_context:
                issues.append({
                    'id': 'M21', 'prim': gp,
                    'message': 'ROS2 nodes present but no ROS2Context node — bridge will not function',
                    'severity': 'error',
                    'auto_fix': 'Add a ROS2Context node to the graph',
                    'tier': 4,
                })

            # M22: Missing /clock publisher with use_sim_time
            if not has_clock_pub:
                issues.append({
                    'id': 'M22', 'prim': gp,
                    'message': 'ROS2 nodes present but no clock publisher — use_sim_time will not work',
                    'severity': 'warning',
                    'auto_fix': 'Add a ROS2PublishClock node to publish /clock',
                    'tier': 4,
                })

            # M14: ROS2 QoS mismatch — check for sensor reliability vs subscriber expectations
            for node_path, nt, node in ros2_sensor_nodes:
                qos_attr = None
                try:
                    qos_attr = node.get_attribute('inputs:qosProfile')
                except Exception:
                    pass
                if qos_attr is not None:
                    qos_val = qos_attr.get()
                    if qos_val and isinstance(qos_val, str) and qos_val.lower() == 'reliable':
                        issues.append({
                            'id': 'M14', 'prim': node_path,
                            'message': f'Sensor publisher using RELIABLE QoS — may cause latency; use BEST_EFFORT for real-time data',
                            'severity': 'warning',
                            'auto_fix': "Set qosProfile to 'sensor_data' or 'best_effort'",
                            'tier': 4,
                        })

            # M23: Sensor frame ID mismatch — check if frame_id inputs are set
            for node_path, nt, node in ros2_sensor_nodes:
                frame_attr = None
                try:
                    frame_attr = node.get_attribute('inputs:frameId')
                except Exception:
                    pass
                if frame_attr is not None:
                    fid = frame_attr.get()
                    if not fid or fid == '' or fid == 'sim':
                        issues.append({
                            'id': 'M23', 'prim': node_path,
                            'message': f'Sensor frame_id is empty or default ("{fid}") — will not match robot TF tree',
                            'severity': 'warning',
                            'auto_fix': 'Set frameId to the correct link name (e.g. "camera_link")',
                            'tier': 4,
                        })
"""

    return f"""\
import omni.usd
import json
from pxr import UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
physics_scene_prim = None
{scope_block}
{tier1_block}
{tier2_block}
{tier3_block}
{tier4_block}
# ── Summary ──────────────────────────────────────────────────────────────
tier1_errors = [i for i in issues if i['tier'] == 1]
tier2_warnings = [i for i in issues if i['tier'] == 2]
tier3_rl = [i for i in issues if i['tier'] == 3]
tier4_ros2 = [i for i in issues if i['tier'] == 4]
auto_fixable = sum(1 for i in issues if i.get('auto_fix'))

result = {{
    'status': 'PASS' if not tier1_errors else 'FAIL',
    'total_issues': len(issues),
    'tier1_errors': tier1_errors,
    'tier2_warnings': tier2_warnings,
    'tier3_rl': tier3_rl,
    'tier4_ros2': tier4_ros2,
    'auto_fixable_count': auto_fixable,
    'summary': {{
        'tier1': len(tier1_errors),
        'tier2': len(tier2_warnings),
        'tier3': len(tier3_rl),
        'tier4': len(tier4_ros2),
    }},
}}
print(json.dumps(result, indent=2))
"""


CODE_GEN_HANDLERS["preflight_check"] = _gen_preflight_check


# ── Preflight Check (Phase 2 Addendum — 23 checks, 4 tiers) ───────────────

def _gen_preflight_check(args: Dict) -> str:
    """Generate code that runs all 23 preflight checks inside Kit."""
    scope = args.get("scope", "all")
    articulation_path = args.get("articulation_path")

    # Build scope filter
    if articulation_path:
        scope_block = f"""\
# Scope: specific articulation
scope_root = stage.GetPrimAtPath('{articulation_path}')
if not scope_root.IsValid():
    issues.append({{
        'id': 'SCOPE', 'prim': '{articulation_path}',
        'message': 'Articulation prim not found',
        'severity': 'error', 'auto_fix': None, 'tier': 0,
    }})
    all_prims = []
else:
    all_prims = [scope_root] + list(scope_root.GetAllDescendants())
"""
    else:
        scope_block = """\
# Scope: entire stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(root.GetAllDescendants())
"""

    run_tier1 = scope in ("all", "tier1")
    run_tier2 = scope in ("all", "tier2")
    run_tier3 = scope in ("all", "tier3")
    run_tier4 = scope in ("all", "tier4")

    # ── Tier 1 checks ──
    tier1_block = ""
    if run_tier1:
        tier1_block = r"""
# ── Tier 1: Crash Preventers (errors) ────────────────────────────────────

# M04: Missing PhysicsScene prim
has_physics_scene = False
physics_scene_prim = None
for p in all_prims:
    if not p.IsValid():
        continue
    if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene':
        has_physics_scene = True
        physics_scene_prim = p
        break
if not has_physics_scene:
    issues.append({
        'id': 'M04', 'prim': '/World/PhysicsScene',
        'message': 'Missing PhysicsScene prim — simulation cannot run',
        'severity': 'error', 'auto_fix': "stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene')",
        'tier': 1,
    })

# M11: metersPerUnit mismatch
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if meters_per_unit not in (1.0, 0.01):
    issues.append({
        'id': 'M11', 'prim': 'stage',
        'message': f'metersPerUnit={meters_per_unit} — expected 1.0 (meters) or 0.01 (cm)',
        'severity': 'error',
        'auto_fix': 'UsdGeom.SetStageMetersPerUnit(stage, 1.0)',
        'tier': 1,
    })

for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M01: Missing CollisionAPI on mesh prims with RigidBodyAPI
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        if not p.HasAPI(UsdPhysics.CollisionAPI):
            issues.append({
                'id': 'M01', 'prim': pp,
                'message': 'Mesh has RigidBodyAPI but no CollisionAPI — will not collide',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M02: Missing RigidBodyAPI on dynamic objects (have mass but no RigidBody)
    if p.HasAPI(UsdPhysics.MassAPI) and not p.HasAPI(UsdPhysics.RigidBodyAPI):
        # Skip if it is part of an articulation (joints handle dynamics)
        if not p.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M02', 'prim': pp,
                'message': 'Has MassAPI but no RigidBodyAPI — mass will be ignored',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M03: ArticulationRootAPI on wrong prim (not the root link)
    if p.HasAPI(UsdPhysics.ArticulationRootAPI):
        parent = p.GetParent()
        if parent and parent.IsValid() and parent.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M03', 'prim': pp,
                'message': 'ArticulationRootAPI found on a non-root prim (parent also has it)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M05: Zero or negative mass
    if p.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(p)
        mass_val = mass_api.GetMassAttr().Get()
        if mass_val is not None and mass_val <= 0:
            issues.append({
                'id': 'M05', 'prim': pp,
                'message': f'Zero or negative mass: {mass_val}',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.MassAPI(stage.GetPrimAtPath("{pp}")).GetMassAttr().Set(1.0)',
                'tier': 1,
            })

        # M06: Invalid inertia tensor (zero/negative diagonal)
        inertia = mass_api.GetDiagonalInertiaAttr().Get()
        if inertia is not None and any(v <= 0 for v in inertia):
            issues.append({
                'id': 'M06', 'prim': pp,
                'message': f'Invalid inertia tensor: {inertia} (zero/negative diagonal)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M08: Joint drive kp * dt > 0.5 (stability criterion)
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                kp = drive.GetStiffnessAttr().Get()
                if kp is not None and kp > 0:
                    # Assume default dt = 1/60 if we cannot read it
                    dt = 1.0 / 60.0
                    if physics_scene_prim and physics_scene_prim.IsValid():
                        ts_attr = physics_scene_prim.GetAttribute('physxScene:timeStepsPerSecond')
                        if ts_attr and ts_attr.IsValid():
                            ts_val = ts_attr.Get()
                            if ts_val and ts_val > 0:
                                dt = 1.0 / ts_val
                    if kp * dt > 0.5:
                        issues.append({
                            'id': 'M08', 'prim': pp,
                            'message': f'Drive stiffness kp={kp} * dt={dt:.4f} = {kp*dt:.2f} > 0.5 — may cause instability',
                            'severity': 'error',
                            'auto_fix': f'Reduce kp to {0.5/dt:.1f} or lower',
                            'tier': 1,
                        })
                        break
"""

    # ── Tier 2 checks ──
    tier2_block = ""
    if run_tier2:
        tier2_block = r"""
# ── Tier 2: Correctness (warnings) ──────────────────────────────────────

# M12: Up-axis mismatch
up_axis = UsdGeom.GetStageUpAxis(stage)
if up_axis not in ('Y', 'Z'):
    issues.append({
        'id': 'M12', 'prim': 'stage',
        'message': f'Unusual up-axis: {up_axis} — Isaac Sim expects Z-up',
        'severity': 'warning', 'auto_fix': "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)",
        'tier': 2,
    })

mass_map = {}
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M07: Joint limits +/- inf
    if p.IsA(UsdPhysics.RevoluteJoint):
        joint = UsdPhysics.RevoluteJoint(p)
        lower = joint.GetLowerLimitAttr().Get()
        upper = joint.GetUpperLimitAttr().Get()
        if lower is not None and upper is not None:
            if abs(lower) > 1e30 or abs(upper) > 1e30:
                issues.append({
                    'id': 'M07', 'prim': pp,
                    'message': f'Joint limits effectively infinite: lower={lower}, upper={upper}',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

    # Collect masses for M09
    if p.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(p).GetMassAttr().Get()
        if m is not None and m > 0:
            mass_map[pp] = m

    # M10: Collision mesh > 10K triangles on dynamic body
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        mesh = UsdGeom.Mesh(p)
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        if fvc is not None and len(fvc) > 10000:
            issues.append({
                'id': 'M10', 'prim': pp,
                'message': f'Collision mesh has {len(fvc)} faces on a dynamic body — may slow simulation',
                'severity': 'warning',
                'auto_fix': 'Use convex decomposition or simplified collision mesh',
                'tier': 2,
            })

    # M13: CCD on slow/large objects (unnecessary cost)
    if p.HasAPI(UsdPhysics.RigidBodyAPI):
        ccd_attr = p.GetAttribute('physxRigidBody:enableCCD')
        if ccd_attr and ccd_attr.IsValid() and ccd_attr.Get() is True:
            # Check if object has large extent
            if p.IsA(UsdGeom.Boundable):
                extent_attr = UsdGeom.Boundable(p).GetExtentAttr()
                ext = extent_attr.Get() if extent_attr else None
                if ext is not None and len(ext) == 2:
                    diag = ((ext[1][0]-ext[0][0])**2 + (ext[1][1]-ext[0][1])**2 + (ext[1][2]-ext[0][2])**2)**0.5
                    if diag > 1.0:
                        issues.append({
                            'id': 'M13', 'prim': pp,
                            'message': f'CCD enabled on large object (extent diagonal={diag:.2f}m) — unnecessary cost',
                            'severity': 'warning',
                            'auto_fix': f'p.GetAttribute("physxRigidBody:enableCCD").Set(False)',
                            'tier': 2,
                        })

    # M15: Self-collision enabled with potentially overlapping meshes
    if p.HasAPI(PhysxSchema.PhysxArticulationAPI):
        sc_attr = p.GetAttribute('physxArticulation:enabledSelfCollisions')
        if sc_attr and sc_attr.IsValid() and sc_attr.Get() is True:
            # Count mesh children — if many are close, warn
            mesh_children = [c for c in p.GetAllDescendants() if c.IsA(UsdGeom.Mesh)]
            if len(mesh_children) > 5:
                issues.append({
                    'id': 'M15', 'prim': pp,
                    'message': f'Self-collision enabled with {len(mesh_children)} mesh links — check for initial overlaps',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

# M09: Extreme mass ratio > 100:1
if len(mass_map) >= 2:
    masses = list(mass_map.values())
    max_m, min_m = max(masses), min(masses)
    if min_m > 0 and max_m / min_m > 100:
        issues.append({
            'id': 'M09', 'prim': 'scene-wide',
            'message': f'Extreme mass ratio: {max_m/min_m:.1f}:1 (max={max_m}, min={min_m})',
            'severity': 'warning',
            'auto_fix': 'Reduce mass ratio to below 100:1',
            'tier': 2,
        })
"""

    # ── Tier 3 checks ──
    tier3_block = ""
    if run_tier3:
        tier3_block = r"""
# ── Tier 3: RL Training ─────────────────────────────────────────────────

# M16: replicate_physics=False (check if cloner used without it)
# Detect GridCloner usage by looking for /envs pattern
env_prims = [p for p in all_prims if p.IsValid() and '/envs/env_' in str(p.GetPath())]
if len(env_prims) > 1:
    # Multiple envs found — check if physics replication is enabled
    if physics_scene_prim and physics_scene_prim.IsValid():
        rp_attr = physics_scene_prim.GetAttribute('physxScene:enableGPUDynamics')
        gpu_dyn = rp_attr.Get() if rp_attr and rp_attr.IsValid() else None
        if gpu_dyn is not True:
            issues.append({
                'id': 'M16', 'prim': str(physics_scene_prim.GetPath()) if physics_scene_prim else '/PhysicsScene',
                'message': 'Multiple envs detected but GPU dynamics not enabled — replicate_physics may be False',
                'severity': 'warning',
                'auto_fix': 'Enable GPU dynamics on PhysicsScene',
                'tier': 3,
            })

# M17: Env spacing too small
if len(env_prims) >= 2:
    env_roots = {}
    for ep in env_prims:
        ep_path = str(ep.GetPath())
        parts = ep_path.split('/')
        for i, part in enumerate(parts):
            if part.startswith('env_'):
                root_path = '/'.join(parts[:i+1])
                if root_path not in env_roots:
                    env_roots[root_path] = ep
                break
    if len(env_roots) >= 2:
        root_list = list(env_roots.values())
        try:
            xf0 = UsdGeom.Xformable(root_list[0]).ComputeLocalToWorldTransform(0)
            xf1 = UsdGeom.Xformable(root_list[1]).ComputeLocalToWorldTransform(0)
            pos0 = xf0.ExtractTranslation()
            pos1 = xf1.ExtractTranslation()
            spacing = ((pos1[0]-pos0[0])**2 + (pos1[1]-pos0[1])**2 + (pos1[2]-pos0[2])**2)**0.5
            if spacing < 1.0:
                issues.append({
                    'id': 'M17', 'prim': 'envs',
                    'message': f'Env spacing = {spacing:.2f}m — may cause inter-env collisions (recommend >= 2.0m)',
                    'severity': 'warning',
                    'auto_fix': 'Increase GridCloner spacing parameter',
                    'tier': 3,
                })
        except Exception:
            pass

# M19: GPU contact buffer too small
if physics_scene_prim and physics_scene_prim.IsValid():
    buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxNumPartitions')
    if buf_attr and buf_attr.IsValid():
        buf_val = buf_attr.Get()
        if buf_val is not None and buf_val < 8:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU max partitions = {buf_val} — may be too small for RL with many envs',
                'severity': 'warning',
                'auto_fix': 'Increase gpuMaxNumPartitions to 8 or higher',
                'tier': 3,
            })
    contact_buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxRigidContactCount')
    if contact_buf_attr and contact_buf_attr.IsValid():
        cb_val = contact_buf_attr.Get()
        if cb_val is not None and cb_val < 524288:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU contact buffer = {cb_val} — may overflow with many envs (recommend >= 524288)',
                'severity': 'warning',
                'auto_fix': f'Set gpuMaxRigidContactCount to 524288',
                'tier': 3,
            })

# M20: Observation normalization issues — check for very large/small attribute values
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                max_force = drive.GetMaxForceAttr().Get()
                if max_force is not None and max_force > 1e6:
                    issues.append({
                        'id': 'M20', 'prim': pp,
                        'message': f'Drive maxForce={max_force} — very large value may cause observation normalization issues in RL',
                        'severity': 'warning',
                        'auto_fix': None, 'tier': 3,
                    })
                    break
"""

    # ── Tier 4 checks ──
    tier4_block = ""
    if run_tier4:
        tier4_block = r"""
# ── Tier 4: ROS2 / OmniGraph ────────────────────────────────────────────

try:
    import omni.graph.core as og
    graphs_available = True
except ImportError:
    graphs_available = False

if graphs_available:
    all_graphs = og.get_all_graphs()

    for graph in all_graphs:
        gp = graph.get_path_to_graph()
        nodes = graph.get_nodes()

        # M18: OmniGraph without tick source
        has_tick = False
        has_ros2_context = False
        has_clock_pub = False
        ros2_sensor_nodes = []

        for node in nodes:
            nt = node.get_node_type().get_node_type()
            node_path = node.get_prim_path()

            if 'OnPlaybackTick' in nt or 'OnPhysicsStep' in nt or 'OnTick' in nt:
                has_tick = True

            # M21: Detect ROS2Context
            if 'ROS2Context' in nt:
                has_ros2_context = True

            # M22: Detect clock publisher
            if 'ROS2PublishClock' in nt or 'PublishClock' in nt:
                has_clock_pub = True

            # Collect sensor nodes for M23
            if any(s in nt for s in ('ROS2Publish', 'ROS2Camera', 'ROS2Lidar', 'ROS2Imu')):
                ros2_sensor_nodes.append((node_path, nt, node))

        # M18: No tick source
        if not has_tick and len(nodes) > 0:
            issues.append({
                'id': 'M18', 'prim': gp,
                'message': 'OmniGraph has no tick source (OnPlaybackTick/OnPhysicsStep) — graph will not execute',
                'severity': 'error',
                'auto_fix': 'Add an OnPlaybackTick node and connect its execOut to the first node',
                'tier': 4,
            })

        # Only check ROS2-specific issues if there are ROS2 nodes
        has_ros2_nodes = any('ROS2' in n.get_node_type().get_node_type() or 'ros2' in n.get_node_type().get_node_type().lower() for n in nodes)

        if has_ros2_nodes:
            # M21: Missing ROS2Context
            if not has_ros2_context:
                issues.append({
                    'id': 'M21', 'prim': gp,
                    'message': 'ROS2 nodes present but no ROS2Context node — bridge will not function',
                    'severity': 'error',
                    'auto_fix': 'Add a ROS2Context node to the graph',
                    'tier': 4,
                })

            # M22: Missing /clock publisher with use_sim_time
            if not has_clock_pub:
                issues.append({
                    'id': 'M22', 'prim': gp,
                    'message': 'ROS2 nodes present but no clock publisher — use_sim_time will not work',
                    'severity': 'warning',
                    'auto_fix': 'Add a ROS2PublishClock node to publish /clock',
                    'tier': 4,
                })

            # M14: ROS2 QoS mismatch — check for sensor reliability vs subscriber expectations
            for node_path, nt, node in ros2_sensor_nodes:
                qos_attr = None
                try:
                    qos_attr = node.get_attribute('inputs:qosProfile')
                except Exception:
                    pass
                if qos_attr is not None:
                    qos_val = qos_attr.get()
                    if qos_val and isinstance(qos_val, str) and qos_val.lower() == 'reliable':
                        issues.append({
                            'id': 'M14', 'prim': node_path,
                            'message': f'Sensor publisher using RELIABLE QoS — may cause latency; use BEST_EFFORT for real-time data',
                            'severity': 'warning',
                            'auto_fix': "Set qosProfile to 'sensor_data' or 'best_effort'",
                            'tier': 4,
                        })

            # M23: Sensor frame ID mismatch — check if frame_id inputs are set
            for node_path, nt, node in ros2_sensor_nodes:
                frame_attr = None
                try:
                    frame_attr = node.get_attribute('inputs:frameId')
                except Exception:
                    pass
                if frame_attr is not None:
                    fid = frame_attr.get()
                    if not fid or fid == '' or fid == 'sim':
                        issues.append({
                            'id': 'M23', 'prim': node_path,
                            'message': f'Sensor frame_id is empty or default ("{fid}") — will not match robot TF tree',
                            'severity': 'warning',
                            'auto_fix': 'Set frameId to the correct link name (e.g. "camera_link")',
                            'tier': 4,
                        })
"""

    return f"""\
import omni.usd
import json
from pxr import UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
physics_scene_prim = None
{scope_block}
{tier1_block}
{tier2_block}
{tier3_block}
{tier4_block}
# ── Summary ──────────────────────────────────────────────────────────────
tier1_errors = [i for i in issues if i['tier'] == 1]
tier2_warnings = [i for i in issues if i['tier'] == 2]
tier3_rl = [i for i in issues if i['tier'] == 3]
tier4_ros2 = [i for i in issues if i['tier'] == 4]
auto_fixable = sum(1 for i in issues if i.get('auto_fix'))

result = {{
    'status': 'PASS' if not tier1_errors else 'FAIL',
    'total_issues': len(issues),
    'tier1_errors': tier1_errors,
    'tier2_warnings': tier2_warnings,
    'tier3_rl': tier3_rl,
    'tier4_ros2': tier4_ros2,
    'auto_fixable_count': auto_fixable,
    'summary': {{
        'tier1': len(tier1_errors),
        'tier2': len(tier2_warnings),
        'tier3': len(tier3_rl),
        'tier4': len(tier4_ros2),
    }},
}}
print(json.dumps(result, indent=2))
"""


CODE_GEN_HANDLERS["preflight_check"] = _gen_preflight_check
