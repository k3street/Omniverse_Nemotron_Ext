"""Robot handlers — target scope: import_robot, anchor_robot,
robot_wizard, IK / move_to_pose, singularity check, drive gains,
gripper control, motion policy (RmpFlow / Lula), surface gripper.

Phase 6 wave 1 — moves the first self-contained robot code generators
out of `tool_executor.py` (anchor_robot + verify_import). Same migration
pattern as Phase 3 scene-authoring / Phase 5 physics: function bodies
live here, `tool_executor.py` re-imports the names so the existing
CODE_GEN_HANDLERS dispatch dict keeps working.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 1 — anchor_robot + verify_import


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

# Pre-check: the robot must actually exist AND have loaded children. The old
# generator blindly called HasAPI/CreateAttribute on a potentially-missing
# prim, which threw obscure Usd/PhysX errors that the agent mis-diagnosed as
# "anchor_robot is broken". The real cause is almost always "robot was never
# imported". Catch it up-front with a clear message.
if not robot_prim.IsValid():
    raise RuntimeError(
        f"anchor_robot: prim at {{robot_path!r}} does not exist. "
        f"Import the robot FIRST via robot_wizard(asset_path=...) or "
        f"add_reference / run_usd_script with AddReference(), then call "
        f"anchor_robot on the resulting prim."
    )
# HasAuthoredReferences catches the silent-404 case where DefinePrim +
# AddReference succeeded at USD level but the asset resolver failed to
# fetch the payload (common with deprecated 4.x asset URLs). Child count
# is the hard check: a real Franka has ~34 descendants, a silent 404
# gives you an empty Xform.
_desc_count = len(list(Usd.PrimRange(robot_prim))[1:])
if _desc_count < 2:
    raise RuntimeError(
        f"anchor_robot: prim at {{robot_path!r}} exists but has {{_desc_count}} "
        f"descendants — the asset reference likely failed to resolve. "
        f"Check the asset_path (deprecated /Isaac/4.2/ paths can 404 silently). "
        f"Use robot_wizard or add_reference with a current 5.x asset URL."
    )

# Step 1: Set fixedBase=True on PhysxArticulationAPI
# This tells PhysX the root link is immovable (no need to move ArticulationRootAPI)
if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)
# Use raw attribute authoring — Isaac Sim 5.x dropped the CreateFixedBaseAttr
# convenience; the attribute name physxArticulation:fixedBase is stable.
from pxr import Sdf as _Sdf
_fb_attr = robot_prim.GetAttribute('physxArticulation:fixedBase')
if not _fb_attr or not _fb_attr.IsDefined():
    _fb_attr = robot_prim.CreateAttribute('physxArticulation:fixedBase', _Sdf.ValueTypeNames.Bool)
_fb_attr.Set(True)
print("Set physxArticulation:fixedBase=True on root")

# Step 2: Delete the rootJoint if present (6-DOF free joint that lets the
# robot float). NOT all assets have one — Isaac's stock robot USDs do, but
# a bare ArticulationRootAPI-only fixture does not. Emit an explicit
# "no rootJoint" line when absent so the agent doesn't fabricate
# "rootJoint has been removed" in its reply.
root_joint_path = robot_path + '/rootJoint'
rj = stage.GetPrimAtPath(root_joint_path)
if rj.IsValid():
    stage.RemovePrim(root_joint_path)
    print(f"Deleted {{root_joint_path}} (6-DOF free joint)")
else:
    print(f"No rootJoint at {{root_joint_path}} — nothing to delete (fixedBase attribute is the sole anchor mechanism here)")
{fixed_joint_block}
print(f"Robot at {{robot_path}} is now anchored (fixedBase=True)")
print(f"ArticulationRootAPI remains on {{robot_path}} — tensor API patterns will work")
"""


def _gen_verify_import(args: Dict) -> str:
    """Generate code that audits a URDF-imported articulation for common issues."""
    art_path = args["articulation_path"]

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, PhysxSchema, Gf
import json

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath('{art_path}')
if not root.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

issues = []
all_prims = [root] + list(Usd.PrimRange(root))[1:]

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
            c.HasAPI(UsdPhysics.CollisionAPI) for c in list(Usd.PrimRange(prim))[1:]
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
    if prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.RevoluteJoint):
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


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 1 — dispatch lines in `tool_executor.py` still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
