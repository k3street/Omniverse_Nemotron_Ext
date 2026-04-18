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

    # ─── OmniGraph Assistant ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "explain_graph",
            "description": "Explain an existing OmniGraph action graph in plain language. Reads all nodes, connections, and attribute values, then returns a structured description of what the graph does (e.g., 'ticks every physics frame, reads joint states, publishes to ROS2'). Use when the user asks 'what does this graph do?' or 'explain the action graph'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "USD path to the OmniGraph prim, e.g. '/World/ActionGraph'"},
                },
                "required": ["graph_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_graph",
            "description": (
                "Create an OmniGraph from a natural language description using known ROS2/sensor templates. "
                "Covers 8 canonical patterns: ros2_clock, ros2_joint_state, ros2_camera, ros2_lidar, "
                "ros2_cmd_vel, ros2_tf, ros2_imu, ros2_odom. Automatically includes the required ROS2Context node. "
                "Use when the user says 'publish joint states to ROS2', 'set up a camera topic', "
                "'create a lidar publisher', 'subscribe to cmd_vel', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language description of the desired graph — e.g. 'publish Franka joint states to ROS2', 'subscribe to /cmd_vel for the Carter robot'"},
                    "template": {
                        "type": "string",
                        "enum": [
                            "ros2_clock", "ros2_joint_state", "ros2_camera",
                            "ros2_lidar", "ros2_cmd_vel", "ros2_tf",
                            "ros2_imu", "ros2_odom",
                        ],
                        "description": "Explicit template name. If omitted, auto-detected from description.",
                    },
                    "graph_path": {"type": "string", "description": "USD path for the new graph prim. Default: '/World/ActionGraph'"},
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Franka'. Required for joint_state, cmd_vel."},
                    "topic": {"type": "string", "description": "ROS2 topic name override, e.g. '/joint_states', '/camera/image_raw'"},
                    "camera_path": {"type": "string", "description": "USD path to the camera prim (for ros2_camera template)"},
                    "lidar_path": {"type": "string", "description": "USD path to the lidar prim (for ros2_lidar template)"},
                    "imu_path": {"type": "string", "description": "USD path to the IMU prim (for ros2_imu template)"},
                    "chassis_path": {"type": "string", "description": "USD path to the chassis prim (for ros2_odom template)"},
                    "root_prim": {"type": "string", "description": "USD path to the root prim for TF broadcasting (for ros2_tf template)"},
                    "fps": {"type": "number", "description": "Publishing rate in Hz. Default varies by template."},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debug_graph",
            "description": (
                "Debug an OmniGraph that isn't working correctly. Checks for common issues: "
                "missing ROS2Context node, disconnected inputs, type mismatches, missing OnTick trigger, "
                "duplicate node names. Returns a list of issues found with suggested fixes. "
                "Use when the user says 'my graph isn't working', 'ROS2 topics not appearing', "
                "'OmniGraph not evaluating'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "USD path to the OmniGraph prim, e.g. '/World/ActionGraph'"},
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

    # ─── Physics Material Database ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "lookup_material",
            "description": "Look up physics material properties (friction, restitution, density) for a material pair. Returns pair-specific measured values when available, otherwise computes average-combine values. Covers 16 common robotics materials with 20 pre-measured pairs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_a": {"type": "string", "description": "First material name (e.g. 'steel', 'rubber', 'aluminum', 'plastic_abs', 'concrete', 'glass', 'wood', 'cardboard')"},
                    "material_b": {"type": "string", "description": "Second material name for the contact pair"},
                },
                "required": ["material_a", "material_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_physics_material",
            "description": "Create a PhysicsMaterialAPI on a USD prim with correct friction and restitution values from the material database. Also applies CollisionAPI if not already present.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path of the prim to apply the material to"},
                    "material_name": {"type": "string", "description": "Material name from the database (e.g. 'steel', 'rubber', 'aluminum')"},
                },
                "required": ["prim_path", "material_name"],
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
    # ─── ZMQ Sensor Streaming ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "configure_zmq_stream",
            "description": "Configure a ZMQ PUB sensor stream from a camera or lidar prim using NVIDIA's C++ OgnIsaacBridgeZMQNode. Wires an OmniGraph that ticks on playback, captures sensor data via CameraHelper, and publishes via ZMQ on 127.0.0.1. No Python-level ZMQ sockets — all I/O through the C++ OmniGraph node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_prim": {"type": "string", "description": "USD path to the camera or lidar prim, e.g. '/World/Camera'"},
                    "pub_port": {"type": "integer", "description": "ZMQ PUB port (1024-65535). Default: 5555", "default": 5555},
                    "resolution": {"type": "array", "items": {"type": "integer"}, "description": "Downscale resolution [width, height]. Default: [640, 480]", "default": [640, 480]},
                    "fps": {"type": "integer", "description": "Target frame rate. Default: 30", "default": 30},
                    "compression": {"type": "string", "enum": ["none", "jpeg"], "description": "Compression mode. Default: jpeg", "default": "jpeg"},
                },
                "required": ["camera_prim"],
            },
        },
    },

    # ─── SDG Quality (Phase 7B Addendum) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "validate_annotations",
            "description": "Cross-check SDG annotations for common issues: bounding boxes outside image bounds, duplicate instance IDs, zero-area boxes, and missing declared classes. Returns a health report with per-issue details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_samples": {"type": "integer", "description": "Number of annotation samples to validate. Default: 10", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_randomization",
            "description": "Analyze domain randomization parameter distributions from an SDG run. Returns per-parameter statistics (min, max, mean, std) and flags misconfiguration like near-constant values or collapsed ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_samples": {"type": "integer", "description": "Number of DR samples to analyze. Default: 50", "default": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_domain_gap",
            "description": "Compare synthetic and real image datasets to diagnose domain gap issues. Returns a FID-like similarity score, per-class distribution differences, and suggested DR adjustments to reduce the gap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "synthetic_dir": {"type": "string", "description": "Path to synthetic image dataset directory"},
                    "real_dir": {"type": "string", "description": "Path to real image dataset directory"},
                    "model_checkpoint": {"type": "string", "description": "Optional path to a model checkpoint for feature extraction"},
                },
                "required": ["synthetic_dir", "real_dir"],
            },
        },
    },

    # ─── Performance Diagnostics ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_performance",
            "description": "Diagnose why the simulation is slow. Reads PhysX scene statistics, per-zone timing, and GPU/VRAM usage, then returns actionable issues ranked by severity. Use when user asks 'why is my sim slow?', 'low FPS', 'performance problems', or 'profiling'.",
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
            "name": "find_heavy_prims",
            "description": "Find all mesh prims with triangle count above a threshold. Returns sorted list with prim path, triangle count, and collision approximation type. Use to identify geometry that may be causing performance issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_triangles": {"type": "integer", "description": "Minimum triangle count to report. Default: 10000"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_collision",
            "description": "Switch a collision mesh to a simpler approximation to improve physics performance. Options: convexHull (single convex wrap, fastest), convexDecomposition (multiple convex pieces, good balance), boundingSphere/boundingCube (simplest, for non-contact objects), meshSimplification (reduced triangle mesh, keeps shape).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the mesh prim with collision"},
                    "approximation": {
                        "type": "string",
                        "enum": ["convexHull", "convexDecomposition", "boundingSphere", "boundingCube", "meshSimplification"],
                        "description": "Collision approximation type",
                    },
                },
                "required": ["prim_path", "approximation"],
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

    # ─── GR00T N1 Foundation Policy ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "load_groot_policy",
            "description": "Load the NVIDIA GR00T N1 foundation model for a robot articulation. Downloads the model from HuggingFace and configures the policy server. Returns download commands, launch config, and VRAM requirements. Requires >= 24 GB VRAM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "HuggingFace model ID. Default: 'nvidia/GR00T-N1.6-3B'", "default": "nvidia/GR00T-N1.6-3B"},
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Robot'"},
                    "embodiment": {
                        "type": "string",
                        "enum": ["LIBERO_PANDA", "OXE_WIDOWX", "UNITREE_G1", "custom"],
                        "description": "Embodiment preset for the robot. Determines observation/action space mapping.",
                    },
                },
                "required": ["robot_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_groot",
            "description": "Run closed-loop evaluation of a GR00T N1 policy on an IsaacLab task. Launches the evaluation subprocess, connects to the policy server, and collects success_rate and task_metrics over N episodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "HuggingFace model ID. Default: 'nvidia/GR00T-N1.6-3B'", "default": "nvidia/GR00T-N1.6-3B"},
                    "task": {"type": "string", "description": "Evaluation task name, e.g. 'Isaac-GR00T-Reach-v0'"},
                    "num_episodes": {"type": "integer", "description": "Number of evaluation episodes. Default: 50", "default": 50},
                    "checkpoint": {"type": "string", "description": "Optional path to a custom fine-tuned checkpoint"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finetune_groot",
            "description": "Fine-tune a GR00T N1 model on demonstration data in LeRobot v2 format. Supports LoRA (lower VRAM) and full fine-tuning. Launches training as a subprocess from Isaac-GR00T scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "HuggingFace model ID. Default: 'nvidia/GR00T-N1.6-3B'", "default": "nvidia/GR00T-N1.6-3B"},
                    "demo_data": {"type": "string", "description": "Path to demonstration data in LeRobot v2 format"},
                    "num_steps": {"type": "integer", "description": "Number of training steps. Default: 10000", "default": 10000},
                    "lora": {"type": "boolean", "description": "Use LoRA for lower VRAM requirements. Default: true", "default": True},
                    "output_dir": {"type": "string", "description": "Directory for saving checkpoints. Default: 'workspace/groot_checkpoints'", "default": "workspace/groot_checkpoints"},
                },
                "required": ["demo_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_policies",
            "description": "Compare evaluation results from multiple GR00T policies side by side. Returns a formatted comparison table showing zero-shot generalization, single-task performance, training data needed, and observation type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "policy_name": {"type": "string"},
                                "model_id": {"type": "string"},
                                "success_rate": {"type": "number"},
                                "task_metrics": {"type": "object"},
                                "training_data_size": {"type": "string"},
                                "observation_type": {"type": "string"},
                            },
                        },
                        "description": "List of evaluation results from different policies",
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

    # ─── IsaacAutomator Cloud Deployment ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "cloud_launch",
            "description": "Launch a cloud GPU instance via IsaacAutomator for training, SDG, evaluation, or headless simulation. Returns the deploy command, estimated cost, and prerequisites. Always requires user approval before execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["aws", "gcp", "azure"],
                        "description": "Cloud provider: aws, gcp, or azure",
                    },
                    "instance_type": {"type": "string", "description": "Instance type, e.g. 'g5.2xlarge', 'g2-standard-8', 'NCasT4_v3'"},
                    "isaac_version": {"type": "string", "description": "Isaac Sim version to deploy. Default: '5.1.0'"},
                    "script_template": {
                        "type": "string",
                        "enum": ["training", "sdg", "evaluation", "headless_sim"],
                        "description": "Job script template to use on the cloud instance",
                    },
                    "num_gpus": {"type": "integer", "description": "Number of GPUs to allocate. Default: 1"},
                },
                "required": ["provider", "instance_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cloud_status",
            "description": "Check the status of a running cloud job launched via cloud_launch. Returns GPU utilization, estimated time remaining, and cost so far.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID returned by cloud_launch"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cloud_download_results",
            "description": "Generate code to download results from a cloud instance (scp/rsync). Use after a cloud job completes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID of the completed cloud job"},
                    "output_dir": {"type": "string", "description": "Local directory to download results to. Default: 'workspace/cloud_results'"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cloud_teardown",
            "description": "Tear down a cloud instance launched via cloud_launch. Returns the teardown command. Always requires approval. Warns about cost if the instance has been running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID of the cloud instance to terminate"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cloud_estimate_cost",
            "description": "Estimate the cost of running a cloud GPU instance for a given duration. Uses a built-in pricing table for common GPU instance types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["aws", "gcp", "azure"],
                        "description": "Cloud provider",
                    },
                    "instance_type": {"type": "string", "description": "Instance type, e.g. 'g5.2xlarge'"},
                    "hours": {"type": "number", "description": "Estimated runtime in hours"},
                },
                "required": ["provider", "instance_type", "hours"],
            },
        },
    },

