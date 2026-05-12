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
# Phase 6 wave 22 — stragglers


def _gen_set_linear_velocity(args: Dict) -> str:
    """Generate code to set rigid body linear velocity."""
    prim_path = args["prim_path"]
    vel = args.get("vel") or [0.0, 0.0, 0.0]
    vx, vy, vz = float(vel[0]), float(vel[1]), float(vel[2])
    return f"""\
import omni.usd
from pxr import UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    raise RuntimeError('prim not found: ' + repr(prim_path))
if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    UsdPhysics.RigidBodyAPI.Apply(prim)
rb = UsdPhysics.RigidBodyAPI(prim)
attr = rb.GetVelocityAttr() or rb.CreateVelocityAttr()
attr.Set(Gf.Vec3f({vx}, {vy}, {vz}))
print('Set linear velocity on ' + repr(prim_path) + ' to ({vx}, {vy}, {vz}) m/s')
"""


def _gen_compute_convex_hull(args: Dict) -> str:
    """Apply convexHull collision approximation, optionally export hull mesh."""
    prim_path = args["prim_path"]
    export_hull_path = args.get("export_hull_path")
    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf, Vt",
        "",
        f"prim_path = {prim_path!r}",
        f"export_hull_path = {export_hull_path!r}",
        "stage = omni.usd.get_context().get_stage()",
        "prim = stage.GetPrimAtPath(prim_path)",
        "if not prim or not prim.IsValid():",
        "    raise RuntimeError(f'prim not found: {prim_path}')",
        "if not prim.IsA(UsdGeom.Mesh):",
        "    raise RuntimeError(f'prim is not a Mesh: {prim.GetTypeName()}')",
        "",
        "# 1) Mark the prim as a collider, then declare convexHull approximation",
        "UsdPhysics.CollisionAPI.Apply(prim)",
        "mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(prim)",
        "approx_attr = mesh_collision.GetApproximationAttr()",
        "if not approx_attr or not approx_attr.IsDefined():",
        "    approx_attr = mesh_collision.CreateApproximationAttr()",
        "approx_attr.Set(UsdPhysics.Tokens.convexHull)",
        "",
        "exported_path = None",
        "if export_hull_path:",
        "    # 2) Compute the convex hull (scipy if available, else manual gift-wrap)",
        "    mesh = UsdGeom.Mesh(prim)",
        "    xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())",
        "    local_points = mesh.GetPointsAttr().Get() or []",
        "    world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]",
        "    hull_vertices = []",
        "    hull_triangles = []",
        "    if len(world_points) < 4:",
        "        raise RuntimeError(f'need at least 4 points for a 3D hull, got {len(world_points)}')",
        "    try:",
        "        import numpy as np",
        "        from scipy.spatial import ConvexHull",
        "        pts = np.array([(p[0], p[1], p[2]) for p in world_points], dtype=float)",
        "        hull = ConvexHull(pts)",
        "        index_remap = {orig: new for new, orig in enumerate(sorted(set(int(i) for i in hull.vertices)))}",
        "        hull_vertices = [tuple(pts[orig]) for orig in sorted(index_remap.keys())]",
        "        for simplex in hull.simplices:",
        "            tri = tuple(index_remap[int(i)] for i in simplex)",
        "            hull_triangles.append(tri)",
        "    except Exception:",
        "        # Manual fallback: just take the AABB-corner hull (8 verts, 12 triangles).",
        "        # This is a coarse but always-valid convex envelope when scipy is missing.",
        "        xs = [p[0] for p in world_points]",
        "        ys = [p[1] for p in world_points]",
        "        zs = [p[2] for p in world_points]",
        "        mn = (min(xs), min(ys), min(zs))",
        "        mx = (max(xs), max(ys), max(zs))",
        "        hull_vertices = [",
        "            (mn[0], mn[1], mn[2]), (mx[0], mn[1], mn[2]),",
        "            (mx[0], mx[1], mn[2]), (mn[0], mx[1], mn[2]),",
        "            (mn[0], mn[1], mx[2]), (mx[0], mn[1], mx[2]),",
        "            (mx[0], mx[1], mx[2]), (mn[0], mx[1], mx[2]),",
        "        ]",
        "        hull_triangles = [",
        "            (0, 1, 2), (0, 2, 3),  # -Z",
        "            (4, 6, 5), (4, 7, 6),  # +Z",
        "            (0, 4, 5), (0, 5, 1),  # -Y",
        "            (3, 2, 6), (3, 6, 7),  # +Y",
        "            (0, 3, 7), (0, 7, 4),  # -X",
        "            (1, 5, 6), (1, 6, 2),  # +X",
        "        ]",
        "    # 3) Author hull mesh prim",
        "    hull_prim = stage.DefinePrim(export_hull_path, 'Mesh')",
        "    hull_mesh = UsdGeom.Mesh(hull_prim)",
        "    hull_mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in hull_vertices])",
        "    hull_mesh.CreateFaceVertexCountsAttr([3] * len(hull_triangles))",
        "    flat_indices = [idx for tri in hull_triangles for idx in tri]",
        "    hull_mesh.CreateFaceVertexIndicesAttr(flat_indices)",
        "    exported_path = export_hull_path",
        "",
        "print(f'compute_convex_hull applied to {prim_path} (export={exported_path})')",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 7 wave 2 — physics getters (17 data handlers)


