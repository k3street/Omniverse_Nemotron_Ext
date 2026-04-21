"""
Isaac ROS Mapping & Localization stack.

Covers:
  - OccupancyGridLocalizer  (2D lidar global localization in a pre-built pgm map)
  - VisualGlobalLocalization (camera-based global localization via cuVGL)
  - PointCloud utilities     (PointCloud2 / LaserScan ↔ FlatScan converters)
  - Visual map builder       (rosbag_to_mapping_data + offline SfM pipeline)
  - cuVSLAM map services     (load_map, localize_in_map, reset, get_all_poses)

Docs:
  https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_mapping_and_localization/index.html
  https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_visual_slam/index.html
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
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


async def _call_service(service: str, srv_type: str, request: str = "{}",
                        timeout: int = 10) -> Dict[str, Any]:
    """Call a ROS2 service and return parsed response."""
    proc = await asyncio.create_subprocess_exec(
        "ros2", "service", "call", service, srv_type, request,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"status": "timeout", "service": service}
    out = stdout.decode().strip()
    err = stderr.decode().strip()
    if proc.returncode != 0:
        return {"status": "error", "service": service, "stderr": err}
    return {"status": "ok", "service": service, "response": out}


def _find_latest_map_yaml(scene_dir: Path) -> Optional[str]:
    """Return the most recently modified .yaml in scene_dir/maps/."""
    maps_dir = scene_dir / "maps"
    if not maps_dir.exists():
        return None
    yamls = sorted(maps_dir.glob("**/*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(yamls[0]) if yamls else None


# ---------------------------------------------------------------------------
# Param YAML templates
# ---------------------------------------------------------------------------

_OCC_GRID_LOC_PARAMS = """\
# OccupancyGridLocalizer Parameters
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_mapping_and_localization/isaac_ros_occupancy_grid_localizer/index.html
occupancy_grid_localizer_node:
  ros__parameters:
    loc_result_frame: "{loc_result_frame}"
    map_yaml_path: "{map_yaml_path}"
    resolution: {resolution}
    origin: [{origin_x}, {origin_y}, {origin_z}]
    occupied_thresh: {occupied_thresh}
    max_points: {max_points}
    robot_radius: {robot_radius}
    min_output_error: 0.22
    max_output_error: 0.35
    max_beam_error: 0.5
    num_beams_gpu: 512
    batch_size: 512
    sample_distance: 0.1
    out_of_range_threshold: 100.0
    invalid_range_threshold: 0.0
    min_scan_fov_degrees: {min_scan_fov_degrees}
    use_closest_beam: true
"""

_VGL_PARAMS = """\
# Isaac ROS Visual Global Localization Parameters
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_mapping_and_localization/isaac_ros_visual_global_localization/index.html
isaac_ros_visual_global_localization:
  ros__parameters:
    num_cameras: {num_cameras}
    stereo_localizer_cam_ids: "{stereo_cam_ids}"
    image_sync_match_threshold_ms: 5.0
    enable_rectify_images: {enable_rectify}
    enable_continuous_localization: {enable_continuous}
    use_initial_guess: false
    map_dir: "{map_dir}"
    config_dir: "{config_dir}"
    localization_precision_level: {precision_level}
    map_frame: "{map_frame}"
    base_frame: "{base_frame}"
    publish_map_to_base_tf: true
    invert_map_to_base_tf: false
    image_buffer_size: 100
    image_qos_profile: "DEFAULT"
    verbose_logging: false
