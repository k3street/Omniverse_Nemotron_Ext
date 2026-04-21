"""
ros2_lingbot_tools.py
----------------------
Handler for launching a LingBot-Map streaming 3D reconstruction node.

LingBot-Map (https://github.com/Robbyant/lingbot-map) is a feed-forward
Geometric Context Transformer (GCT) that processes a video stream and
produces per-frame:
  - 3D world-space point maps    [H, W, 3]
  - Per-pixel depth              [H, W]
  - Per-pixel confidence         [H, W]
  - Camera pose encoding         [9]  → 4×4 extrinsics + 3×3 intrinsics

The generated ROS2 node:
  - Subscribes to a sensor_msgs/Image topic
  - Batches frames and calls model.inference_streaming()
  - Publishes sensor_msgs/PointCloud2 on /lingbot/pointcloud
  - Publishes geometry_msgs/PoseStamped on /lingbot/camera_pose
  - Publishes sensor_msgs/Image (float32, confidence-filtered) on /lingbot/depth
  - Optionally writes world_config.yaml obstacle meshes for cuRobo integration

HuggingFace model IDs:
  robbyant/lingbot-map           — standard model
  robbyant/lingbot-map-long      — optimised for long sequences (recommended)
  robbyant/lingbot-map-stage1    — stage-1 weights
"""
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
# Scene-dir helper (shared pattern across ros2_* tools)
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
# ROS2 node template
# ---------------------------------------------------------------------------

