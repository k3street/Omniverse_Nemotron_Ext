# New Capability — Automatic Scene Simplification

**Type:** New tool set  
**Source:** Personas P08 (Alex), P09 (Fatima), P02 (Erik)  
**Research:** `rev2/research_scene_simplification.md`

---

## Overview

"This scene is too heavy, optimize it" → system identifies performance bottlenecks and suggests/applies simplifications. Combines performance diagnostics with automated fixes.

---

## Tools

### `optimize_scene(mode, target_fps)`

**Type:** CODE_GEN handler (generates batch of simplification patches)

**Modes:**
- `"analyze"` — report issues only, no changes
- `"conservative"` — apply safe optimizations (collision approximation, solver tuning)
- `"aggressive"` — apply all optimizations including mesh simplification

**Implementation — optimization pipeline:**

```python
# Step 1: Identify bottlenecks (reuse diagnose_performance)
perf = await diagnose_performance()

# Step 2: Generate optimization patches
patches = []

# 2a. Collision mesh simplification (biggest lever)
for prim_info in find_heavy_prims(threshold=10000):
    if prim_info["is_static"]:
        # Static objects → convex hull (fastest, safe)
        patches.append(f"UsdPhysics.MeshCollisionAPI(stage.GetPrimAtPath('{prim_info['path']}')).GetApproximationAttr().Set('convexHull')")
    else:
        # Dynamic objects → convex decomposition (preserves concavity)
        patches.append(f"...Set('convexDecomposition')")

# 2b. Solver iteration reduction
for art_path in find_over_iterated_articulations(threshold=16):
    patches.append(f"PhysxSchema.PhysxArticulationAPI(prim).GetSolverPositionIterationCountAttr().Set(4)")

# 2c. GPU physics (if CPU-bound)
if perf["physics_on_cpu"] and perf["gpu_available"]:
    patches.append("PhysicsContext.enable_gpu_dynamics(True)")
    patches.append("PhysicsContext.set_broadphase_type('GPU')")

# 2d. Disable CCD where not needed
for body in find_bodies_with_ccd():
    if not body["needs_ccd"]:  # slow-moving, large objects don't need CCD
        patches.append(f"PhysxSchema.PhysxRigidBodyAPI(prim).GetEnableCCDAttr().Set(False)")

# 2e. Lock static joints (articulation simplification)
for joint in find_never_moving_joints():
    patches.append(f"# Convert {joint} to FixedJoint")
```

**Returns:**
```json
{
  "current_fps": 12,
  "estimated_fps_after": 45,
  "optimizations": [
    {"type": "collision_simplify", "count": 3, "impact": "high"},
    {"type": "solver_reduction", "count": 2, "impact": "medium"},
    {"type": "gpu_physics", "impact": "high"},
    {"type": "ccd_disable", "count": 5, "impact": "low"}
  ],
  "patches": [...]
}
```

### `simplify_collision(prim_path, approximation)`

**Type:** CODE_GEN handler — single prim collision simplification.

**Approximation options (from MeshCollisionAPI):**
| Mode | Speed | Fidelity | Use when |
|------|-------|----------|----------|
| `convexHull` | Fastest | Low (loses concavity) | Static background objects |
| `convexDecomposition` | Fast | Medium | Dynamic objects that need rough shape |
| `meshSimplification` | Medium | High | Objects where shape matters |
| `boundingSphere` | Fastest | Lowest | Far-away objects, triggers only |
| `sdf` | Slow | Highest | Deformable body interaction |

### `suggest_physics_settings(scene_type)`

**Type:** DATA handler

Given a scene type, suggest optimal physics settings:

| Scene Type | Solver | Iterations | GPU | CCD | dt |
|-----------|--------|-----------|-----|-----|-----|
| RL training (1024 envs) | TGS | 4 | Yes | No | 1/120 |
| Manipulation (precision) | TGS | 16 | Optional | Yes (gripper) | 1/240 |
| Mobile robot (navigation) | TGS | 4 | Yes | No | 1/60 |
| Digital twin (visualization) | PGS | 4 | No | No | 1/60 |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Optimization pipeline logic | L0 | Mock perf data → correct patches generated |
| Approximation selection | L0 | Static vs dynamic → correct approximation |
| Physics settings lookup | L0 | Scene type → correct settings |
| FPS estimation | L0 | Known bottleneck → reasonable estimate |
| Full optimization | L3 | Requires Kit + heavy scene |
