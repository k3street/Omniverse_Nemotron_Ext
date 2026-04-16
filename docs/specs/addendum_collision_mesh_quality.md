# Addendum — Collision Mesh Quality

**For:** Sessions that import meshes and add physics colliders (URDF imports,
manipulation scenes, Nucleus props). The PhysX collider you ship determines
whether contacts are stable, whether the GPU narrowphase fits in budget, and
whether the robot can actually grasp the object.
**Priority:** Add alongside `apply_api_schema` / `import_robot` — these are L0
analyzers that flag bad colliders BEFORE the user notices a 30-fps drop or a
penetration glitch.
**Effort:** Small — five tool handlers, no new Kit RPC endpoint.

---

## Motivation

Three classes of failure dominate "my physics is wrong" tickets and they all
trace back to the same root cause: the wrong `collisionApproximation` was
applied to a render mesh that was never reviewed for collider-suitability.

1. **`convexHull` on concave geometry** — the user wraps a cup in a hull,
   tries to pour in a ball, and the ball can't enter. There's no warning;
   the visual mesh hides the hull.
2. **`triangleMesh` on dynamic bodies** — PhysX silently demotes to CPU
   contact processing or refuses the simulation entirely. Users see "no
   physics happening" with no clear error.
3. **Million-triangle render meshes used as colliders** — narrowphase blows
   the per-frame budget, framerate halves, and there's no UI breadcrumb
   pointing at the offender.

All five new tools are pure data / code-gen — no Kit RPC mutation, no
subprocess, no network. They sit in front of `apply_api_schema` the same way
the Phase 7C addendum sits in front of `start_teleop_session`.

---

## Tools

### CMQ.1 `analyze_collision_mesh(prim_path, max_triangles)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Issue a Kit RPC `/exec` (via the existing `kit_tools.queue_exec_patch`)
   that, for the given mesh prim, reads `points` and `faceVertexIndices`,
   counts triangles, computes axis-aligned bounding-box volume, and infers
   convexity using the signed-volume-vs-hull-volume ratio.
2. If `max_triangles` is provided (default 5000), flag the mesh as
   `over_budget=True` when triangle count exceeds it.
3. Return a structured report with the metrics plus a `recommended_approximation`
   string: `convexHull` for convex, `convexDecomposition` for concave with
   ≤ 50k triangles, `sdf` for high-detail concave, `none` for "skip
   collision entirely".

**Returns:**
```python
{
    "prim_path": "/World/Cup",
    "triangle_count": 12834,
    "vertex_count": 6450,
    "bbox_volume_m3": 0.0008,
    "convexity_ratio": 0.42,         # 1.0 = perfect convex
    "is_convex": False,
    "over_budget": True,
    "recommended_approximation": "convexDecomposition",
    "rationale": "Concave shape (ratio 0.42) over budget — decompose to N hulls.",
}
```

**Why DATA:** the LLM uses this to *decide* which collision approximation to
apply via a follow-up `apply_collision_approximation`. The result must come
back in-context.

### CMQ.2 `validate_collision_setup(prim_path)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Walk the prim subtree via Kit RPC `/exec` and collect every prim that
   has `PhysicsCollisionAPI` applied.
2. For each collider, check the four most common misconfigurations:
   - `triangleMesh` approximation on a prim that also has
     `RigidBodyAPI` (illegal for dynamic bodies).
   - `convexHull` applied to a prim whose render mesh is highly concave
     (signal: convexity ratio < 0.7, derived inline from points).
   - Missing `physics:approximation` attribute (PhysX defaults to
     `convexHull` silently).
   - Unit mismatch — collider scaled by xform but `meshSimplification` not
     set, so triangle count is preserved at the wrong density.
3. Return per-collider issues with severity (`error` / `warning` / `info`).

**Returns:**
```python
{
    "prim_path": "/World/Robot",
    "colliders_checked": 7,
    "issues": [
        {
            "prim": "/World/Robot/gripper_finger_L",
            "severity": "error",
            "kind": "triangle_mesh_on_dynamic",
            "message": "triangleMesh approximation on dynamic body — PhysX will reject contacts.",
        },
        {
            "prim": "/World/Robot/forearm_visual",
            "severity": "warning",
            "kind": "convex_hull_on_concave",
            "message": "convexHull on concave mesh (ratio 0.41) — graspable cavities will be filled.",
        },
    ],
    "ready_for_simulation": False,
}
```

**Why DATA:** mirrors `validate_teleop_demo` from the 7C addendum — the LLM
folds the report into a user-facing "your gripper finger needs a different
collider" message and may auto-trigger CMQ.4 to fix it.

### CMQ.3 `suggest_collision_approximation(prim_path, intent)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Run the same convexity / triangle-count analysis as CMQ.1 but layer the
   user's `intent` on top: `static_environment` (favor `triangleMesh`),
   `dynamic_object` (favor `convexHull` or `convexDecomposition`),
   `graspable` (force `convexDecomposition` even at small sizes),
   `sensor_only` (return `none` — visual only).
2. Cap `convexDecomposition` hull count by triangle budget: small mesh
   (< 1k tri) → max 4 hulls, medium → max 16, large → max 64.
3. Return the recommendation plus the exact attribute payload the caller
   should pass to `apply_collision_approximation`.

**Returns:**
```python
{
    "prim_path": "/World/Cup",
    "intent": "graspable",
    "approximation": "convexDecomposition",
    "params": {
        "physxConvexDecompositionCollision:maxConvexHulls": 16,
        "physxConvexDecompositionCollision:voxelResolution": 500000,
        "physxConvexDecompositionCollision:errorPercentage": 1.0,
    },
    "rationale": "Concave graspable object — 16 hulls capture cavities.",
}
```

