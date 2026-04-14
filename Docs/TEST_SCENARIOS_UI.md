# Isaac Assist — In-UI Test Scenarios

## Startup

### Terminal 1: Start the FastAPI Service
```bash
cd /path/to/Omniverse_Nemotron_Ext
source service/.env 2>/dev/null; set -a; source .env 2>/dev/null; set +a
uvicorn service.isaac_assist_service.main:app --host 0.0.0.0 --port 8000 --reload
```
Wait until you see `Uvicorn running on http://0.0.0.0:8000`.

### Terminal 2: Launch Isaac Sim
```bash
cd /path/to/Omniverse_Nemotron_Ext
./launch_isaac.sh
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
> Create a cylinder at -1, 0, 0.5 named Pillar, height 1, radius 0.2 name it cilly_the_cylinder

**Verify:** Cylinder appears at the specified position after approval.

---

### T04 — Delete a Prim
**Prerequisite:** T01 (MyCube exists)

**Type:**
### T08 — Make a Deformable Cloth
**Type:**
> Create a plane named ClothSheet at 0, 0, 3 and make it a deformable cloth

**Expected:** Code patch creating a mesh plane and applying `PhysxSchema.PhysxDeformableBodyAPI` or cloth API. After approval:
- Play the sim — the sheet should drape/fall softly rather than as a rigid body

> Delete /World/MyCube

**Expected:** Code patch using `stage.RemovePrim(...)`. After approval:
- MyCube disappears from viewport and Stage tree

---

### T05 — Scene Summary (Data Query)
**Type:**
> What's in the scene right now?

**Expected:** Isaac Assist returns a text description listing prims currently in the stage. No code patch — just a text rep
### T08 — Make a Deformable Cloth
**Type:**
> Create a plane named ClothSheet at 0, 0, 3 and make it a deformable cloth

**Expected:** Code patch creating a mesh plane and applying `PhysxSchema.PhysxDeformableBodyAPI` or cloth API. After approval:
- Play the sim — the sheet should drape/fall softly rather than as a rigid body
ly summarizing the scene hierarchy. This tests the `scene_summary` data handler.

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
- LLM Model field (should show `claude-opus-4-6` or your configured model)
- "Contribute Fine-Tuning Data" checkbox
- **"Auto-Approve Code Patches"** checkbox (default: unchecked)
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

### T31 — Wire ROS2 JointState Graph for Franka
**Prerequisite:** T11 (Franka robot loaded at `/World/Franka`). ROS2 bridge extension should be enabled.

**Type:**
> Wire a full ROS2 JointState publisher and subscriber OmniGraph for /World/Franka. Add a ROS2SubscribeJointState node on /joint_command, an IsaacArticulationController targeting /World/Franka, and a ROS2PublishJointState node publishing to /joint_states. Connect them all to the OnPlaybackTick in /World/FrankaROS2Graph.

**Expected:** Code patch that:
1. Adds `ROS2SubscribeJointState` node (subscribes to `/joint_command`)
2. Adds `IsaacArticulationController` node (targets `/World/Franka`)
3. Adds `ROS2PublishJointState` node (publishes to `/joint_states`)
4. Wires all nodes from the existing `OnPlaybackTick` tick source

**Verify after approval:**
- `/World/FrankaROS2Graph` in the Stage tree now contains multiple OmniGraph nodes
- Open **Window → Visual Scripting → Action Graph** and select `/World/FrankaROS2Graph` — nodes and connections visible
- Play the sim → run `ros2 topic list` in a terminal — `/joint_states` and `/joint_command` topics should appear

---

### T32 — Send ROS2 Joint Command to Franka
**Prerequisite:** T31 (ROS2 JointState graph wired), simulation playing

**In a ROS2-sourced terminal, run:**
```bash
ros2 topic pub --once /joint_command sensor_msgs/msg/JointState \
  "{header: {stamp: {sec: 0}, frame_id: ''}, name: ['panda_joint1','panda_joint2','panda_joint3','panda_joint4','panda_joint5','panda_joint6','panda_joint7'], position: [0.0, -0.5, 0.0, -1.5, 0.0, 1.2, 0.8], velocity: [], effort: []}"
