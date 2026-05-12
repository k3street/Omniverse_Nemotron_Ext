"""Diagnostics handlers — target scope: debug-draw overlays, physics
health checks, singularity check, joint-effort monitor, OmniGraph
debug, preflight checks, clearance visualization + path-clearance
check, collision-mesh visualization, force visualization, prim
highlighting.

Phase 6 wave 10 — diagnostics code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-9.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 10 — debug-draw + physics health + singularity + visualization


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
    all_prims = [scope_root] + list(Usd.PrimRange(scope_root))[1:]
"""
    else:
        scope_filter = """
# Check all prims in the stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(Usd.PrimRange(root))[1:]
"""

    return f"""\
import omni.usd
import json
from pxr import Usd, UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
{scope_filter}
# 1. Check for missing PhysicsScene prim — ALWAYS search the whole stage,
# not the articulation-scoped all_prims list. A PhysicsScene at
# /World/PhysicsScene won't appear under /World/Arm, so the scoped
# search reports "missing" even when one exists. This caused C-03's
# persistent fabrication where the agent claimed to create a
# PhysicsScene that was already seeded.
_all_stage_prims = list(Usd.PrimRange(stage.GetPseudoRoot()))
physics_scenes = [p for p in _all_stage_prims if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene']
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
    if prim.IsA(UsdPhysics.RevoluteJoint):
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
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect joint info
joint_names = []
effort_limits = []
for desc in list(Usd.PrimRange(art_prim))[1:]:
    if desc.IsA(UsdPhysics.RevoluteJoint) or desc.IsA(UsdPhysics.RevoluteJoint):
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
    all_prims = [scope_root] + list(Usd.PrimRange(scope_root))[1:]
"""
    else:
        scope_block = """\
# Scope: entire stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(Usd.PrimRange(root))[1:]
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
            mesh_children = [c for c in list(Usd.PrimRange(p))[1:] if c.IsA(UsdGeom.Mesh)]
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
from pxr import Usd, UsdGeom, UsdPhysics, UsdLux, Gf, PhysxSchema

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


def _gen_visualize_clearance(args: Dict) -> str:
    """Generate code to visualize clearance via SDF heatmap or trigger zones."""
    art_path = args["articulation_path"]
    mode = args.get("mode", "heatmap")
    target_prims = args.get("target_prims") or []
    clearance_mm = float(args.get("clearance_mm", 50.0))
    warning_mm = float(args.get("warning_mm", 100.0))
    targets_repr = repr(list(target_prims))
    stop_m = clearance_mm / 1000.0
    warn_m = warning_mm / 1000.0

    if mode == "zones":
        # Static trigger volumes (cubes scaled to warning/stop dist) around
        # each target. Trigger prims are invisible but report enter/exit.
        return f"""\
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf, Sdf

stage = omni.usd.get_context().get_stage()
target_paths = list({targets_repr})
stop_m = {stop_m}
warn_m = {warn_m}

created = []
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if not tprim.IsValid():
        print(f'[CLEARANCE-ZONES] Skipping invalid target: {{tp}}')
        continue

    # Compute world-space bounds of the target to size the trigger zones
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    world_bbox = bbox_cache.ComputeWorldBound(tprim)
    bb_range = world_bbox.ComputeAlignedRange()
    center = bb_range.GetMidpoint()
    extent = bb_range.GetSize()

    # Warning zone (outer) and Stop zone (inner) trigger volumes
    for zone_name, gap_m in (('WarningZone', warn_m), ('StopZone', stop_m)):
        zone_path = f'{{tp}}_{{zone_name}}'
        zone_prim = stage.DefinePrim(zone_path, 'Cube')
        zone_xf = UsdGeom.Xformable(zone_prim)
        zone_xf.ClearXformOpOrder()
        zone_xf.AddTranslateOp().Set(Gf.Vec3d(center[0], center[1], center[2]))
        zone_xf.AddScaleOp().Set(Gf.Vec3d(
            float(extent[0])/2 + gap_m,
            float(extent[1])/2 + gap_m,
            float(extent[2])/2 + gap_m,
        ))
        # Make invisible — the cube only matters as a collider/trigger
        UsdGeom.Imageable(zone_prim).MakeInvisible()
        UsdPhysics.CollisionAPI.Apply(zone_prim)
        PhysxSchema.PhysxTriggerAPI.Apply(zone_prim)
        created.append(zone_path)

