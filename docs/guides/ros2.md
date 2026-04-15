# ROS2 Integration

How to connect Isaac Sim to ROS2 for publishing, subscribing, and calling services.

---

## Prerequisites

ROS2 integration requires a running **rosbridge** WebSocket server. Isaac Assist communicates with ROS2 through rosbridge, not directly.

!!! warning "Rosbridge required"
    Start rosbridge before connecting:
    ```bash
    ros2 launch rosbridge_server rosbridge_websocket_launch.xml
    ```
    Default WebSocket URL: `ws://localhost:9090`

---

## Connecting to ROS2

Establish the connection from the chat panel.

> Connect to ROS2

> Connect to ROS2 at ws://localhost:9090

Isaac Assist calls `ros2_connect` and confirms the connection status. If the connection drops, it reconnects automatically on the next ROS2 command.

---

## Listing Topics, Services, and Nodes

Explore what is available on the ROS2 network.

=== "Topics"

    > List all ROS2 topics

    > What ROS2 topics are available?

    Returns topic names and their message types (e.g., `/cmd_vel` -- `geometry_msgs/msg/Twist`).

=== "Services"

    > List all ROS2 services

    > What services are available?

    Returns service names and types.

=== "Nodes"

    > List all ROS2 nodes

    > What nodes are running?

    Returns active node names.

---

## Subscribing to Topics

Listen to messages on a ROS2 topic.

> Subscribe to /odom

> Listen to /scan topic

> Show me messages from /joint_states

Isaac Assist subscribes and displays incoming messages in the chat. The subscription stays active until you unsubscribe or disconnect.

> Unsubscribe from /odom

> Stop listening to /scan

---

## Publishing Messages

Send messages to any ROS2 topic.

> Publish a Twist to /cmd_vel with linear x 0.5

> Send a message to /cmd_vel: linear x=1.0, angular z=0.3

> Publish to /joint_commands with position [0, -0.78, 0, -2.36, 0, 1.57, 0.78]

### Practical Example: Driving a Robot

A complete flow for commanding a mobile robot:

**Step 1:** Connect to ROS2.

> Connect to ROS2

**Step 2:** Check available topics.

> List ROS2 topics

**Step 3:** Publish velocity commands.

> Publish a Twist to /cmd_vel with linear x 0.5 and angular z 0.0

To stop:

> Publish a Twist to /cmd_vel with linear x 0 and angular z 0

---

## Calling Services

Invoke ROS2 services directly from the chat.

> Call service /reset_simulation

> Call /spawn_entity with name "box" and position [1, 0, 0.5]

---

## Common Message Types

| Message Type | Fields | Example Chat |
|-------------|--------|-------------|
| `geometry_msgs/Twist` | linear (x,y,z), angular (x,y,z) | `Publish Twist to /cmd_vel: linear x=0.5` |
| `std_msgs/String` | data | `Publish String "hello" to /chatter` |
| `std_msgs/Float64` | data | `Publish 3.14 to /set_speed` |
| `sensor_msgs/JointState` | name[], position[], velocity[] | Subscribe-only, typically |
| `geometry_msgs/PoseStamped` | position (x,y,z), orientation (x,y,z,w) | `Publish PoseStamped to /goal_pose` |

---

## ROS2 + Isaac Sim Sensors

Combine Isaac Sim sensors with ROS2 publishing:

> Attach a camera to /World/Franka/panda_hand and publish to /wrist_camera/image_raw

> Add a lidar to /World/Carter/base_link and publish to /scan

Isaac Assist follows ROS2 naming conventions: camera images to `*/image_raw`, lidar to `/scan`, IMU to `*/data`.

---

## Troubleshooting ROS2

| Problem | Fix |
|---------|-----|
| Cannot connect | Start rosbridge: `ros2 launch rosbridge_server rosbridge_websocket_launch.xml` |
| No topics listed | Start your ROS2 nodes, then list topics again |
| Messages not arriving | Double-check topic name and message type |
| Publishing has no effect | Verify the receiving node is running and subscribed |

---

## What's Next?

- [Importing Robots](importing-robots.md) -- Load robots to control via ROS2
- [Sensors & Cameras](sensors-and-cameras.md) -- Set up sensors that publish to ROS2
- [Debugging & Diagnostics](debugging.md) -- Diagnose connectivity issues