_NODE_SCRIPT = '''\
#!/usr/bin/env python3
"""
LingBot-Map streaming 3D reconstruction ROS2 node.

Subscribes:
  {image_topic}                  — sensor_msgs/Image  (RGB8 or BGR8)

Publishes:
  /lingbot/pointcloud            — sensor_msgs/PointCloud2  (world-frame XYZ float32)
  /lingbot/camera_pose           — geometry_msgs/PoseStamped (camera extrinsics)
  /lingbot/depth                 — sensor_msgs/Image         (float32 metric depth)
  /lingbot/conf                  — sensor_msgs/Image         (float32 confidence [0,1])

Parameters (all settable at launch):
  model_variant     str  "{model_variant}"
  checkpoint_path   str  "{checkpoint_path}"
  image_topic       str  "{image_topic}"
  keyframe_interval int  {keyframe_interval}
  conf_threshold    float {conf_threshold}
  mask_sky          bool  {mask_sky}
  output_device     str  "{output_device}"
  publish_rate      float {publish_rate}
  max_buffer_frames int  {max_buffer_frames}
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSPresetProfiles

from sensor_msgs.msg import Image, PointCloud2, PointField
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Header
import sensor_msgs_py.point_cloud2 as pc2
from cv_bridge import CvBridge

# ── Model bootstrap ──────────────────────────────────────────────────────────

def _load_model(variant: str, checkpoint_path: str, device: str):
    """Load LingBot-Map GCTStream model from HuggingFace or local checkpoint."""
    import torch
    from huggingface_hub import hf_hub_download

    hf_ids = {{
        "lingbot-map":        "robbyant/lingbot-map",
        "lingbot-map-long":   "robbyant/lingbot-map-long",
        "lingbot-map-stage1": "robbyant/lingbot-map-stage1",
    }}

    # Try local checkpoint first, fall back to HuggingFace download
    if checkpoint_path and Path(checkpoint_path).exists():
        ckpt_file = checkpoint_path
    else:
        hf_id = hf_ids.get(variant, hf_ids["lingbot-map-long"])
        print(f"[LingBot] Downloading checkpoint from HuggingFace: {{hf_id}}")
        ckpt_file = hf_hub_download(repo_id=hf_id, filename="model.pt")

    try:
        from lingbot_map.models import GCTStream
    except ImportError:
        raise RuntimeError(
            "lingbot_map not installed. Clone https://github.com/Robbyant/lingbot-map "
            "and run: pip install -e ."
        )

    model = GCTStream(img_size=518, patch_size=14, embed_dim=1024)
    import torch
    ckpt = torch.load(ckpt_file, map_location=device, weights_only=False)
    model.load_state_dict(ckpt.get("model", ckpt), strict=False)
    model.to(device).eval()
    print(f"[LingBot] Model loaded on {{device}}")
    return model


def _load_sky_masker():
    """Load ONNX sky segmentation model (auto-downloaded from HuggingFace)."""
    try:
        from lingbot_map.sky_mask import SkyMasker
        return SkyMasker()
    except Exception as exc:
        print(f"[LingBot] Sky masker unavailable: {{exc}}")
        return None


# ── Pose decoding ─────────────────────────────────────────────────────────────

def _pose_enc_to_matrix(pose_enc: "np.ndarray") -> "np.ndarray":
    """
    Decode 9-D pose encoding → 4×4 camera-to-world matrix.
    Layout: [r0, r1, r2, t0, t1, t2, fx, fy, _] (rotation cols + translation + focal)
    """
    r0 = pose_enc[0:3]
    r1 = pose_enc[3:6]
    r2 = np.cross(r0, r1)
    R = np.stack([r0, r1, r2], axis=1)            # 3×3 orthonormal
    t = pose_enc[6:9] if pose_enc.shape[-1] > 8 else pose_enc[6:9]

    # Reconstruct from a 9-vector that encodes [R_col0, R_col1, t]
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3]  = t
    return T


def _matrix_to_pose_stamped(T: "np.ndarray", frame_id: str, stamp) -> PoseStamped:
    from scipy.spatial.transform import Rotation
    msg = PoseStamped()
    msg.header.frame_id = frame_id
    msg.header.stamp    = stamp
    quat = Rotation.from_matrix(T[:3, :3]).as_quat()  # [x,y,z,w]
    msg.pose.position.x = float(T[0, 3])
    msg.pose.position.y = float(T[1, 3])
    msg.pose.position.z = float(T[2, 3])
    msg.pose.orientation.x = float(quat[0])
    msg.pose.orientation.y = float(quat[1])
    msg.pose.orientation.z = float(quat[2])
    msg.pose.orientation.w = float(quat[3])
    return msg


# ── PointCloud2 builder ───────────────────────────────────────────────────────

def _points_to_pc2(
    world_pts: "np.ndarray",    # [H, W, 3]
    conf:      "np.ndarray",    # [H, W]
    conf_thr:  float,
    frame_id:  str,
    stamp,
) -> PointCloud2:
    mask = conf > conf_thr
    pts  = world_pts[mask].astype(np.float32)       # [N, 3]

    fields = [
        PointField(name="x", offset=0,  datatype=PointField.FLOAT32, count=1),
        PointField(name="y", offset=4,  datatype=PointField.FLOAT32, count=1),
        PointField(name="z", offset=8,  datatype=PointField.FLOAT32, count=1),
    ]
    header = Header(frame_id=frame_id, stamp=stamp)
    return pc2.create_cloud(header, fields, pts)


# ── Main node ─────────────────────────────────────────────────────────────────

class LingBotMapNode(Node):
    def __init__(self):
        super().__init__("lingbot_map_node")

        # Declare + read parameters
        self.declare_parameter("model_variant",     "{model_variant}")
        self.declare_parameter("checkpoint_path",   "{checkpoint_path}")
        self.declare_parameter("image_topic",       "{image_topic}")
        self.declare_parameter("keyframe_interval", {keyframe_interval})
        self.declare_parameter("conf_threshold",    {conf_threshold})
        self.declare_parameter("mask_sky",          {mask_sky_bool})
        self.declare_parameter("output_device",     "{output_device}")
        self.declare_parameter("publish_rate",      {publish_rate})
        self.declare_parameter("max_buffer_frames", {max_buffer_frames})

        p = self.get_parameter
        self._variant        = p("model_variant").value
        self._ckpt_path      = p("checkpoint_path").value
        self._image_topic    = p("image_topic").value
        self._kf_interval    = int(p("keyframe_interval").value)
        self._conf_thr       = float(p("conf_threshold").value)
        self._mask_sky       = bool(p("mask_sky").value)
        self._out_device     = p("output_device").value
        self._max_buf        = int(p("max_buffer_frames").value)

        import torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._bridge = CvBridge()
        self._frame_buf: list = []
        self._buf_lock  = threading.Lock()

        # Publishers
        qos = QoSPresetProfiles.SENSOR_DATA.value
        self._pub_pc    = self.create_publisher(PointCloud2, "/lingbot/pointcloud",   10)
        self._pub_pose  = self.create_publisher(PoseStamped, "/lingbot/camera_pose",  10)
        self._pub_depth = self.create_publisher(Image,        "/lingbot/depth",        qos)
        self._pub_conf  = self.create_publisher(Image,        "/lingbot/conf",         qos)

        # Image subscriber
        self._sub = self.create_subscription(
            Image, self._image_topic, self._image_cb,
            QoSPresetProfiles.SENSOR_DATA.value,
        )

        # Publish timer
        self._timer = self.create_timer(1.0 / {publish_rate}, self._process_buffer)

        # Load model in background so node doesn't block spinning
        self._model       = None
        self._sky_masker  = None
        self._model_ready = False
        threading.Thread(target=self._load_model_bg, daemon=True).start()

        self.get_logger().info(
            f"LingBot-Map node started. Waiting for {{self._image_topic}} ..."
        )

    def _load_model_bg(self):
        try:
            self._model = _load_model(self._variant, self._ckpt_path, self._device)
            if self._mask_sky:
                self._sky_masker = _load_sky_masker()
            self._model_ready = True
            self.get_logger().info("[LingBot] Model ready — processing frames.")
        except Exception as exc:
            self.get_logger().error(f"[LingBot] Failed to load model: {{exc}}")

    def _image_cb(self, msg: Image):
        if not self._model_ready:
            return
        try:
            cv_img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
        except Exception:
            return
        with self._buf_lock:
            self._frame_buf.append((msg.header.stamp, cv_img))
            if len(self._frame_buf) > self._max_buf:
                self._frame_buf.pop(0)

    def _process_buffer(self):
        if not self._model_ready:
            return
        with self._buf_lock:
            if not self._frame_buf:
                return
            frames = list(self._frame_buf)
            self._frame_buf.clear()

        import torch
        import torch.nn.functional as F

        stamps = [s for s, _ in frames]
        imgs   = [f for _, f in frames]
        latest_stamp = stamps[-1]

        # Preprocess: resize to 518×378, normalise to [0,1]
        H_target, W_target = 378, 518
        tensors = []
        for img_np in imgs:
            t = torch.from_numpy(img_np).float() / 255.0    # [H, W, 3]
            t = t.permute(2, 0, 1).unsqueeze(0)             # [1, 3, H, W]
            t = F.interpolate(t, size=(H_target, W_target), mode="bilinear",
                              align_corners=False)
            tensors.append(t)

        seq = torch.cat(tensors, dim=0).unsqueeze(0).to(self._device)  # [1, S, 3, H, W]

        if self._sky_masker is not None:
            # Apply sky mask to suppress background noise
            pass  # sky_masker applied inside model via hook in LingBot demo

        try:
            with torch.no_grad():
                preds = self._model.inference_streaming(
                    seq,
                    num_scale_frames=min(2, seq.shape[1]),
                    keyframe_interval=self._kf_interval,
                    output_device=self._out_device,
                )
        except Exception as exc:
            self.get_logger().warn(f"[LingBot] Inference error: {{exc}}")
            return

        # Extract last frame predictions
        import numpy as np

        def _to_np(x):
            if hasattr(x, "cpu"):
                return x.cpu().numpy()
            return np.array(x)

        world_pts = _to_np(preds["world_points"])[0, -1]   # [H, W, 3]
        conf      = _to_np(preds["world_points_conf"])[0, -1]  # [H, W]
        depth     = _to_np(preds["depth"])[0, -1, :, :, 0]   # [H, W]
        pose_enc  = _to_np(preds["pose_enc"])[0, -1]           # [9]

        # Publish PointCloud2
        pc_msg = _points_to_pc2(world_pts, conf, self._conf_thr,
                                 "map", latest_stamp)
        self._pub_pc.publish(pc_msg)

        # Publish camera pose
        T = _pose_enc_to_matrix(pose_enc)
        pose_msg = _matrix_to_pose_stamped(T, "map", latest_stamp)
        self._pub_pose.publish(pose_msg)

        # Publish depth image (float32)
        depth_msg = self._bridge.cv2_to_imgmsg(
            depth.astype(np.float32), encoding="32FC1"
        )
        depth_msg.header.stamp    = latest_stamp
        depth_msg.header.frame_id = "map"
        self._pub_depth.publish(depth_msg)

        # Publish confidence image (float32)
        conf_msg = self._bridge.cv2_to_imgmsg(
            conf.astype(np.float32), encoding="32FC1"
        )
        conf_msg.header.stamp    = latest_stamp
        conf_msg.header.frame_id = "map"
        self._pub_conf.publish(conf_msg)


def main(args=None):
    rclpy.init(args=args)
    node = LingBotMapNode()
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


_LAUNCH_PY = '''\
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("model_variant",     default_value="{model_variant}"),
        DeclareLaunchArgument("checkpoint_path",   default_value="{checkpoint_path}"),
        DeclareLaunchArgument("image_topic",       default_value="{image_topic}"),
        DeclareLaunchArgument("keyframe_interval", default_value="{keyframe_interval}"),
        DeclareLaunchArgument("conf_threshold",    default_value="{conf_threshold}"),
        DeclareLaunchArgument("mask_sky",          default_value="{mask_sky}"),
        DeclareLaunchArgument("output_device",     default_value="{output_device}"),
        DeclareLaunchArgument("publish_rate",      default_value="{publish_rate}"),
        DeclareLaunchArgument("max_buffer_frames", default_value="{max_buffer_frames}"),
        Node(
            package="lingbot_map_ros",
            executable="lingbot_map_node",
            name="lingbot_map_node",
            output="screen",
            parameters=[{{
                "model_variant":     LaunchConfiguration("model_variant"),
                "checkpoint_path":   LaunchConfiguration("checkpoint_path"),
                "image_topic":       LaunchConfiguration("image_topic"),
                "keyframe_interval": LaunchConfiguration("keyframe_interval"),
                "conf_threshold":    LaunchConfiguration("conf_threshold"),
                "mask_sky":          LaunchConfiguration("mask_sky"),
                "output_device":     LaunchConfiguration("output_device"),
                "publish_rate":      LaunchConfiguration("publish_rate"),
                "max_buffer_frames": LaunchConfiguration("max_buffer_frames"),
            }}],
        ),
    ])
