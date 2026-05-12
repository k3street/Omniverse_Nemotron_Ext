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

from typing import Any, Callable, Dict, List, Optional


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
# Phase 6 wave 22 — stragglers


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


def _gen_show_workspace(args: Dict) -> str:
    """Generate code to visualize robot workspace with manipulability gradient."""
    art_path = args["articulation_path"]
    resolution = args.get("resolution", 500000)
    color_mode = args.get("color_mode", "manipulability")

    return f"""\
import omni.usd
import numpy as np
from pxr import Usd, UsdPhysics
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect revolute joint limits
joints = []
for desc in list(Usd.PrimRange(art_prim))[1:]:
    if desc.IsA(UsdPhysics.RevoluteJoint) or desc.IsA(UsdPhysics.RevoluteJoint):
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


def _gen_build_stage_index(args: Dict) -> str:
    """Emit code that walks the stage with Usd.PrimRange and prints an index."""
    prim_scope = args.get("prim_scope") or "/World"
    max_prims = int(args.get("max_prims", 50000))
    return f"""\
import json
import omni.usd
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath('{prim_scope}') or stage.GetPseudoRoot()
index = {{}}
count = 0
for prim in Usd.PrimRange(root):
    if count >= {max_prims}:
        break
    try:
        schemas = [s.GetType().typeName for s in prim.GetAppliedSchemas()]
    except Exception:
        schemas = []
    try:
        has_physics = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    except Exception:
        has_physics = False
    index[str(prim.GetPath())] = {{
        'type': prim.GetTypeName(),
        'schemas': schemas,
        'has_physics': bool(has_physics),
    }}
    count += 1

print(json.dumps({{'prim_scope': '{prim_scope}', 'prim_count': count, 'truncated': count >= {max_prims}, 'index': index}}))
"""


def _gen_enable_extension(args: Dict) -> str:
    ext_id = args["ext_id"]
    # set_extension_enabled_immediate returns False for unknown ext ids
    # (and the try/except used to swallow the signal). Post-check
    # is_extension_enabled and raise when it's still disabled.
    return f"""\
import omni.kit.app

mgr = omni.kit.app.get_app().get_extension_manager()
ext_id = {repr(ext_id)}
if mgr.is_extension_enabled(ext_id):
    print(f"enable_extension: '{{ext_id}}' already enabled")
else:
    try:
        ok = mgr.set_extension_enabled_immediate(ext_id, True)
    except Exception as e:
        raise RuntimeError(
            f"enable_extension: set_extension_enabled_immediate raised for '{{ext_id}}': {{e}}"
        )
    if not mgr.is_extension_enabled(ext_id):
        raise RuntimeError(
            f"enable_extension: '{{ext_id}}' is still disabled after set_enabled "
            f"(set_extension_enabled_immediate returned {{ok!r}}) — likely unknown extension id."
        )
    print(f"enable_extension: '{{ext_id}}' enabled")
"""


# ---------------------------------------------------------------------------
# Phase 6 wave 23 — broken-scene + determinism + clearance monitor


def _gen_create_broken_scene(args: Dict) -> str:
    """Generate code that creates a scene with a specific, diagnosable fault for teaching."""
    from .. import tool_executor as _te  # noqa: PLC0415
    _BROKEN_SCENE_FAULTS = _te._BROKEN_SCENE_FAULTS

    fault_type = args.get("fault_type", "missing_collision")
    scene_name = args.get("scene_name", "BrokenScene")

    if fault_type not in _BROKEN_SCENE_FAULTS:
        raise ValueError(f"Unknown fault_type: {fault_type}. Valid: {list(_BROKEN_SCENE_FAULTS.keys())}")

    fault = _BROKEN_SCENE_FAULTS[fault_type]
    scene_path = f"/World/{scene_name}"

    physics_scene_code = (
        ""
        if fault_type == "no_physics_scene"
        else "if not stage.GetPrimAtPath('/World/PhysicsScene'):\n    UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')"
    )

    if fault_type == "missing_collision":
        fault_code = f"""\
ground = UsdGeom.Cube.Define(stage, '{scene_path}/Ground')
ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
ground.AddScaleOp().Set(Gf.Vec3f(5, 5, 0.05))
# FAULT: NO CollisionAPI applied — the ground will not collide with anything
# UsdPhysics.CollisionAPI.Apply(ground.GetPrim())  # THIS LINE DELIBERATELY MISSING

