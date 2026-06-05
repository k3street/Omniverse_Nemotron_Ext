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
    from ..tool_executor import _ROS2_QOS_PRESETS  # noqa: PLC0415
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
    from ..tool_executor import _NAV2_BRIDGE_PROFILES, _OG_NODE_TYPE_MAP  # noqa: PLC0415
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
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 7 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
