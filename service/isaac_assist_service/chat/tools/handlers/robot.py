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
# Phase 6 wave 3 — gripper + wheeled robot + navigation + conveyors + bin + robot_description


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
    """Make a belt prim act as a moving conveyor via PhysX surface-velocity.

    Rewritten 2026-04-19 after 7 scenario runs where the old OmniGraph-based
    path failed with 'incompatible function arguments' — the generator
    passed GraphBackingType where GraphPipelineStage was required. New
    approach applies the 3-API combo from the conveyor_surface_velocity
    cite: CollisionAPI + kinematic RigidBodyAPI + PhysxSurfaceVelocityAPI.
    Deterministic, no OmniGraph, matches NVIDIA 5.x recommendation.

    2026-05-06: was hard-failing when prim_path didn't exist. Auto-creates
    a Cube geometry now when missing, using `size` + `position`. Also
    accepts `surface_velocity` as a vector alternative to scalar
    `speed` + `direction`. The CP-01 canonical template (and any agent
    treating this as a single-shot 'make a working conveyor') passes
    size+position+surface_velocity — that contract is now honored.
    """
    prim_path = args["prim_path"]

    # New-style vector or legacy scalar+direction
    surface_velocity = args.get("surface_velocity")
    if surface_velocity is not None:
        velocity_vec = list(surface_velocity)
    else:
        speed = args.get("speed", 0.5)
        direction = args.get("direction", [1, 0, 0])
        velocity_vec = [direction[0] * speed, direction[1] * speed, direction[2] * speed]

    # Geometry hints — used only if prim_path doesn't exist yet
    position = args.get("position", [0.0, 0.0, 0.0])
    size = args.get("size", [1.0, 0.3, 0.05])

    return f"""\
import omni.usd
from pxr import UsdGeom, UsdPhysics, PhysxSchema, Sdf, Gf

prim_path = '{prim_path}'
velocity_vec = {velocity_vec}
geom_position = {position}
geom_size = {size}

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    # Auto-create Cube geometry sized via `size`, placed via `position`.
    # USD Cube has unit edges → scale by size/2 to get half-extents.
    cube = UsdGeom.Cube.Define(stage, prim_path)
    cube.CreateSizeAttr(1.0)
    xf = UsdGeom.Xformable(cube)
    _t = None; _s = None
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate: _t = op
        elif op.GetOpType() == UsdGeom.XformOp.TypeScale: _s = op
    if _t is None: _t = xf.AddTranslateOp()
    _t.Set(Gf.Vec3d(*geom_position))
    if _s is None: _s = xf.AddScaleOp()
    # USD Cube has unit edge (extent ±0.5 with size=1) → scale = desired
    # edge length per axis. Earlier I had *0.5 which halved the belt.
    _s.Set(Gf.Vec3f(geom_size[0], geom_size[1], geom_size[2]))
    prim = cube.GetPrim()

# 1. CollisionAPI — so dynamic bodies can collide with the belt
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)

# 2. RigidBodyAPI with kinematicEnabled=True — REQUIRED for PhysX to
#    integrate surface-velocity. A plain collider is ignored by the
#    surface-velocity integrator; this is the #1 cause of "belt is
#    configured but cubes just sit on it".
if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    rb = UsdPhysics.RigidBodyAPI.Apply(prim)
else:
    rb = UsdPhysics.RigidBodyAPI(prim)
kin_attr = prim.GetAttribute("physics:kinematicEnabled")
if not kin_attr or not kin_attr.IsDefined():
    kin_attr = rb.CreateKinematicEnabledAttr()
kin_attr.Set(True)

# 3. PhysxSurfaceVelocityAPI — sets the per-frame velocity that's
#    applied to colliding bodies. Local-space by default.
if not prim.HasAPI(PhysxSchema.PhysxSurfaceVelocityAPI):
    PhysxSchema.PhysxSurfaceVelocityAPI.Apply(prim)
sv = Gf.Vec3f(velocity_vec[0], velocity_vec[1], velocity_vec[2])
sv_attr = prim.GetAttribute("physxSurfaceVelocity:surfaceVelocity")
if not sv_attr or not sv_attr.IsDefined():
    sv_attr = prim.CreateAttribute("physxSurfaceVelocity:surfaceVelocity",
                                    Sdf.ValueTypeNames.Vector3f)
sv_attr.Set(sv)
en_attr = prim.GetAttribute("physxSurfaceVelocity:surfaceVelocityEnabled")
if not en_attr or not en_attr.IsDefined():
    en_attr = prim.CreateAttribute("physxSurfaceVelocity:surfaceVelocityEnabled",
                                    Sdf.ValueTypeNames.Bool)
en_attr.Set(True)
ls_attr = prim.GetAttribute("physxSurfaceVelocity:surfaceVelocityLocalSpace")
if not ls_attr or not ls_attr.IsDefined():
    ls_attr = prim.CreateAttribute("physxSurfaceVelocity:surfaceVelocityLocalSpace",
                                    Sdf.ValueTypeNames.Bool)
ls_attr.Set(True)

import json
print(json.dumps({{
    "ok": True,
    "prim_path": prim_path,
    "surface_velocity": [float(sv[0]), float(sv[1]), float(sv[2])],
    "kinematic": True,
    "note": "3-API combo applied (Collision + kinematic RigidBody + SurfaceVelocity). Start sim (Play) — objects on the belt will be carried in the direction vector.",
}}))
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


def _gen_create_bin(args: Dict) -> str:
    """Build an open-top container from 5 thin Cubes (floor + 4 walls).

    Added 2026-04-19 after the conveyor_pick_place scenario showed agents
    following the open_top_bin cite's STRUCTURE (5 children with
    CollisionAPI) but improvising internally-inconsistent DIMENSIONS —
    floor overhanging walls, walls offset below floor, etc. A dedicated
    tool eliminates that class of error by computing all offsets from
    the same size argument.

    All 5 child Cubes get UsdPhysics.CollisionAPI so dropped objects
    collide and come to rest. Parent Xform gets no physics API and
    carries the world transform. Wall thickness defaults to 0.01m
    (PhysX contact-detection minimum at normal velocities).
    """
    prim_path = args["prim_path"]
    size = args.get("size", [0.3, 0.3, 0.15])
    position = args.get("position", [0.0, 0.0, 0.0])
    wall_thickness = args.get("wall_thickness", 0.01)

    return f"""\
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf

