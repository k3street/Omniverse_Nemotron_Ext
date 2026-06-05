# Phase File-Write-Set Matrix

**Phases parsed:** 146
**Unique files referenced:** 270

**Files written by ≥ 2 phases (potential conflicts):** 20

Use `scripts/safe_batch.py PHASE_IDS...` to check whether a proposed batch of phases can run in parallel safely (no overlapping file writes).

## Most-contended files (top 30)

| file | phase count | phases |
|---|---|---|
| `tool_executor.py` | 15 | `6`, `9`, `13`, `14`, `15`, `18b`, `47b`, `70b`, `70c`, `72b`, `72c`, `80b`, `80c`, `81b`, `81c` |
| `handlers/robot.py` | 7 | `11c`, `56c`, `63b`, `63c`, `63d`, `79b`, `80b` |
| `handlers/contact_sequence.py` | 4 | `63`, `63b`, `63d`, `70d` |
| `tool_schemas.py` | 4 | `18b`, `72b`, `72c`, `81b` |
| `chat/canonical_instantiator.py` | 3 | `8d`, `11c`, `78c` |
| `data/object_classes.yaml` | 3 | `25`, `25b`, `71` |
| `diagnose/schema.py` | 3 | `8c`, `56b`, `56c` |
| `handlers/workflow.py` | 3 | `15`, `39`, `94b` |
| `service/isaac_assist_service/chat/tools/tool_executor.py` | 3 | `1`, `3`, `5` |
| `chat/orchestrator.py` | 2 | `63c`, `94b` |
| `diagnose/gap_log.py` | 2 | `54`, `56b` |
| `diagnose/recalibrate.py` | 2 | `56`, `56b` |
| `diagnose/tool.py` | 2 | `48`, `49b` |
| `governance/policy_engine.py` | 2 | `42`, `88b` |
| `handlers/ros2.py` | 2 | `7b`, `31b` |
| `mcp_server.py` | 2 | `18b`, `85b` |
| `multimodal/handlers.py:347-359` | 2 | `8c`, `25b` |
| `multimodal/routes.py` | 2 | `24b`, `26` |
| `multimodal/types.py` | 2 | `8c`, `25b` |
| `service/isaac_assist_service/types/uncertainty.py` | 2 | `8c`, `56b` |

## Per-phase file declarations

(See `docs/audits/phase_file_writes.json` for the full machine-readable index.)

### Phase 0b  (line 264)

- **new** (2):
  - `scripts/audit_fork_divergence.py`
  - `docs/audits/fork_divergence_template.md`

### Phase 1  (line 378)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`
- **new** (3):
  - `scripts/audit_tools.py`
  - `docs/forensics/tool_audit_2026-05-10.md`
  - `tests/test_tool_audit.py`

### Phase 2  (line 454)

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

### Phase 2b  (line 545)

- **new** (6):
  - `scripts/audit_handler_cross_refs.py`
  - `scripts/audit_phase_file_writes.py`
  - `scripts/safe_batch.py`
  - `docs/audits/handler_cross_refs.md`
  - `docs/audits/phase_file_writes.md`
  - `tests/test_safe_batch.py`

### Phase 3  (line 648)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`

### Phase 4  (line 714)

- **changes** (1):
  - `.github/workflows/ci.yml`
- **new** (2):
  - `tests/test_tool_audit.py::test_every_tool_resolves`
  - `tests/fixtures/no_handler_tools.json`

### Phase 5  (line 787)

- **changes** (1):
  - `service/isaac_assist_service/chat/tools/tool_executor.py`

### Phase 6  (line 826)

- **changes** (1):
  - `tool_executor.py`

### Phase 7b  (line 904)

- **changes** (4):
  - `handlers/ros2.py`
  - `handlers/sensors.py`
  - `chat/tools/ros_mcp_tools.py`
  - `infra/ci/.github/workflows/test_isaac_5.1.yml`