# ─── Environment Cloning ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "clone_envs",
            "description": "Clone a source environment prim into a grid of N parallel environments using isaacsim.core.cloner.GridCloner. Ideal for RL training with replicated physics and optional inter-env collision filtering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "USD path to the source environment prim, e.g. '/World/envs/env_0'"},
                    "num_envs": {"type": "integer", "description": "Number of environment clones to create"},
                    "spacing": {"type": "number", "description": "Distance between environments in meters. Default: 2.5"},
                    "collision_filter": {"type": "boolean", "description": "If true, filter inter-environment collisions. Default: true"},
                },
                "required": ["source_path", "num_envs"],
            },
        },
    },

    # ─── Debug Draw ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "debug_draw",
            "description": "Draw debug visualizations (points, lines, spline curves) in the viewport using isaacsim.util.debug_draw. Only supports points, lines, and lines_spline — no spheres, arrows, boxes, or text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draw_type": {
                        "type": "string",
                        "enum": ["points", "lines", "lines_spline"],
                        "description": "Type of drawing: 'points' for individual points, 'lines' for paired start/end line segments, 'lines_spline' for a smooth spline curve",
                    },
                    "points": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                        "description": "Coordinates: [[x,y,z], ...] for points/spline, or [[x1,y1,z1],[x2,y2,z2],...] pairs for lines",
                    },
                    "color": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "RGBA color [r, g, b, a] 0-1. Default: [1, 0, 0, 1] (red)",
                    },
                    "size": {"type": "number", "description": "Point size or line width. Default: 5"},
                    "lifetime": {"type": "number", "description": "Seconds before auto-clear. 0 = persistent. Default: 0"},
                },
                "required": ["draw_type", "points"],
            },
        },
    },

    # ─── Occupancy Map ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "generate_occupancy_map",
            "description": "Generate a 2D occupancy map of the scene using ray casting. Useful for navigation planning, obstacle detection, and workspace analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "XY origin of the map [x, y]. Default: [0, 0]",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Width and height of the map in meters [w, h]. Default: [10, 10]",
                    },
                    "resolution": {"type": "number", "description": "Cell size in meters. Default: 0.05"},
                    "height_range": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Min/max Z for ray casting [min_z, max_z]. Default: [0, 2]",
                    },
                },
            },
        },
    },

    # ─── Camera Inspection / Configuration ───────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "inspect_camera",
            "description": "Read and return the current properties of a USD camera prim: focal length, aperture, clipping range, focus distance, and projection type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_path": {"type": "string", "description": "USD path to the camera prim, e.g. '/World/Camera'"},
                },
                "required": ["camera_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_camera",
            "description": "Set camera properties on a USD camera prim: focal length, aperture, clipping range, focus distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_path": {"type": "string", "description": "USD path to the camera prim"},
                    "focal_length": {"type": "number", "description": "Focal length in mm"},
                    "horizontal_aperture": {"type": "number", "description": "Horizontal aperture in mm"},
                    "vertical_aperture": {"type": "number", "description": "Vertical aperture in mm"},
                    "clipping_range": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Near and far clipping planes [near, far] in scene units",
                    },
                    "focus_distance": {"type": "number", "description": "Focus distance in scene units"},
                },
                "required": ["camera_path"],
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

# ─── Motion Policy / IK / Robot Description ────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "set_motion_policy",
            "description": "Configure motion policy for a robot articulation: add/remove obstacles for collision avoidance, or adjust joint limit padding. Uses RMPflow under the hood.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    "policy_type": {
                        "type": "string",
                        "enum": ["add_obstacle", "remove_obstacle", "set_joint_limits"],
                        "description": "Policy action: add/remove obstacle or set joint limit padding",
                    },
                    "obstacle_name": {"type": "string", "description": "Name for the obstacle (used as identifier)"},
                    "obstacle_type": {"type": "string", "enum": ["cuboid", "sphere"], "description": "Obstacle shape type"},
                    "obstacle_dims": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Dimensions: [x, y, z] for cuboid or [radius] for sphere",
                    },
                    "obstacle_position": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Obstacle world position [x, y, z]",
                    },
                    "joint_limit_buffers": {"type": "number", "description": "Joint limit padding in radians (for set_joint_limits)"},
                    "robot_type": {"type": "string", "description": "Robot name for config: 'franka', 'ur10', 'ur5e', 'cobotta'. Default: 'franka'"},
                },
                "required": ["articulation_path", "policy_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_robot_description",
            "description": "Check if a robot has pre-built motion generation configs (URDF, XRDF, Lula descriptors). For supported robots, returns config file paths. For unsupported robots, explains how to create configs using the XRDF Editor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root"},
                    "robot_type": {"type": "string", "description": "Robot name to check, e.g. 'franka', 'ur10', 'my_custom_arm'"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solve_ik",
            "description": "Solve inverse kinematics for a robot arm — compute joint positions that place the end-effector at a target pose. Uses Lula kinematics solver. Applies the solution directly if successful.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    "target_position": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Target XYZ position [x, y, z] in world space",
                    },
                    "target_orientation": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Target orientation as quaternion [w, x, y, z]. Optional — omit to use default.",
                    },
                    "robot_type": {"type": "string", "description": "Robot name for config: 'franka', 'ur10', 'ur5e', 'cobotta'. Default: 'franka'"},
                },
                "required": ["articulation_path", "target_position"],
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

