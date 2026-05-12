# Handler Cross-Reference Audit

**Handlers analysed:** 406
**Module-level utilities:** 52
**Handler→callee edges (deduped):** 107
**Min fan-in for `_shared.py` candidacy:** 3

## High-fan-in utilities — `_shared.py` candidates

Module-level utilities called by ≥ 3 handlers. These belong in `handlers/_shared.py` (Phase 8 — but identifying them now lets Phase 3-7 avoid silent collisions between agents).

| utility | fan-in |
|---|---|
| `execute_tool_call` | 8 |
| `_get_viewport_bytes` | 5 |
| `_get_vision_provider` | 5 |
| `_query_run_ipc` | 5 |
| `_resolve_run_id` | 5 |
| `_check_real_data_path` | 4 |
| `_wf_now_iso` | 4 |
| `_parse_last_json_line` | 3 |
| `_safe_robot_name` | 3 |
| `_validate_env_id` | 3 |

## Handlers with highest outgoing fan-out

Handlers calling many other module-level functions. High fan-out increases the cross-theme dependency risk when the handler moves out of `tool_executor.py` — review the call list and either move callees together or refactor to inject the dependency via `handlers/_shared.py`.

| handler | fan-out |
|---|---|
| `_gen_setup_pick_place_controller` | 10 |
| `_handle_calibrate_physics` | 4 |
| `_handle_list_available_controllers` | 4 |
| `_handle_quick_calibrate` | 4 |
| `_gen_deformable` | 3 |
| `_handle_add_vision_classifier_gate` | 3 |
| `_handle_get_env_observations` | 3 |
| `_handle_get_env_rewards` | 3 |
| `_handle_get_env_termination_state` | 3 |
| `_handle_train_actuator_net` | 3 |
| `_gen_apply_physics_material` | 2 |
| `_handle_approve_workflow_checkpoint` | 2 |
| `_handle_check_vram_headroom` | 2 |
| `_handle_checkpoint_training` | 2 |
| `_handle_diagnose_training` | 2 |
| `_handle_filter_templates_by_hardware` | 2 |
| `_handle_lookup_material` | 2 |
| `_handle_pause_training` | 2 |
| `_handle_query_stage_index` | 2 |
| `_handle_scene_diff` | 2 |

## Full edge list — machine-readable

See `docs/audits/handler_cross_refs.json` for the complete edge list (107 entries).