```

**Verify:**
- Franka arm moves to the commanded joint positions in the viewport
- `ros2 topic echo /joint_states` shows updated position values matching the command

_Skip T31–T32 if ROS2 is not installed or the ROS2 bridge extension is unavailable._

---

### T33 — GPU-Batched Cloning (GridCloner)
**Prerequisite:** A prim exists (e.g., `/World/Ball` from T02)

**Type:**
> Clone /World/Ball 20 times in a grid layout with collision filtering

**Expected:** Code patch using `isaacsim.core.cloner.GridCloner` (not `Sdf.CopySpec`) because count ≥ 4. The code should:
1. Define a `GridCloner` with spacing
2. Call `generate_paths()` and `clone()`
3. Apply `filter_collisions()` to prevent self-collision

**Verify after approval:**
- 20 copies of Ball arranged in a grid pattern in the viewport
- Stage tree shows `/World/Ball_01` through `/World/Ball_20` (or similar)
- Play the sim — cloned objects should not collide with each other if they overlap

---

### T34 — Motion Planning: Move Robot to Pose
**Prerequisite:** T11 (Franka robot loaded at `/World/Franka`)

**Type:**
> Move the Franka end-effector to position 0.4, 0.3, 0.5 with the gripper pointing down

**Expected:** Code patch using RMPflow (Lula) motion planning. The code should:
1. Load the Franka RMPflow config from `isaacsim.robot_motion.motion_policy`
2. Set a target pose for the end-effector
3. Step the RMP to compute joint targets

**Verify after approval:**
- Play the sim — Franka arm smoothly moves to reach position (0.4, 0.3, 0.5)
- End-effector orientation is approximately pointing down
- No collision with the table/ground

---

### T35 — Multi-Waypoint Trajectory Planning
**Prerequisite:** T11 (Franka robot loaded)

**Type:**
> Plan a trajectory for /World/Franka through these waypoints: pick at 0.4,0,0.2, lift to 0.4,0,0.5, place at -0.4,0,0.2

**Expected:** Code patch using Lula RRT trajectory planner. The code should:
1. Load the Franka URDF and robot description
2. Plan through each waypoint sequentially
3. Concatenate trajectory segments

**Verify after approval:**
- Play the sim — Franka arm follows the pick → lift → place path
- Motion is smooth and collision-free

---

### T36 — Asset Catalog Search
**Type:**
> Search the asset catalog for UR10 robots

**Expected:** Text reply (no code patch) listing matching assets from the catalog. Should include:
- Asset name and USD path for UR10 variants
- Confidence/match scores

**Then type:**
> Search for any wheeled robots

**Verify:** Returns results for any available wheeled robot assets (e.g., Carter, Jetbot, Nova Carter). If no matches, Isaac Assist explains that no wheeled robots were found in the catalog.

---

### T37 — Natural Language Scene Builder
**Type:**
> Design a warehouse scene with a conveyor belt in the center, two Franka robots on either side, and a stack of boxes at the start of the conveyor

**Expected flow:**
1. First, a `generate_scene_blueprint` data response returns a JSON blueprint listing available assets and a recommended spatial layout
2. Isaac Assist uses the blueprint to call `build_scene_from_blueprint` with a code patch
3. The code patch creates all objects with correct positions and orientations

**Verify after approval:**
- Multiple prims appear forming a warehouse-like layout
- Two robot prims placed on opposite sides
- Box prims stacked at one end
- Spatial arrangement is reasonable (no overlapping objects)

---

### T38 — IsaacLab RL Environment Setup
**Type:**
> Create an IsaacLab reinforcement learning environment for a Franka manipulation task — the robot should learn to pick up a cube

**Expected flow:**
1. Isaac Assist calls `create_isaaclab_env` (data handler) which returns a scaffolded `ManagerBasedRLEnv` config
2. A follow-up code patch writes the environment Python file with:
   - Observation space (joint positions, object pose)
   - Action space (joint position targets)
   - Reward function (distance to cube, grasp success)

**Then type:**
> Launch training for this environment with 512 parallel envs

**Expected:** Code patch that launches `isaaclab.train` via subprocess with `--num_envs 512`. After approval:
- A training process starts (may fail if IsaacLab is not installed — Isaac Assist should note this)
- Check terminal output for training logs

_Skip T38 if IsaacLab is not installed._

---

### T39 — Auto-Approve Mode
1. Open Settings (⚙)
2. Check **"Auto-Approve Code Patches"**
3. Click **Save Settings**

**Type:**
> Create a cube named AutoCube at 0, 0, 1

**Expected flow:**
1. Isaac Assist responds with text
2. Instead of showing an "Approve & Execute" card, a system message "Auto-approved: ..." appears
3. The code executes immediately without user interaction

**Verify:**
- AutoCube appears in the viewport without clicking any approval button
- The patch still gets logged to the audit trail

**Then disable auto-approve:**
1. Open Settings (⚙), uncheck "Auto-Approve Code Patches", Save
2. Type: `Create a sphere named ManualBall at 1, 0, 1`
3. **Verify:** An approval card appears again — auto-approve is off

---

### T40 — Import Nova Carter Robot
**Type:**
> Import a Nova Carter robot at position 0, 0, 0

**Expected:** Code patch using a USD reference to the Nova Carter asset. After approval:
- A Nova Carter wheeled robot appears at the origin
- `/World/NovaCarter` (or similar) in the Stage tree
- Differential drive base and sensor mounts visible

_Note: Requires Isaac Sim robot asset library. If the asset isn't found, Isaac Assist should explain._

---

### T41 — Nova Carter: Physics + Anchor + ROS2 Twist Drive
**Prerequisite:** T40 (Nova Carter loaded at `/World/NovaCarter`)

**Step 1 — Anchor the robot:**
**Type:**
> Anchor /World/NovaCarter to the ground plane so it doesn't float

**Expected:** Code patch using `anchor_robot` tool — sets `PhysxArticulationAPI.fixedBase=True` and deletes rootJoint. After approval:
- Play the sim — Nova Carter stays on the ground, wheels touch the surface

**Step 2 — Wire ROS2 Twist drive graph:**
**Type:**
> Create a ROS2 OmniGraph for /World/NovaCarter: subscribe to /cmd_vel (Twist), connect to a DifferentialController, then to an ArticulationController targeting the robot. Add a ROS2PublishOdometry node publishing to /odom. Use /World/CarterROS2Graph.

**Expected:** Code patch creating an action graph with:
1. `OnPlaybackTick` tick source
2. `ROS2SubscribeTwist` (subscribes to `/cmd_vel`)
3. `DifferentialController` (converts twist to wheel velocities)
4. `IsaacArticulationController` (targets `/World/NovaCarter`)
5. `ROS2PublishOdometry` (publishes to `/odom`)

**Verify after approval:**
- `/World/CarterROS2Graph` exists in Stage tree with all nodes
- Play the sim → `ros2 topic list` shows `/cmd_vel` and `/odom`

**Step 3 — Drive the robot:**
```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.5, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

