"""ROS2 Node Scaffolder — generate colcon-ready packages from natural language descriptions."""
from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Dict

from . import kit_tools

_WORKSPACE = Path(__file__).resolve().parents[5] / "workspace"

# ---------------------------------------------------------------------------
# Scene-dir helper (mirrors ros2_autonomy_tools._get_scene_dir)
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


# ---------------------------------------------------------------------------
# Node templates
# ---------------------------------------------------------------------------

_CLASSIFY_OBJECTS_NODE = '''\
#!/usr/bin/env python3
"""classify_objects_node — image classification ROS2 node.

Subscribes to a camera topic, runs a torchvision model, and publishes
JSON-encoded classification results.  Falls back to heuristic colour-based
classification when torch is unavailable.
"""

import json
import time
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from sensor_msgs.msg import Image

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    import torch
    import torchvision.transforms as T
    from torchvision import models
    from PIL import Image as PILImage
    import numpy as np
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


# ImageNet top-1000 labels (abbreviated — replace with full list for production)
_IMAGENET_LABELS: List[str] = []
try:
    import urllib.request, json as _json
    with urllib.request.urlopen(
        "https://raw.githubusercontent.com/anishathalye/imagenet-simple-labels/master/imagenet-simple-labels.json",
        timeout=2,
    ) as _r:
        _IMAGENET_LABELS = _json.load(_r)
except Exception:
    _IMAGENET_LABELS = [f"class_{i}" for i in range(1000)]


class ClassifyObjectsNode(Node):
    """Classify objects in camera images using a torchvision model."""

    MODEL_REGISTRY = {
        "resnet50":           models.resnet50,
        "efficientnet_b0":    models.efficientnet_b0,
        "mobilenet_v3_small": models.mobilenet_v3_small,
    }

    def __init__(self) -> None:
        super().__init__("classify_objects_node")

        # Parameters
        self.declare_parameter("image_topic",          "/front_stereo_camera/left/image_rect_color")
        self.declare_parameter("output_topic",         "/classify_objects/results")
        self.declare_parameter("model",                "mobilenet_v3_small")
        self.declare_parameter("confidence_threshold", 0.1)
        self.declare_parameter("top_k",                5)
        self.declare_parameter("device",               "")  # auto

        image_topic         = self.get_parameter("image_topic").value
        output_topic        = self.get_parameter("output_topic").value
        model_name          = self.get_parameter("model").value
        self.confidence_thr = float(self.get_parameter("confidence_threshold").value)
        self.top_k          = int(self.get_parameter("top_k").value)

        # Device
        self._device = self._select_device(self.get_parameter("device").value)
        self.get_logger().info(f"Using device: {self._device}")

        # Model
        self._model: Optional[Any] = None
        self._transform: Optional[Any] = None
        if _TORCH_AVAILABLE:
            self._load_model(model_name)
        else:
            self.get_logger().warn(
                "torch / torchvision not available — falling back to colour heuristics"
            )

        # CvBridge
        self._bridge = CvBridge() if _CV2_AVAILABLE else None

        # Publisher / Subscriber
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub = self.create_publisher(String, output_topic, 10)
        self._sub = self.create_subscription(Image, image_topic, self._image_cb, qos)
        self.get_logger().info(
            f"ClassifyObjectsNode listening on {image_topic!r}, publishing to {output_topic!r}"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_device(self, hint: str) -> str:
        if hint:
            return hint
        if _TORCH_AVAILABLE and torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load_model(self, name: str) -> None:
        factory = self.MODEL_REGISTRY.get(name)
        if factory is None:
            self.get_logger().warn(f"Unknown model {name!r}, defaulting to mobilenet_v3_small")
            factory = models.mobilenet_v3_small

        self.get_logger().info(f"Loading {name} …")
        self._model = factory(pretrained=True).to(self._device).eval()
        self._transform = T.Compose([
            T.Resize(256),
            T.CenterCrop(224),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        self.get_logger().info(f"Model ready on {self._device}")

    def _image_cb(self, msg: Image) -> None:
        try:
            results = self._classify(msg)
        except Exception as exc:
            self.get_logger().error(f"Classification error: {exc}")
            return

        payload = json.dumps({
            "topic":           msg.header.frame_id,
            "timestamp":       msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            "classifications": results,
        })
        out = String()
        out.data = payload
        self._pub.publish(out)

    def _classify(self, msg: Image) -> List[Dict[str, Any]]:
        if _TORCH_AVAILABLE and self._model is not None and _CV2_AVAILABLE:
            return self._classify_torch(msg)
        return self._classify_colour(msg)

    def _classify_torch(self, msg: Image) -> List[Dict[str, Any]]:
        cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
        pil_img = PILImage.fromarray(cv_img)
        tensor = self._transform(pil_img).unsqueeze(0).to(self._device)
        with torch.no_grad():
            logits = self._model(tensor)
            probs  = torch.softmax(logits, dim=1)[0]
        top = torch.topk(probs, min(self.top_k, len(_IMAGENET_LABELS)))
        results = []
        for score, idx in zip(top.values.tolist(), top.indices.tolist()):
            if score >= self.confidence_thr:
                results.append({
                    "label":      _IMAGENET_LABELS[idx] if idx < len(_IMAGENET_LABELS) else f"class_{idx}",
                    "confidence": round(score, 4),
                    "class_id":   idx,
                })
        return results

    def _classify_colour(self, msg: Image) -> List[Dict[str, Any]]:
        """Colour-histogram heuristic when torch is unavailable."""
        raw = bytes(msg.data)
        n   = len(raw)
        if n < 3:
            return []
        r = sum(raw[0::3]) / (n // 3)
        g = sum(raw[1::3]) / (n // 3)
        b = sum(raw[2::3]) / (n // 3)
        dominant = max((r, "red"), (g, "green"), (b, "blue"), key=lambda x: x[0])
        return [{"label": f"dominant_{dominant[1]}", "confidence": round(dominant[0] / 255, 3), "class_id": -1}]


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ClassifyObjectsNode()
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

_GENERIC_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — {{ description }}

Auto-generated by Isaac Assist scaffold_ros2_node.
"""

import json
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class {{ class_name }}(Node):
    """{{ description }}"""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        # --- Declare parameters ---
        self.declare_parameter("input_topic",  "{{ input_topic }}")
        self.declare_parameter("output_topic", "{{ output_topic }}")

        input_topic  = self.get_parameter("input_topic").value
        output_topic = self.get_parameter("output_topic").value

        # --- Publisher / Subscriber ---
        self._pub = self.create_publisher(String, output_topic, 10)
        self._sub = self.create_subscription(String, input_topic, self._cb, 10)

        self.get_logger().info(
            f"{{ class_name }} ready — listening on {input_topic!r}, publishing to {output_topic!r}"
        )

    def _cb(self, msg: String) -> None:
        """Process incoming message and publish result."""
        # TODO: implement {{ description }}
        result = {"input": msg.data, "processed": True}
        out = String()
        out.data = json.dumps(result)
        self._pub.publish(out)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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

_SETUP_PY = '''\
from setuptools import setup, find_packages
import os
from glob import glob

