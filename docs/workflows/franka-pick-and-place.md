# Franka Pick-and-Place Workflow

This workflow walks you through setting up a complete Franka Panda pick-and-place simulation using only chat commands. By the end you will have a robot arm on a table, objects to pick, physics enabled, sensors attached, and motion planning wired up.

---

## Step 1: Import the Franka Robot

```
Import a Franka Panda robot
```

Isaac Assist loads the Franka from the asset library and places it at the world origin.

## Step 2: Create a Table

```
Create a cube at 0, 0, 0.4 with scale 0.8, 0.8, 0.02 and name it Table
```

This creates a thin slab acting as the table surface.

## Step 3: Anchor the Robot to the Table

```
Anchor the Franka to /World/Table with position 0, 0, 0.41
```

The robot base is now fixed on top of the table surface. This sets `fixedBase=True` and removes the root joint so the robot does not fall.

## Step 4: Add Physics to the Table

```
Apply collision and rigid body physics to /World/Table
```

!!! note
    The table needs both `PhysicsRigidBodyAPI` and `PhysicsCollisionAPI`. Isaac Assist applies both when you say "add physics."

## Step 5: Make the Table Static

```
Set the mass of /World/Table to 0 to make it static
```

A mass of 0 tells PhysX to treat the table as a kinematic (immovable) body.

## Step 6: Create Objects to Pick

```
Create a cube at 0.4, 0.1, 0.45 with scale 0.04, 0.04, 0.04 and name it RedCube
```

```
Create a sphere at 0.4, -0.1, 0.45 with scale 0.03, 0.03, 0.03 and name it BlueBall
```

## Step 7: Add Physics to the Objects

```
Add rigid body physics and collision to /World/RedCube and /World/BlueBall
```

Or use a batch operation:

```
Apply physics to all meshes under /World that aren't the Table or Franka
```

## Step 8: Create a Ground Plane

```
Create a ground plane at 0, 0, 0
```

Without a ground plane, objects will fall forever.

## Step 9: Add Materials for Visual Clarity

```
Create a red metal material and apply it to /World/RedCube
```

```
Create a blue glossy material and apply it to /World/BlueBall
```

## Step 10: Attach a Contact Sensor to the Gripper

```
Add a contact sensor to /World/Franka/panda_leftfinger
```

This lets you detect when the gripper touches an object.

## Step 11: Check the Scene

```
Show me a scene summary
```

Isaac Assist returns a structured overview: prim counts, physics setup, robots detected, sensors present. Verify everything looks correct before simulating.

## Step 12: Start the Simulation

```
Play the simulation
```

At this point the physics engine is active. The cube and sphere should rest on the table under gravity.

## Step 13: Move the End-Effector to the Cube

```
Move the Franka end-effector to position 0.4, 0.1, 0.50
```

Isaac Assist uses RMPflow motion planning to compute a collision-free joint trajectory and executes it. The arm moves above the red cube.

## Step 14: Lower to Grasp Position

```
Move the Franka end-effector to position 0.4, 0.1, 0.44
```

The end-effector descends to the cube's height.

## Step 15: Close the Gripper

```
Set joint targets on /World/Franka for panda_finger_joint1 to position 0.01
```

```
Set joint targets on /World/Franka for panda_finger_joint2 to position 0.01
```

The fingers close around the cube.

## Step 16: Lift the Object

```
Move the Franka end-effector to position 0.4, 0.1, 0.55
```

The arm lifts with the cube grasped.

## Step 17: Move to Place Position

```
Move the Franka end-effector to position 0.3, -0.2, 0.50
```

## Step 18: Open the Gripper

```
Set joint targets on /World/Franka for panda_finger_joint1 to position 0.04
```

```
Set joint targets on /World/Franka for panda_finger_joint2 to position 0.04
```

The cube drops at the new location.

## Step 19: Capture the Result

```
Capture a viewport screenshot
```

Review the final scene state visually.

## Step 20: Export the Scene

```
Export this scene as a project package named "franka_pick_and_place"
```

This generates a standalone Python script, README, and configuration files you can reuse outside Isaac Assist.

---

## Tips

!!! tip "Use motion planning, not joint targets"
    `move_to_pose` handles inverse kinematics and collision avoidance for you. Only use `set_joint_targets` for gripper fingers or when you need direct joint control.

!!! warning "Anchoring matters"
    If you forget to anchor the Franka, it will fall over when simulation starts. Always anchor stationary robots.

!!! info "Sensor verification"
    After attaching a contact sensor, ask _"Read the contact sensor on panda_leftfinger"_ during simulation to verify it detects contact forces.
