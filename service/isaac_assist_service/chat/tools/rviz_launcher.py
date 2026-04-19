"""
rviz_launcher.py
----------------
Launch RViz2 with an auto-generated config based on active ROS2 topics.

Topic patterns are matched to RViz2 display types:
  /*/rgb, /*/image_raw  → Image display
  /*/depth              → Image display (depth colormap)
  /scan                 → LaserScan
  /points, /*/points    → PointCloud2
  /odom                 → Odometry
  /tf                   → TF
  /map                  → Map
  /*/camera_info        → skipped (paired with Image)

The generated .rviz YAML is saved to workspace/rviz_configs/ and
rviz2 is launched as a managed subprocess.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


async def _get_scene_name() -> str:
    """Get the current USD scene name from Kit RPC (e.g. 'carter_warehouse')."""
    try:
        from . import kit_tools
        ctx = await kit_tools.get_stage_context(full=False)
        stage_url = ctx.get("stage", {}).get("stage_url", "")
        if stage_url:
            # Extract filename without extension from URL/path
            # e.g. "omniverse://localhost/.../carter_warehouse.usd" → "carter_warehouse"
            basename = os.path.basename(stage_url)
            name, _ = os.path.splitext(basename)
            if name:
                # Sanitize for filesystem
                return re.sub(r'[^\w\-]', '_', name)
    except Exception as e:
        logger.debug(f"[RViz2] Could not get scene name: {e}")
    return "scene"


async def _detect_tf_fixed_frame() -> str:
    """Detect the correct Fixed Frame by querying the OmniGraph TF publisher's parentPrim.

    Isaac Sim's ROS2PublishTransformTree uses the prim *name* (not full path)
    as the TF parent frame_id. For example, parentPrim="/World/NovaCarter" 
    publishes frame_id="NovaCarter".

    Falls back to checking live /tf data, then to 'World'.
    """
    # Strategy 1: Query OmniGraph for a PublishTransformTree node's parentPrim
    try:
        from . import kit_tools
        result = await kit_tools.exec_sync(
            'import omni.graph.core as og\n'
            'import omni.usd\n'
            'stage = omni.usd.get_context().get_stage()\n'
            'found = []\n'
            'for prim in stage.Traverse():\n'
            '    if prim.GetTypeName() == "OmniGraphNode":\n'
            '        node = og.get_node_by_path(str(prim.GetPath()))\n'
            '        if node and node.get_type_name() == "isaacsim.ros2.bridge.ROS2PublishTransformTree":\n'
            '            for attr in node.get_attributes():\n'
            '                if attr.get_name() == "inputs:parentPrim":\n'
            '                    val = attr.get()\n'
            '                    if val:\n'
            '                        p = str(val[0]) if isinstance(val, (list, tuple)) else str(val)\n'
            '                        found.append(p)\n'
            'if found:\n'
            '    print(found[0])\n'
            'else:\n'
            '    print("")\n'
        )
        if result and result.get("success"):
            prim_path = result.get("output", "").strip()
            if prim_path:
                # TF frame_id is the prim name (last path component)
                frame_id = prim_path.rstrip("/").split("/")[-1]
                if frame_id:
                    logger.info(f"[RViz2] Detected TF fixed frame from OG: {frame_id} (parentPrim={prim_path})")
                    return frame_id
    except Exception as e:
        logger.debug(f"[RViz2] OG query for TF parent failed: {e}")

    # Strategy 2: Check live /tf topic for the root frame_id
    try:
        proc = await asyncio.create_subprocess_exec(
            "ros2", "topic", "echo", "/tf", "--once", "--no-arr",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        for line in stdout.decode().splitlines():
            line = line.strip()
            if line.startswith("frame_id:"):
                frame_id = line.split(":", 1)[1].strip().strip('"')
                if frame_id:
                    logger.info(f"[RViz2] Detected TF fixed frame from /tf topic: {frame_id}")
                    return frame_id
    except Exception as e:
        logger.debug(f"[RViz2] /tf echo fallback failed: {e}")

    logger.warning("[RViz2] Could not detect TF fixed frame, falling back to 'World'")
    return "World"

# ── Singleton process registry ──────────────────────────────────────────────
_rviz_proc: Optional[asyncio.subprocess.Process] = None
_rviz_config_path: Optional[str] = None

# ── Workspace path ──────────────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"
_RVIZ_DIR = _WORKSPACE / "rviz_configs"

# ── Topic-to-display mapping rules ──────────────────────────────────────────
# Each rule: (compiled_regex_pattern, rviz_display_class, extra_config_fn)

def _image_config(topic: str) -> dict:
    """Config for Image display."""
    return {
        "Class": "rviz_default_plugins/Image",
        "Name": _display_name(topic),
        "Enabled": True,
        "Value": True,
        "Topic": {
            "Value": topic,
            "Depth": 5,
            "History Policy": "Keep Last",
            "Reliability Policy": "Best Effort",
            "Durability Policy": "Volatile",
        },
        "Max Value": 1.0,
        "Min Value": 0.0,
        "Median window": 5,
        "Normalize Range": True,
    }


def _depth_config(topic: str) -> dict:
    """Config for depth Image display."""
    cfg = _image_config(topic)
    cfg["Name"] = _display_name(topic, suffix="Depth")
    cfg["Max Value"] = 10.0
    cfg["Normalize Range"] = True
    return cfg


def _laserscan_config(topic: str) -> dict:
    return {
        "Class": "rviz_default_plugins/LaserScan",
        "Name": _display_name(topic),
        "Enabled": True,
        "Value": True,
        "Topic": {
            "Value": topic,
            "Depth": 5,
            "History Policy": "Keep Last",
            "Reliability Policy": "Best Effort",
            "Durability Policy": "Volatile",
        },
        "Size (m)": 0.02,
        "Style": "Flat Squares",
        "Color Transformer": "Intensity",
    }


def _pointcloud_config(topic: str) -> dict:
    return {
        "Class": "rviz_default_plugins/PointCloud2",
        "Name": _display_name(topic),
        "Enabled": True,
        "Value": True,
        "Topic": {
            "Value": topic,
            "Depth": 5,
            "History Policy": "Keep Last",
            "Reliability Policy": "Best Effort",
            "Durability Policy": "Volatile",
        },
        "Size (Pixels)": 3,
        "Style": "Flat Squares",
        "Color Transformer": "FlatColor",
    }


def _odometry_config(topic: str) -> dict:
    return {
        "Class": "rviz_default_plugins/Odometry",
        "Name": _display_name(topic),
        "Enabled": True,
        "Value": True,
        "Topic": {
            "Value": topic,
            "Depth": 5,
            "History Policy": "Keep Last",
            "Reliability Policy": "Best Effort",
            "Durability Policy": "Volatile",
        },
        "Keep": 100,
        "Shape": "Arrow",
    }


def _map_config(topic: str) -> dict:
    return {
        "Class": "rviz_default_plugins/Map",
        "Name": _display_name(topic),
        "Enabled": True,
        "Value": True,
        "Topic": {
            "Value": topic,
            "Depth": 5,
            "History Policy": "Keep Last",
            "Reliability Policy": "Reliable",
            "Durability Policy": "Transient Local",
        },
        "Alpha": 0.7,
        "Draw Behind": False,
    }


def _tf_config(_topic: str) -> dict:
    return {
        "Class": "rviz_default_plugins/TF",
        "Name": "TF",
        "Enabled": True,
        "Value": True,
        "Show Arrows": True,
        "Show Axes": True,
        "Show Names": True,
        "Update Interval": 0,
    }


# Pattern rules: (regex matching topic name, config_builder_fn)
# Order matters — first match wins.
_TOPIC_RULES: List[Tuple[re.Pattern, Any]] = [
    (re.compile(r"/camera_info$"),  None),           # skip — paired with Image
    (re.compile(r"/(rgb|image_raw)$"), _image_config),
    (re.compile(r"/depth$"),        _depth_config),
    (re.compile(r"^/scan$"),        _laserscan_config),
    (re.compile(r"/point_cloud$|/points$"), _pointcloud_config),
    (re.compile(r"/odom$"),         _odometry_config),
    (re.compile(r"^/tf$"),          _tf_config),
    (re.compile(r"^/map$"),         _map_config),
]


def _display_name(topic: str, suffix: str = "") -> str:
    """Generate a human-readable display name from a topic path."""
    parts = [p for p in topic.strip("/").split("/") if p]
    name = " / ".join(parts).title()
    if suffix:
        name = f"{name} ({suffix})"
    return name


# ── Config generation ───────────────────────────────────────────────────────

def build_rviz_config(
    topics: List[str],
    fixed_frame: str = "odom",
) -> Tuple[dict, List[dict]]:
    """
    Build a .rviz YAML config dict from a list of active topic names.

    Returns (config_dict, display_summary_list).
    """
    displays = []
    summary = []
    tf_added = False

    for topic in sorted(topics):
        matched = False
        for pattern, builder in _TOPIC_RULES:
            if pattern.search(topic):
                matched = True
                if builder is None:
                    # skip rule (e.g. camera_info)
                    break
                # Special: only add TF once
                if builder is _tf_config:
                    if tf_added:
                        break
                    tf_added = True
                cfg = builder(topic)
                displays.append(cfg)
                summary.append({
                    "topic": topic,
                    "display_type": cfg["Class"],
                    "name": cfg["Name"],
                })
                break

    # Always add a Grid display for spatial reference
    displays.insert(0, {
        "Class": "rviz_default_plugins/Grid",
        "Name": "Grid",
        "Enabled": True,
        "Value": True,
        "Cell Count": 20,
        "Cell Size": 1,
        "Line Style": {"Line Width": 0.03, "Value": "Lines"},
        "Plane": "XY",
        "Plane Cell Count": 10,
        "Color": "160; 160; 164",
    })

    config = {
        "Panels": [
            {
                "Class": "rviz_common/Displays",
                "Name": "Displays",
                "Help Height": 78,
            },
        ],
        "Visualization Manager": {
            "Class": "",
            "Displays": displays,
            "Enabled": True,
            "Global Options": {
                "Background Color": "48; 48; 48",
                "Fixed Frame": fixed_frame,
                "Frame Rate": 30,
            },
            "Parameters": {
                "use_sim_time": True,
            },
            "Name": "root",
            "Tools": [
                {"Class": "rviz_default_plugins/MoveCamera"},
                {"Class": "rviz_default_plugins/FocusCamera"},
            ],
            "Value": True,
        },
        "Window Geometry": {
            "Width": 1920,
            "Height": 1080,
        },
    }

    return config, summary


def save_rviz_config(config: dict, name: str = "auto") -> str:
    """Save a .rviz YAML file and return the absolute path."""
    _RVIZ_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{ts}.rviz"
    path = _RVIZ_DIR / filename
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    logger.info(f"[RViz2] Config saved → {path}")
    return str(path)


# ── Process management ──────────────────────────────────────────────────────

async def launch_rviz2_process(config_path: str) -> int:
    """Launch rviz2 as a subprocess. Returns PID."""
    global _rviz_proc, _rviz_config_path

    # Kill existing instance if running
    await stop_rviz2_process()

    _rviz_proc = await asyncio.create_subprocess_exec(
        "rviz2", "-d", config_path,
        "--ros-args", "-p", "use_sim_time:=true",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _rviz_config_path = config_path
    logger.info(f"[RViz2] Launched PID {_rviz_proc.pid} with config {config_path}")
    return _rviz_proc.pid


async def stop_rviz2_process() -> Optional[int]:
    """Stop the managed rviz2 process. Returns the old PID or None."""
    global _rviz_proc, _rviz_config_path
    if _rviz_proc is None:
        return None
    old_pid = _rviz_proc.pid
    try:
        _rviz_proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(_rviz_proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _rviz_proc.kill()
            await _rviz_proc.wait()
        logger.info(f"[RViz2] Stopped PID {old_pid}")
    except ProcessLookupError:
        logger.info(f"[RViz2] PID {old_pid} already exited")
    _rviz_proc = None
    _rviz_config_path = None
    return old_pid


# ── Tool handlers ───────────────────────────────────────────────────────────

async def handle_launch_rviz2(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tool handler for launch_rviz2.

    1. Discover active topics via ros_mcp_tools
    2. Build .rviz config
    3. Save config file
    4. Launch rviz2 subprocess
    """
    extra_topics = args.get("extra_topics", [])
    explicit_frame = args.get("fixed_frame", "")

    # 1. Discover topics
    try:
        from .ros_mcp_tools import handle_ros2_list_topics
        topic_result = await handle_ros2_list_topics({})
    except Exception as e:
        return {"error": f"Failed to discover ROS2 topics: {e}"}

    if "error" in topic_result:
        return {"error": f"Topic discovery failed: {topic_result['error']}"}

    all_topics = topic_result.get("topics", [])
    if extra_topics:
        for t in extra_topics:
            if t not in all_topics:
                all_topics.append(t)

    if not all_topics:
        return {"error": "No active ROS2 topics found. Is the simulation running with ROS2 bridges?"}

    # 2. Detect fixed frame (auto-detect from TF publisher or use explicit)
    if explicit_frame:
        fixed_frame = explicit_frame
    else:
        fixed_frame = await _detect_tf_fixed_frame()

    # 3. Build config
    config, summary = build_rviz_config(all_topics, fixed_frame=fixed_frame)

    if not summary:
        return {
            "warning": "No topics matched known display types",
            "all_topics": all_topics,
        }

    # 4. Save config — named after the current USD scene
    scene_name = await _get_scene_name()
    config_path = save_rviz_config(config, name=scene_name)

    # 5. Launch rviz2
    try:
        pid = await launch_rviz2_process(config_path)
    except FileNotFoundError:
        return {
            "error": "rviz2 not found. Is ROS2 sourced? (source /opt/ros/jazzy/setup.bash)",
            "config_path": config_path,
            "displays": summary,
        }
    except Exception as e:
        return {
            "error": f"Failed to launch rviz2: {e}",
            "config_path": config_path,
            "displays": summary,
        }

    return {
        "status": "rviz2_launched",
        "pid": pid,
        "config_path": config_path,
        "fixed_frame": fixed_frame,
        "displays": summary,
        "display_count": len(summary),
        "total_topics_discovered": len(all_topics),
    }


async def handle_stop_rviz2(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Tool handler for stop_rviz2."""
    old_pid = await stop_rviz2_process()
    if old_pid is None:
        return {"status": "no_rviz2_running", "message": "No managed RViz2 instance is running."}
    return {"status": "stopped", "pid": old_pid}
