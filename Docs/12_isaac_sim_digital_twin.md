# 12 — Isaac Sim Digital Twin (Inference Path with ROS2)

The deployment-time integration. Isaac Sim runs your `tenthings_v1_open_arm_bimanual` USD scene as a digital twin; the manipulation stack from modules `01–10` connects to it via the `isaacsim.ros2.bridge` extension over ROS2. From the manipulation stack's perspective, there should be **zero code differences** between sim and real — only topic remaps.

This is what unlocks pre-deployment validation: same Continuity Manager, same Pi0.5 service, same Policy Bank, same predicate evaluator, swapped only at the ROS2 layer.

## Topology

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Isaac Sim 5.x                                  │
│  USD scene: tenthings_v1_open_arm_bimanual + workspace + objects     │
│  ─────────────────────────────────────────────────────────────────   │
│  OmniGraph (Action Graph) running on physics tick:                   │
│   • ROS2 Publish JointState     ←  /sim/arm_left/joint_states        │
│   • ROS2 Publish JointState     ←  /sim/arm_right/joint_states       │
│   • ROS2 Publish CameraInfo+RGB ←  /sim/camera_scene/rgb             │
│   • ROS2 Publish PointCloud2    ←  /sim/camera_scene/depth           │
│   • ROS2 Publish Image          ←  /sim/camera_wrist_left/rgb        │
│   • ROS2 Publish Image          ←  /sim/camera_wrist_right/rgb       │
│   • ROS2 Publish WrenchStamped  ←  /sim/arm_left/ft                  │
│   • ROS2 Publish WrenchStamped  ←  /sim/arm_right/ft                 │
│   • ROS2 Publish Clock          ←  /clock                            │
│   • ROS2 Subscribe JointCommand →  /sim/arm_left/joint_command       │
│   • ROS2 Subscribe JointCommand →  /sim/arm_right/joint_command      │
│   • ROS2 Subscribe Bool         →  /sim/base_stationary  (latch)     │
└──────────────────────────────────────────────────────────────────────┘
                              ▲ │
                       ROS2   │ │   ROS2
                              │ ▼
