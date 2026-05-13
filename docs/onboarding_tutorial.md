# Isaac Assist Onboarding Tutorial

## Welcome

Isaac Assist is an AI-powered assistant for NVIDIA Omniverse Isaac Sim. It exposes a conversational interface backed by hundreds of tool handlers — letting engineers build, simulate, and train robotic systems through natural-language requests. Whether you are setting up a warehouse pick-and-place cell or tuning domain randomisation for synthetic data generation, Isaac Assist translates intent into Isaac Sim API calls without requiring deep USD or Python expertise.

## Prerequisites

Before starting, ensure you have the following installed and configured:

- **Isaac Sim 5.x** — install via NVIDIA Omniverse Launcher or the container image `nvcr.io/nvidia/isaac-sim:5.0`.
- **Python 3.11** — the service and all tests run on CPython 3.11; earlier versions are not supported.
- **Anthropic API key** — set `ANTHROPIC_API_KEY` in your environment. The service authenticates to Claude on every LLM call; without a valid key the service starts but every chat message returns an auth error.
- **uvicorn** — `pip install uvicorn` or install the full service requirements via `pip install -r service/requirements.txt`.

## Step 1: First Connection

Launch Isaac Sim (or let it run headless), then start the Isaac Assist service:

```bash
cd service
uvicorn isaac_assist_service.main:app --host 0.0.0.0 --port 8000 --reload
```

Open the chat UI at `http://localhost:3000` (or send requests directly to `http://localhost:8000/chat`). On first load the sidebar lists all registered tools. Verify that tools like `scene_summary`, `create_prim`, and `list_all_prims` appear — this confirms the service found Isaac Sim's Kit RPC endpoint on port `33080`.

## Step 2: Your First Scene

Ask Isaac Assist to create a simple object to confirm the round-trip works:

> "Create a Cube called TestCube at position (0, 0, 50) cm."

Isaac Assist calls `create_prim` with `prim_type=Cube` and `prim_path=/World/TestCube`, then follows up with `scene_summary` to confirm the prim exists. You should see the cube appear in the Isaac Sim viewport. If the viewport is blank, run `sim_control` with action `play` first to initialise the physics scene.

## Step 3: Adding a Robot

With a scene open, add a robot arm using the wizard:

> "Add a Franka Panda robot to the scene."

Isaac Assist invokes `robot_wizard` (or `assemble_robot` for a lower-level path), downloads the USD asset from Nucleus, and anchors the robot at the world origin. After the call returns, `list_all_prims` confirms the robot articulation root is present under `/World/Franka`. You can then inspect joint limits with `get_joint_limits` or check the kinematic state with `get_kinematic_state`.

## Step 4: Running a Pick-and-Place

Once a robot and a target object are in the scene, set up a pick-and-place pipeline:

> "Set up a pick-and-place task: pick the TestCube and place it in a bin at (0.5, 0.2, 0) m."

Isaac Assist calls `setup_pick_place_with_vision` which configures a `PickPlaceController`, attaches a camera for object detection, and wires up the motion planner. Send `sim_control play` to run a single cycle. The controller status is returned via `get_gripper_state` and `get_joint_positions` after each step. Expect a full pick-and-place cycle to complete in 3–8 simulated seconds depending on trajectory complexity.

## Step 5: Domain Randomisation

To generate training-grade synthetic data, apply a DR preset:

> "Apply the 'warehouse_lighting' domain randomisation preset and preview it."

Isaac Assist calls `apply_dr_preset` with the named preset, then `preview_dr` to render a thumbnail showing the randomised lighting and material variation. You can inspect the active randomisers with `analyze_randomization` and tune individual parameters with `suggest_dr_ranges`. Run `benchmark_sdg` to measure throughput before starting a full dataset generation run.

## Step 6: Workflows

For multi-step operations that span several tool calls and require human checkpoints, use the workflow engine:

> "Start the assemble_pick_place_cell workflow."

Isaac Assist calls `start_workflow` with the named template, which enqueues a sequence of checkpointed steps. After each checkpoint the system pauses and calls `approve_workflow_checkpoint` — you confirm or revise before the next step executes. Use `get_workflow_status` to inspect progress and `cancel_workflow` to abort. Workflow state is persisted so you can resume after an Isaac Sim restart.

## Troubleshooting

**Kit not running / connection refused on port 33080** — Isaac Sim must be open with the Isaac Assist extension enabled. In the Isaac Sim Extensions panel search for `omni.isaac.assist` and enable it. Then restart the service.

**Wrong port** — The service reads `KIT_RPC_PORT` from the environment (default `33080`). If you launched Isaac Sim with a custom port, set `export KIT_RPC_PORT=<port>` before starting uvicorn.

**USD path not found** — Nucleus paths like `omniverse://localhost/NVIDIA/Assets/...` require a running Nucleus server. Start it via Omniverse Launcher or set `NUCLEUS_SERVER` to your server address. For offline work, use local file paths (`/home/<user>/assets/...`) and pass them explicitly in the chat.

**Anthropic API auth error** — Confirm `ANTHROPIC_API_KEY` is exported in the same shell that runs uvicorn. The service does not read `.env` files by default; source one manually or use `python-dotenv` in a wrapper script.

**Tool call returns "handler not found"** — The service registers handlers at startup. If a tool listed in the sidebar returns this error, the handler module failed to import (check the uvicorn log for `ImportError`). Most commonly this is caused by a missing Isaac Sim Python package that is only available inside the Sim environment.

## Next Steps

- **Operator guide** — `docs/user/session_notebook.md` covers advanced session management, multi-robot setups, and the CAS history system.
- **Arena benchmarks** — run `arena_leaderboard` to see how your configuration compares against reference baselines, or launch a full benchmark with `run_arena_benchmark`.
- **Canonical templates** — browse available scene templates with `list_scene_templates` to find pre-built industrial cell configurations ready to customise.
- **API reference** — every tool is documented in `docs/architecture/`; the tool registry is introspectable via `list_extensions` at runtime.
- **Phase release notes** — `docs/release/` tracks what changed in each phase so you can find new capabilities as they land.
