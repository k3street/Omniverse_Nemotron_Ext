# Tool Audit Phase A — DATA_HANDLERS catalog

**Date**: 2026-05-05
**Scope**: 183 DATA_HANDLERS in `service/isaac_assist_service/chat/tools/tool_executor.py`
**Method**: `scripts/qa/audit_data_handlers.py` — calls each handler with minimal default args from its schema, classifies the outcome
**Phase A is catalog-only** — no fixes applied. Fixes belong in a future phase, prioritized from this data.

## Summary

| Outcome | Count | % |
|---|---|---|
| PASS | 125 | 68% |
| SOFT_FAIL (handler returned `error` or `success: false`) | 49 | 27% |
| HARD_FAIL (raised exception or timed out >30s) | 8 | 4% |
| SKIPPED (handler is None — inline LLM or absent dep) | 1 | 1% |
| **Total** | **183** | |

Raw output: `workspace/qa_runs/tool_audit_20260505T162258.jsonl`

## HARD_FAIL (8) — bugs

| Tool | Error | Class |
|---|---|---|
| `overlap_box` | TimeoutError 30s | Kit RPC blocking; needs handler-side timeout |
| `overlap_sphere` | TimeoutError 30s | Same |
| `pixel_to_world` | TimeoutError 30s | Same |
| `ros2_connect` | ModuleNotFoundError 'ros_mcp' | Should be SKIPPED — None-handler path skipped, but exception still raises |
| `ros2_get_topic_type` | (same) | (same) |
| `ros2_list_nodes` | (same) | (same) |
| `ros2_list_services` | (same) | (same) |
| `ros2_list_topics` | (same) | (same) |

**Fix targets:**
1. Three Kit-RPC handlers (`overlap_box`, `overlap_sphere`, `pixel_to_world`) need an inner request-timeout so they fail-fast instead of hanging at 30s. The 504-style failure mode of `nucleus_browse` (SOFT_FAIL with 504 message) is the correct shape — these three should match it.
2. The five ros2 handlers reach the audit despite the `ros_mcp` import failure. Either the import-guard is incomplete or the wrong handler attribute survives. Should resolve to None and then SKIPPED.

## SOFT_FAIL (49) — three sub-classes

### A. Required-args validation — **EXPECTED behavior, NOT a bug** (16 tools)

Handler correctly rejects empty/invalid input. The audit feeds defaults derived from the schema's `required` fields, but the handler's *semantic* requirements (non-empty strings, dict shapes, file paths) reject defaults.

| Tool | Reject reason |
|---|---|
| `approve_workflow_checkpoint`, `cancel_workflow`, `edit_workflow_plan`, `get_workflow_status`, `start_workflow` | empty workflow_id / goal |
| `checkpoint_training`, `pause_training`, `get_env_observations`, `get_env_rewards`, `get_env_termination_state`, `get_training_status` | no active training run |
| `detect_ood` | tier must be 1/2/3 |
| `diagnose_domain_gap` | synthetic_dir+real_dir required |
| `execute_with_retry`, `queue_write_locked_patch`, `review_reward` | code/reward_code required |
| `lookup_material` | both materials required |
| `measure_sim_real_gap` | unsupported file format (empty path) |
| `query_async_task` | task_id not found (empty) |
| `ros2_call_service`, `ros2_get_message_type`, `ros2_get_node_details`, `ros2_publish`, `ros2_publish_sequence`, `ros2_subscribe_once` | required ROS2 args |
| `scene_diff` | must provide since= or both snapshot args |
| `suggest_dr_ranges` | task_type required |
| `suggest_parameter_adjustment` | gap_report shape |
| `trace_config` | param_name required |
| `validate_calibration` | calibrated_params dict required |

**Conclusion:** these 16 are honest fail-fast validators. **No fix needed.**

### B. Stage-state-dependent — expected fail when prereq missing (~25 tools)

Handler call needs a specific scene state (robot, camera, asset path, file on disk) that the empty-stage audit doesn't provide.

| Tool | Missing prereq |
|---|---|
| `analyze_checkpoint`, `calibrate_physics`, `quick_calibrate`, `train_actuator_net` | checkpoint/data file at `/World/Cube` |
| `capture_camera_image`, `get_camera_params`, `inspect_camera` | Camera prim |
| `compare_sim_real_video` | video files |
| `download_asset` | nucleus_url must start with `omniverse://` (default `""` rejected) |
| `inspect_graph` | OmniGraph at path |
| `load_scene_template` | template name |
| `profile_training_throughput` | Perf scalars in stage |
| `restore_delta_snapshot` | delta manifest file |
| `validate_calibration` | calibrated_params data |

**Conclusion:** also correct fail-fast. **No fix needed**, but the audit script should mark these so they don't pollute the failure stats. Suggested: enrich `audit_data_handlers.py` with a per-tool prereq-spec so it can supply minimal stage state (a `/World/Camera`, `/World/Franka`, etc.) before calling.

### C. Empty error message — REAL silent-failure candidates (5 tools)

These returned `success: false` (or similar) without an error string explaining why. **This is the silent-failure bug class** Phase A is hunting for.

| Tool | Notes |
|---|---|
| `get_attribute` | empty error — handler must populate it |
| `inspect_camera` | empty error |
| `measure_distance` | dispatched to Kit but `success/output` shape with empty error |
| `preview_sdg` | empty error |
| `raycast` | empty error |
| `sweep_sphere` | empty error |

**Action for Phase B (fix-phase):** open each handler, ensure failure paths populate a meaningful `error` field. These are exactly the "told the LLM things look fine while they didn't" class that motivated the audit.

### D. Single confirmed real bug

| Tool | Notes |
|---|---|
| `nucleus_browse` | Kit RPC `/exec_sync` returned 504 — possibly a Nucleus-server reach issue, possibly a Kit-side timeout. Worth deeper investigation before fixing blindly. |

## Next steps (future phase, not Phase A scope)

1. Fix the 3 Kit-RPC handlers with inner timeouts (`overlap_box`, `overlap_sphere`, `pixel_to_world`).
2. Fix the ros2-import guard so missing `ros_mcp` produces SKIPPED rather than HARD_FAIL.
3. Populate empty error strings in the 5+ silent-fail handlers (sub-class C).
4. Investigate `nucleus_browse` 504 — environmental or code bug?
5. Enrich the audit script with per-tool prereq fixtures, so sub-class B (stage-state-dependent) gets fair coverage rather than counting as fail.
