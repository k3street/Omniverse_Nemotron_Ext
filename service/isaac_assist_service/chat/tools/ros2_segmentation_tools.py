"""
Isaac ROS Image Segmentation stack.

Packages:
  - isaac_ros_unet          UNet decoder (TensorRT/Triton) — generic semantic segmentation
  - isaac_ros_segformer     PeopleSemSegFormer — people semantic segmentation
  - isaac_ros_segment_anything    SAM / Mobile SAM — prompt-driven single-frame masks
  - isaac_ros_segment_anything2   SAM2 — video object tracking with add/remove services

Integration map:
  RT-DETR/YOLO → Detection2DArray → /prompts → SAM → segmentation_mask
  segmentation_mask → nvblox (people_segmentation mode)
  segmentation_mask → FoundationPose (object mask input)
  segmentation_mask → cuMotion object attachment

Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_image_segmentation/index.html
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

# Default Triton model repository (user should override with actual path)
_DEFAULT_TRITON_REPO = str(Path.home() / "triton_models")


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
    if proc.returncode != 0:
        return {"status": "error", "service": service, "stderr": stderr.decode()[-500:]}
    return {"status": "ok", "service": service, "response": stdout.decode().strip()[-500:]}


# ---------------------------------------------------------------------------
# Param YAML templates
# ---------------------------------------------------------------------------

_UNET_PARAMS = """\
# Isaac ROS UNet segmentation decoder parameters
unet_decoder_node:
  ros__parameters:
    network_output_type: "{network_output_type}"
    color_segmentation_mask_encoding: "rgb8"
    mask_width: {mask_width}
    mask_height: {mask_height}
    color_palette: {color_palette}
"""

_SAM_PARAMS = """\
# Isaac ROS Segment Anything parameters
segment_anything_data_encoder_node:
  ros__parameters:
    prompt_input_type: "{prompt_input_type}"
    has_input_mask: {has_input_mask}
    max_batch_size: {max_batch_size}
    orig_img_dims: [{img_height}, {img_width}]

segment_anything_decoder_node:
  ros__parameters:
    mask_width: {mask_width}
    mask_height: {mask_height}
    max_batch_size: {max_batch_size}
"""

_SAM2_PARAMS = """\
# Isaac ROS Segment Anything 2 parameters
segment_anything2_data_encoder_node:
  ros__parameters:
    max_num_objects: {max_num_objects}
    orig_img_dims: [{img_height}, {img_width}]
"""

# People segmentation color palette (background, person, bicycle, car, ...)
_PEOPLE_SEG_PALETTE = (
    "[0x000000, 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, "
    "0x00FFFF, 0x800000, 0x008000, 0x000080, 0x808000, 0x800080]"
)


# ---------------------------------------------------------------------------
# UNet segmentation
# ---------------------------------------------------------------------------

async def handle_launch_unet_segmentation(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch the Isaac ROS UNet segmentation pipeline.

    Chains: image → DnnImageEncoder → TensorRT/Triton → UNetDecoder
    Publishes:
      unet/raw_segmentation_mask    (sensor_msgs/Image, mono8 — pixel = class label)
      unet/colored_segmentation_mask (sensor_msgs/Image, rgb8 — false-colour overlay)

    Backends:
      tensorrt — faster, needs pre-built .plan engine file
      triton   — flexible, needs Triton model repository
    """
    backend       = args.get("backend", "triton")           # tensorrt | triton
    model_name    = args.get("model_name", "peoplesemsegnet")
    image_topic   = args.get("image_topic", "/image_rect")
    mask_width    = int(args.get("mask_width",  960))
    mask_height   = int(args.get("mask_height", 544))
    network_output_type = args.get("network_output_type", "softmax")  # softmax|argmax|sigmoid
    color_palette = args.get("color_palette", _PEOPLE_SEG_PALETTE)
    engine_file   = args.get("engine_file_path", "")
    triton_repo   = args.get("model_repository_path", _DEFAULT_TRITON_REPO)

    key = "segmentation/unet"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_unet"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_unet not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_unet\n"
                "  source install/setup.bash\n"
                "See: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_image_segmentation/isaac_ros_unet/index.html"
            ),
        }

    scene_dir = await _get_scene_dir()
    seg_dir   = scene_dir / "segmentation"
    seg_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = seg_dir / "unet_params.yaml"
    params_yaml.write_text(
        _UNET_PARAMS.format(
            network_output_type=network_output_type,
            mask_width=mask_width,
            mask_height=mask_height,
            color_palette=color_palette,
        )
    )

    if backend == "tensorrt":
        launch_file = "isaac_ros_unet_tensor_rt.launch.py"
        extra_args  = [f"engine_file_path:={engine_file}"] if engine_file else []
    else:
        launch_file = "isaac_ros_unet_triton.launch.py"
        extra_args  = [
            f"model_name:={model_name}",
            f"model_repository_paths:=[\"{triton_repo}\"]",
        ]

    cmd = [
        "ros2", "launch", "isaac_ros_unet", launch_file,
        f"image_topic:={image_topic}",
        f"mask_width:={mask_width}",
        f"mask_height:={mask_height}",
        f"params_file:={params_yaml}",
    ] + extra_args

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "unet_segmentation", "model": model_name,
    }

    return {
        "status":           "launched",
        "key":              key,
        "pid":              proc.pid,
        "backend":          backend,
        "model":            model_name,
        "image_topic":      image_topic,
        "published_topics": {
            "raw_mask":     "unet/raw_segmentation_mask (sensor_msgs/Image mono8 — pixel=class_id)",
            "colored_mask": "unet/colored_segmentation_mask (sensor_msgs/Image rgb8)",
        },
        "nvblox_hint": "For people-aware 3D mapping: launch_nvblox with mapping_type='people_segmentation'",
        "params_file":  str(params_yaml),
        "message": (
            f"UNet segmentation launched ({backend}, PID {proc.pid}). "
            f"Raw mask on unet/raw_segmentation_mask, coloured on unet/colored_segmentation_mask."
        ),
    }