**Verify:** Nova Carter drives forward in the viewport.

---

### T42 — Add RealSense Camera to Nova Carter
**Prerequisite:** T40 (Nova Carter loaded)

**Type:**
> Add an Intel RealSense D435i camera to /World/NovaCarter/chassis_link, positioned at 0.2, 0, 0.15 facing forward

**Expected:** Code patch using `add_sensor_to_prim` with `product_name: "RealSense D435i"`. After approval:
- A camera prim appears under `/World/NovaCarter/chassis_link/RealSenseD435i` (or similar)
- Camera FOV: 87°, resolution: 1280×720 (from product spec)

**Then wire ROS2 image publishing:**
**Type:**
> Create a ROS2 camera publisher OmniGraph at /World/CameraROS2Graph: create a render product from /World/NovaCarter/chassis_link/RealSenseD435i, then publish RGB to /carter/camera/rgb and depth to /carter/camera/depth

**Verify after approval:**
- Play the sim → `ros2 topic list` shows `/carter/camera/rgb` and `/carter/camera/depth`
- `ros2 topic hz /carter/camera/rgb` shows ~30 Hz

---

### T43 — Vision Spatial Awareness (Gemini Robotics-ER)
**Prerequisite:** T40 + T42 (Nova Carter with RealSense camera in a scene with objects)

First, add some objects to the scene:
**Type:**
> Create a red cube named TargetBox at 2, 0, 0.25 with size 0.5. Create a blue cylinder named Obstacle at 1, 0.5, 0.5.

Approve the patches. Then:

**Step 1 — Object detection from viewport:**
**Type:**
> Use vision to detect all objects in the current viewport

**Expected:** Text reply listing detected objects with normalized 2D coordinates (from `vision_detect_objects`):
- TargetBox, Obstacle, NovaCarter, ground plane, etc.

**Step 2 — Spatial reasoning:**
**Type:**
> Looking at the scene, what is to the right of the red cube? And how far is the blue cylinder from the robot?

