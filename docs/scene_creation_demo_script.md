# Isaac Assist — Scene Creation Demo Script
### "Build a robot scene in plain English"

**Format:** Product walkthrough video  
**Target audience:** Robotics engineers, Isaac Sim users, AI/LLM enthusiasts  
**Runtime:** ~8–12 minutes  
**Repo:** [github.com/10things-tech/isaac-assist](https://github.com/10things-tech/isaac-assist)  
**Service docs:** `http://localhost:8000/docs` (live after `./launch_service.sh`)

---

## 0 — Cold Open (0:00–0:30)

> *Show Isaac Sim open, the Isaac Assist chat panel docked on the right, an empty stage.*

**Voiceover / on-screen text:**

> "What if you could build a complete robot simulation just by describing it?  
> No menus. No USD APIs. No OmniGraph wiring. Just English."

**Cut to:** user typing the first prompt. Chat responds. Robot appears in the viewport.

---

## 1 — What Is Isaac Assist? (0:30–1:30)

**Slide / overlay:**

```
Isaac Assist
├── Dockable AI chat panel  (omni.ui, inside Isaac Sim)
├── FastAPI orchestration service  (localhost:8000)
├── Kit RPC bridge  (localhost:8001 — executes Python inside the sim)
└── LLM backend  (Claude / GPT-4o / Gemini / Ollama — hot-switchable)
```

- You type in English → the LLM picks the right tool → code runs inside the live sim
- Every edit goes through `omni.kit.commands` — fully undoable with Ctrl+Z
- Governance layer asks for approval before any high-risk change
- Full audit trail + snapshot rollback

---

## 2 — Current Capabilities (What Works Today) (1:30–3:00)

> *Walk through each section with a short screen recording clip.*

### 2a — Robot Import

| What you say | What happens |
|---|---|
| `"Load a Nova Carter into the scene"` | Searches asset catalog, imports USD, places at origin |
| `"Import a Franka arm from the asset library"` | Resolves alias, fetches from Nucleus or local path |
| `"Bring in a Jetbot"` | Robot name normalized, URDF/USD auto-selected |

**Supported robots (20+):** Nova Carter, Franka Emika, UR10e, Jetbot, H1, Spot, Go2, Kaya, Transporter, Carter v2, Dingo, Ridgeback, Husky, and more.

---

### 2b — Scene Layout

| What you say | What happens |
|---|---|
| `"Add a warehouse floor, 20m × 20m"` | Creates a UsdGeom Mesh with PhysX collider |
| `"Place three shelving units along the north wall"` | Clones prim, positions with transform offsets |
| `"Add a table 2m in front of the robot"` | Uses stage bounding box to compute relative placement |
| `"Make that surface slightly rough — friction 0.6"` | Applies PhysX physics material with specified coefficients |

---

### 2c — Sensors

| What you say | What happens |
|---|---|
| `"Attach a Velodyne VLP-16 lidar to the robot"` | Creates RTX LiDAR prim, config preset `Velodyne_VLP16` |
| `"Add an Ouster OS1-64 on the top of the chassis"` | 64-channel rotating LiDAR, config `Ouster_OS1_64` |
| `"Give it a front-facing RealSense D435i"` | Camera prim with depth + RGB, spec from sensor_specs.jsonl |
| `"Add an IMU to the chassis link"` | Physics IMU sensor attached to the articulation |
| `"List all sensors on the stage"` | Returns paths, types, parent prims |

**Available LiDAR presets:** `Velodyne_VLP16` · `HESAI_XT32_SD10` · `Ouster_OS1_64` · `SICK_picoScan150` · `Livox_Mid_360` · `Example_Rotary` · `Example_Solid_State`

---

### 2d — ROS2 Wiring

| What you say | What happens |
|---|---|
| `"Wire up the lidar to publish on /scan"` | Builds RtxLidarHelper OmniGraph: RenderProduct → LaserScan publisher |
| `"Publish the robot's TF tree"` | Creates ROS2PublishTransformTree node with correct parentPrim |
| `"Start the camera RGB feed on /camera/rgb"` | OmniGraph camera publisher with clock sync |
| `"Open RViz2 and show me everything"` | Discovers topics → generates .rviz config → launches rviz2 subprocess |
| `"Check the ROS bridge for issues"` | ROSBridgeReadinessValidator: fullScan flag, frameId match, TF wiring, clock |

---

### 2e — Materials & Physics

| What you say | What happens |
|---|---|
| `"Make the robot chassis matte black"` | Creates OmniPBR material, sets albedo, assigns to prim |
| `"Apply a shiny metal finish to the arm"` | OmniPBR with metallic=1.0, roughness=0.1 |
| `"Set gravity to Mars gravity"` | Sets physics scene `gravityMagnitude` to 3.72 m/s² |
| `"Make the floor grippy — static friction 0.9"` | PhysX material applied to floor mesh |

---

### 2f — Diagnostics & Repair

| What you say | What happens |
|---|---|
| `"Why is the simulation crashing?"` | Reads carb console log, explains top errors |
| `"Validate the scene"` | Runs Stage Analyzer: schema consistency, ROS bridge readiness, robot motion |
| `"Take a snapshot before I change anything"` | Serializes stage state, stores with timestamp |
| `"Roll back to my last snapshot"` | Restores previous USD state |

---

### 2g — Simulation Control

| What you say | What happens |
|---|---|
| `"Play the simulation"` | `omni.timeline.play()` |
| `"Pause it and step forward 10 frames"` | Pause + `timeline.forward()` × 10 |
| `"Move the robot arm to [0.3, 0.1, 0.5]"` | Sets joint targets via `SingleArticulation` |
| `"Reset the scene"` | Stops sim, restores stage |

---

## 3 — Full Demo Flow (3:00–7:00)

> *Continuous recording, no cuts. Show the full prompt sequence.*

### Prompt Sequence — "Warehouse LiDAR Bot"

```
1.  "New scene. Add a 30x30m warehouse floor with 4 support columns at the corners."

2.  "Load a Nova Carter robot and place it in the center."

3.  "Attach a Velodyne VLP-16 lidar to the top of the chassis."

4.  "Add a RealSense D435i camera facing forward."

5.  "Wire up the lidar to publish on /scan and the camera on /camera/rgb. 
     Also publish the TF tree."

6.  "Check the ROS bridge — any issues?"

7.  "Open RViz2."

8.  "Make the floor concrete gray and the robot chassis matte black."

9.  "Play the simulation, then read the lidar sensor data."

10. "Take a snapshot. Now apply a shiny metal finish to the robot — 
     actually, roll that back."
```

**Expected visible results at each step:**
1. Flat grey plane + four columns appear
2. Nova Carter materializes at origin
3. OmniLidar prim appears under the chassis in stage tree
4. Camera prim appears, depth + RGB outputs visible in property panel
5. OmniGraph action graph visible in graph editor
6. Validator report: ✅ all checks pass (or lists specific fixes)
7. RViz2 window opens, LiDAR scan + camera feeds visible
8. Materials update in viewport in real time
9. Simulation runs; sensor readout shows point cloud metadata
10. Snapshot saved; metal finish applied; rollback restores matte black

---

## 4 — What's Coming Next (Backlog) (7:00–8:30)

> *Slide: roadmap graphic*

### P0 — Next Sprint

| Feature | What it enables |
|---|---|
| **NL Scene Builder** | "Build a kitchen with my robot" → full spatial layout from asset catalog, one prompt |
| **IsaacLab RL training** | Scaffold training environments, launch training, see live metrics from chat |
| **Motion planning (RMPflow/Lula)** | "Move the arm to this pose" — collision-aware trajectory |
| **GPU-batched cloning** | Clone 1,000 environments for parallel RL training in seconds |
| **Real doc scraping** | LLM answers grounded in actual NVIDIA Isaac Sim docs |
| **5 more Stage Analyzer validators** | Import health, articulation integrity, sensor completeness, IsaacLab sanity, perf warnings |

### P1 — Soon

| Feature | What it enables |
|---|---|
| **Image-to-USD** | Upload a photo → generate 3D mesh → place in scene |
| **Chat file upload** | Drop in a URDF, OBJ, or USD file and say "add this" |
| **XR teleoperation** | WebRTC hand-tracking → joint control via LiveKit |
| **Debug draw** | Draw waypoints, bounding boxes, collision hulls in the viewport |

### P2 — Future

| Feature | What it enables |
|---|---|
| **GR00T N1.7 policy eval** | Deploy VLA foundation policies, fine-tune, evaluate in sim |
| **Eureka reward generation** | LLM writes RL reward functions with iterative refinement |
| **Cortex behavior trees** | Reactive pick-and-place without raw joint targets |

---

## 5 — Get Started (8:30–9:30)

### Prerequisites

| | |
|---|---|
| Isaac Sim | 5.1 or 6.0 |
| Python | 3.10+ |
| NVIDIA RTX GPU | Required by Isaac Sim |
| ROS2 | Jazzy (for ROS2 bridge features) |

### 3-Step Quickstart

**Step 1 — Clone & install**
```bash
git clone https://github.com/10things-tech/isaac-assist
cd isaac-assist
pip install -r requirements.txt
```

**Step 2 — Configure your LLM**
```bash
cp .env.local.example .env.local
# Set ANTHROPIC_API_KEY (or OPENAI_API_KEY, etc.)
# Set ASSETS_ROOT_PATH to your Isaac Sim assets folder
```

**Step 3 — Launch**
```bash
# Terminal 1: start the AI service
./launch_service.sh anthropic    # or: local, openai, cloud, grok

# Terminal 2: launch Isaac Sim with the extension
./launch_isaac.sh
```

The **Isaac Assist** panel appears docked in Isaac Sim. Start typing.

### Switch LLM at runtime (no restart)
```bash
curl -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "local"}'
```

---

## 6 — Links & Resources

| Resource | URL |
|---|---|
| GitHub repo | `https://github.com/10things-tech/isaac-assist` |
| API docs (live) | `http://localhost:8000/docs` |
| Isaac Sim download | `https://developer.nvidia.com/isaac/sim` |
| Isaac Sim asset library | `https://docs.isaacsim.omniverse.nvidia.com/latest/reference_material/reference_assets.html` |
| ROS2 Jazzy install | `https://docs.ros.org/en/jazzy/Installation.html` |
| Ollama (local LLM) | `https://ollama.com` |
| 10Things, Inc. | `https://www.10things.tech` |

---

## Appendix — Quick Prompt Reference Card

> *Print this or show as an end-card.*

```
ROBOTS          "Load a [robot name] into the scene"
SENSORS         "Attach a [sensor] to [prim path]"
LIDAR PRESETS   Velodyne_VLP16 | HESAI_XT32_SD10 | Ouster_OS1_64
                SICK_picoScan150 | Livox_Mid_360 | Example_Rotary
MATERIALS       "Make [prim] [description] — e.g. matte black, shiny metal"
PHYSICS         "Set gravity to [value]" | "Make [surface] friction [value]"
ROS2            "Wire up [sensor] to publish on [/topic]"
                "Publish the TF tree" | "Check the ROS bridge"
RVIZ2           "Open RViz2" | "Stop RViz2"
SIMULATION      "Play" | "Pause" | "Reset" | "Step [n] frames"
DIAGNOSIS       "Why is the sim crashing?" | "Validate the scene"
SNAPSHOTS       "Take a snapshot" | "Roll back to my last snapshot"
```

---

*Isaac Assist — built by [10Things, Inc.](https://www.10things.tech)*  
*Isaac Sim is a product of NVIDIA Corporation.*