# ---------------------------------------------------------------------------
# Segformer (people semantic segmentation)
# ---------------------------------------------------------------------------

async def handle_launch_segformer(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch PeopleSemSegFormer — transformer-based people semantic segmentation.
    Higher accuracy than UNet for people/crowd scenes.

    Published topics (same remapping as UNet):
      /segformer/raw_segmentation_mask
      /segformer/colored_segmentation_mask
    """
    backend     = args.get("backend", "triton")
    image_topic = args.get("image_topic", "/image_rect")
    model_name  = args.get("model_name", "peoplesemsegformer")
    engine_file = args.get("engine_file_path", "")
    triton_repo = args.get("model_repository_path", _DEFAULT_TRITON_REPO)
    interface_specs = args.get("interface_specs_file", "")

    key = "segmentation/segformer"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_segformer"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_segformer not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_segformer\n"
                "  source install/setup.bash"
            ),
        }

    scene_dir = await _get_scene_dir()
    seg_dir   = scene_dir / "segmentation"
    seg_dir.mkdir(parents=True, exist_ok=True)

    if backend == "tensorrt":
        launch_file = "isaac_ros_people_sem_segformer_tensor_rt.launch.py"
        extra_args  = [f"engine_file_path:={engine_file}"] if engine_file else []
    else:
        launch_file = "isaac_ros_people_sem_segformer_triton.launch.py"
        extra_args  = [
            f"model_name:={model_name}",
            f"model_repository_paths:=[\"{triton_repo}\"]",
        ]
    if interface_specs:
        extra_args.append(f"interface_specs_file:={interface_specs}")

    cmd = [
        "ros2", "launch", "isaac_ros_segformer", launch_file,
        f"image_topic:={image_topic}",
    ] + extra_args

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "segformer",
    }

    return {
        "status":       "launched",
        "key":          key,
        "pid":          proc.pid,
        "backend":      backend,
        "image_topic":  image_topic,
        "published_topics": {
            "raw_mask":     "/segformer/raw_segmentation_mask",
            "colored_mask": "/segformer/colored_segmentation_mask",
        },
        "message": (
            f"Segformer people segmentation launched ({backend}, PID {proc.pid}). "
            f"Publishes to /segformer/raw_segmentation_mask."
        ),
    }


# ---------------------------------------------------------------------------
# Segment Anything (SAM / Mobile SAM)
# ---------------------------------------------------------------------------

async def handle_launch_segment_anything(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch SAM or Mobile SAM for prompt-driven single-frame instance segmentation.

    Prompt input:
      'bbox'  — pipe RT-DETR/YOLO Detection2DArray output to /prompts
      'point' — publish point prompts as Detection2DArray to /prompts

    Integration with object detection:
      launch_object_detection output_topic → remap to /prompts
      SAM generates per-object masks → feed to FoundationPose or nvblox

    Publishes:
      /segment_anything/raw_segmentation_mask (TensorList)
    """
    model_name      = args.get("model_name", "mobile_sam")   # mobile_sam | sam_vit_h | sam_vit_l
    image_topic     = args.get("image_topic", "/image_rect")
    prompt_topic    = args.get("prompt_topic", "/prompts")    # Detection2DArray
    prompt_type     = args.get("prompt_input_type", "bbox")   # bbox | point
    max_batch_size  = int(args.get("max_batch_size", 20))
    img_width       = int(args.get("image_width",  1200))
    img_height      = int(args.get("image_height",  632))
    mask_width      = int(args.get("mask_width",   960))
    mask_height     = int(args.get("mask_height",  544))
    triton_repo     = args.get("model_repository_path", _DEFAULT_TRITON_REPO)
    has_input_mask  = bool(args.get("has_input_mask", False))

    # SAM only supports Triton
    key = "segmentation/segment_anything"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_segment_anything"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_segment_anything not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_segment_anything\n"
                "  source install/setup.bash"
            ),
        }

    scene_dir = await _get_scene_dir()
    seg_dir   = scene_dir / "segmentation"
    seg_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = seg_dir / "sam_params.yaml"
    params_yaml.write_text(
        _SAM_PARAMS.format(
            prompt_input_type=prompt_type,
            has_input_mask=str(has_input_mask).lower(),
            max_batch_size=max_batch_size,
            img_height=img_height,
            img_width=img_width,
            mask_width=mask_width,
            mask_height=mask_height,
        )
    )

    cmd = [
        "ros2", "launch", "isaac_ros_segment_anything",
        "isaac_ros_segment_anything_triton.launch.py",
        f"model_name:={model_name}",
        f"model_repository_paths:=[\"{triton_repo}\"]",
        f"image_topic:={image_topic}",
        f"params_file:={params_yaml}",
        "--ros-args",
        "-r", f"/prompts:={prompt_topic}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "segment_anything", "model": model_name,
    }

    return {
        "status":          "launched",
        "key":             key,
        "pid":             proc.pid,
        "model":           model_name,
        "prompt_type":     prompt_type,
        "prompt_topic":    prompt_topic,
        "image_topic":     image_topic,
        "published_topics": {
            "raw_mask": "/segment_anything/raw_segmentation_mask (TensorList)",
        },
        "params_file": str(params_yaml),
        "detection_pipeline_hint": (
            f"To feed RT-DETR/YOLO detections as prompts: "
            f"launch_object_detection with output_topic='{prompt_topic}' "
            f"(Detection2DArray auto-remapped to /prompts)"
        ),
        "foundationpose_hint": (
            "SAM output → FoundationPose: remap /segment_anything/raw_segmentation_mask "
            "to /segmentation_image for pose estimation."
        ),
        "message": (
            f"SAM ({model_name}) launched (PID {proc.pid}). "
            f"Prompt type: {prompt_type} on {prompt_topic!r}. "
            f"Masks on /segment_anything/raw_segmentation_mask."
        ),
    }