falling = UsdGeom.Cube.Define(stage, '{scene_path}/FallingCube')
falling.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2.0))
falling.AddScaleOp().Set(Gf.Vec3f(0.1, 0.1, 0.1))
UsdPhysics.RigidBodyAPI.Apply(falling.GetPrim())
UsdPhysics.CollisionAPI.Apply(falling.GetPrim())
"""
    elif fault_type == "zero_mass":
        fault_code = f"""\
body = UsdGeom.Cube.Define(stage, '{scene_path}/ZeroMassBody')
body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
UsdPhysics.CollisionAPI.Apply(body.GetPrim())
mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
mass_api.CreateMassAttr().Set(0.0)  # FAULT: zero mass causes PhysX NaN explosion
"""
    elif fault_type == "wrong_scale":
        fault_code = f"""\
# FAULT: object scaled 100x (cm interpreted as m)
big = UsdGeom.Cube.Define(stage, '{scene_path}/HugeBox')
big.AddTranslateOp().Set(Gf.Vec3d(0, 0, 50))
big.AddScaleOp().Set(Gf.Vec3f(100, 100, 100))
UsdPhysics.RigidBodyAPI.Apply(big.GetPrim())
UsdPhysics.CollisionAPI.Apply(big.GetPrim())
"""
    elif fault_type == "inverted_joint":
        fault_code = f"""\
base = UsdGeom.Cube.Define(stage, '{scene_path}/Base')
base.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.5))
arm = UsdGeom.Cube.Define(stage, '{scene_path}/Arm')
arm.AddTranslateOp().Set(Gf.Vec3d(0.6, 0, 0.5))
joint = UsdPhysics.RevoluteJoint.Define(stage, '{scene_path}/Joint')
joint.CreateBody0Rel().SetTargets(['{scene_path}/Base'])
joint.CreateBody1Rel().SetTargets(['{scene_path}/Arm'])
joint.CreateAxisAttr().Set('Z')  # FAULT: should be Y for typical hinge
"""
    elif fault_type == "no_physics_scene":
        fault_code = f"""\
# FAULT: PhysicsScene prim deliberately not created — no physics will run
body = UsdGeom.Cube.Define(stage, '{scene_path}/Cube')
body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2))
UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
UsdPhysics.CollisionAPI.Apply(body.GetPrim())
"""
    else:  # inf_joint_limits
        fault_code = f"""\
base = UsdGeom.Cube.Define(stage, '{scene_path}/Base')
arm = UsdGeom.Cube.Define(stage, '{scene_path}/Arm')
arm.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0))
joint = UsdPhysics.RevoluteJoint.Define(stage, '{scene_path}/Joint')
joint.CreateBody0Rel().SetTargets(['{scene_path}/Base'])
joint.CreateBody1Rel().SetTargets(['{scene_path}/Arm'])
joint.CreateAxisAttr().Set('Y')
joint.CreateLowerLimitAttr().Set(float('-inf'))  # FAULT: ±inf limits
joint.CreateUpperLimitAttr().Set(float('inf'))
"""

    return f"""\
# Broken scene: {fault_type}
# What breaks: {fault['what_breaks']}
# Learning goal: {fault['learning_goal']}
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
scope = UsdGeom.Xform.Define(stage, '{scene_path}')

{physics_scene_code}

{fault_code}

print(f"Created broken scene: {scene_path}")
print(f"Fault type: {fault_type}")
print(f"What's wrong: {fault['what_breaks']}")
print(f"Learning goal: {fault['learning_goal']}")
print(f"Hint: students should diagnose this without being told the answer.")
"""


def _gen_enable_deterministic_mode(args: Dict) -> str:
    """Generate code to enable deterministic simulation mode for safety validation."""
    seed = args.get("seed", 42)
    physics_dt = args.get("physics_dt", 1.0 / 60.0)
    solver_iterations = args.get("solver_iterations", 4)
    archive_path = args.get("export_archive_path")

    archive_code = ""
    if archive_path:
        archive_code = f"""
# Export reproducibility archive
import zipfile
import json
import platform
archive = {archive_path!r}
manifest = {{
    "seed": {seed},
    "physics_dt": {physics_dt},
    "solver_iterations": {solver_iterations},
    "platform": platform.platform(),
    "python_version": platform.python_version(),
}}
try:
    import isaacsim
    manifest["isaac_sim_version"] = isaacsim.__version__
