# Tool Audit — 2026-05-12

**`tool_schemas.py` ISAAC_SIM_TOOLS:** 416 tools
**`tool_executor.py` monolith size:** 35842 lines
**Recovered-state forensic block:** lines 33-1572 (1540 lines — treat as read-only until Phase 13)
**Dead handlers (registered but no schema):** 6

**Status counts:**
- `none_explicit`: 12
- `real`: 404

## `none_explicit` (12)

| name | in DATA | DATA callable | in CODE_GEN | CODE_GEN callable |
|---|---|---|---|---|
| `explain_error` | True | False | False | False |
| `ros2_call_service` | True | False | False | False |
| `ros2_connect` | True | False | False | False |
| `ros2_get_message_type` | True | False | False | False |
| `ros2_get_node_details` | True | False | False | False |
| `ros2_get_topic_type` | True | False | False | False |
| `ros2_list_nodes` | True | False | False | False |
| `ros2_list_services` | True | False | False | False |
| `ros2_list_topics` | True | False | False | False |
| `ros2_publish` | True | False | False | False |
| `ros2_publish_sequence` | True | False | False | False |
| `ros2_subscribe_once` | True | False | False | False |

## `real` (404)

(table omitted for brevity — these are healthy entries)

## Dead handlers (6)

Handlers registered in DATA_HANDLERS or CODE_GEN_HANDLERS but with no matching entry in `ISAAC_SIM_TOOLS`. These tools cannot be called by the LLM (no schema to advertise). Either add a schema or remove the registration.

- `apply_layout_spec_to_scene`
- `commit_layout_spec`
- `query_layout_metric`
- `read_layout_spec`
- `rebind_role`
- `update_layout_spec`

## Allowlist (`tests/fixtures/no_handler_tools.json`)

Schema names whose handler value is intentionally `None` (handled inline by the LLM, special-cased in the orchestrator, or stubbed pending integration work).

- `explain_error`
- `ros2_call_service`
- `ros2_connect`
- `ros2_get_message_type`
- `ros2_get_node_details`
- `ros2_get_topic_type`
- `ros2_list_nodes`
- `ros2_list_services`
- `ros2_list_topics`
- `ros2_publish`
- `ros2_publish_sequence`
- `ros2_subscribe_once`