# ─── Cortex Behaviors & Manipulation ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_behavior",
            "description": "Create a Cortex behavior (decision framework) for a robot. Sets up a CortexWorld with a decider network for autonomous behavior like pick-and-place or target following. Generates a complete runnable script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Franka'"},
                    "behavior_type": {
                        "type": "string",
                        "enum": ["pick_and_place", "follow_target"],
                        "description": "Type of behavior to create",
                    },
                    "target_prim": {"type": "string", "description": "USD path to the target prim (object to pick or follow)"},
                    "params": {"type": "object", "description": "Additional behavior parameters (speed, thresholds, etc.)"},
                },
                "required": ["articulation_path", "behavior_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_gripper",
            "description": "Create and configure a gripper on a robot articulation. Supports parallel jaw (finger) grippers and suction grippers. Generates code to initialize the gripper with open/close positions and DOF names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "gripper_type": {
                        "type": "string",
                        "enum": ["parallel_jaw", "suction"],
                        "description": "Type of gripper",
                    },
                    "gripper_dof_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Joint names for gripper DOFs (required for parallel_jaw), e.g. ['panda_finger_joint1', 'panda_finger_joint2']",
                    },
                    "open_position": {"type": "number", "description": "Joint position for fully open gripper. Default: 0.04"},
                    "closed_position": {"type": "number", "description": "Joint position for fully closed gripper. Default: 0.0"},
                },
                "required": ["articulation_path", "gripper_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grasp_object",
            "description": "Generate a complete grasp sequence for a robot: approach, grasp, and lift an object. Supports top-down grasps, side grasps, and loading grasps from .isaac_grasp files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "target_prim": {"type": "string", "description": "USD path to the object to grasp"},
                    "grasp_type": {
                        "type": "string",
                        "enum": ["top_down", "side", "from_file"],
                        "description": "Grasp approach strategy. Default: top_down",
                    },
                    "grasp_file": {"type": "string", "description": "Path to .isaac_grasp YAML file (required when grasp_type='from_file')"},
                    "approach_distance": {"type": "number", "description": "Pre-grasp approach distance in meters. Default: 0.1"},
                    "lift_height": {"type": "number", "description": "Post-grasp lift height in meters. Default: 0.1"},
                },
                "required": ["robot_path", "target_prim"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "define_grasp_pose",
            "description": "Define and save a grasp pose specification for a robot-object pair. Creates a .isaac_grasp YAML file with the grasp transform, approach direction, and gripper parameters. Use with grasp_object(grasp_type='from_file').",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "object_path": {"type": "string", "description": "USD path to the target object"},
                    "gripper_offset": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Offset from object center to gripper [x, y, z]. Default: [0, 0, 0]",
                    },
                    "approach_direction": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Approach direction vector [x, y, z]. Default: [0, 0, -1] (top-down)",
                    },
                },
                "required": ["robot_path", "object_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visualize_behavior_tree",
            "description": "Visualize the structure of a Cortex behavior (decider) network as a formatted text tree. Shows the hierarchy of decision nodes, their types, and connections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_name": {"type": "string", "description": "Name of the behavior/decider network to visualize"},
                },
                "required": ["network_name"],
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

# ─── Scene Templates ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_scene_templates",
            "description": "List available pre-built scene templates for common robotics setups (tabletop manipulation, warehouse picking, mobile navigation, inspection cell, etc.). Returns template names with descriptions. Use when the user asks for a 'template', 'starter scene', or 'example setup'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional category filter: 'manipulation', 'mobile', 'inspection', 'warehouse'. Omit to list all."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_scene_template",
            "description": "Load a pre-built scene template by name. Returns a full blueprint dict (compatible with build_scene_from_blueprint) including objects, positions, sensors, and physics settings. Call build_scene_from_blueprint with the returned blueprint to create the scene.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {"type": "string", "description": "Template name from list_scene_templates, e.g. 'tabletop_manipulation', 'warehouse_picking', 'mobile_navigation', 'inspection_cell'"},
                },
                "required": ["template_name"],
            },
        },
    },

    # ─── Batch Operations ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "batch_apply_operation",
            "description": "Apply an operation to all child prims under a parent path. Supports applying physics, collisions, materials, visibility, deletion, or setting attributes in bulk. Optionally filter by prim type (Mesh, Xform, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {"type": "string", "description": "Parent prim path — all children under this path will be affected, e.g. '/World/Objects'"},
                    "operation": {
                        "type": "string",
                        "enum": ["apply_physics", "apply_collision", "set_material", "delete", "set_visibility", "set_attribute"],
                        "description": "Operation to apply to each matching child prim",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Operation-specific parameters. For set_material: {material_path: str}. For set_visibility: {visible: bool}. For set_attribute: {attr_name: str, value: any}. For apply_physics: {mass: number (optional)}.",
                    },
                    "filter_type": {"type": "string", "description": "USD prim type to filter by — e.g. 'Mesh', 'Xform', 'Cube'. Only matching prims are affected."},
                },
                "required": ["target_path", "operation"],
            },
        },
    },

    # ─── Scene Validation ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "validate_scene_blueprint",
            "description": "Validate a scene blueprint before building. Checks for AABB overlaps, floating objects, unrealistic scales, missing fields, and invalid asset paths. Use this before build_scene_from_blueprint to catch problems early.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blueprint": {
                        "type": "object",
                        "description": "Scene blueprint to validate — same format as build_scene_from_blueprint input.",
                    },
                },
                "required": ["blueprint"],
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

    # ─── Physics Debugging ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_physics_errors",
            "description": "Retrieve recent PhysX-specific errors and warnings from the console log. Filters for physics simulation errors (collision, joint, articulation, solver issues). Use when the user reports physics misbehavior or simulation crashes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "last_n": {"type": "integer", "description": "Number of recent PhysX entries to return. Default: 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_collisions",
            "description": "Validate collision setup on a prim: checks for CollisionAPI, counts mesh children, verifies collision geometry exists. Use to diagnose why objects pass through each other or physics interactions fail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the prim to check, e.g. '/World/MyCube'"},
                },
                "required": ["prim_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fix_error",
            "description": "Analyze a physics or USD error message, look up known fix patterns in the knowledge base, and generate a code patch to resolve it. Handles common issues: missing CollisionAPI, low solver iterations, broken joint paths, missing ground plane, OmniGraph wiring errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The error message text to diagnose and fix"},
                },
                "required": ["error_text"],
            },
        },
    },

# ─── Robot Setup Suite (Phase 8D) ────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "robot_wizard",
            "description": "Import a robot from URDF/USD, apply sensible drive defaults based on robot type (manipulator, mobile, humanoid), apply convex-hull collision meshes, and print a configuration summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_path": {"type": "string", "description": "Path to URDF or USD robot file"},
                    "robot_type": {
                        "type": "string",
                        "enum": ["manipulator", "mobile", "humanoid"],
                        "description": "Robot category — determines default drive stiffness/damping. Default: manipulator",
                    },
                    "drive_stiffness": {"type": "number", "description": "Override default Kp (position gain)"},
                    "drive_damping": {"type": "number", "description": "Override default Kd (damping gain)"},
                },
                "required": ["asset_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tune_gains",
            "description": "Tune joint drive gains for a robot articulation. 'manual' mode sets Kp/Kd directly via UsdPhysics.DriveAPI. 'step_response' mode runs an automated test using the GainTuner extension and reports RMSE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root"},
                    "method": {
                        "type": "string",
                        "enum": ["manual", "step_response"],
                        "description": "Tuning method. Default: manual",
                    },
                    "joint_name": {"type": "string", "description": "Specific joint name (omit for all joints)"},
                    "kp": {"type": "number", "description": "Position gain (for manual method)"},
                    "kd": {"type": "number", "description": "Damping gain (for manual method)"},
                    "test_mode": {
                        "type": "string",
                        "enum": ["sinusoidal", "step"],
                        "description": "Test signal type for step_response method. Default: step",
                    },
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assemble_robot",
            "description": "Assemble a robot by attaching a tool or gripper to a base robot using a fixed joint. Uses the RobotAssembler extension to compose base + attachment at specified mount frames.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_path": {"type": "string", "description": "USD path to the base robot"},
                    "attachment_path": {"type": "string", "description": "USD path to the attachment (gripper/tool)"},
                    "base_mount": {"type": "string", "description": "Mount frame name on the base robot (e.g. 'panda_hand')"},
                    "attach_mount": {"type": "string", "description": "Mount frame name on the attachment (e.g. 'tool_base')"},
                },
                "required": ["base_path", "attachment_path", "base_mount", "attach_mount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_self_collision",
            "description": "Configure self-collision behavior for a robot articulation. 'auto' keeps defaults (adjacent links skip collision). 'enable'/'disable' explicitly sets the enabledSelfCollisions flag. Optionally filter specific link pairs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root"},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "enable", "disable"],
                        "description": "Self-collision mode",
                    },
                    "filtered_pairs": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Pairs of link paths to filter from collision checks, e.g. [['/link1', '/link2']]",
                    },
                },
                "required": ["articulation_path", "mode"],
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