prim_path = '{prim_path}'
w, d, h = {size[0]}, {size[1]}, {size[2]}
px, py, pz = {position[0]}, {position[1]}, {position[2]}
t = {wall_thickness}

stage = omni.usd.get_context().get_stage()

# Parent Xform carries the world transform. Children use local coords
# computed from (w, d, h) so they stay consistent regardless of how the
# parent is later moved.
parent_prim = stage.GetPrimAtPath(prim_path)
if not parent_prim or not parent_prim.IsValid():
    parent_prim = stage.DefinePrim(prim_path, 'Xform')

xf = UsdGeom.Xformable(parent_prim)
# Reuse existing translate op if present (avoids op-stack duplication)
translate_op = None
for op in xf.GetOrderedXformOps():
    if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
        translate_op = op
        break
if translate_op is None:
    translate_op = xf.AddTranslateOp()
translate_op.Set(Gf.Vec3d(float(px), float(py), float(pz)))

def _define_cube(child_name, scale, local_translate):
    cube_path = f"{{prim_path}}/{{child_name}}"
    cube_prim = stage.GetPrimAtPath(cube_path)
    if not cube_prim or not cube_prim.IsValid():
        cube_prim = UsdGeom.Cube.Define(stage, cube_path).GetPrim()
    cube = UsdGeom.Cube(cube_prim)
    # UsdGeom.Cube defaults to size=2 (−1..1 extent). Use scale to set true dimensions.
    cube.GetSizeAttr().Set(2.0)
    cube_xf = UsdGeom.Xformable(cube_prim)
    # Clear existing ops, set scale+translate in consistent order
    cube_xf.ClearXformOpOrder()
    ts_op = cube_xf.AddTranslateOp()
    ts_op.Set(Gf.Vec3d(*local_translate))
    sc_op = cube_xf.AddScaleOp()
    sc_op.Set(Gf.Vec3f(scale[0]/2.0, scale[1]/2.0, scale[2]/2.0))
    if not cube_prim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(cube_prim)
    return cube_path

# Floor: covers w × d footprint, thickness = t, sits at z=0 (bottom of bin)
floor = _define_cube("Floor", (w, d, t), (0.0, 0.0, t/2.0))

