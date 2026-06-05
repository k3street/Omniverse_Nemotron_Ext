# Phase File-Write-Set Matrix

**Phases parsed:** 107
**Unique files referenced:** 107

**Files written by ≥ 2 phases (potential conflicts):** 4

Use `scripts/safe_batch.py PHASE_IDS...` to check whether a proposed batch of phases can run in parallel safely (no overlapping file writes).

## Most-contended files (top 30)

| file | phase count | phases |
|---|---|---|
| `tool_executor.py` | 5 | `6`, `9`, `13`, `14`, `15` |
| `service/isaac_assist_service/chat/tools/tool_executor.py` | 3 | `1`, `3`, `5` |
| `data/object_classes.yaml` | 2 | `25`, `71` |
| `handlers/workflow.py` | 2 | `15`, `39` |

## Per-phase file declarations

(See `docs/audits/phase_file_writes.json` for the full machine-readable index.)

### Phase 0b  (line 207)

- **new** (2):
  - `scripts/audit_fork_divergence.py`
  - `docs/audits/fork_divergence_template.md`

### Phase 1  (line 232)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`
- **new** (3):
  - `scripts/audit_tools.py`
  - `docs/forensics/tool_audit_2026-05-10.md`
  - `tests/test_tool_audit.py`

### Phase 2  (line 308)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/__init__.py`
- **new** (16):
  - `service/isaac_assist_service/chat/tools/handlers/__init__.py`
  - `service/isaac_assist_service/chat/tools/handlers/scene_authoring.py`
  - `service/isaac_assist_service/chat/tools/handlers/physics.py`
  - `service/isaac_assist_service/chat/tools/handlers/robot.py`
  - `service/isaac_assist_service/chat/tools/handlers/sensors.py`
  - `service/isaac_assist_service/chat/tools/handlers/sdg.py`
  - `service/isaac_assist_service/chat/tools/handlers/training.py`
  - `service/isaac_assist_service/chat/tools/handlers/ros2.py`
  - `service/isaac_assist_service/chat/tools/handlers/teleop.py`
  - `service/isaac_assist_service/chat/tools/handlers/scene_blueprints.py`
  - `service/isaac_assist_service/chat/tools/handlers/diagnostics.py`
  - `service/isaac_assist_service/chat/tools/handlers/arena.py`
  - `service/isaac_assist_service/chat/tools/handlers/workflow.py`
  - `service/isaac_assist_service/chat/tools/handlers/resolve.py`
  - `service/isaac_assist_service/chat/tools/handlers/vision.py`
  - `service/isaac_assist_service/chat/tools/handlers/_dispatch.py`

### Phase 3  (line 399)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`

### Phase 4  (line 465)

- **changes** (1):
  - `.github/workflows/ci.yml`
- **new** (2):
  - `tests/test_tool_audit.py::test_every_tool_resolves`
  - `tests/fixtures/no_handler_tools.json`

### Phase 5  (line 538)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`

### Phase 6  (line 577)

- **changes** (1):
  - `tool_executor.py`

### Phase 8  (line 655)

- **new** (2):
  - `handlers/_shared.py`
  - `handlers/_state.py`

### Phase 9  (line 729)

- **changes** (1):
  - `tool_executor.py`

### Phase 10  (line 796)

- **new** (2):
  - `handlers/_models.py`
  - `scripts/gen_handler_models.py`

### Phase 11  (line 867)

- **changes** (1):
  - `chat/tools/patch_validator.py`
- **new** (3):
  - `chat/tools/patch_validators/__init__.py`
  - `chat/tools/patch_validators/rules/{rule_name}.py`
  - `chat/tools/patch_validators/registry.py`

### Phase 12  (line 959)

- **new** (2):
  - `tests/test_no_circular_imports.py`
  - `scripts/diag_imports.py`

### Phase 13  (line 1000)

- **changes** (1):
  - `tool_executor.py`
- **new** (1):
  - `docs/forensics/tool_executor_recovered_state_2026-05-10.md`

### Phase 14  (line 1056)

- **changes** (1):
  - `tool_executor.py`

### Phase 15  (line 1094)

- **changes** (2):
  - `tool_executor.py`
  - `handlers/workflow.py`
- **new** (2):
  - `handlers/workflow.py:WorkflowRecord`
  - `handlers/workflow.py:WorkflowTemplate`

### Phase 17  (line 1204)

- **new** (2):
  - `scripts/lint/no_handler_in_dispatch.py`
  - `scripts/lint/regen_models_check.py`

### Phase 19  (line 1291)

- **changes** (2):
  - `service/isaac_assist_service/chat/tools/multimodal_handlers.py:212-305`
  - `service/isaac_assist_service/multimodal/routes.py:219-224`
- **new** (2):
  - `service/isaac_assist_service/multimodal/instantiator.py`
  - `service/isaac_assist_service/multimodal/instantiator_test.py`

### Phase 20  (line 1384)

- **changes** (2):
  - `service/isaac_assist_service/chat/tools/template_retriever.py:1-300`
  - `workspace/templates/CP-01.json`

### Phase 21  (line 1457)

- **new** (1):
  - `service/isaac_assist_service/chat/tools/role_index.py`

### Phase 22  (line 1482)

