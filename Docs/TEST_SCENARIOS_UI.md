# Isaac Assist — In-UI Test Scenarios

## Startup

### Terminal 1: Start the FastAPI Service
```bash
cd /path/to/Omniverse_Nemotron_Ext
./launch_service.sh              # Interactive menu — pick local/anthropic/cloud/openai/grok
./launch_service.sh anthropic    # Or pass mode directly (no menu)
./launch_service.sh local        # Ollama on local GPU
```
Wait until you see `Uvicorn running on http://0.0.0.0:8000`.

**Hot-switch without restart** (while the service is already running):
```bash
curl -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "local"}'
```

### Terminal 2: Launch Isaac Sim
```bash
cd /path/to/Omniverse_Nemotron_Ext
./launch_isaac.sh
```
Wait for Isaac Sim to fully load (viewport shows the default ground plane or empty stage).

### Terminal 3: Launch Rosbridge (for ROS2 live tools T52–T57)
```bash
source /opt/ros/${ROS_DISTRO}/setup.bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
```
Wait until you see `Rosbridge WebSocket server started on port 9090`. This bridges ROS2 topics/services over WebSocket so Isaac Assist can interact with live ROS2 data.

_Skip Terminal 3 if you don't need ROS2 live tools (T52–T57). The remaining tests work without rosbridge._

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
- LLM Model field (should show `claude-opus-4-7` or your configured model)
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

### T41 — Nova Carter: Physics + ROS2 Differential Drive
**Prerequisite:** T40 (Nova Carter loaded at `/World/NovaCarter`). Ground plane with collision (T07).

_Nova Carter is a **differential-drive** wheeled robot: 2 powered front wheels + 2 free-spinning rear caster wheels. Do NOT use `fixedBase=True` — that pins the robot in place. Wheeled robots need to remain mobile._

**Step 1 — Add physics and ensure wheels contact the ground:**
**Type:**
> Add rigid body physics and collision to /World/NovaCarter so it sits on the ground plane under gravity. Make sure the wheels have colliders. Do NOT set fixedBase — this is a mobile robot that needs to drive.

**Expected:** Code patch that:
- Applies `RigidBodyAPI` and `CollisionAPI` to the chassis/links as needed
- Deletes any `rootJoint` (6-DOF free joint) that would cause instability
- Does NOT set `fixedBase=True` (would prevent driving)

**Verify:** Play the sim — Nova Carter drops onto the ground plane and rests on its 4 wheels (2 front drive + 2 rear casters). It should not float or fly away.

**Step 2 — Wire ROS2 differential drive graph:**
**Type:**
> Create a ROS2 OmniGraph for /World/NovaCarter: subscribe to /cmd_vel (Twist), connect to a DifferentialController for the two front drive wheels, then to an ArticulationController targeting the robot. Add a ROS2PublishOdometry node publishing to /odom. Use /World/CarterROS2Graph.

**Expected:** Code patch creating an action graph with:
1. `OnPlaybackTick` tick source
2. `ROS2SubscribeTwist` (subscribes to `/cmd_vel`)
3. `DifferentialController` (converts linear/angular twist to left/right front wheel velocities)
4. `IsaacArticulationController` (targets `/World/NovaCarter`, drives the front wheel joints)
5. `ROS2PublishOdometry` (publishes to `/odom`)

**Verify after approval:**
- `/World/CarterROS2Graph` exists in Stage tree with all nodes
- DifferentialController should reference the two front wheel joints (rear casters are passive)
- Play the sim → `ros2 topic list` shows `/cmd_vel` and `/odom`

**Step 3 — Drive the robot:**
```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.5, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

**Verify:** Nova Carter drives forward in the viewport. Rear caster wheels spin freely as the front wheels are powered.

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

### T46 — Pipeline: Nova Carter in a Home (Autonomous)
**Prerequisite:** Fresh scene (click 🗑 New Scene), `AUTO_APPROVE=true` in Settings.

_This test uses the **Pipeline Executor** — a multi-phase autonomous planner that builds an entire ROS2-enabled robot simulation from a single prompt. Each phase executes sequentially with verification between steps._

**Type:**
> pipeline: Nova Carter in a home environment

**Expected flow:**
1. Chat shows "Generating pipeline plan for: Nova Carter in a home environment"
2. Chat shows "Plan: Nova Carter — Home Pipeline (5 phases, source: template)"
3. **Phase 1 — Scene Setup:** Ground plane + walls + table + shelf + chair created with collision
4. **Phase 2 — Robot Import:** Nova Carter imported at origin with physics (no fixedBase)
5. **Phase 3 — ROS2 Differential Drive:** OmniGraph at `/World/CarterROS2Graph` with Twist→DifferentialController→ArticulationController→Odometry
6. **Phase 4 — Sensor Setup:** RealSense D435i camera attached to chassis
7. **Phase 5 — Final Verification:** Scene summary listing all prims, physics, OmniGraph nodes

Each phase shows ✅ or ❌ status. If a phase fails, it retries once with a fix hint.

**Verify:**
- Final summary: "Pipeline complete: 5/5 phases succeeded"
- Viewport shows a room with furniture and a Nova Carter robot
- Stage tree has `/World/CarterROS2Graph` with OmniGraph nodes
- Play the sim → `ros2 topic list` shows `/cmd_vel` and `/odom`
- Drive the robot:
```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.5, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

---

### T47 — Pipeline: Jetbot in a Warehouse
**Prerequisite:** Fresh scene, `AUTO_APPROVE=true`.

**Type:**
> pipeline: Jetbot in a warehouse

**Expected flow:**
1. 5-phase pipeline (Scene Setup → Robot Import → ROS2 Drive → Camera → Verification)
2. Warehouse environment: shelves, conveyor placeholder, stacked boxes
3. Jetbot with differential drive OmniGraph at `/World/JetbotROS2Graph`

