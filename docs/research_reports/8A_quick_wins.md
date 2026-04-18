# Phase 8A — Quick Wins: Cloner, Debug Draw, Omap, Camera, Sensors: Critique

**Agent:** Research 8A Quick Wins  
**Date:** 2026-04-15  
**Status:** Complete

## API Corrections

| Item | Spec Says | Reality |
|---|---|---|
| Cloner `collision_filter` | Param in clone call | Separate `filter_collisions()` method with 3 required args |
| Cloner class | Unspecified | Should use `GridCloner` for RL grid layouts |
| Debug Draw types | spheres, arrows, boxes, text | Only `points`, `lines`, `lines_spline` exist |
| Debug Draw `lifetime` | Auto-clear | No lifetime API — manual clear only |
| Omap `height_range` | Named parameter | No such param; use `set_transform` z-bounds |
| Camera Inspector | `isaacsim.util.camera_inspector` | **UI-only widget, no Python API** |
| Sensors (6.0) | `isaacsim.sensors.physics` | Deprecated → `isaacsim.sensors.experimental.physics` |
| LightbeamSensor | Python class | No class; only USD creation command |
| ProximitySensor | `(prim_path, ...)` | Takes `Usd.Prim` object, not string |

## Performance Note

Occupancy map: synchronous PhysX ray-cast that blocks Kit main thread. 1000×1000 grid = several seconds.

## Sources
- [isaacsim.core.cloner](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.core.cloner/docs/index.html)
- [isaacsim.util.debug_draw](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.util.debug_draw/docs/index.html)
- [isaacsim.asset.gen.omap](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/digital_twin/ext_isaacsim_asset_generator_occupancy_map.html)
