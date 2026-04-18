# Cross-Cutting: LLM Tool Design Review

**Agent:** LLM Tool Design  
**Date:** 2026-04-15  
**Status:** Complete

## Tool Count

- Current: 49 implemented
- Planned: 89 total
- Per-intent via distiller: 5–13 (ideal range)

## Context Distiller — Best Architecture Decision

Mirrors Anthropic's "Tool Search" finding: 58→10 tools improved accuracy 49%→74%. **Never abandon this.**

## 12 Tools Missing from `TOOL_CATEGORIES`

`anchor_robot`, `capture_viewport`, `batch_apply_operation`, `vision_*`, `list_scene_templates`, `load_scene_template`, `get_articulation_state`, `export_scene_package`, `get_physics_errors`, `check_collisions`, `fix_error`, `validate_scene_blueprint` — invisible to LLM for most intents.

## 43 Parameters Without Descriptions

Bare `{"type": "string"}`. LLM guesses values. Critical missing: `prim_path`, `joint_name`, `articulation_path`.

## Top 7 Actions

1. Add enum to `prim_type` in `create_prim`
2. Register 12 missing tools in `TOOL_CATEGORIES`
3. Add descriptions to 43 bare parameters
4. Merge `get_console_errors` + `explain_error` → `diagnose_console`
5. Flatten `batch_apply_operation.parameters`
6. Rename `plan_trajectory` → `plan_joint_trajectory`
7. Hard 15-tool cap assertion in distiller

## Merge Candidates

- `get_console_errors` + `explain_error` → `diagnose_console`
- `get_physics_errors` + `check_collisions` → `diagnose_physics`
- `create_material` + `assign_material` → `apply_material`
- 3-tool blueprint pipeline → 2 tools (validate absorbed into build)

## Sources
- [Anthropic — Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Anthropic — Token-Efficient Tool Use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/token-efficient-tool-use)