**Verify:**
- Jetbot appears in a warehouse-like scene
- ROS2 topics `/cmd_vel` and `/odom` available after playing the sim

---

### T48 — Pipeline: Franka Arm in an Office
**Prerequisite:** Fresh scene, `AUTO_APPROVE=true`.

**Type:**
> pipeline: Franka robot picking things in an office

**Expected flow:**
1. 4-phase pipeline (Scene Setup → Robot Import → ROS2 JointState → Verification)
2. Office environment: desks, chairs, cabinet + a table + target cube
3. Franka imported with `fixedBase=True` (stationary arm)
4. ROS2 JointState graph at `/World/FrankaROS2Graph`

**Verify:**
- Franka arm at origin with a small cube on a nearby table
- `ros2 topic list` shows `/joint_states` and `/joint_command`
- Send a joint command:
```bash
ros2 topic pub --once /joint_command sensor_msgs/msg/JointState \
  "{name: ['panda_joint1','panda_joint2','panda_joint3','panda_joint4','panda_joint5','panda_joint6','panda_joint7'], position: [0.0, -0.5, 0.0, -1.5, 0.0, 1.2, 0.8]}"
```

---

### T49 — Pipeline: Unitree G1 Humanoid
**Prerequisite:** Fresh scene, `AUTO_APPROVE=true`.

**Type:**
> pipeline: Unitree G1 in a home

**Expected flow:**
1. 4-phase pipeline (Scene Setup → Robot Import → ROS2 JointState → Verification)
2. Home environment with walls, furniture
3. G1 humanoid imported without fixedBase
4. JointState ROS2 graph at `/World/G1ROS2Graph`

**Verify:**
- G1 robot standing in a room
- Joint states publishable via ROS2

_Note: If the G1 asset is not in the catalog, Phase 2 will search for alternatives. May fall back to a generic humanoid._

---

### T50 — Pipeline Error Recovery
**Prerequisite:** Fresh scene, `AUTO_APPROVE=true`.

This tests the pipeline's retry mechanism. Intentionally corrupt the scene between phases.

1. **Type:**
> pipeline: Nova Carter in a simple environment

2. While Phase 1 is running, do nothing — let it complete.
3. After Phase 2 starts, watch the chat.
4. If any phase shows ❌, verify:
   - "Retrying with fix hint..." message appears
   - The retry attempts to correct the issue
   - Final summary shows the retry result

**Verify:**
- Failed phases show retry attempts
- Knowledge base learns from failures (`/api/v1/chat/log_execution` called with `success: false`)

---

### T51 — Pipeline via API (Headless)
**Prerequisite:** FastAPI service running. Isaac Sim with Kit RPC running (port 8001).

This tests the pipeline plan endpoint directly, without the extension UI.

**Step 1 — Get a plan:**
```bash
curl -s -X POST http://localhost:8000/api/v1/chat/pipeline/plan \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Nova Carter in a warehouse"}' | python3 -m json.tool
```

**Expected:** JSON with `title`, `phases` array (5 items), `source: "template"`.

**Step 2 — Execute phases manually:**
For each phase in the plan, send the phase prompt to `/api/v1/chat/message`:
```bash
curl -s -X POST http://localhost:8000/api/v1/chat/message \
  -H "Content-Type: application/json" \
  -d '{"session_id": "pipeline_test", "message": "<phase prompt here>"}' | python3 -m json.tool
```

**Verify:** Each phase returns `actions_to_approve` with code patches.

---

### T52 — ROS2 Connect (rosbridge)
**Prerequisite:** Rosbridge running (Terminal 3).

**Type:**
> Connect to the ROS2 system on localhost port 9090

**Expected:** Isaac Assist calls `ros2_connect`. Text reply confirms:
- WebSocket target set to 127.0.0.1:9090
- Connectivity test passes (ping OK, port open)

**Then type (with wrong port to test error handling):**
> Connect to rosbridge on 127.0.0.1 port 1234

**Expected:** Text reply showing port is closed or connection refused.

---

### T53 — ROS2 List Topics & Nodes
**Prerequisite:** T52 (rosbridge connected). Isaac Sim playing with at least one ROS2 OmniGraph (e.g., from T19, T31, or T41).

**Type:**
> What ROS2 topics are available right now?

**Expected:** Isaac Assist calls `ros2_list_topics`. Text reply lists all active topics with their types:
- `/clock` — `rosgraph_msgs/msg/Clock`
- `/joint_states` — `sensor_msgs/msg/JointState` (if Franka graph wired)
- `/cmd_vel` — `geometry_msgs/msg/Twist` (if Nova Carter graph wired)
- etc.

**Then type:**
> What ROS2 nodes are running?

**Expected:** Calls `ros2_list_nodes`. Lists active nodes (rosbridge, Isaac Sim bridge nodes).

---

### T54 — ROS2 Subscribe & Verify Data Flow
**Prerequisite:** T53 (topics visible). Simulation playing.

**Type:**
> Read one message from /clock

**Expected:** Calls `ros2_subscribe_once` with topic `/clock` and type `rosgraph_msgs/msg/Clock`. Returns the clock message with `sec` and `nanosec` fields.

**If a Franka ROS2 graph is active (T31):**
> Read the current joint states from /joint_states

**Expected:** Returns a JointState message with joint names and position values.

---

### T55 — ROS2 Publish & Drive Robot
**Prerequisite:** T41 or T47 (a robot with `/cmd_vel` subscriber and differential drive OmniGraph). Simulation playing.

**Type:**
> Publish a Twist message to /cmd_vel with linear x=0.5 to drive the robot forward

