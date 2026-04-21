"""
ros2_autonomy_tools.py
----------------------
Phase 9 — ROS2 Autonomy Stack

Implements (Phase A P0):
  check_scene_ready, get_machine_specs, suggest_next_steps, check_sensor_health

Implements (Phase B P0-P1):
  launch_nav2, launch_slam
  list_launched, stop_launched, restart_launched

Implements (Phase D P1):
  slam_start, slam_stop, slam_status, nav2_goto, map_export

All launched processes are registered in _LAUNCHED_PROCESSES so list_launched
/ stop_launched / restart_launched can manage them uniformly.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Workspace paths ──────────────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"

# All per-scene outputs live under workspace/scenes/<scene_name>/
# ┌─ workspace/scenes/<scene>/
# │   maps/          ← 2D pgm+yaml (slam_stop, map_export)
# │   visual_slam/   ← 3D cuVSLAM serialized map (save_visual_slam_map)
# │   nav2/          ← generated Nav2 params YAML
# │   slam/          ← generated SLAM params YAML
# │   locations.json ← named waypoints (save_location / nav2_goto)
# └─────────────────────────────────────────────────────

# Fallback (used before scene name is known)
_MAPS_DIR = _WORKSPACE / "maps"
_NAV2_CONFIGS_DIR = _WORKSPACE / "nav2_configs"
_SLAM_CONFIGS_DIR = _WORKSPACE / "slam_configs"


async def _get_scene_dir() -> Path:
    """
    Return the per-scene output directory: workspace/scenes/<scene_name>/
    Falls back to workspace/scenes/untitled/ if Kit is unreachable.
    """
    scene_name = "untitled"
    try:
        from . import kit_tools
        ctx = await kit_tools.get_stage_context(full=False)
        stage_url = ctx.get("stage", {}).get("stage_url", "")
        if stage_url:
            import os, re
            basename = os.path.basename(stage_url)
            name, _ = os.path.splitext(basename)
            if name:
                scene_name = re.sub(r"[^\w\-]", "_", name)
    except Exception:
        pass
    d = _WORKSPACE / "scenes" / scene_name
    d.mkdir(parents=True, exist_ok=True)
    return d

# ═══════════════════════════════════════════════════════════════════════════
#  PROCESS REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

# Key: process name (e.g. "nav2", "slam")
# Value: {pid, proc, cmd, config_path, launch_time, topics_used}
_LAUNCHED_PROCESSES: Dict[str, Dict[str, Any]] = {}


def _register_process(
    name: str,
    proc: "asyncio.subprocess.Process",
    cmd: List[str],
    config_path: Optional[str] = None,
    topics_used: Optional[List[str]] = None,
) -> None:
    _LAUNCHED_PROCESSES[name] = {
        "pid": proc.pid,
        "proc": proc,
        "cmd": cmd,
        "config_path": config_path,
        "launch_time": datetime.utcnow().isoformat(),
        "topics_used": topics_used or [],
    }


def _process_info(name: str) -> Optional[Dict[str, Any]]:
    entry = _LAUNCHED_PROCESSES.get(name)
    if not entry:
        return None
    proc = entry["proc"]
    running = proc.returncode is None
    return {
        "name": name,
        "pid": entry["pid"],
        "running": running,
        "return_code": proc.returncode,
        "config_path": entry.get("config_path"),
        "launch_time": entry["launch_time"],
        "topics_used": entry.get("topics_used", []),
        "cmd": " ".join(entry.get("cmd", [])),
    }


async def _stop_process(name: str, timeout: float = 5.0) -> Optional[int]:
    entry = _LAUNCHED_PROCESSES.pop(name, None)
    if not entry:
        return None
    proc = entry["proc"]
    pid = entry["pid"]
    if proc.returncode is not None:
        return pid
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass
    logger.info(f"[Autonomy] Stopped {name} PID {pid}")
    return pid


# ═══════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════

async def _get_active_topics() -> List[str]:
    try:
        from .ros_mcp_tools import handle_ros2_list_topics
        result = await handle_ros2_list_topics({})
        return result.get("topics", [])
    except Exception:
        return []


async def _kit_exec(code: str) -> Optional[str]:
    try:
        from . import kit_tools
        result = await kit_tools.exec_sync(code)
        if result and result.get("success"):
            return result.get("output", "").strip()
    except Exception:
        pass
    return None


def _has_topic(topics: List[str], pattern: str) -> bool:
    r = re.compile(pattern)
    return any(r.search(t) for t in topics)


def _first_topic(topics: List[str], pattern: str) -> Optional[str]:
    r = re.compile(pattern)
    for t in topics:
        if r.search(t):
            return t
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE A — SCENE READINESS & MACHINE SPECS
# ═══════════════════════════════════════════════════════════════════════════

async def handle_check_scene_ready(_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs 11 readiness checks (rosbridge, sim playing, robot, drive graph,
    camera, lidar, IMU, odom, TF, clock, map) and returns a structured report
    with a score, per-check status, and actionable fix suggestions.
    """
    checks: List[Dict[str, Any]] = []
    missing: List[str] = []

    def _chk(name: str, passed: bool, message: str, fix: Optional[str] = None) -> None:
        checks.append({"name": name, "passed": passed, "message": message, "fix": fix})
        if not passed:
            missing.append(name)

    # 1. Rosbridge connectivity
    topics: List[str] = []
    rosbridge_ok = False
    try:
        from .ros_mcp_tools import handle_ros2_list_topics
        r = await handle_ros2_list_topics({})
        rosbridge_ok = "error" not in r
        topics = r.get("topics", [])
    except Exception:
        pass
    _chk(
        "rosbridge_connected", rosbridge_ok,
        f"rosbridge reachable ({len(topics)} topics)" if rosbridge_ok else "Cannot connect to rosbridge",
        fix="ros2 launch rosbridge_server rosbridge_websocket_launch.xml" if not rosbridge_ok else None,
    )

    # 2. Simulation playing
    sim_playing = False
    out = await _kit_exec(
        "import omni.timeline\n"
        "tl = omni.timeline.get_timeline_interface()\n"
        "print('playing' if tl.is_playing() else 'stopped')\n"
    )
    sim_playing = out == "playing"
    _chk(
        "sim_playing", sim_playing,
        "Simulation is playing" if sim_playing else "Simulation is not playing",
        fix="Click ▶ Play or call sim_control({action:'play'})" if not sim_playing else None,
    )

    # 3. Robot articulation present
    out = await _kit_exec(
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "found = []\n"
        "for prim in stage.Traverse():\n"
        "    schemas = [str(s) for s in prim.GetAppliedSchemas()]\n"
        "    if any('ArticulationRoot' in s for s in schemas):\n"
        "        found.append(str(prim.GetPath()))\n"
        "print(found[0] if found else '')\n"
    )
    robot_present = bool(out)
    _chk(
        "robot_present", robot_present,
        f"Robot found at {out}" if robot_present else "No robot articulation in scene",
        fix="Import a robot: import_robot tool" if not robot_present else None,
    )

    # 4. Drive graph wired — look for cmd_vel subscriber in OmniGraph or active topic
    drive_ok = _has_topic(topics, r"/cmd_vel")
    if not drive_ok:
        og_out = await _kit_exec(
            "import omni.graph.core as og, omni.usd\n"
            "stage = omni.usd.get_context().get_stage()\n"
            "found = []\n"
            "for prim in stage.Traverse():\n"
            "    if prim.GetTypeName() == 'OmniGraphNode':\n"
            "        n = og.get_node_by_path(str(prim.GetPath()))\n"
            "        if n and 'SubscribeTwist' in n.get_type_name():\n"
            "            found.append(str(prim.GetPath()))\n"
            "print(found[0] if found else '')\n"
        )
        drive_ok = bool(og_out)
    _chk(
        "drive_graph_wired", drive_ok,
        "/cmd_vel subscriber wired" if drive_ok else "No /cmd_vel subscriber in OmniGraph",
        fix="create_omnigraph_from_template template='ros2_cmd_vel'" if not drive_ok else None,
    )

    # 5–10. Topic presence checks
    def _topic_chk(name: str, pattern: str, label: str, fix: str) -> None:
        ok = _has_topic(topics, pattern)
        _chk(name, ok, f"{label} publishing" if ok else f"No {label} topic", fix=fix if not ok else None)

    _topic_chk("camera_topics",   r"(rgb|image_raw)",   "camera image",   "create_omnigraph_from_template template='ros2_camera' or add_full_sensor_suite")
    _topic_chk("lidar_topics",    r"(scan|lidar|point_cloud)", "LiDAR/scan", "create_omnigraph_from_template template='ros2_lidar' or add_full_sensor_suite")
    _topic_chk("imu_topic",       r"^/imu/",            "/imu/data",      "create_omnigraph_from_template template='ros2_imu'")
    _topic_chk("odom_topic",      r"^/odom$",           "/odom",          "create_omnigraph_from_template template='ros2_odom'")
    _topic_chk("tf_publishing",   r"^/tf$",             "/tf",            "create_omnigraph_from_template template='ros2_tf'")
    _topic_chk("clock_publishing",r"^/clock$",          "/clock",         "create_omnigraph_from_template template='ros2_clock'")

    # 11. Map available
    map_ok = _has_topic(topics, r"^/map$")
    _chk(
        "map_available", map_ok,
        "/map topic active" if map_ok else "No /map topic (SLAM not running or no map loaded)",
        fix="slam_start → drive to map → slam_stop (saves map) → launch_nav2" if not map_ok else None,
    )

    passed = sum(1 for c in checks if c["passed"])
    nav_required = {"sim_playing", "robot_present", "odom_topic", "tf_publishing", "clock_publishing"}
    nav_ready = all(c["passed"] for c in checks if c["name"] in nav_required)

    return {
        "score": round(passed / len(checks) * 100),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "missing": missing,
        "ready_for_navigation": nav_ready,
        "suggested_next_steps": [c["fix"] for c in checks if not c["passed"] and c.get("fix")],
    }