┌──────────────────────────────────────────────────────────────────────┐
│            Manipulation Stack (modules 01–10, unchanged)             │
│  Observation Pipeline   ──▶  Continuity Manager  ──▶  Action Arb     │
│         ▲   ▲                       │                    │           │
│         │   │                       ▼                    ▼           │
│      cams  proprio              Pi0.5 + RL          joint cmds       │
└──────────────────────────────────────────────────────────────────────┘
```

The stack subscribes to the `/sim/...` topics in sim and `/...` (no prefix) topics on real. A topic remap launch file is the only difference between the two configurations.

## Version Targets

| Component | Version |
|---|---|
| Isaac Sim | 5.x (5.0+) |
| Isaac Lab | 2.x compatible with the Isaac Sim above |
| ROS 2 | Jazzy (Ubuntu 24.04) |
| Python (Isaac Sim internal) | 3.11 |
| ROS 2 Bridge extension name | `isaacsim.ros2.bridge` (NOT the legacy `omni.isaac.ros2_bridge`) |
| RMW | `rmw_zenoh_cpp` (Isaac Sim 5 default) or `rmw_fastrtps_cpp` (set per Isaac Sim docs) |

If you build a custom ROS 2 workspace for use with Isaac Sim 5, **build it with Python 3.11**. Mixing 3.11 (Isaac Sim) with 3.12 (default Jazzy) breaks `rclpy` interop. The `IsaacSim-ros_workspaces` repo includes Dockerfiles that pin this correctly.

## Workspace Layout

```
~/workspaces/manipulation_ws/
├── src/
│   ├── manipulation_msgs/              # custom .msg/.srv definitions
│   ├── manipulation_stack/             # modules 06, 07 as ROS2 nodes
│   ├── manipulation_bringup/           # launch files (sim + real)
│   ├── manipulation_sim_assets/        # USD files for sim (linked, not copied)
│   └── tenthings_description/          # URDF/USD of the embodiment
├── fastdds.xml                         # only if using FastDDS instead of Zenoh
└── install/  build/  log/
```

Build with the Python 3.11 toolchain:

```bash
cd ~/workspaces/manipulation_ws
source /opt/ros/jazzy/setup.bash         # if using native ROS 2
rosdep install -i --from-path src --rosdistro jazzy -y
colcon build --symlink-install
source install/local_setup.bash
```

## Custom Message Definitions

```
# manipulation_msgs/msg/Observation.msg
builtin_interfaces/Time stamp
geometry_msgs/Pose tcp_pose_left
geometry_msgs/Pose tcp_pose_right
geometry_msgs/Twist tcp_vel_left
geometry_msgs/Twist tcp_vel_right
float32[] joint_positions_left
float32[] joint_positions_right
float32[] joint_velocities_left
float32[] joint_velocities_right
float32 gripper_width_left
float32 gripper_width_right
float32 gripper_force_left
float32 gripper_force_right
float32[6] ft_left
float32[6] ft_right
sensor_msgs/Image rgb_wrist_left
sensor_msgs/Image rgb_wrist_right
sensor_msgs/Image rgb_scene
sensor_msgs/Image depth_scene
manipulation_msgs/DetectedObject[] detected_objects
bool base_stationary
float32 telescope_height_m
```

```
# manipulation_msgs/msg/DetectedObject.msg
string object_id
string class_name
geometry_msgs/Pose pose
float32 confidence
float32[3] bbox_3d
```

```
# manipulation_msgs/msg/ArmAction.msg
string arm_id          # "left" | "right" | "both"
string action_type     # matches policy config.yaml action.type
float32[] action       # variable-length per action_type
```

```
# manipulation_msgs/msg/Mode.msg
uint8 SINGLE          = 0
uint8 ASSIST          = 1
uint8 SYNC_BIMANUAL   = 2
uint8 SAFE_HOLD       = 3
uint8 mode
string lead_arm       # "left" | "right" — required when mode != SAFE_HOLD
float32 max_force_n   # phase constraint, enforced by arbitration
```

```
# manipulation_msgs/srv/RunTask.srv
string goal_text
---
bool success
int32 completed_phases
string final_state
string reason
```

## Isaac Sim USD Scene Setup

Two viable paths to set up the OmniGraph that handles ROS2 publishing/subscribing:

### Path A — Action Graph in the GUI (one-time setup, persisted in USD)

For each publisher/subscriber, drop a node from the **Isaac Ros2** category, wire it on `On Physics Step`, and bind to the relevant prim. Save the scene; the graph is part of the USD.

Per the H1 RL ROS2 tutorial pattern: `On Physics Step` → reads (e.g., `Isaac Read IMU` / joint state / camera helper) → `ROS2 Publish *`. For subscribers: `ROS2 Subscribe JointState` → `Isaac Articulation Controller` writes back into the sim.

### Path B — Programmatic graph construction (preferred for reproducibility)

```python
# manipulation_sim_assets/scripts/setup_ros2_graph.py
"""
Run once after loading the tenthings_v1 USD in Isaac Sim 5.
Creates an Action Graph wiring sim → ROS2 topics under /sim/* prefix.
"""
import omni.graph.core as og
from isaacsim.core.utils.extensions import enable_extension

enable_extension("isaacsim.ros2.bridge")

GRAPH = "/World/ROS2Graph"
keys = og.Controller.Keys