**Why DATA:** purely a recommender. The LLM should be able to chain this
into `apply_collision_approximation` without the user re-confirming.

### CMQ.4 `apply_collision_approximation(prim_path, approximation, params)`

**Type:** CODE_GEN handler (returns a Python script Kit will queue).

**Output:** A standalone script that, when executed inside Kit:

1. Imports `pxr.UsdPhysics`, `pxr.PhysxSchema`, `omni.usd`.
2. Resolves the prim, applies `PhysicsCollisionAPI` if absent, then sets
   `physics:approximation` to the requested string from this allowed
   set: `convexHull`, `convexDecomposition`, `triangleMesh`, `boundingCube`,
   `boundingSphere`, `meshSimplification`, `sdf`, `none`.
3. For `convexDecomposition`, applies `PhysxConvexDecompositionCollisionAPI`
   and writes every key from `params` whose attribute exists.
4. For `sdf`, applies `PhysxSDFMeshCollisionAPI` and sets
   `physxSDFMeshCollision:sdfResolution` if present in `params`.
5. Prints `[collision] applied <approximation> on <prim_path>` so the
   tool-result loop has confirmation.

**Why CODE_GEN:** mutating USD attributes on user prims must go through
Kit's patch-approval pipeline — that's what `tool_executor.py` already does
for `apply_api_schema`. The LLM should never write USD directly.

### CMQ.5 `generate_collision_audit_script(scope_path, output_path)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A runnable script the user can paste into the Script Editor or
queue via patch approval. The script:

1. Walks every prim under `scope_path` (or `/World` if omitted).
2. For each prim with `PhysicsCollisionAPI`, records: prim path,
   approximation, triangle count, has-RigidBodyAPI, convexity ratio.
3. Writes a JSON report to `output_path` (default
   `workspace/collision_audits/<timestamp>.json`).
4. Prints `[audit] wrote N entries to <output_path>` on completion.

**Why CODE_GEN:** the audit is a read-only sweep but it produces a
user-owned file the user can diff between sessions and check into git.
That's exactly the boundary code-gen is meant for.

---

## Code patterns

- `analyze_collision_mesh` and `validate_collision_setup` build a small Kit
  patch via `kit_tools.queue_exec_patch` — the patch reads USD, computes
  metrics, and `print(json.dumps(...))`. The handler awaits the patch and
  parses the printed payload, mirroring `_handle_get_articulation_state`.
- `suggest_collision_approximation` is pure-Python — it calls
  `analyze_collision_mesh` internally, then layers intent rules. No Kit
  round-trip beyond the analyzer.
- `apply_collision_approximation` and `generate_collision_audit_script`
  follow the existing code-gen pattern (`_gen_*` returning `str` of Python
  source). Use `repr()` for user-supplied path / string literals to avoid
  injection.
- Module-level constants: `_VALID_APPROXIMATIONS = {...}` and
  `_INTENT_DEFAULTS = {...}` near the top of the new section, mirroring
  `SCHEMA_MAP` in `_gen_apply_api_schema`.
- Register under `DATA_HANDLERS` / `CODE_GEN_HANDLERS` at the end of
  `tool_executor.py`, mirroring the layout of every other addendum.

---

## Schemas (tool_schemas.py)

Five entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Collision Mesh Quality Addendum ─────────────────────────────────────
```

All five are `type: function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                       | Level | What                                                      |
|-----------------------------------------------------------|-------|-----------------------------------------------------------|
| schema registration — all 5 in ISAAC_SIM_TOOLS            | L0    | Names appear in schema list                               |
| handler registration — all 5 in DATA / CODE_GEN           | L0    | Names dispatch to a handler                               |
| `analyze_collision_mesh` — mock Kit response              | L0    | Returns recommended_approximation field                   |
| `analyze_collision_mesh` — over-budget flagged            | L0    | High triangle count flags `over_budget=True`              |
| `validate_collision_setup` — clean mesh                   | L0    | Returns `ready_for_simulation=True`, no errors            |
| `validate_collision_setup` — triangle_mesh on dynamic     | L0    | Flags `triangle_mesh_on_dynamic` error                    |
| `suggest_collision_approximation` — graspable intent      | L0    | Returns `convexDecomposition` with hull-count param       |
| `suggest_collision_approximation` — sensor_only intent    | L0    | Returns `none`                                            |
| `suggest_collision_approximation` — static_environment    | L0    | Returns `triangleMesh` for large concave meshes           |
| `apply_collision_approximation` — compiles                | L0    | `compile()` success + references prim_path                |
| `apply_collision_approximation` — convexDecomposition     | L0    | Generated code applies decomposition API + params         |
| `apply_collision_approximation` — invalid approximation   | L0    | Falls back to `convexHull` (safe default)                 |
| `apply_collision_approximation` — path injection safe     | L0    | `repr()` used, path with quote doesn't break syntax       |
| `generate_collision_audit_script` — compiles              | L0    | `compile()` success + writes to `output_path`             |
| `generate_collision_audit_script` — default scope         | L0    | Defaults to `/World` when scope_path missing              |

All fifteen tests are L0 — no Kit, no GPU, no PhysX runtime.

---

## Known Limitations

- `analyze_collision_mesh` infers convexity from the signed-volume / hull-volume
  ratio; this is fast but can mis-classify thin shells (cloth-like meshes).
  A Phase 8 follow-up could use V-HACD or a proper concavity metric.
- `validate_collision_setup` only catches the four most common
  misconfigurations; per-asset rules (e.g. specific robot finger meshes
  must be `sdf`) belong in a higher-level profile system.
- `apply_collision_approximation` cannot bake a baked-down mesh — it only
  toggles approximation flags. For permanent mesh-decimation, the user
  still needs the Mesh Decimation extension in the Script Editor.
