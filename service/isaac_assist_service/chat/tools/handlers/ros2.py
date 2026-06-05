"""ROS2 handlers — target scope: ROS2 bridge setup, TF tree
inspection, QoS reconfiguration, ROS2 time/clock config,
rosbag replay.

Phase 6 wave 7 — first ROS2 code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-6.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

# ---------------------------------------------------------------------------
# Theme-local constants (Phase 8 wave 4, 2026-05-13)
# Migrated from tool_executor.py — used only by this module.

_ROS2_QOS_PRESETS = {
    "scan": ("BEST_EFFORT", "VOLATILE", "Laser scan data — high-frequency, drop-tolerant"),
    "robot_description": ("RELIABLE", "TRANSIENT_LOCAL", "Robot URDF — latched, must arrive"),
    "tf": ("RELIABLE", "VOLATILE", "Transform tree — must be reliable"),
    "tf_static": ("RELIABLE", "TRANSIENT_LOCAL", "Static transforms — latched"),
    "cmd_vel": ("RELIABLE", "VOLATILE", "Velocity commands — must not be dropped"),
    "camera": ("BEST_EFFORT", "VOLATILE", "Camera images — high-bandwidth, drop-tolerant"),
    "image": ("BEST_EFFORT", "VOLATILE", "Image data — high-bandwidth, drop-tolerant"),
    "joint_states": ("RELIABLE", "VOLATILE", "Joint state feedback — must be reliable"),
    "clock": ("BEST_EFFORT", "VOLATILE", "Simulation clock — high-frequency"),
}

_NAV2_BRIDGE_PROFILES = {
    "ur10e_moveit2": {
        "description": "UR10e arm wired for MoveIt2 — joint state publish, FollowJointTrajectory subscribe, TF.",
        "topics": ["/joint_states", "/joint_command", "/tf"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "topic_values": {
            "PublishJointState.inputs:topicName": "/joint_states",
            "SubscribeJointState.inputs:topicName": "/joint_command",
        },
    },
    "jetbot_nav2": {
        "description": "Jetbot wired for Nav2 — lidar publish, cmd_vel subscribe, odom publish, TF, clock.",
        "topics": ["/scan", "/cmd_vel", "/odom", "/tf", "/clock"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishLidar", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("SubscribeCmdVel", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("PublishOdom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("DifferentialController", "isaacsim.robot.wheeled_robots.DifferentialController"),
        ],
        "topic_values": {
            "PublishLidar.inputs:topicName": "/scan",
            "SubscribeCmdVel.inputs:topicName": "/cmd_vel",
            "PublishOdom.inputs:topicName": "/odom",
            "PublishClock.inputs:topicName": "/clock",
        },
    },
    "franka_moveit2": {
        "description": "Franka arm wired for MoveIt2 — joint state, gripper state, TF.",
        "topics": ["/joint_states", "/gripper", "/tf"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("PublishGripper", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "topic_values": {
            "PublishJointState.inputs:topicName": "/joint_states",
            "SubscribeJointState.inputs:topicName": "/joint_command",
            "PublishGripper.inputs:topicName": "/gripper",
        },
    },
    "amr_full": {
        "description": "Full AMR — 2x lidar, 4x camera, odom, cmd_vel, TF, clock.",
        "topics": [
            "/scan_front", "/scan_rear", "/cmd_vel", "/odom", "/tf", "/clock",
            "/camera_front/image_raw", "/camera_rear/image_raw",
            "/camera_left/image_raw", "/camera_right/image_raw",
        ],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishLidarFront", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("PublishLidarRear", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("SubscribeCmdVel", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("PublishOdom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("PublishCamFront", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamRear", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamLeft", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamRight", "isaacsim.ros2.bridge.ROS2PublishImage"),
        ],
        "topic_values": {
            "PublishLidarFront.inputs:topicName": "/scan_front",
            "PublishLidarRear.inputs:topicName": "/scan_rear",
            "SubscribeCmdVel.inputs:topicName": "/cmd_vel",
            "PublishOdom.inputs:topicName": "/odom",
            "PublishCamFront.inputs:topicName": "/camera_front/image_raw",
            "PublishCamRear.inputs:topicName": "/camera_rear/image_raw",
            "PublishCamLeft.inputs:topicName": "/camera_left/image_raw",
            "PublishCamRight.inputs:topicName": "/camera_right/image_raw",
        },
    },
}


# ---------------------------------------------------------------------------
# Phase 6 wave 7 — ROS2 bridge + TF + QoS + rosbag


def _gen_show_tf_tree(args: Dict) -> str:
    root_frame = args.get("root_frame", "world")
    return f'''\
import os
import omni.graph.core as og

# Auto-detect ROS distro
ros_distro = os.environ.get("ROS_DISTRO", "humble")
print(f"ROS distro: {{ros_distro}}")

# Check for TF publisher OmniGraph node — create one if missing
stage = __import__("omni.usd", fromlist=["usd"]).get_context().get_stage()
tf_graph_path = "/World/ROS2_TF_Tree"
tf_prim = stage.GetPrimAtPath(tf_graph_path)
if not tf_prim.IsValid():
    print("No TF publisher graph found — creating one at " + tf_graph_path)
    _bt = og.GraphBackingType
    if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
        _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
    elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
        _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
    else:
        _backing = list(_bt)[0]

    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": tf_graph_path,
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("tf_pub", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "tf_pub.inputs:execIn"),
            ],
        }},
    )
    print("Created ROS2PublishTransformTree graph")

# Acquire TF data via the transform listener interface
from isaacsim.ros2.tf_viewer import acquire_transform_listener_interface

interface = acquire_transform_listener_interface()
interface.initialize(ros_distro)
transforms = interface.get_transforms("{root_frame}")

# Format and print as indented tree
def _print_tree(frames, parent, indent=0):
    prefix = "  " * indent + ("|- " if indent > 0 else "")
    print(f"{{prefix}}{{parent}}")
    children = [f for f in frames if f.get("parent") == parent]
    for child in children:
        _print_tree(frames, child["child"], indent + 1)

print(f"\\nTF Tree (root: {root_frame}):")
print("=" * 40)
if transforms:
    _print_tree(transforms, "{root_frame}")
    print(f"\\nTotal frames: {{len(transforms)}}")
else:
    print("(no transforms found — is the simulation running?)")
'''


def _gen_configure_ros2_bridge(args: Dict) -> str:
    sensors = args.get("sensors", [])
    domain_id = args.get("ros2_domain_id", 0)

    if not sensors:
        return (
            "raise ValueError("
            "'configure_ros2_bridge: no sensors were specified — nothing to configure. "
            "Pass a sensors list like [{\"type\":\"camera\",\"prim_path\":\"/World/Camera\","
            "\"topic_name\":\"/camera/rgb\"}].')\n"
        )

    # Build OmniGraph nodes and connections
    node_defs = []
    conn_defs = []
    val_defs = []

    # Always add tick + ROS2Context
    node_defs.append('("tick", "omni.graph.action.OnPlaybackTick")')
    node_defs.append(f'("ros2_context", f"{{_ROS2_NS}}.ROS2Context")')
    if domain_id != 0:
        val_defs.append(f'("ros2_context.inputs:domain_id", {domain_id})')

    for i, sensor in enumerate(sensors):
        stype = sensor.get("type", "camera")
        prim_path = sensor.get("prim_path", "")
        topic_name = sensor.get("topic_name", "")
        frame_id = sensor.get("frame_id", "")
        node_name = f"{stype}_{i}"

        # Map sensor type to OG node type
        og_node_class = {
            "camera": "ROS2CameraHelper",
            "lidar": "ROS2PublishLaserScan",
            "imu": "ROS2PublishImu",
            "clock": "ROS2PublishClock",
            "joint_state": "ROS2PublishJointState",
        }.get(stype, f"ROS2Publish{stype.title()}")

        node_defs.append(f'("{node_name}", f"{{_ROS2_NS}}.{og_node_class}")')

        # Connect tick → sensor node
        conn_defs.append(f'("tick.outputs:tick", "{node_name}.inputs:execIn")')

        # Connect context
        conn_defs.append(f'("ros2_context.outputs:context", "{node_name}.inputs:context")')

        # Set values
        if topic_name:
            val_defs.append(f'("{node_name}.inputs:topicName", "{topic_name}")')
        if frame_id:
            val_defs.append(f'("{node_name}.inputs:frameId", "{frame_id}")')
        if prim_path and stype != "clock":
            # clock doesn't have a prim path input
            if stype == "camera":
                val_defs.append(f'("{node_name}.inputs:renderProductPath", "{prim_path}")')
            elif stype == "joint_state":
                val_defs.append(f'("{node_name}.inputs:targetPrim", "{prim_path}")')
            else:
                val_defs.append(f'("{node_name}.inputs:prim", "{prim_path}")')

    nodes_str = ",\n            ".join(node_defs)
    conns_str = ",\n            ".join(conn_defs)
    vals_str = ",\n            ".join(val_defs)

    sensor_summary = ", ".join(s.get("type", "?") for s in sensors)

    return f'''\
import os as _ros2_os
import omni.graph.core as og

# Same pre-check as setup_ros2_bridge: rmw init fails cryptically
# without AMENT_PREFIX_PATH in Kit's environment. Raise before
# attempting graph build.
if not _ros2_os.environ.get("AMENT_PREFIX_PATH"):
    raise RuntimeError(
        "configure_ros2_bridge: AMENT_PREFIX_PATH is not set in the Kit process "
        "environment. This is a pre-launch env config — the agent running inside "
        "Kit cannot fix it retroactively. Relay to the user: close Isaac Sim, "
        "source ROS2 setup in the terminal "
        "(e.g. `source /opt/ros/humble/setup.bash`), then relaunch Isaac Sim "
        "from that terminal. No nodes were created this call."
    )

# Handle Isaac Sim version namespace differences
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
print(f"Isaac Sim version: {{isaacsim.__version__}}, using namespace: {{_ROS2_NS}}")

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
        "graph_path": "/World/ROS2_Bridge",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {nodes_str}
        ],
        keys.CONNECT: [
            {conns_str}
        ],
        keys.SET_VALUES: [
            {vals_str}
        ],
    }},
)

print(f"ROS2 bridge configured with {{len(nodes)}} nodes")
print(f"Sensors: {sensor_summary}")
print(f"Domain ID: {domain_id}")
print("Start simulation (Play) to begin publishing.")
'''


def _gen_fix_ros2_qos(args: Dict) -> str:
    """Generate code to update the QoS profile on a ROS2 publisher for a given topic."""
    # Phase 8 wave 4 — _ROS2_QOS_PRESETS migrated to module body.
    topic = args["topic"]

    # Determine the QoS preset from the topic name
    topic_key = topic.strip("/").split("/")[-1]
    preset = _ROS2_QOS_PRESETS.get(topic_key)

    if preset:
        reliability, durability, description = preset
    else:
        # Default to RELIABLE + VOLATILE for unknown topics
        reliability = "RELIABLE"
        durability = "VOLATILE"
        description = f"Unknown topic '{topic}' — defaulting to RELIABLE"

    return f'''\
import omni.graph.core as og
import json

topic_name = "{topic}"
target_reliability = "{reliability}"
target_durability = "{durability}"

# QoS profile: {description}
# Find the publisher node for this topic and update its QoS profile
all_graphs = og.get_all_graphs()
updated = False

for graph in all_graphs:
    for node in graph.get_nodes():
        node_type = node.get_type_name()
        if "ROS2" not in node_type:
            continue

        topic_attr = node.get_attribute("inputs:topicName")
        if not topic_attr:
            continue

        current_topic = topic_attr.get()
        if current_topic != topic_name:
            continue

        # Found the node — update QoS profile
        qos_attr = node.get_attribute("inputs:qosProfile")
        if qos_attr:
            qos_attr.set(f"{{target_reliability}}, {{target_durability}}")
            updated = True
            print(f"Updated QoS on {{node.get_prim_path()}}: {{target_reliability}}, {{target_durability}}")

        # Also set reliability/durability if separate attributes exist
        rel_attr = node.get_attribute("inputs:reliability")
        if rel_attr:
            rel_attr.set(target_reliability)

        dur_attr = node.get_attribute("inputs:durability")
        if dur_attr:
            dur_attr.set(target_durability)

        break  # Only update the first matching node

if not updated:
    # No existing publisher node to patch. The tool's name is fix_ros2_qos
    # — claiming success when nothing was fixed misleads the agent. Raise
    # with the hint so the user/agent knows to create the publisher first.
    raise RuntimeError(
        f"fix_ros2_qos: no ROS2 publisher node found for topic {{topic_name!r}} — "
        f"nothing to patch. Create the publisher first (e.g. via "
        f"configure_ros2_bridge) then re-run. Recommended QoS: "
        f"reliability={{target_reliability}}, durability={{target_durability}} "
        f"({description})."
    )
'''


def _gen_configure_ros2_time(args: Dict) -> str:
    """Generate OmniGraph code for ROS2 clock publishing and use_sim_time configuration."""
    mode = args["mode"]
    time_scale = args.get("time_scale", 1.0)

    if mode == "real_time":
        return '''\
import carb.settings
import omni.graph.core as og

# Configure real_time mode: disable use_sim_time, no clock publishing needed
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", False)

# Remove existing ROS2PublishClock nodes if any
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            node_path = node.get_prim_path()
            print(f"Note: ROS2PublishClock at {node_path} is active but use_sim_time=false")
            print("ROS2 nodes will use wall clock time.")

print("Configured real_time mode: use_sim_time=false")
print("ROS2 nodes will use the system wall clock.")
'''

    # sim_time or scaled mode — both need clock publishing
    time_scale_block = ""
    if mode == "scaled":
        time_scale_block = f'''
# Set simulation time scale
import omni.timeline
tl = omni.timeline.get_timeline_interface()
tl.set_time_codes_per_second(tl.get_time_codes_per_second() * {time_scale})
print(f"Time scale set to {time_scale}x")
'''

    return f'''\
import omni.graph.core as og
import carb.settings

# ── Step 1: Enable use_sim_time ──────────────────────────────────────────
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", True)
print("Enabled use_sim_time=true")

# ── Step 2: Create ROS2PublishClock node in an action graph ──────────────
# Check if a clock publisher already exists
clock_exists = False
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            clock_exists = True
            print(f"ROS2PublishClock already exists at {{node.get_prim_path()}}")
            break
    if clock_exists:
        break

if not clock_exists:
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
            "graph_path": "/World/ROS2ClockGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
            ],
        }},
    )
    print("Created ROS2ClockGraph with ROS2PublishClock node")
    print("  /clock topic will publish simulation time")
{time_scale_block}
print("Configured {mode} mode: ROS2 nodes will use simulation clock from /clock topic")
'''


def _gen_setup_ros2_bridge(args: Dict) -> str:
    """Generate OmniGraph code for a complete ROS2 bridge profile."""
    from ._shared import _OG_NODE_TYPE_MAP
    profile_name = args["profile"]
    robot_path = args["robot_path"]
    graph_path = args.get("graph_path", "/World/ROS2_Bridge")

    profile = _NAV2_BRIDGE_PROFILES.get(profile_name)
    if profile is None:
        valid = ", ".join(sorted(_NAV2_BRIDGE_PROFILES.keys()))
        # repr() ensures the embedded profile name is properly quoted in source
        return (
            "# ROS2 bridge profile not found.\n"
            f"raise ValueError('Unknown profile ' + {profile_name!r} + '. Valid: {valid}')\n"
        )

    nodes = profile["nodes"]
    # OnPlaybackTick → every other node's exec input (where present)
    connections = []
    for name, _ntype in nodes:
        if name == "OnPlaybackTick":
            continue
        if name == "ROS2Context":
            continue  # context is referenced, not ticked
        connections.append((f"OnPlaybackTick.outputs:tick", f"{name}.inputs:execIn"))

    # Bind articulation/controller to the robot path where applicable
    values = dict(profile.get("topic_values", {}))
    for name, _ntype in nodes:
        if name == "ArticulationController":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "DifferentialController":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "PublishJointState":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "SubscribeJointState":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "PublishOdom":
            values[f"{name}.inputs:chassisPrim"] = robot_path
        elif name == "PublishTF":
            values[f"{name}.inputs:targetPrims"] = [robot_path]

    # Render node tuples (with type remap for safety)
    node_defs = ",\n            ".join(
        f"('{n}', '{_OG_NODE_TYPE_MAP.get(t, t)}')" for n, t in nodes
    )
    conn_defs = ",\n            ".join(
        f"('{s}', '{t}')" for s, t in connections
    )
    val_lines = []
    for k, v in values.items():
        if isinstance(v, str):
            val_lines.append(f"            ('{k}', '{v}')")
        else:
            val_lines.append(f"            ('{k}', {v!r})")
    val_block = ",\n".join(val_lines)

    return f"""\
