# Your First Task

A hands-on walkthrough: create an object, add physics, and watch it fall.

!!! info "Prerequisites"
    Complete the [Quick Start](quick-start.md) first. You need the service running and the chat panel open.

---

## Step 1: Create a Cube

Type in the chat panel:

> Create a cube named PhysBox at position 0, 0, 2

Isaac Assist responds with an explanation and a **code patch card** showing Python code like:

```python
from pxr import UsdGeom, Gf
stage = omni.usd.get_context().get_stage()
prim = UsdGeom.Cube.Define(stage, '/World/PhysBox')
prim.AddTranslateOp().Set(Gf.Vec3f(0, 0, 2))
```

Click **Approve & Execute**.

**Verify:** A cube named `PhysBox` appears floating at height 2 in the viewport. You can see `/World/PhysBox` in the Stage panel.

---

## Step 2: Add a Ground Plane

Without a ground, the cube will fall forever. Type:

> Create a ground plane with collision at 0, 0, 0

Approve the patch. A ground plane appears at floor level.

---

## Step 3: Add Physics

Now make the cube a rigid body. Type:

> Add rigid body physics to /World/PhysBox

!!! tip "Selection shortcut"
    Instead of typing the full path, you can click on `PhysBox` in the viewport first, then just type:
    > Make this a rigid body

Approve the patch. The cube now has physics icons in the Stage panel.

---

## Step 4: Play the Simulation

Type:

> Play the simulation

Or click the **Play** button (▶) in the timeline bar at the bottom.

**What happens:** The cube falls under gravity and lands on the ground plane. Physics is working!

Press **Stop** (■) to reset the scene to its original state.

---

## Step 5: Explore Further

Try these follow-up commands:

| Try This | What Happens |
|----------|-------------|
| `Create a sphere at 1, 0, 3 with radius 0.3` | Adds a sphere |
| `Add rigid body physics to /World/Sphere` | Makes it fall too |
| `Set the gravity to 0, 0, -1.62` | Moon gravity! |
| `What's in the scene right now?` | Scene summary (no code, just info) |
| `Delete /World/PhysBox` | Removes the cube |
| `Undo` or `Ctrl+Z` | Reverses the last action |

---

## Key Takeaways

1. **Describe what you want** in natural language
2. **Review the code** Isaac Assist generates
3. **Approve** to execute, or **reject** if it's not what you want
4. **Ctrl+Z** to undo any action
5. **Click objects first** to give Isaac Assist selection context

---

## What's Next?

- [Creating Objects](../guides/creating-objects.md) — All the shapes, lights, and cameras you can create
- [Physics & Simulation](../guides/physics-and-simulation.md) — Rigid bodies, soft bodies, and simulation control
- [Importing Robots](../guides/importing-robots.md) — Bring in Franka, UR10, Nova Carter, and more
