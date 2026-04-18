# Glossary

Key terms used throughout Isaac Assist and Isaac Sim.

---

## USD & Scene Concepts

**USD (Universal Scene Description)**
: The file format and framework Isaac Sim uses to describe 3D scenes. Everything in your scene is stored as USD data.

**Stage**
: The currently loaded USD scene. Think of it as the "document" you're working on.

**Prim (Primitive)**
: Any object in the scene — a cube, a robot, a light, a camera, a group. Everything in the stage tree is a prim.

**Prim Path**
: The address of a prim in the scene hierarchy, like a file path. Example: `/World/Robot/arm_link_3`

**Attribute**
: A property on a prim — position, scale, color, visibility, mass, etc.

**Xform**
: A transform prim — an empty container that can hold position/rotation/scale. Used to group other prims.

**Reference**
: A link to an external USD file. Used to import assets without copying them into your scene.

**Layer**
: USD scenes can have multiple layers stacked on top of each other. Each layer can override attributes from layers below it.

---

## Physics

**Rigid Body**
: An object that participates in physics simulation — it has mass, can collide, and responds to forces and gravity. Apply `RigidBodyAPI` to make a prim a rigid body.

**Collision**
: The invisible shape used for physics collision detection. Often simpler than the visual mesh. Apply `CollisionAPI` to enable collisions.

**Articulation**
: A chain of connected rigid bodies linked by joints — like a robot arm. The root prim has `ArticulationRootAPI`.

**Joint**
: A connection between two rigid bodies that constrains their relative motion. Types: revolute (rotation), prismatic (sliding), fixed.

**Deformable / Soft Body**
: An object that can bend, stretch, or deform — cloth, rubber, rope. Uses PhysX deformable APIs.

**Solver Iterations**
: How many times the physics engine refines its calculations per step. More iterations = more accurate but slower.

---

## Robots & Sensors

**URDF (Unified Robot Description Format)**
: An XML format for describing robot models — links, joints, meshes, inertia. Common in ROS.

**MJCF (MuJoCo Description Format)**
: An XML format for describing robots and environments used by MuJoCo physics.

**End-Effector**
: The "hand" or "tool" at the end of a robot arm — a gripper, suction cup, or camera.

**LiDAR**
: A sensor that measures distances using laser pulses. Returns a point cloud of the environment.

**IMU (Inertial Measurement Unit)**
: A sensor that measures acceleration and angular velocity. Used for balance and orientation.

**Contact Sensor**
: A sensor that detects when a prim touches another prim. Reports contact forces.

---

## OmniGraph

**OmniGraph**
: Isaac Sim's visual scripting system. Connects nodes with data flow to create behaviors without writing code.

**Action Graph**
: An OmniGraph that runs in response to events (tick, key press, physics step). Used for robot control logic.

**Push Graph**
: An OmniGraph where data flows automatically when inputs change.

---

## AI & Service

**Tool**
: A function that Isaac Assist can call to perform an action — create a prim, add physics, import a robot. There are 50+ tools available.

**Code Patch**
: The Python/USD code that Isaac Assist generates to perform your request. Shown in the approval dialog before execution.

**Approval Flow**
: The review step where you see the generated code and choose to approve or reject it.

**Kit RPC**
: The internal communication bridge between the Isaac Assist service and Isaac Sim. Runs on port 8001.

**Knowledge Base**
: Isaac Assist's memory of past errors, fixes, and learned patterns. Improves over time.

---

## ROS2

**Topic**
: A named channel for publishing and subscribing to messages in ROS2. Example: `/cmd_vel` for velocity commands.

**Service**
: A request/response pattern in ROS2. Unlike topics (continuous stream), services are one-shot calls.

**TF (Transform)**
: The ROS2 system for tracking coordinate frames and their relationships over time.

**Bridge**
: Software that connects Isaac Sim's internal simulation to external ROS2 nodes.

---

## Simulation

**Replicator / SDG (Synthetic Data Generation)**
: NVIDIA's tool for generating training data (images with annotations) from simulation.

**Domain Randomization**
: Randomly varying lighting, textures, positions, etc. during SDG to make trained models more robust.

**IsaacLab**
: NVIDIA's reinforcement learning framework built on top of Isaac Sim. Used for training robot policies.
