"""
tool_descriptions_polish_b5.py
------------------------------
Rich tool descriptions — Batch 5 (47 tools).

Same WHAT / WHEN / RETURNS / CAVEATS format as tool_descriptions_polish.py.
Covers:
  - USD inspection primitives (Tier 1): list_attributes / list_relationships /
    list_applied_schemas / get/set_prim_metadata / get_prim_type /
    find_prims_by_schema / find_prims_by_name / get_kind / get_active_state
  - Spatial primitives: get_world_transform / get_bounding_box / set_semantic_label
  - Render / variant / training: set_render_mode / set_variant /
    get_training_status / pixel_to_world / record_trajectory
  - Physics body primitives (Tier 2): get/set_linear_velocity /
    get_angular_velocity / get_mass / get_inertia /
    get/set_physics_scene_config / list_contacts / apply_force /
    get_kinematic_state / get_contact_report
  - Articulation primitives (Tier 3): get/set_drive_gains /
    get_articulation_mass / get_center_of_mass / get_gripper_state
  - Geometry primitives (Tier 4): raycast / overlap_sphere / overlap_box /
    sweep_sphere / compute_volume / compute_surface_area / compute_convex_hull
  - OmniGraph primitives (Tier 5): list_graphs / inspect_graph / add_node /
    connect_nodes / set_graph_variable / delete_node

Apply at MCP server startup or import-time:

    from tool_descriptions_polish_b5 import POLISH_B5
    for tool in ISAAC_SIM_TOOLS:
        name = tool["function"]["name"]
        if name in POLISH_B5:
            tool["function"]["description"] = POLISH_B5[name]
"""

