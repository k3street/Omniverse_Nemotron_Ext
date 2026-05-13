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


# _handle_diagnose_ros2 moved to handlers/ros2.py (Phase 7 wave 14).


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
# Phase 7 wave 14 — validate/verify/measure/trace/proactive stragglers


async def _handle_measure_distance(args: Dict) -> Dict:
    prim_a = args["prim_a"]
    prim_b = args["prim_b"]
    # UsdGeom.Xformable(invalid_prim).ComputeLocalToWorldTransform(0) silently
    # returns the identity matrix — distance_m = 0.0, success=True. Without
    # the IsValid gate, missing prims looked like coincident geometry. Raise
    # with the specific invalid path so the agent can report it.
    from .. import kit_tools  # noqa: PLC0415
    code = f"""\
import omni.usd
from pxr import UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
_pa = stage.GetPrimAtPath({prim_a!r})
_pb = stage.GetPrimAtPath({prim_b!r})
if not _pa or not _pa.IsValid():
    raise RuntimeError('measure_distance: prim_a not found: ' + repr({prim_a!r}))
if not _pb or not _pb.IsValid():
    raise RuntimeError('measure_distance: prim_b not found: ' + repr({prim_b!r}))
_xa = UsdGeom.Xformable(_pa)
_xb = UsdGeom.Xformable(_pb)
if not _xa or not _xb:
    raise RuntimeError('measure_distance: one or both prims are not Xformable')
xf_a = _xa.ComputeLocalToWorldTransform(0)
xf_b = _xb.ComputeLocalToWorldTransform(0)
pos_a = xf_a.ExtractTranslation()
pos_b = xf_b.ExtractTranslation()
dist = (pos_a - pos_b).GetLength()
print(json.dumps({{'prim_a': {prim_a!r}, 'prim_b': {prim_b!r}, 'distance_m': dist,
       'position_a': list(pos_a), 'position_b': list(pos_b)}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Measure distance {prim_a} ↔ {prim_b}")


async def _handle_measure_sim_real_gap(args: Dict) -> Dict:
    """Compare sim and real trajectories to quantify the gap."""
    from .. import tool_executor as _te  # noqa: PLC0415
    _load_trajectory_for_gap = _te._load_trajectory_for_gap
    sim_path = args.get("sim_trajectory", "")
    real_path = args.get("real_trajectory", "")

    sim = _load_trajectory_for_gap(sim_path)
    real = _load_trajectory_for_gap(real_path)

    if sim is None or real is None:
        missing = []
        if sim is None:
            missing.append(sim_path)
        if real is None:
            missing.append(real_path)
        return {"error": f"Trajectory file(s) not found: {missing}"}

    if (isinstance(sim, dict) and sim.get("_error")) or (isinstance(real, dict) and real.get("_error")):
        return {"error": sim.get("_error") if isinstance(sim, dict) else real.get("_error")}

    sim_joints = sim.get("joint_positions") or sim.get("joints") or sim.get("q")
    real_joints = real.get("joint_positions") or real.get("joints") or real.get("q")

    if not sim_joints or not real_joints:
        return {
            "error": "Could not find joint_positions/joints/q in trajectory files",
            "sim_keys": list(sim.keys()),
            "real_keys": list(real.keys()),
        }

    n_steps = min(len(sim_joints), len(real_joints))
    if n_steps == 0:
        return {"error": "Empty trajectories"}

    n_joints = len(sim_joints[0]) if isinstance(sim_joints[0], (list, tuple)) else 1
    joint_errors = {}
    for j in range(n_joints):
        errors = []
        for t in range(n_steps):
            s_val = sim_joints[t][j] if isinstance(sim_joints[t], (list, tuple)) else sim_joints[t]
            r_val = real_joints[t][j] if isinstance(real_joints[t], (list, tuple)) else real_joints[t]
            errors.append(abs(float(s_val) - float(r_val)))
        joint_errors[f"joint_{j}"] = {
            "mean_error_deg": round(sum(errors) / len(errors), 4),
            "max_error_deg": round(max(errors), 4),
        }

    worst_joint = max(joint_errors, key=lambda k: joint_errors[k]["mean_error_deg"])

    ee_error_mm = None
    sim_ee = sim.get("ee_pos") or sim.get("end_effector")
    real_ee = real.get("ee_pos") or real.get("end_effector")
    if sim_ee and real_ee:
        ee_errs = []
        for t in range(min(len(sim_ee), len(real_ee))):
            s, r = sim_ee[t], real_ee[t]
            d = sum((s[i] - r[i]) ** 2 for i in range(min(len(s), len(r)))) ** 0.5
            ee_errs.append(d)
        if ee_errs:
            ee_error_mm = {
                "mean_mm": round((sum(ee_errs) / len(ee_errs)) * 1000, 2),
                "max_mm": round(max(ee_errs) * 1000, 2),
            }

    recommendation = []
    worst_err = joint_errors[worst_joint]["mean_error_deg"]
    if worst_err > 5.0:
        recommendation.append(f"{worst_joint} has {worst_err:.1f}° mean error — investigate friction/damping mismatch")
    if ee_error_mm and ee_error_mm["mean_mm"] > 10:
        recommendation.append(f"EE drifts {ee_error_mm['mean_mm']:.0f}mm — likely joint compliance issue")

    return {
        "joint_errors": joint_errors,
        "worst_joint": worst_joint,
        "ee_error_mm": ee_error_mm,
        "n_steps": n_steps,
        "n_joints": n_joints,
        "recommendation": recommendation,
    }


async def _handle_proactive_check(args: Dict) -> Dict:
    """Run the proactive agent for a scene-state trigger.

    The agent calls each tool in the trigger's playbook and aggregates
    findings. Auto-fixes are gated by both the per-call `auto_fix=True`
    and the AUTO_PROACTIVE_FIX env var so tests + dry runs never mutate
    the scene without explicit opt-in.
    """
    import os  # noqa: PLC0415
    import logging  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _PROACTIVE_TRIGGER_PLAYBOOKS = _te._PROACTIVE_TRIGGER_PLAYBOOKS
    DATA_HANDLERS = _te.DATA_HANDLERS
    _logger = logging.getLogger(_te.__name__)

    trigger = args.get("trigger")
    context = args.get("context") or {}
    auto_fix_requested = bool(args.get("auto_fix", False))

    playbook = _PROACTIVE_TRIGGER_PLAYBOOKS.get(trigger)
    if playbook is None:
        return {
            "ok": False,
            "error": f"Unknown proactive trigger '{trigger}'. Supported: {sorted(_PROACTIVE_TRIGGER_PLAYBOOKS)}",
        }

    auto_fix_env = os.environ.get("AUTO_PROACTIVE_FIX", "false").lower() in ("1", "true", "yes")
    auto_fix_enabled = auto_fix_requested and auto_fix_env

    findings: List[Dict[str, Any]] = []
    for tool_name in playbook:
        handler = DATA_HANDLERS.get(tool_name)
        if handler is None:
            # Tool is LLM-handled or disabled — note it and move on.
            findings.append({
                "tool": tool_name,
                "skipped": True,
                "note": "Tool handled by LLM reasoning or unavailable; no live data captured.",
            })
            continue
        try:
            # Pass the context as kwargs where the handler accepts them; otherwise
            # just call with the raw context dict — every data handler takes a dict.
            tool_args = {}
            if tool_name == "explain_error":
                tool_args = {"error_text": context.get("error_text", "")}
            elif tool_name == "measure_distance":
                # target_placed trigger needs prim_a + prim_b
                if "target_path" in context and "robot_path" in context:
                    tool_args = {"prim_a": context["target_path"], "prim_b": context["robot_path"]}
                else:
                    findings.append({
                        "tool": tool_name,
                        "skipped": True,
                        "note": "measure_distance needs target_path + robot_path in context.",
                    })
                    continue
            result = await handler(tool_args)
            findings.append({"tool": tool_name, "result": result})
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning(f"[ProactiveAgent] {tool_name} raised: {exc}")
            findings.append({"tool": tool_name, "error": str(exc)})

    return {
        "ok": True,
        "trigger": trigger,
        "context": context,
        "playbook": playbook,
        "findings": findings,
        "auto_fix_enabled": auto_fix_enabled,
        "auto_fix_applied": [],  # populated only when AUTO_PROACTIVE_FIX is on
        "principle": "Proactive ≠ autonomous modification — observations only unless AUTO_PROACTIVE_FIX is enabled.",
    }


async def _handle_simulate_traversal_check(args: Dict) -> Dict:
    """Function-gate counterpart to verify_pickplace_pipeline's form-gate.

    Plays the timeline for `duration_s` of sim time, samples cube position
    twice (once just before stop for velocity, once at stop for final pose),
    and checks whether the cube actually arrived at target_path's bbox AND
    came to rest. Returns {success, cube_initial, cube_final, cube_velocity,
    target_bbox, in_target_xy, above_floor, at_rest, sim_duration}.

    Args:
      cube_path: prim path of the cube to track (required for single-cube mode)
      cube_paths: list of prim paths to track in MULTI-CUBE mode — success if
                  ANY of them reaches target_path's bbox. Useful for Cortex
                  behavior trees and multi-cube canonicals where simulate_
                  traversal_check shouldn't be limited to Cube_1. cube_paths
                  takes precedence over cube_path when both provided.
      target_path: prim path of the destination (its world bbox is the target)
      duration_s: sim duration in seconds (default 60)
      xy_tolerance: xy bbox tolerance in meters (default 0.0 — strict)
      floor_tolerance: z below target floor allowed in meters (default 0.10)
      rest_speed_threshold: max speed (m/s) to consider "at rest" (default 0.05)
      seed: int seed for RNGs (random/numpy/torch). Default 42.
            Run i uses seed + i to vary while staying reproducible.
      n_runs: number of repeated runs against the same built scene. Default 1.
              Captures initial cube xform + robot joint state + ctrl:* attrs
              before run 0, restores them before each subsequent run, deletes
              FJ prims created during run. Returns success_rate + per_run.
              Run-classification: stable_ok=>=4/5, flaky=1-3/5, stable_fail=0.
    """
    from .. import kit_tools  # noqa: PLC0415
    cube_paths = args.get("cube_paths") or []
    if isinstance(cube_paths, str):
        cube_paths = [cube_paths]
    cube_paths = [str(p).strip() for p in cube_paths if str(p).strip()]
    cube_path = (args.get("cube_path") or "").strip()
    target_path = (args.get("target_path") or "").strip()
    if not (cube_path or cube_paths) or not target_path:
        return {"error": "simulate_traversal_check requires cube_path or cube_paths, plus target_path"}
    # Multi-cube mode: cube_paths takes precedence. Single-cube mode: cube_path
    # is the only target. The generated code handles both via cube_paths list.
    if not cube_paths:
        cube_paths = [cube_path]
    duration_s = float(args.get("duration_s", 60))
    xy_tolerance = float(args.get("xy_tolerance", 0.0))
    floor_tolerance = float(args.get("floor_tolerance", 0.10))
    rest_speed = float(args.get("rest_speed_threshold", 0.05))
    require_upright = bool(args.get("require_upright", False))
    upright_tol = float(args.get("upright_tolerance_dot", 0.95))
    seed = int(args.get("seed", 42))
    n_runs = max(1, min(int(args.get("n_runs", 1)), 50))

    code = f"""\