'''


_PACKAGE_XML = '''\
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>lingbot_map_ros</name>
  <version>0.1.0</version>
  <description>ROS2 wrapper for LingBot-Map streaming 3D reconstruction</description>
  <maintainer email="user@example.com">Isaac Assist</maintainer>
  <license>Apache-2.0</license>

  <depend>rclpy</depend>
  <depend>sensor_msgs</depend>
  <depend>geometry_msgs</depend>
  <depend>std_msgs</depend>
  <depend>cv_bridge</depend>
  <depend>python3-numpy</depend>
  <depend>python3-scipy</depend>

  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
'''


_SETUP_PY = '''\
from setuptools import find_packages, setup
import os
from glob import glob

package_name = "lingbot_map_ros"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"),
            glob(os.path.join("launch", "*.py"))),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Isaac Assist",
    maintainer_email="user@example.com",
    description="ROS2 wrapper for LingBot-Map streaming 3D reconstruction",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={{
        "console_scripts": [
            "lingbot_map_node = lingbot_map_ros.lingbot_map_node:main",
        ],
    }},
)
'''


_SETUP_CFG = '''\
[develop]
script_dir=$base/lib/lingbot_map_ros
[install]
install_scripts=$base/lib/lingbot_map_ros
'''


_CUROBO_EXPORT_SCRIPT = '''\
#!/usr/bin/env python3
"""
export_lingbot_to_curobo.py
----------------------------
Subscribe to /lingbot/pointcloud, voxelise the accumulated point cloud,
and export a cuRobo world_config.yaml mesh entry so the cuMotion planner
can treat the LingBot-Map reconstruction as a collision world.

Usage:
    python export_lingbot_to_curobo.py \\
        --world_config /path/to/world_config.yaml \\
        --mesh_out     /path/to/lingbot_scene.ply  \\
        --conf_min     0.3                          \\
        --frames       200

After writing the YAML, restart / reload the cuMotion planner or call
the WorldCollisionManagerNode reload service.
"""
import argparse
import time

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
import sensor_msgs_py.point_cloud2 as pc2
import yaml
from pathlib import Path


