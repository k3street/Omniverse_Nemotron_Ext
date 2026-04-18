# Isaac Assist — ROS2 Autonomy & Scene Intelligence Plan

**Author:** 10Things, Inc. — [www.10things.tech](http://www.10things.tech)  
**Date:** April 17, 2026  
**Target:** Isaac Sim 5.1 on DGX Spark (aarch64) / ROS2 Jazzy  
**Depends on:** Existing rosbridge MCP tools (11 handlers), Gemini Vision provider, Pipeline planner, Export system

---

## Executive Summary

Extend Isaac Assist from "build and drive a robot in sim" to **full autonomy stack integration**: SLAM mapping, Nav2 navigation, object classification, segmentation, vision-language commands, RViz2 visualization, ros2_control, and Gazebo co-sim — all launchable from chat or MCP tools. The system checks scene readiness, detects machine capabilities, suggests next steps, and exports complete ROS2 project packages as ZIP files that users can drop into their own workspaces.

---

## Current State (What We Have)

| Capability | Status |
|---|---|
| ROS2 topic pub/sub via rosbridge | ✅ 11 MCP tools |
| OmniGraph: diff drive, camera, odom, clock, joint state | ✅ Pipeline templates |
| Gemini Robotics-ER vision (detect, bbox, trajectory, analyze) | ✅ 4 tools |
| Isaac Sim sensors: Camera, LiDAR RTX, IMU, Contact | ✅ `add_sensor_to_prim` |
| Scene export (directory: scene_setup.py + README + ros2_launch) | ✅ `export_scene_package` |
| Stage analyzer (1/8 validators) | ⚠️ Partial |
| Occupancy map generation | ⚠️ In PLAN.md, not implemented |
| Nav2, SLAM, RViz2, Gazebo, ros2_control | ❌ Not started |
| ZIP export | ❌ Not started |
| Machine-aware suggestions | ❌ Not started |
| User project folder integration | ❌ Not started |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Isaac Sim (Kit + OmniGraph)                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Camera ROS2      │  │ LiDAR ROS2       │  │ IMU / Contact    │  │
│  │ RGB + Depth +    │  │ LaserScan +      │  │ sensor_msgs/     │  │
│  │ CameraInfo +     │  │ PointCloud2      │  │ Imu + Contact    │  │
│  │ Segmentation     │  │                  │  │                  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │ /camera/*           │ /scan, /points       │ /imu, etc  │
│  ┌────────┴─────────────────────┴──────────────────────┴─────────┐  │
│  │ ROS2 Bridge (isaacsim.ros2.bridge)                             │  │
│  │  OmniGraph nodes → ROS2 topics via DDS                        │  │
│  └────────────────────────────────┬──────────────────────────────┘  │
└───────────────────────────────────┼──────────────────────────────────┘
                                    │ DDS (localhost)
┌───────────────────────────────────┼──────────────────────────────────┐
│  ROS2 Jazzy Stack (host)          │                                  │
│                                   │                                  │
│  ┌────────────┐  ┌────────────┐  │  ┌────────────┐  ┌────────────┐ │
│  │ Nav2       │  │ SLAM       │  │  │ ros2_ctrl  │  │ RViz2      │ │
│  │ navigation │  │ rtabmap /  │  │  │ diff_drive │  │ visualizer │ │
│  │ + costmaps │  │ slam_tbx   │  │  │ controller │  │            │ │
│  └────────────┘  └────────────┘  │  └────────────┘  └────────────┘ │
│                                   │                                  │
│  ┌────────────┐  ┌────────────┐  │  ┌────────────┐                 │
│  │ Gemini ER  │  │ Object     │  │  │ Gazebo     │                 │
│  │ scene VQA  │  │ classifier │  │  │ co-sim     │                 │
│  └────────────┘  └────────────┘  │  └────────────┘                 │
└──────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────┼──────────────────────────────────┐
│  Isaac Assist Service :8000       │                                  │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ MCP Tools Layer                                                 ││
│  │                                                                 ││
│  │  LAUNCH TOOLS        READINESS          AUTONOMY                ││
│  │  ─────────────       ─────────          ────────                ││
│  │  launch_rviz2        check_scene_ready  nav2_goto               ││
│  │  launch_nav2         suggest_next_step  slam_start / slam_stop  ││
│  │  launch_slam         check_sensor_health map_export             ││
│  │  launch_ros2_ctrl    get_machine_specs  classify_objects        ││
│  │  launch_gazebo       check_ros2_ready   get_segmentation_map   ││
│  │                                         vision_command          ││
│  │  EXPORT                                                         ││
│  │  ──────                                                         ││
│  │  export_project_zip                                             ││
│  │  scaffold_ros2_workspace                                        ││
│  │  connect_user_model                                             ││
│  └─────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

---

## Phase A — Scene Readiness & Machine-Aware Suggestions (Week 1)

**Goal:** Before users can launch RViz2 or Nav2, the system must know what's available. Inspect the scene, the machine, and the ROS2 environment, then tell the user what to do next.

### A1 — `check_scene_ready` (MCP + chat tool)

Inspects the current Isaac Sim scene and ROS2 state and returns a structured readiness report.

**Checks performed:**

| Check | How | Result |
|-------|-----|--------|
| Simulation running? | Kit RPC: `omni.timeline` state | `sim_playing: bool` |
| Robot in scene? | `list_all_prims(type=Articulation)` | `robots: [{path, type, joints}]` |
| Drive graph wired? | Scan OmniGraph for DiffController / HolonomicController | `drive_ready: bool, drive_type: str` |
| Camera topics publishing? | `ros2_list_topics` → filter `/camera/*`, `/rgb`, `/depth`, `/image_raw` | `cameras: [{topic, type, hz}]` |
| LiDAR topics publishing? | `ros2_list_topics` → filter `/scan`, `/points`, `/lidar` | `lidars: [{topic, type, hz}]` |
| IMU topics publishing? | `ros2_list_topics` → filter `/imu` | `imus: [{topic, type}]` |
| Odometry publishing? | `ros2_list_topics` → filter `/odom` | `odom_ready: bool` |
| TF tree available? | `ros2_list_topics` → check `/tf`, `/tf_static` | `tf_ready: bool` |
| Clock publishing? | `ros2_list_topics` → check `/clock` | `clock_ready: bool` |
| Map available? | Check for `/map` topic or local `.pgm`/`.yaml` files | `map_available: bool` |
| Rosbridge connected? | Ping test | `rosbridge_ok: bool` |

**Return format:**
```json
{
  "ready": true,
  "score": 8,
  "max_score": 11,
  "checks": { ... },
  "missing": ["lidar_not_publishing", "no_map_available"],
  "suggested_next_steps": [
    {"action": "launch_rviz2", "reason": "Camera + odom topics are live — visualize them in RViz2"},
    {"action": "launch_slam", "reason": "Stereo cameras publishing but no map yet — start SLAM to build one"},
    {"action": "add_lidar", "reason": "No LiDAR detected — add one for Nav2 costmaps"}
  ]
}
```

### A2 — `get_machine_specs` (MCP + chat tool)

Detect host machine capabilities to tailor recommendations.

**Detected specs:**

| Spec | Source | Used for |
|------|--------|----------|
| GPU model + VRAM | `nvidia-smi` | Recommend sensor resolutions, num cameras |
| CPU cores + model | `/proc/cpuinfo` | Nav2 planner complexity, SLAM algorithm choice |
| RAM total + free | `/proc/meminfo` | Warn if too many sensor streams |
| Disk free | `df` | ZIP export feasibility, map storage |
| ROS2 distro | `$ROS_DISTRO` | Package compatibility (Jazzy vs Humble) |
| Architecture | `uname -m` | aarch64 vs x86_64 package availability |
| Isaac Sim version | Kit RPC `APP_VERSION` | Extension compatibility |
| Available ROS2 packages | `ros2 pkg list` via rosbridge or subprocess | What can be launched |

**Machine-aware suggestions:**

| Condition | Suggestion |
|-----------|------------|
| GPU VRAM < 8 GB | "Reduce camera resolution to 640×480 and limit to 2 camera streams" |
| GPU VRAM ≥ 16 GB | "Full resolution sensors supported — you can run 4+ cameras at 1280×720" |
| No `nav2_bringup` package | "Install Nav2: `sudo apt install ros-${ROS_DISTRO}-navigation2 ros-${ROS_DISTRO}-nav2-bringup`" |
| No `slam_toolbox` package | "Install SLAM Toolbox: `sudo apt install ros-${ROS_DISTRO}-slam-toolbox`" |
| No `rtabmap_ros` package | "For stereo camera SLAM: `sudo apt install ros-${ROS_DISTRO}-rtabmap-ros`" |
| No `rviz2` binary | "Install RViz2: `sudo apt install ros-${ROS_DISTRO}-rviz2`" |
| aarch64 + Jazzy | "Some packages may need source build on aarch64 — check availability" |
| RAM < 16 GB | "Consider using SLAM Toolbox (lighter) instead of rtabmap (heavier)" |
| No `ros2_control` packages | "Install ros2_control: `sudo apt install ros-${ROS_DISTRO}-ros2-control ros-${ROS_DISTRO}-ros2-controllers`" |

### A3 — `suggest_next_steps` (MCP + chat tool)

Combines `check_scene_ready` + `get_machine_specs` + current topic list into an ordered recommendation list. This is what the user sees when they ask "what should I do next?"

**Logic:**

```
1. If sim not playing → "Start the simulation first (play button or type 'play the simulation')"
2. If no robot → "Import a robot first (e.g., 'pipeline: Nova Carter in a home')"
3. If robot but no drive graph → "Wire a ROS2 drive graph (e.g., 'create a differential drive OmniGraph for /World/Robot')"
4. If drive graph but no odom → "Add odometry publisher to the drive graph"
5. If no cameras → "Add a camera sensor (e.g., 'add a RealSense D435i to the robot')"
6. If cameras but no camera topics → "Wire a camera ROS2 graph with IsaacCreateRenderProduct"
7. If odom + camera topics live → "Launch RViz2 to visualize: 'launch rviz2 with camera and odom'"
8. If odom + lidar but no map → "Start SLAM: 'launch slam with the lidar'"
9. If map available → "Launch Nav2: 'launch nav2 with the saved map'"
10. If Nav2 running → "Navigate: 'go to the kitchen' or 'navigate to position 3, 2'"
11. If everything running → "Export your project: 'export everything as a zip'"
```

### A4 — `check_sensor_health` (MCP + chat tool)

Deep-inspect individual sensor streams for quality issues.

**Per-sensor checks:**

| Sensor | Health checks |
|--------|---------------|
| **Camera (RGB)** | Topic Hz > 10? Image dimensions match configured resolution? Encoding correct (rgb8/bgr8)? Not all-black? |
| **Camera (Depth)** | Topic Hz > 10? Range values non-zero? Encoding 32FC1 or 16UC1? Min/max depth reasonable (0.1–10m)? |
| **Camera (Segmentation)** | Topic publishing? Labels present? Class count > 0? |
| **LiDAR (LaserScan)** | Topic Hz > 5? Range values non-inf? angle_min/max reasonable? |
| **LiDAR (PointCloud2)** | Topic Hz > 5? Point count > 0? Fields include x,y,z? |
| **IMU** | Topic Hz > 50? Orientation quaternion normalized? Angular velocity non-NaN? |
| **Contact Sensor** | Topic publishing? Force values reasonable? |
| **CameraInfo** | Matches paired image topic? Intrinsics non-zero? Distortion model set? |

**Return:**
```json
{
  "sensors": [
    {"topic": "/camera/rgb", "type": "camera_rgb", "status": "healthy", "hz": 30.1, "resolution": "1280x720"},
    {"topic": "/scan", "type": "lidar_2d", "status": "warning", "hz": 5.2, "issue": "Low publish rate — expected ≥10 Hz"},
    {"topic": "/imu", "type": "imu", "status": "error", "hz": 0, "issue": "Not publishing — check OmniGraph wiring"}
  ],
  "overall": "warning",
  "recommendations": ["Increase LiDAR publish rate in OmniGraph settings", "Wire IMU sensor to ROS2 bridge"]
}
```

---

## Phase B — Launch Tools (Week 2)

**Goal:** Launch external ROS2 tools (RViz2, Nav2, SLAM, ros2_control, Gazebo) from chat or MCP, with auto-generated config files based on the current scene state.

### B1 — `launch_rviz2` (MCP + chat tool)

**Input:** Optional display config preferences (which topics to show)

**Behavior:**
1. Call `check_scene_ready` to discover active topics
2. Auto-generate an RViz2 config file (`.rviz`) based on discovered topics:

| Topic pattern | RViz2 display type |
|---------------|-------------------|
| `/camera/*/image_raw` or `/*/rgb` | `image_display` (Image panel) |
| `/camera/*/depth` | `image_display` (Depth colormap) |
| `/camera/*/camera_info` | (paired with Image display) |
| `/scan` | `LaserScan` (red dots, decay 0.1s) |
| `/points`, `/pointcloud` | `PointCloud2` (flat squares, size 0.02) |
| `/odom` | `Odometry` (arrows) |
| `/tf`, `/tf_static` | `TF` (show tree) |
| `/map` | `Map` (occupancy grid) |
| `/global_costmap/*` | `Map` (costmap overlay) |
| `/local_costmap/*` | `Map` (local costmap) |
| `/plan`, `/global_plan` | `Path` (green line) |
| `/cmd_vel` | (no display, but note in log) |
| `/joint_states` | `RobotModel` (if URDF available) |
| `/imu/data` | (no native display, note available) |
| `/segmentation/*` | `Image` (semantic overlay) |
| `/goal_pose` | `PoseStamped` (interactive marker) |

3. Write config to `workspace/rviz_configs/<scene_name>.rviz`
4. Launch: `rviz2 -d <config_path>` via subprocess (non-blocking)
5. Return PID + config path for later cleanup

**RViz config template structure:**
```yaml
Panels:
  - Class: rviz_common/Displays
  - Class: rviz_common/Views
Visualization Manager:
  Displays:
    - Class: rviz_default_plugins/Image
      Name: Camera RGB
      Topic:
        Value: /camera/rgb
        Reliability: Best Effort
    - Class: rviz_default_plugins/LaserScan
      Name: LiDAR Scan
      Topic:
        Value: /scan
      Size (m): 0.03
      Color Transformer: FlatColor
      Color: 255; 0; 0
    - Class: rviz_default_plugins/Map
      Name: Occupancy Map
      Topic:
        Value: /map
    - Class: rviz_default_plugins/TF
      Name: TF Tree
    - Class: rviz_default_plugins/Odometry
      Name: Odometry
      Topic:
        Value: /odom
```

**Chat example:**
```
User: launch rviz2 with camera and lidar
→ Isaac Assist discovers /camera/rgb (30Hz), /camera/depth (30Hz), /scan (10Hz), /odom, /tf
→ Generates .rviz config with Image + Depth + LaserScan + Odometry + TF displays
→ Launches rviz2 subprocess
→ "Launched RViz2 with 5 displays: RGB camera, Depth, LiDAR scan, Odometry, TF tree.
   Config saved to workspace/rviz_configs/carter_home.rviz"
```

### B2 — `launch_nav2` (MCP + chat tool)

**Input:** `map_file` (optional — path to `.yaml` map), `use_sim_time: true`, `robot_base_frame`, `params_overrides`

**Behavior:**
1. Check prerequisites: `/odom` topic, `/scan` or `/points` topic, `/tf` + `/tf_static`, clock publishing
2. If no map provided, check if one was previously exported or if SLAM is running
3. Auto-generate Nav2 params YAML from scene state:

```yaml
# Auto-generated by Isaac Assist
amcl:
  ros__parameters:
    use_sim_time: true
    base_frame_id: "base_link"
    odom_frame_id: "odom"
    scan_topic: "/scan"                    # auto-detected
    robot_model_type: "differential"       # from drive graph analysis

bt_navigator:
  ros__parameters:
    use_sim_time: true
    default_bt_xml_filename: "navigate_w_replanning_and_recovery.xml"

controller_server:
  ros__parameters:
    use_sim_time: true
    controller_plugins: ["FollowPath"]
    FollowPath:
      plugin: "dwb_core::DWBLocalPlanner"
      max_vel_x: 0.5                      # from DiffController maxLinearSpeed
      max_vel_theta: 1.0                   # from DiffController maxAngularSpeed
      min_vel_x: -0.1

local_costmap:
  local_costmap:
    ros__parameters:
      use_sim_time: true
      plugins: ["obstacle_layer", "inflation_layer"]
      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleCostmapPlugin"
        observation_sources: "scan"        # or "pointcloud" if PointCloud2
        scan:
          topic: "/scan"                   # auto-detected
          sensor_model: "ray"

global_costmap:
  global_costmap:
    ros__parameters:
      use_sim_time: true
      plugins: ["static_layer", "obstacle_layer", "inflation_layer"]
```

4. Launch via `ros2 launch nav2_bringup bringup_launch.py map:=<map> params_file:=<params> use_sim_time:=True`
5. Return PID + params path

**Missing prerequisite handling:**
```
User: launch nav2
→ check_scene_ready finds: no /scan topic, no map
→ "Can't launch Nav2 yet. Missing:
   1. No LiDAR topic detected — add a LiDAR sensor: 'add a Velodyne VLP-16 to the robot'
   2. No map available — either start SLAM first: 'launch slam' or provide a map file
   
   Suggested path: Add LiDAR → Start SLAM → Drive around → Save map → Launch Nav2"
```

### B3 — `launch_slam` (MCP + chat tool)

**Input:** `algorithm` (slam_toolbox | rtabmap | isaac_slam), `sensor_source` (lidar | stereo_camera | rgbd_camera)

**Behavior:**
1. Detect available sensor topics and choose SLAM algorithm:

| Sensor available | Recommended SLAM | Why |
|-----------------|------------------|-----|
| 2D LiDAR (`/scan`) | `slam_toolbox` | Lightweight, real-time, works great with LaserScan |
| 3D LiDAR (`/points`) | `rtabmap` with lidar mode | Handles 3D point clouds natively |
| Stereo camera (`/camera/left` + `/camera/right`) | `rtabmap` stereo mode | Visual SLAM from stereo pair |
| RGB-D camera (`/camera/rgb` + `/camera/depth`) | `rtabmap` rgbd mode | Visual SLAM from depth camera |
| RGB only | `rtabmap` mono mode (limited) | Monocular SLAM — scale ambiguity warning |

2. Auto-generate launch params based on sensor config
3. Launch appropriate SLAM node
4. Monitor `/map` topic to confirm mapping started

**SLAM Toolbox launch (2D LiDAR):**
```bash
ros2 launch slam_toolbox online_async_launch.py \
  slam_params_file:=<generated_params> \
  use_sim_time:=True
```

**rtabmap launch (stereo / RGB-D):**
```bash
ros2 launch rtabmap_launch rtabmap.launch.py \
  rgb_topic:=/camera/rgb \
  depth_topic:=/camera/depth \
  camera_info_topic:=/camera/camera_info \
  frame_id:=base_link \
  use_sim_time:=true \
  approx_sync:=true
```

**Machine-aware selection:**
```
User: launch slam
→ get_machine_specs: 16GB RAM, RTX 4090, aarch64
→ check_scene_ready: /camera/rgb (30Hz), /camera/depth (30Hz), /odom, no /scan
→ "Detected RGB-D camera on /camera/rgb + /camera/depth. 
   Launching rtabmap in RGB-D mode (your GPU has plenty of VRAM).
   
   Drive the robot around to build the map. When done, type 'save the map' to export.
   
   Tip: You can also view the map building in RViz2: 'launch rviz2 with map and camera'"
```

### B4 — `launch_ros2_control` (MCP + chat tool)

**Input:** `robot_path`, `controller_type` (diff_drive | joint_trajectory | effort | velocity)

**Behavior:**
1. Inspect robot articulation to determine controller type
2. Generate `ros2_control` URDF tags and controller YAML
3. For Isaac Sim: use the `topic_based_ros2_control` plugin (bridges Isaac Sim joint commands via ROS2 topics instead of hardware interface)

**Controller types:**

| Robot type | Controller | Topics |
|------------|-----------|--------|
| Wheeled (Nova Carter, Jetbot) | `diff_drive_controller/DiffDriveController` | cmd: `/cmd_vel`, state: `/odom` |
| Arm (Franka, UR10) | `joint_trajectory_controller/JointTrajectoryController` | cmd: `/joint_trajectory_controller/joint_trajectory`, state: `/joint_states` |
| Humanoid (G1) | `effort_controllers/JointGroupEffortController` | cmd: `/effort_controller/commands`, state: `/joint_states` |

**Generated controller config:**
```yaml
controller_manager:
  ros__parameters:
    update_rate: 100
    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster
    diff_drive_controller:
      type: diff_drive_controller/DiffDriveController

diff_drive_controller:
  ros__parameters:
    left_wheel_names: ["joint_wheel_left"]
    right_wheel_names: ["joint_wheel_right"]
    wheel_separation: 0.4132
    wheel_radius: 0.14
    publish_rate: 50.0
    odom_frame_id: "odom"
    base_frame_id: "base_link"
    use_stamped_vel: false
```

### B5 — `launch_gazebo` (MCP + chat tool)

**Input:** `world_file` (optional), `bridge_topics` (list of topics to bridge between Isaac Sim and Gazebo)

**Behavior:**
1. Export current scene as SDF/URDF for Gazebo compatibility
2. Configure `ros_gz_bridge` for topic bridging between Isaac Sim and Gazebo
3. Launch Gazebo with the exported world

**Use cases:**
- Co-simulation: Run physics in Isaac Sim, visualization in Gazebo
- Validation: Compare Isaac Sim behavior with Gazebo
- Interop: User has existing Gazebo plugins/worlds they want to test alongside Isaac Sim

### B6 — Process Manager

All launch tools register their subprocesses in a `_LAUNCHED_PROCESSES` registry:

```python
_LAUNCHED_PROCESSES: Dict[str, LaunchedProcess] = {}

@dataclass
class LaunchedProcess:
    name: str           # "rviz2", "nav2", "slam_toolbox", etc.
    pid: int
    config_path: str    # Generated config file path
    launch_time: float
    topics_used: List[str]
```

**Additional tools:**
- `list_launched` — show all running processes launched by Isaac Assist
- `stop_launched(name)` — gracefully stop a launched process
- `restart_launched(name)` — restart with same config (useful after scene changes)

---

## Phase C — Isaac Sim Sensor Coverage (Week 2–3)

**Goal:** Ensure every Isaac Sim sensor type has a complete OmniGraph → ROS2 topic pipeline and is auto-detected by the readiness checker.

### C1 — Sensor-to-ROS2 Topic Matrix

| Sensor | Isaac Sim Extension | OmniGraph Node | ROS2 Topic | Message Type |
|--------|-------------------|----------------|------------|-------------|
| **RGB Camera** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=rgb) | `/camera/rgb` | `sensor_msgs/Image` |
| **Depth Camera** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=depth) | `/camera/depth` | `sensor_msgs/Image` |
| **Camera Info** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=camera_info) via writer | `/camera/camera_info` | `sensor_msgs/CameraInfo` |
| **Semantic Segmentation** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=semantic_segmentation) | `/camera/semantic` | `sensor_msgs/Image` |
| **Instance Segmentation** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=instance_segmentation) | `/camera/instance` | `sensor_msgs/Image` |
| **Bounding Box 2D (tight)** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=bbox_2d_tight) | `/camera/bbox_2d` | `vision_msgs/Detection2DArray` |
| **Bounding Box 2D (loose)** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=bbox_2d_loose) | `/camera/bbox_2d_loose` | `vision_msgs/Detection2DArray` |
| **Bounding Box 3D** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=bbox_3d) | `/camera/bbox_3d` | `vision_msgs/Detection3DArray` |
| **Depth PCL** | `isaacsim.ros2.bridge` | `ROS2CameraHelper` (type=depth_pcl) | `/camera/points` | `sensor_msgs/PointCloud2` |
| **2D LiDAR** | `isaacsim.sensors.rtx` | `ROS2PublishLaserScan` | `/scan` | `sensor_msgs/LaserScan` |
| **3D LiDAR** | `isaacsim.sensors.rtx` | `ROS2PublishPointCloud` | `/points` | `sensor_msgs/PointCloud2` |
| **IMU** | `isaacsim.sensors.physics` | `ROS2PublishImu` | `/imu/data` | `sensor_msgs/Imu` |
| **Contact Sensor** | `isaacsim.sensors.physics` | (custom via `IsaacReadContactSensor`) | `/contact` | `geometry_msgs/WrenchStamped` |
| **GPS** | `isaacsim.sensors.physics` | (custom) | `/gps/fix` | `sensor_msgs/NavSatFix` |
| **Odometry** | `isaacsim.core.nodes` | `IsaacComputeOdometry` + `ROS2PublishOdometry` | `/odom` | `nav_msgs/Odometry` |
| **Clock** | `isaacsim.ros2.bridge` | `ROS2PublishClock` | `/clock` | `rosgraph_msgs/Clock` |
| **Joint State** | `isaacsim.ros2.bridge` | `ROS2PublishJointState` | `/joint_states` | `sensor_msgs/JointState` |
| **TF** | `isaacsim.ros2.bridge` | `ROS2PublishTransformTree` | `/tf` | `tf2_msgs/TFMessage` |

### C2 — Pipeline Template Updates

Add sensor setup as a pipeline phase for each robot template. The pipeline planner already has Phase 4 for sensors — extend it with full sensor suites:

**Nova Carter sensor suite:**
- Front stereo cameras (2× RGB + 2× Depth) → `/front_left/rgb`, `/front_right/rgb`, `/front_left/depth`, `/front_right/depth`
- Rear stereo camera → `/rear/rgb`, `/rear/depth`
- 2D LiDAR → `/scan`
- IMU → `/imu/data`
- Odometry → `/odom`
- TF tree → `/tf`, `/tf_static`
- Clock → `/clock`

**Franka sensor suite:**
- Wrist camera (RGB + Depth) → `/wrist_camera/rgb`, `/wrist_camera/depth`
- Joint states → `/joint_states`
- Contact sensors on gripper fingers → `/gripper/left_finger/contact`, `/gripper/right_finger/contact`

### C3 — `add_full_sensor_suite` (MCP + chat tool)

One-command sensor setup that adds all appropriate sensors for a robot type:

```
User: add a full sensor suite to the Nova Carter
→ Detects: Nova Carter (differential drive)
→ Adds: stereo cameras, LiDAR, IMU, odom, TF, clock
→ Wires: all OmniGraph nodes with IsaacCreateRenderProduct for cameras
→ "Added 8 sensor streams to Nova Carter:
   - Front stereo cameras (RGB+Depth, 1280×720, 30Hz)
   - 2D LiDAR (360°, 10m range, 10Hz)
   - IMU (100Hz)
   - Odometry, TF, Clock
   
   Next: Play the simulation, then 'launch rviz2' to visualize everything"
```

---

## Phase D — Navigation & Mapping (Week 3–4)

### D1 — `slam_start` / `slam_stop` / `slam_status`

**`slam_start`** — wrapper around `launch_slam` that also:
- Verifies sensor health before starting
- Monitors mapping progress (tracks `/map` topic updates)
- Reports coverage area estimate periodically

**`slam_stop`** — saves the map and stops SLAM:
```
User: stop mapping and save the map
→ Calls slam_stop()
→ Calls ros2 service: /slam_toolbox/save_map (or /rtabmap/save_map)
→ Copies map to workspace/maps/<scene_name>/
→ "Map saved. Coverage: ~45 m², 2847 cells occupied.
   Files: workspace/maps/carter_home/map.pgm + map.yaml
   
   Next: 'launch nav2 with the saved map' to start autonomous navigation"
```

### D2 — `map_export` (MCP + chat tool)

Export the current map (from SLAM or occupancy map generator) in multiple formats:

| Format | Files | Use |
|--------|-------|-----|
| Nav2 standard | `map.pgm` + `map.yaml` | Nav2 `map_server` |
| PNG image | `map.png` | Visual inspection, documentation |
| ROS2 bag | `map_bag/` | Replay and re-process later |
| Isaac Sim occupancy | `omap.usd` | Re-import into Isaac Sim |

### D3 — `nav2_goto` (MCP + chat tool)

Send navigation goals to Nav2.

**Input modes:**
- **Coordinates:** `nav2_goto(x=3.0, y=2.0, theta=1.57)` — direct pose goal
- **Named location:** `nav2_goto(location="kitchen")` — looks up saved locations
- **Relative:** `nav2_goto(forward=2.0, left=1.0)` — relative to current pose
- **Vision-guided:** `nav2_goto(description="the blue ball")` — uses Gemini to find the object, plans path to it

**Named locations registry:**
```json
{
  "kitchen": {"x": 3.0, "y": 2.0, "theta": 0.0},
  "living_room": {"x": -1.0, "y": 4.0, "theta": 1.57},
  "charging_station": {"x": 0.0, "y": 0.0, "theta": 0.0}
}
```

Users save locations: `"save this location as kitchen"` → records current `/odom` pose

**Nav2 goal publishing:**
```python
# Publish to /goal_pose (Nav2 action)
goal = {
    "header": {"frame_id": "map"},
    "pose": {
        "position": {"x": x, "y": y, "z": 0.0},
        "orientation": quaternion_from_yaw(theta)
    }
}
await handle_ros2_publish({"topic": "/goal_pose", "type": "geometry_msgs/msg/PoseStamped", "data": goal})
```

**Monitoring:** After sending goal, poll `/nav2/status` or `/navigate_to_pose/_action/status` to report progress:
```
"Navigating to kitchen (3.0, 2.0)... 
 Distance remaining: 2.1m
 ETA: ~8 seconds
 ✅ Arrived at kitchen"
```

### D4 — `nav2_waypoints` (MCP + chat tool)

Follow a sequence of waypoints:
```
User: patrol the kitchen, then the living room, then back to the charging station
→ Resolves named locations
→ Publishes waypoint sequence to /follow_waypoints
→ Monitors progress through each waypoint
```

### D5 — Vision-Language Navigation

Combines Gemini Robotics-ER with Nav2:

```
User: move toward the blue ball
→ 1. capture_viewport() → base64 image
→ 2. vision_detect_objects("blue ball") → [{"point": [450, 600], "label": "blue ball"}]
→ 3. Convert 2D image coords → 3D world coords (using depth image + camera intrinsics)
→ 4. nav2_goto(x=world_x, y=world_y) → Nav2 goal
→ "Found the blue ball at approximately (2.3, 1.1) in the map. Navigating there now..."
```

**Pipeline for 2D→3D projection:**
1. Get pixel coordinates from Gemini vision
2. Subscribe to depth image at same pixel → get depth value
3. Use camera intrinsics (from `/camera/camera_info`) to back-project:
   ```
   X = (u - cx) * depth / fx
   Y = (v - cy) * depth / fy
   Z = depth
   ```
4. Transform from camera frame to map frame using TF
5. Send as Nav2 goal

---

## Phase E — Perception & Classification (Week 4–5)

### E1 — `classify_objects` (MCP + chat tool)

Object classification from the robot's camera perspective.

**Methods:**

| Method | Source | Strengths |
|--------|--------|-----------|
| **Gemini Robotics-ER** | Viewport screenshot → Gemini API | Zero-shot, any object, spatial reasoning |
| **Isaac Sim Semantic Segmentation** | OmniGraph `ROS2CameraHelper` (type=semantic_segmentation) | Ground truth labels from USD semantic tags |
| **Isaac Sim Instance Segmentation** | OmniGraph `ROS2CameraHelper` (type=instance_segmentation) | Per-object masks with unique IDs |
| **Isaac Sim BBox 2D/3D** | OmniGraph `ROS2CameraHelper` (type=bbox_2d_tight/bbox_3d) | Precise bounding boxes from renderer |

**Chat usage:**
```
User: what objects can the robot see?
→ 1. Capture from robot's camera (or subscribe to /camera/rgb once)
→ 2. Send to Gemini: "List all objects visible in this image with their approximate positions"
→ 3. Also query Isaac Sim segmentation if available: ground-truth labels
→ "From the robot's camera I can see:
   - Red cube (TargetBox) — ~1.5m ahead, slightly to the right
   - Blue cylinder (Obstacle) — ~2m ahead, to the left
   - Table — ~0.8m ahead
   - Wall — background
   
   Isaac Sim ground truth confirms: 4 objects in view with matching labels"
```

### E2 — `get_segmentation_map` (MCP + chat tool)

Get semantic or instance segmentation from Isaac Sim's renderer.

**Input:** `camera_path`, `segmentation_type` (semantic | instance | panoptic)

**Behavior:**
1. Ensure camera has a `ROS2CameraHelper` node with `type=semantic_segmentation` or `instance_segmentation`
2. If not wired, auto-add the OmniGraph node
3. Subscribe to segmentation topic once → return the image + label map

**Return:**
```json
{
  "segmentation_image_b64": "<base64 PNG with colored segments>",
  "labels": {
    "0": {"class": "background", "color": [0, 0, 0]},
    "1": {"class": "floor", "color": [128, 128, 128]},
    "2": {"class": "table", "color": [139, 69, 19]},
    "3": {"class": "robot", "color": [0, 255, 0]},
    "4": {"class": "cube", "color": [255, 0, 0]}
  },
  "instance_count": 5
}
```

**Labeling:** USD prims must have semantic labels applied. Isaac Assist can auto-label:
```
User: label all objects in the scene for segmentation
→ Walks stage tree, applies SemanticLabel API based on prim names/types
→ "Applied semantic labels to 12 prims:
   - floor (1), walls (4), table (1), chairs (2), robot (1), cubes (3)"
```

### E3 — `vision_command` (MCP + chat tool)

Natural language vision-to-action commands that combine perception with robot control.

**Supported patterns:**

| Command pattern | Action pipeline |
|----------------|-----------------|
| "move toward the [object]" | detect → 3D locate → nav2_goto |
| "pick up the [object]" | detect → 3D locate → move_to_pose → grasp |
| "what color is the [object]?" | detect → Gemini analyze → text response |
| "how many [objects] are there?" | detect → count → text response |
| "is the [object] on the table?" | detect both → spatial reasoning → text response |
| "go to the nearest [object]" | detect all → compute distances → nav2_goto nearest |
| "avoid the [object]" | detect → add to Nav2 costmap as obstacle |
| "follow the [object]" | detect continuously → publish goals → tracking loop |

### E4 — Gemini Scene VQA from Robot's Perspective

Extend the existing `vision_analyze_scene` to work from the robot's onboard camera (not just the viewport):

```
User: from the robot's perspective, describe what you see
→ 1. Subscribe to /camera/rgb once → get image
→ 2. Send to Gemini: "You are looking through a robot's onboard camera. Describe what you see,
      including objects, their approximate distances, and spatial relationships."
→ "From the robot's camera (mounted at chest height, facing forward):
   - Directly ahead (~1.5m): a wooden table with a red cube on top
   - To the right (~2m): a blue cylinder on the floor
   - Background: white wall with a doorway to the left
   - Floor: gray concrete
   - The path to the right of the table appears clear for navigation"
```

---

## Phase F — Export & Project Integration (Week 5–6)

### F1 — `export_project_zip` (MCP + chat tool)

Export everything as a downloadable ZIP file.

**Contents:**

```
carter_navigation_demo.zip
├── README.md                      # Setup instructions, topic list, architecture
├── scene_setup.py                 # All Isaac Sim patches as runnable script
├── config/
│   ├── nav2_params.yaml           # Auto-generated Nav2 config
│   ├── slam_params.yaml           # SLAM config used during mapping
│   ├── controller_params.yaml     # ros2_control config
│   └── rviz_config.rviz           # RViz2 display config
├── launch/
│   ├── isaac_sim_bringup.launch.py   # Launch Isaac Sim + OmniGraph setup
│   ├── navigation.launch.py          # Nav2 + SLAM + ros2_control
│   ├── visualization.launch.py       # RViz2 with config
│   └── full_demo.launch.py           # Everything at once
├── maps/
│   ├── map.pgm                    # Saved occupancy map
│   └── map.yaml                   # Map metadata
├── urdf/
│   └── robot.urdf                 # Auto-exported from Isaac Sim articulation
├── models/
│   └── (any custom USD/URDF files)
├── scripts/
│   ├── teleop.py                  # Keyboard teleoperation script
│   └── patrol.py                  # Waypoint patrol example
├── requirements.txt               # Python dependencies
├── package.xml                    # ROS2 package manifest
└── CMakeLists.txt                 # ROS2 build file (ament_cmake)
```

**Download endpoint:**
```
POST /api/v1/chat/export_project_zip
→ Returns: {"zip_path": "workspace/exports/carter_navigation_demo.zip", "size_mb": 12.3}

GET /api/v1/chat/export_project_zip/download?path=<zip_path>
→ Returns: ZIP file stream
```

### F2 — `scaffold_ros2_workspace` (MCP + chat tool)

Create a full ROS2 workspace that users can `colcon build`:

```
User: create a ROS2 workspace for this project
→ Creates ~/ros2_ws/src/carter_navigation_demo/
→ Writes package.xml, CMakeLists.txt, launch files, configs
→ "Created ROS2 workspace at ~/ros2_ws/src/carter_navigation_demo/
   
   To build and use:
   cd ~/ros2_ws
   colcon build --packages-select carter_navigation_demo
   source install/setup.bash
   ros2 launch carter_navigation_demo full_demo.launch.py"
```

### F3 — `connect_user_model` (MCP + chat tool)

Let users connect their own robot model (URDF/USD/MJCF) from their own project folder.

**Input:** `model_path` (absolute or relative path to user's model), `model_format` (auto-detect from extension)

**Behavior:**
1. Validate the model file exists and is parseable
2. Auto-detect format: `.urdf` → URDF, `.usd/.usda/.usdc` → USD, `.xml` (with MuJoCo header) → MJCF
3. Import into Isaac Sim via `import_robot`
4. Auto-detect: joints, drive types, sensor mounts, collision meshes
5. Generate a scene readiness report specific to this robot
6. Suggest next steps based on robot capabilities

```
User: connect my robot from /home/kimate/my_project/robots/my_custom_robot.urdf
→ Validates URDF: 12 links, 11 joints, differential drive base
→ Imports into Isaac Sim at /World/MyCustomRobot
→ "Imported your robot from /home/kimate/my_project/robots/my_custom_robot.urdf
   
   Detected capabilities:
   - Differential drive (2 wheel joints: left_wheel, right_wheel)
   - 1 camera mount (camera_link)
   - 1 LiDAR mount (lidar_link)
   - 11 joints total (2 continuous, 9 fixed)
   
   Suggested next steps:
   1. Wire differential drive: 'create a diff drive OmniGraph for /World/MyCustomRobot'
   2. Add sensors: 'add a RealSense D435i to camera_link'
   3. Add LiDAR: 'add a Velodyne VLP-16 to lidar_link'
   4. Full setup: 'set up this robot with all sensors and ROS2 graphs'
   
   Your model will also be registered in the asset catalog for future use."
```

**Project folder integration:**
- Isaac Assist watches the user's project folder for changes to the model file
- If the URDF/USD is updated externally, prompt: "Your robot model was modified. Reload?"
- Generated configs reference the user's original model path (not a copy)

---

## Phase G — Extended Pipeline Templates (Week 6)

### G1 — Full Autonomy Pipeline

A new 8-phase pipeline template for complete autonomous robot setup:

```
User: pipeline: Nova Carter autonomous navigation in a warehouse
```

| Phase | Name | What it does |
|-------|------|-------------|
| 1 | Scene Setup | Ground plane + warehouse environment (shelves, walls, loading dock) |
| 2 | Robot Import | Nova Carter with physics, no fixedBase |
| 3 | Drive Graph | Differential drive OmniGraph with odom + clock publishers |
| 4 | Full Sensor Suite | Stereo cameras + LiDAR + IMU + TF + all ROS2 publishers |
| 5 | Verify ROS2 | Check all topics publishing, sensor health green |
| 6 | Launch SLAM | Start slam_toolbox, drive robot around for mapping |
| 7 | Launch Nav2 | Save map, start Nav2 with generated config |
| 8 | Final Verify | Scene summary + topic list + suggest next steps |

### G2 — Scene Readiness Gate

Between each pipeline phase, run `check_scene_ready` and only proceed if prerequisites are met. If a gate fails, provide actionable fix suggestions before retrying.

---

## New Tool Summary

| Tool | Type | Category | Phase |
|------|------|----------|-------|
| `check_scene_ready` | data | Readiness | A |
| `get_machine_specs` | data | Readiness | A |
| `suggest_next_steps` | data | Readiness | A |
| `check_sensor_health` | data | Readiness | A |
| `launch_rviz2` | action | Launch | B |
| `launch_nav2` | action | Launch | B |
| `launch_slam` | action | Launch | B |
| `launch_ros2_control` | action | Launch | B |
| `launch_gazebo` | action | Launch | B |
| `list_launched` | data | Launch | B |
| `stop_launched` | action | Launch | B |
| `add_full_sensor_suite` | code_gen | Sensors | C |
| `slam_start` | action | Mapping | D |
| `slam_stop` | action | Mapping | D |
| `map_export` | data | Mapping | D |
| `nav2_goto` | action | Navigation | D |
| `nav2_waypoints` | action | Navigation | D |
| `save_location` | data | Navigation | D |
| `classify_objects` | data | Perception | E |
| `get_segmentation_map` | data | Perception | E |
| `vision_command` | action | Perception | E |
| `label_scene_for_segmentation` | code_gen | Perception | E |
| `export_project_zip` | data | Export | F |
| `scaffold_ros2_workspace` | data | Export | F |
| `connect_user_model` | code_gen | Integration | F |

**Total: 25 new tools** (on top of existing 40+)

---

## Implementation Priority

| Priority | Tools | Rationale |
|----------|-------|-----------|
| **P0 — Do first** | `check_scene_ready`, `suggest_next_steps`, `get_machine_specs`, `check_sensor_health` | Everything else depends on readiness checking |
| **P0 — Do first** | `launch_rviz2` | Most immediately useful — users want to see their data |
| **P1 — Core loop** | `launch_slam`, `slam_start/stop`, `map_export` | Mapping is prerequisite for navigation |
| **P1 — Core loop** | `launch_nav2`, `nav2_goto` | The main autonomy use case |
| **P1 — Core loop** | `add_full_sensor_suite` | Needed for SLAM + Nav2 to work |
| **P1 — Core loop** | `export_project_zip` | Users want to take their work with them |
| **P2 — Extend** | `classify_objects`, `get_segmentation_map`, `vision_command` | Perception features |
| **P2 — Extend** | `launch_ros2_control`, `nav2_waypoints`, `save_location` | Advanced navigation |
| **P2 — Extend** | `connect_user_model`, `scaffold_ros2_workspace` | User project integration |
| **P3 — Nice to have** | `launch_gazebo`, `label_scene_for_segmentation` | Co-sim and annotation |

---

## Dependencies & Prerequisites

### ROS2 Packages Required

```bash
# Core (likely already installed with ROS2 Jazzy)
sudo apt install ros-${ROS_DISTRO}-rviz2

# Navigation
sudo apt install ros-${ROS_DISTRO}-navigation2 ros-${ROS_DISTRO}-nav2-bringup

# SLAM
sudo apt install ros-${ROS_DISTRO}-slam-toolbox
sudo apt install ros-${ROS_DISTRO}-rtabmap-ros  # For stereo/RGB-D SLAM

# Control
sudo apt install ros-${ROS_DISTRO}-ros2-control ros-${ROS_DISTRO}-ros2-controllers

# Gazebo (optional)
sudo apt install ros-${ROS_DISTRO}-ros-gz

# Vision (for detection message types)
sudo apt install ros-${ROS_DISTRO}-vision-msgs
```

### Python Dependencies (service)

```
ros-mcp>=0.2.0          # Already installed — rosbridge WebSocket client
psutil                   # Process management for launched tools
pyyaml                   # Config file generation
```

### Isaac Sim Extensions Required

```
isaacsim.ros2.bridge              # ROS2 OmniGraph nodes
isaacsim.sensors.rtx              # RTX LiDAR, radar
isaacsim.sensors.physics          # IMU, contact, GPS
isaacsim.core.nodes               # ComputeOdometry, CreateRenderProduct, ReadSimTime
isaacsim.ros2.tf_viewer           # TF tree visualization (optional)
isaacsim.asset.gen.omap           # Occupancy map generation (optional)
```

---

## Test Scenarios (to add to TEST_SCENARIOS_UI.md)

### T62 — Scene Readiness Check
```
User: is the scene ready for navigation?
→ check_scene_ready runs all checks
→ Returns readiness report with score and suggestions
```

### T63 — Machine Specs + Suggestions
```
User: what should I do next?
→ Checks machine, scene, ROS2 state
→ "Your DGX Spark has 128GB RAM and an RTX 6000 — plenty for all sensors.
   Currently missing: LiDAR sensor. Add one to enable SLAM and Nav2.
   Type: 'add a Velodyne VLP-16 to the robot' to proceed."
```

### T64 — Launch RViz2
```
User: launch rviz2 with all available sensors
→ Auto-discovers topics, generates config, launches
```

### T65 — SLAM Mapping Session
```
User: start mapping this room
→ Detects sensor type, launches SLAM
→ User drives robot around
User: save the map
→ Exports map files
```

### T66 — Nav2 Autonomous Navigation
```
User: launch navigation with the saved map
→ Launches Nav2 stack
User: go to position 3, 2
→ Sends Nav2 goal
User: go to the kitchen
→ Looks up saved location, sends goal
```

### T67 — Vision-Language Navigation
```
User: move toward the blue ball
→ Gemini detects blue ball → 3D projection → Nav2 goal
```

### T68 — Segmentation Map
```
User: show me the segmentation map from the robot's camera
→ Returns semantic segmentation image + labels
```

### T69 — Export ZIP
```
User: export everything as a zip
→ Generates complete ROS2 project package
```

### T70 — Connect User Model
```
User: load my robot from ~/my_project/robot.urdf
→ Imports, analyzes, suggests next steps
```

### T71 — Full Autonomy Pipeline
```
User: pipeline: Nova Carter autonomous navigation in a warehouse
→ 8-phase pipeline: scene → robot → drive → sensors → verify → SLAM → Nav2 → done
```

### T72 — Sensor Health Check
```
User: check all sensor health
→ Inspects each topic's Hz, data quality, issues
→ "LiDAR healthy (10Hz), Camera RGB healthy (30Hz), IMU: not publishing — wire it"
```