"""


# ---------------------------------------------------------------------------
# OccupancyGridLocalizer
# ---------------------------------------------------------------------------

async def handle_launch_occupancy_grid_localizer(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch isaac_ros_occupancy_grid_localizer for 2D global localization in a pre-built pgm map.

    Subscribes to /flatscan (FlatScan). Use launch_pointcloud_to_flatscan or
    launch_laserscan_to_flatscan to bridge lidar data.
    Publishes localization result to /localization_result (PoseWithCovarianceStamped).
    Call trigger_grid_search_localization to initiate a localization attempt.
    """
    map_yaml_path = args.get("map_yaml_path", "")

    if not map_yaml_path:
        scene_dir = await _get_scene_dir()
        map_yaml_path = _find_latest_map_yaml(scene_dir) or ""

    if not map_yaml_path or not Path(map_yaml_path).exists():
        return {
            "status": "error",
            "message": (
                "No map YAML found. Run slam_stop or map_export first to generate a map, "
                "or provide 'map_yaml_path' pointing to an existing .yaml file."
            ),
        }

    key = "localization/occupancy_grid"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_occupancy_grid_localizer"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_occupancy_grid_localizer not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_occupancy_grid_localizer\n"
                "  source install/setup.bash"
            ),
        }

    scene_dir = await _get_scene_dir()
    loc_dir   = scene_dir / "localization"
    loc_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = loc_dir / "occ_grid_localizer_params.yaml"
    params_yaml.write_text(
        _OCC_GRID_LOC_PARAMS.format(
            loc_result_frame=args.get("loc_result_frame", "map"),
            map_yaml_path=map_yaml_path,
            resolution=float(args.get("resolution", 0.05)),
            origin_x=0.0, origin_y=0.0, origin_z=0.0,
            occupied_thresh=float(args.get("occupied_thresh", 0.65)),
            max_points=int(args.get("max_points", 20000)),
            robot_radius=float(args.get("robot_radius", 0.25)),
            min_scan_fov_degrees=float(args.get("min_scan_fov_degrees", 270.0)),
        )
    )

    # Choose launch file — nav2 variant wires localization_result into Nav2 AMCL slot
    use_nav2 = bool(args.get("use_nav2_integration", False))
    launch_file = (
        "isaac_ros_occupancy_grid_localizer_nav2.launch.py"
        if use_nav2
        else "isaac_ros_occupancy_grid_localizer.launch.py"
    )

    cmd = [
        "ros2", "launch",
        "isaac_ros_occupancy_grid_localizer", launch_file,
        f"params_file:={params_yaml}",
        f"map_yaml_path:={map_yaml_path}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "occupancy_grid_localizer",
    }

    return {
        "status":           "launched",
        "key":              key,
        "pid":              proc.pid,
        "map_yaml_path":    map_yaml_path,
        "params_file":      str(params_yaml),
        "subscribed_topics": {
            "flatscan":              "/flatscan (FlatScan — pipe lidar here via launch_pointcloud_to_flatscan)",
            "flatscan_localization": "/flatscan_localization (FlatScan — triggers localization immediately)",
        },
        "published_topics": {
            "localization_result": "/localization_result (PoseWithCovarianceStamped)",
        },
        "next_steps": [
            "Run launch_pointcloud_to_flatscan to bridge PointCloud2 → /flatscan",
            "Call trigger_grid_search_localization to start a localization sweep",
        ],
        "message": (
            f"OccupancyGridLocalizer launched (PID {proc.pid}) with map {map_yaml_path}. "
            f"Pipe lidar to /flatscan, then call trigger_grid_search_localization."
        ),
    }


async def handle_trigger_grid_search_localization(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger a global grid-search localization using the buffered flatscan."""
    return await _call_service(
        "/trigger_grid_search_localization",
        "std_srvs/srv/Empty",
        "{}",
        timeout=30,
    )


# ---------------------------------------------------------------------------
# PointCloud utilities
# ---------------------------------------------------------------------------

async def handle_launch_pointcloud_to_flatscan(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch isaac_ros_pointcloud_to_flatscan to convert PointCloud2 → FlatScan.
    Required as input to OccupancyGridLocalizer.
    """
    input_topic  = args.get("input_topic",  "/point_cloud")
    output_topic = args.get("output_topic", "/flatscan")
    min_z        = float(args.get("min_z", -0.1))
    max_z        = float(args.get("max_z",  0.1))

    key = "pointcloud_utils/pc_to_flatscan"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_pointcloud_utils"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_pointcloud_utils not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_pointcloud_utils\n"
                "  source install/setup.bash"
            ),
        }

    cmd = [
        "ros2", "launch",
        "isaac_ros_pointcloud_utils", "isaac_ros_pointcloud_to_flatscan.launch.py",
        f"input_topic:={input_topic}",
        f"output_topic:={output_topic}",
        f"min_z:={min_z}",
        f"max_z:={max_z}",
        f"threshold_z_axis:=true",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "pointcloud_to_flatscan",
    }

    return {
        "status":        "launched",
        "key":           key,
        "pid":           proc.pid,
        "input_topic":   input_topic,
        "output_topic":  output_topic,
        "z_range":       [min_z, max_z],
        "message":       f"PointCloud→FlatScan converter launched (PID {proc.pid}). {input_topic} → {output_topic}.",
    }


