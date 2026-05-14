# Articulated Joints Research

For canonicals needing drawers (#30 FrankaDrawerOpen), active flippers (CP-05 v2), peg-in-hole (#22), gantries (#29), conveyor rollers.

## API summary

### Create joints

```python
from pxr import UsdPhysics, PhysxSchema, Sdf
import omni.usd
stage = omni.usd.get_context().get_stage()

# Revolute (hinge)
j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Cabinet/DrawerJoint")
j.CreateAxisAttr("Z")  # "X", "Y", "Z"
j.CreateBody0Rel().SetTargets([Sdf.Path("/World/Cabinet/Body")])  # parent
j.CreateBody1Rel().SetTargets([Sdf.Path("/World/Cabinet/Drawer")])  # child

# Prismatic (slider — drawer slide)
j = UsdPhysics.PrismaticJoint.Define(stage, "/World/Cabinet/SlideJoint")
j.CreateAxisAttr("Y")
# (same body0/body1 setup)

# Fixed (rigid weld — cube-to-gripper during transport)
j = UsdPhysics.FixedJoint.Define(stage, "/World/Gripper/AttachJoint")
j.GetPrim().GetRelationship("physics:body0").SetTargets([...])
j.GetPrim().GetRelationship("physics:body1").SetTargets([...])
```

### Limits (deg for revolute, m for prismatic)
```python
j.CreateLowerLimitAttr(-90.0)
j.CreateUpperLimitAttr(0.0)
```

### Drive (DriveAPI; "angular" for revolute, "linear" for prismatic)
```python
drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
drive.CreateTypeAttr("force")  # or "acceleration"
drive.CreateStiffnessAttr(1000.0)  # Kp
drive.CreateDampingAttr(100.0)     # Kd
drive.CreateTargetPositionAttr(-45.0)
drive.CreateMaxForceAttr(1e6)
# Runtime change:
drive.GetTargetPositionAttr().Set(-90.0)  # or .GetTargetVelocityAttr()
```

### Read state (requires JointStateAPI)
```python
PhysxSchema.JointStateAPI.Apply(j.GetPrim(), "angular")
state = PhysxSchema.JointStateAPI(j.GetPrim(), "angular")
pos = state.GetPositionAttr().Get()  # degrees
vel = state.GetVelocityAttr().Get()  # deg/s
# Effort/torque:
# isaacsim.sensors.physics.EffortSensor(prim_path=joint_path)
```

## New tools to add

### `create_articulated_joint`
```json
{
  "joint_path": "USD path for new joint",
  "parent_path": "body0 path",
  "child_path": "body1 path",
  "joint_type": "revolute | prismatic | fixed | spherical",
  "axis": "X | Y | Z (required for revolute/prismatic)",
  "lower_limit": "deg or m (optional)",
  "upper_limit": "(optional)",
  "stiffness": "Kp >0 for position drive (optional)",
  "damping": "Kd (optional)",
  "target_position": "initial target (optional)",
  "max_force": "default 1e6"
}
```

### `set_joint_drive_target`
For per-tick drive updates (active flipper, animated drawer).
```json
{
  "joint_path": "USD joint path",
  "target_position": "(optional)",
  "target_velocity": "(optional)",
  "stiffness": "override (optional)",
  "damping": "override (optional)"
}
```

## Existing tools that overlap

| Tool | Covers |
|---|---|
| `set_joint_targets` | High-level ArticulationController (not per-USD-joint) |
| `get_articulation_state` | All joint pos/vel/names for a robot articulation |
| `set_drive_gains` | Writes Kp/Kd to `UsdPhysics.DriveAPI` on a joint |
| `get_drive_gains` | Reads Kp/Kd |
| `get_joint_limits` / `set_joint_limits` | physics:lowerLimit/upperLimit |
| `set_joint_velocity_limit` | physxJoint:maxJointVelocity |
| `get_joint_positions` / `_velocities` / `_torques` | PhysxJointStateAPI per-joint reads |
| `apply_api_schema` | Can apply DriveAPI / JointStateAPI |

**Gap**: no tool creates a joint from scratch between two arbitrary prims. `set_joint_targets` works only on existing articulations imported from URDF/USD.

## Worked examples

### Cabinet drawer (revolute, limited 0..-60°, position drive)
```python
j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Cabinet/DrawerJoint")
j.CreateAxisAttr("Z")
j.CreateBody0Rel().SetTargets([Sdf.Path("/World/Cabinet/Body")])
j.CreateBody1Rel().SetTargets([Sdf.Path("/World/Cabinet/Drawer")])
j.CreateLowerLimitAttr(-60.0); j.CreateUpperLimitAttr(0.0)
d = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
d.CreateStiffnessAttr(800.0); d.CreateDampingAttr(80.0); d.CreateMaxForceAttr(1e5)
PhysxSchema.JointStateAPI.Apply(j.GetPrim(), "angular")
```

### Active flipper (90° rotate in 1s — velocity drive then position-lock)
```python
# Setup
j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Station/FlipperJoint")
j.CreateAxisAttr("Y")
# bodies...
d = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
d.CreateStiffnessAttr(0.0); d.CreateDampingAttr(5000.0)  # velocity drive
d.CreateTargetVelocityAttr(90.0)  # deg/s → 90° in 1s

# After 1s, switch to position hold:
d.GetTargetVelocityAttr().Set(0.0)
d.GetStiffnessAttr().Set(2000.0); d.GetTargetPositionAttr().Set(-90.0)
```

## Mapping to canonicals

| Canonical | Joint type | Notes |
|---|---|---|
| #30 FrankaDrawerOpen | Revolute or Prismatic | `create_articulated_joint` + `set_joint_drive_target` |
| CP-05 v2 active flipper | Revolute + velocity drive | Drives flip sequence |
| #20 Brick-layer palletizer | Revolute (wrist rotation) | Existing tools cover robot joints; new tool for fixture pivot |
| #29 2-DOF gantry | 2× Prismatic | Two joint creations on X and Y |
| #22 Peg-in-hole | No joint | Use `PhysxContactReportAPI` + `get_contact_report` |
| Cube-to-gripper coupling | Fixed | `create_articulated_joint(joint_type="fixed")` welds during transport |
| Conveyor rollers | Multiple revolute (velocity drive) | Per-roller joint + `set_joint_drive_target` |

**Key implementation note**: both body0 + body1 must have `PhysicsRigidBodyAPI` applied (or sit inside an articulation). For active flipper, articulation root must be on frame ancestor — not on joint prim itself. Auto-apply `RigidBodyAPI` on missing bodies. Apply `JointStateAPI` whenever drive configured (required for state readback).

Source: Sonnet agent `a13b73dc0e08c2503` 2026-05-07.