import os as _ros2_os
import omni.graph.core as og

# ROS2 bridge profile: {profile_name}
# {profile['description']}
# Topics: {', '.join(profile['topics'])}

# Pre-check: the ROS2 bridge nodes load rmw at create time, which needs
# AMENT_PREFIX_PATH set in the process environment. If Isaac Sim was
# launched without sourcing /opt/ros/.../setup.bash (the common
# user-error), og.Controller.edit would emit cryptic "failed to get
# symbol 'rmw_init_options_init'" errors and leave a half-built graph.
# Raise early with an actionable message so the agent can relay it.
if not _ros2_os.environ.get('AMENT_PREFIX_PATH'):
    raise RuntimeError(
        'setup_ros2_bridge: AMENT_PREFIX_PATH is not set in the Kit process '
        'environment. ROS2 middleware (rmw) cannot initialize without it. '
        'The fix has to happen OUTSIDE Kit (the agent running inside Kit cannot '
        'set this retroactively). Ask the user to: (1) close Isaac Sim, '
        '(2) in the terminal, source their ROS2 distro setup '
        '(e.g. `source /opt/ros/humble/setup.bash`), then '
        '(3) relaunch Isaac Sim from that terminal. Until then, no ROS2 graph '
        'can be built. No nodes were created this call.'
    )

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
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
        keys.SET_VALUES: [
{val_block}
        ],
    }},
)
print('ROS2 bridge profile {profile_name} ready at {graph_path} for robot {robot_path}')
"""


def _gen_replay_rosbag(args: Dict) -> str:
    """Generate code to replay a rosbag deterministically through sim."""
    bag_path = args["bag_path"]
    sync_mode = args.get("sync_mode", "sim_time")
    topics = args.get("topics") or ["/cmd_vel"]
    rate = args.get("rate", 1.0)

    topic_list = ", ".join(repr(t) for t in topics)

    return f"""\
