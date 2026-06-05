# Isaac Assist v1 — Release Announcement

## What is Isaac Assist?

Isaac Assist is an agentic AI assistant for NVIDIA Isaac Sim that surfaces
LLM-powered scene diagnostics, patch planning, and governance through a
dockable Omniverse UI panel backed by a local FastAPI service. Users interact
with it through natural-language chat; the system resolves intent to one of
416 registered tool calls, executes them against the live Kit session, and
returns structured results — all without leaving the simulator. The assistant
supports multiple LLM backends (Anthropic Claude, local Ollama, OpenAI, Gemini,
xAI Grok) and is designed to be swapped at runtime without a service restart.

## Headline features

- **Register-callback dispatch architecture (Phase 9)** — the monolithic
  `tool_executor.py` (formerly 35 k lines) is replaced by a
  `register_handlers()` entry point that walks 17 themed handler modules at
  import time; each theme is independently testable.
- **17 themed handler modules** — arena, rendering, teleop, vision, ros2,
  physics, scene_blueprints, pick_place, robot, sensors, diagnostics,
  training, workflow, resolve, scene_authoring, multimodal, and industrial.
- **Pydantic validation layer (Phase 10 partial)** — 416 auto-generated
  `Args` models in `handlers/_models.py` provide a named-field contract for
  every tool; permissive mode at v1 with strict enforcement queued for v2.
- **416 tool schemas** — `tool_schemas.py` is the single source of truth for
  the full tool surface; schema entries drive both the MCP client and the
  internal dispatcher.
- **Patch validator pipeline** — `patch_validator.py` runs 22 blocking rules
  on every emitted code patch before execution reaches Kit; violations are
  surfaced as structured `ConstraintViolation` objects.
- **Multimodal / canvas foundation (Block 1B)** — Phase 105+ multimodal
  scaffolding integrates the announcement and demo pipeline into the service
  so future video and image demos load from structured manifests.
- **Workflow lifecycle + governance** — `start_workflow`, `approve_workflow_checkpoint`,
  `cancel_workflow`, and `get_workflow_status` give a full async lifecycle for
  multi-step autonomous operations; all destructive edits gate on a
  dry-run approval dialog.

## What's new in this release

- **Epoch I foundation refactor (Phase 8 + Phase 9)** — 29 migration waves
  reduced `tool_executor.py` from 35 842 to 2 418 lines (−93.3 %). All
  handler definitions now live in `handlers/<theme>.py`; zero handler bodies
  remain in the dispatch core.
- **Phase 9 dispatch swap** — `handlers/_dispatch.py:register_handlers()` is
  the sole registration entry; inline `DATA_HANDLERS["X"]` assignments are
  retired. Dispatch remains byte-identical pre/post.
- **Phase 10 partial — Pydantic models** — `scripts/gen_handler_models.py`
  generates `handlers/_models.py` (416 models) from tool schemas; a regen
  check is wired in `.pre-commit-config.yaml`.
- **Phase 12 — circular-import guard** — 20 AST-based tests detect import
  cycles; `scripts/diag_imports.py` produces a graphviz dependency graph.
- **Phase 17 + 17b — tools-hygiene pre-commit** — lint scripts block new
  handler definitions appearing in `tool_executor.py`.
- **Phase 18 — handler architecture doc** rewritten to describe the live
  shape (see `docs/architecture/handlers.md`).
- See `docs/2026-05-12-night-1-progress.md` for the full wave-by-wave log.

## Install

```bash
# 1. Clone
git clone https://github.com/k3street/Omniverse_Nemotron_Ext.git
cd Omniverse_Nemotron_Ext

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure environment
cp service/isaac_assist_service/.env.example service/isaac_assist_service/.env
# Set LLM_MODE and your API key in .env

# 4. Start the backend service
./launch_service.sh anthropic   # or: local / cloud / openai / grok

# 5. Launch Isaac Sim 5.1 with the extension
./launch_isaac.sh
# Then enable omni.isaac.assist in Window → Extensions
```

Requirements: NVIDIA Isaac Sim 5.1 or 6.0, Python 3.10+, NVIDIA RTX GPU.

## Credits

Isaac Assist v1 was designed and built by **Anton Bjornsson** with
**Anthropic Claude Opus 4.7** and **Anthropic Claude Sonnet 4.6** as
co-developers — the Epoch I refactor (100+ commits, −93 % lines) was
executed autonomously overnight by the Claude Code agent. The system runs
on **NVIDIA Isaac Sim** (Kit 106 / PhysX 5) and makes use of the following
open-source libraries: FastAPI, Uvicorn, Pydantic v2, aiohttp, python-dotenv,
beautifulsoup4, livekit-agents, and ros-mcp.

## Try it

Start a chat session in the Isaac Assist panel and try:

```
"Spawn a Franka Emika Panda at the origin and show its workspace."
```
Expected tool chain: `import_robot` → `show_workspace` → `get_joint_limits`

```
"Set up a pick-and-place cell with a conveyor feeding a bin."
```
Expected tool chain: `create_conveyor` → `create_bin` → `setup_pick_place_controller`
→ `setup_pick_place_with_vision`

## Demo videos

Suggested demo recordings (to be produced post-release):

- **Pick-and-place cell** — Franka + conveyor + bin, full cycle with vision
  gating and patch-validator in the loop.
- **Scene blueprint round-trip** — `generate_scene_blueprint` →
  `validate_scene_blueprint` → `build_scene_from_blueprint` on a warehouse
  floor plan.
- **RL training launch** — `create_isaaclab_env` → `launch_training` →
  `get_training_status` → `checkpoint_training` driving IsaacLab from chat.

## Roadmap

The full phase plan lives in `specs/phase_metadata.yaml` (106 phases tracked)
and the human-readable summary is at `docs/spec_coverage.md`. Landed phases
include the complete Epoch I dispatch refactor (Phases 7–9, 10 partial, 12,
17–18), industrial bridge handlers (Modbus, OPC-UA, MQTT Sparkplug, OpenPLC),
and 20 yrkesroll canonical templates. Queued for daytime-supervised work:
Phase 10 full (405 handler signature changes requiring live Kit RPC), Phase 11
(patch-validator rule classes), Phase 13 (recovered-state block deletion), and
Phase 15 (workflow stateful migration). The overall goal is a community-trained
model fine-tuned on verified Isaac Sim workflows — every chat session and
contributed code pattern brings that closer.
