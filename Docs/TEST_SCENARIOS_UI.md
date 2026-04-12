# Isaac Assist — In-UI Test Scenarios

## Startup

### Terminal 1: Start the FastAPI Service
```bash
cd ${HOME}/Omniverse_Nemotron_Ext
source service/.env 2>/dev/null; set -a; source .env 2>/dev/null; set +a
uvicorn service.isaac_assist_service.main:app --host 0.0.0.0 --port 8000 --reload
```
Wait until you see `Uvicorn running on http://0.0.0.0:8000`.

### Terminal 2: Launch Isaac Sim
```bash
cd ${HOME}/Omniverse_Nemotron_Ext
./launch_isaac_fixed.sh
```
Wait for Isaac Sim to fully load (viewport shows the default ground plane or empty stage).

### Open the Chat Panel
**Window → Isaac Assist** (menu bar). The "Isaac Assist AI" panel should appear docked to the right.

### Quick Health Check
Type into the chat panel:
> Hello, what can you do?

**Expected:** Isaac Assist replies with a summary of its capabilities (create prims, physics, materials, etc.). If you see an error like `Failed to communicate with service`, the FastAPI service isn't reachable — check Terminal 1.

---

## Test Scenarios

Each scenario below shows: **what to type** in the chat panel, **what to click**, and **what to verify** in the viewport/stage tree.

---

### T01 — Create a Cube
**Type:**
> Create a cube named MyCube at position 0, 0, 0.5

**Expected flow:**
1. Isaac Assist responds with a text explanation
2. A **TOOL-GENERATED PATCH** card appears with Python code using `UsdGeom.Cube.Define(...)` or `stage.DefinePrim(...)`
3. Click **"Approve & Execute"**

**Verify:**
- A cube named `MyCube` appears in the viewport at height 0.5
- In the Stage panel (left sidebar), `/World/MyCube` is visible

---

### T02 — Create a Sphere
**Type:**
> Add a sphere named Ball at position 2, 0, 1 with radius 0.3

**Expected:** Code patch to define a `UsdGeom.Sphere`. After approval:
- Sphere appears at (2, 0, 1) in viewport
- `/World/Ball` in Stage tree

---

### T03 — Create a Cylinder
**Type:**
> Create a cylinder at -1, 0, 0.5 named Pillar, height 1, radius 0.2

**Verify:** Cylinder appears at the specified position after approval.

---

### T04 — Delete a Prim
**Prerequisite:** T01 (MyCube exists)

**Type:**
> Delete /World/MyCube

**Expected:** Code patch using `stage.RemovePrim(...)`. After approval:
- MyCube disappears from viewport and Stage tree

---

### T05 — Scene Summary (Data Query)
**Type:**
> What's in the scene right now?

**Expected:** Isaac Assist returns a text description listing prims currently in the stage. No code patch — just a text reply summarizing the scene hierarchy. This tests the `scene_summary` data handler.

---

### T06 — Add Rigid Body Physics
**Prerequisite:** Create a cube first (T01) or type:
> Create a cube named PhysBox at 0, 0, 2

Approve the patch. Then:

**Type:**
> Add rigid body physics to /World/PhysBox

**Expected:** Code patch applying `UsdPhysics.RigidBodyAPI` and `UsdPhysics.CollisionAPI`. After approval:
- PhysBox now has physics icons in the Stage panel
- **Click Play (▶) in the timeline.** PhysBox should fall due to gravity

---

### T07 — Add a Ground Plane Collider
**Type:**
> Create a ground plane with collision at position 0, 0, 0

**Verify:** A ground plane prim appears. When you play the sim, PhysBox from T06 lands on the ground instead of falling forever.

---

### T08 — Make a Deformable Cloth
**Type:**
> Create a plane named ClothSheet at 0, 0, 3 and make it a deformable cloth

**Expected:** Code patch creating a mesh plane and applying `PhysxSchema.PhysxDeformableBodyAPI` or cloth API. After approval:
- Play the sim — the sheet should drape/fall softly rather than as a rigid body

---

### T09 — Apply a Red Material
**Prerequisite:** A prim exists (e.g., create a sphere)

**Type:**
> Make /World/Ball red and metallic

**Expected:** Code patch creating an `OmniPBR` material with diffuse color (1, 0, 0) and high metallic value, then binding it. After approval:
- The sphere turns red and shiny in the viewport

---

