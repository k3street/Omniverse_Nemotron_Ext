# Configuration

Isaac Assist is configured through environment variables defined in `.env` files. No YAML, no TOML -- just key-value pairs.

---

## Config File Priority

The service loads environment files in the following order. **Later files override earlier ones.**

| Priority | File | Purpose |
|----------|------|---------|
| 1 (lowest) | `<repo_root>/.env` | Shared defaults checked into the repo |
| 2 | `service/isaac_assist_service/.env` | Service-specific overrides |
| 3 (highest) | `<repo_root>/.env.local` | Your personal overrides (gitignored) |

!!! tip "Use `.env.local` for API keys"
    Never put real API keys in the repo-level `.env`. Create a `.env.local` file at the repo root -- it is gitignored and takes the highest priority.

---

## .env.example

```bash
# ── LLM Routing ──────────────────────────────────────────────
# Which LLM provider to use: local | cloud | anthropic | openai | grok
LLM_MODE=local

# Model name when LLM_MODE=local (served by Ollama)
LOCAL_MODEL_NAME=qwen3.5:35b

# Model name when LLM_MODE=cloud or anthropic
CLOUD_MODEL_NAME=claude-opus-4-6

# Small model for context compression (blank = use LOCAL_MODEL_NAME)
DISTILLER_MODEL_NAME=

# Vision model for viewport analysis (Gemini Robotics-ER)
VISION_MODEL_NAME=gemini-robotics-er-1.6-preview

# ── API Keys ─────────────────────────────────────────────────
# Only set the keys for providers you actually use
API_KEY_GEMINI=your-gemini-key-here
ANTHROPIC_API_KEY=your-anthropic-key-here
OPENAI_API_KEY=your-openai-key-here
GROK_API_KEY=your-grok-key-here

# ── LiveKit (WebRTC viewport streaming) ─────────────────────
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# ── MCP Server ───────────────────────────────────────────────
MCP_HOST=127.0.0.1
MCP_PORT=8002

# ── ROS Bridge ───────────────────────────────────────────────
ROSBRIDGE_HOST=127.0.0.1
ROSBRIDGE_PORT=9090

# ── OpenAI-compatible API base ───────────────────────────────
OPENAI_API_BASE=https://api.openai.com/v1

# ── Behavior ─────────────────────────────────────────────────
# Skip the approval dialog for all code patches (use with caution)
AUTO_APPROVE=false

# Maximum LLM tool-calling rounds per chat turn
MAX_TOOL_ROUNDS=10

# Contribute anonymized usage data for model improvement
CONTRIBUTE_DATA=false

# ── Assets ───────────────────────────────────────────────────
# Path to Isaac Sim assets (local directory or Nucleus URL)
ASSETS_ROOT_PATH=/home/user/.local/share/ov/pkg/isaac-sim-5.1.0/data/Assets
ASSETS_ROBOTS_SUBDIR=Collected_Robots
```

---

## Setting Details

### LLM Routing

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `LLM_MODE` | `local`, `cloud`, `anthropic`, `openai`, `grok` | `local` | Which LLM backend to use. `local` uses Ollama, others use cloud APIs. |
| `LOCAL_MODEL_NAME` | Any Ollama model tag | `qwen3.5:35b` | Model served by Ollama when `LLM_MODE=local`. |
| `CLOUD_MODEL_NAME` | Any Anthropic/OpenAI model ID | `claude-opus-4-6` | Model used for `cloud` or `anthropic` mode. |
| `DISTILLER_MODEL_NAME` | Model tag or blank | _(blank)_ | Small model for compressing context. Falls back to `LOCAL_MODEL_NAME`. |
| `VISION_MODEL_NAME` | Gemini model ID | `gemini-robotics-er-1.6-preview` | Vision model for viewport analysis and object detection. |

### API Keys

| Variable | Aliases | Provider |
|----------|---------|----------|
| `API_KEY_GEMINI` | `GEMINI_API_KEY` | Google Gemini (vision tools) |
| `ANTHROPIC_API_KEY` | -- | Anthropic Claude |
| `OPENAI_API_KEY` | -- | OpenAI GPT |
| `GROK_API_KEY` | `XAI_API_KEY` | xAI Grok |

!!! warning "API keys are sensitive"
    Store keys in `.env.local` only. The `.env` file is checked into version control.

### LiveKit

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVEKIT_URL` | _(blank)_ | WebSocket URL for LiveKit server (WebRTC viewport streaming). |
| `LIVEKIT_API_KEY` | _(blank)_ | LiveKit API key. |
| `LIVEKIT_API_SECRET` | _(blank)_ | LiveKit API secret. |

### MCP Server

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `127.0.0.1` | MCP protocol server bind address. |
| `MCP_PORT` | `8002` | MCP protocol server port. Exposes tools to external agents via SSE or stdio. |

### ROS Bridge

| Variable | Default | Description |
|----------|---------|-------------|
| `ROSBRIDGE_HOST` | `127.0.0.1` | Rosbridge WebSocket server host. |
| `ROSBRIDGE_PORT` | `9090` | Rosbridge WebSocket server port. |

### Assets

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSETS_ROOT_PATH` | _(blank)_ | Local filesystem path or Nucleus URL to Isaac Sim assets. Example: `omniverse://localhost/NVIDIA/Assets/Isaac/5.1` |
| `ASSETS_ROBOTS_SUBDIR` | `Collected_Robots` | Subdirectory under `ASSETS_ROOT_PATH` containing robot models. |

### Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTO_APPROVE` | `false` | Skip the approval dialog for all code patches. Useful for scripted workflows. |
| `MAX_TOOL_ROUNDS` | `10` | Maximum number of tool-calling rounds the LLM can perform per chat turn. |
| `CONTRIBUTE_DATA` | `false` | Send anonymized usage data for model improvement. |

---

## Runtime Configuration

You can change the LLM mode at runtime without restarting the service:

=== "Chat"

    ```
    "Switch to Claude for the next requests"
    ```

    Isaac Assist will call the settings API to switch the active LLM provider.

=== "API"

    ```bash
    curl -X PUT http://localhost:8000/api/v1/settings/llm_mode \
      -H "Content-Type: application/json" \
      -d '{"mode": "anthropic"}'
    ```

=== "CLI"

    ```bash
    python -m service.isaac_assist_service.main --mode anthropic
    ```