**Expected:** Text reply from `vision_analyze_scene` with spatial analysis:
- Describes relative positions of objects
- Estimates distances based on visual cues

**Step 3 — Bounding boxes:**
**Type:**
> Get bounding boxes for all objects in the viewport

**Expected:** Returns `vision_bounding_boxes` data with [ymin, xmin, ymax, xmax] coordinates for each detected object.

---

### T44 — Vision-Guided Navigation Command
**Prerequisite:** T41 + T43 (Nova Carter with ROS2 drive + vision + objects in scene)

**Type:**
> Look at the scene and plan a trajectory to drive the robot to the right side of the red cube, avoiding the blue cylinder

**Expected flow:**
1. Isaac Assist calls `vision_analyze_scene` to understand the spatial layout
2. Calls `vision_plan_trajectory` with instruction "drive to the right side of the red cube, avoiding the blue cylinder"
3. Returns a sequence of 2D waypoints forming a collision-free path
4. Optionally generates a code patch to publish the waypoints as ROS2 nav goals

**Verify:**
- Trajectory points form a reasonable path that curves around the cylinder
- If code patch published, `ros2 topic echo /cmd_vel` shows changing velocity commands

**Then type:**
> Describe what the robot's onboard camera can see right now

**Expected:** `vision_analyze_scene` with the question, returning a spatial description of the scene from the viewport perspective (objects, relative positions, distances).

_Skip T43–T44 if Gemini API key is not configured._

---

### T45 — Export Scene Package
**Prerequisite:** Several patches approved and executed in the session (e.g., T01–T03 or T40–T42)

**Type:**
> Export this scene as a project package named "carter_navigation_demo"

**Expected flow:**
1. Isaac Assist calls `export_scene_package` with scene_name "carter_navigation_demo"
2. Text reply confirms export with file list

**Verify:**
- Directory `workspace/scene_exports/carter_navigation_demo/` created with:
  - `scene_setup.py` — all approved patches as a single runnable Python script
  - `README.md` — scene description, robot list, ROS2 topics, usage instructions
  - `ros2_topics.yaml` — detected ROS2 topics and OmniGraph node types
  - `ros2_launch.py` — ROS2 launch file template (if ROS2 nodes were used)
- `scene_setup.py` is valid Python (runs without syntax errors)
- `README.md` lists the correct robots and topics

**Via API (optional verification):**
```bash
curl -X POST http://localhost:8000/api/v1/chat/export_scene \
  -H "Content-Type: application/json" \
  -d '{"scene_name": "carter_navigation_demo", "session_id": "default_session"}'
```

**Download a file:**
```bash
curl "http://localhost:8000/api/v1/chat/export_scene/download?filepath=workspace/scene_exports/carter_navigation_demo/scene_setup.py" -o scene_setup.py
```

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
| T31 | ROS2 | Wire JointState graph | ✅ |
| T32 | ROS2 | Send joint command | N/A (external ROS2 cmd) |
| T33 | Clone (batch) | GPU-batched GridCloner | ✅ |
| T34 | Motion planning | Move robot to pose | ✅ |
| T35 | Motion planning | Multi-waypoint trajectory | ✅ |
| T36 | Data query | Asset catalog search | ❌ (text only) |
| T37 | Scene builder | NL warehouse scene | ✅ |
| T38 | IsaacLab RL | RL env + launch training | ✅ |
| T39 | Settings | Auto-approve toggle | ✅ (then ❌) |
| T40 | Import | Nova Carter robot | ✅ |
| T41 | ROS2 + Physics | Carter anchor + Twist drive | ✅ |
| T42 | Sensor + ROS2 | RealSense camera + image pub | ✅ |
| T43 | Vision | Spatial awareness (Gemini ER) | ❌ (data only) |
| T44 | Vision + Nav | Vision-guided navigation | ❌ (data) / ✅ (nav patch) |
| T45 | Export | Scene package export | ❌ (data only) |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Failed to communicate with service" | FastAPI not running → check Terminal 1 |
| "Mock echo: ..." responses | `aiohttp` not installed in Isaac Sim's Python → run `pip install aiohttp` inside Isaac Sim's Python |
| No code patches returned | LLM may not be calling tools → check `LLM_MODE` and API key in `.env` |
| Code patch fails on execution | Check Isaac Sim console (Window → Console) for Python errors |
| Chat panel not visible | Window → Isaac Assist (menu bar) |
| Extension not loaded | Verify `--ext-folder` path in launch script matches your extension directory |