print(f'Created {{len(created)}} trigger zones around {{len(target_paths)}} targets for {art_path}')
print(f'  stop zone offset: {{stop_m*1000:.0f}}mm   warning zone offset: {{warn_m*1000:.0f}}mm')
"""

    # Default: heatmap. Apply PhysX SDF mesh collision to each target so we
    # can query signed distance, then color robot link positions accordingly.
    return f"""\
import omni.usd
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

target_paths = list({targets_repr})
stop_m = {stop_m}
warn_m = {warn_m}

# 1) Apply SDF mesh collision to each target so SDF queries can resolve
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if not tprim.IsValid():
        continue
    if not tprim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(tprim)
    PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(tprim)

# 2) Collect world positions of every robot link with a collider
link_positions = []
for desc in Usd.PrimRange(robot_prim):
    if desc.HasAPI(UsdPhysics.CollisionAPI):
        xf = UsdGeom.Xformable(desc).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        link_positions.append(np.array(xf.ExtractTranslation()))

if not link_positions:
    raise RuntimeError('No collider links found on {art_path}')

# 3) For each link, compute min distance to any target centroid as a coarse
#    fallback when the SDF query API isn't directly exposed in this Kit build.
target_centers = []
bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if tprim.IsValid():
        wb = bbox_cache.ComputeWorldBound(tprim)
        target_centers.append(np.array(wb.ComputeAlignedRange().GetMidpoint()))

distances = []
for lp in link_positions:
    if target_centers:
        d = min(float(np.linalg.norm(lp - tc)) for tc in target_centers)
    else:
        d = float('inf')
    distances.append(d)

# 4) Color: red if < stop, yellow if < warning, green otherwise
colors = []
for d in distances:
    if d < stop_m:
        colors.append((1.0, 0.0, 0.0, 1.0))   # red
    elif d < warn_m:
        colors.append((1.0, 1.0, 0.0, 1.0))   # yellow
    else:
        colors.append((0.0, 1.0, 0.0, 1.0))   # green

draw = _debug_draw.acquire_debug_draw_interface()
draw.clear_points()
points = [(float(p[0]), float(p[1]), float(p[2])) for p in link_positions]
draw.draw_points(points, colors, [12] * len(points))

print(f'Clearance heatmap drawn for {{len(points)}} links of {art_path}')
print(f'  stop<{{stop_m*1000:.0f}}mm=red  warn<{{warn_m*1000:.0f}}mm=yellow  safe=green')
"""


def _gen_check_path_clearance(args: Dict) -> str:
    """Generate code that runs FK on every waypoint and reports min clearance."""
    art_path = args["articulation_path"]
    trajectory = args["trajectory"]
    obstacles = args.get("obstacles") or []
    clearance_mm = float(args.get("clearance_mm", 50.0))
    threshold_m = clearance_mm / 1000.0
    obstacles_repr = repr(list(obstacles))
    # Render trajectory as a Python list literal of lists
    traj_repr = "[" + ", ".join("[" + ", ".join(f"{float(v):.6f}" for v in wp) + "]" for wp in trajectory) + "]"

    return f"""\
import json
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
import omni.usd
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

trajectory = {traj_repr}
obstacle_paths = list({obstacles_repr})
threshold_m = {threshold_m}

# Resolve obstacle world-space centroids (coarse SDF fallback)
bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
obstacle_centers = []
for op in obstacle_paths:
    oprim = stage.GetPrimAtPath(op)
    if not oprim.IsValid():
        continue
    wb = bbox_cache.ComputeWorldBound(oprim)
    obstacle_centers.append((op, np.array(wb.ComputeAlignedRange().GetMidpoint())))

# Load kinematics for FK
robot_name = '{art_path}'.split('/')[-1].lower()
try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config(robot_name)
    kin = LulaKinematicsSolver(**kin_config)
    frame_names = kin.get_all_frame_names()
except Exception as e:
    print(json.dumps({{'status': 'error', 'message': f'Kinematics not available: {{e}}'}}))
    raise

violations = []
per_waypoint = []