- **new** (2):
  - `service/isaac_assist_service/compat/isaac_version.py`
  - `tests/test_isaac_6_0_imports.py`

### Phase 8  (line 1005)

- **new** (2):
  - `handlers/_shared.py`
  - `handlers/_state.py`

### Phase 8b  (line 1079)

- **changes** (3):
  - `planner/agents/sim_harness.py`
  - `multimodal/render.py`
  - `diagnose/sim_backend_mock.py`
- **new** (2):
  - `service/isaac_assist_service/utils/determinism.py`
  - `tests/test_determinism.py`

### Phase 8c  (line 1165)

- **new** (8):
  - `service/isaac_assist_service/types/__init__.py`
  - `service/isaac_assist_service/types/spatial.py`
  - `service/isaac_assist_service/types/uncertainty.py`
  - `service/isaac_assist_service/types/provenance.py`
  - `tests/test_shared_types.py`
  - `multimodal/types.py`
  - `diagnose/schema.py`
  - `multimodal/handlers.py:347-359`

### Phase 8d  (line 1232)

- **changes** (2):
  - `chat/canonical_instantiator.py`
  - `scripts/qa/`
- **new** (6):
  - `service/isaac_assist_service/qa/__init__.py`
  - `service/isaac_assist_service/qa/baseline_status.py`
  - `service/isaac_assist_service/qa/regression.py`
  - `service/isaac_assist_service/qa/baseline.py`
  - `data/baselines/_README.md`
  - `tests/test_baseline_taxonomy.py`

### Phase 9  (line 1316)

- **changes** (1):
  - `tool_executor.py`

### Phase 10  (line 1383)

- **new** (2):
  - `handlers/_models.py`
  - `scripts/gen_handler_models.py`

### Phase 11  (line 1454)

- **changes** (1):
  - `chat/tools/patch_validator.py`
- **new** (3):
  - `chat/tools/patch_validators/__init__.py`
  - `chat/tools/patch_validators/rules/{rule_name}.py`
  - `chat/tools/patch_validators/registry.py`

### Phase 11b  (line 1546)

- **new** (2):
  - `service/isaac_assist_service/types/violations.py`
  - `tests/test_violations.py`

### Phase 11c  (line 1629)

- **changes** (3):
  - `handlers/robot.py`
  - `scripts/qa/probe_ctrl_telemetry.py`
  - `chat/canonical_instantiator.py`
- **new** (2):
  - `service/isaac_assist_service/types/ctrl_namespace.py`
  - `tests/test_ctrl_namespace.py`

### Phase 12  (line 1701)

- **new** (2):
  - `tests/test_no_circular_imports.py`
  - `scripts/diag_imports.py`

### Phase 13  (line 1742)

- **changes** (1):
  - `tool_executor.py`
- **new** (1):
  - `docs/forensics/tool_executor_recovered_state_2026-05-10.md`

### Phase 14  (line 1798)

- **changes** (1):
  - `tool_executor.py`

### Phase 15  (line 1836)

- **changes** (2):
  - `tool_executor.py`
  - `handlers/workflow.py`
- **new** (2):
  - `handlers/workflow.py:WorkflowRecord`
  - `handlers/workflow.py:WorkflowTemplate`

### Phase 17  (line 1946)

- **new** (2):
  - `scripts/lint/no_handler_in_dispatch.py`
  - `scripts/lint/regen_models_check.py`

### Phase 17b  (line 1981)

- **new** (3):
  - `scripts/lint_mandate.py`
  - `tests/test_lint_mandate.py`
  - `docs/architecture/mandate_boundary.md`

### Phase 18b  (line 2104)

- **changes** (3):
  - `tool_schemas.py`
  - `tool_executor.py`
  - `mcp_server.py`
- **new** (2):
  - `scripts/audit_tool_levels.py`
  - `docs/architecture/action_levels.md`

### Phase 18c  (line 2193)

- **changes** (1):
  - `IA_FULL_SPEC_2026-05-10.md`