# ─── Wheeled Robots & Conveyor Systems ───────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_wheeled_robot",
            "description": "Create a wheeled robot controller (differential drive, Ackermann, or holonomic) for an existing robot articulation. Generates code that sets up the controller and a reusable drive function.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Carter'"},
                    "drive_type": {
                        "type": "string",
                        "enum": ["differential", "ackermann", "holonomic"],
                        "description": "Drive kinematics type",
                    },
                    "wheel_radius": {"type": "number", "description": "Wheel radius in meters"},
                    "wheel_base": {"type": "number", "description": "Distance between wheels (or axles) in meters"},
                    "wheel_dof_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Joint names for the wheel DOFs. If omitted, auto-detected.",
                    },
                    "max_linear_speed": {"type": "number", "description": "Maximum linear speed in m/s. Default: 1.0"},
                    "max_angular_speed": {"type": "number", "description": "Maximum angular speed in rad/s. Default: 3.14"},
                },
                "required": ["robot_path", "drive_type", "wheel_radius", "wheel_base"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_to",
            "description": "Navigate a wheeled robot to a target [x, y] position using direct drive or A* path planning. Generates code with a physics callback that drives the robot to the goal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "target_position": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Target [x, y] position in world space",
                    },
                    "planner": {
                        "type": "string",
                        "enum": ["astar", "direct"],
                        "description": "Planning strategy: 'direct' (straight line) or 'astar' (grid-based A*). Default: direct",
                    },
                },
                "required": ["robot_path", "target_position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_conveyor",
            "description": "Turn an existing mesh prim into a conveyor belt using OmniGraph with the OgnIsaacConveyor node. Includes a GPU/Fabric physics check (conveyors require CPU physics).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the conveyor mesh prim"},
                    "speed": {"type": "number", "description": "Belt speed in m/s. Default: 0.5"},
                    "direction": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Belt direction vector [x, y, z]. Default: [1, 0, 0]",
                    },
                },
                "required": ["prim_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_conveyor_track",
            "description": "Create a multi-segment conveyor track along a sequence of waypoints. Each segment is an oriented conveyor belt with correct rotation to connect consecutive waypoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "waypoints": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                        "description": "List of [x, y, z] waypoints defining the track path",
                    },
                    "belt_width": {"type": "number", "description": "Belt width in meters. Default: 0.5"},
                    "speed": {"type": "number", "description": "Belt speed in m/s. Default: 0.5"},
                },
                "required": ["waypoints"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "merge_meshes",
            "description": "Merge multiple mesh prims into a single optimized mesh using the MeshMerger utility. Useful for combining conveyor segments, static geometry, or reducing draw calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of USD paths to mesh prims to merge",
                    },
                    "output_path": {"type": "string", "description": "USD path for the merged output mesh"},
                },
                "required": ["prim_paths", "output_path"],
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

    # ─── ROS2 Deep Integration (Phase 8F) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "show_tf_tree",
            "description": "Visualize the ROS2 TF transform tree from a given root frame. Creates a ROS2PublishTransformTree OmniGraph node if missing, acquires the TF listener, and prints the tree hierarchy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root_frame": {"type": "string", "description": "Root TF frame to start from. Default: 'world'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_robot_description",
            "description": "Publish a simplified URDF robot description to a ROS2 topic with TRANSIENT_LOCAL durability. Reads the USD articulation structure and generates a URDF string with link names, joint types, and transforms. For full-fidelity URDF export, use Isaac Sim's URDF Exporter UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    "topic": {"type": "string", "description": "ROS2 topic to publish on. Default: '/robot_description'"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ros2_bridge",
            "description": "Configure an OmniGraph-based ROS2 bridge for multiple sensors. Creates a ROS2Context node and wires up publisher nodes for cameras, lidar, IMU, clock, and joint states. Handles Isaac Sim version namespace differences automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["camera", "lidar", "imu", "clock", "joint_state"], "description": "Sensor type"},
                                "prim_path": {"type": "string", "description": "USD path to the sensor or robot prim"},
                                "topic_name": {"type": "string", "description": "ROS2 topic name for this sensor"},
                                "frame_id": {"type": "string", "description": "TF frame ID for the sensor"},
                            },
                            "required": ["type"],
                        },
                        "description": "List of sensors to bridge",
                    },
                    "ros2_domain_id": {"type": "integer", "description": "ROS2 domain ID. Default: 0"},
                },
                "required": ["sensors"],
            },
        },
    },

# ─── ROS2 Deep Integration (Phase 8F) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "show_tf_tree",
            "description": "Visualize the ROS2 TF transform tree from a given root frame. Creates a ROS2PublishTransformTree OmniGraph node if missing, acquires the TF listener, and prints the tree hierarchy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root_frame": {"type": "string", "description": "Root TF frame to start from. Default: 'world'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_robot_description",
            "description": "Publish a simplified URDF robot description to a ROS2 topic with TRANSIENT_LOCAL durability. Reads the USD articulation structure and generates a URDF string with link names, joint types, and transforms. For full-fidelity URDF export, use Isaac Sim's URDF Exporter UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    "topic": {"type": "string", "description": "ROS2 topic to publish on. Default: '/robot_description'"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ros2_bridge",
            "description": "Configure an OmniGraph-based ROS2 bridge for multiple sensors. Creates a ROS2Context node and wires up publisher nodes for cameras, lidar, IMU, clock, and joint states. Handles Isaac Sim version namespace differences automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["camera", "lidar", "imu", "clock", "joint_state"], "description": "Sensor type"},
                                "prim_path": {"type": "string", "description": "USD path to the sensor or robot prim"},
                                "topic_name": {"type": "string", "description": "ROS2 topic name for this sensor"},
                                "frame_id": {"type": "string", "description": "TF frame ID for the sensor"},
                            },
                            "required": ["type"],
                        },
                        "description": "List of sensors to bridge",
                    },
                    "ros2_domain_id": {"type": "integer", "description": "ROS2 domain ID. Default: 0"},
                },
                "required": ["sensors"],
            },
        },
    },

# ─── Fine-Tune Flywheel (DATA) ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "record_feedback",
            "description": "Record user feedback (approve / reject / correct) for a previous chat turn. Links feedback to a recorded turn for fine-tuning quality filtering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Chat session ID"},
                    "turn_id": {"type": "integer", "description": "Turn number within the session"},
                    "approved": {"type": "boolean", "description": "True if the user approved the assistant's response"},
                    "edited": {"type": "boolean", "description": "True if the user edited the response before approving. Default: false"},
                    "correction": {"type": "string", "description": "User's correction text when rejecting or editing"},
                },
                "required": ["session_id", "turn_id", "approved"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_finetune_data",
            "description": "Export recorded chat turns to a provider-specific fine-tuning format. Filters by quality and converts to OpenAI, Anthropic, Ollama (Unsloth/ShareGPT), or Alpaca JSONL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["openai", "anthropic", "ollama", "alpaca"],
                        "description": "Target fine-tuning format",
                    },
                    "min_quality": {
                        "type": "string",
                        "enum": ["all", "approved", "approved_successful"],
                        "description": "Minimum quality filter. Default: approved_successful",
                    },
                    "output_path": {"type": "string", "description": "Optional explicit output file path"},
                },
                "required": ["format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finetune_stats",
            "description": "Return aggregate statistics about recorded fine-tuning data: total turns, approval rate, tool distribution, error rate, date range, and rejection-correction pair count.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "redact_finetune_data",
            "description": "Run the redaction pipeline on an existing JSONL data file. Strips API keys (sk-*, AIza*, Bearer *, ghp_*, etc.), external file paths, and user-identifiable information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string", "description": "Path to the input JSONL file to redact"},
                    "output_path": {"type": "string", "description": "Optional output path. Defaults to <input>_redacted.jsonl"},
                },
                "required": ["input_path"],
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

    # ─── Smart Debugging (Phase 2 Addendum) ──────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_physics_error",
            "description": "Pattern-match a PhysX/physics error message against the Top 20 known errors. Returns the affected prim path (if identifiable), error category, specific fix instructions, and severity. Deduplicates repeated errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The physics error message(s) to diagnose. Can include multiple lines."},
                },
                "required": ["error_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_config",
            "description": "Parse IsaacLab @configclass files to trace a parameter's resolution chain. Returns the final value and each source file + line where the parameter was defined or overridden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "param_name": {"type": "string", "description": "Dotted parameter name to trace, e.g. 'sim.dt' or 'robot.actuators.stiffness'"},
                    "env_source_path": {"type": "string", "description": "Path to the IsaacLab environment source file to start tracing from"},
                },
                "required": ["param_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_physics_health",
            "description": "Generate code that performs a comprehensive physics health check on the current scene. Detects missing CollisionAPI, invalid inertia tensors, extreme mass ratios, infinite joint limits, missing PhysicsScene, and metersPerUnit mismatches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "Optional: check a specific robot articulation instead of the whole scene"},
                },
            },
        },
    },

# ─── Smart Debugging (Phase 2 Addendum) ──────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_physics_error",
            "description": "Pattern-match a PhysX/physics error message against the Top 20 known errors. Returns the affected prim path (if identifiable), error category, specific fix instructions, and severity. Deduplicates repeated errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The physics error message(s) to diagnose. Can include multiple lines."},
                },
                "required": ["error_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_config",
            "description": "Parse IsaacLab @configclass files to trace a parameter's resolution chain. Returns the final value and each source file + line where the parameter was defined or overridden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "param_name": {"type": "string", "description": "Dotted parameter name to trace, e.g. 'sim.dt' or 'robot.actuators.stiffness'"},
                    "env_source_path": {"type": "string", "description": "Path to the IsaacLab environment source file to start tracing from"},
                },
                "required": ["param_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_physics_health",
            "description": "Generate code that performs a comprehensive physics health check on the current scene. Detects missing CollisionAPI, invalid inertia tensors, extreme mass ratios, infinite joint limits, missing PhysicsScene, and metersPerUnit mismatches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "Optional: check a specific robot articulation instead of the whole scene"},
                },
            },
        },
    },

