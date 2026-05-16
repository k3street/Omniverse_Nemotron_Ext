# A5 Canonical Draft — 2026-05-16

## §1 Candidate + Category + Pattern

**Backlog id**: `ros2-bridge-setup-franka-001`
**Category**: `ros2-bridge` (first CP-NEW in this category — A1-A4 covered yrkesroll×3 + research×1)
**Pattern_hint**: `other`
**Why diversifying**: All four prior A-agents drafted pick/place, sort, or compliance tasks involving
physical robot motion; the ros2-bridge category is untouched and covers a fundamentally different
topology — pure middleware integration with no motion planner, exercising setup_ros2_bridge,
configure_ros2_time, emit_ros2_control_yaml, ros2_list_topics, and ros2_subscribe_once.

**Selection rationale**:
- Tier-1, no blockers, no Nucleus-only assets
- Distinct pattern: plumbing-only bridge validation (same class as CP-NEW-plc-conveyor / CP-NEW-opcua-12conveyors but for ROS2 middleware)
- Directly enables the full MoveIt2 pipeline (ros2-moveit2-franka-001 depends on this bridge being verified first)

---

## §2 Template Summary

**File**: `workspace/templates/CP-NEW-ros2-bridge-franka.json`
**Code field LOC**: 62 lines (scene setup + bridge bring-up + validation assertions)
**Roles count**: 1 (`primary_robot`, constraints: franka_panda / ur5e / kinova_gen3)
**Distinguishing structural_tags**:
- `isaac:bridge.ros2`
- `isaac:integration.joint_state_command`
- `isaac:topology.ros2_bridge_validation`

**Key design decisions**:
- `configure_ros2_time` called before `setup_ros2_bridge` — ordering prevents /clock timestamp=0 issue on first messages
- `verify_args.stages = []` (plumbing-only, no cube delivery, same pattern as CP-NEW-plc-conveyor)
- `simulate_args = null` — consistent with bridge-only templates
- `motion_controllers.verified = []`, untested = [ros2_control, moveit2] — honest: not yet run
- Active validation baked into `code` field: `ros2_list_topics` + `ros2_subscribe_once` with 7-joint assertion
- `emit_ros2_control_yaml` included for downstream MoveIt2 bring-up completeness

---

## §3 Form-Gate Result

```
workspace/templates/CP-NEW-ros2-bridge-franka.json: OK

1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Exit 0. Clean pass, no warnings.

---

## §4 Backlog Status

`ros2-bridge-setup-franka-001` updated:
- `status: queued` → `status: drafted`
- `template_file: workspace/templates/CP-NEW-ros2-bridge-franka.json`
- `drafted_date: "2026-05-16"`

Remaining `ros2-bridge` entries still queued: 9 (ros2-moveit2-franka-001, ros2-opcua-conveyor-12-001, ros2-modbus-plc-fixture-001, ros2-mqtt-sparkplug-001, ros2-multi-rate-setup-001, ros2-zmq-stream-viz-001, ros2-rosbag-replay-001, ros2-nav2-integration-001, ros2-ros2control-compat-001).
