# Phase 8A — Cloner, Debug Draw, Occupancy Map, Camera, Sensors

**Status:** Not implemented (except clone_prim in Phase 3 codegen)  
**Depends on:** Phase 3 (clone_prim predecessor), Phase 4C (camera)  
**Blocks:** Phase 8B (debug_draw), Phase 8E (occupancy_map)  
**Research:** `research_reports/8A_quick_wins.md`, `rev2/verify_api_claims.md`

---

## Tools

### 8A.1 `clone_envs(source_path, num_envs, spacing, collision_filter)`

**API:** `isaacsim.core.cloner.GridCloner` (confirmed stable 4.5-6.0)

**Corrections:**
- `collision_filter` is NOT a parameter of `clone()`. It's a separate call: `cloner.filter_collisions(physicsscene_path, collision_root_path, prim_paths, global_paths)`
- Use `GridCloner` (not base `Cloner`) for RL grid layouts
- **Must set `replicate_physics=True`** — difference between 5s and 60s+ init
- Default `spacing` should be 2.5m (not 1.0m) to avoid inter-env arm collisions

**Generated code pattern:**
```python
from isaacsim.core.cloner import GridCloner

cloner = GridCloner(spacing=spacing)
positions = cloner.clone(
    source_prim_path=source_path,
    prim_paths=prim_paths,
    positions=positions,
    replicate_physics=True,  # CRITICAL for performance
)

# Collision filtering is a SEPARATE step:
cloner.filter_collisions(
    physicsscene_path="/World/PhysicsScene",  # auto-detect this
    collision_root_path="/World/collisionGroups",
    prim_paths=prim_paths,
)
```

### 8A.2 `debug_draw(draw_type, points, color, lifetime)`

**API:** `isaacsim.util.debug_draw` (confirmed stable)

**Available draw types (confirmed):**
- `points` — `draw.draw_points(points, colors, sizes)`
- `lines` — `draw.draw_lines(start_points, end_points, colors, widths)`
- `lines_spline` — `draw.draw_lines_spline(points, color, width, closed)`

**NOT available (remove from spec):** `spheres`, `arrows`, `boxes`, `text`

**For spheres/boxes:** Use IsaacLab's `VisualizationMarkers` class or create temporary `UsdGeom` prims.

**`lifetime` parameter does NOT exist.** Implement manually:
```python
import carb
draw = _debug_draw.acquire_debug_draw_interface()
draw.draw_points(points, colors, sizes)
# Schedule clear after N seconds:
carb.timer.schedule(lambda: draw.clear_points(), delay_seconds=lifetime)
```

### 8A.3 `generate_occupancy_map(origin, dimensions, resolution, height_range)`

**API:** `isaacsim.asset.gen.omap.MapGenerator`

**Corrections:**
- `height_range` is NOT a named parameter. Map to `set_transform()` z-bounds:
```python
gen = MapGenerator()
gen.update_settings(cell_size=resolution)
gen.set_transform(
    origin=carb.Float3(origin[0], origin[1], 0),
    min_bound=carb.Float3(-dim[0]/2, -dim[1]/2, height_range[0]),
    max_bound=carb.Float3(dim[0]/2, dim[1]/2, height_range[1]),
)
gen.generate2d()
buffer = gen.get_buffer()
```

**Performance:** Synchronous PhysX ray-cast, blocks Kit main thread. For large scenes (1000×1000 grid), takes several seconds. Consider running via Kit RPC with extended timeout.

**Requires:** All geometry must have Collisions Enabled or it's invisible to the map.

### 8A.4 `inspect_camera(camera_path)` / `configure_camera(camera_path, params)`

**`isaacsim.util.camera_inspector` has NO Python API** (confirmed GUI-only by rev1+rev2).

**Correct implementation:** Use `UsdGeom.Camera` directly or `isaacsim.sensors.camera.Camera`:

```python
from pxr import UsdGeom

cam = UsdGeom.Camera(stage.GetPrimAtPath(camera_path))
focal_length = cam.GetFocalLengthAttr().Get()
aperture_h = cam.GetHorizontalApertureAttr().Get()
clipping = cam.GetClippingRangeAttr().Get()

# Write:
cam.GetFocalLengthAttr().Set(new_focal_length)
```

### 8A.5 Sensor Migration

**Current code uses deprecated `omni.isaac.sensor`** (tool_executor.py lines 1493-1503).

| Old | New (5.1) | New (6.0) |
|-----|-----------|-----------|
| `omni.isaac.sensor.LidarRtx` | `isaacsim.sensors.rtx.LidarRtx` | Same |
| `omni.isaac.sensor.IMUSensor` | `isaacsim.sensors.physics.IMUSensor` | `isaacsim.sensors.experimental.physics.IMUSensor` |
| `omni.isaac.sensor.ContactSensor` | `isaacsim.sensors.physics.ContactSensor` | `isaacsim.sensors.experimental.physics.ContactSensor` |

**New sensors:**
- `ProximitySensor` — constructor takes `Usd.Prim` object (not string path)
- `LightbeamSensor` — NO Python class exists. Use `IsaacSensorCreateLightBeamSensor` command + USD attribute polling

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| GridCloner codegen | L0 | Verify `replicate_physics=True`, `filter_collisions` as separate call |
| debug_draw codegen | L0 | Only valid types: points, lines, lines_spline |
| occupancy_map param mapping | L0 | height_range → set_transform z-bounds |
| camera inspect/configure | L0 | UsdGeom.Camera attribute names |
| sensor import paths | L0 | Verify new namespace per Isaac Sim version |
