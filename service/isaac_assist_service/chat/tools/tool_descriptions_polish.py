"""
tool_descriptions_polish.py
---------------------------
Rich tool descriptions for LLM tool selection accuracy.

Each entry uses the format: WHAT / WHEN / RETURNS / CAVEATS

Why: short descriptions make LLMs pick the wrong tool, miss tools that exist,
or misinterpret outputs. Rich descriptions reduce false-positive "missing
feature" reports during Phase 12 QA.

Apply at MCP server startup or import-time:

    from tool_descriptions_polish import POLISH
    for tool in ISAAC_SIM_TOOLS:
        name = tool["function"]["name"]
        if name in POLISH:
            tool["function"]["description"] = POLISH[name]
"""

POLISH = {
    # ── USD Basics (PR #1) ──────────────────────────────────────────────────
    "delete_prim": (
        "WHAT: Permanently remove a prim and all its descendants from the USD stage. "
        "WHEN: cleaning up after a failed import, deleting a placeholder before adding the real asset, "
        "removing test objects from a scene. "
        "RETURNS: code patch for approval; on execute, the prim and all children disappear from stage. "
        "CAVEATS: undoable with Ctrl+Z. "
        "Cannot delete the pseudo-root (/). "
        "Deleting a prim that's referenced elsewhere can break those references — use list_relationships first to check. "
        "Different from set_active_state(false) which only hides; this is permanent."
    ),
    "set_attribute": (
        "WHAT: Set the value of any USD attribute on a prim (position, scale, color, mass, custom properties). "
        "WHEN: tweaking a single property without recreating the prim, applying a parameter change, animating manually. "
        "RETURNS: code patch for approval. "
        "CAVEATS: attribute must already exist OR be auto-creatable from the value type. "
        "For transforms (xformOp:translate etc.), USD requires the op to be added to xformOpOrder — use teleport_prim instead for safety. "
        "Use bulk_set_attribute for many prims at once. "
        "Use set_keyframe to animate over time."
    ),
    "add_reference": (
        "WHAT: Add a USD reference from an external file to an existing prim. The referenced content appears under that prim. "
        "WHEN: loading a robot USD onto a placeholder, instancing the same asset multiple times, composing scenes from libraries. "
        "RETURNS: code patch for approval. "
        "CAVEATS: target prim must exist (use create_prim with type='Xform' first if needed). "
        "Reference path can be local (/path/to.usd), Nucleus (omniverse://), or relative. "
        "Use add_usd_reference for advanced options (layer offset, ref_prim_path, instanceable). "
        "Reference is composed in, not flattened — original asset edits propagate."
    ),
    "apply_api_schema": (
        "WHAT: Apply a USD API schema (mixin) to a prim, enabling that schema's attributes and behavior. "
        "WHEN: making a Mesh into a rigid body (RigidBodyAPI), enabling collisions (CollisionAPI), giving mass (MassAPI), "
        "marking as articulation root (ArticulationRootAPI), enabling self-collision (PhysxArticulationAPI). "
        "RETURNS: code patch for approval. "
        "CAVEATS: applying CollisionAPI without MeshCollisionAPI gives default approximation (often wrong). "
        "Some schemas REQUIRE others (e.g., RigidBodyAPI usually needs CollisionAPI to be useful). "
        "Use list_applied_schemas to check what's already applied."
    ),

    # ── Materials (PR #1) ───────────────────────────────────────────────────
    "create_material": (
        "WHAT: Create a new MDL material (OmniPBR, OmniGlass, OmniSurface) with specified PBR parameters. "
        "WHEN: making custom materials before assigning to prims, building a material library, matching real-world materials. "
        "RETURNS: code patch for approval; creates a Material prim with shader. "
        "CAVEATS: material is created but NOT assigned — call assign_material after to bind to a prim. "
        "OmniPBR for general use, OmniGlass for transparent, OmniSurface for advanced (subsurface, anisotropy). "
        "For physics-accurate friction/restitution, use lookup_material + apply_physics_material — NOT this tool."
    ),
    "assign_material": (
        "WHAT: Bind an existing Material prim to a target geometry prim, applying that material's appearance. "
        "WHEN: applying a created material to a mesh, swapping the look of an asset, materializing a placeholder. "
        "RETURNS: code patch for approval. "
        "CAVEATS: both prims must exist. "
        "Material applies to the entire prim including descendants by default. "
        "Visual material only — for physics properties (friction/restitution) also call apply_physics_material."
    ),

    # ── Simulation (PR #1) ──────────────────────────────────────────────────
    "sim_control": (
        "WHAT: Control PhysX simulation playback: play (start physics), pause, stop (reset to t=0), step (advance N frames), reset. "
        "WHEN: starting/pausing simulation from chat, single-stepping for debugging, resetting to initial state. "
        "RETURNS: code patch for approval; simulation state changes immediately on execute. "
        "CAVEATS: PhysX simulation, not USD timeline animation — use play_animation for timeline scrubbing. "
        "stop() resets all transforms to authored values. "
        "step requires a valid PhysicsScene prim. "
        "Some tools (RMPflow, motion planning) require sim to be playing — check with get_timeline_state."
    ),
    "set_physics_params": (
        "WHAT: Configure scene-level PhysX parameters: gravity vector, fixed timestep, solver iterations. "
        "WHEN: changing gravity for moon/zero-G sims, increasing solver iterations for stability with stiff joints, "
        "lowering timestep for fast-moving objects. "
        "RETURNS: code patch for approval. "
        "CAVEATS: dt × stiffness > 0.5 causes instability — for high stiffness, lower dt. "
        "More iterations = slower but more stable. "
        "RTX devices can handle dt=1/60; use 1/120 for fast motion. "
        "Affects PhysicsScene globally."
    ),
    "teleport_prim": (
        "WHAT: Atomically set a prim's world transform (position and/or rotation), bypassing physics. "
        "WHEN: placing objects at known positions, snapping a robot to a starting pose, repositioning between trials. "
        "RETURNS: code patch for approval. "
        "CAVEATS: instant translation — physics doesn't see the motion (no contact forces). "
        "For physics-driven placement, use apply_force or set_linear_velocity. "
        "For animated motion, use set_keyframe or motion planning. "
        "Position in stage units (typically meters), rotation as Euler degrees [rx,ry,rz]."
    ),

    # ── Joints (PR #1) ──────────────────────────────────────────────────────
    "set_joint_targets": (
        "WHAT: Set target position or velocity for articulated joints (used by drive controllers). "
        "WHEN: commanding a robot arm to a pose, setting wheel velocities, opening/closing a gripper. "
        "RETURNS: code patch for approval. "
        "CAVEATS: requires DriveAPI on each joint with reasonable kp/kd (use set_drive_gains). "
        "Position targets only effective for revolute/prismatic; spherical needs different approach. "
        "Robot must be articulation (ArticulationRootAPI on root). "
        "For full motion planning, use move_to_pose instead. "
        "For monitoring, use get_joint_positions / get_joint_torques."
    ),
    "get_articulation_state": (
        "WHAT: Read current state of a robot articulation (joint positions, velocities, joint names, dof count). "
        "WHEN: checking robot pose mid-simulation, monitoring training progress, debugging joint behavior. "
        "RETURNS: {joint_names: [...], positions: [rad], velocities: [rad/s], dof: N}. "
        "CAVEATS: requires sim to be playing or just played (state cleared on stop). "
        "Articulation must have ArticulationRootAPI on its root link. "
        "For more granular reads use get_joint_positions / get_joint_velocities / get_joint_torques (Tier 3 atomic tools)."
    ),

    # ── Debugging (PR #1) ───────────────────────────────────────────────────
    "get_console_errors": (
        "WHAT: Retrieve recent error and warning messages from the Kit/Isaac Sim console log. "
        "WHEN: 'why is X broken' debugging, after a sim crash, checking for silent warnings. "
        "RETURNS: list of {timestamp, severity, source, message} entries. "
        "CAVEATS: limited to last N messages (default 50) — older errors lost. "
        "PhysX-specific errors come through a separate stream — use get_physics_errors for those. "
        "Use diagnose_physics_error to interpret a specific error string with prim-aware fix suggestions."
    ),
    "get_debug_info": (
        "WHAT: Read runtime performance metrics: FPS, frame time, GPU utilization, physics step time, render time. "
        "WHEN: 'why is sim slow' diagnostics, profiling before optimization, checking GPU bottleneck. "
        "RETURNS: {fps, frame_ms, gpu_pct, physics_step_ms, render_ms, vram_used_mb}. "
        "CAVEATS: requires sim playing for physics_step_ms. "
        "For deeper analysis (per-zone timing, narrow phase, solver), use diagnose_performance. "
        "GPU utilization is sampled, not averaged — single read can be noisy."
    ),

    # ── Viewport (PR #1) ────────────────────────────────────────────────────
    "set_viewport_camera": (
        "WHAT: Switch the active viewport to render from a different camera prim. "
        "WHEN: 'show me what the wrist camera sees', cycling cameras during a presentation, focusing on a specific angle. "
        "RETURNS: code patch for approval. "
        "CAVEATS: camera prim must exist and be a UsdGeom.Camera. "
        "Use list_cameras to find available cameras. "
        "Switching does NOT capture an image — use capture_viewport or capture_camera_image for that."
    ),

    # ── Scene Query (PR #1) ─────────────────────────────────────────────────
    "list_all_prims": (
        "WHAT: List all prims in the scene with their types, optionally filtered by type. "
        "WHEN: 'what's in the scene', finding all cameras/lights/meshes, surveying a complex stage. "
        "RETURNS: list of {path, type, kind} for each prim. "
        "CAVEATS: traverses the entire stage — slow on >10k prim scenes. "
        "Use find_prims_by_schema for API-schema filtering. "
        "Use find_prims_by_name for regex pattern matching. "
        "Use scene_summary for natural-language overview instead of raw list."
    ),
    "measure_distance": (
        "WHAT: Measure the world-space straight-line distance between two prims (centroid-to-centroid). "
        "WHEN: checking robot reach, validating object spacing, computing approach distance for grasping. "
        "RETURNS: {distance_m: float, direction_unit: [x,y,z]}. "
        "CAVEATS: uses bounding box centroids, not surface-to-surface. "
        "For nearest-surface distance use raycast or check_collisions. "
        "Both prims must have computable bounds (Xform/Mesh). "
        "Result in stage meters by default."
    ),

    # ── ROS2 (PR #6) ────────────────────────────────────────────────────────
    "ros2_get_topic_type": (
        "WHAT: Get the ROS2 message type for a specific topic (e.g., /cmd_vel → geometry_msgs/msg/Twist). "
        "WHEN: before publishing/subscribing to verify type compatibility, debugging type mismatches. "
        "RETURNS: {topic: str, type: 'pkg/msg/Type'} or error if topic doesn't exist. "
        "CAVEATS: requires rosbridge running (port 9090) AND topic must be active (publisher exists). "
        "If topic doesn't show up, ensure ROS2Context node is in OmniGraph. "
        "Type check is exact match — sensor_msgs/msg/Image ≠ sensor_msgs/msg/CompressedImage."
    ),
    "ros2_list_services": (
        "WHAT: List all advertised ROS2 services in the current ROS_DOMAIN_ID. "
        "WHEN: discovering available services, debugging 'service not found' errors, exploring a robot's API. "
        "RETURNS: list of {service_name: str, type: 'pkg/srv/Type'}. "
        "CAVEATS: requires rosbridge active. "
        "Only shows services from nodes that have advertised — late-starting nodes may not appear immediately. "
        "Use ros2_call_service to invoke a service."
    ),
    "ros2_list_nodes": (
        "WHAT: List all currently running ROS2 nodes (Isaac Sim's bridge nodes + external nodes). "
        "WHEN: verifying ROS2 bridge is active, checking which external systems are connected, debugging missing topics. "
        "RETURNS: list of {node_name, namespace}. "
        "CAVEATS: requires rosbridge. "
        "If Isaac Sim's bridge nodes are missing, check that ROS2Context exists in an active OmniGraph and use_sim_time matches. "
        "Use ros2_get_node_details for a specific node's pubs/subs."
    ),
    "ros2_get_node_details": (
        "WHAT: Get detailed info about a ROS2 node: its publishers, subscribers, services, parameters. "
        "WHEN: debugging why a node isn't publishing/subscribing, validating bridge configuration, troubleshooting QoS. "
        "RETURNS: {node, publishers: [topic, type, qos], subscribers: [...], services: [...], parameters: [...]}. "
        "CAVEATS: requires rosbridge. "
        "Some nodes (especially Python ones) report less detail than C++ nodes. "
        "Combine with diagnose_ros2 for end-to-end QoS-mismatch detection."
    ),

    # ── Camera (PR #12) ─────────────────────────────────────────────────────
    "configure_camera": (
        "WHAT: Set camera intrinsics on a UsdGeom.Camera: focal length, aperture, clipping range, focus distance. "
        "WHEN: matching real camera specs (35mm focal=standard lens, 14mm=wide, 200mm=telephoto), tweaking depth of field, "
        "fixing clipping issues for very small/large scenes. "
        "RETURNS: code patch for approval. "
        "CAVEATS: focal_length and apertures in mm; clipping in stage units (typically meters). "
        "Setting only some params leaves others unchanged. "
        "For perspective vs orthographic projection, use set_camera_params (Tier 7) which has more options."
    ),

    # ── Quick Demo Builder (PR #54) ─────────────────────────────────────────
    "record_demo_video": (
        "WHAT: Record viewport to an MP4 video file at specified resolution and fps. "
        "WHEN: capturing demo footage for investor decks/YouTube, recording trained policy in action, archiving training results. "
        "RETURNS: code patch for approval; produces MP4 at output_path. "
        "CAVEATS: requires omni.kit.capture extension OR falls back to per-frame PNG capture (then needs ffmpeg). "
        "Long videos at 4K can be 100s of MB — use lower resolution for previews. "
        "Path-traced rendering is much slower than ray-traced — use render preview unless you need final quality."
    ),

    # ── Sim-to-Real Gap (PR #55) ────────────────────────────────────────────
    "create_calibration_experiment": (
        "WHAT: Generate a grid-search experiment over a single physics parameter (friction, damping, stiffness) "
        "to find the value that minimizes sim-to-real gap when compared to real robot data. "
        "WHEN: matching sim friction to real measurements, tuning joint damping for trajectory matching, "
        "calibrating stiffness for grasp behavior. "
        "RETURNS: code patch for approval; runs N sim trials and reports best parameter value. "
        "CAVEATS: requires real_data_path with matched task execution (joint trajectories at minimum). "
        "Grid search is expensive (N trials × trajectory length) — for fine tuning use Bayesian optimization (calibrate_physics). "
        "Currently mocks gap score — wire to measure_sim_real_gap for real evaluation."
    ),

    # ── COLLISION DISAMBIGUATION (7 tools — pick the right one) ────────────
    "check_collisions": (
        "WHAT: Validate that a prim has CollisionAPI applied and check basic collision geometry. "
        "WHEN: 'why is my robot falling through the floor', verifying physics setup before play, sanity-checking imports. "
        "RETURNS: {has_collision_api, has_mesh_collision_api, mesh_count, collision_geom_count, issues: [...]}. "
        "CAVEATS: this is a SHALLOW check (API presence + geometry count). "
        "For mesh quality (watertight, manifold, degenerate triangles) use check_collision_mesh. "
        "For pre-flight scene-wide validation use check_physics_health or preflight_check."
    ),
    "check_collision_mesh": (
        "WHAT: DEEP analysis of a single mesh's collision quality: watertight/manifold/degenerate-triangle/inverted-normals checks. "
        "WHEN: diagnosing 'weird physics' (jitter, ghost contacts, tunneling), validating imported assets before sim. "
        "RETURNS: {triangle_count, is_watertight, is_manifold, degenerate_faces, issues: [...], recommendation}. "
        "CAVEATS: requires trimesh installed; expensive for >50K triangle meshes. "
        "Reports issues but doesn't fix — use fix_collision_mesh for repair. "
        "Different from check_collisions which is shallow API-presence check."
    ),
    "fix_collision_mesh": (
        "WHAT: Auto-repair a broken collision mesh: fix normals → remove degenerate triangles → fill holes → simplify → convex decompose. "
        "WHEN: after check_collision_mesh reports issues, when imported asset has bad geometry. "
        "RETURNS: code patch for approval; produces new mesh data + updated MeshCollisionAPI. "
        "CAVEATS: simplification is lossy. "
        "Convex decomposition uses CoACD (threshold=0.05, max_hulls=16). "
        "Verify hull vertex count ≤64 (PhysX GPU limit). "
        "For approximation-only changes use simplify_collision instead."
    ),
    "visualize_collision_mesh": (
        "WHAT: Show collision geometry as wireframe overlay in viewport (the actual shape PhysX uses, not the visual mesh). "
        "WHEN: 'why isn't my object colliding correctly' — see what physics actually tests against, debug convex decomposition. "
        "RETURNS: code patch for approval; toggles physics debug visualization. "
        "CAVEATS: changes are scene-wide, not per-prim. "
        "Visual mesh and collision mesh often differ — that's by design (perf). "
        "Toggle off when done to avoid clutter."
    ),
    "configure_self_collision": (
        "WHAT: Enable/disable self-collision on an articulation, optionally with filtered link-pair list. "
        "WHEN: enabling self-collision for humanoid (avoid limb intersections), disabling for performance, filtering known-overlapping pairs. "
        "RETURNS: code patch for approval. "
        "CAVEATS: enabling self-collision on overlapping geometry causes PhysX 'explosion'. "
        "Default articulation behavior already skips adjacent links — 'auto' mode is essentially no-op. "
        "Use FilteredPairsAPI (mode=manual) for fine control."
    ),
    "optimize_collision": (
        "WHAT: Switch a single mesh's collision approximation type (convexHull, convexDecomposition, meshSimplification, boundingSphere, sdf). "
        "WHEN: changing approximation per-prim for accuracy/speed tradeoff, applying optimization recommendations from check_collision_mesh. "
        "RETURNS: code patch for approval; applies CollisionAPI + MeshCollisionAPI with chosen approximation. "
        "CAVEATS: convexHull is fast but loses concavity. "
        "convexDecomposition is more accurate but compute-heavy. "
        "boundingSphere/Cube fastest but very approximate. "
        "sdf is GPU-only."
    ),
    "simplify_collision": (
        "WHAT: Apply collision approximation to a prim (subset of optimize_collision focused on swap-only). "
        "WHEN: bulk-converting expensive triangle meshes to convex hulls for performance. "
        "RETURNS: code patch for approval. "
        "CAVEATS: same as optimize_collision but doesn't analyze first. "
        "If unsure of best approximation, run check_collision_mesh first or use suggest_collision_approximation."
    ),

    # ── JOINT DISAMBIGUATION (8 tools — same domain, different ops) ────────
    "get_joint_positions": (
        "WHAT: Read current joint positions (radians/meters) from an articulation. "
        "WHEN: monitoring robot pose during sim, capturing waypoints for replay, computing forward kinematics manually. "
        "RETURNS: {joint_names: [str], positions: [float]}. "
        "CAVEATS: requires sim playing. "
        "For all state at once (pos+vel+name) use get_articulation_state. "
        "For joint torques use get_joint_torques. "
        "For limits use get_joint_limits."
    ),
    "get_joint_velocities": (
        "WHAT: Read current joint velocities (rad/s for revolute, m/s for prismatic). "
        "WHEN: detecting joint motion, computing kinetic energy, validating velocity limits, debugging instability. "
        "RETURNS: {joint_names: [str], velocities: [float]}. "
        "CAVEATS: requires sim playing. "
        "For position use get_joint_positions, for force use get_joint_torques."
    ),
    "get_joint_torques": (
        "WHAT: Read currently-applied joint torques (effort) on an articulation. "
        "WHEN: detecting overload, computing power consumption, validating drive gains aren't saturating, monitoring effort vs limits. "
        "RETURNS: {joint_names: [str], torques: [Nm]}. "
        "CAVEATS: requires sim playing. "
        "Reports applied effort, not externally-imposed forces. "
        "Use monitor_joint_effort for time-series with limit-violation flagging."
    ),
    "get_joint_limits": (
        "WHAT: Read joint position limits (lower/upper) for a specific joint. "
        "WHEN: pre-flight check before commanding move_to_pose, validating IK targets are reachable, debugging 'joint limit exceeded' errors. "
        "RETURNS: {joint: str, lower: float, upper: float, unit: 'rad' or 'm'}. "
        "CAVEATS: returns ±inf for unlimited joints (often a bug — check with preflight_check). "
        "For drive gains use get_drive_gains."
    ),
    "set_joint_limits": (
        "WHAT: Modify the lower/upper position limits on a joint. "
        "WHEN: tightening loose limits (preventing self-collision), relaxing limits for testing, fixing ±inf imports. "
        "RETURNS: code patch for approval. "
        "CAVEATS: setting limits tighter than current position causes PhysX to snap-correct. "
        "For velocity limits use set_joint_velocity_limit instead."
    ),
    "set_joint_velocity_limit": (
        "WHAT: Set the maximum velocity constraint on a joint (joint won't be commanded above this). "
        "WHEN: enforcing safety limits, matching real-robot velocity caps, preventing overshoot. "
        "RETURNS: code patch for approval. "
        "CAVEATS: PhysX may still exceed briefly during impulses. "
        "Combine with set_drive_gains for soft enforcement. "
        "For position limits use set_joint_limits."
    ),
    "monitor_joint_effort": (
        "WHAT: Sample joint efforts (torques) over a duration via physics callback, flag joints exceeding 90% of limits. "
        "WHEN: stress-testing a robot pose, finding which joint saturates first, debugging instability. "
        "RETURNS: {samples: [...], per_joint_max: {...}, flagged: [joints near limit]}. "
        "CAVEATS: requires sim playing for the full duration. "
        "Heavy on PhysX callback chain — use sparingly. "
        "For instantaneous read use get_joint_torques."
    ),

    # ── ROBOT DISAMBIGUATION (8 tools — workflow stages) ───────────────────
    "import_robot": (
        "WHAT: Import a robot from URDF/MJCF/USD file or asset library name into the scene. "
        "WHEN: adding a robot to an empty scene, loading a known robot (Franka, UR10, Jetbot, G1), bringing in a custom URDF. "
        "RETURNS: code patch for approval; creates robot prim hierarchy. "
        "CAVEATS: prefer USD library names ('franka', 'ur10') over URDF when possible — better tested. "
        "Run verify_import + apply_robot_fix_profile after to catch common issues. "
        "For composing robots from parts use assemble_robot."
    ),
    "anchor_robot": (
        "WHAT: Fix a robot to the world (or a specific surface) via fixed joint, preventing it from falling. "
        "WHEN: placing a manipulator on a table, fixing a base before testing, anchoring during teleop setup. "
        "RETURNS: code patch for approval. "
        "CAVEATS: for wheeled robots use differential drive setup, not anchor. "
        "Anchored articulations can still be moved via teleport_prim. "
        "Removes mobility — for mobile robots use create_wheeled_robot."
    ),
    "robot_wizard": (
        "WHAT: Import a URDF/USD with sensible defaults applied (drive gains by type, convex collision, self-collision filtered). "
        "WHEN: 'just import this robot and make it work' for new URDF, quick prototyping. "
        "RETURNS: code patch for approval. "
        "CAVEATS: applies generic defaults — for robot-specific tuning use apply_robot_fix_profile or tune_gains. "
        "Replaces step-by-step manual import. "
        "For interactive GUI wizard, open Window > Robotics > Robot Wizard in Isaac Sim."
    ),
    "assemble_robot": (
        "WHAT: Attach an end-effector/tool/gripper to a base robot via fixed joint at a mount frame. "
        "WHEN: adding a custom gripper to Franka, mounting a sensor on a UR10, building multi-part robot. "
        "RETURNS: code patch for approval. "
        "CAVEATS: ONLY fixed joints supported (no revolute/prismatic for connection). "
        "Mount frames must exist on both base and attachment. "
        "For motion planning the assembled robot needs robot_description regenerated."
    ),
    "create_wheeled_robot": (
        "WHAT: Create a wheeled robot controller: differential, ackermann, or holonomic drive. "
        "WHEN: setting up a mobile base (Jetbot, Carter, AMR), adding navigation control to wheeled platform. "
        "RETURNS: code patch for approval; creates controller and wires to wheel joints. "
        "CAVEATS: wheel_radius and wheel_base must match the robot geometry. "
        "For navigation-to-target use navigate_to. "
        "For arm robots use anchor_robot or assemble_robot instead."
    ),
    "generate_robot_description": (
        "WHAT: Generate motion planning robot_description for a known robot, OR return XRDF Editor instructions for unknown. "
        "WHEN: setting up move_to_pose / plan_trajectory / IK for a robot, before any motion planning workflow. "
        "RETURNS: {supported: bool, robot_type, config_paths: {...}} or instructions for manual setup. "
        "CAVEATS: only ~9 robots are pre-supported (franka, ur10/5e/3e, cobotta, rs007n, dofbot, kawasaki, flexiv_rizon). "
        "Custom robots need XRDF authoring (GUI). "
        "Required before solve_ik / move_to_pose for that robot."
    ),
    "publish_robot_description": (
        "WHAT: Build simplified URDF from USD articulation, publish to /robot_description ROS2 topic with TRANSIENT_LOCAL QoS. "
        "WHEN: bringing up Nav2/MoveIt2 (which need /robot_description), setting up RViz visualization. "
        "RETURNS: code patch for approval. "
        "CAVEATS: this is a SIMPLIFIED URDF (link names + joints only). "
        "For full URDF export use Isaac Sim's URDF Exporter UI. "
        "Note: isaacsim.ros2.urdf is an IMPORTER, not exporter — we build URDF from USD ourselves."
    ),
    "apply_robot_fix_profile": (
        "WHAT: Apply known fix profile for a specific robot (Franka link0 collision, UR5 joint axis, G1 foot collision, Allegro inertia). "
        "WHEN: after import_robot for the 5 supported robots (franka, ur5, ur10, g1, allegro). "
        "RETURNS: {robot_type, fixes: [{path, command}], recommended_gains}. "
        "CAVEATS: only 5 robots have profiles — others return generic verify_import suggestion. "
        "Combine with verify_import to find issues first."
    ),

    # ── SCENE DISAMBIGUATION (5 most-confused) ─────────────────────────────
    "scene_summary": (
        "WHAT: Natural-language summary of the scene: prim counts, robots present, lights, cameras, physics state. "
        "WHEN: 'what's in my scene', initial inspection of a loaded stage, sanity check after major changes. "
        "RETURNS: {prim_count, robot_count, light_count, camera_count, has_physics, summary_text}. "
        "CAVEATS: high-level overview — for full prim list use list_all_prims. "
        "For per-prim details use get_prim_metadata / list_attributes."
    ),
    "generate_scene_blueprint": (
        "WHAT: LLM-powered spatial planner: convert natural-language description into structured blueprint JSON (room, objects, positions). "
        "WHEN: 'build a kitchen with a Franka', 'set up a warehouse with shelves', high-level scene design. "
        "RETURNS: {scene_name, room_dims, objects: [{asset, position, rotation, purpose}]}. "
        "CAVEATS: blueprint is a PLAN — must call build_scene_from_blueprint to actually create prims. "
        "Run validate_scene_blueprint before build to catch placement issues. "
        "For pre-built templates use load_scene_template (cheaper, no LLM call)."
    ),
    "build_scene_from_blueprint": (
        "WHAT: Execute a scene blueprint by creating all prims with positions, applying physics, importing assets. "
        "WHEN: after generate_scene_blueprint or load_scene_template + validate_scene_blueprint. "
        "RETURNS: code patch for approval (large multi-step patch). "
        "CAVEATS: dry_run=true returns code without executing — recommended for review. "
        "Each object becomes a code segment — large blueprints generate large patches. "
        "Best practice: run validate_scene_blueprint first, then build with dry_run=true, then approve."
    ),
    "validate_scene_blueprint": (
        "WHAT: Pre-flight validation of a blueprint: AABB overlaps, floating objects, scale outliers, missing assets, PhysX collision check. "
        "WHEN: between generate_scene_blueprint and build_scene_from_blueprint, after editing a blueprint manually. "
        "RETURNS: {valid: bool, issues: [...], warnings: [...], object_count}. "
        "CAVEATS: PhysX collision check requires Kit RPC alive. "
        "Catches most issues but not all (e.g., articulation feasibility). "
        "Cheap (<1s) — always run before build."
    ),
    "list_scene_templates": (
        "WHAT: List pre-built scene templates (tabletop manipulation, warehouse picking, mobile navigation, inspection cell). "
        "WHEN: 'I want a quick demo scene', browsing available starters, no need for custom layout. "
        "RETURNS: list of {name, description, category, object_count, room_dims}. "
        "CAVEATS: templates are static — for custom scenes use generate_scene_blueprint. "
        "Use load_scene_template to get a specific one as a blueprint."
    ),
    "load_scene_template": (
        "WHAT: Load a specific scene template as a blueprint (compatible with build_scene_from_blueprint). "
        "WHEN: after list_scene_templates picked one, want a known-good starter. "
        "RETURNS: full blueprint dict ready to pass to build_scene_from_blueprint. "
        "CAVEATS: blueprint is editable before build — modify objects/positions as needed."
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
        if name in POLISH:
            tool["function"]["description"] = POLISH[name]
            count += 1
    return count
