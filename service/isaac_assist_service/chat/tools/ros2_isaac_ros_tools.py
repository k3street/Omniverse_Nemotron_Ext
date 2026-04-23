"""Isaac ROS perception stack launchers: object detection, pose estimation, nvblox 3D reconstruction."""
from __future__ import annotations

import asyncio
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Dict

from . import kit_tools
from .ros2_autonomy_tools import _LAUNCHED_PROCESSES

_WORKSPACE = Path(__file__).resolve().parents[5] / "workspace"


# ---------------------------------------------------------------------------
# Scene-dir helper
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
    """Return True if a ROS2 package is installed/findable."""
    proc = await asyncio.create_subprocess_exec(
        "ros2", "pkg", "prefix", pkg,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Param YAML templates
# ---------------------------------------------------------------------------

_RTDETR_PARAMS = """\
# RT-DETR Object Detection Parameters (Isaac ROS)
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_object_detection/index.html
rtdetr_decoder_node:
  ros__parameters:
    confidence_threshold: {confidence_threshold}
    nms_threshold: 0.5
    num_classes: 90
    input_binding_names: ["images"]
    output_binding_names: ["labels", "boxes", "scores"]

dnn_image_encoder_node:
  ros__parameters:
    network_image_width: 640
    network_image_height: 640
    encoding_desired: "rgb8"
    maintain_aspect_ratio: false
    center_crop: false
"""

_YOLOV8_PARAMS = """\
# YOLOv8 Object Detection Parameters (Isaac ROS)
yolov8_decoder_node:
  ros__parameters:
    confidence_threshold: {confidence_threshold}
    nms_threshold: 0.45
    num_classes: 80

dnn_image_encoder_node:
  ros__parameters:
    network_image_width: 640
    network_image_height: 640
    encoding_desired: "rgb8"
    maintain_aspect_ratio: false
"""

_FOUNDATIONPOSE_PARAMS = """\
# FoundationPose 6-DoF Pose Estimation Parameters (Isaac ROS)
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_pose_estimation/isaac_ros_foundationpose/index.html
foundationpose_node:
  ros__parameters:
    mesh_file_path: "{mesh_file_path}"
    texture_path: "{texture_path}"
    refine_iterations: 1
    score_threshold: {score_threshold}
    symmetry_planes: []

# Segmentation mask source: run isaac_ros_segment_anything alongside this node
# and remap /segmentation -> /segmentation_image
"""

_DOPE_PARAMS = """\
# DOPE Pose Estimation Parameters (Isaac ROS)
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_pose_estimation/isaac_ros_dope/index.html
dope_decoder_node:
  ros__parameters:
    object_name: "{object_name}"
    map_peak_threshold: {score_threshold}
    # Provide pre-trained .onnx weights converted to TensorRT .plan
    model_file_path: "{model_file_path}"
    input_binding_names: ["input"]
    output_binding_names: ["belief_maps"]

dnn_image_encoder_node:
  ros__parameters:
    network_image_width: 640
    network_image_height: 480
    encoding_desired: "rgb8"
"""

_CENTERPOSE_PARAMS = """\
# CenterPose Pose Estimation Parameters (Isaac ROS)
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_pose_estimation/isaac_ros_centerpose/index.html
centerpose_decoder_node:
  ros__parameters:
    object_name: "{object_name}"
    score_threshold: {score_threshold}
    num_keypoints: 8
    input_binding_names: ["input"]
    output_binding_names: ["hm", "wh", "hps", "reg", "hm_hp", "hp_offset"]

dnn_image_encoder_node:
  ros__parameters:
    network_image_width: 512
    network_image_height: 512
    encoding_desired: "rgb8"
"""

_NVBLOX_PARAMS = """\
# Nvblox 3D Reconstruction Parameters (Isaac ROS)
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_nvblox/index.html
nvblox_node:
  ros__parameters:
    # Voxel resolution (metres)
    voxel_size: {voxel_size}

    # Integration distances
    max_integration_distance_m: {max_integration_distance}
    truncation_distance_vox: 4.0
    lidar_max_integration_distance_m: 10.0

    # Mesh output
    mesh_integrator_min_weight: 1.0
    mesh_update_rate_hz: 5.0

    # ESDF (Euclidean Signed Distance Field) — used by Nav2 costmap
    esdf_update_rate_hz: 2.0
    esdf_2d: true
    esdf_distance_slice: true
    esdf_slice_height: 0.3

    # Mapping mode: "static" | "people_segmentation" | "dynamic"
    mapping_type: "{mapping_type}"

    # Input remapping is done at launch; record configured topics here for reference
    depth_topic: "{depth_topic}"
    color_topic: "{color_topic}"
    camera_info_topic: "{camera_info_topic}"
    pose_frame: "odom"
"""


# ---------------------------------------------------------------------------
# Object Detection launcher
# ---------------------------------------------------------------------------

async def handle_launch_object_detection(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch Isaac ROS RT-DETR or YOLOv8 object detection pipeline."""
    model             = args.get("model", "rtdetr")          # rtdetr | yolov8
    image_topic       = args.get("image_topic", "/image_rect")
    camera_info_topic = args.get("camera_info_topic", "/camera_info")
    output_topic      = args.get("output_topic", "/detections")
    confidence        = float(args.get("confidence_threshold", 0.5))
    engine_file       = args.get("engine_file_path", "")     # pre-built TensorRT .plan

    key = f"object_detection/{model}"
    if key in _LAUNCHED_PROCESSES:
        proc = _LAUNCHED_PROCESSES[key]
        if proc["process"].returncode is None:
            return {"status": "already_running", "key": key}

    scene_dir = await _get_scene_dir()
    od_dir    = scene_dir / "object_detection"
    od_dir.mkdir(parents=True, exist_ok=True)

    # ── Select package / launch file ─────────────────────────────────────────
    if model == "rtdetr":
        pkg         = "isaac_ros_rtdetr"
        launch_file = "isaac_ros_rtdetr_isaac_sim.launch.py"
        params_yaml = od_dir / "rtdetr_params.yaml"
        params_yaml.write_text(
            _RTDETR_PARAMS.format(confidence_threshold=confidence)
        )
    elif model == "yolov8":
        pkg         = "isaac_ros_yolov8"
        launch_file = "isaac_ros_yolov8_isaac_sim.launch.py"
        params_yaml = od_dir / "yolov8_params.yaml"
        params_yaml.write_text(
            _YOLOV8_PARAMS.format(confidence_threshold=confidence)
        )
    else:
        return {"status": "error", "message": f"Unknown model '{model}'. Choose 'rtdetr' or 'yolov8'."}

    if not await _pkg_exists(pkg):
        return {
            "status":  "error",
            "message": (
                f"Package '{pkg}' not found. Build Isaac ROS object detection:\n"
                f"  cd ~/ros2_ws && colcon build --packages-up-to {pkg}\n"
                f"  source install/setup.bash"
            ),
        }

    # ── Build launch command ──────────────────────────────────────────────────
    cmd = [
        "ros2", "launch", pkg, launch_file,
        f"image_topic:={image_topic}",
        f"camera_info_topic:={camera_info_topic}",
        f"detections_topic:={output_topic}",
        f"params_file:={params_yaml}",
    ]
    if engine_file:
        cmd.append(f"engine_file_path:={engine_file}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc,
        "pid":     proc.pid,
        "cmd":     " ".join(cmd),
        "type":    "object_detection",
        "model":   model,
    }

    return {
        "status":       "launched",
        "key":          key,
        "pid":          proc.pid,
        "model":        model,
        "image_topic":  image_topic,
        "output_topic": output_topic,
        "params_file":  str(params_yaml),
        "message": (
            f"Isaac ROS {model.upper()} detection launched (PID {proc.pid}). "
            f"Subscribes to {image_topic!r}, publishes Detection2DArray to {output_topic!r}. "
            f"Params saved to {params_yaml}."
        ),
    }


# ---------------------------------------------------------------------------
# Pose Estimation launcher
# ---------------------------------------------------------------------------

async def handle_launch_pose_estimation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch Isaac ROS FoundationPose, DOPE, or CenterPose for 6-DoF object pose."""
    model             = args.get("model", "foundationpose")   # foundationpose | dope | centerpose
    image_topic       = args.get("image_topic", "/image_rect")
    depth_topic       = args.get("depth_topic", "/depth")
    camera_info_topic = args.get("camera_info_topic", "/camera_info")
    output_topic      = args.get("output_topic", "/pose_estimation/output")
    score_threshold   = float(args.get("score_threshold", 0.5))

    # FoundationPose-specific
    mesh_file    = args.get("mesh_file_path", "")
    texture_path = args.get("texture_path", "")

    # DOPE / CenterPose
    object_name      = args.get("object_name", "object")
    model_file_path  = args.get("model_file_path", "")

    key = f"pose_estimation/{model}"
    if key in _LAUNCHED_PROCESSES:
        proc = _LAUNCHED_PROCESSES[key]
        if proc["process"].returncode is None:
            return {"status": "already_running", "key": key}

    scene_dir = await _get_scene_dir()
    pe_dir    = scene_dir / "pose_estimation"
    pe_dir.mkdir(parents=True, exist_ok=True)

    # ── Select package / launch file ─────────────────────────────────────────
    if model == "foundationpose":
        pkg         = "isaac_ros_foundationpose"
        launch_file = "isaac_ros_foundationpose.launch.py"
        params_yaml = pe_dir / "foundationpose_params.yaml"
        params_yaml.write_text(
            _FOUNDATIONPOSE_PARAMS.format(
                mesh_file_path=mesh_file,
                texture_path=texture_path,
                score_threshold=score_threshold,
            )
        )
        extra_args = []
        if mesh_file:
            extra_args.append(f"mesh_file_path:={mesh_file}")
        if texture_path:
            extra_args.append(f"texture_path:={texture_path}")

    elif model == "dope":
        pkg         = "isaac_ros_dope"
        launch_file = "isaac_ros_dope_isaac_sim.launch.py"
        params_yaml = pe_dir / "dope_params.yaml"
        params_yaml.write_text(
            _DOPE_PARAMS.format(
                object_name=object_name,
                score_threshold=score_threshold,
                model_file_path=model_file_path,
            )
        )
        extra_args = [f"object_name:={object_name}"]
        if model_file_path:
            extra_args.append(f"model_file_path:={model_file_path}")

    elif model == "centerpose":
        pkg         = "isaac_ros_centerpose"
        launch_file = "isaac_ros_centerpose.launch.py"
        params_yaml = pe_dir / "centerpose_params.yaml"
        params_yaml.write_text(
            _CENTERPOSE_PARAMS.format(
                object_name=object_name,
                score_threshold=score_threshold,
            )
        )
        extra_args = [f"object_name:={object_name}"]
        if model_file_path:
            extra_args.append(f"model_file_path:={model_file_path}")

    else:
        return {
            "status":  "error",
            "message": f"Unknown model '{model}'. Choose 'foundationpose', 'dope', or 'centerpose'.",
        }

    if not await _pkg_exists(pkg):
        return {
            "status":  "error",
            "message": (
                f"Package '{pkg}' not found. Build Isaac ROS pose estimation:\n"
                f"  cd ~/ros2_ws && colcon build --packages-up-to {pkg}\n"
                f"  source install/setup.bash\n"
                f"See: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_pose_estimation/index.html"
            ),
        }

    # FoundationPose requires a segmentation mask — check for it
    if model == "foundationpose" and not mesh_file:
        return {
            "status":  "error",
            "message": (
                "FoundationPose requires a 3D mesh file of the target object. "
                "Provide 'mesh_file_path' pointing to a .obj or .ply file. "
                "Also needs a segmentation mask on /segmentation_image — pair with "
                "launch_object_detection or isaac_ros_segment_anything."
            ),
        }

    cmd = [
        "ros2", "launch", pkg, launch_file,
        f"image_topic:={image_topic}",
        f"depth_topic:={depth_topic}",
        f"camera_info_topic:={camera_info_topic}",
        f"output_topic:={output_topic}",
        f"params_file:={params_yaml}",
    ] + extra_args

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc,
        "pid":     proc.pid,
        "cmd":     " ".join(cmd),
        "type":    "pose_estimation",
        "model":   model,
    }

    return {
        "status":         "launched",
        "key":            key,
        "pid":            proc.pid,
        "model":          model,
        "image_topic":    image_topic,
        "depth_topic":    depth_topic,
        "output_topic":   output_topic,
        "params_file":    str(params_yaml),
        "message": (
            f"Isaac ROS {model} pose estimation launched (PID {proc.pid}). "
            f"Publishes Detection3DArray / PoseArray to {output_topic!r}. "
            f"Params saved to {params_yaml}."
        ),
    }


# ---------------------------------------------------------------------------
# Nvblox 3D reconstruction launcher
# ---------------------------------------------------------------------------

async def handle_launch_nvblox(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch Isaac ROS Nvblox for dense 3D reconstruction + ESDF Nav2 costmap layer."""
    image_topic           = args.get("image_topic",           "/front_stereo_camera/left/image_rect_color")
    depth_topic           = args.get("depth_topic",           "/front_stereo_camera/left/depth")
    camera_info_topic     = args.get("camera_info_topic",     "/front_stereo_camera/left/camera_info")
    odom_topic            = args.get("odom_topic",            "visual_slam/tracking/odometry")
    voxel_size            = float(args.get("voxel_size",      0.05))
    max_integration_dist  = float(args.get("max_integration_distance", 5.0))
    mapping_type          = args.get("mapping_type",          "static")  # static | people_segmentation | dynamic
    publish_esdf_slice    = bool(args.get("publish_esdf_slice", True))

    key = "nvblox/reconstruction"
    if key in _LAUNCHED_PROCESSES:
        proc = _LAUNCHED_PROCESSES[key]
        if proc["process"].returncode is None:
            return {"status": "already_running", "key": key}

    pkg = "nvblox_ros"
    if not await _pkg_exists(pkg):
        # Try alternate package name
        if await _pkg_exists("isaac_ros_nvblox"):
            pkg = "isaac_ros_nvblox"
        else:
            return {
                "status":  "error",
                "message": (
                    "nvblox_ros package not found. Build Isaac ROS Nvblox:\n"
                    "  cd ~/ros2_ws && colcon build --packages-up-to nvblox_ros\n"
                    "  source install/setup.bash\n"
                    "See: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_nvblox/index.html"
                ),
            }

    scene_dir   = await _get_scene_dir()
    nvblox_dir  = scene_dir / "nvblox"
    nvblox_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = nvblox_dir / "nvblox_params.yaml"
    params_yaml.write_text(
        _NVBLOX_PARAMS.format(
            voxel_size=voxel_size,
            max_integration_distance=max_integration_dist,
            mapping_type=mapping_type,
            depth_topic=depth_topic,
            color_topic=image_topic,
            camera_info_topic=camera_info_topic,
        )
    )

    mesh_output  = str(nvblox_dir / "mesh.ply")
    launch_file  = "nvblox_ros_isaac_sim.launch.py"

    cmd = [
        "ros2", "launch", pkg, launch_file,
        f"depth_topic:={depth_topic}",
        f"color_topic:={image_topic}",
        f"camera_info_topic:={camera_info_topic}",
        f"odom_topic:={odom_topic}",
        f"voxel_size:={voxel_size}",
        f"params_file:={params_yaml}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc,
        "pid":     proc.pid,
        "cmd":     " ".join(cmd),
        "type":    "nvblox",
    }

    return {
        "status":               "launched",
        "key":                  key,
        "pid":                  proc.pid,
        "voxel_size":           voxel_size,
        "mapping_type":         mapping_type,
        "depth_topic":          depth_topic,
        "color_topic":          image_topic,
        "odom_topic":           odom_topic,
        "params_file":          str(params_yaml),
        "published_topics": {
            "mesh":              "/nvblox_node/mesh",
            "esdf_slice":        "/nvblox_node/static_esdf_pointcloud",
            "occupancy_layer":   "/nvblox_node/static_map_slice",
        },
        "nav2_costmap_hint": (
            "Add nvblox_costmap_plugin to Nav2 costmap_2d local/global costmap "
            "plugins list and set topic to /nvblox_node/static_map_slice."
        ),
        "message": (
            f"Nvblox 3D reconstruction launched (PID {proc.pid}). "
            f"Voxel size: {voxel_size}m, mapping: {mapping_type}. "
            f"ESDF slice on /nvblox_node/static_esdf_pointcloud for Nav2 costmap. "
            f"Mesh on /nvblox_node/mesh. Params saved to {params_yaml}."
        ),
    }

# ── Isaac ROS Image Pipeline ────────────────────────────────────────────

async def handle_launch_isaac_ros_image_pipeline(**kwargs: Any) -> Dict[str, Any]:
    node_type = kwargs.get("node_type", "rectify")
    camera_name = kwargs.get("camera_name", "camera")
    input_topic = kwargs.get("input_topic", f"/{camera_name}/image_raw")
    camera_info_topic = kwargs.get("camera_info_topic", f"/{camera_name}/camera_info")
    
    scene_dir = await _get_scene_dir()
    image_pipeline_dir = scene_dir / "image_pipeline"
    image_pipeline_dir.mkdir(parents=True, exist_ok=True)
    
    launch_path = image_pipeline_dir / f"launch_{node_type}.py"
    
    components = {
        "rectify": "isaac_ros_image_proc::RectifyNode",
        "resize": "isaac_ros_image_proc::ResizeNode",
        "crop": "isaac_ros_image_proc::CropNode",
        "format_converter": "isaac_ros_image_proc::ImageFormatConverterNode",
        "stereo": "isaac_ros_stereo_image_proc::DisparityNode"
    }
    
    pkg = "isaac_ros_stereo_image_proc" if node_type == "stereo" else "isaac_ros_image_proc"
    component = components.get(node_type, components["rectify"])
    
    launch_code = textwrap.dedent(f'''\
        import launch
        from launch_ros.actions import ComposableNodeContainer
        from launch_ros.descriptions import ComposableNode

        def generate_launch_description():
            container = ComposableNodeContainer(
                name='image_pipeline_container',
                namespace='{camera_name}',
                package='rclcpp_components',
                executable='component_container_mt',
                composable_node_descriptions=[
                    ComposableNode(
                        package='{pkg}',
                        plugin='{component}',
                        name='{node_type}_node',
                        parameters=[{{
                            'output_width': 640,
                            'output_height': 480,
                            'encoding_desired': 'rgb8',
                        }}],
                        remappings=[
                            ('image', '{input_topic}'),
                            ('camera_info', '{camera_info_topic}'),
                            ('image_rect', '{input_topic.replace("raw", "rect")}'),
                            ('image_resized', '{input_topic.replace("raw", "resized")}'),
                            ('disparity', '/{camera_name}/disparity')
                        ]
                    )
                ],
                output='screen'
            )
            return launch.LaunchDescription([container])
    ''')
    
    launch_path.write_text(launch_code)
    
    return {
        "type": "code_patch",
        "description": f"Generated Isaac ROS hardware-accelerated {node_type} launch file at {launch_path}. Run with: ros2 launch {launch_path}",
        "code": launch_code,
        "launch_file": str(launch_path)
    }