async def _handle_get_articulation_state(args: Dict) -> Dict:
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import UsdPhysics
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
joints = []
for child in prim.GetAllChildren():
    if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
        joints.append({{'name': child.GetName(), 'path': str(child.GetPath())}})
result = {{'articulation_path': '{prim_path}', 'joints': joints}}
print(json.dumps(result))
"""
    return await kit_tools.queue_exec_patch(code, f"Read articulation state for {prim_path}")


async def _handle_get_physics_errors(args: Dict) -> Dict:
    """Filter console logs for PhysX-specific errors and warnings."""
    from .. import kit_tools
    from ..tool_executor import _PHYSX_ERROR_RE
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


async def _handle_get_joint_limits(args: Dict) -> Dict:
    from .. import kit_tools
    articulation = args["articulation"]
    joint_name = args["joint_name"]
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics
import json

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'joint_name': {joint_name!r}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joint_prim = None
    for p in Usd.PrimRange(art):
        if p.GetName() == {joint_name!r}:
            joint_prim = p
            break
    if joint_prim is None:
        result['error'] = 'joint not found'
    else:
        result['joint_path'] = str(joint_prim.GetPath())
        joint = UsdPhysics.RevoluteJoint(joint_prim) or UsdPhysics.PrismaticJoint(joint_prim)
        if not joint:
            result['error'] = 'joint is not Revolute or Prismatic'
        else:
            lower_attr = joint_prim.GetAttribute('physics:lowerLimit')
            upper_attr = joint_prim.GetAttribute('physics:upperLimit')
            result['lower'] = lower_attr.Get() if lower_attr and lower_attr.IsDefined() else None
            result['upper'] = upper_attr.Get() if upper_attr and upper_attr.IsDefined() else None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_limits {articulation}.{joint_name}")


async def _handle_get_contact_report(args: Dict) -> Dict:
    from .. import kit_tools
    prim_path = args["prim_path"]
    max_contacts = int(args.get("max_contacts", 50))
    code = f"""\
import omni.usd
import json

prim_path = {prim_path!r}
max_contacts = {max_contacts}

# Pull the running contact buffer from the global ContactReporter (set up by
# set_clearance_monitor or apply_api_schema(PhysxContactReportAPI)). When no
# buffer exists yet, return an empty report instead of crashing so callers can
# tell apart "no contacts" from "API not applied".
buf = globals().get('_ATOMIC_CONTACT_BUFFER')
contacts = []
if buf is not None:
    for entry in list(buf)[-max_contacts:]:
        if entry.get('actor0') == prim_path or entry.get('actor1') == prim_path:
            contacts.append(entry)