except (ImportError, AttributeError):
    pass
try:
    import omni.physx
    manifest["physx_version"] = "see omni.physx package"
except ImportError:
    pass
os.makedirs(os.path.dirname(archive) or ".", exist_ok=True)
with zipfile.ZipFile(archive, "w") as z:
    z.writestr("manifest.json", json.dumps(manifest, indent=2))
print(f"Reproducibility archive: {{archive}}")
"""

    return f"""\
# Enable deterministic simulation mode (Safety & Compliance S.5)
import os
import random
import omni.usd
from pxr import UsdPhysics, PhysxSchema

random.seed({seed})
try:
    import numpy as np
    np.random.seed({seed})
except ImportError:
    pass

# Configure physics scene for determinism
stage = omni.usd.get_context().get_stage()
physics_scene_path = "/PhysicsScene"
physics_scene = stage.GetPrimAtPath(physics_scene_path)
if not physics_scene.IsValid():
    physics_scene_path = "/World/PhysicsScene"
    physics_scene = stage.GetPrimAtPath(physics_scene_path)

if physics_scene.IsValid():
    # Apply PhysxSceneAPI for advanced settings
    physx_api = PhysxSchema.PhysxSceneAPI.Apply(physics_scene)
    # TGS solver — deterministic for identical inputs (vs PGS which has slight nondeterminism)
    physx_api.CreateSolverTypeAttr().Set("TGS")
    # Force CPU mode — GPU dynamics is NOT fully deterministic
    physx_api.CreateBroadphaseTypeAttr().Set("MBP")
    physx_api.CreateGpuFoundLostPairsCapacityAttr().Set(0)  # Disable GPU broadphase
    physx_api.CreateEnableGPUDynamicsAttr().Set(False)
    # Fixed solver iterations
    physx_api.CreateMinPositionIterationCountAttr().Set({solver_iterations})
    physx_api.CreateMaxPositionIterationCountAttr().Set({solver_iterations})

# Set fixed physics timestep
import carb.settings
settings = carb.settings.get_settings()
settings.set("/persistent/simulation/minFrameRate", int(1.0 / {physics_dt}))
settings.set("/physics/fixedTimeStep", {physics_dt})

print(f"Deterministic mode ENABLED:")
print(f"  Seed: {seed}")
print(f"  Physics dt: {physics_dt}s ({{1.0 / {physics_dt}:.0f}} Hz)")
print(f"  Solver iterations: {solver_iterations} (fixed)")
print(f"  Solver: TGS (deterministic for identical inputs)")
print(f"  GPU dynamics: DISABLED (CPU only — GPU is not fully deterministic)")
print(f"  WARNING: PhysX GPU mode is NOT deterministic. CPU+TGS is for safety validation.")
{archive_code}
"""


def _gen_set_clearance_monitor(args: Dict) -> str:
    """Generate code that arms a clearance / near-miss monitor on a robot."""
    art_path = args["articulation_path"]
    clearance_mm = float(args.get("clearance_mm", 50.0))
    warning_mm = float(args.get("warning_mm", 100.0))
    target_prims = args.get("target_prims") or []

    # Stop zone is the contactOffset — events fire when within this distance.
    # Use the larger of warning/stop for the contactOffset itself so we get
    # warning-zone events too; the callback then classifies them by separation.
    monitor_offset_mm = max(clearance_mm, warning_mm)
    stop_m = clearance_mm / 1000.0
    warn_m = warning_mm / 1000.0
    monitor_m = monitor_offset_mm / 1000.0
    targets_repr = repr(list(target_prims))

    return f"""\
import omni.usd
import omni.physx
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

stop_threshold_m = {stop_m}
warning_threshold_m = {warn_m}
monitor_offset_m = {monitor_m}
target_paths = set({targets_repr})

# 1) Walk all descendants and arm contactOffset + contact reporting on every
#    prim that already has a CollisionAPI (i.e. each robot link's collider).
link_paths = []
for desc in Usd.PrimRange(robot_prim):
    if desc.HasAPI(UsdPhysics.CollisionAPI):
        physx_col = PhysxSchema.PhysxCollisionAPI.Apply(desc)
        # contactOffset is in scene units (meters in Isaac Sim defaults)
        physx_col.CreateContactOffsetAttr().Set(monitor_offset_m)
        # contactReport API must be applied to receive on_contact events
        PhysxSchema.PhysxContactReportAPI.Apply(desc)
        link_paths.append(str(desc.GetPath()))