- **new** (2):
  - `docs/architecture/honesty.md`
  - `scripts/audit_honesty_links.py`

### Phase 19  (line 2299)

- **changes** (2):
  - `service/isaac_assist_service/chat/tools/multimodal_handlers.py:212-305`
  - `service/isaac_assist_service/multimodal/routes.py:219-224`
- **new** (2):
  - `service/isaac_assist_service/multimodal/instantiator.py`
  - `service/isaac_assist_service/multimodal/instantiator_test.py`

### Phase 20  (line 2392)

- **changes** (2):
  - `service/isaac_assist_service/chat/tools/template_retriever.py:1-300`
  - `workspace/templates/CP-01.json`

### Phase 21  (line 2465)

- **new** (1):
  - `service/isaac_assist_service/chat/tools/role_index.py`

### Phase 22  (line 2490)

- **new** (1):
  - `service/isaac_assist_service/multimodal/stage_to_spec.py`

### Phase 23  (line 2552)

- **changes** (1):
  - `web/floor-plan-ui/src/canvas/snap.ts`
- **new** (1):
  - `web/floor-plan-ui/src/canvas/snap.test.ts`

### Phase 24  (line 2588)

- **changes** (3):
  - `web/floor-plan-ui/src/components/ConfirmBar.tsx`
  - `web/floor-plan-ui/src/store/floorPlanStore.ts`
  - `service/isaac_assist_service/multimodal/routes.py`

### Phase 24b  (line 2618)

- **changes** (3):
  - `web/floor-plan-ui/src/canvas/`
  - `web/floor-plan-ui/src/store/`
  - `multimodal/routes.py`
- **new** (7):
  - `web/floor-plan-ui/src/canvas/SmartGuides.tsx`
  - `web/floor-plan-ui/src/canvas/DimensionLines.tsx`
  - `web/floor-plan-ui/src/canvas/MotionVocabularyTokens.tsx`
  - `web/floor-plan-ui/src/canvas/ObjectInspector.tsx`
  - `web/floor-plan-ui/src/store/historyMiddleware.ts`
  - `web/floor-plan-ui/src/store/walMiddleware.ts`
  - `tests/e2e/canvas_interactive_parity.spec.ts`

### Phase 25  (line 2687)

- **new** (3):
  - `service/isaac_assist_service/multimodal/object_classes.py`
  - `data/object_classes.yaml`
  - `scripts/regen_object_classes.py`

### Phase 25b  (line 2752)

- **changes** (4):
  - `data/object_classes.yaml`
  - `multimodal/handlers.py:347-359`
  - `multimodal/types.py`
  - `multimodal/handlers.py`
- **new** (2):
  - `data/object_classes_validator.py`
  - `tests/test_object_class_metadata.py`

### Phase 26  (line 2815)

- **changes** (2):
  - `web/floor-plan-ui/src/components/HistoryPanel.tsx`
  - `multimodal/routes.py`
- **new** (1):
  - `multimodal/diff.py`

### Phase 27  (line 2842)

- **new** (1):
  - `multimodal/blueprint_adapter.py`

### Phase 29  (line 2887)

- **changes** (1):
  - `exts/isaac_6.0/.../canvas_mirror.py`
- **new** (1):
  - `exts/isaac_6.0/.../stage_event_listener.py`

### Phase 31b  (line 2959)

- **changes** (2):
  - `handlers/ros2.py`
  - `tool_executor.py:35606+`
- **new** (5):
  - `handlers/bridges.py`
  - `data/canonical_templates/modbus_to_ros2_relay.json`
  - `data/canonical_templates/mqtt_sparkplug_telemetry.json`
  - `data/canonical_templates/openplc_handshake.json`
  - `tests/integration/test_bridge_lifecycle.py`

### Phase 32  (line 3039)

- **new** (1):
  - `tests/integration/test_canvas_30_objects.py`

### Phase 33  (line 3074)

- **changes** (2):
  - `handlers/workflow.py:register_workflow_template(WorkflowTemplate)`
  - `handlers/_dispatch.py:register_all_handlers`