# ---------------------------------------------------------------------------
# Segment Anything 2 (video tracking)
# ---------------------------------------------------------------------------

async def handle_launch_segment_anything2(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch SAM2 for video object tracking across frames.
    Unlike SAM (single-frame), SAM2 maintains object memory across the video stream.

    Workflow:
      1. Launch this node
      2. Call sam2_add_objects with initial bbox/point prompts to register objects
      3. SAM2 tracks those objects automatically on subsequent frames
      4. Call sam2_remove_object when tracking is no longer needed

    Only Triton ONNX backend supported.
    Publishes: /segment_anything2/raw_segmentation_mask
    """
    max_num_objects = int(args.get("max_num_objects", 10))
    img_width       = int(args.get("image_width",  640))
    img_height      = int(args.get("image_height", 480))
    image_topic     = args.get("image_topic", "/image_rect")
    triton_repo     = args.get("model_repository_path", _DEFAULT_TRITON_REPO)

    key = "segmentation/segment_anything2"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if not await _pkg_exists("isaac_ros_segment_anything2"):
        return {
            "status": "error",
            "message": (
                "isaac_ros_segment_anything2 not found.\n"
                "  cd ~/ros2_ws && colcon build --packages-up-to isaac_ros_segment_anything2\n"
                "  source install/setup.bash"
            ),
        }

    scene_dir = await _get_scene_dir()
    seg_dir   = scene_dir / "segmentation"
    seg_dir.mkdir(parents=True, exist_ok=True)

    params_yaml = seg_dir / "sam2_params.yaml"
    params_yaml.write_text(
        _SAM2_PARAMS.format(
            max_num_objects=max_num_objects,
            img_height=img_height,
            img_width=img_width,
        )
    )

    cmd = [
        "ros2", "launch", "isaac_ros_segment_anything2",
        "isaac_ros_segment_anything2_core.launch.py",
        f"model_repository_paths:=[\"{triton_repo}\"]",
        f"image_topic:={image_topic}",
        f"params_file:={params_yaml}",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _LAUNCHED_PROCESSES[key] = {
        "process": proc, "pid": proc.pid,
        "cmd": " ".join(cmd), "type": "segment_anything2",
    }

    return {
        "status":           "launched",
        "key":              key,
        "pid":              proc.pid,
        "max_objects":      max_num_objects,
        "image_topic":      image_topic,
        "published_topics": {
            "raw_mask": "/segment_anything2/raw_segmentation_mask",
        },
        "services": {
            "add_objects":   "/add_objects (isaac_ros_segment_anything2_interfaces/AddObjects)",
            "remove_object": "/remove_object (isaac_ros_segment_anything2_interfaces/RemoveObject)",
        },
        "params_file": str(params_yaml),
        "warmup_note": "SAM2 requires warmup time before accurate tracking begins.",
        "message": (
            f"SAM2 video tracking launched (PID {proc.pid}). "
            f"Call sam2_add_objects to register up to {max_num_objects} objects to track. "
            f"Masks on /segment_anything2/raw_segmentation_mask."
        ),
    }


# ---------------------------------------------------------------------------
# SAM2 object management services
# ---------------------------------------------------------------------------

async def handle_sam2_add_objects(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Register objects for SAM2 tracking via /add_objects service.
    Accepts a list of bounding boxes or points as initial object prompts.
    SAM2 will track these objects across subsequent video frames.
    """
    objects = args.get("objects", [])
    # objects: list of {"id": int, "bbox": [x1, y1, x2, y2]} or {"id": int, "point": [x, y]}

    if not objects:
        return {
            "status":  "error",
            "message": (
                "Provide 'objects' as a list of dicts, e.g.: "
                "[{\"id\": 1, \"bbox\": [100, 100, 300, 400]}, ...]"
            ),
        }

    # Build ROS2 service request
    # The AddObjects service accepts annotations with object ids and prompts
    annotations_str = json.dumps(objects)
    request = f"{{annotations: {annotations_str}}}"

    result = await _call_service(
        "/add_objects",
        "isaac_ros_segment_anything2_interfaces/srv/AddObjects",
        request,
        timeout=15,
    )
    if result["status"] == "ok":
        result["objects_added"] = len(objects)
        result["object_ids"] = [o.get("id", i) for i, o in enumerate(objects)]
        result["message"] = (
            f"Added {len(objects)} object(s) to SAM2 tracking. "
            f"Masks will appear on /segment_anything2/raw_segmentation_mask."
        )
    return result


async def handle_sam2_remove_object(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a tracked object from SAM2 by its object ID."""
    object_id = int(args.get("object_id", 0))

    result = await _call_service(
        "/remove_object",
        "isaac_ros_segment_anything2_interfaces/srv/RemoveObject",
        f"{{object_id: {object_id}}}",
        timeout=10,
    )
    if result["status"] == "ok":
        result["message"] = f"Object {object_id} removed from SAM2 tracking."
    return result


# ---------------------------------------------------------------------------
# Segmentation → nvblox integration helper
# ---------------------------------------------------------------------------

async def handle_configure_segmentation_for_nvblox(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return the remapping configuration needed to pipe segmentation output into
    nvblox for people-aware 3D mapping (mapping_type='people_segmentation').

    Nvblox expects a segmentation mask on the people_segmentation topic alongside
    the depth stream. This tool returns the topic names and launch_nvblox params.
    """
    seg_source   = args.get("segmentation_source", "unet")    # unet | segformer | sam | sam2
    depth_topic  = args.get("depth_topic",  "/front_stereo_camera/left/depth")
    color_topic  = args.get("color_topic",  "/front_stereo_camera/left/image_rect_color")
    cam_info     = args.get("camera_info_topic", "/front_stereo_camera/left/camera_info")
    odom_topic   = args.get("odom_topic",   "visual_slam/tracking/odometry")

    mask_topic_map = {
        "unet":      "unet/raw_segmentation_mask",
        "segformer": "/segformer/raw_segmentation_mask",
        "sam":       "/segment_anything/raw_segmentation_mask",
        "sam2":      "/segment_anything2/raw_segmentation_mask",
    }
    mask_topic = mask_topic_map.get(seg_source, "unet/raw_segmentation_mask")

    return {
        "status":              "config_ready",
        "segmentation_source": seg_source,
        "mask_topic":          mask_topic,
        "nvblox_launch_args": {
            "mapping_type":    "people_segmentation",
            "depth_topic":     depth_topic,
            "image_topic":     color_topic,
            "camera_info_topic": cam_info,
            "odom_topic":      odom_topic,
        },
        "remapping_needed": {
            f"{mask_topic}": "→ nvblox people_segmentation input topic",
        },
        "instruction": (
            f"Run launch_nvblox with mapping_type='people_segmentation'. "
            f"Remap {mask_topic!r} to the nvblox people segmentation topic. "
            f"This removes people from the 3D obstacle map so the robot navigates around "
            f"static obstacles only."
        ),
    }
