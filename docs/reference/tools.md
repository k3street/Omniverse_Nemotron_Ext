# Tools Reference

Isaac Assist exposes **50+ tools** that the LLM can call in response to your natural language requests. Each tool maps to a specific Isaac Sim operation. You never call these directly — just describe what you want, and Isaac Assist picks the right tool.

---

## USD Basics

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `create_prim` | Create a USD prim (Cube, Sphere, Cylinder, Camera, Light, etc.) at a given path. | `prim_path`, `prim_type`, `position`, `scale`, `rotation_euler` | "Create a cube at 0, 0, 0.5" |
| `delete_prim` | Delete a prim and all its children from the stage. | `prim_path` | "Delete /World/OldBox" |
| `set_attribute` | Set any attribute on a USD prim (position, color, visibility, radius, etc.). | `prim_path`, `attr_name`, `value` | "Set the radius of /World/Ball to 0.3" |
| `add_reference` | Add a USD file reference to load external assets into the scene. | `prim_path`, `reference_path` | "Load the warehouse environment USD" |
| `apply_api_schema` | Apply a USD API schema (RigidBodyAPI, CollisionAPI, MassAPI, etc.) to a prim. | `prim_path`, `schema_name` | "Add rigid body physics to this cube" |
| `clone_prim` | Duplicate a prim. For count >= 4, uses GPU-batched `GridCloner` with optional collision filtering. | `source_path`, `target_path`, `count`, `spacing`, `collision_filter` | "Clone this box 64 times with 2m spacing" |
| `run_usd_script` | Execute arbitrary Python code inside the Kit process. Requires approval. | `code`, `description` | "Run a custom script to reorganize the stage hierarchy" |

## Deformable

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `create_deformable_mesh` | Convert a mesh prim into a soft body (cloth, sponge, rubber, gel, rope). | `prim_path`, `soft_body_type`, `youngs_modulus`, `damping` | "Make this mesh behave like cloth" |

## Robot

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `anchor_robot` | Anchor a stationary robot (e.g., Franka arm) to the world. Sets `fixedBase=True`. Do NOT use for mobile robots. | `robot_path`, `anchor_surface_path`, `position` | "Anchor the Franka to the table" |
| `import_robot` | Import a robot from URDF, MJCF, USD, or the built-in asset library (233+ robots). | `file_path`, `format`, `dest_path` | "Import a Franka Panda robot" |
| `set_joint_targets` | Set target position or velocity for articulation joints. | `articulation_path`, `joint_name`, `target_position`, `target_velocity` | "Set joint 3 to 90 degrees" |
| `get_articulation_state` | Read joint positions, velocities, and names from a robot articulation. | `prim_path` | "Show me the Franka's joint states" |

## OmniGraph

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `create_omnigraph` | Create an OmniGraph (action/push/lazy) with nodes, connections, and attribute values. | `graph_path`, `graph_type`, `nodes`, `connections`, `values` | "Create a ROS2 differential drive graph for Nova Carter" |

## Sensors

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `add_sensor_to_prim` | Attach a sensor (camera, lidar, IMU, contact sensor) to a prim, optionally using real product specs. | `prim_path`, `sensor_type`, `product_name`, `fov`, `resolution` | "Attach a RealSense D435i to the wrist link" |
| `lookup_product_spec` | Look up real-world sensor specs (FOV, resolution, range, FPS) from manufacturer datasheets. | `product_name` | "What are the specs of a Velodyne VLP-16?" |

## Materials

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `create_material` | Create a new MDL material (OmniPBR, OmniGlass, OmniSurface) with appearance properties. | `material_path`, `shader_type`, `diffuse_color`, `metallic`, `roughness`, `opacity` | "Create a shiny red metal material" |
| `assign_material` | Bind an existing material to a prim. | `prim_path`, `material_path` | "Apply the steel material to the table" |

## Simulation

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `sim_control` | Control the simulation timeline: play, pause, stop, step, or reset. | `action`, `step_count` | "Play the simulation" / "Step 10 frames" |
| `set_physics_params` | Configure scene-level physics: gravity, timestep, solver iterations. | `gravity_magnitude`, `time_step`, `solver_iterations` | "Set the physics timestep to 1/120" |

## Transform

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `teleport_prim` | Move a prim to a specific world position and/or rotation. | `prim_path`, `position`, `rotation_euler` | "Move the cube to position 2, 0, 1" |

## Viewport

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `capture_viewport` | Capture a screenshot of the current viewport as a base64 PNG. | `max_dim` | "Show me what the scene looks like" |
| `set_viewport_camera` | Switch the active viewport to a different camera prim. | `camera_path` | "Switch to the robot's wrist camera" |

## Scene Query

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `list_all_prims` | List all prims in the scene, optionally filtered by type. | `filter_type`, `under_path` | "Show me all cameras in the scene" |
| `measure_distance` | Measure the world-space distance between two prims. | `prim_a`, `prim_b` | "How far is the robot from the table?" |
| `scene_summary` | Generate a high-level natural language summary of the scene. | _(none)_ | "Describe the current scene" |