package_name = "{{ package_name }}"

setup(
    name=package_name,
    version="{{ version }}",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="{{ maintainer }}",
    maintainer_email="{{ maintainer_email }}",
    description="{{ description }}",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
{{ entry_points }}
        ],
    },
)
'''

_PACKAGE_XML = '''\
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>{{ package_name }}</name>
  <version>{{ version }}</version>
  <description>{{ description }}</description>
  <maintainer email="{{ maintainer_email }}">{{ maintainer }}</maintainer>
  <license>Apache-2.0</license>

  <depend>rclpy</depend>
  <depend>std_msgs</depend>
  <depend>sensor_msgs</depend>
{{ extra_depends }}
  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
'''

_SETUP_CFG = '''\
[develop]
script_dir=$base/lib/{{ package_name }}
[install]
install_scripts=$base/lib/{{ package_name }}
'''

_LAUNCH_PY = '''\
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import LaunchConfiguration
from launch.actions import DeclareLaunchArgument


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument("image_topic",  default_value="{{ default_input_topic }}"),
        DeclareLaunchArgument("output_topic", default_value="{{ default_output_topic }}"),
        Node(
            package="{{ package_name }}",
            executable="{{ node_name }}",
            name="{{ node_name }}",
            parameters=[{
                "image_topic":  LaunchConfiguration("image_topic"),
                "output_topic": LaunchConfiguration("output_topic"),
            }],
            output="screen",
        ),
    ])
