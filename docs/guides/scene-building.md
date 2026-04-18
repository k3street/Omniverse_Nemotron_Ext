# Scene Building

How to build complete simulation environments using templates, blueprints, and asset catalogs.

---

## Scene Templates

Isaac Assist ships with pre-built scene templates for common robotics scenarios. List what is available:

> List scene templates

> What scene templates are available?

### Available Templates

| Template | Description | Key Contents |
|----------|-------------|-------------|
| `tabletop_manipulation` | Table with a robot arm and graspable objects | Franka, table, objects, lights |
| `warehouse_picking` | Warehouse shelf with bin picking setup | UR10, shelves, bins, objects |
| `mobile_navigation` | Open area with obstacles for navigation | Carter, obstacles, walls, goals |
| `inspection_cell` | Inspection station with cameras and conveyor | Arm, conveyor, cameras, parts |

### Loading a Template

> Load the tabletop manipulation scene

> Set up a warehouse picking scene

> Load the mobile navigation template

Isaac Assist calls `load_scene_template` and generates code that creates all objects, robots, sensors, physics, and materials in one patch.

!!! tip "Customization after loading"
    Templates are a starting point. After loading, you can modify anything:
    > Replace the Franka in the tabletop scene with a UR10
    > Add more objects to the table
    > Move the camera to a different angle

---

## Custom Scene Blueprints

When templates don't fit your needs, generate a custom blueprint.

### Generating a Blueprint

Describe the scene you want and Isaac Assist creates a structured blueprint.

> Generate a scene blueprint for a dual-arm assembly station with two UR5 robots facing each other, a conveyor belt between them, and overhead cameras

> Create a blueprint for a kitchen environment with a mobile robot

The `generate_scene_blueprint` tool returns a JSON blueprint specifying:

- Objects and their positions
- Robots and mounting points
- Sensors and their targets
- Physics configuration
- Materials and lighting

### Validating a Blueprint

Before building, validate the blueprint for issues.

> Validate the scene blueprint

The `validate_scene_blueprint` tool checks for:

- Overlapping objects
- Missing physics on dynamic objects
- Unreachable robot poses
- Missing ground planes or lighting
- Invalid prim paths

!!! warning "Always validate"
    Validation catches problems like two robots placed at the same position or objects floating with no physics. Fix these before building.

### Building from a Blueprint

Once validated, build the scene.

> Build the scene from the blueprint

The `build_scene_from_blueprint` tool generates a comprehensive code patch that creates the entire scene. Review and approve it.

### Full Blueprint Workflow

1. Describe your scene:
   > Generate a blueprint for a quality inspection cell with a Franka robot, a turntable, two cameras, and a reject bin

2. Review the blueprint output (object list, positions, configuration).

3. Validate:
   > Validate this blueprint

4. Fix any issues reported:
   > Move the reject bin to 1.5, 0, 0 to avoid overlapping with the turntable

5. Build:
   > Build the scene from the blueprint

---

## Asset Catalog Search

Find assets from the Isaac Sim content library for your scenes.

> Search for table assets

> Find warehouse shelving

Isaac Assist searches the asset catalog and returns matching entries with their USD paths and descriptions.

> Add the industrial table from the catalog at 0, 0, 0

---

## Batch Operations

Apply operations across multiple objects at once using `batch_apply_operation`.

> Add rigid body physics to all objects on the table

> Make all boxes the same size

> Apply the steel material to all metal parts

> Delete all lights and add new ones

Batch operations generate a single code patch that modifies all matching prims, so you approve once for the entire change.

---

## Building Scenes Incrementally

You can also build scenes step by step without templates or blueprints:

> Create a ground plane

> Add a dome light with intensity 1000

> Import a Franka Panda at 0, 0, 0

> Create a cube named Table at 0.5, 0, 0.4 with scale 0.6, 1.0, 0.02 and add collision

> Create 5 small cubes on the table with rigid body physics

> Attach a camera to the Franka wrist

> What's in the scene?

---

## What's Next?

- [Creating Objects](creating-objects.md) -- Add individual objects to your scene
- [Physics & Simulation](physics-and-simulation.md) -- Configure physics for your scene
- [Materials & Appearance](materials.md) -- Style your environment
- [Importing Robots](importing-robots.md) -- Add robots to your scene
