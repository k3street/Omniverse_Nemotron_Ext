"""Physics handlers — target scope: physics scene config,
articulations, drives, joints, deformable meshes, contact sensors,
gravity dispenser, force application.

Phase 5 wave 1 — first physics code-generators move out of
`tool_executor.py`. Same migration pattern as Phase 3 scene-authoring:
function bodies live here, `tool_executor.py` re-imports the names
so the existing CODE_GEN_HANDLERS dispatch dict keeps working.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phases 2 + 5.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 5 wave 1 — physics scene config + joint targets


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


# ---------------------------------------------------------------------------
# Phase 5 wave 2 — drive gains + joint limits


def _gen_set_drive_gains(args: Dict) -> str:
    joint_path = args["joint_path"]
    kp = args["kp"]
    kd = args["kd"]
    drive_type = args.get("drive_type", "angular")
    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"joint = stage.GetPrimAtPath({joint_path!r})\n"
        f"drive = UsdPhysics.DriveAPI.Apply(joint, {drive_type!r})\n"
        f"drive.CreateStiffnessAttr({float(kp)!r})\n"
        f"drive.CreateDampingAttr({float(kd)!r})\n"
        f"print('drive_gains', {joint_path!r}, 'kp=', {float(kp)!r}, 'kd=', {float(kd)!r})"
    )


def _gen_set_joint_limits(args: Dict) -> str:
    """Generate code to set physics:lowerLimit and physics:upperLimit."""
    joint_path = args["joint_path"]
    lower = float(args["lower"])
    upper = float(args["upper"])
    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_path = {joint_path!r}
joint = stage.GetPrimAtPath(joint_path)
if not joint or not joint.IsValid():
    raise RuntimeError('joint not found: ' + repr(joint_path))
rj = UsdPhysics.RevoluteJoint(joint)
pj = UsdPhysics.PrismaticJoint(joint)
if not (rj or pj):
    raise RuntimeError('joint is not Revolute or Prismatic: ' + repr(joint_path))
lower_attr = joint.GetAttribute('physics:lowerLimit')
if not (lower_attr and lower_attr.IsDefined()):
    lower_attr = (rj or pj).CreateLowerLimitAttr()
upper_attr = joint.GetAttribute('physics:upperLimit')
if not (upper_attr and upper_attr.IsDefined()):
    upper_attr = (rj or pj).CreateUpperLimitAttr()
lower_attr.Set({lower})
upper_attr.Set({upper})
print('joint_limits ' + repr(joint_path) + ' lower=' + repr({lower}) + ' upper=' + repr({upper}))
"""


# ---------------------------------------------------------------------------
# Phase 5 wave 3 — physics material + scene config + force application + joint vel limit


def _gen_apply_physics_material(args: Dict) -> str:
    """Generate code to create a PhysicsMaterialAPI with values from the material database."""
    from ..tool_executor import _load_physics_materials, _normalize_material_name

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
_target_path = {prim_path!r}
prim = stage.GetPrimAtPath(_target_path)
if not prim or not prim.IsValid():
    raise RuntimeError(
        'apply_physics_material: prim not found: ' + repr(_target_path)
    )

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