import subprocess
import shlex
import omni.timeline

bag_path = {bag_path!r}
sync_mode = {sync_mode!r}
rate = float({rate})
topics = [{topic_list}]

# Build ros2 bag play command. --clock makes the bag drive /clock when sim_time.
cmd_parts = ['ros2', 'bag', 'play', bag_path, '--rate', str(rate)]
if sync_mode == 'sim_time':
    cmd_parts.append('--clock')
if topics:
    cmd_parts.extend(['--topics'] + topics)

# Start the timeline so OmniGraph publishers/subscribers tick during replay.
tl = omni.timeline.get_timeline_interface()
if not tl.is_playing():
    tl.play()

print(f'Starting rosbag replay ({{sync_mode}} @ {{rate}}x): {{shlex.join(cmd_parts)}}')
proc = subprocess.Popen(cmd_parts)
print(f'Replay PID: {{proc.pid}} — use proc.wait() to block, proc.terminate() to abort')
"""


# ---------------------------------------------------------------------------
# Phase 7 wave 14 — ros2 diagnose/emit/precheck stragglers


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
    # Phase 8 mop-up — _ROS2_QOS_PRESETS is now module-local.
    from typing import List, Dict as _Dict, Any  # noqa: PLC0415
    issues: List[_Dict[str, Any]] = []

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
        "success": True,
        "issues": issues,
        "issue_count": len(issues),
        "ros2_context_found": scene_info.get("ros2_context_found", False),
        "distro": scene_info.get("distro"),
        "domain_id": scene_info.get("domain_id", "0"),
        "clock_publishing": scene_info.get("clock_publisher_found", False),
        "graphs_scanned": len(scene_info.get("og_graphs", [])),
        "message": f"Found {len(issues)} issue(s)" if issues else "All ROS2 checks passed — no issues found",
    }


async def _handle_emit_ros2_control_yaml(args: Dict) -> Dict:
    """Phase 6 M1: emit colcon-buildable ros2_control YAML for outside-Kit launch.

    Produces controller_manager + joint_state_broadcaster + joint_trajectory_controller
    config that uses the topic_based_ros2_control/TopicBasedSystem hardware plugin.
    Output written to args["output_path"] or returned as text.
    """
    robot_path = (args.get("robot_path") or "").strip()
    if not robot_path:
        return {"error": "emit_ros2_control_yaml requires robot_path"}
    controller_type = args.get("controller_type", "joint_trajectory_controller")
    output_path = args.get("output_path")
    js_topic = args.get("joint_states_topic", "/isaac_joint_states")
    jc_topic = args.get("joint_commands_topic", "/isaac_joint_commands")
    update_rate_hz = int(args.get("update_rate_hz", 100))

    yaml_text = f"""controller_manager:
  ros__parameters:
    update_rate: {update_rate_hz}

    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster

    {controller_type}:
      type: joint_trajectory_controller/JointTrajectoryController

