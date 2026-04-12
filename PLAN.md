# Isaac Assist — Full-Control Project Plan

**Author:** 10Things, Inc. — [www.10things.tech](http://www.10things.tech)  
**Extension:** `omni.isaac.assist`  
**Target:** Isaac Sim 5.1 / 6.0 on NVIDIA Omniverse  
**Date:** April 2026

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
| USD patch executor (exec in Kit) | ✅ Basic |
| Swarm patch planner (coder/critic/QA agents) | ✅ Basic |
| LiveKit WebRTC viewport streaming | ⚠️ Scaffold only |
| Physics articulation state read | ✅ Running |
| Governance / approval dialogs | ✅ Running |
| Snapshots / rollback | ✅ Running |

---

## What's Missing (Gap Analysis)

| Gap | Priority |
|---|---|
| **Selection-aware chat context** — auto-inject clicked prim into chat turn | P0 |
| **Viewport visual feedback** — show LLM what the user sees per turn | P0 |
| **OmniGraph node creation** — create/wire action graphs via natural language | P0 |
| **Deformable/soft-body mesh creation** — cloth, sponge, rubber from text | P0 |
| **Product spec lookup** — fetch real camera/sensor datasheets from the web | P0 |
| **Material authoring** — MDL/OmniPBR material creation & assignment | P1 |
| **Full USD code generation & execution** — arbitrary pxr Python | P1 |
| **Console error diagnosis** — "what's wrong?" reads errors, suggests fixes | P1 |
| **Simulation control** — play/pause/step/reset from chat | P1 |
| **Import pipeline** — URDF/MJCF/USD reference from chat | P1 |
| **Replicator / SDG** — synthetic data generation from chat | P2 |
| **ROS2 bridge control** — topic pub/sub from chat | P2 |
| **Multi-viewport / camera switching** — "show me the wrist camera" | P2 |
| **Undo/redo narration** — LLM explains what it did, user can Ctrl+Z | P2 |
| **Fine-tune data capture** — every chat→action pair stored for training | P2 |

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

- [ ] **4B.1** Tool: `ros2_publish(topic, msg_type, data)` — publish to a ROS2 topic
- [ ] **4B.2** Tool: `ros2_subscribe(topic, msg_type)` — subscribe and show data in chat
- [ ] **4B.3** Tool: `ros2_list_topics()` — show active topics

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
| `set_viewport_camera` | 4C | Viewport |
| `create_render_product` | 4C | Viewport |
| `list_all_prims` | 4D | Query |
| `measure_distance` | 4D | Query |
| `check_collisions` | 4D | Query |
| `scene_summary` | 4D | Query |

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

### Flow 4: "Show me what the overhead camera sees"
```
User types: "switch viewport to the overhead camera"
→ LLM calls: set_viewport_camera("/World/Cameras/overhead_cam")
→ Viewport switches instantly
→ LLM auto-captures and says: "Here's the overhead view. I can see 3 objects on the table."
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
