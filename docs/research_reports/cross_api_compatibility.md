# Cross-Cutting: Isaac Sim API Compatibility Review

**Agent:** Isaac Sim API Changes  
**Date:** 2026-04-15  
**Status:** Complete

## Version Map

| Isaac Sim | Kit SDK | Python | Key Shift |
|---|---|---|---|
| 4.5 | 107.x | 3.10 | Mass rename `omni.isaac.*` → `isaacsim.*` |
| 5.1 | 107.3.3 | 3.10 | Stable |
| 6.0 | 109→110 | **3.12** | Breaking removals; ROS2 split; sensor experimental |

## Blocking Issues for 6.0

| Issue | File | Severity |
|---|---|---|
| `omni.isaac.sensor` (LidarRtx/IMU/Contact) | Extension + codegen | **BLOCKING** |
| `omni.isaac.urdf` import | tool_executor.py | **BLOCKING** |
| `isaacsim.ros2.bridge.*` OG node types → `isaacsim.ros2.nodes.*` | tool_executor.py | **BLOCKING** |
| `isaacsim.sensors.physics` deprecated | Sensor code | WARNING |
| Missing `omni.kit.pipapi` in 6.0 toml | extension.toml | Breaks pip |
| 6.0 `extension.py` is non-functional stub | exts/isaac_6.0/ | Incomplete |
| `enable_residual_reporting` removed | Phase 2 tools | Hard crash |
| `ApplyReferencePointAPI` → `ApplySiteAPI` | patch_validator | Silent fail |

## Stable Across All Versions

- `pxr.*` (Usd, UsdGeom, UsdPhysics, UsdShade, PhysxSchema, Gf, Vt, Sdf)
- `omni.usd`, `omni.timeline`, `carb.*`
- `isaacsim.core.cloner.GridCloner`
- `isaacsim.core.prims.SingleArticulation`
- `og.GraphBackingType` fallback (already handled)

## Dual-Version Pattern

```python
import isaacsim
_ISAAC_VERSION = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])

if _ISAAC_VERSION >= (6, 0):
    _ROS2_NS = "isaacsim.ros2.nodes"
else:
    _ROS2_NS = "isaacsim.ros2.bridge"
```

## Sources
- [Isaac Sim 6.0 Release Notes](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/overview/release_notes.html)
- [Extension Renaming Guide](https://docs.isaacsim.omniverse.nvidia.com/latest/overview/extensions_renaming.html)
- [Isaac Lab Migration Guide](https://isaac-sim.github.io/IsaacLab/main/source/refs/migration.html)