og.Controller.edit(
    {"graph_path": GRAPH, "evaluator_name": "execution"},
    {
        keys.CREATE_NODES: [
            ("OnTick",            "omni.graph.action.OnPlaybackTick"),
            ("ReadSimTime",       "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("PubClock",          "isaacsim.ros2.bridge.ROS2PublishClock"),

            ("ReadJointsLeft",    "isaacsim.core.nodes.IsaacArticulationStateNode"),
            ("PubJointsLeft",     "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("ReadJointsRight",   "isaacsim.core.nodes.IsaacArticulationStateNode"),
            ("PubJointsRight",    "isaacsim.ros2.bridge.ROS2PublishJointState"),

            ("CamHelperScene",    "isaacsim.ros2.bridge.ROS2CameraHelper"),
            ("CamHelperWristL",   "isaacsim.ros2.bridge.ROS2CameraHelper"),
            ("CamHelperWristR",   "isaacsim.ros2.bridge.ROS2CameraHelper"),

            ("SubCmdLeft",        "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("ArtCtrlLeft",       "isaacsim.core.nodes.IsaacArticulationController"),
            ("SubCmdRight",       "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("ArtCtrlRight",      "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        keys.SET_VALUES: [
            ("PubClock.inputs:topicName",        "/clock"),

            ("ReadJointsLeft.inputs:targetPrim", "/World/Robot"),
            ("PubJointsLeft.inputs:topicName",   "/sim/arm_left/joint_states"),
            ("PubJointsLeft.inputs:nodeNamespace", ""),

            ("ReadJointsRight.inputs:targetPrim", "/World/Robot"),
            ("PubJointsRight.inputs:topicName",   "/sim/arm_right/joint_states"),

            ("CamHelperScene.inputs:topicName",  "/sim/camera_scene/rgb"),
            ("CamHelperScene.inputs:type",       "rgb"),
            ("CamHelperScene.inputs:renderProductPath", "/Render/Vars/scene_cam"),

            ("CamHelperWristL.inputs:topicName", "/sim/camera_wrist_left/rgb"),
            ("CamHelperWristL.inputs:type",     "rgb"),
            ("CamHelperWristL.inputs:renderProductPath", "/Render/Vars/wrist_cam_left"),

            ("CamHelperWristR.inputs:topicName", "/sim/camera_wrist_right/rgb"),
            ("CamHelperWristR.inputs:type",     "rgb"),
            ("CamHelperWristR.inputs:renderProductPath", "/Render/Vars/wrist_cam_right"),

            ("SubCmdLeft.inputs:topicName",  "/sim/arm_left/joint_command"),
            ("ArtCtrlLeft.inputs:targetPrim", "/World/Robot"),

            ("SubCmdRight.inputs:topicName", "/sim/arm_right/joint_command"),
            ("ArtCtrlRight.inputs:targetPrim", "/World/Robot"),
        ],
        keys.CONNECT: [
            ("OnTick.outputs:tick",              "PubClock.inputs:execIn"),
            ("ReadSimTime.outputs:simulationTime","PubClock.inputs:timeStamp"),

            ("OnTick.outputs:tick",              "ReadJointsLeft.inputs:execIn"),
            ("ReadJointsLeft.outputs:execOut",   "PubJointsLeft.inputs:execIn"),
            ("ReadJointsLeft.outputs:jointPositions",  "PubJointsLeft.inputs:positionCommand"),
            ("ReadSimTime.outputs:simulationTime","PubJointsLeft.inputs:timeStamp"),

            ("OnTick.outputs:tick",              "ReadJointsRight.inputs:execIn"),
            ("ReadJointsRight.outputs:execOut",  "PubJointsRight.inputs:execIn"),
            ("ReadJointsRight.outputs:jointPositions", "PubJointsRight.inputs:positionCommand"),

            ("OnTick.outputs:tick",              "CamHelperScene.inputs:execIn"),
            ("OnTick.outputs:tick",              "CamHelperWristL.inputs:execIn"),
            ("OnTick.outputs:tick",              "CamHelperWristR.inputs:execIn"),

            ("OnTick.outputs:tick",              "SubCmdLeft.inputs:execIn"),
            ("SubCmdLeft.outputs:execOut",       "ArtCtrlLeft.inputs:execIn"),
            ("SubCmdLeft.outputs:jointNames",    "ArtCtrlLeft.inputs:jointNames"),
            ("SubCmdLeft.outputs:positionCommand","ArtCtrlLeft.inputs:positionCommand"),

            ("OnTick.outputs:tick",              "SubCmdRight.inputs:execIn"),
            ("SubCmdRight.outputs:execOut",      "ArtCtrlRight.inputs:execIn"),
            ("SubCmdRight.outputs:jointNames",   "ArtCtrlRight.inputs:jointNames"),
            ("SubCmdRight.outputs:positionCommand","ArtCtrlRight.inputs:positionCommand"),
        ],
    },
)
```

For F/T sensors and contact, use `Isaac Read Effort Sensor` / contact sensor primitives published via `ROS2PublishWrench` (or a custom OmniGraph node — the bridge provides primitives for the common message types and you can extend with Python OmniGraph nodes for less-common ones).

## Topic Remapping for Sim vs Real

`manipulation_bringup/launch/manipulation_sim.launch.py`:

```python
from launch import LaunchDescription
from launch.actions import GroupAction, IncludeLaunchDescription
from launch_ros.actions import Node, PushRosNamespace
from launch.launch_description_sources import PythonLaunchDescriptionSource

def generate_launch_description():
    sim_remaps = [
        ("/arm_left/joint_states",      "/sim/arm_left/joint_states"),
        ("/arm_right/joint_states",     "/sim/arm_right/joint_states"),
        ("/arm_left/ft",                "/sim/arm_left/ft"),
        ("/arm_right/ft",               "/sim/arm_right/ft"),
        ("/camera_scene/rgb",           "/sim/camera_scene/rgb"),
        ("/camera_scene/depth",         "/sim/camera_scene/depth"),
        ("/camera_wrist_left/rgb",      "/sim/camera_wrist_left/rgb"),
        ("/camera_wrist_right/rgb",     "/sim/camera_wrist_right/rgb"),
        ("/arm_left/joint_command",     "/sim/arm_left/joint_command"),
        ("/arm_right/joint_command",    "/sim/arm_right/joint_command"),
        ("/base_stationary",            "/sim/base_stationary"),
    ]
    return LaunchDescription([
        Node(
            package="manipulation_stack",
            executable="observation_pipeline_node",
            name="observation_pipeline",
            parameters=[{"use_sim_time": True}],
            remappings=sim_remaps,
        ),
        Node(
            package="manipulation_stack",
            executable="action_arbitration_node",
            name="action_arbitration",
            parameters=[{"use_sim_time": True}],
            remappings=sim_remaps,
        ),
        Node(
            package="manipulation_stack",
            executable="continuity_manager_node",
            name="continuity_manager",
            parameters=[{
                "use_sim_time": True,
                "embodiment_id": "tenthings_v1_open_arm_bimanual",
                "pi05_url": "http://localhost:7100",
                "policy_bank_url": "http://localhost:7101",
            }],
        ),
    ])
```

`manipulation_real.launch.py` is the same file with `use_sim_time: False` and no remaps.

**This is the entire sim/real switching mechanism.** No `if sim:` branches in the manipulation code.

## Sim Time Discipline

Every node in the stack must declare `use_sim_time: True` when running against Isaac Sim, or timestamps will be off and `duration_exceeded` predicates will fire incorrectly. The Continuity Manager's `time.time()` calls in `10_reference_code.md` should be wrapped:

```python
# continuity_manager/clock.py
import rclpy
from rclpy.time import Time

class Clock:
    def __init__(self, node):
        self._node = node           # any rclpy.node.Node
    def now(self) -> float:
        return self._node.get_clock().now().nanoseconds / 1e9

# Replace direct time.time() in manager.py with self.clock.now()
```

When `use_sim_time` is true, this returns sim time published by Isaac Sim on `/clock`. When false, it returns wall-clock. Same code, both regimes.

## Bringup Sequence (sim mode)

```bash
# Terminal 1 — Pi0.5 service (host or container)
python -m pi05_service.server          # :7100

# Terminal 2 — Policy Bank
python -m policy_bank.server           # :7101

# Terminal 3 — Isaac Sim with the scene + Action Graph
./isaac-sim.selector.sh
# In Isaac Sim: open USD, run setup_ros2_graph.py once, save, press Play.
# (Or launch headless from terminal with the script as a startup arg.)

# Terminal 4 — manipulation stack against sim
source ~/workspaces/manipulation_ws/install/local_setup.bash
ros2 launch manipulation_bringup manipulation_sim.launch.py

# Terminal 5 — issue a goal
ros2 service call /run_task manipulation_msgs/srv/RunTask \
    "{goal_text: 'pick up the red mug and place it on the tray'}"
```

## Validation Suite (sim, before real)

| Test | What it shows |
|---|---|
| `clock_only` | `/clock` ticks, `use_sim_time` flows through |
| `obs_pub_smoke` | All sensor topics publish at expected rates; observation pipeline assembles `Observation.msg` |
| `single_skill_inference` | Pi0.5 → CM → policy → arbitration → joint cmds → arm moves; success predicate trips. One skill at a time. |
| `phase_handoff` | Multi-phase task crosses phase boundaries cleanly; idle arm holds retract during transitions. |
| `failure_escalation` | Force a failure (move target object mid-grasp); verify CM escalates to Pi0.5, replan succeeds. |
| `safe_hold_recovery` | Drop `/sim/base_stationary` mid-task; verify SAFE_HOLD engages, no commands published until re-latched. |
| `latency_profile` | Measure end-to-end obs→action latency; should match real-robot budget within 20 ms. |
| `cross_skill_chain` | Bimanual fold task: Pi0.5 emits ASSIST decomposition; both arm policies run concurrently. |

Each test is a pytest case in `tests/sim_integration/`, run against a scripted Isaac Sim scene loaded headless.

## Performance Notes

- Headless Isaac Sim with the bimanual robot, two wrist cams (84×84), one scene cam (640×480), and physics at 200 Hz hits ~50–80 FPS rendering on a single Ada-class GPU. That's enough for a 50 Hz control loop with margin.
- The bottleneck for ROS2 throughput is usually camera publishing. Drop scene camera to 5–10 Hz if needed; wrist cams must match policy control rate.
- For zero-copy GPU camera transfer, enable NITROS bridge:
  ```bash
  ./isaac-sim.sh --/exts/isaacsim.ros2.bridge/enable_nitros_bridge=true
  ```
  Useful when running Isaac ROS perception nodes against the sim camera streams.

## Sim Gotchas Specific to This Stack

1. **Joint name ordering** — Isaac Sim's `JointState` orders joints by USD traversal, which may not match the order your URDF or policy expects. Always remap explicitly in the Subscribe node, never rely on positional order.
2. **F/T sensors** — contact-derived F/T in Isaac Sim is noisier than real ATI sensors. If your policy was trained on clean F/T, add a low-pass filter in the Observation Pipeline that's parameterized by environment (sim vs real).
3. **Camera intrinsics drift** — Isaac Sim's pinhole camera intrinsics are derived from focal length and resolution. Confirm `camera_info` matches what your perception pipeline expects, or pose estimation breaks silently.
4. **Gripper contact** — sim gripper closures with rigid contact can spike F/T; tune contact compliance in the gripper actuator config to match real behavior.
5. **`/clock` race** — the manipulation stack must wait for `/clock` to start ticking before issuing the first goal. Add a 2 s settle in launch, or block on `Clock.now()` returning > 0 before starting the Continuity Manager loop.

## Crossing into Real

When promoting to real:
1. Swap launch file (`manipulation_real.launch.py`).
2. The Observation Pipeline now subscribes to real driver topics; the Action Arbitration publishes to real low-level controllers.
3. Pi0.5, RL Policy Bank, Continuity Manager — unchanged, same processes.
4. The first real run is single-skill, low-speed, with operator e-stop in hand.

If anything in the manipulation stack required code changes between sim and real, that's a design bug — file it against this module, not the production code.
