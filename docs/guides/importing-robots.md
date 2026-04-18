# Importing Robots

How to load robots from the asset library or from URDF/MJCF files, and control them.

---

## Import from Asset Library

Isaac Sim ships with a library of pre-configured robot assets. Isaac Assist knows them by name.

> Import a Franka Panda robot

> Load a UR10 robot at position 1, 0, 0

> Add a Nova Carter mobile robot

### Available Robots

| Robot | Type | Chat Name |
|-------|------|-----------|
| Franka Emika Panda | 7-DOF arm | `Franka`, `Panda` |
| Universal Robots UR10 | 6-DOF arm | `UR10` |
| Universal Robots UR5 | 6-DOF arm | `UR5` |
| Unitree G1 | Humanoid | `G1`, `Unitree G1` |
| Unitree Go1 | Quadruped | `Go1`, `Unitree Go1` |
| NVIDIA Jetbot | Wheeled | `Jetbot` |
| NVIDIA Carter | Wheeled | `Carter`, `Nova Carter` |
| ANYmal C | Quadruped | `ANYmal` |
| Spot | Quadruped | `Spot` |
| Kaya | Holonomic | `Kaya` |

> Import a Go1 quadruped at 0, 0, 0

> Add a Jetbot at 2, 0, 0

!!! tip "Name matching"
    Isaac Assist does fuzzy matching on robot names. "Franka", "franka panda", and "panda robot" all resolve to the same asset.

---

## Import from URDF

If you have a custom robot described in a URDF file, Isaac Assist can import it.

> Import the robot from /home/user/my_robot/robot.urdf

> Load URDF file /tmp/custom_arm.urdf at position 0, 0, 0

=== "Basic URDF Import"

    > Import robot from /path/to/robot.urdf

=== "With Options"

    > Import /path/to/robot.urdf with fixed base and merge fixed joints

Key import options: `fixedBase` (default true), `merge_fixed_joints` (default false), `self_collision` (default false).

---

## Import from MJCF

MuJoCo model files (MJCF/XML) are also supported.

> Import the MuJoCo model from /path/to/model.xml

---

## Robot Anchoring

By default, robots are imported with a fixed base so they don't fall over.

> Import a Franka with fixed base

> Make /World/Franka free-standing (remove fixed base)

> Anchor /World/UR10 to the ground

!!! warning "Floating robots"
    If you remove the fixed base from a manipulator arm, it will fall over when you play the simulation. Only do this if the robot is mounted on a mobile base or you have other supports.

---

## Reading Joint States

Query the current state of robot joints.

> What are the joint positions of /World/Franka?

> Show me the joint states of the UR10

> Read the joint angles

Isaac Assist calls `read_joint_states` and returns a table of joint names, positions (radians), and velocities.

---

## Setting Joint Targets

Move individual joints or set all joint positions at once.

> Set joint_1 of /World/Franka to 0.5 radians

> Move all joints to home position

> Set the gripper to open

> Close the Franka gripper

For position-controlled joints:

> Set joint targets for /World/Franka to [0, -0.78, 0, -2.36, 0, 1.57, 0.78]

---

## Motion Planning

Isaac Assist can plan and execute end-effector motions using the robot's kinematics.

> Move the Franka end-effector to position 0.5, 0, 0.3

> Plan a trajectory to position 0.4, 0.2, 0.5 with orientation pointing down

The motion planning pipeline:

1. **`move_to_pose`** -- Compute and execute a joint trajectory to reach a target pose
2. **`plan_trajectory`** -- Plan without executing (returns the joint waypoints)

> Plan a trajectory for the Franka to reach 0.5, 0.1, 0.3

> Execute the planned trajectory

!!! note "Collision-aware planning"
    Motion planning respects collision objects in the scene. Add obstacles before planning to get collision-free trajectories.

---

## Practical Example: Pick and Place

> Import a Franka Panda at 0, 0, 0

> Create a small cube named Target at 0.5, 0, 0.02 with rigid body physics

> Open the Franka gripper

> Move the end-effector to 0.5, 0, 0.04

> Close the Franka gripper

> Move the end-effector to 0.3, 0.3, 0.3

---

## What's Next?

- [Sensors & Cameras](sensors-and-cameras.md) -- Attach sensors to your robot
- [Physics & Simulation](physics-and-simulation.md) -- Configure the physics for your robot
- [ROS2 Integration](ros2.md) -- Connect your robot to ROS2