### T10 — Apply a Glass Material
**Type:**
> Create a sphere named GlassBall at 1, 1, 0.5 and make it transparent glass

**Verify:** A sphere with an `OmniGlass`-style material (transparent, refractive) appears.

---

### T11 — Import a Robot (Franka)
**Type:**
> Import a Franka Emika Panda robot at the origin

**Expected:** Code patch using Isaac Sim's robot loader or a USD reference. After approval:
- A Franka robot model appears at the origin
- Articulation joints visible in the property panel

_Note: This requires the Isaac Sim asset library to be accessible. If the asset isn't found, Isaac Assist should explain what happened._

---

### T12 — Simulation Play/Pause/Stop
**Type each in sequence:**
> Play the simulation

**Verify:** Timeline starts playing (▶ icon active, physics simulates).

> Pause the simulation

**Verify:** Timeline pauses, objects freeze mid-motion.

> Stop the simulation

**Verify:** Timeline stops, objects reset to initial positions.

> Step the simulation forward 10 frames

**Verify:** Timeline advances exactly 10 frames.

---

### T13 — Selection-Aware Context
1. **Click on a prim** in the viewport (e.g., select `/World/Ball`)
2. You should see a selection chip `[/World/Ball]` appear when you type

**Type:**
> Make this bigger

**Expected:** Isaac Assist knows you mean `/World/Ball` (from selection context). Code patch scales the selected prim. After approval:
- The selected prim is larger

---

### T14 — Selection + Attribute Change
1. Select a prim in the viewport
2. **Type:**
> Change the color of this to blue

**Expected:** Code patch references the selected prim path and applies a blue material. After approval:
- Selected prim turns blue

---

### T15 — Create a Camera
**Type:**
> Add a camera named TopCam at position 0, 0, 5 looking down

**Expected:** Code patch creating a `UsdGeom.Camera` prim with a downward orientation. After approval:
- Camera prim appears in Stage tree at `/World/TopCam`

---

### T16 — Set Viewport Camera
**Prerequisite:** T15 (TopCam exists)

**Type:**
> Switch the viewport to use /World/TopCam

**Expected:** Viewport perspective changes to the top-down camera view.

---

### T17 — Capture Screenshot
**Type:**
> Capture a screenshot of the current viewport

**Expected:** Isaac Assist returns a viewport capture (text description or base64-encoded image reference). No code patch needed — this is a data query.

---

### T18 — Add a Light
**Type:**
> Add a dome light with intensity 1000

**Verify:** Scene lighting changes after approval. A DomeLight prim appears in the Stage tree.

---

### T19 — Create an OmniGraph
**Type:**
> Create a ROS2 clock publisher OmniGraph

**Expected:** Code patch using OmniGraph APIs to create action graph nodes for ROS2 clock publishing. After approval:
- `/World/ActionGraph` (or similar) appears in Stage tree
- Graph nodes visible in the OmniGraph editor

---

### T20 — Multi-Object Scene Build
**Type:**
> Create a table scene: a flat box as a table at 0,0,0.5 (size 2x1x0.05), and three small spheres on top at positions -0.5,0,0.55 and 0,0,0.55 and 0.5,0,0.55

**Expected:** Multiple code patches (or one combined patch) creating all objects. After approval:
- A flat box and 3 spheres arranged like a table scene

---

### T21 — Console Error Check
**Type:**
> Are there any errors in the console?

**Expected:** Text reply listing recent Kit console warnings/errors (or "no errors found"). No code patch.

---

### T22 — Sensor Spec Lookup
**Type:**
> What are the specs for the Intel RealSense D455?

**Expected:** Text reply with camera product specifications (resolution, FOV, depth range, etc.) from the embedded sensor knowledge base.

---

### T23 — Add a Sensor to a Prim
**Prerequisite:** A robot or prim exists

**Type:**
> Add a camera sensor to /World/Franka

**Expected:** Code patch adding a camera prim as a child of the robot. After approval:
- A camera prim appears under the specified parent in the Stage tree.

---

### T24 — Clone a Prim
**Prerequisite:** A prim exists (e.g., `/World/Ball`)

**Type:**
> Clone /World/Ball to /World/Ball_Copy at position 3, 0, 0.5

**Expected:** Code patch duplicating the prim. After approval:
- A copy of Ball appears at the new position

---

### T25 — Teleport (Move) a Prim
**Type:**
> Move /World/Ball to position 0, 5, 1

