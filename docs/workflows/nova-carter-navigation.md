# Nova Carter Navigation Workflow

This workflow sets up a Nova Carter mobile robot navigating through an obstacle course, driven by ROS2 velocity commands through an OmniGraph differential drive controller.

---

## Step 1: Import the Nova Carter

```
Import a Nova Carter robot
```

Isaac Assist loads the Nova Carter from the asset library. Unlike the Franka, the Nova Carter is a mobile robot -- do **not** anchor it.

## Step 2: Create a Ground Plane

```
Create a ground plane at 0, 0, 0
```

The ground plane provides the driving surface and collision geometry.

## Step 3: Position the Robot

```
Teleport /World/Nova_Carter to position 0, 0, 0.05
```

Raise the robot slightly above the ground so the wheels start in contact rather than intersecting.

## Step 4: Build an Obstacle Course

```
Create a cube at 3, 1, 0.5 with scale 0.5, 0.5, 1.0 and name it Wall1
```

```
Create a cube at 3, -1, 0.5 with scale 0.5, 0.5, 1.0 and name it Wall2
```

```
Create a cylinder at 5, 0, 0.3 with scale 0.3, 0.3, 0.6 and name it Pillar1
```

```
Create a cube at 7, 0.5, 0.5 with scale 2.0, 0.1, 1.0 and name it Barrier
```

## Step 5: Add Physics to Obstacles

```
Apply collision to all meshes under /World that start with Wall or Pillar or Barrier
```

Or one by one:

```
Apply physics and collision to /World/Wall1
Make /World/Wall1 static by setting mass to 0
```

Repeat for all obstacles. Static obstacles need `mass=0` so they do not move on contact.

## Step 6: Add a Lidar Sensor

```
Add an RTX lidar sensor to /World/Nova_Carter/chassis_link with product name "Hesai AT128"
```

The lidar sensor provides distance measurements that a navigation stack would use for obstacle avoidance.

## Step 7: Create the ROS2 Differential Drive OmniGraph

```
Create a ROS2 differential drive action graph for the Nova Carter
```

!!! warning "OmniGraph Gotchas"
    The differential drive graph requires careful wiring. Isaac Assist handles these known issues:

    - **Joint names must match exactly** -- the Nova Carter uses `joint_wheel_left` and `joint_wheel_right`. Mismatched names cause silent failures.
    - **Wheel radius and separation** -- these values must match the URDF. For Nova Carter: wheel radius ~0.08m, wheel separation ~0.494m.
    - **Type compatibility** -- the DifferentialController node outputs `double[]` but the ArticulationController expects a specific token format. Isaac Assist inserts the correct type conversion nodes.

The graph subscribes to `/cmd_vel` (Twist messages) and drives the wheels accordingly.

## Step 8: Verify the Graph

```
List all prims under /World/ActionGraph
```

You should see nodes for: `OnPlaybackTick`, `ROS2SubscribeTwist`, `DifferentialController`, `ArticulationController`, and a `ROS2Context` node.

## Step 9: Start the Simulation

```
Play the simulation
```

The simulation must be running before ROS2 topics become active.

## Step 10: Verify ROS2 Topics

```
List all ROS2 topics
```

You should see `/cmd_vel` among the active topics. If it does not appear, check that rosbridge is running:

```
Connect to rosbridge
```

## Step 11: Drive Forward

```
Publish a Twist to /cmd_vel with linear.x = 0.5 for 3 seconds, then stop
```

Isaac Assist uses `ros2_publish_sequence` with continuous publishing at 10 Hz. The robot drives forward for 3 seconds.

!!! important "Continuous publishing is required"
    Differential drive controllers in Isaac Sim require continuous velocity commands. A single published message is consumed in one physics step and then lost. Use `ros2_publish_sequence` with `rate_hz: 10` (the default) to keep the robot moving.

## Step 12: Turn Left

```
Publish a Twist to /cmd_vel with angular.z = 0.5 for 2 seconds, then stop
```

The robot rotates in place.

## Step 13: Drive Through the Course

```
Drive the Nova Carter forward at 0.3 m/s for 5 seconds, turn right for 1 second, then forward for 3 seconds
```

Isaac Assist translates this into a `ros2_publish_sequence` with three messages and durations.

## Step 14: Read the Lidar

```
Subscribe once to the lidar topic
```

This reads a single scan from the lidar sensor and returns the distance measurements. Objects within range should appear as nearby readings.

## Step 15: Check for Collisions

```
Check collisions on /World/Nova_Carter
```

Verify the robot has proper collision geometry on its chassis and wheels.

## Step 16: Capture and Export

```
Capture a viewport screenshot
```

```
Export this scene as "nova_carter_navigation"
```

---

## Wheel Collision Setup

!!! tip "Wheel collision geometry"
    The Nova Carter's wheels need collision geometry to drive on the ground. If the robot slides instead of driving, check:

    1. Wheels have `PhysicsCollisionAPI` applied.
    2. Wheel collision shapes are cylinders (not mesh approximations that may have gaps).
    3. Wheel material friction is sufficient (default PhysX friction is usually fine).

## Common Problems

| Problem | Cause | Solution |
|---------|-------|----------|
| Robot does not move | `/cmd_vel` not publishing | Check rosbridge connection and simulation is playing |
| Robot slides sideways | Wheel joint axis wrong | Verify joint axis matches URDF (usually Y or Z) |
| Robot spins in circles | Wheel directions inverted | Swap left/right joint names in the OmniGraph |
| Robot moves once then stops | Single publish instead of continuous | Use `ros2_publish_sequence` with `rate_hz: 10` |
| Graph nodes show errors | Type mismatch between nodes | Ensure DifferentialController output type matches ArticulationController input |