async def handle_launch_laserscan_to_flatscan(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch isaac_ros_laserscan_to_flatscan to convert LaserScan → FlatScan."""
    input_topic  = args.get("input_topic",  "/scan")
    output_topic = args.get("output_topic", "/flatscan")

    key = "pointcloud_utils/scan_to_flatscan"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_pointcloud_utils"):
        return {"status": "error", "message": "isaac_ros_pointcloud_utils not found."}

    cmd = [
        "ros2", "launch",
        "isaac_ros_pointcloud_utils", "isaac_ros_laserscan_to_flatscan.launch.py",
        f"input_topic:={input_topic}",
        f"output_topic:={output_topic}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "laserscan_to_flatscan",
    }
    return {
        "status": "launched", "key": key, "pid": proc.pid,
        "input_topic": input_topic, "output_topic": output_topic,
        "message": f"LaserScan→FlatScan converter launched (PID {proc.pid}). {input_topic} → {output_topic}.",
    }


# ---------------------------------------------------------------------------
# Visual Global Localization (cuVGL)
# ---------------------------------------------------------------------------

async def handle_launch_visual_global_localization(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch isaac_ros_visual_global_localization for camera-based global localization
    using a pre-built cuVGL map (built offline via handle_build_visual_map).

    Subscribes to visual_localization/image_0, visual_localization/image_1 and
    corresponding camera_info topics.
    Publishes visual_localization/pose (PoseWithCovarianceStamped).
    """
    map_dir    = args.get("map_dir", "")
    config_dir = args.get("config_dir", "")
    model_dir  = args.get("model_dir", "")

    if not map_dir:
        scene_dir = await _get_scene_dir()
        vgl_dir = scene_dir / "visual_global_localization" / "map"
        if vgl_dir.exists():
            map_dir = str(vgl_dir)

    if not map_dir or not Path(map_dir).exists():
        return {
            "status": "error",
            "message": (
                "No cuVGL map directory found. Build a visual map first with build_visual_map, "
                "or provide 'map_dir' pointing to an existing cuVGL map folder."
            ),
        }

    key = "localization/visual_global"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_visual_global_localization"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_visual_global_localization not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_visual_global_localization\n"
                "  source install/setup.bash"
            ),
        }

    left_image   = args.get("left_image_topic",  "front_stereo_camera/left/image_rect_color")
    right_image  = args.get("right_image_topic", "front_stereo_camera/right/image_rect_color")
    base_frame   = args.get("base_frame", "base_link")
    map_frame    = args.get("map_frame",  "vmap")
    precision    = int(args.get("localization_precision_level", 2))
    continuous   = bool(args.get("enable_continuous_localization", True))

    scene_dir = await _get_scene_dir()
    vgl_cfg   = scene_dir / "visual_global_localization"
    vgl_cfg.mkdir(parents=True, exist_ok=True)

    params_yaml = vgl_cfg / "vgl_params.yaml"
    params_yaml.write_text(
        _VGL_PARAMS.format(
            num_cameras=2,
            stereo_cam_ids="0,1",
            enable_rectify="false",
            enable_continuous=str(continuous).lower(),
            map_dir=map_dir,
            config_dir=config_dir or str(vgl_cfg / "config"),
            precision_level=precision,
            map_frame=map_frame,
            base_frame=base_frame,
        )
    )

    cmd = [
        "ros2", "launch",
        "isaac_ros_visual_global_localization",
        "isaac_ros_visual_global_localization_node.launch.py",
        f"params_file:={params_yaml}",
        "--ros-args",
        "-r", f"visual_localization/image_0:={left_image}",
        "-r", f"visual_localization/image_1:={right_image}",
    ]
    if model_dir:
        cmd += ["-p", f"model_dir:={model_dir}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "visual_global_localization",
    }

    return {
        "status":    "launched",
        "key":       key,
        "pid":       proc.pid,
        "map_dir":   map_dir,
        "map_frame": map_frame,
        "subscribed_topics": {
            "left_image":        left_image,
            "right_image":       right_image,
            "trigger":           "visual_localization/trigger_localization (PoseWithCovarianceStamped)",
        },
        "published_topics": {
            "pose":              "visual_localization/pose (PoseWithCovarianceStamped)",
            "debug_image":       "visual_localization/debug_image",
        },
        "params_file": str(params_yaml),
        "message": (
            f"Visual Global Localization launched (PID {proc.pid}). "
            f"Map: {map_dir}. Publishes to visual_localization/pose. "
            f"Call trigger_visual_localization to initiate a localization attempt."
        ),
    }