'''


# ---------------------------------------------------------------------------
# Template renderer — simple {{ key }} substitution
# ---------------------------------------------------------------------------

_OBJECT_DETECTION_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — Isaac ROS object detection node.

Subscribes to a camera topic and publishes vision_msgs/Detection2DArray results.
Uses Isaac ROS RT-DETR or YOLOv8 if available, falls back to ultralytics YOLOv8
Python API, falls back to OpenCV DNN.
"""

import json
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

try:
    from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
    _VISION_MSGS = True
except ImportError:
    _VISION_MSGS = False

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    from ultralytics import YOLO as _YOLO
    _ULTRALYTICS = True
except ImportError:
    _ULTRALYTICS = False


class {{ class_name }}(Node):
    """Object detection node — RT-DETR / YOLOv8 / OpenCV DNN with graceful fallback."""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("image_topic",          "{{ input_topic }}")
        self.declare_parameter("output_topic",         "{{ output_topic }}")
        self.declare_parameter("model",                "yolov8n.pt")   # ultralytics model file or "rtdetr-l.pt"
        self.declare_parameter("confidence_threshold", 0.5)
        self.declare_parameter("device",               "")             # auto

        image_topic  = self.get_parameter("image_topic").value
        output_topic = self.get_parameter("output_topic").value
        model_path   = self.get_parameter("model").value
        self._conf   = float(self.get_parameter("confidence_threshold").value)
        device_hint  = self.get_parameter("device").value

        self._bridge: Optional[Any] = CvBridge() if _CV2_AVAILABLE else None
        self._model:  Optional[Any] = None
        self._backend = "none"

        if _ULTRALYTICS:
            try:
                import torch
                dev = device_hint or ("cuda" if torch.cuda.is_available() else "cpu")
                self._model = _YOLO(model_path)
                self._device = dev
                self._backend = "ultralytics"
                self.get_logger().info(f"Loaded ultralytics {model_path} on {dev}")
            except Exception as exc:
                self.get_logger().warn(f"ultralytics load failed: {exc}")

        if self._backend == "none":
            self.get_logger().warn("No detection backend available — publishing empty detections")

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub_det  = self.create_publisher(
            Detection2DArray if _VISION_MSGS else String, output_topic, 10
        )
        self._pub_json = self.create_publisher(String, output_topic + "/json", 10)
        self._sub      = self.create_subscription(Image, image_topic, self._cb, qos)
        self.get_logger().info(f"{{ class_name }} ready on {image_topic!r} → {output_topic!r}")

    def _cb(self, msg: Image) -> None:
        detections = self._detect(msg)
        self._publish(msg, detections)

    def _detect(self, msg: Image) -> List[Dict[str, Any]]:
        if self._backend == "ultralytics" and _CV2_AVAILABLE:
            return self._detect_ultralytics(msg)
        return []

    def _detect_ultralytics(self, msg: Image) -> List[Dict[str, Any]]:
        cv_img   = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        results  = self._model.predict(cv_img, conf=self._conf, device=self._device, verbose=False)
        out = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                out.append({
                    "label":      r.names[int(box.cls[0])],
                    "confidence": float(box.conf[0]),
                    "bbox":       {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                })
        return out

    def _publish(self, msg: Image, detections: List[Dict[str, Any]]) -> None:
        # JSON side-channel (always available)
        js = String()
        js.data = json.dumps({"frame_id": msg.header.frame_id, "detections": detections})
        self._pub_json.publish(js)

        if not _VISION_MSGS:
            self._pub_det.publish(js)
            return

        arr = Detection2DArray()
        arr.header = msg.header
        for d in detections:
            det = Detection2D()
            det.header = msg.header
            bb = BoundingBox2D()
            b  = d["bbox"]
            bb.center.position.x = (b["x1"] + b["x2"]) / 2
            bb.center.position.y = (b["y1"] + b["y2"]) / 2
            bb.size_x = b["x2"] - b["x1"]
            bb.size_y = b["y2"] - b["y1"]
            det.bbox  = bb
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = d["label"]
            hyp.hypothesis.score    = d["confidence"]
            det.results.append(hyp)
            arr.detections.append(det)
        self._pub_det.publish(arr)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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

_POSE_ESTIMATION_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — 6-DoF object pose estimation node.

Interfaces with Isaac ROS FoundationPose service if available.
Falls back to depth-based centroid estimation for basic position.
Subscribes to RGB + depth, publishes geometry_msgs/PoseArray.
"""

import json
import math
from typing import Any, Dict, List, Optional

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Pose, PoseArray, Point, Quaternion
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import String

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    from message_filters import ApproximateTimeSynchronizer, Subscriber
    _MF_AVAILABLE = True
except ImportError:
    _MF_AVAILABLE = False


class {{ class_name }}(Node):
    """6-DoF pose estimation — wraps FoundationPose service or depth centroid fallback."""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("image_topic",         "{{ input_topic }}")
        self.declare_parameter("depth_topic",         "/depth")
        self.declare_parameter("camera_info_topic",   "/camera_info")
        self.declare_parameter("output_topic",        "{{ output_topic }}")
        self.declare_parameter("depth_threshold_min", 0.1)   # metres
        self.declare_parameter("depth_threshold_max", 3.0)
        self.declare_parameter("min_cluster_pixels",  100)

        rgb_topic   = self.get_parameter("image_topic").value
        depth_topic = self.get_parameter("depth_topic").value
        cam_topic   = self.get_parameter("camera_info_topic").value
        out_topic   = self.get_parameter("output_topic").value
        self._dmin  = float(self.get_parameter("depth_threshold_min").value)
        self._dmax  = float(self.get_parameter("depth_threshold_max").value)
        self._min_px = int(self.get_parameter("min_cluster_pixels").value)

        self._bridge = CvBridge() if _CV2_AVAILABLE else None
        self._fx = self._fy = self._cx = self._cy = None

        self._pub      = self.create_publisher(PoseArray, out_topic, 10)
        self._pub_json = self.create_publisher(String, out_topic + "/json", 10)

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(CameraInfo, cam_topic, self._cam_info_cb, qos)

        if _MF_AVAILABLE and _CV2_AVAILABLE:
            self._rgb_sub   = Subscriber(self, Image, rgb_topic,   qos_profile=qos)
            self._depth_sub = Subscriber(self, Image, depth_topic, qos_profile=qos)
            self._sync = ApproximateTimeSynchronizer(
                [self._rgb_sub, self._depth_sub], queue_size=5, slop=0.05
            )
            self._sync.registerCallback(self._sync_cb)
        else:
            self.create_subscription(Image, depth_topic, self._depth_cb, qos)

        self.get_logger().info(f"{{ class_name }} ready on {rgb_topic!r} + {depth_topic!r} → {out_topic!r}")

    def _cam_info_cb(self, msg: CameraInfo) -> None:
        self._fx = msg.k[0]; self._fy = msg.k[4]
        self._cx = msg.k[2]; self._cy = msg.k[5]

    def _sync_cb(self, rgb_msg: Image, depth_msg: Image) -> None:
        self._estimate_from_depth(depth_msg, rgb_msg.header)

    def _depth_cb(self, msg: Image) -> None:
        self._estimate_from_depth(msg, msg.header)

    def _estimate_from_depth(self, depth_msg: Image, header: Any) -> None:
        if self._bridge is None or self._fx is None:
            return
        try:
            depth = self._bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
        except Exception as exc:
            self.get_logger().error(f"cv_bridge error: {exc}")
            return

        depth = depth.astype(np.float32)
        if depth_msg.encoding in ("16UC1",):
            depth /= 1000.0  # mm → m

        mask = (depth > self._dmin) & (depth < self._dmax)
        ys, xs = np.where(mask)
        if len(xs) < self._min_px:
            return

        zs = depth[ys, xs]
        x3 = ((xs - self._cx) * zs / self._fx).mean()
        y3 = ((ys - self._cy) * zs / self._fy).mean()
        z3 = zs.mean()

        pa = PoseArray()
        pa.header = header
        p = Pose()
        p.position    = Point(x=float(x3), y=float(y3), z=float(z3))
        p.orientation = Quaternion(w=1.0)
        pa.poses.append(p)
        self._pub.publish(pa)

        js = String()
        js.data = json.dumps({"frame_id": header.frame_id, "poses": [
            {"x": float(x3), "y": float(y3), "z": float(z3)}
        ]})
        self._pub_json.publish(js)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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

_VLA_ACTION_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — Vision-Language-Action inference node.

Sends camera frames + a natural-language goal to a VLA model endpoint
(NVIDIA Cosmos NIM or Google Gemini Robotics ER) and publishes the
resulting action as geometry_msgs/Twist (velocity commands) or
std_msgs/String (raw action JSON for downstream processing).

Required environment variables (set one pair):
  NVIDIA_API_KEY       — for Cosmos NIM endpoint
  GOOGLE_API_KEY       — for Gemini Robotics ER endpoint

Parameters:
  image_topic          — camera topic (default: {{ input_topic }})
  goal                 — natural-language instruction (default: "navigate to the goal")
  backend              — "cosmos" | "gemini" | "openai_compat"
  api_endpoint         — override endpoint URL
  model_id             — override model ID
  publish_twist        — if true, parse linear/angular from response and publish Twist
  inference_rate_hz    — max inference calls per second (default: 2.0)
"""

import base64
import json
import os
import time
import threading
from typing import Any, Dict, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image
from std_msgs.msg import String

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

def _encode_image_b64(cv_img) -> str:
    import cv2 as _cv2
    _, buf = _cv2.imencode(".jpg", cv_img, [_cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def _call_cosmos(endpoint: str, model_id: str, api_key: str, image_b64: str, goal: str) -> str:
    import urllib.request, urllib.error
    payload = json.dumps({
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text",      "text": goal},
                ],
            }
        ],
        "max_tokens": 256,
    }).encode()
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def _call_gemini(model_id: str, api_key: str, image_b64: str, goal: str) -> str:
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": goal},
            ]
        }]
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _parse_twist(text: str) -> Optional[Dict[str, float]]:
    """Try to extract linear.x, angular.z from model response JSON."""
    try:
        start = text.index("{")
        end   = text.rindex("}") + 1
        obj   = json.loads(text[start:end])
        lx = float(obj.get("linear_x",  obj.get("linear",  {}).get("x", 0.0)))
        az = float(obj.get("angular_z", obj.get("angular", {}).get("z", 0.0)))
        return {"linear_x": lx, "angular_z": az}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class {{ class_name }}(Node):

    _COSMOS_ENDPOINT  = "https://integrate.api.nvidia.com/v1/chat/completions"
    _COSMOS_MODEL     = "nvidia/cosmos-reason1-7b"
    _GEMINI_MODEL     = "gemini-2.0-flash"

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("image_topic",       "{{ input_topic }}")
        self.declare_parameter("output_topic",      "{{ output_topic }}")
        self.declare_parameter("goal",              "Describe what you see and suggest a safe navigation action.")
        self.declare_parameter("backend",           "cosmos")   # cosmos | gemini | openai_compat
        self.declare_parameter("api_endpoint",      "")
        self.declare_parameter("model_id",          "")
        self.declare_parameter("publish_twist",     True)
        self.declare_parameter("inference_rate_hz", 2.0)

        image_topic   = self.get_parameter("image_topic").value
        output_topic  = self.get_parameter("output_topic").value
        self._goal    = self.get_parameter("goal").value
        self._backend = self.get_parameter("backend").value
        self._ep      = self.get_parameter("api_endpoint").value
        self._model   = self.get_parameter("model_id").value
        self._do_twist = bool(self.get_parameter("publish_twist").value)
        rate          = float(self.get_parameter("inference_rate_hz").value)
        self._min_interval = 1.0 / max(rate, 0.1)

        self._bridge: Optional[Any] = CvBridge() if _CV2_AVAILABLE else None
        self._last_call = 0.0
        self._lock      = threading.Lock()
        self._latest_img: Optional[Any] = None

        # Resolve API keys
        self._nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
        self._google_key = os.environ.get("GOOGLE_API_KEY", "")

        if self._backend == "cosmos" and not self._nvidia_key:
            self.get_logger().warn("NVIDIA_API_KEY not set — Cosmos NIM calls will fail")
        if self._backend == "gemini" and not self._google_key:
            self.get_logger().warn("GOOGLE_API_KEY not set — Gemini calls will fail")

        # Apply defaults
        if not self._ep and self._backend == "cosmos":
            self._ep = self._COSMOS_ENDPOINT
        if not self._model:
            self._model = self._COSMOS_MODEL if self._backend == "cosmos" else self._GEMINI_MODEL

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub_str   = self.create_publisher(String, output_topic, 10)
        self._pub_twist = self.create_publisher(Twist, output_topic + "/twist", 10)
        self._sub       = self.create_subscription(Image, image_topic, self._img_cb, qos)
        self._goal_sub  = self.create_subscription(String, "{{ node_name }}/goal", self._goal_cb, 10)

        self.get_logger().info(
            f"{{ class_name }} backend={self._backend!r} model={self._model!r} "
            f"rate={rate}Hz on {image_topic!r}"
        )

    def _goal_cb(self, msg: String) -> None:
        self._goal = msg.data
        self.get_logger().info(f"Goal updated: {self._goal!r}")

    def _img_cb(self, msg: Image) -> None:
        with self._lock:
            self._latest_img = msg

        now = time.monotonic()
        if now - self._last_call < self._min_interval:
            return
        self._last_call = now

        threading.Thread(target=self._infer, args=(msg,), daemon=True).start()

    def _infer(self, msg: Image) -> None:
        if self._bridge is None:
            return
        try:
            cv_img    = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            img_b64   = _encode_image_b64(cv_img)
            response  = self._call_backend(img_b64)
            self._publish(response)
        except Exception as exc:
            self.get_logger().error(f"Inference error: {exc}")

    def _call_backend(self, img_b64: str) -> str:
        if self._backend == "cosmos" or self._backend == "openai_compat":
            key = self._nvidia_key or os.environ.get("OPENAI_API_KEY", "")
            return _call_cosmos(self._ep, self._model, key, img_b64, self._goal)
        elif self._backend == "gemini":
            return _call_gemini(self._model, self._google_key, img_b64, self._goal)
        else:
            return json.dumps({"error": f"Unknown backend: {self._backend!r}"})

    def _publish(self, text: str) -> None:
        out = String()
        out.data = json.dumps({"backend": self._backend, "goal": self._goal, "response": text})
        self._pub_str.publish(out)

        if self._do_twist:
            twist_vals = _parse_twist(text)
            if twist_vals:
                tw = Twist()
                tw.linear.x  = max(-1.0, min(1.0, twist_vals["linear_x"]))
                tw.angular.z = max(-1.0, min(1.0, twist_vals["angular_z"]))
                self._pub_twist.publish(tw)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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

_CUMOTION_MANIPULATION_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — cuMotion manipulation pipeline node.

Receives a target end-effector pose (service or topic), sends it to MoveIt 2
via the GoalSetter service (/set_target_pose), monitors execution, and
publishes a status string with outcome.

Requires:
  - launch_cumotion_planner (cuMotion action server)
  - launch_cumotion_moveit  (MoveIt 2 + cuMotion plugin)
  - launch_goal_setter      (optional — can call /set_target_pose directly)
  - launch_robot_segmenter  (for collision-aware depth filtering)
"""

import json
import threading
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState
from std_msgs.msg import String

try:
    from moveit_msgs.action import MoveGroup
    from moveit_msgs.msg import (
        MotionPlanRequest, WorkspaceParameters,
        Constraints, PositionConstraint, OrientationConstraint,
        BoundingVolume,
    )
    from shape_msgs.msg import SolidPrimitive
    _MOVEIT = True
except ImportError:
    _MOVEIT = False

try:
    from isaac_ros_goal_setter_interfaces.srv import SetTargetPose
    _GOAL_SETTER = True
except ImportError:
    _GOAL_SETTER = False


class {{ class_name }}(Node):
    """{{ description }}"""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("planning_group",    "{{ planning_group }}")
        self.declare_parameter("end_effector_link", "{{ end_effector_link }}")
        self.declare_parameter("base_frame",        "base_link")
        self.declare_parameter("goal_topic",        "{{ input_topic }}")
        self.declare_parameter("status_topic",      "{{ output_topic }}")
        self.declare_parameter("timeout_s",         10.0)

        self._group    = self.get_parameter("planning_group").value
        self._ee_link  = self.get_parameter("end_effector_link").value
        self._frame    = self.get_parameter("base_frame").value
        self._timeout  = float(self.get_parameter("timeout_s").value)

        goal_topic   = self.get_parameter("goal_topic").value
        status_topic = self.get_parameter("status_topic").value

        # Publishers / Subscribers
        self._status_pub = self.create_publisher(String, status_topic, 10)
        self._goal_sub   = self.create_subscription(
            PoseStamped, goal_topic, self._goal_cb, 10
        )
        self._js_sub = self.create_subscription(
            JointState, "/joint_states", self._js_cb, 10
        )
        self._current_joints: Optional[JointState] = None

        # GoalSetter service client (preferred — simpler than raw MoveGroup action)
        self._goal_setter_client = None
        if _GOAL_SETTER:
            self._goal_setter_client = self.create_client(SetTargetPose, "/set_target_pose")

        # MoveGroup action client (direct fallback)
        self._move_group_client = None
        if _MOVEIT:
            self._move_group_client = ActionClient(self, MoveGroup, "move_group")

        self._lock = threading.Lock()
        self.get_logger().info(
            f"{{ class_name }} ready — listening on {goal_topic!r}, "
            f"group={self._group!r}, ee={self._ee_link!r}"
        )

    def _js_cb(self, msg: JointState) -> None:
        self._current_joints = msg

    def _goal_cb(self, msg: PoseStamped) -> None:
        threading.Thread(target=self._plan_and_execute, args=(msg,), daemon=True).start()

    def _plan_and_execute(self, pose_msg: PoseStamped) -> None:
        with self._lock:
            self._publish_status("planning", pose_msg)
            success = False

            # Try GoalSetter service first (requires launch_goal_setter)
            if self._goal_setter_client and self._goal_setter_client.wait_for_service(timeout_sec=1.0):
                req = SetTargetPose.Request()
                req.target_pose = pose_msg
                future = self._goal_setter_client.call_async(req)
                rclpy.spin_until_future_complete(self, future, timeout_sec=self._timeout)
                if future.done():
                    success = True

            # Fall back to raw MoveGroup action
            elif self._move_group_client and self._move_group_client.wait_for_server(timeout_sec=2.0):
                success = self._send_move_group_goal(pose_msg)

            else:
                self.get_logger().error(
                    "Neither /set_target_pose service nor move_group action available. "
                    "Run launch_goal_setter or launch_cumotion_moveit."
                )

            self._publish_status("success" if success else "failed", pose_msg)

    def _send_move_group_goal(self, pose_msg: PoseStamped) -> bool:
        """Send a MoveGroup action goal directly (no GoalSetter required)."""
        if not _MOVEIT:
            return False

        goal = MoveGroup.Goal()
        goal.request.group_name = self._group
        goal.request.num_planning_attempts = 5
        goal.request.allowed_planning_time = self._timeout
        goal.request.planner_id = "cuMotion"

        # Build position + orientation constraints
        pos_c = PositionConstraint()
        pos_c.header          = pose_msg.header
        pos_c.link_name       = self._ee_link
        pos_c.target_point_offset.x = 0.0
        pos_c.target_point_offset.y = 0.0
        pos_c.target_point_offset.z = 0.0
        box = SolidPrimitive(type=SolidPrimitive.BOX, dimensions=[0.01, 0.01, 0.01])
        bv  = BoundingVolume()
        bv.primitives.append(box)
        bv.primitive_poses.append(pose_msg.pose)
        pos_c.constraint_region = bv
        pos_c.weight = 1.0

        ori_c = OrientationConstraint()
        ori_c.header            = pose_msg.header
        ori_c.link_name         = self._ee_link
        ori_c.orientation       = pose_msg.pose.orientation
        ori_c.absolute_x_axis_tolerance = 0.01
        ori_c.absolute_y_axis_tolerance = 0.01
        ori_c.absolute_z_axis_tolerance = 0.01
        ori_c.weight            = 1.0

        c = Constraints()
        c.position_constraints.append(pos_c)
        c.orientation_constraints.append(ori_c)
        goal.request.goal_constraints.append(c)

        future = self._move_group_client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, future, timeout_sec=self._timeout)
        if not future.done():
            return False
        result_future = future.result().get_result_async()
        rclpy.spin_until_future_complete(self, result_future, timeout_sec=self._timeout)
        return result_future.done() and result_future.result().result.error_code.val == 1

    def _publish_status(self, state: str, pose_msg: PoseStamped) -> None:
        p = pose_msg.pose.position
        o = pose_msg.pose.orientation
        out = String()
        out.data = json.dumps({
            "state":    state,
            "group":    self._group,
            "ee_link":  self._ee_link,
            "target":   {"x": p.x, "y": p.y, "z": p.z,
                         "qx": o.x, "qy": o.y, "qz": o.z, "qw": o.w},
        })
        self._status_pub.publish(out)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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

_SEMANTIC_SEGMENTATION_NODE = '''\
#!/usr/bin/env python3
"""{{ node_name }} — semantic segmentation post-processor.

Subscribes to raw segmentation mask output from any Isaac ROS segmentation
pipeline (UNet, Segformer, SAM, SAM2) and performs downstream processing:
  - Class-level pixel counting / area statistics
  - Filtering for specific class IDs
  - Publishing filtered binary masks per target class
  - JSON summary for downstream decision-making

Compatible mask sources (set mask_topic parameter):
  unet/raw_segmentation_mask               ← UNet / Segformer
  /segment_anything/raw_segmentation_mask  ← SAM
  /segment_anything2/raw_segmentation_mask ← SAM2
"""