- **new** (1):
  - `service/isaac_assist_service/multimodal/stage_to_spec.py`

### Phase 23  (line 1544)

- **changes** (1):
  - `web/floor-plan-ui/src/canvas/snap.ts`
- **new** (1):
  - `web/floor-plan-ui/src/canvas/snap.test.ts`

### Phase 24  (line 1580)

- **changes** (3):
  - `web/floor-plan-ui/src/components/ConfirmBar.tsx`
  - `web/floor-plan-ui/src/store/floorPlanStore.ts`
  - `service/isaac_assist_service/multimodal/routes.py`

### Phase 25  (line 1610)

- **new** (3):
  - `service/isaac_assist_service/multimodal/object_classes.py`
  - `data/object_classes.yaml`
  - `scripts/regen_object_classes.py`

### Phase 26  (line 1667)

- **changes** (2):
  - `web/floor-plan-ui/src/components/HistoryPanel.tsx`
  - `multimodal/routes.py`
- **new** (1):
  - `multimodal/diff.py`

### Phase 27  (line 1694)

- **new** (1):
  - `multimodal/blueprint_adapter.py`

### Phase 29  (line 1739)

- **changes** (1):
  - `exts/isaac_6.0/.../canvas_mirror.py`
- **new** (1):
  - `exts/isaac_6.0/.../stage_event_listener.py`

### Phase 32  (line 1811)

- **new** (1):
  - `tests/integration/test_canvas_30_objects.py`

### Phase 33  (line 1846)

- **changes** (2):
  - `handlers/workflow.py:register_workflow_template(WorkflowTemplate)`
  - `handlers/_dispatch.py:register_all_handlers`
- **new** (2):
  - `service/isaac_assist_service/workflow/template_base.py`
  - `service/isaac_assist_service/workflow/decorators.py`

### Phase 37  (line 1950)

- **changes** (1):
  - `exts/isaac_6.0/.../ui/chat_view.py`
- **new** (1):
  - `exts/isaac_6.0/.../ui/workflow_timeline.py`

### Phase 38  (line 1971)

- **changes** (1):
  - `web/floor-plan-ui/src/App.tsx`
- **new** (1):
  - `web/floor-plan-ui/src/components/WorkflowTimelinePanel.tsx`

### Phase 39  (line 1988)

- **changes** (1):
  - `handlers/workflow.py`
- **new** (2):
  - `service/isaac_assist_service/workflow/persistence.py`
  - `service/isaac_assist_service/workflow/migrations/0001_initial.sql`

### Phase 41  (line 2037)

- **changes** (1):
  - `chat/turn_snapshot.py`
- **new** (1):
  - `chat/snapshot_restore.py`

### Phase 42  (line 2067)

- **changes** (2):
  - `governance/policy_engine.py`
  - `chat/tools/kit_tools.py:queue_exec_patch`

### Phase 43  (line 2094)

- **changes** (1):
  - `chat/slash_commands.py`

### Phase 45  (line 2140)

- **changes** (1):
  - `planner/agents/critic.py`
- **new** (3):
  - `planner/agents/critic_features.py`
  - `planner/agents/math_critic.py`
  - `planner/agents/critic_calibration.py`

### Phase 46  (line 2222)

- **new** (1):
  - `tests/test_pm_determinism.py`

### Phase 47  (line 2245)

- **changes** (1):
  - `chat/tools/patch_validators/rules/`

### Phase 48  (line 2303)

- **changes** (3):
  - `diagnose/metrics.py`
  - `diagnose/schema.py:THRESHOLDS`
  - `diagnose/tool.py`

### Phase 53  (line 2425)

- **new** (1):
  - `diagnose/simulation_runner.py`

### Phase 54  (line 2488)

- **new** (1):
  - `diagnose/gap_log.py`

### Phase 55  (line 2508)

- **new** (2):
  - `scripts/analyze_gaps.py`
  - `docs/diagnose/gap_report_template.md`

### Phase 56  (line 2525)

- **new** (1):
  - `diagnose/recalibrate.py`

### Phase 59  (line 2598)

- **changes** (1):
  - `tool_executor.py:215-224`

### Phase 63  (line 2681)

- **new** (2):
  - `handlers/contact_sequence.py`
  - `tests/test_contact_sequence.py`

### Phase 65  (line 2745)

- **new** (1):
  - `service/isaac_assist_service/training/persistence.py`

### Phase 71  (line 2848)

- **changes** (1):
  - `data/object_classes.yaml`
- **new** (3):
  - `data/usd/robots/yaskawa_gp25/source/yaskawa_gp25.urdf`
  - `data/usd/robots/yaskawa_gp25/source/meshes/`
  - `data/usd/robots/yaskawa_gp25/yaskawa_gp25.usd`

### Phase 72  (line 2880)

- **new** (1):
  - `handlers/assembly_runtime.py`

### Phase 99  (line 3346)

- **new** (2):
  - `tests/integration/test_pick_hold_weld_e2e.py`
  - `docs/demos/pick_hold_weld.md`

### Phase 100  (line 3382)

- **new** (2):
  - `tests/benchmark/arena_ia_vs_handcrafted.py`
  - `docs/benchmarks/2026-05-{date}-arena.md`