for idx, q in enumerate(trajectory):
    q_np = np.array(q, dtype=float)
    # Compute FK at each frame to get all link world positions
    link_positions = []
    for fname in frame_names:
        try:
            pos, _ = kin.compute_forward_kinematics(fname, q_np)
            link_positions.append(np.array(pos))
        except Exception:
            continue

    # Min distance from any link to any obstacle centroid
    if obstacle_centers and link_positions:
        min_dist = min(
            float(np.linalg.norm(lp - oc))
            for lp in link_positions
            for _, oc in obstacle_centers
        )
        # Identify the closest obstacle
        closest = min(
            (
                (op, float(np.linalg.norm(lp - oc)))
                for lp in link_positions
                for op, oc in obstacle_centers
            ),
            key=lambda x: x[1],
        )
    else:
        min_dist = float('inf')
        closest = (None, float('inf'))

    waypoint_info = {{
        'waypoint_index': idx,
        'min_clearance_mm': round(min_dist * 1000, 2) if min_dist != float('inf') else None,
        'closest_obstacle': closest[0],
    }}
    per_waypoint.append(waypoint_info)

    if min_dist < threshold_m:
        violations.append({{
            **waypoint_info,
            'threshold_mm': threshold_m * 1000,
            'message': f'Waypoint {{idx}}: min clearance {{min_dist*1000:.1f}}mm < {{threshold_m*1000:.0f}}mm',
        }})

result = {{
    'status': 'violation' if violations else 'ok',
    'articulation_path': '{art_path}',
    'threshold_mm': threshold_m * 1000,
    'num_waypoints': len(trajectory),
    'num_violations': len(violations),
    'violations': violations,
    'per_waypoint': per_waypoint,
}}
print(json.dumps(result))
"""


def _gen_visualize_collision_mesh(args: Dict) -> str:
    """Toggle PhysX collision-shape debug visualization for a prim (CODE_GEN handler)."""
    prim_path = args["prim_path"]
    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics

PRIM_PATH = "{safe_path}"

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(PRIM_PATH)
if not prim or not prim.IsValid():
    raise RuntimeError(f"Prim not found: {{PRIM_PATH}}")

# ── Enable per-prim collision visualization via CollisionAPI displayColor ─
# UsdPhysics offers a CollisionGroup and the omni.physx.ui debug
# visualization mode "Collision Shapes". Enable both so the user can
# clearly see what PhysX is using for collision.
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)

# ── Enable the global Physics Debug "Collision Shapes" visualization ────
try:
    import carb.settings
    settings = carb.settings.get_settings()
    # omni.physx.ui debug visualization toggles (these are the documented paths)
    settings.set("/persistent/physics/visualizationCollisionMesh", True)
    settings.set("/physics/visualizationDisplayJoints", False)
    settings.set("/physics/visualizationSimulationOutput", True)
    print(f"Collision-shape visualization ENABLED for {{PRIM_PATH}}")
except Exception as exc:
    print(f"Failed to set carb settings: {{exc}}")

# ── Try the omni.physx.ui PhysicsDebugView API as a secondary path ──────
try:
    import omni.physx.ui as physx_ui
    # Newer Kit versions expose a debug-view manager; fall back gracefully.
    try:
        physx_ui.get_physx_debug_view().enable_debug_visualization(True)
    except Exception:
        pass
    print("omni.physx.ui debug visualization enabled")
except ImportError:
    print("omni.physx.ui not available — relying on carb.settings toggles")

# ── Highlight the prim with a wireframe display style ──────────────────
imageable = UsdGeom.Imageable(prim)
if imageable:
    try:
        imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
    except Exception:
        pass

print(f"OK: visualizing collision mesh for {{PRIM_PATH}}")
"""