- **new** (2):
  - `service/isaac_assist_service/workflow/template_base.py`
  - `service/isaac_assist_service/workflow/decorators.py`

### Phase 37  (line 3178)

- **changes** (1):
  - `exts/isaac_6.0/.../ui/chat_view.py`
- **new** (1):
  - `exts/isaac_6.0/.../ui/workflow_timeline.py`

### Phase 38  (line 3199)

- **changes** (1):
  - `web/floor-plan-ui/src/App.tsx`
- **new** (1):
  - `web/floor-plan-ui/src/components/WorkflowTimelinePanel.tsx`

### Phase 39  (line 3216)

- **changes** (1):
  - `handlers/workflow.py`
- **new** (2):
  - `service/isaac_assist_service/workflow/persistence.py`
  - `service/isaac_assist_service/workflow/migrations/0001_initial.sql`

### Phase 41  (line 3265)

- **changes** (1):
  - `chat/turn_snapshot.py`
- **new** (1):
  - `chat/snapshot_restore.py`

### Phase 42  (line 3295)

- **changes** (2):
  - `governance/policy_engine.py`
  - `chat/tools/kit_tools.py:queue_exec_patch`

### Phase 43  (line 3322)

- **changes** (1):
  - `chat/slash_commands.py`

### Phase 45  (line 3368)

- **changes** (1):
  - `planner/agents/critic.py`
- **new** (3):
  - `planner/agents/critic_features.py`
  - `planner/agents/math_critic.py`
  - `planner/agents/critic_calibration.py`

### Phase 46  (line 3450)

- **new** (1):
  - `tests/test_pm_determinism.py`

### Phase 47  (line 3473)

- **changes** (1):
  - `chat/tools/patch_validators/rules/`

### Phase 47b  (line 3531)

- **changes** (1):
  - `tool_executor.py`
- **new** (3):
  - `scripts/honesty_inventory.py`
  - `chat/tools/queue_exec_honesty.py`
  - `docs/audits/honesty_inventory.md`

### Phase 48  (line 3604)

- **changes** (3):
  - `diagnose/metrics.py`
  - `diagnose/schema.py:THRESHOLDS`
  - `diagnose/tool.py`

### Phase 49b  (line 3667)

- **changes** (2):
  - `diagnose/cache.py`
  - `diagnose/tool.py`
- **new** (1):
  - `tests/test_diagnose_cache_invalidation.py`

### Phase 53  (line 3766)

- **new** (1):
  - `diagnose/simulation_runner.py`

### Phase 54  (line 3871)

- **new** (2):
  - `diagnose/gap_log.py`
  - `diagnose/gap_query.py`

### Phase 55  (line 3962)

- **new** (2):
  - `scripts/analyze_gaps.py`
  - `docs/diagnose/gap_report_template.md`

### Phase 56  (line 3979)

- **new** (3):
  - `diagnose/recalibrate.py`
  - `workspace/diagnose_gaps/_corrections.yaml`
  - `diagnose/escalation.py`

### Phase 56b  (line 4103)

- **changes** (4):
  - `service/isaac_assist_service/types/uncertainty.py`
  - `diagnose/schema.py`
  - `diagnose/gap_log.py`
  - `diagnose/recalibrate.py`
- **new** (2):
  - `service/isaac_assist_service/utils/bootstrap_ci.py`
  - `tests/test_bootstrap_ci.py`

### Phase 56c  (line 4194)

- **changes** (2):
  - `handlers/robot.py`
  - `diagnose/schema.py`
- **new** (3):
  - `handlers/control_profiles.py`
  - `data/control_profiles.yaml`
  - `tests/test_control_profiles.py`

### Phase 59  (line 4332)

- **changes** (1):
  - `tool_executor.py:215-224`

### Phase 60b  (line 4372)

- **changes** (3):
  - `handlers/sdg.py`
  - `data/dr/distractor_pool.yaml`
  - `tests/test_sdg_extreme_dr.py`