# ─── Phase 2 Addendum: Smart Debugging ───────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_physics_error",
            "description": "Pattern-match a PhysX error string against known error patterns. Returns matched categories with prim paths, severity, and specific fix instructions. Deduplicates repeated errors from parallel envs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The PhysX error text to diagnose"},
                },
                "required": ["error_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_config",
            "description": "Trace the resolution chain for an IsaacLab config parameter. Shows where a parameter is defined, overridden, and its final value. Useful for debugging 'wrong value' issues in RL training configs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "param_name": {"type": "string", "description": "Dotted parameter name, e.g. 'sim.dt', 'scene.table.friction'"},
                    "env_source_path": {"type": "string", "description": "Path to the IsaacLab env config Python file"},
                },
                "required": ["param_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_physics_health",
            "description": "Run a pre-flight physics health check on the stage or a specific articulation. Checks for missing CollisionAPI, zero mass, infinite joint limits, invalid inertia, and metersPerUnit mismatches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "Optional USD path to scope the check to a specific articulation"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_robot_description",
            "description": "Get config file paths (RMPflow, robot descriptor, URDF) for a known robot type. For unknown robots, returns instructions to create configs manually using XRDF Editor and CollisionSphereEditor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root"},
                    "robot_type": {"type": "string", "description": "Robot type: 'franka', 'ur10', 'ur5e', 'cobotta'. If omitted, auto-detected from path."},
                },
                "required": ["articulation_path"],
            },
        },
    },

    # ─── Phase 3 Addendum: URDF Post-Processor ──────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "verify_import",
            "description": "Audit a URDF-imported articulation for common post-import issues: missing CollisionAPI on links, zero-mass links, infinite joint limits, missing ArticulationRootAPI, metersPerUnit mismatch, and extreme inertia ratios. Returns a JSON issues list with prim paths, severity, and fix commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the imported articulation root, e.g. '/World/Robot'"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_robot_fix_profile",
            "description": "Look up known import issues for a specific robot model and return a fix profile with pre-built fix commands. Supports: Franka, UR5, UR10, G1, Allegro. For unknown robots, suggests using verify_import instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the articulation root"},
                    "robot_name": {"type": "string", "description": "Robot name: 'franka', 'ur5', 'ur10', 'g1', 'allegro'. If omitted, auto-detected from path."},
                },
                "required": ["articulation_path"],
            },
        },
    },

# ─── SDG Quality (Phase 7B Addendum) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "validate_annotations",
            "description": "Cross-check SDG annotations for common issues: bounding boxes outside image bounds, duplicate instance IDs, zero-area boxes, and missing declared classes. Returns a health report with per-issue details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_samples": {"type": "integer", "description": "Number of annotation samples to validate. Default: 10", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_randomization",
            "description": "Analyze domain randomization parameter distributions from an SDG run. Returns per-parameter statistics (min, max, mean, std) and flags misconfiguration like near-constant values or collapsed ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_samples": {"type": "integer", "description": "Number of DR samples to analyze. Default: 50", "default": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_domain_gap",
            "description": "Compare synthetic and real image datasets to diagnose domain gap issues. Returns a FID-like similarity score, per-class distribution differences, and suggested DR adjustments to reduce the gap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "synthetic_dir": {"type": "string", "description": "Path to synthetic image dataset directory"},
                    "real_dir": {"type": "string", "description": "Path to real image dataset directory"},
                    "model_checkpoint": {"type": "string", "description": "Optional path to a model checkpoint for feature extraction"},
                },
                "required": ["synthetic_dir", "real_dir"],
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

    # ─── ROS2 Quality / Diagnostics (Phase 8F Addendum) ──────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_ros2",
            "description": "Run a comprehensive ROS2 integration health check on the current scene. Checks for: ROS2Context node presence, distro detection, QoS profile mismatches, use_sim_time configuration, clock publishing, domain ID consistency, and dangling OmniGraph connections. Returns a list of issues with suggested fixes.",
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
            "name": "fix_ros2_qos",
            "description": "Generate code to fix the QoS (Quality of Service) profile on a ROS2 publisher node for a specific topic. Uses a preset mapping: scan->BEST_EFFORT, robot_description->TRANSIENT_LOCAL, tf->RELIABLE, cmd_vel->RELIABLE, camera->BEST_EFFORT. Ensures publisher and subscriber QoS profiles are compatible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "ROS2 topic name to fix QoS for, e.g. '/scan', '/robot_description', '/tf', '/cmd_vel', '/camera/rgb'"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ros2_time",
            "description": "Generate OmniGraph code to configure ROS2 time synchronization. Sets up a ROS2PublishClock node and configures use_sim_time parameter. Supports sim_time (Isaac Sim clock published to /clock), real_time (wall clock, use_sim_time=false), and scaled (sim_time with a time scale factor).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["sim_time", "real_time", "scaled"],
                        "description": "Time synchronization mode",
                    },
                    "time_scale": {
                        "type": "number",
                        "description": "Time scale factor (only used when mode='scaled'). Default: 1.0",
                    },
                },
                "required": ["mode"],
            },
        },
    },

# ─── ROS2 Quality / Diagnostics (Phase 8F Addendum) ──────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_ros2",
            "description": "Run a comprehensive ROS2 integration health check on the current scene. Checks for: ROS2Context node presence, distro detection, QoS profile mismatches, use_sim_time configuration, clock publishing, domain ID consistency, and dangling OmniGraph connections. Returns a list of issues with suggested fixes.",
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
            "name": "fix_ros2_qos",
            "description": "Generate code to fix the QoS (Quality of Service) profile on a ROS2 publisher node for a specific topic. Uses a preset mapping: scan->BEST_EFFORT, robot_description->TRANSIENT_LOCAL, tf->RELIABLE, cmd_vel->RELIABLE, camera->BEST_EFFORT. Ensures publisher and subscriber QoS profiles are compatible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "ROS2 topic name to fix QoS for, e.g. '/scan', '/robot_description', '/tf', '/cmd_vel', '/camera/rgb'"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ros2_time",
            "description": "Generate OmniGraph code to configure ROS2 time synchronization. Sets up a ROS2PublishClock node and configures use_sim_time parameter. Supports sim_time (Isaac Sim clock published to /clock), real_time (wall clock, use_sim_time=false), and scaled (sim_time with a time scale factor).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["sim_time", "real_time", "scaled"],
                        "description": "Time synchronization mode",
                    },
                    "time_scale": {
                        "type": "number",
                        "description": "Time scale factor (only used when mode='scaled'). Default: 1.0",
                    },
                },
                "required": ["mode"],
            },
        },
    },

# ─── Workspace & Singularity (Phase 8B Addendum) ─────────────────────────
    {
        "type": "function",
        "function": {
            "name": "show_workspace",
            "description": "Visualize robot workspace as a color-coded point cloud. Green = high manipulability, red = near singularity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "resolution": {"type": "integer", "description": "Number of joint configuration samples (default 500000)"},
                    "color_mode": {"type": "string", "enum": ["reachability", "manipulability", "singularity_distance"], "description": "Color mode: reachability (binary), manipulability (gradient), singularity_distance (red near singularities)"},
                },
                "required": ["articulation_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_singularity",
            "description": "Check if a target pose is near a robot singularity. Returns condition number: <50 safe, 50-100 warning, >=100 danger.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "target_position": {"type": "array", "items": {"type": "number"}, "description": "Target XYZ position [x, y, z]"},
                    "target_orientation": {"type": "array", "items": {"type": "number"}, "description": "Target quaternion [w, x, y, z] (optional)"},
                },
                "required": ["articulation_path", "target_position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monitor_joint_effort",
            "description": "Monitor joint positions, velocities, and efforts over time. Flags joints exceeding 90% of effort limits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation"},
                    "duration_seconds": {"type": "number", "description": "Monitoring duration in seconds (default 5.0)"},
                },
                "required": ["articulation_path"],
            },
        },
    },

# ─── Performance Diagnostics ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_performance",
            "description": "Diagnose why the simulation is slow. Reads PhysX scene statistics, per-zone timing, and GPU/VRAM usage, then returns actionable issues ranked by severity. Use when user asks 'why is my sim slow?', 'low FPS', 'performance problems', or 'profiling'.",
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
            "name": "find_heavy_prims",
            "description": "Find all mesh prims with triangle count above a threshold. Returns sorted list with prim path, triangle count, and collision approximation type. Use to identify geometry that may be causing performance issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_triangles": {"type": "integer", "description": "Minimum triangle count to report. Default: 10000"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_collision",
            "description": "Switch a collision mesh to a simpler approximation to improve physics performance. Options: convexHull (single convex wrap, fastest), convexDecomposition (multiple convex pieces, good balance), boundingSphere/boundingCube (simplest, for non-contact objects), meshSimplification (reduced triangle mesh, keeps shape).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the mesh prim with collision"},
                    "approximation": {
                        "type": "string",
                        "enum": ["convexHull", "convexDecomposition", "boundingSphere", "boundingCube", "meshSimplification"],
                        "description": "Collision approximation type",
                    },
                },
                "required": ["prim_path", "approximation"],
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

