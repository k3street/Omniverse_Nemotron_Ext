"""Teleop handlers — target scope: VR/glove/keyboard teleop session
start/stop, controller→robot mapping config, demo recording,
safety limits/watchdog, mapping export.

Phase 6 wave 8 — first teleop code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-7.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 8 — teleop session + mapping + safety + watchdog


def _gen_start_teleop_session(args: Dict) -> str:
    from ..tool_executor import _STREAM_QUALITY_PRESETS, _DEVICE_AXIS_DEFAULTS
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
        is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
        is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
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
if not robot_prim or not robot_prim.IsValid():
    raise RuntimeError(f'configure_teleop_mapping: robot not found at {{ROBOT_PATH!r}}')

# Discover joints if not explicitly provided
if JOINT_NAMES is None:
    JOINT_NAMES = []
    for child in robot_prim.GetAllChildren():
        if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
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

if not mapping:
    # No axes mapped — either the robot has no revolute/prismatic joints
    # under it, or DEVICE_AXES was given but JOINT_NAMES auto-discovery
    # returned nothing. Printing "configured" with 0 axes mapped is a
    # silent-success — the teleop session would do nothing on input.
    raise RuntimeError(
        f'configure_teleop_mapping: 0 axes ended up mapped on {{ROBOT_PATH!r}} — '
        f'either the robot has no Revolute/Prismatic joints under it, or '
        f'the provided DEVICE_AXES/JOINT_NAMES combination produced no '
        f'entries. Joints discovered: {{JOINT_NAMES!r}}'
    )

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
    if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
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
        is_revolute = j.IsA(UsdPhysics.RevoluteJoint)
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
        is_revolute = j.IsA(UsdPhysics.RevoluteJoint)
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
            is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
            is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
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
        is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
        is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
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


def _gen_export_teleop_mapping(args: Dict) -> str:
    """Generate a script that writes the teleop mapping YAML to workspace/teleop_mappings/."""
    session_name = str(args["session_name"])
    device = str(args["device"])
    joint_map = args.get("joint_map") or []
    gains = args.get("gains") or {"position": 400, "velocity": 40}
    robot = str(args.get("robot", "franka_panda"))

    # Safe quoting — repr() on every user string that ends up in source
    return (
        "from pathlib import Path\n"
        "import json\n"
        "\n"
        f"session_name = {repr(session_name)}\n"
        f"device = {repr(device)}\n"
        f"robot = {repr(robot)}\n"
        f"joint_map = {json.dumps(joint_map)}\n"
        f"gains = {json.dumps(gains)}\n"
        "\n"
        "out_dir = Path('workspace') / 'teleop_mappings'\n"
        "out_dir.mkdir(parents=True, exist_ok=True)\n"
        "out_path = out_dir / f'{session_name}.yaml'\n"
        "\n"
        "lines = []\n"
        "lines.append(f'robot: {robot}')\n"
        "lines.append(f'device: {device}')\n"
        "lines.append('joints:')\n"
        "for j in joint_map:\n"
        "    name = j.get('name', '')\n"
        "    source = j.get('source', '')\n"
        "    gain = j.get('gain', 1.0)\n"
        "    limit = j.get('limit_rad', [-3.14, 3.14])\n"
        "    lines.append(f'  - name: {name}')\n"
        "    lines.append(f'    source: {source}')\n"
        "    lines.append(f'    gain: {gain}')\n"
        "    lines.append(f'    limit_rad: [{limit[0]}, {limit[1]}]')\n"
        "lines.append('gains:')\n"
        "for k, v in gains.items():\n"
        "    lines.append(f'  {k}: {v}')\n"
        "\n"
        "out_path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
        "print(f'Wrote mapping to {out_path}')\n"
    )


def _gen_generate_teleop_watchdog_script(args: Dict) -> str:
    """Generate a Python script arming a teleop watchdog on a given articulation."""
    robot_path = str(args["robot_path"])
    timeout_ms = int(args.get("timeout_ms", 500))
    hold_time_ms = int(args.get("hold_time_ms", 2000))
    socket_path = str(args.get("socket_path", "/ws/teleop"))

    return (
        '"""\n'
        'Teleop watchdog — hold-last-command then zero velocity targets on timeout.\n'
        'Auto-generated by Isaac Assist (Phase 7C addendum).\n'
        '"""\n'
        "import asyncio\n"
        "import time\n"
        "\n"
        f"ROBOT_PATH = {repr(robot_path)}\n"
        f"SOCKET_PATH = {repr(socket_path)}\n"
        f"TIMEOUT_MS = {timeout_ms}\n"
        f"HOLD_TIME_MS = {hold_time_ms}\n"
        "\n"
        "_last_msg_ts = time.monotonic()\n"
        "_zeroed = False\n"
        "\n"
        "\n"
        "def _on_teleop_message(msg):\n"
        "    global _last_msg_ts, _zeroed\n"
        "    _last_msg_ts = time.monotonic()\n"
        "    _zeroed = False\n"
        "\n"
        "\n"
        "def _zero_velocity_targets():\n"
        "    import omni.usd\n"
        "    from pxr import UsdPhysics\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    root = stage.GetPrimAtPath(ROBOT_PATH)\n"
        "    if not root or not root.IsValid():\n"
        "        print(f'[watchdog] robot not found: {ROBOT_PATH}')\n"
        "        return\n"
        "    count = 0\n"
        "    for prim in stage.Traverse():\n"
        "        if not str(prim.GetPath()).startswith(ROBOT_PATH):\n"
        "            continue\n"
        "        if prim.HasAPI(UsdPhysics.DriveAPI):\n"
        "            drive = UsdPhysics.DriveAPI.Get(prim, 'angular')\n"
        "            attr = drive.GetTargetVelocityAttr()\n"
        "            if attr:\n"
        "                attr.Set(0.0)\n"
        "                count += 1\n"
        "    print(f'[watchdog] zeroed {count} joint drive(s)')\n"
        "\n"
        "\n"
        "async def watchdog_loop():\n"
        "    global _zeroed\n"
        "    print(f'[watchdog] armed on {ROBOT_PATH} — timeout {TIMEOUT_MS} ms, hold {HOLD_TIME_MS} ms')\n"
        "    while True:\n"
        "        await asyncio.sleep(TIMEOUT_MS / 1000.0)\n"
        "        elapsed_ms = (time.monotonic() - _last_msg_ts) * 1000.0\n"
        "        if elapsed_ms <= TIMEOUT_MS:\n"
        "            continue\n"
        "        print(f'[watchdog] timeout — elapsed {elapsed_ms:.0f} ms, holding last command')\n"
        "        await asyncio.sleep(HOLD_TIME_MS / 1000.0)\n"
        "        if not _zeroed:\n"
        "            _zero_velocity_targets()\n"
        "            _zeroed = True\n"
        "\n"
        "\n"
        "# Entry point — call arm() from the Kit main loop or Script Editor.\n"
        "def arm():\n"
        "    loop = asyncio.get_event_loop()\n"
        "    return loop.create_task(watchdog_loop())\n"
    )


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 8 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
