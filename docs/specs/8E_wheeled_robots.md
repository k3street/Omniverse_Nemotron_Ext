# Phase 8E — Wheeled Robots & Conveyor Systems

**Status:** Not implemented  
**Depends on:** Phase 8A (occupancy_map for navigation), Phase 1C (OmniGraph)  
**Research:** `research_reports/8E_wheeled_robots.md`

---

## Tools

### 8E.1 `create_wheeled_robot(robot_path, drive_type, wheel_config)`

**API:** `isaacsim.robot.wheeled_robots` (confirmed stable 4.5-6.0)

**Drive types:**
| Type | Controller | Notes |
|------|-----------|-------|
| differential | `DifferentialController` | Standard 2-wheel |
| ackermann | `AckermannController` | Car-like, non-holonomic |
| holonomic | `HolonomicController` | Mecanum/omnidirectional |

**Correction:** `mecanum` and `omnidirectional` are the SAME controller (`HolonomicController`). Use `holonomic` as the enum value.

**Mecanum setup is non-trivial:** Requires setting custom USD attributes on every wheel joint (`isaacmecanumwheel:radius`, `isaacmecanumwheel:angle`) + running `HolonomicRobotUsdSetup`. Document this complexity.

**Generated code:** OmniGraph action graph with controller node + ArticulationController.

### 8E.2 `navigate_to(robot_path, target_position, planner)`

**Critical: Isaac Sim has NO built-in A* or RRT for wheeled robots.**

**Implementation requires:**
1. **Occupancy map** from Phase 8A.3
2. **Path planner** — must implement or bundle:
   - A* on 2D grid (suitable for holonomic/differential)
   - For Ackermann: Reeds-Shepp or Dubins curves (respects turning radius)
3. **Pose estimation** — in simulation, use ground truth from USD prim transform
4. **Path following** — feed waypoints to `WheelBasePoseController` (exists in Isaac Sim)

**Recommended: Extract planner as pure Python module** for testability:
```python
def plan_path(occupancy_grid: np.ndarray, start: tuple, goal: tuple, planner: str) -> List[Tuple]:
    if planner == "astar":
        return astar_2d(grid, start, goal)
    elif planner == "reeds_shepp":
        return reeds_shepp(grid, start, goal, min_radius)
```

**Navigation loop must run across many physics steps** — use physics callback, not fire-and-forget.

**Missing (defer to future):**
- Dynamic obstacle replanning
- SLAM / localization (sim uses ground truth)
- Sensor fusion

### 8E.3 `create_conveyor(mesh_path, speed, direction)`

**API:** `isaacsim.asset.gen.conveyor` + `OgnIsaacConveyor` OmniGraph node

**Correction:** Mechanism is **PhysX surface velocity** (tangential contact force), NOT "rigid body velocity injection."

**CRITICAL: CPU-only physics.** PhysX surface velocity is incompatible with GPU/Fabric physics (`use_fabric=True`). If the scene uses GPU physics (default for RL), the conveyor silently won't work.

**Implementation must:**
1. Check physics backend: if GPU → warn user that conveyor requires CPU physics
2. Generate OmniGraph with `OgnIsaacConveyor` node (programmatic API confirmed)
3. Set speed and direction attributes

### 8E.4 `create_conveyor_track(waypoints, belt_width, speed)`

**`isaacsim.asset.gen.conveyor.ui` has NO programmatic API** (confirmed, GUI-only Track Builder).

**Realistic implementation:** Generate individual conveyor segments at each waypoint using `OgnIsaacConveyor`, with computed orientations for straight/curved sections. This is a geometry + asset placement problem, not a single API call.

### 8E.5 `merge_meshes(prim_paths, output_path)`

**`isaacsim.util.merge_mesh` has NO Python API** (confirmed, GUI-only).

**Realistic implementation:** Use raw USD/UsdGeom APIs:
```python
# Combine meshes manually via UsdGeom.Mesh
# Copy points, faceVertexCounts, faceVertexIndices from each source
# Offset vertex indices for subsequent meshes
# Combine material assignments
```

**Note:** "Deduplicates vertices" is NOT a feature of the extension. Welding requires explicit tolerance-based vertex merging (can use `trimesh` or `numpy` for this).

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Wheeled robot OG codegen | L0 | Verify controller node type per drive_type |
| A* path planner | L0 | Pure Python on grid fixture — critical for testability |
| Conveyor OG codegen | L0 | Verify OgnIsaacConveyor node |
| GPU physics check | L0 | Detect and warn |
| Conveyor track geometry | L0 | Segment orientation computation |
| Navigation execution | L3 | Requires Kit + physics |

## Known Limitations

- No built-in path planner — must implement A*/Reeds-Shepp
- Conveyors require CPU physics (incompatible with GPU RL training)
- Conveyor track builder is GUI-only — must place segments programmatically
- merge_mesh is GUI-only — must implement via raw USD APIs
- No dynamic obstacle replanning
- No localization (uses ground truth in sim)
