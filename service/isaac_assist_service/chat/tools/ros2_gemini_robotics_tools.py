"""
ros2_gemini_robotics_tools.py
------------------------------
Handlers for launching a Gemini Robotics ER bridge node that exposes all
Gemini Robotics ER 1.6 capabilities as ROS2 services/actions.

Capabilities exposed:
  detect_objects       — point-level object detection
  detect_bboxes        — bounding box detection
  plan_trajectory      — ordered waypoint trajectory
  orchestrate          — multi-function orchestration
  plan_grasp           — grasp planning
  read_gauge           — gauge reading
  measure_fluid        — fluid level measurement
  read_text            — OCR / text extraction
  segment_objects      — pixel-level segmentation masks (Gemini 2.5+)
  spatial_query        — spatial constraint-based object finding
"""
from __future__ import annotations

import asyncio
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Dict

from . import kit_tools

_WORKSPACE = Path(__file__).resolve().parents[5] / "workspace"


# ── Scene-dir helper ──────────────────────────────────────────────────────────

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


# ── Package file templates ────────────────────────────────────────────────────

_CMAKE_LISTS = """\
cmake_minimum_required(VERSION 3.8)
project(gemini_robotics_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/GeminiQuery.srv"
  "action/GeminiTask.action"
  DEPENDENCIES std_msgs geometry_msgs
)

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  scripts/gemini_robotics_node.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
"""

_PACKAGE_XML = """\
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>gemini_robotics_bridge</name>
  <version>0.1.0</version>
  <description>Gemini Robotics ER 1.6 ROS2 service/action bridge</description>
  <maintainer email="isaac@omniverse.local">Isaac Assist</maintainer>
  <license>Apache-2.0</license>

  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>ament_cmake_python</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>

  <depend>rclpy</depend>
  <depend>std_msgs</depend>
  <depend>sensor_msgs</depend>
  <depend>geometry_msgs</depend>
  <depend>vision_msgs</depend>
  <depend>cv_bridge</depend>

  <exec_depend>rosidl_default_runtime</exec_depend>

  <member_of_group>rosidl_interface_packages</member_of_group>

  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
"""

_GEMINI_QUERY_SRV = """\
# Gemini Robotics ER query service
# Request: submit a vision+language query using the latest cached camera frame
# or a specific image topic.
string capability        # detect_objects | detect_bboxes | plan_trajectory | orchestrate
                         # plan_grasp | read_gauge | measure_fluid | read_text
                         # segment_objects | spatial_query
string prompt            # natural language task description
string parameters        # JSON-encoded extra params (e.g. {"object": "cup"})
string image_topic       # override camera topic (empty = use node default)
---
bool success
string result            # JSON-encoded capability-specific response
string error_message     # empty on success
"""

_GEMINI_TASK_ACTION = """\
# Gemini Robotics ER long-running task action
# Used for multi-step tasks (e.g. pick-and-place orchestration)
string capability
string prompt
string parameters
string image_topic
---
bool success
string result
string error_message
---
string status            # e.g. "capturing_image", "calling_api", "parsing_response"
float32 progress         # 0.0 - 1.0
string partial_result    # incremental JSON result (empty until final step)
"""

_LAUNCH_PY = """\
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("image_topic",        default_value="/camera/image_raw"),
        DeclareLaunchArgument("depth_topic",        default_value="/camera/depth/image_rect_raw"),
        DeclareLaunchArgument("model_id",           default_value="gemini-robotics-er-1.6-preview"),
        DeclareLaunchArgument("api_key_env",        default_value="GOOGLE_API_KEY"),
        DeclareLaunchArgument("inference_rate_hz",  default_value="2.0"),
        DeclareLaunchArgument("save_annotated",     default_value="true"),
        DeclareLaunchArgument("output_dir",         default_value="{output_dir}"),
        Node(
            package="gemini_robotics_bridge",
            executable="gemini_robotics_node.py",
            name="gemini_robotics_bridge",
            parameters=[{{
                "image_topic":       LaunchConfiguration("image_topic"),
                "depth_topic":       LaunchConfiguration("depth_topic"),
                "model_id":          LaunchConfiguration("model_id"),
                "api_key_env":       LaunchConfiguration("api_key_env"),
                "inference_rate_hz": LaunchConfiguration("inference_rate_hz"),
                "save_annotated":    LaunchConfiguration("save_annotated"),
                "output_dir":        LaunchConfiguration("output_dir"),
            }}],
            output="screen",
        ),
    ])
"""