result = {{
    'prim_path': prim_path,
    'contact_count': len(contacts),
    'contacts': contacts,
    'buffer_initialized': buf is not None,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_contact_report {prim_path}")


async def _handle_get_joint_targets(args: Dict) -> Dict:
    """Read per-joint drive/velocity TARGETS (what the controller is aiming
    for), distinct from current state. Used to verify 'robot will move on
    Play' claims — if DriveAPI targets aren't authored, the robot won't move."""
    from .. import kit_tools
    articulation_path = args["articulation_path"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath({articulation_path!r})
result = {{'articulation_path': {articulation_path!r}}}
if not root or not root.IsValid():
    result['error'] = 'articulation not found'
    result['joints'] = []
else:
    joints = []
    for p in Usd.PrimRange(root):
        if not (p.IsA(UsdPhysics.RevoluteJoint) or p.IsA(UsdPhysics.PrismaticJoint)):
            continue
        entry = {{'path': str(p.GetPath()), 'type': str(p.GetTypeName())}}
        has_drive = False
        for suffix in ('angular', 'linear'):
            drive_api = UsdPhysics.DriveAPI.Get(p, suffix)
            if drive_api:
                tp = drive_api.GetTargetPositionAttr()
                tv = drive_api.GetTargetVelocityAttr()
                stiffness = drive_api.GetStiffnessAttr()
                damping = drive_api.GetDampingAttr()
                if tp and tp.IsAuthored():
                    entry[f'{{suffix}}_target_position'] = float(tp.Get() or 0.0)
                    has_drive = True
                if tv and tv.IsAuthored():
                    entry[f'{{suffix}}_target_velocity'] = float(tv.Get() or 0.0)
                    has_drive = True
                if stiffness and stiffness.IsAuthored():
                    entry[f'{{suffix}}_stiffness'] = float(stiffness.Get() or 0.0)
                if damping and damping.IsAuthored():
                    entry[f'{{suffix}}_damping'] = float(damping.Get() or 0.0)
        entry['has_drive'] = has_drive
        joints.append(entry)
    result['joints'] = joints
    result['joint_count'] = len(joints)
    result['joints_with_drive'] = sum(1 for j in joints if j.get('has_drive'))
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_targets {articulation_path}")


async def _handle_get_linear_velocity(args: Dict) -> Dict:
    """Return rigid body linear velocity via UsdPhysics.RigidBodyAPI."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    result['error'] = 'PhysicsRigidBodyAPI not applied — apply it first'
    result['has_rigid_body_api'] = False
else:
    rb = UsdPhysics.RigidBodyAPI(prim)
    attr = rb.GetVelocityAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['linear_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['linear_velocity'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['linear_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    result['units'] = 'm/s'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_linear_velocity {prim_path}")


async def _handle_get_angular_velocity(args: Dict) -> Dict:
    """Return rigid body angular velocity via UsdPhysics.RigidBodyAPI."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    result['error'] = 'PhysicsRigidBodyAPI not applied — apply it first'
    result['has_rigid_body_api'] = False
else:
    rb = UsdPhysics.RigidBodyAPI(prim)
    attr = rb.GetAngularVelocityAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['angular_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['angular_velocity'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['angular_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    result['units'] = 'deg/s'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_angular_velocity {prim_path}")


async def _handle_get_mass(args: Dict) -> Dict:
    """Return current rigid body mass via UsdPhysics.MassAPI."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'units': 'kg'}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.MassAPI):
    result['has_mass_api'] = False
    result['mass'] = 0.0
    result['note'] = 'PhysicsMassAPI not applied — PhysX will compute mass from collision geometry + density'
else:
    result['has_mass_api'] = True
    mass_api = UsdPhysics.MassAPI(prim)
    attr = mass_api.GetMassAttr()
    if attr and attr.HasAuthoredValue():
        result['mass'] = float(attr.Get())
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        result['mass'] = float(v) if v is not None else 0.0
        result['authored'] = False
    den_attr = mass_api.GetDensityAttr()
    if den_attr and den_attr.HasAuthoredValue():
        result['density_kg_m3'] = float(den_attr.Get())
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_mass {prim_path}")


async def _handle_get_inertia(args: Dict) -> Dict:
    """Return diagonal inertia tensor via UsdPhysics.MassAPI."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'units': 'kg*m^2'}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.MassAPI):
    result['has_mass_api'] = False
    result['diagonal_inertia'] = [0.0, 0.0, 0.0]
    result['note'] = 'PhysicsMassAPI not applied — PhysX will compute inertia from collision geometry'
else:
    result['has_mass_api'] = True
    mass_api = UsdPhysics.MassAPI(prim)
    attr = mass_api.GetDiagonalInertiaAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['diagonal_inertia'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['diagonal_inertia'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['diagonal_inertia'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    com_attr = mass_api.GetCenterOfMassAttr()
    if com_attr and com_attr.HasAuthoredValue():
        com = com_attr.Get()
        result['center_of_mass'] = [float(com[0]), float(com[1]), float(com[2])]
    pq_attr = mass_api.GetPrincipalAxesAttr()
    if pq_attr and pq_attr.HasAuthoredValue():
        q = pq_attr.Get()
        result['principal_axes_quat'] = [float(q.GetReal()),
                                         float(q.GetImaginary()[0]),
                                         float(q.GetImaginary()[1]),
                                         float(q.GetImaginary()[2])]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_inertia {prim_path}")


async def _handle_get_physics_scene_config(args: Dict) -> Dict:
    """Read the global PhysicsScene config: gravity, solver, iterations, dt, GPU."""
    from .. import kit_tools
    scene_path = args.get("scene_path", "")
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
result = {{}}
target = {scene_path!r}
scene_prim = None
if target:
    scene_prim = stage.GetPrimAtPath(target)
    if not scene_prim or not scene_prim.IsValid():
        scene_prim = None
        result['warning'] = f'scene_path {{target!r}} not found, falling back to first PhysicsScene on stage'
if scene_prim is None:
    for p in stage.Traverse():
        if p.IsA(UsdPhysics.Scene):
            scene_prim = p
            break
if scene_prim is None:
    result['error'] = 'no UsdPhysics.Scene found on stage'
else:
    result['scene_path'] = str(scene_prim.GetPath())
    scene = UsdPhysics.Scene(scene_prim)
    # Always report gravity — `.Get()` returns the USD-schema default
    # (direction (0,0,-1), magnitude 9.81) when not explicitly authored.
    # Without this fallback the agent sees missing keys and has historically
    # fabricated "nan / -inf" claims (see CW-49 run-2 verdict).
    g_dir_attr = scene.GetGravityDirectionAttr()
    g_mag_attr = scene.GetGravityMagnitudeAttr()
    if g_dir_attr:
        d = g_dir_attr.Get()
        if d is not None:
            result['gravity_direction'] = [float(d[0]), float(d[1]), float(d[2])]
            result['gravity_direction_authored'] = bool(g_dir_attr.HasAuthoredValue())
    if g_mag_attr:
        m = g_mag_attr.Get()
        if m is not None:
            result['gravity_magnitude'] = float(m)
            result['gravity_magnitude_authored'] = bool(g_mag_attr.HasAuthoredValue())
    try:
        from pxr import PhysxSchema
        if scene_prim.HasAPI(PhysxSchema.PhysxSceneAPI):
            phx = PhysxSchema.PhysxSceneAPI(scene_prim)
            if phx.GetSolverTypeAttr() and phx.GetSolverTypeAttr().HasAuthoredValue():
                result['solver_type'] = str(phx.GetSolverTypeAttr().Get())
            if phx.GetMinPositionIterationCountAttr() and phx.GetMinPositionIterationCountAttr().HasAuthoredValue():
                result['min_position_iterations'] = int(phx.GetMinPositionIterationCountAttr().Get())
            if phx.GetMaxPositionIterationCountAttr() and phx.GetMaxPositionIterationCountAttr().HasAuthoredValue():
                result['max_position_iterations'] = int(phx.GetMaxPositionIterationCountAttr().Get())
            if phx.GetMinVelocityIterationCountAttr() and phx.GetMinVelocityIterationCountAttr().HasAuthoredValue():
                result['min_velocity_iterations'] = int(phx.GetMinVelocityIterationCountAttr().Get())
            if phx.GetMaxVelocityIterationCountAttr() and phx.GetMaxVelocityIterationCountAttr().HasAuthoredValue():
                result['max_velocity_iterations'] = int(phx.GetMaxVelocityIterationCountAttr().Get())
            if phx.GetEnableGPUDynamicsAttr() and phx.GetEnableGPUDynamicsAttr().HasAuthoredValue():
                result['enable_gpu_dynamics'] = bool(phx.GetEnableGPUDynamicsAttr().Get())
            if phx.GetBroadphaseTypeAttr() and phx.GetBroadphaseTypeAttr().HasAuthoredValue():
                result['broadphase_type'] = str(phx.GetBroadphaseTypeAttr().Get())
            if phx.GetTimeStepsPerSecondAttr() and phx.GetTimeStepsPerSecondAttr().HasAuthoredValue():
                result['time_steps_per_second'] = int(phx.GetTimeStepsPerSecondAttr().Get())
                result['time_step'] = 1.0 / float(phx.GetTimeStepsPerSecondAttr().Get())
    except Exception as exc:
        result['physx_scene_api_error'] = str(exc)
    try:
        import carb.settings
        s = carb.settings.get_settings()
        tps = s.get('/persistent/physics/timeStepsPerSecond')
        if tps:
            result.setdefault('time_steps_per_second', int(tps))
            result.setdefault('time_step', 1.0 / float(tps))
    except Exception:
        pass
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, "get_physics_scene_config")


async def _handle_get_kinematic_state(args: Dict) -> Dict:
    """Return full kinematic state: pose + linear/angular velocity + acceleration estimate."""
    from .. import kit_tools
    prim_path = args["prim_path"]
    sample_dt = float(args.get("sample_dt", 0.05))
    code = f"""\
import omni.usd
import json
import time
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
sample_dt = {sample_dt}
result = {{'prim_path': prim_path}}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    # World transform via UsdGeom.Xformable.
    try:
        xf = UsdGeom.Xformable(prim)
        local_to_world = xf.ComputeLocalToWorldTransform(0)
        pos = local_to_world.ExtractTranslation()
        rot_q = local_to_world.ExtractRotationQuat()
        result['position'] = [float(pos[0]), float(pos[1]), float(pos[2])]
        imag = rot_q.GetImaginary()
        result['orientation_quat'] = [float(rot_q.GetReal()),
                                      float(imag[0]), float(imag[1]), float(imag[2])]
    except Exception as exc:
        result['transform_error'] = str(exc)

    has_rb = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    result['has_rigid_body_api'] = bool(has_rb)
    if has_rb:
        rb = UsdPhysics.RigidBodyAPI(prim)
        v_attr = rb.GetVelocityAttr()
        w_attr = rb.GetAngularVelocityAttr()
        v0 = v_attr.Get() if v_attr else None
        w0 = w_attr.Get() if w_attr else None
        if v0 is None:
            v0 = (0.0, 0.0, 0.0)
        if w0 is None:
            w0 = (0.0, 0.0, 0.0)
        result['linear_velocity'] = [float(v0[0]), float(v0[1]), float(v0[2])]
        result['angular_velocity'] = [float(w0[0]), float(w0[1]), float(w0[2])]
        # Best-effort acceleration via finite diff over sample_dt seconds.
        try:
            time.sleep(max(0.0, sample_dt))
            v1 = v_attr.Get() if v_attr else None
            w1 = w_attr.Get() if w_attr else None
            if v1 is None:
                v1 = (0.0, 0.0, 0.0)
            if w1 is None:
                w1 = (0.0, 0.0, 0.0)
            dt = max(sample_dt, 1e-6)
            result['linear_acceleration'] = [
                (float(v1[0]) - float(v0[0])) / dt,
                (float(v1[1]) - float(v0[1])) / dt,
                (float(v1[2]) - float(v0[2])) / dt,
            ]
            result['angular_acceleration'] = [
                (float(w1[0]) - float(w0[0])) / dt,
                (float(w1[1]) - float(w0[1])) / dt,
                (float(w1[2]) - float(w0[2])) / dt,
            ]
            result['acceleration_dt'] = dt
        except Exception as exc:
            result['acceleration_error'] = str(exc)
    else:
        result['linear_velocity'] = [0.0, 0.0, 0.0]
        result['angular_velocity'] = [0.0, 0.0, 0.0]
        result['note'] = 'no PhysicsRigidBodyAPI — velocity/acceleration unavailable'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_kinematic_state {prim_path}")


async def _handle_get_joint_positions(args: Dict) -> Dict:
    """Return current position of every joint in an articulation."""
    from .. import kit_tools
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'deg', 'prismatic': 'm'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # Prefer PhysxJointStateAPI live state, fall back to authored target
        state_attr = p.GetAttribute('state:angular:physics:position') if rj else p.GetAttribute('state:linear:physics:position')
        if not (state_attr and state_attr.IsDefined()):
            state_attr = p.GetAttribute('physics:position')
        target_attr = p.GetAttribute('drive:angular:physics:targetPosition') if rj else p.GetAttribute('drive:linear:physics:targetPosition')
        pos = None
        source = None
        if state_attr and state_attr.HasAuthoredValue():
            pos = float(state_attr.Get())
            source = 'state'
        elif target_attr and target_attr.HasAuthoredValue():
            pos = float(target_attr.Get())
            source = 'drive_target'
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'position': pos,
            'source': source,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['positions'] = [j['position'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_positions {articulation}")


async def _handle_get_joint_velocities(args: Dict) -> Dict:
    """Return current velocity of every joint in an articulation."""
    from .. import kit_tools
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'deg/s', 'prismatic': 'm/s'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # PhysxJointStateAPI velocity attribute
        vel_attr = p.GetAttribute('state:angular:physics:velocity') if rj else p.GetAttribute('state:linear:physics:velocity')
        if not (vel_attr and vel_attr.IsDefined()):
            vel_attr = p.GetAttribute('physics:velocity')
        vel = float(vel_attr.Get()) if (vel_attr and vel_attr.HasAuthoredValue()) else 0.0
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'velocity': vel,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['velocities'] = [j['velocity'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_velocities {articulation}")


async def _handle_get_joint_torques(args: Dict) -> Dict:
    """Return most recently applied torque/force on every joint."""
    from .. import kit_tools
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'N*m', 'prismatic': 'N'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # PhysxJointStateAPI: appliedJointTorque (revolute) / appliedJointForce (prismatic)
        torque_attr = (
            p.GetAttribute('state:angular:physics:appliedJointTorque') if rj
            else p.GetAttribute('state:linear:physics:appliedJointForce')
        )
        if not (torque_attr and torque_attr.IsDefined()):
            torque_attr = p.GetAttribute('physics:appliedTorque')
        torque = float(torque_attr.Get()) if (torque_attr and torque_attr.HasAuthoredValue()) else 0.0
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'torque': torque,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['torques'] = [j['torque'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_torques {articulation}")


async def _handle_get_drive_gains(args: Dict) -> Dict:
    """Read current kp/kd from UsdPhysics.DriveAPI on a joint."""
    from .. import kit_tools
    joint_path = args["joint_path"]
    drive_type = args.get("drive_type", "auto")
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint = stage.GetPrimAtPath({joint_path!r})
requested = {drive_type!r}
result = {{'joint_path': {joint_path!r}, 'requested_drive_type': requested}}
if not joint or not joint.IsValid():
    result['error'] = 'joint not found'
else:
    candidates = ['angular', 'linear'] if requested == 'auto' else [requested]
    drives = {{}}
    for token in candidates:
        drive = UsdPhysics.DriveAPI(joint, token)
        if not drive or not drive.GetPrim().HasAPI(UsdPhysics.DriveAPI):
            continue
        kp_attr = drive.GetStiffnessAttr()
        kd_attr = drive.GetDampingAttr()
        max_force_attr = drive.GetMaxForceAttr()
        target_pos_attr = drive.GetTargetPositionAttr()
        target_vel_attr = drive.GetTargetVelocityAttr()
        drives[token] = {{
            'kp': float(kp_attr.Get()) if (kp_attr and kp_attr.HasAuthoredValue()) else None,
            'kd': float(kd_attr.Get()) if (kd_attr and kd_attr.HasAuthoredValue()) else None,
            'max_force': float(max_force_attr.Get()) if (max_force_attr and max_force_attr.HasAuthoredValue()) else None,
            'target_position': float(target_pos_attr.Get()) if (target_pos_attr and target_pos_attr.HasAuthoredValue()) else None,
            'target_velocity': float(target_vel_attr.Get()) if (target_vel_attr and target_vel_attr.HasAuthoredValue()) else None,
        }}
    if not drives:
        result['error'] = 'no DriveAPI applied on this joint'
        result['has_drive_api'] = False
    else:
        result['drives'] = drives
        result['has_drive_api'] = True
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_drive_gains {joint_path}")


async def _handle_get_articulation_mass(args: Dict) -> Dict:
    """Sum mass of every link in the articulation."""
    from .. import kit_tools
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': 'kg'}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    links = []
    total = 0.0
    for p in Usd.PrimRange(art):
        if not p.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        m = 0.0
        authored = False
        if p.HasAPI(UsdPhysics.MassAPI):
            mass_attr = UsdPhysics.MassAPI(p).GetMassAttr()
            if mass_attr and mass_attr.HasAuthoredValue():
                m = float(mass_attr.Get())
                authored = True
        links.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'mass': m,
            'authored': authored,
        }})
        total += m
    result['link_count'] = len(links)
    result['total_mass'] = total
    result['links'] = links
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_articulation_mass {articulation}")


async def _handle_get_center_of_mass(args: Dict) -> Dict:
    """Compute world-space mass-weighted center of mass of an articulation."""
    from .. import kit_tools
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': 'm'}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    sum_x = sum_y = sum_z = 0.0
    total_mass = 0.0
    link_breakdown = []
    for p in Usd.PrimRange(art):
        if not p.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        m = 0.0
        local_com = Gf.Vec3f(0.0, 0.0, 0.0)
        if p.HasAPI(UsdPhysics.MassAPI):
            mass_api = UsdPhysics.MassAPI(p)
            mass_attr = mass_api.GetMassAttr()
            if mass_attr and mass_attr.HasAuthoredValue():
                m = float(mass_attr.Get())
            com_attr = mass_api.GetCenterOfMassAttr()
            if com_attr and com_attr.HasAuthoredValue():
                v = com_attr.Get()
                local_com = Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))
        # Skip zero-mass links (PhysX auto-mass not yet computed)
        if m <= 0.0:
            continue
        xf = UsdGeom.Xformable(p)
        if not xf:
            continue
        mat = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world_com = mat.Transform(Gf.Vec3d(local_com[0], local_com[1], local_com[2]))
        sum_x += m * world_com[0]
        sum_y += m * world_com[1]
        sum_z += m * world_com[2]
        total_mass += m
        link_breakdown.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'mass': m,
            'world_com': [world_com[0], world_com[1], world_com[2]],
        }})
    if total_mass <= 0.0:
        result['error'] = 'no mass-bearing links found (apply MassAPI to set mass)'
        result['total_mass'] = 0.0
        result['center_of_mass'] = None
    else:
        result['total_mass'] = total_mass
        result['center_of_mass'] = [sum_x / total_mass, sum_y / total_mass, sum_z / total_mass]
        result['link_breakdown'] = link_breakdown
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_center_of_mass {articulation}")


# ---------------------------------------------------------------------------
# Phase 7 wave 16 — final data-handler stragglers (COMPLETES data-handler migration)


async def _handle_lookup_material(args: Dict) -> Dict:
    """Look up physics material properties for a material pair."""
    from ..tool_executor import _load_physics_materials, _normalize_material_name
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


async def _handle_suggest_physics_settings(args: Dict) -> Dict:
    """Return recommended physics settings for the given scene type."""
    from ..tool_executor import _PHYSICS_SETTINGS_PRESETS
    scene_type = args.get("scene_type", "manipulation")
    preset = _PHYSICS_SETTINGS_PRESETS.get(scene_type)
    if preset is None:
        return {
            "error": f"Unknown scene type '{scene_type}'. Valid types: {', '.join(_PHYSICS_SETTINGS_PRESETS.keys())}",
            "valid_types": list(_PHYSICS_SETTINGS_PRESETS.keys()),
        }
    return {"type": "data", "settings": preset}


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