import omni.usd, omni.timeline, omni.kit.app, json, time as _t
import random as _rand
from pxr import UsdGeom, Usd, Sdf, Gf

stage = omni.usd.get_context().get_stage()

# Note: UsdGeom.BBoxCache caches per-prim and never invalidates against
# physics-driven transform updates. Re-using one cache across the play
# loop returns the AUTHORED (stale) position instead of the live one.
# Fix 2026-05-07: build a fresh cache per query for the moving cube;
# only static target bbox can safely use a cached lookup.

def _world_pos(path):
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    # FRESH cache per call — physics-driven xformOps require it.
    fresh = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = fresh.ComputeWorldBound(p).ComputeAlignedRange()
    if not b.IsEmpty():
        c = b.GetMidpoint()
        return [float(c[0]), float(c[1]), float(c[2])]
    try:
        xf = UsdGeom.Xformable(p)
        t = xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
        return [float(t[0]), float(t[1]), float(t[2])]
    except Exception:
        return None

# Static target bbox is computed ONCE before play — no cache reuse hazard.
def _world_bbox(path):
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    fresh = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    b = fresh.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        return None
    mn = b.GetMin(); mx = b.GetMax()
    return {{
        'min': [float(mn[0]), float(mn[1]), float(mn[2])],
        'max': [float(mx[0]), float(mx[1]), float(mx[2])],
    }}

cube_paths = {cube_paths!r}
cube_path = cube_paths[0] if cube_paths else ""  # primary cube for legacy fields
target_path = {target_path!r}
duration_s = {duration_s}
xy_tol = {xy_tolerance}
floor_tol = {floor_tolerance}
rest_speed = {rest_speed}
require_upright = {require_upright}
upright_tol = {upright_tol}
seed_base = {seed}
n_runs = {n_runs}

def _world_up_dot(path):
    \"\"\"Read prim's world rotation, return cube_up_vector · world_up.
    Cube's local +Z transformed to world. Dot with world +Z gives the
    'upright-ness' in [-1, 1] (1 = perfectly upright, -1 = upside-down,
    0 = on its side).\"\"\"
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    try:
        xf = UsdGeom.Xformable(p)
        m = xf.ComputeLocalToWorldTransform(0)
        # world up vector after rotation = third column of rotation matrix
        # (m * [0,0,1] direction; ignore translation)
        col_z = (float(m[2][0]), float(m[2][1]), float(m[2][2]))
        # Normalize (in case scale != 1)
        n = (col_z[0]**2 + col_z[1]**2 + col_z[2]**2) ** 0.5
        if n < 1e-9: return None
        # Dot with world up (0,0,1) = z component normalized
        return float(col_z[2] / n)
    except Exception:
        return None

# ---- Phase 0 multi-run support: snapshot + restore between runs ----

def _seed_all(s):
    \"\"\"Pin random/numpy/torch seeds. cuRobo trajopt uses torch RNGs;
    pinning before each run makes IK initial-guesses deterministic
    given (seed, run_idx).\"\"\"
    _rand.seed(s)
    try:
        import numpy as _np
        _np.random.seed(s)
    except Exception:
        pass
    try:
        import torch as _t_mod
        _t_mod.manual_seed(s)
        if _t_mod.cuda.is_available():
            _t_mod.cuda.manual_seed_all(s)
    except Exception:
        pass

def _ensure_translate_op(prim):
    \"\"\"Get-or-create a Translate xform op. Used for cube reset.\"\"\"
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            return op
    return xf.AddTranslateOp()