# 2) Arm the same APIs on each target so PhysX pairs them with the robot links
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if tprim.IsValid() and tprim.HasAPI(UsdPhysics.CollisionAPI):
        physx_col = PhysxSchema.PhysxCollisionAPI.Apply(tprim)
        physx_col.CreateContactOffsetAttr().Set(monitor_offset_m)
        PhysxSchema.PhysxContactReportAPI.Apply(tprim)

# 3) Subscribe to contact-report events. `separation > 0` means the two
#    colliders are still apart but inside the contactOffset zone.
def _on_contact_report(contact_headers, contact_data):
    for header in contact_headers:
        actor0 = str(header.actor0)
        actor1 = str(header.actor1)
        # If targets were provided, only report robot-vs-target pairs
        if target_paths and not (actor0 in target_paths or actor1 in target_paths):
            continue
        for i in range(header.contact_data_offset,
                       header.contact_data_offset + header.num_contact_data):
            sep = float(contact_data[i].separation)
            if sep <= 0:
                # Actual penetration — full collision
                print(f'[CLEARANCE] COLLISION: {{actor0}} <-> {{actor1}} (penetration={{-sep*1000:.1f}}mm)')
            elif sep < stop_threshold_m:
                print(f'[CLEARANCE] STOP: {{actor0}} within {{sep*1000:.1f}}mm of {{actor1}} (<{{stop_threshold_m*1000:.0f}}mm stop zone)')
            elif sep < warning_threshold_m:
                print(f'[CLEARANCE] WARNING: {{actor0}} within {{sep*1000:.1f}}mm of {{actor1}} (<{{warning_threshold_m*1000:.0f}}mm warning zone)')

physx_iface = omni.physx.get_physx_interface()
_clearance_sub = physx_iface.subscribe_contact_report_events(_on_contact_report)

print(f'Clearance monitor armed on {{len(link_paths)}} robot links of {art_path}')
print(f'  warning zone: <{{warning_threshold_m*1000:.0f}}mm   stop zone: <{{stop_threshold_m*1000:.0f}}mm')
print(f'  monitoring against {{len(target_paths)}} target prims')
"""



# ---------------------------------------------------------------------------
# Phase 6 wave 24 — stragglers


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


# ---------------------------------------------------------------------------
# Phase 7 wave 10 — diagnostic data-handlers (check, diagnose, get_console/active, compare_sim_real, hardware_compat)


async def _handle_check_collision_mesh(args: Dict) -> Dict:
    """Analyze a USD mesh prim's collision quality (DATA handler)."""
    from .. import kit_tools  # noqa: PLC0415
    from ..handlers.physics import _gen_check_collision_mesh_code  # noqa: PLC0415
    import json  # noqa: PLC0415
    prim_path = args.get("prim_path", "")
    if not prim_path or not prim_path.startswith("/"):
        return {"error": "prim_path must be a non-empty USD path starting with /"}
    code = _gen_check_collision_mesh_code(prim_path)
    result = await kit_tools.exec_sync(code, timeout=20)
    if not result.get("success"):
        return {
            "error": f"Kit RPC failed: {result.get('output', 'unknown')}",
            "hint": "Is Isaac Sim running with the Kit RPC bridge on port 8001?",
        }
    output = (result.get("output") or "").strip()
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"error": "Failed to parse collision-mesh response", "raw_output": output[:500]}


async def _handle_check_collisions(args: Dict) -> Dict:
    """Validate collision meshes on a prim via Kit RPC."""
    from .. import kit_tools  # noqa: PLC0415
    import json  # noqa: PLC0415
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, PhysxSchema
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
    for child in list(Usd.PrimRange(prim))[1:]:
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


