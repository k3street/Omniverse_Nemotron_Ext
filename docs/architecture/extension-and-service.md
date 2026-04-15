# Extension & Service Details

This page provides a deeper look at the two sides of Isaac Assist: the Kit extension and the FastAPI service. It covers what each component does internally and how to extend them.

---

## Extension (omni.isaac.assist)

The extension lives in `exts/omni.isaac.assist/` and runs inside the Isaac Sim Kit process.

### Chat UI Panel

Built with `omni.ui`, the chat panel is a dockable window containing:

- **Input bar** -- text field with Send button. Ctrl+Enter to send.
- **Chat bubbles** -- alternating user/assistant message display with markdown rendering.
- **Approval dialogs** -- code patches shown with syntax highlighting. Execute/Reject buttons.
- **Render previews** -- inline viewport thumbnails in assistant responses.
- **Context chip** -- shows the currently selected prim above the input bar so the user knows what the AI sees.

### Context Collectors

On every chat turn, the extension gathers context and sends it with the message:

| Collector | Data | When |
|-----------|------|------|
| **Selection** | Selected prim path, type, schemas, authored attributes, world transform | Every send |
| **Viewport** | 256px thumbnail as base64 PNG | Every send |
| **Console** | Recent carb log entries (errors/warnings) | On request |
| **Stage Tree** | Prim hierarchy (path, type, children count) | On request |
| **Physics State** | Articulation joint positions/velocities | On request |

### Kit RPC Server

An HTTP server on port 8001 that accepts execution requests from the FastAPI service:

```python
# Simplified handler
@app.post("/execute")
async def execute_code(request):
    code = request.code
    # Execute inside Kit's main thread
    omni.kit.app.get_app().post_task(lambda: exec(code, globals()))
```

All code execution goes through `omni.kit.commands` so every mutation is undoable with Ctrl+Z.

### Executors

Specialized modules that handle different categories of Kit operations:

| Executor | Responsibility |
|----------|---------------|
| **USD Executor** | `CreateMeshPrimCommand`, `DeletePrims`, `SetAttribute`, `AddReference` |
| **OmniGraph Executor** | `og.Controller.edit()` -- node creation, wiring, attribute setting |
| **Material Executor** | MDL shader creation, material binding |
| **Physics Executor** | `ApplyAPISchema` for RigidBody/Collision/Mass, PhysX scene config |
| **Sim Control** | `omni.timeline` play/pause/stop/step/reset |
| **Import Executor** | URDF/MJCF importers, asset library resolution |

### Extending the Extension

To add a new executor:

1. Create a new module in the extension's executor directory.
2. Register the executor's endpoints with the Kit RPC server.
3. Add corresponding tool schemas in the service side (see below).

!!! note "Extension development requires Kit SDK"
    You need Isaac Sim installed to develop and test extension code. The extension cannot run standalone.

---

## Service (FastAPI)

The service lives in `service/isaac_assist_service/` and runs as a standalone Python process.

### Module Map

```
service/isaac_assist_service/
├── main.py                 # FastAPI app, router registration
├── config.py               # Singleton Config (reads .env files)
├── mcp_server.py           # MCP protocol server (SSE + stdio)
├── chat/
│   ├── routes.py           # /api/v1/chat/* endpoints
│   ├── orchestrator.py     # Multi-turn chat manager, tool-calling loop
│   ├── intent_router.py    # Message intent classification (8 intents)
│   └── tools/
│       ├── tool_schemas.py # 50+ tool definitions (OpenAI format)
│       ├── tool_executor.py# Dispatch: code-gen handlers vs. data handlers
│       ├── patch_validator.py # Pre-flight code validation rules
│       └── kit_tools.py    # Async HTTP calls to Kit RPC (port 8001)
├── governance/
│   ├── routes.py           # /api/v1/governance/* endpoints
│   ├── policy_engine.py    # Risk classification (low/medium/high)
│   └── models.py           # GovernanceConfig, AuditEntry, ApprovalDecision
├── knowledge/
│   └── knowledge_base.py   # JSONL-backed experiential memory
├── snapshots/
│   ├── routes.py           # /api/v1/snapshots/* endpoints
│   └── manager.py          # Pre-execution snapshots (max 50, auto-pruned)
├── settings/
│   ├── routes.py           # /api/v1/settings/* endpoints
│   └── manager.py          # Runtime .env read/write
├── retrieval/
│   └── routes.py           # /api/v1/retrieval/* (RAG, spec lookup)
├── analysis/
│   └── routes.py           # /api/v1/analysis/* (stage analysis)
├── planner/
│   └── routes.py           # /api/v1/plans/* (patch planner)
├── fingerprint/
│   └── routes.py           # /api/v1/fingerprint/* (env detection)
└── finetune/
    └── routes.py           # /api/v1/finetune/* (dataset export)
```