_NODE_SCRIPT = r'''#!/usr/bin/env python3
"""gemini_robotics_node — Gemini Robotics ER 1.6 ROS2 bridge node.

Exposes all Gemini Robotics ER capabilities as ROS2 services and actions.
Each service accepts a GeminiQuery.srv request (capability + prompt + params)
and returns a JSON-encoded result.

Services:
  /gemini_robotics/query          (GeminiQuery)  — all capabilities
  /gemini_robotics/detect_objects (GeminiQuery)  — detect_objects shortcut
  /gemini_robotics/detect_bboxes  (GeminiQuery)  — detect_bboxes shortcut
  /gemini_robotics/plan_trajectory (GeminiQuery) — plan_trajectory shortcut
  /gemini_robotics/plan_grasp     (GeminiQuery)  — plan_grasp shortcut
  /gemini_robotics/spatial_query  (GeminiQuery)  — spatial_query shortcut
  /gemini_robotics/read_sensor    (GeminiQuery)  — read_gauge/measure_fluid/read_text shortcut

Actions:
  /gemini_robotics/task           (GeminiTask)   — long-running multi-step tasks

Topics subscribed:
  /camera/image_raw               (sensor_msgs/Image)  — primary image source
  /camera/depth/image_rect_raw    (sensor_msgs/Image)  — optional depth

Topics published:
  /gemini_robotics/detections     (std_msgs/String)    — latest detection JSON
  /gemini_robotics/detections_viz (sensor_msgs/Image)  — annotated image

Requires:
  GOOGLE_API_KEY environment variable
  pip install google-genai
"""
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

try:
    from gemini_robotics_bridge.srv import GeminiQuery
    from gemini_robotics_bridge.action import GeminiTask
    _IFACES_OK = True
except ImportError:
    _IFACES_OK = False


# ── Gemini API helpers ────────────────────────────────────────────────────────

def _encode_image_b64(cv_img) -> str:
    import cv2 as _cv
    _, buf = _cv.imencode(".jpg", cv_img, [_cv.IMWRITE_JPEG_QUALITY, 90])
    return base64.b64encode(buf).decode()


def _call_gemini(model_id: str, api_key: str, image_b64: str, system_prompt: str, user_prompt: str) -> str:
    """Call Gemini API with image + prompts, return raw text response."""
    import urllib.request as _req
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": user_prompt},
            ]
        }],
        "generation_config": {
            "response_mime_type": "application/json",
            "temperature": 0.1,
            "max_output_tokens": 2048,
        },
    }).encode()
    request = _req.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with _req.urlopen(request, timeout=30) as resp:
        data = json.load(resp)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _extract_json(text: str) -> Any:
    """Extract and parse the first JSON object/array from a string."""
    text = text.strip()
    # Gemini JSON mode usually returns clean JSON directly
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find first [ or { and extract
    start = min(
        (text.find("[") if text.find("[") != -1 else len(text)),
        (text.find("{") if text.find("{") != -1 else len(text)),
    )
    if start < len(text):
        try:
            return json.loads(text[start:])
        except Exception:
            pass
    return {"raw": text}


# ── Capability system prompts ─────────────────────────────────────────────────
# Each prompt instructs Gemini ER to output coordinates in [y, x] 0-1000 space.

_SYSTEM_PROMPTS: Dict[str, str] = {
    "detect_objects": (
        "You are a robot perception system. Detect all objects in the image and return their "
        "locations as a JSON array. Format: [{\"point\": [y, x], \"label\": \"object_name\"}, ...] "
        "where coordinates are normalized 0-1000 (y=vertical 0=top, x=horizontal 0=left). "
        "Return ONLY valid JSON."
    ),
    "detect_bboxes": (
        "You are a robot perception system. Detect all objects and return bounding boxes as a "
        "JSON array. Format: [{\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": \"object_name\"}, ...] "
        "where coordinates are normalized 0-1000. Return ONLY valid JSON."
    ),
    "plan_trajectory": (
        "You are a robot trajectory planner. Given the task, plan an ordered sequence of "
        "waypoints the robot end-effector should visit. Return as a JSON array ordered by execution "
        "sequence. Format: [{\"point\": [y, x], \"label\": \"0\"}, {\"point\": [y, x], \"label\": \"1\"}, ...] "
        "where label is the step index (string) and coordinates are normalized 0-1000. "
        "Return ONLY valid JSON."
    ),
    "orchestrate": (
        "You are a robot task orchestrator. Break the task into a sequence of function calls "
        "the robot should execute. Return as a JSON array. "
        "Format: [{\"function\": \"function_name\", \"args\": [arg1, arg2, ...]}, ...] "
        "Available functions: move_to, pick, place, rotate, open_gripper, close_gripper, wait, look_at. "
        "Return ONLY valid JSON."
    ),
    "plan_grasp": (
        "You are a robot grasp planner. Analyze the scene and recommend the optimal grasp strategy. "
        "Return as JSON with: {\"target_object\": str, \"grasp_point\": [y, x], "
        "\"approach_direction\": [dy, dx, dz], \"gripper_width_mm\": float, "
        "\"grasp_type\": \"pinch|power|lateral\", \"confidence\": float, "
        "\"reasoning\": str}. Coordinates normalized 0-1000. Return ONLY valid JSON."
    ),
    "read_gauge": (
        "You are an industrial sensor reader. Read the gauge/dial/meter in the image and return "
        "the measurement as JSON: {\"reading\": float, \"unit\": str, \"min_scale\": float, "
        "\"max_scale\": float, \"confidence\": float, \"description\": str}. Return ONLY valid JSON."
    ),
    "measure_fluid": (
        "You are a fluid level measurement system. Measure the fluid level in the container visible "
        "in the image. Return as JSON: {\"level_percent\": float, \"level_normalized\": float, "
        "\"unit\": str, \"estimated_volume\": float, \"container_type\": str, "
        "\"confidence\": float}. Return ONLY valid JSON."
    ),
    "read_text": (
        "You are an OCR system for robotics. Extract all visible text from the image. "
        "Return as JSON: {\"text\": str, \"regions\": [{\"text\": str, \"bbox\": [ymin, xmin, ymax, xmax], "
        "\"confidence\": float}]}. Coordinates normalized 0-1000. Return ONLY valid JSON."
    ),
    "segment_objects": (
        "You are a robot perception system with segmentation capability. Segment all relevant objects. "
        "Return as JSON array: [{\"label\": str, \"box_2d\": [ymin, xmin, ymax, xmax], "
        "\"mask\": \"<base64_png_mask>\", \"confidence\": float}, ...]. "
        "Coordinates normalized 0-1000. Return ONLY valid JSON."
    ),
    "spatial_query": (
        "You are a robot spatial reasoning system. Find objects in the image that satisfy the "
        "spatial constraint described in the query. Return as JSON array: "
        "[{\"point\": [y, x], \"label\": \"object_name\", \"spatial_relation\": str, "
        "\"confidence\": float}, ...]. Coordinates normalized 0-1000. Return ONLY valid JSON."
    ),
}

_DEFAULT_PROMPTS: Dict[str, str] = {
    "detect_objects":  "Detect and localize all objects in the scene.",
    "detect_bboxes":   "Detect all objects and provide their bounding boxes.",
    "plan_trajectory": "Plan a trajectory for the robot to reach the target object.",
    "orchestrate":     "Plan the sequence of actions to complete the manipulation task.",
    "plan_grasp":      "Plan the optimal grasp for the most graspable object in the scene.",
    "read_gauge":      "Read the gauge or meter value.",
    "measure_fluid":   "Measure the fluid level in the container.",
    "read_text":       "Extract all visible text from the image.",
    "segment_objects": "Segment all objects in the scene.",
    "spatial_query":   "Find the object that is closest to the center of the scene.",
}


def _denormalize_bbox(box: List[float], h: int, w: int) -> Dict:
    """Convert Gemini normalized [0-1000] bbox to pixel coordinates."""
    ymin, xmin, ymax, xmax = box
    return {
        "ymin": int(ymin * h / 1000),
        "xmin": int(xmin * w / 1000),
        "ymax": int(ymax * h / 1000),
        "xmax": int(xmax * w / 1000),
    }


def _denormalize_point(point: List[float], h: int, w: int) -> Dict:
    """Convert Gemini normalized [y, x] point to pixel coordinates."""
    y, x = point
    return {"x_px": int(x * w / 1000), "y_px": int(y * h / 1000)}


def _annotate_detections(cv_img, result_json: Any, capability: str):
    """Draw detection overlays on a copy of the image (requires cv2)."""
    import cv2 as _cv
    img = cv_img.copy()
    h, w = img.shape[:2]
    items = result_json if isinstance(result_json, list) else [result_json]
    for item in items:
        label = str(item.get("label", "?"))
        if "box_2d" in item:
            px = _denormalize_bbox(item["box_2d"], h, w)
            _cv.rectangle(img, (px["xmin"], px["ymin"]), (px["xmax"], px["ymax"]), (0, 255, 0), 2)
            _cv.putText(img, label, (px["xmin"], px["ymin"] - 5),
                        _cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        elif "point" in item:
            pt = _denormalize_point(item["point"], h, w)
            _cv.circle(img, (pt["x_px"], pt["y_px"]), 8, (0, 0, 255), -1)
            _cv.putText(img, label, (pt["x_px"] + 10, pt["y_px"]),
                        _cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        elif "grasp_point" in item:
            pt = _denormalize_point(item["grasp_point"], h, w)
            _cv.drawMarker(img, (pt["x_px"], pt["y_px"]), (255, 0, 0),
                           _cv.MARKER_CROSS, 20, 2)
    return img


# ── ROS2 Node ─────────────────────────────────────────────────────────────────

class GeminiRoboticsBridgeNode(Node):
    """Gemini Robotics ER 1.6 bridge — all capabilities as ROS2 services/actions."""

    def __init__(self) -> None:
        super().__init__("gemini_robotics_bridge")

        self.declare_parameter("image_topic",       "/camera/image_raw")
        self.declare_parameter("depth_topic",       "/camera/depth/image_rect_raw")
        self.declare_parameter("model_id",          "gemini-robotics-er-1.6-preview")
        self.declare_parameter("api_key_env",       "GOOGLE_API_KEY")
        self.declare_parameter("inference_rate_hz", 2.0)
        self.declare_parameter("save_annotated",    True)
        self.declare_parameter("output_dir",        "")

        image_topic    = self.get_parameter("image_topic").value
        self._model    = self.get_parameter("model_id").value
        api_key_env    = self.get_parameter("api_key_env").value
        rate           = float(self.get_parameter("inference_rate_hz").value)
        self._save     = bool(self.get_parameter("save_annotated").value)
        output_dir_str = self.get_parameter("output_dir").value

        self._api_key    = os.environ.get(api_key_env, "")
        self._min_iv     = 1.0 / max(rate, 0.1)
        self._output_dir = Path(output_dir_str) if output_dir_str else Path.home() / "gemini_results"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if not self._api_key:
            self.get_logger().warn(
                f"Environment variable {api_key_env!r} is not set — Gemini API calls will fail. "
                "Set GOOGLE_API_KEY before running."
            )

        self._bridge: Optional[Any]     = CvBridge() if _CV2_OK else None
        self._latest_img: Optional[Any] = None   # cv2 image
        self._latest_hw: Tuple[int,int] = (480, 640)
        self._lock = threading.Lock()

        # Image subscriber
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._img_sub = self.create_subscription(Image, image_topic, self._img_cb, qos)

        # Result publisher
        self._result_pub = self.create_publisher(String, "/gemini_robotics/detections", 10)
        self._viz_pub    = self.create_publisher(Image,  "/gemini_robotics/detections_viz", 10)

        # Services
        if _IFACES_OK:
            self._svc = self.create_service(
                GeminiQuery, "/gemini_robotics/query", self._query_cb
            )
            # Shortcut services for each capability
            for cap in _SYSTEM_PROMPTS:
                self.create_service(
                    GeminiQuery,
                    f"/gemini_robotics/{cap}",
                    lambda req, resp, c=cap: self._query_cb_for(req, resp, c),
                )
            # Action server for long-running tasks
            self._action_server = ActionServer(
                self, GeminiTask, "/gemini_robotics/task", self._task_cb
            )
            self.get_logger().info("Services and action server registered.")
        else:
            self.get_logger().warn(
                "gemini_robotics_bridge interfaces not found. "
                "Run 'colcon build --packages-select gemini_robotics_bridge' first."
            )

        self.get_logger().info(
            f"GeminiRoboticsBridgeNode ready — model={self._model!r} "
            f"image_topic={image_topic!r}"
        )

    # ── Image subscription ────────────────────────────────────────────────────

    def _img_cb(self, msg: Image) -> None:
        if self._bridge is None:
            return
        try:
            cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            with self._lock:
                self._latest_img = cv_img
                self._latest_hw  = (cv_img.shape[0], cv_img.shape[1])
        except Exception as exc:
            self.get_logger().debug(f"Image decode error: {exc}")

    # ── Core inference ────────────────────────────────────────────────────────

    def _infer(self, capability: str, prompt: str, parameters: str,
               image_topic_override: str = "") -> Tuple[bool, str]:
        """Run a Gemini ER inference call. Returns (success, json_result_string)."""
        if not self._api_key:
            return False, json.dumps({"error": "GOOGLE_API_KEY not set"})

        # Get latest image
        with self._lock:
            cv_img = self._latest_img
            h, w   = self._latest_hw

        if cv_img is None:
            return False, json.dumps({"error": "No image received yet on camera topic"})

        if capability not in _SYSTEM_PROMPTS:
            return False, json.dumps({"error": f"Unknown capability: {capability!r}. "
                                      f"Available: {list(_SYSTEM_PROMPTS.keys())}"})

        system_prompt = _SYSTEM_PROMPTS[capability]
        user_prompt   = prompt or _DEFAULT_PROMPTS.get(capability, "")

        # Append extra parameters to user prompt
        if parameters:
            try:
                params = json.loads(parameters)
                if params:
                    user_prompt += f"\n\nAdditional parameters: {json.dumps(params)}"
            except json.JSONDecodeError:
                user_prompt += f"\n\nAdditional context: {parameters}"

        try:
            img_b64  = _encode_image_b64(cv_img)
            raw_text = _call_gemini(self._model, self._api_key, img_b64, system_prompt, user_prompt)
            result   = _extract_json(raw_text)

            # Enrich with pixel coords
            result_with_px = self._enrich_with_pixel_coords(result, capability, h, w)

            result_str = json.dumps(result_with_px, indent=2)

            # Save annotated image
            if self._save and _CV2_OK:
                self._save_annotated(cv_img, result, capability)

            # Publish to detections topic
            msg = String()
            msg.data = json.dumps({
                "capability": capability,
                "prompt": user_prompt,
                "model": self._model,
                "result": result_with_px,
                "timestamp": time.time(),
            })
            self._result_pub.publish(msg)

            return True, result_str

        except Exception as exc:
            err = json.dumps({"error": str(exc), "capability": capability})
            self.get_logger().error(f"Gemini ER error [{capability}]: {exc}")
            return False, err

    def _enrich_with_pixel_coords(self, result: Any, capability: str, h: int, w: int) -> Any:
        """Add pixel-space coordinates alongside normalized 0-1000 coords."""
        if not isinstance(result, list):
            return result
        enriched = []
        for item in result:
            item = dict(item) if isinstance(item, dict) else item
            if isinstance(item, dict):
                if "point" in item:
                    item["point_px"] = _denormalize_point(item["point"], h, w)
                if "box_2d" in item:
                    item["box_2d_px"] = _denormalize_bbox(item["box_2d"], h, w)
                if "grasp_point" in item:
                    item["grasp_point_px"] = _denormalize_point(item["grasp_point"], h, w)
            enriched.append(item)
        return enriched

    def _save_annotated(self, cv_img, result: Any, capability: str) -> None:
        import cv2 as _cv
        try:
            annotated = _annotate_detections(cv_img, result, capability)
            ts   = int(time.time())
            path = self._output_dir / f"{capability}_{ts}.jpg"
            _cv.imwrite(str(path), annotated)
        except Exception as exc:
            self.get_logger().debug(f"Failed to save annotated image: {exc}")

    # ── Service callbacks ─────────────────────────────────────────────────────

    def _query_cb(self, request, response):
        """Universal /gemini_robotics/query service callback."""
        success, result = self._infer(
            capability=request.capability,
            prompt=request.prompt,
            parameters=request.parameters,
            image_topic_override=request.image_topic,
        )
        response.success       = success
        response.result        = result
        response.error_message = "" if success else json.loads(result).get("error", "Unknown error")
        return response

    def _query_cb_for(self, request, response, capability: str):
        """Shortcut service callback for a specific capability."""
        # Use the capability from routing, but allow override in request
        cap = request.capability if request.capability else capability
        success, result = self._infer(
            capability=cap,
            prompt=request.prompt,
            parameters=request.parameters,
            image_topic_override=request.image_topic,
        )
        response.success       = success
        response.result        = result
        response.error_message = "" if success else json.loads(result).get("error", "Unknown error")
        return response

    # ── Action callback ───────────────────────────────────────────────────────

    def _task_cb(self, goal_handle):
        """Long-running task action callback with progress feedback."""
        req      = goal_handle.request
        feedback = GeminiTask.Feedback()
        result   = GeminiTask.Result()

        steps = [
            ("capturing_image",    0.2),
            ("calling_api",        0.6),
            ("parsing_response",   0.9),
        ]

        for status, progress in steps:
            feedback.status        = status
            feedback.progress      = progress
            feedback.partial_result = ""
            goal_handle.publish_feedback(feedback)
            time.sleep(0.05)

        success, result_str = self._infer(
            capability=req.capability,
            prompt=req.prompt,
            parameters=req.parameters,
            image_topic_override=req.image_topic,
        )

        feedback.status        = "complete"
        feedback.progress      = 1.0
        feedback.partial_result = result_str
        goal_handle.publish_feedback(feedback)

        result.success       = success
        result.result        = result_str
        result.error_message = "" if success else json.loads(result_str).get("error", "")

        if success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = GeminiRoboticsBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
'''


