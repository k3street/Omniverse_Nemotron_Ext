# Creating Objects

How to create shapes, lights, cameras, and groups using natural language.

---

## Primitive Shapes

Isaac Assist can create any USD geometric primitive. Type what you want and specify optional position, scale, and rotation.

> Create a cube at 0, 0, 0.5

> Add a sphere named Ball with radius 0.3 at position 1, 0, 1

> Create a cylinder at -1, 0, 0.5 with height 2 and radius 0.25

> Make a cone at 2, 0, 0.5

| Shape | Key Parameters |
|-------|---------------|
| Cube | `size` (default 1.0) |
| Sphere | `radius` (default 0.5) |
| Cylinder | `radius`, `height` |
| Cone | `radius`, `height` |
| Capsule | `radius`, `height` |
| Torus | `radius1` (ring), `radius2` (tube) |

---

## Position, Scale, and Rotation

You can set transforms inline or adjust them after creation.

> Create a cube at 3, -1, 0.5 with scale 2, 1, 0.5

> Rotate /World/Cube by 45 degrees around Z

> Move /World/Sphere to position 0, 2, 1

!!! tip "Selection shortcut"
    Click an object in the viewport first, then say "Move this to 0, 0, 3" instead of typing the full path.

Parameters you can set:

| Parameter | Format | Example |
|-----------|--------|---------|
| `position` | x, y, z | `0, 0, 0.5` |
| `scale` | x, y, z or uniform | `2, 1, 0.5` or `0.5` |
| `rotation_euler` | rx, ry, rz (degrees) | `0, 0, 45` |

---

## Cameras

Create cameras and switch the active viewport to them.

> Create a camera named TopDown at 0, 0, 10 looking down

> Add a camera at 3, 3, 2 looking at the origin

> Switch viewport to /World/TopDown

Isaac Assist sets the camera's position and orientation so it points where you describe. You can also set focal length and aperture:

> Create a camera with focal length 50mm at position 2, -2, 1.5

---

## Lights

Isaac Assist supports all USD light types.

=== "DomeLight"

    Provides environment-wide illumination (image-based lighting).

    > Add a dome light with intensity 1000

    > Create a DomeLight using an HDR texture

=== "DistantLight"

    Simulates sunlight with parallel rays.

    > Add a distant light pointing down with intensity 500

    > Create a DistantLight with angle 0.53 and color warm white

=== "SphereLight"

    Point light source with falloff.

    > Add a sphere light at 0, 0, 3 with radius 0.1 and intensity 5000

=== "RectLight"

    Area light for soft shadows.

    > Create a rect light above the table at 0, 0, 2 with width 1 and height 1

Other light types: DiskLight (spotlight-like), CylinderLight (tube/fluorescent).

---

## Grouping with Xform

Use Xform prims to organize objects into groups. This lets you move, scale, or delete them together.

> Create an Xform group called TableSetup

> Create a cube named Table at 0, 0, 0.5 under /World/TableSetup

> Add a sphere named Mug at 0.3, 0, 1.05 under /World/TableSetup

Now you can manipulate the whole group:

> Move /World/TableSetup to 5, 0, 0

> Scale /World/TableSetup by 2

!!! note "Hierarchy"
    When you specify "under /World/TableSetup", the object is created as a child prim. Moving the parent moves all children with it.

---

## Batch Creation

You can create multiple objects in a single request:

> Create 5 cubes in a row along the X axis, spaced 1.5 apart, starting at 0, 0, 0.5

Isaac Assist generates a loop in the code patch to create all objects at once.

---

## Deleting and Renaming

> Delete /World/Cube

> Rename /World/Box to /World/Container

All deletions can be reversed with **Ctrl+Z**.

---

## What's Next?

- [Physics & Simulation](physics-and-simulation.md) -- Make your objects interact physically
- [Materials & Appearance](materials.md) -- Add realistic materials to your shapes
- [Scene Building](scene-building.md) -- Build full environments from templates