# Wall centers: walls sit on top of floor (z from t to h), thickness t
wall_mid_z = t + (h - t) / 2.0
wall_inner_h = h - t

# Two walls along X-axis (short walls, full width in Y) at ±(w/2 − t/2)
wall_x1 = _define_cube("WallX1", (t, d, wall_inner_h), (-(w - t)/2.0, 0.0, wall_mid_z))
wall_x2 = _define_cube("WallX2", (t, d, wall_inner_h), ( (w - t)/2.0, 0.0, wall_mid_z))

# Two walls along Y-axis (long walls, between the X walls) at ±(d/2 − t/2)
# Length is (w − 2t) so they don't overlap the X-walls.
wall_y1 = _define_cube("WallY1", (w - 2*t, t, wall_inner_h), (0.0, -(d - t)/2.0, wall_mid_z))
wall_y2 = _define_cube("WallY2", (w - 2*t, t, wall_inner_h), (0.0,  (d - t)/2.0, wall_mid_z))

import json
print(json.dumps({{
    "ok": True,
    "prim_path": prim_path,
    "children": [floor, wall_x1, wall_x2, wall_y1, wall_y2],
    "interior_wxdxh": [round(w - 2*t, 4), round(d - 2*t, 4), round(h - t, 4)],
    "world_position": [px, py, pz],
    "note": "Open-top container with 5 collision-enabled Cubes. Interior volume is (w-2t) × (d-2t) × (h-t). Drop objects from above z=position[2]+h.",
}}))
"""


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
            if child.IsA(UsdPhysics.RevoluteJoint):
                joints.append({{
                    "name": child.GetName(),
                    "type": "revolute",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})
            elif child.IsA(UsdPhysics.PrismaticJoint):
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


# ---------------------------------------------------------------------------
# Phase 6 wave 12 — motion planning + IK + grasp + waypoint recording


def _gen_move_to_pose(args: Dict) -> str:
    from ..tool_executor import _MOTION_ROBOT_CONFIGS  # noqa: PLC0415
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
            "if trajectory is None:",
            "    raise RuntimeError(",
            "        'move_to_pose (lula_rrt): planner returned None — '",
            "        'no path to the target pose. Common causes: target unreachable, '",
            "        'IK singularity, robot_type mismatch, or obstacles in the way.'",
            "    )",
            "print(f'Lula RRT: planned trajectory with {{len(trajectory)}} waypoints')",
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
        "if trajectory is None:",
        "    raise RuntimeError(",
        "        'plan_trajectory: LulaTaskSpaceTrajectoryGenerator returned None — '",
        "        'the planner could not connect the requested waypoints. Common causes: '",
        "        'IK singularity near a waypoint, unreachable target pose, or robot model/robot_type mismatch.'",
        "    )",
        f"print(f'Planned trajectory through {len(waypoints)} waypoints')",
    ]
    return "\n".join(lines)


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

    return (
        "raise ValueError("
        + repr(
            f"set_motion_policy: unknown policy_type {policy_type!r}. "
            f"Valid: add_obstacle, remove_obstacle, set_joint_limits."
        )
        + ")"
    )


def _gen_solve_ik(args: Dict) -> str:
    from ..tool_executor import _MOTION_ROBOT_CONFIGS, _CUROBO_ROBOT_YML_MAP  # noqa: PLC0415
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    robot_type = args.get("robot_type", "franka").lower()

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee_frame = cfg["ee_frame"]
    curobo_yml = _CUROBO_ROBOT_YML_MAP.get(robot_type, "franka.yml")

    lines = [
        "import numpy as np",
        "import json",
        "",
        "# Try cuRobo IK first — it works without isaacsim Articulation init",
        "# (just needs URDF + bundled YAML), so it succeeds on placeholder",
        "# USD-only articulations where Lula's ArticulationKinematicsSolver",
        "# fails on art.initialize(). Falls through to Lula for robots cuRobo",
        "# doesn't ship configs for (or when CUDA/GPU unavailable).",
        f"target_position = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_orientation = np.array({list(target_ori)})")
    else:
        lines.append("target_orientation = None")

    lines.extend([
        "",
        "_ik_via = None",
        "_ik_solution = None",
        "_ik_errors = []",
        "",
        "# ── Path 1: cuRobo (GPU/CPU, no Kit Articulation needed) ──",
        "try:",
        "    import torch",
        "    from curobo.types.base import TensorDeviceType",
        "    from curobo.wrap.reacher.ik_solver import IKSolver, IKSolverConfig",
        "    from curobo.types.math import Pose",
        f"    _curobo_yml = '{curobo_yml}'",
        "    _tensor_args = TensorDeviceType()",
        "    _ik_cfg = IKSolverConfig.load_from_robot_config(",
        "        _curobo_yml,",
        "        None,  # no world obstacles",
        "        rotation_threshold=0.05,",
        "        position_threshold=0.005,",
        "        num_seeds=20,",
        "        self_collision_check=False,",
        "        tensor_args=_tensor_args,",
        "    )",
        "    _ik_solver = IKSolver(_ik_cfg)",
        "    _qx, _qy, _qz, _qw = 0.0, 0.0, 0.0, 1.0",
        "    if target_orientation is not None and len(target_orientation) >= 4:",
        "        # Accept (qw, qx, qy, qz) input order; cuRobo wants (qw, qx, qy, qz)",
        "        _qw, _qx, _qy, _qz = (float(x) for x in target_orientation[:4])",
        "    _pose = Pose.from_list(",
        "        [float(target_position[0]), float(target_position[1]), float(target_position[2]),",
        "         _qw, _qx, _qy, _qz]",
        "    )",
        "    _result = _ik_solver.solve_single(_pose)",
        "    if bool(_result.success.item()):",
        "        _ik_solution = _result.solution[_result.success].cpu().numpy().tolist()",
        "        _ik_via = 'curobo'",
        "    else:",
        "        _ik_errors.append('curobo: target unreachable or no IK solution found')",
        "except Exception as _ce:",
        "    _ik_errors.append(f'curobo: {type(_ce).__name__}: {_ce}')",
        "",
        "# ── Path 2: Lula via isaacsim (legacy fallback) ──",
        "if _ik_solution is None:",
        "    try:",
        "        from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver",
        "        from isaacsim.robot_motion.motion_generation import ArticulationKinematicsSolver",
        "        from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "        from isaacsim.core.prims import SingleArticulation",
        "",
        f"        kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{robot_type}')",
        "        if kin_config is None:",
        f"            for _alt in ['{robot_type}', '{robot_type}'.capitalize(), '{robot_type}_panda', 'Franka']:",
        "                kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config(_alt)",
        "                if kin_config is not None:",
        "                    break",
        "        if kin_config is None:",
        "            _ik_errors.append('lula: no kinematics config registered for ' + " + repr(robot_type) + ")",
        "        else:",
        "            kin_solver = LulaKinematicsSolver(**kin_config)",
        f"            art = SingleArticulation(prim_path='{art_path}')",
        "            art.initialize()",
        f"            art_kin = ArticulationKinematicsSolver(art, kin_solver, '{ee_frame}')",
        "            action, _success = art_kin.compute_inverse_kinematics(",
        "                target_position=target_position,",
        "                target_orientation=target_orientation,",
        "            )",
        "            if _success:",
        "                _ik_solution = list(getattr(action, 'joint_positions', []) or [])",
        "                _ik_via = 'lula'",
        "                art.apply_action(action)",
        "            else:",
        "                _ik_errors.append('lula: IK failed for ' + " + repr(ee_frame) + " + ' to target_position=' + str(target_position.tolist()))",
        "    except Exception as _le:",
        "        _ik_errors.append(f'lula: {type(_le).__name__}: {_le}')",
        "",
        "if _ik_solution is None:",
        "    raise RuntimeError(",
        f"        'solve_ik: all paths failed. Tried: ' + ' | '.join(_ik_errors)",
        "    )",
        f"print(f'IK solved via {{_ik_via}} — {ee_frame} → joints={{_ik_solution}}')",
        "print(json.dumps({'method': _ik_via, 'joint_positions': _ik_solution, 'errors': _ik_errors}))",
    ])
    return "\n".join(lines)


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

if not joint_positions:
    raise RuntimeError(
        "record_waypoints: articulation at " + repr({art_path!r}) + " has no joints — "
        "nothing to record. Check the prim path points at an actual articulation root."
    )

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