async def _handle_check_teleop_hardware(args: Dict) -> Dict:
    """Look up a teleop device in the known-devices table and probe local availability."""
    from pathlib import Path  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _TELEOP_DEVICES = _te._TELEOP_DEVICES
    device = str(args.get("device", "")).lower()
    info = _TELEOP_DEVICES.get(device)
    if info is None:
        return {
            "device": device,
            "supported": False,
            "reason": f"Unknown teleop device '{device}'. Known: {sorted(_TELEOP_DEVICES.keys())}",
        }

    result: Dict[str, Any] = {
        "device": device,
        "supported": info["supported"],
        "transport": info["transport"],
        "latency_budget_ms": info["latency_budget_ms"],
        "known_limitations": list(info["known_limitations"]),
        "notes": info["notes"],
    }

    # Local probe — best-effort, never raises into the tool loop
    if info["transport"] == "usb-hid":
        try:
            dev_input = Path("/dev/input")
            result["local_probe"] = {
                "dev_input_exists": dev_input.exists(),
                "entries": len(list(dev_input.iterdir())) if dev_input.exists() else 0,
            }
        except Exception as e:  # noqa: BLE001 — probe must never raise
            result["local_probe"] = {"error": str(e)}
    else:
        # XR path — just report that the probe is out of scope for L0.
        result["local_probe"] = {
            "note": "Network / XR-runtime probe not performed; run the device's own diagnostics.",
        }

    return result


async def _handle_check_tf_health(args: Dict) -> Dict:
    """Diagnose ROS2 TF tree health by introspecting the bridge in-Kit."""
    from .. import kit_tools  # noqa: PLC0415
    expected = args.get("expected_frames") or ["base_link", "odom", "map"]
    max_age = float(args.get("max_age_seconds", 1.0))
    root_frame = args.get("root_frame", "map")

    code = f"""\
import json
import time

expected_frames = {expected!r}
max_age = {max_age}
root_frame = {root_frame!r}

report = {{
    'expected_frames': expected_frames,
    'present_frames': [],
    'missing_frames': [],
    'stale_frames': [],
    'future_extrapolation_frames': [],
    'orphan_frames': [],
    'static_transforms_ok': True,
    'errors': [],
}}

try:
    import rclpy
    from tf2_ros import Buffer, TransformListener  # noqa: F401
except ImportError as e:
    report['errors'].append(f'rclpy/tf2_ros not importable in Kit: {{e}}')
    print(json.dumps(report))
else:
    if not rclpy.ok():
        try:
            rclpy.init()
        except Exception as init_err:
            report['errors'].append(f'rclpy.init failed: {{init_err}}')
    node = rclpy.create_node('isaac_assist_tf_health')
    buf = Buffer()
    listener = TransformListener(buf, node)
    # Spin briefly to populate the buffer
    deadline = time.time() + 1.5
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
    try:
        all_frames_yaml = buf.all_frames_as_yaml() or ''
    except Exception as e:
        report['errors'].append(f'all_frames_as_yaml failed: {{e}}')
        all_frames_yaml = ''
    # Parse the very simple YAML format (frame_name:\\n  parent: ...)
    present = []
    for line in all_frames_yaml.splitlines():
        if line and not line.startswith(' ') and line.endswith(':'):
            present.append(line[:-1].strip())
    report['present_frames'] = present
    report['missing_frames'] = [f for f in expected_frames if f not in present]
    # Staleness check
    now = node.get_clock().now()
    for frame in present:
        try:
            tfs = buf.lookup_transform(root_frame, frame, rclpy.time.Time())
            stamp = tfs.header.stamp
            age = (now.nanoseconds - (stamp.sec * 1_000_000_000 + stamp.nanosec)) / 1e9
            if age > max_age:
                report['stale_frames'].append({{'frame': frame, 'age_s': age}})
            if age < -0.05:
                report['future_extrapolation_frames'].append({{'frame': frame, 'age_s': age}})
        except Exception:
            report['orphan_frames'].append(frame)
    listener.unregister() if hasattr(listener, 'unregister') else None
    node.destroy_node()
    print(json.dumps(report))
"""

    return await kit_tools.queue_exec_patch(code, "Read TF tree health for Nav2 diagnostics")