def _snapshot_cubes():
    \"\"\"Read cube xform translates (USD-authoritative initial state)
    plus PhysX velocities if rigid-body API present.\"\"\"
    snap = {{}}
    for cp in cube_paths:
        prim = stage.GetPrimAtPath(cp)
        if not prim or not prim.IsValid():
            continue
        wp = _world_pos(cp)
        # Local translate op value (write-back target)
        local_t = None
        try:
            xf = UsdGeom.Xformable(prim)
            for op in xf.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    v = op.Get()
                    if v is not None:
                        local_t = (float(v[0]), float(v[1]), float(v[2]))
                    break
        except Exception:
            pass
        snap[cp] = {{'world': wp, 'local_t': local_t}}
    return snap

def _restore_cubes(snap):
    \"\"\"Restore cube xform translates AND zero PhysX velocity attrs.\"\"\"
    for cp, s in snap.items():
        prim = stage.GetPrimAtPath(cp)
        if not prim or not prim.IsValid():
            continue
        if s.get('local_t') is not None:
            try:
                op = _ensure_translate_op(prim)
                op.Set(Gf.Vec3d(*s['local_t']))
            except Exception:
                pass
        # Zero velocities — PhysX caches them on the rigid-body API
        for vname in ('physics:velocity', 'physics:angularVelocity'):
            a = prim.GetAttribute(vname)
            if a and a.IsValid():
                try: a.Set(Gf.Vec3f(0, 0, 0))
                except Exception:
                    try: a.Set(Gf.Vec3d(0, 0, 0))
                    except Exception: pass

def _snapshot_ctrl_attrs():
    \"\"\"Walk stage, find prims with any ctrl:* attr, snapshot all values.\"\"\"
    snap = {{}}
    for prim in stage.Traverse():
        ctrl_vals = {{}}
        for a in prim.GetAttributes():
            n = a.GetName()
            if n.startswith('ctrl:') or n.startswith('builtin_pp:') or n.startswith('cortex:'):
                v = a.Get()
                if v is not None:
                    ctrl_vals[n] = v
        if ctrl_vals:
            snap[str(prim.GetPath())] = ctrl_vals
    return snap

def _restore_ctrl_attrs(snap):
    for path, vals in snap.items():
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        for n, v in vals.items():
            a = prim.GetAttribute(n)
            if a and a.IsValid():
                try: a.Set(v)
                except Exception: pass

def _snapshot_articulation_joints():
    \"\"\"Capture joint positions for every articulation root via Articulation
    Cache if the helper is loaded; fall back to UsdPhysics joint-pos attrs.\"\"\"
    snap = {{}}
    try:
        from omni.isaac.core.articulations import Articulation
    except Exception:
        Articulation = None
    if Articulation is None:
        return snap
    # Find articulation roots via PhysxSchema.PhysxArticulationAPI
    try:
        from pxr import PhysxSchema
    except Exception:
        return snap
    for prim in stage.Traverse():
        if prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
            try:
                art = Articulation(str(prim.GetPath()))
                art.initialize()
                jp = art.get_joint_positions()
                if jp is not None:
                    snap[str(prim.GetPath())] = [float(x) for x in jp]
            except Exception:
                pass
    return snap

def _restore_articulation_joints(snap):
    if not snap: return
    try:
        from omni.isaac.core.articulations import Articulation
    except Exception:
        return
    for path, jp in snap.items():
        try:
            art = Articulation(path)
            art.initialize()
            art.set_joint_positions(jp)
        except Exception:
            pass

def _snapshot_fj_set():
    \"\"\"Set of FixedJoint prim paths existing now. Diff after run = transient FJs to delete.\"\"\"
    out = set()
    try:
        from pxr import UsdPhysics
    except Exception:
        return out
    for prim in stage.Traverse():
        try:
            if prim.IsA(UsdPhysics.FixedJoint) or prim.GetTypeName() == 'PhysicsFixedJoint':
                out.add(str(prim.GetPath()))
        except Exception:
            pass
    return out

def _delete_fj_diff(initial_set):
    \"\"\"Delete FJ prims that appeared since snapshot.\"\"\"
    current = _snapshot_fj_set()
    new_paths = current - initial_set
    for path in new_paths:
        try: stage.RemovePrim(path)
        except Exception: pass

def _do_one_run(run_idx, target_bbox):
    \"\"\"Single play→sample cycle. Caller is responsible for reset BEFORE call.\"\"\"
    _seed_all(seed_base + run_idx)
    tl.set_current_time(0.0)
    tl.play()
    p_pre = _world_pos(cube_path) or [0,0,0]
    pre_pos_per_cube = {{cp: (_world_pos(cp) or [0,0,0]) for cp in cube_paths}}
    real_start = _t.time()
    last_t = 0.0
    while True:
        app.update()
        cur_t = float(tl.get_current_time())
        if cur_t >= duration_s - 0.15 and last_t < duration_s - 0.15:
            _p = _world_pos(cube_path)
            if _p is not None: p_pre = _p
            for cp in cube_paths:
                _pp = _world_pos(cp)
                if _pp is not None: pre_pos_per_cube[cp] = _pp
        if cur_t >= duration_s:
            break
        if _t.time() - real_start > duration_s + 60:
            break
        last_t = cur_t

    p_final = _world_pos(cube_path)
    final_pos_per_cube = {{cp: _world_pos(cp) for cp in cube_paths}}
    cur_t = float(tl.get_current_time())
    tl.stop()

    dt = max(cur_t - last_t, 1e-3)
    if p_final is None:
        velocity = [0.0, 0.0, 0.0]; speed = 0.0
    else:
        velocity = [(p_final[i] - p_pre[i]) / dt for i in range(3)]
        speed = (velocity[0]**2 + velocity[1]**2 + velocity[2]**2) ** 0.5

    bb = target_bbox
    delivered = []
    per_cube = {{}}
    for cp in cube_paths:
        cp_final = final_pos_per_cube.get(cp)
        cp_pre   = pre_pos_per_cube.get(cp)
        if cp_final is None: continue
        cp_in_xy = (bb['min'][0] - xy_tol <= cp_final[0] <= bb['max'][0] + xy_tol
                    and bb['min'][1] - xy_tol <= cp_final[1] <= bb['max'][1] + xy_tol)
        cp_above = cp_final[2] >= bb['min'][2] - floor_tol
        if cp_pre is not None:
            cp_v = [(cp_final[i] - cp_pre[i]) / dt for i in range(3)]
            cp_speed = (cp_v[0]**2 + cp_v[1]**2 + cp_v[2]**2) ** 0.5
        else:
            cp_speed = speed
        cp_at_rest = cp_speed < rest_speed
        cp_delivered = bool(cp_in_xy and cp_above and cp_at_rest)
        per_cube[cp] = {{'final': cp_final, 'in_xy': cp_in_xy, 'above_floor': cp_above,
                        'speed': cp_speed, 'delivered': cp_delivered}}
        if cp_delivered: delivered.append(cp)

    in_xy = (p_final is not None
             and bb['min'][0] - xy_tol <= p_final[0] <= bb['max'][0] + xy_tol
             and bb['min'][1] - xy_tol <= p_final[1] <= bb['max'][1] + xy_tol)
    above_floor = (p_final is not None
                   and p_final[2] >= bb['min'][2] - floor_tol)
    at_rest = speed < rest_speed

    upright_dot = _world_up_dot(cube_path)
    upright_ok = (not require_upright) or (upright_dot is not None and upright_dot >= upright_tol)

    if len(cube_paths) > 1:
        success = bool(delivered) and upright_ok
    else:
        success = bool(in_xy and above_floor and at_rest and upright_ok)

    return {{
        'success': success,
        'cube_final': p_final,
        'cube_velocity': velocity,
        'cube_speed': speed,
        'in_target_xy': in_xy,
        'above_floor': above_floor,
        'at_rest': at_rest,
        'cube_upright_dot': upright_dot,
        'upright_ok': upright_ok,
        'sim_t_reached': cur_t,
        'delivered_cubes': delivered,
        'per_cube_status': per_cube,
        'seed': seed_base + run_idx,
    }}

