# Morning Brief — 2026-05-10

Generated from Phase 0 baseline + autonomous telemetry collection.

## Summary
- stable_ok: **2**
- flaky:     1
- stable_fail: 22
- other:     0
- total CPs in patched-set: 25
- telemetry probes available: 0

## ur10_grip (6 CPs)
### CP-73 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

### CP-74 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

### CP-76 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

### CP-80 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

### CP-84 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

### CP-85 — status=stable_fail rate=0.00
  - UR10 family — IsaacSurfaceGripper articulation-link bug. Verify raycast→FixedJoint workaround applied.

## multi_robot_relay (4 CPs)
### CP-51 — status=stable_fail rate=0.00
  - Multi-robot relay — investigate MUTEX_PATH spline injection, robot-B sensor zone, handoff timing.

### CP-53 — status=stable_fail rate=0.00
  - Multi-robot relay — investigate MUTEX_PATH spline injection, robot-B sensor zone, handoff timing.

### CP-67 — status=stable_fail rate=0.00
  - Multi-robot relay — investigate MUTEX_PATH spline injection, robot-B sensor zone, handoff timing.

### CP-68 — status=stable_fail rate=0.00
  - Multi-robot relay — investigate MUTEX_PATH spline injection, robot-B sensor zone, handoff timing.

## scenario_profile_obstacle (3 CPs)
### CP-37 — status=stable_fail rate=0.00
  - Obstacle-rich — sensor-gate factor + scene-collision exclude_floor policy (per scenario-profile spec).

### CP-46 — status=stable_fail rate=0.00
  - Obstacle-rich — sensor-gate factor + scene-collision exclude_floor policy (per scenario-profile spec).

### CP-48 — status=stable_fail rate=0.00
  - Obstacle-rich — sensor-gate factor + scene-collision exclude_floor policy (per scenario-profile spec).

## investigate (10 CPs)
### CP-05 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-06 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-35 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-40 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-52 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-57 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-58 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-59 — status=flaky rate=0.60
  - No specific signal — needs manual investigation.

### CP-60 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

### CP-62 — status=stable_fail rate=0.00
  - No specific signal — needs manual investigation.

## no_action (2 CPs)
### CP-22 — status=stable_ok rate=1.00
  - Already passing

### CP-65 — status=stable_ok rate=1.00
  - Already passing


## How to act tomorrow
- **controller_install**: probably means the canonical's controller setup is wrong; check setup_pick_place_controller args.
- **planner_tune**: cuRobo failing >50%. Check scene_cfg obstacles, pose feasibility. Consider Phase 4 scenario-profile.
- **gripper_or_drop**: Mode B FJ release or drop-precision. Targeted fix in tool_executor's pick-place handler.
- **ur10_grip**: verify raycast→FixedJoint workaround. CP-74/80 specific belt-pause-from-callback bug remains.
- **multi_robot_relay**: MUTEX_PATH spline injection + sensor zone for robot B. Phase 4 scenario-profile candidate.
- **scenario_profile_obstacle**: sensor-gate factor + scene-collision exclude_floor — Phase 4 candidate.
- **investigate**: needs manual run + look at simulate_traversal_check output for pattern.