**Expected:** Calls `ros2_publish`. The robot moves forward briefly in the viewport.

**Then type:**
> Drive the robot forward for 3 seconds, then turn left for 2 seconds, then stop

**Expected:** Calls `ros2_publish_sequence` with 3 messages at 10 Hz:
1. `{linear: {x: 0.5}}` for 3s
2. `{angular: {z: 0.5}}` for 2s
3. `{linear: {x: 0.0}, angular: {z: 0.0}}` for 0.5s (stop)

**Verify:** Robot drives forward, turns left, then stops in the viewport.

---

### T56 — ROS2 Service Discovery & Call
**Prerequisite:** T52 (rosbridge connected). Simulation playing.

**Type:**
> List all available ROS2 services

**Expected:** Calls `ros2_list_services`. Text reply lists services from rosapi and any Isaac Sim services.

**Then type:**
> What type is the /rosapi/topics service?

**Expected:** Returns the service type for the rosapi topics introspection service.

---

### T57 — ROS2 Integration Pipeline (End-to-End)
**Prerequisite:** Fresh scene. `AUTO_APPROVE=true`. Rosbridge running.

_This test combines the pipeline executor with live ROS2 verification using the new ros-mcp tools._

**Step 1 — Build the scene:**
> pipeline: Nova Carter in a home environment

Wait for all 5 phases to complete.

**Step 2 — Verify ROS2 is working:**
> List all ROS2 topics

**Expected:** Topics include `/cmd_vel`, `/odom`, and any camera topics.

**Step 3 — Read live data:**
> Subscribe to /odom and show me the current position

**Expected:** Returns an Odometry message with position data.

**Step 4 — Drive the robot:**
> Drive the Nova Carter forward for 2 seconds at 0.3 m/s then stop

**Expected:** Robot moves in viewport. Subsequent `/odom` read shows changed position.

**Step 5 — Confirm position changed:**
> Read /odom again

**Expected:** Position values differ from Step 3.

_Skip T52–T57 if rosbridge is not installed or ROS2 is not available._

---

### T58 — LLM Mode Switching (Cloud ↔ Local)

_Tests the ability to switch between LLM providers at startup and at runtime._

**Step 1 — Check current mode:**
```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```
**Expected:** JSON with `llm_mode` and `model` fields showing the active provider.

**Step 2 — Hot-switch to local (Ollama):**
```bash
curl -s -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "local"}' | python3 -m json.tool
```
**Expected:** `{"status": "success", "llm_mode": "local", "model": "qwen3.5:35b"}`

**Step 3 — Verify in chat panel:**
Type: `Hello, what model are you?`
**Expected:** Response comes from the local Ollama model.

**Step 4 — Switch to Claude:**
```bash
curl -s -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "anthropic"}' | python3 -m json.tool
```
**Expected:** `{"status": "success", "llm_mode": "anthropic", "model": "claude-opus-4-7"}`

**Step 5 — Verify in chat again:**
Type: `Hello, what model are you?`
**Expected:** Response comes from Claude.

**Step 6 — Test invalid mode rejection:**
```bash
curl -s -X PUT http://localhost:8000/api/v1/settings/llm_mode \
  -H "Content-Type: application/json" -d '{"mode": "banana"}' | python3 -m json.tool
```
**Expected:** `400` error: `"Invalid mode 'banana'. Choose from: local, cloud, anthropic, openai, grok"`

**Step 7 — Startup with mode flag:**
Stop the service and restart with:
```bash
./launch_service.sh anthropic
```
**Verify:** Health check shows `llm_mode: "anthropic"` without any manual API call.

---

### T59 — Browse Nucleus Content Library
**Prerequisite:** Isaac Sim running (Terminal 2). A Nucleus server accessible (local or cloud).

_This test uses `omni.client` inside Isaac Sim's runtime (via Kit RPC) to browse Omniverse Nucleus server directories. The content library contains thousands of USD assets — robots, environments, props, materials._

**Type:**
> Browse the Isaac Sim content library for available robots

**Expected:** Isaac Assist calls `nucleus_browse` with path `/NVIDIA/Assets/Isaac/5.1/Robots`. Returns a list of folders/files:
- `Franka/`, `UR10/`, `Jetbot/`, `Carter/`, `Spot/`, etc.

**Then type:**
> Show me what's inside the Franka folder on Nucleus

**Expected:** Calls `nucleus_browse` with path `/NVIDIA/Assets/Isaac/5.1/Robots/Franka`. Returns USD files:
- `franka.usd`, `franka_instanceable.usd`, etc.

**Then try environments:**
> Browse the Nucleus content library for warehouse environments

**Expected:** Calls `nucleus_browse` with path `/NVIDIA/Assets/Isaac/5.1/Environments` or similar. Returns available environment folders.

_Skip T59 if no Nucleus server is accessible. The tool will return a connection error — Isaac Assist should explain this._

---

### T60 — Download Asset from Nucleus
**Prerequisite:** T59 (Nucleus browsable). Isaac Sim running.

**Type:**
> Download the UR10 robot from Nucleus to my local assets folder

**Expected flow:**
1. Isaac Assist calls `nucleus_browse` to find the UR10 path
2. Calls `download_asset` with `nucleus_url: "omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/UR10/ur10.usd"`
3. Asset is copied to `Desktop/assets/Nucleus_Downloads/Robots/UR10/ur10.usd`
4. Registered in `asset_catalog.json` with category "robot"

**Verify:**
- File exists at `~/Desktop/assets/Nucleus_Downloads/Robots/UR10/ur10.usd`
- Run: `python3 -c "import json; c=json.load(open('$HOME/Desktop/assets/asset_catalog.json')); print([a for a in c['assets'] if 'nucleus_download' in a.get('tags',[])][-1])"`
- The new entry has `"nucleus_source"` field and `"nucleus_download"` tag