# ---- Main ----

tl = omni.timeline.get_timeline_interface()
app = omni.kit.app.get_app()

tl.stop()
tl.set_current_time(0.0)
tl.set_end_time(max(tl.get_end_time(), duration_s + 5.0))

p_init = _world_pos(cube_path)
target_bbox = _world_bbox(target_path)
cube_inits = {{cp: _world_pos(cp) for cp in cube_paths}}

if p_init is None or target_bbox is None:
    print(json.dumps({{
        'success': False,
        'error': f'cube_path or target_path not found: cube={{p_init is not None}}, target={{target_bbox is not None}}',
        'n_runs': n_runs, 'seed': seed_base,
    }}))
else:
    # Snapshot pre-play state — only needed for reset between runs (n_runs > 1).
    # Calling Articulation.initialize() pre-play in a stopped timeline can
    # corrupt the SimulationView and cause cube free-fall through the floor;
    # we therefore skip the snapshot entirely on the n_runs=1 fast path.
    if n_runs > 1:
        snap_cubes = _snapshot_cubes()
        snap_ctrl = _snapshot_ctrl_attrs()
        snap_joints = _snapshot_articulation_joints()
        snap_fj = _snapshot_fj_set()
    else:
        snap_cubes = snap_ctrl = snap_joints = None
        snap_fj = set()

    runs = []
    for ri in range(n_runs):
        if ri > 0:
            # Reset BEFORE run i (for i>=1). Run 0 uses scene as-is (live state from build+settle).
            tl.stop()
            _delete_fj_diff(snap_fj)
            _restore_cubes(snap_cubes)
            _restore_ctrl_attrs(snap_ctrl)
            _restore_articulation_joints(snap_joints)
            for _ in range(3): app.update()  # commit USD edits
        try:
            r = _do_one_run(ri, target_bbox)
        except Exception as _e:
            r = {{'success': False, 'error': f'run_exception: {{type(_e).__name__}}: {{str(_e)[:200]}}',
                  'seed': seed_base + ri}}
        runs.append(r)

    # Aggregate
    n_ok = sum(1 for r in runs if r.get('success'))
    success_rate = n_ok / float(n_runs)
    if n_runs >= 5:
        if n_ok >= 4: status = 'stable_ok'
        elif n_ok == 0: status = 'stable_fail'
        else: status = 'flaky'
    else:
        if n_ok == n_runs: status = 'stable_ok'
        elif n_ok == 0: status = 'stable_fail'
        else: status = 'flaky'

    # Primary result for legacy single-run callers: run 0's fields, hoisted
    primary = runs[0] if runs else {{}}
    out = dict(primary)
    out.update({{
        'cube_initial': p_init,
        'target_path': target_path,
        'target_bbox': target_bbox,
        'duration_s_requested': duration_s,
        'rest_speed_threshold': rest_speed,
        'floor_tolerance': floor_tol,
        'upright_tolerance_dot': upright_tol,
        'require_upright': require_upright,
        'cube_paths': cube_paths,
        'n_runs': n_runs,
        'seed_base': seed_base,
        'n_ok': n_ok,
        'success_rate': success_rate,
        'status': status,
        'runs': runs,
    }})
    # Multi-run: success reflects MAJORITY (≥ ceil(n/2)). Single-run: run 0.
    if n_runs > 1:
        out['success'] = (n_ok * 2 >= n_runs)
    print(json.dumps(out))
"""
    # Phase 0.7: scale timeout with n_runs × duration_s; default 600s
    # was insufficient for n_runs=5 × duration_s=90 (CP-35 NO_RESULT incident).
    _scaled_timeout = max(900, int(n_runs * (duration_s + 30) * 1.5 + 60))
    return await kit_tools.queue_exec_patch(code, "simulate_traversal_check", timeout=_scaled_timeout)


async def _handle_trace_config(args: Dict) -> Dict:
    """Parse IsaacLab @configclass files to trace parameter resolution chain."""
    import ast  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

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


async def _handle_validate_annotations(args: Dict) -> Dict:
    """Cross-check SDG annotations for common quality issues.

    Validates: bbox within image bounds, unique instance IDs,
    no zero-area boxes, declared classes actually appear.
    """
    from .. import kit_tools  # noqa: PLC0415
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


async def _handle_validate_calibration(args: Dict) -> Dict:
    """Validate a calibration result on a held-out test trajectory.

    Inputs:
      - calibrated_params: dict — typically the output of calibrate_physics
      - test_data_path: path to HDF5 with held-out real trajectory
      - baseline_error (optional): pre-calibration error to compare against

    Returns: per-joint and overall RMSE, plus contact-force comparison if F/T data
    is detected. The actual replay-in-sim happens via IsaacLab; this handler
    validates inputs and prepares the comparison report. If the HDF5 file already
    contains a sim_joint_positions field (added by a prior replay run), the
    report is computed in-process.
    """
    from .. import tool_executor as _te  # noqa: PLC0415
    _check_real_data_path = _te._check_real_data_path
    _per_joint_rmse = _te._per_joint_rmse

    calibrated_params = args.get("calibrated_params")
    test_data_path = args.get("test_data_path", "")
    baseline_error = args.get("baseline_error")

    if not isinstance(calibrated_params, dict) or not calibrated_params:
        return {"error": "calibrated_params must be a non-empty dict"}

    err = _check_real_data_path(test_data_path)
    if err:
        return {"error": err}

    # Try to read sim/real trajectories if a prior replay has populated them.
    sim_positions: Optional[List[List[float]]] = None
    real_positions: Optional[List[List[float]]] = None
    contact_forces_sim: Optional[List[List[float]]] = None
    contact_forces_real: Optional[List[List[float]]] = None
    has_ft_data = False
    try:
        import h5py  # type: ignore  # noqa: PLC0415
        with h5py.File(test_data_path, "r") as f:
            if "joint_positions" in f:
                real_positions = f["joint_positions"][:].tolist()
            if "sim_joint_positions" in f:
                sim_positions = f["sim_joint_positions"][:].tolist()
            if "contact_forces" in f:
                has_ft_data = True
                contact_forces_real = f["contact_forces"][:].tolist()
            if "sim_contact_forces" in f:
                contact_forces_sim = f["sim_contact_forces"][:].tolist()
    except ImportError:
        pass
    except Exception as e:  # pragma: no cover — corrupted HDF5
        return {"error": f"Failed to read test_data_path: {e}"}

    per_joint_rmse: List[float] = []
    overall_rmse: Optional[float] = None
    if sim_positions is not None and real_positions is not None:
        per_joint_rmse = _per_joint_rmse(sim_positions, real_positions)
        if per_joint_rmse:
            overall_rmse = sum(r * r for r in per_joint_rmse) / len(per_joint_rmse)
            overall_rmse = overall_rmse ** 0.5

    contact_force_rmse: Optional[float] = None
    if contact_forces_sim is not None and contact_forces_real is not None:
        n = min(len(contact_forces_sim), len(contact_forces_real))
        if n > 0:
            comp = min(len(contact_forces_sim[0]), len(contact_forces_real[0]))
            sq = 0.0
            count = 0
            for t in range(n):
                for c in range(comp):
                    d = float(contact_forces_sim[t][c]) - float(contact_forces_real[t][c])
                    sq += d * d
                    count += 1
            if count:
                contact_force_rmse = (sq / count) ** 0.5

    improvement_pct: Optional[float] = None
    if overall_rmse is not None and baseline_error not in (None, 0):
        try:
            baseline = float(baseline_error)
            if baseline > 0:
                improvement_pct = round(100.0 * (baseline - overall_rmse) / baseline, 2)
        except (TypeError, ValueError):
            improvement_pct = None

    needs_replay = sim_positions is None or real_positions is None

    return {
        "type": "calibration_validation",
        "test_data_path": test_data_path,
        "calibrated_param_keys": sorted(calibrated_params.keys()),
        "trajectory_error": overall_rmse,
        "per_joint_rmse": per_joint_rmse,
        "baseline_error": baseline_error,
        "improvement_pct": improvement_pct,
        "has_ft_data": has_ft_data,
        "contact_force_rmse": contact_force_rmse,
        "needs_replay": needs_replay,
        "message": (
            "Validation report computed in-process from cached sim trajectories."
            if not needs_replay
            else "Sim trajectories not present in HDF5 — run the calibrated params in IsaacLab "
                 "to produce 'sim_joint_positions' before reporting tracking error."
        ),
    }


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

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "object_count": len(objects),
    }


async def _handle_validate_semantic_labels(args: Dict) -> Dict:
    """Lint every Semantics.SemanticsAPI annotation on the current stage."""
    from .. import kit_tools  # noqa: PLC0415
    code = """\
