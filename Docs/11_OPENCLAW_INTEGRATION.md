# 11 — OpenClaw / NemoClaw Integration

> **Status**: Architecture defined, MCP bridge implemented  
> **Owner**: 10Things, Inc.  
> **Depends on**: Chat orchestrator (§10), Tool executor

---

## 1  Executive Summary

**NemoClaw** (NVIDIA's reference stack) and **OpenClaw** (the underlying agent
framework) operate at a **different layer** than Isaac Assist.  They are
**complementary, not competing**.

| Concern | Isaac Assist | OpenClaw / NemoClaw |
|---------|-------------|-------------------|
| Domain | Isaac Sim scene manipulation, physics, sensors, OmniGraph | General-purpose personal AI assistant |
| Execution | Runs inside Kit process via RPC | Runs in sandboxed container or local machine |
| Tools | 30+ Isaac Sim-specific tool schemas | Bash, browser, file I/O, plugins, skills |
| UX surface | Kit extension panel (in-sim chat) | Telegram, Discord, WhatsApp, Slack, iMessage |
| Inference | 4 LLM providers (OpenAI, Anthropic, Gemini, Ollama) | Routed through OpenShell gateway |
| Security | Governance engine + user approval | Landlock, seccomp, netns sandbox |

**NemoClaw cannot replace Isaac Assist** because it has zero knowledge of USD,
PhysX, OmniGraph, or any Isaac Sim APIs.  It also runs in isolated containers
that cannot access the Kit process.

**But NemoClaw can complement Isaac Assist** by providing:
- Remote control from chat apps (Telegram → OpenClaw → Isaac Assist)
- Sandboxed execution for service deployment
- Centralized inference routing and credential management
- Multi-tool orchestration alongside GitHub, docs, monitoring, etc.

---

## 2  Integration Architecture

```
┌──────────────────────────────────────────────────────────┐
│  User (Telegram / Discord / WhatsApp / Slack / iMessage) │
└──────────────────────┬───────────────────────────────────┘
                       │ message
                       ▼
┌──────────────────────────────────────────────────────────┐
│  OpenClaw Agent                                          │
│  ┌─────────────────┐  ┌──────────────┐                   │
│  │ isaac-sim skill  │  │ other skills │                   │
│  │  (SKILL.md)     │  │ (github,etc) │                   │
│  └────────┬────────┘  └──────────────┘                   │
│           │ MCP / HTTP                                   │
└───────────┼──────────────────────────────────────────────┘
            │ JSON-RPC 2.0
            ▼
┌───────────────────────────────────────┐
│  Isaac Assist MCP Server  :8002       │
│  ┌─────────────────────────────────┐  │
│  │ tools/list → 30+ Isaac Sim tools│  │
│  │ tools/call → tool_executor.py   │  │
│  └─────────────────────────────────┘  │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  Isaac Assist FastAPI  :8000          │
│  Chat orchestrator, RAG, analysis,    │
│  governance, snapshots, finetune      │
└───────────────┬───────────────────────┘
                │ HTTP (Kit RPC)
                ▼
┌───────────────────────────────────────┐
│  Isaac Sim (Kit RPC)  :8001           │
│  USD stage, PhysX, OmniGraph,         │
│  viewport, simulation timeline        │
└───────────────────────────────────────┘
```

### With NemoClaw Sandboxing

When deployed via NemoClaw, the OpenClaw agent runs inside an OpenShell
sandbox.  Isaac Assist runs on the host (or a separate machine) alongside
Isaac Sim.  NemoClaw's L7 proxy routes inference and API calls:

```
OpenClaw (in sandbox) → OpenShell gateway → Isaac Assist MCP :8002
```

Network policy YAML allows egress only to the MCP port:

```yaml
allow:
  - host: "host.docker.internal"
    port: 8002
    protocol: tcp
    reason: "Isaac Assist MCP server"
```

---

## 3  Components

### 3.1  MCP Server (`service/isaac_assist_service/mcp_server.py`)

Converts our existing `ISAAC_SIM_TOOLS` (OpenAI function-calling format) into
MCP-compatible tool definitions.  Supports two transports:

| Transport | Use case | Command |
|-----------|----------|---------|
| **SSE** | Remote agents (OpenClaw, web clients) | `python -m service.isaac_assist_service.mcp_server --transport sse --port 8002` |
| **stdio** | Local MCP clients (Claude Desktop) | `python -m service.isaac_assist_service.mcp_server --transport stdio` |

Methods implemented:
- `initialize` — returns server capabilities and version
- `tools/list` — returns all 30+ Isaac Sim tools
- `tools/call` — dispatches to `tool_executor.execute_tool_call()`
- `resources/list` / `prompts/list` — empty (extensible)
- `ping` — health check

### 3.2  OpenClaw Skill (`skills/isaac-sim/SKILL.md`)

AgentSkills-compatible skill file that teaches OpenClaw agents how to interact
with Isaac Sim.  Includes:
- Capability overview (USD, physics, sensors, OmniGraph, SDG, ROS2)
- HTTP API usage examples
- Common workflow recipes (robot scene setup, deformable creation, SDG)
- Environment variable configuration

Install into an OpenClaw workspace:
```bash
cp -r skills/isaac-sim ~/.openclaw/skills/isaac-sim
```

Or publish to ClawHub for community distribution.

### 3.3  OpenClaw Config (`openclaw.json` snippet)

```json
{
  "skills": {
    "entries": {
      "isaac-sim": {
        "enabled": true,
        "env": {
          "ISAAC_ASSIST_URL": "http://localhost:8000",
          "ISAAC_ASSIST_MCP_URL": "http://localhost:8002"
        }
      }
    }
  },
  "mcp": {
    "servers": {
      "isaac-assist": {
        "url": "http://localhost:8002/mcp/sse"
      }
    }
  }
}
```

---

## 4  Deployment Scenarios

### 4.1  Local Development (no NemoClaw)

1. Start Isaac Sim → Kit RPC on :8001
2. Start Isaac Assist service → FastAPI on :8000
3. Start MCP server → SSE on :8002
4. Install OpenClaw skill → test via Telegram/Discord

### 4.2  NemoClaw Sandbox Deployment

1. Host runs Isaac Sim + Isaac Assist + MCP server
2. `nemoclaw onboard` creates sandboxed OpenClaw instance
3. Network policy allows egress to :8002 only
4. OpenClaw uses isaac-sim skill to control simulator from chat apps
5. NemoClaw handles inference routing (API keys stay on host)

### 4.3  Multi-Agent Workflow

OpenClaw orchestrates multiple tools simultaneously:
- **isaac-sim skill**: manipulate the simulation
- **github skill**: create issues/PRs for USD scene configs
- **claude-code skill**: generate complex Kit extensions
- **monitor skill**: watch simulation metrics, alert on anomalies

---

## 5  What NemoClaw Does NOT Provide

To be clear about the boundaries:

- **No USD scene awareness** — NemoClaw has no concept of prims, stages, or layers
- **No PhysX integration** — cannot configure deformable bodies, rigid bodies, joints
- **No OmniGraph** — cannot create sensor pipelines or controller graphs
- **No viewport access** — cannot capture screenshots or switch cameras
- **No Isaac Sim APIs** — doesn't know about `omni.usd`, `omni.kit`, or `pxr`
- **No Kit process access** — runs in a separate sandboxed container

All of these capabilities live in Isaac Assist.  NemoClaw adds the
infrastructure layer (sandboxing, inference routing, channel messaging) around
our domain-specific tooling.

---

## 6  Future Enhancements

| Enhancement | Description |
|---|---|
| **MCP Resources** | Expose live scene state as MCP resources (prim tree, physics config) |
| **MCP Prompts** | Pre-built prompts for common workflows (robot setup, SDG pipeline) |
| **Webhook notifications** | Push simulation events to OpenClaw (collision, error, completion) |
| **ClawHub publishing** | Publish isaac-sim skill to ClawHub for community access |
| **NemoClaw blueprint** | Custom blueprint with Isaac Assist pre-configured |