def _gen_visualize_forces(args: Dict) -> str:
    """Generate code that reads applied joint torques and draws colored arrows.

    Color rules (per spec):
      green  : |torque| <= 70 % of effort limit
      yellow : 70 % < |torque| <= 90 %
      red    : |torque| > 90 %
    """
    art_path = args["articulation_path"]
    scale = float(args.get("scale", 0.01))
    update_hz = float(args.get("update_hz", 30.0))

    return f"""\
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, Gf

try:
    from isaacsim.util.debug_draw import _debug_draw  # Isaac 4.x
except ImportError:  # Isaac Sim 5.x renamed module
    from isaacsim.util.debug_draw import _debug_draw

draw = _debug_draw.acquire_debug_draw_interface()

ART_PATH = {art_path!r}
SCALE = {scale!r}
UPDATE_HZ = {update_hz!r}

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath(ART_PATH)
if not art_prim or not art_prim.IsValid():
    raise RuntimeError(f'Articulation prim not found: {{ART_PATH}}')


def _color_for(ratio):
    # ratio = |torque| / effort_limit
    if ratio <= 0.70:
        return (0.1, 1.0, 0.1, 1.0)  # green
    if ratio <= 0.90:
        return (1.0, 0.95, 0.1, 1.0)  # yellow
    return (1.0, 0.15, 0.15, 1.0)  # red


def _collect_joints(prim):
    joints = []
    for child in Usd.PrimRange(prim):
        if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
            joints.append(child)
    return joints


def _draw_once():
    draw.clear_lines()
    points_a = []
    points_b = []
    colors = []
    sizes = []

    for joint in _collect_joints(art_prim):
        # Joint world position (best-effort via Xformable)
        try:
            xf = UsdGeom.Xformable(joint).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            pos = xf.ExtractTranslation()
        except Exception:
            pos = Gf.Vec3d(0, 0, 0)

        # Drive API: applied target torque + effort limit
        drive = UsdPhysics.DriveAPI.Get(joint, 'angular') or UsdPhysics.DriveAPI.Get(joint, 'linear')
        torque = 0.0
        limit = 1.0
        if drive:
            try:
                torque = float(drive.GetTargetPositionAttr().Get() or 0.0)
            except Exception:
                torque = 0.0
            try:
                limit = float(drive.GetMaxForceAttr().Get() or 1.0)
            except Exception:
                limit = 1.0

        ratio = min(abs(torque) / max(abs(limit), 1e-6), 1.5)
        color = _color_for(ratio)
        length = max(abs(torque), 0.05) * SCALE

        start = (pos[0], pos[1], pos[2])
        end = (pos[0], pos[1], pos[2] + length)
        points_a.append(start)
        points_b.append(end)
        colors.append(color)
        sizes.append(2.0)

        # Arrowhead: two short lines making a wedge above the tip.
        head = max(length * 0.2, 0.01)
        for ox in (-head, head):
            points_a.append(end)
            points_b.append((end[0] + ox, end[1], end[2] - head))
            colors.append(color)
            sizes.append(2.0)

    if points_a:
        draw.draw_lines(points_a, points_b, colors, sizes)


_draw_once()
print(f'[visualize_forces] drew arrows for {{ART_PATH}} at scale={{SCALE}} (update={{UPDATE_HZ}} Hz)')
"""


def _gen_highlight_prim(args: Dict) -> str:
    prim_path = args["prim_path"]
    color = args.get("color", [1.0, 1.0, 0.0])
    duration = float(args.get("duration", 2.0))
    if len(color) < 3:
        color = list(color) + [0.0] * (3 - len(color))
    r, g, b = color[0], color[1], color[2]
    return f"""\
import asyncio
import omni.usd
import omni.kit.app
from pxr import UsdGeom, Gf

try:
    from isaacsim.util.debug_draw import _debug_draw
    _draw = _debug_draw.acquire_debug_draw_interface()
except Exception:
    _draw = None

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
if not prim or not prim.IsValid():
    print("highlight_prim: prim not found at '{prim_path}'")
else:
    bbox_cache = UsdGeom.BBoxCache(0, includedPurposes=[UsdGeom.Tokens.default_])
    bbox = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
    mn = bbox.GetMin()
    mx = bbox.GetMax()
    corners = [
        Gf.Vec3d(mn[0], mn[1], mn[2]),
        Gf.Vec3d(mx[0], mn[1], mn[2]),
        Gf.Vec3d(mx[0], mx[1], mn[2]),
        Gf.Vec3d(mn[0], mx[1], mn[2]),
        Gf.Vec3d(mn[0], mn[1], mx[2]),
        Gf.Vec3d(mx[0], mn[1], mx[2]),
        Gf.Vec3d(mx[0], mx[1], mx[2]),
        Gf.Vec3d(mn[0], mx[1], mx[2]),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    starts = [corners[a] for a, _ in edges]
    ends = [corners[b] for _, b in edges]
    color = ({r}, {g}, {b}, 1.0)
    colors = [color] * len(edges)
    sizes = [3] * len(edges)
    if _draw is not None:
        _draw.draw_lines(starts, ends, colors, sizes)

        async def _clear_after():
            await asyncio.sleep({duration})
            try:
                _draw.clear_lines()
            except Exception:
                pass

        asyncio.ensure_future(_clear_after())
        print(f"highlight_prim: drew {{len(edges)}} edges around '{prim_path}', clear in {duration}s")
    else:
        print("highlight_prim: omni.isaac.debug_draw unavailable — no overlay drawn")
"""


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 10 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