async def _handle_check_vram_headroom(args: Dict) -> Dict:
    """Estimate VRAM cost vs available, return warnings + suggestions."""
    from .. import tool_executor as _te  # noqa: PLC0415
    _VRAM_PER_ENV_MB = _te._VRAM_PER_ENV_MB
    _detect_local_vram_gb = _te._detect_local_vram_gb
    _detect_used_vram_gb = _te._detect_used_vram_gb
    operation = args.get("operation", "custom")
    num_envs = int(args.get("num_envs", 1))
    complexity = args.get("complexity", "medium")
    if complexity not in ("low", "medium", "high"):
        complexity = "medium"

    per_env_mb = args.get("per_env_mb_override")
    if per_env_mb is None:
        per_env_mb = _VRAM_PER_ENV_MB.get(operation, _VRAM_PER_ENV_MB["custom"]).get(
            complexity, 32
        )
    per_env_mb = float(per_env_mb)
    estimated_mb = per_env_mb * max(num_envs, 1)
    estimated_gb = round(estimated_mb / 1024.0, 2)

    device_vram_gb = args.get("device_vram_gb")
    if device_vram_gb is None:
        device_vram_gb = _detect_local_vram_gb()
    used_gb = args.get("currently_used_gb")
    if used_gb is None:
        used_gb = _detect_used_vram_gb()

    available_gb: Optional[float]
    if device_vram_gb is not None and used_gb is not None:
        available_gb = round(max(device_vram_gb - used_gb, 0.0), 2)
    elif device_vram_gb is not None:
        # Assume ~1 GB baseline used by the OS / Kit if we can't query.
        available_gb = round(max(device_vram_gb - 1.0, 0.0), 2)
    else:
        available_gb = None

    fits = (
        available_gb is not None
        and estimated_gb <= available_gb
    )

    suggestions: List[str] = []
    if not fits and available_gb is not None:
        # Suggest a reduced env count that fits in ~80 % of available VRAM.
        budget_mb = available_gb * 1024.0 * 0.8
        if per_env_mb > 0:
            safe_envs = max(int(budget_mb // per_env_mb), 1)
            if safe_envs < num_envs:
                suggestions.append(
                    f"Reduce to {safe_envs} environments (fits in ~{round(safe_envs * per_env_mb / 1024.0, 2)} GB)"
                )
        suggestions.append("Use headless mode to free ~2 GB")
        suggestions.append("Use cloud compute (Phase 7H IsaacAutomator)")

    warning: Optional[str] = None
    if not fits:
        if available_gb is None:
            warning = (
                f"Could not auto-detect GPU VRAM. Estimated need: {estimated_gb} GB "
                f"for {num_envs}× {operation} ({complexity})."
            )
        else:
            warning = (
                f"This will need approximately {estimated_gb} GB additional VRAM. "
                f"Available: {available_gb} GB — not enough for {num_envs} {operation}."
            )

    return {
        "operation": operation,
        "num_envs": num_envs,
        "complexity": complexity,
        "per_env_mb": per_env_mb,
        "estimated_gb": estimated_gb,
        "device_vram_gb": device_vram_gb,
        "currently_used_gb": used_gb,
        "available_gb": available_gb,
        "fits": fits,
        "warning": warning,
        "suggestions": suggestions,
    }


async def _handle_compare_sim_real_video(args: Dict) -> Dict:
    """Compare sim and real videos using vision LLM."""
    from pathlib import Path  # noqa: PLC0415
    sim_path = args.get("sim_video_path", "")
    real_path = args.get("real_video_path", "")

    if not Path(sim_path).exists() or not Path(real_path).exists():
        return {
            "error": "Video file(s) not found",
            "sim_exists": Path(sim_path).exists(),
            "real_exists": Path(real_path).exists(),
        }

    return {
        "sim_video": sim_path,
        "real_video": real_path,
        "analysis_prompt": (
            "Compare these two robot trajectories. Identify: "
            "1) Behavioral differences (overshoot, undershoot, tremor) "
            "2) Contact timing differences "
            "3) Stability/oscillation differences "
            "4) Speed/timing differences"
        ),
        "note": "Vision analysis to be performed by Gemini Vision provider",
        "next_step": "Call vision_analyze_scene with these videos as input",
    }


async def _handle_console_error_autodetect(args: Dict) -> Dict:
    """Check for new console errors since a given timestamp."""
    from .. import kit_tools  # noqa: PLC0415
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


async def _handle_diagnose_domain_gap(args: Dict) -> Dict:
    """Compare synthetic vs real image datasets to diagnose domain gap.

    Returns a FID-like comparison score, per-class distribution differences,
    and suggested DR adjustments.
    """
    from .. import kit_tools  # noqa: PLC0415
    import re as _re  # noqa: PLC0415
    synthetic_dir = args.get("synthetic_dir", "")
    real_dir = args.get("real_dir", "")
    checkpoint = args.get("model_checkpoint")

    if not synthetic_dir or not real_dir:
        return {"error": "Both synthetic_dir and real_dir are required"}

    # Sanitize paths
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


async def _handle_diagnose_performance(args: Dict) -> Dict:
    """Collect PhysX stats, timing, and GPU memory, then analyze for bottlenecks."""
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _analyze_performance = _te._analyze_performance
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


async def _handle_diagnose_physics_error(args: Dict) -> Dict:
    """Pattern-match against known PhysX errors and return diagnosis."""
    import re as _re  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _PHYSX_ERROR_PATTERNS = _te._PHYSX_ERROR_PATTERNS
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
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _ROS2_QOS_PRESETS = _te._ROS2_QOS_PRESETS
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
            import json as _json  # noqa: PLC0415
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


async def _handle_diagnose_whole_body(args: Dict) -> Dict:
    """Diagnostic checklist for humanoid balance/coordination during arm motion."""
    articulation_path = args["articulation_path"]
    margin = float(args.get("support_polygon_margin_m", 0.05))
    accel_thresh = float(args.get("ee_accel_threshold_m_s2", 5.0))

    checks = [
        {
            "id": "balance_margin",
            "name": "Balance margin during arm motion",
            "description": (
                "Compare CoM ground projection to support polygon (foot contacts). "
                f"Min margin: {margin} m. If CoM exits the polygon during reach, "
                "the locomotion policy will compensate or the robot will tip."
            ),
        },
        {
            "id": "com_projection",
            "name": "CoM projection vs support polygon",
            "description": (
                "Compute polygon from active foot contact patches, project CoM onto ground "
                "plane, measure signed distance to nearest edge. Negative = CoM outside polygon."
            ),
        },
        {
            "id": "arm_payload_effect",
            "name": "Arm payload effect on locomotion policy",
            "description": (
                "If the locomotion policy was trained with a free arm, attaching a "
                "heavy end-effector (or carrying an object) shifts the CoM and can "
                "destabilize gait. Retrain with payload domain randomization or use "
                "a payload-conditioned policy."
            ),
        },
        {
            "id": "ee_acceleration",
            "name": "EE acceleration during gait",
            "description": (
                f"High EE acceleration (> {accel_thresh} m/s^2) injects reaction "
                "forces into the torso that the locomotion policy did not see during "
                "training. Smooth the IK trajectory or add an EE-acceleration penalty."
            ),
        },
    ]

    return {
        "articulation_path": articulation_path,
        "support_polygon_margin_m": margin,
        "ee_accel_threshold_m_s2": accel_thresh,
        "checks": checks,
        "message": (
            f"Diagnose whole-body checklist for {articulation_path} "
            f"({len(checks)} items). Run each check against the live articulation "
            "to identify why the robot is falling during arm motion."
        ),
    }


async def _handle_get_active_state(args: Dict) -> Dict:
    """Return prim.IsActive() (active/deactivated state)."""
    from .. import kit_tools  # noqa: PLC0415
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
    result['is_active'] = None
else:
    try:
        result['is_active'] = bool(prim.IsActive())
    except Exception as exc:
        result['error'] = f'IsActive failed: {{exc}}'
        result['is_active'] = None
    try:
        result['is_loaded'] = bool(prim.IsLoaded())
    except Exception:
        result['is_loaded'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_active_state {prim_path}")


async def _handle_get_console_errors(args: Dict) -> Dict:
    from .. import kit_tools  # noqa: PLC0415
    ctx = await kit_tools.get_stage_context(full=False)
    logs = ctx.get("recent_logs", [])
    min_level = args.get("min_level", "warning")
    level_order = ["verbose", "info", "warning", "error", "fatal"]
    min_idx = level_order.index(min_level) if min_level in level_order else 2
    filtered = [l for l in logs if level_order.index(l.get("level", "info")) >= min_idx]
    last_n = args.get("last_n", 50)
    return {"errors": filtered[-last_n:], "total_count": len(filtered)}


async def _handle_get_debug_info(args: Dict) -> Dict:
    """Return perf metrics via Kit RPC /context fallback."""
    from .. import kit_tools  # noqa: PLC0415
    ctx = await kit_tools.get_stage_context(full=False)
    return {
        "prim_count": ctx.get("stage", {}).get("prim_count"),
        "stage_url": ctx.get("stage", {}).get("stage_url"),
        "note": "Full perf metrics require Kit-side instrumentation",
    }


async def _handle_hardware_compatibility_check(args: Dict) -> Dict:
    """Run hardware and software compatibility probe."""
    from .. import kit_tools  # noqa: PLC0415
    import os  # noqa: PLC0415
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
    import sys  # noqa: PLC0415
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