**Then try a duplicate download:**
> Download the UR10 robot from Nucleus again

**Expected:** Returns `"status": "already_exists"` with the local path — no re-download.

---

### T61 — Nucleus Search → Download → Import (End-to-End)
**Prerequisite:** Isaac Sim running. Nucleus accessible. Fresh scene.

_This test chains the full workflow: search the catalog, download a missing asset, then import it into the scene._

**Type:**
> I need a Ridgeback robot with a Franka arm. Search for it, download it if needed, and import it at the origin.

**Expected flow:**
1. Isaac Assist calls `catalog_search` with query "ridgeback franka"
2. If found locally → calls `import_robot` directly
3. If NOT found locally → calls `nucleus_browse` to locate it on Nucleus → calls `download_asset` → then `import_robot`
4. Code patch references the local path for the USD

**Verify after approval:**
- Robot appears at the origin in the viewport
- The asset is now in the local catalog (searchable for future sessions)
- `catalog_search` for "ridgeback franka" returns the local path

**Via API (catalog verification):**
```bash
curl -s -X POST http://localhost:8000/api/v1/chat/message \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test", "message": "Search the asset catalog for ridgeback"}' | python3 -m json.tool
```

---

### T62 — Scene Readiness Check
**Type:**
> Is the scene ready for navigation?

**Expected:** Calls `check_scene_ready`. Returns structured report:
- Simulation playing state
- Robot(s) found in scene
- Drive graph wired (diff drive / holonomic / joint)
- Camera, LiDAR, IMU, odom, TF, clock topics
- Map availability
- Score (e.g. 7/11) + `suggested_next_steps` list

---

### T63 — Machine Specs + Suggestions
**Type:**
> What should I do next?

**Expected:** Calls `suggest_next_steps` (which internally calls `get_machine_specs` + `check_scene_ready`).

Example on DGX Spark:
- "Your RTX 6000 Ada (48 GB) supports full-resolution sensors and rtabmap SLAM."
- "Missing: no LiDAR topic — add one to enable SLAM and Nav2."
- "Suggested: 'add a Velodyne VLP-16 to the robot'"

---

### T64 — Launch RViz2 (Full)
**Prerequisite:** T41 or T57 (robot with active ROS2 topics). Rosbridge running.

**Type:**
> Launch RViz2 with all available sensors

**Expected:** Calls `launch_rviz2`. Auto-discovers topics, generates `.rviz` config with:
- Image panels for each camera topic
- LaserScan / PointCloud2 displays
- Odometry arrows
- TF tree
- Map display (if `/map` topic present)

Config saved to `workspace/rviz_configs/<scene_name>.rviz`.

**Verify:** RViz2 window opens with panels showing live sensor data.

---

### T65 — SLAM Mapping Session
**Prerequisite:** Robot with LiDAR or stereo cameras, ROS2 drive graph wired. Rosbridge running.

**Step 1 — Start SLAM:**
**Type:**
> Start mapping this room

**Expected:** Calls `slam_start`. Isaac Assist:
1. Calls `check_sensor_health` — verifies LiDAR or camera is healthy
2. Detects sensor type → selects algorithm (slam_toolbox for 2D LiDAR, rtabmap for RGB-D)
3. Launches SLAM node with auto-generated params
4. "Mapping started with slam_toolbox. Drive the robot around to build the map."

**Step 2 — Drive the robot** (from a ROS2 terminal):
```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.3}, angular: {z: 0.3}}"
```
Verify map building in RViz2 or: `ros2 topic hz /map`

**Step 3 — Save the map:**
**Type:**
> Stop mapping and save the map

**Expected:** Calls `slam_stop`. Saves to `workspace/maps/<scene>/map.pgm + map.yaml`. Returns coverage estimate.

---

### T66 — Nav2 Autonomous Navigation
**Prerequisite:** T65 complete (map saved). Robot with odom + LiDAR/scan publishing. Rosbridge running.

**Step 1 — Launch Nav2:**
**Type:**
> Launch navigation with the saved map

**Expected:** Calls `launch_nav2`:
1. Checks prerequisites (odom, scan, TF, clock)
2. Auto-generates `nav2_params.yaml` from scene state
3. Launches `ros2 launch nav2_bringup bringup_launch.py`

**Step 2 — Navigate to a coordinate:**
**Type:**
> Go to position 3, 2

**Expected:** Calls `nav2_goto(x=3.0, y=2.0)`. Publishes `/goal_pose`. Reports progress:
- "Navigating to (3.0, 2.0)... distance remaining: 2.4m"
- "✅ Arrived at (3.0, 2.0)"

**Step 3 — Navigate to a named location:**
First save a location:
> Save this location as kitchen

Then navigate:
> Go to the kitchen

**Expected:** Recalls saved pose, sends Nav2 goal.

---

### T67 — Vision-Language Navigation
**Prerequisite:** T42 (robot camera active), T66 (Nav2 running). Objects in scene.

**Type:**
> Move toward the red cube

**Expected flow:**
1. `capture_viewport()` → image
2. Gemini detects red cube at pixel (u, v)
3. Subscribes to depth topic → gets depth at pixel
4. Back-projects using camera intrinsics → 3D world coords
5. TF transform to map frame
6. `nav2_goto(x=world_x, y=world_y)`

**Verify:** Robot navigates toward the red cube in viewport.

---

### T68 — Semantic Segmentation Map
**Prerequisite:** Robot camera active in scene with semantically labelled prims.

**Step 1 — Label the scene:**
**Type:**
> Label all objects in the scene for segmentation

**Expected:** Auto-applies `SemanticLabel` API to all prims by name/type.

