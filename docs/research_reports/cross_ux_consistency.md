# Cross-Cutting: UX Consistency Review

**Agent:** UX Consistency  
**Date:** 2026-04-15  
**Status:** Complete

## Parameter Naming Inconsistency

`robot_path` / `articulation_path` / `prim_path` / `camera_path` all refer to USD prims. Must be unified.

## Three Different Approval UIs

1. Inline code card (good)
2. Separate modal for swarm patches (too much friction — 4 clicks)
3. Pipeline mode auto-executes silently

Risk level (low/medium/high) computed but never shown in UI.

## Magic Prefix Routing

`pipeline:` and `patch`/`fix` prefixes trigger different execution paths. Undiscoverable. "fix the camera color" triggers 1-3 min swarm instead of quick LLM reply. Replace with explicit slash commands.

## File Upload Issues (6B)

- 3D files sent as local filepath — FastAPI may not have access
- No drag-and-drop spec
- Large GLB as base64 in JSON body will crash
- USD files should just use `add_reference`, no upload needed

## Missing User Flows

- Undo narration
- Multi-selection operations
- Long-running operation monitoring
- Save/export

## Blueprint Card Rendering

Detailed omni.ui recommendation provided with concrete widget structure.

## Sources
- Internal code review of chat_view.py, tool_executor.py, policy_engine.py
