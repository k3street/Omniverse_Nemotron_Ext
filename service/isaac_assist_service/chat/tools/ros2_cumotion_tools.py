"""
Isaac ROS cuMotion — CUDA-accelerated motion planning for manipulation.

Covers:
  - CumotionActionServer        (time-optimal, collision-free trajectory planning)
  - CumotionRobotSegmenter      (depth-based robot masking for obstacle detection)
  - ESDFVisualizer              (nvblox ESDF voxel debug view)
  - GoalSetterNode              (MoveIt 2 end-effector goal interface)
  - AttachObjectServer          (collision-aware grasped-object attachment)
  - MoveIt 2 / Isaac Sim launch (UR / Franka / custom robots)
  - generate_xrdf               (XRDF skeleton generator from URDF)

Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_cumotion/index.html
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import kit_tools
from .ros2_autonomy_tools import _LAUNCHED_PROCESSES

_WORKSPACE = Path(__file__).resolve().parents[5] / "workspace"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_scene_dir() -> Path:
    scene_name = "untitled"
    try:
        ctx = await kit_tools.get_stage_context(full=False)
        stage_url = ctx.get("stage", {}).get("stage_url", "")
        if stage_url:
            basename = os.path.basename(stage_url)
            name, _ = os.path.splitext(basename)
            if name:
                scene_name = re.sub(r"[^\w\-]", "_", name)
    except Exception:
        pass
    d = _WORKSPACE / "scenes" / scene_name
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _pkg_exists(pkg: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ros2", "pkg", "prefix", pkg,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


def _nvblox_running() -> bool:
    return "nvblox/reconstruction" in _LAUNCHED_PROCESSES and \
        _LAUNCHED_PROCESSES["nvblox/reconstruction"]["process"].returncode is None


# ---------------------------------------------------------------------------
# Param YAML templates
# ---------------------------------------------------------------------------

_CUMOTION_PLANNER_PARAMS = """\
# Isaac ROS cuMotion planner parameters
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_cumotion/index.html
cumotion_planner:
  ros__parameters:
    robot: "{robot_xrdf}"
    urdf_path: "{urdf_path}"

    # Trajectory
    time_dilation_factor: {time_dilation_factor}
    max_attempts: {max_attempts}

    # World representation
    voxel_size: {voxel_size}
    read_esdf_world: {read_esdf_world}
    publish_curobo_world_as_voxels: {publish_voxels}
    esdf_service_name: "/nvblox_node/get_esdf_and_gradient"

    joint_states_topic: "{joint_states_topic}"
"""

_ROBOT_SEGMENTER_PARAMS = """\
# cuMotion robot segmenter parameters
robot_segmenter_node:
  ros__parameters:
    robot: "{robot_xrdf}"
    urdf_path: "{urdf_path}"
    joint_states_topic: "{joint_states_topic}"
    distance_threshold: {distance_threshold}
    depth_image_topics: {depth_topics}
    depth_camera_infos: {camera_info_topics}
    robot_mask_publish_topics: {robot_mask_topics}
    world_depth_publish_topics: {world_depth_topics}
    debug_robot_topic: "/cumotion/robot_segmenter/robot_spheres"
"""

_ESDF_VISUALIZER_PARAMS = """\
# cuMotion ESDF visualizer parameters
esdf_visualizer_node:
  ros__parameters:
    grid_size_m: {grid_size}
    grid_center_m: {grid_center}
    voxel_size: {voxel_size}
    publish_voxel_size: {publish_voxel_size}
    max_publish_voxels: {max_publish_voxels}
    esdf_service_name: "/nvblox_node/get_esdf_and_gradient"
    robot_base_frame: "{robot_base_frame}"
"""

_GOAL_SETTER_PARAMS = """\
# cuMotion goal setter parameters
goal_setter_node:
  ros__parameters:
    planner_group_name: "{planner_group}"
    planner_id: "cuMotion"
    end_effector_link: "{end_effector_link}"