**Step 2 — Get segmentation map:**
**Type:**
> Show me the segmentation map from the robot's camera

**Expected:** Returns base64 segmentation image + label map JSON:
```json
{"labels": {"0": "background", "1": "floor", "2": "table", "3": "robot"}}
```

---

### T69 — Export Project ZIP
**Prerequisite:** Several patches approved + maps saved (e.g. after T65–T66).

**Type:**
> Export everything as a zip

**Expected flow:** Calls `export_project_zip`. Returns path to ZIP containing:
- `scene_setup.py` — all Isaac Sim patches
- `launch/` — `full_demo.launch.py`, `navigation.launch.py`, `visualization.launch.py`
- `config/nav2_params.yaml`, `slam_params.yaml`, `rviz_config.rviz`
- `maps/map.pgm` + `map.yaml`
- `urdf/robot.urdf`
- `scripts/teleop.py`, `patrol.py`
- `package.xml`, `CMakeLists.txt`

**Download:**
```bash
curl "http://localhost:8000/api/v1/chat/export_project_zip/download?path=workspace/exports/<name>.zip" -o project.zip
```

---

### T70 — Connect User's Own Robot Model
**Type:**
> Load my robot from ~/my_project/robot.urdf

**Expected flow:** Calls `connect_user_model`:
1. Validates and parses URDF
2. Imports into Isaac Sim via `import_robot`
3. Auto-detects joints, drive type, sensor mounts
4. Reports capabilities + suggests next steps:
   - "Differential drive detected (left_wheel, right_wheel)"
   - "1 camera mount at camera_link"
   - "Next: 'create a diff drive OmniGraph for /World/MyRobot'"
5. Registers in asset catalog for future sessions

---

### T71 — Full Autonomy Pipeline
**Prerequisite:** Fresh scene, `AUTO_APPROVE=true`.

**Type:**
> pipeline: Nova Carter autonomous navigation in a warehouse

**Expected flow (8 phases):**
1. **Scene Setup** — Ground plane + warehouse (shelves, walls, loading dock)
2. **Robot Import** — Nova Carter with physics, no fixedBase
3. **Drive Graph** — Diff drive OmniGraph + odom + clock
4. **Full Sensor Suite** — Stereo cameras + LiDAR + IMU + TF
5. **Verify ROS2** — Topic health check, all sensors green
6. **Launch SLAM** — slam_toolbox started; drive robot around
7. **Launch Nav2** — Map saved, Nav2 bringup with auto-generated params
8. **Final Verify** — Scene summary + topic list + suggested next steps

Each phase shows ✅ or ❌. Failed phases retry once with fix hint.

**Verify:**
- "Pipeline complete: 8/8 phases succeeded"
- Nova Carter in a warehouse scene with working ROS2 stack
- Type "go to position 5, 3" → robot navigates autonomously

---

### T72 — Sensor Health Check
**Prerequisite:** Robot with sensors running and ROS2 topics publishing.

**Type:**
> Check all sensor health

**Expected:** Calls `check_sensor_health`. Per-sensor report:
```
/camera/rgb       — ✅ healthy (30.1 Hz, 1280×720, rgb8)
/camera/depth     — ✅ healthy (30.0 Hz, 32FC1, range 0.3–8.0m)
/scan             — ⚠️  warning (5.2 Hz — expected ≥10 Hz)
/imu/data         — ❌ error (not publishing — check OmniGraph wiring)
```
With fix recommendations for each warning/error.

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
| T46 | Pipeline | Nova Carter home (autonomous) | ✅ (auto-approve) |
| T47 | Pipeline | Jetbot warehouse (autonomous) | ✅ (auto-approve) |
| T48 | Pipeline | Franka office (autonomous) | ✅ (auto-approve) |
| T49 | Pipeline | Unitree G1 home (autonomous) | ✅ (auto-approve) |
| T50 | Pipeline | Error recovery / retry | ✅ (auto-approve) |
| T51 | Pipeline | API-only headless plan | N/A (API test) |
| T52 | ROS2 Live | Connect to rosbridge | ❌ (data only) |
| T53 | ROS2 Live | List topics & nodes | ❌ (data only) |
| T54 | ROS2 Live | Subscribe & verify data | ❌ (data only) |
| T55 | ROS2 Live | Publish & drive robot | ❌ (data only) |
| T56 | ROS2 Live | Service discovery & call | ❌ (data only) |
| T57 | ROS2 Live | End-to-end pipeline + ROS2 | ✅ (auto-approve) + ❌ (data) |
| T58 | Settings | LLM mode switch (cloud ↔ local) | N/A (API + chat verify) |
| T59 | Nucleus | Browse content library | ❌ (data only) |
| T60 | Nucleus | Download asset to local | ❌ (data only) |
| T61 | Nucleus | Search → Download → Import | ✅ (import patch) |
| T62 | Autonomy | Scene readiness check | ❌ (data only) |
| T63 | Autonomy | Machine specs + next steps | ❌ (data only) |
| T64 | Autonomy | Launch RViz2 (full auto) | ❌ (subprocess) |
| T65 | Autonomy | SLAM mapping session | ❌ (subprocess) |
| T66 | Autonomy | Nav2 navigation + goto | ❌ (subprocess + data) |
| T67 | Autonomy | Vision-language navigation | ❌ (data only) |
| T68 | Autonomy | Semantic segmentation map | ✅ (labelling patch) |
| T69 | Autonomy | Export project ZIP | ❌ (data only) |
| T70 | Autonomy | Connect user robot model | ✅ (import patch) |
| T71 | Autonomy | Full 8-phase autonomy pipeline | ✅ (auto-approve) |
| T72 | Autonomy | Sensor health check | ❌ (data only) |
| T73 | MediaPipe Teleop | MediaPipe stay-in-frame teleop panel | ✅ (code gen patch) |
| T74 | Gemini Robotics | Scaffold Gemini Robotics ER bridge package | ❌ (data only) |
| T75 | cuRobo | Generate cuRobo world collision config | ❌ (data only) |
| T76 | cuRobo | Dynamic obstacle CRUD (add / update / remove) | ❌ (data only) |
| T77 | cuRobo | Sphere collision distance query | ❌ (data only) |
| T78 | cuRobo | Launch WorldCollisionManager node | ❌ (subprocess) |
| T79 | Isaac ROS | Object detection pipeline (RT-DETR / YOLOv8) | ❌ (subprocess) |
| T80 | Isaac ROS | Segmentation pipeline (UNet / Segformer / SAM / SAM2) | ❌ (subprocess) |
| T81 | cuMotion | cuMotion planner + MoveIt 2 integration | ❌ (subprocess) |
| T82 | Localization | Visual SLAM map build + load + localize | ❌ (subprocess) |
| T83 | Localization | Occupancy grid localizer + grid-search trigger | ❌ (subprocess) |
| T84 | LingBot-Map | Streaming 3D reconstruction → PointCloud2 + cuRobo mesh | ❌ (subprocess) |