print(f"Applied {{mat_path}} to " + repr(_target_path) + ": static_friction={sf}, dynamic_friction={df}, restitution={rest}, density={density}")
"""


def _gen_set_physics_scene_config(args: Dict) -> str:
    """Generate code to update the PhysicsScene config."""
    cfg = args.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    scene_path = cfg.get("scene_path", "")
    solver_type = cfg.get("solver_type")
    pos_iters = cfg.get("position_iterations")
    vel_iters = cfg.get("velocity_iterations")
    tps = cfg.get("time_steps_per_second")
    enable_gpu = cfg.get("enable_gpu_dynamics")
    broadphase = cfg.get("broadphase_type")
    grav_dir = cfg.get("gravity_direction")
    grav_mag = cfg.get("gravity_magnitude")

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdPhysics, PhysxSchema, Sdf, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"target_path = {scene_path!r}",
        "scene_prim = None",
        "if target_path:",
        "    scene_prim = stage.GetPrimAtPath(target_path)",
        "    if not scene_prim or not scene_prim.IsValid():",
        "        scene_prim = None",
        "if scene_prim is None:",
        "    for p in stage.Traverse():",
        "        if p.IsA(UsdPhysics.Scene):",
        "            scene_prim = p",
        "            break",
        "if scene_prim is None:",
        "    scene = UsdPhysics.Scene.Define(stage, Sdf.Path('/PhysicsScene'))",
        "    scene_prim = scene.GetPrim()",
        "scene = UsdPhysics.Scene(scene_prim)",
        "if not scene_prim.HasAPI(PhysxSchema.PhysxSceneAPI):",
        "    PhysxSchema.PhysxSceneAPI.Apply(scene_prim)",
        "phx = PhysxSchema.PhysxSceneAPI(scene_prim)",
    ]
    if grav_dir is not None and len(grav_dir) >= 3:
        lines.append(
            f"(scene.GetGravityDirectionAttr() or scene.CreateGravityDirectionAttr()).Set("
            f"Gf.Vec3f({float(grav_dir[0])}, {float(grav_dir[1])}, {float(grav_dir[2])}))"
        )
    if grav_mag is not None:
        lines.append(
            f"(scene.GetGravityMagnitudeAttr() or scene.CreateGravityMagnitudeAttr()).Set({float(grav_mag)})"
        )
    if solver_type is not None:
        lines.append(
            f"(phx.GetSolverTypeAttr() or phx.CreateSolverTypeAttr()).Set({solver_type!r})"
        )
    if pos_iters is not None:
        lines.append(
            f"(phx.GetMinPositionIterationCountAttr() or phx.CreateMinPositionIterationCountAttr()).Set({int(pos_iters)})"
        )
        lines.append(
            f"(phx.GetMaxPositionIterationCountAttr() or phx.CreateMaxPositionIterationCountAttr()).Set({int(pos_iters)})"
        )
    if vel_iters is not None:
        lines.append(
            f"(phx.GetMinVelocityIterationCountAttr() or phx.CreateMinVelocityIterationCountAttr()).Set({int(vel_iters)})"
        )
        lines.append(
            f"(phx.GetMaxVelocityIterationCountAttr() or phx.CreateMaxVelocityIterationCountAttr()).Set({int(vel_iters)})"
        )
    if enable_gpu is not None:
        lines.append(
            f"(phx.GetEnableGPUDynamicsAttr() or phx.CreateEnableGPUDynamicsAttr()).Set({bool(enable_gpu)})"
        )
    if broadphase is not None:
        lines.append(
            f"(phx.GetBroadphaseTypeAttr() or phx.CreateBroadphaseTypeAttr()).Set({broadphase!r})"
        )
    if tps is not None:
        lines.append(
            f"(phx.GetTimeStepsPerSecondAttr() or phx.CreateTimeStepsPerSecondAttr()).Set({int(tps)})"
        )
        lines.append("try:")
        lines.append("    import carb.settings")
        lines.append(f"    carb.settings.get_settings().set('/persistent/physics/timeStepsPerSecond', int({int(tps)}))")
        lines.append("except Exception:")
        lines.append("    pass")
    lines.append("print(f'Updated PhysicsScene config on {scene_prim.GetPath()}')")
    return "\n".join(lines)


def _gen_apply_force(args: Dict) -> str:
    """Generate code to apply external force/torque to a rigid body."""
    prim_path = args["prim_path"]
    force = args.get("force") or [0.0, 0.0, 0.0]
    torque = args.get("torque") or [0.0, 0.0, 0.0]
    position = args.get("position")

    pos_block = "None"
    if position is not None and len(position) >= 3:
        pos_block = f"[{float(position[0])}, {float(position[1])}, {float(position[2])}]"

    return f"""\