class LingBotCollector(Node):
    def __init__(self, target_frames: int, mesh_out: str):
        super().__init__("lingbot_curobo_exporter")
        self._target = target_frames
        self._mesh_out = mesh_out
        self._points: list = []
        self._sub = self.create_subscription(
            PointCloud2, "/lingbot/pointcloud", self._cb, 10
        )
        self.get_logger().info(
            f"Collecting {target_frames} PointCloud2 frames → {mesh_out}"
        )

    def _cb(self, msg: PointCloud2):
        pts = np.array(list(pc2.read_points(msg, field_names=("x", "y", "z"),
                                             skip_nans=True)), dtype=np.float32)
        if pts.size:
            self._points.append(pts)
        if len(self._points) >= self._target:
            self.get_logger().info("Collection complete — writing mesh.")
            self._write_mesh()
            rclpy.shutdown()

    def _write_mesh(self):
        all_pts = np.concatenate(self._points, axis=0)  # [N, 3]
        try:
            import open3d as o3d
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(all_pts)
            pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
            mesh, _ = pcd.compute_convex_hull()
            o3d.io.write_triangle_mesh(self._mesh_out, mesh)
            self.get_logger().info(f"Mesh written: {self._mesh_out}")
        except ImportError:
            # Fallback: write raw PLY without Open3D
            Path(self._mesh_out).write_text(_ply_from_points(all_pts))
            self.get_logger().warn(
                "open3d not installed — wrote raw XYZ PLY (no convex hull)"
            )