### Chat Orchestrator

The core of the service. Located in `chat/orchestrator.py`, it manages:

1. **Session state** -- conversation history, per-session tool results.
2. **Tool-calling loop** -- sends the user message + tool schemas to the LLM, processes tool calls, feeds results back, repeats up to `MAX_TOOL_ROUNDS` (default 10).
3. **Code generation** -- when a tool call requires Kit execution, the orchestrator routes to the appropriate code generator.
4. **Response assembly** -- combines LLM text, code patches, and data results into the final response.

### Intent Router

Classifies each user message into one of 8 intents before the LLM sees it:

| Intent | Examples | Handling |
|--------|----------|----------|
| `general_query` | "What is PhysX?" | LLM answers directly, no tools |
| `patch_request` | "Create a cube" | Tool-calling loop with code gen |
| `diagnosis` | "Why is the robot falling?" | Debug tools + explain_error |
| `scene_query` | "What's in the scene?" | Data tools (list_prims, summary) |
| `simulation` | "Play the simulation" | Sim control tool |
| `import` | "Import a Franka" | Import tool |
| `configuration` | "Switch to Claude" | Settings API |
| `export` | "Export the scene" | Export tool |

### Tool Executor

Dispatches tool calls into two categories:

- **`CODE_GEN_HANDLERS`** -- produce a Python code string that will be sent to Kit RPC for execution (e.g., `create_prim`, `apply_api_schema`). The code goes through patch validation and governance before execution.
- **`DATA_HANDLERS`** -- return a data dictionary that goes back to the LLM for further reasoning (e.g., `list_all_prims`, `scene_summary`, `ros2_list_topics`). No Kit execution needed.

### Patch Validator

Pre-flight validation rules that catch common mistakes:

| Rule | What It Catches |
|------|-----------------|
| OmniGraph type check | Incompatible port types in graph connections |
| PhysX collision check | RigidBody without CollisionAPI |
| USD path validation | Invalid prim paths, missing parents |
| Destructive operation check | `DeletePrims` on `/World` root |

### Governance Engine

Every code patch is classified by risk:

| Risk | Example | Approval |
|------|---------|----------|
| **Low** | Create a cube, set an attribute | Auto-approved (unless `AUTO_APPROVE=false`) |
| **Medium** | Apply physics schema, import robot | Shown for review |
| **High** | Delete prims, run arbitrary script | Requires explicit user approval |

All decisions are recorded in the audit log.

### Knowledge Base

A JSONL file storing experiential knowledge:

- API patterns for the detected Isaac Sim version.
- Code examples from past successful executions.
- Error-fix pairs learned from the diagnosis loop.
- Supports deduplication and compaction via `POST /api/v1/chat/compact_knowledge`.

### MCP Server

Exposes Isaac Assist tools to external agents via the Model Context Protocol:

- **SSE transport** -- for web-based agent clients.
- **stdio transport** -- for CLI-based agents.
- Runs on port 8002 by default (`MCP_HOST`, `MCP_PORT`).

---

## Developing New Tools

Adding a new tool requires changes in both the service and (usually) the extension.

### Service Side

1. **Define the schema** in `chat/tools/tool_schemas.py`:

    ```python
    {
        "type": "function",
        "function": {
            "name": "my_new_tool",
            "description": "Does something useful.",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "..."},
                },
                "required": ["param1"],
            },
        },
    },
    ```

2. **Add the handler** in `chat/tools/tool_executor.py`:

    ```python
    # For code generation:
    CODE_GEN_HANDLERS["my_new_tool"] = generate_my_new_tool_code

    # For data retrieval:
    DATA_HANDLERS["my_new_tool"] = handle_my_new_tool_data
    ```

3. **Add validation rules** in `chat/tools/patch_validator.py` if the tool generates code that needs pre-flight checks.

### Extension Side

If the tool requires new Kit functionality:

1. Add a new executor or extend an existing one.
2. Register the new RPC endpoint on port 8001.
3. Test with the L3 integration test suite.

### Testing

```bash
# Add test vectors for code gen
# In tests/test_code_generators.py, add to _TEST_VECTORS

# Add validation tests
# In tests/test_patch_validator.py, add a new test class

# Run
pytest tests/test_code_generators.py -v
pytest tests/test_patch_validator.py -v
```
