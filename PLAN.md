# Isaac Assist — Full-Control Project Plan

**Author:** 10Things, Inc. — [www.10things.tech](http://www.10things.tech)  
**Extension:** `omni.isaac.assist`  
**Target:** Isaac Sim 5.1 / 6.0 on NVIDIA Omniverse  
**Date:** April 2026  
**Last Updated:** April 18, 2026

---

## Vision

Complete natural-language control over every Isaac Sim capability — USD authoring, physics, OmniGraph, materials, sensors, viewport, console, debugging — through a dockable AI chat panel backed by an LLM orchestration service. If you can do it in Isaac Sim with menus, scripts, or the property panel, you can say it in English.

---

## Current State (What Already Works)

| Capability | Status |
|---|---|
| Dockable chat panel (omni.ui) | ✅ Running |
| FastAPI backend (port 8000) | ✅ Running |
| Kit RPC bridge (port 8001) | ✅ Running |
| Viewport capture → base64 PNG | ✅ Running |
| Console/carb log capture | ✅ Running |
| Stage tree & prim property read | ✅ Running |
| Selected-prim inspector | ✅ Running |
| USD patch executor (exec in Kit) | ✅ Running |
| Swarm patch planner (coder/critic/QA agents) | ✅ Running |
| LiveKit WebRTC viewport streaming | ⚠️ Scaffold only |
| Physics articulation state read | ✅ Running |
| Governance / approval dialogs | ✅ Running |
| Snapshots / rollback | ✅ Running |
| Selection-aware chat context — auto-inject prim into turn | ✅ Running |
| OmniGraph node creation — `og.Controller.edit()` via NL | ✅ Running |
| Deformable/soft-body mesh — cloth, sponge, rubber, gel, rope | ✅ Running |
| Product spec lookup — sensor_specs.jsonl fuzzy match | ✅ Running |
| Material authoring — OmniPBR/MDL create & assign | ✅ Running |
| Full USD code gen & execution — arbitrary pxr Python | ✅ Running |
| Console error diagnosis — read errors + explain_error tool | ✅ Running |
| Simulation control — play/pause/stop/step/reset + physics params | ✅ Running |
| Import pipeline — URDF/MJCF/USD/asset_library (20+ robots) | ✅ Running |
| Multi-viewport / camera switching | ✅ Running |
| Session reset / new scene button | ✅ Running |
| Multi-provider LLM (Anthropic/OpenAI/Gemini/Ollama/Grok) | ✅ Running |
| LLM mode hot-switching (CLI `--mode` + API `/switch_mode`) | ✅ Running |
| Nucleus browse & download tools (asset library integration) | ✅ Running |
| Asset catalog search (5,178 indexed assets, fuzzy match + tags) | ✅ Running |
| Golden code patterns (49 verified patterns + auto-capture pipeline) | ✅ Running |
| Robot name normalization (alias mapping for 20+ robots) | ✅ Running |
| Patch validator (12 regex rules for legacy API detection) | ✅ Running |
| Context distiller (20 tool categories, smart tool pre-selection) | ✅ Running |
| Per-tool call throttling (configurable limits per turn) | ✅ Running |
| Secret redaction + audit trail (governance module) | ✅ Running |
| Pipeline planner (multi-step plan generation + execution) | ✅ Running |
| ROS2 bridge tools (13 tools: topic pub/sub, service calls, node info via rosbridge) | ✅ Running |
| ROS2 camera topics (4 cameras × 3 topics = 12 live via OmniGraph) | ✅ Running |
| RViz2 auto-launch (topic discovery → config gen → process management) | ✅ Running |
| MCP server (auto-converts all tools to JSON-RPC 2.0 over SSE/stdio) | ✅ Running |

---

## What's Missing (Gap Analysis)

### Partial — Implemented But Incomplete

| Gap | Status | What's Missing |
|---|---|---|
| **Viewport visual feedback** — show LLM what the user sees per turn | ⚠️ Tool-call only | LLM must call `capture_viewport` explicitly; not auto-injected per chat turn |
| **Replicator / SDG** — synthetic data generation from chat | ⚠️ Basic only | Annotators + BasicWriter work; no domain randomization, no custom annotators |
| **ROS2 bridge control** — topic pub/sub from chat | ✅ Working | 13 ROS2 tools fully connected via rosbridge WebSocket (port 9090); topic list/pub/sub/echo, service list/call, node list/details all functional |
| **Undo/redo narration** — LLM explains what it did, user can Ctrl+Z | ⚠️ Undo works | All mutations go through `omni.kit.commands` (Ctrl+Z); no per-action LLM narration |
| **Fine-tune data capture** — every chat→action pair stored for training | ⚠️ Patches only | Code patches logged via `/log_execution`; tool-call chat→action pairs NOT captured |
| **Stage Analyzer** — scene diagnosis & validation | ⚠️ 1/8 validators | Only `SchemaConsistencyRule` implemented; missing: import health, material/physics mismatch, articulation integrity, sensor completeness, ROS bridge readiness, IsaacLab sanity, performance warnings |
| **Knowledge base feedback loop** — learn from plan outcomes | ⚠️ Schema only | `knowledge_base.py` exists but `query_by_error_pattern()` returns `[]`; no negative memory; auto-capture not wired to plan outcomes |
| **Source Registry / document retrieval** — real NVIDIA docs | ⚠️ Mock only | Only hardcoded mock doc in `index_mock_doc()`; no real web scraping, no actual NVIDIA docs loaded into FTS index |
| **Environment fingerprint caching** — thread-safe system info | ⚠️ Brittle | Uses mutable `global` variable, not thread-safe under concurrent requests; no durable cache |
| **Vision tools** — object detection, bounding boxes in viewport | ⚠️ Stub only | `_handle_vision_*` handlers all return empty `{}`; detect_objects, get_bounding_boxes non-functional |
| **Patch planner ↔ Stage Analyzer** — plan from real findings | ⚠️ Disconnected | Plan generation accepts `mock_findings` parameter; not integrated with actual Stage Analyzer output |
| **Chat file upload** — 📎 button backend handling | ⚠️ UI only | Button defined in extension UI, no backend route processes uploaded files |

### Missing — Not Yet Implemented

| Gap | Priority |
|---|---|
| **NL scene builder** — "build a kitchen with my robot" → full spatial layout from asset catalog | P0 |
| **Asset catalog search** — fuzzy-match local/Nucleus assets by name, tag, type | ✅ Done |
| **RViz2 integration** — auto-configured launch with topic discovery + scene-named configs | ✅ Done |
| **IsaacLab RL training** — env scaffolding, training launch, live metrics from chat | P0 |
| **Motion planning (RMPflow/Lula)** — "move arm to this pose" via `isaacsim.robot_motion` | P0 |
| **GPU-batched cloning** — replace naive clone loop with `isaacsim.core.cloner` | P0 |
| **Telemetry & evaluation pipeline** — metrics, events, dashboards, diagnose time, fix rate, rollback rate | P0 |
| **Real document scraping & indexing** — scrape NVIDIA Isaac Sim docs by version, load into FTS index | P0 |
| **7 Stage Analyzer validator packs** — import health, material/physics, articulation, sensor, ROS bridge, IsaacLab, perf | P0 |
| **Knowledge negative memory** — store what failed and why, not just successes | P1 |
| **Image-to-USD pipeline** — upload photo → generate 3D mesh → place in scene | P1 |
| **Chat file upload** — 📎 button for images, OBJ, GLB, USD files | P1 |
| **XR teleoperation** — WebRTC hand-tracking → joint control via LiveKit | P1 |
| **IsaacLab-Arena environments** — composable multi-robot benchmark arenas | P1 |
| **Debug draw visualization** — draw paths, waypoints, bounding boxes in viewport via `omni.isaac.debug_draw` | P1 |
| **Cortex behavior trees** — reactive pick-and-place via `isaacsim.cortex.framework` | P1 |
| **Robot assembler** — "attach this gripper to the arm" via `isaacsim.robot_setup.assembler` | P1 |
| **Gain tuner** — auto-tune articulation PD gains via `isaacsim.robot_setup.gain_tuner` | P1 |
| **Occupancy map gen** — 2D walkable-area maps via `isaacsim.asset.gen.omap` | P1 |
| **RL policy deployment** — load trained policies via `isaacsim.robot.policy.examples` | P1 |
| **URDF importer migration** — switch legacy `omni.isaac.urdf` to `isaacsim.asset.importer.urdf` | P1 |
| **Replicator DR nodes** — use built-in DR OmniGraph nodes via `isaacsim.replicator.domain_randomization` | P1 |
| **Manipulator abstractions** — gripper/end-effector wrappers via `isaacsim.robot.manipulators` | P1 |
| **Eureka reward generation** — LLM-authored reward functions with iterative refinement | P2 |
| **IsaacSimZMQ bridge** — ZMQ pub/sub for external process comms | P2 |
| **GR00T N1 policy eval** — deploy foundation policies and evaluate in sim | P2 |
| **Grasp editor** — author grasp poses via `isaacsim.robot_setup.grasp_editor` | P2 |
| **Camera inspector** — inspect/modify all camera properties via `isaacsim.util.camera_inspector` | P2 |
| **Mesh merge utility** — combine meshes into one prim via `isaacsim.util.merge_mesh` | P2 |
| **Conveyor belt gen** — create conveyor systems via `isaacsim.asset.gen.conveyor` | P2 |
| **Grasping SDG workflow** — full grasping data gen via `isaacsim.replicator.grasping` | P2 |
| **Robot wizard** — guided import + config via `isaacsim.robot_setup.wizard` | P2 |
| **Wheeled robot utils** — differential drive, nav via `isaacsim.robot.wheeled_robots` | P2 |
| **Surface gripper** — suction/magnetic gripper modeling via `isaacsim.robot.surface_gripper` | P2 |
| **ROS2 TF viewer** — show transform tree in viewport via `isaacsim.ros2.tf_viewer` | P2 |
| **IsaacAutomator cloud deploy** — one-click cloud launch of headless Isaac Sim | P3 |

---

## Extension Audit (Isaac Sim 5.1 API)

Audit of all `isaacsim.*` extensions available vs. currently used.

### Currently Used (10 extensions)

| Extension | Usage |
|---|---|
| `omni.usd` | Pervasive — stage access, prim creation, attribute reads |
| `omni.kit.commands` | All undoable mutations (CreateMeshPrim, DeletePrims, ApplyAPISchema, etc.) |
| `omni.timeline` | Play/pause/stop simulation control |
| `omni.ui` | Entire chat panel UI |
| `omni.ext` | Extension lifecycle (on_startup/on_shutdown) |
| `omni.graph.core` / `og.Controller` | OmniGraph creation and wiring |
| `omni.replicator.core` | Basic Replicator/SDG pipeline |
| `omni.isaac.sensor` | LidarRtx, IMUSensor, ContactSensor |
| `omni.isaac.urdf` | Legacy URDF import (needs migration to `isaacsim.asset.importer.urdf`) |
| `isaacsim.core.prims` | SingleArticulation for physics state reads |

### High-Value Unused Extensions

| Extension | Impact | Effort | Phase |
|---|---|---|---|
| `isaacsim.core.cloner` | GPU-batched env cloning, collision filtering — critical for RL | Low | 8A |
| `isaacsim.robot_motion.motion_generation` | RMPflow/Lula motion planning — "move arm to pose" | Medium | 8B |
| `isaacsim.cortex.framework` | Behavior-tree reactive control — pick-and-place without raw joints | Medium | 8C |
| `omni.isaac.debug_draw` | Runtime debug viz (lines, spheres, arrows in viewport) | Low | 8A |
| `isaacsim.asset.gen.omap` | 2D occupancy map generation for navigation | Low | 8A |
| `isaacsim.robot_setup.assembler` | Compose multi-body robots from parts | Medium | 8D |
| `isaacsim.robot_setup.gain_tuner` | Auto-tune PD gains for articulations | Low | 8D |
| `isaacsim.robot_setup.grasp_editor` | Author grasp poses interactively | Medium | 8D |
| `isaacsim.robot_setup.wizard` | Guided robot import + configuration | Medium | 8D |
| `isaacsim.replicator.domain_randomization` | Pre-built DR OmniGraph nodes | Low | 7B |
| `isaacsim.replicator.grasping` | Full grasping SDG workflow | Medium | 7B |
| `isaacsim.robot.policy.examples` | RL policy inference deployment | Low | 7A |
| `isaacsim.robot.manipulators` | Gripper/end-effector abstractions | Medium | 8C |
| `isaacsim.robot.surface_gripper` | Suction/magnetic gripper modeling | Low | 8C |
| `isaacsim.robot.wheeled_robots` | Differential drive, nav utilities | Low | 8E |
| `isaacsim.util.merge_mesh` | Combine meshes — cleanup after image-to-3D gen | Low | 6B |
| `isaacsim.util.camera_inspector` | Inspect/modify all camera properties | Low | 8A |
| `isaacsim.asset.gen.conveyor` | Conveyor belt creation for warehouse sims | Low | 8E |
| `isaacsim.sensors.physics` | Physics-based contact, effort, IMU sensors | Low | 8A |
| `isaacsim.sensors.rtx` | RTX lidar/radar APIs | Low | 8A |
| `isaacsim.ros2.tf_viewer` | TF transform tree visualization | Low | 4B |
| `isaacsim.asset.importer.urdf` | Modern URDF importer (replace legacy) | Low | 3 |
| `isaacsim.asset.importer.mjcf` | Modern MJCF importer | Low | 3 |

---

## Architecture

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
│  │  │   Previews│ │ • Physics    │  │ • Import Exec    │  │  │
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

## Phase 0 — Rebrand & Selection-Aware Context (Week 1)

**Goal:** Every chat message automatically carries the selected prim + viewport screenshot so the LLM always knows what the user is looking at.

### Tasks

- [ ] **0.1** Update `extension.toml` for both 5.1 and 6.0:
  - `authors = ["10Things, Inc."]`
  - `repository = "http://www.10things.tech"`
  - `description` updated to reflect full-control vision
- [ ] **0.2** Wire selection listener (`omni.usd.get_context().get_selection()`) into chat — on every `Send`, auto-attach:
  - Selected prim path(s)
  - Prim type + applied schemas
  - Authored attributes (filtered to top 20)
  - World transform
- [ ] **0.3** Auto-capture viewport thumbnail (256px) on every chat turn, send as base64 to service `/api/v1/chat/message` context payload
- [ ] **0.4** Extend service `/api/v1/chat/message` to accept `context.selected_prim` and `context.viewport_b64` fields and inject them into the LLM system prompt
- [ ] **0.5** Show a "Context:" chip above the input bar displaying the selected prim path, so the user knows what the AI sees

---

## Phase 1 — Core Isaac Sim Tool Functions (Weeks 2–4)

**Goal:** Expose every major Isaac Sim operation as a callable LLM tool so the model can chain them in response to natural language.

### 1A — USD Code Generation & Execution

- [ ] **1A.1** Create `tools/usd_tools.py` in the service with tool-call schemas:
  - `create_prim(path, type, attributes)` — create any prim type
  - `set_attribute(prim_path, attr_name, value)` — modify any attribute  
  - `delete_prim(prim_path)` — remove a prim
  - `add_reference(prim_path, usd_url)` — add USD reference
  - `apply_api_schema(prim_path, schema_name)` — apply physics/etc API
  - `run_usd_script(python_code)` — arbitrary pxr Python (sandboxed)
- [ ] **1A.2** Kit RPC endpoints for each tool — service calls Kit :8001 which executes inside the Kit process with `omni.kit.commands` (undoable)
- [ ] **1A.3** Every tool execution wrapped in governance approval flow — show code in chat, user clicks "Execute" or "Reject"

### 1B — Deformable / Custom Mesh Creation

- [ ] **1B.1** Tool: `create_deformable_mesh(prim_path, soft_body_type, params)`
  - `soft_body_type`: cloth, sponge, rubber, gel, rope
  - Internally: create UsdGeom.Mesh → apply `PhysxSchema.PhysxDeformableBodyAPI` or `PhysxSchema.PhysxDeformableSurfaceAPI`
  - Set solver params (vertex count, self-collision, damping) from type presets
- [ ] **1B.2** Presets file `deformable_presets.json`:
  ```json
  {
    "cloth": {"simulation_hexahedral_resolution": 10, "self_collision": true, "damping": 0.1},
    "sponge": {"simulation_hexahedral_resolution": 5, "youngs_modulus": 1000, "poissons_ratio": 0.3},
    "rubber": {"youngs_modulus": 50000, "dynamic_friction": 0.8}
  }
  ```
- [ ] **1B.3** User flow: click mesh in viewport → type "make this cloth" → LLM calls `create_deformable_mesh` with the selected prim path → approval → physics schema applied

### 1C — OmniGraph Node Creation from Product Specs

- [ ] **1C.1** Tool: `create_omnigraph(prim_path, graph_type, config)`
  - `graph_type`: action_graph, push_graph, lazy_graph
  - Node creation via `og.Controller.edit()` API
- [ ] **1C.2** Tool: `add_sensor_to_prim(prim_path, sensor_type, params)`
  - `sensor_type`: camera, lidar, imu, contact_sensor, rtx_lidar
  - Internally: create sensor prim → attach OmniGraph nodes → wire outputs
  - Params populated from product spec lookup (next section)
- [ ] **1C.3** Tool: `lookup_product_spec(product_name_or_url)`
  - Fetches manufacturer spec sheets (e.g., Intel RealSense D435i, Velodyne VLP-16)
  - Parses FOV, resolution, range, FPS into structured JSON
  - Populates sensor creation params automatically
- [ ] **1C.4** Product spec database (`workspace/knowledge/sensor_specs.jsonl`):
  - Ship with 20+ common robotics sensors pre-indexed
  - Supports user adding custom specs via chat: "add my custom camera: 1920x1080 90deg FOV 30fps"
- [ ] **1C.5** User flow: click prim → "add a RealSense D435i camera here" → LLM calls `lookup_product_spec("RealSense D435i")` → gets FOV/resolution → calls `add_sensor_to_prim` → OmniGraph wired → approval → executed

### 1D — Material Authoring

- [ ] **1D.1** Tool: `create_material(material_path, shader_type, params)`
  - `shader_type`: OmniPBR, OmniGlass, OmniSurface, MDL custom
  - Sets albedo, roughness, metallic, normal map, opacity
- [ ] **1D.2** Tool: `assign_material(prim_path, material_path)`
- [ ] **1D.3** User flow: "make this box look like brushed steel" → LLM creates OmniPBR with metallic=0.95, roughness=0.3 → assigns to selected prim

---

## Phase 2 — Console & Debugging Intelligence (Weeks 5–6)

**Goal:** The LLM can read, understand, and fix errors in the Isaac Sim console and debug output.

### Tasks

- [ ] **2.1** Tool: `get_console_errors(last_n, min_level)` — already have `get_recent_logs`, expose as LLM tool
- [ ] **2.2** Tool: `get_physics_errors()` — read PhysX error stream separately (collision mesh issues, solver failures)
- [ ] **2.3** Tool: `explain_error(error_text)` — LLM diagnoses the error using Isaac Sim documentation context (RAG from knowledge base)
- [ ] **2.4** Tool: `fix_error(error_text)` — LLM proposes a USD patch to resolve the issue, routed through approval
- [ ] **2.5** Auto-diagnosis mode: poll console every 5s, if new errors appear, show a non-intrusive notification: "⚠ 3 new errors — ask me to fix them"
- [ ] **2.6** Tool: `get_debug_info()` — collects GPU utilization, FPS, physics step time, renderer stats

---

## Phase 3 — Simulation Control & Import (Weeks 7–8)

**Goal:** Start/stop/step simulation, import robot models, all from chat.

### Tasks

- [ ] **3.1** Tool: `sim_control(action)` — play, pause, stop, step(n), reset
  - Uses `omni.timeline` and `SimulationContext` API
- [ ] **3.2** Tool: `import_robot(file_path_or_url, format)` — URDF, MJCF, USD, OnShape URL
  - Leverages Isaac Sim's native importers (`isaacsim.asset.importer.urdf`)
- [ ] **3.3** Tool: `set_physics_params(gravity, time_step, solver_iterations)` — scene-level physics config
- [ ] **3.4** Tool: `teleport_prim(prim_path, position, rotation)` — move anything
- [ ] **3.5** Tool: `clone_prim(source_path, target_path, count)` — duplicate prims in a grid/line pattern
- [ ] **3.6** Tool: `set_joint_targets(articulation_path, joint_name, position, velocity)` — direct joint control

---

## Phase 4 — Advanced Capabilities (Weeks 9–12)

### 4A — Replicator / Synthetic Data Generation

- [ ] **4A.1** Tool: `configure_sdg(annotators, num_frames, output_dir)` — set up Replicator pipeline
- [ ] **4A.2** Tool: `randomize_domain(randomizers)` — lighting, texture, pose randomization
- [ ] **4A.3** Tool: `run_sdg(num_frames)` — execute data generation

### 4B — ROS2 Bridge

- [x] **4B.1** Tool: `ros2_publish(topic, msg_type, data)` — publish to a ROS2 topic
- [x] **4B.2** Tool: `ros2_subscribe(topic, msg_type)` — subscribe and show data in chat
- [x] **4B.3** Tool: `ros2_list_topics()` — show active topics
- [x] **4B.4** Tool: `launch_rviz2(extra_topics, fixed_frame)` — auto-discover topics, generate config (named after USD scene), launch RViz2 process
- [x] **4B.5** Tool: `stop_rviz2()` — stop managed RViz2 instance (SIGTERM → SIGKILL)

### 4C — Camera & Viewport Control

- [ ] **4C.1** Tool: `set_viewport_camera(camera_prim_path)` — switch active viewport camera
- [ ] **4C.2** Tool: `create_render_product(camera_path, resolution)` — create offscreen render
- [ ] **4C.3** "Show me what the robot's wrist camera sees" → captures from that camera, embeds in chat

### 4D — Scene Interrogation

- [ ] **4D.1** Tool: `list_all_prims(filter_type)` — "show me all cameras in the scene"
- [ ] **4D.2** Tool: `measure_distance(prim_a, prim_b)` — spatial queries
- [ ] **4D.3** Tool: `check_collisions(prim_path)` — collision mesh validation
- [ ] **4D.4** Tool: `scene_summary()` — high-level natural language scene description

---

## Phase 5 — Polish & Fine-Tuning Loop (Weeks 13–16)

- [ ] **5.1** Every tool invocation logged as `(user_message, context, tool_calls, result)` tuple to `workspace/finetune_exports/`
- [ ] **5.2** Unsloth fine-tune pipeline on collected data → domain-specific Isaac Sim model
- [ ] **5.3** UI polish: inline code syntax highlighting, image previews in chat, progress bars for long operations
- [ ] **5.4** Multi-turn memory: LLM remembers what it did 10 turns ago, references previous actions
- [ ] **5.5** Keyboard shortcuts: Ctrl+Shift+A to open chat, Ctrl+Enter to send
- [ ] **5.6** Batch operations: "add physics to all meshes in /World/Objects"
- [ ] **5.7** Template library: "set up a tabletop manipulation scene" → pre-built multi-step plan

---

## Phase 6 — Intelligent Scene Builder & Image-to-USD Pipeline (Weeks 17–22)

### 6A — Scene Blueprint Builder (NL → Full Scene)

**Goal:** The user describes a scene in plain English, and the system designs a spatial blueprint, resolves assets from the local catalog / Nucleus, places everything with physically correct positioning, self-QAs, and lets the user iterate.

**User flow:** _"Build a house and put my Unitree G1 robot with kitchen items"_

```
User types: "Build a house and put my Unitree G1 robot with kitchen items"
→ Phase 1 — Blueprint: LLM generates a spatial layout plan (rooms, dimensions, object list)
→ Phase 2 — Asset Resolution: catalog_search matches assets (walls, floor, table, sink, G1 robot, etc.)
→ Phase 3 — Placement: spatial planner positions objects logically (table on floor, sink against wall,
   robot in walkable area, items ON surfaces not floating)
→ Phase 4 — Self-QA: physics validator checks collisions, overlaps, floating objects, scale mismatches
→ Phase 5 — Confirm: user sees the blueprint summary + preview, can approve/reject/modify per object
→ Phase 6 — Iterate: "move the robot to the living room" or "add a fridge next to the sink"
```

#### Tasks

- [ ] **6A.1** Tool: `catalog_search(query, asset_type, limit)` — fuzzy-match against local asset catalog + Nucleus browser
  - Searches `ASSETS_ROOT_PATH` recursively for USD/USDZ files by name/tag
  - Searches Nucleus if `NUCLEUS_SERVER` configured
  - Returns ranked list: `[{path, name, type, thumbnail_b64, bounding_box}]`
  - Caches directory listing for fast subsequent queries
- [ ] **6A.2** Tool: `generate_scene_blueprint(description, available_assets)` — LLM-powered spatial planner
  - Input: user description + asset catalog results
  - Output: structured blueprint JSON:
    ```json
    {
      "scene_name": "Kitchen with G1 Robot",
      "rooms": [{"name": "kitchen", "bounds": [6, 4, 3], "objects": [...]}],
      "objects": [
        {"asset": "/assets/kitchen/table.usd", "position": [2, 1, 0], "rotation": [0, 0, 0], "purpose": "kitchen table"},
        {"asset": "/assets/Collected_Robots/unitree_g1.usd", "position": [3, 2, 0], "rotation": [0, 90, 0], "purpose": "main robot"}
      ],
      "spatial_rules": ["robot has 1m clearance on all sides", "items placed ON surfaces, not floating"]
    }
    ```
  - Uses LLM with spatial reasoning prompt + retrieved furniture/object dimensions
- [ ] **6A.3** Tool: `validate_scene_blueprint(blueprint)` — physics & spatial QA
  - Checks: bounding box overlaps, objects below ground, floating objects, scale mismatches (2m tall chair?), physics scene missing
  - Returns: `{valid: bool, issues: [{object, problem, suggestion}]}`
  - Auto-fixes trivial issues (snap to ground, remove clipping overlaps)
- [ ] **6A.4** Tool: `build_scene_from_blueprint(blueprint, dry_run)` — executes the plan
  - `dry_run=true`: generates code patches but doesn't execute — shows user a summary
  - `dry_run=false`: creates all prims, adds references, positions, applies physics
  - Each object is an individual code patch for granular approve/reject
  - Groups related items (e.g., "kitchen cluster") for batch approval
- [ ] **6A.5** Blueprint preview card in chat UI
  - Shows object list with asset names, positions, and status (resolved / missing / ambiguous)
  - Per-object approve/reject/modify buttons
  - "Build All" button to approve the entire plan at once
  - Highlights unresolved assets with suggestions
- [ ] **6A.6** Iterative refinement loop
  - After scene is built, user can say: "move the table 1m left", "replace the chair with a stool", "add another robot"
  - System tracks the blueprint state, applies delta changes, re-validates
  - Supports undo per blueprint step (not just per prim)
- [ ] **6A.7** Asset thumbnail indexer
  - On first run, renders 128px thumbnails of each USD asset in the catalog
  - Stores in `workspace/knowledge/asset_thumbnails/` as PNGs
  - Used for blueprint preview cards and LLM visual grounding

### 6B — Image-to-USD Model Generation

**Goal:** The user uploads an image through the chat panel, and the system generates a USD 3D model from it using an image-to-3D pipeline, then places it in the scene.

**User flow:** _User clicks the image upload button, selects a photo of a coffee mug → system generates a 3D mesh → places it in the scene_

```
User clicks 📎 button → selects coffee_mug.jpg
User types: "Create a 3D model from this image and place it at 0, 0, 1"
→ Phase 1 — Upload: image sent to service as base64 in context
→ Phase 2 — Generation: image-to-3D model generates mesh (TripoSR / InstantMesh / Trellis)
→ Phase 3 — USD Conversion: mesh → OBJ/GLB → USD via omni.kit.asset_converter or trimesh
→ Phase 4 — Placement: model imported as USD reference at specified position
→ Phase 5 — Refinement: user can say "make it bigger", "rotate it", "add physics to it"
```

#### Tasks

- [ ] **6B.1** Chat UI: file upload button (📎 icon) left of the text input
  - Accepts: `.jpg`, `.png`, `.webp`, `.obj`, `.glb`, `.usd`, `.usda`, `.usdz`
  - Images: sent as base64 to service `context.uploaded_image`
  - 3D files: sent as file path to service `context.uploaded_model`
  - Shows thumbnail preview chip above input bar
- [ ] **6B.2** Service: image-to-3D generation endpoint
  - `POST /api/v1/generate/image_to_3d` — accepts base64 image, returns mesh
  - Backend options (configurable via `IMAGE_TO_3D_BACKEND` env var):
    - `triposr` — TripoSR (local, runs on GPU, fast single-image)
    - `instantmesh` — InstantMesh (local, higher quality, slower)
    - `trellis` — Trellis (local, state-of-art)
    - `api` — External API endpoint (user provides `IMAGE_TO_3D_API_URL`)
  - Returns: `{mesh_path: "/tmp/generated/model.glb", format: "glb", vertices: N}`
- [ ] **6B.3** Tool: `generate_3d_from_image(image_b64, output_name, backend)` — LLM-callable tool
  - Invokes the generation pipeline
  - Converts output to USD (via `omni.kit.asset_converter` or trimesh + UsdGeom.Mesh)
  - Stores generated USD in `workspace/generated_models/{output_name}.usd`
  - Returns path for subsequent placement
- [ ] **6B.4** Tool: `import_generated_model(model_path, prim_path, position, scale)` — place the generated model
  - Adds as USD reference at the target prim path
  - Sets transform (position, rotation, scale)
  - Optionally applies default material (OmniPBR with albedo from original image)
- [ ] **6B.5** USD conversion utilities
  - GLB → USD via `omni.kit.asset_converter` (preferred, runs in Kit)
  - OBJ → USD via trimesh + pxr (fallback, runs in service)
  - Automatic UV unwrapping and normal recalculation for generated meshes
  - Scale normalization (generated models often need rescaling to real-world units)
- [ ] **6B.6** Image preprocessing
  - Background removal (rembg or SAM) before feeding to image-to-3D
  - Multi-view estimation for better 3D reconstruction when single image is ambiguous
  - Resolution normalization to model's expected input size

---

## Phase 7 — Isaac Sim Ecosystem Integration (Weeks 23–30)

### 7A — IsaacLab Reinforcement Learning (Tier 1)

**Goal:** Scaffold RL training environments, launch training runs, and stream live metrics — all from the chat panel. Integrates with [IsaacLab](https://github.com/isaac-sim/IsaacLab) (6.9k ★).

#### Tasks

- [ ] **7A.1** Tool: `create_isaaclab_env(task_name, num_envs, env_spacing, params)` — scaffold an IsaacLab `DirectRLEnv` or `ManagerBasedRLEnv` from natural language
  - Generates `__init__.py`, `env_cfg.py`, reward/observation/action config
  - Links to the robot and objects already in the current scene
- [ ] **7A.2** Tool: `launch_training(task, algo, num_steps, checkpoint_dir)` — kick off `rsl_rl`, `rl_games`, or `skrl` training
  - Spawns subprocess or submits to cloud (via IsaacAutomator, see 7H)
  - Streams stdout / TensorBoard scalars back to the chat panel
- [ ] **7A.3** Tool: `show_training_metrics(run_id)` — render reward curve, episode length, success rate inline in the chat
  - Reads TensorBoard event files or WandB API
- [ ] **7A.4** Tool: `deploy_policy(checkpoint, articulation_path)` — load a trained ONNX/JIT policy and run inference in sim
  - Wraps the policy in an OmniGraph action-graph tick loop
- [ ] **7A.5** Tool: `evaluate_policy(checkpoint, num_episodes)` — run N episodes headless, report success rate and metrics
- [ ] **7A.6** RL task template library: pick-and-place, locomotion, cabinet open/close, in-hand reorientation — each pre-wired with reward terms

### 7B — Enhanced Replicator / Synthetic Data Generation (Tier 1)

**Goal:** Extend Phase 4A with full domain-randomization authoring, dataset export, and integration with NVIDIA TAO / Omniverse Farm. Integrates with [OmniReplicator](https://github.com/isaac-sim/OmniIsaacGymEnvs).

#### Tasks

- [ ] **7B.1** Tool: `create_sdg_pipeline(annotators, randomizers, output_format, num_frames)` — full Replicator pipeline from natural language
  - Annotators: bounding_box_2d, semantic_segmentation, depth, normals, instance_seg, occlusion, keypoints
  - Output formats: KITTI, COCO, TFRecord, raw NumPy
- [ ] **7B.2** Tool: `add_domain_randomizer(target, randomizer_type, params)` — add/modify randomization
  - Types: pose, texture, lighting, camera, distractors, material properties
  - "randomize lighting between 500–2000 lux with color jitter" → structured config
- [ ] **7B.3** Tool: `preview_sdg(num_samples)` — render a few sample frames and display annotated images in the chat
- [ ] **7B.4** Tool: `export_dataset(pipeline_id, output_dir, cloud_upload)` — run full generation, optionally upload to S3/GCS
- [ ] **7B.5** Integration with Omniverse Farm for distributed rendering at scale

### 7C — XR Teleoperation (Tier 1)

**Goal:** Stream viewport via LiveKit WebRTC and map XR hand-tracking / controller inputs to robot joint targets in real-time. Extends the existing LiveKit scaffold.

#### Tasks

- [ ] **7C.1** Tool: `start_teleop_session(robot_path, input_device, stream_quality)` — open a WebRTC teleop channel
  - `input_device`: quest_3, vision_pro, spacemouse, keyboard
  - Starts LiveKit room, streams viewport, maps input to joint targets
- [ ] **7C.2** Tool: `configure_teleop_mapping(device_axes, joint_names, gains)` — customize axis-to-joint mapping
- [ ] **7C.3** Tool: `record_teleop_demo(output_path)` — record joint trajectories as USD TimeSamples for demonstration data
- [ ] **7C.4** Hand-tracking retargeting: Apple Vision Pro / Meta Quest hand landmarks → robot end-effector IK
- [ ] **7C.5** Chat integration: "start teleop with my Quest" → auto-configures, opens browser link to WebRTC viewer

### 7D — IsaacLab-Arena Composable Environments (Tier 2)

**Goal:** One-command multi-robot benchmark arenas. Integrates with [IsaacLab-Arena](https://github.com/isaac-sim/IsaacLab-Arena).

#### Tasks

- [ ] **7D.1** Tool: `create_arena(arena_type, robots, task, num_envs)` — spawn composable arenas
  - Arena types: flat_ground, maze, obstacle_course, warehouse, tabletop
  - Multiple robots with different policies competing or cooperating
- [ ] **7D.2** Tool: `add_arena_robot(arena_id, robot_asset, spawn_position, policy)` — add a robot to an existing arena
- [ ] **7D.3** Tool: `run_arena_benchmark(arena_id, num_episodes, metrics)` — run and compare multi-agent performance
- [ ] **7D.4** Leaderboard view in chat: ranked performance table across robot/policy combos

### 7E — Eureka LLM Reward Generation (Tier 2)

**Goal:** LLM writes reward functions, evaluates them in sim, and iteratively refines. Integrates with [Eureka](https://github.com/isaac-sim/Eureka).

#### Tasks

- [ ] **7E.1** Tool: `generate_reward(task_description, env_obs_space)` — LLM produces a Python reward function
  - Uses the Eureka evolutionary strategy: generate K candidates, evaluate, select, mutate
- [ ] **7E.2** Tool: `evaluate_reward(reward_code, env, num_episodes)` — run the reward in IsaacLab, return fitness score
- [ ] **7E.3** Tool: `iterate_reward(prev_reward, feedback)` — LLM refines the reward based on training curves and failure modes
- [ ] **7E.4** User flow: "teach my robot to open the cabinet" → LLM generates reward → trains → shows results → user says "it keeps dropping the handle" → LLM refines reward → re-trains

### 7F — IsaacSimZMQ External Comms Bridge (Tier 2)

**Goal:** Bidirectional ZMQ messaging between Isaac Sim and external processes (Python, C++, ROS-less setups). Integrates with [IsaacSimZMQ](https://github.com/isaac-sim/IsaacSimZMQ).

#### Tasks

- [ ] **7F.1** Tool: `start_zmq_bridge(pub_port, sub_port, topics)` — start ZMQ pub/sub sockets inside Kit
- [ ] **7F.2** Tool: `zmq_publish(topic, data)` — push sensor data / joint states to external consumers
- [ ] **7F.3** Tool: `zmq_subscribe(topic, callback_script)` — receive commands from external processes
- [ ] **7F.4** Tool: `zmq_list_connections()` — show active ZMQ links and throughput stats
- [ ] **7F.5** User flow: "stream the lidar data over ZMQ to my Python training script" → auto-configures pub socket + topic

### 7G — GR00T N1 Foundation Policy Evaluation (Tier 3)

**Goal:** Deploy NVIDIA GR00T N1 foundation robot policies in Isaac Sim and benchmark them on custom scenes.

#### Tasks

- [ ] **7G.1** Tool: `load_groot_policy(model_id, robot_path)` — download and attach a GR00T N1 checkpoint
- [ ] **7G.2** Tool: `evaluate_groot(model_id, task, num_episodes)` — run zero-shot or fine-tuned policy, report metrics
- [ ] **7G.3** Tool: `finetune_groot(model_id, demo_data, num_steps)` — fine-tune on user's teleop demonstrations (from 7C.3)
- [ ] **7G.4** Comparison dashboard: "compare GR00T N1 vs my RL policy on the pick-and-place task"

### 7H — IsaacAutomator Cloud Deployment (Tier 3)

**Goal:** Launch headless Isaac Sim instances on cloud GPUs for training, SDG, and evaluation at scale. Integrates with [IsaacAutomator](https://github.com/isaac-sim/IsaacAutomator).

#### Tasks

- [ ] **7H.1** Tool: `cloud_launch(instance_type, num_gpus, isaac_version, script)` — spin up cloud instances
  - Providers: AWS, GCP, Azure, OVHcloud (via IsaacAutomator Terraform configs)
- [ ] **7H.2** Tool: `cloud_status(job_id)` — check running jobs, GPU utilization, estimated time remaining
- [ ] **7H.3** Tool: `cloud_download_results(job_id, output_dir)` — pull checkpoints / datasets back to local
- [ ] **7H.4** Tool: `cloud_teardown(job_id)` — terminate instances to avoid runaway costs
- [ ] **7H.5** Cost estimator: "how much would it cost to train for 10M steps on 4×A100?" → estimates shown in chat before launch

---

## Phase 8 — Native Extension Integration (Weeks 31–38)

**Goal:** Wrap the high-value Isaac Sim built-in extensions as LLM-callable tools, replacing manual/legacy approaches with the official APIs. This eliminates raw USD scripting for common operations and gives the LLM access to Isaac Sim's full built-in capability surface.

### 8A — Quick Wins: Cloner, Debug Draw, Occupancy Map, Camera Inspector, Sensor APIs (Weeks 31–32)

**Goal:** Low-effort, high-impact integrations that immediately improve existing tools.

#### Tasks

- [ ] **8A.1** Tool: `clone_envs(source_path, num_envs, spacing, collision_filter)` — GPU-batched environment cloning
  - Uses `isaacsim.core.cloner.Cloner` for efficient parallel env creation
  - Auto-filters collisions between clones (critical for RL)
  - Replaces the naive for-loop in current `_gen_clone_prim`
  - User flow: "clone this robot setup 1024 times for training" → instant parallel clone
- [ ] **8A.2** Tool: `debug_draw(draw_type, points, color, lifetime)` — runtime visualization in viewport
  - Uses `omni.isaac.debug_draw` extension
  - `draw_type`: lines, points, spheres, arrows, boxes, text
  - "show me the robot's planned path" → draws trajectory as colored line in viewport
  - "highlight all collision contacts" → draws red spheres at contact points
  - Auto-clears after configurable lifetime (default 5s)
- [ ] **8A.3** Tool: `generate_occupancy_map(origin, dimensions, resolution, height_range)` — 2D occupancy map
  - Uses `isaacsim.asset.gen.omap` to ray-cast the scene
  - Returns PNG image of walkable/obstacle areas displayed inline in chat
  - "show me a 2D map of the warehouse floor" → overhead occupancy grid
  - Supports custom height thresholds for multi-level environments
- [ ] **8A.4** Tool: `inspect_camera(camera_path)` / `configure_camera(camera_path, params)` — camera property management
  - Uses `isaacsim.util.camera_inspector` API
  - Reads: focal length, aperture, clipping range, resolution, lens distortion, projection type
  - Writes: any camera attribute — "set the wrist camera to 120° FOV fisheye"
  - Returns structured JSON for LLM reasoning about camera setup
- [ ] **8A.5** Migrate sensor tools to native APIs:
  - Switch `omni.isaac.sensor.LidarRtx` → `isaacsim.sensors.rtx` for RTX lidar/radar
  - Switch `omni.isaac.sensor.IMUSensor` → `isaacsim.sensors.physics` for physics-based sensors
  - Add `isaacsim.sensors.physx` for PhysX-raycast-based proximity and lightbeam sensors
  - New tool: `create_proximity_sensor(prim_path, range, fov)` — detect nearby objects
  - New tool: `create_lightbeam_sensor(prim_path, beam_config)` — industrial light curtain simulation

### 8B — Motion Planning: RMPflow & Lula (Weeks 33–34)

**Goal:** Replace raw joint-target commands with intelligent motion planning. "Move the arm to grab the cup" instead of specifying 7 joint angles.

#### Tasks

- [ ] **8B.1** Tool: `move_to_pose(articulation_path, target_position, target_orientation, planner)` — end-effector motion planning
  - Uses `isaacsim.robot_motion.motion_generation` with RMPflow (reactive) or Lula RRT (global)
  - `planner`: `rmpflow` (fast, reactive, local), `lula_rrt` (global, obstacle-aware), `lula_cspace` (C-space trajectory)
  - Automatically loads robot description from XRDF/URDF
  - User flow: "move the Franka gripper to position [0.5, 0.2, 0.3]" → plans and executes collision-free trajectory
- [ ] **8B.2** Tool: `plan_trajectory(articulation_path, waypoints, planner)` — multi-waypoint trajectory planning
  - Input: list of target poses → output: smooth joint trajectory
  - Preview mode: draws planned path via `debug_draw` (8A.2) before execution
  - "plan a path from the home position to the bin, then to the shelf" → visualized trajectory → approve → execute
- [ ] **8B.3** Tool: `set_motion_policy(articulation_path, policy_type, params)` — configure motion behavior
  - Collision avoidance spheres, joint limits, velocity limits, workspace boundaries
  - "keep the arm away from the table while moving" → adds table as collision obstacle
- [ ] **8B.4** Tool: `generate_robot_description(articulation_path, output_path)` — auto-generate XRDF/Lula robot description
  - Uses `isaacsim.robot_setup.xrdf_editor` to create the YAML needed by motion planners
  - Required before first motion planning call — can auto-detect and generate
- [ ] **8B.5** IK solver integration: single-shot inverse kinematics without full trajectory planning
  - "what joint angles put the gripper at [0.5, 0, 0.3]?" → returns joint config
  - Useful for teleop target computation (feeds into Phase 7C)

### 8C — Cortex Behaviors & Manipulation (Weeks 33–34)

**Goal:** Enable reactive, task-level robot control through behavior trees and built-in manipulation abstractions.

#### Tasks

- [ ] **8C.1** Tool: `create_behavior(articulation_path, behavior_type, params)` — attach Cortex behavior to a robot
  - Uses `isaacsim.cortex.framework` decider networks
  - `behavior_type`: pick_and_place, stacking, peg_insertion, reactive_avoid, follow_target
  - Internally wires: perception → decision → motion generation → gripper control
  - "make the Franka pick up the red cube and place it in the bin" → full behavior pipeline
- [ ] **8C.2** Tool: `create_gripper(articulation_path, gripper_type, params)` — configure grippers
  - Wraps `isaacsim.robot.manipulators` for parallel/finger grippers
  - Wraps `isaacsim.robot.surface_gripper` for suction/magnetic grippers
  - `gripper_type`: parallel_jaw, suction, magnetic, custom
  - Auto-detects gripper joints from articulation structure
- [ ] **8C.3** Tool: `grasp_object(robot_path, target_prim, grasp_type)` — execute a grasp
  - Uses `isaacsim.robot_setup.grasp_editor` authored grasps or auto-computed approach vectors
  - Plans approach → pre-grasp → grasp → lift sequence via motion planner (8B)
  - "pick up the mug with a top-down grasp" → full grasp execution
- [ ] **8C.4** Tool: `define_grasp_pose(robot_path, object_path, gripper_offset, approach_dir)` — author custom grasp
  - Uses `isaacsim.robot_setup.grasp_editor` API — stores as reusable grasp definition
  - "define a side grasp for the bottle" → interactive grasp authoring in viewport
- [ ] **8C.5** Behavior tree editor integration:
  - "show me the current behavior tree" → renders tree structure in chat as formatted text
  - "add a 'check gripper closed' guard before the lift step" → modifies active behavior

### 8D — Robot Setup Suite (Weeks 35–36)

**Goal:** Wrap the robot setup extensions as tools for one-command robot import, configuration, and assembly.

#### Tasks

- [ ] **8D.1** Tool: `robot_wizard(asset_path, config)` — full guided robot import and configuration
  - Uses `isaacsim.robot_setup.wizard` workflow
  - Auto-detects: joint types, drive modes, collision meshes, self-collision groups
  - Applies sensible defaults for drive stiffness/damping based on robot type
  - "import my_robot.urdf and configure it for manipulation" → full setup in one command
- [ ] **8D.2** Tool: `tune_gains(articulation_path, method, target_performance)` — auto-tune PD gains
  - Uses `isaacsim.robot_setup.gain_tuner`
  - `method`: manual (set values), auto_step (step response tuning), auto_trajectory (trajectory tracking)
  - "the arm is oscillating, tune the gains" → runs step response → adjusts kp/kd → reports improvement
- [ ] **8D.3** Tool: `assemble_robot(base_path, attachment_path, mount_frame, joint_type)` — compose robots from parts
  - Uses `isaacsim.robot_setup.assembler`
  - "attach the Robotiq gripper to the UR10 wrist flange" → creates fixed joint, merges articulation
  - Supports: fixed mount, revolute joint, prismatic joint
  - Auto-aligns mount frames if XRDF/URDF specifies tool flange
- [ ] **8D.4** Tool: `configure_self_collision(articulation_path, mode)` — self-collision filtering
  - `mode`: auto (detect adjacent links), manual (specify pairs), disable
  - "enable self-collision but skip adjacent links" → auto-configures collision pairs
- [ ] **8D.5** Tool: `migrate_urdf_importer(prim_path)` — migrate legacy `omni.isaac.urdf` imports to `isaacsim.asset.importer.urdf`
  - Updates import paths, config structures, and joint drive API calls
  - Also supports `isaacsim.asset.importer.mjcf` for MuJoCo files
  - Runs as transparent upgrade — existing robot import tool calls route to new API

### 8E — Wheeled Robots & Conveyor Systems (Weeks 37–38)

**Goal:** Built-in support for mobile robots and industrial automation primitives.

#### Tasks

- [ ] **8E.1** Tool: `create_wheeled_robot(robot_path, drive_type, wheel_config)` — configure wheeled robot control
  - Uses `isaacsim.robot.wheeled_robots`
  - `drive_type`: differential, ackermann, mecanum, omnidirectional
  - Auto-creates OmniGraph with DifferentialController / HolonomicController nodes
  - "set up the Jetbot for differential drive" → controller + OmniGraph ready
- [ ] **8E.2** Tool: `navigate_to(robot_path, target_position, planner)` — 2D navigation for mobile robots
  - Combines occupancy map (8A.3) + path planning (A*, RRT) + drive controller
  - "drive the robot to the loading dock" → path planned, visualized, executed
- [ ] **8E.3** Tool: `create_conveyor(mesh_path, speed, direction)` — set up conveyor belt
  - Uses `isaacsim.asset.gen.conveyor` extension
  - Turns any mesh into a conveyor via rigid body velocity injection
  - "make this belt move at 0.5 m/s towards the robot" → conveyor active
- [ ] **8E.4** Tool: `create_conveyor_track(waypoints, belt_width, speed)` — create conveyor track from path
  - Uses `isaacsim.asset.gen.conveyor.ui` track tool API
  - Creates a system of connected conveyor segments following a path
  - "create a conveyor track from station A to station B with a 90° turn" → full track system
- [ ] **8E.5** Tool: `merge_meshes(prim_paths, output_path)` — combine multiple meshes into one
  - Uses `isaacsim.util.merge_mesh` utility
  - Resets origin, combines materials, deduplicates vertices
  - "merge all the shelf parts into a single mesh" → cleaner scene hierarchy

### 8F — ROS2 Deep Integration (Weeks 37–38)

**Goal:** Go beyond basic topic pub/sub — expose TF trees, URDF publishing, and full bridge configuration.

#### Tasks

- [ ] **8F.1** Tool: `show_tf_tree(root_frame)` — visualize ROS2 TF transform tree
  - Uses `isaacsim.ros2.tf_viewer` to render the tree in the viewport
  - Also returns text-formatted tree in the chat panel
  - "show me the TF tree for the robot" → viewport overlay + chat summary
- [ ] **8F.2** Tool: `publish_robot_description(articulation_path, topic)` — publish URDF to `/robot_description`
  - Uses `isaacsim.ros2.urdf` to export and publish
  - Auto-generates URDF from the USD articulation
- [ ] **8F.3** Tool: `configure_ros2_bridge(config)` — batch-configure multiple ROS2 pub/sub connections
  - Generates the OmniGraph action graph with all configured topics/services
  - "set up ROS2 bridge: publish joint states, subscribe to cmd_vel, publish camera images" → full bridge in one command
- [ ] **8F.4** Extension integration: wire `isaacsim.replicator.domain_randomization` DR nodes into ROS2 pipelines for sim-to-real transfer

---

## Phase 9 — Service Infrastructure Hardening (Weeks 39–43)

**Goal:** Close the critical backend gaps that prevent the system from being a production-ready diagnostic assistant — validators for scene diagnosis, a knowledge feedback loop that learns, real document retrieval, and telemetry to measure improvement.

### 9A — Stage Analyzer Validator Packs (Weeks 39–41)

**Goal:** Expand the Stage Analyzer from 1 validator (SchemaConsistencyRule) to the full 8-pack needed for comprehensive scene diagnosis.

#### Tasks

- [ ] **9A.1** Validator: `ImportHealthValidator` — detect broken USD references, missing assets, unresolved payloads
  - Scan `references` and `payloads` on all prims, flag missing files
  - Check for orphan Xform prims with no geometry or children
  - "3 broken asset references found in /World/Props"
- [ ] **9A.2** Validator: `MaterialPhysicsMismatchValidator` — detect visual vs physics inconsistencies
  - Prims with RigidBodyAPI but no CollisionAPI
  - Meshes with materials but no physics (floating visual-only objects)
  - CollisionAPI with wrong approximation (mesh vs convexHull mismatch)
  - "15 rigid bodies have no collision shapes — they'll fall through everything"
- [ ] **9A.3** Validator: `ArticulationIntegrityValidator` — check articulation chain health
  - Verify joint hierarchy is connected (no orphan joints)
  - Check drive stiffness/damping are non-zero
  - Detect joints with no limits (infinite rotation)
  - Flag articulations with no root body
  - "Franka joint 5 has zero stiffness — arm will collapse under gravity"
- [ ] **9A.4** Validator: `SensorCompletenessValidator` — verify sensor wiring
  - Sensors without RenderProducts (cameras not rendering)
  - LiDAR without OmniGraph tick pipeline
  - IMU not attached to a physics-enabled body
  - "Wrist camera has no RenderProduct — it can't produce images"
- [ ] **9A.5** Validator: `ROSBridgeReadinessValidator` — check ROS2 integration health
  - Verify OG action graph exists for each published topic
  - Check frame_id consistency across sensor publishers
  - Detect topic name collisions
  - Verify clock publisher exists for sim-time sync
- [ ] **9A.6** Validator: `IsaacLabSanityValidator` — check RL environment setup
  - Verify env spacing and collision filtering between clones
  - Check observation/action spaces match env config
  - Detect reward terms referencing non-existent prims
  - Validate reset behavior (prims return to initial state)
- [ ] **9A.7** Validator: `PerformanceWarningsValidator` — flag performance issues
  - High poly-count meshes not using convex decomposition
  - Too many rigid bodies without GPU pipeline
  - Excessive USD layers or sublayers
  - Missing LOD on detailed assets
  - "Scene has 2.3M triangles — consider enabling convex decomposition for collision shapes"
- [ ] **9A.8** Validator registry / factory pattern
  - Replace hardcoded validator list with plugin-style registry
  - `@register_validator("import_health")` decorator
  - Validators can be enabled/disabled per scene profile (RL, manipulation, warehouse)
  - CLI: `python -m isaac_assist_service.analysis.validate --scene /path/to/scene.usd`

### 9B — Knowledge Base Feedback Loop (Weeks 39–40)

**Goal:** Wire the knowledge base so the system learns from every plan execution — successes stored as patterns, failures as anti-patterns — enabling continuous improvement.

#### Tasks

- [ ] **9B.1** Wire plan outcome capture: `plan.apply()` → `knowledge_base.capture_outcome(plan, result, success)`
  - On success: extract code pattern + metadata, store via `save_pattern_from_success()`
  - On failure: store error signature + failing code + root cause in negative memory
  - Connect through `log_execution` route (already captures patches)
- [ ] **9B.2** Implement `query_by_error_pattern()` with real queries
  - Currently returns `[]` — replace with FTS query against error signatures
  - Match against known failure patterns, return previous fixes
  - "I've seen this error before — last time, the fix was adding CollisionAPI"
- [ ] **9B.3** Negative memory store
  - New JSONL: `workspace/knowledge/negative_patterns.jsonl`
  - Schema: `{error_signature, failing_code, root_cause, fix_applied, timestamp}`
  - Queried when similar errors appear — prevents repeating the same mistakes
  - Dedup: same error signature within 24h is not stored again
- [ ] **9B.4** Knowledge playbook authoring
  - Multi-step repair recipes stored as reusable playbooks
  - Schema: `{name, trigger_conditions, steps: [{tool_call, params}], success_criteria}`
  - "When you see 'invalid prim' errors after import, run these 3 steps..."
  - User can create playbooks via chat: "save what you just did as a playbook called 'fix_import'"
- [ ] **9B.5** Knowledge quality metrics
  - Track pattern hit rate (how often patterns are matched and used)
  - Track fix success rate per pattern
  - Auto-deprecate patterns with <20% success rate after 10+ uses

### 9C — Real Document Retrieval Pipeline (Weeks 40–41)

**Goal:** Replace mock documents with real NVIDIA Isaac Sim documentation, scraped, versioned, and indexed for RAG-powered retrieval.

#### Tasks

- [ ] **9C.1** NVIDIA doc scraper
  - Scrape Isaac Sim 5.1 docs from `docs.omniverse.nvidia.com` (or local HTML dump)
  - Parse: API reference, tutorials, migration guides, extension docs
  - Store as chunked markdown in `workspace/knowledge/docs/{version}/`
  - Respect rate limits and cache for offline use
- [ ] **9C.2** Version-aware indexing
  - Index docs tagged by Isaac Sim version (5.1 vs 6.0)
  - Query filters by current environment fingerprint version
  - "Using Isaac Sim 5.1 — showing 5.1-specific API docs"
- [ ] **9C.3** FTS index integration
  - Replace `index_mock_doc()` with real document loading on startup
  - Use existing FTS infrastructure in `retrieval/indexer.py`
  - Chunk large pages into 500-token segments with overlap
  - Re-index on version change or manual trigger
- [ ] **9C.4** Scheduled re-indexing
  - Daily/weekly check for doc updates (configurable `DOC_REINDEX_INTERVAL`)
  - Diff detection — only re-index changed pages
  - Admin endpoint: `POST /api/v1/retrieval/reindex`
- [ ] **9C.5** Remove `mock_findings` parameter from plan generation
  - Replace with real call to Stage Analyzer (9A) for scene findings
  - Plans based on actual scene problems, not hardcoded test data

### 9D — Telemetry & Evaluation Pipeline (Weeks 41–43)

**Goal:** Instrument the service to measure diagnostic accuracy, fix success rate, and user satisfaction — enabling data-driven improvement of the system.

#### Tasks

- [ ] **9D.1** Event emitter framework
  - `TelemetryEvent` dataclass: `{event_type, timestamp, session_id, data}`
  - Event types: `chat_turn`, `tool_call`, `plan_generated`, `plan_applied`, `plan_failed`, `rollback`, `approval_granted`, `approval_denied`, `error_diagnosed`, `pattern_matched`
  - Async emit — doesn't block request handling
  - Pluggable backends: SQLite (default), file, webhook
- [ ] **9D.2** Metrics store (SQLite)
  - `telemetry.db` in `workspace/` directory
  - Tables: `events`, `metrics_hourly`, `metrics_daily`
  - Rolling aggregation: avg diagnose time, fix success rate, rollback rate, tool usage distribution
  - Auto-prune events older than 90 days
- [ ] **9D.3** API endpoints
  - `GET /api/v1/telemetry/summary` — overall system health metrics
  - `GET /api/v1/telemetry/events?type=plan_failed&since=24h` — filtered event query
  - `GET /api/v1/telemetry/tool_usage` — tool call frequency and success rate
  - `GET /api/v1/telemetry/patterns` — pattern match rate and effectiveness
- [ ] **9D.4** Built-in evaluation suite
  - Run test scenarios from `workspace/knowledge/test_cases.jsonl` automatically
  - Measure: correct tool selection, code quality, execution success
  - Compare across LLM providers (Anthropic vs Gemini vs local)
  - Report: accuracy, latency p50/p95/p99, token usage, cost per turn
- [ ] **9D.5** Dashboard data export
  - JSON export for external dashboarding (Grafana, Streamlit)
  - Markdown report generation: "weekly summary" command
  - Trend detection: alert if fix success rate drops below threshold

### 9E — Service Hardening & Cleanup (Weeks 42–43)

**Goal:** Fix brittle code, wire stubs, and remove mock data to bring the service to production quality.

#### Tasks

- [ ] **9E.1** Fix fingerprint cache thread safety
  - Replace mutable `global _cached_fingerprint` with `asyncio.Lock` + TTL cache
  - Add `Cache-Control` headers for HTTP clients
  - Invalidate on Isaac Sim version change detection
- [ ] **9E.2** Wire vision tool handlers
  - Implement `_handle_vision_detect_objects()` — use `omni.replicator.core` semantic segmentation or render-based detection
  - Implement `_handle_vision_bounding_boxes()` — return 2D/3D bounding boxes from viewport
  - Connect to Kit RPC for renderer-based object detection
- [ ] **9E.3** Wire file upload backend
  - `POST /api/v1/chat/upload` — accept multipart file upload
  - Route images to image-to-3D pipeline (Phase 6B) or viewport comparison
  - Route USD/OBJ/GLB to import pipeline
  - Route URDF/MJCF to robot import pipeline
  - Store uploaded files in `workspace/uploads/` with metadata
- [ ] **9E.4** Integrate Plan generation with real Stage Analyzer
  - Replace `mock_findings` parameter with real `run_analysis()` call
  - Plan steps derived from actual validator findings (9A)
  - Each plan step references the specific finding it addresses
- [x] **9E.5** Wire ROS2 tool execution handlers
  - Connected `tool_executor.py` handlers to `ros_mcp_tools.py` for:
    - `ros2_list_topics` / `ros2_get_topic_type` / `ros2_get_message_type`
    - `ros2_subscribe_once` / `ros2_publish` / `ros2_publish_sequence`
    - `ros2_list_services` / `ros2_call_service`
    - `ros2_list_nodes` / `ros2_get_node_details`
    - `launch_rviz2` / `stop_rviz2` (via `rviz_launcher.py`)
  - Tested with rosbridge WebSocket at `127.0.0.1:9090`
- [ ] **9E.6** Add integration tests (L1–L2)
  - L1: Service integration tests — start service, send chat, verify tool calls
  - L2: MCP server tests — verify tool schema registration and dispatch
  - CI-ready: can run without Isaac Sim (mock Kit RPC responses)
  - Add to `scripts/test_full.py --level 1` and `--level 2`

---

## Tool Function Registry (Summary)

All tools are exposed to the LLM via structured function-calling schemas. The LLM picks which tool(s) to call based on the user's message.

| Tool Name | Phase | Category |
|---|---|---|
| `create_prim` | 1A | USD |
| `set_attribute` | 1A | USD |
| `delete_prim` | 1A | USD |
| `add_reference` | 1A | USD |
| `apply_api_schema` | 1A | USD |
| `run_usd_script` | 1A | USD |
| `create_deformable_mesh` | 1B | Mesh / Physics |
| `create_omnigraph` | 1C | OmniGraph |
| `add_sensor_to_prim` | 1C | OmniGraph / Sensors |
| `lookup_product_spec` | 1C | Web / Knowledge |
| `create_material` | 1D | Materials |
| `assign_material` | 1D | Materials |
| `get_console_errors` | 2 | Debugging |
| `get_physics_errors` | 2 | Debugging |
| `explain_error` | 2 | Debugging |
| `fix_error` | 2 | Debugging |
| `get_debug_info` | 2 | Debugging |
| `sim_control` | 3 | Simulation |
| `import_robot` | 3 | Import |
| `set_physics_params` | 3 | Physics |
| `teleport_prim` | 3 | Transform |
| `clone_prim` | 3 | USD |
| `set_joint_targets` | 3 | Articulation |
| `configure_sdg` | 4A | Replicator |
| `run_sdg` | 4A | Replicator |
| `ros2_publish` | 4B | ROS2 |
| `ros2_subscribe` | 4B | ROS2 |
| `launch_rviz2` | 4B | ROS2 |
| `stop_rviz2` | 4B | ROS2 |
| `set_viewport_camera` | 4C | Viewport |
| `create_render_product` | 4C | Viewport |
| `list_all_prims` | 4D | Query |
| `measure_distance` | 4D | Query |
| `check_collisions` | 4D | Query |
| `scene_summary` | 4D | Query |
| `catalog_search` | 6A | Scene Builder |
| `generate_scene_blueprint` | 6A | Scene Builder |
| `validate_scene_blueprint` | 6A | Scene Builder |
| `build_scene_from_blueprint` | 6A | Scene Builder |
| `generate_3d_from_image` | 6B | Image-to-USD |
| `import_generated_model` | 6B | Image-to-USD |
| `create_isaaclab_env` | 7A | RL / IsaacLab |
| `launch_training` | 7A | RL / IsaacLab |
| `show_training_metrics` | 7A | RL / IsaacLab |
| `deploy_policy` | 7A | RL / IsaacLab |
| `evaluate_policy` | 7A | RL / IsaacLab |
| `create_sdg_pipeline` | 7B | Replicator / SDG |
| `add_domain_randomizer` | 7B | Replicator / SDG |
| `preview_sdg` | 7B | Replicator / SDG |
| `export_dataset` | 7B | Replicator / SDG |
| `start_teleop_session` | 7C | XR Teleop |
| `configure_teleop_mapping` | 7C | XR Teleop |
| `record_teleop_demo` | 7C | XR Teleop |
| `create_arena` | 7D | Arena / Multi-Agent |
| `run_arena_benchmark` | 7D | Arena / Multi-Agent |
| `generate_reward` | 7E | Eureka / Reward |
| `evaluate_reward` | 7E | Eureka / Reward |
| `iterate_reward` | 7E | Eureka / Reward |
| `start_zmq_bridge` | 7F | ZMQ Comms |
| `zmq_publish` | 7F | ZMQ Comms |
| `zmq_subscribe` | 7F | ZMQ Comms |
| `load_groot_policy` | 7G | GR00T N1 |
| `evaluate_groot` | 7G | GR00T N1 |
| `finetune_groot` | 7G | GR00T N1 |
| `cloud_launch` | 7H | Cloud Deploy |
| `cloud_status` | 7H | Cloud Deploy |
| `cloud_download_results` | 7H | Cloud Deploy |
| `cloud_teardown` | 7H | Cloud Deploy |
| `clone_envs` | 8A | Cloner |
| `debug_draw` | 8A | Visualization |
| `generate_occupancy_map` | 8A | Navigation |
| `inspect_camera` | 8A | Sensors |
| `configure_camera` | 8A | Sensors |
| `create_proximity_sensor` | 8A | Sensors |
| `create_lightbeam_sensor` | 8A | Sensors |
| `move_to_pose` | 8B | Motion Planning |
| `plan_trajectory` | 8B | Motion Planning |
| `set_motion_policy` | 8B | Motion Planning |
| `generate_robot_description` | 8B | Robot Setup |
| `create_behavior` | 8C | Cortex / Behaviors |
| `create_gripper` | 8C | Manipulation |
| `grasp_object` | 8C | Manipulation |
| `define_grasp_pose` | 8C | Manipulation |
| `robot_wizard` | 8D | Robot Setup |
| `tune_gains` | 8D | Robot Setup |
| `assemble_robot` | 8D | Robot Setup |
| `configure_self_collision` | 8D | Robot Setup |
| `create_wheeled_robot` | 8E | Mobile Robots |
| `navigate_to` | 8E | Mobile Robots |
| `create_conveyor` | 8E | Industrial |
| `create_conveyor_track` | 8E | Industrial |
| `merge_meshes` | 8E | Mesh Utils |
| `show_tf_tree` | 8F | ROS2 |
| `publish_robot_description` | 8F | ROS2 |
| `configure_ros2_bridge` | 8F | ROS2 |
| `validate_import_health` | 9A | Stage Analyzer |
| `validate_material_physics` | 9A | Stage Analyzer |
| `validate_articulation` | 9A | Stage Analyzer |
| `validate_sensors` | 9A | Stage Analyzer |
| `validate_ros_bridge` | 9A | Stage Analyzer |
| `validate_isaaclab` | 9A | Stage Analyzer |
| `validate_performance` | 9A | Stage Analyzer |
| `capture_plan_outcome` | 9B | Knowledge |
| `query_error_pattern` | 9B | Knowledge |
| `create_playbook` | 9B | Knowledge |
| `search_docs` | 9C | Retrieval |
| `reindex_docs` | 9C | Retrieval |
| `get_telemetry_summary` | 9D | Telemetry |
| `get_tool_usage_stats` | 9D | Telemetry |
| `detect_objects` | 9E | Vision |
| `get_bounding_boxes` | 9E | Vision |
| `upload_file` | 9E | File Upload |

---

## Example User Flows

### Flow 1: "Make this cloth"
```
User clicks mesh /World/Table/Napkin in viewport
User types: "make this a soft cloth that drapes"
→ LLM sees context: selected_prim=/World/Table/Napkin, type=Mesh
→ LLM calls: create_deformable_mesh("/World/Table/Napkin", "cloth", {damping: 0.1})
→ Approval dialog shows the PhysX code
→ User clicks Execute
→ Mesh now has DeformableSurfaceAPI, ready for cloth sim
```

### Flow 2: "Add a RealSense camera here"
```
User clicks prim /World/Robot/wrist_link
User types: "attach a RealSense D435i depth camera to this link"
→ LLM calls: lookup_product_spec("Intel RealSense D435i")
→ Returns: {fov_h: 87, fov_v: 58, resolution: [1280,720], depth_range: [0.1, 10.0], fps: 30}
→ LLM calls: add_sensor_to_prim("/World/Robot/wrist_link", "camera", {fov: 87, resolution: [1280,720], ...})
→ Internally: creates Camera prim + RenderProduct + OmniGraph action graph for depth output
→ User approves → sensor live in simulation
```

### Flow 3: "Why is my robot falling through the floor?"
```
User types: "the robot keeps falling through the ground, help"
→ LLM calls: get_console_errors(50, "warning")
→ Sees: "PhysX warning: no collision mesh on /World/Ground"
→ LLM calls: explain_error("no collision mesh on /World/Ground")
→ LLM responds: "Your ground plane doesn't have a collision shape. I can fix this."
→ LLM calls: apply_api_schema("/World/Ground", "PhysicsCollisionAPI")
→ Approval → fixed
```

### Flow 4: "Move the arm to grab the cup"
```
User clicks /World/Franka in viewport
User types: "move the arm to grab the coffee cup on the table"
→ LLM sees context: selected_prim=/World/Franka, type=Articulation
→ LLM calls: move_to_pose("/World/Franka", target_position=[0.5, 0.2, 0.15],
              target_orientation=[0, 1, 0, 0], planner="rmpflow")
→ Internally: loads XRDF robot description, initializes RMPflow,
   plans collision-free trajectory to cup position
→ LLM calls: debug_draw("lines", trajectory_points, color="green", lifetime=5)
→ Green trajectory appears in viewport
→ Approval → arm moves smoothly to cup
→ LLM calls: grasp_object("/World/Franka", "/World/Table/CoffeeCup", "top_down")
→ Gripper closes, cup lifted
```

### Flow 5: "Clone this 1024 times for training"
```
User types: "clone this robot setup 1024 times in a grid for RL training"
→ LLM calls: clone_envs("/World/Env_0", num_envs=1024, spacing=2.0,
              collision_filter=true)
→ Internally: isaacsim.core.cloner.Cloner creates 1024 envs in parallel on GPU
→ Auto-filters inter-environment collisions
→ Done in <1s (vs ~30s with naive for-loop clone)
```

### Flow 6: "Show me a map of the warehouse"
```
User types: "generate a 2D occupancy map of the warehouse floor"
→ LLM calls: generate_occupancy_map(origin=[0,0,0], dimensions=[20,20],
              resolution=0.05, height_range=[0.1, 2.0])
→ Internally: isaacsim.asset.gen.omap ray-casts the scene at 5cm resolution
→ Returns PNG occupancy grid — obstacles in black, free space in white
→ Image displayed inline in chat
→ LLM: "The warehouse has a clear 3m corridor along the north wall.
         I can plan a path for the robot through there."
```

### Flow 7: "Tune the arm, it's oscillating"
```
User types: "the robot arm is oscillating when it moves, fix the gains"
→ LLM calls: tune_gains("/World/Franka", method="auto_step",
              target_performance={overshoot: 0.05, settling_time: 0.3})
→ Internally: isaacsim.robot_setup.gain_tuner runs step response on each joint
→ Returns before/after kp/kd values + settling time improvement
→ LLM: "Reduced overshoot from 23% to 4%. Joint 4 kp: 800→520, kd: 40→65."
→ Approval → gains applied
```

### Flow 8: "Set up the Jetbot for differential drive"
```
User types: "configure the Jetbot for differential drive and drive it to the corner"
→ LLM calls: create_wheeled_robot("/World/Jetbot", drive_type="differential",
              wheel_config={radius: 0.03, separation: 0.12})
→ OmniGraph DifferentialController node created and wired
→ LLM calls: navigate_to("/World/Jetbot", target=[5.0, 5.0], planner="astar")
→ Generates occupancy map → plans path → executes differential drive commands
→ Jetbot drives to the corner avoiding obstacles
```

### Flow 9: "Attach the Robotiq gripper to the UR10"
```
User types: "attach the Robotiq 2F-85 gripper to the UR10 tool flange"
→ LLM calls: assemble_robot(
    base_path="/World/UR10",
    attachment_path="/World/Robotiq_2F85",
    mount_frame="tool0",
    joint_type="fixed"
  )
→ Internally: isaacsim.robot_setup.assembler merges articulations,
   creates fixed joint at tool flange, updates collision groups
→ Approval → unified robot with working gripper
→ LLM: "Gripper attached. 8 DOF total (6 arm + 2 finger)."
```

### Flow 10: "Launch RViz to see the robot's sensors"
```
User types: "launch rviz"
→ LLM calls: launch_rviz2(fixed_frame="odom")
→ Internally: discovers 22 active ROS2 topics via rosbridge
→ Maps topics to RViz2 display types (8 camera Image displays, TF, Odometry, etc.)
→ Queries Kit RPC for scene file name → "nova_carter_omnigraph_kitchen"
→ Saves config: workspace/rviz_configs/nova_carter_omnigraph_kitchen_20260418_082211.rviz
→ Launches rviz2 subprocess with the config
→ LLM: "RViz2 launched with 9 displays (8 cameras, TF). PID 48231.
         Config saved as nova_carter_omnigraph_kitchen_20260418_082211.rviz"
→ User: "stop rviz" → LLM calls stop_rviz2() → process terminated
```

### Flow 11: "Show me what the overhead camera sees"
```
User types: "switch viewport to the overhead camera"
→ LLM calls: set_viewport_camera("/World/Cameras/overhead_cam")
→ Viewport switches instantly
→ LLM auto-captures and says: "Here's the overhead view. I can see 3 objects on the table."
```

### Flow 12: "Build a house and put my robot in the kitchen"
```
User types: "Build a house with a kitchen and put my Unitree G1 robot inside
            with a table, chairs, sink, and some kitchen items"
→ LLM calls: catalog_search("house walls floor kitchen", limit=20)
→ Returns 15 matched assets (walls, floor tiles, cabinets, appliances)
→ LLM calls: catalog_search("Unitree G1", asset_type="robot")
→ Returns: {path: "~/Desktop/assets/Collected_Robots/unitree_g1.usd", bounding_box: [0.4, 0.3, 1.2]}
→ LLM calls: generate_scene_blueprint({
    description: "Kitchen room 6x4m with table, 4 chairs, sink, fridge,
                  Unitree G1 robot with 1m clearance",
    available_assets: [...]
  })
→ Returns blueprint: 12 objects with positions, rotations, spatial rules
→ LLM calls: validate_scene_blueprint(blueprint)
→ Returns: {valid: true, issues: [{object: "chair_3", problem: "clips with table leg",
            suggestion: "shift 0.15m right"}]}
→ Auto-fix applied. Blueprint preview card shown in chat:
    ┌──────────────────────────────────────────┐
    │ 🏠 Scene Blueprint: Kitchen with G1      │
    │                                          │
    │  ✅ Floor (6x4m)        → /World/Floor   │
    │  ✅ Wall_North           → /World/Walls/N │
    │  ✅ Wall_South           → /World/Walls/S │
    │  ✅ Kitchen_Table        → /World/Table   │
    │  ✅ Chair x4             → /World/Chairs/ │
    │  ✅ Sink                 → /World/Sink    │
    │  ✅ Unitree_G1           → /World/G1      │
    │  ⚠️  Fridge (not found)  → suggest: box   │
    │                                          │
    │  [Build All] [Modify] [Cancel]           │
    └──────────────────────────────────────────┘
→ User clicks "Build All"
→ 12 code patches generated, executed in order
→ User: "move the robot closer to the table"
→ LLM updates blueprint delta, re-validates, applies change
```

### Flow 13: "Turn this photo into a 3D model"
```
User clicks 📎 → selects photo of a coffee mug from desktop
User types: "Create a 3D model from this image and put it on the table"
→ context.uploaded_image = base64(coffee_mug.jpg)
→ LLM calls: generate_3d_from_image(image_b64=..., output_name="coffee_mug", backend="triposr")
→ Service: removes background → feeds to TripoSR → generates GLB mesh
→ Service: converts GLB → USD via asset_converter
→ Returns: {mesh_path: "workspace/generated_models/coffee_mug.usd", vertices: 12400}
→ LLM calls: import_generated_model(
    model_path="workspace/generated_models/coffee_mug.usd",
    prim_path="/World/Table/CoffeeMug",
    position=[2.1, 1.0, 0.76],  # on top of the table surface
    scale=[0.1, 0.1, 0.1]       # normalize to real-world size
  )
→ Approval dialog → user executes → mug appears on the table
→ User: "make it a bit bigger and add physics"
→ LLM adjusts scale + applies RigidBodyAPI + CollisionAPI
```

---

## Technical Notes

- **All USD mutations go through `omni.kit.commands`** so every AI action is Ctrl+Z undoable
- **Governance approval** is mandatory for any tool that modifies the stage — no silent writes
- **Kit RPC server** (port 8001) stays inside the Kit process; the FastAPI service (port 8000) is the LLM orchestrator
- **Tool calling** uses structured function schemas compatible with OpenAI, Anthropic, and Ollama tool-call formats
- **Product spec lookup** combines a local JSONL knowledge base with live web scraping (manufacturer pages) as fallback
- **Viewport captures** are downscaled to 512px max before sending to the LLM to keep token costs low

---

## License & Attribution

Extension: `omni.isaac.assist`  
Publisher: **10Things, Inc.**  
Website: [www.10things.tech](http://www.10things.tech)  
Built on NVIDIA Isaac Sim (Apache 2.0) and NVIDIA Omniverse Kit