---

## T73 — MediaPipe Stay-in-Frame Teleop Panel

**Goal**: Verify that `launch_mediapipe_teleop` produces a working Kit script that opens the teleop UI and lets the user drive a robot arm with hand gestures.

**Prerequisites**: Isaac Sim running. `platform_sdk` cloned at the path configured via `sdk_path`. Webcam connected.

**Steps**:
1. In the chat panel type: `"Launch MediaPipe teleop for /World/Nova_Carter/arm"`
2. The assistant should call `launch_mediapipe_teleop` and send a code patch.
3. Approve the patch (or auto-approve in test mode).
4. A floating window **"MediaPipe Teleop"** should appear in the Isaac Sim viewport.
5. Verify the 5×5 position grid is visible.
6. Click **Start** — the camera feed should start (webcam LED lights up).
7. Move your hand in/out of the grid center — the dot should turn green (center) → yellow → red (edge).
8. Click **Stop** — camera feed stops.
9. Click **E-Stop** — a stop patch should be sent, halting any in-flight motion.

**Pass criteria**:
- Window appears without Python traceback in the Isaac Sim console.
- Grid dot changes color as described.
- Start/Stop/E-Stop buttons respond without error.

---

## T74 — Gemini Robotics ER Bridge Scaffold

**Goal**: Verify `launch_gemini_robotics_bridge` scaffolds a complete, buildable colcon package.

**Prerequisites**: ROS2 Humble/Iron workspace at `~/ros2_ws`. `GOOGLE_API_KEY` set in `.env`.

**Steps**:
1. Chat: `"Set up a Gemini Robotics ER bridge for /camera/image_raw"`
2. Confirm the assistant calls `launch_gemini_robotics_bridge`.
3. The response should list the generated file paths.
4. In a terminal: `cd ~/ros2_ws && colcon build --packages-select gemini_robotics_bridge`
5. Source the workspace: `source install/setup.bash`
6. Launch: `ros2 launch gemini_robotics_bridge gemini_robotics.launch.py`
7. Call the detect service: `ros2 service call /gemini_robotics/detect_objects gemini_robotics_bridge/srv/GeminiQuery "{capability: 'detect_objects', prompt: 'red cup', image_topic: '/camera/image_raw'}"`

**Pass criteria**:
- `colcon build` completes with zero errors.
- Node starts and prints "Gemini Robotics Bridge node started".
- Service call returns `success: true` with `result_json` containing detected objects.

---

## T75 — cuRobo World Collision Config Generation

**Goal**: Verify `configure_curobo_world` produces valid YAML files that cuRobo can load.

**Prerequisites**: cuRobo installed (`pip install curobo`). Isaac Sim running.

**Steps**:
1. Chat: `"Configure cuRobo world with a table (0.6×0.6×0.05 m at z=0.4) and pre-allocate 20 OBB cache slots"`
2. Confirm the assistant calls `configure_curobo_world` with cuboid + cache params.
3. Note the output YAML paths (e.g., `workspace/scenes/untitled/curobo/`).
4. Inspect `world_config.yaml` — verify `cuboid.table.dims: [0.6, 0.6, 0.05]`.
5. Inspect `world_collision_config.yaml` — verify `cache.obb: 20`.
6. In a Python shell: `import yaml; d = yaml.safe_load(open("world_config.yaml")); print(d["cuboid"])`

**Pass criteria**:
- Both YAML files are created and parse cleanly.
- Cuboid entry matches the requested parameters.
- `world_collision_config.yaml` contains the specified cache sizes and activation distance.

---

## T76 — cuRobo Dynamic Obstacle CRUD

**Goal**: Verify obstacle add/update/remove/enable handlers keep `world_config.yaml` consistent.

**Prerequisites**: T75 completed (world_config.yaml exists).

**Steps**:
1. Chat: `"Add a box obstacle named shelf_left at position [1.2, -0.3, 0.6]"`
2. Verify `add_world_obstacle` is called; YAML updated with `shelf_left`.
3. Chat: `"Move shelf_left to [1.2, -0.3, 0.8]"`
4. Verify `update_obstacle_pose` is called; YAML shows new pose.
5. Chat: `"Disable shelf_left temporarily"`
6. Verify `enable_world_obstacle` sets `enable: false`.
7. Chat: `"Remove shelf_left from the world"`
8. Verify `remove_world_obstacle` removes the entry from YAML.

