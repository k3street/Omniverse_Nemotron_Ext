"""
tool_schemas.py
---------------
Structured tool definitions for LLM function-calling.
Each tool maps to a Kit RPC endpoint or a code-generation pattern.
The LLM picks tools based on user intent, then the orchestrator
executes them via Kit RPC (port 8001).
"""
from __future__ import annotations

ISAAC_SIM_TOOLS = [
    # ─── USD Basics ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_prim",
            "description": "Create a new USD prim (Cube, Sphere, Cylinder, Cone, Mesh, Xform, Camera, etc.) at a given path with optional position and scale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path for the new prim, e.g. '/World/MyCube'"},
                    "prim_type": {"type": "string", "description": "Type: Cube, Sphere, Cylinder, Cone, Mesh, Xform, Camera, DistantLight, DomeLight"},
                    "position": {"type": "array", "items": {"type": "number"}, "description": "XYZ position in world space [x, y, z]"},
                    "scale": {"type": "array", "items": {"type": "number"}, "description": "XYZ scale [sx, sy, sz]"},
                    "rotation_euler": {"type": "array", "items": {"type": "number"}, "description": "Euler rotation in degrees [rx, ry, rz]"},
                },
                "required": ["prim_path", "prim_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_prim",
            "description": "Delete a prim and all its children from the USD stage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to delete"},
                },
                "required": ["prim_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_attribute",
            "description": "Set an attribute value on a USD prim (position, scale, color, any property).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string"},
                    "attr_name": {"type": "string", "description": "Attribute name, e.g. 'xformOp:translate', 'radius', 'visibility'"},
                    "value": {"description": "New value (number, array, string, bool)"},
                },
                "required": ["prim_path", "attr_name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_reference",
            "description": "Add a USD file reference to an existing prim (load external assets, robots, environments).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string"},
                    "reference_path": {"type": "string", "description": "Path or URL to the .usd/.usda/.usdz file"},
                },
                "required": ["prim_path", "reference_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_api_schema",
            "description": "Apply a USD API schema to a prim (RigidBodyAPI, CollisionAPI, MassAPI, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string"},
                    "schema_name": {"type": "string", "description": "e.g. 'PhysicsRigidBodyAPI', 'PhysicsCollisionAPI', 'PhysicsMassAPI'"},
                },
                "required": ["prim_path", "schema_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clone_prim",
            "description": "Duplicate a prim to a new path, or create a grid of copies. For count >= 4, uses GPU-batched isaacsim.core.cloner.GridCloner for fast parallel cloning with optional collision filtering (ideal for RL envs). For small counts, uses Sdf.CopySpec.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "target_path": {"type": "string"},
                    "position": {"type": "array", "items": {"type": "number"}, "description": "XYZ position for the cloned prim [x, y, z]"},
                    "count": {"type": "integer", "description": "Number of copies (for grid patterns). Use >= 4 for GPU-batched cloner."},
                    "spacing": {"type": "number", "description": "Distance between copies in meters"},
                    "collision_filter": {"type": "boolean", "description": "If true, filter collisions between clones (required for RL envs). Default false."},
                },
                "required": ["source_path", "target_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_usd_script",
            "description": "Execute arbitrary Python code inside the Kit process. Use for complex operations that don't fit other tools. Code has access to omni.usd, pxr, omni.kit.commands, and all Isaac Sim APIs. Requires user approval before execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute in Kit"},
                    "description": {"type": "string", "description": "Human-readable description of what the code does"},
                },
                "required": ["code", "description"],
            },
        },
    },

    # ─── Deformable / Soft Body ───────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_deformable_mesh",
            "description": "Convert an existing mesh prim into a deformable soft body (cloth, sponge, rubber, gel, rope). Applies PhysX deformable APIs with physics presets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "Path to the existing Mesh prim"},
                    "soft_body_type": {
                        "type": "string",
                        "enum": ["cloth", "sponge", "rubber", "gel", "rope"],
                        "description": "Type of soft body behavior",
                    },
                    "youngs_modulus": {"type": "number", "description": "Override stiffness (Pa). Higher = stiffer."},
                    "poissons_ratio": {"type": "number", "description": "Override compressibility (0-0.49). Higher = less compressible."},
                    "damping": {"type": "number", "description": "Override damping factor"},
                    "self_collision": {"type": "boolean", "description": "Enable self-collision detection"},
                },
                "required": ["prim_path", "soft_body_type"],
            },
        },
    },

    # ─── Robot anchoring ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "anchor_robot",
            "description": "Anchor a STATIONARY robot (e.g., Franka arm) to the world or a surface. Sets PhysxArticulationAPI.fixedBase=True and deletes the rootJoint. Do NOT use for wheeled/mobile robots (Nova Carter, Jetbot) — they need to remain mobile. For mobile robots, just delete rootJoint and add physics without fixedBase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot root prim (e.g., '/World/Franka')"},
                    "anchor_surface_path": {"type": "string", "description": "Optional USD path to the surface prim to anchor to (e.g., '/World/Table')"},
                    "base_link_name": {"type": "string", "description": "Name of the robot base link (default: 'panda_link0')"},
                    "position": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] world position for the anchor"},
                },
                "required": ["robot_path"],
            },
        },
    },

    # ─── OmniGraph ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_omnigraph",
            "description": "Create an OmniGraph action graph (e.g., ROS2 publishers, sensor pipelines, controller graphs). Builds nodes and connections programmatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "USD path for the graph prim"},
                    "graph_type": {"type": "string", "enum": ["action_graph", "push_graph", "lazy_graph"]},
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "description": "OmniGraph node type ID"},
                            },
                        },
                        "description": "Nodes to create",
                    },
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                            },
                        },
                        "description": "Connections between node ports",
                    },
                    "values": {
                        "type": "object",
                        "description": "Node attribute values to set {node.input: value}",
                    },
                },
                "required": ["graph_path"],
            },
        },
    },

    # ─── Sensors ──────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "add_sensor_to_prim",
            "description": "Attach a sensor (camera, lidar, IMU, contact sensor) to a prim. Optionally configure from a real product spec.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "Prim to attach the sensor to"},
                    "sensor_type": {"type": "string", "enum": ["camera", "rtx_lidar", "imu", "contact_sensor", "effort_sensor"]},
                    "product_name": {"type": "string", "description": "Real product name for spec lookup (e.g., 'RealSense D435i', 'Velodyne VLP-16')"},
                    "fov": {"type": "number", "description": "Field of view in degrees"},
                    "resolution": {"type": "array", "items": {"type": "integer"}, "description": "[width, height]"},
                    "range": {"type": "array", "items": {"type": "number"}, "description": "[min_range, max_range] in meters"},
                    "fps": {"type": "number"},
                },
                "required": ["prim_path", "sensor_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_product_spec",
            "description": "Look up real-world sensor/camera product specifications. Returns FOV, resolution, range, FPS, and other technical parameters from manufacturer datasheets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "Product name (e.g., 'Intel RealSense D435i', 'Velodyne VLP-16', 'ZED 2i')"},
                },
                "required": ["product_name"],
            },
        },
    },

    # ─── Materials ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_material",
            "description": "Create a new MDL material (OmniPBR, OmniGlass, OmniSurface) with specified appearance properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_path": {"type": "string", "description": "USD path for the material"},
                    "shader_type": {"type": "string", "enum": ["OmniPBR", "OmniGlass", "OmniSurface"]},
                    "diffuse_color": {"type": "array", "items": {"type": "number"}, "description": "RGB color [r, g, b] 0-1"},
                    "metallic": {"type": "number", "description": "Metallic factor 0-1"},
                    "roughness": {"type": "number", "description": "Roughness factor 0-1"},
                    "opacity": {"type": "number", "description": "Opacity 0-1 (for glass)"},
                    "ior": {"type": "number", "description": "Index of refraction (for glass)"},
                },
                "required": ["material_path", "shader_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_material",
            "description": "Bind an existing material to a prim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string"},
                    "material_path": {"type": "string"},
                },
                "required": ["prim_path", "material_path"],
            },
        },
    },

    # ─── Simulation Control ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "sim_control",
            "description": "Control the simulation timeline: play, pause, stop, step forward, or reset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play", "pause", "stop", "step", "reset"]},
                    "step_count": {"type": "integer", "description": "Number of simulation steps (for 'step' action)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_physics_params",
            "description": "Configure scene-level physics parameters (gravity, timestep, solver iterations).",
            "parameters": {
                "type": "object",
                "properties": {
                    "gravity_direction": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z]"},
                    "gravity_magnitude": {"type": "number"},
                    "time_step": {"type": "number", "description": "Physics timestep in seconds"},
                    "solver_iterations": {"type": "integer"},
                },
            },
        },
    },

    # ─── Transform / Navigation ───────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "teleport_prim",
            "description": "Move a prim to a specific world position and/or rotation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string"},
                    "position": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z]"},
                    "rotation_euler": {"type": "array", "items": {"type": "number"}, "description": "[rx, ry, rz] in degrees"},
                },
                "required": ["prim_path"],
            },
        },
    },

    # ─── Articulation / Joints ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "set_joint_targets",
            "description": "Set target position or velocity for articulation joints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string"},
                    "joint_name": {"type": "string"},
                    "target_position": {"type": "number", "description": "Target position in radians"},
                    "target_velocity": {"type": "number", "description": "Target velocity in rad/s"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_articulation_state",
            "description": "Read joint positions, velocities, and names for a robot articulation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "Path to the articulation root"},
                },
                "required": ["prim_path"],
            },
        },
    },

    # ─── Import ───────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "import_robot",
            "description": "Import a robot model from URDF, MJCF, or USD file. Supports loading by name (e.g., 'Franka', 'Spot', 'UR10', 'ANYmal_C') from the local asset library with 233+ robots available. Use format 'asset_library' when loading by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to URDF/MJCF/USD file, or asset name like 'Franka', 'Carter'"},
                    "format": {"type": "string", "enum": ["urdf", "mjcf", "usd", "asset_library"]},
                    "dest_path": {"type": "string", "description": "USD path to place the imported robot"},
                },
                "required": ["file_path"],
            },
        },
    },

    # ─── Console / Debug ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_console_errors",
            "description": "Retrieve recent error and warning messages from the Isaac Sim console log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "last_n": {"type": "integer", "description": "Number of recent entries to return", "default": 50},
                    "min_level": {"type": "string", "enum": ["verbose", "info", "warning", "error", "fatal"], "default": "warning"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_error",
            "description": "Analyze and explain an Isaac Sim error message, suggest fixes using Isaac Sim documentation knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The error message to diagnose"},
                },
                "required": ["error_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_debug_info",
            "description": "Get runtime performance metrics: FPS, frame time, GPU utilization, physics step time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },

    # ─── Viewport ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "capture_viewport",
            "description": "Capture a screenshot of the current viewport and return it as a base64 PNG. Use to show the user what the scene looks like.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_dim": {"type": "integer", "description": "Maximum image dimension in pixels", "default": 1280},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_viewport_camera",
            "description": "Switch the active viewport to a different camera prim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_path": {"type": "string", "description": "USD path to the camera prim"},
                },
                "required": ["camera_path"],
            },
        },
    },

    # ─── Scene Query ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_all_prims",
            "description": "List all prims in the scene, optionally filtered by type (Mesh, Camera, Light, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_type": {"type": "string", "description": "USD prim type to filter by (e.g., 'Mesh', 'Camera', 'DistantLight')"},
                    "under_path": {"type": "string", "description": "Only list prims under this path"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_distance",
            "description": "Measure the world-space distance between two prims.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_a": {"type": "string"},
                    "prim_b": {"type": "string"},
                },
                "required": ["prim_a", "prim_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scene_summary",
            "description": "Generate a high-level natural language summary of the current scene: prim counts by type, physics setup, lighting, robots present.",
            "parameters": {"type": "object", "properties": {}},
        },
    },

    # ─── Replicator / SDG ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "configure_sdg",
            "description": "Configure and run synthetic data generation using Omniverse Replicator. Sets up annotators, randomizers, and output writers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "annotators": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of annotators: 'rgb', 'bounding_box_2d', 'semantic_segmentation', 'instance_segmentation', 'distance_to_camera', 'normals'",
                    },
                    "num_frames": {"type": "integer"},
                    "output_dir": {"type": "string"},
                    "resolution": {"type": "array", "items": {"type": "integer"}, "description": "[width, height]"},
                },
                "required": ["num_frames", "output_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_sdg_pipeline",
            "description": "Create a full Omniverse Replicator synthetic data generation pipeline with camera, render product, annotators, and a dataset writer (COCO, KITTI, Basic, or NumPy). Generates omni.replicator.core Python code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "annotators": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["bounding_box_2d", "bounding_box_3d", "semantic_segmentation", "instance_segmentation", "depth", "normals", "occlusion"],
                        },
                        "description": "List of annotators to attach to the render product",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["coco", "kitti", "basic", "numpy"],
                        "description": "Output writer format: 'coco' (CocoWriter), 'kitti' (KittiWriter), 'basic' (BasicWriter), 'numpy' (BasicWriter with raw arrays)",
                    },
                    "num_frames": {"type": "integer", "description": "Number of frames to generate. Default: 100"},
                    "output_dir": {"type": "string", "description": "Directory for output data. Default: '/tmp/sdg_output'"},
                    "camera_position": {"type": "array", "items": {"type": "number"}, "description": "Camera XYZ position [x, y, z]. Default: [0, 0, 5]"},
                    "camera_look_at": {"type": "array", "items": {"type": "number"}, "description": "Camera look-at target [x, y, z]. Default: [0, 0, 0]"},
                    "resolution": {"type": "array", "items": {"type": "integer"}, "description": "Render resolution [width, height]. Default: [1280, 720]"},
                },
                "required": ["annotators", "output_format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_domain_randomizer",
            "description": "Add a domain randomization node to the Replicator graph. Randomizes pose, texture, color, lighting, material properties, or visibility for prims matching a path pattern. Generates omni.replicator.core Python code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Prim path pattern for target prims, e.g. '/World/Objects/.*'"},
                    "randomizer_type": {
                        "type": "string",
                        "enum": ["pose", "texture", "lighting", "color", "material_properties", "visibility"],
                        "description": "Type of domain randomization to apply",
                    },
                    "params": {
                        "type": "object",
                        "description": "Type-specific parameters. Pose: min_angle, max_angle, surface_prim. Color: color_min [r,g,b], color_max [r,g,b]. Lighting: intensity_min, intensity_max. Material: roughness_min, roughness_max, metallic_min, metallic_max. Visibility: probability.",
                    },
                },
                "required": ["target", "randomizer_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_sdg",
            "description": "Generate a small number of sample frames from the current Replicator pipeline to preview results without running the full dataset generation. Uses rep.orchestrator.step() to avoid blocking the Kit UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_samples": {"type": "integer", "description": "Number of preview frames to generate. Default: 3"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_dataset",
            "description": "Run full Replicator dataset generation using an async step-loop that yields to the Kit UI periodically to avoid freezing. Use after setting up a pipeline with create_sdg_pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_dir": {"type": "string", "description": "Directory for output data"},
                    "num_frames": {"type": "integer", "description": "Total number of frames to generate"},
                    "step_batch": {"type": "integer", "description": "Number of frames per batch before yielding to UI. Default: 10"},
                },
                "required": ["output_dir", "num_frames"],
            },
        },
    },

    # ─── ROS2 (live interaction via rosbridge / ros-mcp) ─────────────────────
    {
        "type": "function",
        "function": {
            "name": "ros2_connect",
            "description": "Configure the rosbridge WebSocket connection. Call this to point at a different rosbridge host/port or to verify connectivity. Default: 127.0.0.1:9090.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "Rosbridge server IP. Default: 127.0.0.1"},
                    "port": {"type": "integer", "description": "Rosbridge server port. Default: 9090"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_list_topics",
            "description": "List all active ROS2 topics with their message types. Connects to rosbridge to get live data — use this to verify OmniGraph ROS2 nodes are publishing after setup.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_get_topic_type",
            "description": "Get the message type for a specific ROS2 topic (e.g. /cmd_vel → geometry_msgs/msg/Twist).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name, e.g. '/cmd_vel'"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_get_message_type",
            "description": "Get the full field structure of a ROS2 message type (e.g. 'geometry_msgs/Twist' → linear.x, linear.y, ...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_type": {"type": "string", "description": "Message type, e.g. 'geometry_msgs/Twist' or 'sensor_msgs/JointState'"},
                },
                "required": ["message_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_subscribe_once",
            "description": "Subscribe to a ROS2 topic and return the first message received. Use this to verify a topic is publishing data (e.g. read /joint_states after wiring an OmniGraph).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name, e.g. '/joint_states'"},
                    "msg_type": {"type": "string", "description": "Message type, e.g. 'sensor_msgs/msg/JointState'"},
                    "timeout": {"type": "number", "description": "Seconds to wait for a message. Default: 5.0"},
                },
                "required": ["topic", "msg_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_publish",
            "description": "Publish a single message to a ROS2 topic. Use for one-shot commands like a single Twist or JointState.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name, e.g. '/cmd_vel'"},
                    "msg_type": {"type": "string", "description": "Message type, e.g. 'geometry_msgs/msg/Twist'"},
                    "data": {"type": "object", "description": "Message payload as JSON, e.g. {\"linear\": {\"x\": 0.5}}"},
                },
                "required": ["topic", "msg_type", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_publish_sequence",
            "description": "Publish a sequence of messages with durations — ideal for driving robots. With rate_hz>0, messages are published continuously at that rate for each duration (required for diff_drive controllers). Example: drive forward 2s then stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name, e.g. '/cmd_vel'"},
                    "msg_type": {"type": "string", "description": "Message type, e.g. 'geometry_msgs/msg/Twist'"},
                    "messages": {"type": "array", "items": {"type": "object"}, "description": "List of message payloads"},
                    "durations": {"type": "array", "items": {"type": "number"}, "description": "Duration in seconds for each message"},
                    "rate_hz": {"type": "number", "description": "Publishing rate in Hz. 0 = publish once per step. >0 = continuous at that rate. Default: 10"},
                },
                "required": ["topic", "msg_type", "messages", "durations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_list_services",
            "description": "List all available ROS2 services.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_call_service",
            "description": "Call a ROS2 service with specified request data. Use get_service_details first to find the correct type and request fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service name, e.g. '/reset_world'"},
                    "service_type": {"type": "string", "description": "Service type, e.g. 'std_srvs/srv/Empty'"},
                    "request": {"type": "object", "description": "Request payload. {} for parameterless services."},
                    "timeout": {"type": "number", "description": "Timeout in seconds. Default: 5.0"},
                },
                "required": ["service_name", "service_type", "request"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_list_nodes",
            "description": "List all currently running ROS2 nodes. Use to verify Isaac Sim's ROS2 bridge nodes are active.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ros2_get_node_details",
            "description": "Get detailed information about a ROS2 node including its publishers, subscribers, and services.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node name, e.g. '/isaac_sim'"},
                },
                "required": ["node"],
            },
        },
    },

    # ─── Knowledge Retrieval ──────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "lookup_knowledge",
            "description": "Search the version-specific knowledge base for API patterns, code examples, and documentation. Use this when you need to verify correct API usage for the current Isaac Sim version.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query — e.g. 'create OmniPBR material', 'apply rigid body physics', 'OmniGraph ROS2 clock'"},
                },
                "required": ["query"],
            },
        },
    },

    # ─── Motion Planning (RMPflow / Lula) ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "move_to_pose",
            "description": "Move a robot end-effector to a target pose using motion planning (RMPflow or Lula RRT). Plans a collision-free trajectory and executes it. Much easier than setting individual joint targets — just specify where you want the end-effector to go.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    "target_position": {"type": "array", "items": {"type": "number"}, "description": "Target XYZ position [x, y, z] in world space"},
                    "target_orientation": {"type": "array", "items": {"type": "number"}, "description": "Target orientation as quaternion [w, x, y, z]. Optional — omit to keep current orientation."},
                    "planner": {"type": "string", "enum": ["rmpflow", "lula_rrt"], "description": "Motion planner: 'rmpflow' (fast reactive) or 'lula_rrt' (global, obstacle-aware). Default: rmpflow"},
                    "robot_type": {"type": "string", "description": "Robot name for auto-loading config: 'franka', 'ur10', 'ur5e', 'cobotta', 'rs007n'. If omitted, tries to auto-detect."},
                },
                "required": ["articulation_path", "target_position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_trajectory",
            "description": "Plan a multi-waypoint trajectory for a robot arm without executing it. Returns the planned joint trajectory. Use move_to_pose for single-target moves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string"},
                    "waypoints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "position": {"type": "array", "items": {"type": "number"}},
                                "orientation": {"type": "array", "items": {"type": "number"}},
                            },
                            "required": ["position"],
                        },
                        "description": "List of target poses [{position: [x,y,z], orientation: [w,x,y,z]}, ...]",
                    },
                    "planner": {"type": "string", "enum": ["rmpflow", "lula_rrt"]},
                    "robot_type": {"type": "string"},
                },
                "required": ["articulation_path", "waypoints"],
            },
        },
    },

    # ─── Asset Catalog Search ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "catalog_search",
            "description": "Search for USD assets in the local Isaac Sim asset library and user directories by name, category, or description. Returns matching asset paths with metadata. Use this before importing objects to find the right asset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query — e.g. 'table', 'warehouse shelf', 'Franka robot', 'kitchen', 'forklift'"},
                    "asset_type": {"type": "string", "enum": ["robot", "prop", "environment", "sensor", "material", "any"], "description": "Filter by asset category. Default: 'any'"},
                    "limit": {"type": "integer", "description": "Max results to return. Default: 10"},
                },
                "required": ["query"],
            },
        },
    },

    # ─── Scene Builder ────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "generate_scene_blueprint",
            "description": "Generate a spatial layout plan (blueprint) from a natural language scene description. Uses the asset catalog to resolve assets and computes physically correct positions. Returns a structured blueprint for review before building.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language scene description — e.g. 'a kitchen with a table, 4 chairs, a Franka robot, and a fridge'"},
                    "room_dimensions": {"type": "array", "items": {"type": "number"}, "description": "Room size [length, width, height] in meters. Default: auto from description."},
                    "available_assets": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-resolved asset list from catalog_search. If omitted, auto-searches.",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_scene_from_blueprint",
            "description": "Execute a scene blueprint — creates all prims, places assets, applies physics. The blueprint should come from generate_scene_blueprint. Each object becomes a code patch for individual approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blueprint": {
                        "type": "object",
                        "description": "Scene blueprint from generate_scene_blueprint with objects, positions, and asset paths.",
                    },
                    "dry_run": {"type": "boolean", "description": "If true, generate code patches but don't execute. Default: false"},
                },
                "required": ["blueprint"],
            },
        },
    },

    # ─── IsaacLab RL Training ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_isaaclab_env",
            "description": "Scaffold an IsaacLab reinforcement learning environment from the current scene. Generates env config, observation, action, and reward definitions. The created env can be trained with launch_training.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "Name for the task — e.g. 'FrankaPickCube', 'QuadrupedLocomotion'"},
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation in the current scene"},
                    "task_type": {"type": "string", "enum": ["manipulation", "locomotion", "navigation", "custom"], "description": "Task category — determines default observation/action spaces"},
                    "num_envs": {"type": "integer", "description": "Number of parallel environments. Default: 64"},
                    "env_spacing": {"type": "number", "description": "Spacing between environments in meters. Default: 2.0"},
                    "reward_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Reward components — e.g. ['reach_target', 'grasp_success', 'action_penalty']",
                    },
                },
                "required": ["task_name", "robot_path", "task_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_training",
            "description": "Launch an IsaacLab reinforcement learning training run. Requires a task created by create_isaaclab_env or one of the built-in IsaacLab tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task name — custom (from create_isaaclab_env) or built-in (e.g. 'Isaac-Reach-Franka-v0', 'Isaac-Velocity-Anymal-C-v0')"},
                    "algo": {"type": "string", "enum": ["ppo", "sac", "td3", "rsl_rl"], "description": "RL algorithm. Default: ppo"},
                    "num_steps": {"type": "integer", "description": "Total training timesteps. Default: 1000000"},
                    "num_envs": {"type": "integer", "description": "Number of parallel envs. Default: 64"},
                    "checkpoint_dir": {"type": "string", "description": "Directory for saving checkpoints. Default: workspace/rl_checkpoints/<task>"},
                },
                "required": ["task"],
            },
        },
    },
    # ─── Vision (Gemini Robotics-ER) ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "vision_detect_objects",
            "description": "Use the Gemini Robotics-ER vision model to detect and locate objects in the current viewport image. Returns normalized 2D points and labels. Use this when the user asks 'what objects are in the scene', 'find the robot', 'locate the cube', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of specific object names to search for (e.g. ['robot', 'cube', 'table']). If omitted, detects all visible objects.",
                    },
                    "max_objects": {"type": "integer", "description": "Max objects to return. Default: 10"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_bounding_boxes",
            "description": "Use the Gemini Robotics-ER vision model to detect objects and return 2D bounding boxes from the viewport. Returns [ymin, xmin, ymax, xmax] coordinates normalized 0-1000.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_objects": {"type": "integer", "description": "Max objects to detect. Default: 25"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_plan_trajectory",
            "description": "Use the Gemini Robotics-ER vision model to plan a 2D trajectory from the current viewport image. Given a task instruction, returns a sequence of points the robot should follow. Use for visual pick-and-place planning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "Natural language task description — e.g. 'move the red pen to the organizer on the left'"},
                    "num_points": {"type": "integer", "description": "Number of trajectory waypoints. Default: 15"},
                },
                "required": ["instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_analyze_scene",
            "description": "Use the Gemini Robotics-ER vision model for free-form spatial reasoning about the viewport. Ask questions like 'what object should I move to make room?', 'how full is the container?', 'describe the workspace layout'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Natural language question about the scene"},
                },
                "required": ["question"],
            },
        },
    },

    # ─── Nucleus Browse & Download ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "nucleus_browse",
            "description": "Browse an Omniverse Nucleus server directory to discover available assets (robots, environments, props, materials). Use this to explore the Isaac Sim content library before downloading assets. Returns a list of files and folders at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Nucleus directory path to browse — e.g. '/NVIDIA/Assets/Isaac/5.1/Robots', '/NVIDIA/Assets/Isaac/5.1/Environments'. Default: '/NVIDIA/Assets/Isaac/5.1'"},
                    "server": {"type": "string", "description": "Nucleus server URL. Default: 'omniverse://localhost'"},
                    "limit": {"type": "integer", "description": "Max entries to return. Default: 50"},
                },
                "required": [],
            },
        },
    },
    # ─── IsaacLab-Arena Composable Environments ──────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_arena",
            "description": "Create a composable IsaacLab-Arena environment by combining a scene, robot embodiment, and task config. Registers the env with gymnasium and returns the env_id. Arena uses compile-time composition — environments are fixed after env.reset().",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_type": {
                        "type": "string",
                        "enum": ["tabletop_pick_and_place", "kitchen", "galileo", "custom"],
                        "description": "Pre-defined scene type or 'custom' for user-defined scenes",
                    },
                    "robot_asset": {"type": "string", "description": "Robot name or USD path (e.g. 'Franka', '/path/to/robot.usd')"},
                    "task": {"type": "string", "description": "Task description — e.g. 'pick_and_place', 'locomotion', 'navigation'"},
                    "num_envs": {"type": "integer", "description": "Number of parallel environments. Default: 64"},
                    "env_spacing": {"type": "number", "description": "Spacing between environments in meters. Default: 2.5"},
                },
                "required": ["scene_type", "robot_asset", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_asset",
            "description": "Download an asset from an Omniverse Nucleus server to the local Desktop/assets folder and register it in the asset catalog. Use after browsing with nucleus_browse to pull specific USD files locally. The downloaded asset can then be imported into scenes via import_robot or USD references.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nucleus_url": {"type": "string", "description": "Full Nucleus URL — e.g. 'omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/Franka/franka.usd'"},
                    "local_subdir": {"type": "string", "description": "Local subdirectory under ASSETS_ROOT_PATH. Auto-derived from Nucleus path if omitted."},
                    "category": {"type": "string", "enum": ["robot", "prop", "scene", "sensor", "material"], "description": "Asset category for catalog registration. Auto-detected from path if omitted."},
                },
                "required": ["nucleus_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_arena_variant",
            "description": "Create a variant of an existing Arena environment with a different robot embodiment. Each variant is a separate simulation registered under a new env_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_env_id": {"type": "string", "description": "The original env ID from create_arena (e.g. 'Arena-TabletopPickAndPlace-Franka-v0')"},
                    "robot_asset": {"type": "string", "description": "Different robot for comparison (e.g. 'UR10', '/path/to/other_robot.usd')"},
                },
                "required": ["base_env_id", "robot_asset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_arena_benchmark",
            "description": "Run a benchmark on an Arena environment. Launches as a subprocess (separate IsaacLab process), collects results including success rate, episode length, and custom metrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {"type": "string", "description": "Gymnasium env ID (from create_arena or create_arena_variant)"},
                    "num_episodes": {"type": "integer", "description": "Number of evaluation episodes. Default: 100"},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Metrics to collect — e.g. ['success_rate', 'episode_length', 'object_moved']",
                    },
                    "checkpoint": {"type": "string", "description": "Path to a trained policy checkpoint. If omitted, uses random actions."},
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "arena_leaderboard",
            "description": "Format a leaderboard table comparing Arena benchmark results across different robots and policies. Input is an array of result objects from run_arena_benchmark.",
            "parameters": {
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "env_id": {"type": "string"},
                                "robot": {"type": "string"},
                                "metrics": {"type": "object"},
                            },
                        },
                        "description": "List of benchmark result objects with env_id, robot, and metrics fields",
                    },
                },
                "required": ["results"],
            },
        },
    },

    # ─── Scene Export ─────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "export_scene_package",
            "description": "Export the current scene as a reusable file package. Collects all approved code patches from the session and generates: scene_setup.py (runnable script), README.md, ros2_topics.yaml (detected ROS2 topics), and ros2_launch.py (if ROS2 nodes present). Use when the user asks to 'export', 'save the scene files', 'generate a package', or 'create project files'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "Name of the scene/project (used for directory name and README title). Default: 'exported_scene'"},
                    "session_id": {"type": "string", "description": "Chat session ID to export patches from. Default: 'default_session'"},
                },
                "required": [],
            },
        },
    },

    # ─── Stage Analysis ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_stage_analysis",
            "description": "Run the Stage Analyzer to diagnose problems in the current scene. Checks for broken references, physics/material mismatches, articulation issues, sensor wiring, ROS2 bridge readiness, and performance warnings. Use when the user asks to 'diagnose the scene', 'check for errors', 'validate the stage', 'what's wrong with my scene', or 'run analysis'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "packs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of validator packs to run. Available: schema_consistency, import_health, material_physics, articulation_integrity, sensor_completeness, ros_bridge_readiness, performance_warnings, isaaclab_sanity. Default: all packs.",
                    },
                },
                "required": [],
            },
        },
    },

    # ─── cuRobo GPU Motion Planning ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "curobo_motion_plan",
            "description": "Plan a collision-free trajectory to a target pose using cuRobo GPU-accelerated motion planning. Returns interpolated joint waypoints. Use this for precise, collision-aware motion. Faster than RMPflow for single-target planning and supports obstacle avoidance from the USD stage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Franka'"},
                    "target_position": {"type": "array", "items": {"type": "number"}, "description": "Target EE position [x, y, z] in meters"},
                    "target_orientation": {"type": "array", "items": {"type": "number"}, "description": "Target EE orientation as quaternion [qw, qx, qy, qz]. Omit to keep current."},
                    "robot_config": {"type": "string", "enum": ["franka.yml", "ur5e.yml", "ur10e.yml", "kinova_gen3.yml", "iiwa.yml", "jaco7.yml", "tm12.yml"], "description": "cuRobo robot config file. Default: franka.yml"},
                    "interpolation_dt": {"type": "number", "description": "Time step between waypoints in seconds. Default: 0.02"},
                    "max_attempts": {"type": "integer", "description": "Max planning attempts. Default: 5"},
                    "world_obstacles": {
                        "type": "object",
                        "description": "Optional world obstacles as cuboids/meshes dict. If omitted, reads from USD stage via UsdHelper.",
                    },
                },
                "required": ["articulation_path", "target_position"],
            },
        },
    },
    # ─── Eureka: LLM Reward Generation ───────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "generate_reward",
            "description": "Generate a Eureka-style LLM reward function configuration for a DirectRLEnv. Reads the environment source code and produces the initial reward generation prompt with Eureka hyperparameters. Only works with DirectRLEnv (NOT ManagerBasedRLEnv).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "Natural language description of the RL task — e.g. 'make the robot arm reach a target position'"},
                    "env_source_path": {"type": "string", "description": "Path to the DirectRLEnv Python file — e.g. '/workspace/envs/reach_env.py'"},
                    "num_candidates": {"type": "integer", "description": "Number of reward candidates per iteration (K). Default: 4"},
                    "num_iterations": {"type": "integer", "description": "Number of Eureka evolution iterations. Default: 5"},
                },
                "required": ["task_description", "env_source_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_reward",
            "description": "Evaluate a candidate reward function by launching a short training run and collecting per-component metrics. Writes the reward code to a temp file, runs training as a subprocess, and returns fitness + component breakdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reward_code": {"type": "string", "description": "Python reward function code to evaluate"},
                    "env_id": {"type": "string", "description": "Gymnasium environment ID — e.g. 'Isaac-Reach-Franka-Direct-v0'"},
                    "num_steps": {"type": "integer", "description": "Training steps per candidate. Default: 1000"},
                },
                "required": ["reward_code", "env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "iterate_reward",
            "description": "Generate a mutation prompt for the next Eureka iteration. Combines the previous reward function, per-component training metrics, and optional user feedback into a structured prompt for the LLM to produce an improved reward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prev_reward_code": {"type": "string", "description": "Previous iteration's reward function code"},
                    "metrics": {
                        "type": "object",
                        "description": "Training metrics: { fitness: float, components: { name: { mean: [float], converged: bool } }, task_success_rate: float }",
                    },
                    "user_feedback": {"type": "string", "description": "Optional user feedback — e.g. 'it keeps dropping the handle'"},
                },
                "required": ["prev_reward_code", "metrics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "curobo_pick_place",
            "description": "Plan and execute a complete pick-and-place sequence using cuRobo: approach → grasp → lift → move → place → release. Handles gripper open/close and collision-free trajectory planning. Publishes joint commands via ROS2 /joint_command topic for execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot, e.g. '/World/Franka'"},
                    "pick_position": {"type": "array", "items": {"type": "number"}, "description": "Object grasp position [x, y, z] in meters"},
                    "pick_orientation": {"type": "array", "items": {"type": "number"}, "description": "Grasp orientation [qw, qx, qy, qz]. Default: top-down grasp [0, 1, 0, 0]"},
                    "place_position": {"type": "array", "items": {"type": "number"}, "description": "Place target position [x, y, z] in meters"},
                    "place_orientation": {"type": "array", "items": {"type": "number"}, "description": "Place orientation [qw, qx, qy, qz]. Default: same as pick."},
                    "approach_height": {"type": "number", "description": "Height above pick/place for approach/retreat in meters. Default: 0.1"},
                    "robot_config": {"type": "string", "description": "cuRobo config file. Default: franka.yml"},
                    "gripper_joint_names": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Gripper joint names. Default: ['panda_finger_joint1', 'panda_finger_joint2']",
                    },
                    "gripper_open": {"type": "array", "items": {"type": "number"}, "description": "Gripper open joint positions. Default: [0.04, 0.04]"},
                    "gripper_close": {"type": "array", "items": {"type": "number"}, "description": "Gripper close joint positions. Default: [0.0, 0.0]"},
                    "joint_command_topic": {"type": "string", "description": "ROS2 topic for joint commands. Default: /joint_command"},
                    "joint_state_topic": {"type": "string", "description": "ROS2 topic for reading current state. Default: /joint_states"},
                    "execution_rate_hz": {"type": "number", "description": "Rate to publish trajectory waypoints in Hz. Default: 50"},
                },
                "required": ["articulation_path", "pick_position", "place_position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "curobo_execute_trajectory",
            "description": "Execute a pre-planned joint trajectory by publishing waypoints to a ROS2 topic at a given rate. Use after curobo_motion_plan to send the computed trajectory to the robot. Can also be used to replay any sequence of joint positions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "joint_names": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Joint names matching the trajectory columns, e.g. ['panda_joint1', ..., 'panda_joint7']",
                    },
                    "waypoints": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                        "description": "List of joint position arrays — each inner array is one waypoint",
                    },
                    "joint_command_topic": {"type": "string", "description": "ROS2 topic. Default: /joint_command"},
                    "rate_hz": {"type": "number", "description": "Publishing rate. Default: 50"},
                    "msg_type": {"type": "string", "description": "Message type. Default: sensor_msgs/msg/JointState"},
                },
                "required": ["joint_names", "waypoints"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "curobo_vision_pick",
            "description": "Vision-guided pick-and-place using depth camera + cuRobo robot segmentation. Reads depth image from a ROS2 camera topic, uses RobotSegmenter to filter the robot from the depth image, builds a clean collision world from the remaining pointcloud, then plans and executes collision-free pick-and-place. Requires a depth camera publishing via ROS2.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot, e.g. '/World/Franka'"},
                    "camera_prim_path": {"type": "string", "description": "USD path to the depth camera prim, e.g. '/World/Camera'"},
                    "pick_position": {"type": "array", "items": {"type": "number"}, "description": "Object grasp position [x, y, z] in meters"},
                    "place_position": {"type": "array", "items": {"type": "number"}, "description": "Place target position [x, y, z] in meters"},
                    "pick_orientation": {"type": "array", "items": {"type": "number"}, "description": "Grasp orientation [qw, qx, qy, qz]. Default: top-down [0, 1, 0, 0]"},
                    "place_orientation": {"type": "array", "items": {"type": "number"}, "description": "Place orientation. Default: same as pick."},
                    "depth_topic": {"type": "string", "description": "ROS2 depth image topic. Default: /camera/depth"},
                    "depth_image_size": {"type": "array", "items": {"type": "integer"}, "description": "Depth image [width, height]. Default: [640, 480]"},
                    "robot_config": {"type": "string", "description": "cuRobo config. Default: franka.yml"},
                    "approach_height": {"type": "number", "description": "Height above pick/place for approach in meters. Default: 0.1"},
                    "gripper_joint_names": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Gripper joint names. Default: ['panda_finger_joint1', 'panda_finger_joint2']",
                    },
                    "gripper_open": {"type": "array", "items": {"type": "number"}, "description": "Open positions. Default: [0.04, 0.04]"},
                    "gripper_close": {"type": "array", "items": {"type": "number"}, "description": "Close positions. Default: [0.0, 0.0]"},
                    "segmentation_buffer": {"type": "number", "description": "Extra radius margin for robot sphere filtering in meters. Default: 0.02"},
                    "voxel_size": {"type": "number", "description": "Voxel size for collision world in meters. Default: 0.02"},
                    "execution_rate_hz": {"type": "number", "description": "Rate to play waypoints. Default: 50"},
                },
                "required": ["articulation_path", "camera_prim_path", "pick_position", "place_position"],
            },
        },
    },
    # ── ROS2 Launch Tools ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "launch_rviz2",
            "description": (
                "Launch RViz2 with an auto-generated display configuration based on currently "
                "active ROS2 topics. Discovers camera, LiDAR, odometry, TF, map, and other "
                "topics and creates matching RViz2 display panels. Returns the PID and config path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "extra_topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional topic names to include beyond auto-discovered ones",
                    },
                    "fixed_frame": {
                        "type": "string",
                        "description": "TF fixed frame for RViz2. Default: 'odom' (falls back to 'base_link')",
                    },
                },
                "required": [],
            },
        },
    },
    # ─── XR Teleoperation ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "start_teleop_session",
            "description": "Start an XR teleoperation session for a robot. Configures a WebSocket bridge for control data, sets up viewport streaming, creates a physics callback for receiving and applying joint commands with watchdog safety, and returns the connection URL for the XR client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Franka'"},
                    "input_device": {
                        "type": "string",
                        "enum": ["quest_3", "vision_pro", "spacemouse", "keyboard"],
                        "description": "XR input device type. Default: 'keyboard'",
                    },
                    "stream_quality": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Viewport streaming quality preset. Default: 'medium'",
                    },
                },
                "required": ["robot_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_rviz2",
            "description": "Stop a previously launched RViz2 instance.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_teleop_mapping",
            "description": "Configure the mapping between XR input device axes and robot joints for teleoperation. Sets up axis-to-joint mapping with position and velocity gains.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "device_axes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Axis names from the input device, e.g. ['left_x', 'left_y', 'right_x', 'right_y', 'trigger_left', 'trigger_right']",
                    },
                    "joint_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target joint names on the robot, e.g. ['panda_joint1', 'panda_joint2', ...]",
                    },
                    "gains": {
                        "type": "object",
                        "properties": {
                            "position": {"type": "number", "description": "Position gain multiplier"},
                            "velocity": {"type": "number", "description": "Velocity gain multiplier"},
                        },
                        "description": "Control gains for the mapping",
                    },
                },
                "required": ["robot_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_teleop_demo",
            "description": "Record a teleoperation demonstration to an HDF5 file with robomimic-compatible schema. Captures joint positions, velocities, and end-effector poses per timestep using a physics callback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_path": {"type": "string", "description": "File path for the output HDF5 recording, e.g. '/tmp/demo_001.hdf5'"},
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "frequency_hz": {"type": "integer", "description": "Recording frequency in Hz. Default: 30"},
                },
                "required": ["output_path", "robot_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_teleop_session",
            "description": "Stop the active XR teleoperation session. Removes all teleop physics callbacks, zeros joint velocities for safety, stops viewport streaming, closes WebSocket connections, and finalizes any active HDF5 recording.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "teleop_safety_config",
            "description": "Configure safety parameters for the active teleoperation session: watchdog timeout, maximum joint velocity cap, and workspace position limits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "watchdog_timeout_ms": {"type": "integer", "description": "Watchdog timeout in milliseconds. Robot holds last command until timeout, then zeros velocity. Default: 500"},
                    "max_joint_velocity": {"type": "number", "description": "Maximum joint velocity cap in rad/s"},
                    "workspace_limits": {
                        "type": "object",
                        "properties": {
                            "min": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] minimum workspace corner"},
                            "max": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] maximum workspace corner"},
                        },
                        "description": "Axis-aligned bounding box for workspace limits",
                    },
                },
                "required": ["robot_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "eureka_status",
            "description": "Get the current status of a running Eureka reward optimization run. Returns iteration progress, best fitness so far, and candidates evaluated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Eureka run identifier"},
                },
                "required": ["run_id"],
            },
        },
    },
]