async def handle_get_machine_specs(_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detects GPU/VRAM, CPU, RAM, disk, ROS2 distro, architecture, and checks
    which critical ROS2 packages are installed. Returns machine-aware
    install suggestions and sensor resolution recommendations.
    """
    specs: Dict[str, Any] = {
        "arch": platform.machine(),
        "os": platform.system(),
        "platform": platform.platform(),
    }
    suggestions: List[str] = []

    ros_distro = os.environ.get("ROS_DISTRO", "")
    specs["ros_distro"] = ros_distro or "not_detected"
    if not ros_distro:
        suggestions.append("ROS2 not sourced — run: source /opt/ros/jazzy/setup.bash")

    # CPU + RAM + Disk via psutil
    try:
        import psutil
        specs["cpu_cores"] = psutil.cpu_count(logical=False)
        specs["cpu_logical"] = psutil.cpu_count(logical=True)
        vm = psutil.virtual_memory()
        specs["ram_gb"] = round(vm.total / 1e9, 1)
        specs["ram_available_gb"] = round(vm.available / 1e9, 1)
        du = psutil.disk_usage("/")
        specs["disk_total_gb"] = round(du.total / 1e9, 1)
        specs["disk_free_gb"] = round(du.free / 1e9, 1)
    except ImportError:
        specs["psutil"] = "not_installed"
        suggestions.append("pip install psutil  # for CPU/RAM stats")

    # GPU via nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            gpus = []
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpus.append({
                        "name": parts[0],
                        "vram_mb": int(parts[1]) if parts[1].isdigit() else parts[1],
                        "vram_free_mb": int(parts[2]) if parts[2].isdigit() else parts[2],
                        "driver": parts[3] if len(parts) > 3 else "",
                    })
            specs["gpus"] = gpus
            if gpus:
                vram = gpus[0].get("vram_mb", 0)
                if isinstance(vram, int) and vram < 8000:
                    suggestions.append("Low VRAM (<8 GB): prefer slam_toolbox over rtabmap")
                elif isinstance(vram, int) and vram < 16000:
                    suggestions.append("VRAM <16 GB: GR00T N1.7 inference requires 16 GB+")
        else:
            specs["gpus"] = []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        specs["gpus"] = []

    # ROS2 package audit
    if ros_distro:
        critical = [
            f"ros-{ros_distro}-navigation2",
            f"ros-{ros_distro}-nav2-bringup",
            f"ros-{ros_distro}-slam-toolbox",
            f"ros-{ros_distro}-rtabmap-ros",
            f"ros-{ros_distro}-ros2-control",
            f"ros-{ros_distro}-ros2-controllers",
            f"ros-{ros_distro}-rosbridge-suite",
            f"ros-{ros_distro}-vision-msgs",
        ]
        missing_pkgs: List[str] = []
        try:
            r = subprocess.run(
                ["dpkg", "-l"] + critical,
                capture_output=True, text=True, timeout=10,
            )
            installed = set(re.findall(r"ii\s+(ros-\S+)", r.stdout))
            missing_pkgs = [p for p in critical if p not in installed]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        specs["missing_ros2_packages"] = missing_pkgs
        if missing_pkgs:
            suggestions.append(f"sudo apt install {' '.join(missing_pkgs)}")

    # Sensor resolution recommendations
    ram = specs.get("ram_gb", 32)
    specs["sensor_recommendations"] = (
        ["Low RAM: use 640×480 cameras, LiDAR ≤32 beams"]
        if isinstance(ram, (int, float)) and ram < 16
        else ["RAM sufficient for 1080p cameras and 64-beam LiDAR"]
    )
    specs["install_suggestions"] = suggestions
    return specs


async def handle_suggest_next_steps(_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combines check_scene_ready + get_machine_specs + live topic list into an
    ordered 'what to do next' recommendation ladder.
    """
    scene, machine = await asyncio.gather(
        handle_check_scene_ready({}),
        handle_get_machine_specs({}),
    )

    steps: List[Dict[str, Any]] = []
    pri = 1

    for c in scene.get("checks", []):
        if not c["passed"] and c.get("fix"):
            steps.append({"priority": pri, "category": "scene", "issue": c["name"], "action": c["fix"]})
            pri += 1

    for s in machine.get("install_suggestions", []):
        steps.append({"priority": pri, "category": "environment", "issue": "dependency", "action": s})
        pri += 1

    if scene.get("ready_for_navigation") and not scene.get("missing"):
        steps.append({"priority": pri, "category": "navigation", "issue": "next",
                       "action": "All prerequisites met — start mapping: slam_start → drive robot → slam_stop"})
        pri += 1
        steps.append({"priority": pri, "category": "navigation", "issue": "next",
                       "action": "After saving map: launch_nav2 → nav2_goto to send goals"})

    return {
        "scene_score": scene.get("score", 0),
        "ready_for_navigation": scene.get("ready_for_navigation", False),
        "steps": steps,
        "ros_distro": machine.get("ros_distro"),
        "arch": machine.get("arch"),
        "ram_gb": machine.get("ram_gb"),
        "gpu": machine.get("gpus", [{}])[0].get("name") if machine.get("gpus") else None,
    }


# ── Sensor health ─────────────────────────────────────────────────────────────

_SENSOR_PATTERNS: List[Tuple[str, str, float, Optional[float]]] = [
    # (sensor_name, topic_regex, min_hz, max_hz)
    ("camera_rgb",    r"(rgb|image_raw)$",      5.0,  60.0),
    ("camera_depth",  r"/depth$",               5.0,  60.0),
    ("lidar_scan",    r"^/scan$",               1.0,  30.0),
    ("lidar_points",  r"/point_cloud$|/points$",1.0,  30.0),
    ("imu",           r"^/imu/",               10.0, 500.0),
    ("odom",          r"^/odom$",               5.0, 100.0),
    ("joint_states",  r"^/joint_states$",       5.0, 200.0),
    ("clock",         r"^/clock$",              1.0,  None),
    ("tf",            r"^/tf$",                 1.0,  None),
]


async def _measure_hz(topic: str, window_s: float = 3.0) -> Optional[float]:
    """Measure topic Hz using 'ros2 topic hz --window 5'. Returns Hz or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ros2", "topic", "hz", topic, "--window", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=window_s + 2.0)
            for line in stdout.decode().splitlines():
                m = re.search(r"average rate:\s*([\d.]+)", line)
                if m:
                    return float(m.group(1))
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
    except Exception:
        pass
    return None


async def handle_check_sensor_health(_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Per-sensor Hz, encoding, and validity checks for camera, LiDAR, IMU, odom,
    joint_states, clock, TF. Returns health report with per-sensor status
    (healthy / warning / error / missing) and fix hints.
    """
    topics = await _get_active_topics()
    results: List[Dict[str, Any]] = []

    # Check each sensor pattern concurrently
    async def _check_sensor(
        name: str, pattern: str, min_hz: float, max_hz: Optional[float],
    ) -> None:
        matched = [t for t in topics if re.search(pattern, t)]
        if not matched:
            results.append({
                "sensor": name, "status": "missing", "topics": [],
                "message": f"No {name} topic found",
                "fix": f"Wire a ROS2 OmniGraph publisher for {name}",
            })
            return
        topic = matched[0]
        hz = await _measure_hz(topic)
        if hz is None:
            status, message, fix = (
                "warning",
                "Topic exists but no messages received",
                "Verify simulation is playing and OmniGraph is executing",
            )
        elif hz < min_hz:
            status = "error"
            message = f"Hz too low: {hz:.1f} (min {min_hz})"
            fix = "Check OmniGraph tick rate or reduce physics step size"
        elif max_hz is not None and hz > max_hz * 1.5:
            status = "warning"
            message = f"Hz very high: {hz:.1f} — consider throttling"
            fix = "Add a throttle or reduce OmniGraph publish rate"
        else:
            status, message, fix = "healthy", f"Publishing at {hz:.1f} Hz", None

        results.append({
            "sensor": name, "topic": topic, "status": status,
            "hz": hz, "message": message, "fix": fix,
        })

    await asyncio.gather(*[
        _check_sensor(name, pattern, min_hz, max_hz)
        for name, pattern, min_hz, max_hz in _SENSOR_PATTERNS
    ])

    healthy = sum(1 for r in results if r["status"] == "healthy")
    warnings = sum(1 for r in results if r["status"] == "warning")
    errors = sum(1 for r in results if r["status"] in ("error", "missing"))
    return {
        "overall": "healthy" if errors == 0 and warnings == 0 else ("warning" if errors == 0 else "error"),
        "healthy": healthy, "warnings": warnings, "errors": errors,
        "total_checked": len(results),
        "sensors": results,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE B — LAUNCH TOOLS
# ═══════════════════════════════════════════════════════════════════════════

_NAV2_PARAMS_TEMPLATE = """\
amcl:
  ros__parameters:
    use_sim_time: true
    global_frame_id: "map"
    odom_frame_id: "odom"
    base_frame_id: "{base_frame}"
    scan_topic: "{scan_topic}"
    robot_model_type: "nav2_amcl::DifferentialMotionModel"
    max_particles: 2000
    min_particles: 500
    transform_tolerance: 1.0
    update_min_d: 0.25
    update_min_a: 0.2
    laser_model_type: "likelihood_field"
    z_hit: 0.5
    z_rand: 0.5
    sigma_hit: 0.2
    laser_likelihood_max_dist: 2.0
bt_navigator:
  ros__parameters:
    use_sim_time: true
    global_frame: map
    robot_base_frame: "{base_frame}"
    odom_topic: /odom
    navigators: ["navigate_to_pose", "navigate_through_poses"]
    navigate_to_pose:
      plugin: "nav2_bt_navigator/NavigateToPoseNavigator"
    navigate_through_poses:
      plugin: "nav2_bt_navigator/NavigateThroughPosesNavigator"
controller_server:
  ros__parameters:
    use_sim_time: true
    controller_frequency: 20.0
    FollowPath:
      plugin: "nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController"
      desired_linear_vel: {max_linear_vel}
      lookahead_dist: 0.6
      min_lookahead_dist: 0.3
      max_lookahead_dist: 0.9
local_costmap:
  local_costmap:
    ros__parameters:
      use_sim_time: true
      global_frame: odom
      robot_base_frame: "{base_frame}"
      rolling_window: true
      width: 3
      height: 3
      resolution: 0.05
      robot_radius: {robot_radius}
      plugins: ["voxel_layer", "inflation_layer"]
      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 3.0
        inflation_radius: 0.55
      voxel_layer:
        plugin: "nav2_costmap_2d::VoxelLayer"
        observation_sources: scan
        scan:
          topic: "{scan_topic}"
          data_type: "LaserScan"
          clearing: true
          marking: true
global_costmap:
  global_costmap:
    ros__parameters:
      use_sim_time: true
      global_frame: map
      robot_base_frame: "{base_frame}"
      robot_radius: {robot_radius}
      resolution: 0.05
      track_unknown_space: true
      plugins: ["static_layer", "obstacle_layer", "inflation_layer"]
      static_layer:
        plugin: "nav2_costmap_2d::StaticLayer"
        map_subscribe_transient_local: true
      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        observation_sources: scan
        scan:
          topic: "{scan_topic}"
          data_type: "LaserScan"
          clearing: true
          marking: true
      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 3.0
        inflation_radius: 0.55
map_server:
  ros__parameters:
    use_sim_time: true
    yaml_filename: "{map_yaml_path}"
map_saver:
  ros__parameters:
    use_sim_time: true
    save_map_timeout: 5.0
planner_server:
  ros__parameters:
    use_sim_time: true
    expected_planner_frequency: 20.0
    GridBased:
      plugin: "nav2_navfn_planner/NavfnPlanner"
      tolerance: 0.5
      use_astar: false
      allow_unknown: true
behavior_server:
  ros__parameters:
    use_sim_time: true
    costmap_topic: local_costmap/costmap_raw
    footprint_topic: local_costmap/published_footprint
    cycle_frequency: 10.0
    behavior_plugins: ["spin", "backup", "drive_on_heading", "wait"]
    spin:
      plugin: "nav2_behaviors/Spin"
    backup:
      plugin: "nav2_behaviors/BackUp"
    drive_on_heading:
      plugin: "nav2_behaviors/DriveOnHeading"
    wait:
      plugin: "nav2_behaviors/Wait"
    global_frame: odom
    robot_base_frame: "{base_frame}"
    transform_tolerance: 0.1
    max_rotational_vel: {max_rotational_vel}
    min_rotational_vel: 0.4
waypoint_follower:
  ros__parameters:
    use_sim_time: true
    loop_rate: 20
    stop_on_failure: false
velocity_smoother:
  ros__parameters:
    use_sim_time: true
    smoothing_frequency: 20.0
    scale_velocities: false
    feedback: "OPEN_LOOP"
    max_velocity: [{max_linear_vel}, 0.0, {max_rotational_vel}]
    min_velocity: [-{max_linear_vel}, 0.0, -{max_rotational_vel}]
    max_accel: [2.5, 0.0, 3.2]
    max_decel: [-2.5, 0.0, -3.2]
    odom_topic: "/odom"
"""


async def handle_launch_nav2(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-generate Nav2 params.yaml from current scene state, then launch
    nav2_bringup. Checks prerequisites (odom, TF, clock, scan) first and
    returns actionable fixes if any are missing.
    """
    map_yaml = args.get("map_yaml_path", "")
    base_frame = args.get("base_frame", "base_link")
    scan_topic = args.get("scan_topic", "")
    max_linear_vel = float(args.get("max_linear_vel", 0.5))
    max_rotational_vel = float(args.get("max_rotational_vel", 1.0))
    robot_radius = float(args.get("robot_radius", 0.3))

    if not shutil.which("ros2"):
        return {"error": "ros2 CLI not found. Source ROS2: source /opt/ros/jazzy/setup.bash"}

    topics = await _get_active_topics()
    prereqs: List[str] = []
    if not _has_topic(topics, r"^/odom$"):
        prereqs.append("No /odom topic — create_omnigraph_from_template template='ros2_odom'")
    if not _has_topic(topics, r"^/tf$"):
        prereqs.append("No /tf topic — create_omnigraph_from_template template='ros2_tf'")
    if not _has_topic(topics, r"^/clock$"):
        prereqs.append("No /clock topic — create_omnigraph_from_template template='ros2_clock'")
    if not scan_topic:
        scan_topic = _first_topic(topics, r"^/scan$") or _first_topic(topics, r"(scan|lidar)") or ""
    if not scan_topic:
        prereqs.append("No LiDAR/scan topic — create_omnigraph_from_template template='ros2_lidar'")
    if prereqs:
        return {
            "status": "prerequisite_failed",
            "missing_prerequisites": prereqs,
            "message": "Fix the above before launching Nav2",
        }

    # Resolve map
    if not map_yaml:
        scene_dir = await _get_scene_dir()
        maps = sorted(
            list((scene_dir / "maps").glob("**/*.yaml")) +
            list(_MAPS_DIR.glob("**/*.yaml")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if maps:
            map_yaml = str(maps[0])
        else:
            return {
                "status": "no_map",
                "message": "No saved map found. Run: slam_start → drive to map environment → slam_stop (saves map) → call launch_nav2 again.",
            }

    # Write params file (scene-scoped)
    scene_dir = await _get_scene_dir()
    (scene_dir / "nav2").mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    params_path = scene_dir / "nav2" / f"nav2_params_{ts}.yaml"
    params_path.write_text(_NAV2_PARAMS_TEMPLATE.format(
        base_frame=base_frame,
        scan_topic=scan_topic,
        max_linear_vel=max_linear_vel,
        max_rotational_vel=max_rotational_vel,
        robot_radius=robot_radius,
        map_yaml_path=map_yaml,
    ))

    # Support visual-slam odometry topic override
    odom_topic = args.get("odom_topic", "/odom")
    if odom_topic != "/odom":
        # Patch the bt_navigator odom_topic in the generated params
        content = params_path.read_text()
        content = content.replace("odom_topic: /odom", f"odom_topic: {odom_topic}")
        params_path.write_text(content)

    cmd = [
        "ros2", "launch", "nav2_bringup", "bringup_launch.py",
        "use_sim_time:=true",
        f"params_file:={params_path}",
        f"map:={map_yaml}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _register_process(
            "nav2", proc, cmd,
            config_path=str(params_path),
            topics_used=["/odom", "/tf", "/clock", scan_topic, "/map"],
        )
        return {
            "status": "launched",
            "pid": proc.pid,
            "params_path": str(params_path),
            "map_yaml": map_yaml,
            "scan_topic": scan_topic,
            "base_frame": base_frame,
            "odom_topic": odom_topic,
            "message": "Nav2 launched. Use nav2_goto to send navigation goals.",
        }
    except FileNotFoundError:
        return {
            "error": "nav2_bringup not found",
            "fix": f"sudo apt install ros-${{ROS_DISTRO}}-nav2-bringup ros-${{ROS_DISTRO}}-navigation2",
        }
    except Exception as e:
        return {"error": f"Failed to launch Nav2: {e}"}


_SLAM_TOOLBOX_PARAMS = """\
slam_toolbox:
  ros__parameters:
    use_sim_time: true
    solver_plugin: solver_plugins::CeresSolver
    ceres_linear_solver: SPARSE_NORMAL_CHOLESKY
    ceres_preconditioner: SCHUR_JACOBI
    ceres_trust_strategy: LEVENBERG_MARQUARDT
    ceres_dogleg_type: TRADITIONAL_DOGLEG
    ceres_loss_function: None
    odom_frame: odom
    map_frame: map
    base_frame: "{base_frame}"
    scan_topic: "{scan_topic}"
    mode: mapping
    debug_logging: false
    throttle_scans: 1
    transform_publish_period: 0.02
    map_update_interval: 5.0
    resolution: 0.05
    max_laser_range: 20.0
    minimum_time_interval: 0.5
    transform_timeout: 0.2
    tf_buffer_duration: 30.0
    stack_size_to_use: 40000000
    enable_interactive_mode: true
"""


async def handle_launch_slam(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch SLAM — auto-selects slam_toolbox (2D LiDAR) or rtabmap (3D/stereo).
    'algorithm' can be 'auto', 'slam_toolbox', or 'rtabmap'.
    """
    base_frame = args.get("base_frame", "base_link")
    algorithm = args.get("algorithm", "auto")

    if not shutil.which("ros2"):
        return {"error": "ros2 CLI not found. Source ROS2: source /opt/ros/jazzy/setup.bash"}

    topics = await _get_active_topics()
    has_scan = _has_topic(topics, r"^/scan$")
    has_pointcloud = _has_topic(topics, r"(point_cloud|points)")

    if algorithm == "auto":
        algorithm = "slam_toolbox" if has_scan else ("rtabmap" if has_pointcloud else "slam_toolbox")

    scan_topic = _first_topic(topics, r"^/scan$") or _first_topic(topics, r"(scan|lidar)") or "/scan"

    if algorithm == "slam_toolbox":
        scene_dir = await _get_scene_dir()
        (scene_dir / "slam").mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        params_path = scene_dir / "slam" / f"slam_toolbox_{ts}.yaml"
        params_path.write_text(
            _SLAM_TOOLBOX_PARAMS.format(base_frame=base_frame, scan_topic=scan_topic)
        )
        cmd = [
            "ros2", "launch", "slam_toolbox", "online_async_launch.py",
            "use_sim_time:=true",
            f"slam_params_file:={params_path}",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _register_process(
                "slam", proc, cmd,
                config_path=str(params_path),
                topics_used=[scan_topic, "/odom", "/tf"],
            )
            return {
                "status": "launched", "algorithm": "slam_toolbox",
                "pid": proc.pid, "params_path": str(params_path),
                "scan_topic": scan_topic,
                "message": "slam_toolbox launched. Drive the robot to map the environment, then call slam_stop to save the map.",
            }
        except FileNotFoundError:
            return {
                "error": "slam_toolbox not found",
                "fix": "sudo apt install ros-${ROS_DISTRO}-slam-toolbox",
            }
        except Exception as e:
            return {"error": f"Failed to launch slam_toolbox: {e}"}

    # rtabmap
    rgb_topic = _first_topic(topics, r"(rgb|image_raw)") or "/camera/rgb/image_raw"
    depth_topic = _first_topic(topics, r"/depth") or "/camera/depth/image_raw"
    info_topic = _first_topic(topics, r"/camera_info") or "/camera/rgb/camera_info"
    cmd = [
        "ros2", "launch", "rtabmap_launch", "rtabmap.launch.py",
        "use_sim_time:=true",
        f"rgb_topic:={rgb_topic}",
        f"depth_topic:={depth_topic}",
        f"camera_info_topic:={info_topic}",
        f"frame_id:={base_frame}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _register_process("slam", proc, cmd, topics_used=[rgb_topic, depth_topic])
        return {
            "status": "launched", "algorithm": "rtabmap", "pid": proc.pid,
            "message": "rtabmap SLAM launched.",
        }
    except FileNotFoundError:
        return {
            "error": "rtabmap not found",
            "fix": "sudo apt install ros-${ROS_DISTRO}-rtabmap-ros",
        }
    except Exception as e:
        return {"error": f"Failed to launch rtabmap: {e}"}


# ── Process registry management ───────────────────────────────────────────────

async def handle_list_launched(_args: Dict[str, Any]) -> Dict[str, Any]:
    """List all Isaac Assist–managed ROS2 subprocesses."""
    infos = [_process_info(n) for n in list(_LAUNCHED_PROCESSES.keys())]
    infos = [i for i in infos if i]
    return {
        "processes": infos,
        "count": len(infos),
        "running": sum(1 for i in infos if i["running"]),
    }


async def handle_stop_launched(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a named managed process (or all processes if name omitted)."""
    name = args.get("name", "")
    if not name:
        stopped = []
        for n in list(_LAUNCHED_PROCESSES.keys()):
            pid = await _stop_process(n)
            if pid:
                stopped.append({"name": n, "pid": pid})
        return {"stopped": stopped, "count": len(stopped)}
    pid = await _stop_process(name)
    if pid is None:
        return {
            "error": f"No process '{name}'",
            "known_processes": list(_LAUNCHED_PROCESSES.keys()),
        }
    return {"status": "stopped", "name": name, "pid": pid}


async def handle_restart_launched(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stop and re-launch a named managed process using its original command."""
    name = args.get("name", "")
    if not name:
        return {"error": "name is required"}
    entry = _LAUNCHED_PROCESSES.get(name)
    if not entry:
        return {"error": f"No process '{name}'", "known_processes": list(_LAUNCHED_PROCESSES.keys())}

    cmd = entry.get("cmd", [])
    config_path = entry.get("config_path")
    topics_used = entry.get("topics_used", [])
    await _stop_process(name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _register_process(name, proc, cmd, config_path=config_path, topics_used=topics_used)
        return {"status": "restarted", "name": name, "new_pid": proc.pid, "cmd": " ".join(cmd)}
    except Exception as e:
        return {"error": f"Failed to restart {name}: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE D — MAPPING & NAVIGATION
# ═══════════════════════════════════════════════════════════════════════════

async def handle_slam_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Start SLAM with a sensor health gate. Requires lidar_scan + odom + tf to
    be healthy before launching. Delegates to launch_slam.
    """
    health = await handle_check_sensor_health({})
    critical_sensors = {"lidar_scan", "odom", "tf"}
    missing = [
        s for s in health.get("sensors", [])
        if s["sensor"] in critical_sensors and s["status"] in ("missing", "error")
    ]
    if missing:
        return {
            "status": "sensor_gate_failed",
            "missing_sensors": [s["sensor"] for s in missing],
            "fixes": [s.get("fix", "") for s in missing if s.get("fix")],
            "message": "Required sensors not healthy — fix before starting SLAM",
        }
    return await handle_launch_slam(args)


async def handle_slam_stop(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save the current SLAM map (slam_toolbox/save_map service or nav2 map_saver),
    then stop the managed SLAM process.
    """
    map_name = args.get("map_name", "map")
    map_saved = False
    save_path = ""

    topics = await _get_active_topics()
    if _has_topic(topics, r"^/map$") or "slam" in _LAUNCHED_PROCESSES:
        try:
            scene_dir = await _get_scene_dir()
            (scene_dir / "maps").mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            map_dir = scene_dir / "maps" / map_name
            map_dir.mkdir(parents=True, exist_ok=True)
            save_path = str(map_dir / "map")

            # Try slam_toolbox service
            p = await asyncio.create_subprocess_exec(
                "ros2", "service", "call",
                "/slam_toolbox/save_map",
                "slam_toolbox/srv/SaveMap",
                f"{{name: {{data: '{save_path}'}}}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(p.communicate(), timeout=10.0)
            map_saved = p.returncode == 0

            if not map_saved:
                # Fallback: nav2 map_saver_cli
                p2 = await asyncio.create_subprocess_exec(
                    "ros2", "run", "nav2_map_server", "map_saver_cli",
                    "-f", save_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(p2.communicate(), timeout=10.0)
                map_saved = p2.returncode == 0
        except Exception as e:
            logger.warning(f"[SLAM] Map save error: {e}")

    pid = await _stop_process("slam")
    return {
        "status": "stopped",
        "slam_pid": pid,
        "map_saved": map_saved,
        "map_pgm": f"{save_path}.pgm" if map_saved else None,
        "map_yaml": f"{save_path}.yaml" if map_saved else None,
        "message": (
            f"SLAM stopped. Map saved: {save_path}.pgm / .yaml"
            if map_saved else
            "SLAM stopped. Map save failed — check /map topic is active and slam_toolbox is running."
        ),
    }


async def handle_slam_status(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Return current SLAM process status and /map topic activity."""
    topics = await _get_active_topics()
    map_active = _has_topic(topics, r"^/map$")
    entry = _LAUNCHED_PROCESSES.get("slam")
    if not entry:
        return {"running": False, "map_topic_active": map_active, "message": "SLAM not running"}
    proc = entry["proc"]
    running = proc.returncode is None
    algorithm = "slam_toolbox" if "slam_toolbox" in " ".join(entry.get("cmd", [])) else "rtabmap"
    return {
        "running": running,
        "pid": entry["pid"],
        "algorithm": algorithm,
        "launch_time": entry["launch_time"],
        "config_path": entry.get("config_path"),
        "map_topic_active": map_active,
        "return_code": proc.returncode,
        "message": (
            f"{algorithm} running — drive robot to map the environment"
            if running else f"{algorithm} process has exited (code {proc.returncode})"
        ),
    }


async def handle_map_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Export the current map in multiple formats: Nav2 pgm+yaml, PNG, and
    (optionally) a ROS2 bag. Returns paths to all exported files.
    """
    map_name = args.get("map_name", "map")
    formats = args.get("formats", ["nav2"])  # nav2, png, bag

    scene_dir = await _get_scene_dir()
    (scene_dir / "maps").mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    map_dir = scene_dir / "maps" / f"{map_name}_{ts}"
    map_dir.mkdir(parents=True, exist_ok=True)
    save_base = str(map_dir / "map")

    exported: Dict[str, str] = {}
    errors: List[str] = []

    # Nav2 pgm+yaml via map_saver_cli
    if "nav2" in formats or not formats:
        try:
            p = await asyncio.create_subprocess_exec(
                "ros2", "run", "nav2_map_server", "map_saver_cli",
                "-f", save_base, "--ros-args", "-p", "use_sim_time:=true",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(p.communicate(), timeout=15.0)
            if p.returncode == 0:
                exported["pgm"] = f"{save_base}.pgm"
                exported["yaml"] = f"{save_base}.yaml"
            else:
                errors.append("map_saver_cli failed — is Nav2 running and /map topic active?")
        except FileNotFoundError:
            errors.append("nav2_map_server not found. Install: sudo apt install ros-${ROS_DISTRO}-nav2-map-server")
        except asyncio.TimeoutError:
            errors.append("map_saver_cli timed out")
        except Exception as e:
            errors.append(f"Map save error: {e}")

    # PNG conversion via imagemagick/pillow if pgm was saved
    if "png" in formats and "pgm" in exported:
        png_path = f"{save_base}.png"
        try:
            import PIL.Image
            img = PIL.Image.open(exported["pgm"])
            img.save(png_path)
            exported["png"] = png_path
        except ImportError:
            try:
                r = subprocess.run(["convert", exported["pgm"], png_path], timeout=5)
                if r.returncode == 0:
                    exported["png"] = png_path
                else:
                    errors.append("ImageMagick convert failed for PNG export")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                errors.append("PNG export requires pillow (pip install pillow) or ImageMagick")

    # ROS2 bag: record /map for 2 seconds
    if "bag" in formats:
        bag_path = str(map_dir / "map_bag")
        try:
            p = await asyncio.create_subprocess_exec(
                "ros2", "bag", "record", "-o", bag_path,
                "/map", "--max-bag-duration", "2",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(p.communicate(), timeout=10.0)
            if p.returncode == 0:
                exported["bag"] = bag_path
            else:
                errors.append("ros2 bag record failed")
        except Exception as e:
            errors.append(f"Bag export error: {e}")

    return {
        "map_dir": str(map_dir),
        "exported": exported,
        "formats_requested": formats,
        "errors": errors,
        "success": len(exported) > 0,
        "message": (
            f"Map exported to {map_dir}" if exported else
            "Map export failed. Ensure Nav2 or SLAM is running with /map topic active."
        ),
    }


async def handle_nav2_goto(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a Nav2 navigation goal by coordinates, named location, or relative offset.
    Publishes a PoseStamped to /goal_pose.
    """
    x = float(args.get("x", 0.0))
    y = float(args.get("y", 0.0))
    yaw = float(args.get("yaw", 0.0))
    frame_id = args.get("frame_id", "map")
    location_name = args.get("location_name", "")

    # Resolve named location if provided
    if location_name:
        import json
        scene_dir = await _get_scene_dir()
        locations_file = scene_dir / "locations.json"
        if locations_file.exists():
            locs = json.loads(locations_file.read_text())
            loc = locs.get(location_name)
            if loc:
                x = float(loc.get("x", x))
                y = float(loc.get("y", y))
                yaw = float(loc.get("yaw", yaw))
                frame_id = loc.get("frame_id", frame_id)
            else:
                return {
                    "error": f"Location '{location_name}' not found",
                    "available_locations": list(locs.keys()),
                }
        else:
            return {
                "error": f"No saved locations file found",
                "fix": "Use save_location tool to save named locations",
            }

    qz = math.sin(yaw / 2.0)
    qw = math.cos(yaw / 2.0)

    try:
        from .ros_mcp_tools import handle_ros2_publish
        result = await handle_ros2_publish({
            "topic": "/goal_pose",
            "msg_type": "geometry_msgs/msg/PoseStamped",
            "data": {
                "header": {"frame_id": frame_id, "stamp": {"sec": 0, "nanosec": 0}},
                "pose": {
                    "position": {"x": x, "y": y, "z": 0.0},
                    "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                },
            },
        })
        if "error" in result:
            return {
                "status": "error", "error": result["error"],
                "fix": "Ensure Nav2 is running (launch_nav2) and /goal_pose topic is active",
            }
        return {
            "status": "goal_sent",
            "x": x, "y": y, "yaw": yaw, "frame_id": frame_id,
            "location_name": location_name or None,
            "message": f"Navigation goal sent to ({x:.2f}, {y:.2f}) yaw={yaw:.2f} rad in frame '{frame_id}'",
        }
    except Exception as e:
        return {"error": f"Failed to send nav2 goal: {e}"}


async def handle_save_location(args: Dict[str, Any]) -> Dict[str, Any]:
    """Save current /odom pose under a user-defined name for later nav2_goto use."""
    import json
    location_name = args.get("name", "")
    if not location_name:
        return {"error": "name is required"}

    # Read current /odom pose via rosbridge
    try:
        from .ros_mcp_tools import handle_ros2_subscribe_once
        result = await handle_ros2_subscribe_once({
            "topic": "/odom",
            "msg_type": "nav_msgs/msg/Odometry",
            "timeout": 3.0,
        })
        if "error" in result:
            return {"error": f"Could not read /odom: {result['error']}"}
        msg = result.get("msg", {})
        pose = msg.get("pose", {}).get("pose", {})
        pos = pose.get("position", {})
        orient = pose.get("orientation", {})
        # Quaternion to yaw
        qz = float(orient.get("z", 0.0))
        qw = float(orient.get("w", 1.0))
        yaw = 2.0 * math.atan2(qz, qw)
        location = {
            "x": float(pos.get("x", 0.0)),
            "y": float(pos.get("y", 0.0)),
            "yaw": yaw,
            "frame_id": "odom",
        }
    except Exception as e:
        return {"error": f"Failed to read /odom: {e}"}

    import json
    scene_dir = await _get_scene_dir()
    locations_file = scene_dir / "locations.json"
    locations: Dict[str, Any] = {}
    if locations_file.exists():
        locations = json.loads(locations_file.read_text())
    locations[location_name] = location
    locations_file.write_text(json.dumps(locations, indent=2))
    return {
        "status": "saved",
        "name": location_name,
        "location": location,
        "all_locations": list(locations.keys()),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ISAAC ROS VISUAL SLAM — 3D SLAM + 2D COSTMAP STACK
# ═══════════════════════════════════════════════════════════════════════════

_VISUAL_SLAM_PARAMS = """\
# Isaac ROS cuVSLAM Parameters
# Docs: https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_visual_slam/index.html
visual_slam_node:
  ros__parameters:
    # ── Camera ──────────────────────────────────────────────────────────────
    num_cameras: {num_cameras}
    min_num_images: {num_cameras}
    rectified_images: true
    enable_image_denoising: false
    sync_matching_threshold_ms: 5.0
    image_jitter_threshold_ms: 34.0
    image_buffer_size: 100
    image_qos: "DEFAULT"

    # tracking_mode: 0=Multi-camera  1=Visual-Inertial  2=RGBD
    tracking_mode: {tracking_mode}

    # ── IMU (tracking_mode=1 only) ───────────────────────────────────────────
    imu_buffer_size: 50
    imu_jitter_threshold_ms: 10.0
    gyro_noise_density: {gyro_noise_density}
    gyro_random_walk: {gyro_random_walk}
    accel_noise_density: {accel_noise_density}
    accel_random_walk: {accel_random_walk}
    calibration_frequency: 200.0
    imu_qos: "DEFAULT"

    # ── RGBD (tracking_mode=2 only) ──────────────────────────────────────────
    depth_scale_factor: 1000.0
    depth_camera_id: 0
    depth_enable_stereo_tracking: false

    # ── Map frames ──────────────────────────────────────────────────────────
    map_frame: "map"
    odom_frame: "odom"
    base_frame: "{base_frame}"
    imu_frame: "imu"
    publish_map_to_odom_tf: true
    publish_odom_to_base_tf: true
    invert_map_to_odom_tf: false
    invert_odom_to_base_tf: false

    # ── SLAM mode ───────────────────────────────────────────────────────────
    enable_localization_n_mapping: {enable_slam}
    enable_ground_constraint_in_odometry: {enable_ground_constraint}
    enable_ground_constraint_in_slam: {enable_ground_constraint}
    slam_max_map_size: 300

    # ── Map persistence ─────────────────────────────────────────────────────
    save_map_folder_path: "{save_map_folder_path}"
    load_map_folder_path: "{load_map_folder_path}"
    localize_on_startup: {localize_on_startup}
    localizer_horizontal_radius: 1.5
    localizer_vertical_radius: 0.5
    localizer_horizontal_step: 0.5
    localizer_vertical_step: 0.25
    localizer_angular_step: 0.1745

    # ── Output / debug ──────────────────────────────────────────────────────
    override_publishing_stamp: false
    enable_slam_visualization: {enable_visualization}
    enable_landmarks_view: {enable_visualization}
    enable_observations_view: {enable_visualization}
    path_max_size: 1024
    verbosity: 0
    enable_debug_mode: false
    debug_dump_path: "{debug_dump_path}"
"""


async def handle_launch_visual_slam(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch Isaac ROS Visual SLAM (cuVSLAM).

    tracking_mode:
      0 = Multi-camera stereo (default)
      1 = Visual-Inertial (requires imu_topic)
      2 = RGBD (requires depth_topic)

    Input → visual_slam/image_{i} remapped from left/right image topics
    Output → visual_slam/tracking/odometry  (nav_msgs/Odometry)
             visual_slam/tracking/slam_path (nav_msgs/Path)
             visual_slam/tracking/vo_pose   (geometry_msgs/PoseStamped)
             /tf  (odom → base_link)
    Services → /visual_slam/save_map, /visual_slam/load_map, /visual_slam/reset,
               /visual_slam/localize_in_map, /visual_slam/get_all_poses
    """
    base_frame    = args.get("base_frame", "base_link")
    left_image    = args.get("left_image_topic",  "front_stereo_camera/left/image_rect_color")
    right_image   = args.get("right_image_topic", "front_stereo_camera/right/image_rect_color")
    tracking_mode = int(args.get("tracking_mode", 0))  # 0=stereo, 1=VIO, 2=RGBD
    enable_slam   = bool(args.get("enable_localization_n_mapping", True))
    enable_viz    = bool(args.get("enable_slam_visualization", True))
    enable_ground = bool(args.get("enable_ground_constraint", False))

    # IMU params (tracking_mode=1)
    imu_topic           = args.get("imu_topic", "imu/data")
    gyro_noise_density  = float(args.get("gyro_noise_density",  0.000244))
    gyro_random_walk    = float(args.get("gyro_random_walk",    0.000019393))
    accel_noise_density = float(args.get("accel_noise_density", 0.001862))
    accel_random_walk   = float(args.get("accel_random_walk",   0.003))

    # RGBD param (tracking_mode=2)
    depth_topic = args.get("depth_topic", "front_stereo_camera/left/depth")

    def _camera_info_topic(image_topic: str) -> str:
        parts = image_topic.rsplit("/", 1)
        return parts[0] + "/camera_info" if len(parts) == 2 else image_topic + "/camera_info"

    left_info  = args.get("left_camera_info_topic",  _camera_info_topic(left_image))
    right_info = args.get("right_camera_info_topic", _camera_info_topic(right_image))

    if not shutil.which("ros2"):
        return {"error": "ros2 CLI not found. Source ROS2: source /opt/ros/jazzy/setup.bash"}

    check = subprocess.run(
        ["ros2", "pkg", "list"], capture_output=True, text=True, timeout=5
    )
    if "isaac_ros_visual_slam" not in check.stdout:
        return {
            "error": "isaac_ros_visual_slam package not found",
            "fix": (
                "Build Isaac ROS Visual SLAM in your workspace:\n"
                "  cd ~/ros2_ws/src && git clone https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_visual_slam\n"
                "  cd ~/ros2_ws && colcon build --symlink-install --packages-select isaac_ros_visual_slam\n"
                "  source ~/ros2_ws/install/setup.bash"
            ),
        }

    topics = await _get_active_topics()
    required = [left_image, right_image, left_info, right_info]
    if tracking_mode == 1:
        required.append(imu_topic)
    if tracking_mode == 2:
        required.append(depth_topic)
    missing_topics = [t for t in required if t not in topics]
    if missing_topics:
        return {
            "status": "missing_camera_topics",
            "missing": missing_topics,
            "fix": (
                "Wire a stereo OmniGraph: create_omnigraph_from_template "
                "template='ros2_stereo_camera' with your camera prim paths"
            ),
        }

    scene_dir = await _get_scene_dir()
    vs_dir    = scene_dir / "visual_slam"
    vs_dir.mkdir(exist_ok=True)
    ts           = time.strftime("%Y%m%d_%H%M%S")
    params_path  = vs_dir / f"params_{ts}.yaml"

    # Map persistence paths
    load_map_path  = args.get("load_map_folder_path", "")
    save_map_path  = args.get("save_map_folder_path", str(vs_dir / "map_autosave"))
    localize_on_startup = str(bool(args.get("localize_on_startup", False))).lower()

    params_path.write_text(_VISUAL_SLAM_PARAMS.format(
        base_frame=base_frame,
        num_cameras=2,
        tracking_mode=tracking_mode,
        enable_slam=str(enable_slam).lower(),
        enable_visualization=str(enable_viz).lower(),
        enable_ground_constraint=str(enable_ground).lower(),
        gyro_noise_density=gyro_noise_density,
        gyro_random_walk=gyro_random_walk,
        accel_noise_density=accel_noise_density,
        accel_random_walk=accel_random_walk,
        save_map_folder_path=save_map_path,
        load_map_folder_path=load_map_path,
        localize_on_startup=localize_on_startup,
        debug_dump_path=str(vs_dir / "debug"),
    ))

    cmd = [
        "ros2", "launch", "isaac_ros_visual_slam", "isaac_ros_visual_slam.launch.py",
        "--ros-args",
        "-r", f"visual_slam/image_0:={left_image}",
        "-r", f"visual_slam/camera_info_0:={left_info}",
        "-r", f"visual_slam/image_1:={right_image}",
        "-r", f"visual_slam/camera_info_1:={right_info}",
        "--params-file", str(params_path),
    ]
    if tracking_mode == 1:
        cmd += ["-r", f"visual_slam/imu:={imu_topic}"]
    if tracking_mode == 2:
        cmd += ["-r", f"visual_slam/depth_0:={depth_topic}"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _register_process(
            "visual_slam", proc, cmd,
            config_path=str(params_path),
            topics_used=required,
        )
        tracking_label = {0: "Multi-camera stereo", 1: "Visual-Inertial", 2: "RGBD"}[tracking_mode]
        return {
            "status":         "launched",
            "pid":            proc.pid,
            "params_path":    str(params_path),
            "tracking_mode":  tracking_label,
            "save_map_path":  save_map_path,
            "input_topics":   {"left_image": left_image, "left_info": left_info,
                               "right_image": right_image, "right_info": right_info},
            "output_topics":  {"odometry": "visual_slam/tracking/odometry",
                               "path": "visual_slam/tracking/slam_path",
                               "pose": "visual_slam/tracking/vo_pose",
                               "status": "visual_slam/status"},
            "services":       ["/visual_slam/save_map", "/visual_slam/load_map",
                               "/visual_slam/reset", "/visual_slam/localize_in_map",
                               "/visual_slam/get_all_poses", "/visual_slam/set_slam_pose"],
            "message": (
                f"Isaac ROS Visual SLAM launched ({tracking_label}, PID {proc.pid}). "
                f"Odometry: visual_slam/tracking/odometry. "
                f"Map auto-saves to {save_map_path} on shutdown. "
                f"Use launch_nav2 with odom_topic='visual_slam/tracking/odometry'. "
                f"Use save_visual_slam_map to persist manually."
            ),
        }
    except FileNotFoundError:
        return {"error": "Failed to launch isaac_ros_visual_slam",
                "fix": "Ensure isaac_ros_visual_slam is built and workspace is sourced"}
    except Exception as e:
        return {"error": f"Failed to launch visual SLAM: {e}"}


async def handle_launch_depth_to_laserscan(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Launch depthimage_to_laserscan to convert a depth image into a 2D LaserScan
    for Nav2 costmap_2d. Typically feeds from the left stereo camera depth topic.

    Input:  depth image topic (e.g. front_stereo_camera/left/depth)
    Output: /scan  (sensor_msgs/LaserScan) for Nav2 2D costmap
    """
    depth_topic = args.get(
        "depth_topic", "front_stereo_camera/left/depth"
    )
    camera_info_topic = args.get(
        "camera_info_topic",
        depth_topic.rsplit("/", 1)[0] + "/camera_info",
    )
    output_scan_topic = args.get("scan_topic", "/scan")
    output_frame = args.get("output_frame", "base_link")
    range_min = float(args.get("range_min", 0.1))
    range_max = float(args.get("range_max", 10.0))
    scan_height = int(args.get("scan_height", 1))

    if not shutil.which("ros2"):
        return {"error": "ros2 CLI not found. Source ROS2: source /opt/ros/jazzy/setup.bash"}

    # Check package availability
    check = subprocess.run(
        ["ros2", "pkg", "list"], capture_output=True, text=True, timeout=5
    )
    if "depthimage_to_laserscan" not in check.stdout:
        return {
            "error": "depthimage_to_laserscan not found",
            "fix": f"sudo apt install ros-${{ROS_DISTRO}}-depthimage-to-laserscan",
        }

    # Verify depth topic is active
    topics = await _get_active_topics()
    if depth_topic not in topics:
        return {
            "status": "missing_depth_topic",
            "missing": depth_topic,
            "fix": (
                "Wire stereo cameras: create_omnigraph_from_template "
                "template='ros2_stereo_camera' with left_camera_path set"
            ),
        }

    cmd = [
        "ros2", "run", "depthimage_to_laserscan", "depthimage_to_laserscan_node",
        "--ros-args",
        "-r", f"image:={depth_topic}",
        "-r", f"camera_info:={camera_info_topic}",
        "-r", f"scan:={output_scan_topic}",
        "-p", f"output_frame_id:={output_frame}",
        "-p", f"range_min:={range_min}",
        "-p", f"range_max:={range_max}",
        "-p", f"scan_height:={scan_height}",
        "-p", "use_sim_time:=true",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _register_process(
            "depth_to_laserscan", proc, cmd,
            topics_used=[depth_topic, camera_info_topic, output_scan_topic],
        )
        return {
            "status": "launched",
            "pid": proc.pid,
            "depth_topic": depth_topic,
            "camera_info_topic": camera_info_topic,
            "scan_topic": output_scan_topic,
            "output_frame": output_frame,
            "range_min": range_min,
            "range_max": range_max,
            "message": (
                f"depthimage_to_laserscan launched.\n"
                f"Depth: {depth_topic} → LaserScan: {output_scan_topic}\n"
                f"Use launch_nav2 with scan_topic='{output_scan_topic}' for 2D costmap."
            ),
        }
    except FileNotFoundError:
        return {
            "error": "depthimage_to_laserscan node not found",
            "fix": f"sudo apt install ros-${{ROS_DISTRO}}-depthimage-to-laserscan",
        }
    except Exception as e:
        return {"error": f"Failed to launch depth_to_laserscan: {e}"}


async def handle_save_visual_slam_map(_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save the current Isaac ROS Visual SLAM (cuVSLAM) 3D map to disk via the
    /visual_slam/save_map service. Saves to workspace/scenes/<scene>/visual_slam/map/.

    The saved map can later be loaded with /visual_slam/load_map_and_localize for
    re-localization in a known environment without re-running full SLAM.
    """
    if not shutil.which("ros2"):
        return {"error": "ros2 CLI not found"}

    topics = await _get_active_topics()
    vs_running = "visual_slam" in _LAUNCHED_PROCESSES or any(
        "visual_slam" in t for t in topics
    )
    if not vs_running:
        return {
            "error": "Visual SLAM does not appear to be running",
            "fix": "Run launch_visual_slam first",
        }

    scene_dir = await _get_scene_dir()
    ts = time.strftime("%Y%m%d_%H%M%S")
    vs_map_dir = scene_dir / "visual_slam" / f"map_{ts}"
    vs_map_dir.mkdir(parents=True, exist_ok=True)
    map_path = str(vs_map_dir)

    try:
        p = await asyncio.create_subprocess_exec(
            "ros2", "service", "call",
            "/visual_slam/save_map",
            "isaac_ros_visual_slam_interfaces/srv/SaveMap",
            f"{{map_url: {{data: '{map_path}'}}}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(p.communicate(), timeout=15.0)
        if p.returncode == 0:
            return {
                "status": "saved",
                "map_path": map_path,
                "scene_dir": str(scene_dir),
                "message": (
                    f"3D cuVSLAM map saved to {map_path}\n"
                    f"To relocalize later: ros2 service call /visual_slam/load_map_and_localize "
                    f"isaac_ros_visual_slam_interfaces/srv/LoadMapAndLocalize "
                    f"\"{{map_url: {{data: '{map_path}'}}}}\""
                ),
            }
        else:
            return {
                "error": "save_map service call failed",
                "stderr": stderr.decode()[:500],
                "fix": "Ensure isaac_ros_visual_slam is running and has a valid map",
            }
    except asyncio.TimeoutError:
        return {"error": "save_map service call timed out (15 s)"}
    except Exception as e:
        return {"error": f"Failed to save Visual SLAM map: {e}"}