### Phase 62b  (line 4476)

- **changes** (1):
  - `handlers/training.py`
- **new** (2):
  - `handlers/groot_blueprints.py`
  - `tests/test_groot_n17_workflow.py`

### Phase 63  (line 4534)

- **new** (2):
  - `handlers/contact_sequence.py`
  - `tests/test_contact_sequence.py`

### Phase 63b  (line 4577)

- **changes** (3):
  - `handlers/robot.py`
  - `handlers/contact_sequence.py`
  - `handlers/multi_rate.py`
- **new** (3):
  - `handlers/motion_planning.py`
  - `tests/test_curobov2_pick_grasp.py`
  - `docs/handlers/motion_backends.md`

### Phase 63c  (line 4669)

- **changes** (2):
  - `handlers/robot.py`
  - `chat/orchestrator.py`
- **new** (3):
  - `handlers/curobo_debug.py`
  - `data/curobo_robot_configs/{ur10,ur5e,ur3e,franka,fr3,gp25}.yaml`
  - `tests/test_curobo_per_robot.py`

### Phase 63d  (line 4751)

- **changes** (2):
  - `handlers/robot.py`
  - `handlers/contact_sequence.py`
- **new** (2):
  - `handlers/multi_robot_coord.py`
  - `tests/integration/test_multi_robot_handoff.py`

### Phase 65  (line 4852)

- **new** (1):
  - `service/isaac_assist_service/training/persistence.py`

### Phase 70b  (line 4955)

- **changes** (1):
  - `tool_executor.py`
- **new** (2):
  - `tests/integration/test_create_behavior.py`
  - `docs/handlers/create_behavior.md`

### Phase 70c  (line 5051)

- **changes** (2):
  - `tool_executor.py`
  - `chat/intent_router.py`
- **new** (3):
  - `handlers/articulated_pull.py`
  - `tests/integration/test_drawer_open_pull.py`
  - `tests/integration/test_revolute_pull.py`

### Phase 70d  (line 5140)

- **changes** (2):
  - `handlers/robot.py:setup_pick_place_controller`
  - `handlers/contact_sequence.py`
- **new** (2):
  - `data/bin_interior_metadata.yaml`
  - `tests/integration/test_drop_target_default.py`

### Phase 71  (line 5221)

- **changes** (1):
  - `data/object_classes.yaml`
- **new** (3):
  - `data/usd/robots/yaskawa_gp25/source/yaskawa_gp25.urdf`
  - `data/usd/robots/yaskawa_gp25/source/meshes/`
  - `data/usd/robots/yaskawa_gp25/yaskawa_gp25.usd`

### Phase 72  (line 5253)

- **new** (1):
  - `handlers/assembly_runtime.py`

### Phase 72b  (line 5275)

- **changes** (2):
  - `tool_executor.py`
  - `tool_schemas.py`
- **new** (2):
  - `handlers/joint_inference.py`
  - `tests/test_joint_inference.py`

### Phase 72c  (line 5388)

- **changes** (2):
  - `tool_executor.py`
  - `tool_schemas.py`
- **new** (3):
  - `handlers/blueprint_revise.py`
  - `tests/blueprints/scale_30.yaml`
  - `tests/test_blueprint_validator_strength.py`

### Phase 78b  (line 5612)

- **changes** (2):
  - `data/canonical_templates/brick_stacking*.json`
  - `data/canonical_templates/drawer_open*.json`
- **new** (3):
  - `scripts/audit_yrkesroll.py`
  - `docs/audits/yrkesroll_status_template.md`
  - `tests/integration/test_brick_stacking_5_bricks.py`

### Phase 78c  (line 5679)

- **changes** (2):
  - `chat/canonical_instantiator.py`
  - `data/canonical_templates/*.json`
- **new** (4):
  - `handlers/asset_precheck.py`
  - `handlers/asset_mock_fallback.py`
  - `data/asset_mock_registry.yaml`
  - `tests/test_asset_precheck.py`