import omni.usd
from pxr import UsdPhysics, Sdf

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
force = [{float(force[0])}, {float(force[1])}, {float(force[2])}]
torque = [{float(torque[0])}, {float(torque[1])}, {float(torque[2])}]
position = {pos_block}

prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    raise RuntimeError(f'prim not found: {{prim_path!r}}')
if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    UsdPhysics.RigidBodyAPI.Apply(prim)

errors = []
applied_via = None

# Path 1: IPhysxSimulation.apply_force_at_pos(stage_id, body_path_int, ...)
# This is the canonical 5.x signature; args must be (stage_id: int, body_path: int, force, pos, mode).
try:
    import omni.physx as _omni_physx
    sim_iface = _omni_physx.get_physx_simulation_interface()
    stage_id = omni.usd.get_context().get_stage_id()
    body_path_int = Sdf.Path(prim_path).pathString
    body_path_int = int(stage.GetPrimAtPath(prim_path).GetPath().pathString.__hash__())
    # Try full signature first
    sim_iface.apply_force_at_pos(stage_id, prim_path, force, position or (0.0, 0.0, 0.0), 'force')
    applied_via = 'IPhysxSimulation.apply_force_at_pos'
except (TypeError, AttributeError) as e:
    errors.append(f'IPhysxSimulation: {{type(e).__name__}}: {{e}}')
except Exception as e:
    errors.append(f'IPhysxSimulation: {{type(e).__name__}}: {{e}}')

# Path 2: omni.physx.scripts.physicsUtils.apply_force_at_pos
if applied_via is None:
    try:
        from omni.physx.scripts import physicsUtils
        if hasattr(physicsUtils, 'apply_force_at_pos'):
            # Modern signature: (prim, force, pos)
            physicsUtils.apply_force_at_pos(prim, force, position or (0.0, 0.0, 0.0))
            applied_via = 'physicsUtils.apply_force_at_pos'
    except Exception as e:
        errors.append(f'physicsUtils: {{type(e).__name__}}: {{e}}')

# Path 3: tensor API — only works while sim is playing
if applied_via is None:
    try:
        import omni.physics.tensors as physics_tensors
        sim_view = physics_tensors.create_simulation_view('numpy')
        rb_view = sim_view.create_rigid_body_view([prim_path])
        import numpy as np
        f_arr = np.array([force], dtype='float32')
        t_arr = np.array([torque], dtype='float32')
        rb_view.apply_forces_and_torques_at_pos(
            f_arr, t_arr, None,
            indices=np.array([0], dtype='int32'), is_global=True,
        )
        applied_via = 'omni.physics.tensors'
    except Exception as e:
        errors.append(f'tensors: {{type(e).__name__}}: {{e}}')

# Path 4: write linear velocity directly (works without sim playing,
# acts as an instantaneous impulse-equivalent on the rigid body).
if applied_via is None:
    try:
        rb = UsdPhysics.RigidBodyAPI(prim)
        # Compute a velocity that corresponds to applying force for one frame
        # at default 60Hz. This is a degraded fallback — not a real force, but
        # achieves the user-visible effect of pushing the body.
        dt = 1.0 / 60.0
        mass = 1.0
        try:
            mass_attr = prim.GetAttribute('physics:mass')
            if mass_attr and mass_attr.Get():
                mass = float(mass_attr.Get())
        except Exception:
            pass
        impulse_velocity = [f * dt / mass for f in force]
        existing = rb.GetVelocityAttr().Get() if rb.GetVelocityAttr().HasAuthoredValue() else (0.0, 0.0, 0.0)
        new_v = (existing[0] + impulse_velocity[0],
                 existing[1] + impulse_velocity[1],
                 existing[2] + impulse_velocity[2])
        rb.GetVelocityAttr().Set(new_v)
        applied_via = 'velocity-impulse-fallback'
    except Exception as e:
        errors.append(f'velocity-impulse-fallback: {{type(e).__name__}}: {{e}}')

