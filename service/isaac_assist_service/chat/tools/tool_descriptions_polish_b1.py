"""Batch 1 polish — 47 tools.

Rich WHAT/WHEN/RETURNS/CAVEATS descriptions for the first batch of Isaac Assist
tools. Apply alongside POLISH from tool_descriptions_polish.py at MCP server
startup or import-time.
"""

POLISH_B1 = {
    # ── USD Basics ─────────────────────────────────────────────────────────────
    "create_prim": (
        "WHAT: Create a new USD prim (Cube, Sphere, Cylinder, Cone, Mesh, Xform, Camera, DistantLight, DomeLight) at a stage path with optional position/scale/rotation. "
        "WHEN: spawning primitive geometry for tests, adding an Xform parent before referencing an asset, dropping in a placeholder camera/light. "
        "RETURNS: code patch for approval; produces a new prim at prim_path with the requested type and transform. "
        "CAVEATS: position in stage units (typically meters), rotation_euler in degrees [rx,ry,rz]. "
        "For external USD/URDF use add_reference or import_robot. "
        "For bulk grids use clone_prim with count>=4 (GPU cloner)."
    ),
    "clone_prim": (
        "WHAT: Duplicate a prim to a new path or create a grid of N copies; for count>=4 uses GPU-batched isaacsim.core.cloner.GridCloner with optional collision filtering. "
        "WHEN: creating RL env grids (256 parallel Frankas), instancing many bins/boxes for SDG, copying a tuned prim to a new location. "
        "RETURNS: code patch for approval; produces target_path (single) or target_path/Env_0..N-1 (grid). "
        "CAVEATS: spacing in meters; collision_filter=true REQUIRED for IsaacLab/RL so envs don't collide with each other. "
        "For count<4 falls back to Sdf.CopySpec (no GPU). "
        "Source prim must exist — use list_all_prims to verify. "
        "Doesn't deep-copy referenced assets (shared, not flattened)."
    ),
    "run_usd_script": (
        "WHAT: Execute arbitrary Python inside the Kit process with full access to omni.usd, pxr, omni.kit.commands, and Isaac Sim APIs. "
        "WHEN: an operation that no other tool covers (custom traversal, exotic schema, multi-step transaction), prototyping a new pattern, scripted batch ops. "
        "RETURNS: code patch for approval; runs in-process and prints stdout to the chat. "
        "CAVEATS: highest blast radius — prefer a typed tool (set_attribute, batch_apply_operation, apply_api_schema) when one exists. "
        "Errors abort the whole script. "
        "No sandbox: filesystem and network are reachable. "
        "Always supply a clear 'description' so the user can reason about the patch before approving."
    ),

    # ── Deformable / Soft Body ────────────────────────────────────────────────
    "create_deformable_mesh": (
        "WHAT: Convert an existing Mesh prim into a PhysX deformable soft body via preset (cloth, sponge, rubber, gel, rope) with optional Young's modulus / Poisson ratio / damping overrides. "
        "WHEN: simulating tablecloths, foam packing, tearable bags, surgical tissue, pickable rope/cable. "
        "RETURNS: code patch for approval; applies PhysxDeformableBodyAPI + collision schemas with preset values. "
        "CAVEATS: youngs_modulus in Pa (cloth ~1e5, rubber ~1e7, sponge ~1e4), poissons_ratio 0-0.49, damping unitless. "
        "Requires GPU PhysX and a closed/manifold mesh — run check_collision_mesh first. "
        "For rigid bodies use apply_api_schema with PhysicsRigidBodyAPI."
    ),

    # ── OmniGraph ─────────────────────────────────────────────────────────────
    "create_omnigraph": (
        "WHAT: Build an OmniGraph (action_graph / push_graph / lazy_graph) from a node list, connection list, and initial attribute values. "
        "WHEN: wiring ROS2 publishers/subscribers, sensor pipelines (camera→ROS2 image), controller graphs, tick logic. "
        "RETURNS: code patch for approval; creates the graph prim, instantiates nodes, sets values, connects ports. "
        "CAVEATS: action_graph for trigger flows, push_graph for every-frame, lazy_graph for on-demand. "
        "Node 'type' is the OmniGraph identifier (e.g. omni.graph.action.OnPlaybackTick). "
        "Connections are 'node.outPort' → 'node.inPort'. "
        "For ROS2, prefer canned helpers (publish_robot_description, set_motion_policy) when available."
    ),

    # ── Sensors ───────────────────────────────────────────────────────────────
    "add_sensor_to_prim": (
        "WHAT: Attach a sensor (camera, rtx_lidar, imu, contact_sensor, effort_sensor) to a prim, optionally configured from a real product spec via product_name. "
        "WHEN: mounting a wrist camera, adding a lidar to an AMR, instrumenting a foot for contact, attaching an IMU to a base link. "
        "RETURNS: code patch for approval; creates the sensor prim under prim_path with intrinsics applied. "
        "CAVEATS: fov in degrees, range [min,max] in meters, fps in Hz. "
        "If product_name given, intrinsics are auto-resolved from the spec DB (use lookup_product_spec to preview). "
        "rtx_lidar requires the RTX renderer enabled. "
        "For just a USD camera prim with no sensor wiring use create_prim with type='Camera'."
    ),
    "lookup_product_spec": (
        "WHAT: Fuzzy-search a real-world sensor/camera product (RealSense D435i, Velodyne VLP-16, ZED 2i, etc.) for its FOV, resolution, range, FPS, and other datasheet specs. "
        "WHEN: before add_sensor_to_prim to preview specs, comparing two cameras, validating sim sensor matches a physical device. "
        "RETURNS: {found: bool, spec: {product, type, fov, resolution, range, fps, ...}, alternatives: [...]} or {found: false, suggestions: [...]} for near-misses. "
        "CAVEATS: substring/word match against product DB — typos may need manual lookup. "
        "Returns datasheet values, not measured ones. "
        "Use add_sensor_to_prim with the same product_name to apply specs directly."
    ),

    # ── Scripting / Diagnostics ────────────────────────────────────────────────
    "explain_error": (
        "WHAT: LLM-side diagnosis of an Isaac Sim/PhysX/USD error string with suggested fixes (no Kit execution). "
        "WHEN: user pastes a stack trace or warning, you need a quick interpretation before deciding which fix tool to call. "
        "RETURNS: free-form natural-language explanation produced inline by the LLM (no tool result payload). "
        "CAVEATS: explanatory only — no code patch is produced; pair with fix_error to get an actual patch, or with run_stage_analysis to scan the live stage. "
        "For PhysX-specific log filtering use get_physics_errors instead."
    ),
    "capture_viewport": (
        "WHAT: Take a screenshot of the active viewport and return it as a base64-encoded PNG. "
        "WHEN: 'show me the scene', visual confirmation after a build, attaching an image to a chat reply, feeding the vision_* tools. "
        "RETURNS: {image_b64: str, width: int, height: int, mime: 'image/png'}. "
        "CAVEATS: max_dim caps the longest side in pixels (default 1280) — image is downscaled to fit. "
        "Captures whatever camera is currently active; switch first with set_viewport_camera. "
        "For per-camera captures or RTX/path-traced output use capture_camera_image (Tier 7) instead."
    ),

    # ── Replicator / SDG ──────────────────────────────────────────────────────
    "configure_sdg": (
        "WHAT: Configure a Replicator SDG run with annotators (rgb, bounding_box_2d, semantic/instance_segmentation, distance_to_camera, normals), num_frames, output_dir, resolution. "
        "WHEN: 'generate 1000 labeled images for perception training', quick SDG setup without per-component wiring. "
        "RETURNS: code patch for approval; sets up writer, render product, annotators, runs orchestrator until num_frames. "
        "CAVEATS: resolution [width,height] in pixels. "
        "For richer setups (COCO/KITTI writer, randomizers, async export) use create_sdg_pipeline + add_domain_randomizer + export_dataset. "
        "Long runs at 4K can produce 10s of GB."
    ),

    # ── ROS2 (live via rosbridge) ─────────────────────────────────────────────
    "ros2_connect": (
        "WHAT: Configure or verify the rosbridge WebSocket connection (default 127.0.0.1:9090) used for all ros2_* live tools. "
        "WHEN: pointing at a remote rosbridge, switching ports, sanity-checking connectivity before publish/subscribe. "
        "RETURNS: {connected: bool, ip, port, ros_version} or {error}. "
        "CAVEATS: rosbridge_server must be running on the target host (`ros2 launch rosbridge_server rosbridge_websocket_launch.xml`). "
        "Connection is per-process and persists for subsequent ros2_* calls. "
        "DOMAIN_ID matching is the bridge's responsibility, not this tool's."
    ),
    "ros2_list_topics": (
        "WHAT: List all currently active ROS2 topics with their message types (live snapshot from rosbridge). "
        "WHEN: 'is /joint_states publishing?', verifying OmniGraph ROS2 nodes after building, discovering what a robot exposes. "
        "RETURNS: list of {topic: str, type: 'pkg/msg/Type'}. "
        "CAVEATS: requires rosbridge running (call ros2_connect first). "
        "Only shows topics that have at least one publisher OR subscriber — silent topics won't appear. "
        "For a single topic's type use ros2_get_topic_type."
    ),
    "ros2_get_message_type": (
        "WHAT: Get the full field structure of a ROS2 message type (e.g. 'geometry_msgs/Twist' → linear.{x,y,z}, angular.{x,y,z}). "
        "WHEN: building a payload for ros2_publish, decoding a ros2_subscribe_once result, validating field names. "
        "RETURNS: {message_type, fields: [{name, type, is_array}]} describing the IDL schema. "
        "CAVEATS: requires rosbridge with the message package available on its rclpy side. "
        "For getting which type a topic uses use ros2_get_topic_type. "
        "Slash form 'pkg/Msg' and 'pkg/msg/Msg' both accepted."
    ),
    "ros2_subscribe_once": (
        "WHAT: Subscribe to a topic, return the first message received within timeout, then unsubscribe. "
        "WHEN: 'show me one /joint_states reading', confirming a publisher is alive, capturing a snapshot for further analysis. "
        "RETURNS: {topic, msg_type, data: {...message fields...}, received_at: timestamp} or {error: 'timeout'}. "
        "CAVEATS: timeout in seconds (default 5.0). "
        "Returns the FIRST message — for sustained streams handle the data in your code separately. "
        "msg_type must match what the publisher uses exactly (use ros2_get_topic_type to confirm). "
        "For repeated polling, call multiple times rather than holding a subscription."
    ),
    "ros2_publish": (
        "WHAT: Publish a single ROS2 message to a topic with the given payload. "
        "WHEN: one-shot commands ('send /cmd_vel zero', 'trigger /reset'), testing a subscriber, sending a one-time JointState. "
        "RETURNS: {published: bool, topic, msg_type} or {error}. "
        "CAVEATS: requires rosbridge. "
        "data must match the message schema (use ros2_get_message_type to inspect). "
        "For diff_drive controllers the message must be RECEIVED while the controller's update window is open — use ros2_publish_sequence with rate_hz>0 instead. "
        "Numbers in JSON are floats by default; bool/int may need explicit casting on the receiver."
    ),
    "ros2_publish_sequence": (
        "WHAT: Publish a sequence of messages each held for a duration, optionally at a continuous rate_hz (required for diff_drive and other latched controllers). "
        "WHEN: driving a robot ('forward 2s, stop 1s, turn 1s'), playing back a recorded /cmd_vel trajectory, smooth velocity ramps. "
        "RETURNS: {published_count: int, total_duration_s: float, topic, msg_type}. "
        "CAVEATS: durations in seconds (one per message in the list). "
        "rate_hz=0 publishes once per duration step (good for latched topics); rate_hz>0 republishes at that Hz throughout each duration (REQUIRED for diff_drive_controller, default 10). "
        "Total wall time = sum(durations); blocks until done. "
        "For one-shot use ros2_publish."
    ),
    "ros2_call_service": (
        "WHAT: Call a ROS2 service synchronously with a JSON request and return the response. "
        "WHEN: invoking /reset_world, calling MoveIt2 plan service, querying a parameter server, triggering Nav2 actions. "
        "RETURNS: {service_name, response: {...}, success: bool} or {error: 'timeout' | 'service_not_found'}. "
        "CAVEATS: timeout in seconds (default 5.0). "
        "request={} for parameterless services like std_srvs/srv/Empty. "
        "service_type must be exact ('std_srvs/srv/Empty', not 'std_srvs/Empty'). "
        "For discovering services use ros2_list_services; for fields use ros2_get_message_type-style introspection on the .srv."
    ),

    # ── Knowledge Retrieval ───────────────────────────────────────────────────
    "lookup_knowledge": (
        "WHAT: Hybrid FTS + code-pattern search over the version-specific Isaac Sim knowledge base (docs, API patterns, snippets). "
        "WHEN: 'how do I create an OmniPBR material in 5.1?', verifying API names changed between Isaac versions, finding a tested snippet before writing run_usd_script. "
        "RETURNS: {version, query, results: [{source, section, content} | {source: 'code_patterns', title, code, note}], count}. "
        "CAVEATS: detects Isaac version automatically — results are version-pinned. "
        "Content is truncated to 600 chars per result. "
        "For runtime errors use explain_error or fix_error instead — those reason about specific messages, this returns reference docs."
    ),

    # ── Motion Planning (RMPflow / Lula) ──────────────────────────────────────
    "move_to_pose": (
        "WHAT: Plan and execute a collision-free end-effector motion to a target world pose via RMPflow (reactive) or Lula RRT (global). "
        "WHEN: 'move arm to grasp pose', single-target manipulation, smooth point-to-point motion. "
        "RETURNS: code patch for approval; loads planner, sets target, runs steps until convergence. "
        "CAVEATS: target_position [x,y,z] in meters; target_orientation quaternion [w,x,y,z] (omit to keep current). "
        "Requires generate_robot_description (only ~9 robots: franka, ur10/5e/3e, cobotta, rs007n, dofbot, kawasaki, flexiv_rizon). "
        "rmpflow=fast/reactive, lula_rrt=slower but obstacle-aware. "
        "For multi-waypoint use plan_trajectory."
    ),
    "plan_trajectory": (
        "WHAT: Plan (without executing) a multi-waypoint joint trajectory through an ordered list of target poses. "
        "WHEN: 'plan a pick-place sequence with 3 waypoints', generating a trajectory to inspect or send to a controller, dry-running before commitment. "
        "RETURNS: code patch for approval; produces {trajectory: [[joint_pos]...], times: [s], waypoints_reached: int}. "
        "CAVEATS: each waypoint is {position: [x,y,z], orientation: [w,x,y,z] optional}. "
        "Doesn't actuate joints — use move_to_pose for execute, or send the returned trajectory through your own controller. "
        "Same robot_type prerequisites as move_to_pose."
    ),

    # ── Asset Catalog ─────────────────────────────────────────────────────────
    "catalog_search": (
        "WHAT: Fuzzy-rank assets in the local Isaac Sim asset library by name/tags/path with optional asset_type filter (robot/prop/environment/sensor/material/any). "
        "WHEN: 'find a Franka', 'list all warehouse shelves', resolving a natural-language object name to an importable USD before generate_scene_blueprint. "
        "RETURNS: {query, results: [{name, path, type, tags, ...}], total_matches, index_size}. "
        "CAVEATS: limit defaults to 10. "
        "Searches LOCAL catalog only — for browsing Nucleus servers use nucleus_browse, then download_asset to populate the catalog. "
        "Score: exact-name=100 > all-words-present > partial-match."
    ),

    # ── IsaacLab RL ───────────────────────────────────────────────────────────
    "create_isaaclab_env": (
        "WHAT: Scaffold an IsaacLab ManagerBasedRLEnv config (obs/action/reward terms) for a robot, given task_type (manipulation/locomotion/navigation/custom). "
        "WHEN: starting a new RL task, converting a manual scene into a trainable env, prototyping reward terms. "
        "RETURNS: {type: 'isaaclab_env', task_name, config: {...}, generated_code: '...EnvCfg python...', instructions}. "
        "CAVEATS: produces config code, not patches — review before running. "
        "Defaults: 64 envs, 2.0m spacing, episode_length=500, dt=1/120s, decimation=2. "
        "Then call launch_training to start. "
        "For LLM-evolved rewards use generate_reward + iterate_reward instead."
    ),
    "launch_training": (
        "WHAT: Launch an IsaacLab RL training subprocess for a task with PPO/SAC/TD3/RSL_RL on N parallel envs for num_steps. "
        "WHEN: starting training after create_isaaclab_env or running a built-in task ('Isaac-Reach-Franka-v0', 'Isaac-Velocity-Anymal-C-v0'). "
        "RETURNS: code patch for approval; spawns the training subprocess and writes checkpoints to checkpoint_dir. "
        "CAVEATS: num_steps is total timesteps (default 1M); converted internally to max_iterations = steps/(envs*horizon). "
        "PPO/RSL_RL use rsl_rl runner; SAC/TD3 use skrl. "
        "Default checkpoint_dir is workspace/rl_checkpoints/<task>. "
        "For Arena-based eval after training use run_arena_benchmark with the checkpoint path."
    ),

    # ── Vision (Gemini Robotics-ER) ───────────────────────────────────────────
    "vision_detect_objects": (
        "WHAT: Use Gemini Robotics-ER to detect and locate objects in the current viewport, returning normalized 2D points and labels (optionally filtered to a list of class names). "
        "WHEN: 'what objects are visible', 'find the red cube', counting items on a table for SDG ground truth. "
        "RETURNS: {detections: [{label, point: [x,y] normalized 0-1}], count, model}. "
        "CAVEATS: requires viewport image (auto-captured). "
        "Points are normalized to [0,1] image coords, NOT 3D world coords — back-project via raycast for world positions. "
        "For pixel bounding boxes use vision_bounding_boxes. "
        "max_objects default 10."
    ),
    "vision_bounding_boxes": (
        "WHAT: Use Gemini Robotics-ER to return 2D axis-aligned bounding boxes for visible objects from the viewport. "
        "WHEN: producing labels for a perception eval, finding click-targets, generating SDG annotations from a screenshot. "
        "RETURNS: {bounding_boxes: [{label, box: [ymin, xmin, ymax, xmax]}], count, model}. "
        "CAVEATS: box coords are normalized 0-1000 (Gemini convention), NOT 0-1; convert before pixel use. "
        "max_objects default 25. "
        "For just centroid points use vision_detect_objects (cheaper). "
        "For 3D boxes use the SDG annotator 'bounding_box_3d' via configure_sdg."
    ),
    "vision_plan_trajectory": (
        "WHAT: Use Gemini Robotics-ER to plan a 2D image-space trajectory for a natural-language pick-and-place style instruction. "
        "WHEN: 'move the red pen to the organizer on the left', visual demonstrations for behavior cloning, drawing a path overlay for an operator. "
        "RETURNS: {trajectory: [[x,y]...], num_points, model} with normalized image coords. "
        "CAVEATS: 2D image trajectory only — back-project to world via depth/raycast for actual robot motion. "
        "num_points default 15; more = smoother but slower. "
        "For executing the plan on a robot use move_to_pose / plan_trajectory after world-space conversion."
    ),
    "vision_analyze_scene": (
        "WHAT: Free-form spatial reasoning over the viewport via Gemini Robotics-ER (any natural-language question). "
        "WHEN: 'what's blocking the gripper?', 'how full is this bin?', 'describe the workspace layout for a writeup'. "
        "RETURNS: {analysis: '<free-text response>', model}. "
        "CAVEATS: text-only output — no structured detections. "
        "Use vision_detect_objects/vision_bounding_boxes when you need parseable coords. "
        "Quality depends on viewport content and lighting; capture_viewport first to verify what the model will see."
    ),

    # ── Nucleus Browse & Download ─────────────────────────────────────────────
    "nucleus_browse": (
        "WHAT: List files and folders at a path on an Omniverse Nucleus server (default omniverse://localhost), via Kit's omni.client. "
        "WHEN: exploring NVIDIA's Isaac Sim asset library, finding available robots/environments under /NVIDIA/Assets/Isaac/5.1, discovering custom team assets. "
        "RETURNS: {status, path, items: [{name, size, is_folder, modified_time}], count}. "
        "CAVEATS: requires Isaac Sim running with Nucleus reachable. "
        "Path strictly sanitized (alphanumeric + /._:-) to avoid injection. "
        "limit capped at 200. "
        "To pull an asset to local disk use download_asset; to search the LOCAL catalog use catalog_search."
    ),
    "download_asset": (
        "WHAT: Download a Nucleus URL to local Desktop/assets and register it in the local asset catalog under a category (robot/prop/scene/sensor/material). "
        "WHEN: pulling a Franka USD from Nucleus to use offline, populating the local catalog so catalog_search returns it, archiving a custom asset locally. "
        "RETURNS: {downloaded: bool, local_path, category, registered: bool, size_bytes}. "
        "CAVEATS: requires Isaac Sim running. "
        "local_subdir auto-derived from Nucleus path if omitted; category auto-detected from path keywords. "
        "Re-downloads overwrite existing files. "
        "After download the asset is importable via import_robot or add_reference using the local path."
    ),

    # ── Scene Export ──────────────────────────────────────────────────────────
    "export_scene_package": (
        "WHAT: Bundle all approved code patches from a chat session into a reusable file package: scene_setup.py + README.md + ros2_topics.yaml + ros2_launch.py (if ROS2 nodes detected). "
        "WHEN: user asks to 'export', 'save the scene files', 'generate a runnable script', archiving a working setup for teammates. "
        "RETURNS: {output_dir, files: [...], patch_count, ros2_topics: [...]}; written under workspace/scene_exports/<scene_name>/. "
        "CAVEATS: only includes successful patches from the audit log for session_id. "
        "Falls back to all sessions if the session has none. "
        "Re-running overwrites the output directory. "
        "For just the current stage as USD use Isaac Sim's File > Save instead."
    ),

    # ── Stage Analysis ────────────────────────────────────────────────────────
    "run_stage_analysis": (
        "WHAT: Run the Stage Analyzer (8 validator packs: schema_consistency, import_health, material_physics, articulation_integrity, sensor_completeness, ros_bridge_readiness, performance_warnings, isaaclab_sanity). "
        "WHEN: 'diagnose the scene', pre-flight before sim play, 'what's wrong with my robot import'. "
        "RETURNS: {total_findings, summary: {severity: count}, findings: [{rule, severity, prim, message, fix_hint}], truncated}. "
        "CAVEATS: requires Kit RPC alive. "
        "Findings capped at 50 (truncated flag set if more). "
        "packs param filters which packs run (omit for all). "
        "For specific errors use fix_error/explain_error; for collision-mesh deep-dive use check_collision_mesh."
    ),

    # ── From feat/tools-and-bugfixes ──────────────────────────────────────────
    "batch_apply_operation": (
        "WHAT: Apply an operation (apply_physics, apply_collision, set_material, delete, set_visibility, set_attribute) to ALL descendants of target_path, optionally filtered by USD prim type. "
        "WHEN: 'add physics to all meshes under /World/Objects', bulk-delete placeholders, recolor a category, hide/show a folder. "
        "RETURNS: code patch for approval; prints '<count> prims affected' on execute. "
        "CAVEATS: traverses entire descendant tree — limit with filter_type ('Mesh', 'Xform'). "
        "params: set_material needs {material_path}, set_visibility {visible: bool}, set_attribute {attr_name, value}, apply_physics optionally {mass}. "
        "For single-prim ops use the typed tools (set_attribute, apply_api_schema)."
    ),
    "get_physics_errors": (
        "WHAT: Filter the recent Kit console log down to PhysX-specific errors and warnings (regex + source name match). "
        "WHEN: 'why is physics broken', after a sim crash, before/after applying check_collisions to confirm the fix. "
        "RETURNS: {physics_errors: [{level, msg, source, timestamp}], total_count, note}. "
        "CAVEATS: last_n caps returned entries (default 20). "
        "Filters are PhysX-only — for general errors use get_console_errors. "
        "Pair with explain_error or fix_error to get a suggested patch for a specific error string."
    ),
    "fix_error": (
        "WHAT: Look up a known fix pattern in the knowledge base for a physics/USD error string and generate a corrective code patch. "
        "WHEN: console shows 'CollisionAPI missing', 'solver iterations too low', 'rootJoint missing', 'OmniGraph compute failed' — anything where a canned fix exists. "
        "RETURNS: code patch for approval implementing the fix. "
        "CAVEATS: only handles patterns in the fix DB — unknown errors return a generic 'no fix found' patch. "
        "For free-form interpretation without a patch use explain_error. "
        "For a stage-wide scan that finds AND suggests fixes use run_stage_analysis."
    ),

    # ── SDG v2 (Replicator) ───────────────────────────────────────────────────
    "create_sdg_pipeline": (
        "WHAT: Build a full Replicator pipeline (camera + render product + annotators + writer) generating omni.replicator.core code. "
        "WHEN: setting up labeled data generation with COCO/KITTI/Basic/NumPy output, attaching multiple annotators (bbox 2d/3d, semantic/instance seg, depth, normals, occlusion). "
        "RETURNS: code patch for approval; produces an end-to-end SDG script. "
        "CAVEATS: camera_position/look_at in stage units (default [0,0,5] looking at origin); resolution [w,h] default [1280,720]. "
        "Generates code only — call preview_sdg first (small N) to sanity-check, then export_dataset to run async without freezing UI. "
        "For domain randomization add add_domain_randomizer separately."
    ),
    "add_domain_randomizer": (
        "WHAT: Append a Replicator domain randomizer (pose, texture, lighting, color, material_properties, visibility) targeting a prim path pattern. "
        "WHEN: randomizing poses for sim2real, jittering textures/colors/lighting per frame, varying material roughness/metallic. "
        "RETURNS: code patch for approval; appends a rep.randomizer.* call to the active pipeline. "
        "CAVEATS: target is a regex-style path pattern (e.g. '/World/Objects/.*'). "
        "params per type: pose {min_angle, max_angle, surface_prim}; color {color_min:[r,g,b], color_max}; lighting {intensity_min/max}; material {roughness/metallic_min/max}; visibility {probability}. "
        "Must be called AFTER create_sdg_pipeline."
    ),
    "preview_sdg": (
        "WHAT: Step the active Replicator pipeline a few times via rep.orchestrator.step() to generate sample frames without blocking the Kit UI. "
        "WHEN: sanity-checking annotator output, verifying randomizers fire, looking at a few frames before committing to an N=10000 export. "
        "RETURNS: code patch for approval; writes num_samples (default 3) frames to the configured output_dir. "
        "CAVEATS: requires create_sdg_pipeline to have been called this session. "
        "For full async generation use export_dataset (yields to UI periodically)."
    ),
    "export_dataset": (
        "WHAT: Run the configured Replicator pipeline for num_frames in step batches, yielding to the Kit UI between batches to avoid freezing. "
        "WHEN: kicking off the actual labeled-dataset run after preview_sdg looked good. "
        "RETURNS: code patch for approval; writes all frames + annotations to output_dir. "
        "CAVEATS: step_batch=10 frames per yield by default — tune up for speed or down for UI responsiveness. "
        "Long runs at high resolution can fill disk fast (10s of GB). "
        "Currently no resume — interruption means re-running. "
        "For preview use preview_sdg."
    ),

    # ── Teleoperation (XR) ────────────────────────────────────────────────────
    "start_teleop_session": (
        "WHAT: Start an XR teleop session: open WebSocket bridge, attach watchdog physics callback for joint commands, start viewport stream, return XR client URL. "
        "WHEN: bringing up Quest 3 / Vision Pro / Spacemouse / keyboard control for demonstration capture. "
        "RETURNS: code patch for approval; on execute returns {connection_url, session_id, stream_quality}. "
        "CAVEATS: input_device default 'keyboard'; stream_quality 'low'|'medium'(default)|'high'. "
        "Watchdog defaults to 500ms (tune via teleop_safety_config). "
        "Robot must have ArticulationRootAPI. "
        "Pair with configure_teleop_mapping, record_teleop_demo, stop_teleop_session."
    ),
    "configure_teleop_mapping": (
        "WHAT: Bind XR input device axes (left_x, right_y, trigger_left, etc.) to robot joint names with position/velocity gain multipliers. "
        "WHEN: after start_teleop_session, mapping a Quest controller to Franka's 7 joints, customizing axis-to-joint per task. "
        "RETURNS: code patch for approval. "
        "CAVEATS: device_axes and joint_names are positional (axes[i] → joints[i]), so list lengths must match. "
        "gains.{position,velocity} are unitless multipliers applied to raw axis values. "
        "Re-call to overwrite the mapping mid-session."
    ),
    "record_teleop_demo": (
        "WHAT: Record a teleop demonstration to an HDF5 file in robomimic-compatible schema (joint positions, velocities, end-effector poses) via a physics callback. "
        "WHEN: capturing demonstrations for behavior cloning or GR00T fine-tuning, building a LeRobot v2-style dataset. "
        "RETURNS: code patch for approval; writes to output_path while session is active. "
        "CAVEATS: frequency_hz default 30 (matches typical RL/BC training rate). "
        "Recording stops on stop_teleop_session, which finalizes the HDF5. "
        "For LeRobot v2 conversion use a downstream converter; this writes the robomimic schema. "
        "Pair with finetune_groot to train on the captured data."
    ),
    "stop_teleop_session": (
        "WHAT: Cleanly stop the active XR teleop session: remove physics callback, zero joint velocities (safety), close WebSocket, stop stream, finalize any HDF5 recording. "
        "WHEN: user is done teleoperating, before switching to RL/policy execution, on session timeout. "
        "RETURNS: code patch for approval; on execute returns {stopped: bool, recording_path?: str, frames_recorded?: int}. "
        "CAVEATS: takes no args (operates on the active session). "
        "Joint velocities are zeroed for safety — robot will hold position. "
        "Calling without an active session is a no-op."
    ),
    "teleop_safety_config": (
        "WHAT: Configure teleop safety: watchdog_timeout_ms (zero velocity if no command received), max_joint_velocity cap (rad/s), workspace_limits AABB ({min:[x,y,z], max:[x,y,z]}). "
        "WHEN: hardening a teleop session before a demo, restricting end-effector reach to a safe workspace, throttling velocity for novice operators. "
        "RETURNS: code patch for approval. "
        "CAVEATS: watchdog_timeout_ms default 500. "
        "max_joint_velocity in rad/s applies to all joints. "
        "workspace_limits in meters world-space. "
        "Doesn't replace physical e-stop — software safety only. "
        "Apply AFTER start_teleop_session."
    ),

    # ── Arena ─────────────────────────────────────────────────────────────────
    "create_arena": (
        "WHAT: Create a composable IsaacLab-Arena environment by combining a scene_type (tabletop_pick_and_place / kitchen / galileo / custom), robot_asset, and task; registers it with gymnasium and returns env_id. "
        "WHEN: setting up a benchmarkable task across multiple robots, building a leaderboard-able env, swapping embodiments for ablations. "
        "RETURNS: code patch for approval; on execute returns {env_id: 'Arena-<Scene>-<Robot>-v0', num_envs}. "
        "CAVEATS: num_envs default 64; env_spacing 2.5m. "
        "Arena uses COMPILE-time composition — env is fixed after env.reset() (no dynamic scene mutation). "
        "For comparison variants use create_arena_variant. "
        "For non-benchmark RL use create_isaaclab_env instead."
    ),
    "create_arena_variant": (
        "WHAT: Spawn a variant of an existing Arena env with a different robot_asset; registers under a new env_id for side-by-side comparison. "
        "WHEN: comparing Franka vs UR10 on the same task, A/B testing embodiments, building a multi-robot benchmark sweep. "
        "RETURNS: code patch for approval; on execute returns {env_id, base_env_id, robot_asset}. "
        "CAVEATS: scene/task inherited from base_env_id — only the robot changes. "
        "Each variant is a separate gymnasium registration (isolated state). "
        "For aggregating results across variants use run_arena_benchmark per env_id then arena_leaderboard."
    ),
    "run_arena_benchmark": (
        "WHAT: Run an evaluation on an Arena env for num_episodes (default 100) with optional checkpoint policy, collecting metrics (success_rate, episode_length, etc.). "
        "WHEN: scoring a trained policy, generating numbers for arena_leaderboard, regression-testing after a code change. "
        "RETURNS: code patch for approval; spawns a separate IsaacLab subprocess and writes results JSON. "
        "CAVEATS: checkpoint omitted = random actions (sanity baseline). "
        "metrics list selects which to collect (default ['success_rate', 'episode_length']). "
        "Subprocess launches its own Isaac Sim instance — VRAM/GPU heavy. "
        "Feed result list to arena_leaderboard for tabular comparison."
    ),
    "arena_leaderboard": (
        "WHAT: Format an array of run_arena_benchmark results into a sorted leaderboard table (text + structured rows). "
        "WHEN: summarizing a multi-robot/multi-policy sweep, generating a comparison block for a report or chat reply. "
        "RETURNS: {leaderboard: '<formatted ASCII table>', entries: [{rank, env_id, robot, <metric cols>}], metric_columns: [...], count}. "
        "CAVEATS: sorts by success_rate descending if present, else first metric column. "
        "Pure formatter — does no benchmarking itself; pass in results from run_arena_benchmark calls. "
        "Empty results list returns {leaderboard: 'No results to display.'}."
    ),
}


def apply_polish(tools_list):
    """Apply polished descriptions to a list of tool dicts in-place.

    Args:
        tools_list: ISAAC_SIM_TOOLS list (each entry has 'function.name' and 'function.description')

    Returns:
        Number of descriptions replaced.
    """
    count = 0
    for tool in tools_list:
        try:
            name = tool["function"]["name"]
        except (KeyError, TypeError):
            continue
        if name in POLISH_B1:
            tool["function"]["description"] = POLISH_B1[name]
            count += 1
    return count