POLISH_B5 = {
    # ── Spatial / Geometry inspection ───────────────────────────────────────
    "get_world_transform": (
        "WHAT: Compute a prim's world-space 4x4 transform via UsdGeom.Xformable.ComputeLocalToWorldTransform, returning the row-major matrix plus extracted translation, quaternion (w,x,y,z), and scale. "
        "WHEN: querying the actual world pose of a nested prim (parent xforms applied), validating placement after add_reference, computing camera/end-effector world position. "
        "RETURNS: {matrix:[16 floats], translation:[x,y,z], rotation_quat:[w,x,y,z], scale:[sx,sy,sz]}. "
        "CAVEATS: requires a UsdGeom.Xformable (Mesh, Xform, Camera, etc.) — pure typeless prims return identity. "
        "Time-sampled xforms need an explicit time_code parameter. "
        "For just bounds use get_bounding_box; for setting transforms use teleport_prim."
    ),
    "get_bounding_box": (
        "WHAT: Compute the world-space axis-aligned bounding box of a prim and its descendants via UsdGeom.BBoxCache.ComputeWorldBound. "
        "WHEN: clearance reasoning before placement, sizing a robot vs a table, computing object footprint for grasping, deciding camera framing. "
        "RETURNS: {min:[x,y,z], max:[x,y,z], center:[x,y,z], size:[dx,dy,dz]} — all in stage units (typically meters). "
        "CAVEATS: AABB is in WORLD space, NOT local — it grows when the prim is rotated. "
        "purpose='render' excludes proxy geometry; 'proxy' uses fast lower-poly bounds. "
        "Returns zero-extent for empty Xforms. "
        "For surface-to-surface distance use raycast; for centroid distance use measure_distance."
    ),
    "set_semantic_label": (
        "WHAT: Apply Semantics.SemanticsAPI to a prim with a class name so Replicator writers emit the prim in semantic_segmentation, instance_segmentation, and bounding_box outputs. "
        "WHEN: preparing scene for synthetic data generation (SDG), labeling target objects before launching a Replicator run, annotating new assets for downstream training. "
        "RETURNS: code patch for approval; adds the SemanticsAPI + class_name + semantic_type tokens to the prim. "
        "CAVEATS: label is on this prim only — children NOT labeled automatically (use assign_class_to_children for hierarchies). "
        "semantic_type defaults to 'class'. "
        "Use list_semantic_classes to see existing labels, validate_semantic_labels to spot gaps."
    ),

    # ── Articulation drive control ──────────────────────────────────────────
    "set_drive_gains": (
        "WHAT: Set drive stiffness (kp) and damping (kd) on a UsdPhysics.DriveAPI for a single joint. "
        "WHEN: tuning RL policy stability, fixing oscillating/sluggish joints, matching real-robot controller bandwidth, applying recommended gains from apply_robot_fix_profile. "
        "RETURNS: code patch for approval. "
        "CAVEATS: kp too high + low dt causes simulation explosion (rule of thumb: kp*dt < 0.5). "
        "drive_type must match joint type — 'angular' for revolute, 'linear' for prismatic. "
        "DriveAPI must already be applied (use apply_api_schema with 'PhysicsDriveAPI' first if missing). "
        "For reading current gains use get_drive_gains; for commanding targets use set_joint_targets."
    ),
    "get_drive_gains": (
        "WHAT: Read current drive stiffness (kp) and damping (kd) from a joint's UsdPhysics.DriveAPI. "
        "WHEN: BEFORE set_drive_gains so you know what you are overwriting, debugging a robot that won't track targets, comparing tuned vs default gains. "
        "RETURNS: {joint_path, drive_type, kp, kd, max_force} or per-axis dict when drive_type='auto'. "
        "CAVEATS: returns nulls when no DriveAPI is applied — check with list_applied_schemas first. "
        "drive_type='auto' tries both angular and linear and reports whichever exists. "
        "For setting gains use set_drive_gains; for joint state use get_joint_torques."
    ),

    # ── Contacts & collision reporting ──────────────────────────────────────
    "get_contact_report": (
        "WHAT: Read the most recent PhysX contact-report events involving a specific prim (one-shot snapshot from the contact stream). "
        "WHEN: detecting whether a grasp succeeded, finding what a robot just collided with, debugging unexpected contact forces. "
        "RETURNS: list of up to max_contacts (default 50) {actor0, actor1, impulse, normal, position} entries from the latest physics step. "
        "CAVEATS: requires PhysxContactReportAPI applied to the prim BEFORE sim runs (apply with apply_api_schema). "
        "Returns empty if no contacts in the last step or if API missing. "
        "For a time-windowed sweep use list_contacts (auto-applies API). "
        "For triangle-precise tests use overlap_sphere/overlap_box."
    ),
    "list_contacts": (
        "WHAT: List active contact pairs for a rigid body over a sample window. Auto-applies PhysxContactReportAPI if missing, then subscribes to the contact stream for `duration` seconds. "
        "WHEN: 'who is the gripper actually touching', diagnosing penetration, validating that an object rests on a target surface. "
        "RETURNS: list of {body_a, body_b, impulse} pairs above min_impulse threshold. "
        "CAVEATS: requires sim to be playing (otherwise no physics steps fire). "
        "Default duration 0.5s, min_impulse 0.0 N·s — bump min_impulse to filter noise. "
        "Different from get_contact_report (which is a one-shot of last step) — this samples a window and aggregates. "
        "Heavy on the contact-stream callback — keep durations short."
    ),

    # ── Render / variants / training ────────────────────────────────────────
    "set_render_mode": (
        "WHAT: Switch the active renderer between 'preview' (Hydra Storm rasterizer, fast/low-quality), 'rt' (RTX Real-Time path tracing, interactive PBR), and 'path_traced' (RTX Path-Traced, offline-quality). "
        "WHEN: switching to 'preview' for fast iteration, 'rt' for demos, 'path_traced' for SDG ground-truth renders or marketing footage. "
        "RETURNS: code patch for approval; renderer changes immediately on the active viewport. "
        "CAVEATS: path_traced is 5-50x slower than rt — never use for live training. "
        "First switch to RTX modes can take seconds (shader compile). "
        "For per-camera/render-product config use set_render_settings; for capture use record_demo_video."
    ),
    "set_variant": (
        "WHAT: Select a USD variant inside a variant set on a prim via UsdVariantSet.SetVariantSelection (e.g., switch 'color' set to 'red', 'lod' set to 'high'). "
        "WHEN: changing asset color/material/rig variant without re-importing, swapping LOD levels, toggling 'damaged' vs 'pristine' object states. "
        "RETURNS: code patch for approval; geometry/material under the variant updates on stage. "
        "CAVEATS: variant_set name must already exist on the prim — discover with list_variant_sets. "
        "Variant token must be in list_variants for that set — case-sensitive. "
        "Variant changes can hide/show large subtrees; expect bounding-box and physics changes."
    ),
    "get_training_status": (
        "WHAT: Inspect the live state of an IsaacLab RL training run by reading TensorBoard event files + the launcher's subprocess state. "
        "WHEN: polling progress of a long RL run, deciding when to stop early, dashboard updates, validating a job started successfully. "
        "RETURNS: {run_id, state:'running'|'finished'|'crashed', step, total_steps, latest_reward, log_dir}. "
        "CAVEATS: requires the run to have been started via launch_training (sets up log_dir convention). "
        "TensorBoard event flush lag means latest_reward can be a few seconds stale. "
        "log_dir override only needed for runs in non-default paths. "
        "For full launch lifecycle use launch_training / list_training_runs / stop_training."
    ),
    "pixel_to_world": (
        "WHAT: Project a 2D viewport pixel through a camera intrinsics + depth buffer to a 3D world-space point. "
        "WHEN: implementing 'click to place', visual servoing target selection, mapping a perception detection back to scene coords, debugging camera calibration. "
        "RETURNS: {world_position:[x,y,z], depth_m, ray_origin:[x,y,z], ray_direction:[dx,dy,dz]}. "
        "CAVEATS: depth is read from the depth buffer — pixels hitting the skybox return NaN/very large depth. "
        "Pixel (0,0) is top-left. "
        "Resolution param overrides the actual viewport — only set if you've authored a custom render product. "
        "For the inverse (3D → pixel) use camera projection in scripts."
    ),
    "record_trajectory": (
        "WHAT: Subscribe to PhysX step events for `duration` seconds and sample joint positions / velocities / efforts at `rate_hz`, then write to a .npz file. "
        "WHEN: capturing a human demonstration during teleop, archiving a successful policy rollout for replay/analysis, comparing two control strategies offline. "
        "RETURNS: code patch for approval; produces .npz at output_path with per-joint sample arrays + metadata. "
        "CAVEATS: requires sim playing for full duration — pausing produces gaps. "
        "rate_hz default 60 — exceeding physics fps just duplicates samples. "
        "Default output: workspace/trajectories/<timestamp>.npz. "
        "For real-time monitoring use monitor_joint_effort or get_joint_positions."
    ),

    # ── USD inspection (Tier 1) ─────────────────────────────────────────────
    "list_attributes": (
        "WHAT: Enumerate every attribute defined on a USD prim via prim.GetAttributes(), listing name, typeName, has_value, and custom flag. "
        "WHEN: discovering what can be read or set on an unfamiliar prim, debugging 'attribute not found' errors, exploring an imported asset. "
        "RETURNS: list of {name, type, has_value, custom} entries — one per attribute. "
        "CAVEATS: lists DEFINED attributes only — auto-creatable attrs (e.g., new xformOp:translate) won't appear until first set. "
        "Includes inherited attributes from applied schemas. "
        "Different from list_relationships (targets) and list_applied_schemas (mixins). "
        "For a single attr value use get_attribute."
    ),
    "list_relationships": (
        "WHAT: List every relationship on a USD prim via prim.GetRelationships() with each relationship's current target paths. "
        "WHEN: inspecting material bindings (material:binding), physics filtered pairs, skeleton bindings, joint body0/body1 wiring, custom proxy refs. "
        "RETURNS: list of {name, targets:[paths]} — one per relationship. "
        "CAVEATS: relationships can have multiple targets (e.g., FilteredPairsAPI). "
        "Empty targets[] means relationship is declared but unset. "
        "For attribute values use list_attributes; for applied API schemas use list_applied_schemas."
    ),
    "list_applied_schemas": (
        "WHAT: Return the API schemas applied to a prim via prim.GetAppliedSchemas() — e.g., PhysicsRigidBodyAPI, PhysicsCollisionAPI, MaterialBindingAPI, DriveAPI. "
        "WHEN: 'why isn't physics working on this prim', verifying which APIs are present before apply_api_schema (avoid double-apply), pre-flight checks. "
        "RETURNS: list of schema short-name strings. "
        "CAVEATS: lists APPLIED schemas only — built-in schemas (e.g., UsdGeom.Mesh's typed schema) are NOT listed. "
        "Multi-apply schemas (e.g., DriveAPI:angular) include the instance suffix. "
        "To apply use apply_api_schema; to find all prims with a given API use find_prims_by_schema."
    ),
    "get_prim_metadata": (
        "WHAT: Read a single USD metadata field on a prim via prim.GetMetadata(key). "
        "WHEN: reading kind ('component'|'assembly'|'group'|'subcomponent'), checking 'hidden'/'instanceable'/'active' state, retrieving 'documentation' string, inspecting 'specifier'. "
        "RETURNS: {key, value, python_type}. "
        "CAVEATS: metadata is NOT the same as attributes — 'hidden' metadata only affects UI; for true invisibility use visibility attribute. "
        "Returns None for unset metadata. "
        "For 'kind' specifically use get_kind (typed accessor); for 'active' use get_active_state. "
        "To write use set_prim_metadata."
    ),
    "set_prim_metadata": (
        "WHAT: Write a USD metadata field on a prim via prim.SetMetadata(key, value). "
        "WHEN: setting kind for asset organization ('component', 'assembly'), marking prims hidden/instanceable, attaching documentation strings, toggling active. "
        "RETURNS: code patch for approval. "
        "CAVEATS: setting kind affects USD model hierarchy rules (component cannot contain another component). "
        "'hidden' metadata only affects UI listing, NOT rendering — for rendering visibility use the visibility attribute. "
        "For deactivating a prim from physics/render/traversal use set_active_state instead."
    ),
    "get_prim_type": (
        "WHAT: Return the typeName of a prim via prim.GetTypeName() — e.g., 'Mesh', 'Xform', 'Camera', 'Cube', 'DistantLight', 'PhysicsScene'. "
        "WHEN: branching logic on prim type, validating that a path resolves to the expected type before calling type-specific tools (e.g., camera-only ops). "
        "RETURNS: type name string, or empty string for typeless/over prims. "
        "CAVEATS: returns the CONCRETE type only — for applied API schemas use list_applied_schemas. "
        "Empty string is valid for typeless 'over' prims used to layer overrides. "
        "For finding all prims of a given type use list_all_prims with a type filter."
    ),
    "find_prims_by_schema": (
        "WHAT: Traverse the stage and return every prim path where prim.HasAPI(schema) is true. "
        "WHEN: locating all rigid bodies, all articulation roots, all colliders, all light prims for batch operations, scene-wide validation. "
        "RETURNS: list of prim path strings (capped at limit, default 500). "
        "CAVEATS: schema_name is the SHORT class name ('PhysicsRigidBodyAPI', 'PhysicsArticulationRootAPI'), NOT the typed schema. "
        "Default limit 500 — bump for big scenes. "
        "Scoped to root_path subtree if provided. "
        "For name/regex matching use find_prims_by_name; for type filtering use list_all_prims with type filter."
    ),
    "find_prims_by_name": (
        "WHAT: Regex search across all prim paths on the stage using Python re.search semantics. "
        "WHEN: locating prims by partial name ('.*panda_link.*'), filtering by namespace ('/World/Robots/.*Franka.*'), gathering all matches for a bulk op. "
        "RETURNS: list of matching prim path strings (capped at limit, default 500). "
        "CAVEATS: pattern uses re.search (NOT fullmatch) — anchor with ^/$ if needed. "
        "Default scope is full stage; pass root_path to limit. "
        "For schema-based filtering use find_prims_by_schema; for type-based use list_all_prims."
    ),
    "get_kind": (
        "WHAT: Read the Kind metadata of a prim via Usd.ModelAPI(prim).GetKind() — one of 'component', 'assembly', 'group', 'subcomponent', or '' if unset. "
        "WHEN: respecting USD model hierarchy rules during scene composition, identifying assembly roots, filtering for asset-level prims. "
        "RETURNS: kind token string. "
        "CAVEATS: Kind is metadata, not a typed attribute — set with set_prim_metadata(key='kind') or USD-level kind setter. "
        "Kind affects how Hydra/Kit treat instancing and selection groupings. "
        "For arbitrary metadata use get_prim_metadata."
    ),
    "get_active_state": (
        "WHAT: Return whether a prim is active on the stage via prim.IsActive(). Inactive prims (and descendants) are excluded from rendering, physics, and traversal. "
        "WHEN: 'why isn't this prim showing up', toggling test variants, debugging unexpected physics no-ops, sanity checks. "
        "RETURNS: {prim_path, active: bool}. "
        "CAVEATS: 'active' is COMPLETE deactivation — different from 'visibility' (render-only) or 'hidden' metadata (UI-only). "
        "Inactive prims also vanish from get/list traversals (UsdPrimRange respects active). "
        "To toggle use set_active_state."
    ),

    # ── Physics body primitives (Tier 2) ────────────────────────────────────
    "get_linear_velocity": (
        "WHAT: Return the current linear velocity (m/s) of a rigid body via UsdPhysics.RigidBodyAPI(prim).GetVelocityAttr().Get(). "
        "WHEN: verifying a body moves at the expected speed after apply_force, monitoring fall speed, validating reset to rest. "
        "RETURNS: {prim_path, velocity:[vx,vy,vz], magnitude}. "
        "CAVEATS: requires sim playing — value frozen at last step when paused/stopped. "
        "Prim must have RigidBodyAPI applied (use apply_api_schema first). "
        "For angular use get_angular_velocity; for full state use get_kinematic_state."
    ),
    "get_angular_velocity": (
        "WHAT: Return the current angular velocity (deg/s) of a rigid body via UsdPhysics.RigidBodyAPI(prim).GetAngularVelocityAttr().Get(). "
        "WHEN: inspecting spin rate, debugging tumbling objects, validating wheel rotation rate, detecting unexpected torque. "
        "RETURNS: {prim_path, angular_velocity:[wx,wy,wz], magnitude_deg_s}. "
        "CAVEATS: units are DEGREES per second (USD convention), NOT radians. "
        "Requires sim playing + RigidBodyAPI on prim. "
        "For linear use get_linear_velocity; for one-shot full kinematic snapshot use get_kinematic_state."
    ),
    "set_linear_velocity": (
        "WHAT: Set the linear velocity (m/s) on a rigid body via UsdPhysics.RigidBodyAPI(prim).GetVelocityAttr().Set(). "
        "WHEN: launching projectiles at a known speed, initializing a falling object's velocity, resetting to a velocity for repeatable trials. "
        "RETURNS: code patch for approval. "
        "CAVEATS: takes effect at the next physics step — sim must be playing/about to play. "
        "Setting velocity on a kinematic body is silently ignored — use teleport_prim instead. "
        "For applying impulses/continuous force use apply_force; for angular use a similar attribute."
    ),
    "get_mass": (
        "WHAT: Return the current mass (kg) of a rigid body via UsdPhysics.MassAPI(prim).GetMassAttr().Get(). "
        "WHEN: verifying physics setup, debugging unexpected fall acceleration, checking mass before tuning grasp force. "
        "RETURNS: {prim_path, mass_kg}. "
        "CAVEATS: 0.0 is a magic value — PhysX computes mass from collision geometry × density. "
        "Mass on parent Xform is ignored — must be on the rigid-body prim itself. "
        "For inertia use get_inertia; for sum across articulation use get_articulation_mass."
    ),
    "get_inertia": (
        "WHAT: Return the diagonal of the inertia tensor (kg·m²) of a rigid body via UsdPhysics.MassAPI(prim).GetDiagonalInertiaAttr().Get(). "
        "WHEN: debugging unexpected rotation behavior, validating imported assets have sane inertia, comparing manually-authored vs computed inertia. "
        "RETURNS: {prim_path, inertia_diag:[Ixx,Iyy,Izz]}. "
        "CAVEATS: zero vector means PhysX auto-computes from collision geometry — usually fine. "
        "Off-diagonal elements not exposed (USD authoring is principal-axis only). "
        "For mass use get_mass; for full kinematic state use get_kinematic_state."
    ),
    "get_physics_scene_config": (
        "WHAT: Read the global PhysicsScene config: gravity (direction + magnitude), solver type (PGS/TGS), iteration counts, time step, GPU enabled flag, broadphase type. "
        "WHEN: pre-flight check before tuning RL or rigid-body sims, debugging instability, verifying gravity matches intended environment (Earth/moon/zero-G). "
        "RETURNS: {scene_path, solver_type, position_iterations, velocity_iterations, time_steps_per_second, enable_gpu_dynamics, broadphase_type, gravity_direction, gravity_magnitude}. "
        "CAVEATS: auto-detects first PhysicsScene if scene_path omitted — fails on multi-scene stages without explicit path. "
        "For setting use set_physics_scene_config; legacy alias set_physics_params writes a subset."
    ),
    "set_physics_scene_config": (
        "WHAT: Update PhysicsScene config (solver type, iterations, time step, GPU enable, broadphase, gravity) via UsdPhysics.Scene + PhysxSchema.PhysxSceneAPI. "
        "WHEN: switching solvers (PGS↔TGS), tuning iteration count for stiff joints, lowering time step for fast objects, changing gravity for moon/zero-G/space sims. "
        "RETURNS: code patch for approval; affects PhysicsScene globally. "
        "CAVEATS: time_steps_per_second × stiffness > ~30000 causes instability — for high stiffness raise tps. "
        "TGS solver is more stable for articulations but slightly slower. "
        "GPU broadphase only valid with enable_gpu_dynamics=true. "
        "Use get_physics_scene_config first to know current values."
    ),
    "apply_force": (
        "WHAT: Apply external force and/or torque to a rigid body for one physics step using PhysX tensor API. "
        "WHEN: pushing objects in tests, applying wrench at a specific contact point, simulating wind/thrust, scripted disturbances for robustness testing. "
        "RETURNS: code patch for approval; impulse applied at next physics step. "
        "CAVEATS: applies for ONE step only — for sustained force, call repeatedly or use set_linear_velocity. "
        "Force in Newtons (world frame), torque in N·m (world frame). "
        "If position omitted, force is applied at center of mass (no induced torque). "
        "Requires RigidBodyAPI + sim playing."
    ),
    "get_kinematic_state": (
        "WHAT: One-shot snapshot of a rigid body's full kinematic state: world transform (position + rotation), linear and angular velocity, plus best-effort linear/angular acceleration via finite difference. "
        "WHEN: debugging body behavior at a specific moment, capturing a 'before/after' diff for a maneuver, comparing simulated vs reference policy. "
        "RETURNS: {prim_path, position, rotation_quat, linear_velocity, angular_velocity, linear_acceleration, angular_acceleration}. "
        "CAVEATS: acceleration uses finite-diff over sample_dt (default 0.05s) — noisy at high frequencies. "
        "Requires sim playing. "
        "For individual reads use get_world_transform / get_linear_velocity / get_angular_velocity."
    ),

    # ── Articulation primitives (Tier 3) ────────────────────────────────────
    "get_articulation_mass": (
        "WHAT: Sum the mass of every link in an articulation by walking Usd.PrimRange under the root and adding UsdPhysics.MassAPI mass for each rigid body. "
        "WHEN: comparing total robot mass to spec, validating import (often missing inertia → wrong mass), preparing for force/payload calculations. "
        "RETURNS: {articulation, total_mass_kg, per_link:[{path, mass_kg}]}. "
        "CAVEATS: links with mass=0 (PhysX auto-compute) are NOT counted in the sum — flag in per_link to detect. "
        "Requires articulation root path (use find_prims_by_schema 'PhysicsArticulationRootAPI' to discover). "
        "For per-link inertia use get_inertia per link."
    ),
    "get_center_of_mass": (
        "WHAT: Compute the world-space center-of-mass of an articulation as the mass-weighted average of each link's CoM transformed via ComputeLocalToWorldTransform. "
        "WHEN: balance analysis for humanoids/quadrupeds, computing static stability margin, planning support polygon for footing. "
        "RETURNS: {articulation, com_world:[cx,cy,cz], total_mass_kg}. "
        "CAVEATS: requires every link to have a non-zero mass — auto-compute (mass=0) skews the CoM. "
        "For total mass alone use get_articulation_mass; for ZMP/balance metrics use higher-level balance tools."
    ),
    "get_gripper_state": (
        "WHAT: Classify a gripper as 'open' / 'closed' / 'midway' from joint position vs limits, plus average commanded torque as 'force_estimate'. "
        "WHEN: confirming a grasp completed, deciding whether to release, monitoring grip force during a manipulation. "
        "RETURNS: {state, joint_positions, force_estimate_Nm, fraction_closed}. "
        "CAVEATS: requires explicit gripper_joints list — tool doesn't infer them (use list_joints + filter). "
        "open_threshold / closed_threshold are fractions of joint range (default 0.6 / 0.1). "
        "force_estimate is COMMANDED torque, not measured contact force — for actual grasp force use get_contact_report on the gripped object."
    ),

    # ── Geometry primitives (Tier 4) ────────────────────────────────────────
    "raycast": (
        "WHAT: Cast a single ray through the PhysX scene and return the closest hit (prim path, world position, normal, distance) via raycast_closest. "
        "WHEN: surface picking ('what's directly below the gripper'), distance-to-surface measurement, line-of-sight checks for sensors, snapping objects to terrain. "
        "RETURNS: {hit:bool, prim_path, position:[x,y,z], normal:[nx,ny,nz], distance_m} or hit=false. "
        "CAVEATS: requires PhysX scene with colliders — pure visual meshes (no CollisionAPI) are invisible to raycast. "
        "direction is normalized internally. "
        "max_distance default 1000m — increase for very large scenes. "
        "For volume queries use overlap_sphere/overlap_box; for sweep tests use sweep_sphere."
    ),
    "overlap_sphere": (
        "WHAT: Find every PhysX collider whose AABB overlaps a sphere centered at `center` with `radius` (uses overlap_sphere with report_fn callback). "
        "WHEN: 'what's within 0.3m of the end-effector', proximity triggers, area-of-effect queries, sensor cone approximations. "
        "RETURNS: list of prim path strings — every collider intersecting the sphere. "
        "CAVEATS: AABB-level overlap (broadphase) — not exact mesh-vs-sphere. "
        "Returns empty if no colliders intersect. "
        "Requires PhysX scene + colliders. "
        "For oriented box use overlap_box; for line-segment tests use sweep_sphere; for point-in-volume use raycast."
    ),
    "overlap_box": (
        "WHAT: Find every PhysX collider that overlaps an oriented bounding box defined by center, half-extents, and quaternion rotation. "
        "WHEN: workspace-bounds queries ('what's inside the bin'), volumetric triggers, view frustum approximations, object containment checks. "
        "RETURNS: list of prim path strings — every collider intersecting the box. "
        "CAVEATS: AABB-level test in box's local frame (broadphase). "
        "rotation defaults to identity if omitted. "
        "Half-extents (NOT full extents) — pass [0.5,0.5,0.5] for a 1m³ box. "
        "For sphere queries use overlap_sphere; for raycasts use raycast."
    ),
    "sweep_sphere": (
        "WHAT: Sweep a sphere from `start` to `end` and return the closest hit along the sweep (prim path, hit position, normal, distance). "
        "WHEN: continuous-collision detection for fast objects, motion-planning shortcut tests, predicting where a thrown ball will hit. "
        "RETURNS: {hit:bool, prim_path, position, normal, distance_m_along_sweep}. "
        "CAVEATS: catches collisions a static raycast would miss (tunneling check). "
        "Requires PhysX scene + colliders. "
        "Sphere only — no box-sweep variant. "
        "For instantaneous overlap use overlap_sphere; for ray-only use raycast."
    ),
    "compute_volume": (
        "WHAT: Compute the signed volume of a Mesh prim by summing tetrahedra formed by every triangle and the origin (divergence theorem). Uses trimesh if installed, else manual fallback. "
        "WHEN: estimating object mass from density × volume, validating watertightness (negative volume → flipped normals), comparing CAD vs imported geometry. "
        "RETURNS: {prim_path, volume_m3}. "
        "CAVEATS: result is SIGNED — negative volume indicates inverted/flipped normals (use fix_collision_mesh to repair). "
        "Non-watertight meshes give garbage volume — check with check_collision_mesh first. "
        "Triangulates non-tri faces internally. "
        "For surface area use compute_surface_area; for hull use compute_convex_hull."
    ),
    "compute_surface_area": (
        "WHAT: Compute the total surface area of a Mesh prim by summing triangle areas (after triangulating non-tri faces). "
        "WHEN: estimating coating/paint coverage, computing heat-exchange surface, validating mesh density (tris per m²), comparing model fidelity. "
        "RETURNS: {prim_path, area_m2}. "
        "CAVEATS: counts both sides of an open surface as one area — for closed shells this is fine. "
        "Self-intersecting meshes over-count overlapping regions. "
        "For volume use compute_volume."
    ),
    "compute_convex_hull": (
        "WHAT: Apply UsdPhysics.MeshCollisionAPI with approximation='convexHull' to a Mesh prim. Optionally export the hull mesh to a sibling prim via scipy.spatial.ConvexHull. "
        "WHEN: simplifying a complex collision mesh, replacing concave geometry with a hull-only collider, visualizing what PhysX uses for collision. "
        "RETURNS: code patch for approval; sets MeshCollisionAPI approximation + optionally creates export_hull_path prim. "
        "CAVEATS: convex hull DROPS concavity — for cups/bowls use convexDecomposition via optimize_collision. "
        "Hull vertex count must stay ≤64 for PhysX GPU. "
        "Falls back to gift-wrap (slow) if scipy unavailable."
    ),

    # ── OmniGraph primitives (Tier 5) ───────────────────────────────────────
    "list_graphs": (
        "WHAT: Enumerate all OmniGraph action graphs on the current USD stage. "
        "WHEN: discovering existing graphs before inspecting/editing, sanity check after import (some assets bring their own graphs), 'what graphs exist'. "
        "RETURNS: list of {graph_path, node_count, evaluator_type} entries — one per graph prim. "
        "CAVEATS: returns Action graphs by default; Push/Lazy graphs may need a separate query. "
        "Empty list does NOT mean OmniGraph is broken — scene may simply have no graphs yet. "
        "Use inspect_graph to drill into a single graph's nodes/connections."
    ),
    "inspect_graph": (
        "WHAT: Inspect an OmniGraph: enumerate its nodes, connections, and current node attribute values via og.Controller. "
        "WHEN: debugging 'why isn't my ROS2 publisher firing', understanding a graph imported with an asset, planning edits before add_node/connect_nodes. "
        "RETURNS: {graph_path, nodes:[{name,type,attrs}], connections:[{src,dst}]}. "
        "CAVEATS: large graphs (>50 nodes) produce big payloads — narrow with node-name filter if possible. "
        "Attribute values are CURRENT values (not connections) — connected ports show their evaluated value. "
        "Use list_graphs to find graphs first; use add_node/delete_node to mutate."
    ),
    "add_node": (
        "WHAT: Add a single node to an existing OmniGraph using og.Controller.edit() with CREATE_NODES. "
        "WHEN: surgically extending an existing graph (e.g., adding ROS2PublishClock), atomic alternative to rebuilding the whole graph with create_omnigraph. "
        "RETURNS: code patch for approval; new node appears under the graph. "
        "CAVEATS: node_type must be a valid registered OmniGraph node ID (e.g., 'omni.graph.action.OnPlaybackTick', 'isaacsim.ros2.bridge.ROS2PublishClock'). "
        "Wrong node_type silently no-ops in some Kit builds — verify with inspect_graph. "
        "Use connect_nodes after to wire it up; use delete_node to remove."
    ),
    "connect_nodes": (
        "WHAT: Wire one node's output port to another node's input port within an OmniGraph via og.Controller.edit() with CONNECT. "
        "WHEN: completing a graph after add_node, fixing missing wires diagnosed by inspect_graph, rerouting evaluation flow. "
        "RETURNS: code patch for approval. "
        "CAVEATS: src/dst paths use 'NodeName.outputs:portName' and 'NodeName.inputs:portName' format — case-sensitive. "
        "Type-mismatch (e.g., float→bundle) raises at evaluation, NOT at connect-time. "
        "Existing connection on dst is replaced silently. "
        "Use inspect_graph to see exact port names."
    ),
    "set_graph_variable": (
        "WHAT: Set a graph-scoped variable on an OmniGraph via og.Controller. Variables persist across node evaluations and can be read by GetVariable nodes. "
        "WHEN: parameterizing graphs (topic names, frame IDs, scaling factors) without rewiring, sharing state between subgraphs, runtime-tunable settings. "
        "RETURNS: code patch for approval. "
        "CAVEATS: variable must already exist on the graph (created via OmniGraph editor or programmatic CreateVariable). "
        "Type must match declared variable type — string into int variable will error. "
        "Use inspect_graph to list current variables and types."
    ),
    "delete_node": (
        "WHAT: Remove a single node from an OmniGraph using og.Controller.edit() with DELETE_NODES. "
        "WHEN: surgically pruning unused nodes, cleaning up after a failed edit, removing nodes brought in by an unwanted asset reference. "
        "RETURNS: code patch for approval. "
        "CAVEATS: deletion DROPS all connections to/from that node — downstream nodes will go silent. "
        "node_name is RELATIVE to the graph (just the node's friendly name, not full path). "
        "No undo — use inspect_graph to confirm before deleting. "
        "To replace a node, delete then add_node + connect_nodes."
    ),
}
