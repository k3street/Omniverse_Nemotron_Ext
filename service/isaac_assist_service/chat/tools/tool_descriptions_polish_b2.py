"""Batch 2 polish — 47 tools.

Rich WHAT/WHEN/RETURNS/CAVEATS descriptions for the second wave of
Isaac Assist tools (Eureka rewards, ZMQ bridge, GR00T N1, cloud deploy,
quick wins, motion planning, Cortex behaviors, robot setup, wheeled
robots, ROS2 deep, finetune flywheel, smart debugging, URDF
post-processor, SDG quality, ROS2 quality, workspace singularity).

Apply the same way as POLISH from tool_descriptions_polish.py:

    from tool_descriptions_polish_b2 import POLISH_B2
    for tool in ISAAC_SIM_TOOLS:
        name = tool["function"]["name"]
        if name in POLISH_B2:
            tool["function"]["description"] = POLISH_B2[name]
"""

POLISH_B2 = {
    # ── Eureka Rewards (PR #7E) ─────────────────────────────────────────────
    "generate_reward": (
        "WHAT: Generate the initial Eureka LLM-prompt for evolving a reward function for a DirectRLEnv task. "
        "WHEN: starting a new RL task from scratch ('make the arm reach target'), bootstrapping reward design "
        "without hand-crafting, kicking off a num_iterations × num_candidates evolution loop. "
        "RETURNS: {prompt, env_source, eureka_config: {num_candidates, num_iterations}}. "
        "CAVEATS: ONLY DirectRLEnv subclasses — ManagerBasedRLEnv is NOT supported (different reward API). "
        "Defaults K=4 candidates × 5 iterations = 20 trial trainings (expensive). "
        "Follow with evaluate_reward per candidate, then iterate_reward to mutate. "
        "Track progress with eureka_status."
    ),
    "evaluate_reward": (
        "WHAT: Run a short training trial of one candidate reward function and report fitness + per-component metrics. "
        "WHEN: scoring a single Eureka candidate, A/B testing two hand-written rewards, sanity-checking a reward "
        "before a long training run. "
        "RETURNS: {fitness: float, components: {name: {mean, converged}}, task_success_rate: float}. "
        "CAVEATS: subprocess training run — num_steps=1000 default is intentionally short (smoke-test, not converged). "
        "env_id must be a registered Gymnasium ID. "
        "Component metrics feed iterate_reward — keep schema stable across iterations."
    ),
    "iterate_reward": (
        "WHAT: Build the next-iteration mutation prompt by combining previous reward code + training metrics + optional human feedback. "
        "WHEN: between Eureka iterations to evolve a reward, after evaluate_reward shows weak components, "
        "incorporating human observations like 'it keeps dropping the handle'. "
        "RETURNS: {prompt} ready to send to the LLM for the next K candidates. "
        "CAVEATS: metrics dict must match the schema from evaluate_reward (fitness + components + task_success_rate). "
        "User feedback is concatenated verbatim — keep concise. "
        "Does NOT call the LLM itself — caller wires prompt → LLM → evaluate_reward."
    ),
    "eureka_status": (
        "WHAT: Inspect the live state of an in-progress Eureka run: current iteration, best fitness, candidates evaluated. "
        "WHEN: monitoring a long-running reward evolution, deciding whether to early-stop, dashboarding progress. "
        "RETURNS: {run_id, iteration, total_iterations, best_fitness, candidates_done, candidates_total, status}. "
        "CAVEATS: run_id is the identifier returned when the loop started; no-op for unknown IDs. "
        "Read-only — does not pause/resume. "
        "Use this between iterate_reward calls to detect convergence."
    ),

    # ── ZMQ Bridge (PR #7F) ────────────────────────────────────────────────
    "configure_zmq_stream": (
        "WHAT: Wire an OmniGraph ZMQ PUB stream from a camera/lidar prim using NVIDIA's C++ OgnIsaacBridgeZMQNode. "
        "WHEN: streaming sensor data to an external trainer/inference node, low-latency telemetry for teleop, "
        "bypassing ROS2 for performance-critical pipelines. "
        "RETURNS: code patch for approval; OmniGraph nodes ticking on playback, publishing on tcp://127.0.0.1:<port>. "
        "CAVEATS: localhost only (127.0.0.1) — no remote publishing. "
        "Port range 1024-65535, default 5555. "
        "JPEG compression default; switch to 'none' for raw frames at higher bandwidth cost. "
        "All I/O via the C++ node — do NOT add Python pyzmq sockets in parallel (race conditions)."
    ),

    # ── GR00T N1 (PR #7G) ──────────────────────────────────────────────────
    "load_groot_policy": (
        "WHAT: Configure NVIDIA GR00T N1 foundation-model policy server for a robot articulation. "
        "WHEN: deploying a vision-language-action policy on a known embodiment (LIBERO_PANDA, OXE_WIDOWX, UNITREE_G1), "
        "before evaluate_groot or finetune_groot. "
        "RETURNS: {download_cmd, launch_config, vram_required_gb} — actual download/launch happens via returned commands. "
        "CAVEATS: requires >=24 GB VRAM (3B model). "
        "embodiment preset determines obs/action mapping — 'custom' needs manual schema. "
        "Default model nvidia/GR00T-N1.6-3B downloads from HuggingFace (~6 GB)."
    ),
    "evaluate_groot": (
        "WHAT: Closed-loop evaluation of a GR00T N1 policy on an IsaacLab task over N episodes. "
        "WHEN: benchmarking zero-shot generalization, validating a fine-tuned checkpoint, comparing models with compare_policies. "
        "RETURNS: {success_rate: float, task_metrics: {...}, num_episodes_done}. "
        "CAVEATS: launches subprocess — long-running for num_episodes=50 default. "
        "Requires policy server already loaded via load_groot_policy. "
        "task must match a registered Isaac-GR00T-* env. "
        "checkpoint param overrides the base model with a fine-tuned weights path."
    ),
    "finetune_groot": (
        "WHAT: Fine-tune GR00T N1 on demonstration data in LeRobot v2 format (LoRA or full). "
        "WHEN: adapting the foundation model to a new task, leveraging collected teleop demos, "
        "domain-specializing the base policy. "
        "RETURNS: code patch for approval; subprocess training writes checkpoints to output_dir. "
        "CAVEATS: LoRA (default) needs ~24 GB VRAM; full fine-tune needs ~80 GB. "
        "demo_data MUST be LeRobot v2 layout (info.json + episodes/) — other formats fail silently. "
        "Default 10 000 steps; output to workspace/groot_checkpoints. "
        "Verify with evaluate_groot after."
    ),
    "compare_policies": (
        "WHAT: Format a side-by-side comparison table of multiple GR00T evaluation results. "
        "WHEN: 'which policy is best for my task', writing a benchmark report, deciding whether fine-tuning helped. "
        "RETURNS: {table_text, ranked_by_success_rate, best_policy}. "
        "CAVEATS: pure presentation — does NOT run any evaluation (caller must run evaluate_groot first per policy). "
        "Each input result needs policy_name + model_id + success_rate at minimum. "
        "training_data_size and observation_type are descriptive strings, not metrics."
    ),

    # ── Cloud Deployment (PR #7H) ──────────────────────────────────────────
    "cloud_launch": (
        "WHAT: Launch a cloud GPU instance via IsaacAutomator on AWS/GCP/Azure for training, SDG, evaluation, or headless sim. "
        "WHEN: scaling RL training beyond local GPU, batch SDG generation, large-scale evaluation sweeps. "
        "RETURNS: {deploy_cmd, estimated_cost_per_hour_usd, prerequisites, job_id_template}. "
        "CAVEATS: ALWAYS requires user approval before execution (cost). "
        "instance_type names differ per provider (g5.2xlarge AWS / g2-standard-8 GCP / NCasT4_v3 Azure). "
        "Default isaac_version 5.1.0. "
        "Pre-check cost with cloud_estimate_cost. "
        "Always pair with cloud_teardown when done — running instances bill continuously."
    ),
    "cloud_status": (
        "WHAT: Poll a running cloud job's status: GPU utilization, ETA, cost-so-far. "
        "WHEN: monitoring a long training run, deciding when results are ready, tracking spend. "
        "RETURNS: {job_id, status, gpu_util_pct, eta_minutes, cost_so_far_usd}. "
        "CAVEATS: requires job_id from cloud_launch. "
        "Cost is best-effort estimate from elapsed-time × instance rate, not the cloud bill. "
        "Use cloud_download_results once job is COMPLETE."
    ),
    "cloud_download_results": (
        "WHAT: Generate scp/rsync commands to pull job artifacts from a cloud instance to a local directory. "
        "WHEN: cloud job COMPLETE state, retrieving checkpoints/logs/SDG outputs before teardown. "
        "RETURNS: code patch for approval; commands run on user shell, not in-Sim. "
        "CAVEATS: job must be in COMPLETE state — running jobs may have partial files. "
        "Default output_dir workspace/cloud_results. "
        "Run BEFORE cloud_teardown — teardown destroys the instance disk."
    ),
    "cloud_teardown": (
        "WHAT: Terminate a cloud instance launched via cloud_launch (stops billing). "
        "WHEN: job complete and results downloaded, abort a runaway/expensive job, end-of-session cleanup. "
        "RETURNS: code patch for approval; command + cost-so-far warning. "
        "CAVEATS: ALWAYS requires user approval. "
        "Destroys instance disk — call cloud_download_results FIRST. "
        "Forgetting to teardown is the #1 source of unexpected cloud bills."
    ),
    "cloud_estimate_cost": (
        "WHAT: Estimate USD cost of running a given cloud GPU instance for N hours from a built-in pricing table. "
        "WHEN: budgeting before cloud_launch, comparing providers/instance types, justifying cost in a PR. "
        "RETURNS: {provider, instance_type, hours, hourly_rate_usd, total_usd}. "
        "CAVEATS: pricing table is static (drift over time), excludes egress/storage/spot discounts. "
        "Use as ballpark, not final-bill prediction. "
        "Real billing comes from the provider console."
    ),

    # ── Quick Wins (PR #8A) ────────────────────────────────────────────────
    "clone_envs": (
        "WHAT: Replicate a source env prim into N parallel envs in a grid layout via isaacsim.core.cloner.GridCloner. "
        "WHEN: vectorized RL training (1024+ envs), domain randomization across many copies, parallel data collection. "
        "RETURNS: code patch for approval; creates /World/envs/env_0..env_{N-1} grid. "
        "CAVEATS: spacing in meters (default 2.5) — too tight causes inter-env overlap. "
        "collision_filter=true (default) prevents envs from interacting; turn off only for multi-agent setups. "
        "GPU physics required for >100 envs in real time."
    ),
    "debug_draw": (
        "WHAT: Draw debug primitives (points / lines / lines_spline) in the viewport via isaacsim.util.debug_draw. "
        "WHEN: visualizing waypoints, drawing planned trajectories, marking target poses, showing raycast results. "
        "RETURNS: code patch for approval; primitives appear immediately in viewport. "
        "CAVEATS: ONLY points / lines / lines_spline — no spheres / arrows / boxes / text "
        "(use add_primitive or text overlays for those). "
        "Color RGBA 0-1, default red. "
        "lifetime=0 is persistent — must clear manually; >0 auto-clears after N seconds."
    ),
    "generate_occupancy_map": (
        "WHAT: Build a 2D top-down occupancy grid of the scene via raycasting in a [min_z,max_z] band. "
        "WHEN: navigation pre-planning, exporting a static map for Nav2, finding free workspace area. "
        "RETURNS: {grid: 2D array, origin: [x,y], resolution_m: float, dimensions_m: [w,h]}. "
        "CAVEATS: resolution default 0.05 m — finer is much slower (10×10 m at 0.01 m = 1M rays). "
        "Only obstacles inside height_range are detected (default [0,2] m). "
        "Static snapshot — does not update as scene changes."
    ),
    "inspect_camera": (
        "WHAT: Read current intrinsics of a UsdGeom.Camera prim: focal length, aperture, clipping, focus distance, projection. "
        "WHEN: confirming camera settings before render, debugging clipping issues, capturing config to reproduce a view. "
        "RETURNS: {focal_length_mm, horizontal_aperture_mm, vertical_aperture_mm, clipping_range, focus_distance, projection}. "
        "CAVEATS: read-only — to modify use configure_camera. "
        "camera_path must be a UsdGeom.Camera (not a parent Xform)."
    ),

    # ── Motion Planning (PR #8B) ───────────────────────────────────────────
    "set_motion_policy": (
        "WHAT: Tweak an RMPflow motion policy: add/remove obstacle, or pad joint limits for safer planning. "
        "WHEN: registering a new collision obstacle for IK/planning, removing a stale one, "
        "shrinking effective joint range to avoid singularities. "
        "RETURNS: code patch for approval. "
        "CAVEATS: only supports cuboid/sphere obstacles (no mesh). "
        "obstacle_dims = [x,y,z] for cuboid or [radius] for sphere. "
        "joint_limit_buffers in radians. "
        "Requires a robot with motion-gen config — generate_robot_description first."
    ),
    "solve_ik": (
        "WHAT: Solve inverse kinematics with the Lula solver — joint positions for a target end-effector pose. "
        "WHEN: 'put the gripper at this XYZ', single-shot pose commands, computing reachability of a target. "
        "RETURNS: code patch for approval; joints set to IK solution if successful. "
        "CAVEATS: requires generate_robot_description for the robot first. "
        "target_position in world meters; target_orientation as [w,x,y,z] quaternion (omit for default). "
        "Only ~9 robots have built-in configs (franka, ur10/5e/3e, cobotta, rs007n, dofbot, kawasaki, flexiv). "
        "For full path planning use motion_generation tools, not single-shot IK."
    ),

    # ── Cortex Behaviors (PR #8C) ──────────────────────────────────────────
    "create_behavior": (
        "WHAT: Generate a Cortex CortexWorld + decider-network script for an autonomous behavior (pick_and_place, follow_target). "
        "WHEN: prototyping autonomous robot logic without writing Cortex from scratch, demoing decision-tree behaviors. "
        "RETURNS: code patch for approval; complete runnable script. "
        "CAVEATS: ONLY pick_and_place and follow_target presets — custom behaviors need hand-written deciders. "
        "target_prim required for both behavior types. "
        "Script REPLACES current scene controller — combine carefully with other controllers. "
        "Visualize with visualize_behavior_tree."
    ),
    "create_gripper": (
        "WHAT: Configure a gripper (parallel_jaw or suction) on a robot with open/closed positions and DOF wiring. "
        "WHEN: setting up a Franka panda hand, adding a suction cup tool, before grasp_object. "
        "RETURNS: code patch for approval; initializes gripper state. "
        "CAVEATS: parallel_jaw REQUIRES gripper_dof_names (e.g. ['panda_finger_joint1','panda_finger_joint2']). "
        "suction does not need DOF names. "
        "Default open_position=0.04, closed=0.0 (Franka). "
        "Requires robot articulation with gripper joints already imported."
    ),
    "grasp_object": (
        "WHAT: Generate full approach → close → lift grasp sequence script for a robot+object pair. "
        "WHEN: pick-and-place demos, loading curated grasps from .isaac_grasp files, "
        "top-down/side approach without manual waypoints. "
        "RETURNS: code patch for approval; scripted grasp sequence. "
        "CAVEATS: grasp_type='from_file' REQUIRES grasp_file path to a .isaac_grasp YAML. "
        "Default approach 0.10 m, lift 0.10 m. "
        "For top_down/side, gripper geometry assumes Franka-style — adjust for other grippers. "
        "Pair with create_gripper. "
        "Define new poses with define_grasp_pose."
    ),
    "define_grasp_pose": (
        "WHAT: Author and save a .isaac_grasp YAML grasp specification for a robot+object pair. "
        "WHEN: building a curated grasp library, capturing demonstrated grasps for replay, "
        "adding custom grasp poses for grasp_object(grasp_type='from_file'). "
        "RETURNS: code patch for approval; writes YAML with transform + approach + gripper params. "
        "CAVEATS: gripper_offset = offset from object centroid in meters; default [0,0,0]. "
        "approach_direction default [0,0,-1] (top-down in +Z up). "
        "Output is a static spec — does not validate reachability (use solve_ik for that)."
    ),
    "visualize_behavior_tree": (
        "WHAT: Print a Cortex decider network as an ASCII hierarchy with node types and connections. "
        "WHEN: debugging why a behavior takes the wrong branch, documenting a behavior's structure, code review. "
        "RETURNS: {tree_text: str} formatted hierarchy. "
        "CAVEATS: text-only — no graphical render. "
        "Requires the network already constructed by create_behavior or hand-written code. "
        "Read-only inspection — does not modify the network."
    ),

    # ── Robot Setup (PR #8D) ───────────────────────────────────────────────
    "tune_gains": (
        "WHAT: Set or auto-tune joint drive Kp/Kd via UsdPhysics.DriveAPI, manually or via GainTuner step-response test. "
        "WHEN: stiffening a sloppy arm, fixing wobble/instability, matching real-robot tracking, post-import drive setup. "
        "RETURNS: code patch for approval; for step_response also returns RMSE per joint. "
        "CAVEATS: 'manual' applies kp/kd directly (fast). "
        "'step_response' runs an automated sinusoidal/step trajectory (sim must play, slow). "
        "Omit joint_name to apply to all joints. "
        "For overall robot defaults use robot_wizard or apply_robot_fix_profile."
    ),

    # ── Wheeled Robots (PR #8E) ────────────────────────────────────────────
    "navigate_to": (
        "WHAT: Drive a wheeled robot to an [x,y] target via direct line-of-sight or A* grid path. "
        "WHEN: 'go to that location' for a mobile base, scripted demo navigation, basic point-to-point movement. "
        "RETURNS: code patch for approval; physics callback drives wheels until goal reached. "
        "CAVEATS: requires robot already configured via create_wheeled_robot. "
        "'direct' has no obstacle avoidance — prefer 'astar' in cluttered scenes. "
        "A* uses a static occupancy grid (run generate_occupancy_map first). "
        "For Nav2 stack use ROS2 bridge instead."
    ),
    "create_conveyor": (
        "WHAT: Convert an existing mesh prim into a conveyor belt via OgnIsaacConveyor OmniGraph node. "
        "WHEN: warehouse/factory sims, sorting demos, moving objects without articulation. "
        "RETURNS: code patch for approval; conveyor belts ticking on play. "
        "CAVEATS: REQUIRES CPU physics — GPU/Fabric physics not supported (tool checks and warns). "
        "speed in m/s, default 0.5. "
        "direction unit vector (default +X). "
        "For multi-segment tracks use create_conveyor_track."
    ),
    "create_conveyor_track": (
        "WHAT: Build a multi-segment conveyor track from a list of [x,y,z] waypoints, each segment oriented to connect them. "
        "WHEN: L-shaped/curved conveyor lines, complex factory layouts, replacing many create_conveyor calls. "
        "RETURNS: code patch for approval; N-1 conveyor segments wired together. "
        "CAVEATS: segments are straight between waypoints — sharp angles cause discontinuities. "
        "Default belt_width 0.5 m, speed 0.5 m/s. "
        "All segments share speed; for variable-speed use multiple create_conveyor calls. "
        "Same CPU physics restriction as create_conveyor."
    ),
    "merge_meshes": (
        "WHAT: Merge multiple mesh prims into a single mesh via MeshMerger utility. "
        "WHEN: reducing draw calls for static geometry, combining conveyor segments, "
        "consolidating imported sub-meshes for performance. "
        "RETURNS: code patch for approval; new mesh created at output_path, sources untouched (unless deleted manually). "
        "CAVEATS: result is one mesh — loses per-source materials/transforms. "
        "Don't merge meshes that need independent physics or animation. "
        "For collision meshes use optimize_collision after merge."
    ),

    # ── ROS2 Deep (PR #8F) ─────────────────────────────────────────────────
    "show_tf_tree": (
        "WHAT: Print the ROS2 TF transform tree from a root frame; auto-creates ROS2PublishTransformTree node if missing. "
        "WHEN: debugging missing/disconnected frames, validating sensor mounting transforms, RViz pre-flight. "
        "RETURNS: {tree_text} ASCII hierarchy from root_frame. "
        "CAVEATS: requires rosbridge active. "
        "Default root_frame='world'. "
        "Frames published by Isaac Sim only — external publishers (Nav2, MoveIt) appear if connected to same domain. "
        "For deeper analysis use ros2_get_node_details on the TF publisher."
    ),
    "configure_ros2_bridge": (
        "WHAT: Set up an OmniGraph ROS2 bridge for multiple sensors (camera/lidar/imu/clock/joint_state) in one call. "
        "WHEN: bringing up a new robot's full sensor suite, batch-wiring publishers, fresh ROS2 integration. "
        "RETURNS: code patch for approval; ROS2Context + per-sensor publisher nodes. "
        "CAVEATS: handles isaacsim.ros2.* vs omni.isaac.ros2_bridge namespace differences automatically. "
        "Default ros2_domain_id=0 — must match all consumers. "
        "For QoS issues use fix_ros2_qos. "
        "For time sync use configure_ros2_time. "
        "Diagnose end-to-end with diagnose_ros2."
    ),

    # ── Finetune Flywheel (PR #9) ──────────────────────────────────────────
    "record_feedback": (
        "WHAT: Record approve / reject / correct feedback on a previous chat turn for fine-tuning quality filtering. "
        "WHEN: thumbs-up/down on assistant response, logging an edit before approval, capturing rejection reason. "
        "RETURNS: {recorded: bool, turn_id, session_id}. "
        "CAVEATS: turn_id is integer turn-index within the session. "
        "edited=true means user accepted-with-modifications (still high quality). "
        "correction text is preserved verbatim — feeds future training, keep it specific. "
        "View aggregates with finetune_stats; export filtered set with export_finetune_data."
    ),
    "export_finetune_data": (
        "WHAT: Export recorded chat turns to OpenAI / Anthropic / Ollama (Unsloth-ShareGPT) / Alpaca JSONL fine-tuning format. "
        "WHEN: building a SFT dataset, preparing data for a LoRA training run, sharing high-quality examples. "
        "RETURNS: {output_path, num_records, format}. "
        "CAVEATS: default min_quality='approved_successful' (strictest) — switch to 'approved' or 'all' for more volume. "
        "ALWAYS run redact_finetune_data on output before sharing externally (scrubs API keys/paths/PII). "
        "output_path optional — auto-generated under workspace/finetune/."
    ),
    "finetune_stats": (
        "WHAT: Aggregate counters over recorded fine-tuning data: total turns, approval rate, tool distribution, error rate, date range. "
        "WHEN: dashboarding feedback collection, deciding if there's enough data to train, finding which tools dominate. "
        "RETURNS: {total_turns, approved, rejected, edited, approval_rate, tool_distribution: {...}, error_rate, date_range, "
        "rejection_correction_pairs}. "
        "CAVEATS: read-only counters. "
        "For per-turn detail iterate the underlying database directly. "
        "rejection_correction_pairs are especially valuable training data (negative + positive)."
    ),
    "redact_finetune_data": (
        "WHAT: Run regex-based redaction over a fine-tuning JSONL: strips API keys (sk-*, AIza*, ghp_*, Bearer *), file paths, PII. "
        "WHEN: BEFORE sharing/uploading any export_finetune_data output, before pushing dataset to HuggingFace, sanitizing legacy data. "
        "RETURNS: code patch for approval; new <input>_redacted.jsonl (or output_path if given). "
        "CAVEATS: regex-based — may miss provider-specific or novel key formats. "
        "Spot-check output before publishing. "
        "Idempotent — safe to re-run."
    ),

    # ── Smart Debugging (Phase 2 Addendum) ─────────────────────────────────
    "diagnose_physics_error": (
        "WHAT: Pattern-match a PhysX/physics error string against the Top 20 known errors with prim-aware fix instructions. "
        "WHEN: pasted PhysX error from console, 'why is my sim broken' triage, after get_console_errors / get_physics_errors. "
        "RETURNS: {category, affected_prim, fix_instructions, severity, deduplicated_count}. "
        "CAVEATS: pattern-based — unknown error strings return generic guidance. "
        "Repeated identical errors are deduped. "
        "For scene-wide pre-flight use check_physics_health instead. "
        "For raw error stream use get_console_errors / get_physics_errors."
    ),
    "trace_config": (
        "WHAT: Trace an IsaacLab @configclass parameter through all source files showing each definition / override site. "
        "WHEN: 'where does sim.dt actually come from', debugging unexpected param values, "
        "understanding inheritance in nested configs. "
        "RETURNS: {param_name, final_value, sources: [{file, line, value}]}. "
        "CAVEATS: only follows @configclass inheritance — runtime mutations (cfg.sim.dt = 0.001 in __init__) not always caught. "
        "Dotted name format: 'sim.dt', 'robot.actuators.stiffness'. "
        "env_source_path must point to the leaf env file."
    ),
    "check_physics_health": (
        "WHAT: Generate code for a comprehensive scene physics audit: missing CollisionAPI, bad inertia, extreme mass ratios, "
        "infinite joint limits, missing PhysicsScene, metersPerUnit mismatch. "
        "WHEN: pre-flight before sim_control(play), post-import sanity check, troubleshooting unstable sim. "
        "RETURNS: code patch for approval; on execute returns {issues: [...], warnings: [...], passed: bool}. "
        "CAVEATS: scene-wide by default; pass articulation_path to scope to one robot. "
        "For URDF-import-specific issues use verify_import. "
        "For single error string interpretation use diagnose_physics_error. "
        "For shallow per-prim check use check_collisions."
    ),

    # ── URDF Post-Processor (Phase 3 Addendum) ─────────────────────────────
    "verify_import": (
        "WHAT: Audit a URDF-imported articulation for common post-import defects with severity-tagged issue list and fix commands. "
        "WHEN: immediately after import_robot or robot_wizard on a custom URDF, debugging 'robot won't move' or 'falls through floor'. "
        "RETURNS: JSON {issues: [{prim_path, severity, issue_type, fix_command}], passed: bool}. "
        "CAVEATS: checks: missing CollisionAPI on links, zero-mass links, infinite joint limits, missing ArticulationRootAPI, "
        "metersPerUnit drift, extreme inertia ratios. "
        "For known robots use apply_robot_fix_profile (specialized fixes). "
        "For scene-wide health use check_physics_health."
    ),

    # ── SDG Quality (Phase 7B Addendum) ────────────────────────────────────
    "validate_annotations": (
        "WHAT: Validate SDG annotation samples for bbox-out-of-bounds, duplicate instance IDs, zero-area boxes, missing classes. "
        "WHEN: post-SDG-run quality check, before training on synthetic data, debugging weird detection metrics. "
        "RETURNS: {samples_checked, issues: [{sample_id, type, detail}], pass_rate}. "
        "CAVEATS: samples a subset (default 10) — for full audit raise num_samples (slow). "
        "Catches annotation issues, not visual quality (lighting/composition) — use diagnose_domain_gap for that. "
        "For DR parameter problems use analyze_randomization."
    ),
    "analyze_randomization": (
        "WHAT: Per-parameter statistics (min/max/mean/std) over an SDG run's domain randomization samples; flags collapsed ranges. "
        "WHEN: debugging 'all my synthetic data looks the same', validating DR config, finding near-constant params. "
        "RETURNS: {per_param: {min, max, mean, std, flagged_reason}, recommendations}. "
        "CAVEATS: default 50 samples — raise for finer statistics. "
        "Flags 'collapsed' if max-min ≈ 0 or std ≈ 0 (likely misconfig). "
        "Does NOT generate new DR configs — caller adjusts based on recommendations. "
        "For sim-vs-real gap use diagnose_domain_gap."
    ),
    "diagnose_domain_gap": (
        "WHAT: Compare synthetic vs real image dataset distributions; returns FID-like score + per-class deltas + DR adjustment hints. "
        "WHEN: trained model works in sim but fails on real images, calibrating DR to close the gap, sim2real validation. "
        "RETURNS: {fid_score, per_class_deltas: {...}, suggestions: [...]}. "
        "CAVEATS: lower FID = closer match (~0 identical, >100 large gap). "
        "Optional model_checkpoint for feature extraction (default uses generic features). "
        "Compares overall distributions — does not pinpoint individual outlier images. "
        "For per-sample annotation issues use validate_annotations."
    ),

    # ── ROS2 Quality (Phase 8F Addendum) ───────────────────────────────────
    "diagnose_ros2": (
        "WHAT: Comprehensive ROS2 integration health check: ROS2Context presence, distro, QoS mismatches, use_sim_time, "
        "clock publishing, domain ID consistency, dangling OmniGraph connections. "
        "WHEN: 'ROS2 isn't working' triage, after configure_ros2_bridge, before bringing up Nav2/MoveIt. "
        "RETURNS: {issues: [{type, severity, fix_suggestion}], passed: bool}. "
        "CAVEATS: requires rosbridge running. "
        "For specific QoS fix use fix_ros2_qos. "
        "For time-sync use configure_ros2_time. "
        "Per-node detail via ros2_get_node_details."
    ),
    "fix_ros2_qos": (
        "WHAT: Apply a preset-driven QoS profile fix to a publisher for a specific topic (matches typical subscriber expectations). "
        "WHEN: 'subscriber not receiving' after diagnose_ros2 reports QoS mismatch, RViz/Nav2/MoveIt connection issues. "
        "RETURNS: code patch for approval. "
        "CAVEATS: preset map: /scan→BEST_EFFORT, /robot_description→TRANSIENT_LOCAL, /tf→RELIABLE, /cmd_vel→RELIABLE, /camera/*→BEST_EFFORT. "
        "Custom topics not in the map fall back to RELIABLE. "
        "For multi-sensor setup use configure_ros2_bridge."
    ),
    "configure_ros2_time": (
        "WHAT: Wire ROS2PublishClock OmniGraph node and set use_sim_time mode (sim_time / real_time / scaled). "
        "WHEN: synchronizing Nav2/MoveIt with Isaac Sim's clock, replaying logs at altered speed, "
        "fixing 'TF transform too old' errors. "
        "RETURNS: code patch for approval. "
        "CAVEATS: 'sim_time' (publishes to /clock, use_sim_time=true) most common. "
        "'real_time' (use_sim_time=false) — Isaac runs decoupled from external nodes. "
        "'scaled' uses time_scale (default 1.0; <1 slow-mo, >1 fast-forward). "
        "All ROS2 consumers must agree on mode."
    ),

    # ── Workspace Singularity (Phase 8B Addendum) ──────────────────────────
    "show_workspace": (
        "WHAT: Render the robot's reachable workspace as a color-coded point cloud overlay in the viewport. "
        "WHEN: 'where can my arm reach', planning task placement, identifying singularity-prone regions before motion planning. "
        "RETURNS: code patch for approval; point cloud overlay with chosen color_mode. "
        "CAVEATS: default 500 000 joint samples — large scenes may take seconds-to-minutes. "
        "color_mode: 'reachability' (binary green/no-point), 'manipulability' (gradient), 'singularity_distance' "
        "(red near singularities). "
        "For point-target singularity check use check_singularity (cheaper)."
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
        if name in POLISH_B2:
            tool["function"]["description"] = POLISH_B2[name]
            count += 1
    return count
