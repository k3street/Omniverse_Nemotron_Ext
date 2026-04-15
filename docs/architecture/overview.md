# Architecture Overview

Isaac Assist uses a **two-tier architecture**: an Extension running inside the Isaac Sim Kit process, and a FastAPI Service running as a separate Python process.

---

## System Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  Isaac Sim (Kit Process)                                      │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  omni.isaac.assist Extension (10Things, Inc.)           │  │
│  │                                                          │  │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │  │
│  │  │ Chat UI  │  │ Context      │  │ Executors        │  │  │
│  │  │ Panel    │  │ Collectors   │  │                  │  │  │
│  │  │          │  │              │  │ • USD Executor   │  │  │
│  │  │ • Input  │  │ • Selection  │  │ • OmniGraph Exec │  │  │
│  │  │ • Bubbles│  │ • Viewport   │  │ • Material Exec  │  │  │
│  │  │ • Approve│  │ • Console    │  │ • Physics Exec   │  │  │
│  │  │ • Render │  │ • Stage Tree │  │ • Sim Control    │  │  │
│  │  │  Previews│  │ • Physics    │  │ • Import Exec    │  │  │
│  │  └──────────┘  └──────────────┘  └──────────────────┘  │  │
│  │                       │                    ▲             │  │
│  │  Kit RPC Server :8001 │ ◄──────────────────┘             │  │
│  └───────────────────────┼──────────────────────────────────┘  │
│                          │                                      │
└──────────────────────────┼──────────────────────────────────────┘
                           │ HTTP (JSON)
┌──────────────────────────┼──────────────────────────────────────┐
│  FastAPI Orchestration Service :8000                             │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐│
│  │ Intent Router│→ │ Tool Picker  │→ │ LLM (Ollama/Cloud)    ││
│  │              │  │              │  │                        ││
│  │ • chat       │  │ • USD tools  │  │ Tool-calling w/ schema ││
│  │ • create mesh│  │ • OG tools   │  │ for each Isaac Sim op  ││
│  │ • add sensor │  │ • material   │  │                        ││
│  │ • diagnose   │  │ • sim ctrl   │  └────────────────────────┘│
│  │ • explain    │  │ • web lookup │                             │
│  └──────────────┘  │ • console    │  ┌────────────────────────┐│
│                     └──────────────┘  │ Product Spec Fetcher  ││
│                                       │ camera/sensor db +    ││
│                                       │ live web scrape       ││
│                                       └────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

---

## The Two Tiers

### Extension (port 8001)

The `omni.isaac.assist` extension runs **inside** the Isaac Sim Kit process. It has direct access to all `omni.*`, `pxr.*`, and `isaacsim.*` APIs. It provides:

- **Chat UI panel** -- dockable `omni.ui` window with input, chat bubbles, and approval dialogs.
- **Context collectors** -- read the selected prim, viewport screenshot, console logs, stage tree, and physics state on every chat turn.
- **Kit RPC server** -- HTTP server on port 8001 that accepts code patches from the service and executes them inside the Kit process using `omni.kit.commands` (making every action undoable with Ctrl+Z).
- **Executors** -- specialized execution modules for USD, OmniGraph, materials, physics, simulation control, and robot import.

### Service (port 8000)

The FastAPI service runs as a **separate process** outside Isaac Sim. It handles all LLM interaction, tool selection, code generation, and governance. It provides:

- **Chat orchestrator** -- multi-turn session management with tool-calling loop.
- **Intent router** -- classifies user messages into intents (general query, patch request, diagnosis, etc.).
- **Tool schemas** -- 50+ tool definitions in OpenAI function-calling format.
- **Tool executor** -- dispatches tool calls to code generators (produce Python code) or data handlers (return structured data).
- **LLM providers** -- Ollama (local), Anthropic, OpenAI, Gemini, Grok.
- **Governance engine** -- risk classification (low/medium/high), approval enforcement, audit logging.
- **Knowledge base** -- JSONL-backed experiential memory for API patterns and code examples.
- **Snapshot manager** -- pre-execution state snapshots with rollback capability.
- **MCP server** -- Model Context Protocol server (SSE + stdio) exposing tools to external agents.

---

## Request Flow

A single user message follows this path:

1. **User types** a request in the chat panel (Extension).
2. **Extension sends** the message + context (selected prim, viewport screenshot) to the Service via `POST /api/v1/chat/message`.
3. **Intent Router** classifies the message (e.g., "create an object" vs. "fix an error" vs. "general question").
4. **Orchestrator** sends the message + tool schemas to the LLM.
5. **LLM selects tools** and provides parameters via function calling.
6. **Tool Executor** dispatches each tool call:
    - **Code-gen tools** (e.g., `create_prim`) produce a Python code string.
    - **Data tools** (e.g., `list_all_prims`) return structured data.
7. **Patch Validator** checks generated code against safety rules (OmniGraph wiring, PhysX constraints, USD conventions).
8. **Governance Engine** classifies risk level. High-risk patches require explicit user approval.
9. **Service returns** the response to the Extension, including the code patch and risk level.
10. **Extension shows** the code in an approval dialog. User clicks **Execute** or **Reject**.
11. **On approval**, the Extension sends the code to the Kit RPC server (port 8001), which executes it inside the Kit process.
12. **Execution result** is logged back to the Service via `POST /api/v1/chat/log_execution` for the learning loop.

---

## Key Design Decisions

### Why two processes?

Isaac Sim's Kit process is single-threaded for USD operations. Running LLM inference (which can take seconds) inside Kit would freeze the UI. The separate service keeps the UI responsive and allows the LLM to be swapped (local/cloud) without touching the extension.

### Why Kit RPC instead of direct API calls?

All USD mutations must happen on Kit's main thread. The RPC bridge serializes code patches and executes them via `exec()` inside Kit, wrapped in `omni.kit.commands` for undo support.

### Why function calling instead of free-form code generation?

Structured tool schemas constrain the LLM's output to valid operations with typed parameters. The tool executor then generates the actual Python code from these parameters, ensuring correctness. The LLM never writes raw Python directly -- it picks tools and fills in parameters.

---

## Supporting Systems

| System | Purpose |
|--------|---------|
| **Knowledge Base** | JSONL-backed memory storing API patterns, code examples, and past solutions. Queried by the LLM via `lookup_knowledge`. |
| **Audit Log** | Every code patch execution is logged with timestamp, code, approval decision, and result. |
| **Snapshot Manager** | Takes pre-execution scene snapshots. Supports rollback to any of the last 50 snapshots. |
| **Patch Validator** | Pre-flight rules that catch common mistakes before the code reaches Kit (e.g., missing collision on physics bodies). |
| **Fine-tune Exporter** | Collects (message, tool_calls, result) tuples for domain-specific model fine-tuning. |
