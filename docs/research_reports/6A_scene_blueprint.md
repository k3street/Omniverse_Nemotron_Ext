# Phase 6A — Scene Blueprint Builder: Critique & Improvement Report

**Agent:** Research 6A Scene Blueprint  
**Date:** 2026-04-15  
**Status:** Complete

## Executive Summary

Phase 6A has a solid conceptual pipeline that mirrors the state of the art in NL-to-3D scene generation, but it skips past the hardest implementation problems: the LLM spatial planner will hallucinate coordinates; the asset catalog API it depends on does not exist as a Python-callable search function; the QA validator has no ground truth for dimensions; and thumbnail rendering from a FastAPI process is architecturally impossible. Each of these is a silent failure mode — the system will appear to work but produce physically wrong scenes.

---

## 1. The `catalog_search` API (6A.1) — The Referenced API Does Not Exist

**The spec says:** search `ASSETS_ROOT_PATH` recursively and also search via "Nucleus browser." It implies a clean catalog-query API.

**Reality:**
- `isaacsim.asset.browser` has **no public Python search API**. The official docs confirm it is a GUI-only widget. The only settings-level Python access is `carb.settings.get_settings().get("/exts/isaacsim.asset.browser/SETTING_NAME")` — not asset querying.
- The asset browser cache lives at `<isaac_install>/exts/isaacsim.asset.browser/cache/isaacsim.asset.browser.cache.json` (confirmed by GitHub issues #290 and #127 on isaac-sim/IsaacSim). This file exists but has no documented schema, and two separate GitHub issues report it frequently missing.
- The real catalog API in `isaacsim.storage.native` provides `list_folder(path)` and `recursive_list_folder(path)`, both wrapping `omni.client.list()`. These return raw file entries with `relative_path`, not semantic metadata.

**Concrete implementation path:**
```python
# The actual discoverable API — use this, not a phantom "catalog_search"
from isaacsim.storage.native import recursive_list_folder, get_assets_root_path
import omni.client

assets_root = get_assets_root_path()   # e.g. "omniverse://localhost/NVIDIA/Assets/Isaac/5.1"
# Known structure: Isaac/Robots/, Isaac/Environments/, Isaac/Props/, Isaac/Sensors/
result, entries = omni.client.list(assets_root + "/Isaac/Robots")
for e in entries:
    # e.relative_path, e.flags (file vs folder)
```

**Risk:** `recursive_list_folder` on a remote Nucleus server can be slow (thousands of files). The spec mentions caching the directory listing — this is correct and essential, but the spec doesn't specify what the cache format should be or how it gets invalidated.

**Fix for 6A.1:**
- Build a one-time index crawler that walks `get_assets_root_path() + "/Isaac/{Robots,Environments,Props}"` plus `ASSETS_ROOT_PATH` using `omni.client.list()`.
- Store results as a local JSONL at `workspace/knowledge/asset_index.jsonl` with fields: `{path, name, type, last_modified, size_bytes, tags: []}`.
- Add fuzzy search over name using `rapidfuzz` (already commonly available in Python environments), not a full embedding pipeline for MVP.
- For Nucleus, check `isaacsim.storage.native.check_server()` before attempting any listing — Nucleus is frequently not running in offline/local setups.
- Do not call `isaacsim.asset.browser` internals at all; it is an unstable beta with known cache corruption issues.

---

## 2. `generate_scene_blueprint` (6A.2) — Fundamental Spatial Reasoning Problem

**The spec says:** LLM generates a spatial layout plan with positions like `[2, 1, 0]`.

**What the research says (verified 2024-2026):**

LLMs have a well-documented catastrophic failure mode in exact 3D coordinate generation. LayoutGPT (NeurIPS 2023) showed that raw LLM coordinate generation produces floating objects, impossible overlaps, and wildly incorrect scale (2m tall chairs, robot inside a wall) even with careful prompting. The 2025 survey found models deteriorate 42–80% as scene complexity grows and never exceed 50% accuracy on global spatial integration tasks.

The state of the art (Holodeck CVPR 2024, SceneCraft 2024, RoomPlanner 2025, LayoutVLM CVPR 2025, SceneSmith Feb 2026) **does not ask the LLM for coordinates directly.** The universally converged pattern is:

1. LLM generates **spatial constraints** (symbolic): `{table: near_wall, robot: 1m_clearance_all_sides, sink: adjacent_to_counter}`
2. A **deterministic constraint solver** converts constraints to coordinates (DFS, LP, or gradient-based)
3. A **physics validator** runs collision checks post-placement

The spec's blueprint JSON with hardcoded position arrays is step 3 attempted at step 1. The LLM has no chance of getting `[2, 1, 0]` correct for even a 3-object scene.

**Fix for 6A.2:**

Replace the one-shot coordinate-generation prompt with a two-stage approach:

**Stage A — Constraint generation (LLM):**
```json
{
  "room": {"width": 6, "depth": 4, "height": 3},
  "constraints": [
    {"object": "table", "relation": "against_wall", "wall": "north"},
    {"object": "sink", "relation": "adjacent", "reference": "table", "side": "left"},
    {"object": "robot", "relation": "clearance", "meters": 1.0, "from": "all_objects"},
    {"object": "items", "relation": "on_surface", "surface": "table"}
  ]
}
```

**Stage B — Constraint solver (deterministic Python):**
Write a simple 2D packing solver (a 400-line Python class) that:
- Looks up bounding box of each asset using `UsdGeomBBoxCache` (called in Kit via RPC after loading the USD as a reference)
- Places objects sequentially, checking AABB overlaps via `ComputeAlignedBox()`
- Enforces semantic rules (on-surface = `z = surface_top`, against-wall = `x = wall_coord + bbox_half_x`)
- Uses `isaacsim.asset.gen.omap.MapGenerator` to get the 2D walkable area for robot placement

This is exactly the architecture in Holodeck 1.0 (open source: `github.com/allenai/Holodeck`), SceneCraft, and RoomPlanner.

**Missing from spec:** No mention of unit system. Isaac Sim uses centimeters by default in USD (1 unit = 1 cm), but the spec shows positions like `[2, 1, 0]` without units.

---

## 3. `validate_scene_blueprint` (6A.3) — No Bounding Box Source for Pre-Build Validation

At blueprint validation time, the USD assets have not been loaded into the stage yet. `UsdGeomBBoxCache.ComputeWorldBound()` requires a live USD stage with the prim present.

**Fix for 6A.3:**

Build a separate **pre-flight dimension cache** step:
- When `catalog_search` indexes an asset, also load it into a temporary headless stage, compute its `UsdGeomBBoxCache`, record `{min_x,min_y,min_z,max_x,max_y,max_z}` in the `asset_index.jsonl`.
- The validator then queries the cached extents, never needing a live stage.

**Also missing:** The validator checks `floating objects` but never defines the ground plane coordinate.

---

## 4. `build_scene_from_blueprint` (6A.4) — Dry-Run Semantics Are Broken

If `dry_run=false` and the build fails on object 8 of 15, there's no transactional rollback. The user will click "Build All" and then click approve 15 times.

**Fix for 6A.4:**
- Add a `batch_snapshot` concept to `snapshots/manager.py`
- Dry-run should output one viewable script, not 15 individual patches
- Asset path resolution must happen during `generate_scene_blueprint`, not during `build`

---

## 5. 6A.5 Blueprint Preview Card — Thumbnail Rendering Is Architecturally Wrong

Rendering requires an RTX render context. The FastAPI service is a plain Python process with no GPU/RTX context. Nucleus already maintains thumbnails for USD files if the thumbnail service is running.

**Fix for 6A.7:**
- Add a Kit RPC endpoint `GET /asset_thumb?path=<nucleus_path>` using `omni.kit.thumbnails.usd`
- Fall back to placeholder icons for local files without Nucleus thumbnails
- Do not attempt to render from the FastAPI process

---

## 6. 6A.6 Iterative Refinement — Blueprint State Management Is Underspecified

- Where is blueprint state stored?
- "Move the table 1m left" — left relative to what?
- Undo per blueprint step: the current snapshot manager returns to most recent only

**Fix for 6A.6:**
- Store blueprint state as session-scoped JSON
- Express all refinement instructions as **constraint updates**, not coordinate deltas
- Add `blueprint_step_index` to snapshots

---

## 7. Missing: Coordinate Frame and Up-Axis Convention

Isaac Sim defaults to Y-up for USD, but many imported robots use Z-up. The spec makes no mention of this.

---

## 8. Scale Mismatch Is Deeper Than the Spec Acknowledges

USD assets have no guaranteed unit convention. `metersPerUnit=0.01` vs `1.0` → robot 100x smaller than the table. Isaac Sim does NOT automatically remap units on `add_reference`.

---

## 9. Existing Open-Source Tools That Should Be Leveraged

| Project | What to Steal | License |
|---|---|---|
| **Holodeck** (`github.com/allenai/Holodeck`) | Constraint-based layout solver; Objaverse asset retrieval | Apache-2.0 |
| **SceneSmith** (`github.com/nepfaff/scenesmith`) | VLM critic/designer loop; V-HACD convex decomposition | MIT |
| **LayoutVLM** (`github.com/sunfanyunn/LayoutVLM`) | Differentiable spatial constraint optimizer | Apache-2.0 |
| **GenManip** (`github.com/InternRobotics/GenManip`) | Task-oriented scene graph on Isaac Sim (CVPR 2025) | Apache-2.0 |

---

## Sources
- [SceneTeller: Language-to-3D Scene Generation](https://arxiv.org/html/2407.20727v1)
- [3D Scene Generation: A Survey (arXiv 2505.05474)](https://arxiv.org/abs/2505.05474)
- [LayoutGPT](https://layoutgpt.github.io/)
- [LayoutVLM (CVPR 2025)](https://github.com/sunfanyunn/LayoutVLM)
- [Holodeck (CVPR 2024)](https://github.com/allenai/Holodeck)
- [SceneSmith](https://github.com/nepfaff/scenesmith)
- [GenManip (CVPR 2025)](https://github.com/InternRobotics/GenManip)
- [isaacsim.storage.native API](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/source/extensions/isaacsim.storage.native/docs/api.html)