**Pass criteria**:
- Each operation returns `status: "added/updated/removed"`.
- YAML file reflects each change immediately (no server restart needed).
- After remove, the key is absent from `world_config.yaml`.

---

## T77 — cuRobo Sphere Collision Distance Query

**Goal**: Verify `query_sphere_collision` returns signed distances for robot link spheres.

**Prerequisites**: cuRobo installed. `world_config.yaml` exists with at least one cuboid.

**Steps**:
1. Chat: `"Query sphere collision for 3 arm link spheres: center [0.3,0,0.5] r=0.05, center [0.5,0,0.5] r=0.05, center [0.7,0,0.5] r=0.05"`
2. Confirm `query_sphere_collision` is called.
3. Review the returned distances: negative = free, positive = in collision.

**Pass criteria**:
- Returns a list of 3 signed distances, one per input sphere.
- Distances are floats (not errors).
- A sphere placed inside the table cuboid returns a positive distance.

---

## T78 — WorldCollisionManager ROS2 Node

**Goal**: Verify `launch_world_collision_manager` scaffolds and starts the manager node.

**Prerequisites**: ROS2 running. T75 world_config.yaml exists.

**Steps**:
1. Chat: `"Launch the world collision manager node"`
2. Confirm the assistant calls `launch_world_collision_manager`.
3. Verify node appears: `ros2 node list | grep world_collision_manager`
4. Check marker array: `ros2 topic echo /curobo/world_markers --once`
5. Call add service: `ros2 service call /curobo/add_obstacle std_srvs/srv/Trigger {}`

**Pass criteria**:
- Node appears in `ros2 node list`.
- `/curobo/world_markers` publishes `MarkerArray` messages.
- Service calls return `success: true`.

---

## T79 — Isaac ROS Object Detection Pipeline

**Goal**: Verify `launch_object_detection` starts RT-DETR or YOLOv8 and detections publish.

**Prerequisites**: Isaac ROS Humble installed. TensorRT engine file built or auto-build enabled. Camera publishing on `/image_rect`.

**Steps**:
1. Chat: `"Launch RT-DETR object detection on /camera/image_rect with 0.6 confidence"`
2. Confirm `launch_object_detection` is called with `model=rtdetr`.
3. Verify node starts: `ros2 node list | grep rtdetr`
4. Check detections: `ros2 topic echo /detections --once`
5. Chat: `"Switch to YOLOv8 for faster inference"`
6. Confirm a second call with `model=yolov8`.

**Pass criteria**:
- First detection on `/detections` arrives within 10 seconds of node start.
- Bounding boxes have valid `center.x/y` and `size_x/y` fields.
- YOLOv8 model starts without error after RT-DETR is already running.

---

## T80 — Isaac ROS Segmentation Pipeline

**Goal**: Verify UNet / Segformer / SAM / SAM2 segmentation launchers work end-to-end.

**Prerequisites**: Isaac ROS Humble + respective model packages installed.

**Steps**:
1. Chat: `"Launch UNet semantic segmentation on /image_rect"`
2. Verify segmentation mask publishes on `/unet/colored_segmentation_mask`.
3. Chat: `"Switch to Segment Anything 2 with objects: cup, bottle, robot_arm"`
4. Confirm `launch_segment_anything2` then `sam2_add_objects` are called.
5. Verify `/sam2/mask_array` publishes per-object masks.
6. Chat: `"Remove cup from SAM2 tracking"` → confirm `sam2_remove_object`.
7. Chat: `"Configure segmentation output for nvblox freespace estimation"`

**Pass criteria**:
- UNet mask topic publishes colored images.
- SAM2 tracks the three requested objects with distinct mask IDs.
- After remove, cup mask no longer appears.
- nvblox segmentation config YAML is written to disk.

---

## T81 — cuMotion Planner + MoveIt 2 Integration

**Goal**: Verify the full cuMotion stack (planner + robot segmenter + MoveIt 2) launches and accepts goals.

**Prerequisites**: Isaac ROS Humble. cuMotion package. Robot URDF/XRDF. MoveIt 2.

**Steps**:
1. Chat: `"Generate XRDF for the Franka Panda arm"`
2. Confirm `generate_xrdf` is called; `.xrdf` file created in workspace.
3. Chat: `"Launch cuMotion planner for Franka with world_config from T75"`
4. Confirm `launch_cumotion_planner` is called.
5. Chat: `"Launch robot segmenter to mask the arm from nvblox"`
6. Confirm `launch_robot_segmenter`.
7. Chat: `"Launch cuMotion MoveIt 2 bridge"` → confirm `launch_cumotion_moveit`.
8. In RViz2, use MotionPlanning panel to set a goal pose.
9. Click Plan & Execute — arm should move without collision.

**Pass criteria**:
- All three nodes appear in `ros2 node list`.
- MoveIt 2 plan succeeds (returns SUCCEEDED in RViz2 status).
- Robot does not collide with the table obstacle from T75.

---

## T82 — Visual SLAM Map Build, Load, and Localize

**Goal**: Verify Isaac ROS Visual SLAM map workflow: build → save → load → localize.

**Prerequisites**: Isaac ROS Visual SLAM (`isaac_ros_visual_slam`) installed. Stereo camera publishing.

**Steps**:
1. Chat: `"Start building a visual SLAM map"`
2. Confirm `build_visual_map` is called.
3. Drive the robot around the scene (teleop or Nav2) to build coverage.
4. Chat: `"Save the SLAM map to /tmp/office_map.db"`
5. Confirm map file created.
6. Restart Isaac Sim to a new scene.
7. Chat: `"Load the SLAM map from /tmp/office_map.db"`
8. Confirm `load_visual_slam_map` is called.
9. Chat: `"Localize the robot in the loaded map"`
10. Confirm `localize_in_visual_slam_map` is called.
11. Verify pose estimate converges: `ros2 topic echo /visual_slam/tracking/odometry --once`