# ─── Physics Material Database ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "lookup_material",
            "description": "Look up physics material properties (friction, restitution, density) for a material pair. Returns pair-specific measured values when available, otherwise computes average-combine values. Covers 16 common robotics materials with 20 pre-measured pairs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_a": {"type": "string", "description": "First material name (e.g. 'steel', 'rubber', 'aluminum', 'plastic_abs', 'concrete', 'glass', 'wood', 'cardboard')"},
                    "material_b": {"type": "string", "description": "Second material name for the contact pair"},
                },
                "required": ["material_a", "material_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_physics_material",
            "description": "Create a PhysicsMaterialAPI on a USD prim with correct friction and restitution values from the material database. Also applies CollisionAPI if not already present.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path of the prim to apply the material to"},
                    "material_name": {"type": "string", "description": "Material name from the database (e.g. 'steel', 'rubber', 'aluminum')"},
                },
                "required": ["prim_path", "material_name"],
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

    # ─── Scene Diff ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "scene_diff",
            "description": "Show what changed in the scene since last save, last snapshot, or between two explicit snapshots. Returns a structured list of added/removed/modified prims with attribute-level detail, plus an LLM-ready plain-language summary. Use when the user asks 'what changed', 'show diff', 'compare with last save', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "since": {
                        "type": "string",
                        "enum": ["last_save", "last_snapshot"],
                        "description": "Shortcut: compare current state against last save or last snapshot.",
                    },
                    "snapshot_a": {"type": "string", "description": "Snapshot ID for the 'before' state (explicit comparison)."},
                    "snapshot_b": {"type": "string", "description": "Snapshot ID for the 'after' state (explicit comparison)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_changes",
            "description": "Start or stop live change tracking on the USD stage. When started, accumulates structural and value changes via Tf.Notice. Query accumulated changes with scene_diff(since='last_snapshot'). Use when the user asks to 'watch changes', 'track changes', or 'monitor the scene'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "query"],
                        "description": "'start' begins tracking, 'stop' ends it, 'query' returns accumulated changes without stopping.",
                    },
                },
                "required": ["action"],
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

# ─── Scene Diff ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "scene_diff",
            "description": "Show what changed in the scene since last save, last snapshot, or between two explicit snapshots. Returns a structured list of added/removed/modified prims with attribute-level detail, plus an LLM-ready plain-language summary. Use when the user asks 'what changed', 'show diff', 'compare with last save', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "since": {
                        "type": "string",
                        "enum": ["last_save", "last_snapshot"],
                        "description": "Shortcut: compare current state against last save or last snapshot.",
                    },
                    "snapshot_a": {"type": "string", "description": "Snapshot ID for the 'before' state (explicit comparison)."},
                    "snapshot_b": {"type": "string", "description": "Snapshot ID for the 'after' state (explicit comparison)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_changes",
            "description": "Start or stop live change tracking on the USD stage. When started, accumulates structural and value changes via Tf.Notice. Query accumulated changes with scene_diff(since='last_snapshot'). Use when the user asks to 'watch changes', 'track changes', or 'monitor the scene'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "query"],
                        "description": "'start' begins tracking, 'stop' ends it, 'query' returns accumulated changes without stopping.",
                    },
                },
                "required": ["action"],
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

    # ─── Automatic Scene Simplification ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "optimize_scene",
            "description": "Analyze the scene for performance bottlenecks and generate optimization patches. Identifies heavy collision meshes, over-iterated articulations, CPU-bound physics, unnecessary CCD, and never-moving joints. Use when the user says 'optimize my scene', 'it runs too slow', 'improve FPS', or 'simplify physics'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["analyze", "conservative", "aggressive"],
                        "description": "Optimization mode: 'analyze' reports issues only (no changes), 'conservative' applies safe optimizations (collision approximation, solver tuning), 'aggressive' applies all optimizations including mesh simplification. Default: 'conservative'",
                    },
                    "target_fps": {
                        "type": "number",
                        "description": "Desired target FPS. Used to estimate whether optimizations are sufficient. Default: 60",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simplify_collision",
            "description": "Simplify the collision mesh of a single prim by setting the MeshCollisionAPI approximation type. Use for targeted collision optimization on specific objects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the prim to simplify"},
                    "approximation": {
                        "type": "string",
                        "enum": ["convexHull", "convexDecomposition", "meshSimplification", "boundingSphere", "sdf"],
                        "description": "Collision approximation type: 'convexHull' (fastest, static bg objects), 'convexDecomposition' (dynamic objects needing rough shape), 'meshSimplification' (shape matters), 'boundingSphere' (far-away/triggers), 'sdf' (deformable interaction)",
                    },
                },
                "required": ["prim_path", "approximation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_physics_settings",
            "description": "Suggest optimal physics settings (solver, iterations, GPU, CCD, timestep) for a given scene type. Returns recommended configuration without modifying the scene.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_type": {
                        "type": "string",
                        "enum": ["rl_training", "manipulation", "mobile_robot", "digital_twin"],
                        "description": "Scene type: 'rl_training' (1024 envs, fast), 'manipulation' (precision grasping), 'mobile_robot' (navigation), 'digital_twin' (visualization)",
                    },
                },
                "required": ["scene_type"],
            },
        },
    },

# ─── Automatic Scene Simplification ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "optimize_scene",
            "description": "Analyze the scene for performance bottlenecks and generate optimization patches. Identifies heavy collision meshes, over-iterated articulations, CPU-bound physics, unnecessary CCD, and never-moving joints. Use when the user says 'optimize my scene', 'it runs too slow', 'improve FPS', or 'simplify physics'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["analyze", "conservative", "aggressive"],
                        "description": "Optimization mode: 'analyze' reports issues only (no changes), 'conservative' applies safe optimizations (collision approximation, solver tuning), 'aggressive' applies all optimizations including mesh simplification. Default: 'conservative'",
                    },
                    "target_fps": {
                        "type": "number",
                        "description": "Desired target FPS. Used to estimate whether optimizations are sufficient. Default: 60",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simplify_collision",
            "description": "Simplify the collision mesh of a single prim by setting the MeshCollisionAPI approximation type. Use for targeted collision optimization on specific objects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to the prim to simplify"},
                    "approximation": {
                        "type": "string",
                        "enum": ["convexHull", "convexDecomposition", "meshSimplification", "boundingSphere", "sdf"],
                        "description": "Collision approximation type: 'convexHull' (fastest, static bg objects), 'convexDecomposition' (dynamic objects needing rough shape), 'meshSimplification' (shape matters), 'boundingSphere' (far-away/triggers), 'sdf' (deformable interaction)",
                    },
                },
                "required": ["prim_path", "approximation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_physics_settings",
            "description": "Suggest optimal physics settings (solver, iterations, GPU, CCD, timestep) for a given scene type. Returns recommended configuration without modifying the scene.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_type": {
                        "type": "string",
                        "enum": ["rl_training", "manipulation", "mobile_robot", "digital_twin"],
                        "description": "Scene type: 'rl_training' (1024 envs, fast), 'manipulation' (precision grasping), 'mobile_robot' (navigation), 'digital_twin' (visualization)",
                    },
                },
                "required": ["scene_type"],
            },
        },
    },

