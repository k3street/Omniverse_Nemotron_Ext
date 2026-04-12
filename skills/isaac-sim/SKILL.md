---
name: isaac-sim
description: Control NVIDIA Isaac Sim robotics simulator — create/delete prims, configure physics, attach sensors, run simulations, manipulate OmniGraph, import robots. Requires Isaac Assist service running on the same network.
metadata: {"openclaw": {"requires": {"env": ["ISAAC_ASSIST_URL"]}, "primaryEnv": "ISAAC_ASSIST_URL", "emoji": "🤖", "homepage": "https://www.10things.tech"}}
---

# Isaac Sim Control Skill

You can control a running NVIDIA Isaac Sim instance through the Isaac Assist service.
All commands go through the HTTP API at `$ISAAC_ASSIST_URL` (default: `http://localhost:8000`).

## Available Capabilities

### Scene Manipulation (USD)
- **Create prims**: cubes, spheres, meshes, cameras, lights at specific paths and positions
- **Delete prims**: remove any prim and its children
- **Set attributes**: change position, scale, rotation, color, visibility, any USD attribute
- **Clone prims**: duplicate objects with optional grid layouts
- **Add references**: load external USD/URDF/MJCF files into the scene

### Physics & Deformable Bodies
- **Soft bodies**: convert meshes to cloth, sponge, rubber, gel, or rope with PhysX presets
- **Physics schemas**: apply RigidBodyAPI, CollisionAPI, MassAPI to prims
- **Physics params**: set gravity, timestep, solver iterations
- **Simulation control**: play, pause, stop, step, reset the timeline

### Sensors & Robots
- **Attach sensors**: camera, RTX lidar, IMU, contact sensor, effort sensor
- **Product specs**: look up real-world sensor parameters (RealSense, VLP-16, ZED, etc.)
- **Import robots**: load from URDF, MJCF, USD, or Isaac Sim asset library (Franka, Carter, etc.)
- **Joint control**: set target position/velocity for articulation joints

### OmniGraph & ROS2
- **Action graphs**: create OmniGraph nodes and connections for sensor pipelines
- **ROS2 topics**: list topics, publish messages

### Materials & Rendering
- **Create materials**: OmniPBR, OmniGlass, OmniSurface with physical properties
- **Assign materials**: bind materials to prims
- **Viewport**: capture screenshots, switch camera views

### Synthetic Data Generation
- **Replicator**: configure annotators, randomizers, and output writers for SDG

## How to Make Requests

Send natural language commands to the chat endpoint:

```bash
curl -X POST "$ISAAC_ASSIST_URL/api/v1/chat/message" \
  -H "Content-Type: application/json" \
  -d '{"message": "Create a red cube at position 1,0,0.5 and add rigid body physics", "session_id": "openclaw"}'
```

The service returns structured responses with:
- `reply`: natural language explanation
- `tool_calls`: list of tools invoked
- `code_patches`: Python code to execute in Kit (requires approval)

### Direct Tool Calls via MCP

If the Isaac Assist MCP server is running (port 8002), you can call tools directly:

```bash
curl -X POST "http://localhost:8002/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_prim", "arguments": {"prim_path": "/World/MyCube", "prim_type": "Cube", "position": [1, 0, 0.5]}}}'
```

## Common Workflows

### Set up a robot manipulation scene
1. Create a ground plane: `create_prim` with type `Mesh` (or use a reference)
2. Import robot: `import_robot` with `Franka` from asset library
3. Add a table: `create_prim` Cube, scale to table dimensions
4. Place objects: `create_prim` for each object, apply `RigidBodyAPI`
5. Attach camera: `add_sensor_to_prim` with product spec like `RealSense D435i`
6. Play simulation: `sim_control play`

### Convert mesh to deformable cloth
1. Select/create the mesh prim
2. Use `create_deformable_mesh` with `soft_body_type: cloth`
3. Optionally override physics params: Young's modulus, Poisson's ratio, damping

### Generate synthetic training data
1. Set up scene with objects and sensors
2. Configure `configure_sdg` with annotators: `rgb`, `bounding_box_2d`, `semantic_segmentation`
3. Set frame count and output directory
4. Run to generate labeled datasets

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ISAAC_ASSIST_URL` | `http://localhost:8000` | Isaac Assist FastAPI service URL |
| `ISAAC_ASSIST_MCP_URL` | `http://localhost:8002` | Isaac Assist MCP server URL |

## Error Handling

- If the service returns `code_patches`, those need user approval before execution in Isaac Sim
- The `run_usd_script` tool requires explicit approval — always explain what the code does
- Check `get_console_errors` if operations fail silently
- Use `scene_summary` to verify the scene state after complex operations
