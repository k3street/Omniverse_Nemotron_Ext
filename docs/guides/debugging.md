# Debugging & Diagnostics

How to identify, understand, and fix issues in your simulation using Isaac Assist's diagnostic tools.

---

## Diagnostic Tools

Isaac Assist provides several tools for inspecting and debugging your scene.

| Tool | Purpose | Chat Example |
|------|---------|-------------|
| `get_console_errors` | Fetch recent errors from Isaac Sim's console | `Show me console errors` |
| `get_physics_errors` | Get physics-specific errors and warnings | `Any physics errors?` |
| `explain_error` | Get a plain-language explanation of an error | `Explain this error: ...` |
| `fix_error` | Automatically generate a fix for a known error | `Fix this error` |
| `get_debug_info` | Collect system and scene debug information | `Get debug info` |
| `check_collisions` | Verify collision setup between prims | `Check collisions on /World/Robot` |
| `scene_summary` | Overview of everything in the scene | `What's in the scene?` |

---

## Checking Scene State

Start any debugging session by understanding what's in the scene.

> What's in the scene right now?

Isaac Assist calls `scene_summary` and returns a structured overview: all prims, their types, physics properties, materials, and hierarchy.

> How many rigid bodies are in the scene?

> List all prims with collision enabled

---

## Checking for Errors

Pull errors directly from Isaac Sim's console without switching windows.

> Show me any console errors

> Are there physics errors?

> Get the last 10 errors from the console

Isaac Assist retrieves and formats the errors so you can read them in the chat panel.

---

## Understanding Errors

When you see an error you don't understand, ask Isaac Assist to explain it.

> Explain this error: PhysX error: PxRigidBody::setGlobalPose: pose is not valid

Isaac Assist breaks down:

- **What the error means** in plain language
- **Why it happened** (common causes)
- **How to fix it** (specific steps)

---

## Fixing Errors Automatically

For known error patterns, Isaac Assist can generate a fix.

> Fix this error

> Fix the collision issue on /World/Robot

Isaac Assist analyzes the error, determines the appropriate fix, and generates a code patch for your approval.

---

## Troubleshooting Workflow

Here is a step-by-step workflow for the most common issue: "my robot falls through the floor."

### Step 1: Check collisions

> Check collisions on /World/Robot

Isaac Assist inspects the robot and ground for CollisionAPI. Output might say: "Ground plane at /World/GroundPlane has no CollisionAPI."

### Step 2: Understand the problem

> Why is the robot falling through the floor?

Isaac Assist explains: the ground plane needs CollisionAPI, or the robot's base link is missing collision meshes.

### Step 3: Apply the fix

> Fix the collision issue

Isaac Assist generates a patch that adds CollisionAPI to the ground plane and any robot links missing collision.

### Step 4: Verify

> Check collisions on /World/Robot

Confirm all collision pairs are properly configured.

> Play the simulation

The robot now rests on the ground.

---

## Common Issues and Solutions

| Symptom | Diagnostic Command | Likely Fix |
|---------|-------------------|-----------|
| Object falls through floor | `Check collisions on /World/Object` | Add CollisionAPI to floor or object |
| Robot explodes on play | `Get physics errors` | Fix overlapping collision meshes |
| Joints at wrong angles | `Read joint states of /World/Robot` | Set correct initial joint targets |
| Nothing moves on play | `Scene summary` | Add RigidBodyAPI to dynamic objects |
| Simulation runs slowly | `Get debug info` | Reduce solver iterations or mesh complexity |
| Material looks wrong | `Get console errors` | Switch to RTX renderer |
| Sensor returns no data | `Get console errors` | Check sensor is attached to valid prim |

---

## Debug Info

> Get debug info

Returns Isaac Sim version, PhysX settings, GPU info, scene statistics, active extensions, and memory usage. Useful when reporting issues.

---

## Collision Checking

Inspect specific collision relationships.

> Check collisions between /World/Robot and /World/Table

> Are there any self-collisions on the robot?

The `check_collisions` tool reports whether CollisionAPI is present, collision filter settings, active collision pairs, and any overlapping geometry at rest.

---

## Auto-Learning from Errors

Isaac Assist learns from past errors. When you encounter and fix an error, the solution is stored in the knowledge base (JSONL format, persists across restarts). The next time the same error pattern appears, Isaac Assist suggests the fix immediately.

!!! tip "Building knowledge"
    The more issues you diagnose with Isaac Assist, the smarter it gets about your specific setup.

---

## What's Next?

- [Physics & Simulation](physics-and-simulation.md) -- Understand physics configuration
- [ROS2 Integration](ros2.md) -- Debug ROS2 connectivity
- [Scene Building](scene-building.md) -- Validate scene blueprints before building