import json
try:
    import omni.usd
    from pxr import Usd, UsdGeom, Semantics
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        issues = []
        labeled_prims = 0
        class_to_prims = {}  # class_name -> [prim_path, ...]
        for prim in stage.Traverse():
            try:
                instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                    Semantics.SemanticsAPI, 'GetAll'
                ) else []
            except Exception:
                instances = []
            if not instances:
                continue
            labeled_prims += 1
            prim_path = str(prim.GetPath())
            # Visibility / active checks — labels on hidden prims don't render
            try:
                is_active = bool(prim.IsActive())
            except Exception:
                is_active = True
            try:
                imageable = UsdGeom.Imageable(prim)
                vis = imageable.ComputeVisibility() if imageable else 'inherited'
                is_visible = vis != 'invisible'
            except Exception:
                is_visible = True
            if not is_active:
                issues.append({
                    'severity': 'warning', 'kind': 'inactive_labeled_prim',
                    'prim_path': prim_path,
                    'detail': 'Prim has Semantics labels but is deactivated — will not appear in SDG output.',
                })
            elif not is_visible:
                issues.append({
                    'severity': 'warning', 'kind': 'invisible_labeled_prim',
                    'prim_path': prim_path,
                    'detail': 'Prim has Semantics labels but visibility=invisible — will not render.',
                })
            class_seen_on_prim = []
            for sem in instances:
                try:
                    instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''
                except Exception:
                    instance_name = ''
                try:
                    type_attr = sem.GetSemanticTypeAttr()
                    sem_type = type_attr.Get() if type_attr and type_attr.IsValid() else ''
                except Exception:
                    sem_type = ''
                try:
                    data_attr = sem.GetSemanticDataAttr()
                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else ''
                except Exception:
                    cls = ''
                cls = '' if cls is None else str(cls)
                if cls == '':
                    issues.append({
                        'severity': 'error', 'kind': 'empty_class_name',
                        'prim_path': prim_path,
                        'detail': f'Semantics instance {instance_name!r} has empty semanticData — SDG writer will skip the label.',
                    })
                else:
                    class_to_prims.setdefault(cls, []).append(prim_path)
                if str(sem_type) == 'class' and cls != '':
                    class_seen_on_prim.append(cls)
            if len(class_seen_on_prim) > 1 and len(set(class_seen_on_prim)) > 1:
                issues.append({
                    'severity': 'error', 'kind': 'conflicting_class_labels',
                    'prim_path': prim_path,
                    'detail': f'Prim has multiple semantic_type=class instances with different class_names: {sorted(set(class_seen_on_prim))}',
                })
        # Singleton-class warnings: a class with exactly one prim is often a typo.
        for cls, prims in class_to_prims.items():
            if len(prims) == 1 and len(class_to_prims) > 1:
                issues.append({
                    'severity': 'warning', 'kind': 'singleton_class',
                    'prim_path': prims[0],
                    'detail': f'Class {cls!r} is used on a single prim — likely a typo against the intended bulk class.',
                })
        summary = {
            'labeled_prims': labeled_prims,
            'classes': len(class_to_prims),
            'issues': len(issues),
        }
        ok = not any(i['severity'] == 'error' for i in issues)
        print(json.dumps({
            'ok': ok,
            'summary': summary,
            'issues': issues,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(
        code, "Validate USD-side Semantics.SemanticsAPI annotations on the stage"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Semantic-label validation queued. Kit will print a JSON dict with keys: "
            "ok (bool, false when any error issue is present), summary "
            "({labeled_prims, classes, issues}), issues (list of {severity, kind, "
            "prim_path, detail}). Distinct from PR #23 validate_annotations: this tool "
            "lints the USD STAGE annotations, validate_annotations lints the SDG "
            "OUTPUT FILES on disk."
        ),
    }


async def _handle_validate_teleop_demo(args: Dict) -> Dict:
    """Validate an HDF5 teleop file against the robomimic schema."""
    from .. import tool_executor as _te  # noqa: PLC0415
    _open_hdf5_safely = _te._open_hdf5_safely
    import math  # noqa: PLC0415
    path = args["hdf5_path"]
    f, reason = _open_hdf5_safely(path)
    if f is None:
        # Distinguish "h5py missing" from "file missing" for the LLM
        available = not reason.startswith("h5py")
        return {
            "available": available,
            "path": path,
            "reason": reason,
            "demos_checked": 0,
            "demos_ok": 0,
            "issues": [{"demo": "*", "problem": reason}],
            "ready_for_training": False,
        }

    issues: List[Dict[str, str]] = []
    demos_checked = 0
    demos_ok = 0
    total_transitions = 0
    try:
        data_group = f.get("data")
        if data_group is None:
            issues.append({"demo": "*", "problem": "missing /data group"})
        else:
            for demo_name in data_group.keys():
                demos_checked += 1
                demo = data_group[demo_name]
                actions = demo.get("actions")
                if actions is None:
                    issues.append({"demo": demo_name, "problem": "missing actions dataset"})
                    continue
                shape = getattr(actions, "shape", ())
                if len(shape) != 2:
                    issues.append({
                        "demo": demo_name,
                        "problem": f"actions rank {len(shape)} != 2, shape={shape}",
                    })
                    continue
                if shape[0] == 0:
                    issues.append({"demo": demo_name, "problem": "episode length 0"})
                    continue
                # NaN / Inf check — sample first N rows to stay L0-cheap
                sample = actions[: min(shape[0], 4096)]
                has_bad = False
                for row in sample:
                    for v in row:
                        try:
                            fv = float(v)
                        except (TypeError, ValueError):
                            continue
                        if math.isnan(fv) or math.isinf(fv):
                            has_bad = True
                            break
                    if has_bad:
                        break
                if has_bad:
                    issues.append({"demo": demo_name, "problem": "NaN or Inf in actions"})
                    continue
                obs = demo.get("obs")
                if obs is not None and len(obs.keys()) == 0:
                    issues.append({"demo": demo_name, "problem": "obs group is empty"})
                    continue
                demos_ok += 1
                total_transitions += int(shape[0])
    finally:
        try:
            f.close()
        except Exception:
            pass

    return {
        "available": True,
        "path": path,
        "demos_checked": demos_checked,
        "demos_ok": demos_ok,
        "total_transitions": total_transitions,
        "issues": issues,
        "ready_for_training": demos_checked > 0 and len(issues) == 0,
    }


async def _handle_verify_pickplace_pipeline(args: Dict) -> Dict:
    """Verify a pick-place pipeline is physically executable.

    Pilot of the *verifier* tool class — counterpart to *resolvers*.
    Resolvers translate input to structured values; verifiers check that
    a built scene actually meets the functional requirements of a skill.

    Use case: after building a pick-place cell, call this to confirm
    every (robot, pick, place) stage is within the robot's workspace.
    Returns per-stage reach data + a boolean `pipeline_ok` summary +
    a list of `issues` describing any unreachable stages or handoff
    gaps. Agent's protocol: call this BEFORE declaring the build done.
    If pipeline_ok is False, either fix the layout or surface the
    issue to the user.

    Args (one of):
      stages: list of {"robot_path": str, "pick_path": str, "place_path": str,
                       optional "robot_kind": str, "reach_m": float}
      OR pairwise: robot_path, pick_path, place_path for a single stage

    Optional flags:
      feasibility: bool (default False). When True, after form-gate runs,
                   delegate each stage to diagnose_scene_feasibility (Phase 1)
                   to add geometric pre-flight checks. Verdicts of
                   `infeasible`/`overconstrained` propagate as `issues[]` so
                   the form-gate flips to NOT-OK before expensive sim.
                   Per Opus §F. Default off preserves current contract;
                   canonical_instantiator turns it on for hard-instantiate.
    """
    from .. import kit_tools  # noqa: PLC0415
    from .. import tool_executor as _te  # noqa: PLC0415
    _ROBOT_REACH_M = _te._ROBOT_REACH_M
    _augment_verify_with_feasibility = _te._augment_verify_with_feasibility
    stages = args.get("stages")
    if not stages:
        # Allow a single-stage shorthand
        rp = args.get("robot_path"); pp = args.get("pick_path"); pl = args.get("place_path")
        if rp and pp and pl:
            stages = [{"robot_path": rp, "pick_path": pp, "place_path": pl,
                       "robot_kind": args.get("robot_kind", ""),
                       "reach_m": args.get("reach_m")}]
    if not stages:
        return {"error": "verify_pickplace_pipeline requires 'stages' (list of {robot_path,pick_path,place_path}) or single robot_path+pick_path+place_path"}

    cube_path = args.get("cube_path", "") or ""
    footprint_bounds = args.get("footprint_bounds")  # optional [[xmin,ymin],[xmax,ymax]]

    # Build the Kit script that resolves world positions per stage.
    import json as _j  # noqa: PLC0415
    stages_json = _j.dumps(stages)
    cube_path_json = _j.dumps(cube_path)
    # repr() handles Python None correctly; json.dumps() would emit "null"
    # which is not a valid Python identifier when interpolated into the script.
    footprint_bounds_repr = repr(footprint_bounds)
    code = f"""\
import omni.usd, json, builtins as _bi
from pxr import UsdGeom, Usd, PhysxSchema

stage = omni.usd.get_context().get_stage()
cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
ROBOT_REACH = {_ROBOT_REACH_M!r}
stages = {stages_json}
cube_path = {cube_path_json}
footprint_bounds = {footprint_bounds_repr}

def _world_pos(path):
    \"\"\"Return bbox center as a fallback. Used only for the robot base call below.\"\"\"
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        try:
            xf = UsdGeom.Xformable(p)
            t = xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
            return [float(t[0]), float(t[1]), float(t[2])]
        except Exception:
            return None
    c = b.GetMidpoint()
    return [float(c[0]), float(c[1]), float(c[2])]

def _robot_base_pos(path):
    \"\"\"Robot base = (xy center of bbox, min z). Reach is measured from
    the floor-mounted base, not the mid-height bbox center.\"\"\"
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        return _world_pos(path)
    c = b.GetMidpoint(); mn = b.GetMin()
    return [float(c[0]), float(c[1]), float(mn[2])]

def _closest_point_on_bbox(path, ref):
    \"\"\"Return the point on prim's world-space bbox closest to ref. For
    a long conveyor or a wide bin this lets reach calculations target
    the nearest accessible point, not the (possibly far) bbox center.\"\"\"
    p = stage.GetPrimAtPath(path)
    if not p or not p.IsValid():
        return None
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        return _world_pos(path)
    mn = b.GetMin(); mx = b.GetMax()
    return [
        float(max(mn[0], min(ref[0], mx[0]))),
        float(max(mn[1], min(ref[1], mx[1]))),
        float(max(mn[2], min(ref[2], mx[2]))),
    ]

def _dist(a, b):
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5

def _bbox_xy_of(prim_or_path):
    \"\"\"Return ((xmin,ymin),(xmax,ymax)) of prim's world bbox xy, or None.\"\"\"
    if isinstance(prim_or_path, str):
        p = stage.GetPrimAtPath(prim_or_path)
    else:
        p = prim_or_path
    if not p or not p.IsValid():
        return None
    b = cache.ComputeWorldBound(p).ComputeAlignedRange()
    if b.IsEmpty():
        return None
    mn = b.GetMin(); mx = b.GetMax()
    return ((float(mn[0]), float(mn[1])), (float(mx[0]), float(mx[1])))

def _active_conveyors():
    \"\"\"Discover prims with PhysxSurfaceVelocityAPI applied AND non-zero velocity.
    Returns list of (path, ((xmin,ymin),(xmax,ymax)), speed_m_per_s).\"\"\"
    out = []
    for prim in stage.Traverse():
        if not prim.HasAPI(PhysxSchema.PhysxSurfaceVelocityAPI):
            continue
        api = PhysxSchema.PhysxSurfaceVelocityAPI(prim)
        attr = api.GetSurfaceVelocityAttr()
        v = attr.Get() if attr else None
        if v is None:
            continue
        speed = (float(v[0])**2 + float(v[1])**2 + float(v[2])**2) ** 0.5
        if speed < 1e-6:
            continue
        bb = _bbox_xy_of(prim)
        if bb is None:
            continue
        out.append((str(prim.GetPath()), bb, speed))
    return out

def _segment_overlaps_bbox_xy(a, b, bbox, samples=20):
    \"\"\"Sample segment a->b in xy; True if any sample lies inside bbox.\"\"\"
    (xmin, ymin), (xmax, ymax) = bbox
    for k in range(samples + 1):
        t = k / samples
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return True
    return False

def _controller_installed(robot_path, n_robots_in_pipeline):
    \"\"\"Check builtins for a pick-place controller subscription tied to this
    robot. Returns (attr_name, kind) or None.

    cuRobo scopes its subscription per-robot (_curobo_pp_sub_<TAG>) so multi-
    robot scenes can be checked unambiguously. Other controllers (native,
    spline, diffik, osc) use un-tagged names — those can only be attributed
    to a robot when there's a single robot in the pipeline.\"\"\"
    tag = robot_path.replace('/', '_').strip('_')
    # Per-robot tagged subs (multi-robot safe)
    if hasattr(_bi, '_curobo_pp_sub_' + tag):
        return ('_curobo_pp_sub_' + tag, 'curobo')
    if hasattr(_bi, '_builtin_pp_sub_' + tag):
        return ('_builtin_pp_sub_' + tag, 'builtin')
    # Un-tagged subs (only safe to attribute when scene has 1 robot)
    if n_robots_in_pipeline == 1:
        for prefix, kind in (('_native_pp_sub', 'native'), ('_spline_pp_sub', 'spline'),
                             ('_diffik_pp_sub', 'diffik'), ('_osc_pp_sub', 'osc')):
            if hasattr(_bi, prefix):
                return (prefix, kind)
    return None

results = []
issues = []
prev_place = None
_n_robots = len({{s.get('robot_path','') for s in stages if s.get('robot_path','')}})
for i, s in enumerate(stages):
    rp = s.get('robot_path',''); pkp = s.get('pick_path',''); plp = s.get('place_path','')
    rk = (s.get('robot_kind','') or '').lower()
    reach = s.get('reach_m')
    if reach is None:
        reach = ROBOT_REACH.get(rk) if rk in ROBOT_REACH else ROBOT_REACH.get('default', 0.8)
    rpos = _robot_base_pos(rp)
    pkpos = _closest_point_on_bbox(pkp, rpos) if rpos else _world_pos(pkp)
    plpos = _closest_point_on_bbox(plp, rpos) if rpos else _world_pos(plp)
    stage_result = {{'index': i, 'robot_path': rp, 'pick_path': pkp, 'place_path': plp,
                     'robot_pos': rpos, 'pick_pos': pkpos, 'place_pos': plpos,
                     'reach_m': reach}}
    bad = False
    if rpos is None:
        issues.append(f'stage {{i}}: robot prim {{rp!r}} not found'); bad = True
    if pkpos is None:
        issues.append(f'stage {{i}}: pick prim {{pkp!r}} not found'); bad = True
    if plpos is None:
        issues.append(f'stage {{i}}: place prim {{plp!r}} not found'); bad = True
    if rpos and pkpos:
        d = _dist(rpos, pkpos); stage_result['pick_distance'] = d
        if d > reach:
            issues.append(f'stage {{i}}: pick at {{pkp}} is {{d:.2f}}m from robot {{rp}} (>reach {{reach:.2f}}m)'); bad = True
    if rpos and plpos:
        d = _dist(rpos, plpos); stage_result['place_distance'] = d
        if d > reach:
            issues.append(f'stage {{i}}: place at {{plp}} is {{d:.2f}}m from robot {{rp}} (>reach {{reach:.2f}}m)'); bad = True
    stage_result['reachable'] = not bad
    # controller_installed check: per-robot subscription must exist in builtins
    if rp:
        ci = _controller_installed(rp, _n_robots)
        stage_result['controller_installed'] = ci is not None
        if ci is None:
            issues.append(f'[controller_installed] stage {{i}}: no pick-place controller subscription found in builtins for robot {{rp}} (looked for _curobo_pp_sub_<tag>; un-tagged variants only matched when single-robot)')
        else:
            stage_result['controller_attr'] = ci[0]
            stage_result['controller_kind'] = ci[1]
    if prev_place is not None and pkpos is not None:
        # Handoff gap: distance from previous stage's place point to this stage's pick.
        # In a conveyor pipeline these can differ (cube travels along the conveyor)
        # but if the conveyor doesn't span the gap, the cube never arrives.
        gap = _dist(prev_place, pkpos)
        stage_result['handoff_gap_to_prev'] = gap
        if gap > 3.0:  # very loose — flag obviously broken handoffs
            issues.append(f'stage {{i}}: handoff gap from previous place to this pick is {{gap:.2f}}m — is there a conveyor bridging?')
        if gap > 0.30:
            # conveyor_active check: must have an active conveyor whose xy bbox
            # intersects the place->pick segment, otherwise the cube is stranded.
            actives = _active_conveyors()
            bridge = None
            for cpath, cbbox, cspd in actives:
                if _segment_overlaps_bbox_xy(prev_place, pkpos, cbbox):
                    bridge = (cpath, cspd); break
            stage_result['conveyor_active'] = bridge is not None
            if bridge is None:
                issues.append(f'[conveyor_active] stage {{i}}: no active conveyor bridges the {{gap:.2f}}m place->pick handoff (looking for any prim with PhysxSurfaceVelocityAPI applied AND non-zero velocity whose xy bbox intersects the segment)')
            else:
                stage_result['bridging_conveyor'] = bridge[0]
                stage_result['bridging_conveyor_speed'] = bridge[1]
    prev_place = plpos
    results.append(stage_result)

# cube_source_bridged: cube must start at first pick zone xy (within 0.20m)
# OR be on an active conveyor whose bbox contains both the cube and the pick.
cube_source_bridged = None
cube_source_note = ''
if cube_path:
    cpos = _world_pos(cube_path)
    first_pick = results[0].get('pick_pos') if results else None
    if cpos is None:
        issues.append(f'[cube_source_bridged] cube prim {{cube_path!r}} not found')
        cube_source_bridged = False
    elif first_pick is None:
        issues.append(f'[cube_source_bridged] no first-stage pick position resolved')
        cube_source_bridged = False
    else:
        dxy = ((cpos[0]-first_pick[0])**2 + (cpos[1]-first_pick[1])**2) ** 0.5
        if dxy <= 0.20:
            cube_source_bridged = True
            cube_source_note = f'cube at first pick zone (dxy={{dxy:.2f}}m)'
        else:
            for cpath, cbbox, cspd in _active_conveyors():
                (xmin, ymin), (xmax, ymax) = cbbox
                cube_on_it = xmin <= cpos[0] <= xmax and ymin <= cpos[1] <= ymax
                pick_in_it = xmin <= first_pick[0] <= xmax and ymin <= first_pick[1] <= ymax
                if cube_on_it and pick_in_it:
                    cube_source_bridged = True
                    cube_source_note = f'cube on active conveyor {{cpath}} (speed={{cspd:.3f}}) bridges pick zone'
                    break
            if cube_source_bridged is None:
                cube_source_bridged = False
                issues.append(f'[cube_source_bridged] cube {{cube_path}} at xy=({{cpos[0]:.2f}},{{cpos[1]:.2f}}) is {{dxy:.2f}}m from first pick zone xy=({{first_pick[0]:.2f}},{{first_pick[1]:.2f}}); not at pick zone (within 0.20m) AND not on an active conveyor that bridges into it')
                cube_source_note = 'cube stranded'

# footprint_within_bounds: when caller supplies footprint_bounds, every
# user-authored prim under /World must have its xy world bbox inside.
# Catches CONSTRAINT-01-style "build a 2x2m cell" violations early —
# without it, reach checks alone pass while the cell sprawls beyond bounds.
footprint_violations = []
if footprint_bounds:
    (fb_xmin, fb_ymin), (fb_xmax, fb_ymax) = footprint_bounds
    # Skip these — they're system prims with unbounded or negative-z extents
    _skip_prefixes = ('/World/Render', '/World/persistent', '/World/Look',
                      '/World/PhysicsScene', '/World/Materials')
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if not path.startswith('/World/') or path.count('/') < 2:
            continue
        if path.startswith(_skip_prefixes):
            continue
        # Only top-level user prims — descendants are inside their parent's bbox
        if path.count('/') > 2:
            continue
        bb = _bbox_xy_of(prim)
        if bb is None:
            continue
        (pxmin, pymin), (pxmax, pymax) = bb
        if pxmin < fb_xmin or pymin < fb_ymin or pxmax > fb_xmax or pymax > fb_ymax:
            footprint_violations.append({{
                'path': path,
                'bbox_xy': [[round(pxmin, 3), round(pymin, 3)],
                            [round(pxmax, 3), round(pymax, 3)]],
            }})
            issues.append(
                f'[footprint_bounds] {{path}} bbox xy '
                f'({{pxmin:.2f}},{{pymin:.2f}})->({{pxmax:.2f}},{{pymax:.2f}}) '
                f'exceeds bounds [{{fb_xmin}},{{fb_ymin}}]->[{{fb_xmax}},{{fb_ymax}}]'
            )

ok = (all(s.get('reachable', False) for s in results)
      and all(s.get('conveyor_active', True) for s in results)
      and all(s.get('controller_installed', True) for s in results)
      and (cube_source_bridged is None or cube_source_bridged)
      and not footprint_violations
      and not any('handoff' in i for i in issues)
      and not any(i.startswith('[conveyor_active]') for i in issues)
      and not any(i.startswith('[controller_installed]') for i in issues)
      and not any(i.startswith('[cube_source_bridged]') for i in issues)
      and not any(i.startswith('[footprint_bounds]') for i in issues))
out = {{
    'stages': results,
    'issues': issues,
    'pipeline_ok': ok,
    'cube_source_bridged': cube_source_bridged,
    'cube_source_note': cube_source_note,
    'footprint_violations': footprint_violations,
    'rationale': 'Per-stage: reach + controller subscription + handoff conveyor; pipeline-level: cube source at pick zone or on active bridging conveyor; optional: footprint_bounds for CONSTRAINT-01.',
}}
print(json.dumps(out))
"""
    result = await kit_tools.queue_exec_patch(code, "verify_pickplace_pipeline")
    feasibility = bool(args.get("feasibility", False))
    if feasibility:
        result = await _augment_verify_with_feasibility(result, stages)
    return result


# ---------------------------------------------------------------------------
# Phase 7 wave 16 — final data-handler stragglers (COMPLETES data-handler migration)


async def _handle_list_extensions(args: Dict) -> Dict:
    """List Kit extensions registered with the extension manager."""
    from .. import kit_tools
    enabled_only = bool(args.get("enabled_only", False))
    name_filter = args.get("name_filter") or ""
    code = f"""\
import json
import omni.kit.app

mgr = omni.kit.app.get_app().get_extension_manager()
exts = list(mgr.get_extensions())
enabled_only = {repr(enabled_only)}
nf = {repr(name_filter)}.lower()

out = []
for ext in exts:
    try:
        ext_id = ext.get("id") or ext.get("name") or ""
        version = ext.get("version") or ""
        enabled = bool(ext.get("enabled", False))
        title = ext.get("title") or ext.get("name") or ext_id
    except AttributeError:
        ext_id = getattr(ext, "id", "") or ""
        version = getattr(ext, "version", "") or ""
        enabled = bool(getattr(ext, "enabled", False))
        title = getattr(ext, "title", "") or ext_id
    if enabled_only and not enabled:
        continue
    if nf and nf not in str(ext_id).lower():
        continue
    out.append({{
        "id": str(ext_id),
        "version": str(version),
        "enabled": enabled,
        "title": str(title),
    }})

print(json.dumps({{"extensions": out, "total": len(out)}}))
"""
    return await kit_tools.queue_exec_patch(code, "List Kit extensions")



# ---------------------------------------------------------------------------
# Phase 9 follow-up — _handle_fix_error migrated from tool_executor.py (2026-05-13)


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
        from ....retrieval.context_retriever import find_matching_patterns, detect_isaac_version
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
from pxr import Usd, UsdPhysics, PhysxSchema

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
        for child in list(Usd.PrimRange(prim))[1:]:
            if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
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


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 9 — populate dispatch dicts with this module's handlers.

    Called by `handlers/_dispatch.py:register_handlers()` which is the
    sole dispatch entry point from `tool_executor.py`.
    """
    # Data handlers (27)
    data["check_collision_mesh"] = _handle_check_collision_mesh
    data["check_teleop_hardware"] = _handle_check_teleop_hardware
    data["check_tf_health"] = _handle_check_tf_health
    data["check_vram_headroom"] = _handle_check_vram_headroom
    data["compare_sim_real_video"] = _handle_compare_sim_real_video
    data["console_error_autodetect"] = _handle_console_error_autodetect
    data["diagnose_domain_gap"] = _handle_diagnose_domain_gap
    data["diagnose_performance"] = _handle_diagnose_performance
    data["diagnose_physics_error"] = _handle_diagnose_physics_error
    data["diagnose_whole_body"] = _handle_diagnose_whole_body
    data["explain_error"] = None  # LLM-inline (no executor)
    data["get_active_state"] = _handle_get_active_state
    data["get_console_errors"] = _handle_get_console_errors
    data["get_debug_info"] = _handle_get_debug_info
    data["hardware_compatibility_check"] = _handle_hardware_compatibility_check
    data["list_extensions"] = _handle_list_extensions
    data["measure_distance"] = _handle_measure_distance
    data["measure_sim_real_gap"] = _handle_measure_sim_real_gap
    data["proactive_check"] = _handle_proactive_check
    data["simulate_traversal_check"] = _handle_simulate_traversal_check
    data["trace_config"] = _handle_trace_config
    data["validate_annotations"] = _handle_validate_annotations
    data["validate_calibration"] = _handle_validate_calibration
    data["validate_scene_blueprint"] = _handle_validate_scene_blueprint
    data["validate_semantic_labels"] = _handle_validate_semantic_labels
    data["validate_teleop_demo"] = _handle_validate_teleop_demo
    data["verify_pickplace_pipeline"] = _handle_verify_pickplace_pipeline

    # Code-gen handlers (19)
    codegen["check_path_clearance"] = _gen_check_path_clearance
    codegen["check_physics_health"] = _gen_check_physics_health
    codegen["check_singularity"] = _gen_check_singularity
    codegen["configure_zmq_stream"] = _gen_configure_zmq_stream
    codegen["create_broken_scene"] = _gen_create_broken_scene
    codegen["debug_draw"] = _gen_debug_draw
    codegen["debug_graph"] = _gen_debug_graph
    codegen["enable_deterministic_mode"] = _gen_enable_deterministic_mode
    codegen["enable_extension"] = _gen_enable_extension
    codegen["fix_error"] = _handle_fix_error
    codegen["highlight_prim"] = _gen_highlight_prim
    codegen["monitor_joint_effort"] = _gen_monitor_joint_effort
    codegen["preflight_check"] = _gen_preflight_check
    codegen["set_clearance_monitor"] = _gen_set_clearance_monitor
    codegen["show_workspace"] = _gen_show_workspace
    codegen["sim_control"] = _gen_sim_control
    codegen["visualize_clearance"] = _gen_visualize_clearance
    codegen["visualize_collision_mesh"] = _gen_visualize_collision_mesh
    codegen["visualize_forces"] = _gen_visualize_forces

