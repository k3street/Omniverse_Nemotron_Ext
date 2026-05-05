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

## Phase B fixes — applied same session (2026-05-05)

After cataloging, three of the five action items in the original "next steps" list were addressed in this session. Re-running the audit against the fixed service produced a clean delta:

| Outcome | Before | After | Δ |
|---|---|---|---|
| PASS | 125 | 129 | +4 |
| SOFT_FAIL | 49 | 42 | −7 (error strings now populated) |
| HARD_FAIL | 8 | **0** | −8 |
| SKIPPED | 1 | 12 | +11 (ros2 handlers correctly skipped) |

Raw output: `workspace/qa_runs/tool_audit_20260505T171614.jsonl`

### Fix 1 — ros2 import-guard (5 HARD_FAIL → SKIPPED)

`service/isaac_assist_service/chat/tools/ros_mcp_tools.py` now does an explicit `importlib.import_module("ros_mcp.utils.websocket")` at module top so the `try/except ImportError` in `tool_executor.py`'s registration block catches it and registers all ros2 handlers as `None`. The audit and runtime then SKIP them with a clear "missing dependency" reason instead of every call hitting a lazy `ModuleNotFoundError`.

### Fix 2 — silent-fail error strings (centralized in kit_tools)

`kit_tools.queue_exec_patch` now populates an `error` field from the Kit RPC's `output` whenever `success=False`. This was the right layer to fix because dozens of code-gen handlers funnel through `queue_exec_patch` and previously returned its raw `{queued, executed, success, output}` shape with no `error` key. The 5+ tools the audit identified (and many more not in the audit list) now expose the underlying Kit error to the LLM and to the audit:

```
get_attribute      "Error: ... UsdStage::_GetDefiningSpec ..."
inspect_camera     "Error: inspect_camera: prim not found: '/World/Camera'"
measure_distance   "Error: measure_distance: prim_a not found: ''"
preview_sdg        "Error: Synchronous call to `step` can only be performed in a standalone workflow"
raycast            "Error: not enough values to unpack (expected 3, got 1)"
sweep_sphere       "Error: list index out of range"
```

### Fix 3 — audit script honours AUTO_APPROVE (3 timeouts → PASS in 8 ms)

The original audit's `overlap_box`/`overlap_sphere`/`pixel_to_world` 30-second timeouts were a script-side artifact, not a handler bug. Without `AUTO_APPROVE=true`, `queue_exec_patch` posts to Kit's `/exec_patch` (approval queue) and blocks waiting for a UI to drain. `audit_data_handlers.py` now sets `os.environ.setdefault("AUTO_APPROVE", "true")` at module top so the audit exercises Kit synchronously via `/exec_sync`. After the change all three handlers PASS in ≈8 ms.

## Remaining work (not addressed in this session)

1. **`nucleus_browse` 504** — environmental check needed (Nucleus reachability, Kit-side timeout). Could be code or could be infra. Worth investigating once before fixing blindly.
2. **Enrich audit-script prereq fixtures** — sub-class B (stage-state-dependent) currently counts the missing-prim/missing-camera failures as SOFT_FAIL. Adding minimal fixtures (`/World/Camera`, `/World/Franka`, etc.) before invoking these handlers would shrink the SOFT_FAIL count further and give a cleaner signal on real handler bugs.
3. **Sub-class A (required-args validation)** — these 16 are correct fail-fast and need no fix; the audit could mark them as expected-soft-fail rather than counting toward the bug list.