**Pass criteria**:
- Map file exists and is non-empty after step 5.
- After localize, odometry topic publishes with `pose_covariance` decreasing over 5 seconds.
- `get_visual_slam_poses` returns at least one keyframe pose.

---

## T83 — Occupancy Grid Localizer + Grid-Search Trigger

**Goal**: Verify occupancy grid global localization initializes robot pose from a pre-built map.

**Prerequisites**: Isaac ROS Occupancy Grid Localizer. 2D LiDAR map (`.yaml` + `.pgm`) from Nav2 mapping.

**Steps**:
1. Chat: `"Launch occupancy grid localizer with map /maps/office.yaml"`
2. Confirm `launch_occupancy_grid_localizer` is called.
3. Verify node: `ros2 node list | grep occupancy_grid_localizer`
4. Chat: `"Trigger a grid search localization to find the robot's initial pose"`
5. Confirm `trigger_grid_search_localization` is called.
6. In RViz2, check that the particle cloud converges to the robot's actual position within 30 seconds.
7. Chat: `"Convert the 3D pointcloud to flatscan for localization"`
8. Confirm `launch_pointcloud_to_flatscan` is called.

**Pass criteria**:
- Localizer node starts and `/initialpose` topic is available.
- Grid search trigger returns `success: true` within 5 seconds.
- After convergence, estimated pose error < 0.3 m from ground truth.
- Flatscan topic (`/flatscan`) publishes after pointcloud converter starts.

---

## T84 — LingBot-Map Streaming 3D Reconstruction

**Goal**: Verify `launch_lingbot_map` scaffolds the ROS2 node and produces `/lingbot/pointcloud` from a live camera, then exports the accumulated reconstruction as a cuRobo world obstacle.

**Prerequisites**: LingBot-Map installed (`pip install -e .` from cloned repo). ROS2 workspace. Camera publishing on the configured topic.

**Steps**:
1. Chat: `"Launch LingBot-Map streaming reconstruction on /camera/image_raw with sky masking for outdoor scene"`
2. Confirm the assistant calls `launch_lingbot_map` with `mask_sky=true`.
3. Note the scaffolded package path in the response.
4. Build: `cd ~/ros2_ws && colcon build --packages-select lingbot_map_ros && source install/setup.bash`
5. Launch: `ros2 launch lingbot_map_ros lingbot_map.launch.py image_topic:=/camera/image_raw model_variant:=lingbot-map-long`
6. Verify topics appear:
   ```bash
   ros2 topic list | grep lingbot
   ```
   Expected: `/lingbot/pointcloud`, `/lingbot/camera_pose`, `/lingbot/depth`, `/lingbot/conf`
7. Drive the robot (or move the camera) for 30 seconds to build up the point cloud.
8. Visualize in RViz2: add PointCloud2 display → topic `/lingbot/pointcloud`, frame `map`.
9. Run the cuRobo export script to write a world obstacle:
   ```bash
   python scripts/export_lingbot_to_curobo.py \
     --world_config ~/ros2_ws/src/.../curobo/world_config.yaml \
     --mesh_out /tmp/lingbot_scene.ply \
     --frames 200
   ```
10. Verify `world_config.yaml` now contains a `mesh.lingbot_scene` entry.

**Pass criteria**:
- Node starts and model loads without error (check `ros2 topic hz /lingbot/pointcloud` ≥ 5 Hz).
- PointCloud2 in RViz2 shows the scene geometry (walls, floor, objects).
- After export, `world_config.yaml` has a valid `mesh` entry pointing to the `.ply` file.
- Re-launch cuMotion planner → it loads the new mesh as a collision obstacle.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Failed to communicate with service" | FastAPI not running → check Terminal 1 |
| "Mock echo: ..." responses | `aiohttp` not installed in Isaac Sim's Python → run `pip install aiohttp` inside Isaac Sim's Python |
| No code patches returned | LLM may not be calling tools → check `LLM_MODE` and API key in `.env` |
| Code patch fails on execution | Check Isaac Sim console (Window → Console) for Python errors |
| Chat panel not visible | Window → Isaac Assist (menu bar) |
| Extension not loaded | Verify `--ext-folder` path in launch script matches your extension directory |
| "No response from rosbridge" | rosbridge_server not running → start it: `ros2 launch rosbridge_server rosbridge_websocket_launch.xml` |
| ROS2 tools return "Connection refused" | Check `ROSBRIDGE_HOST`/`ROSBRIDGE_PORT` in `.env` match rosbridge config (default: 127.0.0.1:9090) |
| "ros-mcp not installed" warning in logs | Run `pip install ros-mcp` in the service Python environment |
| "Kit RPC failed" on Nucleus browse | Isaac Sim not running or Nucleus server not accessible — check `omniverse://localhost` in content browser |
| Download returns "copy failed" | Nucleus server may require authentication — check Omniverse Hub login |
| `launch_nav2` fails prereq check | Missing `/odom` or `/scan` or `/tf` topic — ensure drive graph + sensor OmniGraph nodes are running and sim is playing |
| `launch_slam` says "no sensor" | No LiDAR or camera topics detected — wire sensor OmniGraph first, or use `add_full_sensor_suite` |
| `nav2_goto` goal not reached | Nav2 costmap may be blocking path — check `/global_costmap/costmap` in RViz2; obstacles may need inflation radius tuning |
| SLAM map not building | Drive the robot around — SLAM needs sensor data from new positions. `ros2 topic hz /scan` to verify LiDAR publishing |
| `export_project_zip` empty launch files | Ensure phases 3–7 ran successfully (drive graph + sensors + Nav2 wired) before exporting |