**Expected:** Code patch setting the translate attribute. After approval:
- Ball moves to (0, 5, 1) in the viewport

---

### T26 — Swarm Patch (multi-agent planning)
Messages starting with `patch` or `fix` route to the multi-agent swarm.

**Type:**
> patch: The robot should pick up an object from the table and place it 1 meter to the right

**Expected:**
1. "Submitting query to the Coder/QA/Critic multi-agent swarm..." message appears
2. After 1-3 minutes, a **SWARM EXECUTABLE PATCH** card with confidence score
3. Click **"Review & Approve Execution"** → approval dialog opens
4. Review the code, then click **EXECUTE PATCH** or **REJECT**

---

### T27 — Settings Panel
1. Click the **⚙** (gear icon) in the chat panel header
2. The "Isaac Assist Settings" window opens

**Verify:**
- OpenAI API Base field (shows current value)
- API Key field (password masked)
- LLM Model field (should show `claude-sonnet-4-6` or your configured model)
- "Contribute Fine-Tuning Data" checkbox
- **Save Settings** and **Export Training Data** buttons

Click **Save Settings** → should see "Settings successfully updated dynamically" in chat.

---

### T28 — Export Training Data
1. Open Settings (⚙)
2. Click **"Export Training Data"**

**Expected:** Chat shows "Triggering local Knowledge Base export for Fine-tuning..." then "Export successful."

---

### T29 — Physics Parameters
**Type:**
> Set gravity to 0 and the time step to 1/120

**Expected:** Code patch modifying `PhysicsScene` attributes. After approval:
- Play the sim — objects should float (zero gravity)

---

### T30 — LiveKit Vision/Voice (Optional)
**Prerequisite:** LiveKit server running (`docker compose up` in `infra/livekit/`)

1. Click **"Start Vision / Voice"** button
2. **Expected:** Chat shows "Connected to LiveKit. The AI can now see your screen and talk to you."
3. Button text changes to **"Stop Vision"**

_Skip this test if LiveKit is not configured._

---

## Summary Checklist

| # | Category | Prompt | Requires Approval? |
|---|----------|--------|-------------------|
| T01 | Create prim | Create a cube | ✅ |
| T02 | Create prim | Add a sphere | ✅ |
| T03 | Create prim | Create a cylinder | ✅ |
| T04 | Delete prim | Delete a prim | ✅ |
| T05 | Data query | Scene summary | ❌ (text only) |
| T06 | Physics | Add rigid body | ✅ |
| T07 | Physics | Ground collider | ✅ |
| T08 | Deformable | Cloth simulation | ✅ |
| T09 | Material | Red metallic | ✅ |
| T10 | Material | Glass | ✅ |
| T11 | Import | Franka robot | ✅ |
| T12 | Sim control | Play/Pause/Stop/Step | ✅ |
| T13 | Selection | Scale selected prim | ✅ |
| T14 | Selection | Color selected prim | ✅ |
| T15 | Create camera | Top-down camera | ✅ |
| T16 | Viewport | Switch camera | ✅ |
| T17 | Data query | Capture screenshot | ❌ |
| T18 | Lighting | Dome light | ✅ |
| T19 | OmniGraph | ROS2 clock graph | ✅ |
| T20 | Complex | Multi-object scene | ✅ |
| T21 | Data query | Console errors | ❌ (text only) |
| T22 | Knowledge | Sensor specs | ❌ (text only) |
| T23 | Sensor | Add camera sensor | ✅ |
| T24 | Clone | Clone a prim | ✅ |
| T25 | Transform | Move a prim | ✅ |
| T26 | Swarm | Multi-agent patch | ✅ (special) |
| T27 | Settings | Settings panel | N/A (UI) |
| T28 | Export | Training data | N/A (UI button) |
| T29 | Physics | Gravity/timestep | ✅ |
| T30 | LiveKit | Vision/Voice stream | N/A (optional) |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Failed to communicate with service" | FastAPI not running → check Terminal 1 |
| "Mock echo: ..." responses | `aiohttp` not installed in Isaac Sim's Python → run `pip install aiohttp` inside Isaac Sim's Python |
| No code patches returned | LLM may not be calling tools → check `LLM_MODE` and API key in `.env` |
| Code patch fails on execution | Check Isaac Sim console (Window → Console) for Python errors |
| Chat panel not visible | Window → Isaac Assist (menu bar) |
| Extension not loaded | Verify `--ext-folder` path in launch script matches your extension directory |
