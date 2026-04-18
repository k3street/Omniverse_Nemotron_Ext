# Cross-Cutting: Testing Strategy

**Agent:** Testing Strategy  
**Date:** 2026-04-15  
**Status:** Complete

## Three Clean Mock Boundaries

1. **Code generator output** — `compile()` + substring (L0, zero deps)
2. **Data handler pure logic** — monkeypatch internal caches (L0)
3. **Kit RPC `_get`/`_post`** — `mock_kit_rpc` fixture (L1)

## Priority Gaps to Fill Now

1. **`validate_scene_blueprint` — zero tests** (pure Python, several branches)
2. **`create_isaaclab_env` — zero tests** (template selection, reward override)
3. **`build_scene_from_blueprint` — 1 test vector** (multi-object, dry_run, rotation uncovered)
4. **`catalog_search` scoring** — missing sort ordering, type filtering
5. **`launch_training` algo mapping** — untested sac→skrl, rsl_rl mappings

## What Cannot Be Tested Automatically

Motion planning quality, RL convergence, Cortex behaviors, occupancy map accuracy, image-to-3D mesh quality, URDF import fidelity, ROS2 topic round-trip, gain tuner convergence.

## CI Pipeline

- **Every PR:** `pytest -m l0` (< 30s)
- **Nightly:** `pytest -m "l0 or l1"` (~2 min)
- **MCP:** `pytest -m "l0 or l1 or l2"`
- **Integration:** `pytest -m l3` (requires Kit, manual)

## Key Recommendation

Add `TestAllDataHandlersCovered` class (parallel to existing `TestAllCodeGenHandlersCovered`).