## Debugging

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `get_console_errors` | Retrieve recent error and warning messages from the Isaac Sim console. | `last_n`, `min_level` | "Are there any errors in the console?" |
| `get_physics_errors` | Retrieve PhysX-specific errors (collision, joint, solver issues). | `last_n` | "Check for physics errors" |
| `explain_error` | Analyze and explain an Isaac Sim error message using documentation knowledge. | `error_text` | "Explain this error: PhysX joint limit exceeded" |
| `fix_error` | Diagnose an error and generate a code patch to fix it. | `error_text` | "Fix the missing collision error" |
| `get_debug_info` | Get runtime performance metrics: FPS, frame time, GPU utilization, physics step time. | _(none)_ | "Show me the current FPS" |
| `check_collisions` | Validate collision setup on a prim — checks for CollisionAPI and collision geometry. | `prim_path` | "Why does the box pass through the floor?" |

## ROS2

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `ros2_connect` | Configure the rosbridge WebSocket connection (host/port). | `ip`, `port` | "Connect to rosbridge on 192.168.1.5" |
| `ros2_list_topics` | List all active ROS2 topics with message types. | _(none)_ | "List all ROS2 topics" |
| `ros2_get_topic_type` | Get the message type for a specific ROS2 topic. | `topic` | "What type is /cmd_vel?" |
| `ros2_get_message_type` | Get the full field structure of a ROS2 message type. | `message_type` | "Show the fields of geometry_msgs/Twist" |
| `ros2_subscribe_once` | Subscribe to a topic and return the first message received. | `topic`, `msg_type`, `timeout` | "Read one message from /joint_states" |
| `ros2_publish` | Publish a single message to a ROS2 topic. | `topic`, `msg_type`, `data` | "Publish a Twist to /cmd_vel with linear.x = 0.5" |
| `ros2_publish_sequence` | Publish a sequence of messages with durations — ideal for driving robots. | `topic`, `msg_type`, `messages`, `durations`, `rate_hz` | "Drive forward for 2 seconds then stop" |
| `ros2_list_services` | List all available ROS2 services. | _(none)_ | "Show all ROS2 services" |
| `ros2_call_service` | Call a ROS2 service with request data. | `service_name`, `service_type`, `request` | "Call the /reset_world service" |
| `ros2_list_nodes` | List all currently running ROS2 nodes. | _(none)_ | "Show active ROS2 nodes" |
| `ros2_get_node_details` | Get publishers, subscribers, and services for a ROS2 node. | `node` | "Show details for the /isaac_sim node" |

## Scene Building

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `catalog_search` | Search for USD assets in the local Isaac Sim library by name, category, or description. | `query`, `asset_type`, `limit` | "Find a kitchen table asset" |
| `generate_scene_blueprint` | Generate a spatial layout plan from a natural language scene description. | `description`, `room_dimensions` | "Design a warehouse with two robots and shelves" |
| `validate_scene_blueprint` | Validate a blueprint for overlaps, floating objects, scale issues, and missing assets. | `blueprint` | "Check the blueprint for problems" |
| `build_scene_from_blueprint` | Execute a scene blueprint — creates all prims, places assets, applies physics. | `blueprint`, `dry_run` | "Build the scene from the blueprint" |
| `list_scene_templates` | List pre-built scene templates (tabletop manipulation, warehouse, mobile navigation, etc.). | `category` | "Show me available scene templates" |
| `load_scene_template` | Load a pre-built scene template by name and return its blueprint. | `template_name` | "Load the tabletop manipulation template" |

## Batch

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `batch_apply_operation` | Apply an operation (physics, collision, material, deletion, visibility) to all children under a path. | `target_path`, `operation`, `parameters`, `filter_type` | "Add physics to all meshes under /World/Objects" |

## Motion Planning

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `move_to_pose` | Move a robot end-effector to a target pose using RMPflow or Lula RRT motion planning. | `articulation_path`, `target_position`, `target_orientation`, `planner`, `robot_type` | "Move the Franka end-effector to 0.5, 0, 0.3" |
| `plan_trajectory` | Plan a multi-waypoint trajectory without executing it. | `articulation_path`, `waypoints`, `planner`, `robot_type` | "Plan a path through these 5 waypoints" |

## Vision

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `vision_detect_objects` | Detect and locate objects in the viewport using Gemini Robotics-ER. | `labels`, `max_objects` | "What objects are in the scene?" |
| `vision_bounding_boxes` | Detect objects and return 2D bounding boxes from the viewport. | `max_objects` | "Show bounding boxes for all objects" |
| `vision_analyze_scene` | Free-form spatial reasoning about the viewport image. | `question` | "How full is the container?" |
| `vision_plan_trajectory` | Plan a 2D trajectory from the viewport image given a task instruction. | `instruction`, `num_points` | "Plan a path to pick up the red pen" |

## IsaacLab

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `create_isaaclab_env` | Scaffold an IsaacLab RL environment from the current scene. | `task_name`, `robot_path`, `task_type`, `num_envs`, `reward_terms` | "Create a pick-and-place RL environment" |
| `launch_training` | Launch an IsaacLab RL training run with a specified algorithm. | `task`, `algo`, `num_steps`, `num_envs` | "Train the Franka reach task with PPO" |

## Export

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `export_scene_package` | Export the scene as a reusable file package (setup script, README, ROS2 config). | `scene_name`, `session_id` | "Export the scene as a project package" |

## Knowledge

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `lookup_knowledge` | Search the version-specific knowledge base for API patterns and code examples. | `query` | "How do I create an OmniPBR material?" |

## Replicator

| Tool | Description | Key Parameters | Example Chat Phrase |
|------|-------------|----------------|---------------------|
| `configure_sdg` | Configure and run synthetic data generation using Omniverse Replicator with annotators and writers. | `annotators`, `num_frames`, `output_dir`, `resolution` | "Generate 100 frames of training data with bounding boxes" |
