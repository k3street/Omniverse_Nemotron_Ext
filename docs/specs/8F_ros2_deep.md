# Phase 8F — ROS2 Deep Integration

**Status:** Not implemented (4B stubs exist)  
**Depends on:** Phase 4B (basic ROS2 bridge)  
**Research:** `research_reports/8F_ros2_deep.md`, `rev2/verify_8F_urdf.md`

---

## Overview

Go beyond basic topic pub/sub — expose TF trees, URDF publishing, and full bridge configuration.

---

## Tools

### 8F.1 `show_tf_tree(root_frame)`

**API:** `isaacsim.ros2.tf_viewer.ITransformListener`

**Implementation:**
```python
interface = acquire_transform_listener_interface()
interface.initialize(ros_distro)  # "humble" or "jazzy" — detect dynamically
interface.spin()
raw = interface.get_transforms(root_frame)
# Format raw into tree string manually — no built-in formatter
```

**Prerequisites:**
- A `ROS2PublishTransformTree` OmniGraph node must be active — otherwise tf_viewer receives nothing
- Tool should check for this node and create it automatically if missing
- `ros_distro` must be detected at runtime, not hardcoded

### 8F.2 `publish_robot_description(articulation_path, topic)`

**CRITICAL CORRECTION:** `isaacsim.ros2.urdf` is a **URDF IMPORTER** (ROS2 → Isaac Sim), NOT an exporter. Confirmed by both rev1 and rev2.

**Correct implementation:**
1. Export USD → URDF via `isaacsim.asset.exporter.urdf` (GUI-only — may need workaround via internal API or manual export)
2. Publish via `rclpy` Python:
```python
import rclpy
from std_msgs.msg import String

node = rclpy.create_node("robot_description_publisher")
pub = node.create_publisher(String, topic, qos_profile=QoSProfile(
    durability=DurabilityPolicy.TRANSIENT_LOCAL  # late subscribers receive it
))
pub.publish(String(data=urdf_string))
```

**Known limitations of `isaacsim.asset.exporter.urdf`:**
- Tree-structure kinematics only (no closed chains)
- Only prismatic, revolute, fixed joints survive
- Complex USD geometry may be dropped
- No documented programmatic Python API — GUI path only

### 8F.3 `configure_ros2_bridge(config)`

**Type:** CODE_GEN handler (generates OmniGraph action graph)

**Critical details missing from original spec:**

1. **`ROS2Context` node is MANDATORY** — every graph needs one
2. Camera publishing requires `ROS2CameraHelper` node (not direct `PublishImage`)
3. Services require 3 nodes each: `ServiceClient`, `ServiceServerRequest`, `ServiceServerResponse`
4. **Action servers/clients NOT supported** as OmniGraph nodes — `rclpy` scripting only
5. **Lifecycle nodes NOT supported**

**Isaac Sim 6.0 namespace change:** `isaacsim.ros2.bridge.*` → `isaacsim.ros2.nodes.*`

```python
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
```

**QoS profiles:** `ROS2QoSProfile` node exists but has a known bug — custom profiles can't be saved to USD unless `createProfile` is set to "Custom" first. Handle this ordering in generated code.

### 8F.4 Domain Randomization + ROS2

**CORRECTION:** Cannot be "wired together" — different execution contexts.

DR (`isaacsim.replicator.domain_randomization`) operates through its own OmniGraph pass. ROS2 bridge publishes sensor data. They are parallel concerns, not a connected pipeline.

**Correct approach:** Standalone workflow coordinator that ensures ordering:
1. DR randomization step
2. Physics step
3. OmniGraph tick
4. ROS2 publish

This is NOT an OmniGraph graph wiring problem — it's a script-level orchestration problem.

---

## ROS2 Version Compatibility

| Version | Support |
|---------|---------|
| Humble (Ubuntu 22.04) | Full |
| Jazzy (Ubuntu 24.04) | Full |
| Kilted | Experimental/community |

Isaac Sim auto-detects host distro via `system_default` setting. Bundled libraries ship for both Humble and Jazzy — no separate ROS2 install needed for basic bridge.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| OG bridge codegen | L0 | compile(), verify ROS2Context included, correct namespace per version |
| QoS profile handling | L0 | Ordering workaround in generated code |
| TF tree formatting | L0 | Mock raw data → formatted tree string |
| Namespace version switch | L0 | 5.1 vs 6.0 node type names |
| Actual topic pub/sub | L3 | Requires Kit + ROS2 |

## Known Limitations

- URDF export is GUI-only (no documented programmatic API)
- Action servers/clients: `rclpy` scripting only, no OmniGraph
- Lifecycle nodes: not supported
- QoS save bug with custom profiles
- DR + ROS2 is orchestration, not graph wiring
- 6.0 bridge namespace split requires conditional imports