"""

_OBJECT_ATTACHMENT_PARAMS = """\
# cuMotion object attachment parameters
attach_object_server:
  ros__parameters:
    robot: "{robot_xrdf}"
    urdf_path: "{urdf_path}"
    joint_states_topic: "{joint_states_topic}"
    depth_image_topics: ["{depth_topic}"]
    depth_camera_infos: ["{camera_info_topic}"]
    search_radius: {search_radius}
    surface_sphere_radius: {surface_sphere_radius}
    time_sync_slop: 0.1
    object_esdf_clearing_padding: [0.05, 0.05, 0.05]
    clustering_hdbscan_min_samples: {hdbscan_min_samples}
    clustering_hdbscan_min_cluster_size: {hdbscan_min_cluster_size}
"""

# ---------------------------------------------------------------------------
# XRDF skeleton template
# ---------------------------------------------------------------------------

_XRDF_SKELETON = """\
# Extended Robot Description Format (XRDF) for {robot_name}
# Auto-generated by Isaac Assist — review and adjust sphere geometry before use.
# Docs: https://nvidia-isaac-ros.github.io/concepts/manipulation/xrdf.html
format: xrdf
format_version: 1.0

default_joint_positions:
{default_joint_positions}

cspace:
  joint_names: {joint_names}
  acceleration_limits: {accel_limits}
  jerk_limits: {jerk_limits}

tool_frames: ["{tool_frame}"]

geometry:
  - geometry_name: collision
    spheres:{sphere_entries}

self_collision:
  ignore:
    # Adjacent link pairs — add more to improve performance
{self_collision_ignore}
  buffer_distance:
    default: 0.005
"""


def _build_xrdf(robot_name: str, joints: List[Dict], links: List[str],
                tool_frame: str) -> str:
    """Generate an XRDF skeleton from parsed URDF data."""
    joint_names = [f'"{j["name"]}"' for j in joints]
    n = len(joints)
    accel_limits = "[" + ", ".join(["10.0"] * n) + "]"
    jerk_limits  = "[" + ", ".join(["500.0"] * n) + "]"

    default_positions = "\n".join(
        f"  {j['name']}: 0.0" for j in joints
    )

    # One sphere per link — placeholder geometry
    sphere_entries = ""
    for link in links:
        sphere_entries += f"""
      - frame: {link}
        center: [0.0, 0.0, 0.0]
        radius: 0.05  # TODO: tune to actual link geometry"""

    # Ignore adjacent links
    ignore_pairs = ""
    for i in range(len(links) - 1):
        ignore_pairs += f'    - ["{links[i]}", "{links[i+1]}"]\n'

    return _XRDF_SKELETON.format(
        robot_name=robot_name,
        joint_names="[" + ", ".join(joint_names) + "]",
        accel_limits=accel_limits,
        jerk_limits=jerk_limits,
        default_joint_positions=default_positions,
        tool_frame=tool_frame,
        sphere_entries=sphere_entries,
        self_collision_ignore=ignore_pairs or '    - ["base_link", "link_1"]\n',
    )


# ---------------------------------------------------------------------------
# cuMotion Planner
# ---------------------------------------------------------------------------