import json
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

try:
    import cv2
    from cv_bridge import CvBridge
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


class {{ class_name }}(Node):
    """{{ description }}"""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("mask_topic",      "{{ input_topic }}")
        self.declare_parameter("output_topic",    "{{ output_topic }}")
        self.declare_parameter("target_class_ids", [1])     # class IDs to track/filter
        self.declare_parameter("min_area_pixels",  100)     # ignore tiny detections
        self.declare_parameter("publish_filtered_mask", True)
        self.declare_parameter("class_names", [""])         # optional label list

        mask_topic    = self.get_parameter("mask_topic").value
        output_topic  = self.get_parameter("output_topic").value
        self._targets = list(self.get_parameter("target_class_ids").value)
        self._min_area = int(self.get_parameter("min_area_pixels").value)
        self._pub_filtered = bool(self.get_parameter("publish_filtered_mask").value)
        class_names_param  = list(self.get_parameter("class_names").value)
        self._class_names: Dict[int, str] = {
            i: n for i, n in enumerate(class_names_param) if n
        }

        self._bridge: Optional[object] = CvBridge() if _CV2_AVAILABLE else None

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub_json    = self.create_publisher(String, output_topic, 10)
        self._pub_mask    = self.create_publisher(Image,  output_topic + "/filtered_mask", 10)
        self._sub         = self.create_subscription(Image, mask_topic, self._mask_cb, qos)

        self.get_logger().info(
            f"{{ class_name }} ready — mask_topic={mask_topic!r}, "
            f"tracking class IDs={self._targets}"
        )

    def _mask_cb(self, msg: Image) -> None:
        if self._bridge is None:
            return
        try:
            mask = self._bridge.imgmsg_to_cv2(msg, desired_encoding="mono8")
        except Exception as exc:
            self.get_logger().error(f"cv_bridge error: {exc}")
            return

        stats  = self._compute_stats(mask, msg)
        result = self._build_result(stats, msg)
        self._publish(msg, mask, result, stats)

    def _compute_stats(self, mask: np.ndarray, msg: Image) -> List[Dict]:
        stats = []
        unique, counts = np.unique(mask, return_counts=True)
        total_pixels = mask.size

        for cls_id, count in zip(unique.tolist(), counts.tolist()):
            if count < self._min_area:
                continue
            label = self._class_names.get(cls_id, f"class_{cls_id}")
            is_target = cls_id in self._targets
            # Compute bounding box of class region
            ys, xs = np.where(mask == cls_id)
            bbox = None
            if len(xs) > 0:
                bbox = {
                    "x_min": int(xs.min()), "y_min": int(ys.min()),
                    "x_max": int(xs.max()), "y_max": int(ys.max()),
                    "center_x": float(xs.mean()), "center_y": float(ys.mean()),
                }
            stats.append({
                "class_id":    cls_id,
                "label":       label,
                "pixel_count": count,
                "area_pct":    round(100.0 * count / total_pixels, 2),
                "is_target":   is_target,
                "bbox":        bbox,
            })
        return stats

    def _build_result(self, stats: List[Dict], msg: Image) -> Dict:
        target_detections = [s for s in stats if s["is_target"]]
        return {
            "timestamp":          msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            "frame_id":           msg.header.frame_id,
            "image_size":         [msg.width, msg.height],
            "target_class_ids":   self._targets,
            "target_detections":  target_detections,
            "all_classes":        stats,
            "targets_detected":   len(target_detections) > 0,
        }

    def _publish(self, msg: Image, mask: np.ndarray, result: Dict, stats: List[Dict]) -> None:
        js = String()
        js.data = json.dumps(result)
        self._pub_json.publish(js)

        if self._pub_filtered and self._bridge is not None:
            # Binary mask: 255 where pixel is any target class, 0 elsewhere
            filtered = np.zeros_like(mask)
            for s in stats:
                if s["is_target"]:
                    filtered[mask == s["class_id"]] = 255
            out_msg = self._bridge.cv2_to_imgmsg(filtered, encoding="mono8")
            out_msg.header = msg.header
            self._pub_mask.publish(out_msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = {{ class_name }}()
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


def _render_world_manager(ctx: Dict[str, str]) -> str:
    """Render the WorldCollisionManager template from ros2_curobo_world_tools."""
    from .ros2_curobo_world_tools import _WORLD_MANAGER_NODE
    return _render(_WORLD_MANAGER_NODE, ctx)


def _render(template: str, ctx: Dict[str, str]) -> str:
    result = template
    for k, v in ctx.items():
        result = result.replace("{{ " + k + " }}", v)
    return result


def _to_class_name(name: str) -> str:
    return "".join(w.title() for w in re.split(r"[_\-\s]+", name))


def _to_package_name(name: str) -> str:
    return re.sub(r"[^\w]", "_", name.lower()).strip("_")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_scaffold_ros2_node(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a colcon-ready ROS2 Python package for a custom node."""
    node_name    = _to_package_name(args.get("node_name", "my_node"))
    description  = args.get("description", "Custom ROS2 node generated by Isaac Assist")
    node_type    = args.get("node_type", "generic")   # "classify_objects" | "generic"
    input_topic  = args.get("input_topic",  "/input")
    output_topic = args.get("output_topic", "/output")
    maintainer   = args.get("maintainer",   "Isaac Assist")
    email        = args.get("maintainer_email", "isaac@omniverse.local")
    version      = args.get("version",      "0.1.0")

    pkg_name   = node_name
    class_name = _to_class_name(node_name)

    # Determine package directory
    scene_dir = await _get_scene_dir()
    pkg_dir   = scene_dir / "ros2_nodes" / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    ctx = {
        "node_name":           node_name,
        "class_name":          class_name,
        "package_name":        pkg_name,
        "description":         description,
        "input_topic":         input_topic,
        "output_topic":        output_topic,
        "default_input_topic": input_topic,
        "default_output_topic": output_topic,
        "maintainer":          maintainer,
        "maintainer_email":    email,
        "version":             version,
    }

    # --- Choose node template ---
    if node_type == "classify_objects":
        node_src = _CLASSIFY_OBJECTS_NODE
        extra_depends = (
            "  <depend>cv_bridge</depend>\n"
            "  <depend>python3-torch</depend>\n"
            "  <depend>python3-torchvision</depend>\n"
        )
    elif node_type == "isaac_ros_object_detection":
        node_src = _render(_OBJECT_DETECTION_NODE, ctx)
        extra_depends = (
            "  <depend>cv_bridge</depend>\n"
            "  <depend>vision_msgs</depend>\n"
        )
    elif node_type == "isaac_ros_pose_estimation":
        node_src = _render(_POSE_ESTIMATION_NODE, ctx)
        extra_depends = (
            "  <depend>cv_bridge</depend>\n"
            "  <depend>vision_msgs</depend>\n"
            "  <depend>message_filters</depend>\n"
            "  <depend>python3-numpy</depend>\n"
        )
    elif node_type == "vla_action_inference":
        node_src = _render(_VLA_ACTION_NODE, ctx)
        extra_depends = (
            "  <depend>cv_bridge</depend>\n"
            "  <depend>geometry_msgs</depend>\n"
        )
    elif node_type == "semantic_segmentation":
        node_src = _render(_SEMANTIC_SEGMENTATION_NODE, ctx)
        extra_depends = (
            "  <depend>cv_bridge</depend>\n"
            "  <depend>python3-numpy</depend>\n"
            "  <depend>vision_msgs</depend>\n"
        )
    elif node_type == "cumotion_manipulation":
        # Extra context for manipulation template
        ctx["planning_group"]    = args.get("planning_group",    "ur_manipulator")
        ctx["end_effector_link"] = args.get("end_effector_link", "wrist_3_link")
        node_src = _render(_CUMOTION_MANIPULATION_NODE, ctx)
        extra_depends = (
            "  <depend>geometry_msgs</depend>\n"
            "  <depend>moveit_msgs</depend>\n"
            "  <depend>moveit_ros_planning_interface</depend>\n"
            "  <depend>isaac_ros_goal_setter_interfaces</depend>\n"
        )
    elif node_type == "world_collision_manager":
        ctx["world_config_path"] = args.get(
            "world_config_path",
            str(Path(args.get("output_topic", "/tmp")) / "world_config.yaml"),
        )
        node_src = _render_world_manager(ctx)
        extra_depends = (
            "  <depend>geometry_msgs</depend>\n"
            "  <depend>std_srvs</depend>\n"
            "  <depend>tf2_ros</depend>\n"
            "  <depend>visualization_msgs</depend>\n"
            "  <depend>python3-yaml</depend>\n"
        )
    else:
        node_src = _render(_GENERIC_NODE, ctx)
        extra_depends = ""

    entry_points_str = f'            "{node_name} = {pkg_name}.{node_name}:main",'

    ctx["extra_depends"] = extra_depends
    ctx["entry_points"]  = entry_points_str

    # --- Write files ---
    files_written: list[str] = []

    def _write(rel: str, content: str) -> None:
        path = pkg_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        files_written.append(str(path.relative_to(scene_dir.parent.parent)))  # relative to workspace/

    _write(f"{pkg_name}/{node_name}.py",            node_src)
    _write(f"{pkg_name}/__init__.py",               "")
    _write("setup.py",                              _render(_SETUP_PY, ctx))
    _write("package.xml",                           _render(_PACKAGE_XML, ctx))
    _write("setup.cfg",                             _render(_SETUP_CFG, ctx))
    _write(f"resource/{pkg_name}",                  "")
    _write(f"launch/{node_name}.launch.py",         _render(_LAUNCH_PY, ctx))
    _write("test/test_copyright.py",
           "# Copyright test placeholder\n")

    rel_pkg = str(pkg_dir.relative_to(_WORKSPACE.parent))

    return {
        "status":        "scaffolded",
        "package_name":  pkg_name,
        "node_name":     node_name,
        "class_name":    class_name,
        "node_type":     node_type,
        "package_dir":   str(pkg_dir),
        "files_written": files_written,
        "build_commands": [
            f"cd ~/ros2_ws",
            f"ln -sfn {pkg_dir} src/{pkg_name}",
            f"colcon build --packages-select {pkg_name}",
            f"source install/setup.bash",
        ],
        "run_command":   f"ros2 run {pkg_name} {node_name}",
        "launch_command": f"ros2 launch {pkg_name} {node_name}.launch.py",
        "message": (
            f"Package '{pkg_name}' scaffolded at {rel_pkg}. "
            f"Symlink into ~/ros2_ws/src and run `colcon build --packages-select {pkg_name}` to build."
        ),
    }


async def handle_launch_ros2_node(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch a previously scaffolded ROS2 node via ros2 run or ros2 launch."""
    import asyncio
    from .ros2_autonomy_tools import _LAUNCHED_PROCESSES  # shared registry

    package_name = args.get("package_name", "")
    node_name    = args.get("node_name", "")
    use_launch   = args.get("use_launch", False)
    extra_params = args.get("parameters", {})

    if not package_name or not node_name:
        return {"status": "error", "message": "package_name and node_name are required"}

    key = f"{package_name}/{node_name}"
    if key in _LAUNCHED_PROCESSES:
        proc = _LAUNCHED_PROCESSES[key]
        if proc["process"].returncode is None:
            return {"status": "already_running", "key": key}

    if use_launch:
        cmd = ["ros2", "launch", package_name, f"{node_name}.launch.py"]
    else:
        cmd = ["ros2", "run", package_name, node_name]

    # Append --ros-args params
    if extra_params:
        cmd.append("--ros-args")
        for k, v in extra_params.items():
            cmd += ["-p", f"{k}:={v}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _LAUNCHED_PROCESSES[key] = {
        "process":   proc,
        "pid":       proc.pid,
        "cmd":       " ".join(cmd),
        "type":      "ros2_node",
        "package":   package_name,
        "node":      node_name,
    }

    return {
        "status":      "launched",
        "key":         key,
        "pid":         proc.pid,
        "cmd":         " ".join(cmd),
        "message":     f"Launched {key} (PID {proc.pid}). Use list_launched / stop_launched to manage.",
    }
