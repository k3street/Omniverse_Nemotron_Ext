# OmniGraph + ROS2 Patterns

For event-driven canonicals + Kimate's ROS2-bridge-fidelity priority axis.

## OmniGraph 1-page summary

**Graph in Isaac Sim**: dataflow/execution graph stored as USD prim at e.g. `/World/ActionGraph`. Nodes typed (e.g. `omni.graph.action.OnPlaybackTick`), connected via named ports (`outputs:tick` → `inputs:execIn`). Built atomically via `og.Controller.edit()`.

**Action graph vs push graph**:
- Action (`evaluator_name="execution"`): event-driven; needs trigger node (OnPlaybackTick, OnTick, OnImpulseEvent). Fires only on execIn signal. All ROS2 publish/subscribe uses this.
- Push (`evaluator_name="push"`): re-evaluates every frame. For passive transforms. On-demand variant triggered via `og.Controller.evaluate_sync(graph)`.

**Common nodes**:

| Node | Purpose |
|---|---|
| `omni.graph.action.OnPlaybackTick` | Fires every sim step when timeline plays |
| `omni.graph.action.OnTick` | Fires during rendering, not physics |
| `isaacsim.core.nodes.IsaacReadSimulationTime` | Provides `outputs:simulationTime` for timestamps |
| `isaacsim.ros2.bridge.ROS2Context` | DDS context; required by every ROS2 node |
| `isaacsim.ros2.bridge.ROS2PublishJointState` | Publishes /joint_states |
| `isaacsim.ros2.bridge.ROS2SubscribeJointState` | Subscribes /joint_command |
| `isaacsim.ros2.bridge.ROS2SubscribeTwist` | Subscribes /cmd_vel |
| `isaacsim.ros2.bridge.ROS2PublishTransformTree` | Publishes /tf |
| `isaacsim.ros2.bridge.ROS2PublishClock` | Publishes /clock |
| `isaacsim.ros2.bridge.ROS2PublishLaserScan` | Publishes /scan |
| `isaacsim.ros2.bridge.ROS2PublishOdometry` | Publishes /odom |
| `isaacsim.ros2.bridge.ROS2PublishImu` | Publishes /imu/data |
| `isaacsim.ros2.bridge.ROS2CameraHelper` | Image + camera_info |
| `isaacsim.sensor.nodes.IsaacReadLidar` | Reads lidar into graph |
| `isaacsim.sensor.nodes.IsaacReadIMU` | Reads IMU |
| `isaacsim.core.nodes.IsaacArticulationController` | Applies joint commands |
| `isaacsim.robot.wheeled_robots.DifferentialController` | linear/angular velocity → wheels |

**Connection semantics**: `execIn`/`execOut` carry execution tokens (action graph flow); `inputs:*`/`outputs:*` carry typed values. Data wires evaluate lazily; exec wires sequence firing.

## ROS2 bridge core endpoints

| Topic | Direction | Key nodes |
|---|---|---|
| /joint_states | sim → ROS | OnPlaybackTick → ROS2PublishJointState + IsaacReadSimulationTime |
| /joint_command | ROS → sim | ROS2SubscribeJointState → IsaacArticulationController |
| /cmd_vel | ROS → sim | ROS2SubscribeTwist → DifferentialController → IsaacArticulationController |
| /clock | sim → ROS | OnPlaybackTick → ROS2PublishClock |
| /tf | sim → ROS | OnPlaybackTick → ROS2PublishTransformTree (targetPrims = robot root) |
| /scan | sim → ROS | IsaacReadLidar → ROS2PublishLaserScan; 32-beam RTX needs slice_elevation_deg=0.0 for Nav2 |
| /odom | sim → ROS | IsaacComputeOdometry → ROS2PublishOdometry |
| /imu/data | sim → ROS | IsaacReadIMU → ROS2PublishImu |
| /{ns}/image_raw | sim → ROS | ROS2CameraHelper |

Prerequisite: `extensions.enable_extension("isaacsim.ros2.bridge")`. Module path 5.x: `isaacsim.ros2.bridge.*` (deprecated: `omni.isaac.ros2_bridge`).

