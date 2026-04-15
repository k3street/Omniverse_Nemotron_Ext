# Physics & Simulation

How to add physics properties to objects, configure the simulation, and work with soft bodies.

---

## Adding Rigid Body Physics

To make an object respond to gravity and collisions, it needs two USD APIs: **RigidBodyAPI** (dynamics) and **CollisionAPI** (collision shape).

> Add rigid body physics to /World/Cube

> Make /World/Sphere a rigid body

!!! tip "Selection shortcut"
    Click the object first, then type: "Make this a rigid body"

Isaac Assist applies both APIs in one step. The generated code looks like:

```python
from pxr import UsdPhysics
UsdPhysics.RigidBodyAPI.Apply(prim)
UsdPhysics.CollisionAPI.Apply(prim)
```

To add only collision (a static collider that doesn't move):

> Add collision to /World/Table but keep it static

---

## Ground Planes

Most physics scenes need a ground plane so objects don't fall forever.

> Create a ground plane

> Add a ground plane with collision at 0, 0, 0

The ground plane is a large flat mesh with CollisionAPI applied. It acts as a static surface.

---

## Deformable and Soft Bodies

Isaac Sim supports deformable body simulation through PhysX. Isaac Assist can set up common soft body types.

=== "Cloth"

    > Make /World/Sheet a cloth simulation

    > Create a cloth plane at 0, 0, 2 with 50x50 resolution

    Cloth uses particle-based simulation with self-collision.

=== "Soft Body (Sponge, Rubber)"

    > Make /World/Cube a soft body like a sponge

    > Add deformable body physics to /World/Ball with stiffness 0.5

    Soft bodies use FEM (finite element method) simulation.

=== "Rope"

    > Create a rope from 0, 0, 1 to 2, 0, 1 with 20 segments

    Ropes are chains of rigid capsules connected by joints.

| Body Type | Method | Key Parameters |
|-----------|--------|---------------|
| Rigid | RigidBodyAPI | mass, friction, restitution |
| Cloth | Particle cloth | stretch/bend stiffness, damping |
| Soft/Deformable | DeformableBodyAPI | youngs_modulus, poissons_ratio |
| Rope | Joint chain | segment_count, joint_stiffness |

---

## Simulation Control

Control the simulation timeline from the chat panel.

| Command | Chat Message |
|---------|-------------|
| **Play** | `Play the simulation` |
| **Pause** | `Pause the simulation` |
| **Stop** | `Stop the simulation` |
| **Step** | `Step 10 frames` or `Advance one physics step` |
| **Reset** | `Reset the simulation` |

> Play the simulation

> Step 5 frames forward

> Stop and reset

!!! note "Play vs Reset"
    **Stop** returns all objects to their positions at the last play. **Reset** additionally clears any accumulated state. Use **Stop** during iterative testing.

---

## Physics Parameters

Tune the physics engine for your scenario.

| Parameter | Default | Chat Example |
|-----------|---------|-------------|
| Gravity | (0, 0, -9.81) | `Set gravity to 0, 0, -3.71` (Mars) |
| Timestep | 1/60 s | `Set physics timestep to 1/120` |
| Position iterations | 4 | `Set solver position iterations to 16` |
| Velocity iterations | 1 | `Set solver velocity iterations to 4` |

---

## Batch Physics Operations

Apply physics to multiple objects at once.

> Add rigid body physics to all cubes in the scene

> Make everything under /World/Objects a rigid body

> Remove physics from all objects

Isaac Assist iterates over matching prims and applies the APIs in a single code patch.

---

## Troubleshooting Physics

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Object falls through floor | Missing CollisionAPI on floor or object | `Add collision to /World/GroundPlane` |
| Object jitters or explodes | Overlapping colliders at start | Move objects apart before playing |
| Simulation too slow | High solver iterations or many objects | Reduce solver iterations or simplify scene |
| Object doesn't move | Missing RigidBodyAPI | `Add rigid body physics to /World/Object` |

!!! warning "Always add collision"
    RigidBodyAPI alone is not enough for objects to interact. You also need CollisionAPI. Isaac Assist adds both by default when you say "add physics."

---

## What's Next?

- [Importing Robots](importing-robots.md) -- Bring in articulated robots with physics
- [Debugging & Diagnostics](debugging.md) -- Diagnose physics issues
- [Scene Building](scene-building.md) -- Build complete physics-enabled scenes
