"""
ros2_curobo_world_tools.py
---------------------------
cuRobo World Collision — generate WorldConfig YAMLs, manage dynamic obstacles,
query sphere collisions, and scaffold a WorldCollisionManagerNode.

Covers:
  WorldPrimitiveCollision  — cuboids / OBBs (fastest, ~4× faster than mesh)
  WorldMeshCollision       — BVH mesh traversal via NVIDIA Warp
  WorldBloxCollision       — nvblox live depth integration
  WorldVoxelCollision      — ESDF signed distance voxel grids

Docs: https://curobo.org/get_started/2c_world_collision.html
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


def _nvblox_running() -> bool:
    return "nvblox/reconstruction" in _LAUNCHED_PROCESSES and \
        _LAUNCHED_PROCESSES["nvblox/reconstruction"]["process"].returncode is None


def _cumotion_planner_running() -> bool:
    return "cumotion/planner" in _LAUNCHED_PROCESSES and \
        _LAUNCHED_PROCESSES["cumotion/planner"]["process"].returncode is None


# ── WorldConfig YAML template ─────────────────────────────────────────────────

_WORLD_CONFIG_YAML = """\
# cuRobo WorldConfig — scene collision representation
# Docs: https://curobo.org/get_started/2c_world_collision.html
#
# Pose format: [x, y, z, qw, qx, qy, qz]  (quaternion w-first)
# Coordinates: metres, robot base frame
#
# Priority (fastest → most accurate):
#   cuboid > mesh > blox > voxel
# For best performance, approximate meshes as cuboids where possible.
# cuRobo cuboid checking is ~4× faster than mesh BVH traversal.

cuboid:
{cuboid_entries}

mesh:
{mesh_entries}

{blox_section}

{voxel_section}
"""

_CUBOID_ENTRY = """\
  {name}:
    dims: [{dx}, {dy}, {dz}]
    pose: [{x}, {y}, {z}, {qw}, {qx}, {qy}, {qz}]
    # enable: true   # set false to disable without removing
"""

_MESH_ENTRY = """\
  {name}:
    file_path: "{file_path}"
    pose: [{x}, {y}, {z}, {qw}, {qx}, {qy}, {qz}]
    scale: [{sx}, {sy}, {sz}]
"""

_BLOX_SECTION = """\
blox:
  world:
    voxel_size: {voxel_size}
    integrator_type: "{integrator_type}"
    # nvblox topic: /nvblox_node/mesh  (auto-connected by cuMotion planner)