async def handle_trigger_visual_localization(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger a visual global localization attempt via service call."""
    return await _call_service(
        "/visual_localization/trigger_localization",
        "std_srvs/srv/Trigger",
        "{}",
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Visual map builder  (isaac_mapping_ros)
# ---------------------------------------------------------------------------

async def handle_build_visual_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract images + poses from a ROS2 bag file using rosbag_to_mapping_data
    (first step of the cuVGL map-building pipeline).

    After this completes, run offline:
      1. feature_extractor_main       (extract visual features)
      2. generate_bow_vocabulary_main  (build BoW vocabulary)
      3. generate_bow_index_main       (build retrieval index)

    Then launch_visual_global_localization with the resulting map_dir.
    """
    bag_file         = args.get("bag_file", "")
    pose_topic       = args.get("pose_topic", "/visual_slam/vis/slam_odometry")
    min_frame_dist   = float(args.get("min_inter_frame_distance", 0.1))
    min_frame_rot    = float(args.get("min_inter_frame_rotation_degrees", 5.0))
    rectify_images   = bool(args.get("rectify_images", False))
    left_image_topic = args.get("left_image_topic", "/front_stereo_camera/left/image_rect_color")
    right_image_topic = args.get("right_image_topic", "/front_stereo_camera/right/image_rect_color")

    if not bag_file or not Path(bag_file).exists():
        return {
            "status": "error",
            "message": (
                "Provide 'bag_file' path to a ROS2 bag containing stereo images and SLAM odometry. "
                "Record a bag with: ros2 bag record "
                f"{left_image_topic} {right_image_topic} {pose_topic}"
            ),
        }

    if not await _pkg_exists("isaac_mapping_ros"):
        return {
            "status": "error",
            "message": (
                "isaac_mapping_ros not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_mapping_ros\n"
                "  source install/setup.bash"
            ),
        }

    scene_dir  = await _get_scene_dir()
    vgl_dir    = scene_dir / "visual_global_localization"
    output_dir = vgl_dir / f"mapping_data_{time.strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cam_config = json.dumps([[
        "left",  left_image_topic,  "", "", "",
    ], [
        "right", right_image_topic, "", "", "",
    ]])

    cmd = [
        "ros2", "run", "isaac_mapping_ros", "rosbag_to_mapping_data",
        "--ros-args",
        "-p", f"sensor_data_bag_file:={bag_file}",
        "-p", f"pose_bag_file:={bag_file}",
        "-p", f"output_folder_path:={output_dir}",
        "-p", f"pose_topic_name:={pose_topic}",
        "-p", f"camera_topic_config:={cam_config}",
        "-p", f"rectify_images:={str(rectify_images).lower()}",
        "-p", f"min_inter_frame_distance:={min_frame_dist}",
        "-p", f"min_inter_frame_rotation_degrees:={min_frame_rot}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return {
            "status":   "error",
            "message":  "rosbag_to_mapping_data failed",
            "stderr":   stderr.decode()[-2000:],
        }

    map_dir = str(vgl_dir / "map")
    return {
        "status":        "extracted",
        "output_dir":    str(output_dir),
        "next_steps": [
            f"# Step 2 — extract features:",
            f"feature_extractor_main --input_dir {output_dir} --output_dir {output_dir}/features",
            f"# Step 3 — build BoW vocabulary:",
            f"generate_bow_vocabulary_main --input_dir {output_dir}/features --output_dir {vgl_dir}/vocab",
            f"# Step 4 — build retrieval index:",
            f"generate_bow_index_main --input_dir {output_dir}/features --vocab_dir {vgl_dir}/vocab --output_dir {map_dir}",
            f"# Step 5 — launch localization:",
            f"launch_visual_global_localization with map_dir='{map_dir}'",
        ],
        "message": (
            f"Bag data extracted to {output_dir}. "
            f"Run the feature_extractor_main and generate_bow_* tools offline to build the cuVGL map, "
            f"then call launch_visual_global_localization."
        ),
    }


