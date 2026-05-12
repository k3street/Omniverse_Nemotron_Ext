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