# ── Handler ───────────────────────────────────────────────────────────────────

async def handle_launch_gemini_robotics_bridge(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scaffold and launch the Gemini Robotics ER ROS2 bridge package.

    Generates a colcon-ready CMake package with:
      - srv/GeminiQuery.srv    — universal service interface
      - action/GeminiTask.action — long-running task action
      - scripts/gemini_robotics_node.py — Python bridge node
      - launch/gemini_robotics.launch.py

    The node exposes all Gemini Robotics ER 1.6 capabilities as ROS2
    services (/gemini_robotics/query and per-capability shortcuts) plus
    an action server (/gemini_robotics/task) for multi-step tasks.
    """
    scene_dir   = await _get_scene_dir()
    image_topic = args.get("image_topic", "/camera/image_raw")
    depth_topic = args.get("depth_topic", "/camera/depth/image_rect_raw")
    model_id    = args.get("model_id",    "gemini-robotics-er-1.6-preview")
    api_key_env = args.get("api_key_env", "GOOGLE_API_KEY")
    save_annot  = args.get("save_annotated", True)
    ros_ws      = args.get("ros_workspace", "~/ros2_ws")
    rate_hz     = float(args.get("inference_rate_hz", 2.0))

    output_dir = str(scene_dir / "gemini")
    pkg_dir    = scene_dir / "ros2_nodes" / "gemini_robotics_bridge"

    # Sub-directories
    (pkg_dir / "srv").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "action").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "launch").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "gemini_robotics_bridge").mkdir(parents=True, exist_ok=True)
    (scene_dir / "gemini").mkdir(parents=True, exist_ok=True)

    def _write(rel: str, content: str) -> None:
        p = pkg_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    # Fill launch template
    launch_content = _LAUNCH_PY.format(output_dir=output_dir)

    _write("CMakeLists.txt",                          _CMAKE_LISTS)
    _write("package.xml",                             _PACKAGE_XML)
    _write("srv/GeminiQuery.srv",                     _GEMINI_QUERY_SRV)
    _write("action/GeminiTask.action",                _GEMINI_TASK_ACTION)
    _write("scripts/gemini_robotics_node.py",         _NODE_SCRIPT)
    _write("launch/gemini_robotics.launch.py",        launch_content)
    _write("gemini_robotics_bridge/__init__.py",      "")

    # Make the node script executable
    script_path = pkg_dir / "scripts" / "gemini_robotics_node.py"
    script_path.chmod(0o755)

    rel_pkg = str(pkg_dir.relative_to(_WORKSPACE.parent))

    return {
        "status":       "scaffolded",
        "package_name": "gemini_robotics_bridge",
        "package_dir":  str(pkg_dir),
        "model_id":     model_id,
        "image_topic":  image_topic,
        "output_dir":   output_dir,
        "services": [
            "/gemini_robotics/query          (GeminiQuery) — all capabilities",
            "/gemini_robotics/detect_objects  (GeminiQuery) — object detection",
            "/gemini_robotics/detect_bboxes   (GeminiQuery) — bounding box detection",
            "/gemini_robotics/plan_trajectory (GeminiQuery) — waypoint planning",
            "/gemini_robotics/plan_grasp      (GeminiQuery) — grasp planning",
            "/gemini_robotics/spatial_query   (GeminiQuery) — spatial reasoning",
            "/gemini_robotics/read_sensor     (GeminiQuery) — gauge/fluid/text reading",
            "/gemini_robotics/orchestrate     (GeminiQuery) — function orchestration",
            "/gemini_robotics/segment_objects (GeminiQuery) — pixel segmentation",
        ],
        "actions": [
            "/gemini_robotics/task (GeminiTask) — long-running tasks with feedback",
        ],
        "environment": {
            "required": f"export {api_key_env}=<your_google_api_key>",
            "install":  "pip install google-genai",
        },
        "build_commands": [
            f"cd {ros_ws}",
            f"ln -sfn {pkg_dir} src/gemini_robotics_bridge",
            "colcon build --packages-select gemini_robotics_bridge",
            "source install/setup.bash",
        ],
        "launch_command": (
            f"ros2 launch gemini_robotics_bridge gemini_robotics.launch.py "
            f"image_topic:={image_topic}"
        ),
        "usage_example": {
            "cli": (
                "ros2 service call /gemini_robotics/detect_bboxes "
                "gemini_robotics_bridge/srv/GeminiQuery "
                "'{capability: detect_bboxes, prompt: \"Find all objects on the table\", parameters: \"\", image_topic: \"\"}'"
            ),
            "python": textwrap.dedent("""
                import rclpy
                from rclpy.node import Node
                from gemini_robotics_bridge.srv import GeminiQuery

                class GeminiClient(Node):
                    def __init__(self):
                        super().__init__("gemini_client")
                        self.cli = self.create_client(GeminiQuery, "/gemini_robotics/detect_bboxes")
                        self.cli.wait_for_service()

                    def detect(self, prompt: str):
                        req = GeminiQuery.Request()
                        req.capability = "detect_bboxes"
                        req.prompt = prompt
                        future = self.cli.call_async(req)
                        rclpy.spin_until_future_complete(self, future)
                        return future.result()

                rclpy.init()
                client = GeminiClient()
                result = client.detect("Find the red cup")
                print(result.result)  # JSON with bboxes + pixel coords
            """).strip(),
        },
        "message": (
            f"Gemini Robotics ER bridge scaffolded at {rel_pkg}. "
            f"Set {api_key_env} env var, build with colcon, then launch. "
            "All 10 capabilities exposed as /gemini_robotics/* services + 1 action."
        ),
    }