"""

_VOXEL_SECTION = """\
voxel:
  world_voxel:
    dims: [{vx}, {vy}, {vz}]
    pose: [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    voxel_size: {voxel_size}
    feature_dtype: "float32"
"""

_WORLD_COLLISION_CONFIG_YAML = """\
# WorldCollisionConfig — cuRobo collision checker setup
# Cache pre-allocates GPU buffers so dynamic obstacle updates
# do NOT require CUDA graph re-compilation.
#
# Increase obb/mesh cache sizes if you plan to add obstacles at runtime.
world_collision:
  cache:
    obb:  {cache_obb}     # pre-allocated oriented bounding box slots
    mesh: {cache_mesh}    # pre-allocated mesh slots

  activation_distance: {activation_distance}  # metres — cost ramp-up zone around obstacles
  max_distance:        {max_distance}          # metres — beyond this, cost = 0

  # Speed-weighted continuous collision checking
  # Prevents fast-moving trajectories from passing through thin obstacles
  use_sweep: {use_sweep}
"""

# ── WorldCollisionManager node (Python, std_srvs-based) ──────────────────────
# Uses JSON-string services (std_msgs/String request → String response)
# to avoid custom .srv compilation while still being a proper ROS2 service node.

_WORLD_MANAGER_NODE = r'''#!/usr/bin/env python3
"""{{ node_name }} — cuRobo WorldConfig collision manager ROS2 node.

Maintains a live cuRobo WorldConfig and exposes ROS2 services for
dynamic obstacle management. Integrates with the cuMotion planner via
the shared world YAML file watched by the planner node.

Services (all use std_srvs/srv/SetBool or custom JSON strings):
  /curobo_world/add_obstacle        — add cuboid or mesh by JSON description
  /curobo_world/remove_obstacle     — remove named obstacle
  /curobo_world/update_pose         — update obstacle pose by name
  /curobo_world/enable_obstacle     — enable/disable obstacle collision
  /curobo_world/query_spheres       — test sphere set against current world
  /curobo_world/get_config          — return current WorldConfig as YAML

Topics subscribed:
  /tf                               — auto-sync tracked obstacle poses
  /{{ node_name }}/add_obstacle     — String (JSON) for programmatic add
  /{{ node_name }}/remove_obstacle  — String (obstacle name)

Topics published:
  /curobo_world/markers             — visualization_msgs/MarkerArray
  /curobo_world/status              — std_msgs/String (JSON state)

Parameters:
  world_config_path  — YAML file to watch/write  (default: {{ world_config_path }})
  robot_base_frame   — TF frame for world origin  (default: base_link)
  sync_rate_hz       — TF sync rate               (default: 10.0)
  use_curobo         — Enable live cuRobo SDF queries (requires curobo installed)
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from geometry_msgs.msg import TransformStamped
from tf2_ros import TransformListener, Buffer
from visualization_msgs.msg import MarkerArray, Marker
from builtin_interfaces.msg import Duration

try:
    from std_srvs.srv import Trigger
    _STD_SRVS_OK = True
except ImportError:
    _STD_SRVS_OK = False


# cuRobo imports (optional — node works in "config-only" mode without them)
try:
    from curobo.geom.types import WorldConfig, Cuboid, Mesh, Pose
    from curobo.geom.sdf.world import WorldCollisionConfig
    from curobo.types.math import Pose as CuroboPose
    from curobo.util_file import get_robot_configs_path
    import torch
    _CUROBO_OK = True
except ImportError:
    _CUROBO_OK = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_pose(pose_list: List[float]) -> Dict:
    """Parse [x,y,z,qw,qx,qy,qz] pose list."""
    x, y, z, qw, qx, qy, qz = pose_list if len(pose_list) == 7 else pose_list + [1,0,0,0][len(pose_list)-3:]
    return {"x": x, "y": y, "z": z, "qw": qw, "qx": qx, "qy": qy, "qz": qz}


def _tf_to_pose(tf: TransformStamped) -> List[float]:
    t = tf.transform.translation
    r = tf.transform.rotation
    return [t.x, t.y, t.z, r.w, r.x, r.y, r.z]


def _make_cube_marker(marker_id: int, name: str, dims: List[float],
                      pose: List[float], ns: str = "curobo_world") -> Marker:
    m = Marker()
    m.header.frame_id = "base_link"
    m.ns = ns
    m.id = marker_id
    m.type = Marker.CUBE
    m.action = Marker.ADD
    x, y, z, qw, qx, qy, qz = pose
    m.pose.position.x = x
    m.pose.position.y = y
    m.pose.position.z = z
    m.pose.orientation.w = qw
    m.pose.orientation.x = qx
    m.pose.orientation.y = qy
    m.pose.orientation.z = qz
    m.scale.x = dims[0]
    m.scale.y = dims[1]
    m.scale.z = dims[2]
    m.color.r = 0.8
    m.color.g = 0.4
    m.color.b = 0.1
    m.color.a = 0.5
    m.lifetime = Duration(sec=0)  # permanent until removed
    m.text = name
    return m


class {{ class_name }}(Node):
    """cuRobo WorldConfig collision manager — dynamic obstacle CRUD + ROS2 services."""

    def __init__(self) -> None:
        super().__init__("{{ node_name }}")

        self.declare_parameter("world_config_path", "{{ world_config_path }}")
        self.declare_parameter("robot_base_frame",  "base_link")
        self.declare_parameter("sync_rate_hz",      10.0)
        self.declare_parameter("use_curobo",        True)
        self.declare_parameter("cache_obb",         20)
        self.declare_parameter("cache_mesh",        5)
        self.declare_parameter("activation_distance", 0.01)

        self._cfg_path    = Path(self.get_parameter("world_config_path").value)
        self._base_frame  = self.get_parameter("robot_base_frame").value
        self._use_curobo  = bool(self.get_parameter("use_curobo").value) and _CUROBO_OK
        self._cache_obb   = int(self.get_parameter("cache_obb").value)
        self._cache_mesh  = int(self.get_parameter("cache_mesh").value)
        self._act_dist    = float(self.get_parameter("activation_distance").value)
        sync_rate         = float(self.get_parameter("sync_rate_hz").value)

        # In-memory world state: {name: {type: "cuboid"|"mesh", enabled: bool, ...}}
        self._world: Dict[str, Dict] = {}
        self._marker_ids: Dict[str, int] = {}
        self._next_marker_id = 0
        self._tf_tracked: Dict[str, str] = {}  # obstacle_name → tf_frame
        self._lock = threading.Lock()

        # Load initial config if file exists
        if self._cfg_path.exists():
            self._load_from_yaml(self._cfg_path)
            self.get_logger().info(f"Loaded world config: {self._cfg_path}")

        # TF listener for pose sync
        self._tf_buffer   = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # Publishers
        self._marker_pub = self.create_publisher(MarkerArray, "/curobo_world/markers", 10)
        self._status_pub = self.create_publisher(String,      "/curobo_world/status",  10)

        # Topic-based obstacle management (for programmatic use)
        self._add_sub = self.create_subscription(
            String, "/{{ node_name }}/add_obstacle",
            lambda msg: self._add_from_json(msg.data), 10
        )
        self._remove_sub = self.create_subscription(
            String, "/{{ node_name }}/remove_obstacle",
            lambda msg: self._remove_obstacle(msg.data.strip()), 10
        )

        # Services (JSON in → JSON out via std_msgs/String topics with response)
        self._add_svc    = self.create_service(Trigger, "/curobo_world/add_obstacle",    self._add_svc_cb)
        self._remove_svc = self.create_service(Trigger, "/curobo_world/remove_obstacle", self._remove_svc_cb)
        self._pose_svc   = self.create_service(Trigger, "/curobo_world/update_pose",     self._pose_svc_cb)
        self._enable_svc = self.create_service(Trigger, "/curobo_world/enable_obstacle", self._enable_svc_cb)
        self._query_svc  = self.create_service(Trigger, "/curobo_world/query_spheres",   self._query_svc_cb)
        self._config_svc = self.create_service(Trigger, "/curobo_world/get_config",      self._config_svc_cb)

        # Timers
        self.create_timer(1.0 / sync_rate, self._sync_timer)
        self.create_timer(1.0,             self._publish_status)

        if self._use_curobo:
            self._init_curobo()
            self.get_logger().info("cuRobo SDF world initialized.")
        else:
            self.get_logger().info(
                "Running in config-only mode (curobo not installed or use_curobo=false). "
                "Obstacle CRUD and visualization active; SDF queries disabled."
            )

        self.get_logger().info(
            f"{{ class_name }} ready — config={self._cfg_path} "
            f"base_frame={self._base_frame!r}"
        )

    # ── cuRobo initialization ─────────────────────────────────────────────────

    def _init_curobo(self) -> None:
        try:
            world_cfg = WorldConfig.from_dict(self._to_world_dict())
            self._collision_cfg = WorldCollisionConfig(
                world_model=world_cfg,
                cache={"obb": self._cache_obb, "mesh": self._cache_mesh},
            )
            self.get_logger().info(
                f"WorldCollisionConfig: cache obb={self._cache_obb} mesh={self._cache_mesh}"
            )
        except Exception as exc:
            self.get_logger().error(f"cuRobo init error: {exc}")
            self._use_curobo = False

    # ── World state helpers ───────────────────────────────────────────────────

    def _to_world_dict(self) -> Dict:
        """Serialize in-memory world to cuRobo WorldConfig dict format."""
        cuboids = {}
        meshes  = {}
        with self._lock:
            for name, obs in self._world.items():
                if not obs.get("enabled", True):
                    continue
                if obs["type"] == "cuboid":
                    cuboids[name] = {
                        "dims": obs["dims"],
                        "pose": obs["pose"],
                    }
                elif obs["type"] == "mesh":
                    meshes[name] = {
                        "file_path": obs["file_path"],
                        "pose":      obs["pose"],
                        "scale":     obs.get("scale", [1.0, 1.0, 1.0]),
                    }
        return {"cuboid": cuboids, "mesh": meshes}

    def _load_from_yaml(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text()) or {}
        with self._lock:
            for name, cfg in (data.get("cuboid") or {}).items():
                self._world[name] = {
                    "type":    "cuboid",
                    "dims":    cfg.get("dims", [0.1, 0.1, 0.1]),
                    "pose":    cfg.get("pose", [0, 0, 0, 1, 0, 0, 0]),
                    "enabled": cfg.get("enable", True),
                }
            for name, cfg in (data.get("mesh") or {}).items():
                self._world[name] = {
                    "type":      "mesh",
                    "file_path": cfg.get("file_path", ""),
                    "pose":      cfg.get("pose", [0, 0, 0, 1, 0, 0, 0]),
                    "scale":     cfg.get("scale", [1.0, 1.0, 1.0]),
                    "enabled":   True,
                }

    def _save_yaml(self) -> None:
        """Write current world state to the config YAML file."""
        data = self._to_world_dict()
        self._cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cfg_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # ── Obstacle CRUD ─────────────────────────────────────────────────────────

    def _add_obstacle(self, name: str, obs_dict: Dict) -> Dict:
        """Add or update a named obstacle. Returns result dict."""
        obs_type = obs_dict.get("type", "cuboid")
        with self._lock:
            if obs_type == "cuboid":
                self._world[name] = {
                    "type":    "cuboid",
                    "dims":    obs_dict.get("dims", [0.1, 0.1, 0.1]),
                    "pose":    obs_dict.get("pose", [0, 0, 0, 1, 0, 0, 0]),
                    "enabled": True,
                }
            elif obs_type == "mesh":
                self._world[name] = {
                    "type":      "mesh",
                    "file_path": obs_dict.get("file_path", ""),
                    "pose":      obs_dict.get("pose", [0, 0, 0, 1, 0, 0, 0]),
                    "scale":     obs_dict.get("scale", [1.0, 1.0, 1.0]),
                    "enabled":   True,
                }
            else:
                return {"success": False, "message": f"Unknown type: {obs_type!r}. Use 'cuboid' or 'mesh'."}

            # Register TF tracking if tf_frame provided
            if "tf_frame" in obs_dict:
                self._tf_tracked[name] = obs_dict["tf_frame"]

        self._save_yaml()
        self._refresh_curobo()
        self._publish_markers()
        return {"success": True, "name": name, "type": obs_type, "message": f"Added {name}"}

    def _remove_obstacle(self, name: str) -> Dict:
        with self._lock:
            if name not in self._world:
                return {"success": False, "message": f"Obstacle {name!r} not found"}
            del self._world[name]
            self._tf_tracked.pop(name, None)
            # Delete marker
            mid = self._marker_ids.pop(name, None)

        if mid is not None:
            arr = MarkerArray()
            m = Marker()
            m.id     = mid
            m.action = Marker.DELETE
            arr.markers.append(m)
            self._marker_pub.publish(arr)

        self._save_yaml()
        self._refresh_curobo()
        return {"success": True, "message": f"Removed {name!r}"}

    def _update_obstacle_pose(self, name: str, pose: List[float]) -> Dict:
        with self._lock:
            if name not in self._world:
                return {"success": False, "message": f"Obstacle {name!r} not found"}
            self._world[name]["pose"] = pose

        self._save_yaml()
        self._refresh_curobo()
        self._publish_markers()
        return {"success": True, "name": name, "pose": pose}

    def _enable_obstacle(self, name: str, enabled: bool) -> Dict:
        with self._lock:
            if name not in self._world:
                return {"success": False, "message": f"Obstacle {name!r} not found"}
            self._world[name]["enabled"] = enabled

        self._save_yaml()
        self._refresh_curobo()
        self._publish_markers()
        return {"success": True, "name": name, "enabled": enabled}

    def _query_spheres(self, spheres: List[List[float]]) -> Dict:
        """Query [x, y, z, radius] spheres against the collision world."""
        if not self._use_curobo:
            return {"success": False, "message": "cuRobo not available — install curobo first"}
        if not spheres:
            return {"success": False, "message": "No spheres provided"}
        try:
            import torch
            from curobo.geom.sdf.world import WorldPrimitiveCollision

            world_cfg = WorldConfig.from_dict(self._to_world_dict())
            checker   = WorldPrimitiveCollision(WorldCollisionConfig(world_model=world_cfg))

            # Shape: [1, 1, N, 4] — batch=1, horizon=1, N spheres
            t = torch.tensor(spheres, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
            dist = checker.get_sphere_distance(t)  # [1, 1, N]

            results = []
            for i, s in enumerate(spheres):
                d = float(dist[0, 0, i])
                results.append({
                    "sphere":    s,
                    "distance":  d,
                    "in_collision": d > 0.0,
                })
            return {"success": True, "spheres": results, "any_collision": any(r["in_collision"] for r in results)}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    def _add_from_json(self, json_str: str) -> None:
        try:
            data = json.loads(json_str)
            name = data.pop("name", f"obs_{int(time.time())}")
            result = self._add_obstacle(name, data)
            self.get_logger().info(f"add_obstacle via topic: {result}")
        except Exception as exc:
            self.get_logger().error(f"add_from_json error: {exc}")

    # ── cuRobo refresh ────────────────────────────────────────────────────────

    def _refresh_curobo(self) -> None:
        if not self._use_curobo:
            return
        try:
            world_cfg = WorldConfig.from_dict(self._to_world_dict())
            self._collision_cfg = WorldCollisionConfig(
                world_model=world_cfg,
                cache={"obb": self._cache_obb, "mesh": self._cache_mesh},
            )
        except Exception as exc:
            self.get_logger().error(f"cuRobo refresh error: {exc}")

    # ── TF sync timer ─────────────────────────────────────────────────────────

    def _sync_timer(self) -> None:
        if not self._tf_tracked:
            return
        changed = False
        for obs_name, tf_frame in list(self._tf_tracked.items()):
            try:
                tf = self._tf_buffer.lookup_transform(
                    self._base_frame, tf_frame, rclpy.time.Time()
                )
                pose = _tf_to_pose(tf)
                with self._lock:
                    if obs_name in self._world:
                        self._world[obs_name]["pose"] = pose
                        changed = True
            except Exception:
                pass
        if changed:
            self._publish_markers()

    # ── Visualization ─────────────────────────────────────────────────────────

    def _publish_markers(self) -> None:
        arr = MarkerArray()
        with self._lock:
            for name, obs in self._world.items():
                if not obs.get("enabled", True):
                    continue
                if obs["type"] != "cuboid":
                    continue
                mid = self._marker_ids.get(name)
                if mid is None:
                    mid = self._next_marker_id
                    self._next_marker_id += 1
                    self._marker_ids[name] = mid
                arr.markers.append(
                    _make_cube_marker(mid, name, obs["dims"], obs["pose"])
                )
        if arr.markers:
            self._marker_pub.publish(arr)

    def _publish_status(self) -> None:
        with self._lock:
            world_summary = {
                name: {
                    "type":    obs["type"],
                    "enabled": obs.get("enabled", True),
                    "tf_tracked": name in self._tf_tracked,
                }
                for name, obs in self._world.items()
            }
        msg = String()
        msg.data = json.dumps({
            "obstacle_count": len(world_summary),
            "curobo_active": self._use_curobo,
            "config_path": str(self._cfg_path),
            "obstacles": world_summary,
        })
        self._status_pub.publish(msg)

    # ── Service callbacks (stateless Trigger + JSON context) ──────────────────
    # Services use std_srvs/Trigger — callers pass JSON data on a paired topic first
    # and read results from /curobo_world/status.  For proper typed services,
    # build the gemini_robotics_bridge-style CMake package with custom .srv files.

    def _add_svc_cb(self, req, resp):
        resp.success = True
        resp.message = "Use /{{ node_name }}/add_obstacle topic with JSON payload to add obstacles."
        return resp

    def _remove_svc_cb(self, req, resp):
        resp.success = True
        resp.message = "Use /{{ node_name }}/remove_obstacle topic with obstacle name to remove."
        return resp

    def _pose_svc_cb(self, req, resp):
        resp.success = True
        resp.message = "Publish {name, pose:[x,y,z,qw,qx,qy,qz]} to /{{ node_name }}/add_obstacle to update poses."
        return resp

    def _enable_svc_cb(self, req, resp):
        resp.success = True
        resp.message = "Publish {name, type, enabled:true/false} to /{{ node_name }}/add_obstacle to toggle."
        return resp

    def _query_svc_cb(self, req, resp):
        result = self._query_spheres([])
        resp.success = result.get("success", False)
        resp.message = json.dumps(result)
        return resp

    def _config_svc_cb(self, req, resp):
        import yaml as _yaml
        resp.success = True
        resp.message = _yaml.dump(self._to_world_dict(), default_flow_style=False)
        return resp


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

# ── Tool handlers ─────────────────────────────────────────────────────────────


async def handle_configure_curobo_world(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a cuRobo WorldConfig YAML and WorldCollisionConfig YAML for a scene.

    Creates two files in scene_dir/curobo/:
      world_config.yaml          — obstacles: cuboids, meshes, blox, voxels
      world_collision_config.yaml — cache sizes, activation distance, sweep flag

    The world_config.yaml can be passed directly to:
      - cuMotion planner (world_config parameter)
      - WorldCollisionManagerNode (world_config_path parameter)
    """
    scene_dir   = await _get_scene_dir()
    curobo_dir  = scene_dir / "curobo"
    curobo_dir.mkdir(parents=True, exist_ok=True)

    # Cuboid obstacles
    cuboids: List[Dict] = args.get("cuboids", [])
    # Mesh obstacles
    meshes: List[Dict] = args.get("meshes", [])
    # Whether to include blox (nvblox) section
    use_blox        = bool(args.get("use_blox", _nvblox_running()))
    blox_voxel_size = float(args.get("blox_voxel_size", 0.02))
    integrator_type = args.get("integrator_type", "occupancy")  # or "tsdf"
    # Whether to include a voxel/ESDF section
    use_voxel    = bool(args.get("use_voxel", False))
    voxel_dims   = args.get("voxel_dims", [2.0, 2.0, 2.0])
    voxel_size   = float(args.get("voxel_size", 0.02))
    # Cache config
    cache_obb    = int(args.get("cache_obb",  20))
    cache_mesh   = int(args.get("cache_mesh",  5))
    act_dist     = float(args.get("activation_distance", 0.01))
    max_dist     = float(args.get("max_distance", 0.5))
    use_sweep    = bool(args.get("use_sweep", True))

    # Build cuboid YAML entries
    if cuboids:
        cuboid_lines = []
        for c in cuboids:
            name = c.get("name", f"cuboid_{len(cuboid_lines)}")
            dims = c.get("dims", [0.1, 0.1, 0.1])
            pose = c.get("pose", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
            while len(pose) < 7:
                pose.append([1, 0, 0, 0][len(pose) - 3])
            cuboid_lines.append(
                _CUBOID_ENTRY.format(
                    name=name,
                    dx=dims[0], dy=dims[1], dz=dims[2],
                    x=pose[0], y=pose[1], z=pose[2],
                    qw=pose[3], qx=pose[4], qy=pose[5], qz=pose[6],
                )
            )
        cuboid_section = "\n".join(cuboid_lines)
    else:
        cuboid_section = (
            "  # table:\n"
            "  #   dims: [0.6, 0.6, 0.05]\n"
            "  #   pose: [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]\n"
        )

    # Build mesh YAML entries
    if meshes:
        mesh_lines = []
        for m in meshes:
            name = m.get("name", f"mesh_{len(mesh_lines)}")
            scale = m.get("scale", [1.0, 1.0, 1.0])
            pose  = m.get("pose", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
            while len(pose) < 7:
                pose.append([1, 0, 0, 0][len(pose) - 3])
            mesh_lines.append(
                _MESH_ENTRY.format(
                    name=name,
                    file_path=m.get("file_path", ""),
                    x=pose[0], y=pose[1], z=pose[2],
                    qw=pose[3], qx=pose[4], qy=pose[5], qz=pose[6],
                    sx=scale[0], sy=scale[1], sz=scale[2],
                )
            )
        mesh_section = "\n".join(mesh_lines)
    else:
        mesh_section = (
            "  # shelf:\n"
            "  #   file_path: \"meshes/shelf.obj\"\n"
            "  #   pose: [0.5, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0]\n"
            "  #   scale: [1.0, 1.0, 1.0]\n"
        )

    blox_section = (
        _BLOX_SECTION.format(
            voxel_size=blox_voxel_size,
            integrator_type=integrator_type,
        )
        if use_blox else
        "# blox:\n"
        "#   world:\n"
        "#     voxel_size: 0.02\n"
        "#     integrator_type: \"occupancy\"\n"
        "#     # Uncomment + launch nvblox to enable live depth integration\n"
    )

    voxel_section = (
        _VOXEL_SECTION.format(
            vx=voxel_dims[0], vy=voxel_dims[1], vz=voxel_dims[2],
            voxel_size=voxel_size,
        )
        if use_voxel else
        "# voxel:\n"
        "#   world_voxel:\n"
        "#     dims: [2.0, 2.0, 2.0]\n"
        "#     voxel_size: 0.02\n"
    )

    world_yaml = _WORLD_CONFIG_YAML.format(
        cuboid_entries=cuboid_section,
        mesh_entries=mesh_section,
        blox_section=blox_section,
        voxel_section=voxel_section,
    )

    collision_yaml = _WORLD_COLLISION_CONFIG_YAML.format(
        cache_obb=cache_obb,
        cache_mesh=cache_mesh,
        activation_distance=act_dist,
        max_distance=max_dist,
        use_sweep=str(use_sweep).lower(),
    )

    world_cfg_path      = curobo_dir / "world_config.yaml"
    collision_cfg_path  = curobo_dir / "world_collision_config.yaml"
    world_cfg_path.write_text(world_yaml)
    collision_cfg_path.write_text(collision_yaml)

    return {
        "status":                "created",
        "world_config_path":     str(world_cfg_path),
        "collision_config_path": str(collision_cfg_path),
        "cuboids":               [c.get("name", f"cuboid_{i}") for i, c in enumerate(cuboids)],
        "meshes":                [m.get("name", f"mesh_{i}") for i, m in enumerate(meshes)],
        "blox_enabled":          use_blox,
        "voxel_enabled":         use_voxel,
        "cache":                 {"obb": cache_obb, "mesh": cache_mesh},
        "cost_params": {
            "activation_distance": act_dist,
            "max_distance":        max_dist,
            "use_sweep":           use_sweep,
        },
        "cumotion_integration": {
            "param": "world_config",
            "value": str(world_cfg_path),
            "note":  (
                "Pass world_config_path to cumotion_planner_node --ros-args "
                f"--params-file {world_cfg_path}"
            ),
        },
        "message": (
            f"WorldConfig written to {world_cfg_path}. "
            f"Contains {len(cuboids)} cuboid(s), {len(meshes)} mesh(es)"
            + (", nvblox blox section" if use_blox else "")
            + ". Pass world_config_path to cumotion_planner_node."
        ),
    }


async def handle_add_world_obstacle(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add or update a single obstacle in the scene's world_config.yaml.

    Supports cuboid (OBB) and mesh types.  Updates the YAML on disk —
    the cuMotion planner / WorldCollisionManagerNode will pick up the
    change on next map reload.

    For zero-downtime updates (no CUDA graph re-compile), the planner
    must have been started with a cache pre-allocation:
      cache_obb / cache_mesh ≥ 1 (set in configure_curobo_world).
    """
    scene_dir  = await _get_scene_dir()
    world_yaml = scene_dir / "curobo" / "world_config.yaml"

    # Load or bootstrap
    if world_yaml.exists():
        import yaml
        data = yaml.safe_load(world_yaml.read_text()) or {}
    else:
        data = {"cuboid": {}, "mesh": {}}

    name     = args.get("name", f"obs_{int(time.time())}")
    obs_type = args.get("type", "cuboid")
    pose     = args.get("pose", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
    while len(pose) < 7:
        pose.append([1, 0, 0, 0][len(pose) - 3])
    enabled  = bool(args.get("enabled", True))

    import yaml
    if obs_type == "cuboid":
        dims = args.get("dims", [0.1, 0.1, 0.1])
        data.setdefault("cuboid", {})[name] = {
            "dims":   dims,
            "pose":   pose,
            "enable": enabled,
        }
    elif obs_type == "mesh":
        file_path = args.get("file_path", "")
        scale     = args.get("scale", [1.0, 1.0, 1.0])
        data.setdefault("mesh", {})[name] = {
            "file_path": file_path,
            "pose":      pose,
            "scale":     scale,
        }
    else:
        return {"status": "error", "message": f"Unknown type {obs_type!r}. Use 'cuboid' or 'mesh'."}

    world_yaml.parent.mkdir(parents=True, exist_ok=True)
    world_yaml.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    return {
        "status":    "added",
        "name":      name,
        "type":      obs_type,
        "pose":      pose,
        "yaml_path": str(world_yaml),
        "note": (
            "Restart or reload the cuMotion planner to pick up the change. "
            "For zero-downtime updates, use WorldCollisionManagerNode "
            "with pre-allocated cache (launch_world_collision_manager)."
        ),
        "message": f"Obstacle '{name}' ({obs_type}) added to {world_yaml}.",
    }


async def handle_remove_world_obstacle(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a named obstacle from world_config.yaml."""
    scene_dir  = await _get_scene_dir()
    world_yaml = scene_dir / "curobo" / "world_config.yaml"

    if not world_yaml.exists():
        return {"status": "error", "message": "world_config.yaml not found. Run configure_curobo_world first."}

    import yaml
    data = yaml.safe_load(world_yaml.read_text()) or {}
    name = args.get("name", "")

    removed_from = []
    for section in ("cuboid", "mesh"):
        if name in (data.get(section) or {}):
            del data[section][name]
            removed_from.append(section)

    if not removed_from:
        return {"status": "error", "message": f"Obstacle '{name}' not found in world_config.yaml."}

    world_yaml.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return {
        "status":   "removed",
        "name":     name,
        "sections": removed_from,
        "message":  f"Removed '{name}' from {world_yaml}.",
    }


async def handle_update_obstacle_pose(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update the pose of a named obstacle in world_config.yaml.

    Pose format: [x, y, z, qw, qx, qy, qz]  (quaternion w-first, metres)

    For live pose updates without restarting the planner, publish the
    update to the WorldCollisionManagerNode topic:
      ros2 topic pub /world_manager/add_obstacle std_msgs/String
        '{data: "{\"name\": \"box1\", \"type\": \"cuboid\", \"dims\": [0.1,0.1,0.1],
          \"pose\": [0.5, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0]}"}'
    """
    scene_dir  = await _get_scene_dir()
    world_yaml = scene_dir / "curobo" / "world_config.yaml"

    if not world_yaml.exists():
        return {"status": "error", "message": "world_config.yaml not found. Run configure_curobo_world first."}

    import yaml
    data = yaml.safe_load(world_yaml.read_text()) or {}
    name = args.get("name", "")
    pose = args.get("pose", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
    while len(pose) < 7:
        pose.append([1, 0, 0, 0][len(pose) - 3])

    updated = False
    for section in ("cuboid", "mesh"):
        if name in (data.get(section) or {}):
            data[section][name]["pose"] = pose
            updated = True

    if not updated:
        return {"status": "error", "message": f"Obstacle '{name}' not found."}

    world_yaml.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return {
        "status":  "updated",
        "name":    name,
        "pose":    pose,
        "message": f"Pose of '{name}' updated in {world_yaml}.",
    }


async def handle_enable_world_obstacle(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enable or disable collision checking for a named obstacle.

    Disabled obstacles remain in the YAML but are ignored during planning.
    This is cheaper than remove/re-add (no geometry reallocation).
    """
    scene_dir  = await _get_scene_dir()
    world_yaml = scene_dir / "curobo" / "world_config.yaml"

    if not world_yaml.exists():
        return {"status": "error", "message": "world_config.yaml not found."}

    import yaml
    data    = yaml.safe_load(world_yaml.read_text()) or {}
    name    = args.get("name", "")
    enabled = bool(args.get("enabled", True))

    updated = False
    for section in ("cuboid",):   # mesh entries don't have an enable flag in cuRobo
        if name in (data.get(section) or {}):
            data[section][name]["enable"] = enabled
            updated = True

    if not updated:
        return {
            "status":  "warning",
            "message": (
                f"'{name}' not found or is a mesh (mesh obstacles must be removed "
                "rather than disabled). Use remove_world_obstacle for meshes."
            ),
        }

    world_yaml.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return {
        "status":  "ok",
        "name":    name,
        "enabled": enabled,
        "message": f"{'Enabled' if enabled else 'Disabled'} obstacle '{name}'.",
    }


async def handle_query_sphere_collision(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Test one or more spheres against the current world_config.yaml using cuRobo.

    Each sphere is [x, y, z, radius].  Returns signed distance for each sphere:
      positive → sphere penetrates or is within activation_distance of an obstacle
      negative → sphere is clear

    Requires curobo Python package:  pip install curobo
    """
    scene_dir  = await _get_scene_dir()
    world_yaml = scene_dir / "curobo" / "world_config.yaml"

    if not world_yaml.exists():
        return {"status": "error", "message": "world_config.yaml not found. Run configure_curobo_world first."}

    spheres = args.get("spheres", [])
    if not spheres:
        return {"status": "error", "message": "Provide at least one sphere as [x, y, z, radius]."}

    try:
        import yaml
        import torch
        from curobo.geom.types import WorldConfig
        from curobo.geom.sdf.world import WorldCollisionConfig, WorldPrimitiveCollision

        world_dict = yaml.safe_load(world_yaml.read_text()) or {}
        world_cfg  = WorldConfig.from_dict(world_dict)
        coll_cfg   = WorldCollisionConfig(world_model=world_cfg)
        checker    = WorldPrimitiveCollision(coll_cfg)

        # [1, 1, N, 4]
        t    = torch.tensor(spheres, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
        dist = checker.get_sphere_distance(t)   # [1, 1, N]

        results = []
        any_coll = False
        for i, s in enumerate(spheres):
            d = float(dist[0, 0, i])
            coll = d > 0.0
            any_coll = any_coll or coll
            results.append({
                "sphere":          s,
                "signed_distance": round(d, 6),
                "in_collision":    coll,
            })

        return {
            "status":        "ok",
            "any_collision": any_coll,
            "spheres":       results,
            "world_config":  str(world_yaml),
        }

    except ImportError:
        return {
            "status":  "error",
            "message": "cuRobo not installed. Run: pip install curobo",
            "fallback": "Use WorldCollisionManagerNode with use_curobo:=true for live queries.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


async def handle_launch_world_collision_manager(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch the cuRobo WorldCollisionManager ROS2 node.

    The node maintains a live WorldConfig, watches the YAML for changes,
    and exposes ROS2 topics/services for dynamic obstacle management.
    It publishes MarkerArray visualizations and syncs obstacle poses from TF.
    """
    from .ros2_autonomy_tools import _LAUNCHED_PROCESSES

    scene_dir  = await _get_scene_dir()
    curobo_dir = scene_dir / "curobo"
    curobo_dir.mkdir(parents=True, exist_ok=True)

    world_cfg_path = str(curobo_dir / "world_config.yaml")
    node_name      = args.get("node_name",       "world_collision_manager")
    base_frame     = args.get("robot_base_frame", "base_link")
    sync_rate      = float(args.get("sync_rate_hz", 10.0))
    use_curobo     = bool(args.get("use_curobo", True))
    cache_obb      = int(args.get("cache_obb",  20))
    cache_mesh     = int(args.get("cache_mesh",  5))

    key = f"curobo/world_manager"
    if key in _LAUNCHED_PROCESSES:
        if _LAUNCHED_PROCESSES[key]["process"].returncode is None:
            return {"status": "already_running", "key": key}

    # Scaffold the node if not already done
    from .ros2_node_scaffolder import handle_scaffold_ros2_node
    scaffold_result = await handle_scaffold_ros2_node({
        "node_name":   node_name,
        "node_type":   "world_collision_manager",
        "description": "cuRobo WorldConfig collision manager — dynamic obstacle CRUD + visualization",
        "input_topic":  "/tf",
        "output_topic": "/curobo_world/markers",
        "world_config_path": world_cfg_path,
    })

    pkg_name = scaffold_result.get("package_name", node_name)

    cmd = [
        "ros2", "run", pkg_name, node_name,
        "--ros-args",
        "-p", f"world_config_path:={world_cfg_path}",
        "-p", f"robot_base_frame:={base_frame}",
        "-p", f"sync_rate_hz:={sync_rate}",
        "-p", f"use_curobo:={str(use_curobo).lower()}",
        "-p", f"cache_obb:={cache_obb}",
        "-p", f"cache_mesh:={cache_mesh}",
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
        "type":    "world_collision_manager",
    }

    return {
        "status":            "launched",
        "key":               key,
        "pid":               proc.pid,
        "world_config_path": world_cfg_path,
        "node_name":         node_name,
        "scaffold_result":   scaffold_result,
        "services": {
            "/curobo_world/get_config":     "std_srvs/Trigger → YAML string of current world",
            "/curobo_world/query_spheres":  "std_srvs/Trigger → sphere collision results",
        },
        "topics": {
            f"/{node_name}/add_obstacle":    "std_msgs/String  → JSON {name, type, dims/file_path, pose}",
            f"/{node_name}/remove_obstacle": "std_msgs/String  → obstacle name",
            "/curobo_world/markers":         "visualization_msgs/MarkerArray",
            "/curobo_world/status":          "std_msgs/String  → JSON world summary",
        },
        "example_add_cuboid": (
            f"ros2 topic pub --once /{node_name}/add_obstacle std_msgs/String "
            "'{data: \"{\\\"name\\\": \\\"table\\\", \\\"type\\\": \\\"cuboid\\\", "
            "\\\"dims\\\": [0.6, 0.6, 0.05], "
            "\\\"pose\\\": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]}\"}'"
        ),
        "message": (
            f"WorldCollisionManager launched (PID {proc.pid}). "
            f"Watching {world_cfg_path}. "
            f"Publish obstacles to /{node_name}/add_obstacle (JSON)."
        ),
    }
