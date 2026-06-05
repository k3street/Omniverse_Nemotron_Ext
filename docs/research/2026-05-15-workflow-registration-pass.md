# Workflow Registration Pass — 2026-05-15

## §1 Pre-state

`_WORKFLOW_TEMPLATES` in `handlers/_state.py` contained exactly 3 entries:
`rl_training`, `robot_import`, `sim_debugging`.

Phase 34/35/36 template data existed in:
- `multimodal/workflow_template_pick_place.py` — `ASSEMBLE_PICK_PLACE_CELL_TEMPLATE`
- `multimodal/workflow_template_validate_robot.py` — `VALIDATE_ROBOT_IMPORT_TEMPLATE`
- `multimodal/workflow_template_sdg.py` — `GENERATE_SDG_DATASET_TEMPLATE`

None were registered. `start_workflow('assemble_pick_place_cell')` returned
`"Unknown workflow_type 'assemble_pick_place_cell'."` for all three names.

Secondary pre-existing bug: `_wf_make_initial_plan()` in `workflow.py:56` referenced
`_WORKFLOW_TEMPLATES` as a bare name but it was never imported at module scope (the
gate at line 606 correctly uses `_te._WORKFLOW_TEMPLATES`). This caused a `NameError`
the first time `_wf_make_initial_plan` ran — i.e. any successful `start_workflow` call
would crash immediately after passing the type-check.

## §2 What was registered

Three entries added to `_WORKFLOW_TEMPLATES` in `handlers/_state.py` (lines 324–371):

| workflow_type | Phases | Source module |
|---|---|---|
| `assemble_pick_place_cell` | load_template, place_objects, teach_grasp_pose, setup_controller, smoke_test | `workflow_template_pick_place.py` |
| `validate_robot_import` | import_robot, verify_articulation, check_collision_meshes, test_motion | `workflow_template_validate_robot.py` |
| `generate_sdg_dataset` | configure_scene, configure_dr_ranges, preview_render, generate_dataset, validate_annotations, export | `workflow_template_sdg.py` |

The data was copied inline (not imported) to match the existing dict-literal style of
the three legacy entries. A comment on each entry cites the Phase and source module.

Additional fix — `handlers/workflow.py` line 19: added `_WORKFLOW_TEMPLATES` to the
`from ._state import (...)` block so `_wf_make_initial_plan` has it in scope.

## §3 Verification

```
python -m pytest \
  tests/test_workflow_template_registration.py \
  tests/test_workflow_engine.py \
  tests/test_workflow_template_pick_place.py \
  tests/test_workflow_template_sdg.py \
  tests/test_workflow_template_validate_robot.py \
  -x --tb=short
```

Result: **9 passed** in 0.16 s, 0 failures, 0 errors.

New tests in `test_workflow_template_registration.py` cover:
- Registry presence (all 3 new names in dict)
- Template shape (description / phases / default_params / per-phase keys)
- Data consistency vs source module constants
- `start_workflow` dispatch — no Unknown-workflow-type error (no Kit RPC needed)
- Regression guard — 3 legacy templates still registered

## §4 Unreachable / dead code still in workflow surface

- **Phase executor stubs**: `_wf_make_initial_plan` and `_wf_advance_phase` build
  the structural plan but there is no phase-runner that actually calls Kit RPC per
  phase name. Advancing phases beyond the initial plan creation works at the
  workflow-lifecycle level (approve/advance), but the *content* of each phase
  (e.g. "smoke_test" actually triggering a simulation) is not dispatched anywhere —
  it relies entirely on the LLM issuing the appropriate tool calls in subsequent
  turns. This is by design per the spec (human-in-the-loop at checkpoints), but
  `error_fix: true` phases have no automated retry driver either.

- **Source module constants unused after registration**: `ASSEMBLE_PICK_PLACE_CELL_TEMPLATE`,
  `VALIDATE_ROBOT_IMPORT_TEMPLATE`, `GENERATE_SDG_DATASET_TEMPLATE` are now
  only referenced by `test_workflow_template_registration.py` for consistency checks.
  The registry uses inline copies. If the source modules are the single source of
  truth, a future pass should replace the inline copies with direct imports.

- **`_WORKFLOWS` singleton vs `_WORKFLOW_TEMPLATES`**: `_WORKFLOWS` (dict of active
  workflow instances) and `_WORKFLOW_TEMPLATES` (registry of types) are both aliased
  into `tool_executor.py` via `_state_module`. The `_WORKFLOWS` dict is never
  persisted to disk — a service restart loses all in-flight workflow state.