async def handle_launch_cumotion_planner(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch the cuMotion action server (CumotionActionServer) — CUDA-accelerated
    time-optimal trajectory planning with collision avoidance.

    Exposes cumotion/move_group action (moveit_msgs/MoveGroup) for MoveIt 2.
    If nvblox is running, set read_esdf_world=true to use live 3D obstacle data.
    """
    robot_xrdf    = args.get("robot_xrdf", "ur5e.xrdf")
    urdf_path     = args.get("urdf_path", "")
    time_dilation = float(args.get("time_dilation_factor", 0.5))
    max_attempts  = int(args.get("max_attempts", 10))
    voxel_size    = float(args.get("voxel_size", 0.05))
    joint_states  = args.get("joint_states_topic", "/joint_states")

    # Auto-enable ESDF world if nvblox is running
    read_esdf = bool(args.get("read_esdf_world", _nvblox_running()))
    publish_voxels = bool(args.get("publish_curobo_world_as_voxels", False))

    key = "cumotion/planner"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_cumotion"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_cumotion not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_cumotion\n"
                "  source install/setup.bash\n"
                "See: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_cumotion/index.html"
            ),
        }

    scene_dir    = await _get_scene_dir()
    cumotion_dir = scene_dir / "cumotion"
    cumotion_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = cumotion_dir / "planner_params.yaml"
    params_yaml.write_text(
        _CUMOTION_PLANNER_PARAMS.format(
            robot_xrdf=robot_xrdf,
            urdf_path=urdf_path,
            time_dilation_factor=time_dilation,
            max_attempts=max_attempts,
            voxel_size=voxel_size,
            read_esdf_world=str(read_esdf).lower(),
            publish_voxels=str(publish_voxels).lower(),
            joint_states_topic=joint_states,
        )
    )

    cmd = [
        "ros2", "run", "isaac_ros_cumotion", "cumotion_planner_node",
        "--ros-args",
        "--params-file", str(params_yaml),
    ]
    if robot_xrdf and not robot_xrdf.endswith(".xrdf"):
        cmd += ["-p", f"robot:={robot_xrdf}"]
    if urdf_path:
        cmd += ["-p", f"urdf_path:={urdf_path}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "cumotion_planner",
    }

    return {
        "status":           "launched",
        "key":              key,
        "pid":              proc.pid,
        "robot_xrdf":       robot_xrdf,
        "urdf_path":        urdf_path,
        "read_esdf_world":  read_esdf,
        "time_dilation":    time_dilation,
        "params_file":      str(params_yaml),
        "actions": {
            "move_group": "cumotion/move_group (moveit_msgs/MoveGroup)",
        },
        "subscribed_topics": {
            "joint_states": joint_states,
        },
        "published_topics": {
            "voxels": "/curobo/voxels (visualization_msgs/Marker)",
        },
        "moveit_config_hint": (
            "Add to MoveIt planning pipeline:\n"
            "  planning_pipelines: [ompl, isaac_ros_cumotion]\n"
            "In RViz: Planning Library → 'isaac_ros_cumotion', Planner → 'cuMotion'"
        ),
        "message": (
            f"cuMotion planner launched (PID {proc.pid}). "
            f"ESDF world: {'enabled (nvblox)' if read_esdf else 'disabled'}. "
            f"Speed: {time_dilation:.0%}. "
            f"Select 'cuMotion' in RViz Planning Library to use."
        ),
    }


# ---------------------------------------------------------------------------
# Robot Segmenter
# ---------------------------------------------------------------------------

async def handle_launch_robot_segmenter(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch CumotionRobotSegmenter — segments robot geometry from depth images
    using cuMotion's collision sphere model. Outputs world_depth (depth without
    robot) for feeding into nvblox without robot-as-obstacle contamination.
    """
    robot_xrdf   = args.get("robot_xrdf", "ur5e.xrdf")
    urdf_path    = args.get("urdf_path", "")
    joint_states = args.get("joint_states_topic", "/joint_states")
    dist_thresh  = float(args.get("distance_threshold", 0.1))

    # Support single or multiple cameras
    depth_topics     = args.get("depth_image_topics",    ["/cumotion/depth_1/image_raw"])
    cam_info_topics  = args.get("depth_camera_infos",    ["/cumotion/depth_1/camera_info"])
    if isinstance(depth_topics, str):
        depth_topics = [depth_topics]
    if isinstance(cam_info_topics, str):
        cam_info_topics = [cam_info_topics]

    robot_mask_topics  = [t.replace("/image_raw", "/robot_mask").replace("/world_depth", "/robot_mask")
                          for t in depth_topics]
    world_depth_topics = [t.replace("/image_raw", "/world_depth")
                          for t in depth_topics]

    key = "cumotion/robot_segmenter"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_cumotion"):
        return {"status": "error", "message": "isaac_ros_cumotion not found. Build it first."}

    scene_dir    = await _get_scene_dir()
    cumotion_dir = scene_dir / "cumotion"
    cumotion_dir.mkdir(parents=True, exist_ok=True)

    def _yaml_list(items: List[str]) -> str:
        return "[" + ", ".join(f'"{t}"' for t in items) + "]"

    params_yaml = cumotion_dir / "segmenter_params.yaml"
    params_yaml.write_text(
        _ROBOT_SEGMENTER_PARAMS.format(
            robot_xrdf=robot_xrdf,
            urdf_path=urdf_path,
            joint_states_topic=joint_states,
            distance_threshold=dist_thresh,
            depth_topics=_yaml_list(depth_topics),
            camera_info_topics=_yaml_list(cam_info_topics),
            robot_mask_topics=_yaml_list(robot_mask_topics),
            world_depth_topics=_yaml_list(world_depth_topics),
        )
    )

    cmd = [
        "ros2", "run", "isaac_ros_cumotion", "robot_segmenter_node",
        "--ros-args", "--params-file", str(params_yaml),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "robot_segmenter",
    }

    return {
        "status":              "launched",
        "key":                 key,
        "pid":                 proc.pid,
        "depth_topics":        depth_topics,
        "world_depth_topics":  world_depth_topics,
        "robot_mask_topics":   robot_mask_topics,
        "params_file":         str(params_yaml),
        "nvblox_hint": (
            "Feed world_depth topics into nvblox instead of raw depth to prevent "
            "the robot arm appearing as an obstacle: "
            f"launch_nvblox with depth_topic='{world_depth_topics[0]}'"
        ),
        "message": (
            f"Robot segmenter launched (PID {proc.pid}). "
            f"Outputs robot-free depth on {world_depth_topics}. "
            f"Feed these into nvblox to avoid self-occlusion."
        ),
    }


# ---------------------------------------------------------------------------
# ESDF Visualizer
# ---------------------------------------------------------------------------

async def handle_launch_esdf_visualizer(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch ESDFVisualizer to query nvblox ESDF and render voxels in RViz.
    Requires nvblox to be running (launch_nvblox).
    """
    grid_size   = args.get("grid_size_m",  [2.0, 2.0, 2.0])
    grid_center = args.get("grid_center_m", [0.0, 0.0, 0.0])
    voxel_size  = float(args.get("voxel_size", 0.05))
    base_frame  = args.get("robot_base_frame", "base_link")

    key = "cumotion/esdf_visualizer"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not _nvblox_running():
        return {
            "status":  "warning",
            "message": "nvblox is not running — ESDF queries will fail. Run launch_nvblox first.",
        }

    if not await _pkg_exists("isaac_ros_esdf_visualizer"):
        return {"status": "error", "message": "isaac_ros_esdf_visualizer not found."}

    scene_dir    = await _get_scene_dir()
    cumotion_dir = scene_dir / "cumotion"
    cumotion_dir.mkdir(parents=True, exist_ok=True)

    def _list_str(lst): return "[" + ", ".join(str(v) for v in lst) + "]"

    params_yaml = cumotion_dir / "esdf_visualizer_params.yaml"
    params_yaml.write_text(
        _ESDF_VISUALIZER_PARAMS.format(
            grid_size=_list_str(grid_size),
            grid_center=_list_str(grid_center),
            voxel_size=voxel_size,
            publish_voxel_size=voxel_size / 2,
            max_publish_voxels=50000,
            robot_base_frame=base_frame,
        )
    )

    cmd = [
        "ros2", "run", "isaac_ros_esdf_visualizer", "esdf_visualizer_node",
        "--ros-args", "--params-file", str(params_yaml),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "esdf_visualizer",
    }

    return {
        "status":          "launched",
        "key":             key,
        "pid":             proc.pid,
        "published_topics": {"/curobo/voxels": "visualization_msgs/Marker — add Marker display in RViz"},
        "message": f"ESDF visualizer launched (PID {proc.pid}). Add /curobo/voxels Marker in RViz to inspect obstacle voxels.",
    }


# ---------------------------------------------------------------------------
# MoveIt 2 + cuMotion (Isaac Sim)
# ---------------------------------------------------------------------------

async def handle_launch_cumotion_moveit(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch the full MoveIt 2 stack with cuMotion plugin for Isaac Sim.
    Supports UR robots, Franka Panda, and custom robots.
    Isaac Sim scene must be open and timeline playing before calling this.
    """
    robot_type  = args.get("robot_type", "ur5e")   # ur5e|ur10e|ur3e|franka|custom
    robot_xrdf  = args.get("robot_xrdf", "")
    urdf_path   = args.get("urdf_path",  "")
    robot_ip    = args.get("robot_ip",   "192.56.1.2")   # ignored for Isaac Sim

    key = "cumotion/moveit"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    # Determine launch package + file
    if robot_type.startswith("ur"):
        pkg         = "isaac_ros_cumotion_examples"
        launch_file = "ur_isaac_sim.launch.py"
        extra_args  = [f"ur_type:={robot_type}"]
        if not await _pkg_exists(pkg):
            # Fall back to cumotion_moveit direct launch
            pkg         = "isaac_ros_cumotion_moveit"
            launch_file = "ur.launch.py"
            extra_args  = [f"ur_type:={robot_type}", f"robot_ip:={robot_ip}"]

    elif robot_type == "franka":
        pkg         = "isaac_ros_cumotion_examples"
        launch_file = "franka_isaac_sim.launch.py"
        extra_args  = []
        if not await _pkg_exists(pkg):
            pkg         = "isaac_ros_cumotion_moveit"
            launch_file = "franka.launch.py"
            extra_args  = []

    elif robot_type == "custom":
        if not robot_xrdf or not urdf_path:
            return {
                "status":  "error",
                "message": (
                    "Custom robot requires 'robot_xrdf' and 'urdf_path'. "
                    "Generate an XRDF skeleton with generate_xrdf first."
                ),
            }
        pkg         = "isaac_ros_cumotion_moveit"
        launch_file = "isaac_ros_cumotion.launch.py"
        extra_args  = [
            f"cumotion_planner.robot:={robot_xrdf}",
            f"cumotion_planner.urdf_path:={urdf_path}",
        ]
    else:
        return {"status": "error", "message": f"Unknown robot_type '{robot_type}'. Use: ur5e, ur10e, ur3e, franka, custom."}

    if not await _pkg_exists(pkg):
        return {
            "status":  "error",
            "message": (
                f"Package '{pkg}' not found.\n"
                f"  cd ~/ros2_ws && colcon build --packages-up-to {pkg}\n"
                f"  source install/setup.bash"
            ),
        }

    cmd = ["ros2", "launch", pkg, launch_file] + extra_args

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "cumotion_moveit", "robot": robot_type,
    }

    return {
        "status":      "launched",
        "key":         key,
        "pid":         proc.pid,
        "robot_type":  robot_type,
        "rviz_guide": (
            "In RViz: MotionPlanning → Context → Planning Library → 'isaac_ros_cumotion' → Planner → 'cuMotion'. "
            "Set a goal pose with interactive markers, click Plan then Execute."
        ),
        "message": (
            f"MoveIt 2 + cuMotion launched for {robot_type} (PID {proc.pid}). "
            f"Open RViz, select 'cuMotion' planner, and use the MotionPlanning panel to plan."
        ),
    }


# ---------------------------------------------------------------------------
# Goal Setter
# ---------------------------------------------------------------------------

async def handle_launch_goal_setter(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch GoalSetterNode — service-based end-effector goal interface for MoveIt 2."""
    planner_group = args.get("planner_group_name", "ur_manipulator")
    ee_link       = args.get("end_effector_link",  "wrist_3_link")
    robot_type    = args.get("robot_type",         "ur5e")

    key = "cumotion/goal_setter"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_moveit_goal_setter"):
        return {"status": "error", "message": "isaac_ros_moveit_goal_setter not found."}

    scene_dir    = await _get_scene_dir()
    cumotion_dir = scene_dir / "cumotion"
    cumotion_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = cumotion_dir / "goal_setter_params.yaml"
    params_yaml.write_text(
        _GOAL_SETTER_PARAMS.format(
            planner_group=planner_group,
            end_effector_link=ee_link,
        )
    )

    cmd = [
        "ros2", "launch", "isaac_ros_moveit_goal_setter",
        "isaac_ros_goal_setter.launch.py",
        f"ur_type:={robot_type}",
        f"params_file:={params_yaml}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "goal_setter",
    }

    return {
        "status": "launched",
        "key":    key,
        "pid":    proc.pid,
        "services": {
            "set_target_pose": "/set_target_pose (isaac_ros_goal_setter_interfaces/SetTargetPose)",
        },
        "message": f"GoalSetter launched (PID {proc.pid}). Call set_cumotion_target_pose to send goals.",
    }


async def handle_set_cumotion_target_pose(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set cuMotion end-effector target pose via /set_target_pose service.
    GoalSetterNode or launch_goal_setter must be running.
    """
    x  = float(args.get("x",  0.0))
    y  = float(args.get("y",  0.0))
    z  = float(args.get("z",  0.5))
    qx = float(args.get("qx", 0.0))
    qy = float(args.get("qy", 0.0))
    qz = float(args.get("qz", 0.0))
    qw = float(args.get("qw", 1.0))
    frame = args.get("frame_id", "base_link")

    request = (
        f"{{target_pose: {{"
        f"header: {{frame_id: '{frame}'}}, "
        f"pose: {{"
        f"position: {{x: {x}, y: {y}, z: {z}}}, "
        f"orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}"
        f"}}}}}}"
    )

    proc = await asyncio.create_subprocess_exec(
        "ros2", "service", "call",
        "/set_target_pose",
        "isaac_ros_goal_setter_interfaces/srv/SetTargetPose",
        request,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        return {"status": "timeout", "message": "set_target_pose service call timed out"}

    if proc.returncode != 0:
        return {"status": "error", "stderr": stderr.decode()[-500:]}

    return {
        "status":   "sent",
        "position": {"x": x, "y": y, "z": z},
        "orientation": {"qx": qx, "qy": qy, "qz": qz, "qw": qw},
        "frame_id": frame,
        "response": stdout.decode().strip()[-500:],
        "message":  f"Target pose sent: ({x:.3f}, {y:.3f}, {z:.3f}) in {frame!r}.",
    }


# ---------------------------------------------------------------------------
# Object Attachment
# ---------------------------------------------------------------------------

async def handle_launch_object_attachment(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch cumotion_bringup for collision-aware grasped-object attachment.
    Clusters point cloud near gripper, fits collision spheres, and incorporates
    them into cuMotion planning to avoid collisions while holding an object.
    """
    robot_xrdf      = args.get("robot_xrdf", "ur5e_robotiq_2f_140.xrdf")
    urdf_path       = args.get("urdf_path", "")
    depth_topic     = args.get("depth_topic",     "/cumotion/camera_1/world_depth")
    cam_info_topic  = args.get("camera_info_topic", "/camera_1/aligned_depth_to_color/camera_info")
    joint_states    = args.get("joint_states_topic", "/joint_states")
    search_radius   = float(args.get("search_radius", 0.2))
    surface_radius  = float(args.get("surface_sphere_radius", 0.01))
    min_samples     = int(args.get("clustering_hdbscan_min_samples", 20))
    min_cluster     = int(args.get("clustering_hdbscan_min_cluster_size", 30))

    key = "cumotion/object_attachment"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_cumotion_object_attachment"):
        return {
            "status":  "error",
            "message": "isaac_ros_cumotion_object_attachment not found. Build it first.",
        }

    scene_dir    = await _get_scene_dir()
    cumotion_dir = scene_dir / "cumotion"
    cumotion_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = cumotion_dir / "object_attachment_params.yaml"
    params_yaml.write_text(
        _OBJECT_ATTACHMENT_PARAMS.format(
            robot_xrdf=robot_xrdf,
            urdf_path=urdf_path,
            joint_states_topic=joint_states,
            depth_topic=depth_topic,
            camera_info_topic=cam_info_topic,
            search_radius=search_radius,
            surface_sphere_radius=surface_radius,
            hdbscan_min_samples=min_samples,
            hdbscan_min_cluster_size=min_cluster,
        )
    )

    cmd = [
        "ros2", "launch",
        "isaac_ros_cumotion_object_attachment", "cumotion_bringup.launch.py",
        f"params_file:={params_yaml}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "object_attachment",
    }

    return {
        "status":   "launched",
        "key":      key,
        "pid":      proc.pid,
        "actions": {
            "attach":        "/segmenter_attach_object (AttachObject)",
            "planner_attach": "/planner_attach_object (AttachObject)",
            "update_spheres": "UpdateLinkSpheres action",
        },
        "workflow": [
            "1. Robot segmenter must be running (launch_robot_segmenter)",
            "2. cuMotion planner must be running (launch_cumotion_planner)",
            "3. Close gripper on object",
            "4. Call attach_object with attach=true to cluster + attach geometry",
            "5. Plan/execute motions — object collision spheres are included",
            "6. Call attach_object with attach=false to detach after placing",
        ],
        "message": (
            f"Object attachment server launched (PID {proc.pid}). "
            f"Use attach_object action to attach/detach grasped object geometry."
        ),
    }


async def handle_attach_object(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send an AttachObject action goal to attach or detach a grasped object
    to cuMotion's collision geometry.
    """
    attach          = bool(args.get("attach", True))
    fallback_radius = float(args.get("fallback_radius", 0.1))
    action_name     = args.get("action_name", "segmenter_attach_object")

    goal = f"{{attach_object: {'true' if attach else 'false'}, fallback_radius: {fallback_radius}}}"

    proc = await asyncio.create_subprocess_exec(
        "ros2", "action", "send_goal",
        f"/{action_name}",
        "isaac_ros_cumotion_object_attachment/action/AttachObject",
        goal,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return {"status": "timeout"}

    if proc.returncode != 0:
        return {"status": "error", "stderr": stderr.decode()[-500:]}

    return {
        "status":   "attached" if attach else "detached",
        "action":   action_name,
        "response": stdout.decode().strip()[-500:],
        "message":  f"Object {'attached to' if attach else 'detached from'} cuMotion collision geometry.",
    }


# ---------------------------------------------------------------------------
# XRDF generator
# ---------------------------------------------------------------------------

async def handle_generate_xrdf(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse a URDF file and generate an XRDF skeleton for cuMotion.
    Extracts non-fixed joints, link names, and joint limits.
    The generated file has placeholder sphere geometry — review and tune radii
    to match actual link shapes before using in production.
    """
    urdf_path   = args.get("urdf_path", "")
    robot_name  = args.get("robot_name", "")
    tool_frame  = args.get("tool_frame", "tool0")
    joint_names = args.get("joint_names", [])  # override auto-detection

    if not urdf_path or not Path(urdf_path).exists():
        return {
            "status":  "error",
            "message": f"URDF file not found: {urdf_path!r}. Provide a valid urdf_path.",
        }

    try:
        tree = ET.parse(urdf_path)
        root = tree.getroot()
    except ET.ParseError as e:
        return {"status": "error", "message": f"Failed to parse URDF: {e}"}

    if not robot_name:
        robot_name = root.get("name", "robot")

    # Extract non-fixed, non-mimic joints
    all_joints: List[Dict] = []
    for joint in root.findall("joint"):
        jtype = joint.get("type", "fixed")
        if jtype in ("fixed", "floating"):
            continue
        if joint.find("mimic") is not None:
            continue
        jname = joint.get("name", "")
        if not jname:
            continue
        # Get limits
        limit_el = joint.find("limit")
        effort = float(limit_el.get("effort", "100")) if limit_el is not None else 100.0
        all_joints.append({
            "name":   jname,
            "type":   jtype,
            "effort": effort,
        })

    if joint_names:
        # Filter to user-specified joints in order
        name_set    = set(joint_names)
        all_joints  = [j for j in all_joints if j["name"] in name_set]
        all_joints.sort(key=lambda j: joint_names.index(j["name"]))

    if not all_joints:
        return {
            "status":  "error",
            "message": "No non-fixed joints found in URDF. Check the file or provide joint_names.",
        }

    # Extract link names (preserve order for sphere generation)
    links = [link.get("name") for link in root.findall("link") if link.get("name")]

    scene_dir    = await _get_scene_dir()
    robot_dir    = scene_dir / "robot"
    robot_dir.mkdir(parents=True, exist_ok=True)

    xrdf_path = robot_dir / f"{robot_name}.xrdf"
    xrdf_path.write_text(_build_xrdf(robot_name, all_joints, links, tool_frame))

    return {
        "status":       "generated",
        "xrdf_path":    str(xrdf_path),
        "robot_name":   robot_name,
        "joint_count":  len(all_joints),
        "joint_names":  [j["name"] for j in all_joints],
        "link_count":   len(links),
        "warnings": [
            "Sphere geometry uses placeholder radius=0.05 — tune to actual link shapes.",
            "Acceleration/jerk limits default to 10.0/500.0 — check robot spec sheet.",
            "Self-collision ignore list only covers adjacent links — add more pairs.",
            "Default joint positions are all 0.0 — ensure this is collision-free.",
        ],
        "next_steps": [
            f"Review and edit: {xrdf_path}",
            f"Launch planner: launch_cumotion_planner robot_xrdf='{xrdf_path}' urdf_path='{urdf_path}'",
            "Or use Isaac Sim Robot Description Editor (4.0+) for guided XRDF generation.",
        ],
        "message": (
            f"XRDF skeleton generated at {xrdf_path} with {len(all_joints)} joints. "
            f"Review sphere radii before use."
        ),
    }
