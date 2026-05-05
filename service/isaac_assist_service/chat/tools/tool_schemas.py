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
            "description": (
                "Create a new USD prim (Cube, Sphere, Cylinder, Cone, Mesh, Xform, Camera, etc.) at a given path. "
                "IMPORTANT — USD primitive defaults: Cube.size=2m (edge length), Sphere.radius=1m, "
                "Cylinder/Cone/Capsule radius=1m height=2m. If the user wants a 1m cube, EITHER pass "
                "size=1.0 (preferred, authors the USD attribute directly) OR scale=[0.5,0.5,0.5] (scales the 2m default). "
                "Passing scale=[1,1,1] on a Cube leaves it at 2m, not 1m. Same caveat for sphere/cylinder radius/height. "
                "POSITION: if omitted, the prim is placed at world origin (0, 0, 0). Always pass `position` "
                "unless you specifically want origin. "
                "GEOMETRY: a Cube with `size=S` at `position=(x,y,z)` has its bounds at "
                "[x-S/2 .. x+S/2, y-S/2 .. y+S/2, z-S/2 .. z+S/2] — its TOP face is at z+S/2, NOT at z+S. "
                "If you need to place another prim on top, do NOT compute z yourself — call `place_on_top_of` "
                "which reads the actual bbox. "
                "Lights: pass `intensity` (defaults to 1000 if omitted for "
                "DomeLight / DistantLight / SphereLight / RectLight / DiskLight / CylinderLight)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path for the new prim, e.g. '/World/MyCube'"},
                    "prim_type": {"type": "string", "description": "Type: Cube, Sphere, Cylinder, Cone, Capsule, Mesh, Xform, Camera, DistantLight, DomeLight"},
                    "position": {"type": "array", "items": {"type": "number"}, "description": "XYZ position in world space [x, y, z]. IF OMITTED: prim is placed at origin (0, 0, 0). Always specify this for geometry you don't want stacked at origin."},
                    "scale": {"type": "array", "items": {"type": "number"}, "description": "XYZ scale [sx, sy, sz]. Multiplies the primitive's geometric defaults (see description)."},
                    "rotation_euler": {"type": "array", "items": {"type": "number"}, "description": "Euler rotation in degrees [rx, ry, rz]"},
                    "size": {"type": "number", "description": "Cube: edge length in meters. Overrides the USD default (2m). Ignored for non-Cube types."},
                    "radius": {"type": "number", "description": "Sphere/Cylinder/Cone/Capsule: radius in meters. Overrides the USD default (1m). Ignored for non-round types."},
                    "height": {"type": "number", "description": "Cylinder/Cone/Capsule: height in meters. Overrides the USD default (2m). Ignored for types without height."},
                    "intensity": {"type": "number", "description": "Light intensity (inputs:intensity). Only used for Light prim types. Default: 1000 if a Light type is specified without explicit intensity."},
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
            "name": "resolve_material_properties",
            "description": (
                "Map a material descriptor ('metal', 'rubber', 'wood', 'soft', 'rigid', "
                "'deformable') to canonical physics properties: density (kg/m^3), "
                "static/dynamic friction, restitution, and body_type (rigid|deformable). "
                "USE THIS instead of inventing per-material physics numbers. body_type "
                "tells you whether to apply RigidBodyAPI or PhysxDeformableBodyAPI."
            ),
            "parameters": {
                "type": "object",
                "properties": {"material": {"type": "string", "description": "Material name. English: metal, steel, aluminum, wood, plastic, rubber, glass, concrete, rigid, soft, deformable, fabric. Swedish: metall, trä, gummi, glas, betong."}},
                "required": ["material"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_constraint_phrase",
            "description": (
                "Parse a constraint phrase ('with 5cm clearance', '10kg max weight', "
                "'within 2 minutes', 'no closer than 1m') into structured numeric data: "
                "{kind: clearance|mass|time|collision_avoidance|angular|size, parsed: "
                "{value, raw_value, raw_unit, si_unit}}. USE THIS to extract numeric "
                "limits before passing them to physics / clearance / fit-check tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {"phrase": {"type": "string", "description": "The full constraint phrase from the prompt, e.g. 'with 5cm clearance' or 'maximum 10kg'."}},
                "required": ["phrase"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_sequence_phrase",
            "description": (
                "Split a sequence phrase ('first X, then Y', 'after X do Y', 'sen Y') "
                "into an ordered list of intent fragments. USE THIS to recover the "
                "execution order before issuing tool calls — issue them in the order "
                "the fragments appear in the returned array. Pure text parsing; no "
                "scene access needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {"phrase": {"type": "string", "description": "The full sequence phrase from the prompt."}},
                "required": ["phrase"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_context_reference",
            "description": (
                "Resolve an implicit context reference like 'another one', 'the same as "
                "before', 'the last cube I made' by querying the stage for the most "
                "recently-created prim of the requested class. USE THIS when the prompt "
                "refers to a recently-made object without naming it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"noun_class": {"type": "string", "description": "Object class to look for: cube/sphere/cylinder/camera/light/robot/etc."}},
                "required": ["noun_class"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_skill_composition",
            "description": (
                "Map a skill-composition name ('pick-and-place', 'calibration', 'ros2', "
                "'teleop', 'rl env') to a known tool-chain recipe. USE THIS when the "
                "user asks for a high-level skill by name — the recipe tells you which "
                "tools to call and in what order. Args_template fields like '<ROBOT>' "
                "are placeholders to fill in (typically via resolve_prim_reference)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"skill": {"type": "string", "description": "Skill / composition name. Known: pick_and_place, calibrate_camera, rl_training_env, ros2_bridge, teleop_demo (plus aliases like 'pick-and-place', 'manipulation', 'calibration', 'rl env')."}},
                "required": ["skill"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_count_vagueness",
            "description": (
                "Map a vague count phrase ('a few', 'several', 'many', 'lots', 'dozens') "
                "to a canonical integer. USE THIS whenever the user asks for an "
                "unspecified plural — do NOT invent a number ('a few cubes' should not "
                "be 7 one turn and 4 the next; use this resolver for stable results). "
                "Variables to extract from the prompt: the count phrase. Returns "
                "{count: int, alternatives}. Use the count directly in the next tool "
                "(loop create_prim N times, etc). Known phrases include English (one, "
                "couple, pair, few, several, some, many, lots, dozens, hundreds) and "
                "Swedish (en, ett par, några, flera, många, dussintals, hundratals)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "description": "The count phrase from the user prompt, lowercase. Examples: 'a few', 'many', 'several', 'a couple', 'lots'.",
                    },
                },
                "required": ["term"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_robot_class",
            "description": (
                "Map a generic robot class phrase ('a manipulator', 'a humanoid', 'a "
                "wheeled robot') to a concrete robot_name from the registry. USE THIS "
                "when the user asks for a robot by class without naming a specific "
                "model — do NOT invent asset paths or robot names. Returns "
                "{robot_name, asset_url, robot_type, alternatives}. Pass the resolved "
                "robot_name straight to robot_wizard, or use the asset_url with "
                "import_robot. Known classes: manipulator/arm (→franka_panda), "
                "wheeled/mobile/amr (→nova_carter), humanoid/biped (→h1), "
                "quadruped/dog (→anymal_c, spot), hand/gripper (→allegro)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_class": {
                        "type": "string",
                        "description": "The robot class phrase from the user prompt, lowercase. Examples: 'manipulator', 'humanoid', 'wheeled robot', 'quadruped'.",
                    },
                },
                "required": ["robot_class"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_size_adjective",
            "description": (
                "Map a size adjective ('small', 'tiny', 'large', 'huge') for a given "
                "object class to a canonical numeric extent in meters. USE THIS whenever "
                "the user uses a size adjective in a prompt — do NOT invent specific "
                "numbers like 0.5 or 0.1 yourself, because each invocation will pick a "
                "different value and 'a small cube' will be different sizes across turns. "
                "Variables to extract from the prompt: the size adjective ('small', "
                "'large', etc) and the head noun describing the object class (cube, "
                "sphere, table, conveyor, robot, ...). Stop and identify those before "
                "calling. Returns {value, unit: 'meters', bucket, alternatives}. Use the "
                "value directly in the next tool's size/scale arg. If the user "
                "subsequently pushes back ('no, smaller'), call again with a smaller "
                "bucket — alternatives gives you the neighbouring values so you don't "
                "have to guess. Known adjectives: tiny / small / medium / large / huge "
                "and Swedish equivalents (liten/litet, mellan, stor/stort, enorm)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "adjective": {
                        "type": "string",
                        "description": "The size adjective from the user prompt, lowercase. Examples: 'small', 'large', 'tiny', 'huge'. Swedish: 'liten', 'stor', 'enorm', 'mellan'.",
                    },
                    "object_class": {
                        "type": "string",
                        "description": "The head noun for the object being sized, lowercase singular. Examples: 'cube', 'sphere', 'table', 'conveyor', 'bin', 'wall', 'room'. If unknown, leave empty — the resolver falls back to a sane default scale.",
                    },
                },
                "required": ["adjective"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_prim_reference",
            "description": (
                "Resolve a deictic reference ('the cube', 'the robot', 'this one', 'the light') "
                "to one or more concrete prim paths in the current stage. Do NOT hallucinate "
                "prim paths like '/World/Cube' — search the actual stage via this tool. "
                "Variables to extract from the prompt: the head noun (cube/robot/light/...) and "
                "any optional adjective. Stop and identify those before calling. "
                "Returns {candidates: [{prim_path, type, position}, ...], count, exact_match, "
                "ambiguous, no_match}. Agent protocol after the call: count==1 → use exact_match "
                "in the next tool call; count>1 → ASK the user 'which one?' with the candidates; "
                "count==0 → tell the user nothing matches, offer to create or rename. "
                "This is the resolver-as-clarification-gate pattern: when the resolver "
                "returns ambiguous, the *resolver* tells you to ask, not the prompt-classifier."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_hint": {
                        "type": "string",
                        "description": "The head noun from the user's reference, lowercase, in Swedish or English. Examples: 'kub', 'kuben', 'cube', 'robot', 'ljus', 'kamera'. Definite-article suffixes are stripped automatically.",
                    },
                    "prim_type": {
                        "type": "string",
                        "description": "Optional explicit USD type filter (e.g. 'Cube', 'Sphere', 'Camera'). 'Robot' is a virtual class — matches any prim with PhysicsArticulationRootAPI. 'Light' matches all UsdLux light types. Leave empty when name_hint already implies the type.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_on_top_of",
            "description": (
                "Place a source prim on top of a target prim, aligned correctly. "
                "USE THIS whenever the user uses spatial language ('place X on top of Y', "
                "'put the robot on the cube', 'stack X on Y', 'sit on'). Do NOT use "
                "robot_wizard's `position` arg and do NOT compute z manually from `size` "
                "or `scale`. This tool reads the target's authoritative world-space "
                "bounding box and the source's local bbox, then places the source so "
                "the lowest point of its mesh sits exactly `clearance` (default 1mm) "
                "above the target's top surface. Handles the common gotchas the LLM "
                "gets wrong: USD Cube `size`-as-edge-length vs half-extent confusion, "
                "and asset-origin-vs-mesh-base offsets like Franka's flange thickness. "
                "Variables to extract from the prompt: source prim path, target prim "
                "path. Stop and identify those two before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {
                        "type": "string",
                        "description": "USD path of the source prim — what to place. Must already exist in the stage.",
                    },
                    "target_prim_path": {
                        "type": "string",
                        "description": "USD path of the target prim — what to place on. Must already exist with geometry (bbox).",
                    },
                    "clearance": {
                        "type": "number",
                        "description": "Vertical gap above the target's top surface, in stage units (default 0.001 = 1mm — beats z-fighting in the depth buffer while staying within PhysX's contact-offset so physics treats them as touching). Raise to 0.01+ if you want a visible gap.",
                    },
                    "xy_align": {
                        "type": "string",
                        "enum": ["center", "preserve"],
                        "description": "'center' (default): center the source's xy on the target's xy. 'preserve': keep the source's existing xy translate (only adjust z).",
                    },
                },
                "required": ["prim_path", "target_prim_path"],
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
    {
        "type": "function",
        "function": {
            "name": "lookup_api_deprecation",
            "description": "**PREFER THIS** over lookup_knowledge for any query about API migration (4.x→5.x), deprecated names, version-gate rules (e.g. ArticulationRootAPI placement, ROS2 namespace, deterministic replay, URDF importer), or cite-able technical facts. Returns structured rows with tool_5x (authoritative 5.x name), deprecated_4x (flag as removed), ready-to-paste cite paragraph, caveats (the hidden gotchas — like 'GridCloner' as the alternative when users propose moving ArticulationRootAPI), and references. DETERMINISTIC KEYWORD INDEX — the returned fields are the canonical phrasing, use them verbatim. Call this BEFORE answering anything about: 'can I use X?' / 'is X deprecated?' / 'where did X move to in 5.x?' / 'what's the 5.x replacement for X?'. If both this and lookup_knowledge seem applicable, call this one first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text query — e.g. 'deterministic replay for CI regression', 'ros2 bridge migration', 'urdf importer 5.x'.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max rows to return (default 3). Use 1 when you want the single best match.",
                    },
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

    # ─── Local Filesystem Search ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_local_files",
            "description": (
                "Search the local filesystem under known asset roots (workspace, "
                "Downloads, Documents, /tmp) for asset files (URDF, USD, STEP, "
                "IFC, etc). USE THIS BEFORE asking the user for a file path — if "
                "the user says 'this URDF' or 'my STEP file' without giving a "
                "path, search first. Only ask the user when search returns zero "
                "matches or multiple plausible matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob-style pattern matched against the filename basename (case-insensitive). Examples: '*ur10*', 'franka*.urdf', '*warehouse*.usd'. Default '*' (match all).",
                    },
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Limit to these file extensions. Examples: ['.urdf'], ['.step','.stp','.iges'], ['.usd','.usda','.usdc']. Defaults to all asset-relevant extensions.",
                    },
                    "search_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Override default search roots. Default: [workspace/, data/, ~/Downloads, ~/Documents, /tmp].",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matches to return (default 50, hard cap 200).",
                    },
                },
                "required": [],
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
    # ── From feat/tools-and-bugfixes ─────────────────────────────────
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

    # ── From feat/7B-replicator-sdg-v2 ─────────────────────────────────
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

    # ── From feat/7C-xr-teleoperation ─────────────────────────────────
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

    # ── From feat/7D-arena ─────────────────────────────────
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

    # ── From feat/7E-eureka-rewards ─────────────────────────────────
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

    # ── From feat/7F-zmq-bridge ─────────────────────────────────
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

    # ── From feat/7G-groot-n1 ─────────────────────────────────
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

    # ── From feat/7H-cloud-deployment ─────────────────────────────────
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

    # ── From feat/8A-quick-wins ─────────────────────────────────
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

    # ── From feat/8B-motion-planning-complete ─────────────────────────────────
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

    # ── From feat/8C-cortex-v2 ─────────────────────────────────
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

    # ── From feat/8D-robot-setup ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "robot_wizard",
                "description": "Import a robot from URDF/USD, apply sensible drive defaults based on robot type, apply convex-hull collision meshes, and print a configuration summary. PREFERRED USAGE: pass `robot_name` (e.g. 'franka_panda') — the tool resolves a verified canonical Isaac Sim 5.1 URL, no guessing needed. Fallback: pass `asset_path` for custom URLs/URDFs. For USD assets, the reference is added at `dest_path` (default /World/Robot) — pass `dest_path` explicitly for paths like /World/Franka.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_name": {"type": "string", "description": "Known-robot name. Currently supports: 'franka_panda' (aliases: 'franka', 'panda'). When provided, the tool uses the registered canonical 5.1 asset URL — preferred over asset_path for supported robots because it eliminates URL-guessing drift."},
                        "asset_path": {"type": "string", "description": "Path to URDF or USD robot file. Use this for custom robots or when robot_name is not in the registry. Required if robot_name is not set."},
                        "dest_path": {"type": "string", "description": "USD path where the robot should be created. Default: /World/Robot. Pass /World/Franka or /World/UR10 etc. when the task specifies a path. URDF imports ignore this."},
                        "position": {"type": "array", "items": {"type": "number"}, "description": "XYZ world position to place the robot base at [x, y, z]. If omitted, the robot stays at the reference's default origin (usually (0,0,0)). ALWAYS pass this when the robot should sit on a table top — e.g. position=[0, 0, 0.75] for a 0.75m-tall table."},
                        "orientation": {"type": "array", "items": {"type": "number"}, "description": "Robot base orientation. Pass a 4-element quaternion (w, x, y, z) or a 3-element euler angle [roll, pitch, yaw] in radians. Use this when the spec requires a specific facing direction — e.g. orientation=[0.7071, 0, 0, 0.7071] (90° around Z) to rotate Franka from its default +X-forward to +Y-forward. If omitted, robot keeps its USD-authored default orient (usually identity)."},
                        "robot_type": {
                            "type": "string",
                            "enum": ["manipulator", "mobile", "humanoid"],
                            "description": "Robot category — determines default drive stiffness/damping. Auto-resolved from robot_name when available. Default: manipulator.",
                        },
                        "drive_stiffness": {"type": "number", "description": "Override Kp (position gain). For known robots (robot_name), auto-resolved from profile (Franka=6000). Only set this if the profile default is wrong for your scenario."},
                        "drive_damping": {"type": "number", "description": "Override Kd (damping gain). For known robots, auto-resolved from profile (Franka=500)."},
                        "variants": {"type": "object", "description": "USD variant selections to apply after loading (e.g. {'Gripper': 'AlternateFinger'} for Franka). For known robots this is auto-applied from the profile — only pass to override."},
                        "home_joints": {"type": "array", "items": {"type": "number"}, "description": "Default joint positions in radians, one per DOF (7 arm + 2 finger for Franka). Applied as drive target positions so the robot holds this pose after play. For known robots auto-applied from profile."},
                    },
                    "required": [],
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

    # ── From feat/8E-wheeled-robots ─────────────────────────────────
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
                "name": "create_bin",
                "description": "Build an open-top container (5 thin collision-enabled Cubes: floor + 4 walls) at a world position with consistent internal dimensions. Use this instead of authoring the 5-prim pattern manually — the tool computes all wall offsets from the size argument so the floor, walls, and interior stay geometrically coherent. Each child Cube gets UsdPhysics.CollisionAPI. Canonical pick-and-place drop-off target; pairs well with create_conveyor + robot pick-place controllers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path for the bin parent Xform, e.g. '/World/Bin'."},
                        "size": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "External [width, depth, height] in meters. Default: [0.3, 0.3, 0.15]. Interior volume will be (w - 2t) × (d - 2t) × (h - t) where t is wall_thickness.",
                        },
                        "position": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "World [x, y, z] of the bin base corner (floor bottom). Default: [0, 0, 0]. To place bin on a table-top at z=0.75, pass [x, y, 0.75].",
                        },
                        "wall_thickness": {"type": "number", "description": "Wall + floor thickness in meters. Default: 0.01 (PhysX-reliable minimum)."},
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
                "name": "setup_pick_place_controller",
                "description": "Composite stateful pick-and-place controller with a full matrix of motion architectures via target_source. Pick based on hardware + scenario: 'auto' (let the system probe the env and pick — recommended default when hardware is unknown); 'native' (Franka + CPU, reactive RmpFlow, 1/4 baseline delivery on conveyor-pick-place); 'spline' (CPU-only, deterministic pre-planned Cartesian waypoints with scipy.CubicSpline — beats native 3x at 3/4 delivery; best CPU choice); 'curobo' (GPU-accelerated global optimization with collision awareness — shortest cycle, requires NVIDIA GPU >= Volta + cuRobo lib + Warp 1.9+); 'sensor_gated' (industrial PLC-mimic, pre-taught or coord-IK poses, belt pause gated on photoelectric sensor); 'fixed_poses' (timer-driven pose replay, no sensing); 'cube_tracking' (omniscient — NOT sim2real); 'ros2_cmd' (external ROS2/MoveIt drives state machine); 'diffik' (Isaac Lab stateless Jacobian, teleop use case); 'osc' (experimental task-space impedance). Before calling, invoke list_available_controllers to see which modes are runnable on the current env. Installs physics-step callback — start the simulation to begin.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_path": {"type": "string", "description": "USD path to articulation root"},
                        "target_source": {
                            "type": "string",
                            "description": (
                                "Motion controller architecture. Decision guide: "
                                "(1) 'auto' — probe env and pick best; safe default when the user's hardware is unknown. "
                                "(2) 'curobo' — fastest cycle time and only option with true collision-aware planning; requires NVIDIA GPU (Volta 7.0+, 4GB VRAM) AND cuRobo package. Production quality. "
                                "(3) 'spline' — best CPU-only option. Deterministic 6-waypoint Cartesian trajectory, warm-start IK chaining, scipy.CubicSpline interpolation. Verified 3/4 delivery on conveyor scenario (beats native 3x). Use for CPU-only machines, sim2real demos, reproducible cycles. "
                                "(4) 'native' — canonical Franka PickPlaceController (only Franka supported). Reactive, RmpFlow-based, 1/4 delivery baseline. Use when the user explicitly wants the stock Isaac Sim controller. "
                                "(5) 'sensor_gated' — industrial PLC-mimic with pre-taught or coord-IK PICK/DROP/HOME. Use for PLC/teach-pendant sim2real workflows (non-Franka arms, CPU). "
                                "(6) 'ros2_cmd' — external ROS2/MoveIt drives state machine. Digital-twin / PLC-in-loop. "
                                "(7) 'fixed_poses' — timer-driven pose replay. Cycle-time demos / validation only. "
                                "(8) 'cube_tracking' — omniscient live retargeting. ML demo-gen ONLY (NOT sim2real honest). "
                                "(9) 'diffik' — Isaac Lab Jacobian IK. Teleop / Cartesian RL obs. No obstacle awareness. "
                                "(10) 'osc' — Isaac Lab operational-space impedance (experimental, contact-rich tasks)."
                            ),
                            "enum": ["auto", "native", "spline", "curobo", "diffik", "osc",
                                     "sensor_gated", "fixed_poses", "cube_tracking", "ros2_cmd"],
                        },
                        "source_paths": {"type": "array", "items": {"type": "string"}, "description": "native/cube_tracking mode: objects to pick (in priority order)"},
                        "destination_path": {"type": "string", "description": "native/cube_tracking mode: bin prim — controller drops at its top-center + 0.05m clearance unless drop_target overrides"},
                        "sensor_path": {"type": "string", "description": "native/sensor_gated mode: USD path to a proximity sensor (from add_proximity_sensor). In native mode, if omitted the controller free-runs (picks first available cube continuously)."},
                        "belt_path": {"type": "string", "description": "native/sensor_gated mode: conveyor prim to pause during pick and resume after release"},
                        "end_effector_offset": {"type": "array", "items": {"type": "number"}, "description": "native mode: [x, y, z] offset from EE frame to grasp point. Default [0, 0.005, 0] — verified from Franka standalone. Do NOT set a z-offset like [0, 0, -0.098] unless you know your gripper requires it; the canonical controller handles TCP internally."},
                        "pick_pose_name": {"type": "string", "description": "sensor_gated mode (pose-replay style): name of pre-taught pick pose. Requires teach_robot_pose to have run first. Use this OR pick_target, not both."},
                        "drop_pose_name": {"type": "string", "description": "sensor_gated mode (pose-replay): name of pre-taught drop pose"},
                        "home_pose_name": {"type": "string", "description": "sensor_gated mode (pose-replay): name of pre-taught home/idle pose"},
                        "pick_target": {"type": "array", "items": {"type": "number"}, "description": "sensor_gated mode (coord-IK style): world position [x, y, z] the end-effector should reach at the pick station. Controller uses RmpFlow IK at runtime — no teach step needed. Use this for automated sim pipelines where playing sim + teaching poses would add unnecessary complexity. Override: if all three of pick_target, drop_target, home_target are set, coord mode is used and pose_name args are ignored."},
                        "drop_target": {"type": "array", "items": {"type": "number"}, "description": "sensor_gated mode (coord-IK): world position [x, y, z] for the drop point (above the bin)"},
                        "home_target": {"type": "array", "items": {"type": "number"}, "description": "sensor_gated mode (coord-IK): world position [x, y, z] for the idle/home pose (clear of belt and bin)"},
                        "grip_style": {
                            "type": "string",
                            "enum": ["fixed_joint", "friction"],
                            "description": "sensor_gated mode: how the cube is held during transport. 'fixed_joint' (default) creates a UsdPhysics.FixedJoint between end-effector and cube — robust, demo-stable, but physically dishonest (cube follows EE regardless of finger contact). 'friction' skips the joint; fingers close via position-drive and the cube is held by contact friction alone — bind source cubes + fingers to a high-friction PhysicsMaterialAPI at install. More realistic but flaky under belt motion or wrong tuning; expect iteration on mass, friction coefficients, and drive gains.",
                        },
                        "pose_sequence": {"type": "array", "items": {"type": "string"}, "description": "fixed_poses mode: ordered list of pose names"},
                        "cycles": {"type": "integer", "description": "fixed_poses mode: how many times to repeat the sequence"},
                        "target_topic": {"type": "string", "description": "ros2_cmd mode: topic for EE target pose"},
                        "gripper_topic": {"type": "string", "description": "ros2_cmd mode: topic for gripper command"},
                        "end_effector_link": {"type": "string"},
                        "gripper_joint_1": {"type": "string"},
                        "gripper_joint_2": {"type": "string"},
                        "gripper_open": {"type": "number"},
                        "gripper_close": {"type": "number"},
                        "approach_height": {"type": "number"},
                        "lift_height": {"type": "number"},
                        "drop_height": {"type": "number"},
                        "end_effector_initial_height": {"type": "number", "description": "native/spline mode: absolute world-Z (m) for trajectory approach/retreat. If omitted, auto-computed as max(source_z, drop_z) + 0.2m clearance. Override when robot is on a tall pedestal or when default clearance clips above the workspace."},
                        "events_dt": {"type": "array", "items": {"type": "number"}, "description": "native mode only: 10-element list overriding PickPlaceController event phase durations. Default [0.008, 0.002, 1, 0.025, 0.05, 0.05, 0.0025, 1, 0.008, 0.08] (approach, descend, grip-wait, grip-close, lift, transit, place-descend, release-wait, release-open, retreat). Lower values = faster cycle but more RmpFlow tracking failure."},
                        "spline_waypoint_dt": {"type": "number", "description": "spline mode: seconds per segment between the 6 Cartesian waypoints. Default 1.5s. Total cycle ~10s with 1.2s dwells. Decrease for faster cycle (requires higher joint-drive gains); increase if fingers miss cubes during close."},
                        "curobo_world_yml": {"type": "string", "description": "curobo mode: path to cuRobo world_config YAML (cuboid/mesh obstacles). If omitted, the live USD stage is used to auto-build a Cuboid scene for collision checking."},
                        "planning_obstacles": {"type": "array", "items": {"type": "string"}, "description": "curobo mode: list of USD paths to include as collision obstacles during planning. Each prim's world-bound is converted to a Cuboid. Use to avoid the conveyor/table/walls during transit."},
                        "diffik_method": {"type": "string", "enum": ["dls", "svd", "pinv"], "description": "diffik mode: Jacobian inversion method. 'dls' (damped least-squares, default, λ=0.05) handles singularities gracefully; 'pinv' is Moore-Penrose pseudoinverse; 'svd' is truncated SVD. Use 'dls' unless you know you need the others."},
                    },
                    "required": ["robot_path", "target_source"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_available_controllers",
                "description": "Probe the current Kit runtime and report which pick-place controller modes (target_source values) are available, plus hardware capabilities (GPU arch + VRAM, scipy version, cuRobo presence, Isaac Lab presence). Use this BEFORE setup_pick_place_controller to pick a target_source that actually works on the user's machine. Returns a list of recommended controllers in priority order ('curobo' first if available, else 'spline'/'native'), and for each controller: hardware_req, cycle_class, motion_quality (1-5), collision_aware, use_case_fit tags, and reason_if_not available.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    {
            "type": "function",
            "function": {
                "name": "add_proximity_sensor",
                "description": "Create an invisible physics trigger volume at a fixed world position. When any prim matching a pattern enters the volume, a custom attribute 'isaac_sensor:triggered' on the sensor prim flips to True; 'isaac_sensor:last_triggered_path' records the most recent entrant. Controllers can read these attributes each frame. Sim2real-honest: sensor output is binary (in-zone / not), the same interface as a real photoelectric beam-break sensor. Used for belt-pause triggering, fixed-station pick confirmation, gate sensors, and safety zones.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sensor_path": {"type": "string", "description": "USD path for the new sensor prim (e.g. /World/PickSensor)"},
                        "position": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] world position for the sensor center"},
                        "size": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] detection volume in meters. Default [0.1, 0.1, 0.1]."},
                        "watched_path_pattern": {"type": "string", "description": "Prefix that triggering prim paths must start with. Default '/World/' (any world prim)."},
                    },
                    "required": ["sensor_path", "position"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "teach_robot_pose",
                "description": "Snapshot the current joint configuration of a robot articulation and save to workspace/robot_poses/<robot>/<pose_name>.json. Mimics the 'teach pendant' flow from industrial robotics: manually jog the robot via Kit UI or a separate script to the desired configuration, then call this to persist it. The saved pose can be restored via load_robot_pose. Industrial workflow: teach {home, pick_approach, pick, pick_lift, drop_approach, drop} and feed them to setup_pick_place_controller(target_source='sensor_gated').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_path": {"type": "string", "description": "USD path to the articulation root"},
                        "pose_name": {"type": "string", "description": "Short name to save the pose under (e.g. 'home', 'pick', 'drop')"},
                    },
                    "required": ["robot_path", "pose_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "load_robot_pose",
                "description": "Move a robot to a previously-taught pose saved by teach_robot_pose. With interpolation_seconds=0 (default) the move is instantaneous. With >0, joint positions linearly interpolate over N seconds via a physics-step callback. Handles DOF-name remapping if the live articulation has a slightly different order than the saved pose.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_path": {"type": "string"},
                        "pose_name": {"type": "string"},
                        "interpolation_seconds": {"type": "number", "description": "0 = instant, >0 = linear interpolation over N seconds. Default 0."},
                    },
                    "required": ["robot_path", "pose_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "setup_pick_place_ros2_bridge",
                "description": "Industrial-realism alternative to setup_pick_place_controller: wire OmniGraph ROS2 nodes so Isaac Sim publishes robot joint_states + cube poses, subscribes to target-pose + gripper commands. External controller (ROS2 node, or real PLC via OPC-UA bridge) runs the state machine. Use this when the scenario simulates a digital twin / HIL / PLC-integration setup; for in-sim self-contained execution prefer setup_pick_place_controller.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_path": {"type": "string"},
                        "source_paths": {"type": "array", "items": {"type": "string"}},
                        "destination_path": {"type": "string"},
                        "end_effector_link": {"type": "string"},
                        "ros_domain_id": {"type": "integer", "description": "ROS_DOMAIN_ID the bridge uses (default 0). External controller must match."},
                    },
                    "required": ["robot_path", "source_paths", "destination_path"],
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

    # ── From feat/8F-ros2-deep ─────────────────────────────────
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

    # ── From feat/9-finetune-flywheel ─────────────────────────────────
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

    # ── From feat/addendum-phase2-smart-debugging ─────────────────────────────────
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

    # ── From feat/addendum-phase3-urdf-postprocessor ─────────────────────────────────
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

    # ── From feat/addendum-phase7B-sdg-quality ─────────────────────────────────
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

    # ── From feat/addendum-phase8F-ros2-quality ─────────────────────────────────
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

    # ── From feat/addendum-phase8B-workspace-singularity-v2 ─────────────────────────────────
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

    # ── From feat/new-performance-diagnostics ─────────────────────────────────
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

    # ── From feat/new-material-database ─────────────────────────────────
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

    # ── From feat/new-scene-diff ─────────────────────────────────
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

    # ── From feat/new-auto-simplification ─────────────────────────────────
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

    # ── From feat/new-onboarding ─────────────────────────────────
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

    # ── From feat/new-omnigraph-assistant ─────────────────────────────────
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

    # ── From feat/new-interactive-teaching ─────────────────────────────────
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

    # ── From feat/preflight-check-23 ─────────────────────────────────
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

    # ── From feat/addendum-phase7A-rl-debugging ─────────────────────────────────
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

    # ── From feat/addendum-phase7C-teleop-quality ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "check_teleop_hardware",
                "description": "Check whether a teleop input device is supported, report its transport (WebXR, CloudXR, USB-HID), latency budget, and known limitations. Use before calling start_teleop_session so the LLM can decide whether the device will work on the user's setup.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device": {
                            "type": "string",
                            "enum": ["quest_3", "vision_pro", "spacemouse", "keyboard"],
                            "description": "Input device to probe. quest_3 uses WebXR over Wi-Fi, vision_pro requires CloudXR native, spacemouse/keyboard are local USB-HID.",
                        },
                    },
                    "required": ["device"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "validate_teleop_demo",
                "description": "Validate an HDF5 teleop demo file against the robomimic schema used by Phase 7C recording. Checks action shape, obs keys, episode length, and NaN/Inf in actions. Use before feeding demos to Phase 7G fine-tuning to avoid wasted GPU time on corrupt episodes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hdf5_path": {"type": "string", "description": "Absolute path to the .hdf5 demo file recorded by record_teleop_demo."},
                    },
                    "required": ["hdf5_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "export_teleop_mapping",
                "description": "Generate a Python script that writes a teleop mapping YAML to workspace/teleop_mappings/<session_name>.yaml. The YAML follows the IsaacTeleop / dex-retargeting config shape and can be committed, diffed, and re-fed to configure_teleop_mapping.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_name": {"type": "string", "description": "Short identifier for the mapping file (becomes <session_name>.yaml)."},
                        "device": {
                            "type": "string",
                            "enum": ["quest_3", "vision_pro", "spacemouse", "keyboard"],
                            "description": "Input device the mapping targets.",
                        },
                        "joint_map": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of joint entries: [{'name': str, 'source': str, 'gain': float, 'limit_rad': [lo, hi]}, ...]",
                        },
                        "gains": {
                            "type": "object",
                            "description": "Global gains, e.g. {'position': 400, 'velocity': 40}.",
                        },
                        "robot": {"type": "string", "description": "Robot identifier for the mapping (default 'franka_panda')."},
                    },
                    "required": ["session_name", "device", "joint_map"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "generate_teleop_watchdog_script",
                "description": "Generate a Python script that arms a teleop watchdog inside Kit: if no control message arrives on /ws/teleop within timeout_ms, the watchdog holds the last command for hold_time_ms and then zeros all joint velocity targets on robot_path. Use this to add a safety layer around start_teleop_session.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_path": {"type": "string", "description": "USD path of the articulation the watchdog protects, e.g. '/World/Franka'."},
                        "timeout_ms": {"type": "integer", "description": "Milliseconds of silence before hold mode triggers. Default 500."},
                        "hold_time_ms": {"type": "integer", "description": "Milliseconds to hold last command before zeroing. Default 2000."},
                        "socket_path": {"type": "string", "description": "WebSocket path to subscribe to. Default '/ws/teleop'."},
                    },
                    "required": ["robot_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "summarize_teleop_session",
                "description": "Summarize a recorded HDF5 teleop session: demo count, total duration, per-joint velocity and range statistics. Use to answer human-readable questions ('how much data did I record?') and to seed Phase 7G fine-tuning decisions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hdf5_path": {"type": "string", "description": "Absolute path to the .hdf5 demo file recorded by record_teleop_demo."},
                        "fps": {"type": "integer", "description": "Recording frame rate. Defaults to the file's attr 'fps' or 30."},
                    },
                    "required": ["hdf5_path"],
                },
            },
        },

    # ── From feat/addendum-phase7B-sdg-advanced ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "scatter_on_surface",
                "description": "Scatter source prims across the surface of an arbitrary target mesh (e.g. fruit on a plant branch). Samples random points on the mesh surface with optional Poisson-disk spacing, normal alignment, and penetration checks. Use when objects must rest on organic / curved geometry rather than flat planes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_prims": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to the prims to scatter (one is picked at random per placement).",
                        },
                        "target_mesh": {"type": "string", "description": "USD path or filesystem path to the target Mesh prim that surfaces are sampled on."},
                        "count": {"type": "integer", "description": "Number of placements to attempt. Default: 50"},
                        "spacing": {"type": "number", "description": "Minimum distance between placements in meters (Poisson-disk). 0 disables. Default: 0.0"},
                        "normal_align": {"type": "boolean", "description": "Align placed object Y-axis to surface normal (e.g. fruit hangs from branch). Default: true"},
                        "penetration_check": {"type": "boolean", "description": "Reject placements that intersect existing geometry. Default: false"},
                        "seed": {"type": "integer", "description": "Random seed for reproducibility. Default: 0"},
                    },
                    "required": ["source_prims", "target_mesh", "count"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "configure_differential_sdg",
                "description": "Configure differential / partial re-render for an SDG pipeline: freeze static scene elements (geometry, materials) and only re-randomize / re-render dynamic ones per frame. Yields 3-10x throughput gain vs full per-frame re-evaluation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "static_elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to elements that should NOT be re-evaluated each frame (walls, floor, static props).",
                        },
                        "dynamic_elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to elements that ARE re-randomized / re-rendered each frame (lights, cameras, dynamic objects).",
                        },
                        "randomize": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["rotation", "position", "color", "intensity", "scale"]},
                            "description": "Which randomizers to apply to dynamic elements. Default: ['rotation', 'color']",
                        },
                    },
                    "required": ["static_elements", "dynamic_elements"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "configure_coco_yolo_writer",
                "description": "Configure a custom COCO/YOLO writer with multi-camera ID handling: globally unique annotation IDs across all cameras, per-camera image-ID namespacing, single merged category map, and optional YOLO (txt + images) output instead of COCO JSON.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_dir": {"type": "string", "description": "Directory where annotations and images are written."},
                        "cameras": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to camera prims to attach to.",
                        },
                        "format": {"type": "string", "enum": ["coco", "yolo"], "description": "Output format. Default: 'coco'"},
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Class names in canonical order (becomes shared category map).",
                        },
                        "id_offset": {"type": "integer", "description": "Starting offset for per-camera image-ID namespace. Default: 1000000"},
                    },
                    "required": ["output_dir", "cameras"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "benchmark_sdg",
                "description": "Run a headless SDG throughput benchmark: report frames-per-second per annotator combination, peak VRAM usage, disk I/O throughput, and a coarse bottleneck label (CPU randomization vs GPU render vs disk write). Compares against preset baselines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pipeline_id": {"type": "string", "description": "Identifier of the SDG pipeline to benchmark (e.g. last configured one)."},
                        "num_frames": {"type": "integer", "description": "Number of frames to run. Default: 100"},
                        "annotators": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Annotator combination to benchmark (e.g. ['rgb', 'depth', 'bounding_box_2d']). Default: ['rgb']",
                        },
                        "resolution": {"type": "array", "items": {"type": "integer"}, "description": "[width, height]. Default: [1280, 720]"},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "enforce_class_balance",
                "description": "Enforce minimum class-occurrence per frame before writing to disk. If a declared class is missing (e.g. occluded fruit), re-randomize and retry up to max_retries before falling back to writing the partial frame.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "min_per_class": {"type": "integer", "description": "Minimum required occurrences per class per frame. Default: 1"},
                        "max_retries": {"type": "integer", "description": "Max retry attempts per frame before giving up. Default: 5"},
                        "classes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional explicit list of class names that must appear. If omitted, derived from the pipeline's semantic labels.",
                        },
                        "write_partial_on_fail": {"type": "boolean", "description": "If true, write the frame anyway after retries are exhausted. Default: true"},
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/addendum-enterprise-scale ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "build_stage_index",
                "description": "Build a lightweight metadata index of the USD stage (prim path, type, applied schemas, physics flags) for fast retrieval at enterprise scale. Replaces full-stage context-stuffing into the LLM window. Use prim_scope to index a subtree only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_scope": {"type": "string", "description": "USD subtree root to index. Default: '/World'."},
                        "max_prims": {"type": "integer", "description": "Safety cap on indexed prims. Default: 50000."},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "query_stage_index",
                "description": "Query the in-memory stage metadata index for prims relevant to a question. Returns 50-200 matching prims instead of the full stage — critical for keeping LLM context small at 50K+ prims. Searches prim paths, types, and schemas by keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords to match against prim path / type / schema names."},
                        "selected_prim": {"type": "string", "description": "Optional currently selected prim path — neighbours are included in results."},
                        "max_results": {"type": "integer", "description": "Maximum prims to return. Default: 100."},
                    },
                    "required": ["keywords"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "save_delta_snapshot",
                "description": "Save only the USD layer deltas (dirty layers) rather than the whole stage. Dramatically smaller than a full snapshot at 50K+ prims (800MB-2GB → KB-MB). Applies on top of a base snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "snapshot_id": {"type": "string", "description": "Identifier for the delta snapshot — e.g. 'delta_2026_04_15_001'."},
                        "base_snapshot_id": {"type": "string", "description": "Optional base snapshot to diff against. If omitted, the snapshot manager's last full snapshot is used."},
                    },
                    "required": ["snapshot_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "restore_delta_snapshot",
                "description": "Restore a delta snapshot by replaying the saved dirty-layer strings on top of the base snapshot. Inverse of save_delta_snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "snapshot_id": {"type": "string", "description": "Delta snapshot identifier to restore."},
                    },
                    "required": ["snapshot_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "batch_delete_prims",
                "description": "Delete many prims atomically using Sdf.BatchNamespaceEdit — a single Hydra rebuild instead of thousands of individual omni.kit.commands calls. Use whenever removing >10 prims at once.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_paths": {"type": "array", "items": {"type": "string"}, "description": "List of USD paths to remove in one batch."},
                    },
                    "required": ["prim_paths"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "batch_set_attributes",
                "description": "Set many prim attributes in a single change-block so only one stage notification fires. Use whenever modifying >10 attributes at once; much faster than per-attribute omni.kit.commands calls.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "changes": {
                            "type": "array",
                            "description": "Attribute changes to apply atomically.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prim_path": {"type": "string"},
                                    "attr_name": {"type": "string"},
                                    "value": {"description": "New value (number, array, string, bool)."},
                                },
                                "required": ["prim_path", "attr_name", "value"],
                            },
                        },
                    },
                    "required": ["changes"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "activate_area",
                "description": "Selectively activate a single robot cell or area — deactivates every prim outside prim_scope (SetActive(False)) so they're excluded from physics and rendering. Dramatically reduces load on 50K+ prim enterprise stages when the user only cares about one cell.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_scope": {"type": "string", "description": "USD path of the subtree to keep active. Everything outside this scope is deactivated."},
                        "deactivate_siblings_only": {"type": "boolean", "description": "If true, only deactivate siblings of prim_scope ancestors (preserves /World and pseudo-root). Default: true."},
                    },
                    "required": ["prim_scope"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "queue_write_locked_patch",
                "description": "Queue a Python patch behind the stage write-lock so it serializes cleanly with concurrent OPC-UA / digital-twin syncs that are writing the same layer stack at 30 Hz. Prevents HNSW/USD layer corruption from two writers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute under the write-lock."},
                        "description": {"type": "string", "description": "Human-readable description of the patch."},
                        "priority": {"type": "integer", "description": "Queue priority, higher runs sooner. Default: 0."},
                    },
                    "required": ["code", "description"],
                },
            },
        },

    # ── From feat/addendum-ros2-nav2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "setup_ros2_bridge",
                "description": "Configure a complete ROS2 bridge for a known robot + stack profile in one call. Builds the OmniGraph with all required publishers/subscribers, QoS profiles, and a ROS2 clock node. Use when the user wants to wire Isaac Sim into Nav2, MoveIt2, or another ROS2 stack without configuring nodes individually.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "string",
                            "enum": ["ur10e_moveit2", "jetbot_nav2", "franka_moveit2", "amr_full"],
                            "description": "Predefined bridge profile. ur10e_moveit2 / franka_moveit2 wire joint state + trajectory + TF for arm control. jetbot_nav2 wires lidar + cmd_vel + odom + clock + TF for Nav2. amr_full adds multiple lidars and cameras for full AMR stacks.",
                        },
                        "robot_path": {
                            "type": "string",
                            "description": "USD path to the robot articulation (e.g. '/World/Jetbot'). Used to bind publisher/subscriber nodes to the right articulation.",
                        },
                        "graph_path": {
                            "type": "string",
                            "description": "USD path for the generated OmniGraph. Default: '/World/ROS2_Bridge'",
                        },
                    },
                    "required": ["profile", "robot_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "export_nav2_map",
                "description": "Generate a Nav2 map_server-compatible map.yaml + map.pgm pair from the current Isaac Sim scene. Calls Phase 8A.3 occupancy generation under the hood and writes the standard ROS map format Nav2 reads.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_path": {
                            "type": "string",
                            "description": "Output file stem (no extension). Two files are written: <stem>.pgm and <stem>.yaml. Example: 'workspace/maps/warehouse'",
                        },
                        "resolution": {
                            "type": "number",
                            "description": "Map resolution in meters per pixel. Default: 0.05 (Nav2 standard).",
                        },
                        "origin": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "World-space [x, y, z] origin of the map's bottom-left corner. Default: [0.0, 0.0, 0.0].",
                        },
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "World-space [width, height] of the map area in meters. Default: [10.0, 10.0].",
                        },
                        "height_range": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "[min_z, max_z] slice height for occupancy projection. Cells with geometry between these Z values are considered occupied. Default: [0.05, 0.5].",
                        },
                        "occupied_thresh": {
                            "type": "number",
                            "description": "Nav2 map.yaml occupied threshold (0-1). Default: 0.65",
                        },
                        "free_thresh": {
                            "type": "number",
                            "description": "Nav2 map.yaml free threshold (0-1). Default: 0.196",
                        },
                    },
                    "required": ["output_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "replay_rosbag",
                "description": "Deterministically replay a recorded rosbag through the live Isaac Sim session. Publishes the bag's cmd_vel into the sim and lets the sim produce its own odom/TF, so a downstream comparison can identify where sim and real diverge.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "bag_path": {
                            "type": "string",
                            "description": "Filesystem path to the rosbag2 directory or .db3 file recorded on a real robot.",
                        },
                        "sync_mode": {
                            "type": "string",
                            "enum": ["sim_time", "real_time"],
                            "description": "sim_time = step-locked replay tied to /clock (deterministic, used for sim-vs-real comparisons). real_time = wall-clock replay (used for visualization). Default: sim_time",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional whitelist of bag topics to replay. Default: ['/cmd_vel'].",
                        },
                        "rate": {
                            "type": "number",
                            "description": "Replay rate multiplier (1.0 = original speed). Default: 1.0",
                        },
                    },
                    "required": ["bag_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "check_tf_health",
                "description": "Diagnose the ROS2 TF tree health for the current sim. Reports missing expected frames (base_link, odom, map, sensor frames), stale transforms (>1s old), 'extrapolation into the future' risk, missing static_transforms, and orphan frames not connected to the root chain. Use when Nav2 / MoveIt2 misbehave and TF is suspected.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expected_frames": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Frames that must be present. Default: ['base_link', 'odom', 'map'].",
                        },
                        "max_age_seconds": {
                            "type": "number",
                            "description": "Maximum allowed transform age before it is reported stale. Default: 1.0",
                        },
                        "root_frame": {
                            "type": "string",
                            "description": "Expected TF tree root for orphan detection. Default: 'map'",
                        },
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/addendum-dr-advanced ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "configure_correlated_dr",
                "description": "Configure correlated domain randomization where physically related parameters (e.g. mass and friction, joint damping and temperature) are sampled jointly via a Gaussian copula instead of independently. Generates a Replicator/IsaacLab-compatible randomizer that respects the provided correlation matrix per parameter group.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "parameter_groups": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of correlation groups. Each group: {'params': ['mass', 'static_friction'], 'ranges': {'mass': [0.5, 2.0], 'static_friction': [0.3, 0.8]}, 'correlation': 0.6, 'method': 'copula'}. method in {'copula', 'linear'} (default: 'copula').",
                        },
                        "target_path": {"type": "string", "description": "USD prim path the randomizer applies to. Default: '/World'"},
                        "seed": {"type": "integer", "description": "RNG seed for reproducibility. Default: 0"},
                    },
                    "required": ["parameter_groups"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "suggest_dr_ranges",
                "description": "Recommend reasonable domain-randomization ranges for a task. Given task type, robot, and (optionally) a path to real sensor data, returns suggested ranges for object mass, friction, joint damping, gravity, action latency, and lighting. Uses material/sensor heuristics plus, when real_data_path is provided, empirical variance from logged sensor data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_type": {"type": "string", "description": "Task category — e.g. 'pick_and_place', 'locomotion', 'navigation', 'assembly'. Free-form."},
                        "robot": {"type": "string", "description": "Robot name — e.g. 'Franka Panda', 'UR10', 'Anymal-C'. Free-form; used to look up gripper/material defaults."},
                        "real_data_path": {"type": "string", "description": "Optional path to a CSV/JSON of real sensor measurements. When provided, ranges are estimated from observed variance instead of defaults."},
                    },
                    "required": ["task_type"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "apply_dr_preset",
                "description": "Look up a pre-configured domain-randomization preset for a common scenario (indoor_industrial, outdoor_daylight, warehouse, cleanroom, aggressive_sim2real). Returns the full parameter dict that can be fed into configure_correlated_dr or any IsaacLab event manager.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "preset": {
                            "type": "string",
                            "enum": ["indoor_industrial", "outdoor_daylight", "warehouse", "cleanroom", "aggressive_sim2real"],
                            "description": "Preset name. See the addendum spec for what each preset randomizes.",
                        },
                    },
                    "required": ["preset"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "add_latency_randomization",
                "description": "Inject per-step action delay into an IsaacLab environment to match the jitter of real control loops. Generates an EventManager-compatible ActionLatencyEvent that delays the policy actions by uniform(min_ms, max_ms) each episode. Without this, policies trained in sim fail on real hardware when actions arrive late.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "min_ms": {"type": "number", "description": "Minimum injected latency in milliseconds. Default: 10"},
                        "max_ms": {"type": "number", "description": "Maximum injected latency in milliseconds. Default: 50"},
                        "physics_dt": {"type": "number", "description": "Physics step size in seconds (used to convert ms to step count). Default: 0.005"},
                        "buffer_size": {"type": "integer", "description": "Action buffer size; must be at least ceil(max_ms / (physics_dt * 1000)). Default: auto."},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "preview_dr",
                "description": "Generate a small grid of preview frames using the currently configured domain randomization, so the user can visually verify the ranges before committing to a full SDG / training run. Captures num_samples viewport frames with the DR randomizer triggered between each.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "num_samples": {"type": "integer", "description": "Number of preview frames to generate. Default: 9 (3x3 grid)."},
                        "output_dir": {"type": "string", "description": "Directory for preview images. Default: 'workspace/dr_previews'"},
                        "resolution": {"type": "array", "items": {"type": "integer"}, "description": "[width, height] of each preview. Default: [512, 512]"},
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/addendum-clearance-detection ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "set_clearance_monitor",
                "description": "Configure a near-miss / clearance monitor on a robot articulation. Sets PhysX contactOffset on each robot link so contact-report events fire BEFORE actual penetration, then subscribes to those events to warn when the robot comes within `clearance_mm` of any specified target. Supports multi-tier ISO 10218 zones (warning + stop).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation root (e.g. '/World/Franka')"},
                        "clearance_mm": {"type": "number", "description": "Stop-zone clearance threshold in millimeters. Default: 50.0"},
                        "warning_mm": {"type": "number", "description": "Optional warning-zone threshold in millimeters (must be > clearance_mm). Default: 100.0"},
                        "target_prims": {"type": "array", "items": {"type": "string"}, "description": "USD paths of fixtures / obstacles to monitor against the robot links"},
                    },
                    "required": ["articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "visualize_clearance",
                "description": "Visualize clearance between a robot and surrounding obstacles. mode='heatmap' applies SDF mesh collision and color-codes link positions by signed distance. mode='zones' creates invisible PhysX trigger volumes around obstacles at warning/stop distances.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                        "mode": {"type": "string", "enum": ["heatmap", "zones"], "description": "Visualization mode: 'heatmap' (SDF gradient) or 'zones' (static trigger volumes). Default: 'heatmap'"},
                        "target_prims": {"type": "array", "items": {"type": "string"}, "description": "USD paths of fixture / obstacle prims to visualize clearance against"},
                        "clearance_mm": {"type": "number", "description": "Stop-zone threshold in millimeters. Default: 50.0"},
                        "warning_mm": {"type": "number", "description": "Warning-zone threshold in millimeters (zones mode only). Default: 100.0"},
                    },
                    "required": ["articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "check_path_clearance",
                "description": "Pre-flight clearance check for a planned trajectory. Runs forward kinematics on each waypoint, queries SDF distance to each obstacle, and flags waypoints whose minimum link-to-obstacle clearance falls below the threshold. Use BEFORE execute_trajectory to catch near-miss conditions before motion executes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation root"},
                        "trajectory": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "List of joint-position waypoints, each a list of joint angles in radians"},
                        "obstacles": {"type": "array", "items": {"type": "string"}, "description": "USD paths of obstacle prims (must have a SDF or mesh collider)"},
                        "clearance_mm": {"type": "number", "description": "Minimum allowed clearance in millimeters. Default: 50.0"},
                    },
                    "required": ["articulation_path", "trajectory", "obstacles"],
                },
            },
        },

    # ── From feat/new-physics-calibration ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "calibrate_physics",
                "description": "Calibrate sim physics parameters (joint friction, damping, link masses) from real robot data using Bayesian optimization (Ray Tune + Optuna). Long-running headless job (30-120 min). Real data must be HDF5 with joint_positions, joint_velocities, joint_torques_commanded sampled at 200-500 Hz. Returns calibrated parameters and suggested DR ranges. Use this when the user wants to close the sim-to-real gap by fitting physics parameters to measured robot trajectories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "real_data_path": {"type": "string", "description": "Path to HDF5 file with real robot logs — joint_positions, joint_velocities, joint_torques_commanded at 200-500 Hz, 30-120s of excitation trajectory."},
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation in the IsaacLab scene — e.g. '/World/Franka'."},
                        "parameters_to_calibrate": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["friction", "damping", "armature", "masses", "viscous_friction"]},
                            "description": "Which physics parameters to calibrate. Default: ['friction', 'damping', 'masses']. Skip contact stiffness, restitution, surface friction (not identifiable — use DR instead).",
                        },
                        "num_samples": {"type": "integer", "description": "Number of Bayesian-optimization trial samples. Default: 100."},
                        "num_workers": {"type": "integer", "description": "Number of parallel Ray workers. Default: 4."},
                        "output_dir": {"type": "string", "description": "Directory for calibration results. Default: workspace/calibration/<robot>"},
                    },
                    "required": ["real_data_path", "articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "quick_calibrate",
                "description": "Faster (~5 min) physics calibration that only fits the highest-impact parameters: armature (rotor inertia), Coulomb friction, and link masses (if payload matters). Skips contact stiffness, restitution, surface friction (these should be randomized via DR, not calibrated). Use this for a first-pass sim-to-real gap reduction before running the full calibrate_physics.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "real_data_path": {"type": "string", "description": "Path to HDF5 file with joint logs — 30s minimum at 200-500 Hz."},
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation."},
                        "include_masses": {"type": "boolean", "description": "Whether to include link-mass calibration. Default: true."},
                        "output_dir": {"type": "string", "description": "Directory for calibration results. Default: workspace/calibration/<robot>_quick"},
                    },
                    "required": ["real_data_path", "articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "validate_calibration",
                "description": "Validate a calibration result on a held-out real-data trajectory. Computes per-joint trajectory tracking error (RMSE on positions and velocities) and contact-force comparison if F/T data is present. Reports whether the calibrated parameters reduced the sim-to-real gap. Use this after calibrate_physics or quick_calibrate to check the result on unseen data before committing the parameters.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "calibrated_params": {
                            "type": "object",
                            "description": "Calibrated parameters dict — typically the 'calibrated_parameters' field returned by calibrate_physics. Keys may include joint_friction, joint_damping, link_masses, armature.",
                        },
                        "test_data_path": {"type": "string", "description": "Path to HDF5 file with held-out real test trajectory (different from the one used for calibration)."},
                        "baseline_error": {"type": "number", "description": "Optional pre-calibration trajectory error to compare against. If omitted, only the absolute calibrated error is reported."},
                    },
                    "required": ["calibrated_params", "test_data_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "train_actuator_net",
                "description": "Train an IsaacLab ActuatorNet (LSTM neural actuator model) on real (q_target, q, q_dot, tau) pairs. Learns friction, backlash, and motor dynamics end-to-end without identifying individual physical parameters. Higher fidelity than calibrate_physics but needs more data (5-10 min of diverse motion) and more compute. Long-running headless job. Use this when the user has lots of real motor data and wants the highest-fidelity actuator model.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "real_data_path": {"type": "string", "description": "Path to HDF5 file with diverse-motion logs — q_target, q, q_dot, tau. Recommended 5-10 minutes."},
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation."},
                        "hidden_dim": {"type": "integer", "description": "LSTM hidden dimension. Default: 32."},
                        "num_layers": {"type": "integer", "description": "Number of LSTM layers. Default: 2."},
                        "num_epochs": {"type": "integer", "description": "Training epochs. Default: 200."},
                        "output_dir": {"type": "string", "description": "Directory for the trained ActuatorNet checkpoint. Default: workspace/calibration/<robot>_actuator_net"},
                    },
                    "required": ["real_data_path", "articulation_path"],
                },
            },
        },

    # ── From feat/addendum-humanoid-advanced ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "setup_contact_sensors",
                "description": "Add GPU ContactSensorCfg for one or more bodies (e.g. fingertips) on an articulation, sized for `num_envs` parallel environments. Generates per-body ContactSensorCfg entries plus a PhysxCfg block that bumps gpu_max_rigid_contact_count / gpu_max_rigid_patch_count to avoid silent buffer overflow. Use when the user asks to 'add fingertip contact sensors', 'enable touch sensing on the hand', or similar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the robot articulation, e.g. '/World/Robot' (used to scope {ENV_REGEX_NS}/Robot/<body>)"},
                        "body_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of body / link names to attach ContactSensorCfg to (one cfg per body — mandatory one-to-many constraint). E.g. ['thumb_tip', 'index_tip', 'middle_tip', 'ring_tip']",
                        },
                        "num_envs": {"type": "integer", "description": "Number of parallel environments. Used to size GPU buffers. Default: 4096"},
                        "update_period": {"type": "number", "description": "Sensor update period in seconds. 0.0 = every physics step. Default: 0.0"},
                        "history_length": {"type": "integer", "description": "Number of past frames to retain per sensor. Default: 1"},
                        "track_air_time": {"type": "boolean", "description": "Track time-since-last-contact per body. Default: false"},
                    },
                    "required": ["articulation_path", "body_names"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "setup_whole_body_control",
                "description": "Generate one-shot whole-body control config combining a locomotion RL policy (lower body) with a Pink-IK QP arm planner (upper body), wired into an ActionGroupCfg. Pre-configured profiles available for Unitree G1 (HOVER flat), Unitree H1 (HOVER rough); other robots get a generic skeleton. Use when the user asks to 'set up whole-body control', 'combine HOVER with Pink-IK', or similar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the humanoid articulation"},
                        "locomotion_policy": {"type": "string", "description": "Locomotion policy checkpoint name or identifier, e.g. 'hover_g1_flat.pt', 'hover_h1_rough.pt'"},
                        "arm_planner": {"type": "string", "enum": ["pink_ik", "lula", "rmpflow"], "description": "Upper-body arm planner. Default: 'pink_ik'"},
                        "ee_frame": {"type": "string", "description": "End-effector frame name for the arm task. Default: 'left_hand'"},
                        "robot_profile": {"type": "string", "enum": ["g1", "h1", "figure02", "generic"], "description": "Pre-configured robot profile. Default: 'generic'"},
                    },
                    "required": ["articulation_path", "locomotion_policy"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "diagnose_whole_body",
                "description": "Diagnostic check for whole-body humanoid behavior. Inspects balance margin during arm motion, CoM projection vs support polygon, arm-payload effect on locomotion policy, and EE acceleration during gait. Use when the user asks 'why does the robot fall when reaching?' or similar balance/coordination questions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the humanoid articulation"},
                        "support_polygon_margin_m": {"type": "number", "description": "Minimum acceptable distance from CoM projection to support polygon edge, in meters. Default: 0.05"},
                        "ee_accel_threshold_m_s2": {"type": "number", "description": "Maximum acceptable EE acceleration during gait, m/s^2. Default: 5.0"},
                    },
                    "required": ["articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "setup_loco_manipulation_training",
                "description": "Set up a joint locomotion + manipulation RL training run. Picks an approach (decoupled HOVER+IK / hierarchical dual-agent / joint end-to-end) and emits a reward-mixing advisor with a 3-phase weight schedule that prevents manipulation rewards from drowning out locomotion early in training. Use when the user asks to 'train the robot to walk and pick up X' or similar loco-manipulation tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_description": {"type": "string", "description": "Natural-language task, e.g. 'walk to a table and pick up a cup'"},
                        "robot": {"type": "string", "description": "Robot identifier, e.g. 'g1', 'h1', 'figure02'"},
                        "approach": {"type": "string", "enum": ["decoupled", "hierarchical", "joint"], "description": "Training approach. 'decoupled' = HOVER+IK (low complexity, slow tasks), 'hierarchical' = dual-agent (medium, dynamic), 'joint' = end-to-end (high, max performance). Default: 'decoupled'"},
                        "reward_terms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "weight": {"type": "number"},
                                    "category": {"type": "string", "enum": ["locomotion", "manipulation", "regularization"]},
                                },
                            },
                            "description": "Reward terms with names, weights, and categories. Used to advise on weight imbalances (e.g. manipulation outweighing locomotion).",
                        },
                    },
                    "required": ["task_description", "robot"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "setup_rsi_from_demos",
                "description": "Configure Reference State Initialization (RSI) for an RL env by sampling each episode's initial state from a demonstration trajectory file instead of the default pose. Highest-impact technique for loco-manipulation. Generates an InitialStateCfg with mode='demo_sampling'. Use when the user asks to 'initialize from demos', 'use RSI', or 'sample starting states from demonstrations'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "demo_path": {"type": "string", "description": "Filesystem path to the demonstration file (.npz, .hdf5, .pkl, etc.) containing recorded trajectories"},
                        "env_cfg": {"type": "string", "description": "Python class path or identifier of the env config to attach the InitialStateCfg to, e.g. 'G1WalkPickEnvCfg'"},
                        "noise_std": {"type": "number", "description": "Standard deviation of Gaussian perturbation applied around each sampled demo state. Default: 0.05"},
                    },
                    "required": ["demo_path", "env_cfg"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "setup_multi_rate",
                "description": "Generate a DualRateVecEnvWrapper that runs the upper body (manipulation IK) at a higher rate than the lower body (locomotion RL). Required when manipulation needs ~100 Hz updates while the locomotion policy was trained at ~50 Hz. The wrapper caches lower-body actions between its update ticks. Use when the user asks for 'multi-rate control', 'dual-rate wrapper', or different rates for arms vs legs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lower_rate_hz": {"type": "number", "description": "Lower-body (locomotion) update rate in Hz. Default: 50"},
                        "upper_rate_hz": {"type": "number", "description": "Upper-body (manipulation) update rate in Hz. Default: 100"},
                        "upper_dof": {"type": "integer", "description": "Number of upper-body DOFs at the front of the action vector. Default: 14 (typical humanoid two-arm count)"},
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/phase10-autonomous-workflows ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "start_workflow",
                "description": "Start a multi-step autonomous workflow with an editable plan artifact. Generates a plan first, then waits for user approval before executing. Supported workflow types: 'rl_training' (W1: env -> reward -> train -> evaluate -> deploy), 'robot_import' (W2: import -> verify -> motion plan -> report), 'sim_debugging' (W4: diagnose -> hypothesis -> fix -> verify with autonomous error-fix loop). Use when the user asks for a high-level goal like 'set up RL training for X', 'import this URDF and configure it', or 'debug why my sim is broken'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_type": {
                            "type": "string",
                            "enum": ["rl_training", "robot_import", "sim_debugging"],
                            "description": "Which workflow template to instantiate.",
                        },
                        "goal": {"type": "string", "description": "High-level user goal in natural language — e.g. 'train the Franka to pick up the cup'."},
                        "scope_prim": {"type": "string", "description": "Root prim under which the workflow operates (safety boundary). Default: '/World'."},
                        "params": {"type": "object", "description": "Workflow-specific parameters (robot path, target object, num_envs, urdf_path, etc.). Schema depends on workflow_type."},
                        "max_retries": {"type": "integer", "description": "Max autonomous error-fix retries per code-execution phase. Default: 3."},
                        "auto_approve_checkpoints": {"type": "boolean", "description": "If true, skip user-approval checkpoints (DANGEROUS — only for trusted batch runs). Default: false."},
                    },
                    "required": ["workflow_type", "goal"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "edit_workflow_plan",
                "description": "Edit a workflow's plan artifact before execution. Use during the planning checkpoint when the user wants to adjust parameters (e.g. 'change to 128 envs', 'add orientation reward', 'use a different reward shaping'). The workflow stays paused until approve_workflow_checkpoint is called.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string", "description": "ID returned by start_workflow."},
                        "plan_edits": {"type": "object", "description": "Dict of phase_name -> {field: new_value} edits, e.g. {'env_creation': {'num_envs': 128}, 'reward': {'add_terms': ['orientation']}}."},
                    },
                    "required": ["workflow_id", "plan_edits"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "approve_workflow_checkpoint",
                "description": "Approve, reject, or revise at a workflow checkpoint. Workflows pause at checkpoints (after plan generation, after reward generation, after results, before deploy) and wait for the user. 'approve' resumes execution, 'reject' cancels and rolls back, 'revise' returns to the previous phase for re-generation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string", "description": "ID returned by start_workflow."},
                        "phase": {"type": "string", "description": "Phase name being approved — e.g. 'plan', 'reward', 'results', 'deploy'."},
                        "action": {"type": "string", "enum": ["approve", "reject", "revise"], "description": "Decision at the checkpoint."},
                        "feedback": {"type": "string", "description": "Optional user feedback (passed back to the LLM if action='revise')."},
                    },
                    "required": ["workflow_id", "phase", "action"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "cancel_workflow",
                "description": "Cancel a running workflow and roll back to the pre-workflow snapshot. Use when the user says 'stop', 'cancel', 'abort', or when the workflow can no longer proceed safely.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string", "description": "ID returned by start_workflow."},
                        "reason": {"type": "string", "description": "Why the workflow is being cancelled (logged for audit)."},
                    },
                    "required": ["workflow_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_workflow_status",
                "description": "Query a workflow's current state — current phase, completed phases, pending checkpoints, error-fix attempts, and the plan artifact. Use when the user asks 'how is the workflow going?', 'what step are we on?', or to surface progress.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string", "description": "ID returned by start_workflow."},
                    },
                    "required": ["workflow_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "execute_with_retry",
                "description": "Execute a code patch through the autonomous error-fix loop (max 3 retries by default). On failure, the system reads the error, asks the LLM for a fix, and retries. Stops and reports if all retries exhausted. Use for code-generation steps where occasional API mismatches or PhysX errors are expected (URDF imports, IsaacLab env creation, OmniGraph wiring).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute via Kit RPC."},
                        "description": {"type": "string", "description": "Human-readable description of what the code does."},
                        "max_retries": {"type": "integer", "description": "Max autonomous fix attempts. Default: 3. Hard cap: 5."},
                        "context_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional hints fed to the LLM during error-fix (e.g. ['use mdp.joint_pos not joint_positions', 'IsaacLab 2.x API'])."},
                    },
                    "required": ["code", "description"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "proactive_check",
                "description": "Run the proactive agent for a given trigger. The agent observes scene state and reports issues without auto-modifying (unless AUTO_PROACTIVE_FIX env var is set). Triggers: 'scene_opened' (preflight check), 'robot_imported' (verify import + collision mesh check), 'console_error' (explain error with prim context), 'training_started' / 'training_active' (monitor entropy/reward/NaN), 'training_finished' (diagnose + eval harness), 'sim_idle' (suggest next steps), 'sim_play' (preflight before unpause), 'fps_drop' (performance diagnosis), 'target_placed' (workspace + singularity check).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trigger": {
                            "type": "string",
                            "enum": [
                                "scene_opened",
                                "robot_imported",
                                "console_error",
                                "training_started",
                                "training_active",
                                "training_finished",
                                "sim_idle",
                                "sim_play",
                                "fps_drop",
                                "target_placed",
                            ],
                            "description": "Which proactive trigger fired.",
                        },
                        "context": {"type": "object", "description": "Trigger-specific context (scene_path, robot_path, error_text, run_dir, fps, target_path, etc.)."},
                        "auto_fix": {"type": "boolean", "description": "If true AND AUTO_PROACTIVE_FIX env var is enabled, apply Tier 1 crash-preventer fixes automatically. Default: false."},
                    },
                    "required": ["trigger"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_workflows",
                "description": "List active and recently completed workflows for the current session. Returns workflow_id, type, status, current_phase, and start_time for each. Use to recover after a connection drop or when the user asks 'what workflows are running?'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_completed": {"type": "boolean", "description": "Include workflows that have finished or been cancelled. Default: false."},
                        "limit": {"type": "integer", "description": "Max workflows to return. Default: 20."},
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/addendum-collision-mesh-quality-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "check_collision_mesh",
                "description": "Analyze a USD mesh prim's collision quality. Detects fatal issues (out-of-range vertex indices, zero-area triangles, convex hull >255 polys / >64 vertices, missing CollisionAPI) and silent degradation (non-manifold edges, inverted normals, self-intersections, non-watertight, oversized triangles). Returns triangle_count, is_watertight, is_manifold, degenerate_faces, collision_approximation, issues list, and a recommendation. Use BEFORE simulating to catch bad collision meshes — the #1 cause of weird PhysX behavior.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the mesh prim to analyze, e.g. '/World/Robot/link3'"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "fix_collision_mesh",
                "description": "Auto-repair a broken collision mesh: fix normals → remove degenerate triangles → fill holes → simplify (quadric decimation) → convex decompose if needed (CoACD threshold=0.05, max_convex_hull=16) → verify hulls ≤64 vertices → write back to USD. Triangle count guidelines: dynamic rigid body 100-500 (convexDecomposition), static environment 1000-5000 (convexHull or meshSimplification), background 0 (boundingCube). Run this when check_collision_mesh reports issues.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the mesh prim to repair"},
                        "target_triangles": {"type": "integer", "description": "Target triangle count after simplification. Defaults: 500 for dynamic bodies, 2000 for static, 0 to skip simplification."},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "visualize_collision_mesh",
                "description": "Show a prim's collision mesh as a wireframe overlay in the viewport (distinct from the visual mesh). Uses omni.physx.ui Physics Debug visualization to enable Collision Shapes display. Helps users SEE what PhysX actually uses for collision vs. what's rendered.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim whose collision shape should be visualized"},
                    },
                    "required": ["prim_path"],
                },
            },
        },

    # ── From feat/addendum-community-remote-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "filter_templates_by_hardware",
                "description": "Filter scene/example templates by detected GPU capabilities. Reads template metadata (min_vram_gb, recommended_vram_gb, estimated_fps, tags) and returns only templates that fit the user's hardware. Use when the user asks 'what templates work on my GPU', 'show beginner-friendly examples for my hardware', or wants to browse compatible scene starters. Auto-detects GPU via HydraEngineStats / nvidia-smi when device_vram_gb is not supplied.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_vram_gb": {"type": "number", "description": "Override detected VRAM in GB. If omitted, auto-detect from local GPU info."},
                        "category": {"type": "string", "description": "Optional template category filter (e.g. 'manipulation', 'locomotion', 'rl', 'sdg')."},
                        "tag": {"type": "string", "description": "Optional tag filter (e.g. 'beginner_friendly', 'works_on_12gb')."},
                        "include_recommended_only": {"type": "boolean", "description": "If true, only return templates whose recommended_vram_gb fits. Default false (use min_vram_gb)."},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "export_template",
                "description": "Package the current scene (or a referenced USD path) plus its config and metadata into a portable .isaa file (zip archive with manifest.json). Enables file-based sharing of scene templates via email, GitHub or Discord — no central server required. Use when the user asks to 'export this as a template', 'share this scene', 'package this for someone else', or 'save as .isaa file'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scene_path": {"type": "string", "description": "Path to the .usd scene to export, or omit to export the currently open stage."},
                        "name": {"type": "string", "description": "Template name (used as the .isaa filename and manifest.name)."},
                        "description": {"type": "string", "description": "Human-readable description of what the template demonstrates."},
                        "min_vram_gb": {"type": "number", "description": "Optional minimum VRAM in GB to add to the hardware-tag manifest."},
                        "recommended_vram_gb": {"type": "number", "description": "Optional recommended VRAM in GB."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tag list (e.g. ['beginner_friendly', 'works_on_12gb'])."},
                        "output_dir": {"type": "string", "description": "Directory where the .isaa file is written. Default: workspace/templates/exports."},
                    },
                    "required": ["name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "import_template",
                "description": "Import a shared .isaa template file into the local template library. Validates the manifest, extracts the USD + config + assets, and registers it for use in filter_templates_by_hardware. Use when the user provides a downloaded .isaa file or asks to 'install this template'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to the .isaa file to import."},
                        "library_dir": {"type": "string", "description": "Local template library directory. Default: workspace/templates/library."},
                        "overwrite": {"type": "boolean", "description": "Overwrite an existing template with the same name. Default false."},
                    },
                    "required": ["file_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "check_vram_headroom",
                "description": "Estimate VRAM cost of an upcoming operation (cloning robots, RL training, SDG run) and compare against available GPU memory. Returns a warning + actionable suggestions if the operation is likely to OOM. ALWAYS call this before launching expensive operations like clone_prim with count >= 64, launch_training, or configure_sdg with high frame counts. Suggestions include: reduce env count, switch to headless mode, use cloud compute.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["clone", "train", "sdg", "render", "custom"], "description": "Type of operation being planned."},
                        "num_envs": {"type": "integer", "description": "Number of parallel environments / clones / frames."},
                        "complexity": {"type": "string", "enum": ["low", "medium", "high"], "description": "Per-env complexity (articulation count, sensor count, mesh density). Default: medium."},
                        "per_env_mb_override": {"type": "number", "description": "Optional explicit per-env VRAM estimate in MB. If omitted, derived from operation + complexity."},
                        "device_vram_gb": {"type": "number", "description": "Override detected VRAM in GB. If omitted, auto-detect."},
                        "currently_used_gb": {"type": "number", "description": "Current VRAM usage in GB. If omitted, auto-detect from nvidia-smi."},
                    },
                    "required": ["operation", "num_envs"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "dispatch_async_task",
                "description": "Launch a long-running operation (training, SDG, benchmarks) in a background thread/process and return a task_id immediately so the user can keep working. The task runs out-of-band and the chat panel is notified via SSE on completion. Use for operations that take more than ~30 seconds and don't need the chat to block. Pair with query_async_task for status polling.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_type": {"type": "string", "enum": ["sdg", "train", "benchmark", "render", "custom"], "description": "Type of long-running task."},
                        "params": {"type": "object", "description": "Task-specific parameter dict (e.g. {'num_frames': 500, 'output_dir': 'workspace/sdg_output/run_042'} for SDG)."},
                        "label": {"type": "string", "description": "Human-readable label shown in status messages."},
                    },
                    "required": ["task_type", "params"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "query_async_task",
                "description": "Check the status of a previously dispatched async task. Returns state ('pending' | 'running' | 'done' | 'error'), progress (0-1), elapsed seconds, and any result payload once complete. Use when the user asks 'how's that SDG run going?' or 'is the training done?'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID returned by dispatch_async_task."},
                    },
                    "required": ["task_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "visualize_forces",
                "description": "Draw colored debug arrows in the viewport showing per-joint torques on an articulation. Green = within normal range (<70% of limit), yellow = >70%, red = >90% (near saturation). Useful for visually debugging RL policies, demonstrating robot dynamics, and spotting joints near saturation. Uses omni.isaac.debug_draw.draw_lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation_path": {"type": "string", "description": "USD path to the articulation root (e.g. '/World/Franka')."},
                        "scale": {"type": "number", "description": "Arrow length scale factor (meters per Newton-meter). Default: 0.01."},
                        "update_hz": {"type": "number", "description": "Optional update frequency in Hz for continuous visualization. Default: 30."},
                    },
                    "required": ["articulation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "render_video",
                "description": "Render a video clip of the current scene using Isaac Sim's RTX renderer (NOT a screen capture — full path-traced or ray-traced output via omni.kit.capture / Movie Capture extension). Quality presets: 'preview' (RayTracing, 720p, 1 SPP, fast), 'presentation' (PathTracing, 1080p, 64 SPP, investor demo), 'production' (PathTracing, 4K, 256 SPP, marketing). Use when the user asks for a 'rendered video', 'cinematic clip', 'turntable', or 'export a demo video'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "number", "description": "Clip length in seconds."},
                        "camera": {"type": "string", "description": "USD path of the camera to render from. Default: active viewport camera."},
                        "quality": {"type": "string", "enum": ["preview", "presentation", "production"], "description": "Quality preset. Default: 'preview'."},
                        "output_path": {"type": "string", "description": "Output video path (.mp4 recommended). Default: workspace/renders/<timestamp>.mp4."},
                        "fps": {"type": "integer", "description": "Frames per second. Default: 30."},
                    },
                    "required": ["duration"],
                },
            },
        },

    # ── From feat/new-quick-demo-builder-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "quick_demo",
                "description": "Build a complete demo scene in under 2 minutes. Chains template loading, robot import, object placement, and pre-trained policy deployment.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "demo_type": {"type": "string", "enum": ["pick_place", "mobile_nav", "humanoid_walk"], "description": "Demo template type"},
                        "robot": {"type": "string", "description": "Robot model name (e.g. 'franka', 'ur5e', 'jetbot', 'g1')"},
                        "objects": {"type": "array", "items": {"type": "string"}, "description": "Objects to interact with (e.g. ['cube', 'bottle'])"},
                        "scene_style": {"type": "string", "enum": ["clean", "industrial", "lab", "dramatic"], "description": "Lighting and background preset (default 'clean')"},
                    },
                    "required": ["demo_type"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "record_demo_video",
                "description": "Record viewport to MP4 video file. Output ready for investor decks or YouTube.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration": {"type": "number", "description": "Recording duration in seconds (default 10)"},
                        "camera": {"type": "string", "description": "Camera prim path to record from (default active viewport camera)"},
                        "output_path": {"type": "string", "description": "Output MP4 file path"},
                        "resolution": {"type": "array", "items": {"type": "integer"}, "description": "Resolution [width, height] (default [1920, 1080])"},
                        "fps": {"type": "integer", "description": "Frames per second (default 30)"},
                    },
                    "required": ["output_path"],
                },
            },
        },

    # ── From feat/new-sim-to-real-gap-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "measure_sim_real_gap",
                "description": "Compare sim and real trajectories to quantify the sim-to-real gap. Returns per-joint errors, EE Cartesian error, observation distribution gap.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sim_trajectory": {"type": "string", "description": "Path to sim trajectory file (HDF5 or CSV)"},
                        "real_trajectory": {"type": "string", "description": "Path to real trajectory file (HDF5 or CSV)"},
                    },
                    "required": ["sim_trajectory", "real_trajectory"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "suggest_parameter_adjustment",
                "description": "Given a gap report, suggest which physics parameters (friction, damping, stiffness) to adjust to close the gap.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "gap_report": {"type": "object", "description": "Output from measure_sim_real_gap()"},
                    },
                    "required": ["gap_report"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "compare_sim_real_video",
                "description": "Side-by-side or overlay comparison of sim and real video using vision LLM. Identifies behavioral differences (overshoot, contact timing, etc).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sim_video_path": {"type": "string", "description": "Path to simulation video (MP4)"},
                        "real_video_path": {"type": "string", "description": "Path to real-world video (MP4)"},
                    },
                    "required": ["sim_video_path", "real_video_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "create_calibration_experiment",
                "description": "Generate a grid search over a physics parameter to find the value that minimizes sim-to-real gap.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string", "description": "Parameter to vary (e.g. 'friction', 'damping', 'stiffness')"},
                        "range": {"type": "array", "items": {"type": "number"}, "description": "[min, max] of parameter range"},
                        "num_samples": {"type": "integer", "description": "Number of grid points (default 7)"},
                        "real_data_path": {"type": "string", "description": "Real trajectory to compare against"},
                    },
                    "required": ["parameter", "range", "real_data_path"],
                },
            },
        },

    # ── From feat/addendum-phase7G-groot-tooling-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "extract_attention_maps",
                "description": "Extract cross-attention maps from GR00T's DiT for a failed action. Shows which visual patches and language tokens drove the policy. Layer 12 features per GR00T paper.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "checkpoint_path": {"type": "string", "description": "Path to GR00T checkpoint"},
                        "observation_path": {"type": "string", "description": "Path to observation dump (image + state)"},
                        "layer": {"type": "integer", "description": "ViT layer to tap (default 12)"},
                    },
                    "required": ["checkpoint_path", "observation_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "detect_ood",
                "description": "Detect out-of-distribution observations. Three tiers: action variance/autocorr (Tier 1, free), 4-sample DiT variance (Tier 2, +15ms), Mahalanobis on 12th-layer embeddings (Tier 3, requires calibration).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "integer", "minimum": 1, "maximum": 3, "description": "Detection tier: 1=cheap (action variance), 2=+15ms (DiT variance), 3=best (Mahalanobis)"},
                        "action_sequence": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "Recent action history for Tier 1"},
                        "checkpoint_path": {"type": "string", "description": "Path to GR00T checkpoint (for Tier 2/3)"},
                        "calibration_path": {"type": "string", "description": "Path to calibration stats (for Tier 3)"},
                    },
                    "required": ["tier"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "suggest_data_mix",
                "description": "Recommend sim/real/video data ratio for GR00T fine-tuning. Uses NVIDIA's validated 1:1 real:neural recipe (40% gain over real-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_type": {"type": "string", "description": "Task category (e.g. 'tabletop pick-and-place', 'mobile navigation')"},
                        "available_data": {"type": "object", "description": "Counts: {real_demos, sim_demos, video_demos}"},
                    },
                    "required": ["task_type", "available_data"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "suggest_finetune_config",
                "description": "Recommend layer freeze/tune strategy for GR00T fine-tuning based on task similarity, hardware, and data size. Avoids 'Don't Blind Your VLA' OOD loss.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_type": {"type": "string", "enum": ["similar_to_pretrain", "new_visual_domain", "new_embodiment"], "description": "Task similarity"},
                        "hardware": {"type": "string", "description": "GPU model (e.g. 'A6000', 'RTX 4090', 'RTX 4080')"},
                        "data_size": {"type": "integer", "description": "Number of training demos available"},
                    },
                    "required": ["task_type", "hardware"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "monitor_forgetting",
                "description": "Detect catastrophic forgetting during GR00T fine-tuning. Runs 30-example VQA regression suite + computes per-layer weight drift. Alerts on >20% VQA score drop.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "checkpoint_dir": {"type": "string", "description": "Directory containing checkpoint files"},
                        "base_model": {"type": "string", "description": "Path to base GR00T model for comparison"},
                    },
                    "required": ["checkpoint_dir", "base_model"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "export_policy",
                "description": "Export GR00T checkpoint to deployment format (TensorRT bf16). Targets: Jetson AGX Orin (5.8 Hz), Jetson Orin NX (~3 Hz, no FP8), x86+RTX 4090 (~15 Hz).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "checkpoint": {"type": "string", "description": "Path to .pt checkpoint"},
                        "target_device": {"type": "string", "enum": ["jetson_agx_orin", "jetson_orin_nx", "x86_rtx4090", "x86_a6000"], "description": "Deployment target"},
                        "inference_budget_ms": {"type": "number", "description": "Max inference time per step in ms"},
                    },
                    "required": ["checkpoint", "target_device"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "analyze_checkpoint",
                "description": "Analyze a GR00T checkpoint: detect embodiment, training steps, per-layer drift (vision/DiT/adapter/LM), action statistics, forgetting risk.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "checkpoint_path": {"type": "string", "description": "Path to .pt checkpoint file"},
                        "base_model_path": {"type": "string", "description": "Optional base model for drift comparison"},
                    },
                    "required": ["checkpoint_path"],
                },
            },
        },

    # ── From feat/addendum-phase5-pedagogy-uncertainty-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "create_broken_scene",
                "description": "Create a scene with a specific, diagnosable fault for students to find and fix. Educational tool for professors and self-study.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fault_type": {
                            "type": "string",
                            "enum": ["missing_collision", "zero_mass", "wrong_scale", "inverted_joint", "no_physics_scene", "inf_joint_limits"],
                            "description": "Type of intentional fault to introduce"
                        },
                        "scene_name": {"type": "string", "description": "Name for the broken scene (default 'BrokenScene')"},
                    },
                    "required": ["fault_type"],
                },
            },
        },

    # ── From feat/addendum-safety-compliance-v2 ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "enable_deterministic_mode",
                "description": "Enable deterministic simulation mode for safety validation. Fixes random seeds, sets PhysX to TGS CPU mode (deterministic for identical inputs), disables async ops, exports reproducible session archive.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "seed": {"type": "integer", "description": "Random seed for all PhysX/DR (default 42)"},
                        "physics_dt": {"type": "number", "description": "Fixed physics timestep in seconds (default 1/60)"},
                        "solver_iterations": {"type": "integer", "description": "Fixed solver iteration count (default 4)"},
                        "export_archive_path": {"type": "string", "description": "Optional: path to export reproducibility archive (.zip with scene + params + AI/physics versions)"},
                    },
                    "required": [],
                },
            },
        },

    # ── Verify-contract primitives (used to validate assistant claims) ────
    {
        "type": "function",
        "function": {
            "name": "prim_exists",
            "description": "Boolean existence check for a USD prim path. Use to verify claims like 'the robot is loaded at /World/Franka' — returns {exists, type_name, applied_schemas, child_count} when the prim is present.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prim_path": {"type": "string", "description": "USD path to check, e.g. '/World/Franka'"},
                },
                "required": ["prim_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_prims_under_path",
            "description": "Count children under a parent prim, optionally filtered by type. Use to verify 'I cloned N robots' / 'created N spheres' claims — compares what was claimed against what is actually authored in the stage. Returns {count, paths[], truncated}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {"type": "string", "description": "Parent USD path, e.g. '/World/envs'"},
                    "type_filter": {"type": "string", "description": "Optional USD type name filter, e.g. 'Xform', 'Mesh'. Omit for all types."},
                    "recursive": {"type": "boolean", "description": "If true, count all descendants (not just direct children). Default: false."},
                },
                "required": ["parent_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_joint_targets",
            "description": "Read per-joint DriveAPI targets (position/velocity/stiffness/damping) on an articulation. Use to verify 'robot will move on Play' claims — returns {joints[], joint_count, joints_with_drive}. If joints_with_drive is 0 the robot cannot move when physics starts, regardless of what other tools appeared to do.",
            "parameters": {
                "type": "object",
                "properties": {
                    "articulation_path": {"type": "string", "description": "Path to the articulation root, e.g. '/World/Franka' or '/World/carter'"},
                },
                "required": ["articulation_path"],
            },
        },
    },

    # ── From feat/atomic-tier0-foundation ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_attribute",
                "description": "Read a single attribute value from a USD prim. Verifies the result of any set_attribute call and supports general scene introspection. Returns the typed value (number, string, array, bool, or null).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim, e.g. '/World/Cube'"},
                        "attr_name": {"type": "string", "description": "Attribute name, e.g. 'radius', 'xformOp:translate', 'visibility'"},
                    },
                    "required": ["prim_path", "attr_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_world_transform",
                "description": "Compute the world-space 4x4 transform of a prim using UsdGeom.Xformable.ComputeLocalToWorldTransform. Returns the 16-element row-major matrix plus extracted translation, rotation (quaternion w,x,y,z) and scale.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                        "time_code": {"type": "number", "description": "USD time code. Default: Default (current) time."},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_bounding_box",
                "description": "Compute the world-space axis-aligned bounding box of a prim using UsdGeom.BBoxCache.ComputeWorldBound. Returns {min: [x,y,z], max: [x,y,z], center: [x,y,z], size: [dx,dy,dz]} for placement and clearance reasoning.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                        "purpose": {"type": "string", "description": "BBoxCache purpose token: 'default', 'render', or 'proxy'. Default: 'default'"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_semantic_label",
                "description": "Apply a Semantics.SemanticsAPI to a prim with the given class name. Used for synthetic data generation (SDG) annotation so Replicator writers emit semantic_segmentation / instance_segmentation / bounding_box outputs for the prim.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim to label"},
                        "class_name": {"type": "string", "description": "Semantic class name, e.g. 'cube', 'robot', 'table'"},
                        "semantic_type": {"type": "string", "description": "Semantic type token. Default: 'class'"},
                    },
                    "required": ["prim_path", "class_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_joint_limits",
                "description": "Read the joint position limits (lower, upper) for a single articulation joint. Use BEFORE issuing motion commands to avoid sending out-of-range targets. Reads UsdPhysics.RevoluteJoint or UsdPhysics.PrismaticJoint LowerLimit / UpperLimit attributes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                        "joint_name": {"type": "string", "description": "Joint name (basename, not full path), e.g. 'panda_joint1'"},
                    },
                    "required": ["articulation", "joint_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_drive_gains",
                "description": "Set the drive stiffness (kp) and damping (kd) on a UsdPhysics.DriveAPI for a joint. Tuning these gains is the primary lever for reinforcement-learning policy stability and tracking accuracy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "joint_path": {"type": "string", "description": "USD path to the joint prim, e.g. '/World/Franka/panda_link0/panda_joint1'"},
                        "kp": {"type": "number", "description": "Drive stiffness (proportional gain)"},
                        "kd": {"type": "number", "description": "Drive damping (derivative gain)"},
                        "drive_type": {"type": "string", "enum": ["angular", "linear"], "description": "Drive token type. Default: 'angular' (revolute)"},
                    },
                    "required": ["joint_path", "kp", "kd"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_contact_report",
                "description": "Read the most recent PhysX contact-report events involving a prim. Requires PhysxContactReportAPI to be applied first. Returns a list of {actor0, actor1, impulse, normal, position} entries — the building block for grasp detection and collision diagnosis.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body / collider"},
                        "max_contacts": {"type": "integer", "description": "Maximum number of contact entries to return. Default: 50"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_render_mode",
                "description": "Switch the active renderer / render mode. 'preview' uses Hydra Storm rasterizer (fast, low-quality); 'rt' uses RTX Real-Time path tracing (interactive PBR); 'path_traced' uses RTX Path-Traced (offline-quality, ground-truth for SDG).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["preview", "rt", "path_traced"], "description": "Render mode to activate"},
                    },
                    "required": ["mode"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_variant",
                "description": "Select a USD variant inside a variant set on a prim. Variant sets allow assets to expose alternative geometry, materials, or rigs (e.g. 'color', 'rig', 'lod'). This calls UsdVariantSet.SetVariantSelection.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim that owns the variant set"},
                        "variant_set": {"type": "string", "description": "Variant set name, e.g. 'color', 'rig', 'lod'"},
                        "variant": {"type": "string", "description": "Variant token within the set, e.g. 'red', 'high', 'A'"},
                    },
                    "required": ["prim_path", "variant_set", "variant"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_training_status",
                "description": "Inspect the live status of an IsaacLab RL training run. Reads the run's TensorBoard event files (latest reward / loss / step counter) and the launcher's subprocess state (running / finished / crashed). Returns {run_id, state, step, total_steps, latest_reward, log_dir}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "description": "Training run identifier returned by launch_training (typically the task name)"},
                        "log_dir": {"type": "string", "description": "Optional explicit log directory. Default: workspace/rl_checkpoints/<run_id>"},
                    },
                    "required": ["run_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "pixel_to_world",
                "description": "Project a 2D viewport pixel through a camera and the depth buffer to a 3D world-space point. Returns {world_position: [x,y,z], depth_m, ray_origin, ray_direction} so callers can implement 'click to place' or visual servoing primitives.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera": {"type": "string", "description": "USD path to the camera prim"},
                        "x": {"type": "integer", "description": "Pixel X coordinate (0 = left)"},
                        "y": {"type": "integer", "description": "Pixel Y coordinate (0 = top)"},
                        "resolution": {"type": "array", "items": {"type": "integer"}, "description": "Optional [width, height] override for the depth buffer. Default: viewport resolution."},
                    },
                    "required": ["camera", "x", "y"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "record_trajectory",
                "description": "Subscribe to PhysX step events for `duration` seconds, sample joint positions / velocities / efforts on each tick, and write the recorded trajectory to disk. Returns the output path and per-joint sample counts. Use to capture demonstrations or compare two policies.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root"},
                        "duration": {"type": "number", "description": "Recording duration in seconds"},
                        "output_path": {"type": "string", "description": "Filesystem path for the trajectory file (.npz). Default: workspace/trajectories/<timestamp>.npz"},
                        "rate_hz": {"type": "number", "description": "Sampling rate in Hz. Default: 60"},
                    },
                    "required": ["articulation", "duration"],
                },
            },
        },

    # ── From feat/atomic-tier1-usd-core ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_attributes",
                "description": "Enumerate all attributes defined on a USD prim via prim.GetAttributes(). Returns a list of {name, type, has_value, custom} entries — useful for discovering what can be read or set on a prim before calling get_attribute / set_attribute.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim, e.g. '/World/Cube'"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_relationships",
                "description": "List all relationships on a USD prim via prim.GetRelationships(). Returns each relationship name plus its current target paths — used to inspect material bindings, physics filtered pairs, skeleton bindings, etc.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_applied_schemas",
                "description": "Return the applied API schemas on a prim via prim.GetAppliedSchemas() (e.g. PhysicsRigidBodyAPI, PhysicsCollisionAPI, MaterialBindingAPI). Lets the LLM verify which APIs are present before applying / removing them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_prim_metadata",
                "description": "Read a USD metadata field on a prim via prim.GetMetadata(key). Common keys: 'kind', 'specifier', 'hidden', 'documentation', 'instanceable', 'active', 'apiSchemas'. Returns the value plus its python type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                        "key": {"type": "string", "description": "Metadata key, e.g. 'kind', 'specifier', 'hidden'"},
                    },
                    "required": ["prim_path", "key"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_prim_metadata",
                "description": "Write a USD metadata field on a prim via prim.SetMetadata(key, value). Useful for setting kind ('component', 'assembly', 'group', 'subcomponent'), hidden, instanceable, documentation, etc.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                        "key": {"type": "string", "description": "Metadata key"},
                        "value": {"description": "New metadata value (string, bool, number, list, dict)"},
                    },
                    "required": ["prim_path", "key", "value"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_prim_type",
                "description": "Return the typeName of a prim via prim.GetTypeName() — e.g. 'Mesh', 'Xform', 'Camera', 'Cube', 'DistantLight'. Returns empty string for typeless prims.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "find_prims_by_schema",
                "description": "Traverse the stage and return every prim path where prim.HasAPI(schema) is true. Use to find e.g. all rigid bodies, all articulation roots, all colliders. Schema name accepts the short USD class name (e.g. 'PhysicsRigidBodyAPI', 'PhysicsArticulationRootAPI').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "schema_name": {"type": "string", "description": "API schema class name, e.g. 'PhysicsRigidBodyAPI'"},
                        "root_path": {"type": "string", "description": "Optional sub-tree root to traverse from. Default: stage root."},
                        "limit": {"type": "integer", "description": "Max prims to return. Default: 500"},
                    },
                    "required": ["schema_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "find_prims_by_name",
                "description": "Regex search across all prim paths on the stage. Returns every prim whose path matches the supplied Python regex. Use to locate prims by partial name (e.g. '.*panda_link.*', '/World/Robots/.*Franka.*').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Python regex pattern matched against full prim paths (re.search semantics)"},
                        "root_path": {"type": "string", "description": "Optional sub-tree root to traverse from. Default: stage root."},
                        "limit": {"type": "integer", "description": "Max prims to return. Default: 500"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_kind",
                "description": "Read the Kind metadata of a prim via Usd.ModelAPI(prim).GetKind(). Returns one of 'component', 'assembly', 'group', 'subcomponent', or '' if unset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_active_state",
                "description": "Return whether a prim is active on the stage via prim.IsActive(). Inactive prims (and their descendants) are excluded from rendering, physics, and traversal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },

    # ── From feat/atomic-tier2-physics ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_linear_velocity",
                "description": "Return the current linear velocity (m/s) of a rigid body via UsdPhysics.RigidBodyAPI(prim).GetVelocityAttr().Get(). Use to verify a body is moving at the expected speed, e.g. after applying a force or starting a sim.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_angular_velocity",
                "description": "Return the current angular velocity (deg/s) of a rigid body via UsdPhysics.RigidBodyAPI(prim).GetAngularVelocityAttr().Get(). Use to inspect spin / rotation state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_linear_velocity",
                "description": "Set the linear velocity (m/s) on a rigid body via UsdPhysics.RigidBodyAPI(prim).GetVelocityAttr().Set(). Generates an approvable Python patch.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                        "vel": {"type": "array", "items": {"type": "number"}, "description": "Target linear velocity [vx, vy, vz] in m/s"},
                    },
                    "required": ["prim_path", "vel"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_mass",
                "description": "Return the current mass (kg) of a rigid body via UsdPhysics.MassAPI(prim).GetMassAttr().Get(). 0.0 means PhysX will compute mass from collision geometry + density.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_inertia",
                "description": "Return the diagonal of the inertia tensor (kg·m²) of a rigid body via UsdPhysics.MassAPI(prim).GetDiagonalInertiaAttr().Get(). Zero vector means PhysX will compute inertia from collision geometry.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_physics_scene_config",
                "description": "Read the global PhysicsScene configuration: gravity, solver type (PGS/TGS), iteration counts, time step, GPU enabled flag, broadphase. Used by the LLM to verify solver settings before tuning RL or rigid body sims.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scene_path": {"type": "string", "description": "Optional UsdPhysics.Scene path. Default: auto-detect first scene on stage."},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_physics_scene_config",
                "description": "Update the global PhysicsScene configuration (solver type, iteration counts, time step, GPU enable, broadphase, gravity). Generates an approvable Python patch using UsdPhysics.Scene + PhysxSchema.PhysxSceneAPI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "config": {
                            "type": "object",
                            "description": "Solver settings as a dict. Keys (all optional): solver_type ('PGS'|'TGS'), position_iterations (int), velocity_iterations (int), time_steps_per_second (int), enable_gpu_dynamics (bool), broadphase_type ('MBP'|'GPU'|'SAP'), gravity_direction ([x,y,z]), gravity_magnitude (number), scene_path (string).",
                        },
                    },
                    "required": ["config"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_contacts",
                "description": "List the current contact pairs for a rigid body. Applies PhysxContactReportAPI if missing, then subscribes to the contact report stream for one physics step and returns each contact pair {body_a, body_b, impulse}. Use to debug grasps or unexpected collisions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim to monitor"},
                        "duration": {"type": "number", "description": "Seconds to listen for contacts. Default: 0.5"},
                        "min_impulse": {"type": "number", "description": "Filter out contacts with impulse below this threshold (N·s). Default: 0.0"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "apply_force",
                "description": "Apply an external force and/or torque to a rigid body for one physics step using PhysX tensor API (omni.physics.tensors). Generates an approvable Python patch.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                        "force": {"type": "array", "items": {"type": "number"}, "description": "World-space force vector [fx, fy, fz] in Newtons. Default [0, 0, 0]."},
                        "torque": {"type": "array", "items": {"type": "number"}, "description": "World-space torque vector [tx, ty, tz] in N·m. Default [0, 0, 0]."},
                        "position": {"type": "array", "items": {"type": "number"}, "description": "Optional world-space application point [x, y, z]. If omitted, force is applied at the body center of mass."},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_kinematic_state",
                "description": "Return the full kinematic state (world transform position + rotation, linear velocity, angular velocity, and best-effort linear/angular acceleration via finite difference over a short window) for a rigid body. Used as a one-shot snapshot for debugging and behavior comparison.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to the rigid body prim"},
                        "sample_dt": {"type": "number", "description": "Seconds between velocity samples used to estimate acceleration. Default 0.05."},
                    },
                    "required": ["prim_path"],
                },
            },
        },

    # ── From feat/atomic-tier3-articulation ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_joint_positions",
                "description": "Return the current position of every joint in an articulation as a vector. Walks UsdPhysics.RevoluteJoint / PrismaticJoint children and reads physics:position from PhysxJointStateAPI when present, otherwise the joint's authored target. Use to verify the robot reached a commanded pose or to log demos.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                    },
                    "required": ["articulation"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_joint_velocities",
                "description": "Return the current angular/linear velocity of every joint in an articulation. Reads physics:velocity from PhysxJointStateAPI on each joint. Units: rad/s for revolute, m/s for prismatic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root"},
                    },
                    "required": ["articulation"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_joint_torques",
                "description": "Return the most recently applied actuator torque/force on every joint in an articulation. Reads PhysxJointStateAPI's appliedJointTorque (revolute) or appliedJointForce (prismatic). Use to detect saturation or unexpected torque spikes during RL rollouts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root"},
                    },
                    "required": ["articulation"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_drive_gains",
                "description": "Read the current drive stiffness (kp) and damping (kd) on a joint via UsdPhysics.DriveAPI. Returns gains for both 'angular' and 'linear' drives if applied. Use BEFORE set_drive_gains to know what you are overwriting.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "joint_path": {"type": "string", "description": "USD path to the joint prim, e.g. '/World/Franka/panda_link0/panda_joint1'"},
                        "drive_type": {"type": "string", "enum": ["angular", "linear", "auto"], "description": "Which drive token to inspect. 'auto' (default) tries both."},
                    },
                    "required": ["joint_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_joint_limits",
                "description": "Modify a joint's position range by setting physics:lowerLimit and physics:upperLimit on UsdPhysics.RevoluteJoint or PrismaticJoint. Generates an approvable Python patch. Units: degrees for revolute, meters for prismatic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "joint_path": {"type": "string", "description": "USD path to the joint prim"},
                        "lower": {"type": "number", "description": "Lower limit (deg for revolute, m for prismatic)"},
                        "upper": {"type": "number", "description": "Upper limit (deg for revolute, m for prismatic)"},
                    },
                    "required": ["joint_path", "lower", "upper"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_joint_velocity_limit",
                "description": "Cap a joint's maximum velocity by setting physxJoint:maxJointVelocity (PhysxSchema.PhysxJointAPI). Generates an approvable Python patch. Units: deg/s for revolute, m/s for prismatic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "joint_path": {"type": "string", "description": "USD path to the joint prim"},
                        "vel_limit": {"type": "number", "description": "Max joint velocity (deg/s for revolute, m/s for prismatic)"},
                    },
                    "required": ["joint_path", "vel_limit"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_articulation_mass",
                "description": "Sum the mass of every link in an articulation. Walks Usd.PrimRange under the articulation root and adds UsdPhysics.MassAPI(prim).GetMassAttr().Get() for each rigid body. Returns total kg plus a per-link breakdown.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root"},
                    },
                    "required": ["articulation"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_center_of_mass",
                "description": "Compute the world-space center-of-mass of an articulation. Mass-weighted average of each link's CoM (UsdPhysics.MassAPI center_of_mass transformed to world via ComputeLocalToWorldTransform). Returns [cx, cy, cz] plus total mass.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root"},
                    },
                    "required": ["articulation"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_gripper_state",
                "description": "Report whether a gripper is open or closed and the current grip force. Reads joint position + applied torque on the listed gripper joints, classifies vs joint limits (open/closed/midway), and returns the average commanded torque as 'force_estimate'. Use after issuing a grasp.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "articulation": {"type": "string", "description": "USD path to the articulation root, e.g. '/World/Franka'"},
                        "gripper_joints": {"type": "array", "items": {"type": "string"}, "description": "Names of the joints controlling the gripper, e.g. ['panda_finger_joint1', 'panda_finger_joint2']"},
                        "open_threshold": {"type": "number", "description": "Fraction (0..1) of joint range above which the gripper is 'open'. Default: 0.6"},
                        "closed_threshold": {"type": "number", "description": "Fraction (0..1) of joint range below which the gripper is 'closed'. Default: 0.1"},
                    },
                    "required": ["articulation", "gripper_joints"],
                },
            },
        },

    # ── From feat/atomic-tier4-geometry ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "raycast",
                "description": "Cast a single ray through the PhysX scene and return the closest hit (prim path, world position, normal, distance). Uses get_physx_scene_query_interface().raycast_closest. T4.1.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "array", "items": {"type": "number"}, "description": "Ray origin in world space [x, y, z]"},
                        "direction": {"type": "array", "items": {"type": "number"}, "description": "Ray direction (will be normalized) [dx, dy, dz]"},
                        "max_distance": {"type": "number", "description": "Maximum ray distance in meters. Default: 1000.0"},
                    },
                    "required": ["origin", "direction"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "overlap_sphere",
                "description": "Find every PhysX collider whose AABB overlaps a sphere centered at `center` with radius `radius`. Uses overlap_sphere with a report_fn callback. Returns a list of prim paths. T4.2.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "center": {"type": "array", "items": {"type": "number"}, "description": "Sphere center in world space [x, y, z]"},
                        "radius": {"type": "number", "description": "Sphere radius in meters"},
                    },
                    "required": ["center", "radius"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "overlap_box",
                "description": "Find every PhysX collider that overlaps an oriented box. Uses overlap_box with a report_fn callback. Returns a list of prim paths. T4.3.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "center": {"type": "array", "items": {"type": "number"}, "description": "Box center in world space [x, y, z]"},
                        "half_extents": {"type": "array", "items": {"type": "number"}, "description": "Half-extents along local box axes [hx, hy, hz]"},
                        "rotation": {"type": "array", "items": {"type": "number"}, "description": "Box orientation as quaternion [qx, qy, qz, qw]. Default: identity."},
                    },
                    "required": ["center", "half_extents"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "sweep_sphere",
                "description": "Sweep a sphere from `start` to `end` and return the closest hit along the sweep (prim path, hit position, normal, distance). Uses sweep_sphere on the PhysX scene query interface. T4.4.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "array", "items": {"type": "number"}, "description": "Sweep start position in world space [x, y, z]"},
                        "end": {"type": "array", "items": {"type": "number"}, "description": "Sweep end position in world space [x, y, z]"},
                        "radius": {"type": "number", "description": "Sphere radius in meters"},
                    },
                    "required": ["start", "end", "radius"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "compute_volume",
                "description": "Compute the signed volume of a mesh prim by summing the signed volumes of tetrahedra formed by every triangle and the world origin. Uses trimesh if installed, otherwise falls back to a manual divergence-theorem implementation. Returns volume in cubic meters. T4.5.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to a Mesh prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "compute_surface_area",
                "description": "Compute the surface area of a mesh prim by summing the areas of every triangle (after triangulating any non-triangle faces). Returns area in square meters. T4.6.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to a Mesh prim"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "compute_convex_hull",
                "description": "Apply UsdPhysics.MeshCollisionAPI with approximation='convexHull' to the prim. Optionally export the hull mesh to a sibling Mesh prim under a chosen path so the result is visible in the viewport. T4.7.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to a Mesh prim that should receive a convex-hull collision approximation"},
                        "export_hull_path": {"type": "string", "description": "Optional USD path for an exported convex-hull Mesh prim. If provided, the convex hull is computed via scipy.spatial.ConvexHull (with a manual gift-wrap fallback) and authored at this path so it can be inspected in the viewport."},
                    },
                    "required": ["prim_path"],
                },
            },
        },

    # ── From feat/atomic-tier5-omnigraph ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_graphs",
                "description": "Enumerate all OmniGraph action graphs in the current USD stage. Returns a list of graph prim paths and basic metadata. Use to discover existing graphs before inspecting or modifying them.",
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
                "name": "inspect_graph",
                "description": "Inspect an OmniGraph: return its nodes, connections, and node attribute values. Uses og.Controller to enumerate the graph contents. Use after list_graphs to understand a graph's structure before editing.",
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
                "name": "add_node",
                "description": "Add a single node to an existing OmniGraph using og.Controller.edit() with CREATE_NODES. Atomic alternative to rebuilding the whole graph with create_omnigraph.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "USD path to the existing OmniGraph"},
                        "node_type": {"type": "string", "description": "OmniGraph node type ID, e.g. 'omni.graph.action.OnPlaybackTick' or 'isaacsim.ros2.bridge.ROS2PublishClock'"},
                        "name": {"type": "string", "description": "Friendly name for the new node within the graph"},
                    },
                    "required": ["graph_path", "node_type", "name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "connect_nodes",
                "description": "Wire one node's output port to another node's input port within an OmniGraph. Uses og.Controller.edit() with CONNECT. Source/destination paths look like 'NodeName.outputs:portName' and 'NodeName.inputs:portName'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "USD path to the OmniGraph"},
                        "src": {"type": "string", "description": "Source attribute path — e.g. 'tick.outputs:tick'"},
                        "dst": {"type": "string", "description": "Destination attribute path — e.g. 'publishClock.inputs:execIn'"},
                    },
                    "required": ["graph_path", "src", "dst"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_graph_variable",
                "description": "Set a graph-scoped variable on an OmniGraph via og.Controller. Variables persist across node evaluations and can be read by GetVariable nodes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "USD path to the OmniGraph"},
                        "name": {"type": "string", "description": "Variable name (e.g. 'topicName')"},
                        "value": {"description": "New value (string, number, bool, or array)"},
                    },
                    "required": ["graph_path", "name", "value"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "delete_node",
                "description": "Remove a single node from an OmniGraph using og.Controller.edit() with DELETE_NODES. Use to surgically prune nodes without rebuilding the entire graph.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "USD path to the OmniGraph"},
                        "node_name": {"type": "string", "description": "Name of the node to remove (relative to the graph)"},
                    },
                    "required": ["graph_path", "node_name"],
                },
            },
        },

    # ── From feat/atomic-tier6-lighting ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_lights",
                "description": (
                    "Enumerate every USD light prim in the current stage (DistantLight, DomeLight, SphereLight, "
                    "RectLight, DiskLight, CylinderLight). "
                    "Returns JSON with shape {'lights': [{'path': str, 'type': str, 'intensity': float, "
                    "'color': [r,g,b], 'enabled': bool}, ...], 'count': int, 'has_dome': bool}. "
                    "'type' is one of 'DistantLight'|'DomeLight'|'SphereLight'|'RectLight'|'DiskLight'|'CylinderLight'. "
                    "'intensity' is the raw USD inputs:intensity attribute (nits-equivalent, not lux). "
                    "'color' is the linear RGB triple from inputs:color (each channel 0.0–1.0). "
                    "'has_dome' is true if at least one DomeLight exists (useful to decide whether to create_hdri_skydome). "
                    "Use for: auditing lighting before a render ('how many lights does the scene have?'), "
                    "finding the dome-light path to swap an HDRI, checking whether a scene is dark because "
                    "all lights are disabled. "
                    "Limitations: does not report light visibility flags other than inputs:enabled; does not "
                    "distinguish UsdLux vs PxrLight (both appear by their USD type name); empty stages return count=0."
                ),
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
                "name": "get_light_properties",
                "description": (
                    "Read the full attribute set of a single light prim: type, intensity, color, exposure, "
                    "and type-specific parameters (angle for DistantLight, radius for SphereLight, "
                    "width/height for RectLight, texture for DomeLight). "
                    "Returns JSON with shape {'path': str, 'type': str, 'intensity': float, 'exposure': float, "
                    "'color': [r,g,b], 'enabled': bool, 'angle': float|null, 'radius': float|null, "
                    "'width': float|null, 'height': float|null, 'texture_file': str|null, 'color_temperature': float|null}. "
                    "'angle' is the DistantLight cone angle in degrees (sun ~0.53). "
                    "'texture_file' is the HDRI path for a DomeLight (empty string if unset). "
                    "Use for: inspecting a light before editing it ('what is the current sun intensity?'), "
                    "copying settings from one light to another, debugging a too-dark/too-bright scene. "
                    "Limitations: unknown attributes return null; the prim MUST exist and MUST have UsdLux.LightAPI "
                    "applied — otherwise an error dict is returned. Intensity is in USD's raw 'nits-like' units; "
                    "there is no automatic lux/lumens conversion."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "light_path": {
                            "type": "string",
                            "description": (
                                "Absolute USD path to the light prim, e.g. '/World/SunLight' or '/Environment/DomeLight'. "
                                "Must be a prim with UsdLux LightAPI applied (DistantLight, DomeLight, SphereLight, "
                                "RectLight, DiskLight, or CylinderLight)."
                            ),
                        },
                    },
                    "required": ["light_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_light_intensity",
                "description": (
                    "Set the intensity of an existing light prim by writing the USD inputs:intensity attribute. "
                    "Returns an approval-pending code patch (no direct scene mutation). "
                    "UNITS: Omniverse/UsdLux intensity is NOT lux. It is a dimensionless scalar the path-tracer "
                    "multiplies by the light's emissive term — think of it as 'nits × area' for area lights, and "
                    "a perpendicular-irradiance-equivalent for DistantLight. Typical values: "
                    "  • DistantLight (sun) — 3000–10000 for bright daylight "
                    "  • DomeLight (sky HDRI) — 1000–2500 for balanced ambient "
                    "  • SphereLight / RectLight (indoor) — 500–3000 "
                    "  • Practical 'indoor lamp' ≈ 1500; 'overcast outdoor' ≈ 8000. "
                    "Use for: brightening a scene that renders too dark, dimming a studio key light, "
                    "matching real-world lux targets approximately (no exact conversion), flickering via "
                    "repeated calls with different values. "
                    "Limitations: does NOT convert lux/lumens/candela automatically; does not change exposure "
                    "(use set_attribute on inputs:exposure for stops-based control); light must already exist "
                    "(use create_prim of type DistantLight/DomeLight/SphereLight first)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "light_path": {
                            "type": "string",
                            "description": (
                                "Absolute USD path to an existing light prim, e.g. '/World/KeyLight'. "
                                "Must already exist — this tool only mutates, it does not create."
                            ),
                        },
                        "intensity": {
                            "type": "number",
                            "description": (
                                "New intensity scalar (>= 0). Typical range 0–20000. "
                                "Examples: 0 = off, 500 = dim indoor lamp, 1500 = normal indoor, "
                                "3000–5000 = bright indoor / overcast sun, 10000 = strong direct sun. "
                                "Values above ~50000 tend to burn out the tonemapper without adjusting exposure."
                            ),
                        },
                    },
                    "required": ["light_path", "intensity"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_light_color",
                "description": (
                    "Set the RGB color of an existing light prim by writing the USD inputs:color attribute "
                    "(linear color space, per-channel 0.0–1.0). Returns an approval-pending code patch. "
                    "COLOR SPACE: values are LINEAR RGB, not sRGB — [1,1,1] is pure white, [1,0.5,0.2] is a "
                    "warm tungsten hue, [0.4,0.6,1.0] is a cool sky hue. Intensity is multiplied on top of color, "
                    "so to change ONLY the hue, keep the brightest channel at 1.0 and use set_light_intensity "
                    "for level. For physically based color temperature, prefer setting inputs:colorTemperature "
                    "via set_attribute (Kelvin) and setting color to [1,1,1]. "
                    "Typical presets: "
                    "  • tungsten 2700K ≈ [1.0, 0.56, 0.20] "
                    "  • halogen 3200K ≈ [1.0, 0.70, 0.40] "
                    "  • daylight 5500K ≈ [1.0, 0.94, 0.88] "
                    "  • overcast 7500K ≈ [0.80, 0.87, 1.00] "
                    "  • pure white        [1.0, 1.0, 1.0]. "
                    "Use for: simulating sunrise/sunset (warm red sun), matching showroom lighting "
                    "(5000K neutral), mood lighting (tinted key light), signalling state (red alarm light). "
                    "Limitations: channels are CLAMPED to >= 0 but values > 1 are allowed and act as an "
                    "additional brightness multiplier (non-physical). Light must already exist."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "light_path": {
                            "type": "string",
                            "description": (
                                "Absolute USD path to an existing light prim, e.g. '/World/RedAlarmLight'. "
                                "Must already exist."
                            ),
                        },
                        "rgb": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "Linear RGB triple [r, g, b], each channel in 0.0–1.0 (values >1 act as a "
                                "brightness boost, not recommended — use intensity instead). "
                                "Examples: [1,1,1] white, [1,0.56,0.2] warm tungsten, [0.4,0.6,1] cool sky."
                            ),
                        },
                    },
                    "required": ["light_path", "rgb"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "add_default_light",
                "description": "Add a plain UsdLux.DomeLight to illuminate the scene so the viewport isn't black. Use this for any new scene / industrial cell / pick-and-place demo that does not already have a light authored — Isaac Sim does NOT auto-add one. Minimal: no HDRI texture, no background environment — just enough ambient light for the geometry to render. For textured environment/photorealistic lighting use create_hdri_skydome instead.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "light_path": {"type": "string", "description": "USD path for the DomeLight. Default: '/World/DomeLight'."},
                        "intensity": {"type": "number", "description": "Light intensity. Default: 1000. Scale up for very large scenes, down for interiors."},
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "create_hdri_skydome",
                "description": (
                    "Create (or replace) a DomeLight prim with an HDRI environment texture — the standard way "
                    "to add photorealistic image-based lighting + background to a scene. Returns an "
                    "approval-pending code patch. Internally: defines a UsdLux.DomeLight, sets inputs:texture:file "
                    "to the HDRI path, configures latlong format, and sets a sensible default intensity (1000). "
                    "The HDRI lights the scene AND acts as the visible background (unless hidden via "
                    "primvars:arnold:camera = 0 or similar). "
                    "Supported formats: .hdr, .exr, .ktx, .dds (Omniverse prefers .exr for HDR range, "
                    ".hdr for legacy HDRIs). "
                    "Use for: one-shot realistic outdoor lighting ('make it look like a sunny day'), "
                    "studio turntable renders (gray studio HDRI), quickly replacing the default dim ambient "
                    "with any HDRI from Poly Haven / HDRI-Haven. "
                    "Limitations: overwrites any existing prim at /Environment/DomeLight (idempotent). "
                    "HDRI intensity must often be tuned afterwards via set_light_intensity (typical range "
                    "500–3000). The HDRI file must be accessible to Kit — local paths work; Nucleus paths "
                    "(omniverse://) work if the asset has been uploaded. Relative paths resolve against the "
                    "current stage. Does NOT change the scene's linear workflow / tonemapper — pair with "
                    "set_render_config if you see washed-out colors."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hdri_path": {
                            "type": "string",
                            "description": (
                                "Absolute or Nucleus URL to the HDRI file. Examples: "
                                "'/home/user/hdris/kloofendal_48d_partly_cloudy_4k.exr', "
                                "'omniverse://localhost/NVIDIA/Assets/Skies/Dynamic/cumulus_sky.hdr', "
                                "'./textures/studio.exr' (relative to stage). "
                                "Accepts .hdr, .exr, .ktx, .dds. Non-HDR formats (.png/.jpg) are allowed but "
                                "will not produce true HDR lighting."
                            ),
                        },
                        "dome_path": {
                            "type": "string",
                            "description": (
                                "Optional USD path for the DomeLight prim. Default: '/Environment/DomeLight'. "
                                "Override only if you already have a convention like '/World/Lighting/Sky'."
                            ),
                        },
                        "intensity": {
                            "type": "number",
                            "description": (
                                "Optional initial intensity (default 1000). Typical 500–3000 for HDRIs. "
                                "Overexposed HDRIs (sun-in-frame EXRs) may need 200–500; dim interior "
                                "HDRIs may need 3000–8000."
                            ),
                        },
                    },
                    "required": ["hdri_path"],
                },
            },
        },

    # ── From feat/atomic-tier7-camera ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_cameras",
                "description": (
                    "List all UsdGeom.Camera prims on the stage with their type "
                    "(perspective vs orthographic) and basic identification. "
                    "Returns: {cameras: [{path: str, name: str, projection: 'perspective'|'orthographic', "
                    "purpose: str, kind: str}], count: int}. Use for: discovering what cameras "
                    "exist before switching viewport, picking a camera to render from, building a "
                    "camera selector UI. NOTE: returns only Camera-typed prims; does not include "
                    "the default viewport perspective when no camera is created. Empty list is a valid result."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_camera_params",
                "description": (
                    "Read all cinematographic attributes of a UsdGeom.Camera prim — focal length, "
                    "horizontal/vertical aperture, clipping range, focus distance, f-stop, projection — "
                    "and derive horizontal/vertical field-of-view from focal+aperture. "
                    "Returns: {camera_path: str, projection: 'perspective'|'orthographic', "
                    "focal_length_mm: float, horizontal_aperture_mm: float, vertical_aperture_mm: float, "
                    "horizontal_fov_deg: float, vertical_fov_deg: float, clipping_range_m: [near, far], "
                    "focus_distance_m: float, f_stop: float}. Use for: inspecting a camera before tweaking, "
                    "verifying lens choice matches a real-world reference, computing what is in/out of frame. "
                    "NOTE: focal/aperture are USD-native millimetres; clipping & focus distance are in scene "
                    "units (metres by default). FoV is computed as 2*atan(aperture / (2*focal_length))."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_path": {
                            "type": "string",
                            "description": "USD path to the Camera prim, e.g. '/World/Camera' or '/World/MyRig/Cam01'",
                        },
                    },
                    "required": ["camera_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_camera_params",
                "description": (
                    "Modify camera attributes (focal length, horizontal/vertical aperture, clipping range, "
                    "focus distance, f-stop, projection). Returns approval-pending code patch. "
                    "Use for: matching real-world lens specs (35mm focal=standard, 14mm=wide, 200mm=telephoto), "
                    "tweaking depth of field for cinematic shots, fixing near/far plane clipping issues, "
                    "switching between perspective and orthographic projection. "
                    "NOTE: focal_length and apertures are in mm (USD convention); clipping_range and "
                    "focus_distance are in scene units (metres by default). Setting only some fields "
                    "leaves the others unchanged. clipping_range must satisfy near < far and both > 0."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_path": {
                            "type": "string",
                            "description": "USD path to the Camera prim, e.g. '/World/Camera'",
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "Dict of camera attributes to modify. Recognised keys: "
                                "'focal_length' (mm, e.g. 35.0), "
                                "'horizontal_aperture' (mm, e.g. 20.955 for 35mm full-frame), "
                                "'vertical_aperture' (mm, e.g. 15.2908), "
                                "'clipping_range' ([near, far] in scene units, e.g. [0.1, 1000.0]), "
                                "'focus_distance' (scene units, e.g. 2.5), "
                                "'f_stop' (e.g. 2.8 — used for DoF when render supports it), "
                                "'projection' ('perspective' or 'orthographic')."
                            ),
                            "properties": {
                                "focal_length": {"type": "number", "description": "Focal length in mm"},
                                "horizontal_aperture": {"type": "number", "description": "Horizontal sensor aperture in mm"},
                                "vertical_aperture": {"type": "number", "description": "Vertical sensor aperture in mm"},
                                "clipping_range": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "description": "[near, far] clip planes in scene units",
                                },
                                "focus_distance": {"type": "number", "description": "Focus distance in scene units"},
                                "f_stop": {"type": "number", "description": "Aperture f-stop value (e.g. 2.8, 5.6, 11)"},
                                "projection": {
                                    "type": "string",
                                    "enum": ["perspective", "orthographic"],
                                    "description": "Projection mode",
                                },
                            },
                        },
                    },
                    "required": ["camera_path", "params"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "capture_camera_image",
                "description": (
                    "Render a single frame from a SPECIFIC camera prim (not the active viewport) at a "
                    "user-specified resolution and return the result as a base64-encoded PNG. Internally "
                    "creates a Replicator render product bound to the camera, captures one frame, and "
                    "tears the render product down. "
                    "Returns: {camera_path: str, resolution: [width, height], image_base64: str, "
                    "format: 'png', message: str}. Use for: taking a snapshot from a security camera, "
                    "comparing what each camera sees, generating thumbnails for a multi-camera UI, "
                    "validating sensor placement. NOTE: rendering is GPU-bound and may take 100ms–2s "
                    "depending on resolution and renderer (RTX path tracing is slower than RTX real-time). "
                    "Resolution defaults to [1280, 720] if omitted; max recommended is [3840, 2160]."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_path": {
                            "type": "string",
                            "description": "USD path to the Camera prim to render from, e.g. '/World/SecurityCam'",
                        },
                        "resolution": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Output [width, height] in pixels. Default: [1280, 720]",
                        },
                    },
                    "required": ["camera_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_camera_look_at",
                "description": (
                    "Orient a camera so it points at a given world-space target position, computing the "
                    "required rotation from the camera's current world position. The camera's translation "
                    "is preserved unless 'eye' is supplied. Returns approval-pending code patch. "
                    "Use for: framing a robot or asset for a cinematic shot, aiming a security/inspection "
                    "camera at a region of interest, snapping a debug camera to look at a clicked point in "
                    "the scene. NOTE: target is in world-space scene units (metres by default). The "
                    "computed rotation uses USD's standard look-at convention (camera's -Z axis points at "
                    "target, +Y is up). Optional 'up' vector defaults to world +Y; pass [0,0,1] for Z-up "
                    "scenes. Optional 'eye' overrides the camera's current position."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "camera_path": {
                            "type": "string",
                            "description": "USD path to the Camera prim to orient, e.g. '/World/Camera'",
                        },
                        "target": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "World-space [x, y, z] position to look at, in scene units (metres by default)",
                        },
                        "up": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Optional world up-vector [x, y, z]. Default: [0, 1, 0] (Y-up). Use [0, 0, 1] for Z-up scenes.",
                        },
                        "eye": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Optional override of camera position. If omitted, the camera's current world translation is used.",
                        },
                    },
                    "required": ["camera_path", "target"],
                },
            },
        },

    # ── From feat/atomic-tier8-render ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_render_config",
                "description": (
                    "WHAT: Read the current render configuration — active renderer (RaytracedLighting / "
                    "PathTracing / RealTime), samples-per-pixel (SPP), max bounces, and viewport "
                    "resolution — by inspecting /Render/Vars/* and the active hydra engine on the stage. "
                    "WHEN: before switching from preview to final-quality rendering, when verifying SDG "
                    "output settings, when debugging noisy or slow renders, or when reporting the current "
                    "render state to the user. "
                    "RETURNS: data dict {renderer: 'RaytracedLighting'|'PathTracing'|'RealTime', "
                    "samples_per_pixel: int, max_bounces: int, resolution: [width, height], "
                    "post_process: {bloom: bool, tonemap: str, dof: bool, motion_blur: bool}}. "
                    "UNITS: SPP is dimensionless (typical 1-128), resolution in pixels. "
                    "CAVEATS: read-only — does NOT change anything. PathTracing samples are per-pixel "
                    "per-frame and accumulate over time; SPP=1 in PT means 1 sample per progressive "
                    "iteration. Returns null fields when running headless without a viewport."
                ),
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
                "name": "set_render_config",
                "description": (
                    "WHAT: Switch the renderer mode and adjust quality settings (SPP, max bounces). "
                    "Generates a code patch that updates the active hydra delegate via "
                    "omni.kit.viewport.utility and writes /Render/Vars/* attributes. "
                    "WHEN: switching from preview (fast RaytracedLighting) to final-quality "
                    "(PathTracing for marketing renders or SDG ground-truth), tweaking SPP for SDG "
                    "output, or lowering quality for interactive iteration. "
                    "RETURNS: code patch for approval (queued via Kit RPC /exec). "
                    "UNITS: samples_per_pixel dimensionless (1-1024 typical), max_bounces dimensionless "
                    "(1-16 typical). "
                    "CAVEATS: PathTracing is roughly 10x slower than RaytracedLighting per frame but "
                    "physically correct for caustics/GI. For RL training use 'RealTime' or "
                    "'RaytracedLighting' (PT is too slow). Higher SPP = less noise but proportionally "
                    "slower; double SPP halves noise. Switching renderers may reset accumulated samples."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "renderer": {
                            "type": "string",
                            "enum": ["RaytracedLighting", "PathTracing", "RealTime"],
                            "description": (
                                "Renderer engine. 'RaytracedLighting' = balanced quality+speed (default "
                                "for preview), 'PathTracing' = physically-correct GI/caustics (final "
                                "quality, ~10x slower), 'RealTime' = rasterized (fastest, no RT GI; "
                                "good for RL training). Example: 'PathTracing'."
                            ),
                        },
                        "samples_per_pixel": {
                            "type": "integer",
                            "description": (
                                "Samples per pixel (SPP). Higher = less noise, slower. Typical: "
                                "RaytracedLighting 1-4, PathTracing 32-128 for previews and "
                                "256-1024 for final SDG. Example: 64."
                            ),
                        },
                        "max_bounces": {
                            "type": "integer",
                            "description": (
                                "Maximum light bounces for indirect illumination. PathTracing only. "
                                "Typical: 4-8. Higher = more accurate caustics/GI but slower. "
                                "Example: 6."
                            ),
                        },
                    },
                    "required": ["renderer"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_render_resolution",
                "description": (
                    "WHAT: Set the viewport / render-product resolution by writing to the active "
                    "viewport API (omni.kit.viewport.utility.get_active_viewport().resolution = (w, h)). "
                    "WHEN: configuring SDG output resolution before a Replicator run, switching to a "
                    "specific aspect ratio for marketing renders, downscaling for fast iteration, or "
                    "matching a real-world camera sensor's pixel grid for sim-to-real validation. "
                    "RETURNS: code patch for approval. "
                    "UNITS: width and height in pixels (integers). "
                    "CAVEATS: very high resolutions (>4096) cost VRAM proportionally and may OOM on "
                    "consumer GPUs. Resolution affects SPP cost linearly (4K = 4x as many rays as "
                    "1080p). For SDG, set this BEFORE creating the render_product; changing later "
                    "requires recreating the product. Aspect ratio is implicit from width/height — "
                    "if camera FoV stays fixed, the framing changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "width": {
                            "type": "integer",
                            "description": (
                                "Render width in pixels. Common: 1280 (720p), 1920 (1080p), 2560 (1440p), "
                                "3840 (4K). Example: 1920."
                            ),
                        },
                        "height": {
                            "type": "integer",
                            "description": (
                                "Render height in pixels. Common: 720, 1080, 1440, 2160. Example: 1080."
                            ),
                        },
                    },
                    "required": ["width", "height"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "enable_post_process",
                "description": (
                    "WHAT: Enable or configure a post-processing effect on the active viewport — bloom, "
                    "tonemap, depth-of-field (DoF), or motion blur — by toggling the corresponding "
                    "/Render/PostProcess/* attributes on the render settings prim. "
                    "WHEN: matching a cinematic look (bloom + filmic tonemap), simulating real-camera "
                    "DoF for sim-to-real perception training, adding motion blur for fast-moving "
                    "objects in SDG output, or disabling effects to get clean ground-truth frames. "
                    "RETURNS: code patch for approval. "
                    "UNITS: intensity 0.0-1.0 (dimensionless), focus_distance in scene units (meters), "
                    "f_stop dimensionless (typical 1.4-22), shutter_speed in seconds (typical 1/60). "
                    "CAVEATS: post-process effects are baked into RGB output and CANNOT be removed "
                    "later — for clean SDG ground truth disable bloom/DoF/motion_blur. Tonemap affects "
                    "the perceived brightness range; 'aces' is film-standard, 'reinhard' is simpler. "
                    "DoF requires a Camera prim with focusDistance/fStop attributes set. Motion blur "
                    "requires multi-frame sample accumulation (slower)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "effect": {
                            "type": "string",
                            "enum": ["bloom", "tonemap", "dof", "motion_blur"],
                            "description": (
                                "Post-process effect to enable. 'bloom' = light-bleed glow around bright "
                                "pixels, 'tonemap' = HDR→LDR mapping curve, 'dof' = depth-of-field blur, "
                                "'motion_blur' = temporal blur on moving objects. Example: 'bloom'."
                            ),
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "Effect-specific parameters. bloom: {intensity: 0.0-1.0, threshold: float}. "
                                "tonemap: {operator: 'aces'|'reinhard'|'linear', exposure: float in EV stops}. "
                                "dof: {focus_distance: meters, f_stop: float}. "
                                "motion_blur: {shutter_speed: seconds, samples: int}. "
                                "Example for bloom: {\"intensity\": 0.5, \"threshold\": 1.0}."
                            ),
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": (
                                "True to enable the effect, False to disable. Default: True. "
                                "Example: True."
                            ),
                        },
                    },
                    "required": ["effect"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_environment_background",
                "description": (
                    "WHAT: Set the scene's environment background — either an HDRI dome light texture "
                    "(latlong .hdr/.exr) or a solid color clear background — by creating/updating a "
                    "DomeLight prim at /World/EnvironmentLight or writing /Render/Vars/clearColor. "
                    "WHEN: matching real-world lighting from a HDRI capture for sim-to-real, setting "
                    "a neutral grey backdrop for SDG bounding-box training (avoids background bias), "
                    "switching to a sunset/studio HDRI for marketing renders, or using a flat color "
                    "for fast preview iteration. "
                    "RETURNS: code patch for approval. "
                    "UNITS: color is RGB triplet 0.0-1.0 (linear), HDRI intensity in nits (0.0-10000+), "
                    "HDRI rotation in degrees (0-360). "
                    "CAVEATS: HDRI dome lights provide image-based lighting (IBL) — they affect ALL "
                    "scene illumination, not just the background pixels. Solid colors do NOT contribute "
                    "to lighting; you must add explicit lights. Latlong .hdr/.exr files are required "
                    "(cubemaps unsupported by USD DomeLight). Very high intensity (>1000) can wash out "
                    "shadows. For SDG ground truth, prefer solid color OR a fixed HDRI to keep "
                    "lighting reproducible across frames."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hdri_path": {
                            "type": "string",
                            "description": (
                                "Path or Nucleus URL to a latlong .hdr or .exr HDRI texture. "
                                "Mutually exclusive with 'color'. Example: "
                                "'omniverse://localhost/NVIDIA/Assets/Skies/2k/kloppenheim_06_2k.hdr'."
                            ),
                        },
                        "color": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "Solid background color [r, g, b] in linear 0.0-1.0. Mutually exclusive "
                                "with 'hdri_path'. Example: [0.2, 0.2, 0.2] for neutral grey."
                            ),
                        },
                        "intensity": {
                            "type": "number",
                            "description": (
                                "HDRI dome light intensity multiplier in nits. Default: 1000. Typical: "
                                "500-3000. Example: 1500. Ignored when 'color' is set."
                            ),
                        },
                        "rotation_deg": {
                            "type": "number",
                            "description": (
                                "HDRI rotation around the up-axis in degrees, 0-360. Useful for aiming "
                                "the sun direction. Default: 0. Example: 90. Ignored when 'color' is set."
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/atomic-tier9-layers ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_layers",
                "description": (
                    "WHAT: List every layer participating in the current USD stage's layer stack — "
                    "the root layer, every sublayer attached to it, and every session/anonymous "
                    "layer — together with which one is the current edit target. Walks "
                    "stage.GetLayerStack() and stage.GetEditTarget() and returns identifier, "
                    "display name, anonymous/dirty flags, and stack depth for each. "
                    "WHEN: before adding a sublayer (so the LLM knows what's already wired in), "
                    "before set_edit_target (to confirm the layer exists and is mutable), when "
                    "the user asks 'what layers are loaded' / 'where do my edits go' / 'show me "
                    "the layer stack', or before flatten_layers to preview what will be baked. "
                    "RETURNS: data dict {root_layer: str, edit_target: str, layers: [{identifier, "
                    "display_name, anonymous: bool, dirty: bool, depth: int, is_edit_target: bool}], "
                    "count: int}. "
                    "UNITS: identifier is an .usd/.usda/.usdc filesystem path or anon:0xADDR for "
                    "anonymous (session) layers; depth=0 is the root, deeper means weaker opinion. "
                    "CAVEATS: read-only — no stage mutation. Anonymous layers (depth>0) are session-"
                    "only and lost on stage close. The strongest opinion wins, so layers earlier in "
                    "the list override later ones at the same prim path. Returns an error stub if "
                    "no stage is open."
                ),
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
                "name": "add_sublayer",
                "description": (
                    "WHAT: Attach a new sublayer to the root layer of the current stage. Generates "
                    "a code patch that calls stage.GetRootLayer().subLayerPaths.insert(0, layer_path) "
                    "and (if the file does not yet exist) creates an empty .usda layer at the given "
                    "path via Sdf.Layer.CreateNew. The new sublayer becomes the strongest sublayer "
                    "below the root. "
                    "WHEN: separating user overrides from a base scene (e.g. attach 'overrides.usda' "
                    "above a referenced robot.usd), composing modular shot files, layering a "
                    "lighting/look pass on top of geometry, or wiring in a Replicator output layer "
                    "before SDG. Pair with set_edit_target if subsequent edits should land in the "
                    "new sublayer. "
                    "RETURNS: code patch for approval (queued via Kit RPC /exec). "
                    "UNITS: layer_path is a filesystem path or omniverse:// URL. "
                    "CAVEATS: insertion position 0 = strongest sublayer (overrides everything below). "
                    "If the file already exists it is referenced as-is — its contents will start "
                    "overriding the stage immediately. Sublayer composition is destructive at "
                    "compose time: a delete in a stronger sublayer can't be 'undone' by a weaker "
                    "one. Anonymous (in-memory) sublayers must be saved with .Export() before stage "
                    "close or they are lost."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "layer_path": {
                            "type": "string",
                            "description": (
                                "Filesystem path or Nucleus URL of the sublayer to attach. Created "
                                "as an empty .usda if it does not exist. Example: "
                                "'/tmp/overrides.usda' or 'omniverse://localhost/projects/shot01/lighting.usda'."
                            ),
                        },
                    },
                    "required": ["layer_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_edit_target",
                "description": (
                    "WHAT: Change which USD layer receives subsequent edits by calling "
                    "stage.SetEditTarget(Usd.EditTarget(Sdf.Layer.FindOrOpen(layer_path))). All "
                    "later prim creates / attribute writes / deletes go into this layer instead of "
                    "the root. "
                    "WHEN: separating user-overrides from a base scene, working in a session-only "
                    "layer that won't be saved to disk, applying edits to a specific sublayer "
                    "(e.g. a lighting pass), or recording Replicator randomisations into a dedicated "
                    "output layer. "
                    "RETURNS: code patch for approval. "
                    "UNITS: layer_path is a filesystem path / Nucleus URL / anonymous-layer "
                    "identifier (anon:0x...). "
                    "CAVEATS: edits to anonymous (session) layers are LOST on stage close — call "
                    "Sdf.Layer.Export to persist them. The target layer must already be in the "
                    "layer stack (use list_layers() first to see what's available; use add_sublayer() "
                    "to attach new ones). Switching edit targets does NOT change which opinions are "
                    "active — only where new opinions are written. Edits to a weaker layer can be "
                    "shadowed by stronger layers and may appear to do nothing in the viewport."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "layer_path": {
                            "type": "string",
                            "description": (
                                "Identifier of the layer to make the edit target. Must already be "
                                "in the layer stack — call list_layers() to see options. Example: "
                                "'/tmp/overrides.usda' or 'anon:0x7f8a1c0' for a session layer."
                            ),
                        },
                    },
                    "required": ["layer_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_variant_sets",
                "description": (
                    "WHAT: Read every variant set declared on a prim (and on its ancestors via "
                    "inherited composition) by calling prim.GetVariantSets().GetNames() and "
                    "GetVariantSelection() for each. A variant set is a named switch (e.g. 'modelingVariant', "
                    "'shadingVariant', 'lod') with a list of named choices. "
                    "WHEN: discovering what configuration knobs an asset exposes before set_variant, "
                    "answering 'what variants does this prim have', auditing an asset library for "
                    "consistent variant naming, or before list_variants() to enumerate the choices "
                    "in a specific set. "
                    "RETURNS: data dict {prim_path: str, variant_sets: [{name: str, current: str, "
                    "count: int}], count: int}. "
                    "UNITS: counts are integers (number of variants in the set). 'current' is the "
                    "active selection ('' means no selection — falls back to the variant set's "
                    "default or the first one). "
                    "CAVEATS: read-only. Variant sets are inherited along the namespace, so a prim "
                    "may show variant sets defined on its model root. Returns an empty list when "
                    "the prim has no variant sets — that is NOT an error. Returns an error stub if "
                    "the prim path doesn't resolve."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Asset' or "
                                "'/World/Robot/geom'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_variants",
                "description": (
                    "WHAT: List every named variant choice inside a specific variant set on a prim "
                    "by calling prim.GetVariantSet(variant_set).GetVariantNames(). Also returns "
                    "the currently selected variant. "
                    "WHEN: enumerating asset configurations (e.g. 'red'/'blue'/'green' for a "
                    "shadingVariant; 'low'/'mid'/'high' for a lod set), confirming a variant exists "
                    "before set_variant, or surfacing the choices to the user for selection. "
                    "RETURNS: data dict {prim_path: str, variant_set: str, variants: [str], "
                    "current: str, count: int}. "
                    "UNITS: variants are arbitrary string names defined by the asset author. "
                    "CAVEATS: read-only. Returns an empty variants list (not an error) when the "
                    "variant set exists but has no choices defined yet. Returns an error stub when "
                    "the prim path doesn't resolve OR the variant set isn't declared on the prim "
                    "(use list_variant_sets() first to see what's available)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Asset'."
                            ),
                        },
                        "variant_set": {
                            "type": "string",
                            "description": (
                                "Name of the variant set to enumerate. Example: 'shadingVariant', "
                                "'modelingVariant', 'lod'."
                            ),
                        },
                    },
                    "required": ["prim_path", "variant_set"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "flatten_layers",
                "description": (
                    "WHAT: Bake every layer in the current stage's layer stack — root + all "
                    "sublayers + all references — into a single .usda file at output_path by calling "
                    "stage.Flatten().Export(output_path). The result is a self-contained USD file "
                    "with one layer that has zero composition arcs. "
                    "WHEN: shipping a final scene to a renderer or external tool that doesn't "
                    "support composition, freezing a working scene before refactoring layers, "
                    "creating a deterministic snapshot for SDG/training reproducibility, or "
                    "preparing a scene for a USDZ archive. "
                    "RETURNS: code patch for approval. "
                    "UNITS: output_path is a filesystem path; .usda (ASCII) is human-readable, "
                    ".usdc (crate) is binary and ~10x smaller for large scenes. "
                    "CAVEATS: flattening is LOSSY for composition — variant sets, payloads, "
                    "references, and inherits collapse into resolved opinions; you cannot recover "
                    "the layer structure afterwards. Asset metadata, layer customLayerData, and "
                    "layer-level offsets are dropped. Output file size can be very large for scenes "
                    "with many references (each is fully expanded). Run after set_edit_target / "
                    "add_sublayer changes are saved — unsaved anonymous layers are still flattened "
                    "but the source is then lost. Existing files at output_path are overwritten."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "output_path": {
                            "type": "string",
                            "description": (
                                "Filesystem path for the flattened output. Use .usda for ASCII "
                                "(diff-friendly) or .usdc for binary crate (smaller). Example: "
                                "'/tmp/flattened_scene.usda'."
                            ),
                        },
                    },
                    "required": ["output_path"],
                },
            },
        },

    # ── From feat/atomic-tier10-animation ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_timeline_state",
                "description": (
                    "WHAT: Read the current state of the USD timeline interface — "
                    "current time, start/end time, FPS (timeCodesPerSecond), playback "
                    "direction, and whether the timeline is currently playing or paused. "
                    "Calls omni.timeline.get_timeline_interface() plus stage.GetStartTimeCode/"
                    "GetEndTimeCode/GetTimeCodesPerSecond and reports a snapshot. "
                    "WHEN: before set_timeline_range (so the LLM doesn't truncate animation "
                    "the user already authored), before set_keyframe (to confirm the keyframe "
                    "time falls inside the active range), before play_animation (to know what "
                    "'reset to start' means), when the user asks 'where in the timeline am I' "
                    "/ 'what FPS is the scene' / 'is anything playing', or to verify a previous "
                    "timeline mutation took effect. "
                    "RETURNS: data dict {current_time: float, start_time: float, end_time: float, "
                    "fps: float, is_playing: bool, looping: bool, time_codes_per_second: float, "
                    "duration_seconds: float}. "
                    "UNITS: time fields are USD time codes (a.k.a. frames). duration_seconds = "
                    "(end_time - start_time) / fps. fps is timeCodesPerSecond from the stage's "
                    "metadata, NOT the renderer's framerate. "
                    "CAVEATS: read-only — no mutation. Returns an error stub if no stage is open "
                    "or omni.timeline isn't available (running outside Kit). The timeline reflects "
                    "USD TimeSamples playback, NOT the physics simulation clock; sim_control "
                    "play/pause is a SEPARATE concern (physics steps), although in Kit they are "
                    "wired together by default. is_playing can be true even when no keyframes "
                    "exist — the timeline cursor still advances."
                ),
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
                "name": "set_timeline_range",
                "description": (
                    "WHAT: Configure the USD timeline by setting the stage's startTimeCode, "
                    "endTimeCode, and timeCodesPerSecond (FPS) metadata via "
                    "stage.SetStartTimeCode/SetEndTimeCode/SetTimeCodesPerSecond. Also pushes "
                    "the new range into omni.timeline so the viewport scrubber updates. "
                    "WHEN: setting up an animation/recording window before set_keyframe, "
                    "matching the timeline to a recorded teleop demonstration's duration, "
                    "preparing for SDG capture (one frame per timecode), aligning timeline "
                    "to a target render framerate (24/30/60), or shrinking the range to "
                    "loop-test a sub-clip. "
                    "RETURNS: code patch for approval. "
                    "UNITS: start/end are USD time codes (frames). fps is frames per second "
                    "(timeCodesPerSecond). Real-world duration in seconds = (end - start) / fps. "
                    "CAVEATS: changing fps does NOT rescale existing TimeSamples — keyframes "
                    "stay at the same numeric time codes, so a key at frame 24 plays at t=1.0s "
                    "with fps=24 but at t=0.5s with fps=48. start_time MUST be < end_time. "
                    "Existing TimeSamples outside the new range are NOT deleted — they're just "
                    "no longer played back. After set_timeline_range, call get_timeline_state() "
                    "to verify the write."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "number",
                            "description": (
                                "Start time code (frame). Typically 0 or 1. Example: 0."
                            ),
                        },
                        "end": {
                            "type": "number",
                            "description": (
                                "End time code (frame). Must be > start. Example: 240 for "
                                "10 seconds at 24 fps."
                            ),
                        },
                        "fps": {
                            "type": "number",
                            "description": (
                                "Frames per second (timeCodesPerSecond). Common: 24 (film), "
                                "30 (NTSC video), 60 (interactive). Default: keep existing fps."
                            ),
                        },
                    },
                    "required": ["start", "end"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_keyframe",
                "description": (
                    "WHAT: Write a USD TimeSample (keyframe) for an attribute at a specific "
                    "time by calling prim.GetAttribute(attr).Set(value, time_code). The value "
                    "is recorded into the current edit target layer at the given time code; "
                    "USD interpolates linearly between adjacent samples on playback. "
                    "WHEN: animating object positions/rotations/scales over time, recording "
                    "teleop demonstrations as USD TimeSamples (one frame per joint state), "
                    "creating procedural animations (e.g. door open/close), authoring camera "
                    "moves for cinematics, or baking a controller's output into a static "
                    "animation clip for later replay without physics. "
                    "RETURNS: code patch for approval. "
                    "UNITS: time is in SECONDS — internally multiplied by the stage's "
                    "timeCodesPerSecond to get the USD time code (so set_keyframe(..., time=2.5) "
                    "with fps=24 writes at frame 60). value units depend on the attribute "
                    "(meters for translate, degrees for rotateXYZ, normalized RGB for "
                    "displayColor, etc.). "
                    "CAVEATS: only works on attributes that support TimeSamples — positions, "
                    "rotations, scales, displayColor, light intensity, joint drive targets: YES. "
                    "Topology (mesh point counts, prim hierarchy), API schemas, and metadata: "
                    "NO. The attribute is created if it doesn't exist (provided the prim "
                    "supports it). Writes go into the current edit target layer — call "
                    "set_edit_target() first if you want the keyframe in a specific sublayer. "
                    "Use list_keyframes() afterwards to verify the sample landed at the "
                    "expected time code. Mixing TimeSamples with a default value can cause "
                    "surprising playback behaviour at the range boundaries — TimeSamples "
                    "always win when present."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim that owns the attribute. Example: "
                                "'/World/Cube' or '/World/Robot/joints/panda_joint1'."
                            ),
                        },
                        "attr": {
                            "type": "string",
                            "description": (
                                "Attribute name to keyframe. Examples: 'xformOp:translate', "
                                "'xformOp:rotateXYZ', 'xformOp:scale', "
                                "'primvars:displayColor', 'inputs:intensity', "
                                "'drive:angular:physics:targetPosition'."
                            ),
                        },
                        "time": {
                            "type": "number",
                            "description": (
                                "Keyframe time in SECONDS. Multiplied by the stage's "
                                "timeCodesPerSecond internally. Example: 1.5 (= frame 36 "
                                "at 24 fps)."
                            ),
                        },
                        "value": {
                            "description": (
                                "New value at this time. Number, array (e.g. [x,y,z] for "
                                "translate), or RGB triplet. Type must match the attribute."
                            ),
                        },
                    },
                    "required": ["prim_path", "attr", "time", "value"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_keyframes",
                "description": (
                    "WHAT: Enumerate every TimeSample currently authored on a single attribute "
                    "of a prim by calling attr.GetTimeSamples() and attr.Get(time_code) for each. "
                    "Returns the list of time codes (in seconds and frames), the value at each, "
                    "and the bracketing range. "
                    "WHEN: verifying a previous set_keyframe write landed at the expected time, "
                    "auditing a recorded teleop trajectory for missing/duplicate samples, "
                    "deciding where to insert an in-between key, answering 'what's animated on "
                    "this prim', or before deleting/rewriting a keyframe to confirm the current "
                    "value first. "
                    "RETURNS: data dict {prim_path: str, attr: str, has_timesamples: bool, "
                    "count: int, fps: float, samples: [{time_code: float, time_seconds: float, "
                    "value: any}], time_range_codes: [first, last], time_range_seconds: "
                    "[first, last]}. "
                    "UNITS: time_code is a USD frame number; time_seconds = time_code / fps. "
                    "value units match the attribute (meters / degrees / RGB / etc.). "
                    "CAVEATS: read-only. Returns has_timesamples=false (NOT an error) when the "
                    "attribute has only a default value, or when the attribute exists but has "
                    "no samples yet. Returns an error stub if the prim or attribute doesn't "
                    "exist. attr.GetTimeSamples() reports the resolved list across all layers — "
                    "you may see samples authored in a sublayer that you didn't write yourself. "
                    "Very dense sample lists (e.g. recorded at 60 Hz for several minutes) can "
                    "be large; consider narrowing by querying a specific time range outside this "
                    "tool if the result truncates."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Cube'."
                            ),
                        },
                        "attr": {
                            "type": "string",
                            "description": (
                                "Attribute name to read keyframes from. Example: "
                                "'xformOp:translate'."
                            ),
                        },
                    },
                    "required": ["prim_path", "attr"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "play_animation",
                "description": (
                    "WHAT: Start USD TimeSamples playback over the [start, end] window by "
                    "configuring omni.timeline.get_timeline_interface() — sets start/end time, "
                    "rewinds to start, and calls play(). Distinct from sim_control: sim_control "
                    "drives the PHYSICS simulation step loop (PhysX), while play_animation "
                    "drives the USD TIMELINE cursor that interpolates TimeSamples. In Kit they "
                    "share the same timeline by default, so playback also advances physics "
                    "unless physics is decoupled. "
                    "WHEN: previewing a keyframed animation after authoring it with "
                    "set_keyframe, replaying a recorded teleop demonstration from USD "
                    "TimeSamples, looping a sub-range to debug timing, or kicking off a "
                    "Replicator/SDG capture that's tied to timeline frames. "
                    "RETURNS: code patch for approval. "
                    "UNITS: start, end are in SECONDS — multiplied by the stage's "
                    "timeCodesPerSecond internally to get USD time codes. "
                    "CAVEATS: this is ANIMATION playback (USD TimeSamples interpolation), "
                    "NOT a physics-only step loop — use sim_control(action='play') if you only "
                    "want PhysX advancing without scrubbing the timeline. start MUST be < end. "
                    "If start/end fall outside the stage's startTimeCode/endTimeCode the "
                    "viewport may clamp; pair with set_timeline_range() first to widen the "
                    "range. Does NOT auto-loop unless the timeline's looping flag is already "
                    "true. play_animation triggers a code patch — the actual play() call only "
                    "happens after user approval through Kit RPC."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "number",
                            "description": (
                                "Playback start time in SECONDS. Example: 0 to play from the "
                                "beginning."
                            ),
                        },
                        "end": {
                            "type": "number",
                            "description": (
                                "Playback end time in SECONDS. Must be > start. Example: 5.0 "
                                "to play five seconds of animation."
                            ),
                        },
                    },
                    "required": ["start", "end"],
                },
            },
        },

    # ── From feat/atomic-tier11-sdg ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_semantic_classes",
                "description": (
                    "WHAT: Walk the entire USD stage, find every prim that has "
                    "Semantics.SemanticsAPI applied (any Semantics_* instance), read its "
                    "semanticData attribute, and return the unique set of class labels "
                    "together with the count of prims using each one. "
                    "WHEN: before kicking off SDG / Replicator capture (so the LLM knows the "
                    "label space the writer will see), answering 'what classes are labeled in "
                    "this scene' / 'show me all the semantic categories', auditing an asset "
                    "library for label coverage, or before assign_class_to_children to confirm "
                    "the new class doesn't accidentally collide with an existing label. "
                    "RETURNS: data dict {classes: [{name: str, count: int, sample_prims: "
                    "[str, ...]}], total_classes: int, total_labeled_prims: int}. "
                    "CAVEATS: read-only. Walks the full stage — slow on very large scenes "
                    "(prim_count > 50k); narrow the scope by calling get_semantic_label on a "
                    "subtree root if you only care about one asset. Only counts prims with the "
                    "*default* Semantics_class instance plus any custom Semantics_<type> "
                    "instances; pure metadata-only kind hierarchies are ignored. Returns an "
                    "error stub if no stage is open."
                ),
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
                "name": "get_semantic_label",
                "description": (
                    "WHAT: Read every Semantics.SemanticsAPI instance applied to a single "
                    "prim and return the (semantic_type, class_name) pair for each. Calls "
                    "Semantics.SemanticsAPI.GetAll(prim) and "
                    "GetSemanticTypeAttr/GetSemanticDataAttr on each. "
                    "WHEN: verifying that a previous set_semantic_label / "
                    "assign_class_to_children call landed correctly, answering 'what class is "
                    "this prim labeled as' / 'is this object annotated for SDG', before "
                    "remove_semantic_label so the LLM can confirm the prim is actually "
                    "labeled, or auditing a single asset for missing/duplicate Semantics "
                    "instances. "
                    "RETURNS: data dict {prim_path: str, has_semantics: bool, labels: "
                    "[{instance: str, semantic_type: str, class_name: str}], count: int}. "
                    "CAVEATS: read-only. Returns has_semantics=false (NOT an error) when the "
                    "prim exists but has no SemanticsAPI applied — that is a normal state. "
                    "Returns an error stub when the prim path doesn't resolve. A prim can have "
                    "MULTIPLE Semantics_* instances at once (e.g. one for 'class', one for "
                    "'instance_id'); the labels list reports all of them. Inherited semantics "
                    "from referenced/payloaded layers ARE included — the API resolves across "
                    "the full layer stack."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: "
                                "'/World/Tray/medicine_bottle_03'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "remove_semantic_label",
                "description": (
                    "WHAT: Strip every Semantics.SemanticsAPI instance from a single prim by "
                    "iterating Semantics.SemanticsAPI.GetAll(prim) and calling "
                    "prim.RemoveAPI(Semantics.SemanticsAPI, instance_name) for each. Also "
                    "deletes the underlying semanticType / semanticData attributes left "
                    "behind by RemoveAPI. The prim itself stays in the stage. "
                    "WHEN: re-labeling an asset that was previously tagged with the wrong "
                    "class, excluding a prim from SDG capture (no label = no annotation in "
                    "the writer output), cleaning up after a bad bulk-label run from "
                    "assign_class_to_children, or when the user says 'unlabel this' / 'remove "
                    "the class from /World/Tray/bottle_03'. "
                    "RETURNS: code patch for approval. "
                    "CAVEATS: only clears SemanticsAPI on the GIVEN prim — children retain "
                    "their own labels (use it on each child if you need a recursive clear). "
                    "If the prim has no SemanticsAPI applied the generated code is a safe no-op "
                    "that prints a notice rather than raising. Removing the API but leaving the "
                    "underlying attribute defs in place is a known USD foot-gun; this generator "
                    "explicitly clears the attributes so a downstream "
                    "Semantics.SemanticsAPI.HasAPI() returns False afterwards. Use "
                    "get_semantic_label() before AND after to verify the strip."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to clear. Example: "
                                "'/World/Tray/medicine_bottle_03'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "assign_class_to_children",
                "description": (
                    "WHAT: Apply Semantics.SemanticsAPI to all child meshes of a prim with "
                    "the same class label. Walks the subtree rooted at prim_path, and for "
                    "every descendant that is a Mesh / Imageable (i.e. produces pixels), "
                    "applies Semantics.SemanticsAPI.Apply(child, 'Semantics_class') and sets "
                    "semanticType / semanticData. Xform-only and pure-grouping prims are "
                    "skipped. "
                    "WHEN: bulk-labeling all parts of an asset for SDG (e.g. 'tray' → all "
                    "bottles within get class 'medicine_bottle'), avoiding manual per-prim "
                    "labeling, labeling every link of a robot at once with the robot's class, "
                    "or annotating a freshly imported referenced asset whose internal "
                    "hierarchy has no semantics yet. "
                    "RETURNS: code patch for approval. "
                    "CAVEATS: only Mesh / Imageable children get labeled — Xforms, scopes, "
                    "lights, cameras and pure-namespace prims are skipped (they don't render). "
                    "Existing labels on children ARE OVERWRITTEN by the new class — call "
                    "list_semantic_classes() before to know what you'll clobber. Walks the "
                    "FULL subtree, including referenced/payloaded children, which can be "
                    "thousands of prims for a complex scene; expect the patch to take a "
                    "few seconds to apply on large hierarchies. The root prim itself is also "
                    "labeled if it is a Mesh/Imageable. Use list_semantic_classes() AFTER to "
                    "verify the bulk write."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the subtree root. The root and every "
                                "Mesh/Imageable descendant get the class. Example: "
                                "'/World/Tray' to label every bottle inside as 'medicine_bottle'."
                            ),
                        },
                        "class_name": {
                            "type": "string",
                            "description": (
                                "The class label to apply (semanticData value). Example: "
                                "'medicine_bottle', 'pallet', 'panda_link'."
                            ),
                        },
                        "semantic_type": {
                            "type": "string",
                            "description": (
                                "Optional Semantics instance type. Default: 'class' (the "
                                "standard SDG bucket). Use 'instance_id' or a custom name "
                                "for multi-channel labeling."
                            ),
                        },
                    },
                    "required": ["prim_path", "class_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "validate_semantic_labels",
                "description": (
                    "WHAT: Lint every prim with Semantics.SemanticsAPI applied on the current "
                    "stage and report annotation-quality issues: empty class strings, orphan "
                    "SemanticsAPI applications (API applied but the data attribute is unset), "
                    "classes used on only ONE prim (likely a typo against the bulk class — e.g. "
                    "'bottle' vs 'bottl'), invisible / inactive prims that still carry labels "
                    "(they will not render so the label is dead weight), and prims with multiple "
                    "conflicting Semantics_class instances. "
                    "WHEN: pre-flight before kicking off SDG / Replicator capture — catch label "
                    "bugs before burning hours rendering bad ground truth, after a large "
                    "assign_class_to_children call to confirm the bulk write is consistent, or "
                    "answering 'are my semantic labels correct' / 'why is my SDG class missing'. "
                    "RETURNS: data dict {ok: bool, summary: {labeled_prims: int, classes: int, "
                    "issues: int}, issues: [{severity: 'error'|'warning', kind: str, "
                    "prim_path: str, detail: str}]}. "
                    "CAVEATS: this is DIFFERENT from PR #23 `validate_annotations`. "
                    "validate_annotations reads SDG OUTPUT FILES on disk (writer JSON, bbox "
                    "captures, instance maps) and lints the rendered ground truth. "
                    "validate_semantic_labels reads the USD STAGE and lints the SOURCE "
                    "annotations BEFORE Replicator / SDG runs. Use this tool first to fix "
                    "bad source labels, then run a tiny SDG sample, then PR #23's tool to "
                    "lint the output. Read-only — never mutates the stage. Returns an error "
                    "stub when no stage is open."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },

    # ── From feat/atomic-tier12-asset-mgmt ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "list_references",
                "description": (
                    "WHAT: Enumerate every USD reference arc composed onto a prim. Calls "
                    "prim.GetReferences().GetAllReferences() for the local opinion and walks "
                    "Usd.PrimCompositionQuery to surface inherited / sublayered reference arcs "
                    "with their introducing layer. Reports each reference's asset_path, "
                    "prim_path (the targeted prim inside the referenced file), layer_offset, "
                    "and the layer that introduced it. "
                    "WHEN: answering 'where did this prim come from?' / 'what assets are "
                    "loaded onto /World/Robot?', verifying that a previous add_reference / "
                    "add_usd_reference call landed correctly, before remove_reference (when "
                    "that tool is added) so the LLM can confirm the asset is actually "
                    "referenced, debugging composition issues ('why does this prim have two "
                    "meshes?'), and auditing a scene for which external files it depends on. "
                    "RETURNS: data dict {prim_path: str, has_references: bool, references: "
                    "[{asset_path: str, prim_path: str, layer_offset: {offset: float, scale: "
                    "float}, introducing_layer: str, list_position: 'prepended' | 'appended' "
                    "| 'explicit'}], count: int}. "
                    "CAVEATS: read-only — never mutates the stage. References are ALWAYS "
                    "loaded (vs payloads which are deferred) — so `has_references=true` means "
                    "the asset's prims are already in memory. The list is the COMPOSED order "
                    "after stronger opinions win; use Usd.PrimCompositionQuery in custom "
                    "scripts if you need the raw arc list. Returns has_references=false (NOT "
                    "an error) when the prim has no references — that is a normal state. "
                    "Returns an error stub when the prim path doesn't resolve. Does NOT list "
                    "payloads — use list_payloads for that."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Robot' or "
                                "'/World/Tray/medicine_bottle_03'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "add_usd_reference",
                "description": (
                    "WHAT: Add a USD reference to a prim with the FULL Usd.References surface "
                    "— optional ref_prim_path (target a specific prim inside the file, not the "
                    "defaultPrim), optional layer_offset_seconds for animation retiming "
                    "(Sdf.LayerOffset), and optional instanceable flag for USD point-"
                    "instancing of repeated assets. Calls "
                    "prim.GetReferences().AddReference(asset_path, ref_prim_path?, "
                    "layer_offset?) and optionally prim.SetInstanceable(True). "
                    "WHEN: dropping an asset onto an Xform when you need MORE than the "
                    "simple `add_reference` (PR #1) — e.g. 'reference only the /Manipulator "
                    "subtree from franka.usd' (use ref_prim_path), 'load this anim USD with "
                    "a 2-second offset' (use layer_offset_seconds), or 'add 100 of the same "
                    "tree as instanceable point-instances' (use instanceable=true). For the "
                    "common no-kwargs case prefer the simpler `add_reference` so the user "
                    "approval prompt is cleaner. "
                    "RETURNS: code patch for approval. "
                    "CAVEATS: distinct from PR #1's `add_reference` — both backed by "
                    "AddReference but `add_reference` is the simple default-prim drop with no "
                    "kwargs. Adding a reference that targets a non-existent ref_prim_path "
                    "makes the prim invalid in the composed stage (USD will not raise — the "
                    "prim just renders empty); call list_references afterwards to verify. "
                    "instanceable=True changes the prim into a USD instance master and any "
                    "subsequent edits to its descendants are silently lost (USD instancing "
                    "rule); only set instanceable when you know you will not author "
                    "per-instance edits below this prim. layer_offset_seconds applies to USD "
                    "time codes, not seconds — internally converted via the stage's "
                    "timeCodesPerSecond at apply time. add_usd_reference APPENDS to the "
                    "reference list; existing references on the prim are kept (use a future "
                    "remove_reference / clear_references tool to strip them first if needed)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim that will hold the reference. Example: "
                                "'/World/Robot'. The prim is auto-created as an Xform if it "
                                "does not exist yet."
                            ),
                        },
                        "usd_url": {
                            "type": "string",
                            "description": (
                                "Asset path / URL to the .usd / .usda / .usdz file. Examples: "
                                "'omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/Franka/"
                                "franka.usd', './assets/medicine_bottle.usda', "
                                "'https://example.com/tray.usdz'."
                            ),
                        },
                        "ref_prim_path": {
                            "type": "string",
                            "description": (
                                "Optional. USD path INSIDE the referenced file to target — "
                                "default is the file's defaultPrim. Example: "
                                "'/Manipulator/panda_link0' to reference only the link subtree "
                                "instead of the whole robot."
                            ),
                        },
                        "layer_offset_seconds": {
                            "type": "number",
                            "description": (
                                "Optional. Time offset in seconds applied to the referenced "
                                "layer (Sdf.LayerOffset). Useful for retiming animation USDs. "
                                "Default 0. Internally converted to USD time codes via the "
                                "stage's timeCodesPerSecond."
                            ),
                        },
                        "instanceable": {
                            "type": "boolean",
                            "description": (
                                "Optional. Mark the prim as a USD instance after the "
                                "reference is added (point-instancing for repeated assets). "
                                "Default false. WARNING: per-instance edits below an "
                                "instanceable prim are silently dropped by USD."
                            ),
                        },
                    },
                    "required": ["prim_path", "usd_url"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_payloads",
                "description": (
                    "WHAT: Enumerate every USD payload arc on a prim. Payloads are like "
                    "references but DEFERRED — the asset is not loaded into memory until "
                    "stage.Load() / load_payload is called. Calls "
                    "prim.GetPayloads().GetAllPayloads() for the local opinion and walks "
                    "Usd.PrimCompositionQuery to surface inherited / sublayered payload arcs. "
                    "Reports each payload's asset_path, prim_path inside the payloaded file, "
                    "layer_offset, introducing_layer, AND whether it is currently loaded "
                    "(stage.GetLoadSet() membership). "
                    "WHEN: answering 'what heavy assets is /World/Robot lazily loading?', "
                    "before load_payload to see what would be activated, debugging 'why is "
                    "this prim empty?' (often: payload not loaded yet), auditing a scene's "
                    "memory footprint (unloaded payloads = 0 RAM), or before scene save / "
                    "export (so you know which payloads are activated and which are not). "
                    "RETURNS: data dict {prim_path: str, has_payloads: bool, payloads: "
                    "[{asset_path: str, prim_path: str, layer_offset: {offset: float, scale: "
                    "float}, introducing_layer: str, is_loaded: bool, list_position: "
                    "'prepended' | 'appended' | 'explicit'}], count: int, prim_is_loaded: "
                    "bool}. "
                    "CAVEATS: read-only. has_payloads=false (NOT an error) is normal for "
                    "most prims — payloads are an opt-in performance feature. The is_loaded "
                    "flag reflects the CURRENT load set (stage.GetLoadSet()) not the static "
                    "USD definition; payloads can be loaded / unloaded at runtime via "
                    "load_payload (or the inverse, when added). Does NOT list references — "
                    "use list_references for that. The is_loaded flag is per-prim, not per-"
                    "payload-arc — USD loads the prim's full payload set together."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Robot' or "
                                "'/World/Environment/Warehouse'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "load_payload",
                "description": (
                    "WHAT: Activate a deferred-loaded payload on a prim by adding it to the "
                    "stage's load set. Calls stage.LoadAndUnload({prim_path}, set(), "
                    "Usd.LoadWithDescendants) which loads the prim's payload AND every "
                    "payload below it in the hierarchy. The prim and its descendants become "
                    "inspectable / renderable after this call. "
                    "WHEN: the user says 'load the warehouse environment' / 'activate the "
                    "robot payload' / 'why is this empty? load it', after list_payloads "
                    "showed `is_loaded=false` for a prim that should be visible, before "
                    "running SDG / Replicator on a payloaded asset (the writer cannot capture "
                    "an unloaded subtree), or before computing bounding boxes / running "
                    "physics on a payloaded subtree. "
                    "RETURNS: code patch for approval. "
                    "CAVEATS: loading a heavy payload (a full warehouse, a 500-MB robot) can "
                    "take several seconds and adds significant RAM usage — verify with "
                    "list_payloads first that the asset_path is the one you expect. "
                    "LoadWithDescendants is used by default; descendants-only loads are not "
                    "exposed from this tool (write a custom script via run_usd_script if you "
                    "need LoadWithoutDescendants). The matching unload-payload tool is NOT "
                    "part of tier 12 — call run_usd_script with stage.Unload({prim_path}) for "
                    "the inverse. Soft no-op + printed notice if the prim is already loaded "
                    "(prim_path in stage.GetLoadSet()) — does not raise. Raises only on a "
                    "bad prim_path."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim whose payload(s) to load. Loads the "
                                "prim's payload AND every payload below it. Example: "
                                "'/World/Environment' to activate a deferred warehouse asset."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_asset_info",
                "description": (
                    "WHAT: Read the asset-provenance metadata for a prim — origin file, "
                    "asset name, asset version, payload asset (if any), and the identifier "
                    "of the layer that introduced the prim. Combines prim.GetAssetInfo() "
                    "(the assetInfo metadata dict — `identifier`, `name`, `version`, "
                    "`payloadAssetDependencies`) with the prim's primary spec / introducing "
                    "layer (Sdf.PrimSpec) and a sha256 hash of the resolved layer file when "
                    "the layer is a real on-disk file. "
                    "WHEN: provenance / debugging — 'where did /World/Robot come from?', "
                    "'what version of the franka asset is loaded?', 'is this the Isaac Sim "
                    "5.1 panda or the 5.0 one?', auditing a scene to ensure all referenced "
                    "assets come from approved sources, computing a fingerprint of the scene "
                    "for caching / contribute_data, and as a follow-up to list_references "
                    "when the LLM needs the version / hash of a specific reference. "
                    "RETURNS: data dict {prim_path: str, has_asset_info: bool, asset_info: "
                    "{identifier: str, name: str, version: str, payload_asset_dependencies: "
                    "[str, ...]}, introducing_layer: {identifier: str, real_path: str, "
                    "version: str|null, sha256: str|null}, prim_kind: str|null, "
                    "prim_specifier: 'def' | 'over' | 'class'}. "
                    "CAVEATS: read-only. has_asset_info=false (NOT an error) is normal — "
                    "most prims do not author the assetInfo metadata. The sha256 field is "
                    "populated only when the introducing layer resolves to an on-disk file "
                    "smaller than 256 MB; otherwise sha256=null (hashing a multi-GB layer "
                    "synchronously would block Kit). version comes from "
                    "assetInfo['version'] — many assets do not author this and version "
                    "will be the empty string. introducing_layer.identifier is what you "
                    "would pass to Sdf.Layer.FindOrOpen() to reopen the layer. Returns an "
                    "error stub when the prim path doesn't resolve."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {
                            "type": "string",
                            "description": (
                                "USD path of the prim to inspect. Example: '/World/Robot' "
                                "or '/World/Tray/medicine_bottle_03'."
                            ),
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },

    # ── From feat/atomic-tier13-rl-runtime ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_env_observations",
                "description": (
                    "WHAT: Read the current observation tensor for one parallel environment in a "
                    "running IsaacLab worker. Reaches into the live ManagerBasedRLEnv via the "
                    "training subprocess IPC channel and serializes that env's observation dict "
                    "(joint positions, velocities, target poses, sensor readings, etc.) to JSON. "
                    "WHEN: Use mid-training when the user asks 'what does env 5 see right now?', "
                    "'why is env 12 stuck?', or wants to inspect what the policy network is being "
                    "fed for a specific worker before its episode ends. This is RUNTIME inspection — "
                    "for setup, see create_isaaclab_env; for after-the-fact analysis, see "
                    "diagnose_training. "
                    "RETURNS: {observations: {term_name: [floats]}, env_id: int, step: int, "
                    "episode_step: int, dtype: str, shape: [int], wall_time_ms: float}. "
                    "CAVEATS: Requires a launch_training run that is currently RUNNING (not paused, "
                    "not finished); env_id must be in [0, num_envs); incurs ~1-5ms GPU→CPU sync per "
                    "call so do not poll in a tight loop; observation values are policy-relative and "
                    "may already be normalized depending on the env config."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env_id": {
                            "type": "integer",
                            "description": "Index of the parallel environment to read, in [0, num_envs). For a 64-env training run, valid range is 0-63.",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional training run identifier returned by launch_training. If omitted, defaults to the most recent active run.",
                        },
                    },
                    "required": ["env_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_env_rewards",
                "description": (
                    "WHAT: Read the per-term reward breakdown for one parallel environment at the "
                    "current training step. Returns each named reward component (e.g. "
                    "'tracking_lin_vel', 'action_penalty', 'foot_clearance') with its raw value and "
                    "weighted contribution, plus the total. Useful for understanding WHY a particular "
                    "env is succeeding or failing in real time. "
                    "WHEN: Use during training when the user asks 'why is env 3 getting low reward?', "
                    "'which reward term dominates right now?', or wants to verify a reward shaping "
                    "change is taking effect on live workers. Distinct from review_reward (PR #37) "
                    "which post-mortems the FULL run statistics; this tool gives you the INSTANT "
                    "reward vector for ONE env at the current step. "
                    "RETURNS: {env_id: int, step: int, total_reward: float, terms: [{name: str, "
                    "raw_value: float, weight: float, weighted: float}], episode_return: float, "
                    "wall_time_ms: float}. "
                    "CAVEATS: Requires a RUNNING launch_training process; env_id must be in "
                    "[0, num_envs); reward values are for the most recent .step() — they will change "
                    "next physics tick; episode_return is cumulative since last reset, not a moving "
                    "average."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env_id": {
                            "type": "integer",
                            "description": "Index of the parallel environment to read, in [0, num_envs).",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional training run identifier from launch_training. Defaults to the most recent active run.",
                        },
                    },
                    "required": ["env_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "get_env_termination_state",
                "description": (
                    "WHAT: Report the live termination/done flags for one parallel environment: "
                    "whether it has hit a success condition, a timeout (max episode length), a "
                    "failure/crash (e.g. robot fell, cube dropped, joint limit violated), or is still "
                    "running. Includes the raw termination-term values so you can see WHICH "
                    "condition fired. "
                    "WHEN: Use when the user asks 'did env 7 finish yet?', 'why did env 3 reset?', "
                    "or 'how many envs are timing out vs succeeding?'. This is the live RUNTIME "
                    "view — for aggregate success-rate over a whole run, use diagnose_training "
                    "(PR #37). "
                    "RETURNS: {env_id: int, done: bool, success: bool, timeout: bool, crashed: bool, "
                    "termination_terms: {name: bool}, episode_step: int, max_episode_steps: int, "
                    "last_reset_step: int, wall_time_ms: float}. "
                    "CAVEATS: Requires an active launch_training run; env_id must be in "
                    "[0, num_envs); 'success' is only meaningful if the env defines a success "
                    "termination term (most locomotion tasks do not — they only have timeout + "
                    "crash); flags are sampled from the most recent .step() and reset every episode."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env_id": {
                            "type": "integer",
                            "description": "Index of the parallel environment to inspect, in [0, num_envs).",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional training run identifier from launch_training. Defaults to the most recent active run.",
                        },
                    },
                    "required": ["env_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "pause_training",
                "description": (
                    "WHAT: Send a SIGUSR1-style pause signal to a launch_training subprocess so it "
                    "stops calling .step() and .learn() but KEEPS the policy network, optimizer "
                    "state, replay buffer, and parallel envs alive in memory. The run can be resumed "
                    "later from the exact same state — no checkpoint round-trip required. "
                    "WHEN: Use when the user wants to inspect intermediate state, change a "
                    "hyperparameter, or free the GPU briefly without losing 30 minutes of training "
                    "progress. Common phrasings: 'pause training', 'hold on training', 'freeze the "
                    "RL run while I check something'. Do NOT use to fully stop a run — for that, "
                    "the user should kill the process. "
                    "RETURNS: {run_id: str, paused: bool, previous_state: str, step: int, "
                    "iteration: int, pid: int, signal_sent: str, wall_time_ms: float}. "
                    "CAVEATS: Only works for runs launched via launch_training (which manages the "
                    "subprocess + signal handler); a paused run still holds GPU memory — to release "
                    "VRAM you must checkpoint_training then kill; pause is best-effort and may take "
                    "a few hundred ms to take effect at the next step boundary; calling pause on an "
                    "already-paused run is a no-op."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Training run identifier returned by launch_training. If omitted, the most recent active run is paused.",
                        },
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "checkpoint_training",
                "description": (
                    "WHAT: Trigger an out-of-band checkpoint save on a running launch_training "
                    "subprocess. Serializes the policy network weights, value network weights, "
                    "optimizer state, and (optionally) the replay buffer to "
                    "<checkpoint_dir>/manual_step_<N>.pt. Does NOT stop or pause training — the "
                    "worker keeps stepping while the save runs in a background thread. "
                    "WHEN: Use when the user wants to grab a snapshot of the current policy without "
                    "waiting for the next scheduled checkpoint, e.g. 'save the model now', 'I want "
                    "to test the current policy in eval mode', or right before a risky "
                    "hyperparameter change. Different from launch_training which sets up the "
                    "PERIODIC checkpoint cadence; this triggers ONE save on demand. "
                    "RETURNS: {run_id: str, checkpoint_path: str, step: int, iteration: int, "
                    "size_bytes: int, includes_replay_buffer: bool, save_duration_ms: float, "
                    "wall_time_ms: float}. "
                    "CAVEATS: Requires a RUNNING (not paused, not finished) launch_training "
                    "subprocess; save runs async — file may not be fully flushed when the call "
                    "returns (check checkpoint_path exists before loading); replay-buffer saves can "
                    "be 100MB+ for off-policy algos (SAC/TD3) — set include_replay_buffer=False to "
                    "save only the policy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Training run identifier returned by launch_training. If omitted, the most recent active run is checkpointed.",
                        },
                        "include_replay_buffer": {
                            "type": "boolean",
                            "description": "If true, also serialize the off-policy replay buffer (SAC/TD3 only — ignored for on-policy PPO). Default: false.",
                        },
                        "tag": {
                            "type": "string",
                            "description": "Optional human-readable tag appended to the checkpoint filename (e.g. 'pre_lr_change'). Default: 'manual'.",
                        },
                    },
                    "required": [],
                },
            },
        },

    # ── From feat/atomic-tier14-bulk ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "bulk_set_attribute",
                "description": (
                    "WHAT: Atomically set the SAME attribute (e.g. 'visibility', "
                    "'xformOp:translate', 'physics:rigidBodyEnabled', a custom attr) "
                    "to the SAME value on MANY USD prims in a single Sdf.ChangeBlock. "
                    "Wrapping all writes in one ChangeBlock means USD/Hydra fires a "
                    "single notification batch instead of N — orders of magnitude "
                    "faster for large scenes, and avoids partial-state intermediate "
                    "renders. "
                    "WHEN: Use to bulk-toggle visibility on hundreds of prims, "
                    "bulk-disable physics across an environment, set the same color "
                    "on a swarm, reset a custom flag on every robot, etc. Prefer "
                    "this over a Python loop of set_attribute calls whenever the "
                    "value and attribute name are identical across prims. "
                    "RETURNS: {type: 'code_patch', code, description, queued} — the "
                    "generated patch counts how many prims were valid, how many "
                    "lacked the attribute (auto-created on the fly via "
                    "CreateAttribute when the type is inferable from the value), "
                    "and prints a summary line. "
                    "CAVEATS: All prims must accept the same value type — passing a "
                    "Vec3 to a bool attribute will raise per-prim. Missing prims "
                    "are SKIPPED (not an error). For DIFFERENT attributes per prim, "
                    "use repeated set_attribute. For DIFFERENT values per prim, use "
                    "the existing batch_set_attributes tool. The ChangeBlock "
                    "suppresses notifications until the block exits — observers and "
                    "listeners only see the final state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths of prims to mutate, e.g. ['/World/Cube_001', '/World/Cube_002', ...]. Missing prims are silently skipped.",
                        },
                        "attr": {
                            "type": "string",
                            "description": "Attribute name shared by every prim, e.g. 'visibility', 'xformOp:translate', 'primvars:displayColor', 'myCustomFlag'.",
                        },
                        "value": {
                            "description": "Value to assign on every prim (number, bool, string, list of numbers). Must be type-compatible with the attribute on each prim.",
                        },
                    },
                    "required": ["prim_paths", "attr", "value"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "bulk_apply_schema",
                "description": (
                    "WHAT: Apply the SAME USD API schema (e.g. PhysicsRigidBodyAPI, "
                    "PhysicsCollisionAPI, PhysicsMassAPI, PhysxRigidBodyAPI, a "
                    "custom multi-apply schema) to MANY prims in one atomic "
                    "Sdf.ChangeBlock. Resolves common short-name aliases (e.g. "
                    "'RigidBodyAPI' -> UsdPhysics.RigidBodyAPI) before applying. "
                    "WHEN: Use when an entire group of meshes needs to become rigid "
                    "bodies, when you need to flag every prop in a scene as a "
                    "collider, when adding the same PhysX tuning API to all robot "
                    "links, etc. Prefer this over apply_api_schema in a loop "
                    "whenever the schema name is identical across prims. "
                    "RETURNS: {type: 'code_patch', code, description, queued} — the "
                    "patch reports how many prims successfully had the schema "
                    "applied, how many were missing, and how many already had it "
                    "(idempotent — re-applying is a no-op). "
                    "CAVEATS: Single-apply schemas (RigidBodyAPI) require the prim "
                    "to be Xformable; multi-apply schemas (CollectionAPI) require "
                    "an instance_name (not yet supported here — use "
                    "apply_api_schema for those). Unknown schema names fall back "
                    "to ApplyAPISchemaCommand which may fail silently in older "
                    "Kit. The ChangeBlock prevents intermediate composition "
                    "rebuilds — much faster than a per-prim loop."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths of prims to receive the schema, e.g. ['/World/Box_a', '/World/Box_b'].",
                        },
                        "schema": {
                            "type": "string",
                            "description": "Schema name: 'PhysicsRigidBodyAPI', 'PhysicsCollisionAPI', 'PhysicsMassAPI', 'PhysxRigidBodyAPI', 'PhysxCollisionAPI', 'PhysxDeformableBodyAPI'. Bare class name ('RigidBodyAPI') and dotted form ('UsdPhysics.RigidBodyAPI') both accepted.",
                        },
                    },
                    "required": ["prim_paths", "schema"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "select_by_criteria",
                "description": (
                    "WHAT: Query the USD stage and return a list of prim paths "
                    "matching a criteria dict. Supported criteria keys: "
                    "'type' (typeName e.g. 'Mesh'/'Xform'/'Camera'), "
                    "'has_schema' (applied API schema name), "
                    "'name_pattern' (regex matched against the prim's leaf name), "
                    "'path_pattern' (regex matched against the full USD path), "
                    "'has_attribute' (attribute name that must exist), "
                    "'kind' (USD Kind metadata: 'component', 'assembly', 'group'), "
                    "'parent' (descendants of this path only), "
                    "'active' (bool — only active or only deactivated prims). "
                    "ALL specified criteria must match (AND semantics). "
                    "WHEN: Use as the first step of a bulk workflow — find every "
                    "Mesh under /World/Robot, every prim with RigidBodyAPI, every "
                    "Camera whose name starts with 'cam_', every component-kind "
                    "prim — then feed the result into bulk_set_attribute, "
                    "bulk_apply_schema, group_prims, or duplicate_prims. "
                    "RETURNS: {type: 'data', matches: ['/World/A', '/World/B', "
                    "...], count: N, criteria: {...}} — paths sorted "
                    "alphabetically. Empty list if nothing matches. "
                    "CAVEATS: Runs against the live Kit stage via /exec — requires "
                    "Kit RPC to be reachable. Regex patterns use Python re.search "
                    "(not anchored — use ^...$ to anchor). 'has_schema' matches "
                    "the unaliased applied-API name as USD reports it (e.g. "
                    "'PhysicsRigidBodyAPI'). Large stages: queries are O(N) over "
                    "every prim — narrow with 'parent' for deep hierarchies."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "criteria": {
                            "type": "object",
                            "description": "Dict of filter criteria. Keys: type, has_schema, name_pattern, path_pattern, has_attribute, kind, parent, active. Example: {'type': 'Mesh', 'parent': '/World/Robot', 'has_schema': 'PhysicsCollisionAPI'}.",
                            "properties": {
                                "type": {"type": "string", "description": "USD typeName, e.g. 'Mesh', 'Xform', 'Camera', 'Cube'."},
                                "has_schema": {"type": "string", "description": "Applied API schema name, e.g. 'PhysicsRigidBodyAPI'."},
                                "name_pattern": {"type": "string", "description": "Python regex matched against the prim's leaf name (re.search)."},
                                "path_pattern": {"type": "string", "description": "Python regex matched against the full prim path (re.search)."},
                                "has_attribute": {"type": "string", "description": "Attribute name that must exist on the prim."},
                                "kind": {"type": "string", "description": "USD Kind metadata, e.g. 'component', 'assembly', 'group', 'subcomponent'."},
                                "parent": {"type": "string", "description": "Restrict search to descendants of this prim path."},
                                "active": {"type": "boolean", "description": "Filter on prim active state — true=only active, false=only deactivated."},
                            },
                        },
                    },
                    "required": ["criteria"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "group_prims",
                "description": (
                    "WHAT: Create a new Xform prim and reparent the given prims "
                    "under it, preserving each prim's world transform. Done atomically "
                    "in an Sdf.ChangeBlock so observers see one final hierarchy "
                    "rather than N intermediate moves. "
                    "WHEN: Use to organize a flat scene into logical groups (all "
                    "lights under /World/Lights, all props under /World/Props), to "
                    "create a parent for collective transformation (group ten cubes "
                    "then translate the parent), or to scope variants/visibility "
                    "to a subset of prims. "
                    "RETURNS: {type: 'code_patch', code, description, queued} — the "
                    "generated patch creates the Xform at "
                    "{group_parent}/{group_name} (default group_parent='/World'), "
                    "uses Sdf.CopySpec + RemovePrim to reparent (USD has no native "
                    "MovePrim that preserves composition arcs reliably), then "
                    "bakes the original world transform onto each child as "
                    "translate/rotate/scale ops so visual position is unchanged. "
                    "CAVEATS: Composition-heavy prims (references, payloads, "
                    "variant selections) ARE reparented but their original spec on "
                    "the source layer is removed — undo via the snapshot system if "
                    "needed. Joints/articulations whose body0/body1 relationship "
                    "targets the OLD path will break — fix relationships separately "
                    "or group at the articulation root level. The new group is "
                    "always type Xform; if you need a Scope or other typeless "
                    "container, follow up with set_prim_metadata."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to reparent, e.g. ['/World/Cube_a', '/World/Cube_b'].",
                        },
                        "group_name": {
                            "type": "string",
                            "description": "Name of the new Xform group (leaf name, no slashes), e.g. 'Boxes', 'RedTeam'.",
                        },
                        "group_parent": {
                            "type": "string",
                            "description": "Parent path under which the group is created. Default '/World'.",
                        },
                    },
                    "required": ["prim_paths", "group_name"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "duplicate_prims",
                "description": (
                    "WHAT: Duplicate each given prim once (via Sdf.CopySpec for "
                    "fidelity — references, payloads, applied schemas, attributes "
                    "all preserved) and apply a positional XYZ offset to each "
                    "copy. All copies created in one Sdf.ChangeBlock. "
                    "WHEN: Use to instance variations across a scene — duplicate a "
                    "row of pillars 1m apart, copy a fixture and shift it for "
                    "left/right hand setups, replicate a robot for two-arm "
                    "scenarios. For LARGE replication of ONE prim into a grid, "
                    "prefer the existing clone_prim tool (uses GPU-batched "
                    "GridCloner). duplicate_prims is for replicating a HETEROGENEOUS "
                    "list of prims with the same offset applied to each. "
                    "RETURNS: {type: 'code_patch', code, description, queued} — the "
                    "patch generates copy paths by appending '_copy' (e.g. "
                    "/World/Cube -> /World/Cube_copy); on collision it appends "
                    "'_copy2', '_copy3', etc. The copy's local translate op is "
                    "SET (or added) to original_translation + offset. Prints a "
                    "summary listing source -> destination pairs. "
                    "CAVEATS: Sdf.CopySpec copies the spec on the current edit "
                    "target only — references/payloads in OTHER layers carry over "
                    "as composition arcs but don't get a fresh spec. Joints/"
                    "articulation relationships pointing at the original prim are "
                    "NOT rewritten — duplicated robots end up sharing the same "
                    "joint targets unless you fix relationships afterward. Offset "
                    "is applied to LOCAL translate (not world); for world-space "
                    "offset under a non-identity parent, transform offset into "
                    "parent space first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "USD paths to duplicate, e.g. ['/World/Pillar_1', '/World/Pillar_2'].",
                        },
                        "offset": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "XYZ translation offset applied to every copy [dx, dy, dz] in stage units.",
                        },
                        "suffix": {
                            "type": "string",
                            "description": "Suffix appended to each duplicated prim's name. Default '_copy'. Numeric suffixes are auto-appended on naming collision.",
                        },
                    },
                    "required": ["prim_paths", "offset"],
                },
            },
        },

    # ── From feat/atomic-tier15-18-misc ─────────────────────────────────
    {
            "type": "function",
            "function": {
                "name": "get_viewport_camera",
                "description": (
                    "WHAT: Returns the USD prim path of the camera currently driving the active viewport "
                    "(via omni.kit.viewport.utility.get_active_viewport().camera_path). "
                    "WHEN: Use before set_viewport_camera to remember the previous camera, when the user asks "
                    "'which camera am I looking through?', or when an LLM tool needs to inspect the current "
                    "framing context (e.g. capture_viewport, vision_*) to reason about what the user sees. "
                    "RETURNS: {camera_path: str, viewport_id: str, resolution: [w, h]}. "
                    "CAVEATS: Read-only; does not switch cameras (use set_viewport_camera). Returns null camera_path "
                    "when the viewport is using its default freefly perspective camera that has no underlying USD prim."
                ),
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
                "name": "get_selected_prims",
                "description": (
                    "WHAT: Returns the list of USD prim paths the user has currently selected in the viewport / "
                    "Stage panel (via omni.usd.get_context().get_selection().get_selected_prim_paths()). "
                    "WHEN: Use whenever the user refers to 'this prim', 'the selected object', 'these', or asks to "
                    "operate on their selection. Pair with apply_api_schema, set_attribute, teleport_prim, "
                    "highlight_prim, etc. to act on the user's intent without forcing them to type prim paths. "
                    "RETURNS: {selected_paths: [str], count: int, primary: str|null}. primary is the most recently "
                    "clicked prim (last in the list). "
                    "CAVEATS: Empty list when nothing is selected — handle this gracefully. Selection lives in the "
                    "viewport, not the stage, so it resets on stage open/reload."
                ),
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
                "name": "highlight_prim",
                "description": (
                    "WHAT: Briefly flashes a prim in the viewport by drawing a colored bounding box / wire overlay "
                    "around it via omni.isaac.debug_draw (DebugDrawHelper.draw_lines + scheduled clear). "
                    "WHEN: Use to draw the user's attention to a specific prim — after creating something new, "
                    "when explaining what an OmniGraph node refers to, when surfacing collision/clearance violations, "
                    "or any time the LLM wants to say 'this one' visually. "
                    "ARGS: prim_path (USD path of prim to highlight); color (RGB list 0-1, default [1,1,0] yellow); "
                    "duration (seconds the highlight remains, default 2.0 — a future async task clears the lines). "
                    "RETURNS: code patch that draws the overlay, sent through approval queue. "
                    "CAVEATS: Requires omni.isaac.debug_draw extension loaded (always present in Isaac Sim). "
                    "If duration is very large the lines stay until the next stage reload."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path of the prim to highlight"},
                        "color": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "RGB color [r, g, b] in 0-1 range. Default: [1.0, 1.0, 0.0] (yellow)",
                        },
                        "duration": {
                            "type": "number",
                            "description": "Seconds to keep the highlight visible. Default: 2.0",
                        },
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "focus_viewport_on",
                "description": (
                    "WHAT: Frames a prim in the active viewport — selects it and triggers the 'frame selection' "
                    "command (omni.kit.viewport.utility.frame_viewport_selection) so the camera dollies/zooms to "
                    "fit the prim's bounding box on screen. "
                    "WHEN: Use when the user says 'show me X', 'zoom to the robot', 'focus on /World/Cube', or when "
                    "a tool result references a prim the user can't currently see. Great companion to highlight_prim. "
                    "ARGS: prim_path (USD path of prim to frame). "
                    "RETURNS: code patch that performs selection + frame, sent through approval queue. "
                    "CAVEATS: Operates on the active viewport only. Has no effect if the prim has no computable "
                    "bounding box (e.g. an empty Xform with no children); user will see the camera not move."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path of the prim to frame in view"},
                    },
                    "required": ["prim_path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "save_stage",
                "description": (
                    "WHAT: Saves the current root USD stage to disk via omni.usd.get_context().save_as_stage(path) "
                    "(or save_stage() if the path matches the existing root layer). Writes .usd / .usda / .usdc "
                    "based on file extension. "
                    "WHEN: Use when the user says 'save', 'save as', 'write the scene out', or before any operation "
                    "the user wants persisted across Kit restarts. Always confirm path with the user first if "
                    "overwriting an existing file. "
                    "ARGS: path (absolute filesystem path including extension; .usd/.usda/.usdc or omniverse:// URL). "
                    "RETURNS: code patch that performs the save, sent through approval queue. "
                    "CAVEATS: Save is synchronous — large stages can block Kit briefly. Writing into omniverse:// "
                    "requires Nucleus auth. Does NOT flatten references — sublayers remain external."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Filesystem path (.usd/.usda/.usdc) or omniverse:// URL where the stage will be written.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "open_stage",
                "description": (
                    "WHAT: Loads a USD file from disk into Kit as the new root stage via "
                    "omni.usd.get_context().open_stage(path). Replaces the current stage. "
                    "WHEN: Use when the user says 'open', 'load the scene', 'switch to file X.usd', or wants to "
                    "resume from a saved snapshot. Warn the user that unsaved work in the current stage will be "
                    "discarded. "
                    "ARGS: path (absolute filesystem path or omniverse:// URL to .usd/.usda/.usdc/.usdz file). "
                    "RETURNS: code patch that performs the open, sent through approval queue. "
                    "CAVEATS: This DESTROYS the in-memory current stage. Pair with save_stage first if the user "
                    "has unsaved changes. Loading is asynchronous in Kit — subsequent tool calls may run against a "
                    "still-loading stage; allow a tick before querying."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Filesystem path or omniverse:// URL to a .usd / .usda / .usdc / .usdz file.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "export_stage",
                "description": (
                    "WHAT: Exports the current stage to a non-USD format (FBX / OBJ / GLB / GLTF) via the "
                    "omni.kit.tool.asset_exporter extension (ExportContext + export_asset). Useful for handing "
                    "geometry off to DCC tools (Blender, Maya, Unity, Unreal) or web viewers. "
                    "WHEN: Use when the user says 'export as fbx/obj/glb', 'send to Blender', 'I need a glTF for my "
                    "web app', or any cross-tool handoff. Use save_stage instead if the target is just another USD. "
                    "ARGS: path (absolute output filesystem path including extension); format (fbx/obj/glb/gltf — "
                    "must match extension). "
                    "RETURNS: code patch that performs the export, sent through approval queue. "
                    "CAVEATS: Requires omni.kit.tool.asset_exporter extension loaded (use enable_extension if not). "
                    "FBX/OBJ lose USD-specific data (variants, layers, references). GLB/GLTF preserve PBR materials "
                    "but skin/skeleton support depends on the exporter version."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Output filesystem path including extension."},
                        "format": {
                            "type": "string",
                            "enum": ["fbx", "obj", "glb", "gltf"],
                            "description": "Output format. Must match the file extension in path.",
                        },
                    },
                    "required": ["path", "format"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "list_opened_stages",
                "description": (
                    "WHAT: Lists every USD stage Kit currently has open across all UsdContext instances "
                    "(via omni.usd.get_context_names() + each context's get_stage_url()). Kit can hold "
                    "multiple parallel stages — main scene, preview, library — this returns all of them. "
                    "WHEN: Use when the user asks 'what scenes are loaded?', 'switch to the other stage', or before "
                    "calling save_stage / open_stage to confirm which context will be affected. "
                    "RETURNS: {stages: [{context_name: str, stage_url: str|null, prim_count: int, is_dirty: bool}], "
                    "active_context: str}. "
                    "CAVEATS: Most workflows only use the default '' context (single stage). Stage URL is null for "
                    "in-memory stages that have never been saved. is_dirty indicates unsaved changes."
                ),
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
                "name": "list_extensions",
                "description": (
                    "WHAT: Returns every Kit extension currently registered with the extension manager "
                    "(omni.kit.app.get_app().get_extension_manager().get_extensions()), including ID, version, "
                    "and enabled state. "
                    "WHEN: Use when the user asks 'is X loaded?', 'what version of replicator do I have?', when "
                    "diagnosing 'module not found' errors, or before recommending tools that depend on optional "
                    "extensions (asset_exporter, isaac_lab, ros2_bridge, etc.). "
                    "ARGS: enabled_only (bool, default false — when true, only return currently enabled extensions); "
                    "name_filter (optional substring to match against extension IDs, e.g. 'isaac', 'ros2'). "
                    "RETURNS: {extensions: [{id: str, version: str, enabled: bool, title: str}], total: int}. "
                    "CAVEATS: 'Registered' != 'Loaded' — the manager knows about hundreds of extensions but only "
                    "actually loads those marked enabled. Use enable_extension to activate a registered-but-disabled one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled_only": {
                            "type": "boolean",
                            "description": "If true, return only currently enabled extensions. Default: false",
                        },
                        "name_filter": {
                            "type": "string",
                            "description": "Optional case-insensitive substring filter on extension IDs.",
                        },
                    },
                    "required": [],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "enable_extension",
                "description": (
                    "WHAT: Enables a Kit extension at runtime via "
                    "omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(ext_id, True). "
                    "Loads the extension's Python modules and runs its on_startup hook. "
                    "WHEN: Use when a tool fails because an optional subsystem isn't loaded (e.g. enable "
                    "'omni.kit.tool.asset_exporter' before export_stage; 'omni.replicator.core' before configure_sdg; "
                    "'omni.isaac.ros2_bridge' before ROS2 OmniGraph nodes). Discover IDs with list_extensions. "
                    "ARGS: ext_id (full extension identifier as shown in list_extensions, e.g. "
                    "'omni.kit.tool.asset_exporter'). "
                    "RETURNS: code patch that performs the enable, sent through approval queue. "
                    "CAVEATS: Some extensions take seconds to load. If the extension is already enabled this is a "
                    "no-op. Enabling a broken/missing extension surfaces an error in the next get_console_errors call."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ext_id": {
                            "type": "string",
                            "description": "Full extension ID, e.g. 'omni.kit.tool.asset_exporter', 'omni.replicator.core'.",
                        },
                    },
                    "required": ["ext_id"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "create_audio_prim",
                "description": (
                    "WHAT: Creates a UsdMedia.SpatialAudio prim at a world-space position pointing at a sound file. "
                    "Spatial audio attenuates with distance from the listener (active camera) and supports HRTF / "
                    "stereo panning, enabling positional sound effects (machine hums, voice cues, etc.). "
                    "WHEN: Use when the user says 'add a sound at...', 'put a beep on the robot', 'play this wav "
                    "from the conveyor', or whenever the scene needs immersive 3D audio. "
                    "ARGS: position (world XYZ [x, y, z] for the audio source); audio_file (filesystem path or "
                    "omniverse:// URL to .wav / .mp3 / .ogg); prim_path (optional USD path, default "
                    "'/World/Audio_<n>'); start_time (optional seconds offset, default 0); auto_play (optional bool, "
                    "default true). "
                    "RETURNS: code patch that defines the SpatialAudio prim and sets its asset path / translate ops, "
                    "sent through approval queue. "
                    "CAVEATS: Requires Kit's audio extension (omni.audioplayer or omni.usd.audio) loaded. Some Kit "
                    "builds disable spatial audio by default — use enable_extension('omni.audioplayer') first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "position": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "World-space XYZ position of the audio source [x, y, z].",
                        },
                        "audio_file": {
                            "type": "string",
                            "description": "Filesystem path or omniverse:// URL to .wav / .mp3 / .ogg sound file.",
                        },
                        "prim_path": {
                            "type": "string",
                            "description": "USD path for the new audio prim. Default: '/World/Audio_<index>'.",
                        },
                        "start_time": {
                            "type": "number",
                            "description": "Seconds offset into the audio file at which to begin playback. Default: 0.0",
                        },
                        "auto_play": {
                            "type": "boolean",
                            "description": "If true, start playing as soon as the timeline plays. Default: true",
                        },
                    },
                    "required": ["position", "audio_file"],
                },
            },
        },
    {
            "type": "function",
            "function": {
                "name": "set_audio_property",
                "description": (
                    "WHAT: Sets one playback property on an existing SpatialAudio prim — gain (volume in dB), "
                    "pitch (playback rate multiplier), or attenuation parameters (start/end distance for the "
                    "distance roll-off curve). Maps friendly names to the underlying UsdMedia.SpatialAudio attrs. "
                    "WHEN: Use when the user says 'turn the sound down', 'make it quieter', 'pitch it up an octave', "
                    "'this should fade out beyond 5m', or any other tweak to an existing audio prim from "
                    "create_audio_prim. "
                    "ARGS: prim_path (USD path to a SpatialAudio prim); prop (one of 'volume' / 'gain' / 'pitch' / "
                    "'attenuation_start' / 'attenuation_end' / 'auto_play' / 'start_time'); value (number for "
                    "numeric props, bool for auto_play). "
                    "RETURNS: code patch that sets the corresponding USD attribute, sent through approval queue. "
                    "CAVEATS: 'volume' and 'gain' both map to the gain attribute (decibels — 0 = unchanged, "
                    "negative = quieter, positive = louder). Pitch is a multiplier (1.0 = unchanged, 2.0 = +octave). "
                    "Attenuation distances are in stage units (typically meters). Unknown prop names are rejected."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prim_path": {"type": "string", "description": "USD path to an existing SpatialAudio prim."},
                        "prop": {
                            "type": "string",
                            "enum": [
                                "volume",
                                "gain",
                                "pitch",
                                "attenuation_start",
                                "attenuation_end",
                                "auto_play",
                                "start_time",
                            ],
                            "description": "Property name to set.",
                        },
                        "value": {
                            "description": "New value — number for volume/gain/pitch/attenuation/start_time, bool for auto_play.",
                        },
                    },
                    "required": ["prim_path", "prop", "value"],
                },
            },
        },

]