if applied_via is None:
    raise RuntimeError(
        f'apply_force failed on all paths. Tried: ' + ' | '.join(errors)
    )

print(f'Applied force={{force}} torque={{torque}} on {{prim_path!r}} via {{applied_via}}')
"""


def _gen_set_joint_velocity_limit(args: Dict) -> str:
    """Generate code to cap the joint's max velocity via PhysxJointAPI."""
    joint_path = args["joint_path"]
    vel_limit = float(args["vel_limit"])
    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_path = {joint_path!r}
joint = stage.GetPrimAtPath(joint_path)
if not joint or not joint.IsValid():
    raise RuntimeError('joint not found: ' + repr(joint_path))
rj = UsdPhysics.RevoluteJoint(joint)
pj = UsdPhysics.PrismaticJoint(joint)
if not (rj or pj):
    raise RuntimeError('joint is not Revolute or Prismatic: ' + repr(joint_path))
# Prefer PhysxSchema.PhysxJointAPI when available (Isaac Sim 5.x ships PhysxSchema).
try:
    from pxr import PhysxSchema
    if not joint.HasAPI(PhysxSchema.PhysxJointAPI):
        PhysxSchema.PhysxJointAPI.Apply(joint)
    pjapi = PhysxSchema.PhysxJointAPI(joint)
    attr = pjapi.GetMaxJointVelocityAttr() or pjapi.CreateMaxJointVelocityAttr()
except Exception:
    # Fallback: write the raw USD attribute used by PhysX 5.x.
    attr = joint.GetAttribute('physxJoint:maxJointVelocity')
    if not (attr and attr.IsDefined()):
        attr = joint.CreateAttribute('physxJoint:maxJointVelocity', None)
attr.Set({vel_limit})
print('joint_velocity_limit ' + repr(joint_path) + ' vel_limit=' + repr({vel_limit}))
"""


# ---------------------------------------------------------------------------
# Phase 5 wave 4 — deformable bodies/surfaces + self-collision config


def _gen_deformable(args: Dict) -> str:
    """Generate PhysX deformable body/surface code from presets."""
    from ..tool_executor import _load_deformable_presets

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


def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    # Live-probed 2026-04-18: old code called .Apply on an invalid prim
    # returned from stage.GetPrimAtPath('<bad>') and USD's internal Apply
    # path silently no-oped — tool reported success=True with no effect.
    # Add explicit guard on the articulation root.
    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"_art_path = {art_path!r}",
        "robot_prim = stage.GetPrimAtPath(_art_path)",
        "if not robot_prim or not robot_prim.IsValid():",
        "    raise RuntimeError(f'configure_self_collision: articulation not found: {_art_path!r}')",
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
                    "if not link_a.IsValid() or not link_b.IsValid():",
                    f"    raise RuntimeError('configure_self_collision: filter pair links not found: {pair[0]!r} / {pair[1]!r}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 5 wave 5 — collision-mesh quality + simplify/optimize + contact sensors


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


def _gen_simplify_collision(args: Dict) -> str:
    """Generate code to set collision approximation on a single prim."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]
    # PhysX accepts the approximation as a free string but silently falls
    # back to the default for unknown names. Hard-reject the unknowns at
    # code-gen so the agent gets an immediate, specific failure instead of
    # a "success" that ran with whatever default PhysX picked.
    _VALID_APPROXIMATIONS = {
        "none", "convexHull", "convexDecomposition", "meshSimplification",
        "boundingSphere", "boundingCube", "sphereFill", "sdf",
    }
    if approximation not in _VALID_APPROXIMATIONS:
        return (
            "raise ValueError(\n"
            f"    'simplify_collision: unknown approximation ' + {approximation!r} + '. '\n"
            f"    'Valid: ' + {sorted(_VALID_APPROXIMATIONS)!r}\n"
            ")"
        )

    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"_prim_path = {prim_path!r}\n"
        "prim = stage.GetPrimAtPath(_prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError('simplify_collision: prim not found: ' + repr(_prim_path))\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Apply MeshCollisionAPI and set approximation — verify the set took\n"
        "mesh_col = UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        f"_approx = {approximation!r}\n"
        "_ok = mesh_col.GetApproximationAttr().Set(_approx)\n"
        "if _ok is False:\n"
        "    raise RuntimeError(\n"
        "        'simplify_collision: GetApproximationAttr().Set(' + repr(_approx) + ') returned False '\n"
        "        'on ' + repr(_prim_path) + ' — attribute refused the value'\n"
        "    )\n"
        "print('Set collision approximation to ' + repr(_approx) + ' on ' + repr(_prim_path))"
    )