### Phase 79b  (line 5795)

- **changes** (1):
  - `handlers/robot.py`
- **new** (3):
  - `handlers/locomanip.py`
  - `data/usd/robots/g1/`
  - `tests/integration/test_g1_locomanip.py`

### Phase 80b  (line 5874)

- **changes** (2):
  - `tool_executor.py`
  - `handlers/robot.py`
- **new** (3):
  - `data/physics_defaults.yaml`
  - `handlers/grip_stability.py`
  - `tests/integration/test_grasp_stability.py`

### Phase 80c  (line 5965)

- **changes** (1):
  - `tool_executor.py`
- **new** (2):
  - `handlers/conveyor_curved.py`
  - `tests/integration/test_curved_belt_recirculation.py`

### Phase 81b  (line 6091)

- **changes** (2):
  - `tool_executor.py`
  - `tool_schemas.py`
- **new** (4):
  - `service/isaac_assist_service/validation/__init__.py`
  - `service/isaac_assist_service/validation/routes.py`
  - `tests/test_validation_routes.py`
  - `docs/handlers/validation_primitives.md`

### Phase 81c  (line 6039)

- **changes** (1):
  - `tool_executor.py`
- **new** (3):
  - `handlers/cumotion_moveit.py`
  - `tests/integration/test_cumotion_moveit_franka.py`
  - `docs/handlers/cumotion_moveit.md`

### Phase 85b  (line 6323)

- **changes** (2):
  - `mcp_server.py`
  - `chat/orchestrator.py:773-793`
- **new** (4):
  - `service/isaac_assist_service/mcp_advanced/sampling.py`
  - `service/isaac_assist_service/mcp_advanced/elicitation.py`
  - `service/isaac_assist_service/mcp_advanced/session_resumption.py`
  - `tests/test_mcp_advanced.py`

### Phase 88b  (line 6409)

- **changes** (2):
  - `governance/policy_engine.py`
  - `chat/tools/kit_tools.py`
- **new** (5):
  - `service/isaac_assist_service/sandbox/__init__.py`
  - `service/isaac_assist_service/sandbox/subprocess_seccomp.py`
  - `service/isaac_assist_service/sandbox/firecracker_runner.py`
  - `service/isaac_assist_service/sandbox/gvisor_runner.py`
  - `tests/test_sandbox_routing.py`

### Phase 94b  (line 6576)

- **changes** (2):
  - `chat/orchestrator.py`
  - `handlers/workflow.py`
- **new** (6):
  - `service/isaac_assist_service/flywheel/__init__.py`
  - `service/isaac_assist_service/flywheel/capture.py`
  - `service/isaac_assist_service/flywheel/curate.py`
  - `service/isaac_assist_service/flywheel/synthesise.py`
  - `service/isaac_assist_service/flywheel/registry.py`
  - `tests/test_flywheel.py`

### Phase 96b  (line 6692)

- **new** (6):
  - `scripts/audit_spec_reconciliation.py`
  - `scripts/audit_phase_completion.py`
  - `scripts/audit_phase_overlap.py`
  - `scripts/audit_dependency_drift.py`
  - `docs/audits/spec_reconciliation_template.md`
  - `.github/workflows/quarterly-spec-reconciliation.yml`

### Phase 97b  (line 6840)

- **new** (5):
  - `scripts/qa/fast_sweep.py`
  - `scripts/qa/diff_sweep.py`
  - `scripts/qa/impact_map.py`
  - `.github/workflows/fast-sweep.yml`
  - `workspace/sweeps/_index.json`

### Phase 99  (line 6945)

- **new** (2):
  - `tests/integration/test_pick_hold_weld_e2e.py`
  - `docs/demos/pick_hold_weld.md`

### Phase 100  (line 6981)

- **new** (2):
  - `tests/benchmark/arena_ia_vs_handcrafted.py`
  - `docs/benchmarks/2026-05-{date}-arena.md`