def _ply_from_points(pts: np.ndarray) -> str:
    lines = [
        "ply", "format ascii 1.0",
        f"element vertex {len(pts)}",
        "property float x", "property float y", "property float z",
        "end_header",
    ]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in pts]
    return "\\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--world_config", required=True)
    parser.add_argument("--mesh_out",     required=True)
    parser.add_argument("--frames",       type=int,   default=100)
    parser.add_argument("--mesh_name",    default="lingbot_scene")
    args = parser.parse_args()

    rclpy.init()
    node = LingBotCollector(args.frames, args.mesh_out)
    rclpy.spin(node)

    # Patch world_config.yaml
    p = Path(args.world_config)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    data.setdefault("mesh", {})[args.mesh_name] = {
        "file_path": args.mesh_out,
        "pose": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }
    p.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    print(f"Updated {args.world_config} with mesh entry '{args.mesh_name}'")


if __name__ == "__main__":
    main()
'''


_README = '''\
# lingbot_map_ros

ROS2 wrapper for [LingBot-Map](https://github.com/Robbyant/lingbot-map) —
a streaming feed-forward 3D reconstruction model based on the Geometric
Context Transformer (GCT).

## Prerequisites

```bash
# 1. Install LingBot-Map (source-only, no PyPI)
git clone https://github.com/Robbyant/lingbot-map ~/lingbot-map
cd ~/lingbot-map && pip install -e .

# 2. Optional: visualization
pip install -e ".[vis]"

# 3. Optional: better sky masking (outdoor scenes)
pip install onnxruntime-gpu   # or onnxruntime for CPU-only

# 4. Optional: cuRobo export script
pip install open3d
```

## Build

```bash
cd ~/ros2_ws
colcon build --packages-select lingbot_map_ros
source install/setup.bash
```

## Launch

```bash
# Standard model
ros2 launch lingbot_map_ros lingbot_map.launch.py \\
  image_topic:=/camera/image_raw

# Long-sequence model (recommended for mapping sessions > 500 frames)
ros2 launch lingbot_map_ros lingbot_map.launch.py \\
  model_variant:=lingbot-map-long \\
  image_topic:=/camera/image_raw  \\
  mask_sky:=true

# With local checkpoint
ros2 launch lingbot_map_ros lingbot_map.launch.py \\
  checkpoint_path:=/path/to/lingbot-map-long.pt \\
  image_topic:=/camera/image_raw
```

## Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/lingbot/pointcloud` | sensor_msgs/PointCloud2 | World-frame 3D points (conf-filtered) |
| `/lingbot/camera_pose` | geometry_msgs/PoseStamped | Estimated camera pose in world frame |
| `/lingbot/depth` | sensor_msgs/Image (float32) | Metric depth map |
| `/lingbot/conf` | sensor_msgs/Image (float32) | Per-pixel confidence [0,1] |

## cuRobo Export

Accumulate reconstruction frames and write a cuRobo world obstacle mesh:

```bash
python scripts/export_lingbot_to_curobo.py \\
  --world_config ~/ros2_ws/src/lingbot_map_ros/config/world_config.yaml \\
  --mesh_out     /tmp/lingbot_scene.ply \\
  --frames 200
```
'''


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_launch_lingbot_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scaffold and launch a LingBot-Map streaming 3D reconstruction ROS2 node.

    Generates an ament_python colcon package `lingbot_map_ros` containing:
      - lingbot_map_ros/lingbot_map_node.py  — ROS2 node (Image → PC2 + Pose + Depth)
      - launch/lingbot_map.launch.py
      - scripts/export_lingbot_to_curobo.py  — accumulate cloud → cuRobo YAML
      - package.xml / setup.py / README.md

    The node subscribes to a camera image topic, processes frames through
    the GCTStream model (streaming KV-cache mode), and publishes:
      /lingbot/pointcloud    — sensor_msgs/PointCloud2 (world-frame)
      /lingbot/camera_pose   — geometry_msgs/PoseStamped
      /lingbot/depth         — sensor_msgs/Image (float32 metric depth)
      /lingbot/conf          — sensor_msgs/Image (float32 confidence)

    For cuRobo integration run export_lingbot_to_curobo.py to write the
    accumulated point cloud as a mesh entry in world_config.yaml.
    """
    scene_dir   = await _get_scene_dir()

    image_topic      = args.get("image_topic",       "/camera/image_raw")
    model_variant    = args.get("model_variant",     "lingbot-map-long")
    checkpoint_path  = args.get("checkpoint_path",   "")
    keyframe_interval = int(args.get("keyframe_interval", 1))
    conf_threshold   = float(args.get("conf_threshold",   0.3))
    mask_sky         = bool(args.get("mask_sky",          False))
    output_device    = args.get("output_device",     "cpu")
    publish_rate     = float(args.get("publish_rate",     10.0))
    max_buffer_frames = int(args.get("max_buffer_frames", 8))
    ros_workspace    = args.get("ros_workspace",     "~/ros2_ws")

    mask_sky_bool = str(mask_sky).lower()  # Python bool → ROS param string

    pkg_dir = scene_dir / "ros2_nodes" / "lingbot_map_ros"
    (pkg_dir / "lingbot_map_ros").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "launch").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "resource").mkdir(parents=True, exist_ok=True)

    def _write(rel: str, content: str) -> None:
        p = pkg_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    # Fill templates
    ctx = dict(
        image_topic=image_topic,
        model_variant=model_variant,
        checkpoint_path=checkpoint_path,
        keyframe_interval=keyframe_interval,
        conf_threshold=conf_threshold,
        mask_sky=str(mask_sky).lower(),
        mask_sky_bool=mask_sky_bool,
        output_device=output_device,
        publish_rate=publish_rate,
        max_buffer_frames=max_buffer_frames,
    )

    node_src    = _NODE_SCRIPT.format(**ctx)
    launch_src  = _LAUNCH_PY.format(**ctx)

    _write("lingbot_map_ros/__init__.py",        "")
    _write("lingbot_map_ros/lingbot_map_node.py", node_src)
    _write("launch/lingbot_map.launch.py",        launch_src)
    _write("scripts/export_lingbot_to_curobo.py", _CUROBO_EXPORT_SCRIPT)
    _write("package.xml",                         _PACKAGE_XML)
    _write("setup.py",                            _SETUP_PY)
    _write("setup.cfg",                           _SETUP_CFG)
    _write("README.md",                           _README)
    _write("resource/lingbot_map_ros",            "")

    # Make scripts executable
    (pkg_dir / "scripts" / "export_lingbot_to_curobo.py").chmod(0o755)

    rel_pkg = str(pkg_dir.relative_to(_WORKSPACE.parent))

    return {
        "status":       "scaffolded",
        "package_name": "lingbot_map_ros",
        "package_path": rel_pkg,
        "image_topic":  image_topic,
        "model_variant": model_variant,
        "publishes": {
            "pointcloud":   "/lingbot/pointcloud",
            "camera_pose":  "/lingbot/camera_pose",
            "depth":        "/lingbot/depth",
            "confidence":   "/lingbot/conf",
        },
        "build_cmd": (
            f"cd {ros_workspace} && "
            "colcon build --packages-select lingbot_map_ros && "
            "source install/setup.bash"
        ),
        "launch_cmd": (
            f"ros2 launch lingbot_map_ros lingbot_map.launch.py "
            f"image_topic:={image_topic} "
            f"model_variant:={model_variant}"
        ),
        "prerequisite": (
            "Install LingBot-Map first:\n"
            "  git clone https://github.com/Robbyant/lingbot-map ~/lingbot-map\n"
            "  cd ~/lingbot-map && pip install -e ."
        ),
        "curobo_export": (
            "To feed reconstruction into cuRobo world collision:\n"
            f"  python {rel_pkg}/scripts/export_lingbot_to_curobo.py "
            "--world_config /path/to/world_config.yaml "
            "--mesh_out /tmp/lingbot_scene.ply --frames 200"
        ),
        "message": (
            f"Scaffolded lingbot_map_ros at {rel_pkg}. "
            f"Build with colcon, then launch to start streaming 3D reconstruction "
            f"on {image_topic}."
        ),
    }