## Common patterns in canonicals

**Sensor-gated conveyor** (CP-03, F-02): proximity sensor read each step, OmniGraph branch → `surface_velocity`. F-02 reads OPC-UA tags at 1Hz to drive conveyor states.

**ROS2 command-driven pick** (D-12, S-11): `ROS2SubscribeTwist` execOut on message → `Break3Vector` → `DifferentialController` → `ArticulationController`. S-11 debug: `/cmd_vel` arrives but robot doesn't move = missing exec wire to ArticulationController, or graph on wrong stage layer.

**Camera → ROS image stream** (D-11, S-09): `OnPlaybackTick` → `ROS2CameraHelper` (rgb/depth) + separate `ROS2CameraInfoHelper`. Throttle via `IsaacSimulationGate` `step` attr.

## Existing tools

**OmniGraph**:
- `create_omnigraph` — free-form (nodes, connections, values)
- `create_graph` — template-based; 8 hardcoded ROS2 templates: ros2_clock, ros2_joint_state, ros2_camera, ros2_lidar, ros2_cmd_vel, ros2_tf, ros2_imu, ros2_odom
- `add_node`, `connect_nodes`, `inspect_graph`, `list_graphs`, `debug_graph`, `explain_graph`, `set_graph_variable`

**ROS2**:
- `setup_ros2_bridge(profile, robot_path)` — 4 profiles: ur10e_moveit2, jetbot_nav2, franka_moveit2, amr_full
- `configure_ros2_bridge(qos, domain_id)`, `configure_ros2_time`
- `setup_pick_place_ros2_bridge` — digital-twin HIL: publishes joint_states + cube poses, subscribes target pose + gripper
- `ros2_connect`, `ros2_list_topics`, `ros2_publish`, `ros2_subscribe_once`, `ros2_call_service`, etc.
- `diagnose_ros2`, `fix_ros2_qos`, `show_tf_tree`, `check_tf_health`

## New tools to add

1. **`wire_sensor_to_actuator(sensor_prim, actuator_prim, mapping)`** — single-call helper for sensor→actuator OmniGraph. Currently CP-03/F-02 require manual `add_node` + `connect_nodes` chain. Collapses 4-5 calls to 1.

2. **`setup_full_ros2_robot_iface(robot_path, namespace, domain_id)`** — single call building joint_states + tf + clock + odom + imu + camera profile at namespace. Plumbing exists in `_NAV2_BRIDGE_PROFILES`; add `amr_carter` or `franka_full` profile.

3. **`setup_ros2_bridge` schema enrichment** — current API takes profile enum. Accept either profile OR explicit topic list (`topics_to_publish` / `topics_to_subscribe`).

## ROS2-gated canonicals in 33-set + library

15 of 193 templates explicitly reference ROS2. Strictly ROS2-gated (cannot complete without live bridge):

1. D-05 — LIDAR → /scan at 10Hz, frame_id for Nav2
2. D-09 — Full topic audit table with measured rates
3. D-11 — IMU → /imu/data at 200Hz, BMI088 noise model
4. D-12 — /cmd_vel → DifferentialController OmniGraph (Carter)
5. D-14 — LIDAR → ROS2PublishLaserScan OmniGraph
6. S-01 — TF tree disconnect diagnosis + sim-clock propagation
7. S-02 — Nova Carter + full bridge bringup
8. S-07 — 8 Carters with per-robot namespaces, >20 fps
9. S-09 — Stereo intrinsics match + ROS topic verify
10. S-10 — rosbag recording of autonomous Carter run
11. S-11 — /cmd_vel arrives, robot doesn't move (graph debug)
12. S-12 — 4.x → 5.x ROS2 namespace migration
13. E-07 — On-robot Jetson deploy (Isaac Sim viz-only ROS2 peer)
14. F-02 — OPC-UA → conveyor (bridge pattern, not pure ROS2)

13 strictly + 1 OPC-UA = 14, slightly above 5-8 estimate.

Source: Sonnet agent `ab39907f30e681d1c` 2026-05-07.