# ---------------------------------------------------------------------------
# cuVSLAM map services  (/visual_slam/* ROS2 services)
# ---------------------------------------------------------------------------

async def handle_load_visual_slam_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load a previously saved cuVSLAM map via /visual_slam/load_map service.
    The map must have been saved with save_visual_slam_map (or auto-saved on shutdown
    via save_map_folder_path). After loading, call localize_in_visual_slam_map.
    """
    map_dir = args.get("map_dir", "")

    if not map_dir:
        scene_dir = await _get_scene_dir()
        vs_dir = scene_dir / "visual_slam"
        saved = sorted(vs_dir.glob("map_*/"), key=lambda p: p.stat().st_mtime, reverse=True)
        if saved:
            map_dir = str(saved[0])

    if not map_dir or not Path(map_dir).exists():
        return {
            "status": "error",
            "message": (
                "No saved cuVSLAM map found. Run save_visual_slam_map first, "
                "or provide 'map_dir' explicitly."
            ),
        }

    result = await _call_service(
        "/visual_slam/load_map",
        "isaac_ros_visual_slam_interfaces/srv/FilePath",
        f"{{file_path: '{map_dir}'}}",
        timeout=30,
    )
    result["map_dir"] = map_dir
    if result["status"] == "ok":
        result["message"] = (
            f"Map loaded from {map_dir}. "
            "Call localize_in_visual_slam_map to relocalize in the loaded map."
        )
    return result


async def handle_localize_in_visual_slam_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call /visual_slam/localize_in_map to relocalize using the currently loaded map.
    Optionally provide an initial pose hint to narrow the search.
    """
    # Build pose hint if provided
    x   = float(args.get("x",   0.0))
    y   = float(args.get("y",   0.0))
    z   = float(args.get("z",   0.0))
    qx  = float(args.get("qx",  0.0))
    qy  = float(args.get("qy",  0.0))
    qz  = float(args.get("qz",  0.0))
    qw  = float(args.get("qw",  1.0))

    request = (
        f"{{initial_pose: {{"
        f"pose: {{"
        f"position: {{x: {x}, y: {y}, z: {z}}}, "
        f"orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}"
        f"}}}}}}"
    )

    result = await _call_service(
        "/visual_slam/localize_in_map",
        "isaac_ros_visual_slam_interfaces/srv/LocalizeInMap",
        request,
        timeout=30,
    )
    if result["status"] == "ok":
        result["message"] = (
            "Localization in cuVSLAM map requested. "
            "Monitor visual_slam/tracking/odometry and /tf for updated pose. "
            "Check visual_slam/status for tracking state."
        )
    return result


async def handle_reset_visual_slam(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Reset cuVSLAM state — clears the map and restarts tracking from scratch."""
    result = await _call_service(
        "/visual_slam/reset",
        "isaac_ros_visual_slam_interfaces/srv/Reset",
        "{}",
        timeout=10,
    )
    if result["status"] == "ok":
        result["message"] = "cuVSLAM reset. Tracking will restart on the next stereo frame pair."
    return result


async def handle_get_visual_slam_poses(args: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve all poses in the current cuVSLAM map via /visual_slam/get_all_poses."""
    max_count = int(args.get("max_count", 200))
    result = await _call_service(
        "/visual_slam/get_all_poses",
        "isaac_ros_visual_slam_interfaces/srv/GetAllPoses",
        f"{{max_count: {max_count}}}",
        timeout=15,
    )
    return result


async def handle_set_visual_slam_pose(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Override the current cuVSLAM pose via /visual_slam/set_slam_pose.
    Useful for providing a known starting pose from an external localization source.
    """
    x  = float(args.get("x",  0.0))
    y  = float(args.get("y",  0.0))
    z  = float(args.get("z",  0.0))
    qx = float(args.get("qx", 0.0))
    qy = float(args.get("qy", 0.0))
    qz = float(args.get("qz", 0.0))
    qw = float(args.get("qw", 1.0))

    request = (
        f"{{pose: {{"
        f"position: {{x: {x}, y: {y}, z: {z}}}, "
        f"orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}"
        f"}}}}"
    )
    return await _call_service(
        "/visual_slam/set_slam_pose",
        "isaac_ros_visual_slam_interfaces/srv/SetSlamPose",
        request,
        timeout=10,
    )