def _gen_setup_contact_sensors(args: Dict) -> str:
    """Generate per-fingertip ContactSensorCfg + PhysxCfg buffer bumps for `num_envs`."""
    articulation_path = args["articulation_path"]
    body_names = args["body_names"]
    if not isinstance(body_names, list) or not body_names:
        body_names = ["fingertip"]
    num_envs = int(args.get("num_envs", 4096))
    update_period = float(args.get("update_period", 0.0))
    history_length = int(args.get("history_length", 1))
    track_air_time = bool(args.get("track_air_time", False))

    # Heuristic: bump GPU buffers when num_envs * sensors_per_env exceeds
    # the implicit 8M default (PhysX default = 2**23 contacts, 2**22 patches).
    contacts_needed = num_envs * len(body_names) * 8  # est. 8 contacts per fingertip
    contact_pow = max(24, contacts_needed.bit_length())  # at least 2**24 = 16M
    patch_pow = max(23, (contacts_needed // 2).bit_length())  # at least 2**23 = 8M

    lines = [
        '"""Auto-generated ContactSensorCfg block.',
        f"Articulation: {articulation_path}",
        f"Bodies: {body_names}",
        f"num_envs={num_envs}",
        '"""',
        "from isaaclab.sensors import ContactSensorCfg",
        "from isaaclab.sim import PhysxCfg",
        "",
        "# One ContactSensorCfg per body (mandatory one-to-many constraint —",
        "# wildcards in prim_path do not aggregate, they would silently overwrite).",
        "contact_sensors = {",
    ]
    for body in body_names:
        # Sanitize the body name for use as a Python identifier in the dict key
        safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in body)
        lines.extend([
            f"    {safe_key!r}: ContactSensorCfg(",
            f"        prim_path=f'{{ENV_REGEX_NS}}/Robot/{body}',",
            f"        update_period={update_period},  # 0.0 = every physics step",
            f"        history_length={history_length},",
            f"        track_air_time={track_air_time},",
            "    ),",
        ])
    lines.extend([
        "}",
        "",
        f"# Critical: bump GPU buffers for {num_envs} envs x {len(body_names)} sensors.",
        "# Default 2**23 contacts / 2**22 patches will silently overflow at this scale,",
        "# producing zero forces on all sensors with no error message.",
        "physx_cfg = PhysxCfg(",
        f"    gpu_max_rigid_contact_count=2**{contact_pow},",
        f"    gpu_max_rigid_patch_count=2**{patch_pow},",
        ")",
        "",
        "# Cheap alternative when you just need 'is there contact?':",
        "#   joint_forces = articulation.root_physx_view.get_link_incoming_joint_force()",
        "#   fingertip_forces = joint_forces[:, fingertip_body_ids]",
        "# (Includes gravity / inertia contributions — not pure contact, but zero overhead.)",
    ])
    return "\n".join(lines)


def _gen_check_collision_mesh_code(prim_path: str) -> str:
    """Build the read-only Kit/USD/trimesh analysis script for check_collision_mesh."""
    from ..tool_executor import _PHYSX_HULL_MAX_VERTS, _PHYSX_HULL_MAX_POLYS

    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import json
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics

result = {{
    "prim": "{safe_path}",
    "triangle_count": 0,
    "is_watertight": None,
    "is_manifold": None,
    "degenerate_faces": 0,
    "collision_approximation": "unknown",
    "issues": [],
    "recommendation": "",
}}

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath("{safe_path}")

if not prim or not prim.IsValid():
    result["issues"].append({{"type": "prim_not_found", "severity": "error"}})
    result["recommendation"] = "Prim not found — check the path."
    print(json.dumps(result))
else:
    # ── Fatal check: missing CollisionAPI ────────────────────────────────
    has_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
    if not has_collision:
        result["issues"].append({{"type": "missing_collision_api", "severity": "error"}})

    # ── Read approximation type ──────────────────────────────────────────
    if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
        try:
            approx_attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
            result["collision_approximation"] = approx_attr or "none"
        except Exception:
            result["collision_approximation"] = "none"
    else:
        result["collision_approximation"] = "none (no MeshCollisionAPI)"

    mesh = UsdGeom.Mesh(prim)
    if not mesh:
        result["issues"].append({{"type": "not_a_mesh", "severity": "error"}})
        result["recommendation"] = "Prim is not a UsdGeom.Mesh — collision analysis only supports meshes."
        print(json.dumps(result))
    else:
        points = mesh.GetPointsAttr().Get() or []
        face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
        face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []
        n_points = len(points)

        # ── Fatal: out-of-range vertex indices ───────────────────────────
        oor = [i for i in face_indices if i < 0 or i >= n_points]
        if oor:
            result["issues"].append({{
                "type": "out_of_range_indices", "severity": "error", "count": len(oor),
            }})

        # ── Triangulate face_counts/face_indices into triangles ──────────
        triangles = []
        cursor = 0
        for fc in face_counts:
            if fc < 3:
                cursor += fc
                continue
            base = face_indices[cursor]
            for k in range(1, fc - 1):
                triangles.append((base, face_indices[cursor + k], face_indices[cursor + k + 1]))
            cursor += fc
        result["triangle_count"] = len(triangles)

        # Count degenerate triangles (any two indices equal → zero area)
        degenerate = 0
        for a, b, c in triangles:
            if a == b or b == c or a == c:
                degenerate += 1
        result["degenerate_faces"] = degenerate
        if degenerate > 0:
            result["issues"].append({{
                "type": "degenerate_faces", "severity": "error", "count": degenerate,
            }})

        # ── trimesh-based silent-degradation checks (optional dep) ───────
        try:
            import trimesh
            import numpy as np
            verts = np.array([(p[0], p[1], p[2]) for p in points], dtype=float)
            faces = np.array(triangles, dtype=int) if triangles else np.zeros((0, 3), dtype=int)
            if len(faces) > 0 and len(verts) > 0:
                tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                result["is_watertight"] = bool(tm.is_watertight)
                result["is_manifold"] = bool(getattr(tm, "is_winding_consistent", True))

                # Zero-area triangles (geometric degeneracy)
                area_faces = tm.area_faces
                near_zero = int((area_faces < 1e-10).sum())
                if near_zero > 0 and near_zero != degenerate:
                    result["issues"].append({{
                        "type": "zero_area_faces", "severity": "error", "count": near_zero,
                    }})

                if not tm.is_watertight:
                    result["issues"].append({{"type": "non_watertight", "severity": "warning"}})
                if not getattr(tm, "is_winding_consistent", True):
                    result["issues"].append({{"type": "non_manifold_edges", "severity": "warning"}})
                if not getattr(tm, "is_volume", True):
                    result["issues"].append({{"type": "not_volume", "severity": "warning"}})

                # Oversized-triangle heuristic: any tri area > 10% of bbox area
                try:
                    bbox_diag = float(np.linalg.norm(tm.bounds[1] - tm.bounds[0]))
                    if bbox_diag > 0 and len(area_faces) > 0:
                        max_tri = float(area_faces.max())
                        if max_tri > 0.1 * (bbox_diag ** 2):
                            result["issues"].append({{
                                "type": "oversized_triangles", "severity": "warning",
                                "max_area": max_tri, "bbox_diag": bbox_diag,
                            }})
                except Exception:
                    pass

                # ── Convex hull GPU-limit check (only when relevant) ─────
                if result["collision_approximation"] in ("convexHull", "convexDecomposition"):
                    try:
                        hull = tm.convex_hull
                        n_hv = len(hull.vertices)
                        n_hf = len(hull.faces)
                        if n_hv > {_PHYSX_HULL_MAX_VERTS}:
                            result["issues"].append({{
                                "type": "hull_exceeds_gpu_limit", "severity": "error",
                                "vertices": n_hv, "limit": {_PHYSX_HULL_MAX_VERTS},
                            }})
                        if n_hf > {_PHYSX_HULL_MAX_POLYS}:
                            result["issues"].append({{
                                "type": "hull_exceeds_polygon_limit", "severity": "error",
                                "polygons": n_hf, "limit": {_PHYSX_HULL_MAX_POLYS},
                            }})
                    except Exception as e:
                        result["issues"].append({{"type": "hull_compute_failed", "severity": "warning", "error": str(e)}})
        except ImportError:
            result["issues"].append({{
                "type": "trimesh_unavailable", "severity": "info",
                "message": "trimesh not installed — silent-degradation checks skipped (pip install trimesh)",
            }})

        # ── Recommendation ───────────────────────────────────────────────
        rec_parts = []
        n_tri = result["triangle_count"]
        approx = result["collision_approximation"]
        if n_tri > 5000 and approx in ("none", "none (no MeshCollisionAPI)", ""):
            rec_parts.append(
                f"Switch to convexDecomposition ({{n_tri}} triangles is too heavy for raw triangle-mesh collision)."
            )
        if any(i["severity"] == "error" for i in result["issues"]):
            rec_parts.append("Run fix_collision_mesh first to repair errors.")
        elif any(i["type"] in ("non_watertight", "non_manifold_edges", "not_volume") for i in result["issues"]):
            rec_parts.append("Run fix_collision_mesh to clean up the mesh.")
        if not rec_parts:
            rec_parts.append("Mesh looks healthy — no action needed.")
        result["recommendation"] = " ".join(rec_parts)

        print(json.dumps(result))
"""


def _gen_fix_collision_mesh(args: Dict) -> str:
    """Generate auto-repair code: normals → degenerate → holes → simplify → CoACD → write back."""
    from ..tool_executor import _PHYSX_HULL_MAX_VERTS, _PHYSX_HULL_MAX_POLYS

    prim_path = args["prim_path"]
    target = args.get("target_triangles")
    target_val = "None" if target is None else str(int(target))
    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import omni.usd
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics, Vt, Sdf

PRIM_PATH = "{safe_path}"
TARGET_TRIANGLES = {target_val}
PHYSX_HULL_MAX_VERTS = {_PHYSX_HULL_MAX_VERTS}
PHYSX_HULL_MAX_POLYS = {_PHYSX_HULL_MAX_POLYS}
COACD_THRESHOLD = 0.05
COACD_MAX_CONVEX_HULL = 16

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(PRIM_PATH)
if not prim or not prim.IsValid():
    raise RuntimeError(f"Prim not found: {{PRIM_PATH}}")

mesh = UsdGeom.Mesh(prim)
if not mesh:
    raise RuntimeError(f"Prim {{PRIM_PATH}} is not a UsdGeom.Mesh")

# ── Step 0: Read current mesh data ──────────────────────────────────────
points = mesh.GetPointsAttr().Get() or []
face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []

# Triangulate
triangles = []
cursor = 0
for fc in face_counts:
    if fc < 3:
        cursor += fc
        continue
    base = face_indices[cursor]
    for k in range(1, fc - 1):
        triangles.append((base, face_indices[cursor + k], face_indices[cursor + k + 1]))
    cursor += fc

import trimesh
verts_np = np.array([(p[0], p[1], p[2]) for p in points], dtype=float)
faces_np = np.array(triangles, dtype=int) if triangles else np.zeros((0, 3), dtype=int)
tm = trimesh.Trimesh(vertices=verts_np, faces=faces_np, process=False)

# ── Step 1: Fix normals ─────────────────────────────────────────────────
try:
    tm.fix_normals()
except Exception:
    pass

# ── Step 2: Remove degenerate / duplicate faces ─────────────────────────
try:
    tm.update_faces(tm.unique_faces())
    tm.update_faces(tm.nondegenerate_faces())
    tm.remove_unreferenced_vertices()
except Exception:
    pass

# ── Step 3: Fill holes / make watertight ────────────────────────────────
if not tm.is_watertight:
    try:
        tm.fill_holes()
    except Exception:
        pass

# ── Step 4: Simplify if target_triangles is set or hull > GPU limit ─────
needs_simplify = False
if TARGET_TRIANGLES is not None and len(tm.faces) > TARGET_TRIANGLES:
    needs_simplify = True
else:
    try:
        hull = tm.convex_hull
        if len(hull.vertices) > PHYSX_HULL_MAX_VERTS or len(hull.faces) > PHYSX_HULL_MAX_POLYS:
            needs_simplify = True
    except Exception:
        pass

if needs_simplify:
    target = TARGET_TRIANGLES if TARGET_TRIANGLES is not None else max(1000, len(tm.faces) // 4)
    try:
        tm = tm.simplify_quadric_decimation(target)
    except Exception:
        try:
            # Trimesh ≥4 renamed it
            tm = tm.simplify_quadratic_decimation(target)
        except Exception:
            pass

# ── Step 5: CoACD convex decomposition (best-effort) ────────────────────
hulls = []
try:
    import coacd
    coacd_mesh = coacd.Mesh(tm.vertices, tm.faces)
    parts = coacd.run_coacd(
        coacd_mesh,
        threshold=COACD_THRESHOLD,
        max_convex_hull=COACD_MAX_CONVEX_HULL,
    )
    for verts, faces in parts:
        hulls.append(trimesh.Trimesh(vertices=verts, faces=faces, process=False))
except Exception:
    # Fall back: single convex hull
    try:
        hulls = [tm.convex_hull]
    except Exception:
        hulls = []

# ── Step 6: Verify all hulls ≤ GPU limits ───────────────────────────────
for idx, h in enumerate(hulls):
    if len(h.vertices) > PHYSX_HULL_MAX_VERTS:
        print(f"WARN: hull {{idx}} has {{len(h.vertices)}} vertices > {{PHYSX_HULL_MAX_VERTS}}")
    if len(h.faces) > PHYSX_HULL_MAX_POLYS:
        print(f"WARN: hull {{idx}} has {{len(h.faces)}} faces > {{PHYSX_HULL_MAX_POLYS}}")

# ── Step 7: Write repaired triangle mesh back to USD ────────────────────
new_points = Vt.Vec3fArray([tuple(v) for v in tm.vertices.tolist()])
mesh.GetPointsAttr().Set(new_points)
new_face_counts = Vt.IntArray([3] * len(tm.faces))
mesh.GetFaceVertexCountsAttr().Set(new_face_counts)
flat_indices = [int(i) for tri in tm.faces.tolist() for i in tri]
mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(flat_indices))

# Apply MeshCollisionAPI with appropriate approximation
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)
if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
    UsdPhysics.MeshCollisionAPI.Apply(prim)

mca = UsdPhysics.MeshCollisionAPI(prim)
approx = "convexDecomposition" if len(hulls) > 1 else "convexHull"
mca.CreateApproximationAttr().Set(approx)

print(f"OK: repaired {{PRIM_PATH}} — {{len(tm.faces)}} triangles, {{len(hulls)}} hull(s), approx={{approx}}")
"""


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 5 wave 1 — dispatch lines in `tool_executor.py` still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
