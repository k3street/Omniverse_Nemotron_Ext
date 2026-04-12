# Isaac Assist — Omniverse Extension & Background Service

> An agentic AI assistant for NVIDIA Isaac Sim that provides LLM-powered scene diagnostics, patch planning, and governance — surfaced through a dockable Omniverse UI panel backed by a local FastAPI service.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Layout](#2-repository-layout)
3. [Background Service Setup](#3-background-service-setup)
4. [LiveKit Voice Infrastructure (Optional)](#4-livekit-voice-infrastructure-optional)
5. [Running the Omniverse Extension](#5-running-the-omniverse-extension)
6. [Verify Everything Is Connected](#6-verify-everything-is-connected)
7. [Configuration Reference](#7-configuration-reference)
8. [Feature Modules](#8-feature-modules)

---

## 1. Prerequisites

| Requirement | Version |
|---|---|
| NVIDIA Isaac Sim | 5.1 or 6.0 |
| Python (system host) | 3.10+ |
| Docker + Docker Compose | Latest |
| Ollama *(local LLM mode)* | Latest |
| Git | Any |

> **GPU Note:** Isaac Sim requires an NVIDIA RTX GPU. Ensure your drivers and CUDA toolkit are up to date before proceeding.

---

## 2. Repository Layout

```
Omniverse_Nemotron_Ext/
├── exts/
│   ├── isaac_5.1/          # Omniverse Extension (Isaac Sim 5.1)
│   └── isaac_6.0/          # Omniverse Extension (Isaac Sim 6.0)
├── service/
│   └── isaac_assist_service/   # FastAPI backend service
│       ├── main.py             # App entry point
│       ├── .env.example        # Configuration template
│       └── ...                 # Feature modules (chat, analysis, planner, etc.)
├── infra/
│   └── livekit/            # Self-hosted LiveKit voice stack (Docker Compose)
├── scripts/                # Utility scripts (doc scraping, data curation)
├── launch_isaac_fixed.sh   # Recommended Isaac Sim launcher
└── requirements.txt        # Python backend dependencies
```

---

## 3. Background Service Setup

The FastAPI service must be running **before** you launch Isaac Sim. The extension UI communicates with it over `localhost:8000`.

### 3.1 Install dependencies

```bash
cd /path/to/Omniverse_Nemotron_Ext
pip install -r requirements.txt
```

### 3.2 Configure the environment

```bash
cp service/isaac_assist_service/.env.example service/isaac_assist_service/.env
# Open .env and set your preferred LLM mode and API keys
```

#### Key settings in `.env`

| Variable | Default | Description |
|---|---|---|
| `LLM_MODE` | `local` | `local` (Ollama) or `cloud` (Gemini) |
| `LOCAL_MODEL_NAME` | `cosmos-reason-2:latest` | Model name as shown in `ollama list` |
| `CLOUD_MODEL_NAME` | `gemini-robotics-er-1.5` | Google GenAI model identifier |
| `API_KEY_GEMINI` | *(empty)* | Required when `LLM_MODE=cloud` |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit server URL |

#### Pull the local model (if using `LLM_MODE=local`)

```bash
ollama pull cosmos-reason-2:latest
```

### 3.3 Start the service

```bash
cd /path/to/Omniverse_Nemotron_Ext
uvicorn service.isaac_assist_service.main:app --host 0.0.0.0 --port 8000 --reload
```

The service starts at **`http://localhost:8000`**.  
Interactive API docs are available at **`http://localhost:8000/docs`**.

---

## 4. LiveKit Voice Infrastructure (Optional)

Skip this section if you do not need voice/audio features.

```bash
cd infra/livekit
docker compose up -d
```

This starts:
- **LiveKit server** on ports `7880` (WebSocket), `7881` (HTTP), `7882/udp` (WebRTC)
- **Redis** on port `6379` (required by LiveKit)

To stop:

```bash
docker compose down
```

---

## 5. Running the Omniverse Extension

### 5.1 Using the launch script (recommended)

The `launch_isaac_fixed.sh` script configures the correct ROS2 environment and registers the extension folder automatically.

```bash
# Launch Isaac Sim with an empty scene
./launch_isaac_fixed.sh

# Launch Isaac Sim and open a specific USD file
./launch_isaac_fixed.sh /path/to/scene.usd
```

To point at a custom Isaac Sim installation, set `ISAAC_SIM_PATH` before launching:

```bash
export ISAAC_SIM_PATH=/path/to/your/isaac-sim
./launch_isaac_fixed.sh
```

The script auto-detects architecture (`x86_64` or `aarch64`) and sets default paths accordingly:

| Architecture | Default Path |
|---|---|
| x86_64 | `~/isaac-sim/isaac-sim-standalone-5.1.0-linux-x86_64` |
| aarch64 (Jetson / DGX Spark) | `~/Documents/Github/isaacsim/_build/linux-aarch64/release` |

### 5.2 Manual extension loading (Isaac Sim Extension Manager)

If you prefer to load the extension manually inside Isaac Sim:

1. Open Isaac Sim.
2. Go to **Window → Extensions**.
3. Click the **⚙ gear icon** → **Add Extension Search Path**.
4. Add the path to the appropriate `exts/` folder:
   - Isaac Sim 5.1: `<repo_root>/exts/isaac_5.1`
   - Isaac Sim 6.0: `<repo_root>/exts/isaac_6.0`
5. Search for **`omni.isaac.assist`** and toggle it **ON**.

---

## 6. Verify Everything Is Connected

### Health-check the backend service

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"isaac-assist-backend"}
```

### Check the Extension UI

Once Isaac Sim is open and the extension is enabled, the **Isaac Assist** panel should appear as a dockable window. If it does not:

- Confirm the service is running (`curl` above).
- Check the Isaac Sim console (**Window → Console**) for extension errors.
- Verify the extension search path is registered (Step 5.2).

---

## 7. Configuration Reference

All backend configuration lives in `service/isaac_assist_service/.env`.  
See `.env.example` for the full annotated template.

```
service/isaac_assist_service/
├── .env.example   ← copy this to .env
└── .env           ← your local environment (git-ignored)
```

---

## 8. Feature Modules

The FastAPI service exposes the following REST API modules, all prefixed under `/api/v1/`:

| Endpoint Prefix | Module | Description |
|---|---|---|
| `/chat` | Chat Orchestration | Multi-turn LLM conversations with the stage context |
| `/fingerprint` | Environment Fingerprint | Hardware, Omniverse version & active extension telemetry |
| `/snapshots` | Snapshot Manager | USD stage serialization and rollback |
| `/retrieval` | Source Registry RAG | Omniverse doc scraping + vector retrieval |
| `/analysis` | Stage Analyzer | Scene constraint checks and validator packs |
| `/plans` | Patch Planner | Repair plan generation and execution engine |
| `/governance` | Approval Engine | Dry-run UI dialogs for user-governed USD edits |
| `/settings` | Configuration Options | Model switching, Ollama pull triggers, API keys |
| `/finetune` | Fine-tuning Builder | Knowledge Base → training data pipeline |

Full interactive documentation: **`http://localhost:8000/docs`**

---

> **Spec Reference:** See `Docs/00_INDEX.md` for the full ecosystem specification, data models, and phase roadmap.