{controller_type}:
  ros__parameters:
    joints:
      - panda_joint1
      - panda_joint2
      - panda_joint3
      - panda_joint4
      - panda_joint5
      - panda_joint6
      - panda_joint7
    command_interfaces:
      - position
    state_interfaces:
      - position
      - velocity
    state_publish_rate: {update_rate_hz}
    action_monitor_rate: 20

# ros2_control hardware: topic_based_ros2_control/TopicBasedSystem
# subscribes to {js_topic} and publishes to {jc_topic}
# (Isaac Sim publishes/subscribes the inverse via setup_ros2_control_compat.)
"""
    out = {"success": True, "yaml": yaml_text, "robot_path": robot_path}
    if output_path:
        try:
            from pathlib import Path as _P  # noqa: PLC0415
            p = _P(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml_text)
            out["written_to"] = str(p)
        except Exception as e:
            out["write_error"] = str(e)
    return out


async def _handle_precheck_ros2_environment(args: Dict) -> Dict:
    """Phase 6 M1: verify ROS2 environment before scene build.

    Checks:
      - AMENT_PREFIX_PATH set + non-empty
      - rosbridge port (default 9090) accepting connections
      - ROS_DOMAIN_ID set (or default 0)

    Returns {ok, issues[], details}. Fail-fast for the agent BEFORE expensive
    setup_ros2_bridge / build_scene operations.
    """
    import os, socket  # noqa: PLC0415
    issues = []
    details = {}

    ament = os.environ.get("AMENT_PREFIX_PATH")
    details["AMENT_PREFIX_PATH"] = ament or ""
    if not ament:
        issues.append("AMENT_PREFIX_PATH not set — source ROS2 install first")

    domain_id = os.environ.get("ROS_DOMAIN_ID", "0")
    details["ROS_DOMAIN_ID"] = domain_id

    port = int(args.get("rosbridge_port", 9090))
    details["rosbridge_port"] = port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect(("127.0.0.1", port))
        sock.close()
        details["rosbridge_reachable"] = True
    except Exception:
        details["rosbridge_reachable"] = False
        issues.append(f"rosbridge_server not listening on 127.0.0.1:{port}")

    return {"ok": len(issues) == 0, "issues": issues, "details": details}


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
    # Data handlers (3)
    data["diagnose_ros2"] = _handle_diagnose_ros2
    data["emit_ros2_control_yaml"] = _handle_emit_ros2_control_yaml
    data["precheck_ros2_environment"] = _handle_precheck_ros2_environment

    # Code-gen handlers (6)
    codegen["configure_ros2_bridge"] = _gen_configure_ros2_bridge
    codegen["configure_ros2_time"] = _gen_configure_ros2_time
    codegen["fix_ros2_qos"] = _gen_fix_ros2_qos
    codegen["replay_rosbag"] = _gen_replay_rosbag
    codegen["setup_ros2_bridge"] = _gen_setup_ros2_bridge
    codegen["show_tf_tree"] = _gen_show_tf_tree