# ─── Onboarding & First-Time UX ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "scene_aware_starter_prompts",
            "description": "Generate contextual starter prompts based on the current scene state. Call this when the chat panel opens to give users scene-aware suggestions. Returns 3 example prompts tailored to what's in the scene (empty scene, robot + objects, mobile robot, no physics, etc.).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hardware_compatibility_check",
            "description": "Run a hardware and software compatibility check for Isaac Sim. Probes GPU info, VRAM, Isaac Sim version, Python version, and LLM connectivity. Returns a structured report with status icons (pass/warn/info).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slash_command_discovery",
            "description": "Return available slash commands filtered by current scene state. Shows only commands relevant to what's in the scene (e.g., hides /workspace if no robot is present). Call when user types '/' in chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_has_robot": {"type": "boolean", "description": "Whether the scene contains a robot articulation. If omitted, auto-detected via scene_summary."},
                    "scene_has_physics": {"type": "boolean", "description": "Whether the scene has physics enabled. If omitted, auto-detected."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "console_error_autodetect",
            "description": "Check for new console errors since the last chat message. Returns a count and summary of new errors (not warnings) to proactively offer diagnosis. Only fires for errors to avoid spam.",
            "parameters": {
                "type": "object",
                "properties": {
                    "since_timestamp": {"type": "number", "description": "Unix timestamp of the last chat message. Errors after this time are considered 'new'. Default: 0 (return all errors)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_action_suggestions",
            "description": "Get context-aware next-step suggestions after a tool execution. Returns 2-3 follow-up prompts the user might want based on what tool just ran and its result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "completed_tool": {"type": "string", "description": "Name of the tool that just executed (e.g., 'import_robot', 'create_prim')"},
                    "tool_args": {"type": "object", "description": "Arguments that were passed to the completed tool"},
                    "tool_result": {"type": "object", "description": "Result returned by the completed tool"},
                },
                "required": ["completed_tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_scene_template",
            "description": "Load a pre-built quick-start scene template that gets users to 'something working' fast. Templates include robot + environment + physics setup. Available: 'pick_and_place' (Franka + table + cubes), 'mobile_nav' (Jetbot + warehouse), 'sdg_basic' (camera + objects + Replicator), 'empty_robot' (just a Franka, ready for commands).",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "enum": ["pick_and_place", "mobile_nav", "sdg_basic", "empty_robot"],
                        "description": "Template to load",
                    },
                },
                "required": ["template_name"],
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

# ─── OmniGraph Assistant ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "explain_graph",
            "description": "Explain an existing OmniGraph action graph in plain language. Reads all nodes, connections, and attribute values, then returns a structured description of what the graph does (e.g., 'ticks every physics frame, reads joint states, publishes to ROS2'). Use when the user asks 'what does this graph do?' or 'explain the action graph'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "USD path to the OmniGraph prim, e.g. '/World/ActionGraph'"},
                },
                "required": ["graph_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_graph",
            "description": (
                "Create an OmniGraph from a natural language description using known ROS2/sensor templates. "
                "Covers 8 canonical patterns: ros2_clock, ros2_joint_state, ros2_camera, ros2_lidar, "
                "ros2_cmd_vel, ros2_tf, ros2_imu, ros2_odom. Automatically includes the required ROS2Context node. "
                "Use when the user says 'publish joint states to ROS2', 'set up a camera topic', "
                "'create a lidar publisher', 'subscribe to cmd_vel', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language description of the desired graph — e.g. 'publish Franka joint states to ROS2', 'subscribe to /cmd_vel for the Carter robot'"},
                    "template": {
                        "type": "string",
                        "enum": [
                            "ros2_clock", "ros2_joint_state", "ros2_camera",
                            "ros2_lidar", "ros2_cmd_vel", "ros2_tf",
                            "ros2_imu", "ros2_odom",
                        ],
                        "description": "Explicit template name. If omitted, auto-detected from description.",
                    },
                    "graph_path": {"type": "string", "description": "USD path for the new graph prim. Default: '/World/ActionGraph'"},
                    "robot_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Franka'. Required for joint_state, cmd_vel."},
                    "topic": {"type": "string", "description": "ROS2 topic name override, e.g. '/joint_states', '/camera/image_raw'"},
                    "camera_path": {"type": "string", "description": "USD path to the camera prim (for ros2_camera template)"},
                    "lidar_path": {"type": "string", "description": "USD path to the lidar prim (for ros2_lidar template)"},
                    "imu_path": {"type": "string", "description": "USD path to the IMU prim (for ros2_imu template)"},
                    "chassis_path": {"type": "string", "description": "USD path to the chassis prim (for ros2_odom template)"},
                    "root_prim": {"type": "string", "description": "USD path to the root prim for TF broadcasting (for ros2_tf template)"},
                    "fps": {"type": "number", "description": "Publishing rate in Hz. Default varies by template."},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debug_graph",
            "description": (
                "Debug an OmniGraph that isn't working correctly. Checks for common issues: "
                "missing ROS2Context node, disconnected inputs, type mismatches, missing OnTick trigger, "
                "duplicate node names. Returns a list of issues found with suggested fixes. "
                "Use when the user says 'my graph isn't working', 'ROS2 topics not appearing', "
                "'OmniGraph not evaluating'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "graph_path": {"type": "string", "description": "USD path to the OmniGraph prim, e.g. '/World/ActionGraph'"},
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

    # ─── RL Training Debugging & Quality (Phase 7A Addendum) ──────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_training",
            "description": "Diagnose an active or completed RL training run. Reads TensorBoard scalars and RSL-RL perf logs from the run directory, then runs checks for: action collapse (policy std near zero), entropy collapse (premature exploration loss), reward hacking (reward up but success flat), bimodal success (high per-env variance), NaN detection with PD stability check, and throughput. Returns per-check status + concrete suggestions. Use when training appears stuck, diverged, or behaves degenerately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Path to the training run directory containing TensorBoard event files and (optionally) RSL-RL perf logs"},
                    "physics_dt": {"type": "number", "description": "Physics time step used during training (used by the PD stability check). Default: 1/120"},
                },
                "required": ["run_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_reward",
            "description": "Static analysis of an RL reward function — runs BEFORE training to catch common pitfalls. Checks for: sparse reward (reward desert), dominant term (one term swamps others), reward hacking risk (alive bonus without fall termination), scale issue (max reward too small for value learning), and success-alignment (terms don't correlate with success criterion). Returns per-check verdict with specific fix suggestions. Use when reviewing a new reward function before launching training.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reward_code": {"type": "string", "description": "Python source code of the reward function or RewTerm definitions"},
                    "has_fall_termination": {"type": "boolean", "description": "Whether the env has a fall/early-termination condition. Default: false"},
                    "max_possible_reward": {"type": "number", "description": "Optional max reward magnitude per step (used for scale check). If omitted, inferred from code."},
                },
                "required": ["reward_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profile_training_throughput",
            "description": "Analyze RSL-RL performance logs (Perf/collection_time, Perf/learning_time, Perf/total_fps) to identify whether a training run is sim-bound or train-bound. Suggests concrete fixes: TiledCamera switch for vision policies (10x faster), reducing num_envs/collision-mesh complexity for sim-bound, smaller networks/batch/PPO epochs for train-bound. Use when training throughput is lower than expected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Path to the training run directory containing RSL-RL perf logs (TensorBoard event files)"},
                },
                "required": ["run_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_eval_harness",
            "description": "Generate a reproducible Python evaluation script for a trained RL policy. Runs num_episodes deterministic rollouts on the given task, records reward/success/length per episode, saves a JSON results file, and optionally records video via gym.wrappers.RecordVideo. Use after training completes to benchmark the learned policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "Gym task ID — e.g. 'Isaac-Reach-Franka-v0'"},
                    "num_episodes": {"type": "integer", "description": "Number of evaluation episodes. Default: 100"},
                    "output_dir": {"type": "string", "description": "Directory to write eval_results.json (and optional videos). Default: 'workspace/eval/<task>'"},
                    "checkpoint_path": {"type": "string", "description": "Optional path to a trained policy checkpoint to load before rollout"},
                    "record_video": {"type": "boolean", "description": "If true, wrap the env with gym.wrappers.RecordVideo. Default: false"},
                    "max_steps_per_episode": {"type": "integer", "description": "Hard cap on steps per episode. Default: 1000"},
                },
                "required": ["task_name"],
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

    # ─── Interactive Robot Teaching ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "start_teaching_mode",
            "description": "Start interactive robot teaching mode. Lets the user move the robot to positions interactively and record waypoints for imitation learning or motion planning. Mode 'drag_target' creates a draggable ghost target that the robot follows via RMPflow. Mode 'keyboard' uses WASD/QE keys for end-effector control. Mode 'spacemouse' uses a 3Dconnexion SpaceMouse. Mode 'gravity_comp' zeroes PD gains so the user can drag the arm with viewport physics force-grab (Shift+drag).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root, e.g. '/World/Franka'"},
                    "mode": {
                        "type": "string",
                        "enum": ["drag_target", "keyboard", "spacemouse", "gravity_comp"],
                        "description": "Teaching input mode. 'drag_target' (recommended): draggable ghost target + RMPflow tracking. 'keyboard': WASD/QE keys. 'spacemouse': 3Dconnexion device. 'gravity_comp': zero PD gains for backdrivable arm.",
                    },
                    "robot_type": {"type": "string", "description": "Robot name for auto-loading RMPflow config: 'franka', 'ur10', 'ur5e', 'cobotta'. Default: 'franka'"},
                },
                "required": ["articulation_path", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_waypoints",
            "description": "Record the current robot joint state as a waypoint, or save all recorded waypoints to a file. Use during teaching mode to capture positions. Supports HDF5 (robomimic schema for imitation learning), JSON (for motion planning replay), and USD TimeSamples (for in-sim replay).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "output_path": {"type": "string", "description": "File path to save waypoints. Extension determines format: .hdf5, .json, or .usd"},
                    "format": {
                        "type": "string",
                        "enum": ["hdf5", "json", "usd"],
                        "description": "Output format: 'hdf5' (robomimic schema), 'json' (motion planning), 'usd' (TimeSamples). Default: 'json'",
                    },
                },
                "required": ["articulation_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replay_trajectory",
            "description": "Play back a recorded trajectory (from record_waypoints) in the viewport. Reads waypoints from a JSON or HDF5 file and drives the robot through them at adjustable speed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "trajectory_path": {"type": "string", "description": "Path to the trajectory file (.json or .hdf5)"},
                    "speed": {"type": "number", "description": "Playback speed multiplier (0.1 to 4.0). Default: 1.0"},
                },
                "required": ["articulation_path", "trajectory_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interpolate_trajectory",
            "description": "Generate a smooth trajectory between sparse waypoints. Takes a list of joint-space waypoints and interpolates to produce a dense, smooth trajectory. Methods: 'linear' (joint-space linear), 'cubic' (cubic spline), 'rmpflow' (collision-aware via RMPflow).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "waypoints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "joint_positions": {"type": "array", "items": {"type": "number"}, "description": "Joint positions in radians"},
                            },
                            "required": ["joint_positions"],
                        },
                        "description": "List of joint-space waypoints to interpolate between",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["linear", "cubic", "rmpflow"],
                        "description": "Interpolation method. 'linear': joint-space linear. 'cubic': cubic spline. 'rmpflow': collision-aware. Default: 'linear'",
                    },
                    "num_steps": {"type": "integer", "description": "Number of interpolation steps between each pair of waypoints. Default: 50"},
                    "output_path": {"type": "string", "description": "Optional file path to save the interpolated trajectory (.json)"},
                    "robot_type": {"type": "string", "description": "Robot name for RMPflow config (only needed for method='rmpflow'). Default: 'franka'"},
                },
                "required": ["articulation_path", "waypoints", "method"],
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

