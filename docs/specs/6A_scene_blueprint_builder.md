# Phase 6A — Scene Blueprint Builder (NL → Full Scene)

**Status:** Partially implemented (tool_executor.py, Apr 14 commit)  
**Depends on:** Phase 1A (USD executor), Phase 3 (import_robot), Phase 4D (scene_summary)  
**Research:** `research_reports/6A_scene_blueprint.md`, `rev2/defend_6A_coordinates.md`

---

## Overview

User describes a scene in natural language → system generates a spatial blueprint → resolves assets → places everything with physically correct positioning → user iterates.

**Key research finding:** DirectLayout (2025) and SceneSmith (2026) demonstrate that LLMs CAN generate coordinates directly with proper chain-of-thought prompting, without a constraint solver. The validate+auto-fix layer (6A.3) handles post-placement correction.

---

## Review of Existing Implementation

The other session implemented `catalog_search`, `generate_scene_blueprint`, `validate_scene_blueprint`, `build_scene_from_blueprint` on Apr 14. The following corrections apply based on research:

### 6A.1 `catalog_search` — Asset Discovery

**API correction:** `isaacsim.asset.browser` has no Python search API (GUI-only, confirmed rev1+rev2). The implementation must use:

```python
from isaacsim.storage.native import recursive_list_folder, get_assets_root_path
import omni.client

result, entries = omni.client.list(assets_root + "/Isaac/Robots")
```

**Implementation checklist:**
- [ ] Verify implementation uses `omni.client.list()` or `isaacsim.storage.native`, NOT `isaacsim.asset.browser`
- [ ] Local JSONL cache at `workspace/knowledge/asset_index.jsonl` with bounding box data
- [ ] Fuzzy search via `rapidfuzz` or similar
- [ ] Check `isaacsim.storage.native.check_server()` before Nucleus queries
- [ ] Cache invalidation strategy (timestamp-based or manual refresh)

### 6A.2 `generate_scene_blueprint` — Spatial Planning

**Prompt design (critical):** Structure the LLM call as sequential chain-of-thought:
1. Anchor surfaces and room boundaries first
2. Large fixed objects (walls, floor, table)
3. Surface-placed items (objects ON tables, shelves)
4. Robot with clearance requirements

**Implementation checklist:**
- [ ] Explicit room dimensions and unit convention in prompt (meters, Z-up)
- [ ] Asset bounding boxes from cache included in prompt context
- [ ] `UsdGeom.GetStageMetersPerUnit()` check — normalize all values to meters
- [ ] `UsdGeom.GetStageUpAxis()` check — normalize to Z-up

### 6A.3 `validate_scene_blueprint` — Pre-Build QA

**Bounding box source:** Assets not yet loaded at validation time. Need pre-computed extents.

**Implementation checklist:**
- [ ] Pre-flight dimension cache: when indexing assets, compute `UsdGeomBBoxCache` extents via Kit RPC
- [ ] AABB overlap detection between all object pairs
- [ ] Ground-plane detection (not just z=0 — check for physics ground prim)
- [ ] `metersPerUnit` mismatch detection: `asset_metersPerUnit / stage_metersPerUnit != 1.0` → auto-scale warning
- [ ] Up-axis mismatch detection per asset

### 6A.4 `build_scene_from_blueprint` — Execution

**Implementation checklist:**
- [ ] Batch snapshot before first patch (single rollback point for entire blueprint)
- [ ] "Build All" = one governance approval for the batch, not per-object
- [ ] Asset path resolution at blueprint-generation time, not build time
- [ ] `dry_run=true` returns concatenated code, not individual patches

### 6A.5 Blueprint Preview Card

**Thumbnail source:** Do NOT render from FastAPI. Use Kit RPC endpoint or Nucleus sidecar thumbnails.

- [ ] Kit RPC `GET /asset_thumb?path=<usd_path>` using `omni.kit.thumbnails.usd`
- [ ] Fallback: placeholder icons by asset type

### 6A.6 Iterative Refinement

- [ ] Blueprint state stored as session-scoped JSON: `workspace/sessions/<session_id>/blueprint.json`
- [ ] Delta changes re-validate only affected objects
- [ ] Snapshot manager needs `blueprint_step_index` for per-step undo

### 6A.7 Asset Thumbnail Indexer

Potentially redundant if Nucleus thumbnail service is running. Verify before implementing.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| catalog_search scoring | L0 | Fuzzy match ordering, type filtering, empty results |
| validate_scene_blueprint | L0 | Overlap, floating, scale mismatch, empty blueprint — **currently zero tests** |
| build_scene_from_blueprint codegen | L0 | compile() + substring, multi-object, dry_run, rotation |
| generate_scene_blueprint data handler | L0 | Template selection, asset injection |
| Asset index via Kit RPC | L1 | mock_kit_rpc returns fake asset list |
| Full pipeline | L3 | Requires Kit + real assets |

---

## Known Limitations

- LLM coordinate generation degrades above ~15 objects (per DirectLayout benchmarks)
- No multi-room/multi-floor support in blueprint schema
- Articulated object clearance (drawer opening space) not checked by validator
