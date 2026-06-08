# Isaac Assist — Omniverse Extension & Background Service

> An agentic AI assistant for NVIDIA Isaac Sim that provides LLM-powered scene diagnostics, patch planning, and governance — surfaced through a dockable Omniverse UI panel backed by a local FastAPI service.

---

## Architecture

![Isaac Assist Architecture](isaac_assist_architecture.svg)

![Nemotron Model Stack](nemotron_model_stack.svg)

![NemoClaw Integration](nemoclaw_integration.svg)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Layout](#2-repository-layout)
3. [Background Service Setup](#3-background-service-setup)
4. [LiveKit Voice Infrastructure (Optional)](#4-livekit-voice-infrastructure-optional)
5. [Running the Omniverse Extension](#5-running-the-omniverse-extension)
6. [Verify Everything Is Connected](#6-verify-everything-is-connected)
7. [GUI Smoke Test](#7-gui-smoke-test)
8. [Configuration Reference](#8-configuration-reference)
9. [Feature Modules](#9-feature-modules)
10. [Contributing Data & Helping Train the Model](#10-contributing-data--helping-train-the-model)

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
├── web/
│   └── floor-plan-ui/      # React + Konva multimodal canvas GUI
├── infra/
│   └── livekit/            # Self-hosted LiveKit voice stack (Docker Compose)
├── scripts/                # Utility scripts (doc scraping, data curation)
├── launch_isaac.sh         # Recommended Isaac Sim launcher
├── launch_service.sh       # FastAPI service launcher (interactive mode picker)
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
| `LLM_MODE` | `local` | `local` (Ollama), `anthropic` (Claude), `cloud` (Gemini), `openai`, or `grok` (xAI) |
| `LOCAL_MODEL_NAME` | `qwen3.6:latest` | Model name as shown in `ollama list` |
| `CLOUD_MODEL_NAME` | `claude-opus-4-6` | Cloud model identifier (used by all non-local modes) |
| `ANTHROPIC_API_KEY` | *(empty)* | Required when `LLM_MODE=anthropic` |
| `API_KEY_GEMINI` | *(empty)* | Required when `LLM_MODE=cloud` |
| `OPENAI_API_KEY` | *(empty)* | Required when `LLM_MODE=openai` |
| `GROK_API_KEY` | *(empty)* | Required when `LLM_MODE=grok` |
| `ROSBRIDGE_HOST` | `127.0.0.1` | rosbridge WebSocket host (for live ROS2 tools) |
| `ROSBRIDGE_PORT` | `9090` | rosbridge WebSocket port |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit server URL |

#### Pull the local model (if using `LLM_MODE=local`)

```bash
ollama pull qwen3.6:latest
```

Known-good local Ollama models on the development machine include:

| Model | Use |
|---|---|
| `qwen3.6:latest` | Default local Isaac Assist chat model |
| `nemotron3:33b` | NVIDIA-flavored local coding/reasoning fallback |
| `deepseek-r1:32b` | Deliberate reasoning / audit fallback |

### 3.3 Start the service

```bash
cd /path/to/Omniverse_Nemotron_Ext

# Interactive mode picker (recommended)
./launch_service.sh

# Or pass the LLM mode directly
./launch_service.sh anthropic   # Claude
./launch_service.sh local       # Ollama (local GPU)
./launch_service.sh cloud       # Gemini
./launch_service.sh openai      # OpenAI
./launch_service.sh grok        # xAI Grok
```

The service starts at **`http://localhost:8000`**.  
Interactive API docs are available at **`http://localhost:8000/docs`**.

#### Hot-switch LLM mode at runtime (no restart needed)

```bash
curl -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "local"}'
```

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

The `launch_isaac.sh` script configures the correct ROS2 environment and registers the extension folder automatically. It selects the matching extension harness for the detected Isaac Sim runtime:

| Runtime | Extension path | Notes |
|---|---|---|
| Isaac Sim 5.1 | `exts/isaac_5.1` | Legacy-compatible harness; `KIT_RPC_PORT` can override the default `8001` when co-running with another Kit instance. |
| Isaac Sim 6.0 | `exts/isaac_6.0` | Current active harness for Isaac Sim 6.0 / Isaac Lab 3 workflows. |

```bash
# Launch Isaac Sim with an empty scene
./launch_isaac.sh

# Launch Isaac Sim and open a specific USD file
./launch_isaac.sh /path/to/scene.usd

# Launch Isaac Sim 6.0 with Isaac Assist via the desktop-friendly wrapper
./launch_isaac_assist_desktop.sh

# Launch one canvas/generated scene with Isaac Assist loaded
./launch_canvas_scene.sh /path/to/scene.usd
```

To point at a custom Isaac Sim installation, set `ISAAC_SIM_PATH` in your `.env` file or export it before launching:

```bash
export ISAAC_SIM_PATH=/path/to/your/isaac-sim
./launch_isaac.sh
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
# Expected: {"status":"ok","service":"isaac-assist-backend","llm_mode":"anthropic","model":"claude-opus-4-6"}
```

### Check the Extension UI

Once Isaac Sim is open and the extension is enabled, the **Isaac Assist** panel should appear as a dockable window. If it does not:

- Confirm the service is running (`curl` above).
- Check the Isaac Sim console (**Window → Console**) for extension errors.
- Verify the extension search path is registered (Step 5.2).

---

## 7. GUI Smoke Test

Use this after large merges to verify the visible experience, not just the unit-test layer.

### Floor-plan canvas GUI

```bash
cd web/floor-plan-ui
npm install
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173?session=default_session` and confirm these major surfaces render:

- Header: `Isaac Assist · Floor Plan` and `multimodal canvas v1.0`
- Left tool rail and object palette
- Konva canvas viewport with grid, objects, reach/agency overlays, and snap-guide support
- Properties / layers side panel
- Agent confirmation bar
- Bottom chat ribbon, image import button, viewport import button, and status bar with revision/session/save state

For automated checks, run:

```bash
cd web/floor-plan-ui
npm run build
npm test
```

### Isaac Sim extension GUI

1. Start the service with `./launch_service.sh local`.
2. Start Isaac Sim with `./launch_isaac_assist_desktop.sh`.
3. Verify the **Isaac Assist AI** window opens from the Isaac Sim menu/extension.
4. Use the model selector to choose a local model such as `qwen3.6:latest`.
5. Use **Modes -> Extract layout from scene** to capture the current viewport into a floor-plan proposal.
6. Ask for a simple scene change, then confirm the transcript shows tool activity rather than a plain text-only answer.

The Isaac Sim GUI test requires a live Kit process and GPU. If Kit is not running, keep the verification to the floor-plan GUI plus the backend/unit gates.

---

## 8. Configuration Reference

Configuration is loaded in priority order (later files override earlier ones):

```
.env                  ← repo root defaults (git-ignored)
service/…/.env        ← service-level overrides (git-ignored)
.env.local            ← YOUR personal overrides — highest priority (git-ignored)
```

**Quick start:** Copy the example file and fill in your values:

```bash
cp .env.local.example .env.local
# Edit .env.local with your API keys and asset paths
```

See [`.env.local.example`](.env.local.example) for the full annotated template.

#### Key settings

| Variable | Example | Description |
|---|---|---|
| `LLM_MODE` | `anthropic` | `anthropic`, `openai`, `ollama`, or `gemini` |
| `CLOUD_MODEL_NAME` | `claude-opus-4-6` | Model name for cloud providers |
| `ANTHROPIC_API_KEY` | `sk-ant-xxx` | API key for your chosen provider |
| `ASSETS_ROOT_PATH` | `/home/user/assets` | Path to Isaac Sim USD assets (local or Nucleus) |
| `ISAAC_ASSIST_ASSET_ROOTS` | `/home/user/Desktop/assets` | One or more local USD asset roots for floor-plan build previews; separate multiple roots with `:` on Linux |
| `ASSETS_ROBOTS_SUBDIR` | `Collected_Robots` | Subdirectory containing robot USD files |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit server (optional, for voice/vision) |
| `CONTRIBUTE_DATA` | `false` | Log approved patches for fine-tuning |

#### Cosmos 3 scene proposal flow

Cosmos 3 is treated as a world-model proposal layer, not as a direct Isaac Sim
mutator. A Cosmos Reasoner workflow can turn a photo, screenshot, render, or
prompt into structured scene observations, then submit them to:

```text
POST /api/v1/canvas/{session_id}/cosmos/observe
POST /api/v1/canvas/{session_id}/cosmos/observe_viewport
POST /api/v1/canvas/{session_id}/cosmos/propose
```

`cosmos/observe` calls a configured OpenAI-compatible Cosmos 3 Reasoner
endpoint. If `GEMINI_ROBOTICS_ER_FALLBACK=true` and `GEMINI_API_KEY` is set,
Gemini Robotics-ER can act as a cloud backup that returns the same
`CosmosSceneObservation` contract when the Cosmos endpoint is unavailable.
`cosmos/observe_viewport` first captures the active Isaac Sim viewport
through Kit RPC, then calls the same observation flow. `cosmos/propose` accepts
already-structured observations. The backend
converts the observation into a reviewable `LayoutSpec` proposal.
The floor-plan UI remains the correction/confirmation surface, and the final
build goes through `POST /api/v1/canvas/{session_id}/build`. Builds default to
`dry_run=true`, returning resolved assets and generated Kit code for review;
set `dry_run=false` only when ready to queue the patch into live Isaac Sim. See
[Cosmos 3 to Floor-Plan Flow](docs/architecture/cosmos3-floor-plan-flow.md).

Floor-plan builds can also carry semantic spatial relations such as
`on_top_of`, `inside`, `contains`, and `supports`. The instantiator normalizes
those relations into approximate 3D placement, using support surfaces and
container/interior affordance hints to compute Z offsets. This is the first
step toward rebuilding scenes like "fruit in a bowl on a table" or "a plate in
a microwave on a counter" from a 2D review surface plus vision/Cosmos relation
proposals.

#### Scenario variant campaigns

`LayoutSpec` also carries a `scenario_variants` contract for controlled
multi-scene generation. The floor-plan UI exposes this in the **Scenario
Variants** panel:

- `variant_count` and `seed` control campaign size and repeatability.
- Lighting presets cover studio, warehouse, dome, backlit, and low-angle setups.
- Camera presets cover overhead, robot-view, side-view, and wide-context views.
- Optional actors/circumstances add humans, mobile robots, occlusion,
  distractors, moved targets, and tight-clearance cases.
- Perturbations control pose jitter, rotation jitter, material randomization,
  and sensor noise.
- Validation flags request relation, visibility, and physics checks before
  accepting a generated variant.

Today this is a declarative contract surfaced in **Preview Build** and saved
with the canvas spec. The next execution layer can consume the same contract
locally, through Isaac Automator, or on Brev/DGX to fan out one reviewed
floor-plan into many tested Isaac/Cosmos scenes.

The backend can already expand the saved contract into a deterministic campaign
plan:

```text
POST /api/v1/canvas/{session_id}/campaign/plan
POST /api/v1/canvas/{session_id}/campaign/materialize
```

The response includes a `campaign_id`, per-variant seeds, lighting/camera/actor
and circumstance selections, validation requirements, planned USD paths, and a
`launch_command` for each variant. The floor-plan UI's **Plan campaign** button
flushes pending edits, calls this route, and shows the first launch command.
The **Materialize campaign** button writes the campaign manifest, the saved
`LayoutSpec`, one minimal `.usda` stage per variant, and one Isaac Sim setup
script per variant under `workspace/scenario_campaigns/<campaign_id>/`.

To automatically open one generated or saved USD scene with the extension
already loaded, use:

```bash
./launch_canvas_scene.sh /path/to/scene.usd
```

For a materialized variant, use the launch command emitted in the campaign
manifest. It includes the setup script that applies the generated Kit scene
patch after the minimal stage opens:

```bash
SCENE_SETUP_SCRIPT=workspace/scenario_campaigns/<campaign>/<variant>_setup.py \
  ./launch_canvas_scene.sh workspace/scenario_campaigns/<campaign>/<variant>.usda
```

Or use the local runner, which selects a variant from `campaign_plan.json`,
writes `<variant>_result.json`, tails launcher output into
`<variant>_launch.log`, and starts Isaac Sim with the correct setup script:

```bash
./scripts/run_materialized_variant.sh workspace/scenario_campaigns/<campaign>/campaign_plan.json --index 1

# No Isaac launch; write/inspect the result artifact only
./scripts/run_materialized_variant.sh workspace/scenario_campaigns/<campaign>/campaign_plan.json --index 1 --dry-run
```

This wrapper starts the backend if needed through
`launch_isaac_assist_desktop.sh`, selects Isaac Sim 6.0 by default, registers
`exts/isaac_6.0`, enables `omni.isaac.assist`, and opens the USD through the
startup hook in `launch_isaac.sh`.

Cosmos 3 Reasoner belongs before this materialization step. Use
`/cosmos/observe`, `/cosmos/observe_viewport`, or `/cosmos/propose` to infer
objects, asset hints, and spatial relations from prompts, photos, renders, or
the live Isaac viewport. The floor-plan UI remains the review surface; once the
relations and asset choices are accepted, the campaign planner/materializer
turns that reviewed spec into deterministic variant jobs.

For scale-out, Isaac Assist treats DGX Spark, Brev, and
[isaac-sim/IsaacAutomator](https://github.com/isaac-sim/IsaacAutomator) as
remote capacity providers. See
[Remote Scale Providers](docs/architecture/remote-scale-providers.md) for the
planned extension/backend contract.

For Cosmos 3 Reasoner NIM, prefer a same-LAN DGX Spark when one is available.
That keeps the local Isaac Sim GPU free for rendering and live stage mutation.
The helper below starts the NIM endpoint on Spark or another GPU host:

```bash
export NGC_API_KEY=nvapi-...
COSMOS_NIM_CACHE=$HOME/nim-cache/cosmos3-reasoner \
  COSMOS_NIM_PORT=8081 \
  NIM_MAX_MODEL_LEN=32768 \
  ./scripts/start_cosmos3_reasoner_nim.sh
```

Then point Isaac Assist at the remote endpoint:

```bash
COSMOS3_REASONER_BASE_URL=http://<spark-host-or-ip>:8081/v1
COSMOS3_REASONER_MODEL=nvidia/cosmos3-nano-reasoner
```

#### Asset path examples

```bash
# Local filesystem (recommended — works offline)
ASSETS_ROOT_PATH=/home/user/Desktop/assets
ISAAC_ASSIST_ASSET_ROOTS=/home/user/Desktop/assets

# NVIDIA Omniverse Nucleus server
ASSETS_ROOT_PATH=omniverse://localhost/NVIDIA/Assets/Isaac/5.1

# NVIDIA S3 hosted (requires network access)
ASSETS_ROOT_PATH=https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1
```

#### Gather local assets for floor-plan builds

The floor-plan canvas becomes much more useful when the backend can resolve
reviewed classes to real USD assets instead of primitive placeholder geometry.
For local/offline work, gather Isaac Sim, SimReady, Warehouse, robot, and
customer USD assets under a common folder such as:

```text
/home/<user>/Desktop/assets/
```

Set `ISAAC_ASSIST_ASSET_ROOTS` to that folder before launching the service. The
asset resolver checks explicit user overrides first, then palette references,
then known local asset paths, and finally any `asset_catalog.json` files found
under the configured roots. This is what lets **Preview Build** turn floor-plan
objects such as `conveyor_short`, `bin`, and `cube` into real USD references.

Good asset packs to collect include:

- NVIDIA Isaac Sim robot and sample assets for robot references and baseline props.
- SimReady Containers / Shipping assets for bins, crates, boxes, pallets, and workpieces.
- Warehouse / Digital Twin assets for conveyors, racks, facility equipment, and layout props.
- Project-specific customer assets that should appear in recreated scenes.

Keep `asset_catalog.json` files next to downloaded asset packs when available.
They improve fallback matching when a class does not have a hard-coded local
override yet.

---

## 9. Feature Modules

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
| `/settings/llm_mode` | LLM Mode Switch | `GET` current mode, `PUT` to hot-switch provider |
| `/chat/pipeline/plan` | Pipeline Planner | Template-based multi-phase autonomous scene builder |
| `/finetune` | Fine-tuning Builder | Knowledge Base → training data pipeline |
| `/canvas/{session_id}/cosmos/propose` | Cosmos 3 Adapter | Cosmos Reasoner scene observations → floor-plan `LayoutSpec` proposals |
| `/canvas/{session_id}/cosmos/observe` | Cosmos 3 Runtime | Image/prompt → Cosmos observation → floor-plan `LayoutSpec` proposal |
| `/canvas/{session_id}/cosmos/observe_viewport` | Cosmos 3 Runtime | Active Isaac viewport screenshot → floor-plan `LayoutSpec` proposal |

Full interactive documentation: **`http://localhost:8000/docs`**

### External Chat MCP Floor-Plan Tools

External MCP chat clients can use the floor-plan as the semantic window into
Isaac Sim instead of trying to infer the 3D stage directly. The MCP server
advertises these scene-creation tools:

| MCP Tool | Purpose |
|---|---|
| `create_floor_plan_from_text` | Convert a text scene description into a reviewable `LayoutSpec`. |
| `create_floor_plan_from_image` | Use the configured image/reasoner path to create a floor-plan proposal from an image. |
| `create_franka_physics_pick_scene` | Create a full-physics Franka tabletop pick scene with rigid workpieces, static supports, relation metadata, and a pick-place controller plan. |
| `preflight_isaac_stage_targets` | Read the active Isaac stage identity and confirm caller-specified target prims before graph or robot-control tools run. |
| `search_local_assets` | Search configured USD asset roots such as `/home/kimate/Desktop/assets`. |
| `set_object_asset` | Pin a selected USD asset to a floor-plan object via `metadata.reviewed_asset_ref`. |
| `build_scene_from_floor_plan` | Dry-run or build the current floor-plan into Isaac/Kit generated code. |
| `launch_scene_in_isaac` | Materialize and launch one generated scene variant. Defaults to dry-run. |
| `verify_scene_relations` | Normalize and validate support/containment relations before claiming success. |

Recommended external-client flow: create a floor-plan from text or image,
search and pin real assets where needed, verify relations, dry-run the scene
build, preflight the active Isaac stage and caller-specified target prims, then
launch only after the generated code and relation diagnostics look right.

For a manipulation smoke scene, use `create_franka_physics_pick_scene` with
`motion_backend="auto"` or `"curobo"`. That path creates the physics scene and
returns arguments for the existing `setup_pick_place_controller` live Isaac
tool. `motion_backend="cumotion"` records a MoveIt/cuMotion bridge contract and
validated dry-run plan, while live viewport pickup still routes through the
existing pick-place controller until the opus-runtime cuMotion execution bridge
is connected.

### Recent merged capabilities

The current `master` includes the PR 115-117 integration wave:

- Canonical backlog and template expansion for industrial, ROS2, GR00T, Isaac Lab, safety, SDG, and manipulation workflows.
- Role-based canonical template repairs and sandbox-safety validation for capture-time failures.
- Extended canonical linting, including enum/nested validation and `--validate-sandbox`.
- Coexistence protection for Isaac Sim 5.1 and 6.0 extension harnesses.
- Floor-plan GUI build/test baseline pinned to Vite/Vitest versions that work on Node 18.
- Cosmos 3 proposal adapter for photo/screenshot/prompt-to-floor-plan scene reconstruction.

---

## 10. Contributing Data & Helping Train the Model

Isaac Assist uses a **version-aware knowledge base** to ground the LLM in verified, working code patterns for each Isaac Sim release. Community contributions to this knowledge base directly improve the quality of generated code for everyone — and can ultimately feed into a fine-tuned model purpose-built for Isaac Sim development.

### 9.1 How the Knowledge Base Works

The knowledge base lives in `workspace/knowledge/` and consists of:

| File | Purpose |
|---|---|
| `code_patterns_5.1.0.jsonl` | Verified code snippets for Isaac Sim 5.1 |
| `code_patterns_6.0.0.jsonl` | Verified code snippets for Isaac Sim 6.0 / Isaac Lab 3 |
| `knowledge_5.1.0.jsonl` | Indexed documentation chunks |

When a user asks the LLM to perform an action, the system automatically retrieves relevant patterns for the active Isaac Sim version and injects them into the prompt. This means the LLM sees **working, tested code** rather than hallucinating outdated Kit commands.

### 9.2 Contributing Code Patterns

Code patterns are stored as JSONL (one JSON object per line). Each entry has this format:

```json
{
  "title": "Short descriptive title",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "code": "import omni.usd\nfrom pxr import UsdGeom\n\n# ... working code ...",
  "note": "Brief note about gotchas or why this approach is preferred."
}
```

**To contribute a pattern:**

1. Fork this repository
2. Open the appropriate `workspace/knowledge/code_patterns_<version>.jsonl`
3. Add your entry as a new line at the end of the file
4. Test the code in the matching Isaac Sim version to confirm it works
5. Submit a PR with:
   - The JSONL entry
   - Which Isaac Sim version you tested on
   - A brief description of what the pattern does

**Good pattern contributions:**
- Working code for Isaac Sim APIs that are poorly documented
- Patterns that replace broken or deprecated Kit commands with direct USD/pxr API calls
- Robotics workflows (URDF import, joint drives, articulations)
- Sensor setup (cameras, lidar, IMU)
- OmniGraph node creation for ROS2 bridges
- Physics tuning (solver iterations, collision groups, deformable parameters)

> **Important:** All contributed patterns should use **direct pxr/USD Python APIs** rather than `omni.kit.commands.execute(...)` — Kit commands are unreliable across Isaac Sim versions.

### 9.3 Contributing Documentation

If you have Isaac Sim documentation, tutorials, or workflow notes, you can contribute them to the RAG index:

1. Add `.md` or `.txt` files to `workspace/knowledge/`
2. The indexer will chunk and store them in the full-text search index
3. Submit a PR with your docs and the Isaac Sim version they apply to

### 9.4 Fine-Tuning Data Pipeline

Isaac Assist includes a built-in fine-tuning data pipeline. When the "Contribute Fine-Tuning Data" option is enabled in the extension settings, your chat interactions (prompts + approved code patches) are logged locally in `workspace/finetune_exports/`.

**How this feeds into model training:**

1. **Local collection** — Each approved code execution is recorded as an instruction/response pair
2. **Export** — Use the "Export Training Data" button in settings (or `POST /api/v1/finetune/export`) to generate training-ready JSONL
3. **Community aggregation** — Exported datasets can be contributed via PR to a shared training corpus
4. **Fine-tuning** — The `scripts/tuning/` directory contains tooling for LoRA fine-tuning with [Unsloth](https://github.com/unslothai/unsloth) and GGUF export for local deployment via Ollama

The long-term goal is a community-trained model that understands Isaac Sim's full API surface — every contributed pattern and training pair brings that closer.

### 9.5 Contribution Guidelines

- **One pattern per line** — keep the JSONL format strict (no trailing commas, valid JSON)
- **Test before submitting** — every code pattern must be verified in the stated Isaac Sim version
- **No API keys or secrets** — the secret redactor catches most, but double-check your contributions
- **Version-tag your PR** — indicate which Isaac Sim version(s) your contribution targets
- **Prefer minimal examples** — patterns should be self-contained and focused on one concept

---

> **Spec Reference:** See `Docs/00_INDEX.md` for the full ecosystem specification, data models, and phase roadmap.
