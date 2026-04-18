# Collision Mesh Quality Addendum

**Enhances:** Phase 3 (Import), Phase 8A (Sensors/Collision), Auto-Simplification  
**Source:** Personas P01 (Maya), P02 (Erik), P08 (Alex) — "weird physics behavior"  
**Research:** `rev2/research_collision_mesh_quality.md`

---

## Overview

Bad collision meshes are the #1 cause of "the robot does something weird." These tools detect and fix mesh issues before they reach PhysX.

---

## Tools

### `check_collision_mesh(prim_path)`

**Type:** DATA handler (Kit RPC + trimesh analysis)

**Checks (two tiers):**

**Fatal (blocks simulation):**
- Out-of-range vertex indices
- Zero-area / degenerate triangles
- Convex hull exceeding 255 polygons (PhysX limit) or 64 vertices (GPU limit)
- No `CollisionAPI` on prim that needs it

**Silent degradation (causes weird behavior):**
- Non-manifold edges → ghost contacts, tunneling
- Inverted normals → explosive forces (SDF sign confusion)
- Self-intersections → jitter on first contact
- Non-watertight mesh → PhysX closes holes arbitrarily
- Oversized triangles → SDF/CCT instability

**Implementation:**
```python
# Extract mesh data from USD
mesh = UsdGeom.Mesh(prim)
points = mesh.GetPointsAttr().Get()
face_counts = mesh.GetFaceVertexCountsAttr().Get()
face_indices = mesh.GetFaceVertexIndicesAttr().Get()

# Convert to trimesh for quality analysis
import trimesh
tm = trimesh.Trimesh(vertices=points, faces=triangulate(face_counts, face_indices))

issues = []
if not tm.is_watertight:
    issues.append({"type": "non_watertight", "severity": "warning"})
if not tm.is_volume:
    issues.append({"type": "not_volume", "severity": "warning"})
if len(tm.faces) == 0 or any(tm.area_faces < 1e-10):
    issues.append({"type": "degenerate_faces", "severity": "error", "count": ...})

# Check PhysX GPU limits
if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
    approx = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
    if approx == "convexHull":
        hull = trimesh.convex.convex_hull(tm)
        if len(hull.vertices) > 64:
            issues.append({"type": "hull_exceeds_gpu_limit", "severity": "error",
                          "vertices": len(hull.vertices), "limit": 64})
```

**Returns:**
```json
{
  "prim": "/World/Robot/link3",
  "triangle_count": 45000,
  "is_watertight": false,
  "is_manifold": false,
  "degenerate_faces": 12,
  "collision_approximation": "none (triangle mesh)",
  "issues": [...],
  "recommendation": "Switch to convexDecomposition (45K triangles is too heavy for physics). Run fix_collision_mesh first to repair non-manifold edges."
}
```

### `fix_collision_mesh(prim_path, target_triangles)`

**Type:** CODE_GEN handler

**Auto-repair sequence (from research):**
1. Fix normals (`trimesh.fix_normals()`)
2. Remove degenerate triangles (area < threshold)
3. Fill holes (`trimesh.fill_holes()`, pymeshfix for complex cases)
4. Simplify to target face count (`trimesh.simplify_quadric_decimation()`)
5. Convex decompose if needed (CoACD, `threshold=0.05`, `max_convex_hull=16`)
6. Verify all hulls ≤ 64 vertices
7. Write back to USD

**Triangle count guidelines:**
| Object Type | Target | Approximation |
|-------------|--------|---------------|
| Dynamic rigid body (robot part) | 100-500 | convexDecomposition |
| Static environment (wall, table) | 1000-5000 | convexHull or meshSimplification |
| Non-interacted background | 0 | boundingCube or no collision |

### `visualize_collision_mesh(prim_path)`

Show collision mesh as wireframe overlay in viewport (distinct from visual mesh). Helps users see what PhysX actually uses for collision.

**Implementation:** `omni.physx.ui` Physics Debug visualization → enable `Collision Shapes` programmatically.

---

## Integration Points

- **Phase 3 (Import):** Run `check_collision_mesh` automatically after `import_robot` (URDF post-processor addendum)
- **Auto-Simplification:** `fix_collision_mesh` is called by `optimize_scene` for heavy collision meshes
- **Performance Diagnostics:** `find_heavy_prims` already identifies high-triangle meshes — this adds the fix

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Watertight check | L0 | Known mesh → correct boolean |
| Degenerate face detection | L0 | Mesh with zero-area face → found |
| GPU vertex limit check | L0 | Known hull → correct limit violation |
| Repair sequence | L0 | Known broken mesh → repaired trimesh |
| Triangle count guideline | L0 | Object type → correct recommendation |
| Full mesh analysis | L3 | Requires Kit + USD stage |