# ─── Interactive Robot Teaching ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "start_teaching_mode",
            "description": "Start interactive robot teaching mode. Lets the user move the robot to positions interactively and record waypoints for imitation learning or motion planning. Mode 'drag_target' creates a draggable ghost target that the robot follows via RMPflow. Mode 'keyboard' uses WASD/QE keys for end-effector control. Mode 'spacemouse' uses a 3Dconnexion SpaceMouse. Mode 'gravity_comp' zeroes PD gains so the user can drag the arm with viewport physics force-grab (Shift+drag).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root, e.g. '/World/Franka'"},
                    "mode": {
                        "type": "string",
                        "enum": ["drag_target", "keyboard", "spacemouse", "gravity_comp"],
                        "description": "Teaching input mode. 'drag_target' (recommended): draggable ghost target + RMPflow tracking. 'keyboard': WASD/QE keys. 'spacemouse': 3Dconnexion device. 'gravity_comp': zero PD gains for backdrivable arm.",
                    },
                    "robot_type": {"type": "string", "description": "Robot name for auto-loading RMPflow config: 'franka', 'ur10', 'ur5e', 'cobotta'. Default: 'franka'"},
                },
                "required": ["articulation_path", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_waypoints",
            "description": "Record the current robot joint state as a waypoint, or save all recorded waypoints to a file. Use during teaching mode to capture positions. Supports HDF5 (robomimic schema for imitation learning), JSON (for motion planning replay), and USD TimeSamples (for in-sim replay).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "output_path": {"type": "string", "description": "File path to save waypoints. Extension determines format: .hdf5, .json, or .usd"},
                    "format": {
                        "type": "string",
                        "enum": ["hdf5", "json", "usd"],
                        "description": "Output format: 'hdf5' (robomimic schema), 'json' (motion planning), 'usd' (TimeSamples). Default: 'json'",
                    },
                },
                "required": ["articulation_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replay_trajectory",
            "description": "Play back a recorded trajectory (from record_waypoints) in the viewport. Reads waypoints from a JSON or HDF5 file and drives the robot through them at adjustable speed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "trajectory_path": {"type": "string", "description": "Path to the trajectory file (.json or .hdf5)"},
                    "speed": {"type": "number", "description": "Playback speed multiplier (0.1 to 4.0). Default: 1.0"},
                },
                "required": ["articulation_path", "trajectory_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interpolate_trajectory",
            "description": "Generate a smooth trajectory between sparse waypoints. Takes a list of joint-space waypoints and interpolates to produce a dense, smooth trajectory. Methods: 'linear' (joint-space linear), 'cubic' (cubic spline), 'rmpflow' (collision-aware via RMPflow).",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                    "waypoints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "joint_positions": {"type": "array", "items": {"type": "number"}, "description": "Joint positions in radians"},
                            },
                            "required": ["joint_positions"],
                        },
                        "description": "List of joint-space waypoints to interpolate between",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["linear", "cubic", "rmpflow"],
                        "description": "Interpolation method. 'linear': joint-space linear. 'cubic': cubic spline. 'rmpflow': collision-aware. Default: 'linear'",
                    },
                    "num_steps": {"type": "integer", "description": "Number of interpolation steps between each pair of waypoints. Default: 50"},
                    "output_path": {"type": "string", "description": "Optional file path to save the interpolated trajectory (.json)"},
                    "robot_type": {"type": "string", "description": "Robot name for RMPflow config (only needed for method='rmpflow'). Default: 'franka'"},
                },
                "required": ["articulation_path", "waypoints", "method"],
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

    # ─── Preflight Check (Phase 2 Addendum — 23 checks) ─────────────────────
    {
        "type": "function",
        "function": {
            "name": "preflight_check",
            "description": "Run a comprehensive preflight check on the current scene before simulation. Performs 23 checks across 4 tiers: Tier 1 (crash preventers), Tier 2 (correctness warnings), Tier 3 (RL training), Tier 4 (ROS2/OmniGraph). Returns issues with severity, affected prim, auto-fix suggestions, and tier classification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "tier1", "tier2", "tier3", "tier4"],
                        "description": "Which tier(s) to run. Default: 'all'",
                    },
                    "articulation_path": {
                        "type": "string",
                        "description": "Optional: limit checks to a specific robot articulation path",
                    },
                },
            },
        },
    },

# ─── Preflight Check (Phase 2 Addendum — 23 checks) ─────────────────────
    {
        "type": "function",
        "function": {
            "name": "preflight_check",
            "description": "Run a comprehensive preflight check on the current scene before simulation. Performs 23 checks across 4 tiers: Tier 1 (crash preventers), Tier 2 (correctness warnings), Tier 3 (RL training), Tier 4 (ROS2/OmniGraph). Returns issues with severity, affected prim, auto-fix suggestions, and tier classification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "tier1", "tier2", "tier3", "tier4"],
                        "description": "Which tier(s) to run. Default: 'all'",
                    },
                    "articulation_path": {
                        "type": "string",
                        "description": "Optional: limit checks to a specific robot articulation path",
                    },
                },
            },
        },
    },

# ─── RL Training Debugging & Quality (Phase 7A Addendum) ──────────────────
    {
        "type": "function",
        "function": {
            "name": "diagnose_training",
            "description": "Diagnose an active or completed RL training run. Reads TensorBoard scalars and RSL-RL perf logs from the run directory, then runs checks for: action collapse (policy std near zero), entropy collapse (premature exploration loss), reward hacking (reward up but success flat), bimodal success (high per-env variance), NaN detection with PD stability check, and throughput. Returns per-check status + concrete suggestions. Use when training appears stuck, diverged, or behaves degenerately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Path to the training run directory containing TensorBoard event files and (optionally) RSL-RL perf logs"},
                    "physics_dt": {"type": "number", "description": "Physics time step used during training (used by the PD stability check). Default: 1/120"},
                },
                "required": ["run_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_reward",
            "description": "Static analysis of an RL reward function — runs BEFORE training to catch common pitfalls. Checks for: sparse reward (reward desert), dominant term (one term swamps others), reward hacking risk (alive bonus without fall termination), scale issue (max reward too small for value learning), and success-alignment (terms don't correlate with success criterion). Returns per-check verdict with specific fix suggestions. Use when reviewing a new reward function before launching training.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reward_code": {"type": "string", "description": "Python source code of the reward function or RewTerm definitions"},
                    "has_fall_termination": {"type": "boolean", "description": "Whether the env has a fall/early-termination condition. Default: false"},
                    "max_possible_reward": {"type": "number", "description": "Optional max reward magnitude per step (used for scale check). If omitted, inferred from code."},
                },
                "required": ["reward_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profile_training_throughput",
            "description": "Analyze RSL-RL performance logs (Perf/collection_time, Perf/learning_time, Perf/total_fps) to identify whether a training run is sim-bound or train-bound. Suggests concrete fixes: TiledCamera switch for vision policies (10x faster), reducing num_envs/collision-mesh complexity for sim-bound, smaller networks/batch/PPO epochs for train-bound. Use when training throughput is lower than expected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Path to the training run directory containing RSL-RL perf logs (TensorBoard event files)"},
                },
                "required": ["run_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_eval_harness",
            "description": "Generate a reproducible Python evaluation script for a trained RL policy. Runs num_episodes deterministic rollouts on the given task, records reward/success/length per episode, saves a JSON results file, and optionally records video via gym.wrappers.RecordVideo. Use after training completes to benchmark the learned policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "Gym task ID — e.g. 'Isaac-Reach-Franka-v0'"},
                    "num_episodes": {"type": "integer", "description": "Number of evaluation episodes. Default: 100"},
                    "output_dir": {"type": "string", "description": "Directory to write eval_results.json (and optional videos). Default: 'workspace/eval/<task>'"},
                    "checkpoint_path": {"type": "string", "description": "Optional path to a trained policy checkpoint to load before rollout"},
                    "record_video": {"type": "boolean", "description": "If true, wrap the env with gym.wrappers.RecordVideo. Default: false"},
                    "max_steps_per_episode": {"type": "integer", "description": "Hard cap on steps per episode. Default: 1000"},
                },
                "required": ["task_name"],
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
]
