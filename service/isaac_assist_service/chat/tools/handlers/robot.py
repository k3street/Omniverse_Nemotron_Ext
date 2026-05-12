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
# Phase 6 wave 2 — robot_wizard, tune_gains, assemble_robot


def _gen_robot_wizard(args: Dict) -> str:
    from ..tool_executor import (
        _ROBOT_WIZARD_REGISTRY,
        _ROBOT_TYPE_DEFAULTS,
        _resolve_robot_asset,
    )

    # Resolve `robot_name` against the registry BEFORE requiring asset_path.
    # This is the deterministic path: agent says robot_name="franka_panda"
    # and we fill in the verified URL + robot_type. Falls through to the
    # explicit asset_path for unknown robots / custom URDFs.
    robot_name = args.get("robot_name", "")
    registry_hit = None
    if robot_name:
        key = robot_name.lower().replace("-", "_").replace(" ", "_")
        entry = _ROBOT_WIZARD_REGISTRY.get(key)
        while isinstance(entry, str):  # alias → canonical
            entry = _ROBOT_WIZARD_REGISTRY.get(entry)
        if isinstance(entry, dict):
            registry_hit = entry
    if registry_hit:
        asset_path = _resolve_robot_asset(registry_hit)
        if not asset_path:
            return (
                f"raise RuntimeError('robot_wizard: registry entry for "
                f"{robot_name!r} has no resolvable asset — neither local "
                f"(ASSETS_ROOT_PATH + rel_path) nor cloud_url available')\n"
            )
        robot_type = args.get("robot_type") or registry_hit.get("robot_type", "manipulator")
    else:
        if not args.get("asset_path"):
            return (
                "raise ValueError("
                "'robot_wizard: either robot_name (one of "
                + ", ".join(sorted(k for k, v in _ROBOT_WIZARD_REGISTRY.items() if isinstance(v, dict)))
                + ") or asset_path (explicit URL/URDF) must be provided')\n"
            )
        asset_path = args["asset_path"]
        robot_type = args.get("robot_type", "manipulator")
    defaults = _ROBOT_TYPE_DEFAULTS.get(robot_type, _ROBOT_TYPE_DEFAULTS["manipulator"])
    # Per-robot profile overrides: if the registry entry specifies drive
    # gains, variants, home_joints, etc., use those before falling back to
    # the generic robot_type defaults. Caller args still win over profile.
    profile = registry_hit or {}
    stiffness = args.get("drive_stiffness",
                         profile.get("drive_stiffness", defaults["stiffness"]))
    damping = args.get("drive_damping",
                       profile.get("drive_damping", defaults["damping"]))
    variants = args.get("variants", profile.get("variants") or {})
    home_joints = args.get("home_joints", profile.get("home_joints"))
    import json as _json_rw
    variants_json = _json_rw.dumps(variants)
    # Generated code is Python, not JSON — must be 'None' not 'null' when
    # the profile has no home_joints (Franka is the only profile with a
    # home pose; humanoid / mobile / quadruped registry entries omit it).
    # Caught by 'name null is not defined' on h1 in test session
    # ext_d5abf2ec turn 10: asset loaded, drives applied, then the home-
    # joint block tried to read a Python-level `null` and the script
    # failed at the very end — leaving a half-set-up robot.
    home_joints_json = _json_rw.dumps(home_joints) if home_joints else "None"
    # dest_path is only used for USD-reference imports. URDF goes through
    # import_urdf which returns its own dest_path (and respects the
    # URDF's own root-link naming). Hard-coded /World/Robot before caused
    # path mismatches when the task spec expected /World/Franka.
    dest_path_arg = args.get("dest_path", "/World/Robot")

    # Accept a position arg so the agent doesn't need a separate run_usd_script
    # call to place the robot (which often fails validator's missing-import
    # check). Applied AFTER the reference resolves, via the safe-translate
    # pattern to avoid duplicate xformOps.
    position = args.get("position")
    orientation = args.get("orientation")  # quat (w,x,y,z) or euler [x,y,z]

    is_urdf = asset_path.lower().endswith(".urdf")

    # Common precheck for local filesystem paths. Matches the pattern in
    # import_robot / add_reference / add_usd_reference: URL-scheme prefixes
    # go through USD's asset resolver, everything else must exist on disk.
    #
    # Also rejects known-deprecated 4.x cloud/nucleus asset roots up-front.
    # These return HTTP 200 with an empty stage (or 404 depending on CDN
    # edge), and AddReference is non-erroring on both — you get an empty
    # Xform with no children. The agent then treats the robot as "loaded".
    # Caught 2026-04-19 on conveyor build Run 3.
    _path_check = f"""\
import os as _os
_asset = {asset_path!r}
import re as _re
if _re.search(r'/Isaac/4\\.[0-9]+', _asset):
    raise ValueError(
        f'robot_wizard: asset_path contains deprecated Isaac 4.x path segment '
        f'({{_asset!r}}). Use a 5.x path instead, e.g. '
        f'/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd on the current '
        f'asset_root. Call lookup_api_deprecation("franka panda") for the '
        f'canonical 5.x URL recipe.'
    )
if not any(_asset.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    if not _os.path.exists(_asset):
        raise FileNotFoundError(f'robot_wizard: asset not found on disk: {{_asset!r}}')
"""

    if is_urdf:
        import_block = _path_check + f"""
# Step 1: Import robot from URDF
from isaacsim.asset.importer.urdf import import_urdf, ImportConfig
cfg = ImportConfig()
cfg.convex_decomposition = False  # use convex hull
dest_path = import_urdf({asset_path!r}, cfg)
if not dest_path:
    raise RuntimeError(f'robot_wizard: import_urdf returned empty dest_path for {{_asset!r}}')
_imported_prim = stage.GetPrimAtPath(dest_path)
if not _imported_prim.IsValid():
    raise RuntimeError(f'robot_wizard: import_urdf said dest_path={{dest_path!r}} but no prim exists there')
print(f"Imported URDF → {{dest_path}}")
"""
    else:
        import_block = _path_check + f"""
# Step 1: Import robot from USD
dest_path = {dest_path_arg!r}
prim = stage.DefinePrim(dest_path, 'Xform')
prim.GetReferences().AddReference({asset_path!r})
if not prim.HasAuthoredReferences():
    raise RuntimeError(f'robot_wizard: AddReference({{_asset!r}}) completed but HasAuthoredReferences is False on {{dest_path}}')
# Verify the reference actually resolved — AddReference is lazy and will
# not error on a 404. An empty Xform (≤1 descendant) means the asset
# server rejected the URL. Deprecated /Isaac/4.2/ paths are the most
# common offender; Isaac Sim 5.x uses /Isaac/5.0/ or /Isaac/Assets/.
from pxr import Usd as _Usd
_desc = len(list(_Usd.PrimRange(prim))[1:])
if _desc < 2:
    raise RuntimeError(
        f'robot_wizard: AddReference({{_asset!r}}) left {{dest_path}} with '
        f'{{_desc}} descendants — asset URL likely failed to resolve. '
        f'Check for deprecated 4.x paths; use a 5.x asset URL.'
    )
print(f"Loaded USD asset → {{dest_path}} ({{_desc}} descendants)")
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

{import_block}
# Step 2: Apply drive defaults for {robot_type} (Kp={stiffness}, Kd={damping})
robot_prim = stage.GetPrimAtPath(dest_path)
joint_count = 0
for child in list(Usd.PrimRange(robot_prim))[1:]:
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
for child in list(Usd.PrimRange(robot_prim))[1:]:
    if child.IsA(UsdGeom.Mesh):
        if not child.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(child)
        if not child.HasAPI(PhysxSchema.PhysxCollisionAPI):
            PhysxSchema.PhysxCollisionAPI.Apply(child)
        coll_api = PhysxSchema.PhysxCollisionAPI(child)
        coll_api.CreateContactOffsetAttr(0.02)
        collision_count += 1
print(f"Applied convex-hull collision to {{collision_count}} meshes")

# Step 4 (optional): apply position. Reuse existing translate op if the
# referenced USD already authored one, otherwise add a fresh op. Prevents
# the xformOp stack from growing on repeated tool calls.
{("" if not position else f'''
from pxr import UsdGeom as _UsdGeom, Gf as _Gf
_pos = ({position[0]}, {position[1]}, {position[2]})
_xf = _UsdGeom.Xformable(robot_prim)
_tr_op = None
for _op in _xf.GetOrderedXformOps():
    if _op.GetOpType() == _UsdGeom.XformOp.TypeTranslate:
        _tr_op = _op
        break
if _tr_op is None:
    _tr_op = _xf.AddTranslateOp()
_tr_op.Set(_Gf.Vec3d(*_pos))
print(f"Positioned robot at {{_pos}}")
''')}

# Step 5 (optional): apply orientation. Accepts quat (w,x,y,z) 4-tuple
# or euler [roll,pitch,yaw] 3-tuple in radians. Reuse existing orient op
# if present; match its precision to avoid USD type mismatches.
{("" if not orientation else f'''
from pxr import UsdGeom as _UsdGeomO, Gf as _GfO
_orient_raw = {list(orientation)!r}
if len(_orient_raw) == 4:
    _quat = _GfO.Quatd(float(_orient_raw[0]),
                       _GfO.Vec3d(float(_orient_raw[1]), float(_orient_raw[2]), float(_orient_raw[3])))
elif len(_orient_raw) == 3:
    import math as _m
    _cx, _cy, _cz = [_m.cos(a/2) for a in _orient_raw]
    _sx, _sy, _sz = [_m.sin(a/2) for a in _orient_raw]
    _quat = _GfO.Quatd(_cx*_cy*_cz + _sx*_sy*_sz,
                       _GfO.Vec3d(_sx*_cy*_cz - _cx*_sy*_sz,
                                   _cx*_sy*_cz + _sx*_cy*_sz,
                                   _cx*_cy*_sz - _sx*_sy*_cz))
else:
    raise ValueError(f"orientation must be quat (4) or euler (3), got {{len(_orient_raw)}}")
_xfO = _UsdGeomO.Xformable(robot_prim)
_or_op = None
for _op in _xfO.GetOrderedXformOps():
    if _op.GetOpType() == _UsdGeomO.XformOp.TypeOrient:
        _or_op = _op
        break
if _or_op is None:
    _or_op = _xfO.AddOrientOp(precision=_UsdGeomO.XformOp.PrecisionDouble)
_or_op.Set(_quat)
print(f"Oriented robot: quat={{_quat}}")
''')}

# Step 6: apply variant selections from profile (e.g. Franka Gripper=AlternateFinger)
_variants = {variants_json}
for _vs_name, _vs_sel in _variants.items():
    _vset = robot_prim.GetVariantSets().GetVariantSet(_vs_name)
    if _vset and _vset.GetVariantSelection() != _vs_sel:
        _vset.SetVariantSelection(_vs_sel)
        print(f"Set variant {{_vs_name}}={{_vs_sel}}")

# Step 7: apply home joint config. Set drive targets to match so the
# robot holds this pose after physics starts (no snap-back from drives
# pointing at 0). Uses USD drive target attribute writes — works before
# articulation init and is honored when physics plays.
_home_joints = {home_joints_json}
if _home_joints:
    # Build ordered list of (joint_name, target_value) from robot descendants
    _set_count = 0
    for _child in list(Usd.PrimRange(robot_prim))[1:]:
        if not _child.HasAPI(UsdPhysics.DriveAPI):
            continue
        _joint_name = _child.GetName()
        _target = None
        # Map by joint name: panda_joint1..7, panda_finger_joint1..2
        if _joint_name == "panda_joint1" and len(_home_joints) >= 1: _target = _home_joints[0]
        elif _joint_name == "panda_joint2" and len(_home_joints) >= 2: _target = _home_joints[1]
        elif _joint_name == "panda_joint3" and len(_home_joints) >= 3: _target = _home_joints[2]
        elif _joint_name == "panda_joint4" and len(_home_joints) >= 4: _target = _home_joints[3]
        elif _joint_name == "panda_joint5" and len(_home_joints) >= 5: _target = _home_joints[4]
        elif _joint_name == "panda_joint6" and len(_home_joints) >= 6: _target = _home_joints[5]
        elif _joint_name == "panda_joint7" and len(_home_joints) >= 7: _target = _home_joints[6]
        elif _joint_name == "panda_finger_joint1" and len(_home_joints) >= 8: _target = _home_joints[7]
        elif _joint_name == "panda_finger_joint2" and len(_home_joints) >= 9: _target = _home_joints[8]
        if _target is None: continue
        for _dtype in ("angular", "linear"):
            _drive = UsdPhysics.DriveAPI.Get(_child, _dtype)
            if _drive:
                # Convert radians (rad stored in config) to degrees for angular
                import math as _mh
                _val = _mh.degrees(_target) if _dtype == "angular" else _target
                _drive.GetTargetPositionAttr().Set(_val)
                _set_count += 1
                break
    print(f"Set home-joint drive targets on {{_set_count}} joints")

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

    # Manual method: set gains directly via DriveAPI.
    # Live-probed 2026-04-18: old code let the DriveAPI.Get loop silently
    # fall through (if joint_prim was invalid OR had no DriveAPI,
    # `if drive:` was false on both iterations, 0 drives got set, no print
    # fired, tool reported success=True). Now validate + count explicitly.
    if joint_name:
        return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_prim = stage.GetPrimAtPath('{art_path}/{joint_name}')
if not joint_prim or not joint_prim.IsValid():
    raise RuntimeError(f'tune_gains: joint not found: {art_path}/{joint_name}')

# Set drive gains for {joint_name}
_set_count = 0
for drive_type in ['angular', 'linear']:
    drive = UsdPhysics.DriveAPI.Get(joint_prim, drive_type)
    if drive:
        drive.GetStiffnessAttr().Set({kp})
        drive.GetDampingAttr().Set({kd})
        _set_count += 1
        print(f"Set {{drive_type}} drive on {joint_name}: Kp={kp}, Kd={kd}")
if _set_count == 0:
    raise RuntimeError(
        f'tune_gains: {joint_name} has no DriveAPI (angular or linear) — '
        f'drive schema must be applied before gain tuning'
    )
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim or not robot_prim.IsValid():
    raise RuntimeError(f'tune_gains: articulation not found: {art_path}')

# Set drive gains for all joints
joint_count = 0
for child in list(Usd.PrimRange(robot_prim))[1:]:
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({kp})
                drive.GetDampingAttr().Set({kd})
                joint_count += 1
if joint_count == 0:
    raise RuntimeError(
        f'tune_gains: no DriveAPI drives found under {art_path} — '
        f'articulation has no tunable joints (apply UsdPhysics.DriveAPI first)'
    )
print(f"Set Kp={kp}, Kd={kd} on {{joint_count}} drives")
"""


def _gen_assemble_robot(args: Dict) -> str:
    base_path = args["base_path"]
    attachment_path = args["attachment_path"]
    base_mount = args["base_mount"]
    attach_mount = args["attach_mount"]

    # Live-probed 2026-04-18 against isaacsim 5.x: the old code used
    # `assembler.assemble(base_robot_path=..., attach_robot_path=...,
    # base_robot_mount_frame=..., attach_robot_mount_frame=...)` which
    # raises `TypeError: assemble() got an unexpected keyword argument
    # 'base_robot_path'`. The 5.x API is `begin_assembly()` →
    # `create_fixed_joint(...)` → `finish_assemble()`, and the argument
    # names for the fixed joint differ. Fail-fast rather than emit
    # broken code until we write the 5.x-compliant assembly flow.
    return (
        "raise NotImplementedError("
        "'assemble_robot is a pre-5.x Cortex/Assembler API call that does not match "
        "isaacsim.robot_setup.assembler.RobotAssembler.assemble() in 5.x — '"
        "'the 5.x flow is begin_assembly/create_fixed_joint/finish_assemble with "
        "different arg names. Rewrite this handler against the current API before using it.'"
        ")\n"
    )


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
